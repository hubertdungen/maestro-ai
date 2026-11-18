// node tests/test_decline.js — recusa de sugestões e de comandos, local e no servidor.
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const src = fs.readFileSync(__dirname + '/../ui/app.js', 'utf8');
const a = src.indexOf('  var PENDING_RE'), b = src.indexOf('  function applyImplementationSuggestion');
let calls = [], store = {}, refreshed = 0;
const msg = { role:'assistant', turn_id:'t1', text:'Sugiro implementar esta alteração no código.' };
const chat = { id:'c1', messages:[msg], dismissed:{} };
const ctx = {
  state:{currentChat:chat, messageQueue:[]},
  localStorage:{getItem:k=>store[k]||null, setItem:(k,v)=>{store[k]=v;}},
  hash32:s=>String(s.length), ui:p=>p, selectedModelConfig:()=>({}),
  renderMessages(){ refreshed++; }, updateJumpUpBtn(){}, receiveQueue:()=>false, notifyUser(){},
  api:(u,body)=>{ calls.push([u,body]); return Promise.resolve({}); }, JSON, Object, String, Date
};
vm.createContext(ctx);
vm.runInContext(src.slice(a, b) + ';this.isSeen=isSeen;this.dismissSuggestion=dismissSuggestion;this.codeDismissKey=codeDismissKey;this.suggestionKey=suggestionKey;', ctx);
assert.strictEqual(ctx.isSeen(msg), false);
const nodes = [{removed:false, remove(){this.removed=true;}}, {removed:false, remove(){this.removed=true;}}];
ctx.dismissSuggestion(msg, nodes);
assert.strictEqual(ctx.isSeen(msg), true, 'local esconde já');
assert(nodes.every(n=>n.removed), 'botões removidos');
assert.strictEqual(calls.length, 1, 'uma chamada ao servidor');
assert.strictEqual(calls[0][0], '/api/chat/dismiss');
assert.strictEqual(calls[0][1].keys.join(','), ctx.suggestionKey(msg));
assert.strictEqual(chat.dismissed[ctx.suggestionKey(msg)] > 0, true, 'mapa da conversa atualizado');
assert.strictEqual(ctx.codeDismissKey('docker ps'), ctx.codeDismissKey('docker ps'), 'chave estável');
assert.notStrictEqual(ctx.codeDismissKey('docker ps'), ctx.codeDismissKey('docker images'), 'chave por comando');
console.log('ok');
