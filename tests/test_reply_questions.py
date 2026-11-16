"""Decision requests without a question mark must remain answerable."""
import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class ReplyQuestionTests(unittest.TestCase):
    def test_open_decisions_and_options(self):
        source = (ROOT / 'ui/app.js').read_text()
        detector = source[source.index('window.maestroReplyPrompts ='):source.index('(function(){\n  var state')]
        options = source[source.index('  function qaOptions('):source.index('  function attachReplyBoxes(')]
        harness = r'''
const assert = require('node:assert/strict');
const window = {};
function el(text, cls='rich-p', excluded=false) {
 return {textContent:text, className:cls, tagName:'DIV', contains:()=>false,
 closest:()=>excluded?{}:null};
}
function prompts(...nodes) {return window.maestroReplyPrompts({querySelectorAll:()=>nodes});}
'''
        checks = r'''
const actual = 'O produto é descrito como «Supplied as a matched pair» e «2-piece pair», mas o preço aparece como «150 €–200 € por unidade». Tens de decidir se os 150–200 € são por painel ou pelo par.';
assert.equal(prompts(el(actual)).length,1);
assert.deepEqual(qaOptions(actual), ['por painel','pelo par']);
assert.equal(prompts(el('Qual preferes? Podes escrever outra opção.')).length,1);
assert.equal(prompts(el('Preciso de saber o preço final.')).length,1);
assert.equal(prompts(el('You need to decide whether prices are per panel or per pair.')).length,1);
assert.deepEqual(qaOptions('You need to decide whether prices are per panel or per pair.'), ['per panel','per pair']);
assert.equal(prompts(el('Questão em aberto (ponto 3)','rich-h3'),el('Preço por painel ou pelo par.')).length,1);
assert.equal(prompts(el('Questão em aberto','rich-h3'),el('Concluído','rich-h3'),el('Usamos Fusion ou SolidWorks.')).length,0);
assert.equal(prompts(el('O preço é por painel ou pelo par.')).length,0);
assert.equal(prompts(el('Já decidi usar o preço por painel.')).length,0);
assert.equal(prompts(el(actual,'rich-p',true)).length,0);
assert.deepEqual(qaOptions('Que cor queres usar (azul, verde ou coral)?'),['azul','verde','coral']);
assert.deepEqual(qaOptions('Qual é o preço final?'),[]);
'''
        result = subprocess.run(['node', '-e', harness + detector + options + checks], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
