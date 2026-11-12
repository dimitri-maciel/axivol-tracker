"""Downloader integrity checks with synthetic bytes; no network or real model."""
from io import BytesIO
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from hcclab import model_assets


class ModelAssetTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.data = Path(self.directory.name)
        self.bytes = b'synthetic model transport fixture, not executable'
        self.enterContext(patch.object(model_assets, 'CHECKPOINT_BYTES', len(self.bytes)))
        self.enterContext(patch.object(model_assets, 'CHECKPOINT_SHA256', hashlib.sha256(self.bytes).hexdigest()))

    def test_verified_atomic_download_and_reuse(self):
        with patch.object(model_assets, 'urlopen', return_value=BytesIO(self.bytes)) as download:
            metadata = model_assets.download_model(self.data)
            self.assertEqual(model_assets.download_model(self.data), metadata)
            download.assert_called_once_with(model_assets.CHECKPOINT_URL, timeout=30)
        self.assertEqual(model_assets.model_path(self.data).read_bytes(), self.bytes)
        self.assertEqual(metadata['checkpoint_sha256'], hashlib.sha256(self.bytes).hexdigest())
        self.assertEqual(len(list((self.data / 'models').iterdir())), 1)

    def test_corrupt_truncated_and_oversized_downloads_are_not_published(self):
        for content in (self.bytes[:-1], b'x' * len(self.bytes), self.bytes + b'overflow'):
            with self.subTest(content=content), patch.object(model_assets, 'urlopen', return_value=BytesIO(content)):
                with self.assertRaises(ValueError):
                    model_assets.download_model(self.data)
                self.assertFalse(model_assets.model_path(self.data).exists())
                self.assertEqual(list((self.data / 'models').iterdir()), [])

    def test_existing_bad_file_is_preserved_and_symlinks_rejected(self):
        destination = model_assets.model_path(self.data)
        destination.parent.mkdir()
        destination.write_bytes(b'keep existing invalid download for inspection')
        with patch.object(model_assets, 'urlopen') as download, self.assertRaises(ValueError):
            model_assets.download_model(self.data)
        download.assert_not_called()
        self.assertEqual(destination.read_bytes(), b'keep existing invalid download for inspection')
        destination.unlink()
        target = self.data / 'target'
        target.write_bytes(self.bytes)
        destination.symlink_to(target)
        with self.assertRaises(ValueError):
            model_assets.verify_model(destination)

    def test_upstream_configuration_is_verified_before_installing(self):
        spec = SimpleNamespace(submodule_search_locations=[str(self.data)])
        content = b'synthetic: configuration\n'
        with patch.object(model_assets.importlib.util, 'find_spec', return_value=spec), \
                patch.object(model_assets, 'CONFIG_BYTES', len(content)), \
                patch.object(model_assets, 'CONFIG_SHA256', hashlib.sha256(content).hexdigest()):
            with patch.object(model_assets, 'urlopen', return_value=BytesIO(content)):
                model_assets.install_config()
            target = self.data / 'configs' / model_assets.CONFIG_FILENAME
            self.assertEqual(target.read_bytes(), content)
            with patch.object(model_assets, 'urlopen', return_value=BytesIO(b'x'*len(content))):
                with self.assertRaises(ValueError):
                    model_assets.install_config()
            self.assertEqual(target.read_bytes(), content)


if __name__ == '__main__':
    unittest.main()
