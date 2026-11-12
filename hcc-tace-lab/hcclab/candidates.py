"""Immutable local candidates, stored separately from the read-only TCIA files.

Each directory contains a binary native-grid mask and its provenance. New runs
always get a new ID: adjusting a threshold never overwrites a previous result.
This store has no concept of an approved clinical reference segmentation.
"""
from functools import lru_cache
from io import BytesIO
from pathlib import Path
import hashlib
import json
import re
import shutil
import uuid
from zipfile import ZipFile, ZIP_STORED

import numpy as np

from .geometry import GeometryError


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def stack_identity(stack):
    """Hash the ordered source references and physical grid, not patient names."""
    grid = [{'sop': s.sop, 'acquisition': s.acquisition, 'frame': s.frame,
             'shape': s.shape, 'spacing': s.spacing, 'orientation': s.orientation,
             'position': s.position} for s in stack.slices]
    return hashlib.sha256(json.dumps(grid, sort_keys=True).encode()).hexdigest()


class CandidateStore:
    def __init__(self, root: Path):
        self.root = root

    def folder(self, candidate_id):
        if not isinstance(candidate_id, str) or not re.fullmatch(r'[a-f0-9]{32}', candidate_id):
            raise KeyError('Candidate not found.')
        folder = self.root / candidate_id
        if not folder.is_dir() or folder.is_symlink():
            raise KeyError('Candidate not found.')
        return folder

    def save(self, mask, metadata):
        self.root.mkdir(parents=True, exist_ok=True)
        candidate_id = uuid.uuid4().hex
        temporary = self.root / ('.tmp-' + candidate_id)
        temporary.mkdir(mode=0o700)
        try:
            np.savez_compressed(temporary / 'mask.npz', mask=mask)
            document = dict(metadata, id=candidate_id,
                            mask_sha256=file_sha256(temporary / 'mask.npz'))
            (temporary / 'metadata.json').write_text(
                json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
            temporary.rename(self.root / candidate_id)
        except Exception:
            shutil.rmtree(temporary)
            raise
        return document

    def metadata(self, candidate_id):
        document = json.loads((self.folder(candidate_id) / 'metadata.json').read_text())
        if document.get('id') != candidate_id or document.get('status') != 'unreviewed':
            raise GeometryError('Candidate metadata is inconsistent.')
        return document

    def list(self, case_id, series_uid, stack_index, *, source_segment=None):
        if not self.root.exists():
            return []
        documents = []
        for path in self.root.iterdir():
            if not re.fullmatch(r'[a-f0-9]{32}', path.name):
                continue
            document = self.metadata(path.name)
            if source_segment is not None and (document.get('stage') != 'PRE'
                                                or document.get('source_segment') != source_segment):
                continue
            if (document['case_id'], document['series_uid'], document['stack_index']) == (case_id, series_uid, stack_index):
                documents.append(document)
        return sorted(documents, key=lambda d: (d['created_utc'], d['id']), reverse=True)[:20]

    @lru_cache(maxsize=4)
    def mask(self, candidate_id):
        document = self.metadata(candidate_id)
        path = self.folder(candidate_id) / 'mask.npz'
        if file_sha256(path) != document['mask_sha256']:
            raise GeometryError('The saved candidate mask failed its integrity check.')
        with np.load(path, allow_pickle=False) as saved:
            mask = saved['mask']
        if (mask.dtype != np.bool_ or list(mask.shape) != document['shape_zyx']
                or int(np.count_nonzero(mask)) != document['voxels']):
            raise GeometryError('The saved candidate mask does not match its metadata.')
        mask.flags.writeable = False
        return mask

    def export(self, candidate_id):
        folder = self.folder(candidate_id)
        # Recheck the actual bytes even if the decoded mask is in the LRU cache.
        document = self.metadata(candidate_id)
        if file_sha256(folder / 'mask.npz') != document['mask_sha256']:
            raise GeometryError('The saved candidate mask failed its integrity check.')
        self.mask(candidate_id)
        result = BytesIO()
        with ZipFile(result, 'w', compression=ZIP_STORED) as archive:
            for name in ('mask.npz', 'metadata.json'):
                archive.write(folder / name, name)
        return result.getvalue()
