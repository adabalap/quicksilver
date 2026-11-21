/* ═════════ Your week (digest) ═════════ */
async function openDigest(){
  closeMenu(); $("digestSheet").classList.add("on");
  $("digestBody").innerHTML = '<div class="skel" aria-hidden="true"><i style="height:120px"></i><i style="height:84px"></i><i style="height:140px"></i></div>';
  let d = null;
  try { d = (await api("/digest")).digest; } catch(e){
    $("digestBody").innerHTML = `<div class="tr-note">${e.offline?"Connect to see your digest.":"Couldn't load it."}</div>`; return; }
  S.digest = d; renderDigest();
}
function digestMd(d){
  const c = d.counts||{};
  let md = `# Your week\n\n${d.text}\n\n- ${c.notes||0} notes · ${c.photos||0} photos · ${c.voice||0} voice · ${c.sketches||0} sketches\n- ${c.tasks_done||0} tasks done · ${c.tasks_open||0} open\n`;
  if ((d.top_tags||[]).length) md += `\nMain topics: ${d.top_tags.map(t=>"#"+t.tag).join(" ")}\n`;
  if ((d.open_tasks||[]).length) md += `\n## Still open\n\n${d.open_tasks.slice(0,6).map(t=>"- "+t.text).join("\n")}\n`;
  return md;
}
function renderDigest(){
  const d = S.digest, b = $("digestBody");
  if (!d){ b.innerHTML = emptyHtml({icon:"calendar", title:"No digest yet",
      body:"One is written each Sunday evening from what you saved that week. You can ask for one now."})
      + `<div class="fl-foot"><button class="pri" data-dg="refresh">${svgIcon("sparkle",15)} Write one now</button></div>`; return; }
  const c = d.counts||{}, pl = (n,w)=>`${n} ${w}${n===1?"":"s"}`;
  const tags = d.top_tags||[], tmax = Math.max(1, ...tags.map(t=>t.count||0));
  const media = [[c.photos,"photo"],[c.voice,"voice note"],[c.sketches,"sketch"]].filter(x=>x[0]).map(x=>pl(x[0],x[1])).join(" · ");
  const when = d.ts ? relTime(d.ts) : "";
  // A short list, with the rest folded away, so the screen stays a summary.
  const list = (items, row, n) => items.slice(0,n).map(row).join("")
    + (items.length>n ? `<details class="dmore"><summary>Show ${items.length-n} more</summary>${items.slice(n).map(row).join("")}</details>` : "");
  const noteRow = t => `<button class="wk-row" data-dg="open" data-id="${esc(t.id||t.note_id)}"><span class="wk-ri">${svgIcon("note",17)}</span><span class="wk-rt">${esc(t.title)}</span>${svgIcon("arrow",14)}</button>`;
  b.innerHTML = `<div class="wk-hero">
      <div class="wk-eye">${svgIcon("sparkle",15)}<span>The week in a line</span>${when?`<em>${esc(when==="now"?"just now":when+" ago")}</em>`:""}</div>
      <div class="wk-text">${esc(d.text||"")}</div></div>
    <div class="wk-tiles">
      <div class="wk-tile"><span class="wk-ti">${svgIcon("note",18)}</span><b>${c.notes||0}</b><small>Notes</small></div>
      <div class="wk-tile ok"><span class="wk-ti">${svgIcon("tick",18)}</span><b>${c.tasks_done||0}</b><small>Tasks done</small></div>
      <div class="wk-tile${c.tasks_open?" warn":""}"><span class="wk-ti">${svgIcon("clock",18)}</span><b>${c.tasks_open||0}</b><small>Still open</small></div>
    </div>
    ${media?`<div class="wk-media">${svgIcon("camera",15)}<span>${esc(media)}</span></div>`:""}
    ${tags.length?`<div class="sect">Main topics</div><div class="dcard wk-tags">${tags.map(t=>
      `<button type="button" class="wk-tag" data-dg="tag" data-v="${esc(t.tag)}"><span class="wk-tn">#${esc(t.tag)}</span>
        <span class="wk-bar"><i style="width:${Math.max(6,Math.round(100*(t.count||0)/tmax))}%"></i></span><b>${t.count}</b></button>`).join("")}</div>`:""}
    ${(d.open_tasks||[]).length?`<div class="sect">Still open</div><div class="dcard">${list(d.open_tasks, t=>
      `<button class="wk-row" data-dg="open" data-id="${esc(t.note_id)}"><span class="wk-ri open"></span><span class="wk-rt">${esc(t.text)}</span>${svgIcon("arrow",14)}</button>`, 4)}</div>`:""}
    ${(d.revisit||[]).length?`<div class="sect">Worth another look</div><div class="dcard">${list(d.revisit, noteRow, 4)}</div>`:""}
    ${(d.notes||[]).length?`<div class="sect">This week's notes</div><div class="dcard">${list(d.notes, noteRow, 5)}</div>`:""}
    <div class="wk-actions">
      <button data-dg="refresh">${svgIcon("retry",16)}Refresh</button>
      <button data-dg="share">${svgIcon("arrow",16)}Share</button>
      <button class="pri" data-dg="save">${svgIcon("plus",16)}Save as note</button></div>`;
}
async function digestClick(t){
  const b = t.closest("[data-dg]"); if (!b) return false;
  const k = b.dataset.dg;
  if (k==="open"){ $("digestSheet").classList.remove("on"); await openDetail(b.dataset.id); }
  else if (k==="tag"){ $("digestSheet").classList.remove("on"); await applyFilters({tag:b.dataset.v}, ""); }
  else if (k==="share" && S.digest){ const d = S.digest; shareDoc({title:"Your week", md:digestMd(d), html:mdLite(digestMd(d))}); }
  else if (k==="save" && S.digest){ const m = await createNote(digestMd(S.digest), "off"); if (m) toast("Saved as a note","ok"); }
  else if (k==="refresh"){
    try { await api("/digest/refresh",{method:"POST"}); toast("Asked the agent — give it a minute","ok");
      let tries = 0; const poll = async ()=>{ if (++tries > 8 || !$("digestSheet").classList.contains("on")) return;
        try { const d = (await api("/digest")).digest; if (d && (!S.digest || d.ts !== S.digest.ts)){ S.digest = d; renderDigest(); return; } } catch(e){}
        setTimeout(poll, 8000); }; setTimeout(poll, 6000);
    } catch(e){ toast(e.offline?"Connect first":"Couldn't ask the agent","err"); }
  }
  return true;
}

/* ═════════ Possible duplicates ═════════ */
async function openDups(){
  closeMenu(); $("dupSheet").classList.add("on");
  $("dupBody").innerHTML = `<div class="tr-note">Looking…</div>`;
  await loadDups();
}
async function loadDups(){
  let items = [];
  try { items = (await api("/duplicates")).items; } catch(e){
    $("dupBody").innerHTML = `<div class="tr-note">${e.offline?"Connect to review duplicates.":"Couldn't load them."}</div>`; return; }
  S.dups = items;
  if (S.home) S.home.dups = items.length;
  if (!items.length){ $("dupBody").innerHTML = `<div class="empty"><div class="empty-i">✓</div><div class="empty-t">No duplicates</div>
      <div class="empty-b">The agent checks for near-identical notes in the background.</div></div>`; return; }
  const card = (n, did) => `<div class="dup-n"><b>${esc(n.title)}</b><p>${esc((n.preview||"").slice(0,160))}</p>
      <small>${esc(askDate(n.created_at))} · ${n.length} chars${n.attachments?` · ${n.attachments} attachment${n.attachments===1?"":"s"}`:""}</small>
      <div style="margin-top:8px"><button class="att-lnk" data-dup="keep" data-did="${did}" data-id="${esc(n.id)}">Keep this one</button>
      <button class="att-lnk" data-dup="open" data-id="${esc(n.id)}">Open</button></div></div>`;
  $("dupBody").innerHTML = `<div class="tr-note">Choose the note to keep. The other one's text is added to it, and it moves to Trash.</div>`
    + items.map(x=>`<div class="dup-pair">${card(x.a,x.id)}${card(x.b,x.id)}
      <button class="att-lnk" data-dup="no" data-did="${x.id}">Not duplicates</button></div>`).join("");
}
async function dupClick(t){
  const b = t.closest("[data-dup]"); if (!b) return false;
  const k = b.dataset.dup;
  if (k==="open"){ $("dupSheet").classList.remove("on"); await openDetail(b.dataset.id); }
  else if (k==="no"){ try { await api(`/duplicates/${b.dataset.did}/dismiss`,{method:"POST"}); } catch(e){} await loadDups(); }
  else if (k==="keep"){
    try { await api(`/duplicates/${b.dataset.did}/merge`,{method:"POST",body:{keep:b.dataset.id}});
      toast("Merged — the other note is in Trash","ok"); await loadDups(); refreshAll(); }
    catch(e){ toast(e.offline?"Connect to merge":"Couldn't merge","err"); }
  }
  return true;
}

/* ═════════ System health ═════════ */
async function openHealth(){
  closeMenu(); $("healthSheet").classList.add("on");
  $("healthBody").innerHTML = '<div class="skel" aria-hidden="true"><i style="height:96px"></i><i style="height:200px"></i><i style="height:110px"></i></div>';
  let h; try { h = await api("/health"); } catch(e){
    $("healthBody").innerHTML = emptyHtml({icon:"offline", title: e.offline?"You're offline":"Couldn't read the health",
      body: e.offline?"The app can't check the server until you're connected.":"The server didn't answer. Try again in a moment."}); return; }
  const a = h.agent||{}, v = h.versions||{}, st = h.storage||{}, jb = h.jobs||{};
  const ago = iso => iso ? relTime(iso) : "never";
  const row = (ic, k, val, tone) => `<div class="hh-row"><span class="hh-i${tone?" "+tone:""}">${svgIcon(ic,17)}</span><span class="hh-k">${esc(k)}</span><span class="hh-v${tone?" "+tone:""}">${val}</span></div>`;
  // One verdict up top, so the answer to "is everything fine?" doesn't require reading the page.
  const lowDisk = st.free_bytes!=null && st.free_bytes < 300*1024*1024;
  const issues = [];
  if (!a.alive) issues.push(["The agent isn't running", "Your notes are saved and will be processed when it's back."]);
  if (jb.dead) issues.push([`${jb.dead} item${jb.dead===1?"":"s"} gave up after retries`, "Retry them below."]);
  if (!v.match) issues.push(["App and agent versions differ", `App ${v.app}, agent ${v.agent}. Restart both after an update so they match.`]);
  if (lowDisk) issues.push(["Storage is running low", `${fmtSize(st.free_bytes)} free.`]);
  const bad = !a.alive;
  const engine = (a.medha_ok===false ? ["Medha down","built-in model on demand"] : a.medha_ok===true ? ["Medha live"] : [a.engine, a.model]).filter((x,i,arr)=>x && arr.indexOf(x)===i).join(" · ");
  // The first problem is the headline; only the others are listed below it.
  const top = issues[0];
  let out = `<div class="hh-hero ${bad?"bad":issues.length?"warn":"ok"}">
      <span class="hh-hi">${svgIcon(bad?"stop":issues.length?"alert":"tick",26)}</span>
      <div><div class="hh-ht">${top?esc(top[0]):"All systems normal"}</div>
      <div class="hh-hs">${top?esc(top[1]):("Agent running"+(engine?" · "+esc(engine):"")+(a.last_latency_s!=null?" · last answer "+a.last_latency_s+"s":""))}</div></div></div>`;
  if (issues.length > 1) out += `<div class="hh-issues">${issues.slice(1).map(x=>`<div class="hh-issue">${svgIcon("alert",16)}<div><b>${esc(x[0])}</b><span>${esc(x[1])}</span></div></div>`).join("")}</div>`;
  out += `<div class="sect">Agent</div><div class="dcard">`
    + row("pulse","Status", a.alive?`running${a.state&&a.state!=="idle"?" · "+esc(a.state):""}`:"offline", a.alive?"ok":"bad")
    + row("chip","Engine", esc(engine||"—"))
    + row("mic","Photos, voice & video", esc(a.media||"—"))
    + row("thermo","Phone temperature", esc(a.thermal||"—"), a.thermal==="hot"?"bad":a.thermal==="warm"?"warn":"")
    + row("clock","Last answer took", a.last_latency_s!=null ? a.last_latency_s+" s" : "—")
    + `</div>`;
  out += `<div class="sect">Work queue</div><div class="hh-tiles">
      <div class="hh-tile"><b>${jb.queued||0}</b><small>Waiting</small></div>
      <div class="hh-tile"><b>${jb.running||0}</b><small>Running</small></div>
      <div class="hh-tile"><b>${jb.done||0}</b><small>Done</small></div>
      <div class="hh-tile${jb.dead?" bad":""}"><b>${jb.dead||0}</b><small>Gave up</small></div></div>`;
  (h.failed||[]).forEach(f=>{ out += `<div class="tr-note" style="margin-top:8px"><b>${esc(f.key)}</b> — ${esc(f.error||"failed")}</div>`; });
  if (jb.dead) out += `<div class="fl-foot"><button class="pri" data-hl="retry">${svgIcon("retry",15)} Retry failed items</button></div>`;
  const db = st.db_bytes||0, at = st.attachments_bytes||0, fr = st.free_bytes;
  const tot = db + at + (fr||0) || 1;
  out += `<div class="sect">Storage</div><div class="dcard">
      <div class="hh-meter" role="img" aria-label="Database ${fmtSize(db)}, media ${fmtSize(at)}${fr!=null?", free "+fmtSize(fr):""}">
        <i class="m1" style="width:${Math.max(1,100*db/tot)}%"></i><i class="m2" style="width:${Math.max(1,100*at/tot)}%"></i></div>
      <div class="hh-legend"><span><i class="m1"></i>Database ${fmtSize(db)}</span><span><i class="m2"></i>Photos, voice &amp; video ${fmtSize(at)}</span>${fr!=null?`<span><i class="m3"></i>Free ${fmtSize(fr)}</span>`:""}</div>
      ${row("note","Notes", String(h.notes))}
      ${row("layers","Backups kept", `${st.backups||0} · last ${esc(ago(st.last_backup))}`)}</div>`;
  out += `<div class="sect">Versions</div><div class="dcard">`
    + row("sparkle","App", esc(v.app||"—"))
    + row("chip","Agent", esc(v.agent||"—"), v.match?"":"warn")
    + row("hash","Build", esc(v.build||"—"))
    + row("search","Search model", esc((h.search||{}).embed_model||"—")) + `</div>`;
  $("healthBody").innerHTML = out;
}
async function healthClick(t){
  const b = t.closest("[data-hl]"); if (!b) return false;
  if (b.dataset.hl==="retry"){ try { await api("/agent/jobs/retry",{method:"POST"}); toast("Retrying","ok"); } catch(e){} await openHealth(); }
  return true;
}
