// node tests/test_composer_images.js — imagens coladas/arrastadas para o prompt.
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const src = fs.readFileSync(__dirname + '/../ui/app.js', 'utf8');
const a = src.indexOf('  function imageFiles'), b = src.indexOf('  function attachImagePath');
let uploads = 0, rendered = 0, persisted = 0, notices = 0;
const state = { pendingImages: [] };
class FakeFileReader {
  readAsDataURL(file) {
    this.result = 'data:' + (file.type || 'image/png') + ';base64,AAAA';
    setTimeout(() => this.onload(), 0);
  }
}
const ctx = {
  state, FileReader: FakeFileReader, Promise, Array, String, URL, RegExp,
  location: { href: 'http://nas:8109/', origin: 'http://nas:8109' },
  ui: p => p, notifyUser() { notices++; },
  renderAttachStrip() { rendered++; }, syncComposerBusyUI() {},
  persistDraft() { persisted++; },
  api(url, body) { uploads++; return Promise.resolve({ path: '/pastes/up-' + uploads + '.png' }); }
};
vm.createContext(ctx);
vm.runInContext(src.slice(a, b) + ';this.imageFiles=imageFiles;this.clipboardImageFiles=clipboardImageFiles;this.localImagePath=localImagePath;this.addImageFiles=addImageFiles;', ctx);

const png = { name: 'x.png', type: 'image/png' };
const txt = { name: 'x.txt', type: 'text/plain' };
assert.deepEqual(ctx.imageFiles([png, txt]), [png]);
assert.deepEqual(ctx.clipboardImageFiles({ files: [png], items: [] }), [png], 'clipboard só com files');
assert.deepEqual(ctx.clipboardImageFiles({ items: [{ kind: 'file', type: 'image/png', getAsFile: () => png }] }), [png], 'clipboard com items');
assert.deepEqual(ctx.clipboardImageFiles({ files: [png], items: [{ kind: 'file', type: 'image/png', getAsFile: () => png }] }), [png], 'sem duplicar');
assert.equal(ctx.localImagePath('/api/image?path=%2Fpastes%2Fa.png'), '/pastes/a.png');
assert.equal(ctx.localImagePath('/srv/appdata/pastes/a.png'), '/srv/appdata/pastes/a.png');
assert.equal(ctx.localImagePath('https://outro.example/a.png'), '');

ctx.addImageFiles([png, txt]).then(n => {
  assert.equal(n, 1, 'só a imagem é enviada');
  assert.deepEqual(state.pendingImages, ['/pastes/up-1.png']);
  assert(uploads === 1 && rendered === 1 && persisted === 1);
  assert.equal(notices, 0);
  console.log('ok');
}).catch(e => { console.error(e); process.exit(1); });
