"""Prueba funcional GUI de Slicer; el trazo NO es una referencia experta.

Ejecutar con Slicer --python-script /ruta/experiments/slicer_annotation_smoke.py.
AXIVOL_READER_DIR apunta solo al paquete ciego reader; AXIVOL_SLICER_QA_DIR
recibe una carpeta nueva por ejecución. AXIVOL_SLICER_QA_EXIT=1 cierra Slicer
al finalizar; por defecto deja la escena QA claramente identificada abierta.

Paint recibe eventos de ratón mediante la utilidad oficial clickAndDrag; este
script nunca escribe vóxeles del segmento para simular un trazo satisfactorio.
La escena inicial debe estar vacía. No se carga candidata, ejecuta modelo ni
calcula Dice, HD95 o volumen anatómico. Todos los campos del lector quedan null.

Fuentes API oficiales:
https://slicer.readthedocs.io/en/latest/developer_guide/slicer.html#slicer.util.clickAndDrag
https://github.com/Slicer/Slicer/blob/main/Modules/Loadable/Segmentations/Widgets/qMRMLSegmentEditorWidget.h
https://github.com/Slicer/Slicer/blob/main/Modules/Loadable/Segmentations/EditorEffects/qSlicerSegmentEditorPaintEffect.cxx
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import runpy
import time
import traceback
import uuid

import numpy as np
import qt
import SimpleITK as sitk
import slicer
import vtk


ATTESTATION_FIELDS = ('reader_id', 'qualifications', 'hepatic_imaging_expertise_attested',
                      'independently_drawn', 'model_output_seen', 'target_confirmed',
                      'lesion_identity_confirmed', 'evaluability', 'reviewed_utc')
MAX_SECONDS = 120


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def nrrd_geometry(path):
    reader = sitk.ImageFileReader()
    reader.SetFileName(str(path)); reader.ReadImageInformation()
    require(reader.GetDimension() == 3, 'Input CT is not three-dimensional.')
    return {'size_xyz': list(reader.GetSize()), 'spacing_xyz_mm': list(reader.GetSpacing()),
            'origin_lps_mm': list(reader.GetOrigin()), 'direction_lps': list(reader.GetDirection())}


def expected_ras(grid):
    result = np.eye(4)
    result[:3, :3] = np.diag([-1., -1., 1.]) @ np.asarray(grid['direction_lps']).reshape(3, 3) @ np.diag(grid['spacing_xyz_mm'])
    result[:3, 3] = np.asarray(grid['origin_lps_mm']) * [-1, -1, 1]
    return result


def volume_matrix(node):
    matrix = vtk.vtkMatrix4x4(); node.GetIJKToRASMatrix(matrix)
    return np.array([[matrix.GetElement(i, j) for j in range(4)] for i in range(4)])


def check_geometry(node, grid):
    require(node.GetParentTransformNode() is None, 'Unexpected volume parent transform.')
    require(list(node.GetImageData().GetDimensions()) == grid['size_xyz'], 'Loaded dimensions differ from the source NRRD.')
    require(np.allclose(volume_matrix(node), expected_ras(grid), rtol=0, atol=1e-6),
            'Loaded geometry differs from source LPS-to-RAS geometry.')


class AnnotationSmoke:
    """Etapas cooperativas QTimer: carga, eventos Paint, undo/redo y retorno."""
    def __init__(self):
        reader_value, output_value = os.environ.get('AXIVOL_READER_DIR'), os.environ.get('AXIVOL_SLICER_QA_DIR')
        require(reader_value and output_value, 'Set AXIVOL_READER_DIR and AXIVOL_SLICER_QA_DIR.')
        self.reader = Path(reader_value).expanduser().resolve()
        output_root = Path(output_value).expanduser().resolve()
        require(self.reader.is_dir(), 'Reader directory does not exist.')
        require(output_root != self.reader and self.reader not in output_root.parents,
                'QA output must be outside the immutable reader package.')
        self.output = output_root / ('run-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:8])
        self.output.mkdir(parents=True, exist_ok=False)
        self.started = time.monotonic()
        self.finished = False
        self.stage = 'GUI warmup'
        self.exit_when_completed = os.environ.get('AXIVOL_SLICER_QA_EXIT', '0').lower() in ('1', 'true', 'yes')
        self.input_hashes = {}
        self.report = {'status': 'running', 'kind': 'slicer_functional_annotation_smoke',
                       'scope': 'FUNCTIONAL TEST SCRIBBLE ONLY. Not an expert reference or anatomical evaluation.',
                       'candidate_loaded': False, 'model_executed': False, 'expert_reference_created': False,
                       'checks': [], 'screenshots': [], 'timings_seconds': {},
                       'slicer_version': str(slicer.app.applicationVersion),
                       'script_sha256': sha256(Path(__file__)),
                       'timeout_seconds': MAX_SECONDS}
        self.watchdog = qt.QTimer()
        self.watchdog.setInterval(1000)
        self.watchdog.connect('timeout()', self.check_timeout)
        self.watchdog.start()

    def check_timeout(self):
        if not self.finished and time.monotonic()-self.started > MAX_SECONDS:
            self.finish(False, 'Cooperative GUI timeout during '+self.stage)

    def schedule(self, callback, delay=200):
        qt.QTimer.singleShot(delay, lambda: self.run(callback))

    def run(self, callback):
        if self.finished:
            return
        self.check_timeout()
        if self.finished:
            return
        try:
            callback()
        except Exception as error:
            self.report['traceback'] = traceback.format_exc()
            self.finish(False, type(error).__name__+': '+str(error))

    def screenshot(self, name):
        path = self.output/name
        require(slicer.util.mainWindow().grab().save(str(path)), 'Could not save the Slicer GUI screenshot.')
        self.report['screenshots'].append({'file': name, 'sha256': sha256(path)})

    def warmup(self):
        window = slicer.util.mainWindow()
        if not window or not window.isVisible() or not slicer.app.layoutManager() or not hasattr(slicer.modules, 'segmenteditor'):
            self.schedule(self.warmup)
            return
        self.stage = 'load blinded inputs'
        self.schedule(self.load)

    def load(self):
        started = time.monotonic()
        names = ('PRE_CT.nrrd', 'PRE_Mass.nrrd', 'POST_CT.nrrd', 'case.json',
                 'reader_form.json', 'open_in_slicer.py', 'README.md')
        if (self.reader/'Open_HCC_in_Slicer.command').exists():
            names += ('Open_HCC_in_Slicer.command',)
        self.input_hashes = {name: sha256(self.reader/name) for name in names}
        self.original_form = json.loads((self.reader/'reader_form.json').read_text())
        require(all(self.original_form.get(key) is None for key in ATTESTATION_FIELDS),
                'QA requires an unattested form; never attach a functional scribble to a completed expert form.')
        # runpy's non-main run name loads functions without executing open_case twice.
        self.kit = runpy.run_path(str(self.reader/'open_in_slicer.py'))
        self.kit['open_case']()
        state = self.kit['_state']
        self.case, self.post, self.segmentation, self.segment_id = (state[key] for key in
                                                                  ('case', 'post', 'segmentation', 'segment_id'))
        volumes = slicer.util.getNodesByClass('vtkMRMLScalarVolumeNode')
        pre_volumes = [node for node in volumes if node != self.post]
        require(len(pre_volumes) == 1 and len(volumes) == 2, 'Expected only PRE and POST CT; no candidate may be loaded.')
        self.pre = pre_volumes[0]
        check_geometry(self.pre, nrrd_geometry(self.reader/'PRE_CT.nrrd'))
        check_geometry(self.post, self.case['post']['geometry'])
        segments = slicer.util.getNodesByClass('vtkMRMLSegmentationNode')
        prior_segments = [node for node in segments if node != self.segmentation]
        require(len(prior_segments) == 1 and len(segments) == 2, 'Unexpected segmentation in the blinded scene.')
        self.pre_segmentation = prior_segments[0]
        require(not self.pre_segmentation.GetDisplayNode().GetVisibility(), 'PRE overlay must remain hidden over POST.')
        require(not np.any(self.native_mask()), 'POST manual draft must start empty.')
        self.segmentation.SetName('QA FUNCTIONAL SCRIBBLE - NOT EXPERT REFERENCE')
        self.segmentation.GetSegmentation().GetSegment(self.segment_id).SetName('FUNCTIONAL TEST ONLY')
        slicer.util.mainWindow().setWindowTitle('Axivol functional QA - NOT EXPERT REFERENCE')
        self.report.update(case_id=self.case['case_id'], package_id=self.case['package_id'],
                           pre_acquisition=self.case['pre']['acquisition'], post_acquisition=self.case['post']['acquisition'],
                           pre_geometry=nrrd_geometry(self.reader/'PRE_CT.nrrd'), post_geometry=self.case['post']['geometry'])
        self.report['checks'].extend(['PRE and POST native geometry match the source NRRDs.',
                                      'Only blinded inputs loaded; PRE segmentation hidden; POST draft empty.'])
        self.report['timings_seconds']['load'] = time.monotonic()-started
        self.editor = slicer.modules.segmenteditor.widgetRepresentation().self().editor
        require(self.editor.undoEnabled, 'The reader helper must enable Undo; QA must not enable it itself.')
        require(self.editor.maximumNumberOfUndoStates >= 10, 'The reader helper must provide at least ten undo states.')
        self.report['undo_redo'] = {'enabled_by_reader_helper': True,
                                    'maximum_states': int(self.editor.maximumNumberOfUndoStates)}
        self.editor.setActiveEffectByName('Paint')
        effect = self.editor.activeEffect()
        require(effect is not None, 'Paint effect is unavailable.')
        effect.setCommonParameter('BrushDiameterIsRelative', '0')
        effect.setCommonParameter('BrushAbsoluteDiameter', '6')
        effect.setCommonParameter('BrushSphere', '0')
        self.slice_widget = slicer.app.layoutManager().sliceWidget('Red')
        self.slice_view = self.slice_widget.sliceView()
        center_ijk = (np.asarray(self.post.GetImageData().GetDimensions())-1)/2
        center_ijk[2] = round(center_ijk[2])
        self.center_ras = (volume_matrix(self.post) @ np.r_[center_ijk, 1])[:3]
        for name in ('Red', 'Yellow', 'Green'):
            slicer.app.layoutManager().sliceWidget(name).mrmlSliceNode().JumpSliceByCentering(*self.center_ras)
        self.stage = 'Paint view warmup'
        self.schedule(self.paint, 700)

    def native_mask(self):
        return self.kit['_native_segment_mask'](self.segmentation, self.segment_id, self.post)

    def paint(self):
        self.stage = 'GUI Paint events'
        dimensions = self.slice_widget.mrmlSliceNode().GetDimensions()
        if min(dimensions[:2]) < 50:
            self.schedule(self.paint)
            return
        self.screenshot('01-empty-draft.png')
        matrix = vtk.vtkMatrix4x4()
        vtk.vtkMatrix4x4.Invert(self.slice_widget.mrmlSliceNode().GetXYToRAS(), matrix)
        xy = np.asarray(matrix.MultiplyPoint([*self.center_ras, 1]))[:2]
        start = (int(round(xy[0]-8)), int(round(xy[1])))
        end = (int(round(xy[0]+8)), int(round(xy[1]+4)))
        require(all(1 <= point[axis] < dimensions[axis]-1 for point in (start, end) for axis in (0, 1)),
                'The functional stroke must be inside the rendered slice view.')
        self.slice_view.setFocus()
        interactor = self.slice_view.interactorStyle().GetInteractor()
        interactor.InvokeEvent(vtk.vtkCommand.EnterEvent)
        slicer.util.clickAndDrag(self.slice_view, button='None', start=start, end=start, steps=1)
        slicer.util.clickAndDrag(self.slice_view, button='Left', start=start, end=end, steps=12)
        self.report['paint'] = {'event_api': 'slicer.util.clickAndDrag / VTK interactor mouse events',
                                'start_xy': list(start), 'end_xy': list(end), 'steps': 12,
                                'absolute_diameter_mm': 6, 'sphere': False,
                                'purpose': 'Arbitrary geometric-center scribble, not an anatomical contour.'}
        self.schedule(self.check_paint, 300)

    def check_paint(self):
        self.painted = self.native_mask()
        require(np.count_nonzero(self.painted) > 0, 'GUI Paint did not change the empty draft; no array-write fallback is permitted.')
        self.report['paint']['changed_voxels'] = int(np.count_nonzero(self.painted))
        self.report['paint']['changed_slices_0based'] = np.flatnonzero(np.any(self.painted, axis=(1, 2))).tolist()
        self.report['checks'].append('A real GUI Paint effect produced a nonempty functional test scribble.')
        self.screenshot('02-painted-functional-scribble.png')
        self.stage = 'Undo'
        self.editor.undo()
        self.schedule(self.check_undo)

    def check_undo(self):
        require(not np.any(self.native_mask()), 'Undo did not restore the empty POST draft.')
        self.screenshot('03-after-undo.png')
        self.stage = 'Redo'
        self.editor.redo()
        self.schedule(self.check_redo)

    def check_redo(self):
        require(np.array_equal(self.native_mask(), self.painted), 'Redo did not restore the exact painted pixels.')
        self.report['undo_redo']['status'] = 'passed'
        self.report['checks'].append('Undo restored empty draft and redo restored the exact scribble pixels.')
        self.schedule(self.export)

    def export(self):
        self.stage = 'export and reopen functional draft'
        self.editor.setActiveEffectByName('')
        self.return_folder = Path(self.kit['save_reference'](str(self.output/'functional-test-return')))
        path = self.return_folder/'POST-label.nrrd'
        form = json.loads((self.return_folder/'reader_form.json').read_text())
        export_info = json.loads((self.return_folder/'export.json').read_text())
        require(all(form.get(key) is None for key in ATTESTATION_FIELDS), 'Export filled an expert claim on a functional test.')
        for key, value in self.original_form.items():
            if key not in ('annotation_file', 'annotation_sha256'):
                require(form.get(key) == value, 'Export changed the reader form identity or pending status.')
        require(form['annotation_file'] == path.name and form['annotation_sha256'] == sha256(path),
                'Exported annotation file and form hash disagree.')
        require(export_info['status'] == 'manual_draft_pending_reader_attestation', 'Export falsely marked the draft reviewed.')
        reloaded = slicer.util.loadLabelVolume(str(path), {'name': 'QA REOPENED - NOT EXPERT REFERENCE', 'show': False})
        try:
            check_geometry(reloaded, self.case['post']['geometry'])
            actual = slicer.util.arrayFromVolume(reloaded)
            require(actual.shape == self.painted.shape and np.array_equal(actual, self.painted),
                    'Saved/reopened labelmap differs from the GUI-painted native pixels.')
            require(np.isfinite(actual).all() and np.isin(actual, [0, 1]).all(), 'Reopened draft is not finite binary data.')
        finally:
            slicer.mrmlScene.RemoveNode(reloaded)
        self.report['export'] = {'folder': self.return_folder.name, 'annotation_sha256': sha256(path),
                                 'exact_pixels_after_reopen': True, 'native_geometry_after_reopen': True,
                                 'reader_claims_still_null': list(ATTESTATION_FIELDS)}
        (self.return_folder/'FUNCTIONAL_TEST_ONLY.txt').write_text(
            'FUNCTIONAL GUI TEST SCRIBBLE. NOT AN EXPERT REFERENCE.\n'
            'Created by automated mouse events solely to verify editing and export.\n'
            'Do not complete expert attestations or use this mask to score a model.\n')
        self.report['checks'].append('Export and reload preserve exact binary pixels/native geometry; expert fields remain null.')
        require(not self.pre_segmentation.GetDisplayNode().GetVisibility(), 'PRE overlay became visible over POST.')
        self.stage = 'final screenshot and integrity checks'
        self.schedule(self.complete, 300)

    def complete(self):
        self.screenshot('04-exported-reopened-verified.png')
        self.finish(True)

    def finish(self, success, error=None):
        if self.finished:
            return
        self.finished = True
        self.watchdog.stop()
        try:
            intact = all(sha256(self.reader/name) == digest for name, digest in self.input_hashes.items())
            require(intact, 'An original reader package file changed during QA.')
            self.report['original_files_unchanged'] = bool(self.input_hashes)
            if success:
                self.report['checks'].append('Original reader package bytes remain unchanged; no candidate was loaded or written.')
            else:
                try:
                    self.screenshot('failure.png')
                except Exception:
                    pass
        except Exception as integrity_error:
            success, error = False, str(integrity_error)
        self.report.update(status='passed' if success else 'failed', failed_stage=None if success else self.stage,
                           error=error, elapsed_seconds=time.monotonic()-self.started,
                           original_file_sha256=self.input_hashes,
                           finished_utc=datetime.now(timezone.utc).isoformat())
        (self.output/'validation.json').write_text(json.dumps(self.report, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
        print('AXIVOL_SLICER_QA '+json.dumps({'status': self.report['status'], 'output': str(self.output), 'error': error}), flush=True)
        if self.exit_when_completed:
            qt.QTimer.singleShot(300, lambda: slicer.app.exit(0 if success else 1))


# Retain the QObject timer/callback owner in the script globals until completion.
smoke = AnnotationSmoke()
smoke.schedule(smoke.warmup)
