"""Ablaciones de condicionamiento, seguimiento y entrada con localización fija.

Lee una pasada de borde verificada, reproduce exactamente su salida histórica
y cambia una política declarada. No recalcula registro, no corrige
cortes seleccionados a mano y no escribe en las candidatas de la aplicación.
"""
import argparse
from contextlib import nullcontext
from datetime import datetime, timezone
import importlib.metadata
from pathlib import Path
import platform
import sys
import time

import numpy as np

from hcclab.candidates import file_sha256, stack_identity
from hcclab.catalog import Catalog
from hcclab.dicom import find_seg, segment_options, stacks, verify_pre_sources
from hcclab.model_assets import model_path, verify_model
from hcclab.post_model import _get_predictor, segment_post_model
from .plot_boundary import read_inputs, verify_inputs
from .post_boundary import captured_field, descriptors
from .post_tracking import safe_output_directory, write_json
from .prompt_probe import capture_prompt_alternatives


SCOPE = ('Fixed-localization development comparison without independent POST reference; '
         'descriptive changes only, no anatomical accuracy or treatment response.')


def verify_frozen_pre(data, manifest):
    """Vuelve a verificar la TC y el SEG que originaron la localización fija."""
    selection, source = manifest['selection'], manifest['sources']
    catalog = Catalog(data)
    uid = selection['pre_series_uid']
    allowed = {s['uid'] for study in catalog.case(manifest['case_id'])['studies']
               if study['stage'] == 'PRE' for s in study['series']}
    row = catalog.by_uid.get(uid)
    if uid not in allowed or row is None or not catalog.local(row):
        raise ValueError('Frozen baseline localization does not belong to this local PRE study.')
    options = [s for s in stacks(catalog.folder(row), uid)
               if s.slices[0].acquisition == selection['pre_acquisition']]
    if len(options) != 1 or options[0].issue:
        raise ValueError('Frozen PRE acquisition is ambiguous or incompatible.')
    stack = options[0]
    if (stack_identity(stack) != source['pre']['stack_identity']
            or [s.sop for s in stack.slices] != source['pre']['ordered_sop_uids']
            or [file_sha256(s.path) for s in stack.slices] != source['pre']['ordered_ct_sha256']):
        raise ValueError('PRE source geometry or bytes differ from the frozen localization.')
    seg = find_seg(catalog, row)
    if seg is None or file_sha256(seg[0]) != source['seg_sha256']:
        raise ValueError('Published PRE segmentation differs from the frozen localization.')
    seg = verify_pre_sources(stack, seg, expected_series_uid=uid, expected_study_uid=row['StudyInstanceUID'])
    labels = {s['number']: s['label'] for s in segment_options(seg[1])}
    if labels.get(selection['mass_segment']) != 'Mass' or labels.get(selection['liver_segment']) != 'Liver':
        raise ValueError('Frozen PRE Mass/Liver labels do not match the published SEG.')
    if file_sha256(seg[0]) != source['seg_sha256']:
        raise ValueError('Published SEG changed during verification.')


def verify_run_inputs(inputs, data):
    verify_inputs(inputs['ct_paths'], inputs['ct_hashes'], inputs['manifest_path'],
                  inputs['manifest_sha256'], inputs['artifacts'], inputs['manifest'])
    verify_frozen_pre(data, inputs['manifest'])


def require_same_setup(baseline, current, *, upstream_input=False):
    """Solo el brazo de entrada puede cambiar la preparación de sus píxeles."""
    for key in ('prompt_slice_index', 'prompt_box_xyxy', 'model_prompt_box_xyxy',
                'inference_field_yx', 'inference_field_shape_yx', 'model'):
        if baseline[key] != current[key]:
            raise ValueError(f'Inference setup changed across comparison: {key}.')
    for key in ('configuration', 'configuration_sha256', 'window_level_hu', 'window_width_hu',
                'model_input_shape_yx', 'normalization_mean', 'normalization_std', 'input_resize',
                'context_minimum_mm', 'context_rule', 'cuda_hole_filling'):
        if key == 'input_resize' and upstream_input:
            continue
        if baseline['settings'][key] != current['settings'][key]:
            raise ValueError(f'Inference setting changed across comparison: {key}.')
    expected = 'upstream_uint8' if upstream_input else 'float_bilinear'
    if current['settings'].get('input_mode', 'float_bilinear') != expected:
        raise ValueError('Inference setting changed across comparison: input_mode.')
    if upstream_input:
        for key, expected in {
            'input_resize': 'pil_rgb_bicubic_if_needed',
            'input_quantization': 'clip_window_times_255_truncate_uint8',
            'input_normalization_precision': 'float64_then_float32',
        }.items():
            if current['settings'].get(key) != expected:
                raise ValueError(f'Upstream input contract does not match: {key}.')


def execute(args):
    if args.output.resolve().is_relative_to(args.baseline.resolve()):
        raise ValueError('Outputs cannot be written inside the frozen baseline run.')
    inputs = read_inputs(args.data, args.baseline)
    old = inputs['manifest']
    verify_run_inputs(inputs, args.data)
    folder = safe_output_directory(args.output, args.data)
    started = time.perf_counter()
    after_variant = getattr(args, 'after', 'box')
    changes = {'box': 'binary_reinjection_to_box',
               'single_tracking': 'tracking_multimask_to_single',
               'upstream_input': 'float_bilinear_to_upstream_uint8'}
    if after_variant not in changes:
        raise ValueError('After variant must be box, single_tracking or upstream_input.')
    manifest = {
        'schema_version': 1, 'kind': 'post_prompt_conditioning', 'status': 'running',
        'created_utc': datetime.now(timezone.utc).isoformat(), 'scope': SCOPE,
        'case_id': old['case_id'], 'selection': old['selection'], 'sources': old['sources'],
        'baseline_manifest_sha256': inputs['manifest_sha256'],
        'baseline_artifacts_sha256': old['artifacts_sha256'],
        'localization': old['localization'], 'registrations': old['registrations'],
        'registration_recomputed': False,
        'primary_change': changes[after_variant],
        'boundary_band_mm': 2, 'automatic_parameter_selection': False,
        'source_integrity_verified': False,
        'decoder_probe': bool(getattr(args, 'probe', False)),
        'diagnostic_array_indices': [53, 54, 55, 56] if getattr(args, 'probe', False) else [],
        'implementation_sha256': {
            str(p.relative_to(Path(__file__).resolve().parents[1])): file_sha256(p)
            for parent in (Path(__file__).parent, Path(__file__).resolve().parents[1]/'hcclab')
            for p in parent.glob('*.py')},
        'software': {name: importlib.metadata.version(name) for name in
                     ('numpy', 'scipy', 'pydicom', 'torch', 'torchvision', 'MedSAM2', 'hydra-core', 'Pillow')},
        'platform': {'python': platform.python_version(), 'system': platform.system(), 'machine': platform.machine()},
    }
    write_json(folder/'manifest.json', manifest)
    try:
        checkpoint = model_path(args.data)
        artifacts, reports = {}, {}
        after_mode = 'box' if after_variant == 'box' else 'binary_reinjection'
        for mode, name in [('binary_reinjection', 'before'), (after_mode, 'after')]:
            upstream_input = name == 'after' and after_variant == 'upstream_input'
            input_mode = 'upstream_uint8' if upstream_input else 'float_bilinear'
            print(f'{old["case_id"]}: {name}, {mode}, {input_mode}, frozen prompt', file=sys.stderr, flush=True)
            intermediates = {}
            predictor, _, _ = _get_predictor(checkpoint)
            previous_tracking = predictor.multimask_output_for_tracking
            # This CLI owns a separate, sequential predictor process. Restore
            # the sole override even on failure; never mutate the live server.
            tracking_multimask = not (name == 'after' and after_variant == 'single_tracking')
            low, high = np.asarray(old['inference']['inference_field_yx'])
            valid_field = inputs['valid'][:, low[0]:high[0], low[1]:high[1]]
            probe = None
            try:
                predictor.multimask_output_for_tracking = tracking_multimask
                effective = {key: getattr(predictor, key) for key in (
                    'multimask_output_for_tracking', 'binarize_mask_from_pts_for_mem_enc', 'num_maskmem')}
                context = (capture_prompt_alternatives(predictor, valid_field=valid_field,
                               keep_frames={53, 54, 55, 56}) if getattr(args, 'probe', False)
                           else nullcontext(None))
                with context as probe:
                    mask, info = segment_post_model(inputs['hu'], inputs['spacing'], old['localization'],
                        checkpoint_path=checkpoint, valid_mask=inputs['valid'], refine_boundary=True,
                        intermediates=intermediates, prompt_mode=mode, input_mode=input_mode)
            finally:
                predictor.multimask_output_for_tracking = previous_tracking
            require_same_setup(old['inference'], info, upstream_input=upstream_input)
            low, high = np.asarray(info['inference_field_yx'])
            field = (slice(None), slice(low[0], high[0]), slice(low[1], high[1]))
            raw = np.zeros(mask.shape, bool)
            raw[field] = intermediates['raw_mask']
            captured_field(raw, inputs['valid'], info, intermediates)
            if name == 'before' and (not np.array_equal(raw, inputs['raw'])
                                    or not np.array_equal(mask, inputs['refined'])):
                raise ValueError('Binary replay differs from the frozen baseline; causal comparison stopped.')
            verify_run_inputs(inputs, args.data)
            verify_model(checkpoint)
            for suffix, array in [('', mask), ('-raw', raw)]:
                path = folder/(name+suffix+'.npz')
                np.savez_compressed(path, mask=array)
                artifacts[path.name] = file_sha256(path)
            path = folder/(name+'-logits.npz')
            np.savez_compressed(path, logits=intermediates['logits'],
                                inference_field_yx=np.asarray(info['inference_field_yx'], dtype=np.int64))
            artifacts[path.name] = file_sha256(path)
            if probe is not None:
                path = folder/(name+'-decoder.json')
                write_json(path, {'settings': probe.settings, 'records': probe.records,
                                  'scope': 'Observed model alternatives and scores; no anatomical reference.'})
                artifacts[path.name] = file_sha256(path)
                path = folder/(name+'-alternatives.npz')
                np.savez_compressed(path, **probe.arrays)
                artifacts[path.name] = file_sha256(path)
            reports[name] = {'prompt_mode': mode, 'input_mode': input_mode,
                             'tracking_multimask': tracking_multimask,
                             'effective_model_settings': effective, 'status': 'experimental_unreviewed',
                             'inference': info,
                             'metrics': descriptors(mask, inputs['refined'], inputs['valid'],
                                                    inputs['spacing'], old['localization'], field),
                             'raw_metrics': descriptors(raw, inputs['raw'], inputs['valid'],
                                                        inputs['spacing'], old['localization'], field)}
            print(f'{name}: {int(mask.sum())} voxels', file=sys.stderr, flush=True)
        comparison = {'scope': SCOPE, 'replay_exact': True, 'before': reports['before'], 'after': reports['after']}
        write_json(folder/'comparison.json', comparison)
        artifacts['comparison.json'] = file_sha256(folder/'comparison.json')
        verify_run_inputs(inputs, args.data)
        manifest.update(status='completed', source_integrity_verified=True, replay_exact=True,
                        model=reports['after']['inference']['model'],
                        inference_field_yx=reports['after']['inference']['inference_field_yx'],
                        artifacts_sha256=artifacts, elapsed_seconds=time.perf_counter()-started)
        write_json(folder/'manifest.json', manifest)
        print(folder, flush=True)
        return 0
    except Exception as error:
        manifest.update(status='failed', error_type=type(error).__name__, error=str(error),
                        elapsed_seconds=time.perf_counter()-started)
        if getattr(error, 'diagnostics', None) is not None:
            manifest['failure_diagnostics'] = error.diagnostics
        write_json(folder/'manifest.json', manifest)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--after', choices=('box', 'single_tracking', 'upstream_input'), default='box')
    parser.add_argument('--probe', action='store_true', help='Record decoder alternatives without changing inference.')
    args = parser.parse_args()
    try:
        return execute(args)
    except (ValueError, KeyError, OSError) as error:
        parser.exit(2, f'Prompt conditioning experiment failed: {error}\n')


if __name__ == '__main__':
    raise SystemExit(main())
