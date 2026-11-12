"""Download the pinned public research checkpoint separately from app images.

No patient data leave the machine. The URL and SHA-256 identify an upstream
Hugging Face revision, not an arbitrary user-supplied executable. Loading the
checkpoint is the inference module's responsibility (weights_only=True).
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import os
from urllib.request import urlopen
import uuid


SOURCE_COMMIT = '332f30d420f1d1b08e2a79b3ae6a602458808383'
CHECKPOINT_REVISION = 'e4a6f35edd7e091619cbc0750f462f1574e23955'
CHECKPOINT_FILENAME = 'eff_medsam2_tiny_FLARE25_RECIST_baseline.pt'
CHECKPOINT_SHA256 = '78cd67f41c1c2953026bd6e70a7648998079d08cb6ab0f75bbe289039695c103'
CHECKPOINT_BYTES = 71_662_031
CHECKPOINT_URL = f'https://huggingface.co/wanglab/MedSAM2/resolve/{CHECKPOINT_REVISION}/{CHECKPOINT_FILENAME}'
CONFIG_FILENAME = 'efficienttam_ti_512x512.yaml'
CONFIG_SHA256 = '55a0142a2d0076d3c434a8a35c23ccc16e7e42ef06153b571f147615befa070d'
CONFIG_BYTES = 3860
CONFIG_URL = f'https://raw.githubusercontent.com/bowang-lab/MedSAM2/{SOURCE_COMMIT}/efficient_track_anything/configs/{CONFIG_FILENAME}'


def model_path(data):
    return Path(data) / 'models' / CHECKPOINT_FILENAME


def verify_model(path):
    """Verify exact bytes before a model loader can deserialize any weights."""
    path = Path(path)
    if not path.is_file() or path.is_symlink():
        raise ValueError('The segmentation model is unavailable. Download the pinned model with the setup command.')
    if path.stat().st_size != CHECKPOINT_BYTES:
        raise ValueError('The segmentation model has an unexpected size. Check the model download.')
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    if digest.hexdigest() != CHECKPOINT_SHA256:
        raise ValueError('The segmentation model failed its SHA-256 integrity check.')
    return {'checkpoint': CHECKPOINT_FILENAME, 'checkpoint_revision': CHECKPOINT_REVISION,
            'checkpoint_sha256': CHECKPOINT_SHA256, 'source_commit': SOURCE_COMMIT,
            'checkpoint_bytes': CHECKPOINT_BYTES, 'configuration_sha256': CONFIG_SHA256}


def install_config():
    """Build-time workaround: upstream's wheel omits its required YAML file.

    Download exactly one configuration from the same pinned source commit.
    Its hash is verified before writing it beside the installed code. This
    operation runs during image construction, never during patient inference.
    """
    spec = importlib.util.find_spec('efficient_track_anything')
    if spec is None or not spec.submodule_search_locations:
        raise ValueError('Install the pinned MedSAM2 package before its configuration.')
    with urlopen(CONFIG_URL, timeout=30) as response:
        content = response.read(CONFIG_BYTES + 1)
    if len(content) != CONFIG_BYTES or hashlib.sha256(content).hexdigest() != CONFIG_SHA256:
        raise ValueError('The model configuration failed its pinned integrity check.')
    target = Path(spec.submodule_search_locations[0]) / 'configs' / CONFIG_FILENAME
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return {'configuration': CONFIG_FILENAME, 'configuration_sha256': CONFIG_SHA256}


def download_model(data):
    """Bounded atomic download; a pre-existing invalid file is never replaced."""
    destination = model_path(data)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() or destination.is_symlink():
        return verify_model(destination)
    temporary = destination.with_name('.' + destination.name + '.' + uuid.uuid4().hex + '.partial')
    try:
        total = 0
        with urlopen(CHECKPOINT_URL, timeout=30) as response, temporary.open('xb') as output:
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > CHECKPOINT_BYTES:
                    raise ValueError('The model download exceeds the pinned checkpoint size.')
                output.write(chunk)
        metadata = verify_model(temporary)
        temporary.chmod(0o644)  # The read-only app runs under a separate UID.
        # Publish without overwriting another download that finished first.
        os.link(temporary, destination)
        return metadata
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['fetch', 'verify', 'install-config'])
    parser.add_argument('--data', type=Path, default=Path('/data'))
    args = parser.parse_args()
    operation = (install_config() if args.command == 'install-config' else
                 download_model(args.data) if args.command == 'fetch' else verify_model(model_path(args.data)))
    print(json.dumps(operation, indent=2))


if __name__ == '__main__':
    main()
