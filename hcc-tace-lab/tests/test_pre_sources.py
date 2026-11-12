"""Fuentes DICOM sintéticas: rechazar cabeceras reemplazadas tras indexar.

No se modifican datos públicos ni cachés del visor. Los CT omiten parámetros
HU deliberadamente: un pincel manual solo necesita identidad y geometría.
"""
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
import unittest
from unittest.mock import Mock, patch

import numpy as np
from pydicom import dcmread, dcmwrite
from pydicom.dataset import Dataset, FileDataset, FileMetaDataset
from pydicom.uid import CTImageStorage, ExplicitVRLittleEndian, SegmentationStorage, generate_uid

from hcclab.candidates import CandidateStore, file_sha256
from hcclab.dicom import seg_header, stacks, verify_pre_sources
from hcclab.geometry import GeometryError
from hcclab.lab import Lab


class PreSourceTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.ct_folder = self.root / 'ct'
        self.ct_folder.mkdir()
        self.study, self.series, self.frame = generate_uid(), generate_uid(), generate_uid()
        for z in range(2):
            path = self.ct_folder / f'{z}.dcm'
            ds = self.dataset(path, CTImageStorage)
            ds.Modality, ds.StudyInstanceUID, ds.SeriesInstanceUID = 'CT', self.study, self.series
            ds.FrameOfReferenceUID, ds.AcquisitionNumber = self.frame, '1'
            ds.Rows = ds.Columns = 8
            ds.ImagePositionPatient = [0, 0, z * 2.5]
            ds.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
            ds.PixelSpacing = [.7, .8]
            ds.SamplesPerPixel, ds.PhotometricInterpretation = 1, 'MONOCHROME2'
            ds.BitsAllocated, ds.BitsStored, ds.HighBit, ds.PixelRepresentation = 16, 16, 15, 1
            ds.PixelData = np.zeros((8, 8), dtype=np.int16).tobytes()
            dcmwrite(path, ds, enforce_file_format=True)
        self.stack = stacks(self.ct_folder, self.series)[0]
        self.seg_path = self.root / 'seg.dcm'
        ds = self.dataset(self.seg_path, SegmentationStorage)
        ds.Modality, ds.SegmentationType = 'SEG', 'BINARY'
        ds.StudyInstanceUID, ds.SeriesInstanceUID, ds.FrameOfReferenceUID = self.study, generate_uid(), self.frame
        ds.Rows = ds.Columns = 8
        ds.NumberOfFrames = 2
        ds.SamplesPerPixel, ds.PhotometricInterpretation = 1, 'MONOCHROME2'
        ds.BitsAllocated, ds.BitsStored, ds.HighBit, ds.PixelRepresentation = 1, 1, 0, 0
        segment = Dataset()
        segment.SegmentNumber, segment.SegmentLabel = 2, 'Mass'
        ds.SegmentSequence = [segment]
        shared, orientation, measures = Dataset(), Dataset(), Dataset()
        orientation.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
        measures.PixelSpacing = [.7, .8]
        shared.PlaneOrientationSequence, shared.PixelMeasuresSequence = [orientation], [measures]
        ds.SharedFunctionalGroupsSequence = [shared]
        frames = []
        for s in self.stack.slices:
            frame, identification, position, derivation, source = (Dataset() for _ in range(5))
            identification.ReferencedSegmentNumber = 2
            position.ImagePositionPatient = list(s.position)
            source.ReferencedSOPInstanceUID = s.sop
            derivation.SourceImageSequence = [source]
            frame.SegmentIdentificationSequence, frame.PlanePositionSequence = [identification], [position]
            frame.DerivationImageSequence = [derivation]
            frames.append(frame)
        ds.PerFrameFunctionalGroupsSequence = frames
        mask = np.zeros((2, 8, 8), dtype=bool)
        mask[0, 2:4, 2:4] = True
        ds.PixelData = np.packbits(mask.reshape(-1), bitorder='little').tobytes()
        dcmwrite(self.seg_path, ds, enforce_file_format=True)
        self.seg = (self.seg_path, seg_header(self.seg_path))
        self.expected = dict(expected_series_uid=self.series, expected_study_uid=self.study)

    @staticmethod
    def dataset(path, sop_class):
        meta = FileMetaDataset()
        meta.TransferSyntaxUID, meta.MediaStorageSOPClassUID = ExplicitVRLittleEndian, sop_class
        meta.MediaStorageSOPInstanceUID = generate_uid()
        ds = FileDataset(str(path), {}, file_meta=meta, preamble=b'\0' * 128)
        ds.SOPClassUID, ds.SOPInstanceUID = sop_class, meta.MediaStorageSOPInstanceUID
        return ds

    @staticmethod
    def replace_header(path, change):
        ds = dcmread(path)
        change(ds)
        dcmwrite(path, ds, enforce_file_format=True)

    def operation(self):
        lab = Lab.__new__(Lab)
        lab.catalog = Mock()
        lab.candidates = CandidateStore(self.root / 'annotations')
        lab.segmentation_lock = Lock()
        lab.pre_stack = Mock(return_value=({'StudyInstanceUID': self.study}, self.stack))
        payload = {'case_id': 'SYNTHETIC', 'series_uid': self.series, 'stack_index': 0, 'segment': 2,
                   'strokes': [{'slice': 1, 'mode': 'paint', 'radius_mm': .1, 'points': [[0, 0]]}]}
        return lab, payload

    def test_unchanged_sources_are_reloaded_without_hu_requirement(self):
        path, current = verify_pre_sources(self.stack, self.seg, **self.expected)
        self.assertEqual(path, self.seg_path)
        self.assertEqual(current, self.seg[1])
        self.assertIsNot(current, self.seg[1])
        before = file_sha256(self.seg_path)
        lab, payload = self.operation()
        with patch('hcclab.lab.find_seg', return_value=self.seg):
            candidate = lab.create_pre_edit(payload)
        self.assertEqual((candidate['original_voxels'], candidate['voxels']), (4, 5))
        self.assertEqual(candidate['source_seg_sha256'], before)
        self.assertEqual(file_sha256(self.seg_path), before)

    def test_replaced_ct_identity_and_geometry_are_rejected_before_editing(self):
        path = self.stack.slices[0].path
        original = path.read_bytes()
        changes = {
            'StudyInstanceUID': generate_uid(), 'SeriesInstanceUID': generate_uid(),
            'SOPInstanceUID': generate_uid(), 'FrameOfReferenceUID': generate_uid(),
            'AcquisitionNumber': '2', 'Rows': 9, 'NumberOfFrames': 2, 'Modality': 'MR',
            'ImagePositionPatient': [1, 0, 0], 'PixelSpacing': [1, 1],
            'ImageOrientationPatient': [0, 1, 0, 1, 0, 0],
        }
        lab, payload = self.operation()
        for key, value in changes.items():
            path.write_bytes(original)
            self.replace_header(path, lambda ds: setattr(ds, key, value))
            with self.subTest(field=key), patch('hcclab.lab.find_seg', return_value=self.seg), \
                    patch('hcclab.lab.referenced_mask') as decode:
                with self.assertRaisesRegex(GeometryError, 'CT source'):
                    lab.create_pre_edit(payload)
                decode.assert_not_called()
                self.assertFalse(lab.segmentation_lock.locked())
        self.assertFalse(lab.candidates.root.exists())

    def test_replaced_seg_header_is_rejected_before_decoding(self):
        original = self.seg_path.read_bytes()
        changes = [
            lambda ds: setattr(ds, 'SOPInstanceUID', generate_uid()),
            lambda ds: setattr(ds.SegmentSequence[0], 'SegmentLabel', 'Liver'),
            lambda ds: setattr(ds.SharedFunctionalGroupsSequence[0].PixelMeasuresSequence[0], 'PixelSpacing', [1, 1]),
            lambda ds: setattr(ds.PerFrameFunctionalGroupsSequence[0].DerivationImageSequence[0].SourceImageSequence[0],
                               'ReferencedSOPInstanceUID', generate_uid()),
        ]
        lab, payload = self.operation()
        for index, change in enumerate(changes):
            self.seg_path.write_bytes(original)
            self.replace_header(self.seg_path, change)
            with self.subTest(change=index), patch('hcclab.lab.find_seg', return_value=self.seg), \
                    patch('hcclab.lab.referenced_mask') as decode:
                with self.assertRaisesRegex(GeometryError, 'SEG header changed'):
                    lab.create_pre_edit(payload)
                decode.assert_not_called()
                self.assertFalse(lab.segmentation_lock.locked())
        self.assertFalse(lab.candidates.root.exists())


if __name__ == '__main__':
    unittest.main()
