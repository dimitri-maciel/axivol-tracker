"""Source-verified views of a frozen POST mask and original MedSAM axial output.

The alternative processes initially occupied planes with boxes derived from the
same frozen mask and its immediate neighbours. It is a dependent comparison,
not an independent reference. No inference or app annotation store is used.
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
from .plot_boundary import read_json, read_mask, verified_artifact, verify_inputs
from .plot_neural_refinement import descriptive_rows
from . import plot_prompt_conditioning as shared


METHODS = {'before': 'medsam2_boundary_2mm', 'after': 'medsam_vit_b_neighbor_boxes'}
MASK_FILES = {'before': 'before.npz', 'after': 'after.npz'}
FIGURE_OPTIONS = {
    'column_labels': ('Before · MedSAM2 + 2 mm', 'After · MedSAM ViT-B axial'),
    'method_caption': 'Before: 2 mm refinement. After: no postprocessing.',
    'evidence_caption': 'Experimental axial refinement; boxes from the frozen POST mask. Only initially occupied planes processed. Other planes inherited.',
}


def verify_plane_scope(inference, before, after, spacing):
    """Verify prompts from baseline pixels, never from a new model prediction."""
    selected = np.flatnonzero(before.any(axis=(1, 2))).tolist()
    coverage, settings = inference.get('coverage', {}), inference.get('settings', {})
    reported = coverage.get('processed_slice_indices')
    if (not isinstance(reported, list) or any(type(z) is not int for z in reported)
            or reported != selected
            or coverage.get('unprocessed_planes') != 'inherited_baseline_not_reevaluated_not_confirmed_absence'
            or coverage.get('all_native_slices_retained') is not True
            or settings.get('margin_mm') != 5.0 or settings.get('box_source_neighbors') != [-1, 0, 1]
            or settings.get('input_image') != 'full_native_axial_plane'
            or settings.get('temporal_memory') is not False
            or settings.get('output_box_clipping') is not False
            or settings.get('output_component_filtering') is not False):
        raise ValueError('The declared axial scope must use occupied baseline planes and fixed neighbour boxes.')
    for z in set(range(len(before)))-set(selected):
        if not np.array_equal(before[z], after[z]):
            raise ValueError('An unprocessed plane differs from the inherited baseline.')
    frames = inference.get('frames', [])
    if (not isinstance(frames, list) or len(frames) != len(selected)
            or any(type(frame.get('z')) is not int for frame in frames)
            or [frame['z'] for frame in frames] != selected):
        raise ValueError('The axial frame records must match every processed plane exactly once.')
    height, width = before.shape[1:]
    dy, dx = spacing[1:]
    for z, frame in zip(selected, frames):
        union = before[max(0, z-1):min(len(before), z+2)].any(axis=0)
        ys, xs = np.flatnonzero(union.any(axis=1)), np.flatnonzero(union.any(axis=0))
        raw = np.array([xs[0]-5./dx, ys[0]-5./dy, xs[-1]+5./dx, ys[-1]+5./dy])
        box = np.clip(raw, [0, 0, 0, 0], [width, height, width, height])
        scaled = box/np.array([width, height, width, height])*1024.
        for key, expected in [('box_xyxy_native', box), ('box_xyxy_1024', scaled)]:
            actual = np.asarray(frame.get(key), dtype=float)
            if actual.shape != (4,) or not np.isfinite(actual).all() or not np.allclose(actual, expected, atol=1e-6, rtol=0):
                raise ValueError('A prompt box differs from the frozen neighbour union plus 5 mm.')
        clipped = [name for name, changed in zip(('x_min', 'y_min', 'x_max', 'y_max'), raw != box) if changed]
        if (frame.get('margin_clipped_faces') != clipped or type(frame.get('area')) is not int
                or frame['area'] != int(after[z].sum())):
            raise ValueError('The recorded axial area or prompt-boundary clipping is inconsistent.')


def read_inputs(data, run):
    run = run.resolve()
    manifest_path = run/'manifest.json'
    manifest_hash = file_sha256(manifest_path)
    manifest = read_json(manifest_path)
    if (manifest.get('schema_version') != 1 or manifest.get('kind') != 'post_medsam_slice_refinement'
            or manifest.get('status') != 'completed' or manifest.get('source_integrity_verified') is not True
            or manifest.get('primary_change') != METHODS['after'] or manifest.get('baseline_saved_exact') is not True
            or manifest.get('baseline_regenerated') is not False or manifest.get('postprocessing_after_medsam') != 'none'
            or re.fullmatch(r'[0-9a-f]{64}', str(manifest.get('baseline_manifest_sha256'))) is None
            or manifest.get('baseline_mask_sha256') != manifest.get('artifacts_sha256', {}).get('before.npz')):
        raise ValueError('A completed axial comparison with exact saved baseline bytes is required.')
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
            or inference.get('method') != 'original_medsam_slice_refinement'
            or inference.get('voxels') != int(masks['after'].sum())
            or inference.get('shape_zyx') != list(loaded['hu'].shape)
            or inference.get('spacing_zyx_mm') != list(loaded['spacing'])
            or inference.get('initial_array_sha256') != hashlib.sha256(masks['before'].tobytes(order='C')).hexdigest()):
        raise ValueError('Axial inference does not match the native grid and saved initial mask.')
    verify_plane_scope(inference, masks['before'], masks['after'], loaded['spacing'])
    verify_inputs(loaded['ct_paths'], loaded['ct_hashes'], manifest_path, manifest_hash, artifacts, manifest)
    return {**loaded, **masks, 'manifest': manifest, 'manifest_path': manifest_path,
            'manifest_sha256': manifest_hash, 'artifacts': artifacts, 'comparison': comparison}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('data', 'run', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
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
    record = {'schema_version': 1, 'kind': 'post_medsam_slice_visual_review',
              'created_utc': datetime.now(timezone.utc).isoformat(), 'case_id': manifest['case_id'],
              'selection': manifest['selection'], 'post_sources': manifest['sources']['post'],
              'run_manifest_sha256': inputs['manifest_sha256'], 'input_integrity_verified': True,
              'baseline_manifest_sha256': manifest['baseline_manifest_sha256'],
              'baseline_mask_sha256': manifest['baseline_mask_sha256'], 'baseline_saved_exact': True,
              'mask_artifacts_sha256': {key: manifest['artifacts_sha256'][name] for key, name in MASK_FILES.items()},
              'primary_change': manifest['primary_change'], 'methods': METHODS, **FIGURE_OPTIONS,
              'scope': 'POST-derived plane selection and prompts; no independent anatomical reference or treatment response.',
              'model': {'before': manifest.get('baseline_model'), 'after': manifest.get('model')},
              'inference': {'before': manifest.get('baseline_inference'), 'after': comparison['after']['inference']},
              'inference_scope_note': 'Only initially occupied axial planes are processed. Boxes condition the model; they do not clip its output. Unprocessed planes inherit the baseline.',
              'probability_artifact_verified_by_plotter': False,
              'counts': {key: int(inputs[key].sum()) for key in MASK_FILES},
              'bounds_zyx_halfopen': [value.tolist() for value in selection.pop('bounds')], **selection,
              'total_acquired_slices_in_csv': len(rows), 'margin_mm': shared.MARGIN_MM,
              'windows': shared.WINDOWS, 'primary_window': shared.WINDOWS[0], 'colors': shared.COLORS,
              'image_interpolation': 'nearest', 'mask_contour': 'Internal 4-connected pixel boundary; no smoothing.',
              'display_geometry': 'Native array planes with physical aspect; no oblique reformatting.',
              'script_sha256': file_sha256(Path(__file__)),
              'helpers_sha256': {name: file_sha256(Path(__file__).with_name(name)) for name in
                  ('plot_prompt_conditioning.py', 'plot_neural_refinement.py', 'plot_boundary.py', 'audit_post_slices.py')},
              'software': {name: importlib.metadata.version(name) for name in ('numpy', 'scipy', 'pydicom', 'matplotlib')},
              'outputs_sha256': {path.name: file_sha256(path) for path in sorted(folder.iterdir())}}
    with (folder/'manifest.json').open('x') as stream:
        json.dump(record, stream, indent=2, allow_nan=False); stream.write('\n')
    print(folder)


if __name__ == '__main__':
    main()
