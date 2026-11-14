"""Salida MedSAM vinculada a probabilidades reales, sin pesos ni datos médicos."""
from contextlib import ExitStack
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np

from experiments import post_medsam_slice as experiment
from hcclab.candidates import file_sha256
import test_neural_refinement_experiment as neural_fixture


class ProbabilityExportTests(unittest.TestCase):
    def setUp(self):
        self.before = np.zeros((5, 7, 9), bool)
        self.before[1, 3, 4] = self.before[3, 3, 4] = True
        self.indices = np.array([1, 3], np.int64)
        self.probabilities = np.zeros((2, 7, 9), np.float32)
        self.probabilities[:, 3, 4] = np.nextafter(np.float32(.5), np.float32(1))
        self.probabilities[:, 2, 4] = .5
        self.after = self.before.copy()
        self.after[self.indices] = self.probabilities > .5

    def save(self, folder, **overrides):
        intermediates = {'slice_indices': self.indices, 'probabilities': self.probabilities}
        intermediates.update(overrides)
        experiment.save_probabilities(folder, self.before, self.after, intermediates)

    def test_saves_only_inferred_planes_and_the_strict_threshold(self):
        with TemporaryDirectory() as temp:
            self.save(Path(temp))
            with np.load(Path(temp)/'probabilities.npz', allow_pickle=False) as artifact:
                self.assertEqual(set(artifact.files), {'slice_indices', 'probabilities'})
                np.testing.assert_array_equal(artifact['slice_indices'], [1, 3])
                np.testing.assert_array_equal(artifact['probabilities'], self.probabilities)
                self.assertEqual(artifact['probabilities'].shape[0], 2)
                self.assertEqual(artifact['slice_indices'].dtype, np.int32)
        self.assertFalse(self.after[:, 2, 4].any())  # p=.5 permanece fondo.

    def test_missing_duplicate_reordered_or_phantom_indices_are_rejected(self):
        for indices in ([1], [1, 1], [3, 1], [1, 2, 3], [True, True], [1., 3.]):
            with self.subTest(indices=indices), TemporaryDirectory() as temp:
                with self.assertRaisesRegex(ValueError, 'exactly the occupied'):
                    self.save(Path(temp), slice_indices=np.asarray(indices))
                self.assertFalse((Path(temp)/'probabilities.npz').exists())

    def test_invalid_values_dtype_shape_or_threshold_disagreement_never_publish(self):
        invalid = [self.probabilities.astype(np.float64), self.probabilities[:, :-1],
                   np.full_like(self.probabilities, np.nan), np.full_like(self.probabilities, np.inf),
                   np.full_like(self.probabilities, -.01), np.full_like(self.probabilities, 1.01),
                   np.zeros_like(self.probabilities)]
        for probabilities in invalid:
            with self.subTest(shape=probabilities.shape, dtype=probabilities.dtype), TemporaryDirectory() as temp:
                with self.assertRaises(ValueError):
                    self.save(Path(temp), probabilities=probabilities)
                self.assertFalse((Path(temp)/'probabilities.npz').exists())

    def test_unprocessed_plane_cannot_gain_fabricated_foreground(self):
        self.after[2, 2, 2] = True
        with TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, 'Unprocessed planes must inherit'):
                self.save(Path(temp))
            self.assertFalse((Path(temp)/'probabilities.npz').exists())


class RunnerTests(unittest.TestCase):
    def fixture(self, root, contexts):
        # Reutiliza la fuente CT/NPZ congelada del comparador anterior. Solo
        # reemplaza el adaptador y la lectura del runner nuevo, sin inferencia.
        args, inputs, _ = neural_fixture.RunnerTests().fixture(root, contexts)
        contexts.enter_context(patch.object(experiment, 'read_inputs', return_value=inputs))
        def predict(volume, before, spacing, *, checkpoint_path, valid_mask, intermediates):
            self.assertIs(volume, inputs['hu']); self.assertIs(before, inputs['refined'])
            self.assertEqual(checkpoint_path, args.model_dir/'medsam_vit_b.pth')
            self.assertIs(valid_mask, inputs['valid'])
            indices = np.flatnonzero(before.any(axis=(1, 2)))
            probabilities = before[indices].astype(np.float32)
            probabilities[0, 2, 6] = .75
            after = before.copy(); after[indices] = probabilities > .5
            intermediates.update(slice_indices=indices, probabilities=probabilities)
            return after, {'method': 'synthetic', 'model': {'checkpoint': 'synthetic'}}
        predictor = contexts.enter_context(patch.object(experiment, 'refine_with_medsam', side_effect=predict))
        return args, inputs, predictor

    def test_frozen_baseline_probabilities_and_no_app_writes(self):
        with TemporaryDirectory() as temp, ExitStack() as contexts:
            args, inputs, predict = self.fixture(Path(temp), contexts)
            original = inputs['artifacts']['refined-2mm.npz'].read_bytes()
            save = contexts.enter_context(patch('hcclab.candidates.CandidateStore.save',
                side_effect=AssertionError('Research must not save app masks.')))
            self.assertEqual(experiment.execute(args), 0)
            folder = next(args.output.glob('run-*'))
            report = json.loads((folder/'manifest.json').read_text())
            self.assertEqual(report['status'], 'completed')
            self.assertFalse(report['app_candidate_written'])
            self.assertFalse(report['baseline_regenerated'])
            self.assertFalse(report['registration_recomputed'])
            self.assertEqual((folder/'before.npz').read_bytes(), original)
            self.assertEqual(inputs['artifacts']['refined-2mm.npz'].read_bytes(), original)
            for name, digest in report['artifacts_sha256'].items():
                self.assertEqual(file_sha256(folder/name), digest)
            with np.load(folder/'probabilities.npz', allow_pickle=False) as probabilities, \
                 np.load(folder/'after.npz', allow_pickle=False) as after:
                indices = probabilities['slice_indices']
                np.testing.assert_array_equal(after['mask'][indices], probabilities['probabilities'] > .5)
            predict.assert_called_once(); save.assert_not_called()

    def test_changed_ct_or_mutated_baseline_cannot_publish_completed_result(self):
        for fault in ('ct', 'baseline'):
            with self.subTest(fault=fault), TemporaryDirectory() as temp, ExitStack() as contexts:
                args, inputs, predict = self.fixture(Path(temp), contexts)
                original = predict.side_effect
                def invalid(*a, **kw):
                    result = original(*a, **kw)
                    if fault == 'ct': inputs['ct_paths'][0].write_bytes(b'changed source')
                    else: inputs['refined'][0, 3, 4] = True
                    return result
                predict.side_effect = invalid
                with self.assertRaises(ValueError): experiment.execute(args)
                folder = next(args.output.glob('run-*'))
                report = json.loads((folder/'manifest.json').read_text())
                self.assertEqual(report['status'], 'failed')
                self.assertFalse((folder/'after.npz').exists())

    def test_output_cannot_enter_frozen_baseline_or_model_directories(self):
        with TemporaryDirectory() as temp, ExitStack() as contexts:
            args, _, predict = self.fixture(Path(temp), contexts)
            for destination in (args.baseline/'nested', args.model_dir/'nested'):
                with self.subTest(destination=destination):
                    args.output = destination
                    with self.assertRaisesRegex(ValueError, 'Output must be outside'):
                        experiment.execute(args)
                    predict.assert_not_called()


if __name__ == '__main__':
    unittest.main()
