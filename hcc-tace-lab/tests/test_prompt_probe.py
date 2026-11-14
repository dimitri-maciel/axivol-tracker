"""Model-free checks that instrumentation observes but never selects a mask."""
import unittest

import numpy as np

from experiments.prompt_probe import capture_prompt_alternatives


class Tensor:
    def __init__(self, values):
        self.values = np.asarray(values)

    @property
    def shape(self):
        return self.values.shape

    def __getitem__(self, index):
        return Tensor(self.values[index])

    def detach(self):
        return self

    def cpu(self):
        return self

    def numpy(self):
        return self.values


class Predictor:
    fill_hole_area = 0
    binarize_mask_from_pts_for_mem_enc = True
    multimask_output_for_tracking = True

    def _get_orig_video_res_output(self, state, masks):
        return masks, masks

    def _forward_sam_heads(self, backbone_features, point_inputs=None,
                           mask_inputs=None, multimask_output=False):
        alternatives = np.array([[[[1, 1, 1], [-1, -1, -1]],
                                  [[1, -1, 1], [-1, -1, -1]],
                                  [[-1, -1, -1], [-1, -1, -1]]]], dtype=np.float32)
        if not multimask_output:
            alternatives = alternatives[:, :1]
        scores = np.array([[.1, .9, .3] if multimask_output else [.1]])
        selected = alternatives[:, 1:2] if multimask_output else alternatives
        return (Tensor(alternatives), Tensor(alternatives), Tensor(scores),
                Tensor(selected), Tensor(selected), None, Tensor([[2.]]))

    def _run_single_frame_inference(self, inference_state, frame_idx, point_inputs=None,
                                    mask_inputs=None, reverse=False, is_init_cond_frame=False):
        result = self._forward_sam_heads(None, point_inputs=point_inputs, mask_inputs=mask_inputs,
                                         multimask_output=mask_inputs is None)
        chosen = result[3] if mask_inputs is None else Tensor(np.full((1, 1, 2, 3), 10.))
        self.last_return = ({'unchanged': True}, chosen)
        return self.last_return


class PromptProbeTests(unittest.TestCase):
    def test_records_all_alternatives_selected_index_and_exact_output_without_changes(self):
        p = Predictor()
        with capture_prompt_alternatives(p, valid_field=np.ones((4, 2, 3), bool), keep_frames={2}) as probe:
            result = p._run_single_frame_inference({}, 2, reverse=True)
            self.assertIs(result, p.last_return)
            p._run_single_frame_inference({}, 1)
        self.assertEqual(len(probe.records), 2)
        row = probe.records[0]
        self.assertEqual((row['array_index'], row['app_slice'], row['selected_alternative_index']), (2, 3, 1))
        self.assertEqual(row['predicted_iou'], [.1, .9, .3])
        self.assertEqual(row['alternatives'][1]['components_4'], 2)
        self.assertTrue(row['selected_matches_real_frame_output'])
        self.assertEqual(len(probe.arrays), 1)
        self.assertEqual(probe.arrays[row['array_key']].shape, (3, 2, 3))
        self.assertNotIn('_forward_sam_heads', p.__dict__)
        self.assertNotIn('_run_single_frame_inference', p.__dict__)
        self.assertTrue(p.binarize_mask_from_pts_for_mem_enc)
        self.assertTrue(p.multimask_output_for_tracking)

    def test_binary_anchor_pointer_is_not_misrepresented_as_output_selection(self):
        p = Predictor()
        with capture_prompt_alternatives(p) as probe:
            actual = p._run_single_frame_inference({}, 2, mask_inputs=object())
        self.assertEqual(probe.records[0]['phase'], 'binary_anchor_pointer')
        self.assertEqual(probe.records[0]['selected_matches_real_frame_output'], 'not_applicable_pointer_only')
        self.assertTrue((actual[1].values == 10).all())

    def test_selected_padding_is_counted_but_stored_logits_remain_unmodified(self):
        p = Predictor(); valid = np.ones((3, 2, 3), bool); valid[2, 0, 0] = False
        with capture_prompt_alternatives(p, valid_field=valid) as probe:
            p._run_single_frame_inference({}, 2)
        row = probe.records[0]
        self.assertEqual(row['alternatives'][1]['foreground_pixels'], 1)
        self.assertEqual(row['alternatives'][1]['foreground_on_padding'], 1)
        self.assertGreater(probe.arrays[row['array_key']][1, 0, 0], 0)
        self.assertFalse(valid[2, 0, 0])

    def test_failure_restores_existing_instance_override_and_blocks_nested_probe(self):
        p = Predictor()
        def broken(inference_state, frame_idx, point_inputs=None, mask_inputs=None,
                   reverse=False, is_init_cond_frame=False):
            raise RuntimeError('synthetic failure')
        p._run_single_frame_inference = broken
        with self.assertRaisesRegex(RuntimeError, 'synthetic'):
            with capture_prompt_alternatives(p):
                with self.assertRaisesRegex(ValueError, 'already active'):
                    with capture_prompt_alternatives(p):
                        pass
                p._run_single_frame_inference({}, 1)
        self.assertIs(p._run_single_frame_inference, broken)
        self.assertNotIn('_forward_sam_heads', p.__dict__)

    def test_hole_filling_and_geometry_mismatch_are_explicit(self):
        p = Predictor(); p.fill_hole_area = 8
        with self.assertRaisesRegex(ValueError, 'no-hole-filling'):
            with capture_prompt_alternatives(p):
                pass
        p.fill_hole_area = 0
        with self.assertRaisesRegex(ValueError, 'geometry'):
            with capture_prompt_alternatives(p, valid_field=np.ones((3, 4, 5), bool)):
                p._run_single_frame_inference({}, 1)
        self.assertNotIn('_run_single_frame_inference', p.__dict__)


if __name__ == '__main__':
    unittest.main()
