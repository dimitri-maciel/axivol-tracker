"""Synthetic grid checks only. Slicer/VTK imports are stubs, not a GUI test."""
from pathlib import Path
import runpy
import sys
import types
import unittest
from unittest.mock import patch

import numpy as np


with patch.dict(sys.modules, {'slicer': types.ModuleType('slicer'), 'vtk': types.ModuleType('vtk')}):
    helper = runpy.run_path(str(Path(__file__).resolve().parents[1]
                               / 'experiments/templates/open_in_slicer.py'))

map_native = helper['_map_native_mask']
check_export = helper['_check_export_pixels']


class SlicerReferenceGeometryTests(unittest.TestCase):
    def setUp(self):
        # Oblique in-plane orientation, anisotropic spacing, nonzero RAS origin.
        angle = np.deg2rad(23)
        rotation = np.array([[np.cos(angle), -np.sin(angle), 0],
                             [np.sin(angle), np.cos(angle), 0], [0, 0, 1]])
        self.post = np.eye(4)
        self.post[:3, :3] = rotation @ np.diag([.6, .9, 2.5])
        self.post[:3, 3] = [17.3, -91.2, 34.5]
        self.shape = (9, 11, 13)

    def test_native_extent_and_xyz_to_zyx_mapping(self):
        source = np.zeros((2, 3, 4), np.uint8)
        source[1, 0, 2] = 1
        before = source.copy()
        actual = map_native(source, [4, 7, 3, 5, 2, 3], self.post, self.post, self.shape)
        expected = np.zeros(self.shape, np.uint8)
        expected[3, 3, 6] = 1
        np.testing.assert_array_equal(actual, expected)
        np.testing.assert_array_equal(source, before)

    def test_integer_origin_shift_and_extent_minima_are_both_applied(self):
        source = np.zeros((2, 3, 4), np.uint8)
        source[0, 2, 1] = 1
        shift = np.eye(4); shift[:3, 3] = [5, 4, 3]
        actual = map_native(source, [-1, 2, -2, 0, -1, 0], self.post @ shift, self.post, self.shape)
        expected = np.zeros(self.shape, np.uint8)
        expected[2, 4, 5] = 1
        np.testing.assert_array_equal(actual, expected)

    def test_fractional_shift_spacing_rotation_and_flip_are_rejected(self):
        changes = []
        fractional = np.eye(4); fractional[0, 3] = .25; changes.append(fractional)
        spacing = np.eye(4); spacing[1, 1] = .5; changes.append(spacing)
        rotation = np.eye(4); rotation[:2, :2] = [[0, -1], [1, 0]]; changes.append(rotation)
        flipped = np.eye(4); flipped[2, 2] = -1; changes.append(flipped)
        for change in changes:
            with self.subTest(change=change.tolist()), self.assertRaisesRegex(ValueError, 'resampling'):
                map_native(np.ones((1, 1, 1), np.uint8), [0, 0, 0, 0, 0, 0],
                           self.post @ change, self.post, self.shape)

    def test_outside_foreground_rejected_at_each_native_boundary(self):
        for xyz in [(-1, 0, 0), (13, 0, 0), (0, -1, 0), (0, 11, 0), (0, 0, -1), (0, 0, 9)]:
            shift = np.eye(4); shift[:3, 3] = xyz
            with self.subTest(xyz=xyz), self.assertRaisesRegex(ValueError, 'outside'):
                map_native(np.ones((1, 1, 1), np.uint8), [0, 0, 0, 0, 0, 0],
                           self.post @ shift, self.post, self.shape)

    def test_zero_padding_outside_volume_does_not_discard_foreground(self):
        source = np.zeros((2, 3, 4), np.uint8)
        source[1, 2, 3] = 1
        actual = map_native(source, [-1, 2, -1, 1, -1, 0], self.post, self.post, self.shape)
        expected = np.zeros(self.shape, np.uint8)
        expected[0, 1, 2] = 1
        np.testing.assert_array_equal(actual, expected)
        source[0, 0, 0] = 1
        with self.assertRaisesRegex(ValueError, 'outside'):
            map_native(source, [-1, 2, -1, 1, -1, 0], self.post, self.post, self.shape)

    def test_empty_native_segment_is_preserved(self):
        actual = map_native(np.zeros((0, 0, 0), np.uint8), [0, -1, 0, -1, 0, -1],
                            self.post, self.post, self.shape)
        self.assertEqual(actual.shape, self.shape)
        self.assertFalse(actual.any())
        full = map_native(np.zeros(self.shape, np.uint8), [0, 12, 0, 10, 0, 8],
                          self.post, self.post, self.shape)
        check_export(full, actual)

    def test_export_rejects_clipping_and_equal_count_pixel_movement(self):
        expected = np.zeros(self.shape, np.uint8); expected[2, 3, 4] = 1
        check_export(expected.copy(), expected)
        for changed in [np.zeros_like(expected), np.roll(expected, 1, axis=2)]:
            with self.assertRaisesRegex(ValueError, 'changed'):
                check_export(changed, expected)

    def test_invalid_extent_and_nonfinite_geometry_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'extent'):
            map_native(np.ones((1, 1, 1)), [0, 2, 0, 0, 0, 0], self.post, self.post, self.shape)
        broken = self.post.copy(); broken[0, 0] = np.nan
        with self.assertRaisesRegex(ValueError, 'finite'):
            map_native(np.ones((1, 1, 1)), [0, 0, 0, 0, 0, 0], broken, self.post, self.shape)


if __name__ == '__main__':
    unittest.main()
