"""Refinamiento 3D nnInteractive desde una candidata POST congelada.

No vuelve a registrar, entrenar, elegir puntos ni modificar anotaciones de la
app. La entrada inicial es una predicción POST; nunca se llama referencia.
Los descriptores comparan dos métodos sobre la misma TC, no exactitud clínica.
"""
import argparse
from datetime import datetime, timezone
import importlib.metadata
from pathlib import Path
import platform
import resource
import shutil
import sys
import time

import numpy as np
from scipy import ndimage

from hcclab.candidates import file_sha256
from .nninteractive_refine import refine_with_nninteractive
from .plot_boundary import read_inputs
from .post_prompt_conditioning import verify_run_inputs
from .post_tracking import safe_output_directory, write_json
from .tracking_core import compare_predictions, prediction_metrics


SCOPE = ('Automatic 3D refinement conditioned on a frozen POST prediction; '
         'development comparison without independent POST reference. '
         'No anatomical accuracy or treatment-response claim.')


def process_peak_rss_mib():
    """ru_maxrss uses bytes on macOS and KiB on the Linux experiment host."""
    units_per_mib = 1024**2 if sys.platform == 'darwin' else 1024
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/units_per_mib


def mask_descriptors(mask, before, valid, spacing, location):
    """Conteos descriptivos sin imponer continuidad, volumen ni componentes."""
    result = prediction_metrics(mask, location)
    labels, _ = ndimage.label(mask)
    sizes = np.bincount(labels.ravel())[1:]
    del labels
    areas = mask.sum(axis=(1, 2))
    old_areas = before.sum(axis=(1, 2))
    added = int(np.count_nonzero(mask & ~before))
    removed = int(np.count_nonzero(before & ~mask))
    field_touches = []
    for axis, name in enumerate('zyx'):
        for index, suffix in ((0, 'min'), (-1, 'max')):
            if np.take(mask, index, axis=axis).any():
                field_touches.append(name+'_'+suffix)
    result.update(
        volume_cm3=float(result['voxels']*np.prod(spacing)/1000),
        component_sizes_6_voxels=sorted(sizes.astype(int).tolist(), reverse=True),
        secondary_component_voxels=int(result['voxels']-result['largest_component_voxels']),
        added_voxels=added, removed_voxels=removed, changed_voxels=added+removed,
        changed_percent_of_baseline=100*(added+removed)/int(before.sum()),
        invalid_padding_voxels=int(np.count_nonzero(mask & ~valid)),
        native_fov_touch_faces=field_touches,
        occupied_slice_indices=np.flatnonzero(areas).tolist(),
        new_slice_indices=np.flatnonzero((areas > 0) & (old_areas == 0)).tolist(),
        lost_slice_indices=np.flatnonzero((areas == 0) & (old_areas > 0)).tolist(),
        comparison_to_baseline=compare_predictions(before, mask))
    return result


def initial_mask_retention(before, after, spacing):
    """Pérdidas respecto al prior; preservar ese prior no prueba corrección."""
    labels, count = ndimage.label(before)
    sizes = np.bincount(labels.ravel(), minlength=count+1)[1:]
    retained = np.bincount(labels[after], minlength=count+1)[1:]
    del labels
    components = [{'initial_voxels': int(n), 'retained_voxels': int(k),
                   'retained_fraction': float(k/n)} for n, k in zip(sizes, retained)]
    result = {'components': components,
              'extinguished_components': int(np.count_nonzero(retained == 0)),
              'scope': 'Agreement with model-derived input, not lesion preservation or accuracy.',
              'core_distance_mm': 2.0}
    coordinates = [np.flatnonzero(before.any(axis=tuple(i for i in range(3) if i != axis)))
                   for axis in range(3)]
    low = np.array([c[0] for c in coordinates])
    high = np.array([c[-1]+1 for c in coordinates])
    if (low == 0).any() or (high == before.shape).any():
        result['core_status'] = 'not_computed_initial_mask_touches_native_fov'
        return result
    # Include one known background layer around the complete foreground bbox.
    # It gives the same foreground-to-background center distances as the native
    # grid, without allocating a float64 distance volume for the whole CT.
    crop = tuple(slice(int(a-1), int(b+1)) for a, b in zip(low, high))
    core = ndimage.distance_transform_edt(before[crop], sampling=spacing) > 2.0
    n = int(core.sum())
    kept = int(np.count_nonzero(core & after[crop]))
    result.update(core_status='measured', core_voxels=n, retained_core_voxels=kept,
                  removed_core_voxels=n-kept, retained_core_fraction=kept/n if n else None,
                  core_definition='Foreground centers more than 2 mm from any background center.')
    return result


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
        'schema_version': 1, 'kind': 'post_neural_refinement', 'status': 'running',
        'primary_change': 'nninteractive_initial_segmentation',
        'created_utc': datetime.now(timezone.utc).isoformat(), 'scope': SCOPE,
        'case_id': old['case_id'], 'selection': old['selection'], 'sources': old['sources'],
        'baseline_manifest_sha256': inputs['manifest_sha256'],
        'baseline_mask_sha256': old['artifacts_sha256']['refined-2mm.npz'],
        'baseline_model': old['model'], 'baseline_inference': old['inference'],
        'initial_mask_source': 'frozen_POST_medsam2_boundary_2mm',
        'baseline_saved_exact': False, 'baseline_regenerated': False,
        'registration_recomputed': False, 'localization': old['localization'],
        'source_integrity_verified': False, 'app_candidate_written': False,
        'postprocessing_after_nninteractive': 'none', 'automatic_parameter_selection': False,
        'implementation_sha256': {
            str(p.relative_to(Path(__file__).resolve().parents[1])): file_sha256(p)
            for parent in (Path(__file__).parent, Path(__file__).resolve().parents[1]/'hcclab')
            for p in parent.glob('*.py')},
        'software': {name: importlib.metadata.version(name) for name in
                     ('numpy', 'scipy', 'pydicom', 'torch', 'nnInteractive', 'nnunetv2',
                      'dynamic-network-architectures', 'blosc2')},
        'platform': {'python': platform.python_version(), 'system': platform.system(),
                     'machine': platform.machine()},
    }
    write_json(folder/'manifest.json', manifest)
    print(f'{old["case_id"]}: nnInteractive initial-label refinement; {folder}', flush=True)
    try:
        shutil.copyfile(inputs['artifacts']['refined-2mm.npz'], folder/'before.npz')
        if file_sha256(folder/'before.npz') != manifest['baseline_mask_sha256']:
            raise ValueError('The saved baseline is not an exact copy of its frozen artifact.')
        manifest['baseline_saved_exact'] = True
        write_json(folder/'manifest.json', manifest)
        after, diagnostics = refine_with_nninteractive(
            inputs['hu'], before, inputs['spacing'], model_dir=args.model_dir,
            valid_mask=inputs['valid'])
        if (not isinstance(after, np.ndarray) or after.dtype != np.bool_ or after.shape != before.shape
                or np.any(after & ~inputs['valid'])):
            raise ValueError('Refinement must return a native boolean mask excluding invalid CT support.')
        # Re-read the exact input artifact to catch accidental in-place mutation.
        with np.load(folder/'before.npz', allow_pickle=False) as arrays:
            if not np.array_equal(before, arrays['mask']):
                raise ValueError('Refinement mutated the frozen input mask in memory.')
        verify_run_inputs(inputs, args.data)
        np.savez_compressed(folder/'after.npz', mask=after)
        reports = {}
        for name, mask, method in (
            ('before', before, 'medsam2_boundary_2mm'),
            ('after', after, 'nninteractive_initial_segmentation')):
            reports[name] = {'method': method, 'status': 'experimental_unreviewed',
                             'metrics': mask_descriptors(mask, before, inputs['valid'],
                                                        inputs['spacing'], old['localization'])}
        reports['after']['inference'] = diagnostics
        reports['after']['initial_mask_retention'] = initial_mask_retention(before, after, inputs['spacing'])
        comparison = {'scope': SCOPE, 'baseline_saved_exact': True, **reports}
        write_json(folder/'comparison.json', comparison)
        verify_run_inputs(inputs, args.data)
        artifacts = {name: file_sha256(folder/name) for name in ('before.npz', 'after.npz', 'comparison.json')}
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
        parser.exit(2, f'Neural refinement experiment failed: {error}\n')


if __name__ == '__main__':
    raise SystemExit(main())
