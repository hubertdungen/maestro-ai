"""Exercise the real Pulso launch handler without sending a model request."""
import pathlib
import subprocess
import unittest


class PulseNewChatTests(unittest.TestCase):
    def test_launch_isolated_from_busy_chat_and_handles_creation_failure(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        source = (root / 'maestro_web.py').read_text() + (root / 'ui' / 'app.js').read_text()
        handler = source.split('      function launchPulse(sel, analyze) {', 1)[1].split('\n      box.innerHTML =', 1)[0]
        script = r'''
const assert = require('node:assert/strict');
let state, sent, calls, failure, brokenOpen, notices;
const input = {value:'unsent draft', style:{}, focus(){}};
const document = {getElementById(){return input;}};
function buildPulsePrompt(sel, analyze){return (analyze?'analyze:':'resolve:')+sel.join(',');}
function persistDraft(){calls.push(['draft', state.currentChat.id, input.value]);}
async function api(path, body){
  calls.push([path, body]);
  if(failure) throw Error('offline');
  return {chat:{id:'pulse-new'}};
}
async function openChat(id){if(!brokenOpen) state.currentChat={id};}
function renderAttachStrip(){}
function setModule(){}
function closeMobileDrawers(){}
function refreshChats(){}
function notifyUser(){notices++;}
async function dispatchChatTurn(...args){sent.push([state.currentChat.id,...args]);}
function reset(){
  state={currentChat:{id:'old-busy',messages:['old history']},pendingImages:['old.png'],pendingDocs:['old.txt']};
  calls=[];sent=[];failure=false;brokenOpen=false;notices=0;input.value='unsent draft';
}
''' + 'function launchPulse(sel, analyze) {' + handler + r'''
(async()=>{
  for(const analyze of [false,true]){
    reset();
    const old=state.currentChat;
    const pending=launchPulse(['alert'], analyze);
    assert.equal(launchPulse(['alert'], analyze),undefined);
    await pending;
    assert.deepEqual(old,{id:'old-busy',messages:['old history']});
    assert.deepEqual(calls[0],['draft','old-busy','unsent draft']);
    assert.deepEqual(calls[1],['/api/chat/new',{project:'nas',engine:'auto'}]);
    assert.deepEqual(sent,[['pulse-new',(analyze?'analyze:':'resolve:')+'alert',[],'nas','auto',false,[]]]);
    assert.deepEqual(state.pendingImages,[]);
    assert.deepEqual(state.pendingDocs,[]);
    assert.equal(state.pulseLaunching,false);
  }
  reset();failure=true;await launchPulse(['alert']);
  assert.equal(sent.length,0);assert.equal(state.currentChat.id,'old-busy');
  assert.equal(input.value,'unsent draft');assert.equal(notices,1);
  assert.equal(state.pulseLaunching,false);
  reset();brokenOpen=true;await launchPulse(['alert']);
  assert.equal(sent.length,0);assert.equal(notices,1);
  reset();await launchPulse([]);assert.equal(calls.length,0);
})().catch(e=>{console.error(e);process.exitCode=1;});
'''
        result = subprocess.run(['node', '-e', script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
