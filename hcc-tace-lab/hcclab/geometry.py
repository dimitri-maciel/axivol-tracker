"""Physical geometry rules. No I/O, browser state or dataset-specific labels."""
from dataclasses import dataclass
from pathlib import Path

import numpy as np


class GeometryError(ValueError):
    """A required physical relationship cannot be established."""


@dataclass(frozen=True)
class Slice:
    path: Path
    sop: str
    acquisition: str
    frame: str
    shape: tuple[int, int]
    spacing: tuple[float, float]
    orientation: tuple[float, ...]
    position: tuple[float, ...]


@dataclass(frozen=True)
class Stack:
    slices: tuple[Slice, ...]
    interval_mm: float | None
    issue: str | None = None


def build_stack(slices: list[Slice]) -> Stack:
    if not slices:
        raise GeometryError('No hay cortes DICOM utilizables.')
    first = slices[0]
    row, col = np.asarray(first.orientation, dtype=float).reshape(2, 3)
    if not (np.isfinite([*row, *col]).all() and abs(row @ col) < 1e-4
            and abs(np.linalg.norm(row)-1) < 1e-4 and abs(np.linalg.norm(col)-1) < 1e-4):
        raise GeometryError('La orientación DICOM no define dos ejes ortonormales.')
    if not first.frame or min(first.shape) <= 0 or not np.isfinite(first.spacing).all() or min(first.spacing) <= 0:
        raise GeometryError('La geometría física del corte es incompleta.')
    normal = np.cross(row, col)
    for s in slices:
        if (s.frame != first.frame or s.shape != first.shape or s.acquisition != first.acquisition
                or not np.allclose(s.spacing, first.spacing, atol=1e-4, rtol=0)
                or not np.allclose(s.orientation, first.orientation, atol=1e-4, rtol=0)
                or not np.isfinite(s.position).all()):
            raise GeometryError('La adquisición mezcla orientaciones, dimensiones o referencias espaciales.')
    ordered = tuple(sorted(slices, key=lambda s: float(np.dot(s.position, normal))))
    if len(ordered) == 1:
        return Stack(ordered, None, 'Un solo corte: no se puede verificar el espaciado axial.')
    gaps = np.diff([np.dot(s.position, normal) for s in ordered])
    if np.min(gaps) <= .01:
        return Stack(ordered, None, 'Hay posiciones duplicadas; la serie puede mezclar adquisiciones.')
    interval = float(np.median(gaps))
    if not np.allclose(gaps, interval, atol=.05, rtol=0):
        return Stack(ordered, None, 'El espaciado axial es irregular.')
    return Stack(ordered, interval)


def native_volume_cm3(count: int, spacing: tuple[float, float], interval: float | None) -> float:
    if interval is None or not np.isfinite([*spacing, interval]).all() or min(*spacing, interval) <= 0 or count < 0:
        raise GeometryError('No hay geometría uniforme suficiente para calcular volumen.')
    return count * spacing[0] * spacing[1] * interval / 1000
