"""Synthetic provenance/export checks; no neural inference or clinical reference."""
from contextlib import ExitStack
import hashlib
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from hcclab.candidates import file_sha256
from experiments import plot_neural_refinement as plot


class DisplayTests(unittest.TestCase):
    def test_every_plane_retained_with_new_lost_and_empty_statistics(self):
        for count in (103, 97):
            before = np.zeros((count, 9, 10), bool); before[54, 2:5, 3:7] = True
            after = np.zeros_like(before); after[55, 3:6, 4:8] = True
            inputs = {'before': before, 'after': after, 'hu': np.zeros(before.shape),
                      'valid': np.ones(before.shape, bool), 'spacing': (2.5, 2., .5)}
            rows = plot.descriptive_rows(inputs)
            self.assertEqual(len(rows), count)
            self.assertTrue(rows[54]['lost_occupied_slice'])
            self.assertTrue(rows[55]['newly_occupied_slice'])
            self.assertEqual(rows[55]['after_area_mm2'], 12.)
            self.assertEqual(rows[54]['removed_voxels'], 12)
            self.assertIsNone(rows[54]['after_hu_median'])
            self.assertIsNone(rows[55]['after_centroid_jump_from_previous_slice_mm'])
            self.assertFalse(any('raw' in key or 'refinement_' in key for key in rows[0]))
            selected = plot.shared.display_selection(before, after, inputs['spacing'])
            self.assertEqual([p['index'] for p in selected['focus']], [53, 54, 55, 56])
            self.assertEqual(selected['atlas_indices'], list(range(51, 59)))
            self.assertEqual(len(selected['orthogonal']), 3)


class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name); self.data = self.folder/'data'; self.data.mkdir()
        self.run = self.folder/'run'; self.run.mkdir()
        self.shape = (60, 9, 10)
        self.before = np.zeros(self.shape, bool); self.before[53:56, 3:5, 4:6] = True
        self.after = self.before.copy(); self.after[56, 3:5, 4:6] = True
        self.valid = np.ones(self.shape, bool)
        slices = []
        for z in range(self.shape[0]):
            path = self.data/f'{z}.dcm'; path.write_bytes(b'native-grid-fixture-'+bytes([z]))
            slices.append(SimpleNamespace(path=path, acquisition='1', sop=f'SOP-{z}', shape=self.shape[1:],
                          spacing=(1., .5), position=(0., 0., z*2.5), orientation=(1., 0., 0., 0., 1., 0.)))
        self.stack = SimpleNamespace(slices=slices, interval_mm=2.5, issue=None)
        selection = {'pre_series_uid': 'PRE', 'post_series_uid': 'POST', 'pre_acquisition': '3',
                     'post_acquisition': '1', 'mass_segment': 2, 'liver_segment': 1}
        self.comparison = {'baseline_saved_exact': True, **{name: {
            'method': method, 'status': 'experimental_unreviewed', 'metrics': {'voxels': int(getattr(self, name).sum())}}
            for name, method in plot.METHODS.items()}}
        self.comparison['after']['inference'] = {'status': 'completed_unreviewed',
            'voxels': int(self.after.sum()), 'shape_zyx': list(self.shape), 'spacing_zyx_mm': [2.5, 1., .5],
            'initial_array_sha256': hashlib.sha256(self.before.tobytes(order='C')).hexdigest(), 'coverage': {
            'reported_write_envelope_zyx': [[0, 0, 0], [60, 9, 10]],
            'interpretation': 'envelope_of_writes_not_exact_inference_coverage',
            'outside_envelope': 'inherited_baseline_not_reevaluated', 'outside_envelope_unchanged': True,
            'all_native_slices_retained': True, 'baseline_voxels_outside_envelope': 0}}
        artifacts = {}
        for name, filename in plot.MASK_FILES.items():
            np.savez_compressed(self.run/filename, mask=getattr(self, name))
            artifacts[filename] = file_sha256(self.run/filename)
        self.manifest = {'schema_version': 1, 'kind': 'post_neural_refinement', 'status': 'completed',
            'case_id': 'SYNTHETIC', 'selection': selection, 'source_integrity_verified': True,
            'baseline_saved_exact': True, 'baseline_manifest_sha256': 'a'*64,
            'baseline_regenerated': False, 'postprocessing_after_nninteractive': 'none',
            'baseline_mask_sha256': artifacts['before.npz'], 'primary_change': plot.METHODS['after'],
            'sources': {'case_id': 'SYNTHETIC', 'selection': selection, 'target_label': 'Mass', 'post': {
                'shape_zyx': list(self.shape), 'spacing_zyx_mm': [2.5, 1., .5], 'stack_identity': 'test-grid',
                'origin_lps_mm': list(slices[0].position), 'orientation_patient': list(slices[0].orientation),
                'ordered_sop_uids': [s.sop for s in slices], 'ordered_ct_sha256': [file_sha256(s.path) for s in slices]}},
            'artifacts_sha256': artifacts}
        self.write_records()
        row = {'PatientID': 'SYNTHETIC', 'StudyInstanceUID': 'POST-study'}
        catalog = SimpleNamespace(by_uid={'POST': row}, local=lambda row: True, folder=lambda row: self.data,
            case=lambda case: {'studies': [{'stage': 'POST', 'series': [{'uid': 'POST'}]}]})
        self.mocks = ExitStack(); self.addCleanup(self.mocks.close)
        for name, value in [('Catalog', catalog), ('stacks', [self.stack]), ('stack_identity', 'test-grid')]:
            self.mocks.enter_context(patch.object(plot.shared, name, return_value=value))
        self.mocks.enter_context(patch.object(plot.shared, 'load_hu_volume',
            side_effect=lambda *a, **k: (np.zeros(self.shape), self.valid)))

    def write_records(self):
        (self.run/'comparison.json').write_text(json.dumps(self.comparison))
        self.manifest['artifacts_sha256']['comparison.json'] = file_sha256(self.run/'comparison.json')
        (self.run/'manifest.json').write_text(json.dumps(self.manifest))

    def test_saved_baseline_and_neural_output_need_no_replay_or_raw_artifacts(self):
        inputs = plot.read_inputs(self.data, self.run)
        np.testing.assert_array_equal(inputs['before'], self.before)
        np.testing.assert_array_equal(inputs['after'], self.after)
        self.assertEqual(len(inputs['artifacts']), 3)
        self.assertNotIn('after_raw', inputs)
        self.assertNotIn('replay_exact', inputs['manifest'])

    def test_declared_method_and_baseline_integrity_are_mandatory(self):
        for key, wrong in [('baseline_saved_exact', False), ('baseline_manifest_sha256', 'bad'),
                           ('baseline_mask_sha256', 'b'*64), ('primary_change', 'binary_reinjection_to_box')]:
            original = self.manifest[key]; self.manifest[key] = wrong; self.write_records()
            with self.assertRaisesRegex(ValueError, 'saved baseline'):
                plot.read_inputs(self.data, self.run)
            self.manifest[key] = original
        self.comparison['after']['method'] = 'medsam2_boundary_2mm'; self.write_records()
        with self.assertRaisesRegex(ValueError, 'method'):
            plot.read_inputs(self.data, self.run)
        self.comparison['after']['method'] = plot.METHODS['after']
        self.comparison['after']['metrics']['voxels'] += 1; self.write_records()
        with self.assertRaisesRegex(ValueError, 'counts'):
            plot.read_inputs(self.data, self.run)

    def test_empty_neural_result_is_reported_as_lost_mask_without_hiding_baseline(self):
        np.savez_compressed(self.run/'after.npz', mask=np.zeros(self.shape, bool))
        self.manifest['artifacts_sha256']['after.npz'] = file_sha256(self.run/'after.npz')
        self.comparison['after']['metrics']['voxels'] = 0
        self.comparison['after']['inference']['voxels'] = 0; self.write_records()
        inputs = plot.read_inputs(self.data, self.run)
        rows = plot.descriptive_rows(inputs)
        self.assertEqual(sum(r['lost_occupied_slice'] for r in rows), 3)
        self.assertEqual(sum(r['removed_voxels'] for r in rows), 12)
        self.assertTrue(all(r['after_hu_median'] is None for r in rows))

    def test_inference_cannot_claim_a_different_initial_mask_or_native_grid(self):
        report = self.comparison['after']['inference']
        for key, wrong in [('initial_array_sha256', 'b'*64), ('shape_zyx', [60, 10, 9]),
                           ('spacing_zyx_mm', [1., 1., .5]), ('voxels', 0)]:
            original = report[key]; report[key] = wrong; self.write_records()
            with self.assertRaisesRegex(ValueError, 'initial mask'):
                plot.read_inputs(self.data, self.run)
            report[key] = original

    def test_native_write_envelope_checks_actual_inherited_baseline(self):
        coverage = self.comparison['after']['inference']['coverage']
        coverage['reported_write_envelope_zyx'] = [[55, 3, 4], [57, 5, 6]]
        coverage['baseline_voxels_outside_envelope'] = 8; self.write_records()
        # A crop may leave a nonempty part of the original untouched; it is not a
        # claim that the model reevaluated those inherited foreground voxels.
        plot.read_inputs(self.data, self.run)
        coverage['reported_write_envelope_zyx'][1][0] = 56; self.write_records()
        with self.assertRaisesRegex(ValueError, 'changed outside'):
            plot.read_inputs(self.data, self.run)
        coverage['reported_write_envelope_zyx'][1][0] = 57
        coverage['baseline_voxels_outside_envelope'] = 0; self.write_records()
        with self.assertRaisesRegex(ValueError, 'Inherited baseline counts'):
            plot.read_inputs(self.data, self.run)

    def test_write_scope_and_integer_native_bounds_cannot_be_implicit(self):
        coverage = self.comparison['after']['inference']['coverage']
        for key, wrong in [
                ('interpretation', 'complete_inference_coverage'),
                ('outside_envelope', 'evaluated_background'),
                ('outside_envelope_unchanged', False), ('all_native_slices_retained', False),
                ('reported_write_envelope_zyx', [[0., 0, 0], [60, 9, 10]]),
                ('reported_write_envelope_zyx', [[False, 0, 0], [60, 9, 10]]),
                ('reported_write_envelope_zyx', [[0, 0, 0], [61, 9, 10]])]:
            original = coverage[key]; coverage[key] = wrong; self.write_records()
            with self.assertRaisesRegex(ValueError, 'write envelope'):
                plot.read_inputs(self.data, self.run)
            coverage[key] = original

    def test_changed_artifact_ct_geometry_and_padding_are_rejected(self):
        with (self.run/'after.npz').open('ab') as stream:
            stream.write(b'changed')
        with self.assertRaisesRegex(ValueError, 'Artifact integrity'):
            plot.read_inputs(self.data, self.run)
        self.manifest['artifacts_sha256']['after.npz'] = file_sha256(self.run/'after.npz'); self.write_records()
        self.manifest['sources']['post']['ordered_sop_uids'].reverse(); self.write_records()
        with self.assertRaisesRegex(ValueError, 'ordered instance'):
            plot.read_inputs(self.data, self.run)
        self.manifest['sources']['post']['ordered_sop_uids'].reverse(); self.write_records()
        self.valid[53, 3, 4] = False
        with self.assertRaisesRegex(ValueError, 'invalid CT support'):
            plot.read_inputs(self.data, self.run)
        self.valid[:] = True; self.stack.slices[0].path.write_bytes(b'changed CT')
        with self.assertRaisesRegex(ValueError, 'POST bytes'):
            plot.read_inputs(self.data, self.run)

    @unittest.skipUnless(importlib.util.find_spec('matplotlib'), 'Optional figure dependencies are not installed.')
    def test_export_captions_fonts_hashes_and_write_envelope_scope(self):
        import matplotlib.pyplot as plt
        inputs = plot.read_inputs(self.data, self.run)
        selection = plot.shared.display_selection(inputs['before'], inputs['after'], inputs['spacing'])
        figure = plot.shared.make_figure(inputs, selection['focus'], selection['bounds'],
            plot.shared.WINDOWS[0], 'Synthetic export', **plot.FIGURE_OPTIONS)
        captions = ' '.join(t.get_text() for t in figure.texts)
        self.assertIn('After: no postprocessing.', captions)
        self.assertIn('not rerun', captions)
        self.assertNotIn('Both masks: 2 mm', captions)
        self.assertNotIn('replay exact', captions.lower())
        plt.close(figure)
        output = self.folder/'figures'
        with patch.object(plot, 'read_inputs', return_value=inputs), patch('sys.argv', [
                'plot_neural_refinement', '--data', str(self.data), '--run', str(self.run), '--output', str(output)]):
            plot.main()
        for name in ('focus-W400.pdf', 'focus-W800.pdf', 'orthogonal.pdf', 'atlas.pdf'):
            content = (output/name).read_bytes()
            self.assertTrue(b'/FontFile2' in content, name)
            self.assertFalse(b'/Subtype /Type3' in content, name)
        record = json.loads((output/'manifest.json').read_text())
        self.assertEqual(record['inference']['after'], self.comparison['after']['inference'])
        self.assertIn('not proof', record['inference_scope_note'])
        self.assertEqual(record['total_acquired_slices_in_csv'], self.shape[0])
        for name, expected in record['outputs_sha256'].items():
            self.assertEqual(file_sha256(output/name), expected, name)


if __name__ == '__main__':
    unittest.main()
