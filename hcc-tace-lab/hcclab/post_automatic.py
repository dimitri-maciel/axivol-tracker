"""Orquestación de propuestas POST, con abstención explícita.

La máscara PRE únicamente localiza centro/caja mediante registro. Si se indica
un checkpoint, se ejecuta exclusivamente el adaptador aprendido con imágenes
POST. Sin checkpoint queda disponible el baseline de crecimiento conectado
como comparador: percentiles10–90 y5–95, con abstención por fuga/inestabilidad.
Un fallo del modelo nunca activa este baseline como sustituto silencioso.
Ningún camino devuelve una máscara PRE transferida ni demuestra exactitud.
"""
import numpy as np

from .geometry import GeometryError
from .registration import localize_pre_region, register_pre_to_post, refine_pre_to_post
from .segmentation import grow_region


MIN_SAMPLES = 16
MIN_COMPONENT_VOXELS = 32
MAX_ROI_FRACTION = .5
MIN_SENSITIVITY_DICE = .85
MAX_RELATIVE_VOLUME_DIFFERENCE = .20
MIN_HU_HALF_WIDTH = 5.0


class AutomaticSegmentationError(GeometryError):
    """Abstención revisable: el llamador puede exportar diagnostics sin máscara."""
    def __init__(self, message, diagnostics):
        super().__init__(message)
        self.diagnostics = diagnostics


def _abstain(message, diagnostics, reason):
    diagnostics['status'] = 'abstained'
    diagnostics['reason'] = reason
    raise AutomaticSegmentationError(message, {'segmentation': diagnostics})


def segment_from_location(post_hu, spacing_zyx, location, *, valid_mask=None):
    """Segmenta exclusivamente POST desde una localización, no desde un prior."""
    spacing = np.asarray(spacing_zyx, dtype=float)
    center = np.asarray(location['post_center_zyx'], dtype=float)
    lo, hi = (np.asarray(v, dtype=int) for v in location['roi_zyx'])
    radius = float(min(10, max(2, .1*float(np.min((hi-lo)*spacing)))))
    diagnostics = {'status': 'evaluating', 'method': 'automatic_connected_threshold', 'source_intensities': 'POST_native_HU',
                   'source_mask_usage': 'localization_only', 'connectivity': 6,
                   'settings': {'base_percentiles': [10, 90], 'sensitivity_percentiles': [5, 95],
                                'sample_radius_mm': radius, 'sample_radius_rule': 'clip(0.1*minimum_ROI_side_mm,2,10)',
                                'min_samples': MIN_SAMPLES, 'min_component_voxels': MIN_COMPONENT_VOXELS,
                                'max_roi_fraction': MAX_ROI_FRACTION, 'min_sensitivity_dice': MIN_SENSITIVITY_DICE,
                                'max_relative_volume_difference': MAX_RELATIVE_VOLUME_DIFFERENCE,
                                'minimum_hu_half_width': MIN_HU_HALF_WIDTH},
                   'warnings': ['Intensity-connected POST region; anatomical target and treatment response are unverified.']}
    sample_lo = np.maximum(lo, np.floor(center-radius/spacing).astype(int))
    sample_hi = np.minimum(hi, np.ceil(center+radius/spacing).astype(int)+1)
    if np.any(sample_hi <= sample_lo):
        _abstain('The localized center has no valid POST sampling region.', diagnostics, 'invalid_localization')
    box = tuple(slice(int(a), int(b)) for a, b in zip(sample_lo, sample_hi))
    grids = np.ogrid[tuple(slice(int(a), int(b)) for a, b in zip(sample_lo, sample_hi))]
    squared_distance = sum(((g-c)*s)**2 for g, c, s in zip(grids, center, spacing))
    inside = squared_distance <= radius**2
    usable = inside if valid_mask is None else inside & valid_mask[box]
    sample = post_hu[box][usable]
    diagnostics['sample_voxels'] = int(sample.size)
    if sample.size < MIN_SAMPLES or sample.size < .8*np.count_nonzero(inside):
        _abstain('Insufficient valid POST pixels near the localized center.', diagnostics, 'insufficient_samples')
    quantiles = np.percentile(sample, [5, 10, 50, 90, 95])
    median = float(quantiles[2])
    lower = min(float(quantiles[1]), median-MIN_HU_HALF_WIDTH)
    upper = max(float(quantiles[3]), median+MIN_HU_HALF_WIDTH)
    wide_lower, wide_upper = min(float(quantiles[0]), lower), max(float(quantiles[4]), upper)
    eligible = usable & (post_hu[box] >= lower) & (post_hu[box] <= upper)
    if not eligible.any():
        _abstain('No valid POST seed lies within the automatic intensity interval.', diagnostics, 'no_seed')
    distances = np.where(eligible, squared_distance, np.inf)
    seed = np.asarray(np.unravel_index(int(np.argmin(distances)), distances.shape))+sample_lo
    diagnostics.update(seed_zyx=seed.tolist(), seed_hu=float(post_hu[tuple(seed)]),
                       seed_offset_mm=float(np.sqrt(distances.min())), roi_zyx=[lo.tolist(), hi.tolist()],
                       sample_hu_percentiles={'p05':float(quantiles[0]), 'p10':float(quantiles[1]),
                                              'p50':median, 'p90':float(quantiles[3]), 'p95':float(quantiles[4])},
                       thresholds_hu=[lower, upper], sensitivity_thresholds_hu=[wide_lower, wide_upper])
    base, base_info = grow_region(post_hu, seed.tolist(), [lo.tolist(), hi.tolist()], lower, upper, valid_mask=valid_mask)
    diagnostics['base_component'] = base_info
    if base_info['roi_touch_faces'] or base_info['roi_fraction'] > MAX_ROI_FRACTION:
        _abstain('Automatic POST segmentation spread to the search-region boundary. Review alignment and select a manual region/seed.',
                 diagnostics, 'roi_leakage')
    if base_info['voxels'] < MIN_COMPONENT_VOXELS:
        _abstain('Automatic POST segmentation produced too little connected evidence. Review the region and seed.',
                 diagnostics, 'insufficient_component')
    wide, wide_info = grow_region(post_hu, seed.tolist(), [lo.tolist(), hi.tolist()], wide_lower, wide_upper, valid_mask=valid_mask)
    dice = 2*int(np.count_nonzero(base & wide))/(base_info['voxels']+wide_info['voxels'])
    relative = abs(wide_info['voxels']-base_info['voxels'])/base_info['voxels']
    diagnostics.update(sensitivity_component=wide_info, sensitivity_dice=float(dice),
                       relative_voxel_difference=float(relative))
    if (wide_info['roi_touch_faces'] or wide_info['roi_fraction'] > MAX_ROI_FRACTION
            or dice < MIN_SENSITIVITY_DICE or relative > MAX_RELATIVE_VOLUME_DIFFERENCE):
        _abstain('Automatic POST segmentation is unstable when its HU interval changes. Review it with manual controls.',
                 diagnostics, 'threshold_instability')
    areas = np.count_nonzero(base, axis=(1, 2))
    diagnostics.update(status='candidate_unreviewed', voxels=base_info['voxels'],
                       display_slice_index=int(np.argmax(areas)))
    return base, diagnostics


def automatic_post_candidate(pre_hu, post_hu, pre_mask, pre_stack, post_stack, *,
                             pre_valid=None, post_valid=None, model_path=None, pre_context_mask=None):
    """Devuelve (máscara POST, diagnósticos) o AutomaticSegmentationError.

    El error conserva registro/localización/criterio de abstención para
    inspección y exportación. No tiene una máscara alternativa ni volumen.
    """
    try:
        transform, registration = register_pre_to_post(pre_hu, post_hu, pre_stack, post_stack,
                                                       pre_valid=pre_valid, post_valid=post_valid)
    except GeometryError as error:
        raise AutomaticSegmentationError(str(error), {'registration': {'status': 'failed', 'reason': str(error)},
                                                       'segmentation': {'status': 'not_started'}}) from error
    if pre_context_mask is not None:
        global_registration = registration
        try:
            transform, registration = refine_pre_to_post(
                pre_hu, post_hu, pre_context_mask, pre_stack, post_stack, transform,
                pre_valid=pre_valid, post_valid=post_valid)
        except GeometryError as error:
            raise AutomaticSegmentationError(str(error), {
                'registration': {'status': 'failed', 'stage': 'PRE_context_refinement',
                                 'reason': str(error), 'global_registration': global_registration},
                'segmentation': {'status': 'not_started'}}) from error
        registration['global_registration'] = global_registration
    try:
        location = localize_pre_region(pre_mask, pre_stack, post_stack, transform)
    except GeometryError as error:
        registration['localization'] = {'status': 'failed', 'reason': str(error)}
        raise AutomaticSegmentationError(str(error), {'registration': registration,
                                                       'segmentation': {'status': 'not_started'}}) from error
    registration['localization'] = location
    spacing = (post_stack.interval_mm, *post_stack.slices[0].spacing)
    if model_path is not None:
        # Carga diferida: las pruebas del comparador y la geometría no requieren
        # inicializar Torch. PRE no forma parte de los argumentos del modelo.
        from .post_model import ModelSegmentationError, segment_post_model
        try:
            mask, segmentation = segment_post_model(post_hu, spacing, location,
                                                     checkpoint_path=model_path, valid_mask=post_valid)
        except ModelSegmentationError as error:
            raise AutomaticSegmentationError(str(error), {'registration': registration,
                                                           'segmentation': error.diagnostics}) from error
        return mask, {'registration': registration, 'segmentation': segmentation}
    try:
        mask, segmentation = segment_from_location(post_hu, spacing, location, valid_mask=post_valid)
    except AutomaticSegmentationError as error:
        error.diagnostics['registration'] = registration
        raise
    return mask, {'registration': registration, 'segmentation': segmentation}
