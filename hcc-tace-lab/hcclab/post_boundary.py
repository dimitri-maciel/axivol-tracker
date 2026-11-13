"""Propuesta experimental de borde POST en su retícula nativa.

El corte mínimo usa logits POST sin calibrar, no una probabilidad clínica ni
intensidades aprendidas de una máscara PRE. Solo puede editar centros de vóxel
a distancia física <= b de la clase opuesta de la máscara inicial. El núcleo
y el exterior restantes conservan sus etiquetas; no se garantiza topología.

Para etiqueta L_i en {0,1}, logit l_i y volumen V en mm³:

    D_i(0) = V softplus(l_i), D_i(1) = V softplus(-l_i)
    w_ij = .5 mm * (V / s_eje) * exp(-.5 * (DeltaHU / 20 HU)**2)
    E(L) = sum_i D_i(L_i) + sum_vecinos w_ij [L_i != L_j].

Los seis vecinos comparten una cara, cuya área es V/s_eje. Los HU solamente
modulan esas aristas; no se ajusta un histograma ni se exige homogeneidad del
objeto. La sigmoide del logit no está calibrada para HCC ni para TACE.

La energía se minimiza con el motor entero acotado de refinement.py. Una
energía menor significa coherencia con este modelo de costos, no mayor
exactitud anatómica. Una localización equivocada puede seguir equivocada.
"""
import numpy as np
from scipy import ndimage

from .refinement import (
    BASE_CAPACITY_SCALE, MAX_BAND_NODES, MAX_ROI_VOXELS, MAX_TOTAL_CAPACITY,
    MAX_VOLUME_VOXELS, _energy, _finish, _solve_cut, _validate,
)


SMOOTHNESS_MM = .5
EDGE_SIGMA_HU = 20.0
ENERGY_TOLERANCE = 1e-8


def _validate_logits(logits, initial, valid):
    """Exige puntuaciones reales finitas, conservadas antes del umbral cero."""
    if (not isinstance(logits, np.ndarray) or logits.shape != initial.shape
            or logits.dtype.kind not in 'iuf' or logits.dtype.itemsize > 8):
        raise ValueError('POST logits must be a real 3D array matching the initial mask.')
    for z, plane in enumerate(logits):
        if not np.isfinite(plane).all():
            raise ValueError('POST logits contain NaN or infinite values.')
        usable = True if valid is None else valid[z]
        if np.any(((plane > 0) != initial[z]) & usable):
            raise ValueError('Initial POST mask must equal logits > 0 on valid inferred pixels.')


def _boundary_region(initial, spacing, band):
    """Acota la EDT sin confundir un borde del recorte con evidencia nueva."""
    low, high = [], []
    for axis in range(3):
        occupied = np.flatnonzero(np.any(initial, axis=tuple(a for a in range(3) if a != axis)))
        pad = int(min(initial.shape[axis], np.ceil(band/spacing[axis])))+1
        low.append(max(0, int(occupied[0])-pad))
        high.append(min(initial.shape[axis], int(occupied[-1])+1+pad))
    if int(np.prod(np.asarray(high)-low)) > MAX_ROI_VOXELS:
        raise ValueError('POST boundary ROI exceeds four million voxels.')
    return tuple(slice(a, b) for a, b in zip(low, high)), [low, high]


def _terms(hu, logits, initial, active, valid, spacing):
    """Pliega vecinos fijos en costos unary; no usa capacidades infinitas."""
    count = int(active.sum())
    ids = np.full(hu.shape, -1, dtype=np.int32)
    ids[active] = np.arange(count, dtype=np.int32)
    voxel_volume = float(np.prod(spacing))
    scores = logits[active].astype(np.float64)
    with np.errstate(over='ignore'):
        background = np.logaddexp(0., scores)*voxel_volume
        foreground = np.logaddexp(0., -scores)*voxel_volume
    if not np.isfinite(background).all() or not np.isfinite(foreground).all():
        raise ValueError('POST logit costs exceed the supported numeric range.')
    first, second, weights = [], [], []
    for axis in range(3):
        low = tuple(slice(None, -1) if a == axis else slice(None) for a in range(3))
        high = tuple(slice(1, None) if a == axis else slice(None) for a in range(3))
        used = ((ids[low] >= 0) | (ids[high] >= 0)) & valid[low] & valid[high]
        a, b = ids[low][used], ids[high][used]
        # HU finitos extremadamente alejados pueden saturar delta²; su
        # afinidad límite es cero, sin NaN ni dependencia del dtype de entrada.
        with np.errstate(over='ignore'):
            delta = hu[low][used].astype(np.float64)-hu[high][used].astype(np.float64)
            weight = (SMOOTHNESS_MM*voxel_volume/spacing[axis]
                      * np.exp(-.5*(delta/EDGE_SIGMA_HU)**2))
        both = (a >= 0) & (b >= 0)
        first.append(a[both]); second.append(b[both]); weights.append(weight[both])
        for variable, fixed, fixed_labels in ((a, b, initial[high][used]),
                                              (b, a, initial[low][used])):
            one = (variable >= 0) & (fixed < 0)
            np.add.at(background, variable[one & fixed_labels], weight[one & fixed_labels])
            np.add.at(foreground, variable[one & ~fixed_labels], weight[one & ~fixed_labels])
    # Restar un costo común por nodo no cambia el óptimo y reduce el rango
    # entero. energy_before/after excluyen esa misma constante, documentada.
    common = np.minimum(background, foreground)
    background -= common
    foreground -= common
    return background, foreground, np.concatenate(first), np.concatenate(second), np.concatenate(weights)


def refine_post_boundary(post_hu, initial_mask, logits, spacing_zyx, band_mm=2.0, *, valid_mask=None):
    """Devuelve (copia bool [z,y,x], diagnostics JSON), sin modificar entradas.

    ``logits`` debe conservar las puntuaciones nativas de inferencia, no
    reconstruirlas desde una máscara binaria. En soporte válido, la máscara
    inicial debe coincidir exactamente con ``logits > 0``. El llamador debe
    excluir del soporte tanto padding DICOM como posiciones no inferidas, o
    pasar solo el campo inferido y conservar sus offsets/geometría al insertar
    la salida. Una frontera artificial sigue requiriendo control del llamador.

    Radio 1–5 mm, default 2. EDT entre centros de vóxel, no superficies
    continuas: con dz=2.5 mm, una banda de 2 mm no puede desplazar un extremo
    aislado en z un corte entero. No hay remuestreo, cierre morfológico,
    eliminación de islas, recorte a PRE ni promesa de preservar topología.

    No cambia etiquetas fuera de banda ni padding. Se limita a 64M vóxeles,
    ROI de 4M y 300000 nodos; no reduce resolución ante exceso de recursos.
    Si no disminuye la energía flotante más que la tolerancia relativa 1e-8,
    conserva el original. Distancias de cambios son a la clase opuesta en el
    original, no error respecto de una referencia anatómica independiente.
    """
    spacing, band, _ = _validate(post_hu, initial_mask, spacing_zyx, band_mm, valid_mask)
    if initial_mask.all():
        raise ValueError('Initial POST mask must contain both foreground and background in the inferred field.')
    _validate_logits(logits, initial_mask, valid_mask)
    box, bounds = _boundary_region(initial_mask, spacing, band)
    initial, hu, scores = initial_mask[box], post_hu[box], logits[box]
    valid = np.ones(initial.shape, dtype=bool) if valid_mask is None else valid_mask[box]
    inside = ndimage.distance_transform_edt(initial, sampling=spacing)
    outside = ndimage.distance_transform_edt(~initial, sampling=spacing)
    distance = np.where(initial, inside, outside)
    active = (distance <= band) & valid
    count = int(active.sum())
    if count > MAX_BAND_NODES:
        raise ValueError('POST boundary band exceeds 300000 graph nodes.')
    parameters = {
        'band_mm': band, 'spacing_zyx_mm': list(spacing), 'connectivity': 6,
        'unary': 'voxel_volume_mm3_times_softplus_signed_logit',
        'logit_temperature': 1.0, 'logit_threshold': 0.0,
        'logits_calibrated': False, 'smoothness_mm': SMOOTHNESS_MM,
        'edge_sigma_hu': EDGE_SIGMA_HU, 'base_capacity_scale': BASE_CAPACITY_SCALE,
        'max_total_capacity': MAX_TOTAL_CAPACITY, 'capacity_dtype': 'int32',
        'solver': 'scipy_maximum_flow_dinic', 'energy_relative_tolerance': ENERGY_TOLERANCE,
        'max_volume_voxels': MAX_VOLUME_VOXELS, 'max_roi_voxels': MAX_ROI_VOXELS,
        'max_band_nodes': MAX_BAND_NODES,
        'distance_convention': 'voxel_center_to_opposite_initial_class',
    }
    diagnostics = {
        'method': 'post_native_logit_band_graph_cut', 'parameters': parameters,
        'roi_bounds_zyx': bounds, 'band_voxels': count,
        'initial_voxels': int(initial_mask.sum()),
        'energy_common_unary_constant_removed': True,
        'energy_before': None, 'energy_after': None,
        'editable_band_touch_faces': [name for name, plane, at_edge in (
            ('z_min', active[0], bounds[0][0] == 0),
            ('z_max', active[-1], bounds[1][0] == initial_mask.shape[0]),
            ('y_min', active[:, 0], bounds[0][1] == 0),
            ('y_max', active[:, -1], bounds[1][1] == initial_mask.shape[1]),
            ('x_min', active[:, :, 0], bounds[0][2] == 0),
            ('x_max', active[:, :, -1], bounds[1][2] == initial_mask.shape[2]))
            if at_edge and plane.any()],
        'changed_voxel_distances_mm': {'added_max': 0.0, 'removed_max': 0.0},
        'warnings': ['Experimental POST boundary proposal; target identity and anatomical accuracy remain unverified.'],
    }
    candidate = initial_mask.copy()
    if not count:
        return _finish(initial_mask, candidate, diagnostics, 'unchanged', 'empty_band')
    terms = _terms(hu, scores, initial, active, valid, spacing)
    labels, graph_info = _solve_cut(*terms)
    before, after = _energy(initial[active], terms), _energy(labels, terms)
    diagnostics.update(graph_info, energy_before=before, energy_after=after)
    tolerance = ENERGY_TOLERANCE*max(1.0, abs(before))
    if after > before+tolerance:
        diagnostics['warnings'].append('Quantized optimization increased floating-point energy. Initial mask preserved.')
        return _finish(initial_mask, candidate, diagnostics, 'unchanged', 'quantization_guard')
    if not np.any(labels != initial[active]):
        return _finish(initial_mask, candidate, diagnostics, 'unchanged', 'initial_is_optimal')
    if before-after <= tolerance:
        return _finish(initial_mask, candidate, diagnostics, 'unchanged', 'insufficient_energy_decrease')
    candidate[box][active] = labels
    added = candidate[box] & ~initial
    removed = initial & ~candidate[box]
    diagnostics['changed_voxel_distances_mm'] = {
        'added_max': float(outside[added].max()) if added.any() else 0.0,
        'removed_max': float(inside[removed].max()) if removed.any() else 0.0,
    }
    return _finish(initial_mask, candidate, diagnostics, 'refined', 'minimum_cut')
