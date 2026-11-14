"""MedSAM original: contratos nativos y paridad de entrada sin inferencia pesada."""
import hashlib
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import numpy as np

from experiments import medsam_slice_refine as adapter


class SliceRefinementTests(unittest.TestCase):
    def setUp(self):
        self.hu = np.linspace(-500, 500, 6*12*16, dtype=np.float32).reshape(6, 12, 16)
        self.initial = np.zeros(self.hu.shape, bool)
        self.initial[1, 3, 4] = self.initial[2, 4, 5] = self.initial[3, 5, 6] = True
        self.spacing = [2.5, 2., 1.]
        self.valid = np.ones(self.hu.shape, bool)

    def run_adapter(self, predict, **overrides):
        arguments = dict(post_hu=self.hu, initial_mask=self.initial, spacing_zyx=self.spacing,
                         valid_mask=self.valid, checkpoint_path='/unused', margin_mm=1.)
        arguments.update(overrides)
        with patch.object(adapter, '_load_model', return_value=(object(), {'synthetic': True})), \
             patch.object(adapter, '_prepare_image', return_value=np.zeros((1, 3, 2, 2), np.float32)), \
             patch.object(adapter, '_predict_slice', side_effect=predict), \
             patch.object(adapter, 'verify_assets'):
            return adapter.refine_with_medsam(**arguments)

    def test_independent_neighbor_boxes_all_components_and_native_scope(self):
        original = self.initial.copy(); original_hu = self.hu.copy(); original_valid = self.valid.copy()
        boxes = []
        def predict(model, prepared, box, shape):
            boxes.append(box.copy())
            prediction = np.zeros(shape, np.float32)
            prediction[0, 0] = .75  # Fuera de caja: no recortar ni borrar componente.
            prediction[9, 14] = .9
            return prediction
        intermediates = {}
        result, info = self.run_adapter(predict, intermediates=intermediates)
        self.assertEqual(len(boxes), 3)
        np.testing.assert_array_equal(boxes[1], [3., 2.5, 7., 5.5])
        # La salida z1 en (0,0) no entra en la caja z2: se usa solo baseline.
        self.assertTrue(all(box[0] > 0 for box in boxes))
        np.testing.assert_array_equal(result[[0, 4, 5]], original[[0, 4, 5]])
        self.assertTrue(result[1:4, 0, 0].all() and result[1:4, 9, 14].all())
        self.assertEqual(info['coverage']['processed_slice_indices'], [1, 2, 3])
        self.assertIn('not_reevaluated', info['coverage']['unprocessed_planes'])
        self.assertEqual(info['changed_slices'], [1, 2, 3])
        self.assertEqual(info['added_voxels'], 6)
        self.assertEqual(info['removed_voxels'], 3)
        self.assertAlmostEqual(info['volume_cm3'], 6*2.5*2/1000)
        np.testing.assert_array_equal(intermediates['slice_indices'], [1, 2, 3])
        self.assertEqual(intermediates['probabilities'].shape, (3, 12, 16))
        self.assertEqual(intermediates['probabilities'].dtype, np.float32)
        np.testing.assert_array_equal(info['frames'][1]['box_xyxy_1024'], boxes[1]/[16, 12, 16, 12]*1024)
        np.testing.assert_array_equal(self.initial, original)
        np.testing.assert_array_equal(self.hu, original_hu)
        np.testing.assert_array_equal(self.valid, original_valid)
        result[:] = False
        np.testing.assert_array_equal(self.initial, original)
        json.dumps(info, allow_nan=False)

    def test_first_last_plane_boxes_use_existing_neighbors_and_clip_margin_only(self):
        mask = np.zeros((3, 5, 7), bool)
        mask[0, 0, 0] = mask[-1, -1, -1] = True
        box0, faces0 = adapter._box_for_slice(mask, 0, [3, 2, 1], 5.)
        box2, faces2 = adapter._box_for_slice(mask, 2, [3, 2, 1], 5.)
        np.testing.assert_array_equal(box0, [0, 0, 5, 2.5])
        np.testing.assert_array_equal(box2, [1, 1.5, 7, 5])
        self.assertEqual(faces0, ['x_min', 'y_min'])
        self.assertEqual(faces2, ['x_max', 'y_max'])

    def test_threshold_is_strict_and_empty_output_is_not_confirmed_absence(self):
        result, info = self.run_adapter(lambda *args: np.full(self.hu.shape[1:], .5, np.float32))
        self.assertFalse(result.any())
        self.assertEqual(info['outcome'], 'empty_prediction_not_confirmed_absence')
        self.assertIsNone(info['display_slice_index'])

    def test_predicted_padding_is_rejected_without_clipping_or_partial_intermediates(self):
        self.valid[2, 0, 0] = False
        intermediates = {}
        def predict(model, prepared, box, shape):
            plane = np.zeros(shape, np.float32); plane[0, 0] = 1.
            return plane
        with self.assertRaisesRegex(adapter.MedSAMRefinementError, 'invalid pixel padding') as caught:
            self.run_adapter(predict, intermediates=intermediates)
        self.assertEqual(caught.exception.diagnostics['status'], 'failed')
        self.assertEqual(intermediates, {})

    def test_nonfinite_out_of_range_or_wrong_shape_predictions_fail(self):
        invalid = [np.full((12, 16), np.nan, np.float32), np.full((12, 16), np.inf, np.float32),
                   np.full((12, 16), 1.01, np.float32), np.full((12, 16), -.01, np.float32),
                   np.zeros((11, 16), np.float32), np.zeros((12, 16), bool)]
        for prediction in invalid:
            with self.subTest(shape=prediction.shape, dtype=prediction.dtype):
                with self.assertRaisesRegex(adapter.MedSAMRefinementError, 'probabilities must be finite'):
                    self.run_adapter(lambda *args: prediction)

    def test_readonly_inputs_and_explicit_unchanged_result(self):
        self.hu.flags.writeable = False; self.initial.flags.writeable = False
        index = iter([1, 2, 3])
        result, info = self.run_adapter(lambda *args: self.initial[next(index)].astype(np.float32))
        np.testing.assert_array_equal(result, self.initial)
        self.assertFalse(np.shares_memory(result, self.initial))
        self.assertEqual(info['outcome'], 'unchanged')

    def test_invalid_inputs_rejected_before_loading(self):
        nonfinite = self.hu.copy(); nonfinite[0, 0, 0] = np.nan
        invalid = [dict(post_hu=nonfinite), dict(post_hu=np.ones_like(self.hu)*1000),
                   dict(post_hu=self.hu[0]), dict(initial_mask=self.initial.astype(np.uint8)),
                   dict(initial_mask=np.zeros_like(self.initial)), dict(valid_mask=np.zeros_like(self.initial)),
                   dict(valid_mask=np.ones(self.hu.shape, np.uint8)), dict(spacing_zyx=[1, 0, 1]),
                   dict(spacing_zyx=[1, np.nan, 1]), dict(spacing_zyx=[True, 1, 1]),
                   dict(margin_mm=True), dict(margin_mm=0), dict(margin_mm=21), dict(margin_mm=1j),
                   dict(margin_mm=np.inf), dict(intermediates={'old': 'data'})]
        for override in invalid:
            with self.subTest(keys=override.keys()), patch.object(adapter, '_load_model') as load:
                with self.assertRaises(ValueError):
                    adapter.refine_with_medsam(**{'post_hu': self.hu, 'initial_mask': self.initial,
                        'spacing_zyx': self.spacing, 'checkpoint_path': '/unused', **override})
                load.assert_not_called()

    def test_volume_window_limits_are_global_not_per_plane(self):
        hu = np.array([[[-500, -20], [0, 20]], [[60, 100], [200, 500]]], np.float64)
        initial = np.ones(hu.shape, bool)
        _, limits = adapter._validate(hu, initial, [1, 1, 1], None, 5.)
        self.assertEqual(limits, (-160., 240.))
        _, limited = adapter._validate(hu.clip(-10, 100), initial, [1, 1, 1], None, 5.)
        self.assertEqual(limited, (-10., 100.))


@unittest.skipUnless(importlib.util.find_spec('skimage'), 'scikit-image belongs to the optional research environment')
class PreprocessingTests(unittest.TestCase):
    def test_matches_official_quantization_resize_and_normalization(self):
        from skimage.transform import resize
        plane = np.array([[-300, -150.2, 0], [15, 200, 400]], dtype=np.float32)
        before = plane.copy()
        image = ((np.clip(plane.astype(float), -160, 240)+160)/400*255).astype(np.uint8)
        rgb = np.repeat(image[:, :, None], 3, axis=-1)
        official = resize(rgb, (1024, 1024), order=3, preserve_range=True, anti_aliasing=True).astype(np.uint8)
        official = (official-official.min())/np.clip(official.max()-official.min(), 1e-8, None)
        actual = adapter._prepare_image(plane, (-160., 240.))
        np.testing.assert_array_equal(actual, official.transpose(2, 0, 1)[None].astype(np.float32))
        self.assertEqual(actual.shape, (1, 3, 1024, 1024))
        self.assertEqual(actual.dtype, np.float32)
        np.testing.assert_array_equal(actual[0, 0], actual[0, 1])
        np.testing.assert_array_equal(plane, before)

    def test_constant_plane_is_finite_and_not_skipped_or_given_fabricated_variation(self):
        prepared = adapter._prepare_image(np.full((4, 5), 30., np.float32), (-160., 240.))
        self.assertTrue(np.isfinite(prepared).all())
        self.assertFalse(prepared.any())


@unittest.skipUnless(importlib.util.find_spec('torch'), 'Torch belongs to the optional research environment')
class DecoderTests(unittest.TestCase):
    def test_sigmoid_before_native_interpolation_and_exact_box_geometry(self):
        import torch
        from torch.nn.functional import interpolate
        logits = torch.full((1, 1, 256, 256), -4.)
        logits[:, :, :, :128] = 1.
        prompt = Mock(return_value=(torch.zeros(1), torch.zeros(1)))
        prompt.get_dense_pe.return_value = torch.zeros(1)
        model = Mock(image_encoder=Mock(return_value=torch.zeros((1, 256, 64, 64))),
                     prompt_encoder=prompt, mask_decoder=Mock(return_value=(logits, torch.zeros(1))))
        prepared = np.zeros((1, 3, 1024, 1024), np.float32)
        box = np.array([3., 2.5, 7., 5.5])
        actual = adapter._predict_slice(model, prepared, box, (11, 513))
        expected = interpolate(logits.sigmoid(), (11, 513), mode='bilinear', align_corners=False)[0, 0].numpy()
        wrong_order = interpolate(logits, (11, 513), mode='bilinear', align_corners=False).sigmoid()[0, 0].numpy()
        np.testing.assert_array_equal(actual, expected)
        self.assertGreater(float(np.max(np.abs(actual-wrong_order))), .1)
        np.testing.assert_array_equal(prompt.call_args.kwargs['boxes'].numpy(),
            (box/[513, 11, 513, 11]*1024)[None, None].astype(np.float32))
        self.assertIsNone(prompt.call_args.kwargs['points'])
        self.assertIsNone(prompt.call_args.kwargs['masks'])
        self.assertFalse(model.mask_decoder.call_args.kwargs['multimask_output'])

    def test_nonfinite_decoder_logits_fail_before_sigmoid(self):
        import torch
        prompt = Mock(return_value=(torch.zeros(1), torch.zeros(1)))
        prompt.get_dense_pe.return_value = torch.zeros(1)
        model = Mock(image_encoder=Mock(return_value=torch.zeros(1)), prompt_encoder=prompt,
                     mask_decoder=Mock(return_value=(torch.full((1, 1, 256, 256), float('nan')), None)))
        with self.assertRaisesRegex(ValueError, 'finite single-mask logits'):
            adapter._predict_slice(model, np.zeros((1, 3, 1024, 1024), np.float32), np.array([1, 2, 3, 4]), (8, 9))


class AssetTests(unittest.TestCase):
    def test_checkpoint_hashes_size_and_symlink_are_checked(self):
        content = b'pinned synthetic weights'
        with TemporaryDirectory() as folder, \
             patch.object(adapter, 'CHECKPOINT_BYTES', len(content)), \
             patch.object(adapter, 'CHECKPOINT_SHA256', hashlib.sha256(content).hexdigest()), \
             patch.object(adapter, 'CHECKPOINT_MD5', hashlib.md5(content).hexdigest()):
            path = Path(folder)/'medsam.pth'; path.write_bytes(content)
            info = adapter.verify_assets(path)
            self.assertEqual(info['checkpoint']['sha256'], hashlib.sha256(content).hexdigest())
            path.write_bytes(b'x'*len(content))
            with self.assertRaisesRegex(ValueError, 'checksum'):
                adapter.verify_assets(path)
            path.unlink(); target = Path(folder)/'actual.pth'; target.write_bytes(content); path.symlink_to(target)
            with self.assertRaises(ValueError):
                adapter.verify_assets(path)

    def test_restricted_loader_flag_required_before_import_or_deserialization(self):
        with patch.dict('os.environ', {}, clear=True), patch.object(adapter, 'verify_assets') as verify:
            with self.assertRaisesRegex(ValueError, 'TORCH_FORCE_WEIGHTS_ONLY_LOAD=1'):
                adapter._load_model('/unused')
            verify.assert_not_called()


if __name__ == '__main__':
    unittest.main()
