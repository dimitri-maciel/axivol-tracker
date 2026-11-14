"""Controles causales y de integridad del experimento de condicionamiento.

Usa retículas pequeñas y un predictor falso: no descarga pesos, no infiere
pacientes y nunca toma una candidata de la aplicación como referencia.
"""
from argparse import Namespace
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from experiments import post_prompt_conditioning as experiment
from hcclab.candidates import file_sha256, stack_identity
from hcclab.geometry import Slice, build_stack


def inference_setup():
    """Configuración sintética completa para comparar decisiones independientes."""
    return {
        'prompt_slice_index': 1, 'prompt_box_xyxy': [4, 3, 6, 5],
        'model_prompt_box_xyxy': [2, 2, 4, 4],
        'inference_field_yx': [[1, 2], [7, 9]],
        'inference_field_shape_yx': [6, 7], 'model': {'sha256': 'frozen-weights'},
        'settings': {
            'configuration': 'test-config', 'configuration_sha256': 'frozen-config',
            'window_level_hu': 40, 'window_width_hu': 400,
            'model_input_shape_yx': [512, 512],
            'normalization_mean': [.485, .456, .406],
            'normalization_std': [.229, .224, .225],
            'input_resize': 'bilinear', 'context_minimum_mm': 160,
            'context_rule': 'physical', 'cuda_hole_filling': False,
        },
    }


class FrozenSetupTests(unittest.TestCase):
    def test_geometry_weights_and_preprocessing_changes_cannot_be_called_conditioning_only(self):
        original = inference_setup()
        changes = [
            ('prompt_slice_index', 2), ('prompt_box_xyxy', [5, 3, 7, 5]),
            ('model_prompt_box_xyxy', [3, 2, 5, 4]),
            ('inference_field_yx', [[0, 2], [6, 9]]),
            ('model', {'sha256': 'different-weights'}),
            ('window_width_hu', 800), ('configuration_sha256', 'different-config'),
            ('normalization_mean', [0, 0, 0]),
        ]
        for key, changed in changes:
            current = deepcopy(original)
            (current if key in current else current['settings'])[key] = changed
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, key):
                experiment.require_same_setup(original, current)
        self.assertEqual(original, inference_setup())

    def test_conditioning_and_elapsed_diagnostics_may_differ_with_fixed_physical_setup(self):
        before, after = inference_setup(), inference_setup()
        before.update(prompt_conditioning='binary_reinjection', elapsed_seconds=1)
        after.update(prompt_conditioning='box', elapsed_seconds=2)
        before['settings']['prompt_mode'] = 'binary_reinjection'
        after['settings']['prompt_mode'] = 'box'
        experiment.require_same_setup(before, after)

    def test_upstream_input_policy_cannot_change_window_or_geometry(self):
        before, after = inference_setup(), inference_setup()
        after['settings'].update(input_mode='upstream_uint8', input_resize='pil_rgb_bicubic_if_needed',
            input_quantization='clip_window_times_255_truncate_uint8',
            input_normalization_precision='float64_then_float32')
        experiment.require_same_setup(before, after, upstream_input=True)
        with self.assertRaisesRegex(ValueError, 'input_resize'):
            experiment.require_same_setup(before, after)
        for key, value in [('window_width_hu', 800), ('input_quantization', 'none'),
                           ('input_resize', 'nearest'), ('input_normalization_precision', 'float32')]:
            changed = deepcopy(after)
            changed['settings'][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, key):
                experiment.require_same_setup(before, changed, upstream_input=True)


class FrozenPreTests(unittest.TestCase):
    def fixture(self, root, contexts):
        paths = [root/'ct0.bin', root/'ct1.bin', root/'seg.bin']
        for path in paths:
            path.write_bytes(path.name.encode())
        stack = build_stack([
            Slice(paths[z], f'sop-{z}', '3', 'frame', (8, 10), (.5, 1.),
                  (1., 0., 0., 0., 1., 0.), (0., 0., 2.5*z)) for z in range(2)])
        selection = {'pre_series_uid': 'pre', 'pre_acquisition': '3',
                     'mass_segment': 2, 'liver_segment': 1}
        manifest = {'case_id': 'synthetic', 'selection': selection, 'sources': {
            'pre': {'stack_identity': stack_identity(stack),
                    'ordered_sop_uids': [s.sop for s in stack.slices],
                    'ordered_ct_sha256': [file_sha256(s.path) for s in stack.slices]},
            'seg_sha256': file_sha256(paths[2])}}
        catalog = SimpleNamespace(
            by_uid={'pre': {'StudyInstanceUID': 'pre-study'}},
            local=lambda row: True, folder=lambda row: root,
            case=lambda case: {'studies': [{'stage': 'PRE', 'series': [{'uid': 'pre'}]}]})
        segment = (paths[2], object())
        contexts.enter_context(patch.object(experiment, 'Catalog', return_value=catalog))
        contexts.enter_context(patch.object(experiment, 'stacks', return_value=[stack]))
        contexts.enter_context(patch.object(experiment, 'find_seg', return_value=segment))
        verify = contexts.enter_context(patch.object(experiment, 'verify_pre_sources', return_value=segment))
        labels = contexts.enter_context(patch.object(experiment, 'segment_options', return_value=[
            {'number': 1, 'label': 'Liver'}, {'number': 2, 'label': 'Mass'}]))
        return manifest, paths, stack, verify, labels

    def test_fresh_published_labels_and_expected_study_are_verified(self):
        with TemporaryDirectory() as temp, ExitStack() as contexts:
            root = Path(temp)
            manifest, _, stack, verify, labels = self.fixture(root, contexts)
            experiment.verify_frozen_pre(root, manifest)
            verify.assert_called_once_with(stack, verify.return_value,
                                           expected_series_uid='pre', expected_study_uid='pre-study')
            labels.assert_called_once_with(verify.return_value[1])

    def test_changed_ct_or_ordered_identity_stops_before_seg_decode(self):
        for change in ('bytes', 'references'):
            with self.subTest(change=change), TemporaryDirectory() as temp, ExitStack() as contexts:
                root = Path(temp)
                manifest, paths, _, verify, _ = self.fixture(root, contexts)
                if change == 'bytes':
                    paths[0].write_bytes(b'replaced CT')
                else:
                    manifest['sources']['pre']['ordered_sop_uids'].reverse()
                with self.assertRaisesRegex(ValueError, 'PRE source geometry or bytes'):
                    experiment.verify_frozen_pre(root, manifest)
                verify.assert_not_called()

    def test_seg_replacement_during_fresh_verification_is_rejected(self):
        with TemporaryDirectory() as temp, ExitStack() as contexts:
            root = Path(temp)
            manifest, paths, _, verify, _ = self.fixture(root, contexts)
            def replaced(*args, **kwargs):
                paths[2].write_bytes(b'new SEG after initial hash')
                return verify.return_value
            verify.side_effect = replaced
            with self.assertRaisesRegex(ValueError, 'SEG changed during verification'):
                experiment.verify_frozen_pre(root, manifest)

    def test_mass_and_liver_are_not_interchangeable_sources(self):
        with TemporaryDirectory() as temp, ExitStack() as contexts:
            root = Path(temp)
            manifest, _, _, _, labels = self.fixture(root, contexts)
            labels.return_value = [{'number': 1, 'label': 'Mass'}, {'number': 2, 'label': 'Liver'}]
            with self.assertRaisesRegex(ValueError, 'Mass/Liver labels'):
                experiment.verify_frozen_pre(root, manifest)


class PromptRunnerTests(unittest.TestCase):
    def fixture(self, root, contexts):
        baseline = root/'baseline'; baseline.mkdir()
        data = root/'data'; data.mkdir()
        ct = data/'post.bin'; ct.write_bytes(b'synthetic CT')
        hu = np.zeros((3, 8, 10), np.float32)
        valid = np.ones(hu.shape, bool)
        raw = np.zeros(hu.shape, bool); raw[1, 3:5, 5] = True
        info = inference_setup()
        location = {'roi_zyx': [[0, 0, 0], list(hu.shape)]}
        artifacts = {}
        for name in ('raw.npz', 'refined-2mm.npz'):
            path = baseline/name; np.savez_compressed(path, mask=raw)
            artifacts[name] = path
        artifacts['comparison.json'] = baseline/'comparison.json'
        artifacts['comparison.json'].write_text('{}')
        manifest = {'case_id': 'synthetic', 'selection': {'mass_segment': 2},
                    'sources': {'case_id': 'synthetic'}, 'localization': location,
                    'registrations': {'global': {'status': 'estimated_unverified'}},
                    'inference': info,
                    'artifacts_sha256': {name: file_sha256(path) for name, path in artifacts.items()}}
        manifest_path = baseline/'manifest.json'
        experiment.write_json(manifest_path, manifest)
        inputs = {'manifest': manifest, 'manifest_path': manifest_path,
                  'manifest_sha256': file_sha256(manifest_path), 'artifacts': artifacts,
                  'hu': hu, 'valid': valid, 'raw': raw, 'refined': raw.copy(),
                  'spacing': (2.5, .5, 1), 'ct_paths': [ct], 'ct_hashes': [file_sha256(ct)]}
        contexts.enter_context(patch.object(experiment, 'read_inputs', return_value=inputs))
        contexts.enter_context(patch.object(experiment, 'verify_frozen_pre'))
        contexts.enter_context(patch.object(experiment, 'model_path', return_value=root/'weights.bin'))
        contexts.enter_context(patch.object(experiment, 'verify_model'))
        model = SimpleNamespace(multimask_output_for_tracking=True,
                                binarize_mask_from_pts_for_mem_enc=True, num_maskmem=7)
        contexts.enter_context(patch.object(experiment, '_get_predictor', return_value=(model, None, {})))
        contexts.enter_context(patch.object(experiment.importlib.metadata, 'version', return_value='synthetic'))
        contexts.enter_context(redirect_stdout(io.StringIO()))
        contexts.enter_context(redirect_stderr(io.StringIO()))
        calls = []

        def predict(volume, spacing, registered, **kwargs):
            self.assertIs(volume, hu)
            self.assertIs(kwargs['valid_mask'], valid)
            self.assertIs(registered, location)
            self.assertEqual(spacing, inputs['spacing'])
            self.assertIs(kwargs['refine_boundary'], True)
            mode = kwargs['prompt_mode']; calls.append(mode)
            mask = raw.copy()
            if mode == 'box' or not model.multimask_output_for_tracking:
                mask[1, 4, 6] = True
            field_raw = mask[:, 1:7, 2:9].copy()
            kwargs['intermediates'].update(raw_mask=field_raw,
                logits=np.where(field_raw, 2, -2).astype(np.float32))
            diagnostics = {**deepcopy(info), 'prompt_conditioning': mode}
            input_mode = kwargs['input_mode']
            diagnostics['settings']['input_mode'] = input_mode
            if input_mode == 'upstream_uint8':
                diagnostics['settings'].update(input_resize='pil_rgb_bicubic_if_needed',
                    input_quantization='clip_window_times_255_truncate_uint8',
                    input_normalization_precision='float64_then_float32')
            return mask, diagnostics

        predictor = contexts.enter_context(patch.object(experiment, 'segment_post_model', side_effect=predict))
        predictor.model_state = model
        args = Namespace(data=data, baseline=baseline, output=root/'results')
        return args, inputs, calls, predictor

    @staticmethod
    def report(args):
        folders = list(args.output.glob('run-*'))
        if len(folders) != 1:
            raise AssertionError('Each invocation must own exactly one new run directory.')
        folder = folders[0]
        return folder, json.loads((folder/'manifest.json').read_text())

    def test_completed_comparison_reuses_inputs_and_preserves_baseline_without_app_writes(self):
        with TemporaryDirectory() as temp, ExitStack() as contexts:
            root = Path(temp)
            args, inputs, calls, _ = self.fixture(root, contexts)
            originals = {p: p.read_bytes() for p in args.baseline.iterdir()}
            app_save = contexts.enter_context(patch('hcclab.candidates.CandidateStore.save',
                side_effect=AssertionError('Experiment must not persist an app candidate.')))
            app_open = contexts.enter_context(patch('hcclab.lab.Lab.__init__',
                side_effect=AssertionError('Experiment must not open the app store.')))
            self.assertEqual(experiment.execute(args), 0)
            folder, report = self.report(args)
            self.assertEqual(calls, ['binary_reinjection', 'box'])
            self.assertEqual(report['status'], 'completed')
            self.assertIs(report['replay_exact'], True)
            self.assertIs(report['registration_recomputed'], False)
            self.assertIs(report['source_integrity_verified'], True)
            for path, content in originals.items():
                self.assertEqual(path.read_bytes(), content)
            for name, sha256 in report['artifacts_sha256'].items():
                self.assertEqual(file_sha256(folder/name), sha256)
            with np.load(folder/'before.npz', allow_pickle=False) as saved:
                np.testing.assert_array_equal(saved['mask'], inputs['refined'])
            comparison = json.loads((folder/'comparison.json').read_text())
            self.assertEqual(comparison['after']['metrics']['added_voxels'], 1)
            self.assertEqual(comparison['before']['metrics']['changed_voxels'], 0)
            app_save.assert_not_called(); app_open.assert_not_called()

    def test_single_tracking_changes_only_tracking_flag_and_restores_predictor(self):
        with TemporaryDirectory() as temp, ExitStack() as contexts:
            args, _, calls, predictor = self.fixture(Path(temp), contexts)
            args.after = 'single_tracking'
            self.assertEqual(experiment.execute(args), 0)
            folder, report = self.report(args)
            comparison = json.loads((folder/'comparison.json').read_text())
            self.assertEqual(calls, ['binary_reinjection', 'binary_reinjection'])
            self.assertEqual(report['primary_change'], 'tracking_multimask_to_single')
            self.assertTrue(comparison['before']['effective_model_settings']['multimask_output_for_tracking'])
            self.assertFalse(comparison['after']['effective_model_settings']['multimask_output_for_tracking'])
            for phase in ('before', 'after'):
                self.assertTrue(comparison[phase]['effective_model_settings']['binarize_mask_from_pts_for_mem_enc'])
                self.assertEqual(comparison[phase]['effective_model_settings']['num_maskmem'], 7)
            self.assertTrue(predictor.model_state.multimask_output_for_tracking)

    def test_upstream_input_keeps_tracking_prompt_window_and_frozen_localization(self):
        with TemporaryDirectory() as temp, ExitStack() as contexts:
            args, _, calls, predictor = self.fixture(Path(temp), contexts)
            args.after = 'upstream_input'
            self.assertEqual(experiment.execute(args), 0)
            folder, report = self.report(args)
            comparison = json.loads((folder/'comparison.json').read_text())
            self.assertEqual(report['primary_change'], 'float_bilinear_to_upstream_uint8')
            self.assertEqual(calls, ['binary_reinjection', 'binary_reinjection'])
            self.assertEqual([c.kwargs['input_mode'] for c in predictor.call_args_list],
                             ['float_bilinear', 'upstream_uint8'])
            for phase in ('before', 'after'):
                self.assertTrue(comparison[phase]['tracking_multimask'])
                self.assertEqual(comparison[phase]['inference']['settings']['window_width_hu'], 400)
            self.assertTrue(predictor.model_state.multimask_output_for_tracking)

    def test_override_is_restored_when_second_inference_raises(self):
        with TemporaryDirectory() as temp, ExitStack() as contexts:
            args, _, calls, predictor = self.fixture(Path(temp), contexts)
            args.after = 'single_tracking'
            original_predict = predictor.side_effect
            def fail_on_override(*a, **kw):
                if not predictor.model_state.multimask_output_for_tracking:
                    raise RuntimeError('Synthetic predictor failure')
                return original_predict(*a, **kw)
            predictor.side_effect = fail_on_override
            with self.assertRaisesRegex(RuntimeError, 'Synthetic predictor failure'):
                experiment.execute(args)
            folder, report = self.report(args)
            self.assertEqual(calls, ['binary_reinjection'])
            self.assertEqual(report['status'], 'failed')
            self.assertTrue(predictor.model_state.multimask_output_for_tracking)
            self.assertTrue(predictor.model_state.binarize_mask_from_pts_for_mem_enc)
            self.assertEqual(predictor.model_state.num_maskmem, 7)
            self.assertTrue((folder/'before.npz').is_file())
            self.assertFalse((folder/'after.npz').exists())
            self.assertFalse((folder/'comparison.json').exists())

    def test_binary_replay_mismatch_stops_before_comparison_or_prediction_artifacts(self):
        with TemporaryDirectory() as temp, ExitStack() as contexts:
            args, inputs, calls, _ = self.fixture(Path(temp), contexts)
            inputs['refined'][1, 4, 6] = True  # Historical refined output differs from replay.
            with self.assertRaisesRegex(ValueError, 'Binary replay differs'):
                experiment.execute(args)
            folder, report = self.report(args)
            self.assertEqual(calls, ['binary_reinjection'])
            self.assertEqual(report['status'], 'failed')
            self.assertFalse(list(folder.glob('*.npz')))
            self.assertFalse((folder/'comparison.json').exists())

    def test_mid_run_source_change_prevents_completed_result(self):
        with TemporaryDirectory() as temp, ExitStack() as contexts:
            args, inputs, calls, predictor = self.fixture(Path(temp), contexts)
            original_predict = predictor.side_effect
            def altered(*a, **kw):
                result = original_predict(*a, **kw)
                inputs['ct_paths'][0].write_bytes(b'CT changed during inference')
                return result
            predictor.side_effect = altered
            with self.assertRaisesRegex(ValueError, 'Source CT or manifest changed'):
                experiment.execute(args)
            folder, report = self.report(args)
            self.assertEqual(calls, ['binary_reinjection'])
            self.assertEqual(report['status'], 'failed')
            self.assertIs(report['source_integrity_verified'], False)
            self.assertFalse(list(folder.glob('*.npz')))

    def test_output_cannot_be_baseline_child_or_symlink_to_it(self):
        with TemporaryDirectory() as temp:
            root = Path(temp); baseline = root/'baseline'; baseline.mkdir()
            alias = root/'alias'; alias.symlink_to(baseline, target_is_directory=True)
            for output in (baseline, baseline/'new-run', alias/'new-run'):
                with self.subTest(output=output), patch.object(experiment, 'read_inputs') as read:
                    with self.assertRaisesRegex(ValueError, 'inside the frozen baseline'):
                        experiment.execute(Namespace(data=root/'data', baseline=baseline, output=output))
                    read.assert_not_called()
            self.assertEqual(list(baseline.iterdir()), [])

    def test_data_and_app_annotation_destinations_are_rejected_without_overwrite(self):
        with TemporaryDirectory() as temp:
            root = Path(temp); data = root/'data'; data.mkdir()
            marker = data/'original.bin'; marker.write_bytes(b'original')
            for output in (data, data/'new', Path('/annotations/new')):
                with self.subTest(output=output), self.assertRaisesRegex(ValueError, 'outside source data'):
                    experiment.safe_output_directory(output, data)
            self.assertEqual(marker.read_bytes(), b'original')


if __name__ == '__main__':
    unittest.main()
