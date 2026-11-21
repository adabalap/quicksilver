/* ═════════ Photos · voice · sketches · import ═════════
   Files are uploaded as multipart (never base64 in JSON). When offline they wait
   in the outbox as Blobs and upload in order after their note is created. */
const MEDIA = { urls:{}, sum:null };
async function apiForm(p, fd, method="POST"){
  let r;
  try { r = await fetch(API_BASE+"/api"+p, {method, body:fd}); }
  catch(e){ setOnline(false); throw {offline:true}; }
  setOnline(true);
  if (!r.ok){ let m=""; try{ m=(await r.json()).error||""; }catch(_){} throw {status:r.status,msg:m}; }
  return r.json();
}
const attUrl = a => a._local
  ? (MEDIA.urls[a.id] || (MEDIA.urls[a.id] = URL.createObjectURL(a._local)))
  : `${API_BASE}/api/attachments/${a.id}/file${a.updated_at?"?v="+encodeURIComponent(a.updated_at):""}`;
const attThumb = a => a._local ? attUrl(a)
  : `${API_BASE}/api/attachments/${a.id}/thumb${a.updated_at?"?v="+encodeURIComponent(a.updated_at):""}`;
const fmtDur = s => { s=Math.max(0,Math.round(s||0)); return Math.floor(s/60)+":"+String(s%60).padStart(2,"0"); };
const fmtSize = b => b>=1048576 ? (b/1048576).toFixed(1)+" MB" : Math.max(1,Math.round(b/1024))+" KB";

async function uploadAttachment(nid, kind, blob, meta={}){
  const aid = nano(10);
  meta = {...meta, id:aid};
  const fd = new FormData();
  fd.append("kind", kind); fd.append("meta", JSON.stringify(meta)); fd.append("file", blob, "upload");
  try { return await apiForm("/notes/"+nid+"/attachments", fd); }
  catch(e){
    if (e.offline){
      idb.queue({kind:"attach", nid, aid, akind:kind, blob, meta, iso:new Date().toISOString()});
      return {id:aid, _queued:true};
    }
    throw e;
  }
}
async function createMediaNote(media, items, body=""){
  const id = nano();
  try { await api("/notes",{method:"POST", body:{id, body, media}}); }
  catch(e){
    if (!e.offline){ toast("Save failed","err"); return null; }
    idb.queue({kind:"create", id, body, media, iso:new Date().toISOString()});
  }
  let failed = 0, msg = "";
  for (const it of items){
    try { await uploadAttachment(id, it.akind, it.blob, it.meta); }
    catch(e){ failed++; msg = e.msg || msg; }
  }
  if (failed) toast(msg || `${failed} file${failed>1?"s":""} couldn't be saved`, "err");
  else toast(NET.online ? "Saved — your phone will read it ✨" : OFFLINE_SAVE_MSG, "ok");
  await refreshAll();
  return {id};
}
async function attachTo(nid, kind, blob, meta){
  try { await uploadAttachment(nid, kind, blob, meta); }
  catch(e){ toast(e.msg || "Couldn't attach that", "err"); return false; }
  toast(NET.online ? "Attached ✨" : OFFLINE_SAVE_MSG, "ok");
  await refreshAll();
  if (S.cur?.id===nid) await refreshDetail();
  return true;
}

/* ── photos ─────────────────────────────────────────────────────────────── */
function scanEnhance(x, w, h){
  const im = x.getImageData(0,0,w,h), d = im.data, n = w*h, hist = new Uint32Array(256);
  for (let i=0;i<n;i++){ const g = (d[i*4]*.299 + d[i*4+1]*.587 + d[i*4+2]*.114)|0; d[i*4]=g; hist[g]++; }
  let acc=0, lo=0, hi=255; const a=n*.02, b=n*.97;
  for (let v=0;v<256;v++){ acc+=hist[v]; if (acc>=a){ lo=v; break; } }
  acc=0; for (let v=0;v<256;v++){ acc+=hist[v]; if (acc>=b){ hi=v; break; } }
  if (hi-lo < 30){ lo=Math.max(0,lo-20); hi=Math.min(255,hi+20); }
  for (let i=0;i<n;i++){
    let t = (d[i*4]-lo)/(hi-lo); t = t<0?0:t>1?1:t; t = t*t*(3-2*t);
    const v = (t*255)|0; d[i*4]=d[i*4+1]=d[i*4+2]=v; d[i*4+3]=255;
  }
  x.putImageData(im,0,0);
}
async function prepImage(file, max=2000, q=.85, scan=false){
  if (scan) max = 2400;
  const okType = /^image\/(jpeg|png|webp|gif)$/.test(file.type);
  if (file.type==="image/gif" && okType && !scan) return {blob:file};
  try {
    // Decode already shrunk: a 50 MP camera shot would otherwise need ~200 MB of RAM at full size,
    // which on a busy phone ends in "unable to perform operation due to low memory".
    let bmp;
    try { bmp = await createImageBitmap(file, {imageOrientation:"from-image", resizeWidth:max, resizeQuality:"medium"}); }
    catch(_){ bmp = await createImageBitmap(file, {imageOrientation:"from-image"}); }
    const k = Math.min(1, max/Math.max(bmp.width,bmp.height));
    const w = Math.round(bmp.width*k), h = Math.round(bmp.height*k);
    const c = document.createElement("canvas"); c.width=w; c.height=h;
    const x = c.getContext("2d"); x.fillStyle="#fff"; x.fillRect(0,0,w,h); x.drawImage(bmp,0,0,w,h);
    if (bmp.close) bmp.close();
    if (scan) scanEnhance(x, w, h);
    const blob = await new Promise(r=>c.toBlob(r,"image/jpeg",q));
    c.width = c.height = 0;                       // hand the canvas memory back at once
    if (blob) return {blob, width:w, height:h};
  } catch(_){}
  if (okType && file.size <= 25*1048576) return {blob:file};
  return null;
}
function pickPhotos(nid){ S._photoTarget = nid || null; $("photoMenu").classList.add("on"); }
function photoPick(mode){
  $("photoMenu").classList.remove("on");
  S._scan = mode==="scan";
  const i = $(mode==="gal" ? "photoIn" : "camIn"); i.value = ""; i.click();
}
async function onPhotosChosen(files){
  files = [...files].slice(0, S._photoTarget ? 12 : 12);
  if (!files.length) return;
  toast("Preparing "+(files.length>1?files.length+" photos…":"photo…"));
  const items = [];
  for (const f of files){
    const p = await prepImage(f, 2000, .85, !!S._scan);
    if (p) items.push({akind:"image", blob:p.blob, meta:{width:p.width, height:p.height}});
  }
  if (!items.length){ toast("Couldn't read that image","err"); return; }
  const t = S._photoTarget; S._photoTarget = null; S._scan = false;
  if (t){ let ok=0; for (const it of items) if (await attachTo(t,it.akind,it.blob,it.meta)) ok++; return; }
  await createMediaNote("photo", items);
}

/* ── video ──────────────────────────────────────────────────────────────── */
const VIDEO_MAX_S = 120, VIDEO_MAX_B = 100*1048576;
function pickVideo(nid){ S._videoTarget = nid || null; $("videoMenu").classList.add("on"); }
function videoPick(mode){
  $("videoMenu").classList.remove("on");
  const i = $(mode==="gal" ? "vidIn" : "vidCam"); i.value = ""; i.click();
}
function videoInfo(file){
  return new Promise(res=>{
    const v = document.createElement("video"), u = URL.createObjectURL(file); let done = false;
    const fin = x=>{ if (done) return; done = true; URL.revokeObjectURL(u); res(x); };
    v.preload = "metadata"; v.muted = true;
    v.onloadedmetadata = ()=>fin({duration_s: isFinite(v.duration)?v.duration:0, width:v.videoWidth||null, height:v.videoHeight||null});
    v.onerror = ()=>fin({duration_s:0});
    setTimeout(()=>fin({duration_s:0}), 5000); v.src = u;
  });
}
async function onVideoChosen(files){
  const f = files && files[0]; if (!f) return;
  const t = S._videoTarget; S._videoTarget = null;
  if (f.size > VIDEO_MAX_B){ toast("That video is over 100 MB. Try a shorter clip.","err"); return; }
  const info = await videoInfo(f);
  if (info.duration_s > VIDEO_MAX_S + 3){ toast("Videos can be up to 2 minutes. Trim it and try again.","err"); return; }
  const type = (f.type && f.type.startsWith("video/")) ? f.type : "video/mp4";
  const blob = f.type===type ? f : new Blob([f], {type});
  const meta = {duration_s:info.duration_s||null, width:info.width, height:info.height};
  toast("Saving video…");
  if (t){ await attachTo(t, "video", blob, meta); return; }
  await createMediaNote("video", [{akind:"video", blob, meta}]);
}

/* ── voice recorder ─────────────────────────────────────────────────────── */
const R = { st:"idle", target:null };
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
const capsOn = () => !!SR && lsGet("hg_live_caps")==="1";
const MIC_SVG = `<svg width="38" height="38" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v4"/></svg>`;
const STOP_SVG = `<svg width="34" height="34" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>`;
function openRecorder(nid){
  if (!nid) closeOverlays(); R.target = nid || null; R.st = "ready"; R.blob = null; R.caps = ""; R.err = "";
  recRender(); $("recSheet").classList.add("on");
}
function recRender(){
  const b = $("recBody");
  if (R.st==="ready") b.innerHTML = `<div class="rec-time">0:00</div>
    <div class="rec-hint">${R.err ? esc(R.err) : "Tap to start. It's saved on your phone and read by your agent — nothing goes to the cloud."}</div>
    <button class="rec-btn" id="recGo" aria-label="Start recording">${MIC_SVG}</button>
    ${capsOn() ? `<div class="rec-hint" style="font-size:11.5px">Live captions are on (browser speech service).</div>` : ""}`;
  else if (R.st==="live") b.innerHTML = `<div class="rec-time" id="recT">0:00</div>
    <canvas class="rec-wave" id="recWave" width="640" height="112"></canvas>
    ${capsOn() ? `<div class="rec-caps" id="recCaps"><i>Listening…</i></div>` : `<div class="rec-hint">Recording… keep this screen open.</div>`}
    <button class="rec-btn live" id="recStop" aria-label="Stop">${STOP_SVG}</button>`;
  else if (R.st==="review") b.innerHTML = `<div class="rec-review" style="display:flex;flex-direction:column;align-items:center;gap:16px">
    <div class="rec-time">${fmtDur(R.dur)}</div>
    <audio controls src="${R.url}"></audio>
    ${R.caps ? `<div class="rec-caps">${esc(R.caps)}</div>` : ""}
    <div class="rec-act"><button class="imp-btn ghost" id="recDiscard" style="margin:0">Discard</button>
    <button class="imp-btn" id="recSave" style="margin:0">Save</button></div></div>`;
}
async function recStart(){
  if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder==="undefined"){
    R.err = "Recording needs a secure (https) connection and a browser that supports it."; recRender(); return; }
  let stream;
  try { stream = await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true}}); }
  catch(e){ R.err = "Microphone access was blocked. Allow it in your browser's site settings, then try again."; recRender(); return; }
  const mime = ["audio/webm;codecs=opus","audio/webm","audio/mp4","audio/ogg;codecs=opus"]
    .find(m=>MediaRecorder.isTypeSupported(m)) || "";
  let rec;
  try { rec = new MediaRecorder(stream, mime?{mimeType:mime}:undefined); }
  catch(e){ stream.getTracks().forEach(t=>t.stop()); R.err="This browser can't record audio."; recRender(); return; }
  R.stream = stream; R.rec = rec; R.chunks = []; R.t0 = Date.now(); R.caps = ""; R.interim = "";
  rec.ondataavailable = e=>{ if (e.data && e.data.size) R.chunks.push(e.data); };
  rec.onstop = ()=>recFinish();
  rec.start(1000);
  R.st = "live"; recRender();
  try { R.wake = await navigator.wakeLock?.request("screen"); } catch(_){}
  try {
    const AC = window.AudioContext || window.webkitAudioContext;
    R.ctx = new AC(); const src = R.ctx.createMediaStreamSource(stream);
    R.an = R.ctx.createAnalyser(); R.an.fftSize = 256; src.connect(R.an);
  } catch(_){ R.an = null; }
  R.tick = setInterval(()=>{
    const s = (Date.now()-R.t0)/1000, el=$("recT"); if (el) el.textContent = fmtDur(s);
    if (s >= 1800) recStop();
  }, 250);
  recWaveLoop();
  if (capsOn()) recCapsStart();
}
function recWaveLoop(){
  const cv = $("recWave"); if (!cv || R.st!=="live") return;
  const x = cv.getContext("2d"), w = cv.width, h = cv.height;
  x.clearRect(0,0,w,h);
  const col = getComputedStyle(document.documentElement).getPropertyValue("--violet").trim() || "#7c5cff";
  x.fillStyle = col;
  const n = 40; let data = new Uint8Array(R.an ? R.an.frequencyBinCount : 0);
  if (R.an) R.an.getByteFrequencyData(data);
  for (let i=0;i<n;i++){
    const v = data.length ? data[Math.floor(i*data.length/n/1.6)]/255 : .05;
    const bh = Math.max(6, v*h*.95), bw = w/n*.6;
    x.fillRect(i*(w/n)+(w/n-bw)/2, (h-bh)/2, bw, bh);
  }
  R.raf = requestAnimationFrame(recWaveLoop);
}
function recCapsStart(){
  try {
    const sr = new SR(); R.sr = sr; sr.continuous = true; sr.interimResults = true;
    sr.lang = lsGet("hg_caps_lang") || navigator.language || "en-US";
    sr.onresult = ev=>{
      let fin = "", interim = "";
      for (let i=ev.resultIndex;i<ev.results.length;i++){
        const r = ev.results[i]; if (r.isFinal) fin += r[0].transcript; else interim += r[0].transcript; }
      if (fin) R.caps += (R.caps && !/\s$/.test(R.caps) ? " " : "") + fin.trim();
      R.interim = interim;
      const el = $("recCaps"); if (el) el.innerHTML = esc(R.caps) + (interim?` <i>${esc(interim)}</i>`:"") || "<i>Listening…</i>";
    };
    sr.onend = ()=>{ if (R.st==="live" && R.sr===sr){ try{ sr.start(); }catch(_){} } };
    sr.onerror = ()=>{};
    sr.start();
  } catch(_){ R.sr = null; }
}
function recStop(){
  if (R.st!=="live") return;
  try { R.rec.stop(); } catch(_){ recFinish(); }
}
function recCleanup(){
  clearInterval(R.tick); cancelAnimationFrame(R.raf);
  try { if (R.sr){ const s=R.sr; R.sr=null; s.onend=null; s.stop(); } } catch(_){}
  try { R.stream?.getTracks().forEach(t=>t.stop()); } catch(_){}
  try { R.ctx?.close(); } catch(_){}
  try { R.wake?.release(); } catch(_){}
  R.stream = R.ctx = R.an = R.wake = null;
}
function recFinish(){
  const dur = (Date.now()-R.t0)/1000;
  recCleanup();
  const type = (R.chunks[0]?.type || R.rec?.mimeType || "audio/webm");
  R.blob = new Blob(R.chunks, {type}); R.dur = dur;
  if (R.url) URL.revokeObjectURL(R.url);
  R.url = URL.createObjectURL(R.blob);
  if (R.blob.size < 800 || dur < 0.7){ R.st="ready"; R.err="That was too short — try again."; recRender(); return; }
  R.st = "review"; recRender();
}
async function recSave(){
  const blob = R.blob, meta = {duration_s: Math.round(R.dur*10)/10};
  const caps = (R.caps||"").trim();
  if (caps) meta.transcript = caps;
  const t = R.target;
  recReset();
  if (t) await attachTo(t, "audio", blob, meta);
  else await createMediaNote("voice", [{akind:"audio", blob, meta}]);
}
function recReset(){
  recCleanup(); R.st="idle"; R.blob=null; R.caps="";
  if (R.url){ URL.revokeObjectURL(R.url); R.url=null; }
  $("recSheet").classList.remove("on");
}
async function closeRecorder(){
  const viaPop = _closingViaPop;
  if (R.st==="live" || R.st==="review"){
    const ok = await showConfirm("Throw away this recording?",{title:"Discard", okText:"Discard", danger:true});
    if (!ok){ if (viaPop) pushLayerState(); return; }
  }
  recReset();
}

/* ── sketch pad ─────────────────────────────────────────────────────────── */
const SK = { W:1000, H:1300, strokes:[], redo:[], color:"#1a1a2e", size:5, erase:false, cur:null, pen:false, edit:null, target:null, dirty:false };
const SK_COLORS = ["#1a1a2e","#2f5bea","#d6336c","#2b8a3e","#e8590c"];
function openSketch(nid, editAtt){
  if (!nid) closeOverlays(); SK.target = nid || null; SK.edit = editAtt || null; SK.strokes=[]; SK.redo=[]; SK.dirty=false;
  SK.erase=false; SK.pen=false; SK.W=1000; SK.H=1300; SK.color=SK_COLORS[0]; SK.size=5;
  $("sketchSheet").classList.add("on");
  skBar();
  requestAnimationFrame(async ()=>{
    if (editAtt){
      try {
        const r = await api("/attachments/"+editAtt.id+"/strokes");
        const d = r.strokes;
        if (d && d.s){ SK.strokes = d.s; SK.W = d.w || 1000; SK.H = d.h || 1300; }
        else toast("This sketch has no stroke data to edit","err");
      } catch(e){ toast("Connect to edit this sketch","err"); }
    }
    skLayout();
  });
}
function skBar(){
  const sizes = [[3,6],[6,10],[12,16]];
  $("skBar").innerHTML =
    `<button class="sk-tool" id="skUndo" aria-label="Undo" ${SK.strokes.length?"":"disabled"}>↶</button>`
   +`<button class="sk-tool" id="skRedo" aria-label="Redo" ${SK.redo.length?"":"disabled"}>↷</button>`
   +`<button class="sk-tool${SK.erase?" on":""}" id="skEr" aria-label="Eraser">⌫</button>`
   +`<button class="sk-tool" id="skClr" aria-label="Clear all">🗑</button><span class="sk-sep"></span>`
   + SK_COLORS.map(c=>`<button class="sk-dot${!SK.erase&&SK.color===c?" on":""}" data-skc="${c}" style="background:${c}" aria-label="Colour"></button>`).join("")
   +`<span class="sk-sep"></span>`
   + sizes.map(([v,d])=>`<button class="sk-sz${SK.size===v?" on":""}" data-sks="${v}" aria-label="Pen size"><i style="width:${d}px;height:${d}px"></i></button>`).join("");
}
function skLayout(){
  const wrap = $("skWrap"), cv = $("skCv");
  const aw = wrap.clientWidth-20, ah = wrap.clientHeight-20;
  SK.k = Math.max(.1, Math.min(aw/SK.W, ah/SK.H));
  const cssW = Math.floor(SK.W*SK.k), cssH = Math.floor(SK.H*SK.k), dpr = Math.min(2, window.devicePixelRatio||1);
  cv.style.width = cssW+"px"; cv.style.height = cssH+"px";
  cv.width = Math.round(cssW*dpr); cv.height = Math.round(cssH*dpr);
  SK.scale = cv.width/SK.W;
  skRedraw();
}
function skDrawStroke(x, s, scale){
  const p = s.p; if (!p.length) return;
  x.strokeStyle = s.c; x.fillStyle = s.c; x.lineCap = "round"; x.lineJoin = "round";
  if (p.length===1){ x.beginPath(); x.arc(p[0][0]*scale, p[0][1]*scale, s.k*scale*(.45+p[0][2]*.55), 0, 7); x.fill(); return; }
  for (let i=1;i<p.length;i++){
    x.lineWidth = s.k*scale*(.45+1.1*((p[i][2]+p[i-1][2])/2));
    x.beginPath(); x.moveTo(p[i-1][0]*scale, p[i-1][1]*scale); x.lineTo(p[i][0]*scale, p[i][1]*scale); x.stroke();
  }
}
function skRedraw(){
  const cv = $("skCv"), x = cv.getContext("2d");
  x.fillStyle="#fff"; x.fillRect(0,0,cv.width,cv.height);
  SK.strokes.forEach(s=>skDrawStroke(x,s,SK.scale));
}
function skPt(e){
  const r = $("skCv").getBoundingClientRect();
  const pr = e.pointerType==="pen" ? (e.pressure||.5) : .5;
  return [Math.round((e.clientX-r.left)/SK.k*10)/10, Math.round((e.clientY-r.top)/SK.k*10)/10, Math.round(pr*100)/100];
}
function skEraseAt(pt){
  const rad = 14, before = SK.strokes.length;
  SK.strokes = SK.strokes.filter(s=>{
    const p = s.p;
    for (let i=0;i<p.length;i++){
      let ax=p[i][0], ay=p[i][1], bx=p[Math.min(i+1,p.length-1)][0], by=p[Math.min(i+1,p.length-1)][1];
      const dx=bx-ax, dy=by-ay, L=dx*dx+dy*dy;
      let t = L? ((pt[0]-ax)*dx+(pt[1]-ay)*dy)/L : 0; t=Math.max(0,Math.min(1,t));
      if (Math.hypot(pt[0]-(ax+t*dx), pt[1]-(ay+t*dy)) < rad+s.k/2) return false;
    }
    return true;
  });
  if (SK.strokes.length !== before){ SK.redo=[]; SK.dirty=true; skRedraw(); skBar(); }
}
function skDown(e){
  if (e.pointerType==="pen") SK.pen = true;
  if (e.pointerType==="touch" && SK.pen) return;            // palm rejection once a pen has been seen
  if (e.pointerType==="mouse" && e.button!==0) return;
  e.preventDefault();
  try { $("skCv").setPointerCapture(e.pointerId); } catch(_){}
  SK.pid = e.pointerId;
  const pt = skPt(e);
  if (SK.erase){ SK.erasing = true; skEraseAt(pt); return; }
  SK.cur = {c:SK.color, k:SK.size, p:[pt]};
  skDrawStroke($("skCv").getContext("2d"), SK.cur, SK.scale);
}
function skMove(e){
  if (e.pointerId !== SK.pid) return;
  e.preventDefault();
  const evs = e.getCoalescedEvents ? e.getCoalescedEvents() : [e];
  for (const ev of (evs.length?evs:[e])){
    const pt = skPt(ev);
    if (SK.erasing){ skEraseAt(pt); continue; }
    if (!SK.cur) return;
    const last = SK.cur.p[SK.cur.p.length-1];
    if (Math.hypot(pt[0]-last[0], pt[1]-last[1]) < .6) continue;
    SK.cur.p.push(pt);
    const x = $("skCv").getContext("2d"), n = SK.cur.p.length;
    skDrawStroke(x, {c:SK.cur.c,k:SK.cur.k,p:SK.cur.p.slice(n-2)}, SK.scale);
  }
}
function skUp(e){
  if (e.pointerId !== SK.pid) return;
  SK.pid = null; SK.erasing = false;
  if (SK.cur){ SK.strokes.push(SK.cur); SK.cur = null; SK.redo = []; SK.dirty = true; skBar(); }
}
function skExport(){
  const out = document.createElement("canvas"), sc = 1600/SK.W;
  out.width = 1600; out.height = Math.round(SK.H*sc);
  const x = out.getContext("2d"); x.fillStyle="#fff"; x.fillRect(0,0,out.width,out.height);
  SK.strokes.forEach(s=>skDrawStroke(x,s,sc));
  return new Promise(r=>out.toBlob(r,"image/png"));
}
async function skSave(){
  if (!SK.strokes.length){ toast("Nothing drawn yet"); return; }
  const btn = $("skSave"); btn.disabled = true;
  try {
    const blob = await skExport();
    const meta = {width:1600, height:Math.round(SK.H*1.6), strokes:{w:SK.W,h:SK.H,s:SK.strokes}};
    if (JSON.stringify(meta.strokes).length > 1.9e6){ toast("That sketch is too detailed to save","err"); return; }
    const t = SK.target, ed = SK.edit;
    SK.dirty = false; $("sketchSheet").classList.remove("on");
    if (ed){
      const fd = new FormData(); fd.append("kind","sketch"); fd.append("meta",JSON.stringify(meta)); fd.append("file",blob,"sketch.png");
      try { await apiForm("/attachments/"+ed.id, fd, "PUT"); toast("Sketch updated","ok"); }
      catch(e){ toast(e.offline ? "Connect to save sketch changes" : (e.msg||"Couldn't save"), "err"); }
      await refreshAll(); if (S.cur) await refreshDetail();
    }
    else if (t) await attachTo(t, "sketch", blob, meta);
    else await createMediaNote("sketch", [{akind:"sketch", blob, meta}]);
  } finally { btn.disabled = false; }
}
async function closeSketch(){
  const viaPop = _closingViaPop;
  if (SK.dirty && SK.strokes.length){
    const ok = await showConfirm("Discard this sketch?",{title:"Discard", okText:"Discard", danger:true});
    if (!ok){ if (viaPop) pushLayerState(); return; }
  }
  SK.dirty = false; $("sketchSheet").classList.remove("on");
}

/* ── lightbox ───────────────────────────────────────────────────────────── */
function openLightbox(a){
  const im = $("lbImg"); im.src = attUrl(a);
  $("lbSc").classList.remove("zoom"); $("lbSc").classList.toggle("skp", a.kind==="sketch");
  $("lightbox").classList.add("on");
}
function closeLightbox(){ $("lightbox").classList.remove("on"); $("lbImg").removeAttribute("src"); }
