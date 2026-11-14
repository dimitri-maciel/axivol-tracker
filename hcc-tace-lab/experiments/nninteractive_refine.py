"""Refinamiento 3D experimental desde una predicción POST, nunca una referencia.

El adaptador conserva HU y retícula nativos. La máscara POST completa es una
prior del modelo: no se añaden puntos, cajas, umbrales HU ni etiquetas PRE.
El método oficial decide sus parches y AutoZoom. Su bbox de escritura es una
envolvente, no una certificación de cobertura de inferencia vóxel por vóxel.
"""
import hashlib
import importlib.metadata
import importlib.util
from pathlib import Path
import time

import numpy as np

from hcclab.candidates import file_sha256


SOURCE_COMMIT = 'e3126a029f46dcfd798c8428eec292f52cd438d6'
MODEL_REVISION = '3f308d751c00644e4fde6f09c600264b393b21b5'
# Los 25 .py instalados se compararon con el tar oficial fijado; el wheel
# omite el módulo opcional supervoxel. El loader pickle se documenta aparte.
SOURCE_TREE_SHA256 = '04acbc524c2639c944fdda762c7a8b6f11288212a9ccca7381f7692912557e98'
ASSETS = {
    'LICENSE': (67, '4f60f5747c5506020923866690c2a41a3c74ffa85b7371eac2b02e23185f91d5'),
    'dataset.json': (245, 'e090394ac539b2823b2135173658190245c462cb042d3442487e4829f08252c4'),
    'plans.json': (6287, '64a30a430438302a692a3edadd345a207b3428bea2e5cdc125298757da21f4d4'),
    'inference_session_class.json': (121, '8f43587a9d139fcf7af8776219e9432dab079f985cb0d7da72a4c851b39a4f09'),
    'fold_0/checkpoint_final.pth': (411387150, 'b3ac4421f85457bbd1aa0d87f5e67bcb7bc8e2ce6b824b6ac45077cc5d630ea9'),
}
MAX_VOXELS = 64_000_000


class NeuralRefinementError(ValueError):
    """Fallo auditable, sin presentar una copia de entrada como inferencia exitosa."""
    def __init__(self, message, diagnostics):
        super().__init__(message)
        self.diagnostics = diagnostics


def verify_assets(model_dir):
    """Verifica bytes antes del torch.load oficial (weights_only=False).

El pickle fijado ya se inspeccionó estáticamente en el piloto anterior. Esa
inspección no prueba seguridad formal; se requiere ejecución local aislada,
sin red y con pesos/datos de solo lectura, como documenta el runner.
"""
    root = Path(model_dir)
    files = {}
    for name, (size, expected) in ASSETS.items():
        path = root/name
        if path.is_symlink() or not path.is_file() or path.stat().st_size != size:
            raise ValueError(f'The pinned nnInteractive asset is missing or has an unexpected size: {name}.')
        digest = file_sha256(path)
        if digest != expected:
            raise ValueError(f'The pinned nnInteractive asset failed SHA-256 verification: {name}.')
        files[name] = {'bytes': size, 'sha256': digest}
    return {'source_commit': SOURCE_COMMIT, 'model_revision': MODEL_REVISION,
            'model': 'nnInteractive_v1.0', 'fold': 0, 'files': files,
            'weights_license': 'CC-BY-NC-SA-4.0', 'code_license': 'Apache-2.0',
            'loader': 'official_torch_load_weights_only_false',
            'pickle_review_scope': 'Previous static opcode inspection; not a formal security proof.'}


def _verify_source():
    spec = importlib.util.find_spec('nnInteractive')
    roots = list(spec.submodule_search_locations or ()) if spec else []
    if len(roots) != 1:
        raise ValueError('Use the separately installed pinned nnInteractive research environment.')
    root, digest = Path(roots[0]), hashlib.sha256()
    paths = sorted(root.rglob('*.py'))
    for path in paths:
        if path.is_symlink():
            raise ValueError('The pinned nnInteractive source must not contain symlinked code.')
        digest.update(path.relative_to(root).as_posix().encode()+b'\0')
        digest.update(bytes.fromhex(file_sha256(path))+b'\n')
    if len(paths) != 25 or digest.hexdigest() != SOURCE_TREE_SHA256:
        raise ValueError('Installed nnInteractive Python sources differ from the reviewed pinned package.')
    return {'python_files': len(paths), 'python_tree_sha256': digest.hexdigest()}


def _load_session(model_dir):
    provenance = verify_assets(model_dir)
    provenance['installed_source'] = _verify_source()
    import torch
    from nnInteractive.inference.inference_session import nnInteractiveInferenceSession
    session = nnInteractiveInferenceSession(device=torch.device('cpu'), use_torch_compile=False,
        verbose=True, torch_n_threads=2, do_autozoom=True, interactions_storage='blosc2', enable_undo=False)
    session.initialize_from_trained_model_folder(str(model_dir), use_fold=0)
    verify_assets(model_dir)
    provenance['software'] = {name: importlib.metadata.version(name) for name in
        ('nnInteractive', 'torch', 'nnunetv2', 'dynamic-network-architectures', 'numpy', 'scipy', 'blosc2')}
    return session, provenance


def _validate(hu, initial, spacing_zyx, valid):
    if (not isinstance(hu, np.ndarray) or hu.ndim != 3 or hu.dtype.kind not in 'iuf'
            or hu.dtype.itemsize > 8 or min(hu.shape) < 1 or hu.size > MAX_VOXELS
            or any(not np.isfinite(plane).all() for plane in hu)):
        raise ValueError('POST input must be finite numeric HU on a bounded 3D native grid.')
    if (not isinstance(initial, np.ndarray) or initial.dtype != np.bool_ or initial.shape != hu.shape
            or not initial.any()):
        raise ValueError('Initial POST prediction must be a nonempty boolean mask on the same native grid.')
    raw_spacing = np.asarray(spacing_zyx, dtype=object)
    if (raw_spacing.shape != (3,) or any(isinstance(v, (bool, np.bool_)) for v in raw_spacing)):
        raise ValueError('Spacing must contain three positive finite millimeter values in ZYX order.')
    spacing = np.asarray(spacing_zyx, dtype=float)
    if not np.isfinite(spacing).all() or np.any(spacing <= 0):
        raise ValueError('Spacing must contain three positive finite millimeter values in ZYX order.')
    if valid is not None and (not isinstance(valid, np.ndarray) or valid.dtype != np.bool_ or valid.shape != hu.shape):
        raise ValueError('Pixel validity must be boolean and match the native POST grid.')
    if valid is not None and np.any(initial & ~valid):
        raise ValueError('Initial POST prediction includes invalid pixel padding.')
    if any(np.max(np.abs(plane.astype(np.float64))) > np.finfo(np.float32).max for plane in hu):
        raise ValueError('POST HU cannot be represented as finite float32 input.')
    _check_normalization_region(hu)
    return spacing


def _check_normalization_region(hu):
    """Rechaza regiones constantes tras float32, sin copiar el volumen completo.

El upstream estima mean/std sobre la caja de valores no cero, incluidos sus
ceros internos. Este control temprano usa ocupación; las sumas float32 del
upstream pueden cancelar valores, por lo que también se comprueba su salida
real antes de permitir la inferencia.
"""
    low, high = np.asarray(hu.shape), np.zeros(3, dtype=int)
    for z, native_plane in enumerate(hu):
        occupied = native_plane.astype(np.float32, copy=False) != 0
        rows, columns = np.flatnonzero(occupied.any(axis=1)), np.flatnonzero(occupied.any(axis=0))
        if rows.size:
            low = np.minimum(low, [z, rows[0], columns[0]])
            high = np.maximum(high, [z+1, rows[-1]+1, columns[-1]+1])
    if np.any(high <= low) or np.prod(high-low) < 2:
        raise ValueError('The POST normalization region needs at least two nonzero-bounded voxels.')
    minimum, maximum = np.inf, -np.inf
    for plane in hu[low[0]:high[0], low[1]:high[1], low[2]:high[2]]:
        crop = plane.astype(np.float32, copy=False)
        minimum, maximum = min(minimum, float(crop.min())), max(maximum, float(crop.max()))
    if minimum == maximum:
        raise ValueError('A constant POST normalization region cannot be normalized by nnInteractive.')


def _check_preprocessed_image(image, shape):
    """Comprueba la normalización upstream real: vista CPU y un plano por vez."""
    if image is None or tuple(image.shape) != (1, *shape):
        raise ValueError('The normalized POST image does not preserve the native grid.')
    # El tensor upstream está en CPU. np.asarray comparte su memoria: no se
    # materializa otra copia 3D ni se modifica la imagen normalizada.
    if any(not np.isfinite(np.asarray(image[0, z])).all() for z in range(shape[0])):
        raise ValueError('The normalized POST image contains nonfinite values; inference was not started.')


def _write_envelope(bounds, shape):
    raw = np.asarray(bounds, dtype=object)
    if (raw.shape != (3, 2) or any(isinstance(v, (bool, np.bool_))
            or not isinstance(v, (int, np.integer)) for v in raw.flat)):
        raise ValueError('The official session did not report a valid native write envelope.')
    bbox = raw.astype(np.int64)
    if np.any(bbox[:, 0] < 0) or np.any(bbox[:, 1] > shape) or np.any(bbox[:, 0] >= bbox[:, 1]):
        raise ValueError('The official write envelope lies outside the native POST grid.')
    return bbox[:, 0], bbox[:, 1]


def _outside_unchanged(result, initial, low, high):
    for axis in range(3):
        for portion in (slice(0, low[axis]), slice(high[axis], result.shape[axis])):
            selection = [slice(None)]*3; selection[axis] = portion
            if not np.array_equal(result[tuple(selection)], initial[tuple(selection)]):
                return False
    return True


def refine_with_nninteractive(post_hu, initial_mask, spacing_zyx, *, model_dir, valid_mask=None):
    """Recalcula una prior POST con la API oficial de refinamiento 3D.

No hace remuestreo externo ni aplica espaciado a índices: volumen, máscara y
buffer conservan [z,y,x]. El modelo v1 opera en vóxeles; el spacing se registra
y se usa solo para la medición física de la salida: cm³=N·dz·dy·dx/1000.
Los ceros heredados fuera de la envolvente escrita no prueban ausencia.
"""
    spacing = _validate(post_hu, initial_mask, spacing_zyx, valid_mask)
    started = time.perf_counter()
    diagnostics = {'method': 'nninteractive_initial_post_refinement', 'status': 'evaluating',
        'source_mask_usage': 'full_native_POST_prediction_as_initial_label_not_reference',
        'source_intensities': 'POST_native_HU', 'shape_zyx': list(post_hu.shape),
        'spacing_zyx_mm': spacing.tolist(), 'initial_voxels': int(initial_mask.sum()),
        'initial_array_sha256': hashlib.sha256(initial_mask.tobytes(order='C')).hexdigest(),
        'settings': {'device': 'cpu', 'torch_threads': 2, 'compile': False, 'fold': 0,
                     'autozoom': True, 'interactions_storage': 'blosc2', 'undo': False,
                     'initial_label_prediction': True, 'force_full_refine': True,
                     'external_resampling': False, 'new_points_or_boxes': False,
                     'random_seed_override': None, 'adapter_HU_window_or_threshold': None,
                     'invalid_input_HU': 'retained_native_values; validity_is_output_check',
                     'model_axis_order': 'z,y,x; no physical-spacing normalization by adapter'},
        'warnings': ['Dependent refinement of an unreviewed model prediction; not an independent reference.',
                     'Training overlap with HCC-TACE-Seg has not been established.']}
    session = None
    try:
        load_started = time.perf_counter()
        session, provenance = _load_session(model_dir)
        diagnostics['model_load_seconds'] = time.perf_counter()-load_started
        diagnostics['model'] = provenance
        if not (session.supports_initial_label and session.supports_zero_shot_label_refinement):
            raise ValueError('The pinned session does not support initial-label zero-shot refinement.')
        # Copias separadas, incluso si el usuario entregó arrays read-only o vistas.
        image = np.array(post_hu, dtype=np.float32, copy=True, order='C')
        initial = initial_mask.astype(np.uint8, copy=True)
        target = np.zeros(initial_mask.shape, dtype=np.uint8)
        prediction_started = time.perf_counter()
        session.set_image(image[None])
        # set_image es asíncrono. Esperar el mismo método usado por la API
        # oficial permite detectar divisiones inválidas antes de predecir.
        session._finish_preprocessing_and_initialize_interactions()
        _check_preprocessed_image(session.preprocessed_image, initial_mask.shape)
        session.set_target_buffer(target)
        bounds = session.add_initial_seg_interaction(initial, run_prediction=True)
        diagnostics['preprocess_and_inference_seconds'] = time.perf_counter()-prediction_started
        low, high = _write_envelope(bounds, initial_mask.shape)
        if target.shape != initial_mask.shape or not np.isin(target, [0, 1]).all():
            raise ValueError('The model output is not binary on the native POST grid.')
        result = target.astype(bool, copy=True)
        if valid_mask is not None and np.any(result & ~valid_mask):
            raise ValueError('The refined prediction includes invalid pixel padding; no clipping was applied.')
        if not _outside_unchanged(result, initial_mask, low, high):
            raise ValueError('The output changed outside the official write envelope.')
        added = int(np.count_nonzero(result & ~initial_mask))
        removed = int(np.count_nonzero(initial_mask & ~result))
        changed = np.flatnonzero(np.any(result != initial_mask, axis=(1, 2))).tolist()
        count = int(result.sum())
        envelope = tuple(slice(int(a), int(b)) for a, b in zip(low, high))
        diagnostics.update(status='completed_unreviewed', voxels=count, added_voxels=added,
            removed_voxels=removed, changed_slices=changed,
            outcome='empty_prediction_not_confirmed_absence' if not count else 'changed' if changed else 'unchanged',
            volume_cm3=float(count*np.prod(spacing)/1000),
            display_slice_index=int(np.argmax(result.sum(axis=(1, 2)))) if count else None,
            coverage={'reported_write_envelope_zyx': [low.tolist(), high.tolist()],
                      'interpretation': 'envelope_of_writes_not_exact_inference_coverage',
                      'outside_envelope': 'inherited_baseline_not_reevaluated',
                      'outside_envelope_unchanged': True,
                      'baseline_voxels_outside_envelope': int(initial_mask.sum()-initial_mask[envelope].sum()),
                      'all_native_slices_retained': True},
            acquisition_touch_faces=[name for name, plane in (
                ('z_min', result[0]), ('z_max', result[-1]), ('y_min', result[:, 0]),
                ('y_max', result[:, -1]), ('x_min', result[:, :, 0]), ('x_max', result[:, :, -1])) if plane.any()],
            seconds=time.perf_counter()-started)
        verify_assets(model_dir)
        return result, diagnostics
    except Exception as error:
        diagnostics.update(status='failed', error_type=type(error).__name__, seconds=time.perf_counter()-started)
        raise NeuralRefinementError(str(error), diagnostics) from error
    finally:
        if session is not None and getattr(session, 'executor', None) is not None:
            session.executor.shutdown(wait=True)
