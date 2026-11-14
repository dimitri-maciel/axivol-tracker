"""Assets MedSAM sintéticos: extracción acotada y publicación sin reemplazo."""
from contextlib import ExitStack
import hashlib
from io import BytesIO
from pathlib import Path
import tarfile
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from experiments import medsam_slice_refine as adapter
from experiments import prepare_medsam as setup


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.contexts = ExitStack(); self.addCleanup(self.contexts.close)
        self.weights = b'verified synthetic checkpoint'
        for module in (setup, adapter):
            for name, value in (('CHECKPOINT_BYTES', len(self.weights)),
                                ('CHECKPOINT_SHA256', hashlib.sha256(self.weights).hexdigest())):
                self.contexts.enter_context(patch.object(module, name, value))
        self.contexts.enter_context(patch.object(adapter, 'CHECKPOINT_MD5', hashlib.md5(self.weights).hexdigest()))
        self.package = {'__init__.py': b'# synthetic package\n', 'model.py': b'# never executed\n'}
        digest = hashlib.sha256()
        for name, contents in sorted(self.package.items()):
            digest.update(name.encode()+b'\0'+hashlib.sha256(contents).digest()+b'\n')
        self.contexts.enter_context(patch.object(setup, 'SOURCE_PY_FILES', len(self.package)))
        self.contexts.enter_context(patch.object(setup, 'SOURCE_TREE_SHA256', digest.hexdigest()))

    def archive(self, extras=()):
        path = self.root/'source.tar.gz'
        prefix = f'MedSAM-{setup.SOURCE_COMMIT}/'
        records = [(prefix+'segment_anything/'+name, contents, tarfile.REGTYPE, '')
                   for name, contents in self.package.items()]
        records += [(prefix+'LICENSE', b'synthetic license', tarfile.REGTYPE, ''),
                    (prefix+'setup.py', b'raise RuntimeError("never run")', tarfile.REGTYPE, ''),
                    (prefix+'data/never.dcm', b'not installed', tarfile.REGTYPE, ''), *extras]
        with tarfile.open(path, 'w:gz') as archive:
            for name, contents, kind, link in records:
                member = tarfile.TarInfo(name); member.type = kind; member.linkname = link
                member.size = len(contents) if kind == tarfile.REGTYPE else 0
                archive.addfile(member, BytesIO(contents) if member.size else None)
        return path

    def test_extracts_only_package_and_license_without_install_hooks_or_data(self):
        output = self.root/'source'; output.mkdir()
        result = setup.extract_source(self.archive(), output)
        self.assertEqual(result['python_files'], 2)
        self.assertEqual(sorted(str(p.relative_to(output)) for p in output.rglob('*') if p.is_file()),
                         ['LICENSE', 'segment_anything/__init__.py', 'segment_anything/model.py'])
        (output/'segment_anything/model.py').write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'pinned Python tree'):
            setup.source_tree(output)

    def test_traversal_absolute_paths_symlinks_and_hardlinks_are_rejected(self):
        prefix = f'MedSAM-{setup.SOURCE_COMMIT}/'
        attacks = [(prefix+'segment_anything/../../escape', b'x', tarfile.REGTYPE, ''),
                   (prefix+'/absolute', b'x', tarfile.REGTYPE, ''),
                   (prefix+'segment_anything/link.py', b'', tarfile.SYMTYPE, '/outside'),
                   (prefix+'segment_anything/hard.py', b'', tarfile.LNKTYPE, prefix+'LICENSE')]
        for index, attack in enumerate(attacks):
            with self.subTest(kind=attack[2]):
                output = self.root/f'unsafe-{index}'; output.mkdir()
                with self.assertRaises(ValueError):
                    setup.extract_source(self.archive([attack]), output)
                self.assertFalse((self.root/'escape').exists())
                self.assertFalse(any(p.is_symlink() for p in output.rglob('*')))

    def test_weights_stage_inside_existing_writable_folder_under_readonly_parent(self):
        parent = self.root/'readonly'; parent.mkdir()
        destination = parent/'weights'; destination.mkdir()
        parent.chmod(0o500)
        temporary_directories = []
        original = setup.tempfile.TemporaryDirectory
        def staging(*args, **kwargs):
            temporary_directories.append(kwargs['dir'])
            return original(*args, **kwargs)
        try:
            with patch.object(setup, 'urlopen', return_value=BytesIO(self.weights)), \
                 patch.object(setup.tempfile, 'TemporaryDirectory', side_effect=staging), \
                 patch.object(adapter, '_load_model', side_effect=AssertionError('Setup must not deserialize weights.')) as load:
                setup.prepare('weights', destination)
            self.assertEqual(temporary_directories, [destination.resolve()])
            self.assertEqual((destination/'medsam_vit_b.pth').read_bytes(), self.weights)
            self.assertFalse(list(destination.glob('.medsam-*')))
            load.assert_not_called()
            with patch.object(setup, 'urlopen') as network:
                setup.prepare('weights', destination)
                network.assert_not_called()
        finally:
            parent.chmod(0o700)

    def test_bad_hash_truncation_and_oversize_never_publish_partial_checkpoint(self):
        for content in (b'x'*len(self.weights), self.weights[:-1], self.weights+b'x'):
            with self.subTest(size=len(content)):
                destination = self.root/'weights'
                with patch.object(setup, 'urlopen', return_value=BytesIO(content)), self.assertRaises(ValueError):
                    setup.prepare('weights', destination)
                self.assertEqual(list(destination.iterdir()), [])

    def test_concurrent_checkpoint_is_preserved_by_atomic_no_replace_publication(self):
        destination = self.root/'weights'; destination.mkdir()
        target = destination/'medsam_vit_b.pth'
        def response(*args, **kwargs):
            target.write_bytes(b'another owner')
            return BytesIO(self.weights)
        with patch.object(setup, 'urlopen', side_effect=response), self.assertRaises(FileExistsError):
            setup.prepare('weights', destination)
        self.assertEqual(target.read_bytes(), b'another owner')
        self.assertFalse(list(destination.glob('.medsam-*')))
        with patch.object(setup, 'urlopen') as network, self.assertRaises(ValueError):
            setup.prepare('weights', destination)
        network.assert_not_called()
        self.assertEqual(target.read_bytes(), b'another owner')

    def test_source_destination_appearing_during_download_is_not_replaced(self):
        archive = self.archive().read_bytes(); destination = self.root/'installed'
        def response(*args, **kwargs):
            destination.mkdir(); (destination/'owner').write_text('preserve')
            return BytesIO(archive)
        with patch.object(setup, 'ARCHIVE_BYTES', len(archive)), \
             patch.object(setup, 'ARCHIVE_SHA256', hashlib.sha256(archive).hexdigest()), \
             patch.object(setup, 'urlopen', side_effect=response), self.assertRaises(FileExistsError):
            setup.prepare('source', destination)
        self.assertEqual((destination/'owner').read_text(), 'preserve')
        self.assertFalse(list(self.root.glob('.medsam-*')))


if __name__ == '__main__':
    unittest.main()
