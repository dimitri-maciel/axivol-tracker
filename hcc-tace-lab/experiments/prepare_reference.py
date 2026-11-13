"""Prepare a blinded native-grid annotation kit; never create an expert reference.

The reader receives CT and the original PRE Mass only. The frozen prediction
stays in a separate coordinator folder. Source pixels and app annotations are
read-only. Run with ``python -m experiments.prepare_reference --help``.
"""
import argparse
from datetime import datetime, timezone
import importlib.metadata
import json
from pathlib import Path
import re
import shutil
import uuid
from zipfile import ZipFile, ZIP_DEFLATED

import numpy as np
import SimpleITK as sitk

from hcclab.candidates import CandidateStore, file_sha256, stack_identity
from hcclab.geometry import GeometryError
from hcclab.registration import _image
from hcclab.volume import _check_stack
from .post_tracking import read_sources, verify_unchanged, write_json


TARGET = 'whole_treated_lesion'
CODE = Path(__file__).resolve().parents[1]


def geometry(image):
    """SimpleITK uses XYZ sizes/spacings and physical LPS coordinates."""
    return {'size_xyz': list(image.GetSize()), 'spacing_xyz_mm': list(image.GetSpacing()),
            'origin_lps_mm': list(image.GetOrigin()), 'direction_lps': list(image.GetDirection())}


def write_native(path, array, stack):
    """Lossless NRRD, with full read-back checks; no crop, window or resampling."""
    _check_stack(stack)
    if array.shape != (len(stack.slices), *stack.slices[0].shape) or not np.isfinite(array).all():
        raise GeometryError('Export array must be finite and match its original CT stack.')
    image = _image(array, stack)
    sitk.WriteImage(image, str(path), True)
    restored = sitk.ReadImage(str(path))
    if (geometry(restored) != geometry(image)
            or not np.array_equal(sitk.GetArrayViewFromImage(restored), array)):
        raise GeometryError('Export round-trip changed the native pixels or physical geometry.')
    return {'path': path.name, 'sha256': file_sha256(path), 'geometry': geometry(image)}


def verify_candidate(sources, document, mask):
    """Require the exact case, acquisition, published SEG and ordered CT bytes."""
    source = sources['provenance']
    selected = source['selection']
    baseline = document['baseline_source']
    if (document['status'] != 'unreviewed' or document['stage'] != 'POST'
            or document['case_id'] != source['case_id']
            or document['series_uid'] != selected['post_series_uid']
            or document['acquisition'] != selected['post_acquisition']
            or baseline['series_uid'] != selected['pre_series_uid']
            or baseline['acquisition'] != selected['pre_acquisition']
            or baseline['source_segment'] != selected['mass_segment']
            or baseline['source_segment_label'] != 'Mass'
            or baseline['source_seg_sha256'] != source['seg_sha256']):
        raise GeometryError('Frozen candidate does not match this case, acquisition and published PRE Mass.')
    for key, record in [('pre', baseline['source']), ('post', document['source'])]:
        stack = sources[key + '_stack']
        if (record['stack_identity'] != stack_identity(stack)
                or [s['sha256'] for s in record['slices']] != source[key]['ordered_ct_sha256']
                or [s['sop_uid'] for s in record['slices']] != [s.sop for s in stack.slices]):
            raise GeometryError('Frozen candidate refers to different source CT bytes or geometry.')
    if (mask.dtype != np.bool_ or mask.shape != sources['post_hu'].shape
            or not mask.any() or np.any(mask & ~sources['post_valid'])):
        raise GeometryError('Frozen candidate must be a nonempty native POST mask without padding.')


def new_folder(output, data, annotations, case):
    output = output.resolve()
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', case):
        raise ValueError('Use a simple catalog case identifier.')
    for protected in (data.resolve(), annotations.resolve(), CODE):
        if output == protected or protected in output.parents:
            raise ValueError('Save annotation kits outside data, annotations and the code tree.')
    output.mkdir(parents=True, exist_ok=True)
    package_id = 'reference-' + uuid.uuid4().hex
    folder = output / (case + '-' + package_id)
    folder.mkdir()
    return folder, package_id


def prepare(data, annotations, output, case, selection, candidate_id):
    """Return a ready local kit, or remove only this invocation's incomplete kit."""
    sources = read_sources(data, case, selection, False)
    store = CandidateStore(annotations)
    candidate_folder = store.folder(candidate_id)
    candidate_hashes = {name: file_sha256(candidate_folder / name) for name in ('metadata.json', 'mask.npz')}
    document, mask = store.metadata(candidate_id), store.mask(candidate_id)
    verify_candidate(sources, document, mask)
    folder, package_id = new_folder(output, data, annotations, case)
    try:
        reader, manager = folder / 'reader', folder / 'manager'
        reader.mkdir(); manager.mkdir()
        files = {}
        for filename, array, stack in (
                ('PRE_CT.nrrd', sources['pre_hu'], sources['pre_stack']),
                ('PRE_Mass.nrrd', sources['mass'].astype(np.uint8), sources['pre_stack']),
                ('POST_CT.nrrd', sources['post_hu'], sources['post_stack'])):
            files[filename] = write_native(reader / filename, array, stack)
        post = {'series_uid': selection['post_series_uid'], 'acquisition': selection['post_acquisition'],
                'geometry': files['POST_CT.nrrd']['geometry'], 'ct_sha256': files['POST_CT.nrrd']['sha256']}
        case_record = {
            'schema_version': 1, 'kind': 'independent_POST_annotation_inputs',
            'package_id': package_id, 'case_id': case, 'target_definition': TARGET,
            'created_utc': datetime.now(timezone.utc).isoformat(), 'post': post,
            'pre': {'series_uid': selection['pre_series_uid'], 'acquisition': selection['pre_acquisition'],
                    'segment_number': selection['mass_segment'], 'segment_label': 'Mass'},
            'files': files, 'sources': sources['provenance'],
            'contrast_phase_verified': False,
            'scope': 'Proposed whole treated lesion; expert must establish identity and delineability. No POST contour is supplied.',
        }
        write_json(reader / 'case.json', case_record)
        form = json.loads((CODE / 'docs/research/reader-form.example.json').read_text())
        form.update(case_id=case, package_id=package_id, post_series_uid=post['series_uid'],
                    post_acquisition=post['acquisition'], target_definition=TARGET,
                    reference_ct_sha256=post['ct_sha256'])
        write_json(reader / 'reader_form.json', form)
        shutil.copyfile(CODE / 'docs/research/annotation-kit.md', reader / 'README.md')
        shutil.copyfile(CODE / 'experiments/templates/open_in_slicer.py', reader / 'open_in_slicer.py')
        launcher = reader / 'Open_HCC_in_Slicer.command'
        shutil.copyfile(CODE / 'experiments/templates/Open_HCC_in_Slicer.command', launcher)
        launcher.chmod(0o755)
        candidate = write_native(manager / 'candidate.nrrd', mask.astype(np.uint8), sources['post_stack'])
        valid = write_native(manager / 'post_valid.nrrd', sources['post_valid'].astype(np.uint8), sources['post_stack'])
        shutil.copyfile(reader / 'case.json', manager / 'reader_case.json')
        write_json(manager / 'candidate.json', document)
        manifest = {
            'schema_version': 1, 'kind': 'independent_post_reference_evaluation',
            'package_id': package_id, 'case_id': case, 'target_definition': TARGET, 'post': post,
            'candidate': {'path': 'candidate.nrrd', 'sha256': candidate['sha256'], 'id': candidate_id},
            'validity': {'path': 'post_valid.nrrd', 'sha256': valid['sha256']},
            'reader_case_sha256': file_sha256(manager / 'reader_case.json'),
            'software': {name: importlib.metadata.version(name) for name in ('numpy', 'pydicom', 'SimpleITK')},
            'source_candidate_sha256': candidate_hashes,
            'implementation_sha256': file_sha256(Path(__file__)),
            'status': 'awaiting_independent_expert_reference',
        }
        write_json(manager / 'evaluation_manifest.json', manifest)
        verify_unchanged(sources)
        if candidate_hashes != {name: file_sha256(candidate_folder / name) for name in candidate_hashes}:
            raise GeometryError('The saved candidate changed while freezing the evaluation package.')
        # Only this explicit whitelist can enter the reader archive.
        reader_names = ['PRE_CT.nrrd', 'PRE_Mass.nrrd', 'POST_CT.nrrd', 'case.json',
                        'reader_form.json', 'README.md', 'open_in_slicer.py', 'Open_HCC_in_Slicer.command']
        if sorted(p.name for p in reader.iterdir()) != sorted(reader_names):
            raise ValueError('Unexpected artifact in the blinded reader package.')
        with ZipFile(folder / 'reader.zip', 'w', compression=ZIP_DEFLATED) as archive:
            for name in reader_names:
                archive.write(reader / name, 'reader/' + name)
        write_json(folder / 'preparation.json', {
            'status': 'prepared_not_anatomically_validated', 'package_id': package_id,
            'case_id': case, 'candidate_id': candidate_id,
            'checks': ['native pixel and geometry round-trip', 'original source hashes unchanged',
                       'candidate source match', 'reader archive whitelist'],
            'reader_archive_sha256': file_sha256(folder / 'reader.zip'),
            'expert_reference_available': False, 'accuracy_metrics_computed': False,
            'slicer_runtime_tested': False,
        })
        (folder / 'README.md').write_text(
            '# HCC POST · paquete para revisión independiente\n\n'
            'Estado: preparado; no hay todavía una referencia experta ni métricas de exactitud.\n\n'
            '- `reader/` y `reader.zip`: imágenes e instrucciones para quien anota. Sin predicción POST.\n'
            '- `manager/`: candidata congelada y procedencia. No mostrar al lector antes de anotar.\n'
            '- `preparation.json`: verificaciones de exportación; no evaluación anatómica.\n\n'
            'Si habrá dos lectores, entregar una copia independiente de `reader.zip` a cada uno. '
            'No compartir anotaciones entre lectores antes de completar sus versiones. '
            'Ningún archivo ha sido enviado a terceros.\n', encoding='utf-8')
        return folder
    except Exception:
        shutil.rmtree(folder)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, default=Path('/data'))
    parser.add_argument('--annotations', type=Path, default=Path('/annotations'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True, help='Source selection manifest used by post_tracking.')
    parser.add_argument('--case', required=True)
    parser.add_argument('--candidate-id', required=True)
    args = parser.parse_args()
    if args.manifest.stat().st_size > 65536:
        parser.error('Source selection manifest exceeds 64 KiB.')
    selection = json.loads(args.manifest.read_text())['cases'][args.case]
    folder = prepare(args.data, args.annotations, args.output, args.case, selection, args.candidate_id)
    print(json.dumps({'output': str(folder), 'status': 'awaiting_independent_expert_reference'}))


if __name__ == '__main__':
    main()
