"""Registro rígido para localizar una región PRE en coordenadas POST.

SimpleITK devuelve una transformación fixed→moving. Aquí fixed=PRE y
moving=POST: TransformPoint(p_PRE_LPS) produce p_POST_LPS. Las imágenes de
3 mm son auxiliares para optimizar; ninguna máscara final se calcula en esa
rejilla. La geometría y medición POST permanecen nativas.

Una mejora de información mutua es una comprobación numérica, no demuestra
correspondencia de la lesión ni equivalencia de fases de contraste.
Documentación: https://simpleitk.readthedocs.io/en/master/registrationOverview.html
"""
from itertools import product
import math
import time

import numpy as np
import SimpleITK as sitk

from .geometry import GeometryError
from .volume import _check_stack


WORKING_SPACING_MM = 3.0
MAX_WORKING_VOXELS = 4_000_000
MAX_REGISTRATION_SECONDS = 30.0
MAX_ROI_VOXELS = 4_000_000
ROI_MARGIN_MM = 10.0
SAMPLING_SEED = 20261112


def _geometry(stack):
    first = stack.slices[0]
    x, y = np.asarray(first.orientation, dtype=float).reshape(2, 3)
    direction = np.column_stack((x, y, np.cross(x, y)))
    return np.asarray(first.position, dtype=float), direction, np.asarray((first.spacing[1], first.spacing[0], stack.interval_mm))


def index_to_patient(index_zyx, stack):
    """Centro de vóxel [z,y,x] → punto físico LPS, sin redondear."""
    origin, direction, spacing = _geometry(stack)
    return origin+direction@(np.asarray(index_zyx, dtype=float)[::-1]*spacing)


def patient_to_index(point_lps, stack):
    """Punto LPS → índice continuo [z,y,x] de la adquisición indicada."""
    origin, direction, spacing = _geometry(stack)
    return ((direction.T@(np.asarray(point_lps, dtype=float)-origin))/spacing)[::-1]


def _validate_volume(volume, valid, stack):
    _check_stack(stack)
    shape = (len(stack.slices), *stack.slices[0].shape)
    if (not isinstance(volume, np.ndarray) or volume.shape != shape or volume.dtype.kind not in 'iuf'
            or volume.dtype.itemsize > 8 or any(not np.isfinite(plane).all() for plane in volume)):
        raise GeometryError('Registration requires finite HU on the indexed native CT grid.')
    if valid is not None and (not isinstance(valid, np.ndarray) or valid.dtype != np.bool_ or valid.shape != shape):
        raise GeometryError('Registration validity mask must be boolean and match its CT.')


def _image(array, stack):
    result = sitk.GetImageFromArray(array)
    origin, direction, spacing = _geometry(stack)
    result.SetOrigin(tuple(origin))
    result.SetDirection(tuple(direction.ravel()))
    result.SetSpacing(tuple(spacing))
    return result


def _working_images(volume, valid, stack):
    first = stack.slices[0]
    native_size = (first.shape[1], first.shape[0], len(stack.slices))
    native_spacing = (first.spacing[1], first.spacing[0], stack.interval_mm)
    size = [int(math.ceil((n-1)*s/WORKING_SPACING_MM))+1 for n, s in zip(native_size, native_spacing)]
    if min(size) < 8 or math.prod(size) > MAX_WORKING_VOXELS:
        raise GeometryError('Registration working grid is too small or exceeds four million voxels.')
    image = _image(np.clip(volume, -200, 400).astype(np.float32), stack)
    resampler = sitk.ResampleImageFilter()
    resampler.SetNumberOfThreads(2)
    resampler.SetSize(size)
    resampler.SetOutputOrigin(image.GetOrigin())
    resampler.SetOutputDirection(image.GetDirection())
    resampler.SetOutputSpacing([WORKING_SPACING_MM]*3)
    resampler.SetDefaultPixelValue(-200)
    resampler.SetInterpolator(sitk.sitkLinear)
    working = resampler.Execute(image)
    body = volume > -500
    if valid is not None:
        body &= valid
    body_image = _image(body.astype(np.uint8), stack)
    resampler.SetDefaultPixelValue(0)
    resampler.SetInterpolator(sitk.sitkNearestNeighbor)
    working_body = resampler.Execute(body_image)
    if np.count_nonzero(sitk.GetArrayViewFromImage(working_body)) < 64:
        raise GeometryError('Insufficient valid body pixels for registration.')
    return working, working_body


def register_pre_to_post(pre_hu, post_hu, pre_stack, post_stack, *, pre_valid=None, post_valid=None):
    """Devuelve (transformación PRE→POST, diagnóstico JSON), sin transformar SEG.

    Euler3D rígido, Mattes MI50 bins, muestreo15% con semilla fija, pirámide
    4/2/1 (2/1 para imágenes auxiliares pequeñas), regular-step150 iteraciones
    por nivel. Ventana de optimización [-200,400] HU y máscara corporal >-500
    HU; esto no modifica los HU usados para segmentar POST.
    """
    _validate_volume(pre_hu, pre_valid, pre_stack)
    _validate_volume(post_hu, post_valid, post_stack)
    started = time.perf_counter()
    fixed, fixed_mask = _working_images(pre_hu, pre_valid, pre_stack)
    moving, moving_mask = _working_images(post_hu, post_valid, post_stack)
    registration = sitk.ImageRegistrationMethod()
    registration.SetNumberOfThreads(2)
    registration.SetMetricAsMattesMutualInformation(50)
    registration.SetMetricSamplingStrategy(registration.RANDOM)
    registration.SetMetricSamplingPercentage(.15, SAMPLING_SEED)
    registration.SetMetricFixedMask(fixed_mask)
    registration.SetMetricMovingMask(moving_mask)
    registration.SetInterpolator(sitk.sitkLinear)
    registration.SetOptimizerAsRegularStepGradientDescent(2.0, .05, 150, relaxationFactor=.5,
                                                         gradientMagnitudeTolerance=1e-6)
    registration.SetOptimizerScalesFromPhysicalShift()
    levels = [4, 2, 1] if min(*fixed.GetSize(), *moving.GetSize()) >= 64 else [2, 1]
    smoothing = [2, 1, 0] if len(levels) == 3 else [1, 0]
    registration.SetShrinkFactorsPerLevel(levels)
    registration.SetSmoothingSigmasPerLevel(smoothing)
    registration.SmoothingSigmasAreSpecifiedInPhysicalUnitsOn()
    initial = sitk.CenteredTransformInitializer(fixed, moving, sitk.Euler3DTransform(),
                                               sitk.CenteredTransformInitializerFilter.GEOMETRY)
    registration.SetInitialTransform(initial, inPlace=False)
    state = {'iterations': 0, 'timed_out': False}
    def iteration():
        state['iterations'] += 1
        if time.perf_counter()-started > MAX_REGISTRATION_SECONDS:
            state['timed_out'] = True
            registration.StopRegistration()
    registration.AddCommand(sitk.sitkIterationEvent, iteration)
    try:
        transform = registration.Execute(fixed, moving)
    except RuntimeError as error:
        raise GeometryError('Rigid registration could not establish sufficient image overlap.') from error
    metric = float(registration.GetMetricValue())
    parameters = list(transform.GetParameters())
    if state['timed_out']:
        raise GeometryError('Registration exceeded its 30-second optimization limit.')
    if (not np.isfinite([metric, *parameters]).all() or registration.GetMetricNumberOfValidPoints() < 32
            or len(parameters) != 6 or max(abs(v) for v in parameters[:3]) > np.deg2rad(20)
            or np.linalg.norm(parameters[3:]) > 100):
        raise GeometryError('Rigid registration failed the numeric overlap or motion checks.')
    diagnostics = {'status': 'estimated_unverified', 'mapping': 'PRE_patient_LPS_to_POST_patient_LPS',
                   'fixed': 'PRE', 'moving': 'POST', 'transform': 'Euler3D',
                   'initial_parameters': [float(v) for v in initial.GetParameters()],
                   'initial_fixed_parameters': [float(v) for v in initial.GetFixedParameters()],
                   'parameters': [float(v) for v in parameters],
                   'fixed_parameters': [float(v) for v in transform.GetFixedParameters()],
                   'metric': 'negative_Mattes_mutual_information', 'metric_value': metric,
                   'valid_metric_points': int(registration.GetMetricNumberOfValidPoints()),
                   'iterations': state['iterations'], 'stop_condition': registration.GetOptimizerStopConditionDescription(),
                   'elapsed_seconds': round(time.perf_counter()-started, 4),
                   'contrast_phases_verified': False, 'lesion_correspondence_verified': False,
                   'settings': {'working_spacing_mm': WORKING_SPACING_MM, 'working_shapes_xyz': [list(fixed.GetSize()), list(moving.GetSize())],
                                'hu_clip': [-200, 400], 'body_lower_hu': -500, 'histogram_bins': 50,
                                'sampling_fraction': .15, 'sampling_seed': SAMPLING_SEED, 'threads': 2,
                                'shrink_factors': levels, 'smoothing_mm': smoothing, 'learning_rate': 2.0,
                                'min_step': .05, 'iterations_per_level': 150, 'relaxation_factor': .5,
                                'gradient_tolerance': 1e-6, 'max_seconds': MAX_REGISTRATION_SECONDS,
                                'max_rotation_degrees': 20, 'max_translation_mm': 100},
                   'warnings': ['Rigid alignment only. Contrast phase and lesion correspondence require review.']}
    return transform, diagnostics


def _copy_rigid_initial(initial_transform):
    """Copia un Euler3D; no interpreta parámetros de otro modelo como Euler."""
    if not isinstance(initial_transform, sitk.Transform) or initial_transform.GetDimension() != 3:
        raise GeometryError('Context registration requires a three-dimensional Euler initial transform.')
    candidate = initial_transform
    if candidate.GetName() == 'CompositeTransform':
        composite = sitk.CompositeTransform(candidate)
        if composite.GetNumberOfTransforms() != 1:
            raise GeometryError('Context registration requires exactly one initial rigid transform.')
        candidate = composite.GetNthTransform(0)
    try:
        initial = sitk.Euler3DTransform(candidate)
    except RuntimeError as error:
        raise GeometryError('Context registration requires an Euler3D initial transform.') from error
    parameters = initial.GetParameters()
    if (not np.isfinite([*parameters, *initial.GetFixedParameters()]).all()
            or max(abs(v) for v in parameters[:3]) > np.deg2rad(20)
            or np.linalg.norm(parameters[3:]) > 100):
        raise GeometryError('Initial rigid transform exceeds finite motion limits.')
    return initial


def _context_working_mask(context, valid, stack, fixed):
    """Selecciona contexto válido en PRE y lo remuestrea solo para calcular MI."""
    usable = context.copy() if valid is None else context & valid
    native_count, valid_count = int(context.sum()), int(usable.sum())
    if not valid_count:
        raise GeometryError('PRE registration context contains no valid CT pixels.')
    resampler = sitk.ResampleImageFilter()
    resampler.SetNumberOfThreads(1)
    resampler.SetReferenceImage(fixed)
    resampler.SetInterpolator(sitk.sitkNearestNeighbor)
    resampler.SetDefaultPixelValue(0)
    fixed_mask = resampler.Execute(_image(usable.astype(np.uint8), stack))
    working_count = int(np.count_nonzero(sitk.GetArrayViewFromImage(fixed_mask)))
    if working_count < 64:
        raise GeometryError('Insufficient valid PRE context on the registration working grid.')
    return fixed_mask, {'native_voxels': native_count, 'valid_native_voxels': valid_count,
                        'excluded_padding_voxels': native_count-valid_count,
                        'working_voxels': working_count, 'usage': 'PRE_metric_sampling_only'}


def refine_pre_to_post(pre_hu, post_hu, pre_context_mask, pre_stack, post_stack,
                       initial_transform, *, pre_valid=None, post_valid=None):
    """Refina PRE→POST usando contexto anatómico PRE, sin producir máscaras POST.

    El contexto booleano restringe dónde se estima la información mutua (MI)
    en la imagen fixed=PRE; moving=POST se limita a píxeles corporales válidos.
    Se minimiza la MI negativa con un Euler3D inicial explícito. El contexto
    no entra como etiqueta o restricción de una segmentación POST y no se
    devuelve una máscara transformada. El padding queda fuera de la métrica.

    Se usan HU recortados [-200,400] en una retícula auxiliar de 3 mm, 50 bins,
    muestreo aleatorio 30% con semilla fija y un hilo de optimización. La
    pirámide 2/1 y suavizado 1/0 mm refinan hasta 150 iteraciones por nivel.
    Límites de 30 s, 20° y 100 mm son controles numéricos, no validación
    anatómica. La transformación inicial y todos los arrays se conservan.
    """
    _validate_volume(pre_hu, pre_valid, pre_stack)
    _validate_volume(post_hu, post_valid, post_stack)
    if (not isinstance(pre_context_mask, np.ndarray) or pre_context_mask.dtype != np.bool_
            or pre_context_mask.shape != pre_hu.shape or not pre_context_mask.any()):
        raise GeometryError('PRE registration context must be non-empty boolean data on its native CT grid.')
    initial = _copy_rigid_initial(initial_transform)
    started = time.perf_counter()
    fixed, _ = _working_images(pre_hu, pre_valid, pre_stack)
    moving, moving_mask = _working_images(post_hu, post_valid, post_stack)
    fixed_mask, context_info = _context_working_mask(pre_context_mask, pre_valid, pre_stack, fixed)
    registration = sitk.ImageRegistrationMethod()
    registration.SetNumberOfThreads(1)
    registration.SetMetricAsMattesMutualInformation(50)
    registration.SetMetricSamplingStrategy(registration.RANDOM)
    registration.SetMetricSamplingPercentage(.3, SAMPLING_SEED)
    registration.SetMetricFixedMask(fixed_mask)
    registration.SetMetricMovingMask(moving_mask)
    registration.SetInterpolator(sitk.sitkLinear)
    registration.SetOptimizerAsRegularStepGradientDescent(1.0, .02, 150, relaxationFactor=.5,
                                                         gradientMagnitudeTolerance=1e-4)
    registration.SetOptimizerScalesFromPhysicalShift()
    registration.SetShrinkFactorsPerLevel([2, 1])
    registration.SetSmoothingSigmasPerLevel([1, 0])
    registration.SmoothingSigmasAreSpecifiedInPhysicalUnitsOn()
    registration.SetInitialTransform(initial, inPlace=False)
    state = {'iterations': 0, 'timed_out': False}

    def iteration():
        state['iterations'] += 1
        if time.perf_counter()-started > MAX_REGISTRATION_SECONDS:
            state['timed_out'] = True
            registration.StopRegistration()

    registration.AddCommand(sitk.sitkIterationEvent, iteration)
    try:
        transform = registration.Execute(fixed, moving)
    except RuntimeError as error:
        raise GeometryError('Context registration could not establish sufficient image overlap.') from error
    metric = float(registration.GetMetricValue())
    parameters = list(transform.GetParameters())
    valid_points = int(registration.GetMetricNumberOfValidPoints())
    if state['timed_out']:
        raise GeometryError('Context registration exceeded its 30-second optimization limit.')
    if (not np.isfinite([metric, *parameters, *transform.GetFixedParameters()]).all()
            or valid_points < 32 or len(parameters) != 6
            or max(abs(v) for v in parameters[:3]) > np.deg2rad(20)
            or np.linalg.norm(parameters[3:]) > 100):
        raise GeometryError('Context registration failed numeric overlap or motion checks.')
    diagnostics = {
        'status': 'estimated_unverified', 'method': 'rigid_PRE_context_refinement',
        'mapping': 'PRE_patient_LPS_to_POST_patient_LPS', 'fixed': 'PRE', 'moving': 'POST',
        'transform': 'Euler3D', 'initial_parameters': list(initial.GetParameters()),
        'initial_fixed_parameters': list(initial.GetFixedParameters()),
        'parameters': parameters, 'fixed_parameters': list(transform.GetFixedParameters()),
        'metric': 'negative_Mattes_mutual_information', 'metric_value': metric,
        'valid_metric_points': valid_points, 'iterations': state['iterations'],
        'stop_condition': registration.GetOptimizerStopConditionDescription(),
        'elapsed_seconds': round(time.perf_counter()-started, 4), 'context': context_info,
        'contrast_phases_verified': False, 'lesion_correspondence_verified': False,
        'settings': {'working_spacing_mm': WORKING_SPACING_MM,
                     'working_shapes_xyz': [list(fixed.GetSize()), list(moving.GetSize())],
                     'hu_clip': [-200, 400], 'body_lower_hu': -500, 'histogram_bins': 50,
                     'sampling_fraction': .3, 'sampling_seed': SAMPLING_SEED, 'threads': 1,
                     'resampling_threads': 2, 'context_resampling_threads': 1,
                     'shrink_factors': [2, 1], 'smoothing_mm': [1, 0], 'learning_rate': 1.0,
                     'min_step': .02, 'iterations_per_level': 150, 'relaxation_factor': .5,
                     'gradient_tolerance': 1e-4, 'optimizer_scales': 'physical_shift',
                     'max_seconds': MAX_REGISTRATION_SECONDS, 'max_working_voxels': MAX_WORKING_VOXELS,
                     'minimum_context_voxels': 64, 'minimum_valid_metric_points': 32,
                     'max_rotation_degrees': 20, 'max_translation_mm': 100},
        'warnings': ['Context registration is unverified localization, not a POST segmentation or anatomical reference.']}
    return transform, diagnostics


def localize_pre_region(pre_mask, pre_stack, post_stack, transform):
    """Solo centro y caja de búsqueda: nunca propaga etiquetas al volumen POST."""
    shape = (len(pre_stack.slices), *pre_stack.slices[0].shape)
    if not isinstance(pre_mask, np.ndarray) or pre_mask.dtype != np.bool_ or pre_mask.shape != shape or not pre_mask.any():
        raise GeometryError('PRE localization requires a non-empty binary source segment on its native grid.')
    low, high, center = [], [], []
    count = int(pre_mask.sum())
    for axis in range(3):
        counts = pre_mask.sum(axis=tuple(a for a in range(3) if a != axis), dtype=np.int64)
        occupied = np.flatnonzero(counts)
        low.append(int(occupied[0])); high.append(int(occupied[-1]))
        center.append(float(np.dot(np.arange(len(counts)), counts)/count))
    mapped_center = patient_to_index(transform.TransformPoint(tuple(index_to_patient(center, pre_stack))), post_stack)
    corners = np.asarray([patient_to_index(transform.TransformPoint(tuple(index_to_patient(p, pre_stack))), post_stack)
                          for p in product(*[(a, b) for a, b in zip(low, high)])])
    post_shape = np.asarray((len(post_stack.slices), *post_stack.slices[0].shape))
    if (not np.isfinite(corners).all() or not np.isfinite(mapped_center).all()
            or np.any(corners.min(axis=0) < 0) or np.any(corners.max(axis=0) > post_shape-1)):
        raise GeometryError('The localized PRE region is not fully covered by the selected POST acquisition.')
    spacing = np.asarray((post_stack.interval_mm, *post_stack.slices[0].spacing))
    proposed_lo = np.floor(corners.min(axis=0)-ROI_MARGIN_MM/spacing).astype(int)
    proposed_hi = np.ceil(corners.max(axis=0)+ROI_MARGIN_MM/spacing).astype(int)+1
    lo, hi = np.maximum(0, proposed_lo), np.minimum(post_shape, proposed_hi)
    clipped = []
    for axis, name in enumerate(('z', 'y', 'x')):
        if proposed_lo[axis] < 0:
            clipped.append(name+'_min')
        if proposed_hi[axis] > post_shape[axis]:
            clipped.append(name+'_max')
    if math.prod(int(v) for v in hi-lo) > MAX_ROI_VOXELS:
        raise GeometryError('Localized POST ROI exceeds four million native voxels.')
    return {'pre_center_zyx': center, 'post_center_zyx': mapped_center.tolist(),
            'roi_zyx': [lo.tolist(), hi.tolist()], 'roi_margin_mm': ROI_MARGIN_MM,
            'localized_box_zyx': [np.floor(corners.min(axis=0)).astype(int).tolist(),
                                  (np.ceil(corners.max(axis=0)).astype(int)+1).tolist()],
            'transformed_box_corners_zyx': corners.tolist(), 'clipped_margin_faces': clipped,
            'roi_voxels': int(np.prod(hi-lo)), 'source_mask_usage': 'center_and_search_box_only',
            'propagated_mask_used_as_result': False}
