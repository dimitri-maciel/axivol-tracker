"""Read-only figures for frozen POST conditioning or tracking experiments.

Reads native CT and four source-verified masks; never imports the predictor or
writes to the application. The main comparison uses the same 2-mm boundary
refinement in both arms. Raw counts are retained separately, not called truth.
"""
import argparse
import csv
from datetime import datetime, timezone
import importlib.metadata
import json
from pathlib import Path

import numpy as np
from scipy import ndimage

from hcclab.catalog import Catalog
from hcclab.candidates import file_sha256, stack_identity
from hcclab.dicom import stacks
from hcclab.volume import load_hu_volume
from .audit_post_slices import slice_metrics, selected_slices
from .plot_boundary import read_json, read_mask, verified_artifact, verify_inputs, plane_view


MASK_FILES = {'before': 'before.npz', 'before_raw': 'before-raw.npz',
              'after': 'after.npz', 'after_raw': 'after-raw.npz'}
FOCUS_APP_SLICES = (54, 55, 56, 57)
COLORS = {'before': '#00C9E8', 'after': '#FFAA33', 'added': '#FF496C', 'removed': '#479BFF'}
WINDOWS = ({'level_hu': 40, 'width_hu': 400}, {'level_hu': 200, 'width_hu': 800})
MARGIN_MM = 20.0
CHANGES = {
    'binary_reinjection_to_box': ('Before · binary-mask prompt', 'After · box-conditioned tracking'),
    'tracking_multimask_to_single': ('Before · multi-mask tracking', 'After · single-mask tracking'),
    'float_bilinear_to_upstream_uint8': ('Before · float bilinear input', 'After · upstream uint8 input'),
}


def load_verified_post(data, manifest):
    """Read the recorded chronological POST stack without changing native geometry."""
    source, selection = manifest['sources'], manifest['selection']
    if source['case_id'] != manifest['case_id'] or source['selection'] != selection or source['target_label'] != 'Mass':
        raise ValueError('Experiment case, selection and published target disagree.')
    catalog = Catalog(data.resolve())
    uid = selection['post_series_uid']
    row = catalog.by_uid.get(uid)
    allowed = {s['uid'] for study in catalog.case(manifest['case_id'])['studies']
               if study['stage'] == 'POST' for s in study['series']}
    if row is None or uid not in allowed or row['PatientID'] != manifest['case_id'] or not catalog.local(row):
        raise ValueError('The selected CT is not this case\'s local chronological POST study.')
    options = [stack for stack in stacks(catalog.folder(row), uid)
               if stack.slices[0].acquisition == selection['post_acquisition']]
    if len(options) != 1 or options[0].issue:
        raise ValueError('POST acquisition must identify one compatible native stack.')
    stack, post = options[0], source['post']
    shape = (len(stack.slices), *stack.slices[0].shape)
    spacing = (stack.interval_mm, *stack.slices[0].spacing)
    if (np.prod(shape) > 64_000_000 or list(shape) != post['shape_zyx']
            or stack_identity(stack) != post['stack_identity'] or list(spacing) != post['spacing_zyx_mm']
            or list(stack.slices[0].position) != post['origin_lps_mm']
            or list(stack.slices[0].orientation) != post['orientation_patient']
            or [s.sop for s in stack.slices] != post['ordered_sop_uids']):
        raise ValueError('POST geometry or ordered instance references differ from the experiment.')
    paths = [s.path for s in stack.slices]
    hashes = [file_sha256(path) for path in paths]
    if hashes != post['ordered_ct_sha256']:
        raise ValueError('Current POST bytes differ from the recorded experiment.')
    if 'source_hashes_after' in manifest and hashes != manifest['source_hashes_after']['post']:
        raise ValueError('Recorded POST hashes differ before and after the experiment.')
    hu, valid = load_hu_volume(stack, return_validity=True, expected_series_uid=uid,
                               expected_study_uid=row['StudyInstanceUID'])
    return {'hu': hu, 'valid': valid, 'spacing': spacing, 'ct_paths': paths, 'ct_hashes': hashes}


def read_inputs(data, run):
    """Bind artifact hashes to fresh native CT bytes, geometry and ordered SOPs."""
    run = run.resolve()
    manifest_path = run/'manifest.json'
    manifest_hash = file_sha256(manifest_path)
    manifest = read_json(manifest_path)
    if (manifest.get('schema_version') != 1 or manifest.get('kind') != 'post_prompt_conditioning'
            or manifest.get('status') != 'completed' or manifest.get('source_integrity_verified') is not True
            or manifest.get('replay_exact') is not True or manifest.get('boundary_band_mm') != 2
            or manifest.get('registration_recomputed') is not False
            or manifest.get('primary_change') not in CHANGES):
        raise ValueError('A completed, source-verified frozen comparison with exact replay and 2-mm refinement is required.')
    loaded = load_verified_post(data, manifest)
    hu, valid, spacing, paths, hashes = (loaded[key] for key in ('hu', 'valid', 'spacing', 'ct_paths', 'ct_hashes'))
    shape = hu.shape
    artifacts = {name: verified_artifact(run, name, manifest) for name in (*MASK_FILES.values(), 'comparison.json')}
    masks = {key: read_mask(artifacts[name], shape) for key, name in MASK_FILES.items()}
    if any(np.any(mask & ~valid) for mask in masks.values()):
        raise ValueError('A compared mask includes invalid CT support.')
    if not (masks['before'] | masks['after']).any():
        raise ValueError('There is no foreground to display in either final mask.')
    comparison = read_json(artifacts['comparison.json'])
    if comparison.get('replay_exact') is not True:
        raise ValueError('The comparison does not establish exact baseline replay.')
    after_mode = 'box' if manifest['primary_change'] == 'binary_reinjection_to_box' else 'binary_reinjection'
    for name, mode in (('before', 'binary_reinjection'), ('after', after_mode)):
        report = comparison[name]
        if (report.get('prompt_mode') != mode or report.get('status') != 'experimental_unreviewed'
                or report['metrics']['voxels'] != int(masks[name].sum())
                or report['raw_metrics']['voxels'] != int(masks[name+'_raw'].sum())):
            raise ValueError('Comparison mode, status or mask counts disagree with verified artifacts.')
    if manifest['primary_change'] == 'tracking_multimask_to_single' and (
            comparison['before'].get('tracking_multimask') is not True
            or comparison['after'].get('tracking_multimask') is not False):
        raise ValueError('The declared tracking comparison must change multi-mask to single-mask only.')
    if manifest['primary_change'] == 'float_bilinear_to_upstream_uint8':
        for name, mode in (('before', 'float_bilinear'), ('after', 'upstream_uint8')):
            settings = comparison[name].get('inference', {}).get('settings', {})
            if (settings.get('input_mode') != mode or comparison[name].get('tracking_multimask') is not True
                    or settings.get('window_width_hu') != 400 or settings.get('window_level_hu') != 40):
                raise ValueError('The input comparison must preserve multi-mask tracking and W400/L40.')
    verify_inputs(paths, hashes, manifest_path, manifest_hash, artifacts, manifest)
    return {'manifest': manifest, 'manifest_path': manifest_path, 'manifest_sha256': manifest_hash,
            'hu': hu, 'valid': valid, 'spacing': spacing, **masks, 'ct_paths': paths,
            'ct_hashes': hashes, 'artifacts': artifacts, 'comparison': comparison}


def display_selection(before, after, spacing):
    """A common crop and requested focus; plane selection never ranks changes."""
    union = before | after
    coordinates = np.argwhere(union)
    if not len(coordinates):
        raise ValueError('Both compared masks are empty.')
    centroid = coordinates.mean(axis=0)
    padding = np.ceil(MARGIN_MM/np.asarray(spacing)).astype(int)
    bounds = [np.maximum(0, coordinates.min(axis=0)-padding),
              np.minimum(union.shape, coordinates.max(axis=0)+1+padding)]
    focus = [{'axis': 0, 'index': value-1, 'selection': 'requested_app_slice'}
             for value in FOCUS_APP_SLICES if 1 <= value <= union.shape[0]]
    orthogonal = [{'axis': axis, 'index': int(np.rint(centroid[axis])), 'selection': 'union_centroid'}
                  for axis in range(3)]
    atlas = selected_slices(before, after, neighbours=3)
    return {'bounds': bounds, 'centroid_zyx': centroid.tolist(), 'focus': focus,
            'focus_unavailable_app_slices': [value for value in FOCUS_APP_SLICES if value > union.shape[0]],
            'orthogonal': orthogonal, 'atlas_indices': atlas}


def descriptive_rows(inputs):
    """Every acquired slice is retained; no accuracy or preferred-volume score."""
    hu, valid, spacing = (inputs[key] for key in ('hu', 'valid', 'spacing'))
    before, after = inputs['before'], inputs['after']
    summaries = {name: slice_metrics(hu, inputs[name], inputs[name], valid, spacing)
                 for name in ('before', 'after')}
    measured = ['components_4', 'holes_8', 'hole_pixels', 'centroid_y_px', 'centroid_x_px',
                'centroid_jump_from_previous_slice_mm', 'hu_min', 'hu_median', 'hu_p95',
                'fraction_at_or_above_display_240hu', 'fraction_at_or_above_display_600hu',
                'boundary_gradient_samples', 'boundary_gradient_median_hu_per_mm',
                'boundary_gradient_p10_hu_per_mm', 'boundary_gradient_p90_hu_per_mm']
    rows = []
    for z in range(len(hu)):
        row = {'array_index': z, 'app_slice': z+1,
               'added_voxels': int((after[z] & ~before[z]).sum()),
               'removed_voxels': int((before[z] & ~after[z]).sum())}
        for name in ('before', 'after'):
            mask, raw = inputs[name][z], inputs[name+'_raw'][z]
            row.update({name+'_voxels': int(mask.sum()), name+'_raw_voxels': int(raw.sum()),
                        name+'_area_mm2': int(mask.sum())*spacing[1]*spacing[2],
                        name+'_refinement_added_voxels': int((mask & ~raw).sum()),
                        name+'_refinement_removed_voxels': int((raw & ~mask).sum())})
            row.update({name+'_'+key: summaries[name][z][key] for key in measured})
        rows.append(row)
    return rows


def replay_description(comparison):
    replay = comparison.get('replay_exact')
    exact = replay is True or isinstance(replay, dict) and replay.get('raw') is True and replay.get('final') is True
    return 'Baseline replay exact.' if exact else 'Baseline replay not exact or not established; exploratory comparison.'


def make_figure(inputs, planes, bounds, window, title, *, column_labels=None, method_caption=None, evidence_caption=None):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    before, after, hu = (inputs[key] for key in ('before', 'after', 'hu'))
    manifest, spacing = inputs['manifest'], inputs['spacing']
    with plt.rc_context({'font.family': 'DejaVu Sans', 'font.size': 9, 'pdf.fonttype': 42, 'savefig.facecolor': 'white'}):
        fig, axes = plt.subplots(len(planes), 4, figsize=(14, 2.65*len(planes)+2.1), squeeze=False)
        fig.subplots_adjust(left=.09, right=.985, bottom=.085, top=1-1.35/fig.get_figheight(), hspace=.19, wspace=.055)
        for row, plane in enumerate(planes):
            image, (dy, dx) = plane_view(hu, plane, bounds, spacing)
            old, _ = plane_view(before, plane, bounds, spacing)
            new, _ = plane_view(after, plane, bounds, spacing)
            height, width = np.asarray(image.shape)*[dy, dx]
            upper = plane['axis'] == 0
            extent = (0, width, height, 0) if upper else (0, width, 0, height)
            for col, ax in enumerate(axes[row]):
                ax.imshow(image, cmap='gray', vmin=window['level_hu']-window['width_hu']/2,
                          vmax=window['level_hu']+window['width_hu']/2, interpolation='nearest',
                          origin='upper' if upper else 'lower', extent=extent, aspect='equal')
                overlay = np.zeros((*image.shape, 4))
                if col in (1, 2):
                    mask, name = (old, 'before') if col == 1 else (new, 'after')
                    edge = mask & ~ndimage.binary_erosion(mask, structure=ndimage.generate_binary_structure(2, 1), border_value=0)
                    overlay[edge] = (*matplotlib.colors.to_rgb(COLORS[name]), 1.)
                elif col == 3:
                    for selected, name in ((new & ~old, 'added'), (old & ~new, 'removed')):
                        overlay[selected] = (*matplotlib.colors.to_rgb(COLORS[name]), 1.)
                    ax.text(.02, .98, f'+{int((new & ~old).sum())} / −{int((old & ~new).sum())}',
                            transform=ax.transAxes, color='white', va='top', fontsize=8,
                            bbox={'facecolor': 'black', 'alpha': .75, 'pad': 2, 'edgecolor': 'none'})
                if col:
                    ax.imshow(overlay, interpolation='nearest', origin='upper' if upper else 'lower', extent=extent)
                ax.set(xticks=[], yticks=[], xlim=(0, width), ylim=(height, 0) if upper else (0, height))
                if col == 0:
                    length = 10 if width >= 20 else width/3
                    y = height*.92 if upper else height*.08
                    ax.plot([width*.06, width*.06+length], [y, y], color='white', lw=2)
                    ax.text(.06, .12, f'{length:g} mm', transform=ax.transAxes, color='white', fontsize=8)
            axis = plane['axis']
            label = f'App {plane["index"]+1}\nz={plane["index"]}' if axis == 0 else (
                f'Native {"XZ" if axis == 1 else "YZ"}\n{"y" if axis == 1 else "x"}={plane["index"]}')
            axes[row, 0].set_ylabel(label, rotation=0, ha='right', va='center', labelpad=11)
        before_label, after_label = column_labels or CHANGES[manifest['primary_change']]
        if method_caption is None:
            method_caption = 'Both masks: 2 mm refinement.'
        if evidence_caption is None:
            evidence_caption = 'Experimental comparison. '+replay_description(inputs['comparison'])
        for ax, heading in zip(axes[0], ('POST CT', before_label, after_label, 'Added / removed')):
            ax.set_title(heading, fontsize=10, fontweight='bold', pad=9)
        selection = manifest['selection']
        fig.suptitle(f'{manifest["case_id"]} · {title}', x=.09, y=1-.16/fig.get_figheight(),
                     ha='left', fontsize=15, fontweight='bold')
        fig.text(.09, 1-.48/fig.get_figheight(), f'POST acquisition {selection["post_acquisition"]}; '
                 f'localization: PRE acquisition {selection["pre_acquisition"]}, Mass segment {selection["mass_segment"]}. '
                 f'{method_caption} W{window["width_hu"]} L{window["level_hu"]}.', fontsize=9, va='top')
        fig.text(.09, 1-.76/fig.get_figheight(), evidence_caption, fontsize=9, va='top')
        fig.text(.09, .043, 'Cyan: before. Amber: after. Pink: added. Blue: removed. '
                 'Native pixels and physical aspect; exact internal pixel contours, no smoothing.', fontsize=8)
        fig.text(.09, .022, 'No independent POST reference. Differences do not establish anatomical accuracy or treatment response.', fontsize=8)
    return fig


def save_figures(inputs, selection, folder, **figure_options):
    """Export shared native views; keep font settings active until PDF finalization."""
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages

    def save_panel(name, planes, window, title):
        figure = make_figure(inputs, planes, selection['bounds'], window, title, **figure_options)
        for suffix in ('png', 'pdf'):
            with (folder/f'{name}.{suffix}').open('xb') as stream:
                # Font embedding is selected at export, after make_figure's style context.
                with plt.rc_context({'pdf.fonttype': 42}):
                    figure.savefig(stream, format=suffix, dpi=150)
        plt.close(figure)

    if selection['focus']:
        for window in WINDOWS:
            save_panel(f'focus-W{window["width_hu"]}', selection['focus'], window, 'Requested axial review: app slices 54–57')
    save_panel('orthogonal', selection['orthogonal'], WINDOWS[0], 'Orthogonal views at the union centroid')
    # PdfPages embeds fonts when closing, so the context must cover finalization too.
    with plt.rc_context({'pdf.fonttype': 42}), (folder/'atlas.pdf').open('xb') as stream, PdfPages(stream) as pdf:
        for page, offset in enumerate(range(0, len(selection['atlas_indices']), 5), 1):
            planes = [{'axis': 0, 'index': z, 'selection': 'complete_union_extent_plus_three'}
                      for z in selection['atlas_indices'][offset:offset+5]]
            figure = make_figure(inputs, planes, selection['bounds'], WINDOWS[0], f'Complete occupied extent · page {page}', **figure_options)
            with (folder/f'atlas-{page:02d}.png').open('xb') as image:
                figure.savefig(image, format='png', dpi=150)
            pdf.savefig(figure)
            plt.close(figure)


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
    selection = display_selection(inputs['before'], inputs['after'], inputs['spacing'])
    rows = descriptive_rows(inputs)
    folder.mkdir(parents=True, exist_ok=False)
    with (folder/'slices.csv').open('x', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    with (folder/'slices.json').open('x') as stream:
        json.dump(rows, stream, indent=2, allow_nan=False); stream.write('\n')
    save_figures(inputs, selection, folder)
    verify_inputs(inputs['ct_paths'], inputs['ct_hashes'], inputs['manifest_path'],
                  inputs['manifest_sha256'], inputs['artifacts'], inputs['manifest'])
    record = {'schema_version': 1, 'kind': 'post_prompt_conditioning_visual_review',
              'created_utc': datetime.now(timezone.utc).isoformat(), 'case_id': inputs['manifest']['case_id'],
              'selection': inputs['manifest']['selection'], 'post_sources': inputs['manifest']['sources']['post'],
              'run_manifest_sha256': inputs['manifest_sha256'], 'input_integrity_verified': True,
              'mask_artifacts_sha256': {key: inputs['manifest']['artifacts_sha256'][name] for key, name in MASK_FILES.items()},
              'baseline_replay': inputs['comparison'].get('replay_exact'),
              'primary_change': inputs['manifest']['primary_change'],
              'column_labels': CHANGES[inputs['manifest']['primary_change']],
              'scope': 'Exploratory prediction comparison, no independent anatomical reference or treatment response.',
              'counts': {key: int(inputs[key].sum()) for key in MASK_FILES},
              'bounds_zyx_halfopen': [value.tolist() for value in selection.pop('bounds')], **selection,
              'total_acquired_slices_in_csv': len(rows), 'margin_mm': MARGIN_MM, 'windows': WINDOWS,
              'primary_window': WINDOWS[0], 'colors': COLORS, 'image_interpolation': 'nearest',
              'mask_contour': 'Internal 4-connected pixel boundary; no smoothing.',
              'display_geometry': 'Native array planes with physical aspect; no oblique reformatting.',
              'script_sha256': file_sha256(Path(__file__)),
              'helpers_sha256': {name: file_sha256(Path(__file__).with_name(name)) for name in ('plot_boundary.py', 'audit_post_slices.py')},
              'software': {name: importlib.metadata.version(name) for name in ('numpy', 'scipy', 'pydicom', 'matplotlib')},
              'outputs_sha256': {path.name: file_sha256(path) for path in sorted(folder.iterdir())}}
    with (folder/'manifest.json').open('x') as stream:
        json.dump(record, stream, indent=2, allow_nan=False); stream.write('\n')
    print(folder)


if __name__ == '__main__':
    main()
