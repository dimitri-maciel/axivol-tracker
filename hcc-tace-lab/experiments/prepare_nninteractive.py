"""Download or verify the pinned public nnInteractive assets, never load weights.

The caller explicitly chooses a destination. Complete verified directories are
reused; an existing incomplete or different directory is never repaired in place.
All five files are verified in a sibling staging directory before publication.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from urllib.request import urlopen

from .nninteractive_refine import ASSETS, MODEL_REVISION, verify_assets


MODEL_BASE = f'https://huggingface.co/MIC-DKFZ/nnInteractive/resolve/{MODEL_REVISION}/nnInteractive_v1.0'
BLOCK_BYTES = 1024*1024


def checked_destination(destination):
    requested = Path(destination).absolute()
    if requested.is_symlink():
        raise ValueError('The model destination must not be a symbolic link.')
    destination = requested.resolve()
    code = Path(__file__).resolve().parents[1]
    if destination.is_relative_to(code) and not destination.is_relative_to(code/'data'/'models'):
        raise ValueError('Model files must be outside the code tree, except ignored data/models.')
    if destination.is_relative_to(Path('/annotations')):
        raise ValueError('Do not install model files in application annotations.')
    return destination


def _download_file(name, destination):
    expected_size, expected_hash = ASSETS[name]
    digest, total = hashlib.sha256(), 0
    destination.parent.mkdir(parents=True, exist_ok=True)
    with urlopen(f'{MODEL_BASE}/{name}', timeout=60) as response, destination.open('xb') as output:
        while True:
            chunk = response.read(min(BLOCK_BYTES, expected_size-total+1))
            if not chunk:
                break
            total += len(chunk)
            if total > expected_size:
                raise ValueError(f'The download exceeds the pinned byte size: {name}.')
            digest.update(chunk)
            output.write(chunk)
    if total != expected_size or digest.hexdigest() != expected_hash:
        raise ValueError(f'The downloaded asset failed its pinned size or SHA-256 check: {name}.')
    destination.chmod(0o644)
    return {'url': f'{MODEL_BASE}/{name}', 'bytes': total, 'sha256': digest.hexdigest()}


def fetch_assets(destination):
    destination = checked_destination(destination)
    if destination.exists():
        return {'status': 'already_verified', **verify_assets(destination)}
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f'.{destination.name}.partial-', dir=destination.parent))
    try:
        downloads = {name: _download_file(name, temporary/name) for name in ASSETS}
        verified = verify_assets(temporary)
        for folder in [temporary, *(path for path in temporary.rglob('*') if path.is_dir())]:
            folder.chmod(0o755)
        # Both folders share a filesystem. Rename publishes the complete set;
        # a concurrent completed install is nonempty and cannot be overwritten.
        if destination.exists() or destination.is_symlink():
            raise FileExistsError('Model destination appeared during download; no existing files were replaced.')
        temporary.rename(destination)
        return {'status': 'downloaded_verified', **verified, 'downloads': downloads}
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('fetch', 'verify'))
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    try:
        destination = checked_destination(args.destination)
        result = fetch_assets(destination) if args.command == 'fetch' else verify_assets(destination)
    except (OSError, ValueError) as error:
        parser.exit(2, f'nnInteractive model setup failed: {error}\n')
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
