from io import BytesIO
import os
from pathlib import Path
import unittest

from PIL import Image

from hcclab.geometry import GeometryError
from hcclab.lab import Lab


@unittest.skipUnless(os.environ.get('HCC_PILOT_TEST')=='1', 'Requiere HCC_002 descargado; activa HCC_PILOT_TEST=1.')
class PilotIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lab = Lab(Path(os.environ.get('HCC_DATA_DIR','/data')))
        cls.case = cls.lab.catalog.case('HCC_002')
        cls.pre, cls.post = [s['series'][0]['uid'] for s in cls.case['studies']]

    def test_two_real_acquisitions_are_separated(self):
        pre = self.lab.series(self.pre)
        self.assertEqual([s['slices'] for s in pre['stacks']], [109,109])
        self.assertEqual(pre['default_segment'],2)

    def test_real_png_and_overlay(self):
        base = self.lab.image(self.pre,0,54,50,350)
        overlay = self.lab.image(self.pre,0,54,50,350,2)
        self.assertEqual(Image.open(BytesIO(base)).size,(512,512))
        self.assertEqual(Image.open(BytesIO(overlay)).mode,'RGB')
        self.assertNotEqual(base,overlay)
        self.assertEqual(Image.open(BytesIO(self.lab.image(self.post,0,48,50,350))).size,(512,512))

    def test_published_mass_mask_and_no_post_inference(self):
        r = self.lab.report('HCC_002',self.pre,0,self.post,0,2)
        m = r['pre_segmentation_measurement']
        self.assertEqual((m['label'],m['matched_frames'],m['voxels']),('Mass',109,324430))
        self.assertAlmostEqual(m['volume_cm3'],495.0408935546875)
        self.assertIsNone(r['volume_change_percent'])
        self.assertIsNone(r['post_segmentation_measurement'])

    def test_reverse_chronology_cannot_export_comparison(self):
        with self.assertRaises(GeometryError):
            self.lab.report('HCC_002',self.post,0,self.pre,0,2)
