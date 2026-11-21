/* ── rendering: cards + detail ──────────────────────────────────────────── */
function cardMediaHtml(n){
  const A = n.attachments || [];
  if (!A.length) return "";
  const vis = A.filter(a=>a.kind!=="audio"), au = A.filter(a=>a.kind==="audio");
  let h = "";
  if (vis.length){
    const shown = vis.slice(0,3), more = vis.length-3;
    h += `<div class="c-media m${shown.length}">${shown.map((a,i)=>
      `<div class="cm${a.kind==="sketch"?" skp":a.kind==="video"?" vid":""}"><img loading="lazy" decoding="async" alt="" src="${esc(attThumb(a))}"${a.kind==="video"?"":` data-full="${esc(attUrl(a))}"`}/>${a.kind==="video"?`<span class="cm-play" aria-hidden="true">▶</span>${a.duration_s>0?`<span class="cm-dur">${fmtDur(a.duration_s)}</span>`:""}`:""}${
        (i===2 && more>0)?`<div class="cm-more">+${more}</div>`:""}</div>`).join("")}</div>`;
  }
  au.slice(0,2).forEach(a=>{
    const busy = /pending|running/.test(a.transcript_status) && !a.transcript;
    h += `<span class="c-aud${busy?" busy":""}">🎙 ${fmtDur(a.duration_s)}${busy?" · transcribing…":""}</span>`;
  });
  return h;
}
function mediaPreview(n){
  const t = (n.attachments||[]).map(a=>(a.transcript||"").trim()).filter(Boolean).join(" ");
  return t.length>180 ? t.slice(0,180)+"…" : t;
}
const TXL = {audio:"Transcript", image:"In this photo", sketch:"In this sketch", video:"In this video"};
function attTxHtml(a, n){
  const st = a.transcript_status, t = (a.transcript||"").trim();
  const k = a.kind, verb = k==="audio" ? "Transcribe" : k==="video" ? "Read video" : "Read text";
  if (a._local) return `<div class="att-st">⚡ Waiting to upload — it'll be ${k==="audio"?"transcribed":"read"} once you're online.</div>`;
  if (t){
    const inBody = (n.body||"").includes(t);
    return `<div class="att-tx" data-attbox="${esc(a.id)}"><div class="att-tx-h">${TXL[k]}${a.transcript_model==="browser"?" · live captions":a.transcript_model==="user"?" · edited":""}</div>${esc(t)}</div>
      <div class="att-meta" style="padding-top:0">
        ${inBody?"":`<button class="att-lnk" data-att="ins" data-id="${esc(a.id)}">Insert into note</button>`}
        <button class="att-lnk" data-att="cp" data-id="${esc(a.id)}">Copy</button>
        <button class="att-lnk" data-att="edit" data-id="${esc(a.id)}">Edit</button>
        <span class="sp"></span>
        ${a.transcript_model==="user"?"":`<button class="att-lnk" data-att="redo" data-id="${esc(a.id)}">Redo</button>`}</div>`;
  }
  if (st==="pending") return `<div class="att-st"><span class="spinner" style="width:14px;height:14px;border-width:2px"></span>Waiting for your agent to ${k==="audio"?"transcribe":"read"} this… <button class="att-lnk" data-att="edit" data-id="${esc(a.id)}">Type it in</button></div>`;
  if (st==="running") return `<div class="att-st"><span class="spinner" style="width:14px;height:14px;border-width:2px"></span>${k==="audio"?"Transcribing…":k==="video"?"Watching and listening…":"Reading…"}</div>`;
  if (st==="skipped" || st==="failed")
    return `<div class="att-st" style="flex-wrap:wrap">${esc(a.transcript_error || (st==="failed"?"That didn't work.":"No engine is set up for this yet."))}
      <button class="att-lnk" data-att="redo" data-id="${esc(a.id)}">Try again</button>
      <button class="att-lnk" data-att="edit" data-id="${esc(a.id)}">Type it in</button></div>`;
  if (st==="done") return `<div class="att-st">${k==="audio"?"No speech found.":k==="video"?"Nothing could be made out in this video.":"No text found."}
      <button class="att-lnk" data-att="edit" data-id="${esc(a.id)}">Type it in</button></div>`;
  return `<div class="att-st"><button class="att-lnk" data-att="redo" data-id="${esc(a.id)}">${verb}</button>
      <button class="att-lnk" data-att="edit" data-id="${esc(a.id)}">Type it in</button></div>`;
}
function attHtml(n){
  const A = n.attachments || [];
  if (!A.length) return "";
  let h = `<div class="att-wrap">`;
  for (const a of A){
    if (a.kind==="audio"){
      h += `<div class="att-aud" data-attid="${esc(a.id)}">
        <div class="att-row">
          <button class="att-play" data-att="play" data-id="${esc(a.id)}" aria-label="Play"><svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg></button>
          <input class="att-seek" type="range" min="0" max="1000" value="0" aria-label="Seek"/>
          <span class="att-time">0:00 / ${fmtDur(a.duration_s)}</span>
          <button class="att-spd" data-att="spd" aria-label="Speed">1×</button>
        </div>
        <audio preload="metadata" src="${esc(attUrl(a))}" data-dur="${a.duration_s||0}"></audio>
        ${attTxHtml(a,n)}
        <div class="att-meta"><span>${fmtSize(a.size||a._local?.size||0)}</span><span class="sp"></span>
          <button class="att-lnk danger" data-att="del" data-id="${esc(a.id)}">Delete</button></div></div>`;
    } else if (a.kind==="video"){
      h += `<figure class="att-fig att-vid" data-attid="${esc(a.id)}">
        <video controls playsinline preload="metadata" poster="${esc(attThumb(a))}" src="${esc(attUrl(a))}"></video>
        ${attTxHtml(a,n)}
        <div class="att-meta"><span>Video · ${a.duration_s>0?fmtDur(a.duration_s)+" · ":""}${fmtSize(a.size||a._local?.size||0)}</span><span class="sp"></span>
          <button class="att-lnk danger" data-att="del" data-id="${esc(a.id)}">Delete</button></div></figure>`;
    } else {
      h += `<figure class="att-fig${a.kind==="sketch"?" skp":""}" data-attid="${esc(a.id)}">
        <img alt="${a.kind==="sketch"?"Sketch":"Photo"}" src="${esc(attUrl(a))}" data-att="img" data-id="${esc(a.id)}"/>
        ${attTxHtml(a,n)}
        <div class="att-meta"><span>${a.kind==="sketch"?"Sketch":"Photo"} · ${fmtSize(a.size||a._local?.size||0)}</span><span class="sp"></span>
          ${a.kind==="sketch" && a.has_strokes && !a._local ? `<button class="att-lnk" data-att="skedit" data-id="${esc(a.id)}">Edit sketch</button>`:""}
          <button class="att-lnk danger" data-att="del" data-id="${esc(a.id)}">Delete</button></div></figure>`;
    }
  }
  h += `</div>`;
  const vis = A.filter(a=>a.kind!=="audio" && a.kind!=="video" && !a._local);
  if (vis.length) h += `<button type="button" class="story-cta" data-story="open" data-id="${esc(n.id)}">✍ Write a story from ${vis.length>1?"these photos":"this "+(vis[0].kind==="sketch"?"sketch":"photo")}</button>`;
  return h;
}
const attById = id => (S.cur?.attachments||[]).find(a=>a.id===id);
function audioPlaying(){ return [...document.querySelectorAll("#dBody audio")].some(a=>!a.paused); }
function attEditing(){ return !!document.querySelector("#dBody .att-edit"); }
const _fmtT = (el,cur)=>{ const box=el.closest(".att-aud"); if(!box) return;
  const au=box.querySelector("audio"), d=(isFinite(au.duration)&&au.duration>0)?au.duration:+au.dataset.dur||0;
  box.querySelector(".att-time").textContent = fmtDur(cur)+" / "+fmtDur(d);
  if (d) box.querySelector(".att-seek").value = Math.round(cur/d*1000); };
document.addEventListener("timeupdate", e=>{ if (e.target.tagName==="AUDIO") _fmtT(e.target, e.target.currentTime); }, true);
document.addEventListener("ended", e=>{ if (e.target.tagName==="AUDIO"){
  const b=e.target.closest(".att-aud")?.querySelector(".att-play"); if(b) b.innerHTML=PLAY_SVG; _fmtT(e.target,0);} }, true);
document.addEventListener("input", e=>{
  if (e.target.classList?.contains("att-seek")){
    const au = e.target.closest(".att-aud").querySelector("audio");
    const d = (isFinite(au.duration)&&au.duration>0)?au.duration:+au.dataset.dur||0;
    if (d) au.currentTime = d*e.target.value/1000;
  }
});
const PLAY_SVG = `<svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>`;
const PAUSE_SVG = `<svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><path d="M6 5h4v14H6zM14 5h4v14h-4z"/></svg>`;
async function attAction(el){
  const act = el.dataset.att, id = el.dataset.id, a = id ? attById(id) : null;
  const box = el.closest(".att-aud");
  if (act==="img"){ if (a) openLightbox(a); return; }
  if (act==="play"){
    const au = box.querySelector("audio");
    document.querySelectorAll("#dBody audio").forEach(o=>{ if (o!==au && !o.paused){ o.pause();
      const b=o.closest(".att-aud").querySelector(".att-play"); if(b) b.innerHTML=PLAY_SVG; } });
    if (au.paused){ try{ await au.play(); el.innerHTML = PAUSE_SVG; }catch(_){ toast("Can't play this recording here","err"); } }
    else { au.pause(); el.innerHTML = PLAY_SVG; }
    return;
  }
  if (act==="spd"){
    const au = box.querySelector("audio"), steps=[1,1.25,1.5,2], i=(steps.indexOf(au.playbackRate)+1)%steps.length;
    au.playbackRate = steps[i]; el.textContent = steps[i]+"×"; return;
  }
  if (!a && act!=="skedit") return;
  if (act==="cp"){ try{ await navigator.clipboard.writeText(a.transcript); toast("Copied","ok"); }catch(_){ toast("Couldn't copy","err"); } return; }
  if (act==="ins"){
    const body = S.cur.body||"";
    await patchNote(S.cur.id, {body: (body.trim()?body.replace(/\s+$/,"")+"\n\n":"")+a.transcript});
    await refreshAll(); await refreshDetail(); toast("Added to the note","ok"); return;
  }
  if (act==="edit"){
    const tgt = (box||el.closest("figure")).querySelector("[data-attbox]") || el.closest(".att-st");
    const holder = document.createElement("div");
    holder.innerHTML = `<textarea class="att-edit" id="attEd" placeholder="Type or fix the text…">${esc(a.transcript||"")}</textarea>
      <div class="att-meta" style="padding:0 0 4px"><button class="att-lnk" data-att="edsave" data-id="${esc(a.id)}">Save</button>
      <button class="att-lnk" data-att="edcancel">Cancel</button></div>`;
    const row = tgt ? (tgt.closest(".att-aud,figure")) : (box||el.closest("figure"));
    row.querySelectorAll("[data-attbox],.att-st").forEach(x=>{ x.style.display="none"; });
    const next = row.querySelector(".att-tx + .att-meta"); if (next) next.style.display="none";
    row.insertBefore(holder, row.querySelector(".att-meta:last-of-type"));
    $("attEd").focus(); return;
  }
  if (act==="edcancel"){ renderDetail(S.cur); return; }
  if (act==="edsave"){
    try { await api("/attachments/"+id,{method:"PATCH",body:{transcript:$("attEd").value}}); toast("Saved","ok"); }
    catch(e){ toast(e.offline?"Connect to save edits":"Couldn't save","err"); return; }
    await refreshDetail(); refreshAll(); return;
  }
  if (act==="redo"){
    try { await api("/attachments/"+id+"/retranscribe",{method:"POST"}); toast("Queued — your agent will pick it up","ok"); }
    catch(e){ toast(e.offline?"Connect to do that":"Couldn't queue it","err"); return; }
    await refreshDetail(); return;
  }
  if (act==="skedit"){ if (a) openSketch(S.cur.id, a); return; }
  if (act==="del"){
    const ok = await showConfirm("Delete this "+(a.kind==="audio"?"recording":a.kind==="sketch"?"sketch":a.kind==="video"?"video":"photo")+"? This can't be undone.",{title:"Delete", okText:"Delete", danger:true});
    if (!ok) return;
    if (a._local){
      for (const op of await idb.all("outbox")) if (op.kind==="attach" && op.aid===a.id) idb.rmOp(op.ts);
      await refreshAll(); await refreshDetail(); return;
    }
    try { await api("/attachments/"+id,{method:"DELETE"}); toast("Deleted","ok"); }
    catch(e){ toast(e.offline?"Connect to delete":"Couldn't delete","err"); return; }
    await refreshAll(); await refreshDetail();
  }
}
document.addEventListener("click", ev=>{
  const t = ev.target;
  const a = t.closest("[data-att]"); if (a && $("dBody").contains(a)){ ev.preventDefault(); attAction(a); return; }
  const ad = t.closest("[data-attadd]");
  if (ad && S.cur){
    const k = ad.dataset.attadd, nid = S.cur.id;
    if (k==="photo") pickPhotos(nid); else if (k==="voice") openRecorder(nid); else openSketch(nid);
    return;
  }
  if (t.closest("#attBtn")){ document.getElementById("attAddRow")?.classList.toggle("on"); return; }
  if (t.closest("#fabVoice")){ $("fabMenu").classList.remove("on"); openRecorder(null); return; }
  if (t.closest("#fabPhoto")){ $("fabMenu").classList.remove("on"); pickPhotos(null); return; }
  if (t.closest("#fabVideo")){ $("fabMenu").classList.remove("on"); pickVideo(null); return; }
  if (t.closest("#fabSketch")){ $("fabMenu").classList.remove("on"); openSketch(null); return; }
  if (t.closest("#recBack")){ closeRecorder(); return; }
  if (t.closest("#recGo")){ recStart(); return; }
  if (t.closest("#recStop")){ recStop(); return; }
  if (t.closest("#recDiscard")){ closeRecorder(); return; }
  if (t.closest("#recSave")){ recSave(); return; }
  if (t.closest("#skBack")){ closeSketch(); return; }
  if (t.closest("#skSave")){ skSave(); return; }
  if (t.closest("#skUndo")){ const s=SK.strokes.pop(); if(s){ SK.redo.push(s); skRedraw(); skBar(); } return; }
  if (t.closest("#skRedo")){ const s=SK.redo.pop(); if(s){ SK.strokes.push(s); skRedraw(); skBar(); } return; }
  if (t.closest("#skEr")){ SK.erase=!SK.erase; skBar(); return; }
  if (t.closest("#skClr")){ if (SK.strokes.length){ SK.redo=SK.strokes.slice().reverse(); SK.strokes=[]; SK.dirty=true; skRedraw(); skBar(); } return; }
  const c = t.closest("[data-skc]"); if (c){ SK.color=c.dataset.skc; SK.erase=false; skBar(); return; }
  const z = t.closest("[data-sks]"); if (z){ SK.size=+z.dataset.sks; skBar(); return; }
  if (t.closest("#lbX")){ closeLightbox(); return; }
  const pm = t.closest("[data-pm]"); if (pm){ photoPick(pm.dataset.pm); return; }
  const vm = t.closest("[data-vm]"); if (vm){ videoPick(vm.dataset.vm); return; }
  if (t.id==="videoMenu"){ $("videoMenu").classList.remove("on"); return; }
  if (t.id==="photoMenu"){ $("photoMenu").classList.remove("on"); return; }
  if (t.closest("#lbImg")){ $("lbSc").classList.toggle("zoom"); return; }
  if (t.closest("#setImport")){ openImport(); return; }
  if (t.closest("#impBack")){ $("importSheet").classList.remove("on"); return; }
  const cs = t.closest("[data-caps-set]"); if (cs){ lsSet("hg_live_caps", cs.dataset.capsSet); renderSettings(); return; }
}, false);
document.addEventListener("change", ev=>{
  if (ev.target.id==="photoIn" || ev.target.id==="camIn") onPhotosChosen(ev.target.files);
  if (ev.target.id==="vidIn" || ev.target.id==="vidCam") onVideoChosen(ev.target.files);
  if (ev.target.id==="capsLang") lsSet("hg_caps_lang", ev.target.value);
});
(function(){
  const cv = document.getElementById("skCv");
  cv.addEventListener("pointerdown", skDown); cv.addEventListener("pointermove", skMove);
  cv.addEventListener("pointerup", skUp); cv.addEventListener("pointercancel", skUp);
  cv.addEventListener("contextmenu", e=>e.preventDefault());
  window.addEventListener("resize", ()=>{ if ($("sketchSheet").classList.contains("on")) skLayout(); });
})();

/* ── keep detail fresh while the agent works ───────────────────────────── */
function mediaPending(){ return (S.cur?.attachments||[]).some(a=>/pending|running/.test(a.transcript_status) && !a.transcript); }
function refreshDetailQuiet(){
  if (!S.cur || !$("dSheet").classList.contains("on") || audioPlaying() || attEditing()) return;
  refreshDetail();
}
setInterval(()=>{ if (mediaPending() && NET.online) refreshDetailQuiet(); }, 8000);
