"""Resumable download of public HCC-TACE-Seg series from the official NBIA API."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zipfile import BadZipFile, ZipFile

BASE = 'https://services.cancerimagingarchive.net/nbia-api/services/v1'
COLLECTION = 'HCC-TACE-Seg'
DOI = '10.7937/TCIA.5FNA-0924'
CLINICAL_URL = ('https://wiki.cancerimagingarchive.net/download/attachments/70230229/'
                'HCC-TACE-Seg_clinical_data-V2.xlsx')


def request(endpoint: str, **params):
    url = f'{BASE}/{endpoint}?{urlencode(params)}'
    return urlopen(Request(url, headers={'User-Agent': 'HCC-TACE-Lab/0.1 academic research'}), timeout=90)


def atomic_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', encoding='utf8', dir=path.parent, delete=False) as out:
        temp = Path(out.name)
        try:
            json.dump(data, out, ensure_ascii=False, indent=2)
            out.write('\n')
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
    temp.replace(path)


def validate_manifest(data):
    if not isinstance(data, list) or not data:
        raise ValueError('El catálogo de TCIA está vacío o no es una lista.')
    seen = set()
    for row in data:
        uid = row.get('SeriesInstanceUID', '')
        case = row.get('PatientID', '')
        if not re.fullmatch(r'\d+(\.\d+)+', uid) or not re.fullmatch(r'HCC_\d+', case):
            raise ValueError('El catálogo contiene identificadores inesperados.')
        if uid in seen or row.get('Collection', '').lower() != COLLECTION.lower():
            raise ValueError('El catálogo contiene una serie repetida o ajena a la colección.')
        if int(row['ImageCount']) < 1:
            raise ValueError('Conteo de instancias inválido.')
        seen.add(uid)
    return data


def manifest(data: Path, refresh=False):
    path = data/'catalog'/'tcia-series.json'
    if path.exists() and not refresh:
        return validate_manifest(json.loads(path.read_text()))
    with request('getSeries', Collection=COLLECTION) as response:
        rows = validate_manifest(json.load(response))
    atomic_json(path, rows)
    return rows


def series_dir(data: Path, row: dict) -> Path:
    return data/'dicom'/row['PatientID']/row['SeriesInstanceUID']


def complete(data: Path, row: dict) -> bool:
    dest = series_dir(data, row)
    try:
        mark = json.loads((dest/'complete.json').read_text())
        return (mark['series_uid'] == row['SeriesInstanceUID'] and
                mark['instances'] == int(row['ImageCount']) and
                sum(1 for f in dest.glob('*.dcm') if f.stat().st_size > 0) == mark['instances'])
    except (OSError, ValueError, KeyError):
        return False


def download(data: Path, row: dict) -> str:
    if complete(data, row):
        return 'exists'
    dest = series_dir(data, row)
    if dest.exists():
        raise ValueError(f'Directorio incompleto: {dest}. Revisar antes de reemplazar.')
    dest.parent.mkdir(parents=True, exist_ok=True)
    expected = int(row['ImageCount'])
    uid = row['SeriesInstanceUID']
    with tempfile.TemporaryDirectory(prefix='.tcia-', dir=dest.parent) as staging:
        stage = Path(staging)
        archive = stage/'series.zip'
        out = stage/'dicom'
        out.mkdir()
        digest = hashlib.sha256()
        with request('getImage', SeriesInstanceUID=uid) as response, archive.open('wb') as f:
            while chunk := response.read(1024*1024):
                digest.update(chunk)
                f.write(chunk)
        with ZipFile(archive) as z:
            files = [m for m in z.infolist() if not m.is_dir() and m.filename.lower().endswith('.dcm')]
            if len(files) != expected:
                raise ValueError(f'TCIA informó {expected} instancias, pero el ZIP contiene {len(files)} DICOM.')
            for i, member in enumerate(files, 1):
                # Assign trusted local names; never extract archive paths.
                with z.open(member) as source, (out/f'{i:06d}.dcm').open('wb') as target:
                    shutil.copyfileobj(source, target, length=1024*1024)
            licenses = [m for m in z.infolist() if 'license' in Path(m.filename).name.lower() and m.file_size < 1_000_000]
            if licenses:
                (out/'TCIA-license.txt').write_bytes(z.read(licenses[0]))
        atomic_json(out/'complete.json', {
            'source': BASE, 'collection': COLLECTION, 'doi': DOI, 'series_uid': uid,
            'instances': expected, 'archive_sha256': digest.hexdigest(),
            'downloaded_utc': datetime.now(timezone.utc).isoformat(),
        })
        out.rename(dest)
    return 'downloaded'


def retry_download(data, row):
    for attempt in range(3):
        try:
            return download(data, row)
        except (OSError, ValueError, RuntimeError, BadZipFile):
            if attempt == 2:
                raise
            time.sleep(2**attempt)


def fetch(data: Path, rows, workers: int) -> int:
    errors = []
    downloaded = reused = 0
    print(f'TCIA {COLLECTION} · {len(rows)} series · {sum(int(r.get("FileSize") or 0) for r in rows)/1e9:.2f} GB', flush=True)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = {pool.submit(retry_download, data, r): r for r in rows}
        for i, future in enumerate(as_completed(pending), 1):
            row = pending[future]
            try:
                if future.result() == 'downloaded':
                    downloaded += 1
                else:
                    reused += 1
            except Exception as e:
                errors.append({'series_uid': row['SeriesInstanceUID'], 'case': row['PatientID'], 'error': str(e)})
                print(f'Pendiente {row["PatientID"]}: {e}', flush=True)
            if i % 10 == 0 or i == len(rows):
                print(f'{i}/{len(rows)} · nuevas {downloaded} · reutilizadas {reused} · pendientes {len(errors)}', flush=True)
    atomic_json(data/'catalog'/'download-status.json', {
        'requested': len(rows), 'downloaded': downloaded, 'reused': reused, 'errors': errors,
        'updated_utc': datetime.now(timezone.utc).isoformat(),
    })
    return bool(errors)


def clinical(data: Path):
    target = data/'clinical'/'HCC-TACE-Seg_clinical_data-V2.xlsx'
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        with tempfile.TemporaryDirectory(prefix='.clinical-',dir=target.parent) as scratch:
            temp = Path(scratch)/target.name
            with urlopen(CLINICAL_URL,timeout=60) as response, temp.open('wb') as out:
                shutil.copyfileobj(response,out)
            with ZipFile(temp) as archive:
                if 'xl/workbook.xml' not in archive.namelist() or archive.testzip():
                    raise ValueError('El archivo de TCIA no es un XLSX íntegro.')
            temp.replace(target)
    atomic_json(target.with_suffix('.provenance.json'),{
        'source':CLINICAL_URL,'doi':DOI,'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),
    })
    print(f'Anexo clínico local: {target.name} · {target.stat().st_size} bytes')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['catalog', 'fetch', 'status', 'clinical'])
    p.add_argument('--data', type=Path, default=Path('/data'))
    p.add_argument('--case', action='append', help='Caso público, por ejemplo HCC_002. Repetible.')
    p.add_argument('--all', action='store_true', help='Descarga explícita de todas las series.')
    p.add_argument('--workers', type=int, choices=range(1, 7), default=3)
    p.add_argument('--refresh', action='store_true')
    a = p.parse_args()
    if a.command == 'clinical':
        clinical(a.data)
        return
    rows = manifest(a.data, a.refresh)
    if a.command == 'catalog':
        print(f'{len(rows)} series · {len({r["PatientID"] for r in rows})} casos · DOI {DOI}')
        return
    if a.command == 'status':
        done = [r for r in rows if complete(a.data, r)]
        print(f'{len(done)}/{len(rows)} series verificadas · {len({r["PatientID"] for r in done})} casos con datos locales')
        return
    if bool(a.case) == a.all:
        p.error('Para descargar, elige --case o --all.')
    selected = [r for r in rows if a.all or r['PatientID'] in a.case]
    if not selected or a.case and set(a.case) - {r['PatientID'] for r in selected}:
        p.error('El caso solicitado no está en el catálogo.')
    selected.sort(key=lambda r: (r['PatientID'], r['Modality'] != 'SEG', r['StudyDate'], r['SeriesInstanceUID']))
    raise SystemExit(fetch(a.data, selected, a.workers))


if __name__ == '__main__':
    main()
