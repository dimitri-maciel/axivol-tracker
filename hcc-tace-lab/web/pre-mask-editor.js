// PRE candidates remain separate from the published source. Manual strokes and
// automatic boundary refinement both start from that original mask; neither
// operation overwrites SEG bytes or chains changes onto another candidate.
const $ = name => document.getElementById('pre-edit-' + name);
const format = new Intl.NumberFormat('en-US', {maximumFractionDigits: 2});
const isRefined = item => item?.method?.name === 'boundary_graph_cut';
const copyName = item => isRefined(item) ? 'Refined copy' : 'Edited copy';

// Pixel indices denote pixel centres. Distance is measured in physical mm,
// including different row/column spacing. This is also the server's brush rule.
export function applyBrush(mask, rows, columns, spacing, stroke) {
  const [dy, dx] = spacing, radius = stroke.radius_mm;
  const points = stroke.points;
  for (let i = 0; i < Math.max(1, points.length - 1); i++) {
    const a = points[i], b = points[Math.min(i + 1, points.length - 1)];
    const y0 = Math.max(0, Math.ceil(Math.min(a[0], b[0]) - radius / dy));
    const y1 = Math.min(rows - 1, Math.floor(Math.max(a[0], b[0]) + radius / dy));
    const x0 = Math.max(0, Math.ceil(Math.min(a[1], b[1]) - radius / dx));
    const x1 = Math.min(columns - 1, Math.floor(Math.max(a[1], b[1]) + radius / dx));
    const vy = (b[0] - a[0]) * dy, vx = (b[1] - a[1]) * dx, length2 = vy * vy + vx * vx;
    for (let y = y0; y <= y1; y++) for (let x = x0; x <= x1; x++) {
      const py = (y - a[0]) * dy, px = (x - a[1]) * dx;
      const t = length2 ? Math.max(0, Math.min(1, (py * vy + px * vx) / length2)) : 0;
      if ((py - t * vy) ** 2 + (px - t * vx) ** 2 <= radius * radius) mask[y * columns + x] = stroke.mode === 'paint' ? 1 : 0;
    }
  }
}

export function createPreMaskEditor(adapter) {
  const viewport = document.getElementById('pre-viewport'), canvas = $('canvas');
  const context = canvas.getContext('2d');
  const style = document.getElementById('pre-mask-style'), opacity = document.getElementById('pre-mask-opacity');
  let source = null, active = false, mode = null, strokes = [], drawing = null, saved = [], selected = null;
  let sourceToken = 0, maskRequest = 0, revision = 0, busy = false, refining = false, mask = null, maskSlice = null;
  let maskController = null;
  const cache = new Map();
  const identity = item => item && [item.case_id, item.series_uid, item.stack_index, item.source_segment ?? item.segment].join('|');
  const pointCount = () => strokes.reduce((count, stroke) => count + stroke.points.length, 0);
  const hasSource = () => !!source?.segment && source.shape?.length === 2 && !!source.interval_mm && !source.issue;
  const canDraw = () => active && !!mode && !busy && adapter.ready() && !!mask && maskSlice === adapter.displayedIndex()
    && document.getElementById('show-mask').checked;
  const radius = () => Number($('radius').value);
  const validRadius = () => Number.isFinite(radius()) && radius() >= 1 && radius() <= 20;
  const validBand = () => $('band').value !== '' && Number($('band').value) >= 1 && Number($('band').value) <= 5;

  function message(text = '', error = false) {
    $('message').textContent = text;
    $('message').classList.toggle('error', error);
  }

  function sync() {
    for (const name of ['paint', 'erase']) {
      $(name).disabled = !hasSource() || !adapter.ready() || busy;
      $(name).setAttribute('aria-pressed', String(active && mode === name));
    }
    $('undo').disabled = !strokes.length || busy || !!drawing;
    $('clear').disabled = !strokes.length || busy || !!drawing;
    $('save').disabled = !strokes.length || busy || !!drawing || !hasSource();
    $('save').textContent = busy && !refining ? 'Saving…' : 'Save edited copy';
    $('refine').disabled = busy || !hasSource() || !adapter.ready() || strokes.length > 0 || !!drawing || !validBand();
    $('refine').textContent = refining ? 'Refining…' : 'Refine boundary';
    $('band').disabled = busy;
    $('refine-hint').textContent = strokes.length || drawing ? 'Save or clear your manual edits before refining.'
      : 'Creates a separate candidate from the original mask.';
    $('radius').disabled = busy;
    $('saved').disabled = busy || !hasSource();
    $('stop').hidden = !active;
    $('panel').setAttribute('aria-busy', String(busy));
    const editedSlices = new Set(strokes.map(stroke => stroke.slice)).size;
    $('count').textContent = strokes.length ? strokes.length + (strokes.length === 1 ? ' stroke' : ' strokes') + ' on '
      + editedSlices + (editedSlices === 1 ? ' slice' : ' slices') : 'No edits';
    style.disabled = !source?.segment;
    opacity.disabled = !source?.segment || style.value === 'outline';
    viewport.classList.toggle('selecting', active && !!mode);
    canvas.hidden = !active || !mask || !document.getElementById('show-mask').checked;
    $('hint').hidden = !active;
    if (!canDraw()) $('cursor').hidden = true;
    $('hint').textContent = !document.getElementById('show-mask').checked ? 'Show overlay to edit' : !mask ? 'Loading editable mask…' : mode
      ? (mode === 'paint' ? 'Paint' : 'Erase') + ' · ' + radius() + ' mm radius · Esc to pause'
      : 'Editing copy · Choose Paint or Erase';
    if (active) {
      document.getElementById('pre-zoom-in').disabled = true;
      document.getElementById('pre-zoom-out').disabled = true;
    }
  }

  function preview() {
    context.clearRect(0, 0, canvas.width, canvas.height);
    if (!active || !source || !mask || maskSlice !== adapter.displayedIndex()) return;
    const [rows, columns] = source.shape, result = mask.slice();
    for (const stroke of strokes) if (stroke.slice === maskSlice) applyBrush(result, rows, columns, source.spacing_mm, stroke);
    if (drawing) applyBrush(result, rows, columns, source.spacing_mm, drawing);
    const image = context.createImageData(columns, rows), fill = Math.round(Number(opacity.value) * 255 / 100);
    for (let y = 0; y < rows; y++) for (let x = 0; x < columns; x++) {
      const i = y * columns + x;
      if (!result[i]) continue;
      const boundary = x === 0 || y === 0 || x === columns - 1 || y === rows - 1
        || !result[i - 1] || !result[i + 1] || !result[i - columns] || !result[i + columns];
      const alpha = style.value !== 'fill' && boundary ? Math.round(.9 * 255) : style.value === 'outline' ? 0 : fill;
      image.data.set([50, 213, 222, alpha], i * 4);
    }
    context.putImageData(image, 0, 0);
    sync();
  }

  async function loadMask() {
    maskController?.abort();
    maskController = new AbortController();
    mask = null;
    maskSlice = null;
    preview();
    const z = adapter.displayedIndex(), token = ++maskRequest, epoch = sourceToken;
    if (!active || !hasSource() || !adapter.ready() || !Number.isInteger(z)) { sync(); return; }
    sync();
    try {
      let pixels = cache.get(z);
      if (!pixels) {
        const query = new URLSearchParams({stack: source.stack_index, index: z, segment: source.segment});
        const response = await fetch('/api/series/' + encodeURIComponent(source.series_uid) + '/mask?' + query,
          {signal: maskController.signal});
        if (!response.ok) {
          const payload = await response.json();
          throw new Error(payload.error || 'The published mask could not be loaded.');
        }
        const image = await createImageBitmap(await response.blob());
        const [rows, columns] = source.shape;
        if (image.width !== columns || image.height !== rows) { image.close(); throw new Error('Mask and image dimensions do not match.'); }
        const buffer = document.createElement('canvas'); buffer.width = columns; buffer.height = rows;
        const ctx = buffer.getContext('2d'); ctx.drawImage(image, 0, 0); image.close();
        const data = ctx.getImageData(0, 0, columns, rows).data;
        pixels = Uint8Array.from({length: rows * columns}, (_, index) => data[index * 4] > 0 ? 1 : 0);
      }
      if (token !== maskRequest || epoch !== sourceToken || !active) return;
      cache.set(z, pixels);
      if (cache.size > 8) cache.delete(cache.keys().next().value);
      mask = pixels;
      maskSlice = z;
      canvas.width = source.shape[1]; canvas.height = source.shape[0];
      preview();
    } catch (error) {
      if (error.name !== 'AbortError' && token === maskRequest && epoch === sourceToken) { message(error.message, true); sync(); }
    }
  }

  function renderSaved() {
    $('saved').replaceChildren(new Option('Original (published)', ''), ...saved.map(item =>
      new Option((isRefined(item) ? 'Refined' : 'Edited') + ' · ' + format.format(item.volume_cm3) + ' cm³ · ' + item.id.slice(0, 8), item.id)));
    $('saved').value = selected?.id || '';
    $('result').hidden = !selected;
    $('export').removeAttribute('href');
    $('difference').hidden = !isRefined(selected);
    $('warnings').replaceChildren();
    $('warnings').hidden = true;
    if (selected) {
      $('state').textContent = copyName(selected) + ' · Unreviewed';
      $('volume').textContent = format.format(selected.volume_cm3);
      $('provenance').textContent = selected.case_id + ' · Acquisition ' + selected.acquisition + ' · '
        + selected.source_segment_label + ' (' + selected.source_segment + ') · ' + format.format(selected.voxels) + ' voxels';
      $('export').href = '/api/candidates/' + encodeURIComponent(selected.id) + '/export';
      $('export').textContent = isRefined(selected) ? 'Download refined mask' : 'Download edited mask';
      if (isRefined(selected)) {
        const diagnostics = selected.diagnostics;
        $('band-used').textContent = 'Boundary band used: ' + format.format(selected.parameters.band_mm) + ' mm';
        $('added').textContent = format.format(diagnostics.added_voxels) + ' voxels';
        $('removed').textContent = format.format(diagnostics.removed_voxels) + ' voxels';
        $('changed-slices').textContent = diagnostics.changed_slices.length;
        const warnings = selected.warnings || [];
        $('warnings').replaceChildren(...warnings.map(text => {
          const item = document.createElement('li'); item.textContent = text; return item;
        }));
        $('warnings').hidden = warnings.length === 0;
      }
    }
    adapter.changed();
  }

  async function readSaved() {
    if (!hasSource()) return;
    const epoch = sourceToken;
    try {
      const query = new URLSearchParams({case: source.case_id, series: source.series_uid, stack: source.stack_index, segment: source.segment});
      const response = await fetch('/api/pre-edits?' + query), payload = await response.json();
      if (!response.ok) throw new Error(payload.error || 'Edited copies could not be loaded.');
      if (epoch !== sourceToken) return;
      // A list requested before a save may arrive after that save. Keep local
      // results first so the delayed list cannot remove the newly saved copy.
      const incoming = payload.candidates.filter(item => identity(item) === identity(source));
      saved = [...saved, ...incoming.filter(item => !saved.some(local => local.id === item.id))];
      renderSaved();
    } catch (error) { if (epoch === sourceToken) message(error.message, true); }
  }

  function exit() {
    maskController?.abort();
    active = false; mode = null; drawing = null; mask = null; maskSlice = null; maskRequest++;
    canvas.hidden = true; $('cursor').hidden = true;
    sync(); adapter.updateTransform(); adapter.refreshImage();
  }

  function pause() {
    exit();
    message(strokes.length ? 'Editing paused. Unsaved edits are preserved for this acquisition.'
      : 'Editing paused. Viewing the published mask.');
  }

  function start(tool) {
    if (!hasSource() || busy) return;
    if (!validRadius()) { $('radius').reportValidity(); return; }
    mode = tool;
    $('panel').open = true;
    if (!active) {
      active = true; selected = null; revision++;
      document.getElementById('show-mask').checked = true;
      renderSaved();
      adapter.resetZoom();
      adapter.refreshImage();
      message('Editing a copy of ' + source.label + '. Review each changed slice before saving.');
    }
    viewport.scrollIntoView({block: 'nearest'}); viewport.focus({preventScroll: true});
    sync();
  }

  function setSource(next) {
    maskController?.abort();
    sourceToken++; maskRequest++; revision++;
    source = next; active = false; mode = null; strokes = []; drawing = null;
    selected = null; saved = []; busy = false; refining = false; mask = null; maskSlice = null; cache.clear();
    canvas.hidden = true; $('cursor').hidden = true; $('panel').open = false;
    renderSaved(); sync();
    message();
    readSaved();
  }

  function pixel(event) {
    if (!source) return null;
    const [rows, columns] = source.shape, box = viewport.getBoundingClientRect();
    const scale = Math.min(box.width / columns, box.height / rows);
    const x = Math.floor((event.clientX - box.left - (box.width - columns * scale) / 2) / scale);
    const y = Math.floor((event.clientY - box.top - (box.height - rows * scale) / 2) / scale);
    return x < 0 || y < 0 || x >= columns || y >= rows ? null : [y, x];
  }

  function cursor(event) {
    const point = pixel(event);
    $('cursor').hidden = !point || !canDraw() || !validRadius();
    if ($('cursor').hidden) return;
    const [rows, columns] = source.shape, width = viewport.clientWidth, height = viewport.clientHeight;
    const scale = Math.min(width / columns, height / rows);
    $('cursor').setAttribute('viewBox', '0 0 ' + width + ' ' + height);
    const ellipse = $('cursor').firstElementChild;
    for (const [name, value] of Object.entries({cx: (width - columns * scale) / 2 + (point[1] + .5) * scale,
      cy: (height - rows * scale) / 2 + (point[0] + .5) * scale,
      rx: radius() / source.spacing_mm[1] * scale, ry: radius() / source.spacing_mm[0] * scale})) ellipse.setAttribute(name, value);
  }

  for (const tool of ['paint', 'erase']) $(tool).addEventListener('click', () => start(tool));
  $('stop').addEventListener('click', pause);
  $('panel').addEventListener('toggle', () => { if (!$('panel').open && active) pause(); });
  $('radius').addEventListener('input', () => { $('radius').setAttribute('aria-invalid', String(!validRadius())); sync(); });
  $('band').addEventListener('input', () => { $('band').setAttribute('aria-invalid', String(!validBand())); sync(); });
  $('undo').addEventListener('click', () => { strokes.pop(); revision++; preview(); sync(); message('Last stroke removed.'); });
  $('clear').addEventListener('click', () => { strokes = []; revision++; preview(); sync(); message('Edits cleared. The published mask is unchanged.'); });
  $('saved').addEventListener('change', () => {
    selected = saved.find(item => item.id === $('saved').value) || null;
    strokes = []; revision++; renderSaved(); exit();
    message(selected ? 'Viewing an unreviewed ' + (isRefined(selected) ? 'refined' : 'edited')
      + ' copy. Manual edits start from the published mask.' : 'Viewing the published segmentation.');
  });
  for (const control of [style, opacity]) control.addEventListener('input', () => {
    document.getElementById('pre-mask-opacity-value').textContent = opacity.value + '%';
    sync();
    if (active) preview();
  });
  // The slider updates the canvas immediately, and requests a server image once
  // released in viewing mode; rapid slider input never starts a fetch storm.
  for (const control of [style, opacity]) control.addEventListener('change', () => { if (!active) adapter.refreshImage(); });
  viewport.addEventListener('pointerdown', event => {
    if (!canDraw() || event.button !== 0 || !validRadius()) return;
    const point = pixel(event);
    if (!point) return;
    if (strokes.length >= 100 || pointCount() >= 2048) { message('Edit limit reached. Save this copy or undo a stroke.', true); return; }
    event.preventDefault(); viewport.focus({preventScroll: true}); viewport.setPointerCapture(event.pointerId);
    drawing = {slice: adapter.displayedIndex(), mode, radius_mm: radius(), points: [point]};
    preview(); sync();
  });
  viewport.addEventListener('pointermove', event => {
    cursor(event);
    if (!drawing || !canDraw()) return;
    const point = pixel(event), last = drawing.points[drawing.points.length - 1];
    if (!point || point.every((n, i) => n === last[i])) return;
    if (pointCount() + drawing.points.length >= 2048) { message('Point limit reached. Release the brush, then save or undo.', true); return; }
    drawing.points.push(point); preview();
  });
  viewport.addEventListener('pointerup', () => {
    if (!drawing) return;
    if (canDraw() && drawing.slice === adapter.displayedIndex()) { strokes.push(drawing); revision++; }
    drawing = null; preview(); sync();
  });
  for (const event of ['pointercancel', 'lostpointercapture']) viewport.addEventListener(event, () => {
    if (drawing) { drawing = null; preview(); sync(); }
  });
  viewport.addEventListener('pointerleave', () => { $('cursor').hidden = true; });
  viewport.addEventListener('keydown', event => {
    if (event.key === 'Escape' && active) { mode = null; drawing = null; $('cursor').hidden = true; preview(); sync(); }
  });
  $('save').addEventListener('click', async () => {
    if (!hasSource() || !strokes.length || busy || drawing) return;
    const epoch = sourceToken, draft = revision;
    const payload = {case_id: source.case_id, series_uid: source.series_uid, stack_index: source.stack_index,
      segment: source.segment, strokes: structuredClone(strokes)};
    busy = true; sync(); message('Saving edited copy…');
    try {
      const response = await fetch('/api/pre-edits', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'The edited copy could not be saved.');
      if (epoch !== sourceToken) return;
      saved = [result, ...saved.filter(item => item.id !== result.id)];
      if (draft === revision) {
        selected = result; strokes = []; renderSaved(); exit(); $('panel').open = false;
        message('Edited copy saved. Requires expert review.');
      } else { renderSaved(); message('The previous edits were saved. Save again to include newer changes.'); }
    } catch (error) { if (epoch === sourceToken) message(error.message, true); }
    finally { if (epoch === sourceToken) { busy = false; sync(); } }
  });

  $('refine').addEventListener('click', async () => {
    if (!hasSource() || !adapter.ready() || busy || strokes.length || drawing || !validBand()) return;
    // Refinement always starts from the immutable published mask. The current
    // preview and any saved manual copy are not inputs to this operation.
    selected = null; revision++; renderSaved(); exit();
    const epoch = sourceToken;
    const payload = {case_id: source.case_id, series_uid: source.series_uid, stack_index: source.stack_index,
      segment: source.segment, band_mm: Number($('band').value)};
    busy = true; refining = true; sync(); message('Refining the original boundary…');
    try {
      const response = await fetch('/api/pre-refinements', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'The boundary could not be refined. Try again.');
      if (epoch !== sourceToken) return;
      saved = [result, ...saved.filter(item => item.id !== result.id)];
      selected = result; renderSaved(); adapter.refreshImage();
      const diagnostics = result.diagnostics;
      const unchanged = diagnostics.added_voxels + diagnostics.removed_voxels === 0;
      message(unchanged ? diagnostics.outcome === 'insufficient_contrast'
        ? 'Not enough contrast to adjust the boundary. No changes were made.'
        : 'No boundary changes found with this band. The candidate matches the original mask.'
        : 'Refined copy saved. Compare it with the original across the changed slices.');
    } catch (error) { if (epoch === sourceToken) message(error.message, true); }
    finally { if (epoch === sourceToken) { busy = false; refining = false; sync(); } }
  });

  return {
    setSource, sync,
    pause: () => { if (active || drawing) pause(); },
    get editing() { return active; },
    get candidate() { return selected; },
    get signature() { return [revision, active, selected?.id, style.value, opacity.value]; },
    get presentation() { return {style: style.value, opacity: opacity.value}; },
    get overlayLabel() { return active ? 'Edited copy · Unsaved' : selected ? copyName(selected) + ' · Unreviewed' : 'Published ' + (source?.label || ''); },
    frameLoading() { maskController?.abort(); maskRequest++; mask = null; maskSlice = null; drawing = null; canvas.hidden = true; $('cursor').hidden = true; sync(); },
    frameChanged() { if (active) loadMask(); else sync(); },
    overlayChanged() { if (active) { preview(); sync(); } else adapter.refreshImage(); },
  };
}
