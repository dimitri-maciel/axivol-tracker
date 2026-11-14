"""Observe pinned EfficientMedSAM2 alternatives without changing its decisions.

Use only in an isolated experimental process, never around a shared app model::

    predictor, _, _ = post_model._get_predictor(checkpoint)
    with capture_prompt_alternatives(predictor, valid_field=valid[:, y0:y1, x0:x1],
                                     keep_frames={53, 54, 55, 56}) as probe:
        mask, info = post_model.segment_post_model(..., prompt_mode='binary_reinjection')

``probe.records`` is JSON-serializable. ``probe.arrays`` contains field-native
logits (K,Y,X) for selected frames, never a second inference or a new mask.
The caller saves these with source/model/field-offset provenance. Predicted IoU
is a model score, not agreement measured against an independent reference.
"""
from contextlib import contextmanager
import inspect
from types import SimpleNamespace

import numpy as np
from scipy import ndimage


def _numpy(tensor):
    return tensor.detach().cpu().numpy()


def _describe(logits, valid):
    mask = (logits > 0) & valid
    coordinates = np.argwhere(mask)
    _, components = ndimage.label(mask, ndimage.generate_binary_structure(2, 1))
    return {'foreground_pixels': int(mask.sum()), 'components_4': int(components),
            'foreground_on_padding': int(((logits > 0) & ~valid).sum()),
            'centroid_yx_px': coordinates.mean(axis=0).tolist() if len(coordinates) else None}


@contextmanager
def capture_prompt_alternatives(predictor, *, valid_field=None, keep_frames=None):
    """Temporarily wrap two bound methods; restore exact prior attributes on exit.

    Each decoder call keeps native alternatives after upstream object-presence
    gating and before hole filling (which must be disabled). The original
    predictor result is returned unchanged. The chosen alternative must match
    both the returned selected logits and the real single-frame output exactly.
    A discrepancy raises rather than publishing an ambiguous explanation.
    """
    if getattr(predictor, 'fill_hole_area', None) != 0:
        raise ValueError('Probe requires the existing no-hole-filling configuration.')
    if valid_field is not None and (not isinstance(valid_field, np.ndarray)
            or valid_field.ndim != 3 or valid_field.dtype != np.bool_):
        raise ValueError('valid_field must be a native boolean ZYX array.')
    if keep_frames is not None:
        keep_frames = frozenset(keep_frames)
        if any(type(z) is not int or z < 0 for z in keep_frames):
            raise ValueError('keep_frames must contain nonnegative array indices.')
    names = ('_run_single_frame_inference', '_forward_sam_heads')
    originals = {name: getattr(predictor, name) for name in names}
    own_attributes = {name: predictor.__dict__.get(name) for name in names if name in predictor.__dict__}
    if any(getattr(method, '_axivol_prompt_probe', False) for method in originals.values()):
        raise ValueError('A prompt probe is already active on this predictor.')
    signatures = {name: inspect.signature(method) for name, method in originals.items()}
    settings = {key: getattr(predictor, key, None) for key in (
        'binarize_mask_from_pts_for_mem_enc', 'use_mask_input_as_output_without_sam',
        'multimask_output_for_tracking', 'multimask_output_in_sam',
        'multimask_min_pt_num', 'multimask_max_pt_num', 'num_maskmem',
        'memory_temporal_stride_for_eval', 'fill_hole_area')}
    probe = SimpleNamespace(records=[], arrays={}, settings=settings)
    active = None

    def heads(*args, **kwargs):
        output = originals['_forward_sam_heads'](*args, **kwargs)
        if active is None:
            return output
        bound = signatures['_forward_sam_heads'].bind(*args, **kwargs)
        bound.apply_defaults()
        low_alternatives, _, ious, low_selected, _, _, presence = output
        scores = _numpy(ious)
        if (scores.ndim != 2 or scores.shape[0] != 1 or not np.isfinite(scores).all()
                or low_alternatives.shape[:2] != scores.shape):
            raise ValueError('Probe supports one object with finite, compatible decoder alternatives.')
        state, z = active['state'], active['frame']
        native = []
        for k in range(scores.shape[1]):
            # Apply exactly the predictor's low-resolution -> native mapping,
            # individually, so alternatives cannot be mistaken for objects.
            _, resized = predictor._get_orig_video_res_output(state, low_alternatives[:, k:k+1])
            native.append(_numpy(resized)[0, 0].copy())
        native = np.stack(native).astype(np.float32, copy=False)
        if native.ndim != 3 or not np.isfinite(native).all():
            raise ValueError('Probe received incompatible native alternative logits.')
        selected_index = int(np.argmax(scores[0])) if bound.arguments['multimask_output'] else 0
        _, selected_resized = predictor._get_orig_video_res_output(state, low_selected)
        selected = _numpy(selected_resized)[0, 0]
        if not np.array_equal(native[selected_index], selected):
            raise ValueError('Decoder selection does not match the recorded alternative index.')
        if valid_field is None:
            valid = np.ones(native.shape[1:], dtype=bool)
        elif z >= len(valid_field) or valid_field.shape[1:] != native.shape[1:]:
            raise ValueError('Probe validity does not match native inference-field geometry.')
        else:
            valid = valid_field[z]
        call_id = len(probe.records)
        row = {'call_id': call_id, 'array_index': z, 'app_slice': z+1,
               'reverse': active['reverse'], 'phase': active['phase'],
               'multimask_output': bool(bound.arguments['multimask_output']),
               'predicted_iou': scores[0].astype(float).tolist(),
               'selected_alternative_index': selected_index,
               'object_score_logit': float(_numpy(presence).reshape(-1)[0]),
               'native_field_shape_yx': list(native.shape[1:]),
               'alternatives': [_describe(logits, valid) for logits in native],
               'selected_matches_real_frame_output': None,
               'score_scope': 'Model estimates, not independently measured IoU or calibrated anatomy confidence.'}
        if keep_frames is None or z in keep_frames:
            key = f'call_{call_id:04d}_z{z:03d}_alternatives'
            probe.arrays[key] = native
            row['array_key'] = key
        probe.records.append(row)
        active['calls'].append((row, selected.copy()))
        return output

    def frame(*args, **kwargs):
        nonlocal active
        bound = signatures['_run_single_frame_inference'].bind(*args, **kwargs)
        bound.apply_defaults()
        values = bound.arguments
        if active is not None:
            raise ValueError('Nested frame inference is not supported by this probe.')
        phase = ('binary_anchor_pointer' if values['mask_inputs'] is not None else
                 'box_or_point_prompt' if values['point_inputs'] is not None else 'propagation')
        active = {'state': values['inference_state'], 'frame': int(values['frame_idx']),
                  'reverse': bool(values['reverse']), 'phase': phase, 'calls': []}
        try:
            output = originals['_run_single_frame_inference'](*args, **kwargs)
            _, actual_resized = predictor._get_orig_video_res_output(values['inference_state'], output[1])
            actual = _numpy(actual_resized)[0, 0]
            for row, selected in active['calls']:
                # In direct binary-anchor mode, the decoder is only called for
                # its pointer: its mask is deliberately not the returned mask.
                if phase != 'binary_anchor_pointer':
                    matches = bool(np.array_equal(selected, actual))
                    row['selected_matches_real_frame_output'] = matches
                    if not matches:
                        raise ValueError('Recorded decoder choice differs from the actual frame output.')
                else:
                    row['selected_matches_real_frame_output'] = 'not_applicable_pointer_only'
            return output
        finally:
            active = None

    heads._axivol_prompt_probe = True
    frame._axivol_prompt_probe = True
    try:
        predictor._forward_sam_heads = heads
        predictor._run_single_frame_inference = frame
        yield probe
    finally:
        for name in names:
            if name in own_attributes:
                setattr(predictor, name, own_attributes[name])
            elif name in predictor.__dict__:
                delattr(predictor, name)
