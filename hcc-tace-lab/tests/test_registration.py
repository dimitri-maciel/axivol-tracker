"""Geometría física y recuperación de un desplazamiento sintético conocido."""
from pathlib import Path
import json
import unittest

import numpy as np
from scipy import ndimage
import SimpleITK as sitk

from hcclab.geometry import GeometryError, Slice, build_stack
from hcclab.registration import (index_to_patient, localize_pre_region, patient_to_index,
                                 refine_pre_to_post, register_pre_to_post)


def stack_for(shape, spacing=(2, 1, 1), orientation=(1, 0, 0, 0, 1, 0), origin=(0, 0, 0)):
    direction = np.cross(orientation[:3], orientation[3:])
    return build_stack([Slice(Path(f'{z}.dcm'), f'1.2.{z+1}', '1', 'frame', tuple(shape[1:]),
                              tuple(spacing[1:]), orientation,
                              tuple(np.asarray(origin)+z*spacing[0]*direction)) for z in range(shape[0])])


class RegistrationTests(unittest.TestCase):
    def test_rotated_dicom_axes_and_anisotropic_spacing_round_trip(self):
        stack = stack_for((7, 8, 9), (2.5, .8, .6), (0, 1, 0, -1, 0, 0), (10, 20, 30))
        point = index_to_patient([2, 3, 4], stack)
        np.testing.assert_allclose(point, [7.6, 22.4, 35])
        np.testing.assert_allclose(patient_to_index(point, stack), [2, 3, 4])

    def test_localization_maps_only_center_and_box_without_mask_transfer(self):
        shape = (16, 32, 32)
        stack = stack_for(shape, (2, 2, 2))
        mask = np.zeros(shape, bool); mask[4:8, 10:14, 12:16] = True
        transform = sitk.TranslationTransform(3, (4, -2, 2))
        result = localize_pre_region(mask, stack, stack, transform)
        np.testing.assert_allclose(result['pre_center_zyx'], [5.5, 11.5, 13.5])
        np.testing.assert_allclose(result['post_center_zyx'], [6.5, 10.5, 15.5])
        self.assertEqual(result['source_mask_usage'], 'center_and_search_box_only')
        self.assertFalse(result['propagated_mask_used_as_result'])
        self.assertNotIn('mask', result)
        self.assertEqual(int(mask.sum()), 64)
        with self.assertRaisesRegex(GeometryError, 'covered'):
            localize_pre_region(mask, stack, stack, sitk.TranslationTransform(3, (1000, 0, 0)))
        with self.assertRaises(GeometryError):
            localize_pre_region(mask.astype(np.uint8), stack, stack, transform)

    def test_rigid_registration_recovers_known_translation(self):
        shape = (40, 48, 56)
        z, y, x = np.ogrid[:40, :48, :56]
        body = ((z-20)/17)**2+((y-24)/21)**2+((x-28)/24)**2 < 1
        pre = np.where(body, 20+.3*x+.2*y+np.zeros_like(z), -1000).astype(np.float32)
        pre[((z-20)/12)**2+((y-28)/11)**2+((x-17)/9)**2 < 1] = 85
        pre[((z-19)/15)**2+((y-13)/3)**2+((x-28)/3)**2 < 1] = 350
        pre[((z-26)/5)**2+((y-28)/5)**2+((x-39)/5)**2 < 1] = 155
        texture = ndimage.gaussian_filter(np.random.default_rng(37).normal(size=shape), 1.5)*150
        pre[body] += texture[body].astype(np.float32)
        shift = np.array([1, -2, 3])
        post = ndimage.shift(pre, shift, order=1, mode='constant', cval=-1000)
        original_pre, original_post = pre.copy(), post.copy()
        stack = stack_for(shape, (2, 2, 2))
        transform, diagnostics = register_pre_to_post(pre, post, stack, stack)
        point = index_to_patient([20, 24, 28], stack)
        mapped = patient_to_index(transform.TransformPoint(tuple(point)), stack)
        self.assertLess(np.linalg.norm((mapped-(np.array([20, 24, 28])+shift))*2), 3)
        self.assertEqual(diagnostics['mapping'], 'PRE_patient_LPS_to_POST_patient_LPS')
        self.assertFalse(diagnostics['contrast_phases_verified'])
        self.assertGreaterEqual(diagnostics['valid_metric_points'], 32)
        np.testing.assert_array_equal(pre, original_pre)
        np.testing.assert_array_equal(post, original_post)

    def test_invalid_geometry_volume_and_empty_body_are_rejected(self):
        shape = (16, 24, 24)
        stack = stack_for(shape)
        hu = np.zeros(shape, np.float32)
        with self.assertRaises(GeometryError):
            register_pre_to_post(hu[:1], hu, stack, stack)
        bad = hu.copy(); bad[0, 0, 0] = np.nan
        with self.assertRaises(GeometryError):
            register_pre_to_post(bad, hu, stack, stack)
        with self.assertRaises(GeometryError):
            register_pre_to_post(hu, hu, stack, stack, pre_valid=np.ones(shape, np.uint8))
        air = np.full(shape, -1000, np.float32)
        with self.assertRaisesRegex(GeometryError, 'body'):
            register_pre_to_post(air, air, stack, stack)

    def test_context_requires_native_boolean_nonempty_valid_pixels_and_rigid_initial(self):
        shape = (16, 24, 24)
        stack = stack_for(shape, (2, 2, 2))
        hu = np.zeros(shape, np.float32)
        context = np.ones(shape, bool)
        initial = sitk.Euler3DTransform()
        for bad_context in (context.astype(np.uint8), context[:1], np.zeros(shape, bool)):
            with self.subTest(context_shape=bad_context.shape, dtype=bad_context.dtype):
                with self.assertRaisesRegex(GeometryError, 'context'):
                    refine_pre_to_post(hu, hu, bad_context, stack, stack, initial)
        padding_only = np.zeros(shape, bool); padding_only[:2, :2, :2] = True
        with self.assertRaisesRegex(GeometryError, 'no valid CT'):
            refine_pre_to_post(hu, hu, padding_only, stack, stack, initial, pre_valid=~padding_only)
        tiny = np.zeros(shape, bool); tiny[8, 12, 12] = True
        with self.assertRaisesRegex(GeometryError, 'working grid'):
            refine_pre_to_post(hu, hu, tiny, stack, stack, initial)
        nonfinite = sitk.Euler3DTransform(); nonfinite.SetTranslation((float('nan'), 0, 0))
        too_far = sitk.Euler3DTransform(); too_far.SetTranslation((101, 0, 0))
        composite = sitk.CompositeTransform(3)
        composite.AddTransform(initial); composite.AddTransform(initial)
        for transform in (sitk.TranslationTransform(3), sitk.AffineTransform(3), composite,
                          sitk.Euler2DTransform(), nonfinite, too_far, None):
            with self.subTest(transform=str(type(transform))):
                with self.assertRaises(GeometryError):
                    refine_pre_to_post(hu, hu, context, stack, stack, transform)

    def test_context_refinement_uses_physical_axes_excludes_padding_and_preserves_inputs(self):
        shape = (40, 48, 56)
        z, y, x = np.ogrid[:shape[0], :shape[1], :shape[2]]
        body = ((z-20)/17)**2+((y-24)/21)**2+((x-28)/24)**2 < 1
        context = ((z-20)/12)**2+((y-28)/11)**2+((x-17)/9)**2 < 1
        pre = np.where(body, 20+.3*x+.2*y+np.zeros_like(z), -1000).astype(np.float32)
        pre[context] = 85
        pre[((z-19)/15)**2+((y-13)/3)**2+((x-28)/3)**2 < 1] = 350
        texture = ndimage.gaussian_filter(np.random.default_rng(37).normal(size=shape), 1.5)*150
        pre[body] += texture[body].astype(np.float32)
        shift = np.array([1, -2, 3])
        post = ndimage.shift(pre, shift, order=1, mode='constant', cval=-1000)
        stack = stack_for(shape, (2, 2, 2), (0, 1, 0, -1, 0, 0), (10, 20, 30))
        valid = np.ones(shape, bool); valid[:2, :2, :2] = False
        context[:2, :2, :2] = True
        initial = sitk.Euler3DTransform()
        initial.SetCenter(tuple(index_to_patient([20, 24, 28], stack)))
        initial.SetTranslation((4.5, 5.5, 1.5))
        composite = sitk.CompositeTransform(initial)
        original_params = composite.GetParameters()
        originals = [a.copy() for a in (pre, post, context, valid)]
        transform, diagnostics = refine_pre_to_post(pre, post, context, stack, stack, composite,
                                                    pre_valid=valid)
        point = index_to_patient([20, 28, 17], stack)
        expected = index_to_patient(np.array([20, 28, 17])+shift, stack)
        self.assertLess(np.linalg.norm(np.asarray(transform.TransformPoint(tuple(point)))-expected), 3)
        self.assertEqual(diagnostics['context']['excluded_padding_voxels'], 8)
        self.assertEqual(diagnostics['context']['usage'], 'PRE_metric_sampling_only')
        self.assertFalse(diagnostics['lesion_correspondence_verified'])
        self.assertFalse(diagnostics['contrast_phases_verified'])
        self.assertEqual(diagnostics['settings']['threads'], 1)
        self.assertEqual(composite.GetParameters(), original_params)
        for actual, before in zip((pre, post, context, valid), originals):
            np.testing.assert_array_equal(actual, before)
        json.dumps(diagnostics, allow_nan=False)
        self.assertNotIn('mask', diagnostics)


if __name__ == '__main__':
    unittest.main()
