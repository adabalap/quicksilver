/* ═════════ Editor — isolated layer, never re-rendered externally ═════════ */
const Ed = { open:false, mode:"new", noteId:null, timer:null, saved:"", aiMode:"on" };
function openEditor(mode, note){
  Ed.open = true; Ed.mode = mode; Ed.noteId = note?.id||null;
  Ed.saved = note?.body||"";
  // Remember EXACTLY what we opened with. On close we compare against this to
  // detect a no-op edit (opened and closed, or a stray space/tab) and skip the
  // enrichment-triggering commit entirely.
  Ed.opened = note?.body||"";
  // The AI choice is per-note and NOT sticky: every new note starts at the
  // default (AI on, metadata only). A one-off "off" must never silently become
  // The AI mode is per-note and NOT sticky: every new note starts at the
  // default (Full — metadata + polish). A one-off change must never silently
  // become a permanent setting. A standing preference belongs in Settings.
  // Two-state now: ON = full enrichment, OFF = metadata-only (title/tags/summary).
  // A note that was skipped OR set to metadata-only reads as OFF; anything else
  // is ON. New notes default to ON.
  Ed.aiMode = (mode==="edit" && note)
    ? ((note.enrich_optout || note.meta_only) ? "off" : "on")
    : "on";
  syncAiChip();
  _tagCache = null;
  { const _ts = $("tagSug"); if (_ts){ _ts.hidden = true; _ts.innerHTML = ""; } }
  $("edTitle").textContent = mode==="new"?"New note":"Edit note";
  $("edLayer").style.cssText = mode==="edit" ? cardBg(note) : "background:var(--bg)";
  $("edText").value = Ed.saved;
  $("edLayer").classList.add("on");
  autoGrow($("edText"));
  // Open cleanly: header visible (scroll the layer and textarea to top), then
  // focus and place the cursor deliberately — start for a new note, end for an
  // edit so you can keep writing — rather than wherever the browser left it.
  // Avoids the "screen jumps / cursor in the wrong place" feeling.
  const ta = $("edText");
  ta.scrollTop = 0;
  const sc = $("edLayer").querySelector(".ed-sc"); if (sc) sc.scrollTop = 0;
  setTimeout(()=>{
    ta.focus();
    const pos = (mode==="edit") ? ta.value.length : 0;
    try { ta.setSelectionRange(pos, pos); } catch(e){}
    ta.scrollTop = 0;
  }, 90);
}
// The compose control cycles three modes. Each state says plainly what the user
// GETS, not what the system does internally — and the sub-hint spells out what
// happens to their words, since that's the part people care about most.
const AI_MODES = {
  // Two states. ON = the works (title, summary, polished body, tags, links,
  // action items, reminders, calendar). OFF = the light touch: title, summary,
  // tags only, body untouched. Further enhancement later lives in the kebab.
  on:  { l:"AI: on",  h:"Full enrichment — title, tags, tasks, reminders & polish",
         cls:"full" },
  off: { l:"AI: off", h:"Title, tags & summary only — your words untouched",
         cls:"meta" }
};
function syncAiChip(){
  const seg = $("edAiSeg"), hint = $("edAiHint");
  if (!seg) return;
  const on = Ed.aiMode !== "off";
  seg.setAttribute("aria-checked", on ? "true" : "false");
  const st = seg.querySelector(".ai-tog-state");
  if (st) st.textContent = on ? "On" : "Off";
  if (hint){
    const m = AI_MODES[Ed.aiMode] || AI_MODES.on;
    hint.textContent = m.h;
    hint.classList.remove("full","meta");
    hint.classList.add(m.cls);
    // Keep-style calm: the hint confirms the mode when it changes, then fades
    // so the compose area is pure note. It reappears whenever the toggle is
    // touched (that's when the consequence matters), not as permanent chrome.
    hint.classList.add("show");
    clearTimeout(_aiHintT);
    _aiHintT = setTimeout(()=>hint.classList.remove("show"), 2600);
  }
}
let _aiHintT = null;
// One toggle → flip the state. A switch is a single binary control; tapping it
// means "the other thing now".
function setAiMode(v){
  Ed.aiMode = (v === "off") ? "off" : "on";
  syncAiChip();
}
function toggleAiMode(){
  setAiMode(Ed.aiMode === "off" ? "on" : "off");
}
async function edFlush(){
  const v = $("edText").value.trim();
  // Keep-style: a NEW note comes into being on first real input, then autosaves
  // like any other. No explicit Save needed — the note exists the moment you
  // write something. AI is NOT triggered per keystroke: the agent decides on a
  // content-hash change of the settled body, so debounced saves of unchanged
  // text cost nothing, and one meaningful edit means one enrichment.
  if (Ed.mode === "new"){
    if (!v) return;                         // nothing to create yet
    let nn = null;
    try { nn = await createNote(v, Ed.aiMode); }
    catch(e){ nn = null; }
    if (nn && nn.id){
      Ed.mode = "edit"; Ed.noteId = nn.id; Ed.saved = v;
      flashSaved();
    } else {
      // Creation failed — the note does NOT exist yet. Say so and DON'T mark it
      // saved, so the next debounce/close retries rather than losing the text.
      flashSaveError();
    }
    return;
  }
  if (Ed.mode !== "edit" || !Ed.noteId) return;
  if (v === Ed.saved.trim()) return;
  let up = null;
  try { up = await patchNote(Ed.noteId, {body:v, autosave:true}, true); }
  catch(e){ up = null; }
  if (up){
    Ed.saved = v; flashSaved();
  } else {
    // Autosave failed. Do NOT advance Ed.saved — the change is still pending, so
    // the next keystroke or close will retry. Surface it rather than pretending.
    flashSaveError();
  }
}
// Show a calm, ambient "Saved" — but only settle it AFTER writes stop, so a
// burst of autosaves shows one gentle confirmation at the end, not a strobe.
let _savedFadeT = null, _savedHideT = null;
function flashSaved(){
  const el = $("edSaved"); if (!el) return;
  el.textContent = "\u2713 Saved";
  el.classList.remove("save-err");
  el.classList.add("show");
  clearTimeout(_savedFadeT); clearTimeout(_savedHideT);
  // Keep it visible briefly past the last save, then fade once.
  _savedHideT = setTimeout(()=>el.classList.remove("show"), 2200);
}
// A save failed. This MUST be visible — a silent failure is data loss. It stays
// up (doesn't auto-fade) until the next successful save clears it.
function flashSaveError(){
  const el = $("edSaved"); if (!el) return;
  el.textContent = "\u26A0 Not saved — will retry";
  el.classList.add("show","save-err");
  clearTimeout(_savedHideT);
  toast("Couldn't save yet — your text is safe, retrying","err");
}
async function syncBacklinks(noteId, body){
  // Parse [[wiki]] links from the body, resolve each to a note id via title,
  // and store the set as this note's backlinks.
  const matches = [...(body||"").matchAll(/\[\[([^\]]+)\]\]/g)].map(m=>m[1].trim());
  if (!matches.length){
    await api("/backlinks",{method:"POST",body:{src_id:noteId,dst_ids:[]}}).catch(()=>{});
    return;
  }
  const ids = [];
  for (const title of matches){
    try {
      const res = await api("/notes/search-titles?q="+encodeURIComponent(title));
      const hit = (res.notes||[]).find(n=>n.title.toLowerCase()===title.toLowerCase())
                || (res.notes||[])[0];
      if (hit && hit.id!==noteId) ids.push(hit.id);
    } catch(e){}
  }
  await api("/backlinks",{method:"POST",body:{src_id:noteId,dst_ids:ids}}).catch(()=>{});
}
async function closeEditor(){
  clearTimeout(Ed.timer);
  const v = $("edText").value.trim();
  let savedId = Ed.noteId;
  // Closing the editor is the "I'm done with this thought" signal — the moment
  // enrichment should fire. Autosave kept the body durable throughout; here we
  // COMMIT, promoting the note from 'dirty' into the enrichment queue. A note
  // taken over an hour with pauses thus enriches once, now, not mid-meeting.
  if (Ed.mode==="new"){
    if (v){
      let nn = null;
      try { nn = await createNote(v, Ed.aiMode); } catch(e){ nn = null; }
      if (nn && nn.id) savedId = nn.id;
      else if (v) { flashSaveError(); return; }   // don't close on lost data
    }
  } else if (savedId){
    const cur = $("edText").value.trim();
    // Only COMMIT if the body actually changed since the editor opened. Opening
    // a note and closing it — or adding then removing a space, or just a stray
    // tab — is NOT an edit and must not trigger a ~90s enrichment. Ed.opened
    // holds the exact text we started with; if it's unchanged, close silently.
    if (cur === (Ed.opened ?? Ed.saved ?? "").trim()){
      // nothing changed — leave status as-is, don't promote to force
    } else {
    let ok = false;
    try { ok = !!(await patchNote(savedId, {body:cur, commit:true}, true)); }
    catch(e){ ok = false; }
    if (ok){
      Ed.saved = cur;
    } else {
      // The final commit failed. This is the highest-stakes save — closing now
      // would lose the edit silently. Keep the editor open and surface it.
      flashSaveError();
      return;
    }
    }
  }
  if (savedId && /\[\[[^\]]+\]\]/.test(v)) await syncBacklinks(savedId, v);
  const was = Ed.noteId;
  Ed.open = false; Ed.noteId = null;
  $("edLayer").classList.remove("on"); $("edText").value = "";
  await refreshAll();
  if (S.cur && was===S.cur.id){
    try { S.cur = await api("/notes/"+S.cur.id); } catch(e){}
    renderDetail(S.cur); }
}
