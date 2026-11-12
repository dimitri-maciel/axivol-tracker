"""Candidata POST desde Efficient MedSAM2 tiny, sin usar PRE como salida.

La caja localizada solo inicia el modelo en un corte. La propagación recorre
toda la adquisición POST; ninguna extensión PRE recorta la máscara resultante.
Torch y el predictor se cargan únicamente al generar una candidata, nunca al
navegar imágenes. Código y checkpoint están fijados en model_assets.py.
"""
from importlib.metadata import version
from pathlib import Path
import threading
import time

import numpy as np

from .geometry import GeometryError
from .model_assets import verify_model


CONFIG = 'configs/efficienttam_ti_512x512.yaml'
CONFIG_SHA256 = '55a0142a2d0076d3c434a8a35c23ccc16e7e42ef06153b571f147615befa070d'
WINDOW_LEVEL_HU = 40.0
WINDOW_WIDTH_HU = 400.0
MODEL_SIZE = 512
MAX_VOXELS = 64_000_000
MAX_SLICES = 256
MAX_SECONDS = 300.0
_LOCK = threading.Lock()
_PREDICTOR = None
_PREDICTOR_PATH = None
_TORCH = None


class ModelSegmentationError(GeometryError):
    """Fallo explícito de generación, con diagnóstico y sin máscara sustituta."""
    def __init__(self, message, diagnostics):
        super().__init__(message)
        self.diagnostics = diagnostics


def _fail(message, reason, diagnostics):
    diagnostics.update(status='abstained', reason=reason)
    raise ModelSegmentationError(message, diagnostics)


def _validate_inputs(hu, spacing_zyx, location, valid_mask, diagnostics):
    if (not isinstance(hu, np.ndarray) or hu.ndim != 3 or hu.dtype.kind not in 'iuf'
            or min(hu.shape) < 1 or hu.size > MAX_VOXELS or len(hu) > MAX_SLICES
            or any(not np.isfinite(plane).all() for plane in hu)):
        _fail('POST model input exceeds supported limits or has invalid pixels.', 'invalid_input', diagnostics)
    spacing = np.asarray(spacing_zyx, dtype=float)
    if spacing.shape != (3,) or not np.isfinite(spacing).all() or (spacing <= 0).any():
        _fail('POST spacing must contain three positive finite values.', 'invalid_spacing', diagnostics)
    if valid_mask is not None and (not isinstance(valid_mask, np.ndarray)
            or valid_mask.dtype != np.bool_ or valid_mask.shape != hu.shape):
        _fail('POST pixel validity does not match its native image grid.', 'invalid_validity', diagnostics)
    try:
        center = np.asarray(location['post_center_zyx'], dtype=float)
        bounds = np.asarray(location['localized_box_zyx'], dtype=float)
    except (KeyError, ValueError, TypeError):
        _fail('The registered POST location is incomplete.', 'invalid_prompt', diagnostics)
    if (center.shape != (3,) or bounds.shape != (2, 3) or not np.isfinite(center).all()
            or not np.isfinite(bounds).all() or not np.equal(bounds, np.floor(bounds)).all()
            or np.any(center < 0) or np.any(center > np.asarray(hu.shape)-1)
            or np.any(bounds[0] < 0) or np.any(bounds[1] > hu.shape)
            or np.any(bounds[1]-bounds[0] < 2)):
        _fail('The registered prompt is outside the POST acquisition.', 'invalid_prompt', diagnostics)
    z = int(np.rint(center[0]))
    lo, hi = bounds.astype(int)
    if not lo[0] <= z < hi[0]:
        _fail('The registered center is outside its localization box.', 'invalid_prompt', diagnostics)
    # The source bounding box is half-open. SAM receives its last pixel center.
    box = np.asarray([lo[2], lo[1], hi[2]-1, hi[1]-1], dtype=np.float32)
    if valid_mask is not None and not valid_mask[z, lo[1]:hi[1], lo[2]:hi[2]].any():
        _fail('The initial POST prompt contains no valid image pixels.', 'invalid_prompt', diagnostics)
    return z, box


class _NativeFrames:
    """Normaliza un corte a demanda; evita materializar N×3×512×512 floats."""
    def __init__(self, hu, valid, torch_module):
        self.hu, self.valid, self.torch = hu, valid, torch_module

    def __len__(self):
        return len(self.hu)

    def __getitem__(self, index):
        torch = self.torch
        low = WINDOW_LEVEL_HU-WINDOW_WIDTH_HU/2
        plane = np.clip((self.hu[index].astype(np.float32)-low)/WINDOW_WIDTH_HU, 0, 1)
        if self.valid is not None:
            plane[~self.valid[index]] = 0
        image = torch.from_numpy(plane)[None, None]
        if tuple(plane.shape) != (MODEL_SIZE, MODEL_SIZE):
            image = torch.nn.functional.interpolate(image, size=(MODEL_SIZE, MODEL_SIZE),
                                                     mode='bilinear', align_corners=False)
        image = image[0].repeat(3, 1, 1)
        mean = torch.tensor([.485, .456, .406])[:, None, None]
        std = torch.tensor([.229, .224, .225])[:, None, None]
        return (image-mean)/std


def _get_predictor(checkpoint_path):
    """Una sola instancia CPU; invocar solamente mientras _LOCK está adquirido."""
    global _PREDICTOR, _PREDICTOR_PATH, _TORCH
    metadata = verify_model(checkpoint_path)  # Verifica bytes incluso si hay caché.
    path = str(Path(checkpoint_path).resolve())
    if _PREDICTOR is None or _PREDICTOR_PATH != path:
        import torch
        from efficient_track_anything.build_efficienttam import build_efficienttam_video_predictor_npz
        torch.set_num_threads(2)
        torch.manual_seed(42)
        predictor = build_efficienttam_video_predictor_npz(CONFIG, path, device='cpu', mode='eval')
        verify_model(checkpoint_path)
        # Este posprocesamiento upstream requiere una extensión CUDA; no se usa
        # en CPU ni se sustituye silenciosamente por una operación morfológica.
        predictor.fill_hole_area = 0
        _PREDICTOR, _PREDICTOR_PATH, _TORCH = predictor, path, torch
    return _PREDICTOR, _TORCH, metadata


def _mask_from_logits(logits, native_shape, diagnostics):
    values = logits[0].squeeze(0).detach().cpu().numpy()
    if values.shape != native_shape or not np.isfinite(values).all():
        _fail('The model returned incompatible POST mask geometry.', 'invalid_model_output', diagnostics)
    return values > 0  # Umbral upstream: sigmoid(logit)>0.5.


def segment_post_model(post_hu, spacing_zyx, location, *, checkpoint_path, valid_mask=None):
    """Devuelve máscara bool nativa y diagnóstico; nunca recibe píxeles/SEG PRE.

Los controles aquí verifican ejecución, geometría y cobertura, no exactitud
anatómica. La candidata sigue unreviewed aunque todos estos controles pasen.
"""
    started = time.perf_counter()
    diagnostics = {'method': 'efficient_medsam2_tiny', 'status': 'evaluating',
                   'source_intensities': 'POST_native_HU', 'source_mask_usage': 'localization_only',
                   'settings': {'configuration': CONFIG, 'configuration_sha256': CONFIG_SHA256,
                                'device': 'cpu', 'threads': 2,
                                'window_level_hu': WINDOW_LEVEL_HU, 'window_width_hu': WINDOW_WIDTH_HU,
                                'model_input_shape_yx': [MODEL_SIZE, MODEL_SIZE],
                                'normalization_mean': [.485, .456, .406],
                                'normalization_std': [.229, .224, .225],
                                'input_resize': 'bilinear_align_corners_false_if_needed',
                                'output_grid': 'native_POST', 'output_threshold_logit': 0,
                                'propagation': 'all_POST_slices_bidirectional',
                                'cuda_hole_filling': False, 'max_seconds': MAX_SECONDS},
                   'warnings': ['Training overlap with HCC-TACE-Seg has not been established.']}
    z, box = _validate_inputs(post_hu, spacing_zyx, location, valid_mask, diagnostics)
    diagnostics.update(prompt_slice_index=z, prompt_box_xyxy=box.tolist(),
                       prompt_box_convention='inclusive_pixel_centers',
                       localization_box_zyx=location['localized_box_zyx'])
    if not _LOCK.acquire(blocking=False):
        _fail('A model prediction is already running. Wait for it to finish.', 'model_busy', diagnostics)
    state = None
    predictor = None
    try:
        try:
            predictor, torch, provenance = _get_predictor(checkpoint_path)
        except Exception as error:
            # Hydra and optional-runtime failures have several unrelated base
            # classes. Preserve the type for audit without exposing local paths.
            diagnostics['error_type'] = type(error).__name__
            _fail('The POST model could not load. Check model installation and its checkpoint.',
                  'model_unavailable', diagnostics)
        diagnostics['model'] = provenance
        diagnostics['software'] = {'torch': torch.__version__, 'torchvision': version('torchvision'),
                                   'hydra-core': version('hydra-core'), 'numpy': np.__version__}
        frames = _NativeFrames(post_hu, valid_mask, torch)
        native_shape = post_hu.shape[1:]
        mask = np.zeros(post_hu.shape, dtype=bool)
        seen = np.zeros(len(post_hu), dtype=bool)
        with torch.inference_mode():
            state = predictor.init_state(frames, *native_shape)
            _, _, logits = predictor.add_new_points_or_box(state, frame_idx=z, obj_id=1, box=box)
            prompt = _mask_from_logits(logits, native_shape, diagnostics)
            if not prompt.any():
                _fail('The model could not identify a region at the registered location.', 'empty_prompt', diagnostics)
            for reverse in (False, True):
                if reverse:
                    predictor.reset_state(state)
                    state = predictor.init_state(frames, *native_shape)
                predictor.add_new_mask(state, frame_idx=z, obj_id=1, mask=prompt)
                for index, _, logits in predictor.propagate_in_video(state, start_frame_idx=z, reverse=reverse):
                    if time.perf_counter()-started > MAX_SECONDS:
                        _fail('POST prediction exceeded its execution time limit.', 'time_limit', diagnostics)
                    if not isinstance(index, int) or not 0 <= index < len(post_hu):
                        _fail('The model returned an invalid POST slice.', 'invalid_model_output', diagnostics)
                    mask[index] = _mask_from_logits(logits, native_shape, diagnostics)
                    seen[index] = True
        if not seen.all():
            _fail('The model did not process every POST slice.', 'incomplete_propagation', diagnostics)
        excluded = int(np.count_nonzero(mask & ~valid_mask)) if valid_mask is not None else 0
        if valid_mask is not None:
            mask &= valid_mask
        count = int(np.count_nonzero(mask))
        if not count:
            _fail('The model produced no valid POST region.', 'empty_prediction', diagnostics)
        edge_faces = [name for name, plane in (
            ('z_min', mask[0]), ('z_max', mask[-1]), ('y_min', mask[:, 0]),
            ('y_max', mask[:, -1]), ('x_min', mask[:, :, 0]), ('x_max', mask[:, :, -1])) if plane.any()]
        if edge_faces:
            diagnostics['warnings'].append('Prediction reaches the acquisition boundary; coverage may be incomplete.')
        if excluded:
            diagnostics['warnings'].append('Predicted pixels outside valid CT support were excluded.')
        diagnostics.update(status='candidate_unreviewed', voxels=count,
                           slices_processed=int(seen.sum()), input_slices=len(post_hu),
                           excluded_padding_voxels=excluded, acquisition_touch_faces=edge_faces,
                           display_slice_index=int(np.argmax(mask.sum(axis=(1, 2)))),
                           seconds=time.perf_counter()-started)
        return mask, diagnostics
    except ModelSegmentationError:
        raise
    except Exception as error:
        diagnostics['error_type'] = type(error).__name__
        _fail('POST prediction could not finish. Review the model setup and source acquisition.',
              'inference_failed', diagnostics)
    finally:
        try:
            if predictor is not None and state is not None:
                predictor.reset_state(state)
        finally:
            _LOCK.release()
