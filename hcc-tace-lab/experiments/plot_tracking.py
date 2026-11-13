"""Plot per-slice predictions, not accuracy, from recorded development runs.

Requires manifest-backed reports from the experiment runner.
No CT pixels or masks are loaded. The last record for the bidirectional prompt
slice is used, matching the saved native prediction. Counts must reconcile.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path

import math


LABELS = {'full': 'Campo completo', 'local160': 'Contexto 160 mm',
          'local240': 'Contexto 240 mm', 'local160wide': 'Ventana amplia',
          'local160liver': 'Registro hepático + contexto 160 mm'}
COLORS = ['#222222', '#0072B2', '#D55E00', '#CC79A7', '#009E73']
STYLES = ['-', '--', '-.', ':', (0, (5, 2, 1, 2))]


def read_report(path):
    """Require identical acquisition bytes and geometry before plotting together."""
    report = json.loads(path.read_text())
    manifest_path = path.parent / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    source = manifest['sources']
    post = source['post']
    spacing = post['spacing_zyx_mm']
    if (len(spacing) != 3 or any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in spacing)):
        raise ValueError('Invalid physical spacing in report.')
    frames = [(r['slice_index'], r['area_voxels']) for r in report['inference']['frames']]
    if any(type(z) is not int or type(area) is not int or z < 0 or area < 0 for z, area in frames):
        raise ValueError('Slice indices and foreground counts must be nonnegative integers.')
    last = dict(frames)
    if sorted(last) != list(range(post['shape_zyx'][0])) or sum(last.values()) != report['metrics']['voxels']:
        raise ValueError('Recorded slices are incomplete or do not match the native mask count.')
    identity = {'case_id': source['case_id'], 'post_series_uid': source['selection']['post_series_uid'],
                'post_acquisition': source['selection']['post_acquisition'], 'post': post}
    return {'identity': identity, 'variant': report['variant'], 'indices': sorted(last),
            'area': [last[z] * spacing[1] * spacing[2] / 100 for z in sorted(last)],
            'source_hashes': {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (path, manifest_path)}}


def require_same_acquisition(rows):
    if any(row['identity'] != rows[0]['identity'] for row in rows[1:]):
        raise ValueError('Compare identical POST acquisition bytes and native geometry only.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reports', type=Path, nargs='+')
    parser.add_argument('--output', type=Path, required=True, help='New output prefix outside the code tree; existing outputs are never overwritten.')
    args = parser.parse_args()
    if not 1 <= len(args.reports) <= 5:
        parser.error('Use one to five reports for the same case and acquisition.')
    records = [read_report(path) for path in args.reports]
    require_same_acquisition(records)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.ticker import MaxNLocator
    import numpy as np
    rows = [(r['identity']['case_id'], r['variant'], np.asarray(r['indices']), np.asarray(r['area'])) for r in records]
    code = Path(__file__).resolve().parents[1]
    if args.output.resolve().is_relative_to(code):
        parser.error('Save generated figures outside the code tree.')
    outputs = {ext: args.output.with_suffix(ext) for ext in ('.png', '.pdf', '.csv', '.json')}
    if any(p.exists() or p.is_symlink() for p in outputs.values()):
        parser.error('Output already exists; choose a new prefix.')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with plt.rc_context({'font.size': 11, 'pdf.fonttype': 42, 'savefig.facecolor': 'white'}):
        figure, axes = plt.subplots(1, 2, figsize=(12, 5), sharey=True, layout='constrained')
        for i, (_, variant, z, area) in enumerate(rows):
            for ax in axes:
                ax.plot(z, area, color=COLORS[i], linestyle=STYLES[i], linewidth=1.8,
                        label=LABELS.get(variant, variant))
        for ax in axes:
            ax.set(xlabel='Índice de corte POST (desde 0)', ylabel='Área predicha (cm²)')
            ax.set_ylim(bottom=0)
            ax.xaxis.set_major_locator(MaxNLocator(integer=True))
            ax.grid(axis='y', alpha=.2)
            ax.spines[['top', 'right']].set_visible(False)
        axes[0].set_title('A · Todos los cortes')
        # Zoom derives from all non-full-field predictions; no selected curve is hidden.
        occupied = [z[area > 0] for _, variant, z, area in rows if variant != 'full']
        occupied = [z for z in occupied if len(z)]
        if occupied:
            low, high = max(0, min(z.min() for z in occupied)-2), max(z.max() for z in occupied)+2
            axes[1].set_xlim(low, high)
        axes[1].set_title('B · Extensión de las variantes locales')
        axes[0].legend(fontsize=8, frameon=False)
        figure.suptitle(f'{rows[0][0]} · Área de la predicción POST por corte', fontsize=15, fontweight='bold')
        figure.supxlabel('Un caso de desarrollo, sin referencia POST independiente. Las curvas no representan exactitud.', fontsize=9)
        figure.savefig(outputs['.png'], dpi=220)
        figure.savefig(outputs['.pdf'])
        plt.close(figure)
    with outputs['.csv'].open('w', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(['case_id', 'variant', 'slice_index_zero_based', 'predicted_area_cm2'])
        for case, variant, z, area in rows:
            writer.writerows((case, variant, int(index), float(value)) for index, value in zip(z, area))
    outputs['.json'].write_text(json.dumps({'scope': 'Prediction sensitivity, not anatomical accuracy.',
        'source_hashes': [r['source_hashes'] for r in records],
        'acquisition': records[0]['identity'],
        'transform': 'foreground pixels * native PixelSpacing[0] * PixelSpacing[1] / 100; cm²',
        'smoothing': 'none', 'independent_units': 1, 'reference_standard': None,
        'software': {'matplotlib': matplotlib.__version__, 'numpy': np.__version__}}, indent=2) + '\n')


if __name__ == '__main__':
    main()
