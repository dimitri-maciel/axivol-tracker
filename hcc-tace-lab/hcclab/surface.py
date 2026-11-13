"""Superficie exacta de cajas de vóxel en coordenadas físicas DICOM LPS.

No lee archivos ni altera máscaras. El llamador debe verificar la candidata,
sus hashes y la adquisición antes de proporcionar una máscara y su Stack.
Se representan todas las caras que separan foreground de background,
incluidos huecos, componentes pequeñas y caras en el límite de la adquisición.
No hay interpolación, suavizado, selección de componente mayor ni diezmado.

Cada centro de vóxel (x,y,z) ocupa una caja entre índices ±0.5. Sus esquinas
pasan a LPS con p = origen + dirección @ ((esquina_xyz-0.5)*spacing_xyz).
La dirección contiene, por columnas, los ejes x/y DICOM y su producto cruz z.
Las caras se dividen en dos triángulos orientados hacia fuera; sus vértices se
comparten por coordenada entera de esquina. Contactos por arista o esquina
pueden ser no-manifold: se conservan como parte de la máscara discreta.
"""
import json

import numpy as np

from .geometry import GeometryError, Stack, build_stack, native_volume_cm3


MAX_VOLUME_VOXELS = 64_000_000
MAX_QUAD_FACES = 120_000
MAX_VERTICES = 200_000
MAX_TRIANGLES = 240_000
MAX_JSON_BYTES = 24*1024*1024
POSITION_TOLERANCE_MM = .01

# Esquinas XYZ de cada cara: winding antihorario visto desde fuera.
FACE_CORNERS = {
    (2, -1): ((0, 0, 0), (0, 0, 1), (0, 1, 1), (0, 1, 0)),
    (2, 1): ((1, 0, 0), (1, 1, 0), (1, 1, 1), (1, 0, 1)),
    (1, -1): ((0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1)),
    (1, 1): ((0, 1, 0), (0, 1, 1), (1, 1, 1), (1, 1, 0)),
    (0, -1): ((0, 0, 0), (0, 1, 0), (1, 1, 0), (1, 0, 0)),
    (0, 1): ((0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)),
}


def _geometry(mask, stack):
    if (not isinstance(mask, np.ndarray) or mask.dtype != np.bool_ or mask.ndim != 3
            or not all(mask.shape) or mask.size > MAX_VOLUME_VOXELS):
        raise GeometryError('Surface requires a non-empty 3D boolean array of at most 64 million voxels.')
    if not isinstance(stack, Stack) or not stack.slices:
        raise GeometryError('Surface requires a verified DICOM stack.')
    try:
        checked = build_stack(list(stack.slices))
    except (ValueError, TypeError, OverflowError) as error:
        raise GeometryError('Surface stack geometry is invalid.') from error
    if (stack.issue or checked.issue or stack.interval_mm is None or checked.interval_mm is None
            or isinstance(stack.interval_mm, (bool, np.bool_))
            or not np.isfinite(stack.interval_mm) or stack.interval_mm <= 0
            or not np.isclose(stack.interval_mm, checked.interval_mm, atol=1e-4, rtol=0)
            or [s.sop for s in checked.slices] != [s.sop for s in stack.slices]
            or len({s.sop for s in stack.slices}) != len(stack.slices)
            or any(not s.sop for s in stack.slices)):
        raise GeometryError('Surface requires ordered, unique slices with uniform positive spacing.')
    first = stack.slices[0]
    if mask.shape != (len(stack.slices), *first.shape):
        raise GeometryError('Surface mask shape differs from the native DICOM stack.')
    spacing = np.asarray((stack.interval_mm, *first.spacing), dtype=np.float64)
    with np.errstate(over='ignore', invalid='ignore'):
        volume = float(np.prod(spacing)*int(np.count_nonzero(mask))/1000)
    if not np.isfinite(volume):
        raise GeometryError('Native volume exceeds the supported numeric range.')
    row, column = np.asarray(first.orientation, dtype=np.float64).reshape(2, 3)
    direction = np.column_stack((row, column, np.cross(row, column)))
    origin = np.asarray(first.position, dtype=np.float64)
    expected = origin + np.arange(len(stack.slices))[:, None]*spacing[0]*direction[:, 2]
    actual = np.asarray([s.position for s in stack.slices], dtype=np.float64)
    if not np.isfinite(expected).all() or np.any(np.linalg.norm(actual-expected, axis=1) > POSITION_TOLERANCE_MM):
        raise GeometryError('Surface does not support shifted or nonuniform planes; no resampling is performed.')
    return spacing, direction, origin


def _mask_bounds(mask):
    low, high = [], []
    for axis in range(3):
        occupied = np.flatnonzero(mask.any(axis=tuple(a for a in range(3) if a != axis)))
        low.append(int(occupied[0])); high.append(int(occupied[-1])+1)
    return low, high


def _face_count(mask, voxels):
    """Cuenta antes de crear coordenadas para rechazar superficies excesivas."""
    count = 0
    for axis in range(3):
        low = tuple(slice(None, -1) if a == axis else slice(None) for a in range(3))
        high = tuple(slice(1, None) if a == axis else slice(None) for a in range(3))
        count += 2*(voxels-int(np.count_nonzero(mask[low] & mask[high])))
    if count > MAX_QUAD_FACES or 2*count > MAX_TRIANGLES:
        raise GeometryError('Voxel surface exceeds the exact-face limit; the mask was not simplified.')
    return count


def _corner_mesh(mask, offset):
    """Usa un solo buffer booleano y comparte esquinas sin redondear LPS."""
    boundary = np.empty(mask.shape, dtype=bool)
    faces = []
    for (axis, sign), offsets_xyz in FACE_CORNERS.items():
        edge = [slice(None)]*3
        edge[axis] = 0 if sign < 0 else -1
        boundary[tuple(edge)] = mask[tuple(edge)]
        here, neighbor = [slice(None)]*3, [slice(None)]*3
        here[axis] = slice(1, None) if sign < 0 else slice(None, -1)
        neighbor[axis] = slice(None, -1) if sign < 0 else slice(1, None)
        # bool A > bool B equivale a A & ~B sin otro buffer de inversión.
        np.greater(mask[tuple(here)], mask[tuple(neighbor)], out=boundary[tuple(here)])
        cells_zyx = np.column_stack(np.nonzero(boundary)).astype(np.int32)
        if len(cells_zyx):
            cells_xyz = (cells_zyx+offset)[:, ::-1]
            faces.append(cells_xyz[:, None, :]+np.asarray(offsets_xyz, dtype=np.int32)[None])
    corners = np.concatenate(faces)
    vertices, inverse = np.unique(corners.reshape(-1, 3), axis=0, return_inverse=True)
    if len(vertices) > MAX_VERTICES:
        raise GeometryError('Voxel surface exceeds the vertex limit; no components were removed.')
    quads = inverse.reshape(-1, 4)
    triangles = np.concatenate((quads[:, [0, 1, 2]], quads[:, [0, 2, 3]]))
    return vertices, triangles


def voxel_surface(mask_bool_zyx, stack):
    """Retorna una malla JSON LPS en mm de todos los vóxeles foreground.

    Vacío devuelve status='empty_mask', sin vértices, triángulos ni bounds.
    El límite de adquisición se cierra con caras de vóxel y se declara en
    acquisition_touch_faces: no representa una frontera anatómica verificada.
    El volumen descriptivo sale del conteo y espaciado nativo, no de la malla.

    Límites explícitos: 64M vóxeles, 120k caras, 200k vértices, 240k triángulos
    y 24 MiB de JSON del helper. No hay reducción automática de resolución.
    Se exige una grilla ortogonal con centros a <=0.01 mm de sus posiciones
    uniformes; un desplazamiento lateral o separación incompatible falla.
    """
    spacing, direction, origin = _geometry(mask_bool_zyx, stack)
    mask = mask_bool_zyx
    voxels = int(np.count_nonzero(mask))
    result = {
        'status': 'surface' if voxels else 'empty_mask', 'coordinate_system': 'LPS', 'units': 'mm',
        'representation': 'exposed_voxel_faces', 'all_components_retained': True,
        'smoothing': False, 'decimation': False,
        'vertices_lps_mm': [], 'triangles': [], 'bounds_lps_mm': None, 'center_lps_mm': None,
        'shape_zyx': list(mask.shape), 'spacing_zyx_mm': spacing.tolist(),
        'origin_lps_mm': origin.tolist(), 'direction_lps': direction.tolist(),
        'mask_bounds_zyx_halfopen': None,
        'acquisition_touch_faces': [name for name, plane in (
            ('z_min', mask[0]), ('z_max', mask[-1]), ('y_min', mask[:, 0]),
            ('y_max', mask[:, -1]), ('x_min', mask[:, :, 0]), ('x_max', mask[:, :, -1])) if plane.any()],
        'stats': {'voxels': voxels, 'quad_faces': 0, 'vertices': 0, 'triangles': 0,
                  'native_volume_cm3': float(native_volume_cm3(voxels, tuple(spacing[1:]), spacing[0]))},
        'limits': {'volume_voxels': MAX_VOLUME_VOXELS, 'quad_faces': MAX_QUAD_FACES,
                   'vertices': MAX_VERTICES, 'triangles': MAX_TRIANGLES, 'json_bytes': MAX_JSON_BYTES},
        'scope': 'Native candidate geometry only; not an anatomical reference or treatment-response measure.',
    }
    if voxels:
        low, high = _mask_bounds(mask)
        cropped = mask[tuple(slice(a, b) for a, b in zip(low, high))]
        count = _face_count(cropped, voxels)
        corner_xyz, triangles = _corner_mesh(cropped, np.asarray(low, dtype=np.int32))
        vertices = origin + ((corner_xyz.astype(np.float64)-.5)*spacing[::-1]) @ direction.T
        if not np.isfinite(vertices).all():
            raise GeometryError('Physical surface coordinates exceed the supported numeric range.')
        minimum, maximum = vertices.min(axis=0), vertices.max(axis=0)
        result.update(vertices_lps_mm=vertices.tolist(), triangles=triangles.tolist(),
                      bounds_lps_mm=[minimum.tolist(), maximum.tolist()],
                      center_lps_mm=(minimum*.5+maximum*.5).tolist(), mask_bounds_zyx_halfopen=[low, high])
        result['stats'].update(quad_faces=count, vertices=len(vertices), triangles=len(triangles))
    if len(json.dumps(result, separators=(',', ':'), allow_nan=False).encode()) > MAX_JSON_BYTES:
        raise GeometryError('Exact surface JSON exceeds the response budget; no simplification was applied.')
    return result
