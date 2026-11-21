/* ═════════ Interface kit ═════════
   One line-icon set, teaching empty states, swipe on tasks and follow-up cards,
   the sync chip, the first-run tour, and launch-from-shortcut handling. */

// ── One icon set (24px grid, 1.8 stroke, currentColor) ───────────────────────
const ICONS = {
  eye:'<path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12z"/><circle cx="12" cy="12" r="3"/>',
  check:'<path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2h11"/>',
  tick:'<path d="M20 6L9 17l-5-5"/>',
  clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  alert:'<path d="M10.3 3.9L1.8 18a2 2 0 001.7 3h17a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z"/><path d="M12 9v4M12 17h.01"/>',
  link:'<circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><path d="M8.6 13.5l6.8 4M15.4 6.5l-6.8 4"/>',
  lock:'<rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 018 0v4"/>',
  queue:'<path d="M4 6h16M4 12h16M4 18h10"/>',
  retry:'<path d="M21 12a9 9 0 11-2.6-6.3M21 3v6h-6"/>',
  stop:'<circle cx="12" cy="12" r="9"/><path d="M5.6 5.6l12.8 12.8"/>',
  sparkle:'<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/>',
  search:'<circle cx="11" cy="11" r="7"/><path d="M21 21l-4-4"/>',
  note:'<path d="M14 3H6a2 2 0 00-2 2v14a2 2 0 002 2h12a2 2 0 002-2V9z"/><path d="M14 3v6h6M8 13h8M8 17h5"/>',
  target:'<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',
  calendar:'<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>',
  bell:'<path d="M18 8A6 6 0 006 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.7 21a2 2 0 01-3.4 0"/>',
  pulse:'<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
  thermo:'<path d="M14 14.8V5a2 2 0 00-4 0v9.8a4 4 0 104 0z"/>',
  mic:'<rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 11a7 7 0 0014 0M12 18v4"/>',
  camera:'<path d="M23 19a2 2 0 01-2 2H3a2 2 0 01-2-2V8a2 2 0 012-2h4l2-3h6l2 3h4a2 2 0 012 2z"/><circle cx="12" cy="13" r="4"/>',
  pen:'<path d="M12 20h9M16.5 3.5a2.1 2.1 0 013 3L7 19l-4 1 1-4z"/>',
  plus:'<path d="M12 5v14M5 12h14"/>',
  arrow:'<path d="M5 12h14M12 5l7 7-7 7"/>',
  offline:'<path d="M1 1l22 22M16.7 11a11 11 0 015.2 2.8M5 12.9a11 11 0 015.2-2.8M10.7 5.1A16 16 0 0122.6 9M1.4 9a16 16 0 013.7-2.6M8.5 16.4a6 6 0 017 0M12 20h.01"/>',
  trash:'<path d="M3 6h18"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/>',
  layers:'<path d="M12 2l10 5-10 5L2 7z"/><path d="M2 17l10 5 10-5M2 12l10 5 10-5"/>',
  chip:'<rect x="5" y="5" width="14" height="14" rx="2"/><path d="M9 1v4M15 1v4M9 19v4M15 19v4M1 9h4M1 15h4M19 9h4M19 15h4"/>',
  flame:'<path d="M12 22c4 0 7-3 7-7 0-3-2-5-3-7-1 2-2 3-4 3 0-4-1-7-4-9 0 5-3 7-3 12 0 4 3 8 7 8z"/>',
  snow:'<path d="M12 2v20M4.9 6.5l14.2 11M19.1 6.5L4.9 17.5"/>',
  pin:'<path d="M12 17v5"/><path d="M9 10.8a2 2 0 01-1.1 1.8l-1.8.9A2 2 0 005 15.2V16a1 1 0 001 1h12a1 1 0 001-1v-.8a2 2 0 00-1.1-1.8l-1.8-.9A2 2 0 0115 10.8V7a1 1 0 011-1 2 2 0 000-4H8a2 2 0 000 4 1 1 0 011 1z"/>',
  hash:'<path d="M4 9h16M4 15h16M10 3L8 21M16 3l-2 18"/>',
  wallet:'<path d="M20 7H5a2 2 0 010-4h12v4"/><path d="M3 5v14a2 2 0 002 2h15V7"/><path d="M16 14h.01"/>',
  "chev-l":'<path d="M15 18l-6-6 6-6"/>',
  "chev-r":'<path d="M9 18l6-6-6-6"/>',
};
function svgIcon(name, size){
  const s = size || 18;
  return `<svg class="ic" width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${ICONS[name]||""}</svg>`;
}

// ── Teaching empty states: what will appear here, and what the agent does ────
function emptyHtml(o){
  const acts = (o.acts||[]).map((a,i)=>`<button class="empty-go${i?" alt":""}" data-empty="${a[1]}">${svgIcon(a[2]||"plus",15)}${esc(a[0])}</button>`).join("");
  return `<div class="empty"><div class="empty-i">${svgIcon(o.icon,32)}</div>
    <div class="empty-t">${esc(o.title)}</div><div class="empty-b">${o.body}</div>
    ${acts?`<div class="empty-acts">${acts}</div>`:""}</div>`;
}
const EMPTY = {
  feed:{icon:"note", title:"Your first note starts here",
    body:"Write it, say it, or snap it. In the background the agent adds a title, tags, tasks and reminders — and it all stays on your phone.",
    acts:[["Write a note","text","pen"],["Voice","voice","mic"],["Photo","photo","camera"]]},
  actions:{icon:"check", title:"No tasks yet",
    body:"When a note says “call the bank” or “send the report”, the agent lifts it here. Tasks that sit for a week come back as a gentle prompt on your Agent Dashboard.",
    acts:[["Write a note","text","pen"]]},
  reminders:{icon:"bell", title:"No reminders",
    body:"When a note mentions a date or time — “dentist on Friday” — the agent sets a reminder and can add it to Google Calendar.",
    acts:[["Add an event","event","calendar"]]},
  connections:{icon:"link", title:"No connections yet",
    body:"As you write, the agent links notes that share ideas. After a few related notes you'll see clusters and a map of your thinking here.",
    acts:[["Write a note","text","pen"]]},
};
document.addEventListener("click", e=>{
  const b = e.target.closest("[data-empty]"); if (!b) return;
  const k = b.dataset.empty;
  if (k==="text") openEditor("new");
  else if (k==="voice") openRecorder(null);
  else if (k==="photo") pickPhotos(null);
  else if (k==="event") openQuickEvent();
});

// ── Swipe on task rows and follow-up cards ───────────────────────────────────
// Right = the good move (done / plan it), left = the let-go move (drop / not now).
// Vertical scrolling always wins (rows declare touch-action:pan-y and the drag is
// direction-locked), every action has Undo, and every row keeps its visible buttons
// or tap target so nothing depends on discovering the gesture.
(function(){
  const calm = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
  const KINDS = [
    { sel:"#aBody .aitem[data-aid]",
      right:row=>({label:+row.dataset.done?"Reopen":"Done", icon:"tick", tone:"ok", run:()=>toggleAction(row.dataset.aid, +row.dataset.done)}),
      left:row=>({label:"Drop", icon:"trash", tone:"warn", run:()=>dropTask(row.dataset.aid)}) },
    { sel:"#fuSlot .fu-i[data-fu-id]",
      // A drop suggestion's "plan" is deleting the task. The swipe still does
      // what the card suggests, but says so: a green "Do it" tick over a
      // delete is the label lying about the gesture.
      right:row=>(row.dataset.fuMove==="drop"
        ? {label:"Drop", icon:"trash", tone:"warn", run:()=>fuAct("accept", +row.dataset.fuId)}
        : {label:"Do it", icon:"tick", tone:"ok", run:()=>fuAct("accept", +row.dataset.fuId)}),
      left:row=>({label:"Not now", icon:"clock", tone:"mute", run:()=>fuAct("dismiss", +row.dataset.fuId)}) },
  ];
  let st = null, swallow = false;
  const thr = w => Math.max(72, w * 0.4);
  const clean = s => {
    if (s.under) s.under.remove();
    s.row.classList.remove("sw-row","sw-snap");
    s.row.style.transform = ""; s.row.style.opacity = "";
  };
  document.addEventListener("pointerdown", e=>{
    if (e.pointerType === "mouse" || !e.isPrimary) return;
    for (const k of KINDS){
      const row = e.target.closest(k.sel);
      if (!row || e.target.closest("button,a,input,textarea,[data-goto],[data-pact]")) continue;
      st = { k, row, pid:e.pointerId, x0:e.clientX, y0:e.clientY, dx:0, lock:null, w:row.offsetWidth||200, crossed:false, under:null };
      return;
    }
  }, {passive:true});
  document.addEventListener("pointermove", e=>{
    if (!st || e.pointerId !== st.pid) return;
    const dx = e.clientX - st.x0, dy = e.clientY - st.y0;
    if (!st.lock){
      if (Math.abs(dx) < 10 && Math.abs(dy) < 10) return;
      st.lock = Math.abs(dx) > Math.abs(dy) * 1.4 ? "x" : "y";
      if (st.lock === "y"){ st = null; return; }
      const r = st.row, host = r.parentElement;
      if (getComputedStyle(host).position === "static") host.style.position = "relative";   // before measuring
      const u = document.createElement("div");
      u.className = "sw-under";
      u.style.top = r.offsetTop+"px"; u.style.height = r.offsetHeight+"px";
      host.insertBefore(u, r);
      st.under = u; r.classList.add("sw-row");
      try { r.setPointerCapture(e.pointerId); } catch(_){}
    }
    st.dx = dx;
    const side = dx < 0 ? st.k.left(st.row) : st.k.right(st.row);
    const p = Math.min(1, Math.abs(dx) / thr(st.w));
    st.row.style.transform = `translateX(${dx}px)`;
    st.under.className = `sw-under ${dx<0?"L":"R"} ${side.tone}${p>=1?" armed":""}`;
    st.under.innerHTML = `<span style="opacity:${(0.35+0.65*p).toFixed(2)}">${svgIcon(side.icon,18)}${esc(side.label)}</span>`;
    const crossed = p >= 1;
    if (crossed && !st.crossed && navigator.vibrate){ try { navigator.vibrate(10); } catch(_){} }
    st.crossed = crossed;
  }, {passive:true});
  const finish = (e, cancelled) => {
    if (!st || e.pointerId !== st.pid) return;
    const s = st; st = null;
    if (s.lock !== "x") return;
    swallow = true; setTimeout(()=>{ swallow = false; }, 350);
    if (cancelled || Math.abs(s.dx) < thr(s.w)){
      s.row.classList.add("sw-snap"); s.row.style.transform = "";
      setTimeout(()=>clean(s), calm() ? 0 : 220); return; }
    const side = s.dx < 0 ? s.k.left(s.row) : s.k.right(s.row);
    s.row.classList.add("sw-snap");
    s.row.style.transform = `translateX(${s.dx<0?"-":""}110%)`; s.row.style.opacity = "0";
    setTimeout(async ()=>{ try { await side.run(); } finally { clean(s); } }, calm() ? 0 : 180);
  };
  document.addEventListener("pointerup", e=>finish(e,false), {passive:true});
  document.addEventListener("pointercancel", e=>finish(e,true), {passive:true});
  document.addEventListener("click", e=>{
    if (!swallow) return;
    swallow = false; e.stopPropagation(); e.preventDefault();
  }, true);
  // Keyboard: a task row is a checkbox (Space/Enter), so nothing needs a gesture.
  document.addEventListener("keydown", e=>{
    if (e.key !== " " && e.key !== "Enter") return;
    const row = e.target.closest && e.target.closest("#aBody .aitem[data-aid]");
    if (!row || e.target !== row) return;
    e.preventDefault(); toggleAction(row.dataset.aid, +row.dataset.done);
  });
})();
async function dropTask(aid){
  try { await api("/actions/"+aid, {method:"DELETE"}); }
  catch(e){ toast(e && e.offline ? "You're offline — try again when connected" : "Couldn't drop that task", "err"); await loadActions(); return; }
  toast("Task dropped","ok",{label:"Undo", fn: async ()=>{
    await api("/actions/"+aid+"/restore",{method:"POST"}).catch(()=>null);
    await Promise.all([loadActions(), loadStats()]); }});
  await Promise.all([loadActions(), loadStats()]);
}

// ── Sync chip: are my changes safe, and is the agent listening? ──────────────
let _syncEl = null;
async function syncState(){
  let queued = 0;
  try { queued = (await idb.all("outbox")).length; } catch(e){}
  const agentOff = !!(S.home && S.home.agent && S.home.agent.state === "offline");
  if (!NET.online) return {cls:"off", label:"Offline", tip: queued ? `Offline. ${queued} change${queued===1?"":"s"} saved on this device and waiting to sync.` : "Offline. Changes are saved on this device and sync when you're back."};
  if (queued) return {cls:"q", label:`${queued} queued`, tip:`${queued} change${queued===1?"":"s"} waiting to sync. Tap to sync now.`};
  if (agentOff) return {cls:"ag", label:"Agent off", tip:"Your notes are saved. The agent is offline, so titles, tags and tasks will appear when it's back."};
  return {cls:"ok", label:"Synced", tip:"Everything is saved and the agent is listening."};
}
async function syncRefresh(){
  if (!_syncEl) return;
  const s = await syncState();
  _syncEl.className = "syncchip "+s.cls;
  _syncEl.innerHTML = `<span class="sc-dot"></span><span class="sc-l">${esc(s.label)}</span>`;
  _syncEl.setAttribute("aria-label", s.tip); _syncEl._tip = s.tip; _syncEl._cls = s.cls;
}
function initSyncChip(){
  if (_syncEl) return;
  const anchor = $("srchBtn"); if (!anchor) return;
  _syncEl = document.createElement("button");
  _syncEl.type = "button"; _syncEl.id = "syncChip";
  anchor.parentNode.insertBefore(_syncEl, anchor);
  _syncEl.addEventListener("click", ()=>{
    toast(_syncEl._tip || "", _syncEl._cls==="ok" ? "ok" : "");
    if (_syncEl._cls==="q" && NET.online) flushOutbox().then(syncRefresh);
  });
  const _so = setOnline; setOnline = function(on){ _so(on); syncRefresh(); };
  const _rh = renderHomeStrips; renderHomeStrips = function(){ _rh(); syncRefresh(); };
  addEventListener("online", syncRefresh); addEventListener("offline", syncRefresh);
  setInterval(syncRefresh, 4000);
  syncRefresh();
}

// ── First-run tour (skippable, replayable from Settings) ─────────────────────
const TOUR = [
  {icon:"pen", t:"Capture anything", b:"Tap + to write, speak, snap or sketch. Share from other apps straight into Quicksilver. Long-press the app icon for shortcuts."},
  {icon:"sparkle", t:"The agent tidies up", b:"A model on your phone adds a title, tags, tasks and reminders. Nothing leaves the device, and you can switch any of it off in Settings."},
  {icon:"search", t:"Ask your notes", b:"Tap ✦ in the search bar and ask in plain words. Answers come only from your notes, with the sources shown."},
  {icon:"check", t:"Tasks that don't rot", b:"Once a day the agent brings back tasks that have sat for a week and suggests one move. Nothing changes until you tap. Swipe a task right for done, left to drop."},
];
let _tourStep = 0, _tourPrev = null;
function tourSeen(){ try { return localStorage.getItem("hg_tour")==="1"; } catch(e){ return true; } }
function tourDone(){ try { localStorage.setItem("hg_tour","1"); } catch(e){} }
function openTour(){
  closeOverlays && closeOverlays();
  _tourStep = 0; _tourPrev = document.activeElement;
  let el = $("tour");
  if (!el){
    el = document.createElement("div"); el.id = "tour";
    el.setAttribute("role","dialog"); el.setAttribute("aria-modal","true"); el.setAttribute("aria-label","Quick tour");
    document.body.appendChild(el);
  }
  el.classList.add("on"); renderTour();
}
function renderTour(){
  const el = $("tour"), s = TOUR[_tourStep], last = _tourStep === TOUR.length-1;
  el.innerHTML = `<div class="tour-card">
    <div class="tour-ic">${svgIcon(s.icon,34)}</div>
    <div class="tour-t">${esc(s.t)}</div><div class="tour-b">${esc(s.b)}</div>
    <div class="tour-dots" aria-hidden="true">${TOUR.map((_,i)=>`<i class="${i===_tourStep?"on":""}"></i>`).join("")}</div>
    <div class="tour-nav"><button class="tour-skip" data-tour="skip">${last?"":"Skip"}</button>
      <button class="tour-next" data-tour="next">${last?"Start writing":"Next"}</button></div></div>`;
  el.querySelector(".tour-next").focus();
}
function closeTour(){
  const el = $("tour"); if (el) el.classList.remove("on");
  tourDone(); try { _tourPrev && _tourPrev.focus && _tourPrev.focus(); } catch(e){}
}
document.addEventListener("click", e=>{
  const b = e.target.closest("[data-tour]"); if (!b) return;
  if (b.dataset.tour === "skip") closeTour();
  else if (_tourStep < TOUR.length-1){ _tourStep++; renderTour(); }
  else closeTour();
});
document.addEventListener("keydown", e=>{
  if (e.key==="Escape" && $("tour") && $("tour").classList.contains("on")) closeTour();
});

// ── Launch from a shortcut or the share sheet ────────────────────────────────
function launchFromUrl(){
  let q; try { q = new URLSearchParams(location.search); } catch(e){ return; }
  const nw = q.get("new"), ask = q.get("ask"), shared = q.get("shared"), held = q.get("share_text"), open_ = q.get("note"), bill_ = q.get("bill");
  if (!nw && !ask && !shared && !held && !open_ && !bill_) return;
  try { history.replaceState(null, "", location.pathname); } catch(e){}
  if (held){          // shared while the server was unreachable: save through the offline queue
    createNote(held.slice(0, 100000)).then(()=>toast("Shared text saved","ok"));
  } else if (bill_){          // a bill's notification: its message, links and Mark as paid
    openBill(bill_);
  } else if (open_){          // a link from Medha's brief or a notification: open that note, quietly
    openDetail(open_).catch(()=>toast("That note is gone","err"));
  } else if (shared){
    toast("Saved to Quicksilver","ok");
    openDetail(shared).catch(()=>{});
  } else if (nw === "voice") openRecorder(null);
  else if (nw === "photo") pickPhotos(null);
  else if (nw) openEditor("new");
  else if (ask) setAskMode(true);
  tourDone();           // arriving with a purpose: never greet with a tour
}

// ── Accessibility wiring ─────────────────────────────────────────────────────
function a11yInit(){
  const t = $("toasts"); if (t){ t.setAttribute("role","status"); t.setAttribute("aria-live","polite"); }
  const ob = $("offBar"); if (ob) ob.setAttribute("role","status");
  document.querySelectorAll(".sheet").forEach(s=>{
    s.setAttribute("role","dialog"); s.setAttribute("aria-modal","true");
    const h = s.querySelector(".sh-t"); if (h && !s.getAttribute("aria-label")) s.setAttribute("aria-label", h.textContent.trim());
  });
  document.querySelectorAll(".nitem[data-view]").forEach(n=>n.setAttribute("aria-label", n.textContent.trim().replace(/\s+\d+\+?$/,"")));
}
function initUiKit(){
  try { a11yInit(); } catch(e){}
  try { initSyncChip(); } catch(e){}
  try { launchFromUrl(); } catch(e){}
  try { if (!tourSeen() && (S.notes||[]).length < 3) setTimeout(()=>{ if (!tourSeen()) openTour(); }, 1400); } catch(e){}
}
