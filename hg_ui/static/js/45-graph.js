/* ═════════ Connections graph ═════════
   Dependency-free: inline SVG plus a small deterministic force layout (a
   Fruchterman–Reingold variant). Deterministic on purpose — seeded from cluster
   positions, no Math.random — so re-rendering never makes the graph jump. */
function layoutGraph(nodes, edges, W, H){
  // nodes: [{g}] (g = cluster index); edges: [{a,b,w}] (indices, w = score 0..1)
  const n = nodes.length; if (!n) return [];
  const groups = [...new Set(nodes.map(x=>x.g))];
  const size = new Map(groups.map(g=>[g, nodes.filter(x=>x.g===g).length]));
  const cx = W/2, cy = H/2, R = Math.min(W,H)*0.19;
  const centre = new Map(groups.map((g,i)=>{
    if (groups.length === 1) return [g,[cx,cy]];
    const a = (i/groups.length)*Math.PI*2 - Math.PI/2;
    return [g,[cx + R*Math.cos(a), cy + R*Math.sin(a)]]; }));
  const seen = new Map(), P = nodes.map(nd=>{
    const k = seen.get(nd.g)||0; seen.set(nd.g,k+1);
    const m = size.get(nd.g), r = 12 + m*3.5, a = (k/m)*Math.PI*2;
    const [x0,y0] = centre.get(nd.g); return [x0 + r*Math.cos(a), y0 + r*Math.sin(a)];
  });
  const k0 = Math.sqrt((W*H)/n)*0.55, ITER = 240;
  for (let it=0; it<ITER; it++){
    const temp = k0*0.5*(1 - it/ITER) + 0.4, D = P.map(()=>[0,0]);
    for (let i=0;i<n;i++) for (let j=i+1;j<n;j++){
      let dx = P[i][0]-P[j][0], dy = P[i][1]-P[j][1], d = Math.hypot(dx,dy);
      if (d < 0.01){ dx = (i-j)*0.01; dy = 0.01; d = Math.hypot(dx,dy); }
      const same = nodes[i].g === nodes[j].g;
      const f = (k0*k0/d) * (same ? 1 : 1.25);      // keep clusters distinct without sprawling
      const fx = dx/d*f, fy = dy/d*f;
      D[i][0]+=fx; D[i][1]+=fy; D[j][0]-=fx; D[j][1]-=fy;
    }
    edges.forEach(e=>{
      const dx = P[e.a][0]-P[e.b][0], dy = P[e.a][1]-P[e.b][1], d = Math.hypot(dx,dy)||0.01;
      const f = (d*d/k0) * (0.5 + e.w), fx = dx/d*f, fy = dy/d*f;
      D[e.a][0]-=fx; D[e.a][1]-=fy; D[e.b][0]+=fx; D[e.b][1]+=fy;
    });
    for (let i=0;i<n;i++){                          // gentle pull home + toward canvas centre
      const [gx,gy] = centre.get(nodes[i].g);
      D[i][0] += (gx-P[i][0])*0.9 + (cx-P[i][0])*0.15;
      D[i][1] += (gy-P[i][1])*0.9 + (cy-P[i][1])*0.15;
    }
    for (let i=0;i<n;i++){
      const d = Math.hypot(D[i][0],D[i][1]) || 0.01, m = Math.min(d, temp);
      P[i][0] += D[i][0]/d*m; P[i][1] += D[i][1]/d*m;
    }
  }
  // Fit into the canvas with a uniform scale (keeps relative distances honest).
  // Wider side padding: labels are centred under nodes and ~80px wide, so a node
  // nearer the edge than ~50px would clip its own label.
  const padX = 52, padY = 34, xs = P.map(p=>p[0]), ys = P.map(p=>p[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
  const sc = Math.min((W-2*padX)/Math.max(x1-x0,1), (H-2*padY)/Math.max(y1-y0,1), 3.2);
  const ox = (W - (x1-x0)*sc)/2 - x0*sc, oy = (H - (y1-y0)*sc)/2 - y0*sc;
  return P.map(p=>[p[0]*sc+ox, p[1]*sc+oy]);
}

function renderConnGraph(cl){
  const cw = ($("cBody") && $("cBody").clientWidth) || 360;
  const W = cw >= 700 ? 900 : 360, HARD = 300, MAXN = Math.min(HARD, S.graphMax || 60);
  const byId = new Map(S.notes.map(n=>[n.id,n]));
  // Undirected edges among clustered notes (max score wins when both sides list it).
  const inCl = new Set(); cl.forEach(g=>g.forEach(n=>inCl.add(n.id)));
  const em = new Map(), deg = new Map();
  S.notes.forEach(n=>(n.related||[]).forEach(r=>{
    if (!inCl.has(n.id) || !inCl.has(r.id) || !byId.has(r.id) || n.id===r.id) return;
    const key = n.id < r.id ? n.id+"|"+r.id : r.id+"|"+n.id;
    if (!em.has(key)) em.set(key,{a:n.id, b:r.id, w:r.score||0});
    else em.get(key).w = Math.max(em.get(key).w, r.score||0);
  }));
  em.forEach(e=>{ deg.set(e.a,(deg.get(e.a)||0)+1); deg.set(e.b,(deg.get(e.b)||0)+1); });

  // Cap the node count so it stays legible on a phone: whole clusters, largest
  // first; a single oversized cluster is trimmed to its best-connected notes.
  const total = inCl.size, pick = [];
  let focusIdx = -1;                                   // one cluster, shown whole
  if (S.graphFocus){ focusIdx = cl.findIndex(g=>g.some(n=>n.id===S.graphFocus)); if (focusIdx < 0) S.graphFocus = null; }
  const byDeg = g => [...g].sort((x,y)=>(deg.get(y.id)||0)-(deg.get(x.id)||0));
  if (focusIdx >= 0) byDeg(cl[focusIdx]).slice(0,HARD).forEach(n=>pick.push({n,g:focusIdx}));
  else cl.forEach((g,gi)=>{
    if (pick.length + g.length <= MAXN) g.forEach(n=>pick.push({n,g:gi}));
    else if (!pick.length) byDeg(g).slice(0,MAXN).forEach(n=>pick.push({n,g:gi}));
  });
  const drawn = new Set(pick.map(x=>x.g));
  const H = W > 360 ? Math.max(400, Math.min(560, 300 + pick.length*3))
                    : Math.max(300, Math.min(460, 240 + pick.length*9));   // small graphs don't get a tall empty box
  const idx = new Map(pick.map((x,i)=>[x.n.id,i]));
  const edges = [...em.values()].filter(e=>idx.has(e.a)&&idx.has(e.b))
    .map(e=>({a:idx.get(e.a), b:idx.get(e.b), w:e.w}));

  const sig = pick.map(x=>x.n.id).join(",") + "#" + edges.length + "@" + W;
  if (!S._gl || S._gl.sig !== sig){
    S._gl = { sig, pos: layoutGraph(pick.map(x=>({g:x.g})), edges, W, H) };
    S.gz = {k:1,x:0,y:0};                 // new graph -> start un-zoomed
  }
  const pos = S._gl.pos;

  const adj = pick.map(()=>new Set()); edges.forEach(e=>{ adj[e.a].add(e.b); adj[e.b].add(e.a); });
  const sel = S.graphSel != null && idx.has(S.graphSel) ? idx.get(S.graphSel) : -1;
  const near = i => sel < 0 || i === sel || adj[sel].has(i);
  const gz = S.gz, zoomLbl = gz.k >= 1.8;     // room for every label once zoomed in
  S._gzLbl = zoomLbl;
  const labelAll = pick.length <= 24 || zoomLbl;

  const lines = edges.map(e=>{
    const on = sel < 0 || e.a === sel || e.b === sel;
    return `<line class="g-edge${on?"":" g-dim"}" x1="${pos[e.a][0].toFixed(1)}" y1="${pos[e.a][1].toFixed(1)}"
      x2="${pos[e.b][0].toFixed(1)}" y2="${pos[e.b][1].toFixed(1)}"
      stroke-width="${(1 + e.w*2.5).toFixed(2)}" stroke-opacity="${(0.28 + e.w*0.5).toFixed(2)}"/>`; }).join("");
  const dots = pick.map((x,i)=>{
    const d = adj[i].size, r = 5 + Math.min(7, d*1.4), c = CLUC[x.g % CLUC.length];
    const t = noteTitle(x.n);
    const showLbl = labelAll || d >= 3 || i === sel || (sel >= 0 && adj[sel].has(i));
    const short = t.length > 16 ? t.slice(0,15).trimEnd()+"…" : t;
    // Each node sits at its world position and is counter-scaled by 1/k, so dots
    // and labels stay a constant on-screen size while the layout spreads out.
    return `<g class="g-node${near(i)?"":" g-dim"}${i===sel?" sel":""}" data-id="${esc(x.n.id)}"
        data-x="${pos[i][0].toFixed(1)}" data-y="${pos[i][1].toFixed(1)}"
        transform="translate(${pos[i][0].toFixed(1)} ${pos[i][1].toFixed(1)}) scale(${(1/gz.k).toFixed(4)})"
        role="button" tabindex="0" aria-label="${esc(t)}">
      <circle class="g-hit" cx="0" cy="0" r="${(r+9).toFixed(1)}"/>
      <circle class="g-dot" cx="0" cy="0" r="${r.toFixed(1)}" fill="${c}"/>
      ${showLbl ? `<text class="g-lbl" x="0" y="${(r+11).toFixed(1)}" text-anchor="middle">${esc(short)}</text>` : ""}
    </g>`; }).join("");

  const cut = (t,n)=> t.length > n ? t.slice(0,n-1).trimEnd()+"…" : t;
  const chips = cl.length < 2 ? "" : `<div class="g-chips" role="group" aria-label="Focus on one group of notes">
    <button class="g-chip${focusIdx<0?" on":""}" data-gf="">All <b>${total}</b></button>${cl.map((g,i)=>
    `<button class="g-chip${i===focusIdx?" on":""}${drawn.has(i)?"":" off"}" data-gf="${esc(g[0].id)}"
       style="--c:${CLUC[i%CLUC.length]}" title="${esc(noteTitle(g[0]))}"><i></i>${esc(cut(noteTitle(g[0]),16))} <b>${g.length}</b></button>`).join("")}</div>`;
  let more = "";
  if (focusIdx < 0 && total > pick.length){
    more = pick.length < HARD
      ? ` <button class="g-lnk" data-gmore>Show more</button> · <button class="g-lnk" data-gall>Show all ${Math.min(total,HARD)}</button>`
      : " Pick a group above to see the rest.";
  }
  // The count + "show more" sit ABOVE the graph so they're never below the fold.
  const status = focusIdx >= 0
    ? `<div class="g-count">${pick.length} notes in this group · <button class="g-lnk" data-gf="">Back to all</button></div>`
    : total > pick.length ? `<div class="g-count">Showing ${pick.length} of ${total} connected notes.${more}</div>` : "";
  let info = `<div class="g-hint">Tap a note to see its links</div>`;
  if (sel >= 0){
    const nn = pick[sel].n;
    info = `<div class="g-info"><div class="g-info-b"><div class="g-info-t">${esc(noteTitle(nn))}</div>
      <div class="g-info-s">Linked to ${adj[sel].size} note${adj[sel].size===1?"":"s"} · cluster ${pick[sel].g+1}</div></div>
      <button class="g-open" id="gOpen" data-id="${esc(nn.id)}">Open note</button></div>`;
  }
  $("cBody").innerHTML =
    `${chips}${status}<div class="g-wrap"><svg class="g-svg${gz.k>1.001?" z":""}" viewBox="0 0 ${W} ${H}" role="group" aria-label="Note connections graph">
       <g id="gView" transform="translate(${gz.x.toFixed(2)} ${gz.y.toFixed(2)}) scale(${gz.k.toFixed(3)})">${lines}${dots}</g></svg>
       <div class="g-zc"><button class="g-zb" data-gz="in" aria-label="Zoom in" title="Zoom in">+</button>
         <button class="g-zb" data-gz="out" aria-label="Zoom out" title="Zoom out">−</button>
         <button class="g-zb" data-gz="reset" aria-label="Reset zoom" title="Reset zoom">⟲</button></div></div>${info}`;
}

/* ── Graph zoom / pan ──────────────────────────────────────────────────────
   S.gz = {k, x, y} is the view transform in viewBox units. It lives in S (and is
   embedded in the markup) so tapping a node — which re-renders the SVG — keeps
   your zoom. Two fingers pinch+pan together; one finger pans once zoomed; the
   +/−/reset buttons are the accessible alternative; Ctrl/Cmd+wheel on desktop. */
const GZ_MIN = 1, GZ_MAX = 4;
function gzPoint(svg, cx, cy){
  const r = svg.getBoundingClientRect(), vb = svg.viewBox.baseVal;
  return { x:(cx - r.left) * vb.width / r.width, y:(cy - r.top) * vb.height / r.height };
}
function gzClamp(svg){
  const vb = svg.viewBox.baseVal, g = S.gz;
  g.k = Math.min(GZ_MAX, Math.max(GZ_MIN, g.k));
  g.x = Math.min(0, Math.max(vb.width  * (1 - g.k), g.x));    // content always covers the view
  g.y = Math.min(0, Math.max(vb.height * (1 - g.k), g.y));
  if (g.k <= 1.001){ g.k = 1; g.x = 0; g.y = 0; }
}
function gzApply(svg){
  const g = S.gz, view = svg.querySelector("#gView"); if (!view) return;
  view.setAttribute("transform", `translate(${g.x.toFixed(2)} ${g.y.toFixed(2)}) scale(${g.k.toFixed(3)})`);
  svg.classList.toggle("z", g.k > 1.001);
  const inv = (1 / g.k).toFixed(4);
  svg.querySelectorAll(".g-node").forEach(n =>
    n.setAttribute("transform", `translate(${n.dataset.x} ${n.dataset.y}) scale(${inv})`));
}
function gzZoomAt(svg, p, k2){            // keep the world point under p fixed
  const g = S.gz, k = Math.min(GZ_MAX, Math.max(GZ_MIN, k2));
  const wx = (p.x - g.x) / g.k, wy = (p.y - g.y) / g.k;
  g.k = k; g.x = p.x - k * wx; g.y = p.y - k * wy;
  gzClamp(svg); gzApply(svg);
}
(function(){
  const ptrs = new Map(); let gest = null, swallow = false;
  const svgOf = e => e.target.closest && e.target.closest(".g-svg");
  const relabel = () => { if ((S.gz.k >= 1.8) !== S._gzLbl) renderConnections(); };   // labels appear/disappear

  document.addEventListener("pointerdown", e=>{
    const svg = svgOf(e); if (!svg) return;
    ptrs.set(e.pointerId, {x:e.clientX, y:e.clientY});
    if (ptrs.size === 2){
      const [a,b] = [...ptrs.values()], m = gzPoint(svg,(a.x+b.x)/2,(a.y+b.y)/2), g = S.gz;
      gest = { svg, mode:"pinch", moved:true, d0:Math.hypot(a.x-b.x,a.y-b.y)||1, k0:g.k,
               w0:{x:(m.x-g.x)/g.k, y:(m.y-g.y)/g.k} };
      ptrs.forEach((_,id)=>{ try { svg.setPointerCapture(id); } catch(_){} });
    } else if (ptrs.size === 1){
      gest = { svg, mode:"pan", moved:false, sx:e.clientX, sy:e.clientY, x0:S.gz.x, y0:S.gz.y };
      // NOT captured yet: capturing here would retarget the click away from the node
      // and break tap-to-select. We capture only once a pan genuinely starts.
    }
  });
  document.addEventListener("pointermove", e=>{
    if (!gest || !ptrs.has(e.pointerId)) return;
    ptrs.set(e.pointerId, {x:e.clientX, y:e.clientY});
    const svg = gest.svg;
    if (gest.mode === "pinch" && ptrs.size >= 2){
      const [a,b] = [...ptrs.values()], d = Math.hypot(a.x-b.x,a.y-b.y)||1;
      const m = gzPoint(svg,(a.x+b.x)/2,(a.y+b.y)/2), g = S.gz;
      g.k = Math.min(GZ_MAX, Math.max(GZ_MIN, gest.k0 * d / gest.d0));
      g.x = m.x - g.k * gest.w0.x; g.y = m.y - g.k * gest.w0.y;      // pinch and two-finger pan in one
      gzClamp(svg); gzApply(svg);
    } else if (gest.mode === "pan" && ptrs.size === 1){
      const dx = e.clientX - gest.sx, dy = e.clientY - gest.sy;
      if (!gest.moved && Math.hypot(dx,dy) < 6) return;              // still a tap
      if (S.gz.k <= 1.001) return;                                   // nothing to pan; let the page scroll
      if (!gest.moved){ gest.moved = true; try { svg.setPointerCapture(e.pointerId); } catch(_){} }
      const r = svg.getBoundingClientRect(), vb = svg.viewBox.baseVal;
      S.gz.x = gest.x0 + dx * vb.width / r.width; S.gz.y = gest.y0 + dy * vb.height / r.height;
      gzClamp(svg); gzApply(svg);
    }
  });
  const end = e=>{
    if (!ptrs.has(e.pointerId)) return;
    ptrs.delete(e.pointerId);
    if (gest && gest.moved){ swallow = true; setTimeout(()=>{ swallow = false; }, 350); }
    if (ptrs.size === 0){ gest = null; relabel(); }
    else if (ptrs.size === 1 && gest && gest.mode === "pinch"){       // lift one finger: carry on as a pan
      const [p] = [...ptrs.values()];
      gest = { svg:gest.svg, mode:"pan", moved:true, sx:p.x, sy:p.y, x0:S.gz.x, y0:S.gz.y };
    }
  };
  document.addEventListener("pointerup", end);
  document.addEventListener("pointercancel", end);
  document.addEventListener("click", e=>{
    if (!swallow || !svgOf(e)) return;
    swallow = false; e.stopPropagation(); e.preventDefault();         // a pan/pinch isn't a tap on a node
  }, true);
  document.addEventListener("wheel", e=>{
    const svg = svgOf(e); if (!svg || !(e.ctrlKey || e.metaKey)) return;
    e.preventDefault();
    gzZoomAt(svg, gzPoint(svg, e.clientX, e.clientY), S.gz.k * Math.exp(-e.deltaY * 0.01));
    relabel();
  }, {passive:false});
})();

function renderConnections(){
  const cl = computeClusters();
  document.querySelectorAll("#connTog button").forEach(b=>b.classList.toggle("on", b.dataset.v === S.connView));
  if (!cl.length){ $("cBody").innerHTML = emptyHtml(EMPTY.connections);
    return; }
  if (S.connView === "graph"){ renderConnGraph(cl); return; }
  const CLU_FOLD = 3;
  $("cBody").innerHTML = cl.map((m,i)=>{ const c = CLUC[i%CLUC.length];
    const folded = m.length > CLU_FOLD;
    const expanded = S.connExpanded.has(i);
    const shown = (expanded ? m : m.slice(0, CLU_FOLD));
    return `<div class="clu"><div class="clu-h">
      <div class="clu-i" style="background:${c}">${i+1}</div>
      <div class="clu-t">${esc(noteTitle(m[0]))}</div>
      <span style="font-family:var(--fm);font-size:10px;color:var(--mut)">${m.length} notes</span></div>
      ${shown.map(n=>`<div class="clu-n" data-id="${esc(n.id)}">
        <div class="clu-d" style="background:${c}"></div>
        <div class="clu-nt">${esc(noteTitle(n))}</div>
        <span style="font-family:var(--fm);font-size:9.5px;color:var(--mut)">${relTime(n.updated_at)}</span>
      </div>`).join("")}
      ${folded ? `<button class="clu-more" data-idx="${i}">${expanded
          ? "Show less" : `Show ${m.length - CLU_FOLD} more`}</button>` : ""}
      </div>`; }).join("");
}
