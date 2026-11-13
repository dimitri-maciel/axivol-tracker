"""Figura local de TC POST, máscara del modelo y refinamiento de banda de 2 mm.

Ejemplo: python -m experiments.plot_boundary --data /data --run /run-folder
  --output /research-output/HCC006-boundary

Solo lee TC nativa y artefactos con hashes verificados. No importa Lab,
SimpleITK ni el predictor, no infiere y no consulta candidatas de la app.
Los cinco planos se eligen de forma determinista, sin seleccionar por mejora.
"""
import argparse
from datetime import datetime, timezone
import importlib.metadata
import json
from pathlib import Path
import zipfile

import numpy as np

from hcclab.catalog import Catalog
from hcclab.candidates import file_sha256, stack_identity
from hcclab.dicom import stacks
from hcclab.volume import load_hu_volume


MAX_VOXELS = 64_000_000
MARGIN_MM = 15.0
COLORS = {'raw': '#00C9E8', 'refined': '#FFAA33', 'added': '#FF496C', 'removed': '#479BFF'}


def read_json(path):
    if not path.is_file() or path.stat().st_size > 4_000_000:
        raise ValueError('JSON input is missing or exceeds four megabytes.')
    return json.loads(path.read_text())


def verified_artifact(folder, name, manifest):
    """Nombres fijos y archivos dentro del run; no seguir rutas del manifiesto."""
    path = folder/name
    expected = manifest['artifacts_sha256'].get(name)
    if (path.is_symlink() or not path.is_file() or path.resolve().parent != folder
            or not isinstance(expected, str) or file_sha256(path) != expected):
        raise ValueError(f'Artifact integrity failed: {name}.')
    return path


def read_mask(path, shape):
    """Inspecciona tamaño, dtype y cabecera NPY antes de asignar memoria."""
    with zipfile.ZipFile(path) as archive:
        if archive.namelist() != ['mask.npy']:
            raise ValueError('Mask NPZ must contain only mask.npy.')
        info = archive.getinfo('mask.npy')
        if info.file_size > MAX_VOXELS+4096:
            raise ValueError('Mask artifact exceeds the bounded native volume size.')
        with archive.open(info) as stream:
            version = np.lib.format.read_magic(stream)
            if version == (1, 0):
                saved_shape, _, dtype = np.lib.format.read_array_header_1_0(stream)
            elif version == (2, 0):
                saved_shape, _, dtype = np.lib.format.read_array_header_2_0(stream)
            else:
                raise ValueError('Unsupported NPY mask header version.')
            if saved_shape != shape or dtype != np.dtype(bool):
                raise ValueError('Mask must be boolean on the recorded native POST grid.')
    with np.load(path, allow_pickle=False) as arrays:
        mask = arrays['mask']
    if mask.shape != shape or mask.dtype != np.bool_:
        raise ValueError('Mask changed after header validation.')
    return mask


def read_inputs(data, run):
    """Vincula hashes ordenados, identidad física, catálogo y píxeles actuales."""
    run = run.resolve()
    manifest_path = run/'manifest.json'
    manifest_hash = file_sha256(manifest_path)
    manifest = read_json(manifest_path)
    if (manifest.get('schema_version') != 1 or manifest.get('kind') != 'post_boundary_sensitivity'
            or manifest.get('status') != 'completed' or manifest.get('source_integrity_verified') is not True
            or manifest.get('primary_band_mm') != 2):
        raise ValueError('A completed, source-verified 2-mm boundary experiment is required.')
    source, selection = manifest['sources'], manifest['selection']
    if source['case_id'] != manifest['case_id'] or source['selection'] != selection or source['target_label'] != 'Mass':
        raise ValueError('Manifest case, target and selection provenance disagree.')
    catalog = Catalog(data.resolve())
    uid = selection['post_series_uid']
    row = catalog.by_uid.get(uid)
    allowed = {s['uid'] for study in catalog.case(manifest['case_id'])['studies']
               if study['stage'] == 'POST' for s in study['series']}
    if row is None or uid not in allowed or row['PatientID'] != manifest['case_id'] or not catalog.local(row):
        raise ValueError('The selected local CT series is not this case\'s chronological POST study.')
    options = [stack for stack in stacks(catalog.folder(row), uid)
               if stack.slices[0].acquisition == selection['post_acquisition']]
    if len(options) != 1 or options[0].issue:
        raise ValueError('POST acquisition must identify one compatible native stack.')
    stack = options[0]
    post = source['post']
    shape = (len(stack.slices), *stack.slices[0].shape)
    spacing = (stack.interval_mm, *stack.slices[0].spacing)
    if (np.prod(shape) > MAX_VOXELS or list(shape) != post['shape_zyx']
            or stack_identity(stack) != post['stack_identity']
            or list(spacing) != post['spacing_zyx_mm']
            or list(stack.slices[0].position) != post['origin_lps_mm']
            or list(stack.slices[0].orientation) != post['orientation_patient']
            or [s.sop for s in stack.slices] != post['ordered_sop_uids']):
        raise ValueError('POST native geometry or ordered instance references differ from the experiment.')
    paths = [s.path for s in stack.slices]
    hashes = [file_sha256(path) for path in paths]
    if hashes != post['ordered_ct_sha256'] or hashes != manifest['source_hashes_after']['post']:
        raise ValueError('Current POST bytes differ from the recorded experiment.')
    hu, valid = load_hu_volume(stack, return_validity=True, expected_series_uid=uid,
                               expected_study_uid=row['StudyInstanceUID'])
    artifacts = {name: verified_artifact(run, name, manifest)
                 for name in ('raw.npz', 'refined-2mm.npz', 'comparison.json')}
    raw, refined = (read_mask(artifacts[name], shape) for name in ('raw.npz', 'refined-2mm.npz'))
    if not (raw | refined).any() or np.any((raw | refined) & ~valid):
        raise ValueError('Masks must contain foreground and exclude invalid CT support.')
    comparison = read_json(artifacts['comparison.json'])
    for name, mask in [('raw', raw), ('refined-2mm', refined)]:
        report = comparison['predictions'][name]
        if (report.get('status') != 'experimental_unreviewed'
                or report['artifact_sha256'] != manifest['artifacts_sha256'][name+'.npz']
                or report['metrics']['voxels'] != int(mask.sum())):
            raise ValueError('Comparison counts or artifact provenance disagree with the saved mask.')
    verify_inputs(paths, hashes, manifest_path, manifest_hash, artifacts, manifest)
    return {'manifest': manifest, 'manifest_path': manifest_path, 'manifest_sha256': manifest_hash,
            'hu': hu, 'valid': valid, 'raw': raw, 'refined': refined, 'spacing': spacing,
            'ct_paths': paths, 'ct_hashes': hashes, 'artifacts': artifacts, 'comparison': comparison}


def verify_inputs(paths, hashes, manifest_path, manifest_hash, artifacts, manifest):
    if [file_sha256(path) for path in paths] != hashes or file_sha256(manifest_path) != manifest_hash:
        raise ValueError('Source CT or manifest changed while creating the figure.')
    for name, path in artifacts.items():
        if file_sha256(path) != manifest['artifacts_sha256'][name]:
            raise ValueError('Experiment artifacts changed while creating the figure.')


def select_planes(raw, refined, spacing):
    """Tres cuartiles de extensión z y dos planos por centroide de la unión."""
    union = raw | refined
    coordinates = np.argwhere(union)
    low, high = coordinates.min(axis=0), coordinates.max(axis=0)+1
    centroid = coordinates.mean(axis=0)
    padding = np.ceil(MARGIN_MM/np.asarray(spacing)).astype(int)
    bounds = [np.maximum(0, low-padding), np.minimum(union.shape, high+padding)]
    z = [int(np.rint(low[0]+fraction*(high[0]-low[0]-1))) for fraction in (.25, .5, .75)]
    planes = [{'plane': 'axial', 'axis': 0, 'index': index, 'selection': f'z_extent_q{q}'}
              for index, q in zip(z, (25, 50, 75))]
    planes += [{'plane': 'coronal_native', 'axis': 1, 'index': int(np.rint(centroid[1])), 'selection': 'union_centroid'},
               {'plane': 'sagittal_native', 'axis': 2, 'index': int(np.rint(centroid[2])), 'selection': 'union_centroid'}]
    return bounds, planes, centroid.tolist()


def plane_view(array, plane, bounds, spacing):
    low, high = bounds
    cut = [slice(int(a), int(b)) for a, b in zip(low, high)]
    cut[plane['axis']] = plane['index']
    remaining = [axis for axis in range(3) if axis != plane['axis']]
    return array[tuple(cut)], (spacing[remaining[0]], spacing[remaining[1]])


def make_figure(inputs):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    raw, refined, hu = (inputs[key] for key in ('raw', 'refined', 'hu'))
    spacing = inputs['spacing']
    bounds, planes, centroid = select_planes(raw, refined, spacing)
    added, removed = refined & ~raw, raw & ~refined
    manifest = inputs['manifest']
    with plt.rc_context({'font.family': 'DejaVu Sans', 'font.size': 10, 'pdf.fonttype': 42,
                         'savefig.facecolor': 'white'}):
        fig, axes = plt.subplots(5, 4, figsize=(14, 15))
        fig.subplots_adjust(left=.13, right=.985, bottom=.075, top=.85, hspace=.16, wspace=.04)
        for row_index, plane in enumerate(planes):
            image, (dy, dx) = plane_view(hu, plane, bounds, spacing)
            before, _ = plane_view(raw, plane, bounds, spacing)
            after, _ = plane_view(refined, plane, bounds, spacing)
            plus, minus = after & ~before, before & ~after
            height, width = np.asarray(image.shape)*[dy, dx]
            upper = plane['axis'] == 0
            extent = (0, width, height, 0) if upper else (0, width, 0, height)
            x, y = (np.arange(image.shape[1])+.5)*dx, (np.arange(image.shape[0])+.5)*dy
            for column, ax in enumerate(axes[row_index]):
                ax.imshow(image, cmap='gray', vmin=-160, vmax=240, interpolation='nearest',
                          origin='upper' if upper else 'lower', extent=extent, aspect='equal')
                if column in (1, 2):
                    mask = before if column == 1 else after
                    if mask.any() and not mask.all():
                        ax.contour(x, y, mask, levels=[.5], colors=[COLORS['raw' if column == 1 else 'refined']], linewidths=.9)
                if column == 3:
                    overlay = np.zeros((*image.shape, 4), dtype=float)
                    for selected, color in ((plus, COLORS['added']), (minus, COLORS['removed'])):
                        overlay[selected] = (*matplotlib.colors.to_rgb(color), .95)
                    ax.imshow(overlay, interpolation='nearest', origin='upper' if upper else 'lower', extent=extent)
                    ax.text(.98, .98, f'+{int(plus.sum())}  −{int(minus.sum())}', transform=ax.transAxes,
                            ha='right', va='top', color='white', fontsize=9,
                            bbox={'facecolor': 'black', 'alpha': .7, 'edgecolor': 'none', 'pad': 3})
                ax.set(xlim=(0, width), ylim=(height, 0) if upper else (0, height))
                ax.set_xticks([]); ax.set_yticks([])
                for spine in ax.spines.values():
                    spine.set_visible(False)
                if column == 0:
                    # Barra física común, sin convertir píxeles a milímetros a ojo.
                    bar_mm = 10.
                    ax.plot([.06*width, .06*width+bar_mm], [.08*height if not upper else .92*height]*2,
                            color='white', linewidth=2.4, solid_capstyle='butt')
                    ax.text(.06, .12, '10 mm', transform=ax.transAxes, color='white', fontsize=8)
            name = {'axial': 'Axial', 'coronal_native': 'Coronal nativo', 'sagittal_native': 'Sagital nativo'}[plane['plane']]
            axis_name = 'zyx'[plane['axis']]
            axes[row_index, 0].set_ylabel(f'{chr(65+row_index)}   {name}\n{axis_name} = {plane["index"]}',
                                         rotation=0, ha='right', va='center', labelpad=14, fontsize=10)
        for ax, title in zip(axes[0], ('TC POST', 'Modelo · sin refinamiento', 'Refinamiento · 2 mm', 'Cambios respecto del modelo')):
            ax.set_title(title, fontsize=11, fontweight='bold', pad=11)
        fig.suptitle(f'{manifest["case_id"]} · Delimitación POST antes y después del ajuste',
                     x=.13, y=.975, ha='left', fontsize=18, fontweight='bold')
        selection = manifest['selection']
        fig.text(.13, .945, f'Sin referencia POST independiente · adquisición POST {selection["post_acquisition"]}\n'
                 f'Localización: Mass PRE, segmento {selection["mass_segment"]}, adquisición {selection["pre_acquisition"]} · '
                 'CT nativa, sin remuestreo', fontsize=11, ha='left', va='top', linespacing=1.6)
        handles = [Line2D([], [], color=COLORS['raw'], lw=1.5, label='Contorno del modelo'),
                   Line2D([], [], color=COLORS['refined'], lw=1.5, label='Contorno refinado'),
                   Patch(color=COLORS['added'], label='+ Añadidos'), Patch(color=COLORS['removed'], label='− Retirados')]
        fig.legend(handles=handles, loc='upper left', bbox_to_anchor=(.125, .905), ncol=4,
                   frameon=False, fontsize=10, handlelength=2)
        fig.text(.13, .047, f'Modelo: {int(raw.sum()):,} vóxeles  ·  Refinado: {int(refined.sum()):,}  ·  '
                 f'Añadidos: {int(added.sum()):,}  ·  Retirados: {int(removed.sum()):,}', fontsize=10)
        fig.text(.13, .025, 'Misma ventana: nivel 40 HU, ancho 400 HU. Padding excluido de ambas máscaras. '
                 'Índices desde 0.\nCuartiles de extensión axial y centroide de la unión; '
                 'planos nativos XZ/YZ. Los cambios no prueban exactitud anatómica.', fontsize=9, linespacing=1.5)
    record = {'bounds_zyx_halfopen': [b.tolist() for b in bounds], 'margin_mm': MARGIN_MM,
              'planes': planes, 'union_centroid_zyx': centroid, 'window_level_hu': 40,
              'window_width_hu': 400, 'image_interpolation': 'nearest', 'contour_level': .5,
              'contour_interpolation': 'marching_squares_on_binary_pixel_centers',
              'counts': {'raw': int(raw.sum()), 'refined': int(refined.sum()),
                         'added': int(added.sum()), 'removed': int(removed.sum())},
              'colors': COLORS, 'figure_inches': [14, 15], 'png_dpi': 200,
              'display_geometry': 'Native array planes with physical pixel aspect; no oblique reformatting.'}
    return fig, record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True, help='New figure prefix outside code and source data.')
    args = parser.parse_args()
    code, data, run = Path(__file__).resolve().parents[1], args.data.resolve(), args.run.resolve()
    prefix = args.output.resolve()
    if any(prefix.is_relative_to(root) for root in (code, data, run)):
        parser.error('Write figure outputs outside code, source data and the immutable experiment run.')
    outputs = {suffix: prefix.with_suffix(suffix) for suffix in ('.png', '.pdf', '.json')}
    if any(path.exists() or path.is_symlink() for path in outputs.values()):
        parser.error('Output exists; choose a new prefix. Overwrite is not allowed.')
    inputs = read_inputs(data, run)
    figure, display = make_figure(inputs)
    verify_inputs(inputs['ct_paths'], inputs['ct_hashes'], inputs['manifest_path'],
                  inputs['manifest_sha256'], inputs['artifacts'], inputs['manifest'])
    prefix.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive opens enforce no-overwrite even if another process appears
    # between the preliminary check and writing. JSON is the last artifact.
    import matplotlib.pyplot as plt
    with plt.rc_context({'pdf.fonttype': 42, 'savefig.facecolor': 'white'}):
        for suffix in ('.png', '.pdf'):
            with outputs[suffix].open('xb') as stream:
                figure.savefig(stream, format=suffix[1:], dpi=200)
    plt.close(figure)
    provenance = {
        'schema_version': 1, 'kind': 'post_boundary_visual_comparison',
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'scope': 'Development comparison between predictions; no independent POST reference or accuracy estimate.',
        'case_id': inputs['manifest']['case_id'], 'selection': inputs['manifest']['selection'],
        'post_sources': inputs['manifest']['sources']['post'],
        'manifest_sha256': inputs['manifest_sha256'],
        'mask_artifacts_sha256': {name: inputs['manifest']['artifacts_sha256'][name] for name in inputs['artifacts']},
        'input_integrity_verified': True, 'padding_foreground_voxels': 0,
        'outside_inference_field': inputs['manifest']['outside_inference_field'],
        'plot': display, 'script_sha256': file_sha256(Path(__file__)),
        'software': {name: importlib.metadata.version(name) for name in ('numpy', 'scipy', 'pydicom', 'matplotlib')},
        'outputs_sha256': {suffix: file_sha256(outputs[suffix]) for suffix in ('.png', '.pdf')},
    }
    with outputs['.json'].open('x') as stream:
        json.dump(provenance, stream, indent=2, allow_nan=False)
        stream.write('\n')
    print(outputs['.png'])


if __name__ == '__main__':
    main()
