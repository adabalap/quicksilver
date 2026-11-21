/* ═════════ Utils ═════════ */
const $ = id => document.getElementById(id);
const esc = s => String(s??"").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
/* Card palette. Generated algorithmically: 8 hues spaced evenly around the
   wheel at IDENTICAL saturation (62%) and lightness (92%). Uniformity is the
   point — the previous hand-picked set swung from 28% to 100% saturation, so
   the warm high-saturation tints (peach, yellow, pink) visually dominated while
   green and grey faded into the white background and read as "no colour".
   Even hue spacing + equal presence = every card distinguishable, none louder
   than another. All clear 13:1 contrast against the ink colour (AAA needs 7:1).
   rose · peach · citron · sage · teal · sky · lilac · mauve */
const PASTELS = ["#F7DEE2","#F7EADE","#F7F5DE","#E8F7DE","#DEF7F2","#DEEDF7","#E6DEF7","#F7DEF3"];
// Dark-mode counterparts, index-matched: each note keeps its identity colour but
// as a deep, low-luminance tint that sits calmly on the dark UI instead of
// glaring like the light pastels do. Same hue family, very different lightness.
const PASTELS_DARK = ["#2b1e24","#2b241c","#292a1b","#1f2a1d","#1b2a28","#1b2430","#231d30","#2a1c29"];

// ── Mono: a black & white / grayscale palette. Same 8-way per-note distinction
// (so cards remain individually identifiable, not a flat wall), but carried by
// LIGHTNESS steps instead of hue — genuinely achromatic, not a tinted grey.
const PASTELS_MONO = ["#F3F3F3","#EDEDED","#E7E7E7","#F0F0F0","#E9E9E9","#F4F4F4","#EAEAEA","#F1F1F1"];
const PASTELS_MONO_DARK = ["#232323","#1e1e1e","#272727","#202020","#252525","#1c1c1c","#282828","#212121"];

// ── Slate: one calm elegant tone family (cool blue-grey) instead of eight hues —
// closer to a single "wallpaper" than per-note colour-coding, for people who
// find the rainbow feed busy. Original palette, not tied to any character/IP.
const PASTELS_SLATE = ["#E9EDF2","#E3E9F0","#EDF0F4","#E6EBF1","#E0E6ED","#EAEEF3","#E4E9EF","#EEF1F5"];
const PASTELS_SLATE_DARK = ["#1b2028","#191e26","#1d2229","#1a1f27","#171c24","#1c212a","#1a1f26","#1e232b"];

// ── Midnight: deep jewel tones — an elegant, moody feed inspired by the WhatsApp
// "wallpaper" idea of a single rich mood rather than per-note hue-coding. Each
// swatch is its own jewel colour so cards stay distinguishable, but the family
// reads as one deliberate palette rather than a rainbow. Original, not tied to
// any character or franchise.
const PASTELS_MIDNIGHT = ["#3A2E5C","#2E3C5C","#2E5C4E","#5C3A4E","#4E2E5C","#2E4E5C","#5C4E2E","#3A5C4E"];
const PASTELS_MIDNIGHT_DARK = ["#211a35","#1b2335","#1b352c","#35211b","#2c1b35","#1b2c35","#352c1b","#21352c"];

// ── Sunset: warm, glowing tones — amber through rose. A calm "golden hour" mood.
const PASTELS_SUNSET = ["#FCE0C4","#FBD3B0","#F9C6A8","#F7D9A6","#F5C99C","#FADDB8","#F8CDB4","#FBE3C8"];
const PASTELS_SUNSET_DARK = ["#2e2013","#2c1c10","#2a1a10","#2b1f11","#28190e","#2d2015","#2b1c12","#2f2216"];

// ── Forest: earthy, grounded greens — a quiet, natural feed.
const PASTELS_FOREST = ["#DCE8D8","#D4E4CE","#DDE9DB","#D8E6D4","#E0EBDD","#D6E3D2","#DFE9DC","#D2E1CD"];
const PASTELS_FOREST_DARK = ["#1a2318","#182116","#1b2419","#192217","#1c251a","#172016","#1a2318","#162015"];

// ── Ocean: cool blues and teals — a crisp, clear feed.
const PASTELS_OCEAN = ["#D6EAF0","#CDE6EE","#D2E9EE","#C8E3ED","#DBECF1","#CFE7EE","#D9EBF0","#C5E1EC"];
const PASTELS_OCEAN_DARK = ["#132025","#111e23","#132227","#0f1c21","#152226","#101f24","#142125","#0e1b20"];

// ── Cartoon (pop) and Material 3 (tonal container) palettes.
const PASTELS_POP = ["#FFD3DF","#FFE59A","#BFEFD0","#BEE0FF","#DCCBFF","#FFD2AE","#B4EDEA","#F7C8EE"];
const PASTELS_POP_DARK = ["#5a2c3e","#5a4a1c","#1d4a35","#1d3e5e","#40306a","#5a3a1c","#194848","#522c50"];
const PASTELS_M3 = ["#E8DEF8","#D3E3FD","#C4EED0","#FFDCC2","#FFD9E3","#F6E5A3","#CDEBE4","#E1E2EC"];
const PASTELS_M3_DARK = ["#332D41","#1F3350","#1E3A2A","#4A3220","#4A2B36","#43391A","#193B35","#2E3038"];

// Theme registry — each maps to a light/dark PALETTE pair and describes itself
// for the settings UI. Adding a new theme later is just adding an entry here.
const NOTE_THEMES = {
  colorful: { label:"Colorful", d:"Each note keeps its own pastel colour — the default feed.",
              light:PASTELS,      dark:PASTELS_DARK,
              swatch:isDark=>isDark?PASTELS_DARK[4]:PASTELS[4] },
  mono:     { label:"Black & white", d:"A calm, distraction-free grayscale feed.",
              light:PASTELS_MONO, dark:PASTELS_MONO_DARK,
              swatch:isDark=>isDark?PASTELS_MONO_DARK[0]:PASTELS_MONO[0] },
  slate:    { label:"Slate", d:"One elegant cool tone instead of a rainbow of colours.",
              light:PASTELS_SLATE,dark:PASTELS_SLATE_DARK,
              swatch:isDark=>isDark?PASTELS_SLATE_DARK[0]:PASTELS_SLATE[0] },
  midnight: { label:"Midnight", d:"Deep jewel tones — an elegant, moody feed.",
              light:PASTELS_MIDNIGHT, dark:PASTELS_MIDNIGHT_DARK,
              swatch:isDark=>isDark?PASTELS_MIDNIGHT_DARK[0]:PASTELS_MIDNIGHT[0] },
  sunset:   { label:"Sunset", d:"Warm amber and rose — a golden-hour mood.",
              light:PASTELS_SUNSET, dark:PASTELS_SUNSET_DARK,
              swatch:isDark=>isDark?PASTELS_SUNSET_DARK[0]:PASTELS_SUNSET[0] },
  forest:   { label:"Forest", d:"Earthy, grounded greens — a quiet, natural feed.",
              light:PASTELS_FOREST, dark:PASTELS_FOREST_DARK,
              swatch:isDark=>isDark?PASTELS_FOREST_DARK[0]:PASTELS_FOREST[0] },
  ocean:    { label:"Ocean", d:"Cool blues and teals — a crisp, clear feed.",
              light:PASTELS_OCEAN, dark:PASTELS_OCEAN_DARK,
              swatch:isDark=>isDark?PASTELS_OCEAN_DARK[0]:PASTELS_OCEAN[0] },
  // ── Cartoon: a hand-inked sticker look. Pop pastels, a bold ink outline, a hard offset shadow and one small
  // sticker in the card's footer corner (never over the text). Shapes are original; no characters.
  cartoon:  { label:"Cartoon", d:"Pop colours, inked outlines and a little sticker on every note.",
              light:PASTELS_POP, dark:PASTELS_POP_DARK,
              swatch:isDark=>isDark?PASTELS_POP_DARK[0]:PASTELS_POP[0] },
  // ── Material: Material 3 tonal containers. Flat, soft, large radius, no outlines.
  material: { label:"Material", d:"Material 3 tonal surfaces: flat, soft and calm, with large rounded cards.",
              light:PASTELS_M3, dark:PASTELS_M3_DARK,
              swatch:isDark=>isDark?PASTELS_M3_DARK[0]:PASTELS_M3[0] },
};
function _themeRegistry(){ return NOTE_THEMES; }
// Connection mode: "remote" (default — relative paths, whatever the page's own
// origin is, e.g. the Cloudflare-tunnelled public domain) or "local" (talk to
// http://127.0.0.1:<port> directly, bypassing the tunnel — only meaningful
// when the browser and the server are the same device). Read once at load, not
// per-request: switching it reloads the page (see setConnMode) so every piece
// of state — API_BASE, the SSE connection, cached data — starts clean against
// whichever origin is now in effect, rather than trying to hot-swap mid-session.
function getConnMode(){
  try { return localStorage.getItem("hg_conn_mode") === "local" ? "local" : "remote"; }
  catch(e){ return "remote"; }
}
function getLocalPort(){
  try { return (localStorage.getItem("hg_local_port") || "5230").replace(/\D/g, "") || "5230"; }
  catch(e){ return "5230"; }
}
function setConnMode(mode, port){
  try {
    localStorage.setItem("hg_conn_mode", mode === "local" ? "local" : "remote");
    if (port) localStorage.setItem("hg_local_port", String(port).replace(/\D/g, "") || "5230");
  } catch(e){}
  location.reload();
}
const API_BASE = getConnMode() === "local" ? `http://127.0.0.1:${getLocalPort()}` : "";

function getNoteTheme(){
  try { const v = localStorage.getItem("hg_note_theme"); return NOTE_THEMES[v] ? v : "colorful"; }
  catch(e){ return "colorful"; }
}
function _applyNtAttr(){ try { document.documentElement.dataset.nt = getNoteTheme(); } catch(e){} }
function setNoteTheme(v){
  if (!NOTE_THEMES[v]) return;
  try { localStorage.setItem("hg_note_theme", v); } catch(e){}
  _applyNtAttr();
  try { if (typeof loadNotes === "function") loadNotes(); } catch(e){}
  try { if (S.cur && typeof renderDetail === "function") renderDetail(S.cur); } catch(e){}
  try { if (typeof renderSettings === "function" && $("setBody")) renderSettings(); } catch(e){}
}
function _isDark(){
  const t = document.documentElement.dataset.theme;
  if (t === "dark") return true;
  if (t === "light") return false;
  return matchMedia("(prefers-color-scheme: dark)").matches;
}
const CLUC = ["#6C5CE7","#00A6C4","#2f9e77","#e0447e","#e8964a","#e05252"];
// `idx` is the note's manual colour override (a palette position 0-7, or
// null/undefined for automatic). It's a POSITION, not a hex, so the same choice
// re-resolves against whichever theme — and light/dark variant — is active.
const _palette = () => {
  const th = NOTE_THEMES[getNoteTheme()] || NOTE_THEMES.colorful;
  return _isDark() ? th.dark : th.light;
};
const pastel = (id, idx) => {
  const pal = _palette();
  if (Number.isInteger(idx) && idx >= 0) return pal[idx % pal.length];
  return pal[[...String(id)].reduce((a,c)=>a+c.charCodeAt(0),0) % pal.length];
};
// Cartoon stamps. Original shapes, hand-inked: round caps, slightly wobbly
// paths, drawn in the CARD'S OWN INK rather than a random accent, so a card
// reads as one printed object instead of a coloured rectangle with a sticker
// pasted on it.
//
// WHICH STAMP A NOTE GETS IS NOT A HASH ANY MORE.
//
// It used to be chosen from the note id, which meant the glyph carried no
// information: the bill and the poem were equally likely to get a star.
// A mark that encodes nothing is decoration, and decoration on every card
// is noise you have to look past. Now it comes from what the note IS --
// a checklist, something with open tasks, a bill, code, a link, a document
// -- so the feed can be scanned for a kind rather than read in full.
//
// `k` is the ink. Plain SVG attributes only, so every WebView renders them.
const STAMPS = {
  task:  k=>`<path d='M9 24l9 9L39 13' fill='none' stroke='${k}' stroke-width='6' stroke-linecap='round' stroke-linejoin='round'/>`,
  list:  k=>`<g stroke='${k}' stroke-width='2.8' stroke-linecap='round'><path d='M8 14l4 4 7-8M8 27l4 4 7-8M8 40l4 4 7-8'/><path d='M26 14h14M26 27h14M26 40h10'/></g>`,
  bill:  k=>`<path d='M12 6h24v36l-6-4-6 4-6-4-6 4z' fill='none' stroke='${k}' stroke-width='2.8' stroke-linejoin='round'/><path d='M19 17h10M19 25h10' stroke='${k}' stroke-width='2.6' stroke-linecap='round'/>`,
  code:  k=>`<path d='M18 15L7 24l11 9M30 15l11 9-11 9M27 10l-6 28' fill='none' stroke='${k}' stroke-width='3.4' stroke-linecap='round' stroke-linejoin='round'/>`,
  link:  k=>`<path d='M20 28a8 8 0 0 1 0-11l6-6a8 8 0 0 1 11 11l-3 3' fill='none' stroke='${k}' stroke-width='3.4' stroke-linecap='round'/><path d='M28 20a8 8 0 0 1 0 11l-6 6a8 8 0 0 1-11-11l3-3' fill='none' stroke='${k}' stroke-width='3.4' stroke-linecap='round'/>`,
  doc:   k=>`<path d='M13 5h16l8 8v30H13z' fill='none' stroke='${k}' stroke-width='2.8' stroke-linejoin='round'/><path d='M29 5v9h8M19 25h12M19 32h8' fill='none' stroke='${k}' stroke-width='2.6' stroke-linecap='round' stroke-linejoin='round'/>`,
  photo: k=>`<rect x='6' y='11' width='36' height='28' rx='4' fill='none' stroke='${k}' stroke-width='2.8'/><circle cx='17' cy='21' r='3.4' fill='none' stroke='${k}' stroke-width='2.6'/><path d='M9 35l11-10 7 6 5-4 9 8' fill='none' stroke='${k}' stroke-width='2.8' stroke-linecap='round' stroke-linejoin='round'/>`,
  voice: k=>`<rect x='18' y='5' width='12' height='22' rx='6' fill='none' stroke='${k}' stroke-width='2.8'/><path d='M11 22a13 13 0 0 0 26 0M24 35v7' fill='none' stroke='${k}' stroke-width='2.8' stroke-linecap='round'/>`,
  // The fallback family, for a plain note that is simply a thought. Still
  // varied, because a feed of identical marks is worse than none -- but it
  // only ever applies where there is nothing better to say.
  blob:  k=>`<path d='M10 20c2-9 12-14 21-10 8 4 11 13 6 21-4 7-14 10-21 6-7-3-8-10-6-17z' fill='none' stroke='${k}' stroke-width='2.8' stroke-linejoin='round'/><circle cx='19' cy='23' r='1.9' fill='${k}'/><circle cx='29' cy='23' r='1.9' fill='${k}'/><path d='M20 30c2 2 6 2 8 0' stroke='${k}' stroke-width='2.4' fill='none' stroke-linecap='round'/>`,
  squig: k=>`<path d='M5 30c5-14 9 8 14-4s9 8 14-4 7 2 10-6' stroke='${k}' stroke-width='4' fill='none' stroke-linecap='round'/>`,
  star:  k=>`<path d='M24 5l5.2 12.4 13.3 1.1-10.1 8.8 3.1 13-11.5-7-11.5 7 3.1-13L5.5 18.5l13.3-1.1z' fill='none' stroke='${k}' stroke-width='2.8' stroke-linejoin='round'/>`,
  leaf:  k=>`<path d='M38 8C20 8 9 18 9 31c0 4 1 7 3 9 7-16 16-23 24-26-9 6-16 14-20 26 3 1 6 1 9 0 12-3 19-17 13-32z' fill='none' stroke='${k}' stroke-width='2.8' stroke-linejoin='round'/>`,
};
const _FALLBACK_STAMPS = ["blob","squig","star","leaf"];

// What a note IS, in the order that matters. Checked top-down, so a bill
// with an open task still reads as a bill.
function stampKeyFor(note){
  if (!note || typeof note !== "object") return null;
  const body = String(note.body || ""), title = String(note.title || "");
  const tags = (note.tags || []).map(t => String(t).toLowerCase());
  if (note.note_type === "checklist") return "list";
  if (note.media_kind === "audio" || /\bvoice note\b/i.test(body)) return "voice";
  if (note.media_kind === "image" || (note.attachments || []).some(a => /^image\//.test(a.mime || ""))) return "photo";
  if (tags.includes("bill") || /\b(bill|emi|instalment|installment|due on|payable by)\b/i.test(title + " " + body.slice(0, 200))) return "bill";
  if (/```|\$PREFIX|\bsudo \b|\bgit \b|^\s*[\w.]+\.(py|js|kt|sh|md|json|yml)\b/im.test(body.slice(0, 300))) return "code";
  if (/\.(md|pdf|docx?|xlsx?|csv)\b/i.test(title)) return "doc";
  if ((note.action_items || []).some(a => !a.done)) return "task";
  if (/^https?:\/\//m.test(body) || /https?:\/\//.test(body.slice(0, 300))) return "link";
  return null;
}

// Blend two hexes. Used to pull a card's ink out of its own paper colour so
// the outline belongs to the card: a pink note is outlined in plum-black, a
// mint one in green-black. Plain arithmetic rather than CSS color-mix, which
// an older WebView would drop on the floor -- and a card with no border is
// not a card.
function _mixHex(a, b, t){
  const pa = parseInt(a.slice(1), 16), pb = parseInt(b.slice(1), 16);
  const m = (sa, sb) => Math.round(((pa >> sa) & 255) * (1 - t) + ((pb >> sb) & 255) * t);
  return "#" + ((1 << 24) + (m(16,16) << 16) + (m(8,8) << 8) + m(0,0)).toString(16).slice(1);
}

// ── Ink, in HSL rather than a straight blend toward black.
//
// _mixHex(paper, near-black, 0.80) was the first attempt and it does not
// work, for a reason that is only obvious once you see eight cards together:
// an RGB lerp toward a neutral drags EVERY hue to the same place. At 80% a
// mint card and a rose card both come out #353b40, so the per-card ink that
// was the whole point of the exercise renders as one grey border repeated
// eight times. Pulling the mix back to keep some colour is no answer either
// -- that was the first cartoon pass, where the edges went so pale the cards
// dissolved into blobs against the background.
//
// Lightness and hue are separate axes, so move only the one that needs
// moving: drop L for contrast, keep H, and floor S so a nearly-grey paper
// still yields a tinted ink rather than a dead one. Ceiling it too, or a
// saturated paper inks in something that glows.
function _hsl(hex){
  const n = parseInt(hex.slice(1), 16);
  const r = ((n >> 16) & 255) / 255, g = ((n >> 8) & 255) / 255, b = (n & 255) / 255;
  const mx = Math.max(r, g, b), mn = Math.min(r, g, b), d = mx - mn, l = (mx + mn) / 2;
  let h = 0, s = 0;
  if (d) {
    s = d / (1 - Math.abs(2 * l - 1));
    if (mx === r) h = (g - b) / d + (g < b ? 6 : 0);
    else if (mx === g) h = (b - r) / d + 2;
    else h = (r - g) / d + 4;
    h *= 60;
  }
  return [h, s, l];
}
function _hslHex(h, s, l){
  const c = (1 - Math.abs(2 * l - 1)) * s;
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
  const m = l - c / 2;
  const t = h < 60 ? [c,x,0] : h < 120 ? [x,c,0] : h < 180 ? [0,c,x]
          : h < 240 ? [0,x,c] : h < 300 ? [x,0,c] : [c,0,x];
  const v = n => Math.max(0, Math.min(255, Math.round((n + m) * 255)));
  return "#" + ((1 << 24) + (v(t[0]) << 16) + (v(t[1]) << 8) + v(t[2])).toString(16).slice(1);
}
// WCAG relative luminance, and the contrast ratio built on it.
function _lum(hex){
  const n = parseInt(hex.slice(1), 16);
  const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
  return 0.2126 * f((n >> 16) & 255) + 0.7152 * f((n >> 8) & 255) + 0.0722 * f(n & 255);
}
function _ratio(a, b){
  const x = _lum(a), y = _lum(b);
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
}
// Ink for a paper colour: keep the hue, clamp the saturation, and let the
// LIGHTNESS fall out of a contrast target rather than being fixed.
//
// Fixing L does not work, because luminance is not lightness: HSL L=0.30
// green is far brighter than HSL L=0.30 blue. A single hand-tuned L measured
// 7.5:1 on the rose swatch and 4.48:1 on the teal one -- below AA, on the
// same setting, for no reason a reader could see. Worse, the number held
// only for the eight swatches it was tuned against; the next palette added
// would have broken it silently.
//
// So walk L away from the paper and stop at the first step that clears the
// target. Every swatch then meets the floor by construction, including ones
// nobody has invented yet, and each ink sits as close to its paper as the
// floor allows -- which is what keeps it looking like the card's own colour
// rather than a borrowed black.
function _inkFor(paper, target, minS, maxS, dark){
  const hs = _hsl(paper);
  const s = Math.max(minS, Math.min(maxS, hs[1]));
  const walk = up => {
    for (let i = 0; i <= 50; i++) {
      const l = up ? Math.min(1, hs[2] + i * 0.02) : Math.max(0, hs[2] - i * 0.02);
      const hex = _hslHex(hs[0], s, l);
      if (_ratio(hex, paper) >= target) return hex;
      if (l <= 0 || l >= 1) return null;
    }
    return null;
  };
  // Preferred direction first -- dark text on pale paper, pale on dark. But
  // the preference is only a preference: a palette can hand us a paper that
  // has no room that way (a "light mode" built from deep jewel tones), and
  // walking into the floor there returns a near-black at 1.7:1 while looking
  // like it worked. Fall back to the other direction, then to the extreme
  // that actually contrasts, so the target is met for any paper rather than
  // only for the swatches this was tuned against.
  return walk(dark) || walk(!dark) ||
    (_ratio("#ffffff", paper) >= _ratio("#000000", paper) ? "#ffffff" : "#000000");
}

const DOODLE_ACCENTS = ["#FF6B8B","#FFC83D","#3DD6A0","#4DA3FF","#9B7BFF","#FF9F5A"];
function _shade(hex, amt){
  const n = parseInt(hex.slice(1),16);
  const r = Math.max(0,Math.min(255,(n>>16)+amt));
  const g = Math.max(0,Math.min(255,((n>>8)&255)+amt));
  const b = Math.max(0,Math.min(255,(n&255)+amt));
  return "#"+((1<<24)+(r<<16)+(g<<8)+b).toString(16).slice(1);
}
// The inline style for a note card. Every theme gets its colour; Cartoon adds an ink colour and one small
// sticker (as a CSS variable, drawn by `.card::after` in themes.css, so it sits in the footer corner and can
// never cover text). The data URI is single-quoted and fully percent-encoded because this string lands inside
// a double-quoted HTML style="…" attribute.
function cardBg(note){
  const id = (note && typeof note === "object") ? note.id : note;
  const color = pastel(id, (note && typeof note === "object") ? note.color_idx : undefined);
  // Midnight in light mode is a set of deep jewel colours: give those cards light ink so the text stays readable.
  if (getNoteTheme() === "midnight" && !_isDark()) return `background:${color};color:#F4F0FF;--ink:#F4F0FF;--ink2:#D2CAEE;--mut:#B0A7D6`;
  if (getNoteTheme() !== "cartoon") return `background:${color}`;
  const dark = _isDark();
  // TWO inks from one paper colour, not one.
  //
  // The border is the darker: enough tint to belong to the card, dark
  // enough to hold an edge. The text is a step lighter, because body copy
  // set in the border's weight reads as shouting. A first pass used one
  // mid value for both and the cards dissolved into soft blobs against the
  // background -- richness is not worth losing the edge of a card for.
  //
  // In dark mode the paper is already dark, so both inks go the other way,
  // toward light, and the saturation ceiling drops: a bright tint on a dark
  // ground is far louder than the same tint on a pale one.
  //
  // The edge is targeted HARDER than the text, which is the opposite of the
  // usual arrangement and is deliberate: in a sticker aesthetic the inked
  // outline is the thing that makes a card an object, while 5.5:1 body text
  // is already a full step past AA. Both floors are guaranteed by the search
  // in _inkFor, not by the palette happening to cooperate.
  const edge = _inkFor(color, 7.0, dark ? 0.16 : 0.38, dark ? 0.42 : 0.62, dark);
  const text = _inkFor(color, 5.5, dark ? 0.10 : 0.26, dark ? 0.30 : 0.46, dark);
  const key = stampKeyFor(note) ||
    _FALLBACK_STAMPS[[...String(id)].reduce((a,c,i)=>a+c.charCodeAt(0)*(i+7),0) % _FALLBACK_STAMPS.length];
  const raw = encodeURIComponent(
    `<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 48 48' width='48' height='48'>${STAMPS[key](edge)}</svg>`
  ).replace(/'/g,"%27").replace(/\(/g,"%28").replace(/\)/g,"%29");
  return `background:${color};--nt-ink:${edge};--nt-text:${text};--nt-sticker:url('data:image/svg+xml,${raw}')`;
}
// Three user-facing states. 'force' is an internal re-run trigger, shown as
// "Enriching" because that is what it means to the user (the agent is working).
const statusLabel = s => ({raw:"New",pending:"Review",approved:"Approved",force:"Processing",stalled:"Stalled",protected:"🔒 Protected"})[s]||s;
/* A note is "Processing" only while the agent can plausibly be working on it.
   The agent rescues/gives up on stuck notes itself; if it's down or slow we
   say so honestly (and offer Retry) instead of blinking amber forever. */
const STALL_MS = 5*60*1000;
function effStatus(n){
  const st = n.status||"raw";
  if (st!=="force") return st;
  const t = Date.parse(n.state_updated_at||"");
  return (t && Date.now()-t > STALL_MS) ? "stalled" : st;
}
const TAGE = {work:"💼",finance:"💰",health:"❤️",shopping:"🛒",groceries:"🛒",personal:"👤",
  family:"👨‍👩‍👧",travel:"✈️",tech:"💻",education:"📚",fitness:"🏃",strategy:"🎯",general:"📌"};
function relTime(iso){ if(!iso) return "";
  const m = Math.floor((Date.now()-new Date(iso).getTime())/6e4);
  if(m<1) return "now"; if(m<60) return m+"m";
  const h = Math.floor(m/60); if(h<24) return h+"h";
  const d = Math.floor(h/24); if(d<7) return d+"d";
  return new Date(iso).toLocaleDateString("en-IN",{day:"numeric",month:"short"}); }
/* Highlights are stored as INVISIBLE marker characters that never occur in real text:
   U+2063 + a colour char ... U+2063 U+2063. Pasted code (`a == b`, `x=="y"`) can therefore
   never be mistaken for one. The old =={y}text== form is still read (the server migrates it).
   Keep in sync with app.py and anchor.py. */
const HL_OPEN = "\u2063", HL_CLOSE = "\u2063\u2063";
const HL_CH = {y:"\u2060", g:"\u2061", p:"\u2062", b:"\u2064"};
const HL_COL = {"\u2060":"y", "\u2061":"g", "\u2062":"p", "\u2064":"b"};
const HL_SRC = String.raw`\u2063([\u2060\u2061\u2062\u2064])([\s\S]*?)\u2063\u2063|(?<![=!<>])==\{([ygpb])\}(?![\s=])([\s\S]*?)(?<![\s=])==(?!=)`;
const HL_RE = new RegExp(HL_SRC, "gu");
const hlText = (m,c1,t1,c2,t2) => t1!==undefined ? t1 : (t2||"");
const hlStrip = s => String(s||"").replace(HL_RE, hlText).replace(/[\u2060-\u2064]/g,"");
function plainPreview(body){
  // Clean text for feed cards: drop #tags, ==highlight== markers, and md syntax.
  return String(body||"")
    .replace(HL_RE, hlText).replace(/[\u2060-\u2064]/g,"")   // unwrap highlights
    .replace(/#\w+/g,"")                              // hashtags
    .replace(/^#{1,6}\s+/gm,"")                        // heading marks
    .replace(/\*\*([^*]+)\*\*/g,"$1").replace(/\*([^*]+)\*/g,"$1")
    .replace(/`([^`]+)`/g,"$1")
    .trim();
}
function mdLite(t){
  const src = String(t??"");
  // 1) Pull fenced code blocks out first so nothing inside them is transformed.
  const blocks = [];
  let s = src.replace(/```(\w*)\n?([\s\S]*?)```/g, (m,lang,code)=>{
    const raw = code.replace(/\n$/,"");
    blocks.push('<div class="codewrap"><button class="codecopy" data-code="'
      + esc(raw).replace(/"/g,"&quot;") + '">📋 Copy</button>'
      + '<pre class="code"><code>' + esc(raw) + '</code></pre></div>');
    return "\u0000B"+(blocks.length-1)+"\u0000";
  });
  s = esc(s);
  // 2) Inline: code, bold, em, headings, blockquotes.
  const inl = [];
  s = s.replace(/`([^`\n]+)`/g,(m,c)=>{ inl.push('<code class="ic">'+c+'</code>'); return "\u0000I"+(inl.length-1)+"\u0000"; });
  s = s.replace(/^### (.+)$/gm,"<h3>$1</h3>").replace(/^## (.+)$/gm,"<h2>$1</h2>").replace(/^# (.+)$/gm,"<h1>$1</h1>");
  s = s.replace(/\*\*([^*]+)\*\*/g,"<strong>$1</strong>").replace(/\*([^*]+)\*/g,"<em>$1</em>");
  // ==highlight==  or  =={y|g|p|b}coloured highlight== (may span lines)
  s = s.replace(HL_RE, (m,c1,t1,c2,t2)=>`<mark class="hl hl-${HL_COL[c1]||c2||"y"}">${t1!==undefined ? t1 : t2}</mark>`)
       .replace(/[\u2060-\u2064]/g,"");
  s = s.replace(/^&gt; (.+)$/gm,"<blockquote>$1</blockquote>")
       .replace(/(<blockquote>[\s\S]*?<\/blockquote>\n?)+/g,m=>"<blockquote>"+m.replace(/<\/?blockquote>/g,"").replace(/\n/g,"<br/>")+"</blockquote>");
  // 3) Bullet lists.
  s = s.replace(/^- (.+)$/gm,"<li>$1</li>").replace(/(<li>[\s\S]*?<\/li>\n?)+/g,m=>"<ul>"+m+"</ul>");
  // 3b) Linkify bare URLs (http/https). Runs on already-escaped text, so we match
  //     the escaped form and skip anything already inside a tag attribute. Links
  //     open in a new tab; long URLs wrap via CSS (.ntext overflow-wrap).
  s = s.replace(/(^|[^"'=>])(https?:\/\/[^\s<]+[^\s<.,;:!?)\]}])/g,
    (m,pre,url)=>`${pre}<a class="nlink" href="${url}" target="_blank" rel="noopener noreferrer">${url}</a>`);
  // 4) Paragraphs: blank line → spacer; single newline → break. Don't wrap block elems.
  s = s.split(/\n{2,}/).map(p=>{
    p = p.trim(); if (!p) return "";
    if (/^\s*<(h2|h3|ul|pre|blockquote)/.test(p)) return p;
    return "<p>"+p.replace(/\n/g,"<br/>")+"</p>";
  }).join("");
  // 5) Restore code blocks.
  s = s.replace(/\u0000I(\d+)\u0000/g,(m,i)=>inl[+i]);
  s = s.replace(/\u0000B(\d+)\u0000/g,(m,i)=>blocks[+i]);
  return s;
}
// In-app modal — returns a Promise. Replaces native confirm()/prompt() which
// show the ugly "localhost:5230 says" chrome and break the app's design.
let _modalResolve = null;
function _closeModal(val){
  $("modalLayer").classList.remove("on");
  $("modalInput").style.display = "none";
  $("modalArea").style.display = "none";
  $("modalOk").classList.remove("danger");
  const r = _modalResolve; _modalResolve = null;
  if (r) r(val);
}
function showConfirm(msg, {title="Confirm", okText="OK", danger=false}={}){
  return new Promise(res=>{
    _modalResolve = res;
    $("modalTitle").textContent = title;
    $("modalMsg").textContent = msg;
    $("modalMsg").style.display = msg ? "block" : "none";
    $("modalOk").textContent = okText;
    if (danger) $("modalOk").classList.add("danger");
    $("modalLayer").classList.add("on");
    requestAnimationFrame(()=>$("modalOk").focus());
  });
}
function showPrompt(msg, {title="Enter a value", value="", okText="Save", multiline=false, placeholder=""}={}){
  return new Promise(res=>{
    _modalResolve = res;
    $("modalTitle").textContent = title;
    $("modalMsg").textContent = msg || "";
    $("modalMsg").style.display = msg ? "block" : "none";
    const field = multiline ? $("modalArea") : $("modalInput");
    field.style.display = "block"; field.value = value; field.placeholder = placeholder;
    $("modalOk").textContent = okText;
    $("modalLayer").classList.add("on");
    requestAnimationFrame(()=>{ field.focus(); field.select && field.select(); });
  });
}
function toast(m,t="",action=null){
  const host = $("toasts");
  host.innerHTML = "";                 // one toast at a time — no stacking
  const el = document.createElement("div");
  el.className = "toast "+t;
  const span = document.createElement("span");
  span.textContent = m; el.appendChild(span);
  // Optional action (e.g. Undo). Dismissals are remembered permanently, so an
  // immediate way back matters more than usual — a mis-tap shouldn't be final.
  if (action && action.label && typeof action.fn === "function"){
    const b = document.createElement("button");
    b.className = "toast-act"; b.textContent = action.label;
    b.addEventListener("click", async ()=>{ el.remove(); await action.fn(); });
    el.appendChild(b);
  }
  host.appendChild(el);
  clearTimeout(host._t);
  host._t = setTimeout(()=>{ el.remove(); }, action ? 5200 : 2400);
}
// Re-fetch the open note and re-render its detail view. Used after any change
// that alters what the detail shows (dismissals, extraction switches).
// Move the segmented selection immediately, without a re-render. Updates the
// two explanatory lines too, so the whole block stays coherent mid-flight.
const MODE_TEXT = {
  full:     ["title, tags, summary · rewrites body", "applies next run — use Run now to apply it"],
  metadata: ["title, tags, summary · body untouched", "applies next run — use Run now to apply it"],
  off:      ["the agent skips this note entirely",   "in effect now — nothing will run"],
};
function setSegMode(v){
  document.querySelectorAll("#actionSheet .seg-b").forEach(b=>
    b.classList.toggle("on", b.dataset.enrichMode === v));
  const t = MODE_TEXT[v]; if (!t) return;
  const d = document.querySelector("#actionSheet .mode-desc");
  const w = document.querySelector("#actionSheet .mode-when");
  if (d) d.textContent = t[0];
  // A note that's never been enriched has nothing to re-run, so the
  // "use Run now" wording only makes sense once it's been approved.
  if (w) w.textContent = (v === "off" || S.cur?.status === "approved")
    ? t[1] : "applies when this note is enriched";
}
// Push freshly-built sheet HTML into a sheet that's ALREADY open. Without this,
// anything changed from inside the sheet leaves stale markup on screen.
// Optimistic, then honest: the note and the open sheet repaint on THIS frame —
// waiting on a round-trip for a colour tap feels broken even when it succeeds —
// and if the save fails we put the old colour back and say so.
async function setNoteColor(id, v){
  if (!S.cur || S.cur.id !== id) return;
  const prev = Number.isInteger(S.cur.color_idx) ? S.cur.color_idx : null;
  if (v === prev) return;                        // already selected — no-op
  const apply = val => {
    S.cur.color_idx = val;
    const m = S.notes.find(x=>x.id===id); if (m) m.color_idx = val;
    renderDetail(S.cur); syncOpenSheet(); renderNotes();
  };
  apply(v);
  if (navigator.vibrate) { try { navigator.vibrate(8); } catch(e){} }
  const r = await api("/notes/"+id, {method:"PATCH", body:{color_idx:v}}).catch(()=>null);
  if (r){ invalidateViewCache(); }
  else if (S.cur && S.cur.id === id){ apply(prev); toast("Couldn't change colour","err"); }
}
function syncOpenSheet(){
  const sheet = $("actionSheet");
  if (sheet && sheet.classList.contains("on"))
    $("actionSheetBody").innerHTML = S._actionSheetHtml || "";
}
// ── Tag autocomplete ────────────────────────────────────────────────────────
// Existing tags are cached once per editor session: the list changes rarely and
// a fetch on every keystroke would be wasteful.
let _tagCache = null;
async function tagVocab(){
  if (_tagCache) return _tagCache;
  try { _tagCache = (await api("/tags")).tags || []; }
  catch(e){ _tagCache = []; }
  return _tagCache;
}
// Returns the partial tag being typed at the caret, or null. Only fires on a
// "#" that starts a word, so a mid-word hash (C#, issue#42) is left alone.
function tagFragmentAt(ta){
  const upto = ta.value.slice(0, ta.selectionStart);
  const m = upto.match(/(^|\s)#([\w-]*)$/);
  return m ? m[2] : null;
}
async function updateTagSuggestions(){
  const host = $("tagSug"), ta = $("edText");
  if (!host || !ta) return;
  const frag = tagFragmentAt(ta);
  if (frag === null){ host.hidden = true; host.innerHTML = ""; return; }
  const all = await tagVocab();
  const f = frag.toLowerCase();
  // Prefix matches first (what you're most likely reaching for), then contains.
  const scored = all
    .filter(t => !f || t.tag.toLowerCase().includes(f))
    .sort((a,b) => {
      const ap = a.tag.toLowerCase().startsWith(f) ? 0 : 1;
      const bp = b.tag.toLowerCase().startsWith(f) ? 0 : 1;
      return ap - bp || b.count - a.count;
    })
    .slice(0, 8);
  if (!scored.length){ host.hidden = true; host.innerHTML = ""; return; }
  host.innerHTML = scored.map(t =>
    `<button class="tagsug-b" data-tag="${esc(t.tag)}">#${esc(t.tag)}<span class="tagsug-n">${t.count}</span></button>`
  ).join("");
  host.hidden = false;
}
// Complete the partial tag at the caret, leaving the cursor after it.
function applyTagSuggestion(tag){
  const ta = $("edText");
  const upto = ta.value.slice(0, ta.selectionStart);
  const m = upto.match(/(^|\s)#([\w-]*)$/);
  if (!m) return;
  const start = upto.length - m[2].length;
  const rest = ta.value.slice(ta.selectionStart);
  // Only add a trailing space if there isn't one already — completing a tag
  // mid-sentence otherwise leaves a double space behind the cursor.
  const pad = /^\s/.test(rest) ? "" : " ";
  ta.value = ta.value.slice(0, start) + tag + pad + rest;
  const pos = start + tag.length + pad.length;
  ta.focus(); ta.setSelectionRange(pos, pos);
  $("tagSug").hidden = true; $("tagSug").innerHTML = "";
  autoGrow(ta);
  if (Ed.mode === "edit"){ clearTimeout(Ed.timer); Ed.timer = setTimeout(edFlush, 1200); }
}
async function refreshDetail(){
  if (!S.cur) return;
  try {
    S.cur = await api("/notes/"+S.cur.id);
    renderDetail(S.cur);
  } catch(e){ /* offline or gone — leave the current view */ }
  invalidateViewCache();
}
function autoGrow(el){
  // Preserve the scroll position of the surrounding container: setting
  // height:auto momentarily collapses the textarea, which yanked the editor
  // to the top on every keystroke. Restore keeps the caret in view.
  const sc = el.closest(".ed-sc");
  const st = sc ? sc.scrollTop : 0;
  el.style.height = "auto";
  el.style.height = el.scrollHeight + "px";
  if (sc) sc.scrollTop = st;
}
function nano(n=8){ const A="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
  return [...crypto.getRandomValues(new Uint8Array(n))].map(b=>A[b%A.length]).join(""); }
const noteTitle = n => {
  if (n.user_title) return n.user_title;
  if (n.enrichment?.title) return n.enrichment.title;
  // For a checklist, use the first real item text (strip checkbox markup);
  // for a text note, the first non-empty line.
  const lines = (n.body||"").split("\n");
  for (const l of lines){
    const m = l.match(/^\s*-?\s*\[[ xX]\]\s?(.*)$/);
    const t = (m ? m[1] : l).replace(HL_RE, hlText).replace(/[\u2060-\u2064]/g,"").trim();
    if (t) return t.slice(0,60);
  }
  const A = n.attachments || [];
  if (A.length){
    const t = (A.map(a=>(a.transcript||"").trim()).find(Boolean)||"").split("\n")[0].replace(/^(Description|Spoken):\s*/,"").slice(0,60);
    if (t) return t;
    const k = A[0].kind; return k==="audio" ? "Voice note" : k==="sketch" ? "Sketch" : k==="video" ? "Video" : "Photo";
  }
  return n.note_type==="checklist" ? "Checklist" : "Untitled";
};

/* ═════════ morph: update the DOM in place ═════════
   Replacing a whole screen with innerHTML on every refresh makes it flash, replays entrance
   animations, and throws away scroll position. morph() parses the new markup and reconciles it with
   what's on screen: identical nodes are left alone, keyed nodes (data-id / id) are matched and moved,
   and only what really changed is replaced. If nothing changed, nothing is touched. */
function _mkey(n){
  return n.nodeType === 1 ? (n.getAttribute("data-id") || n.id || n.getAttribute("data-fu-id") || n.getAttribute("data-aid") || "") : "";
}
function _reconcile(parent, src, still){
  [...parent.childNodes].forEach(c=>{ if (c.nodeType === 3 && c.nodeValue.trim()) c.remove(); });
  const news = [...src.children];
  const byKey = new Map();
  [...parent.children].forEach(o=>{ const k = _mkey(o); if (k) byKey.set(k, o); });
  news.forEach((n, i)=>{
    const k = _mkey(n);
    let old = k ? byKey.get(k) : parent.children[i];
    if (old && !k && _mkey(old)) old = null;                 // never morph a keyed node into an unkeyed one
    if (old && old.tagName !== n.tagName) old = null;
    if (!old){
      if (still) n.style.animation = "none";
      parent.insertBefore(n, parent.children[i] || null); return;
    }
    let node = old;
    if (old.isEqualNode(n)){ /* untouched */ }
    else if (old.id && old.id === n.id && old.children.length && n.children.length){
      [...n.attributes].forEach(a=>{ if (old.getAttribute(a.name) !== a.value) old.setAttribute(a.name, a.value); });
      [...old.attributes].forEach(a=>{ if (!n.hasAttribute(a.name)) old.removeAttribute(a.name); });
      _reconcile(old, n, still);
    } else {
      if (still) n.style.animation = "none";
      old.replaceWith(n); node = n;
    }
    if (parent.children[i] !== node) parent.insertBefore(node, parent.children[i] || null);
  });
  while (parent.children.length > news.length) parent.lastElementChild.remove();
}
function morph(host, html, o){
  o = o || {};
  if (!host) return false;
  if (host._mh === html && !o.force) return false;
  host._mh = html;
  const t = document.createElement("template"); t.innerHTML = html;
  if (o.pre) o.pre(t.content);
  _reconcile(host, t.content, o.still !== false);
  return true;
}

/* A card's title is often just the first words of the note, so the preview repeated them. Drop the repeat. */
function dedupeLead(title, text){
  const t = String(title||"").replace(/[…\s]+$/,"").trim(), x = String(text||"").trim();
  if (t.length < 4 || x.length < t.length || x.slice(0, t.length).toLowerCase() !== t.toLowerCase()) return x;
  return x.slice(t.length).replace(/^[\s:;,.\-–—|…]+/,"").trim();
}

// Message text with its web links made tappable. Escaped first, and only http(s) links become anchors, so
// nothing in a message can inject markup or a javascript: link.
function linkText(s){
  return esc(s).replace(/https?:\/\/(?:(?!&quot;|&lt;|&gt;)[^\s<])+/g, u=>{
    const m = u.match(/[.,;:!?)]+$/), tail = m ? m[0] : "", url = tail ? u.slice(0, -tail.length) : u;
    return `<a href="${url}" target="_blank" rel="noopener noreferrer">${url}</a>${tail}`; });
}
_applyNtAttr();
