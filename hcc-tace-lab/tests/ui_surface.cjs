// Read-only 3D QA using saved HCC_002/HCC_006 masks. No inference or annotation writes.
// Error/late-response checks alter transport only; all rendered geometry comes from the real API.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');

(async () => {
  const url = process.env.AXIVOL_URL, output = process.env.AXIVOL_QA_DIR;
  if (!url || !output) throw new Error('Set AXIVOL_URL and AXIVOL_QA_DIR. Saved POST masks are required.');
  await fs.mkdir(output, {recursive: true});
  const browser = await chromium.launch({headless: true});
  const page = await browser.newPage({viewport: {width: 1600, height: 1100}});
  page.setDefaultTimeout(30000);
  const errors = [], writes = [], checks = [], cases = [], requests = [];
  const protect = async target => {
    target.on('pageerror', error => errors.push(error.message));
    await target.route('**/*', route => {
      const request = route.request();
      if (!['GET', 'HEAD'].includes(request.method())) { writes.push(request.url()); return route.abort(); }
      return route.continue();
    });
  };
  await protect(page);
  page.on('request', request => { if (new URL(request.url()).pathname.endsWith('/surface')) requests.push(request.url()); });
  const ready = async (target = page) => {
    for (const stage of ['pre', 'post']) await target.waitForFunction(stage => {
      const image = document.getElementById(stage + '-image');
      return image && !image.hidden && image.complete && image.naturalWidth === 512
        && document.getElementById(stage + '-viewport').getAttribute('aria-busy') === 'false';
    }, stage);
    await target.waitForLoadState('networkidle');
  };
  const frames = () => page.evaluate(() => Object.fromEntries(['pre', 'post'].map(stage => [stage, {
    src: document.getElementById(stage + '-image').src, index: document.getElementById(stage + '-index').value,
    counter: document.getElementById(stage + '-counter').textContent,
  }])));
  const camera = () => page.locator('#lesion-surface-canvas').getAttribute('data-camera');
  const waitCamera = value => page.waitForFunction(value => document.getElementById('lesion-surface-canvas').dataset.camera === value, value);
  const surfaceReady = async (target = page) => {
    await target.waitForFunction(() => {
      const dialog = document.getElementById('lesion-surface-dialog'), canvas = document.getElementById('lesion-surface-canvas');
      return dialog?.open && canvas && !canvas.hidden && canvas.dataset.camera && canvas.dataset.candidateId;
    });
  };
  const open = async () => { await page.locator('#candidate-surface').click(); await surfaceReady(); };
  const close = async () => {
    await page.locator('#lesion-surface-close').click();
    await page.waitForFunction(() => !document.getElementById('lesion-surface-dialog').open);
  };
  const pixels = () => page.locator('#lesion-surface-canvas').evaluate(canvas => {
    const gl = canvas.getContext('webgl2');
    const values = new Uint8Array(canvas.width * canvas.height * 4);
    gl.readPixels(0, 0, canvas.width, canvas.height, gl.RGBA, gl.UNSIGNED_BYTE, values);
    let colored = 0, checksum = 0;
    for (let i = 0; i < values.length; i += 4) {
      if (values[i + 1] > 40 && values[i + 1] > values[i] * 1.8 && values[i + 2] > values[i] * 1.8) colored++;
      checksum = (checksum + values[i + 1] * (i % 1031)) % 4294967291;
    }
    return {colored, checksum, glError: gl.getError(), width: canvas.width, height: canvas.height};
  });
  const selected = async () => {
    const id = await page.locator('#candidate-saved').inputValue();
    assert.match(id, /^[a-f0-9]{32}$/);
    const response = await page.request.get(url + '/api/candidates/' + id);
    assert.equal(response.ok(), true); return response.json();
  };
  try {
    for (const caseId of ['HCC_002', 'HCC_006']) {
      const initialRequests = requests.length;
      await page.goto(url + '/?case=' + caseId); await ready();
      await page.locator('#followup-tab').click();
      await page.waitForFunction(() => !document.getElementById('candidate-surface').disabled);
      const item = await selected(); assert.equal(item.case_id, caseId); assert.equal(item.status, 'unreviewed');
      for (const stage of ['pre', 'post']) { await page.locator('#' + stage + '-next').click(); await ready(); }
      assert.equal(requests.length, initialRequests, 'Loading and scrolling must not request a surface.');
      const before = await frames();
      const responsePromise = page.waitForResponse(response => response.url().endsWith('/' + item.id + '/surface'));
      await open();
      const response = await responsePromise, mesh = await response.json();
      assert.equal(response.status(), 200);
      for (const key of ['case_id', 'series_uid', 'stack_index', 'acquisition']) assert.equal(mesh[key], item[key]);
      assert.equal(mesh.candidate_id, item.id); assert.equal(mesh.review_status, 'unreviewed');
      assert.equal(mesh.mask_sha256, item.mask_sha256);
      assert.equal(mesh.stats.voxels, item.voxels); assert.equal(mesh.smoothing, false); assert.equal(mesh.decimation, false);
      assert.ok(Math.abs(mesh.stats.native_volume_cm3 - item.volume_cm3) < 1e-6);
      assert.equal(await page.locator('#lesion-surface-canvas').getAttribute('data-candidate-id'), item.id);
      assert.match(await page.locator('#lesion-surface-range').innerText(), /^Mask on slices \d+–\d+$/);
      assert.equal(await page.locator('#lesion-surface-boundary').isVisible(), mesh.acquisition_touch_faces.length > 0);
      assert.equal(await page.locator('#lesion-surface-axes text').count(), 6);
      assert.equal(await page.locator('#lesion-surface-axes').isVisible(), true);
      assert.equal(await page.locator('#lesion-surface-axes').evaluate(element => !element.hasAttribute('hidden')
        && getComputedStyle(element).display !== 'none' && element.getBoundingClientRect().width >= 100), true);
      const initial = await pixels(); assert.equal(initial.glError, 0); assert.ok(initial.colored > 500);
      const box = await page.locator('#lesion-surface-canvas').boundingBox();
      await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
      await page.mouse.down(); await page.mouse.move(box.x + box.width / 2 + 80, box.y + box.height / 2 + 25, {steps: 6}); await page.mouse.up();
      await page.waitForFunction(() => document.getElementById('lesion-surface-canvas').dataset.camera !== '-30,18,1');
      const rotated = await pixels(); assert.equal(rotated.glError, 0); assert.notEqual(rotated.checksum, initial.checksum);
      await page.locator('#lesion-surface-plus').click();
      await page.waitForFunction(() => document.getElementById('lesion-surface-zoom').textContent === '120%');
      await page.locator('#lesion-surface-reset').click(); await waitCamera('-30,18,1');
      for (const [name, expected] of [['anterior', '0,0,1'], ['posterior', '180,0,1'], ['left', '90,0,1']]) {
        await page.locator('[data-view=' + name + ']').click(); await waitCamera(expected);
        assert.equal(await page.locator('[data-view=' + name + ']').getAttribute('aria-pressed'), 'true');
      }
      await page.locator('#lesion-surface-canvas').focus(); await page.keyboard.press('ArrowRight'); await waitCamera('100,0,1');
      await page.keyboard.press('r'); await waitCamera('-30,18,1');
      assert.equal(await page.locator('[data-view][aria-pressed=true]').count(), 0);
      await page.locator('#lesion-surface-title').click();
      await page.locator('#lesion-surface-dialog').screenshot({path: path.join(output, caseId + '-surface-desktop.png')});
      await page.keyboard.press('Escape');
      await page.waitForFunction(() => !document.getElementById('lesion-surface-dialog').open);
      await page.waitForFunction(() => document.activeElement.id === 'candidate-surface');
      assert.deepEqual(await frames(), before);
      await open(); assert.equal((await pixels()).glError, 0); await close();
      cases.push({case_id: caseId, candidate_id: item.id, acquisition: item.acquisition, voxels: item.voxels,
        triangles: mesh.stats.triangles, initial, rotated});
    }
    checks.push('Both saved cases render real LPS voxel surfaces; identity, count, volume and six orientation labels agree.');
    checks.push('Lazy loading, pointer rotation, zoom, reset, three camera presets, keyboard rotation and Escape/focus pass.');
    checks.push('Closing/reopening releases the scene without changing either CT source or slice.');

    await page.setViewportSize({width: 390, height: 844}); await open();
    const mobile = await pixels(); assert.equal(mobile.glError, 0); assert.ok(mobile.colored > 100);
    assert.equal(await page.locator('#lesion-surface-axes').isVisible(), true);
    assert.equal(await page.locator('#lesion-surface-dialog').evaluate(element => element.scrollWidth <= element.clientWidth + 1), true);
    await page.locator('#lesion-surface-title').click();
    await page.locator('#lesion-surface-dialog').screenshot({path: path.join(output, 'HCC_006-surface-mobile.png')}); await close();
    await page.setViewportSize({width: 1600, height: 1100});
    checks.push('390 px mobile dialog renders without horizontal overflow.');

    // Hold a genuine API response, then switch case before releasing it.
    let releaseLate, intercepted;
    const held = new Promise(resolve => { intercepted = resolve; });
    const gate = new Promise(resolve => { releaseLate = resolve; });
    const lateRoute = async route => {
      const response = await route.fetch(); intercepted(); await gate;
      try { await route.fulfill({response}); } catch (_) { /* The closed request may already be aborted. */ }
    };
    await page.route('**/api/candidates/' + cases[1].candidate_id + '/surface', lateRoute);
    await page.locator('#candidate-surface').click(); await held;
    await page.keyboard.press('Escape');
    await page.locator('#case').selectOption('HCC_002'); await ready();
    await page.waitForFunction(() => !document.getElementById('candidate-surface').disabled);
    await open(); const activeId = await page.locator('#lesion-surface-canvas').getAttribute('data-candidate-id');
    releaseLate(); await page.waitForTimeout(150);
    assert.equal(activeId, cases[0].candidate_id);
    assert.equal(await page.locator('#lesion-surface-canvas').getAttribute('data-candidate-id'), activeId);
    assert.equal(await page.locator('#lesion-surface-dialog').evaluate(element => element.open), true);
    await close(); await page.unroute('**/api/candidates/' + cases[1].candidate_id + '/surface', lateRoute);
    checks.push('Closing aborts a pending request; its late response cannot replace a newly opened case.');

    await page.route('**/api/candidates/*/surface', route => route.fulfill({status: 503, contentType: 'application/json',
      body: JSON.stringify({error: 'Surface service unavailable for this transport test.'})}));
    await page.locator('#candidate-surface').click();
    await page.waitForFunction(() => document.getElementById('lesion-surface-message').textContent.includes('transport test'));
    assert.equal(await page.locator('#lesion-surface-canvas').isVisible(), false);
    assert.equal(await page.locator('#lesion-surface-plus').isDisabled(), true); await close();
    await page.unroute('**/api/candidates/*/surface');
    checks.push('An API failure displays its reason without retaining an old surface.');

    // Change only the transport fingerprint: identical IDs/counts must not bypass source checks.
    await page.route('**/api/candidates/*/surface', async route => {
      const response = await route.fetch(), body = await response.json();
      body.mask_sha256 = body.mask_sha256 === '0'.repeat(64) ? '1'.repeat(64) : '0'.repeat(64);
      await route.fulfill({response, json: body});
    });
    await page.locator('#candidate-surface').click();
    await page.waitForFunction(() => document.getElementById('lesion-surface-message').textContent.includes('does not match'));
    assert.equal(await page.locator('#lesion-surface-canvas').isVisible(), false);
    assert.equal(await page.locator('#lesion-surface-plus').isDisabled(), true); await close();
    await page.unroute('**/api/candidates/*/surface');
    checks.push('A mismatched mask fingerprint is rejected even when IDs, counts and volume match.');

    // Exercise only the warning presentation with explicitly injected metadata.
    // This does not claim that either real case touches the acquired field of view.
    await page.route('**/api/candidates/*/surface', async route => {
      const response = await route.fetch(), body = await response.json(); body.acquisition_touch_faces = ['z_min'];
      await route.fulfill({response, json: body});
    });
    await open();
    assert.equal(await page.locator('#lesion-surface-boundary').isVisible(), true);
    assert.equal(await page.locator('#lesion-surface-boundary').innerText(), 'Mask reaches the scan boundary.');
    await close(); await page.unroute('**/api/candidates/*/surface');
    await open(); assert.equal(await page.locator('#lesion-surface-boundary').isVisible(), false); await close();
    checks.push('Scan-boundary warning displays for injected diagnostic metadata and clears on the next real response.');

    const unsupported = await browser.newPage({viewport: {width: 1280, height: 900}}); await protect(unsupported);
    await unsupported.addInitScript(() => {
      const original = HTMLCanvasElement.prototype.getContext;
      HTMLCanvasElement.prototype.getContext = function (type, ...args) { return type === 'webgl2' ? null : original.call(this, type, ...args); };
    });
    await unsupported.goto(url + '/?case=HCC_002'); await ready(unsupported);
    await unsupported.locator('#followup-tab').click();
    await unsupported.waitForFunction(() => !document.getElementById('candidate-surface').disabled);
    await unsupported.locator('#candidate-surface').click();
    await unsupported.waitForFunction(() => document.getElementById('lesion-surface-message').textContent.includes('unavailable in this browser'));
    assert.equal(await unsupported.locator('#lesion-surface-canvas').isVisible(), false);
    await unsupported.locator('#lesion-surface-close').click(); await ready(unsupported); await unsupported.close();
    checks.push('WebGL unavailable state leaves both CT viewports operational.');
    assert.deepEqual(errors, []); assert.deepEqual(writes, []);
    await fs.writeFile(path.join(output, 'validation.json'), JSON.stringify({status: 'passed', checks, cases, mobile,
      browser_errors: errors, attempted_writes: writes, surface_requests: requests.length}, null, 2));
    console.log(JSON.stringify({status: 'passed', checks: checks.length, cases: cases.map(item => item.case_id), output}));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
