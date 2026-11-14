"""No weights needed: verify native geometry and propagation orchestration."""
from contextlib import nullcontext
import copy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from hcclab.post_model import ModelSegmentationError, _NativeFrames, segment_post_model


class Tensor:
    def __init__(self, array): self.array = array
    def squeeze(self, axis): return Tensor(self.array.squeeze(axis))
    def detach(self): return self
    def cpu(self): return self
    def numpy(self): return self.array


class Predictor:
    def __init__(self, incomplete=False, empty=False):
        self.incomplete, self.empty = incomplete, empty
        self.box = None
        self.directions = []
        self.reset_count = 0

    def init_state(self, frames, height, width):
        return {'n':len(frames), 'shape':(height, width)}

    def logits(self, state):
        plane = np.full(state['shape'], -1, dtype=np.float32)
        if not self.empty: plane[2:4, 3:5] = 1
        return [Tensor(plane[None])]

    def add_new_points_or_box(self, state, *, frame_idx, obj_id, box):
        self.box = box.copy()
        return frame_idx, [obj_id], self.logits(state)

    def add_new_mask(self, *args, **kwargs): pass
    def reset_state(self, state): self.reset_count += 1

    def propagate_in_video(self, state, *, start_frame_idx, reverse):
        self.directions.append(reverse)
        indices = range(start_frame_idx, -1, -1) if reverse else range(start_frame_idx, state['n'])
        for index in indices:
            if not (self.incomplete and index == 0):
                yield index, [1], self.logits(state)


class FieldPredictor(Predictor):
    """Captura la retícula de entrada sin cargar Torch ni pesos."""
    def __init__(self, edge=None):
        super().__init__()
        self.edge = edge
        self.frames = None
        self.received_shapes = []

    def init_state(self, frames, height, width):
        self.frames = frames
        self.received_shapes.append((height, width))
        return super().init_state(frames, height, width)

    def logits(self, state):
        if self.edge is None:
            return super().logits(state)
        plane = np.full(state['shape'], -1, dtype=np.float32)
        plane[self.edge] = 1
        return [Tensor(plane[None])]


class ConditioningPredictor(Predictor):
    """Registra estados y rutas; no presupone que una caja use memoria suave."""
    def __init__(self, *, bad_reverse=False):
        super().__init__()
        self.states, self.events, self.boxes = [], [], []
        self.bad_reverse = bad_reverse
        self.binarize_mask_from_pts_for_mem_enc = True

    def init_state(self, frames, height, width):
        state = super().init_state(frames, height, width)
        state['id'] = len(self.states)
        self.states.append(state)
        self.events.append(('init', state['id']))
        return state

    def add_new_points_or_box(self, state, **kwargs):
        result = super().add_new_points_or_box(state, **kwargs)
        self.events.append(('box', state['id']))
        self.boxes.append(kwargs['box'].copy())
        state['conditioning'] = 'box'
        result[2][0].array *= .25 if state['id'] == 0 else .75
        if self.bad_reverse and state['id'] == 1:
            result[2][0].array[:] = np.nan
        return result

    def add_new_mask(self, state, **kwargs):
        self.events.append(('mask', state['id']))
        state['conditioning'] = 'binary_reinjection'

    def reset_state(self, state):
        self.events.append(('reset', state['id']))
        super().reset_state(state)

    def propagate_in_video(self, state, *, start_frame_idx, reverse):
        self.events.append(('reverse' if reverse else 'forward', state['id'], state['conditioning']))
        if reverse and start_frame_idx == 0:
            # Comportamiento real upstream: no hay secuencia reverse desde 0.
            self.directions.append(True)
            return
        yield from super().propagate_in_video(state, start_frame_idx=start_frame_idx, reverse=reverse)


class NativeInputTests(unittest.TestCase):
    """Solo transformación de imágenes con Torch real; no modelo ni pesos."""
    @classmethod
    def setUpClass(cls):
        try:
            import torch
        except ImportError:
            raise unittest.SkipTest('Input parity checks require the optional Torch runtime.')
        cls.torch = torch

    def test_upstream_uint8_matches_pinned_loader_order_on_non_square_field(self):
        from PIL import Image
        hu = np.linspace(-240, 320, 77, dtype=np.float32).reshape(1, 7, 11)
        valid = np.ones(hu.shape, bool); valid[0, 3, 5] = False
        before_hu, before_valid = hu.copy(), valid.copy()
        pixels = np.clip((hu[0] + 160) / 400, 0, 1)
        pixels[~valid[0]] = 0
        pixels = (pixels*255).astype(np.uint8)
        # Loader oficial: RGB/PIL -> array float64 -> /255 -> ImageNet;
        # _get_image_feature convierte ese resultado a float32.
        rgb = Image.fromarray(pixels).convert('RGB').resize((512, 512), Image.Resampling.BICUBIC)
        official_array = np.zeros((1, 3, 512, 512))
        official_array[0] = np.asarray(rgb).transpose(2, 0, 1)
        expected = self.torch.from_numpy(official_array / 255.0)
        expected -= self.torch.tensor([.485, .456, .406], dtype=self.torch.float32)[:, None, None]
        expected /= self.torch.tensor([.229, .224, .225], dtype=self.torch.float32)[:, None, None]
        actual = _NativeFrames(hu, valid, self.torch, input_mode='upstream_uint8')[0]
        self.assertEqual(tuple(actual.shape), (3, 512, 512))
        self.assertEqual(actual.dtype, self.torch.float32)
        self.assertTrue(self.torch.equal(actual, expected[0].float()))
        actual.zero_()
        np.testing.assert_array_equal(hu, before_hu)
        np.testing.assert_array_equal(valid, before_valid)

    def test_uint8_extremes_truncation_channels_and_padding_without_resize(self):
        hu = np.full((1, 512, 512), 40, dtype=np.float32)
        hu[0, 0, :5] = [-1000, -160, -60, 240, 2000]
        valid = np.ones(hu.shape, bool); valid[0, 0, 4] = False
        with patch('PIL.Image.Image.resize', side_effect=AssertionError('Native512 must not resize.')):
            actual = _NativeFrames(hu, valid, self.torch, input_mode='upstream_uint8')[0].numpy()
        mean = np.array([.485, .456, .406], np.float32).astype(np.float64)
        std = np.array([.229, .224, .225], np.float32).astype(np.float64)
        gray = np.array([0, 0, 63/255, 1, 0], np.float64)  # 63.75 is truncated.
        expected = ((gray[None]-mean[:, None])/std[:, None]).astype(np.float32)
        np.testing.assert_array_equal(actual[:, 0, :5], expected)
        self.assertTrue(np.isfinite(actual).all())

    def test_default_keeps_unquantized_float_bilinear_behavior(self):
        hu = np.array([[[-200, -60, 30], [40, 200, 300]]], dtype=np.float32)
        plane = np.clip((hu[0]+160)/400, 0, 1)
        expected = self.torch.nn.functional.interpolate(self.torch.from_numpy(plane)[None, None],
            size=(512, 512), mode='bilinear', align_corners=False)[0].repeat(3, 1, 1)
        expected = ((expected-self.torch.tensor([.485, .456, .406])[:, None, None])
                    / self.torch.tensor([.229, .224, .225])[:, None, None])
        actual = _NativeFrames(hu, None, self.torch)[0]
        self.assertTrue(self.torch.equal(actual, expected))
        upstream = _NativeFrames(hu, None, self.torch, input_mode='upstream_uint8')[0]
        self.assertFalse(self.torch.equal(actual, upstream))

    def test_input_transform_failure_releases_model_lock(self):
        class ReadingPredictor(Predictor):
            def init_state(self, frames, height, width):
                frames[0]
                return super().init_state(frames, height, width)
        hu = np.full((3, 8, 10), 40, np.float32)
        location = {'post_center_zyx': [1, 3, 4], 'localized_box_zyx': [[0, 1, 2], [3, 6, 8]],
                    'roi_zyx': [[0, 0, 0], [3, 8, 10]]}
        with patch('hcclab.post_model._get_predictor', return_value=(ReadingPredictor(), self.torch, {})), \
             patch('hcclab.post_model.version', return_value='test'):
            with patch('PIL.Image.Image.resize', side_effect=OSError('Synthetic resize failure')):
                with self.assertRaises(ModelSegmentationError) as caught:
                    segment_post_model(hu, [2.5, .5, 1], location, checkpoint_path='/unused',
                                       input_mode='upstream_uint8', refine_boundary=False)
            self.assertEqual(caught.exception.diagnostics['reason'], 'inference_failed')
            mask, _ = segment_post_model(hu, [2.5, .5, 1], location, checkpoint_path='/unused',
                                        input_mode='upstream_uint8', refine_boundary=False)
            self.assertTrue(mask.any())


class PostModelTests(unittest.TestCase):
    def setUp(self):
        self.hu = np.full((7, 8, 10), 55, dtype=np.float32)
        self.location = {'post_center_zyx':[3, 3, 4], 'localized_box_zyx':[[2,1,2],[5,6,8]],
                         'roi_zyx': [[1,0,0],[6,8,10]]}

    def run_model(self, predictor, **kwargs):
        kwargs.setdefault('refine_boundary', False)
        fake_torch = SimpleNamespace(__version__='test', inference_mode=nullcontext)
        with patch('hcclab.post_model._get_predictor', return_value=(predictor, fake_torch, {'checkpoint':'test'})), \
             patch('hcclab.post_model.version', return_value='test'):
            return segment_post_model(self.hu, [2.5, .7, .8], self.location, checkpoint_path='/unused', **kwargs)

    def test_all_native_slices_are_processed_beyond_pre_box(self):
        predictor = Predictor()
        mask, info = self.run_model(predictor)
        self.assertEqual(mask.shape, self.hu.shape)
        self.assertEqual(mask.dtype, np.bool_)
        self.assertTrue(mask[0].any() and mask[-1].any())
        self.assertEqual(info['slices_processed'], 7)
        self.assertEqual(info['voxels'], 28)
        self.assertEqual(predictor.directions, [False, True])
        np.testing.assert_array_equal(predictor.box, [2, 1, 7, 5])
        self.assertEqual(info['status'], 'candidate_unreviewed')
        self.assertEqual(info['localization_consistency']['outside_voxels'], 8)
        self.assertFalse(info['localization_consistency']['mask_clipped_to_region'])

    def test_invalid_input_modes_fail_before_loading_predictor(self):
        for mode in (None, True, [], 1, 'bicubic', 'UPSTREAM_UINT8'):
            with self.subTest(mode=mode), patch('hcclab.post_model._get_predictor') as load:
                with self.assertRaisesRegex(ValueError, 'Input mode'):
                    segment_post_model(self.hu, [2.5, .7, .8], self.location,
                                       checkpoint_path='/unused', input_mode=mode)
                with self.assertRaisesRegex(ValueError, 'Input mode'):
                    _NativeFrames(self.hu, None, object(), input_mode=mode)
                load.assert_not_called()

    def test_upstream_input_preserves_native_field_box_and_geometry_contract(self):
        self.crop_fixture()
        predictor = FieldPredictor()
        mask, info = self.run_crop_model(predictor, input_mode='upstream_uint8')
        self.assertEqual(mask.shape, self.hu.shape)
        self.assertEqual(predictor.received_shapes, [(160, 80), (160, 80)])
        self.assertEqual(predictor.frames.input_mode, 'upstream_uint8')
        self.assertEqual(info['inference_field_yx'], [[50, 115], [210, 195]])
        self.assertEqual(info['prompt_box_xyxy'], [150, 125, 160, 135])
        self.assertEqual(info['model_prompt_box_xyxy'], [35, 75, 45, 85])
        self.assertEqual(info['settings']['input_resize'], 'pil_rgb_bicubic_if_needed')
        self.assertEqual(info['settings']['input_quantization'], 'clip_window_times_255_truncate_uint8')
        self.assertEqual(info['settings']['input_normalization_precision'], 'float64_then_float32')
        self.assertEqual(info['settings']['window_level_hu'], 40)
        self.assertEqual(info['settings']['window_width_hu'], 400)
        self.assertTrue(info['settings']['model_memory_settings_overridden'] is False)
        json.dumps(info, allow_nan=False)

    def test_original_anchor_scores_and_field_artifacts_are_preserved(self):
        class AnchorPredictor(Predictor):
            def add_new_points_or_box(self, state, **kwargs):
                index, ids, logits = super().add_new_points_or_box(state, **kwargs)
                logits[0].array *= .25
                return index, ids, logits

        captured = {}
        mask, info = self.run_model(AnchorPredictor(), intermediates=captured)
        np.testing.assert_array_equal(captured['raw_mask'], mask)
        np.testing.assert_array_equal(captured['logits'][3], np.where(mask[3], .25, -.25))
        np.testing.assert_array_equal(captured['logits'][2], np.where(mask[2], 1, -1))
        self.assertEqual(captured['logits'].dtype, np.float32)
        self.assertEqual(info['raw_prediction']['anchor_scores'], 'original_box_conditioned_logits')
        captured['raw_mask'][:] = False
        self.assertTrue(mask.any())

    def test_default_keeps_binary_reinjection_and_original_forward_box_scores(self):
        predictor, captured = ConditioningPredictor(), {}
        mask, info = self.run_model(predictor, intermediates=captured)
        self.assertEqual(predictor.events, [
            ('init', 0), ('box', 0), ('mask', 0), ('forward', 0, 'binary_reinjection'),
            ('reset', 0), ('init', 1), ('mask', 1), ('reverse', 1, 'binary_reinjection'), ('reset', 1)])
        np.testing.assert_array_equal(captured['logits'][3], np.where(mask[3], .25, -.25))
        self.assertEqual(info['prompt_conditioning'], 'binary_reinjection')
        self.assertEqual(info['settings']['propagation_conditioning'], 'binary_mask_from_box_prediction')
        self.assertTrue(predictor.binarize_mask_from_pts_for_mem_enc)

    def test_box_mode_conditions_both_fresh_states_without_binary_roundtrip(self):
        predictor, captured = ConditioningPredictor(), {}
        mask, info = self.run_model(predictor, prompt_mode='box', intermediates=captured)
        self.assertEqual(predictor.events, [
            ('init', 0), ('box', 0), ('forward', 0, 'box'),
            ('reset', 0), ('init', 1), ('box', 1), ('reverse', 1, 'box'), ('reset', 1)])
        self.assertIsNot(predictor.states[0], predictor.states[1])
        self.assertEqual(len(predictor.boxes), 2)
        np.testing.assert_array_equal(predictor.boxes[0], predictor.boxes[1])
        # El segundo ancla simulado vale .75: se conserva el primero, .25.
        np.testing.assert_array_equal(captured['logits'][3], np.where(mask[3], .25, -.25))
        np.testing.assert_array_equal(captured['logits'][2], np.where(mask[2], 1, -1))
        self.assertEqual(info['slices_processed'], len(mask))
        self.assertEqual(info['prompt_conditioning'], 'box')
        self.assertEqual(info['settings']['prompt_mode'], 'box')
        self.assertEqual(info['settings']['propagation_conditioning'], 'registered_box')
        self.assertFalse(info['settings']['model_memory_settings_overridden'])
        self.assertTrue(predictor.binarize_mask_from_pts_for_mem_enc)
        json.dumps(info, allow_nan=False)

    def test_box_mode_keeps_anisotropic_field_offsets(self):
        self.crop_fixture()
        predictor = ConditioningPredictor()
        mask, info = self.run_crop_model(predictor, prompt_mode='box')
        for box in predictor.boxes:
            np.testing.assert_array_equal(box, [35, 75, 45, 85])
        self.assertEqual(info['prompt_box_xyxy'], [150, 125, 160, 135])
        self.assertEqual(info['inference_field_yx'], [[50, 115], [210, 195]])
        expected = np.zeros_like(self.hu, dtype=bool)
        expected[:, 52:54, 118:120] = True
        np.testing.assert_array_equal(mask, expected)

    def test_first_and_last_anchor_cover_all_slices_in_both_modes(self):
        for mode in ('binary_reinjection', 'box'):
            for anchor in (0, len(self.hu)-1):
                with self.subTest(mode=mode, anchor=anchor):
                    self.location['post_center_zyx'][0] = anchor
                    self.location['localized_box_zyx'][0][0] = max(0, anchor-1)
                    self.location['localized_box_zyx'][1][0] = min(len(self.hu), anchor+2)
                    self.location['roi_zyx'] = [[0,0,0], list(self.hu.shape)]
                    predictor = ConditioningPredictor()
                    mask, info = self.run_model(predictor, prompt_mode=mode)
                    self.assertTrue(mask[0].any() and mask[-1].any())
                    self.assertEqual(info['slices_processed'], len(self.hu))
                    self.assertEqual(predictor.reset_count, 2)

    def test_invalid_prompt_mode_fails_before_model_loading(self):
        for mode in (None, True, 'mask', 'BOX', [], {}):
            with self.subTest(mode=mode), patch('hcclab.post_model._get_predictor') as load:
                with self.assertRaisesRegex(ValueError, 'Prompt mode'):
                    segment_post_model(self.hu, [2.5,.7,.8], self.location,
                                       checkpoint_path='/unused', prompt_mode=mode)
                load.assert_not_called()

    def test_invalid_reverse_box_output_fails_and_releases_state_and_lock(self):
        predictor = ConditioningPredictor(bad_reverse=True)
        with self.assertRaises(ModelSegmentationError) as caught:
            self.run_model(predictor, prompt_mode='box')
        self.assertEqual(caught.exception.diagnostics['reason'], 'invalid_model_output')
        self.assertEqual(predictor.reset_count, 2)
        self.assertTrue(self.run_model(ConditioningPredictor(), prompt_mode='box')[0].any())

    def test_refinement_uses_only_inferred_field_and_records_its_changes(self):
        self.crop_fixture()
        artifacts = {}
        valid = np.ones(self.hu.shape, bool)

        def refine(hu, raw, scores, spacing, *, valid_mask):
            np.testing.assert_array_equal(hu, self.hu[:,50:210,115:195])
            np.testing.assert_array_equal(raw, (scores > 0) & valid_mask)
            np.testing.assert_array_equal(valid_mask, valid[:,50:210,115:195])
            self.assertEqual(spacing, [2.5,1,2])
            proposal = raw.copy(); proposal[:,3,4] = False
            return proposal, {'outcome': 'refined', 'removed_voxels': 7}

        with patch('hcclab.post_boundary.refine_post_boundary', side_effect=refine) as cut:
            mask, info = self.run_crop_model(FieldPredictor(), valid_mask=valid,
                                             refine_boundary=True, intermediates=artifacts)
        cut.assert_called_once()
        self.assertEqual(info['voxels'], 21)
        self.assertEqual(info['raw_prediction']['voxels'], 28)
        self.assertEqual(artifacts['raw_mask'].sum(), 28)
        self.assertFalse(mask[:,53,119].any())
        self.assertEqual(info['boundary_refinement']['removed_voxels'], 7)
        json.dumps(info, allow_nan=False)

    def test_refinement_failure_is_explicit_without_silent_raw_fallback(self):
        with patch('hcclab.post_boundary.refine_post_boundary', side_effect=ValueError('Invalid synthetic scores.')):
            with self.assertRaises(ModelSegmentationError) as caught:
                self.run_model(Predictor(), refine_boundary=True)
        self.assertEqual(caught.exception.diagnostics['reason'], 'boundary_refinement_failed')
        self.assertEqual(caught.exception.diagnostics['boundary_refinement']['status'], 'failed')
        self.assertTrue(self.run_model(Predictor())[0].any())

    def test_empty_refinement_abstains_before_localization_fraction(self):
        with patch('hcclab.post_boundary.refine_post_boundary', return_value=(
                np.zeros_like(self.hu, dtype=bool), {'outcome': 'refined'})):
            with self.assertRaises(ModelSegmentationError) as caught:
                self.run_model(Predictor(), refine_boundary=True)
        self.assertEqual(caught.exception.diagnostics['reason'], 'empty_refinement')

    def test_refinement_band_needs_complete_artificial_field_context(self):
        self.crop_fixture()
        def edge_band(hu, raw, scores, spacing, **kwargs):
            return raw.copy(), {'editable_band_touch_faces': ['y_min'], 'outcome': 'unchanged'}
        with patch('hcclab.post_boundary.refine_post_boundary', side_effect=edge_band):
            with self.assertRaises(ModelSegmentationError) as caught:
                self.run_crop_model(FieldPredictor(), refine_boundary=True)
        self.assertEqual(caught.exception.diagnostics['reason'], 'incomplete_boundary_context')

    def test_majority_outside_search_region_abstains_without_trimming_or_lock_leak(self):
        self.location['roi_zyx'] = [[2,1,2],[5,6,8]]
        with self.assertRaises(ModelSegmentationError) as caught:
            self.run_model(Predictor())
        info = caught.exception.diagnostics
        self.assertEqual(info['reason'], 'localization_propagation_mismatch')
        qc = info['localization_consistency']
        self.assertEqual((qc['inside_voxels'], qc['outside_voxels']), (12, 16))
        self.assertEqual(qc['outside_fraction'], 4/7)
        self.assertEqual(qc['status'], 'failed')
        self.assertFalse(qc['mask_clipped_to_region'])
        self.location['roi_zyx'] = [[0,0,0],[7,8,10]]
        self.assertEqual(int(self.run_model(Predictor())[0].sum()), 28)

    def test_half_outside_is_not_a_majority_and_output_remains_native(self):
        self.hu = self.hu[:6]
        self.location['roi_zyx'] = [[2,1,2],[5,6,8]]
        mask, info = self.run_model(Predictor())
        self.assertEqual(info['localization_consistency']['outside_fraction'], .5)
        self.assertEqual(int(mask.sum()), 24)
        self.assertTrue(mask[0].any() and mask[-1].any())

    def test_invalid_search_regions_abstain_before_loading_model(self):
        for roi in (None, [[0,0,0],[8,8,10]], [[0,0,0],[6.5,8,10]],
                    [[0,0,0],[6,float('nan'),10]], [[3,1,2],[5,6,8]],
                    [[-1,0,0],[7,8,10]], [[0,0],[6,8]], [[0,0,0],[4,8,10]]):
            with self.subTest(roi=roi), patch('hcclab.post_model._get_predictor') as load:
                self.location['roi_zyx'] = roi
                with self.assertRaises(ModelSegmentationError) as caught:
                    segment_post_model(self.hu, [2.5,.7,.8], self.location, checkpoint_path='/unused')
                self.assertEqual(caught.exception.diagnostics['reason'], 'invalid_search_region')
                load.assert_not_called()

    def test_padding_is_excluded_and_counted_without_mutating_input(self):
        valid = np.ones(self.hu.shape, dtype=bool)
        valid[:, 2, 3] = False
        original = self.hu.copy()
        mask, info = self.run_model(Predictor(), valid_mask=valid)
        self.assertFalse(mask[:, 2, 3].any())
        self.assertEqual(info['excluded_padding_voxels'], 7)
        np.testing.assert_array_equal(original, self.hu)

    def test_incomplete_propagation_abstains_and_releases_lock(self):
        with self.assertRaises(ModelSegmentationError) as caught:
            self.run_model(Predictor(incomplete=True))
        self.assertEqual(caught.exception.diagnostics['reason'], 'incomplete_propagation')
        self.assertTrue(self.run_model(Predictor())[0].any())

    def test_empty_prompt_abstains(self):
        with self.assertRaises(ModelSegmentationError) as caught:
            self.run_model(Predictor(empty=True))
        self.assertEqual(caught.exception.diagnostics['reason'], 'empty_prompt')

    def test_out_of_bounds_prompt_rejected_before_model_load(self):
        self.location['localized_box_zyx'][1][2] = 11
        with patch('hcclab.post_model._get_predictor') as load:
            with self.assertRaises(ModelSegmentationError):
                segment_post_model(self.hu, [2.5,.7,.8], self.location, checkpoint_path='/unused')
            load.assert_not_called()

    def test_incompatible_validity_rejected(self):
        with self.assertRaises(ModelSegmentationError):
            self.run_model(Predictor(), valid_mask=np.ones((1,2,3), dtype=bool))

    def test_setup_error_has_structured_diagnostics_without_local_paths(self):
        with patch('hcclab.post_model._get_predictor', side_effect=RuntimeError('/private/model/config')):
            with self.assertRaises(ModelSegmentationError) as caught:
                segment_post_model(self.hu, [2.5,.7,.8], self.location, checkpoint_path='/unused')
        self.assertEqual(caught.exception.diagnostics['reason'], 'model_unavailable')
        self.assertEqual(caught.exception.diagnostics['error_type'], 'RuntimeError')
        self.assertNotIn('/private', str(caught.exception))
        self.assertTrue(self.run_model(Predictor())[0].any())

    def crop_fixture(self):
        self.hu = np.arange(7*220*260, dtype=np.float32).reshape(7, 220, 260)
        self.location = {'post_center_zyx': [3,130,155],
                         'localized_box_zyx': [[2,125,150],[5,136,161]],
                         'roi_zyx': [[0,0,0],[7,220,260]]}

    def run_crop_model(self, predictor, **kwargs):
        kwargs.setdefault('refine_boundary', False)
        fake_torch = SimpleNamespace(__version__='test', inference_mode=nullcontext)
        with patch('hcclab.post_model._get_predictor', return_value=(predictor, fake_torch, {'checkpoint':'test'})), \
             patch('hcclab.post_model.version', return_value='test'):
            return segment_post_model(self.hu, [2.5,1,2], self.location, checkpoint_path='/unused', **kwargs)

    def test_anisotropic_crop_offsets_boxes_padding_and_all_native_slices(self):
        self.crop_fixture()
        predictor = FieldPredictor()
        valid = np.ones(self.hu.shape, bool); valid[:,52,118] = False
        original_hu, original_valid = self.hu.copy(), valid.copy()
        original_location = copy.deepcopy(self.location)
        mask, info = self.run_crop_model(predictor, valid_mask=valid)
        # 160 mm becomes 160 rows at dy=1 and 80 columns at dx=2.
        self.assertEqual(predictor.received_shapes, [(160,80), (160,80)])
        self.assertEqual(info['inference_field_yx'], [[50,115],[210,195]])
        self.assertEqual(info['inference_field_shape_yx'], [160,80])
        np.testing.assert_array_equal(predictor.frames.hu, self.hu[:,50:210,115:195])
        np.testing.assert_array_equal(predictor.frames.valid, valid[:,50:210,115:195])
        self.assertEqual(info['prompt_box_xyxy'], [150,125,160,135])
        self.assertEqual(info['model_prompt_box_xyxy'], [35,75,45,85])
        np.testing.assert_array_equal(predictor.box, [35,75,45,85])
        expected = np.zeros(self.hu.shape, bool)
        expected[:,52:54,118:120] = True
        expected &= valid
        np.testing.assert_array_equal(mask, expected)
        self.assertTrue(mask[0].any() and mask[-1].any())
        self.assertEqual(info['voxels'], 21)
        self.assertEqual(info['excluded_padding_voxels'], 7)
        self.assertEqual(info['outside_inference_field'], 'not_assessed')
        self.assertEqual(info['internal_field_touch_faces'], [])
        np.testing.assert_array_equal(self.hu, original_hu)
        np.testing.assert_array_equal(valid, original_valid)
        self.assertEqual(self.location, original_location)
        self.assertFalse(np.shares_memory(mask, self.hu))
        json.dumps(info, allow_nan=False)

    def test_internal_crop_edges_abstain_without_saving_truncated_mask_or_lock_leak(self):
        self.crop_fixture()
        for edge, face in (((0,4),'y_min'), ((-1,4),'y_max'), ((3,0),'x_min'), ((3,-1),'x_max')):
            with self.subTest(face=face):
                predictor = FieldPredictor(edge)
                with self.assertRaises(ModelSegmentationError) as caught:
                    self.run_crop_model(predictor)
                info = caught.exception.diagnostics
                self.assertEqual(info['reason'], 'incomplete_input_field')
                self.assertEqual(info['internal_field_touch_faces'], [face])
                self.assertEqual(predictor.reset_count, 2)
                self.assertNotIn('voxels', info)
        self.assertTrue(self.run_crop_model(FieldPredictor())[0].any())

    def test_native_acquisition_edge_is_warning_not_artificial_field_failure(self):
        self.crop_fixture()
        self.location['post_center_zyx'][1] = 5
        self.location['localized_box_zyx'] = [[2,1,150],[5,11,161]]
        mask, info = self.run_crop_model(FieldPredictor((0,4)))
        self.assertTrue(mask[:,0].any())
        self.assertEqual(info['internal_field_touch_faces'], [])
        self.assertIn('y_min', info['acquisition_touch_faces'])
        self.assertEqual(info['status'], 'candidate_unreviewed')

    def test_invalid_field_center_or_boolean_bounds_rejected_before_model_load(self):
        self.crop_fixture()
        original = copy.deepcopy(self.location)
        for change in ('outside_xy_center', 'boolean_bound'):
            with self.subTest(change=change), patch('hcclab.post_model._get_predictor') as load:
                self.location = copy.deepcopy(original)
                if change == 'outside_xy_center':
                    self.location['post_center_zyx'][1] = 140
                else:
                    self.location['localized_box_zyx'][0][0] = True
                with self.assertRaises(ModelSegmentationError) as caught:
                    segment_post_model(self.hu, [2.5,1,2], self.location, checkpoint_path='/unused')
                self.assertEqual(caught.exception.diagnostics['reason'], 'invalid_input_field')
                load.assert_not_called()


if __name__ == '__main__':
    unittest.main()
