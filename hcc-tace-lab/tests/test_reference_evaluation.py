"""Referencias sintéticas únicamente: geometría, métricas y barreras de entrada."""
from contextlib import redirect_stderr, redirect_stdout
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import SimpleITK as sitk

from hcclab.candidates import file_sha256
from experiments.evaluate_reference import EvaluationError, compare_masks, evaluate_reference, main


class MetricTests(unittest.TestCase):
    def test_anisotropic_distance_and_physical_volume_without_mutating_arrays(self):
        candidate = np.zeros((4, 5, 6), bool); candidate[1, 2, 3] = True
        reference = np.zeros_like(candidate); reference[2, 2, 3] = True
        originals = candidate.copy(), reference.copy()
        result = compare_masks(candidate, reference, (2.5, 1.2, .7))
        self.assertEqual(result['dice'], 0)
        self.assertEqual(result['hd95_mm'], 2.5)
        self.assertAlmostEqual(result['candidate_volume_cm3'], .0021)
        self.assertEqual(result['absolute_volume_error_cm3'], 0)
        for actual, original in zip((candidate, reference), originals):
            np.testing.assert_array_equal(actual, original)

    def test_hd95_is_maximum_of_directed_percentiles_not_pooled(self):
        candidate = np.zeros((1, 1, 4), bool); candidate[0, 0, 0] = True
        reference = np.ones_like(candidate)
        result = compare_masks(candidate, reference, (1, 1, 1))
        self.assertAlmostEqual(result['hd95_mm'], 2.85)
        self.assertAlmostEqual(result['directed_p95_mm']['candidate_to_reference'], 0)
        self.assertNotAlmostEqual(result['hd95_mm'], np.percentile([0, 0, 1, 2, 3], 95))

    def test_identical_masks_and_empty_conventions_are_explicit(self):
        mask = np.ones((3, 4, 5), bool)
        result = compare_masks(mask, mask.copy(), (2, 1, 1))
        self.assertEqual((result['dice'], result['hd95_mm']), (1, 0))
        empty = np.zeros_like(mask)
        both = compare_masks(empty, empty, (2, 1, 1))
        self.assertIsNone(both['dice']); self.assertIsNone(both['hd95_mm'])
        self.assertIsNone(both['absolute_relative_volume_error_percent'])
        self.assertTrue(both['presence_agreement'])
        missing = compare_masks(empty, mask, (2, 1, 1))
        self.assertEqual(missing['dice'], 0); self.assertIsNone(missing['hd95_mm'])
        self.assertAlmostEqual(missing['signed_volume_error_cm3'], -.12)
        self.assertEqual(missing['absolute_relative_volume_error_percent'], 100)
        spurious = compare_masks(mask, empty, (2, 1, 1))
        self.assertEqual(spurious['dice'], 0)
        self.assertIsNone(spurious['absolute_relative_volume_error_percent'])
        json.dumps({'both': both, 'missing': missing, 'spurious': spurious}, allow_nan=False)

    def test_metric_contract_rejects_non_boolean_shape_or_spacing(self):
        mask = np.ones((3, 4, 5), bool)
        for other in (mask.astype(np.uint8), mask[:2], np.empty((0,4,5), bool)):
            with self.subTest(shape=other.shape), self.assertRaises(EvaluationError):
                compare_masks(mask, other, (2, 1, 1))
        for spacing in ((0, 1, 1), (np.nan, 1, 1), (True, 1, 1), (1e308, 1e308, 1e308), (1e-300, 1e-300, 1e-300)):
            with self.subTest(spacing=spacing), self.assertRaises(EvaluationError):
                compare_masks(mask, mask, spacing)


class ReferenceEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manager = self.root/'manager'; self.manager.mkdir()
        self.manifest_path = self.manager/'evaluation_manifest.json'
        self.candidate_path = self.manager/'candidate.nrrd'
        self.validity_path = self.manager/'post_valid.nrrd'
        self.reference_path = self.root/'POST-label.nrrd'
        self.form_path = self.root/'reader-form.json'
        self.case_path = self.manager/'reader_case.json'
        self.geometry = {'size_xyz':[8,7,6], 'spacing_xyz_mm':[.7,1.2,2.5],
                         'origin_lps_mm':[17,-38,90], 'direction_lps':[0,-1,0,1,0,0,0,0,1]}
        self.candidate = np.zeros((6,7,8), np.uint8); self.candidate[1:4,2:5,3:6] = 1
        self.reference = np.zeros_like(self.candidate); self.reference[2:5,2:5,3:6] = 1
        self.write_nrrd(self.candidate_path, self.candidate)
        self.write_nrrd(self.reference_path, self.reference)
        self.write_nrrd(self.validity_path, np.ones_like(self.candidate))
        self.case = {'package_id':'synthetic-package', 'case_id':'SYNTHETIC_1',
                     'target_definition':'whole_treated_lesion',
                     'post':{'series_uid':'1.2.3.4','acquisition':'1','geometry':self.geometry,'ct_sha256':'1'*64}}
        self.save_json(self.case_path, self.case)
        self.manifest = {'schema_version':1, 'kind':'independent_post_reference_evaluation', **copy.deepcopy(self.case),
                         'candidate':{'path':'candidate.nrrd','sha256':file_sha256(self.candidate_path),'id':'synthetic-candidate'},
                         'validity':{'path':'post_valid.nrrd','sha256':file_sha256(self.validity_path)},
                         'reader_case_sha256':file_sha256(self.case_path)}
        self.form = {'reader_id':'synthetic-reader', 'qualifications':'Synthetic expert declaration for unit tests only',
                     'independently_drawn':True,'model_output_seen':False,'case_id':'SYNTHETIC_1',
                     'post_series_uid':'1.2.3.4','post_acquisition':'1','package_id':'synthetic-package',
                     'target_definition':'whole_treated_lesion','target_confirmed':True,
                     'lesion_identity_confirmed':True,'evaluability':'delineable',
                     'annotation_file':'POST-label.nrrd','reviewed_utc':'2026-11-12T12:00:00Z',
                     'hepatic_imaging_expertise_attested':True,'reference_ct_sha256':'1'*64,
                     'annotation_sha256':file_sha256(self.reference_path)}
        self.persist()

    def save_json(self, path, value):
        path.write_text(json.dumps(value))

    def persist(self):
        self.save_json(self.manifest_path, self.manifest)
        self.save_json(self.form_path, self.form)

    def write_nrrd(self, path, array, geometry=None):
        g = geometry or self.geometry
        image = sitk.GetImageFromArray(array)
        image.SetSpacing(g['spacing_xyz_mm']); image.SetOrigin(g['origin_lps_mm'])
        image.SetDirection(g['direction_lps'])
        sitk.WriteImage(image, str(path), True)

    def replace_reference(self, array, geometry=None):
        self.write_nrrd(self.reference_path, array, geometry)
        self.form['annotation_sha256'] = file_sha256(self.reference_path)
        self.persist()

    def evaluate(self):
        return evaluate_reference(self.manifest_path,self.candidate_path,self.reference_path,self.form_path)

    def test_complete_attestation_and_native_rotated_geometry_produce_limited_agreement(self):
        before = [file_sha256(p) for p in (self.candidate_path,self.reference_path,self.validity_path)]
        result = self.evaluate()
        self.assertEqual(result['status'],'agreement_with_supplied_expert_reference')
        self.assertAlmostEqual(result['metrics']['dice'],2/3)
        self.assertAlmostEqual(result['metrics']['hd95_mm'],2.5)
        self.assertEqual(result['reader']['independence_and_expertise'],'self_reported_attestation_not_credential_verification')
        self.assertFalse(result['metric_convention']['resampling_performed'])
        self.assertEqual(result['input_sha256']['reference'],file_sha256(self.reference_path))
        self.assertEqual(before,[file_sha256(p) for p in (self.candidate_path,self.reference_path,self.validity_path)])
        json.dumps(result,allow_nan=False)

    def test_incomplete_nonexpert_unblinded_uncertain_or_wrong_target_is_blocked(self):
        original = copy.deepcopy(self.form)
        cases = [('reader_id',None),('qualifications',''),('independently_drawn',False),
                 ('hepatic_imaging_expertise_attested',None),('hepatic_imaging_expertise_attested',False),
                 ('model_output_seen',True),('model_output_seen',None),('target_confirmed',1),
                 ('lesion_identity_confirmed',False),('target_definition','viable_tumor'),
                 ('evaluability','uncertain'),('evaluability','not_identifiable'),
                 ('reviewed_utc','2026-11-12T12:00:00'),('annotation_sha256',None)]
        for key,value in cases:
            with self.subTest(key=key,value=value):
                self.form=copy.deepcopy(original);self.form[key]=value;self.persist()
                with self.assertRaises(EvaluationError):self.evaluate()

    def test_case_series_package_ct_hash_annotation_and_reader_case_mismatches_are_blocked(self):
        original=copy.deepcopy(self.form)
        for key,value in [('case_id','OTHER'),('package_id','other'),('post_series_uid','1.2.9'),
                          ('post_acquisition','2'),('reference_ct_sha256','f'*64),
                          ('annotation_sha256','e'*64),('annotation_file','../POST-label.nrrd')]:
            with self.subTest(key=key):
                self.form=copy.deepcopy(original);self.form[key]=value;self.persist()
                with self.assertRaises(EvaluationError):self.evaluate()
        self.form=original;self.persist()
        self.case['post']['ct_sha256']='2'*64;self.save_json(self.case_path,self.case)
        self.manifest['reader_case_sha256']=file_sha256(self.case_path);self.persist()
        with self.assertRaisesRegex(EvaluationError,'POST source'):self.evaluate()

    def test_frozen_candidate_validity_and_case_file_tampering_are_rejected(self):
        for path in (self.candidate_path,self.validity_path,self.case_path):
            with self.subTest(path=path.name):
                original=path.read_bytes();path.write_bytes(original+b'changed')
                with self.assertRaisesRegex(EvaluationError,'SHA-256'):self.evaluate()
                path.write_bytes(original)

    def test_spacing_origin_direction_or_size_changes_are_not_resampled(self):
        for key,index in [('spacing_xyz_mm',0),('origin_lps_mm',1),('direction_lps',0)]:
            with self.subTest(key=key):
                geometry=copy.deepcopy(self.geometry);geometry[key][index]+=.01
                self.replace_reference(self.reference,geometry)
                with self.assertRaisesRegex(EvaluationError,'native POST grid'):self.evaluate()
        self.replace_reference(self.reference[:5])
        with self.assertRaisesRegex(EvaluationError,'Image size'):self.evaluate()
        geometry=copy.deepcopy(self.geometry);geometry['origin_lps_mm'][0]+=1e-7
        self.replace_reference(self.reference,geometry)
        self.assertEqual(self.evaluate()['metrics']['reference_voxels'],27)

    def test_nonfinite_multilabel_and_padding_foreground_are_rejected_not_thresholded(self):
        for value in (2,np.nan,np.inf):
            with self.subTest(value=value):
                array=self.reference.astype(np.float32);array[0,0,0]=value
                self.replace_reference(array)
                with self.assertRaisesRegex(EvaluationError,'finite and binary'):self.evaluate()
        self.replace_reference(self.reference)
        valid=np.ones_like(self.reference);valid[2,2,3]=0
        self.write_nrrd(self.validity_path,valid)
        self.manifest['validity']['sha256']=file_sha256(self.validity_path);self.persist()
        with self.assertRaisesRegex(EvaluationError,'padding'):self.evaluate()

    def test_absence_requires_explicit_expert_confirmation_and_empty_reference(self):
        self.form['evaluability']='absence_confirmed';self.persist()
        with self.assertRaisesRegex(EvaluationError,'emptiness'):self.evaluate()
        self.replace_reference(np.zeros_like(self.reference))
        result=self.evaluate()
        self.assertEqual(result['metrics']['dice'],0)
        self.assertIsNone(result['metrics']['hd95_mm'])
        self.form['lesion_identity_confirmed']=False;self.persist()
        with self.assertRaises(EvaluationError):self.evaluate()
        self.form['lesion_identity_confirmed']=True;self.form['evaluability']='delineable';self.persist()
        with self.assertRaisesRegex(EvaluationError,'emptiness'):self.evaluate()

    def test_confirmed_both_empty_is_presence_agreement_but_needs_valid_ct_support(self):
        empty=np.zeros_like(self.candidate)
        self.write_nrrd(self.candidate_path,empty)
        self.manifest['candidate']['sha256']=file_sha256(self.candidate_path)
        self.form['evaluability']='absence_confirmed';self.replace_reference(empty)
        result=self.evaluate()['metrics']
        self.assertTrue(result['presence_agreement'])
        self.assertIsNone(result['dice']);self.assertIsNone(result['hd95_mm'])
        self.write_nrrd(self.validity_path,empty)
        self.manifest['validity']['sha256']=file_sha256(self.validity_path);self.persist()
        with self.assertRaisesRegex(EvaluationError,'no valid CT'):self.evaluate()

    def test_layered_nrrd_and_excessive_native_size_are_rejected_before_decoding(self):
        image=sitk.GetImageFromArray(np.zeros((2,6,7,8),np.uint8),isVector=False)
        sitk.WriteImage(image,str(self.reference_path),True)
        self.form['annotation_sha256']=file_sha256(self.reference_path);self.persist()
        with self.assertRaisesRegex(EvaluationError,'scalar three-dimensional'):self.evaluate()
        self.manifest['post']['geometry']['size_xyz']=[10000,10000,10000];self.persist()
        with patch('experiments.evaluate_reference._binary_nrrd') as decode:
            with self.assertRaisesRegex(EvaluationError,'64 million'):self.evaluate()
            decode.assert_not_called()

    def test_detached_payload_candidate_as_reference_and_path_escape_are_rejected(self):
        with self.assertRaisesRegex(EvaluationError,'separate manual'):
            evaluate_reference(self.manifest_path,self.candidate_path,self.candidate_path,self.form_path)
        self.reference_path.write_bytes(b'NRRD0004\ntype: uchar\ndimension: 3\nsizes: 8 7 6\ndata file: private.raw\n\n')
        self.form['annotation_sha256']=file_sha256(self.reference_path);self.persist()
        with self.assertRaisesRegex(EvaluationError,'Detached'):self.evaluate()
        self.manifest['candidate']['path']='../candidate.nrrd';self.persist()
        with self.assertRaisesRegex(EvaluationError,'confined'):self.evaluate()

    def test_input_change_during_evaluation_is_detected(self):
        from experiments import evaluate_reference as module
        original=module._check_form
        def replace_manifest(*args):
            original(*args)
            self.manifest['changed_after_read']=True;self.persist()
        with patch.object(module,'_check_form',side_effect=replace_manifest):
            with self.assertRaisesRegex(EvaluationError,'changed during'):self.evaluate()

    def test_cli_blocked_form_writes_no_metrics_and_success_never_overwrites(self):
        output=self.root/'report.json'
        args=['--manifest',str(self.manifest_path),'--candidate',str(self.candidate_path),
              '--reference',str(self.reference_path),'--reader-form',str(self.form_path),'--output',str(output)]
        self.form['hepatic_imaging_expertise_attested']=None;self.persist()
        with redirect_stderr(io.StringIO()) as err,redirect_stdout(io.StringIO()):
            self.assertEqual(main(args),2)
        self.assertEqual(json.loads(err.getvalue())['status'],'blocked')
        self.assertFalse(output.exists())
        self.form['hepatic_imaging_expertise_attested']=True;self.persist()
        with redirect_stdout(io.StringIO()):self.assertEqual(main(args),0)
        before=output.read_bytes()
        with redirect_stderr(io.StringIO()),redirect_stdout(io.StringIO()):self.assertEqual(main(args),2)
        self.assertEqual(output.read_bytes(),before)


if __name__ == '__main__':
    unittest.main()
