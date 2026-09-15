/* Headless boot test: loads index.html in a real DOM (jsdom) and fails if any
   runtime error fires during startup. Syntax-only checks (new Function on each
   <script>) miss runtime throws and cross-block reference errors — this catches
   the "server up but UI blank" class of bug that those checks let through. */
const path = require("path");
const NODE_MODULES = process.env.JSDOM_PATH || "/tmp/_jsdom/node_modules";
let JSDOM;
try { JSDOM = require(path.join(NODE_MODULES, "jsdom")).JSDOM; }
catch (e) { console.log("⚠ jsdom not installed — skipping boot test (npm i jsdom)"); process.exit(0); }
const fs = require("fs");
const html = fs.readFileSync(path.join(__dirname, "static/index.html"), "utf8");
const errors = [];
new JSDOM(html, {
  runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
  beforeParse(w) {
    w.fetch = () => Promise.resolve({ ok:true, status:200,
      json:()=>Promise.resolve({notes:[],tags:[],stats:{}}), text:()=>Promise.resolve(""), clone(){return this} });
    w.EventSource = function(){ return { addEventListener(){}, close(){} }; };
    w.matchMedia = () => ({ matches:false, addEventListener(){}, addListener(){} });
    w.scrollTo = () => {};
    w.navigator.serviceWorker = { register: () => Promise.resolve({}) };
    w.indexedDB = { open: () => { const r={}; setTimeout(()=>r.onerror&&r.onerror({}),0); return r; } };
    w.onerror = (msg,src,ln,col,err) => errors.push((err&&err.stack)||`${msg} @line ${ln}`);
    w.addEventListener("error", e => errors.push((e.error&&e.error.stack)||e.message));
  }
});
setTimeout(() => {
  if (errors.length) {
    console.log("✗ BOOT FAILED — the UI would be blank on device:");
    for (const e of errors.slice(0,3)) console.log("  " + String(e).split("\n").slice(0,3).join("\n  "));
    process.exit(1);
  }
  console.log("✓ boot test: page initializes with no runtime errors");
  process.exit(0);
}, 700);
