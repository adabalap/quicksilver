/* ═════════ Home strips ═════════
   Three things the Notes home can say, only when they have something to say:
   the agent's state, what it did while you were away, and one old note worth
   another look. All come from one cheap read-only call (/api/home). "Away" is
   per device: localStorage holds the last time this app was active. */
const LV_KEY = "hg_last_active", WN_KEY = "hg_wn_dismissed", AWAY_MS = 10*60*1000;
function lsGet(k){ try { return localStorage.getItem(k)||""; } catch(e){ return ""; } }
function lsSet(k,v){ try { localStorage.setItem(k,v); } catch(e){} }
function stampActive(){ lsSet(LV_KEY, new Date().toISOString()); }
(function initAway(){
  const prev = lsGet(LV_KEY), t = Date.parse(prev);
  S.homeSince = (t && Date.now()-t >= AWAY_MS) ? prev : "";
  S.wnDismissed = !!S.homeSince && lsGet(WN_KEY) === S.homeSince;
  stampActive();
  setInterval(()=>{ if (!document.hidden) stampActive(); }, 60000);
  setInterval(()=>{ if (!document.hidden) loadHome(); }, 30000);
  document.addEventListener("visibilitychange", ()=>{
    if (document.hidden){ stampActive(); S.hiddenAt = Date.now(); return; }
    if (S.hiddenAt && Date.now()-S.hiddenAt >= AWAY_MS){
      S.homeSince = lsGet(LV_KEY); S.wnDismissed = lsGet(WN_KEY) === S.homeSince; }
    S.hiddenAt = 0; loadHome(true); stampActive();
  });
  window.addEventListener("pagehide", stampActive);
})();

async function loadHome(force){
  if (!NET.online) { S.home = null; renderHomeStrips(); return; }
  if (!force && Date.now()-S.homeAt < 3000) return;          // refreshAll fires in bursts
  S.homeAt = Date.now();
  try { S.home = await api("/home" + (S.homeSince ? "?since="+encodeURIComponent(S.homeSince) : "")); }
  catch(e){ S.home = null; }
  renderHomeStrips();
  billNotices();
}
function renderHomeStrips(){
  const el = $("homeStrips"); if (!el) return;
  const h = S.home;
  if (!h || S.askMode || S.q || S.listView || S.filter!=="all" || filOn()){ morph(el, ""); return; }
  let out = "";
  // 1 ─ agent status (silent when idle)
  const a = h.agent || {};
  const plural = (n,w)=>`${n} ${w}${n===1?"":"s"}`;
  const nNotes = (a.processing||0)+(a.waiting||0);
  let msg = "", cls = "";
  if (a.state==="working"){
    msg = a.asks_pending && !nNotes ? "Answering your question…"
        : nNotes ? `Working on ${plural(nNotes,"note")}…` : "Working…"; cls = "working"; }
  else if (a.state==="cooling"){
    msg = `Phone is warm — pacing itself${nNotes?`, ${plural(nNotes,"note")} waiting`:""}`; cls = "cooling"; }
  else if (a.state==="offline"){
    msg = `Agent offline${nNotes?` · ${plural(nNotes,"note")} waiting`:""} — saved, and processed when it's back`; cls = "offline"; }
  if (!msg && a.alive && a.media > 0){
    msg = `Reading ${a.media} photo${a.media===1?"":"s"} / recording${a.media===1?"":"s"}…`; cls = "working"; }
  if (!msg && a.alive && a.index && a.index.done < a.index.total){
    msg = `Getting your notes ready for search · ${a.index.done}/${a.index.total}`; cls = "working"; }
  if (msg) out += `<div class="hs hs-agent ${cls}"><div class="hs-row"><span class="hs-dot"></span><span>${esc(msg)}</span></div></div>`;
  const jb = a.jobs || {};
  if ((jb.dead||0) > 0){
    const what = jb.dead_notes && !jb.dead_asks ? plural(jb.dead_notes,"note")
               : !jb.dead_notes && jb.dead_asks ? plural(jb.dead_asks,"question") : plural(jb.dead,"item");
    out += `<div class="hs hs-agent stalled"><div class="hs-row"><span class="hs-dot"></span>
      <span>${what} couldn't be processed after several tries.</span>
      <button class="hs-link" data-hs="retry-jobs">Retry</button></div></div>`;
  }
  if ((a.stalled||0) > 0) out += `<div class="hs hs-agent stalled"><div class="hs-row"><span class="hs-dot"></span>
      <span>${plural(a.stalled,"note")} didn't finish processing.</span>
      <button class="hs-link" data-hs="open" data-id="${esc((a.stalled_ids||[])[0]||"")}">Review</button></div></div>`;
  // 1b ─ bills due soon (silent when nothing is due within a week, and hideable for the day)
  const bl = h.bills;
  if (bl && (bl.items||[]).length && lsGet("hg_bills_hide") !== new Date().toISOString().slice(0,10)){
    out += `<div class="hs hs-bills"><button class="hs-x" data-hs="bills-hide" aria-label="Hide for today">✕</button><div class="hs-t">🧾 Bills due</div>${bl.items.map(b=>`<div class="hs-bill${b.overdue?" late":""}">
      <button class="hs-bl-m" data-hs="bill-open" data-id="${b.id}"><b>${esc(b.merchant)}</b><span>${esc(inrFmt(b.amount))} · ${esc(dueWords(b))}</span></button>
      <button class="hs-link" data-hs="bill-open" data-id="${b.id}" aria-label="Open">Details ›</button></div>`).join("")}
      <div class="hs-stats"><button class="hs-link" data-hs="bills-all">${bl.more?`+${bl.more} more · `:""}All bills ›</button></div></div>`;
  }
  // 2 ─ while you were away
  const w = h.whatsnew;
  if (w && !S.wnDismissed && (w.enriched||w.tasks||w.reminders)){
    const bits = [];
    if (w.enriched)  bits.push(`<span>✨ <b>${w.enriched}</b> ${w.enriched===1?"note":"notes"} organised</span>`);
    if (w.tasks)     bits.push(`<button class="hs-link" data-hs="go" data-go="vActs">☑ <b>${w.tasks}</b> new ${w.tasks===1?"task":"tasks"}</button>`);
    if (w.reminders) bits.push(`<button class="hs-link" data-hs="go" data-go="vReminders">🔔 <b>${w.reminders}</b> ${w.reminders===1?"reminder":"reminders"}</button>`);
    out += `<div class="hs hs-new"><button class="hs-x" data-hs="dismiss-new" aria-label="Dismiss">✕</button>
      <div class="hs-t">While you were away</div><div class="hs-stats">${bits.join("")}</div>
      ${(w.items||[]).length?`<div class="hs-chips">${w.items.map(i=>
        `<button class="hs-chip" data-hs="open" data-id="${esc(i.id)}">${esc(i.title)}</button>`).join("")}</div>`:""}</div>`;
  }
  if (h.digest) out += `<div class="hs hs-new"><button class="hs-x" data-hs="dismiss-dg" aria-label="Dismiss">✕</button>
      <div class="hs-t">🗓 Your week</div><div class="hs-rs-s" style="-webkit-line-clamp:3">${esc(h.digest.text||"")}</div>
      <div class="hs-stats" style="margin-top:8px"><button class="hs-link" data-hs="open-dg">Read your digest</button></div></div>`;
  if (h.dups > 0) out += `<div class="hs hs-agent"><div class="hs-row"><span>⧉ ${plural(h.dups,"possible duplicate")}</span>
      <button class="hs-link" data-hs="open-dups">Review</button></div></div>`;
  // 3 ─ resurfaced note
  // Keep the home calm: a third strip only appears when it's the only thing competing.
  const r = (out.match(/class="hs /g)||[]).length >= 2 ? null : h.resurface;
  if (r) out += `<div class="hs hs-rs" data-hs="open" data-id="${esc(r.id)}">
      <button class="hs-x" data-hs="dismiss-rs" data-id="${esc(r.id)}" aria-label="Not now">✕</button>
      <div class="hs-rs-l">${esc(r.label)}</div><div class="hs-rs-t">${esc(r.title)}</div>
      <div class="hs-rs-s">${esc(dedupeLead(r.title, r.snippet))}</div>
      <div class="hs-rs-f"><span>${esc(askDate(r.created_at))}</span>${r.open_tasks?`<span>☑ ${r.open_tasks} still open</span>`:""}
        <button class="hs-link" data-hs="dismiss-rs" data-id="${esc(r.id)}">Not now</button></div></div>`;
  morph(el, out);
}
async function homeAction(btn){
  const act = btn.dataset.hs;
  if (act==="bills-hide"){ lsSet("hg_bills_hide", new Date().toISOString().slice(0,10)); renderHomeStrips(); toast("Hidden for today","ok"); return; }
  if (act==="bills-all"){ closeOverlays(); openBillsList(); return; }
  if (act==="bill-open"){ openBill(btn.dataset.id); return; }
  if (act==="open"){ if (btn.dataset.id) await openDetail(btn.dataset.id); return; }
  if (act==="go"){ switchView(btn.dataset.go); return; }
  if (act==="retry-jobs"){
    btn.disabled = true; btn.textContent = "Retrying…";
    try { await api("/agent/jobs/retry", {method:"POST"}); } catch(e){}
    loadHome(true); return; }
  if (act==="open-dg"){ openDigest(); return; }
  if (act==="open-dups"){ openDups(); return; }
  if (act==="dismiss-dg"){
    if (S.home) S.home.digest = null; renderHomeStrips();
    try { await api("/digest/dismiss", {method:"POST"}); } catch(e){}
    return; }
  if (act==="dismiss-new"){ S.wnDismissed = true; lsSet(WN_KEY, S.homeSince); renderHomeStrips(); return; }
  if (act==="dismiss-rs"){
    if (S.home) S.home.resurface = null; renderHomeStrips();
    try { await api("/resurface/dismiss", {method:"POST", body:{id:btn.dataset.id}}); } catch(e){}
    return; }
}

/* ═════════ Bills ═════════
   The agent turns bill messages into reminders (and Google Calendar notifies you when the app is closed). These
   helpers add the in-app side: a one-line "due" wording, a once-a-day notification while the app is open, and the
   Settings status line with Check now / Forget everything. */
const inrFmt = v => { v = Number(v)||0; const f = Math.abs(v-Math.round(v))<.005 ? 0 : 2;
  return "₹" + v.toLocaleString("en-IN",{minimumFractionDigits:f,maximumFractionDigits:f}); };
function dueWords(b){
  if (b.days < 0) return b.days === -1 ? "was due yesterday" : `${-b.days} days overdue`;
  if (b.days === 0) return "due today";
  if (b.days === 1) return "due tomorrow";
  return `due in ${b.days} days`;
}
function billNotices(){
  const items = ((S.home||{}).bills||{}).items || [];
  const day = new Date().toISOString().slice(0,10);
  items.filter(b=>b.days >= 0 && b.days <= 2).forEach(b=>{
    const k = `hg_bn_${b.id}_${day}`;
    if (lsGet(k)) return;
    lsSet(k, "1");
    notify(`${b.merchant} ${inrFmt(b.amount)} ${dueWords(b)}`, "Tap to see your reminders.", {tag:"hg-bill-"+b.id});
  });
}
const SMS_STATE = {
  ok:"Reading your messages", off:"Switched off",
  no_termux_api:"Termux:API isn't available here. If the agent runs inside Fedora, use the SMS bridge (see the README).",
  no_bridge:"The SMS bridge isn't running. Start bin/sms-bridge.sh in Termux.",
  no_permission:"SMS permission is missing. Allow it for the Termux:API app in Android settings.",
  error:"Couldn't read messages."};
let _bsT = null;
function billsStatusSoon(){ clearTimeout(_bsT); _bsT = setTimeout(loadBillsStatus, 50); }
async function loadBillsStatus(){
  const el = $("billsStatus"); if (!el) return;
  let d; try { d = await api("/bills"); } catch(e){ el.textContent = ""; return; }
  if (!$("billsStatus")) return;
  if (!d.enabled){ el.textContent = ""; return; }
  const st = d.status || {};
  let t = st.state ? (SMS_STATE[st.state] || "") : "Waiting for the first read…";
  if (st.detail && st.state && st.state!=="ok" && st.state!=="off") t += " " + st.detail;
  if (st.state==="ok") t += ` · ${(d.bills||[]).length} open bill${(d.bills||[]).length===1?"":"s"} this month`;
  el.textContent = t;
}
async function billsScan(btn){
  btn.disabled = true;
  try { await api("/sms/scan", {method:"POST"}); toast("Checking messages…","ok"); }
  catch(e){ toast("Couldn't start a check","err"); }
  setTimeout(()=>{ btn.disabled = false; billsStatusSoon(); }, 6000);
}
let _bfT = null;
async function billsForget(btn){
  if (!btn.dataset.sure){
    btn.dataset.sure = "1"; btn.textContent = "Tap again to confirm";
    clearTimeout(_bfT); _bfT = setTimeout(()=>{ btn.dataset.sure = ""; btn.textContent = "Forget everything"; }, 4000); return; }
  try { await api("/sms/forget", {method:"POST"}); toast("Forgotten","ok"); loadHome(true); billsStatusSoon(); }
  catch(e){ toast("Couldn't forget","err"); }
}

/* ── Medha's notification stream: the Settings status line and its buttons ──
   The agent polls Medha and writes the `captures` table; this side only
   reports. The state that matters most is `not_listening`: an empty stream
   and a phone that has stopped recording look identical, and an app that
   cannot tell them apart tells you your day is clear when it has gone deaf. */
const CAP_STATE = {
  ok: "Reading your notifications",
  off: "Switched off",
  not_listening: "Medha is not reading notifications right now.",
  no_capability: "Medha has not been given permission.",
  no_token: "Medha's address or token is missing.",
  unsupported: "This version of Medha is too old for this.",
  error: "Couldn't read the stream."};
let _csT = null;
function capturesStatusSoon(){ clearTimeout(_csT); _csT = setTimeout(loadCapturesStatus, 50); }
async function loadCapturesStatus(){
  const el = $("capturesStatus"); if (!el) return;
  let d; try { d = await api("/captures"); } catch(e){ el.textContent = ""; return; }
  if (!$("capturesStatus")) return;
  if (!d.enabled){ el.textContent = ""; return; }
  const st = d.status || {};
  let t = st.state ? (CAP_STATE[st.state] || "") : "Waiting for the first read…";
  if (st.detail && st.state !== "ok" && st.state !== "off") t += " " + st.detail;
  if (st.state === "ok"){
    t += ` · ${d.total||0} kept, ${d.retainDays||21} days`;
    const apps = (d.apps||[]).slice(0,3).map(a=>a.label).join(", ");
    if (apps) t += ` · mostly ${apps}`;
  }
  el.textContent = t;
}
async function capturesScan(btn){
  btn.disabled = true;
  try { await api("/captures/scan", {method:"POST"}); toast("Checking…","ok"); }
  catch(e){ toast("Couldn't start a check","err"); }
  setTimeout(()=>{ btn.disabled = false; capturesStatusSoon(); }, 6000);
}
// The sample exists because a sentence about what is collected is a claim, and
// this is the one source reading data the user did not hand over. Twenty rows,
// newest first, so the claim can be checked rather than believed.
async function capturesSample(){
  let d; try { d = await api("/captures?sample=1"); } catch(e){ toast("Couldn't load","err"); return; }
  const rows = d.recent || [];
  // Reuses the bill sheet rather than introducing a second overlay: it is
  // already a titled scrolling panel with the app's own styling, and a new
  // one would be a new thing to keep in step with the theme.
  $("billTitle").textContent = "What was kept";
  $("billBody").innerHTML = (rows.length
    ? `<div class="sect">Newest first · ${rows.length}</div>` + rows.map(r=>
        `<div class="bill-li"><div class="bill-li-m"><b>${esc(r.title || r.app || "")}</b>`
        + `<span>${esc(r.app||"")}${r.text ? " · " + esc(r.text) : ""}</span></div></div>`).join("")
    : `<div class="aoff">Nothing kept yet.</div>`)
    + `<div class="set-d">Kept for ${d.retainDays||21} days, then deleted. `
    + `Medha keeps its own copy for its own window — turning off `
    + `"Read your notifications" in the Medha app is what deletes that.</div>`;
  $("billSheet").classList.add("on");
}
let _cfT = null;
async function capturesForget(btn){
  if (!btn.dataset.sure){
    btn.dataset.sure = "1"; btn.textContent = "Tap again to confirm";
    clearTimeout(_cfT); _cfT = setTimeout(()=>{ btn.dataset.sure = ""; btn.textContent = "Forget everything"; }, 4000); return; }
  try {
    const d = await api("/captures/forget", {method:"POST"});
    toast(`Forgot ${d.deleted||0}`,"ok");
    btn.dataset.sure = ""; btn.textContent = "Forget everything";
    capturesStatusSoon();
  } catch(e){ toast("Couldn't forget","err"); }
}

/* ── bill detail: the original message, its links, and Paid / Not a bill ── */
async function openBill(id){
  let d; try { d = await api("/bills?all=1"); } catch(e){ toast("Couldn't load the bill","err"); return; }
  const b = (d.bills||[]).find(x=>String(x.id)===String(id));
  if (!b){ toast("That bill is no longer open","ok"); loadHome(true); return; }
  closeOverlays();
  $("billTitle").textContent = "Bill";
  $("billBody").innerHTML = `<div class="bill-top"><div class="bill-m">${esc(b.merchant)}</div>
    <div class="bill-a">${esc(inrFmt(b.amount))}</div>
    <div class="bill-d${b.overdue?" late":""}">${esc(dueWords(b))} · ${esc(new Date(b.due_date+"T12:00:00").toLocaleDateString(undefined,{weekday:"short",day:"numeric",month:"short"}))}</div></div>
    ${b.body?`<div class="sect">The message</div><div class="rmsg bill-msg">${linkText(b.body)}</div>`
            :`<div class="aoff">The original message isn't kept for this bill.</div>`}
    <div class="bill-btns"><button class="set-mini-b" data-bill-act="paid" data-id="${b.id}">Mark as paid</button>
      <button class="set-mini-b ghosty" data-bill-act="dismiss" data-id="${b.id}">Not a bill</button></div>
    <div class="set-note">Marking it paid, or "Not a bill", deletes the message text and its reminders.</div>`;
  $("billSheet").classList.add("on");
}
async function billAct(btn){
  btn.disabled = true;
  try { await api("/bills/"+btn.dataset.id+"/"+btn.dataset.billAct, {method:"POST"}); }
  catch(e){ toast("Couldn't update","err"); btn.disabled = false; return; }
  $("billSheet").classList.remove("on");
  toast(btn.dataset.billAct==="paid" ? "Marked as paid" : "Removed","ok");
  loadHome(true); if (S.view==="vReminders") loadReminders();
  if (btn.dataset.from==="list") openBillsList();
}

/* ── all open bills: act on each, or add one by hand ── */
async function openBillsList(){
  let d; try { d = await api("/bills?all=1"); } catch(e){ toast("Couldn't load bills","err"); return; }
  const bs = d.bills || [];
  const due = new Date(Date.now()+7*864e5).toISOString().slice(0,10);
  $("billTitle").textContent = "Bills";
  $("billBody").innerHTML = `<form class="bill-add" id="billAddForm" autocomplete="off">
      <div class="bill-add-t">Add a bill</div>
      <input class="bill-in" name="merchant" placeholder="Who is it for? (e.g. Electricity)" maxlength="60" required>
      <div class="bill-row"><input class="bill-in" name="amount" type="number" inputmode="decimal" min="0" step="0.01" placeholder="Amount ₹" required>
      <input class="bill-in" name="due_date" type="date" value="${due}" required></div>
      <button class="set-mini-b" type="submit">Add and remind me</button></form>
    <div class="sect">Open${bs.length?` · ${bs.length}`:""}</div>
    ${bs.length ? bs.map(b=>`<div class="bill-li${b.overdue?" late":""}">
      <button class="bill-li-m" data-hs="bill-open" data-id="${b.id}"><b>${esc(b.merchant)}${b.manual?' <i class="bill-tag">added by you</i>':""}</b>
        <span>${esc(inrFmt(b.amount))} · ${esc(dueWords(b))}</span></button>
      <button class="set-mini-b" data-bill-act="paid" data-id="${b.id}" data-from="list">Paid</button>
      <button class="set-mini-b ghosty" data-bill-act="dismiss" data-id="${b.id}" data-from="list" aria-label="Not a bill">✕</button></div>`).join("")
      : `<div class="aoff">${d.enabled?"No open bills.":"Nothing here yet. Add one above, or switch on bill reading in Settings."}</div>`}`;
  $("billSheet").classList.add("on");
}
async function billAdd(form){
  const f = new FormData(form), body = Object.fromEntries(f.entries());
  const btn = form.querySelector("button[type=submit]"); btn.disabled = true;
  try { await api("/bills", {method:"POST", body}); }
  catch(e){ toast("Couldn't add the bill","err"); btn.disabled = false; return; }
  toast("Bill added","ok"); loadHome(true); openBillsList();
}
