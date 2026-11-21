/* ═════════ Event delegation ═════════ */
document.addEventListener("submit", e=>{
  if (e.target && e.target.id==="billAddForm"){ e.preventDefault(); billAdd(e.target); return; }
  if (e.target && e.target.id==="askFollow"){
    e.preventDefault();
    const v = ($("askFollowIn").value||"").trim();
    if (!v) return;
    $("askFollowIn").value = "";
    askQuestion(v, true);
  }
});
document.addEventListener("click", async e=>{
  const t = e.target;
  const sg = t.closest && t.closest("[data-sugg]");
  if (sg){ askQuestion(sg.dataset.sugg, true); return; }
  // Modal buttons (in-app confirm/prompt) — handle before anything else
  if (t.closest("#modalOk")){
    const ml = $("modalLayer");
    const val = $("modalInput").style.display!=="none" ? $("modalInput").value
              : $("modalArea").style.display!=="none" ? $("modalArea").value
              : true;
    _closeModal(val); return;
  }
  if (t.closest("#modalCancel") || t.closest("#modalScrim")){ _closeModal(null); return; }
  // Copy button lives INSIDE the tap-to-edit note surface — handle it first
  // and stop here, or the tap bubbles to #noteRead and opens the editor.
  const cc = t.closest(".codecopy[data-code]");
  if (cc){
    e.stopPropagation();
    try { await navigator.clipboard.writeText(cc.dataset.code);
      const orig = cc.textContent; cc.textContent = "✓ Copied";
      setTimeout(()=>{ cc.textContent = orig; }, 1400);
    } catch(err){ toast("Copy failed","err"); }
    return; }
  if (t.closest("#burgerBtn")){
    // Desktop: the menu is a PERSISTENT rail, so the burger collapses/expands
    // it. Mobile/PWA: unchanged — it opens the drawer overlay.
    if (_isWide()){
      document.body.classList.toggle("rail-off");
      if (!document.body.classList.contains("rail-off")) loadMenuTags();
    } else {
      $("menuLayer").classList.add("on"); loadMenuTags();
    }
    return; }
  if (t.closest("#evSave")){ await saveQuickEvent(); return; }
  if (t.closest("#evCancel") || t.closest("#eventScrim")){ $("eventModal").classList.remove("on"); return; }
  if (t.closest("#settingsItem")){ await leaveEditorIfOpen(); await openSettings(); return; }
  if (t.closest("#setBack")){ $("setSheet").classList.remove("on"); return; }
  // Theme switch. Applied immediately (no reload) and persisted locally — it's
  // a display preference, so it belongs on the device rather than the server.
  const thb = t.closest("[data-theme-set]");
  if (thb){
    const v = thb.dataset.themeSet;
    try { localStorage.setItem("hg_theme", v); } catch(e){}
    applyTheme(v);
    renderSettings();                    // reflect the new selection
    return; }
  // Note-colour theme — same immediate-apply, locally-persisted pattern.
  const ntb = t.closest("[data-note-theme-set]");
  if (ntb){ setNoteTheme(ntb.dataset.noteThemeSet); return; }
  // Connection mode — switching reloads the page against the new API_BASE.
  const cnb = t.closest("[data-conn-set]");
  if (cnb){ setConnMode(cnb.dataset.connSet, getLocalPort()); return; }
  if (t.closest("#localPortApply")){
    const v = $("localPortIn")?.value; setConnMode("local", v); return; }
  const setb = t.closest("[data-setk]");
  if (setb){ await saveSetting(setb.dataset.setk, setb.dataset.setv); return; }
  if (t.closest("#notifyOn")){
    const res = await requestNotifyPermission();   // must come from this gesture
    renderSettings();
    toast(res==="granted" ? "🔔 Notifications on"
        : res==="denied"  ? "Notifications blocked — re-allow them in browser settings"
        : "Notifications not enabled", res==="granted"?"ok":"err");
    return; }
  if (t.closest("#notifyTest")){
    await notify("🔔 Quicksilver", "Notifications are working.", {tag:"hg-test"});
    toast("Test sent","ok");
    return; }
  if (t.closest("#pulseItem")){ await leaveEditorIfOpen(); await openPulse(); return; }
  if (t.closest("#hlItem")){ await leaveEditorIfOpen(); await openHighlights(); return; }
  if (t.closest("#hlBack")){ $("hlSheet").classList.remove("on"); return; }
  const hlGo = t.closest("[data-hl-note]");
  if (hlGo){ $("hlSheet").classList.remove("on"); closeMenu(); await openDetail(hlGo.dataset.hlNote); return; }
  if (t.closest("#dailyItem")){ await openDailyNote(); return; }
  if (t.closest("#tplItem")){ await openTemplates(); return; }
  if (t.closest("#tplNew")){ await newTemplate(); return; }
  const tplUse = t.closest("[data-tpl-use]");
  if (tplUse){ await useTemplate(tplUse.dataset.tplUse); return; }
  const tplDel = t.closest("[data-tpl-del]");
  if (tplDel){ await deleteTemplate(tplDel.dataset.tplDel); return; }
  if (t.closest("#tplBack")){ $("tplSheet").classList.remove("on"); return; }
  if (t.closest("#pulseBack")){ $("pulseSheet").classList.remove("on"); return; }
  const fuB = t.closest("[data-fu-act]");
  if (fuB){ await fuAct(fuB.dataset.fuAct, parseInt(fuB.dataset.fuId, 10)); return; }
  if (t.closest("#billBack")){ $("billSheet").classList.remove("on"); return; }
  if (t.closest("[data-bill-act]")){ await billAct(t.closest("[data-bill-act]")); return; }
  if (t.closest("#billsList")){ await openBillsList(); return; }
  if (t.closest("#billsScan")){ await billsScan(t.closest("#billsScan")); return; }
  if (t.closest("#billsForget")){ await billsForget(t.closest("#billsForget")); return; }
  if (t.closest("#capturesScan")){ await capturesScan(t.closest("#capturesScan")); return; }
  if (t.closest("#capturesSample")){ await capturesSample(); return; }
  if (t.closest("#capturesForget")){ await capturesForget(t.closest("#capturesForget")); return; }
  if (t.closest("#tourBtn")){ $("setSheet").classList.remove("on"); openTour(); return; }
  if (t.closest("#fuCheck")){
    try { await api("/followups/refresh",{method:"POST"}); toast("Checking your tasks — look at the dashboard in a minute","ok"); }
    catch(e){ toast("Couldn't start the check","err"); }
    return; }
  if (t.closest("#consolYes")){
    await api("/consolidation/accept",{method:"POST"}).catch(()=>{});
    $("consolCard").innerHTML = `<div class="consol"><div class="consol-b">
      ✨ Drafting your consolidated note — it'll appear in your feed shortly.</div></div>`;
    return; }
  if (t.closest("#consolNo")){
    await api("/consolidation/dismiss",{method:"POST"}).catch(()=>{});
    $("consolCard").innerHTML = ""; return; }
  if (t.closest("#briefGen")){
    const b = $("briefGen");
    // If it's the compact refresh icon, spin it; if the full "Generate now"
    // button (empty-brief state), show inline progress. Never overflow the icon.
    if (b){
      if (b.classList.contains("brief-refresh")){ b.classList.add("spinning"); b.disabled = true; }
      else { b.textContent = "Composing…"; b.disabled = true; }
    }
    try { await api("/brief/refresh", {method:"POST"});
      toast("Brief composing — check back in a minute ✨","ok");
    } catch(e){ toast("Couldn't request brief","err"); }
    return; }
  const pact = t.closest("[data-pact]");
  if (pact){
    if (pact.dataset.pact==="open"){ $("pulseSheet").classList.remove("on"); await openDetail(pact.dataset.id); return; }
    if (pact.dataset.pact==="retry-jobs"){
      pact.disabled = true; pact.textContent = "…";
      try { await api("/agent/jobs/retry", {method:"POST"}); toast("Re-queued","ok"); } catch(e){ toast("Couldn't retry","err"); }
      openPulse(); return; }
  }
  const pnav = t.closest("[data-pulse-nav]");
  if (pnav){
    const [kind,val] = pnav.dataset.pulseNav.split(":");
    if (kind==="errors"){ await showErrors(); return; }
    if (kind==="saved"){ await showSaved(); return; }
    $("pulseSheet").classList.remove("on");
    if (kind==="filter"){ S.filter=val; switchView("vNotes"); renderNotes(); }
    else if (kind==="view"){
      // val may carry a filter query, e.g. "vActs?aging=1" or "vActs?done=1".
      const [vid, query] = val.split("?");
      S.actionFilter = query || "";   // consumed by loadActions()
      switchView(vid);
    }
    setReturn(true);                  // one tap back to the dashboard
    return; }
  if (t.closest("#backPill")){ setReturn(false); openPulse(); return; }
  if (t.closest("#tagsBack")){ $("tagsSheet").classList.remove("on"); return; }
  if (t.closest("#dTitle") && !t.closest("input")){ renameNote(); return; }
  if (t.closest("#tagsToggle")){ toggleTags(); return; }
  if (t.closest("#rSort")){ toggleRemSort(); return; }
  if (t.closest("#tagAll")){ openAllTags(); return; }
  const retryBtn = t.closest("[data-retry]");
  if (retryBtn){
    const nid = retryBtn.dataset.retry;
    retryBtn.textContent = "…"; retryBtn.disabled = true;
    try { await api("/errors/"+nid+"/retry", {method:"POST"});
      toast("Re-queued for enrichment ⟳","ok");
      retryBtn.closest(".err-item")?.remove();
    } catch(e){ toast("Retry failed","err"); retryBtn.disabled=false; retryBtn.textContent="Retry"; }
    return; }
  // Giving up on an error. The dashboard tile behind this sheet shows the
  // same count, so refresh it silently once the list empties -- otherwise
  // the user dismisses everything and the badge still reads 3.
  const dropBtn = t.closest("[data-err-dismiss]");
  if (dropBtn){
    const nid = dropBtn.dataset.errDismiss;
    dropBtn.textContent = "…"; dropBtn.disabled = true;
    try {
      await api("/errors/"+encodeURIComponent(nid), {method:"DELETE"});
      dropBtn.closest(".err-item")?.remove();
      if (!document.querySelector("#errBody .err-item")) await showErrors();
      if ($("pulseSheet")?.classList.contains("on")) await openPulse(true);
    } catch(e){ toast(apiWhy(e,"Couldn't dismiss"),"err"); dropBtn.disabled=false; dropBtn.textContent="Dismiss"; }
    return; }
  if (t.closest("#errClearAll")){
    const btn = t.closest("#errClearAll");
    btn.textContent = "…"; btn.disabled = true;
    try {
      const r = await api("/errors", {method:"DELETE"});
      toast(`Dismissed ${r.dismissed} error${r.dismissed===1?"":"s"}`,"ok");
      await showErrors();
      if ($("pulseSheet")?.classList.contains("on")) await openPulse(true);
    } catch(e){ toast(apiWhy(e,"Couldn't dismiss"),"err"); btn.disabled=false; btn.textContent="Dismiss all"; }
    return; }
  const errNote = t.closest("[data-err-note]");
  if (errNote && !t.closest("[data-retry]") && !t.closest("[data-err-dismiss]")){
    $("errSheet")?.classList.remove("on");
    await openDetail(errNote.dataset.errNote); return; }
  if (t.closest("#errBack")){ $("errSheet").classList.remove("on"); return; }
  if (t.closest("#svBack")){ $("svSheet").classList.remove("on"); return; }
  const svBtn = t.closest("[data-sv-as]");
  if (svBtn){ await svDecide(svBtn.dataset.svFp, svBtn.dataset.svAs); return; }
  const calNote = t.closest("[data-cal-note]");
  if (calNote){ $("pulseSheet").classList.remove("on"); await openDetail(calNote.dataset.calNote); return; }
  if (t.closest("#toTop")){ $("mainSc").scrollTo({top:0,behavior:"smooth"}); return; }
  if (t.closest("#menuBk")){ $("menuLayer").classList.remove("on"); return; }
  const mf = t.closest(".mitem[data-mf]");
  if (mf){ await leaveEditorIfOpen(); S.filter = mf.dataset.mf; S.listView = ""; $("menuLayer").classList.remove("on");
    switchView("vNotes"); loadNotes(); return; }
  const lv = t.closest(".mitem[data-lv]");
  if (lv){ await leaveEditorIfOpen(); S.listView = lv.dataset.lv; S.filter = "all"; $("menuLayer").classList.remove("on");
    switchView("vNotes"); loadNotes(); return; }
  const mt = t.closest(".tag[data-tag]");
  if (mt){ S.q = mt.dataset.tag; $("srchIn").value = S.q; $("srchWrap").classList.add("on"); srchSync();
    $("menuLayer").classList.remove("on"); switchView("vNotes"); loadNotes(); return; }
  if (t.closest("#clearFilter")){ S.filter = "all"; renderNotes(); return; }
  if (t.closest("#clearListView")){ S.listView = ""; S.filter = "all"; loadNotes(); return; }
  if (t.closest("#srchBtn")){ setSearchOpen(!$("srchWrap").classList.contains("on")); return; }
  if (t.closest("#srchBack")){ setSearchOpen(false); return; }
  if (t.closest("#srchClr")){
    $("srchIn").value = ""; S.q = ""; srchSync();
    if (S.askMode){ S.askThread = []; S.askActive = null; $("askPanel").innerHTML = ""; $("feed").style.display = "none"; showAskHint(); }
    else loadNotes();
    $("srchIn").focus(); return; }
  if (t.closest("#askGo")){ const v = $("srchIn").value; if (v.trim()){ await askQuestion(v); $("srchIn").blur(); } return; }
  const md = t.closest("#srchModes button[data-mode]");
  if (md){
    const on = md.dataset.mode==="ask";
    if (on !== !!S.askMode){
      if (on) setAskMode(true);
      else { setAskMode(false); S.q = $("srchIn").value.trim(); loadNotes(); }
      $("srchIn").focus();
    }
    return; }
  if (t.closest("#storyBack")){ $("storySheet").classList.remove("on"); return; }
  const stOpen = t.closest("[data-story]");
  if (stOpen){ openStory(stOpen.dataset.id); return; }
  if ($("storySheet").classList.contains("on") && await storyClick(t)) return;
  if (t.closest("#filtBtn")){ await openFilters(); return; }
  if (t.closest("#filtBack")){ $("filterSheet").classList.remove("on"); return; }
  if (t.closest("#shareBack")){ $("shareSheet").classList.remove("on"); return; }
  if (t.closest("#digestBack")){ $("digestSheet").classList.remove("on"); return; }
  if (t.closest("#dupBack")){ $("dupSheet").classList.remove("on"); return; }
  if (t.closest("#healthBack")){ $("healthSheet").classList.remove("on"); return; }
  if ($("filterSheet").classList.contains("on") && await filClick(t)) return;
  if ($("digestSheet").classList.contains("on") && await digestClick(t)) return;
  if ($("dupSheet").classList.contains("on") && await dupClick(t)) return;
  if ($("healthSheet").classList.contains("on") && await healthClick(t)) return;
  const shb = t.closest("[data-sh]");
  if (shb){ await shareClick(shb.dataset.sh); return; }
  const filc = t.closest("[data-fil]");
  if (filc){ if (filc.dataset.fil==="open") await openFilters(); else await applyFilters({}); return; }
  if (t.closest("#weekItem")){ await leaveEditorIfOpen(); await openDigest(); return; }
  if (t.closest("#dupItem")){ await leaveEditorIfOpen(); await openDups(); return; }
  if (t.closest("#healthItem")){ await leaveEditorIfOpen(); await openHealth(); return; }
  const kb = t.closest(".ask-kinds [data-kind]");
  if (kb){ setAskKind(kb.dataset.kind); return; }
  const sv = t.closest("[data-ask-save]"), cp = t.closest("[data-ask-copy]");
  if (sv || cp){
    const a = S.askThread[(sv||cp).dataset.askSave ?? (sv||cp).dataset.askCopy];
    if (!a || !a.answer) return;
    const plain = a.answer.replace(/\s*\[\d+(?:\s*[,;]\s*\d+)*\]/g,"").trim();
    if (cp){ try { await navigator.clipboard.writeText(plain); toast("Copied","ok"); } catch(e){ toast("Couldn't copy","err"); } return; }
    const refs = askRefs(a), srcs = refs.length ? "\n\n---\nSources: " + refs.map(r=>r.title||"Untitled").join("; ") : "";
    const head = a.kind==="write" && !/^#\s/.test(plain) ? "" : "";
    const made = await createNote(head + plain + srcs, "off");
    if (made) toast("Saved as a note","ok");
    return; }
  const ash = t.closest("[data-ask-share]");
  if (ash){
    const a = S.askThread[ash.dataset.askShare];
    if (a && a.answer) shareDoc(textToDoc(a.question, a.answer, askRefs(a).map(r=>r.title||"Untitled")));
    return; }
  const eg = t.closest("[data-eg]");
  if (eg){ $("srchIn").value = eg.dataset.eg; srchSync(); await askQuestion(eg.dataset.eg); return; }
  if (t.closest("#askToggle")){ setAskMode(!S.askMode); return; }
  const cTog = t.closest("#connTog button[data-v]");
  if (cTog){ S.connView = cTog.dataset.v; S.graphSel = null; S.gz = {k:1,x:0,y:0}; renderConnections(); return; }
  const gf = t.closest("[data-gf]");
  if (gf){ S.graphFocus = gf.dataset.gf || null; S.graphSel = null; S.gz = {k:1,x:0,y:0}; renderConnections();
    const on = document.querySelector(".g-chip.on"); if (on && on.scrollIntoView) on.scrollIntoView({inline:"center",block:"nearest"});
    return; }
  if (t.closest("[data-gmore]")){ S.graphMax = (S.graphMax||60) + 60; S.graphSel = null; renderConnections(); return; }
  if (t.closest("[data-gall]")){ S.graphMax = 300; S.graphSel = null; renderConnections(); return; }
  const gzb = t.closest("[data-gz]");
  if (gzb){
    const svg = document.querySelector(".g-svg");
    if (gzb.dataset.gz === "reset") S.gz = {k:1,x:0,y:0};
    else if (svg){ const vb = svg.viewBox.baseVal;
      gzZoomAt(svg, {x:vb.width/2, y:vb.height/2}, S.gz.k * (gzb.dataset.gz === "in" ? 1.6 : 1/1.6)); }
    renderConnections(); return; }
  const gOpen = t.closest("#gOpen");
  if (gOpen){ await openDetail(gOpen.dataset.id); return; }
  const gNode = t.closest(".g-node[data-id]");
  if (gNode){ const id = gNode.dataset.id;
    if (S.graphSel === id){ await openDetail(id); return; }   // second tap opens
    S.graphSel = id; renderConnections(); return; }
  if (t.closest(".g-svg") && S.graphSel != null){ S.graphSel = null; renderConnections(); return; }
  const hsBtn = t.closest("[data-hs]");
  if (hsBtn){ e.stopPropagation(); await homeAction(hsBtn); return; }
  const askChip = t.closest(".ask-chip[data-id]");
  if (askChip){ await openDetail(askChip.dataset.id); return; }
  const refBtn = t.closest(".ask-ref[data-n]");
  if (refBtn){
    const tt = refBtn.dataset.t, id = "askRef"+tt+"-"+refBtn.dataset.n;
    if (!document.getElementById(id) && S.askThread[tt]){ S.askThread[tt].moreOpen = true; renderAskPanel(); }
    const c2 = document.getElementById(id);
    if (c2){ c2.scrollIntoView({block:"center",behavior:"smooth"});
      c2.classList.remove("flash"); void c2.offsetWidth; c2.classList.add("flash"); }
    return; }
  const askAct = t.closest(".ask-more[data-act]");
  if (askAct && S.askThread[askAct.dataset.t]){
    const turn = S.askThread[askAct.dataset.t], act = askAct.dataset.act;
    if (act==="more-refs") turn.moreOpen = true;
    if (act==="show-others") turn.otherOpen = true;
    if (act==="hide-others") turn.otherOpen = false;
    renderAskPanel(); return; }
  const refCard = t.closest(".ask-ref-card[data-id], .ask-other[data-id]");
  if (refCard){ await openDetail(refCard.dataset.id); return; }
  if (t.closest("#refreshBtn")){
    $("refreshBtn").classList.add("spin"); await refreshAll();
    setTimeout(()=>$("refreshBtn").classList.remove("spin"),500); return; }
  const nav = t.closest(".nitem[data-view]");
  if (nav){ S.actionFilter = ""; switchView(nav.dataset.view); return; }
  if (t.closest("#fabBtn")){ $("fabMenu").classList.toggle("on"); return; }
  if (t.closest("#fabText")){ $("fabMenu").classList.remove("on"); openEditor("new"); return; }
  if (t.closest("#fabList")){ $("fabMenu").classList.remove("on"); openChecklistNew(); return; }
  if (t.closest("#fabEvent")){ $("fabMenu").classList.remove("on"); await openQuickEvent(); return; }
  if ($("fabMenu").classList.contains("on") && !t.closest("#fabBtn") && !t.closest("#fabMenu")){
    $("fabMenu").classList.remove("on"); }
  const aiTog = t.closest("#edAiSeg");
  if (aiTog){
    toggleAiMode();
    // On an existing note, apply immediately via the coherent mode endpoint so
    // the state is real, not pending a save the user might never make.
    if (Ed.mode==="edit" && Ed.noteId){
      await api("/notes/"+Ed.noteId+"/enrich-mode",
                {method:"POST", body:{mode: Ed.aiMode==="on" ? "full" : "metadata"}}).catch(()=>null);
      invalidateViewCache();
    }
    return; }
  if (t.closest("#edDone")){ await closeEditor(); return; }
  const eb = t.closest("#editBtn");
  if (eb){
    if (!S.cur) return;
    // Reviewing a suggestion → edit the SUGGESTION and adopt it as approved
    // (the user's correction becomes their final note). Otherwise edit the body.
    if (eb.dataset.edmode==="suggestion"){ await openAdoptReview(); }
    else { openEditor("edit", S.cur); }
    return; }
  // Note links: normal tap opens a quick Custom Tab. (A LONG-press instead
  // forces the full default browser — handled separately; if that already fired
  // for this press, _lpFired is set and we skip the tap-open below.)
  const lnk = t.closest("a.nlink");
  if (lnk){
    e.preventDefault();
    if (_lpFired){ _lpFired = false; return; }   // long-press already opened it
    window.open(lnk.href, "_blank", "noopener,noreferrer");
    return; }
  // Tap the note text → edit. But NOT when the user has a text selection
  // (they're highlighting), otherwise the editor steals the gesture.
  if (t.closest("#noteRead") && !t.closest("button") && !t.closest("a.nlink")){
    if (!S.cur) return;
    const sel = window.getSelection();
    if (sel && !sel.isCollapsed && sel.toString().trim()) return;  // selecting → let them
    const n = S.cur, e = n.enrichment;
    const vb = !!(n.protected || (n.body||"").includes("```"));
    const reviewing = (n.status==="pending"||n.status==="raw") && e && e.polished && !vb;
    if (reviewing){ await openAdoptReview(); } else { openEditor("edit", S.cur); }
    return; }
  if (t.closest("#dKebab") || t.closest("#moreBtn")){
    $("actionSheetBody").innerHTML = S._actionSheetHtml || "";
    $("actionScrim").classList.add("on");
    $("actionSheet").classList.add("on");
    return; }
  if (t.closest("#actionScrim") || t.closest("#actBack")){
    $("actionSheet").classList.remove("on");
    $("actionScrim").classList.remove("on");
    return; }
  if (t.closest("#detExpand")){
    const bucket = $("detBucket"), btn = $("detExpand");
    const open = bucket.style.display !== "none";
    bucket.style.display = open ? "none" : "block";
    btn.classList.toggle("open", !open);   // rotates the chevron via CSS
    S._detailsOpen = !open;                // survive the next renderDetail()
    return; }
  if (t.closest("#histBtn")){
    const w = $("histWrap");
    if (w.style.display!=="none"){ w.style.display="none";
      $("histBtn").textContent="🕰 Show revisions"; }
    else await loadHistory();
    return; }
  const rbtn = t.closest("[data-restore]");
  if (rbtn){
    const rid = rbtn.dataset.restore, nid = S.cur && S.cur.id;
    if (!nid) return;
    const okToRestore = await showConfirm(
      "This becomes your note's current text. Your existing text is saved to history first, so you can undo this.",
      {title:"Restore this version?", okText:"Restore"});
    if (!okToRestore) return;
    try {
      const res = await api("/notes/"+nid+"/revisions/"+rid+"/restore",{method:"POST"});
      if (res && res.note){
        S.cur = res.note;
        renderDetail(S.cur);
        loadNotes();
        toast("↩ Restored — your previous text is in history","ok");
      } else if (res && res.unchanged){
        toast("That version matches your current text","ok");
      }
    } catch(err){ toast("Couldn't restore","err"); }
    return; }
  const rh = t.closest(".rev-h[data-rid]");
  if (rh){ const b = $("rev-"+rh.dataset.rid);
    b.style.display = b.style.display==="block"?"none":"block"; return; }
  const gh = t.closest(".agroup-h");
  if (gh){ gh.closest(".agroup").classList.toggle("closed"); return; }
  const goto = t.closest(".asrc[data-goto]");
  if (goto){ e.stopPropagation(); await openDetail(goto.dataset.goto); return; }
  // Dismiss handlers must come BEFORE the .aitem toggle below — the ✕ sits
  // inside the row, so without this a dismiss tap would also tick the checkbox.
  const dAid = t.closest("[data-dismiss-aid]");
  if (dAid){
    const aid = dAid.dataset.dismissAid;
    const r = await api("/actions/"+aid, {method:"DELETE"}).catch(()=>null);
    if (r){
      await refreshDetail();
      // Undo matters here: dismissal is remembered permanently, so a mis-tap
      // needs an immediate way back. The toast carries the escape hatch.
      toast("Dismissed — not a task", "ok", {
        label: "Undo",
        fn: async () => {
          await api("/actions/"+aid+"/restore",{method:"POST"}).catch(()=>null);
          await refreshDetail(); }});
    } else toast("Couldn't dismiss","err");
    return; }

  const dRid = t.closest("[data-dismiss-rid]");
  if (dRid){
    const rid = dRid.dataset.dismissRid;
    const hasEvent = dRid.dataset.hasEvent === "1";
    let alsoDelete = false;
    if (hasEvent){
      // This reminder became a REAL calendar event. Deleting from the user's
      // Google Calendar touches data in an external system they may have shared
      // or already acted on — so we ask explicitly and default to the safer
      // outcome. Cancel = dismiss here only, leaving the event untouched.
      alsoDelete = !!(await showConfirm(
        "This reminder created an event in your calendar. Remove that event too? "
        + "Cancel dismisses the reminder here and leaves your calendar alone.",
        {title:"Remove calendar event?", okText:"Remove event too", danger:true}));
    }
    const r = await api("/reminders/"+rid + (alsoDelete?"?delete_event=true":""),
                        {method:"DELETE"}).catch(()=>null);
    // A bill's line stands for its heads-up and its due-day reminder; both go,
    // or the one left behind reappears as the same bill on the next load.
    const also = (dRid.dataset.alsoRids||"").split(",").filter(Boolean);
    if (r) for (const id of also)
      await api("/reminders/"+id + (alsoDelete?"?delete_event=true":""), {method:"DELETE"}).catch(()=>null);
    if (r){
      // Refresh whichever context we're in: the reminders tab and/or the open
      // note detail (a reminder can be dismissed from either place).
      if (S.view === "vReminders") await loadReminders();
      await refreshDetail();
      const msg = r.calendar === "delete_requested"
        ? "Dismissed — removing the calendar event"
        : (r.calendar === "kept"
            ? "Dismissed — the calendar event is still there"
            : "Reminder dismissed");
      toast(msg, "ok");
    } else toast("Couldn't dismiss","err");
    return; }

  const exOn = t.closest("[data-extract-on]");
  if (exOn){
    if (!S.cur) return;
    const kind = exOn.dataset.extractOn;   // "actions" | "reminders"
    await api("/notes/"+S.cur.id+"/extract",
              {method:"POST", body:{[kind]: true}}).catch(()=>null);
    await refreshDetail();
    toast(kind==="actions" ? "Task extraction on" : "Reminder extraction on","ok");
    return; }

  const ai = t.closest(".aitem[data-aid]");
  if (ai){ await toggleAction(ai.dataset.aid, +ai.dataset.done); return; }
  // Keep-style tile quick-actions — act on the note WITHOUT opening it.
  const cAct = t.closest(".c-act[data-act]");
  if (cAct){
    const id = cAct.dataset.actId, act = cAct.dataset.act;
    if (act==="delete"){
      // Destructive — confirm before removing.
      const r = await api(`/notes/${id}`, {method:"DELETE"}).catch(()=>null);
      if (r!==null){
        const i = S.notes.findIndex(n=>n.id===id);
        if (i>=0) S.notes.splice(i,1);
        invalidateViewCache(); renderNotes();
        if (r.trashed) trashedToast(id); else toast("Deleted permanently — restart the UI server to enable Trash","err");
      } else toast("Couldn't move it to Trash — nothing was deleted","err");
      return;
    }
    if (act==="archive"){ await quickArchive(id); return; }
    if (act==="pin"){ await quickPin(id); return; }
    return;
  }
  const card = t.closest(".card[data-id]");
  if (card){ await openDetail(card.dataset.id); return; }
  if (t.closest("#dBack")){ closeDetail(); return; }
  if (t.closest("#cpBtn")){ await copyNote(); return; }
  if (t.closest("#shBtn")){ await shareNote(); return; }
  // Any action inside the overflow popover dismisses it after acting, so the user
  // lands back on the note (or list) rather than staring at a stale menu.
  if (t.closest("#actionSheet .sheet-act")){
    $("actionSheet").classList.remove("on");
    $("actionScrim").classList.remove("on");
  }
  const colBtn = t.closest("[data-color]");
  if (colBtn && S.cur){
    const v = colBtn.dataset.color === "auto" ? null : parseInt(colBtn.dataset.color, 10);
    await setNoteColor(S.cur.id, v); return; }
  if (t.closest("#pinBtn")){
    if (!S.cur) return;
    const r = await api("/notes/"+S.cur.id+"/pin",{method:"POST"}).catch(()=>null);
    if (r){ S.cur.pinned=r.pinned; renderDetail(S.cur); renderNotes();
      toast(r.pinned?"📌 Pinned to top":"Unpinned","ok"); }
    return; }
  if (t.closest("#archBtn")){
    if (!S.cur) return;
    const r = await api("/notes/"+S.cur.id+"/archive",{method:"POST"}).catch(()=>null);
    if (r){
      S.cur.archived=r.archived;
      if (r.archived){ // leave the note; return to the list where it's now gone
        const archivedId = S.cur.id;   // capture now — S.cur may point elsewhere by the time Undo is tapped
        $("edLayer").classList.remove("on"); invalidateViewCache(); loadNotes();
        toast("🗄 Archived","ok",{label:"Undo", fn: async ()=>{
          const u = await api("/notes/"+archivedId+"/archive",{method:"POST"}).catch(()=>null);
          if (u){ invalidateViewCache(); await loadNotes(); toast("Restored"); }
          else toast("Couldn't undo","err");
        }});
      } else { renderDetail(S.cur); invalidateViewCache(); loadNotes(); toast("Unarchived — back in your notes","ok"); }
    } else toast("Couldn't archive","err");
    return; }
  if (t.closest("#hideBtn")){
    if (!S.cur) return;
    const r = await api("/notes/"+S.cur.id+"/hide",{method:"POST"}).catch(()=>null);
    if (r){
      S.cur.hidden=r.hidden;
      if (r.hidden){
        const stashedId = S.cur.id;
        $("edLayer").classList.remove("on"); invalidateViewCache(); loadNotes();
        toast("📥 Stashed","ok",{label:"Undo", fn: async ()=>{
          const u = await api("/notes/"+stashedId+"/hide",{method:"POST"}).catch(()=>null);
          if (u){ invalidateViewCache(); await loadNotes(); toast("Restored"); }
          else toast("Couldn't undo","err");
        }});
      } else { renderDetail(S.cur); invalidateViewCache(); loadNotes(); toast("Back out of your Stash","ok"); }
    } else toast("Couldn't stash","err");
    return; }
  const tagSugB = t.closest(".tagsug-b[data-tag]");
  if (tagSugB){ applyTagSuggestion(tagSugB.dataset.tag); return; }
  const modeBtn = t.closest("[data-enrich-mode]");
  if (modeBtn){
    if (!S.cur) return;
    const v = modeBtn.dataset.enrichMode;
    const prev = S.cur.enrich_optout ? "off"
               : ((S.cur.meta_only || S.cur.protected) ? "metadata" : "full");
    if (v === prev) return;                       // already selected — no-op
    // OPTIMISTIC: move the highlight on THIS frame, before the request goes out.
    // The sheet is already open, so re-rendering S._actionSheetHtml later never
    // reached this DOM — the selection appeared stuck on the old value. Waiting
    // for a round-trip also just feels broken, even when it succeeds.
    setSegMode(v);
    if (navigator.vibrate) { try { navigator.vibrate(8); } catch(e){} }
    const r = await api("/notes/"+S.cur.id+"/enrich-mode",
                        {method:"POST", body:{mode:v}}).catch(()=>null);
    if (r){
      await refreshDetail();                      // rebuilds S._actionSheetHtml
      syncOpenSheet();                            // …and push it into the open sheet
      loadNotes();
      // Don't imply it already happened. Off bites immediately; the other two
      // only shape the next run, so point at the action that applies them.
      toast(v==="off" ? "AI will skip this note from now on"
          : (S.cur.status==="approved"
              ? (v==="full" ? "Set to Full — tap Run now to apply it"
                            : "Set to Metadata — tap Run now to apply it")
              : (v==="full" ? "Set to Full" : "Set to Metadata")), "ok");
    } else {
      setSegMode(prev);                           // reconcile: put it back
      toast("Couldn't change that","err");
    }
    return; }
  if (t.closest("#polishBtn")){
    if (!S.cur) return;
    // The popover dismisses itself on tap, so there's no button left to show a
    // spinner on — feedback comes from the toast instead.
    const r = await api("/notes/"+S.cur.id+"/polish",{method:"POST"}).catch(()=>null);
    if (r){ toast("✨ Polishing your text — review it shortly","ok"); }
    else { toast("Couldn't request polish","err"); }
    return; }
  const clT = t.closest("[data-cl-toggle]");
  if (clT){ e.preventDefault(); await toggleChecklistItem(+clT.dataset.clToggle); return; }
  const clD = t.closest("[data-cl-del]");
  if (clD){ e.preventDefault(); await deleteChecklistItem(+clD.dataset.clDel); return; }
  // Highlight colour bar (appears while text is selected)
  const hlc = t.closest("[data-hlcolor]");
  if (hlc){ e.preventDefault(); await applyHighlightColor(hlc.dataset.hlcolor); return; }
  if (t.closest("#dDelete")){ await delNote(); return; }
  if (t.closest("#ctaBtn")){ await openAdoptReview(); return; }
  if (t.closest("#adoptBack") || t.closest("#adoptCancel")){
    $("adoptSheet").classList.remove("on");
    toast("Left as-is — still awaiting your review","ok");
    return; }
  if (t.closest("#adoptConfirm")){ await confirmAdopt(); return; }
  if (t.closest("#adoptKeep")){ await keepOriginal(); return; }
  const so = t.closest(".status-act[data-status]");
  if (so){ await setStatus(so.dataset.status); return; }
  const relMore = t.closest("#relMore");
  if (relMore){ S.relExpanded = !S.relExpanded; renderDetail(S.cur); return; }
  const rel = t.closest(".rel[data-id]");
  if (rel){ await openDetail(rel.dataset.id); return; }
  const cn = t.closest(".clu-n[data-id]");
  if (cn){ await openDetail(cn.dataset.id); return; }
  const cluMore = t.closest(".clu-more[data-idx]");
  if (cluMore){ const idx = +cluMore.dataset.idx;
    if (S.connExpanded.has(idx)) S.connExpanded.delete(idx); else S.connExpanded.add(idx);
    renderConnections(); return; }
});
document.addEventListener("input", e=>{
  if (e.target.id==="adoptText"){ autoGrow(e.target); }
  if (e.target.id==="edText"){ autoGrow(e.target); updateTagSuggestions();
    // Autosave both new and existing notes — a new note auto-creates on the
    // first debounced flush, matching the Keep model.
    clearTimeout(Ed.timer); Ed.timer=setTimeout(edFlush,1200); }
  if (e.target.id==="srchIn"){
    srchSync();
    if (S.askMode) return;   // Ask submits on Enter, not per keystroke
    clearTimeout(window._qT);
    S.q = e.target.value.trim(); window._qT = setTimeout(loadNotes,250); }
});
document.addEventListener("keydown", async e=>{
  if ((e.ctrlKey||e.metaKey)&&e.key==="Enter"&&Ed.open) closeEditor();
  if (e.target && e.target.classList && e.target.classList.contains("g-node")
      && (e.key==="Enter" || e.key===" ")){
    e.preventDefault(); e.target.dispatchEvent(new MouseEvent("click",{bubbles:true})); return; }
  if (e.target && e.target.id==="srchIn" && e.key==="Escape"){ setSearchOpen(false); return; }
  if (e.target && e.target.id==="srchIn" && e.key==="Enter"){
    e.preventDefault();                      // the field is a textarea: Enter submits, never a newline
    if (S.askMode){ await askQuestion(e.target.value); e.target.blur(); }
    else e.target.blur();                    // search runs as you type; Enter just closes the keyboard
    return;
  }
  if (e.target && e.target.id==="localPortIn" && e.key==="Enter"){
    e.preventDefault(); setConnMode("local", e.target.value); return;
  }
  if (e.target && e.target.id==="clAdd" && e.key==="Enter"){
    e.preventDefault(); const v=e.target.value; e.target.value=""; await addChecklistItem(v); return;
  }
  if ($("modalLayer").classList.contains("on")){
    if (e.key==="Enter" && $("modalInput").style.display!=="none"){
      e.preventDefault(); _closeModal($("modalInput").value); }
    else if (e.key==="Escape"){ e.preventDefault(); _closeModal(null); }
  }
});
window.addEventListener("online", ()=>flushOutbox());
document.addEventListener("copy", e=>{
  const sel = document.getSelection();
  const text = sel && sel.toString();
  if (!text) return;      // nothing selected — let the browser do its default thing
  e.clipboardData.setData("text/plain", text);
  e.preventDefault();     // suppresses the text/html entry the browser would otherwise add
});
// Fallback for older WebViews without interactive-widget=resizes-content (set
// on the viewport meta tag above, which is the real fix on modern Chrome): on
// some Android versions position:fixed;inset:0 doesn't shrink for the on-screen
// keyboard, so a fixed header can end up above the actually-visible area,
// unreachable without scrolling. Pin the editor's height to the visual
// viewport directly whenever it changes. No-op when the editor is closed.
if (window.visualViewport){
  window.visualViewport.addEventListener("resize", ()=>{
    const ed = $("edLayer");
    if (!ed || !ed.classList.contains("on")) return;
    ed.style.height = window.visualViewport.height + "px";
  });
}

// Tap anywhere on the blank writing area to start typing (the textarea itself only covers part of it).
document.addEventListener("click", e=>{
  const sc = e.target.closest && e.target.closest("#edLayer .ed-sc");
  if (sc && e.target === sc){ const ta = $("edText"); if (ta){ ta.focus(); const n = ta.value.length; try{ ta.setSelectionRange(n, n); }catch(_){} } }
});

// Picking a date on a "Needs a decision" card. The card's primary button is
// the agent's one suggestion; this is the same accept with a day of your own.
// It listens for change rather than click because the value only exists once
// the native picker closes.
document.addEventListener("change", ev=>{
  const inp = ev.target.closest && ev.target.closest("[data-fu-date]");
  if (!inp || !inp.value) return;
  fuAct("accept", +inp.dataset.fuDate, inp.value);
});

// Choosing Reminder on a saved capture. Same reason as the follow-up picker
// above: the day only exists once the native picker closes, so this listens
// for change rather than click.
document.addEventListener("change", ev=>{
  const inp = ev.target.closest && ev.target.closest("[data-sv-date]");
  if (!inp || !inp.value) return;
  svDecide(inp.dataset.svDate, "reminder", inp.value);
});
