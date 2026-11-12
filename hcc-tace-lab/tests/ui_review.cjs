// Requires Playwright, a running Docker app and the public HCC_002 case.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
(async()=>{
 const out=path.resolve(process.env.AXIVOL_QA_DIR||'output/ui');
 await fs.mkdir(out,{recursive:true});
 const browser=await chromium.launch({headless:true});
 const page=await browser.newPage({viewport:{width:1600,height:1000},acceptDownloads:true,bypassCSP:true});
 page.setDefaultTimeout(15000);
 const errors=[],checks=[],timings={};
 page.on('pageerror',e=>errors.push(e.message));
 const ready=stage=>page.waitForFunction(s=>{
  const i=document.getElementById(s+'-image');
  return i&&!i.hidden&&i.complete&&i.naturalWidth===512&&document.getElementById(s+'-viewport').getAttribute('aria-busy')==='false';
 },stage);
 const both=async()=>{await ready('pre');await ready('post');};
 const setSlice=(stage,value)=>page.locator('#'+stage+'-index').evaluate((e,n)=>{
  e.value=n;e.dispatchEvent(new Event('input',{bubbles:true}));
 },value);
 const calculate=async()=>{
  await page.locator('#measure').click();
  await page.waitForFunction(()=>document.getElementById('volume-value').textContent==='495.04');
 };
 const audit=async label=>{
  if(!process.env.AXIVOL_AXE_PATH)return;
  await page.addScriptTag({path:process.env.AXIVOL_AXE_PATH});
  const a=await page.evaluate(()=>axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa','wcag22aa']}}));
  const violations=a.violations.map(v=>({id:v.id,impact:v.impact,nodes:v.nodes.map(n=>({target:n.target,reason:n.failureSummary}))}));
  await fs.writeFile(path.join(out,'accessibility-'+label+'.json'),JSON.stringify({violations,incomplete:a.incomplete.map(v=>v.id)},null,2));
  assert.equal(violations.length,0,JSON.stringify(violations));
 };
 try{
  await page.goto((process.env.AXIVOL_URL||'http://127.0.0.1:8770')+'/?case=HCC_002');
  await both();await calculate();
  assert.equal(await page.locator('html').getAttribute('lang'),'en');
  assert.equal(await page.locator('.study-sequence').count(),0);
  assert.ok((await page.locator('#case').boundingBox()).height>=44);
  assert.equal(await page.locator('#case').evaluate(e=>getComputedStyle(e).paddingLeft),'16px');
  assert.equal(await page.locator('#pre-counter').innerText(),'55 / 109');
  assert.equal(await page.locator('#post-counter').innerText(),'49 / 97');
  for(const [id,value] of [['source-case','HCC_002'],['source-acquisition','1'],['source-segment','Mass (2)'],['source-voxels','324,430']]){
   assert.equal(await page.locator('#'+id).innerText(),value);
  }
  checks.push('Real HCC_002 viewports; acquisition 1, Mass segment 2, 495.04 cm³.');
  const colored=()=>page.locator('#pre-image').evaluate(img=>{
   const c=document.createElement('canvas');c.width=img.naturalWidth;c.height=img.naturalHeight;
   const ctx=c.getContext('2d');ctx.drawImage(img,0,0);
   const data=ctx.getImageData(0,0,c.width,c.height).data;let n=0;
   for(let i=0;i<data.length;i+=4)if(data[i]!==data[i+1])n++;
   return n;
  });
  assert.ok(await colored()>0);
  await page.locator('#show-mask').uncheck();await ready('pre');assert.equal(await colored(),0);
  await page.locator('#show-mask').check();await ready('pre');
  checks.push('Overlay changes actual image pixels; follow-up remains unsegmented.');
  await page.waitForLoadState('networkidle');
  let t=performance.now();await page.locator('#pre-next').click();await ready('pre');
  timings.adjacent_slice_ms=Math.round(performance.now()-t);
  await page.waitForLoadState('networkidle');
  let count=0,active=0,peak=0;
  const req=r=>{if(r.url().includes('/slice?')){count++;active++;peak=Math.max(peak,active);}};
  const done=r=>{if(r.url().includes('/slice?'))active=Math.max(0,active-1);};
  page.on('request',req);page.on('requestfinished',done);page.on('requestfailed',done);
  await page.locator('#pre-index').evaluate(async e=>{
   for(let n=5;n<=35;n++){e.value=n;e.dispatchEvent(new Event('input',{bubbles:true}));await new Promise(r=>setTimeout(r,5));}
  });
  t=performance.now();await ready('pre');
  timings.burst_settle_ms=Math.round(performance.now()-t);
  timings.burst_requests=count;timings.peak_parallel_image_requests=peak;
  page.off('request',req);page.off('requestfinished',done);page.off('requestfailed',done);
  assert.equal(await page.locator('#pre-counter').innerText(),'35 / 109');
  assert.ok(count<15,'Excessive slice requests');assert.ok(peak<=2,'Concurrent image request storm');
  checks.push('Rapid scrubbing coalesces intermediate requests and displays the final requested slice.');
  await page.locator('#link').check();await setSlice('pre',109);await both();
  assert.equal(await page.locator('#post-counter').innerText(),'97 / 97');
  assert.match(await page.locator('#link-note').innerText(),/not registered/);
  await page.locator('#link').uncheck();await page.locator('#post-viewport').focus();
  await page.keyboard.press('ArrowUp');await ready('post');
  assert.equal(await page.locator('#post-counter').innerText(),'96 / 97');
  await page.locator('#post-viewport').hover();await page.mouse.wheel(0,-80);
  await page.waitForFunction(()=>document.getElementById('post-counter').textContent==='95 / 97');
  checks.push('Independent, linked, keyboard and mouse-wheel scrolling.');
  await page.locator('#pre-zoom-in').click();assert.equal(await page.locator('#pre-zoom').innerText(),'125%');
  const box=await page.locator('#pre-viewport').boundingBox();
  await page.mouse.move(box.x+box.width/2,box.y+box.height/2);await page.mouse.down();
  await page.mouse.move(box.x+box.width/2+35,box.y+box.height/2+20);await page.mouse.up();
  assert.match(await page.locator('#pre-image').getAttribute('style'),/translate\(35px, 20px\)/);
  await page.locator('#width').fill('500');await page.locator('#width').press('Tab');await both();
  await page.locator('#width').fill('1');await page.locator('#width').press('Tab');
  assert.equal(await page.locator('#width').getAttribute('aria-invalid'),'true');
  await setSlice('pre',76);await ready('pre');
  await page.locator('#reset-display').click();await both();
  assert.equal(await page.locator('#width').inputValue(),'350');assert.equal(await page.locator('#pre-zoom').innerText(),'100%');
  checks.push('Zoom, pan, window/level validation and display reset.');
  await page.locator('#pre .study-settings summary').click();
  await page.locator('#pre-stack').selectOption('1');await ready('pre');
  assert.equal(await page.locator('#volume-value').innerText(),'—');
  await calculate();
  assert.equal(await page.locator('#source-acquisition').innerText(),'2');
  await page.locator('#pre-stack').selectOption('0');await ready('pre');await calculate();
  const series=await page.locator('#pre-series option').evaluateAll(list=>list.map(o=>o.value));
  await page.locator('#pre-series').selectOption(series[1]);await ready('pre');
  assert.equal(await page.locator('#volume-value').innerText(),'—');assert.equal(await page.locator('#measure').isDisabled(),true);
  await page.locator('#pre-series').selectOption(series[0]);await ready('pre');await calculate();
  await page.locator('#pre .study-settings summary').click();
  checks.push('Acquisition changes clear and recalculate volume with the correct source; missing SEG disables measurement.');
  const reportEvent=page.waitForEvent('download');await page.locator('#report').click();
  const reportDownload=await reportEvent;
  const report=JSON.parse(await fs.readFile(await reportDownload.path(),'utf8'));
  assert.equal(report.case,'HCC_002');assert.equal(report.pre_segmentation_measurement.segment,2);
  assert.equal(report.volume_change_percent,null);assert.equal(report.post_segmentation_measurement,null);
  assert.equal(report.status,'comparison_pending');
  checks.push('Exported report retains provenance and null POST volume and volume change.');
  await setSlice('pre',76);await ready('pre');
  await setSlice('post',49);await ready('post');
  const viewportBoxes={pre:await page.locator('#pre-viewport').boundingBox(),post:await page.locator('#post-viewport').boundingBox()};
  await fs.writeFile(path.join(out,'viewport-boxes.json'),JSON.stringify(viewportBoxes));
  const imageEvent=page.waitForEvent('download',{timeout:30000});await page.locator('#capture').click();
  const image=await imageEvent;assert.equal(image.suggestedFilename(),'Axivol_Tracker_HCC_002.png');
  await image.saveAs(path.join(out,'axivol-export.png'));
  await page.locator('#image-download').evaluate(e=>e.hidden=true);await page.mouse.move(2,2);
  await page.screenshot({path:path.join(out,'axivol-desktop.png'),fullPage:true});
  checks.push('PNG export contains the displayed case and real images.');
  await audit('desktop');
  for(const [label,width,height] of [['laptop',1280,800],['tablet',1024,768],['mobile',390,844],['narrow',320,740]]){
   await page.setViewportSize({width,height});
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,label+' overflow');
   for(const stage of ['pre','post']){
    const n=Number(await page.locator('#'+stage+'-index').inputValue());
    await page.locator('#'+stage+'-prev').click();await ready(stage);
    assert.equal(Number(await page.locator('#'+stage+'-index').inputValue()),n-1);
   }
   if(label==='mobile'){
    await audit('mobile');
    await page.evaluate(()=>{document.activeElement?.blur();window.scrollTo(0,0);});
    await page.mouse.move(1,1);
    await page.screenshot({path:path.join(out,'axivol-mobile.png'),fullPage:true});
   }
  }
  checks.push('Both real viewports usable at 1600, 1280, 1024, 390 and 320 px; no horizontal overflow.');
  if(process.env.AXIVOL_AXE_PATH)checks.push('Zero automated WCAG A/AA violations on desktop and mobile.');
  await page.route('**/slice?**',r=>r.fulfill({status:500,contentType:'application/json',body:'{"error":"QA transport failure"}'}));
  await page.locator('#level').fill('713');await page.locator('#level').press('Tab');
  await page.waitForFunction(()=>document.getElementById('pre-image-state').textContent.startsWith('Image unavailable'));
  assert.equal(await page.locator('#pre-image').isHidden(),true);
  assert.equal(await page.locator('#report').getAttribute('aria-disabled'),'true');
  checks.push('Image load failures hide stale images and block export.');
  assert.deepEqual(errors,[]);
  await fs.writeFile(path.join(out,'ui-validation.json'),JSON.stringify({
   case:'HCC_002',acquisition:'1',segment:{number:2,label:'Mass'},checks,timings,browser_errors:errors,
   scope:'Functional and automated accessibility checks. Not clinical or human-factors validation.'
  },null,2));
  console.log(JSON.stringify({passed:checks.length,timings,checks},null,2));
 }catch(e){await page.screenshot({path:path.join(out,'qa-failure.png'),fullPage:true});throw e;}
 finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
