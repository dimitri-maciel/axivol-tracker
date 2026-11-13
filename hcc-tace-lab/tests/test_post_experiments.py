"""Contratos de ablación: geometría nativa, padding y selección explícita.

No carga pesos, no llama al predictor ni requiere datos TCIA.
"""
from argparse import Namespace
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
from scipy import ndimage
import SimpleITK as sitk

from experiments.post_tracking import read_sources, refine_with_liver, safe_output_directory, selected_stack, selection_from_args
from experiments.tracking_core import VARIANTS, compare_predictions, context_bounds, infer_raw, native_plane, prediction_metrics, window_plane
from hcclab.geometry import Slice, build_stack
from hcclab.registration import index_to_patient, patient_to_index


class ContextGeometryTests(unittest.TestCase):
    def setUp(self):
        self.shape = (30, 500, 600)
        self.location = {'post_center_zyx': [15, 250, 300],
                         'localized_box_zyx': [[10, 230, 280], [20, 270, 320]],
                         'roi_zyx': [[5, 210, 260], [25, 290, 340]]}

    def test_anisotropic_spacing_gives_equal_physical_context_not_equal_pixel_counts(self):
        low, high = context_bounds(self.shape, self.location, [2.5, .5, 1], 160)
        np.testing.assert_array_equal(high - low, [320, 160])
        np.testing.assert_array_equal((high - low) * [.5, 1], [160, 160])
        self.assertTrue(np.all(low <= self.location['post_center_zyx'][1:]))
        self.assertTrue(np.all(high > self.location['post_center_zyx'][1:]))

    def test_context_shifts_inside_fov_without_reducing_requested_extent(self):
        self.location['post_center_zyx'] = [15, 15, 25]
        self.location['localized_box_zyx'] = [[10, 5, 15], [20, 25, 35]]
        low, high = context_bounds(self.shape, self.location, [2.5, 1, 1], 160)
        np.testing.assert_array_equal(low, [0, 0])
        np.testing.assert_array_equal(high - low, [160, 160])

    def test_large_target_scales_context_and_full_variant_preserves_native_fov(self):
        self.location['localized_box_zyx'] = [[10, 100, 200], [20, 400, 400]]
        low, high = context_bounds(self.shape, self.location, [2.5, .5, 1], 160)
        np.testing.assert_array_equal(high - low, [500, 400])
        low, high = context_bounds(self.shape, self.location, [2.5, .5, 1], 0)
        np.testing.assert_array_equal(low, [0, 0])
        np.testing.assert_array_equal(high, [500, 600])

    def test_invalid_geometry_is_rejected(self):
        for spacing in ([1, 0, 1], [1, np.nan, 1], [1, 1]):
            with self.subTest(spacing=spacing), self.assertRaises(ValueError):
                context_bounds(self.shape, self.location, spacing, 160)
        bad = dict(self.location, localized_box_zyx=[[False, 2, 2], [20, 30, 30]])
        with self.assertRaises(ValueError):
            context_bounds(self.shape, bad, [1, 1, 1], 160)

    def test_asymmetric_target_center_does_not_exclude_far_side_of_box(self):
        self.location['localized_box_zyx'] = [[10, 100, 160], [20, 300, 360]]
        self.location['post_center_zyx'] = [15, 101, 359]
        low, high = context_bounds(self.shape, self.location, [2.5, .5, 1], 160)
        self.assertTrue(np.all(low <= [100, 160]))
        self.assertTrue(np.all(high >= [300, 360]))


class PixelContractTests(unittest.TestCase):
    def test_window_excludes_padding_without_mutating_hu_or_validity(self):
        hu = np.array([[-160, 40, 240], [1200, -2000, 0]], dtype=np.float32)
        valid = np.array([[True, True, True], [True, False, True]])
        before, before_valid = hu.copy(), valid.copy()
        result = window_plane(hu, valid, 40, 400)
        np.testing.assert_allclose(result, [[0, .5, 1], [1, 0, .4]])
        np.testing.assert_array_equal(hu, before)
        np.testing.assert_array_equal(valid, before_valid)
        result[:] = 0
        np.testing.assert_array_equal(hu, before)
        with self.assertRaises(ValueError):
            window_plane(hu, valid, 40, 0)

    def test_native_restoration_uses_offsets_and_excludes_padding_without_alias(self):
        valid = np.ones((8, 10), dtype=bool)
        valid[3, 5] = False
        prediction = np.ones((2, 3), dtype=bool)
        result = native_plane(prediction, [2, 4], [4, 7], valid)
        self.assertEqual(int(result.sum()), 5)
        self.assertTrue(result[2, 4] and result[3, 6])
        self.assertFalse(result[3, 5])
        self.assertFalse(result[:2].any())
        result[:] = False
        self.assertTrue(prediction.all())
        self.assertFalse(valid[3, 5])
        with self.assertRaises(ValueError):
            native_plane(prediction, [2, 4], [4, 8], valid)

    def test_metrics_describe_outside_foreground_without_clipping(self):
        mask = np.zeros((6, 8, 10), dtype=bool)
        mask[:, 2, 3] = True
        original = mask.copy()
        result = prediction_metrics(mask, {'roi_zyx': [[2, 0, 0], [5, 8, 10]]})
        self.assertEqual(result['voxels'], 6)
        self.assertEqual(result['outside_fraction'], .5)
        self.assertFalse(result['majority_outside_roi'])
        np.testing.assert_allclose(result['centroid_zyx'], [2.5, 2, 3])
        np.testing.assert_array_equal(mask, original)
        other = mask.copy(); other[0] = False
        comparison = compare_predictions(mask, other)
        self.assertAlmostEqual(comparison['dice_prediction_consistency'], 10/11)
        self.assertTrue(comparison['not_accuracy'])


class SourceSelectionTests(unittest.TestCase):
    def test_seg_replaced_during_header_verification_is_rejected(self):
        with TemporaryDirectory() as root:
            folder = Path(root)
            paths = [folder / name for name in ('pre.bin', 'post.bin', 'seg.bin')]
            for path in paths:
                path.write_bytes(b'original')
            def stack(path):
                image = Slice(path, '1.2.3', '1', 'frame', (2, 2), (1., 1.),
                              (1., 0., 0., 0., 1., 0.), (0., 0., 0.))
                return SimpleNamespace(slices=(image,), interval_mm=2.5)
            selection = {'pre_series_uid': 'pre', 'post_series_uid': 'post',
                         'pre_acquisition': '1', 'post_acquisition': '1', 'mass_segment': 2, 'liver_segment': 1}
            old_seg = (paths[2], object())
            def replace_seg(*args, **kwargs):
                paths[2].write_bytes(b'replaced-after-initial-hash')
                return old_seg
            with patch('experiments.post_tracking.Lab', return_value=SimpleNamespace(catalog=object())), \
                 patch('experiments.post_tracking.selected_stack', side_effect=[
                     ({'StudyInstanceUID': 'pre-study'}, stack(paths[0])),
                     ({'StudyInstanceUID': 'post-study'}, stack(paths[1]))]), \
                 patch('experiments.post_tracking.find_seg', return_value=old_seg), \
                 patch('experiments.post_tracking.verify_pre_sources', side_effect=replace_seg), \
                 patch('experiments.post_tracking.required_segment'), \
                 patch('experiments.post_tracking.referenced_mask', return_value=np.ones((1, 2, 2), bool)), \
                 patch('experiments.post_tracking.load_hu_volume', return_value=(np.zeros((1, 2, 2), np.float32), np.ones((1, 2, 2), bool))):
                with self.assertRaisesRegex(ValueError, 'Source bytes changed'):
                    read_sources(folder, 'test', selection, False)

    def test_acquisition_is_selected_by_identifier_and_ambiguity_is_rejected(self):
        lab = SimpleNamespace(catalog=SimpleNamespace(case=lambda case: {'studies': [
            {'stage': 'PRE', 'series': [{'uid': 'pre'}]}, {'stage': 'POST', 'series': [{'uid': 'post'}]}]}),
            series=lambda uid: {'stacks': [{'index': 0, 'acquisition': '3'}, {'index': 1, 'acquisition': '8'}]},
            stack=Mock(return_value=('row', 'stack')))
        self.assertEqual(selected_stack(lab, 'test', 'PRE', 'pre', '8'), ('row', 'stack'))
        lab.stack.assert_called_once_with('pre', 1)
        with self.assertRaises(ValueError):
            selected_stack(lab, 'test', 'POST', 'pre', '3')
        lab.series = lambda uid: {'stacks': [{'index': 0, 'acquisition': '3'}, {'index': 1, 'acquisition': '3'}]}
        with self.assertRaises(ValueError):
            selected_stack(lab, 'test', 'PRE', 'pre', '3')

    def test_zero_segment_is_rejected_instead_of_replaced_by_default(self):
        args = Namespace(manifest=None, pre_series_uid='pre', post_series_uid='post',
                         pre_acquisition='3', post_acquisition='1', mass_segment=0, liver_segment=None)
        with self.assertRaises(ValueError):
            selection_from_args(args)

    def test_outputs_cannot_be_written_into_source_data(self):
        with TemporaryDirectory() as root:
            data = Path(root) / 'data'; data.mkdir()
            with self.assertRaises(ValueError):
                safe_output_directory(data / 'derived', data)
            folder = safe_output_directory(Path(root) / 'outputs', data)
            self.assertTrue(folder.is_dir())


class RawInferenceTests(unittest.TestCase):
    class Tensor:
        def __init__(self, values): self.values = values
        def squeeze(self, axis): return RawInferenceTests.Tensor(self.values.squeeze(axis))
        def detach(self): return self
        def cpu(self): return self
        def numpy(self): return self.values

    class Predictor:
        def __init__(self, invalid=False):
            self.invalid, self.resets = invalid, 0

        def init_state(self, frames, height, width):
            return {'length': len(frames), 'shape': (height, width),
                    'output_dict_per_obj': {0: {'cond_frame_outputs': {}, 'non_cond_frame_outputs': {}}}}

        def logits(self, state):
            plane = np.full(state['shape'], -1., np.float32)
            plane[2:4, 3:5] = 1
            if self.invalid: plane[0, 0] = np.nan
            return [RawInferenceTests.Tensor(plane[None])]

        def add_new_points_or_box(self, state, **kwargs): return 0, [1], self.logits(state)
        def add_new_mask(self, *args, **kwargs): pass
        def reset_state(self, state): self.resets += 1

        def propagate_in_video(self, state, *, start_frame_idx, reverse):
            indices = range(start_frame_idx, -1, -1) if reverse else range(start_frame_idx, state['length'])
            for index in indices:
                state['output_dict_per_obj'][0]['non_cond_frame_outputs'][index] = {
                    'object_score_logits': np.array([1.0])}
                yield index, [1], self.logits(state)

    def setUp(self):
        self.hu = np.full((6, 8, 10), 55., np.float32)
        self.valid = np.ones(self.hu.shape, dtype=bool)
        self.valid[:, 2, 3] = False
        self.location = {'post_center_zyx': [3, 3, 4],
                         'localized_box_zyx': [[2, 1, 2], [4, 6, 8]],
                         'roi_zyx': [[2, 1, 2], [4, 6, 8]]}
        self.torch = SimpleNamespace(inference_mode=nullcontext)

    def test_raw_ablation_retains_outside_roi_prediction_on_native_valid_grid(self):
        before = self.hu.copy()
        predictor = self.Predictor()
        mask, prompt, info = infer_raw(self.hu, self.valid, [2.5, .7, .8], self.location,
                                       VARIANTS['full'], predictor, self.torch)
        self.assertEqual(mask.shape, self.hu.shape)
        self.assertEqual(int(mask.sum()), 18)
        self.assertTrue(mask[0].any() and mask[-1].any())
        self.assertFalse(mask[:, 2, 3].any())
        self.assertTrue(prediction_metrics(mask, self.location)['majority_outside_roi'])
        self.assertEqual(prompt.shape, self.hu.shape[1:])
        self.assertTrue(info['all_native_z_processed'])
        self.assertEqual(predictor.resets, 2)
        np.testing.assert_array_equal(before, self.hu)

    def test_invalid_model_output_releases_state_and_returns_no_mask(self):
        predictor = self.Predictor(invalid=True)
        with self.assertRaisesRegex(ValueError, 'finite'):
            infer_raw(self.hu, self.valid, [2.5, .7, .8], self.location,
                      VARIANTS['full'], predictor, self.torch)
        self.assertEqual(predictor.resets, 1)


class LiverRegistrationTests(unittest.TestCase):
    def test_known_translation_rotated_axes_is_repeatable_without_mutating_pre_or_post(self):
        shape = (40, 48, 56)
        z, y, x = np.ogrid[:40, :48, :56]
        body = ((z-20)/17)**2 + ((y-24)/21)**2 + ((x-28)/24)**2 < 1
        organ = ((z-21)/12)**2 + ((y-29)/11)**2 + ((x-18)/10)**2 < 1
        mass = (z-22)**2 + (y-29)**2 + (x-18)**2 <= 9
        pre = np.where(body, 20 + .3*x + .2*y + np.zeros_like(z), -1000).astype(np.float32)
        pre[organ] += 70
        texture = ndimage.gaussian_filter(np.random.default_rng(73).normal(size=shape), 1.5) * 100
        pre[body] += texture[body].astype(np.float32)
        shift_zyx = np.array([1, -2, 3])
        post = ndimage.shift(pre, shift_zyx, order=1, mode='constant', cval=-1000)
        orientation = (0, 1, 0, -1, 0, 0)
        origin = np.array([10., 20., 30.])
        stack = build_stack([Slice(Path(f'synthetic-{index}.dcm'), f'1.2.{index+1}', '1', 'frame',
                                    shape[1:], (2., 2.), orientation,
                                    tuple(origin + [0, 0, 2*index])) for index in range(shape[0])])
        source = {'pre_stack': stack, 'post_stack': stack, 'pre_hu': pre, 'post_hu': post,
                  'pre_valid': np.ones(shape, dtype=bool), 'post_valid': np.ones(shape, dtype=bool),
                  'liver': organ & ~mass, 'mass': mass}
        before = {key: value.copy() for key, value in source.items() if isinstance(value, np.ndarray)}
        initial = sitk.Euler3DTransform()
        initial.SetCenter(tuple(index_to_patient((np.array(shape)-1)/2, stack)))
        initial.SetTranslation((4.5, 5.5, 1.5))  # True LPS displacement is (4,6,2) mm.
        transform, info = refine_with_liver(source, initial)
        repeated, _ = refine_with_liver(source, initial)
        point = index_to_patient([22, 29, 18], stack)
        mapped = patient_to_index(transform.TransformPoint(tuple(point)), stack)
        self.assertLess(np.linalg.norm((mapped-(np.array([22, 29, 18])+shift_zyx))*2), 3)
        np.testing.assert_allclose(transform.GetParameters(), repeated.GetParameters(), atol=1e-8, rtol=0)
        self.assertEqual(info['mapping'], 'PRE_patient_LPS_to_POST_patient_LPS')
        self.assertFalse(info['lesion_correspondence_verified'])
        self.assertNotIn('mask', info)
        self.assertEqual(int(np.count_nonzero(source['liver'] & source['mass'])), 0)
        for key, original in before.items():
            np.testing.assert_array_equal(source[key], original)


if __name__ == '__main__':
    unittest.main()
