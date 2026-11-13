"""Read-only, all-slice audit of a source-verified POST boundary experiment.

Writes descriptive CSV/JSON for every native slice and paginated image panels
for every occupied slice plus three neighbouring slices on either end.
No inference, candidate-store writes, anatomical labels or accuracy estimates.
"""
import argparse
import csv
from datetime import datetime, timezone
import importlib.metadata
import json
from pathlib import Path

import numpy as np
from scipy import ndimage

from hcclab.candidates import file_sha256
from .plot_boundary import read_inputs, verify_inputs, COLORS


CROSS = ndimage.generate_binary_structure(2, 1)
EIGHT = ndimage.generate_binary_structure(2, 2)


def slice_metrics(hu, raw, refined, valid, spacing):
    """Describe each native plane; empty masks retain null, not fake, statistics.

    Components use 4-connectivity; enclosed background (holes) uses the dual
    8-connectivity. Gradient is the magnitude of native in-plane finite
    differences in HU/mm, without smoothing, sampled on the internal contour.
    Centroid jumps compare consecutive occupied slices only and are in-plane.
    """
    hu, raw, refined, valid = map(np.asarray, (hu, raw, refined, valid))
    spacing = np.asarray(spacing, dtype=float)
    if (hu.ndim != 3 or min(hu.shape) < 2 or any(a.shape != hu.shape for a in (raw, refined, valid))
            or any(a.dtype != np.bool_ for a in (raw, refined, valid))
            or spacing.shape != (3,) or not np.all(np.isfinite(spacing)) or np.any(spacing <= 0)
            or not np.all(np.isfinite(hu[valid])) or np.any((raw | refined) & ~valid)):
        raise ValueError('Audit requires compatible native finite HU, binary masks and positive spacing.')
    dy, dx = spacing[1:]
    rows, previous = [], None
    for z, mask in enumerate(refined):
        old = raw[z]
        coords = np.argwhere(mask)
        n, n_raw = len(coords), int(old.sum())
        centroid = coords.mean(axis=0) if n else None
        _, components = ndimage.label(mask, structure=CROSS)
        holes = ndimage.binary_fill_holes(mask, structure=EIGHT) & ~mask
        _, hole_count = ndimage.label(holes, structure=EIGHT)
        boundary = mask & ~ndimage.binary_erosion(mask, structure=CROSS, border_value=0)
        # Exclude neighbours of padding from derivative statistics. Copy avoids
        # changing HU source values even if invalid padding contains NaN.
        image = np.where(valid[z], hu[z], 0).astype(float)
        gy, gx = np.gradient(image, dy, dx)
        supported = ndimage.binary_erosion(valid[z], structure=CROSS, border_value=0)
        gradients = np.hypot(gy, gx)[boundary & supported]
        values = hu[z][mask]
        row = {'array_index': z, 'app_slice': z + 1, 'raw_voxels': n_raw, 'refined_voxels': n,
               'raw_area_mm2': n_raw * dy * dx, 'refined_area_mm2': n * dy * dx,
               'added_voxels': int((mask & ~old).sum()), 'removed_voxels': int((old & ~mask).sum()),
               'components_4': int(components), 'holes_8': int(hole_count), 'hole_pixels': int(holes.sum()),
               'centroid_y_px': float(centroid[0]) if n else None,
               'centroid_x_px': float(centroid[1]) if n else None,
               'centroid_jump_from_previous_slice_mm': float(np.linalg.norm((centroid-previous)*[dy, dx]))
                   if n and previous is not None else None,
               'hu_min': float(values.min()) if n else None,
               'hu_median': float(np.median(values)) if n else None,
               'hu_p95': float(np.percentile(values, 95)) if n else None,
               'fraction_at_or_above_display_240hu': float((values >= 240).mean()) if n else None,
               'fraction_at_or_above_display_600hu': float((values >= 600).mean()) if n else None,
               'boundary_gradient_samples': int(len(gradients)),
               'boundary_gradient_median_hu_per_mm': float(np.median(gradients)) if len(gradients) else None,
               'boundary_gradient_p10_hu_per_mm': float(np.percentile(gradients, 10)) if len(gradients) else None,
               'boundary_gradient_p90_hu_per_mm': float(np.percentile(gradients, 90)) if len(gradients) else None}
        rows.append(row)
        previous = centroid
    return rows


def selected_slices(raw, refined, neighbours=3):
    """Include all occupied planes, internal empty planes and both end contexts."""
    occupied = np.flatnonzero((raw | refined).any(axis=(1, 2)))
    if not len(occupied):
        raise ValueError('There is no occupied slice to review.')
    return list(range(max(0, int(occupied[0])-neighbours),
                      min(len(raw), int(occupied[-1])+neighbours+1)))


def crop_bounds(raw, refined, spacing, margin_mm=20):
    coordinates = np.argwhere((raw | refined).any(axis=0))
    padding = np.ceil(margin_mm/np.asarray(spacing[1:])).astype(int)
    return (np.maximum(0, coordinates.min(axis=0)-padding),
            np.minimum(raw.shape[1:], coordinates.max(axis=0)+1+padding))


def make_page(inputs, rows, indices, bounds, page_number):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    low, high = bounds
    sy, sx = slice(int(low[0]), int(high[0])), slice(int(low[1]), int(high[1]))
    dy, dx = inputs['spacing'][1:]
    with plt.rc_context({'font.family': 'DejaVu Sans', 'font.size': 9, 'savefig.facecolor': 'white'}):
        fig, axes = plt.subplots(len(indices), 6, figsize=(19, 2.9*len(indices)+1.8), squeeze=False)
        fig.subplots_adjust(left=.07, right=.99, bottom=.055, top=1-1.4/fig.get_figheight(),
                            hspace=.17, wspace=.045)
        headings = ['CT context\nW400 L40', 'CT detail\nW400 L40', 'Current mask\nW400 L40',
                    'Raw mask\nW400 L40', 'Added / removed\nW400 L40', 'Current mask\nW800 L200']
        for j, title in enumerate(headings):
            axes[0, j].set_title(title, fontsize=10, fontweight='bold', pad=8)
        for r, z in enumerate(indices):
            image = inputs['hu'][z]
            raw, refined = inputs['raw'][z, sy, sx], inputs['refined'][z, sy, sx]
            for col, ax in enumerate(axes[r]):
                view = image if col == 0 else image[sy, sx]
                h, w = np.asarray(view.shape)*[dy, dx]
                extent = (0, w, h, 0)
                ax.imshow(view, cmap='gray', vmin=-200 if col == 5 else -160,
                          vmax=600 if col == 5 else 240, interpolation='nearest', extent=extent)
                if col == 0:
                    ax.add_patch(Rectangle((low[1]*dx, low[0]*dy), (high[1]-low[1])*dx,
                                           (high[0]-low[0])*dy, fill=False, edgecolor='white', lw=.8))
                if col in (2, 3, 5):
                    mask = raw if col == 3 else refined
                    edge = mask & ~ndimage.binary_erosion(mask, structure=CROSS, border_value=0)
                    overlay = np.zeros((*mask.shape, 4))
                    overlay[edge] = (*matplotlib.colors.to_rgb(COLORS['raw' if col == 3 else 'refined']), 1)
                    ax.imshow(overlay, interpolation='nearest', extent=extent)
                if col == 4:
                    overlay = np.zeros((*raw.shape, 4))
                    for selected, name in ((refined & ~raw, 'added'), (raw & ~refined, 'removed')):
                        overlay[selected] = (*matplotlib.colors.to_rgb(COLORS[name]), 1)
                    ax.imshow(overlay, interpolation='nearest', extent=extent)
                    ax.text(.02, .98, f'+{rows[z]["added_voxels"]} / −{rows[z]["removed_voxels"]}',
                            transform=ax.transAxes, color='white', va='top',
                            bbox={'facecolor': 'black', 'alpha': .75, 'pad': 2, 'edgecolor': 'none'})
                ax.set(xticks=[], yticks=[])
                if col:
                    ax.plot([w-14, w-4], [h-4, h-4], color='white', lw=2)
                    ax.text(w-9, h-6, '10 mm', color='white', ha='center', fontsize=7)
            axes[r, 0].set_ylabel(f'App {z+1} · z={z}\n{rows[z]["refined_voxels"]} px\n'
                                  f'{rows[z]["refined_area_mm2"]:.1f} mm²', fontsize=10)
        fig.suptitle(f'{inputs["manifest"]["case_id"]} · POST acquisition '
                     f'{inputs["manifest"]["selection"]["post_acquisition"]} · page {page_number}\n'
                     'Native slices · current = 2 mm refinement · unreviewed prediction',
                     fontsize=14, fontweight='bold', y=.99)
        fig.text(.5, .018, 'Amber: current contour | Cyan: raw contour | Pink: added | Blue: removed. '
                 'Exact internal 1-pixel contours; no smoothing. No independent reference.',
                 ha='center', fontsize=9)
    return fig


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True, help='New directory outside code/data/input run.')
    args = parser.parse_args()
    folder = args.output.resolve()
    code = Path(__file__).resolve().parents[1]
    if any(folder.is_relative_to(root.resolve()) for root in (code, args.data, args.run)):
        parser.error('Output must be outside code, source data and the immutable experiment run.')
    if args.output.is_symlink() or folder.exists():
        parser.error('Output already exists; choose a new directory.')
    inputs = read_inputs(args.data, args.run)
    rows = slice_metrics(inputs['hu'], inputs['raw'], inputs['refined'], inputs['valid'], inputs['spacing'])
    indices = selected_slices(inputs['raw'], inputs['refined'])
    bounds = crop_bounds(inputs['raw'], inputs['refined'], inputs['spacing'])
    folder.mkdir(parents=True, exist_ok=False)
    with (folder/'slices.csv').open('x', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    with (folder/'slices.json').open('x') as stream:
        json.dump(rows, stream, indent=2, allow_nan=False); stream.write('\n')
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages
    with (folder/'slice-review.pdf').open('xb') as stream, PdfPages(stream) as pdf:
        for page, offset in enumerate(range(0, len(indices), 5), 1):
            fig = make_page(inputs, rows, indices[offset:offset+5], bounds, page)
            with (folder/f'page-{page:02d}.png').open('xb') as image:
                fig.savefig(image, format='png', dpi=150)
            pdf.savefig(fig)
            plt.close(fig)
    verify_inputs(inputs['ct_paths'], inputs['ct_hashes'], inputs['manifest_path'],
                  inputs['manifest_sha256'], inputs['artifacts'], inputs['manifest'])
    record = {'schema_version': 1, 'kind': 'post_slice_descriptive_audit',
              'created_utc': datetime.now(timezone.utc).isoformat(),
              'scope': 'All-slice prediction descriptors and visual review, not anatomical accuracy or treatment response.',
              'case_id': inputs['manifest']['case_id'], 'selection': inputs['manifest']['selection'],
              'post_sources': inputs['manifest']['sources']['post'], 'input_integrity_verified': True,
              'run_manifest_sha256': inputs['manifest_sha256'],
              'raw_mask_sha256': inputs['manifest']['artifacts_sha256']['raw.npz'],
              'current_mask_sha256': inputs['manifest']['artifacts_sha256']['refined-2mm.npz'],
              'total_slices': len(rows), 'visualized_array_indices': indices,
              'crop_yx_halfopen': [v.tolist() for v in bounds], 'crop_margin_mm': 20,
              'mask_contour': 'Exact internal 1-pixel boundary with 4-connectivity; no smoothing or interpolation.',
              'windows': [{'width_hu': 400, 'level_hu': 40}, {'width_hu': 800, 'level_hu': 200}],
              'gradient': 'In-plane central finite differences, one-sided at image edge; valid-neighbour internal contour only; HU/mm.',
              'components': 'Foreground 4-connectivity; enclosed background 8-connectivity.',
              'centroid_jump': 'In-plane physical distance to immediately preceding slice; null if either mask empty.',
              'script_sha256': file_sha256(Path(__file__)),
              'reader_sha256': file_sha256(Path(__file__).with_name('plot_boundary.py')),
              'software': {n: importlib.metadata.version(n) for n in ('numpy', 'scipy', 'pydicom', 'matplotlib')},
              'outputs_sha256': {p.name: file_sha256(p) for p in sorted(folder.iterdir())}}
    with (folder/'manifest.json').open('x') as stream:
        json.dump(record, stream, indent=2, allow_nan=False); stream.write('\n')
    print(folder)


if __name__ == '__main__':
    main()
