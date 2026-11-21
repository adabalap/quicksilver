"""Records the phone walkthrough as sharp (2x) frames, then writes phone.mp4 and timeline.json.
   python record.py <workdir> <narration_dir>"""
import base64, json, os, subprocess, sys, time, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import seed
from playwright.sync_api import sync_playwright

WORK, NARR = sys.argv[1], sys.argv[2]
os.makedirs(WORK + "/frames", exist_ok=True)
for f in os.listdir(WORK + "/frames"): os.remove(WORK + "/frames/" + f)
DB = WORK + "/demo.db"; PORT = "5395"; BASE = "http://127.0.0.1:" + PORT
for f in (DB, DB + "-wal", DB + "-shm"):
    if os.path.exists(f): os.remove(f)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sp = subprocess.Popen([sys.executable, ROOT + "/app.py"], cwd=ROOT, env=dict(os.environ, QS_DB=DB, QS_PORT=PORT), stdout=open(WORK + "/srv.log", "w"), stderr=subprocess.STDOUT)
for _ in range(60):
    try: urllib.request.urlopen(BASE + "/api/home"); break
    except Exception: time.sleep(.5)

info = json.load(open(NARR + "/info.json"))
PAD = {"intro": 1.2, "write": 2.0, "outro": 2.2}
DUR = {k: v["dur"] + PAD.get(k, 1.0) for k, v in info.items()}
TYPED = seed.MESSY
RIPPLE = """(()=>{const s=document.createElement('style');s.textContent='.tapr{position:fixed;width:46px;height:46px;margin:-23px 0 0 -23px;border-radius:50%;background:rgba(108,92,231,.35);border:2px solid rgba(108,92,231,.8);pointer-events:none;z-index:99999;animation:tapr .55s ease-out forwards}@keyframes tapr{from{transform:scale(.4);opacity:1}to{transform:scale(1.5);opacity:0}}';document.head.appendChild(s);
addEventListener('pointerdown',e=>{const d=document.createElement('div');d.className='tapr';d.style.left=e.clientX+'px';d.style.top=e.clientY+'px';document.body.appendChild(d);setTimeout(()=>d.remove(),600)},true)})()"""

try:
    ids = seed.seed(BASE, DB)
    agent = seed.StandInAgent(BASE, DB); agent.start(); time.sleep(1.2)
    with sync_playwright() as p:
        br = p.chromium.launch()
        ctx = br.new_context(viewport={"width": 412, "height": 860}, device_scale_factor=2, has_touch=False)
        ctx.add_init_script("localStorage.setItem('hg_tour','1');localStorage.setItem('hg_note_theme','colorful')")
        pg = ctx.new_page()
        frames = []
        cdp = ctx.new_cdp_session(pg)
        def on_frame(params):
            n = len(frames); path = f"{WORK}/frames/{n:05d}.jpg"
            open(path, "wb").write(base64.b64decode(params["data"]))
            frames.append((params["metadata"]["timestamp"], path))
            cdp.send("Page.screencastFrameAck", {"sessionId": params["sessionId"]})
        cdp.on("Page.screencastFrame", on_frame)
        pg.goto(BASE); pg.wait_for_timeout(1800)
        pg.evaluate("document.getElementById('tour')&&document.getElementById('tour').classList.remove('on')")
        pg.evaluate(RIPPLE)
        pg.evaluate("window.scrollTo(0,0)")
        cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 95, "maxWidth": 824, "maxHeight": 1720, "everyNthFrame": 1})
        pg.wait_for_timeout(400)
        T0 = time.time(); starts = {}; state = {}
        W = pg.wait_for_timeout
        def scene(name):
            starts[name] = time.time() - T0
            state["end"] = time.time() + DUR[name]
            print("scene", name, round(starts[name], 2), flush=True)
        def rest():
            left = state["end"] - time.time()
            if left > 0: W(int(left * 1000))
        def click(sel, wait=500):
            pg.click(sel); W(wait)

        scene("intro"); rest()

        scene("write")
        click("#fabBtn", 900); click("#fabText", 900)
        delay = max(25, min(70, int((DUR["write"] - 3.6) * 1000 / len(TYPED))))
        pg.keyboard.type(TYPED, delay=delay); W(500)
        click("#edDone", 300)
        nid = json.load(urllib.request.urlopen(BASE + "/api/notes?limit=1"))
        nid = (nid["notes"] if isinstance(nid, dict) else nid)[0]["id"]
        rest()

        scene("magic")
        W(1000); seed.enrich_typed(BASE, DB, nid); W(1500)
        pg.evaluate(f"openDetail('{nid}')"); W(2800)
        click("#ctaBtn", 3200)
        click("#adoptConfirm", 1800)
        click("#detExpand", 900)
        sc = "(()=>{const b=document.getElementById('dBody'); if(b) b.scrollBy({top:%d,behavior:'smooth'})})()"
        while state["end"] - time.time() > 2.2:
            pg.evaluate(sc % 170); W(1000)
        pg.evaluate("(()=>{const b=document.getElementById('dBody'); if(b) b.scrollTo({top:0,behavior:'smooth'})})()")
        rest()

        scene("find")
        click("#dBack", 700)
        click("#srchBtn", 500)
        pg.keyboard.type("invoice", delay=130); W(1800)
        pg.evaluate("(()=>{const b=document.querySelector('#vNotes'); if(b) b.scrollBy({top:120,behavior:'smooth'})})()"); rest()

        scene("ask")
        click("#srchBack", 400)
        pg.evaluate("S.q='';var i=document.getElementById('srchIn'); if(i) i.value=''")
        click("#srchBtn", 500); click("#srchModes button[data-mode=ask]", 500)
        pg.keyboard.type("what was that book idea about a lighthouse?", delay=50); W(300)
        click("#askGo", 400); W(3800)
        pg.evaluate("(()=>{const b=document.querySelector('#vNotes'); if(b) b.scrollBy({top:160,behavior:'smooth'})})()"); rest()

        scene("bills")
        click("#srchBack", 300)
        pg.evaluate("exitAskMode(); document.body.classList.remove('ask-docked')")
        pg.evaluate("window.scrollTo(0,0); document.querySelectorAll('.view').forEach(v=>v.scrollTop=0)")
        click(".nitem[data-view=vNotes]", 400)
        W(1200)
        click("[data-hs='bills-all']", 1400)
        pg.fill("input[name=merchant]", ""); pg.click("input[name=merchant]")
        pg.keyboard.type("Water board", delay=55); pg.click("input[name=amount]"); pg.keyboard.type("640", delay=80); W(500)
        click("#billAddForm button[type=submit]", 1800)
        click("[data-bill-act=paid]", 1600)
        click("#billBack", 300); rest()

        scene("brief"); rest()

        scene("capture")
        click("#fabBtn", 1700)
        click("#fabSketch", 1200)
        cv = pg.query_selector("canvas")
        if cv:
            bb = cv.bounding_box(); cx, cy = bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2
            import math
            pg.mouse.move(cx - 90, cy + 60); pg.mouse.down()
            for pt in [(cx - 90, cy - 20), (cx, cy - 90), (cx + 90, cy - 20), (cx + 90, cy + 60), (cx - 90, cy + 60), (cx - 90, cy - 20), (cx + 90, cy - 20)]:
                pg.mouse.move(pt[0], pt[1], steps=14)
            pg.mouse.up(); W(300)
            pg.mouse.move(cx - 20, cy + 60); pg.mouse.down(); pg.mouse.move(cx - 20, cy + 5, steps=8); pg.mouse.move(cx + 25, cy + 5, steps=8); pg.mouse.move(cx + 25, cy + 60, steps=8); pg.mouse.up()
        rest()

        scene("themes")
        pg.evaluate("document.getElementById('sketchSheet').classList.remove('on'); document.getElementById('fabMenu') && document.getElementById('fabMenu').classList.remove('on')")
        pg.evaluate("lsSet('hg_bills_hide', new Date().toISOString().slice(0,10)); renderHomeStrips()")
        pg.evaluate("document.querySelectorAll('.view').forEach(v=>v.scrollTop=0); window.scrollTo(0,0)"); W(150)
        for th in ("cartoon", "material", "ocean", "midnight"):
            pg.evaluate(f"setNoteTheme('{th}')"); W(int(max(800, (DUR['themes'] - 0.5) / 4 * 1000)))
        rest()

        scene("outro"); rest()
        end_t = time.time() - T0
        cdp.send("Page.stopScreencast"); W(300)
        ts0 = frames[0][0]
        json.dump({"starts": {k: v for k, v in starts.items()}, "dur": DUR, "end": end_t, "first_frame_offset": ts0 - (T0 - 0.4)}, open(WORK + "/timeline.json", "w"), indent=1)
        # constant-frame-rate video from variable-timestamp frames
        lines = []
        for i, (ts, path) in enumerate(frames):
            d = (frames[i + 1][0] - ts) if i + 1 < len(frames) else 0.5
            lines += [f"file '{path}'", f"duration {max(d, 0.001):.4f}"]
        lines.append(f"file '{frames[-1][1]}'")
        open(WORK + "/frames.txt", "w").write("\n".join(lines))
        json.dump({"t_first": ts0, "T0": T0}, open(WORK + "/clock.json", "w"))
        print("frames", len(frames), "recorded", round(end_t, 1), "s")
finally:
    sp.terminate()
