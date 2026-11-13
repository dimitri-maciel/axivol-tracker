"""Synthetic tests for descriptive metrics, not a clinical validation."""
import unittest

import numpy as np

from experiments.audit_post_slices import slice_metrics, selected_slices, crop_bounds


class SliceAuditTests(unittest.TestCase):
    def setUp(self):
        self.hu = np.broadcast_to(np.arange(10)[None, None, :]*3., (6, 9, 10)).copy()
        self.mask = np.zeros(self.hu.shape, bool)
        self.valid = np.ones(self.hu.shape, bool)

    def test_all_planes_null_empty_and_physical_centroid_jump(self):
        self.mask[1, 2:4, 3:5] = True
        self.mask[2, 3:5, 5:7] = True
        self.mask[4, 3:5, 5:7] = True
        rows = slice_metrics(self.hu, self.mask, self.mask, self.valid, [2.5, 2., .5])
        self.assertEqual(len(rows), 6)
        self.assertEqual(rows[2]['app_slice'], 3)
        self.assertEqual(rows[1]['refined_area_mm2'], 4)
        self.assertAlmostEqual(rows[2]['centroid_jump_from_previous_slice_mm'], np.sqrt(5))
        for z in (0, 1, 3, 4, 5):
            self.assertIsNone(rows[z]['centroid_jump_from_previous_slice_mm'])
        self.assertIsNone(rows[0]['hu_median'])
        self.assertEqual(rows[1]['boundary_gradient_median_hu_per_mm'], 6.)

    def test_components_holes_and_add_remove_do_not_mutate_inputs(self):
        self.mask[2, 2:7, 2:7] = True
        self.mask[2, 3:6, 3:6] = False
        self.mask[2, 0, 0] = True
        raw = self.mask.copy(); raw[2, 0, 0] = False; raw[2, 8, 9] = True
        copies = [a.copy() for a in (self.hu, raw, self.mask, self.valid)]
        row = slice_metrics(self.hu, raw, self.mask, self.valid, [2.5, 1., 1.])[2]
        self.assertEqual((row['components_4'], row['holes_8'], row['hole_pixels']), (2, 1, 9))
        self.assertEqual((row['added_voxels'], row['removed_voxels']), (1, 1))
        for value, before in zip((self.hu, raw, self.mask, self.valid), copies):
            np.testing.assert_array_equal(value, before)

    def test_invalid_gradient_neighbours_excluded_and_padding_foreground_rejected(self):
        self.mask[2, 3, 3] = True
        self.valid[2, 3, 4] = False; self.hu[2, 3, 4] = np.nan
        row = slice_metrics(self.hu, self.mask, self.mask, self.valid, [1, 1, 1])[2]
        self.assertEqual(row['boundary_gradient_samples'], 0)
        self.assertIsNone(row['boundary_gradient_median_hu_per_mm'])
        self.valid[2, 3, 3] = False
        with self.assertRaises(ValueError):
            slice_metrics(self.hu, self.mask, self.mask, self.valid, [1, 1, 1])

    def test_selection_retains_internal_empty_and_context_clips_to_native_bounds(self):
        self.mask[1, 3, 4] = True; self.mask[4, 3, 4] = True
        self.assertEqual(selected_slices(self.mask, self.mask), list(range(6)))
        low, high = crop_bounds(self.mask, self.mask, [2.5, 2, .5], margin_mm=1)
        np.testing.assert_array_equal(low, [2, 2]); np.testing.assert_array_equal(high, [5, 7])


if __name__ == '__main__':
    unittest.main()
