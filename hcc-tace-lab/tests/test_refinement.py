"""Verdad sintética conocida y límites del ajuste; no validación clínica."""
from itertools import product
import unittest
from unittest.mock import patch

import numpy as np
from scipy import ndimage

from hcclab.refinement import _solve_cut, refine_boundary


class RefinementTests(unittest.TestCase):
    def setUp(self):
        self.spacing = (1.5, 1, 1)
        z, y, x = np.ogrid[:32, :48, :48]
        self.distance = np.sqrt(((z-15)*1.5)**2+(y-24)**2+(x-24)**2)
        self.truth = self.distance <= 10
        self.original = self.distance <= 11.5
        self.hu = np.where(self.truth, 120, 0).astype(np.float32)

    def test_known_sphere_corrects_both_over_and_under_segmentation(self):
        for radius in (8.5, 11.5):
            original = self.distance <= radius
            with self.subTest(radius=radius):
                candidate, info = refine_boundary(self.hu, original, self.spacing)
                np.testing.assert_array_equal(candidate, self.truth)
                self.assertEqual(info['outcome'], 'refined')
                self.assertEqual(info['voxels'], int(self.truth.sum()))
                self.assertEqual(info['added_voxels'], int(np.count_nonzero(self.truth & ~original)))
                self.assertEqual(info['removed_voxels'], int(np.count_nonzero(original & ~self.truth)))
                self.assertLessEqual(info['energy_after'], info['energy_before'])
                self.assertLessEqual(info['total_capacity'], 1_000_000_000)

    def test_ambiguous_hu_abstains_without_smoothing_original(self):
        hu = np.full(self.hu.shape, 50, dtype=np.float32)
        candidate, info = refine_boundary(hu, self.original, self.spacing)
        np.testing.assert_array_equal(candidate, self.original)
        self.assertEqual(info['outcome'], 'insufficient_contrast')
        self.assertEqual(info['reason'], 'similar_hu_distributions')
        self.assertAlmostEqual(info['histogram_total_variation'], 0)
        self.assertEqual(info['added_voxels']+info['removed_voxels'], 0)
        self.assertEqual(info['changed_slices'], [])
        self.assertFalse(np.shares_memory(candidate, self.original))

    def test_bimodal_foreground_is_not_reduced_to_one_mean_intensity(self):
        # Dos intensidades de foreground rodean la del fondo. Una media única
        # ocultaría esta separación; el histograma conserva ambos modos.
        hu = np.full(self.hu.shape, 90, dtype=np.float32)
        columns = np.broadcast_to(np.arange(48)[None, None, :], self.hu.shape)
        hu[self.truth & (columns < 24)] = 40
        hu[self.truth & (columns >= 24)] = 140
        candidate, info = refine_boundary(hu, self.original, self.spacing)
        np.testing.assert_array_equal(candidate, self.truth)
        self.assertEqual(info['outcome'], 'refined')

    def test_core_and_exterior_are_fixed_despite_misleading_hu(self):
        # Una isla lejana con HU foreground no debe convertirse en lesión.
        self.hu[1:4, 1:4, 1:4] = 120
        # Un vóxel interior distinto conserva la etiqueta del núcleo fijo.
        self.hu[15, 24, 24] = 0
        before_hu, before_mask = self.hu.copy(), self.original.copy()
        candidate, info = refine_boundary(self.hu, self.original, self.spacing)
        inside = ndimage.distance_transform_edt(self.original, sampling=self.spacing)
        outside = ndimage.distance_transform_edt(~self.original, sampling=self.spacing)
        fixed = np.where(self.original, inside, outside) > 3
        np.testing.assert_array_equal(candidate[fixed], self.original[fixed])
        self.assertTrue(candidate[15, 24, 24])
        self.assertFalse(candidate[1:4, 1:4, 1:4].any())
        np.testing.assert_array_equal(self.hu, before_hu)
        np.testing.assert_array_equal(self.original, before_mask)
        self.assertFalse(np.shares_memory(candidate, self.original))

    def test_axis_permutation_preserves_physical_result_and_runs_are_deterministic(self):
        a, da = refine_boundary(self.hu, self.original, self.spacing)
        b, db = refine_boundary(self.hu, self.original, self.spacing)
        np.testing.assert_array_equal(a, b)
        self.assertEqual(da, db)
        order = (2, 0, 1)
        permuted, _ = refine_boundary(self.hu.transpose(order), self.original.transpose(order),
                                      tuple(self.spacing[i] for i in order))
        np.testing.assert_array_equal(permuted, a.transpose(order))

    def test_missing_core_samples_abstains(self):
        thin = self.distance <= 1
        candidate, info = refine_boundary(self.hu, thin, self.spacing)
        np.testing.assert_array_equal(candidate, thin)
        self.assertEqual(info['outcome'], 'insufficient_contrast')
        self.assertEqual(info['reason'], 'insufficient_samples')

    def test_padding_is_excluded_and_foreground_padding_is_rejected(self):
        valid = np.ones_like(self.original)
        valid[:, :3, :] = False
        candidate, _ = refine_boundary(self.hu, self.original, self.spacing, valid_mask=valid)
        self.assertFalse(candidate[:, :3, :].any())
        valid[15, 24, 24] = False
        with self.assertRaisesRegex(ValueError, 'padding'):
            refine_boundary(self.hu, self.original, self.spacing, valid_mask=valid)

    def test_graph_cut_matches_exhaustive_binary_energy_and_capacity_budget(self):
        # El mínimo conocido asigna F,F,B: paga solo la arista de costo 1.
        background = np.array([10., 0., 0.])
        foreground = np.array([0., 0., 9.])
        first, second, weights = np.array([0, 1], np.int32), np.array([1, 2], np.int32), np.array([3., 1.])
        def energy(labels):
            return np.where(labels, foreground, background).sum()+weights[labels[first] != labels[second]].sum()
        labels, info = _solve_cut(background, foreground, first, second, weights)
        brute_force = min(energy(np.array(values, dtype=bool)) for values in product((False, True), repeat=3))
        self.assertEqual(energy(labels), brute_force)
        np.testing.assert_array_equal(labels, [True, True, False])
        self.assertEqual(info['flow_integer'], 256)
        large_labels, large_info = _solve_cut(background*1e12, foreground*1e12, first, second, weights*1e12)
        np.testing.assert_array_equal(large_labels, labels)
        self.assertLess(large_info['capacity_scale'], 1)
        self.assertLessEqual(large_info['total_capacity'], 1_000_000_000)

    def test_invalid_parameters_and_resource_limits_fail_explicitly(self):
        for spacing, band in [([0, 1, 1], 3), ([True, 1, 1], 3), ([1, float('nan'), 1], 3),
                              ([1, 1], 3), (np.array(1), 3), (self.spacing, True),
                              (self.spacing, 0), (self.spacing, 6), (self.spacing, float('inf'))]:
            with self.subTest(spacing=spacing, band=band), self.assertRaises(ValueError):
                refine_boundary(self.hu, self.original, spacing, band)
        for mask in (self.original.astype(np.uint8), np.zeros_like(self.original), self.original[:1]):
            with self.assertRaises(ValueError):
                refine_boundary(self.hu, mask, self.spacing)
        invalid_hu = self.hu.copy(); invalid_hu[0, 0, 0] = np.nan
        with self.assertRaises(ValueError):
            refine_boundary(invalid_hu, self.original, self.spacing)
        with self.assertRaises(ValueError):
            refine_boundary(self.hu, self.original, self.spacing, valid_mask=np.ones(self.hu.shape, dtype=np.uint8))
        for constant in ('MAX_VOLUME_VOXELS', 'MAX_ROI_VOXELS', 'MAX_BAND_NODES'):
            with self.subTest(limit=constant), patch('hcclab.refinement.'+constant, 1), self.assertRaises(ValueError):
                refine_boundary(self.hu, self.original, self.spacing)


if __name__ == '__main__':
    unittest.main()
