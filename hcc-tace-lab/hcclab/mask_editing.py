"""Corrección manual en una copia de una máscara; nunca modifica la fuente.

Cada trazo es la unión de discos físicos a lo largo de una polilínea. Para
un centro de píxel p y un segmento a→b, en milímetros, se calcula
t = clip(((p-a)·(b-a))/||b-a||², 0, 1). Se incluye p cuando
||p-a-t(b-a)||² <= r². Un segmento de longitud cero es un disco centrado
en a. El espaciado anisotrópico se aplica antes de calcular distancias:
un círculo físico puede verse como una elipse en coordenadas de píxel.

Los trazos solo afectan al corte indicado. No se interpola entre cortes,
no se erosiona automáticamente y una edición no establece precisión clínica.
"""
from math import ceil, floor, isfinite, sqrt
from numbers import Integral, Real

import numpy as np


MAX_VOLUME_VOXELS = 64_000_000
MAX_STROKES = 100
MAX_POINTS = 2048
MAX_RADIUS_MM = 20.0
TILE_SIZE = 512


def _number(value, name):
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real):
        raise ValueError(f'{name} must be a finite number.')
    try:
        number = float(value)
    except (OverflowError, ValueError) as error:
        raise ValueError(f'{name} must be a finite number.') from error
    if not isfinite(number):
        raise ValueError(f'{name} must be a finite number.')
    return number


def _index(value, limit, name):
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Integral):
        raise ValueError(f'{name} must be an integer, not a boolean or decimal.')
    if not 0 <= value < limit:
        raise ValueError(f'{name} is outside the mask.')
    return int(value)


def _sequence(value, length=None):
    return (isinstance(value, (list, tuple, np.ndarray))
            and getattr(value, 'ndim', 1) == 1
            and (length is None or len(value) == length))


def _validate(mask, spacing, strokes):
    """Valida todos los trazos antes de copiar o editar un solo vóxel."""
    if (not isinstance(mask, np.ndarray) or mask.dtype != np.bool_
            or mask.ndim != 3 or not all(mask.shape) or mask.size > MAX_VOLUME_VOXELS):
        raise ValueError('Mask must be a non-empty boolean 3D array of at most 64 million voxels.')
    if not _sequence(spacing, 2):
        raise ValueError('Spacing must contain row and column spacing in millimeters.')
    dy, dx = (_number(value, 'Spacing') for value in spacing)
    if min(dy, dx) <= 0:
        raise ValueError('Spacing must be positive.')
    # Evitar overflow/underflow de distancias al cuadrado en entradas extremas.
    numerical_limit = sqrt(np.finfo(np.float64).max)/4
    if max(dy, dx)*max(mask.shape[1:]) > numerical_limit or min(dy, dx)**2 == 0:
        raise ValueError('Spacing exceeds the supported floating-point distance range.')
    if not isinstance(strokes, (list, tuple)) or len(strokes) > MAX_STROKES:
        raise ValueError('Provide at most 100 brush strokes.')
    normalized, point_count = [], 0
    for stroke in strokes:
        if not isinstance(stroke, dict) or set(stroke) != {'slice', 'mode', 'radius_mm', 'points'}:
            raise ValueError('Each stroke requires slice, mode, radius_mm and points.')
        z = _index(stroke['slice'], mask.shape[0], 'Slice')
        if not isinstance(stroke['mode'], str) or stroke['mode'] not in ('paint', 'erase'):
            raise ValueError('Brush mode must be paint or erase.')
        radius = _number(stroke['radius_mm'], 'Brush radius')
        if not 0 < radius <= MAX_RADIUS_MM or radius**2 == 0:
            raise ValueError('Brush radius must be greater than zero and at most 20 mm, within numeric range.')
        points = stroke['points']
        if not isinstance(points, (list, tuple)) or not points:
            raise ValueError('A stroke must contain at least one pixel center.')
        point_count += len(points)
        if point_count > MAX_POINTS:
            raise ValueError('Brush strokes may contain at most 2048 points in total.')
        coordinates = []
        for point in points:
            if not _sequence(point, 2):
                raise ValueError('Each brush point must contain row and column indices.')
            coordinates.append((_index(point[0], mask.shape[1], 'Row'),
                                _index(point[1], mask.shape[2], 'Column')))
        normalized.append((z, stroke['mode'] == 'paint', radius, coordinates))
    return (dy, dx), normalized


def _segment(plane, start, stop, spacing, radius, value):
    """Rasteriza una cápsula por bloques acotados; incluye ambos extremos."""
    dy, dx = spacing
    y0, x0 = start
    y1, x1 = stop
    # Recortar antes de crear rejillas mantiene el trabajo dentro de la imagen.
    pad_y, pad_x = min(plane.shape[0], radius/dy), min(plane.shape[1], radius/dx)
    top = max(0, ceil(min(y0, y1)-pad_y))
    bottom = min(plane.shape[0], floor(max(y0, y1)+pad_y)+1)
    left = max(0, ceil(min(x0, x1)-pad_x))
    right = min(plane.shape[1], floor(max(x0, x1)+pad_x)+1)
    vy, vx = (y1-y0)*dy, (x1-x0)*dx
    length_squared = vy*vy+vx*vx
    for row in range(top, bottom, TILE_SIZE):
        for column in range(left, right, TILE_SIZE):
            rows = slice(row, min(row+TILE_SIZE, bottom))
            columns = slice(column, min(column+TILE_SIZE, right))
            py = (np.arange(rows.start, rows.stop, dtype=np.float64)-y0)[:, None]*dy
            px = (np.arange(columns.start, columns.stop, dtype=np.float64)-x0)[None, :]*dx
            t = np.clip((py*vy+px*vx)/length_squared, 0, 1) if length_squared else 0
            inside = (py-t*vy)**2+(px-t*vx)**2 <= radius*radius
            view = plane[rows, columns]
            view[inside] = value


def apply_strokes(mask, spacing, strokes):
    """Devuelve una copia editada y cambios finales respecto a ``mask``.

    ``mask``: ndarray bool [z, y, x]; ``spacing``: [dy, dx] en mm.
    Cada trazo contiene ``slice`` entero desde cero, ``mode`` paint/erase,
    ``radius_mm`` en (0, 20] y ``points`` [[y, x], ...], enteros desde cero.
    Los centros deben estar en la imagen; el pincel se recorta en sus bordes.
    Hasta 100 trazos y 2048 puntos en total. Una lista vacía devuelve copia.

    ``added_voxels`` y ``removed_voxels`` comparan el resultado final con la
    fuente, no suman operaciones. ``changed_slices`` contiene índices desde
    cero y ``voxels`` el total final. Así, pintar y después borrar el mismo
    fondo puede producir cero cambios netos. Reproducir una lista previa de
    trazos desde la fuente permite deshacer sin alterar el original.
    """
    spacing, normalized = _validate(mask, spacing, strokes)
    edited = mask.copy()
    for z, value, radius, points in normalized:
        pairs = zip(points, points[1:]) if len(points) > 1 else [(points[0], points[0])]
        for start, stop in pairs:
            _segment(edited[z], start, stop, spacing, radius, value)
    added, removed, changed = 0, 0, []
    for z, (before, after) in enumerate(zip(mask, edited)):
        plus = int(np.count_nonzero(after & ~before))
        minus = int(np.count_nonzero(before & ~after))
        added += plus
        removed += minus
        if plus or minus:
            changed.append(z)
    return edited, {'added_voxels': added, 'removed_voxels': removed,
                    'changed_slices': changed, 'voxels': int(np.count_nonzero(edited))}
