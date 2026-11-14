"""Descarga sintética de assets: límites, hashes y publicación sin sobrescritura."""
from io import BytesIO
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from experiments import nninteractive_refine as adapter
from experiments import prepare_nninteractive as setup


class PrepareAssetsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.destination = self.root/'nnInteractive_v1.0'
        self.contents = {'LICENSE': b'public model license', 'fold_0/checkpoint_final.pth': b'fake test weights'}
        assets = {name: (len(data), hashlib.sha256(data).hexdigest()) for name, data in self.contents.items()}
        self.asset_patch = patch.dict(adapter.ASSETS, assets, clear=True)
        self.asset_patch.start(); self.addCleanup(self.asset_patch.stop)
        self.urls = []

    def response(self, url, timeout):
        self.assertEqual(timeout, 60)
        self.assertTrue(url.startswith(setup.MODEL_BASE+'/'))
        self.urls.append(url)
        return BytesIO(self.contents[url.removeprefix(setup.MODEL_BASE+'/')])

    def test_complete_verified_files_are_published_once_without_loading_weights(self):
        with patch.object(setup, 'urlopen', side_effect=self.response), \
             patch.object(adapter, '_load_session', side_effect=AssertionError('Setup must never load weights.')) as load:
            info = setup.fetch_assets(self.destination)
        self.assertEqual(info['status'], 'downloaded_verified')
        self.assertEqual(len(self.urls), 2)
        for name, content in self.contents.items():
            self.assertEqual((self.destination/name).read_bytes(), content)
        self.assertFalse(list(self.root.glob('.*partial-*')))
        load.assert_not_called()
        with patch.object(setup, 'urlopen', side_effect=AssertionError('Valid assets need no download.')):
            reused = setup.fetch_assets(self.destination)
        self.assertEqual(reused['status'], 'already_verified')

    def test_bad_hash_same_size_fails_without_partial_destination(self):
        self.contents['fold_0/checkpoint_final.pth'] = b'changed---weights'  # Igual longitud.
        self.assertEqual(len(self.contents['fold_0/checkpoint_final.pth']), adapter.ASSETS['fold_0/checkpoint_final.pth'][0])
        with patch.object(setup, 'urlopen', side_effect=self.response), self.assertRaisesRegex(ValueError, 'SHA-256'):
            setup.fetch_assets(self.destination)
        self.assertFalse(self.destination.exists())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_oversized_or_truncated_asset_never_publishes(self):
        original = self.contents['LICENSE']
        for data, message in ((original+b'x', 'exceeds'), (original[:-1], 'size or SHA-256')):
            with self.subTest(length=len(data)):
                self.contents['LICENSE'] = data
                with patch.object(setup, 'urlopen', side_effect=self.response), self.assertRaisesRegex(ValueError, message):
                    setup.fetch_assets(self.destination)
                self.assertFalse(self.destination.exists())
                self.assertEqual(list(self.root.iterdir()), [])

    def test_existing_invalid_destination_is_preserved_without_network(self):
        self.destination.mkdir()
        marker = self.destination/'keep.txt'; marker.write_bytes(b'preserve me')
        with patch.object(setup, 'urlopen') as download, self.assertRaises(ValueError):
            setup.fetch_assets(self.destination)
        download.assert_not_called()
        self.assertEqual(marker.read_bytes(), b'preserve me')
        self.assertEqual([p.name for p in self.destination.iterdir()], ['keep.txt'])

    def test_network_failure_cleans_staging_but_keeps_other_files(self):
        marker = self.root/'unrelated'; marker.write_bytes(b'original')
        with patch.object(setup, 'urlopen', side_effect=OSError('synthetic network failure')), self.assertRaises(OSError):
            setup.fetch_assets(self.destination)
        self.assertFalse(self.destination.exists())
        self.assertEqual(list(self.root.iterdir()), [marker])
        self.assertEqual(marker.read_bytes(), b'original')

    def test_concurrent_destination_is_not_replaced(self):
        def response(url, timeout):
            result = self.response(url, timeout)
            if len(self.urls) == 2:
                self.destination.mkdir()
                (self.destination/'concurrent.txt').write_bytes(b'concurrent owner')
            return result
        with patch.object(setup, 'urlopen', side_effect=response), self.assertRaises(FileExistsError):
            setup.fetch_assets(self.destination)
        self.assertEqual((self.destination/'concurrent.txt').read_bytes(), b'concurrent owner')
        self.assertFalse(list(self.root.glob('.*partial-*')))

    def test_symlink_code_and_annotation_destinations_are_rejected(self):
        actual = self.root/'actual'; actual.mkdir()
        self.destination.symlink_to(actual, target_is_directory=True)
        code = Path(setup.__file__).resolve().parents[1]
        for destination in (self.destination, code/'weights', Path('/annotations/models')):
            with self.subTest(destination=destination), patch.object(setup, 'urlopen') as download:
                with self.assertRaises(ValueError):
                    setup.fetch_assets(destination)
                download.assert_not_called()
        self.assertEqual(setup.checked_destination(code/'data/models/nnInteractive_v1.0'),
                         code/'data/models/nnInteractive_v1.0')


if __name__ == '__main__':
    unittest.main()
