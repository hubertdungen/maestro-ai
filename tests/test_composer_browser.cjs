// All APIs are intercepted BEFORE navigation, including GETs. No live chat writes.
// NODE_PATH=/path/to/node_modules node tests/test_composer_browser.cjs
const { chromium } = require('playwright-core');
const assert = require('node:assert/strict');
const os = require('node:os');
const png = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=';
(async () => {
 const browser = await chromium.launch({executablePath: process.env.CHROME_PATH || os.homedir()+'/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome', args:['--no-sandbox','--disable-dev-shm-usage']});
 try {
 for (const mobile of [false, true]) {
  const context = await browser.newContext({viewport:mobile?{width:390,height:844}:{width:1440,height:900}});
  const page = await context.newPage(), errors=[];
  page.on('pageerror', e=>errors.push(e.stack));
  const chats = ['A','B'].map(id=>({id,title:'Fixture '+id,project:'nas',engine:'auto',messages:[],updated_at:Date.now()/1000}));
  let uploads=0, hold=false, release, started;
  await context.route('**/sw.js', route=>route.fulfill({contentType:'application/javascript',body:'// Test: no service worker installed.'}));
  await context.route('**/api/**', async route=>{
   const u=new URL(route.request().url()); let data={};
   if(u.pathname==='/api/chats') data={chats,archived_chats:[]};
   if(u.pathname==='/api/chat') data={chat:chats.find(c=>c.id===u.searchParams.get('id'))};
   if(u.pathname==='/api/version') data={hash:'fixture'};
   if(u.pathname==='/api/orchestrator/thoughts') data={thoughts:[],now:'Fixture'};
   if(u.pathname==='/api/chat/new') { const chat={id:'C',title:'Fixture C',project:'nas',engine:'auto',messages:[]}; chats.push(chat); data={chat}; }
   if(u.pathname==='/api/upload') {
    uploads++;
    if(hold) { if(started) started(); await new Promise(r=>release=r); }
    data={path:'/fixture/image-'+uploads+'.png'};
   } else if(route.request().method()!=='GET' && u.pathname!=='/api/chat/new') throw Error('Unexpected mutation '+u.pathname);
   if(u.pathname==='/api/image') return route.fulfill({contentType:'image/png',body:Buffer.from(png,'base64')});
   await route.fulfill({json:data});
  });
  await page.goto('http://127.0.0.1:8109/');
  const input=page.locator('#promptInput');
  await page.waitForFunction(()=>document.querySelector('.chat-row.active')?.textContent.includes('Fixture A'));
  async function open(id) {
   // DOM click works with the sidebar closed on mobile, without changing app state.
   await page.locator('.chat-row').filter({hasText:'Fixture '+id}).evaluate(el=>el.click());
   await page.waitForFunction(id=>document.querySelector('.chat-row.active')?.textContent.includes('Fixture '+id),id);
  }
  await input.fill('rascunho A'); await open('B'); assert.equal(await input.inputValue(),'');
  await input.fill('rascunho B'); await open('A'); assert.equal(await input.inputValue(),'rascunho A');
  async function imageEvent(type) {
   await page.evaluate(({type,png})=>{
    const dt=new DataTransfer();dt.items.add(new File([Uint8Array.from(atob(png),c=>c.charCodeAt(0))],'fixture.png',{type:'image/png'}));
    const el=document.querySelector('#promptInput');
    if(type==='paste') el.dispatchEvent(new ClipboardEvent('paste',{clipboardData:dt,bubbles:true,cancelable:true}));
    else {
     el.dispatchEvent(new DragEvent('dragover',{dataTransfer:dt,bubbles:true,cancelable:true}));
     el.dispatchEvent(new DragEvent('drop',{dataTransfer:dt,bubbles:true,cancelable:true}));
    }
   },{type,png});
  }
  await imageEvent('paste'); await page.waitForFunction(()=>document.querySelectorAll('#attachStrip .attach-thumb').length===1);
  await imageEvent('drop'); await page.waitForFunction(()=>document.querySelectorAll('#attachStrip .attach-thumb').length===2);
  await open('B');assert.equal(await page.locator('#attachStrip .attach-thumb').count(),0);
  hold=true; const uploadStarted=new Promise(r=>started=r);
  await imageEvent('paste');await uploadStarted; await open('A'); release(); hold=false;
  await page.waitForFunction(()=>JSON.parse(localStorage.maestro_drafts_v2).B.images.length===1);
  assert.equal(await page.locator('#attachStrip .attach-thumb').count(),2);
  await open('B');assert.equal(await page.locator('#attachStrip .attach-thumb').count(),1);
  await page.reload();await page.waitForFunction(()=>document.querySelectorAll('#attachStrip .attach-thumb').length===1);
  assert.equal(await input.inputValue(),'rascunho B');
  await page.evaluate(()=>{
   const dt=new DataTransfer(); dt.setData('text/uri-list',location.origin+'/api/image?path=%2Ffixture%2Fexisting.png');
   const el=document.querySelector('#promptInput');
   const over=new DragEvent('dragover',{dataTransfer:dt,bubbles:true,cancelable:true});
   el.dispatchEvent(over); if(!over.defaultPrevented) throw Error('Native image drag rejected');
   el.dispatchEvent(new DragEvent('drop',{dataTransfer:dt,bubbles:true,cancelable:true}));
  });
  assert.equal(await page.locator('#attachStrip .attach-thumb').count(),2);
  await page.locator('#btnNewChat').evaluate(el=>el.click());
  await page.waitForFunction(()=>document.querySelector('.chat-row.active')?.textContent.includes('Fixture C'));
  assert.equal(await input.inputValue(),'');
  assert.equal(await page.locator('#attachStrip .attach-thumb').count(),0);
  await open('B'); assert.equal(await input.inputValue(),'rascunho B');
  await page.screenshot({path:'/tmp/maestro-composer-'+(mobile?'mobile':'desktop')+'.png'});
  assert.deepEqual(errors,[]);
  console.log((mobile?'mobile':'desktop')+': drafts A/B, clipboard, file drop, delayed upload, reload OK; all APIs mocked');
  await context.close();
 }
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
