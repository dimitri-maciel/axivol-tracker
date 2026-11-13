"""Run inside 3D Slicer: load blinded inputs and export a manual POST draft.

This helper does not segment, register, smooth or validate anatomy. It requires
an empty imaging scene and never clears an existing scene. See the reader README.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import uuid

import numpy as np
import slicer
import vtk


_state = {}


def _sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _matrix(node):
    matrix = vtk.vtkMatrix4x4()
    node.GetIJKToRASMatrix(matrix)
    return np.array([[matrix.GetElement(i, j) for j in range(4)] for i in range(4)])


def _expected_matrix(case):
    # NRRD/ITK use LPS; Slicer displays RAS. Flip physical x/y, not voxel axes.
    grid = case['post']['geometry']
    result = np.eye(4)
    result[:3, :3] = np.diag([-1., -1., 1.]) @ np.asarray(grid['direction_lps']).reshape(3, 3) @ np.diag(grid['spacing_xyz_mm'])
    result[:3, 3] = np.asarray(grid['origin_lps_mm']) * [-1, -1, 1]
    return result


def _check_post(node, case):
    if (node.GetParentTransformNode() is not None
            or list(node.GetImageData().GetDimensions()) != case['post']['geometry']['size_xyz']
            or not np.allclose(_matrix(node), _expected_matrix(case), rtol=0, atol=1e-6)):
        raise ValueError('POST must retain the original full extent and physical geometry; no parent transforms.')


def _map_native_mask(mask, extent, segment_to_ras, post_to_ras, post_shape):
    """Place an internally cropped labelmap on POST by integer indexing only.

    VTK extents and matrices use IJK/XYZ; NumPy arrays use KJI/ZYX. A crop
    may change extent minima or shift the image origin by whole voxels.
    Neither permits a new spacing, rotation, fractional shift or lost paint.
    """
    mask = np.asarray(mask)
    extent = np.asarray(extent, dtype=int)
    post_shape = np.asarray(post_shape, dtype=int)
    if (mask.ndim != 3 or extent.shape != (6,) or post_shape.shape != (3,)
            or (post_shape <= 0).any() or not np.isfinite(mask).all()
            or tuple((extent[1::2] - extent[::2] + 1)[::-1]) != mask.shape):
        raise ValueError('The segment labelmap has invalid dimensions or extent.')
    segment_to_ras = np.asarray(segment_to_ras, dtype=float)
    post_to_ras = np.asarray(post_to_ras, dtype=float)
    if (segment_to_ras.shape != (4, 4) or post_to_ras.shape != (4, 4)
            or not np.isfinite(segment_to_ras).all() or not np.isfinite(post_to_ras).all()):
        raise ValueError('The segment geometry must contain finite 4 by 4 matrices.')
    try:
        delta = np.linalg.solve(post_to_ras, segment_to_ras)
    except np.linalg.LinAlgError as exc:
        raise ValueError('POST geometry is singular.') from exc
    offset_xyz = np.rint(delta[:3, 3])
    if (not np.allclose(delta[:3, :3], np.eye(3), rtol=0, atol=1e-6)
            or not np.allclose(delta[3], [0, 0, 0, 1], rtol=0, atol=1e-6)
            or not np.allclose(delta[:3, 3], offset_xyz, rtol=0, atol=1e-6)):
        raise ValueError('Segment spacing, orientation or origin changed; resampling is not allowed.')
    offset = (offset_xyz.astype(int) + extent[::2])[::-1]
    first = np.maximum(-offset, 0)
    last = np.minimum(mask.shape, post_shape - offset)
    mapped = np.zeros(tuple(post_shape), dtype=np.uint8)
    foreground = mask != 0
    if (last <= first).any():
        if foreground.any():
            raise ValueError('Segment contains foreground outside the native POST extent.')
        return mapped
    source = tuple(slice(a, b) for a, b in zip(first, last))
    if np.count_nonzero(foreground[source]) != np.count_nonzero(foreground):
        raise ValueError('Segment contains foreground outside the native POST extent.')
    destination = tuple(slice(a, b) for a, b in zip(first + offset, last + offset))
    mapped[destination] = foreground[source]
    return mapped


def _native_segment_mask(segmentation, segment_id, post):
    """Read a copy before export so Slicer cannot silently regrid the draft."""
    from vtk.util.numpy_support import vtk_to_numpy

    binary = slicer.vtkOrientedImageData()
    if not segmentation.GetBinaryLabelmapRepresentation(segment_id, binary):
        raise ValueError('A binary labelmap is required; no representation conversion was performed.')
    post_shape = tuple(reversed(post.GetImageData().GetDimensions()))
    if binary.GetNumberOfPoints() == 0:
        return np.zeros(post_shape, dtype=np.uint8)
    scalars = binary.GetPointData().GetScalars()
    if scalars is None or scalars.GetNumberOfComponents() != 1:
        raise ValueError('The draft must be a scalar binary labelmap.')
    mask = vtk_to_numpy(scalars).reshape(tuple(reversed(binary.GetDimensions())))
    matrix = vtk.vtkMatrix4x4()
    binary.GetImageToWorldMatrix(matrix)
    segment_to_ras = np.array([[matrix.GetElement(i, j) for j in range(4)] for i in range(4)])
    return _map_native_mask(mask, binary.GetExtent(), segment_to_ras, _matrix(post), post_shape)


def _check_export_pixels(array, expected):
    if (array.shape != expected.shape
            or np.count_nonzero(array) != np.count_nonzero(expected)
            or not np.array_equal(array, expected)):
        raise ValueError('Export changed the manual segment pixels; no resampling or clipping is allowed.')


def open_case():
    """Load this reader folder, without consulting any coordinator files."""
    for class_name in ('vtkMRMLVolumeNode', 'vtkMRMLSegmentationNode'):
        if slicer.mrmlScene.GetNumberOfNodesByClass(class_name):
            raise RuntimeError('Save your current work and open a new empty Slicer scene first. No scene was cleared.')
    folder = Path(__file__).resolve().parent
    case = json.loads((folder / 'case.json').read_text())
    for name in ('PRE_CT.nrrd', 'PRE_Mass.nrrd', 'POST_CT.nrrd'):
        if _sha(folder / name) != case['files'][name]['sha256']:
            raise ValueError('Input file integrity check failed: ' + name)
    pre = slicer.util.loadVolume(str(folder / 'PRE_CT.nrrd'), {'name': 'PRE CT - acquisition ' + case['pre']['acquisition'], 'show': False})
    post = slicer.util.loadVolume(str(folder / 'POST_CT.nrrd'), {'name': 'POST CT - acquisition ' + case['post']['acquisition'], 'show': False})
    _check_post(post, case)
    pre_label = slicer.util.loadLabelVolume(str(folder / 'PRE_Mass.nrrd'), {'name': 'Published PRE Mass', 'show': False})
    pre_seg = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLSegmentationNode', 'Published PRE Mass - PRE views only')
    pre_seg.CreateDefaultDisplayNodes()
    pre_seg.SetReferenceImageGeometryParameterFromVolumeNode(pre)
    if not slicer.modules.segmentations.logic().ImportLabelmapToSegmentationNode(pre_label, pre_seg):
        raise RuntimeError('Could not import the published PRE segment.')
    slicer.mrmlScene.RemoveNode(pre_label)
    pre_seg.GetDisplayNode().SetVisibility(False)
    post_seg = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLSegmentationNode', 'POST reference - manual draft')
    post_seg.CreateDefaultDisplayNodes()
    post_seg.SetReferenceImageGeometryParameterFromVolumeNode(post)
    segment_id = post_seg.GetSegmentation().AddEmptySegment('POST_reference', 'Whole treated lesion - draft', [0.1, 0.7, 0.8])
    # Explicit empty native labelmap: no model contour and no claim of absence.
    empty = np.zeros(tuple(reversed(post.GetImageData().GetDimensions())), dtype=np.uint8)
    slicer.util.updateSegmentBinaryLabelmapFromArray(empty, post_seg, segment_id, post)
    for volume in (pre, post):
        volume.GetDisplayNode().AutoWindowLevelOff()
        volume.GetDisplayNode().SetWindowLevel(400, 40)
    slicer.app.layoutManager().setLayout(slicer.vtkMRMLLayoutNode.SlicerLayoutFourUpView)
    slicer.util.setSliceViewerLayers(background=post, foreground=None, label=None)
    slicer.util.resetThreeDViews()
    slicer.util.resetSliceViews()
    slicer.util.selectModule('SegmentEditor')
    editor = slicer.modules.segmenteditor.widgetRepresentation().self().editor
    editor.setSegmentationNode(post_seg)
    editor.setSourceVolumeNode(post)
    editor.setCurrentSegmentID(segment_id)
    editor.setUndoEnabled(True)
    editor.setMaximumNumberOfUndoStates(10)
    _state.update(folder=folder, case=case, post=post, segmentation=post_seg, segment_id=segment_id,
                  pixels=hashlib.sha256(slicer.util.arrayFromVolume(post).tobytes()).hexdigest())
    print('Blinded inputs loaded. POST has an EMPTY manual draft. PRE is not registered to POST.')
    return post_seg


def save_reference(output_dir=None):
    """Export one full-grid binary draft and its form. No expert claims are filled."""
    if not _state:
        raise RuntimeError('Run open_case() first.')
    folder, case, post, segmentation = (_state[k] for k in ('folder', 'case', 'post', 'segmentation'))
    _check_post(post, case)
    if (segmentation.GetParentTransformNode() is not None
            or segmentation.GetSegmentation().GetNumberOfSegments() != 1
            or segmentation.GetSegmentation().GetSegment(_state['segment_id']) is None):
        raise ValueError('Keep exactly the original POST segment without a parent transform.')
    if (_sha(folder / 'POST_CT.nrrd') != case['post']['ct_sha256']
            or hashlib.sha256(slicer.util.arrayFromVolume(post).tobytes()).hexdigest() != _state['pixels']):
        raise ValueError('POST source pixels changed. Annotation cannot be attributed to the frozen CT.')
    expected = _native_segment_mask(segmentation, _state['segment_id'], post)
    ids = vtk.vtkStringArray(); ids.InsertNextValue(_state['segment_id'])
    label = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLLabelMapVolumeNode', 'POST reference export')
    output = Path(output_dir).expanduser().resolve() if output_dir else folder / ('return-' + uuid.uuid4().hex[:8])
    if output.exists():
        slicer.mrmlScene.RemoveNode(label)
        raise ValueError('Choose a new return folder; prior annotations are never overwritten.')
    created = False
    try:
        ok = slicer.modules.segmentations.logic().ExportSegmentsToLabelmapNode(
            segmentation, ids, label, post, slicer.vtkSegmentation.EXTENT_REFERENCE_GEOMETRY)
        if not ok:
            raise RuntimeError('Slicer could not export the POST draft.')
        _check_post(label, case)
        array = slicer.util.arrayFromVolume(label)
        if not np.isfinite(array).all() or not np.isin(array, [0, 1]).all():
            raise ValueError('Export must contain exactly background 0 and target 1.')
        _check_export_pixels(array, expected)
        output.mkdir(parents=True)
        created = True
        path = output / 'POST-label.nrrd'
        if not slicer.util.saveNode(label, str(path)):
            raise RuntimeError('Could not save the POST labelmap.')
        form = json.loads((folder / 'reader_form.json').read_text())
        form.update(annotation_file=path.name, annotation_sha256=_sha(path))
        (output / 'reader_form.json').write_text(json.dumps(form, ensure_ascii=False, indent=2) + '\n')
        (output / 'export.json').write_text(json.dumps({
            'status': 'manual_draft_pending_reader_attestation',
            'exported_utc': datetime.now(timezone.utc).isoformat(),
            'package_id': case['package_id'], 'slicer_version': slicer.app.applicationVersion,
            'helper_sha256': _sha(Path(__file__)),
            'geometry_check': 'Native voxel basis, integer crop offsets, in-bounds foreground and exact exported pixels verified.',
            'note': 'Export does not establish expertise, independence, identity or anatomical validity.'}, indent=2) + '\n')
    except Exception:
        if created:
            shutil.rmtree(output)
        raise
    finally:
        slicer.mrmlScene.RemoveNode(label)
    print('Manual draft saved to ' + str(output) + '. Complete reader_form.json truthfully before evaluation.')
    return str(output)


if __name__ == '__main__':
    open_case()
