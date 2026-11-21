/* ── settings pieces ───────────────────────────────────────────────────── */
async function loadMediaSummary(){
  try { MEDIA.sum = await api("/media/summary"); } catch(e){ return; }
  const el = $("mediaSum"); if (el) el.textContent = mediaSumText();
}
function mediaSumText(){
  const s = MEDIA.sum; if (!s) return "Counting…";
  if (!s.count) return "Nothing yet.";
  const k = s.by_kind||{}, bits = [];
  if (k.image) bits.push(`${k.image} photo${k.image>1?"s":""}`);
  if (k.audio) bits.push(`${k.audio} voice note${k.audio>1?"s":""}`);
  if (k.sketch) bits.push(`${k.sketch} sketch${k.sketch>1?"es":""}`);
  if (k.video) bits.push(`${k.video} video${k.video>1?"s":""}`);
  let t = bits.join(" · ") + " · " + fmtSize(s.bytes);
  if (s.pending) t += ` · ${s.pending} waiting to be read`;
  if (s.failed) t += ` · ${s.failed} failed`;
  if (s.skipped) t += ` · ${s.skipped} skipped (no engine)`;
  return t;
}
function mediaSettingsHtml(){
  const caps = capsOn();
  const langs = [["","Device language"],["en-IN","English (India)"],["en-US","English (US)"],["te-IN","తెలుగు"],["hi-IN","हिन्दी"]];
  const cur = lsGet("hg_caps_lang");
  return `<div class="set-group">
      <div class="set-glabel">Photos, voice &amp; video</div>
      <div class="set-item">
        <div class="set-t">Live captions while recording</div>
        <div class="set-d">${SR ? "Shows words as you speak. This uses your browser's speech service — on many Android phones Chrome sends the audio to Google to do it. Leave it off to keep everything on this phone; your agent transcribes after you save."
            : "Not available in this browser. Your agent transcribes recordings after you save them."}</div>
        ${SR ? `<div class="set-seg"><button class="set-seg-b${caps?"":" on"}" data-caps-set="0">Off</button><button class="set-seg-b${caps?" on":""}" data-caps-set="1">On</button></div>
        ${caps ? `<select class="set-mini-b" id="capsLang" style="margin-top:10px;width:100%">${langs.map(([v,l])=>`<option value="${v}"${cur===v?" selected":""}>${esc(l)}</option>`).join("")}</select>`:""}` : ""}
      </div>
      <div class="set-item">
        <div class="set-t">Stored on this phone</div>
        <div class="set-d" id="mediaSum">${esc(mediaSumText())}</div>
      </div>
    </div>
    <div class="set-group">
      <div class="set-glabel">Your data</div>
      <div class="set-item" style="padding-top:4px;padding-bottom:4px">
        <a class="set-link" href="${API_BASE}/api/export" download>⬇ Export everything (.zip) <span style="color:var(--mut);font-size:12px">notes · Markdown · media</span></a>
        <button class="set-link" id="setImport" type="button">⬆ Import from Google Keep, Markdown… <span style="color:var(--mut)">›</span></button>
      </div>
    </div>`;
}

/* ── import ────────────────────────────────────────────────────────────── */
const IM = { token:null, pv:null, opts:{}, busy:false, files:null, done:null };
function openImport(){
  IM.token = IM.pv = IM.done = null; IM.busy = false;
  IM.opts = {skip_archived:false, include_trashed:false, tag_labels:true, transcribe:true, enrich:false};
  closeOverlays(); $("importSheet").classList.add("on"); impRender();
}
const IMP_FMT = {keep:"Google Keep export", quicksilver:"Quicksilver export", markdown:"Markdown / text files"};
function impOptsHtml(){
  const o = IM.opts, fmt = IM.pv?.format;
  const row = (k,t,d)=>`<label class="imp-opt"><input type="checkbox" data-impopt="${k}" ${o[k]?"checked":""}/><span><b>${t}</b><small>${d}</small></span></label>`;
  return `<div class="imp-card" style="padding:6px 14px">`
    + (fmt==="keep" ? row("skip_archived","Skip archived notes","Leave out anything you archived in Keep.")
                    + row("include_trashed","Include trashed notes","Bring back notes from Keep's bin too.")
                    + row("tag_labels","Turn Keep labels into #tags","So your old organisation keeps working.") : "")
    + row("transcribe","Read photos &amp; transcribe voice","Your agent does this on the phone, one at a time, when it's cool.")
    + row("enrich","Let the agent organise imported notes","Adds titles, tags and tasks. Slower for big imports; off by default.")
    + `</div>`;
}
function impRender(){
  const b = $("impBody");
  if (IM.done){
    const d = IM.done;
    b.innerHTML = `<div class="imp-card"><div class="imp-big">${d.imported}</div>
      <div class="imp-sub">note${d.imported===1?"":"s"} imported${d.attachments?` · ${d.attachments} attachment${d.attachments===1?"":"s"}`:""}${d.tasks?` · ${d.tasks} task${d.tasks===1?"":"s"}`:""}</div>
      ${d.attachments_failed?`<div class="imp-sub" style="color:var(--rose,#e5484d);margin-top:8px">${d.attachments_failed} attachment(s) couldn't be imported.</div>`:""}
      <div class="imp-sub" style="margin-top:10px">A safety copy of your notes was saved just before importing.</div></div>
      <button class="imp-btn" id="impDone">Done</button>`;
    return;
  }
  if (!IM.pv){
    b.innerHTML = `<div class="imp-card"><div style="font-size:15px;font-weight:700;margin-bottom:6px">Bring your notes over</div>
      <div class="imp-sub">Google Keep: go to takeout.google.com, export <b>Keep</b> only, then pick the .zip here. Also reads Quicksilver exports and Markdown / text files.</div>
      <div class="imp-sub" style="margin-top:8px">Nothing is imported until you've seen a preview.</div></div>
      <input type="file" id="impFile" accept=".zip,.md,.markdown,.txt" multiple hidden/>
      <button class="imp-btn" id="impPick" ${IM.busy?"disabled":""}>${IM.busy?"Reading…":"Choose file…"}</button>
      <div id="impErr" class="imp-sub" style="color:var(--rose,#e5484d);margin-top:10px">${esc(IM.err||"")}</div>`;
    return;
  }
  const p = IM.pv, s = p.stats||{};
  const skipped = (s.skipped_dupe||0), other = (s.skipped_empty||0)+(s.skipped_trashed||0)+(s.skipped_archived||0);
  b.innerHTML = `<div class="imp-card"><div class="imp-big">${p.to_import}</div>
      <div class="imp-sub">note${p.to_import===1?"":"s"} ready to import · ${esc(IMP_FMT[p.format]||p.format)}</div>
      <div class="imp-grid"><div><b>${s.photos||0}</b><span>Photos</span></div><div><b>${(s.voice||0)}</b><span>Voice</span></div><div><b>${s.checklists||0}</b><span>Checklists</span></div></div>
      ${skipped||other?`<div class="imp-sub" style="margin-top:12px">Skipping ${[skipped?`${skipped} already here`:"",other?`${other} empty / trashed / archived`:""].filter(Boolean).join(" · ")}.</div>`:""}
      ${s.attachments_unsupported?`<div class="imp-sub" style="margin-top:6px">${s.attachments_unsupported} unsupported attachment(s) will be left out.</div>`:""}
      ${(p.samples||[]).length?`<div style="margin-top:12px;border-top:1px solid var(--line);padding-top:10px">${p.samples.map(x=>`<div class="imp-sample">• ${esc(x)}</div>`).join("")}</div>`:""}</div>
    ${impOptsHtml()}
    <button class="imp-btn" id="impGo" ${(IM.busy||!p.to_import)?"disabled":""}>${IM.busy?"Working…":p.to_import?`Import ${p.to_import} note${p.to_import===1?"":"s"}`:"Nothing new to import"}</button>
    <button class="imp-btn ghost" id="impAgain" ${IM.busy?"disabled":""}>Choose a different file</button>`;
}
async function impUpload(files){
  if (!files.length) return;
  const total = [...files].reduce((n,f)=>n+f.size,0);
  const remote = !/^(localhost|127\.0\.0\.1)$/.test(location.hostname) && !API_BASE;
  if (remote && total > 95*1048576){
    IM.err = "That file is over 95 MB, which is more than the tunnel will carry. On the phone, run:  hg_ui import-keep <file.zip>";
    impRender(); return;
  }
  IM.busy = true; IM.err = ""; impRender();
  const fd = new FormData();
  [...files].forEach(f=>fd.append("file", f, f.name));
  Object.entries(IM.opts).forEach(([k,v])=>{ if (v) fd.append(k,"1"); });
  try { IM.pv = await apiForm("/import/upload", fd); IM.token = IM.pv.token; }
  catch(e){ IM.err = e.offline ? "You're offline." : (e.msg || "Couldn't read that file."); IM.pv = null; }
  IM.busy = false; impRender();
}
async function impRepreview(){
  IM.busy = true; impRender();
  try { const pv = await api("/import/"+IM.token+"/preview",{method:"POST",body:IM.opts}); pv.token = IM.token; IM.pv = pv; }
  catch(e){ toast("Couldn't update the preview","err"); }
  IM.busy = false; impRender();
}
async function impCommit(){
  IM.busy = true; impRender();
  try { IM.done = await api("/import/"+IM.token+"/commit",{method:"POST",body:IM.opts}); }
  catch(e){ toast(e.offline?"You're offline":"Import failed","err"); }
  IM.busy = false; impRender();
  if (IM.done) refreshAll();
}
document.addEventListener("click", ev=>{
  const t = ev.target;
  if (t.closest("#impPick")) $("impFile").click();
  else if (t.closest("#impGo")) impCommit();
  else if (t.closest("#impAgain")){ IM.pv = null; IM.token = null; IM.err = ""; impRender(); }
  else if (t.closest("#impDone")){ $("importSheet").classList.remove("on"); }
});
document.addEventListener("change", ev=>{
  const t = ev.target;
  if (t.id==="impFile") impUpload(t.files);
  else if (t.dataset?.impopt){ IM.opts[t.dataset.impopt] = t.checked; if (IM.pv) impRepreview(); }
});


/* ── trash ──────────────────────────────────────────────────────────────── */
const TR = { items:[], days:30 };
async function openTrash(){
  closeMenu(); closeOverlays(); $("trashSheet").classList.add("on");
  $("trashBody").innerHTML = `<div class="tr-note">Loading…</div>`;
  await loadTrash();
}
async function loadTrash(){
  try { const r = await api("/trash"); TR.items = r.items; TR.days = r.days; }
  catch(e){ $("trashBody").innerHTML = `<div class="tr-note">${e.offline?"Connect to see the Trash.":"Couldn't load the Trash."}</div>`; return; }
  renderTrash();
}
function renderTrash(){
  const it = TR.items; $("trashEmpty").hidden = !it.length;
  if (!it.length){
    $("trashBody").innerHTML = `<div class="empty"><div class="empty-i">🗑</div><div class="empty-t">Trash is empty</div>
      <div class="empty-b">Deleted notes wait here for ${TR.days} days before they're removed for good.</div></div>`;
    return; }
  $("trashBody").innerHTML = `<div class="tr-note">Deleted notes stay here for ${TR.days} days, then are removed for good. Restoring puts back the text, tags, tasks and attachments (calendar events aren't re-created).</div>`
    + it.map(n=>`<div class="tr-item"><div class="tr-t">${esc(n.title)}</div>
      ${n.preview?`<div class="tr-p">${esc(n.preview)}</div>`:""}
      <div class="tr-m">deleted ${esc(relTime(n.deleted_at))} · ${n.days_left} day${n.days_left===1?"":"s"} left${n.attachments?` · ${n.attachments} attachment${n.attachments===1?"":"s"}`:""}</div>
      <div class="tr-a"><button class="att-lnk" data-tr="restore" data-id="${esc(n.id)}">Restore</button>
      <button class="att-lnk danger" data-tr="purge" data-id="${esc(n.id)}">Delete forever</button></div></div>`).join("");
}
async function trashRestore(id){
  try { await api("/trash/"+id+"/restore",{method:"POST"}); }
  catch(e){ toast(e.offline?"Connect to restore":"Couldn't restore","err"); return false; }
  invalidateViewCache(); await refreshAll(); return true;
}
document.addEventListener("click", async ev=>{
  const t = ev.target;
  if (t.closest("#trashItem")){ await leaveEditorIfOpen(); await openTrash(); return; }
  if (t.closest("#trashBack")){ $("trashSheet").classList.remove("on"); return; }
  const b = t.closest("[data-tr]");
  if (b){
    const id = b.dataset.id;
    if (b.dataset.tr==="restore"){ if (await trashRestore(id)){ toast("Restored","ok"); await loadTrash(); } }
    else if (b.dataset.tr==="purge"){
      if (!await showConfirm("This note and its attachments will be removed permanently.",{title:"Delete forever?",okText:"Delete forever",danger:true})) return;
      try { await api("/trash/"+id,{method:"DELETE"}); await loadTrash(); } catch(e){ toast("Couldn't delete","err"); }
    }
    return;
  }
  if (t.closest("#trashEmpty")){
    if (!await showConfirm(`All ${TR.items.length} note${TR.items.length===1?"":"s"} in the Trash will be removed permanently.`,{title:"Empty Trash?",okText:"Empty Trash",danger:true})) return;
    try { await api("/trash",{method:"DELETE"}); toast("Trash emptied","ok"); await loadTrash(); } catch(e){ toast("Couldn't empty","err"); }
  }
});
function trashedToast(id){
  toast("Moved to Trash","ok",{label:"Undo", fn: async ()=>{ if (await trashRestore(id)) toast("Restored","ok"); }});
}
