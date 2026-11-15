// Functional identities stay stable when the underlying provider changes.
window.maestroRoleTag = function(role){
  var names = {maestro:'Maestro',planner:'Architect',analysis:'Analyst',sentinel:'Sentinel',scout:'Scout',implementation:'Maker',review:'Auditor',synthesis:'Scribe'};
  return names[role] ? '<span class="role-tag role-' + role + '">' + names[role] + '</span>' : '';
};
// Share detection between inline replies and the unanswered-question navigator.
window.maestroReplyPrompts = function(root) {
  var out = [], openSection = false;
  root.querySelectorAll('.rich-h1, .rich-h2, .rich-h3, .rich-h4, .rich-h5, .rich-h6, h1, h2, h3, h4, h5, h6, .rich-p, .rich-list-item, p').forEach(function(el) {
    if (el.closest('.code-card, .orch-details, .qa-box, pre, table, blockquote')) return;
    var text = el.textContent.replace(/^\s*[▸•\-\d.]+\s+/, '').trim();
    if (/^(?:rich-h[1-6])$/.test(el.className) || /^H[1-6]$/.test(el.tagName)) {
      openSection = /quest(?:ão|ões)\s+(?:em\s+abert[oa]s?|pendentes?)|open\s+questions?|decis(?:ão|ões)\s+pendentes?/i.test(text);
      return;
    }
    if (text.length < 8 || text.length > 1600) return;
    var decision = /\b(?:tens? de|precisas? de|deves?|necessito que|you (?:need to|must|have to))\s+(?:decidir|escolher|confirmar|indicar|choose|decide|confirm|specify)\b|\b(?:preciso|precisamos) (?:de )?(?:saber|confirmar|que (?:decidas|escolhas|confirmes))\b/i.test(text);
    if (!/\?/.test(text) && !decision && !(openSection && /\b(?:ou|or)\b/i.test(text))) return;
    // A rich list item may contain paragraphs; one control per source block.
    if (out.some(function(q) { return q.el.contains(el); })) return;
    out.push({el:el, text:text});
  });
  return out;
};
(function(){
  var state = {
    mod: "chat",
    folders: [],
    chats: [],
    archivedChats: [],
    archiveView: false,
    archiveEngine: "all",
    currentChat: null,
    filterProj: "all",
    search: "",
    pendingImages: [],
    pendingDocs: [],
    status: null,
    pulse: null,
    projectInfo: null,
    inspTab: "quotas",
    autoEnhance: false,
    notifEnabled: (typeof Notification !== "undefined" && Notification.permission === "granted"),
    lastPulseErrCount: null,
    deferredPrompt: null,
    recognition: null,
    recording: false,
    canvasRawCode: "",
    canvasPreviewMode: false,
    activeJob: null,
    jobPollTimer: null,
    busyMode: localStorage.getItem("maestro_busy_mode") || "queue",
    messageQueue: [],
    foldersOpen: localStorage.getItem("maestro_folders_open") === "1",
    reloading: false,
    updateTimer: null,
    /* Quotas: guardar a última leitura boa e a hora. Se a API falhar, o painel
       NÃO pode mostrar 0% usado (parecia "tudo cheio") nem inventar valores. */
    statusAt: 0,
    statusError: false,
    statusLoading: false,
    pulseError: false,
    quotaRefreshing: false,
    /* Tabela do Maestro: pedida à parte do /api/status para não atrasar as
       quotas com a descoberta de catálogo. */
    orchestration: null,
    orchestrationAt: 0,
    orchestrationLoading: false,
    orchestrationError: false,
    orchestrationPromise: null
  };

  function isStandaloneApp() {
    try {
      return (window.matchMedia && window.matchMedia("(display-mode: standalone)").matches) ||
             window.navigator.standalone === true;
    } catch (e) { return false; }
  }

  function syncInstallButton() {
    var b = document.getElementById("btnInstallPwa");
    if (!b) return;
    var installed = isStandaloneApp();
    b.classList.toggle("installed", installed);
    b.disabled = false;
    if (installed) {
      b.innerHTML = "✅ " + (state.lang === "en" ? "App already installed" : "App já instalada");
    } else if (!window.isSecureContext) {
      b.textContent = state.lang === "en" ? "Open HTTPS to install" : "Abrir HTTPS para instalar";
    } else {
      b.innerHTML = "📲 " + (state.lang === "en" ? "Install app" : "Instalar app");
    }
    if (SVG_ICONS) decorateSettingsIcons();
  }

  function installPromptFailed(error) {
    syncInstallButton();
    console.warn("Maestro PWA install prompt failed", error);
    alert(state.lang === "en"
      ? "Chrome could not open the installation prompt. Reload this HTTPS page and try again. If it still fails, check Chrome's installation error."
      : "O Chrome não conseguiu abrir a instalação. Recarrega esta página HTTPS e tenta novamente. Se voltar a falhar, verifica a mensagem de erro do Chrome.");
  }

  window.addEventListener("beforeinstallprompt", function(e){
    e.preventDefault();
    state.deferredPrompt = e;
    syncInstallButton();
  });

  window.addEventListener("appinstalled", function(){
    state.deferredPrompt = null;
    syncInstallButton();
    notifyUser("📲 Maestro AI", state.lang === "en" ? "App installed successfully." : "App instalada com sucesso.");
  });

  if (window.matchMedia) {
    try {
      window.matchMedia("(display-mode: standalone)").addEventListener("change", syncInstallButton);
    } catch (e) {}
  }

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js", { scope: "/", updateViaCache: "none" }).catch(function(){});
  }

  function api(path, body) {
    var opts = body === undefined ? { cache: "no-store" } : {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Maestro-Action": "1" },
      body: JSON.stringify(body)
    };
    /* Um pedido pendurado deixava o painel em "a ler…" para sempre. */
    var ctrl = (typeof AbortController !== "undefined") ? new AbortController() : null;
    var timer = null;
    if (ctrl) {
      opts.signal = ctrl.signal;
      timer = setTimeout(function(){ try { ctrl.abort(); } catch (e) {} }, path.indexOf("/api/media/generate-") === 0 ? 120000 : 20000);
    }
    return fetch(path, opts).then(function(r){
      if (!r.ok) throw new Error("HTTP " + r.status + " em " + path);
      return r.json();
    }).then(function(data){
      if (timer) clearTimeout(timer);
      return data;
    }, function(err){
      if (timer) clearTimeout(timer);
      throw err;
    });
  }

    function visualTheme() {
    var cs = getComputedStyle(document.documentElement), v = function(n, d){ return (cs.getPropertyValue(n) || d).trim() || d; };
    return '<style>:root{--bg:' + v('--bg', '#0b1020') + ';--card:' + v('--card', '#121c32') + ';--fg:' + v('--text', '#e2e8f0') + ';--muted:' + v('--muted', '#94a3b8') + ';--accent:' + v('--accent', '#38bdf8') + ';color-scheme:dark}body{margin:0;padding:8px;background:var(--bg);color:var(--fg);font-family:system-ui,sans-serif}button{font:inherit;cursor:pointer}</style>';
  }
function esc(s) {
    return String(s || "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
  }

  /* ---- MODULE NAVIGATION (CHAT / STUDIO / MEDIA / CASA / ARTIFACTS) ---- */
  function setModule(m) {
    state.mod = m;
    try { var _p = loadPrefs(); _p.mod = m; savePrefs(_p); } catch (e) {}
    document.querySelectorAll("[data-mod]").forEach(function(b){
      b.classList.toggle("active", b.getAttribute("data-mod") === m);
    });
    document.getElementById("paneChat").hidden = (m !== "chat");
    document.getElementById("paneStudio").hidden = (m !== "studio");
    document.getElementById("paneMedia").hidden = (m !== "media");
    document.getElementById("paneCasa").hidden = (m !== "casa");
    document.getElementById("paneArtifacts").hidden = (m !== "artifacts");
    closeMobileDrawers();

    if (m === "studio") loadGallery();
    if (m === "media") { loadMediaQueue(); loadContainers(); }
    if (m === "casa") { loadHaDashboard(); loadSchedules(); }
    if (m === "artifacts") loadArtifacts();
  }

  document.querySelectorAll("[data-mod]").forEach(function(b){
    b.onclick = function(){ setModule(b.getAttribute("data-mod")); };
  });

  /* ---- NOTIFICATIONS + SOUND + VIBRATION ---- */
  function playChime() {
    try {
      var ctx = new (window.AudioContext || window.webkitAudioContext)();
      var o = ctx.createOscillator(), g = ctx.createGain();
      o.type = "sine";
      o.frequency.setValueAtTime(587.33, ctx.currentTime);
      o.frequency.setValueAtTime(880, ctx.currentTime + 0.11);
      g.gain.setValueAtTime(0.07, ctx.currentTime);
      g.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.34);
      o.connect(g); g.connect(ctx.destination);
      o.start(); o.stop(ctx.currentTime + 0.34);
    } catch (e) {}
    try { if (navigator.vibrate) navigator.vibrate([90, 45, 110]); } catch (e) {}
  }

  function syncNotifButton() {
    var b = document.getElementById("btnNotif");
    var ok = (typeof Notification !== "undefined" && Notification.permission === "granted" && state.notifEnabled);
    b.classList.toggle("active", ok);
    b.textContent = ok ? "🔔 ON" : "🔕";
  }

  function notifyUser(title, bodyText) {
    playChime();
    if (!state.notifEnabled || typeof Notification === "undefined" || Notification.permission !== "granted") return;
    var cleanBody = String(bodyText || "").replace(/[#*`]/g, "").slice(0, 160);
    if (navigator.serviceWorker && navigator.serviceWorker.ready) {
      navigator.serviceWorker.ready.then(function(reg){
        reg.showNotification(title, {
          body: cleanBody,
          icon: "/icon.svg",
          badge: "/icon.svg",
          vibrate: [110, 50, 110],
          tag: "maestro-" + Date.now()
        });
      }).catch(function(){
        try { new Notification(title, { body: cleanBody, icon: "/icon.svg" }); } catch(e){}
      });
    }
  }

  document.getElementById("btnNotif").onclick = function(){
    if (typeof Notification === "undefined") return;
    if (Notification.permission === "granted") {
      state.notifEnabled = !state.notifEnabled;
      syncNotifButton();
      if (state.notifEnabled) notifyUser("👑 Maestro AI Studio", "Notificações ativas!");
      return;
    }
    Notification.requestPermission().then(function(p){
      state.notifEnabled = (p === "granted");
      syncNotifButton();
      if (state.notifEnabled) notifyUser("👑 Maestro AI Studio", "Notificações ativadas!");
    });
  };
  syncNotifButton();

  /* ---- DRAWERS & MOBILE SWIPE GESTURES ----
     Sem botão gigante: a gaveta segue o dedo. Puxar da margem esquerda /
     direita abre; arrastar a gaveta para a sua margem fecha. Tocar fora
     (backdrop) e Escape continuam a fechar, e o foco volta ao botão que abriu. */
  var leftEl = document.getElementById("sidebarLeft");
  var rightEl = document.getElementById("inspectorRight");
  var backdropEl = document.getElementById("mobileBackdrop");
  var leftGrip = document.getElementById("edgeGripLeft");
  var rightGrip = document.getElementById("edgeGripRight");

  var drawerTrigger = null;
  var drawerDrag = null;
  var suppressClickUntil = 0;
  var DRAG_AXIS_PX = 6;
  var EDGE_OPEN_PX = 26;
  var CLOSE_RATIO = 0.32;
  var OPEN_RATIO = 0.24;
  var FLING_VELOCITY = 0.38;
  var FLING_MIN_PX = 28;

  function isMobileLayout() { return window.innerWidth <= 1080; }
  function drawerIsOpen(el) { return el.classList.contains("mobile-open"); }
  function drawerWidth(el) { return el.getBoundingClientRect().width || (el === leftEl ? 310 : 365); }
  function gripFor(el) { return el === leftEl ? leftGrip : rightGrip; }

  function clearDrawerInline(el) {
    el.classList.remove("dragging");
    el.style.transition = "";
    el.style.transform = "";
  }

  function syncMobileDrawers() {
    var mobile = isMobileLayout();
    [leftEl, rightEl].forEach(function(el, i){
      var open = mobile && drawerIsOpen(el);
      el.inert = mobile && !open;
      if (mobile) el.setAttribute("aria-hidden", String(!open));
      else el.removeAttribute("aria-hidden");
      var btn = document.getElementById(i ? "btnToggleRight" : "btnToggleLeft");
      btn.setAttribute("aria-controls", el.id);
      btn.setAttribute("aria-expanded", String(mobile ? open : !el.classList.contains("collapsed")));
    });
    var anyOpen = mobile && (drawerIsOpen(leftEl) || drawerIsOpen(rightEl));
    backdropEl.classList.toggle("show", anyOpen);
    if (!anyOpen && !drawerDrag) {
      backdropEl.style.opacity = "";
      backdropEl.style.pointerEvents = "";
    }
    [leftGrip, rightGrip].forEach(function(grip, i){
      if (!grip) return;
      var open = mobile && drawerIsOpen(i ? rightEl : leftEl);
      grip.classList.toggle("grip-hidden", !mobile || open);
      grip.setAttribute("aria-expanded", String(open));
    });
  }

  function focusDrawer(el) {
    if (!el.hasAttribute("tabindex")) el.setAttribute("tabindex", "-1");
    el.focus({preventScroll:true});
  }

  function closeMobileDrawers(immediate, skipEl) {
    var hadOpen = drawerIsOpen(leftEl) || drawerIsOpen(rightEl);
    [leftEl, rightEl].forEach(function(el){
      if (el === skipEl) return;
      el.classList.remove("mobile-open");
      clearDrawerInline(el);
    });
    backdropEl.style.opacity = "";
    backdropEl.style.pointerEvents = "";
    // Tirar o foco de dentro da gaveta antes de a marcar aria-hidden/inert.
    if (!immediate && hadOpen && drawerTrigger) drawerTrigger.focus({preventScroll:true});
    syncMobileDrawers();
    if (!immediate) drawerTrigger = null;
  }

  function openMobileDrawer(el, fromDrag) {
    closeMobileDrawers(true, el);
    drawerTrigger = document.getElementById(el === leftEl ? "btnToggleLeft" : "btnToggleRight");
    el.classList.add("mobile-open");
    syncMobileDrawers();
    backdropEl.style.opacity = "";
    // Se veio de um arrasto, parte da posição actual do dedo e deixa a
    // transição CSS levar a gaveta até 0. O reflow força o ponto de partida.
    void el.offsetWidth;
    clearDrawerInline(el);
    if (!fromDrag) focusDrawer(el);
    if (el === rightEl) renderInspector();
  }

  function settleDrawer(d, open) {
    var el = d.el;
    if (open) {
      if (!drawerIsOpen(el)) {
        openMobileDrawer(el, true);
      } else {
        void el.offsetWidth;
        clearDrawerInline(el);
        backdropEl.style.opacity = "";
        syncMobileDrawers();
      }
    } else if (drawerIsOpen(el)) {
      closeMobileDrawers();
    } else {
      void el.offsetWidth;
      clearDrawerInline(el);
      backdropEl.style.opacity = "";
      syncMobileDrawers();
    }
  }

  function stopDrawerListeners() {
    window.removeEventListener("pointermove", onDrawerPointerMove);
    window.removeEventListener("pointerup", onDrawerPointerEnd);
    window.removeEventListener("pointercancel", onDrawerPointerCancel);
  }

  function abortDrawerDrag() {
    stopDrawerListeners();
    var d = drawerDrag;
    drawerDrag = null;
    backdropEl.style.pointerEvents = "";
    if (!d) return;
    d.el.classList.remove("dragging");
    d.el.style.transition = "";
    d.el.style.transform = "";
    var g = gripFor(d.el);
    if (g) g.classList.remove("dragging");
    backdropEl.style.opacity = "";
    syncMobileDrawers();
  }

  /* Pointer Events em vez de touch: com touch-action definido no CSS o
     browser deixa de cancelar o gesto horizontal a meio (era isso que
     fazia a gaveta saltar e voltar para trás). */
  function onDrawerPointerDown(e) {
    if (!isMobileLayout()) return;
    // Arrasto preso (pointerup perdido) bloqueava todos os gestos seguintes.
    if (drawerDrag) { if (Date.now() - drawerDrag.t0 < 4000) return; abortDrawerDrag(); }
    if (e.pointerType === "mouse" || e.isPrimary === false) return;
    if (e.button !== undefined && e.button > 0) return;
    if (document.querySelector('.modal-backdrop:not([hidden])')) return;
    if (document.querySelector('.artifact-canvas:not([hidden])')) return;
    // Não roubar o gesto ao compositor, à barra inferior, a campos de texto
    // nem a overlays que estão por cima das gavetas.
    if (e.target.closest && e.target.closest('input, textarea, select, [contenteditable="true"], .composer-dock, .mobile-bottom-nav, .maestro-home, .modal-card, .composer-menu, .composer-popover')) return;
    var openEl = drawerIsOpen(leftEl) ? leftEl : (drawerIsOpen(rightEl) ? rightEl : null);
    var el = null, fromOpen = false;
    if (openEl) {
      if (!openEl.contains(e.target)) return; // toque no backdrop: o click fecha
      el = openEl; fromOpen = true;
    } else if (leftGrip && leftGrip.contains(e.target)) {
      el = leftEl;
    } else if (rightGrip && rightGrip.contains(e.target)) {
      el = rightEl;
    } else if (e.clientX <= EDGE_OPEN_PX) {
      el = leftEl;
    } else if (e.clientX >= window.innerWidth - EDGE_OPEN_PX) {
      el = rightEl;
    } else {
      return;
    }
    var width = drawerWidth(el);
    drawerDrag = {
      el: el, fromOpen: fromOpen, width: width, pointerId: e.pointerId,
      startX: e.clientX, startY: e.clientY,
      offset: fromOpen ? 0 : (el === leftEl ? -width : width),
      axis: null, moved: false, t0: Date.now(),
      lastX: e.clientX, lastTime: Date.now(), velocity: 0
    };
    // Captura o dedo: o arrasto continua mesmo que saia do puxador.
    try { if (e.target.setPointerCapture) e.target.setPointerCapture(e.pointerId); } catch(_){}
    window.addEventListener("pointermove", onDrawerPointerMove, { passive: false });
    window.addEventListener("pointerup", onDrawerPointerEnd);
    window.addEventListener("pointercancel", onDrawerPointerCancel);
  }

  function onDrawerPointerMove(e) {
    if (!drawerDrag || e.pointerId !== drawerDrag.pointerId) return;
    var d = drawerDrag;
    var dx = e.clientX - d.startX;
    var dy = e.clientY - d.startY;
    if (!d.axis) {
      if (Math.abs(dx) < DRAG_AXIS_PX && Math.abs(dy) < DRAG_AXIS_PX) return;
      if (Math.abs(dy) > Math.abs(dx) * 1.15) { abortDrawerDrag(); return; }
      d.axis = "x";
      d.moved = true;
      d.el.classList.add("dragging");
      var g = gripFor(d.el);
      if (g && !d.fromOpen) g.classList.add("dragging");
      backdropEl.classList.add("show");
      backdropEl.style.pointerEvents = "none";
    }
    if (d.axis !== "x") return;
    if (e.cancelable) e.preventDefault();
    var now = Date.now();
    var dt = now - d.lastTime;
    if (dt >= 12) {
      d.velocity = (e.clientX - d.lastX) / dt;
      d.lastX = e.clientX;
      d.lastTime = now;
    }
    var min = d.el === leftEl ? -d.width : 0;
    var max = d.el === leftEl ? 0 : d.width;
    var off = Math.max(min, Math.min(max, d.offset + dx));
    d.offset = off;
    d.el.style.transform = "translate3d(" + off + "px,0,0)";
    var progress = 1 - Math.abs(off) / d.width;
    backdropEl.style.opacity = String(Math.max(0, Math.min(1, progress)));
  }

  function onDrawerPointerEnd(e) {
    if (!drawerDrag || e.pointerId !== drawerDrag.pointerId) return;
    stopDrawerListeners();
    var d = drawerDrag;
    drawerDrag = null;
    backdropEl.style.pointerEvents = "";
    var g = gripFor(d.el);
    if (g) g.classList.remove("dragging");
    if (d.axis !== "x") {
      // Foi um toque ou um scroll vertical: a gaveta fica como estava.
      d.el.classList.remove("dragging");
      d.el.style.transition = "";
      d.el.style.transform = "";
      backdropEl.style.opacity = "";
      syncMobileDrawers();
      return;
    }
    var dx = (typeof e.clientX === "number") ? e.clientX - d.startX : d.offset;
    var v = d.velocity || 0;
    var open;
    if (d.fromOpen) {
      var towardEdge = d.el === leftEl ? -1 : 1;
      var fling = Math.abs(dx) > Math.max(FLING_MIN_PX, d.width * 0.12);
      open = !(Math.abs(d.offset) > d.width * CLOSE_RATIO || (v * towardEdge > FLING_VELOCITY && fling));
    } else {
      var inward = d.el === leftEl ? 1 : -1;
      var flingIn = Math.abs(dx) > Math.max(FLING_MIN_PX, d.width * 0.12);
      open = Math.abs(dx) > d.width * OPEN_RATIO || (v * inward > FLING_VELOCITY && flingIn);
    }
    if (d.moved) suppressClickUntil = Date.now() + 350;
    settleDrawer(d, open);
  }

  function onDrawerPointerCancel(e) {
    if (!drawerDrag || e.pointerId !== drawerDrag.pointerId) return;
    stopDrawerListeners();
    var d = drawerDrag;
    drawerDrag = null;
    backdropEl.style.pointerEvents = "";
    var g = gripFor(d.el);
    if (g) g.classList.remove("dragging");
    if (!d) return;
    if (d.moved) suppressClickUntil = Date.now() + 350;
    settleDrawer(d, d.fromOpen);
  }

  /* Um arrasto não deve transformar-se num click no que estava por baixo. */
  document.addEventListener("click", function(e){
    if (Date.now() < suppressClickUntil) {
      suppressClickUntil = 0;
      e.preventDefault();
      e.stopPropagation();
    }
  }, true);

  backdropEl.onclick = function(){ closeMobileDrawers(); };
  [leftEl, rightEl].forEach(function(el, i){
    document.getElementById(i ? "btnToggleRight" : "btnToggleLeft").onclick = function(){
      if (window.innerWidth <= 1080) {
        if (drawerIsOpen(el)) closeMobileDrawers();
        else openMobileDrawer(el);
      } else {
        el.classList.toggle("collapsed");
        syncMobileDrawers();
      }
    };
  });
  window.addEventListener("resize", function(){
    if (window.innerWidth > 1080) {
      drawerDrag = null;
      closeMobileDrawers(true);
    }
    syncMobileDrawers();
  });
  document.addEventListener("keydown", function(e){
    var drawer = window.innerWidth <= 1080 && document.querySelector(".mobile-open");
    if (!drawer || document.querySelector('.modal-backdrop:not([hidden]), .rename-dialog[open], .maestro-context-menu')) return;
    if (e.key === "Escape") { e.preventDefault(); closeMobileDrawers(); }
    if (e.key === "Tab") {
      var items = Array.from(drawer.querySelectorAll('button, a[href], input, select, [tabindex="0"]')).filter(function(el){ return !el.disabled && el.getClientRects().length; });
      var first = items[0], last = items[items.length - 1];
      if (e.shiftKey && (document.activeElement === first || document.activeElement === drawer || !drawer.contains(document.activeElement))) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && (document.activeElement === last || !drawer.contains(document.activeElement))) { e.preventDefault(); first.focus(); }
    }
  });
  document.addEventListener("pointerdown", onDrawerPointerDown, { passive: true });
  syncMobileDrawers();

  /* ---- COLORED FOLDERS ---- */
  function loadFolders() {
    return api("/api/folders").then(function(d){
      state.folders = d.folders || [];
      renderFolderStripAndSelect();
    });
  }

  function renderFolderStripAndSelect() {
    var strip = document.getElementById("folderStrip");
    var sel = document.getElementById("selProject");
    var curVal = sel.value || "nas";

    var fmap = {};
    state.folders.forEach(function(f){ fmap[f.id] = f; });
    var activeF = fmap[state.filterProj];
    var activeName = state.filterProj === "all" ? "Todas as pastas" : (activeF ? activeF.name : "Todas as pastas");
    var activeIcon = state.filterProj === "all" ? "🗂️" : (activeF ? activeF.icon : "🗂️");
    var activeColor = activeF ? activeF.color : "#38bdf8";
    var activeCount = state.chats.filter(function(c){
      return state.filterProj === "all" || c.project === state.filterProj;
    }).length;

    function cnt(fid) {
      return state.chats.filter(function(c){ return fid === "all" || c.project === fid; }).length;
    }

    var html =
      '<button type="button" class="folder-acc-head' + (state.foldersOpen ? ' open' : '') + '" id="folderAccHead" data-tip="Pastas & Projetos|Abre/fecha a lista de pastas. Cada pasta guarda o cwd e o agente predefinido.">' +
        '<span class="folder-acc-cur"><span class="dot" style="width:9px;height:9px;border-radius:50%;background:' + esc(activeColor) + '"></span>' +
          '<span class="txt">' + projectIco(activeIcon) + ' ' + esc(activeName) + '</span></span>' +
        '<span class="folder-acc-meta"><span class="folder-acc-cnt">' + activeCount + '</span><span class="folder-acc-chev">▾</span></span>' +
      '</button>';

    if (state.foldersOpen) {
      var rows = '<button type="button" class="folder-acc-row ' + (state.filterProj === "all" ? "active" : "") + '" data-fid="all">' +
        '<span class="folder-acc-row-left"><span class="dot" style="background:#38bdf8"></span><span class="fname">' + dualIco('folder', '🗂️') + ' Todas as pastas</span></span>' +
        '<span class="folder-acc-cnt">' + cnt("all") + '</span></button>';
      state.folders.forEach(function(f){
        rows += '<button type="button" class="folder-acc-row ' + (state.filterProj === f.id ? "active" : "") + '" data-fid="' + esc(f.id) + '">' +
          '<span class="folder-acc-row-left"><span class="dot" style="background:' + esc(f.color) + '"></span><span class="fname">' + projectIco(f.icon) + ' ' + esc(f.name) + '</span></span>' +
          '<span class="folder-acc-cnt">' + cnt(f.id) + '</span></button>';
      });
      html += '<div class="folder-acc-list" id="folderAccList">' + rows + '</div>';
    }
    strip.innerHTML = html;

    var head = document.getElementById("folderAccHead");
    if (head) head.onclick = function(){
      state.foldersOpen = !state.foldersOpen;
      try { localStorage.setItem("maestro_folders_open", state.foldersOpen ? "1" : "0"); } catch(e){}
      renderFolderStripAndSelect();
    };
    strip.querySelectorAll("[data-fid]").forEach(function(btn){
      btn.onclick = function(ev){
        ev.stopPropagation();
        state.filterProj = btn.getAttribute("data-fid");
        state.foldersOpen = false;
        try { localStorage.setItem("maestro_folders_open", "0"); } catch(e){}
        if (state.filterProj !== "all") {
          sel.value = state.filterProj;
          loadProjectInspector(state.filterProj);
        }
        renderFolderStripAndSelect();
        renderChatList();
      };
    });

    sel.innerHTML = state.folders.map(function(f){
      return '<option value="' + esc(f.id) + '">' + esc(f.icon) + ' ' + esc(f.name) + '</option>';
    }).join("");
    if (state.folders.some(function(f){ return f.id === curVal; })) sel.value = curVal;
  }

  document.getElementById("btnNewFolder").onclick = function(){
    document.getElementById("folderModal").hidden = false;
  };
  document.getElementById("btnCloseFolderModal").onclick = function(){
    document.getElementById("folderModal").hidden = true;
  };
  document.getElementById("btnSaveFolder").onclick = function(){
    var name = document.getElementById("fName").value.trim();
    if (!name) return;
    api("/api/folders/save", {
      name: name,
      icon: document.getElementById("fIcon").value || "📁",
      color: document.getElementById("fColor").value || "#38bdf8",
      cwd: document.getElementById("fCwd").value || "/home/hubert",
      desc: document.getElementById("fDesc").value || ""
    }).then(function(d){
      state.folders = d.folders || [];
      document.getElementById("folderModal").hidden = true;
      renderFolderStripAndSelect();
    });
  };

  /* ---- SPLIT ARTIFACT / CODE / HTML PREVIEW CANVAS ---- */
  function openCanvas(title, content, lang) {
    var cv = document.getElementById("artifactCanvas");
    var body = document.getElementById("canvasBody");
    var ifr = document.getElementById("canvasIframe");
    var btnPrev = document.getElementById("btnPreviewCanvas");

    state.canvasRawCode = content || "";
    state.canvasPreviewMode = false;
    document.getElementById("canvasTitle").textContent = title || "Artifact";
    body.textContent = state.canvasRawCode;
    body.hidden = false;
    ifr.hidden = true;

    var canPreview = (lang === "html" || lang === "svg" || (content && (content.indexOf("<html") !== -1 || content.indexOf("<svg") !== -1)));
    btnPrev.hidden = !canPreview;
    btnPrev.textContent = "👁 Preview";
    cv.hidden = false;
  }

  document.getElementById("btnPreviewCanvas").onclick = function(){
    var body = document.getElementById("canvasBody");
    var ifr = document.getElementById("canvasIframe");
    var btn = document.getElementById("btnPreviewCanvas");
    state.canvasPreviewMode = !state.canvasPreviewMode;
    if (state.canvasPreviewMode) {
      body.hidden = true;
      ifr.hidden = false;
      ifr.srcdoc = state.canvasRawCode;
      btn.textContent = "💻 Código";
    } else {
      body.hidden = false;
      ifr.hidden = true;
      btn.textContent = "👁 Preview";
    }
  };
  document.getElementById("btnCloseCanvas").onclick = function(){
    document.getElementById("artifactCanvas").hidden = true;
  };
  document.getElementById("btnCopyCanvas").onclick = function(){
    navigator.clipboard && navigator.clipboard.writeText(state.canvasRawCode || "");
    var b = document.getElementById("btnCopyCanvas");
    b.classList.add("copied");
    setTimeout(function(){ b.classList.remove("copied"); }, 1400);
  };

  /* ---- MODULE 2: GALLERY & IMAGE / GIF GENERATOR (WITH PAGINATION, FILTERS & SLIDER) ---- */
  state.galleryAll = [];
  state.galFilter = "all";
  state.galSearch = "";
  state.galPage = 1;
  state.galPerPage = 24;

  function loadGallery() {
    var grid = document.getElementById("galleryGrid");
    grid.innerHTML = '<div class="stat-sub">A carregar galeria…</div>';
    api("/api/gallery").then(function(d){
      state.galleryAll = d.items || [];
      renderGalleryView();
    });
  }

  function renderGalleryView() {
    var grid = document.getElementById("galleryGrid");
    var pag = document.getElementById("galleryPagination");
    var q = (state.galSearch || "").toLowerCase().trim();

    var filtered = state.galleryAll.filter(function(it){
      if (state.galFilter === "ai" && it.kind !== "ai_image" && it.kind !== "gif") return false;
      if (state.galFilter === "paste" && it.kind !== "paste") return false;
      if (state.galFilter === "nas_asset" && it.kind !== "nas_asset") return false;
      if (q && (String(it.prompt || "") + " " + String(it.name || "")).toLowerCase().indexOf(q) === -1) return false;
      return true;
    });

    var total = filtered.length;
    var badge = document.getElementById("galTotalBadge");
    if (badge) badge.textContent = total + (total === 1 ? " imagem" : " imagens");

    if (!total) {
      grid.innerHTML = '<div class="feature-card">Nenhuma imagem encontrada para este filtro.</div>';
      pag.innerHTML = "";
      return;
    }

    var perPage = state.galPerPage || 24;
    var totalPages = Math.max(1, Math.ceil(total / perPage));
    if (state.galPage > totalPages) state.galPage = totalPages;
    if (state.galPage < 1) state.galPage = 1;

    var startIdx = (state.galPage - 1) * perPage;
    var pageItems = filtered.slice(startIdx, startIdx + perPage);

    grid.innerHTML = "";
    pageItems.forEach(function(it){
      var url = "/api/image?path=" + encodeURIComponent(it.path);
      var canDel = (it.kind === "ai_image" || it.kind === "gif" || it.kind === "paste");
      var badgeIcon = it.kind === "gif" ? "🎞 GIF · " : (it.kind === "nas_asset" ? "🖼️ NAS · " : (it.kind === "paste" ? "📋 Print · " : "🎨 AI · "));
      var card = document.createElement("div");
      card.className = "gallery-item anim-in";
      card.innerHTML =
        '<img src="' + url + '" alt="' + esc(it.prompt) + '" loading="lazy">' +
        '<div class="gallery-item-foot">' +
          '<span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="' + esc(it.name) + '">' + badgeIcon + esc(it.prompt || it.name) + '</span>' +
          '<div style="display:flex;gap:4px;flex-shrink:0">' +
            '<button type="button" class="round-tool-btn" data-use-img="' + esc(it.path) + '" title="Usar no Chat">💬</button>' +
            '<a class="round-tool-btn" href="' + url + '" download="' + esc(it.name) + '" title="Download">⬇</a>' +
            (canDel ? '<button type="button" class="round-tool-btn" data-del-img="' + esc(it.path) + '" title="Apagar">🗑</button>' : '') +
          '</div>' +
        '</div>';
      card.querySelector("img").onclick = function(){ window.open(url, "_blank"); };
      card.querySelector("[data-use-img]").onclick = function(){
        state.pendingImages.push(it.path);
        renderAttachStrip();
        setModule("chat");
      };
      var delBtn = card.querySelector("[data-del-img]");
      if (delBtn) {
        delBtn.onclick = function(){
          if (!confirm("Apagar imagem «" + it.name + "»?")) return;
          api("/api/gallery/delete", { path: it.path }).then(function(r){
            if (r.items) { state.galleryAll = r.items; renderGalleryView(); }
          });
        };
      }
      grid.appendChild(card);
    });

    // Render pagination bar
    var pHtml = '<button type="button" class="card-btn" id="galPrevBtn" ' + (state.galPage <= 1 ? 'disabled style="opacity:0.4"' : '') + '>« Anterior</button>';
    for (var p = 1; p <= totalPages; p++) {
      pHtml += '<button type="button" class="card-btn ' + (p === state.galPage ? 'primary' : '') + '" data-gpage="' + p + '">' + p + '</button>';
    }
    pHtml += '<button type="button" class="card-btn" id="galNextBtn" ' + (state.galPage >= totalPages ? 'disabled style="opacity:0.4"' : '') + '>Seguinte »</button>';
    pHtml += '<span style="font-size:0.74rem;color:var(--muted);margin-left:8px">Página <b>' + state.galPage + '</b> de <b>' + totalPages + '</b> (' + total + ' itens)</span>';
    pag.innerHTML = pHtml;

    var prevB = document.getElementById("galPrevBtn");
    var nextB = document.getElementById("galNextBtn");
    if (prevB) prevB.onclick = function(){ if (state.galPage > 1) { state.galPage--; renderGalleryView(); } };
    if (nextB) nextB.onclick = function(){ if (state.galPage < totalPages) { state.galPage++; renderGalleryView(); } };
    pag.querySelectorAll("[data-gpage]").forEach(function(b){
      b.onclick = function(){
        state.galPage = Number(b.getAttribute("data-gpage")) || 1;
        renderGalleryView();
      };
    });
  }

  document.querySelectorAll("#galFilterTabs [data-gkind]").forEach(function(btn){
    btn.onclick = function(){
      document.querySelectorAll("#galFilterTabs [data-gkind]").forEach(function(b){ b.classList.remove("primary"); });
      btn.classList.add("primary");
      state.galFilter = btn.getAttribute("data-gkind");
      state.galPage = 1;
      renderGalleryView();
    };
  });
  document.getElementById("galSearchInput").oninput = function(e){
    state.galSearch = e.target.value || "";
    state.galPage = 1;
    renderGalleryView();
  };
  document.getElementById("galPerPage").onchange = function(e){
    state.galPerPage = Number(e.target.value) || 24;
    state.galPage = 1;
    renderGalleryView();
  };
  document.getElementById("galZoomSlider").oninput = function(e){
    document.documentElement.style.setProperty("--gal-col", e.target.value + "px");
  };

  document.getElementById("btnGenImage").onclick = function(){
    var p = document.getElementById("genMediaPrompt").value.trim() || "Futuristic AI指挥 Maestro neon emblem";
    var asp = document.getElementById("genAspect").value;
    var btn = document.getElementById("btnGenImage");
    btn.disabled = true; btn.textContent = "⏳ A gerar HD…";
    api("/api/media/generate-image", { prompt: p, aspect: asp }).then(function(){
      loadGallery();
      notifyUser("🎨 Imagem AI Criada", p);
    }).finally(function(){
      btn.disabled = false; btn.textContent = "🖼 Gerar Imagem AI";
    });
  };

  document.getElementById("btnGenGif").onclick = function(){
    var p = document.getElementById("genMediaPrompt").value.trim();
    if (!p) { document.getElementById("genMediaPrompt").focus(); return; }
    var feedback = document.getElementById("genMediaFeedback");
    feedback.textContent = "A criar a cena e a renderizar o loop…";
    feedback.setAttribute("role", "status");
    var btn = document.getElementById("btnGenGif");
    btn.disabled = true; btn.textContent = "⏳ A animar…";
    api("/api/media/generate-gif", { prompt: p, aspect: document.getElementById("genAspect").value }).then(function(d){
      if (d.error) throw new Error(d.error);
      loadGallery();
      feedback.textContent = "GIF criado · " + d.width + "×" + d.height + " · loop de 3 segundos.";
      notifyUser("🎞 GIF Animado Criado", p);
    }).catch(function(e){
      feedback.setAttribute("role", "alert");
      feedback.textContent = "Não foi possível criar o GIF: " + e.message;
    }).finally(function(){
      btn.disabled = false; btn.textContent = "🎞 Gerar GIF Animado";
    });
  };

  /* ---- MODULE 3: SONARR / RADARR MEDIA & DOCKER CONTAINERS ---- */
  var mediaQueueLoading = false;
  function loadMediaQueue() {
    if (mediaQueueLoading) return;
    mediaQueueLoading = true;
    var box = document.getElementById("mediaQueueBox");
    api("/api/media/queue").then(function(d){
      var q = d.queue || [];
      var recent = d.recent || [];
      var errors = d.errors || [];
      var recentDetails = box.querySelector('details');
      var recentOpen = !recentDetails || recentDetails.open;
      box.innerHTML = '<div style="margin-bottom:10px">' + q.length + ' na fila · atualização automática a cada 15 s</div>' +
        errors.map(function(e){ return '<div style="color:var(--yellow);margin-bottom:8px">⚠️ ' + esc(e) + '</div>'; }).join('') +
        q.map(function(x){
          var pct = Math.max(0, Math.min(100, Number(x.progress) || 0));
          return '<div style="padding:10px 0;border-bottom:1px solid var(--line-strong)">' +
            '<div style="display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap"><strong style="color:var(--text);overflow-wrap:anywhere">' + esc(x.title) + '</strong>' +
            '<span>' + esc(x.source) + '</span></div>' +
            '<div style="margin:5px 0;color:' + (x.warning ? 'var(--yellow)' : 'var(--muted)') + '">' + esc(x.status_label) + ' · <b>' + pct + '%</b>' +
            (x.timeleft && x.timeleft !== '—' ? ' · falta ' + esc(x.timeleft) : '') + '</div>' +
            '<progress max="100" value="' + pct + '" aria-label="Progresso de ' + esc(x.title) + '" style="width:100%;height:8px;accent-color:#38bdf8">' + pct + '%</progress>' +
            (x.details ? '<div style="margin-top:4px;overflow-wrap:anywhere">⚠️ ' + esc(x.details) + '</div>' : '') + '</div>';
        }).join('') +
        (!q.length ? '<div style="margin:8px 0">' + (errors.length ? 'Não há downloads ativos confirmados.' : 'Sem downloads ativos neste momento.') + '</div>' : '') +
        (recent.length ? '<details ' + (recentOpen ? 'open ' : '') + 'style="margin-top:12px"><summary style="cursor:pointer">Pedidos recentes no Radarr / Sonarr</summary>' +
          recent.map(function(x){
            return '<div style="display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap;padding:7px 0;border-bottom:1px solid var(--line-strong)">' +
              '<span style="overflow-wrap:anywhere">' + esc(x.title) + (x.year ? ' (' + esc(x.year) + ')' : '') + '</span>' +
              '<span>' + (x.available ? '✅ ' : '⏳ ') + esc(x.status_label) + ' · ' + esc(x.source) + '</span></div>';
          }).join('') + '</details>' : '') +
        '<div style="font-size:0.72rem;margin-top:10px">Atualizado às ' + new Date((d.updated_at || Date.now()/1000) * 1000).toLocaleTimeString('pt-PT') + '</div>';
    }).catch(function(){
      box.textContent = '⚠️ Não foi possível atualizar o estado dos downloads. Volta a tentar em Atualizar.';
    }).finally(function(){
      mediaQueueLoading = false;
    });
  }
  document.getElementById("btnRefreshMediaQueue").onclick = loadMediaQueue;
  setInterval(function(){ if (state.mod === "media" && !document.hidden) loadMediaQueue(); }, 15000);

  function searchMediaNow() {
    var q = document.getElementById("mediaSearchInput").value.trim();
    var kind = document.getElementById("selMediaKind").value;
    if (!q) return;
    var grid = document.getElementById("mediaResultsGrid");
    grid.innerHTML = '<div class="stat-sub">A pesquisar em ' + (kind === "series" ? "Sonarr (:8989)" : "Radarr (:7878)") + '…</div>';
    api("/api/media/search?kind=" + encodeURIComponent(kind) + "&q=" + encodeURIComponent(q)).then(function(d){
      var res = d.results || [];
      if (!res.length) {
        grid.innerHTML = '<div class="stat-sub">Sem resultados encontrados.</div>';
        return;
      }
      grid.innerHTML = "";
      res.forEach(function(it){
        var idVal = it.kind === "series" ? it.tvdbId : it.tmdbId;
        var card = document.createElement("div");
        card.className = "poster-card anim-in";
        card.innerHTML =
          (it.poster ? '<img src="' + esc(it.poster) + '" alt="Poster">' : '<div style="width:78px;height:116px;border-radius:9px;background:#080d18;display:grid;place-items:center">🎬</div>') +
          '<div style="flex:1;min-width:0;display:flex;flex-direction:column;justify-content:space-between">' +
            '<div>' +
              '<strong style="font-size:0.88rem">' + esc(it.title) + ' (' + esc(it.year || "—") + ')</strong>' +
              '<div style="font-size:0.72rem;color:var(--muted);margin-top:4px;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden">' + esc(it.overview || "") + '</div>' +
            '</div>' +
            '<div style="margin-top:8px">' +
              (it.already_added
                ? '<span style="color:var(--green);font-size:0.75rem;font-weight:700">✓ Já adicionado</span>'
                : '<button type="button" class="send-circle-btn" style="height:30px;font-size:0.74rem">⬇ Sacar Agora</button>') +
            '</div>' +
          '</div>';
        var btn = card.querySelector("button");
        if (btn) {
          btn.onclick = function(){
            btn.disabled = true; btn.textContent = "A enviar…";
            api("/api/media/request", { kind: it.kind, id: idVal, title: it.title }).then(function(r){
              if (r.ok) {
                btn.textContent = "✓ Pedido adicionado";
                notifyUser("🍿 " + it.title, r.message || "Adicionado com sucesso!");
                loadMediaQueue();
              } else {
                btn.disabled = false; btn.textContent = "⬇ Sacar Agora";
                alert(r.error || "Erro ao adicionar.");
              }
            });
          };
        }
        grid.appendChild(card);
      });
    });
  }
  document.getElementById("btnSearchMedia").onclick = searchMediaNow;
  document.getElementById("mediaSearchInput").addEventListener("keydown", function(e){
    if (e.key === "Enter") { e.preventDefault(); searchMediaNow(); }
  });

  function loadContainers() {
    var grid = document.getElementById("containersGrid");
    api("/api/containers").then(function(d){
      var list = d.containers || [];
      grid.innerHTML = "";
      list.forEach(function(c){
        var isUp = c.state === "running";
        var card = document.createElement("div");
        card.className = "feature-card anim-in";
        card.innerHTML =
          '<div style="display:flex;justify-content:space-between;align-items:center">' +
            '<strong style="font-size:0.86rem">' + esc(c.icon) + ' ' + esc(c.name) + '</strong>' +
            '<span style="font-family:var(--mono);font-size:0.68rem;color:' + (isUp ? 'var(--green)' : 'var(--rose)') + '">' + esc(c.status) + '</span>' +
          '</div>' +
          '<div style="display:flex;gap:5px;flex-wrap:wrap">' +
            (c.prompts || []).map(function(p){
              return '<button type="button" class="round-tool-btn magic" data-cprompt="' + esc(p) + '">✨ ' + esc(p.slice(0, 34)) + '…</button>';
            }).join("") +
          '</div>' +
          '<div style="display:flex;gap:6px;margin-top:auto">' +
            '<button type="button" class="round-tool-btn" data-clogs="' + esc(c.name) + '">📜 Logs</button>' +
            '<button type="button" class="round-tool-btn" data-crestart="' + esc(c.name) + '">🔄 Restart</button>' +
            (c.port ? '<a class="round-tool-btn" href="http://' + window.location.hostname + ':' + c.port + '" target="_blank" style="text-decoration:none">↗ Abrir</a>' : '') +
          '</div>';

        card.querySelectorAll("[data-cprompt]").forEach(function(b){
          b.onclick = function(){
            setModule("chat");
            var ta = document.getElementById("promptInput");
            ta.value = b.getAttribute("data-cprompt");
            ta.focus();
          };
        });
        card.querySelector("[data-clogs]").onclick = function(){
          api("/api/container/action", { name: c.name, action: "logs" }).then(function(r){
            openCanvas("Logs · " + c.name, r.output || r.error || "");
          });
        };
        card.querySelector("[data-crestart]").onclick = function(){
          if (!confirm("Reiniciar container " + c.name + "?")) return;
          api("/api/container/action", { name: c.name, action: "restart" }).then(function(r){
            notifyUser("🐳 Docker", r.output || "Reiniciado");
            loadContainers();
          });
        };
        grid.appendChild(card);
      });
    });
  }

  /* ---- MODULE 4: HOME ASSISTANT (3D PRINTERS, PLUGS) & SCHEDULERS ---- */
  function loadHaDashboard() {
    api("/api/ha/dashboard").then(function(d){
      var pGrid = document.getElementById("haPrintersGrid");
      var sGrid = document.getElementById("haSwitchesGrid");
      pGrid.innerHTML = "";
      (d.printers || []).forEach(function(p){
        var pct = parseFloat(p.progress) || 0;
        var card = document.createElement("div");
        card.className = "feature-card anim-in";
        card.innerHTML =
          '<div class="stat-head"><span>' + esc(p.icon) + ' ' + esc(p.name) + '</span><span style="color:var(--cyan)">' + esc(p.status) + '</span></div>' +
          '<div class="meter-track"><div class="meter-fill ok" style="width:' + Math.min(100, pct) + '%"></div></div>' +
          '<div class="stat-sub">Progresso: <b>' + pct + '%</b> · Bico/Cama: <b>' + esc(p.nozzle) + '° / ' + esc(p.bed) + '°</b> · Tomada: <b>' + esc(p.plug_state) + '</b></div>' +
          '<div style="display:flex;gap:6px">' +
            '<button type="button" class="round-tool-btn" data-ha-ent="' + esc(p.plug_entity) + '">⏻ Tomada (' + esc(p.plug_state) + ')</button>' +
            (p.light_entity ? '<button type="button" class="round-tool-btn" data-ha-ent="' + esc(p.light_entity) + '">💡 Luz (' + esc(p.light_state) + ')</button>' : '') +
          '</div>';
        card.querySelectorAll("[data-ha-ent]").forEach(function(b){
          b.onclick = function(){
            api("/api/ha/action", { entity_id: b.getAttribute("data-ha-ent"), action: "toggle" }).then(loadHaDashboard);
          };
        });
        pGrid.appendChild(card);
      });

      sGrid.innerHTML = "";
      (d.switches || []).forEach(function(sw){
        var on = sw.state === "on";
        var card = document.createElement("div");
        card.className = "feature-card anim-in";
        card.style.borderColor = on ? "rgba(52,211,153,.45)" : "var(--line)";
        card.innerHTML =
          '<div style="display:flex;justify-content:space-between;align-items:center">' +
            '<strong>' + esc(sw.icon) + ' ' + esc(sw.name) + '</strong>' +
            '<button type="button" class="icon-pill-btn ' + (on ? 'active' : '') + '">' + (on ? 'ON' : 'OFF') + '</button>' +
          '</div>';
        card.querySelector("button").onclick = function(){
          api("/api/ha/action", { entity_id: sw.entity_id, action: "toggle" }).then(loadHaDashboard);
        };
        sGrid.appendChild(card);
      });
    });
  }
  document.getElementById("btnRefreshHa").onclick = loadHaDashboard;

  function loadSchedules() {
    var grid = document.getElementById("schedulesGrid");
    api("/api/schedules").then(function(d){
      var list = d.schedules || [];
      grid.innerHTML = "";
      list.forEach(function(s){
        var card = document.createElement("div");
        card.className = "stat-card anim-in";
        card.innerHTML =
          '<div class="stat-head">' +
            '<span>⏰ ' + esc(s.time_hhmm) + ' · ' + esc(s.name) + '</span>' +
            '<span style="color:' + (s.enabled ? 'var(--green)' : 'var(--muted)') + '">' + (s.enabled ? 'ATIVO' : 'PAUSA') + '</span>' +
          '</div>' +
          '<div class="stat-sub">' + esc(s.target) + '<br>Última: ' + esc(s.last_run || "Nunca") + ' (' + esc(s.last_result || "") + ')</div>' +
          '<div style="display:flex;gap:6px">' +
            '<button type="button" class="round-tool-btn magic" data-srun="' + esc(s.id) + '">▶ Correr Agora</button>' +
            '<button type="button" class="round-tool-btn" data-stog="' + esc(s.id) + '">' + (s.enabled ? '⏸ Pausar' : '▶ Ativar') + '</button>' +
            '<button type="button" class="round-tool-btn" data-sdel="' + esc(s.id) + '">🗑</button>' +
          '</div>';
        card.querySelector("[data-srun]").onclick = function(){
          api("/api/schedules/run", { id: s.id }).then(function(r){
            notifyUser("⏰ Automação Executada", r.result || "Concluído");
            loadSchedules();
          });
        };
        card.querySelector("[data-stog]").onclick = function(){
          api("/api/schedules/toggle", { id: s.id }).then(loadSchedules);
        };
        card.querySelector("[data-sdel]").onclick = function(){
          api("/api/schedules/toggle", { id: s.id, delete: true }).then(loadSchedules);
        };
        grid.appendChild(card);
      });
    });
  }

  document.getElementById("btnAddSchedule").onclick = function(){
    var name = document.getElementById("schedName").value.trim();
    var target = document.getElementById("schedTarget").value.trim();
    if (!name || !target) return;
    api("/api/schedules/save", {
      name: name,
      time_hhmm: document.getElementById("schedTime").value || "09:00",
      kind: document.getElementById("schedKind").value,
      target: target,
      enabled: true
    }).then(function(){
      document.getElementById("schedName").value = "";
      document.getElementById("schedTarget").value = "";
      loadSchedules();
    });
  };

  /* ---- MODULE 5: ARTIFACTS HUB ---- */
  function loadArtifacts() {
    var grid = document.getElementById("artifactsGrid");
    api("/api/artifacts").then(function(d){
      var list = d.artifacts || [];
      if (!list.length) {
        grid.innerHTML = '<div class="feature-card">Os blocos de código e interfaces gerados pelo Maestro aparecem aqui automaticamente!</div>';
        return;
      }
      grid.innerHTML = "";
      list.forEach(function(a){
        var card = document.createElement("div");
        card.className = "feature-card anim-in";
        card.innerHTML =
          '<div class="stat-head"><span>🧩 ' + esc(a.title) + '</span><span style="font-family:var(--mono);font-size:0.68rem;color:var(--cyan)">' + esc(a.lang) + '</span></div>' +
          '<pre style="margin:0;padding:8px;border-radius:8px;background:#050810;font-family:var(--mono);font-size:0.7rem;max-height:110px;overflow:hidden;color:#cbd5e1">' + esc((a.content || "").slice(0, 320)) + '</pre>' +
          '<div style="display:flex;gap:6px;margin-top:auto">' +
            '<button type="button" class="round-tool-btn magic" data-aopen="' + esc(a.id) + '">🪟 Abrir no Canvas</button>' +
            '<button type="button" class="round-tool-btn" data-adel="' + esc(a.id) + '">🗑</button>' +
          '</div>';
        card.querySelector("[data-aopen]").onclick = function(){
          openCanvas(a.title, a.content, a.lang);
        };
        card.querySelector("[data-adel]").onclick = function(){
          api("/api/artifacts/delete", { id: a.id }).then(loadArtifacts);
        };
        grid.appendChild(card);
      });
    });
  }

  /* ---- CHAT STREAM & MARKDOWN ---- */
  /* Registo persistente de snippets: os botões Copiar/Canvas guardam um id estável,
     para que o render incremental (que já não recria o chat inteiro) não os parta. */
  var codeStore = {};
  var codeSeq = 0;
  var codeOrder = [];
  function newCodeId(lang, code) {
    var cid = "cb" + (++codeSeq);
    codeStore[cid] = { lang: lang, code: code };
    codeOrder.push(cid);
    while (codeOrder.length > 800) { delete codeStore[codeOrder.shift()]; }
    return cid;
  }
  function mdInline(text) {
    var s = esc(text);
    s = s.replace(/`([^`\n]+)`/g, '<code class="inline-code">$1</code>');
    s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    s = s.replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s).,;:!?]|$)/g, '$1<em>$2</em>');
    s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
    return s;
  }
  function mdCells(line) {
    var t = String(line || "").trim();
    if (t.charAt(0) === "|") t = t.slice(1);
    if (t.charAt(t.length - 1) === "|") t = t.slice(0, -1);
    return t.split("|").map(function(c){ return c.trim(); });
  }
  // QA-CARD-START
  var qaSeq = 0;
  // Bloco ```qa: "Q: pergunta", "## Separador" e passos "1. ..." (ou texto livre) por separador.
  function qaCard(code) {
    var q = "", tabs = [], cur = null;
    String(code || "").split("\n").forEach(function(l) {
      var t = l.trim(), m;
      if (!t) return;
      if ((m = t.match(/^Q:\s*(.+)$/i))) q = m[1];
      else if ((m = t.match(/^##\s+(.+)$/))) { if (cur && !cur.name) cur.name = m[1]; else { cur = { name: m[1], steps: [] }; tabs.push(cur); } }
      else { if (!cur) { cur = { name: "", steps: [] }; tabs.push(cur); } cur.steps.push(t.replace(/^(\d+[.)]|[-*])\s+/, "")); }
    });
    var h = 0, c = String(code || ""); for (var k = 0; k < c.length; k++) h = (h * 31 + c.charCodeAt(k)) | 0;
    var id = "qa" + (h >>> 0).toString(36); // estável entre renders; igual conteúdo repetido é raro
    var head = tabs.length > 1 ? '<div class="qa-tabs">' + tabs.map(function(tb, n) {
      return '<input type="radio" class="qa-radio" name="' + id + '" id="' + id + '-' + n + '"' + (n ? '' : ' checked') + '><label for="' + id + '-' + n + '">' + esc(tb.name) + '</label>';
    }).join("") + '</div>' : '';
    var panes = tabs.map(function(tb, n) {
      return '<ol class="qa-steps qa-pane-' + n + '">' + tb.steps.map(function(x) { return '<li>' + mdInline(x) + '</li>'; }).join("") + '</ol>';
    }).join("");
    return '<div class="qa-card"><div class="qa-q">' + mdInline(q) + '</div>' + head + panes + '</div>';
  }
  // QA-CARD-END
  function renderRichMarkdown(md) {
    var blocks = [];
    var raw = String(md || "").replace(/\r\n/g, "\n");
    raw = raw.replace(/```([a-zA-Z0-9_+-]*)\n([\s\S]*?)```/g, function(_, lang, code){
      if (lang === "qa") { blocks.push(qaCard(code)); return "\n@@CODEBLOCK_" + (blocks.length - 1) + "@@\n"; }
      var idx = newCodeId(lang || "code", code);
      var token = "@@CODEBLOCK_" + blocks.length + "@@";
      blocks.push(
        '<div class="code-card">' +
          '<div class="code-card-head">' +
            '<span>' + esc(lang || "código") + '</span>' +
            '<div class="code-card-acts">' +
              '<button type="button" class="ico-only" data-code-copy="' + idx + '" aria-label="' + ui('Copiar', 'Copy') + '" data-tip="' + ui('Copiar|Copia o código para a área de transferência.', 'Copy|Copy the code to the clipboard.') + '">' + dualIco('copy', '📋') + '</button>' +
              '<button type="button" class="ico-only" data-code-canvas="' + idx + '" aria-label="Canvas" data-tip="' + ui('Canvas|Abre o código no painel lateral, com pré-visualização quando possível.', 'Canvas|Open the code in the side panel, with preview when possible.') + '">' + dualIco('canvas', '🪟') + '</button>' +
            '</div>' +
          '</div>' +
          (/^html$/i.test(lang) && /<(svg|canvas|table|div|section|style|script|button|input)\b/i.test(code) ? '<iframe class="code-visual" sandbox="allow-scripts" srcdoc="' + esc(visualTheme() + code + '<script>(function(){function s(){parent.postMessage({mvh:document.documentElement.scrollHeight},"*")}addEventListener("load",s);if(window.ResizeObserver)new ResizeObserver(s).observe(document.body)})()<\/script>').replace(/"/g, "&quot;") + '"></iframe>' : '') +
          '<pre' + (/^html$/i.test(lang) && /<(svg|canvas|table|div|section|style|script|button|input)\b/i.test(code) ? ' hidden' : '') + '><code>' + esc(code) + '</code></pre>' +
          (/^(bash|sh|shell)$/i.test(lang) ? '<div class="code-card-foot"><button type="button" class="code-authorize" data-code-action="' + idx + '"><span class="code-authorize-ico" aria-hidden="true">⚡</span><span>' + ui('Autorizar e avançar', 'Authorize and proceed') + '</span></button></div>' : '') +
        '</div>'
      );
      return "\n" + token + "\n";
    });

    var lines = raw.split("\n");
    var out = [];
    var i = 0;
    function flushParagraph(buf) {
      if (!buf.length) return;
      out.push('<div class="rich-p">' + buf.map(mdInline).join("<br>") + '</div>');
      buf.length = 0;
    }
    var para = [];

    while (i < lines.length) {
      var line = lines[i];
      var t = line.trim();

      if (!t) { flushParagraph(para); i++; continue; }

      var cb = t.match(/^@@CODEBLOCK_(\d+)@@$/);
      if (cb) {
        flushParagraph(para);
        out.push(blocks[Number(cb[1])] || "");
        i++; continue;
      }

      var hm = t.match(/^(#{1,4})\s+(.+)$/);
      if (hm) {
        flushParagraph(para);
        var lvl = hm[1].length;
        out.push('<div class="rich-h' + lvl + '">' + mdInline(hm[2]) + '</div>');
        i++; continue;
      }

      if (/^(---+|\*\*\*+|___+)$/.test(t)) {
        flushParagraph(para);
        out.push('<hr class="rich-hr">');
        i++; continue;
      }

      // Tabela markdown: | a | b | + |---|:--:|
      if (t.charAt(0) === "|" && i + 1 < lines.length && /^\s*\|?[\s:|-]*-[\s:|-]*\|?[\s:|-]*$/.test(lines[i + 1].trim())) {
        flushParagraph(para);
        var headers = mdCells(t);
        var rows = [];
        i += 2;
        while (i < lines.length && lines[i].trim().charAt(0) === "|") {
          rows.push(mdCells(lines[i]));
          i++;
        }
        var thead = headers.map(function(h){ return '<th>' + mdInline(h) + '</th>'; }).join("");
        var tbody = rows.map(function(r){
          return '<tr>' + headers.map(function(_, ci){ return '<td>' + mdInline(r[ci] || "") + '</td>'; }).join("") + '</tr>';
        }).join("");
        out.push('<div class="rich-table-wrap"><table class="rich-table"><thead><tr>' + thead + '</tr></thead><tbody>' + tbody + '</tbody></table></div>');
        continue;
      }

      // Callout / blockquote
      if (/^>\s?/.test(t)) {
        flushParagraph(para);
        var quote = [];
        while (i < lines.length && /^>\s?/.test(lines[i].trim())) {
          quote.push(lines[i].trim().replace(/^>\s?/, ""));
          i++;
        }
        var qtxt = quote.join(" ");
        var qcls = /(erro|crítico|critico|falha|❌|🚨)/i.test(qtxt) ? "err"
                 : /(aviso|atenção|atencao|⚠️|warn)/i.test(qtxt) ? "warn"
                 : /(ok|resolvido|sucesso|✅|concluído|concluido)/i.test(qtxt) ? "ok" : "";
        out.push('<div class="rich-callout ' + qcls + '">' + quote.map(mdInline).join("<br>") + '</div>');
        continue;
      }

      // Checklists
      if (/^\s*[-*]\s+\[[ xX]\]\s+/.test(line)) {
        flushParagraph(para);
        var items = [];
        while (i < lines.length && /^\s*[-*]\s+\[[ xX]\]\s+/.test(lines[i])) {
          var lm = lines[i].match(/^\s*[-*]\s+\[([ xX])\]\s+(.*)$/);
          var done = lm[1].toLowerCase() === "x";
          items.push('<div class="rich-list-item rich-check' + (done ? ' done' : '') + '"><span class="rich-check-box">' + (done ? "☑" : "☐") + '</span><span>' + mdInline(lm[2]) + '</span></div>');
          i++;
        }
        out.push('<div class="rich-list">' + items.join("") + '</div>');
        continue;
      }

      // Listas com marcadores
      if (/^\s*[-*•]\s+/.test(line)) {
        flushParagraph(para);
        var litems = [];
        while (i < lines.length && /^\s*[-*•]\s+/.test(lines[i]) && !/^\s*[-*]\s+\[[ xX]\]\s+/.test(lines[i])) {
          var li = lines[i].replace(/^\s*[-*•]\s+/, "");
          litems.push('<div class="rich-list-item"><span class="rich-list-bullet">▸</span><span>' + mdInline(li) + '</span></div>');
          i++;
        }
        out.push('<div class="rich-list">' + litems.join("") + '</div>');
        continue;
      }

      // Listas numeradas
      if (/^\s*\d+[.)]\s+/.test(line)) {
        flushParagraph(para);
        var nitems = [];
        while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])) {
          var nm = lines[i].match(/^\s*(\d+)[.)]\s+(.*)$/);
          nitems.push('<div class="rich-list-item"><span class="rich-list-bullet">' + nm[1] + '.</span><span>' + mdInline(nm[2]) + '</span></div>');
          i++;
        }
        out.push('<div class="rich-list">' + nitems.join("") + '</div>');
        continue;
      }

      para.push(line);
      i++;
    }
    flushParagraph(para);
    return out.join("");
  }

  /* ---- RENDER INCREMENTAL DO CHAT (sem "flash" a cada polling) ----
     Antes: cada atualização fazia box.innerHTML = "" e recriava todos os turnos,
     o que reiniciava a animação .anim-in => o chat "piscava" a cada 1,8s.
     Agora: cada turno tem uma assinatura (hash). Só é criado/substituído quando
     o conteúdo muda; o turno "ao vivo" é atualizado em sítio (textContent/innerHTML
     de blocos pequenos). Nada é destruído enquanto o agente pensa. */
  var _turnCache = {};   // idx -> { sig, el }
  var _liveEl = null;

  function hash32(str) {
    var h = 5381, i, s = String(str || "");
    for (i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0;
    return h;
  }

  function fmtChars(n) {
    n = Number(n) || 0;
    if (n >= 1000) return (n / 1000).toFixed(n >= 10000 ? 0 : 1) + "k chars";
    return n + " chars";
  }

  function msgSig(m, idx) {
    return (state.lang || "pt") + "§" + Boolean((state.currentChat || {}).archived) + "§" + idx + "§" + (m.role || "") + "§" + (m.agent_used || "") + "§" +
      ((m.text || "").length) + "§" + hash32(m.text) + "§" +
      ((m.images || []).length) + "§" + ((m.images || []).join(",")) + "§" +
      ((m.docs || []).map(function(d){ return d.path; }).join(",")) + "§" +
      (m.elapsed_s || "") + "§" +
      (m.plan ? "P" + (m.plan.domain || "") + (m.plan.dimension || "") + (m.plan.agents || []).join(",") + (m.tools || []).join(",") : "");
  }

  function hasImplementationSuggestion(m) {
    if (m.role !== 'assistant') return false;
    var text = String(m.text || '').replace(/```[\s\S]*?```/g, '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
    return /\b(?:sugiro|suger\w*|recomendaria|podemos implementar|proponho|propoe\w*|podes? implementar|posso (?:implementar|adicionar|criar)|poderiamos|recomendo|i suggest|i recommend|i can implement|we could|proposed flow)\b/.test(text) &&
      /\b(?:implement\w*|alterac\w*|corrigir|adicionar|botao|fluxo|triagem|subtarefas|codigo|feature|workflow|button|routing|code|change\w*)\b/.test(text);
  }

  function suggestionKey(m) {
    return 'apply-proposal-' + (m.turn_id || String(m.ts || '') + '-' + hash32(m.text));
  }

  function suggestionRequested(m, key) {
    var marker = '[' + (key || suggestionKey(m)) + ']';
    return ((state.currentChat || {}).messages || []).some(function(msg){ return msg.role === 'user' && String(msg.text || '').indexOf(marker) !== -1; }) ||
      state.messageQueue.some(function(t){ return String(t.text || '').indexOf(marker) !== -1; });
  }

  function applyImplementationSuggestion(m, button) {
    var chat = state.currentChat;
    if (!chat || chat.archived || button.disabled || suggestionRequested(m)) return;
    button.disabled = true;
    button.textContent = ui('A colocar em fila…', 'Queuing…');
    var config = selectedModelConfig();
    // A stable request id deduplicates retries; the source quote ties this to this exact response.
    var text = '[' + suggestionKey(m) + ']\n' + ui(
      'Implementa e valida a proposta abaixo nesta conversa. Respeita o âmbito e as restrições que já indiquei. Avança com o trabalho autorizado e apresenta o resultado concreto. Se a proposta tiver alternativas incompatíveis, esclarece apenas a escolha necessária.\n\nProposta a aplicar:\n',
      'Implement and validate the proposal below in this conversation. Respect my existing scope and constraints. Complete the authorized work and report the concrete result. If the proposal contains incompatible alternatives, clarify only the necessary choice.\n\nProposal to apply:\n') + m.text;
    return api('/api/chat/send', {id:chat.id, project:chat.project || 'nas', engine:chat.engine || 'auto',
      model_id:config.model_id, effort:config.effort, text:text, images:[], docs:[], async:true,
      request_id:chat.id + '-' + suggestionKey(m)}).then(function(res){
        button.textContent = ui('Implementação pedida', 'Implementation requested');
        if (!state.currentChat || state.currentChat.id !== chat.id) return;
        if (!receiveQueue(res)) return;
        if (res.chat) state.currentChat = res.chat;
        state.activeJob = res.job || null;
        renderMessages(); syncComposerBusyUI(); startJobPolling(chat.id);
      }).catch(function(){
        button.disabled = false;
        button.textContent = ui('Aplicar implementação sugerida', 'Apply suggested implementation');
        notifyUser('Maestro', ui('Não foi possível confirmar o pedido. Podes tentar novamente.', 'Could not confirm the request. You can retry.'));
      });
  }

  /* Caixas de resposta por pergunta: opções extraídas do texto + «Outra»;
     um Apply global envia tudo como UMA mensagem de seguimento. */
  var _qaDraft = {};
  function qaOptions(q, nextList) {
    var opts = [];
    if (nextList) nextList.querySelectorAll(":scope > .rich-list-item").forEach(function(li){
      var t = li.textContent.replace(/^\s*[▸•\-\d.]+\s*/, "").trim().replace(/[.;]$/, "");
      if (t && t.length <= 120) opts.push(t);
    });
    if (opts.length >= 2 && opts.length <= 8) return opts;
    var t = q.replace(/[.!?\s*]+$/, "");
    // Keep the alternatives, dropping the explanation and decision preamble.
    var choice = t.match(/\b(?:decidir|confirmar|decide|confirm)\s+(?:se|whether)\s+[^.!?]*?\b(?:são|é|are|is)\s+([^.!?]+\b(?:ou|or)\b[^.!?]+)$/i) ||
      t.match(/\b(?:escolher|decidir|choose|decide)\s+(?:entre|between)\s+([^.!?]+\b(?:ou|or)\b[^.!?]+)$/i);
    if (choice) t = choice[1];
    var alternativesOnly = t !== q.replace(/[.!?\s*]+$/, "");
    var paren = t.match(/\(([^()]*(?:\/|,|\bou\b|\bor\b)[^()]*)\)\s*$/);
    if (paren) t = paren[1]; else if (t.indexOf(":") > -1) t = t.slice(t.lastIndexOf(":") + 1);
    else if (!/\b(ou|or)\b|\//.test(t)) return [];
    else t = t.replace(/^.*?,\s*(?=[^,]*\b(?:ou|or)\b)/, "");
    var parts = t.split(/\s*(?:\/|,|;|\bou\b|\bor\b)\s*/i).map(function(s){ return s.trim().replace(/^[-–•*]\s*/, ""); }).filter(Boolean);
    if (parts.length < 2 || parts.length > 6) return [];
    if (!alternativesOnly && !paren && t === q.replace(/[.!?\s*]+$/, "") && parts[0].split(/\s+/).length > 2) /* tira o «Queres usar» */
      parts[0] = parts[0].replace(/^.*\b(?:usar|use|com|with|por|to|for|ficar com)\s+/i, "").split(/\s+/).slice(-2).join(" ");
    return parts.every(function(s){ return s.length <= 90 && s.split(/\s+/).length <= 14; }) ? parts : [];
  }

  function attachReplyBoxes(bubble, m) {
    var qs = window.maestroReplyPrompts(bubble);
    if (!qs.length) return;
    var key = 'qa-' + suggestionKey(m), draft = _qaDraft[key] || (_qaDraft[key] = {});
    var steps = qs.map(function(q, i){
      var next = q.el.nextElementSibling;
      var list = q.el.classList.contains("rich-p") && next && next.classList.contains("rich-list") && next.textContent.indexOf("?") < 0 ? next : null;
      return { hasList: !!list, q: q.text, d: draft[i] || (draft[i] = { sel: "", other: "", on: false }), opts: qaOptions(q.text, list) };
    });
    var boxes = steps;
    steps.forEach(function(st, i){
      var d = st.d, wiz = document.createElement("div");
      wiz.className = "qa-box qa-wiz";
      var row = document.createElement("div"); row.className = "qa-wiz-opts";
      st.opts.forEach(function(o){
        var b = document.createElement("button");
        b.type = "button"; b.className = "qa-opt" + (d.sel === o ? " on" : ""); b.textContent = o;
        b.setAttribute("aria-pressed", String(d.sel === o));
        b.onclick = function(){
          d.sel = d.sel === o ? "" : o; autoTick();
          row.querySelectorAll(".qa-opt").forEach(function(x){ x.classList.toggle("on", x.textContent === d.sel); x.setAttribute("aria-pressed", String(x.textContent === d.sel)); });
        };
        row.appendChild(b);
      });
      var tick = document.createElement("label"); tick.className = "qa-tick";
      var cb = document.createElement("input"); cb.type = "checkbox"; cb.checked = !!d.on;
      cb.onchange = function(){ d.on = cb.checked; };
      tick.appendChild(cb); tick.appendChild(document.createTextNode(ui(" Aplicar esta decisão", " Apply this decision")));
      wiz.appendChild(tick);
      // escolher ou escrever uma resposta marca-a sozinha; o visto continua desmarcável
      var autoTick = function(){ if (!d.on && (d.sel || d.other.trim())) { d.on = cb.checked = true; } };
      if (st.opts.length) wiz.appendChild(row);
      var inp = document.createElement("input");
      inp.type = "text"; inp.className = "qa-other"; inp.value = d.other;
      inp.placeholder = st.opts.length ? ui("Outra resposta…", "Other answer…") : ui("Escreve a tua resposta…", "Write your answer…");
      inp.setAttribute("aria-label", st.q);
      row.setAttribute("role", "group");
      row.setAttribute("aria-label", st.q);
      inp.oninput = function(){ d.other = inp.value; autoTick(); };
      wiz.appendChild(inp);
      var anchor = qs[i].el, nx = anchor.nextElementSibling;
      if (st.hasList) anchor = nx;
      anchor.parentNode.insertBefore(wiz, anchor.nextSibling);
    });
    var apply = document.createElement("button");
    apply.type = "button"; apply.className = "apply-implementation qa-apply";
    var sent = suggestionRequested(m, key);
    var applyLabel = ui("Enviar respostas", "Send answers");
    apply.disabled = sent;
    apply.textContent = sent ? ui("Respostas enviadas", "Answers sent") : applyLabel;
    apply.onclick = function(){
      var lines = [];
      boxes.forEach(function(b, i){
        var a = b.d.on ? [b.d.sel, b.d.other.trim()].filter(Boolean).join(" — ") : "";
        if (a) lines.push((i + 1) + ". " + b.q + "\n   → " + a);
      });
      if (!lines.length) { notifyUser("Maestro", ui("Marca e responde a pelo menos uma decisão.", "Tick and answer at least one decision.")); return; }
      var chat = state.currentChat; if (!chat || apply.disabled) return;
      apply.disabled = true; apply.textContent = ui("A enviar…", "Sending…");
      var config = selectedModelConfig();
      var text = "[" + key + "]\n" + ui("Respostas às tuas perguntas:\n\n", "Answers to your questions:\n\n") + lines.join("\n\n");
      api("/api/chat/send", {id:chat.id, project:chat.project || "nas", engine:chat.engine || "auto",
        model_id:config.model_id, effort:config.effort, text:text, images:[], docs:[], async:true,
        request_id:chat.id + "-" + key}).then(function(res){
          if (!state.currentChat || state.currentChat.id !== chat.id) return;
          if (!receiveQueue(res)) { apply.textContent = suggestionRequested(m, key) ? ui("Respostas enviadas", "Answers sent") : (apply.disabled = false, applyLabel); return; }
          if (res.chat) state.currentChat = res.chat;
          state.activeJob = res.job || null;
          renderMessages(); syncComposerBusyUI(); startJobPolling(chat.id);
        }).catch(function(){
          apply.disabled = false; apply.textContent = applyLabel;
          notifyUser("Maestro", ui("Não foi possível enviar. Tenta novamente.", "Could not send. Try again."));
        });
    };
    bubble.appendChild(apply);
  }

  function buildTurnNode(m, animate) {
    var turn = document.createElement("div");
    turn.className = "turn" + (animate === false ? "" : " anim-in") + " " + (m.role === "user" ? "user" : "assistant");
    var bubble = document.createElement("div");
    bubble.className = "bubble";
    var inner = "";

    if (m.role === "assistant" && m.plan) {
      inner +=
        '<details class="orch-details">' +
          '<summary>' + window.maestroRoleTag('maestro') + ' ' + ui('Orquestração', 'Orchestration') + ' · ' + esc(routeText(m.plan.domain)) + ' (' + esc(routeText(m.plan.dimension)) + ')' +
            (m.plan.agents && m.plan.agents.length ? ' → ' + esc(m.plan.agents.join(" ➔ ")) : '') +
            (m.elapsed_s ? ' · ' + m.elapsed_s + 's' : '') +
          '</summary>' +
          '<div class="orch-body">' +
            '<div><b>Pipeline:</b> ' + esc((m.plan.agents || []).join(" ➔ ")) + '</div>' +
            (m.plan.reason ? '<div><b>' + ui('Porquê:', 'Why:') + '</b> ' + esc(m.plan.reason) + '</div>' : '') +
            (m.tools && m.tools.length ? '<div><b>' + ui('Execução:', 'Execution:') + '</b> ' + esc(m.tools.join(" · ")) + '</div>' : '') +
          '</div>' +
        '</details>';
    }
    if (m.plan && m.plan.workflow) inner += renderWorkflow(m.plan.workflow, m.turn_id);
    inner += renderRichMarkdown(String(m.text || "").replace(/^\[apply-proposal-[^\]\n]+\]\n/, ""));
    if (m.docs && m.docs.length) {
      inner += '<div class="doc-chips">' + m.docs.map(function(d){
        return '<span class="doc-chip" title="' + esc(d.path || "") + '">📄 ' + esc(d.name || "colado") +
               ' · ' + fmtChars(d.chars) + '</span>';
      }).join("") + '</div>';
    }
    if (m.images && m.images.length) {
      inner += '<div class="turn-imgs">' + m.images.map(function(p){
        var url = '/api/image?path=' + encodeURIComponent(p);
        return '<img src="' + url + '" alt="Anexo" onclick="window.open(\'' + url + '\',\'_blank\')">';
      }).join("") + '</div>';
    }
    bubble.innerHTML = inner;
    var _cm = (state.currentChat || {}).messages || [];
    if (m.role === "assistant" && !(state.currentChat || {}).archived && _cm[_cm.length - 1] === m) attachReplyBoxes(bubble, m);
    turn.appendChild(bubble);

    var foot = document.createElement("div");
    foot.className = "turn-foot";
    foot.textContent = m.role === "assistant" ? statusText(m.agent_used || "Maestro AI") : ui("Tu", "You");
    if (!(state.currentChat || {}).archived) {
      var mkBtn = function(label, title, upto, prefill) {
        var b = document.createElement('button');
        b.type = 'button'; b.textContent = label; b.title = title;
        b.onclick = function(){
          var c = state.currentChat; if (!c) return;
          var idx = c.messages.indexOf(m); if (idx < 0) return;
          api('/api/chat/fork', {id: c.id, upto: idx + upto}).then(function(d){
            if (!d.chat) return;
            return openChat(d.chat.id).then(function(){
              refreshChats();
              if (prefill) { var ta = document.getElementById('promptInput'); ta.value = m.text || ''; ta.focus(); }
            });
          });
        };
        foot.appendChild(b);
      };
      if (m.role === 'user') mkBtn('✎ ' + ui('Editar', 'Edit'), ui('Nova conversa até aqui, com esta mensagem para editar', 'New chat up to here, with this message ready to edit'), 0, true);
      mkBtn('⑂ ' + ui('Bifurcar', 'Fork'), ui('Nova conversa a partir daqui', 'New chat from here'), 1, false);
    }
    turn.appendChild(foot);
    if (hasImplementationSuggestion(m) && !(state.currentChat || {}).archived) {
      var action = document.createElement('button');
      action.type = 'button'; action.className = 'apply-implementation';
      action.disabled = suggestionRequested(m);
      action.textContent = action.disabled ? ui('Implementação pedida', 'Implementation requested') : ui('Aplicar implementação sugerida', 'Apply suggested implementation');
      action.onclick = function(){ applyImplementationSuggestion(m, action); };
      turn.appendChild(action);
    }
    return turn;
  }


  function ui(pt, en) { return state.lang === "en" ? en : pt; }

  // Only server-owned status text goes through here; conversation prose stays intact.
  function statusText(value) {
    if (["evaluating", "dependency", "capacity", "quota", "daily_budget", "engine_busy", "interrupted"].indexOf(value) !== -1) return queueReason({reason:value});
    var text = String(value || "");
    if (state.lang !== "en") return text;
    var labels = {
      "Em curso": "In progress", "Interrompido": "Stopped",
      "Última resposta": "Last reply", "Último pedido": "Last request",
      "Nova conversa": "New chat", "À espera do primeiro pedido": "Waiting for the first request",
      "A processar anexos": "Processing attachments"
    };
    if (labels[text]) return labels[text];
    return text
      .replace(/^A executar comando: /, "Running command: ")
      .replace(/^Comando terminou: /, "Command finished: ")
      .replace(/^Alterações em ficheiros: /, "File changes: ")
      .replace(/^A executar ferramenta: /, "Running tool: ")
      .replace(/^Ferramenta terminou: /, "Tool finished: ")
      .replace(/^Ferramenta: /, "Tool: ")
      .replace(/^A ler: /, "Reading: ")
      .replace(/^A editar: /, "Editing: ")
      .replace(/^A escrever: /, "Writing: ")
      .replace(/^A pesquisar: /, "Searching: ")
      .replace(/A iniciar orquestração Maestro/, "Starting Maestro orchestration")
      .replace(/Contexto de passagem carregado do turno anterior/, "Handoff context loaded from the previous turn")
      .replace(/A executar com /, "Running with ")
      .replace(/A orquestrar/, "Orchestrating")
      .replace(/A processar (\d+) imagem\(ns\) anexada\(s\)/, "Processing $1 attached image(s)")
      .replace(/A ler ficheiro: /, "Reading file: ")
      .replace(/A editar ficheiro: /, "Editing file: ")
      .replace(/A criar\/escrever: /, "Creating/writing: ")
      .replace(/A executar: /, "Executing: ")
      .replace(/Pesquisa Web: /, "Web search: ")
      .replace(/A inspecionar código: /, "Inspecting code: ")
      .replace(/ a analisar o pedido/, " analyzing the request")
      .replace(/Tempo limite atingido — a terminar o agente/, "Time limit reached — stopping the agent")
      .replace(/Sessão iniciada · A inspecionar workspace e ferramentas/, "Session started · Inspecting workspace and tools")
      .replace(/A redigir resposta final/, "Writing the final reply")
      .replace(/Failover automático para /, "Automatic failover to ")
      .replace(/Plano: /, "Plan: ")
      .replace(/· motor: /, "· engine: ")
      .replace(/· suplente disponível: /, "· available substitute: ")
      .replace(/Motor · /, "Engine · ")
      .replace(/Maestro Substituto/g, "Substitute Maestro");
  }

  function routeText(value) {
    if (state.lang !== "en") return String(value || "");
    return String(value || "").replace(/Sistema \/ Pulso/, "System / Pulse")
      .replace(/Média \(Execução Cirúrgica\)/, "Medium (Targeted execution)")
      .replace(/Média/g, "Medium").replace(/Baixa/g, "Low").replace(/Alta/g, "High");
  }

  function stepsFeedHtml(steps) {
    return (steps || []).slice(-6).map(function(st, idx, arr){
      var isLast = (idx === arr.length - 1);
      return '<div class="step-row' + (isLast ? ' latest' : '') + '">' +
        '<span>' + (isLast ? '▸' : '✓') + '</span>' +
        '<span>' + esc(routeText(statusText(st))) + '</span>' +
      '</div>';
    }).join("");
  }

  /* Atualiza as linhas de passos em sítio. Só se reconstrói o contentor quando
     o número de linhas muda (primeiros passos); depois disso, cada poll mexe
     apenas em textContent/classes — zero innerHTML, zero pisca. */
  function paintStepsFeed(feed, steps) {
    if (!feed) return;
    var list = (steps || []).slice(-6);
    if (feed.children.length !== list.length) {
      feed.innerHTML = stepsFeedHtml(steps);
      return;
    }
    for (var i = 0; i < list.length; i++) {
      var row = feed.children[i];
      if (!row || row.children.length < 2) { feed.innerHTML = stepsFeedHtml(steps); return; }
      var isLast = i === list.length - 1;
      if (row.classList.contains("latest") !== isLast) row.classList.toggle("latest", isLast);
      var ico = row.children[0], label = row.children[1];
      var icoTxt = isLast ? "▸" : "✓";
      if (ico.textContent !== icoTxt) ico.textContent = icoTxt;
      var labelTxt = routeText(statusText(list[i]));
      if (label.textContent !== labelTxt) label.textContent = labelTxt;
    }
  }

  function buildLiveTurn(j) {
    var el = document.createElement("div");
    el.className = "turn assistant live-turn";
    el.innerHTML =
      '<div class="bubble live-work-bubble">' +
        '<div class="live-work-head" tabindex="0">' +
          '<div class="live-work-status">' +
            '<span class="typing-dots"><span></span><span></span><span></span></span>' +
            '<span class="request-status live-request-status" data-status="running">' + ui('a correr', 'running') + '</span>' +
            '<span class="step-txt"></span>' +
          '</div>' +
          '<span class="live-work-timer"></span>' +
        '</div>' +
        '<div class="orch-decision" hidden>' +
          '<div class="od-head">🧭 ' + ui("DECISÃO DO ORQUESTRADOR", "ORCHESTRATOR DECISION") + '</div>' +
          '<div class="od-route"></div>' +
          '<div class="od-why" hidden></div>' +
        '</div>' +
        '<div class="step-sub" hidden></div>' +
        '<div class="live-workflow"></div>' +
        '<div class="live-steps-feed"></div>' +
        '<div class="live-partial" hidden></div>' +
      '</div>' +
      '<div class="turn-foot"></div>';
    updateLiveTurn(el, j, true);
    return el;
  }

  /* Atualiza o turno ao vivo SEM o recriar: só mexe no que mudou. */
  function liveStableCut(text) {
    var lines = String(text || "").split("\n");
    var inFence = false, pos = 0, cut = 0;
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i];
      if (/^\s*(```|~~~)/.test(line)) inFence = !inFence;
      pos += line.length + 1;
      /* fim de bloco = linha vazia fora de um bloco de código */
      if (!inFence && !line.trim() && cut < pos) cut = pos;
    }
    return cut;
  }

  function paintLivePartial(pb, ptext) {
    if (!pb) return false;
    ptext = String(ptext || "");
    if (!ptext) {
      if (!pb.hidden) pb.hidden = true;
      return false;
    }
    if (pb.hidden) pb.hidden = false;
    var st = pb.querySelector(".lp-stable");
    var tl = pb.querySelector(".lp-tail");
    if (!st || !tl) {
      pb.innerHTML = '<div class="lp-stable"></div><div class="lp-tail"></div>';
      st = pb.querySelector(".lp-stable");
      tl = pb.querySelector(".lp-tail");
      pb.__stable = "";
      pb.__tail = "";
    }
    var changed = false;
    var cut = liveStableCut(ptext);
    var stable = ptext.slice(0, cut);
    if (stable !== pb.__stable) {
      var prev = pb.__stable || "";
      if (prev && stable.indexOf(prev) === 0) {
        /* Só os blocos novos: os antigos não são tocados. */
        var delta = stable.slice(prev.length);
        if (delta.trim()) st.insertAdjacentHTML("beforeend", renderRichMarkdown(delta));
      } else {
        st.innerHTML = renderRichMarkdown(stable);
      }
      pb.__stable = stable;
      changed = true;
    }
    var tail = ptext.slice(cut);
    if (tail !== pb.__tail) {
      /* Nó de texto puro: sem innerHTML não há elementos recriados, logo não
         há "flash" enquanto o modelo escreve a resposta. O Markdown completo
         entra no turno final, quando a execução termina. */
      var safeTail = tail.trim() ? tail : "";
      if (tl.textContent !== safeTail) tl.textContent = safeTail;
      pb.__tail = tail;
      changed = true;
    }
    return changed;
  }

  function updateLiveTurn(el, j, force) {
    if (!el || !j) return;
    var bubble = el.querySelector(".live-work-bubble");
    if (bubble) {
      // Keep shrinking activity rows from clamping a paused viewport, including
      // the moment focus leaves the composer to click the jump button.
      var box = chatScrollBox();
      bubble.style.minHeight = (composerHasFocus() || (box && box.__follow === false)) ? bubble.getBoundingClientRect().height + "px" : "";
    }
    var waiting = j.status === "queued";
    var badge = el.querySelector('.live-request-status');
    if (badge) { badge.dataset.status = waiting ? 'waiting' : 'running'; badge.textContent = waiting ? ui('em espera', 'waiting') : ui('a correr', 'running'); }
    var txt = el.querySelector(".step-txt");
    if (txt) txt.textContent = statusText(j.current_step || "🧠 A orquestrar…");
    var timer = el.querySelector(".live-work-timer");
    if (timer) {
      var secs = Math.max(0, Math.round(j.elapsed_s || 0));
      timer.textContent = waiting ? ui("Em fila", "Queued") : "⏱ " + (secs >= 60 ? Math.floor(secs / 60) + "m" + String(secs % 60).padStart(2, "0") + "s" : secs + "s");
    }
    var dots = el.querySelector(".typing-dots");
    if (dots) dots.hidden = waiting;

    var od = el.querySelector(".orch-decision");
    if (od) {
      var pl = j.plan || {};
      var route = [routeText(pl.domain), routeText(pl.dimension)].filter(Boolean).join(" · ");
      var agents = (pl.agents || []).join(" ➔ ");
      var why = pl.reason || "";
      var has = !!(route || agents || why);
      od.hidden = !has;
      if (has) {
        var rl = od.querySelector(".od-route");
        var txt = (route ? route : "") + (agents ? (route ? "   →   " : "") + agents : "");
        if (rl.textContent !== txt) rl.textContent = txt;
        var wl = od.querySelector(".od-why");
        wl.hidden = !why;
        if (why) wl.textContent = ui("porquê: ", "why: ") + why;
      }
    }

    var wf = el.querySelector(".live-workflow");
    if (wf) updateWorkflowPanel(wf, j.workflow);
    var sub = el.querySelector(".step-sub");
    var progress = el.querySelector('.live-progress');
    if (!progress && sub) {
      progress = document.createElement('details');
      progress.className = 'live-progress';
      progress.innerHTML = '<summary><span class="live-progress-label"></span><span class="live-progress-current" role="status" aria-live="polite" aria-atomic="true"></span><span class="live-progress-hint"></span></summary><div class="live-progress-updates"></div><p class="live-progress-note"></p>';
      sub.insertAdjacentElement('afterend', progress);
    }
    var events = j.progress_events || [];
    if (progress) {
      progress.hidden = waiting;
      progress.querySelector('.live-progress-label').textContent = ui('A trabalhar · agora', 'Working · now');
      var updates = events.filter(function(e) { return e.kind === 'update' && String(e.text || '').trim(); });
      var currentText = updates.length ? updates[updates.length - 1].text : ui('À espera de uma atualização do motor…', 'Waiting for an engine update…');
      var current = progress.querySelector('.live-progress-current');
      if (current.textContent !== currentText) current.textContent = currentText;
      progress.querySelector('.live-progress-hint').textContent = ui('Histórico de progresso', 'Progress history') + (updates.length ? ' · ' + updates.length : '');
      var updatesSig = JSON.stringify(updates);
      var updateList = progress.querySelector('.live-progress-updates');
      if (progress.__updatesSig !== updatesSig) {
        progress.__updatesSig = updatesSig;
        updateList.replaceChildren();
        updates.forEach(function(e) {
          var row = document.createElement('p');
          row.textContent = e.text;
          updateList.appendChild(row);
        });
        updateList.scrollTop = updateList.scrollHeight;
      }
      progress.querySelector('.live-progress-note').textContent = updates.length
        ? ui('Atualizações públicas do motor sobre o trabalho em curso.', 'Public engine updates about the work in progress.')
        : ui('À espera de uma atualização do motor. As ações reportadas aparecem abaixo.', 'Waiting for an engine update. Reported actions appear below.');
    }
    var activity = el.querySelector(".live-activity");
    if (!activity && sub) {
      activity = document.createElement("details");
      activity.className = "live-activity";
      activity.innerHTML = '<summary></summary><pre class="live-activity-detail"></pre>';
      if (progress) progress.appendChild(activity);
      else sub.insertAdjacentElement("afterend", activity);
    }
    var detail = statusText(j.activity_detail || j.last_line || ui("O motor ainda não reportou comandos ou ficheiros neste turno.", "The engine has not reported commands or files in this turn yet."));
    var activityAge = j.activity_age_s == null ? "" : ui(" · há ", " · ") + Math.round(j.activity_age_s) + ui("s", "s ago");
    if (activity) {
      activity.hidden = waiting;
      var summary = activity.querySelector("summary");
      var activityText = ui("Última atividade", "Last activity") + activityAge + ": " + detail.replace(/\s+/g, " ");
      if (summary.textContent !== activityText) summary.textContent = activityText;
      var fullDetail = activity.querySelector("pre");
      var actionHistory = events.filter(function(e) { return e.kind === 'action'; }).map(function(e) { return statusText(e.text); }).join('\n\n') || detail;
      if (fullDetail.textContent !== actionHistory) fullDetail.textContent = actionHistory;
    }
    var activityTip = ui("ⓘ Última atividade reportada", "ⓘ Last reported activity") + "|" +
      statusText(j.agent_label || "Maestro AI") + "\n" + detail + "\n\n" +
      ui("Última saída recebida há ", "Last output received ") + Math.round(j.idle_s || 0) + "s" +
      ui(" · Sem saída não significa que esteja parado.", " ago · No output does not mean it has stalled.");
    [el.querySelector(".live-work-head"), sub].forEach(function(target) {
      if (!target) return;
      target.setAttribute("data-tip", activityTip);
      if (typeof activeTipTarget !== "undefined" && activeTipTarget === target) showModernTip(target);
    });
    if (sub) {
      var bits = [];
      if (j.last_line) bits.push("↳ " + (j.last_line.length > 150 ? j.last_line.slice(0, 150) + "…" : j.last_line));
      if (j.cpu_s > 0) bits.push("cpu " + j.cpu_s + "s");
      if (j.idle_s > 180) bits.push(ui("Sem saída há ", "No output for ") + Math.round(j.idle_s / 60) + ui(" min · a execução pode continuar ativa", " min · execution may still be active"));
      else if (j.idle_s > 20) bits.push(ui("sem saída há ", "no output for ") + Math.round(j.idle_s) + "s");
      var subTxt = bits.join(" · ");
      if (sub.textContent !== subTxt) sub.textContent = subTxt;
      sub.style.color = j.idle_s > 180 ? "#fb923c" : "";
      /* Nunca esconder: a linha tem altura reservada em CSS, por isso aparecer
         ou desaparecer texto já não faz o balão (e o chat) saltar. */
      if (sub.hidden) sub.hidden = false;
    }

    var sSig = (j.steps || []).join("|");
    if (force || el.__stepsSig !== sSig) {
      el.__stepsSig = sSig;
      paintStepsFeed(el.querySelector(".live-steps-feed"), j.steps);
    }

    var ptext = j.partial_text || "";
    var pSig = ptext.length + "§" + hash32(ptext);
    if (force || el.__partSig !== pSig) {
      el.__partSig = pSig;
      paintLivePartial(el.querySelector(".live-partial"), ptext);
    }

    var foot = el.querySelector(".turn-foot");
    if (foot) {
      var _maestro = j.maestro_label || (j.plan || {}).maestro_name || "";
      foot.textContent = (_maestro ? ui("Maestro disponível: ", "Available Maestro: ") + statusText(_maestro) + " · " : "") + statusText(j.agent_label || "Maestro AI") +
        (waiting ? ui(" · Em fila · aguarda execução", " · Queued · waiting to start") : ui(" · Em execução ao vivo (podes escrever abaixo para meter em fila ou orientar)", " · Running live (type below to queue a message or steer)")) +
        ((j.parallel_count || 0) > 1 ? ui(" · Agentes ativos: ", " · Active agents: ") + (j.active_agents || []).map(function(a){ return a.name; }).join(" + ") : "");
    }
    stickToBottom();
  }

  /* Cria o turno ao vivo se ainda não existir; nunca toca nos turnos já renderizados. */
  function ensureLiveTurn(j) {
    if (j.status !== "running") {
      if (_liveEl && _liveEl.parentNode) _liveEl.remove();
      _liveEl = null;
      return;
    }
    var box = document.getElementById("chatScroll");
    if (!box) return;
    if (!_liveEl || _liveEl.parentNode !== box) {
      _liveEl = buildLiveTurn(j);
      placeLiveTurn(box, j);
      stickToBottom();
      return;
    }
    placeLiveTurn(box, j);
    updateLiveTurn(_liveEl, j);
  }

  function placeLiveTurn(box, job) {
    var messages = (state.currentChat || {}).messages || [];
    var idx = messages.findIndex(function(m){ return m.role === 'user' && m.turn_id && m.turn_id === job.task_id; });
    var request = idx >= 0 && _turnCache[idx] && _turnCache[idx].el;
    if (request && request.parentNode === box) {
      if (request.nextSibling !== _liveEl) box.insertBefore(_liveEl, request.nextSibling);
    } else if (_liveEl.parentNode !== box || _liveEl.nextSibling) box.appendChild(_liveEl);
  }

  /* ---- SEGUIMENTO DO FIM DO CHAT (sem "yank" enquanto lês) ----
     O erro antigo: cada poll (950 ms) fazia scroll para o fim sempre que o
     chat estava a menos de 170 px do fundo. Bastava o Hubert subir um pouco
     para ler e era puxado outra vez para baixo — e, como o balão ao vivo
     cresce e encolhe, o chat parecia andar para cima e para baixo.

     Agora o seguimento é uma intenção explícita:
       - liga-se apenas quando o utilizador está mesmo no fim (<= 4 px);
       - desliga-se no primeiro gesto para cima (roda, toque ou scroll);
       - só volta a ligar quando ele regressa ao fim ou carrega em «Ir para o fim»;
       - o scroll automático é instantâneo, coalescido num frame, e o evento
         que ele próprio dispara é ignorado (não conta como gesto do utilizador). */
  var SCROLL_BOTTOM_EPS = 4;
  var _autoScrollAt = 0;
  var _autoScrollTop = -1;
  var _autoScrollRaf = 0;
  var _smoothUntil = 0;

  function chatScrollBox() { return document.getElementById("chatScroll"); }

  function distanceToBottom(box) {
    if (!box) return 0;
    return Math.max(0, box.scrollHeight - box.scrollTop - box.clientHeight);
  }

  function setChatFollow(on) {
    var box = chatScrollBox();
    if (!box) return;
    box.__follow = !!on;
    if (on) {
      var btn = document.getElementById("btnJumpEnd");
      if (btn) btn.classList.remove("show");
    }
  }

  function noteChatIntent() {
    var box = chatScrollBox();
    if (box) box.__userIntentAt = Date.now();
  }

  function composerHasFocus() {
    /* Escrever já não congela o chat: quem está no fundo continua a seguir a
       última mensagem; só quem subiu (follow=false) fica parado. */
    return false;
  }

  function pauseChatForComposer() {
    if (distanceToBottom(chatScrollBox()) <= 8) return; /* no fundo: continua a seguir */
    setChatFollow(false);
    if (_autoScrollRaf) window.cancelAnimationFrame(_autoScrollRaf);
    _autoScrollRaf = 0;
    _smoothUntil = 0;
    var box = chatScrollBox();
    // Cancel any smooth jump already in flight without changing the viewport.
    if (box) box.scrollTo({ top: box.scrollTop, behavior: "instant" });
    updateJumpEndBtn();
  }

  function scrollChatToBottom(force, smooth) {
    var box = chatScrollBox();
    if (!box) return;
    if (!force && (box.__follow === false || composerHasFocus())) {
      updateJumpEndBtn();
      return;
    }
    if (document.hidden) return;
    var top = Math.max(0, box.scrollHeight - box.clientHeight);
    if (smooth) {
      box.__follow = true;
      _autoScrollAt = Date.now();
      _autoScrollTop = top;
      _smoothUntil = Date.now() + 900;
      box.scrollTo({ top: top, behavior: "smooth" });
      return;
    }
    if (Math.abs(box.scrollTop - top) < 1) return;
    if (_autoScrollRaf) return;
    var mustScroll = !!force;
    _autoScrollRaf = window.requestAnimationFrame(function () {
      _autoScrollRaf = 0;
      var b = chatScrollBox();
      if (!b || (!mustScroll && (b.__follow === false || composerHasFocus()))) return;
      var target = Math.max(0, b.scrollHeight - b.clientHeight);
      _autoScrollAt = Date.now();
      _autoScrollTop = target;
      b.scrollTop = target;
      updateJumpEndBtn();
    });
  }

  function onChatScroll() {
    var box = chatScrollBox();
    if (!box) return;
    var d = distanceToBottom(box);
    /* Eco do nosso próprio scroll (ou de um «Ir para o fim» suave): não é o
       utilizador a mexer, por isso não pode desligar o seguimento. */
    var isAuto = (Date.now() < _smoothUntil) ||
      ((Date.now() - _autoScrollAt) < 300 &&
       _autoScrollTop >= 0 && Math.abs(box.scrollTop - _autoScrollTop) < 3);
    if (!isAuto) {
      if (d <= SCROLL_BOTTOM_EPS && !composerHasFocus()) box.__follow = true;
      /* Só um gesto recente do utilizador desliga o seguimento. Assim, uma
         imagem que termina de carregar acima (e ajusta o scrollHeight) já não
         é confundida com «o Hubert quis subir». */
      else if ((Date.now() - (box.__userIntentAt || 0)) < 1500) box.__follow = false;
    }
    updateJumpEndBtn();
  }

  function updateJumpEndBtn() {
    var box = chatScrollBox();
    var btn = document.getElementById("btnJumpEnd");
    if (!box || !btn) return;
    /* Só decide se o botão aparece. Nunca mexe no seguimento: essa decisão é
       do gesto do utilizador (onChatScroll) e não da posição no momento.
       Durante o «salto suave» fica escondido para não piscar, e um crescimento
       transitório do balão (seguimento ainda ligado) também não o mostra. */
    updateJumpUpBtn();
    if (Date.now() < _smoothUntil) { btn.classList.remove("show"); return; }
    btn.classList.toggle("show", box.__follow === false && distanceToBottom(box) > 8);
  }

  /* Navigate every actionable request, including Apply proposals and NAS approvals. */
  function pendingAttentionTargets() {
    var box = chatScrollBox();
    if (!box || (state.currentChat || {}).archived) return [];
    var targets = Array.from(box.querySelectorAll(
      '.turn.assistant:not(.live-turn) .apply-implementation:not(:disabled), ' +
      '.turn.assistant:not(.live-turn) [data-code-action]:not(:disabled)'
    )).filter(function(el) {
      var status = el.dataset.actionState;
      return !el.hidden && el.getClientRects().length &&
        (!status || ['pending', 'failed', 'expired'].indexOf(status) !== -1);
    });
    var question = box.__pendingQuestionTurn;
    if (question && box.contains(question) && question.getClientRects().length &&
        !question.querySelector('.qa-apply:disabled') &&
        !targets.some(function(el) { return question.contains(el); })) targets.push(question);
    return targets;
  }
  function pendingAttentionTarget() {
    var box = chatScrollBox(), targets = pendingAttentionTargets();
    if (!box || !targets.length) return null;
    var top = box.getBoundingClientRect().top + 40;
    // Visit the closest pending control above before others still in view/below.
    var above = targets.filter(function(el) { return el.getBoundingClientRect().top < top; });
    return above.length ? above[above.length - 1] : targets[0];
  }
  function updateJumpUpBtn() {
    var up = document.getElementById('btnJumpUp');
    if (!up) return;
    var targets = pendingAttentionTargets();
    up.classList.toggle('show', targets.length > 0);
    up.tabIndex = targets.length ? 0 : -1;
    var label = ui('Requer a tua atenção', 'Needs your attention') + ' (' + targets.length + ')';
    up.title = label;
    up.setAttribute('aria-label', label);
    var count = up.querySelector('.attention-count');
    if (count) count.textContent = targets.length;
  }

  function stickToBottom(force) {
    scrollChatToBottom(!!force);
  }

  /* FAB circular na borda direita da coluna de chat e com folga real acima do
     composer: mede a caixa do formulário (não só a dock), por isso crescer o
     textarea, a fila ou os anexos nunca o deixa colado à caixa de texto. */
  function placeJumpEndBtn() {
    var btn = document.getElementById("btnJumpEnd");
    var pane = document.getElementById("paneChat");
    if (!btn || !pane) return;
    var form = pane.querySelector("#composerForm") || pane.querySelector(".composer-dock");
    if (!form) return;
    var pr = pane.getBoundingClientRect();
    var fr = form.getBoundingClientRect();
    if (!pr.width || !fr.width) return;
    /* Alinha a borda direita do FAB com a borda direita da coluna de chat. */
    var right = Math.max(10, Math.round(pr.right - fr.right));
    var strategy = document.querySelector('#stPanel:not(.closed)');
    if (strategy && strategy.getClientRects().length) {
      var sr = strategy.getBoundingClientRect();
      right = Math.max(right, Math.round(pr.right - sr.left + 12));
    }
    right = Math.min(right, Math.max(10, pr.width - 56));
    btn.style.setProperty("--fab-right", right + "px");
    btn.style.bottom = Math.round(pr.bottom - fr.top + 20) + "px";
    var up = document.getElementById("btnJumpUp");
    if (up) { up.style.setProperty("--fab-right", btn.style.getPropertyValue("--fab-right")); up.style.bottom = btn.style.bottom; }
    updateJumpEndBtn();
  }

  function renderMessages() {
    var box = document.getElementById("chatScroll");
    var c = state.currentChat;
    if (!box) return;

    var switchedChat = (state.renderedChatId !== (c && c.id));
    state.renderedChatId = c && c.id;
    applyChatStyle();

    if (switchedChat) {
      var _sc = document.getElementById("chatScroll");
      if (_sc) {
        _sc.__follow = true;
        _sc.style.visibility = "hidden"; /* sem salto visível do topo para o fim */
        setTimeout(function () { restoreScrollFor(c && c.id); }, 30);
        setTimeout(function () { _sc.style.visibility = ""; }, 600);
      }
    }

    if (!c || !c.messages || !c.messages.length) {
      _turnCache = {};
      _liveEl = null;
      if (!box.querySelector(".welcome-studio")) {
        box.innerHTML =
          '<div class="welcome-studio anim-in">' +
            '<img class="welcome-logo" src="/icon.svg" alt="Maestro AI">' +
            '<h1>Maestro AI Studio 3.0</h1>' +
            '<p>Comanda os teus projetos, <b>Filmes & Séries (Radarr/Sonarr)</b>, <b>Impressoras 3D (Home Assistant)</b>, <b>Gerador de Imagens/GIFs</b> e <b>Polistrat / SpaceBourne 2</b> num único sítio.</p>' +
            '<div class="starter-grid">' +
              '<div class="starter-card" data-go-mod="media">' +
                '<div class="sc-tag" style="color:var(--rose)">🎬 RADARR & SONARR</div>' +
                '<strong>Sacar Filmes, Séries & Containers</strong>' +
                '<span>Pesquisa filmes/séries com poster e mete a sacar com 1 clique.</span>' +
              '</div>' +
              '<div class="starter-card" data-go-mod="studio">' +
                '<div class="sc-tag" style="color:var(--violet)">🎨 IMAGENS & GIFS</div>' +
                '<strong>Gerador AI, GIFs & Galeria</strong>' +
                '<span>Gera imagens Flux HD, animações GIF89a e gere os teus prints.</span>' +
              '</div>' +
              '<div class="starter-card" data-go-mod="casa">' +
                '<div class="sc-tag" style="color:var(--amber)">🏠 HOME ASSISTANT & 3D</div>' +
                '<strong>Impressoras 3D, Tomadas & Schedulers</strong>' +
                '<span>Centauri Carbon 2, Qidi Plus 4, tomadas e automações agendadas.</span>' +
              '</div>' +
              '<div class="starter-card" data-proj="polistrat" data-q="Inspeciona os scripts C# recentes do Polistrat e resume o estado atual.">' +
                '<div class="sc-tag" style="color:var(--cyan)">🎮 POLISTRAT & MODS</div>' +
                '<strong>Código, Git & Artifacts</strong>' +
                '<span>Scout em Antigravity + delegação cirúrgica com Canvas lateral.</span>' +
              '</div>' +
            '</div>' +
          '</div>';
        box.querySelectorAll("[data-go-mod]").forEach(function(card){
          card.onclick = function(){ setModule(card.getAttribute("data-go-mod")); };
        });
        box.querySelectorAll("[data-q]").forEach(function(card){
          card.onclick = function(){
            var pj = card.getAttribute("data-proj");
            if (pj) document.getElementById("selProject").value = pj;
            var ta = document.getElementById("promptInput");
            ta.value = card.getAttribute("data-q");
            ta.focus();
          };
        });
      }
      placeJumpEndBtn();
      return;
    }

    var welcome = box.querySelector(".welcome-studio");
    if (welcome && welcome.parentNode) welcome.parentNode.removeChild(welcome);

    if (switchedChat) {
      box.innerHTML = "";
      _turnCache = {};
      _liveEl = null;
    }

    var prevTop = box.scrollTop;
    /* Segue apenas se já estava a seguir. A posição a 160 px do fundo deixou
       de contar: estar perto não é o mesmo que querer ser puxado. */
    var wasNearBottom = switchedChat || box.__follow !== false;

    var msgs = c.messages;
    var seen = {};

    // 1) Upsert: cria só turnos novos ou cujo conteúdo mudou.
    msgs.forEach(function(m, idx){
      if (m.role === 'user' && state.messageQueue.some(function(t){ return t.id === m.turn_id && !t.started_at; })) return;
      var sig = msgSig(m, idx);
      seen[idx] = 1;
      var cached = _turnCache[idx];
      if (cached && cached.sig === sig && cached.el.parentNode === box) return;
      /* Substituir um turno já visível não deve repetir a animação de entrada:
         era isso que fazia o bloco inteiro «mexer-se» a cada atualização. */
      var el = buildTurnNode(m, !cached);
      if (cached && cached.el.parentNode === box) box.replaceChild(el, cached.el);
      _turnCache[idx] = { sig: sig, el: el };
    });

    // 2) Remove turnos que já não existem.
    Object.keys(_turnCache).forEach(function(k){
      if (!seen[k]) {
        var node = _turnCache[k].el;
        if (node.parentNode) node.parentNode.removeChild(node);
        delete _turnCache[k];
      }
    });

    // 3) Ordena movendo apenas os nós fora de sítio (nunca recria).
    var prevNode = null;
    msgs.forEach(function(_, idx){
      if (!_turnCache[idx]) return;
      var el = _turnCache[idx].el;
      var expected = prevNode ? prevNode.nextSibling : box.firstChild;
      if (expected !== el) box.insertBefore(el, expected || null);
      prevNode = el;
    });

    msgs.forEach(function(m, idx){
      var el = _turnCache[idx] && _turnCache[idx].el;
      var action = el && el.querySelector('.apply-implementation:not(.qa-apply)');
      if (action && suggestionRequested(m)) {
        action.disabled = true;
        action.textContent = ui('Implementação pedida', 'Implementation requested');
      }
      var qa = el && el.querySelector('.qa-apply');
      if (qa && suggestionRequested(m, 'qa-' + suggestionKey(m))) {
        qa.disabled = true;
        qa.textContent = ui('Respostas enviadas', 'Answers sent');
      }
    });

    // State belongs to a request id, never to whichever message is last.
    msgs.forEach(function(m, idx){
      if (m.role !== 'user' || !_turnCache[idx]) return;
      var queued = state.messageQueue.find(function(t){ return t.id === m.turn_id; });
      var active = state.activeJob && state.activeJob.status === 'running' && state.activeJob.task_id === m.turn_id;
      var requestState = active ? 'running' : queued ? (queued.status === 'interrupted' ? 'interrupted' : 'waiting') : '';
      var label = active ? ui('a correr', 'running') : queued ? (queued.status === 'interrupted' ? ui('interrompido', 'interrupted') : ui('em espera', 'waiting')) : '';
      var admission = m.admission || {};
      var details = [admission.engine, admission.model_id].filter(Boolean).join(' · ');
      if (admission.reason === 'parallel') details += ui(' · Execução em paralelo', ' · Parallel execution');
      if (admission.reason === 'essential_reserve') details += ui(' · Reserva essencial: incidente crítico sem alternativa compatível', ' · Essential reserve: critical incident without a compatible alternative');
      if (admission.reason === 'economy') details += ui(' · Escolha por economia', ' · Economy choice');
      var foot = _turnCache[idx].el.querySelector('.turn-foot');
      var status = foot.querySelector('.request-meta');
      if (!status) {
        Array.from(foot.childNodes).forEach(function(n){ if (n.nodeType === 3) n.remove(); });
        status = document.createElement('span'); status.className = 'request-meta'; foot.prepend(status);
      }
      status.innerHTML = esc(ui('Tu', 'You')) + (label ? ' <span class="request-status" data-status="' + requestState + '">' + esc(label) + '</span>' : '') + (queued ? ' · ' + esc(queueReason(queued)) : '') + (details ? ' · ' + esc(details) : '');
    });

    // 4) Turno ao vivo (atualizado em sítio).
    var live = (state.activeJob && state.activeJob.status === "running" && state.activeJob.chat_id === c.id)
      ? state.activeJob : null;
    if (live) {
      if (!_liveEl || _liveEl.parentNode !== box) {
        _liveEl = buildLiveTurn(live);
        placeLiveTurn(box, live);
      } else {
        placeLiveTurn(box, live);
        updateLiveTurn(_liveEl, live);
      }
    } else if (_liveEl) {
      if (_liveEl.parentNode) _liveEl.parentNode.removeChild(_liveEl);
      _liveEl = null;
    }

    if (wasNearBottom) {
      box.__follow = true;
      scrollChatToBottom(false);
    } else {
      box.scrollTop = prevTop;
    }
    placeJumpEndBtn();
  }

  // Botão flutuante «Ir para o fim»: aparece quando o scroll sobe.
  (function initJumpEndButton() {
    var box = document.getElementById("chatScroll");
    var btn = document.getElementById("btnJumpEnd");
    if (!box || !btn) return;
    box.addEventListener("scroll", onChatScroll, { passive: true });
    var upBtn = document.getElementById("btnJumpUp");
    if (upBtn) upBtn.addEventListener("click", function(){
      var t = pendingAttentionTarget();
      if (t) {
        setChatFollow(false);
        t.scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'center' });
        if (!t.matches('button, input, textarea')) t = t.querySelector('button:not(:disabled), input, textarea') || t;
        if (t.focus) t.focus({ preventScroll: true });
      }
    });
    new MutationObserver(function(){ updateJumpUpBtn(); }).observe(box, { childList: true, subtree: true, attributes: true, attributeFilter: ["disabled", "hidden", "data-action-state"] });
    box.addEventListener("pendingquestionschange", updateJumpUpBtn);
    /* O composer muda de altura (textarea, fila, anexos): o FAB acompanha. */
    var dockEl = document.querySelector("#paneChat #composerForm");
    if (dockEl && window.ResizeObserver) {
      new ResizeObserver(function(){ placeJumpEndBtn(); }).observe(dockEl);
    }
    window.addEventListener("resize", placeJumpEndBtn);
    window.addEventListener("strategyvisibilitychange", placeJumpEndBtn);
    /* Gesto para cima desliga o seguimento ANTES de o scroll suavizar; assim
       nenhum poll de 950 ms volta a puxar o chat para baixo no meio da leitura. */
    box.addEventListener("wheel", function(e){
      noteChatIntent();
      if (e.deltaY < 0) setChatFollow(false);
      else if (distanceToBottom(box) <= SCROLL_BOTTOM_EPS) setChatFollow(true);
    }, { passive: true });
    /* Clique na barra de scroll / arrasto dentro do chat também é intenção. */
    box.addEventListener("pointerdown", function(e){
      if (e.target === box) noteChatIntent();
    }, { passive: true });
    var touchY = 0;
    box.addEventListener("touchstart", function(e){
      if (e.touches && e.touches[0]) touchY = e.touches[0].clientY;
    }, { passive: true });
    box.addEventListener("touchmove", function(e){
      if (!e.touches || !e.touches[0]) return;
      noteChatIntent();
      var y = e.touches[0].clientY;
      /* Dedo a descer = conteúdo a subir = o Hubert está a ler para trás. */
      if (y > touchY + 2) setChatFollow(false);
      touchY = y;
    }, { passive: true });
    btn.addEventListener("click", function(){
      setChatFollow(true);
      scrollChatToBottom(true, true);
      /* A animação suave pode acabar ligeiramente antes do fundo e disparar
         um scroll a >4 px, que desligaria o seguimento. No fim, fixa-o — mas
         só se o Hubert entretanto não tiver voltado a subir. */
      setTimeout(function(){
        if (box.__follow !== false) {
          setChatFollow(true);
          scrollChatToBottom(true);
        }
        updateJumpEndBtn();
      }, 850);
    });
    document.querySelectorAll(".attach-strip, .queue-strip").forEach(function(st){
      if (st && window.MutationObserver) {
        new MutationObserver(placeJumpEndBtn).observe(st, { childList: true, attributes: true });
      }
    });
    window.addEventListener("resize", placeJumpEndBtn);
    placeJumpEndBtn();
  })();

  window.addEventListener("message", function(e){
    var h = e.data && e.data.mvh;
    if (!h) return;
    document.querySelectorAll("iframe.code-visual").forEach(function(f){
      if (f.contentWindow === e.source) f.style.height = Math.min(Math.max(h, 60), 900) + "px";
    });
  });

  /* Delegação: Copiar / Canvas dos blocos de código (sobrevive ao render incremental). */
  (function initCodeActions() {
    var box = document.getElementById("chatScroll");
    if (!box) return;
    box.addEventListener("click", function(e){
      var t = (e.target && e.target.closest) ? e.target.closest("[data-code-copy],[data-code-canvas]") : null;
      if (!t) return;
      var cid = t.getAttribute("data-code-copy") || t.getAttribute("data-code-canvas");
      var item = codeStore[cid];
      if (!item) return;
      if (t.hasAttribute("data-code-copy")) {
        if (navigator.clipboard) {
          navigator.clipboard.writeText(item.code);
          t.classList.add("copied");
          setTimeout(function(){ t.classList.remove("copied"); }, 1200);
        }
      } else {
        openCanvas("Snippet · " + item.lang, item.code, item.lang);
      }
    });
  })();

  /* Explicit NAS actions: preview exact saved command, then approve once. */
  (function initAuthorizedActions(){
    var style = document.createElement("style");
    style.textContent = [
      '.turn.user [data-code-action],.live-turn [data-code-action],.turn.user .code-card-foot,.live-turn .code-card-foot{display:none}',
      '.code-card-acts{flex-wrap:wrap}',
      '@keyframes action-spin{to{transform:rotate(360deg)}}',
      '@keyframes action-done{50%{transform:scale(1.06);filter:brightness(1.3)}}',
      '.code-authorize[data-action-state=succeeded]{background:linear-gradient(135deg,#15803d,#047857);border-color:#4ade80}',
      '.code-authorize[data-action-state=failed]{border-color:#fb7185}',
      '.code-authorize[data-action-state=starting] .code-authorize-ico,.code-authorize[data-action-state=running] .code-authorize-ico{display:inline-block;animation:action-spin 1s linear infinite}',
      '.code-authorize[data-action-state=succeeded]{animation:action-done .5s ease}',
      '.action-approve[aria-busy=true]::before{content:"◌";display:inline-block;margin-right:8px;animation:action-spin 1s linear infinite}',
      '@media(prefers-reduced-motion:reduce){.code-authorize,.code-authorize .code-authorize-ico,.action-approve::before{animation:none!important}}',
      '.action-dialog{color:var(--text,#eee);background:var(--bg,#111827);border:1px solid #64748b;border-radius:16px;width:min(720px,90vw);max-height:85dvh;padding:20px}',
      '.action-dialog::backdrop{background:#0009}',
      '.action-dialog h2{margin:0 0 10px;font-size:1.05rem}',
      '.action-dialog .action-output:empty,.action-dialog .action-status:empty{display:none}',
      '.action-dialog pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:40dvh;overflow:auto;border:1px solid var(--line);border-radius:10px;padding:10px;background:#050810}',
      '.action-dialog .action-acts{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-top:14px}',
      '.action-dialog .action-approve{flex:1 1 240px;justify-content:center;min-height:40px;padding:11px 18px;border-radius:10px;border:1px solid color-mix(in srgb,var(--cyan) 78%,#fff);background:linear-gradient(135deg,var(--cyan),#6366f1 62%,var(--violet));color:#fff;font-family:var(--sans);font-size:.86rem;font-weight:700;cursor:pointer;text-shadow:0 1px 2px rgba(5,9,18,.45);box-shadow:inset 0 1px 0 rgba(255,255,255,.28),0 8px 22px color-mix(in srgb,var(--cyan) 28%,transparent);transition:transform .15s ease,filter .15s ease}',
      '.action-dialog .action-approve:hover{transform:translateY(-1px);filter:brightness(1.08)}',
      '.action-dialog .action-approve:active{transform:translateY(0) scale(.98)}',
      '.action-dialog .action-approve:disabled{opacity:.55;cursor:wait;transform:none;filter:none}',
      '.action-dialog .action-cancel,.action-dialog .action-close{min-height:40px;padding:10px 14px;border-radius:9px;border:1px solid var(--line);background:rgba(255,255,255,.05);color:#e2e8f0;font-family:var(--sans);font-size:.8rem;font-weight:600;cursor:pointer}',
      '.action-dialog .action-cancel:hover,.action-dialog .action-close:hover{background:rgba(255,255,255,.09);border-color:var(--line-strong)}',
      '@media (max-width:640px){.action-dialog{width:calc(100vw - 20px);padding:16px}.action-dialog .action-approve{flex-basis:100%}.action-dialog .action-cancel,.action-dialog .action-close{flex:1 1 0}}'
    ].join('');
    document.head.appendChild(style);
    var dialog = document.createElement("dialog");
    dialog.className = "action-dialog";
    dialog.innerHTML = '<h2></h2><p class="action-cwd"></p><pre class="action-command"></pre><p class="action-status" role="status"></p><pre class="action-output"></pre><div class="action-acts"><button class="action-approve"></button><button class="action-cancel"></button><button class="action-close"></button></div>';
    document.body.appendChild(dialog);
    var current = null, records = {}, timers = {};
    try { records = JSON.parse(localStorage.getItem('maestro_action_feedback') || '{}') || {}; } catch(e){}
    function actionKey(chatId, code){ return JSON.stringify([chatId, code]); }
    function active(a){ return a.status === 'starting' || a.status === 'running'; }
    function syncButtons(){
      if (!state.currentChat) return;
      document.querySelectorAll('#chatScroll .turn.assistant:not(.live-turn) [data-code-action]').forEach(function(button){
        var item = codeStore[button.getAttribute('data-code-action')];
        var a = item && records[actionKey(state.currentChat.id, item.code)];
        if (!a || button.dataset.actionState === a.status) return;
        button.dataset.actionState = a.status;
        var labels = {pending:ui('Autorizar e avançar','Authorize and proceed'), starting:ui('A iniciar…','Starting…'), running:ui('A executar…','Running…'), succeeded:ui('Concluído · Ver resultado','Completed · View result'), failed:ui('Falhou · Ver detalhes','Failed · View details'), cancelled:ui('Cancelado','Cancelled'), expired:ui('Autorização expirada','Approval expired')};
        button.querySelector('.code-authorize-ico').textContent = active(a) ? '◌' : a.status === 'succeeded' ? '✓' : a.status === 'failed' ? '!' : '⚡';
        button.querySelector('span:last-child').textContent = labels[a.status] || a.status;
        button.setAttribute('aria-busy', String(active(a)));
      });
    }
    function remember(a){
      records[actionKey(a.chat_id, a.code)] = {id:a.id, chat_id:a.chat_id, code:a.code, status:a.status};
      var keys = Object.keys(records);
      keys.slice(0, Math.max(0, keys.length - 100)).forEach(function(k){ delete records[k]; });
      try { localStorage.setItem('maestro_action_feedback', JSON.stringify(records)); } catch(e){}
      syncButtons();
      clearTimeout(timers[a.id]);
      if (active(a)) timers[a.id] = setTimeout(function(){ refresh(a.id); }, 1500);
    }
    function refresh(id){
      api('/api/actions/status', {action_id:id}).then(function(a){
        remember(a);
        if (current && current.id === a.id && dialog.open) render(a);
      }).catch(function(){ timers[id] = setTimeout(function(){ refresh(id); }, 2500); });
    }
    // Chat polling replaces DOM nodes and code ids; restore feedback from command identity.
    new MutationObserver(syncButtons).observe(document.getElementById('chatScroll'), {childList:true, subtree:true});
    Object.keys(records).forEach(function(k){ refresh(records[k].id); });
    try {
      var lastAction = localStorage.getItem('maestro_last_action');
      if (lastAction && !Object.keys(records).some(function(k){ return records[k].id === lastAction; })) refresh(lastAction);
    } catch(e){}
    var approve = dialog.querySelector('.action-approve');
    var cancel = dialog.querySelector('.action-cancel');
    dialog.querySelector('h2').textContent = ui('Executar ação no NAS', 'Run action on NAS');
    approve.textContent = ui('Autorizar e avançar', 'Authorize and proceed');
    cancel.textContent = ui('Cancelar ação', 'Cancel action');
    dialog.querySelector('.action-close').textContent = ui('Fechar', 'Close');
    function render(a){
      current = a;
      remember(a);
      dialog.querySelector('.action-cwd').textContent = ui('Pasta: ', 'Directory: ') + a.cwd;
      dialog.querySelector('.action-command').textContent = a.code;
      var names = {pending:ui('Aguarda autorização para este comando.', 'Awaiting approval for this command.'), starting:ui('A iniciar…', 'Starting…'), running:ui('A executar…', 'Running…'), succeeded:ui('Concluído', 'Completed'), failed:ui('Falhou', 'Failed'), cancelled:ui('Cancelado', 'Cancelled'), expired:ui('Autorização expirada', 'Approval expired')};
      dialog.querySelector('.action-status').textContent = (names[a.status] || a.status) + (a.exit_code !== undefined ? ' · exit ' + a.exit_code : '') + (a.error ? ' · ' + a.error : '');
      dialog.querySelector('.action-output').textContent = a.output || '';
      approve.hidden = cancel.hidden = a.status !== 'pending';
      approve.disabled = cancel.disabled = false;
      approve.removeAttribute('aria-busy');
      approve.textContent = ui('Autorizar e avançar', 'Authorize and proceed');
      try { localStorage.setItem('maestro_last_action', a.id); } catch(e){}
    }
    function decide(yes){
      if (!current || current.status !== 'pending' || approve.disabled) return;
      approve.disabled = cancel.disabled = true;
      approve.setAttribute('aria-busy', 'true');
      approve.textContent = yes ? ui('A autorizar…', 'Authorizing…') : ui('A cancelar…', 'Cancelling…');
      api('/api/actions/decide', {action_id:current.id, approve:yes}).then(function(a){
        render(a);
        if (active(a) || a.status === 'succeeded' || a.status === 'cancelled') dialog.close();
      }).catch(function(e){
        approve.removeAttribute('aria-busy');
        approve.textContent = ui('Autorizar e avançar', 'Authorize and proceed');
        dialog.querySelector('.action-status').textContent = e.message;
        approve.disabled = cancel.disabled = false;
      });
    }
    approve.onclick = function(){ decide(true); };
    cancel.onclick = function(){ decide(false); };
    dialog.querySelector('.action-close').onclick = function(){ dialog.close(); };
    document.getElementById('chatScroll').addEventListener('click', function(e){
      var button = e.target.closest('[data-code-action]');
      if (!button || !button.closest('.turn.assistant:not(.live-turn)')) return;
      var item = codeStore[button.getAttribute('data-code-action')];
      if (!item || !state.currentChat) return;
      button.disabled = true;
      api('/api/actions/preview', {chat_id:state.currentChat.id, code:item.code}).then(function(a){
        render(a); dialog.showModal();
      }).catch(function(e){ notifyUser('Maestro', e.message); }).finally(function(){ button.disabled = false; });
    });
  })();

  window.__maestroState = state;
  /* ---- RIGHT INSPECTOR ---- */
  function setInspTab(key){
    state.inspTab = key;
    if (key === "fleet" && !state.orchestration && !state.orchestrationLoading && !state.orchestrationError) {
      renderInspector();
      loadOrchestration();
      return;
    }
    renderInspector();
  }

  document.querySelectorAll(".insp-tab").forEach(function(tab){
    tab.onclick = function(){ setInspTab(tab.getAttribute("data-itab")); };
  });

  /* Tabs a sério: setas, Home/End e um único tab stop (padrão ARIA). */
  (function initInspectorKeys(){
    var list = document.querySelector(".insp-tabs");
    if (!list) return;
    list.addEventListener("keydown", function(e){
      var tabs = Array.prototype.slice.call(list.querySelectorAll(".insp-tab"));
      var idx = tabs.indexOf(document.activeElement);
      if (idx < 0) idx = tabs.map(function(t){ return t.getAttribute("data-itab"); }).indexOf(state.inspTab);
      var next = idx;
      if (e.key === "ArrowRight" || e.key === "ArrowDown") next = (idx + 1) % tabs.length;
      else if (e.key === "ArrowLeft" || e.key === "ArrowUp") next = (idx - 1 + tabs.length) % tabs.length;
      else if (e.key === "Home") next = 0;
      else if (e.key === "End") next = tabs.length - 1;
      else return;
      e.preventDefault();
      if (next < 0) next = 0;
      tabs[next].focus();
      setInspTab(tabs[next].getAttribute("data-itab"));
    });
  })();

  /* ---- QUOTAS: UMA SÓ LINGUAGEM (BATERIA QUE RESTA) ---- */
  function remainingPct(used) {
    var n = Number(used);
    if (used === null || used === undefined || used === "" || !isFinite(n)) return null;
    return Math.max(0, Math.min(100, 100 - n));
  }
  function quotaCls(rem, limited) {
    if (limited || rem === null || rem <= 0) return "bad";
    if (rem < 25) return "bad";
    if (rem < 50) return "warn";
    return "ok";
  }
  function battHtml(used, limited) {
    var rem = remainingPct(used);
    if (rem === null) return '<span class="quota-batt"><span class="batt-ico">🔋</span>sem dados</span>';
    var cls = quotaCls(rem, limited);
    var body = rem <= 0
      ? "resta <b>0%</b>"
      : (limited ? "<b>limite atingido</b>" : "resta <b>" + Math.round(rem) + "%</b>");
    return '<span class="quota-batt ' + cls + '"><span class="batt-ico">🔋</span>' + body + '</span>';
  }
  function restText(used) {
    var rem = remainingPct(used);
    return rem === null ? "?" : String(Math.round(rem));
  }
  function quotaWindowMeter(mainUsed, mainWindow, otherUsed, otherWindow, reset) {
    var main = remainingPct(mainUsed), other = remainingPct(otherUsed);
    var label = otherWindow + ': ' + (other === null ? ui('sem leitura', 'no reading') : ui('resta ', 'remaining ') + Math.round(other) + '%');
    var detail = ui('Reset ', 'Reset ') + otherWindow + ': ' + (reset || ui('sem leitura', 'no reading'));
    return '<button type="button" class="quota-window-meter" aria-expanded="false" aria-label="' + esc(mainWindow + ': ' + restText(mainUsed) + '%. ' + label + '. ' + detail) + '">' +
      '<span class="meter-track battery"><span class="meter-fill ' + quotaCls(main, false) + '" style="width:' + (main === null ? 0 : main) + '%"></span>' +
      '<span class="meter-window-overlay"><span class="meter-window-fill ' + quotaCls(other, false) + '" style="width:' + (other === null ? 0 : other) + '%"></span></span></span>' +
      '<span class="meter-window-details"><span class="meter-window-face primary"><span class="meter-window-label">' + esc(mainWindow + ': ' + (main === null ? ui('sem leitura', 'no reading') : ui('resta ', 'remaining ') + Math.round(main) + '%')) + '</span><span class="meter-window-reset">' + esc(ui('Passa o rato ou toca para ver ', 'Hover or tap to view ') + otherWindow) + '</span></span>' +
      '<span class="meter-window-face alternate"><span class="meter-window-label">' + esc(label) + '</span><span class="meter-window-reset">' + esc(detail) + '</span></span></span></button>';
  }
  function clockText(ts) {
    if (!ts) return "—";
    try { return new Date(ts).toLocaleTimeString("pt-PT", { hour: "2-digit", minute: "2-digit", second: "2-digit" }); }
    catch (e) { return "—"; }
  }
  function fleetQuotaLine(key) {
    var st = state.status || {};
    var a = st.antigravity || {}, c = st.claude || {}, x = st.codex || {}, d = st.deepseek || {};
    if (key === "agy") return "Resta " + restText(a.gemini_used_pct) + "% · reset " + (a.gemini_reset_fmt || "livre");
    if (key === "claude") return "Resta 5h " + restText(c.five_hour_pct) + "% · 7d " + restText(c.seven_day_pct) + "% · " + (c.seven_day_reset_fmt || "—");
    if (key === "codex") return "Resta 5h " + restText(x.five_hour_est_pct) + "% · 7d " + restText(x.seven_day_pct) + "% · " + (x.five_hour_reset_fmt || "—");
    if (key === "deepseek") return "Saldo " + (d.balance_text || "—");
    return "";
  }

  function currentTurnInfo() {
    var live = !!(state.activeJob && state.currentChat &&
      state.activeJob.chat_id === state.currentChat.id &&
      (state.activeJob.status || "running") === "running");
    var plan = live ? (state.activeJob.plan || null) : null;
    if (!plan && state.currentChat && state.currentChat.messages) {
      for (var i = state.currentChat.messages.length - 1; i >= 0; i--) {
        var m = state.currentChat.messages[i];
        if (m && m.role === "assistant" && m.plan) { plan = m.plan; break; }
      }
    }
    return { live: live, plan: plan || {} };
  }

  function mtEngineKey(engine) {
    var e = String(engine || "").toLowerCase();
    if (e === "agy" || e.indexOf("gemini") === 0 || e.indexOf("antigravity") === 0) return "agy";
    if (e === "codex" || e.indexOf("gpt-") === 0) return "codex";
    if (e === "claude" || e.indexOf("claude") === 0 || ["sonnet", "opus", "haiku", "fable"].indexOf(e) !== -1) return "claude";
    if (e.indexOf("deepseek-api") === 0) return "deepseek-api";
    if (e.indexOf("deepseek") === 0) return "deepseek";
    return "";
  }

  function mtOriginText(origin) {
    if (origin === "task_route") return ui("seleção automática por regras", "automatic rule selection");
    if (origin === "failover") return ui("substituição automática", "automatic failover");
    if (origin === "manual") return ui("escolha manual", "manual choice");
    return ui("sem turno nesta conversa", "no turn in this conversation");
  }

  function renderMaestroTable(orch) {
    if (!orch) {
      var msg = state.orchestrationError
        ? ui("A tabela não carregou. Não invento modelos nem quotas: tenta de novo.", "The table did not load. Models and quotas are not invented: try again.")
        : ui("A ler o catálogo real, as quotas e a hierarquia…", "Reading the real catalogue, quotas and hierarchy…");
      return '<div class="stat-card mt-card"><div class="stat-head"><span>🗂️ ' +
        ui("Tabela do Maestro", "Maestro table") + '</span></div><div class="stat-sub">' + msg + '</div>' +
        (state.orchestrationError ? '<button type="button" class="insp-refresh-btn" data-mt-retry>' +
          ui("Tentar de novo", "Try again") + '</button>' : '') + '</div>';
    }

    var turn = currentTurnInfo();
    var plan = turn.plan || {};
    var motorKey = mtEngineKey(plan.exec_engine);
    var motorTxt = plan.exec_engine_name || plan.recommended_engine || "";
    var counts = orch.counts || {};
    var maestro = orch.maestro || {};
    var maestroName = String(maestro.name || "").replace(/\s*\(Maestro Substituto[^)]*\)/, "").replace(/\s*\(Maestro Principal\)/, "");

    var now = '<div class="mt-now">' +
      '<div><span>👑 ' + ui("Maestro disponível", "Available Maestro") + '</span><b>' + esc(maestroName || "—") + '</b><small>' + esc(maestro.reason || "") + '</small></div>' +
      '<div><span>⚡ ' + (turn.live ? ui("Motor a executar", "Engine running") : ui("Motor do último turno", "Last turn engine")) + '</span><b>' + esc(motorTxt || ui("Auto (a decidir)", "Auto (to be decided)")) + '</b><small>' + esc(mtOriginText(plan.engine_origin)) + '</small></div>' +
      '<div><span>🧭 ' + ui("Decisão", "Decision") + '</span><b>' + ui("Maestro / coordenador", "Maestro / coordinator") + '</b><small>' + ui("divide, atribui e espera sem chamar o modelo.", "splits, assigns and waits without calling the model.") + '</small></div>' +
    '</div>' +
    '<div class="mt-facts">' +
      '<span>' + ui("até 6 tarefas", "up to 6 tasks") + '</span>' +
      '<span>' + ui("Até 4 agentes, conforme o pedido", "Up to 4 agents, according to the request") + '</span>' +
      '<span>' + ui("1 agente por plano; escrita isolada por projeto", "1 agent per plan; writes isolated per project") + '</span>' +
      '<span>' + ui("espera sem tokens", "waiting costs no tokens") + '</span>' +
      '<span>' + ui("retoma preserva concluídas", "resume keeps completed") + '</span>' +
    '</div>';

    now += '<details class="mt-roles"><summary>' + ui('Funções e tags', 'Roles and tags') + '</summary><div class="mt-role-grid">' + [
      ['maestro', ui('Coordena e distribui o trabalho.', 'Coordinates and assigns work.')],
      ['planner', ui('Função de planeamento: define a estratégia e dependências.', 'Planning function: defines strategy and dependencies.')],
      ['sentinel', ui('Triagem da fila: Haiku → DeepSeek Flash → espera local se ambos falharem.', 'Queue triage: Haiku → DeepSeek Flash → local wait if both fail.')],
      ['scout', ui('Investiga ficheiros e recolhe contexto.', 'Inspects files and gathers context.')],
      ['analysis', ui('Analisa problemas e propõe soluções.', 'Analyses problems and proposes solutions.')],
      ['implementation', ui('Implementa e valida alterações.', 'Implements and validates changes.')],
      ['review', ui('Revê o resultado e identifica falhas.', 'Reviews results and identifies issues.')],
      ['synthesis', ui('Sintetiza conclusões e resultados.', 'Synthesises findings and results.')]
    ].map(function(r){ return '<div>' + window.maestroRoleTag(r[0]) + '<span>' + r[1] + '</span></div>'; }).join('') + '</div><p>' +
      ui('Tags identificam funções, não modelos nem agentes adicionais. Um motor pode assumir várias funções. Os títulos automáticos usam DeepSeek, com alternativa local.', 'Tags identify functions, not models or extra agents. One engine can serve several roles. Automatic titles use DeepSeek with a local fallback.') + '</p></details>';

    var idx = 0;
    setTimeout(function(){ state._mtSeen = true; }, 1500);
    var rows = (orch.rows || []).map(function(r) {
      var isMotor = r.key === motorKey;
      var decision = r.is_maestro
        ? ui("É o Maestro de serviço: decide o plano, distribui e espera. Os restantes executam sob o seu amparo.", "This is the serving Maestro: it decides the plan, assigns and waits. The others execute under its guidance.")
        : r.decision;
      var chips = [];
      if (r.is_maestro) chips.push('<span class="mt-chip maestro">' + ui("MAESTRO", "MAESTRO") + '</span>');
      if (isMotor) chips.push('<span class="mt-chip motor">' + ui("MOTOR DESTE TURNO", "THIS TURN") + '</span>');
      chips.push('<span class="mt-chip ' + (r.available ? 'ok' : 'bad') + '">' +
        (r.available ? ui("DISPONÍVEL", "AVAILABLE") : (r.model_count ? ui("ESGOTADO", "EXHAUSTED") : ui("SEM CATÁLOGO", "NO CATALOGUE"))) + '</span>');

      var effortHead = r.effort_count
        ? (r.effort_count + ' ' + ui("esforços", "efforts"))
        : ui("esforço no ID/perfil", "effort in ID/profile");
      var modelList = r.models && r.models.length ? r.models.map(function(m) {
        var effort = (m.efforts && m.efforts.length) ? m.efforts.join(" · ") : ui("esforço no ID", "effort in ID");
        return '<div class="mt-model' + (m.available ? '' : ' off') + '">' +
          '<span class="mt-dot ' + (m.available ? 'ok' : 'bad') + '"></span>' +
          '<span class="mt-model-name">' + esc(m.label) + '</span>' +
          (m.read_only_only ? '<span class="mt-chip ghost">' + ui("só leitura", "read-only") + '</span>' : '') +
          '<div class="mt-purpose">' + esc(m.purpose || m.note || '') + '</div>' +
          '<div class="mt-eff">' + esc(effort) + '</div>' +
        '</div>';
      }).join("") : '<div class="mt-note">' + ui("Sem modelos confirmados pelo CLI; a linha não promete acesso.", "No models confirmed by the CLI; this row does not promise access.") + '</div>';

      var tasks = (r.active_tasks && r.active_tasks.length)
        ? '<div class="mt-tasks">' + r.active_tasks.map(function(t) {
            return '<div>▶ ' + esc(t.agent_label || ui("Agente", "Agent")) + ' · ' + esc(t.current_step || ui("a trabalhar", "working")) + '</div>';
          }).join("") + '</div>'
        : '';
      var tone = r.available ? 'ok' : 'bad';

      return '<article class="mt-ac ' + tone + (isMotor ? ' current' : '') + '" style="--i:' + idx++ + '">' +
        '<header class="mt-ac-head">' +
          '<span class="mt-dot big ' + tone + '"></span>' +
          '<b>' + esc(r.icon) + ' ' + esc(r.name) + '</b>' +
          '<span class="mt-rank">' + esc(r.rank) + '</span>' +
        '</header>' +
        '<div class="mt-role">' + esc(r.role) + '</div>' +
        '<div class="mt-chips">' + chips.join("") + '</div>' +
        '<div class="mt-quota"><span class="quota-chip ' + esc(r.quota.tone || "ok") + '">' + esc(r.quota.label) + '</span>' +
          (r.quota.reset ? '<span class="mt-note">' + ui("Reset: ", "Reset: ") + esc(r.quota.reset) + '</span>' : '') + '</div>' +
        tasks +
        '<details class="mt-more" data-model-group="' + esc(r.key) + '"><summary>' + ui("Detalhes", "Details") +
          ' · ' + r.model_count + ' ' + ui("modelos", "models") + ' · ' + effortHead + '</summary>' +
          '<div class="mt-decision">🧭 ' + esc(decision) + '</div>' +
          (r.quota.detail ? '<div class="mt-note">' + esc(r.quota.detail) + (r.quota.source ? ' (' + esc(r.quota.source) + ')' : '') + '</div>' : '') +
          '<div class="mt-model-list">' + modelList + '</div>' +
        '</details>' +
      '</article>';
    }).join("");

    var policy = (orch.policy || []).map(function(p) {
      return '<tr><td data-l="' + ui("Decisão", "Decision") + '"><b>' + esc(p.title) + '</b></td><td data-l="' + ui("Regra aplicada", "Applied rule") + '">' + esc(p.rule) + '</td></tr>';
    }).join("");

    var notes = orch.notes || {};
    var noteRows = [];
    ["catalogue", "read_only", "vision"].forEach(function(k) {
      if (notes[k]) noteRows.push('<div class="mt-note">' + esc(notes[k]) + '</div>');
    });
    if (notes.missing && notes.missing.length) {
      noteRows.push('<div class="quota-note">⚠️ ' + ui("Catálogo incompleto: ", "Incomplete catalogue: ") + esc(notes.missing.join(" · ")) + '</div>');
    }

    return '<div class="stat-card mt-card">' +
      '<div class="stat-head"><span>🗂️ ' + ui("Tabela do Maestro", "Maestro table") + '</span>' +
        '<span class="quota-chip ' + ((counts.available_models || 0) > 0 ? 'ok' : 'bad') + '">' +
          (counts.available_models || 0) + '/' + (counts.models || 0) + ' ' + ui("modelos disponíveis", "models available") +
        '</span></div>' +
      '<div class="stat-sub">' + ui("Quem decide, quem executa e com que modelos. Quota lida agora.", "Who decides, who executes and with which models. Quota read live.") + '</div>' +
      now +
      '<div class="mt-grid' + (state._mtSeen ? ' seen' : '') + '">' + rows + '</div>' +
      '<details class="rules-details">' +
        '<summary>🧭 ' + ui("Como o Maestro distribui, espera e retoma", "How Maestro splits, waits and resumes") + '</summary>' +
        '<div class="rich-table-wrap"><table class="rich-table mt-policy">' +
          '<thead><tr><th>' + ui("Decisão", "Decision") + '</th><th>' + ui("Regra aplicada", "Applied rule") + '</th></tr></thead>' +
          '<tbody>' + policy + '</tbody>' +
        '</table></div>' +
        noteRows.join("") +
      '</details>' +
    '</div>';
  }

  function renderInspector() {
    syncComposerControls();
    var box = document.getElementById("inspBody");
    if (!box) return;
    document.querySelectorAll(".insp-tab").forEach(function(tab){
      var selected = tab.getAttribute("data-itab") === state.inspTab;
      tab.classList.toggle("active", selected);
      tab.setAttribute("aria-selected", String(selected));
      tab.tabIndex = selected ? 0 : -1;
      if (selected) box.setAttribute("aria-labelledby", tab.id);
    });
    if (state.inspTab === "quotas") {
      var refreshed = state.statusAt ? clockText(state.statusAt) : "—";
      var staleNote = (state.statusError && state.status)
        ? '<div class="quota-note">⚠️ Sem resposta do servidor. A mostrar a última leitura válida das ' + esc(refreshed) + '.</div>'
        : '';

      function bindQuotaRefresh(label) {
        var rq = document.getElementById("btnRefreshQuotas");
        if (!rq) return;
        rq.disabled = !!state.quotaRefreshing;
        rq.setAttribute("aria-busy", state.quotaRefreshing ? "true" : "false");
        rq.textContent = state.quotaRefreshing
          ? "⏳ A ler APIs de quota…"
          : (label || "🔄 Atualizar quotas agora");
        rq.onclick = function(){
          if (state.quotaRefreshing) return;
          state.quotaRefreshing = true;
          renderInspector();
          Promise.all([loadStatusAndPulse({ force: true }), loadOrchestration({ force: true })]).then(function(){
            state.quotaRefreshing = false;
            renderInspector();
          });
        };
      }

      if (!state.status) {
        box.innerHTML =
          '<div class="stat-card">' +
            '<div class="stat-head"><span>' + (state.statusError ? "⚠️ Quotas indisponíveis" : "⏳ A ler quotas…") + '</span></div>' +
            '<div class="stat-sub">' + (state.statusError
              ? "O servidor não respondeu a /api/status. Não mostro 0% para não dar a ideia errada de que está tudo cheio."
              : "A consultar as APIs OAuth e o CLI. As barras aparecem dentro de instantes.") + '</div>' +
            '<button type="button" class="insp-refresh-btn" id="btnRefreshQuotas">🔄 Tentar de novo</button>' +
          '</div>';
        bindQuotaRefresh("🔄 Tentar de novo");
        return;
      }

      var a = state.status.antigravity || {}, c = state.status.claude || {}, x = state.status.codex || {}, d = state.status.deepseek || {};
      var am = state.status.active_maestro || {};

      /* Gemini / Antigravity */
      var gUsedRaw = a.gemini_used_pct;
      var gUsed = (gUsedRaw == null || !isFinite(Number(gUsedRaw))) ? null : Math.round(Number(gUsedRaw) * 10) / 10;
      var gRem = remainingPct(gUsedRaw);
      var gLimited = !!(a.limited || (gRem !== null && gRem <= 0));
      var gCls = quotaCls(gRem, gLimited);

      /* Claude Max */
      var c5raw = c.five_hour_pct, c7raw = c.seven_day_pct, cFraw = c.fable_pct;
      var cRem = remainingPct(c7raw);
      var cLimited = !!(c.limited || (cRem !== null && cRem <= 0));
      var cResetFmt = c.seven_day_reset_fmt || c.seven_day_reset || "Quinta 20:00";
      var pace = Math.round(c.pace_target_pct || 0);

      /* Codex */
      var x5raw = x.five_hour_est_pct, x7raw = x.seven_day_pct;
      var xRem = remainingPct(x5raw);
      var xLimited = !!(x.limit_reached || (xRem !== null && xRem <= 0));
      var xResetFmt = x.five_hour_reset_fmt || "Janela 5h";

      var amName = String(am.name || "Antigravity").replace(/\s*\(Maestro Substituto[^)]*\)/, "").replace(/\s*\(Maestro Principal\)/, "");
      var chatEng = document.getElementById("selEngine");
      var curEngKey = (state.currentChat && state.currentChat.engine) || (chatEng ? chatEng.value : "auto");
      var engLabels = { auto: "⚡ Auto (orquestra)", agy: "🧠 Gemini / Antigravity", sonnet: "🎭 Claude Sonnet", "claude-sonnet": "🎭 Claude Sonnet", opus: "👑 Claude Opus", "claude-opus": "👑 Claude Opus", codex: "⚙️ Codex", deepseek: "🔮 DeepSeek V3.2" };
      var chatEngTxt = engLabels[curEngKey] || (chatEng && chatEng.selectedOptions && chatEng.selectedOptions[0] ? chatEng.selectedOptions[0].textContent.trim() : "Auto");
      /* Último plano conhecido: turno ao vivo > última resposta da conversa.
         É a única forma de o painel não contradizer o cartão "Motor · …". */
      var liveTurn = !!(state.activeJob && state.currentChat && state.activeJob.chat_id === state.currentChat.id && state.activeJob.status === "running");
      var planNow = liveTurn ? state.activeJob.plan : null;
      if (!planNow && state.currentChat && state.currentChat.messages) {
        for (var _pi = state.currentChat.messages.length - 1; _pi >= 0; _pi--) {
          var _pm = state.currentChat.messages[_pi];
          if (_pm && _pm.role === "assistant" && _pm.plan) { planNow = _pm.plan; break; }
        }
      }
      var motorKey = planNow ? (planNow.exec_engine || "") : "";
      var motorTxt = planNow ? (planNow.exec_engine_name || engLabels[motorKey] || motorKey) : "";
      var motorOrigin = planNow ? String(planNow.engine_origin || "") : "";
      var amRoleTxt = motorKey && motorKey === am.engine
        ? (liveTurn ? "Este modelo está a executar o pedido." : "Este modelo executou o último turno.")
        : "Disponível na hierarquia de substituição; sem participação confirmada neste turno.";

      box.innerHTML =
        '<div class="quota-legenda">🔋 <span>Uma só leitura: as barras enchem com a <b>quota que resta</b> (100% = cheio). <span class="quota-updated">Última leitura: ' + esc(refreshed) + '</span></span></div>' +
        staleNote +
        '<div class="stat-card">' +
          '<div class="stat-head"><span>👑 Maestro disponível</span><span class="quota-chip ' + (am.is_substitute ? 'warn' : 'ok') + '">' + (am.is_substitute ? "SUBSTITUTO" : "PRINCIPAL") + '</span></div>' +
          '<div class="maestro-name">' + esc(amName) + '</div>' +
          '<div class="stat-sub">' + esc(am.reason || "Substituto automático configurado.") + '</div>' +
          '<div class="stat-sub" style="opacity:.75">' + esc(amRoleTxt) + '</div>' +
          '<div class="stat-sub">Nesta conversa: <b>' + esc(chatEngTxt) + (curEngKey === "auto" && motorTxt ? " → " + esc(motorTxt) : "") + '</b></div>' +
          (motorTxt ? '<div class="stat-sub">' + (liveTurn ? 'A executar agora: ' : 'Motor do último turno: ') + '<b>' + esc(motorTxt) + '</b>' + (motorOrigin === "task_route" ? ' <span style="opacity:.75">(seleção automática por regras)</span>' : motorOrigin === "failover" ? ' <span style="opacity:.75">(substituição automática)</span>' : '') + '</div>' : '') +
        '</div>' +
        '<div class="stat-card' + (gLimited ? ' quota-exhausted' : '') + '">' +
          '<div class="stat-head"><span>♊ Gemini / Antigravity</span>' + battHtml(gUsedRaw, a.limited) + '</div>' +
          '<div class="meter-track battery"><div class="meter-fill ' + gCls + '" style="width:' + (gRem == null ? 0 : gRem) + '%"></div></div>' +
          '<div class="stat-sub">' +
            (gUsed == null ? "sem leitura de quota" : '<span class="quota-used">' + gUsed + '% usado</span>' + (gLimited ? ' · <span class="quota-chip bad">ESGOTADO</span>' : '')) +
            ' · Reset real: <b>' + esc(a.gemini_reset_fmt || "Livre") + '</b>' +
            '<br>3P OAuth (projeto, não fiável): 5h resta <b>' + restText(a.models_3p_5h_used_pct) + '%</b> · 7d resta <b>' + restText(a.models_3p_7d_used_pct) + '%</b>' +
          '</div>' +
          (a.quota_note ? '<div class="quota-note">⚠️ ' + esc(a.quota_note) + '</div>' : '') +
          (a.quota_source ? '<div class="quota-src">fonte: ' + esc(a.quota_source) + '</div>' : '') +
        '</div>' +
        '<div class="stat-card' + (cLimited ? ' quota-exhausted' : '') + '">' +
          '<div class="stat-head"><span>🎭 Claude Max</span>' + battHtml(c7raw, c.limited) + '</div>' +
          quotaWindowMeter(c7raw, '7d', c5raw, '5h', c.five_hour_reset_fmt) +
          '<div class="stat-sub">5h: resta <b>' + restText(c5raw) + '%</b> · 7d: resta <b>' + restText(c7raw) + '%</b> · Fable: resta <b>' + restText(cFraw) + '%</b>' +
            '<br>Usado 7d: <b>' + (c7raw == null ? "?" : Math.round(c7raw) + '%') + '</b> (ideal ≤' + pace + '%) · Reset 7d: <b>' + esc(cResetFmt) + '</b>' +
            (cLimited ? ' · <span class="quota-chip bad">ESGOTADO</span>' : '') +
          '</div>' +
        '</div>' +
        '<div class="stat-card' + (xLimited ? ' quota-exhausted' : '') + '">' +
          '<div class="stat-head"><span>⚙️ Codex Plus (5h)</span>' + battHtml(x5raw, x.limit_reached) + '</div>' +
          quotaWindowMeter(x5raw, '5h', x7raw, '7d', x.seven_day_reset_fmt) +
          '<div class="stat-sub">5h: resta <b>' + restText(x5raw) + '%</b> · 7d: resta <b>' + restText(x7raw) + '%</b>' +
            '<br>Usado 5h: <b>' + (x5raw == null ? "?" : Math.round(x5raw) + '%') + '</b> · Reset 5h: <b>' + esc(xResetFmt) + '</b>' +
            '<br>' + ui('Orçamento diário: ', 'Daily budget: ') + '14.3% · ' + ui('Teto acumulado: ', 'Cumulative allowance: ') + (x.pace_target_pct == null ? '—' : Number(x.pace_target_pct).toFixed(1) + '%') +
            ' · ' + ui('Usado 7d: ', 'Used 7d: ') + (x7raw == null ? '—' : Number(x7raw).toFixed(1) + '%') +
            '<br>' + ui('Consumo observado hoje: ', 'Observed daily usage: ') + (x.daily_used_pct == null ? '—' : Number(x.daily_used_pct).toFixed(1) + '%') + (x.daily_measurement_partial ? ui(' (leitura parcial)', ' (partial reading)') : '') +
            (x.pace_blocked ? '<br><span class="quota-chip bad">' + ui('RESERVADO · orçamento protegido', 'RESERVED · budget protected') + '</span>' : '') +
            (xLimited ? ' · <span class="quota-chip bad">ESGOTADO</span>' : '') +
          '</div>' +
        '</div>' +
        '<div class="stat-card">' +
          '<div class="stat-head"><span>🔮 DeepSeek API</span><span style="color:var(--green)">' + esc(d.balance_text || "Sem leitura") + '</span></div>' +
          '<div class="stat-sub">Saldo em dólares — a API não expõe percentagem de quota, por isso não há barra inventada.</div>' +
        '</div>' +
        '<div class="quota-legenda">🔋 100% = cheio · 0% = esgotado. A barra de cada modelo mostra 7d no Claude e 5h no Codex; passa o rato ou toca para ver a outra janela.</div>' +
        '<button type="button" class="insp-refresh-btn" id="btnRefreshQuotas">🔄 Atualizar quotas agora</button>' +
        '<button type="button" class="insp-refresh-btn" id="btnOpenMaestroTable">🗂️ ' + ui("Ver tabela do Maestro (quem decide / modelos)", "View Maestro table (who decides / models)") + '</button>';
      bindQuotaRefresh();
      box.querySelectorAll('.quota-window-meter').forEach(function(meter){
        meter.onclick = function(){ meter.setAttribute('aria-expanded', String(meter.getAttribute('aria-expanded') !== 'true')); };
        meter.onkeydown = function(e){ if (e.key === 'Escape') { meter.setAttribute('aria-expanded', 'false'); meter.blur(); } };
      });
      var mtOpen = document.getElementById("btnOpenMaestroTable");
      if (mtOpen) mtOpen.onclick = function(){ setInspTab("fleet"); };
      return;
    }

    if (state.inspTab === "pulso") {
      var items = (state.pulse || {}).items || [];
      var pulseErrors = (state.pulse || {}).errors || [];
      if (!items.length) {
        box.innerHTML = '<div class="stat-card">✅ Sem alertas no Pulso!</div>';
        return;
      }
      var urgent = items.filter(function(it){ return ["alert","err","warn"].indexOf(it.level) !== -1; });

      function buildPulsePrompt(sel, analyze) {
        var lines = sel.map(function(it, idx){
          return (idx + 1) + ". [" + String(it.level || "info").toUpperCase() + "] " + it.app + " — " + it.text;
        });
        if (analyze) {
          return "🧠 Analisa o Pulso do Pavilion-NAS e dá-me um plano priorizado. Para cada alerta diz: causa provável, impacto, correção concreta e se é seguro aplicar já.\n\n" + lines.join("\n");
        }
        return "⚡ Resolve os seguintes alertas do Pulso no Pavilion-NAS. Para cada um: diagnostica a causa, aplica a correção no sistema (Docker/Radarr/Sonarr/HA/scripts), valida o resultado e diz-me o que mudou. Não peças confirmação para passos reversíveis.\n\n" + lines.join("\n");
      }
      function launchPulse(sel, analyze) {
        if (!sel.length || state.pulseLaunching) return;
        state.pulseLaunching = true;
        var prompt = buildPulsePrompt(sel, analyze);
        // Pulso is a separate NAS task, even when the selected chat is busy.
        persistDraft(true);
        return api("/api/chat/new", { project: "nas", engine: "auto" }).then(function(d){
          if (!d.chat || !d.chat.id) throw new Error("Conversa não criada");
          return openChat(d.chat.id).then(function(){
            if (!state.currentChat || state.currentChat.id !== d.chat.id) {
              throw new Error("Conversa não aberta");
            }
          });
        }).then(function(){
          state.archiveView = false;
          state.pendingImages = [];
          state.pendingDocs = [];
          var input = document.getElementById("promptInput");
          input.value = "";
          input.style.height = "auto";
          renderAttachStrip();
          setModule("chat");
          closeMobileDrawers();
          refreshChats();
          input.focus();
          return dispatchChatTurn(prompt, [], "nas", "auto", false, []);
        }).catch(function(){
          notifyUser("Pulso", "Não foi possível abrir a nova conversa. Tenta novamente.");
        }).finally(function(){ state.pulseLaunching = false; });
      }
      box.innerHTML =
        '<div class="pulse-cmd">' +
          '<div class="pulse-cmd-info"><b>' + items.length + '</b> alertas · <span class="pulse-urgent-n">' + urgent.length + ' urgentes</span></div>' +
          '<div class="pulse-cmd-acts">' +
            '<button type="button" class="pulse-act urgent" data-pact="urgent"' + (urgent.length ? '' : ' disabled') + '>⚡ Resolver urgentes (' + urgent.length + ')</button>' +
            '<button type="button" class="pulse-act" data-pact="all">📋 Resolver todos (' + items.length + ')</button>' +
            '<button type="button" class="pulse-act analyze" data-pact="analyze">🧠 Analisar Pulso</button>' +
          '</div>' +
          '<div class="pulse-cmd-hint">Marca as caixas para resolver só os que escolheres.</div>' +
        '</div>' +
        (pulseErrors.length ? '<div class="quota-note">⚠️ Fontes com erro: ' + esc(pulseErrors.join(" · ")) + '</div>' : '') +
        items.map(function(it, idx){
          var lvl = String(it.level || "info");
          var badge = lvl === "alert" ? "err" : (lvl === "live" ? "ok" : lvl);
          return (
            '<div class="pulse-card ' + esc(badge) + '">' +
              '<div class="pulse-card-head">' +
                '<label class="pulse-sel"><input type="checkbox" data-psel="' + idx + '"><span>[' + esc(lvl.toUpperCase()) + ']</span></label>' +
                '<strong style="font-size:0.76rem">' + esc(it.app) + '</strong>' +
                '<button type="button" class="round-tool-btn magic" data-pidx="' + idx + '">⚡ Resolver</button>' +
              '</div>' +
              '<div style="font-size:0.75rem;color:#e2e8f0">' + esc(it.text) + '</div>' +
            '</div>'
          );
        }).join("");
      function selectedPulse() {
        return Array.prototype.slice.call(box.querySelectorAll("[data-psel]:checked")).map(function(cb){
          return items[Number(cb.getAttribute("data-psel"))];
        }).filter(Boolean);
      }
      box.querySelectorAll("[data-pact]").forEach(function(b){
        b.onclick = function(){
          var act = b.getAttribute("data-pact");
          if (act === "urgent") return launchPulse(urgent);
          if (act === "analyze") return launchPulse(items, true);
          var sel = selectedPulse();
          launchPulse(sel.length ? sel : items);
        };
      });
      box.querySelectorAll("[data-pidx]").forEach(function(b){
        b.onclick = function(){
          var it = items[Number(b.getAttribute("data-pidx"))];
          if (it) launchPulse([it]);
        };
      });
      return;
    }

    if (state.inspTab === "git") {
      var info = state.projectInfo;
      if (!info) {
        box.innerHTML = '<div class="stat-card">A carregar ficheiros…</div>';
        loadProjectInspector(document.getElementById("selProject").value);
        return;
      }
      var rec = info.recent_files || [];
      box.innerHTML =
        '<div class="stat-card">' +
          '<div class="stat-head"><span>' + esc((info.project||{}).name || "Projeto") + '</span><span style="color:var(--cyan);font-family:var(--mono);font-size:0.7rem">🌿 ' + esc(info.git_branch || "—") + '</span></div>' +
          '<div class="stat-sub" style="font-family:var(--mono)">' + esc((info.project||{}).cwd || "") + '</div>' +
        '</div>' +
        rec.slice(0, 14).map(function(f){
          return '<div class="file-row" data-fpath="' + esc(f.path) + '"><span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + esc(f.rel) + '</span><span>🪟</span></div>';
        }).join("");
      box.querySelectorAll("[data-fpath]").forEach(function(row){
        row.onclick = function(){
          var fp = row.getAttribute("data-fpath");
          api("/api/project/file?path=" + encodeURIComponent(fp)).then(function(res){
            if (res.content !== undefined) openCanvas(res.name || fp, res.content, "code");
          });
        };
      });
      return;
    }

    if (state.inspTab === "fleet") {
      if (!state.status) {
        box.innerHTML = '<div class="stat-card">' + (state.statusError
          ? "⚠️ Frota indisponível: o servidor não respondeu. Os agentes não podem ser inventados."
          : "⏳ A ler a frota…") + '</div>';
        return;
      }
      var st = state.status || {};
      var a = st.antigravity || {}, c = st.claude || {}, x = st.codex || {}, d = st.deepseek || {};
      var fs = st.fleet_summary || {};
      var fleet = fs.fleet || [];
      var engMap = { agy: "agy", claude: "sonnet", codex: "codex", deepseek: "deepseek" };
      var c5 = Math.round(c.five_hour_pct || 0);
      var c7 = Math.round(c.seven_day_pct || 0);
      var x5 = Math.round(x.five_hour_est_pct || 0);
      var x7 = Math.round(x.seven_day_pct || 0);
      if (fleet.length) {
        var cards = fleet.map(function(f){
          var engine = engMap[f.key] || "auto";
          var act = document.getElementById("selEngine").value === engine;
          var tasks = f.active_tasks || [];
          var taskLine = tasks.length
            ? tasks.map(function(task){ return '<div class="fleet-task">▶ ' + esc(task.agent_label || ui("Em execução", "Running")) + ' · ' + esc(statusText(task.current_step || ui("a trabalhar", "working"))) + '</div>'; }).join('')
            : '';
          return '<div class="stat-card fleet-card" style="cursor:pointer;border-color:' + (act ? 'var(--cyan)' : 'var(--line)') + '" data-eng="' + engine + '" data-fleet-key="' + esc(f.key) + '" title="Ver o que este agente está a fazer">' +
            '<div class="stat-head"><span>' + esc(f.icon) + ' ' + esc(f.name) + '</span>' +
              '<span class="quota-chip ' + (f.available ? 'ok' : 'bad') + '">' + (f.available ? "LIVRE" : "ESGOTADO") + '</span></div>' +
            '<div class="stat-sub">' + esc(f.role || "") + '</div>' +
            '<div class="stat-sub fleet-quota">' + esc(fleetQuotaLine(f.key) || f.quota_label || "") + '</div>' +
            (tasks.length ? '<div class="stat-sub" style="color:var(--amber)">' + tasks.length + ' tarefa(s) ativa(s)</div>' : '') +
            taskLine +
          '</div>';
        }).join("");
        var table = renderMaestroTable(state.orchestration);
        var freeNames = fleet.filter(function(x){ return x.available; }).map(function(x){ return x.name; });
        var poolNote = freeNames.length <= 2
          ? '<div class="fleet-task" style="color:var(--amber)">⚠️ Só ' + freeNames.length + ' modelo(s) livre(s): ' + esc(freeNames.join(" + ") || "nenhum") + ' — o Maestro alterna entre eles e guarda o último para o fim.</div>'
          : '';
        if (!state.orchestration && !state.orchestrationLoading && !state.orchestrationError) loadOrchestration();
        var openGroups = Array.from(box.querySelectorAll('[data-model-group][open]')).map(function(el){ return el.getAttribute('data-model-group'); });
        var fleetScroll = box.scrollTop;
        box.innerHTML = '<div class="fleet-summary">🤖 ' + fleet.length + ' agentes · ' + (fs.available_count || 0) + ' livres agora</div>' + table + poolNote;
        box.querySelectorAll('[data-model-group]').forEach(function(el){ el.open = openGroups.indexOf(el.getAttribute('data-model-group')) !== -1; });
        box.scrollTop = fleetScroll;
        var mtRetry = box.querySelector("[data-mt-retry]");
        if (mtRetry) mtRetry.onclick = function(){ state.orchestrationError = false; loadOrchestration({ force: true }); };
        box.querySelectorAll("[data-eng]").forEach(function(card){
          card.onclick = function(){ document.getElementById("selEngine").value = card.getAttribute("data-eng"); renderInspector(); };
        });
      } else {
        box.innerHTML = '<div class="stat-card">A carregar frota…</div>';
      }
    }
  }

  function loadStatusAndPulse(opts) {
    opts = opts || {};
    if (state.statusLoading && !opts.force) return Promise.resolve();
    state.statusLoading = true;

    /* Quotas e Pulso falham em separado: uma API em baixo não pode apagar a
       outra, e uma falha de rede nunca deve transformar-se em "0% usado". */
    var pStatus = api("/api/status").then(function(v){
      if (v && typeof v === "object") {
        state.status = v;
        state.statusAt = Date.now();
        state.statusError = false;
      }
    }, function(){
      state.statusError = true;
    });

    var pPulse = api("/api/pulse").then(function(v){
      state.pulse = v || { items: [] };
      state.pulseError = false;
      var items = (state.pulse.items || []);
      var errs = items.filter(function(i){ return i.level === "err" || i.level === "warn"; });
      var badge = document.getElementById("headerPulseBadge");
      if (badge) badge.textContent = errs.length ? "(" + errs.length + ")" : "";
      if (state.lastPulseErrCount !== null && errs.length > state.lastPulseErrCount && errs[0]) {
        notifyUser("⚠️ Alerta no Pulso · " + errs[0].app, errs[0].text);
      }
      state.lastPulseErrCount = errs.length;
    }, function(){
      state.pulseError = true;
    });

    return Promise.all([pStatus, pPulse]).then(function(){
      state.statusLoading = false;
      var box = document.getElementById("inspBody");
      var top = box ? box.scrollTop : 0;
      try {
        if (!document.hidden) renderInspector();
      } catch (err) {
        if (window.console && console.error) console.error("renderInspector", err);
      }
      if (box) box.scrollTop = top;
      return true;
    });
  }

  function loadOrchestration(opts) {
    opts = opts || {};
    if (state.orchestrationLoading) return state.orchestrationPromise || Promise.resolve();
    state.orchestrationLoading = true;
    var suffix = opts.force ? "?refresh=1" : "";
    var p = api("/api/orchestration" + suffix).then(function(v){
      if (v && Array.isArray(v.rows) && v.rows.length) {
        state.orchestration = v;
        state.orchestrationAt = Date.now();
        state.orchestrationError = false;
      }
    }, function(){
      state.orchestrationError = true;
    }).then(function(){
      state.orchestrationLoading = false;
      state.orchestrationPromise = null;
      if (state.inspTab === "fleet") {
        var box = document.getElementById("inspBody");
        var top = box ? box.scrollTop : 0;
        try { renderInspector(); } catch (err) { if (window.console && console.error) console.error("renderInspector", err); }
        if (box) box.scrollTop = top;
      }
      return state.orchestration;
    });
    state.orchestrationPromise = p;
    return p;
  }

  function loadProjectInspector(projKey) {
    api("/api/project/inspect?project=" + encodeURIComponent(projKey || "nas")).then(function(d){
      state.projectInfo = d;
      if (state.inspTab === "git") renderInspector();
    });
  }
  document.getElementById("selProject").onchange = function(e){ loadProjectInspector(e.target.value); };

  /* ---- CHAT LIST & CRUD ---- */
  function chatRelativeTime(ts) {
    var t = Number(ts) || 0;
    if (!t) return "";
    var diff = Math.max(0, Math.floor(Date.now() / 1000 - t));
    if (diff < 60) return ui("agora", "now");
    if (diff < 3600) return Math.floor(diff / 60) + " min";
    if (diff < 86400) return Math.floor(diff / 3600) + " h";
    if (diff < 172800) return ui("ontem", "yesterday");
    if (diff < 604800) return Math.floor(diff / 86400) + ui(" dias", " days");
    return new Date(t * 1000).toLocaleDateString();
  }

  var SCROLL_KEY = "maestro_scroll_v1";
  function scrollStore() {
    try { return JSON.parse(localStorage.getItem(SCROLL_KEY) || "{}") || {}; } catch (e) { return {}; }
  }
  function saveScrollNow() {
    var box = document.getElementById("chatScroll");
    if (!box || !state.currentChat) return;
    var m = scrollStore();
    var atBottom = distanceToBottom(box) <= SCROLL_BOTTOM_EPS;
    if (atBottom) delete m[state.currentChat.id];
    else m[state.currentChat.id] = { top: Math.round(box.scrollTop), h: box.scrollHeight };
    try { localStorage.setItem(SCROLL_KEY, JSON.stringify(m)); } catch (e) {}
  }
  var _scrollTimer = null;
  function restoreScrollFor(chatId) {
    var box = document.getElementById("chatScroll");
    if (!box) return;
    var v = chatId ? scrollStore()[chatId] : null;
    window.requestAnimationFrame(function () {
      if (v && typeof v.top === "number") {
        box.__follow = false;
        var maxTop = Math.max(0, box.scrollHeight - box.clientHeight);
        box.scrollTop = Math.min(v.top, maxTop);
      } else {
        box.__follow = true;
        scrollChatToBottom(true);
      }
      box.style.visibility = "";
    });
  }
  /* Imagens que acabam de carregar aumentam o chat: se estamos a seguir o fim, ficar no fim. */
  (function(){
    var b = document.getElementById("chatScroll");
    if (b) b.addEventListener("load", function(e){
      if (e.target && e.target.tagName === "IMG" && b.__follow !== false && !composerHasFocus()) { b.scrollTop = b.scrollHeight; }
    }, true);
  })();
  /* Altura real da janela (o 100dvh falha em PWAs Android e esconde o compositor). */
  function setAppH(){ document.documentElement.style.setProperty("--app-h", (window.innerHeight) + "px"); }
  setAppH();
  window.addEventListener("resize", setAppH);
  window.addEventListener("orientationchange", setAppH);
  window.addEventListener("pageshow", setAppH);
  if (window.visualViewport) window.visualViewport.addEventListener("resize", setAppH);

  function chatDayDiff(ts) {
    var d = new Date((ts || 0) * 1000), t = new Date();
    var a = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    var b = new Date(t.getFullYear(), t.getMonth(), t.getDate());
    return Math.round((b - a) / 86400000);
  }
  function chatBucket(c) {
    var n = chatDayDiff(c.updated_at);
    if (n <= 0) return { key: "today", label: ui("Hoje", "Today") };
    if (n === 1) return { key: "yday", label: ui("Ontem", "Yesterday") };
    if (n <= 7) return { key: "week", label: ui("Últimos 7 dias", "Last 7 days") };
    if (n <= 30) return { key: "month", label: ui("Este mês", "This month") };
    return { key: "older", label: ui("Mais antigas", "Older") };
  }
  function chatGroupsCollapsed() {
    try { return JSON.parse(localStorage.getItem("maestro_chat_groups") || "[]"); } catch (e) { return []; }
  }
  function toggleChatGroup(key) {
    var cur = chatGroupsCollapsed();
    var i = cur.indexOf(key);
    if (i === -1) cur.push(key); else cur.splice(i, 1);
    try { localStorage.setItem("maestro_chat_groups", JSON.stringify(cur)); } catch (e) {}
    renderChatList();
  }
  function renameConversation(chat) {
    closeContextMenu();
    if (document.querySelector('.rename-dialog')) return;
    var previous = document.activeElement;
    var dialog = document.createElement('dialog');
    dialog.className = 'rename-dialog';
    dialog.setAttribute('aria-labelledby', 'renameTitle');
    dialog.innerHTML = '<form><h3 id="renameTitle">' + ui('Mudar nome da conversa', 'Rename conversation') + '</h3>' +
      '<label for="renameInput">' + ui('Nome', 'Name') + '</label>' +
      '<input id="renameInput" name="title" maxlength="120" required autocomplete="off">' +
      '<p class="rename-error" role="alert"></p><div class="rename-actions"><button type="button" class="rename-cancel">' +
      ui('Cancelar', 'Cancel') + '</button><button type="submit">' + ui('Guardar', 'Save') + '</button></div></form>';
    document.body.appendChild(dialog);
    var input = dialog.querySelector('input'), submit = dialog.querySelector('[type=submit]');
    input.value = chat.title || '';
    dialog.querySelector('.rename-cancel').onclick = function(){ dialog.close(); };
    dialog.addEventListener('click', function(e){ if (e.target === dialog) {
      var r = dialog.getBoundingClientRect();
      if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) dialog.close();
    }});
    dialog.addEventListener('close', function(){ dialog.remove(); if (previous && previous.isConnected) previous.focus(); });
    dialog.querySelector('form').onsubmit = function(e){
      e.preventDefault();
      var title = input.value.trim();
      if (!title || submit.disabled) return;
      submit.disabled = true;
      api('/api/chat/rename', {id:chat.id, title:title}).then(function(d){
        if (state.currentChat && state.currentChat.id === chat.id) {
          state.currentChat.title = d.chat.title;
          state.currentChat.title_source = 'manual';
        }
        dialog.close();
        return refreshChats();
      }).catch(function(){
        dialog.querySelector('.rename-error').textContent = ui('Não foi possível guardar. Tenta novamente.', 'Could not save. Please try again.');
      }).finally(function(){ submit.disabled = false; });
    };
    dialog.showModal(); input.focus(); input.select();
  }

  function copyContextText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) return navigator.clipboard.writeText(text);
    var holder = document.createElement('textarea'); holder.value = text;
    holder.style.cssText = 'position:fixed;opacity:0;left:0;top:0';
    (document.querySelector('dialog[open]') || document.body).appendChild(holder);
    holder.select(); var copied = document.execCommand('copy'); holder.remove();
    if (!copied) throw new Error('Clipboard unavailable');
    return Promise.resolve();
  }
  var contextMenu = null, contextFocus = null;
  function closeContextMenu(restore) {
    if (contextMenu) contextMenu.remove();
    contextMenu = null;
    if (restore && contextFocus && contextFocus.isConnected) contextFocus.focus();
  }
  function showContextMenu(target, x, y) {
    closeContextMenu();
    contextFocus = document.activeElement;
    var menu = document.createElement('div');
    contextMenu = menu;
    menu.className = 'maestro-context-menu'; menu.setAttribute('role', 'menu');
    menu.setAttribute('aria-label', ui('Ações do Maestro', 'Maestro actions'));
    function add(label, icon, emoji, action, disabled) {
      var button = document.createElement('button'); button.type = 'button';
      button.setAttribute('role', 'menuitem'); button.disabled = !!disabled;
      button.innerHTML = dualIco(icon, emoji) + '<span>' + esc(label) + '</span>';
      button.onclick = function(){ closeContextMenu(); Promise.resolve().then(action).catch(function(){
        alert(ui('Não foi possível concluir a ação. Verifica as permissões da área de transferência.', 'Could not complete the action. Check clipboard permissions.'));
      }); };
      menu.appendChild(button);
    }
    var row = target.closest('.chat-row');
    var chat = row && state.chats.concat(state.archivedChats).find(function(c){ return c.id === row.dataset.chatId; });
    if (chat) {
      var heading = document.createElement('div'); heading.className = 'context-heading'; heading.textContent = chat.title; menu.appendChild(heading);
      add(ui('Abrir conversa', 'Open conversation'), 'chat', '💬', function(){ setModule('chat'); openChat(chat.id); closeMobileDrawers(); });
      add(ui('Mudar nome', 'Rename'), 'edit', '✏️', function(){ renameConversation(chat); });
      Array.from(row.querySelectorAll('.chat-row-actions > button')).slice(1).forEach(function(b){
        var icon = b.querySelector('[data-ico]');
        add(b.title, icon ? icon.dataset.ico : 'dots', b.querySelector('.ico-emoji').textContent, function(){ b.click(); }, b.disabled);
      });
      if (!chat.archived) add(ui('Mover para pasta…', 'Move to folder…'), 'folder', '📁', function(){
        showFolderContext(chat, target, x, y);
      });
    } else {
      var editable = target.closest('input, textarea, [contenteditable="true"]');
      var textInput = editable && (editable.tagName === 'TEXTAREA' || /^(text|search|url|email|tel|password)$/.test(editable.type));
      var start = textInput ? editable.selectionStart : null, end = textInput ? editable.selectionEnd : null;
      var selection = textInput && start !== null ? editable.value.slice(start, end) : window.getSelection().toString();
      var range = !textInput && window.getSelection().rangeCount ? window.getSelection().getRangeAt(0).cloneRange() : null;
      function restoreSelection(){
        editable.focus();
        if (textInput && start !== null) editable.setSelectionRange(start, end);
        else if (range) { var s = window.getSelection(); s.removeAllRanges(); s.addRange(range); }
      }
      if (editable && editable.type === 'password') selection = '';
      if (selection) add(ui('Copiar seleção', 'Copy selection'), 'copy', '📋', function(){ return copyContextText(selection); });
      if (editable) {
        if (selection && !editable.readOnly && editable.type !== 'password') add(ui('Cortar', 'Cut'), 'edit', '✂️', async function(){
          await copyContextText(selection); restoreSelection(); document.execCommand('delete');
        });
        if (!editable.readOnly && !editable.disabled) add(ui('Colar', 'Paste'), 'copy', '📋', async function(){
          if (!navigator.clipboard || !navigator.clipboard.readText) {
            restoreSelection();
            alert(ui('Usa Ctrl+V / ⌘V ou o teclado do dispositivo para colar aqui.', 'Use Ctrl+V / ⌘V or your device keyboard to paste here.'));
            return;
          }
          var text = await navigator.clipboard.readText(); restoreSelection(); document.execCommand('insertText', false, text);
        });
        add(ui('Selecionar tudo', 'Select all'), 'copy', '▤', function(){
          editable.focus(); if (textInput) editable.select(); else { var r = document.createRange(); r.selectNodeContents(editable); var s = window.getSelection(); s.removeAllRanges(); s.addRange(r); }
        });
      }
      var link = target.closest('a[href]');
      if (link && /^https?:/.test(link.href)) {
        add(ui('Abrir ligação', 'Open link'), 'globe', '🌐', function(){ window.open(link.href, '_blank', 'noopener'); });
        add(ui('Copiar ligação', 'Copy link'), 'copy', '📋', function(){ return copyContextText(link.href); });
      }
      var image = target.closest('img[src]');
      if (image && /^https?:/.test(image.src)) {
        add(ui('Abrir imagem', 'Open image'), 'studio', '🖼️', function(){ window.open(image.src, '_blank', 'noopener'); });
        add(ui('Copiar endereço da imagem', 'Copy image address'), 'copy', '📋', function(){ return copyContextText(image.src); });
      }
      var turn = target.closest('.turn');
      if (!selection && turn) add(ui('Copiar mensagem', 'Copy message'), 'copy', '📋', function(){ return copyContextText(turn.innerText); });
      if (state.currentChat) add(ui('Mudar nome da conversa', 'Rename conversation'), 'edit', '✏️', function(){ renameConversation(state.currentChat); });
      add(ui('Nova conversa', 'New conversation'), 'chat', '💬', startNewChat);
      add(ui('Atualizar app', 'Update app'), 'refresh', '↻', function(){ document.getElementById('btnRefreshApp').click(); });
      add(ui('Opções', 'Settings'), 'settings', '⚙️', function(){ document.getElementById('btnHeaderSettings').click(); });
    }
    (target.closest('dialog[open]') || document.body).appendChild(menu);
    menu.style.left = Math.max(8, Math.min(x, innerWidth - menu.offsetWidth - 8)) + 'px';
    menu.style.top = Math.max(8, Math.min(y, innerHeight - menu.offsetHeight - 8)) + 'px';
    menu.addEventListener('keydown', function(e){
      var items = Array.from(menu.querySelectorAll('button:not(:disabled)'));
      var i = items.indexOf(document.activeElement);
      if (['ArrowDown','ArrowUp','Home','End'].indexOf(e.key) >= 0) {
        e.preventDefault();
        var next = e.key === 'Home' ? 0 : e.key === 'End' ? items.length - 1 : (i + (e.key === 'ArrowUp' ? -1 : 1) + items.length) % items.length;
        if (items[next]) items[next].focus();
      }
    });
    var first = menu.querySelector('button:not(:disabled)'); if (first) first.focus();
  }
  function showFolderContext(chat, target, x, y) {
    showContextMenu(target, x, y);
    var menu = contextMenu; menu.innerHTML = '';
    [{id:'auto',name:ui('Automático (45 min)', 'Automatic (45 min)')}].concat(state.folders).forEach(function(folder){
      var b = document.createElement('button'); b.type = 'button'; b.setAttribute('role','menuitem');
      b.innerHTML = dualIco('folder', '📁') + '<span>' + esc(folder.name) + '</span>';
      b.onclick = function(){ closeContextMenu(); api('/api/chat/folder', {id:chat.id,folder:folder.id}).then(refreshChats).catch(function(){ alert(ui('Não foi possível mover a conversa.', 'Could not move the conversation.')); }); };
      menu.appendChild(b);
    });
    menu.style.top = Math.max(8, Math.min(y, innerHeight-menu.offsetHeight-8))+'px';
    menu.querySelector('button').focus();
  }
  document.addEventListener('contextmenu', function(e){
    e.preventDefault();
    if (e.target.closest('.maestro-context-menu')) return;
    showContextMenu(e.target, e.clientX, e.clientY);
  });
  document.addEventListener('pointerdown', function(e){ if (contextMenu && !contextMenu.contains(e.target)) closeContextMenu(); }, true);
  document.addEventListener('keydown', function(e){
    if (e.key === 'Escape' && contextMenu) { e.preventDefault(); e.stopImmediatePropagation(); closeContextMenu(true); return; }
    if (e.key === 'Escape' && document.querySelector('.rename-dialog[open]')) { e.preventDefault(); e.stopImmediatePropagation(); document.querySelector('.rename-dialog[open]').close(); return; }
    if (e.key === 'ContextMenu' || (e.shiftKey && e.key === 'F10')) {
      e.preventDefault(); var target = document.activeElement, r = target.getBoundingClientRect(); showContextMenu(target, r.left, r.bottom);
    }
  }, true);
  window.addEventListener('resize', function(){ closeContextMenu(); });
  document.addEventListener('scroll', function(e){ if (contextMenu && !contextMenu.contains(e.target)) closeContextMenu(); }, true);
  var longPress = null, pressStart = null, swallowClickUntil = 0;
  function cancelLongPress(){ clearTimeout(longPress); longPress = null; pressStart = null; }
  document.addEventListener('pointerdown', function(e){
    if (e.pointerType === 'mouse' || e.button !== 0 || e.target.closest('input, textarea, select, [contenteditable], .maestro-context-menu, dialog')) return;
    cancelLongPress();
    var target = e.target, x = e.clientX, y = e.clientY;
    pressStart = {x:x,y:y};
    longPress = setTimeout(function(){
      longPress = null; swallowClickUntil = Date.now()+1000; showContextMenu(target,x,y);
    }, 550);
  });
  document.addEventListener('pointermove', function(e){ if (pressStart && Math.hypot(e.clientX-pressStart.x,e.clientY-pressStart.y)>10) cancelLongPress(); });
  ['pointerup','pointercancel'].forEach(function(name){ document.addEventListener(name,cancelLongPress); });
  document.addEventListener('click', function(e){ if (Date.now()<swallowClickUntil && !e.target.closest('.maestro-context-menu')) { e.preventDefault(); e.stopImmediatePropagation(); swallowClickUntil=0; } }, true);

  // Etiqueta minimalista do modelo, com cor por empresa.
  window.mtag = function(engine, model){
    var t = (String(model||'').split(':').pop() + ' ' + String(engine||'')).toLowerCase();
    var m = t.match(/sonnet|opus|haiku|fable|sol\b|terra|luna|deepseek|gemini|argon|flash|gpt-oss|codex|claude|antigravity/);
    if (!m) return '';
    var k = m[0], fam = /sonnet|opus|haiku|fable|claude/.test(k) ? 'claude'
      : /sol|terra|luna|codex/.test(k) ? 'openai'
      : k === 'deepseek' ? 'deepseek' : 'google';
    var label = k === 'claude' ? 'claude' : k === 'antigravity' ? 'gemini' : k === 'argon' || k === 'flash' ? k : k.replace(/\b/g,'');
    return '<span class="mtag mtag-' + fam + '">' + label + '</span>';
  };

  function makeChatRow(c, fmap) {
    var fld = fmap[c.project] || { icon: "📁", color: "#38bdf8", name: c.project };
    var row = document.createElement("div");
    row.dataset.chatId = c.id;
    row.className = "chat-row" + (state.currentChat && state.currentChat.id === c.id ? " active" : "");
    var info = document.createElement("div");
    info.className = "chat-row-info";
    var when = chatRelativeTime(c.updated_at);
    var subProj = (state.filterProj === "all")
      ? '<span title="' + esc(fld.name) + '" style="color:' + esc(fld.color) + '">' + projectIco(fld.icon) + '</span><span>·</span>'
      : '';
    var activity = c.activity || { label: "Nova conversa", text: "À espera do primeiro pedido" };
    var label = statusText(activity.label);
    var detailText = routeText(statusText(activity.detail));
    var preview = label + " · " + activity.text;
    var detail = activity.detail ? "\n" + detailText : "";
    info.innerHTML =
      '<div class="chat-row-title">' + (c.pinned ? dualIco("pin", "📌") + " " : "") + esc(c.title) + '</div>' +
      '<div class="chat-row-preview ' + esc(activity.state || 'idle') + '" title="' + esc(preview + detail) + '"><b>' + esc(label) + '</b> · ' + esc(activity.text === "À espera do primeiro pedido" || activity.text === "A processar anexos" ? statusText(activity.text) : activity.text) + '</div>' +
      (activity.state === 'running' && activity.detail ? '<div class="chat-row-step" title="' + esc(detailText) + '">' + esc(detailText) + '</div>' : '') +
      '<div class="chat-row-sub">' + subProj + '<span>' + c.msg_count + ' msg</span>' +
      (when ? '<span>· ' + esc(when) + '</span>' : '') + '</div>';
    if(c.subtasks && c.subtasks.length){
      var children=document.createElement('div');children.className='wf-subchats';
      var chip=document.createElement('button');chip.type='button';chip.className='st-chip';chip.setAttribute('aria-label',ui('Estratégia do pedido: ', 'Request strategy: ')+window.maestroStrategy.summary(c.subtasks));
      chip.innerHTML='🧭 <span>'+esc(window.maestroStrategy.summary(c.subtasks))+'</span>';
      var model=function(){return window.maestroStrategy.fromSubtasks(c.subtasks);};
      children.addEventListener('pointerenter',function(ev){if(ev.pointerType==='mouse')window.maestroStrategy.showTip(children,model());});children.addEventListener('pointerleave',window.maestroStrategy.hideTip);
      chip.onclick=function(ev){window.maestroStrategy.point(c.subtasks[0].turn_id,c.id);if(window.innerWidth<=1080){ev.stopPropagation();window.maestroStrategy.showTip(children,model(),true);}};
      children.appendChild(chip);
      c.subtasks.forEach(function(task){
        var button=document.createElement('button');button.type='button';button.title=task.engine+' · '+task.status;
        button.innerHTML='<span class="wf-dot" data-status="'+esc(task.status)+'">●</span><span>'+esc(task.title)+'</span>'+mtag(task.engine,task.model);
        button.onclick=function(event){event.stopPropagation();wfOpenSubchat(c.id,task.turn_id,task.node_id);};children.appendChild(button);
      });info.appendChild(children);
    }
    row.onclick = function(){ setModule("chat"); openChat(c.id); closeMobileDrawers(); };

    var acts = document.createElement("div");
    acts.className = "chat-row-actions";
    var moveIcon = document.createElement("span");
    moveIcon.innerHTML = dualIco("folder", "📁");
    moveIcon.style.cssText = "position:relative;display:inline-flex;align-items:center;justify-content:center;width:24px;height:24px";
    var move = document.createElement("select");
    move.setAttribute("aria-label", ui("Organizar conversa", "Organize conversation"));
    move.title = ui("Mover para uma pasta ou ativar organização automática", "Move to a folder or enable automatic organization");
    move.style.cssText = "position:absolute;inset:0;width:24px;height:24px;opacity:0;cursor:pointer";
    var placeholder = document.createElement("option");
    placeholder.textContent = "📁"; placeholder.value = ""; placeholder.disabled = true; placeholder.selected = true;
    move.appendChild(placeholder);
    var automatic = document.createElement("option");
    automatic.value = "auto"; automatic.textContent = ui("Automático (45 min)", "Automatic (45 min)");
    move.appendChild(automatic);
    state.folders.forEach(function(folder){
      var option = document.createElement("option");
      option.value = folder.id; option.textContent = folder.icon + " " + folder.name;
      move.appendChild(option);
    });
    move.onclick = function(ev){ ev.stopPropagation(); };
    move.onchange = function(){
      api("/api/chat/folder", {id:c.id, folder:move.value}).then(refreshChats);
    };
    var bPin = document.createElement("button"); bPin.innerHTML = dualIco("pin", "📌");
    bPin.title = c.pinned ? ui("Desafixar conversa", "Unpin conversation") : ui("Fixar conversa", "Pin conversation");
    bPin.setAttribute("aria-label", bPin.title);
    bPin.setAttribute("aria-pressed", c.pinned ? "true" : "false");
    bPin.onclick = function(ev){ ev.stopPropagation(); api("/api/chat/pin", {id: c.id}).then(refreshChats); };
    var bDel = document.createElement("button"); bDel.innerHTML = dualIco("clean", "🗑");
    bDel.title = ui("Apagar conversa", "Delete conversation");
    bDel.setAttribute("aria-label", bDel.title);
    bDel.onclick = function(ev){
      ev.stopPropagation();
      if (confirm(ui("Apagar esta conversa?", "Delete this conversation?"))) {
        api("/api/chat/delete", {id: c.id}).then(function(){
          if (state.currentChat && state.currentChat.id === c.id) state.currentChat = null;
          refreshChats(); renderMessages();
        });
      }
    };
    var bArchive = document.createElement("button");
    bArchive.innerHTML = c.archived ? dualIco("restore", "↩") : dualIco("archive", "🗃");
    bArchive.title = c.archived ? ui("Recuperar conversa", "Restore conversation") : ui("Arquivar conversa", "Archive conversation");
    bArchive.setAttribute("aria-label", bArchive.title);
    bArchive.onclick = function(ev){
      ev.stopPropagation();
      if (!c.archived && c.activity && c.activity.state === 'running') {
        alert(ui("Para a execução antes de arquivar esta conversa.", "Stop the running task before archiving this conversation.")); return;
      }
      bArchive.disabled = true;
      api("/api/chat/archive", {id:c.id, archived:!c.archived}).then(function(d){
        if (state.currentChat && state.currentChat.id === c.id) {
          state.currentChat = d.chat;
          state.activeJob = null;
          if (state.jobPollTimer) clearInterval(state.jobPollTimer);
          state.jobPollTimer = null;
          syncComposerBusyUI(); renderMessages();
          if (!d.chat.archived) checkRunningJobForChat(c.id);
        }
        return refreshChats();
      }).catch(function(){
        alert(ui("Não foi possível alterar o arquivo. Se houver uma execução ativa, para-a primeiro.", "Could not change the archive. Stop any running task first."));
      }).finally(function(){ bArchive.disabled = false; });
    };
    moveIcon.appendChild(move);
    var bRename = document.createElement("button");
    bRename.innerHTML = dualIco("edit", "✏️");
    bRename.title = ui("Mudar nome", "Rename");
    bRename.setAttribute("aria-label", bRename.title);
    bRename.onclick = function(ev){ ev.stopPropagation(); renameConversation(c); };
    acts.appendChild(bRename);
    acts.appendChild(bArchive);
    if (!c.archived) { acts.appendChild(moveIcon); acts.appendChild(bPin); }
    acts.appendChild(bDel);
    row.appendChild(info); row.appendChild(acts);
    return row;
  }
  function renderChatList() {
    if (window.maestroStrategy) window.maestroStrategy.hideTip();
    var box = document.getElementById("chatList");
    var q = state.search.toLowerCase();
    var fmap = {};
    state.folders.forEach(function(f){ fmap[f.id] = f; });

    var archiveButton = document.getElementById("btnArchiveView");
    archiveButton.innerHTML = state.archiveView ? dualIco("restore", "←") + " " + ui("Conversas", "Conversations") : dualIco("archive", "🗃") + " " + ui("Arquivo", "Archive") + " (" + state.archivedChats.length + ")";
    archiveButton.setAttribute("aria-pressed", String(state.archiveView));
    document.getElementById("archiveEngine").hidden = !state.archiveView;
    var filtered = (state.archiveView ? state.archivedChats : state.chats).filter(function(c){
      if (state.archiveView && state.archiveEngine !== "all" && (c.engines || []).indexOf(state.archiveEngine) === -1) return false;
      if (state.filterProj !== "all" && c.project !== state.filterProj) return false;
      if (q && (c.title + " " + ((c.activity || {}).text || "")).toLowerCase().indexOf(q) === -1) return false;
      return true;
    });
    box.innerHTML = "";
    if (state.archiveView) {
      var hint = document.createElement("p");
      hint.style.cssText = "font-size:.75rem;color:var(--muted);padding:6px";
      hint.textContent = ui("Guardadas em disco, sem agentes ativos. Recupera para continuar; os pedidos pendentes voltam à fila.", "Saved on disk with no active agents. Restore to continue; pending requests return to the queue.");
      box.appendChild(hint);
    }

    /* à procura: lista lisa (sem grupos) para os resultados ficarem juntos */
    if (q) {
      filtered.forEach(function(c){ box.appendChild(makeChatRow(c, fmap)); });
      return;
    }

    var collapsed = chatGroupsCollapsed();
    var order = ["pinned", "today", "yday", "week", "month", "older"];
    var byKey = {};
    filtered.forEach(function(c){
      var b = c.pinned ? { key: "pinned", label: ui("Fixadas", "Pinned") } : chatBucket(c);
      if (!byKey[b.key]) byKey[b.key] = { key: b.key, label: b.label, items: [] };
      byKey[b.key].items.push(c);
    });

    order.forEach(function(key){
      var g = byKey[key];
      if (!g || !g.items.length) return;
      var isCollapsed = collapsed.indexOf(key) !== -1;
      var head = document.createElement("div");
      head.className = "chat-group-head";
      head.innerHTML = '<span class="chev">' + (isCollapsed ? "▸" : "▾") + '</span>' +
        '<span>' + (key === 'pinned' ? dualIco('pin', '📌') + ' ' : '') + esc(g.label) + '</span><span class="n">' + g.items.length + '</span>';
      head.onclick = function(){ toggleChatGroup(key); };
      box.appendChild(head);
      var wrap = document.createElement("div");
      wrap.className = "chat-group" + (isCollapsed ? " collapsed" : "");
      g.items.forEach(function(c){ wrap.appendChild(makeChatRow(c, fmap)); });
      box.appendChild(wrap);
    });
  }

  function refreshChats() {
    return api("/api/chats").then(function(d){
      state.chats = d.chats || [];
      state.archivedChats = d.archived_chats || [];
      if (d.folders) state.folders = d.folders;
      renderFolderStripAndSelect();
      renderChatList();
    });
  }

  /* Editar/bifurcar: cria sempre uma conversa nova com o histórico até ao ponto escolhido (o original fica intacto). */
  document.addEventListener("click", function(ev){
    var b = ev.target.closest && ev.target.closest(".turn-acts button");
    var c = state.currentChat;
    if (!b || !c) return;
    var turn = b.closest(".turn"), idx = -1;
    Object.keys(_turnCache).forEach(function(k){ if (_turnCache[k].el === turn) idx = +k; });
    if (idx < 0) return;
    var m = c.messages[idx], edit = b.getAttribute("data-act") === "edit";
    api("/api/chat/fork", {id: c.id, upto: edit ? idx : idx + 1}).then(function(d){
      return openChat(d.chat.id).then(function(){
        if (!edit) return;
        var ta = document.getElementById("promptInput");
        ta.value = String(m.text || ""); ta.focus();
        state.pendingImages = (m.images || []).slice(); state.pendingDocs = (m.docs || []).slice();
        renderAttachStrip();
      });
    }).catch(function(){ notifyUser("Maestro", ui("Não foi possível bifurcar.", "Could not fork.")); });
  });

  function openChat(id) {
    return api("/api/chat?id=" + encodeURIComponent(id)).then(function(d){
      if (d.chat) {
        state.currentChat = d.chat;
        syncComposerBusyUI();
        state.liveSig = null;
        document.getElementById("selProject").value = d.chat.project || "nas";
        document.getElementById("selEngine").value = d.chat.engine || "auto";
        loadProjectInspector(d.chat.project || "nas");
        renderChatList();
        if (state.status) renderInspector();
        restoreQueue(d.chat.id);
        receiveQueue(d);
        state.activeJob = d.job || null;
        renderMessages();
        checkRunningJobForChat(d.chat.id);
        persistLastChat(d.chat.id);
        restoreDraft();
        updateComposerPlaceholder();
      }
    });
  }

  function startNewChat() {
    state.archiveView = false;
    var proj = document.getElementById("selProject").value || "nas";
    var eng = document.getElementById("selEngine").value || "auto";
    api("/api/chat/new", { project: proj, engine: eng }).then(function(d){
      state.currentChat = d.chat;
      state.activeJob = null;
      state.liveSig = null;
      setModule("chat");
      refreshChats();
      renderMessages();
      persistLastChat(d.chat.id);
      restoreDraft();
      syncComposerBusyUI();
      closeMobileDrawers();
    });
  }
  document.getElementById("btnNewChat").onclick = startNewChat;
  document.getElementById("btnArchiveView").onclick = function(){ state.archiveView = !state.archiveView; renderChatList(); };
  document.getElementById("archiveEngine").onchange = function(e){ state.archiveEngine = e.target.value; renderChatList(); };
  document.getElementById("searchBox").oninput = function(e){ state.search = e.target.value || ""; renderChatList(); };

  /* ---- ATTACHMENTS, VOICE & PROMPT ENHANCER ---- */
  function uploadImageFile(file) {
    var reader = new FileReader();
    reader.onload = function(){
      api("/api/upload", { name: file.name, data_url: reader.result }).then(function(res){
        if (res.path) { state.pendingImages.push(res.path); renderAttachStrip(); syncComposerBusyUI(); }
      });
    };
    reader.readAsDataURL(file);
  }

  function renderAttachStrip() {
    var box = document.getElementById("attachStrip");
    box.innerHTML = "";
    state.pendingImages.forEach(function(p, idx){
      var thumb = document.createElement("div");
      thumb.className = "attach-thumb";
      thumb.innerHTML = '<img src="/api/image?path=' + encodeURIComponent(p) + '"><button type="button">✕</button>';
      thumb.querySelector("button").onclick = function(){ state.pendingImages.splice(idx, 1); renderAttachStrip(); syncComposerBusyUI(); };
      box.appendChild(thumb);
    });
    (state.pendingDocs || []).forEach(function(d, idx){
      var chip = document.createElement("div");
      chip.className = "doc-chip";
      chip.title = d.path + "\n" + (d.preview || "");
      chip.innerHTML = '<span>📄</span><span class="doc-name">' + esc(d.name || "colado") + '</span>' +
        '<span style="opacity:.65">' + fmtChars(d.chars) + '</span><button type="button" title="Remover anexo">✕</button>';
      chip.querySelector("button").onclick = function(){
        state.pendingDocs.splice(idx, 1); renderAttachStrip(); syncComposerBusyUI();
      };
      box.appendChild(chip);
    });
    placeJumpEndBtn();
  }

  /* Colar muito texto/código => guarda em ficheiro no NAS e passa só a referência.
     Evita que 400 linhas de log/código poluam o prompt (e o histórico do chat). */
  var PASTE_AS_FILE_MIN_CHARS = 600;
  var PASTE_AS_FILE_MIN_LINES = 12;

  function addPastedDoc(text) {
    var name = "colado-" + ((state.pendingDocs || []).length + 1) + ".md";
    return api("/api/paste-file", { text: text, name: name }).then(function(res){
      if (!res || !res.path) throw new Error("sem path");
      state.pendingDocs.push({
        path: res.path, name: res.name || name,
        chars: res.chars || text.length, lines: res.lines || 0,
        preview: res.preview || text.slice(0, 120)
      });
      renderAttachStrip();
      syncComposerBusyUI();
      document.getElementById("promptInput").focus();
      notifyUser("📄 Texto colado como ficheiro", (res.chars || text.length) + " caracteres guardados em " +
        (res.name || name) + " — o Maestro lê o ficheiro em vez de encher o prompt.");
      return res;
    }).catch(function(){
      // Fallback: se o servidor não tiver o endpoint, cola normal.
      var ta = document.getElementById("promptInput");
      ta.value = (ta.value ? ta.value + "\n" : "") + text;
      ta.dispatchEvent(new Event("input"));
    });
  }
  document.getElementById("btnAttach").onclick = function(){ document.getElementById("fileInput").click(); };
  document.getElementById("fileInput").onchange = function(e){
    Array.prototype.forEach.call(e.target.files || [], uploadImageFile);
    e.target.value = "";
  };
  document.addEventListener("paste", function(e){
    var items = (e.clipboardData || {}).items || [];
    for (var i = 0; i < items.length; i++) {
      if (items[i].type && items[i].type.indexOf("image/") === 0) {
        var f = items[i].getAsFile();
        if (f) uploadImageFile(f);
      }
    }
  });

  var btnVoice = document.getElementById("btnVoice");
  var SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (SpeechRec) {
    var rec = new SpeechRec();
    rec.lang = "pt-PT";
    rec.onresult = function(ev){
      var ta = document.getElementById("promptInput");
      ta.value = (ta.value ? ta.value + " " : "") + ev.results[0][0].transcript;
      ta.dispatchEvent(new Event("input"));
    };
    rec.onend = function(){ state.recording = false; btnVoice.textContent = "🎤 Voz"; };
    state.recognition = rec;
  }
  btnVoice.onclick = function(){
    if (!state.recognition) { document.getElementById("promptInput").focus(); return; }
    if (state.recording) state.recognition.stop();
    else { state.recording = true; btnVoice.textContent = "🔴 A ouvir…"; state.recognition.start(); }
  };

  document.getElementById("btnEnhance").onclick = function(){
    var ta = document.getElementById("promptInput");
    var raw = (ta.value || "").trim();
    if (!raw) { ta.focus(); return; }
    var btn = document.getElementById("btnEnhance");
    btn.textContent = "⏳…";
    api("/api/enhance", { text: raw, project: document.getElementById("selProject").value }).then(function(r){
      if (r.enhanced) { ta.value = r.enhanced; ta.dispatchEvent(new Event("input")); }
    }).finally(function(){ btn.textContent = "✨ Melhorar"; });
  };

  document.getElementById("btnAutoEnhance").onclick = function(){
    state.autoEnhance = !state.autoEnhance;
    syncAutoEnhanceButton();
  };
  function syncAutoEnhanceButton() {
    var b = document.getElementById("btnAutoEnhance");
    b.classList.toggle("magic", state.autoEnhance);
    b.classList.toggle("active", state.autoEnhance);
    b.textContent = ui("Melhorar ao enviar: ", "Enhance on send: ") + (state.autoEnhance ? ui("Sim", "On") : ui("Não", "Off"));
  }

  /* ---- LIVE STREAMING JOB POLLER + MESSAGE QUEUE & STEER ENGINE ---- */
  var ta = document.getElementById("promptInput");
  /* ---- ESTILO DE CHAT: auto / código / prosa ---- */
  var CODE_HINTS = /(```|^(\s*)\$\s|\b(ssh|docker|git|npm|pnpm|systemctl|curl|grep|sed|awk|python|node|bash|sudo|chmod|kubectl)\b|\b(erro|error|traceback|exception|stack trace|segfault|build|deploy|patch|commit|diff|stack|log|stdout|stderr)\b|\b(função|classe|script|endpoint|api|json|yaml|sql|regex|C#|Unity|\.py|\.js|\.ts|\.cs|\.sh)\b)/i;
  var STYLE_LABELS = { auto: "Aspeto: Auto", code: "Aspeto: Código", prose: "Aspeto: Prosa" };
  var STYLE_ORDER = ["auto", "code", "prose"];

  function looksCodey(text) {
    if (!text) return false;
    if (/```/.test(text)) return true;
    var hits = (String(text).match(CODE_HINTS) || []).length;
    return hits >= 2;
  }
  function chatStylePref(chatId) {
    try {
      var m = JSON.parse(localStorage.getItem("maestro_chat_style") || "{}");
      return m[chatId] || "auto";
    } catch (e) { return "auto"; }
  }
  function saveChatStylePref(chatId, style) {
    try {
      var m = JSON.parse(localStorage.getItem("maestro_chat_style") || "{}");
      m[chatId] = style;
      localStorage.setItem("maestro_chat_style", JSON.stringify(m));
    } catch (e) {}
  }
  function detectChatStyle() {
    var c = state.currentChat;
    if (!c || !c.messages || !c.messages.length) return "prose";
    var codey = 0, total = 0;
    for (var i = Math.max(0, c.messages.length - 8); i < c.messages.length; i++) {
      var m = c.messages[i];
      total++;
      if (looksCodey(m.text || "")) codey++;
    }
    return (codey >= Math.max(1, Math.ceil(total / 3))) ? "code" : "prose";
  }
  function applyChatStyle() {
    var box = document.getElementById("chatScroll");
    if (!box) return;
    var cid = state.currentChat ? state.currentChat.id : "__none__";
    var pref = chatStylePref(cid);
    var resolved = (pref === "auto") ? detectChatStyle() : pref;
    if (box.getAttribute("data-style") !== resolved) box.setAttribute("data-style", resolved);
    var btn = document.getElementById("btnChatStyle");
    if (btn) {
      btn.textContent = state.lang === "en" ? ({auto:"Style: Auto", code:"Style: Code", prose:"Style: Prose"}[pref] || "Style: Auto") : (STYLE_LABELS[pref] || "⌘ Auto");
      btn.classList.toggle("magic", pref === "code");
      btn.title = ui("Auto deteta: agora ", "Auto detected: ") + (resolved === "code" ? ui("⌘ código", "⌘ code") : ui("📖 prosa", "📖 prose"));
    }
  }
  var _btnChatStyle = document.getElementById("btnChatStyle");
  if (_btnChatStyle) {
    _btnChatStyle.onclick = function(){
      var cid = state.currentChat ? state.currentChat.id : "__none__";
      var cur = chatStylePref(cid);
      var next = STYLE_ORDER[(STYLE_ORDER.indexOf(cur) + 1) % STYLE_ORDER.length];
      saveChatStylePref(cid, next);
      applyChatStyle();
    };
  }

  // Keep the options above the composer even when its dock scrolls.
  (function initComposerMenu(){
    var trigger = document.getElementById("btnComposerMore");
    var menu = document.getElementById("composerMenu");
    document.body.appendChild(menu);
    function close(restoreFocus){
      menu.hidden = true;
      trigger.setAttribute("aria-expanded", "false");
      if (restoreFocus) trigger.focus();
    }
    trigger.addEventListener("click", function(){
      if (!menu.hidden) { close(true); return; }
      var rect = trigger.getBoundingClientRect();
      menu.style.maxHeight = Math.max(120, rect.top - 20) + "px";
      menu.hidden = false;
      menu.style.left = Math.max(12, Math.min(rect.left, innerWidth - menu.offsetWidth - 12)) + "px";
      menu.style.top = Math.max(12, rect.top - menu.offsetHeight - 8) + "px";
      trigger.setAttribute("aria-expanded", "true");
      menu.querySelector("button:not([hidden])").focus();
    });
    document.addEventListener("click", function(e){
      if (!menu.hidden && !menu.contains(e.target) && !trigger.contains(e.target)) close(false);
    });
    document.addEventListener("keydown", function(e){
      if (menu.hidden) return;
      if (e.key === "Escape") { e.preventDefault(); close(true); return; }
      var dir = e.key === "ArrowDown" ? 1 : e.key === "ArrowUp" ? -1 : e.key === "Home" ? "first" : e.key === "End" ? "last" : 0;
      if (!dir) return;
      var items = Array.prototype.filter.call(menu.querySelectorAll("button"), function(b){ return !b.hidden && b.offsetParent !== null; });
      if (!items.length) return;
      e.preventDefault();
      var i = items.indexOf(document.activeElement);
      i = dir === "first" ? 0 : dir === "last" ? items.length - 1 : (i + dir + items.length) % items.length;
      items[i].focus();
    });
    document.addEventListener("focusin", function(e){
      if (!menu.hidden && !menu.contains(e.target) && e.target !== trigger) close(false);
    });
    menu.addEventListener("click", function(e){
      var button = e.target.closest("button");
      if (button && ["btnAttach", "btnVoice", "btnEnhance", "btnAltBusy"].indexOf(button.id) !== -1) close(true);
    });
    window.addEventListener("resize", function(){ close(false); });
    document.querySelector(".composer-dock").addEventListener("scroll", function(){ close(false); });
  })();

  var btnAltBusy = document.getElementById("btnAltBusy");
  var btnStopJob = document.getElementById("btnStopJob");
  var btnSend = document.getElementById("btnSend");

  function syncBusyModeButton() {
    syncComposerBusyUI();
  }
  function setBusyMode(mode) {
    state.busyMode = mode;
    localStorage.setItem("maestro_busy_mode", mode);
    syncBusyModeButton();
  }

  function isCurrentChatBusy() {
    return Boolean(state.activeJob && ["running", "queued"].indexOf(state.activeJob.status) !== -1 && state.currentChat && state.activeJob.chat_id === state.currentChat.id);
  }

  function queueReason(item) {
    if (item.waiting_on === "__chat_order__") return ui("Na fila · corre depois do pedido anterior desta conversa (⚡ Go força)", "Queued · runs after the previous request in this chat (⚡ Go forces)");
    if (item.reason === "dependency" && item.waiting_on) return ui("À espera de: ", "Waiting for: ") + item.waiting_on + ui(" · mesmo projeto, uma escrita de cada vez", " · same project, one write at a time");
    if (item.blocked_by) return ui("Bloqueado pelo pedido interrompido acima · retomar ou remover esse pedido", "Blocked by the interrupted request above · resume or remove it");
    var labels = {
      evaluating: ["A avaliar estratégia…", "Evaluating strategy…"],
      dependency: ["Aguardar contexto / evitar conflitos", "Waiting for context / avoiding conflicts"],
      capacity: ["Aguardar vaga (máximo 4 agentes)", "Waiting for capacity (maximum 4 agents)"],
      daily_budget: ["Orçamento diário protegido · aguardar quota ou alternativa compatível", "Daily budget protected · waiting for quota or compatible alternative"],
      quota: ["Aguardar quota ou modelo compatível", "Waiting for quota or compatible model"],
      engine_busy: ["Aguardar agente adequado", "Waiting for suitable agent"],
      interrupted: ["Interrompido · retomar manualmente", "Interrupted · resume manually"]
    };
    var label = labels[item.reason] || labels.evaluating;
    return ui(label[0], label[1]);
  }
  function queueTip(item, idx) {
    var t = ["#" + (idx + 1) + " · " + (item.text || ""), queueReason(item)];
    if (item.waiting_on && item.waiting_on !== "__chat_order__") t.push(ui("Depende de: ", "Depends on: ") + item.waiting_on);
    if (item.conflict) t.push(ui("Risco se avançar: ", "Risk if forced: ") + item.conflict);
    if (item.blocked_by) t.push(ui("Bloqueado por: ", "Blocked by: ") + item.blocked_by);
    return t.join("\n");
  }
  function renderQueueStrip() {
    var box = document.getElementById("queueStrip");
    if (!box) return;
    /* A fila é reenviada a cada poll. Reconstruir o innerHTML sem mudanças
       fazia o compositor redimensionar (e o chat saltar). */
    var cid = state.currentChat && state.currentChat.id;
    if (box.__chatId !== cid) {
      box.__chatId = cid; box.__editing = null; box.__dragging = false; box.__queueSig = null;
    }
    var sig = state.lang + "|" + state.messageQueue.map(function(item){
      return [item.id, item.status, item.reason, item.blocked_by, item.text, item.waiting_on, item.conflict, item.force].join("~");
    }).join("|");
    if (box.__queueSig === sig || box.__editing || box.__dragging || box.__saving) return;
    box.__queueSig = sig;
    box.hidden = !state.messageQueue.length;
    /* Fila grande fecha-se sozinha (>3); a escolha manual do utilizador prevalece. */
    if (box.__userFold === undefined) box.classList.toggle('folded', state.messageQueue.length > 3);
    box.innerHTML = state.messageQueue.length ? '<button type="button" class="queue-strip-head queue-fold" aria-label="' + ui('Recolher/expandir fila', 'Fold/unfold queue') + '">' + dualIco('queue','📥') + ' <span>' + ui('FILA MAESTRO', 'MAESTRO QUEUE') + ' · ' + state.messageQueue.length + '</span><span class="queue-chev" aria-hidden="true">⌃</span></button>' +
      state.messageQueue.map(function(item, idx){
        return '<div class="queue-item" data-qid="' + esc(item.id) + '" title="' + esc(queueTip(item, idx)) + '"><button type="button" class="queue-grip" aria-label="' + ui('Arrastar para reordenar. Teclas ↑ e ↓ para mover.', 'Drag to reorder. Use ↑ and ↓ to move.') + '" title="' + ui('Arrastar para reordenar · ↑ ↓', 'Drag to reorder · ↑ ↓') + '">⠿</button><span class="queue-item-txt"><b>#' + (idx+1) + '</b> ' + esc(item.text || ui("Anexo", "Attachment")) +
          ' <span class="request-status" data-status="' + (item.status === 'interrupted' ? 'interrupted' : 'waiting') + '">' + (item.status === 'interrupted' ? ui('interrompido', 'interrupted') : ui('em espera', 'waiting')) + '</span><br><small>' + esc(queueReason(item)) + '</small></span><div class="queue-item-acts">' +
          (item.status === 'interrupted' ? '<button type="button" data-qretry="' + esc(item.id) + '">▶</button>' : '') +
          (item.status === 'queued' && item.conflict && !item.force && ['dependency','engine_busy'].indexOf(item.reason) !== -1 ? '<button type="button" class="qforce qforce-' + esc(item.conflict) + '" data-qforce="' + esc(item.id) + '" title="' + ui('Avançar mesmo assim (risco: ' + item.conflict + ')', 'Run anyway (risk: ' + item.conflict + ')') + '">' + dualIco('bolt','⚡') + ' ' + ui('Avançar', 'Go') + '</button>' : '') +
          '<button type="button" class="qedit" data-qedit="' + esc(item.id) + '" aria-label="' + ui('Editar pedido', 'Edit request') + '" title="' + ui('Editar pedido', 'Edit request') + '"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 20H4v-8M15 4l5 5M8 16l2-6L18 2l4 4-8 8z"/></svg></button>' +
          '<button type="button" data-qcancel="' + esc(item.id) + '" aria-label="' + ui('Remover da fila', 'Remove from queue') + '">' + dualIco('close','✕') + '</button></div></div>';
      }).join('') : '';
    var fold = box.querySelector('.queue-fold');
    if (fold) fold.onclick = function(){ box.__userFold = !box.classList.contains('folded'); box.classList.toggle('folded', box.__userFold); };
    function refreshQueue() {
      box.__queueSig = null;
      renderQueueStrip();
    }
    function showQueueError(message) {
      var error = document.createElement('div'); error.className = 'queue-error'; error.setAttribute('role', 'alert');
      error.textContent = message; box.appendChild(error);
    }
    function saveOrder(ids, focusId) {
      var cid = state.currentChat.id;
      box.__saving = true;
      api('/api/chat/queue/reorder', {id: cid, task_ids: ids}).then(function(res){
        box.__saving = false;
        if (!state.currentChat || state.currentChat.id !== cid) return;
        receiveQueue(res); refreshQueue();
        var row = Array.from(box.querySelectorAll('[data-qid]')).find(function(r){ return r.dataset.qid === focusId; });
        if (row) row.querySelector('.queue-grip').focus({preventScroll:true});
      }).catch(function(){
        box.__saving = false; refreshQueue();
        showQueueError(ui('Não foi possível reordenar. A fila pode ter mudado; tenta novamente.', 'Could not reorder. The queue may have changed; try again.'));
      });
    }
    box.querySelectorAll('[data-qedit]').forEach(function(button){
      button.onclick = function(){
        if (box.__saving || box.__dragging) return;
        var cid = state.currentChat.id, item = state.messageQueue.find(function(t){ return t.id === button.dataset.qedit; });
        if (!item) return;
        var row = button.closest('[data-qid]');
        box.__editing = item.id; row.classList.add('qediting');
        var editor = document.createElement('div'); editor.className = 'queue-editor';
        editor.innerHTML = '<textarea aria-label="' + ui('Editar texto do pedido', 'Edit request text') + '"></textarea><div class="queue-edit-footer"><span class="queue-error" role="alert"></span><button type="button" class="qdiscard">' + ui('Cancelar', 'Cancel') + '</button><button type="button" class="qsave">' + ui('Guardar', 'Save') + '</button></div>';
        box.querySelectorAll('button').forEach(function(b){ b.disabled = true; });
        row.appendChild(editor);
        var input = editor.querySelector('textarea'), save = editor.querySelector('.qsave'), cancel = editor.querySelector('.qdiscard');
        input.value = item.text || ''; input.focus();
        function close() { box.__editing = null; refreshQueue(); }
        cancel.onclick = close;
        save.onclick = function(){
          save.disabled = cancel.disabled = true;
          api('/api/chat/queue/edit', {id: cid, task_id: item.id, text: input.value, expected_text: item.text}).then(function(res){
            box.__editing = null;
            if (state.currentChat && state.currentChat.id === cid) { receiveQueue(res); refreshQueue(); }
          }).catch(function(){
            save.disabled = cancel.disabled = false;
            editor.querySelector('.queue-error').textContent = ui('Não foi possível guardar. O pedido pode já ter avançado ou sido alterado. Copia o texto antes de fechar.', 'Could not save. The request may have started or changed. Copy your text before closing.');
          });
        };
        input.onkeydown = function(e){
          if (e.key === 'Escape' && !save.disabled) { e.preventDefault(); e.stopPropagation(); close(); }
          if (e.key === 'Enter' && (e.ctrlKey || e.metaKey) && !save.disabled) { e.preventDefault(); e.stopPropagation(); save.click(); }
        };
      };
    });
    box.querySelectorAll('.queue-grip').forEach(function(grip){
      var row = grip.closest('[data-qid]');
      grip.onkeydown = function(e){
        if (box.__editing || box.__saving || ['ArrowUp','ArrowDown'].indexOf(e.key) === -1) return;
        e.preventDefault();
        var ids = state.messageQueue.map(function(t){ return t.id; }), i = ids.indexOf(row.dataset.qid), j = i + (e.key === 'ArrowUp' ? -1 : 1);
        if (j < 0 || j >= ids.length) return;
        ids.splice(j, 0, ids.splice(i, 1)[0]); saveOrder(ids, row.dataset.qid);
      };
      grip.onpointerdown = function(e){
        if (e.button !== 0 || box.__editing || box.__saving) return;
        e.preventDefault();
        var original = Array.from(box.querySelectorAll('[data-qid]')).map(function(r){ return r.dataset.qid; });
        box.__dragging = true; row.classList.add('qdrag'); box.setPointerCapture(e.pointerId);
        box.onpointermove = function(move){
          var target = document.elementFromPoint(move.clientX, move.clientY);
          target = target && target.closest('[data-qid]');
          if (!target || target === row || target.parentNode !== box) return;
          var rect = target.getBoundingClientRect();
          box.insertBefore(row, move.clientY < rect.top + rect.height / 2 ? target : target.nextSibling);
        };
        function finish(cancelled) {
          box.onpointermove = box.onpointerup = box.onpointercancel = box.onlostpointercapture = null;
          box.__dragging = false; row.classList.remove('qdrag');
          var ids = Array.from(box.querySelectorAll('[data-qid]')).map(function(r){ return r.dataset.qid; });
          if (!cancelled && ids.join('|') !== original.join('|')) saveOrder(ids, row.dataset.qid);
          else refreshQueue();
        }
        box.onpointerup = function(){ finish(false); };
        box.onpointercancel = box.onlostpointercapture = function(){ finish(true); };
      };
    });
    box.querySelectorAll('[data-qcancel], [data-qretry], [data-qforce]').forEach(function(button){
      button.onclick = function(){
        var retry = button.getAttribute('data-qretry'), force = button.getAttribute('data-qforce');
        button.disabled = true;
        api('/api/chat/queue/' + (force ? 'force' : retry ? 'retry' : 'cancel'), {task_id: force || retry || button.getAttribute('data-qcancel')})
          .then(function(){ checkRunningJobForChat(state.currentChat.id); });
      };
    });
  }
  function receiveQueue(res) {
    if (res && res.activity_version && res.chat) {
      var versions = state.activityVersions || (state.activityVersions = {});
      if (res.activity_version < (versions[res.chat.id] || 0)) return false;
      versions[res.chat.id] = res.activity_version;
    }
    if (res && Array.isArray(res.queue)) { state.messageQueue = res.queue; renderQueueStrip(); }
    return true;
  }

  function syncComposerBusyUI() {
    syncComposerControls();
    var busy = isCurrentChatBusy();
    updateComposerPlaceholder();
    var hasInput = Boolean((ta.value || "").trim() || state.pendingImages.length);
    /* Este bloco corre a cada 950 ms. Escrever o mesmo texto (ou recriar o
       innerHTML dos pontinhos) obrigava o dock a re-medir a altura, e o chat
       inteiro mexia. Só se toca no DOM quando o estado muda mesmo. */
    function setModeText(el, mode, txt) {
      if (!el) return;
      if (el.dataset.mode !== mode) {
        el.dataset.mode = mode;
        el.innerHTML = txt;
      } else if (el.__h !== txt) {
        el.innerHTML = txt;
      }
      el.__h = txt;
    }
    function setAlt(mode, txt) {
      if (!btnAltBusy) return;
      if (btnAltBusy.hidden) btnAltBusy.hidden = false;
      setModeText(btnAltBusy, "alt-" + mode, txt);
    }
    if (btnStopJob && btnStopJob.hidden === busy) btnStopJob.hidden = !busy;
    if (state.currentChat && state.currentChat.archived) {
      if (btnAltBusy) btnAltBusy.hidden = true;
      if (btnStopJob) btnStopJob.hidden = true;
      if (btnSend) { btnSend.disabled = true; setModeText(btnSend, "archived", ui("Arquivada · recupera para continuar", "Archived · restore to continue")); }
      return;
    }
    if (!busy) {
      if (btnAltBusy && !btnAltBusy.hidden) btnAltBusy.hidden = true;
      if (btnSend) {
        btnSend.disabled = false;
        setModeText(btnSend, "idle", ui("Enviar ↗", "Send ↗"));
      }
      return;
    }
    if (!hasInput) {
      if (btnAltBusy && !btnAltBusy.hidden) btnAltBusy.hidden = true;
      if (btnSend) {
        btnSend.disabled = false;
        var busyMode = state.activeJob.status === 'queued' ? 'waiting' : 'busy';
        if (btnSend.dataset.mode !== busyMode) {
          btnSend.dataset.mode = busyMode;
          btnSend.innerHTML = state.activeJob.status === 'queued' ? ui('Em fila', 'Queued') : ui('A trabalhar', 'Working') + '<span class="btn-dots"><i></i><i></i><i></i></span>';
        }
      }
    } else {
      if (btnSend) btnSend.disabled = false;
      if (state.busyMode === "queue") {
        setModeText(btnSend, "queue", dualIco("queue","📥") + " " + ui("Meter em Fila ↵", "Queue message ↵"));
        setAlt("queue", dualIco("steer","🧭") + " " + ui("Orientar já", "Steer now"));
      } else {
        setModeText(btnSend, "steer", dualIco("steer","🧭") + " " + ui("Orientar Agora ↵", "Steer now ↵"));
        setAlt("steer", dualIco("queue","📥") + " " + ui("Meter em Fila", "Queue message"));
      }
    }
  }
  syncBusyModeButton();

  if (btnStopJob) {
    btnStopJob.onclick = function(){
      if (!state.currentChat) return;
      api("/api/chat/stop", { id: state.currentChat.id }).then(function(){
        state.activeJob = null;
        syncComposerBusyUI();
        openChat(state.currentChat.id);
      });
    };
  }

  if (btnAltBusy) {
    btnAltBusy.onclick = function(){
      var oppositeMode = (state.busyMode === "queue") ? "steer" : "queue";
      handleComposerSubmit(oppositeMode);
    };
  }

  function checkRunningJobForChat(chatId) {
    if (!chatId || (state.currentChat && state.currentChat.id === chatId && state.currentChat.archived)) return;
    api("/api/chat/job?id=" + encodeURIComponent(chatId)).then(function(res){
      if (!state.currentChat || state.currentChat.id !== chatId) return;
      if (!receiveQueue(res)) return;
      if (res.chat) state.currentChat = res.chat;
      if (res && res.job && ["running", "queued"].indexOf(res.job.status) !== -1) {
        state.activeJob = res.job;
        renderMessages();
        syncComposerBusyUI();
        startJobPolling(chatId);
      } else {
        if (state.activeJob && state.activeJob.chat_id === chatId) {
          state.activeJob = null;
          syncComposerBusyUI();
        }
        /* Fila restaurada de um reload/turno falhado: sem isto ficava
           eternamente à espera de um job que já não existe. */
        renderMessages();
        syncComposerBusyUI();
        flushQueueIfIdle(chatId);
      }
    }).catch(function(){});
  }

  function flushQueueIfIdle(chatId) {
    // Admission and dispatch belong to the durable server queue.
  }

  function startJobPolling(chatId) {
    if (state.jobPollTimer) clearInterval(state.jobPollTimer);
    state.jobPollTimer = setInterval(function(){
      if (!state.currentChat || state.currentChat.id !== chatId || state.currentChat.archived) {
        clearInterval(state.jobPollTimer); state.jobPollTimer = null; return;
      }
      api("/api/chat/job?id=" + encodeURIComponent(chatId)).then(function(res){
        if (!res || !state.currentChat || state.currentChat.id !== chatId) return;
        if (!receiveQueue(res)) return;
        var changed = res.chat && JSON.stringify(res.chat.messages) !== JSON.stringify(state.currentChat.messages);
        if (res.chat) state.currentChat = res.chat;
        if (res.job && ["running", "queued"].indexOf(res.job.status) !== -1) {
          var activityChanged = !state.activeJob || state.activeJob.task_id !== res.job.task_id || state.activeJob.status !== res.job.status;
          var queueChanged = JSON.stringify(state.messageQueue) !== state.renderedQueue;
          state.activeJob = res.job;
          if (state.currentChat && state.currentChat.id === chatId) {
            /* Atualização cirúrgica: só o turno "ao vivo" é tocado, em sítio.
               O histórico já renderizado nunca é reconstruído => zero flashes. */
            if (changed || activityChanged || queueChanged) renderMessages();
            state.renderedQueue = JSON.stringify(state.messageQueue);
            ensureLiveTurn(res.job);
            syncComposerBusyUI();
          }
        } else {
          state.liveSig = null;
          state.liveStructSig = null;
          state.lastLiveRender = 0;
          clearInterval(state.jobPollTimer);
          state.jobPollTimer = null;
          state.activeJob = null;
          if (res.chat && state.currentChat && state.currentChat.id === chatId) {
            state.currentChat = res.chat;
          }
          refreshChats();
          renderMessages();
          syncComposerBusyUI();
          loadStatusAndPulse();
          if (res.chat && res.chat.messages && res.chat.messages.length) {
            var lastMsg = res.chat.messages[res.chat.messages.length - 1];
            if (lastMsg && lastMsg.role === "assistant") {
              notifyUser("👑 " + (lastMsg.agent_used || "Maestro AI"), lastMsg.text || "Concluído!");
            }
          }
          flushQueueIfIdle(chatId);
        }
      }).catch(function(){});
    }, 950);
  }

  // Writing is an explicit request to keep the conversation still. Resume via
  // a gesture back to the bottom, the jump button, or sending a new request.
  ta.addEventListener("focus", pauseChatForComposer);
  ta.addEventListener("input", function(){
    pauseChatForComposer();
    /* height:auto colapsa o composer por um instante: o chat fica mais alto e o
       browser reduz o scrollTop (clamp) — a cada tecla com 2+ linhas o chat
       "subia/descia". Guardamos e repomos o scroll à volta do redimensionamento. */
    var _cb = chatScrollBox(), _ct = _cb ? _cb.scrollTop : 0;
    ta.style.height = "auto";
    ta.style.height = Math.min(160, ta.scrollHeight) + "px";
    if (_cb) _cb.scrollTop = _ct;
    syncComposerBusyUI();
    if (_cb) _cb.scrollTop = _ct;
  });

  /* ---- RASCUNHOS PERSISTENTES ----
     O auto-update, um F5 ou um crash a meio do turno recarregavam a página e
     levavam o texto já escrito. Aqui o rascunho fica em localStorage, por
     conversa: sobrevive a reload, a fechar o separador e a reiniciar a app. */
  var DRAFT_STORE = "maestro_drafts_v2";
  var QUEUE_STORE = "maestro_queue_v2";
  var LAST_CHAT_STORE = "maestro_last_chat";

  function readStore(key){
    try { return JSON.parse(localStorage.getItem(key) || "null"); } catch(e){ return null; }
  }
  function writeStore(key, val){
    try { localStorage.setItem(key, JSON.stringify(val)); } catch(e){}
  }
  function draftSlot(){
    return (state.currentChat && state.currentChat.id) ? state.currentChat.id : "__new__";
  }
  var draftTimer = null;
  function persistDraft(immediate){
    if (draftTimer) { clearTimeout(draftTimer); draftTimer = null; }
    var write = function(){
      var all = readStore(DRAFT_STORE) || {};
      var slot = draftSlot();
      var txt = ta.value || "";
      if (txt.trim()) all[slot] = { text: txt, ts: Date.now() };
      else delete all[slot];
      var keys = Object.keys(all);
      if (keys.length > 80) {
        keys.sort(function(a, b){ return (all[a].ts || 0) - (all[b].ts || 0); });
        keys.slice(0, keys.length - 80).forEach(function(k){ delete all[k]; });
      }
      writeStore(DRAFT_STORE, all);
    };
    if (immediate) write(); else draftTimer = setTimeout(write, 250);
  }
  function clearDraft(){
    var all = readStore(DRAFT_STORE) || {};
    delete all[draftSlot()];
    writeStore(DRAFT_STORE, all);
  }
  function restoreDraft(){
    var all = readStore(DRAFT_STORE) || {};
    var d = all[draftSlot()];
    if (!d || typeof d.text !== "string" || !d.text.trim()) return;
    ta.value = d.text;
    ta.dispatchEvent(new Event("input"));
    syncComposerBusyUI();
  }
  function persistLastChat(id){
    try { if (id) localStorage.setItem(LAST_CHAT_STORE, id); } catch(e){}
  }
  function restoreQueue(chatId){
    state.messageQueue = [];
    renderQueueStrip();
    var legacy = readStore(QUEUE_STORE);
    if (legacy && legacy.chatId === chatId && Array.isArray(legacy.items) && legacy.items.length) {
      legacy.items.reduce(function(chain, item, index){
        return chain.then(function(){ return api('/api/chat/send', {id: chatId, text: item.text, images: item.images || [], docs: item.docs || [],
          project: item.proj, engine: item.eng || 'auto', async: true, request_id: 'legacy-' + chatId + '-' + index + '-' + hash32(item.text)}); });
      }, Promise.resolve()).then(function(){ localStorage.removeItem(QUEUE_STORE); checkRunningJobForChat(chatId); });
    }
  }

  ta.addEventListener("input", function(){ persistDraft(false); });
  ta.addEventListener("change", function(){ persistDraft(true); });
  window.addEventListener("beforeunload", function(){ persistDraft(true); });
  document.addEventListener("visibilitychange", function(){ if (document.hidden) persistDraft(true); });

  /* Colar blocos grandes => anexo em ficheiro (como no Claude/CLIs), não lixo no prompt. */
  ta.addEventListener("paste", function(e){
    var cd = e.clipboardData;
    if (!cd) return;
    var txt = "";
    try { txt = cd.getData("text/plain") || ""; } catch(err) { return; }
    var hasImage = Array.prototype.some.call(cd.items || [], function(it){
      return it.kind === "file" && String(it.type || "").indexOf("image/") === 0;
    });
    if (hasImage || !txt.trim()) return;
    var lines = txt.split("\n").length;
    if (txt.length < PASTE_AS_FILE_MIN_CHARS && lines < PASTE_AS_FILE_MIN_LINES) return;
    e.preventDefault();
    addPastedDoc(txt);
  });
  ta.addEventListener("keydown", function(e){
    if (e.isComposing || e.keyCode === 229 || e.repeat) return;
    if (e.key === "Enter" && !e.shiftKey && !e.altKey && ((e.ctrlKey || e.metaKey) || window.innerWidth > 760)) {
      e.preventDefault();
      document.getElementById("composerForm").requestSubmit();
    }
  });

  function dispatchChatTurn(rawTxt, imgs, proj, eng, isSteer, docs) {
    if (state.currentChat && state.currentChat.archived) {
      alert(ui("Recupera a conversa do arquivo para continuar.", "Restore the conversation from the archive to continue.")); return;
    }
    docs = docs || [];
    var prep = (state.autoEnhance && rawTxt.length > 3 && rawTxt.length < 220)
      ? api("/api/enhance", { text: rawTxt, project: proj }).then(function(r){ return r.enhanced || rawTxt; }).catch(function(){ return rawTxt; })
      : Promise.resolve(rawTxt);

    var targetChat = state.currentChat;
    var modelConfig = selectedModelConfig();
    var requestId = (window.crypto && crypto.randomUUID) ? crypto.randomUUID() : String(Date.now()) + '-' + Math.random();
    return state.submitChain = (state.submitChain || Promise.resolve()).then(function(){ return prep; }).then(function(finalTxt){
      var ensure = (targetChat || state.currentChat)
        ? Promise.resolve(targetChat || state.currentChat)
        : api("/api/chat/new", { project: proj, engine: eng }).then(function(d){ return d.chat; });

      return ensure.then(function(chat){
        if (!state.currentChat) state.currentChat = chat;
        return api("/api/chat/send", {
          id: chat.id,
          project: proj,
          engine: eng,
          model_id: modelConfig.model_id,
          effort: modelConfig.effort,
          text: finalTxt,
          images: imgs,
          docs: docs,
          steer: Boolean(isSteer),
          async: true,
          request_id: requestId
        });
      });
    }).then(function(res){
      if (res && res.chat && state.currentChat && res.chat.id !== state.currentChat.id) return;
      if (!receiveQueue(res)) return;
      if (res && res.chat) state.currentChat = res.chat;
      state.activeJob = res && res.job || null;
      renderMessages();
      syncComposerBusyUI();
      if (state.currentChat) startJobPolling(state.currentChat.id);
    }).catch(function(error){
      ta.value = rawTxt;
      state.pendingImages = imgs;
      state.pendingDocs = docs;
      renderAttachStrip(); persistDraft(true);
      notifyUser("Maestro", ui("Não foi possível guardar o pedido. O texto foi reposto.", "Could not save the request. Your text was restored."));
      if (state.currentChat) checkRunningJobForChat(state.currentChat.id);
    });
  }

  function handleComposerSubmit(overrideBusyMode) {
    var txt = (ta.value || "").trim();
    var docs = (state.pendingDocs || []).slice();
    if (!txt && !state.pendingImages.length && !docs.length) {
      ta.focus();
      return;
    }
    if (!txt && (docs.length || state.pendingImages.length)) {
      txt = docs.length ? "Analisa o conteúdo dos ficheiros que colei e diz-me o que encontras." : "Analisa a imagem anexada.";
    }
    var proj = document.getElementById("selProject").value || "nas";
    var eng = document.getElementById("selEngine").value || "auto";
    var imgs = state.pendingImages.slice();
    state.pendingImages = [];
    state.pendingDocs = [];
    renderAttachStrip();
    ta.value = ""; ta.style.height = "auto";
    clearDraft();

    if (isCurrentChatBusy()) {
      var modeToUse = overrideBusyMode || state.busyMode || "queue";
      if (modeToUse === "queue") {
        dispatchChatTurn(txt, imgs, proj, eng, false, docs);
        return;
      } else {
        dispatchChatTurn(txt, imgs, proj, eng, true, docs);
        return;
      }
    }
    dispatchChatTurn(txt, imgs, proj, eng, false, docs);
  }

  document.getElementById("composerForm").onsubmit = function(e){
    e.preventDefault();
    handleComposerSubmit();
  };

  /* ---- MODERN HOVER TOOLTIP ENGINE ---- */
  var tipEl = document.createElement("div");
  tipEl.id = "modernTooltip";
  tipEl.className = "modern-tooltip";
  tipEl.setAttribute("role", "tooltip");
  document.body.appendChild(tipEl);
  var activeTipTarget = null;

  var DEFAULT_TIPS = {
    "btnNewChat": "＋ Nova Conversa|Cria uma sessão limpa no projeto e motor selecionados.",
    "btnOpenSettings": "⚙️ Opções & Personalização|Muda idioma (PT/EN), cores de destaque, ícones unicolores e instaladores (.exe / Android).",
    "btnHeaderSettings": "⚙️ Opções & Personalização|Abre o painel de idioma, temas de cor, estilo de ícones e Auto-Updater.",
    "btnCleanup": "🧹 Limpar Sessões|Fecha processos temporários e limpa recursos órfãos do Maestro.",
    "btnToggleLeft": "☰ Menu & Pastas|Mostra ou recolhe a barra lateral de projetos e histórico de conversas.",
    "btnToggleRight": "📊 Painel Studio|Mostra ou recolhe o inspetor lateral (Quotas reais OAuth, Pulso NAS, Git & Frota).",
    "btnNotif": "🔔 Notificações Nativas|Ativa alertas sonoros e notificações quando o Maestro termina uma tarefa.",
    "selProject": "📁 Pasta / Projeto Ativo|Define o diretório de trabalho (NAS, Polistrat, SpaceBourne 2, DungenLabs…).",
    "selEngine": "🧠 Motor de IA|Escolhe Auto (Maestro Orquestra) ou fixa Antigravity, Claude Sonnet/Opus, Codex ou DeepSeek."
  };

  function resolveTipContent(el) {
    if (!el) return null;
    if (el.id === "btnSend" || el.id === "btnAltBusy") return composerSendTip(el.id === "btnAltBusy");
    var raw = el.getAttribute("data-tip");
    if (!raw && el.id && DEFAULT_TIPS[el.id]) raw = DEFAULT_TIPS[el.id];
    if (!raw && el.getAttribute("title")) {
      raw = el.getAttribute("title");
      el.setAttribute("data-tip", raw);
      el.removeAttribute("title");
    }
    if (!raw) {
      var mod = el.getAttribute("data-mod");
      if (mod === "chat") raw = "💬 Chat & Orquestrador|Conversa multimodal com visão, voz, fila de mensagens e orientação em tempo real.";
      else if (mod === "studio") raw = "🎨 Imagens AI, GIFs & Galeria|Gera imagens Flux HD, animações GIF e explora os teus screenshots com zoom.";
      else if (mod === "media") raw = "🎬 Media (Radarr/Sonarr) & Docker|Pesquisa filmes/séries para sacar em 1 clique e gere containers do NAS.";
      else if (mod === "casa") raw = "🏠 Casa, 3D & Schedulers|Monitoriza as impressoras Centauri Carbon 2 e Qidi Plus 4, tomadas e automações.";
      else if (mod === "artifacts") raw = "🧩 Biblioteca de Artifacts|Código, scripts C# e previews HTML/SVG guardados por pasta.";
    }
    return raw;
  }

  function showModernTip(target) {
    var tipText = resolveTipContent(target);
    if (!tipText) {
      hideModernTip();
      return;
    }
    activeTipTarget = target;
    tipEl.classList.toggle("activity-tooltip", target.matches(".live-work-head, .step-sub"));
    var parts = tipText.split("|");
    if (parts.length > 1) {
      tipEl.innerHTML = '<span class="tt-title">' + esc(parts[0]) + '</span><span class="tt-desc">' + esc(parts.slice(1).join("|")) + '</span>';
    } else {
      tipEl.innerHTML = '<span class="tt-title">' + esc(parts[0]) + '</span>';
    }
    var rect = target.getBoundingClientRect();
    tipEl.classList.add("show");
    var tw = tipEl.offsetWidth || 220;
    var th = tipEl.offsetHeight || 48;
    var left = Math.max(10, Math.min(window.innerWidth - tw - 10, rect.left + rect.width / 2 - tw / 2));
    var top = (rect.top > th + 16) ? (rect.top - th - 9) : (rect.bottom + 9);
    tipEl.style.left = Math.round(left) + "px";
    tipEl.style.top = Math.round(top) + "px";
  }
  function hideModernTip() {
    activeTipTarget = null;
    tipEl.classList.remove("show");
  }
  var tipTimer = null;
  document.addEventListener("mouseover", function(e){
    if (window.innerWidth <= 820) return;
    var target = e.target.closest ? e.target.closest("button, a, select, [data-tip], [title]") : null;
    clearTimeout(tipTimer);
    /* Botões só com ícone: todos os tooltips aparecem após ~1,2 s de hover. */
    if (!target) hideModernTip();
    else tipTimer = setTimeout(function(){ showModernTip(target); }, 1200);
  });
  document.addEventListener("mouseout", function(e){
    if (activeTipTarget && (!e.relatedTarget || !activeTipTarget.contains(e.relatedTarget))) hideModernTip();
    if (!e.relatedTarget || !e.target.contains || !e.target.contains(e.relatedTarget)) clearTimeout(tipTimer);
  });
  document.addEventListener("focusin", function(e){
    if (e.target.matches(".live-work-head, .nav-mod-btn, #btnSend, #btnAltBusy, .composer-chip, .composer-quota")) {
      e.target.setAttribute("aria-describedby", "modernTooltip");
      showModernTip(e.target);
    }
  });
  document.addEventListener("focusout", function(e){
    if (e.target.matches(".live-work-head, .nav-mod-btn, #btnSend, #btnAltBusy, .composer-chip, .composer-quota")) e.target.removeAttribute("aria-describedby");
    hideModernTip();
  });
  document.addEventListener("keydown", function(e){ if (e.key === "Escape") hideModernTip(); });
  document.addEventListener("scroll", hideModernTip, true);
  document.addEventListener("mousedown", hideModernTip);

  document.getElementById("btnCleanup").onclick = function(){
    api("/api/cleanup", {}).then(function(r){
      notifyUser("🧹 Maestro", (state.lang === "en" ? "Temporary sessions cleaned (" : "Sessões temporárias limpas (") + (r.cleaned_sessions || []).length + ").");
    });
  };
  document.getElementById("btnInstallPwa").onclick = function(){
    if (isStandaloneApp()) {
      notifyUser("📲 Maestro AI", state.lang === "en" ? "You are already using the installed app." : "Já estás a usar a app instalada.");
      return;
    }
    if (!window.isSecureContext) {
      window.open("https://maestro.hubertdungen.com/", "_blank", "noopener,noreferrer");
      return;
    }
    var promptEvent = state.deferredPrompt;
    if (promptEvent) {
      state.deferredPrompt = null;
      try {
        var promptResult = promptEvent.prompt();
        if (promptResult && promptResult.catch) promptResult.catch(installPromptFailed);
        if (promptEvent.userChoice && promptEvent.userChoice.then) {
          promptEvent.userChoice.then(function(choice){
            if (choice && choice.outcome === "accepted") {
              notifyUser("📲 Maestro AI", state.lang === "en" ? "Installing app…" : "A instalar a app…");
            } else {
              syncInstallButton();
            }
          }).catch(installPromptFailed);
        }
      } catch (e) {
        installPromptFailed(e);
      }
      return;
    }
    var ua = navigator.userAgent || "";
    var android = /Android/i.test(ua);
    var ios = /iPhone|iPad|iPod/i.test(ua);
    var msg;
    if (state.lang === "en") {
      msg = android
        ? "📲 Chrome on Android: tap ⋮ → «Install app» (or «Add to Home screen»). If «Install app» is unavailable, open the site over HTTPS (maestro.hubertdungen.com)."
        : ios
          ? "📲 On iPhone/iPad: open in Safari → Share → «Add to Home Screen»."
          : "📲 In Chrome/Edge: use the install icon in the address bar, or the browser menu → «Install Maestro AI».";
    } else {
      msg = android
        ? "📲 Chrome no Android: toca em ⋮ → «Instalar aplicação» (ou «Adicionar ao ecrã principal»). Se «Instalar aplicação» não aparecer, abre o site por HTTPS (maestro.hubertdungen.com)."
        : ios
          ? "📲 No iPhone/iPad: abre no Safari → Partilhar → «Adicionar ao ecrã principal»."
          : "📲 No Chrome/Edge: usa o ícone de instalação na barra de endereço, ou o menu do browser → «Instalar Maestro AI».";
    }
    alert(msg);
  };

  /* ---- SVG UNICOLOR ICON LIBRARY & ICON STYLE SWITCHER ---- */
  var SVG_ICONS = {
    check: '<svg class="svg-ico" viewBox="0 0 24 24"><path d="M4 12l5 5L20 6"/></svg>',
    folder: '<svg class="svg-ico" viewBox="0 0 24 24"><path class="d2" d="M3 7V5a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z"/><path d="M3 9h18"/></svg>',
    pin: '<svg class="svg-ico" viewBox="0 0 24 24"><path class="d2" d="M8 3h8l-1 7 4 4v2H5v-2l4-4z"/><path d="M12 16v6"/></svg>',
    archive: '<svg class="svg-ico" viewBox="0 0 24 24"><rect class="d2" x="3" y="3" width="18" height="5" rx="1"/><path d="M5 8v13h14V8M10 12h4"/></svg>',
    restore: '<svg class="svg-ico" viewBox="0 0 24 24"><path d="M4 10h10a6 6 0 0 1 0 12M4 10l5-5M4 10l5 5"/></svg>',
    globe: '<svg class="svg-ico" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><ellipse cx="12" cy="12" rx="4" ry="9"/><path d="M3 12h18"/></svg>',
    diamond: '<svg class="svg-ico" viewBox="0 0 24 24"><polygon points="3 8 7 3 17 3 21 8 12 21"/><path d="M3 8h18M7 3l5 18 5-18"/></svg>',
    palette: '<svg class="svg-ico" viewBox="0 0 24 24"><path class="d2" d="M12 3a9 9 0 1 0 0 18h1a2 2 0 0 0 1-4c-1-1 0-3 2-3h2c5 0 3-11-6-11z"/><path d="M7 10h.01M10 6h.01M15 6h.01M18 10h.01"/></svg>',
    matrix: '<svg class="svg-ico" viewBox="0 0 24 24"><rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 7v4M12 5v9M16 9v8M8 15v3M12 18v1"/></svg>',
    satellite: '<svg class="svg-ico" viewBox="0 0 24 24"><circle cx="15" cy="9" r="3"/><path d="M3 3l5 5M16 16l5 5M3 16l5 5 3-3-5-5zM13 6l-2-2 3-3 5 5-2 2M4 11a9 9 0 0 0 9 9M4 15a5 5 0 0 0 5 5"/></svg>',
    city: '<svg class="svg-ico" viewBox="0 0 24 24"><path class="d2" d="M3 21V9h6v12M9 21V3h7v18M16 21V12h5v9"/><path d="M12 7h1M12 11h1M12 15h1M5 13h1M18 16h1"/></svg>',
    temple: '<svg class="svg-ico" viewBox="0 0 24 24"><polygon points="12 3 2 8 22 8"/><path d="M3 21h18M4 18h16M6 10v8M12 10v8M18 10v8"/></svg>',
    desert: '<svg class="svg-ico" viewBox="0 0 24 24"><circle cx="17" cy="6" r="3"/><path class="d2" d="M2 21l7-11 7 11z"/><path d="M14 18l3-5 5 8"/></svg>',
    rock: '<svg class="svg-ico" viewBox="0 0 24 24"><polygon points="8 3 17 5 22 15 16 21 5 20 2 11"/><path d="M8 3l3 9 11 3M11 12l-6 8"/></svg>',
    brain: '<svg class="svg-ico" viewBox="0 0 24 24"><path class="d2" d="M12 5C8 0 3 4 4 8c-4 2-3 7 0 8-1 5 6 7 8 3 2 4 9 2 8-3 3-1 4-6 0-8 1-4-4-8-8-3z"/><path d="M12 5v14M7 8l2 3-2 3M17 8l-2 3 2 3"/></svg>',
    heart: '<svg class="svg-ico" viewBox="0 0 24 24"><path class="d2" d="M12 21L3 12C-2 4 7-1 12 6c5-7 14-2 9 6z"/></svg>',
    flame: '<svg class="svg-ico" viewBox="0 0 24 24"><path class="d2" d="M13 2c1 7 8 7 7 13a8 8 0 0 1-16 0c0-4 3-7 5-9 0 4 2 5 2 5s3-4 2-9z"/></svg>',
    crown: '<svg class="svg-ico" viewBox="0 0 24 24"><path class="d2" d="M3 5l5 5 4-7 4 7 5-5-3 14H6z"/><path d="M6 22h12"/></svg>',
    leaf: '<svg class="svg-ico" viewBox="0 0 24 24"><path class="d2" d="M20 3C3 1 0 18 9 20 20 23 22 10 20 3z"/><path d="M4 22L16 8"/></svg>',
    refresh: '<svg class="svg-ico" viewBox="0 0 24 24"><path d="M3 10a9 9 0 0 1 15-6l3 3M21 3v4h-4M21 14a9 9 0 0 1-15 6l-3-3M3 21v-4h4"/></svg>',
    send: '<svg class="svg-ico" viewBox="0 0 24 24"><path class="d2" d="M3 3l19 9-19 9 4-9z"/><path d="M7 12h15"/></svg>',
    search: '<svg class="svg-ico" viewBox="0 0 24 24"><circle class="d2" cx="10" cy="10" r="7"/><path d="M15 15l7 7"/></svg>',
    download: '<svg class="svg-ico" viewBox="0 0 24 24"><path d="M12 3v12M7 10l5 5 5-5"/><path class="d2" d="M3 16v5h18v-5"/></svg>',
    stop: '<svg class="svg-ico" viewBox="0 0 24 24"><rect class="d2" x="5" y="5" width="14" height="14" rx="2"/></svg>',
    battery: '<svg class="svg-ico" viewBox="0 0 24 24"><rect class="d2" x="2" y="6" width="17" height="12" rx="2"/><path d="M22 10v4M6 10v4M10 10v4M14 10v4"/></svg>',
    warning: '<svg class="svg-ico" viewBox="0 0 24 24"><path class="d2" d="M12 3L2 21h20z"/><path d="M12 9v5M12 17h.01"/></svg>',
    hourglass: '<svg class="svg-ico" viewBox="0 0 24 24"><path class="d2" d="M6 3h12v4l-6 5 6 5v4H6v-4l6-5-6-5z"/><path d="M4 3h16M4 21h16"/></svg>',
    chat: '<svg class="svg-ico" viewBox="0 0 24 24"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/></svg>',
    studio: '<svg class="svg-ico" viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/></svg>',
    media: '<svg class="svg-ico" viewBox="0 0 24 24"><rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4M10 7l6 3-6 3V7z"/></svg>',
    casa: '<svg class="svg-ico" viewBox="0 0 24 24"><path d="M3 10.5L12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1v-9.5z"/></svg>',
    artifacts: '<svg class="svg-ico" viewBox="0 0 24 24"><polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/></svg>',
    settings: '<svg class="svg-ico" viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9c.26.604.852.997 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>',
    clean: '<svg class="svg-ico" viewBox="0 0 24 24"><path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6h14z"/></svg>',
    notif: '<svg class="svg-ico" viewBox="0 0 24 24"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>',
    panel: '<svg class="svg-ico" viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="2"/><line x1="15" y1="3" x2="15" y2="21"/></svg>',
    camera: '<svg class="svg-ico" viewBox="0 0 24 24"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/></svg>',
    mic: '<svg class="svg-ico" viewBox="0 0 24 24"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2M12 19v4M8 23h8"/></svg>',
    sparkle: '<svg class="svg-ico" viewBox="0 0 24 24"><path d="M12 3l1.9 5.8a2 2 0 0 0 1.3 1.3L21 12l-5.8 1.9a2 2 0 0 0-1.3 1.3L12 21l-1.9-5.8a2 2 0 0 0-1.3-1.3L3 12l5.8-1.9a2 2 0 0 0 1.3-1.3L12 3z"/></svg>',
    quotas: '<svg class="svg-ico" viewBox="0 0 24 24"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>',
    pulso: '<svg class="svg-ico" viewBox="0 0 24 24"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>',
    git: '<svg class="svg-ico" viewBox="0 0 24 24"><circle cx="18" cy="18" r="3"/><circle cx="6" cy="6" r="3"/><path d="M6 21V9a9 9 0 0 0 9 9"/></svg>',
    fleet: '<svg class="svg-ico" viewBox="0 0 24 24"><rect x="3" y="11" width="18" height="10" rx="2"/><circle cx="12" cy="5" r="2"/><path d="M12 7v4M8 16h.01M16 16h.01"/></svg>',
    copy: '<svg class="svg-ico" viewBox="0 0 24 24"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1"/></svg>',
    canvas: '<svg class="svg-ico" viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M13 9v11"/></svg>',
    nas: '<svg class="svg-ico" viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="6" rx="1.5"/><rect x="3" y="14" width="18" height="6" rx="1.5"/><path d="M7 7h.01M7 17h.01M12 7h5M12 17h5"/></svg>',
    models: '<svg class="svg-ico" viewBox="0 0 24 24"><path d="M4 7h10M18 7h2M4 17h2M10 17h10"/><circle cx="16" cy="7" r="2"/><circle cx="8" cy="17" r="2"/></svg>',
    queue: '<svg class="svg-ico" viewBox="0 0 24 24"><path d="M3 13l3-8h12l3 8v6a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z"/><path d="M3 13h5l1 3h6l1-3h5"/></svg>',
    steer: '<svg class="svg-ico" viewBox="0 0 24 24"><circle class="d2" cx="12" cy="12" r="9"/><path d="M15.5 8.5l-2 5-5 2 2-5z"/></svg>',
    bolt: '<svg class="svg-ico" viewBox="0 0 24 24"><path class="d2" d="M13 2L4 14h7l-1 8 9-12h-7z"/></svg>',
    close: '<svg class="svg-ico" viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg>',
    edit: '<svg class="svg-ico" viewBox="0 0 24 24"><path class="d2" d="M4 16l-1 5 5-1L20 8l-4-4z"/><path d="M14 6l4 4M4 16l4 4M16 4l2-2 4 4-2 2"/></svg>',
    dots: '<svg class="svg-ico svg-dots" viewBox="0 0 24 24"><circle cx="5" cy="12" r="1.9"/><circle cx="12" cy="12" r="1.9"/><circle cx="19" cy="12" r="1.9"/></svg>'
  };
  /* Ícone nos três estilos: SVG (unicolor/duotone) ou emoji (colorido), alternado só por CSS. */
  function dualIco(name, emoji) {
    return '<span class="ico-svg" data-ico="' + name + '" aria-hidden="true">' + ((typeof SVG_ICONS !== 'undefined' && SVG_ICONS[name]) || '') + '</span><span class="ico-emoji" aria-hidden="true">' + emoji + '</span>';
  }

  function projectIco(emoji) {
    var name = {"🧠":"brain", "⚡":"bolt", "🌐":"globe", "🏠":"casa", "🎨":"palette", "🤖":"fleet", "🖥️":"nas", "💻":"nas", "🎮":"studio"}[emoji] || "folder";
    return dualIco(name, esc(emoji || "📁"));
  }

  /* Only decorate interface text. Messages, code and editable values stay intact. */
  function decorateInterfaceIcons(root) {
    if (!root || !root.isConnected) return;
    var names = {"↗":"send", "🔍":"search", "🔎":"search", "🖼":"studio", "⬇":"download", "⏹":"stop", "❌":"close", "❎":"close", "✖":"close", "✔":"check", "💾":"nas", "📄":"canvas", "📦":"archive", "🚀":"send", "💰":"diamond", "🛠":"settings", "🔧":"settings", "💻":"nas", "🖥":"nas", "📲":"panel", "✅":"check", "🇵🇹":"globe", "🇬🇧":"globe", "⚙":"settings", "🌐":"globe", "🌍":"globe", "✨":"sparkle", "💎":"diamond", "🎨":"palette", "🟩":"matrix", "🛰":"satellite", "🌆":"city", "🏛":"temple", "🏜":"desert", "🪨":"rock", "🌌":"artifacts", "🌲":"leaf", "🔥":"flame", "👑":"crown", "🖤":"heart", "⚡":"bolt", "🔄":"refresh", "↻":"refresh", "↺":"refresh", "🔋":"battery", "♊":"brain", "⚠":"warning", "⏳":"hourglass", "⌛":"hourglass", "🔔":"notif", "🔕":"notif", "📁":"folder", "🗂":"folder", "📂":"folder", "🗃":"archive", "🤖":"fleet", "🧠":"brain", "🎭":"brain", "🔮":"diamond", "📊":"quotas", "📈":"quotas", "📉":"quotas", "📋":"copy", "📷":"camera", "🎤":"mic", "💬":"chat", "🧩":"artifacts", "🏠":"casa", "🎬":"media", "📥":"queue", "🧭":"steer", "🧹":"clean", "📌":"pin", "💓":"pulso"};
    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT), nodes = [], node;
    while ((node = walker.nextNode())) {
      var parent = node.parentElement;
      if (parent.closest('.ico-svg, .ico-emoji, svg, script, style, pre, code, input, textarea, select, [contenteditable], .chat-row-title, .fname')) continue;
      if (parent.closest('.chat-scroll') && !parent.closest('button')) continue;
      if (/\p{Extended_Pictographic}|\p{Regional_Indicator}|[↻↺]/u.test(node.nodeValue)) nodes.push(node);
    }
    nodes.forEach(function(text) {
      var value = text.nodeValue, re = /\p{Regional_Indicator}{2}|\p{Extended_Pictographic}(?:\uFE0F|[\u{1F3FB}-\u{1F3FF}])?(?:\u200D\p{Extended_Pictographic}\uFE0F?)*|[↻↺]/gu;
      var fragment = document.createDocumentFragment(), last = 0, match;
      while ((match = re.exec(value))) {
        fragment.appendChild(document.createTextNode(value.slice(last, match.index)));
        var icon = document.createElement('span');
        icon.className = 'interface-icon';
        icon.innerHTML = dualIco(names[match[0].replace(/\uFE0F/g, '')] || 'sparkle', esc(match[0]));
        fragment.appendChild(icon); last = re.lastIndex;
      }
      fragment.appendChild(document.createTextNode(value.slice(last)));
      text.replaceWith(fragment);
    });
  }
  function decorateSettingsIcons() { decorateInterfaceIcons(document.getElementById('settingsModal')); }

  function observeInterfaceIcons() {
    decorateInterfaceIcons(document.body);
    var pending = new Set(), scheduled = false;
    var observer = new MutationObserver(function(records) {
      records.forEach(function(record) {
        if (record.target.nodeType === 1 && record.target.closest('.interface-icon, .ico-svg, .ico-emoji, svg')) return;
        if (record.type === 'characterData') pending.add(record.target.parentElement);
        else record.addedNodes.forEach(function(node) { pending.add(node.nodeType === 1 ? node : node.parentElement); });
      });
      if (!pending.size || scheduled) return;
      scheduled = true;
      requestAnimationFrame(function() {
        scheduled = false;
        var roots = Array.from(pending); pending.clear();
        roots.forEach(function(root) {
          if (root && !roots.some(function(other) { return other && other !== root && other.contains(root); })) decorateInterfaceIcons(root);
        });
      });
    });
    observer.observe(document.body, {childList:true, subtree:true, characterData:true});
  }

  var EMOJI_ICONS = {
    chat: "💬", studio: "🎨", media: "🎬", casa: "🏠", artifacts: "🧩"
  };

  /* ---- SETTINGS MODAL, LANGUAGE (PT/EN) & THEME/COLOR CUSTOMIZER ---- */
  var THEME_PRESETS = {
    cyber:   { cyan: "#38bdf8", violet: "#a855f7", bg: "#050811", panel: "#0a0f1d", card: "#10182d" },
    emerald: { cyan: "#10b981", violet: "#06b6d4", bg: "#040d0a", panel: "#071712", card: "#0c241c" },
    solar:   { cyan: "#f59e0b", violet: "#ef4444", bg: "#0f0906", panel: "#18100b", card: "#241810" },
    opus:    { cyan: "#c084fc", violet: "#ec4899", bg: "#090514", panel: "#120a24", card: "#1b1035" },
    oled:    { cyan: "#38bdf8", violet: "#a855f7", bg: "#000000", panel: "#050508", card: "#0c0c12" },
    crimson: { cyan: "#fb7185", violet: "#f43f5e", bg: "#0d0408", panel: "#170910", card: "#240f19" }
  };

  var I18N = {
    pt: {
      settings_btn: "Opções",
      clean_btn: "Limpar",
      panel_btn: "Painel",
      settings_title: "⚙️ Opções, Idioma, Cores & Instaladores",
      sec_lang: "🌐 IDIOMA DA INTERFACE / LANGUAGE",
      sec_icons: "✨ ESTILO DE ÍCONES (UNICOLOR / DUOTONE / COLORIDO)",
      sec_theme: "🎨 TEMAS & PERSONALIZADOR DE CORES",
      reset_theme: "↺ Repor Padrão",
      col_primary: "Cor Principal",
      col_secondary: "Brilho / Glow",
      col_bg: "Fundo App",
      col_panel: "Painéis / Cartões",
      ui_scale_lbl: "Escala da Interface / Texto:",
      sec_updater: "🔄 AUTO-UPDATER & INSTALADORES DA APP",
      updater_desc: "Atualiza automaticamente a interface e o .exe sempre que o Maestro evolui no servidor.",
      check_upd_btn: "Verificar",
      refresh_app: "Atualizar app",
      pull_refresh: "Puxa para atualizar",
      install_desc: "Instala o Maestro AI Studio como aplicação nativa no teu Windows (ecrãs grandes) ou no telemóvel/tablet Android & iOS.",
      dl_exe_btn: "Instalar no Windows (atalho)",
      dl_pwa_btn: "Instalar App Android / Tablet",
      modal_folder_title: "🎨 Nova Pasta / Projeto Colorido",
      save_folder_btn: "Guardar Pasta Colorida",
      nav_chat: "Chat", nav_studio: "Galeria", nav_media: "Filmes", nav_casa: "Casa/HA", nav_artifacts: "Artifacts",
      new_chat: "＋ Nova Conversa",
      folders_hdr: "PASTAS & PROJETOS",
      new_folder_btn: "＋ Pasta",
      search_ph: "Procurar conversas ou pastas…",
      prompt_ph: "Diz ao Maestro o que queres fazer (ex: 'mete o filme Dune a sacar', 'cria script C#', 'estado da Qidi')…",
      lbl_attach: "Imagem", lbl_voice: "Voz", lbl_enhance: "Melhorar", btn_send: "Enviar ↗",
      itab_quotas: "Quotas", itab_pulso: "Pulso", itab_git: "Git/Files", itab_fleet: "Frota"
    },
    en: {
      settings_btn: "Settings",
      clean_btn: "Cleanup",
      panel_btn: "Inspector",
      settings_title: "⚙️ Settings, Language, Colors & Installers",
      sec_lang: "🌐 INTERFACE LANGUAGE / IDIOMA",
      sec_icons: "✨ ICON STYLE (UNICOLOR / DUOTONE / COLOR)",
      sec_theme: "🎨 THEMES & COLOR CUSTOMIZER",
      reset_theme: "↺ Reset Default",
      col_primary: "Primary Accent",
      col_secondary: "Secondary Glow",
      col_bg: "App Background",
      col_panel: "Panels / Cards",
      ui_scale_lbl: "UI & Font Scale:",
      sec_updater: "🔄 LIVE AUTO-UPDATER & APP INSTALLERS",
      updater_desc: "Automatically updates the UI and Windows .exe whenever Maestro evolves on the server.",
      check_upd_btn: "Check Now",
      refresh_app: "Update app",
      pull_refresh: "Pull to refresh",
      install_desc: "Install Maestro AI Studio as a native desktop app on Windows (widescreen) or on your Android / iOS phone & tablet.",
      dl_exe_btn: "Install on Windows (shortcut)",
      dl_pwa_btn: "Install Android / Tablet App",
      modal_folder_title: "🎨 New Colored Folder / Project",
      save_folder_btn: "Save Colored Folder",
      nav_chat: "Chat", nav_studio: "Gallery", nav_media: "Media", nav_casa: "Home/HA", nav_artifacts: "Artifacts",
      new_chat: "＋ New Chat",
      folders_hdr: "FOLDERS & PROJECTS",
      new_folder_btn: "＋ Folder",
      search_ph: "Search chats or folders…",
      prompt_ph: "Ask Maestro anything (e.g., 'download Dune in Radarr', 'inspect C# script', 'check Qidi status')…",
      lbl_attach: "Image", lbl_voice: "Voice", lbl_enhance: "Enhance", btn_send: "Send ↗",
      itab_quotas: "Quotas", itab_pulso: "Pulse", itab_git: "Git/Files", itab_fleet: "Fleet"
    }
  };

  function savePrefs(prefs) {
    try { localStorage.setItem("maestro_prefs_v3", JSON.stringify(prefs)); } catch(e){}
  }
  function loadPrefs() {
    try { return JSON.parse(localStorage.getItem("maestro_prefs_v3") || "{}"); } catch(e){ return {}; }
  }

  /* Cores por mundo: cada mundo traz a sua cor no CSS; só o que o utilizador mudar
     é guardado (prefs.worldTheme[mundo]) e aplicado por cima. Repor limpa o mundo ativo. */
  var THEME_KEYS = ["cyan", "violet", "bg", "panel", "card"];
  function worldKey() { return state.world || "default"; }
  function cssToHex(v) {
    v = (v || "").trim();
    var m = v.match(/^#([0-9a-f]{3})$/i);
    if (m) return "#" + m[1].split("").map(function(c){ return c + c; }).join("");
    if (/^#[0-9a-f]{6}$/i.test(v)) return v;
    m = v.match(/rgba?\(\s*(\d+)[\s,]+(\d+)[\s,]+(\d+)/);
    return m ? "#" + [m[1], m[2], m[3]].map(function(n){ return ("0" + (+n).toString(16)).slice(-2); }).join("") : null;
  }
  function syncThemeInputs() {
    var cs = getComputedStyle(document.documentElement);
    [["colCyan", "--cyan"], ["colViolet", "--violet"], ["colBg", "--bg"], ["colPanel", "--panel"]].forEach(function(p){
      var hex = cssToHex(cs.getPropertyValue(p[1]));
      var el = document.getElementById(p[0]);
      if (hex && el) el.value = hex;
    });
  }
  function clearThemeColors() {
    THEME_KEYS.forEach(function(k){ document.documentElement.style.removeProperty("--" + k); });
  }
  function applyThemeColors(t, skipSave) {
    var root = document.documentElement;
    if (t.cyan) root.style.setProperty("--cyan", t.cyan);
    if (t.violet) root.style.setProperty("--violet", t.violet);
    if (t.bg) root.style.setProperty("--bg", t.bg);
    if (t.panel) {
      root.style.setProperty("--panel", t.panel);
      root.style.setProperty("--card", t.card || t.panel);
    }
    if (t.scale) { root.style.fontSize = t.scale; }
    syncThemeInputs();
    if (skipSave) return;
    var prefs = loadPrefs(), changed = {};
    THEME_KEYS.forEach(function(k){ if (t[k]) changed[k] = t[k]; });
    if (t.panel && !t.card) changed.card = t.panel;
    if (Object.keys(changed).length) {
      prefs.worldTheme = prefs.worldTheme || {};
      prefs.worldTheme[worldKey()] = Object.assign(prefs.worldTheme[worldKey()] || {}, changed);
    }
    if (t.scale) prefs.theme = Object.assign(prefs.theme || {}, { scale: t.scale });
    savePrefs(prefs);
  }
  /* Aplica (só) as cores personalizadas do mundo ativo; sem personalização vale o CSS do mundo. */
  function loadWorldTheme() {
    clearThemeColors();
    var own = (loadPrefs().worldTheme || {})[worldKey()];
    if (own) applyThemeColors(own, true); else syncThemeInputs();
  }
  function resetThemeColors() {
    var prefs = loadPrefs();
    if (prefs.worldTheme) delete prefs.worldTheme[worldKey()];
    savePrefs(prefs);
    clearThemeColors();
    applyThemeColors({ scale: "15.5px" });
  }

  function applyIconStyle(styleName) {
    state.iconStyle = styleName || "unicolor";
    document.documentElement.setAttribute("data-icon-style", state.iconStyle);
    var useSvg = (state.iconStyle === "unicolor" || state.iconStyle === "duotone");
    var dict = I18N[state.lang || "pt"];

    // Module navigation buttons (sidebar + mobile bottom nav)
    document.querySelectorAll("[data-mod]").forEach(function(btn){
      var m = btn.getAttribute("data-mod");
      var icoSpan = btn.querySelector(".ico");
      if (icoSpan) {
        icoSpan.innerHTML = dualIco(m, EMOJI_ICONS[m] || "💬");
      }
    });

    // Header & sidebar buttons
    // Botões só com ícone (nome no tooltip e em aria-label); SVG/emoji alternados por CSS.
    [["btnOpenSettings", "settings", "⚙️"], ["btnCleanup", "clean", "🧹"], ["btnNasLink", "nas", "🖥️"],
     ["btnNotif", "notif", "🔔"], ["btnCopyCanvas", "copy", "📋"], ["btnComposerMore", "dots", "⋯"]].forEach(function(b){
      var el = document.getElementById(b[0]);
      if (el) el.innerHTML = dualIco(b[1], b[2]);
    });
    var btnHdrSet = document.getElementById("btnHeaderSettings");
    if (btnHdrSet) btnHdrSet.innerHTML = dualIco("settings", "⚙️");
    var btnRight = document.getElementById("btnToggleRight");
    if (btnRight) {
      var badgeHtml = document.getElementById("headerPulseBadge") ? document.getElementById("headerPulseBadge").outerHTML : '<span id="headerPulseBadge"></span>';
      btnRight.innerHTML = dualIco("panel", "📊") + " " + '<span data-i18n="panel_btn">' + esc(dict.panel_btn) + '</span> ' + badgeHtml;
    }

    // Composer buttons
    document.getElementById("btnAttach").innerHTML = (useSvg ? SVG_ICONS.camera + " " : "📷 ") + esc(dict.lbl_attach);
    if (!state.recording) {
      document.getElementById("btnVoice").innerHTML = (useSvg ? SVG_ICONS.mic + " " : "🎤 ") + esc(dict.lbl_voice);
    }
    document.getElementById("btnEnhance").innerHTML = (useSvg ? SVG_ICONS.sparkle + " " : "✨ ") + esc(dict.lbl_enhance);
    syncAutoEnhanceButton();

    // Inspector tabs
    var itabMap = { quotas: [SVG_ICONS.quotas, "📊", dict.itab_quotas], pulso: [SVG_ICONS.pulso, "💓", dict.itab_pulso], git: [SVG_ICONS.git, "📁", dict.itab_git], fleet: [SVG_ICONS.fleet, "🤖", dict.itab_fleet] };
    document.querySelectorAll("[data-itab]").forEach(function(tab){
      var k = tab.getAttribute("data-itab");
      if (itabMap[k]) {
        tab.innerHTML = dualIco(k, itabMap[k][1]) + " " + esc(itabMap[k][2]);
      }
    });

    document.querySelectorAll("#iconStyleGrid [data-icostyle]").forEach(function(b){
      b.classList.toggle("primary", b.getAttribute("data-icostyle") === state.iconStyle);
    });

    decorateSettingsIcons();
    var prefs = loadPrefs();
    prefs.iconStyle = state.iconStyle;
    savePrefs(prefs);
  }

  function applyLanguage(lang) {
    state.lang = (lang === "en") ? "en" : "pt";
    document.documentElement.lang = state.lang;
    var dict = I18N[state.lang];
    document.querySelectorAll("[data-i18n]").forEach(function(el){
      var k = el.getAttribute("data-i18n");
      if (dict[k]) el.textContent = dict[k];
    });
    var navMap = { chat: dict.nav_chat, studio: dict.nav_studio, media: dict.nav_media, casa: dict.nav_casa, artifacts: dict.nav_artifacts };
    document.querySelectorAll("[data-mod]").forEach(function(btn){
      var label = btn.querySelector(".ico + span");
      var m = btn.getAttribute("data-mod");
      if (label && navMap[m]) label.textContent = navMap[m];
      if (navMap[m]) btn.setAttribute("aria-label", navMap[m]);
    });
    var newChatSpans = document.querySelectorAll("#btnNewChat span");
    if (newChatSpans.length) newChatSpans[0].textContent = dict.new_chat;
    var secLbl = document.querySelector(".sec-label-row span");
    if (secLbl) secLbl.textContent = dict.folders_hdr;
    var btnNF = document.getElementById("btnNewFolder");
    if (btnNF) btnNF.textContent = dict.new_folder_btn;
    document.getElementById("searchBox").placeholder = dict.search_ph;
    document.getElementById("promptInput").placeholder = dict.prompt_ph;
    document.getElementById("btnSend").textContent = dict.btn_send;

    document.getElementById("btnLangPt").classList.toggle("active", state.lang === "pt");
    document.getElementById("btnLangEn").classList.toggle("active", state.lang === "en");
    if (state.recognition) state.recognition.lang = (state.lang === "en" ? "en-US" : "pt-PT");

    var prefs = loadPrefs();
    prefs.lang = state.lang;
    savePrefs(prefs);
    applyIconStyle(state.iconStyle || prefs.iconStyle || "unicolor");
    if (typeof applyLessOutlines === "function") applyLessOutlines(document.documentElement.hasAttribute("data-less-outlines"));
    document.querySelectorAll("[data-chat-label]").forEach(function(el){
      var labels = [["⬇ Ir para o fim", "⬇ Jump to latest"], ["Ferramentas do pedido", "Request tools"], ["Preferências e ações", "Preferences and actions"]];
      var pair = labels[Number(el.getAttribute("data-chat-label"))];
      el.textContent = ui(pair[0], pair[1]);
    });
    var rb = document.getElementById("btnRefreshApp");
    if (rb && !rb.classList.contains("has-update")) {
      rb.title = ui("Atualizar app", "Update app");
      rb.setAttribute("aria-label", ui("Atualizar app", "Update app"));
    }
    var rdb = document.getElementById("btnRefreshDrawer");
    if (rdb && !rdb.classList.contains("has-update")) {
      rdb.setAttribute("aria-label", ui("Verificar e instalar atualização da app", "Check for and install an app update"));
    }
    var tips = {
      btnComposerMore: "More request options", composerMenu: "Request options",
      btnOpenSettings: "⚙️ Settings|Language, colors, icon style and installers.",
      btnCleanup: "🧹 Cleanup|Close temporary processes and free orphaned Maestro resources.",
      btnNasLink: "🖥️ NAS|Open the Pavilion-NAS terminals in a new tab.",
      btnCopyCanvas: "Copy|Copy the canvas code to the clipboard.",
      btnStopJob: "Stop task", btnJumpEnd: "Jump to latest",
      btnAttach: "📷 Attach image|Add images/screenshots to the request (or paste with Ctrl+V).",
      btnVoice: "🎤 Voice dictation|Dictate in the selected interface language.",
      btnEnhance: "✨ Enhance prompt|Add technical context from the current project.",
      btnAutoEnhance: "Auto-✨ On send|Automatically enhance short requests when sending.",
      btnChatStyle: "Chat appearance|Detect code or prose automatically. Click to cycle: Auto → Code → Prose.",
      btnAltBusy: "Alternative action|Use the opposite action: steer now or queue a message.",
      btnSend: "Send request (Enter)|Send to Maestro, queue a message, or steer while running."
    };
    Object.keys(tips).forEach(function(id){
      var el = document.getElementById(id);
      if (!el) return;
      ["title", "aria-label", "data-tip"].forEach(function(attr){
        if (!el.hasAttribute(attr)) return;
        var saved = "data-pt-" + attr;
        if (!el.hasAttribute(saved)) el.setAttribute(saved, el.getAttribute(attr));
        el.setAttribute(attr, ui(el.getAttribute(saved), tips[id]));
      });
    });
    applyChatStyle();
    syncHomeLanguage();
    var scroll = document.getElementById("chatScroll");
    var top = scroll.scrollTop, follow = scroll.__follow;
    if (_liveEl) { _liveEl.remove(); _liveEl = null; }
    renderChatList();
    renderMessages();
    syncBusyModeButton();
    renderQueueStrip();
    syncInstallButton();
    scroll.scrollTop = top; scroll.__follow = follow;
  }

  /* ---- MUNDOS VISUAIS (matrix / scifi / cyberpunk / hesperia / dune / dungen) ---- */
  var _rainRAF = null;
  function stopMatrixRain() {
    if (_rainRAF) { cancelAnimationFrame(_rainRAF); _rainRAF = null; }
    var cv = document.querySelector(".world-bg-canvas");
    if (cv) { var c = cv.getContext("2d"); c.clearRect(0, 0, cv.width, cv.height); }
  }
  function startMatrixRain() {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    var cv = document.querySelector(".world-bg-canvas");
    if (!cv) return;
    var ctx = cv.getContext("2d");
    function size() { cv.width = window.innerWidth; cv.height = window.innerHeight; }
    size();
    window.addEventListener("resize", size);
    var font = 15;
    var nCols = Math.max(1, Math.floor(cv.width / font));
    var drops = [];
    for (var i = 0; i < nCols; i++) drops.push(Math.random() * cv.height);
    var glyphs = "01アイウエオカキクケコサシスセソタチツテトナニヌネノハヒフヘホマミムメモ".split("");
    function frame() {
      ctx.fillStyle = "rgba(2,8,4,0.13)";
      ctx.fillRect(0, 0, cv.width, cv.height);
      ctx.font = font + "px 'Share Tech Mono', monospace";
      for (var i = 0; i < drops.length; i++) {
        var ch = glyphs[(Math.random() * glyphs.length) | 0];
        ctx.fillStyle = Math.random() < 0.07 ? "#e6ffee" : "rgba(0,255,65,0.72)";
        ctx.fillText(ch, i * font, drops[i]);
        if (drops[i] > cv.height && Math.random() > 0.972) drops[i] = 0;
        drops[i] += font;
      }
      _rainRAF = requestAnimationFrame(frame);
    }
    if (_rainRAF) cancelAnimationFrame(_rainRAF);
    frame();
  }
  function applyWorld(name) {
    name = name || "";
    var root = document.documentElement;
    if (name) root.setAttribute("data-world", name);
    else root.removeAttribute("data-world");
    if (name === "matrix") startMatrixRain(); else stopMatrixRain();
    state.world = name;
    loadWorldTheme();
    document.querySelectorAll("[data-world-pick]").forEach(function (b) {
      b.classList.toggle("active", (b.getAttribute("data-world-pick") || "") === name);
    });
  }
  function pickWorld(name) {
    applyWorld(name);
    var prefs = loadPrefs();
    prefs.world = name || "";
    savePrefs(prefs);
  }
  document.querySelectorAll("[data-world-pick]").forEach(function (b) {
    b.onclick = function () { pickWorld(b.getAttribute("data-world-pick")); };
  });
  var _wReset = document.getElementById("btnWorldReset");
  if (_wReset) _wReset.onclick = function () { pickWorld(""); };

  /* ---- MAESTRO WORLDS: homepage aberta pelo logótipo ---- */
  var HOME_WORLDS = [
    { id: "matrix", name: "Matrix", icon: "🟩", tag: "Chuva digital", tag_en: "Digital rain", desc: "Verde fósforo, scanlines e código a cair.", desc_en: "Phosphor green, scanlines and falling code." },
    { id: "scifi", name: "Sci-Fi HUD", icon: "🛰", tag: "Órbita", tag_en: "Orbit", desc: "Grelha orbital, radar e azul de instrumento.", desc_en: "Orbital grid, radar and instrument blue." },
    { id: "cyberpunk", name: "Cyberpunk", icon: "🌆", tag: "Neon 2099", tag_en: "Neon 2099", desc: "Rosa elétrico, amarelo ácido e horizonte em perspetiva.", desc_en: "Electric pink, acid yellow and a perspective horizon." },
    { id: "hesperia", name: "Hesperia", icon: "🏛", tag: "Civitas", tag_en: "Civitas", desc: "Mármore, ouro e geometria greco-romana.", desc_en: "Marble, gold and Greco-Roman geometry." },
    { id: "dune", name: "Dune · Arrakis", icon: "🏜", tag: "Deserto", tag_en: "Desert", desc: "Areia, especiaria e um planeta em rotação.", desc_en: "Sand, spice and a rotating planet." },
    { id: "dungen", name: "Hubert Dungen", icon: "🪨", tag: "Bone & sand", tag_en: "Bone & sand", desc: "Osso, carvão e a textura do portfólio.", desc_en: "Bone, charcoal and the portfolio texture." }
  ];
  var _homeRoot = null;

  function homeWorldName(id) {
    for (var i = 0; i < HOME_WORLDS.length; i++) if (HOME_WORLDS[i].id === id) return HOME_WORLDS[i].name;
    return state.lang === "en" ? "Default" : "Padrão";
  }
  function updateHomeActive() {
    var el = document.getElementById("mhActive");
    if (!el) return;
    var id = (_homeRoot && _homeRoot.getAttribute("data-home-world")) || state.world || "";
    el.textContent = homeWorldName(id);
  }
  function syncHomeLanguage() {
    var en = state.lang === "en";
    var t = document.getElementById("mhTitle");
    if (t) t.innerHTML = en ? "One studio.<br><em>Six worlds.</em>" : "Um estúdio.<br><em>Seis mundos.</em>";
    var lead = document.querySelector(".mh-lead");
    if (lead) lead.textContent = en
      ? "Maestro orchestrates your agents, files and services. Each world changes the light, type and effects of the whole app — pick one and step in."
      : "O Maestro orquestra os teus agentes, ficheiros e serviços. Cada mundo muda a luz, a tipografia e os efeitos da app inteira — escolhe um e entra.";
    var hint = document.querySelector(".mh-hint");
    if (hint) hint.textContent = en
      ? "Click a world to try it. Your choice is saved on this device."
      : "Clica num mundo para o experimentar. A escolha fica guardada neste dispositivo.";
    var enter = document.getElementById("btnHomeEnter");
    if (enter) enter.innerHTML = (en ? "Enter Studio" : "Entrar no Studio") + ' <span aria-hidden="true">→</span>';
    var start = document.getElementById("btnHomeStart");
    if (start) start.textContent = en ? "Enter Studio" : "Entrar no Studio";
    var install = document.getElementById("btnHomeInstall");
    if (install) install.textContent = en ? "📲 Install app" : "📲 Instalar app";
    var foot = document.querySelector(".mh-foot > span");
    if (foot) foot.textContent = en ? "✨ Backgrounds, type and animated effects per theme" : "✨ Fundos, tipografia e efeitos animados por tema";
    var wrap = document.getElementById("mhActiveWrap");
    if (wrap) wrap.innerHTML = (en ? "Active world" : "Mundo ativo") + ': <b id="mhActive">' + esc(homeWorldName(state.world || "")) + '</b>';
    renderHomeWorlds();
    updateHomeActive();
  }

  function renderHomeWorlds() {
    var box = document.getElementById("mhWorlds");
    if (!box) return;
    var current = (_homeRoot && _homeRoot.getAttribute("data-home-world")) || state.world || "";
    box.innerHTML = HOME_WORLDS.map(function(w){
      var on = w.id === current;
      var en = state.lang === "en";
      var tag = (en && w.tag_en) ? w.tag_en : w.tag;
      var desc = (en && w.desc_en) ? w.desc_en : w.desc;
      return '<button type="button" class="mh-world' + (on ? ' active' : '') + '" data-home-world="' + w.id + '" aria-pressed="' + (on ? 'true' : 'false') + '">' +
        '<span class="mh-art mh-art-' + w.id + '" aria-hidden="true"></span>' +
        '<span class="mh-meta"><span class="mh-name">' + dualIco({matrix:'matrix', scifi:'satellite', cyberpunk:'city', hesperia:'temple', dune:'desert', dungen:'rock'}[w.id], w.icon) + ' ' + esc(w.name) + '</span>' +
          '<span class="mh-tag">' + esc(tag) + '</span><span class="mh-desc">' + esc(desc) + '</span></span>' +
        '<span class="mh-active-chip" aria-hidden="true">' + (en ? "ACTIVE" : "ATIVO") + '</span>' +
      '</button>';
    }).join("");
    box.querySelectorAll("[data-home-world]").forEach(function(btn){
      btn.onclick = function(){
        var id = btn.getAttribute("data-home-world");
        pickWorld(id);
        if (_homeRoot) _homeRoot.setAttribute("data-home-world", id);
        box.querySelectorAll("[data-home-world]").forEach(function(other){
          var on = other.getAttribute("data-home-world") === id;
          other.classList.toggle("active", on);
          other.setAttribute("aria-pressed", on ? "true" : "false");
        });
        updateHomeActive();
        btn.focus();
      };
    });
  }
  function openMaestroHome() {
    _homeRoot = document.getElementById("maestroHome");
    if (!_homeRoot) return;
    var id = state.world || "";
    _homeRoot.setAttribute("data-home-world", id);
    _homeRoot.hidden = false;
    document.body.classList.add("home-open");
    renderHomeWorlds();
    updateHomeActive();
    var start = document.getElementById("btnHomeStart");
    if (start) setTimeout(function(){ start.focus(); }, 30);
  }
  function closeMaestroHome() {
    if (!_homeRoot) _homeRoot = document.getElementById("maestroHome");
    if (!_homeRoot) return;
    _homeRoot.hidden = true;
    document.body.classList.remove("home-open");
    var brand = document.getElementById("btnBrandHome");
    if (brand) brand.focus();
  }

  var _brandHome = document.getElementById("btnBrandHome");
  if (_brandHome) _brandHome.onclick = openMaestroHome;
  var _homeEnter = document.getElementById("btnHomeEnter");
  if (_homeEnter) _homeEnter.onclick = closeMaestroHome;
  var _homeStart = document.getElementById("btnHomeStart");
  if (_homeStart) _homeStart.onclick = closeMaestroHome;
  var _homeInstall = document.getElementById("btnHomeInstall");
  if (_homeInstall) _homeInstall.onclick = function(){
    closeMaestroHome();
    setTimeout(function(){ var b = document.getElementById("btnInstallPwa"); if (b) b.click(); }, 120);
  };
  /* Foco preso dentro da homepage enquanto estiver aberta. */
  document.addEventListener("keydown", function(e){
    if (e.key !== "Tab" || !_homeRoot || _homeRoot.hidden) return;
    var focusable = Array.prototype.slice.call(_homeRoot.querySelectorAll("button:not([disabled]), a[href], [tabindex]:not([tabindex='-1'])"));
    if (!focusable.length) return;
    var first = focusable[0], last = focusable[focusable.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  });

  /* ---- PLACEHOLDER ADAPTATIVO DO COMPOSER ---- */
  function updateComposerPlaceholder() {
    var el = document.getElementById("promptInput");
    if (!el) return;
    var en = state.lang === "en";
    var chat = state.currentChat;
    var nMsgs = chat && chat.messages ? chat.messages.length : 0;
    var ph;
    if (isCurrentChatBusy()) {
      ph = state.activeJob.status === 'queued' ? (en ? "Queued… waiting to start" : "Em fila… a aguardar execução") : en ? "Working… type to queue or steer (Ctrl+Enter sends)"
              : "A trabalhar… escreve para meter em fila ou orientar (Ctrl+Enter envia)";
    } else if (nMsgs > 0) {
      var last = null;
      for (var i = chat.messages.length - 1; i >= 0; i--) {
        if (chat.messages[i].role === "user") { last = chat.messages[i].text || ""; break; }
      }
      var hint = (last || "")
        .replace(/\[[^\]]*\]/g, " ")
        .replace(/^[\s\u{1F300}-\u{1FAFF}\u2600-\u27BF•·\-–—>]+/u, "")
        .replace(/\s+/g, " ")
        .trim()
        .slice(0, 34);
      ph = en ? "Continue the conversation…" + (hint ? " (" + hint + "…)" : "") + " · Ctrl+Enter sends"
              : "Continua a conversa…" + (hint ? " (a propósito de «" + hint + "…»)" : "") + " · Ctrl+Enter envia";
    } else {
      ph = en ? "Tell Maestro what to do (e.g. 'download Dune', 'inspect C# script', 'Qidi status')…"
              : "Diz ao Maestro o que queres fazer (ex: 'mete o filme Dune a sacar', 'cria script C#', 'estado da Qidi')…";
    }
    /* O placeholder é só texto: remove ícones/emoji iniciais (⏳, 📥, 🧭, …). */
    ph = String(ph || "").replace(/^[\s\u{1F000}-\u{1FAFF}\u{2300}-\u{23FF}\u{2600}-\u{27BF}\u{2B00}-\u{2BFF}\u{FE0F}\u{200D}]+/u, "").trim();
    if (el.placeholder !== ph) el.placeholder = ph;
  }

  function openSettings() { document.getElementById("settingsModal").hidden = false; }
  function closeSettings() { document.getElementById("settingsModal").hidden = true; }

  /* ---- DETALHE POR AGENTE (o que faz agora + últimas tarefas) ---- */
  var AGENT_ENGINE = { agy: "agy", claude: "sonnet", codex: "codex", deepseek: "deepseek" };
  var AGENT_ENGINE_LABEL = { agy: "🧠 Gemini / Antigravity", claude: "🎭 Claude Sonnet", codex: "⚙️ Codex", deepseek: "🔮 DeepSeek V3.2" };

  function agentRecentTasks(f) {
    var out = [];
    var msgs = (state.currentChat && state.currentChat.messages) || [];
    var needle = String(f.name || "").toLowerCase().split(/[\s·]+/)[0];
    for (var i = msgs.length - 1; i >= 0 && out.length < 5; i--) {
      var m = msgs[i];
      if (m.role !== "assistant") continue;
      var used = String(m.agent_used || "").toLowerCase();
      if (needle && used.indexOf(needle) === -1) continue;
      out.push(m);
    }
    return out;
  }

  function openAgentDetail(key) {
    var st = state.status || {};
    var fleet = (st.fleet_summary || {}).fleet || st.fleet || [];
    var f = null;
    for (var i = 0; i < fleet.length; i++) if (fleet[i].key === key) f = fleet[i];
    if (!f) return;
    var engine = AGENT_ENGINE[f.key] || "auto";
    var body = document.getElementById("agentModalBody");
    document.getElementById("agentModalTitle").textContent = (f.icon || "🤖") + " " + f.name;

    var aj = state.activeJob;
    var html = '';
    html += '<div class="stat-card" style="margin:0">' +
      '<div class="stat-head"><span>' + esc(f.role || "") + '</span>' +
      '<span class="quota-chip ' + (f.available ? "ok" : "bad") + '">' + (f.available ? "LIVRE" : "ESGOTADO") + '</span></div>' +
      '<div class="stat-sub">' + esc(fleetQuotaLine(f.key) || f.quota_label || "—") + '</div></div>';

    var now = aj ? (aj.agent_label + " · " + (aj.current_step || "a trabalhar")) : "sem tarefa a correr agora";
    html += '<div class="stat-card" style="margin:0">' +
      '<div class="stat-head"><span>▶ A fazer agora</span></div>' +
      '<div class="stat-sub">' + esc(now) + '</div>' +
      (aj && aj.last_line ? '<div class="stat-sub" style="font-family:var(--mono);font-size:.64rem">↳ ' + esc(String(aj.last_line).slice(0, 160)) + '</div>' : '') +
      '</div>';

    var tasks = agentRecentTasks(f);
    html += '<div class="stat-card" style="margin:0"><div class="stat-head"><span>🕘 Últimas tarefas nesta conversa</span></div>';
    if (!tasks.length) {
      html += '<div class="stat-sub">Ainda sem tarefas deste agente nesta conversa.</div>';
    } else {
      tasks.forEach(function(m){
        var when = new Date((m.ts || 0) * 1000).toLocaleTimeString("pt-PT", { hour: "2-digit", minute: "2-digit" });
        var line = String(m.text || "").replace(/\s+/g, " ").slice(0, 110);
        html += '<div class="stat-sub" style="display:flex;gap:8px"><span style="color:var(--cyan);font-family:var(--mono)">' + when + '</span><span>' + esc(line) + '…</span></div>';
      });
    }
    html += '</div>';

    html += '<div style="display:flex;gap:8px;margin-top:4px">' +
      '<button type="button" class="card-btn" id="btnAgentUse" style="flex:1">Usar este motor</button></div>';
    body.innerHTML = html;
    document.getElementById("agentModal").hidden = false;

    document.getElementById("btnAgentUse").onclick = function(){
      document.getElementById("selEngine").value = engine;
      closeAgentDetail();
      notifyUser("🧠 Motor", "Passa a usar " + (AGENT_ENGINE_LABEL[f.key] || f.name) + " nas próximas mensagens.");
    };
  }
  function closeAgentDetail() { document.getElementById("agentModal").hidden = true; }

  document.addEventListener("click", function(ev){
    var t = ev.target.closest ? ev.target.closest(".fleet-card") : null;
    if (!t) return;
    ev.preventDefault(); ev.stopPropagation();
    var key = t.getAttribute("data-fleet-key") || "";
    if (key) openAgentDetail(key);
  }, true);

  var _bCloseAgent = document.getElementById("btnCloseAgentModal");
  if (_bCloseAgent) _bCloseAgent.onclick = closeAgentDetail;
  var _mAgent = document.getElementById("agentModal");
  if (_mAgent) _mAgent.onclick = function(e){ if (e.target === _mAgent) closeAgentDetail(); };
  function applyLessOutlines(on) {
    document.documentElement.toggleAttribute("data-less-outlines", on);
    var b = document.getElementById("btnLessOutlines");
    if (b) { b.classList.toggle("primary", on); b.textContent = ui("▢ Menos contornos: ", "▢ Fewer outlines: ") + (on ? "ON" : "OFF"); }
  }
  document.getElementById("btnLessOutlines").onclick = function(){
    var on = !document.documentElement.hasAttribute("data-less-outlines");
    var p = loadPrefs(); p.lessOutlines = on; savePrefs(p); applyLessOutlines(on);
  };
  applyLessOutlines(!!loadPrefs().lessOutlines);
  document.getElementById("btnOpenSettings").onclick = openSettings;
  document.getElementById("btnHeaderSettings").onclick = openSettings;
  document.getElementById("btnCloseSettingsModal").onclick = closeSettings;
  document.getElementById("settingsModal").onclick = function(e){
    if (e.target === document.getElementById("settingsModal")) closeSettings();
  };

  document.getElementById("btnLangPt").onclick = function(){ applyLanguage("pt"); };
  document.getElementById("btnLangEn").onclick = function(){ applyLanguage("en"); };

  document.querySelectorAll("#iconStyleGrid [data-icostyle]").forEach(function(btn){
    btn.onclick = function(){ applyIconStyle(btn.getAttribute("data-icostyle")); };
  });

  document.querySelectorAll("#themePresetsGrid [data-preset]").forEach(function(btn){
    btn.onclick = function(){
      var p = THEME_PRESETS[btn.getAttribute("data-preset")];
      if (p) applyThemeColors(p);
    };
  });
  document.getElementById("btnResetTheme").onclick = resetThemeColors;
  document.getElementById("colCyan").oninput = function(e){ applyThemeColors({ cyan: e.target.value }); };
  document.getElementById("colViolet").oninput = function(e){ applyThemeColors({ violet: e.target.value }); };
  document.getElementById("colBg").oninput = function(e){ applyThemeColors({ bg: e.target.value }); };
  document.getElementById("colPanel").oninput = function(e){ applyThemeColors({ panel: e.target.value, card: e.target.value }); };
  document.querySelectorAll("[data-scale]").forEach(function(btn){
    btn.onclick = function(){
      var sc = btn.getAttribute("data-scale");
      applyThemeColors({ scale: sc });
      var sl = document.getElementById("uiScaleSlider");
      if (sl) sl.value = parseFloat(sc) || 15.5;
    };
  });
  var uiScaleSl = document.getElementById("uiScaleSlider");
  if (uiScaleSl) {
    uiScaleSl.oninput = function(e){ applyThemeColors({ scale: e.target.value + "px" }); };
  }

  /* ---- LIVE AUTO-UPDATER ---- */
  state.buildHash = null;
  state.autoUpdate = (loadPrefs().autoUpdate !== false);

  function syncAutoUpdateBtn() {
    var b = document.getElementById("btnToggleAutoUpdate");
    if (!b) return;
    b.classList.toggle("primary", state.autoUpdate);
    b.textContent = "🔄 Auto: " + (state.autoUpdate ? "ON" : "OFF");
  }
  syncAutoUpdateBtn();

  document.getElementById("btnToggleAutoUpdate").onclick = function(){
    state.autoUpdate = !state.autoUpdate;
    var prefs = loadPrefs();
    prefs.autoUpdate = state.autoUpdate;
    savePrefs(prefs);
    syncAutoUpdateBtn();
  };

  function isBusyNow() {
    var aj = state.activeJob;
    return !!(aj && (aj.status || "running") === "running");
  }

  var REFRESH_BTN_IDS = ["btnRefreshApp", "btnRefreshDrawer", "btnRefreshSettings"];

  function setRefreshBusy(busy) {
    REFRESH_BTN_IDS.forEach(function(id){
      var b = document.getElementById(id);
      if (!b) return;
      b.classList.toggle("busy", !!busy);
      b.setAttribute("aria-busy", busy ? "true" : "false");
    });
    var ind = document.getElementById("pullRefresh");
    if (ind) ind.classList.toggle("loading", !!busy);
  }

  function markUpdateAvailable(hash) {
    var on = !!hash;
    REFRESH_BTN_IDS.forEach(function(id){
      var b = document.getElementById(id);
      if (!b) return;
      b.classList.toggle("has-update", on);
      if (id === "btnRefreshApp") {
        b.title = on ? ui("Nova versão #", "New version #") + hash.slice(0, 6) + ui(" — atualizar", " — update") : ui("Atualizar app", "Update app");
        b.setAttribute("aria-label", on ? ui("Nova versão disponível. Atualizar app", "New version available. Update app") : ui("Atualizar app", "Update app"));
      } else {
        b.setAttribute("aria-label", on
          ? ui("Nova versão disponível. Verificar e instalar atualização", "New version available. Check for and install the update")
          : ui("Verificar e instalar atualização da app", "Check for and install an app update"));
        var label = b.querySelector(".du-text");
        if (label) label.textContent = on ? ui("Nova versão · atualizar", "New version · update") : ui("Atualizar app", "Update app");
      }
    });
  }

  function syncVersionLabels(v) {
    if (!v || !v.hash) return;
    var vb = document.getElementById("versionBadge");
    if (vb) vb.textContent = "v" + v.version + " · #" + v.hash.slice(0, 6) + " (" + v.updated_str + ")";
    var dv = document.getElementById("drawerVersion");
    if (dv) dv.textContent = "v" + v.version + " · #" + v.hash.slice(0, 6);
    state.buildVersion = v.version;
  }

  function showUpdateToast(v, busyJob) {
    var toast = document.getElementById("updateToast");
    var toastTxt = document.getElementById("updateToastText");
    if (!toast || !toastTxt) return;
    toast.setAttribute("data-hash", v.hash);
    toastTxt.textContent = state.lang === "en"
      ? "New Maestro AI update (#" + v.hash.slice(0,6) + ") detected." + (busyJob ? " It will apply when the current run finishes." : "")
      : "Nova atualização do Maestro AI (#" + v.hash.slice(0,6) + ") detetada." + (busyJob ? " Aplica-se quando a execução atual terminar." : "");
    toast.hidden = false;
  }

  function performSeamlessReload(force) {
    if (state.reloading) return false;
    if (isBusyNow() && !force) {
      var ok = confirm(state.lang === "en"
        ? "A run is still in progress. Reloading now will interrupt its live view. Continue?"
        : "Há uma execução em curso. Atualizar agora interrompe o acompanhamento em direto. Continuar?");
      if (!ok) return false;
    }
    state.reloading = true;
    setRefreshBusy(true);
    var applyBtn = document.getElementById("btnApplyUpdateNow");
    if (applyBtn) { applyBtn.disabled = true; applyBtn.textContent = state.lang === "en" ? "Updating…" : "A atualizar…"; }
    var pullText = document.querySelector("#pullRefresh .pr-text");
    if (pullText) pullText.textContent = state.lang === "en" ? "Updating…" : "A atualizar…";
    persistDraft(true);
    try {
      sessionStorage.setItem("maestro_resume_state", JSON.stringify({
        mod: state.mod,
        chatId: state.currentChat ? state.currentChat.id : null,
        draft: document.getElementById("promptInput").value || ""
      }));
    } catch(e){}

    var reloaded = false;
    function reloadNow() {
      if (reloaded) return;
      reloaded = true;
      window.location.reload();
    }

    /* Limpar caches e pedir ao service worker que procure uma versão nova.
       A limpeza nunca pode bloquear a atualização: há um limite de tempo. */
    var jobs = [];
    if ("caches" in window) {
      jobs.push(caches.keys().then(function(names){
        return Promise.all(names.map(function(n){ return caches.delete(n); }));
      }));
    }
    if (navigator.serviceWorker && navigator.serviceWorker.getRegistrations) {
      jobs.push(navigator.serviceWorker.getRegistrations().then(function(regs){
        return Promise.all(regs.map(function(reg){
          try { return reg.update(); } catch (e) { return Promise.resolve(); }
        }));
      }).catch(function(){}));
    }
    var cleanup = Promise.all(jobs);
    // Nunca deixar a limpeza de cache bloquear a atualização.
    Promise.race([cleanup, new Promise(function(resolve){ setTimeout(resolve, 1500); })]).then(reloadNow, reloadNow);
    setTimeout(reloadNow, 2400);
    return true;
  }

  function requestAppRefresh() {
    if (state.reloading) return;
    if (isBusyNow()) {
      var ok = confirm(state.lang === "en"
        ? "A run is still in progress. Reloading now will interrupt its live view. Continue?"
        : "Há uma execução em curso. Atualizar agora interrompe o acompanhamento em direto. Continuar?");
      if (!ok) return;
    }
    setRefreshBusy(true);
    var pullText = document.querySelector("#pullRefresh .pr-text");
    if (pullText) pullText.textContent = state.lang === "en" ? "Checking…" : "A verificar…";
    var before = state.buildHash;
    api("/api/version").then(function(v){
      if (v && v.hash) {
        syncVersionLabels(v);
        if (before && v.hash !== before) {
          markUpdateAvailable(v.hash);
          showUpdateToast(v, false);
        }
      }
      setTimeout(function(){ performSeamlessReload(true); }, 240);
    }).catch(function(){
      // Sem servidor não há versão para comparar; recarregar mesmo assim.
      setTimeout(function(){ performSeamlessReload(true); }, 160);
    });
  }

  REFRESH_BTN_IDS.forEach(function(id){
    var b = document.getElementById(id);
    if (!b) return;
    b.onclick = requestAppRefresh;
  });

  document.getElementById("btnApplyUpdateNow").onclick = function(){
    performSeamlessReload(false);
  };

  document.getElementById("btnDismissUpdate").onclick = function(){
    var toast = document.getElementById("updateToast");
    if (toast) {
      toast.hidden = true;
      try { sessionStorage.setItem("maestro_update_dismissed", toast.getAttribute("data-hash") || ""); } catch(e){}
    }
    if (state.updateTimer) { clearTimeout(state.updateTimer); state.updateTimer = null; }
    // O ponto verde fica: continua a haver uma versão por instalar.
  };

  function checkServerVersion(manual) {
    return api("/api/version").then(function(v){
      if (!v || !v.hash) return;
      syncVersionLabels(v);
      if (!state.buildHash) {
        state.buildHash = v.hash;
        markUpdateAvailable("");
        if (manual) {
          notifyUser("🔄 Auto-Updater", state.lang === "en" ? "Maestro AI is up to date (" + v.hash.slice(0,6) + ")." : "Maestro AI está na versão mais recente (#" + v.hash.slice(0,6) + ").");
        }
        return;
      }
      if (v.hash !== state.buildHash) {
        var toast = document.getElementById("updateToast");
        var busyJob = isBusyNow();
        var busyTyping = (document.getElementById("promptInput").value || "").trim().length > 0 || document.getElementById("btnSend").disabled;
        var dismissed = "";
        try { dismissed = sessionStorage.getItem("maestro_update_dismissed") || ""; } catch(e){}
        markUpdateAvailable(v.hash);
        if (dismissed === v.hash) {
          // O utilizador já fechou este aviso; não voltar a abri-lo a cada 10s.
          if (state.autoUpdate && !busyTyping && !busyJob && !document.hidden) {
            state.buildHash = v.hash;
            state.updateTimer = setTimeout(performSeamlessReload, 1500);
          }
          return;
        }
        showUpdateToast(v, busyJob);
        var busy = busyTyping || busyJob;
        // NUNCA recarregar a página a meio de uma execução nem com a app em
        // segundo plano: isso cortava o scroll, o acompanhamento e o trabalho.
        if (state.autoUpdate && !busy && !document.hidden && !state.updateTimer) {
          state.buildHash = v.hash;
          state.updateTimer = setTimeout(function(){
            state.updateTimer = null;
            performSeamlessReload();
          }, 3000);
        }
      } else {
        markUpdateAvailable("");
        if (manual) {
          notifyUser("🔄 Auto-Updater", state.lang === "en" ? "Already on latest build (#" + v.hash.slice(0,6) + ")." : "Já estás na build mais recente (#" + v.hash.slice(0,6) + ").");
        }
      }
    }).catch(function(){});
  }

  document.getElementById("btnCheckUpdateNow").onclick = function(){ checkServerVersion(true); };

  /* Puxar para atualizar no topo da conversa (telemóvel/tablet).
     Fica longe das margens para não competir com as gavetas. */
  (function bindPullToRefresh() {
    var box = document.getElementById("chatScroll");
    var ind = document.getElementById("pullRefresh");
    if (!box || !ind) return;
    var startY = 0, startX = 0, dist = 0, pulling = false, active = false;
    var THRESHOLD = 64;
    function resetPull() {
      pulling = false; active = false; dist = 0;
      ind.classList.remove("visible", "ready");
      ind.style.transform = ""; ind.style.opacity = "";
      var t = ind.querySelector(".pr-text");
      if (t) t.textContent = state.lang === "en" ? "Pull to refresh" : "Puxa para atualizar";
    }
    box.addEventListener("touchstart", function(e){
      if (e.touches.length !== 1 || state.reloading || document.hidden) return;
      if (box.scrollTop > 0) return;
      if (document.querySelector(".mobile-open")) return;
      if (document.querySelector(".modal-backdrop:not([hidden])")) return;
      var t = e.touches[0];
      if (t.clientX < 30 || t.clientX > window.innerWidth - 30) return;
      startY = t.clientY; startX = t.clientX; pulling = true; active = false; dist = 0;
    }, { passive: true });
    box.addEventListener("touchmove", function(e){
      if (!pulling || e.touches.length !== 1) return;
      if (box.scrollTop > 0) { resetPull(); return; }
      var t = e.touches[0];
      var dy = t.clientY - startY;
      var dx = Math.abs(t.clientX - startX);
      if (dy <= 0 || dx > dy * 1.25) { if (!active) resetPull(); return; }
      if (dy > 8) { active = true; e.preventDefault(); }
      dist = Math.min(96, Math.max(0, (dy - 6) * 0.55));
      ind.classList.add("visible");
      ind.classList.toggle("ready", dist >= THRESHOLD);
      ind.style.transform = "translate(-50%, " + (4 + Math.min(38, dist * 0.34)) + "px)";
      ind.style.opacity = String(Math.min(1, dist / 42));
      var txt = ind.querySelector(".pr-text");
      if (txt) txt.textContent = dist >= THRESHOLD
        ? (state.lang === "en" ? "Release to refresh" : "Solta para atualizar")
        : (state.lang === "en" ? "Pull to refresh" : "Puxa para atualizar");
    }, { passive: false });
    function endPull() {
      if (!pulling) return;
      var go = dist >= THRESHOLD;
      if (go) {
        ind.classList.add("loading", "visible", "ready");
        ind.style.opacity = "1";
        var txt = ind.querySelector(".pr-text");
        if (txt) txt.textContent = state.lang === "en" ? "Updating…" : "A atualizar…";
        pulling = false;
        if (performSeamlessReload(false) === false) resetPull();
        return;
      }
      resetPull();
    }
    box.addEventListener("touchend", endPull, { passive: true });
    box.addEventListener("touchcancel", function(){ if (!pulling) return; resetPull(); }, { passive: true });
  })();

  /* Ao voltar à app (Android/iOS) verifica logo se há uma versão nova. */
  document.addEventListener("visibilitychange", function(){
    if (document.hidden) return;
    setTimeout(function(){ checkServerVersion(false); }, 300);
  });
  window.addEventListener("pageshow", function(e){ if (e.persisted) checkServerVersion(false); });
  window.addEventListener("online", function(){ checkServerVersion(false); });

  // Restore saved preferences on boot
  var initialPrefs = loadPrefs();
  if (initialPrefs.mod) setModule(initialPrefs.mod);
  applyWorld(initialPrefs.world || "");
  if (initialPrefs.theme && initialPrefs.theme.scale) applyThemeColors({ scale: initialPrefs.theme.scale }, true);
  state.iconStyle = initialPrefs.iconStyle || "unicolor";
  applyLanguage(initialPrefs.lang || "pt");

  /* ---- ATALHOS DE TECLADO (Ctrl/Cmd) ---- */
  document.addEventListener("keydown", function(e){
    var mod = e.ctrlKey || e.metaKey;
    var k = (e.key || "").toLowerCase();
    if (e.key === "Escape") {
      if (_homeRoot && !_homeRoot.hidden) { closeMaestroHome(); return; }
      var opened = document.querySelectorAll(".modal-backdrop:not([hidden])");
      if (opened.length) { opened.forEach(function(m){ m.hidden = true; }); return; }
      var cv = document.getElementById("artifactCanvas");
      if (cv && !cv.hidden) { cv.hidden = true; return; }
      return;
    }
    if (!mod) return;
    if (k === "n") { e.preventDefault(); startNewChat(); return; }
    if (k === "k") { e.preventDefault(); document.getElementById("searchBox").focus(); return; }
    if (k === "b") { e.preventDefault(); document.getElementById("btnToggleLeft").click(); return; }
    if (k === "q") { e.preventDefault(); document.getElementById("btnToggleRight").click(); return; }
    if (k === "enter") { e.preventDefault(); handleComposerSubmit(); return; }
    if (["1","2","3","4","5"].indexOf(k) !== -1) {
      e.preventDefault();
      setModule(["chat","studio","media","casa","artifacts"][Number(k) - 1]);
    }
  });

  /* Match the actual message column, including native scrollbar space and zoom. */
  (function bindComposerColumn() {
    var stream = document.getElementById("chatScroll");
    var dock = document.querySelector(".composer-dock");
    var form = document.getElementById("composerForm");
    var pane = document.getElementById("paneChat");
    function align() {
      if (!stream.clientWidth || !dock.clientWidth) return;
      var cs = getComputedStyle(stream), ds = getComputedStyle(dock);
      var available = stream.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
      var width = Math.min(available, parseFloat(getComputedStyle(pane).getPropertyValue("--chat-column-width")));
      var left = stream.getBoundingClientRect().left + stream.clientLeft + parseFloat(cs.paddingLeft) + (available - width) / 2;
      var origin = dock.getBoundingClientRect().left + dock.clientLeft + parseFloat(ds.paddingLeft);
      form.style.setProperty("--composer-column-size", width + "px");
      form.style.setProperty("--composer-column-offset", (left - origin) + "px");
    }
    new ResizeObserver(align).observe(stream);
    new ResizeObserver(align).observe(dock);
    window.addEventListener("resize", align);
    align();
  })();

  /* ---- BOOT ---- */
  (function bindScrollMemory() {
    var box = document.getElementById("chatScroll");
    if (!box) return;
    box.addEventListener("scroll", function () {
      if (_scrollTimer) return;
      _scrollTimer = setTimeout(function () { _scrollTimer = null; saveScrollNow(); }, 350);
    }, { passive: true });
    window.addEventListener("beforeunload", saveScrollNow);
  })();

  /* Pinta o painel já com o estado "a ler" em vez de o deixar vazio. */
  renderInspector();

  checkServerVersion(false);
  setInterval(function(){ checkServerVersion(false); }, 10000);

  loadFolders().then(function(){
    loadStatusAndPulse();
    loadOrchestration();
    loadProjectInspector("nas");
    return refreshChats();
  }).then(function(){
    var resumed = null;
    try {
      resumed = JSON.parse(sessionStorage.getItem("maestro_resume_state") || "null");
      sessionStorage.removeItem("maestro_resume_state");
    } catch(e){}
    if (resumed && resumed.mod) setModule(resumed.mod);
    if (resumed && resumed.draft) document.getElementById("promptInput").value = resumed.draft;
    /* volta à conversa onde estavas (e não à mais recente) */
    var lastChat = null;
    try { lastChat = localStorage.getItem(LAST_CHAT_STORE); } catch(e){}
    var targetId = (resumed && resumed.chatId) || null;
    if (!targetId && lastChat && state.chats.some(function(c){ return c.id === lastChat; })) targetId = lastChat;
    if (targetId) return openChat(targetId);
    if (state.chats.length) openChat(state.chats[0].id);
    else renderMessages();
  });
  setInterval(loadStatusAndPulse, 10000);
  // A tabela só é atualizada enquanto a aba Frota está aberta; o catálogo tem cache própria.
  setInterval(function(){ if (state.inspTab === "fleet" && !document.hidden) loadOrchestration(); }, 30000);
  // Refresh all chat previews, including tasks running in another conversation.
  setInterval(function(){
    if (document.hidden) return;
    api("/api/chats").then(function(d){
      var chats = d.chats || [];
      var foldersChanged = d.folders && JSON.stringify(d.folders) !== JSON.stringify(state.folders);
      var archived = d.archived_chats || [];
      if (!foldersChanged && JSON.stringify(chats) === JSON.stringify(state.chats) && JSON.stringify(archived) === JSON.stringify(state.archivedChats)) return;
      state.archivedChats = archived;
      if (d.folders) state.folders = d.folders;
      state.chats = chats;
      renderFolderStripAndSelect();
      renderChatList();
    }).catch(function(){});
  }, 4000);

  // Compact controls share the existing engine and busy-mode state.
  function composerSendTip(alternative) {
    var busy = isCurrentChatBusy();
    var mode = alternative ? (state.busyMode === 'queue' ? 'steer' : 'queue') : state.busyMode;
    var action = !busy ? ui('Enviar pedido', 'Send request') : mode === 'steer' ? ui('Orientar agora', 'Steer now') : ui('Meter em fila', 'Queue message');
    var keys = window.innerWidth > 760
      ? ui('Enter: envia · Shift+Enter: nova linha · Ctrl+Enter / ⌘+Enter: envia.', 'Enter: send · Shift+Enter: new line · Ctrl+Enter / ⌘+Enter: send.')
      : ui('Enter: nova linha · Shift+Enter: nova linha · Ctrl+Enter / ⌘+Enter: envia.', 'Enter: new line · Shift+Enter: new line · Ctrl+Enter / ⌘+Enter: send.');
    return action + '|' + keys + (alternative ? ui(' Os atalhos usam a ação principal; este botão executa a alternativa.', 'Shortcuts use the primary action; this button runs the alternative.') : busy ? (mode === 'queue' ? ui(' Aguarda a estratégia da fila; não interrompe a tarefa atual.', ' Waits for queue scheduling; does not interrupt the current task.') : ui(' Envia orientação à tarefa em curso.', ' Sends guidance to the running task.')) : '');
  }
  function syncComposerControls() {
    var engineButton = document.getElementById('btnComposerEngine');
    if (!engineButton) return;
    var engine = document.getElementById('selEngine').value || 'auto';
    var names = {auto:'Auto',agy:'Antigravity',sonnet:'Sonnet',opus:'Opus',haiku:'Haiku',fable:'Fable',codex:'Codex',deepseek:'DeepSeek'};
    var config = typeof selectedModelConfig === 'function' && workflowModelPrefs && workflowModels ? selectedModelConfig() : {};
    var label = (names[engine] || engine) + (config.effort ? ' · ' + config.effort : '');
    if (engineButton.dataset.label !== label) { engineButton.textContent = label + ' ⌄'; engineButton.dataset.label = label; }
    engineButton.setAttribute('data-tip', ui('Motor e esforço|Escolhe quem executa o próximo pedido e o esforço de raciocínio.', 'Engine and effort|Choose who runs the next request and the reasoning effort.'));
    document.getElementById('btnComposerMode').textContent = (state.busyMode === 'steer' ? ui('Orientar', 'Steer') : ui('Fila', 'Queue')) + ' ⌄';
    document.getElementById('btnComposerMode').setAttribute('data-tip', ui('Durante a execução|Escolhe entre guardar o próximo pedido na fila ou orientar a tarefa em curso. Não altera permissões.', 'While running|Choose whether to queue the next request or steer the running task. Does not change permissions.'));
    document.getElementById('btnSend').setAttribute('aria-description', composerSendTip(false).replace('|', '. '));
    document.getElementById('btnSend').setAttribute('aria-keyshortcuts', 'Control+Enter Meta+Enter' + (window.innerWidth > 760 ? ' Enter' : ''));
    var st = state.status, dot = document.getElementById('btnComposerQuota');
    dot.setAttribute('data-tip', ui('Quotas e tokens|Ver janelas de 5h/7d e saldo. Os fornecedores não disponibilizam um total de tokens restantes.', 'Quotas and tokens|View 5h/7d windows and balance. Providers do not expose a total of remaining tokens.'));
    dot.setAttribute('aria-label', ui('Quotas e tokens: abrir resumo', 'Quotas and tokens: open summary'));
    // A provider-specific ring: never average incompatible quotas or money.
    var provider = engine === 'auto' ? ((st && st.active_maestro || {}).engine || '') : engine;
    var data = st && (/^(sonnet|opus|haiku|fable|claude)/.test(provider) ? st.claude : provider === 'codex' ? st.codex : provider === 'agy' ? st.antigravity : null);
    var used = data ? (provider === 'codex' ? data.five_hour_est_pct : provider === 'agy' ? data.gemini_used_pct : data.five_hour_pct) : null;
    dot.style.setProperty('--quota-used', typeof used === 'number' ? Math.max(0,Math.min(100,used)) + '%' : '0%');
    var ringInfo = typeof used === 'number' ? (names[provider] || provider) + (provider === 'agy' ? '' : ' · 5h') + ': ' + used + ui('% usado. ', '% used. ') : ui('Sem percentagem para este motor. ', 'No percentage for this engine. ');
    dot.setAttribute('data-tip', ui('Quotas e tokens|', 'Quotas and tokens|') + ringInfo + ui('Clica para ver todas as quotas. Total de tokens restantes indisponível.', 'Click to view all quotas. Total remaining tokens unavailable.'));
    dot.setAttribute('aria-label', ui('Quotas e tokens. ', 'Quotas and tokens. ') + ringInfo);
    dot.classList.toggle('quota-unknown', typeof used !== 'number');
    dot.classList.toggle('quota-stale', !!state.statusError);
    if (dot.getAttribute('aria-expanded') === 'true') renderComposerQuota();
  }
  function renderComposerQuota() {
    var panel = document.getElementById('composerQuotaPanel');
    if (!panel) return;
    var st = state.status;
    var html = '<h3>' + ui('Quotas disponíveis', 'Available quotas') + '</h3><p class="composer-panel-note">' + ui('Tokens restantes · total indisponível. Os planos usam limites diferentes; não existe uma soma fiável.', 'Remaining tokens · total unavailable. Plans use different limits; there is no reliable sum.') + '</p>';
    if (!st) html += '<p>' + ui('Sem leitura de quotas disponível.', 'No quota reading available.') + '</p>';
    else {
      var rows = [
        ['Claude · 5h', (st.claude||{}).five_hour_pct, (st.claude||{}).five_hour_reset_fmt],
        ['Claude · 7d', (st.claude||{}).seven_day_pct, (st.claude||{}).seven_day_reset_fmt],
        ['Codex · 5h', (st.codex||{}).five_hour_est_pct, (st.codex||{}).five_hour_reset_fmt],
        ['Codex · 7d', (st.codex||{}).seven_day_pct, (st.codex||{}).seven_day_reset_fmt],
        ['Antigravity', (st.antigravity||{}).gemini_used_pct, (st.antigravity||{}).gemini_reset_fmt]
      ];
      rows.forEach(function(row){
        var remaining = typeof row[1] === 'number' ? Math.max(0,Math.min(100,100-row[1])) : null;
        html += '<div class="composer-quota-row"><div><strong>' + esc(row[0]) + '</strong><span>' + (remaining === null ? ui('sem dados','no data') : remaining + ui('% livre','% remaining')) + '</span></div><div class="composer-quota-track"><span style="width:' + (remaining === null ? 0 : remaining) + '%"></span></div><small>' + esc(row[2] || ui('Reset não disponível','Reset unavailable')) + '</small></div>';
      });
      html += '<div class="composer-balance"><strong>DeepSeek</strong><span>' + esc((st.deepseek||{}).balance_text || '—') + '</span></div>';
      html += '<p class="composer-panel-note">' + (state.statusError ? ui('Última leitura guardada · falha ao atualizar.', 'Last saved reading · refresh failed.') : ui('Última leitura: ', 'Last read: ') + (state.statusAt ? esc(clockText(state.statusAt)) : '—')) + '</p>';
    }
    var efficiency = st && st.efficiency;
    if (efficiency && !efficiency.error) {
      html += '<p class="composer-panel-note">' + ui('Autoavaliação local · sem chamadas a modelos.', 'Local evaluation · no model calls.') + '<br>';
      html += esc(efficiency.measured_total_tokens) + ui(' tokens reportados em ', ' reported tokens across ') + esc(efficiency.measured_calls) + '/' + esc(efficiency.calls) + ui(' execuções recentes.', ' recent executions.');
      var evaluation = efficiency.last_evaluation;
      if (evaluation) html += '<br>' + ui('Texto de entrada estimado: ', 'Estimated input text: ') + esc(evaluation.supplied_input_estimate) + ui(' tokens; envio direto: ', ' tokens; direct submission: ') + esc(evaluation.direct_input_estimate) + '.';
      html += '<br>' + ui('Poupança total sem Maestro: não medida.', 'Total savings without Maestro: not measured.') + '</p>';
    }
    html += '<button type="button" class="composer-panel-action" id="composerAllQuotas">' + ui('Ver painel completo →', 'View full panel →') + '</button>';
    if (panel.__html === html) return;
    panel.__html = html;
    panel.innerHTML = html;
    panel.querySelector('button').onclick = function(){
      closeComposerPanels(false); state.inspTab = 'quotas';
      if (document.getElementById('btnToggleRight').getAttribute('aria-expanded') !== 'true') document.getElementById('btnToggleRight').click();
      document.getElementById('itab-quotas').click();
    };
  }
  function closeComposerPanels(focus) {
    ['Engine','Mode','Quota'].forEach(function(key){
      var panel = document.getElementById('composer'+key+'Panel'), button = document.getElementById('btnComposer'+key);
      if (!panel || panel.hidden) return;
      panel.hidden = true; button.setAttribute('aria-expanded','false');
      if (focus) button.focus();
    });
  }
  function composerChoice(title, detail, checked, run) {
    var button = document.createElement('button'); button.type='button'; button.className='composer-choice';
    button.innerHTML='<span><strong>'+esc(title)+'</strong>'+(detail?'<small>'+esc(detail)+'</small>':'')+'</span><span aria-hidden="true">'+(checked?'✓':'')+'</span>';
    button.setAttribute('aria-pressed', String(checked)); button.onclick=run; return button;
  }
  function renderComposerChoices(key, panel) {
    panel.replaceChildren();
    var title = document.createElement('h3'); title.textContent=key==='Engine'?ui('Motor do próximo pedido','Engine for next request'):ui('Durante a execução','While running');panel.appendChild(title);
    if(key==='Engine') {
      var select=document.getElementById('selEngine');
      Array.prototype.forEach.call(select.options,function(o){
        panel.appendChild(composerChoice(o.textContent.replace(/^[^\p{L}\p{N}]+/u,''), o.value==='auto'?ui('O Maestro escolhe conforme contexto e quotas','Maestro chooses by context and quotas'):'',o.value===select.value,function(){select.value=o.value;select.dispatchEvent(new Event('change',{bubbles:true}));syncComposerControls();closeComposerPanels(true);}));
      });
      var more=document.createElement('button');more.type='button';more.className='composer-panel-action';more.textContent=ui('Modelo e esforço…','Model and effort…');
      more.onclick=function(){closeComposerPanels(false);var b=document.getElementById('wfModelButton');if(b)b.click();};panel.appendChild(more);
    } else {
      [['queue',ui('Meter em fila','Queue message'),ui('Guarda o pedido; o Maestro agenda quando puder executar.','Saves the request; Maestro schedules it when execution is possible.')],['steer',ui('Orientar agora','Steer now'),ui('Envia instruções adicionais à tarefa em curso.','Sends additional instructions to the running task.')]].forEach(function(row){panel.appendChild(composerChoice(row[1],row[2],state.busyMode===row[0],function(){setBusyMode(row[0]);syncComposerControls();closeComposerPanels(true);}));});
      var note=document.createElement('p');note.className='composer-panel-note';note.textContent=ui('Define a ação de envio enquanto o Maestro está ocupado. As permissões continuam a ser as da sessão real.','Sets the send action while Maestro is busy. Permissions remain those of the actual session.');panel.appendChild(note);
    }
  }
  setTimeout(function initComposerPanels(){
    ['Engine','Mode','Quota'].forEach(function(key){
      var button=document.getElementById('btnComposer'+key), panel=document.createElement('div');
      panel.id='composer'+key+'Panel';panel.className='composer-popover';panel.hidden=true;panel.setAttribute('role','dialog');panel.setAttribute('aria-label',key==='Engine'?ui('Motor e esforço','Engine and effort'):key==='Mode'?ui('Durante a execução','While running'):ui('Quotas e tokens','Quotas and tokens'));document.body.appendChild(panel);
      button.onclick=function(){
        if(!panel.hidden){closeComposerPanels(true);return;}
        closeComposerPanels(false);document.getElementById('composerMenu').hidden=true;document.getElementById('btnComposerMore').setAttribute('aria-expanded','false');hideModernTip();
        if(key==='Quota')renderComposerQuota();else renderComposerChoices(key,panel);
        var rect=button.getBoundingClientRect();panel.style.maxHeight=Math.max(120,rect.top-20)+'px';panel.hidden=false;
        panel.style.left=Math.max(12,Math.min(rect.right-panel.offsetWidth,innerWidth-panel.offsetWidth-12))+'px';panel.style.top=Math.max(12,rect.top-panel.offsetHeight-8)+'px';button.setAttribute('aria-expanded','true');panel.querySelector('button').focus();
      };
    });
    document.addEventListener('click',function(e){if(!e.target.closest('.composer-popover, #btnComposerEngine, #btnComposerMode, #btnComposerQuota'))closeComposerPanels(false);});
    document.addEventListener('keydown',function(e){
      var panel=document.querySelector('.composer-popover:not([hidden])');if(!panel)return;
      if(e.key==='Escape'){e.preventDefault();closeComposerPanels(true);return;}
      var items=Array.from(panel.querySelectorAll('button'));var index=items.indexOf(document.activeElement);
      if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();items[(index+(e.key==='ArrowDown'?1:-1)+items.length)%items.length].focus();}
    });
    document.addEventListener('focusin',function(e){if(!e.target.closest('.composer-popover, #btnComposerEngine, #btnComposerMode, #btnComposerQuota'))closeComposerPanels(false);});
    window.addEventListener('resize',function(){closeComposerPanels(false);syncComposerControls();});
    document.querySelector('.composer-dock').addEventListener('scroll',function(){closeComposerPanels(false);});
    document.getElementById('selEngine').addEventListener('change',syncComposerControls);
    document.getElementById('wfModelDialog').addEventListener('close',syncComposerControls);
    syncComposerControls();
  },0);

  window.matchMedia("(prefers-reduced-motion: reduce)").addEventListener('change', function(event) {
    if (event.matches) stopMatrixRain();
    else if (state.world === 'matrix') startMatrixRain();
  });
  observeInterfaceIcons();
})();

/* Menu próprio para <select> (o popup nativo é cinzento e não se estiliza). Toque mantém o nativo. */
(function () {
  var menu = null;
  function close() { if (menu) { menu.remove(); menu = null; } }
  document.addEventListener("pointerdown", function (e) {
    var s = e.target.closest && e.target.closest("select");
    if (menu && !e.target.closest(".sel-menu")) close();
    if (!s || s.disabled || s.multiple || e.pointerType === "touch" || e.button !== 0) return;
    e.preventDefault(); s.focus(); close();
    menu = document.createElement("div"); menu.className = "sel-menu";
    Array.prototype.forEach.call(s.options, function (o) {
      var b = document.createElement("button"); b.type = "button"; b.textContent = o.textContent;
      b.disabled = o.disabled; if (o.selected) b.className = "on";
      b.onclick = function () { s.value = o.value; s.dispatchEvent(new Event("input", { bubbles: true })); s.dispatchEvent(new Event("change", { bubbles: true })); close(); };
      menu.appendChild(b);
    });
    document.body.appendChild(menu);
    var r = s.getBoundingClientRect(), h = Math.min(menu.scrollHeight, 320);
    menu.style.minWidth = r.width + "px";
    menu.style.left = Math.max(4, Math.min(r.left, innerWidth - menu.offsetWidth - 4)) + "px";
    menu.style.top = (r.bottom + h + 8 > innerHeight && r.top > h ? r.top - h - 4 : r.bottom + 4) + "px";
    var on = menu.querySelector(".on"); if (on) on.scrollIntoView({ block: "nearest" });
  }, true);
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") close(); });
  window.addEventListener("resize", close); document.addEventListener("scroll", function (e) { if (menu && !e.target.closest) close(); }, true);
})();

/* Popup «Responder»: ao selecionar texto numa mensagem, cita-o no prompt. */
(function () {
  var scroll = document.getElementById("chatScroll"), ta = document.getElementById("promptInput");
  if (!scroll || !ta) return;
  var btn = document.createElement("button");
  btn.type = "button"; btn.className = "reply-pop"; btn.hidden = true;
  btn.innerHTML = '<span aria-hidden="true">↩</span> ' + ((document.documentElement.lang || "").indexOf("en") === 0 ? "Reply" : "Responder");
  document.body.appendChild(btn);
  var quoted = "";
  function hide() { btn.hidden = true; }
  function show() {
    var s = window.getSelection();
    var text = s && !s.isCollapsed ? s.toString().trim() : "";
    var node = s && s.rangeCount ? s.getRangeAt(0).commonAncestorContainer : null;
    if (node && node.nodeType === 3) node = node.parentNode;
    if (!text || !node || !scroll.contains(node) || !node.closest(".turn")) return hide();
    quoted = text;
    var r = s.getRangeAt(0).getBoundingClientRect();
    btn.hidden = false;
    var w = btn.offsetWidth;
    btn.style.left = Math.max(8, Math.min(innerWidth - w - 8, r.left + r.width / 2 - w / 2)) + "px";
    btn.style.top = (r.top > 54 ? r.top - 44 : r.bottom + 8) + "px";
  }
  // mouseup/touchend/teclado: a seleção só está estável depois do gesto
  ["mouseup", "touchend", "keyup"].forEach(function (ev) { document.addEventListener(ev, function () { setTimeout(show, 10); }); });
  document.addEventListener("mousedown", function (e) { if (e.target !== btn && !btn.contains(e.target)) hide(); });
  scroll.addEventListener("scroll", hide, { passive: true });
  btn.addEventListener("mousedown", function (e) { e.preventDefault(); }); // não perder a seleção
  btn.addEventListener("click", function () {
    var q = quoted.split("\n").map(function (l) { return "> " + l; }).join("\n");
    ta.value = (ta.value ? ta.value.replace(/\s*$/, "\n\n") : "") + q + "\n\n";
    ta.dispatchEvent(new Event("input", { bubbles: true }));
    window.getSelection().removeAllRanges(); hide(); ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length);
  });
})();

/* Perguntas por responder: lista as perguntas da última resposta do Maestro (se ainda não respondeste)
   e deixa citá-las uma a uma ou todas de uma vez no prompt. */
(function () {
  var scroll = document.getElementById("chatScroll"), ta = document.getElementById("promptInput"), form = document.getElementById("composerForm");
  if (!scroll || !ta || !form) return;
  var en = (document.documentElement.lang || "").indexOf("en") === 0;
  var bar = document.createElement("div");
  bar.className = "pending-qs"; bar.hidden = true;
  form.insertBefore(bar, form.firstChild);
  var dismissed = "", timer = 0;
  function setPendingTurn(turn) {
    scroll.__pendingQuestionTurn = turn;
    scroll.dispatchEvent(new Event("pendingquestionschange"));
  }
  function questions(turn) {
    return window.maestroReplyPrompts(turn).map(function(q) { return q.text; }).slice(0, 8);
  }

  function put(qs) {
    var q = qs.map(function (t, i) { return "> " + (qs.length > 1 ? (i + 1) + ". " : "") + t + "\n\n"; }).join("");
    ta.value = (ta.value ? ta.value.replace(/\s*$/, "\n\n") : "") + q;
    ta.dispatchEvent(new Event("input", { bubbles: true }));
    ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length);
  }
  function refresh() {
    var turns = scroll.querySelectorAll(".turn"), last = turns[turns.length - 1];
    var inlineReply = last && last.querySelector('.qa-apply');
    if (inlineReply) { bar.hidden = true; setPendingTurn(inlineReply.disabled ? null : last); return; }
    var qs = last && last.classList.contains("assistant") && !last.classList.contains("live-turn") ? questions(last) : [];
    var key = qs.join("|");
    if (!qs.length || key === dismissed) { bar.hidden = true; setPendingTurn(null); return; }
    bar.hidden = false; bar.innerHTML = "";
    setPendingTurn(last);
    var head = document.createElement("div"); head.className = "pq-head";
    head.innerHTML = '<span class="pq-title">❓ ' + (en ? "Unanswered questions" : "Perguntas por responder") + ' (' + qs.length + ')</span>';
    var all = document.createElement("button"); all.type = "button"; all.className = "pq-all";
    all.textContent = en ? "Answer all" : "Responder a todas"; all.onclick = function () { put(qs); };
    var x = document.createElement("button"); x.type = "button"; x.className = "pq-x"; x.textContent = "×";
    x.title = en ? "Dismiss" : "Dispensar"; x.onclick = function () { dismissed = key; bar.hidden = true; setPendingTurn(null); };
    var ap = document.createElement("button"); ap.type = "button"; ap.className = "pq-all pq-apply";
    ap.textContent = en ? "Apply suggestions" : "Aplicar sugestões";
    ap.title = en ? "Send now: my answers (if any) + go ahead with the reasonable defaults for the rest" : "Enviar já: as minhas respostas (se as houver) + avança com os defaults razoáveis no resto";
    ap.onclick = function () {
      put(qs);
      ta.value += en ? "Apply your suggestions and the reasonable defaults for anything I did not answer above. Finish everything (commit/PR/merge/deploy) in this turn, no further questions."
                     : "Aplica as tuas sugestões e os defaults razoáveis no que não respondi acima. Fecha tudo (commit/PR/merge/deploy) neste turno, sem mais perguntas.";
      ta.dispatchEvent(new Event("input", { bubbles: true }));
      form.requestSubmit();
    };
    head.appendChild(ap); head.appendChild(all); head.appendChild(x); bar.appendChild(head);
    qs.forEach(function (t) {
      var b = document.createElement("button"); b.type = "button"; b.className = "pq-item"; b.textContent = t;
      b.title = en ? "Quote in the prompt" : "Citar no prompt"; b.onclick = function () { put([t]); };
      bar.appendChild(b);
    });
  }
  new MutationObserver(function () { clearTimeout(timer); timer = setTimeout(refresh, 400); })
    .observe(scroll, { childList: true, subtree: true, characterData: true });
  refresh();
})();

/* ---- ESTRATÉGIA: árvore animada, painel lateral (PC) e apontador na lista ---- */
(function(){
  var S = window.maestroStrategy = {};
  function ui(pt,en){ return (window.__maestroState || {}).lang === 'en' ? en : pt; }
  function labels(){ return {pending:ui('A aguardar','Pending'),running:ui('A executar','Running'),done:ui('Concluído','Done'),failed:ui('Falhou','Failed'),blocked:ui('Bloqueado','Blocked'),cancelled:ui('Cancelado','Cancelled'),interrupted:ui('Interrompido','Interrupted')}; }
  function localReason(source){ return source === 'local_feedback' || source === 'local_capability'; }
  function reason(m){ return localReason(m.source) ? ui('Plano local: um só passo, sem consultar o coordenador.','Local plan: one step, without consulting the coordinator.') : (m.reason || ui('Sem justificação guardada para este plano.','No explanation saved for this plan.')); }
  var ICO = {pending:'○',running:'◉',done:'✓',failed:'✕',blocked:'⛔',cancelled:'—',interrupted:'⏸'};
  function e(v){ return String(v == null ? '' : v).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
  function clip(s, n){ s = String(s || '').replace(/\s+/g,' ').trim(); return s.length > n ? s.slice(0, n-1) + '…' : s; }
  function wide(){ return window.innerWidth > 1080; }

  // Modelo único: vem de um workflow completo ou do resumo (c.subtasks) da lista.
  S.fromWorkflow = function(wf, turn){
    var coord = wf.coordinator && typeof wf.coordinator === 'object' ? (wf.coordinator.label || wf.coordinator.name || '') : (wf.coordinator || '');
    return {turn: turn || wf.turn_id || '', coord: coord || ui('Orquestrador','Orchestrator'), reason: localReason(wf.planning_source) ? '' : (wf.reason || ''), source: wf.planning_source || '', created: wf.created_at, status: wf.status,
      nodes: (wf.nodes || []).filter(function(n, i, a){ return a.findIndex(function(x){ return x.id === n.id; }) === i; }).map(function(n){ var r = n.result || {};
        return {id:n.id, title:n.title || n.id, status:n.status || 'pending', engine:r.agent_used || n.actual_agent || n.engine || '', model:n.model_id || r.model_id || '',
          role:n.role, deps:n.depends_on || [], note:n.error || n.reason || '', text:typeof r === 'string' ? r : (r.text || ''), started:n.started_at, finished:n.finished_at, guided:(n.guidance || []).length}; })};
  };
  S.fromSubtasks = function(tasks){
    return {turn:'', coord:ui('Orquestrador','Orchestrator'), reason:'', source:'', nodes:(tasks || []).map(function(t){
      return {id:t.node_id, title:t.title, status:t.status, engine:t.engine, model:t.model, role:t.role, deps:t.deps || [], note:'', text:''}; })};
  };

  S.summary = function(tasks){
    var n = (tasks || []).length, d = (tasks || []).filter(function(t){ return t.status === 'done'; }).length;
    var run = (tasks || []).filter(function(t){ return t.status === 'running'; })[0];
    return d + '/' + n + (run ? ui(' · a executar: ',' · running: ') + clip(run.title, 28) : '');
  };

  S.treeHTML = function(m){
    var by = {}, depth = {}, LBL = labels();
    var sources = {coordinator:ui('do coordenador','from coordinator'),rules:ui('local','local'),local_feedback:ui('local','local'),local_capability:ui('local','local'),fallback:ui('de reserva','fallback')};
    m.nodes.forEach(function(n){ by[n.id] = n; });
    function dp(n, seen){ if (depth[n.id] != null) return depth[n.id]; if (seen[n.id]) return 0; seen[n.id] = 1;
      var d = 0; n.deps.forEach(function(x){ if (by[x]) d = Math.max(d, dp(by[x], seen) + 1); }); return depth[n.id] = d; }
    var rows = [];
    m.nodes.forEach(function(n){ (rows[dp(n, {})] = rows[dp(n, {})] || []).push(n); });
    var rootState = m.nodes.some(function(n){ return n.status === 'running'; }) ? 'running' : (m.nodes.length && m.nodes.every(function(n){ return n.status === 'done'; }) ? 'done' : 'pending');
    var h = '<div class="st-tree"' + (rootState === 'done' ? ' data-done="1"' : '') + ' data-edges="' + e(JSON.stringify(m.nodes.map(function(n){ return [n.id, n.deps, n.status]; }))) + '">' +
      '<div class="st-row"><div class="st-node st-root" data-id="__root" data-status="' + rootState + '"><b>' + window.maestroRoleTag('maestro') + ' ' + e(m.coord) + '</b><small>' + window.maestroRoleTag('planner') + ' · ' + ui('Orquestrador','Orchestrator') + (m.source ? ui(' · plano ',' · plan ') + e(sources[m.source] || m.source) : '') + '</small></div></div>';
    rows.forEach(function(row){
      h += '<div class="st-row">' + (row || []).map(function(n){
        var tag = window.mtag ? window.mtag(n.engine, n.model) : '';
        var decision = clip(n.note || n.text, 150);
        return '<div class="st-node" data-id="' + e(n.id) + '" data-status="' + e(n.status) + '"><span class="st-title"><i>' + (ICO[n.status] || '○') + '</i>' + e(n.title) + '</span>' +
          '<small>' + window.maestroRoleTag(n.role) + ' ' + e(LBL[n.status] || n.status) + (n.engine ? ' · ' + e(clip(n.engine, 24)) : '') + ' ' + tag + '</small>' +
          (decision ? '<small class="st-decision">' + e(decision) + '</small>' : '') + '</div>';
      }).join('') + '</div>';
    });
    return h + '<svg class="st-edges" aria-hidden="true"></svg></div>';
  };

  // Liga pai → filho com curvas; o fluxo anima quando o destino está a executar.
  S.drawEdges = function(root){
    root.querySelectorAll('.st-tree').forEach(function(tree){
      var svg = tree.querySelector('.st-edges'), data;
      try { data = JSON.parse(tree.getAttribute('data-edges')); } catch(_) { return; }
      if (!svg || tree.closest('.st-list')) { if (svg) svg.innerHTML = ''; return; }
      var box = tree.getBoundingClientRect(), d = '';
      function pt(id, bottom){ var el = tree.querySelector('[data-id="' + id.replace(/"/g,'') + '"]'); if (!el) return null;
        var r = el.getBoundingClientRect(); return [r.left - box.left + r.width/2, (bottom ? r.bottom : r.top) - box.top]; }
      svg.setAttribute('width', box.width); svg.setAttribute('height', box.height);
      data.forEach(function(n){
        (n[1].length ? n[1] : ['__root']).forEach(function(p){
          var a = pt(p, true), b = pt(n[0], false); if (!a || !b) return;
          var my = (a[1] + b[1]) / 2;
          d += '<path class="st-edge ' + (n[2] === 'running' ? 'flow' : n[2] === 'done' ? 'done' : '') + '" d="M' + a[0] + ' ' + a[1] + 'C' + a[0] + ' ' + my + ' ' + b[0] + ' ' + my + ' ' + b[0] + ' ' + b[1] + '"/>';
        });
      });
      svg.innerHTML = d;
    });
  };

  function listHTML(m){ return '<div class="st-list">' + S.treeHTML(m) + '</div>'; }

  /* ---- hover (lista de pedidos) ---- */
  var tip = null, tipTimer = null;
  function ensureTip(){ if (tip) return tip; tip = document.createElement('div'); tip.id = 'stTip'; tip.hidden = true;
    document.body.appendChild(tip); return tip; }
  S.showTip = function(anchor, m, immediate){
    S.hideTip();
    function show(){
      if (!anchor.isConnected) return;
      var t = ensureTip();
      t.innerHTML = '<div class="st-tip-head">' + ui('Estratégia do pedido','Request strategy') + '</div>' + (wide() ? S.treeHTML(m) : listHTML(m)); t.hidden = false;
      var r = anchor.getBoundingClientRect(), w = Math.min(380, window.innerWidth - 16);
      t.style.width = w + 'px'; t.style.left = Math.max(8, Math.min(r.left, window.innerWidth - w - 8)) + 'px';
      var below = r.bottom + 6; t.style.top = below + 'px';
      var h = t.getBoundingClientRect().height; if (below + h > window.innerHeight - 8) t.style.top = Math.max(8, r.top - h - 6) + 'px';
      S.drawEdges(t);
    }
    if (immediate) show(); else tipTimer = setTimeout(show, 300);
  };
  S.hideTip = function(){ clearTimeout(tipTimer); tipTimer = null; if (tip) tip.hidden = true; };
  document.addEventListener('keydown',function(ev){ if (ev.key === 'Escape') S.hideTip(); });
  document.addEventListener('pointerdown',function(ev){ if (!ev.target.closest('#stTip,.st-chip')) S.hideTip(); });

  /* ---- painel lateral ---- */
  var panel, body, openKey = 'maestroStrategyOpen', turn = '', sig = '', thinkNext = false, pendingTurn = '', pendingChat = '';
  function isOpen(){ return localStorage.getItem(openKey) !== '0'; }
  function current(){
    var st = window.__maestroState || {}, chat = st.currentChat; if (!chat) return null;
    var job = st.activeJob;
    if (job && job.status === 'running' && job.workflow && job.chat_id !== (chat.id)) job = null;
    var list = [];
    (chat.messages || []).forEach(function(msg){ if (msg.plan && msg.plan.workflow && msg.plan.workflow.nodes) list.push([msg.turn_id || msg.plan.workflow.turn_id || '', msg.plan.workflow]); });
    // The live snapshot replaces the saved snapshot for the same request.
    if (job && job.status === 'running' && job.chat_id === chat.id && job.workflow && job.workflow.nodes) {
      var key = job.task_id || job.workflow.turn_id || 'live';
      list = list.filter(function(x){ return x[0] !== key; });
      list.push([key, job.workflow, 1]);
    }
    if (!list.length) return null;
    var wanted = thinkNext && chat.id === pendingChat ? pendingTurn : turn;
    var pick = list.filter(function(x){ return x[0] === wanted || (wanted === 'live' && x[2]); })[0] || list[list.length - 1];
    return S.fromWorkflow(pick[1], pick[0]);
  }
  function followScroll(){
    var sc = document.getElementById('chatScroll'); if (!sc) return;
    var mid = sc.getBoundingClientRect(); mid = mid.top + mid.height * 0.5; var best = null, bd = 1e9;
    sc.querySelectorAll('.wf-panel, .live-workflow').forEach(function(el){
      var r = el.getBoundingClientRect(), d = Math.abs((r.top + r.bottom) / 2 - mid);
      if (el.matches('.live-workflow') && !el.children.length) return;
      if (d < bd){ var b = el.matches('[data-wf-turn]') ? el : el.querySelector('[data-wf-turn]'); bd = d; best = el.matches('.live-workflow') ? 'live' : (b ? b.getAttribute('data-wf-turn') : ''); }
    });
    if (best && best !== turn){ turn = best; refresh(); }
  }
  function refresh(){
    if (!panel) return;
    var insp = document.getElementById('inspectorRight'), ir = insp ? insp.getBoundingClientRect() : null;
    panel.style.right = ((ir && ir.width > 8) ? window.innerWidth - ir.left + 10 : 10) + 'px';
    var open = isOpen(); panel.classList.toggle('closed', !open);
    document.body.classList.toggle('st-open', open && wide());
    var pane = document.getElementById('paneChat');
    var reserve = open && wide() && pane ? Math.max(0, pane.getBoundingClientRect().right - panel.getBoundingClientRect().left + 12) : 0;
    document.body.style.setProperty('--st-w', reserve + 'px');
    panel.setAttribute('aria-label', ui('Estratégia do pedido','Request strategy'));
    var toggle = panel.querySelector('#stToggle');
    toggle.setAttribute('aria-expanded', String(open));
    toggle.setAttribute('aria-label', ui('Mostrar ou ocultar a estratégia','Show or hide strategy'));
    toggle.title = ui('Estratégia','Strategy');
    panel.querySelector('header strong').textContent = ui('Estratégia','Strategy');
    panel.querySelector('#stClose').setAttribute('aria-label', ui('Fechar','Close'));
    window.dispatchEvent(new Event('strategyvisibilitychange'));
    var m = open ? current() : null;
    var highlight = thinkNext && m && m.turn === pendingTurn && (window.__maestroState || {}).currentChat.id === pendingChat;
    if (highlight) turn = pendingTurn;
    var LBL = labels();
    var s = open ? JSON.stringify([m, highlight, ui('pt','en')]) + panel.style.right : 'x';
    if (s === sig) return; sig = s;
    if (!m){ body.innerHTML = '<p class="st-empty">' + ui('Sem plano multiagente neste pedido. Quando o Maestro dividir o trabalho, a árvore aparece aqui.','No multi-agent plan for this request. The tree appears here when Maestro splits the work.') + '</p>'; return; }
    var evs = [];
    m.nodes.forEach(function(n){ if (n.started) evs.push([n.started, ui('Início · ','Started · ') + n.title]); if (n.finished) evs.push([n.finished, (LBL[n.status] || n.status) + ' · ' + n.title]); if (n.guided) evs.push([n.started || 0, n.guided + ui(' orientação(ões) · ',' guidance update(s) · ') + n.title]); });
    evs.sort(function(a, b){ return a[0] - b[0]; });
    body.innerHTML = '<section class="st-think' + (highlight ? ' pulse' : '') + '" id="stThink"><h4>💭 ' + ui('Pensamento do orquestrador','Orchestrator thinking') + '</h4><p>' + e(reason(m)) + '</p></section>' +
      S.treeHTML(m) + (evs.length ? '<h4>' + ui('Decisões','Decisions') + '</h4><ol class="st-events">' + evs.slice(-12).map(function(x){ return '<li><time>' + (x[0] ? new Date(x[0] * 1000).toLocaleTimeString(ui('pt-PT','en-GB'), {hour:'2-digit', minute:'2-digit', second:'2-digit'}) : '') + '</time> ' + e(x[1]) + '</li>'; }).join('') + '</ol>' : '');
    S.drawEdges(body);
    if (highlight){ var t = document.getElementById('stThink'); if (t) t.scrollIntoView({block:'nearest'}); thinkNext = false; pendingTurn = ''; pendingChat = ''; }
  }
  S.open = function(thinking){ localStorage.setItem(openKey, '1'); thinkNext = !!thinking; sig = ''; refresh(); };
  // Apontador: abre o pensamento do orquestrador desse pedido (o chat abre pelo clique da linha).
  S.point = function(requestTurn, chatId){ if (wide()){ pendingTurn = requestTurn || ''; pendingChat = chatId; S.open(true); } };

  function build(){
    panel = document.createElement('aside'); panel.id = 'stPanel'; panel.setAttribute('aria-label', ui('Estratégia do pedido','Request strategy'));
    panel.innerHTML = '<button type="button" class="st-toggle" id="stToggle" aria-controls="stBody" aria-expanded="false" aria-label="' + ui('Mostrar ou ocultar a estratégia','Show or hide strategy') + '" title="' + ui('Estratégia','Strategy') + '">🧭</button>' +
      '<div class="st-main"><header><strong>' + ui('Estratégia','Strategy') + '</strong><button type="button" class="st-close" id="stClose" aria-label="' + ui('Fechar','Close') + '">✕</button></header><div class="st-body" id="stBody"></div></div>';
    document.body.appendChild(panel); body = panel.querySelector('#stBody');
    function close(){ localStorage.setItem(openKey, '0'); thinkNext = false; refresh(); panel.querySelector('#stToggle').focus(); }
    panel.querySelector('#stClose').onclick = close;
    panel.addEventListener('keydown',function(ev){ if(ev.key === 'Escape'){ ev.preventDefault(); close(); } });
    if (window.ResizeObserver){ new ResizeObserver(function(){ refresh(); }).observe(document.getElementById('paneChat')); }
    panel.querySelector('#stToggle').onclick = function(){ if (isOpen()) localStorage.setItem(openKey, '0'); else localStorage.setItem(openKey, '1'); sig = ''; refresh(); };
    var sc = document.getElementById('chatScroll'); if (sc) sc.addEventListener('scroll', followScroll, {passive:true});
    window.addEventListener('resize', function(){ sig = ''; refresh(); });
    setInterval(function(){ if (!document.hidden && wide()){ followScroll(); refresh(); } }, 1500);
    refresh();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', build); else build();
})();
