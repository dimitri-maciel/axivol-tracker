"""Ajuste experimental 3D de una máscara PRE mediante corte mínimo.

Solo cambian centros de vóxel a distancia física <= b de la clase opuesta.
El núcleo interior y el exterior restante conservan sus etiquetas originales.
No hay registro, remuestreo, uso de POST ni eliminación de componentes pequeñas.

E(L) = sum_i V[-log P_clase(I_i) + (d_i/b)^2 [L_i != M_i]]
       + sum_{i,j} .5 A_ij exp(-.5((I_i-I_j)/20)^2) [L_i != L_j].

V es volumen de vóxel en mm³ y A_ij = V/espaciado_del_eje la cara en mm².
Los histogramas suavizados estiman P(HU|clase). Los pares son los seis vecinos
por cara. Un vecino fijo aporta su costo al unary de la etiqueta opuesta;
no se necesitan capacidades infinitas para mantener las anclas.

Los parámetros son elecciones de ingeniería, no umbrales clínicos validados.
Minimizar esta energía no demuestra mayor exactitud anatómica. Todo resultado
debe conservar la procedencia y quedar pendiente de revisión independiente.

Base: Boykov y Jolly, 2001, doi:10.1109/ICCV.2001.937505.
Motor: scipy.sparse.csgraph.maximum_flow. SciPy 1.18.1 convierte capacidades
a int32 incluso si recibe int64; aquí se cuantizan y verifican explícitamente.
"""
from numbers import Real

import numpy as np
from scipy import ndimage
from scipy.sparse import coo_array
from scipy.sparse.csgraph import breadth_first_order, maximum_flow


MAX_VOLUME_VOXELS = 64_000_000
MAX_ROI_VOXELS = 4_000_000
MAX_BAND_NODES = 300_000
HISTOGRAM_MIN_HU = -1024.0
HISTOGRAM_BIN_HU = 8.0
HISTOGRAM_BINS = 512
HISTOGRAM_SIGMA_BINS = 1.0
HISTOGRAM_PSEUDO_PROBABILITY = 1e-6
BACKGROUND_RING_MM = 3.0
MIN_CLASS_SAMPLES = 32
MIN_HISTOGRAM_TV = .05
PRIOR_WEIGHT = 1.0
SMOOTHNESS_MM = .5
EDGE_SIGMA_HU = 20.0
BASE_CAPACITY_SCALE = 256.0
MAX_TOTAL_CAPACITY = 1_000_000_000


def _number(value, name):
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real):
        raise ValueError(f'{name} must be finite and numeric.')
    try:
        value = float(value)
    except (OverflowError, ValueError) as error:
        raise ValueError(f'{name} must be finite and numeric.') from error
    if not np.isfinite(value):
        raise ValueError(f'{name} must be finite and numeric.')
    return value


def _validate(volume, original, spacing, band, valid):
    if (not isinstance(volume, np.ndarray) or volume.ndim != 3 or not all(volume.shape)
            or volume.size > MAX_VOLUME_VOXELS or volume.dtype.kind not in 'iuf'
            or volume.dtype.itemsize > 8):
        raise ValueError('HU volume must be a non-empty real 3D array of at most 64 million voxels.')
    if any(not np.isfinite(plane).all() for plane in volume):
        raise ValueError('HU volume contains NaN or infinite values.')
    if (not isinstance(original, np.ndarray) or original.dtype != np.bool_
            or original.shape != volume.shape or not original.any()):
        raise ValueError('Original mask must be a non-empty boolean foreground mask matching the HU volume.')
    if (not isinstance(spacing, (list, tuple, np.ndarray))
            or getattr(spacing, 'ndim', 1) != 1 or len(spacing) != 3):
        raise ValueError('Provide spacing in z, y, x order.')
    spacing = tuple(_number(value, 'Spacing') for value in spacing)
    if min(spacing) <= 0:
        raise ValueError('Spacing must be positive.')
    voxel_volume = float(np.prod(np.asarray(spacing, dtype=np.float64)))
    if not np.isfinite(voxel_volume) or not 0 < voxel_volume < 1e12:
        raise ValueError('Voxel spacing exceeds the supported numeric range.')
    band = _number(band, 'Band width')
    if not 1 <= band <= 5:
        raise ValueError('Boundary band must be between 1 and 5 mm.')
    if valid is not None:
        if not isinstance(valid, np.ndarray) or valid.dtype != np.bool_ or valid.shape != volume.shape:
            raise ValueError('Validity mask must be boolean and match the HU volume.')
        if any(np.any(foreground & ~usable) for foreground, usable in zip(original, valid)):
            raise ValueError('Original foreground includes DICOM padding; verify the source mask before refinement.')
    return spacing, band, voxel_volume


def _crop(original, spacing, band):
    """Acota EDT y modelos a la caja de la máscara más banda y anillo externo."""
    start, stop = [], []
    for axis in range(3):
        occupied = np.flatnonzero(np.any(original, axis=tuple(a for a in range(3) if a != axis)))
        # El límite previo a int evita overflow con espaciados extremadamente pequeños.
        pad = int(min(original.shape[axis], np.ceil((band+BACKGROUND_RING_MM)/spacing[axis])))+1
        start.append(max(0, int(occupied[0])-pad))
        stop.append(min(original.shape[axis], int(occupied[-1])+1+pad))
    shape = tuple(b-a for a, b in zip(start, stop))
    if int(np.prod(shape)) > MAX_ROI_VOXELS:
        raise ValueError('Refinement ROI exceeds four million voxels; use a smaller region or band.')
    return tuple(slice(a, b) for a, b in zip(start, stop)), [start, stop]


def _bins(values):
    # Saturar antes de convertir a entero evita overflow de HU extremos finitos.
    scaled = (np.asarray(values, dtype=np.float64)-HISTOGRAM_MIN_HU)/HISTOGRAM_BIN_HU
    return np.clip(scaled, 0, HISTOGRAM_BINS-1).astype(np.int32)


def _histogram(values):
    histogram = np.bincount(_bins(values), minlength=HISTOGRAM_BINS).astype(np.float64)
    histogram /= histogram.sum()
    histogram = ndimage.gaussian_filter1d(histogram, HISTOGRAM_SIGMA_BINS, mode='nearest')
    histogram += HISTOGRAM_PSEUDO_PROBABILITY
    return histogram/histogram.sum()


def _energy_terms(hu, original, active, valid, distance, spacing, band, foreground, background):
    """Construye costos no negativos y pliega los vecinos fijos en los unaries."""
    size = int(active.sum())
    ids = np.full(hu.shape, -1, dtype=np.int32)
    ids[active] = np.arange(size, dtype=np.int32)
    original_labels = original[active]
    bin_ids = _bins(hu[active])
    voxel_volume = float(np.prod(spacing))
    background_cost = -np.log(background[bin_ids])*voxel_volume
    foreground_cost = -np.log(foreground[bin_ids])*voxel_volume
    prior = PRIOR_WEIGHT*(distance[active]/band)**2*voxel_volume
    background_cost += prior*original_labels
    foreground_cost += prior*~original_labels
    first, second, weights = [], [], []
    for axis in range(3):
        low = tuple(slice(None, -1) if a == axis else slice(None) for a in range(3))
        high = tuple(slice(1, None) if a == axis else slice(None) for a in range(3))
        used = ((ids[low] >= 0) | (ids[high] >= 0)) & valid[low] & valid[high]
        a, b = ids[low][used], ids[high][used]
        difference = hu[low][used].astype(np.float64)-hu[high][used].astype(np.float64)
        weight = SMOOTHNESS_MM*(voxel_volume/spacing[axis])*np.exp(-.5*(difference/EDGE_SIGMA_HU)**2)
        both = (a >= 0) & (b >= 0)
        first.append(a[both]); second.append(b[both]); weights.append(weight[both])
        # Para vecino fijo F, solo la etiqueta B paga w; y viceversa.
        for variable, fixed, fixed_labels in ((a, b, original[high][used]),
                                              (b, a, original[low][used])):
            one = (variable >= 0) & (fixed < 0)
            np.add.at(background_cost, variable[one & fixed_labels], weight[one & fixed_labels])
            np.add.at(foreground_cost, variable[one & ~fixed_labels], weight[one & ~fixed_labels])
    common = np.minimum(background_cost, foreground_cost)
    background_cost -= common
    foreground_cost -= common
    return background_cost, foreground_cost, np.concatenate(first), np.concatenate(second), np.concatenate(weights)


def _solve_cut(background_cost, foreground_cost, first, second, weights):
    """Fuente=foreground. Capacidades y suma global verificadas antes de int32."""
    size = len(background_cost)
    source, sink = size, size+1
    vertices = np.arange(size, dtype=np.int32)
    rows = np.concatenate((np.full(size, source, dtype=np.int32), vertices, first, second))
    columns = np.concatenate((vertices, np.full(size, sink, dtype=np.int32), second, first))
    costs = np.concatenate((background_cost, foreground_cost, weights, weights))
    if not np.isfinite(costs).all() or np.any(costs < 0):
        raise ValueError('Refinement produced invalid graph costs.')
    total = float(costs.sum())
    if total <= 0:
        raise ValueError('Refinement graph has no positive evidence.')
    # Reservar un entero por arco cubre el error máximo del redondeo.
    scale = min(BASE_CAPACITY_SCALE, (MAX_TOTAL_CAPACITY-len(costs))/total)
    if scale <= 0 or not np.isfinite(scale):
        raise ValueError('Refinement graph exceeds the integer capacity budget.')
    capacities = np.rint(costs*scale).astype(np.int64)
    if capacities.sum(dtype=np.int64) > MAX_TOTAL_CAPACITY or np.any(capacities < 0):
        raise ValueError('Refinement capacities exceed the safe int32 budget.')
    graph = coo_array((capacities.astype(np.int32), (rows, columns)), shape=(size+2, size+2)).tocsr()
    graph.eliminate_zeros()
    graph.sort_indices()
    result = maximum_flow(graph, source, sink, method='dinic')
    # Restar en int64 también protege arcos recíprocos en el grafo residual.
    residual = graph.astype(np.int64)-result.flow.astype(np.int64)
    residual.data = (residual.data > 0).astype(np.int8)
    residual.eliminate_zeros()
    reachable = breadth_first_order(residual, source, directed=True, return_predecessors=False)
    labels = np.zeros(size, dtype=bool)
    labels[reachable[reachable < size]] = True
    return labels, {'capacity_scale': float(scale), 'total_capacity': int(capacities.sum(dtype=np.int64)),
                    'flow_integer': int(result.flow_value), 'graph_nodes': size+2, 'graph_arcs': int(graph.nnz)}


def _energy(labels, terms):
    background, foreground, first, second, weights = terms
    return float(np.where(labels, foreground, background).sum()+weights[labels[first] != labels[second]].sum())


def _finish(original, candidate, diagnostics, outcome, reason):
    added, removed, changed = 0, 0, []
    for z, (before, after) in enumerate(zip(original, candidate)):
        plus = int(np.count_nonzero(after & ~before))
        minus = int(np.count_nonzero(before & ~after))
        added += plus; removed += minus
        if plus or minus:
            changed.append(z)
    diagnostics.update(outcome=outcome, reason=reason, added_voxels=added, removed_voxels=removed,
                       changed_slices=changed, voxels=int(np.count_nonzero(candidate)))
    return candidate, diagnostics


def refine_boundary(volume_hu, original_bool, spacing_zyx, band_mm=3, *, valid_mask=None):
    """Propone una copia refinada en la misma grilla [z,y,x], sin tocar fuente.

    Distancias EDT usan centros de vóxel y sampling=[dz,dy,dx], en mm; no
    prometen precisión subvoxel. El radio admitido es 1–5 mm. Se rechazan
    ROI>4M o banda>300000 nodos, sin reducir resolución automáticamente.

    El núcleo EDT>b aporta muestras F; el anillo exterior b<EDT<=b+3 aporta
    B. Histogramas de 512 bins de 8 HU, suavizado sigma=1 bin y pseudoprob
    1e-6. Menos de 32 muestras por clase o TV<.05 devuelve el original como
    copia con outcome='insufficient_contrast'. TV es distancia entre modelos,
    no probabilidad de exactitud. El grafo minimiza una energía cuantizada;
    si aumenta la energía flotante, se conserva el original.

    Los diagnósticos incluyen parámetros, muestras, costos, cambios finales
    contra original y outcome refined/unchanged/insufficient_contrast. El
    llamador debe guardar adquisición, SEG, archivos fuente y versión de código.
    """
    spacing, band, _ = _validate(volume_hu, original_bool, spacing_zyx, band_mm, valid_mask)
    box, bounds = _crop(original_bool, spacing, band)
    original = original_bool[box]
    hu = volume_hu[box]
    valid = np.ones(original.shape, dtype=bool) if valid_mask is None else valid_mask[box]
    inside = ndimage.distance_transform_edt(original, sampling=spacing)
    outside = ndimage.distance_transform_edt(~original, sampling=spacing)
    distance = np.where(original, inside, outside)
    active = (distance <= band) & valid
    node_count = int(active.sum())
    if node_count > MAX_BAND_NODES:
        raise ValueError('Boundary band exceeds 300000 graph nodes; use a narrower band.')
    foreground_samples = original & (inside > band) & valid
    background_samples = ~original & (outside > band) & (outside <= band+BACKGROUND_RING_MM) & valid
    counts = (int(foreground_samples.sum()), int(background_samples.sum()))
    parameters = {'band_mm': band, 'spacing_zyx_mm': list(spacing), 'connectivity': 6,
                  'background_ring_mm': BACKGROUND_RING_MM, 'histogram_min_hu': HISTOGRAM_MIN_HU,
                  'histogram_bins': HISTOGRAM_BINS, 'histogram_bin_hu': HISTOGRAM_BIN_HU,
                  'histogram_sigma_bins': HISTOGRAM_SIGMA_BINS, 'histogram_pseudoprobability': HISTOGRAM_PSEUDO_PROBABILITY,
                  'min_class_samples': MIN_CLASS_SAMPLES, 'min_histogram_tv': MIN_HISTOGRAM_TV,
                  'prior_weight': PRIOR_WEIGHT, 'smoothness_mm': SMOOTHNESS_MM, 'edge_sigma_hu': EDGE_SIGMA_HU,
                  'base_capacity_scale': BASE_CAPACITY_SCALE, 'max_total_capacity': MAX_TOTAL_CAPACITY,
                  'max_band_nodes': MAX_BAND_NODES, 'max_roi_voxels': MAX_ROI_VOXELS,
                  'max_volume_voxels': MAX_VOLUME_VOXELS, 'solver': 'scipy_maximum_flow_dinic',
                  'capacity_dtype': 'int32', 'distance_convention': 'voxel_center_to_opposite_class'}
    diagnostics = {'method': 'native_band_graph_cut', 'parameters': parameters,
                   'roi_bounds_zyx': bounds, 'band_voxels': node_count,
                   'foreground_samples': counts[0], 'background_samples': counts[1],
                   'histogram_total_variation': None, 'warnings': ['Experimental boundary proposal; expert review is required.']}
    candidate = original_bool.copy()
    if min(counts) < MIN_CLASS_SAMPLES:
        diagnostics['warnings'].append('The band leaves insufficient stable foreground or background samples. Original preserved.')
        return _finish(original_bool, candidate, diagnostics, 'insufficient_contrast', 'insufficient_samples')
    foreground = _histogram(hu[foreground_samples])
    background = _histogram(hu[background_samples])
    tv = .5*float(np.abs(foreground-background).sum())
    diagnostics['histogram_total_variation'] = tv
    if tv < MIN_HISTOGRAM_TV:
        diagnostics['warnings'].append('Foreground and surrounding HU distributions overlap too strongly. Original preserved.')
        return _finish(original_bool, candidate, diagnostics, 'insufficient_contrast', 'similar_hu_distributions')
    if not node_count:
        return _finish(original_bool, candidate, diagnostics, 'unchanged', 'empty_band')
    terms = _energy_terms(hu, original, active, valid, distance, spacing, band, foreground, background)
    labels, graph_info = _solve_cut(*terms)
    before, after = _energy(original[active], terms), _energy(labels, terms)
    diagnostics.update(graph_info, energy_before=before, energy_after=after)
    if after > before+1e-8*max(1.0, abs(before)):
        diagnostics['warnings'].append('Quantized optimization increased the unquantized energy. Original preserved.')
        return _finish(original_bool, candidate, diagnostics, 'unchanged', 'quantization_guard')
    candidate[box][active] = labels
    changed = bool(np.any(labels != original[active]))
    return _finish(original_bool, candidate, diagnostics, 'refined' if changed else 'unchanged',
                   'minimum_cut' if changed else 'original_is_optimal')
