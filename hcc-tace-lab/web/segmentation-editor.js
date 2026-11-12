// Coordinates belong to the selected follow-up acquisition, never the baseline.
// The UI uses 1-based inclusive fields; the API uses [z,y,x], 0-based, max-exclusive ROI.
const $ = id => document.getElementById('candidate-' + id);
const integer = new Intl.NumberFormat('en-US');
const decimal = new Intl.NumberFormat('en-US', {maximumFractionDigits: 2});
const fields = ['first', 'last', 'lower', 'upper', 'x0', 'x1', 'y0', 'y1', 'seed-x', 'seed-y', 'seed-z'];
const isAutomatic = item => item?.method?.name === 'registered_post_segmentation';
const candidateLabel = item => isAutomatic(item) ? 'Automatic candidate · Unreviewed' : 'Manual candidate · Unreviewed';
const baselineKey = item => item ? [item.series_uid, item.stack_index, item.source_segment].join('|') : '';

export function createSegmentationEditor(adapter) {
  const viewport = document.getElementById('post-viewport');
  const guides = $('guides');
  let source = null, candidate = null, saved = [], mode = null, drag = null;
  let operation = 0, revision = 0, busy = false, jobKind = null;
  let baselineIdentity = '', baselineEpoch = 0;
  let attempt = null, attemptUrl = null;
  let pendingRestore = null;
  let seedRequest = 0, seedKey = '', seedValue = null, seedBusy = false;
  const value = name => $(name).value === '' ? NaN : Number($(name).value);
  const key = item => item && [item.case_id, item.series_uid, item.stack_index].join('|');
  const sourceMatches = item => key(item) === key(source);
  const selectable = item => sourceMatches(item) && (!isAutomatic(item)
    || baselineKey(item.baseline_source) === baselineKey(adapter.baselineSource()));

  function baselineChanged() {
    const next = baselineKey(adapter.baselineSource());
    if (next === baselineIdentity) return;
    baselineIdentity = next;
    baselineEpoch++;
    clearAttempt();
    // Manual POST candidates have no PRE dependency. Only detach an automatic
    // result when its published baseline reference changes.
    if (isAutomatic(candidate)) {
      const hadOverlay = $('overlay').checked;
      candidate = null;
      renderCandidate();
      if (hadOverlay) adapter.refreshImage();
      message('Baseline reference changed. Select a matching saved candidate or generate again.');
    }
    if (busy && jobKind === 'automatic') message('Baseline reference changed. This generation will not be applied.');
    updateSaved();
    sync();
    restoreInitialCandidate();
  }

  function restoreInitialCandidate() {
    if (!pendingRestore || pendingRestore.operation !== operation || pendingRestore.revision !== revision
      || mode || busy || candidate) return;
    // If the newest saved mask depends on PRE, wait for that source before
    // falling back to an older manual mask. PRE and POST load independently.
    if (isAutomatic(saved.find(sourceMatches)) && !adapter.baselineSource()) return;
    const first = saved.find(selectable);
    if (first) restore(first);
  }

  function automaticAvailable() {
    const baseline = adapter.baselineSource();
    return !!source?.shape && adapter.ready() && baseline?.ready && baseline.case_id === source.case_id
      && baseline.source_segment_label.trim().toLowerCase() === 'mass'
      && !baseline.issue && !!baseline.interval_mm && !source.issue && !!source.interval_mm;
  }

  function clearAttempt() {
    attempt = null;
    if (attemptUrl) URL.revokeObjectURL(attemptUrl);
    attemptUrl = null;
    $('attempt').hidden = true;
    $('attempt-export').removeAttribute('href');
    document.getElementById('registration-status').textContent = isAutomatic(candidate) ? 'Unverified' : 'Pending';
  }

  function showAttempt(request, response, baseline, acquisition) {
    clearAttempt();
    attempt = {created_utc: new Date().toISOString(), request, response,
      scope: 'No automatic candidate was created. Registration remains unverified.'};
    $('attempt').hidden = false;
    $('attempt-title').textContent = response.status === 'abstained' ? 'Automatic segmentation needs review' : 'Automatic generation failed';
    $('attempt-reason').textContent = response.error;
    $('attempt-baseline').textContent = baseline.source_segment_label + ' (' + baseline.source_segment
      + ') · Acquisition ' + baseline.acquisition;
    $('attempt-pre-uid').textContent = request.pre_series_uid;
    $('attempt-post').textContent = acquisition;
    $('attempt-post-uid').textContent = request.series_uid;
    const registration = response.diagnostics?.registration?.status === 'estimated_unverified' ? 'Unverified' : 'Not verified';
    $('attempt-registration').textContent = registration;
    document.getElementById('registration-status').textContent = registration;
    attemptUrl = URL.createObjectURL(new Blob([JSON.stringify(attempt, null, 2)], {type: 'application/json'}));
    $('attempt-export').href = attemptUrl;
    $('attempt-export').download = 'Axivol_POST_run_' + request.case_id + '_' + Date.now() + '.json';
  }

  function message(text, error = false) {
    $('message').textContent = text;
    $('message').classList.toggle('error', error);
  }

  function parameters() {
    return {
      seed: [value('seed-z') - 1, value('seed-y') - 1, value('seed-x') - 1],
      roi: [[value('first') - 1, value('y0') - 1, value('x0') - 1],
        [value('last'), value('y1'), value('x1')]],
      lower_hu: value('lower'), upper_hu: value('upper'),
    };
  }

  function validation() {
    if (!source?.shape?.every(n => Number.isInteger(n) && n > 0)) return 'Image geometry is unavailable for this acquisition.';
    if (source.issue || !source.interval_mm) return source.issue || 'Slice spacing must be verified before generating a mask.';
    const {seed, roi, lower_hu, upper_hu} = parameters();
    const bounds = [source.slices, ...source.shape];
    if (![...seed, ...roi.flat()].every(Number.isInteger)) return 'Choose a region, seed and slice range.';
    if (roi[0].some((n, i) => n < 0 || n >= roi[1][i] || roi[1][i] > bounds[i])) return 'Keep the region and slice range within this acquisition.';
    if (roi[1].reduce((size, n, i) => size * (n - roi[0][i]), 1) > 4000000) return 'Region exceeds 4,000,000 voxels. Draw a smaller region or reduce the slice range.';
    if (seed.some((n, i) => n < roi[0][i] || n >= roi[1][i])) return 'Place the seed inside the region and slice range.';
    if (seedValue?.valid === false) return 'The seed is outside valid image data. Pick another pixel.';
    if (!Number.isFinite(lower_hu) || !Number.isFinite(upper_hu)) return 'Enter both HU limits.';
    if (lower_hu > upper_hu) return 'Lower HU must not exceed upper HU.';
    return '';
  }

  async function readSeed() {
    const {seed} = parameters();
    const identity = key(source) + '|' + seed.join(',');
    if (identity === seedKey) return;
    seedKey = identity;
    seedValue = null;
    seedBusy = false;
    const token = ++seedRequest, sourceToken = operation;
    const bounds = source && [source.slices, ...source.shape || []];
    if (!bounds || !seed.every((n, i) => Number.isInteger(n) && n >= 0 && n < bounds[i])) {
      $('seed-hu').textContent = 'Pick a seed to inspect its HU value.';
      return;
    }
    seedBusy = true;
    $('seed-hu').textContent = 'Reading seed intensity…';
    sync();
    const query = new URLSearchParams({stack: source.stack_index, index: seed[0], row: seed[1], column: seed[2]});
    try {
      const response = await fetch('/api/series/' + encodeURIComponent(source.series_uid) + '/voxel?' + query);
      const payload = await response.json();
      if (token !== seedRequest || sourceToken !== operation) return;
      if (!response.ok) throw new Error(payload.error || 'Seed intensity unavailable.');
      seedValue = payload;
      $('seed-hu').textContent = payload.valid ? 'Seed intensity: ' + decimal.format(payload.hu) + ' HU'
        : 'Seed is outside valid image data. Pick another pixel.';
      sync();
    } catch {
      if (token === seedRequest && sourceToken === operation) {
        seedKey = '';
        $('seed-hu').textContent = 'Seed intensity unavailable. Select the seed again to retry.';
      }
    } finally {
      if (token === seedRequest && sourceToken === operation) { seedBusy = false; sync(); }
    }
  }

  function sync() {
    const available = !!source?.shape && adapter.ready();
    for (const name of ['region-tool', 'seed-tool']) $(name).disabled = !available;
    for (const name of fields) $(name).disabled = !source?.shape;
    $('generate').disabled = busy || seedBusy || !available || !!validation();
    $('generate').textContent = busy && jobKind === 'manual' ? 'Generating…' : seedBusy ? 'Reading seed…' : 'Generate candidate';
    $('automatic').disabled = busy || seedBusy || !!drag || !automaticAvailable();
    $('automatic').textContent = busy && jobKind === 'automatic' ? 'Generating…' : 'Generate automatically';
    $('automatic').classList.toggle('is-busy', busy && jobKind === 'automatic');
    const baseline = adapter.baselineSource();
    $('baseline').textContent = baseline ? 'Published ' + baseline.source_segment_label + ' (' + baseline.source_segment
      + ') · Acquisition ' + baseline.acquisition : 'Select a published Mass segmentation.';
    $('baseline-series').textContent = baseline?.description || '—';
    $('baseline-uid').textContent = baseline?.series_uid || '—';
    $('automatic-note').textContent = baseline?.source_segment_label.trim().toLowerCase() === 'mass'
      ? 'Uses the published baseline mask to locate the target. Segments follow-up CT.'
      : 'Select the published Mass structure in Baseline.';
    $('editor').setAttribute('aria-busy', String(busy));
    for (const name of ['region', 'seed']) $(name + '-tool').setAttribute('aria-pressed', String(mode === name));
    viewport.classList.toggle('selecting', !!mode);
    $('tool-hint').hidden = !mode;
    $('tool-hint').textContent = mode === 'region'
      ? 'Drag a region · 100% zoom · Esc to cancel' : 'Click a seed inside the region · Esc to cancel';
    // Selection tools use an untransformed image. Pan and zoom resume on exit.
    if (mode) {
      document.getElementById('post-zoom-in').disabled = true;
      document.getElementById('post-zoom-out').disabled = true;
    }
  }

  function setMode(next) {
    if (next) pendingRestore = null;
    drag = null;
    mode = next;
    if (mode) {
      adapter.resetZoom();
      viewport.scrollIntoView({block: 'nearest'});
      viewport.focus({preventScroll: true});
    } else adapter.updateTransform();
    sync();
    drawGuides();
  }

  function renderCandidate() {
    $('result').hidden = !candidate;
    $('empty').hidden = !!candidate;
    $('status').textContent = candidate ? candidateLabel(candidate) : 'No candidate';
    document.getElementById('followup-segmentation-status').textContent = candidate ? candidateLabel(candidate) : 'Unavailable';
    document.getElementById('registration-status').textContent = isAutomatic(candidate)
      || attempt?.response.diagnostics?.registration?.status === 'estimated_unverified' ? 'Unverified' : 'Pending';
    $('automatic-result').hidden = !isAutomatic(candidate);
    $('export').removeAttribute('href');
    const warnings = candidate?.warnings || [];
    $('warnings').replaceChildren(...warnings.map(text => {
      const boundaries = {z_min: 'first slice', z_max: 'last slice', y_min: 'top', y_max: 'bottom', x_min: 'left', x_max: 'right'};
      const item = document.createElement('li');
      item.textContent = String(text).replace(/\b(z_min|z_max|y_min|y_max|x_min|x_max)\b/g, name => boundaries[name]);
      return item;
    }));
    $('warnings').hidden = warnings.length === 0;
    if (candidate) {
      $('volume').textContent = decimal.format(candidate.volume_cm3);
      $('voxels').textContent = integer.format(candidate.voxels);
      $('source').textContent = candidate.case_id + ' · Acquisition ' + candidate.acquisition + ' · ' + candidate.label;
      $('export').href = '/api/candidates/' + encodeURIComponent(candidate.id) + '/export';
      if (isAutomatic(candidate)) {
        const baseline = candidate.baseline_source;
        $('reference-used').textContent = baseline.source_segment_label + ' (' + baseline.source_segment
          + ') · Acquisition ' + baseline.acquisition;
        $('reference-uid').textContent = baseline.series_uid;
      }
    }
    adapter.changed();
  }

  function changed() {
    pendingRestore = null;
    revision++;
    const hadOverlay = !!candidate && $('overlay').checked;
    candidate = null;
    $('saved').value = '';
    renderCandidate();
    const {roi, seed} = parameters();
    const regionReady = roi.flat().every(Number.isInteger);
    const seedReady = seed.every(Number.isInteger);
    $('selection').textContent = [
      regionReady ? 'Region: ' + (roi[1][2] - roi[0][2]) + ' × ' + (roi[1][1] - roi[0][1]) + ' pixels' : 'Draw a region',
      seedReady ? 'Seed on slice ' + (seed[0] + 1) : 'Pick a seed inside it',
    ].join('. ') + '.';
    message(busy ? 'Parameters changed. Generate again when the current request finishes.'
      : validation() || 'Ready to generate. The result will be an unreviewed candidate.');
    if (hadOverlay) adapter.refreshImage();
    readSeed();
    sync();
    drawGuides();
  }

  function restore(item) {
    if (!selectable(item)) return;
    pendingRestore = null;
    revision++;
    if (isAutomatic(item)) {
      // An automatic candidate is not a manual ROI/seed recipe. Keep the
      // user's existing manual fields, and render only its actual POST mask.
      candidate = item;
      $('saved').value = item.id;
      $('overlay').checked = true;
      $('selection').textContent = 'Manual settings are separate from the automatic mask.';
      setMode(null);
      renderCandidate();
      message('Automatic candidate loaded. Registration and the mask require review.');
      adapter.focusSlice(item.diagnostics.segmentation.display_slice_index);
      return;
    }
    const {seed, roi, lower_hu, upper_hu} = item.parameters;
    for (const [name, n] of Object.entries({first: roi[0][0] + 1, last: roi[1][0],
      y0: roi[0][1] + 1, y1: roi[1][1], x0: roi[0][2] + 1, x1: roi[1][2],
      'seed-z': seed[0] + 1, 'seed-y': seed[1] + 1, 'seed-x': seed[2] + 1, lower: lower_hu, upper: upper_hu})) $(name).value = n;
    candidate = item;
    $('saved').value = item.id;
    $('overlay').checked = true;
    $('selection').textContent = 'Saved region and seed restored. Seed on slice ' + (seed[0] + 1) + '.';
    setMode(null);
    renderCandidate();
    message('Candidate saved. Inspect it across slices; adjust parameters to generate another.');
    adapter.refreshImage();
    readSeed();
  }

  function updateSaved() {
    const available = saved.filter(selectable);
    const placeholder = new Option(available.length ? 'Choose a candidate' : 'No saved candidates', '');
    placeholder.disabled = true;
    placeholder.selected = !candidate;
    $('saved').replaceChildren(placeholder,
      ...available.map(item => new Option((isAutomatic(item) ? 'Automatic' : 'Manual') + ' · '
        + decimal.format(item.volume_cm3) + ' cm³ · ' + item.id.slice(0, 8), item.id)));
    $('saved').disabled = !available.length;
    if (candidate) $('saved').value = candidate.id;
  }

  async function readSaved(autoRestore = false) {
    if (!source) return;
    const token = operation, draft = revision;
    const query = new URLSearchParams({case: source.case_id, series: source.series_uid, stack: source.stack_index});
    try {
      const response = await fetch('/api/candidates?' + query);
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || 'Saved candidates could not be loaded.');
      if (token !== operation) return;
      const incoming = payload.candidates.filter(sourceMatches);
      saved = [...saved, ...incoming.filter(item => !saved.some(local => local.id === item.id))];
      saved.sort((a, b) => (b.created_utc || '').localeCompare(a.created_utc || ''));
      updateSaved();
      if (autoRestore && revision === draft) restoreInitialCandidate();
    } catch (error) {
      if (token === operation && revision === draft) message(error.message, true);
    }
  }

  function setSource(next) {
    operation++;
    revision++;
    pendingRestore = {operation, revision};
    source = next;
    candidate = null;
    saved = [];
    busy = false;
    jobKind = null;
    clearAttempt();
    seedRequest++;
    seedKey = '';
    seedValue = null;
    seedBusy = false;
    $('seed-hu').textContent = 'Pick a seed to inspect its HU value.';
    for (const name of fields) $(name).value = '';
    if (source) {
      $('first').value = 1;
      $('last').value = source.slices;
      for (const name of ['first', 'last', 'seed-z']) $(name).max = source.slices;
      for (const name of ['y0', 'y1', 'seed-y']) $(name).max = source.shape?.[0] || 1;
      for (const name of ['x0', 'x1', 'seed-x']) $(name).max = source.shape?.[1] || 1;
    }
    $('selection').textContent = 'Draw a region on the follow-up image, then pick a seed inside it.';
    document.getElementById('post-overlay-label').hidden = true;
    setMode(null);
    renderCandidate();
    updateSaved();
    message(source ? 'Choose a region, seed and HU range to begin.' : 'Select a follow-up acquisition to begin.');
    if (source) readSaved(true);
  }

  // object-fit: contain leaves letterboxing; do not interpret it as image pixels.
  function geometry() {
    if (!source?.shape) return null;
    const [rows, columns] = source.shape;
    const width = viewport.clientWidth, height = viewport.clientHeight;
    const scale = Math.min(width / columns, height / rows);
    return {width, height, scale, left: (width - columns * scale) / 2, top: (height - rows * scale) / 2, rows, columns};
  }

  function pixel(event, clamp = false) {
    const g = geometry();
    if (!g) return null;
    const box = viewport.getBoundingClientRect();
    let x = Math.floor((event.clientX - box.left - g.left) / g.scale);
    let y = Math.floor((event.clientY - box.top - g.top) / g.scale);
    if (!clamp && (x < 0 || y < 0 || x >= g.columns || y >= g.rows)) return null;
    return [Math.max(0, Math.min(g.rows - 1, y)), Math.max(0, Math.min(g.columns - 1, x))];
  }

  function drawGuides() {
    guides.replaceChildren();
    const g = geometry(), z = adapter.displayedIndex();
    if (!g || !adapter.ready() || !Number.isInteger(z) || isAutomatic(candidate)) return;
    const {roi, seed} = parameters(), transform = adapter.transform();
    guides.setAttribute('viewBox', '0 0 ' + g.width + ' ' + g.height);
    const point = (x, y) => [g.width / 2 + (g.left + x * g.scale - g.width / 2) * transform.zoom + transform.x,
      g.height / 2 + (g.top + y * g.scale - g.height / 2) * transform.zoom + transform.y];
    const shape = (tag, attrs) => {
      const el = document.createElementNS('http://www.w3.org/2000/svg', tag);
      for (const [name, val] of Object.entries(attrs)) el.setAttribute(name, val);
      guides.append(el);
    };
    const draft = drag && [Math.min(drag.start[0], drag.end[0]), Math.min(drag.start[1], drag.end[1]),
      Math.max(drag.start[0], drag.end[0]) + 1, Math.max(drag.start[1], drag.end[1]) + 1];
    const region = draft || [roi[0][1], roi[0][2], roi[1][1], roi[1][2]];
    if (region.every(Number.isInteger) && (draft || (z >= roi[0][0] && z < roi[1][0]))) {
      const [x, y] = point(region[1], region[0]), [right, bottom] = point(region[3], region[2]);
      shape('rect', {x, y, width: Math.max(0, right - x), height: Math.max(0, bottom - y), class: 'region-guide'});
    }
    if (seed.every(Number.isInteger) && seed[0] === z) {
      const [cx, cy] = point(seed[2] + .5, seed[1] + .5);
      shape('circle', {cx, cy, r: 5, class: 'seed-guide'});
      shape('path', {d: 'M' + (cx - 10) + ' ' + cy + 'h20 M' + cx + ' ' + (cy - 10) + 'v20', class: 'seed-guide'});
    }
  }

  for (const name of fields) $(name).addEventListener('input', changed);
  for (const name of ['region', 'seed']) $(name + '-tool').addEventListener('click', () => setMode(mode === name ? null : name));
  $('saved').addEventListener('change', () => {
    const item = saved.find(item => item.id === $('saved').value);
    if (item) restore(item);
  });
  $('overlay').addEventListener('change', () => { adapter.changed(); adapter.refreshImage(); });
  viewport.addEventListener('keydown', event => {
    if (event.key === 'Escape' && mode) { event.preventDefault(); setMode(null); }
  });
  viewport.addEventListener('pointerdown', event => {
    if (!mode || event.button !== 0 || !adapter.ready()) return;
    const position = pixel(event), z = adapter.displayedIndex();
    if (!position || !Number.isInteger(z)) return;
    event.preventDefault();
    viewport.focus({preventScroll: true});
    if (mode === 'seed') {
      $('seed-z').value = z + 1;
      $('seed-y').value = position[0] + 1;
      $('seed-x').value = position[1] + 1;
      changed();
      setMode(null);
    } else {
      drag = {start: position, end: position, slice: z};
      viewport.setPointerCapture(event.pointerId);
      drawGuides();
    }
  });
  viewport.addEventListener('pointermove', event => {
    if (!drag) return;
    drag.end = pixel(event, true);
    drawGuides();
  });
  viewport.addEventListener('pointerup', () => {
    if (!drag) return;
    const item = drag;
    drag = null;
    // A slice change during a drag invalidates the gesture rather than moving its geometry.
    if (!adapter.ready() || item.slice !== adapter.displayedIndex()) { setMode(null); return; }
    $('y0').value = Math.min(item.start[0], item.end[0]) + 1;
    $('y1').value = Math.max(item.start[0], item.end[0]) + 1;
    $('x0').value = Math.min(item.start[1], item.end[1]) + 1;
    $('x1').value = Math.max(item.start[1], item.end[1]) + 1;
    changed();
    setMode(null);
  });
  for (const event of ['pointercancel', 'lostpointercapture']) viewport.addEventListener(event, () => { drag = null; drawGuides(); });
  new ResizeObserver(drawGuides).observe(viewport);

  $('generate').addEventListener('click', async () => {
    if (busy || seedBusy || !source || validation()) return;
    pendingRestore = null;
    setMode(null);
    const token = operation, draft = revision;
    const request = {case_id: source.case_id, series_uid: source.series_uid, stack_index: source.stack_index, ...parameters()};
    busy = true;
    jobKind = 'manual';
    message('Generating candidate from follow-up CT…');
    sync();
    try {
      const response = await fetch('/api/candidates', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(request)});
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || 'Candidate generation failed. Check the region, seed and HU range.');
      if (token !== operation) return;
      if (draft === revision && sourceMatches(payload)) {
        saved = [payload, ...saved.filter(item => item.id !== payload.id)];
        updateSaved();
        restore(payload);
      } else {
        message('The previous parameters were saved as a candidate. Generate again to use your changes.');
        readSaved();
      }
    } catch (error) {
      if (token === operation) message(error.message, true);
    } finally {
      if (token === operation) { busy = false; jobKind = null; sync(); }
    }
  });

  $('automatic').addEventListener('click', async () => {
    if (busy || seedBusy || drag || !automaticAvailable()) return;
    pendingRestore = null;
    setMode(null);
    const baseline = adapter.baselineSource();
    const token = operation, referenceToken = baselineEpoch, draft = revision;
    const acquisition = source.acquisition;
    const request = {case_id: source.case_id, series_uid: source.series_uid, stack_index: source.stack_index,
      pre_series_uid: baseline.series_uid, pre_stack_index: baseline.stack_index, segment: baseline.source_segment};
    // Keep any existing mask while trying automatic generation. An abstention
    // must not replace it with an ROI, a warped baseline mask, or an empty mask.
    clearAttempt();
    busy = true; jobKind = 'automatic'; sync();
    message('Localizing and segmenting follow-up CT…');
    try {
      const response = await fetch('/api/automatic-post-candidates', {method: 'POST',
        headers: {'Content-Type': 'application/json'}, body: JSON.stringify(request)});
      const result = await response.json();
      if (token !== operation) return;
      if (!response.ok) {
        if (referenceToken === baselineEpoch) {
          showAttempt(request, result, baseline, acquisition);
          message(result.status === 'abstained' ? 'No automatic candidate was created. The existing mask, if any, is unchanged.'
            : result.error || 'Automatic generation failed. Use manual selection.');
        }
        return;
      }
      saved = [result, ...saved.filter(item => item.id !== result.id)];
      updateSaved();
      if (referenceToken !== baselineEpoch || !selectable(result)) {
        message('Baseline reference changed. Generate again using the current reference.');
      } else if (draft !== revision || mode) {
        message('Automatic candidate saved. Your manual settings were kept; select the saved candidate to view it.');
      } else restore(result);
    } catch (error) {
      if (token === operation && referenceToken === baselineEpoch) {
        showAttempt(request, {status: 'error', error: error.message}, baseline, acquisition);
        message(error.message, true);
      }
    } finally {
      if (token === operation) { busy = false; jobKind = null; sync(); }
    }
  });

  return {
    setSource, baselineChanged, sync, drawGuides,
    get selecting() { return !!mode; },
    get candidate() { return candidate; },
    get automatic() { return isAutomatic(candidate); },
    get signature() { return [revision, candidate?.id, $('overlay').checked]; },
    get overlayId() { return candidate && $('overlay').checked ? candidate.id : null; },
    frameChanged(index, overlayId) {
      document.getElementById('post-overlay-label').hidden = !overlayId;
      document.getElementById('post-overlay-label').querySelector('span').textContent = candidateLabel(candidate);
      drawGuides();
      sync();
    },
  };
}
