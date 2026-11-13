// Read-only workspace QA on existing HCC_002/HCC_006 masks. No inference or editing.
// Every browser write request is blocked; tool tests activate and pause without a stroke.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');

(async () => {
  const url = process.env.AXIVOL_URL;
  const output = process.env.AXIVOL_QA_DIR;
  if (!url || !output) throw new Error('Set AXIVOL_URL and AXIVOL_QA_DIR. Existing automatic masks are required.');
  await fs.mkdir(output, {recursive: true});
  const browser = await chromium.launch({headless: true});
  const page = await browser.newPage({viewport: {width: 1600, height: 1100}});
  page.setDefaultTimeout(30000);
  const errors = [], blocked = [], checks = [], cases = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', route => {
    const request = route.request();
    if (!['GET', 'HEAD'].includes(request.method())) {
      blocked.push({method: request.method(), path: new URL(request.url()).pathname});
      return route.abort('blockedbyclient');
    }
    return route.continue();
  });
  const ready = stage => page.waitForFunction(stage => {
    const image = document.getElementById(stage + '-image');
    return image && !image.hidden && image.complete && image.naturalWidth === 512
      && document.getElementById(stage + '-viewport').getAttribute('aria-busy') === 'false';
  }, stage);
  const both = async () => { await ready('pre'); await ready('post'); };
  const settled = async () => { await both(); await page.waitForLoadState('networkidle'); };
  const tab = async name => {
    await page.locator('#' + name + '-tab').click();
    await expectTab(name);
  };
  const expectTab = async name => {
    const other = name === 'baseline' ? 'followup' : 'baseline';
    await page.waitForFunction(name => document.getElementById(name + '-tab').getAttribute('aria-selected') === 'true', name);
    assert.equal(await page.locator('#' + name + '-tab').getAttribute('tabindex'), '0');
    assert.equal(await page.locator('#' + other + '-tab').getAttribute('aria-selected'), 'false');
    assert.equal(await page.locator('#' + other + '-tab').getAttribute('tabindex'), '-1');
    assert.equal(await page.locator('#baseline-panel').isVisible(), name === 'baseline');
    assert.equal(await page.locator('#candidate-editor').isVisible(), name === 'followup');
  };
  const disclosure = async (id, open = true) => {
    if (await page.locator('#' + id).evaluate(element => element.open) !== open) {
      await page.locator('#' + id + ' > summary').click();
    }
  };
  const frames = () => page.evaluate(() => Object.fromEntries(['pre', 'post'].map(stage => [stage, {
    source: document.getElementById(stage + '-image').src,
    slice: document.getElementById(stage + '-index').value,
    counter: document.getElementById(stage + '-counter').textContent,
    transform: document.getElementById(stage + '-image').style.transform,
  }])));
  const colored = () => page.locator('#post-image').evaluate(image => {
    const canvas = document.createElement('canvas');
    canvas.width = image.naturalWidth; canvas.height = image.naturalHeight;
    const context = canvas.getContext('2d'); context.drawImage(image, 0, 0);
    const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
    let count = 0;
    for (let i = 0; i < pixels.length; i += 4) if (pixels[i] !== pixels[i + 1]) count++;
    return count;
  });
  const saved = async query => {
    const response = await page.request.get(url + '/api/candidates?' + query);
    assert.equal(response.ok(), true);
    return (await response.json()).candidates;
  };
  try {
    for (const [caseId, acquisition] of [['HCC_002', '1'], ['HCC_006', '3']]) {
      if (!cases.length) {
        await page.goto(url + '/?case=' + caseId); await settled();
        await expectTab('baseline');
      } else {
        await tab('followup');
        await page.locator('#case').selectOption(caseId); await settled();
        await expectTab('followup');
        checks.push('Changing case retains the selected inspector tab and replaces both image sources.');
      }
      const query = new URLSearchParams({case: caseId, series: await page.locator('#post-series').inputValue(),
        stack: await page.locator('#post-stack').inputValue()});
      const before = await saved(query);
      const preUid = await page.locator('#pre-series').inputValue();
      const preStack = Number(await page.locator('#pre-stack').inputValue());
      const segment = Number(await page.locator('#segment').inputValue());
      const candidate = before.find(item => item.method.name === 'registered_post_segmentation'
        && item.baseline_source.series_uid === preUid && item.baseline_source.stack_index === preStack
        && item.baseline_source.source_segment === segment);
      assert.ok(candidate, caseId + ' needs an existing automatic mask; this test will not generate one.');
      assert.equal(candidate.case_id, caseId);
      assert.equal(candidate.status, 'unreviewed');
      assert.equal(candidate.acquisition, '1');
      assert.equal(candidate.baseline_source.acquisition, acquisition);
      assert.equal(candidate.baseline_source.source_segment_label, 'Mass');
      assert.equal(candidate.baseline_source.source_segment, 2);
      await tab('followup');
      await page.locator('#candidate-saved').selectOption(candidate.id); await settled();
      assert.equal(await page.locator('#candidate-status').innerText(), 'Automatic mask · Unreviewed');
      assert.equal(Number(await page.locator('#post-index').inputValue()), candidate.diagnostics.segmentation.display_slice_index + 1);
      const pixels = await colored(); assert.ok(pixels > 0, 'Existing native POST mask must be visible.');

      const snapshot = await frames();
      await tab('baseline'); await page.locator('#baseline-tab').focus();
      for (const [key, selected] of [['ArrowRight', 'followup'], ['ArrowLeft', 'baseline'], ['End', 'followup'], ['Home', 'baseline']]) {
        await page.keyboard.press(key); await expectTab(selected);
        assert.equal(await page.evaluate(() => document.activeElement.id), selected + '-tab');
      }
      await settled();
      assert.deepEqual(await frames(), snapshot, 'Inspector tabs must not reload, navigate or transform either viewport.');
      checks.push(caseId + ': keyboard tabs preserve both displayed frames and view transforms.');

      // Use the viewport action while Baseline is active: mask visibility is independent of the inspector.
      await page.locator('#post-mask-action').click(); await ready('post');
      assert.equal(await colored(), 0);
      assert.equal(await page.locator('#post-mask-state').innerText(), 'Automatic mask · Unreviewed');
      assert.equal(await page.locator('#post-mask-reason').innerText(), 'Mask hidden');
      await page.locator('#post-mask-action').click(); await ready('post');
      assert.equal(await colored(), pixels);
      assert.equal(await page.locator('#candidate-saved').inputValue(), candidate.id);
      checks.push(caseId + ': hide/show changes native overlay pixels without losing mask identity.');

      // Activate tools without drawing; pausing must not create an annotation or leave a hidden active brush.
      await disclosure('pre-edit-panel');
      const draft = await page.locator('#pre-edit-count').textContent();
      await page.locator('#pre-edit-paint').click(); await ready('pre');
      await page.waitForFunction(() => !document.getElementById('pre-edit-canvas').hidden);
      assert.equal(await page.locator('#pre-edit-paint').getAttribute('aria-pressed'), 'true');
      await tab('followup'); await ready('pre');
      assert.equal(await page.locator('#pre-edit-paint').getAttribute('aria-pressed'), 'false');
      assert.equal(await page.locator('#pre-edit-canvas').isHidden(), true);
      assert.equal(await page.locator('#pre-edit-cursor').isHidden(), true);
      assert.equal(await page.locator('#pre-edit-count').textContent(), draft);
      await disclosure('manual-segmentation');
      await page.locator('#candidate-region-tool').click();
      assert.equal(await page.locator('#candidate-region-tool').getAttribute('aria-pressed'), 'true');
      await tab('baseline');
      assert.equal(await page.locator('#candidate-region-tool').getAttribute('aria-pressed'), 'false');
      assert.equal(await page.locator('#candidate-tool-hint').isHidden(), true);
      await tab('followup');
      await page.locator('#candidate-region-tool').click();
      await disclosure('manual-segmentation', false);
      await page.waitForFunction(() => document.getElementById('candidate-region-tool').getAttribute('aria-pressed') === 'false');
      assert.equal(await page.locator('#candidate-saved').inputValue(), candidate.id);
      await tab('baseline'); await disclosure('pre-edit-panel', false);
      checks.push(caseId + ': tabs and manual disclosure pause tools without strokes or saved-mask changes.');

      for (const [label, width, height] of [['desktop', 1600, 1100], ['laptop', 1280, 800], ['mobile', 390, 844]]) {
        await page.setViewportSize({width, height});
        for (const selected of ['baseline', 'followup']) {
          await tab(selected);
          assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false,
            caseId + ' ' + label + ' ' + selected + ' has horizontal overflow');
          await page.evaluate(() => { document.activeElement?.blur(); window.scrollTo(0, 0); });
          await page.screenshot({path: path.join(output, caseId + '-' + label + '-' + selected + '.png'), fullPage: true});
        }
        for (const stage of ['pre', 'post']) {
          const initial = await page.locator('#' + stage + '-index').inputValue();
          await page.locator('#' + stage + '-next').click(); await ready(stage);
          await page.locator('#' + stage + '-prev').click(); await ready(stage);
          assert.equal(await page.locator('#' + stage + '-index').inputValue(), initial);
        }
      }
      const after = await saved(query);
      assert.deepEqual(after, before, 'Read-only workspace QA must not modify any saved candidate metadata.');
      cases.push({case_id: caseId, pre_acquisition: acquisition, post_acquisition: '1', segment: 'Mass (2)',
        candidate_id: candidate.id, visible_overlay_pixels: pixels, saved_candidates_unchanged: true});
    }
    assert.deepEqual(blocked, []);
    assert.deepEqual(errors, []);
    const report = {checks, cases, blocked_write_requests: blocked, browser_errors: errors,
      viewports: [1600, 1280, 390], scope: 'Read-only interface checks with existing unreviewed masks. No inference, strokes or anatomical evaluation.'};
    await fs.writeFile(path.join(output, 'workspace-validation.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report, null, 2));
  } catch (error) {
    await page.screenshot({path: path.join(output, 'workspace-failure.png'), fullPage: true});
    throw error;
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
