"""Synthetic plotting/provenance tests; no model loading or patient reference."""
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
from experiments import plot_medsam_slice as plot


class AxialReviewTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name); self.data = self.folder/'data'; self.data.mkdir()
        self.run = self.folder/'run'; self.run.mkdir()
        self.shape = (60, 30, 40)
        self.before = np.zeros(self.shape, bool); self.before[53:56, 10:14, 16:21] = True
        self.after = self.before.copy(); self.after[53, 10, 16] = False; self.after[54, 1, 1] = True
        self.valid = np.ones(self.shape, bool)
        slices = []
        for z in range(self.shape[0]):
            path = self.data/f'{z}.dcm'; path.write_bytes(b'axial-native-fixture-'+bytes([z]))
            slices.append(SimpleNamespace(path=path, acquisition='1', sop=f'SOP-{z}', shape=self.shape[1:],
                spacing=(1., 1.), position=(0., 0., z*2.5), orientation=(1., 0., 0., 0., 1., 0.)))
        self.stack = SimpleNamespace(slices=slices, interval_mm=2.5, issue=None)
        selection = {'pre_series_uid': 'PRE', 'post_series_uid': 'POST', 'pre_acquisition': '3',
                     'post_acquisition': '1', 'mass_segment': 2, 'liver_segment': 1}
        self.comparison = {'baseline_saved_exact': True, **{name: {'method': method,
            'status': 'experimental_unreviewed', 'metrics': {'voxels': int(getattr(self, name).sum())}}
            for name, method in plot.METHODS.items()}}
        box = [11., 5., 25., 18.]
        self.inference = {'method': 'original_medsam_slice_refinement', 'status': 'completed_unreviewed',
            'voxels': int(self.after.sum()), 'shape_zyx': list(self.shape), 'spacing_zyx_mm': [2.5, 1., 1.],
            'initial_array_sha256': hashlib.sha256(self.before.tobytes(order='C')).hexdigest(),
            'settings': {'margin_mm': 5., 'box_source_neighbors': [-1, 0, 1], 'input_image': 'full_native_axial_plane',
                'temporal_memory': False, 'output_box_clipping': False, 'output_component_filtering': False},
            'coverage': {'processed_slice_indices': [53, 54, 55],
                'unprocessed_planes': 'inherited_baseline_not_reevaluated_not_confirmed_absence',
                'all_native_slices_retained': True},
            'frames': [{'z': z, 'box_xyxy_native': box.copy(),
                'box_xyxy_1024': (np.array(box)/[40, 30, 40, 30]*1024).tolist(),
                'margin_clipped_faces': [], 'area': int(self.after[z].sum())} for z in [53, 54, 55]]}
        self.comparison['after']['inference'] = self.inference
        artifacts = {}
        for name, filename in plot.MASK_FILES.items():
            np.savez_compressed(self.run/filename, mask=getattr(self, name))
            artifacts[filename] = file_sha256(self.run/filename)
        self.manifest = {'schema_version': 1, 'kind': 'post_medsam_slice_refinement', 'status': 'completed',
            'case_id': 'SYNTHETIC', 'selection': selection, 'source_integrity_verified': True,
            'baseline_saved_exact': True, 'baseline_manifest_sha256': 'a'*64, 'baseline_regenerated': False,
            'postprocessing_after_medsam': 'none', 'baseline_mask_sha256': artifacts['before.npz'],
            'primary_change': plot.METHODS['after'], 'artifacts_sha256': artifacts,
            'sources': {'case_id': 'SYNTHETIC', 'selection': selection, 'target_label': 'Mass', 'post': {
                'shape_zyx': list(self.shape), 'spacing_zyx_mm': [2.5, 1., 1.], 'stack_identity': 'test-grid',
                'origin_lps_mm': list(slices[0].position), 'orientation_patient': list(slices[0].orientation),
                'ordered_sop_uids': [s.sop for s in slices], 'ordered_ct_sha256': [file_sha256(s.path) for s in slices]}}}
        self.write_records()
        row = {'PatientID': 'SYNTHETIC', 'StudyInstanceUID': 'POST-study'}
        catalog = SimpleNamespace(by_uid={'POST': row}, local=lambda row: True, folder=lambda row: self.data,
            case=lambda case: {'studies': [{'stage': 'POST', 'series': [{'uid': 'POST'}]}]})
        mocks = ExitStack(); self.addCleanup(mocks.close)
        for name, value in [('Catalog', catalog), ('stacks', [self.stack]), ('stack_identity', 'test-grid')]:
            mocks.enter_context(patch.object(plot.shared, name, return_value=value))
        mocks.enter_context(patch.object(plot.shared, 'load_hu_volume',
            side_effect=lambda *a, **k: (np.zeros(self.shape), self.valid)))

    def write_records(self):
        (self.run/'comparison.json').write_text(json.dumps(self.comparison))
        self.manifest['artifacts_sha256']['comparison.json'] = file_sha256(self.run/'comparison.json')
        (self.run/'manifest.json').write_text(json.dumps(self.manifest))

    def save_after(self):
        np.savez_compressed(self.run/'after.npz', mask=self.after)
        self.manifest['artifacts_sha256']['after.npz'] = file_sha256(self.run/'after.npz')
        self.comparison['after']['metrics']['voxels'] = int(self.after.sum())
        self.inference['voxels'] = int(self.after.sum())
        for frame in self.inference['frames']:
            frame['area'] = int(self.after[frame['z']].sum())
        self.write_records()

    def test_saved_native_output_keeps_prediction_outside_prompt_box(self):
        inputs = plot.read_inputs(self.data, self.run)
        self.assertTrue(inputs['after'][54, 1, 1])  # A prompt is not an output crop.
        self.assertEqual(len(inputs['artifacts']), 3)
        self.assertNotIn('replay_exact', inputs['manifest'])
        self.assertEqual(len(plot.descriptive_rows(inputs)), self.shape[0])

    def test_source_hash_and_method_contract_are_mandatory(self):
        for key, wrong in [('baseline_saved_exact', False), ('baseline_mask_sha256', 'b'*64),
                           ('kind', 'post_neural_refinement'), ('postprocessing_after_medsam', 'graph_cut')]:
            original = self.manifest[key]; self.manifest[key] = wrong; self.write_records()
            with self.assertRaisesRegex(ValueError, 'saved baseline'):
                plot.read_inputs(self.data, self.run)
            self.manifest[key] = original
        self.comparison['after']['method'] = 'nninteractive_initial_segmentation'; self.write_records()
        with self.assertRaisesRegex(ValueError, 'method'):
            plot.read_inputs(self.data, self.run)

    def test_native_grid_initial_mask_and_frame_counts_must_match(self):
        for key, wrong in [('initial_array_sha256', 'b'*64), ('shape_zyx', [60, 40, 30]),
                           ('spacing_zyx_mm', [1., 1., 1.]), ('voxels', 0)]:
            original = self.inference[key]; self.inference[key] = wrong; self.write_records()
            with self.assertRaisesRegex(ValueError, 'initial mask'):
                plot.read_inputs(self.data, self.run)
            self.inference[key] = original
        self.inference['frames'][0]['area'] += 1; self.write_records()
        with self.assertRaisesRegex(ValueError, 'axial area'):
            plot.read_inputs(self.data, self.run)

    def test_processed_planes_must_be_exact_and_unprocessed_pixels_unchanged(self):
        coverage = self.inference['coverage']
        for wrong in ([53, 54], [53, 54, 55, 56], [53, 53, 55], [53., 54, 55]):
            coverage['processed_slice_indices'] = wrong; self.write_records()
            with self.assertRaisesRegex(ValueError, 'axial scope'):
                plot.read_inputs(self.data, self.run)
        coverage['processed_slice_indices'] = [53, 54, 55]
        self.after[56, 2, 2] = True; self.save_after()
        with self.assertRaisesRegex(ValueError, 'unprocessed plane'):
            plot.read_inputs(self.data, self.run)

    def test_boxes_are_derived_from_frozen_neighbors_not_model_output(self):
        frame = self.inference['frames'][1]
        for key, wrong in [('box_xyxy_native', [0., 0., 25., 18.]),
                           ('box_xyxy_1024', [0., 0., 640., 614.4]), ('margin_clipped_faces', ['x_min'])]:
            original = frame[key]; frame[key] = wrong; self.write_records()
            with self.assertRaises(ValueError):
                plot.read_inputs(self.data, self.run)
            frame[key] = original
        self.inference['settings']['margin_mm'] = 6.; self.write_records()
        with self.assertRaisesRegex(ValueError, 'fixed neighbour boxes'):
            plot.read_inputs(self.data, self.run)

    def test_neighbor_union_and_anisotropic_physical_margin_at_image_edges(self):
        before = np.zeros((5, 12, 16), bool)
        before[2, 1:3, 1:4] = True; before[3, 0, 10] = True
        inference = json.loads(json.dumps(self.inference))
        inference['coverage']['processed_slice_indices'] = [2, 3]
        # dx=2 gives 2.5 columns; dy=.5 gives 10 rows. The neighbour at
        # x=10 must extend both prompts. Upper coordinates may equal W/H.
        inference['frames'] = [{'z': z, 'box_xyxy_native': [0., 0., 12.5, 12.],
            'box_xyxy_1024': [0., 0., 800., 1024.], 'margin_clipped_faces': ['x_min', 'y_min'],
            'area': int(before[z].sum())} for z in [2, 3]]
        plot.verify_plane_scope(inference, before, before.copy(), [2.5, .5, 2.])
        inference['frames'][0]['box_xyxy_native'][2] = 5.5  # Current slice only is wrong.
        with self.assertRaisesRegex(ValueError, 'neighbour union'):
            plot.verify_plane_scope(inference, before, before.copy(), [2.5, .5, 2.])

    def test_empty_prediction_is_visible_as_loss_not_confirmed_absence(self):
        self.after[:] = False; self.save_after()
        inputs = plot.read_inputs(self.data, self.run)
        rows = plot.descriptive_rows(inputs)
        self.assertEqual(sum(r['lost_occupied_slice'] for r in rows), 3)
        self.assertTrue(all(r['after_hu_median'] is None for r in rows))
        selected = plot.shared.display_selection(inputs['before'], inputs['after'], inputs['spacing'])
        self.assertEqual(selected['atlas_indices'], list(range(50, 59)))

    def test_changed_artifact_and_pixel_padding_are_rejected(self):
        self.valid[54, 1, 1] = False
        with self.assertRaisesRegex(ValueError, 'invalid CT support'):
            plot.read_inputs(self.data, self.run)
        self.valid[:] = True
        with (self.run/'after.npz').open('ab') as stream:
            stream.write(b'changed')
        with self.assertRaisesRegex(ValueError, 'Artifact integrity'):
            plot.read_inputs(self.data, self.run)

    @unittest.skipUnless(importlib.util.find_spec('matplotlib'), 'Optional figure dependencies are not installed.')
    def test_export_labels_full_slice_csv_and_truetype_pdfs(self):
        import matplotlib.pyplot as plt
        inputs = plot.read_inputs(self.data, self.run)
        selected = plot.shared.display_selection(inputs['before'], inputs['after'], inputs['spacing'])
        figure = plot.shared.make_figure(inputs, selected['focus'], selected['bounds'],
            plot.shared.WINDOWS[0], 'Synthetic axial review', **plot.FIGURE_OPTIONS)
        captions = ' '.join(t.get_text() for t in figure.texts)
        self.assertIn('After: no postprocessing.', captions)
        self.assertIn('Other planes inherited.', captions)
        self.assertNotIn('nnInteractive', captions); plt.close(figure)
        output = self.folder/'figures'
        with patch.object(plot, 'read_inputs', return_value=inputs), patch('sys.argv', ['plot_medsam_slice',
                '--data', str(self.data), '--run', str(self.run), '--output', str(output)]):
            plot.main()
        record = json.loads((output/'manifest.json').read_text())
        self.assertEqual(record['total_acquired_slices_in_csv'], self.shape[0])
        self.assertEqual(record['inference']['after'], self.inference)
        self.assertFalse(record['probability_artifact_verified_by_plotter'])
        for name, digest in record['outputs_sha256'].items():
            self.assertEqual(file_sha256(output/name), digest)
        for name in ('focus-W400.pdf', 'focus-W800.pdf', 'orthogonal.pdf', 'atlas.pdf'):
            data = (output/name).read_bytes()
            self.assertIn(b'/FontFile2', data)
            self.assertNotIn(b'/Subtype /Type3', data)


if __name__ == '__main__':
    unittest.main()
