// Real HCC_002 engineering QA, not a statement of segmentation accuracy.
// Run against an isolated annotation store via AXIVOL_URL.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const {createHash} = require('node:crypto');

(async () => {
  const url = process.env.AXIVOL_URL;
  if (!url) throw new Error('Set AXIVOL_URL to an isolated QA app.');
  const out = path.resolve(process.env.AXIVOL_QA_DIR || 'output/ui-refinement');
  await fs.mkdir(out, {recursive: true});
  const browser = await chromium.launch({headless: true});
  const page = await browser.newPage({viewport: {width: 1600, height: 1100}, acceptDownloads: true});
  page.setDefaultTimeout(60000);
  const checks = [], errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const ready = stage => page.waitForFunction(s => {
    const image = document.getElementById(s + '-image');
    return image && !image.hidden && image.complete && image.naturalWidth > 0
      && document.getElementById(s + '-viewport').getAttribute('aria-busy') === 'false';
  }, stage);
  const setSlice = async n => {
    await page.locator('#pre-index').evaluate((input, value) => {
      input.value = value; input.dispatchEvent(new Event('input', {bubbles: true}));
    }, n); await ready('pre');
  };
  const binaryMask = endpoint => page.evaluate(async endpoint => {
    const response = await fetch(endpoint);
    if (!response.ok) throw new Error('Mask request failed: ' + response.status);
    const image = await createImageBitmap(await response.blob());
    const canvas = document.createElement('canvas'); canvas.width = image.width; canvas.height = image.height;
    const context = canvas.getContext('2d'); context.drawImage(image, 0, 0); image.close();
    const rgba = context.getImageData(0, 0, canvas.width, canvas.height).data;
    return Array.from({length: canvas.width * canvas.height}, (_, i) => rgba[4 * i] > 0 ? 1 : 0);
  }, endpoint);
  const signature = () => page.evaluate(() => ({slice: document.getElementById('pre-index').value,
    zoom: document.getElementById('pre-zoom').textContent, level: document.getElementById('level').value,
    width: document.getElementById('width').value, opacity: document.getElementById('pre-mask-opacity').value,
    style: document.getElementById('pre-mask-style').value}));
  let candidate;
  try {
    await page.goto(url + '/?case=HCC_002'); await ready('pre'); await ready('post');
    await page.waitForLoadState('networkidle');
    assert.equal(await page.locator('#pre-edit-band').inputValue(), '3');
    await page.locator('#pre-edit-band').fill('0');
    assert.equal(await page.locator('#pre-edit-refine').isDisabled(), true);
    assert.equal(await page.locator('#pre-edit-band').getAttribute('aria-invalid'), 'true');
    await page.locator('#pre-edit-band').fill('6');
    assert.equal(await page.locator('#pre-edit-refine').isDisabled(), true);
    await page.locator('#pre-edit-band').fill('3');
    await setSlice(76);
    const uid = await page.locator('#pre-series').inputValue(), segment = Number(await page.locator('#segment').inputValue());
    const maskUrl = '/api/series/' + uid + '/mask?stack=0&index=75&segment=' + segment;
    const originalBytes = await (await page.request.get(url + maskUrl)).body();
    const originalHash = createHash('sha256').update(originalBytes).digest('hex');
    const original = await binaryMask(maskUrl);
    // A temporary manual gesture must block refinement, including when paused.
    await page.locator('#pre-edit-panel summary').click(); await page.locator('#pre-edit-paint').click(); await ready('pre');
    await page.waitForFunction(() => !document.getElementById('pre-edit-canvas').hidden);
    const box = await page.locator('#pre-viewport').boundingBox();
    await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
    assert.equal(await page.locator('#pre-edit-refine').isDisabled(), true);
    assert.match(await page.locator('#pre-edit-refine-hint').innerText(), /Save or clear/);
    await page.locator('#pre-edit-stop').click(); await ready('pre');
    assert.equal(await page.locator('#pre-edit-refine').isDisabled(), true);
    await page.locator('#pre-edit-clear').click();
    assert.equal(await page.locator('#pre-edit-refine').isEnabled(), true);
    await page.locator('#pre-edit-panel summary').click();
    checks.push('Band defaults to 3 mm and enforces 1–5 mm; active and paused manual drafts block refinement until cleared or saved.');
    const responseEvent = page.waitForResponse(r => r.url().endsWith('/api/pre-refinements') && r.request().method() === 'POST');
    await page.locator('#pre-edit-refine').click();
    const response = await responseEvent; assert.equal(response.status(), 201);
    candidate = await response.json();
    assert.deepEqual(response.request().postDataJSON(), {case_id: 'HCC_002', series_uid: uid, stack_index: 0, segment, band_mm: 3});
    assert.equal(candidate.stage, 'PRE'); assert.equal(candidate.status, 'unreviewed');
    assert.equal(candidate.method.name, 'boundary_graph_cut');
    assert.equal(candidate.parameters.band_mm, 3);
    const diagnostics = candidate.diagnostics;
    await ready('pre');
    assert.equal(await page.locator('#pre-edit-saved').inputValue(), candidate.id);
    assert.match(await page.locator('#pre-edit-state').innerText(), /Refined copy.*Unreviewed/);
    assert.match(await page.locator('#pre-overlay-label').innerText(), /Refined copy.*Unreviewed/);
    assert.equal(await page.locator('#pre-edit-added').innerText(), diagnostics.added_voxels.toLocaleString('en-US') + ' voxels');
    assert.equal(await page.locator('#pre-edit-removed').innerText(), diagnostics.removed_voxels.toLocaleString('en-US') + ' voxels');
    assert.equal(await page.locator('#pre-edit-changed-slices').innerText(), String(diagnostics.changed_slices.length));
    if (diagnostics.added_voxels + diagnostics.removed_voxels === 0) {
      assert.match(await page.locator('#pre-edit-message').innerText(), /No boundary changes|No changes were made/);
    }
    // Old result parameters remain visible when preparing a different run.
    await page.locator('#pre-edit-band').fill('2');
    assert.match(await page.locator('#pre-edit-band-used').innerText(), /3 mm/);
    await page.locator('#pre-edit-band').fill('3');
    const z = diagnostics.changed_slices[0] ?? 75;
    const base = await binaryMask('/api/series/' + uid + '/mask?stack=0&index=' + z + '&segment=' + segment);
    const refined = await binaryMask('/api/series/' + uid + '/mask?stack=0&index=' + z + '&candidate=' + candidate.id);
    let added = 0, removed = 0;
    for (let i = 0; i < base.length; i++) { if (!base[i] && refined[i]) added++; if (base[i] && !refined[i]) removed++; }
    assert.ok(added <= diagnostics.added_voxels && removed <= diagnostics.removed_voxels);
    if (diagnostics.changed_slices.length) assert.ok(added + removed > 0);
    else assert.deepEqual(base, refined);
    checks.push('Real graph-cut result is explicitly unreviewed; change counts and a changed slice agree with exported mask data, with frozen band provenance.');
    await setSlice(76); await page.locator('#pre-zoom-in').click();
    const before = await signature();
    await page.locator('#pre-edit-saved').selectOption(''); await ready('pre');
    assert.match(await page.locator('#pre-overlay-label').innerText(), /Published Mass/);
    assert.deepEqual(await signature(), before);
    await page.locator('#pre-edit-saved').selectOption(candidate.id); await ready('pre');
    assert.deepEqual(await signature(), before);
    const afterBytes = await (await page.request.get(url + maskUrl)).body();
    assert.equal(createHash('sha256').update(afterBytes).digest('hex'), originalHash);
    await page.locator('#measure').click();
    await page.waitForFunction(() => document.getElementById('volume-value').textContent === '495.04');
    const report = await (await page.request.get(new URL(await page.locator('#report').getAttribute('href'), url).href)).json();
    assert.equal(report.pre_segmentation_measurement.volume_cm3, 495.0408935546875);
    assert.equal(report.pre_edited_candidate.id, candidate.id);
    assert.equal(report.volume_change_percent, null); assert.equal(report.post_segmentation_measurement, null);
    const downloadEvent = page.waitForEvent('download'); await page.locator('#pre-edit-export').click();
    const zip = await fs.readFile(await (await downloadEvent).path());
    assert.equal(zip.subarray(0, 2).toString(), 'PK'); assert.ok(zip.includes(Buffer.from('metadata.json')));
    checks.push('Original/refined comparison preserves slice and display settings; source hash and published volume stay unchanged; ZIP/report retain separate provenance.');
    // Entering manual editing from a refined preview must load the original.
    await page.locator('#pre-edit-panel summary').click(); await page.locator('#pre-edit-paint').click(); await ready('pre');
    await page.waitForFunction(() => !document.getElementById('pre-edit-canvas').hidden);
    const manualBase = await page.locator('#pre-edit-canvas').evaluate(canvas => {
      const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
      return Array.from({length: canvas.width * canvas.height}, (_, i) => pixels[i * 4 + 3] > 0 ? 1 : 0);
    });
    assert.deepEqual(manualBase, original);
    await page.locator('#pre-edit-stop').click(); await ready('pre');
    await page.locator('#pre-edit-panel summary').click();
    checks.push('Manual editing started from a refined preview still loads the published source, never chains onto the refined candidate.');
    // Delay a real refinement result while changing acquisition.
    let held, release;
    const handler = async route => {
      const actual = await route.fetch(); held = actual;
      await new Promise(resolve => { release = resolve; });
      await route.fulfill({response: actual});
    };
    await page.route('**/api/pre-refinements', handler);
    await page.locator('#pre-edit-band').fill('1');
    const delayed = page.waitForResponse(r => r.url().endsWith('/api/pre-refinements') && r.request().method() === 'POST');
    await page.locator('#pre-edit-refine').click();
    for (let n = 0; !held && n < 1200; n++) await new Promise(resolve => setTimeout(resolve, 50));
    assert.ok(held, 'Refinement did not finish within 60 seconds');
    assert.equal(await page.locator('#pre-edit-refine').innerText(), 'Refining…');
    const priorSlice = Number(await page.locator('#pre-index').inputValue());
    await page.locator('#pre-next').click(); await ready('pre');
    assert.equal(Number(await page.locator('#pre-index').inputValue()), priorSlice + 1);
    await page.locator('#post-next').click(); await ready('post');
    assert.equal(await page.locator('#pre-edit-refine').innerText(), 'Refining…');
    await page.locator('#pre .study-settings summary').click();
    await page.locator('#pre-stack').selectOption('1'); await ready('pre');
    release(); await delayed; await page.waitForLoadState('networkidle');
    assert.equal(await page.locator('#pre-edit-saved').inputValue(), '');
    assert.equal(await page.locator('#pre-edit-result').isHidden(), true);
    await page.unroute('**/api/pre-refinements', handler);
    await page.locator('#pre-stack').selectOption('0'); await ready('pre'); await page.waitForLoadState('networkidle');
    await page.locator('#pre-edit-saved').selectOption(candidate.id); await ready('pre'); await setSlice(76);
    await page.locator('#pre .study-settings summary').click();
    checks.push('Both viewports remain navigable while refinement is pending; late completion cannot select a candidate from a previous acquisition.');
    // Transport failure is injected; patient images and candidate data remain real.
    const failed = route => route.fulfill({status: 422, contentType: 'application/json', body: '{"error":"QA refinement unavailable"}'});
    await page.route('**/api/pre-refinements', failed);
    await page.locator('#pre-edit-refine').click();
    await page.waitForFunction(() => document.getElementById('pre-edit-message').textContent === 'QA refinement unavailable');
    await ready('pre');
    assert.equal(await page.locator('#pre-edit-refine').isEnabled(), true);
    assert.equal(await page.locator('#pre-edit-result').isHidden(), true);
    assert.equal(await page.locator('#pre-edit-saved').inputValue(), '');
    await page.unroute('**/api/pre-refinements', failed);
    await page.locator('#pre-edit-saved').selectOption(candidate.id); await ready('pre');
    checks.push('Refinement failure leaves the published mask visible, clears pending status and allows retry or selection of a saved copy.');
    await page.locator('#measure').click();
    await page.waitForFunction(() => document.getElementById('volume-value').textContent === '495.04');
    await page.waitForLoadState('networkidle');
    await page.evaluate(() => { document.activeElement?.blur(); window.scrollTo(0, 0); }); await page.mouse.move(1, 1);
    await page.screenshot({path: path.join(out, 'refinement-desktop.png'), fullPage: true});
    for (const width of [1024, 390, 320]) {
      await page.setViewportSize({width, height: 844});
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      await page.locator('#pre-next').click(); await ready('pre'); await page.locator('#post-next').click(); await ready('post');
    }
    await page.evaluate(() => { document.activeElement?.blur(); window.scrollTo(0, 0); }); await page.mouse.move(1, 1);
    await page.screenshot({path: path.join(out, 'refinement-mobile.png'), fullPage: true});
    assert.deepEqual(errors, []);
    checks.push('Both viewports remain usable at desktop/tablet/mobile widths without overflow or browser errors.');
    await fs.writeFile(path.join(out, 'refinement-validation.json'), JSON.stringify({checks, case: 'HCC_002', acquisition: '1',
      source_segment: segment, source_segment_label: 'Mass', candidate_id: candidate.id, parameters: candidate.parameters,
      diagnostics: candidate.diagnostics, original_mask_sha256: originalHash,
      scope: 'Functional and provenance QA of an unreviewed refinement. No claim of anatomical improvement or clinical accuracy.'}, null, 2));
    console.log(JSON.stringify({passed: checks.length, checks}, null, 2));
  } catch (error) { await page.screenshot({path: path.join(out, 'refinement-failure.png'), fullPage: true}); throw error; }
  finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
