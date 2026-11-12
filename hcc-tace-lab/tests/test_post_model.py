"""No weights needed: verify native geometry and propagation orchestration."""
from contextlib import nullcontext
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from hcclab.post_model import ModelSegmentationError, segment_post_model


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


class PostModelTests(unittest.TestCase):
    def setUp(self):
        self.hu = np.full((7, 8, 10), 55, dtype=np.float32)
        self.location = {'post_center_zyx':[3, 3, 4], 'localized_box_zyx':[[2,1,2],[5,6,8]]}

    def run_model(self, predictor, **kwargs):
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


if __name__ == '__main__':
    unittest.main()
