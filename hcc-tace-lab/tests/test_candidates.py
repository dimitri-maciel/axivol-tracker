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
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), handler(cls.lab))
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def request(self, headers, body=b'{}'):
        self.lab.create_candidate.reset_mock()
        connection = HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        connection.request('POST', '/api/candidates', body, headers)
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
