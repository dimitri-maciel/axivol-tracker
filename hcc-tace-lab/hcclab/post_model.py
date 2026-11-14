"""Candidata POST desde Efficient MedSAM2 tiny, sin usar PRE como salida.

La caja localizada inicia el modelo en un corte de un contexto XY físico.
La propagación recorre todos los cortes POST; ninguna extensión PRE en z
recorta la máscara resultante. El exterior del campo XY no se evalúa.
Torch y el predictor se cargan únicamente al generar una candidata, nunca al
navegar imágenes. Código y checkpoint están fijados en model_assets.py.
"""
from importlib.metadata import version
import hashlib
from pathlib import Path
import threading
import time

import numpy as np

from .geometry import GeometryError
from .inference_field import context_bounds
from .model_assets import verify_model


CONFIG = 'configs/efficienttam_ti_512x512.yaml'
CONFIG_SHA256 = '55a0142a2d0076d3c434a8a35c23ccc16e7e42ef06153b571f147615befa070d'
WINDOW_LEVEL_HU = 40.0
WINDOW_WIDTH_HU = 400.0
MODEL_SIZE = 512
MIN_CONTEXT_MM = 160
MAX_VOXELS = 64_000_000
MAX_SLICES = 256
MAX_SECONDS = 300.0
MAX_OUTSIDE_ROI_FRACTION = .5  # Engineering abstention gate, not anatomical validation.
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
    try:
        roi = np.asarray(location['roi_zyx'], dtype=float)
    except (KeyError, ValueError, TypeError):
        _fail('The registered POST search region is incomplete.', 'invalid_search_region', diagnostics)
    if (roi.shape != (2, 3) or not np.isfinite(roi).all()
            or not np.equal(roi, np.floor(roi)).all()
            or np.any(roi[0] < 0) or np.any(roi[1] > hu.shape)
            or np.any(roi[0] > lo) or np.any(roi[1] < hi)):
        _fail('The registered search region must contain the prompt within POST.', 'invalid_search_region', diagnostics)
    return z, box, roi.astype(int)


def _check_localization_consistency(mask, roi, diagnostics):
    """Abstain on gross propagation drift; never clip a prediction to PRE.

    The expanded registered search box is only a consistency reference. Passing
    does not verify anatomy; failing can also reflect growth or registration error.
    This engineering gate was introduced after the HCC_006 development failure.
    """
    count = int(np.count_nonzero(mask))  # Caller has already rejected empty masks.
    inside = int(np.count_nonzero(mask[tuple(slice(int(a), int(b)) for a, b in zip(*roi))]))
    outside_fraction = (count-inside)/count
    passed = outside_fraction <= MAX_OUTSIDE_ROI_FRACTION
    diagnostics['localization_consistency'] = {
        'search_region_zyx': roi.tolist(), 'inside_voxels': inside,
        'outside_voxels': count-inside, 'outside_fraction': outside_fraction,
        'max_outside_fraction': MAX_OUTSIDE_ROI_FRACTION,
        'status': 'passed' if passed else 'failed', 'mask_clipped_to_region': False,
    }
    if not passed:
        _fail('Prediction extends mostly outside the localized target region. '
              'Review alignment and target selection before generating another mask.',
              'localization_propagation_mismatch', diagnostics)


class _NativeFrames:
    """Normaliza un corte a demanda; evita materializar N×3×512×512 floats."""
    def __init__(self, hu, valid, torch_module, *, input_mode='float_bilinear'):
        if type(input_mode) is not str or input_mode not in ('float_bilinear', 'upstream_uint8'):
            raise ValueError('Input mode must be float_bilinear or upstream_uint8.')
        self.hu, self.valid, self.torch = hu, valid, torch_module
        self.input_mode = input_mode

    def __len__(self):
        return len(self.hu)

    def __getitem__(self, index):
        torch = self.torch
        low = WINDOW_LEVEL_HU-WINDOW_WIDTH_HU/2
        plane = np.clip((self.hu[index].astype(np.float32)-low)/WINDOW_WIDTH_HU, 0, 1)
        if self.valid is not None:
            plane[~self.valid[index]] = 0
        if self.input_mode == 'upstream_uint8':
            from PIL import Image
            # El script RECIST oficial recibe un NPZ ya en [0,255]. Aquí
            # mantenemos nuestra ventana HU fija; su cuantización es explícita.
            pixels = (plane * 255).astype(np.uint8)  # Truncar, no redondear.
            rgb = Image.fromarray(pixels).convert('RGB')
            if tuple(plane.shape) != (MODEL_SIZE, MODEL_SIZE):
                rgb = rgb.resize((MODEL_SIZE, MODEL_SIZE), resample=Image.Resampling.BICUBIC)
            # Su loader normaliza float64 con constantes float32 y el extractor
            # convierte después a float32. Preservar ese orden evita otra ablación.
            image = torch.from_numpy(np.asarray(rgb, dtype=np.float64).transpose(2, 0, 1).copy() / 255)
        else:
            image = torch.from_numpy(plane)[None, None]
            if tuple(plane.shape) != (MODEL_SIZE, MODEL_SIZE):
                image = torch.nn.functional.interpolate(image, size=(MODEL_SIZE, MODEL_SIZE),
                                                         mode='bilinear', align_corners=False)
            image = image[0].repeat(3, 1, 1)
        mean = torch.tensor([.485, .456, .406])[:, None, None]
        std = torch.tensor([.229, .224, .225])[:, None, None]
        normalized = (image-mean)/std
        return normalized.float() if self.input_mode == 'upstream_uint8' else normalized


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


def _scores_from_logits(logits, native_shape, diagnostics):
    values = logits[0].squeeze(0).detach().cpu().numpy()
    if values.shape != native_shape or not np.isfinite(values).all():
        _fail('The model returned incompatible POST mask geometry.', 'invalid_model_output', diagnostics)
    return values.astype(np.float32, copy=True)


def _field_contacts(mask, low, high):
    return [name for name, internal, plane in (
        ('y_min', low[0] > 0, mask[:, low[0], low[1]:high[1]]),
        ('y_max', high[0] < mask.shape[1], mask[:, high[0]-1, low[1]:high[1]]),
        ('x_min', low[1] > 0, mask[:, low[0]:high[0], low[1]]),
        ('x_max', high[1] < mask.shape[2], mask[:, low[0]:high[0], high[1]-1]))
        if internal and plane.any()]


def segment_post_model(post_hu, spacing_zyx, location, *, checkpoint_path, valid_mask=None,
                       refine_boundary=True, intermediates=None, prompt_mode='binary_reinjection',
                       input_mode='float_bilinear'):
    """Devuelve máscara bool nativa y diagnóstico; nunca recibe píxeles/SEG PRE.

Los controles aquí verifican ejecución, geometría, cobertura y coherencia espacial, no exactitud
anatómica. La candidata sigue unreviewed aunque todos estos controles pasen.

``intermediates`` permite al CLI experimental conservar logits y máscara cruda
del campo realmente inferido. No contiene puntuaciones ficticias fuera de él.
``refine_boundary=False`` es el comparador explícito, no una salida de respaldo.
``prompt_mode='binary_reinjection'`` conserva el protocolo previo. La variante
experimental ``'box'`` propaga directamente desde la caja en cada dirección,
sin convertir la primera predicción a una máscara que se reinserta. No cambia
las opciones internas de memoria del modelo ni implica memoria probabilística.
``input_mode='upstream_uint8'`` ensaya cuantización uint8 e interpolación PIL
bicúbica del loader RECIST. Conserva nuestra ventana HU fija y no implica que
esa ventana estuviera definida en el NPZ oficial. El modo previo sigue por defecto.
"""
    if type(refine_boundary) is not bool or (intermediates is not None and not isinstance(intermediates, dict)):
        raise ValueError('Provide a boolean refinement flag and an optional artifacts dictionary.')
    if type(prompt_mode) is not str or prompt_mode not in ('binary_reinjection', 'box'):
        raise ValueError('Prompt mode must be binary_reinjection or box.')
    if type(input_mode) is not str or input_mode not in ('float_bilinear', 'upstream_uint8'):
        raise ValueError('Input mode must be float_bilinear or upstream_uint8.')
    started = time.perf_counter()
    diagnostics = {'method': 'efficient_medsam2_tiny', 'status': 'evaluating',
                   'prompt_conditioning': prompt_mode,
                   'source_intensities': 'POST_native_HU', 'source_mask_usage': 'localization_only',
                   'settings': {'configuration': CONFIG, 'configuration_sha256': CONFIG_SHA256,
                                'device': 'cpu', 'threads': 2,
                                'window_level_hu': WINDOW_LEVEL_HU, 'window_width_hu': WINDOW_WIDTH_HU,
                                'model_input_shape_yx': [MODEL_SIZE, MODEL_SIZE],
                                'normalization_mean': [.485, .456, .406],
                                'normalization_std': [.229, .224, .225],
                                'input_mode': input_mode,
                                'input_resize': ('pil_rgb_bicubic_if_needed' if input_mode == 'upstream_uint8'
                                                 else 'bilinear_align_corners_false_if_needed'),
                                'input_quantization': ('clip_window_times_255_truncate_uint8'
                                                       if input_mode == 'upstream_uint8' else 'none'),
                                'input_normalization_precision': ('float64_then_float32'
                                                                  if input_mode == 'upstream_uint8' else 'float32'),
                                'output_grid': 'native_POST', 'initial_threshold_logit': 0,
                                'output_rule': 'post_native_logit_band_graph_cut' if refine_boundary else 'logit_gt_zero',
                                'propagation': 'all_POST_slices_bidirectional',
                                'prompt_mode': prompt_mode,
                                'initial_prompt': 'registered_box',
                                'propagation_conditioning': ('binary_mask_from_box_prediction'
                                    if prompt_mode == 'binary_reinjection' else 'registered_box'),
                                'model_memory_settings_overridden': False,
                                'context_minimum_mm': MIN_CONTEXT_MM,
                                'context_rule': 'max(160 mm, 2 * largest localized XY box side in mm)',
                                'cuda_hole_filling': False, 'max_seconds': MAX_SECONDS},
                   'warnings': ['Training overlap with HCC-TACE-Seg has not been established.']}
    z, box, roi = _validate_inputs(post_hu, spacing_zyx, location, valid_mask, diagnostics)
    try:
        low, high = context_bounds(post_hu.shape, location, spacing_zyx, MIN_CONTEXT_MM)
    except (KeyError, TypeError, ValueError):
        _fail('The localized input field is incompatible with POST.', 'invalid_input_field', diagnostics)
    native_box = box.copy()
    box = box - np.asarray([low[1], low[0], low[1], low[0]])
    field = (slice(None), slice(low[0], high[0]), slice(low[1], high[1]))
    field_shape = tuple(int(value) for value in high - low)
    diagnostics.update(prompt_slice_index=z, prompt_box_xyxy=native_box.tolist(),
                       model_prompt_box_xyxy=box.tolist(),
                       prompt_box_convention='inclusive_pixel_centers',
                       localization_box_zyx=location['localized_box_zyx'],
                       inference_field_yx=[low.tolist(), high.tolist()],
                       inference_field_shape_yx=list(field_shape),
                       outside_inference_field='not_assessed',
                       model_input_resized=field_shape != (MODEL_SIZE, MODEL_SIZE))
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
        frames = _NativeFrames(post_hu[field], None if valid_mask is None else valid_mask[field], torch,
                               input_mode=input_mode)
        mask = np.zeros(post_hu.shape, dtype=bool)
        scores = np.empty((len(post_hu), *field_shape), dtype=np.float32)
        seen = np.zeros(len(post_hu), dtype=bool)
        with torch.inference_mode():
            state = predictor.init_state(frames, *field_shape)
            _, _, logits = predictor.add_new_points_or_box(state, frame_idx=z, obj_id=1, box=box)
            prompt_scores = _scores_from_logits(logits, field_shape, diagnostics)
            prompt = prompt_scores > 0
            if not prompt.any():
                _fail('The model could not identify a region at the registered location.', 'empty_prompt', diagnostics)
            for reverse in (False, True):
                if reverse:
                    predictor.reset_state(state)
                    state = predictor.init_state(frames, *field_shape)
                    if prompt_mode == 'box':
                        _, _, reverse_logits = predictor.add_new_points_or_box(
                            state, frame_idx=z, obj_id=1, box=box)
                        reverse_scores = _scores_from_logits(reverse_logits, field_shape, diagnostics)
                        if not (reverse_scores > 0).any():
                            _fail('The reverse pass could not identify a region at the registered box.',
                                  'empty_reverse_prompt', diagnostics)
                if prompt_mode == 'binary_reinjection':
                    predictor.add_new_mask(state, frame_idx=z, obj_id=1, mask=prompt)
                for index, _, logits in predictor.propagate_in_video(state, start_frame_idx=z, reverse=reverse):
                    if time.perf_counter()-started > MAX_SECONDS:
                        _fail('POST prediction exceeded its execution time limit.', 'time_limit', diagnostics)
                    if not isinstance(index, int) or not 0 <= index < len(post_hu):
                        _fail('The model returned an invalid POST slice.', 'invalid_model_output', diagnostics)
                    values = _scores_from_logits(logits, field_shape, diagnostics)
                    # add_new_mask can manufacture strong anchor scores from a
                    # binary prompt. Keep the original box-conditioned evidence
                    # at that slice, in both propagation directions.
                    scores[index] = prompt_scores if index == z else values
                    seen[index] = True
        if not seen.all():
            _fail('The model did not process every POST slice.', 'incomplete_propagation', diagnostics)
        mask[field] = scores > 0
        excluded = int(np.count_nonzero(mask & ~valid_mask)) if valid_mask is not None else 0
        if valid_mask is not None:
            mask &= valid_mask
        count = int(np.count_nonzero(mask))
        if not count:
            _fail('The model produced no valid POST region.', 'empty_prediction', diagnostics)
        # A crop boundary is an artificial field limit. Never silently present
        # a prediction truncated there as a complete treated-lesion candidate.
        contacts = _field_contacts(mask, low, high)
        diagnostics['internal_field_touch_faces'] = contacts
        if contacts:
            _fail('Prediction reaches the input field boundary. A larger field must be evaluated.',
                  'incomplete_input_field', diagnostics)
        _check_localization_consistency(mask, roi, diagnostics)
        diagnostics['raw_prediction'] = {
            'voxels': count, 'mask_array_sha256': hashlib.sha256(mask.tobytes(order='C')).hexdigest(),
            'logits_field_sha256': hashlib.sha256(scores.astype('<f4').tobytes(order='C')).hexdigest(),
            'logits_dtype': 'float32_little_endian', 'logits_axis_order': 'z,y,x',
            'anchor_scores': 'original_box_conditioned_logits',
            'scope': 'Uncalibrated model scores, not anatomical confidence.'}
        if intermediates is not None:
            intermediates.update(raw_mask=mask[field].copy(), logits=scores.copy())
        if refine_boundary:
            from .post_boundary import refine_post_boundary
            try:
                refined, boundary_info = refine_post_boundary(
                    post_hu[field], mask[field], scores, spacing_zyx,
                    valid_mask=None if valid_mask is None else valid_mask[field])
            except ValueError as error:
                diagnostics['boundary_refinement'] = {'status': 'failed', 'reason': str(error)}
                _fail('POST boundary refinement could not finish. Review its diagnostics.',
                      'boundary_refinement_failed', diagnostics)
            mask[field] = refined
            diagnostics['boundary_refinement'] = boundary_info
            internal_faces = {name for name, internal in (
                ('y_min', low[0] > 0), ('y_max', high[0] < post_hu.shape[1]),
                ('x_min', low[1] > 0), ('x_max', high[1] < post_hu.shape[2])) if internal}
            if internal_faces.intersection(boundary_info.get('editable_band_touch_faces', [])):
                _fail('The boundary band reaches an artificial input-field edge. Evaluate a larger field.',
                      'incomplete_boundary_context', diagnostics)
            contacts = _field_contacts(mask, low, high)
            diagnostics['internal_field_touch_faces'] = contacts
            if contacts:
                _fail('Refined prediction reaches the input field boundary.', 'incomplete_input_field', diagnostics)
            count = int(np.count_nonzero(mask))
            if not count:
                _fail('POST boundary refinement produced an empty mask.', 'empty_refinement', diagnostics)
            _check_localization_consistency(mask, roi, diagnostics)
        else:
            diagnostics['boundary_refinement'] = {'status': 'disabled_for_comparison'}
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
