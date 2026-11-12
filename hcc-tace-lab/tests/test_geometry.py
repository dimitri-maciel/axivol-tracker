from dataclasses import replace
from pathlib import Path
import unittest

from hcclab.geometry import GeometryError, Slice, build_stack, native_volume_cm3


class GeometryTests(unittest.TestCase):
    def setUp(self):
        self.s = Slice(Path('slice.dcm'),'1.2.3','1','1.2.4',(512,512),(.78125,.78125),
                       (1,0,0,0,1,0),(0,0,-7.5))

    def test_sorted_by_physical_position(self):
        first = replace(self.s,sop='1.2.5',position=(0,0,-10))
        stack = build_stack([self.s,first])
        self.assertEqual(stack.slices[0],first)
        self.assertAlmostEqual(stack.interval_mm,2.5)

    def test_oblique_order_uses_plane_normal(self):
        a = replace(self.s,orientation=(0,1,0,0,0,1),position=(5,0,10))
        b = replace(a,sop='1.2.5',position=(2.5,0,20))
        stack = build_stack([a,b])
        self.assertEqual(stack.slices[0],b)
        self.assertAlmostEqual(stack.interval_mm,2.5)

    def test_duplicates_cannot_be_measured(self):
        stack = build_stack([self.s,replace(self.s,sop='1.2.5')])
        self.assertIsNone(stack.interval_mm)
        with self.assertRaises(GeometryError):
            native_volume_cm3(100,self.s.spacing,stack.interval_mm)

    def test_irregular_spacing_remains_viewable(self):
        stack = build_stack([self.s,replace(self.s,sop='1.2.5',position=(0,0,-5)),
                             replace(self.s,sop='1.2.6',position=(0,0,0))])
        self.assertEqual(len(stack.slices),3)
        self.assertIsNone(stack.interval_mm)

    def test_mixed_frame_is_rejected(self):
        with self.assertRaises(GeometryError):
            build_stack([self.s,replace(self.s,frame='1.2.6')])

    def test_volume_uses_physical_voxel_geometry(self):
        self.assertAlmostEqual(native_volume_cm3(120,(1,2),3),.72)
