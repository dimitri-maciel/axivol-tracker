"""Acuerdo de una candidata POST congelada con una referencia experta suministrada.

No crea referencias, ejecuta modelos ni acredita credenciales. La independencia
y experiencia proceden de una declaración del lector; los hashes solo verifican
los archivos declarados. Una práctica propia o una referencia incierta no habilita
métricas. El resultado es acuerdo por caso/lesión, no exactitud poblacional,
validación externa ni respuesta terapéutica.

HD95 = max(P95(d(S_P,S_G)), P95(d(S_G,S_P))). Las superficies son centros de
vóxeles de borde 6-conexo, incluidos huecos; se usa scipy.ndimage EDT con
sampling=(dz,dy,dx), sin ponderación por área, y percentil lineal. No equivale
al percentil de distancias agrupadas ni a distancia entre mallas continuas.
https://docs.scipy.org/doc/scipy/reference/generated/scipy.ndimage.distance_transform_edt.html
"""
import argparse
from datetime import datetime, timedelta
from importlib.metadata import version
import json
import math
from pathlib import Path
import re
import sys

import numpy as np
from scipy import ndimage
import SimpleITK as sitk

from hcclab.candidates import file_sha256


MAX_VOXELS = 64_000_000
MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_JSON_BYTES = 1024 * 1024
GEOMETRY_ATOL = 1e-6  # mm para origen/spacing; adimensional para dirección; rtol=0.
TARGET = 'whole_treated_lesion'


class EvaluationError(ValueError):
    """El contrato o la independencia declarada no permiten calcular acuerdo."""


def _require(condition, message):
    if not condition:
        raise EvaluationError(message)


def _text(value, field):
    _require(isinstance(value, str) and bool(value.strip()) and len(value) <= 2000,
             f'{field} must be completed with a non-empty string.')
    return value


def _sha(value, field):
    _require(isinstance(value, str) and re.fullmatch(r'[a-f0-9]{64}', value) is not None,
             f'{field} must contain a lowercase SHA-256 digest.')
    return value


def _json(path):
    _require(path.is_file() and path.stat().st_size <= MAX_JSON_BYTES,
             'JSON input is missing or exceeds one MiB.')

    def unique(pairs):
        result = {}
        for key, value in pairs:
            _require(key not in result, 'JSON input contains a duplicate key.')
            result[key] = value
        return result

    try:
        result = json.loads(path.read_text(), object_pairs_hook=unique,
                            parse_constant=lambda _: (_ for _ in ()).throw(EvaluationError('JSON contains non-finite values.')))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise EvaluationError('Input is not a readable JSON object.') from error
    _require(isinstance(result, dict), 'JSON input must be an object.')
    return result


def _package_path(folder, value):
    _text(value, 'Package path')
    path = Path(value)
    _require(not path.is_absolute() and '\\' not in value and '..' not in path.parts,
             'Package paths must be relative and confined to the manifest directory.')
    resolved = (folder / path).resolve()
    _require(folder in resolved.parents and resolved.is_file(),
             'Package file is missing or escapes the manifest directory.')
    return resolved


def _numbers(value, shape, name):
    raw = np.asarray(value, dtype=object)
    _require(raw.shape == shape and all(isinstance(v, (int, float, np.integer, np.floating))
             and not isinstance(v, (bool, np.bool_)) for v in raw.flat),
             f'{name} must contain numeric values with shape {shape}.')
    array = raw.astype(float)
    _require(np.isfinite(array).all(), f'{name} must be finite.')
    return array


def _geometry(document):
    _require(isinstance(document, dict), 'Native geometry is missing.')
    size = document.get('size_xyz')
    _require(isinstance(size, list) and len(size) == 3
             and all(type(v) is int and v > 0 for v in size) and math.prod(size) <= MAX_VOXELS,
             'Native size must contain three positive integers, at most 64 million voxels.')
    spacing = _numbers(document.get('spacing_xyz_mm'), (3,), 'Native spacing')
    origin = _numbers(document.get('origin_lps_mm'), (3,), 'Native origin')
    direction = _numbers(document.get('direction_lps'), (9,), 'Native direction').reshape(3, 3)
    _require((spacing > 0).all(), 'Native spacing must be positive.')
    _require(np.allclose(direction.T @ direction, np.eye(3), atol=GEOMETRY_ATOL, rtol=0),
             'Native direction must describe orthonormal image axes; no shear is supported.')
    return tuple(size), spacing, origin, direction.ravel()


def _same_geometry(actual, expected):
    _require(actual[0] == expected[0], 'Image size differs from the frozen native POST grid.')
    for measured, frozen in zip(actual[1:], expected[1:]):
        _require(np.allclose(measured, frozen, atol=GEOMETRY_ATOL, rtol=0),
                 'Image spacing, origin or direction differs from the frozen native POST grid; no resampling is performed.')


def _self_contained_nrrd(path):
    _require(path.is_file() and path.suffix.lower() == '.nrrd'
             and 0 < path.stat().st_size <= MAX_FILE_BYTES, 'Expected a bounded, self-contained .nrrd file.')
    with path.open('rb') as source:
        header = source.read(65536)
    lines = re.split(br'\r?\n', header)
    _require(lines[0].startswith(b'NRRD000') and b'' in lines,
             'NRRD header is invalid or exceeds 64 KiB.')
    for line in lines[1:lines.index(b'')]:
        field, separator, value = line.partition(b':')
        if separator and not field.lstrip().startswith(b'#'):
            name = field.strip().lower().replace(b' ', b'')
            _require(name != b'datafile', 'Detached NRRD payloads are not accepted; their bytes are not covered by the file hash.')
            if name == b'spaceunits':
                _require(value.strip().replace(b'"', b'').split() == [b'mm'] * 3,
                         'NRRD physical units must be millimeters.')


def _binary_nrrd(path, expected_geometry):
    """Lee únicamente retícula escalar 3D nativa; jamás remuestrea ni umbraliza."""
    _self_contained_nrrd(path)
    reader = sitk.ImageFileReader()
    reader.SetFileName(str(path))
    reader.SetImageIO('NrrdImageIO')
    scalar_types = {sitk.sitkInt8, sitk.sitkUInt8, sitk.sitkInt16, sitk.sitkUInt16,
                    sitk.sitkInt32, sitk.sitkUInt32, sitk.sitkInt64, sitk.sitkUInt64,
                    sitk.sitkFloat32, sitk.sitkFloat64}
    try:
        reader.ReadImageInformation()
        _require(reader.GetDimension() == 3 and reader.GetNumberOfComponents() == 1
                 and reader.GetPixelID() in scalar_types,
                 'Mask must be a scalar three-dimensional NRRD, not a layered segmentation.')
        actual = (tuple(reader.GetSize()), reader.GetSpacing(), reader.GetOrigin(), reader.GetDirection())
        _same_geometry(actual, expected_geometry)
        image = reader.Execute()
    except RuntimeError as error:
        raise EvaluationError('NRRD image could not be read.') from error
    array = sitk.GetArrayViewFromImage(image)
    _require(array.ndim == 3 and all(np.isfinite(plane).all()
             and np.logical_or(plane == 0, plane == 1).all() for plane in array),
             'Mask must be finite and binary with values exactly 0 or 1.')
    return array.astype(bool)  # Copia independiente antes de liberar la imagen SimpleITK.


def _identity(document, manifest):
    for key in ('package_id', 'case_id', 'target_definition'):
        _require(document.get(key) == manifest[key], f'{key} differs from the frozen package.')


def _check_form(form, manifest, reference_path, reference_hash):
    _identity(form, manifest)
    for key, expected in (('post_series_uid', manifest['post']['series_uid']),
                          ('post_acquisition', manifest['post']['acquisition']),
                          ('reference_ct_sha256', manifest['post']['ct_sha256'])):
        _require(form.get(key) == expected, f'{key} differs from the frozen POST source.')
    for key in ('independently_drawn', 'hepatic_imaging_expertise_attested',
                'target_confirmed', 'lesion_identity_confirmed'):
        _require(form.get(key) is True, f'{key} must be explicitly attested true by the expert reader.')
    _require(form.get('model_output_seen') is False,
             'The reader must explicitly attest not having seen model output before the reference was completed.')
    _text(form.get('reader_id'), 'reader_id')
    _text(form.get('qualifications'), 'qualifications')
    _require(form.get('evaluability') in ('delineable', 'absence_confirmed'),
             'Reference is uncertain or not evaluable; no contour agreement may be computed.')
    annotation = _text(form.get('annotation_file'), 'annotation_file')
    _require(annotation == reference_path.name and '/' not in annotation and '\\' not in annotation,
             'annotation_file must be the basename of the supplied manual reference.')
    _require(_sha(form.get('annotation_sha256'), 'annotation_sha256') == reference_hash,
             'The reference bytes differ from the reader-attested annotation.')
    try:
        reviewed = datetime.fromisoformat(_text(form.get('reviewed_utc'), 'reviewed_utc').replace('Z', '+00:00'))
        _require(reviewed.tzinfo is not None and reviewed.utcoffset() == timedelta(0),
                 'reviewed_utc must include an explicit UTC timezone.')
    except ValueError as error:
        raise EvaluationError('reviewed_utc must be a valid ISO-8601 UTC timestamp.') from error


def _surface_hd95(candidate, reference, spacing):
    """Máximo P95 dirigido de superficies voxel-center; EDT exacto de SciPy."""
    union = candidate | reference
    bounds = []
    for axis in range(3):
        indices = np.flatnonzero(np.any(union, axis=tuple(a for a in range(3) if a != axis)))
        bounds.append(slice(int(indices[0]), int(indices[-1])+1))
    crop = tuple(bounds)
    structure = ndimage.generate_binary_structure(3, 1)
    surfaces = [mask[crop] & ~ndimage.binary_erosion(mask[crop], structure=structure, border_value=0)
                for mask in (candidate, reference)]
    directed = []
    for source, target in (surfaces, surfaces[::-1]):
        distance = ndimage.distance_transform_edt(~target, sampling=spacing)
        values = distance[source]
        _require(np.isfinite(values).all(), 'Physical spacing exceeds the finite boundary-distance range.')
        directed.append(float(np.percentile(values, 95, method='linear')))
        del distance
    return max(directed), directed


def compare_masks(candidate, reference, spacing_zyx_mm):
    """Métricas geométricas puras; el llamador debe verificar independencia.

    V = N*dz*dy*dx/1000 cm³. El error relativo absoluto solo se define con
    referencia no vacía. Si ambas están vacías, Dice/HD95 de contorno son null:
    se informa concordancia de ausencia sin añadir un Dice=1 al resumen de
    lesiones delineables. Con una vacía, Dice=0 y HD95=null (sin superficie).
    """
    _require(isinstance(candidate, np.ndarray) and isinstance(reference, np.ndarray)
             and candidate.dtype == np.bool_ and reference.dtype == np.bool_
             and candidate.ndim == 3 and candidate.shape == reference.shape
             and candidate.size <= MAX_VOXELS and min(candidate.shape) > 0,
             'Metrics require matching bounded three-dimensional boolean arrays.')
    spacing = _numbers(spacing_zyx_mm, (3,), 'Metric spacing')
    _require((spacing > 0).all(), 'Metric spacing must be positive.')
    a, b = int(candidate.sum()), int(reference.sum())
    intersection = int(np.count_nonzero(candidate & reference))
    voxel_cm3 = math.prod(float(value) for value in spacing)/1000
    va, vb = a*voxel_cm3, b*voxel_cm3
    _require(voxel_cm3 > 0 and all(math.isfinite(v) for v in (voxel_cm3, va, vb)),
             'Physical spacing exceeds the finite volume range.')
    result = {'candidate_voxels': a, 'reference_voxels': b, 'intersection_voxels': intersection,
              'dice': 2*intersection/(a+b) if a+b else None, 'hd95_mm': None,
              'candidate_volume_cm3': va, 'reference_volume_cm3': vb,
              'signed_volume_error_cm3': va-vb, 'absolute_volume_error_cm3': abs(va-vb),
              'absolute_relative_volume_error_percent': 100*abs(a-b)/b if b else None,
              'presence_agreement': bool(a) == bool(b)}
    if a and b:
        result['hd95_mm'], directed = _surface_hd95(candidate, reference, spacing)
        result['directed_p95_mm'] = {'candidate_to_reference': directed[0], 'reference_to_candidate': directed[1]}
        result['contour_status'] = 'both_nonempty'
    else:
        result['contour_status'] = 'both_empty' if not a and not b else 'one_empty'
        result['hd95_undefined_reason'] = 'A surface is absent; no finite boundary distance is defined.'
    return result


def evaluate_reference(manifest_path, candidate_path, reference_path, attestation_path):
    """Evalúa solo archivos congelados y una declaración experta completa."""
    paths = [Path(p).resolve() for p in (manifest_path, candidate_path, reference_path, attestation_path)]
    manifest_path, candidate_path, reference_path, attestation_path = paths
    _require(manifest_path.is_file(), 'Frozen evaluation manifest is missing.')
    manifest_hash = file_sha256(manifest_path)
    manifest = _json(manifest_path)
    _require(type(manifest.get('schema_version')) is int and manifest['schema_version'] == 1
             and manifest.get('kind') == 'independent_post_reference_evaluation', 'Unsupported evaluation manifest.')
    for key in ('package_id', 'case_id'):
        _text(manifest.get(key), key)
    _require(manifest.get('target_definition') == TARGET, 'The package does not define the whole treated lesion target.')
    for key in ('post', 'candidate', 'validity'):
        _require(isinstance(manifest.get(key), dict), f'{key} is missing from the frozen package.')
    for key in ('series_uid', 'acquisition'):
        _text(manifest['post'].get(key), 'POST '+key)
    _sha(manifest['post'].get('ct_sha256'), 'POST CT SHA-256')
    _text(manifest['candidate'].get('id'), 'Frozen candidate ID')
    geometry = _geometry(manifest['post'].get('geometry'))
    folder = manifest_path.parent
    frozen_candidate = _package_path(folder, manifest['candidate'].get('path'))
    validity_path = _package_path(folder, manifest['validity'].get('path'))
    case_path = _package_path(folder, 'reader_case.json')
    _require(candidate_path == frozen_candidate, 'The candidate argument must be the frozen package candidate.')
    _require(not candidate_path.samefile(validity_path), 'Candidate and POST validity must be separate package files.')
    _require(reference_path.is_file() and all(not reference_path.samefile(path)
             for path in (candidate_path, validity_path)),
             'A separate manual reference file is required; do not use package masks as reference.')
    expected = {candidate_path: _sha(manifest['candidate'].get('sha256'), 'Candidate SHA-256'),
                validity_path: _sha(manifest['validity'].get('sha256'), 'Validity SHA-256'),
                case_path: _sha(manifest.get('reader_case_sha256'), 'Reader case SHA-256')}
    for path, digest in expected.items():
        _require(file_sha256(path) == digest, 'A frozen package file failed its SHA-256 check.')
    # Preserva también bytes de manifiesto, formulario y referencia durante el cálculo.
    expected[manifest_path] = manifest_hash
    expected.update({path: file_sha256(path) for path in (reference_path, attestation_path)})
    case = _json(case_path)
    _identity(case, manifest)
    _require(isinstance(case.get('post'), dict), 'Reader case POST identity is missing.')
    for key in ('series_uid', 'acquisition', 'ct_sha256'):
        _require(case['post'].get(key) == manifest['post'][key], 'Reader case and evaluator POST source differ.')
    _same_geometry(_geometry(case['post'].get('geometry')), geometry)
    form = _json(attestation_path)
    _check_form(form, manifest, reference_path, expected[reference_path])
    candidate = _binary_nrrd(candidate_path, geometry)
    reference = _binary_nrrd(reference_path, geometry)
    validity = _binary_nrrd(validity_path, geometry)
    _require(validity.any(), 'Frozen POST validity contains no valid CT support.')
    _require(not np.any(candidate & ~validity) and not np.any(reference & ~validity),
             'Candidate or reference contains foreground in invalid POST padding; no clipping is performed.')
    _require(bool(reference.any()) == (form['evaluability'] == 'delineable'),
             'Reference emptiness disagrees with the expert evaluability declaration.')
    metrics = compare_masks(candidate, reference, list(geometry[1][::-1]))
    for path, digest in expected.items():
        _require(file_sha256(path) == digest, 'Input bytes changed during evaluation; no result may be published.')
    return {'schema_version': 1, 'status': 'agreement_with_supplied_expert_reference',
            'package_id': manifest['package_id'], 'case_id': manifest['case_id'],
            'target_definition': TARGET, 'candidate_id': manifest['candidate']['id'],
            'post': manifest['post'], 'metrics': metrics,
            'reader': {'reader_id': form['reader_id'], 'qualifications': form['qualifications'],
                       'reviewed_utc': form['reviewed_utc'], 'evaluability': form['evaluability'],
                       'independence_and_expertise': 'self_reported_attestation_not_credential_verification'},
            'input_sha256': {'manifest': expected[manifest_path], 'candidate': expected[candidate_path],
                             'validity': expected[validity_path], 'reader_case': expected[case_path],
                             'reference': expected[reference_path], 'reader_form': expected[attestation_path]},
            'metric_convention': {'hd95': 'max_of_directed_95th_percentiles', 'percentile_method': 'linear',
                                  'surface': '6_connected_boundary_voxel_centers_including_holes',
                                  'surface_weighting': 'one_sample_per_surface_voxel_not_area_weighted',
                                  'distance': 'scipy_exact_Euclidean_EDT_with_spacing_zyx_mm',
                                  'geometry_absolute_tolerance': GEOMETRY_ATOL, 'geometry_relative_tolerance': 0,
                                  'geometry_units': 'millimeters; direction dimensionless', 'resampling_performed': False,
                                  'both_empty_dice': None, 'empty_surface_hd95': None},
            'software': {name: version(name) for name in ('numpy', 'scipy', 'SimpleITK')},
            'implementation_sha256': file_sha256(Path(__file__)),
            'scope': 'Per-case agreement only. Self-reported expert reference; no credential verification, population accuracy, external validation, or treatment response.'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--reference', type=Path, required=True)
    parser.add_argument('--reader-form', '--attestation', dest='reader_form', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = evaluate_reference(args.manifest, args.candidate, args.reference, args.reader_form)
        _require(not args.output.exists(), 'Output already exists; evaluation will not overwrite any file.')
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x') as target:
            target.write(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
        print(json.dumps({'status': report['status'], 'output': str(args.output), 'metrics': report['metrics']}))
        return 0
    except (EvaluationError, OSError) as error:
        print(json.dumps({'status': 'blocked', 'error': str(error)}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
