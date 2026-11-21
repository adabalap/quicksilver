#!/usr/bin/env python3
"""Medha integration: capability auto-detect, max_tokens, priority header, busy/413/501 handling, medha_check."""
import sys, os, json, threading, time, types, io, contextlib, base64, argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
P = F = 0
def check(c, m):
    global P, F
    if c: P += 1
    else: F += 1; print(f"  ✗ {m}")

STATE = {"caps": {"text": True, "vision": False, "audio": False}, "seen": [], "busy": 0, "chat_status": 200, "chat_code": ""}
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, obj, headers=None):
        b = json.dumps(obj).encode(); self.send_response(code)
        self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b)))
        for k, v in (headers or {}).items(): self.send_header(k, v)
        self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"status": "ok", "modelLoaded": True, "backend": "GPU", "capabilities": STATE["caps"], "max_context": 4096})
        elif self.path == "/system":
            self._send(200, {"multimodal": {"vision": False, "audio": False, "imageMethods": [], "audioMethods": [], "sdkMethodCount": 47}})
        else: self._send(404, {})
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0); body = json.loads(self.rfile.read(n) or b"{}")
        STATE["seen"].append((self.path, dict(self.headers), body))
        if self.path == "/generate":
            if STATE["busy"] > 0:
                STATE["busy"] -= 1; return self._send(429, {"error": "busy"}, {"Retry-After": "1"})
            if "TOO_LONG" in body.get("prompt", ""):
                return self._send(413, {"error": {"code": "context_overflow"}})
            cap = body.get("max_tokens")
            text = " ".join(str(i) for i in range(1, 401))
            if cap: text = " ".join(text.split()[:cap])
            if "తెలుగు" in body.get("prompt", ""): text = "సముద్రం చాలా అందంగా ఉంది."
            self._send(200, {"text": text, "finish_reason": "length" if cap else "stop"})
        elif self.path == "/v1/chat/completions":
            msgs = body.get("messages") or []
            if not msgs: return self._send(400, {"error": {"code": "bad_request"}})
            c = msgs[0].get("content")
            if isinstance(c, list) and any(p.get("type") == "image_url" and not p["image_url"]["url"].startswith("data:") for p in c):
                return self._send(422, {"error": {"code": "remote_image_unsupported"}})
            if STATE["chat_status"] != 200:
                return self._send(STATE["chat_status"], {"error": {"code": STATE["chat_code"]}})
            self._send(200, {"choices": [{"message": {"content": "A man driving a car."}}]})
srv = HTTPServer(("127.0.0.1", 0), H); port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()

from memos_daemon.medha_engine import MedhaEngine, priority, _mm_mode
from memos_daemon.auto_engine import AutoEngine
from memos_daemon.media import Router, LiteRTBackend
base = {"medha_url": f"http://127.0.0.1:{port}", "medha_token": "t", "medha_busy_wait_s": 5}

check([_mm_mode(x) for x in (True, False, "auto", "TRUE", "off", None, "yes")] == ["on", "off", "auto", "on", "off", "auto", "on"], "medha_multimodal parses true/false/auto")

# ── capability follow
e = MedhaEngine(dict(base))            # default = auto
check(e._mm_cfg == "auto" and not e.can_multimodal("image") and not e.can_multimodal("audio") and not e._mm_enabled, "auto: vision/audio off while Medha says false")
STATE["caps"] = {"text": True, "vision": True, "audio": False}; e._caps_cache = (0.0, {})
check(e.can_multimodal("image") and not e.can_multimodal("audio") and e._mm_enabled, "auto: follows Medha — vision on, audio still off (per kind)")
STATE["caps"] = {"text": True, "vision": True, "audio": True}; e._caps_cache = (0.0, {})
check(e.can_multimodal("audio"), "auto: audio switches on when Medha reports it")
STATE["caps"] = {"text": True, "vision": False, "audio": False}
check(MedhaEngine(dict(base, medha_multimodal=True)).can_multimodal("image"), "true forces it on")
STATE["caps"] = {"text": True, "vision": True, "audio": True}
check(not MedhaEngine(dict(base, medha_multimodal=False)).can_multimodal("image"), "false forces it off")
STATE["caps"] = {"text": True, "vision": False, "audio": False}
try:
    MedhaEngine(dict(base)).multimodal([{"type": "image", "path": __file__}], "x"); check(False, "gated")
except RuntimeError as ex:
    check("doesn't report vision" in str(ex), "gated: a clear message when Medha says vision is off")

# ── router follows capabilities (tesseract stays the live path until Medha reports vision)
ae = AutoEngine(dict(base))
r = Router({"media_backend": "auto", "media_enabled": True}, ae)
check(r.llm is not None and not r.llm.can("image") and not r.llm.can("audio"), "router: Medha vision/audio unavailable while it reports false")
STATE["caps"] = {"text": True, "vision": True, "audio": False}; ae._medha._caps_cache = (0.0, {})
check(r.llm.can("image") and not r.llm.can("audio"), "router: image goes to Medha once it reports vision, audio doesn't")

# ── max_tokens + priority
STATE["seen"].clear(); e = MedhaEngine(dict(base))
out = e._generate([], "count", cap=24)
p, hd, body = STATE["seen"][-1]
check(body.get("max_tokens") == 24 and len(out.split()) == 24, "/generate carries max_tokens and Medha honours it")
check(hd.get("X-Medha-Priority") == "batch", "default priority is batch")
class Probe(MedhaEngine):
    def _generate(self, messages, prompt, cap=None): return MedhaEngine._generate(self, messages, prompt, cap)
with priority("interactive"): e._generate([], "hi", cap=10)
check(STATE["seen"][-1][1].get("X-Medha-Priority") == "interactive", "priority('interactive') is sent")
check(STATE["seen"][-1][1].get("X-Medha-Priority") != "batch" and getattr(__import__("memos_daemon.medha_engine", fromlist=["_prio"])._prio, "v", None) is None, "priority resets afterwards")
STATE["seen"].clear()
e2 = MedhaEngine(dict(base))
e2.chat("hello there")                  # Chat is interactive
check(STATE["seen"] and STATE["seen"][-1][1].get("X-Medha-Priority") == "interactive", "Chat goes out as interactive")
STATE["seen"].clear()
ae2 = AutoEngine(dict(base)); ae2.story("a photo", outline="late for a movie")
check(STATE["seen"] and STATE["seen"][-1][1].get("X-Medha-Priority") == "interactive", "Story through the auto engine goes out as interactive")
STATE["seen"].clear(); ae2.complete("enrich this note", max_tokens=100)
check(STATE["seen"][-1][1].get("X-Medha-Priority") == "batch", "plain completions (enrichment, digests) stay batch")

# ── busy → Retry-After honoured; give up cleanly when it never clears
STATE["busy"] = 2; t0 = time.monotonic()
out = MedhaEngine(dict(base))._generate([], "x", cap=5)
check(out and time.monotonic() - t0 >= 2, "429 waits out Retry-After and then succeeds")
STATE["busy"] = 50
try:
    MedhaEngine(dict(base, medha_busy_wait_s=2))._generate([], "x"); check(False, "busy give-up")
except RuntimeError as ex:
    check("busy" in str(ex) and not AutoEngine._is_server_error(ex), "a Medha that stays busy raises a busy error that does NOT trigger the local fallback")
STATE["busy"] = 0

# ── 413 → overflow handling recognises it
try:
    MedhaEngine(dict(base))._generate([], "TOO_LONG")
except RuntimeError as ex:
    from memos_daemon.engine import GemmaEngine
    check(GemmaEngine._is_token_overflow(ex), "413 context_overflow is recognised as a token overflow (trim-and-retry)")

# ── chat errors
img = os.path.join(os.path.dirname(os.path.abspath(__file__)), "smoke_test.py")
open("/tmp/_mh.jpg", "wb").write(base64.b64decode("/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////wgALCAABAAEBAREA/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxA="))
cfg_on = dict(base, medha_multimodal=True)
STATE["chat_status"], STATE["chat_code"] = 501, "multimodal_unsupported"
try:
    MedhaEngine(cfg_on).multimodal([{"type": "image", "path": "/tmp/_mh.jpg"}], "x"); check(False, "501")
except RuntimeError as ex:
    check("doesn't accept images" in str(ex), "501 multimodal_unsupported → 'doesn't accept images/audio yet' (fallback path)")
STATE["chat_status"], STATE["chat_code"] = 413, "image_too_large"
try:
    MedhaEngine(cfg_on).multimodal([{"type": "image", "path": "/tmp/_mh.jpg"}], "x"); check(False, "413")
except RuntimeError as ex:
    check("image_too_large" in str(ex), "413 image_too_large is reported with its code")
STATE["chat_status"], STATE["chat_code"] = 200, ""
check(MedhaEngine(cfg_on).multimodal([{"type": "image", "path": "/tmp/_mh.jpg"}], "x") == "A man driving a car.", "a supported image round-trips")

# ── medha_check
from memos_daemon import cli
a = argparse.Namespace(image="/tmp/_mh.jpg", sketch=None, telugu_image=None, audio_en=None, audio_te=None, expect_en=None, expect_te=None)
buf = io.StringIO()
with contextlib.redirect_stdout(buf): rc = cli.cmd_medha_check(a, dict(base))
o = buf.getvalue()
check(rc == 0 and "✓ PASS  health" in o and "max_tokens is a real cap" in o and "· skip" in o and "7 bad input is refused cleanly" in o, "medha_check passes against a conforming Medha and skips what has no sample file")
check("1 photo → description" in o and "A man driving a car." in o and "no --audio-te given" in o, "medha_check shows photo result and skips voice without clips")
check("/system → multimodal" in o and "sdkMethodCount" in o, "medha_check prints the /system multimodal block when capabilities are false")
STATE["chat_status"], STATE["chat_code"] = 501, "multimodal_unsupported"
buf = io.StringIO()
with contextlib.redirect_stdout(buf): rc = cli.cmd_medha_check(a, dict(base))
check(rc == 1 and "✗ FAIL  1 photo" in buf.getvalue(), "medha_check fails (exit 1) when the photo check fails")

# ── command backend: broken UTF-8 from whisper.cpp (Telugu cut mid-character) must not crash the job
from memos_daemon.media import _run, _text
out = _run([sys.executable, "-c", "import sys; sys.stdout.buffer.write('నమస్తే '.encode() + 'ప్ర'.encode()[:2])"], 10)
check(out.startswith("నమస్తే") and "\ufffd" not in out, "a transcript cut mid-character is decoded, not a UnicodeDecodeError")
check(_text(None) == "" and _text("ok") == "ok" and _text(b"a\xff") == "a", "_text tolerates None, str and bad bytes")
try:
    _run([sys.executable, "-c", "import sys; sys.stderr.write('boom\\n'); sys.exit(3)"], 10); check(False, "exit code")
except Exception as ex:
    check("exited 3: boom" in str(ex), "a failing command still reports its exit code and last stderr line")

# ── model output that is complete but not strict JSON, and blank titles ──
from memos_daemon.engine import GemmaEngine
raw = '{"title": "Plumber visit", "summary": "Plumber comes Thursday.", "polished": "Line one\nLine two", "tags": ["home"], "priority": "low"}'
d = GemmaEngine._parse(raw)
check(d and d.get("title") == "Plumber visit" and d["polished"] == "Line one\nLine two", "a newline inside a string value still parses (it used to fall into the truncated path)")
d = GemmaEngine._parse('{"title": "\n  ", "tags": ["personal"], "summary": "x"')
check(d is None or not d.get("title", "").strip(), "a whitespace-only title is never salvaged as the title")
eng = MedhaEngine({"medha_url": "http://127.0.0.1:1", "medha_token": "t"})
eng._generate = lambda messages, prompt, cap=None: '{"title": "\n  ", "summary": "", "polished": "", "tags": ["personal"], "priority": "low", "confidence": 0.7}'
r = eng.enrich("Note testing message for testing\nmore words here")
check(r and r["title"] == "Note testing message for testing", "a blank title falls back to the note's first line")
srv.shutdown()
print(f"\n  {P} passed, {F} failed")
sys.exit(1 if F else 0)
