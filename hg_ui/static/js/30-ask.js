/* ═════════ Ask your notes ═════════
   Enter in the search bar while Ask mode is on posts the question, shows a
   thinking state immediately, then waits for the daemon's answer to arrive
   over the SSE channel (connectSSE's "ask_answered" listener below) — the
   same live-push path enrichment already uses, not a poll from the client. */
/* The answer normally arrives over SSE, but a push can be lost (tunnel buffering,
   a dropped connection, the app backgrounded while the keyboard was up) and the
   agent has ALREADY saved the answer by then. So a pending turn also polls the
   saved request — the SSE push is the fast path, this is the guarantee. */
function applyAskResult(turn, d){
  if (!turn || turn.status !== "pending") return;       // already settled (SSE and poll can both land)
  if (d.status !== "done" && d.status !== "error") return;
  turn.status = d.status; turn.answer = d.answer;
  turn.citedIds = d.cited_note_ids || []; turn.refs = d.refs || null; turn.error = d.error;
  if (d.retrieved && d.retrieved.length) turn.retrieved = d.retrieved;
  renderAskPanel();
}
async function pollAsk(turn){
  if (!turn || !turn.id || turn.status !== "pending") return;
  try { applyAskResult(turn, await api("/ask/"+turn.id)); } catch(e){ /* transient; next tick retries */ }
}
function watchAsk(turn){
  const t0 = Date.now();
  const tick = async ()=>{
    if (S.askActive !== turn || turn.status !== "pending") return;      // answered, superseded or dismissed
    if (!document.hidden) await pollAsk(turn);
    if (turn.status !== "pending") return;
    if (Date.now()-t0 > (turn.kind==="write" ? 1200000 : 180000)){
      turn.status = "error";
      turn.error = "This is taking longer than expected. The agent may be busy or offline — your question is saved; try again in a moment.";
      renderAskPanel(); return; }
    setTimeout(tick, 3000);
  };
  setTimeout(tick, 4000);
}
document.addEventListener("visibilitychange", ()=>{
  if (!document.hidden && S.askActive && S.askActive.status === "pending") pollAsk(S.askActive);
});
async function askQuestion(question, follow){
  question = (question||"").trim();
  if (!question) return;
  $("feed").style.display = "none";
  $("homeStrips").style.display = "none";
  $("askPanel").classList.add("on");
  if (!follow) S.askThread = [];
  // The conversation so far (last 2 answered turns) rides along so the server
  // can resolve "and the other one?" and keep the previous answer's notes in play.
  const hist = (follow && kind!=="write") ? S.askThread.filter(t=>t.status==="done" && t.answer).slice(-2)
      .map(t=>({question:t.question, answer:t.answer, cited_note_ids:t.citedIds||[]})) : [];
  const kind = S.askKind;
  const turn = { id:null, question, kind, status:"pending", retrieved:[], moreOpen:false, otherOpen:false,
                 refs:null, citedIds:[] };
  S.askThread.push(turn);
  if (S.askThread.length > 4) S.askThread.shift();
  S.askActive = turn;
  renderAskPanel();
  if (follow) document.getElementById("askTurn"+(S.askThread.length-1))
      ?.scrollIntoView({block:"start",behavior:"smooth"});
  try {
    const res = await api("/ask", { method:"POST", body:{ question, history:hist, mode:kind } });
    if (S.askActive !== turn) return; // superseded / dismissed
    turn.id = res.request_id;
    turn.retrieved = res.retrieved || [];
    turn.window = res.window || null;
    if (res.status === "done"){
      turn.status = "done"; turn.answer = res.answer;
      turn.citedIds = res.cited_note_ids || [];
    }
    renderAskPanel();
    if (turn.status === "pending") watchAsk(turn);
  } catch(e){
    if (S.askActive !== turn) return;
    turn.status = "error";
    turn.error = e?.offline
      ? "You're offline — Ask needs the on-device agent running."
      : "Couldn't reach the agent. Is it running?";
    renderAskPanel();
  }
}
/* Search / Ask mode plumbing (one place, so header, switch, clear and send stay in sync). */
function srchGrow(){ const t=$("srchIn"), cap = matchMedia("(min-width:900px)").matches ? 42 : 104;   // desktop: never taller than the 62px header
  t.style.height="auto"; t.style.height=Math.min(t.scrollHeight, cap)+"px"; }
function srchSync(){
  const open = $("srchWrap").classList.contains("on");
  document.querySelector(".hdr").classList.toggle("searching", open);
  $("srchModes").classList.toggle("on", open);
  const has = !!$("srchIn").value;
  $("srchClr").hidden = !has;
  $("askGo").hidden = !(S.askMode && has);
  $("srchWrap").classList.toggle("ask", !!S.askMode);
  $("srchModes").querySelectorAll("button").forEach(b=>b.classList.toggle("on", (b.dataset.mode==="ask")===!!S.askMode));
  $("srchIn").enterKeyHint = S.askMode ? "send" : "search";
  srchGrow();
}
function setSearchOpen(open){
  $("srchWrap").classList.toggle("on", open);
  if (open) $("srchIn").focus();
  else { S.q=""; $("srchIn").value=""; exitAskMode(); loadNotes(); $("srchIn").blur(); }
  srchSync();
}
function setAskMode(on){
  S.askMode = !!on;
  $("askToggle").classList.toggle("on", S.askMode);
  if (S.askMode){
    $("srchIn").placeholder = ASK_KIND_INFO[S.askKind].ph;
    $("srchWrap").classList.add("on");
    showAskHint();
    $("srchIn").focus();
  } else exitAskMode();
  srchSync();
}
const ASK_EXAMPLES = {
  notes: ["When is my next appointment?", "What did I note about travel plans?", "Summarise my notes from last week"],
  chat:  ["Explain how DNF differs from RPM", "Give me 5 ideas for a weekend project", "Help me word a polite reminder email"],
  write: ["Write a blog post from my notes on Fedora", "Build a white paper from my notes about cloud costs", "Draft a one-page summary of my travel notes"]
};
const ASK_KIND_INFO = {
  notes: {ph:"Ask anything about your notes…", h:"Ask your notes", p:"Answers come from your own notes, on this phone, with the sources shown."},
  chat:  {ph:"Ask the model anything…", h:"Chat with the model", p:"A plain prompt to the on-device model. Your notes are not used."},
  write: {ph:"What should I write from your notes?", h:"Write from your notes", p:"Describe a blog post, white paper, guide or summary. It is written only from your matching notes, section by section, so it can take a few minutes."}
};
function askKindsHtml(){
  return `<div class="ask-kinds" role="tablist">${[["notes","Notes"],["chat","Chat"],["write","Write"]].map(([k,l])=>
    `<button type="button" data-kind="${k}" class="${S.askKind===k?"on":""}">${l}</button>`).join("")}</div>`;
}
function setAskKind(k){
  if (!ASK_KIND_INFO[k] || S.askKind===k) return;
  S.askKind = k; S.askActive = null; S.askThread = [];
  $("srchIn").placeholder = ASK_KIND_INFO[k].ph;
  showAskHint();
}
function showAskHint(){
  if (S.askThread && S.askThread.length) return;
  const p = $("askPanel");
  p.classList.add("on");
  $("feed").style.display = "none"; $("homeStrips").style.display = "none";
  const ki = ASK_KIND_INFO[S.askKind];
  p.innerHTML = `${askKindsHtml()}<div class="ask-empty"><h4>${ki.h}</h4>
    <p>${ki.p}</p>
    <div class="eg">${ASK_EXAMPLES[S.askKind].map(q=>`<button type="button" data-eg="${esc(q)}">${esc(q)}</button>`).join("")}</div></div>`;
}
function exitAskMode(){
  S.askMode = false; S.askActive = null; S.askThread = [];
  $("homeStrips").style.display = "";
  $("askToggle").classList.remove("on");
  $("askPanel").classList.remove("on"); $("askPanel").innerHTML = "";
  $("feed").style.display = "";
  $("srchIn").placeholder = "Search…";
  srchSync();
}
/* Reference cards for an Ask answer. The server sends `refs` (snippet, matched
   terms, support level); if it didn't (older payload / offline refetch) build a
   plain equivalent from what we already hold so cited notes always show. */
function askRefs(a){
  if (Array.isArray(a.refs) && a.refs.length) return a.refs;
  const cited = a.citedIds || [];
  return (a.retrieved||[]).map((ex,i)=>({n:i+1, id:ex.id, title:ex.title||"Untitled",
      date:ex.date, tags:ex.tags||[], relevance:ex.score,
      snippet:(ex.text||"").slice(0,200), terms:[], level:"partial"}))
    .filter(r=>cited.includes(r.id));
}
function askHl(text, terms){
  const safe = esc(text||"");
  const ts = (terms||[]).filter(t=>t && t.length>=3 && !/^(amp|lt|gt|quot|apos|39)$/i.test(t))
      .sort((x,y)=>y.length-x.length).map(t=>esc(t).replace(/[.*+?^${}()|[\]\\]/g,"\\$&"));
  if (!ts.length) return safe;
  return safe.replace(new RegExp(ts.join("|"),"gi"), m=>`<mark>${m}</mark>`);
}
function askDate(d){
  try { const t = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(d||"") ? d : (d||"").replace(" ","T")+"Z");
    if (isNaN(t)) return "";
    // Older than ~10 months: the year is the point ("24 Oct 2025"), not noise.
    const opt = Date.now()-t > 300*864e5 ? {day:"numeric",month:"short",year:"numeric"} : {day:"numeric",month:"short"};
    return t.toLocaleDateString("en-IN",opt); } catch(e){ return ""; }
}
function askRefCard(r, t){
  const pct = typeof r.relevance==="number" ? Math.round(Math.min(1,Math.max(0,r.relevance))*100) : null;
  return `<div class="ask-ref-card" id="askRef${t}-${r.n}" data-id="${esc(r.id)}">
    <div class="rc-top"><span class="rc-n">${r.n}</span><span class="rc-t">${esc(r.title||"Untitled")}</span>
      <span class="rc-d">${esc(askDate(r.date))}</span></div>
    <div class="rc-sn">${askHl(r.snippet, r.terms)}</div>
    <div class="rc-meta">${pct!==null?`<span class="rc-bar" title="Retrieval match"><i style="width:${pct}%"></i></span><span>${pct}% match</span>`:""}
      ${r.level==="weak"?`<span class="rc-weak" title="Few of the answer's key words appear in this note">weak support</span>`:""}
      ${(r.tags||[]).slice(0,2).map(x=>`<span class="rc-tag">#${esc(x)}</span>`).join("")}</div>
  </div>`;
}
/* One question + its answer card. `t` is the turn's position in the thread, so
   element ids (and the marker → card jump) stay unique across turns. */
function askTurnHtml(a, t){
  let body;
  if (a.status === "pending"){
    const checklist = (a.retrieved||[]).map((ex,i)=>
      `<li style="animation-delay:${i*0.15}s"><span class="tick">✓</span>${esc(ex.title||"Untitled")}</li>`
    ).join("");
    body = (checklist ? `<ul class="ask-check">${checklist}</ul>` : "")
      + `<div class="ask-think"><span class="ask-dot"></span><span class="ask-dot"></span><span class="ask-dot"></span>
         ${a.kind==="write" ? "Writing from your notes — this can take a few minutes…"
           : a.kind==="chat" ? "Thinking…" : (checklist ? "Thinking…" : "Reading your notes…")}</div>`;
  } else if (a.status === "error"){
    body = `<div class="ask-a err">${esc(a.error||"Something went wrong.")}</div>`;
  } else {
    const refs = askRefs(a);
    const byN = {}; refs.forEach(r=>{ byN[r.n] = r; });
    const rich = a.kind==="write" || a.kind==="chat";
    const ansHtml = (rich ? mdLite(a.answer||"") : esc(a.answer||"")).replace(/(\s*)\[(\d+)\]/g, (m,sp,n)=>
      byN[n] ? `<sup><button class="ask-ref" data-t="${t}" data-n="${n}" aria-label="Source ${n}">${n}</button></sup>` : sp);
    const shown = a.moreOpen ? refs : refs.slice(0,3);
    const cards = shown.map(r=>askRefCard(r,t)).join("");
    const more = (refs.length>3 && !a.moreOpen)
      ? `<button class="ask-more" data-t="${t}" data-act="more-refs">+${refs.length-3} more source${refs.length-3>1?"s":""}</button>` : "";
    const citedSet = new Set(refs.map(r=>r.id));
    const others = (a.retrieved||[]).filter(ex=>!citedSet.has(ex.id));
    const otherHtml = others.length ? (a.otherOpen
      ? `<button class="ask-more" data-t="${t}" data-act="hide-others">Hide ▲</button>
         <div class="ask-others">${others.map(ex=>
           `<div class="ask-other" data-id="${esc(ex.id)}"><span class="o-t">${esc(ex.title||"Untitled")}</span>
            ${typeof ex.score==="number"?`<span class="o-m">${Math.round(ex.score*100)}%</span>`:""}</div>`).join("")}</div>`
      : `<button class="ask-more" data-t="${t}" data-act="show-others">Also looked at (${others.length}) ▾</button>`) : "";
    const win = a.window && a.window.total > a.window.shown
      ? `<div class="ask-scope">Based on the ${a.window.shown} most recent of ${a.window.total} notes from that period.</div>` : "";
    body = `<div class="ask-a${rich?" md":""}">${ansHtml}</div>${win}`
         + (rich ? `<div class="ask-acts"><button type="button" data-ask-save="${t}">Save as note</button>
              <button type="button" data-ask-copy="${t}">Copy</button>
              <button type="button" data-ask-share="${t}">Share</button></div>` : "")
         + (cards ? `<div class="ask-refs-h">Sources</div><div class="ask-refs">${cards}</div>${more}` : "")
         + otherHtml;
  }
  return `<div class="ask-turn" id="askTurn${t}"><div class="ask-q">“${esc(a.question)}”</div><div class="ask-card">${body}</div></div>`;
}
function renderAskPanel(){
  const panel = $("askPanel");
  const thread = S.askThread || [];
  if (!S.askActive || !thread.length){ panel.innerHTML = ""; panel.classList.remove("on","docked"); document.body.classList.remove("ask-docked"); return; }
  const keep = $("askFollowIn") ? $("askFollowIn").value : "";       // typed-but-unsent follow-up survives re-renders
  const hadFocus = document.activeElement && document.activeElement.id === "askFollowIn";
  const last = S.askActive;
  const canFollow = last.status === "done" && last.answer && last.kind!=="write";
  const sugg = ["Tell me more", "Summarise that", "Any dates or deadlines?", "What else is related?"];
  const follow = canFollow
    ? `<div class="ask-dock" id="askDock">
         <div class="ask-sugg">${sugg.map(q=>`<button type="button" data-sugg="${esc(q)}">${esc(q)}</button>`).join("")}</div>
         <form class="ask-follow" id="askFollow" autocomplete="off">
           <span class="af-ic" aria-hidden="true">✦</span>
           <input id="askFollowIn" type="text" placeholder="Ask a follow-up…" enterkeyhint="send" maxlength="500">
           <button type="submit" aria-label="Send follow-up">➤</button></form></div>` : "";
  panel.innerHTML = askKindsHtml() + thread.map((a,t)=>askTurnHtml(a,t)).join("") + follow;
  panel.classList.toggle("docked", !!canFollow);
  document.body.classList.toggle("ask-docked", !!canFollow);
  if (canFollow){ const d = $("askDock"); if (d) panel.style.setProperty("--askdock", d.offsetHeight+"px"); }
  if ($("askFollowIn")){ $("askFollowIn").value = keep; if (hadFocus) $("askFollowIn").focus(); }
}

// Fetch the next page and APPEND it — this is what lets the list grow past one
// page. Triggered by scrolling near the bottom. Skipped for search (its results
// are already the full set) and when a page is already in flight or everything
// is loaded. Appends to S.notes and to the DOM without rebuilding the list.
async function loadMore(){
  if (S.loadingMore) return;
  if (S.q) return;                               // search isn't paginated
  if (S.listView && S.listView!=="archived" && S.listView!=="hidden") return;
  if (S.loadedCount >= S.total) return;          // nothing more to load
  S.loadingMore = true;
  const box = $("masonryBox");
  if (box) box.insertAdjacentHTML("afterend",
    `<div id="moreSpin" class="more-spin"><div class="spinner"></div></div>`);
  try {
    const filt = (S.listView ? "&filter="+S.listView : "") + (filOn() ? "&"+filQS() : "");
    const res = await api(`/notes?limit=${PAGE}&offset=${S.loadedCount}${filt}`);
    const more = res.notes || [];
    S.total = res.total ?? S.total;
    // Guard against duplicates if the list shifted between pages.
    const have = new Set(S.notes.map(n=>n.id));
    const fresh = more.filter(n=>!have.has(n.id));
    S.notes = S.notes.concat(fresh);
    S.loadedCount += more.length;
    if (!S.q && S.listView==="" && !filOn()) S.viewCache[""] = S.notes;   // keep cache in sync
    // Append the new cards to the existing masonry, no full re-render.
    const box2 = $("masonryBox");
    if (box2 && fresh.length)
      box2.insertAdjacentHTML("beforeend",
        fresh.map((n,i)=>_cardHtml(n, S.loadedCount+i, false)).join(""));
  } catch(e){ /* leave it; next scroll retries */ }
  finally {
    $("moreSpin")?.remove();
    S.loadingMore = false;
  }
}
// Cheap equality check: same ids in the same order, same updated_at + status
// (the fields that would change what's rendered). Avoids a full re-render when
// a background fetch returns exactly what we already show.
function _sameNoteList(a, b){
  if (a.length !== b.length) return false;
  for (let i=0;i<a.length;i++){
    if (a[i].id !== b[i].id) return false;
    if (a[i].updated_at !== b[i].updated_at) return false;
    if (a[i].status !== b[i].status) return false;
    if (a[i].pinned !== b[i].pinned) return false;
  }
  return true;
}
async function loadActions(){
  const q = S.actionFilter ? ("?" + S.actionFilter) : "";
  try { S.actions = (await api("/actions" + q)).actions; }
  catch(e){ // offline: derive from mirrored notes, applying the same filter locally
    S.actions = [];
    const wk = Date.now() - 7*24*3600*1000;
    (await localNotes()).forEach(n=>(n.action_items||[]).forEach(a=>{
      const created = new Date(n.created_at||0).getTime();
      if (S.actionFilter==="done=1" && !a.done) return;
      if (S.actionFilter==="done=0" && a.done) return;
      if (S.actionFilter==="aging=1" && (a.done || created >= wk)) return;
      S.actions.push({...a, note_id:n.id, note_title:noteTitle(n),
        tags:(n.tags||[]).join(",")});
    }));
  }
  renderActions();
}
async function loadStats(){
  try { S.stats = await api("/stats?tz="+(-new Date().getTimezoneOffset())); }
  catch(e){ const ns = await localNotes();
    S.stats = { total_notes:ns.length,
      approved:ns.filter(n=>n.status==="approved").length,
      pending:ns.filter(n=>n.status==="pending").length,
      action_pending:ns.reduce((a,n)=>a+(n.action_items||[]).filter(x=>!x.done).length,0),
      agent_alive:false }; }
  renderStats();
  if (!S._verAt || Date.now()-S._verAt > 60000){ S._verAt = Date.now(); loadVersion(); }
}
// Real version info (app release · build stamp · agent) for the drawer footer.
// Cached so the drawer still shows something sensible offline.
const API_NEED = 8;     // keep in step with API_LEVEL (server/system.py); api_level_test asserts it
async function loadVersion(){
  let v = null;
  try { v = await api("/version"); lsSet("hg_version", JSON.stringify(v));
    // The page is served from disk but the server is a running process: after an update the
    // two can be out of step (new page, old server). Say so, instead of features silently failing.
    if ((v.api||0) < API_NEED && !$("skewBar")){
      const b = document.createElement("div"); b.id = "skewBar";
      b.style.cssText = "position:fixed;left:0;right:0;top:0;z-index:9999;background:#b45309;color:#fff;padding:10px 14px;font:600 13px/1.4 system-ui;display:flex;gap:10px;align-items:center";
      // Deliberately does not list which features are missing: that list was
      // written for one release, went stale the next, and the instruction is
      // the same whatever is missing.
      b.innerHTML = '<span style="flex:1">This page is newer than the server, so some actions will fail. Stop and start hg_ui (python3 app.py).</span><button style="background:none;border:1px solid #fff;color:#fff;border-radius:8px;padding:4px 10px" aria-label="Dismiss">✕</button>';
      b.querySelector("button").onclick = ()=>b.remove();
      document.body.appendChild(b);
    } }
  catch(e){ try { v = JSON.parse(localStorage.getItem("hg_version")||"null"); } catch(_){} }
  if (!v) return;
  const sub = $("verSub"), ft = $("verFoot");
  if (sub) sub.textContent = "AI Note Agent · v" + v.app;
  if (ft) ft.innerHTML = "పాదరసం v" + esc(v.app) + (v.build ? " · build " + esc(v.build) : "")
                       + (v.agent ? "<br/>agent v" + esc(v.agent) : "<br/>agent offline")
                       + (v.embed_model ? " · " + esc(v.embed_model.split("/").pop()) : "");
}
async function refreshAll(){ await Promise.all([loadNotes(), loadStats(), loadHome()]); }
