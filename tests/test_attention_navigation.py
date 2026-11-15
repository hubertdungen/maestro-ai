"""Regression: every unresolved action participates in attention navigation."""
import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class AttentionNavigationTests(unittest.TestCase):
    def test_pending_controls_and_navigation(self):
        source = (ROOT / 'ui/app.js').read_text()
        start = source.index('  function pendingAttentionTargets()')
        end = source.index('  function stickToBottom', start)
        harness = r'''
const assert = require('node:assert/strict');
let controls = [], question = null, selector;
function control(top, status, visible=true) {
  return {dataset:{actionState:status}, hidden:false,
    getClientRects:()=>visible?[{}]:[], getBoundingClientRect:()=>({top}),
    contains:el=>el===question, querySelector:()=>null};
}
const box = {querySelectorAll:s=>{selector=s;return controls;},
  contains:()=>true, getBoundingClientRect:()=>({top:100})};
const state = {currentChat:{}};
function chatScrollBox(){return box;}
const classes = new Set(); const count = {};
const up = {classList:{toggle:(k,v)=>v?classes.add(k):classes.delete(k)},
  setAttribute:(k,v)=>up[k]=v, querySelector:()=>count};
const document = {getElementById:()=>up};
function ui(pt,en){return en;}
'''
        checks = r'''
controls = [control(20),control(80),control(300)];
assert.equal(pendingAttentionTarget(),controls[1]);
updateJumpUpBtn(); assert(classes.has('show')); assert.equal(count.textContent,3);
assert(selector.includes('.apply-implementation:not(:disabled)'));
assert(selector.includes('[data-code-action]:not(:disabled)'));
controls = [control(300)]; // Still visible: keep the attention indicator.
assert.equal(pendingAttentionTarget(),controls[0]);
for (const status of ['running','starting','succeeded','cancelled']) {
  controls = [control(20,status)]; updateJumpUpBtn(); assert(!classes.has('show'));
}
for (const status of ['pending','failed','expired']) {
  controls = [control(20,status)]; updateJumpUpBtn(); assert(classes.has('show'));
}
controls = [control(20,undefined,false)]; assert.equal(pendingAttentionTarget(),null);
controls = []; question = control(10); box.__pendingQuestionTurn=question;
assert.equal(pendingAttentionTarget(),question);
question.querySelector=()=>({disabled:true}); assert.equal(pendingAttentionTarget(),null);
question.querySelector=()=>null; controls=[question];
assert.equal(pendingAttentionTargets().length,1); // No duplicate question/control.
state.currentChat.archived=true; updateJumpUpBtn(); assert(!classes.has('show'));
assert.equal(up.tabIndex,-1);
'''
        result = subprocess.run(['node', '-e', harness + source[start:end] + checks],
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
