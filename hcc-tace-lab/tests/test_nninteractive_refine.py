"""Refinamiento neural POST: contrato nativo y procedencia, sin modelo/pesos."""
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from experiments import nninteractive_refine as adapter


class Session:
    supports_initial_label = True
    supports_zero_shot_label_refinement = True

    def __init__(self, action=None, bounds=None, preprocess=None):
        self.action = action
        self.preprocess = preprocess
        self.bounds = [[1, 5], [1, 7], [1, 8]] if bounds is None else bounds
        self.executor = SimpleNamespace(shutdown=Mock())
        self.calls = []

    def set_image(self, image):
        self.image = image
        self.calls.append('image')

    def _finish_preprocessing_and_initialize_interactions(self):
        self.calls.append('preprocess')
        self.preprocessed_image = self.image.copy()
        if self.preprocess:
            self.preprocess(self)

    def set_target_buffer(self, target):
        self.target = target
        self.calls.append('target')

    def add_initial_seg_interaction(self, initial, *, run_prediction=False):
        if run_prediction is not True:
            raise AssertionError('Initial-label API must actually predict, not copy the baseline only.')
        self.calls.append('initial_label_predict')
        self.initial = initial
        self.target[:] = initial  # Contrato upstream: toda la máscara empieza preservada.
        if self.action:
            self.action(self)
        return self.bounds


class RefinementContractTests(unittest.TestCase):
    def setUp(self):
        self.hu = np.arange(5*8*10, dtype=np.float32).reshape(5, 8, 10)
        self.initial = np.zeros(self.hu.shape, bool)
        self.initial[2:4, 3:5, 4:6] = True
        self.initial[0, 0, 0] = True  # Componente fuera de la región escrita.
        self.valid = np.ones(self.hu.shape, bool)
        self.spacing = [2.5, .5, 1.]

    def refine(self, session, **overrides):
        kwargs = {'post_hu': self.hu, 'initial_mask': self.initial,
                  'spacing_zyx': self.spacing, 'valid_mask': self.valid,
                  'model_dir': '/unused'}
        kwargs.update(overrides)
        with patch.object(adapter, '_load_session', return_value=(session, {'checkpoint': 'synthetic'})), \
             patch.object(adapter, 'verify_assets'):
            return adapter.refine_with_nninteractive(**kwargs)

    def test_native_change_can_extend_z_and_preserves_other_components_and_inputs(self):
        before_hu, before_initial, before_valid = self.hu.copy(), self.initial.copy(), self.valid.copy()
        def action(session):
            self.assertEqual(session.image.shape, (1, *self.hu.shape))
            self.assertEqual(session.image.dtype, np.float32)
            self.assertEqual(session.initial.dtype, np.uint8)
            np.testing.assert_array_equal(session.image[0], before_hu)
            np.testing.assert_array_equal(session.initial, before_initial)
            session.target[4, 3, 4] = 1  # Corte posterior al foreground inicial principal.
            session.target[2, 3, 4] = 0
            session.image[:] = -9  # Ni un consumidor que modifica sus inputs toca originales.
            session.initial[:] = 0
        session = Session(action)
        result, info = self.refine(session)
        self.assertTrue(result[4, 3, 4] and result[0, 0, 0])
        self.assertFalse(result[2, 3, 4])
        self.assertEqual(result.shape, self.initial.shape)
        self.assertEqual(result.dtype, np.bool_)
        self.assertEqual(info['added_voxels'], 1)
        self.assertEqual(info['removed_voxels'], 1)
        self.assertEqual(info['changed_slices'], [2, 4])
        self.assertEqual(info['volume_cm3'], self.initial.sum()*1.25/1000)
        self.assertEqual(info['coverage']['reported_write_envelope_zyx'], [[1, 1, 1], [5, 7, 8]])
        self.assertEqual(info['coverage']['baseline_voxels_outside_envelope'], 1)
        self.assertEqual(info['coverage']['outside_envelope'], 'inherited_baseline_not_reevaluated')
        self.assertIn('z_min', info['acquisition_touch_faces'])
        self.assertEqual(session.calls, ['image', 'preprocess', 'target', 'initial_label_predict'])
        session.executor.shutdown.assert_called_once_with(wait=True)
        np.testing.assert_array_equal(self.hu, before_hu)
        np.testing.assert_array_equal(self.initial, before_initial)
        np.testing.assert_array_equal(self.valid, before_valid)
        result[:] = False
        np.testing.assert_array_equal(self.initial, before_initial)
        json.dumps(info, allow_nan=False)

    def test_empty_output_is_not_relabelled_as_confirmed_absence_or_silently_filled(self):
        self.initial[0, 0, 0] = False
        result, info = self.refine(Session(lambda s: s.target.fill(0)))
        self.assertFalse(result.any())
        self.assertEqual(info['outcome'], 'empty_prediction_not_confirmed_absence')
        self.assertIsNone(info['display_slice_index'])
        self.assertEqual(info['status'], 'completed_unreviewed')
        self.assertGreater(info['removed_voxels'], 0)

    def test_unchanged_result_is_explicit_and_readonly_inputs_are_supported(self):
        self.hu.flags.writeable = False; self.initial.flags.writeable = False
        result, info = self.refine(Session())
        np.testing.assert_array_equal(result, self.initial)
        self.assertEqual(info['outcome'], 'unchanged')
        self.assertEqual(info['changed_slices'], [])
        self.assertFalse(np.shares_memory(result, self.initial))

    def test_changed_padding_is_rejected_instead_of_clipped(self):
        self.valid[3, 4, 7] = False
        session = Session(lambda s: s.target.__setitem__((3, 4, 7), 1))
        with self.assertRaisesRegex(adapter.NeuralRefinementError, 'invalid pixel padding') as caught:
            self.refine(session)
        self.assertEqual(caught.exception.diagnostics['status'], 'failed')
        self.assertEqual(session.target[3, 4, 7], 1)
        session.executor.shutdown.assert_called_once_with(wait=True)

    def test_changed_voxel_outside_reported_envelope_is_rejected(self):
        with self.assertRaisesRegex(adapter.NeuralRefinementError, 'outside the official write envelope'):
            self.refine(Session(lambda s: s.target.__setitem__((0, 0, 0), 0)))

    def test_nonbinary_output_is_rejected_without_bool_coercion(self):
        with self.assertRaisesRegex(adapter.NeuralRefinementError, 'not binary'):
            self.refine(Session(lambda s: s.target.__setitem__((2, 3, 4), 2)))

    def test_write_envelope_must_be_native_halfopen_integer_bounds(self):
        invalid = [None, [[1, 5], [1, 7]], [[True, 5], [1, 7], [1, 8]],
                   [[1, 6], [1, 7], [1, 8]], [[1, 5], [7, 7], [1, 8]],
                   [[1.5, 5], [1, 7], [1, 8]]]
        for bounds in invalid:
            with self.subTest(bounds=bounds), self.assertRaises(adapter.NeuralRefinementError):
                session = Session(); session.bounds = bounds
                self.refine(session)

    def test_model_failures_cleanup_and_do_not_fabricate_a_successful_baseline_copy(self):
        def fail(session):
            raise RuntimeError('Synthetic model failure')
        session = Session(fail)
        with self.assertRaisesRegex(adapter.NeuralRefinementError, 'Synthetic model failure') as caught:
            self.refine(session)
        self.assertEqual(caught.exception.diagnostics['error_type'], 'RuntimeError')
        self.assertEqual(caught.exception.diagnostics['status'], 'failed')
        session.executor.shutdown.assert_called_once_with(wait=True)

    def test_capability_must_support_both_initial_label_and_refinement(self):
        for capability in ('supports_initial_label', 'supports_zero_shot_label_refinement'):
            with self.subTest(capability=capability):
                session = Session(); setattr(session, capability, False)
                with self.assertRaisesRegex(adapter.NeuralRefinementError, 'does not support'):
                    self.refine(session)
                self.assertEqual(session.calls, [])

    def test_invalid_inputs_fail_before_model_loading(self):
        too_large = np.broadcast_to(np.ones(1, np.float32), (401, 400, 400))
        bad_hu = self.hu.copy(); bad_hu[0, 0, 0] = np.nan
        overflow_hu = self.hu.astype(np.float64); overflow_hu[0, 0, 0] = 1e300
        invalid = [dict(post_hu=bad_hu), dict(post_hu=overflow_hu), dict(post_hu=too_large),
                   dict(post_hu=np.zeros_like(self.hu)), dict(post_hu=self.hu[0]),
                   dict(initial_mask=self.initial.astype(np.uint8)),
                   dict(initial_mask=np.zeros_like(self.initial)),
                   dict(valid_mask=np.ones(self.hu.shape, np.uint8)),
                   dict(valid_mask=np.zeros(self.hu.shape, bool)),
                   dict(spacing_zyx=[True, 1, 1]), dict(spacing_zyx=[1, 0, 1]),
                   dict(spacing_zyx=[1, np.inf, 1]), dict(spacing_zyx=[1, 1])]
        for overrides in invalid:
            with self.subTest(keys=list(overrides)), patch.object(adapter, '_load_session') as load:
                with self.assertRaises(ValueError):
                    adapter.refine_with_nninteractive(**{'post_hu': self.hu, 'initial_mask': self.initial,
                        'spacing_zyx': self.spacing, 'model_dir': '/unused', 'valid_mask': self.valid, **overrides})
                load.assert_not_called()

    def test_constant_nonzero_bounding_region_is_rejected_before_model_loading(self):
        # El volumen completo varía, pero la caja usada para normalizar tiene
        # desviación estándar cero. Float64 puede colapsar a lo mismo en float32.
        constant_block = np.zeros(self.hu.shape, np.float32)
        constant_block[1:4, 2:6, 3:7] = 100
        collapsed_block = constant_block.astype(np.float64)
        collapsed_block[2, 3, 4] += 1e-8
        single_voxel = np.zeros_like(self.hu); single_voxel[2, 3, 4] = 1
        for hu in (constant_block, collapsed_block, single_voxel):
            with self.subTest(dtype=hu.dtype), patch.object(adapter, '_load_session') as load:
                self.assertGreater(float(hu.std()), 0)
                with self.assertRaisesRegex(ValueError, 'normalization region'):
                    adapter.refine_with_nninteractive(hu, self.initial, self.spacing, model_dir='/unused')
                load.assert_not_called()

    def test_internal_zero_is_part_of_normalization_box_not_discarded(self):
        hu = np.zeros(self.hu.shape, np.float32)
        hu[1:4, 2:6, 3:7] = 100
        hu[2, 3, 4] = 0
        result, info = self.refine(Session(), post_hu=hu)
        np.testing.assert_array_equal(result, self.initial)
        self.assertEqual(info['status'], 'completed_unreviewed')

    def test_actual_nonfinite_normalization_stops_before_inference(self):
        for invalid in (np.nan, np.inf, -np.inf):
            with self.subTest(invalid=invalid):
                def preprocess(session):
                    session.preprocessed_image[0, 2, 3, 4] = invalid
                session = Session(preprocess=preprocess)
                with self.assertRaisesRegex(adapter.NeuralRefinementError, 'normalized POST image contains nonfinite') as caught:
                    self.refine(session)
                self.assertEqual(session.calls, ['image', 'preprocess'])
                self.assertEqual(caught.exception.diagnostics['status'], 'failed')
                self.assertFalse(hasattr(session, 'target'))
                session.executor.shutdown.assert_called_once_with(wait=True)

    def test_preprocessing_must_preserve_native_shape(self):
        session = Session(preprocess=lambda s: setattr(s, 'preprocessed_image', s.preprocessed_image[:, :-1]))
        with self.assertRaisesRegex(adapter.NeuralRefinementError, 'normalized POST image does not preserve'):
            self.refine(session)
        self.assertEqual(session.calls, ['image', 'preprocess'])


class AssetIntegrityTests(unittest.TestCase):
    def test_assets_checked_by_exact_bytes_before_any_model_loader(self):
        with TemporaryDirectory() as temp:
            root = Path(temp); path = root/'weights.bin'; path.write_bytes(b'official-test')
            expected = (len(b'official-test'), hashlib.sha256(b'official-test').hexdigest())
            with patch.dict(adapter.ASSETS, {'weights.bin': expected}, clear=True):
                info = adapter.verify_assets(root)
                self.assertEqual(info['files']['weights.bin']['sha256'], expected[1])
                path.write_bytes(b'changed--test')  # Mismo tamaño, hash distinto.
                with self.assertRaisesRegex(ValueError, 'SHA-256'):
                    adapter.verify_assets(root)
                path.unlink()
                target = root/'alias.bin'; target.write_bytes(b'official-test')
                path.symlink_to(target)
                with self.assertRaisesRegex(ValueError, 'missing or has an unexpected size'):
                    adapter.verify_assets(root)


if __name__ == '__main__':
    unittest.main()
