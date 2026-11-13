"""Pruebas de energía/geometría con verdad sintética; no exactitud en pacientes."""
import json
import unittest
from unittest.mock import patch

import numpy as np
from scipy import ndimage

from hcclab.post_boundary import refine_post_boundary


class PostBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.spacing = (1.5, .8, 1.0)
        self.truth = np.zeros((20, 28, 30), dtype=bool)
        self.truth[5:15, 7:21, 7:23] = True
        # Una concavidad genuina evita confundir regularización con convexidad.
        self.truth[5:15, 7:11, 7:11] = False
        self.hu = np.zeros(self.truth.shape, dtype=np.float32)
        self.hu[self.truth] = 70
        # Interior heterogéneo sin reinterpretarlo como dos objetivos.
        self.hu[8:12, 12:17, 12:19] = 700
        self.initial = self.truth.copy()
        self.initial[10, 14, 23] = True  # Espícula débil fuera de la verdad.
        self.initial[10, 14, 22] = False  # Muesca débil dentro de la verdad.
        self.logits = np.where(self.initial, 8., -8.).astype(np.float32)
        self.logits[10, 14, 23] = .02
        self.logits[10, 14, 22] = -.02

    def run_refinement(self, **kwargs):
        return refine_post_boundary(self.hu, self.initial, self.logits, self.spacing, **kwargs)

    def test_irregular_known_boundary_corrects_weak_spike_and_notch(self):
        result, info = self.run_refinement()
        np.testing.assert_array_equal(result, self.truth)
        self.assertEqual(info['outcome'], 'refined')
        self.assertEqual(info['added_voxels'], 1)
        self.assertEqual(info['removed_voxels'], 1)
        self.assertEqual(info['changed_slices'], [10])
        self.assertEqual(info['voxels'], int(self.truth.sum()))
        self.assertLess(info['energy_after'], info['energy_before'])
        self.assertLessEqual(info['total_capacity'], 1_000_000_000)
        json.dumps(info, allow_nan=False)

    def test_no_mutation_or_alias_and_repeated_results_are_equal(self):
        valid = np.ones_like(self.initial)
        arrays = (self.hu, self.initial, self.logits, valid)
        copies = tuple(array.copy() for array in arrays)
        first, first_info = self.run_refinement(valid_mask=valid)
        second, second_info = self.run_refinement(valid_mask=valid)
        for array, before in zip(arrays, copies):
            np.testing.assert_array_equal(array, before)
            self.assertFalse(np.shares_memory(first, array))
        np.testing.assert_array_equal(first, second)
        self.assertEqual(first_info, second_info)

    def test_band_and_physical_displacement_are_bounded(self):
        result, info = self.run_refinement(band_mm=1)
        inside = ndimage.distance_transform_edt(self.initial, sampling=self.spacing)
        outside = ndimage.distance_transform_edt(~self.initial, sampling=self.spacing)
        distance = np.where(self.initial, inside, outside)
        np.testing.assert_array_equal(result[distance > 1], self.initial[distance > 1])
        self.assertLessEqual(info['changed_voxel_distances_mm']['added_max'], 1)
        self.assertLessEqual(info['changed_voxel_distances_mm']['removed_max'], 1)
        self.assertEqual(info['changed_voxel_distances_mm']['added_max'], .8)
        self.assertEqual(info['changed_voxel_distances_mm']['removed_max'], .8)

    def test_axis_permutation_preserves_anisotropic_result(self):
        result, _ = self.run_refinement()
        order = (2, 0, 1)
        rotated, _ = refine_post_boundary(self.hu.transpose(order), self.initial.transpose(order),
                                          self.logits.transpose(order), tuple(self.spacing[a] for a in order))
        np.testing.assert_array_equal(rotated, result.transpose(order))

    def test_z_neighbors_use_physical_face_areas(self):
        shape = (7, 7, 7)
        initial = np.zeros(shape, dtype=bool)
        initial[2, 3, 3] = initial[4, 3, 3] = True
        logits = np.where(initial, 8., -8.)
        logits[3, 3, 3] = -.01
        hu = np.zeros(shape, dtype=np.float32)
        result, _ = refine_post_boundary(hu, initial, logits, (.5, 2, 2))
        self.assertTrue(result[3, 3, 3])
        # Mismo arreglo, geometría distinta: las caras z ahora pesan menos.
        thick, _ = refine_post_boundary(hu, initial, logits, (2, 2, 2))
        self.assertFalse(thick[3, 3, 3])

    def test_strong_logits_preserve_irregular_shape_despite_uniform_hu(self):
        hu = np.full_like(self.hu, 50.)
        logits = np.where(self.initial, 50., -50.)
        result, info = refine_post_boundary(hu, self.initial, logits, self.spacing)
        np.testing.assert_array_equal(result, self.initial)
        self.assertEqual(info['outcome'], 'unchanged')

    def test_heterogeneous_core_and_strong_concavity_are_not_filled_or_eroded(self):
        result, _ = self.run_refinement()
        self.assertTrue(result[8:12, 12:17, 12:19].all())
        self.assertFalse(result[5:15, 7:11, 7:11].any())

    def test_padding_and_unassessed_support_never_grow(self):
        valid = np.ones_like(self.initial)
        valid[:, 0:5] = False
        scores = self.logits.copy()
        scores[:, 0:5] = 1000  # Sin evidencia utilizable fuera de soporte.
        result, _ = refine_post_boundary(self.hu, self.initial, scores, self.spacing, valid_mask=valid)
        self.assertFalse(result[~valid].any())
        valid[10, 14, 23] = False
        with self.assertRaisesRegex(ValueError, 'padding'):
            refine_post_boundary(self.hu, self.initial, scores, self.spacing, valid_mask=valid)

    def test_mismatched_logit_sign_is_rejected_not_silently_rethresholded(self):
        scores = self.logits.copy()
        scores[10, 14, 23] = -1
        with self.assertRaisesRegex(ValueError, 'logits > 0'):
            refine_post_boundary(self.hu, self.initial, scores, self.spacing)

    def test_field_touch_faces_are_native_input_faces_not_internal_roi_faces(self):
        _, centered = self.run_refinement()
        self.assertEqual(centered['editable_band_touch_faces'], [])
        initial = np.zeros((10, 12, 14), dtype=bool)
        initial[3:7, 1:5, 4:9] = True
        scores = np.where(initial, 50., -50.)
        _, info = refine_post_boundary(np.zeros(initial.shape), initial, scores, (1, 1, 1))
        self.assertEqual(info['editable_band_touch_faces'], ['y_min'])

    def test_quantization_failure_cannot_increase_floating_energy(self):
        # Un solver simulado que ignora los costos no debe alterar la fuente.
        def bad_solver(background, foreground, first, second, weights):
            return np.ones(len(background), dtype=bool), {'capacity_scale': 256.}
        with patch('hcclab.post_boundary._solve_cut', side_effect=bad_solver):
            result, info = self.run_refinement()
        np.testing.assert_array_equal(result, self.initial)
        self.assertEqual(info['reason'], 'quantization_guard')

    def test_inputs_and_resource_bounds_reject_explicitly(self):
        bad_scores = [self.logits[:1], self.logits.astype(bool)]
        for value in (np.nan, np.inf):
            scores = self.logits.copy(); scores[0, 0, 0] = value
            bad_scores.append(scores)
        for scores in bad_scores:
            with self.subTest(scores_shape=scores.shape), self.assertRaises(ValueError):
                refine_post_boundary(self.hu, self.initial, scores, self.spacing)
        for spacing, band in (([True, 1, 1], 2), ([1, np.nan, 1], 2), ([0, 1, 1], 2),
                              (self.spacing, True), (self.spacing, 0), (self.spacing, 6)):
            with self.subTest(spacing=spacing, band=band), self.assertRaises(ValueError):
                refine_post_boundary(self.hu, self.initial, self.logits, spacing, band)
        for limit in ('MAX_ROI_VOXELS', 'MAX_BAND_NODES'):
            with patch('hcclab.post_boundary.'+limit, 1), self.assertRaisesRegex(ValueError, 'exceeds'):
                self.run_refinement()
        with self.assertRaisesRegex(ValueError, 'both foreground and background'):
            refine_post_boundary(self.hu, np.ones_like(self.initial), np.ones_like(self.hu), self.spacing)
        invalid_hu = self.hu.copy(); invalid_hu[0, 0, 0] = np.nan
        with self.assertRaisesRegex(ValueError, 'NaN'):
            refine_post_boundary(invalid_hu, self.initial, self.logits, self.spacing)
        with self.assertRaisesRegex(ValueError, 'Validity'):
            self.run_refinement(valid_mask=np.ones(self.initial.shape, dtype=np.uint8))


if __name__ == '__main__':
    unittest.main()
