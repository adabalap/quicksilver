/* ═════════ IndexedDB mirror + offline outbox ═════════
   PRIVACY: the mirror caches note bodies in the browser's IndexedDB so the app
   works offline. That's right on a trusted device (localhost / the installed
   PWA), but when the app is reached over the network — e.g. from a laptop or a
   shared machine at hg.adabala.com — leaving note text at rest in that browser
   is a data-exposure risk the user didn't ask for.

   So: on a REMOTE origin we never open the database. Nothing is persisted to
   disk; notes live only in memory for the session and vanish when the tab
   closes. Every idb.* method already guards on `db` being null, so the whole
   mirror degrades to a clean no-op.

   Trade-off, stated plainly: no local cache means no offline support on the
   remote origin. Crucially the OUTBOX also can't persist — so instead of
   silently dropping an offline write (which would LOSE the note while the UI
   claimed "saved offline"), we keep it in memory for the session and tell the
   user the truth. See `_memOutbox` below. */
const IS_LOCAL = ["localhost","127.0.0.1","::1"].includes(location.hostname)
              || location.hostname.endsWith(".localhost");
const PERSIST_OK = IS_LOCAL;

// Offline-save wording must match reality: on a trusted origin the outbox is on
// disk and survives a restart; on a remote origin it only lives for the session.
// Promising "will sync" when a tab-close would discard the note is a lie.
const OFFLINE_SAVE_MSG = PERSIST_OK
  ? "Saved offline ⚡ will sync"
  : "Saved in this tab ⚡ keep it open until you're back online";
const OFFLINE_SAVE_SHORT = PERSIST_OK ? "Saved offline ⚡" : "Saved in this tab ⚡";

// In-memory stand-in for the outbox on remote origins: survives the session so
// a brief connection blip still recovers, but never touches disk.
let _memOutbox = [];

const idb = {
  db:null,
  open(){
    // Remote origin → never persist. Also purge anything an EARLIER visit left
    // behind (before this gating existed), so switching to a remote host
    // actively cleans up rather than just stopping new writes.
    if (!PERSIST_OK){
      try { indexedDB.deleteDatabase("hg"); } catch(_){}
      return Promise.resolve();
    }
    return new Promise((res,rej)=>{ const r=indexedDB.open("hg",1);
    r.onupgradeneeded=()=>{ const d=r.result;
      d.createObjectStore("notes",{keyPath:"id"});
      d.createObjectStore("outbox",{keyPath:"ts"}); };
    r.onsuccess=()=>{ idb.db=r.result; res(); };
    r.onerror=()=>rej(r.error); }); },
  st(n,m="readonly"){ return idb.db.transaction(n,m).objectStore(n); },
  mirror(list){ if(!idb.db) return;      // remote: nothing cached to disk
    const s=idb.st("notes","readwrite"); s.clear();
    list.forEach(n=>s.put(n)); },
  all(store){ return new Promise(res=>{
    if(!idb.db){
      // Remote: the notes mirror is genuinely empty (by design), but the outbox
      // must still return queued ops or offline writes would be lost.
      return res(store==="outbox" ? _memOutbox.slice() : []);
    }
    const out=[]; const c=idb.st(store).openCursor();
    c.onsuccess=e=>{ const cu=e.target.result;
      if(cu){ out.push(cu.value); cu.continue(); } else res(out); };
    c.onerror=()=>res([]); }); },
  queue(op){
    const rec = {ts:Date.now()+Math.random(),...op};
    if(idb.db) idb.st("outbox","readwrite").put(rec);
    else _memOutbox.push(rec);           // remote: session-only, never on disk
  },
  rmOp(ts){
    if(idb.db) idb.st("outbox","readwrite").delete(ts);
    else _memOutbox = _memOutbox.filter(o=>o.ts!==ts);
  },
};

/* ═════════ Network state ═════════ */
const NET = { online:true };
function setOnline(on){
  if (NET.online === on) return;
  NET.online = on;
  $("offBar").classList.toggle("on", !on);
  document.body.classList.toggle("offline", !on);
  if (on){ toast("Back online — syncing ⚡","ok"); flushOutbox(); }
}
async function api(p,o={}){
  let r;
  try { r = await fetch(API_BASE+"/api"+p,{ method:o.method||"GET",
        headers:{"Content-Type":"application/json"},
        body:o.body?JSON.stringify(o.body):undefined }); }
  catch(e){ setOnline(false); throw {offline:true}; }
  setOnline(true);
  if (!r.ok){ let data=null; try { data=await r.json(); } catch(e){} throw {status:r.status,data}; }
  return r.json();
}
/* Why an api() call failed, in words the person can act on.

   "Couldn't dismiss" was the whole message the first time this fired, and it
   sent the wrong person looking: the request was fine, the route simply did
   not exist on the running server. That is not an edge case here. The PWA's
   JS is served off disk, so unzipping a new build updates the UI the moment
   the page reloads, while Flask keeps running the Python it imported at
   startup. Until the service is restarted the app is strictly newer than its
   server, and every route added in that build answers 404 or 405.

   So name that case. Anything else falls back to the caller's wording plus
   the status, which is still more than the bare sentence was.

   A 404 is NOT always a missing route, and the first version said it was. A
   route can answer 404 itself -- "that save is no longer here" -- and that
   person would have been told to restart a server that was fine. The two are
   told apart by the body: a route the server lacks gets Flask's HTML error
   page, so api() parses no JSON; an answer from one of our routes is JSON
   with an `error`. And when our route wrote a `message`, it was written for
   a person, so it is shown as is. */
function apiWhy(e, fallback){
  if (e && e.offline) return "You're offline — try again when connected";
  const s = e && e.status, d = e && e.data;
  if (d && typeof d.message === "string" && d.message) return d.message;
  if ((s === 404 || s === 405) && !(d && d.error)) return "Restart hg_ui — the server is older than the app";
  if (s === 409) return "That's already been handled";
  return s ? `${fallback} (${s})` : fallback;
}
async function flushOutbox(){
  const ops = (await idb.all("outbox")).sort((a,b)=>a.ts-b.ts)
    .sort((a,b)=>(a.kind==="attach")-(b.kind==="attach"));   // uploads go after their note exists
  for (const op of ops){
    try {
      if (op.kind==="create") await api("/notes",{method:"POST",body:{id:op.id,body:op.body,...(op.reminder?{reminder:op.reminder}:{}),...(op.media?{media:op.media}:{})}});
      else if (op.kind==="attach"){ const fd=new FormData(); fd.append("kind",op.akind); fd.append("meta",JSON.stringify(op.meta||{})); fd.append("file",op.blob,"upload");
        await apiForm("/notes/"+op.nid+"/attachments", fd); }
      else if (op.kind==="patch") await api("/notes/"+op.id,{method:"PATCH",body:op.payload});
      else if (op.kind==="del") await api("/notes/"+op.id,{method:"DELETE"});
      else if (op.kind==="action") await api("/actions/"+op.aid,{method:"PATCH",body:{done:op.done}});
      idb.rmOp(op.ts);
    } catch(e){
      if (e.offline) return;          // still down — retry later
      idb.rmOp(op.ts);                // 4xx: op no longer applies, drop it
    }
  }
  await refreshAll();
}

/* ═════════ State + data (offline-first) ═════════ */
const S = { notes:[], actions:[], stats:{}, filter:"all", listView:"", q:"", cur:null,
  // Which connection clusters (by index) the user has expanded past the fold on
  // the Connections page. Starts empty — every cluster loads collapsed, same as
  // Related notes on the detail view — and stays in memory only for this session.
  connExpanded:new Set(),
  // Connections page: "list" (default) or "graph"; graphSel = the tapped node id.
  connView:"list", graphSel:null, graphFocus:null, graphMax:60, gz:{k:1,x:0,y:0},
  // Ask your notes: askMode toggles what Enter in the search bar does; askActive
  // holds the in-flight/answered request ({id, question, status, answer,
  // citedIds, error}) so renderAskPanel() has something to paint. Session-only,
  // same as connExpanded — nothing here persists or needs to.
  askMode:false, askKind:"notes", askActive:null, askThread:[], fil:{type:"",range:"",tag:""},
  home:null, homeSince:"", homeAt:0, wnDismissed:false,
  // Per-view cache of the last notes we showed for each list view ("all",
  // "archived", "hidden"). Lets a view switch paint INSTANTLY from cache while a
  // fresh fetch happens in the background — the switch no longer blocks on the
  // network. Keyed by listView; "" = the main all-notes view. In-memory only:
  // this never touches localStorage/IndexedDB, so nothing is left on the client.
  viewCache:{},
  // Pagination: how many notes are loaded, the server's true total, and whether
  // a page fetch is in flight (so scroll events don't stack requests). The list
  // fetches one page at a time and appends as the user scrolls — so a collection
  // of any size is reachable, not just the first 200.
  total:0, loadedCount:0, loadingMore:false };
const PAGE = 200;   // notes per page (server caps at 200)

async function localNotes(){
  const base = new Map((await idb.all("notes")).map(n=>[n.id,n]));
  const ops = (await idb.all("outbox")).sort((a,b)=>a.ts-b.ts);
  for (const op of ops){
    if (op.kind==="create") base.set(op.id, { id:op.id, body:op.body, status:"raw",
      tags:[...new Set((op.body.match(/#(\w+)/g)||[]).map(t=>t.slice(1)))],
      action_items:[], related:[], enrichment:null, protected:0, _pending:1,
      created_at:op.iso, updated_at:op.iso });
    else if (op.kind==="patch" && base.has(op.id)){
      const n = base.get(op.id);
      Object.assign(n, op.payload, {_pending:1, updated_at:op.iso});
    }
    else if (op.kind==="del") base.delete(op.id);
    else if (op.kind==="attach" && base.has(op.nid)){
      const n = base.get(op.nid);
      n.attachments = [...(n.attachments||[]), {id:op.aid, kind:op.akind, mime:op.blob?.type, size:op.blob?.size||0,
        duration_s:op.meta?.duration_s, transcript:op.meta?.transcript||"", transcript_status:op.meta?.transcript?"done":"pending", _local:op.blob}];
    }
    else if (op.kind==="action"){
      for (const n of base.values())
        (n.action_items||[]).forEach(a=>{ if(a.id===op.aid) a.done = op.done?1:0; });
    }
  }
  return [...base.values()].sort((a,b)=>(b.updated_at||"").localeCompare(a.updated_at||""));
}
async function loadNotes(){
  // Stale-while-revalidate: if we already have notes cached for THIS view, paint
  // them immediately so the switch feels instant, then fetch fresh below and
  // re-render only if the data actually changed. Search (S.q) always fetches —
  // results are query-specific and shouldn't be served stale.
  const viewKey = (S.q || filOn()) ? null : S.listView;   // null = don't use the view cache
  if (viewKey !== null && S.viewCache[viewKey]){
    S.notes = S.viewCache[viewKey];
    renderNotes();                            // instant paint from cache
  }
  try {
    let server;
    if (S.q){
      const res = await api("/search?q="+encodeURIComponent(S.q)+(filOn()?"&"+filQS():""));
      server = res.notes;
      S.searchMode = res.mode;
      S.total = server.length;              // search returns its full result set
    } else if (S.listView==="archived" || S.listView==="hidden"){
      // Archived/Hidden are server-side views (not client-filterable, since the
      // main mirror deliberately excludes them). Fetch them explicitly.
      const res = await api("/notes?limit="+PAGE+"&filter="+S.listView+(filOn()?"&"+filQS():""));
      server = res.notes; S.total = res.total ?? server.length;
      S.searchMode = null;
    } else {
      const res = await api("/notes?limit="+PAGE+(filOn()?"&"+filQS():""));
      server = res.notes; S.total = res.total ?? server.length;
      S.searchMode = null;
    }
    S.loadedCount = server.length;          // page 1 loaded
    if (!S.q && !S.listView && !filOn()) idb.mirror(server);
    const pend = (await idb.all("outbox")).some(o=>o.kind==="create") && !S.q && !S.listView && !filOn()
      ? await localNotes() : null;
    const fresh = pend || server;
    // Cache the fresh result for this view for next time.
    if (viewKey !== null) S.viewCache[viewKey] = fresh;
    // Skip the re-render if we already painted identical data from cache
    // (avoids rebuilding the whole list DOM for no reason).
    const unchanged = !!S._painted && viewKey !== null && S.notes.length === fresh.length
      && _sameNoteList(S.notes, fresh);
    S.notes = fresh;
    if (!unchanged) renderNotes();
  } catch(e){
    S.notes = await localNotes();
    if (S.q){ const q=S.q.toLowerCase();
      S.notes = S.notes.filter(n=>noteTitle(n).toLowerCase().includes(q)
        || (n.body||"").toLowerCase().includes(q)
        || (n.tags||[]).some(t=>t.includes(q))); }
    renderNotes();
  }
  updateConnBadge();
}
// Drop all cached view lists — call after any mutation (archive, hide, delete,
// edit, create) so the next loadNotes() shows fresh data instead of stale cache.
function invalidateViewCache(){ S.viewCache = {}; }
