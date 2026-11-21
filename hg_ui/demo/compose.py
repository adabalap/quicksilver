"""Builds the final video: backdrop, phone (with frame), captions, cards, voice-over and a soft pad.
   python compose.py <workdir> <narration_dir> <out.mp4>"""
import json, os, subprocess, sys
import numpy as np, soundfile as sf
from playwright.sync_api import sync_playwright

WORK, NARR, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
tl = json.load(open(WORK + "/timeline.json")); clk = json.load(open(WORK + "/clock.json")); info = json.load(open(NARR + "/info.json"))
OFF = clk["T0"] - clk["t_first"]; S = tl["starts"]; D = tl["dur"]; END = S["outro"] + D["outro"] + 0.4
W, H = 1920, 1080; PW, PH = 460, 960; PX, PY = 250, 60
CARDS = ["intro", "brief", "outro"]
PHONE_SCENES = ["write", "magic", "find", "ask", "bills", "capture", "themes"]

CSS = """
*{box-sizing:border-box;margin:0}
body{width:1920px;height:1080px;font-family:'Plus Jakarta Sans','Noto Sans Telugu',sans-serif;color:#fff;overflow:hidden;position:relative}
.bg{background:radial-gradient(1200px 800px at 15% 20%,#3b2fa0 0%,transparent 60%),radial-gradient(900px 700px at 90% 90%,#0b6e8a 0%,transparent 55%),linear-gradient(135deg,#120e2b,#1d1650 55%,#0e2a3a)}
.tel{font-family:'Noto Sans Telugu',sans-serif}
.kick{display:inline-block;font-size:26px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;color:#bdb4ff;background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.14);padding:10px 20px;border-radius:999px}
h1{font-size:92px;line-height:1.04;font-weight:800;letter-spacing:-.02em}
.sub{font-size:38px;line-height:1.35;font-weight:500;color:rgba(255,255,255,.78);max-width:860px}
.brand{position:absolute;left:70px;bottom:46px;font-size:30px;font-weight:700;color:rgba(255,255,255,.85);display:flex;gap:14px;align-items:baseline}
.brand small{font-size:22px;font-weight:500;color:rgba(255,255,255,.55)}
.cap{position:absolute;left:900px;top:0;height:1080px;width:900px;display:flex;flex-direction:column;justify-content:center;gap:34px}
.center{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;gap:26px}
.mark{font-size:210px;font-weight:800;letter-spacing:-.01em;line-height:1.2;background:linear-gradient(120deg,#fff,#c9c2ff 45%,#7de3f0);-webkit-background-clip:text;color:transparent}
.word{font-size:104px;font-weight:800;letter-spacing:.02em}
.tag{font-size:44px;font-weight:500;color:rgba(255,255,255,.82);max-width:1300px;line-height:1.35}
.panel{text-align:left;width:1080px;background:rgba(255,255,255,.96);color:#1d1a26;border-radius:34px;padding:26px 30px;box-shadow:0 40px 100px rgba(0,0,0,.45)}
.ph{display:flex;align-items:center;justify-content:space-between;font-size:26px;font-weight:800;color:#5a5568;letter-spacing:.08em;text-transform:uppercase;padding:6px 10px 16px}
.row{display:flex;align-items:center;gap:26px;padding:22px 20px;border-top:1px solid #ececf1}
.ic{width:76px;height:76px;border-radius:22px;display:flex;align-items:center;justify-content:center;font-size:40px;flex:none}
.rt{font-size:36px;font-weight:800}.rs{font-size:27px;color:#5a5568;margin-top:4px}
.pill{margin-left:auto;font-size:26px;font-weight:800;padding:9px 20px;border-radius:999px;white-space:nowrap}
.note{font-size:28px;color:rgba(255,255,255,.6);margin-top:8px}
.grid{display:grid;grid-template-columns:repeat(2,1fr);gap:26px;width:1180px;margin-top:10px}
.g{background:rgba(255,255,255,.09);border:1px solid rgba(255,255,255,.16);border-radius:30px;padding:30px 34px;text-align:left}
.g b{display:block;font-size:42px;margin-bottom:6px}.g span{font-size:29px;color:rgba(255,255,255,.72);line-height:1.35}
"""
BRAND = '<div class="brand"><span class="tel">పాదరసం</span> Quicksilver <small>private, on-device AI notes</small></div>'
def page(inner, bg): return f"<html><head><meta charset=utf-8><style>{CSS}</style></head><body class='{'bg' if bg else ''}'>{inner}</body></html>"
CAPS = {
 "write":   ("1 · Capture", "Just write.", "Messy, fast, no punctuation. One breathless line is fine."),
 "magic":   ("2 · Organise", "Chaos in. Clarity out.", "A clean note, tasks, and every date set as a reminder. You review it first."),
 "find":    ("3 · Find", "Type a word. Found.", "Search your notes instantly, all on the device."),
 "ask":     ("4 · Remember", "Brings your thought back.", "Ask in plain words. The answer comes only from your notes, with the source cited."),
 "bills":   ("5 · Bills", "Never miss a due date.", "Read from messages on your phone. Mark paid, or add your own."),
 "capture": ("6 · Capture anything", "Voice. Photo. Sketch.", "Telugu voice notes too. It listens, reads and remembers."),
 "themes":  ("7 · Make it yours", "Cartoon. Material. Ocean. Midnight.", "Pick a mood for your whole feed."),
}
HTML = {}
for k, (kick, h, sub) in CAPS.items():
    HTML[k] = (page(f"<div class='cap'><div><span class='kick'>{kick}</span></div><h1>{h}</h1><div class='sub'>{sub}</div></div>{BRAND.replace('class=\"brand\"', 'class=\"brand\" style=\"left:900px\"')}", False), True)
HTML["intro"] = (page(f"<div class='center'><div class='mark tel'>పాదరసం</div><div class='word'>Quicksilver</div><div class='tag'>The notebook that thinks with you, and never sends a word anywhere.</div></div>", True), False)
HTML["brief"] = (page(f"""<div class='center'><span class='kick'>8 · Always in reach</span>
<h1 style='font-size:84px'>What needs you, on your home screen.</h1>
<div class='panel'><div class='ph'><span>Now brief</span><span>tap opens the exact note or bill</span></div>
<div class='row'><div class='ic' style='background:#fde3e8'>🧾</div><div><div class='rt'>HDFC credit card · ₹12,480</div><div class='rs'>Credit card bill due. Tap to open it and mark it paid.</div></div><div class='pill' style='background:#fde3e8;color:#b0304a'>in 3d</div></div>
<div class='row'><div class='ic' style='background:#e3f0ff'>🔔</div><div><div class='rt'>Plumber visit</div><div class='rs'>Thursday after 5 pm. Ask about the tap washer.</div></div><div class='pill' style='background:#e3f0ff;color:#1f5fb0'>Thu</div></div>
<div class='row'><div class='ic' style='background:#e6f6ec'>✅</div><div><div class='rt'>Call Ravi about the kitchen quote</div><div class='rs'>An open task from your notes.</div></div><div class='pill' style='background:#e6f6ec;color:#1d7a46'>to do</div></div></div>
<div class='note'>Shown through Medha's Now brief. Illustration.</div></div>{BRAND}""", True), False)
HTML["outro"] = (page(f"""<div class='center'><div class='mark tel' style='font-size:150px'>పాదరసం</div><div class='word' style='font-size:84px'>Quicksilver</div>
<div class='grid'><div class='g'><b>On your phone</b><span>Notes are read by a model that runs on the device.</span></div><div class='g'><b>No cloud, no account</b><span>Nothing is uploaded, tracked or sold.</span></div>
<div class='g'><b>Yours to keep</b><span>Plain SQLite database. Export and back up any time.</span></div><div class='g'><b>Works offline</b><span>Capture anywhere and it syncs when you are back.</span></div></div>
<div class='tag' style='margin-top:14px'>Notes that move, join, and never stain.</div></div>""", True), False)

os.makedirs(WORK + "/layers", exist_ok=True)
with sync_playwright() as p:
    br = p.chromium.launch(); pg = br.new_page(viewport={"width": W, "height": H})
    for k, (html, transparent) in HTML.items():
        pg.set_content(html); pg.wait_for_timeout(250)
        pg.screenshot(path=f"{WORK}/layers/{k}.png", omit_background=transparent)
    pg.set_content(page('', True)); pg.wait_for_timeout(200)
    pg.screenshot(path=f"{WORK}/layers/bg.png")
    # phone frame (transparent window) and rounded mask
    fr = f"<html><body style='margin:0;background:transparent'><div style='position:absolute;left:{PX-14}px;top:{PY-14}px;width:{PW+28}px;height:{PH+28}px;border-radius:64px;border:14px solid #0b0a12;box-shadow:0 30px 90px rgba(0,0,0,.55),0 0 0 2px #3a3560'></div></body></html>"
    pg.set_content(fr); pg.screenshot(path=f"{WORK}/layers/frame.png", omit_background=True)
    pg.set_content(f"<html><body style='margin:0;background:#000'><div style='width:{PW}px;height:{PH}px;border-radius:50px;background:#fff'></div></body></html>")
    pg.set_viewport_size({"width": PW, "height": PH}); pg.screenshot(path=f"{WORK}/layers/mask.png")
    br.close()

# ── audio: voice-over + a very soft pad
SR = 24000; n = int(END * SR); voice = np.zeros(n, dtype=np.float32)
for k, v in info.items():
    a, sr = sf.read(f"{NARR}/{k}.wav", dtype="float32"); assert sr == SR
    i = int((S[k] + 0.35) * SR); voice[i:i + len(a)] += a[: max(0, n - i)]
BPM = 112; beat = 60 / BPM; rng = np.random.default_rng(7)
def add(buf, i, x, g=1.0):
    j = min(n, i + len(x))
    if i < n: buf[i:j] += g * x[: j - i]
def tt(d): return np.arange(int(d * SR)) / SR
kick = np.sin(2 * np.pi * (48 * tt(.28) + 90 * (1 - np.exp(-tt(.28) * 30)) / 30 * 1)) * np.exp(-tt(.28) * 13)
hat = rng.standard_normal(int(.06 * SR)) * np.exp(-tt(.06) * 70); hat = np.diff(hat, prepend=0)
clap = rng.standard_normal(int(.16 * SR)) * np.exp(-tt(.16) * 26)
def note(f, d, sh=1.0):
    x = tt(d); e = np.minimum(1, x / .006) * np.exp(-x * sh)
    return (np.sin(2*np.pi*f*x) + .35*np.sin(4*np.pi*f*x) + .15*np.sin(6*np.pi*f*x)) * e
mus = np.zeros(n, dtype=np.float32); t = np.arange(n) / SR
prog = [(110.0, [220.0, 277.18, 329.63]), (87.31, [174.61, 220.0, 261.63]), (130.81, [261.63, 329.63, 392.0]), (98.0, [196.0, 246.94, 293.66])]
drums_on = S["write"] - 0.1; lift = S["themes"]; outro = S["outro"]
nb = int(END / beat) + 2
for b in range(nb):
    tb = b * beat; i = int(tb * SR); bar = b // 4; root, ch = prog[bar % 4]; bt = b % 4
    full = tb >= drums_on
    if full:
        add(mus, i, kick, .9)
        if bt in (1, 3): add(mus, i, clap, .22)
        add(mus, i + int(beat / 2 * SR), hat, .16)
        if tb >= lift or bt == 3: add(mus, i + int(beat * .75 * SR), hat, .08)
        add(mus, i, note(root / 2, beat * .95, 2.2), .22)
        add(mus, i + int(beat / 2 * SR), note(root / 2, beat * .4, 4), .14)
    arp = [ch[0], ch[1], ch[2], ch[1]]
    for q in range(2):
        f = arp[(b * 2 + q) % 4] * (2 if tb >= lift else 1)
        add(mus, i + int(q * beat / 2 * SR), note(f, beat * .5, 5), .09 if full else .05)
    if bt == 0 and tb < outro: add(mus, i, sum(note(f, beat * 3.6, .9) for f in ch) / 3, .07)
# riser into the first beat
rs = int(drums_on * SR); rl = int(2.2 * SR)
if rs > rl:
    x = rng.standard_normal(rl) * (np.linspace(0, 1, rl) ** 2); x = np.convolve(x, np.ones(20) / 20, "same"); mus[rs - rl:rs] += .1 * x
# duck under voice
env = np.abs(voice); k = int(.12 * SR); env = np.convolve(env, np.ones(k) / k, "same"); env = np.clip(env * 9, 0, 1)
duck = 1 - .6 * env
mus *= duck * np.minimum(1, t / 1.5) * np.minimum(1, (END - t) / 2.5)
pk = np.max(np.abs(mus)) or 1; mus = mus / pk * .30
sf.write(WORK + "/audio.wav", np.clip(voice * 0.95 + mus, -1, 1).astype(np.float32), SR)

# ── video graph
def iv(names): return "+".join(f"between(t,{S[k]:.3f},{S[k] + D[k]:.3f})" for k in names)
inputs = ["-loop", "1", "-framerate", "30", "-t", f"{END:.2f}", "-i", f"{WORK}/layers/bg.png",
          "-i", f"{WORK}/phone.mp4",
          "-loop", "1", "-framerate", "30", "-t", f"{END:.2f}", "-i", f"{WORK}/layers/mask.png",
          "-loop", "1", "-framerate", "30", "-t", f"{END:.2f}", "-i", f"{WORK}/layers/frame.png"]
fg = [f"[1:v]trim=start={OFF:.3f},setpts=PTS-STARTPTS,scale={PW}:{PH},format=rgba[pv]",
      f"[2:v]scale={PW}:{PH},format=gray[mk]", "[pv][mk]alphamerge[pvr]",
      f"[0:v][pvr]overlay={PX}:{PY}:enable='{iv(PHONE_SCENES)}'[a]",
      f"[a][3:v]overlay=0:0:enable='{iv(PHONE_SCENES)}'[b]"]
last = "b"; idx = 4
for k in PHONE_SCENES + CARDS:
    d = D[k]
    inputs += ["-loop", "1", "-framerate", "30", "-t", f"{d:.2f}", "-i", f"{WORK}/layers/{k}.png"]
    fg.append(f"[{idx}:v]format=rgba,fade=t=in:st=0:d=0.4:alpha=1,fade=t=out:st={d - 0.4:.2f}:d=0.4:alpha=1,setpts=PTS+{S[k]:.3f}/TB[c{idx}]")
    fg.append(f"[{last}][c{idx}]overlay=0:0:eof_action=pass:enable='between(t,{S[k]:.3f},{S[k] + d:.3f})'[o{idx}]")
    last = f"o{idx}"; idx += 1
fg.append(f"[{last}]fade=t=in:st=0:d=0.5,fade=t=out:st={END - 0.8:.2f}:d=0.8,format=yuv420p[v]")
inputs += ["-i", WORK + "/audio.wav"]
cmd = ["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", ";".join(fg), "-map", "[v]", "-map", f"{idx}:a",
       "-t", f"{END:.2f}", "-r", "30", "-c:v", "libx264", "-preset", "medium", "-crf", "21", "-pix_fmt", "yuv420p",
       "-af", f"afade=t=out:st={END - 1.5:.2f}:d=1.5", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", OUT]
subprocess.run(cmd, check=True)
print("wrote", OUT, os.path.getsize(OUT) // 1024, "KB")
