"""Fidelidad visual y edición PRE separada de la máscara publicada.

El caso público comprueba correspondencia y persistencia, no precisión clínica.
Sus trazos de prueba se guardan únicamente en un directorio temporal.
"""
from dataclasses import replace
from io import BytesIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

import numpy as np
from PIL import Image

from hcclab.candidates import file_sha256
from hcclab.dicom import find_seg, frame_mask, mask_display, referenced_mask
from hcclab.geometry import GeometryError
from hcclab.lab import Lab


class MaskDisplayTests(unittest.TestCase):
    def setUp(self):
        self.window = np.full((9, 9), 100, dtype=np.uint8)
        self.mask = np.zeros((9, 9), dtype=bool)
        self.mask[1:8, 1:8] = True
        self.mask[4, 4] = False

    def test_fill_alpha_only_changes_foreground(self):
        rgb = mask_display(self.window, self.mask, style='fill', opacity=20)
        np.testing.assert_array_equal(rgb[self.mask], np.tile([90, 123, 124], (int(self.mask.sum()), 1)))
        np.testing.assert_array_equal(rgb[~self.mask], np.full((int((~self.mask).sum()), 3), 100))
        invisible = mask_display(self.window, self.mask, style='fill', opacity=0)
        np.testing.assert_array_equal(invisible, np.repeat(self.window[..., None], 3, axis=2))

    def test_outline_includes_holes_and_preserves_interior_and_exterior(self):
        expected_edge = np.zeros_like(self.mask)
        expected_edge[1, 1:8] = expected_edge[7, 1:8] = True
        expected_edge[1:8, 1] = expected_edge[1:8, 7] = True
        expected_edge[3, 4] = expected_edge[5, 4] = True
        expected_edge[4, 3] = expected_edge[4, 5] = True
        rgb = mask_display(self.window, self.mask, style='outline', opacity=0)
        np.testing.assert_array_equal(np.any(rgb != 100, axis=2), expected_edge)
        np.testing.assert_array_equal(rgb[expected_edge], np.tile([55, 202, 210], (int(expected_edge.sum()), 1)))
        self.assertTrue((rgb[4, 4] == 100).all())  # El hueco sigue vacío.

    def test_image_edge_is_contoured_and_combined_uses_interior_opacity(self):
        window = np.full((3, 3), 100, dtype=np.uint8)
        mask = np.ones_like(window, dtype=bool)
        rgb = mask_display(window, mask, style='outline', opacity=100)
        self.assertEqual(np.count_nonzero(np.any(rgb != 100, axis=2)), 8)
        np.testing.assert_array_equal(rgb[1, 1], [100, 100, 100])
        combined = mask_display(window, mask, style='combined', opacity=20)
        np.testing.assert_array_equal(combined[1, 1], [90, 123, 124])
        np.testing.assert_array_equal(combined[0, 0], [55, 202, 210])

    def test_display_never_mutates_or_aliases_sources(self):
        before_mask, before_window = self.mask.copy(), self.window.copy()
        for style in ('fill', 'outline', 'combined'):
            result = mask_display(self.window, self.mask, style=style, opacity=30)
            self.assertFalse(np.shares_memory(result, self.window))
            self.assertFalse(np.shares_memory(result, self.mask))
        np.testing.assert_array_equal(self.mask, before_mask)
        np.testing.assert_array_equal(self.window, before_window)

    def test_invalid_style_opacity_and_mask_are_rejected(self):
        for parameters in ({'style': 'smooth'}, {'opacity': -1}, {'opacity': 101},
                           {'opacity': True}, {'opacity': 20.0}, {'opacity': float('nan')}):
            with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                mask_display(self.window, self.mask, **parameters)
        for mask in (np.zeros((8, 9), dtype=bool), self.mask.astype(np.uint8)):
            with self.subTest(shape=mask.shape), self.assertRaises(GeometryError):
                mask_display(self.window, mask)


@unittest.skipUnless(os.environ.get('HCC_PILOT_TEST') == '1', 'Requires public HCC_002.')
class PreMaskPilotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = TemporaryDirectory()
        cls.addClassCleanup(cls.directory.cleanup)
        cls.lab = Lab(Path(os.environ.get('HCC_DATA_DIR', '/data')), Path(cls.directory.name))
        cls.addClassCleanup(cls.lab.candidates.mask.cache_clear)
        cls.addClassCleanup(cls.lab.verify_candidate_sources.cache_clear)
        case = cls.lab.catalog.case('HCC_002')
        cls.pre, cls.post = [study['series'][0]['uid'] for study in case['studies']]
        row, cls.stack = cls.lab.stack(cls.pre, 0)
        cls.seg = find_seg(cls.lab.catalog, row)
        cls.seg_hash = file_sha256(cls.seg[0])
        cls.original = referenced_mask(*cls.seg, 2, cls.stack)
        # Una cruz pequeña en un corte de máscara vacío prueba el pincel y el
        # guardado; estas coordenadas no representan una corrección anatómica.
        cls.payload = {'case_id': 'HCC_002', 'series_uid': cls.pre, 'stack_index': 0, 'segment': 2,
                       'strokes': [{'slice': 0, 'mode': 'paint', 'radius_mm': .78125, 'points': [[2, 2]]}]}
        cls.candidate = cls.lab.create_pre_edit(cls.payload)

    def test_edited_mask_png_and_provenance_preserve_published_voxels(self):
        candidate = self.candidate
        self.assertEqual(int(self.original.sum()), 324430)
        self.assertEqual((candidate['stage'], candidate['acquisition'], candidate['source_segment']), ('PRE', '1', 2))
        self.assertEqual(candidate['source_segment_label'], 'Mass')
        self.assertEqual(candidate['status'], 'unreviewed')
        self.assertEqual(candidate['shape_zyx'], [109, 512, 512])
        self.assertEqual(candidate['original_voxels'], 324430)
        self.assertEqual(candidate['voxels'], 324435)
        self.assertEqual(candidate['diagnostics'], {'added_voxels': 5, 'removed_voxels': 0,
                                                    'changed_slices': [0], 'voxels': 324435})
        self.assertFalse(candidate['method']['resampling'])
        self.assertEqual(candidate['method']['brush_units'], 'mm')
        self.assertEqual(candidate['parameters']['strokes'], self.payload['strokes'])
        edited = self.lab.candidates.mask(candidate['id'])
        expected = self.original.copy()
        expected[0, 2, 1:4] = True
        expected[0, 1:4, 2] = True
        np.testing.assert_array_equal(edited, expected)
        png = self.lab.mask_image(self.pre, 0, 0, candidate_id=candidate['id'])
        pixels = np.asarray(Image.open(BytesIO(png)))
        self.assertEqual(pixels.shape, (512, 512))
        self.assertEqual(set(np.unique(pixels)), {0, 255})
        np.testing.assert_array_equal(pixels > 0, expected[0])
        original_png = self.lab.mask_image(self.pre, 0, 0, segment=2)
        self.assertFalse(np.asarray(Image.open(BytesIO(original_png))).any())
        self.assertEqual(file_sha256(self.seg[0]), self.seg_hash)
        self.assertEqual(self.lab.measure(self.pre, 0, 2)['voxels'], 324430)
        self.assertAlmostEqual(candidate['volume_cm3'], 324435*.78125**2*2.5/1000)

    def test_restore_export_and_report_keep_edit_separate(self):
        reopened = Lab(self.lab.catalog.data, Path(self.directory.name))
        candidate_id = self.candidate['id']
        listed = reopened.list_pre_edits('HCC_002', self.pre, 0, 2)['candidates']
        self.assertEqual([item['id'] for item in listed], [candidate_id])
        self.assertEqual(reopened.list_pre_edits('HCC_002', self.pre, 0, 1)['candidates'], [])
        with ZipFile(BytesIO(reopened.candidates.export(candidate_id))) as archive:
            self.assertEqual(set(archive.namelist()), {'mask.npz', 'metadata.json'})
            metadata = json.loads(archive.read('metadata.json'))
            self.assertEqual(metadata['source_seg_sha256'], self.seg_hash)
            self.assertEqual(metadata['original_voxels'], 324430)
            self.assertEqual(metadata['status'], 'unreviewed')
            with np.load(BytesIO(archive.read('mask.npz')), allow_pickle=False) as saved:
                np.testing.assert_array_equal(saved['mask'], self.lab.candidates.mask(candidate_id))
        report = reopened.report('HCC_002', self.pre, 0, self.post, 0, segment=2, pre_candidate_id=candidate_id)
        self.assertEqual(report['pre_segmentation_measurement']['voxels'], 324430)
        self.assertEqual(report['pre_edited_candidate']['voxels'], 324435)
        self.assertIsNone(report['post_candidate'])
        self.assertIsNone(report['post_segmentation_measurement'])
        self.assertIsNone(report['volume_change_percent'])

    def test_stage_segment_acquisition_and_empty_changes_cannot_mix(self):
        candidate_id = self.candidate['id']
        with self.assertRaises(GeometryError):
            self.lab.create_pre_edit(dict(self.payload, series_uid=self.post))
        with self.assertRaises(GeometryError):
            self.lab.create_pre_edit(dict(self.payload, segment=99))
        with self.assertRaises(ValueError):
            self.lab.create_pre_edit(dict(self.payload, segment=True))
        with self.assertRaises(ValueError):
            self.lab.create_pre_edit(dict(self.payload, stack_index=True))
        with self.assertRaises(ValueError):
            self.lab.create_pre_edit(dict(self.payload, strokes=[]))
        no_change = dict(self.payload['strokes'][0], mode='erase')
        with self.assertRaisesRegex(ValueError, 'do not change'):
            self.lab.create_pre_edit(dict(self.payload, strokes=[no_change]))
        with self.assertRaises(GeometryError):
            self.lab.candidate_for_stack(candidate_id, self.post, 0)
        with self.assertRaises(GeometryError):
            self.lab.candidate_for_stack(candidate_id, self.pre, 1)
        with self.assertRaises(GeometryError):
            self.lab.report('HCC_002', self.pre, 0, self.post, 0, segment=1, pre_candidate_id=candidate_id)
        with self.assertRaises(GeometryError):
            self.lab.report('HCC_002', self.pre, 0, self.post, 0, segment=2, candidate_id=candidate_id)
        with self.assertRaises(ValueError):
            self.lab.mask_image(self.pre, 0, 0, segment=2, candidate_id=candidate_id)

    def test_referenced_geometry_mismatch_is_explicit(self):
        original_slice = self.stack.slices[0]
        position = list(original_slice.position)
        position[0] += 1
        inconsistent = replace(original_slice, position=tuple(position))
        with self.assertRaisesRegex(GeometryError, 'incompatible'):
            frame_mask(*self.seg, 2, inconsistent)


if __name__ == '__main__':
    unittest.main()
