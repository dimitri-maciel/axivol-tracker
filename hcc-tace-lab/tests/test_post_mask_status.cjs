// Existing real masks only. The Generate action receives an intercepted transport
// error; this test never runs inference or writes an annotation to the server.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');

(async () => {
  const url = process.env.AXIVOL_URL;
  if (!url) throw new Error('Set AXIVOL_URL to the local app containing the saved HCC_002 mask and HCC_006 studies.');
  const out = path.resolve(process.env.AXIVOL_QA_DIR || 'output/ui-post-mask-status');
  await fs.mkdir(out, {recursive: true});
  const browser = await chromium.launch({headless: true});
  const page = await browser.newPage({viewport: {width: 1600, height: 1100}});
  page.setDefaultTimeout(30000);
  const errors = [], checks = [], requests = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => { if (!['GET', 'HEAD'].includes(request.method())) requests.push(request.url()); });
  const ready = stage => page.waitForFunction(s => {
    const image = document.getElementById(s + '-image');
    return image && !image.hidden && image.complete && image.naturalWidth > 0
      && document.getElementById(s + '-viewport').getAttribute('aria-busy') === 'false';
  }, stage);
  const colored = () => page.locator('#post-image').evaluate(image => {
    const canvas = document.createElement('canvas');
    canvas.width = image.naturalWidth; canvas.height = image.naturalHeight;
    const context = canvas.getContext('2d'); context.drawImage(image, 0, 0);
    const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
    let count = 0;
    for (let p = 0; p < pixels.length; p += 4) if (pixels[p] !== pixels[p + 1]) count++;
    return count;
  });
  const settle = async () => { await ready('pre'); await ready('post'); await page.waitForLoadState('networkidle'); };
  try {
    await page.goto(url + '/?case=HCC_002'); await settle();
    const candidate002 = await page.locator('#candidate-saved').inputValue();
    assert.ok(candidate002);
    assert.equal(await page.locator('#post-mask-state').innerText(), 'Automatic mask · Unreviewed');
    assert.equal(await page.locator('#post-mask-action').innerText(), 'Hide mask');
    assert.ok(await colored() > 0);
    await page.locator('#post-mask-action').click(); await ready('post');
    assert.equal(await page.locator('#post-mask-state').innerText(), 'Mask hidden');
    assert.equal(await page.locator('#post-mask-action').innerText(), 'Show mask');
    assert.equal(await page.locator('#candidate-overlay').isChecked(), false);
    assert.equal(await colored(), 0);
    await page.locator('#post-mask-action').click(); await ready('post');
    assert.equal(await page.locator('#candidate-saved').inputValue(), candidate002);
    assert.ok(await colored() > 0);
    await page.locator('#pre .study-settings summary').click();
    await page.locator('#pre-stack').selectOption('1'); await ready('pre');
    assert.equal(await page.locator('#post-mask-state').innerText(), 'No mask for this baseline reference');
    assert.match(await page.locator('#post-mask-reason').innerText(), /different baseline reference/);
    await page.locator('#pre-stack').selectOption('0'); await ready('pre');
    assert.equal(await page.locator('#post-mask-state').innerText(), 'Saved mask available');
    assert.equal(await page.locator('#post-mask-action').innerText(), 'Show saved mask');
    await page.locator('#candidate-lower').fill('17');
    assert.equal(await page.locator('#candidate-result').isHidden(), true);
    assert.equal(await page.locator('#candidate-lower').inputValue(), '17');
    await page.locator('#post-mask-action').click(); await ready('post');
    assert.equal(await page.locator('#candidate-saved').inputValue(), candidate002);
    assert.equal(await page.locator('#candidate-lower').inputValue(), '17');
    await page.locator('#pre .study-settings summary').click();
    for (const stage of ['pre', 'post']) {
      const index = await page.locator('#' + stage + '-index').inputValue();
      await page.locator('#' + stage + '-next').click(); await ready(stage);
      await page.locator('#' + stage + '-prev').click(); await ready(stage);
      assert.equal(await page.locator('#' + stage + '-index').inputValue(), index);
    }
    checks.push('HCC_002 restores its real mask, Hide/Show controls actual pixels, and both viewports scroll. Returning to its PRE reference offers Show saved mask without overwriting a manual draft.');
    await page.evaluate(() => { document.activeElement?.blur(); window.scrollTo(0, 0); }); await page.mouse.move(1, 1);
    await page.screenshot({path: path.join(out, 'post-mask-active.png'), fullPage: true});
    for (const width of [390, 320]) {
      await page.setViewportSize({width, height: 844});
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    }
    await page.screenshot({path: path.join(out, 'post-mask-mobile.png'), fullPage: true});
    await page.setViewportSize({width: 1600, height: 1100});

    await page.locator('#case').selectOption('HCC_006'); await settle();
    const candidate006 = await page.locator('#candidate-saved').inputValue();
    assert.equal(candidate006, '', 'The excluded HCC_006 candidate must not be restored');
    assert.match(await page.locator('#candidate-baseline').innerText(), /Published Mass \(2\).*Acquisition 3/);
    assert.equal(await page.locator('#post-mask-state').innerText(), 'No follow-up mask for this acquisition');
    assert.equal(await page.locator('#candidate-result').isHidden(), true);
    assert.equal(await colored(), 0);
    const originalSeries = await page.locator('#post-series').inputValue();
    const emptySeries = await page.locator('#post-series option').evaluateAll(options =>
      options.find(option => option.value.endsWith('908670668499030554148605049062'))?.value);
    assert.ok(emptySeries, 'Expected the unannotated second HCC_006 POST series');
    await page.locator('#post .study-settings summary').click();
    await page.locator('#post-series').selectOption(emptySeries); await settle();
    assert.equal(await page.locator('#post-mask-state').innerText(), 'No follow-up mask for this acquisition');
    assert.equal(await page.locator('#post-mask-action').innerText(), 'Generate mask');
    assert.equal(await page.locator('#post-mask-action').isDisabled(), false);
    assert.equal(await colored(), 0);
    const mass = await page.locator('#segment').inputValue();
    await page.locator('#segment').selectOption(''); await ready('pre');
    assert.equal(await page.locator('#post-mask-action').isDisabled(), true);
    assert.match(await page.locator('#post-mask-reason').innerText(), /Select a published Mass/);
    await page.locator('#segment').selectOption(mass); await ready('pre');
    assert.equal(await page.locator('#post-mask-action').isDisabled(), false);
    checks.push('HCC_006 restores no excluded or cross-case mask; its acquisitions show an explicit empty state, with published PRE Mass eligibility enforced.');

    let release, intercepted;
    const handler = async route => {
      intercepted = route.request().postDataJSON();
      await new Promise(resolve => { release = resolve; });
      await route.fulfill({status: 503, contentType: 'application/json', body: JSON.stringify({
        status: 'error', error: 'Automatic segmentation is unavailable for this UI error-state check.',
      })});
    };
    await page.route('**/api/automatic-post-candidates', handler);
    const response = page.waitForResponse(item => item.url().endsWith('/api/automatic-post-candidates'));
    await page.locator('#post-mask-action').click();
    for (let n = 0; n < 100 && !release; n++) await new Promise(resolve => setTimeout(resolve, 20));
    assert.ok(release);
    assert.deepEqual(intercepted, {case_id: 'HCC_006', series_uid: emptySeries,
      stack_index: Number(await page.locator('#post-stack').inputValue()),
      pre_series_uid: await page.locator('#pre-series').inputValue(),
      pre_stack_index: Number(await page.locator('#pre-stack').inputValue()), segment: Number(mass)});
    assert.equal(await page.locator('#post-mask-state').innerText(), 'Generating follow-up mask…');
    assert.equal(await page.locator('#post-mask-action').isDisabled(), true);
    release(); await response;
    await page.waitForFunction(() => document.getElementById('candidate-editor').getAttribute('aria-busy') === 'false');
    await page.unroute('**/api/automatic-post-candidates', handler);
    assert.equal(await page.locator('#post-mask-details').isVisible(), true);
    await page.locator('#post-mask-details').click();
    assert.equal(new URL(page.url()).hash, '#candidate-editor');
    await page.locator('#candidate-attempt summary').click();
    assert.match(await page.locator('#candidate-attempt-reason').innerText(), /UI error-state check/);
    assert.equal(await page.locator('#candidate-result').isHidden(), true);
    assert.equal(await colored(), 0);
    await page.locator('#post-series').selectOption(originalSeries); await settle();
    assert.equal(await page.locator('#candidate-saved').inputValue(), '');
    assert.equal(await page.locator('#post-mask-state').innerText(), 'No follow-up mask for this acquisition');
    assert.equal(await page.locator('#post-mask-details').isHidden(), true);
    checks.push('Generate invokes the existing endpoint with exact source IDs; an intercepted error preserves the reason through Review details and creates no mask.');
    assert.deepEqual(requests, [url + '/api/automatic-post-candidates']);
    assert.deepEqual(errors, []);
    await fs.writeFile(path.join(out, 'validation.json'), JSON.stringify({checks, candidate002, candidate006,
      server_writes: 0, scope: 'Real saved HCC_002 mask; HCC_006 excluded candidate stays absent. No inference; one intercepted transport error. Screenshots contain only HCC_002.'}, null, 2));
    console.log(JSON.stringify({passed: checks.length, checks, server_writes: 0}, null, 2));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
