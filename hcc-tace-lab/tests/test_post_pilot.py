"""Engineering checks on public POST pixels, not lesion-accuracy validation."""
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile
import json
import os
import unittest

import numpy as np
from PIL import Image

from hcclab.geometry import GeometryError
from hcclab.lab import Lab


@unittest.skipUnless(os.environ.get('HCC_PILOT_TEST') == '1', 'Requires public HCC_002.')
class PostPilotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = TemporaryDirectory()
        cls.lab = Lab(Path(os.environ.get('HCC_DATA_DIR', '/data')), Path(cls.directory.name))
        case = cls.lab.catalog.case('HCC_002')
        cls.pre, cls.post = [study['series'][0]['uid'] for study in case['studies']]
        # A tiny image-center test region exercises real geometry and persistence.
        # These coordinates do not declare or approximate a lesion annotation.
        cls.payload = {'case_id': 'HCC_002', 'series_uid': cls.post, 'stack_index': 0,
                       'seed': [48, 256, 256], 'roi': [[47, 254, 254], [50, 259, 259]],
                       'lower_hu': -1024, 'upper_hu': 3071}
        cls.candidate = cls.lab.create_candidate(cls.payload)

    @classmethod
    def tearDownClass(cls):
        cls.lab.hu_volume.cache_clear()
        cls.lab.candidates.mask.cache_clear()
        cls.directory.cleanup()

    def test_real_post_candidate_provenance_and_overlay(self):
        candidate = self.candidate
        self.assertEqual((candidate['case_id'], candidate['acquisition'], candidate['stage']), ('HCC_002', '1', 'POST'))
        self.assertEqual(candidate['status'], 'unreviewed')
        self.assertEqual(candidate['shape_zyx'], [97, 512, 512])
        self.assertFalse(candidate['method']['uses_pre_mask'])
        self.assertTrue(candidate['method']['padding_excluded'])
        self.assertEqual(len(candidate['source']['slices']), 97)
        self.assertTrue(all(len(item['sha256']) == 64 for item in candidate['source']['slices']))
        mask = self.lab.candidates.mask(candidate['id'])
        self.assertGreater(candidate['voxels'], 0)
        self.assertLessEqual(candidate['voxels'], 75)
        self.assertEqual(candidate['voxels'], int(mask.sum()))
        self.assertAlmostEqual(candidate['volume_cm3'], candidate['voxels'] * .78125 ** 2 * 2.5 / 1000)
        base = self.lab.image(self.post, 0, 48, 50, 350)
        overlay = self.lab.image(self.post, 0, 48, 50, 350, candidate_id=candidate['id'])
        self.assertEqual(Image.open(BytesIO(overlay)).size, (512, 512))
        self.assertNotEqual(base, overlay)

    def test_restore_export_and_no_treatment_response(self):
        reopened = Lab(self.lab.catalog.data, Path(self.directory.name))
        candidate_id = self.candidate['id']
        self.assertEqual(reopened.list_candidates('HCC_002', self.post, 0)['candidates'][0]['id'], candidate_id)
        with ZipFile(BytesIO(reopened.candidates.export(candidate_id))) as archive:
            metadata = json.loads(archive.read('metadata.json'))
            self.assertEqual(metadata['parameters'], {k: self.payload[k] for k in ('seed', 'roi', 'lower_hu', 'upper_hu')})
            with np.load(BytesIO(archive.read('mask.npz')), allow_pickle=False) as saved:
                np.testing.assert_array_equal(saved['mask'], self.lab.candidates.mask(candidate_id))
        report = reopened.report('HCC_002', self.pre, 0, self.post, 0, 2, candidate_id)
        self.assertEqual(report['post_candidate']['status'], 'unreviewed')
        self.assertIsNone(report['post_segmentation_measurement'])
        self.assertIsNone(report['volume_change_percent'])

    def test_pre_other_acquisition_and_invalid_parameters_cannot_mix(self):
        with self.assertRaises(GeometryError):
            self.lab.create_candidate(dict(self.payload, series_uid=self.pre))
        with self.assertRaises(GeometryError):
            self.lab.candidate_for_stack(self.candidate['id'], self.post, 1)
        with self.assertRaises(GeometryError):
            self.lab.candidate_for_stack(self.candidate['id'], self.pre, 0)
        with self.assertRaises(ValueError):
            self.lab.create_candidate(dict(self.payload, stack_index=True))
        with self.assertRaises(ValueError):
            self.lab.create_candidate(dict(self.payload, extra='unexpected'))
