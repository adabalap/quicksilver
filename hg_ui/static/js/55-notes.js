/* ═════════ Note operations (offline-aware) ═════════ */
async function createNote(body, aiMode){
  const id = nano();
  // OFF = metadata only (title/tags/summary, body untouched). ON = full.
  const extra = aiMode==="off" ? {meta_only:true} : {};
  const msg = aiMode==="off" ? "Saved — agent will tag & summarize ✨"
            : "Saved — agent will enrich ✨";
  try { await api("/notes",{method:"POST", body:{id, body, ...extra}});
    toast(msg,"ok"); }
  catch(e){
    if (e.status===413){ toast("Note too long to save","err"); return null; }
    if (!e.offline){ toast("Save failed","err"); return null; }
    idb.queue({kind:"create",id,body,iso:new Date().toISOString()});
    toast(OFFLINE_SAVE_MSG,"ok"); }
  await refreshAll();
  return {id};
}
async function patchNote(id, payload, quiet){
  try { const up = await api("/notes/"+id,{method:"PATCH",body:payload});
    const i = S.notes.findIndex(x=>x.id===id); if (i>-1) S.notes[i]=up;
    if (S.cur?.id===id) S.cur = up;
    return up; }
  catch(e){
    if (!e.offline){ if(!quiet) toast("Failed","err"); throw e; }
    idb.queue({kind:"patch",id,payload,iso:new Date().toISOString()});
    const i = S.notes.findIndex(x=>x.id===id);
    if (i>-1) Object.assign(S.notes[i], payload, {_pending:1});
    if (S.cur?.id===id) Object.assign(S.cur, payload, {_pending:1});
    if (!quiet) toast(OFFLINE_SAVE_SHORT,"ok");
    return S.cur?.id===id ? S.cur : (i>-1?S.notes[i]:null); }
}
function renameNote(){
  const n = S.cur; if (!n) return;
  const el = $("dTitle"); if (!el || el.querySelector("input")) return;
  const inp = document.createElement("input");
  inp.className = "t-edit"; inp.maxLength = 120; inp.value = noteTitle(n); inp.setAttribute("aria-label","Note title");
  el.textContent = ""; el.appendChild(inp); inp.focus(); inp.select();
  let done = false;
  const finish = async (save)=>{
    if (done) return; done = true;
    const v = inp.value.trim().replace(/\s+/g," ");
    if (save && v !== noteTitle(n)){
      try { const up = await patchNote(n.id, {title:v}, true);
        if (up && up.user_title === undefined) up.user_title = v;
        if (S.cur?.id===n.id) S.cur.user_title = v;
        const i = S.notes.findIndex(x=>x.id===n.id); if (i>-1) S.notes[i].user_title = v;
        toast(v ? "Title updated" : "Back to the suggested title","ok"); renderNotes(); }
      catch(e){ toast("Couldn't rename","err"); } }
    $("dTitle").textContent = noteTitle(S.cur || n);
  };
  inp.addEventListener("keydown", e=>{ if (e.key==="Enter"){ e.preventDefault(); finish(true); } if (e.key==="Escape") finish(false); });
  inp.addEventListener("blur", ()=>finish(true));
}
async function openAdoptReview(){
  if (!S.cur) return;
  const n = S.cur, e = n.enrichment;
  const verbatim = !!(n.protected || (n.body||"").includes("```"));
  // Protected / code notes have no polished rewrite — approving them keeps the
  // body as-is, so skip the review sheet and approve directly.
  if (verbatim || !e || !e.polished || e.polished.trim()===(n.body||"").trim()){
    await setStatus("approved"); return;
  }
  // Pre-fill with the AI's polished text; the user can tweak before adopting.
  $("adoptText").value = e.polished;
  $("adoptSheet").classList.add("on");
  const ta = $("adoptText");
  requestAnimationFrame(()=>{ autoGrow(ta); ta.scrollTop = 0; });
}
async function confirmAdopt(){
  if (!S.cur) return;
  const edited = $("adoptText").value.trim();
  if (!edited){ toast("Note can't be empty","err"); return; }
  const id = S.cur.id;
  const btn = $("adoptConfirm"); btn.disabled = true; btn.textContent = "Saving…";
  let up;
  try {
    up = await api("/notes/"+id, {method:"PATCH",
      body:{status:"approved", adopt_body:edited}});
    const i = S.notes.findIndex(x=>x.id===id); if (i>-1) S.notes[i]=up;
    S.cur = up;
  } catch(e){
    btn.disabled = false; btn.textContent = "Save";
    if (!e.offline){ toast("Couldn't save","err"); return; }
    // Offline: queue the adoption AND apply the edited body locally so the
    // note reads correctly right away. adopt_body carries the user's text.
    idb.queue({kind:"patch", id, payload:{status:"approved", adopt_body:edited},
               iso:new Date().toISOString()});
    if (S.cur){
      S.cur.body = edited; S.cur.status = "approved"; S.cur._pending = 1;
      const i = S.notes.findIndex(x=>x.id===id);
      if (i>-1){ S.notes[i].body = edited; S.notes[i].status = "approved"; S.notes[i]._pending = 1; }
    }
    $("adoptSheet").classList.remove("on");
    renderDetail(S.cur); renderNotes();
    toast(OFFLINE_SAVE_SHORT,"ok");
    return;
  }
  btn.disabled = false; btn.textContent = "Save";
  $("adoptSheet").classList.remove("on");
  renderDetail(up); renderNotes(); loadStats();
  toast("Saved — this is your note now ✨","ok");
}
async function keepOriginal(){
  // Approve the note but keep the user's ORIGINAL body (don't adopt the
  // agent's polished rewrite). The title & tags the agent added still apply.
  if (!S.cur) return;
  $("adoptSheet").classList.remove("on");
  await setStatus("approved");
  toast("Kept your original wording ✓","ok");
}
async function setStatus(status){
  if (!S.cur) return;
  const up = await patchNote(S.cur.id, {status}, true).catch(()=>null);
  if (!up) return;
  renderDetail(up); renderNotes(); loadStats();
  toast(status==="force"?"Queued for processing ⟳"
      : status==="approved"?(NET.online?"Saved — this is your note now ✨":"Approval queued ⚡")
      : "Status: "+statusLabel(status), "ok");
}
async function toggleAction(aid, cur){
  try { await api("/actions/"+aid,{method:"PATCH",body:{done:!cur}}); }
  catch(e){
    if (!e.offline) return toast("Failed","err");
    idb.queue({kind:"action",aid,done:!cur}); }
  await Promise.all([loadActions(), loadStats()]);
  if (S.cur){
    const a = (S.cur.action_items||[]).find(x=>x.id===aid);
    if (a) a.done = cur?0:1;
    try { if (NET.online) S.cur = await api("/notes/"+S.cur.id); } catch(e){}
    renderDetail(S.cur); }
  loadNotes();
}
async function delNote(){
  if (!S.cur) return;
  const ok = await showConfirm("It moves to Trash and is removed for good after 30 days.",
    {title:"Delete this note?", okText:"Delete", danger:true});
  if (!ok) return;
  const id = S.cur.id;
  let r = null;
  try { r = await api("/notes/"+id,{method:"DELETE"}); }
  catch(e){
    if (!e.offline) return toast("Couldn't move it to Trash — nothing was deleted","err");
    idb.queue({kind:"del",id}); }
  closeDetail(); invalidateViewCache(); await refreshAll();
  if (r && r.trashed) trashedToast(id);
  else if (r) toast("Deleted permanently — restart the UI server to enable Trash","err");
  else toast("Will move to Trash when you're back online");
}

/* ═════════ Detail open/close ═════════ */
async function openDetail(id){
  // A newly-opened note starts collapsed (clean reading view). The expander only
  // persists across re-renders OF THE SAME note, so an in-place change like
  // dismissing a task doesn't fold the panel the user is working in.
  if (S.cur?.id !== id) { S._detailsOpen = false; S.relExpanded = false; }
  try { S.cur = await api("/notes/"+id); }
  catch(e){ S.cur = (await localNotes()).find(n=>n.id===id);
    if (!S.cur) return toast("Note unavailable","err"); }
  renderDetail(S.cur);
  $("dSheet").classList.add("on");
}
function closeDetail(){ $("dSheet").classList.remove("on"); S.cur=null;
  _hlSel=null; $("hlbar").classList.remove("on"); }
