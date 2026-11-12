"""Geometría del pincel y separación entre original y revisión manual."""
import unittest
from unittest.mock import patch

import numpy as np

from hcclab.mask_editing import apply_strokes


def stroke(points, *, mode='paint', radius=1, z=0):
    return {'slice': z, 'mode': mode, 'radius_mm': radius, 'points': points}


class MaskEditingTests(unittest.TestCase):
    def setUp(self):
        self.source = np.zeros((3, 11, 11), dtype=bool)

    def test_single_point_is_physical_disk_with_anisotropic_spacing(self):
        edited, info = apply_strokes(self.source, [2, 1], [stroke([[5, 5]], radius=2)])
        expected = np.zeros_like(self.source)
        expected[0, 5, 3:8] = True
        expected[0, 4, 5] = expected[0, 6, 5] = True
        np.testing.assert_array_equal(edited, expected)
        self.assertEqual(info, {'added_voxels': 7, 'removed_voxels': 0, 'changed_slices': [0], 'voxels': 7})

    def test_line_includes_endpoint_caps_and_intermediate_centers(self):
        edited, _ = apply_strokes(self.source, [1, 1], [stroke([[5, 3], [5, 7]])])
        expected = np.zeros_like(self.source)
        expected[0, 4:7, 3:8] = True
        expected[0, 5, 2] = expected[0, 5, 8] = True
        np.testing.assert_array_equal(edited, expected)

    def test_diagonal_distance_and_zero_length_segments(self):
        points = [[2, 2], [2, 2], [7, 7]]
        edited, _ = apply_strokes(self.source, [1, 1], [stroke(points, radius=.5)])
        expected = np.zeros_like(self.source)
        expected[0, np.arange(2, 8), np.arange(2, 8)] = True
        np.testing.assert_array_equal(edited, expected)

    def test_clipping_at_boundary_never_changes_other_slices(self):
        edited, info = apply_strokes(self.source, [1, 1], [stroke([[0, 0]], z=1)])
        self.assertEqual(info['voxels'], 3)
        self.assertEqual(info['changed_slices'], [1])
        self.assertTrue(edited[1, 0, 0] and edited[1, 1, 0] and edited[1, 0, 1])
        self.assertFalse(edited[[0, 2]].any())

    def test_partial_erase_changes_only_intersection_and_preserves_original(self):
        self.source[0, 5, 4:7] = True
        original = self.source.copy()
        edited, info = apply_strokes(self.source, [1, 1], [stroke([[5, 6]], mode='erase')])
        self.assertEqual(info['removed_voxels'], 2)
        self.assertEqual(info['added_voxels'], 0)
        self.assertEqual(info['voxels'], 1)
        self.assertTrue(edited[0, 5, 4])
        np.testing.assert_array_equal(self.source, original)
        self.assertFalse(np.shares_memory(edited, self.source))

    def test_diagnostics_are_net_changes_and_replay_undo_is_deterministic(self):
        paint = stroke([[4, 4], [4, 6]])
        erase = stroke([[4, 4], [4, 6]], mode='erase')
        first, first_info = apply_strokes(self.source, [1, 1], [paint])
        final, info = apply_strokes(self.source, [1, 1], [paint, erase])
        np.testing.assert_array_equal(final, self.source)
        self.assertEqual(info, {'added_voxels': 0, 'removed_voxels': 0, 'changed_slices': [], 'voxels': 0})
        undone, undone_info = apply_strokes(self.source, [1, 1], [paint])
        np.testing.assert_array_equal(undone, first)
        self.assertEqual(undone_info, first_info)
        empty, _ = apply_strokes(self.source, [1, 1], [])
        self.assertFalse(np.shares_memory(empty, self.source))

    def test_tiling_does_not_change_geometry(self):
        edits = [stroke([[1, 1], [8, 4], [6, 9]], radius=1.7)]
        expected, expected_info = apply_strokes(self.source, [.7, 1.2], edits)
        with patch('hcclab.mask_editing.TILE_SIZE', 2):
            actual, actual_info = apply_strokes(self.source, [.7, 1.2], edits)
        np.testing.assert_array_equal(actual, expected)
        self.assertEqual(actual_info, expected_info)

    def test_invalid_types_values_and_limits_are_rejected_without_mutation(self):
        invalid = [dict(stroke([[5, 5]]), **{key: value}) for key, value in [
            ('slice', True), ('slice', 1.0), ('slice', -1), ('slice', 3),
            ('mode', 'smooth'), ('mode', None), ('radius_mm', True), ('radius_mm', 0),
            ('radius_mm', 21), ('radius_mm', float('nan')), ('radius_mm', float('inf')),
            ('radius_mm', 10**400), ('points', []), ('points', [[5.0, 5]]),
            ('points', [[True, 5]]), ('points', [[5, 11]]), ('points', [[-1, 5]]),
            ('points', [[5, float('nan')]]), ('points', [[5]]), ('points', [[5, 5, 5]])]]
        invalid += [None, dict(stroke([[5, 5]]), extra=1)]
        for bad in invalid:
            with self.subTest(stroke=bad), self.assertRaises(ValueError):
                apply_strokes(self.source, [1, 1], [stroke([[5, 5]]), bad])
        self.assertFalse(self.source.any())
        for spacing in ([0, 1], [-1, 1], [True, 1], [float('inf'), 1], [1], np.array(1)):
            with self.subTest(spacing=spacing), self.assertRaises(ValueError):
                apply_strokes(self.source, spacing, [])
        for bad_mask in (np.zeros((2, 2), dtype=bool), np.zeros((2, 2, 2)), np.zeros((0, 2, 2), dtype=bool)):
            with self.assertRaises(ValueError):
                apply_strokes(bad_mask, [1, 1], [])
        with self.assertRaisesRegex(ValueError, '100'):
            apply_strokes(self.source, [1, 1], [stroke([[5, 5]])]*101)
        with self.assertRaisesRegex(ValueError, '2048'):
            apply_strokes(self.source, [1, 1], [stroke([[5, 5]]*2049)])
        with patch('hcclab.mask_editing.MAX_VOLUME_VOXELS', 1), self.assertRaises(ValueError):
            apply_strokes(self.source, [1, 1], [])


if __name__ == '__main__':
    unittest.main()
