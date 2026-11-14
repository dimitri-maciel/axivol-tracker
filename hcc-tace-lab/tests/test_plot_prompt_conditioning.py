"""Synthetic geometry/provenance tests; no model or clinical accuracy claims."""
from contextlib import ExitStack
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from hcclab.candidates import file_sha256
from experiments import plot_prompt_conditioning as plot


class DisplayTests(unittest.TestCase):
    def setUp(self):
        self.before = np.zeros((70, 12, 14), bool)
        self.after = self.before.copy()
        self.before[52, 4:6, 5:7] = True
        self.after[57, 6:8, 7:9] = True
        self.inputs = {'before': self.before, 'after': self.after,
                       'before_raw': self.before.copy(), 'after_raw': self.after.copy(),
                       'hu': np.zeros(self.before.shape), 'valid': np.ones(self.before.shape, bool),
                       'spacing': (2.5, 2., .5)}

    def test_fixed_focus_union_orthogonals_and_full_occupied_atlas_are_not_ranked(self):
        selected = plot.display_selection(self.before, self.after, self.inputs['spacing'])
        self.assertEqual([p['index'] for p in selected['focus']], [53, 54, 55, 56])
        self.assertEqual([p['index'] for p in selected['orthogonal']], [54, 6, 6])
        self.assertEqual(selected['atlas_indices'], list(range(49, 61)))
        np.testing.assert_array_equal(selected['bounds'][0], [44, 0, 0])
        np.testing.assert_array_equal(selected['bounds'][1], [66, 12, 14])
        swapped = plot.display_selection(self.after, self.before, self.inputs['spacing'])
        self.assertEqual(selected['focus'], swapped['focus'])
        self.assertEqual(selected['orthogonal'], swapped['orthogonal'])
        self.assertEqual(selected['atlas_indices'], swapped['atlas_indices'])

    def test_short_volume_reports_unavailable_focus_without_inventing_slices(self):
        before = self.before[:10].copy(); before[1, 3, 4] = True
        selected = plot.display_selection(before, before, self.inputs['spacing'])
        self.assertEqual(selected['focus'], [])
        self.assertEqual(selected['focus_unavailable_app_slices'], [54, 55, 56, 57])
        self.assertEqual(selected['atlas_indices'], list(range(5)))
        with self.assertRaisesRegex(ValueError, 'empty'):
            plot.display_selection(np.zeros_like(before), np.zeros_like(before), self.inputs['spacing'])

    def test_rows_keep_every_acquired_plane_and_separate_prompt_from_refinement(self):
        self.inputs['after_raw'][57, 6, 6] = True
        copies = {name: value.copy() for name, value in self.inputs.items() if isinstance(value, np.ndarray)}
        rows = plot.descriptive_rows(self.inputs)
        self.assertEqual(len(rows), 70)
        self.assertEqual(rows[53]['app_slice'], 54)
        self.assertEqual(rows[57]['after_voxels'], 4)
        self.assertEqual(rows[57]['after_raw_voxels'], 5)
        self.assertEqual(rows[57]['after_refinement_removed_voxels'], 1)
        self.assertEqual(rows[57]['added_voxels'], 4)
        self.assertEqual(rows[52]['removed_voxels'], 4)
        self.assertEqual(rows[57]['after_area_mm2'], 4.)
        self.assertIsNone(rows[0]['after_hu_median'])
        self.assertIsNone(rows[57]['after_centroid_jump_from_previous_slice_mm'])
        self.assertNotIn('raw_voxels', rows[57])
        for name, before in copies.items():
            np.testing.assert_array_equal(self.inputs[name], before)

    def test_native_orthogonal_slice_keeps_physical_pixel_aspect(self):
        hu = np.arange(self.before.size).reshape(self.before.shape)
        view, spacing = plot.plane_view(hu, {'axis': 1, 'index': 5}, ([49, 2, 3], [61, 10, 11]), self.inputs['spacing'])
        np.testing.assert_array_equal(view, hu[49:61, 5, 3:11])
        self.assertEqual(spacing, (2.5, .5))


class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name); self.data = self.folder/'data'; self.data.mkdir()
        self.run = self.folder/'run'; self.run.mkdir()
        self.shape = (6, 9, 10)
        self.mask = np.zeros(self.shape, bool); self.mask[2:4, 3:5, 4:6] = True
        self.valid = np.ones(self.shape, bool)
        slices = []
        for z in range(self.shape[0]):
            path = self.data/f'{z}.dcm'; path.write_bytes(b'identity-test-fixture-'+bytes([z]))
            slices.append(SimpleNamespace(path=path, acquisition='1', sop=f'SOP-{z}', shape=self.shape[1:],
                          spacing=(1., .5), position=(0., 0., z*2.5), orientation=(1., 0., 0., 0., 1., 0.)))
        self.stack = SimpleNamespace(slices=slices, interval_mm=2.5, issue=None)
        selection = {'pre_series_uid': 'PRE', 'post_series_uid': 'POST', 'pre_acquisition': '3',
                     'post_acquisition': '1', 'mass_segment': 2, 'liver_segment': 1}
        self.comparison = {'replay_exact': True, **{name: {'prompt_mode': mode,
            'status': 'experimental_unreviewed', 'metrics': {'voxels': int(self.mask.sum())},
            'raw_metrics': {'voxels': int(self.mask.sum())}}
            for name, mode in [('before', 'binary_reinjection'), ('after', 'box')]}}
        artifacts = {}
        for name in plot.MASK_FILES.values():
            np.savez_compressed(self.run/name, mask=self.mask); artifacts[name] = file_sha256(self.run/name)
        self.manifest = {'schema_version': 1, 'kind': 'post_prompt_conditioning', 'status': 'completed',
            'case_id': 'SYNTHETIC', 'selection': selection, 'source_integrity_verified': True, 'replay_exact': True,
            'boundary_band_mm': 2, 'registration_recomputed': False, 'primary_change': 'binary_reinjection_to_box',
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
            self.mocks.enter_context(patch.object(plot, name, return_value=value))
        self.mocks.enter_context(patch.object(plot, 'load_hu_volume', side_effect=lambda *a, **k: (np.zeros(self.shape), self.valid)))

    def write_records(self):
        (self.run/'comparison.json').write_text(json.dumps(self.comparison))
        self.manifest['artifacts_sha256']['comparison.json'] = file_sha256(self.run/'comparison.json')
        (self.run/'manifest.json').write_text(json.dumps(self.manifest))

    def test_verified_four_masks_native_source_and_snapshot_hashes(self):
        inputs = plot.read_inputs(self.data, self.run)
        self.assertEqual(inputs['spacing'], (2.5, 1., .5))
        self.assertEqual(len(inputs['artifacts']), 5)
        for name in plot.MASK_FILES:
            np.testing.assert_array_equal(inputs[name], self.mask)
        self.assertEqual(inputs['manifest_sha256'], file_sha256(self.run/'manifest.json'))

    def test_single_tracking_contract_is_distinct_from_box_conditioning(self):
        self.manifest['primary_change'] = 'tracking_multimask_to_single'
        self.comparison['after']['prompt_mode'] = 'binary_reinjection'
        self.comparison['before']['tracking_multimask'] = True
        self.comparison['after']['tracking_multimask'] = False
        self.write_records()
        inputs = plot.read_inputs(self.data, self.run)
        self.assertEqual(plot.CHANGES[inputs['manifest']['primary_change']][1], 'After · single-mask tracking')
        self.comparison['after']['tracking_multimask'] = True; self.write_records()
        with self.assertRaisesRegex(ValueError, 'multi-mask to single-mask'):
            plot.read_inputs(self.data, self.run)
        self.comparison['after']['tracking_multimask'] = False
        self.comparison['after']['prompt_mode'] = 'box'; self.write_records()
        with self.assertRaisesRegex(ValueError, 'mode'):
            plot.read_inputs(self.data, self.run)

    def test_replaced_artifact_and_changed_ct_are_rejected(self):
        with (self.run/'after.npz').open('ab') as stream:
            stream.write(b'changed')
        with self.assertRaisesRegex(ValueError, 'Artifact integrity'):
            plot.read_inputs(self.data, self.run)
        self.manifest['artifacts_sha256']['after.npz'] = file_sha256(self.run/'after.npz'); self.write_records()
        self.stack.slices[0].path.write_bytes(b'replaced CT fixture')
        with self.assertRaisesRegex(ValueError, 'POST bytes'):
            plot.read_inputs(self.data, self.run)

    def test_upstream_input_contract_does_not_silently_change_window_or_tracking(self):
        self.manifest['primary_change'] = 'float_bilinear_to_upstream_uint8'
        self.comparison['after']['prompt_mode'] = 'binary_reinjection'
        for name, mode in (('before', 'float_bilinear'), ('after', 'upstream_uint8')):
            self.comparison[name]['tracking_multimask'] = True
            self.comparison[name]['inference'] = {'settings': {
                'input_mode': mode, 'window_width_hu': 400., 'window_level_hu': 40.}}
        self.write_records()
        inputs = plot.read_inputs(self.data, self.run)
        self.assertEqual(plot.CHANGES[inputs['manifest']['primary_change']][1], 'After · upstream uint8 input')
        settings = self.comparison['after']['inference']['settings']
        for key, wrong in (('input_mode', 'float_bilinear'), ('window_width_hu', 800.), ('window_level_hu', 200.)):
            original = settings[key]; settings[key] = wrong; self.write_records()
            with self.assertRaisesRegex(ValueError, 'input comparison'):
                plot.read_inputs(self.data, self.run)
            settings[key] = original
        self.comparison['after']['tracking_multimask'] = False; self.write_records()
        with self.assertRaisesRegex(ValueError, 'input comparison'):
            plot.read_inputs(self.data, self.run)

    @unittest.skipUnless(importlib.util.find_spec('matplotlib'), 'Optional figure dependencies are not installed.')
    def test_cli_embeds_truetype_in_panel_and_multipage_pdf_exports(self):
        inputs = plot.read_inputs(self.data, self.run)
        output = self.folder/'figures'
        with patch.object(plot, 'read_inputs', return_value=inputs), patch('sys.argv', [
                'plot_prompt_conditioning', '--data', str(self.data), '--run', str(self.run),
                '--output', str(output)]):
            plot.main()
        for name in ('orthogonal.pdf', 'atlas.pdf'):
            content = (output/name).read_bytes()
            self.assertTrue(b'/FontFile2' in content, f'{name}: missing embedded TrueType font')
            self.assertFalse(b'/Subtype /Type3' in content, f'{name}: unexpected Type3 font')
        record = json.loads((output/'manifest.json').read_text())
        for name, expected in record['outputs_sha256'].items():
            self.assertEqual(file_sha256(output/name), expected, name)

    def test_replay_counts_geometry_and_padding_cannot_be_silently_ignored(self):
        self.manifest['replay_exact'] = False; self.write_records()
        with self.assertRaisesRegex(ValueError, 'exact replay'):
            plot.read_inputs(self.data, self.run)
        self.manifest['replay_exact'] = True
        self.comparison['after']['metrics']['voxels'] += 1; self.write_records()
        with self.assertRaisesRegex(ValueError, 'counts disagree'):
            plot.read_inputs(self.data, self.run)
        self.comparison['after']['metrics']['voxels'] -= 1
        self.manifest['sources']['post']['ordered_sop_uids'].reverse(); self.write_records()
        with self.assertRaisesRegex(ValueError, 'ordered instance'):
            plot.read_inputs(self.data, self.run)
        self.manifest['sources']['post']['ordered_sop_uids'].reverse(); self.write_records()
        self.valid[2, 3, 4] = False
        with self.assertRaisesRegex(ValueError, 'invalid CT support'):
            plot.read_inputs(self.data, self.run)


if __name__ == '__main__':
    unittest.main()
