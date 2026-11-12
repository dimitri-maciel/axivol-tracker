"""Candidato 3D por umbral y conectividad; no contiene reglas de respuesta clínica.

Para una imagen I, una caja R y un intervalo [L, U], definimos el conjunto
admisible A = {p en R : L <= I(p) <= U}. El resultado es la componente de A
conectada con la semilla mediante vecinos que comparten una cara (6 en 3D).
Dos vóxeles que solo se tocan por una arista o esquina no quedan conectados.

Este baseline es determinista y no está entrenado. Un umbral puede incluir
tejidos vecinos con intensidad similar; limitar la ROI no demuestra que sus
límites coincidan con una lesión. El llamador debe conservar el estado de
candidato pendiente de revisión, la adquisición y los parámetros humanos.

Referencia de la operación de componentes conexas:
https://docs.scipy.org/doc/scipy/reference/generated/scipy.ndimage.label.html
"""
from math import prod
from numbers import Integral, Real

import numpy as np
from scipy import ndimage


MAX_VOLUME_VOXELS = 64_000_000
MAX_ROI_VOXELS = 4_000_000


def _index_triplet(value, name):
    """Exige índices enteros reales: no redondea 1.5 ni acepta True como 1."""
    if (not isinstance(value, (list, tuple, np.ndarray))
            or getattr(value, 'ndim', 1) != 1 or len(value) != 3):
        raise ValueError(f'{name} must contain three integer indices in z, y, x order.')
    if any(isinstance(v, (bool, np.bool_)) or not isinstance(v, Integral) for v in value):
        raise ValueError(f'{name} must contain integer indices, not booleans or decimals.')
    return tuple(int(v) for v in value)


def _finite_number(value, name):
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real):
        raise ValueError(f'{name} must be a finite number.')
    try:
        result = float(value)
    except (OverflowError, ValueError) as error:
        raise ValueError(f'{name} must be a finite number.') from error
    if not np.isfinite(result):
        raise ValueError(f'{name} must be a finite number.')
    return result


def grow_region(volume_hu, seed, roi, lower_hu, upper_hu, *, valid_mask=None):
    """Devuelve ``(mask, diagnostics)`` en la misma grilla nativa de entrada.

    ``volume_hu`` es un ndarray numérico finito con orden [z, y, x], ya
    convertido a unidades Hounsfield. No es una imagen PNG con ventana/nivel.
    ``seed`` es [z, y, x], con índices desde cero. ``roi`` contiene dos
    esquinas [[z0, y0, x0], [z1, y1, x1]]; la primera se incluye y la segunda
    se excluye, igual que el slicing de NumPy. [L, U] incluye ambos extremos.

    Se exige que la semilla esté dentro de la caja y del intervalo HU. La
    función rechaza entradas ambiguas y nunca elige una semilla alternativa.
    La entrada no se modifica. ``mask`` es booleana, independiente de la
    entrada y falsa fuera de la ROI. No se interpola ni se remuestrea.
    ``valid_mask``, cuando se suministra, es booleana y tiene la misma forma;
    permite excluir el padding DICOM sin inventarle una intensidad distinta.

    ``bounds`` describe la caja mínima del resultado, también half-open.
    ``roi_touch_faces`` enumera las caras de la ROI tocadas por el candidato:
    es una advertencia de posible truncamiento/fuga, no una prueba de error.
    Los límites de tamaño acotan las matrices de trabajo: la etiqueta int32
    ocupa como máximo 16 MB para una ROI de cuatro millones de vóxeles.
    """
    if (not isinstance(volume_hu, np.ndarray) or volume_hu.ndim != 3
            or any(n <= 0 for n in volume_hu.shape)
            or volume_hu.dtype.kind not in 'iuf' or volume_hu.dtype.itemsize > 8):
        raise ValueError('HU volume must be a non-empty, real numeric 3D array.')
    if volume_hu.size > MAX_VOLUME_VOXELS:
        raise ValueError(f'HU volume exceeds the {MAX_VOLUME_VOXELS:,}-voxel limit.')
    # Validar plano por plano evita otra matriz temporal del tamaño del volumen.
    if any(not np.isfinite(plane).all() for plane in volume_hu):
        raise ValueError('HU volume contains NaN or infinite values.')
    if valid_mask is not None and (not isinstance(valid_mask, np.ndarray)
            or valid_mask.shape != volume_hu.shape or valid_mask.dtype != np.bool_):
        raise ValueError('Validity mask must be boolean and match the HU volume shape.')

    seed = _index_triplet(seed, 'Seed')
    if (not isinstance(roi, (list, tuple, np.ndarray))
            or getattr(roi, 'ndim', 2) != 2 or len(roi) != 2):
        raise ValueError('ROI must contain a lower and an exclusive upper corner.')
    start = _index_triplet(roi[0], 'ROI lower corner')
    stop = _index_triplet(roi[1], 'ROI upper corner')
    if any(not 0 <= lo < hi <= size for lo, hi, size in zip(start, stop, volume_hu.shape)):
        raise ValueError('ROI must be non-empty and inside the HU volume.')
    roi_shape = tuple(hi-lo for lo, hi in zip(start, stop))
    roi_voxels = prod(roi_shape)
    if roi_voxels > MAX_ROI_VOXELS:
        raise ValueError(f'ROI exceeds the {MAX_ROI_VOXELS:,}-voxel limit.')
    if any(not lo <= point < hi for point, lo, hi in zip(seed, start, stop)):
        raise ValueError('Seed must be inside the ROI.')
    if valid_mask is not None and not valid_mask[seed]:
        raise ValueError('Seed is in DICOM pixel padding; choose a valid image pixel.')

    lower = _finite_number(lower_hu, 'Lower HU threshold')
    upper = _finite_number(upper_hu, 'Upper HU threshold')
    if lower > upper:
        raise ValueError('Lower HU threshold must not exceed upper HU threshold.')
    if not lower <= float(volume_hu[seed]) <= upper:
        raise ValueError('Seed intensity is outside the selected HU interval.')

    box = tuple(slice(lo, hi) for lo, hi in zip(start, stop))
    cropped = volume_hu[box]
    admissible = (cropped >= lower) & (cropped <= upper)
    if valid_mask is not None:
        admissible &= valid_mask[box]
    # generate_binary_structure(3, 1) fija los seis vecinos por cara; no diagonales.
    labels, _ = ndimage.label(admissible, structure=ndimage.generate_binary_structure(3, 1))
    local_seed = tuple(point-lo for point, lo in zip(seed, start))
    selected = labels == labels[local_seed]
    mask = np.zeros(volume_hu.shape, dtype=bool)
    mask[box] = selected

    bounds_start, bounds_stop, faces = [], [], []
    for axis, axis_name in enumerate(('z', 'y', 'x')):
        occupied = np.flatnonzero(np.any(selected, axis=tuple(a for a in range(3) if a != axis)))
        bounds_start.append(int(occupied[0])+start[axis])
        bounds_stop.append(int(occupied[-1])+start[axis]+1)
        if occupied[0] == 0:
            faces.append(f'{axis_name}_min')
        if occupied[-1] == roi_shape[axis]-1:
            faces.append(f'{axis_name}_max')
    voxels = int(np.count_nonzero(selected))
    diagnostics = {'voxels':voxels, 'roi_touch_faces':faces,
                   'bounds':[bounds_start,bounds_stop], 'roi_voxels':roi_voxels,
                   'roi_fraction':voxels/roi_voxels, 'connectivity':6}
    return mask, diagnostics
