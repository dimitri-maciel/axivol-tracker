"""Prepare pinned public assets; never execute the upstream setup.py or weights."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tarfile
import tempfile
from urllib.request import urlopen

from .medsam_slice_refine import (
    SOURCE_COMMIT, SOURCE_PY_FILES, SOURCE_TREE_SHA256,
    CHECKPOINT_BYTES, CHECKPOINT_SHA256, verify_assets,
)

ARCHIVE_URL = f'https://codeload.github.com/bowang-lab/MedSAM/tar.gz/{SOURCE_COMMIT}'
ARCHIVE_BYTES = 33327385
ARCHIVE_SHA256 = '71fb5ac457805d9c0df0df0833b1bbd6a3da9489795b1d5b6c3a6377f11ef916'
WEIGHTS_URL = 'https://zenodo.org/records/10689643/files/medsam_vit_b.pth?download=1'


def download(url, path, size, digest):
    """Bound the download and verify bytes before publishing or extracting."""
    h, count = hashlib.sha256(), 0
    with urlopen(url, timeout=60) as response, path.open('xb') as stream:
        while chunk := response.read(min(1024*1024, size-count+1)):
            count += len(chunk)
            if count > size:
                raise ValueError('Asset exceeds its pinned size.')
            h.update(chunk); stream.write(chunk)
    if count != size or h.hexdigest() != digest:
        raise ValueError('Asset does not match its pinned size and SHA-256.')


def source_tree(root):
    digest = hashlib.sha256()
    paths = sorted((root/'segment_anything').rglob('*.py'))
    for p in paths:
        if p.is_symlink():
            raise ValueError('Source cannot contain symlinks.')
        digest.update(p.relative_to(root/'segment_anything').as_posix().encode()+b'\0')
        digest.update(hashlib.sha256(p.read_bytes()).digest()+b'\n')
    if len(paths) != SOURCE_PY_FILES or digest.hexdigest() != SOURCE_TREE_SHA256:
        raise ValueError('The extracted source differs from the pinned Python tree.')
    if not (root/'LICENSE').is_file():
        raise ValueError('Retain the upstream license with its source.')
    return {'python_files': len(paths), 'python_tree_sha256': digest.hexdigest()}


def extract_source(archive, output):
    """Copy only regular package files and LICENSE; ignore install hooks/data."""
    prefix = f'MedSAM-{SOURCE_COMMIT}/'
    with tarfile.open(archive, 'r:gz') as stream:
        for item in stream.getmembers():
            if not item.name.startswith(prefix):
                continue
            name = item.name[len(prefix):]
            relative = Path(name)
            if relative.is_absolute() or '..' in relative.parts:
                raise ValueError('Unsafe archive path.')
            if not (name.startswith('segment_anything/') or name == 'LICENSE') or item.isdir():
                continue
            if not item.isfile():
                raise ValueError('Only regular source files can be installed.')
            target = output/relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('xb') as dest, stream.extractfile(item) as source:
                shutil.copyfileobj(source, dest)
    return source_tree(output)


def prepare(command, destination):
    requested = Path(destination).absolute()
    if requested.is_symlink():
        raise ValueError('Destination cannot be a symlink.')
    destination = requested.resolve()
    code = Path(__file__).resolve().parents[1]
    if destination.is_relative_to(code) and not destination.is_relative_to(code/'data'/'models'):
        raise ValueError('Keep assets outside code, except ignored data/models.')
    if destination.is_relative_to(Path('/annotations')):
        raise ValueError('Do not write research assets to application annotations.')
    if command == 'verify':
        return verify_assets(destination/'medsam_vit_b.pth')
    if command == 'weights' and (destination/'medsam_vit_b.pth').exists():
        return verify_assets(destination/'medsam_vit_b.pth')
    if command == 'source' and destination.exists():
        return source_tree(destination)
    temporary_parent = destination.parent if command == 'source' else destination
    temporary_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.medsam-', dir=temporary_parent) as temporary:
        temporary = Path(temporary)
        if command == 'source':
            archive = temporary/'source.tar.gz'
            download(ARCHIVE_URL, archive, ARCHIVE_BYTES, ARCHIVE_SHA256)
            staged = temporary/'source'; staged.mkdir()
            result = extract_source(archive, staged)
            if destination.exists():
                raise FileExistsError('Source destination appeared; refusing replacement.')
            staged.rename(destination)
        else:
            staged = temporary/'medsam_vit_b.pth'
            download(WEIGHTS_URL, staged, CHECKPOINT_BYTES, CHECKPOINT_SHA256)
            result = verify_assets(staged)
            # Same filesystem: atomic publication, no replacement of a concurrent
            # file, and no partial checkpoint if interrupted during copying.
            os.link(staged, destination/staged.name)
            verify_assets(destination/staged.name)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('source', 'weights', 'verify'))
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(args.command, args.destination), indent=2))
    except (OSError, ValueError) as error:
        parser.exit(2, f'MedSAM preparation failed: {error}\n')


if __name__ == '__main__':
    main()
