"""MedSAM ViT-B original: refinamiento POST por planos independientes.

La predicción POST inicial proporciona únicamente el ámbito de cortes y cajas;
no es una referencia independiente. Cada caja procede de la unión de ese corte
y sus dos vecinos en la máscara inicial, nunca de una nueva predicción. No se
propaga memoria ni se filtran componentes. Los planos no procesados se heredan
explícitamente: sus ceros no constituyen una detección de ausencia.
"""
import hashlib
import importlib.metadata
import importlib.util
import os
from pathlib import Path
import time

import numpy as np

from hcclab.candidates import file_sha256


SOURCE_COMMIT = 'd71e8a1a99ad751840a22a7fa3ecfb4166fb1488'
CHECKPOINT_BYTES = 375049145
CHECKPOINT_MD5 = '3bb6db55bd0c9ca30b61248bca72f8d6'
# SHA-256 local tras verificar el MD5 publicado por Zenodo.
CHECKPOINT_SHA256 = '34b34b78c1d18cb8c6bf84cf9c00e135d6d6c965699f3c0e31ef1bc9dcb5be74'
SOURCE_TREE_SHA256 = 'b471fbce67496842f908f606dbe50205b3288c90ed9d99a0ab5f7455d110440e'
SOURCE_PY_FILES = 15
MAX_VOXELS = 64_000_000
WINDOW_HU = (-160., 240.)


class MedSAMRefinementError(ValueError):
    """Fallo auditable; nunca convierte una copia del baseline en éxito."""
    def __init__(self, message, diagnostics):
        super().__init__(message)
        self.diagnostics = diagnostics


def verify_assets(checkpoint_path):
    """Valida tamaño, MD5 publicado y SHA-256 fijado sin deserializar pesos."""
    path = Path(checkpoint_path)
    if (not CHECKPOINT_SHA256 or path.is_symlink() or not path.is_file()
            or path.stat().st_size != CHECKPOINT_BYTES):
        raise ValueError('The pinned MedSAM checkpoint is missing or has an unexpected size.')
    sha, md5 = hashlib.sha256(), hashlib.md5()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            sha.update(chunk); md5.update(chunk)
    if sha.hexdigest() != CHECKPOINT_SHA256 or md5.hexdigest() != CHECKPOINT_MD5:
        raise ValueError('The pinned MedSAM checkpoint failed checksum verification.')
    return {'model': 'MedSAM_ViT_B', 'source_commit': SOURCE_COMMIT,
            'checkpoint': {'bytes': CHECKPOINT_BYTES, 'sha256': sha.hexdigest(), 'md5': md5.hexdigest()},
            'weights_source': 'https://zenodo.org/records/10689643',
            'weights_license': 'CC-BY-4.0', 'code_license': 'Apache-2.0',
            'loader': 'official_registry_with_TORCH_FORCE_WEIGHTS_ONLY_LOAD_1'}


def _verify_source():
    spec = importlib.util.find_spec('segment_anything')
    roots = list(spec.submodule_search_locations or ()) if spec else []
    if len(roots) != 1 or not SOURCE_TREE_SHA256:
        raise ValueError('Use the separately installed pinned original MedSAM research environment.')
    root, digest = Path(roots[0]), hashlib.sha256()
    paths = sorted(root.rglob('*.py'))
    for path in paths:
        if path.is_symlink():
            raise ValueError('The pinned MedSAM source must not contain symlinked code.')
        digest.update(path.relative_to(root).as_posix().encode()+b'\0')
        digest.update(bytes.fromhex(file_sha256(path))+b'\n')
    if len(paths) != SOURCE_PY_FILES or digest.hexdigest() != SOURCE_TREE_SHA256:
        raise ValueError('Installed MedSAM Python sources differ from the reviewed pinned package.')
    return {'python_files': len(paths), 'python_tree_sha256': digest.hexdigest()}


def _load_model(checkpoint_path):
    # El constructor oficial usa torch.load sin weights_only explícito. El
    # runtime fijado reconoce esta variable; no hay fallback a pickle libre.
    if os.environ.get('TORCH_FORCE_WEIGHTS_ONLY_LOAD') != '1':
        raise ValueError('MedSAM requires TORCH_FORCE_WEIGHTS_ONLY_LOAD=1 before model loading.')
    provenance = verify_assets(checkpoint_path)
    provenance['installed_source'] = _verify_source()
    import torch
    from segment_anything import sam_model_registry
    torch.set_num_threads(2)
    model = sam_model_registry['vit_b'](checkpoint=str(checkpoint_path)).to('cpu').eval()
    verify_assets(checkpoint_path)
    provenance['software'] = {name: importlib.metadata.version(name) for name in
                              ('torch', 'torchvision', 'numpy', 'scipy', 'scikit-image')}
    return model, provenance


def _validate(hu, initial, spacing_zyx, valid, margin_mm):
    if (not isinstance(hu, np.ndarray) or hu.ndim != 3 or hu.dtype.kind not in 'iuf'
            or hu.dtype.itemsize > 8 or min(hu.shape) < 1 or hu.size > MAX_VOXELS
            or any(not np.isfinite(plane).all() for plane in hu)):
        raise ValueError('POST HU must be finite numeric values on a bounded 3D native grid.')
    if (not isinstance(initial, np.ndarray) or initial.dtype != np.bool_
            or initial.shape != hu.shape or not initial.any()):
        raise ValueError('Initial POST prediction must be a nonempty boolean mask on the native grid.')
    raw = np.asarray(spacing_zyx, dtype=object)
    if raw.shape != (3,) or any(isinstance(v, (bool, np.bool_)) for v in raw):
        raise ValueError('Spacing must contain three positive finite millimeter values in ZYX order.')
    spacing = np.asarray(spacing_zyx, dtype=float)
    if not np.isfinite(spacing).all() or np.any(spacing <= 0):
        raise ValueError('Spacing must contain three positive finite millimeter values in ZYX order.')
    if (isinstance(margin_mm, (bool, np.bool_)) or not isinstance(margin_mm, (int, float, np.integer, np.floating))
            or not np.isfinite(margin_mm) or not 0 < margin_mm <= 20):
        raise ValueError('The physical box margin must be finite, greater than zero and at most 20 mm.')
    if valid is not None and (not isinstance(valid, np.ndarray) or valid.dtype != np.bool_
                              or valid.shape != hu.shape):
        raise ValueError('Pixel validity must be boolean and match the native POST grid.')
    if valid is not None and np.any(initial & ~valid):
        raise ValueError('The initial POST prediction includes invalid pixel padding.')
    lower = min(max(float(plane.min()), WINDOW_HU[0]) for plane in hu)
    upper = max(min(float(plane.max()), WINDOW_HU[1]) for plane in hu)
    lower, upper = np.clip([lower, upper], *WINDOW_HU)
    if upper <= lower:
        raise ValueError('The clipped POST volume has no intensity variation for MedSAM normalization.')
    return spacing, (float(lower), float(upper))


def _box_for_slice(initial, z, spacing_zyx, margin_mm):
    """Caja XYXY de centros inclusivos; margen físico exacto, sin sumar 0.5."""
    union = initial[max(0, z-1):min(len(initial), z+2)].any(axis=0)
    ys, xs = np.flatnonzero(union.any(axis=1)), np.flatnonzero(union.any(axis=0))
    dy, dx = spacing_zyx[1:]
    unbounded = np.array([xs[0]-margin_mm/dx, ys[0]-margin_mm/dy,
                          xs[-1]+margin_mm/dx, ys[-1]+margin_mm/dy], dtype=float)
    height, width = union.shape
    box = np.clip(unbounded, [0, 0, 0, 0], [width, height, width, height])
    clipped = [name for name, differs in zip(('x_min', 'y_min', 'x_max', 'y_max'), box != unbounded) if differs]
    return box, clipped


def _prepare_image(plane_hu, clipped_limits):
    """Etapa CT oficial y entrada de MedSAM_Inference.py, sin ImageNet.

CT: clip W400/L40 y minmax del volumen completo → uint8. Luego RGB repetido,
resize cúbico skimage a 1024 (reflect, antialias), uint8, minmax del plano y
float32 BCHW. Se conservan los HU de padding como fuente; valid se verifica
en la salida, no se usa para inventar píxeles o recortar la predicción.
"""
    from skimage.transform import resize
    lower, upper = clipped_limits
    plane = np.clip(plane_hu.astype(np.float64), *WINDOW_HU)
    image8 = ((plane-lower)/(upper-lower)*255.).astype(np.uint8)
    rgb = np.repeat(image8[:, :, None], 3, axis=-1)
    resized = resize(rgb, (1024, 1024), order=3, mode='reflect',
                     preserve_range=True, anti_aliasing=True).astype(np.uint8)
    normalized = (resized-resized.min())/np.clip(resized.max()-resized.min(), 1e-8, None)
    return np.ascontiguousarray(normalized.transpose(2, 0, 1)[None], dtype=np.float32)


def _predict_slice(model, prepared, box_native, native_shape):
    """Una inferencia independiente; sigmoid precede al resize, como upstream."""
    import torch
    import torch.nn.functional as functional
    height, width = native_shape
    box1024 = box_native/np.array([width, height, width, height])*1024.
    with torch.inference_mode():
        image = torch.from_numpy(prepared)
        embedding = model.image_encoder(image)
        sparse, dense = model.prompt_encoder(points=None, masks=None,
            boxes=torch.as_tensor(box1024[None, None], dtype=torch.float32))
        logits, _ = model.mask_decoder(image_embeddings=embedding,
            image_pe=model.prompt_encoder.get_dense_pe(), sparse_prompt_embeddings=sparse,
            dense_prompt_embeddings=dense, multimask_output=False)
        if tuple(logits.shape) != (1, 1, 256, 256) or not bool(torch.isfinite(logits).all()):
            raise ValueError('MedSAM did not return finite single-mask logits on its 256-pixel grid.')
        probability = functional.interpolate(torch.sigmoid(logits), size=(height, width),
                                             mode='bilinear', align_corners=False)
        return probability[0, 0].cpu().numpy().copy()


def refine_with_medsam(post_hu, initial_mask, spacing_zyx, *, checkpoint_path,
                       valid_mask=None, margin_mm=5.0, intermediates=None):
    """Devuelve máscara nativa y diagnóstico; no promete exactitud ni respuesta.

Solo los planos ocupados en initial_mask se recalculan. Cada plano completo
se reemplaza por la salida independiente del modelo (p>.5), sin limitarla a
la caja ni eliminar componentes. cm³=N·dz·dy·dx/1000. Si se pide intermediates,
probabilities[n,y,x] corresponde a slice_indices[n]; no hay valores inventados
para planos no inferidos. El llamante conserva por separado el baseline.
"""
    spacing, clipped_limits = _validate(post_hu, initial_mask, spacing_zyx, valid_mask, margin_mm)
    if intermediates is not None and (not isinstance(intermediates, dict) or intermediates):
        raise ValueError('Intermediates must be an empty dict or None.')
    selected = np.flatnonzero(initial_mask.any(axis=(1, 2)))
    started = time.perf_counter()
    diagnostics = {'method': 'original_medsam_slice_refinement', 'status': 'evaluating',
        'source_mask_usage': 'POST_prediction_for_plane_selection_and_neighbor_union_boxes_not_reference',
        'shape_zyx': list(post_hu.shape), 'spacing_zyx_mm': spacing.tolist(),
        'initial_voxels': int(initial_mask.sum()),
        'initial_array_sha256': hashlib.sha256(initial_mask.tobytes(order='C')).hexdigest(), 'settings': {
            'device': 'cpu', 'torch_threads': 2, 'model': 'vit_b', 'margin_mm': float(margin_mm),
            'box_source_neighbors': [-1, 0, 1], 'box_convention': 'inclusive_foreground_centers_plus_physical_margin_clipped_to_0_W_H',
            'input_image': 'full_native_axial_plane', 'window_hu': list(WINDOW_HU),
            'clipped_volume_minmax': list(clipped_limits),
            'resize': 'skimage_order3_reflect_antialias_1024_then_uint8',
            'normalization': 'per_resized_plane_minmax_0_1_no_ImageNet',
            'output': 'sigmoid_then_bilinear_native_align_corners_false_then_greater_than_0.5',
            'temporal_memory': False, 'output_component_filtering': False, 'output_box_clipping': False,
            'invalid_input_HU': 'retained_native_values; validity_is_output_check'},
        'coverage': {'processed_slice_indices': selected.tolist(),
                     'unprocessed_planes': 'inherited_baseline_not_reevaluated_not_confirmed_absence',
                     'all_native_slices_retained': True}, 'frames': [],
        'warnings': ['Dependent refinement using model-derived boxes; not an independent reference.',
                     'Original MedSAM is not a detector of target presence or absence.',
                     'HCC-TACE-Seg appears in the original model external benchmark; this is not new external validation.']}
    try:
        loading = time.perf_counter()
        model, provenance = _load_model(checkpoint_path)
        diagnostics['model'], diagnostics['model_load_seconds'] = provenance, time.perf_counter()-loading
        result = initial_mask.copy()
        probabilities = np.empty((len(selected), *initial_mask.shape[1:]), np.float32) if intermediates is not None else None
        inference_started = time.perf_counter()
        for index, z in enumerate(selected):
            frame_started = time.perf_counter()
            box, clipped_faces = _box_for_slice(initial_mask, int(z), spacing, margin_mm)
            prepared = _prepare_image(post_hu[z], clipped_limits)
            prediction = _predict_slice(model, prepared, box.copy(), post_hu.shape[1:])
            if (not isinstance(prediction, np.ndarray) or prediction.shape != post_hu.shape[1:]
                    or prediction.dtype.kind not in 'f' or not np.isfinite(prediction).all()
                    or np.any((prediction < 0) | (prediction > 1))):
                raise ValueError('MedSAM probabilities must be finite values in [0,1] on the native plane.')
            mask = prediction > .5
            if valid_mask is not None and np.any(mask & ~valid_mask[z]):
                raise ValueError('MedSAM predicted invalid pixel padding; no clipping was applied.')
            result[z] = mask
            if probabilities is not None:
                probabilities[index] = prediction
            height, width = post_hu.shape[1:]
            diagnostics['frames'].append({'z': int(z), 'box_xyxy_native': box.tolist(),
                'box_xyxy_1024': (box/np.array([width, height, width, height])*1024).tolist(),
                'margin_clipped_faces': clipped_faces, 'area': int(mask.sum()),
                'constant_model_input': bool(prepared.max() == prepared.min()),
                'seconds': time.perf_counter()-frame_started})
        diagnostics['preprocess_and_inference_seconds'] = time.perf_counter()-inference_started
        verify_assets(checkpoint_path)
        changed = np.flatnonzero(np.any(result != initial_mask, axis=(1, 2))).tolist()
        count = int(result.sum())
        diagnostics.update(status='completed_unreviewed', voxels=count,
            added_voxels=int(np.count_nonzero(result & ~initial_mask)),
            removed_voxels=int(np.count_nonzero(initial_mask & ~result)), changed_slices=changed,
            outcome='empty_prediction_not_confirmed_absence' if not count else 'changed' if changed else 'unchanged',
            volume_cm3=float(count*np.prod(spacing)/1000),
            display_slice_index=int(np.argmax(result.sum(axis=(1, 2)))) if count else None,
            seconds=time.perf_counter()-started)
        if intermediates is not None:
            intermediates.update(slice_indices=selected.copy(), probabilities=probabilities)
        return result, diagnostics
    except Exception as error:
        diagnostics.update(status='failed', error_type=type(error).__name__, seconds=time.perf_counter()-started)
        raise MedSAMRefinementError(str(error), diagnostics) from error
