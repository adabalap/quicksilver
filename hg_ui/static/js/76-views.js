/* ═════════ Views + menu ═════════ */
// Overlays (sheets, menus) that sit ABOVE the main views. Any of these left
// open while the user navigates elsewhere would cover the view and make it look
// like the notes vanished. switchView() dismisses them all so a main-view
// navigation always lands on a clean screen. (Previously each sheet closed only
// via its own back button, so jumping away by any other route left it stuck on
// top — the "I click Highlights/Settings then can't see my notes" bug.)
const OVERLAYS = ["menuLayer","hlSheet","setSheet","pulseSheet","tplSheet",
                  "errSheet","tagsSheet","adoptSheet","importSheet","trashSheet","filterSheet","storySheet","shareSheet","digestSheet","dupSheet","healthSheet","billSheet","photoMenu","videoMenu","dSheet","actionSheet","actionScrim"];
function closeOverlays(){
  OVERLAYS.forEach(id=>{ const el=$(id); if (el) el.classList.remove("on"); });
}
// Sidebar destinations (Dashboard, Highlights, Settings) sit at a LOWER z-index
// than the open editor, so navigating to one while editing would leave the
// editor covering it — the note appears "stuck". Flush-and-close the editor
// first (saving its content the normal way) so the destination is actually
// visible. Safe to call when the editor is closed — it's a no-op then.
async function leaveEditorIfOpen(){
  if (Ed.open) await closeEditor();
}
function setReturn(on){ S.ret = on ? "pulse" : ""; const b=$("backPill"); if (b) b.classList.toggle("on", !!on); }
function switchView(id){
  setReturn(false);
  closeOverlays();
  S.view = id;                    // record active view for context-aware refreshes
  document.querySelectorAll(".view").forEach(v=>v.classList.remove("on"));
  $(id).classList.add("on");
  document.querySelectorAll(".nitem").forEach(b=>b.classList.toggle("on",b.dataset.view===id));
  $("mainSc").scrollTop = 0;
  if (id==="vActs") loadActions();
  if (id==="vReminders") loadReminders();
  if (id==="vConn") renderConnections();
}
function applyTagsOpen(){
  let open = true; try { open = localStorage.getItem("hg_tags_open") !== "0"; } catch(e){}
  const m = $("mTags"), b = $("tagsToggle"); if (!m || !b) return;
  m.hidden = !open; b.setAttribute("aria-expanded", String(open)); b.classList.toggle("closed", !open);
}
function toggleTags(){
  let open = true; try { open = localStorage.getItem("hg_tags_open") !== "0"; localStorage.setItem("hg_tags_open", open ? "0" : "1"); } catch(e){}
  applyTagsOpen();
}
async function loadMenuTags(){
  applyTagsOpen();
  let tags = [];
  try { tags = (await api("/tags")).tags; }
  catch(e){ const cnt = {};
    (await localNotes()).forEach(n=>(n.tags||[]).forEach(t=>cnt[t]=(cnt[t]||0)+1));
    tags = Object.entries(cnt).map(([tag,count])=>({tag,count}))
      .sort((a,b)=>b.count-a.count); }
  $("mTags").innerHTML = tags.length
    ? tags.slice(0,12).map(t=>`<span class="tag" data-tag="${esc(t.tag)}" style="cursor:pointer">#${esc(t.tag)} <span style="opacity:.5">${t.count}</span></span>`).join("")
      + (tags.length>12 ? `<span class="tagall" id="tagAll">All ${tags.length} tags ›</span>` : "")
    : `<span style="font-size:11px;color:var(--mut);font-family:var(--fm)">No tags yet</span>`;
}

S.allTags = [];
async function openAllTags(){
  $("menuLayer").classList.remove("on");
  closeOverlays(); $("tagsSheet").classList.add("on");
  $("tagsQ").value = ""; $("tagsAll").innerHTML = '<div class="spinner"></div>';
  try { S.allTags = (await api("/tags")).tags; }
  catch(e){ const cnt = {}; (await localNotes()).forEach(n=>(n.tags||[]).forEach(t=>cnt[t]=(cnt[t]||0)+1));
    S.allTags = Object.entries(cnt).map(([tag,count])=>({tag,count})).sort((a,b)=>b.count-a.count); }
  renderAllTags();
}
function renderAllTags(){
  const q = ($("tagsQ").value||"").trim().toLowerCase();
  const list = S.allTags.filter(t=>!q || t.tag.toLowerCase().includes(q));
  $("tagsTitle").textContent = `All tags · ${S.allTags.length}`;
  $("tagsAll").innerHTML = list.length
    ? list.map(t=>`<span class="tag" data-tag="${esc(t.tag)}" style="cursor:pointer">#${esc(t.tag)} <span style="opacity:.5">${t.count}</span></span>`).join("")
    : `<span style="font-size:12px;color:var(--mut);font-family:var(--fm)">No tag matches “${esc(q)}”</span>`;
}
$("tagsQ").addEventListener("input", renderAllTags);

/* ═════════ History loader ═════════ */
async function loadHistory(){
  const w = $("histWrap");
  w.style.display = "block";
  $("histBtn").textContent = "▲ Hide revisions";
  w.innerHTML = '<div class="spinner"></div>';
  try {
    const revs = (await api("/notes/"+S.cur.id+"/revisions")).revisions;
    w.innerHTML = revs.length ? revs.map(r=>`
      <div class="rev"><div class="rev-h" data-rid="${esc(r.id)}">
        <span class="rev-k" style="background:${r.kind==="pre_adopt"?"rgba(108,92,231,.1)":"#fdf1e3"};
          color:${r.kind==="pre_adopt"?"var(--violet)":"#b06d24"}">${r.kind==="pre_adopt"?"before AI rewrite":"before your edit"}</span>
        <span style="font-family:var(--fm);font-size:10px;color:var(--mut);flex:1">${new Date(r.created_at).toLocaleString("en-IN")}</span>
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="var(--mut)" stroke-width="2"><path d="M6 9l6 6 6-6"/></svg></div>
      <div class="rev-b" id="rev-${esc(r.id)}">${esc(r.body)}
        <button class="rev-restore" data-restore="${esc(r.id)}">↩ Restore this version</button>
      </div></div>`).join("")
      : `<div style="font-size:12px;color:var(--mut);font-family:var(--fm);padding:8px">No revisions yet — history builds as the note evolves.</div>`;
  } catch(e){ w.innerHTML = `<div style="font-size:12px;color:var(--mut);font-family:var(--fm);padding:8px">${e.offline?"⚡ History needs a connection":"Failed to load"}</div>`; }
}


/* A thumbnail that fails to load (older server, no image tool) falls back to the full picture. */
document.addEventListener("error", e=>{
  const i = e.target;
  if (i && i.tagName==="IMG" && i.dataset && i.dataset.full && !i.dataset.fb){ i.dataset.fb = "1"; i.src = i.dataset.full; }
}, true);
