/* ═════════ Pulse dashboard ═════════ */
// True when the desktop layout is active (menu is a persistent rail, not a
// drawer). Must match the @media(min-width:900px) breakpoint in the CSS.
const _RAIL_MQ = window.matchMedia("(min-width:900px)");
const _isWide = () => _RAIL_MQ.matches;
function closeMenu(){ $("menuLayer").classList.remove("on"); }
async function openDailyNote(){
  closeMenu();
  try {
    const note = await api("/daily",{method:"POST"});
    await refreshAll();
    await openDetail(note.id);
    // Jump straight into editing today's note
    openEditor("edit", note);
  } catch(e){ toast("Couldn't open today's note","err"); }
}
async function openTemplates(){
  closeMenu();
  await renderTemplates();
  closeOverlays(); $("tplSheet").classList.add("on");
}
async function renderTemplates(){
  let tpls = [];
  try { tpls = (await api("/templates")).templates || []; } catch(e){}
  const host = $("tplList");
  if (!tpls.length){
    host.innerHTML = `<div style="text-align:center;padding:30px 16px;color:var(--mut);font-family:var(--fm);font-size:12.5px">
      No templates yet. Create one from a note structure you use often — meeting notes, 1:1s, daily logs.</div>`;
    return;
  }
  host.innerHTML = tpls.map(t=>`<div class="tpl-row">
    <div class="tpl-info" data-tpl-use="${esc(t.id)}">
      <div class="tpl-name">${esc(t.name)}</div>
      <div class="tpl-prev">${esc((t.body||"").replace(/\n/g," ").slice(0,60))}</div></div>
    <button class="tpl-del" data-tpl-del="${esc(t.id)}" aria-label="Delete template">🗑</button>
  </div>`).join("");
}
async function newTemplate(){
  const name = await showPrompt("", {title:"New template",
    value:"", okText:"Next", placeholder:"e.g. Meeting notes"});
  if (!name || !name.trim()) return;
  const body = await showPrompt("Use blank lines and ## headings.",
    {title:"Template body", value:"## Context\n\n## Notes\n\n## Action items\n",
     okText:"Save", multiline:true}) || "";
  try { await api("/templates",{method:"POST",body:{name:name.trim(),body}});
    await renderTemplates(); toast("Template saved","ok"); }
  catch(e){ toast("Couldn't save template","err"); }
}
async function useTemplate(tid){
  let tpls = [];
  try { tpls = (await api("/templates")).templates || []; } catch(e){}
  const t = tpls.find(x=>x.id===tid);
  if (!t) return;
  $("tplSheet").classList.remove("on");
  openEditor("new");
  $("edText").value = t.body || "";
  requestAnimationFrame(()=>{ autoGrow($("edText")); $("edText").focus(); });
}
async function deleteTemplate(tid){
  try { await api("/templates/"+tid,{method:"DELETE"}); await renderTemplates(); }
  catch(e){ toast("Couldn't delete","err"); }
}
let _prefs = null;
let _calEnabled = false;
async function openSettings(){
  closeMenu();
  try { const r = await api("/settings"); _prefs = r.prefs; _calEnabled = !!r.calendar_enabled; }
  catch(e){ _prefs = {}; _calEnabled = false; }
  renderSettings(); loadMediaSummary(); billsStatusSoon(); capturesStatusSoon();
  closeOverlays(); $("setSheet").classList.add("on");
}
function _setOpt(key, options){
  // options: [{v, label}] → segmented control
  return `<div class="set-seg">${options.map(o=>
    `<button class="set-seg-b${_prefs[key]===o.v?" on":""}" data-setk="${key}" data-setv="${o.v}">${o.label}</button>`
  ).join("")}</div>`;
}
// Apply a theme choice: "system" | "light" | "dark". Also updates the browser
// chrome colour so the status bar matches — a detail that makes the app feel
// native rather than like a page that happens to be dark.
function applyTheme(v){
  const root = document.documentElement;
  if (v === "light" || v === "dark") root.dataset.theme = v;
  else delete root.dataset.theme;        // "system" → let the media query decide
  const dark = v === "dark" ||
    (v === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
  const m = document.querySelector('meta[name="theme-color"]');
  if (m) m.setAttribute("content", dark ? "#2d2766" : "#6C5CE7");
  // Card backgrounds are per-note pastels set inline, and the light/dark palettes
  // differ — so re-render the visible list and any open detail to pick up the
  // right palette immediately rather than on next load.
  try { if (typeof loadNotes === "function") loadNotes(); } catch(e){}
  try { if (S.cur && typeof renderDetail === "function") renderDetail(S.cur); } catch(e){}
}
function renderSettings(){
  const p = _prefs;
  const _theme = (()=>{ try{ return localStorage.getItem("hg_theme")||"system"; }
                        catch(e){ return "system"; } })();
  const _noteTheme = getNoteTheme();
  const _connMode = getConnMode();
  const _localPort = getLocalPort();
  $("setBody").innerHTML = `
    <div class="set-group">
      <div class="set-glabel">Appearance</div>
      <div class="set-item">
        <div class="set-t">Theme</div>
        <div class="set-d">System follows your device's light/dark setting.</div>
        <div class="set-seg" id="themeOpts">
          ${["system","light","dark"].map(v=>
            `<button class="set-seg-b${_theme===v?" on":""}" data-theme-set="${v}">${v==="system"?"Auto":v[0].toUpperCase()+v.slice(1)}</button>`).join("")}
        </div>
      </div>
      <div class="set-item">
        <div class="set-t">Note color</div>
        <div class="set-d">${NOTE_THEMES[_noteTheme]?.d || "Pick a mood for your notes feed — like choosing a chat wallpaper."}</div>
        <div class="set-swatches theme-grid" id="noteThemeOpts">
          ${Object.keys(NOTE_THEMES).map(v=>{
            const t = NOTE_THEMES[v];
            const pal = (_isDark() ? t.dark : t.light).slice(0, 4);
            return `<button class="swb theme-card${_noteTheme===v?" on":""}" data-note-theme-set="${v}" title="${esc(t.label)}" aria-label="${esc(t.label)}">
              <span class="theme-cluster">${pal.map(c=>`<span class="theme-dot" style="background:${c}"></span>`).join("")}</span>
              <span class="swb-l">${esc(t.label)}</span>
            </button>`;
          }).join("")}
        </div>
      </div>
    </div>

    ${mediaSettingsHtml()}

    <div class="set-group">
      <div class="set-glabel">Connection</div>
      <div class="set-item">
        <div class="set-t">Server</div>
        <div class="set-d">${_connMode==="local"
          ? "Talking to the agent directly on this device at 127.0.0.1 — skips the tunnel entirely. Only works when the app and the server are on the same phone. If this ever breaks, come back here and switch to Remote."
          : "Remote uses the usual address (works from any device). If the app and the agent are on this same phone, Local skips the tunnel round-trip and talks to it directly."}</div>
        <div class="set-seg" id="connOpts">
          <button class="set-seg-b${_connMode==="remote"?" on":""}" data-conn-set="remote">Remote</button>
          <button class="set-seg-b${_connMode==="local"?" on":""}" data-conn-set="local">Local</button>
        </div>
        ${_connMode==="local" ? `<div class="set-portrow">
          <input type="number" inputmode="numeric" id="localPortIn" value="${esc(_localPort)}" placeholder="5230">
          <button class="set-mini-b" id="localPortApply">Apply</button>
        </div>` : ""}
      </div>
    </div>

    <div class="set-group">
      <div class="set-glabel">The agent</div>

      <div class="set-item">
        <div class="set-t">Only enrich notes longer than</div>
        <div class="set-d">Skip titles, tags &amp; summaries for very short notes — saves battery.</div>
        ${_setOpt("enrich_min_words", [{v:0,label:"Always"},{v:6,label:"6 words"},{v:12,label:"12 words"},{v:20,label:"20 words"}])}
      </div>

      <div class="set-item">
        <div class="set-t">Generate summaries</div>
        <div class="set-d">A one-line gist the agent writes for a note.</div>
        ${_setOpt("summary_mode", [{v:"always",label:"Always"},{v:"long",label:"Long notes"},{v:"never",label:"Never"}])}
      </div>

      <div class="set-item">
        <div class="set-t">Daily brief</div>
        <div class="set-d">On-demand keeps the agent quiet until you tap refresh.</div>
        ${_setOpt("brief_mode", [{v:"ondemand",label:"On demand"},{v:"auto",label:"Automatic"}])}
      </div>

      <div class="set-item">
        <div class="set-t">Task follow-ups</div>
        <div class="set-d">Once a day the agent picks tasks that have sat for a week and suggests one move: plan it, start it, or drop it. Nothing changes until you tap.</div>
        ${_setOpt("followups", [{v:"on",label:"On"},{v:"off",label:"Off"}])}
        ${(_prefs.followups||"on")==="on" ? '<div class="set-portrow"><button class="set-mini-b" id="fuCheck">Check now</button></div>' : ""}
      </div>

      <div class="set-item">
        <div class="set-t">Google Calendar</div>
        <div class="set-status ${_calEnabled?"ok":"off"}">
          <span class="set-dot"></span>
          ${_calEnabled ? "Connected — events sync to your calendar"
            : "Not connected — run <b>calendar-auth</b> in Termux, then set calendar_enabled"}
        </div>
      </div>
    </div>

    <div class="set-group">
      <div class="set-glabel">Notes</div>
      <div class="set-item">
        <div class="set-t">Default new note</div>
        ${_setOpt("default_note_type", [{v:"text",label:"Text"},{v:"checklist",label:"Checklist"}])}
      </div>
      <div class="set-item">
        <div class="set-t">Default highlight colour</div>
        ${_setOpt("highlight_color", [{v:"y",label:"Yellow"},{v:"g",label:"Green"},{v:"p",label:"Pink"},{v:"b",label:"Blue"}])}
      </div>
      <div class="set-item">
        <div class="set-t">Search precision</div>
        <div class="set-d">How closely a note must match your search. Stricter shows fewer, more relevant results; looser finds more loosely-related notes.</div>
        ${_setOpt("search_min_similarity", [{v:0.20,label:"Loose"},{v:0.30,label:"Balanced"},{v:0.40,label:"Strict"}])}
      </div>
      <div class="set-item">
        <div class="set-t">Notifications</div>
        <div class="set-d">${_notifyDesc()}</div>
        ${_notifyControl()}
      </div>
    </div>

    <div class="set-group">
      <div class="set-glabel">Bills</div>
      <div class="set-item">
        <div class="set-t">Bill reminders from messages</div>
        <div class="set-d">Reads bill messages on this phone and sets two reminders for each, two days before and on the day at 9:00. A bill closes itself when its payment message arrives. Each reminder shows the original bill message, with its payment link, until the bill is paid; nothing else is kept.</div>
        ${_setOpt("sms", [{v:"on",label:"On"},{v:"off",label:"Off"}])}
        <div class="set-d" id="billsStatus" aria-live="polite"></div>
        <div class="set-portrow"><button class="set-mini-b" id="billsList">View and add bills</button></div>
        ${_prefs.sms==="on" ? '<div class="set-portrow"><button class="set-mini-b" id="billsScan">Check now</button><button class="set-mini-b" id="billsForget">Forget everything</button></div>' : ""}
      </div>
    </div>

    <div class="set-group">
      <div class="set-glabel">Phone notifications</div>
      <div class="set-item">
        <div class="set-t">Read notifications from Medha</div>
        <div class="set-d">Medha can see the notifications your phone shows — including a work calendar that lives inside Outlook or Teams and never reaches Android's own calendar. With this on, Quicksilver reads that stream and keeps what looks like an observation about your life for a few weeks (the status line below says exactly how long). One-time passcodes are never included, and neither is device chatter like charging or screenshots.</div>
        <div class="set-d"><strong>Nothing here is added to your brief.</strong> It is used the other way round: a notification saying a meeting was cancelled, or a bill was paid, settles a line that was going to be on your brief anyway.</div>
        ${_setOpt("captures", [{v:"on",label:"On"},{v:"off",label:"Off"}])}
        <div class="set-d" id="capturesStatus" aria-live="polite"></div>
        ${_prefs.captures==="on" ? '<div class="set-portrow"><button class="set-mini-b" id="capturesScan">Check now</button><button class="set-mini-b" id="capturesSample">See what was kept</button><button class="set-mini-b" id="capturesForget">Forget everything</button></div>' : ""}
      </div>
    </div>

    <div class="set-group">
      <div class="set-glabel">Help</div>
      <div class="set-item">
        <div class="set-t">Quick tour</div>
        <div class="set-d">Four short screens on capturing, the agent, Ask and follow-ups.</div>
        <div class="set-portrow"><button class="set-mini-b" id="tourBtn">Replay tour</button></div>
      </div>
    </div>

    <div class="set-note">Changes apply to new activity. Notes already enriched aren't reprocessed.</div>`;
}
async function saveSetting(key, val){
  if (key==="enrich_min_words" || key==="summary_min_words") val = parseInt(val);
  else if (key==="search_min_similarity") val = parseFloat(val);
  else if (key==="notify_muted") val = parseInt(val) ? 1 : 0;
  _prefs[key] = val;
  renderSettings(); billsStatusSoon(); capturesStatusSoon();
  try { await api("/settings",{method:"POST",body:{[key]:val}}); toast("Saved","ok"); }
  catch(e){ toast("Couldn\'t save setting","err"); }
}
function openQuickEvent(){
  const now = new Date();
  $("evTitle").value = "";
  $("evDate").value = now.toISOString().slice(0,10);
  $("evTime").value = "";
  $("evNotes").value = "";
  $("eventModal").classList.add("on");
  requestAnimationFrame(()=>$("evTitle").focus());
}
async function saveQuickEvent(){
  const title = $("evTitle").value.trim();
  if (!title){ $("evTitle").focus(); return; }
  const date = $("evDate").value;      // YYYY-MM-DD
  const time = $("evTime").value;      // HH:MM or ""
  const notes = $("evNotes").value.trim();
  // Compose a natural-language note the agent recognises as an event. Include
  // an explicit ISO datetime so extraction is reliable.
  let when = "";
  if (date){
    const d = new Date(date + (time?("T"+time):"T09:00"));
    if (!isNaN(d.getTime())){
      when = d.toLocaleString("en-IN", {weekday:"long", year:"numeric", month:"long",
        day:"numeric", ...(time?{hour:"2-digit", minute:"2-digit"}:{})});
    }
  }
  const body = `📅 ${title}` + (when?`\n\nWhen: ${when}`:"") +
    (date?`\n(${date}${time?" "+time:""})`:"") + (notes?`\n\n${notes}`:"");
  // Build a deterministic reminder so the calendar event doesn't depend on the
  // model re-parsing the date. datetime_hint uses ISO the resolver understands.
  const reminder = date ? {
    title: title,
    datetime_hint: date + (time ? ("T"+time) : ""),
    datetime_resolved: date + (time ? (" "+time) : ""),
    description: notes || ""
  } : null;
  $("eventModal").classList.remove("on");
  const id = nano();
  const payload = {id, body};
  if (reminder) payload.reminder = reminder;
  try { await api("/notes",{method:"POST",body:payload});
    toast("Event saved — agent will add it to your calendar 📅","ok"); }
  catch(e){ if(!e.offline){ toast("Couldn\'t save event","err"); return; }
    idb.queue({kind:"create",id,body,...(reminder?{reminder}:{}),iso:new Date().toISOString()});
    toast(PERSIST_OK ? "Event saved offline ⚡" : "Event saved in this tab ⚡","ok"); }
  await refreshAll();
}
async function openHighlights(){
  closeMenu();
  const host = $("hlList");
  host.innerHTML = `<div style="text-align:center;padding:24px;color:var(--mut);font-family:var(--fm);font-size:12px">Loading…</div>`;
  closeOverlays(); $("hlSheet").classList.add("on");
  let hls = [];
  try { hls = (await api("/highlights")).highlights || []; } catch(e){}
  if (!hls.length){
    host.innerHTML = `<div style="text-align:center;padding:36px 18px;color:var(--mut);font-family:var(--fm);font-size:12.5px;line-height:1.7">
      No highlights yet.<br/>Select any text in a note and pick a colour to highlight it.</div>`;
    return;
  }
  const cname = {y:"hl-y",g:"hl-g",p:"hl-p",b:"hl-b"};
  // group by note
  const byNote = {};
  hls.forEach(h=>{ (byNote[h.note_id] = byNote[h.note_id] || {title:h.note_title, items:[]}).items.push(h); });
  host.innerHTML = Object.entries(byNote).map(([nid,grp])=>`
    <div class="hl-group">
      <div class="hl-group-t" data-hl-note="${esc(nid)}">${esc(grp.title)}
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 18l6-6-6-6"/></svg></div>
      ${grp.items.map(h=>`<div class="hl-item" data-hl-note="${esc(nid)}">
        <mark class="hl ${cname[h.color]||"hl-y"}">${esc(h.text)}</mark></div>`).join("")}
    </div>`).join("");
}
async function openPulse(silent){
  silent = silent === true;               // true = quiet in-place refresh of a dashboard that's already open
  if (!silent){
    $("menuLayer").classList.remove("on");
    closeOverlays(); $("pulseSheet").classList.add("on");
    $("pulseBody").innerHTML = '<div class="skel" aria-hidden="true"><i style="height:44px;width:55%"></i><i style="height:92px"></i><i style="height:120px"></i><i style="height:150px"></i></div>';
  }
  let p;
  try { p = await api("/pulse?tz="+(-new Date().getTimezoneOffset())); }
  catch(e){ if (silent) return; $("pulseBody").innerHTML = emptyHtml({icon:"offline", title:"Dashboard needs a connection", body:"Agent status and your weekly digest appear when the server is reachable."}); return; }
  if (silent && !$("pulseSheet").classList.contains("on")) return;
  const d = p.digest||{}, jb = p.jobs||{}, ix = p.index, tdy = p.today||{};
  const alive = p.agent_alive, tel = p.telemetry||{};
  // Vitals are the agent's last report. When it's offline they're history, not status, so
  // we show "last seen" instead of a stale "46°C".
  const t = alive ? tel : {};
  const thermal = t.thermal_state||"cool";
  const inflight = (jb.queued||0)+(jb.running||0);
  const model = tel.medha_ok===false ? "Medha down · built-in model" : tel.medha_ok===true ? "Medha live" : (tel.model && tel.model!=="unknown" ? tel.model : "");
  const seen = p.agent_last_seen ? "seen "+relTime(p.agent_last_seen)+(relTime(p.agent_last_seen)==="now"?"":" ago") : "never seen";
  const hr = new Date().getHours();
  const greet = hr<5?"Late night":hr<12?"Good morning":hr<17?"Good afternoon":"Good evening";
  const tile = (icon,n,l,nav,tone)=>`<button class="dtile ${tone||""}" data-pulse-nav="${nav}"><span class="dtile-i">${svgIcon(icon,18)}</span><span class="dtile-n">${n}</span><span class="dtile-l">${l}</span></button>`;
  // 1 ─ the greeting and one line of status. Detail lives in the footer.
  let h = `<div class="dgreet dsec"><div class="dgreet-h">${greet}</div>
    <div class="dgreet-s"><span class="pulse-hero-dot" style="${alive?"":"opacity:.4"}"></span>${alive
      ? "Agent live"+(model?" · "+esc(model):"")+(inflight?` · ${inflight} in the queue`:"")
      : "Agent offline · "+esc(seen)}</div></div>
    <div id="briefSlot" class="dsec"></div>
    <div id="fuSlot" class="dsec"></div>`;
  // 2 ─ what needs you. Quiet when nothing does.
  const needs = [];
  // First: these are things the person pressed Save on, so they are the
  // likeliest of anything here to be wanted right now.
  if (d.saved_waiting) needs.push(dRow("note","Saved from Medha", d.saved_waiting, "", "saved:"));
  if (d.pending_review) needs.push(dRow("eye","Notes waiting for review", d.pending_review, "", "filter:pending"));
  if (d.errors_current) needs.push(dRow("alert","Couldn't be processed", d.errors_current, "warn", "errors:"));
  if (jb.dead) needs.push(`<div class="drow"><div class="drow-i warn">${svgIcon("stop",17)}</div>
      <div class="drow-t">Failed every retry</div><div class="drow-v" style="color:var(--amber)">${jb.dead}</div>
      <button class="ghost" data-pact="retry-jobs" style="padding:6px 12px;margin-left:6px">Retry</button></div>`);
  if (d.actions_aging) needs.push(dRow("clock","Tasks over a week old", d.actions_aging, "warn", "view:vActs?aging=1"));
  const calmAll = !needs.length && !((S.stats||{}).followups);
  if (needs.length) h += `<div class="sect">Needs you</div><div class="dcard dsec">${needs.join("")}</div>`;
  else if (calmAll) h += `<div class="dcard dsec dclear">${svgIcon("tick",18)}<span>You're all caught up. Nothing needs you right now.</span></div>`;
  // 3 ─ what the agent did today (exact counts), with the recent items folded away
  const bits = [];
  const pl = (n,w)=>`${n} ${w}${n===1?"":"s"}`;
  if (tdy.organised) bits.push("organised "+pl(tdy.organised,"note"));
  if (tdy.tasks) bits.push("found "+pl(tdy.tasks,"task"));
  if (tdy.reminders) bits.push("set "+pl(tdy.reminders,"reminder"));
  if (tdy.planned) bits.push("planned "+pl(tdy.planned,"follow-up"));
  const act = p.activity||[];
  const kindIc = {organised:"sparkle", task:"check", reminder:"bell"}, kindVb = {organised:"Organised", task:"Found task", reminder:"Found reminder"};
  h += `<div class="sect">Today</div><div class="dcard dsec">
    <div class="dtoday">${bits.length
      ? "Today the agent "+bits.slice(0,-1).join(", ")+(bits.length>1?" and ":"")+bits[bits.length-1]+"."
      : "Nothing yet today. The agent works as you write."}</div>
    ${act.length?`<details class="dmore"><summary>Recent activity</summary>`+act.map(a=>`<div class="drow" data-pact="open" data-id="${esc(a.id)}" style="cursor:pointer">
        <div class="drow-i">${svgIcon(kindIc[a.kind]||"note",17)}</div>
        <div class="drow-t" style="min-width:0"><div class="dtrunc">${esc(a.title||"Untitled note")}</div>
          <div class="dmeta">${kindVb[a.kind]||""} · ${esc(relTime(a.t))}</div></div></div>`).join("")+`</details>`:""}
  </div>`;
  // 4 ─ at a glance: only what isn't zero
  const glance = [
    [d.open_actions, "check", "Open tasks", "view:vActs", ""],
    [d.connections, "link", "Connections", "view:vConn", ""],
    [d.protected, "lock", "Protected", "filter:protected", ""],
  ].filter(x=>x[0]);
  if (glance.length) h += `<div class="sect">At a glance</div><div class="dtiles dsec" style="grid-template-columns:repeat(${glance.length},1fr)">`
    + glance.map(x=>tile(x[1], x[0], x[2], x[3], x[4])).join("") + `</div>`;
  // 5 ─ the week, with the numbers on the bars
  const cap = p.capture||[], capMax = Math.max(1, ...cap.map(c=>c.n)), capTot = cap.reduce((x,c)=>x+c.n,0);
  const dn = ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"];
  const dName = c => dn[new Date(c.d+"T12:00:00").getDay()];
  h += `<div class="sect">This week</div>
    <div class="dcard dsec">
      <div class="dchart" role="img" aria-label="Notes captured per day: ${cap.map(c=>dName(c)+" "+c.n).join(", ")}">
        ${cap.map((c,i)=>`<div class="dbar${i===cap.length-1?" now":""}">
          <span class="dbar-n">${c.n}</span>
          <div class="dbar-b"><i style="height:${c.n?Math.max(8,Math.round(100*c.n/capMax)):3}%"></i></div>
          <span class="dbar-d">${dName(c)}</span></div>`).join("")}
      </div>
      ${dRow("note","Notes captured", capTot, "", (capTot?"view:vNotes":""))}
      ${dRow("target","Tasks completed", d.done_this_week??0, "", (d.done_this_week?"view:vActs?done=1":""))}
    </div>`;
  if ((p.top_tags_week||[]).length){
    h += `<div class="sect">Top themes this week</div><div class="tags dsec" style="margin-top:0">`
       + p.top_tags_week.map(t=>`<span class="tag">#${esc(t.tag)} <span style="opacity:.6">${t.c}</span></span>`).join("")
       + `</div>`;
  }
  h += `<div class="sect">Calendar events</div><div id="pulseCal" class="dsec"><div class="spinner" style="margin:12px auto"></div></div>`;

  h += `<div id="consolCard"></div>`;
  // 6 ─ status footer: the vitals, for when you want them
  h += `<div class="sect">Agent status</div><div class="dcard dsec">
    ${dRow(alive?"pulse":"offline","Agent", alive?"live":esc(seen), alive?"":"warn", "")}
    ${alive?dRow(thermal==="hot"?"flame":thermal==="warm"?"thermo":"snow","Temperature", t.soc_temp_c!=null?t.soc_temp_c+"°C · "+thermal:thermal, thermal==="hot"?"warn":"", ""):""}
    ${alive&&t.avg_latency_s!=null?dRow("clock","Average per note", t.avg_latency_s+"s", "", ""):""}
    ${dRow("queue","Queue", inflight?inflight:"clear", "", "")}
    ${jb.retrying?dRow("retry","Waiting to retry", jb.retrying, "warn", ""):""}
    ${ix?`<div class="drow" style="flex-direction:column;align-items:stretch;gap:7px">
      <div style="display:flex;align-items:center;gap:12px"><div class="drow-i">${svgIcon("search",17)}</div>
      <div class="drow-t">Search index</div><div class="drow-v">${ix.done}/${ix.total}</div></div>
      <div class="dprog" role="progressbar" aria-valuemin="0" aria-valuemax="${ix.total}" aria-valuenow="${ix.done}" aria-label="Search index"><i style="width:${ix.total?Math.round(100*ix.done/ix.total):100}%"></i></div>
      <div class="dmeta">${esc(ix.model)}${ix.done<ix.total?" · indexing in the background":""}</div>
      ${(ix.waiting||[]).length?`<div class="dmeta">Still waiting: ${ix.waiting.map(w=>`<a href="#" data-pact="open" data-id="${esc(w.id)}">${esc(w.title)}</a>`).join(" · ")}</div>`:""}</div>`:""}
  </div>`;
  if (silent){
    // Swap only what we own; keep the brief / calendar / consolidation cards the
    // user is already looking at, and their scroll position.
    // morph(): only the sections whose content changed are touched, so the screen doesn't flash and
    // the scroll position stays put. The async slots keep what they already show.
    const keep = ["briefSlot","fuSlot","pulseCal","consolCard"];
    const _od = [...$("pulseBody").querySelectorAll("details")];
    morph($("pulseBody"), h, {pre: frag=>{ [...frag.querySelectorAll("details")].forEach((d,i)=>{ if (_od[i] && _od[i].open) d.setAttribute("open",""); });
      keep.forEach(id=>{
      const o = $(id), n = frag.getElementById ? frag.getElementById(id) : frag.querySelector("#"+id);
      if (o && n) n.innerHTML = o.innerHTML; }); }});
    loadFollowups();
    return;
  }
  $("pulseBody").innerHTML = h; $("pulseBody")._mh = null;
  loadFollowups();
  loadConsolidation();
  loadBrief();
  loadPulseCalendar();
}
// Keep an open dashboard honest: refresh when the agent reports something, and
// on a slow timer while it's on screen (queue depth, temperature and "alive"
// all change without any user action).
let _pulseT = 0;
function pulseSoon(){
  if (!$("pulseSheet").classList.contains("on")) return;
  clearTimeout(_pulseT); _pulseT = setTimeout(()=>openPulse(true), 700);
}
setInterval(()=>{ if (document.visibilityState==="visible") pulseSoon(); }, 15000);
document.addEventListener("visibilitychange", ()=>{ if (document.visibilityState==="visible") pulseSoon(); });
// ── Task follow-through: stale tasks the agent wants a decision on ──────────────
const _FU_OFF = {drop:"dropping tasks", reschedule:"moving tasks to another day",
                 today:"doing tasks today", first_step:"first-step suggestions"};
function _localISO(d){ const p=n=>String(n).padStart(2,"0"); return d.getFullYear()+"-"+p(d.getMonth()+1)+"-"+p(d.getDate()); }
function fuDay(iso){
  if (!iso) return "";
  const now = new Date(), t = _localISO(now); now.setDate(now.getDate()+1);
  if (iso === t) return "today";
  if (iso === _localISO(now)) return "tomorrow";
  return new Date(iso+"T12:00:00").toLocaleDateString(undefined,{weekday:"short",day:"numeric",month:"short"});
}
function fuLabel(f){
  const day = fuDay(f.move_date);
  if (f.move==="drop") return "Drop it";
  if (f.move==="today") return day==="today" ? "Do it today" : "Do it "+day;
  if (f.move==="first_step") return "Plan it for "+day;
  return "Move to "+day;
}
async function loadFollowups(){
  const host = $("fuSlot"); if (!host) return;
  let r; try { r = await api("/followups"); } catch(e){ return; }   // keep whatever is already showing
  const items = r.items||[], L = r.learning||{};
  const until = L.paused_until ? new Date(L.paused_until) : null;
  if (!items.length){
    morph(host, until && until > new Date()
      ? `<div class="fu-note">Task follow-ups are paused until ${esc(until.toLocaleDateString(undefined,{weekday:"short",day:"numeric",month:"short"}))} — you've said "not now" a few times in a row.</div>` : "");
    return;
  }
  const off = (L.disabled||[]).map(m=>_FU_OFF[m]).filter(Boolean);
  morph(host, `<div class="fu">
    <div class="fu-h"><span>Needs a decision</span><span class="fu-n">${items.length}</span></div>
    ${items.map(f=>`<div class="fu-i" data-fu-id="${f.id}">
      <div class="fu-t" data-pact="open" data-id="${esc(f.note_id)}">${esc(f.text)}</div>
      <div class="fu-s">${esc(f.reason)}${f.note_title?" · "+esc(f.note_title):""}</div>
      ${f.step?`<div class="fu-step">Start with: <b>${esc(f.step)}</b></div>`:""}
      <div class="fu-row">
        <button class="fu-go" data-fu-act="accept" data-fu-id="${f.id}">${esc(fuLabel(f))}</button>
        ${f.move!=="drop"?`<label class="fu-when" title="Pick another day">${svgIcon("calendar",19)}<input
          type="date" data-fu-date="${f.id}" min="${_localISO(new Date())}"
          aria-label="Move this to a day you pick"></label>`:""}
      </div>
      <div class="fu-sec">
        <button data-fu-act="done" data-fu-id="${f.id}">Already done</button>
        ${f.move!=="drop"?`<button data-fu-act="drop" data-fu-id="${f.id}">Drop</button>`:""}
        <button data-fu-act="dismiss" data-fu-id="${f.id}">Not now</button>
      </div></div>`).join("")}
    ${off.length?`<div class="fu-note">Not suggesting ${esc(off.join(" or "))} for now — you usually say no to those.</div>`:""}
  </div>`);
}
async function fuAct(act, id, date){
  document.querySelector(`.fu-i[data-fu-id="${id}"]`)?.classList.add("busy");
  let r;
  try { r = await api("/followups/"+id+"/"+act,
                      date ? {method:"POST", body:{date}} : {method:"POST"}); }
  catch(e){
    toast(e && e.status===409 ? "That task is already finished" : e && e.offline ? "You're offline — try again when connected" : "Couldn't save that",
          e && e.status===409 ? "ok" : "err");
    await loadFollowups(); loadStats(); return; }
  const msg = act==="accept" ? (r.move==="drop" ? "Dropped" : "Planned for "+fuDay(r.date))
            : act==="done" ? "Marked done" : act==="drop" ? "Dropped" : "Okay — I'll ask again later";
  toast(msg, "ok", {label:"Undo", fn: async ()=>{
    await api("/followups/"+id+"/undo",{method:"POST"}).catch(()=>null);
    await loadFollowups(); loadStats(); try { loadActions(); } catch(_){} }});
  await loadFollowups(); loadStats();
  if (act!=="dismiss") { try { loadActions(); } catch(_){} }
}
async function loadConsolidation(){
  let offer = null;
  try { offer = (await api("/consolidation")).offer; } catch(e){}
  const host = $("consolCard");
  if (!host) return;
  if (!offer || !offer.tag || (offer.note_count||0) < 4){ host.innerHTML = ""; return; }
  host.innerHTML = `<div class="consol">
    <div class="consol-h"><span class="drow-ic">${svgIcon("layers",18)}</span>
      <span>A theme is emerging</span></div>
    <div class="consol-b">You have <b>${offer.note_count}</b> notes tagged
      <b>#${esc(offer.tag)}</b> this week. Want the agent to draft a single
      consolidated summary that ties them together?</div>
    <div class="consol-act">
      <button class="consol-yes" id="consolYes">${svgIcon("sparkle",15)} Draft it</button>
      <button class="consol-no" id="consolNo">Dismiss</button>
    </div></div>`;
}
async function showErrors(){
  let sheet = $("errSheet");
  if (!sheet){
    sheet = document.createElement("div");
    sheet.className = "sheet"; sheet.id = "errSheet";
    sheet.innerHTML = `<div class="sh-hd">
      <button class="ibtn" id="errBack" aria-label="Back">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M19 12H5M12 19l-7-7 7-7"/></svg>
      </button><span class="sh-t">Agent errors</span></div>
      <div class="sh-sc" id="errBody"><div class="spinner"></div></div>`;
    document.body.appendChild(sheet);
  }
  sheet.classList.add("on");
  const body = $("errBody"); body.innerHTML = '<div class="spinner"></div>';
  try {
    const errs = (await api("/errors")).errors;
    if (!errs.length){
      body.innerHTML = `<div class="empty"><div class="empty-i">✅</div>
        <div class="empty-t">No failures right now</div>
        <div class="empty-b">Notes the agent couldn't process would show here with a retry.</div></div>`;
      return;
    }
    // Retry was the only action, so a note that will never enrich could not
    // leave this list. Every row now also offers Dismiss, and the header
    // offers to clear the lot — after a model outage they all failed together
    // and they are all the same decision.
    body.innerHTML = `<div style="display:flex;align-items:flex-start;gap:12px;margin-bottom:14px">
      <div style="flex:1;font-family:var(--fb);font-size:13.5px;color:var(--ink2);line-height:1.6">These
      notes couldn't be enriched — usually a transient model hiccup. Retry to re-queue,
      or dismiss to stop tracking it.</div>
      <button class="ghost" id="errClearAll" style="flex-shrink:0;padding:7px 12px;white-space:nowrap">Dismiss all</button>
      </div>`
      + errs.map(e=>`<div class="err-item" data-err-note="${esc(e.id)}" style="display:flex;
        align-items:center;gap:10px;padding:13px;border:1px solid var(--line);border-radius:12px;
        margin-bottom:9px;cursor:pointer">
        <span style="font-size:17px">⚠️</span>
        <div style="flex:1;min-width:0">
          <div style="font-family:var(--fb);font-size:14px;font-weight:600;color:var(--ink);
            white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(e.title)}</div>
          <div style="font-family:var(--fm);font-size:11px;color:var(--mut)">${esc(e.reason)}</div>
        </div>
        <button class="ghost" data-retry="${esc(e.id)}" style="flex-shrink:0;padding:7px 13px">Retry</button>
        <button class="ghost" data-err-dismiss="${esc(e.id)}" aria-label="Dismiss this error"
          style="flex-shrink:0;padding:7px 11px;color:var(--mut)">Dismiss</button>
      </div>`).join("");
  } catch(e){
    body.innerHTML = `<div class="empty"><div class="empty-i">📡</div>
      <div class="empty-t">Couldn't load errors</div></div>`;
  }
}

// ── Saved from Medha: decide what each capture becomes ───────────────────────
// The agent copies every Save here verbatim and does nothing else; this is the
// only place a capture turns into anything. One suggestion is highlighted --
// a checklist when there are several lines, a note otherwise -- and that is
// all it is: nothing is applied until a choice is tapped.
function svSuggest(it){ return (it.lines||0) >= 2 ? "checklist" : "note"; }
function svCard(it){
  const sug = svSuggest(it), fp = esc(it.fp);
  const chip = (as, label) => `<button class="sv-k${as===sug?" sug":""}" data-sv-as="${as}" data-sv-fp="${fp}">${label}</button>`;
  const when = it.posted_at ? relTime(it.posted_at) : "";
  return `<div class="sv-card" data-sv="${fp}">
    <div class="sv-meta">${esc(it.origin||"A notification")}${when?` · ${esc(when)}`:""}</div>
    <input class="sv-title" value="${esc(it.title||"")}" placeholder="Untitled"
      aria-label="Title, which you can change before choosing" maxlength="200">
    ${it.body ? `<div class="sv-body">${esc(it.body)}</div>` : ""}
    ${it.truncated ? `<div class="sv-cut">Medha kept the first part only — the message was longer.</div>` : ""}
    <div class="sv-kinds" role="group" aria-label="Save it as">
      ${chip("note","Note")}
      ${chip("checklist", it.lines>=2
          ? `Checklist<span class="sv-k-n">${it.lines} items</span>` : "Checklist")}
      <label class="sv-k sv-when">Reminder<input type="date"
        data-sv-date="${fp}" min="${_localISO(new Date())}" aria-label="Remind me on a day you pick"></label>
      ${chip("task","Task")}
    </div>
    <button class="sv-drop" data-sv-as="discard" data-sv-fp="${fp}">Discard</button>
  </div>`;
}
async function showSaved(){
  let sheet = $("svSheet");
  if (!sheet){
    sheet = document.createElement("div");
    sheet.className = "sheet"; sheet.id = "svSheet";
    sheet.innerHTML = `<div class="sh-hd">
      <button class="ibtn" id="svBack" aria-label="Back">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M19 12H5M12 19l-7-7 7-7"/></svg>
      </button><span class="sh-t">Saved from Medha</span></div>
      <div class="sh-sc" id="svBody"><div class="spinner"></div></div>`;
    document.body.appendChild(sheet);
  }
  sheet.classList.add("on");
  const body = $("svBody");
  let r;
  try { r = await api("/saved"); }
  catch(e){
    body.innerHTML = `<div class="empty"><div class="empty-t">Couldn't load your saves</div>
      <div class="empty-b">${esc(apiWhy(e, "Try again in a moment"))}</div></div>`;
    return;
  }
  if (!r.items.length){
    body.innerHTML = `<div class="empty"><div class="empty-i">${svgIcon("note",26)}</div>
      <div class="empty-t">Nothing waiting</div>
      <div class="empty-b">Open a notification on Medha's brief and tap Save. It arrives here exactly as
      it was sent, and you choose what it becomes.</div></div>`;
    return;
  }
  body.innerHTML = r.items.map(svCard).join("");
}
async function svDecide(fp, as, date){
  const card = document.querySelector(`.sv-card[data-sv="${CSS.escape(fp)}"]`);
  const title = card?.querySelector(".sv-title")?.value ?? "";
  card?.classList.add("busy");
  let r;
  try {
    r = await api(`/saved/${encodeURIComponent(fp)}/decide`,
                  {method:"POST", body: date ? {as, title, date} : {as, title}});
  } catch(e){
    card?.classList.remove("busy");
    toast(apiWhy(e, "Couldn't save that"), "err");
    return;
  }
  // The toast names what was made, in the same words as the button, so the
  // person can tell which choice they just made from the toast alone.
  const said = {note:"Saved as a note", checklist:"Saved as a checklist", task:"Saved as a task",
                reminder:"Reminder set for "+fuDay(r.date), discard:"Discarded"}[as];
  toast(said, "ok", {label:"Undo", fn: async ()=>{
    await api(`/saved/${encodeURIComponent(fp)}/undo`, {method:"POST"}).catch(()=>null);
    if ($("svSheet")?.classList.contains("on")) await showSaved();
    if ($("pulseSheet")?.classList.contains("on")) openPulse(true);
  }});
  card?.remove();
  if (!document.querySelector("#svBody .sv-card")) await showSaved();
  if ($("pulseSheet")?.classList.contains("on")) openPulse(true);
}

function renderBriefProse(text){
  const t = String(text||"").trim();
  // Strip a leading "Here is your daily brief:" style preamble — it's noise.
  const cleaned = t.replace(/^here('?s| is) your daily brief[:\-—]?\s*/i, "");
  // Split into sentences, then group ~2 per paragraph for readability.
  const sentences = cleaned.match(/[^.!?]+[.!?]+(\s|$)|[^.!?]+$/g) || [cleaned];
  const paras = [];
  for (let i=0;i<sentences.length;i+=2){
    paras.push(sentences.slice(i,i+2).join("").trim());
  }
  // Highlight an action sentence (contains prioritize/review/remember/need)
  return paras.map(p=>{
    const isFocus = /\b(prioritize|remember to|please review|needs? attention|don'?t forget)\b/i.test(p);
    if (isFocus) return `<div style="display:flex;gap:9px;align-items:flex-start;
      background:rgba(108,92,231,.07);border-left:3px solid var(--violet);border-radius:8px;
      padding:10px 12px;margin:4px 0">
      <span style="font-size:14px;line-height:1.4">🎯</span>
      <div style="font-family:var(--fb);font-size:13.5px;line-height:1.6;color:var(--ink)">${esc(p)}</div></div>`;
    return `<div style="font-family:var(--fb);font-size:14.5px;line-height:1.7;color:var(--ink);
      margin-bottom:10px">${esc(p)}</div>`;
  }).join("");
}
async function loadBrief(){
  const slot = $("briefSlot"); if (!slot) return;
  try {
    const r = await api("/brief");
    const b = r.brief;
    if (!b || (!b.text && !b.greeting && !(b.bullets||[]).length)){
      slot.innerHTML = `<div style="border-radius:16px;padding:16px 18px;margin-bottom:4px;
        background:var(--prism-soft);border:1px dashed var(--line2);text-align:center">
        <div class="empty-ic">${svgIcon("note",26)}</div>
        <div style="font-family:var(--fb);font-size:14px;color:var(--ink2);line-height:1.6;margin-bottom:10px">
          Your daily brief appears here once the agent has notes to reflect on.</div>
        <button id="briefGen" class="ghost" style="background:var(--bg)">${svgIcon("sparkle",15)} Generate now</button>
      </div>`;
      return;
    }
    const when = b.updated_at ? relTime(b.updated_at) : "";
    let body = "";
    if (b.greeting || b.focus || (b.bullets||[]).length){
      if (b.greeting) body += `<div style="font-family:var(--fb);font-size:15px;line-height:1.6;
        color:var(--ink);margin-bottom:${b.focus||b.bullets?.length?"12px":"0"}">${esc(b.greeting)}</div>`;
      if (b.focus) body += `<div style="display:flex;gap:9px;align-items:flex-start;
        background:rgba(108,92,231,.07);border-left:3px solid var(--violet);
        border-radius:8px;padding:10px 12px;margin-bottom:${b.bullets?.length?"12px":"0"}">
        <span style="font-size:14px;line-height:1.4">🎯</span>
        <div style="font-family:var(--fb);font-size:13.5px;line-height:1.55;color:var(--ink)">${esc(b.focus)}</div></div>`;
      if ((b.bullets||[]).length) body += `<div style="display:flex;flex-direction:column;gap:7px">`
        + b.bullets.map(x=>`<div style="display:flex;gap:8px;align-items:flex-start">
          <span style="color:var(--violet);font-weight:800;line-height:1.5">·</span>
          <span style="font-family:var(--fb);font-size:13.5px;line-height:1.55;color:var(--ink2)">${esc(x)}</span></div>`).join("")
        + `</div>`;
    } else {
      body = renderBriefProse(b.text);
    }
    slot.innerHTML = `<div style="border-radius:16px;padding:16px 18px;margin-bottom:4px;
      background:var(--prism-soft, #f3f0fc);border:1px solid var(--line)">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:12px">
        <span class="drow-ic">${svgIcon("note",18)}</span>
        <span style="font-family:var(--fm);font-size:11px;font-weight:800;letter-spacing:.06em;
          text-transform:uppercase;color:var(--violet);flex:1">Your daily brief</span>
        <span style="font-family:var(--fm);font-size:11px;color:var(--mut);margin-right:8px;flex-shrink:0">${when}</span>
        <button id="briefGen" class="brief-refresh" aria-label="Refresh brief">↻</button>
      </div>
      ${body}
    </div>`;
  } catch(e){ slot.innerHTML = ""; }
}
// Uniform calendar date display. A resolved timestamp → "Jul 11, 2026 · 12:05 PM"
// (or date-only when there's no time). An unresolved hint ("next Monday") →
// shown as-is but capitalized, so the list reads consistently.
// Resolve an event to a real Date when possible — from a stored timestamp OR by
// parsing a relative hint ("today", "tomorrow at 11am", "next Monday", "July 15").
// Mirrors the agent's resolver so the dashboard shows sortable, uniform dates
// even when the DB only kept the fuzzy hint. Returns {date, hasTime} or null.
function resolveEventDate(ev){
  const r = (ev.datetime_resolved||"").trim();
  if (r){
    const iso = r.includes("T") ? r : r.replace(" ", "T");
    const d = new Date(iso);
    if (!isNaN(d)) return {date:d, hasTime:/\d{1,2}:\d{2}/.test(r)};
  }
  const h = (ev.datetime_hint||"").trim().toLowerCase();
  if (!h) return null;
  const now = new Date();
  const base = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const WD = {sunday:0,monday:1,tuesday:2,wednesday:3,thursday:4,friday:5,saturday:6};
  let hh=null, mm=0;
  let tm = h.match(/(\d{1,2}):(\d{2})\s*(am|pm)?/) || h.match(/(\d{1,2})\s*(am|pm)/);
  if (tm){
    hh = parseInt(tm[1],10); mm = tm[2] && /^\d+$/.test(tm[2]) ? parseInt(tm[2],10) : 0;
    const ap = (h.match(/(am|pm)/)||[])[1];
    if (ap==="pm" && hh<12) hh+=12; if (ap==="am" && hh===12) hh=0;
  } else if (/\bevening\b|\btonight\b/.test(h)) hh=19;
  else if (/\bafternoon\b/.test(h)) hh=14;
  else if (/\bmorning\b/.test(h)) hh=9;
  else if (/\bnoon\b/.test(h)) hh=12;
  else if (/\bmidnight\b/.test(h)) hh=0;
  const setT = d => { if (hh!==null){ d.setHours(hh,mm,0,0); return true; } return false; };

  let d = null;
  if (/^today\b|^tonight\b/.test(h)) d = new Date(base);
  else if (/^tomorrow\b/.test(h)){ d=new Date(base); d.setDate(d.getDate()+1); }
  else if (/^yesterday\b/.test(h)){ d=new Date(base); d.setDate(d.getDate()-1); }
  else {
    let m = h.match(/^(next|this)\s+(\w+)/);
    if (m && WD[m[2]]!==undefined){
      let ahead=(WD[m[2]]-base.getDay()+7)%7; if(ahead===0)ahead=7;
      d=new Date(base); d.setDate(d.getDate()+ahead);
    } else {
      const fw=(h.split(/[\s,]/)[0]||"");
      if (WD[fw]!==undefined){ let ahead=(WD[fw]-base.getDay()+7)%7; if(ahead===0)ahead=7; d=new Date(base); d.setDate(d.getDate()+ahead); }
      else {
        const dm = h.match(/([a-z]{3,9})\s+(\d{1,2})/);
        if (dm){
          const parsed = new Date(`${dm[1]} ${dm[2]}, ${now.getFullYear()}`);
          if (!isNaN(parsed)) d=parsed;
        } else {
          const p=new Date(h); if(!isNaN(p)) d=p;
        }
      }
    }
  }
  if (!d) return null;
  const hasTime = setT(d);
  return {date:d, hasTime};
}
function fmtEventDate(ev){
  const res = resolveEventDate(ev);
  if (res){
    const dateStr = res.date.toLocaleDateString(undefined,{month:"short",day:"numeric",year:"numeric"});
    if (res.hasTime){
      const timeStr = res.date.toLocaleTimeString(undefined,{hour:"numeric",minute:"2-digit"});
      return `${dateStr} · ${timeStr}`;
    }
    return dateStr;
  }
  const h = (ev.datetime_hint||"").trim();
  return h ? h.charAt(0).toUpperCase()+h.slice(1) : "No date set";
}
// One calendar row.
function calRow(ev){
  return `<div class="cal-item" data-cal-note="${esc(ev.note_id)}" style="cursor:pointer;border:none;margin-bottom:0;background:none;border-bottom:1px solid var(--line);border-radius:0">
      <span class="cal-i">${svgIcon("calendar",17)}</span>
      <div style="flex:1"><div class="cal-t">${esc(ev.title)}</div>
        <div class="cal-when">${esc(fmtEventDate(ev))}</div></div>
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--mut)" stroke-width="2"><path d="M9 18l6-6-6-6"/></svg>
    </div>`;
}
async function loadPulseCalendar(){
  const slot = $("pulseCal"); if (!slot) return;
  try {
    let evs = (await api("/calendar-events")).events;
    if (!evs.length){
      slot.innerHTML = `<div style="font-family:var(--fm);font-size:12px;color:var(--mut);padding:10px 4px">No calendar events yet. Approve a note with a date and the agent creates one.</div>`;
      return;
    }
    const today = new Date(); today.setHours(0,0,0,0);
    const tomorrow = new Date(today); tomorrow.setDate(tomorrow.getDate()+1);
    // Resolve, drop past (by date so a today-5pm task survives past 5pm), then
    // bucket into Today / Tomorrow / Upcoming / Someday (undated).
    const buckets = { today:[], tomorrow:[], upcoming:[], someday:[] };
    evs.map(ev=>({ev, r:resolveEventDate(ev)}))
      .filter(x=>{ if(!x.r) return true; const d=new Date(x.r.date); d.setHours(0,0,0,0); return d>=today; })
      .forEach(x=>{
        if (!x.r){ buckets.someday.push(x); return; }
        const d = new Date(x.r.date); d.setHours(0,0,0,0);
        if (d.getTime()===today.getTime()) buckets.today.push(x);
        else if (d.getTime()===tomorrow.getTime()) buckets.tomorrow.push(x);
        else buckets.upcoming.push(x);
      });
    // Ascending within each dated group (soonest next).
    const asc = (a,b)=> a.r.date - b.r.date;
    buckets.today.sort(asc); buckets.tomorrow.sort(asc); buckets.upcoming.sort(asc);

    const total = buckets.today.length + buckets.tomorrow.length
                + buckets.upcoming.length + buckets.someday.length;
    if (!total){
      slot.innerHTML = `<div style="font-family:var(--fm);font-size:12px;color:var(--mut);padding:10px 4px">No upcoming events. Approve a note with a future date and the agent creates one.</div>`;
      return;
    }
    // Render only non-empty groups, each with a quiet header.
    const section = (label, items) => items.length
      ? `<div class="cal-grp-h">${label}</div><div class="dcard" style="padding:8px 12px;margin-bottom:12px">`
        + items.map(x=>calRow(x.ev)).join("") + `</div>`
      : "";
    slot.innerHTML =
        section("Today", buckets.today)
      + section("Tomorrow", buckets.tomorrow)
      + section("Upcoming", buckets.upcoming)
      + section("Someday", buckets.someday)
      + `<div style="font-family:var(--fm);font-size:11px;color:var(--mut);padding:2px 4px 4px">Tap to open the note · manage in Google Calendar</div>`;
  } catch(e){ slot.innerHTML = ""; }
}
function dRow(icon, label, val, flag, nav){
  const tappable = nav ? ` data-pulse-nav="${nav}" role="button" tabindex="0" style="cursor:pointer"` : "";
  const chev = nav ? '<svg class="drow-chev" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M9 18l6-6-6-6"/></svg>' : "";
  return `<div class="drow"${tappable}><div class="drow-i${flag?" warn":""}">${svgIcon(icon,17)}</div>
    <div class="drow-t">${label}</div><div class="drow-v${flag?" warn":""}">${val}</div>${chev}</div>`;
}
