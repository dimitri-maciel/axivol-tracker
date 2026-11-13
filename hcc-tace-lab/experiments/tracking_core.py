"""Geometría, inferencia y descriptores para ablaciones de seguimiento POST.

Las máscaras son predicciones experimentales, no referencias anatómicas. El
contexto XY cambia el campo que ve la red; z siempre conserva todos los cortes.
No se ajusta una máscara ya calculada a la caja PRE ni a un volumen deseado.
"""
import hashlib
import importlib.util
from pathlib import Path
import time

import numpy as np
from scipy import ndimage

from hcclab.post_model import CONFIG, CONFIG_SHA256, _get_predictor, _validate_inputs
from hcclab.inference_field import context_bounds


VARIANTS = {
    'full': {'context_min_mm': 0, 'level_hu': 40, 'width_hu': 400, 'registration': 'global'},
    'local160': {'context_min_mm': 160, 'level_hu': 40, 'width_hu': 400, 'registration': 'global'},
    'local240': {'context_min_mm': 240, 'level_hu': 40, 'width_hu': 400, 'registration': 'global'},
    'local160wide': {'context_min_mm': 160, 'level_hu': 200, 'width_hu': 800, 'registration': 'global'},
    'local160liver': {'context_min_mm': 160, 'level_hu': 40, 'width_hu': 400, 'registration': 'liver'},
}



def window_plane(plane, valid, level, width):
    """u=clip((HU−(nivel−ancho/2))/ancho,0,1); padding=0, sin mutar HU."""
    if (not isinstance(plane, np.ndarray) or plane.ndim != 2 or plane.dtype.kind not in 'iuf'
            or not np.isfinite(plane).all() or not isinstance(valid, np.ndarray)
            or valid.dtype != np.bool_ or valid.shape != plane.shape):
        raise ValueError('A finite 2D HU plane and matching boolean validity are required.')
    if not np.isfinite([level, width]).all() or width <= 0:
        raise ValueError('Window level and width must be finite; width must be positive.')
    values = np.clip((plane.astype(np.float32) - (level - width / 2)) / width, 0, 1)
    values[~valid] = 0
    return values


class Frames:
    """Normaliza un corte a demanda; no materializa N×3×512×512."""
    def __init__(self, hu, valid, torch, level, width):
        self.hu, self.valid, self.torch = hu, valid, torch
        self.level, self.width = level, width

    def __len__(self):
        return len(self.hu)

    def __getitem__(self, index):
        t = self.torch
        plane = window_plane(self.hu[index], self.valid[index], self.level, self.width)
        image = t.from_numpy(plane)[None, None]
        if plane.shape != (512, 512):
            image = t.nn.functional.interpolate(image, size=(512, 512), mode='bilinear', align_corners=False)
        image = image[0].repeat(3, 1, 1)
        return (image - t.tensor([.485, .456, .406])[:, None, None]) / t.tensor([.229, .224, .225])[:, None, None]


def native_plane(cropped_mask, low, high, valid_plane):
    """Inserta la predicción del campo de inferencia en la retícula POST original.

    Fuera de ese campo no se ejecutó la red: los ceros no equivalen a una
    afirmación de fondo anatómico. El informe conserva campo y contacto de borde.
    """
    raw_low, raw_high = np.asarray(low, dtype=object), np.asarray(high, dtype=object)
    if (raw_low.shape != (2,) or raw_high.shape != (2,) or any(isinstance(v, (bool, np.bool_))
            or not isinstance(v, (int, np.integer)) for raw in (raw_low, raw_high) for v in raw.flat)):
        raise ValueError('Inference offsets must contain integer indices, not booleans.')
    low, high = raw_low.astype(np.int64), raw_high.astype(np.int64)
    if (not isinstance(valid_plane, np.ndarray) or valid_plane.ndim != 2 or valid_plane.dtype != np.bool_
            or low.shape != (2,) or high.shape != (2,) or low.dtype.kind not in 'iu'
            or high.dtype.kind not in 'iu' or np.any(low < 0) or np.any(high > valid_plane.shape)
            or np.any(high <= low) or not isinstance(cropped_mask, np.ndarray)
            or cropped_mask.dtype != np.bool_ or tuple(high - low) != cropped_mask.shape):
        raise ValueError('Prediction and inference field must match native POST geometry.')
    result = np.zeros(valid_plane.shape, dtype=bool)
    slices = tuple(slice(a, b) for a, b in zip(low, high))
    result[slices] = cropped_mask & valid_plane[slices]
    return result


def load_predictor(checkpoint):
    """Verifica YAML y pesos fijados; usa el mismo adaptador CPU que la app."""
    spec = importlib.util.find_spec('efficient_track_anything')
    if spec is None or not spec.submodule_search_locations:
        raise ValueError('The pinned MedSAM2 runtime is not installed.')
    config_path = Path(spec.submodule_search_locations[0]) / CONFIG
    if hashlib.sha256(config_path.read_bytes()).hexdigest() != CONFIG_SHA256:
        raise ValueError('Model configuration differs from the pinned SHA-256.')
    return _get_predictor(checkpoint)  # SHA-256 de pesos antes/después de cargar.


def infer_raw(post_hu, valid, spacing, location, settings, predictor, torch, *, max_seconds=300):
    """Ejecuta una ablación sin el rechazo final de coherencia de la aplicación.

    Se conservan chequeos de entradas, logits finitos, cobertura de todos los
    cortes y padding. La omisión deliberada del guard permite estudiar fallos;
    la salida jamás entra a CandidateStore. El límite temporal es cooperativo.
    """
    z, native_box, _ = _validate_inputs(post_hu, spacing, location, valid, {})
    low, high = context_bounds(post_hu.shape, location, spacing, settings['context_min_mm'])
    crop = (slice(None), slice(low[0], high[0]), slice(low[1], high[1]))
    frames = Frames(post_hu[crop], valid[crop], torch, settings['level_hu'], settings['width_hu'])
    box = native_box - np.array([low[1], low[0], low[1], low[0]])
    shape = frames.hu.shape[1:]
    result = np.zeros(post_hu.shape, dtype=bool)
    seen = np.zeros(len(result), dtype=bool)
    records, state = [], None
    started = time.perf_counter()

    def plane_from_logits(logits):
        raw = logits[0].squeeze(0).detach().cpu().numpy()
        if raw.shape != shape or not np.isfinite(raw).all():
            raise ValueError('Model logits must be finite and match the native inference field.')
        return raw > 0

    try:
        with torch.inference_mode():
            state = predictor.init_state(frames, *shape)
            _, _, logits = predictor.add_new_points_or_box(state, frame_idx=z, obj_id=1, box=box)
            prompt = plane_from_logits(logits)
            if not prompt.any():
                raise ValueError('Model returned an empty initial POST prompt.')
            prompt_native = native_plane(prompt, low, high, valid[z])
            for reverse in (False, True):
                if reverse:
                    predictor.reset_state(state)
                    state = predictor.init_state(frames, *shape)
                predictor.add_new_mask(state, frame_idx=z, obj_id=1, mask=prompt)
                previous = None
                for index, _, logits in predictor.propagate_in_video(state, start_frame_idx=z, reverse=reverse):
                    if time.perf_counter() - started > max_seconds:
                        raise ValueError('Experimental inference exceeded its cooperative time limit.')
                    if type(index) is not int or not 0 <= index < len(result):
                        raise ValueError('Model returned an incompatible POST slice index.')
                    plane = plane_from_logits(logits)
                    result[index] = native_plane(plane, low, high, valid[index])
                    plane = result[index, crop[1], crop[2]]
                    seen[index] = True
                    outputs = state['output_dict_per_obj'][0]
                    current = outputs['cond_frame_outputs'].get(index, outputs['non_cond_frame_outputs'].get(index))
                    score = float(current['object_score_logits'].item())
                    if not np.isfinite(score):
                        raise ValueError('Tracker returned a non-finite object score.')
                    union = int(np.count_nonzero(plane | previous)) if previous is not None else 0
                    coordinates = np.argwhere(plane)
                    records.append({'slice_index': index, 'reverse': reverse, 'area_voxels': int(plane.sum()),
                                    'object_score_logit_not_calibrated': score,
                                    'centroid_yx': (coordinates.mean(0) + low).tolist() if len(coordinates) else None,
                                    'previous_slice_iou': float(np.count_nonzero(plane & previous) / union) if union else None,
                                    'inference_edge_contact': bool(plane[0].any() or plane[-1].any() or plane[:, 0].any() or plane[:, -1].any())})
                    previous = plane.copy()
        if not seen.all():
            raise ValueError('The model did not process every native POST slice.')
        info = {'seconds': time.perf_counter() - started, 'prompt_slice_index': z,
                'prompt_box_xyxy_native': native_box.tolist(), 'prompt_voxels': int(prompt_native.sum()),
                'inference_xy_bounds_halfopen': [low.tolist(), high.tolist()],
                'all_native_z_processed': True, 'native_output_grid': True,
                'preprocessing': {'context_box_scale': 2, 'model_size_yx': [512, 512],
                                  'resize': 'bilinear_align_corners_false',
                                  'normalization_mean': [.485, .456, .406],
                                  'normalization_std': [.229, .224, .225],
                                  'output_threshold_logit': 0, 'device': 'cpu', 'threads': 2,
                                  'cuda_hole_filling': False, 'cooperative_max_seconds': max_seconds},
                'outside_inference_field': 'not evaluated; stored as zero',
                'mask_clipped_after_inference': False, 'frames': records}
        return result, prompt_native, info
    finally:
        if state is not None:
            predictor.reset_state(state)


def prediction_metrics(mask, location):
    """Describe una predicción sin tratar la ROI como referencia de segmentación."""
    count = int(np.count_nonzero(mask))
    low, high = location['roi_zyx']
    inside = int(np.count_nonzero(mask[tuple(slice(a, b) for a, b in zip(low, high))]))
    areas = np.count_nonzero(mask, axis=(1, 2))
    occupied = np.flatnonzero(areas)
    centroid = []
    for axis in range(3):
        marginal = mask.sum(axis=tuple(i for i in range(3) if i != axis), dtype=np.int64)
        centroid.append(float(np.dot(np.arange(mask.shape[axis]), marginal) / count) if count else None)
    labels, components = ndimage.label(mask)
    sizes = np.bincount(labels.ravel())[1:]
    return {'voxels': count, 'inside_search_roi_voxels': inside, 'outside_search_roi_voxels': count - inside,
            'outside_fraction': (count - inside) / count if count else None,
            'majority_outside_roi': bool(2 * (count - inside) > count) if count else None,
            'centroid_zyx': centroid, 'max_area_slice_index': int(np.argmax(areas)) if count else None,
            'z_bounds_inclusive': [int(occupied[0]), int(occupied[-1])] if count else None,
            'components_6': int(components), 'largest_component_voxels': int(max(sizes)) if len(sizes) else 0,
            'scope': 'Prediction descriptors, not anatomical accuracy or treatment response.'}


def compare_predictions(a, b):
    """Dice/IoU entre predicciones: sensibilidad, nunca exactitud contra GT."""
    if (a.dtype != np.bool_ or b.dtype != np.bool_ or a.ndim != 3 or a.shape != b.shape):
        raise ValueError('Comparison requires boolean masks on the same native 3D grid.')
    intersection = int(np.count_nonzero(a & b))
    union = int(np.count_nonzero(a | b))
    total = int(a.sum()) + int(b.sum())
    return {'dice_prediction_consistency': 2 * intersection / total if total else 1.0,
            'iou_prediction_consistency': intersection / union if union else 1.0,
            'not_accuracy': True}
