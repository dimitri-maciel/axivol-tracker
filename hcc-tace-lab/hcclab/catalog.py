"""Read-only view of the TCIA manifest and the local DICOM collection."""
from collections import defaultdict
from pathlib import Path
import json

from .tcia import COLLECTION, DOI, series_dir, validate_manifest


class Catalog:
    def __init__(self, data: Path):
        self.data = data
        path = data/'catalog'/'tcia-series.json'
        self.rows = validate_manifest(json.loads(path.read_text())) if path.exists() else []
        self.by_uid = {r['SeriesInstanceUID']: r for r in self.rows}
        self.by_case = defaultdict(list)
        for r in self.rows:
            self.by_case[r['PatientID']].append(r)

    def local(self, row):
        return (series_dir(self.data, row)/'complete.json').is_file()

    def folder(self, row):
        return series_dir(self.data, row)

    def overview(self):
        cases, available = [], 0
        for name, rows in sorted(self.by_case.items()):
            local = [r for r in rows if self.local(r)]
            available += len(local)
            studies = {r['StudyInstanceUID'] for r in local if r['Modality']=='CT'}
            if local:
                cases.append({'id':name, 'series':len(local), 'expected':len(rows), 'studies':len(studies)})
        return {'collection':COLLECTION, 'doi':DOI, 'cases':cases,
                'series_local':available, 'series_total':len(self.rows), 'cases_total':len(self.by_case)}

    def case(self, name):
        rows = self.by_case.get(name)
        if rows is None:
            raise KeyError('Case not found.')
        groups = defaultdict(list)
        for row in rows:
            if row['Modality'] == 'CT':
                groups[row['StudyInstanceUID']].append(row)
        studies = []
        ordered = sorted(groups.items(), key=lambda pair: (pair[1][0].get('StudyDate',''), pair[0]))
        days = [series[0].get('StudyDate','')[:10] for _,series in ordered]
        chronological_pair = len(days)==2 and all(days) and days[0] < days[1]
        for i, (uid, series) in enumerate(ordered):
            items = [{'uid':r['SeriesInstanceUID'], 'description':r.get('SeriesDescription') or 'Unnamed series',
                      'instances':int(r['ImageCount']), 'local':self.local(r)} for r in series]
            studies.append({'uid':uid, 'date':days[i], 'stage':('PRE' if i==0 else 'POST') if chronological_pair else None,
                            'series':sorted(items,key=lambda r:(not r['local'],-r['instances'],r['uid']))})
        return {'id':name, 'studies':studies, 'chronological_pair':chronological_pair,
                'note':'Baseline and follow-up are assigned by study order. Contrast phases require review.'}
