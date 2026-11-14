"""Read-only native views of saved POST masks before and after 3D refinement.

The baseline already includes a 2-mm graph cut. The alternative is the direct
nnInteractive output conditioned on that POST mask, without another graph cut.
Neither is an independent reference. No predictor or annotation store is used.
"""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re

import numpy as np

from hcclab.candidates import file_sha256
from .audit_post_slices import slice_metrics
from .plot_boundary import read_json, read_mask, verified_artifact, verify_inputs
from . import plot_prompt_conditioning as shared


METHODS = {'before': 'medsam2_boundary_2mm', 'after': 'nninteractive_initial_segmentation'}
MASK_FILES = {'before': 'before.npz', 'after': 'after.npz'}
FIGURE_OPTIONS = {
    'column_labels': ('Before · MedSAM2 + 2 mm', 'After · nnInteractive 3D'),
    'method_caption': 'Before: 2 mm refinement. After: no postprocessing.',
    'evidence_caption': 'Experimental 3D refinement uses the saved POST mask. Baseline verified, not rerun. Outside the write envelope: inherited baseline.',
}


def verify_write_envelope(inference, before, after):
    """Check inherited values without equating a write envelope to full inference."""
    coverage = inference.get('coverage', {})
    bounds = np.asarray(coverage.get('reported_write_envelope_zyx'), dtype=object)
    if (coverage.get('interpretation') != 'envelope_of_writes_not_exact_inference_coverage'
            or coverage.get('outside_envelope') != 'inherited_baseline_not_reevaluated'
            or coverage.get('outside_envelope_unchanged') is not True
            or coverage.get('all_native_slices_retained') is not True
            or bounds.shape != (2, 3)
            or any(isinstance(value, bool) or not isinstance(value, int) for value in bounds.flat)):
        raise ValueError('The native write envelope and inherited-baseline scope must be explicit.')
    low, high = bounds.astype(np.int64)
    if np.any(low < 0) or np.any(high > before.shape) or np.any(low >= high):
        raise ValueError('The native write envelope lies outside the POST grid.')
    for axis in range(3):
        for portion in (slice(0, low[axis]), slice(high[axis], before.shape[axis])):
            selection = [slice(None)]*3; selection[axis] = portion
            if not np.array_equal(before[tuple(selection)], after[tuple(selection)]):
                raise ValueError('The output changed outside the native write envelope.')
    envelope = tuple(slice(a, b) for a, b in zip(low, high))
    outside = int(before.sum()-before[envelope].sum())
    declared = coverage.get('baseline_voxels_outside_envelope')
    if type(declared) is not int or declared != outside:
        raise ValueError('Inherited baseline counts disagree with the native write envelope.')


def read_inputs(data, run):
    """Verify native sources, immutable baseline bytes and direct neural output."""
    run = run.resolve()
    manifest_path = run/'manifest.json'
    manifest_hash = file_sha256(manifest_path)
    manifest = read_json(manifest_path)
    if (manifest.get('schema_version') != 1 or manifest.get('kind') != 'post_neural_refinement'
            or manifest.get('status') != 'completed' or manifest.get('source_integrity_verified') is not True
            or manifest.get('primary_change') != METHODS['after']
            or manifest.get('baseline_saved_exact') is not True
            or manifest.get('baseline_regenerated') is not False
            or manifest.get('postprocessing_after_nninteractive') != 'none'
            or re.fullmatch(r'[0-9a-f]{64}', str(manifest.get('baseline_manifest_sha256'))) is None
            or manifest.get('baseline_mask_sha256') != manifest.get('artifacts_sha256', {}).get('before.npz')):
        raise ValueError('A completed, source-verified neural comparison with exact saved baseline bytes is required.')
    loaded = shared.load_verified_post(data, manifest)
    artifacts = {name: verified_artifact(run, name, manifest)
                 for name in (*MASK_FILES.values(), 'comparison.json')}
    masks = {key: read_mask(artifacts[name], loaded['hu'].shape) for key, name in MASK_FILES.items()}
    if not masks['before'].any() or any(np.any(mask & ~loaded['valid']) for mask in masks.values()):
        raise ValueError('The baseline must be nonempty and both masks must exclude invalid CT support.')
    comparison = read_json(artifacts['comparison.json'])
    if comparison.get('baseline_saved_exact') is not True:
        raise ValueError('The comparison does not establish exact saved baseline bytes.')
    for name, method in METHODS.items():
        report = comparison[name]
        if (report.get('method') != method or report.get('status') != 'experimental_unreviewed'
                or report['metrics']['voxels'] != int(masks[name].sum())):
            raise ValueError('Comparison method, status or counts disagree with the verified masks.')
    inference = comparison['after']['inference']
    if (inference.get('status') != 'completed_unreviewed'
            or inference.get('voxels') != int(masks['after'].sum())
            or inference.get('shape_zyx') != list(loaded['hu'].shape)
            or inference.get('spacing_zyx_mm') != list(loaded['spacing'])
            or inference.get('initial_array_sha256') != hashlib.sha256(masks['before'].tobytes(order='C')).hexdigest()):
        raise ValueError('Neural inference does not match the native grid and saved initial mask.')
    verify_write_envelope(inference, masks['before'], masks['after'])
    verify_inputs(loaded['ct_paths'], loaded['ct_hashes'], manifest_path, manifest_hash, artifacts, manifest)
    return {**loaded, **masks, 'manifest': manifest, 'manifest_path': manifest_path,
            'manifest_sha256': manifest_hash, 'artifacts': artifacts, 'comparison': comparison}


def descriptive_rows(inputs):
    """Keep every acquired plane, including empty outputs; do not invent raw masks."""
    summaries = {name: slice_metrics(inputs['hu'], inputs[name], inputs[name], inputs['valid'], inputs['spacing'])
                 for name in METHODS}
    rows = []
    for z, (before, after) in enumerate(zip(inputs['before'], inputs['after'])):
        row = {'array_index': z, 'app_slice': z+1,
               'added_voxels': int((after & ~before).sum()),
               'removed_voxels': int((before & ~after).sum()),
               'newly_occupied_slice': bool(after.any() and not before.any()),
               'lost_occupied_slice': bool(before.any() and not after.any())}
        for name in METHODS:
            summary = summaries[name][z]
            row[name+'_voxels'] = summary['refined_voxels']
            row[name+'_area_mm2'] = summary['refined_area_mm2']
            for key, value in summary.items():
                if key not in {'array_index', 'app_slice', 'added_voxels', 'removed_voxels'} and not key.startswith(('raw_', 'refined_')):
                    row[name+'_'+key] = value
        rows.append(row)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True, help='New output directory outside code, data and input run.')
    args = parser.parse_args()
    folder = args.output.resolve()
    if any(folder.is_relative_to(root.resolve()) for root in (Path(__file__).resolve().parents[1], args.data, args.run)):
        parser.error('Output must be outside code, source data and the immutable input run.')
    if folder.exists() or args.output.is_symlink():
        parser.error('Output already exists; choose a new directory.')
    inputs = read_inputs(args.data, args.run)
    selection = shared.display_selection(inputs['before'], inputs['after'], inputs['spacing'])
    rows = descriptive_rows(inputs)
    folder.mkdir(parents=True, exist_ok=False)
    with (folder/'slices.csv').open('x', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    with (folder/'slices.json').open('x') as stream:
        json.dump(rows, stream, indent=2, allow_nan=False); stream.write('\n')
    shared.save_figures(inputs, selection, folder, **FIGURE_OPTIONS)
    verify_inputs(inputs['ct_paths'], inputs['ct_hashes'], inputs['manifest_path'], inputs['manifest_sha256'],
                  inputs['artifacts'], inputs['manifest'])
    manifest, comparison = inputs['manifest'], inputs['comparison']
    record = {'schema_version': 1, 'kind': 'post_neural_refinement_visual_review',
              'created_utc': datetime.now(timezone.utc).isoformat(), 'case_id': manifest['case_id'],
              'selection': manifest['selection'], 'post_sources': manifest['sources']['post'],
              'run_manifest_sha256': inputs['manifest_sha256'], 'input_integrity_verified': True,
              'baseline_manifest_sha256': manifest['baseline_manifest_sha256'],
              'baseline_mask_sha256': manifest['baseline_mask_sha256'], 'baseline_saved_exact': True,
              'mask_artifacts_sha256': {key: manifest['artifacts_sha256'][name] for key, name in MASK_FILES.items()},
              'primary_change': manifest['primary_change'], 'methods': METHODS, **FIGURE_OPTIONS,
              'scope': 'POST-mask-conditioned refinement; no independent anatomical reference or treatment response.',
              'model': {'before': manifest.get('baseline_model'), 'after': manifest.get('model')},
              'inference': {'before': manifest.get('baseline_inference'), 'after': comparison['after']['inference']},
              'inference_scope_note': 'Recorded model fields/crops are distinct from display bounds. A written bbox is not proof every voxel inside was evaluated.',
              'counts': {key: int(inputs[key].sum()) for key in MASK_FILES},
              'bounds_zyx_halfopen': [value.tolist() for value in selection.pop('bounds')], **selection,
              'total_acquired_slices_in_csv': len(rows), 'margin_mm': shared.MARGIN_MM,
              'windows': shared.WINDOWS, 'primary_window': shared.WINDOWS[0], 'colors': shared.COLORS,
              'image_interpolation': 'nearest', 'mask_contour': 'Internal 4-connected pixel boundary; no smoothing.',
              'display_geometry': 'Native array planes with physical aspect; no oblique reformatting.',
              'script_sha256': file_sha256(Path(__file__)),
              'helpers_sha256': {name: file_sha256(Path(__file__).with_name(name))
                  for name in ('plot_prompt_conditioning.py', 'plot_boundary.py', 'audit_post_slices.py')},
              'software': {name: importlib.metadata.version(name) for name in ('numpy', 'scipy', 'pydicom', 'matplotlib')},
              'outputs_sha256': {path.name: file_sha256(path) for path in sorted(folder.iterdir())}}
    with (folder/'manifest.json').open('x') as stream:
        json.dump(record, stream, indent=2, allow_nan=False); stream.write('\n')
    print(folder)


if __name__ == '__main__':
    main()
