// Real HCC_002 QA on an isolated annotation store. No fabricated success mask.
// AXIVOL_EXPECT_AUTO defaults to abstained; set success only for a validated API build.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');

(async () => {
  const url = process.env.AXIVOL_URL;
  if (!url) throw new Error('Set AXIVOL_URL to an isolated QA app.');
  const expected = process.env.AXIVOL_EXPECT_AUTO || 'abstained';
  const out = path.resolve(process.env.AXIVOL_QA_DIR || 'output/ui-automatic-post');
  await fs.mkdir(out, {recursive: true});
  const browser = await chromium.launch({headless: true});
  const page = await browser.newPage({viewport: {width: 1600, height: 1100}, acceptDownloads: true});
  page.setDefaultTimeout(180000);
  const errors = [], checks = [], timings = {};
  page.on('pageerror', error => errors.push(error.message));
  const ready = stage => page.waitForFunction(s => {
    const image = document.getElementById(s + '-image');
    return image && !image.hidden && image.complete && image.naturalWidth > 0
      && document.getElementById(s + '-viewport').getAttribute('aria-busy') === 'false';
  }, stage);
  const setSlice = async (stage, value) => {
    await page.locator('#' + stage + '-index').evaluate((input, value) => {
      input.value = value; input.dispatchEvent(new Event('input', {bubbles: true}));
    }, value); await ready(stage);
  };
  const openFollowup = async (target = page, manual = false) => {
    await target.locator('#followup-tab').click();
    if (manual && !await target.locator('#manual-segmentation').evaluate(element => element.open)) {
      await target.locator('#manual-segmentation > summary').click();
    }
  };
  const manualFields = () => page.locator('#candidate-editor input[type=number]').evaluateAll(inputs => Object.fromEntries(inputs.map(input => [input.id, input.value])));
  const colored = () => page.locator('#post-image').evaluate(image => {
    const canvas = document.createElement('canvas'); canvas.width = image.naturalWidth; canvas.height = image.naturalHeight;
    const context = canvas.getContext('2d'); context.drawImage(image, 0, 0);
    const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
    let count = 0;
    for (let i = 0; i < pixels.length; i += 4) if (pixels[i] !== pixels[i + 1]) count++;
    return count;
  });
  const until = async predicate => {
    for (let n = 0; n < 3600 && !predicate(); n++) await new Promise(resolve => setTimeout(resolve, 50));
    assert.ok(predicate(), 'Timed out waiting for held API response');
  };
  let realResponse, automaticRequest, manual, automaticStatus;
  try {
    await page.goto(url + '/?case=HCC_002'); await ready('pre'); await ready('post');
    await page.waitForLoadState('networkidle');
    const preUid = await page.locator('#pre-series').inputValue(), postUid = await page.locator('#post-series').inputValue();
    const segment = Number(await page.locator('#segment').inputValue());
    const summary = await (await page.request.get(url + '/api/series/' + postUid)).json();
    const stack = summary.stacks[0], [rows, columns] = stack.shape;
    automaticRequest = {case_id: 'HCC_002', series_uid: postUid, stack_index: 0,
      pre_series_uid: preUid, pre_stack_index: 0, segment};
    assert.match(await page.locator('#candidate-baseline').innerText(), /Published Mass.*Acquisition 1/);
    const otherSegment = await page.locator('#segment option').evaluateAll(items => items.find(item => item.value && item.textContent.toLowerCase() !== 'mass')?.value);
    if (otherSegment) {
      await page.locator('#segment').selectOption(otherSegment); await ready('pre');
      assert.equal(await page.locator('#candidate-automatic').isDisabled(), true);
      await page.locator('#segment').selectOption(String(segment)); await ready('pre');
    }
    await openFollowup(page, true);
    // Clear a previously restored candidate while retaining the stored artifacts.
    await page.locator('#candidate-lower').fill('0'); await page.locator('#candidate-lower').fill(''); await ready('post');
    const draft = await manualFields();
    const listUrl = url + '/api/candidates?' + new URLSearchParams({case: 'HCC_002', series: postUid, stack: 0});
    const beforeIds = (await (await page.request.get(listUrl)).json()).candidates.map(item => item.id).sort();
    let received, release, started, finished;
    const realHandler = async route => {
      assert.deepEqual(route.request().postDataJSON(), automaticRequest);
      started = performance.now();
      const actual = await route.fetch({timeout: 180000}); received = actual; finished = performance.now();
      await new Promise(resolve => { release = resolve; });
      await route.fulfill({response: actual});
    };
    await page.route('**/api/automatic-post-candidates', realHandler);
    const responseEvent = page.waitForResponse(r => r.url().endsWith('/api/automatic-post-candidates'));
    await page.locator('#candidate-automatic').click();
    await until(() => started);
    for (const [stage, index] of [['pre', 65], ['post', 59]]) {
      const backendAlreadyReturned = !!received, t = performance.now();
      await setSlice(stage, index);
      timings[stage + '_slice_while_pending_ms'] = Math.round(performance.now() - t);
      timings[stage + '_backend_already_returned'] = backendAlreadyReturned;
      assert.equal(await page.locator('#candidate-automatic').innerText(), 'Generating…');
      assert.ok(timings[stage + '_slice_while_pending_ms'] < 5000, stage + ' viewport blocked during generation');
    }
    if (expected === 'success') {
      // CPU inference is longer than startup: sample again after it has had
      // time to begin, using slices outside the initial prefetch neighborhood.
      await new Promise(resolve => setTimeout(resolve, 15000));
      for (const [stage, index] of [['pre', 82], ['post', 76]]) {
        const backendAlreadyReturned = !!received, t = performance.now();
        await setSlice(stage, index);
        timings[stage + '_slice_during_inference_ms'] = Math.round(performance.now() - t);
        timings[stage + '_inference_already_returned'] = backendAlreadyReturned;
        assert.ok(timings[stage + '_slice_during_inference_ms'] < 5000, stage + ' viewport blocked during inference');
      }
    }
    await until(() => received && release); timings.api_ms = Math.round(finished - started);
    automaticStatus = received.status(); realResponse = await received.json(); release();
    await responseEvent; await page.unroute('**/api/automatic-post-candidates', realHandler);
    await page.waitForFunction(() => document.getElementById('candidate-editor').getAttribute('aria-busy') === 'false');
    await ready('post');
    assert.deepEqual(await manualFields(), draft);
    if (expected === 'abstained') {
      assert.equal(automaticStatus, 422); assert.equal(realResponse.status, 'abstained');
      assert.equal(realResponse.id, undefined); assert.equal(realResponse.volume_cm3, undefined);
      assert.equal(await page.locator('#candidate-result').isHidden(), true);
      assert.equal(await page.locator('#post-overlay-label').isHidden(), true);
      assert.equal(await colored(), 0);
      assert.equal(await page.locator('#candidate-attempt-title').innerText(), 'No segmentation generated');
      const afterIds = (await (await page.request.get(listUrl)).json()).candidates.map(item => item.id).sort();
      assert.deepEqual(afterIds, beforeIds, 'Abstention must not create a candidate artifact');
      await page.locator('#candidate-attempt summary').click();
      const downloadEvent = page.waitForEvent('download'); await page.locator('#candidate-attempt-export').click();
      const run = JSON.parse(await fs.readFile(await (await downloadEvent).path(), 'utf8'));
      assert.deepEqual(run.request, automaticRequest); assert.deepEqual(run.response, realResponse);
      await page.locator('#candidate-attempt summary').click();
      checks.push('Real HCC_002 abstention creates no candidate, volume or colored overlay; exact request/diagnostics export as run-details JSON.');
    } else {
      assert.equal(automaticStatus, 201); assert.equal(realResponse.status, 'unreviewed');
      assert.equal(realResponse.method.name, 'registered_post_segmentation');
      assert.equal(realResponse.baseline_source.series_uid, preUid);
      assert.equal(realResponse.baseline_source.source_segment, segment);
      assert.equal(Number(await page.locator('#post-index').inputValue()), realResponse.diagnostics.segmentation.display_slice_index + 1);
      assert.match(await page.locator('#candidate-status').innerText(), /Automatic mask.*Unreviewed/);
      assert.equal(await page.locator('#registration-status').innerText(), 'Unverified');
      assert.ok(await colored() > 0);
      checks.push('Real automatic result is unreviewed, uses the requested published reference, and centers on a slice containing the POST mask.');

      // Delay only the real PRE summary. POST and its real saved list arrive
      // first, including the actual successful candidate generated above.
      for (const editDraft of [false, true]) {
        const reload = await browser.newPage({viewport: {width: 1600, height: 1100}});
        reload.setDefaultTimeout(90000);
        reload.on('pageerror', error => errors.push(error.message));
        let releasePre, listDelivered = false;
        await reload.route(url + '/api/series/' + preUid, async route => {
          const actual = await route.fetch();
          await new Promise(resolve => { releasePre = resolve; });
          await route.fulfill({response: actual});
        });
        await reload.route('**/api/candidates?*', async route => {
          const actual = await route.fetch();
          await route.fulfill({response: actual}); listDelivered = true;
        });
        try {
          await reload.goto(url + '/?case=HCC_002', {waitUntil: 'domcontentloaded'});
          await until(() => releasePre && listDelivered);
          await openFollowup(reload, true);
          await reload.waitForFunction(() => document.getElementById('post-viewport').getAttribute('aria-busy') === 'false');
          await reload.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
          assert.equal(await reload.locator('#candidate-result').isHidden(), true,
            'The latest automatic mask must wait for its PRE source, not restore an older manual mask');
          if (editDraft) await reload.locator('#candidate-lower').fill('17');
          releasePre();
          await reload.waitForLoadState('networkidle');
          await reload.waitForFunction(() => document.getElementById('pre-viewport').getAttribute('aria-busy') === 'false');
          if (editDraft) {
            assert.equal(await reload.locator('#candidate-result').isHidden(), true);
            assert.equal(await reload.locator('#candidate-lower').inputValue(), '17');
          } else {
            await reload.waitForFunction(id => document.getElementById('candidate-saved').value === id, realResponse.id);
            assert.equal(Number(await reload.locator('#post-index').inputValue()), realResponse.diagnostics.segmentation.display_slice_index + 1);
            await reload.locator('#candidate-lower').fill('18');
            await reload.locator('#pre-next').click();
            await reload.waitForLoadState('networkidle');
            assert.equal(await reload.locator('#candidate-result').isHidden(), true,
              'Initial restoration must not repeat after a manual edit');
          }
        } finally { releasePre?.(); await reload.close(); }
      }
      checks.push('The real saved automatic candidate restores once after delayed PRE metadata, but never replaces a manual draft.');
    }
    checks.push('Both real viewports remain navigable while the automatic response is pending; manual parameters are preserved.');

    // Exercise the manual fallback using a small engineering-only region.
    await setSlice('post', 49);
    await page.locator('.candidate-coordinates summary').click();
    const x = Math.floor(columns / 2), y = Math.floor(rows / 2);
    const values = {first: 48, last: 50, x0: x - 3, x1: x + 5, y0: y - 3, y1: y + 5,
      'seed-x': x + 1, 'seed-y': y + 1, 'seed-z': 49, lower: -1024, upper: 3071};
    for (const [name, value] of Object.entries(values)) await page.locator('#candidate-' + name).fill(String(value));
    await page.waitForFunction(() => !document.getElementById('candidate-generate').disabled);
    const manualEvent = page.waitForResponse(r => r.url().endsWith('/api/candidates') && r.request().method() === 'POST');
    await page.locator('#candidate-generate').click(); const manualResponse = await manualEvent;
    assert.equal(manualResponse.status(), 201); manual = await manualResponse.json();
    await ready('post'); assert.ok(await colored() > 0);
    assert.match(await page.locator('#candidate-status').innerText(), /Seed-based mask/);
    await page.locator('.candidate-coordinates summary').click();

    // A real edited PRE artifact is deliberately displayed; the automatic API
    // must still receive the original published segment, never this candidate ID.
    const editResponse = await page.request.post(url + '/api/pre-edits', {data: {
      case_id: 'HCC_002', series_uid: preUid, stack_index: 0, segment,
      strokes: [{slice: 75, mode: 'paint', radius_mm: 1, points: [[256, 256]]}],
    }});
    assert.equal(editResponse.status(), 201); const edited = await editResponse.json();
    await page.locator('#pre .study-settings summary').click();
    await page.locator('#pre-stack').selectOption('1'); await ready('pre');
    assert.equal(await page.locator('#candidate-saved').inputValue(), manual.id, 'PRE changes must not detach manual POST masks');
    await page.locator('#pre-stack').selectOption('0'); await ready('pre'); await page.waitForLoadState('networkidle');
    await page.locator('#baseline-tab').click();
    await page.locator('#pre-edit-saved').selectOption(edited.id); await ready('pre');
    await page.locator('#pre .study-settings summary').click();
    assert.match(await page.locator('#pre-overlay-label').innerText(), /Edited copy/);
    assert.match(await page.locator('#candidate-baseline').innerText(), /Published Mass/);
    checks.push('Manual fallback still generates real POST pixels and survives PRE acquisition changes; the automatic reference remains the published SEG even while an edited PRE copy is displayed.');

    // Replay the recorded real API response to exercise delayed transport. This
    // is not a second algorithm evaluation and never fabricates a success mask.
    const replay = async mutate => {
      let resume, intercepted;
      const handler = async route => {
        assert.deepEqual(route.request().postDataJSON(), automaticRequest);
        intercepted = true; await new Promise(resolve => { resume = resolve; });
        await route.fulfill({status: automaticStatus, contentType: 'application/json', body: JSON.stringify(realResponse)});
      };
      await openFollowup(page, true);
      await page.route('**/api/automatic-post-candidates', handler);
      const event = page.waitForResponse(r => r.url().endsWith('/api/automatic-post-candidates'));
      await page.locator('#candidate-automatic').click(); await until(() => intercepted && resume);
      if (mutate) await mutate();
      resume(); await event;
      await page.waitForFunction(() => document.getElementById('candidate-editor').getAttribute('aria-busy') === 'false');
      await page.unroute('**/api/automatic-post-candidates', handler);
    };
    const priorImage = await page.locator('#post-image').getAttribute('src'), priorDraft = await manualFields();
    if (expected === 'success') {
      await replay(async () => { await page.locator('#candidate-region-tool').click(); });
      assert.equal(await page.locator('#candidate-region-tool').getAttribute('aria-pressed'), 'true');
      assert.equal(await page.locator('#candidate-saved').inputValue(), manual.id);
      await page.locator('#candidate-region-tool').click();
      checks.push('A delayed real automatic result is saved without interrupting an active manual selection tool.');
    }
    await replay(); await ready('post');
    if (expected === 'abstained') {
      assert.equal(await page.locator('#candidate-saved').inputValue(), manual.id);
      assert.equal(await page.locator('#post-image').getAttribute('src'), priorImage);
      assert.deepEqual(await manualFields(), priorDraft);
      assert.match(await page.locator('#candidate-status').innerText(), /Seed-based mask/);
    }
    await replay(async () => {
      await page.locator('#pre .study-settings summary').click();
      await page.locator('#pre-stack').selectOption('1'); await ready('pre');
    });
    assert.equal(await page.locator('#candidate-attempt').isHidden(), true);
    if (expected === 'abstained') assert.equal(await page.locator('#candidate-saved').inputValue(), manual.id);
    await page.locator('#pre-stack').selectOption('0'); await ready('pre'); await page.waitForLoadState('networkidle');
    await page.locator('#pre .study-settings summary').click();
    checks.push(expected === 'abstained'
      ? 'Recorded real abstention preserves an existing manual mask; a delayed run cannot attach diagnostics or results after its PRE reference changes.'
      : 'A delayed recorded real automatic result cannot attach after its PRE reference changes.');
    const alternate = await page.locator('#post-series option').evaluateAll(items => items.map(item => item.value));
    const otherUid = alternate.find(uid => uid !== postUid);
    if (otherUid) {
      await replay(async () => {
        await page.locator('#post .study-settings summary').click();
        await page.locator('#post-series').selectOption(otherUid); await ready('post');
      });
      assert.equal(await page.locator('#candidate-attempt').isHidden(), true);
      assert.equal(await page.locator('#candidate-result').isHidden(), true);
      await page.locator('#post-series').selectOption(postUid); await ready('post'); await page.waitForLoadState('networkidle');
      await page.locator('#post .study-settings summary').click();
      checks.push('Changing POST series also discards the delayed run from the previous source.');
    }
    // Present the actual successful mask, or manual fallback plus abstention.
    await page.locator('#candidate-saved').selectOption(expected === 'abstained' ? manual.id : realResponse.id); await ready('post');
    if (expected === 'abstained') { await setSlice('post', 49); await replay(); }
    const report = await (await page.request.get(new URL(await page.locator('#report').getAttribute('href'), url).href)).json();
    assert.equal(report.post_segmentation_measurement, null); assert.equal(report.volume_change_percent, null);
    if (expected === 'abstained') assert.equal(report.post_candidate.id, manual.id);
    await page.waitForLoadState('networkidle');
    await page.evaluate(() => { document.activeElement?.blur(); window.scrollTo(0, 0); }); await page.mouse.move(1, 1);
    await page.screenshot({path: path.join(out, 'automatic-post-desktop.png'), fullPage: true});
    for (const width of [1024, 390, 320]) {
      await page.setViewportSize({width, height: 844});
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      await page.locator('#pre-next').click(); await ready('pre'); await page.locator('#post-next').click(); await ready('post');
    }
    await page.evaluate(() => { document.activeElement?.blur(); window.scrollTo(0, 0); }); await page.mouse.move(1, 1);
    await page.screenshot({path: path.join(out, 'automatic-post-mobile.png'), fullPage: true});
    assert.deepEqual(errors, []);
    checks.push('Responsive layout and both viewports remain usable; reports keep candidate and clinical measurements separate.');
    await fs.writeFile(path.join(out, 'automatic-post-validation.json'), JSON.stringify({checks, timings, expected_outcome: expected,
      real_request: automaticRequest, real_response: realResponse, manual_candidate_id: manual.id,
      scope: 'One real automatic computation; late-response checks replay that exact response. Manual QA mask is not a lesion reference. No fabricated automatic success.'}, null, 2));
    console.log(JSON.stringify({passed: checks.length, timings, checks}, null, 2));
  } catch (error) { await page.screenshot({path: path.join(out, 'automatic-post-failure.png'), fullPage: true}); throw error; }
  finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
