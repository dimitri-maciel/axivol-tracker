import {FrameLoader} from './frame-loader.js';
import {createSegmentationEditor} from './segmentation-editor.js';

const $ = id => document.getElementById(id);
const frames = new FrameLoader();
const stages = ['pre', 'post'];
const element = (stage, part) => $(stage + '-' + part);
const stageName = stage => stage === 'pre' ? 'baseline' : 'follow-up';
const state = {
  epoch: 0, case: null, views: {}, requested: {pre: 0, post: 0},
  imageRequest: {pre: 0, post: 0}, measureRequest: 0, measuring: false, window: {level: 50, width: 350},
  display: {pre: {zoom: 1, x: 0, y: 0}, post: {zoom: 1, x: 0, y: 0}},
};
const number = new Intl.NumberFormat('en-US', {maximumFractionDigits: 2});
const integer = new Intl.NumberFormat('en-US', {maximumFractionDigits: 0});
const editor = createSegmentationEditor({
  ready: () => !!state.views.post?.ready,
  displayedIndex: () => state.views.post?.displayedIndex,
  transform: () => state.display.post,
  resetZoom: () => setZoom('post', 1),
  updateTransform: () => updateTransform('post'),
  changed: () => reportLink(),
  refreshImage() {
    // Do not leave pixels from a previous candidate visible during an overlay change.
    $('post-image').hidden = true;
    $('post-overlay-label').hidden = true;
    showSlice('post');
  },
});

async function json(url) {
  let response;
  try { response = await fetch(url); }
  catch { throw new Error('Connection lost. Check that the app is running and try again.'); }
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || 'The study could not be loaded. Select another series.');
  return payload;
}

function notice(message = '') {
  $('alert').textContent = message;
  $('alert').hidden = !message;
}

function options(select, entries, selected) {
  select.replaceChildren(...entries.map(([value, label]) => new Option(label, String(value))));
  if (selected !== undefined && entries.some(([value]) => String(value) === String(selected))) {
    select.value = String(selected);
  }
}

function selection(stage) {
  return state.views[stage]?.summary.stacks[Number(element(stage, 'stack').value)];
}

function selectedSegment() {
  return state.views.pre?.summary.segments.find(s => String(s.number) === $('segment').value);
}

function reportLink() {
  const available = state.case && stages.every(stage => state.views[stage]?.ready);
  $('capture').disabled = !available;
  $('report').setAttribute('aria-disabled', String(!available));
  $('report').tabIndex = available ? 0 : -1;
  if (!available) {
    $('report').removeAttribute('href');
    return;
  }
  const query = new URLSearchParams({
    case: state.case.id, pre: state.views.pre.uid, preStack: $('pre-stack').value,
    post: state.views.post.uid, postStack: $('post-stack').value,
  });
  if ($('segment').value) query.set('segment', $('segment').value);
  if (editor.candidate) query.set('candidate', editor.candidate.id);
  $('report').href = '/api/report?' + query;
}

function syncActions() {
  const stack = selection('pre');
  $('measure').disabled = state.measuring || !state.views.pre?.ready
    || !selectedSegment() || !stack?.interval_mm;
  $('show-mask').disabled = !selectedSegment();
  editor.sync();
  reportLink();
}

function resetMeasurement() {
  state.measureRequest++;
  state.measuring = false;
  $('measure').textContent = 'Calculate volume';
  $('volume-value').textContent = '—';
  $('source-voxels').textContent = '—';
  $('source-case').textContent = state.case?.id || '—';
  $('source-acquisition').textContent = selection('pre')?.acquisition || '—';
  const segment = selectedSegment();
  $('source-segment').textContent = segment ? segment.label + ' (' + segment.number + ')' : '—';
  $('measure-result').classList.remove('error');
  $('measure-result').textContent = selection('pre')?.issue
    || (segment ? 'Ready to measure the baseline segmentation.' : 'No segmentation selected for this series.');
  syncActions();
}

function updateTransform(stage) {
  const display = state.display[stage];
  element(stage, 'image').style.transform = 'translate(' + display.x + 'px, ' + display.y + 'px) scale(' + display.zoom + ')';
  element(stage, 'viewport').classList.toggle('zoomed', display.zoom > 1);
  element(stage, 'zoom').textContent = Math.round(display.zoom * 100) + '%';
  element(stage, 'zoom-out').disabled = display.zoom <= 1 || !state.views[stage]?.ready;
  element(stage, 'zoom-in').disabled = display.zoom >= 3 || !state.views[stage]?.ready;
  if (stage === 'post') editor.drawGuides();
}

function setZoom(stage, zoom) {
  const display = state.display[stage];
  display.zoom = Math.min(3, Math.max(1, zoom));
  if (display.zoom === 1) { display.x = 0; display.y = 0; }
  updateTransform(stage);
}

function frameState(stage, message) {
  const status = element(stage, 'image-state');
  const loading = message.startsWith('Loading');
  status.textContent = message;
  status.hidden = !message;
  status.classList.toggle('compact', loading && !element(stage, 'image').hidden);
  element(stage, 'viewport').setAttribute('aria-busy', String(loading));
}

async function showSlice(stage) {
  const stack = selection(stage);
  if (!stack) return;
  const token = ++state.imageRequest[stage], epoch = state.epoch;
  const view = state.views[stage];
  view.ready = false;
  const index = Number(element(stage, 'index').value) - 1;
  const query = new URLSearchParams({
    stack: String(stack.index), index: String(index),
    level: String(state.window.level), width: String(state.window.width),
  });
  const segment = stage === 'pre' && $('show-mask').checked ? selectedSegment() : null;
  if (segment) query.set('segment', segment.number);
  const candidateId = stage === 'post' ? editor.overlayId : null;
  if (candidateId) query.set('candidate', candidateId);
  element(stage, 'index').setAttribute('aria-valuetext', 'Requested slice ' + (index + 1) + ' of ' + stack.slices);
  element(stage, 'prev').disabled = index === 0;
  element(stage, 'next').disabled = index + 1 >= stack.slices;
  element(stage, 'status').textContent = stack.issue || (stack.interval_mm
    ? number.format(stack.interval_mm) + ' mm slice spacing' : 'Slice spacing not verified');
  const labels = stack.orientation_labels || {};
  for (const side of ['a', 'p', 'r', 'l']) {
    $(stage).querySelector('.marker.' + side).textContent = labels[side] || '';
  }
  frameState(stage, 'Loading slice ' + (index + 1) + '…');
  syncActions();
  if (stage === 'post') editor.drawGuides();
  const image = new Image();
  image.id = stage + '-image';
  image.alt = (stage === 'pre' ? 'Baseline' : 'Follow-up') + ' CT, slice ' + (index + 1) + ' of ' + stack.slices;
  image.draggable = false;
  const url = '/api/series/' + view.uid + '/slice?' + query;
  const current = () => epoch === state.epoch && token === state.imageRequest[stage] && state.views[stage] === view;
  try {
    const blob = await frames.load(stage, url);
    if (!blob || !current()) return;
    image.src = blob;
    await image.decode();
    if (!current()) return;
    element(stage, 'image').replaceWith(image);
    frames.pin(stage, url);
    view.ready = true;
    view.displayedIndex = index;
    element(stage, 'counter').textContent = (index + 1) + ' / ' + stack.slices;
    element(stage, 'index').setAttribute('aria-valuetext', 'Slice ' + (index + 1) + ' of ' + stack.slices);
    if (stage === 'pre') {
      $('pre-overlay-label').hidden = !segment;
      $('pre-overlay-label').querySelector('span').textContent = segment?.label || '';
    }
    frameState(stage, '');
    updateTransform(stage);
    if (stage === 'post') editor.frameChanged(index, candidateId);
    const neighbors = [];
    for (const offset of [1, -1, 2, -2, 3, -3]) {
      if (index + offset < 0 || index + offset >= stack.slices) continue;
      const next = new URLSearchParams(query);
      next.set('index', index + offset);
      neighbors.push('/api/series/' + view.uid + '/slice?' + next);
    }
    frames.warm(stage, neighbors);
  } catch {
    if (!current()) return;
    element(stage, 'image').hidden = true;
    frameState(stage, 'Image unavailable. Select another acquisition.');
    element(stage, 'status').textContent = 'This slice could not be decoded.';
  } finally {
    if (current()) syncActions();
  }
}

function changeStack(stage) {
  const stack = selection(stage);
  if (!stack) return;
  element(stage, 'image').hidden = true;
  element(stage, 'counter').textContent = '';
  if (stage === 'pre') $('pre-overlay-label').hidden = true;
  element(stage, 'index').max = stack.slices;
  element(stage, 'index').value = Math.ceil(stack.slices / 2);
  element(stage, 'meta').textContent = 'Acquisition ' + stack.acquisition;
  element(stage, 'spacing').textContent = stack.spacing_mm.join(' × ') + ' mm';
  element(stage, 'uid').textContent = state.views[stage].uid;
  state.display[stage] = {zoom: 1, x: 0, y: 0};
  state.views[stage].ready = false;
  state.views[stage].displayedIndex = null;
  if (stage === 'pre') resetMeasurement();
  if (stage === 'post') editor.setSource({
    ...stack, case_id: state.case.id, series_uid: state.views.post.uid, stack_index: stack.index,
  });
  showSlice(stage);
}

function setStudyControls(stage, disabled) {
  for (const part of ['stack', 'index', 'prev', 'next', 'zoom-in', 'zoom-out']) {
    element(stage, part).disabled = disabled;
  }
}

async function loadSeries(stage, epoch) {
  const uid = element(stage, 'series').value;
  const serial = ++state.requested[stage];
  ++state.imageRequest[stage];
  delete state.views[stage];
  if (stage === 'post') editor.setSource(null);
  setStudyControls(stage, true);
  element(stage, 'image').hidden = true;
  element(stage, 'meta').textContent = 'Loading acquisition…';
  element(stage, 'counter').textContent = '';
  element(stage, 'status').textContent = '';
  element(stage, 'uid').textContent = uid;
  element(stage, 'spacing').textContent = '—';
  frameState(stage, 'Loading image…');
  if (stage === 'pre') {
    options($('segment'), [['', 'Loading…']]);
    $('segment').disabled = true;
    $('pre-overlay-label').hidden = true;
    resetMeasurement();
  }
  syncActions();
  try {
    const summary = await json('/api/series/' + uid);
    if (epoch !== state.epoch || serial !== state.requested[stage]) return;
    if (!summary.stacks.length) throw new Error('No usable acquisitions in this series.');
    state.views[stage] = {uid, summary, ready: false};
    options(element(stage, 'stack'), summary.stacks.map(s => [s.index,
      'Acquisition ' + s.acquisition + ' (' + s.slices + ' slices)' + (s.issue ? ' — review required' : '')]));
    if (stage === 'pre') {
      options($('segment'), summary.segments.length
        ? [['', 'Select structure'], ...summary.segments.map(s => [s.number, s.label])]
        : [['', 'No segmentation available']], summary.default_segment ?? '');
      $('segment').disabled = !summary.segments.length;
      $('show-mask').checked = !!selectedSegment();
    }
    setStudyControls(stage, false);
    changeStack(stage);
  } catch (error) {
    if (epoch !== state.epoch || serial !== state.requested[stage]) return;
    frameState(stage, 'Study unavailable. Select another series.');
    if (stage === 'pre') { options($('segment'), [['', 'No segmentation available']]); resetMeasurement(); }
    notice(error.message);
  }
}

function studyDate(value) {
  const date = new Date(value + 'T12:00:00Z');
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat('en-GB',
    {day: '2-digit', month: 'short', year: 'numeric', timeZone: 'UTC'}).format(date);
}

async function loadCase(id) {
  const epoch = ++state.epoch;
  frames.clear();
  state.views = {};
  state.case = null;
  editor.setSource(null);
  resetMeasurement();
  $('review').hidden = true;
  $('loading').hidden = false;
  $('loading').textContent = 'Loading studies…';
  $('image-download').hidden = true;
  $('link').checked = false;
  $('link-note').hidden = true;
  notice();
  try {
    const item = await json('/api/cases/' + encodeURIComponent(id));
    if (epoch !== state.epoch) return;
    if (!item.chronological_pair) throw new Error('Study order is ambiguous. Select a case with a distinct baseline and follow-up.');
    state.case = item;
    for (let i = 0; i < stages.length; i++) {
      const stage = stages[i], study = item.studies[i];
      const series = study.series.filter(s => s.local);
      if (!series.length) throw new Error('The ' + stageName(stage) + ' study is not available. Download its series first.');
      element(stage, 'date').textContent = studyDate(study.date);
      element(stage, 'date').dateTime = study.date;
      options(element(stage, 'series'), series.map(s => [s.uid, s.description + ' (' + s.instances + ' images)']));
    }
    $('review').hidden = false;
    await Promise.all(stages.map(stage => loadSeries(stage, epoch)));
    if (epoch !== state.epoch) return;
    history.replaceState(null, '', '?case=' + encodeURIComponent(id));
  } catch (error) {
    if (epoch === state.epoch) { notice(error.message); $('review').hidden = true; }
  } finally {
    if (epoch === state.epoch) $('loading').hidden = true;
  }
}

function navigateSlice(stage, value) {
  const stack = selection(stage);
  if (!stack) return;
  element(stage, 'index').value = Math.min(stack.slices, Math.max(1, value));
  showSlice(stage);
  if ($('link').checked) {
    const other = stage === 'pre' ? 'post' : 'pre';
    const target = selection(other);
    if (target) {
      const ratio = (Number(element(stage, 'index').value) - 1) / Math.max(1, stack.slices - 1);
      element(other, 'index').value = Math.round(ratio * (target.slices - 1)) + 1;
      showSlice(other);
    }
  }
}

for (const stage of stages) {
  element(stage, 'series').addEventListener('change', () => { notice(); loadSeries(stage, state.epoch); });
  element(stage, 'stack').addEventListener('change', () => changeStack(stage));
  element(stage, 'index').addEventListener('input', e => navigateSlice(stage, Number(e.target.value)));
  for (const [part, step] of [['prev', -1], ['next', 1]]) {
    element(stage, part).addEventListener('click', () => navigateSlice(stage, Number(element(stage, 'index').value) + step));
  }
  for (const [part, step] of [['zoom-out', -.25], ['zoom-in', .25]]) {
    element(stage, part).addEventListener('click', () => setZoom(stage, state.display[stage].zoom + step));
  }
  const viewport = element(stage, 'viewport');
  let wheelDelta = 0, wheelFrame = null;
  viewport.addEventListener('wheel', event => {
    if (document.activeElement !== viewport || !selection(stage)) return;
    event.preventDefault();
    wheelDelta += event.deltaY;
    if (wheelFrame !== null) return;
    wheelFrame = requestAnimationFrame(() => {
      if (Math.abs(wheelDelta) >= 12) {
        const step = Math.sign(wheelDelta) * Math.min(3, Math.max(1, Math.round(Math.abs(wheelDelta) / 80)));
        navigateSlice(stage, Number(element(stage, 'index').value) + step);
        wheelDelta = 0;
      }
      wheelFrame = null;
    });
  }, {passive: false});
  viewport.addEventListener('keydown', event => {
    const moves = {ArrowUp: -1, ArrowDown: 1, PageUp: -10, PageDown: 10};
    if (event.key in moves && selection(stage)) {
      event.preventDefault();
      navigateSlice(stage, Number(element(stage, 'index').value) + moves[event.key]);
    }
  });
  let drag = null;
  viewport.addEventListener('pointerdown', event => {
    if (stage === 'post' && editor.selecting) return;
    if (state.display[stage].zoom <= 1 || !state.views[stage]?.ready || event.button !== 0) return;
    viewport.focus();
    drag = {x: event.clientX, y: event.clientY, ...{panX: state.display[stage].x, panY: state.display[stage].y}};
    viewport.setPointerCapture(event.pointerId);
    event.preventDefault();
  });
  viewport.addEventListener('pointermove', event => {
    if (!drag) return;
    const display = state.display[stage];
    const limitX = viewport.clientWidth * (display.zoom - 1) / 2;
    const limitY = viewport.clientHeight * (display.zoom - 1) / 2;
    display.x = Math.min(limitX, Math.max(-limitX, drag.panX + event.clientX - drag.x));
    display.y = Math.min(limitY, Math.max(-limitY, drag.panY + event.clientY - drag.y));
    updateTransform(stage);
  });
  for (const event of ['pointerup', 'pointercancel', 'lostpointercapture']) {
    viewport.addEventListener(event, () => { drag = null; });
  }
}

$('case').addEventListener('change', e => loadCase(e.target.value));
$('link').addEventListener('change', e => { $('link-note').hidden = !e.target.checked; });
$('show-mask').addEventListener('change', () => showSlice('pre'));
$('segment').addEventListener('change', () => {
  $('show-mask').checked = !!selectedSegment();
  resetMeasurement();
  showSlice('pre');
});
function validWindow() {
  return ['level', 'width'].map(id => {
    $(id).setAttribute('aria-invalid', String(!$(id).validity.valid));
    return $(id).reportValidity();
  }).every(Boolean);
}
for (const id of ['level', 'width']) {
  $(id).addEventListener('change', () => {
    if (!validWindow()) return;
    state.window = {level: Number($('level').value), width: Number($('width').value)};
    stages.forEach(showSlice);
  });
}
$('reset-display').addEventListener('click', () => {
  $('width').value = 350;
  $('level').value = 50;
  state.window = {level: 50, width: 350};
  validWindow();
  stages.forEach(stage => { setZoom(stage, 1); showSlice(stage); });
});

$('measure').addEventListener('click', async () => {
  if ($('measure').disabled) return;
  const uid = state.views.pre.uid, epoch = state.epoch;
  const stack = $('pre-stack').value, segment = $('segment').value;
  const token = ++state.measureRequest;
  const matches = () => epoch === state.epoch && token === state.measureRequest
    && uid === state.views.pre?.uid && stack === $('pre-stack').value && segment === $('segment').value;
  state.measuring = true;
  $('measure').textContent = 'Calculating…';
  $('measure-result').classList.remove('error');
  $('measure-result').textContent = 'Checking segmentation references and geometry…';
  syncActions();
  try {
    const value = await json('/api/series/' + uid + '/volume?' + new URLSearchParams({stack, segment}));
    if (!matches()) return;
    $('volume-value').textContent = value.volume_cm3.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
    $('source-voxels').textContent = integer.format(value.voxels);
    $('measure-result').textContent = 'Measured from ' + value.matched_frames + ' referenced baseline slices.';
  } catch (error) {
    if (matches()) {
      $('volume-value').textContent = '—';
      $('measure-result').classList.add('error');
      $('measure-result').textContent = error.message;
    }
  } finally {
    if (matches()) {
      state.measuring = false;
      $('measure').textContent = 'Calculate volume';
      syncActions();
    }
  }
});

function viewSignature() {
  return JSON.stringify({
    epoch: state.epoch, image: state.imageRequest, measure: state.measureRequest,
    display: state.display, volume: $('volume-value').textContent,
    candidate: editor.signature,
  });
}
$('capture').addEventListener('click', async () => {
  const button = $('capture'), signature = viewSignature();
  button.disabled = true;
  try {
    const canvas = await window.html2canvas(document.body, {
      scale: 2, backgroundColor: '#111418', logging: false,
      onclone(doc) {
        // html2canvas does not implement object-fit. Paint each image with
        // the same contain geometry as the live viewport before exporting.
        for (const stage of stages) {
          const source = element(stage, 'image');
          const viewport = element(stage, 'viewport');
          const width = viewport.clientWidth, height = viewport.clientHeight;
          const canvas = doc.createElement('canvas');
          canvas.width = width * 2;
          canvas.height = height * 2;
          const context = canvas.getContext('2d');
          const scale = Math.min(width / source.naturalWidth, height / source.naturalHeight);
          const imageWidth = source.naturalWidth * scale, imageHeight = source.naturalHeight * scale;
          context.scale(2, 2);
          context.drawImage(source, (width - imageWidth) / 2, (height - imageHeight) / 2, imageWidth, imageHeight);
          Object.assign(canvas.style, {display: 'block', width: '100%', height: '100%', minHeight: '0', transform: source.style.transform});
          doc.getElementById(stage + '-image').replaceWith(canvas);
        }
        doc.querySelectorAll('details:not([open]) .study-fields').forEach(el => el.remove());
        doc.querySelectorAll('input[type=range]').forEach(input => {
          const track = doc.createElement('div');
          const fraction = (Number(input.value) - Number(input.min)) / Math.max(1, Number(input.max) - Number(input.min));
          track.className = 'capture-track';
          const thumb = doc.createElement('span');
          thumb.style.left = fraction * 100 + '%';
          track.append(thumb);
          input.replaceWith(track);
        });
      },
    });
    if (signature !== viewSignature()) throw new Error('The view changed during export. Export the image again.');
    const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'));
    if (!blob) throw new Error('Image export failed. Try again.');
    const download = $('image-download');
    if (download.href.startsWith('blob:')) URL.revokeObjectURL(download.href);
    download.href = URL.createObjectURL(blob);
    download.download = 'Axivol_Tracker_' + state.case.id + '.png';
    download.hidden = false;
    download.click();
  } catch (error) { notice(error.message); }
  finally { syncActions(); }
});

try {
  const overview = await json('/api/cases');
  const ready = overview.cases.filter(c => c.studies >= 2);
  $('catalog-status').textContent = ready.length + ' available cases';
  if (!ready.length) {
    options($('case'), [['', 'No studies available']]);
    $('loading').textContent = 'Download a baseline and follow-up study to begin.';
  } else {
    const requested = new URLSearchParams(location.search).get('case');
    const initial = requested || (ready.some(c => c.id === 'HCC_002') ? 'HCC_002' : ready[0].id);
    options($('case'), ready.map(c => [c.id, c.id]), initial);
    $('case').disabled = false;
    await loadCase($('case').value);
  }
} catch (error) { $('loading').hidden = true; notice(error.message); }
