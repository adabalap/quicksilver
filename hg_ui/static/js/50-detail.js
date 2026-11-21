/* ═════════ Detail (reading view) ═════════ */
const SUMMARY_MIN_BODY = 280;   // characters; below this a summary repeats the note
function renderDetail(n){
  const e = n.enrichment, st = effStatus(n);
  $("dTitle").textContent = noteTitle(n);
  const stale = !!(e && n.updated_at > e.enriched_at);
  const fresh = !!(e && !stale && (st==="raw"||st==="pending")) && NET.online;
  const verbatim = !!(n.protected || n.user_edited || (n.body||"").includes("```"));
  const caps = {
    raw: fresh?"Suggestion ready to review"
       : stale?"Reworking after your edit…"
       : "Waiting for the agent",
    pending: stale?"Reworking after your edit…"
           : "Review the suggestion, then approve",
    approved: (n.user_edited && !n.protected)?"Your words — the agent adds context"
            : verbatim?"Original kept — context only"
            :"Living document — edit any time",
    force:"Processing your changes…",
    stalled:"The agent hasn't finished this one"};
  const pas = cardBg(n);

  let h = "";
  // A summary earns its place above a note only when the note is long enough
  // to need one. Over a two-line note it repeats the note, in the agent's
  // words, in the most prominent spot on the screen.
  const showSum = st==="approved" && !!e?.summary && (n.body||"").trim().length > SUMMARY_MIN_BODY;
  if (showSum) h += `
    <div class="sect" style="margin-top:0">Summary</div>
    <div class="sumcard">${esc(e.summary)}</div>`;

  h += attHtml(n);
  // While reviewing, the card shows the polished SUGGESTION (what you'll adopt);
  // approved/verbatim notes show their actual body. What you see is what you edit.
  const reviewing = (st==="pending"||st==="raw") && e && e.polished && !verbatim
                    && e.polished.trim()!==(n.body||"").trim();
  const cardText = reviewing ? e.polished : n.body;
  const cardLabel = reviewing ? "Suggested note" : "Note";
  if (n.note_type==="checklist"){
    h += renderChecklist(n);
  } else if (!( !(n.body||"").trim() && (n.attachments||[]).length )) {
    h += `<div class="sect"${showSum?"":' style="margin-top:0"'}>${cardLabel}</div>
      <div class="ncard" id="noteRead" style="${pas}">
        <div class="ntext" id="noteReadText">${mdLite(cardText)}</div>
      </div>`;
    S._editMode = reviewing ? "suggestion" : "body";   // consumed by the Edit button
  } else { S._editMode = "body"; }

  // What the note asks of you comes straight after the note. These were in
  // the collapsed Details bucket, below the AI panel and the related notes:
  // the one part of a note a person acts on was the part they had to dig for.
  if (n.action_items?.length) h += `<div class="sect">Action items</div>
    <div class="alist">${n.action_items.map(a=>`
      <div class="aitem ${a.done?"done":""}" data-aid="${esc(a.id)}" data-done="${a.done?1:0}">
        <div class="acheck">${a.done?'<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"/></svg>':""}</div>
        <div class="atext">${esc(a.text)}</div>
        <button class="adismiss" data-dismiss-aid="${esc(a.id)}"
          aria-label="Not a task" title="Not a task">✕</button></div>`).join("")}</div>`;
  if (n.reminders?.length) h += `<div class="sect">Reminders</div>
    <div class="alist">${n.reminders.map(r=>`
      <div class="aitem" data-rid="${esc(r.id)}">
        <div class="acheck rem">${r.calendar_event_id?"📅":"⏰"}</div>
        <div class="atext">${esc(r.title)}${r.datetime_hint
          ? `<span class="ahint"> · ${esc(r.datetime_hint)}</span>` : ""}${r.description?`<div class="rmsg">${linkText(r.description)}</div>`:""}</div>
        <button class="adismiss" data-dismiss-rid="${esc(r.id)}"
          data-has-event="${r.calendar_event_id?1:0}"
          aria-label="Dismiss reminder" title="Dismiss reminder">✕</button></div>`).join("")}</div>`;

  if (n.tags?.length) h += `<div class="tags">${n.tags.map(t=>
    `<span class="tag">#${esc(t)}</span>`).join("")}</div>`;

  // Status sits with the content it describes; actions move to the bottom.
  // Shown only when the state is news. "Approved" was on every note -- they
  // all auto-approve -- so the pill named a pipeline stage, not anything the
  // person could act on, and trained them to skip the row.
  if (st!=="approved" || n._pending) h += `<div class="strow" style="margin:12px 0 2px">
      ${st!=="approved"?`<span class="stpill ${st}">${statusLabel(st)}</span>`:""}
      <span class="stcap">${n._pending?"⚡ saved offline"+(st!=="approved"?" · ":""):""}${st!=="approved"?(caps[st]||""):""}</span>${st==="stalled"?`<button class="retry-btn status-act" data-status="force">Retry</button>`:""}
    </div>`;

  // ── Everything below is secondary detail: collapsed by default for a clean,
  // distraction-free reading view. A single elegant toggle expands it. ──
  let dtl = "";   // accumulate the collapsible content, then wrap it
  const _h = s => { dtl += s; };   // helper to append into the detail bucket

  if (!n.action_items?.length && n.no_actions) _h(`<div class="sect">Action items</div>
    <div class="aoff">Task extraction is off for this note.
      <button class="alink" data-extract-on="actions">Turn on</button></div>`);

  // Reminders the agent found. Dismissing one is separate from dismissing a
  // task: a reminder may already have become a real calendar event, so the
  // dismiss flow has to ask about that rather than assume.
  if (!n.reminders?.length && n.no_reminders) _h(`<div class="sect">Reminders</div>
    <div class="aoff">Reminder extraction is off for this note.
      <button class="alink" data-extract-on="reminders">Turn on</button></div>`);

  _h(`<div id="calSlot"></div>`);   // async-filled with calendar events

  if (st!=="approved" && e && stale){
    // The user just edited — the shown suggestion is outdated. Show a single
    // calm line, not the stale body at full confidence (which read as an error).
    _h(`<div class="sect">AI suggestion</div>
      <div style="padding:14px 16px;border:1px solid var(--line);border-radius:14px;
        font-size:13.5px;color:var(--ink2);display:flex;align-items:center;gap:9px">
        <span class="spinner" style="width:15px;height:15px;border-width:2px"></span>
        Reworking the suggestion after your edit…</div>`);
  } else if (st!=="approved" && e){
    _h(`<div class="sect">AI suggestion</div><div class="sug">
      <div class="sug-h"><span style="font-size:14px">${verbatim?"🔒":"✨"}</span>
        <span class="sug-l">${n.user_edited||verbatim?"Your text is kept — title &amp; tags only"
          : "On approval, this becomes your note"}</span></div>`);
    if (e.summary) _h(`<div class="sug-s"><div class="sug-sl">Summary</div>
      <div style="font-family:var(--fb);font-size:13.5px;font-style:italic;line-height:1.65;color:var(--ink2)">${esc(e.summary)}</div></div>`);
    if (!verbatim && e.polished && e.polished!==n.body) _h(`<div class="sug-s">
      <div class="sug-sl">Polished version</div>
      <div class="ntext" style="font-size:14px">${mdLite(e.polished)}</div></div>`);
    _h(`</div>`);
  } else if (st!=="approved" && !e){
    _h(`<div class="sect">AI enrichment</div>
      <div style="padding:16px;text-align:center;border:1.5px dashed var(--line2);
        border-radius:14px;font-size:13px;color:var(--ink2)">
        ${st==="pending"||st==="force"?"✨ The agent is working on this note…"
          :NET.online?"Not enriched yet — the agent picks it up automatically."
          :"⚡ Will be enriched when back online."}</div>`);
  }

  const relSeen = new Set();
  const relUnique = (n.related||[]).filter(r=>{
    if (r.id===n.id || relSeen.has(r.id)) return false;
    relSeen.add(r.id); return true; });
  // Related notes arrive newest-first from the server. The list can be long and
  // hard to scroll, so fold to the most recent few by default and let the user
  // expand. Collapsed height stays small; the freshest relations are always the
  // ones on show.
  if (relUnique.length){
    const REL_FOLD = 3;
    const folded = relUnique.length > REL_FOLD;
    const shown = (S.relExpanded ? relUnique : relUnique.slice(0, REL_FOLD));
    const relRow = r=>`<div class="rel" data-id="${esc(r.id)}">
      <span class="rel-s">${(r.score*100).toFixed(0)}%</span>
      <span class="rel-t">${esc(r.title)}</span>
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 18l6-6-6-6"/></svg>
    </div>`;
    _h(`<div class="sect">Related notes${folded?` <span class="sect-n">${relUnique.length}</span>`:""}</div>`
       + shown.map(relRow).join("")
       + (folded?`<button class="rel-more" id="relMore">${S.relExpanded
            ? "Show less"
            : `Show ${relUnique.length - REL_FOLD} more`}</button>`:""));
  }

  // Backlinks — explicit [[links]] the user created in the body
  if ((n.backlinks||[]).length) _h(`<div class="sect">Linked notes</div>` +
    n.backlinks.map(r=>`<div class="rel" data-id="${esc(r.id)}">
      <span class="rel-s" style="background:rgba(67,170,139,.12);color:var(--teal,#43AA8B)">🔗</span>
      <span class="rel-t">${esc(r.title)}</span>
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 18l6-6-6-6"/></svg>
    </div>`).join(""));

  if (st==="approved") _h(`<div class="sect">History</div>
    <button id="histBtn" class="ghost" style="width:100%;padding:12px;background:var(--bg);
      border-style:dashed">🕰 Show revisions</button>
    <div id="histWrap" style="display:none;margin-top:10px"></div>`);

  // Wrap the accumulated secondary detail behind a single elegant expander so
  // the reading view stays clean. Count the sections for a helpful label.
  if (dtl.trim()){
    const parts = [];
    if (e) parts.push("AI");
    if (relUnique.length || (n.backlinks||[]).length) parts.push("links");
    if (st==="approved") parts.push("history");
    const label = parts.length ? parts.join(" · ") : "details";
    // Preserve the expander's open/closed state across re-renders. renderDetail
    // runs after every change (dismissing a task, toggling a switch), and
    // hardcoding display:none meant the panel slammed shut each time — the user
    // had to re-open it to do the next thing. State belongs to the user, not the
    // render pass.
    const _openNow = S._detailsOpen ? "" : "display:none";
    h += `<button class="detRow${S._detailsOpen?" on":""}" id="detExpand">
        <svg class="detRow-chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M9 18l6-6-6-6"/></svg>
        <span class="detRow-l">Details</span>
        <span class="detRow-sub">${label}</span>
      </button>
      <div id="detBucket" class="detBucket" style="${_openNow}">${dtl}</div>`;
  }

  // Actions: keep the everyday ones visible (Copy, Share, Pin); tuck the rest
  // (Protect, Re-run, Polish, Delete) behind a "More" overflow so the view
  // stays clean. Delete lives in the overflow to avoid accidental taps.
  // Option A layout: a slim ICON toolbar for the everyday actions (Edit, Copy,
  // Share, More). Everything else lives in a grouped bottom sheet behind "⋯",
  // so the note stays the hero and the action count can grow without clutter.
  // Build the overflow sheet's contents (grouped; Delete separated in red).
  const org = [];
  {
    // Swatches show the REAL colours of the active theme (and light/dark
    // variant), so what you tap is what the note becomes. Position, not hex, is
    // what's stored — see color_idx in schema.sql.
    const _cur = Number.isInteger(n.color_idx) ? n.color_idx : -1;
    org.push(`<div class="sheet-colour"><span class="sa-i">🎨</span><div class="sc-body">
      <div class="sc-l">Colour</div>
      <div class="sc-row" role="radiogroup" aria-label="Note colour">
        <button class="sc-auto${_cur<0?" on":""}" data-color="auto" role="radio" aria-checked="${_cur<0}" aria-label="Automatic colour">Auto</button>
        ${_palette().map((c,i)=>`<button class="sc-dot${_cur===i?" on":""}" data-color="${i}" role="radio" aria-checked="${_cur===i}" aria-label="Colour ${i+1}" style="background:${c}"></button>`).join("")}
      </div></div></div>`);
  }
  org.push(`<button class="sheet-act${n.pinned?" on":""}" id="pinBtn"><span class="sa-i">${n.pinned?"📌":"📍"}</span><span class="sa-l">${n.pinned?"Unpin":"Pin to top"}</span></button>`);
  org.push(`<button class="sheet-act${n.archived?" on":""}" id="archBtn"><span class="sa-i">🗄</span><span class="sa-l">${n.archived?"Unarchive":"Archive"}</span></button>`);
  org.push(`<button class="sheet-act${n.hidden?" on":""}" id="hideBtn"><span class="sa-i">📥</span><span class="sa-l">${n.hidden?"Remove from Stash":"Stash"}</span></button>`);
  // The old single "AI" group mixed two different kinds of thing in identical
  // rows: one-shot ACTIONS ("run it now") and persistent SETTINGS ("how this
  // note behaves"). Nothing signalled which was a verb and which was a switch,
  // and it produced a visible contradiction — "Run AI again" offered directly
  // above an unchecked "Let AI work on this note". Split into two groups.
  //
  // Settings use a constant noun-phrase label with the ICON carrying state, so
  // the row doesn't rewrite itself as you toggle it (which is disorienting when
  // the thing you just tapped changes its own name).
  const aiDo = [];
  if (st==="approved"||((st==="raw"||st==="dirty")&&!e)) {
    // If AI is off for this note, running it necessarily turns it back on
    // (status=force clears the opt-out). Say so, rather than silently flipping
    // a setting the user deliberately set.
    const label = n.enrich_optout ? "Turn AI on and run now"
                : ((st==="raw"||st==="dirty") && !e ? "Enrich this note" : "Run now");
    // The action APPLIES the setting above — say so, so the two groups read as
    // one idea (what AI does / do it) rather than two unrelated lists.
    const sub = n.enrich_optout ? "switches AI on, then runs"
                                : "uses the setting above";
    aiDo.push(`<button class="sheet-act status-act" data-status="force">
      <span class="sa-i">⟳</span>
      <span class="sa-l">${label}<span class="sa-sub">${sub}</span></span></button>`);
  }
  // Polish OVERRIDES the setting: an explicit request beats a standing
  // preference. Only worth showing when it does something Run now WOULDN'T —
  // i.e. the text is protected (user-edited or verbatim), so a normal Full run
  // leaves the body untouched. When the mode is already Full on unprotected
  // text, Run now tidies the writing itself and this button is redundant, so
  // we hide it (your observation — remove the duplicate path).
  const _textProtected = !!(n.user_edited || n.protected || (n.body||"").includes("```"));
  if (_textProtected && st==="approved") aiDo.push(`<button class="sheet-act" id="polishBtn">
      <span class="sa-i">✨</span>
      <span class="sa-l">Tidy up my writing<span class="sa-sub">one-off — rewrites your protected text just this once</span></span></button>`);

  // One question, three answers. This replaces two overlapping booleans
  // ("Enrich with AI" + "Keep my exact words") that encoded the same decision
  // in a way nobody could hold in their head. The field list IS the
  // explanation — no invented wording beats naming what you get.
  //
  // Effective state, not raw flags: `protected` independently forces
  // metadata-only in the agent, so a protected note reads as Metadata here
  // rather than claiming Full and behaving otherwise.
  // Effective mode, not raw flags: `protected` independently forces
  // metadata-only in the agent, so a protected note reads as Metadata rather
  // than claiming Full and behaving otherwise.
  const mode = n.enrich_optout ? "off"
             : ((n.meta_only || n.protected) ? "metadata" : "full");
  // A segmented control rather than three stacked rows: same choice, a third of
  // the height, and it echoes the compose capsule so the two places you set
  // this look related. The description below reflects the current selection, so
  // the meaning is still spelled out without repeating it three times.
  const MODE_DESC = {
    full:     "title, tags, summary · rewrites body",
    metadata: "title, tags, summary · body untouched",
    off:      "the agent skips this note entirely",
  };
  // Honest about WHEN it applies. Off takes effect immediately (the agent's
  // gate skips the note from now on), but Full↔Metadata only shape the NEXT
  // enrichment — decide() keys off status and body hash alone, so flipping this
  // on a settled note changes nothing until something re-runs it.
  const modeWhen = (mode === "off")
    ? "in effect now — nothing will run"
    : (st === "approved" ? "applies next run — use Run now to apply it" : "applies when this note is enriched");
  const aiSet = [`
    <div class="mode-wrap">
      <div class="seg" role="group" aria-label="AI enrichment">
        ${["full","metadata","off"].map(v=>
          `<button class="seg-b${mode===v?" on":""}" data-enrich-mode="${v}">${
            v==="full"?"Full":v==="metadata"?"Metadata":"Off"}</button>`).join("")}
      </div>
      <div class="mode-desc">${MODE_DESC[mode]}</div>
      <div class="mode-when">${modeWhen}</div>
    </div>`];

  const grp = (title, items)=> items.length
    ? `<div class="sheet-grp-h">${title}</div>${items.join("")}` : "";
  S._actionSheetHtml =
      grp("Organize", org)
    + grp("AI", aiSet.concat(aiDo))
    + `<div class="sheet-grp-h">Danger zone</div>`
    + `<button class="sheet-act danger" id="dDelete"><span class="sa-i">🗑</span><span class="sa-l">Delete note</span></button>`;

  const editLabel = (S._editMode==="suggestion") ? "Edit & save" : "Edit";
  h += `<div class="actbar-ic">
      <button class="act-ic" id="editBtn" data-edmode="${S._editMode||"body"}" aria-label="${editLabel}"><span class="act-ic-i">✏️</span><span class="act-ic-l">${editLabel}</span></button>
      <button class="act-ic" id="cpBtn" aria-label="Copy"><span class="act-ic-i">📋</span><span class="act-ic-l">Copy</span></button>
      <button class="act-ic" id="shBtn" aria-label="Share"><span class="act-ic-i">↗</span><span class="act-ic-l">Share</span></button>
      <button class="act-ic" id="attBtn" aria-label="Attach"><span class="act-ic-i">📎</span><span class="act-ic-l">Attach</span></button>
    </div>
    <div class="att-add" id="attAddRow"><button data-attadd="photo"><span>📷</span>Photo</button><button data-attadd="voice"><span>🎙</span>Voice</button><button data-attadd="sketch"><span>✏️</span>Sketch</button></div>
    <div class="hl-cap">✨ Tip: select any text in the note to highlight it</div>`;

  const _when = t => new Date(t).toLocaleString([], {day:"numeric", month:"short", year:"numeric", hour:"numeric", minute:"2-digit"});
  h += `<div class="meta">Created ${esc(_when(n.created_at))}${n.updated_at && n.updated_at !== n.created_at
    ? ` · edited ${esc(_when(n.updated_at))}` : ""}</div>`;
  $("dBody").innerHTML = h;
  $("ctaBar").style.display = fresh ? "block" : "none";
  $("ctaBtn").textContent = verbatim ? "✓ Approve note" : "Review suggestion";
  $("dBody").style.paddingBottom = fresh ? "110px" : "";
  loadCalEvents(n.id);
}
async function loadCalEvents(nid){
  const slot = $("calSlot"); if (!slot) return;
  try {
    const evs = (await api("/calendar-events?note_id="+nid)).events;
    if (!evs.length){ slot.innerHTML=""; return; }
    slot.innerHTML = `<div class="sect">📅 Calendar events</div>` + evs.map(ev=>`
      <div class="cal-item">
        <span class="cal-i">🗓</span>
        <div style="flex:1"><div class="cal-t">${esc(ev.title)}</div>
          <div class="cal-when">${esc(ev.datetime_resolved||ev.datetime_hint||"")}</div></div>
        <span style="font-family:var(--fm);font-size:10px;color:var(--teal);font-weight:700">✓ added</span>
      </div>`).join("")
      + `<div style="font-family:var(--fm);font-size:10.5px;color:var(--mut);padding:2px 4px 4px">Manage in Google Calendar</div>`;
  } catch(e){ slot.innerHTML=""; }
}

async function copyNote(){
  if (!S.cur) return;
  try { await navigator.clipboard.writeText(S.cur.body||"");
    toast("Copied 📋","ok"); }
  catch(e){ toast("Copy failed","err"); }
}
async function shareNote(){
  if (!S.cur) return;
  shareDoc(noteToDoc(S.cur));
}
