"""CLI reproducible para ablaciones POST; no lee ni escribe candidatas de la app.

Ejecutar como ``python -m experiments.post_tracking --help``. Las identidades
de entrada se especifican mediante CLI o manifiesto; PRE solo proporciona
localización. Las salidas experimentales se escriben fuera del árbol de código.
"""
import argparse
from datetime import datetime, timezone
import importlib.metadata
from itertools import combinations
import json
from pathlib import Path
import platform
import sys
import uuid

import numpy as np

from hcclab.candidates import file_sha256, stack_identity
from hcclab.dicom import find_seg, referenced_mask, segment_options, verify_pre_sources
from hcclab.geometry import GeometryError
from hcclab.lab import Lab
from hcclab.model_assets import model_path, verify_model
from hcclab.registration import localize_pre_region, refine_pre_to_post, register_pre_to_post
from hcclab.volume import load_hu_volume

from .tracking_core import VARIANTS, compare_predictions, infer_raw, load_predictor, prediction_metrics


def selected_stack(lab, case, stage, series_uid, acquisition):
    """Resuelve adquisición por su identificador DICOM, sin elegir índice a ciegas."""
    studies = lab.catalog.case(case)['studies']
    allowed = {series['uid'] for study in studies if study['stage'] == stage for series in study['series']}
    if series_uid not in allowed:
        raise GeometryError(f'The specified {stage} series does not belong to this chronological case pair.')
    options = lab.series(series_uid)['stacks']
    matches = [option['index'] for option in options if option['acquisition'] == acquisition]
    if len(matches) != 1:
        raise GeometryError(f'{stage} acquisition must identify exactly one available stack.')
    return lab.stack(series_uid, matches[0])


def required_segment(seg, number, label):
    if type(number) is not int or number <= 0:
        raise ValueError('Segment numbers must be positive integers.')
    actual = next((s['label'] for s in segment_options(seg[1]) if s['number'] == number), None)
    if actual is None or actual.casefold() != label.casefold():
        raise GeometryError(f'Segment {number} must have the published label {label}.')


def read_sources(data, case, selection, needs_liver):
    lab = Lab(data)  # Catalog/CT operations only; CandidateStore is never consulted.
    pre_row, pre = selected_stack(lab, case, 'PRE', selection['pre_series_uid'], selection['pre_acquisition'])
    post_row, post = selected_stack(lab, case, 'POST', selection['post_series_uid'], selection['post_acquisition'])
    if len(post.slices) > 256:
        raise GeometryError('Experimental POST tracking is limited to 256 native slices.')
    seg = find_seg(lab.catalog, pre_row)
    if seg is None:
        raise GeometryError('The selected PRE acquisition has no published SEG.')
    paths = [s.path for stack in (pre, post) for s in stack.slices] + [seg[0]]
    hashes = [file_sha256(path) for path in paths]
    # Hash before reopening the SEG header; otherwise a replacement between
    # header validation and hashing could pair stale metadata with new pixels.
    seg = verify_pre_sources(pre, seg, expected_series_uid=selection['pre_series_uid'],
                             expected_study_uid=pre_row['StudyInstanceUID'])
    required_segment(seg, selection['mass_segment'], 'Mass')
    if needs_liver:
        required_segment(seg, selection['liver_segment'], 'Liver')
    mass = referenced_mask(*seg, selection['mass_segment'], pre)
    liver = referenced_mask(*seg, selection['liver_segment'], pre) if needs_liver else None
    pre_hu, pre_valid = load_hu_volume(pre, return_validity=True,
                                     expected_series_uid=selection['pre_series_uid'], expected_study_uid=pre_row['StudyInstanceUID'])
    post_hu, post_valid = load_hu_volume(post, return_validity=True,
                                       expected_series_uid=selection['post_series_uid'], expected_study_uid=post_row['StudyInstanceUID'])
    provenance = {'case_id': case, 'selection': selection, 'seg_sha256': hashes[-1],
                  'target_label': 'Mass', 'mask_usage': 'PRE localization only; never a POST result'}
    offset = 0
    for name, stack in [('pre', pre), ('post', post)]:
        first = stack.slices[0]
        provenance[name] = {'stack_identity': stack_identity(stack),
                            'shape_zyx': [len(stack.slices), *first.shape],
                            'spacing_zyx_mm': [stack.interval_mm, *first.spacing],
                            'origin_lps_mm': list(first.position), 'orientation_patient': list(first.orientation),
                            'ordered_ct_sha256': hashes[offset:offset + len(stack.slices)]}
        offset += len(stack.slices)
    sources = {'pre_stack': pre, 'post_stack': post, 'pre_hu': pre_hu, 'post_hu': post_hu,
               'pre_valid': pre_valid, 'post_valid': post_valid, 'mass': mass, 'liver': liver,
               'paths': paths, 'hashes': hashes, 'provenance': provenance}
    verify_unchanged(sources)
    return sources


def verify_unchanged(sources):
    if sources['hashes'] != [file_sha256(path) for path in sources['paths']]:
        raise GeometryError('Source bytes changed during the experiment; no new mask may be published.')


def refine_with_liver(sources, global_transform):
    """Hipótesis experimental: refinar MI en Liver∪Mass PRE, sin propagar SEG.

    Parte del registro global; usa una retícula auxiliar de 3 mm. La unión
    incluye Mass porque puede no estar incluida en la etiqueta Liver publicada.
    Un score MI favorable no comprueba la identidad de la lesión POST.
    """
    context = sources['liver'] | sources['mass']
    transform, diagnostics = refine_pre_to_post(
        sources['pre_hu'], sources['post_hu'], context,
        sources['pre_stack'], sources['post_stack'], global_transform,
        pre_valid=sources['pre_valid'], post_valid=sources['post_valid'])
    diagnostics['method'] = 'global_then_PRE_liver_union_mass_MI'
    diagnostics['context']['source_labels'] = ['Liver', 'Mass']
    diagnostics['seconds'] = diagnostics['elapsed_seconds']
    # Conserva las claves históricas de los informes experimentales.
    diagnostics['settings']['seed'] = diagnostics['settings']['sampling_seed']
    diagnostics['settings']['max_iterations'] = diagnostics['settings']['iterations_per_level']
    return transform, diagnostics


def high_hu_descriptors(hu, valid, mask, common_location):
    """Descriptor de intensidades en una caja común, sin identificar tejido."""
    low, high = common_location['localized_box_zyx']
    crop = tuple(slice(a, b) for a, b in zip(low, high))
    result = {}
    for threshold in (240, 600):
        high_hu = (hu[crop] > threshold) & valid[crop]
        result[str(threshold)] = {'total': int(high_hu.sum()),
                                 'inside_prediction': int(np.count_nonzero(high_hu & mask[crop]))}
    return {'thresholds_hu': result, 'scope': 'Intensity only; not Lipiodol, viability or anatomical truth.'}


def safe_output_directory(output, data):
    output, data = output.resolve(), data.resolve()
    code = Path(__file__).resolve().parents[1]
    annotations = Path('/annotations').resolve()
    for protected in (data, code, annotations):
        if output == protected or protected in output.parents:
            raise ValueError('Experiment outputs must be outside source data, app annotations and the code tree.')
    output.mkdir(parents=True, exist_ok=True)
    folder = output / ('run-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:8])
    folder.mkdir()
    return folder


def write_json(path, document):
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def execute(args, selection):
    folder = safe_output_directory(args.output, args.data)
    sources = read_sources(args.data, args.case, selection, 'local160liver' in args.variants)
    verify_unchanged(sources)
    global_transform, global_info = register_pre_to_post(
        sources['pre_hu'], sources['post_hu'], sources['pre_stack'], sources['post_stack'],
        pre_valid=sources['pre_valid'], post_valid=sources['post_valid'])
    global_location = localize_pre_region(sources['mass'], sources['pre_stack'], sources['post_stack'], global_transform)
    registrations = {'global': {'diagnostics': global_info, 'location': global_location}}
    if 'local160liver' in args.variants:
        transform, info = refine_with_liver(sources, global_transform)
        registrations['liver'] = {'diagnostics': info, 'location': localize_pre_region(
            sources['mass'], sources['pre_stack'], sources['post_stack'], transform)}
    # One process, one CPU predictor, sequential variants. No concurrent UI worker.
    predictor, torch, model = load_predictor(model_path(args.data))
    software = {name: importlib.metadata.version(name) for name in
                ('numpy', 'scipy', 'pydicom', 'SimpleITK', 'torch', 'torchvision', 'MedSAM2', 'hydra-core')}
    implementation = {'experiments/' + p.name: file_sha256(p) for p in Path(__file__).parent.glob('*.py')}
    module_root = Path(sys.modules['hcclab.lab'].__file__).parent
    implementation.update({'hcclab/' + p.name: file_sha256(p) for p in module_root.glob('*.py')})
    manifest = {'schema_version': 1, 'kind': 'post_tracking_ablation', 'status': 'running',
                'sources': sources['provenance'], 'model': model, 'software': software,
                'platform': {'system': platform.system(), 'machine': platform.machine(), 'python': platform.python_version()},
                'implementation_sha256': implementation, 'registrations': registrations,
                'variants': {name: VARIANTS[name] for name in args.variants},
                'scope': 'Development counterfactuals; no independent POST reference and no clinical comparison.'}
    write_json(folder / 'manifest.json', manifest)
    spacing = np.asarray(sources['provenance']['post']['spacing_zyx_mm'])
    completed, failures = [], []
    for name in args.variants:
        settings = VARIANTS[name]
        location = registrations[settings['registration']]['location']
        print(f'Running {args.case}: {name}', file=sys.stderr, flush=True)
        try:
            mask, prompt, info = infer_raw(sources['post_hu'], sources['post_valid'], spacing, location, settings, predictor, torch)
            verify_unchanged(sources)
            verify_model(model_path(args.data))
            metrics = prediction_metrics(mask, location)
            report = {'kind': 'counterfactual_prediction', 'status': 'experimental_unreviewed', 'variant': name,
                      'settings': settings, 'registration_variant': settings['registration'],
                      'metrics': metrics, 'inference': info,
                      'high_hu_common_box': high_hu_descriptors(sources['post_hu'], sources['post_valid'], mask, global_location),
                      'production_guard_bypassed_for_ablation': True,
                      'not_a_saved_app_candidate': True, 'scope': manifest['scope']}
            path = folder / (name + '.npz')
            np.savez_compressed(path, mask=mask, prompt=prompt)
            report['artifact_sha256'] = file_sha256(path)
            write_json(folder / (name + '.json'), report)
            completed.append(name)
            print(json.dumps({'variant': name, 'voxels': metrics['voxels'], 'outside_fraction': metrics['outside_fraction']}), flush=True)
        except Exception as error:
            verify_unchanged(sources)  # Changed source bytes abort the entire run.
            failures.append(name)
            write_json(folder / (name + '.json'), {'variant': name, 'status': 'failed',
                                                  'error_type': type(error).__name__, 'error': str(error)})
    comparisons = []
    for first, second in combinations(completed, 2):
        with np.load(folder / (first + '.npz'), allow_pickle=False) as a, np.load(folder / (second + '.npz'), allow_pickle=False) as b:
            comparisons.append({'first': first, 'second': second, **compare_predictions(a['mask'], b['mask'])})
    write_json(folder / 'prediction_consistency.json', comparisons)
    manifest.update(status='completed_with_failures' if failures else 'completed', completed=completed, failed=failures)
    write_json(folder / 'manifest.json', manifest)
    print(json.dumps({'output': str(folder), 'completed': completed, 'failed': failures}))
    return 1 if failures else 0


def selection_from_args(args):
    fields = ('pre_series_uid', 'post_series_uid', 'pre_acquisition', 'post_acquisition', 'mass_segment', 'liver_segment')
    if args.manifest:
        if any(getattr(args, field) is not None for field in fields):
            raise ValueError('Use either a manifest or explicit source arguments, not both.')
        if args.manifest.stat().st_size > 65536:
            raise ValueError('Source manifest exceeds 64 KiB.')
        document = json.loads(args.manifest.read_text())
        if not isinstance(document, dict) or not isinstance(document.get('cases'), dict):
            raise ValueError('Source manifest must contain a cases mapping.')
        selection = document['cases'][args.case]
    else:
        selection = {field: getattr(args, field) for field in fields}
        if selection['mass_segment'] is None:
            selection['mass_segment'] = 2
        if selection['liver_segment'] is None:
            selection['liver_segment'] = 1
    if (not isinstance(selection, dict) or set(selection) != set(fields)
            or any(not isinstance(selection[name], str) or not selection[name].strip() for name in fields[:4])):
        raise ValueError('Provide explicit PRE/POST series UIDs and acquisition identifiers.')
    if any(type(selection[name]) is not int or selection[name] <= 0 for name in fields[4:]):
        raise ValueError('Published segment identifiers must be positive integers.')
    return selection


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, default=Path('/data'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--case', required=True)
    parser.add_argument('--manifest', type=Path)
    for name in ('pre-series-uid', 'post-series-uid', 'pre-acquisition', 'post-acquisition'):
        parser.add_argument('--' + name)
    parser.add_argument('--mass-segment', type=int)
    parser.add_argument('--liver-segment', type=int)
    parser.add_argument('--variants', nargs='+', choices=VARIANTS, default=list(VARIANTS))
    args = parser.parse_args()
    if len(set(args.variants)) != len(args.variants):
        parser.error('Select each experimental variant at most once.')
    try:
        return execute(args, selection_from_args(args))
    except (ValueError, KeyError, OSError) as error:
        parser.exit(2, f'Experiment failed: {error}\n')


if __name__ == '__main__':
    raise SystemExit(main())
