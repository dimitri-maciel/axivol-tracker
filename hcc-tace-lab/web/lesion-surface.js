// Local, read-only display of exposed voxel faces from a saved POST mask.
// Camera changes affect only rendering; geometry is never smoothed or resampled.
const decimal = new Intl.NumberFormat('en-US', {maximumFractionDigits: 2});
const integer = new Intl.NumberFormat('en-US');
const identity = item => item && JSON.stringify([item.id, item.case_id, item.series_uid, item.stack_index, item.acquisition, item.mask_sha256]);
const clamp = (value, low, high) => Math.max(low, Math.min(high, value));

function cameraBasis(azimuth, elevation) {
  const a = azimuth * Math.PI / 180, e = elevation * Math.PI / 180;
  return {
    right: [Math.cos(a), Math.sin(a), 0],
    up: [-Math.sin(a) * Math.sin(e), Math.cos(a) * Math.sin(e), Math.cos(e)],
    eye: [Math.sin(a) * Math.cos(e), -Math.cos(a) * Math.cos(e), Math.sin(e)],
  };
}

function checkedMesh(payload, candidate) {
  if (payload.candidate_id !== candidate.id || payload.case_id !== candidate.case_id
      || payload.series_uid !== candidate.series_uid || payload.stack_index !== candidate.stack_index
      || payload.acquisition !== candidate.acquisition || payload.mask_sha256 !== candidate.mask_sha256
      || !/^[a-f0-9]{64}$/.test(payload.mask_sha256 || '') || payload.review_status !== candidate.status) {
    throw new Error('The surface does not match the selected mask. Close this view and select the mask again.');
  }
  if (payload.coordinate_system !== 'LPS' || payload.units !== 'mm'
      || payload.representation !== 'exposed_voxel_faces' || payload.smoothing !== false
      || payload.decimation !== false || payload.all_components_retained !== true) {
    throw new Error('The surface geometry could not be verified.');
  }
  if (payload.stats?.voxels !== candidate.voxels) throw new Error('The surface voxel count does not match the saved mask.');
  if (!Number.isFinite(payload.stats.native_volume_cm3) || !Number.isFinite(candidate.volume_cm3)
      || Math.abs(payload.stats.native_volume_cm3 - candidate.volume_cm3) > Math.max(1e-6, candidate.volume_cm3 * 1e-6)) {
    throw new Error('The surface volume does not match the saved mask.');
  }
  if (payload.status === 'empty_mask') return null;
  const vertices = payload.vertices_lps_mm, triangles = payload.triangles, bounds = payload.bounds_lps_mm;
  if (payload.status !== 'surface' || !Array.isArray(vertices) || !vertices.length || vertices.length > 200000
      || !Array.isArray(triangles) || !triangles.length || triangles.length > 240000
      || payload.stats.vertices !== vertices.length || payload.stats.triangles !== triangles.length
      || !Array.isArray(bounds) || bounds.length !== 2
      || !bounds.every(row => Array.isArray(row) && row.length === 3 && row.every(Number.isFinite))
      || bounds[0].some((value, i) => value >= bounds[1][i])) {
    throw new Error('The surface geometry is incomplete or exceeds the display limit.');
  }
  const center = bounds[0].map((value, i) => (value + bounds[1][i]) / 2);
  const positions = new Float32Array(vertices.length * 3), indices = new Uint32Array(triangles.length * 3);
  for (let i = 0; i < vertices.length; i++) {
    const row = vertices[i];
    if (!Array.isArray(row) || row.length !== 3 || row.some((value, axis) => !Number.isFinite(value)
        || value < bounds[0][axis] - 1e-5 || value > bounds[1][axis] + 1e-5)) {
      throw new Error('The surface contains invalid coordinates.');
    }
    // Subtract the physical center before Float32 conversion to preserve local precision.
    for (let axis = 0; axis < 3; axis++) positions[3 * i + axis] = row[axis] - center[axis];
  }
  for (let i = 0; i < triangles.length; i++) {
    const row = triangles[i];
    if (!Array.isArray(row) || row.length !== 3 || row.some(value => !Number.isInteger(value) || value < 0 || value >= vertices.length)) {
      throw new Error('The surface contains invalid triangles.');
    }
    indices.set(row, 3 * i);
  }
  return {positions, indices, radius: Math.hypot(...bounds[1].map((value, i) => value - bounds[0][i])) / 2};
}

function createRenderer(canvas, mesh) {
  const gl = canvas.getContext('webgl2', {alpha: false, antialias: true, preserveDrawingBuffer: true});
  if (!gl) throw new Error('3D display is unavailable in this browser. The saved mask remains available in the CT views.');
  const shader = (type, text) => {
    const value = gl.createShader(type);
    gl.shaderSource(value, text); gl.compileShader(value);
    if (!gl.getShaderParameter(value, gl.COMPILE_STATUS)) {
      gl.deleteShader(value); throw new Error('The 3D renderer could not start.');
    }
    return value;
  };
  const program = gl.createProgram(), buffers = [], shaders = [];
  let vao;
  const dispose = () => {
    buffers.forEach(value => gl.deleteBuffer(value));
    shaders.forEach(value => gl.deleteShader(value));
    if (vao) gl.deleteVertexArray(vao);
    gl.deleteProgram(program);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
  };
  try {
    shaders.push(shader(gl.VERTEX_SHADER, `#version 300 es
      in vec3 position;
      uniform vec3 cameraRight, cameraUp, cameraEye;
      uniform vec3 viewScale;
      out vec3 viewPosition;
      void main() {
        viewPosition = vec3(dot(position, cameraRight), dot(position, cameraUp), dot(position, cameraEye));
        gl_Position = vec4(viewPosition * viewScale, 1.0);
      }`));
    shaders.push(shader(gl.FRAGMENT_SHADER, `#version 300 es
      precision highp float;
      in vec3 viewPosition;
      out vec4 color;
      void main() {
        vec3 normal = normalize(cross(dFdx(viewPosition), dFdy(viewPosition)));
        if (normal.z < 0.0) normal = -normal;
        float light = 0.44 + 0.56 * max(dot(normal, normalize(vec3(-0.45, 0.65, 1.0))), 0.0);
        color = vec4(vec3(0.196, 0.835, 0.871) * light, 1.0);
      }`));
    shaders.forEach(value => gl.attachShader(program, value)); gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error('The 3D renderer could not start.');
    vao = gl.createVertexArray(); gl.bindVertexArray(vao);
    const positions = gl.createBuffer(); buffers.push(positions);
    gl.bindBuffer(gl.ARRAY_BUFFER, positions); gl.bufferData(gl.ARRAY_BUFFER, mesh.positions, gl.STATIC_DRAW);
    const position = gl.getAttribLocation(program, 'position');
    gl.enableVertexAttribArray(position); gl.vertexAttribPointer(position, 3, gl.FLOAT, false, 0, 0);
    const indices = gl.createBuffer(); buffers.push(indices);
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, indices); gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, mesh.indices, gl.STATIC_DRAW);
    const uniforms = Object.fromEntries(['cameraRight', 'cameraUp', 'cameraEye', 'viewScale'].map(name => [name, gl.getUniformLocation(program, name)]));
    gl.enable(gl.DEPTH_TEST); gl.disable(gl.CULL_FACE);
    gl.clearColor(0.045, 0.067, 0.09, 1);
    return {
      dispose,
      draw(basis, zoom) {
        const ratio = Math.min(window.devicePixelRatio || 1, 2), box = canvas.getBoundingClientRect();
        const width = Math.max(1, Math.round(box.width * ratio)), height = Math.max(1, Math.round(box.height * ratio));
        if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height; }
        gl.viewport(0, 0, width, height); gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
        gl.useProgram(program); gl.bindVertexArray(vao);
        gl.uniform3fv(uniforms.cameraRight, basis.right); gl.uniform3fv(uniforms.cameraUp, basis.up); gl.uniform3fv(uniforms.cameraEye, basis.eye);
        const scale = zoom / (mesh.radius * 1.2), aspect = width / height;
        gl.uniform3f(uniforms.viewScale, scale / Math.max(1, aspect), scale * Math.min(1, aspect), -1 / (mesh.radius * 3));
        gl.drawElements(gl.TRIANGLES, mesh.indices.length, gl.UNSIGNED_INT, 0);
      },
    };
  } catch (error) { dispose(); throw error; }
}

export function createLesionSurfaceDialog() {
  const dialog = document.createElement('dialog');
  dialog.id = 'lesion-surface-dialog'; dialog.className = 'lesion-surface-dialog';
  dialog.setAttribute('data-html2canvas-ignore', 'true');
  dialog.setAttribute('aria-labelledby', 'lesion-surface-title');
  dialog.innerHTML = `
    <header class="surface-heading"><div><h2 id="lesion-surface-title">Follow-up mask</h2><p id="lesion-surface-source"></p></div><div class="surface-heading-actions"><span class="status-tag">Unreviewed</span><button type="button" id="lesion-surface-close" class="button secondary" autofocus>Close</button></div></header>
    <div class="surface-stage" aria-busy="true">
      <canvas id="lesion-surface-canvas" tabindex="0" aria-label="3D follow-up mask. Arrow keys rotate; plus and minus zoom; R resets the view." aria-describedby="lesion-surface-help" hidden></canvas>
      <p id="lesion-surface-message" class="surface-message" role="status">Loading surface…</p>
      <svg id="lesion-surface-axes" class="surface-axes" viewBox="0 0 130 130" role="img" aria-label="Patient directions: L left, R right, P posterior, A anterior, S superior, I inferior" hidden></svg>
      <span class="surface-render-label" hidden>3D surface from saved mask</span>
    </div>
    <div class="surface-toolbar"><div class="surface-presets" role="group" aria-label="View direction"><button type="button" class="button secondary" data-view="anterior">Anterior</button><button type="button" class="button secondary" data-view="posterior">Posterior</button><button type="button" class="button secondary" data-view="left">Left</button></div><div class="surface-camera" role="group" aria-label="3D camera"><button type="button" id="lesion-surface-minus" class="button secondary" aria-label="Zoom out 3D mask">−</button><output id="lesion-surface-zoom">100%</output><button type="button" id="lesion-surface-plus" class="button secondary" aria-label="Zoom in 3D mask">+</button><button type="button" id="lesion-surface-reset" class="button secondary">Reset view</button></div></div>
    <footer class="surface-footer"><div><p id="lesion-surface-help">Drag to rotate. Scroll to zoom.</p><p>No smoothing. All mask components are shown.</p></div><div class="surface-measurement"><p id="lesion-surface-volume"></p><p id="lesion-surface-range"></p><p id="lesion-surface-boundary" class="surface-boundary-warning" hidden>Mask reaches the scan boundary.</p></div></footer>`;
  document.body.append(dialog);
  const find = name => dialog.querySelector('#lesion-surface-' + name);
  const canvas = find('canvas'), axes = find('axes'), stage = dialog.querySelector('.surface-stage');
  const controls = [...dialog.querySelectorAll('.surface-toolbar button')];
  let sourceKey = null, operation = 0, request = null, renderer = null, frame = 0, drag = null;
  let azimuth = -30, elevation = 18, zoom = 1, opener = null;
  const resize = new ResizeObserver(() => schedule());

  function release() {
    operation++; request?.abort(); request = null; sourceKey = null; drag = null;
    cancelAnimationFrame(frame); frame = 0; resize.disconnect(); renderer?.dispose(); renderer = null;
    canvas.hidden = true; axes.toggleAttribute('hidden', true); stage.setAttribute('aria-busy', 'false');
    delete canvas.dataset.candidateId; delete canvas.dataset.triangles; delete canvas.dataset.camera;
    dialog.querySelector('.surface-render-label').hidden = true;
  }
  function orientation(basis) {
    // Positive physical LPS axes and their opposites use the same camera as the mesh.
    const directions = [['L', 0, 1], ['R', 0, -1], ['P', 1, 1], ['A', 1, -1], ['S', 2, 1], ['I', 2, -1]];
    const projected = directions.map(([label, axis, sign]) => ({label, depth: sign * basis.eye[axis],
      x: 65 + sign * basis.right[axis] * 43, y: 65 - sign * basis.up[axis] * 43})).sort((a, b) => a.depth - b.depth);
    axes.replaceChildren();
    for (const point of projected) {
      const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
      for (const [key, value] of Object.entries({x1: 65, y1: 65, x2: point.x, y2: point.y})) line.setAttribute(key, value);
      const text = document.createElementNS('http://www.w3.org/2000/svg', 'text');
      text.setAttribute('x', point.x);
      // Axes parallel to the viewing direction project to the same point.
      // Offset their labels, not the axis endpoints, so both directions remain legible.
      text.setAttribute('y', point.y + (Math.hypot(point.x - 65, point.y - 65) < 18 ? (point.depth > 0 ? -12 : 12) : 0));
      text.textContent = point.label;
      const group = document.createElementNS('http://www.w3.org/2000/svg', 'g');
      group.setAttribute('opacity', point.depth < -0.2 ? '.5' : '1'); group.append(line, text); axes.append(group);
    }
  }
  function schedule() {
    if (frame || !renderer || !dialog.open) return;
    frame = requestAnimationFrame(() => {
      frame = 0; if (!renderer || !dialog.open) return;
      const basis = cameraBasis(azimuth, elevation); renderer.draw(basis, zoom); orientation(basis);
      const angle = ((azimuth % 360) + 360) % 360;
      dialog.querySelectorAll('[data-view]').forEach(button => button.setAttribute('aria-pressed',
        String(elevation === 0 && angle === {anterior: 0, posterior: 180, left: 90}[button.dataset.view])));
      find('zoom').textContent = Math.round(zoom * 100) + '%';
      canvas.dataset.camera = [azimuth, elevation, zoom].join(',');
    });
  }
  function setZoom(value) { zoom = clamp(value, 0.4, 4); schedule(); }
  function reset() { azimuth = -30; elevation = 18; zoom = 1; schedule(); }
  function close() { if (dialog.open) dialog.close(); release(); }
  dialog.addEventListener('close', () => {
    if (dialog.open) return; // An older close event must not clear a newly opened source.
    release(); if (opener?.isConnected && !opener.disabled) opener.focus();
  });
  find('close').addEventListener('click', close);
  find('reset').addEventListener('click', reset);
  find('minus').addEventListener('click', () => setZoom(zoom / 1.2));
  find('plus').addEventListener('click', () => setZoom(zoom * 1.2));
  dialog.querySelectorAll('[data-view]').forEach(button => button.addEventListener('click', () => {
    azimuth = {anterior: 0, posterior: 180, left: 90}[button.dataset.view]; elevation = 0; schedule();
  }));
  canvas.addEventListener('pointerdown', event => {
    if (!renderer || !event.isPrimary || event.button !== 0) return;
    drag = {x: event.clientX, y: event.clientY}; canvas.setPointerCapture(event.pointerId); canvas.focus();
  });
  canvas.addEventListener('pointermove', event => {
    if (!drag) return;
    azimuth += (event.clientX - drag.x) * 0.45; elevation = clamp(elevation + (event.clientY - drag.y) * 0.45, -85, 85);
    drag = {x: event.clientX, y: event.clientY}; schedule();
  });
  for (const name of ['pointerup', 'pointercancel', 'lostpointercapture']) canvas.addEventListener(name, () => { drag = null; });
  canvas.addEventListener('wheel', event => {
    if (!renderer) return;
    event.preventDefault(); setZoom(zoom * Math.exp(-event.deltaY * (event.deltaMode === 1 ? 0.025 : 0.0015)));
  }, {passive: false});
  canvas.addEventListener('keydown', event => {
    if (!renderer) return;
    if (event.key === 'ArrowLeft') azimuth -= 10;
    else if (event.key === 'ArrowRight') azimuth += 10;
    else if (event.key === 'ArrowUp') elevation = clamp(elevation + 10, -85, 85);
    else if (event.key === 'ArrowDown') elevation = clamp(elevation - 10, -85, 85);
    else if (event.key === '+' || event.key === '=') setZoom(zoom * 1.2);
    else if (event.key === '-') setZoom(zoom / 1.2);
    else if (event.key.toLowerCase() === 'r') reset();
    else return;
    event.preventDefault(); schedule();
  });
  canvas.addEventListener('webglcontextlost', event => {
    event.preventDefault(); renderer?.dispose(); renderer = null;
    controls.forEach(button => { button.disabled = true; }); canvas.hidden = true; axes.toggleAttribute('hidden', true);
    find('message').hidden = false; find('message').textContent = '3D display was interrupted. Close and reopen this view.';
  });

  return {
    async open(candidate) {
      if (!candidate?.id) return;
      if (dialog.open && sourceKey === identity(candidate)) { canvas.focus(); return; }
      release(); sourceKey = identity(candidate); const token = operation;
      opener = document.activeElement; reset(); controls.forEach(button => { button.disabled = true; });
      find('source').textContent = candidate.case_id + ' · Acquisition ' + candidate.acquisition;
      find('volume').textContent = decimal.format(candidate.volume_cm3) + ' cm³ · ' + integer.format(candidate.voxels) + ' voxels';
      find('range').textContent = '';
      find('boundary').hidden = true;
      find('message').hidden = false; find('message').textContent = 'Loading surface…'; stage.setAttribute('aria-busy', 'true');
      if (!dialog.open) dialog.showModal();
      request = new AbortController();
      try {
        const response = await fetch('/api/candidates/' + encodeURIComponent(candidate.id) + '/surface', {signal: request.signal});
        const payload = await response.json();
        if (token !== operation || !dialog.open) return;
        if (!response.ok) throw new Error(payload.error || 'The surface could not be loaded. Close this view and try again.');
        const mesh = checkedMesh(payload, candidate);
        if (!mesh) { find('message').textContent = 'This saved mask is empty. There is no surface to display.'; return; }
        find('boundary').hidden = !payload.acquisition_touch_faces?.length;
        const bounds = payload.mask_bounds_zyx_halfopen;
        if (Array.isArray(bounds) && bounds.length === 2 && Number.isInteger(bounds[0]?.[0]) && Number.isInteger(bounds[1]?.[0])) {
          find('range').textContent = 'Mask on slices ' + (bounds[0][0] + 1) + '–' + bounds[1][0];
        }
        renderer = createRenderer(canvas, mesh); canvas.hidden = false; axes.toggleAttribute('hidden', false);
        find('message').hidden = true; controls.forEach(button => { button.disabled = false; });
        dialog.querySelector('.surface-render-label').hidden = false;
        canvas.dataset.candidateId = candidate.id; canvas.dataset.triangles = payload.stats.triangles;
        resize.observe(stage); schedule();
      } catch (error) {
        if (token === operation && error.name !== 'AbortError') find('message').textContent = error.message;
      } finally { if (token === operation) stage.setAttribute('aria-busy', 'false'); }
    },
    sync(candidate) { if (sourceKey && sourceKey !== identity(candidate)) close(); },
    close,
    destroy() { close(); resize.disconnect(); dialog.remove(); },
  };
}
