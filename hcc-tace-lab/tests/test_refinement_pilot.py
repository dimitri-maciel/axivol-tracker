"""Real PRE pixels verify integration; they do not provide an independent truth.

All generated masks remain in a temporary annotation directory. Comparing a
refinement with its own initialization measures change, not clinical accuracy.
"""
from io import BytesIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

import numpy as np
from PIL import Image
from scipy import ndimage

from hcclab.candidates import file_sha256
from hcclab.dicom import find_seg, referenced_mask
from hcclab.geometry import GeometryError
from hcclab.lab import Lab


@unittest.skipUnless(os.environ.get('HCC_PILOT_TEST') == '1', 'Requires public HCC_002.')
class RefinementPilotTests(unittest.TestCase):
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
        row, cls.stack = cls.lab.stack(cls.pre, 0)
        cls.seg = find_seg(cls.lab.catalog, row)
        cls.source_hash = file_sha256(cls.seg[0])
        cls.original = referenced_mask(*cls.seg, 2, cls.stack)
        cls.payload = {'case_id': 'HCC_002', 'series_uid': cls.pre, 'stack_index': 0,
                       'segment': 2, 'band_mm': 3}
        cls.result = cls.lab.create_pre_refinement(cls.payload)
        cls.refined = cls.lab.candidates.mask(cls.result['id'])

    def test_changes_are_bounded_and_original_is_preserved(self):
        before, after = self.original, self.refined
        result = self.result
        diagnostics = result['diagnostics']
        self.assertEqual(int(before.sum()), 324430)
        self.assertEqual(result['original_voxels'], 324430)
        self.assertEqual(result['stage'], 'PRE')
        self.assertEqual(result['status'], 'unreviewed')
        self.assertEqual(result['source_segment_label'], 'Mass')
        self.assertEqual(result['acquisition'], '1')
        self.assertEqual(diagnostics['added_voxels'], int(np.count_nonzero(after & ~before)))
        self.assertEqual(diagnostics['removed_voxels'], int(np.count_nonzero(before & ~after)))
        self.assertEqual(diagnostics['voxels'], int(after.sum()))
        spacing = (self.stack.interval_mm, *self.stack.slices[0].spacing)
        coordinates = np.where(before)
        bounds = [(max(0, int(axis.min())-int(np.ceil(5/s))-2),
                   min(size, int(axis.max())+int(np.ceil(5/s))+3))
                  for axis, s, size in zip(coordinates, spacing, before.shape)]
        box = tuple(slice(low, high) for low, high in bounds)
        mask = before[box]
        distance_in = ndimage.distance_transform_edt(mask, sampling=spacing)
        distance_out = ndimage.distance_transform_edt(~mask, sampling=spacing)
        band = np.where(mask, distance_in, distance_out) <= 3
        changed = before[box] != after[box]
        self.assertFalse(np.any(changed & ~band))
        self.assertEqual(int(changed.sum()), diagnostics['added_voxels']+diagnostics['removed_voxels'])
        self.assertEqual(file_sha256(self.seg[0]), self.source_hash)
        self.assertEqual(self.lab.measure(self.pre, 0, 2)['voxels'], 324430)
        self.assertAlmostEqual(result['volume_cm3'], int(after.sum())*np.prod(spacing)/1000)

    def test_restore_export_overlay_and_report_keep_provenance(self):
        candidate_id = self.result['id']
        reopened = Lab(self.lab.catalog.data, Path(self.temporary.name))
        self.assertEqual(reopened.list_pre_edits('HCC_002', self.pre, 0, 2)['candidates'][0]['id'], candidate_id)
        with ZipFile(BytesIO(reopened.candidates.export(candidate_id))) as archive:
            metadata = json.loads(archive.read('metadata.json'))
            self.assertEqual(metadata['method']['name'], 'boundary_graph_cut')
            self.assertFalse(metadata['method']['resampling'])
            self.assertFalse(metadata['method']['uses_post_image'])
            self.assertEqual(metadata['parameters']['band_mm'], 3)
            self.assertEqual(metadata['source_seg_sha256'], self.source_hash)
            self.assertTrue(all(len(s['sha256']) == 64 for s in metadata['source']['slices']))
            with np.load(BytesIO(archive.read('mask.npz')), allow_pickle=False) as saved:
                np.testing.assert_array_equal(saved['mask'], self.refined)
        report = reopened.report('HCC_002', self.pre, 0, self.post, 0, 2, pre_candidate_id=candidate_id)
        self.assertEqual(report['pre_segmentation_measurement']['voxels'], 324430)
        self.assertEqual(report['pre_edited_candidate']['id'], candidate_id)
        self.assertIsNone(report['post_segmentation_measurement'])
        self.assertIsNone(report['volume_change_percent'])
        pixels = np.asarray(Image.open(BytesIO(reopened.mask_image(self.pre, 0, 75, candidate_id=candidate_id))))
        np.testing.assert_array_equal(pixels > 0, self.refined[75])
        self.assertTrue(reopened.image(self.pre, 0, 75, 50, 350, candidate_id=candidate_id,
                                       mask_style='combined', mask_opacity=15).startswith(b'\x89PNG'))

    def test_wrong_stage_geometry_segment_and_parameters_are_rejected(self):
        with self.assertRaises(GeometryError):
            self.lab.create_pre_refinement(dict(self.payload, series_uid=self.post))
        with self.assertRaises(GeometryError):
            self.lab.create_pre_refinement(dict(self.payload, segment=99))
        for value in (0, 6, True, '3', float('nan'), float('inf'), 10**400):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.lab.create_pre_refinement(dict(self.payload, band_mm=value))
        with self.assertRaises(ValueError):
            self.lab.create_pre_refinement(dict(self.payload, unexpected=True))
        with self.assertRaises(GeometryError):
            self.lab.candidate_for_stack(self.result['id'], self.pre, 1)
        with self.assertRaises(GeometryError):
            self.lab.report('HCC_002', self.pre, 0, self.post, 0, 1, pre_candidate_id=self.result['id'])
        with self.assertRaises(GeometryError):
            self.lab.report('HCC_002', self.pre, 0, self.post, 0, 2, candidate_id=self.result['id'])


if __name__ == '__main__':
    unittest.main()
