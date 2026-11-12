"""Application operations shared by the HTTP interface and integration checks."""
from functools import lru_cache
from pathlib import Path

from .catalog import Catalog
from .dicom import default_segment, find_seg, frame_mask, mask_volume, render_slice, segment_options, stacks
from .geometry import GeometryError
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
    def __init__(self, data: Path):
        self.catalog = Catalog(data)

    def row(self, uid):
        row = self.catalog.by_uid.get(uid)
        if not row or row['Modality']!='CT' or not self.catalog.local(row):
            raise KeyError('Serie CT no disponible en el almacenamiento local.')
        return row

    def stack(self, uid, index):
        row = self.row(uid)
        options = stacks(self.catalog.folder(row),uid)
        if not 0<=index<len(options):
            raise KeyError('Adquisición no encontrada.')
        return row, options[index]

    def series(self, uid):
        row = self.row(uid)
        seg = find_seg(self.catalog,row)
        labels = segment_options(seg[1]) if seg else []
        return {'uid':uid,'description':row.get('SeriesDescription') or 'Serie sin descripción',
                'segments':labels,'default_segment':default_segment(labels),
                'stacks':[{'index':i,'acquisition':s.slices[0].acquisition,'slices':len(s.slices),
                           'spacing_mm':s.slices[0].spacing,'interval_mm':s.interval_mm,'issue':s.issue,
                           'orientation_labels':orientation_labels(s.slices[0])}
                          for i,s in enumerate(stacks(self.catalog.folder(row),uid))]}

    def image(self, uid, stack_index, slice_index, level, width, segment=None):
        row, st = self.stack(uid,stack_index)
        if not 0<=slice_index<len(st.slices):
            raise KeyError('Corte fuera de la adquisición.')
        s = st.slices[slice_index]
        mask = None
        if segment is not None:
            seg = find_seg(self.catalog,row)
            if not seg:
                raise GeometryError('No hay una máscara publicada para esta serie.')
            mask = frame_mask(*seg,segment,s)
        return render_slice(s,level=level,width=width,mask=mask)

    @lru_cache(maxsize=32)
    def measure(self, uid, stack_index, segment):
        row, st = self.stack(uid,stack_index)
        seg = find_seg(self.catalog,row)
        if not seg:
            raise GeometryError('Esta serie no tiene una máscara DICOM SEG publicada.')
        return mask_volume(*seg,segment,st)

    def report(self, case_id, pre_uid, pre_stack, post_uid, post_stack, segment=None):
        case = self.catalog.case(case_id)
        if not case['chronological_pair']:
            raise GeometryError('No se pudo determinar un par cronológico de estudios.')
        pre, post = case['studies']
        if pre_uid not in {s['uid'] for s in pre['series']} or post_uid not in {s['uid'] for s in post['series']}:
            raise GeometryError('Las series seleccionadas no pertenecen al par PRE/POST de este caso.')
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
        return {'collection':COLLECTION,'dataset_doi':DOI,'case':case_id,'studies':rows,
                'pre_segmentation_measurement':measured,'pre_measurement_issue':issue,
                'post_segmentation_measurement':None,'volume_change_percent':None,
                'status':'comparacion_pendiente',
                'pending':['Revisar fases y cobertura','Evaluar registro','Verificar correspondencia y máscara POST'],
                'scope':'Investigación: una medición de máscara publicada no es respuesta clínica.'}
