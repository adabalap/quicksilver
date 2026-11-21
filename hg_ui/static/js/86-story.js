/* ═════════ Story from a photo ═════════ */
const ST_GENRES = [["","Surprise me"],["fairy tale","Fairy tale"],["mystery","Mystery"],["funny","Funny"],["heartfelt","Heartfelt"],["adventure","Adventure"],["sci-fi","Sci-fi"]];
const ST_TWEAKS = ["Shorter","Longer","Funnier","Sadder","Add a twist","More dialogue","Simpler words","Happier ending"];
const ST = { nid:null, outline:"", genre:"", length:"short", lang:"en", status:"form", id:null, text:"", error:"", err409:false, prev:[], revising:false };
function openStory(nid){
  const n = (S.cur && S.cur.id===nid) ? S.cur : null; if (!n) return;
  Object.assign(ST, {nid, status:"form", id:null, text:"", error:"", err409:false, prev:[], revising:false});
  $("storySheet").classList.add("on"); renderStory();
}
function storyThumbs(){
  const n = S.cur; if (!n || n.id!==ST.nid) return "";
  const v = (n.attachments||[]).filter(a=>a.kind!=="audio");
  return v.length ? `<div class="st-thumbs">${v.map(a=>`<img alt="" src="${esc(attThumb(a))}" data-full="${esc(attUrl(a))}"/>`).join("")}</div>` : "";
}
function renderStory(){
  const b = $("storyBody");
  const chips = (list,key) => `<div class="fl-chips">${list.map(([v,l])=>
    `<button type="button" data-st="${key}" data-v="${esc(v)}" class="${ST[key]===v?"on":""}">${esc(l)}</button>`).join("")}</div>`;
  if (ST.status==="form" || ST.status==="error"){
    b.innerHTML = storyThumbs()
      + (ST.status==="error" ? `<div class="hl-warn">${esc(ST.error)}${ST.err409?` <button class="att-lnk" data-st="describe">Describe the photo now</button>`:""}</div>` : "")
      + `<div class="fl-sec" style="margin-top:4px">Your idea (optional)</div>
         <textarea class="st-in" id="stOutline" maxlength="600" placeholder="A few lines: who is in it, what happens, how it ends… Leave empty and the agent will make one up from the photo.">${esc(ST.outline)}</textarea>
         <div class="fl-sec">Style</div>${chips(ST_GENRES,"genre")}
         <div class="fl-sec">Length</div>${chips([["short","Short (~180 words)"],["medium","Longer (~380 words)"]],"length")}
         <div class="fl-sec">Language</div>${chips([["en","English"],["te","తెలుగు"]],"lang")}
         ${ST.lang==="te"?`<div class="tr-note" style="margin-top:8px">Telugu stories are slower and can be rougher than English ones.</div>`:""}
         <div class="fl-foot"><button class="pri" data-st="go">✍ Write the story</button></div>`;
  } else if (ST.status==="pending"){
    b.innerHTML = storyThumbs() + `<div class="ask-think" style="margin:18px 2px"><span class="ask-dot"></span><span class="ask-dot"></span><span class="ask-dot"></span>
      ${ST.revising?"Revising your story":"Writing your story"}… this can take a minute or two. You can close this and come back — it will be waiting.</div>`;
  } else {
    const t = ST.text;
    b.innerHTML = storyThumbs() + `<div class="st-story">${mdLite(t)}</div>
      <div class="fl-sec">Change something</div>
      <div class="fl-chips">${ST_TWEAKS.map(x=>`<button type="button" data-st="tweak" data-v="${esc(x)}">${esc(x)}</button>`).join("")}</div>
      <div style="display:flex;gap:8px;margin-top:10px"><input class="st-in" id="stTweak" style="min-height:0;flex:1" maxlength="300" placeholder="Or say it: make the fox a girl, end with a twist…"/>
        <button class="pri" data-st="revise" style="flex:0 0 auto;border-radius:12px;padding:0 16px;border:none;background:var(--violet);color:#fff;font:700 14px var(--fb)">Revise</button></div>
      ${ST.prev.length?`<button class="att-lnk" data-st="undo" style="margin-top:8px">↩ Back to the previous version (${ST.prev.length})</button>`:""}
      <div class="fl-foot" style="flex-wrap:wrap"><button class="pri" data-st="append">Add to this note</button>
        <button data-st="newnote">Save as new note</button><button data-st="share">Share</button><button data-st="again">Write another</button></div>`;
  }
}
function storyPoll(id){
  const t0 = Date.now();
  const tick = async ()=>{
    if (ST.id !== id || ST.status !== "pending") return;
    if (Date.now()-t0 > 900000){ ST.status="error"; ST.error="This is taking longer than expected. The agent may be busy or offline."; renderStory(); return; }
    if (!document.hidden){ try { storyApply(await api("/ask/"+id)); } catch(e){} }
    if (ST.status === "pending") setTimeout(tick, 4000);
  };
  setTimeout(tick, 5000);
}
function storyApply(d){
  if (!d || d.request_id !== ST.id || ST.status !== "pending") return;
  if (d.status==="done" && d.answer){ ST.status="done"; ST.text=d.answer; ST.revising=false; if ($("storySheet").classList.contains("on")) renderStory(); else toast("✍ Your story is ready","ok"); }
  else if (d.status==="error"){
    if (ST.revising && ST.prev.length){ ST.text=ST.prev.pop(); ST.status="done"; ST.revising=false; renderStory(); toast(d.error||"Couldn't revise the story","err"); }
    else { ST.status="error"; ST.error=d.error||"Couldn't write a story."; ST.err409=false; renderStory(); } }
}
function storyTitle(){ const m = (ST.text||"").match(/^# (.+)$/m); return m ? m[1] : "Story"; }
async function storyRevise(instr){
  instr = (instr||"").trim();
  if (!instr){ toast("Say what to change","err"); return; }
  const cur = ST.text;
  try {
    const r = await api(`/notes/${ST.nid}/story`, {method:"POST", body:{outline:ST.outline, genre:ST.genre, length:ST.length, lang:ST.lang, revise:cur, instruction:instr}});
    ST.prev.push(cur); ST.id = r.request_id; ST.status = "pending"; ST.revising = true; renderStory(); storyPoll(ST.id);
  } catch(e){ toast(e.offline ? "You're offline" : "Couldn't start the revision","err"); }
}
async function storyClick(t){
  const b = t.closest("[data-st]"); if (!b) return false;
  const k = b.dataset.st;
  if (k==="genre"||k==="length"||k==="lang"){ ST.outline = $("stOutline")?.value ?? ST.outline; ST[k]=b.dataset.v; renderStory(); return true; }
  if (k==="go"){
    ST.outline = $("stOutline")?.value ?? ST.outline;
    try {
      const r = await api(`/notes/${ST.nid}/story`, {method:"POST", body:{outline:ST.outline, genre:ST.genre, length:ST.length, lang:ST.lang}});
      ST.id = r.request_id; ST.status = "pending"; renderStory(); storyPoll(ST.id);
    } catch(e){
      ST.status = "error"; ST.err409 = e.status===409;
      ST.error = e.offline ? "You're offline — the agent can't be reached." :
        (e.status===409 ? (e.data?.pending ? "The photo is still being read — try again in a moment." : "No description of this photo is available (the agent only reads text in photos right now). Type a few lines of your idea above and tap Write the story again.") : "Couldn't start the story.");
      renderStory();
    }
    return true; }
  if (k==="describe"){
    const a = (S.cur?.attachments||[]).find(x=>x.kind!=="audio");
    if (a){ try { await api(`/attachments/${a.id}/retranscribe`,{method:"POST"}); toast("Reading the photo… try again in a moment","ok"); } catch(e){ toast("Couldn't start that","err"); } }
    return true; }
  if (k==="tweak"){ const i=$("stTweak"); if (i){ i.value=b.dataset.v; } await storyRevise(b.dataset.v); return true; }
  if (k==="revise"){ await storyRevise($("stTweak")?.value || ""); return true; }
  if (k==="undo"){ const p=ST.prev.pop(); if (p){ ST.text=p; renderStory(); } return true; }
  if (k==="again"){ ST.status="form"; ST.text=""; renderStory(); return true; }
  if (k==="share"){ shareDoc(textToDoc(storyTitle(), ST.text, [], (S.cur?.id===ST.nid ? (S.cur.attachments||[]).filter(a=>a.kind!=="audio"&&!a._local).map(attUrl) : []))); return true; }
  if (k==="newnote"){ const m = await createNote(ST.text, "off"); if (m) toast("Saved as a new note","ok"); return true; }
  if (k==="append"){
    const n = S.cur; if (!n || n.id!==ST.nid){ toast("Open the note first","err"); return true; }
    try { const up = await patchNote(n.id, {body: (n.body||"").replace(/\s+$/,"") + "\n\n" + ST.text, commit:true});
      toast("Added to the note","ok"); $("storySheet").classList.remove("on"); if (up && !Ed.open) renderDetail(up); }
    catch(e){}
    return true; }
  return true;
}
