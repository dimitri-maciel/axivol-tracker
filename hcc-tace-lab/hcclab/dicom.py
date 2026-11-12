"""DICOM adapter: local slices, referenced SEG frames and physically measured masks."""
from collections import defaultdict
from functools import lru_cache
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image
from pydicom import dcmread
from pydicom.pixels import apply_rescale, pixel_array

from .geometry import GeometryError, Slice, build_stack, native_volume_cm3


def floats(values):
    return tuple(float(v) for v in values)


@lru_cache(maxsize=32)
def stacks(folder: Path, uid: str):
    groups = defaultdict(list)
    for path in sorted(folder.glob('*.dcm')):
        ds = dcmread(path, stop_before_pixels=True)
        if ds.Modality!='CT' or str(ds.SeriesInstanceUID)!=uid or int(ds.get('NumberOfFrames',1))!=1:
            raise GeometryError('This series is not a single-frame DICOM CT stack.')
        s = Slice(path, str(ds.SOPInstanceUID), str(ds.get('AcquisitionNumber','Unspecified')),
                  str(ds.get('FrameOfReferenceUID','')), (int(ds.Rows),int(ds.Columns)), floats(ds.PixelSpacing),
                  floats(ds.ImageOrientationPatient), floats(ds.ImagePositionPatient))
        key = (s.acquisition, s.frame, s.shape, tuple(round(x,5) for x in s.spacing+s.orientation))
        groups[key].append(s)
    result = [build_stack(v) for v in groups.values()]
    if not result:
        raise GeometryError('No CT slices are available for this series.')
    return tuple(sorted(result,key=lambda v:(v.issue is not None,-len(v.slices),v.slices[0].acquisition)))


@lru_cache(maxsize=12)
def seg_header(path: Path):
    ds = dcmread(path, stop_before_pixels=True)
    if ds.Modality!='SEG' or ds.SegmentationType!='BINARY':
        raise GeometryError('Only binary DICOM SEG objects are supported.')
    return ds


def find_seg(catalog, row):
    for seg_row in catalog.by_case[row['PatientID']]:
        if seg_row['Modality']!='SEG' or not catalog.local(seg_row):
            continue
        for path in sorted(catalog.folder(seg_row).glob('*.dcm')):
            ds = seg_header(path)
            if any(str(x.SeriesInstanceUID)==row['SeriesInstanceUID'] for x in ds.get('ReferencedSeriesSequence',[])):
                return path, ds
    return None


def segment_options(seg):
    return [{'number':int(s.SegmentNumber),'label':str(s.SegmentLabel)} for s in seg.SegmentSequence]


def default_segment(options):
    return next((s['number'] for s in options if any(w in s['label'].lower() for w in ('mass','tumor','lesion'))), None)


def compatible_geometry(ds, frame, s):
    shared = ds.SharedFunctionalGroupsSequence[0]
    orient = frame.get('PlaneOrientationSequence') or shared.get('PlaneOrientationSequence')
    pixel = frame.get('PixelMeasuresSequence') or shared.get('PixelMeasuresSequence')
    position = frame.get('PlanePositionSequence')
    if not (orient and pixel and position):
        return False
    return (str(ds.get('FrameOfReferenceUID',''))==s.frame
            and (int(ds.Rows),int(ds.Columns))==s.shape
            and np.allclose(floats(orient[0].ImageOrientationPatient),s.orientation,atol=1e-4,rtol=0)
            and np.allclose(floats(pixel[0].PixelSpacing),s.spacing,atol=1e-4,rtol=0)
            and np.allclose(floats(position[0].ImagePositionPatient),s.position,atol=.01,rtol=0))


def references(frame):
    return {str(src.ReferencedSOPInstanceUID) for deriv in frame.get('DerivationImageSequence',[])
            for src in deriv.get('SourceImageSequence',[])}


def segment_frames(ds, segment: int):
    if segment not in {s['number'] for s in segment_options(ds)}:
        raise GeometryError('The selected segment is not present in the segmentation.')
    return [(i,frame) for i,frame in enumerate(ds.PerFrameFunctionalGroupsSequence)
            if int(frame.SegmentIdentificationSequence[0].ReferencedSegmentNumber)==segment]


def frame_mask(path, ds, segment, s):
    frames = [(i,f) for i,f in segment_frames(ds,segment) if s.sop in references(f) and compatible_geometry(ds,f,s)]
    if len(frames)>1:
        raise GeometryError('Multiple segmentation frames reference the same slice.')
    return pixel_array(path,index=frames[0][0]).astype(bool) if frames else None


def mask_volume(path, ds, segment: int, stack):
    if stack.interval_mm is None:
        raise GeometryError(stack.issue or 'Slice spacing could not be verified.')
    by_sop = {s.sop:s for s in stack.slices}
    used = set()
    count = 0
    frames = segment_frames(ds, segment)
    if not frames:
        raise GeometryError('The selected segment has no frames.')
    for idx, frame in frames:
        hits = [by_sop[uid] for uid in references(frame) & by_sop.keys() if compatible_geometry(ds,frame,by_sop[uid])]
        if len(hits)!=1 or hits[0].sop in used:
            raise GeometryError('Segmentation references do not match this acquisition. Select the referenced acquisition.')
        used.add(hits[0].sop)
        count += int(np.count_nonzero(pixel_array(path,index=idx)))
    label = next(s['label'] for s in segment_options(ds) if s['number']==segment)
    return {'label':label,'segment':segment,'voxels':count,
            'volume_cm3':native_volume_cm3(count,stack.slices[0].spacing,stack.interval_mm),
            'row_column_spacing_mm':stack.slices[0].spacing,'slice_interval_mm':stack.interval_mm,
            'matched_frames':len(used),'seg_sop_uid':str(ds.SOPInstanceUID),
            'source':'Published TCIA binary segmentation. Physical segment volume; not a measure of viability.'}


def render_slice(s, *, level=50, width=350, mask=None):
    ds = dcmread(s.path)
    hu = np.asarray(apply_rescale(pixel_array(ds),ds), dtype=np.float32)
    if hu.shape!=s.shape or not np.isfinite(hu).all():
        raise GeometryError('Pixel data does not match the slice dimensions.')
    window = np.rint(np.clip((hu-(level-.5))/(max(width,2)-1)+.5,0,1)*255).astype(np.uint8)
    if str(ds.get('PhotometricInterpretation',''))=='MONOCHROME1':
        window = 255-window
    im = Image.fromarray(window)
    if mask is not None:
        if mask.shape!=window.shape:
            raise GeometryError('Segmentation dimensions do not match the slice.')
        rgb = np.repeat(window[...,None],3,axis=2)
        rgb[mask] = np.rint(rgb[mask]*.55 + np.array([50,213,222])*.45).astype(np.uint8)
        im = Image.fromarray(rgb)
    out = BytesIO()
    im.save(out,format='PNG')
    return out.getvalue()
