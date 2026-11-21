/* ═════════ Filters · saved searches ═════════ */
const FIL_TYPES = [["","Any"],["photo","Photos"],["voice","Voice notes"],["video","Videos"],["sketch","Sketches"],["text","Text only"],["task","Open tasks"]];
const FIL_RANGES = [["","Any time"],["today","Today"],["week","Last 7 days"],["month","Last 30 days"],["year","This year"]];
const filOn = () => !!(S.fil.type || S.fil.range || S.fil.tag);
function filSince(r){
  const d = new Date();
  if (r==="today") d.setHours(0,0,0,0);
  else if (r==="week") d.setDate(d.getDate()-7);
  else if (r==="month") d.setDate(d.getDate()-30);
  else if (r==="year") return new Date(d.getFullYear(),0,1).toISOString();
  return d.toISOString();
}
function filQS(){
  const f = S.fil, p = [];
  if (f.type) p.push("type="+encodeURIComponent(f.type));
  if (f.tag) p.push("tag="+encodeURIComponent(f.tag));
  if (f.range) p.push("since="+encodeURIComponent(filSince(f.range)));
  return p.join("&");
}
function filLabel(f=S.fil){
  const bits = [];
  if (f.type) bits.push((FIL_TYPES.find(x=>x[0]===f.type)||[0,f.type])[1]);
  if (f.range) bits.push((FIL_RANGES.find(x=>x[0]===f.range)||[0,f.range])[1]);
  if (f.tag) bits.push("#"+f.tag);
  return bits.join(" · ") || "Filters";
}
function filSync(){ $("filtBtn")?.classList.toggle("act", filOn()); }
let FD = {type:"",range:"",tag:""};
async function openFilters(){
  closeMenu(); FD = {...S.fil};
  $("filterSheet").classList.add("on");
  renderFilters();
  try { const [t, sv] = await Promise.all([api("/tags"), api("/saved-searches")]);
    FD._tags = (t.tags||[]).slice(0,14).map(x=>x.tag); FD._saved = sv.items||[]; renderFilters(); } catch(e){}
}
function renderFilters(){
  const chips = (list, key) => `<div class="fl-chips">${list.map(([v,l])=>
    `<button type="button" data-fd="${key}" data-v="${esc(v)}" class="${FD[key]===v?"on":""}">${esc(l)}</button>`).join("")}</div>`;
  const tagList = (FD._tags||[]).map(t=>[t,"#"+t]);
  const saved = (FD._saved||[]);
  $("filtBody").innerHTML =
    (saved.length ? `<div class="fl-sec">Saved searches</div>`+saved.map(x=>
      `<div class="fl-saved"><button class="fs-go" data-fs="go" data-id="${esc(x.id)}">★ ${esc(x.name)}</button>
       <button class="fs-x" data-fs="del" data-id="${esc(x.id)}" aria-label="Delete">✕</button></div>`).join("") : "")
    + `<div class="fl-sec">Type</div>${chips(FIL_TYPES,"type")}`
    + `<div class="fl-sec">When</div>${chips(FIL_RANGES,"range")}`
    + `<div class="fl-sec">Tag</div>${chips([["","Any"],...tagList],"tag")}`
    + `<div class="fl-foot"><button data-fd="reset">Reset</button><button data-fd="save">★ Save this search</button>
       <button class="pri" data-fd="apply">Apply</button></div>`;
}
async function applyFilters(f, q){
  S.fil = {type:f.type||"", range:f.range||"", tag:f.tag||""};
  if (q !== undefined){
    S.q = q; $("srchIn").value = q;
    if (q) $("srchWrap").classList.add("on");
    srchSync();
  }
  filSync(); $("filterSheet").classList.remove("on");
  switchView("vNotes");
  await loadNotes(); renderNotes();
}
async function filClick(t){
  const b = t.closest("[data-fd]");
  if (b){
    const k = b.dataset.fd;
    if (k==="reset"){ FD.type = FD.range = FD.tag = ""; renderFilters(); return true; }
    if (k==="apply"){ await applyFilters(FD); return true; }
    if (k==="save"){
      try { const r = await api("/saved-searches",{method:"POST",body:{name:"",q:S.q||"",type:FD.type,range:FD.range,tag:FD.tag}});
        FD._saved = r.items; toast("Search saved ★","ok"); renderFilters(); }
      catch(e){ toast(e.status===400?"Pick a filter first":"Couldn't save","err"); }
      return true; }
    FD[k] = b.dataset.v; renderFilters(); return true;
  }
  const sv = t.closest("[data-fs]");
  if (sv){
    const it = (FD._saved||[]).find(x=>x.id===sv.dataset.id);
    if (sv.dataset.fs==="del"){
      try { const r = await api("/saved-searches/"+sv.dataset.id,{method:"DELETE"}); FD._saved = r.items; renderFilters(); } catch(e){}
    } else if (it){ await applyFilters(it, it.q || ""); }
    return true;
  }
  return false;
}
