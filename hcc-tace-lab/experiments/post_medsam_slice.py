"""Refinamiento axial independiente desde un baseline POST congelado.

Cada caja proviene de la máscara POST en tres planos vecinos. Solo se procesan
planos ya ocupados; los demás se heredan, sin atribuirles ausencia comprobada.
No usa PRE como salida, no repite el registro ni escribe candidatas de la app.
"""
import argparse
from datetime import datetime, timezone
import importlib.metadata
from pathlib import Path
import platform
import shutil
import time

import numpy as np

from hcclab.candidates import file_sha256
from .medsam_slice_refine import refine_with_medsam
from .plot_boundary import read_inputs
from .post_neural_refinement import mask_descriptors, initial_mask_retention, process_peak_rss_mib
from .post_prompt_conditioning import verify_run_inputs
from .post_tracking import safe_output_directory, write_json

SCOPE = ('Targeted independent axial refinement conditioned on frozen POST predictions; '
         'unprocessed planes inherit the baseline. Development comparison without '
         'independent POST reference; not anatomical accuracy or treatment response.')


def save_probabilities(folder, before, after, intermediates):
    """Conservar solo las puntuaciones realmente inferidas y su índice nativo."""
    indices = np.asarray(intermediates['slice_indices'])
    expected = np.flatnonzero(before.any(axis=(1, 2)))
    probabilities = np.asarray(intermediates['probabilities'])
    if (indices.dtype.kind not in 'iu' or not np.array_equal(indices, expected)
            or probabilities.shape != (len(expected), *before.shape[1:])
            or probabilities.dtype != np.float32):
        raise ValueError('Probability planes must match exactly the occupied native baseline slices.')
    for z, plane in zip(indices, probabilities):
        if (not np.isfinite(plane).all() or plane.min() < 0 or plane.max() > 1
                or not np.array_equal(after[z], plane > .5)):
            raise ValueError('Saved probabilities do not match the unfiltered model output.')
    untouched = np.ones(len(before), bool); untouched[indices] = False
    if not np.array_equal(after[untouched], before[untouched]):
        raise ValueError('Unprocessed planes must inherit the initial POST prediction.')
    np.savez_compressed(folder/'probabilities.npz', slice_indices=indices.astype(np.int32),
                        probabilities=probabilities)


def execute(args):
    if args.output.resolve().is_relative_to(args.baseline.resolve()):
        raise ValueError('Output must be outside the frozen baseline directory.')
    if args.output.resolve().is_relative_to(args.model_dir.resolve()):
        raise ValueError('Output must be outside the model artifact directory.')
    inputs = read_inputs(args.data, args.baseline)
    verify_run_inputs(inputs, args.data)
    old = inputs['manifest']
    before = inputs['refined']
    folder = safe_output_directory(args.output, args.data)
    started = time.perf_counter()
    manifest = {
        'schema_version': 1, 'kind': 'post_medsam_slice_refinement', 'status': 'running',
        'primary_change': 'medsam_vit_b_neighbor_boxes',
        'created_utc': datetime.now(timezone.utc).isoformat(), 'scope': SCOPE,
        'case_id': old['case_id'], 'selection': old['selection'], 'sources': old['sources'],
        'baseline_manifest_sha256': inputs['manifest_sha256'],
        'baseline_mask_sha256': old['artifacts_sha256']['refined-2mm.npz'],
        'baseline_model': old['model'], 'baseline_inference': old['inference'],
        'initial_mask_source': 'frozen_POST_medsam2_boundary_2mm',
        'baseline_saved_exact': False, 'baseline_regenerated': False,
        'registration_recomputed': False, 'localization': old['localization'],
        'source_integrity_verified': False, 'app_candidate_written': False,
        'postprocessing_after_medsam': 'none', 'automatic_parameter_selection': False,
        'implementation_sha256': {
            str(p.relative_to(Path(__file__).resolve().parents[1])): file_sha256(p)
            for parent in (Path(__file__).parent, Path(__file__).resolve().parents[1]/'hcclab')
            for p in parent.glob('*.py')},
        'software': {name: importlib.metadata.version(name) for name in
                     ('numpy', 'scipy', 'pydicom', 'torch', 'torchvision', 'scikit-image')},
        'platform': {'python': platform.python_version(), 'system': platform.system(),
                     'machine': platform.machine()},
    }
    write_json(folder/'manifest.json', manifest)
    print(f'{old["case_id"]}: MedSAM independent axial refinement; {folder}', flush=True)
    try:
        shutil.copyfile(inputs['artifacts']['refined-2mm.npz'], folder/'before.npz')
        if file_sha256(folder/'before.npz') != manifest['baseline_mask_sha256']:
            raise ValueError('The saved baseline is not an exact copy of its frozen artifact.')
        manifest['baseline_saved_exact'] = True
        write_json(folder/'manifest.json', manifest)
        intermediates = {}
        after, diagnostics = refine_with_medsam(
            inputs['hu'], before, inputs['spacing'],
            checkpoint_path=args.model_dir/'medsam_vit_b.pth',
            valid_mask=inputs['valid'], intermediates=intermediates)
        if (not isinstance(after, np.ndarray) or after.dtype != np.bool_ or after.shape != before.shape
                or np.any(after & ~inputs['valid'])):
            raise ValueError('Refinement must return a native boolean mask excluding invalid CT support.')
        # Re-read the exact input artifact to catch accidental in-place mutation.
        with np.load(folder/'before.npz', allow_pickle=False) as arrays:
            if not np.array_equal(before, arrays['mask']):
                raise ValueError('Refinement mutated the frozen input mask in memory.')
        verify_run_inputs(inputs, args.data)
        save_probabilities(folder, before, after, intermediates)
        np.savez_compressed(folder/'after.npz', mask=after)
        reports = {}
        for name, mask, method in (
            ('before', before, 'medsam2_boundary_2mm'),
            ('after', after, 'medsam_vit_b_neighbor_boxes')):
            reports[name] = {'method': method, 'status': 'experimental_unreviewed',
                             'metrics': mask_descriptors(mask, before, inputs['valid'],
                                                        inputs['spacing'], old['localization'])}
        reports['after']['inference'] = diagnostics
        reports['after']['initial_mask_retention'] = initial_mask_retention(before, after, inputs['spacing'])
        comparison = {'scope': SCOPE, 'baseline_saved_exact': True, **reports}
        write_json(folder/'comparison.json', comparison)
        verify_run_inputs(inputs, args.data)
        artifacts = {name: file_sha256(folder/name) for name in ('before.npz', 'after.npz', 'probabilities.npz', 'comparison.json')}
        manifest.update(status='completed', source_integrity_verified=True, artifacts_sha256=artifacts,
                        inference=diagnostics, model=diagnostics.get('model'),
                        elapsed_seconds=time.perf_counter()-started,
                        process_peak_rss_mib=process_peak_rss_mib())
        write_json(folder/'manifest.json', manifest)
        print(f'Completed: {int(before.sum())} -> {int(after.sum())} voxels; {folder}', flush=True)
        return 0
    except Exception as error:
        manifest.update(status='failed', error_type=type(error).__name__, error=str(error),
                        elapsed_seconds=time.perf_counter()-started,
                        process_peak_rss_mib=process_peak_rss_mib())
        if getattr(error, 'diagnostics', None) is not None:
            manifest['failure_diagnostics'] = error.diagnostics
        write_json(folder/'manifest.json', manifest)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('data', 'baseline', 'model-dir', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    args = parser.parse_args()
    try:
        return execute(args)
    except (ValueError, KeyError, OSError) as error:
        parser.exit(2, f'MedSAM slice experiment failed: {error}\n')


if __name__ == '__main__':
    raise SystemExit(main())
