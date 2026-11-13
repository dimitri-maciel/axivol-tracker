"""Physical XY input context for POST inference; all native slices are retained.

This selects the image field seen by the network, not a box used to clip an
already predicted mask. The unassessed exterior is recorded in diagnostics.
"""
import numpy as np


def context_bounds(shape, location, spacing_zyx, minimum_mm):
    """Retorna límites XY semiabiertos para contexto físico cuadrado.

    Lado = max(mínimo en mm, 2 × lado mayor de la caja localizada en mm).
    Cada eje se convierte con su propio espaciado. Si cabe, se desplaza el
    campo hacia dentro de la imagen sin reducirlo. No modifica el eje z.
    """
    if (len(shape) != 3 or any(isinstance(v, (bool, np.bool_)) or not isinstance(v, (int, np.integer))
                              or v <= 0 for v in shape)):
        raise ValueError('Native shape must contain three positive integer dimensions.')
    spacing = np.asarray(spacing_zyx, dtype=float)
    if spacing.shape != (3,) or not np.isfinite(spacing).all() or np.any(spacing <= 0):
        raise ValueError('Spacing must contain three positive finite values.')
    if isinstance(minimum_mm, bool) or minimum_mm not in (0, 160, 240):
        raise ValueError('Use a declared context variant: full, 160 mm or 240 mm.')
    raw_bounds = np.asarray(location['localized_box_zyx'], dtype=object)
    if (raw_bounds.shape != (2, 3) or any(isinstance(v, (bool, np.bool_))
            or not isinstance(v, (int, np.integer)) for v in raw_bounds.flat)):
        raise ValueError('Localization bounds must contain integer indices, not booleans.')
    bounds = raw_bounds.astype(np.int64)
    center = np.asarray(location['post_center_zyx'], dtype=float)
    if (bounds.shape != (2, 3) or bounds.dtype.kind not in 'iu' or bounds.dtype.kind == 'b'
            or np.any(bounds[0] < 0) or np.any(bounds[1] > shape)
            or np.any(bounds[1] <= bounds[0]) or center.shape != (3,)
            or not np.isfinite(center).all() or np.any(center < bounds[0]) or np.any(center >= bounds[1])):
        raise ValueError('Localization must be a valid integer box and contained center.')
    if minimum_mm == 0:
        return np.array([0, 0]), np.array(shape[1:])
    side_mm = max(minimum_mm, 2 * float(np.max((bounds[1, 1:] - bounds[0, 1:]) * spacing[1:])))
    size = np.minimum(np.ceil(side_mm / spacing[1:]).astype(int), shape[1:])
    low = np.floor(center[1:] - (size - 1) / 2).astype(int)
    low = np.maximum(0, np.minimum(low, np.asarray(shape[1:]) - size))
    if np.any(low > bounds[0, 1:]) or np.any(low + size < bounds[1, 1:]):
        raise ValueError('The inference field must contain the entire localized box.')
    return low, low + size

