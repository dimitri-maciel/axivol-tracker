"""Geometría sintética de la superficie exacta; no validación anatómica."""
from dataclasses import replace
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np

from hcclab.geometry import GeometryError, Slice, Stack, build_stack
from hcclab.surface import voxel_surface


def stack_for(shape, spacing=(2.5, .7, .8), origin=(10., 20., 30.),
              orientation=(1., 0., 0., 0., 1., 0.)):
    row, column = np.asarray(orientation).reshape(2, 3)
    normal = np.cross(row, column)
    return build_stack([Slice(Path(f'{z}.dcm'), f'sop-{z}', '1', 'frame', tuple(shape[1:]),
                             tuple(spacing[1:]), tuple(orientation),
                             tuple(np.asarray(origin)+z*spacing[0]*normal)) for z in range(shape[0])])


def triangle_data(surface):
    vertices = np.asarray(surface['vertices_lps_mm'])
    triangles = vertices[np.asarray(surface['triangles'])]
    cross = np.cross(triangles[:, 1]-triangles[:, 0], triangles[:, 2]-triangles[:, 0])
    # Trasladar antes de calcular evita cancelación por el origen físico.
    shifted = triangles-vertices.mean(axis=0)
    volume = np.einsum('ij,ij->i', shifted[:, 0], np.cross(shifted[:, 1], shifted[:, 2])).sum()/6
    return triangles, cross, volume


class SurfaceTests(unittest.TestCase):
    def setUp(self):
        self.mask = np.zeros((5, 6, 7), dtype=bool)
        self.mask[2, 3, 4] = True
        self.stack = stack_for(self.mask.shape)

    def test_single_voxel_has_half_voxel_bounds_outward_winding_and_physical_volume(self):
        result = voxel_surface(self.mask, self.stack)
        center = np.array([10+4*.8, 20+3*.7, 30+2*2.5])
        half = np.array([.4, .35, 1.25])
        np.testing.assert_allclose(result['bounds_lps_mm'], [center-half, center+half])
        np.testing.assert_allclose(result['center_lps_mm'], center)
        self.assertEqual(result['stats']['quad_faces'], 6)
        self.assertEqual(result['stats']['vertices'], 8)
        self.assertEqual(result['stats']['triangles'], 12)
        self.assertEqual(result['mask_bounds_zyx_halfopen'], [[2, 3, 4], [3, 4, 5]])
        triangles, normals, volume = triangle_data(result)
        self.assertTrue(np.all(np.einsum('ij,ij->i', normals, triangles.mean(axis=1)-center) > 0))
        self.assertAlmostEqual(volume, 2.5*.7*.8)
        self.assertAlmostEqual(result['stats']['native_volume_cm3'], volume/1000)
        json.dumps(result, allow_nan=False)

    def test_adjacent_voxels_remove_only_internal_face(self):
        self.mask[2, 3, 5] = True
        result = voxel_surface(self.mask, self.stack)
        self.assertEqual(result['stats']['quad_faces'], 10)
        self.assertEqual(result['stats']['triangles'], 20)
        self.assertEqual(result['stats']['vertices'], 12)
        triangles, _, volume = triangle_data(result)
        shared_x = 10+4.5*.8
        self.assertFalse(np.any(np.all(np.isclose(triangles[:, :, 0], shared_x), axis=1)))
        self.assertAlmostEqual(volume, 2*2.5*.7*.8)

    def test_all_disconnected_components_and_internal_cavity_are_preserved(self):
        self.mask[1, 1, 1] = True
        result = voxel_surface(self.mask, self.stack)
        self.assertEqual(result['stats']['quad_faces'], 12)
        self.assertEqual(result['stats']['vertices'], 16)
        self.assertTrue(result['all_components_retained'])
        hollow = np.zeros((5, 5, 5), dtype=bool)
        hollow[1:4, 1:4, 1:4] = True; hollow[2, 2, 2] = False
        result = voxel_surface(hollow, stack_for(hollow.shape, spacing=(1, 1, 1), origin=(0, 0, 0)))
        self.assertEqual(result['stats']['quad_faces'], 60)
        self.assertAlmostEqual(triangle_data(result)[2], 26)

    def test_corner_touching_voxels_are_not_fused_or_cleaned(self):
        mask = np.zeros((5, 5, 5), dtype=bool)
        mask[1, 1, 1] = mask[2, 2, 2] = True
        result = voxel_surface(mask, stack_for(mask.shape, spacing=(1, 1, 1)))
        self.assertEqual(result['stats']['quad_faces'], 12)
        self.assertEqual(result['stats']['vertices'], 15)  # Solo una esquina compartida.
        self.assertAlmostEqual(triangle_data(result)[2], 2)

    def test_rotated_dicom_axes_preserve_patient_orientation_and_winding(self):
        orientation = (0, 1, 0, 0, 0, 1)
        result = voxel_surface(self.mask, stack_for(self.mask.shape, orientation=orientation))
        expected_center = np.array([10+2*2.5, 20+4*.8, 30+3*.7])
        expected_half = np.array([1.25, .4, .35])
        np.testing.assert_allclose(result['bounds_lps_mm'], [expected_center-expected_half, expected_center+expected_half])
        np.testing.assert_allclose(result['direction_lps'], [[0, 0, 1], [1, 0, 0], [0, 1, 0]])
        triangles, normals, volume = triangle_data(result)
        self.assertTrue(np.all(np.einsum('ij,ij->i', normals, triangles.mean(1)-expected_center) > 0))
        self.assertAlmostEqual(volume, 2.5*.7*.8)

    def test_acquisition_contact_is_explicit_and_outer_faces_remain_closed(self):
        mask = np.ones((3, 4, 5), dtype=bool)
        result = voxel_surface(mask, stack_for(mask.shape, spacing=(1, 1, 1)))
        self.assertEqual(result['acquisition_touch_faces'], ['z_min', 'z_max', 'y_min', 'y_max', 'x_min', 'x_max'])
        self.assertEqual(result['stats']['quad_faces'], 2*(3*4+3*5+4*5))
        self.assertAlmostEqual(triangle_data(result)[2], 60)

    def test_empty_mask_is_explicit_without_fabricating_surface(self):
        result = voxel_surface(np.zeros_like(self.mask), self.stack)
        self.assertEqual(result['status'], 'empty_mask')
        self.assertEqual(result['vertices_lps_mm'], [])
        self.assertEqual(result['triangles'], [])
        self.assertIsNone(result['bounds_lps_mm'])
        self.assertIsNone(result['mask_bounds_zyx_halfopen'])
        self.assertEqual(result['stats']['native_volume_cm3'], 0)

    def test_readonly_input_and_repeated_output_are_unchanged(self):
        before = self.mask.copy()
        self.mask.flags.writeable = False
        first = voxel_surface(self.mask, self.stack)
        second = voxel_surface(self.mask, self.stack)
        self.assertEqual(first, second)
        np.testing.assert_array_equal(self.mask, before)
        self.assertFalse(first['smoothing'])
        self.assertFalse(first['decimation'])

    def test_nonuniform_lateral_shift_order_and_duplicate_reference_are_rejected(self):
        reversed_stack = Stack(tuple(reversed(self.stack.slices)), self.stack.interval_mm)
        repeated = Stack((self.stack.slices[0],)*len(self.stack.slices), self.stack.interval_mm)
        moved = list(self.stack.slices)
        moved[2] = replace(moved[2], position=tuple(np.asarray(moved[2].position)+[.1, 0, 0]))
        shifted = Stack(tuple(moved), self.stack.interval_mm)
        irregular = list(self.stack.slices)
        irregular[2] = replace(irregular[2], position=tuple(np.asarray(irregular[2].position)+[0, 0, .02]))
        for stack in (reversed_stack, repeated, shifted, Stack(tuple(irregular), self.stack.interval_mm),
                      replace(self.stack, interval_mm=1), Stack(self.stack.slices[:1], None)):
            with self.subTest(stack=stack), self.assertRaises(GeometryError):
                voxel_surface(self.mask, stack)

    def test_bad_mask_and_nonfinite_geometry_are_rejected(self):
        for mask in (self.mask.astype(np.uint8), self.mask[0], self.mask[:, :, :1], np.zeros((0, 2, 3), dtype=bool)):
            with self.subTest(shape=mask.shape), self.assertRaises(GeometryError):
                voxel_surface(mask, self.stack)
        huge = np.broadcast_to(np.array(False), (4001, 4001, 4))
        with self.assertRaisesRegex(GeometryError, '64 million'):
            voxel_surface(huge, self.stack)
        broken = list(self.stack.slices)
        broken[0] = replace(broken[0], position=(np.nan, 0, 0))
        with self.assertRaises(GeometryError):
            voxel_surface(self.mask, Stack(tuple(broken), self.stack.interval_mm))

    def test_limits_fail_without_simplification_or_mutation(self):
        before = self.mask.copy()
        for name in ('MAX_QUAD_FACES', 'MAX_VERTICES', 'MAX_TRIANGLES', 'MAX_JSON_BYTES'):
            with self.subTest(limit=name), patch('hcclab.surface.'+name, 1), self.assertRaises(GeometryError):
                voxel_surface(self.mask, self.stack)
        np.testing.assert_array_equal(self.mask, before)


if __name__ == '__main__':
    unittest.main()
