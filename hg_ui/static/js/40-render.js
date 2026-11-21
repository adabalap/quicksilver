/* ═════════ Renders ═════════ */
function renderNotes(){
  renderHomeStrips();
  let ns = S.notes;
  if (S.filter==="protected") ns = ns.filter(n=>n.protected);
  else if (S.filter!=="all") ns = ns.filter(n=>n.status===S.filter);
  if (S.q && NET.online){ /* server already filtered */ }
  else if (S.q){ /* filtered in loadNotes offline path */ }
  let h = "";
  if (S.listView==="archived" || S.listView==="hidden"){
    const lbl = S.listView==="archived" ? "🗄 Archived" : "📥 Stash";
    const note = S.listView==="hidden"
      ? "Tucked out of your list & search — not encrypted." : "Tucked away, not deleted.";
    h += `<div class="chiprow"><button class="chip" id="clearListView">${lbl} ✕</button>
      <span class="chip" style="cursor:default;background:none;border:none;color:var(--mut);font-size:11px">${note}</span></div>`;
  }
  if (filOn()) h += `<div class="chiprow"><button class="chip" data-fil="open">⚙ ${esc(filLabel())}</button>
    <button class="chip" data-fil="clear">✕ Clear</button></div>`;
  if (S.q && S.searchMode==="semantic") h += `<div class="chiprow">
    <span class="chip" style="cursor:default">🧠 semantic results</span></div>`;
  if (S.filter!=="all" && !S.listView) h += `<div class="chiprow">
    <button class="chip" id="clearFilter">${statusLabel(S.filter)} ✕</button></div>`;
  if (!ns.length){
    h += (S.q||filOn())
      ? emptyHtml({icon:"search", title:"No matches", body:"Try a different word, tag or filter."})
      : emptyHtml(EMPTY.feed);
    morph($("feed"), h); return;
  }
  const _firstPaint = !S._painted; S._painted = true;
  // Progressive render: build the first screenful synchronously so the view
  // appears instantly, then append the rest on the next frame. At 200 notes the
  // full list is ~150 KB of HTML; painting it all at once is what makes a switch
  // feel heavy on a phone. The user sees the top of the list immediately and the
  // remainder fills in a frame later — invisible in practice, but it keeps the
  // switch feeling instant no matter how long the list grows.
  const FIRST = 30;
  if (!_firstPaint){
    // Later refreshes (the agent finished a note, a timer fired) build everything and let morph()
    // touch only the cards that changed: no flash, and the scroll position stays where it is.
    h += `<div class="masonry" id="masonryBox">` + ns.map((n,ci)=>_cardHtml(n,ci,false)).join("") + `</div>`;
    morph($("feed"), h); return;
  }
  const head = ns.slice(0, FIRST), rest = ns.slice(FIRST);
  h += `<div class="masonry${_firstPaint?" first-paint":""}" id="masonryBox">`
     + head.map((n,ci)=>_cardHtml(n,ci,_firstPaint)).join("") + `</div>`;
  $("feed").innerHTML = h; $("feed")._mh = null;
  if (rest.length){
    const box = $("masonryBox");
    requestAnimationFrame(()=>{
      if (!box || !box.isConnected) return;   // view changed before the frame
      box.insertAdjacentHTML("beforeend",
        rest.map((n,ci)=>_cardHtml(n, ci+FIRST, false)).join(""));
    });
  }
}
// Build one note card's HTML. Extracted so the list can render in batches
// (first screenful now, the rest after paint) without duplicating the markup.
function _cardHtml(n, ci, _firstPaint){
  const open = (n.action_items||[]).filter(a=>!a.done).length;
  const tag = (n.tags||[])[0];
  const isCl = n.note_type==="checklist";
  let bodyHtml, clProg="";
  if (isCl){
    const its = (n.body||"").split("\n").map(l=>l.match(/^\s*-?\s*\[([ xX])\]\s?(.*)$/))
      .filter(m=>m && m[2] && m[2].trim());
    const done = its.filter(m=>/[xX]/.test(m[1])).length;
    bodyHtml = its.slice(0,4).map(m=>`<div class="c-cl${/[xX]/.test(m[1])?" done":""}">${/[xX]/.test(m[1])?"☑":"☐"} ${esc(m[2])}</div>`).join("");
    if (its.length>4) bodyHtml += `<div class="c-cl-more">+${its.length-4} more</div>`;
    if (its.length) clProg = `<span class="c-tasks">☑ ${done}/${its.length}</span>`;
  } else {
    const pv = dedupeLead(noteTitle(n), plainPreview(n.body));
    bodyHtml = (pv || !plainPreview(n.body)) ? `<div class="c-body">${esc(pv || mediaPreview(n))}</div>` : "";
  }
  const _delay = _firstPaint ? Math.min(ci, 10) * 0.03 : 0;
  return `<article class="card${n.hidden?" card-hidden":""}" data-id="${esc(n.id)}" style="${cardBg(n)}${_firstPaint?`;animation-delay:${_delay}s`:""}">
      <span class="dot ${effStatus(n)}"></span>
      ${n.pinned?`<span class="pin-badge" aria-label="Pinned">${svgIcon("pin",12)}</span>`:""}
      <div class="c-title">${isCl?"☑️ ":""}${esc(noteTitle(n))}</div>
      ${cardMediaHtml(n)}${bodyHtml}
      <div class="c-foot">
        <span>${n._pending?"⚡ ":""}${relTime(n.updated_at)}</span>
        ${tag?`<span style="color:var(--violet);font-weight:600">#${esc(tag)}</span>`:""}
        ${isCl?clProg:(open?`<span class="c-tasks"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M9 11l3 3L22 4"/></svg>${open}</span>`:"")}
      </div>
      <div class="c-actions" role="group" aria-label="Quick actions">
        <button class="c-act" data-act="pin" data-act-id="${esc(n.id)}" title="${n.pinned?"Unpin":"Pin"}" aria-label="${n.pinned?"Unpin":"Pin"}">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="${n.pinned?"currentColor":"none"}" stroke="currentColor" stroke-width="2"><path d="M12 17v5"/><path d="M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z"/></svg>
        </button>
        <button class="c-act" data-act="archive" data-act-id="${esc(n.id)}" title="Archive" aria-label="Archive">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="4" width="20" height="5" rx="1"/><path d="M4 9v9a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9"/><path d="M10 13h4"/></svg>
        </button>
        <button class="c-act c-act-danger" data-act="delete" data-act-id="${esc(n.id)}" title="Delete" aria-label="Delete">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M10 11v6"/><path d="M14 11v6"/></svg>
        </button>
      </div></article>`;
}
function renderStats(){
  const s = S.stats;
  $("mN").textContent = s.total_notes ?? "–";
  $("mA").textContent = s.approved ?? "–";
  $("mP").textContent = s.pending ?? "–";
  const set = (id,v)=>{ $(id).textContent = v>9?"9+":v; $(id).style.display = v?"block":"none"; };
  set("bActs", s.action_pending||0);
  set("bRem", s.reminders_active||0);
  set("bNotes", s.pending||0);
  set("bFu", s.followups||0);
  // (c) Mirror the "needs review" count onto the launcher icon, so the app tells
  // you there's something waiting without being opened.
  // Badge the thing that actually needs you. It used to mirror "pending review",
  // but every note auto-approves now, so that count is permanently 0 and the
  // badge said nothing. Open action items is the number worth surfacing on the
  // launcher icon.
  setAppBadge(s.action_pending || 0);
  const on = !!s.agent_alive;
}
async function loadReminders(){
  try { S.reminders = (await api("/reminders")).reminders || []; }
  catch(e){
    S.reminders = [];
    (await localNotes()).forEach(n=>(n.reminders||[]).forEach(r=>{
      S.reminders.push({...r, note_id:n.id, note_title:noteTitle(n)}); }));
  }
  renderReminders();
}

// Soonest first unless the person chose otherwise. The default used to be
// latest first, which put a reminder for 6 December above tomorrow's bill --
// and the server already returns them soonest first, so the page was undoing
// the right order. Only an explicit "desc" keeps the old order: a person who
// toggled to it deliberately still gets it.
function remSortDir(){ try { return localStorage.getItem("hg_rem_sort") === "desc" ? "desc" : "asc"; } catch(e){ return "asc"; } }
function toggleRemSort(){ try { localStorage.setItem("hg_rem_sort", remSortDir()==="desc" ? "asc" : "desc"); } catch(e){} renderReminders(); }
function renderReminders(){
  const dir = remSortDir(), sb = $("rSort"); if (sb) sb.textContent = dir==="desc" ? "Latest first ↓" : "Soonest first ↑";
  const tsOf = r=>{ const t = Date.parse((r.datetime_resolved||"").trim()); return isNaN(t) ? null : t; };
  const rs = (S.reminders || []).slice().sort((a,b)=>{
    const x = tsOf(a), y = tsOf(b);
    if (x===null && y===null) return 0;
    if (x===null) return 1; if (y===null) return -1;      // undated always last
    return dir==="desc" ? y-x : x-y; });
  const cEl = $("rCount"); if (cEl) cEl.textContent = String(rs.length);
  if (!rs.length){
    $("rBody").innerHTML = emptyHtml(EMPTY.reminders);
    return; }
  const fmt = (r)=>{
    const dt = (r.datetime_resolved||"").trim();
    if (dt){ const d=new Date(dt); if(!isNaN(d)) return d.toLocaleString([],
      {weekday:"short",month:"short",day:"numeric",hour:"numeric",minute:"2-digit"}); }
    return (r.datetime_hint||"").trim() || "No date";
  };
  // One line per bill. A bill from a message has a heads-up reminder and one
  // on the day; drawn separately they read as two things to do, each with the
  // whole message pasted under it. The line shows the due day, mentions the
  // heads-up while it is still ahead, and leaves the message to the bill
  // itself. Dismissing the line dismisses both.
  const groups = new Map(), lines = [];
  rs.forEach(r=>{
    if (!r.bill_id){ lines.push({r, ids:[r.id]}); return; }
    const g = groups.get(r.bill_id);
    if (g){ g.all.push(r); return; }
    const ng = {bill:true, all:[r]}; groups.set(r.bill_id, ng); lines.push(ng);
  });
  const dayOf = iso => { const d = new Date((iso||"").slice(0,10)+"T12:00:00");
    return isNaN(d) ? "" : d.toLocaleDateString([], {weekday:"short", month:"short", day:"numeric"}); };
  let h = `<div class="alist">`;
  lines.forEach(L=>{
    if (!L.bill) return;
    const all = L.all, now = Date.now();
    const due = all[0].bill_due;
    const ahead = all.filter(x => { const t = tsOf(x); return t !== null && t > now && (x.datetime_resolved||"").slice(0,10) < (due||""); });
    L.r = all[all.length-1];
    L.ids = all.map(x => x.id);
    L.bill_id = all[0].bill_id;
    L.title = (all[0].title||"").replace(/\s*·\s*due\b.*$/i, "");
    L.when = due ? "due " + dayOf(due) : fmt(L.r);
    L.note = ahead.length ? "Heads-up " + dayOf(ahead[0].datetime_resolved) : "";
  });
  lines.forEach(L=>{
    const r = L.r;
    if (L.bill){
      h += `<div class="aitem" data-rid="${esc(r.id)}">
      <div class="acheck rem">⏰</div>
      <div class="atext">${esc(L.title)}
        <span class="ahint"> · ${esc(L.when)}</span>
        ${L.note?`<div class="asrc">${esc(L.note)}</div>`:""}
        <div class="asrc" data-hs="bill-open" data-id="${esc(L.bill_id)}" role="button">↗ Open the bill</div></div>
      <button class="adismiss" data-dismiss-rid="${esc(r.id)}" data-also-rids="${esc(L.ids.filter(i=>i!==r.id).join(","))}"
        data-has-event="${L.all.some(x=>x.calendar_event_id)?1:0}"
        aria-label="Dismiss this bill's reminders" title="Dismiss">✕</button></div>`;
      return;
    }
    h += `<div class="aitem" data-rid="${esc(r.id)}">
      <div class="acheck rem">${r.calendar_event_id?"📅":"⏰"}</div>
      <div class="atext">${esc(r.title)}
        <span class="ahint"> · ${esc(fmt(r))}</span>
        ${r.description?`<div class="rmsg">${linkText(r.description)}</div>`:""}
        ${r.note_title?`<div class="asrc" data-goto="${esc(r.note_id)}">↗ ${esc(r.note_title)}</div>`:""}</div>
      <button class="adismiss" data-dismiss-rid="${esc(r.id)}"
        data-has-event="${r.calendar_event_id?1:0}"
        aria-label="Dismiss reminder" title="Dismiss reminder">✕</button></div>`;
  });
  h += `</div>`;
  morph($("rBody"), h);
}

function renderActions(){
  const lbl = $("aFilterLbl");
  if (lbl) lbl.textContent =
    S.actionFilter==="aging=1" ? "· aging > 1 week" :
    S.actionFilter==="done=1"  ? "· completed" :
    S.actionFilter==="done=0"  ? "· open" : "";
  const pend = S.actions.filter(a=>!a.done), done = S.actions.filter(a=>a.done);
  $("aCount").textContent = pend.length+" open";
  if (!S.actions.length){
    morph($("aBody"), emptyHtml(EMPTY.actions));
    return; }
  const g = {};
  pend.forEach(a=>{ const k=(a.tags||"").split(",")[0].trim()||"general";
    (g[k]=g[k]||[]).push(a); });
  let h = "";
  const _was = {}; document.querySelectorAll("#aBody .agroup").forEach(x=>{ _was[x.dataset.group] = x.classList.contains("closed"); });
  const _cl = k => (k in _was ? _was[k] : k==="__done") ? " closed" : "";
  Object.entries(g).sort().forEach(([k,items])=>{
    h += `<div class="agroup${_cl(k)}" data-group="${esc(k)}">
      <div class="agroup-h">
        <svg class="agroup-chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M6 9l6 6 6-6"/></svg>
        <div class="sect" style="margin:0;flex:1">${TAGE[k]||"📌"} ${esc(k)}</div>
        <span class="agroup-count">${items.length}</span>
      </div>
      <div class="alist">${items.map(a=>aHtml(a,false)).join("")}</div></div>`; });
  // Completed items collapsed by default — they grow unbounded otherwise.
  if (done.length) h += `<div class="agroup${_cl("__done")}" data-group="__done">
    <div class="agroup-h">
      <svg class="agroup-chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M6 9l6 6 6-6"/></svg>
      <div class="sect" style="margin:0;flex:1">✅ Completed</div>
      <span class="agroup-count">${done.length}</span></div>
    <div class="alist">${done.slice(0,50).map(a=>aHtml(a,true)).join("")}</div></div>`;
  morph($("aBody"), h);
}
const aHtml = (a,d) => `<div class="aitem ${d?"done":""}" data-aid="${esc(a.id)}" data-done="${d?1:0}" tabindex="0" role="checkbox" aria-checked="${d?"true":"false"}">
  <div class="acheck">${d?'<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"/></svg>':""}</div>
  <div style="flex:1"><div class="atext">${esc(a.text)}${a.category?`<span class="acat acat-${esc(a.category)}">${esc(a.category)}</span>`:""}</div>
  ${a.blocked_by?`<div class="ablock">⏳ waiting on ${esc(a.blocked_by)}</div>`:""}
  ${a.note_title?`<div class="asrc" data-goto="${esc(a.note_id)}">↗ ${esc(a.note_title)}</div>`:""}</div></div>`;

function computeClusters(){
  // A cluster is a connected component of the relationship graph — a set of
  // notes transitively linked. Build it with union-find over ALL related
  // edges (both directions), so a hub with many links stays ONE cluster
  // instead of fragmenting when any single note's related list is capped.
  // This matches the server's Pulse count exactly.
  const byId = new Map(S.notes.map(n=>[n.id,n]));
  const parent = new Map();
  const find = x=>{ while(parent.get(x)!==x){ parent.set(x,parent.get(parent.get(x))); x=parent.get(x);} return x; };
  const union = (a,b)=>{ if(!parent.has(a))parent.set(a,a); if(!parent.has(b))parent.set(b,b);
    const ra=find(a), rb=find(b); if(ra!==rb) parent.set(ra,rb); };
  S.notes.forEach(n=>{
    (n.related||[]).forEach(r=>{ if(byId.has(r.id)) union(n.id, r.id); });
  });
  // Group note objects by their component root.
  const groups = new Map();
  parent.forEach((_,id)=>{
    if(!byId.has(id)) return;
    const root = find(id);
    if(!groups.has(root)) groups.set(root, []);
    groups.get(root).push(byId.get(id));
  });
  // Only components with 2+ members are "connections"; sort largest first.
  return [...groups.values()].filter(g=>g.length>=2)
    .sort((a,b)=>b.length-a.length);
}
function updateConnBadge(){
  const c = computeClusters().length;
  $("bConn").textContent = c>9?"9+":c;
  $("bConn").style.display = c?"block":"none";
}
