"""Native export and blind-package separation, using synthetic images only."""
import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zipfile import ZipFile

import numpy as np
import SimpleITK as sitk

from experiments.prepare_reference import geometry, new_folder, prepare, verify_candidate, write_native
from hcclab.candidates import CandidateStore, file_sha256, stack_identity
from hcclab.geometry import GeometryError, Slice, build_stack
from hcclab.registration import index_to_patient


def fixture(root):
    shape = (4, 6, 7)
    selection = {'pre_series_uid': 'pre-uid', 'post_series_uid': 'post-uid',
                 'pre_acquisition': '3', 'post_acquisition': '1', 'mass_segment': 2, 'liver_segment': 1}
    sources = {'paths': [], 'hashes': [], 'provenance': {'case_id': 'synthetic', 'selection': selection}}
    for phase in ('pre', 'post'):
        slices = []
        for z in range(shape[0]):
            path = root / (phase + str(z) + '.bin'); path.write_bytes((phase + str(z)).encode())
            sources['paths'].append(path); sources['hashes'].append(file_sha256(path))
            slices.append(Slice(path, phase + '-sop-' + str(z), selection[phase + '_acquisition'],
                                phase + '-frame', shape[1:], (.8, .6), (0, 1, 0, -1, 0, 0), (10, 20, 30 + 2.5*z)))
        stack = build_stack(slices)
        sources[phase + '_stack'] = stack
        sources[phase + '_hu'] = np.arange(np.prod(shape), dtype=np.float32).reshape(shape) - 2000
        sources[phase + '_valid'] = np.ones(shape, bool)
        sources['provenance'][phase] = {'ordered_ct_sha256': [file_sha256(s.path) for s in stack.slices]}
    seg = root / 'seg.bin'; seg.write_bytes(b'published-seg')
    sources['paths'].append(seg); sources['hashes'].append(file_sha256(seg))
    sources['provenance']['seg_sha256'] = file_sha256(seg)
    mass = np.zeros(shape, bool); mass[1:3, 2:4, 3:5] = True
    sources['mass'] = mass
    def source(phase):
        stack = sources[phase + '_stack']
        return {'stack_identity': stack_identity(stack), 'slices': [
            {'sop_uid': s.sop, 'sha256': file_sha256(s.path)} for s in stack.slices]}
    metadata = {'status': 'unreviewed', 'stage': 'POST', 'case_id': 'synthetic',
                'series_uid': 'post-uid', 'acquisition': '1', 'shape_zyx': list(shape),
                'voxels': int(mass.sum()), 'source': source('post'),
                'baseline_source': {'series_uid': 'pre-uid', 'acquisition': '3',
                                    'source_segment': 2, 'source_segment_label': 'Mass',
                                    'source_seg_sha256': file_sha256(seg), 'source': source('pre')}}
    return sources, metadata


class NativeExportTests(unittest.TestCase):
    def test_native_hu_and_oblique_physical_points_survive_nrrd_roundtrip(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary); sources, _ = fixture(root)
            array, stack = sources['post_hu'], sources['post_stack']
            before = array.copy()
            record = write_native(root / 'ct.nrrd', array, stack)
            image = sitk.ReadImage(str(root / 'ct.nrrd'))
            np.testing.assert_array_equal(sitk.GetArrayViewFromImage(image), before)
            np.testing.assert_array_equal(array, before)
            self.assertEqual(record['geometry'], geometry(image))
            np.testing.assert_allclose(image.TransformIndexToPhysicalPoint((4, 3, 2)),
                                       index_to_patient([2, 3, 4], stack))
            self.assertEqual(image.GetSpacing(), (.6, .8, 2.5))

    def test_shape_or_nonfinite_export_is_rejected(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary); sources, _ = fixture(root)
            bad = sources['post_hu'].copy(); bad[0, 0, 0] = np.nan
            for array in (bad, sources['post_hu'][:2]):
                with self.assertRaises(GeometryError):
                    write_native(root / 'invalid.nrrd', array, sources['post_stack'])
            self.assertFalse((root / 'invalid.nrrd').exists())

    def test_candidate_identity_and_source_bytes_cannot_be_replaced(self):
        with TemporaryDirectory() as temporary:
            sources, document = fixture(Path(temporary))
            verify_candidate(sources, document, sources['mass'])
            bad = copy.deepcopy(document); bad['source']['slices'][0]['sha256'] = '0'*64
            with self.assertRaisesRegex(GeometryError, 'source CT bytes'):
                verify_candidate(sources, bad, sources['mass'])
            bad = copy.deepcopy(document); bad['baseline_source']['source_seg_sha256'] = '0'*64
            with self.assertRaisesRegex(GeometryError, 'published PRE Mass'):
                verify_candidate(sources, bad, sources['mass'])
            bad = copy.deepcopy(document); bad['acquisition'] = '2'
            with self.assertRaises(GeometryError):
                verify_candidate(sources, bad, sources['mass'])

    def test_prediction_on_padding_is_not_frozen_as_valid_candidate(self):
        with TemporaryDirectory() as temporary:
            sources, document = fixture(Path(temporary))
            sources['post_valid'][1, 2, 3] = False
            with self.assertRaisesRegex(GeometryError, 'without padding'):
                verify_candidate(sources, document, sources['mass'])


class BlindPackageTests(unittest.TestCase):
    def test_reader_has_no_predicted_mask_and_no_expert_claims_are_prefilled(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary); sources, document = fixture(root)
            annotations = root / 'annotations'
            candidate = CandidateStore(annotations).save(sources['mass'], document)
            with patch('experiments.prepare_reference.read_sources', return_value=sources):
                folder = prepare(root / 'data', annotations, root / 'out', 'synthetic',
                                 sources['provenance']['selection'], candidate['id'])
            reader = folder / 'reader'
            with ZipFile(folder / 'reader.zip') as archive:
                self.assertEqual(set(archive.namelist()), {'reader/' + p.name for p in reader.iterdir()})
                self.assertFalse(any('candidate' in name or 'manager' in name for name in archive.namelist()))
            self.assertEqual(sorted(p.name for p in reader.glob('*.nrrd')),
                             ['POST_CT.nrrd', 'PRE_CT.nrrd', 'PRE_Mass.nrrd'])
            self.assertTrue((reader / 'Open_HCC_in_Slicer.command').stat().st_mode & 0o111)
            form = json.loads((reader / 'reader_form.json').read_text())
            for key in ('reader_id', 'qualifications', 'hepatic_imaging_expertise_attested',
                        'model_output_seen', 'independently_drawn', 'annotation_sha256', 'reviewed_utc'):
                self.assertIsNone(form[key])
            self.assertEqual(form['reference_ct_sha256'], file_sha256(reader / 'POST_CT.nrrd'))
            case_text = (reader / 'case.json').read_text()
            self.assertNotIn(candidate['id'], case_text)
            self.assertNotIn('voxels', case_text)
            manifest = json.loads((folder / 'manager/evaluation_manifest.json').read_text())
            self.assertEqual(manifest['candidate']['id'], candidate['id'])
            self.assertEqual(manifest['reader_case_sha256'], file_sha256(reader / 'case.json'))
            status = json.loads((folder / 'preparation.json').read_text())
            self.assertFalse(status['expert_reference_available'])
            self.assertFalse(status['accuracy_metrics_computed'])

    def test_output_cannot_be_inside_either_source_store(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            data, annotations = root / 'data', root / 'annotations'
            for output in (data, data / 'export', annotations / 'export'):
                with self.assertRaises(ValueError):
                    new_folder(output, data, annotations, 'synthetic')
            with self.assertRaises(ValueError):
                new_folder(root / 'out', data, annotations, '../../outside')

    def test_source_change_aborts_and_removes_only_incomplete_package(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary); sources, document = fixture(root)
            annotations = root / 'annotations'
            candidate = CandidateStore(annotations).save(sources['mass'], document)
            out = root / 'out'; out.mkdir(); (out / 'existing.txt').write_text('keep')
            with patch('experiments.prepare_reference.read_sources', return_value=sources), \
                 patch('experiments.prepare_reference.verify_unchanged', side_effect=GeometryError('changed')):
                with self.assertRaisesRegex(GeometryError, 'changed'):
                    prepare(root / 'data', annotations, out, 'synthetic',
                            sources['provenance']['selection'], candidate['id'])
            self.assertEqual([p.name for p in out.iterdir()], ['existing.txt'])
            self.assertTrue((annotations / candidate['id'] / 'mask.npz').exists())


if __name__ == '__main__':
    unittest.main()
