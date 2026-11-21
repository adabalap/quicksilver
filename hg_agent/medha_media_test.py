#!/usr/bin/env python3
"""Voice and photos through Medha: /v1/audio/transcriptions, per-run capability check, whisper fallback,
description + text for photos and sketches, and the Medha-vs-whisper comparison log."""
import sys, os, json, wave, struct, tempfile, threading, time, re, io, contextlib, argparse, shutil
from http.server import BaseHTTPRequestHandler, HTTPServer
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
P = F = 0
def check(c, m):
    global P, F
    if c: P += 1
    else: F += 1; print(f"  ✗ {m}")

S = {"audio": True, "vision": True, "reqs": [], "stt_status": 200, "stt_busy": 0, "stt_text": "నమస్తే మిత్రమా", "chat": "DESCRIPTION: A man driving a car.\nTEXT: NONE", "chats": []}
def multipart(headers, body):
    b = re.search(r'boundary=(.+)', headers.get("Content-Type", "")).group(1).strip('"').encode()
    fields, file = {}, None
    for part in body.split(b"--" + b)[1:-1]:
        head, _, data = part.partition(b"\r\n\r\n"); data = data[:-2] if data.endswith(b"\r\n") else data
        name = re.search(rb'name="([^"]+)"', head).group(1).decode()
        if b"filename=" in head: file = data
        else: fields[name] = data.decode()
    return fields, file
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, obj, headers=None):
        b = json.dumps(obj).encode(); self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        for k, v in (headers or {}).items(): self.send_header(k, v)
        self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        self._send(200, {"status": "ok", "modelLoaded": True, "backend": "GPU", "capabilities": {"text": True, "vision": S["vision"], "audio": S["audio"]}})
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0); raw = self.rfile.read(n)
        if self.path == "/v1/audio/transcriptions":
            fields, file = multipart(self.headers, raw)
            S["reqs"].append({"fields": fields, "bytes": len(file or b""), "auth": self.headers.get("Authorization"), "prio": self.headers.get("X-Medha-Priority")})
            if S["stt_busy"] > 0:
                S["stt_busy"] -= 1; return self._send(429, {"error": "busy"}, {"Retry-After": "1"})
            if S["stt_status"] != 200: return self._send(S["stt_status"], {"error": {"code": "multimodal_unsupported"}})
            return self._send(200, {"text": S["stt_text"]})
        if self.path == "/v1/chat/completions":
            body = json.loads(raw); S["chats"].append(body)
            self._send(200, {"choices": [{"message": {"content": S["chat"]}}]})
        else: self._send(404, {})
srv = HTTPServer(("127.0.0.1", 0), H); port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()

from memos_daemon.medha_engine import MedhaEngine
from memos_daemon.auto_engine import AutoEngine
from memos_daemon.media import Router, _parse_analysis, _plausible_text
base = {"medha_url": f"http://127.0.0.1:{port}", "medha_token": "tok", "medha_busy_wait_s": 5}
td = tempfile.mkdtemp()
def wav(path, secs, rate=16000):
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(struct.pack("<h", 100) * int(rate * secs))
    return path
W5, W70 = wav(f"{td}/a5.wav", 5), wav(f"{td}/a70.wav", 70)
PNG = f"{td}/p.png"
open(PNG, "wb").write(bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478da63f8cfc0f01f0005000201a2a1f3b60000000049454e44ae426082"))

# ── /v1/audio/transcriptions
e = MedhaEngine(dict(base))
out = e.transcribe_audio(W5, language="te")
r = S["reqs"][-1]
check(out == "నమస్తే మిత్రమా", "the transcript comes back from /v1/audio/transcriptions")
check(r["fields"].get("language") == "te" and r["fields"].get("response_format") == "json" and "model" in r["fields"], "language=te, response_format=json and model are sent")
check(r["auth"] == "Bearer tok" and r["prio"] == "batch" and r["bytes"] > 1000, "bearer token, batch priority and the WAV bytes are sent")
S["reqs"].clear(); e.transcribe_audio(W5, language="auto")
check("language" not in S["reqs"][-1]["fields"], "auto omits language (Medha autodetects)")
S["reqs"].clear(); e.transcribe_audio(W5, language="en", prompt="Adabala, Quicksilver")
check(S["reqs"][-1]["fields"].get("language") == "en" and S["reqs"][-1]["fields"].get("prompt") == "Adabala, Quicksilver", "language=en and spelling hints are passed")
S["reqs"].clear(); out = e.transcribe_audio(W70, language="te")
check(len(S["reqs"]) == 3 and out == " ".join([S["stt_text"]] * 3), f"70 s is cut into 3 requests (28 + 28 + 14 s) and joined ({len(S['reqs'])})")
check(all(q["bytes"] <= 16000 * 2 * 28 + 100 for q in S["reqs"]), "every piece is within Medha's 30 s limit")
S["stt_busy"] = 2; t0 = time.monotonic()
check(e.transcribe_audio(W5, language="te") and time.monotonic() - t0 >= 2, "a 429 waits out Retry-After and then succeeds")
S["stt_status"] = 501
try: e.transcribe_audio(W5, language="te"); check(False, "501")
except RuntimeError as ex: check("doesn't accept audio" in str(ex), "501 → 'doesn't accept audio yet'")
S["stt_status"] = 200

# ── router: voice goes to Medha only while /health says audio — checked on every recording
whisper = f"{td}/whisper.py"; open(whisper, "w").write("import sys; print('whisper heard: ' + sys.argv[1].split('/')[-1])")
cfg = {"media_backend": "auto", "media_audio_cmd": f"{sys.executable} {whisper} {{file}}", "media_audio_lang": "te", "state_file": f"{td}/state.json", "media_image_cmd": ""}
ae = AutoEngine(dict(base, **cfg))
rt = Router(cfg, ae)
S["audio"] = True; S["reqs"].clear()
text, be = rt.run("audio", W5, "audio/wav")
check(be == "medha" and text == "నమస్తే మిత్రమా" and len(S["reqs"]) == 1, "voice uses Medha when capabilities.audio is true")
S["audio"] = False; S["reqs"].clear()
text, be = rt.run("audio", W5, "audio/wav")      # NO cache clearing: the check must be fresh each run
check(be == "command" and text.startswith("whisper heard") and not S["reqs"], "the very next recording uses whisper once Medha stops reporting audio")
S["audio"] = True
S["stt_status"] = 501
text, be = rt.run("audio", W5, "audio/wav")
check(be == "command" and text.startswith("whisper heard"), "if Medha errors on a clip, whisper does that clip")
S["stt_status"] = 200
S["stt_text"] = ""
text, be = rt.run("audio", W5, "audio/wav")
check(be == "command", "an empty Medha transcript falls back to whisper")
S["stt_text"] = "నమస్తే మిత్రమా"
check("voice: medha (fallback: command)" in rt.describe(), f"startup line shows Medha with whisper behind it ({rt.describe()})")
none = Router({"media_backend": "litert"}, ae)
S["audio"] = False
try: none.run("audio", W5, "audio/wav"); check(False, "no engine")
except Exception as ex: check(type(ex).__name__ == "MediaUnavailable", "no whisper and Medha without audio → unavailable (not a crash)")
S["audio"] = True

# ── photos: description + the text the model reads, in one call
check(_parse_analysis("DESCRIPTION: A man driving.\nTEXT: NONE") == {"text": "", "description": "A man driving."}, "parse: NONE means no text")
check(_parse_analysis("**DESCRIPTION:** A signboard.\n**TEXT:** స్వాగతం\nరెండవ లైన్") == {"text": "స్వాగతం\nరెండవ లైన్", "description": "A signboard."}, "parse: bold labels, Telugu text and line breaks")
check(_parse_analysis('{"text":"HELLO","description":"A sign."}') == {"text": "HELLO", "description": "A sign."}, "parse: JSON still accepted")
check(_parse_analysis("A dog on a stone floor.") == {"text": "", "description": "A dog on a stone floor."}, "parse: plain prose becomes the description")
check(_parse_analysis("") is None, "parse: empty reply is nothing")
check(not _plausible_text("~~ ;; ,,") and not _plausible_text("ab") and _plausible_text("Invoice 4521 total due"), "OCR noise is not mistaken for text")
S["chats"].clear(); S["chat"] = "DESCRIPTION: A man driving a car, looking worried.\nTEXT: NONE"
text, be = rt.run("image", PNG, "image/png")
prompt = S["chats"][-1]["messages"][0]["content"][-1]["text"]
check(text == "Description: A man driving a car, looking worried." and be == "medha" and len(S["chats"]) == 1, "photo: one call → 'Description: …' (no text found)")
check("DESCRIPTION:" in prompt and "TEXT:" in prompt and "Telugu" in prompt and "hand-drawn" not in prompt, "photo prompt asks for both description and text, Telugu-aware")
check(S["chats"][-1]["messages"][0]["content"][0]["image_url"]["url"].startswith("data:image/"), "photo goes as an image data URL")
S["chat"] = "DESCRIPTION: A shop board.\nTEXT: శ్రీ లక్ష్మి స్టోర్స్"
text, be = rt.run("image", PNG, "image/png")
check(text == "Description: A shop board.\n\nText: శ్రీ లక్ష్మి స్టోర్స్", "photo with writing → description and the text it read")
S["chats"].clear(); S["chat"] = "DESCRIPTION: A floor plan of a two-room house.\nTEXT: Kitchen\nBedroom"
text, be = rt.run("sketch", PNG, "image/png")
prompt = S["chats"][-1]["messages"][0]["content"][-1]["text"]
check(text == "Description: A floor plan of a two-room house.\n\nText: Kitchen\nBedroom" and "hand-drawn sketch" in prompt, "sketch: always described, and its labels are read")
# a fake tesseract that returns noise on a plain photo must not leak into the text
noisy = f"{td}/ocr.py"; open(noisy, "w").write("print('~~ ;; ,, ``')")
rt2 = Router({**cfg, "media_image_cmd": f"{sys.executable} {noisy} {{file}}"}, ae)
S["chat"] = "DESCRIPTION: A dog.\nTEXT: NONE"
text, be = rt2.run("image", PNG, "image/png")
check(text == "Description: A dog." , f"OCR noise doesn't pollute a described photo ({text!r})")
real = f"{td}/ocr2.py"; open(real, "w").write("print('INVOICE 2231 total due 4500')")
rt3 = Router({**cfg, "media_image_cmd": f"{sys.executable} {real} {{file}}"}, ae)
text, be = rt3.run("image", PNG, "image/png")
check(text == "Description: A dog.\n\nText: INVOICE 2231 total due 4500", "tesseract double-checks only when the model found no text")
S["vision"] = False
rt4 = Router({**cfg, "media_image_cmd": f"{sys.executable} {real} {{file}}"}, ae); ae._medha._caps_cache = (0.0, {})
text, be = rt4.run("image", PNG, "image/png")
check(be == "command" and "INVOICE" in text, "vision off → tesseract as before")
S["vision"] = True; ae._medha._caps_cache = (0.0, {})

# ── image prep: PNG keeps its type, 4-image cap
e = MedhaEngine(dict(base, medha_multimodal=True))
S["chats"].clear(); e.multimodal([{"type": "image", "path": PNG}] * 6, "x")
c = S["chats"][-1]["messages"][0]["content"]
check(sum(1 for p in c if p["type"] == "image_url") == 4, "at most 4 images go in one request")
ff = shutil.which("ffmpeg"); shutil.which = lambda n, _o=shutil.which: None if n == "ffmpeg" else _o(n)
check(e._prep_image(PNG).startswith("data:image/png;base64,"), "without ffmpeg a PNG is sent as image/png, not mislabeled")
shutil.which = lambda n: ff if n == "ffmpeg" else None

# ── comparison log
S["audio"] = True
cfg5 = {**cfg, "media_audio_compare": True}
rt5 = Router(cfg5, ae)
check(rt5.wants_compare(), "comparison is on while whisper is available")
check(not Router({**cfg5, "media_audio_compare": False}, ae).wants_compare(), "comparison can be switched off")
rt5.compare_audio("clip1", W5, "audio/wav", "నమస్తే మిత్రమా", 3.2)
rows = [json.loads(l) for l in open(rt5.compare_file(), encoding="utf-8")]
check(len(rows) == 1 and rows[0]["id"] == "clip1" and rows[0]["medha"]["telugu"] > 0 and rows[0]["whisper"]["text"].startswith("whisper heard")
      and rows[0]["medha"]["secs"] == 3.2 and rows[0]["similarity"] is not None, "compare writes both transcripts, script counts, timings and agreement")
check(str(rt5.compare_file()).startswith(td), "the log lives next to the agent's state file")
from memos_daemon import cli
buf = io.StringIO()
with contextlib.redirect_stdout(buf): cli.cmd_media_compare(argparse.Namespace(last=3), cfg5)
o = buf.getvalue()
check("1 clips compared" in o and "Telugu-script answers: medha 1/1" in o and "M: నమస్తే మిత్రమా" in o and "W: whisper heard" in o, "media_compare prints a readable summary")

# ── medha_check uses the real endpoints
a = argparse.Namespace(image=PNG, sketch=PNG, telugu_image=None, audio_en=W5, audio_te=W5, expect_en=None, expect_te="నమస్తే మిత్రమా")
buf = io.StringIO()
with contextlib.redirect_stdout(buf): rc = cli.cmd_medha_check(a, dict(base))
o = buf.getvalue()
check("✓ PASS  3 English voice clip" in o and "✓ PASS  4 Telugu voice clip" in o and "similarity to expected 100%" in o, "medha_check tests voice via /v1/audio/transcriptions, with a similarity score")
check("✓ PASS  1 photo → description + text" in o and "✓ PASS  2 sketch → described" in o, "medha_check tests photo and sketch with the agent's real prompts")
srv.shutdown()
print(f"\n  {P} passed, {F} failed")
sys.exit(1 if F else 0)
