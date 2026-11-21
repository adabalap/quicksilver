/* ═════════ Scroll-to-top ═════════ */
$("mainSc").addEventListener("scroll", ()=>{
  const sc = $("mainSc");
  $("toTop").classList.toggle("on", sc.scrollTop > 400);
  // Infinite scroll: when the user nears the bottom of the notes view, pull the
  // next page. Only on the notes view (not actions/connections). The 600px
  // threshold starts the fetch before they hit the end, so it feels seamless.
  if ($("vNotes").classList.contains("on")
      && sc.scrollHeight - sc.scrollTop - sc.clientHeight < 600){
    loadMore();
  }
});

/* ═════════ Boot: offline-first paint, then network ═════════ */
// ── Back-button / history management for overlay layers ──
// Ordered by visual stacking (topmost first). Each entry: how to detect it's
// open, and how to close it. On the OS back gesture we close the top open one.
const BACK_LAYERS = [
  { id:"modalLayer", close:()=>_closeModal(null) },
  { id:"edLayer",    close:()=>closeEditor() },
  { id:"lightbox",   close:()=>closeLightbox() },
  { id:"photoMenu",  close:()=>$("photoMenu").classList.remove("on") },
  { id:"videoMenu",  close:()=>$("videoMenu").classList.remove("on") },
  { id:"sketchSheet",close:()=>closeSketch() },
  { id:"recSheet",   close:()=>closeRecorder() },
  { id:"importSheet",close:()=>$("importSheet").classList.remove("on") },
  { id:"trashSheet", close:()=>$("trashSheet").classList.remove("on") },
  ...["shareSheet","storySheet","filterSheet","digestSheet","dupSheet","healthSheet"].map(id=>({ id, close:()=>$(id).classList.remove("on") })),
  { id:"actionSheet",close:()=>{ $("actionSheet").classList.remove("on"); $("actionScrim").classList.remove("on"); } },
  { id:"adoptSheet", close:()=>$("adoptSheet").classList.remove("on") },
  { id:"tplSheet",   close:()=>$("tplSheet").classList.remove("on") },
  { id:"errSheet",   close:()=>$("errSheet")?.classList.remove("on") },
  { id:"tagsSheet",  close:()=>$("tagsSheet").classList.remove("on") },
  { id:"pulseSheet", close:()=>$("pulseSheet").classList.remove("on") },
  { id:"dSheet",     close:()=>closeDetail() },
  { id:"menuLayer",  close:()=>$("menuLayer").classList.remove("on") },
];
function _topOpenLayer(){
  for (const L of BACK_LAYERS){
    const el = $(L.id);
    if (el && el.classList.contains("on")) return L;
  }
  return null;
}
function pushLayerState(){
  // Push a history entry for EACH layer that opens, so every Back press closes
  // exactly one layer before the app can exit. We tag each with a depth so
  // stacked sheets (detail → sub-sheet) each get their own back-step rather than
  // collapsing into one (which used to let a second Back minimise the app while
  // a layer was still open).
  const depth = (history.state && history.state.qsDepth) || 0;
  history.pushState({ qsLayer:true, qsDepth: depth + 1 }, "");
}
let _closingViaPop = false;
window.addEventListener("popstate", ()=>{
  const L = _topOpenLayer();
  if (L){
    _closingViaPop = true;
    L.close();
    _closingViaPop = false;
  }
  // If nothing was open, the pop proceeds normally (may exit app) — expected.
});
// Each layer open pushes its own history entry; each UI-driven close consumes
// one (via history.back), so the browser history depth always matches the number
// of open layers. This keeps the system Back button perfectly in step: it closes
// the top layer, never skips, and never minimises the app while a layer is open.
BACK_LAYERS.forEach(L=>{
  const el = $(L.id);
  if (!el) return;
  let wasOpen = el.classList.contains("on");
  const obs = new MutationObserver(()=>{
    const isOpen = el.classList.contains("on");
    if (isOpen && !wasOpen){
      pushLayerState();                 // opened → add a back-step
    } else if (!isOpen && wasOpen && !_closingViaPop){
      // Closed by UI (not the Back key). Defer the decision to the next tick so
      // any layer that's opening in the SAME user action (e.g. menu → pulse
      // sheet) has settled. Only consume a history entry on a genuine dismiss to
      // the base app (nothing else open) — otherwise history.back()'s delayed
      // popstate would close the just-opened layer and drop the user to notes.
      setTimeout(()=>{
        if (!_topOpenLayer() && history.state && history.state.qsLayer && !_closingViaPop){
          _closingViaPop = true;
          history.back();
          setTimeout(()=>{ _closingViaPop = false; }, 0);
        }
      }, 0);
    }
    wasOpen = isOpen;
  });
  obs.observe(el, {attributes:true, attributeFilter:["class"]});
});

// ── Checklist notes (Keep-style items) ──
function parseChecklist(body){
  // returns [{text, done, raw}]; non-item lines are kept as {text, item:false}
  return String(body||"").split("\n").map(line=>{
    const m = line.match(/^\s*-?\s*\[([ xX])\]\s?(.*)$/);
    if (m) return { item:true, done:/[xX]/.test(m[1]), text:m[2] };
    return { item:false, text:line };
  });
}
function checklistToBody(items){
  // Drop empty item rows so a blank checkbox never persists in the body.
  return items
    .filter(it=> !it.item || (it.text && it.text.trim()))
    .map(it=> it.item ? `- [${it.done?"x":" "}] ${it.text.trim()}` : it.text)
    .join("\n");
}
function renderChecklist(n){
  const items = parseChecklist(n.body);
  const rows = items.map((it,i)=>{
    if (!it.item){
      // preserve non-item lines (e.g. a heading) as light text
      return it.text.trim() ? `<div class="cl-note-line">${esc(it.text)}</div>` : "";
    }
    if (!it.text.trim()) return "";   // skip empty item rows (the seed line)
    return `<div class="cl-row${it.done?" done":""}" data-cl-i="${i}">
      <button class="cl-box" data-cl-toggle="${i}" aria-label="Toggle">${it.done?"✓":""}</button>
      <span class="cl-txt">${esc(it.text)}</span>
      <button class="cl-del" data-cl-del="${i}" aria-label="Remove">×</button>
    </div>`;
  }).join("");
  const openCount = items.filter(it=>it.item && !it.done).length;
  const doneCount = items.filter(it=>it.item && it.done).length;
  return `<div class="sect" style="margin-top:0">Checklist${doneCount?` · ${openCount} left`:""}</div>
    <div class="cl-card">
      ${rows}
      <div class="cl-add">
        <span class="cl-add-ic">+</span>
        <input class="cl-add-in" id="clAdd" placeholder="Add item…" />
      </div>
    </div>`;
}
function sortChecklist(items){
  // Open items keep their order at the top; completed items sink to the bottom
  // (also preserving their relative order). Non-item lines (headings) stay put
  // at the front.
  const heads = items.filter(it=>!it.item);
  const open  = items.filter(it=>it.item && !it.done);
  const done  = items.filter(it=>it.item && it.done);
  return [...heads, ...open, ...done];
}
async function saveChecklist(items){
  if (!S.cur) return;
  const newBody = checklistToBody(sortChecklist(items));
  const up = await patchNote(S.cur.id, {body:newBody}, true).catch(()=>null);
  if (up){ S.cur = up; renderDetail(up); renderNotes(); }
  else { S.cur.body = newBody; renderDetail(S.cur); }
}
async function toggleChecklistItem(i){
  const items = parseChecklist(S.cur.body);
  if (items[i] && items[i].item){ items[i].done = !items[i].done; await saveChecklist(items); }
}
async function deleteChecklistItem(i){
  const items = parseChecklist(S.cur.body);
  if (items[i]){ items.splice(i,1); await saveChecklist(items); }
}
async function addChecklistItem(text){
  text = (text||"").trim(); if (!text) return;
  const items = parseChecklist(S.cur.body);
  items.push({item:true, done:false, text});
  await saveChecklist(items);
  requestAnimationFrame(()=>{ const el=$("clAdd"); if(el){ el.focus(); } });
}
async function addChecklistLines(raw){
  // Split a pasted blob into individual items — one per non-empty line.
  // Strips existing bullet/checkbox prefixes so pasted lists come in clean.
  const lines = String(raw||"").split(/\r?\n/)
    .map(l=>l.replace(/^\s*(?:[-*]\s*)?(?:\[[ xX]\]\s*)?/,"").trim())
    .filter(Boolean);
  if (!lines.length) return;
  const items = parseChecklist(S.cur.body);
  lines.forEach(text=>items.push({item:true, done:false, text}));
  await saveChecklist(items);
  requestAnimationFrame(()=>{ const el=$("clAdd"); if(el){ el.focus(); } });
}
// Paste into the add-item field: multi-line → multiple rows.
document.addEventListener("paste", async e=>{
  if (!e.target || e.target.id!=="clAdd") return;
  const txt = (e.clipboardData||window.clipboardData)?.getData("text") || "";
  if (txt.includes("\n")){
    e.preventDefault(); e.target.value=""; await addChecklistLines(txt);
  }
});
async function openChecklistNew(){
  // Create an empty checklist note and open it in checklist detail view.
  const id = nano();
  try { await api("/notes",{method:"POST",body:{id, body:"", note_type:"checklist"}});
    toast("Checklist created ☑️","ok"); }
  catch(e){ if(!e.offline){ toast("Couldn\'t create","err"); return; }
    idb.queue({kind:"create",id,body:"",iso:new Date().toISOString()}); }
  await refreshAll(); await openDetail(id);
  requestAnimationFrame(()=>{ const el=$("clAdd"); if(el) el.focus(); });
}

// ── Text highlighting: DRAG-to-highlight (robust range model) ──
// Rather than splice markers into the body (which drifts and orphans markers),
// we parse the body into (plainText + a list of {start,end,color} ranges over
// the PLAIN text), edit that model, then regenerate the body from scratch. This
// makes markers impossible to orphan — every save produces clean, valid markup.

function parseHighlights(body){
  // → { plain, ranges:[{start,end,color}] } over the plain (marker-free) text.
  const ranges = [];
  let plain = "";
  const re = new RegExp(HL_SRC, "gu");
  let last = 0, m;
  while ((m = re.exec(body)) !== null){
    plain += body.slice(last, m.index);
    const color = HL_COL[m[1]] || m[3] || "y";
    const text = (m[2] !== undefined ? m[2] : m[4]) || "";
    const s = plain.length;
    plain += text;
    ranges.push({ start:s, end:plain.length, color });
    last = re.lastIndex;
  }
  plain += body.slice(last);
  return { plain, ranges };
}

function buildBody(plain, ranges){
  // Regenerate the body from plain text + ranges. Ranges are normalised:
  // sorted, merged where adjacent/overlapping with the same colour, and any
  // zero-width or whitespace-only ranges dropped — so no stray markers survive.
  const clean = ranges
    .filter(r => r.end > r.start && plain.slice(r.start, r.end).trim())
    .sort((a,b)=> a.start - b.start);
  // merge overlaps: later same-colour overlaps absorb; different colours, the
  // later one wins on the overlap.
  const merged = [];
  for (const r of clean){
    const prev = merged[merged.length-1];
    if (prev && r.start <= prev.end && r.color === prev.color){
      prev.end = Math.max(prev.end, r.end);
    } else if (prev && r.start < prev.end && r.color !== prev.color){
      // trim the previous to avoid overlap, then push
      prev.end = r.start;
      if (prev.end <= prev.start) merged.pop();
      merged.push({...r});
    } else {
      merged.push({...r});
    }
  }
  let out = "", pos = 0;
  for (const r of merged){
    if (r.start > pos) out += plain.slice(pos, r.start);
    out += HL_OPEN + HL_CH[r.color] + plain.slice(r.start, r.end) + HL_CLOSE;
    pos = r.end;
  }
  out += plain.slice(pos);
  return out;
}

// Locate the current selection as a {start,end} range in the PLAIN text.
function _selectionPlainRange(){
  const nr = $("noteReadText");
  if (!nr || !S.cur) return null;
  const sel = window.getSelection();
  if (!sel || sel.isCollapsed || !sel.rangeCount) return null;
  const range = sel.getRangeAt(0);
  if (!nr.contains(range.commonAncestorContainer)) return null;
  let selText = sel.toString();
  if (!selText || !selText.trim()) return null;

  // ROBUST ANCHORING: don't rely on DOM↔plain offset alignment (paragraph
  // breaks, lists, and <br>s make the rendered DOM shorter than the stored
  // plain text, which caused highlights to drift). Instead, normalise both the
  // selection and the plain text to a whitespace-collapsed form, find the
  // selection by string match, and use a per-word context to disambiguate when
  // the same phrase appears more than once.
  const { plain } = parseHighlights(S.cur.body || "");

  // Normalise: collapse all runs of whitespace to a single space. Keep a map
  // from normalised index → original plain index so we can translate back.
  function normalise(str){
    const map = [];       // map[normIndex] = origIndex
    let out = "", prevWs = false;
    for (let i = 0; i < str.length; i++){
      const ch = str[i];
      if (/\s/.test(ch)){
        if (!prevWs){ out += " "; map.push(i); prevWs = true; }
      } else { out += ch; map.push(i); prevWs = false; }
    }
    map.push(str.length);
    return { norm: out, map };
  }
  const { norm: plainNorm, map } = normalise(plain);
  const selNorm = selText.replace(/\s+/g, " ").trim();
  if (!selNorm) return null;

  // Find the occurrence of the selection. If it appears multiple times, pick
  // the one nearest the caret using the selection's approximate DOM position.
  const occurrences = [];
  let idx = plainNorm.indexOf(selNorm);
  while (idx !== -1){ occurrences.push(idx); idx = plainNorm.indexOf(selNorm, idx + 1); }
  if (!occurrences.length) return null;

  let chosen = occurrences[0];
  if (occurrences.length > 1){
    // Disambiguate: build a rough caret offset from the DOM and pick the
    // occurrence closest to it (in normalised space, scaled).
    const domFull = (nr.textContent || "").replace(/\s+/g," ");
    const domIdx = domFull.indexOf(selNorm);
    if (domIdx !== -1){
      // approximate: same relative position in normalised plain text
      let best = occurrences[0], bestD = Infinity;
      for (const o of occurrences){
        const d = Math.abs(o - domIdx);
        if (d < bestD){ bestD = d; best = o; }
      }
      chosen = best;
    }
  }

  const start = map[chosen];
  const end = map[chosen + selNorm.length];
  if (start == null || end == null || end <= start) return null;
  return { start, end };
}

function _refreshHlBar(){
  const r = _selectionPlainRange();
  _hlSel = r;
  const bar = $("hlbar");
  bar.classList.toggle("on", !!r);
}
let _hlSel = null;
document.addEventListener("selectionchange", ()=>{
  clearTimeout(window._hlT);
  window._hlT = setTimeout(_refreshHlBar, 80);
});

// ── Note links: tap = quick Custom Tab; LONG-PRESS = full standalone browser ──
// A normal tap uses window.open (fast in-context Custom Tab). Holding a link
// ~500ms instead forces a full app-switch to the phone's default browser via an
// Android intent URL, so the user lands in a real, standalone browser tab.
let _lpTimer = null, _lpFired = false, _lpLink = null;
function _openInFullBrowser(url){
  // Build an Android intent URL that hands off to the default browser app and
  // falls back to the plain URL on non-Android platforms.
  try {
    const isAndroid = /Android/i.test(navigator.userAgent);
    if (isAndroid){
      const noScheme = url.replace(/^https?:\/\//, "");
      const scheme = url.startsWith("https") ? "https" : "http";
      const intent = `intent://${noScheme}#Intent;scheme=${scheme};`
        + `action=android.intent.action.VIEW;`
        + `S.browser_fallback_url=${encodeURIComponent(url)};end`;
      window.location.href = intent;
    } else {
      window.open(url, "_blank", "noopener,noreferrer");
    }
  } catch(_){ window.open(url, "_blank", "noopener,noreferrer"); }
}
function _lpStart(clientTarget){
  const lnk = clientTarget.closest && clientTarget.closest("a.nlink");
  if (!lnk) return;
  _lpFired = false; _lpLink = lnk;
  clearTimeout(_lpTimer);
  _lpTimer = setTimeout(()=>{
    _lpFired = true;
    if (navigator.vibrate) { try { navigator.vibrate(15); } catch(_){} }
    toast("Opening in browser…","ok");
    _openInFullBrowser(lnk.href);
  }, 500);
}
function _lpCancel(){ clearTimeout(_lpTimer); _lpLink = null; }
document.addEventListener("touchstart", e=>_lpStart(e.target), {passive:true});
document.addEventListener("touchend", _lpCancel, {passive:true});
document.addEventListener("touchmove", _lpCancel, {passive:true});
// Desktop parity: long mouse-press also forces the full browser.
document.addEventListener("mousedown", e=>{ if (e.button===0) _lpStart(e.target); });
document.addEventListener("mouseup", _lpCancel);

async function applyHighlightColor(color){
  if (!_hlSel || !S.cur){ return; }
  const { plain, ranges } = parseHighlights(S.cur.body || "");
  let { start, end } = _hlSel;
  let newRanges;
  if (color === "erase"){
    // Remove/clip any highlight overlapping the selection.
    newRanges = [];
    for (const r of ranges){
      if (r.end <= start || r.start >= end){ newRanges.push(r); continue; }
      if (r.start < start) newRanges.push({ start:r.start, end:start, color:r.color });
      if (r.end > end)     newRanges.push({ start:end, end:r.end, color:r.color });
    }
  } else {
    // Remove any existing highlight in the selected span, then add the new one.
    newRanges = [];
    for (const r of ranges){
      if (r.end <= start || r.start >= end){ newRanges.push(r); continue; }
      if (r.start < start) newRanges.push({ start:r.start, end:start, color:r.color });
      if (r.end > end)     newRanges.push({ start:end, end:r.end, color:r.color });
    }
    newRanges.push({ start, end, color });
  }
  const out = buildBody(plain, newRanges);
  $("hlbar").classList.remove("on");
  window.getSelection()?.removeAllRanges();
  _hlSel = null;
  const up = await patchNote(S.cur.id, { body: out }, true).catch(()=>null);
  if (up){ S.cur = up; renderDetail(up); renderNotes(); }
  else { S.cur.body = out; renderDetail(S.cur); }
}


if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(()=>{});

// Long-press to reveal tile quick-actions on touch devices (Keep-style press &
// hold). Desktop uses hover; this gives the same reach on mobile without a
// permanent button cluttering every tile. A press that turns into a scroll or a
// quick tap is NOT a long-press, so normal tapping/scrolling is unaffected.
(function(){
  let lpTimer=null, lpCard=null, lpX=0, lpY=0, lpFired=false;
  const feed = ()=>$("feed");
  const clearActs = ()=>{ document.querySelectorAll(".card.show-acts")
                            .forEach(c=>c.classList.remove("show-acts")); };
  document.addEventListener("touchstart", e=>{
    const card = e.target.closest(".card[data-id]");
    if (!card) { clearActs(); return; }
    if (e.target.closest(".c-act")) return;   // tapping an action, not a press
    lpCard = card; lpFired=false;
    lpX = e.touches[0].clientX; lpY = e.touches[0].clientY;
    lpTimer = setTimeout(()=>{
      lpFired = true;
      clearActs(); card.classList.add("show-acts");
      if (navigator.vibrate) navigator.vibrate(12);
    }, 500);
  }, {passive:true});
  document.addEventListener("touchmove", e=>{
    if (!lpTimer) return;
    const dx = Math.abs(e.touches[0].clientX - lpX);
    const dy = Math.abs(e.touches[0].clientY - lpY);
    if (dx>10 || dy>10){ clearTimeout(lpTimer); lpTimer=null; }  // it's a scroll
  }, {passive:true});
  const endPress = ()=>{ if (lpTimer){ clearTimeout(lpTimer); lpTimer=null; } };
  document.addEventListener("touchend", e=>{
    endPress();
    // If the long-press fired, swallow the tap that would otherwise open the note.
    if (lpFired){ e.preventDefault?.(); lpFired=false; }
  });
  document.addEventListener("touchcancel", endPress);
})();

// Card-level actions, shared by the hover/long-press buttons and the swipe
// gesture so there is exactly one code path (and one Undo) for each. Both
// return true on success so a caller can put a card back when the save failed.
async function quickArchive(id){
  const r = await api(`/notes/${id}/archive`, {method:"POST"}).catch(()=>null);
  if (!r){ toast("Couldn't update note","err"); return false; }
  const i = S.notes.findIndex(n=>n.id===id);
  if (i>=0) S.notes.splice(i,1);
  renderNotes();
  toast("Archived","ok",{label:"Undo", fn: async ()=>{
    const u = await api(`/notes/${id}/archive`,{method:"POST"}).catch(()=>null);
    if (u){ invalidateViewCache(); await loadNotes(); toast("Restored"); }
    else toast("Couldn't undo","err");
  }});
  return true;
}
async function quickPin(id){
  const r = await api(`/notes/${id}/pin`, {method:"POST"}).catch(()=>null);
  if (!r){ toast("Couldn't update note","err"); return false; }
  const i = S.notes.findIndex(n=>n.id===id);
  if (i>=0) S.notes[i].pinned = r.pinned ?? !S.notes[i].pinned;
  renderNotes();
  toast(r.pinned ? "Pinned" : "Unpinned");
  return true;
}

// Swipe a tile: left = Archive (undoable), right = Pin/Unpin. Deliberately NOT
// Delete — that's a real cascading hard-delete with no undo, far too easy to
// trigger by accident from a drag. Touch/pen only (desktop keeps hover
// actions), and only on the main list, where "archive" is unambiguous — in the
// Archive/Stash views the same endpoint would silently mean "restore".
//
// Vertical scrolling always wins: the card declares touch-action:pan-y, so the
// browser keeps vertical pans for itself (and cancels our pointer) while
// horizontal drags reach us. Movement is direction-locked after ~10px, biased
// toward vertical so a diagonal scroll never becomes a swipe.
(function(){
  let st = null, swallow = false;
  const THR_MIN = 72;
  const thr = w => Math.max(THR_MIN, w * 0.45);
  const calm = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
  const reset = card => {
    card.classList.remove("sw-on","sw-back","sw-L","sw-R");
    card.style.transform = ""; card.style.opacity = "";
    card.style.removeProperty("--swp"); card.style.removeProperty("--swc");
    card.removeAttribute("data-sw");
  };
  const springBack = card => {
    if (calm()){ reset(card); return; }
    card.classList.add("sw-back");
    card.style.transform = ""; card.style.opacity = ""; card.style.setProperty("--swp","0");
    setTimeout(()=>reset(card), 260);
  };

  document.addEventListener("pointerdown", e=>{
    if (e.pointerType === "mouse" || !e.isPrimary || S.listView) return;
    const card = e.target.closest("#feed .card[data-id]");
    if (!card || e.target.closest(".c-act,a,button,input,textarea")) return;
    st = { card, id:card.dataset.id, pid:e.pointerId, x0:e.clientX, y0:e.clientY,
           w:card.offsetWidth || 160, dx:0, lock:null, crossed:false,
           rec:[{x:e.clientX, t:e.timeStamp}] };
  }, {passive:true});

  document.addEventListener("pointermove", e=>{
    if (!st || e.pointerId !== st.pid) return;
    const dx = e.clientX - st.x0, dy = e.clientY - st.y0;
    if (!st.lock){
      if (Math.abs(dx) < 10 && Math.abs(dy) < 10) return;
      st.lock = Math.abs(dx) > Math.abs(dy) * 1.4 ? "x" : "y";
      if (st.lock === "y"){ st = null; return; }          // a scroll — not ours
      document.querySelectorAll(".card.show-acts").forEach(c=>c.classList.remove("show-acts"));
      st.card.classList.add("sw-on");
      try { st.card.setPointerCapture(e.pointerId); } catch(_){}
    }
    st.dx = dx;
    st.rec.push({x:e.clientX, t:e.timeStamp});            // keep only the last ~120ms
    while (st.rec.length > 2 && st.rec[st.rec.length-1].t - st.rec[0].t > 120) st.rec.shift();
    const left = dx < 0, p = Math.min(1, Math.abs(dx) / thr(st.w));
    const n = S.notes.find(x=>x.id===st.id);
    st.card.style.transform = `translateX(${dx}px) rotate(${(dx/st.w*3).toFixed(2)}deg)`;
    st.card.style.setProperty("--swp", p.toFixed(2));
    st.card.style.setProperty("--swc", left ? "var(--teal)" : "var(--violet)");
    st.card.dataset.sw = left ? "🗄  Archive" : (n && n.pinned ? "📌  Unpin" : "📌  Pin");
    st.card.classList.toggle("sw-L", left); st.card.classList.toggle("sw-R", !left);
    const crossed = p >= 1;
    if (crossed && !st.crossed && navigator.vibrate){ try { navigator.vibrate(10); } catch(_){} }
    st.crossed = crossed;
  }, {passive:true});

  const finish = (e, cancelled) => {
    if (!st || e.pointerId !== st.pid) return;
    const s = st; st = null;
    if (s.lock !== "x") return;
    // The click that follows a drag would open the note — swallow exactly one.
    swallow = true; setTimeout(()=>{ swallow = false; }, 350);
    // A flick is judged by velocity at the moment of release (not the average
    // since touch-down — a finger that rested for a second then flicked would
    // read as slow), in the drag's own direction, and not if it was resting.
    let v = 0;
    const r = s.rec, last = r[r.length-1];
    if (r.length >= 2 && e.timeStamp - last.t < 90)
      v = (last.x - r[0].x) / Math.max(1, last.t - r[0].t);          // px/ms, signed
    const flick = Math.abs(s.dx) > 40 && Math.abs(v) > 0.5 && Math.sign(v) === Math.sign(s.dx);
    const commit = !cancelled && (Math.abs(s.dx) >= thr(s.w) || flick);
    if (!commit){ springBack(s.card); return; }
    if (s.dx < 0){
      const done = ok => { if (!ok) springBack(s.card); };
      if (calm()){ quickArchive(s.id).then(done); return; }
      s.card.classList.add("sw-back");
      s.card.style.transform = "translateX(-125%)"; s.card.style.opacity = "0";
      setTimeout(()=>quickArchive(s.id).then(done), 180);
    } else {
      springBack(s.card);
      quickPin(s.id);
    }
  };
  document.addEventListener("pointerup", e=>finish(e,false), {passive:true});
  document.addEventListener("pointercancel", e=>finish(e,true), {passive:true});
  document.addEventListener("click", e=>{
    if (!swallow) return;
    swallow = false; e.stopPropagation(); e.preventDefault();
  }, true);
})();
(async ()=>{
  try { await idb.open(); } catch(e){}
  const cached = await localNotes();
  if (cached.length){ S.notes = cached; renderNotes(); updateConnBadge(); }
  // On a remote origin nothing is cached to disk, so the offline bar must not
  // promise on-device durability.
  if (!PERSIST_OK){
    const _ob = $("offBar");
    if (_ob) _ob.textContent = "⚡ Offline — changes are held in this tab only; keep it open";
  }
  await refreshAll();
  connectSSE();
  initUiKit();
  // On desktop the menu is a persistent rail, so its contents must be loaded at
  // startup rather than on drawer-open (which never happens there).
  if (_isWide()) loadMenuTags();
  _RAIL_MQ.addEventListener("change", e=>{
    if (e.matches){ loadMenuTags(); $("menuLayer").classList.remove("on"); }
  });
  setInterval(loadStats, 30000);
  setInterval(()=>{ if (!NET.online) flushOutbox(); }, 12000);
})();
