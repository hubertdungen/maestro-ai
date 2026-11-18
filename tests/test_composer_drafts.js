// node tests/test_composer_drafts.js — rascunhos isolados por conversa (texto + anexos).
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const src = fs.readFileSync(__dirname + '/../ui/app.js', 'utf8');
const a = src.indexOf('  var DRAFT_STORE'), b = src.indexOf('  function persistLastChat');
let store = {}, rendered = 0;
const ta = { value: '', style: {}, scrollHeight: 24 };
const state = { currentChat: { id: 'A' }, pendingImages: [], pendingDocs: [] };
const ctx = {
  state, ta, localStorage: { getItem: k => store[k] || null, setItem: (k, v) => { store[k] = v; } },
  renderAttachStrip() { rendered++; }, syncComposerBusyUI() {}, Math, Date, JSON, Object, String,
  setTimeout, clearTimeout
};
vm.createContext(ctx);
vm.runInContext(src.slice(a, b) + ';this.appendDraftAttachment=appendDraftAttachment;this.persistDraft=persistDraft;this.restoreDraft=restoreDraft;this.clearDraft=clearDraft;', ctx);

// A: texto + imagem + documento
ta.value = 'pedido A';
state.pendingImages = ['/pastes/a.png'];
state.pendingDocs = [{ path: '/docs/a.md', name: 'a.md' }];
ctx.persistDraft(true);
const all = () => JSON.parse(store.maestro_drafts_v2 || '{}');
assert.equal(all().A.text, 'pedido A');
assert.deepEqual(all().A.images, ['/pastes/a.png']);
assert.equal(all().A.docs.length, 1);

// Trocar para B sem rascunho: limpa tudo, não herda A.
state.currentChat = { id: 'B' };
ctx.restoreDraft();
assert.equal(ta.value, '', 'B não pode herdar o texto de A');
assert.deepEqual(state.pendingImages, [], 'B não pode herdar imagens de A');
assert.deepEqual(state.pendingDocs, [], 'B não pode herdar documentos de A');

// B: rascunho próprio
ta.value = 'pedido B';
state.pendingImages = ['/pastes/b.png'];
ctx.persistDraft(true);
assert.equal(all().B.text, 'pedido B');
assert.deepEqual(all().A.images, ['/pastes/a.png'], 'gravar B não altera A');

// Voltar a A: restaura exatamente A.
state.currentChat = { id: 'A' };
ctx.restoreDraft();
assert.equal(ta.value, 'pedido A');
assert.deepEqual(state.pendingImages, ['/pastes/a.png']);
assert.equal(state.pendingDocs[0].name, 'a.md');

// Timer pendente de A não pode escrever no slot de B depois da troca.
ta.value = 'A ainda a escrever';
state.currentChat = { id: 'A' };
ctx.persistDraft(false);
ctx.persistDraft(true); // flush que openChat faz antes de trocar de id
state.currentChat = { id: 'B' };
ctx.restoreDraft(); // cancela qualquer timer pendente
setTimeout(() => {
  assert.equal(all().B.text, 'pedido B', 'timer de A invadiu B');
  assert.equal(all().A.text, 'A ainda a escrever', 'o flush antes da troca perdeu o texto de A');
  // clearDraft só apaga o slot atual.
  ctx.clearDraft();
  assert.equal(all().B, undefined);
  assert.equal(all().A.text, 'A ainda a escrever');
  ctx.appendDraftAttachment('A', 'images', '/pastes/late.png');
  assert.deepEqual(state.pendingImages, ['/pastes/b.png'], 'upload tardio não invade B');
  state.currentChat = { id: 'A' }; ctx.restoreDraft();
  assert.equal(state.pendingImages.at(-1), '/pastes/late.png');
  assert.equal(ta.value, 'A ainda a escrever');
  // Clearing a submitted draft cancels pending debounce writes.
  ctx.persistDraft(false); ctx.clearDraft();
  assert.equal(all().A, undefined);
  assert(rendered > 0);
  console.log('ok');
}, 350);
