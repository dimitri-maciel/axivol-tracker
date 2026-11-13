"""La salida POST depende de POST; los datos ambiguos provocan abstención."""
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
from types import ModuleType

import numpy as np
import SimpleITK as sitk

from hcclab.geometry import GeometryError, Slice, build_stack
from hcclab.post_automatic import AutomaticSegmentationError, automatic_post_candidate, segment_from_location


class AutomaticPostTests(unittest.TestCase):
    def setUp(self):
        self.spacing = (2, 1, 1)
        self.shape = (40, 60, 60)
        z, y, x = np.ogrid[:40, :60, :60]
        self.distance = np.sqrt(((z-20)*2)**2+(y-30)**2+(x-30)**2)
        self.truth = self.distance <= 8
        self.hu = np.where(self.truth, 120, 0).astype(np.float32)
        self.location = {'post_center_zyx': [20., 30., 30.], 'roi_zyx': [[10, 10, 10], [30, 50, 50]]}

    def test_clear_post_object_is_recovered_in_native_grid(self):
        original = self.hu.copy()
        candidate, diagnostics = segment_from_location(self.hu, self.spacing, self.location)
        np.testing.assert_array_equal(candidate, self.truth)
        np.testing.assert_array_equal(self.hu, original)
        self.assertEqual(candidate.dtype, np.bool_)
        self.assertEqual(diagnostics['voxels'], int(self.truth.sum()))
        self.assertEqual(diagnostics['display_slice_index'], 20)
        self.assertEqual(diagnostics['status'], 'candidate_unreviewed')
        self.assertEqual(diagnostics['source_intensities'], 'POST_native_HU')
        self.assertEqual(diagnostics['sensitivity_dice'], 1)
        json.dumps(diagnostics, allow_nan=False)

    def test_ambiguous_post_leakage_returns_diagnostics_without_mask_or_volume(self):
        uniform = np.full(self.shape, 120, np.float32)
        with self.assertRaises(AutomaticSegmentationError) as captured:
            segment_from_location(uniform, self.spacing, self.location)
        diagnostics = captured.exception.diagnostics
        self.assertEqual(diagnostics['segmentation']['status'], 'abstained')
        self.assertEqual(diagnostics['segmentation']['reason'], 'roi_leakage')
        self.assertEqual(len(diagnostics['segmentation']['base_component']['roi_touch_faces']), 6)
        self.assertNotIn('mask', diagnostics)
        self.assertNotIn('volume_cm3', diagnostics['segmentation'])
        json.dumps(diagnostics, allow_nan=False)

    def test_wider_interval_exposes_instability(self):
        hu = np.where(self.truth, 100, 0).astype(np.float32)
        sample = np.argwhere(self.distance <= 4)
        # 8% de los píxeles de muestra cambian el percentil95, no el90.
        for z, y, x in sample[:max(1, round(.08*len(sample)))]:
            hu[z, y, x] = 110
        hu[20, 30, 38:50] = 110  # Puente que solo entra con el intervalo amplio.
        with self.assertRaises(AutomaticSegmentationError) as captured:
            segment_from_location(hu, self.spacing, self.location)
        self.assertEqual(captured.exception.diagnostics['segmentation']['reason'], 'threshold_instability')

    def test_padding_near_center_causes_abstention(self):
        valid = np.ones(self.shape, bool)
        valid[self.distance <= 5] = False
        with self.assertRaises(AutomaticSegmentationError) as captured:
            segment_from_location(self.hu, self.spacing, self.location, valid_mask=valid)
        self.assertEqual(captured.exception.diagnostics['segmentation']['reason'], 'insufficient_samples')

    def test_pre_shape_is_not_transferred_and_abstention_retains_registration(self):
        stack = build_stack([Slice(Path(f'{z}.dcm'), f'1.2.{z+1}', '1', 'frame', self.shape[1:], (1, 1),
                                   (1, 0, 0, 0, 1, 0), (0, 0, z*2)) for z in range(self.shape[0])])
        pre_a = self.distance <= 6
        pre_b = np.zeros(self.shape, bool); pre_b[17:24, 24:37, 24:37] = True
        identity = sitk.Transform(3, sitk.sitkIdentity)
        with patch('hcclab.post_automatic.register_pre_to_post', return_value=(identity, {'status': 'estimated_unverified'})):
            a, _ = automatic_post_candidate(self.hu, self.hu, pre_a, stack, stack)
            b, _ = automatic_post_candidate(self.hu, self.hu, pre_b, stack, stack)
            np.testing.assert_array_equal(a, self.truth)
            np.testing.assert_array_equal(b, self.truth)
            self.assertNotEqual(int(a.sum()), int(pre_a.sum()))
            self.assertNotEqual(int(b.sum()), int(pre_b.sum()))
            with self.assertRaises(AutomaticSegmentationError) as captured:
                automatic_post_candidate(self.hu, np.full(self.shape, 120, np.float32), pre_a, stack, stack)
        self.assertIn('localization', captured.exception.diagnostics['registration'])
        self.assertEqual(captured.exception.diagnostics['segmentation']['status'], 'abstained')

    def test_model_receives_only_post_and_never_falls_back_to_hu(self):
        stack = Mock(interval_mm=2, slices=[Mock(spacing=(1, 1))])
        module = ModuleType('hcclab.post_model')
        class ModelError(GeometryError):
            def __init__(self):
                super().__init__('Model abstained.')
                self.diagnostics = {'status': 'abstained', 'reason': 'synthetic_model_check'}
        module.ModelSegmentationError = ModelError
        module.segment_post_model = Mock(return_value=(self.truth.copy(), {'voxels': int(self.truth.sum())}))
        with patch.dict('sys.modules', {'hcclab.post_model': module}), \
                patch('hcclab.post_automatic.register_pre_to_post', return_value=(None, {'status': 'estimated_unverified'})), \
                patch('hcclab.post_automatic.localize_pre_region', return_value=self.location), \
                patch('hcclab.post_automatic.segment_from_location') as baseline:
            result, info = automatic_post_candidate(None, self.hu, None, stack, stack, model_path=Path('model.pt'))
            np.testing.assert_array_equal(result, self.truth)
            args, kwargs = module.segment_post_model.call_args
            self.assertIs(args[0], self.hu)
            self.assertEqual(args[1], (2, 1, 1))
            self.assertIs(args[2], self.location)
            self.assertEqual(kwargs['checkpoint_path'], Path('model.pt'))
            self.assertIsNone(kwargs['valid_mask'])
            baseline.assert_not_called()
            module.segment_post_model.side_effect = ModelError()
            with self.assertRaises(AutomaticSegmentationError) as captured:
                automatic_post_candidate(None, self.hu, None, stack, stack, model_path=Path('model.pt'))
            self.assertEqual(captured.exception.diagnostics['segmentation']['reason'], 'synthetic_model_check')
            self.assertIn('registration', captured.exception.diagnostics)
            baseline.assert_not_called()

    def test_localization_failure_preserves_estimated_registration(self):
        registration = {'status': 'estimated_unverified', 'parameters': [0]*6}
        with patch('hcclab.post_automatic.register_pre_to_post', return_value=(None, registration)), \
                patch('hcclab.post_automatic.localize_pre_region', side_effect=GeometryError('Coverage incomplete.')):
            with self.assertRaises(AutomaticSegmentationError) as captured:
                automatic_post_candidate(None, None, None, None, None)
        self.assertEqual(captured.exception.diagnostics['registration']['parameters'], [0]*6)
        self.assertEqual(captured.exception.diagnostics['segmentation']['status'], 'not_started')

    def test_context_refines_localization_but_never_becomes_model_input(self):
        stack = Mock(interval_mm=2, slices=[Mock(spacing=(1, 1))])
        context = np.ones_like(self.hu, dtype=bool)
        initial, refined = object(), object()
        with patch('hcclab.post_automatic.register_pre_to_post', return_value=(initial, {'status': 'estimated_unverified'})), \
                patch('hcclab.post_automatic.refine_pre_to_post', return_value=(refined, {'status': 'estimated_unverified'})) as refine, \
                patch('hcclab.post_automatic.localize_pre_region', return_value=self.location) as locate, \
                patch('hcclab.post_model.segment_post_model', return_value=(self.truth, {'voxels': int(self.truth.sum())})) as model:
            result, info = automatic_post_candidate(self.hu, self.hu, self.truth, stack, stack,
                                                    pre_context_mask=context, model_path=Path('model.pt'))
            self.assertIs(refine.call_args.args[2], context)
            self.assertIs(refine.call_args.args[5], initial)
            self.assertIs(locate.call_args.args[3], refined)
            self.assertEqual(len(model.call_args.args), 3)
            self.assertIs(model.call_args.args[0], self.hu)
            self.assertIs(model.call_args.args[2], self.location)
            self.assertNotIn('pre_context_mask', model.call_args.kwargs)
            self.assertIs(result, self.truth)
            self.assertIn('global_registration', info['registration'])

    def test_failed_context_refinement_does_not_silently_use_global_registration(self):
        with patch('hcclab.post_automatic.register_pre_to_post', return_value=(None, {'parameters': [0]*6})), \
                patch('hcclab.post_automatic.refine_pre_to_post', side_effect=GeometryError('Invalid context.')), \
                patch('hcclab.post_automatic.localize_pre_region') as locate:
            with self.assertRaises(AutomaticSegmentationError) as caught:
                automatic_post_candidate(None, None, None, None, None, pre_context_mask=self.truth)
            self.assertEqual(caught.exception.diagnostics['registration']['stage'], 'PRE_context_refinement')
            self.assertEqual(caught.exception.diagnostics['registration']['global_registration']['parameters'], [0]*6)
            self.assertEqual(caught.exception.diagnostics['segmentation']['status'], 'not_started')
            locate.assert_not_called()


if __name__ == '__main__':
    unittest.main()
