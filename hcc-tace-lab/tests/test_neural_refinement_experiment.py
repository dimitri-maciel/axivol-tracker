"""Integridad del comparador 3D; retículas sintéticas, sin pesos ni pacientes."""
from argparse import Namespace
from contextlib import ExitStack, redirect_stdout
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np

from experiments import post_neural_refinement as experiment
from hcclab.candidates import file_sha256


class DescriptorTests(unittest.TestCase):
    def test_new_planes_and_component_loss_are_reported_without_editing_output(self):
        before = np.zeros((8, 11, 14), bool)
        before[2:5, 3:7, 4:9] = True
        before[6, 8, 11] = True
        after = before.copy(); after[6, 8, 11] = False; after[1, 3, 4] = True
        original = after.copy()
        info = experiment.mask_descriptors(after, before, np.ones_like(before), (2.5, 1, .5),
            {'roi_zyx': [[0, 0, 0], list(before.shape)]})
        self.assertEqual(info['new_slice_indices'], [1])
        self.assertEqual(info['lost_slice_indices'], [6])
        self.assertEqual((info['added_voxels'], info['removed_voxels']), (1, 1))
        self.assertEqual(info['volume_cm3'], int(after.sum())*1.25/1000)
        retention = experiment.initial_mask_retention(before, after, (2.5, 1, .5))
        self.assertEqual(retention['extinguished_components'], 1)
        self.assertEqual(sorted(x['initial_voxels'] for x in retention['components']), [1, 60])
        np.testing.assert_array_equal(after, original)

    def test_core_uses_millimeters_and_does_not_claim_anatomical_preservation(self):
        before = np.zeros((7, 9, 13), bool); before[1:6, 1:8, 1:12] = True
        after = before.copy(); after[3, 4, 6] = False
        isotropic = experiment.initial_mask_retention(before, after, (1, 1, 1))
        anisotropic = experiment.initial_mask_retention(before, after, (.1, 1, 1))
        self.assertGreater(isotropic['core_voxels'], 0)
        self.assertEqual(isotropic['removed_core_voxels'], 1)
        self.assertEqual(anisotropic['core_voxels'], 0)
        self.assertIsNone(anisotropic['retained_core_fraction'])
        self.assertIn('not lesion preservation', isotropic['scope'])
        before[0, 4, 6] = True
        self.assertEqual(experiment.initial_mask_retention(before, after, (1, 1, 1))['core_status'],
                         'not_computed_initial_mask_touches_native_fov')


class RunnerTests(unittest.TestCase):
    def fixture(self, root, contexts):
        baseline = root/'baseline'; baseline.mkdir()
        data = root/'data'; data.mkdir()
        model = root/'model'; model.mkdir()
        ct = data/'post.bin'; ct.write_bytes(b'fixed CT source')
        hu = np.arange(8*11*14, dtype=np.float32).reshape(8, 11, 14)
        mask = np.zeros(hu.shape, bool); mask[2:5, 3:7, 4:9] = True
        valid = np.ones(hu.shape, bool); valid[:, 0, 0] = False
        artifacts = {}
        for name in ('raw.npz', 'refined-2mm.npz'):
            p = baseline/name; np.savez_compressed(p, mask=mask); artifacts[name] = p
        p = baseline/'comparison.json'; p.write_text('{}'); artifacts[p.name] = p
        manifest = {'case_id': 'synthetic', 'selection': {'mass_segment': 2},
            'sources': {'case_id': 'synthetic'}, 'model': {'sha256': 'baseline-checkpoint'},
            'inference': {'inference_field_yx': [[0, 0], [11, 14]]},
            'localization': {'roi_zyx': [[0, 0, 0], list(mask.shape)]},
            'artifacts_sha256': {name: file_sha256(path) for name, path in artifacts.items()}}
        manifest_path = baseline/'manifest.json'; experiment.write_json(manifest_path, manifest)
        inputs = {'manifest': manifest, 'manifest_path': manifest_path,
            'manifest_sha256': file_sha256(manifest_path), 'artifacts': artifacts,
            'hu': hu, 'valid': valid, 'refined': mask, 'raw': mask.copy(),
            'spacing': (2.5, 1, .5), 'ct_paths': [ct], 'ct_hashes': [file_sha256(ct)]}
        contexts.enter_context(patch.object(experiment, 'read_inputs', return_value=inputs))
        contexts.enter_context(patch('experiments.post_prompt_conditioning.verify_frozen_pre'))
        contexts.enter_context(patch.object(experiment.importlib.metadata, 'version', return_value='synthetic'))
        contexts.enter_context(redirect_stdout(io.StringIO()))
        def predict(volume, initial, spacing, **kwargs):
            self.assertIs(volume, hu); self.assertIs(initial, mask)
            self.assertEqual(tuple(spacing), (2.5, 1, .5))
            self.assertIs(kwargs['valid_mask'], valid); self.assertEqual(kwargs['model_dir'], model)
            result = initial.copy(); result[1, 4, 6] = True
            return result, {'method': 'synthetic', 'model': {'sha256': 'refiner-checkpoint'}}
        predictor = contexts.enter_context(patch.object(experiment, 'refine_with_nninteractive', side_effect=predict))
        return Namespace(data=data, baseline=baseline, model_dir=model, output=root/'results'), inputs, predictor

    def report(self, args):
        folders = list(args.output.glob('run-*')); self.assertEqual(len(folders), 1)
        return folders[0], json.loads((folders[0]/'manifest.json').read_text())

    def test_exact_baseline_single_refinement_and_no_app_candidate_write(self):
        with TemporaryDirectory() as temp, ExitStack() as contexts:
            args, inputs, predict = self.fixture(Path(temp), contexts)
            originals = {p: p.read_bytes() for p in args.baseline.iterdir()}
            save = contexts.enter_context(patch('hcclab.candidates.CandidateStore.save',
                side_effect=AssertionError('Research runner cannot save an app candidate.')))
            self.assertEqual(experiment.execute(args), 0)
            folder, report = self.report(args)
            self.assertEqual(report['status'], 'completed')
            self.assertTrue(report['source_integrity_verified']); self.assertTrue(report['baseline_saved_exact'])
            self.assertFalse(report['registration_recomputed']); self.assertFalse(report['baseline_regenerated'])
            self.assertEqual(report['baseline_mask_sha256'], file_sha256(folder/'before.npz'))
            for name, digest in report['artifacts_sha256'].items():
                self.assertEqual(file_sha256(folder/name), digest)
            self.assertEqual((folder/'before.npz').read_bytes(), originals[inputs['artifacts']['refined-2mm.npz']])
            for path, content in originals.items(): self.assertEqual(path.read_bytes(), content)
            comparison = json.loads((folder/'comparison.json').read_text())
            self.assertEqual(comparison['after']['metrics']['added_voxels'], 1)
            self.assertEqual(comparison['after']['method'], 'nninteractive_initial_segmentation')
            predict.assert_called_once(); save.assert_not_called()

    def test_mutating_initial_mask_is_not_an_acceptable_refinement(self):
        with TemporaryDirectory() as temp, ExitStack() as contexts:
            args, inputs, predict = self.fixture(Path(temp), contexts)
            def mutate(*a, **kw):
                inputs['refined'][1, 4, 6] = True
                return inputs['refined'].copy(), {}
            predict.side_effect = mutate
            with self.assertRaisesRegex(ValueError, 'mutated the frozen input'):
                experiment.execute(args)
            folder, report = self.report(args)
            self.assertEqual(report['status'], 'failed'); self.assertFalse((folder/'after.npz').exists())

    def test_changed_source_or_invalid_support_does_not_publish_completed_result(self):
        for fault in ('ct', 'padding', 'shape'):
            with self.subTest(fault=fault), TemporaryDirectory() as temp, ExitStack() as contexts:
                args, inputs, predict = self.fixture(Path(temp), contexts)
                original = predict.side_effect
                def invalid(*a, **kw):
                    mask, info = original(*a, **kw)
                    if fault == 'ct': inputs['ct_paths'][0].write_bytes(b'changed')
                    elif fault == 'padding': mask[1, 0, 0] = True
                    else: mask = mask[1:]
                    return mask, info
                predict.side_effect = invalid
                with self.assertRaises(ValueError): experiment.execute(args)
                folder, report = self.report(args)
                self.assertEqual(report['status'], 'failed')
                self.assertFalse(report['source_integrity_verified'])
                self.assertFalse((folder/'comparison.json').exists())

    def test_empty_output_is_recorded_without_falling_back_to_initial_mask(self):
        with TemporaryDirectory() as temp, ExitStack() as contexts:
            args, inputs, predict = self.fixture(Path(temp), contexts)
            predict.side_effect = lambda *a, **kw: (np.zeros_like(inputs['refined']), {'outcome': 'empty_prediction_not_confirmed_absence'})
            self.assertEqual(experiment.execute(args), 0)
            folder, _ = self.report(args)
            comparison = json.loads((folder/'comparison.json').read_text())
            self.assertEqual(comparison['after']['metrics']['voxels'], 0)
            self.assertEqual(comparison['after']['initial_mask_retention']['extinguished_components'], 1)
            self.assertFalse(np.load(folder/'after.npz')['mask'].any())

    def test_output_cannot_overwrite_baseline_or_weights_even_through_symlink(self):
        with TemporaryDirectory() as temp:
            root = Path(temp); baseline = root/'baseline'; baseline.mkdir()
            model = root/'model'; model.mkdir(); alias = root/'alias'; alias.symlink_to(model)
            for destination in (baseline, baseline/'nested', model/'nested', alias/'nested'):
                args = Namespace(data=root/'data', baseline=baseline, model_dir=model, output=destination)
                with self.subTest(destination=destination), patch.object(experiment, 'read_inputs') as read:
                    with self.assertRaisesRegex(ValueError, 'Output must be outside'):
                        experiment.execute(args)
                    read.assert_not_called()


if __name__ == '__main__':
    unittest.main()
