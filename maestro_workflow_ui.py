"""Small UI additions for model selection and inspectable orchestration.

Paste CSS before the closing style tag and JS inside the app's existing IIFE.
The host owns request payloads and invokes renderWorkflow(snapshot) where desired.
"""

CSS = r"""
.wf-open{margin-top:8px;padding:5px 9px;border:1px solid #38bdf855;border-radius:7px;background:transparent;color:var(--cyan,#38bdf8);cursor:pointer;font:inherit}
.wf-subchats{display:grid;gap:3px;margin:8px 0 2px 5px;padding-left:9px;border-left:1px solid #38bdf855}.wf-subchats button{display:flex;align-items:center;gap:6px;width:100%;min-width:0;border:0;background:transparent;color:inherit;padding:6px 2px;text-align:left;cursor:pointer;font-size:12px}.wf-subchats button span:last-child{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.wf-dot{color:#94a3b8}.wf-dot[data-status=running]{color:#38bdf8}.wf-dot[data-status=done]{color:#4ade80}.wf-dot[data-status=failed],.wf-dot[data-status=blocked],.wf-dot[data-status=interrupted]{color:#fb7185}
.wf-subchat{width:min(740px,calc(100vw - 24px))}.wf-subchat-log{max-height:45dvh;overflow:auto;margin:12px 0;overflow-wrap:anywhere}.wf-subchat-log article{border-left:2px solid #64748b66;padding:8px 12px;margin:8px 0}.wf-subchat-log article.user{border-color:#e879f9}.wf-subchat-log pre{white-space:pre-wrap;font:inherit;font-size:13px;margin:6px 0}.wf-subchat textarea{box-sizing:border-box;display:block;width:100%;min-height:84px;resize:vertical;background:var(--card,#182133);color:inherit;border:1px solid #64748b66;border-radius:9px;padding:10px;font:inherit}.wf-subchat-meta{font-size:12px;color:var(--muted,#a6b1c1)}
.wf-panel{margin:12px 0;padding:14px;border:1px solid var(--border,rgba(56,189,248,.25));border-radius:14px;background:var(--panel,#111827);font-family:system-ui,sans-serif;font-size:13px;line-height:1.5;overflow-anchor:none;max-width:100%;box-sizing:border-box}
.wf-head{display:flex;align-items:center;flex-wrap:wrap;gap:8px;justify-content:space-between;margin-bottom:7px}.wf-head strong{font-size:14px}.wf-count{font-size:12px;color:var(--cyan,#38bdf8);font-variant-numeric:tabular-nums}
.wf-reason,.wf-coordinator{margin:5px 0;overflow-wrap:anywhere}.wf-coordinator{color:var(--muted,#a6b1c1);font-size:12px}.wf-nodes{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(230px,100%),1fr));gap:8px;list-style:none!important;margin:12px 0 8px!important;padding:0!important}
.wf-node-state{overflow-wrap:normal;white-space:nowrap;flex:none}.wf-node{padding:10px;border:1px solid rgba(148,163,184,.24);border-left:3px solid #64748b;border-radius:9px;min-width:0;list-style:none!important;overflow-wrap:anywhere}.wf-node[data-status=running]{border-left-color:#38bdf8}.wf-node[data-status=done],.wf-node[data-status=completed]{border-left-color:#4ade80}.wf-node[data-status=failed],.wf-node[data-status=blocked],.wf-node[data-status=interrupted]{border-left-color:#fb7185}.wf-node-title{display:flex;justify-content:space-between;gap:8px}.wf-node-title strong{font-size:13px}.wf-node small{display:block;margin-top:5px;color:var(--muted,#a6b1c1)}.wf-node-state{font-size:11px;white-space:nowrap}.wf-node-title strong{flex:1;min-width:0}.wf-note{font-size:11px;color:var(--muted,#a6b1c1);margin:8px 0 0}.wf-output{margin-top:8px;font-size:12px}.wf-output summary{cursor:pointer}.wf-output pre{white-space:pre-wrap;max-height:230px;overflow:auto;font-size:12px;word-break:break-word}
.wf-model-dialog{color:var(--text,#e5e7eb);background:var(--panel,#111827);border:1px solid var(--border,rgba(56,189,248,.3));border-radius:18px;padding:20px;width:min(560px,calc(100vw - 32px));max-height:calc(100dvh - 40px);box-sizing:border-box;overflow:auto;box-shadow:0 18px 80px #0008}.wf-model-dialog::backdrop{background:#0009}.wf-model-dialog h2{font-size:19px;margin:0}.wf-model-dialog .wf-model-row{display:grid;gap:6px;margin:14px 0}.wf-model-dialog label{font-size:13px;font-weight:600}.wf-model-dialog select{width:100%;min-height:42px;box-sizing:border-box;border:1px solid #64748b66;border-radius:9px;padding:9px;background:var(--card,#182133);color:inherit;font-size:14px}.wf-model-dialog p{font-size:13px;line-height:1.5;overflow-wrap:anywhere}.wf-model-list{margin:10px 0;padding-left:18px;font-size:12px;line-height:1.6}.wf-model-list li{margin-bottom:7px}.wf-model-dialog .wf-dialog-head{display:flex;justify-content:space-between;align-items:center;gap:12px}.wf-dialog-head button{min-width:40px;min-height:40px}.wf-model-status{color:var(--muted,#a6b1c1);min-height:2.8em}.wf-model-list [data-available=false]{opacity:.7}
.wf-subchat{padding:0;border-radius:20px;display:flex;flex-direction:column;overflow:hidden;animation:wfPop .22s cubic-bezier(.2,.9,.3,1.2)}
.wf-subchat[open]::backdrop{animation:wfFade .2s ease}
.wf-subchat .wf-dialog-head{padding:16px 18px 4px}.wf-subchat .wf-dialog-head h2{font-size:17px;line-height:1.3;letter-spacing:-.01em}
.wf-subchat .wf-dialog-head button{border:0;border-radius:50%;min-width:34px;min-height:34px;width:34px;height:34px;padding:0;font-size:18px;line-height:1;background:var(--card,#182133);color:var(--muted,#94a3b8);cursor:pointer;transition:background .15s,color .15s,transform .15s}
.wf-subchat .wf-dialog-head button:hover{background:var(--card-hover,#192746);color:var(--text,#fff);transform:rotate(90deg)}
.wf-subchat>.wf-subchat-meta:first-of-type{display:flex;flex-wrap:wrap;align-items:center;gap:6px;margin:6px 18px 0}
.wf-pill{display:inline-flex;align-items:center;gap:6px;padding:2px 10px;border-radius:999px;background:var(--card,#182133);font-size:11px;color:var(--text,#fff)}
.wf-pill i{width:7px;height:7px;border-radius:50%;background:var(--muted,#94a3b8)}
.wf-pill[data-s=running] i{background:var(--cyan,#38bdf8);animation:wfPulse 1.2s ease-in-out infinite}.wf-pill[data-s=done] i,.wf-pill[data-s=completed] i{background:var(--green,#34d399)}.wf-pill[data-s=failed] i,.wf-pill[data-s=blocked] i{background:var(--rose,#f43f5e)}
.wf-subchat .wf-subchat-log{display:flex;flex-direction:column;gap:10px;max-height:50dvh;margin:0;padding:14px 18px;scroll-behavior:smooth}
.wf-subchat .wf-subchat-log article{border:0;margin:0;padding:10px 14px;border-radius:16px 16px 16px 4px;background:var(--card,#182133);max-width:88%;align-self:flex-start}
.wf-subchat .wf-subchat-log article.task{align-self:stretch;max-width:100%;border-radius:12px;background:transparent;border:1px dashed var(--line-strong,#38bdf855);font-size:12px}
.wf-subchat .wf-subchat-log article.task pre{max-height:5.2em;overflow:auto}
.wf-subchat .wf-subchat-log article.user{align-self:flex-end;border-radius:16px 16px 4px 16px;background:color-mix(in srgb,var(--violet,#a78bfa) 28%,var(--card,#182133))}
.wf-subchat .wf-subchat-log article.error{background:color-mix(in srgb,var(--rose,#f43f5e) 22%,var(--card,#182133))}
.wf-subchat .wf-subchat-log article small{display:block;font-size:10.5px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted,#94a3b8)}
.wf-subchat .wf-subchat-log article.live{border:1px solid var(--line-strong,#38bdf855);background:transparent}
.wf-subchat .wf-subchat-log article.wf-new{animation:wfIn .28s ease both}
.wf-typing{display:inline-flex;gap:3px;margin-left:6px;vertical-align:middle}.wf-typing b{width:4px;height:4px;border-radius:50%;background:currentColor;animation:wfDot 1s infinite ease-in-out}.wf-typing b:nth-child(2){animation-delay:.15s}.wf-typing b:nth-child(3){animation-delay:.3s}
.wf-subchat form{padding:12px 18px 16px;border-top:1px solid var(--line,#ffffff14);background:var(--glass,transparent)}.wf-subchat form label{font-size:12px;color:var(--muted,#94a3b8)}
.wf-subchat textarea{min-height:56px;margin-top:6px;border-radius:12px;transition:border-color .15s,box-shadow .15s}.wf-subchat textarea:focus{outline:0;border-color:var(--cyan,#38bdf8);box-shadow:0 0 0 3px color-mix(in srgb,var(--cyan,#38bdf8) 25%,transparent)}
.wf-subchat form button[type=submit]{border-radius:999px;padding:8px 18px;cursor:pointer;transition:transform .12s}.wf-subchat form button[type=submit]:active{transform:scale(.96)}
#wfSubchatStatus{margin:0 18px;min-height:0}
@keyframes wfPop{from{opacity:0;transform:translateY(14px) scale(.97)}}
@keyframes wfFade{from{opacity:0}}
@keyframes wfIn{from{opacity:0;transform:translateY(8px)}}
@keyframes wfPulse{50%{opacity:.35;transform:scale(.7)}}
@keyframes wfDot{0%,80%,100%{opacity:.25;transform:translateY(0)}40%{opacity:1;transform:translateY(-3px)}}
.wf-chip{display:inline-flex;padding:2px 9px;border-radius:999px;background:var(--card,#182133);font-size:11px;color:var(--muted,#94a3b8);font-variant-numeric:tabular-nums}
.wf-think-now{display:flex;align-items:center;gap:9px;font-size:13px;font-weight:600}
.wf-spin{flex:none;width:14px;height:14px;border-radius:50%;border:2px solid color-mix(in srgb,var(--cyan,#38bdf8) 30%,transparent);border-top-color:var(--cyan,#38bdf8);animation:wfSpin .8s linear infinite}
@keyframes wfSpin{to{transform:rotate(360deg)}}
.wf-steps{list-style:none;margin:10px 0 2px;padding:0 0 0 6px;display:grid;gap:5px;border-left:1px solid var(--line,#ffffff18)}
.wf-steps li{display:flex;gap:8px;align-items:baseline;font-size:12px;color:var(--muted,#94a3b8);padding-left:8px}
.wf-steps li i{flex:none;width:12px;font-style:normal;font-size:10px;color:var(--green,#34d399)}
.wf-steps li.now{color:var(--text,#fff)}.wf-steps li.now i{width:6px;height:6px;margin:0 3px;border-radius:50%;background:var(--cyan,#38bdf8);animation:wfPulse 1.2s ease-in-out infinite}
.wf-partial{margin-top:10px;font-size:12px}.wf-partial summary{cursor:pointer;color:var(--muted,#94a3b8);font-size:11px;text-transform:uppercase;letter-spacing:.06em}
.wf-partial pre{max-height:9em;overflow:auto;opacity:.85}
.wf-rich{font-size:13.5px;line-height:1.55;overflow-wrap:anywhere}.wf-rich>:first-child{margin-top:0}.wf-rich>:last-child{margin-bottom:0}
.wf-rich .rich-h1,.wf-rich .rich-h2,.wf-rich .rich-h3{font-size:14.5px;margin:12px 0 6px}
.wf-rich .rich-table-wrap{overflow:auto;max-width:100%}
.wf-subchat .wf-subchat-log article.agent{max-width:94%;border:1px solid var(--line,#ffffff14)}
.wf-partial .wf-rich{max-height:11em;overflow:auto;opacity:.85;padding:8px 10px;border-radius:10px;background:var(--card,#182133)}
.wf-think{padding:2px 0}.wf-think-now span:last-child{background:linear-gradient(90deg,var(--cyan,#38bdf8),var(--violet,#a78bfa));-webkit-background-clip:text;background-clip:text;color:transparent}
@media(prefers-reduced-motion:reduce){.wf-subchat,.wf-subchat *,.wf-model-dialog,.wf-model-dialog *{animation:none!important;transition:none!important;scroll-behavior:auto!important}}
.wf-pills{display:flex;flex-wrap:wrap;gap:8px}.wf-pill{min-height:38px;padding:7px 14px;border-radius:999px;border:1px solid #64748b55;background:var(--card,#182133);color:inherit;font:inherit;font-size:13px;cursor:pointer;transition:background .15s,border-color .15s,transform .1s}.wf-pill:hover:not(:disabled){border-color:var(--accent,#ec4899);transform:translateY(-1px)}.wf-pill:active:not(:disabled){transform:scale(.96)}.wf-pill[aria-pressed=true]{background:var(--accent,#ec4899);border-color:var(--accent,#ec4899);color:#fff;font-weight:600}.wf-pill:disabled{opacity:.4;cursor:not-allowed}
.wf-model-dialog input[type=range]{-webkit-appearance:none;appearance:none;width:100%;height:28px;background:transparent;cursor:pointer;--p:0%}.wf-model-dialog input[type=range]::-webkit-slider-runnable-track{height:6px;border-radius:3px;background:linear-gradient(90deg,var(--accent,#ec4899) var(--p),#64748b55 var(--p))}.wf-model-dialog input[type=range]::-moz-range-track{height:6px;border-radius:3px;background:#64748b55}.wf-model-dialog input[type=range]::-moz-range-progress{height:6px;border-radius:3px;background:var(--accent,#ec4899)}.wf-model-dialog input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;width:22px;height:22px;margin-top:-8px;border-radius:50%;background:#fff;border:3px solid var(--accent,#ec4899);box-shadow:0 2px 8px #0008}.wf-model-dialog input[type=range]::-moz-range-thumb{width:16px;height:16px;border-radius:50%;background:#fff;border:3px solid var(--accent,#ec4899)}.wf-model-dialog input[type=range]:disabled{opacity:.35;cursor:not-allowed}
.wf-ticks{display:flex;justify-content:space-between;font-size:11px;color:var(--muted,#94a3b8)}.wf-ticks span{cursor:pointer;padding:2px 0}.wf-ticks .on{color:var(--text,#fff);font-weight:700}.wf-effort-val{color:var(--accent,#ec4899);margin-left:6px}
.wf-model-dialog>.wf-dialog-head button{border:0;border-radius:50%;width:34px;height:34px;min-width:34px;min-height:34px;padding:0;font-size:18px;line-height:1;background:var(--card,#182133);color:var(--muted,#94a3b8);cursor:pointer;transition:background .15s,color .15s,transform .15s}.wf-model-dialog>.wf-dialog-head button:hover{background:var(--card-hover,#192746);color:var(--text,#fff)}@media(hover:hover){.wf-model-dialog>.wf-dialog-head button:hover{transform:rotate(90deg)}}
@media(max-width:640px){.wf-panel{padding:11px}.wf-nodes{grid-template-columns:1fr}.wf-model-dialog{padding:16px}.wf-node-title{flex-wrap:wrap}}
"""

JS = r"""
  // Persistent per-provider model choices; waiting tasks have no live LLM process.
  var workflowModels = [];
  var workflowModelPrefs = {};
  try { workflowModelPrefs = JSON.parse(localStorage.getItem('maestro_model_preferences') || '{}') || {}; } catch(e) {}
  function wfModelShown(id,engine){var m=String(id||'').split(':').pop();return m && String(engine||'').toLowerCase().indexOf(m.toLowerCase())===-1?m:'';}
    function wfText(value) { return String(value == null ? '' : value).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;'); }
  function wfFamily(engine) {
    return /^(claude|sonnet|opus|haiku|fable)/.test(engine || '') ? 'claude' : engine;
  }
  function wfEngine() { var s=document.getElementById('selEngine'); return s ? s.value : 'auto'; }
  function selectedModelConfig() {
    var engine=wfEngine(), pref=workflowModelPrefs[engine] || {};
    if (engine === 'auto') return {model_id:'', effort:''};
    var entry=workflowModels.find(function(m){ return m.id===pref.model_id && m.available && wfFamily(m.engine)===wfFamily(engine); });
    if (!entry) return {model_id:'', effort:''};
    return {model_id:entry.id, effort:(entry.efforts || []).indexOf(pref.effort)!==-1 ? pref.effort : (entry.default_effort || '')};
  }
  function wfSaveModelPreference() {
    var model=document.getElementById('wfModelSelect'), effort=document.getElementById('wfEffortSelect');
    if (!model || !effort) return;
    workflowModelPrefs[wfEngine()]={model_id:model.value,effort:effort.value};
    try { localStorage.setItem('maestro_model_preferences',JSON.stringify(workflowModelPrefs)); } catch(e) {}
  }
  function wfFillEfforts() {
    var model=document.getElementById('wfModelSelect'), effort=document.getElementById('wfEffortSelect'), note=document.getElementById('wfModelStatus');
    if (!model || !effort) return;
    var entry=workflowModels.find(function(m){return m.id===model.value;}), pref=workflowModelPrefs[wfEngine()] || {};
    effort.replaceChildren();
    var levels=entry ? (entry.efforts || []) : [];
    if (!levels.length) { var o=document.createElement('option');o.value='';o.textContent=ui('Predefinido pelo motor','Engine default');effort.appendChild(o); }
    levels.forEach(function(level){ var o=document.createElement('option');o.value=level;o.textContent=level;effort.appendChild(o); });
    effort.disabled=!levels.length;
    if (levels.indexOf(pref.effort)!==-1) effort.value=pref.effort;
    else if (entry && levels.indexOf(entry.default_effort)!==-1) effort.value=entry.default_effort;
    note.textContent=entry ? [entry.note || entry.availability_note || '', entry.source ? ui('Fonte: ','Source: ')+entry.source : ''].filter(Boolean).join(' · ') : ui('O Maestro escolhe o modelo segundo a tarefa, os motores disponíveis e as quotas.','Maestro chooses a model using the task, available engines and quotas.');
    wfPaint();
  }
  function wfChips(host,sel,onPick){
    var fv=host.contains(document.activeElement)?document.activeElement.value:null;
    host.replaceChildren();
    Array.prototype.forEach.call(sel.options,function(o){
      var b=document.createElement('button');b.type='button';b.className='wf-pill';b.value=o.value;b.textContent=o.textContent;b.disabled=o.disabled;
      b.setAttribute('aria-pressed',String(o.value===sel.value));
      b.onclick=function(){onPick(o.value);};host.appendChild(b);
      if(fv!==null&&fv===o.value&&!o.disabled)b.focus();
    });
  }
  function wfPaint(){
    var E=document.getElementById('wfEngineSelect'),M=document.getElementById('wfModelSelect'),F=document.getElementById('wfEffortSelect');
    if(!E||!M||!F||!document.getElementById('wfEngineChips'))return;
    wfChips(document.getElementById('wfEngineChips'),E,function(v){E.value=v;E.dispatchEvent(new Event('change'));});
    wfChips(document.getElementById('wfModelChips'),M,function(v){M.value=v;M.dispatchEvent(new Event('change'));});
    var r=document.getElementById('wfEffortRange'),t=document.getElementById('wfEffortTicks'),val=document.getElementById('wfEffortVal');
    var lv=Array.prototype.map.call(F.options,function(o){return o.value;}), real=!F.disabled && lv.length>0;
    r.disabled=!real;r.max=Math.max(0,lv.length-1);r.value=Math.max(0,lv.indexOf(F.value));
    t.replaceChildren();
    if(real)lv.forEach(function(l,i){var s=document.createElement('span');s.textContent=l;s.className=i===+r.value?'on':'';s.onclick=function(){r.value=i;r.oninput();};t.appendChild(s);});
    val.textContent=real?F.value:ui('Predefinido pelo motor','Engine default');
    r.style.setProperty('--p',lv.length>1?(r.value/(lv.length-1)*100)+'%':'0%');
    r.oninput=function(){F.value=lv[r.value];wfSaveModelPreference();wfPaint();};
  }
  function wfRefreshModelControls() {
    var model=document.getElementById('wfModelSelect'), engine=document.getElementById('wfEngineSelect');
    if (!model) return;
    var key=wfEngine(), pref=workflowModelPrefs[key] || {};
    if (engine) engine.value=key;
    model.replaceChildren();
    var def=document.createElement('option');def.value='';def.textContent=ui('Automático / predefinido','Automatic / default');model.appendChild(def);
    workflowModels.filter(function(m){return key!=='auto' && wfFamily(m.engine)===wfFamily(key);}).forEach(function(m){
      var o=document.createElement('option');o.value=m.id;o.disabled=!m.available;o.textContent=(m.label || m.model || m.id)+(m.available?'':ui(' — indisponível / não confirmado',' — unavailable / unverified'));model.appendChild(o);
    });
    model.disabled=key==='auto';
    if (Array.prototype.some.call(model.options,function(o){return o.value===pref.model_id && !o.disabled;})) model.value=pref.model_id;
    wfFillEfforts();
    var list=document.getElementById('wfModelInventory');
    if (list) list.innerHTML=workflowModels.map(function(m){return '<li data-available="'+(!!m.available)+'"><strong>'+wfText(m.label || m.model || m.id)+'</strong> · '+wfText(m.engine)+'<br>'+wfText(m.available?ui('Configurável neste NAS','Configurable on this NAS'):ui('Indisponível / acesso por confirmar','Unavailable / access unverified'))+(m.note || m.availability_note ? ' · '+wfText(m.note || m.availability_note):'')+(m.efforts && m.efforts.length ? '<br>'+ui('Esforço: ','Effort: ')+wfText(m.efforts.join(', ')):'')+'</li>';}).join('');
  }
  function wfModelDialogMarkup() {
    return '<div class="wf-dialog-head"><h2 id="wfModelTitle">'+ui('Modelos e esforço','Models and effort')+'</h2><button type="button" id="wfModelClose" aria-label="'+ui('Fechar','Close')+'">×</button></div>'+
      '<p>'+ui('Escolhe um motor ou deixa o Maestro coordenar. O catálogo distingue a configuração do NAS do acesso na app de cada fornecedor.','Choose an engine or let Maestro coordinate. The catalog distinguishes NAS configuration from access in each provider’s app.')+'</p>'+
      '<div class="wf-model-row"><label id="wfEngineLbl">'+ui('Motor','Engine')+'</label><div class="wf-chips" id="wfEngineChips" role="group" aria-labelledby="wfEngineLbl"></div><select id="wfEngineSelect" hidden></select></div>'+
      '<div class="wf-model-row"><label id="wfModelLbl">'+ui('Modelo','Model')+'</label><div class="wf-chips" id="wfModelChips" role="group" aria-labelledby="wfModelLbl"></div><select id="wfModelSelect" hidden></select></div>'+
      '<div class="wf-model-row"><label for="wfEffortRange">'+ui('Esforço de raciocínio','Reasoning effort')+' <b class="wf-effort-val" id="wfEffortVal"></b></label><input type="range" id="wfEffortRange" min="0" max="0" step="1"><div class="wf-ticks" id="wfEffortTicks"></div><select id="wfEffortSelect" hidden></select></div>'+
      '<p class="wf-model-status" id="wfModelStatus" role="status"></p><details><summary>'+ui('Ver catálogo e disponibilidade','View catalog and availability')+'</summary><ul id="wfModelInventory" class="wf-model-list"></ul></details>';
  }
  function initWorkflowControls() {
    var mainEngine=document.getElementById('selEngine'), menu=document.getElementById('composerMenu');
    if (!mainEngine || !menu || document.getElementById('wfModelButton')) return;
    var button=document.createElement('button');button.type='button';button.id='wfModelButton';button.className='round-tool-btn';button.innerHTML=dualIco('models','🧠')+'<span>'+ui('Modelos e esforço','Models and effort')+'</span>';button.setAttribute('aria-haspopup','dialog');
    button.hidden=true;document.body.appendChild(button);
    var dialog=document.createElement('dialog');dialog.className='wf-model-dialog';dialog.id='wfModelDialog';dialog.setAttribute('aria-labelledby','wfModelTitle');document.body.appendChild(dialog);
    button.addEventListener('click',function(){
      dialog.innerHTML=wfModelDialogMarkup();
      var engine=document.getElementById('wfEngineSelect');
      Array.prototype.forEach.call(mainEngine.options,function(existing){engine.appendChild(existing.cloneNode(true));});
      engine.addEventListener('change',function(){mainEngine.value=engine.value;mainEngine.dispatchEvent(new Event('change',{bubbles:true}));wfRefreshModelControls();});
      document.getElementById('wfModelSelect').addEventListener('change',function(){wfFillEfforts();wfSaveModelPreference();});
      document.getElementById('wfEffortSelect').addEventListener('change',wfSaveModelPreference);
      document.getElementById('wfModelClose').onclick=function(){dialog.close();};
      wfRefreshModelControls();
      menu.hidden=true;var trigger=document.getElementById('btnComposerMore');if(trigger)trigger.setAttribute('aria-expanded','false');
      dialog.showModal();
      api('/api/models').then(function(response){workflowModels=Array.isArray(response.models)?response.models:[];wfRefreshModelControls();}).catch(function(){});
    });
    dialog.addEventListener('close',function(){var trigger=document.getElementById('btnComposerMore');if(trigger)trigger.focus();});
    dialog.addEventListener('click',function(e){if(e.target===dialog){var r=dialog.getBoundingClientRect();if(e.clientX<r.left || e.clientX>r.right || e.clientY<r.top || e.clientY>r.bottom)dialog.close();}});
    mainEngine.addEventListener('change',wfRefreshModelControls);
    api('/api/models').then(function(response){workflowModels=Array.isArray(response.models)?response.models:[];wfRefreshModelControls();}).catch(function(){workflowModels=[];var note=document.getElementById('wfModelStatus');if(note)note.textContent=ui('Catálogo indisponível. Mantém-se a seleção predefinida do motor.','Catalog unavailable. The engine default remains selected.');});
  }
  function renderWorkflow(wf, turnId) {
    if (!wf || !Array.isArray(wf.nodes) || !wf.nodes.length) return '';
    var nodes=wf.nodes, running=0,waiting=0,done=0;
    var labels={pending:ui('A aguardar','Waiting'),queued:ui('Em fila','Queued'),waiting:ui('A aguardar','Waiting'),running:ui('A executar','Running'),done:ui('Concluído','Done'),completed:ui('Concluído','Done'),failed:ui('Falhou','Failed'),blocked:ui('Bloqueado','Blocked'),cancelled:ui('Cancelado','Cancelled'),stopped:ui('Interrompido','Stopped'),interrupted:ui('Interrompido','Interrupted')};
    nodes.forEach(function(n){if(n.status==='running')running++;else if(n.status==='done'||n.status==='completed')done++;else if(['pending','queued','waiting'].indexOf(n.status)!==-1)waiting++;});
    var titles={};nodes.forEach(function(n){titles[n.id]=n.title || n.id;});
    var coord=typeof wf.coordinator==='object'?(wf.coordinator.label || wf.coordinator.name || wf.coordinator.engine || ''):wf.coordinator;
    return '<section class="wf-panel" data-wf-turn="'+wfText(turnId || wf.turn_id || '')+'" aria-label="'+ui('Coordenação de agentes','Agent coordination')+'"><div class="wf-head"><strong>'+ui('Plano de trabalho','Work plan')+'</strong><span class="wf-count">'+nodes.length+' '+(nodes.length===1?ui('tarefa','task'):ui('tarefas','tasks'))+' · '+running+' '+ui('a executar','running')+' · '+waiting+' '+ui('a aguardar','waiting')+' · '+done+' '+ui('concluídas','done')+'</span></div>'+
      (wf.reason?'<p class="wf-reason">'+wfText(wf.reason)+'</p>':'')+
      (coord?'<p class="wf-coordinator">'+ui('Coordenação: ','Coordinator: ')+wfText(coord)+'</p>':'')+
      '<ol class="wf-nodes">'+nodes.map(function(n){
        var result=n.result || {},actual=result.agent_used || n.actual_agent || n.agent_used || '',engine=actual || n.engine || ui('Por atribuir','Unassigned');
        var deps=n.depends_on || n.deps || [],status=n.status || 'pending';
        var depText=deps.length?ui('Depende de: ','Depends on: ')+deps.map(function(d){return titles[d] || d;}).join(', '):ui('Sem dependências','No dependencies');
        var text=typeof result==='string'?result:result.text;
        return '<li class="wf-node" data-wf-node="'+wfText(n.id)+'" data-status="'+wfText(status)+'"><div class="wf-node-title"><strong>'+wfText(n.title || n.id)+'</strong>'+(window.mtag?window.mtag(engine,n.model || n.model_id || result.model_id):'')+'<span class="wf-node-state">'+wfText(labels[status] || status)+'</span></div><small>'+ui(actual?'Executado por: ':'Atribuído a: ',actual?'Executed by: ':'Assigned to: ')+wfText(engine)+(wfModelShown(n.model || n.model_id || result.model_id,engine)?' · '+wfText(wfModelShown(n.model || n.model_id || result.model_id,engine)):'')+((n.effort || result.effort)?' · '+wfText(n.effort || result.effort):'')+'</small><small>'+wfText(depText)+'</small>'+
          (n.reason || n.error ? '<small>'+wfText(n.error || n.reason)+'</small>':'')+
          ((wf.turn_id || turnId)?'<button type="button" class="wf-open" data-wf-turn="'+wfText(wf.turn_id || turnId)+'" data-wf-open="'+wfText(n.id)+'">'+ui('Abrir subchat','Open subchat')+'</button>':'')+
          (text?'<details class="wf-output"><summary>'+ui('Ver passagem de informação','View handoff')+'</summary><pre>'+wfText(text)+'</pre></details>':'')+'</li>';
      }).join('')+'</ol><p class="wf-note">'+ui('Tarefas a aguardar dependências ficam suspensas no servidor, sem chamadas ao modelo. Só os agentes em execução consomem tokens.','Tasks waiting on dependencies are suspended on the server without model calls. Only running agents consume tokens.')+'</p></section>';
  }
  function updateWorkflowPanel(host, wf) {
    if (!host) return;
    var html=wf ? renderWorkflow(wf) : '';
    if (host.__workflowHTML===html) return;
    var expanded={}, positions={};
    host.querySelectorAll('[data-wf-node]').forEach(function(node){
      var key=node.getAttribute('data-wf-node'), details=node.querySelector('details'), pre=node.querySelector('pre');
      if(details && details.open)expanded[key]=true;
      if(pre)positions[key]=pre.scrollTop;
    });
    host.innerHTML=html;host.__workflowHTML=html;
    host.querySelectorAll('[data-wf-node]').forEach(function(node){
      var key=node.getAttribute('data-wf-node'), details=node.querySelector('details'), pre=node.querySelector('pre');
      if(details && expanded[key])details.open=true;
      if(pre && positions[key])pre.scrollTop=positions[key];
    });
  }
  var wfSubchatTimer=null, wfSubchatSelection=null, wfSubchatGeneration=0;
  function wfOpenSubchat(chatId, turnId, nodeId) {
    var dialog=document.getElementById('wfSubchatDialog');
    if(!dialog){
      dialog=document.createElement('dialog');dialog.id='wfSubchatDialog';dialog.className='wf-model-dialog wf-subchat';
      dialog.setAttribute('aria-labelledby','wfSubchatTitle');document.body.appendChild(dialog);
      dialog.innerHTML='<div class="wf-dialog-head"><h2 id="wfSubchatTitle"></h2><button type="button" id="wfSubchatClose" aria-label="'+ui('Fechar','Close')+'">×</button></div><p class="wf-subchat-meta" id="wfSubchatMeta"></p><div class="wf-subchat-log" id="wfSubchatLog"></div><form id="wfSubchatForm"><label for="wfSubchatInput">'+ui('Orientação para este agente','Guidance for this agent')+'</label><textarea id="wfSubchatInput" maxlength="6000"></textarea><p class="wf-subchat-meta">'+ui('Mantém-se o objetivo e a coordenação da missão. Se o agente estiver a executar, recebe a orientação no fim dessa execução, antes das tarefas dependentes.','The mission objective and coordination remain in place. A running agent receives guidance at the end of its current execution, before dependent tasks start.')+'</p><button class="wf-open" id="wfSubchatSend" type="submit">'+ui('Enviar orientação','Send guidance')+'</button><p role="status" class="wf-subchat-meta" id="wfSubchatStatus"></p></form>';
      document.getElementById('wfSubchatClose').onclick=function(){dialog.close();};
      dialog.addEventListener('close',function(){clearInterval(wfSubchatTimer);wfSubchatTimer=null;wfSubchatSelection=null;wfSubchatGeneration++;});
      document.getElementById('wfSubchatForm').onsubmit=function(event){
        event.preventDefault();var input=document.getElementById('wfSubchatInput'),text=input.value.trim();
        if(!text || !wfSubchatSelection)return;
        var selection=Object.assign({},wfSubchatSelection), generation=wfSubchatGeneration;
        var button=document.getElementById('wfSubchatSend');button.disabled=true;
        var payload=Object.assign({},selection,{text:text,request_id:'guide-'+Date.now()+'-'+Math.random().toString(36).slice(2)});
        api('/api/chat/subchat/guide',payload).then(function(data){
          if(generation!==wfSubchatGeneration)return;
          if(data.error)throw new Error(data.error);
          input.value='';wfRenderSubchat(data);document.getElementById('wfSubchatStatus').textContent=ui('Orientação guardada para o agente.','Guidance saved for the agent.');
        }).catch(function(error){if(generation===wfSubchatGeneration)document.getElementById('wfSubchatStatus').textContent=error.message;})
        .finally(function(){if(generation===wfSubchatGeneration)button.disabled=false;});
      };
    }
    clearInterval(wfSubchatTimer);wfSubchatGeneration++;
    wfSubchatSelection={id:chatId,turn_id:turnId,node_id:nodeId};
    // Status must remain visible even when guidance is unavailable or loading fails.
    dialog.insertBefore(document.getElementById('wfSubchatStatus'),document.getElementById('wfSubchatForm'));
    document.getElementById('wfSubchatInput').value='';document.getElementById('wfSubchatStatus').textContent='';
    document.getElementById('wfSubchatMeta').textContent='';
    document.getElementById('wfSubchatSend').disabled=false;
    document.getElementById('wfSubchatTitle').textContent=ui('A carregar subtarefa…','Loading task…');
    document.getElementById('wfSubchatLog').replaceChildren();
    document.getElementById('wfSubchatLog').__html=null;document.getElementById('wfSubchatLog').__n=null;
    document.getElementById('wfSubchatForm').hidden=true;
    if(!dialog.open)dialog.showModal();
    var pending=false,loaded=false;
    function poll(){
      var generation=wfSubchatGeneration, s=wfSubchatSelection;if(!s || pending)return;
      pending=true;
      api('/api/chat/subchat?id='+encodeURIComponent(s.id)+'&turn_id='+encodeURIComponent(s.turn_id)+'&node_id='+encodeURIComponent(s.node_id))
      .then(function(data){if(generation!==wfSubchatGeneration)return;if(data.error)throw new Error(data.error);wfRenderSubchat(data);loaded=true;document.getElementById('wfSubchatStatus').textContent='';})
      .catch(function(error){if(generation!==wfSubchatGeneration)return;
        if(!loaded)document.getElementById('wfSubchatTitle').textContent=ui('Não foi possível carregar a subtarefa','Could not load task');
        document.getElementById('wfSubchatStatus').textContent=ui('Ligação falhou. A tentar novamente… ','Connection failed. Retrying… ')+error.message;
      }).finally(function(){pending=false;});
    }
    poll();wfSubchatTimer=setInterval(poll,1800);
  }
  function wfThinking(l){
    var steps=(l.steps||[]).slice(-6),cur=l.current_step||'',h='<div class="wf-think"><div class="wf-think-now"><span class="wf-spin"></span><span>'+wfText(cur||ui('A trabalhar…','Working…'))+'</span></div>';
    if(steps.length>1||(steps.length&&steps[steps.length-1]!==cur))h+='<ol class="wf-steps">'+steps.map(function(t,i){var last=i===steps.length-1;return '<li class="'+(last?'now':'done')+'"><i>'+(last?'':'✓')+'</i>'+wfText(t)+'</li>';}).join('')+'</ol>';
    if(l.partial_text)h+='<details class="wf-partial" open><summary>'+ui('Rascunho em curso','Draft in progress')+'</summary><div class="wf-rich">'+renderRichMarkdown(l.partial_text)+'</div></details>';
    return h+'</div>';
  }
  function wfStats(n,live){
    var c=[],g=n.guidance||[],q=g.filter(function(m){return m.status==='queued';}).length;
    if(n.started_at){var e=Math.max(0,Math.round((n.finished_at||Date.now()/1000)-n.started_at)),t=e>=60?Math.floor(e/60)+'m '+(e%60)+'s':e+'s';c.push('⏱ '+t);}
    if((live.steps||[]).length)c.push('⚙ '+live.steps.length+' '+ui('passos','steps'));
    if(g.length)c.push('✎ '+g.length+' '+ui('orientações','guidance')+(q?' ('+q+' '+ui('em fila','queued')+')':''));
    return c.map(function(x){return ' <span class="wf-chip">'+wfText(x)+'</span>';}).join('');
  }
  function wfRenderSubchat(data){
    var n=data.node || {},live=n.live || {},log=document.getElementById('wfSubchatLog');
    document.getElementById('wfSubchatTitle').textContent=n.title || n.id;
    document.getElementById('wfSubchatMeta').innerHTML=wfText(ui('Coordenação: ','Coordinator: ')+(data.coordinator || 'Maestro')+' · '+(live.agent_label || n.actual_agent || n.engine))+' <span class="wf-pill" data-s="'+wfText(n.status)+'"><i></i>'+wfText(n.status)+'</span>'+wfStats(n,live);
    var entries=[{kind:'task',text:n.instruction,ts:0}];
    (n.guidance || []).forEach(function(m){entries.push({kind:'user',text:m.text,ts:m.ts,note:m.status==='queued'?ui('Em fila para o agente','Queued for agent'):ui('Entregue ao agente','Delivered to agent')});});
    (n.exchanges || []).forEach(function(m){entries.push({kind:'agent',text:m.text,ts:m.ts,note:m.agent_used});});
    if(!n.exchanges && n.result && n.result.text)entries.push({kind:'agent',text:n.result.text,ts:n.finished_at || 1});
    entries.sort(function(a,b){return a.ts-b.ts;});
    var running=n.status==='running'||n.status==='pending';
    if(running && (live.partial_text || live.current_step || (live.steps||[]).length))entries.push({kind:'live',live:live,note:ui('A pensar','Thinking')});
    if(n.error)entries.push({kind:'error',text:n.error,note:ui('Interrupção','Interruption')});
    var prev=log.__n;
    var html=entries.map(function(e,i){return '<article class="'+wfText(e.kind)+(prev!=null && i>=prev?' wf-new':'')+'"><small>'+wfText(e.note || (e.kind==='task'?ui('Missão atribuída','Assigned task'):ui('Agente','Agent')))+(e.kind==='live'?'<span class="wf-typing"><b></b><b></b><b></b></span>':'')+'</small>'+(e.kind==='live'?wfThinking(e.live):(e.kind==='task'?'<pre>'+wfText(e.text)+'</pre>':'<div class="wf-rich">'+renderRichMarkdown(e.text)+'</div>'))+'</article>';}).join('');
    if(log.__html!==html){var bottom=log.scrollHeight-log.scrollTop-log.clientHeight<50,top=log.scrollTop;log.innerHTML=html;log.__html=html;log.__n=entries.length;log.scrollTop=bottom?log.scrollHeight:top;}
    document.getElementById('wfSubchatForm').hidden=!data.can_guide;
  }
  document.addEventListener('click',function(event){var button=event.target.closest('[data-wf-open]');if(button && state.currentChat)wfOpenSubchat(state.currentChat.id,button.getAttribute('data-wf-turn'),button.getAttribute('data-wf-open'));});
  initWorkflowControls();
"""
