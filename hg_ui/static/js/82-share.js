/* ═════════ Share / export ═════════ */
const SH = { doc:null };
const slug = s => (s||"note").toLowerCase().replace(/[^a-z0-9ఀ-౿]+/g,"-").replace(/^-+|-+$/g,"").slice(0,48) || "note";
function noteToDoc(n){
  const title = noteTitle(n), body = (n.body||"").trim();
  const A = n.attachments||[], tags = (n.tags||[]).map(t=>"#"+t).join(" ");
  let md = `# ${title}\n\n${body}\n`;
  const tx = A.filter(a=>(a.transcript||"").trim());
  tx.forEach(a=>{ md += `\n## ${TXL[a.kind]||"Text"}\n\n${a.transcript.trim()}\n`; });
  if (tags) md += `\n${tags}\n`;
  const imgUrls = A.filter(a=>a.kind!=="audio" && !a._local).map(attUrl);
  const html = `<h1>${esc(title)}</h1><!--IMGS-->${mdLite(body)}`
    + tx.map(a=>`<h2>${esc(TXL[a.kind]||"Text")}</h2>${mdLite(a.transcript.trim())}`).join("")
    + (tags ? `<p class="tg">${esc(tags)}</p>` : "");
  return {title, md, html, imgUrls};
}
function textToDoc(title, text, sources, imgUrls){
  const clean = (text||"").replace(/\s*\[\d+(?:\s*[,;]\s*\d+)*\]/g,"").trim();
  let md = clean, html = mdLite(clean);
  const hasH1 = /^# /.test(clean);
  if (!hasH1){ md = `# ${title}\n\n${md}`; html = `<h1>${esc(title)}</h1>${html}`; }
  if (sources && sources.length){
    md += `\n\n---\nSources: ${sources.join("; ")}`;
    html += `<p class="tg">Sources: ${esc(sources.join("; "))}</p>`;
  }
  const h1 = clean.match(/^# (.+)$/m);
  if (imgUrls && imgUrls.length) html = html.replace(/<\/h1>/, "</h1><!--IMGS-->");
  return {title: h1 ? h1[1] : title, md, html, imgUrls: imgUrls||[]};
}
/* Photos for export: fetched and shrunk to JPEG once, so the print view and the
   share sheet never depend on loading remote images at the last second. */
async function loadDocImages(doc){
  if (doc._imgs) return doc._imgs;
  const out = [];
  for (const u of (doc.imgUrls||[]).slice(0,6)){
    try {
      const blob = await (await fetch(u)).blob();
      const bmp = await createImageBitmap(blob);
      const k = Math.min(1, 1100/Math.max(bmp.width, bmp.height));
      const cv = document.createElement("canvas"); cv.width = Math.round(bmp.width*k); cv.height = Math.round(bmp.height*k);
      cv.getContext("2d").drawImage(bmp, 0, 0, cv.width, cv.height);
      const jb = await new Promise(r=>cv.toBlob(r, "image/jpeg", .85));
      const url = await new Promise(r=>{ const fr = new FileReader(); fr.onload = ()=>r(fr.result); fr.readAsDataURL(jb); });
      out.push({blob:jb, url});
    } catch(e){}
  }
  return doc._imgs = out;
}
const docHtmlWithImages = (doc, imgs) => doc.html.replace("<!--IMGS-->", imgs.map(i=>`<img src="${i.url}" alt="">`).join(""));
function shareDoc(doc){
  SH.doc = doc; $("shareSheet").classList.add("on"); loadDocImages(doc);
  const canShare = !!navigator.share;
  $("shareBody").innerHTML = `<div class="tr-note" style="margin-bottom:12px">${esc(doc.title)}</div>`
    + (canShare ? `<button class="sh-row" data-sh="share">↗ <span>Share…<small>Send it to another app</small></span></button>` : "")
    + `<button class="sh-row" data-sh="md">⬇ <span>Save as Markdown (.md)<small>A plain text file you can open anywhere</small></span></button>`
    + `<button class="sh-row" data-sh="pdf">🖨 <span>Save as PDF<small>Opens the print screen — choose “Save as PDF”</small></span></button>`
    + `<button class="sh-row" data-sh="copy">📋 <span>Copy text</span></button>`;
}
function downloadBlob(blob, name){
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = name;
  document.body.appendChild(a); a.click(); a.remove(); setTimeout(()=>URL.revokeObjectURL(a.href), 4000);
}
function printHtml(title, html){
  const f = document.createElement("iframe");
  f.style.cssText = "position:fixed;right:0;bottom:0;width:0;height:0;border:0";
  document.body.appendChild(f);
  const d = f.contentWindow.document;
  d.open(); d.write(`<!doctype html><html><head><meta charset="utf-8"><title>${esc(title)}</title><style>
    body{font:16px/1.6 Georgia,'Noto Serif Telugu',serif;max-width:720px;margin:24px auto;padding:0 18px;color:#111}
    h1{font-size:26px;margin:0 0 14px} h2{font-size:19px;margin:22px 0 6px} h3{font-size:16px}
    img{max-width:100%;height:auto;margin:8px 0;border-radius:6px} p{margin:0 0 10px}
    .tg{color:#666;font-size:13px;margin-top:22px} ul{padding-left:22px}</style></head><body>${html}</body></html>`);
  d.close();
  const go = ()=>{ try { f.contentWindow.focus(); f.contentWindow.print(); } catch(e){ toast("Couldn't open the print screen","err"); }
    setTimeout(()=>f.remove(), 60000); };
  const imgs = [...d.images].filter(i=>!i.complete);
  if (!imgs.length) setTimeout(go, 150);
  else Promise.race([Promise.all(imgs.map(i=>new Promise(r=>{ i.onload = i.onerror = r; }))),
                     new Promise(r=>setTimeout(r, 4000))]).then(go);
}
async function shareClick(act){
  const d = SH.doc; if (!d) return;
  if (act==="share"){
    const im = await loadDocImages(d);
    const files = im.map((i,n)=>new File([i.blob], slug(d.title)+(im.length>1?"-"+(n+1):"")+".jpg", {type:"image/jpeg"}));
    const tries = [];
    if (files.length && navigator.canShare && navigator.canShare({files})) tries.push({title:d.title, text:d.md, files});
    tries.push({title:d.title, text:d.md});
    let last = null;
    for (const data of tries){
      try { await navigator.share(data); return; }
      catch(e){ if (e.name==="AbortError") return; last = e; }
    }
    toast("Couldn't share"+(last?` (${last.name})`:""),"err");
  } else if (act==="md"){ downloadBlob(new Blob([d.md],{type:"text/markdown"}), slug(d.title)+".md"); toast("Saved ⬇","ok"); }
  else if (act==="pdf"){ const im = await loadDocImages(d); printHtml(d.title, docHtmlWithImages(d, im)); }
  else if (act==="copy"){ try { await navigator.clipboard.writeText(d.md); toast("Copied 📋","ok"); } catch(e){ toast("Copy failed","err"); } }
}
