"""Pruebas con volúmenes conocidos; no son validación de lesiones de pacientes."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from pydicom import dcmread, dcmwrite
from pydicom.dataset import FileDataset, FileMetaDataset
from pydicom.uid import CTImageStorage, ExplicitVRLittleEndian, generate_uid

from hcclab.geometry import GeometryError, Slice, Stack, build_stack
from hcclab.segmentation import grow_region
from hcclab.volume import load_hu_volume


class RegionGrowingTests(unittest.TestCase):
    def setUp(self):
        self.hu = np.full((7, 7, 7), -1000, dtype=np.float32)
        self.hu[1:3, 1:3, 1:3] = 50
        self.roi = [[0, 0, 0], [7, 7, 7]]

    def test_only_seed_component_no_diagonal_connection_and_native_grid(self):
        self.hu[3, 3, 3] = 50  # Se toca por esquina, no por cara.
        self.hu[5, 5, 5] = 50  # Componente separada con la misma intensidad.
        original = self.hu.copy()
        mask, info = grow_region(self.hu, [1, 1, 1], self.roi, 40, 60)
        expected = np.zeros_like(self.hu, dtype=bool)
        expected[1:3, 1:3, 1:3] = True
        np.testing.assert_array_equal(mask, expected)
        np.testing.assert_array_equal(self.hu, original)
        self.assertEqual(info['voxels'], 8)
        self.assertEqual(info['bounds'], [[1, 1, 1], [3, 3, 3]])
        self.assertEqual(info['roi_touch_faces'], [])
        self.assertEqual(info['connectivity'], 6)
        self.assertFalse(np.shares_memory(mask, self.hu))

    def test_out_of_range_barrier_stops_region(self):
        hu = np.zeros((5, 5, 7), dtype=np.int16)
        hu[2, 2, :] = 10
        hu[2, 2, 3] = 100
        mask, info = grow_region(hu, [2, 2, 1], [[0, 0, 0], [5, 5, 7]], 10, 10)
        self.assertEqual(info['voxels'], 3)
        self.assertTrue(mask[2, 2, :3].all())
        self.assertFalse(mask[2, 2, 3:].any())

    def test_roi_is_half_open_and_boundary_contact_is_reported(self):
        hu = np.full((5, 5, 5), 50, dtype=np.float32)
        mask, info = grow_region(hu, [2, 2, 2], [[1, 1, 1], [4, 4, 4]], 50, 50)
        self.assertEqual(np.count_nonzero(mask), 27)
        self.assertFalse(mask[4].any())
        self.assertEqual(info['bounds'], [[1, 1, 1], [4, 4, 4]])
        self.assertEqual(info['roi_fraction'], 1)
        self.assertEqual(info['roi_touch_faces'], ['z_min','z_max','y_min','y_max','x_min','x_max'])

    def test_hu_endpoints_are_inclusive_and_run_is_reproducible(self):
        self.hu[1, 1, 1] = 40
        self.hu[1, 1, 2] = 60
        a, da = grow_region(self.hu, [1, 1, 1], self.roi, 40, 60)
        b, db = grow_region(self.hu, [1, 1, 1], self.roi, 40, 60)
        np.testing.assert_array_equal(a, b)
        self.assertEqual(da, db)
        self.assertEqual(da['voxels'], 8)

    def test_padding_cannot_seed_or_bridge_a_component(self):
        hu = np.full((3, 3, 5), -2000, dtype=np.float32)
        valid = np.zeros_like(hu, dtype=bool)
        valid[1, 1, 0:2] = True
        valid[1, 1, 3:5] = True
        box = [[0, 0, 0], [3, 3, 5]]
        mask, info = grow_region(hu, [1, 1, 0], box, -2000, -2000, valid_mask=valid)
        self.assertEqual(info['voxels'], 2)
        self.assertFalse(mask[1, 1, 3])
        with self.assertRaisesRegex(ValueError, 'padding'):
            grow_region(hu, [1, 1, 2], box, -2000, -2000, valid_mask=valid)

    def test_bad_seed_roi_threshold_and_validity_are_rejected(self):
        invalid = [
            {'seed':[True, 1, 1]}, {'seed':[1.0, 1, 1]}, {'seed':[-1, 1, 1]},
            {'seed':np.array(1)}, {'seed':[7, 1, 1]}, {'seed':[0, 0, 0]},
            {'roi':[[1, 1, 1], [1, 3, 3]]}, {'roi':[[0, 0, 0], [8, 7, 7]]},
            {'roi':[[False, 0, 0], [7, 7, 7]]}, {'roi':np.array(2)},
            {'lower_hu':61}, {'lower_hu':True}, {'upper_hu':float('inf')},
            {'lower_hu':float('nan')}, {'upper_hu':'60'},
            {'lower_hu':10**400},
            {'valid_mask':np.ones((2, 2, 2), dtype=bool)},
            {'valid_mask':np.ones_like(self.hu, dtype=np.uint8)},
        ]
        for values in invalid:
            args={'volume_hu':self.hu,'seed':[1,1,1],'roi':self.roi,'lower_hu':40,'upper_hu':60}
            args.update(values)
            with self.subTest(values=values.keys()), self.assertRaises(ValueError):
                grow_region(**args)

    def test_nan_even_outside_roi_and_invalid_volume_type_are_rejected(self):
        self.hu[6, 6, 6] = np.nan
        with self.assertRaisesRegex(ValueError, 'NaN'):
            grow_region(self.hu, [1, 1, 1], [[1, 1, 1], [3, 3, 3]], 40, 60)
        for invalid in [np.ones((3, 3)), np.ones((3, 3, 3), dtype=bool),
                        np.ones((3, 3, 3), dtype=complex), np.empty((0, 3, 3))]:
            with self.subTest(dtype=invalid.dtype), self.assertRaises(ValueError):
                grow_region(invalid, [1, 1, 1], self.roi, 40, 60)

    def test_size_limits_fail_before_label_allocation(self):
        with patch('hcclab.segmentation.MAX_VOLUME_VOXELS', 342):
            with self.assertRaisesRegex(ValueError, 'volume exceeds'):
                grow_region(self.hu, [1, 1, 1], self.roi, 40, 60)
        with patch('hcclab.segmentation.MAX_ROI_VOXELS', 7):
            with self.assertRaisesRegex(ValueError, 'ROI exceeds'):
                grow_region(self.hu, [1, 1, 1], [[1, 1, 1], [3, 3, 3]], 40, 60)


class QuantitativeVolumeTests(unittest.TestCase):
    """DICOM sintético: verifica transformaciones y estados rechazados."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        study, series, frame = generate_uid(), generate_uid(), generate_uid()
        slices = []
        self.raw = np.array([[-2000, -1999, 0], [10, 20, 30]], dtype=np.int16)
        for z in range(3):
            path = Path(self.temp.name)/f'{z}.dcm'
            meta = FileMetaDataset()
            meta.TransferSyntaxUID = ExplicitVRLittleEndian
            meta.MediaStorageSOPClassUID = CTImageStorage
            meta.MediaStorageSOPInstanceUID = generate_uid()
            ds = FileDataset(str(path), {}, file_meta=meta, preamble=b'\0'*128)
            ds.SOPClassUID, ds.SOPInstanceUID = CTImageStorage, meta.MediaStorageSOPInstanceUID
            ds.Modality, ds.StudyInstanceUID, ds.SeriesInstanceUID = 'CT', study, series
            ds.FrameOfReferenceUID, ds.AcquisitionNumber = frame, '1'
            ds.Rows, ds.Columns = self.raw.shape
            ds.ImagePositionPatient = [0, 0, z*2.5]
            ds.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
            ds.PixelSpacing = [.7, .8]
            ds.ImageType = ['ORIGINAL', 'PRIMARY', 'AXIAL']
            ds.SamplesPerPixel, ds.PhotometricInterpretation = 1, 'MONOCHROME2'
            ds.BitsAllocated, ds.BitsStored, ds.HighBit, ds.PixelRepresentation = 16, 16, 15, 1
            ds.RescaleSlope, ds.RescaleIntercept = 2, -1000
            ds.add_new((0x0028, 0x0120), 'US', 63536)  # Caso signed heredado de HCC_002.
            ds.PixelData = self.raw.tobytes()
            dcmwrite(path, ds, enforce_file_format=True)
            slices.append(Slice(path, str(ds.SOPInstanceUID), '1', frame, self.raw.shape,
                                (.7, .8), (1, 0, 0, 0, 1, 0), (0, 0, z*2.5)))
        self.stack = build_stack(slices)

    def edit(self, index, change):
        path = self.stack.slices[index].path
        ds = dcmread(path)
        change(ds)
        dcmwrite(path, ds, enforce_file_format=True)

    def test_rescale_native_geometry_and_conditional_hu_units(self):
        hu, valid = load_hu_volume(self.stack, return_validity=True)
        self.assertEqual(hu.dtype, np.float32)
        self.assertEqual(hu.shape, (3, 2, 3))
        np.testing.assert_array_equal(hu[0], self.raw.astype(np.float32)*2-1000)
        self.assertFalse(valid[:, 0, 0].any())
        self.assertTrue(valid[:, 0, 1:].all())
        np.testing.assert_array_equal(hu, load_hu_volume(self.stack))

    def test_padding_range_is_inclusive_in_stored_values(self):
        for z in range(3):
            self.edit(z, lambda ds: ds.add_new((0x0028, 0x0121), 'SS', -1999))
        hu, valid = load_hu_volume(self.stack, return_validity=True)
        self.assertFalse(valid[:, 0, :2].any())
        self.assertTrue(valid[:, 1, :].all())
        # El padding no entra aunque su HU esté dentro del umbral solicitado.
        with self.assertRaisesRegex(ValueError, 'padding'):
            grow_region(hu, [0, 0, 0], [[0, 0, 0], [3, 2, 3]], -6000, 0, valid_mask=valid)

    def test_missing_or_non_hu_rescale_is_rejected(self):
        for mutate in [lambda ds: ds.__delattr__('RescaleSlope'),
                       lambda ds: setattr(ds, 'RescaleType', 'US'),
                       lambda ds: setattr(ds, 'ImageType', ['DERIVED', 'PRIMARY', 'AXIAL'])]:
            original = self.stack.slices[0].path.read_bytes()
            self.edit(0, mutate)
            with self.subTest(change=mutate), self.assertRaises(GeometryError):
                load_hu_volume(self.stack)
            self.stack.slices[0].path.write_bytes(original)

    def test_multiframe_and_multi_energy_are_rejected(self):
        for mutate in [lambda ds: setattr(ds, 'NumberOfFrames', 2),
                       lambda ds: ds.add_new((0x0018, 0x9361), 'CS', 'YES')]:
            original = self.stack.slices[0].path.read_bytes()
            self.edit(0, mutate)
            with self.subTest(change=mutate), self.assertRaises(GeometryError):
                load_hu_volume(self.stack)
            self.stack.slices[0].path.write_bytes(original)

    def test_lateral_shift_and_irregular_interval_are_rejected(self):
        shifted = list(self.stack.slices)
        shifted[1] = replace(shifted[1], position=(.05, 0, 2.5))
        with self.assertRaisesRegex(GeometryError, 'Laterally'):
            load_hu_volume(build_stack(shifted))
        shifted[1] = replace(shifted[1], position=(0, 0, 3))
        with self.assertRaisesRegex(GeometryError, 'regular'):
            load_hu_volume(build_stack(shifted))
        with self.assertRaises(GeometryError):
            load_hu_volume(Stack(self.stack.slices, None))

    def test_changed_sop_and_mixed_studies_are_rejected(self):
        for mutate in [lambda ds: setattr(ds, 'SOPInstanceUID', generate_uid()),
                       lambda ds: setattr(ds, 'StudyInstanceUID', generate_uid())]:
            original = self.stack.slices[1].path.read_bytes()
            self.edit(1, mutate)
            with self.subTest(change=mutate), self.assertRaises(GeometryError):
                load_hu_volume(self.stack)
            self.stack.slices[1].path.write_bytes(original)

    def test_catalog_references_must_match_every_source(self):
        ds = dcmread(self.stack.slices[0].path, stop_before_pixels=True)
        expected = {'expected_study_uid': str(ds.StudyInstanceUID),
                    'expected_series_uid': str(ds.SeriesInstanceUID)}
        hu = load_hu_volume(self.stack, **expected)
        self.assertEqual(hu.shape, (3, 2, 3))
        # Aunque todos los planos concuerden entre sí, no pertenecen a otro UID.
        for key in expected:
            with self.subTest(reference=key), self.assertRaisesRegex(GeometryError, 'catalog'):
                load_hu_volume(self.stack, **dict(expected, **{key: generate_uid()}))

    def test_limits_and_wrong_stack_order_are_rejected(self):
        with patch('hcclab.volume.MAX_VOLUME_VOXELS', 17), self.assertRaises(GeometryError):
            load_hu_volume(self.stack)
        with patch('hcclab.volume.MAX_FILE_BYTES', 1), self.assertRaises(GeometryError):
            load_hu_volume(self.stack)
        with self.assertRaisesRegex(GeometryError, 'ordered'):
            load_hu_volume(Stack(tuple(reversed(self.stack.slices)), 2.5))


if __name__ == '__main__':
    unittest.main()
