"""Runner de sensibilidad: transporte/retícula/procedencia con datos sintéticos.

No carga pesos, no infiere pacientes y no escribe en las anotaciones de la app.
Las pruebas matemáticas del refinamiento pertenecen a test_post_boundary.py.
"""
from argparse import Namespace
from contextlib import ExitStack, redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from experiments import post_boundary as experiment
from hcclab.candidates import file_sha256


class BoundaryFieldTests(unittest.TestCase):
    def setUp(self):
        self.valid = np.ones((5, 8, 10), dtype=bool)
        self.native = np.zeros(self.valid.shape, dtype=bool)
        self.native[2, 3:5, 5] = True
        self.field = (slice(None), slice(1, 7), slice(2, 9))
        self.raw = self.native[self.field].copy()
        self.logits = np.where(self.raw, 2, -2).astype(np.float32)
        self.info = {'inference_field_yx': [[1, 2], [7, 9]]}

    def capture(self, **intermediates):
        return experiment.captured_field(self.native, self.valid, self.info,
            {'raw_mask': self.raw, 'logits': self.logits, **intermediates})

    def test_native_offsets_and_positive_logits_round_trip_without_resampling(self):
        field, raw, logits = self.capture()
        np.testing.assert_array_equal(experiment.native_prediction(raw, field, self.valid), self.native)
        self.assertEqual(logits.shape, (5, 6, 7))
        metrics = experiment.descriptors(self.native, self.native, self.valid, [2.5, .5, 1],
            {'roi_zyx': [[2, 3, 4], [3, 4, 6]]}, field)
        self.assertEqual(metrics['bounds_zyx_halfopen'], [[2, 3, 5], [3, 5, 6]])
        self.assertEqual(metrics['outside_search_roi_voxels'], 1)
        self.assertEqual(metrics['outside_inference_field_voxels'], 0)
        self.assertEqual(metrics['components_6'], 1)
        self.assertEqual(metrics['volume_cm3'], .0025)

    def test_invalid_offsets_logits_or_raw_alignment_rejected(self):
        for bounds in ([[True, 2], [7, 9]], [[1.5, 2], [7, 9]], [[1, 2], [9, 9]]):
            with self.subTest(bounds=bounds), self.assertRaises(ValueError):
                experiment.captured_field(self.native, self.valid, {'inference_field_yx': bounds},
                    {'raw_mask': self.raw, 'logits': self.logits})
        for logits in (self.logits.astype(np.float64), self.logits[:, :-1], np.full_like(self.logits, np.nan),
                       -self.logits):
            with self.subTest(dtype=logits.dtype, shape=logits.shape), self.assertRaises(ValueError):
                self.capture(logits=logits)
        self.native[0, 0, 0] = True
        with self.assertRaisesRegex(ValueError, 'differs'):
            self.capture()

    def test_padding_is_excluded_but_never_silently_clipped_from_refined_output(self):
        self.valid[2, 3, 5] = False
        self.native[2, 3, 5] = False
        self.raw[2, 2, 3] = False
        field, _, _ = self.capture()  # Positive padding logit may remain; validity is explicit.
        bad = self.raw.copy(); bad[2, 2, 3] = True
        with self.assertRaisesRegex(ValueError, 'invalid CT support'):
            experiment.native_prediction(bad, field, self.valid)

    def test_band_on_internal_field_edge_would_abstain_but_acquisition_z_edge_is_distinct(self):
        metrics = experiment.descriptors(self.native, self.native, self.valid, [2.5, .5, 1],
            {'roi_zyx': [[0, 0, 0], list(self.native.shape)]}, self.field)
        report = experiment.application_checks(metrics, {'editable_band_touch_faces': ['z_min', 'x_max']},
            self.field, self.native.shape)
        self.assertEqual(report['status'], 'would_abstain')
        self.assertEqual(report['reasons'], ['incomplete_boundary_context'])
        self.assertEqual(report['editable_band_internal_field_faces'], ['x_max'])
        report = experiment.application_checks(metrics, {'editable_band_touch_faces': ['z_min']},
            self.field, self.native.shape)
        self.assertEqual(report['status'], 'spatial_checks_passed')


class BoundaryRunnerTests(unittest.TestCase):
    def run_setup(self, root, stack):
        fixture = BoundaryFieldTests(); fixture.setUp()
        files = []
        for name in [f'{stage}-{i}' for stage in ('pre', 'post') for i in range(5)] + ['seg']:
            path = root / (name + '.bin'); path.write_bytes(name.encode()); files.append(path)
        sources = {'pre_stack': SimpleNamespace(slices=[SimpleNamespace(sop=f'pre-{i}', path=files[i]) for i in range(5)]),
                   'post_stack': SimpleNamespace(slices=[SimpleNamespace(sop=f'post-{i}', path=files[5+i]) for i in range(5)]),
                   'pre_hu': np.zeros(fixture.valid.shape, dtype=np.float32),
                   'post_hu': np.zeros(fixture.valid.shape, dtype=np.float32),
                   'pre_valid': fixture.valid.copy(), 'post_valid': fixture.valid.copy(),
                   'mass': fixture.native.copy(), 'liver': fixture.native.copy(),
                   'paths': files, 'hashes': [file_sha256(p) for p in files],
                   'provenance': {'case_id': 'synthetic', 'pre': {}, 'post': {'spacing_zyx_mm': [2.5, .5, 1]}}}
        args = Namespace(data=root / 'data', output=root / 'output', case='synthetic')
        location = {'roi_zyx': [[1, 1, 2], [4, 7, 9]]}
        calls = []

        def infer(*args, **kwargs):
            self.assertIs(kwargs['refine_boundary'], False)
            kwargs['intermediates'].update(raw_mask=fixture.raw.copy(), logits=fixture.logits.copy())
            return fixture.native.copy(), {**fixture.info, 'model': {'sha256': 'synthetic'}, 'prompt_slice_index': 2}

        def refine(hu, initial, logits, spacing, *, band_mm, valid_mask):
            np.testing.assert_array_equal(initial, fixture.raw)
            np.testing.assert_array_equal(logits, fixture.logits)
            calls.append(band_mm)
            result = initial.copy(); result[2, 1, 1] = True
            if band_mm == 2: result[2, 2, 3] = False
            # Deliberate mutation confirms bands are independently protected.
            initial[:] = False; logits[:] = 0
            return result, {'energy_before': 8., 'energy_after': 7., 'outcome': 'refined', 'editable_band_touch_faces': []}

        replacements = {'read_sources': lambda *a, **k: sources,
                        'register_pre_to_post': lambda *a, **k: ('global', {'status': 'estimated_unverified'}),
                        'refine_with_liver': lambda *a: ('liver', {'status': 'estimated_unverified'}),
                        'localize_pre_region': lambda *a: location,
                        'model_path': lambda *a: root / 'synthetic-weights', 'verify_model': lambda *a: None,
                        'segment_post_model': infer, 'refine_post_boundary': refine}
        for name, replacement in replacements.items():
            stack.enter_context(patch.object(experiment, name, side_effect=replacement))
        stack.enter_context(patch.object(experiment.importlib.metadata, 'version', return_value='synthetic'))
        stack.enter_context(redirect_stdout(io.StringIO())); stack.enter_context(redirect_stderr(io.StringIO()))
        return args, sources, fixture, calls

    def test_single_inference_saves_original_and_independent_bands_with_native_hashes(self):
        with TemporaryDirectory() as temporary, ExitStack() as stack:
            args, sources, fixture, calls = self.run_setup(Path(temporary), stack)
            self.assertEqual(experiment.execute(args, {'synthetic_selection': True}), 0)
            experiment.segment_post_model.assert_called_once()
            self.assertEqual(calls, [1, 2, 3])
            folder = next(args.output.iterdir())
            manifest = json.loads((folder / 'manifest.json').read_text())
            report = json.loads((folder / 'comparison.json').read_text())
            self.assertEqual(manifest['primary_band_mm'], 2)
            self.assertFalse(manifest['automatic_selection'])
            self.assertEqual(manifest['status'], 'completed')
            self.assertTrue(manifest['source_integrity_verified'])
            self.assertEqual(manifest['sources']['post']['ordered_sop_uids'], [f'post-{i}' for i in range(5)])
            self.assertEqual(manifest['source_hashes_after']['post'], sources['hashes'][5:10])
            for name, digest in manifest['artifacts_sha256'].items():
                self.assertEqual(file_sha256(folder / name), digest)
            with np.load(folder / 'raw.npz', allow_pickle=False) as archive:
                np.testing.assert_array_equal(archive['mask'], fixture.native)
            with np.load(folder / 'logits-field.npz', allow_pickle=False) as archive:
                np.testing.assert_array_equal(archive['logits'], fixture.logits)
                np.testing.assert_array_equal(archive['inference_field_yx'], fixture.info['inference_field_yx'])
            with np.load(folder / 'refined-2mm.npz', allow_pickle=False) as archive:
                self.assertEqual(archive['mask'].shape, fixture.valid.shape)
                self.assertTrue(archive['mask'][2, 2, 3])
                self.assertFalse(archive['mask'][2, 3, 5])
            result = report['predictions']['refined-2mm']
            self.assertEqual(result['diagnostics']['energy_after'], 7.)
            self.assertEqual(result['metrics']['added_voxels'], 1)
            self.assertEqual(result['metrics']['removed_voxels'], 1)
            self.assertEqual(result['metrics']['changed_percent_of_raw'], 100.)

    def test_source_replacement_aborts_before_any_mask_artifact_is_saved(self):
        with TemporaryDirectory() as temporary, ExitStack() as stack:
            args, sources, fixture, calls = self.run_setup(Path(temporary), stack)
            refine = experiment.refine_post_boundary.side_effect
            def replace_source(*a, **kw):
                result = refine(*a, **kw)
                sources['paths'][0].write_bytes(b'replaced during computation')
                return result
            experiment.refine_post_boundary.side_effect = replace_source
            with self.assertRaisesRegex(ValueError, 'Source bytes changed'):
                experiment.execute(args, {})
            folder = next(args.output.iterdir())
            self.assertEqual(list(folder.glob('*.npz')), [])
            manifest = json.loads((folder / 'manifest.json').read_text())
            self.assertEqual(manifest['status'], 'failed')
            self.assertFalse(manifest['source_integrity_verified'])

    def test_one_band_limit_keeps_other_results_without_selecting_a_replacement(self):
        with TemporaryDirectory() as temporary, ExitStack() as stack:
            args, sources, fixture, calls = self.run_setup(Path(temporary), stack)
            refine = experiment.refine_post_boundary.side_effect
            def reject_primary(*a, **kw):
                if kw['band_mm'] == 2:
                    raise ValueError('Synthetic graph resource limit')
                return refine(*a, **kw)
            experiment.refine_post_boundary.side_effect = reject_primary
            self.assertEqual(experiment.execute(args, {}), 1)
            folder = next(args.output.iterdir())
            manifest = json.loads((folder / 'manifest.json').read_text())
            self.assertEqual(manifest['status'], 'completed_with_failures')
            self.assertEqual(manifest['failed'], ['refined-2mm'])
            self.assertEqual(manifest['primary_band_mm'], 2)
            self.assertFalse(manifest['automatic_selection'])
            self.assertFalse((folder / 'refined-2mm.npz').exists())
            self.assertTrue((folder / 'raw.npz').exists())
            self.assertTrue((folder / 'refined-3mm.npz').exists())


if __name__ == '__main__':
    unittest.main()
