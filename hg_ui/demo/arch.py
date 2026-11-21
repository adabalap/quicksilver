"""Builds the 'How it stays private' architecture video.  python arch.py <workdir> <out.mp4>"""
import json, os, subprocess, sys
import numpy as np, soundfile as sf
from playwright.sync_api import sync_playwright
WORK, OUT = sys.argv[1], sys.argv[2]; os.makedirs(WORK, exist_ok=True)
SC = [
 ("intro", "", "Where do your notes go? Nowhere. Here is exactly how Quicksilver keeps it that way."),
 ("phone", "phone db", "Everything lives on one phone. The app, a plain SQLite database, and your photos and voice notes, in a folder you can back up."),
 ("agent", "agent medha", "A small agent notices each new note and asks Medha, an AI model that runs on the phone itself, to read it. No API key, because there is no server."),
 ("embed", "embed", "Search and Ask use a local embedding service on the same device, listening only on the loopback address, so nothing else on the network can reach it."),
 ("tunnel", "tunnel", "Want your notes on a laptop? An optional Cloudflare Tunnel carries the app to your own address. It is off unless you set it up, and it never carries the model."),
 ("honest", "honest", "Two honest notes. Live captions can use your browser's speech service, so they are off by default. And bill messages are read on the phone, then deleted once paid."),
 ("outro", "", "No cloud. No account. No analytics. Quicksilver."),
]
CSS = """*{box-sizing:border-box;margin:0}body{width:1920px;height:1080px;position:relative;overflow:hidden;color:#fff;font-family:'Plus Jakarta Sans','Noto Sans Telugu',sans-serif;background:radial-gradient(1200px 800px at 15% 20%,#3b2fa0 0%,transparent 60%),radial-gradient(900px 700px at 90% 90%,#0b6e8a 0%,transparent 55%),linear-gradient(135deg,#120e2b,#1d1650 55%,#0e2a3a)}
h1{position:absolute;left:80px;top:48px;font-size:60px;font-weight:800;letter-spacing:-.01em}
.cap{position:absolute;left:80px;right:80px;bottom:40px;font-size:34px;line-height:1.35;color:rgba(255,255,255,.88);font-weight:500;min-height:92px}
.box{position:absolute;border-radius:30px;background:rgba(255,255,255,.07);border:2px solid rgba(255,255,255,.18);transition:none}
.n{position:absolute;border-radius:24px;padding:22px 26px;background:rgba(255,255,255,.10);border:2px solid rgba(255,255,255,.2);opacity:.45}
.n b{display:block;font-size:34px;font-weight:800}.n span{font-size:24px;color:rgba(255,255,255,.75);line-height:1.3;display:block;margin-top:6px}
.on{opacity:1;background:rgba(125,227,240,.18);border-color:#7de3f0;box-shadow:0 0 60px rgba(125,227,240,.35)}
.lab{position:absolute;font-size:24px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:#bdb4ff}
svg{position:absolute;left:0;top:0}
.cloud{position:absolute;right:90px;top:70px;width:360px;text-align:center;font-size:30px;font-weight:700;color:#ffb3c1;border:3px dashed #ff6b86;border-radius:26px;padding:18px}
.big{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;gap:20px}
.mark{font-size:170px;font-weight:800;background:linear-gradient(120deg,#fff,#c9c2ff 45%,#7de3f0);-webkit-background-clip:text;color:transparent;line-height:1.2}
.q{font-size:70px;font-weight:800;max-width:1400px;line-height:1.15}.pl{display:flex;gap:26px;margin-top:20px}.pl div{font-size:40px;font-weight:800;background:rgba(255,255,255,.1);border:2px solid rgba(255,255,255,.2);border-radius:22px;padding:18px 30px}
.ok{position:absolute;right:90px;top:320px;width:360px;font-size:26px;line-height:1.35;color:rgba(255,255,255,.8)}
"""
def diagram(act, cap):
    a = set(act.split()); c = lambda k: "n on" if k in a else "n"
    line = lambda x1,y1,x2,y2,k: f"<line x1='{x1}' y1='{y1}' x2='{x2}' y2='{y2}' stroke='{'#7de3f0' if k in a else 'rgba(255,255,255,.25)'}' stroke-width='{5 if k in a else 3}' stroke-dasharray='{'' if k!='tunnel' else '14 10'}'/>"
    return f"""<html><head><meta charset=utf-8><style>{CSS}</style></head><body><h1>How your notes stay private</h1>
<div class='cloud'>☁ Cloud servers<br><span style='font-size:24px;font-weight:500'>never involved</span></div>
<div class='box' style='left:80px;top:170px;width:1180px;height:700px;{"border-color:#7de3f0" if "phone" in a else ""}'></div><div class='lab' style='left:116px;top:190px'>Your phone · Android + Termux</div>
<svg width='1920' height='1080'>{line(345,370,345,520,'agent')}{line(560,580,760,580,'agent')}{line(345,650,345,740,'db')}{line(980,650,980,740,'db')}{line(560,310,760,310,'embed')}{line(1200,310,1500,310,'tunnel')}</svg>
<div class='{c("phone")}' style='left:130px;top:260px;width:430px'><b>Quicksilver app</b><span>hg_ui · web app on 127.0.0.1:5230</span></div>
<div class='{c("agent")}' style='left:130px;top:520px;width:430px'><b>Agent</b><span>hg_agent · reads each new note</span></div>
<div class='{c("agent")}' style='left:760px;top:520px;width:440px'><b>Medha · local AI</b><span>language model on the phone, 127.0.0.1:8001</span></div>
<div class='{c("embed")}' style='left:760px;top:260px;width:440px'><b>Embeddings</b><span>search and Ask, 127.0.0.1:5232</span></div>
<div class='{c("db")}' style='left:130px;top:740px;width:1070px'><b>SQLite database + attachments folder</b><span>your text, tags, tasks, photos and voice · export and back up any time</span></div>
<div class='{c("tunnel")}' style='left:1500px;top:250px;width:340px'><b>Optional tunnel</b><span>Cloudflare Tunnel to your own address</span></div>
<div class='{c("tunnel")}' style='left:1500px;top:480px;width:340px'><b>Your browser</b><span>laptop or another phone</span></div>
<svg width='1920' height='1080'>{line(1670,400,1670,480,'tunnel')}</svg>
<div class='ok' style='top:700px;opacity:{1 if "honest" in a else 0}'><b style='color:#ffd98a'>Worth knowing</b><br>Live captions may use the browser's speech service (off by default). Bill messages are read on the phone and deleted once paid.</div>
<div class='cap'>{cap}</div></body></html>"""
def card(kind):
    if kind == "intro": inner = "<div class='big'><div class='mark' style='font-size:130px'>పాదరసం</div><div class='q'>Where do your notes go?</div><div class='q' style='color:#7de3f0'>Nowhere.</div></div>"
    else: inner = "<div class='big'><div class='mark'>పాదరసం</div><div class='q'>Quicksilver</div><div class='pl'><div>No cloud</div><div>No account</div><div>No analytics</div></div></div>"
    return f"<html><head><meta charset=utf-8><style>{CSS}</style></head><body>{inner}</body></html>"
from kokoro_onnx import Kokoro
d = os.environ.get("KOKORO_DIR", "/tmp/tts"); k = Kokoro(d + "/kokoro.onnx", d + "/voices.bin")
SR = 24000; PADS = {"intro": 1.0, "outro": 2.0}; items = []; t = 0.0
for sid, act, text in SC:
    s, sr = k.create(text, voice="af_heart", speed=0.96, lang="en-us"); assert sr == SR
    dur = len(s) / sr + 0.35 + PADS.get(sid, 1.0); items.append((sid, act, text, s, t, dur)); t += dur
END = t + 0.3
with sync_playwright() as p:
    br = p.chromium.launch(); pg = br.new_page(viewport={"width": 1920, "height": 1080})
    for sid, act, text, s, st, dur in items:
        pg.set_content(card(sid) if sid in ("intro", "outro") else diagram(act, text)); pg.wait_for_timeout(300)
        pg.screenshot(path=f"{WORK}/{sid}.png")
    br.close()
n = int(END * SR); voice = np.zeros(n, np.float32)
for sid, act, text, s, st, dur in items:
    i = int((st + .35) * SR); voice[i:i + len(s)] += s[: n - i]
# music: calm pulse, a touch brighter than the main demo, ducked under voice
BPM = 96; beat = 60 / BPM; rng = np.random.default_rng(3); mus = np.zeros(n, np.float32); tt = lambda d: np.arange(int(d * SR)) / SR
def add(i, x, g):
    j = min(n, i + len(x))
    if i < n: mus[i:j] += g * x[: j - i]
def note(f, d, sh):
    x = tt(d); return (np.sin(2*np.pi*f*x) + .3*np.sin(4*np.pi*f*x)) * np.minimum(1, x/.006) * np.exp(-x*sh)
kick = np.sin(2*np.pi*(55*tt(.25) + 40*np.exp(-tt(.25)*25)/25)) * np.exp(-tt(.25)*14)
prog = [(110.0,[220.0,277.18,329.63]),(87.31,[174.61,220.0,261.63]),(130.81,[261.63,329.63,392.0]),(98.0,[196.0,246.94,293.66])]
for b in range(int(END / beat) + 2):
    i = int(b * beat * SR); root, ch = prog[(b // 4) % 4]; full = b * beat > items[1][4] - .1
    if full and b % 2 == 0: add(i, kick, .5)
    add(i, note(root / 2, beat * .9, 2.5), .22 if full else .1)
    for q in range(2): add(i + int(q * beat / 2 * SR), note([ch[0], ch[1], ch[2], ch[1]][(b * 2 + q) % 4], beat * .5, 5), .1)
    if b % 4 == 0: add(i, sum(note(f, beat * 3.8, .8) for f in ch) / 3, .08)
env = np.convolve(np.abs(voice), np.ones(2880) / 2880, "same"); duck = 1 - .6 * np.clip(env * 9, 0, 1)
tx = np.arange(n) / SR; mus *= duck * np.minimum(1, tx / 1.5) * np.minimum(1, (END - tx) / 2.5); mus = mus / (np.max(np.abs(mus)) or 1) * .28
sf.write(WORK + "/audio.wav", np.clip(voice * .95 + mus, -1, 1), SR)
inp, fg = [], []
for j, (sid, act, text, s, st, dur) in enumerate(items):
    inp += ["-loop", "1", "-framerate", "30", "-t", f"{dur + .5:.2f}", "-i", f"{WORK}/{sid}.png"]
    fg.append(f"[{j}:v]format=rgba,fade=t=in:st=0:d=0.4:alpha=1,setpts=PTS+{st:.3f}/TB[c{j}]")
fg.insert(0, f"color=c=#120e2b:s=1920x1080:r=30:d={END:.2f}[base]"); last = "base"
for j in range(len(items)):
    fg.append(f"[{last}][c{j}]overlay=0:0:eof_action=pass[o{j}]"); last = f"o{j}"
fg.append(f"[{last}]fade=t=out:st={END-.8:.2f}:d=0.8,format=yuv420p[v]")
inp += ["-i", WORK + "/audio.wav"]
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *inp, "-filter_complex", ";".join(fg), "-map", "[v]", "-map", f"{len(items)}:a", "-t", f"{END:.2f}", "-r", "30",
    "-c:v", "libx264", "-crf", "21", "-pix_fmt", "yuv420p", "-af", f"afade=t=out:st={END-1.5:.2f}:d=1.5", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", OUT], check=True)
print("wrote", OUT, round(END, 1), "s")
