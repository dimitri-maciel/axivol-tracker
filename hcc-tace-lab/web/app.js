const $ = id => document.getElementById(id);
const stages = ['pre', 'post'];
const state = { epoch: 0, case: null, views: {}, requested: {pre: 0, post: 0} };

async function json(url) {
  const response = await fetch(url);
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || 'No se pudo cargar el estudio.');
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

function resetMeasurement() {
  $('measure-title').textContent = 'Volumen de la máscara';
  $('measure-result').textContent = $('segment').value
    ? 'La medición utiliza la etiqueta y la geometría de la serie PRE seleccionada.'
    : 'Esta serie PRE no tiene una etiqueta SEG vinculada.';
  $('measure').disabled = !$('segment').value;
}

function selection(stage) {
  const view = state.views[stage];
  return view?.summary.stacks[Number($(`${stage}-stack`).value)];
}

function reportLink() {
  if (!state.case || !state.views.pre || !state.views.post) {
    $('report').removeAttribute('href');
    return;
  }
  const query = new URLSearchParams({case: state.case.id, pre: state.views.pre.uid,
    preStack: $('pre-stack').value, post: state.views.post.uid, postStack: $('post-stack').value});
  if ($('segment').value) query.set('segment', $('segment').value);
  $('report').href = `/api/report?${query}`;
}

function showSlice(stage) {
  const stack = selection(stage);
  if (!stack) return;
  const index = Number($(`${stage}-index`).value) - 1;
  const query = new URLSearchParams({stack: String(stack.index), index: String(index),
    level: $('level').value, width: $('width').value});
  if (stage === 'pre' && $('show-mask').checked && $('segment').value) {
    query.set('segment', $('segment').value);
  }
  const image = $(`${stage}-image`);
  image.hidden = false;
  image.src = `/api/series/${state.views[stage].uid}/slice?${query}`;
  $(`${stage}-counter`).textContent = `${index + 1} / ${stack.slices}`;
  $(`${stage}-prev`).disabled = index === 0;
  $(`${stage}-next`).disabled = index + 1 >= stack.slices;
  $(`${stage}-status`).textContent = stack.issue || (stack.interval_mm
    ? `Espaciado axial ${stack.interval_mm.toFixed(2)} mm · corte nativo`
    : 'Corte nativo · espaciado no verificado');
  const labels = stack.orientation_labels || {};
  for (const side of ['a', 'p', 'r', 'l']) {
    $(`${stage}`).querySelector(`.marker.${side}`).textContent = labels[side] || '';
  }
}

function changeStack(stage) {
  const stack = selection(stage);
  if (!stack) return;
  const slider = $(`${stage}-index`);
  slider.max = stack.slices;
  slider.value = Math.ceil(stack.slices / 2);
  $(`${stage}-meta`).textContent = `Adquisición ${stack.acquisition} · ${stack.slices} cortes`;
  if (stage === 'pre') resetMeasurement();
  showSlice(stage);
  reportLink();
}

async function loadSeries(stage, epoch) {
  const uid = $(`${stage}-series`).value;
  const serial = ++state.requested[stage];
  const summary = await json(`/api/series/${uid}`);
  if (epoch !== state.epoch || serial !== state.requested[stage]) return;
  state.views[stage] = {uid, summary};
  options($(`${stage}-stack`), summary.stacks.map(s => [s.index, `${s.acquisition} · ${s.slices} cortes${s.issue ? ' · revisar' : ''}`]));
  if (stage === 'pre') {
    options($('segment'), summary.segments.length
      ? [['', 'Elige una etiqueta'], ...summary.segments.map(s => [s.number, s.label])]
      : [['', 'Sin SEG vinculado']], summary.default_segment ?? '');
    $('show-mask').disabled = !summary.segments.length;
    $('show-mask').checked = !!$('segment').value;
  }
  changeStack(stage);
}

async function loadCase(id) {
  const epoch = ++state.epoch;
  state.views = {};
  state.case = null;
  $('review').hidden = true;
  notice();
  try {
    const item = await json(`/api/cases/${id}`);
    if (epoch !== state.epoch) return;
    if (!item.chronological_pair) throw new Error('No se puede asignar un par PRE/POST inequívoco para este caso.');
    state.case = item;
    for (let i = 0; i < stages.length; i++) {
      const stage = stages[i], study = item.studies[i];
      const series = study.series.filter(s => s.local);
      if (!series.length) throw new Error(`Todavía no hay series ${stage.toUpperCase()} completas para este caso.`);
      $(`${stage}-date`).textContent = study.date;
      options($(`${stage}-series`), series.map(s => [s.uid, `${s.description} · ${s.instances}`]));
    }
    await Promise.all(stages.map(stage => loadSeries(stage, epoch)));
    if (epoch !== state.epoch) return;
    $('review').hidden = false;
    reportLink();
  } catch (error) {
    if (epoch === state.epoch) notice(error.message);
  }
}

function navigateSlice(stage, value) {
  const s = selection(stage);
  if (!s) return;
  $(`${stage}-index`).value = Math.min(s.slices, Math.max(1, value));
  showSlice(stage);
  if ($('link').checked) {
    const other = stage === 'pre' ? 'post' : 'pre';
    const target = selection(other);
    if (target) {
      const ratio = (Number($(`${stage}-index`).value) - 1) / Math.max(1, s.slices - 1);
      $(`${other}-index`).value = Math.round(ratio * (target.slices - 1)) + 1;
      showSlice(other);
    }
  }
}

for (const stage of stages) {
  $(`${stage}-series`).addEventListener('change', async () => {
    notice();
    const epoch = state.epoch;
    const selected = $(`${stage}-series`).value;
    try { await loadSeries(stage, epoch); } catch (error) {
      if (epoch !== state.epoch || $(`${stage}-series`).value !== selected) return;
      $(`${stage}-series`).value = state.views[stage]?.uid || selected;
      notice(error.message);
    }
  });
  $(`${stage}-stack`).addEventListener('change', () => changeStack(stage));
  $(`${stage}-index`).addEventListener('input', e => navigateSlice(stage, Number(e.target.value)));
  for (const [id, step] of [['prev', -1], ['next', 1]]) {
    $(`${stage}-${id}`).addEventListener('click', () => navigateSlice(stage, Number($(`${stage}-index`).value) + step));
  }
  $(`${stage}-image`).addEventListener('error', () => {
    $(`${stage}-image`).hidden = true;
    $(`${stage}-status`).textContent = 'No se pudo decodificar este corte. Prueba otra adquisición.';
  });
}

$('case').addEventListener('change', e => loadCase(e.target.value));
$('link').addEventListener('change', e => { $('link-note').hidden = !e.target.checked; });
$('show-mask').addEventListener('change', () => showSlice('pre'));
$('segment').addEventListener('change', () => { resetMeasurement(); showSlice('pre'); reportLink(); });
for (const id of ['level', 'width']) {
  $(id).addEventListener('change', () => {
    if (!$(id).reportValidity()) return;
    stages.forEach(showSlice);
  });
}

$('measure').addEventListener('click', async () => {
  const uid = state.views.pre.uid;
  const epoch = state.epoch;
  const stack = $('pre-stack').value, segment = $('segment').value;
  const matches = () => epoch === state.epoch && uid === state.views.pre?.uid
    && stack === $('pre-stack').value && segment === $('segment').value;
  $('measure').disabled = true;
  $('measure-result').textContent = 'Verificando referencias SEG y geometría…';
  try {
    const value = await json(`/api/series/${uid}/volume?${new URLSearchParams({stack, segment})}`);
    if (!matches()) return;
    $('measure-title').textContent = `${value.volume_cm3.toLocaleString('es-MX', {maximumFractionDigits: 2})} cm³ · ${value.label}`;
    $('measure-result').textContent = `${value.matched_frames} marcos SEG vinculados a la TC PRE. Volumen de la máscara publicada.`;
  } catch (error) {
    if (matches()) $('measure-result').textContent = error.message;
  } finally {
    if (matches()) $('measure').disabled = false;
  }
});

$('capture').addEventListener('click', async () => {
  const button = $('capture');
  button.disabled = true;
  try {
    await Promise.all([...document.images].map(img => img.decode().catch(() => null)));
    const canvas = await window.html2canvas(document.body, {
      scale: 2.5, backgroundColor: '#090e16', logging: false,
      onclone(doc) {
        doc.querySelectorAll('details:not([open]) .options-grid').forEach(el => el.remove());
        // Represent native range controls in the export; their browser chrome is not painted reliably.
        doc.querySelectorAll('input[type=range]').forEach(input => {
          const track = doc.createElement('div');
          const fraction = (Number(input.value) - Number(input.min)) / Math.max(1, Number(input.max) - Number(input.min));
          track.className = 'capture-track';
          const thumb = doc.createElement('span');
          thumb.style.left = `${fraction * 100}%`;
          track.append(thumb);
          input.replaceWith(track);
        });
      },
    });
    const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'));
    if (!blob) throw new Error('No se pudo preparar la imagen.');
    const download = $('image-download');
    if (download.href.startsWith('blob:')) URL.revokeObjectURL(download.href);
    download.href = URL.createObjectURL(blob);
    download.download = `HCC-TACE_Lab_${state.case?.id || 'vista'}.png`;
    download.hidden = false;
  } catch (error) { notice(error.message); }
  finally { button.disabled = false; }
});

try {
  const overview = await json('/api/cases');
  $('catalog-status').textContent = `${overview.series_local} de ${overview.series_total} series locales`;
  const ready = overview.cases.filter(c => c.studies >= 2);
  if (!ready.length) {
    options($('case'), [['', 'Sin pares locales']]);
    notice('Todavía no hay pares PRE/POST locales. Termina la ingesta y vuelve a abrir el visor.');
  } else {
    const requested = new URLSearchParams(location.search).get('case');
    options($('case'), ready.map(c => [c.id, c.id]), requested ?? undefined);
    $('case').disabled = false;
    await loadCase($('case').value);
  }
} catch (error) { notice(error.message); }
