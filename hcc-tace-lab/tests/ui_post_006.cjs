// Generates one real unreviewed HCC_006 candidate in the selected local app.
// This is execution/display verification, not validation against a POST reference.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');

(async () => {
  const url = process.env.AXIVOL_URL;
  const output = process.env.AXIVOL_QA_DIR;
  if (!url || !output) throw new Error('Set AXIVOL_URL and AXIVOL_QA_DIR (local screenshots only).');
  await fs.mkdir(output, {recursive: true});
  const browser = await chromium.launch({headless: true});
  const page = await browser.newPage({viewport: {width: 1600, height: 1100}});
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.setDefaultTimeout(360000);
  const ready = stage => page.waitForFunction(s => {
    const image = document.getElementById(s + '-image');
    return image && !image.hidden && image.complete && image.naturalWidth > 0
      && document.getElementById(s + '-viewport').getAttribute('aria-busy') === 'false';
  }, stage);
  const colored = () => page.locator('#post-image').evaluate(image => {
    const canvas = document.createElement('canvas');
    canvas.width = image.naturalWidth; canvas.height = image.naturalHeight;
    const ctx = canvas.getContext('2d'); ctx.drawImage(image, 0, 0);
    const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
    let pixels = 0;
    for (let i = 0; i < data.length; i += 4) if (data[i] !== data[i + 1]) pixels++;
    return pixels;
  });
  try {
    await page.goto(url + '/?case=HCC_006');
    await page.waitForLoadState('networkidle'); await ready('pre'); await ready('post');
    await page.locator('#followup-tab').click();
    assert.match(await page.locator('#candidate-baseline').innerText(), /Published Mass \(2\).*Acquisition 3/);
    const pre = await page.locator('#pre-series').inputValue();
    const post = await page.locator('#post-series').inputValue();
    assert.equal(pre, '1.3.6.1.4.1.14519.5.2.1.1706.8374.169644075146766664505907245033');
    assert.equal(post, '1.3.6.1.4.1.14519.5.2.1.1706.8374.282486593875087229522187699711');
    const response = page.waitForResponse(r => r.url().endsWith('/api/automatic-post-candidates'), {timeout: 360000});
    const started = Date.now();
    await page.locator('#candidate-automatic').click();
    await page.waitForFunction(() => document.getElementById('candidate-editor').getAttribute('aria-busy') === 'true');
    const navigation = {};
    for (const stage of ['pre', 'post']) {
      const t = Date.now();
      await page.locator('#' + stage + '-next').click(); await ready(stage);
      navigation[stage + '_ms'] = Date.now() - t;
      assert.ok(navigation[stage + '_ms'] < 5000);
    }
    const actual = await response, result = await actual.json();
    assert.equal(actual.status(), 201, JSON.stringify(result));
    assert.equal(result.case_id, 'HCC_006');
    assert.equal(result.status, 'unreviewed');
    assert.equal(result.acquisition, '1');
    assert.equal(result.baseline_source.source_segment_label, 'Mass');
    assert.equal(result.baseline_source.acquisition, '3');
    assert.equal(result.baseline_source.series_uid, pre);
    assert.equal(result.series_uid, post);
    assert.ok(result.voxels > 0);
    await page.waitForFunction(() => document.getElementById('candidate-editor').getAttribute('aria-busy') === 'false');
    await ready('post');
    assert.equal(await page.locator('#post-mask-state').innerText(), 'Automatic mask · Unreviewed');
    assert.equal(Number(await page.locator('#post-index').inputValue()), result.diagnostics.segmentation.display_slice_index + 1);
    const visiblePixels = await colored(); assert.ok(visiblePixels > 0);
    await page.locator('#post-mask-action').click(); await ready('post'); assert.equal(await colored(), 0);
    await page.locator('#post-mask-action').click(); await ready('post'); assert.equal(await colored(), visiblePixels);
    await page.reload(); await page.waitForLoadState('networkidle'); await ready('pre'); await ready('post');
    assert.equal(await page.locator('#candidate-saved').inputValue(), result.id);
    assert.ok(await colored() > 0);
    for (const stage of ['pre', 'post']) {
      const index = await page.locator('#' + stage + '-index').inputValue();
      await page.locator('#' + stage + '-next').click(); await ready(stage);
      await page.locator('#' + stage + '-prev').click(); await ready(stage);
      assert.equal(await page.locator('#' + stage + '-index').inputValue(), index);
    }
    await page.screenshot({path: path.join(output, 'HCC006-automatic-local.png'), fullPage: true});
    assert.deepEqual(errors, []);
    const report = {case_id: 'HCC_006', candidate_id: result.id, elapsed_ms: Date.now() - started,
      navigation, visible_pixels: visiblePixels, voxels: result.voxels, method: result.method,
      checks: ['real automatic generation', 'exact PRE Mass/acquisition and POST acquisition',
        'visible native overlay', 'hide/show', 'reload restoration', 'both viewports navigate'],
      scope: 'Execution and display only; no independent POST reference.'};
    await fs.writeFile(path.join(output, 'validation.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report));
  } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exit(1);});
