"""Saved-mask/HTTP contracts for 3D display; no clinical data or inference."""
from dataclasses import replace
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import urlopen
import json
import unittest

import numpy as np

from hcclab.candidates import file_sha256, stack_identity
from hcclab.geometry import GeometryError, Slice, build_stack
from hcclab.lab import Lab
from hcclab.server import handler


class SurfaceIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        first = Slice(self.root/'slice', '1.1', '1', '1.2', (5, 6), (.7, .8),
                      (1, 0, 0, 0, 1, 0), (10, 20, 30))
        self.stack = build_stack([replace(first, sop=f'1.{z+1}', position=(10, 20, 30+2*z)) for z in range(3)])
        self.lab = Lab(self.root/'data', self.root/'annotations')
        self.addCleanup(self.lab._candidate_surface.cache_clear)
        self.addCleanup(self.lab.candidates.mask.cache_clear)
        self.lab.stack = Mock(return_value=({}, self.stack))
        self.mask = np.zeros((3, 5, 6), bool); self.mask[1, 2, 3] = True
        self.document = self.lab.candidates.save(self.mask, {
            'status': 'unreviewed', 'stage': 'POST', 'case_id': 'SYNTHETIC',
            'series_uid': '1.3', 'stack_index': 0, 'acquisition': '1', 'label': 'Automatic candidate',
            'shape_zyx': list(self.mask.shape), 'voxels': 1, 'volume_cm3': .7*.8*2/1000,
            'source': {'stack_identity': stack_identity(self.stack)},
        })
        self.lab.candidate_for_stack = Mock(side_effect=lambda *a: self.lab.candidates.metadata(self.document['id']))

    def test_native_saved_surface_is_read_only_and_source_checked_on_cache_hit(self):
        folder = self.lab.candidates.folder(self.document['id'])
        before = {p.name: p.read_bytes() for p in folder.iterdir()}
        with patch('hcclab.surface.voxel_surface', wraps=__import__('hcclab.surface', fromlist=['voxel_surface']).voxel_surface) as make:
            first = self.lab.candidate_surface(self.document['id'])
            second = self.lab.candidate_surface(self.document['id'])
        self.assertEqual(make.call_count, 1)
        self.assertEqual(self.lab.candidate_for_stack.call_count, 4)
        self.assertEqual(first, second)
        self.assertEqual(first['candidate_id'], self.document['id'])
        self.assertEqual(first['review_status'], 'unreviewed')
        self.assertEqual(first['coordinate_system'], 'LPS')
        self.assertEqual(first['stats']['voxels'], 1)
        self.assertEqual(first['acquisition'], '1')
        self.assertEqual(first['source_metadata']['stack_identity'], stack_identity(self.stack))
        json.dumps(first, allow_nan=False)
        self.assertEqual({p.name: p.read_bytes() for p in folder.iterdir()}, before)

    def test_mask_tampering_rejected_even_with_cached_surface(self):
        self.lab.candidate_surface(self.document['id'])
        (self.lab.candidates.folder(self.document['id'])/'mask.npz').write_bytes(b'changed')
        with self.assertRaisesRegex(GeometryError, 'integrity'):
            self.lab.candidate_surface(self.document['id'])

    def test_source_verification_failure_prevents_surface(self):
        self.lab.candidate_for_stack.side_effect = GeometryError('Source CT files differ.')
        with patch('hcclab.surface.voxel_surface') as make, self.assertRaisesRegex(GeometryError, 'Source CT'):
            self.lab.candidate_surface(self.document['id'])
        make.assert_not_called()

    def test_new_hash_cannot_reuse_an_old_decoded_mask(self):
        first = self.lab.candidate_surface(self.document['id'])
        self.lab.candidates.mask(self.document['id'])  # Prime the 2D display cache.
        folder = self.lab.candidates.folder(self.document['id'])
        moved = np.roll(self.mask, 1, axis=2)  # Same shape, count and volume.
        np.savez_compressed(folder/'mask.npz', mask=moved)
        updated = dict(self.document, mask_sha256=file_sha256(folder/'mask.npz'))
        (folder/'metadata.json').write_text(json.dumps(updated))
        second = self.lab.candidate_surface(self.document['id'])
        self.assertEqual(second['mask_sha256'], updated['mask_sha256'])
        self.assertNotEqual(first['vertices_lps_mm'], second['vertices_lps_mm'])
        np.testing.assert_allclose(np.array(second['center_lps_mm'])-first['center_lps_mm'], [.8, 0, 0])

    def test_native_measurement_mismatch_rejected(self):
        path = self.lab.candidates.folder(self.document['id'])/'metadata.json'
        document = dict(self.document, volume_cm3=10)
        path.write_text(json.dumps(document))
        with self.assertRaisesRegex(GeometryError, 'measurements'):
            self.lab.candidate_surface(self.document['id'])
        self.assertFalse(self.lab.surface_lock.locked())

    def test_pre_mask_and_simultaneous_generation_have_explicit_states(self):
        self.lab.surface_lock.acquire()
        try:
            with self.assertRaisesRegex(ValueError, 'Another 3D surface'):
                self.lab.candidate_surface(self.document['id'])
        finally:
            self.lab.surface_lock.release()
        path = self.lab.candidates.folder(self.document['id'])/'metadata.json'
        path.write_text(json.dumps(dict(self.document, stage='PRE')))
        with self.assertRaisesRegex(GeometryError, 'follow-up mask'):
            self.lab.candidate_surface(self.document['id'])


class SurfaceHTTPTests(unittest.TestCase):
    def test_get_surface_routes_read_only_and_propagates_geometry_error(self):
        lab = Mock()
        lab.candidate_surface.return_value = {'status': 'surface', 'review_status': 'unreviewed'}
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler(lab))
        thread = Thread(target=server.serve_forever, daemon=True); thread.start()
        candidate_id = 'a'*32
        url = f'http://127.0.0.1:{server.server_port}/api/candidates/{candidate_id}/surface'
        try:
            with urlopen(url) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers['Cache-Control'], 'no-store')
                self.assertEqual(json.load(response)['review_status'], 'unreviewed')
            lab.candidate_surface.assert_called_once_with(candidate_id)
            lab.candidate_surface.side_effect = GeometryError('Incompatible geometry.')
            with self.assertRaises(HTTPError) as caught:
                urlopen(url)
            self.assertEqual(caught.exception.code, 422)
            self.assertEqual(json.load(caught.exception)['error'], 'Incompatible geometry.')
            lab.create_candidate.assert_not_called()
            lab.create_automatic_post_candidate.assert_not_called()
        finally:
            server.shutdown(); thread.join(timeout=2); server.server_close()


if __name__ == '__main__':
    unittest.main()
