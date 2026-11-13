"""Una inferencia POST y sensibilidad del refinamiento de borde a 1, 2 y 3 mm.

Ejemplo: python -m experiments.post_boundary --data /data --output /experiment-output
  --manifest /app/experiments/manifest.example.json --case HCC_006

La banda principal de 2 mm se fija antes de ejecutar; las otras dos describen
sensibilidad, no seleccionan un resultado ganador. PRE Mass y Liver solo
localizan. Logits y máscaras se conservan sin escribir candidatas en la app.
"""
import argparse
from datetime import datetime, timezone
import importlib.metadata
from pathlib import Path
import platform
import sys
import time

import numpy as np

from hcclab.candidates import file_sha256
from hcclab.model_assets import model_path, verify_model
from hcclab.post_boundary import EDGE_SIGMA_HU, SMOOTHNESS_MM, refine_post_boundary
from hcclab.post_model import segment_post_model
from hcclab.registration import localize_pre_region, register_pre_to_post

from .post_tracking import (read_sources, refine_with_liver, safe_output_directory,
                            selection_from_args, verify_unchanged, write_json)
from .tracking_core import compare_predictions, prediction_metrics


BANDS_MM = (1, 2, 3)
PRIMARY_BAND_MM = 2
SCOPE = ('Development sensitivity experiment without independent POST reference. '
         'No anatomical accuracy or treatment-response assessment; no app annotation writes.')


def captured_field(native_mask, valid, diagnostics, intermediates):
    """Comprueba offsets ZYX y el signo de los logits, incluido el corte ancla.

El campo conserva todos los cortes z. No remuestrea ni recorta foreground:
exige que la máscara nativa sea exactamente su inserción en el campo XY.
"""
    if (not isinstance(native_mask, np.ndarray) or native_mask.ndim != 3 or native_mask.dtype != np.bool_
            or not isinstance(valid, np.ndarray) or valid.dtype != np.bool_ or valid.shape != native_mask.shape):
        raise ValueError('Native mask and validity must share a boolean ZYX grid.')
    bounds = np.asarray(diagnostics.get('inference_field_yx'), dtype=object)
    if (bounds.shape != (2, 2) or any(isinstance(x, (bool, np.bool_))
            or not isinstance(x, (int, np.integer)) for x in bounds.flat)):
        raise ValueError('Inference field bounds must contain integer YX indices.')
    low, high = bounds.astype(np.int64)
    if np.any(low < 0) or np.any(high > native_mask.shape[1:]) or np.any(high <= low):
        raise ValueError('Inference field is outside the native POST grid.')
    field = (slice(None), slice(low[0], high[0]), slice(low[1], high[1]))
    shape = (native_mask.shape[0], *(high - low))
    raw, logits = intermediates.get('raw_mask'), intermediates.get('logits')
    if (not isinstance(raw, np.ndarray) or raw.dtype != np.bool_ or raw.shape != shape
            or not isinstance(logits, np.ndarray) or logits.dtype != np.float32 or logits.shape != shape
            or not np.isfinite(logits).all()):
        raise ValueError('Captured mask/logits must match the native inference field and be finite.')
    if (not np.array_equal(raw, native_mask[field]) or np.count_nonzero(raw) != np.count_nonzero(native_mask)
            or not np.array_equal(raw, (logits > 0) & valid[field])):
        raise ValueError('Captured raw mask differs from native output or the original logit threshold.')
    return field, raw, logits


def native_prediction(field_mask, field, valid):
    """Inserta sin interpolar; un foreground inválido se rechaza, no se oculta."""
    if (not isinstance(field_mask, np.ndarray) or field_mask.dtype != np.bool_
            or field_mask.shape != valid[field].shape or np.any(field_mask & ~valid[field])):
        raise ValueError('Refined field must be boolean, native-sized and exclude invalid CT support.')
    result = np.zeros(valid.shape, dtype=bool)
    result[field] = field_mask
    return result


def descriptors(mask, raw, valid, spacing, location, field):
    """Conteos y geometría; el denominador de cambio siempre es el raw original."""
    result = prediction_metrics(mask, location)
    bounds = []
    for axis in range(3):
        occupied = np.flatnonzero(mask.any(axis=tuple(i for i in range(3) if i != axis)))
        bounds.append([int(occupied[0]), int(occupied[-1]) + 1] if len(occupied) else None)
    result['bounds_zyx_halfopen'] = [[b[0] for b in bounds], [b[1] for b in bounds]] if all(bounds) else None
    added, removed = int(np.count_nonzero(mask & ~raw)), int(np.count_nonzero(raw & ~mask))
    raw_count = int(np.count_nonzero(raw))
    result.update(volume_cm3=float(result['voxels'] * np.prod(spacing) / 1000),
                  added_voxels=added, removed_voxels=removed, changed_voxels=added + removed,
                  changed_percent_of_raw=100 * (added + removed) / raw_count if raw_count else None,
                  invalid_padding_voxels=int(np.count_nonzero(mask & ~valid)),
                  outside_inference_field_voxels=result['voxels'] - int(np.count_nonzero(mask[field])),
                  comparison_to_raw=compare_predictions(raw, mask))
    low_y, low_x = field[1].start, field[2].start
    high_y, high_x = field[1].stop, field[2].stop
    result['internal_field_touch_faces'] = [name for name, internal, plane in (
        ('y_min', low_y > 0, mask[:, low_y, low_x:high_x]),
        ('y_max', high_y < mask.shape[1], mask[:, high_y - 1, low_x:high_x]),
        ('x_min', low_x > 0, mask[:, low_y:high_y, low_x]),
        ('x_max', high_x < mask.shape[2], mask[:, low_y:high_y, high_x - 1])) if internal and plane.any()]
    return result


def application_checks(metrics, diagnostics, field, shape):
    """Replica los rechazos espaciales, sin declarar validez anatómica."""
    internal = {name for name, present in (
        ('y_min', field[1].start > 0), ('y_max', field[1].stop < shape[1]),
        ('x_min', field[2].start > 0), ('x_max', field[2].stop < shape[2])) if present}
    band_faces = sorted(internal.intersection(diagnostics['editable_band_touch_faces']))
    reasons = []
    if band_faces:
        reasons.append('incomplete_boundary_context')
    if metrics['internal_field_touch_faces']:
        reasons.append('incomplete_input_field')
    if metrics['majority_outside_roi']:
        reasons.append('localization_propagation_mismatch')
    if not metrics['voxels']:
        reasons.append('empty_prediction')
    return {'status': 'would_abstain' if reasons else 'spatial_checks_passed', 'reasons': reasons,
            'editable_band_internal_field_faces': band_faces,
            'scope': 'Spatial execution checks only; not clinical eligibility or boundary accuracy.'}


def execute(args, selection):
    folder = safe_output_directory(args.output, args.data)
    started = time.perf_counter()
    manifest = {'schema_version': 1, 'kind': 'post_boundary_sensitivity', 'status': 'running',
                'created_utc': datetime.now(timezone.utc).isoformat(), 'case_id': args.case,
                'selection': selection, 'scope': SCOPE, 'primary_band_mm': PRIMARY_BAND_MM,
                'sensitivity_bands_mm': list(BANDS_MM), 'automatic_selection': False,
                'fixed_parameters': {'smoothness_mm': SMOOTHNESS_MM, 'edge_sigma_hu': EDGE_SIGMA_HU},
                'energy_comparison_scope': 'Before/after within each band only; active nodes and subtracted constants differ across bands.',
                'outside_inference_field': 'not_assessed', 'source_integrity_verified': False}
    write_json(folder / 'manifest.json', manifest)
    try:
        if SMOOTHNESS_MM != 0.5 or EDGE_SIGMA_HU != 20.0:
            raise ValueError('This experiment fixes smoothness at 0.5 mm and edge sigma at 20 HU.')
        sources = read_sources(args.data, args.case, selection, needs_liver=True)
        provenance = sources['provenance']
        for stage in ('pre', 'post'):
            provenance[stage]['ordered_sop_uids'] = [s.sop for s in sources[stage + '_stack'].slices]
        manifest['sources'] = provenance
        manifest['implementation_sha256'] = {
            str(p.relative_to(Path(__file__).resolve().parents[1])): file_sha256(p)
            for parent in (Path(__file__).parent, Path(__file__).resolve().parents[1] / 'hcclab')
            for p in parent.glob('*.py')}
        manifest['software'] = {name: importlib.metadata.version(name) for name in
                                ('numpy', 'scipy', 'pydicom', 'SimpleITK', 'torch', 'torchvision', 'MedSAM2', 'hydra-core')}
        manifest['platform'] = {'system': platform.system(), 'machine': platform.machine(),
                                'python': platform.python_version()}
        global_transform, global_info = register_pre_to_post(
            sources['pre_hu'], sources['post_hu'], sources['pre_stack'], sources['post_stack'],
            pre_valid=sources['pre_valid'], post_valid=sources['post_valid'])
        transform, liver_info = refine_with_liver(sources, global_transform)
        location = localize_pre_region(sources['mass'], sources['pre_stack'], sources['post_stack'], transform)
        manifest['registrations'] = {'global': global_info, 'liver': liver_info}
        manifest['localization'] = location
        write_json(folder / 'manifest.json', manifest)
        spacing = np.asarray(provenance['post']['spacing_zyx_mm'], dtype=float)
        checkpoint = model_path(args.data)
        intermediates = {}
        print(f'Running one POST inference for {args.case}', file=sys.stderr, flush=True)
        raw, inference = segment_post_model(sources['post_hu'], spacing, location,
            checkpoint_path=checkpoint, valid_mask=sources['post_valid'],
            refine_boundary=False, intermediates=intermediates)
        field, raw_field, logits = captured_field(raw, sources['post_valid'], inference, intermediates)
        manifest['model'] = inference['model']
        manifest['inference'] = inference
        manifest['inference_field_yx'] = inference['inference_field_yx']
        manifest['logits'] = {'dtype': 'float32', 'axis_order': 'z,y,x', 'shape': list(logits.shape),
                              'threshold': 0, 'prompt_slice': 'original box logits, not mask reinjection logits',
                              'scope': 'Uncalibrated model scores; not anatomical probabilities.'}
        # Cada banda parte del mismo raw/logits. No encadena refinamientos.
        outputs = {'raw': raw}
        reports = {'raw': {'status': 'experimental_unreviewed', 'metrics': descriptors(
            raw, raw, sources['post_valid'], spacing, location, field)}}
        for band in BANDS_MM:
            print(f'Refining boundary: {band} mm', file=sys.stderr, flush=True)
            name = f'refined-{band}mm'
            try:
                refined, diagnostics = refine_post_boundary(
                    sources['post_hu'][field], raw_field.copy(), logits.copy(), spacing,
                    band_mm=band, valid_mask=sources['post_valid'][field])
                native = native_prediction(refined, field, sources['post_valid'])
                metrics = descriptors(native, raw, sources['post_valid'], spacing, location, field)
                checks = application_checks(metrics, diagnostics, field, raw.shape)
                reports[name] = {'status': 'experimental_unreviewed', 'band_mm': band,
                                 'primary': band == PRIMARY_BAND_MM, 'diagnostics': diagnostics,
                                 'metrics': metrics, 'application_checks': checks}
                outputs[name] = native
            except ValueError as error:
                reports[name] = {'status': 'failed', 'band_mm': band, 'primary': band == PRIMARY_BAND_MM,
                                 'error_type': type(error).__name__, 'error': str(error)}
        verify_unchanged(sources)
        verify_model(checkpoint)
        artifacts = {}
        for name, mask in outputs.items():
            path = folder / (name + '.npz')
            np.savez_compressed(path, mask=mask)
            artifacts[path.name] = file_sha256(path)
            reports[name]['artifact_sha256'] = artifacts[path.name]
        logit_path = folder / 'logits-field.npz'
        np.savez_compressed(logit_path, logits=logits, raw_mask=raw_field,
            inference_field_yx=np.asarray(inference['inference_field_yx'], dtype=np.int64),
            native_shape_zyx=np.asarray(raw.shape, dtype=np.int64), spacing_zyx_mm=spacing)
        artifacts[logit_path.name] = file_sha256(logit_path)
        # Verifica de nuevo después de guardar: un manifiesto completo siempre
        # corresponde a los bytes CT/SEG con los que comenzó la ejecución.
        verify_unchanged(sources)
        hashes_after = [file_sha256(path) for path in sources['paths']]
        if hashes_after != sources['hashes']:
            raise ValueError('Source bytes changed before finalizing the experiment.')
        write_json(folder / 'comparison.json', {'scope': SCOPE, 'primary_band_mm': PRIMARY_BAND_MM,
                                               'predictions': reports})
        artifacts['comparison.json'] = file_sha256(folder / 'comparison.json')
        pre_count = len(sources['pre_stack'].slices)
        failed = [name for name, report in reports.items() if report['status'] == 'failed']
        manifest.update(status='completed_with_failures' if failed else 'completed',
                        completed=list(outputs), failed=failed, source_integrity_verified=True,
                        source_hashes_after={'pre': hashes_after[:pre_count],
                                             'post': hashes_after[pre_count:-1], 'seg': hashes_after[-1]},
                        artifacts_sha256=artifacts, elapsed_seconds=time.perf_counter() - started)
        write_json(folder / 'manifest.json', manifest)
        print(str(folder), flush=True)
        return 1 if failed else 0
    except Exception as error:
        manifest.update(status='failed', error_type=type(error).__name__, error=str(error),
                        elapsed_seconds=time.perf_counter() - started)
        if getattr(error, 'diagnostics', None) is not None:
            manifest['failure_diagnostics'] = error.diagnostics
        write_json(folder / 'manifest.json', manifest)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, default=Path('/data'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--case', required=True)
    parser.add_argument('--manifest', type=Path)
    for name in ('pre-series-uid', 'post-series-uid', 'pre-acquisition', 'post-acquisition'):
        parser.add_argument('--' + name)
    parser.add_argument('--mass-segment', type=int)
    parser.add_argument('--liver-segment', type=int)
    args = parser.parse_args()
    try:
        return execute(args, selection_from_args(args))
    except (ValueError, KeyError, OSError) as error:
        parser.exit(2, f'Boundary experiment failed: {error}\n')


if __name__ == '__main__':
    raise SystemExit(main())
