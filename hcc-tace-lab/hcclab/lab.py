"""Application operations shared by the HTTP interface and integration checks."""
from functools import lru_cache
from pathlib import Path
from io import BytesIO
from datetime import datetime, timezone
from threading import Lock
import importlib.metadata
import time
import numpy as np
from PIL import Image

from .candidates import CandidateStore, file_sha256, stack_identity
from .catalog import Catalog
from .dicom import default_segment, find_seg, frame_mask, mask_volume, referenced_mask, render_slice, segment_options, stacks, verify_pre_sources
from .geometry import GeometryError, native_volume_cm3
from .segmentation import grow_region
from .mask_editing import apply_strokes
from .volume import load_hu_volume
from .tcia import COLLECTION, DOI


def orientation_labels(s):
    def label(vector, sign):
        index = max(range(3), key=lambda i: abs(vector[i]))
        if abs(vector[index]) < .9:
            return ''
        return ('LPS' if vector[index]*sign>0 else 'RAI')[index]
    row, col = s.orientation[:3], s.orientation[3:]
    return {'a':label(col,-1),'p':label(col,1),'l':label(row,1),'r':label(row,-1)}


class Lab:
    def __init__(self, data: Path, annotations: Path = Path('/annotations')):
        self.catalog = Catalog(data)
        self.candidates = CandidateStore(annotations)
        self.segmentation_lock = Lock()

    def row(self, uid):
        row = self.catalog.by_uid.get(uid)
        if not row or row['Modality']!='CT' or not self.catalog.local(row):
            raise KeyError('This CT series is not available. Download the series before opening it.')
        return row

    def stack(self, uid, index):
        row = self.row(uid)
        options = stacks(self.catalog.folder(row),uid)
        if not 0<=index<len(options):
            raise KeyError('Acquisition not found.')
        return row, options[index]

    def series(self, uid):
        row = self.row(uid)
        seg = find_seg(self.catalog,row)
        labels = segment_options(seg[1]) if seg else []
        return {'uid':uid,'description':row.get('SeriesDescription') or 'Unnamed series',
                'segments':labels,'default_segment':default_segment(labels),
                'stacks':[{'index':i,'acquisition':s.slices[0].acquisition,'slices':len(s.slices),
                           'spacing_mm':s.slices[0].spacing,'interval_mm':s.interval_mm,'issue':s.issue,
                           'shape':s.slices[0].shape,
                           'orientation_labels':orientation_labels(s.slices[0])}
                          for i,s in enumerate(stacks(self.catalog.folder(row),uid))]}

    def image(self, uid, stack_index, slice_index, level, width, segment=None, candidate_id=None,
              mask_style='fill', mask_opacity=45):
        if mask_style not in ('fill', 'outline', 'combined') or type(mask_opacity) is not int or not 0 <= mask_opacity <= 100:
            raise ValueError('Invalid mask display style or opacity.')
        if candidate_id is not None:
            self.candidate_for_stack(candidate_id, uid, stack_index)
        return self._image(uid, stack_index, slice_index, level, width, segment, candidate_id, mask_style, mask_opacity)

    @lru_cache(maxsize=192)
    def _image(self, uid, stack_index, slice_index, level, width, segment=None, candidate_id=None,
               mask_style='fill', mask_opacity=45):
        row, st = self.stack(uid,stack_index)
        if not 0<=slice_index<len(st.slices):
            raise KeyError('Slice index is outside this acquisition.')
        s = st.slices[slice_index]
        mask = None
        if candidate_id is not None:
            if segment is not None:
                raise ValueError('Select either a published segment or a candidate overlay.')
            mask = self.candidates.mask(candidate_id)[slice_index]
        if segment is not None:
            seg = find_seg(self.catalog,row)
            if not seg:
                raise GeometryError('No published segmentation is available for this series.')
            mask = frame_mask(*seg,segment,s)
        return render_slice(s,level=level,width=width,mask=mask,mask_style=mask_style,mask_opacity=mask_opacity)

    def mask_image(self, uid, stack_index, slice_index, segment=None, candidate_id=None):
        """Binary PNG for the local editor; grayscale 255 is foreground."""
        row, stack = self.stack(uid, stack_index)
        if not 0 <= slice_index < len(stack.slices):
            raise ValueError('Slice index is outside this acquisition.')
        if candidate_id:
            if segment is not None:
                raise ValueError('Select a source segment or an edited copy, not both.')
            self.candidate_for_stack(candidate_id, uid, stack_index)
            mask = self.candidates.mask(candidate_id)[slice_index]
        else:
            seg = find_seg(self.catalog, row)
            if segment is None or seg is None:
                raise GeometryError('Select a published segment for editing.')
            mask = frame_mask(*seg, segment, stack.slices[slice_index])
            if mask is None:
                mask = np.zeros(stack.slices[slice_index].shape, dtype=bool)
        output = BytesIO()
        Image.fromarray(mask.astype(np.uint8) * 255).save(output, format='PNG')
        return output.getvalue()

    def pre_stack(self, case_id, uid, stack_index):
        case = self.catalog.case(case_id)
        if not case['chronological_pair'] or uid not in {s['uid'] for s in case['studies'][0]['series']}:
            raise GeometryError('Select a baseline CT series from this case.')
        if type(stack_index) is not int or stack_index < 0:
            raise ValueError('stack_index must be a non-negative integer.')
        return self.stack(uid, stack_index)

    def list_pre_edits(self, case_id, uid, stack_index, segment):
        self.pre_stack(case_id, uid, stack_index)
        return {'candidates': self.candidates.list(case_id, uid, stack_index, source_segment=segment)}

    def create_pre_edit(self, payload):
        required = {'case_id', 'series_uid', 'stack_index', 'segment', 'strokes'}
        if not isinstance(payload, dict) or set(payload) != required:
            raise ValueError('Provide case_id, series_uid, stack_index, segment and strokes.')
        case_id, uid, index, segment = (payload[k] for k in ('case_id', 'series_uid', 'stack_index', 'segment'))
        if not isinstance(case_id, str) or not isinstance(uid, str) or type(segment) is not int or segment < 1:
            raise ValueError('Invalid source identifiers or segment number.')
        row, stack = self.pre_stack(case_id, uid, index)
        if stack.interval_mm is None:
            raise GeometryError('Uniform slice spacing is required to measure an edited copy.')
        seg = find_seg(self.catalog, row)
        if not seg:
            raise GeometryError('No published segmentation is available for this acquisition.')
        if not payload['strokes']:
            raise ValueError('Make a correction before saving an edited copy.')
        if not self.segmentation_lock.acquire(blocking=False):
            raise ValueError('A mask operation is running. Wait for it to finish.')
        try:
            source_hashes = tuple(file_sha256(s.path) for s in stack.slices)
            seg_hash = file_sha256(seg[0])
            seg = verify_pre_sources(stack, seg, expected_series_uid=uid,
                                     expected_study_uid=row['StudyInstanceUID'])
            original = referenced_mask(*seg, segment, stack)
            edited, diagnostics = apply_strokes(original, stack.slices[0].spacing, payload['strokes'])
            if diagnostics['added_voxels'] + diagnostics['removed_voxels'] == 0:
                raise ValueError('The strokes do not change the published mask. Adjust the correction before saving.')
            if seg_hash != file_sha256(seg[0]) or source_hashes != tuple(file_sha256(s.path) for s in stack.slices):
                raise GeometryError('Source files changed during editing. Reload the acquisition.')
            label = next(item['label'] for item in segment_options(seg[1]) if item['number'] == segment)
            first = stack.slices[0]
            metadata = {
                'schema_version': 1, 'status': 'unreviewed', 'stage': 'PRE',
                'created_utc': datetime.now(timezone.utc).isoformat(), 'case_id': case_id,
                'series_uid': uid, 'stack_index': index, 'acquisition': first.acquisition,
                'label': 'Edited ' + label, 'source_segment': segment, 'source_segment_label': label,
                'source_seg_sop_uid': str(seg[1].SOPInstanceUID), 'source_seg_sha256': seg_hash,
                'target': 'User-edited copy of a published segment; changes require expert review',
                'parameters': {'strokes': payload['strokes']}, 'shape_zyx': list(edited.shape),
                'voxels': diagnostics['voxels'],
                'volume_cm3': native_volume_cm3(diagnostics['voxels'], first.spacing, stack.interval_mm),
                'original_voxels': int(original.sum()), 'diagnostics': diagnostics,
                'method': {'name': 'manual_brush_edit', 'version': '1.0.0', 'resampling': False,
                           'brush_units': 'mm', 'point_order': 'y,x',
                           'implementation_sha256': {'mask_editing.py': file_sha256(Path(__file__).with_name('mask_editing.py'))}},
                'source': {'collection': COLLECTION, 'dataset_doi': DOI, 'study_uid': row['StudyInstanceUID'],
                           'frame_of_reference_uid': first.frame, 'stack_identity': stack_identity(stack),
                           'axis_order': 'z,y,x', 'row_column_spacing_mm': first.spacing,
                           'slice_interval_mm': stack.interval_mm, 'orientation_patient': first.orientation,
                           'slices': [{'index': i, 'sop_uid': s.sop, 'position_patient_mm': s.position,
                                       'sha256': source_hashes[i]} for i, s in enumerate(stack.slices)]},
                'software': {name: importlib.metadata.version(name) for name in ('numpy', 'pydicom')},
                'warnings': ['Edited copy. Changes require expert review. The published mask is preserved.'],
                'scope': 'Manual PRE correction candidate; not a replacement clinical reference or treatment-response measurement.',
            }
            return self.candidates.save(edited, metadata)
        finally:
            self.segmentation_lock.release()

    def post_stack(self, case_id, uid, stack_index):
        """Chronology is necessary to label POST, but does not verify phase."""
        case = self.catalog.case(case_id)
        if not case['chronological_pair']:
            raise GeometryError('A distinct baseline and follow-up study is required.')
        if uid not in {s['uid'] for s in case['studies'][1]['series']}:
            raise GeometryError('Select a follow-up CT series from this case.')
        if type(stack_index) is not int or stack_index < 0:
            raise ValueError('stack_index must be a non-negative integer.')
        return self.stack(uid, stack_index)

    @lru_cache(maxsize=1)
    def hu_volume(self, uid, stack_index, source_hashes):
        # Only one decoded acquisition is kept; frame browsing uses its own cache.
        # Source hashes are part of the key so changed bytes cannot reuse old HU.
        row, stack = self.stack(uid, stack_index)
        return load_hu_volume(stack, return_validity=True, expected_series_uid=uid,
                              expected_study_uid=row['StudyInstanceUID'])

    def candidate_for_stack(self, candidate_id, uid, stack_index):
        document = self.candidates.metadata(candidate_id)
        if document['series_uid'] != uid or document['stack_index'] != stack_index:
            raise GeometryError('This candidate belongs to a different acquisition.')
        stage = document.get('stage', 'POST')
        if stage not in ('PRE', 'POST'):
            raise GeometryError('Candidate stage is invalid.')
        operation = self.pre_stack if stage == 'PRE' else self.post_stack
        row, stack = operation(document['case_id'], uid, stack_index)
        if document['source']['stack_identity'] != stack_identity(stack):
            raise GeometryError('The source geometry or slice references have changed.')
        # Cheap file stats invalidate the hash check without rehashing 97+ CT
        # files for every scroll event. Source files are read-only in the app.
        version = tuple((s.path.stat().st_size, s.path.stat().st_mtime_ns, s.path.stat().st_ino)
                        for s in stack.slices)
        if stage == 'PRE':
            seg = find_seg(self.catalog, row)
            if seg is None or str(seg[1].SOPInstanceUID) != document['source_seg_sop_uid']:
                raise GeometryError('The published source segmentation has changed.')
            stat = seg[0].stat()
            version += ((stat.st_size, stat.st_mtime_ns, stat.st_ino),)
        self.verify_candidate_sources(candidate_id, version)
        return document

    @lru_cache(maxsize=16)
    def verify_candidate_sources(self, candidate_id, source_version):
        document = self.candidates.metadata(candidate_id)
        row, stack = self.stack(document['series_uid'], document['stack_index'])
        current = tuple(file_sha256(s.path) for s in stack.slices)
        if current != tuple(s['sha256'] for s in document['source']['slices']):
            raise GeometryError('Source CT files differ from those used for this candidate. Generate a new candidate.')
        if document.get('stage') == 'PRE':
            seg = find_seg(self.catalog, row)
            if seg is None or file_sha256(seg[0]) != document['source_seg_sha256']:
                raise GeometryError('The published source segmentation differs from the edited copy source.')

    def voxel(self, uid, stack_index, z, y, x):
        """Expose one calibrated intensity to help choose explicit HU limits."""
        _, stack = self.stack(uid, stack_index)
        shape = (len(stack.slices), *stack.slices[0].shape)
        if any(not 0 <= point < size for point, size in zip((z, y, x), shape)):
            raise ValueError('Seed coordinates are outside this acquisition.')
        if not self.segmentation_lock.acquire(blocking=False):
            raise ValueError('An image operation is running. Try the seed again when it finishes.')
        try:
            source_hashes = tuple(file_sha256(s.path) for s in stack.slices)
            hu, valid = self.hu_volume(uid, stack_index, source_hashes)
            return {'hu': float(hu[z, y, x]), 'valid': bool(valid[z, y, x])}
        finally:
            self.segmentation_lock.release()

    def list_candidates(self, case_id, uid, stack_index):
        self.post_stack(case_id, uid, stack_index)
        return {'candidates': self.candidates.list(case_id, uid, stack_index)}

    def create_candidate(self, payload):
        required = {'case_id', 'series_uid', 'stack_index', 'seed', 'roi', 'lower_hu', 'upper_hu'}
        if not isinstance(payload, dict) or set(payload) != required:
            raise ValueError('Provide case_id, series_uid, stack_index, seed, roi, lower_hu and upper_hu.')
        case_id, uid, index = payload['case_id'], payload['series_uid'], payload['stack_index']
        if not isinstance(case_id, str) or not isinstance(uid, str):
            raise ValueError('Case and series identifiers must be strings.')
        row, stack = self.post_stack(case_id, uid, index)
        # A bounded single job avoids decoding several full CT volumes at once.
        if not self.segmentation_lock.acquire(blocking=False):
            raise ValueError('A candidate is being generated. Wait for it to finish.')
        started = time.perf_counter()
        try:
            source_hashes = tuple(file_sha256(s.path) for s in stack.slices)
            hu, valid = self.hu_volume(uid, index, source_hashes)
            mask, diagnostics = grow_region(hu, payload['seed'], payload['roi'],
                                            payload['lower_hu'], payload['upper_hu'], valid_mask=valid)
            warnings = ['Candidate only. Review the boundary on every included slice.',
                        'Contrast phase and lesion correspondence have not been verified.']
            if diagnostics['roi_touch_faces']:
                warnings.append('The mask reaches the region boundary (' + ', '.join(diagnostics['roi_touch_faces'])
                                + '). Check for clipping or leakage before expanding the region.')
            first = stack.slices[0]
            if source_hashes != tuple(file_sha256(s.path) for s in stack.slices):
                raise GeometryError('Source files changed during segmentation. Reload the acquisition.')
            parameters = {key: payload[key] for key in ('seed', 'roi', 'lower_hu', 'upper_hu')}
            metadata = {
                'schema_version': 1, 'status': 'unreviewed', 'stage': 'POST',
                'created_utc': datetime.now(timezone.utc).isoformat(),
                'case_id': case_id, 'series_uid': uid, 'stack_index': index,
                'acquisition': first.acquisition, 'label': 'Region-growing candidate',
                'target': 'User-selected connected region; anatomical target unverified',
                'parameters': parameters, 'shape_zyx': list(mask.shape),
                'voxels': diagnostics['voxels'],
                'volume_cm3': native_volume_cm3(diagnostics['voxels'], first.spacing, stack.interval_mm),
                'diagnostics': diagnostics, 'warnings': warnings,
                'method': {'name': 'seeded_connected_threshold', 'version': '1.0.0', 'connectivity': 6,
                           'resampling': False, 'uses_pre_mask': False, 'padding_excluded': True,
                           'implementation_sha256': {name: file_sha256(Path(__file__).with_name(name))
                                                     for name in ('segmentation.py', 'volume.py')}},
                'source': {'collection': COLLECTION, 'dataset_doi': DOI,
                           'study_uid': row['StudyInstanceUID'], 'frame_of_reference_uid': first.frame,
                           'stack_identity': stack_identity(stack), 'axis_order': 'z,y,x',
                           'row_column_spacing_mm': first.spacing, 'slice_interval_mm': stack.interval_mm,
                           'orientation_patient': first.orientation,
                           'intensity_units': 'HU',
                           'intensity_rule': 'Per-slice rescale slope/intercept; explicit HU or DICOM classic ORIGINAL CT conditional HU rule; pixel padding excluded.',
                           'slices': [{'index': i, 'sop_uid': s.sop, 'position_patient_mm': s.position,
                                       'sha256': source_hashes[i]} for i, s in enumerate(stack.slices)]},
                'software': {name: importlib.metadata.version(name) for name in ('numpy', 'scipy', 'pydicom')},
                'elapsed_seconds': round(time.perf_counter() - started, 4),
                'scope': 'Unreviewed connected region in native POST geometry. Not a clinical reference or treatment-response measurement.',
            }
            return self.candidates.save(mask, metadata)
        finally:
            self.segmentation_lock.release()

    @lru_cache(maxsize=32)
    def measure(self, uid, stack_index, segment):
        row, st = self.stack(uid,stack_index)
        seg = find_seg(self.catalog,row)
        if not seg:
            raise GeometryError('No published DICOM SEG is available for this series.')
        return mask_volume(*seg,segment,st)

    def report(self, case_id, pre_uid, pre_stack, post_uid, post_stack, segment=None, candidate_id=None, pre_candidate_id=None):
        case = self.catalog.case(case_id)
        if not case['chronological_pair']:
            raise GeometryError('A baseline and follow-up pair could not be established.')
        pre, post = case['studies']
        if pre_uid not in {s['uid'] for s in pre['series']} or post_uid not in {s['uid'] for s in post['series']}:
            raise GeometryError('The selected series do not belong to this case in baseline and follow-up order.')
        rows = []
        for stage, uid, idx in [('PRE',pre_uid,pre_stack),('POST',post_uid,post_stack)]:
            row, st = self.stack(uid,idx)
            rows.append({'stage':stage,'series_uid':uid,'study_uid':row['StudyInstanceUID'],
                         'description':row.get('SeriesDescription'), 'acquisition':st.slices[0].acquisition,
                         'slice_count':len(st.slices), 'spacing_mm':st.slices[0].spacing,
                         'interval_mm':st.interval_mm,'geometry_issue':st.issue,'contrast_phase_verified':False})
        measured, issue = None, None
        if segment is not None:
            try:
                measured = self.measure(pre_uid,pre_stack,segment)
            except GeometryError as error:
                issue = str(error)
        candidate = self.candidate_for_stack(candidate_id, post_uid, post_stack) if candidate_id else None
        pre_candidate = self.candidate_for_stack(pre_candidate_id, pre_uid, pre_stack) if pre_candidate_id else None
        if pre_candidate and pre_candidate['source_segment'] != segment:
            raise GeometryError('The edited copy belongs to a different baseline segment.')
        return {'collection':COLLECTION,'dataset_doi':DOI,'case':case_id,'studies':rows,
                'pre_segmentation_measurement':measured,'pre_measurement_issue':issue,
                'post_segmentation_measurement':None,'volume_change_percent':None,
                'post_candidate':candidate,
                'pre_edited_candidate':pre_candidate,
                'status':'comparison_pending',
                'pending':['Review contrast phases and coverage','Evaluate registration','Verify lesion correspondence and follow-up segmentation'],
                'scope':'Development build: published segmentation volume does not establish treatment response.'}
