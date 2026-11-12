"""Persistence and HTTP boundaries; synthetic arrays are never patient results."""
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from unittest.mock import Mock
from zipfile import ZipFile
from dataclasses import replace
import json
import unittest

import numpy as np

from hcclab.candidates import CandidateStore, file_sha256, stack_identity
from hcclab.geometry import GeometryError, Slice, build_stack
from hcclab.lab import Lab
from hcclab.server import handler


class CandidateStoreTests(unittest.TestCase):
    def test_pre_versions_are_filtered_before_the_recent_limit(self):
        with TemporaryDirectory() as directory:
            store = CandidateStore(Path(directory))
            mask = np.ones((1, 1, 1), dtype=bool)
            metadata = {'status': 'unreviewed', 'stage': 'PRE', 'case_id': 'SYNTHETIC',
                        'series_uid': '1.2.3', 'stack_index': 0, 'source_segment': 2,
                        'created_utc': '2026-01-01T00:00:00+00:00', 'shape_zyx': [1, 1, 1], 'voxels': 1}
            mass = store.save(mask, metadata)
            for _ in range(20):
                store.save(mask, dict(metadata, source_segment=1, created_utc='2026-01-02T00:00:00+00:00'))
            self.assertEqual(len(store.list('SYNTHETIC', '1.2.3', 0)), 20)
            selected = store.list('SYNTHETIC', '1.2.3', 0, source_segment=2)
            self.assertEqual([item['id'] for item in selected], [mass['id']])

    def test_changed_source_pixels_with_same_geometry_cannot_reuse_candidate(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            first, second = root / 'a.dcm', root / 'b.dcm'
            first.write_bytes(b'synthetic source A')
            second.write_bytes(b'synthetic source B')
            a = Slice(first, '1.2.3', '1', '1.2.4', (2, 2), (1, 1), (1, 0, 0, 0, 1, 0), (0, 0, 0))
            b = replace(a, path=second, sop='1.2.5', position=(0, 0, 1))
            stack = build_stack([a, b])
            lab = Lab(root, root / 'candidates')
            lab.stack = Mock(return_value=({}, stack))
            lab.post_stack = Mock(return_value=({}, stack))
            document = lab.candidates.save(np.ones((2, 2, 2), dtype=bool), {
                'status': 'unreviewed', 'case_id': 'SYNTHETIC', 'series_uid': '1.2.6', 'stack_index': 0,
                'shape_zyx': [2, 2, 2], 'voxels': 8,
                'source': {'stack_identity': stack_identity(stack),
                           'slices': [{'sha256': file_sha256(s.path)} for s in stack.slices]}})
            lab.candidate_for_stack(document['id'], '1.2.6', 0)
            first.write_bytes(b'replaced synthetic source pixels')
            with self.assertRaises(GeometryError):
                lab.candidate_for_stack(document['id'], '1.2.6', 0)

    def test_restart_export_and_immutable_runs(self):
        with TemporaryDirectory() as directory:
            store = CandidateStore(Path(directory))
            mask = np.zeros((3, 4, 5), dtype=bool)
            mask[1, 1:3, 2:4] = True
            metadata = {'status': 'unreviewed', 'case_id': 'SYNTHETIC', 'series_uid': '1.2.3',
                        'stack_index': 0, 'created_utc': '2026-01-01T00:00:00+00:00',
                        'shape_zyx': [3, 4, 5], 'voxels': 4}
            first = store.save(mask, metadata)
            second = store.save(mask, metadata)
            self.assertNotEqual(first['id'], second['id'])
            reopened = CandidateStore(Path(directory))
            np.testing.assert_array_equal(reopened.mask(first['id']), mask)
            self.assertEqual(len(reopened.list('SYNTHETIC', '1.2.3', 0)), 2)
            self.assertEqual(reopened.list('SYNTHETIC', '1.2.3', 1), [])
            with ZipFile(BytesIO(reopened.export(first['id']))) as archive:
                self.assertEqual(set(archive.namelist()), {'mask.npz', 'metadata.json'})
                exported = json.loads(archive.read('metadata.json'))
                self.assertEqual(exported['status'], 'unreviewed')
                with np.load(BytesIO(archive.read('mask.npz')), allow_pickle=False) as saved:
                    np.testing.assert_array_equal(saved['mask'], mask)

    def test_tampered_mask_and_unsafe_id_are_rejected(self):
        with TemporaryDirectory() as directory:
            store = CandidateStore(Path(directory))
            with self.assertRaises(KeyError):
                store.metadata('../../outside')
            document = store.save(np.ones((2, 2, 2), dtype=bool),
                                  {'status': 'unreviewed', 'shape_zyx': [2, 2, 2], 'voxels': 8})
            store.mask(document['id'])
            (Path(directory) / document['id'] / 'mask.npz').write_bytes(b'changed')
            with self.assertRaises(GeometryError):
                store.export(document['id'])
            with self.assertRaises(GeometryError):
                CandidateStore(Path(directory)).mask(document['id'])


class CandidateHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lab = Mock()
        cls.lab.create_candidate.return_value = {'id': 'a' * 32, 'status': 'unreviewed'}
        cls.lab.create_pre_edit.return_value = {'id': 'b' * 32, 'status': 'unreviewed', 'stage': 'PRE'}
        cls.lab.create_pre_refinement.return_value = {'id': 'c' * 32, 'status': 'unreviewed', 'stage': 'PRE'}
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), handler(cls.lab))
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def request(self, headers, body=b'{}', path='/api/candidates'):
        self.lab.create_candidate.reset_mock()
        self.lab.create_pre_edit.reset_mock()
        self.lab.create_pre_refinement.reset_mock()
        connection = HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        connection.request('POST', path, body, headers)
        response = connection.getresponse()
        result = response.status, json.loads(response.read())
        connection.close()
        return result

    def test_local_json_can_create_candidate(self):
        host = f'127.0.0.1:{self.server.server_port}'
        status, result = self.request({'Content-Type': 'application/json', 'Origin': 'http://' + host})
        self.assertEqual(status, 201)
        self.assertEqual(result['status'], 'unreviewed')
        self.lab.create_candidate.assert_called_once_with({})

    def test_cross_origin_and_nonlocal_host_do_not_create(self):
        for extra in [{'Origin': 'https://outside.example'}, {'Origin': 'null'},
                      {'Host': 'outside.example'}, {'Sec-Fetch-Site': 'cross-site'}]:
            with self.subTest(extra=extra):
                status, _ = self.request(dict({'Content-Type': 'application/json'}, **extra))
                self.assertEqual(status, 403)
                self.lab.create_candidate.assert_not_called()

    def test_bad_content_and_oversized_request_do_not_create(self):
        for headers, body, expected in [({'Content-Type': 'text/plain'}, b'{}', 415),
                                        ({'Content-Type': 'application/json'}, b'{', 422),
                                        ({'Content-Type': 'application/json'}, b' ' * 8193, 413)]:
            with self.subTest(expected=expected):
                status, _ = self.request(headers, body)
                self.assertEqual(status, expected)
                self.lab.create_candidate.assert_not_called()

    def test_pre_edits_use_separate_operation_and_bounded_local_json(self):
        headers = {'Content-Type': 'application/json'}
        body = json.dumps({'strokes': ['synthetic-transport-check'] * 1000}).encode()
        self.assertGreater(len(body), 8192)
        status, result = self.request(headers, body, '/api/pre-edits')
        self.assertEqual((status, result['stage']), (201, 'PRE'))
        self.lab.create_pre_edit.assert_called_once_with(json.loads(body))
        self.lab.create_candidate.assert_not_called()
        for extra, body, expected in [({'Origin': 'https://outside.example'}, b'{}', 403),
                                      ({'Host': 'outside.example'}, b'{}', 403),
                                      ({'Content-Type': 'text/plain'}, b'{}', 415),
                                      ({}, b' ' * 131073, 413), ({}, b'{', 422)]:
            with self.subTest(expected=expected, extra=extra):
                status, _ = self.request(dict(headers, **extra), body, '/api/pre-edits')
                self.assertEqual(status, expected)
                self.lab.create_pre_edit.assert_not_called()
                self.lab.create_candidate.assert_not_called()

    def test_pre_refinement_uses_bounded_same_origin_json(self):
        headers = {'Content-Type': 'application/json'}
        status, document = self.request(headers, b'{"band_mm": 3}', '/api/pre-refinements')
        self.assertEqual((status, document['stage']), (201, 'PRE'))
        self.lab.create_pre_refinement.assert_called_once_with({'band_mm': 3})
        self.lab.create_pre_edit.assert_not_called()
        for extra, body, expected in [({'Origin': 'https://outside.example'}, b'{}', 403),
                                      ({'Host': 'outside.example'}, b'{}', 403),
                                      ({'Content-Type': 'text/plain'}, b'{}', 415),
                                      ({}, b' ' * 8193, 413)]:
            with self.subTest(extra=extra, expected=expected):
                status, _ = self.request(dict(headers, **extra), body, '/api/pre-refinements')
                self.assertEqual(status, expected)
                self.lab.create_pre_refinement.assert_not_called()
