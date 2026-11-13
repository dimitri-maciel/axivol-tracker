"""Synthetic source selection and persistence checks; no DICOM study or model run."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from hcclab.candidates import file_sha256, stack_identity
from hcclab.geometry import GeometryError, Slice, build_stack
from hcclab.lab import Lab
from hcclab.post_automatic import AutomaticSegmentationError


class AutomaticContextSourcesTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        paths = [self.root / name for name in ('pre-a', 'pre-b', 'post-a', 'post-b', 'seg')]
        for path in paths:
            path.write_bytes(('synthetic source ' + path.name).encode())
        self.source_bytes = {path: path.read_bytes() for path in paths}

        def stack_for(files, uid, acquisition):
            first = Slice(files[0], uid + '.1', acquisition, uid + '.9', (2, 2), (.7, .8),
                          (1, 0, 0, 0, 1, 0), (10, 20, 30))
            return build_stack([first, replace(first, path=files[1], sop=uid + '.2', position=(10, 20, 32))])

        self.pre = stack_for(paths[:2], '1.2', '3')
        self.post = stack_for(paths[2:4], '1.3', '1')
        self.seg = (paths[4], SimpleNamespace(SOPInstanceUID='1.4'))
        self.options = [{'number': 1, 'label': 'Liver'}, {'number': 2, 'label': 'Mass'}]
        self.mass = np.zeros((2, 2, 2), bool); self.mass[0, 0, 0] = True
        self.liver = np.zeros_like(self.mass); self.liver[1, 1, 1] = True
        self.output = np.zeros_like(self.mass); self.output[1, 0, :] = True; self.output[1, 1, 0] = True
        self.pre_hu = np.zeros(self.mass.shape, np.float32)
        self.post_hu = np.full(self.mass.shape, 42, np.float32)
        self.valid = np.ones_like(self.mass)
        self.lab = Lab(self.root, self.root / 'annotations')
        self.lab.pre_stack = Mock(return_value=({'StudyInstanceUID': '1.2.8'}, self.pre))
        self.lab.post_stack = Mock(return_value=({'StudyInstanceUID': '1.3.8'}, self.post))
        self.lab.hu_volume = Mock(side_effect=lambda uid, index, hashes:
                                  (self.pre_hu if uid == '1.2' else self.post_hu, self.valid))
        self.payload = {'case_id': 'SYNTHETIC', 'series_uid': '1.3', 'stack_index': 0,
                        'pre_series_uid': '1.2', 'pre_stack_index': 0, 'segment': 2}
        self.enterContext(patch('hcclab.lab.find_seg', return_value=self.seg))
        self.enterContext(patch('hcclab.lab.verify_pre_sources', return_value=self.seg))
        self.enterContext(patch('hcclab.lab.segment_options', side_effect=lambda header: self.options))
        self.enterContext(patch('hcclab.lab.importlib.metadata.version', return_value='synthetic-test'))
        self.decode = self.enterContext(patch('hcclab.lab.referenced_mask', side_effect=
                                              lambda path, header, number, stack: self.mass if number == 2 else self.liver))
        self.generate = self.enterContext(patch('hcclab.lab.automatic_post_candidate', return_value=(
            self.output.copy(), {'registration': {'status': 'estimated_unverified'},
                                 'segmentation': {'voxels': 3, 'display_slice_index': 1,
                                                  'model_input_resized': True,
                                                  'inference_field_yx': [[0, 0], [2, 2]]}})))

    def assert_unsaved_and_unlocked(self):
        self.assertFalse(self.lab.segmentation_lock.locked())
        self.assertFalse(self.lab.candidates.root.exists())
        for path, original in self.source_bytes.items():
            self.assertEqual(path.read_bytes(), original)

    def test_missing_liver_rejects_before_decoding_or_model(self):
        self.options = [{'number': 2, 'label': 'Mass'}]
        with self.assertRaisesRegex(GeometryError, 'one published Liver'):
            self.lab.create_automatic_post_candidate(self.payload)
        self.decode.assert_not_called()
        self.generate.assert_not_called()
        self.assert_unsaved_and_unlocked()

    def test_duplicate_liver_labels_reject_ambiguous_context(self):
        self.options.append({'number': 3, 'label': ' liver '})
        with self.assertRaisesRegex(GeometryError, 'one published Liver'):
            self.lab.create_automatic_post_candidate(self.payload)
        self.decode.assert_not_called()
        self.generate.assert_not_called()
        self.assert_unsaved_and_unlocked()

    def test_empty_liver_rejects_without_falling_back_to_mass_context(self):
        self.liver[:] = False
        with self.assertRaisesRegex(GeometryError, 'Liver segment is empty'):
            self.lab.create_automatic_post_candidate(self.payload)
        self.generate.assert_not_called()
        self.assert_unsaved_and_unlocked()

    def test_native_post_output_and_original_context_provenance_are_saved(self):
        original_mass, original_liver = self.mass.copy(), self.liver.copy()
        document = self.lab.create_automatic_post_candidate(self.payload)
        args, kwargs = self.generate.call_args
        self.assertIs(args[0], self.pre_hu)
        self.assertIs(args[1], self.post_hu)
        self.assertIs(args[2], self.mass)
        np.testing.assert_array_equal(kwargs['pre_context_mask'], original_liver | original_mass)
        np.testing.assert_array_equal(self.mass, original_mass)
        np.testing.assert_array_equal(self.liver, original_liver)
        self.assertEqual([call.args[2] for call in self.decode.call_args_list], [2, 1])
        context = [{'number': 2, 'label': 'Mass'}, {'number': 1, 'label': 'Liver'}]
        baseline = document['baseline_source']
        self.assertEqual(baseline['registration_context_segments'], context)
        self.assertEqual(document['diagnostics']['registration']['context_segments'], context)
        self.assertEqual((baseline['series_uid'], baseline['acquisition'], baseline['source_segment']), ('1.2', '3', 2))
        self.assertEqual(baseline['source_seg_sha256'], file_sha256(self.seg[0]))
        self.assertEqual(baseline['source']['stack_identity'], stack_identity(self.pre))
        self.assertEqual(document['source']['stack_identity'], stack_identity(self.post))
        self.assertEqual((document['series_uid'], document['acquisition'], document['stage']), ('1.3', '1', 'POST'))
        self.assertEqual(document['status'], 'unreviewed')
        self.assertEqual(document['method']['version'], '2.0.0')
        self.assertEqual(document['method']['protocol'], 'liver_context_local_medsam2')
        self.assertEqual(document['method']['inference_field_yx'], [[0, 0], [2, 2]])
        self.assertEqual(document['method']['outside_inference_field'], 'not_assessed')
        self.assertTrue(document['method']['model_input_resized'])
        self.assertEqual(document['voxels'], 3)
        self.assertAlmostEqual(document['volume_cm3'], 3 * .7 * .8 * 2 / 1000)
        np.testing.assert_array_equal(self.lab.candidates.mask(document['id']), self.output)
        self.assertFalse(self.lab.segmentation_lock.locked())
        for path, original in self.source_bytes.items():
            self.assertEqual(path.read_bytes(), original)

    def test_abstention_preserves_context_sources_without_saving_a_mask(self):
        failure = AutomaticSegmentationError('Synthetic abstention.', {
            'registration': {'status': 'failed'}, 'segmentation': {'status': 'not_started'}})
        self.generate.side_effect = failure
        with self.assertRaises(AutomaticSegmentationError) as caught:
            self.lab.create_automatic_post_candidate(self.payload)
        diagnostic = caught.exception.diagnostics
        self.assertEqual(diagnostic['registration']['context_segments'],
                         [{'number': 2, 'label': 'Mass'}, {'number': 1, 'label': 'Liver'}])
        self.assertEqual(diagnostic['sources']['pre']['source_seg_sha256'], file_sha256(self.seg[0]))
        self.assertEqual(diagnostic['sources']['post']['stack_identity'], stack_identity(self.post))
        self.assertNotIn('mask', diagnostic)
        self.assertNotIn('volume_cm3', diagnostic['segmentation'])
        self.assert_unsaved_and_unlocked()


if __name__ == '__main__':
    unittest.main()
