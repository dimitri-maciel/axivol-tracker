"""Scientific plots must not pool different POST acquisitions or omit slices."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from experiments.plot_tracking import read_report, require_same_acquisition


class TrackingPlotTests(unittest.TestCase):
    def setUp(self):
        self.manifest = {'sources': {
            'case_id': 'synthetic',
            'selection': {'post_series_uid': '1.2.3', 'post_acquisition': '1'},
            'post': {'shape_zyx': [3, 4, 4], 'spacing_zyx_mm': [2, 1, 1],
                     'stack_identity': 'fixture', 'ordered_ct_sha256': ['a', 'b', 'c']}}}
        self.report = {'variant': 'full', 'metrics': {'voxels': 5}, 'inference': {
            'frames': [{'slice_index': z, 'area_voxels': area} for z, area in [(1, 3), (2, 0), (1, 3), (0, 2)]]}}

    def read(self, report=None):
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / 'manifest.json').write_text(json.dumps(self.manifest))
            path = folder / 'full.json'
            path.write_text(json.dumps(self.report if report is None else report))
            return read_report(path)

    def test_native_area_and_bidirectional_prompt_count(self):
        result = self.read()
        self.assertEqual(result['area'], [.02, .03, 0])
        self.assertEqual(len(result['source_hashes']), 2)

    def test_missing_empty_slice_is_rejected(self):
        self.report['inference']['frames'] = [r for r in self.report['inference']['frames'] if r['slice_index'] != 2]
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            self.read()

    def test_same_case_and_slice_count_do_not_prove_same_acquisition(self):
        first = self.read()
        for key, value in [('post_series_uid', '4.5.6'), ('post_acquisition', '2')]:
            second = deepcopy(first)
            second['identity'][key] = value
            with self.assertRaisesRegex(ValueError, 'identical POST'):
                require_same_acquisition([first, second])
        for key, value in [('spacing_zyx_mm', [5, 1, 1]), ('ordered_ct_sha256', ['x', 'b', 'c'])]:
            second = deepcopy(first)
            second['identity']['post'][key] = value
            with self.assertRaisesRegex(ValueError, 'identical POST'):
                require_same_acquisition([first, second])


if __name__ == '__main__':
    unittest.main()
