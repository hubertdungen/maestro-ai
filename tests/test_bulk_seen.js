// node tests/test_bulk_seen.js — extrai o bloco «Visto/Continuar todas» de ui/app.js e testa com stubs.
const fs = require('fs'), vm = require('vm');
const src = fs.readFileSync(__dirname + '/../ui/app.js', 'utf8');
const a = src.indexOf('  var PENDING_RE'), b = src.indexOf('  function applyImplementationSuggestion');
let sent = null, store = {};
const msgs = [];
for (let i = 0; i < 4; i++) msgs.push({role:'assistant', turn_id:'t'+i, text:'Sugiro implementar a alteração no código '+i});
msgs.push({role:'assistant', turn_id:'t9', text:'Tudo feito.'});
const ctx = { state:{currentChat:{id:'c1', messages:msgs}, messageQueue:[]}, localStorage:{getItem:k=>store[k]||null, setItem:(k,v)=>{store[k]=v;}},
  hash32:s=>String(s.length), ui:(p)=>p, selectedModelConfig:()=>({}), renderMessages(){}, updateJumpUpBtn(){}, receiveQueue:()=>false, notifyUser(){},
  api:(u,body)=>{ sent = body; return Promise.resolve({}); }, JSON, Object, String, Date };
vm.createContext(ctx); vm.runInContext(src.slice(a, b) + ';this.bulkMsgs=bulkMsgs;this.bulkSeen=bulkSeen;this.bulkContinue=bulkContinue;', ctx);
const assert = require('assert');
assert.strictEqual(ctx.bulkMsgs().length, 4);
ctx.bulkContinue({disabled:false});
setTimeout(() => {
  assert(sent && /apply-all-4-/.test(sent.text) && /4\. /.test(sent.text), 'mega-reply');
  assert.strictEqual(ctx.bulkMsgs().length, 0, 'marcadas como vistas após enviar');
  store = {}; assert.strictEqual(ctx.bulkMsgs().length, 4);
  ctx.bulkSeen(); assert.strictEqual(ctx.bulkMsgs().length, 0, 'visto em todas');
  console.log('ok');
}, 20);
