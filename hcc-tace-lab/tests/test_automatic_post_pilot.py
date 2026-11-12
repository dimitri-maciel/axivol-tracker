"""Public HCC_002 integration and provenance, not anatomical validation.

All candidates live in temporary storage. There is no independent POST truth
in this fixture, so a successful run does not establish lesion accuracy.
"""
from io import BytesIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zipfile import ZipFile

import numpy as np
from PIL import Image

from hcclab.candidates import file_sha256
from hcclab.dicom import find_seg
from hcclab.geometry import GeometryError
from hcclab.lab import Lab


@unittest.skipUnless(os.environ.get('HCC_PILOT_TEST') == '1', 'Requires public HCC_002.')
class AutomaticPostPilotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.lab = Lab(Path(os.environ.get('HCC_DATA_DIR', '/data')), Path(cls.temporary.name))
        cls.addClassCleanup(cls.lab.hu_volume.cache_clear)
        cls.addClassCleanup(cls.lab.candidates.mask.cache_clear)
        cls.addClassCleanup(cls.lab.verify_candidate_sources.cache_clear)
        case = cls.lab.catalog.case('HCC_002')
        cls.pre, cls.post = [study['series'][0]['uid'] for study in case['studies']]
        cls.payload = {'case_id': 'HCC_002', 'pre_series_uid': cls.pre, 'pre_stack_index': 0,
                       'series_uid': cls.post, 'stack_index': 0, 'segment': 2}
        cls.result = cls.lab.create_automatic_post_candidate(cls.payload)
        cls.mask = cls.lab.candidates.mask(cls.result['id'])

    def test_native_post_pixels_and_explicit_baseline_provenance(self):
        result, mask = self.result, self.mask
        self.assertEqual((result['case_id'], result['stage'], result['acquisition']), ('HCC_002', 'POST', '1'))
        self.assertEqual(result['status'], 'unreviewed')
        self.assertEqual(result['shape_zyx'], [97, 512, 512])
        self.assertEqual(mask.dtype, np.bool_)
        self.assertTrue(result['method']['native_output_grid'])
        self.assertEqual(result['method']['uses_pre_mask'], 'localization_only')
        self.assertEqual(result['method']['mask_intensities'], 'native_POST_HU')
        self.assertEqual(result['voxels'], int(mask.sum()))
        self.assertGreater(result['voxels'], 0)
        self.assertAlmostEqual(result['volume_cm3'], int(mask.sum()) * .78125 ** 2 * 2.5 / 1000)
        baseline = result['baseline_source']
        self.assertEqual((baseline['series_uid'], baseline['stack_index'], baseline['source_segment']), (self.pre, 0, 2))
        self.assertEqual(baseline['source_segment_label'], 'Mass')
        row, _ = self.lab.stack(self.pre, 0)
        self.assertEqual(baseline['source_seg_sha256'], file_sha256(find_seg(self.lab.catalog, row)[0]))
        self.assertEqual(len(baseline['source']['slices']), 109)
        self.assertEqual(len(result['source']['slices']), 97)
        self.assertTrue(all(len(s['sha256']) == 64 for s in result['source']['slices']))
        self.assertEqual(self.lab.measure(self.pre, 0, 2)['voxels'], 324430)
        self.assertEqual(result['diagnostics']['registration']['status'], 'estimated_unverified')

    def test_saved_overlay_export_and_report_preserve_scope(self):
        candidate_id = self.result['id']
        reopened = Lab(self.lab.catalog.data, Path(self.temporary.name))
        self.assertEqual(reopened.list_candidates('HCC_002', self.post, 0)['candidates'][0]['id'], candidate_id)
        self.assertEqual(reopened.candidate_for_stack(candidate_id, self.post, 0)['id'], candidate_id)
        z = self.result['diagnostics']['segmentation']['display_slice_index']
        self.assertGreater(int(self.mask[z].sum()), 0)
        binary = np.asarray(Image.open(BytesIO(reopened.mask_image(self.post, 0, z, candidate_id=candidate_id))))
        np.testing.assert_array_equal(binary > 0, self.mask[z])
        self.assertNotEqual(reopened.image(self.post, 0, z, 50, 350),
                            reopened.image(self.post, 0, z, 50, 350, candidate_id=candidate_id,
                                           mask_style='combined', mask_opacity=15))
        with ZipFile(BytesIO(reopened.candidates.export(candidate_id))) as archive:
            metadata = json.loads(archive.read('metadata.json'))
            self.assertEqual(metadata['baseline_source'], json.loads(json.dumps(self.result['baseline_source'])))
            self.assertEqual(metadata['method']['name'], 'registered_post_segmentation')
            with np.load(BytesIO(archive.read('mask.npz')), allow_pickle=False) as saved:
                np.testing.assert_array_equal(saved['mask'], self.mask)
        report = reopened.report('HCC_002', self.pre, 0, self.post, 0, 2, candidate_id)
        self.assertEqual(report['post_candidate']['id'], candidate_id)
        self.assertEqual(report['pre_segmentation_measurement']['voxels'], 324430)
        self.assertIsNone(report['post_segmentation_measurement'])
        self.assertIsNone(report['volume_change_percent'])

    def test_mismatched_selection_and_invalid_requests_are_rejected(self):
        for change in ({'series_uid': self.pre}, {'pre_series_uid': self.post}, {'segment': 1},
                       {'segment': 99}, {'segment': True}, {'pre_stack_index': True},
                       {'stack_index': True}, {'unexpected': True}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.lab.create_automatic_post_candidate(dict(self.payload, **change))
        with self.assertRaises(GeometryError):
            self.lab.candidate_for_stack(self.result['id'], self.post, 1)
        for pre_index, segment in ((1, 2), (0, 1)):
            with self.subTest(pre_index=pre_index, segment=segment), self.assertRaises(GeometryError):
                self.lab.report('HCC_002', self.pre, pre_index, self.post, 0, segment, self.result['id'])

    def test_algorithm_abstention_saves_nothing_and_releases_lock(self):
        before = {p.name for p in Path(self.temporary.name).iterdir()}
        with patch('hcclab.lab.automatic_post_candidate', side_effect=GeometryError('No separable POST region.')):
            with self.assertRaisesRegex(GeometryError, 'No separable POST region'):
                self.lab.create_automatic_post_candidate(self.payload)
        self.assertEqual({p.name for p in Path(self.temporary.name).iterdir()}, before)
        self.assertTrue(self.lab.segmentation_lock.acquire(blocking=False))
        self.lab.segmentation_lock.release()


if __name__ == '__main__':
    unittest.main()
