"""Carga cuantitativa de CT clásico en su grilla original, sin ventana/nivel.

Cada plano usa HU = RescaleSlope * valor_almacenado + RescaleIntercept.
La polaridad de presentación y la ventana no forman parte de esa ecuación.
No se interpola, registra ni reutiliza ninguna máscara PRE.

El DICOM CT Image Module permite omitir RescaleType cuando las unidades son
HU. Aquí se acepta esa omisión solo para CT ORIGINAL, no LOCALIZER y sin
adquisición multi-energy. Fuente normativa:
https://dicom.nema.org/medical/dicom/current/output/chtml/part03/sect_C.8.2.html
"""
from math import prod

import numpy as np
from pydicom import dcmread
from pydicom.filereader import read_file_meta_info
from pydicom.pixels import pixel_array
from pydicom.uid import CTImageStorage, DeflatedExplicitVRLittleEndian

from .geometry import GeometryError, build_stack


MAX_VOLUME_VOXELS = 64_000_000
MAX_SLICE_PIXELS = 1_048_576
MAX_SLICES = 2048
MAX_FILE_BYTES = 32 * 1024 * 1024


def _check_stack(stack):
    """Comprueba la retícula y rechaza desplazamiento lateral entre planos."""
    if not stack.slices or len(stack.slices) > MAX_SLICES:
        raise GeometryError('CT stack is empty or exceeds the slice-count limit.')
    checked = build_stack(list(stack.slices))
    if (checked.issue or checked.interval_mm is None or stack.issue
            or stack.interval_mm is None or not np.isfinite(stack.interval_mm)
            or not np.isclose(stack.interval_mm, checked.interval_mm, atol=1e-4, rtol=0)):
        raise GeometryError('A regular CT stack is required for 3D segmentation.')
    if [s.sop for s in checked.slices] != [s.sop for s in stack.slices]:
        raise GeometryError('CT slices must already be ordered by physical position.')
    if len({s.sop for s in stack.slices}) != len(stack.slices):
        raise GeometryError('CT stack contains duplicate SOP instance references.')
    first = stack.slices[0]
    if prod(first.shape) > MAX_SLICE_PIXELS or len(stack.slices)*prod(first.shape) > MAX_VOLUME_VOXELS:
        raise GeometryError('CT stack exceeds the bounded volume size.')
    axes = np.asarray(first.orientation).reshape(2, 3)
    normal = np.cross(*axes)
    for s in stack.slices:
        displacement = np.asarray(s.position)-np.asarray(first.position)
        lateral = displacement-np.dot(displacement, normal)*normal
        if np.linalg.norm(lateral) > .01:
            raise GeometryError('Laterally shifted CT planes require resampling, which is not supported.')


def _check_header(ds, s):
    """Verifica identidad, geometría y semántica HU antes de decodificar."""
    if (ds.get('Modality') != 'CT' or ds.get('SOPClassUID') != CTImageStorage
            or int(ds.get('NumberOfFrames', 1)) != 1
            or 'SharedFunctionalGroupsSequence' in ds or 'PerFrameFunctionalGroupsSequence' in ds):
        raise GeometryError('Only classic single-frame CT images are supported for segmentation.')
    if (str(ds.get('SOPInstanceUID', '')) != s.sop
            or str(ds.get('FrameOfReferenceUID', '')) != s.frame
            or str(ds.get('AcquisitionNumber', 'Unspecified')) != s.acquisition
            or (int(ds.get('Rows', 0)), int(ds.get('Columns', 0))) != s.shape):
        raise GeometryError('CT source identity or dimensions changed after indexing.')
    for name, expected in [('ImagePositionPatient', s.position),
                           ('ImageOrientationPatient', s.orientation), ('PixelSpacing', s.spacing)]:
        actual = np.asarray(ds.get(name, []), dtype=float)
        if actual.shape != (len(expected),) or not np.allclose(actual, expected, atol=1e-4, rtol=0):
            raise GeometryError('CT source geometry changed after indexing.')
    if (int(ds.get('SamplesPerPixel', 0)) != 1
            or ds.get('PhotometricInterpretation') != 'MONOCHROME2'
            or int(ds.get('BitsAllocated', 0)) != 16
            or not 1 <= int(ds.get('BitsStored', 0)) <= 16
            or int(ds.get('PixelRepresentation', -1)) not in (0, 1)):
        raise GeometryError('Segmentation requires single-channel, 16-bit classic CT pixels.')
    if 'ModalityLUTSequence' in ds or 'RealWorldValueMappingSequence' in ds:
        raise GeometryError('This CT uses an unsupported quantitative pixel transform.')
    image_type = list(ds.get('ImageType', []))
    multi_energy = ds.get((0x0018, 0x9361))
    multi_energy = str(multi_energy.value).strip().upper() if multi_energy else ''
    localizer = len(image_type) > 2 and str(image_type[2]).upper() == 'LOCALIZER'
    if localizer or multi_energy not in ('', 'NO'):
        raise GeometryError('Localizer and multi-energy CT are not supported by this baseline.')
    units = str(ds.get('RescaleType', '')).strip().upper()
    implicit_hu = len(image_type) >= 3 and str(image_type[0]).upper() == 'ORIGINAL'
    if units != 'HU' and not (not units and implicit_hu):
        raise GeometryError('The CT output units cannot be established as Hounsfield units.')
    try:
        slope = float(ds.RescaleSlope)
        intercept = float(ds.RescaleIntercept)
    except (AttributeError, TypeError, ValueError) as error:
        raise GeometryError('CT rescale slope and intercept are required.') from error
    if not np.isfinite([slope, intercept]).all() or slope == 0:
        raise GeometryError('CT rescale parameters must be finite with a nonzero slope.')
    return slope, intercept


def _padding_bound(value, bits_stored, signed):
    """Interpreta un patrón unsigned heredado cuando los píxeles son signed.

    HCC_002 almacena 63536 como padding con píxeles signed de 16 bits. El
    patrón de bits representa 63536 - 2**16 = -2000. Solo se transforma un
    valor fuera del rango signed pero dentro del rango de bits almacenados;
    un valor que ya es signed se conserva, sin aplicar dos veces el ajuste.
    """
    result = int(value)
    if signed and 2**(bits_stored-1) <= result < 2**bits_stored:
        result -= 2**bits_stored
    low, high = (-2**(bits_stored-1), 2**(bits_stored-1)-1) if signed else (0, 2**bits_stored-1)
    if not low <= result <= high:
        raise GeometryError('Pixel padding is outside the stored pixel range.')
    return result


def _valid_pixels(raw, ds):
    """Excluye padding en valores almacenados, antes del rescale a HU."""
    if 'PixelPaddingValue' not in ds:
        if 'PixelPaddingRangeLimit' in ds:
            raise GeometryError('PixelPaddingRangeLimit is present without PixelPaddingValue.')
        return np.ones(raw.shape, dtype=bool)
    bits = int(ds.BitsStored)
    signed = bool(int(ds.PixelRepresentation))
    start = _padding_bound(ds.PixelPaddingValue, bits, signed)
    end = _padding_bound(ds.get('PixelPaddingRangeLimit', ds.PixelPaddingValue), bits, signed)
    low, high = sorted((start, end))
    return (raw < low) | (raw > high)


def load_hu_volume(stack, *, return_validity=False,
                   expected_series_uid=None, expected_study_uid=None):
    """Lee un ``Stack`` como float32 [z, y, x] en orden de posición física.

    Con ``return_validity=True`` devuelve ``(hu, valid_mask)``; esta es la
    ruta que debe usar la segmentación para excluir padding. El modo simple
    conserva todos los valores reescalados, incluido el padding, y sirve
    únicamente a consumidores que gestionen esa validez por separado.

    Se permite un máximo de 64 millones de vóxeles (256 MB float32), 2048
    cortes, 1,048,576 píxeles por corte y 32 MiB por archivo de entrada. Son
    límites de las matrices/archivos, no una garantía sobre memoria interna
    de codecs. Deflated DICOM se rechaza antes de leer el dataset completo.
    Todas las referencias, geometría y transformaciones se comprueban contra
    los archivos actuales; no se confía solo en metadatos indexados antes.
    Los UID esperados, si se proporcionan, vinculan cada archivo al estudio
    y la serie seleccionados en el catálogo, no solo a sus planos vecinos.
    """
    _check_stack(stack)
    shape = (len(stack.slices), *stack.slices[0].shape)
    hu = np.empty(shape, dtype=np.float32)
    validity = np.empty(shape, dtype=bool) if return_validity else None
    study_series = None
    for z, s in enumerate(stack.slices):
        if not 0 < s.path.stat().st_size <= MAX_FILE_BYTES:
            raise GeometryError('CT file is empty or exceeds the input-file size limit.')
        meta = read_file_meta_info(s.path)
        if meta.get('TransferSyntaxUID') == DeflatedExplicitVRLittleEndian:
            raise GeometryError('Deflated DICOM is not supported by the bounded CT loader.')
        ds = dcmread(s.path)
        slope, intercept = _check_header(ds, s)
        identity = (str(ds.get('StudyInstanceUID', '')), str(ds.get('SeriesInstanceUID', '')))
        if not all(identity) or (study_series is not None and identity != study_series):
            raise GeometryError('CT stack contains missing or mixed study/series references.')
        if ((expected_study_uid is not None and identity[0] != expected_study_uid)
                or (expected_series_uid is not None and identity[1] != expected_series_uid)):
            raise GeometryError('CT study or series references do not match the selected catalog entry.')
        study_series = identity
        raw = pixel_array(ds)
        if raw.shape != s.shape or raw.dtype.kind not in 'iu':
            raise GeometryError('Decoded CT pixels do not match the indexed integer image.')
        valid = _valid_pixels(raw, ds)
        # Rescale en float64 para detectar overflow antes de convertir a float32.
        scaled = raw.astype(np.float64)*slope+intercept
        if not np.isfinite(scaled).all() or np.max(np.abs(scaled)) > np.finfo(np.float32).max:
            raise GeometryError('Rescaled CT values cannot be represented as finite float32.')
        hu[z] = scaled
        if validity is not None:
            validity[z] = valid
    return (hu, validity) if return_validity else hu
