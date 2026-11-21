/* ═════════ SSE (never touches the editor layer) ═════════ */
// Debounce refreshes: the agent can fire several events in quick succession
// (a burst of enrichments). Coalesce them into one refresh so the feed doesn't
// rebuild repeatedly — smoother, and avoids any flicker on rapid updates.
let _refreshT = null;
function refreshSoon(){
  clearTimeout(_refreshT);
  // A server-side change (create/update/delete/enrich from the agent) means our
  // cached view lists may be stale — drop them so the refresh shows fresh data.
  invalidateViewCache();
  _refreshT = setTimeout(()=>{ refreshAll(); }, 250);
}
/* ═════════ Notifications + app badge ═════════
   Both are strictly OPT-IN and best-effort: every call is wrapped so an
   unsupported browser, a denied permission, or an insecure origin degrades to
   nothing rather than throwing. The in-app toast remains the primary feedback —
   these only add value when the app ISN'T in front of you. */

// Show a system notification via the service worker (a page-level `new
// Notification()` dies when the app is backgrounded, which is precisely when we
// need it). No-ops unless the user has granted permission.
async function notify(title, body, {tag="hg", url="/"}={}){
  try {
    if (!("Notification" in window) || Notification.permission !== "granted") return;
    if (_prefs?.notify_muted) return;   // user turned notifications off in-app
    const reg = await navigator.serviceWorker?.ready;
    if (!reg) return;
    reg.active?.postMessage({type:"notify", title, body, tag, url});
  } catch(_){}
}

// Ask for permission — only ever from a user gesture (browsers reject or
// penalise unprompted requests, and an unexpected permission prompt is rude).
async function requestNotifyPermission(){
  try {
    if (!("Notification" in window)) return "unsupported";
    if (Notification.permission !== "default") return Notification.permission;
    return await Notification.requestPermission();
  } catch(_){ return "denied"; }
}

// (c) App badge — the count on the launcher/taskbar icon. Shows how many notes
// are waiting for review, so the icon itself tells you there's something to do.
function setAppBadge(n){
  try {
    if (!("setAppBadge" in navigator)) return;      // unsupported → silently skip
    if (n > 0) navigator.setAppBadge(n).catch(()=>{});
    else navigator.clearAppBadge?.().catch(()=>{});
  } catch(_){}
}

// Settings UI for notifications. Reports the REAL permission state — including
// "denied", which the app cannot reverse (only the user can, in browser
// settings). Pretending a toggle works when the browser has blocked us would be
// a lie the user only discovers by never getting a notification.
function _notifyDesc(){
  if (!("Notification" in window))
    return "This browser doesn't support notifications.";
  if (Notification.permission === "granted" && _prefs?.notify_muted)
    return "Off — turned off in-app. The browser permission stays granted, so switching this back on takes effect immediately.";
  switch (Notification.permission){
    case "granted": return "On — you'll be told when the agent finishes enriching a note while the app is in the background.";
    case "denied":  return "Blocked. Your browser is refusing notifications for this site — you'll need to re-allow them in the browser's site settings; the app can't do it for you.";
    default:        return "Get told when the agent finishes enriching a note while the app is in the background. Nothing is sent anywhere — the notification is raised on-device.";
  }
}
function _notifyControl(){
  if (!("Notification" in window)) return "";
  const p = Notification.permission;
  if (p === "granted"){
    const muted = !!_prefs?.notify_muted;
    return `<div class="set-seg">
        <button class="set-seg-b${muted?"":" on"}" data-setk="notify_muted" data-setv="0">On</button>
        <button class="set-seg-b${muted?" on":""}" data-setk="notify_muted" data-setv="1">Off</button>
        ${muted?"":`<button class="set-seg-b" id="notifyTest">Send a test</button>`}
      </div>`;
  }
  if (p === "denied")
    return `<div class="set-seg"><button class="set-seg-b" disabled>Blocked by browser</button></div>`;
  return `<div class="set-seg"><button class="set-seg-b" id="notifyOn">Turn on</button></div>`;
}

function connectSSE(){
  const es = new EventSource(API_BASE+"/api/events");
  es.addEventListener("note_created", refreshSoon);
  es.addEventListener("note_updated", refreshSoon);
  es.addEventListener("note_deleted", refreshSoon);
  es.addEventListener("bills_changed", ()=>{ pulseSoon(); loadHome(true); billsStatusSoon(); });
  es.addEventListener("followups_changed", ()=>{ pulseSoon(); loadStats(); });
  es.addEventListener("action_updated", ()=>{ loadActions(); loadStats(); pulseSoon(); });
  ["note_created","note_updated","note_deleted","jobs_changed","relationships_updated"].forEach(ev=>es.addEventListener(ev, pulseSoon));
  es.addEventListener("relationships_updated", ()=>loadNotes().then(renderConnections));
  ["attachment_added","attachment_ready"].forEach(name=>es.addEventListener(name, ev=>{
    let d = {}; try{ d = JSON.parse(ev.data); }catch(_){}
    refreshSoon(); pulseSoon();
    const nid = d.note_id || d.id;
    if (S.cur && nid===S.cur.id) refreshDetailQuiet();
  }));
  es.addEventListener("ask_answered", ev=>{
    const d = JSON.parse(ev.data);
    if (ST.id && ST.id === d.request_id){ storyApply(d); return; }
    if (!S.askActive || S.askActive.id !== d.request_id) return; // superseded/dismissed
    applyAskResult(S.askActive, d);
  });
  es.addEventListener("note_enriched", async ev=>{
    const d = JSON.parse(ev.data);
    pulseSoon();
    toast("✨ "+(d.enrichment?.title||"Note enriched"),"ok");
    // Only notify when the app ISN'T in front of you — if you're looking at the
    // screen the toast already told you, and a tray notification would be noise.
    if (document.visibilityState !== "visible"){
      notify("✨ Note enriched", d.enrichment?.title || "Your note is ready to review",
             { tag:"hg-enriched", url:"/" });
    }
    await refreshAll();
    if (S.cur?.id===d.id && !Ed.open){ S.cur = d; renderDetail(d); }
  });
  es.onerror = ()=>{ es.close(); setTimeout(connectSSE, 4000); };
}
