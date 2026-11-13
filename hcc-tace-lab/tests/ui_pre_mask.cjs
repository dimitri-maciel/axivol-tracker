// Public HCC_002 engineering QA. These gestures test mask editing, not anatomy.
// Require an isolated annotation store; never run against the user's saved work.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const {createHash} = require('node:crypto');

(async () => {
  const url = process.env.AXIVOL_URL;
  if (!url) throw new Error('Set AXIVOL_URL to the isolated QA app.');
  const out = path.resolve(process.env.AXIVOL_QA_DIR || 'output/ui-pre-mask');
  await fs.mkdir(out, {recursive: true});
  const browser = await chromium.launch({headless: true});
  const page = await browser.newPage({viewport: {width: 1600, height: 1100}, acceptDownloads: true});
  page.setDefaultTimeout(30000);
  const errors = [], checks = [];
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
  const canvasReady = () => page.waitForFunction(() => {
    const canvas = document.getElementById('pre-edit-canvas');
    return !canvas.hidden && canvas.width === 512 && !document.getElementById('pre-edit-hint').textContent.startsWith('Loading');
  });
  const previewMask = () => page.locator('#pre-edit-canvas').evaluate(canvas => {
    const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
    return Array.from({length: canvas.width * canvas.height}, (_, i) => pixels[i * 4 + 3] > 0 ? 1 : 0);
  });
  const brush = async (tool, points) => {
    await page.locator('#pre-edit-' + tool).click(); await ready('pre'); await canvasReady();
    const box = await page.locator('#pre-viewport').boundingBox(), scale = Math.min(box.width, box.height) / 512;
    const screen = ([y, x]) => [box.x + (box.width - 512 * scale) / 2 + (x + .5) * scale,
      box.y + (box.height - 512 * scale) / 2 + (y + .5) * scale];
    await page.mouse.move(...screen(points[0])); await page.mouse.down();
    for (const point of points.slice(1)) await page.mouse.move(...screen(point));
    await page.mouse.up();
  };
  try {
    await page.goto(url + '/?case=HCC_002'); await ready('pre'); await ready('post');
    await page.waitForLoadState('networkidle');
    await page.locator('#baseline-tab').click();
    await page.locator('#overlay-display > summary').click();
    const anisotropic = await page.evaluate(async () => {
      const {applyBrush} = await import('/pre-mask-editor.js');
      const mask = new Uint8Array(81);
      applyBrush(mask, 9, 9, [2, .5], {mode: 'paint', radius_mm: 1, points: [[4, 4]]});
      return Array.from(mask.entries()).filter(([, value]) => value).map(([index]) => index);
    });
    assert.deepEqual(anisotropic, [38, 39, 40, 41, 42], 'Physical radius must respect anisotropic row/column spacing.');
    assert.equal(await page.locator('#pre-mask-style').inputValue(), 'combined');
    assert.equal(await page.locator('#pre-mask-opacity').inputValue(), '15');
    assert.equal(await page.locator('#pre-edit-saved').inputValue(), '');
    await setSlice(76);
    const uid = await page.locator('#pre-series').inputValue(), segment = Number(await page.locator('#segment').inputValue());
    const maskUrl = '/api/series/' + uid + '/mask?stack=0&index=75&segment=' + segment;
    const originalBytes = await (await page.request.get(url + maskUrl)).body();
    const originalHash = createHash('sha256').update(originalBytes).digest('hex');
    const original = await page.evaluate(async endpoint => {
      const image = await createImageBitmap(await (await fetch(endpoint)).blob());
      const canvas = document.createElement('canvas'); canvas.width = image.width; canvas.height = image.height;
      const context = canvas.getContext('2d'); context.drawImage(image, 0, 0); image.close();
      const data = context.getImageData(0, 0, canvas.width, canvas.height).data;
      return Array.from({length: canvas.width * canvas.height}, (_, i) => data[i * 4] ? 1 : 0);
    }, maskUrl);
    const foreground = original.reduce((sum, n) => sum + n, 0);
    assert.ok(foreground > 100);
    const colored = () => page.locator('#pre-image').evaluate(image => {
      const canvas = document.createElement('canvas'); canvas.width = image.naturalWidth; canvas.height = image.naturalHeight;
      const context = canvas.getContext('2d'); context.drawImage(image, 0, 0);
      const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
      let count = 0;
      for (let i = 0; i < pixels.length; i += 4) if (pixels[i] !== pixels[i + 1]) count++;
      return count;
    });
    await page.locator('#pre-mask-style').selectOption('outline'); await ready('pre');
    const boundaryCount = await colored(); assert.ok(boundaryCount > 0 && boundaryCount < foreground);
    await page.locator('#pre-mask-style').selectOption('fill'); await ready('pre');
    await page.locator('#pre-mask-opacity').evaluate(input => {
      input.value = 0; input.dispatchEvent(new Event('input', {bubbles: true})); input.dispatchEvent(new Event('change', {bubbles: true}));
    }); await ready('pre'); assert.equal(await colored(), 0);
    await page.locator('#pre-mask-opacity').evaluate(input => {
      input.value = 15; input.dispatchEvent(new Event('input', {bubbles: true})); input.dispatchEvent(new Event('change', {bubbles: true}));
    });
    await page.locator('#pre-mask-style').selectOption('combined'); await ready('pre');
    checks.push('PRE defaults to inner outline + 15% fill; Outline and 0% Fill preserve the CT beneath.');
    await page.evaluate(() => { document.activeElement?.blur(); window.scrollTo(0, 0); }); await page.mouse.move(1, 1);
    await page.screenshot({path: path.join(out, 'pre-mask-published.png'), fullPage: true});
    await page.locator('#pre-edit-panel summary').click();
    assert.equal(await page.locator('#pre-edit-save').isDisabled(), true);
    await page.locator('#pre-zoom-in').click();
    await page.locator('#pre-edit-paint').click(); await ready('pre'); await canvasReady();
    assert.equal(await page.locator('#pre-zoom').innerText(), '100%');
    assert.equal(await page.locator('#pre-zoom-in').isDisabled(), true);
    assert.equal(await colored(), 0, 'Editable view must use unsegmented CT beneath the canvas.');
    assert.deepEqual(await previewMask(), original);
    await page.locator('#show-mask').uncheck();
    await page.locator('#pre-viewport').click({position: {x: 150, y: 150}});
    assert.equal(await page.locator('#pre-edit-count').textContent(), 'No edits');
    assert.match(await page.locator('#pre-edit-hint').innerText(), /Show overlay to edit/);
    await page.locator('#show-mask').check(); await canvasReady();
    assert.deepEqual(await previewMask(), original);
    let eraseIndex = -1;
    for (let y = 5; y < 507 && eraseIndex < 0; y++) for (let x = 5; x < 507; x++) {
      const i = y * 512 + x;
      if (original[i] && original[i - 5] && original[i + 5] && original[i - 5 * 512] && original[i + 5 * 512]) { eraseIndex = i; break; }
    }
    assert.ok(eraseIndex >= 0);
    const erasePoint = [Math.floor(eraseIndex / 512), eraseIndex % 512];
    await brush('erase', [erasePoint]);
    const erased = await previewMask(); assert.ok(erased.reduce((n, v) => n + v, 0) < foreground);
    await page.locator('#pre-edit-stop').click(); await ready('pre');
    assert.match(await page.locator('#pre-edit-message').innerText(), /Editing paused.*Unsaved edits are preserved/);
    assert.match(await page.locator('#pre-overlay-label').innerText(), /Published Mass/);
    await page.locator('#pre-edit-erase').click(); await ready('pre'); await canvasReady();
    assert.deepEqual(await previewMask(), erased, 'Pausing must preserve the unsaved strokes.');
    await page.locator('#pre-edit-undo').click(); assert.deepEqual(await previewMask(), original);
    const paintPoint = [256, 256];
    assert.equal(original[256 * 512 + 256], 0);
    await brush('paint', [paintPoint, [258, 261]]);
    assert.ok((await previewMask()).reduce((n, v) => n + v, 0) > foreground);
    await page.locator('#pre-edit-clear').click(); assert.deepEqual(await previewMask(), original);
    assert.equal(await page.locator('#pre-edit-save').isDisabled(), true);
    checks.push('Editing uses published binary mask over clean CT; Paint, Erase, Undo and Clear affect only explicit strokes.');
    await brush('erase', [erasePoint]);
    await brush('paint', [paintPoint, [258, 261]]);
    const preview = await previewMask();
    const responseEvent = page.waitForResponse(r => r.url().endsWith('/api/pre-edits') && r.request().method() === 'POST');
    await page.locator('#pre-edit-save').click();
    const response = await responseEvent; assert.equal(response.status(), 201);
    const edited = await response.json();
    assert.equal(edited.status, 'unreviewed'); assert.equal(edited.stage, 'PRE');
    assert.equal(edited.source_segment, segment); assert.equal(edited.source_segment_label, 'Mass');
    assert.equal(response.request().postDataJSON().strokes.length, 2);
    await ready('pre');
    assert.match(await page.locator('#pre-overlay-label').innerText(), /Edited copy.*Unreviewed/);
    const stored = await page.evaluate(async endpoint => {
      const image = await createImageBitmap(await (await fetch(endpoint)).blob());
      const canvas = document.createElement('canvas'); canvas.width = image.width; canvas.height = image.height;
      const context = canvas.getContext('2d'); context.drawImage(image, 0, 0); image.close();
      const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
      return Array.from({length: canvas.width * canvas.height}, (_, i) => pixels[i * 4] > 0 ? 1 : 0);
    }, '/api/series/' + uid + '/mask?stack=0&index=75&candidate=' + edited.id);
    assert.deepEqual(stored, preview, 'Physical brush preview must match persisted mask pixel for pixel.');
    const afterBytes = await (await page.request.get(url + maskUrl)).body();
    assert.equal(createHash('sha256').update(afterBytes).digest('hex'), originalHash);
    await page.locator('#measure').click();
    await page.waitForFunction(() => document.getElementById('volume-value').textContent === '495.04');
    assert.match(await page.locator('#measure-title').innerText(), /Published/);
    assert.equal(await page.locator('#source-segment').innerText(), 'Mass (2)');
    const report = await (await page.request.get(new URL(await page.locator('#report').getAttribute('href'), url).href)).json();
    assert.equal(report.pre_edited_candidate.id, edited.id);
    assert.equal(report.pre_segmentation_measurement.volume_cm3, 495.0408935546875);
    assert.equal(report.post_segmentation_measurement, null);
    assert.equal(report.volume_change_percent, null);
    const downloadEvent = page.waitForEvent('download'); await page.locator('#pre-edit-export').click();
    const zip = await fs.readFile(await (await downloadEvent).path());
    assert.equal(zip.subarray(0, 2).toString(), 'PK');
    assert.ok(zip.includes(Buffer.from('mask.npz')) && zip.includes(Buffer.from('metadata.json')));
    checks.push('Saved PRE copy matches browser preview pixel-for-pixel, exports ZIP, and leaves published PNG and 495.04 cm³ measurement unchanged.');
    await page.locator('#pre-edit-saved').selectOption(''); await ready('pre');
    assert.match(await page.locator('#pre-overlay-label').innerText(), /Published Mass/);
    await page.reload(); await ready('pre'); await ready('post'); await page.waitForLoadState('networkidle');
    assert.equal(await page.locator('#pre-edit-saved').inputValue(), '');
    await page.locator('#pre-edit-saved').selectOption(edited.id); await ready('pre');
    await setSlice(76);
    await page.locator('#pre .study-settings summary').click();
    await page.locator('#pre-stack').selectOption('1'); await ready('pre'); await page.waitForLoadState('networkidle');
    assert.equal(await page.locator('#pre-edit-saved').inputValue(), '');
    assert.equal(await page.locator('#pre-edit-result').isHidden(), true);
    await page.locator('#pre-stack').selectOption('0'); await ready('pre'); await page.waitForLoadState('networkidle');
    await page.locator('#pre-edit-saved').selectOption(edited.id); await ready('pre'); await setSlice(76);
    await page.locator('#pre .study-settings summary').click();
    checks.push('Saved copies remain tied to case/acquisition/segment; reload defaults to the published mask and permits explicit preview.');
    // Hold a real save response while the user changes acquisition.
    let releaseSave, saveFetched;
    const delaySave = async route => {
      if (route.request().method() !== 'POST') return route.continue();
      const actual = await route.fetch(); saveFetched = actual;
      await new Promise(resolve => { releaseSave = resolve; });
      await route.fulfill({response: actual});
    };
    await page.route('**/api/pre-edits', delaySave);
    await page.locator('#pre-edit-panel summary').click();
    await brush('erase', [erasePoint]);
    const lateSaveEvent = page.waitForResponse(r => r.url().endsWith('/api/pre-edits') && r.request().method() === 'POST');
    await page.locator('#pre-edit-save').click();
    for (let n = 0; !saveFetched && n < 300; n++) await new Promise(resolve => setTimeout(resolve, 50));
    assert.ok(saveFetched, 'Held save response did not arrive');
    await page.locator('#pre .study-settings summary').click();
    await page.locator('#pre-stack').selectOption('1'); await ready('pre');
    releaseSave(); await lateSaveEvent; await page.waitForLoadState('networkidle');
    assert.equal(await page.locator('#pre-edit-saved').inputValue(), '');
    assert.equal(await page.locator('#pre-edit-result').isHidden(), true);
    assert.equal(await page.locator('#pre-edit-count').textContent(), 'No edits');
    await page.unroute('**/api/pre-edits', delaySave);
    checks.push('A delayed PRE save cannot select an edited copy after the acquisition changes.');
    // Hold an old list, save a new version, then deliver the old list.
    let releaseList, listFetched;
    const delayList = async route => {
      if (new URL(route.request().url()).searchParams.get('stack') !== '0') return route.continue();
      const actual = await route.fetch(); listFetched = actual;
      await new Promise(resolve => { releaseList = resolve; });
      await route.fulfill({response: actual});
    };
    await page.route('**/api/pre-edits?**', delayList);
    await page.locator('#pre-stack').selectOption('0'); await ready('pre'); await setSlice(76);
    for (let n = 0; !listFetched && n < 300; n++) await new Promise(resolve => setTimeout(resolve, 50));
    assert.ok(listFetched, 'Held list response did not arrive');
    await page.locator('#pre-edit-panel summary').click();
    await brush('paint', [paintPoint]);
    const newSaveEvent = page.waitForResponse(r => r.url().endsWith('/api/pre-edits') && r.request().method() === 'POST');
    await page.locator('#pre-edit-save').click();
    const newest = await (await newSaveEvent).json(); await ready('pre');
    assert.equal(await page.locator('#pre-edit-saved').inputValue(), newest.id);
    releaseList(); await page.waitForLoadState('networkidle');
    assert.equal(await page.locator('#pre-edit-saved').inputValue(), newest.id);
    assert.equal(await page.locator('#pre-edit-result').isVisible(), true);
    await page.unroute('**/api/pre-edits?**', delayList);
    await page.locator('#pre-edit-saved').selectOption(edited.id); await ready('pre');
    await page.locator('#pre .study-settings summary').click();
    checks.push('A delayed initial list cannot remove or deselect a newly saved edited copy.');
    await page.waitForLoadState('networkidle');
    await page.evaluate(() => { document.activeElement?.blur(); window.scrollTo(0, 0); }); await page.mouse.move(1, 1);
    await page.screenshot({path: path.join(out, 'pre-mask-desktop.png'), fullPage: true});
    for (const width of [1024, 390, 320]) {
      await page.setViewportSize({width, height: 844});
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      await page.locator('#pre-next').click(); await ready('pre');
      await page.locator('#post-next').click(); await ready('post');
    }
    await page.evaluate(() => { document.activeElement?.blur(); window.scrollTo(0, 0); }); await page.mouse.move(1, 1);
    await page.screenshot({path: path.join(out, 'pre-mask-mobile.png'), fullPage: true});
    assert.deepEqual(errors, []);
    checks.push('Both real viewports remain usable at desktop, tablet and mobile widths without overflow or browser errors.');
    await fs.writeFile(path.join(out, 'pre-mask-validation.json'), JSON.stringify({checks, case: 'HCC_002', acquisition: '1', segment: {number: segment, label: 'Mass'}, candidate_id: edited.id,
      original_mask_sha256: originalHash, scope: 'Engineering-only gestures; not anatomical correction or clinical review.'}, null, 2));
    console.log(JSON.stringify({passed: checks.length, checks}, null, 2));
  } catch (error) { await page.screenshot({path: path.join(out, 'pre-mask-failure.png'), fullPage: true}); throw error; }
  finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
