// Engineering checks on the public HCC_002 CT, not a lesion reference or clinical validation.
// Run only against an isolated annotation store, e.g. AXIVOL_URL=http://127.0.0.1:8771.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');

(async () => {
  const url = process.env.AXIVOL_URL;
  if (!url) throw new Error('Set AXIVOL_URL to an app with an isolated QA annotation store.');
  const out = path.resolve(process.env.AXIVOL_QA_DIR || 'output/ui-segmentation');
  await fs.mkdir(out, {recursive: true});
  const browser = await chromium.launch({headless: true});
  const page = await browser.newPage({viewport: {width: 1600, height: 1000}, acceptDownloads: true});
  page.setDefaultTimeout(30000);
  const errors = [], checks = [];
  page.on('pageerror', error => errors.push(error.message));
  const ready = stage => page.waitForFunction(s => {
    const img = document.getElementById(s + '-image');
    return img && !img.hidden && img.complete && img.naturalWidth > 0
      && document.getElementById(s + '-viewport').getAttribute('aria-busy') === 'false';
  }, stage);
  const input = (name, value) => page.locator('#candidate-' + name).fill(String(value));
  const setSlice = value => page.locator('#post-index').evaluate((el, n) => {
    el.value = n; el.dispatchEvent(new Event('input', {bubbles: true}));
  }, value);
  let first;
  try {
    await page.goto(url + '/?case=HCC_002');
    await ready('pre'); await ready('post');
    await page.waitForLoadState('networkidle');
    assert.equal(await page.locator('#candidate-lower').inputValue(), '');
    assert.equal(await page.locator('#candidate-generate').isDisabled(), true);
    const uid = await page.locator('#post-series').inputValue();
    const summary = await (await page.request.get(url + '/api/series/' + uid)).json();
    const stack = summary.stacks[0], [rows, columns] = stack.shape;
    // A small central tissue region exercises geometry without claiming a tumor annotation.
    const z = Math.floor(stack.slices / 2), y = Math.floor(rows / 2), x = Math.floor(columns / 2);
    await setSlice(z + 1); await ready('post');
    await page.locator('#post-zoom-in').click();
    await page.locator('#candidate-region-tool').click();
    assert.equal(await page.locator('#post-zoom').innerText(), '100%');
    assert.equal(await page.locator('#post-zoom-in').isDisabled(), true);
    const box = await page.locator('#post-viewport').boundingBox();
    const scale = Math.min(box.width / columns, box.height / rows);
    const point = (column, row) => ({x: box.x + (box.width - columns * scale) / 2 + (column + .5) * scale,
      y: box.y + (box.height - rows * scale) / 2 + (row + .5) * scale});
    const start = point(x - 4, y - 4), end = point(x + 4, y + 4);
    await page.mouse.move(start.x, start.y); await page.mouse.down();
    await page.mouse.move(end.x, end.y, {steps: 4}); await page.mouse.up();
    assert.equal(await page.locator('#candidate-x0').inputValue(), String(x - 3));
    assert.equal(await page.locator('#candidate-x1').inputValue(), String(x + 5));
    assert.equal(await page.locator('#candidate-y0').inputValue(), String(y - 3));
    assert.equal(await page.locator('#candidate-y1').inputValue(), String(y + 5));
    await page.locator('#candidate-seed-tool').click();
    // scrollIntoView can move the viewport: compute the point again after tool activation.
    const seedBox = await page.locator('#post-viewport').boundingBox();
    await page.mouse.click(seedBox.x + seedBox.width / 2 + scale / 2, seedBox.y + seedBox.height / 2 + scale / 2);
    assert.equal(await page.locator('#candidate-seed-z').inputValue(), String(z + 1));
    assert.equal(await page.locator('#candidate-seed-y').inputValue(), String(y + 1));
    assert.equal(await page.locator('#candidate-seed-x').inputValue(), String(x + 1));
    await page.waitForFunction(() => document.getElementById('candidate-seed-hu').textContent.includes('HU'));
    await input('first', z); await input('last', z + 2);
    // Deliberately broad HU range: tests mechanics and ROI boundary warnings, not segmentation accuracy.
    await input('lower', -1024); await input('upper', 3071);
    const responseEvent = page.waitForResponse(r => r.url().endsWith('/api/candidates') && r.request().method() === 'POST');
    await page.locator('#candidate-generate').click();
    const response = await responseEvent;
    assert.equal(response.status(), 201);
    first = await response.json();
    const request = response.request().postDataJSON();
    assert.deepEqual(request.seed, [z, y, x]);
    assert.deepEqual(request.roi, [[z - 1, y - 4, x - 4], [z + 2, y + 5, x + 5]]);
    assert.equal(first.status, 'unreviewed');
    assert.ok(first.voxels > 0 && first.voxels <= 243);
    await page.locator('#candidate-result').waitFor({state: 'visible'});
    await ready('post');
    assert.match(await page.locator('#candidate-status').innerText(), /Unreviewed/);
    assert.match(await page.locator('#candidate-source').innerText(), /HCC_002.*Acquisition 1/);
    assert.equal(await page.locator('#candidate-warnings').isVisible(), true);
    checks.push('Draw and seed map real CT pixels through contain geometry; API receives [z,y,x] and max-exclusive ROI.');
    const colors = () => page.locator('#post-image').evaluate(img => {
      const canvas = document.createElement('canvas'); canvas.width = img.naturalWidth; canvas.height = img.naturalHeight;
      const ctx = canvas.getContext('2d'); ctx.drawImage(img, 0, 0);
      const pixels = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
      let count = 0;
      for (let i = 0; i < pixels.length; i += 4) if (pixels[i] !== pixels[i + 1]) count++;
      return count;
    });
    assert.ok(await colors() > 0);
    await page.locator('#candidate-overlay').uncheck(); await ready('post'); assert.equal(await colors(), 0);
    await page.locator('#candidate-overlay').check(); await ready('post'); assert.ok(await colors() > 0);
    const report = await (await page.request.get(new URL(await page.locator('#report').getAttribute('href'), url).href)).json();
    assert.equal(report.post_candidate.id, first.id);
    assert.equal(report.post_candidate.status, 'unreviewed');
    assert.equal(report.post_segmentation_measurement, null);
    assert.equal(report.volume_change_percent, null);
    const downloadEvent = page.waitForEvent('download'); await page.locator('#candidate-export').click();
    const download = await downloadEvent, zip = await fs.readFile(await download.path());
    assert.equal(zip.subarray(0, 2).toString(), 'PK');
    assert.ok(zip.includes(Buffer.from('mask.npz')) && zip.includes(Buffer.from('metadata.json')));
    checks.push('Overlay changes POST pixels; report keeps candidate separate and response measurements null; ZIP exports mask and provenance.');
    await input('upper', 3000);
    assert.equal(await page.locator('#candidate-result').isHidden(), true);
    await ready('post'); assert.equal(await colors(), 0);
    assert.equal(new URL(await page.locator('#report').getAttribute('href'), url).searchParams.has('candidate'), false);
    await page.locator('#candidate-saved').selectOption(first.id); await ready('post');
    assert.equal(await page.locator('#candidate-upper').inputValue(), '3071');
    await page.reload(); await ready('pre'); await ready('post');
    await page.waitForFunction(id => document.getElementById('candidate-saved').value === id, first.id);
    checks.push('Editing parameters clears old overlay/export; saved versions restore frozen parameters after reload.');
    // Delay a real candidate response, edit parameters, then ensure the old result cannot replace the draft.
    let release, fetched;
    const routeHandler = async route => {
      if (route.request().method() !== 'POST') return route.continue();
      const actual = await route.fetch(); fetched = actual;
      await new Promise(resolve => { release = resolve; });
      await route.fulfill({response: actual});
    };
    await page.route('**/api/candidates', routeHandler);
    await input('upper', 3060); await page.locator('#candidate-generate').click();
    for (let attempt = 0; !fetched && attempt < 200; attempt++) await new Promise(r => setTimeout(r, 50));
    assert.ok(fetched, 'Candidate response did not arrive');
    await input('upper', 3050); release();
    await page.waitForFunction(() => document.getElementById('candidate-editor').getAttribute('aria-busy') === 'false');
    assert.equal(await page.locator('#candidate-result').isHidden(), true);
    assert.equal(await page.locator('#candidate-upper').inputValue(), '3050');
    await page.unroute('**/api/candidates', routeHandler);
    checks.push('Late generation response cannot overwrite parameters edited while the request was running.');
    const alternatives = await page.locator('#post-series option').evaluateAll(items => items.map(item => item.value));
    const otherUid = alternatives.find(item => item !== uid);
    if (otherUid) {
      await page.locator('#post .study-settings summary').click();
      await page.locator('#post-series').selectOption(otherUid); await ready('post');
      await page.waitForLoadState('networkidle');
      assert.equal(await page.locator('#candidate-result').isHidden(), true);
      assert.equal(await page.locator('#candidate-seed-z').inputValue(), '');
      assert.equal(await page.locator('#candidate-saved option').evaluateAll(items => items.some(item => item.value.length === 32)), false);
      await page.locator('#post-series').selectOption(uid); await ready('post');
      await page.waitForFunction(() => !document.getElementById('candidate-saved').disabled);
      await page.locator('#post .study-settings summary').click();
      checks.push('Switching follow-up series clears geometry, seed and candidate; saved versions remain tied to their source.');
    }
    await page.locator('#candidate-saved').selectOption(first.id); await ready('post');
    await page.waitForLoadState('networkidle');
    await page.evaluate(() => { document.activeElement?.blur(); window.scrollTo(0, 0); });
    await page.mouse.move(1, 1);
    await page.screenshot({path: path.join(out, 'candidate-desktop.png'), fullPage: true});
    for (const width of [1024, 390, 320]) {
      await page.setViewportSize({width, height: 844});
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, width + 'px overflow');
      await page.locator('#post-next').click(); await ready('post');
      await page.locator('#pre-next').click(); await ready('pre');
    }
    checks.push('Both viewports still scroll at desktop/tablet/mobile sizes; no horizontal overflow.');
    await page.waitForLoadState('networkidle');
    await page.evaluate(() => { document.activeElement?.blur(); window.scrollTo(0, 0); });
    await page.mouse.move(1, 1);
    await page.screenshot({path: path.join(out, 'candidate-mobile.png'), fullPage: true});
    assert.deepEqual(errors, []);
    await fs.writeFile(path.join(out, 'candidate-validation.json'), JSON.stringify({checks, case: 'HCC_002',
      series_uid: uid, acquisition: '1', candidate_id: first.id, parameters: first.parameters,
      scope: 'Engineering QA with a small central region and broad HU range. Not a tumor mask or clinical validation.'}, null, 2));
    console.log(JSON.stringify({passed: checks.length, checks}, null, 2));
  } catch (error) {
    await page.screenshot({path: path.join(out, 'candidate-failure.png'), fullPage: true});
    throw error;
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
