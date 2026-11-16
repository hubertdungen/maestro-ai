// Teste de browser do botão «Continuar trabalho» (conversa simulada por rotas).
// Uso: node tests/test_continue_button.js [url]   (PLAYWRIGHT_MODULE para outro caminho do playwright)
const path = require('path');
let pw; try { pw = require(process.env.PLAYWRIGHT_MODULE || 'playwright'); } catch (e) { pw = require(path.join(process.env.HOME, 'Despensa/node_modules/playwright')); }
const { chromium } = pw;
const BASE = process.argv[2] || 'http://localhost:8109/';
const assert = require('assert');
(async () => {
  const b = await chromium.launch(); const p = await b.newPage();
  await p.route(/\.(png|jpe?g|gif|webp|svg)(\?|$)/, r => r.abort());
  const chat = {id:'chat-t1', title:'T', project:'nas', engine:'auto', archived:false, messages:[
    {role:'user', text:'faz isto', ts:1},
    {role:'assistant', text:'Feito parte. Porta de qualidade: reprovada. O que falta: commit.', ts:2, turn_id:'t-A'},
    {role:'user', text:'ok', ts:3},
    {role:'assistant', text:'Tudo feito e verificado.', ts:4, turn_id:'t-B'}]};
  let sends = [];
  await p.route(/\/api\/chat\?id=/, r => r.fulfill({json:{chat}}));
  await p.route(/\/api\/chat\/send/, r => { sends.push(JSON.parse(r.request().postData())); const c = JSON.parse(JSON.stringify(chat)); c.messages.push({role:'user', text:sends.at(-1).text, ts:5}); r.fulfill({json:{chat:c, job:null, queued:false}}); });
  await p.route(/\/api\/chats(\?|$)/, r => r.fulfill({json:{chats:[{id:'chat-t1',title:'ZZTESTE',project:'nas',engine:'auto',archived:false,pinned:false,msg_count:4,updated_at:9e9,activity:{state:'idle'}}],archived_chats:[],folders:[]}}));
  await p.goto(BASE); await p.waitForTimeout(1500);
  await p.getByText('ZZTESTE').first().click();
  await p.waitForTimeout(1500);
  const btns = () => p.$$eval('button', bs => bs.filter(b => /Continuar trabalho|Implementação pedida/.test(b.textContent) && b.textContent.length<40).map(b => b.textContent));
  const a = await btns(); console.log('antes (última sem pendência):', a); assert.deepStrictEqual(a, []);
  chat.messages.pop(); await p.reload(); await p.waitForTimeout(1500); await p.getByText('ZZTESTE').first().click(); await p.waitForTimeout(1500);
  const c = await btns(); console.log('última com pendência:', c); assert.deepStrictEqual(c, ['▶ Continuar trabalho']);
  const bt = p.locator('button', {hasText:'Continuar trabalho'}).first();
  await bt.click(); await p.waitForTimeout(1000);
  await bt.click({force:true}).catch(()=>{}); await p.waitForTimeout(500);
  const f = await btns(); console.log('envios:', sends.length, 'botão:', f);
  assert.strictEqual(sends.length, 1); assert.match(sends[0].text, /\[apply-proposal-t-A\]/); assert.ok(f.some(t => /Implementação pedida/.test(t)));
  console.log('OK');
  await b.close();
})().catch(e => { console.error(e); process.exit(1); });
