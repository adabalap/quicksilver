#!/usr/bin/env python3
"""Video notes in the agent: sound is transcribed, a few frames are described, either half may be missing."""
import sys, os, subprocess, shutil, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
P = F = 0
def check(c, m):
    global P, F
    if c: P += 1
    else: F += 1; print(f"  ✗ {m}")
if not shutil.which("ffmpeg"):
    print("ffmpeg not installed: video test skipped"); sys.exit(0)
from memos_daemon.media import Router, MediaUnavailable, MediaFailed

tmp = tempfile.mkdtemp()
def make(name, secs, audio=True):
    p = os.path.join(tmp, name)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc=size=160x120:rate=10:duration={secs}"]
    if audio: cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={secs}"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p"] + (["-c:a", "aac", "-shortest"] if audio else []) + [p]
    subprocess.run(cmd, check=True); return p

class Eng:
    """Stands in for Medha: speech-to-text plus a vision model."""
    active = "medha"
    def __init__(self, audio=True, vision=True): self.audio, self.vision, self.frames, self.stt = audio, vision, [], []
    def can_multimodal(self, k, fresh=False): return self.audio if k == "audio" else self.vision
    def transcribe_audio(self, path, language="auto", prompt=""):
        self.stt.append(os.path.getsize(path)); return "[00:01] remember to renew the passport"
    def multimodal(self, parts, prompt, max_tokens=None):
        self.frames.append(parts[0]["path"]); n = len(self.frames)
        return f"DESCRIPTION: Frame {n}: a colour test pattern with a clock.\nTEXT: " + ("NONE" if n != 2 else "PASSPORT OFFICE")
Eng.__name__ = "MedhaEngine"

long_clip = make("long.mp4", 40); short_clip = make("short.mp4", 3); silent = make("silent.mp4", 20, audio=False)
cfg = {"media_enabled": True, "media_backend": "litert", "media_video_frames": 4, "media_video_max_s": 300, "media_describe": True, "media_job_timeout_s": 120}

e = Eng(); r = Router(dict(cfg), e)
text, be = r.run("video", long_clip, "video/mp4")
check(be == "medha", "the model's name is recorded")
check(text.startswith("Spoken: ") and "renew the passport" in text, "the spoken words come first")
check("On screen:" in text and text.count("\n- ") == 4, "four frames are described for a 40 s clip")
check("Text: PASSPORT OFFICE" in text and "Text: NONE" not in text, "on-screen text is kept, 'NONE' is dropped")
check("- 0:05 " in text, "frames are timestamped (first of four at ~5 s)")
check(len(e.stt) == 1 and e.stt[0] > 1000, "the sound track was extracted and sent to speech-to-text")

e = Eng(); r = Router(dict(cfg), e); text, _ = r.run("video", short_clip, "video/mp4")
check(text.count("\n- ") == 1 or text.count("- 0:") == 1, "a 3 s clip is described with a single frame")

e = Eng(); r = Router(dict(cfg), e); text, _ = r.run("video", silent, "video/mp4")
check("Spoken:" not in text and "On screen:" in text and not e.stt, "a silent video skips speech-to-text")

e = Eng(vision=False); r = Router(dict(cfg), e); text, _ = r.run("video", long_clip, "video/mp4")
check(text.startswith("Spoken:") and "On screen" not in text and not e.frames, "no vision model: sound only")

e = Eng(audio=False); r = Router(dict(cfg), e); text, _ = r.run("video", long_clip, "video/mp4")
check("Spoken:" not in text and "On screen:" in text, "no speech engine: frames only")

e = Eng(audio=False, vision=False); r = Router(dict(cfg), e)
try: r.run("video", long_clip, "video/mp4"); check(False, "no engine should be unavailable")
except MediaUnavailable: check(True, "no engine at all: unavailable (not retried)")

c2 = dict(cfg, media_video_frames=0); e = Eng(); r = Router(c2, e); text, _ = r.run("video", long_clip, "video/mp4")
check(text.startswith("Spoken:") and not e.frames, "media_video_frames=0 reads the sound only")

c3 = dict(cfg, media_video_max_s=10); e = Eng(); r = Router(c3, e); text, _ = r.run("video", long_clip, "video/mp4")
check(text.count("\n- ") == 2, "media_video_max_s limits how much is read (10 s → 2 frames)")


# heat gate: asked before the sound and before every frame; returning False stops the job; progress is reported
calls, msgs = [], []
e = Eng(); r = Router(dict(cfg), e); r.run("video", long_clip, "video/mp4", gate=lambda: calls.append(1) or True, progress=msgs.append)
check(len(calls) == 5 and msgs[0].startswith("sound") and msgs[-1] == "frame 4 of 4", "the heat gate runs before the sound and each of 4 frames, with progress")
e = Eng(); r = Router(dict(cfg), e)
try: r.run("video", long_clip, "video/mp4", gate=lambda: False); check(False, "a closed gate should stop the job")
except MediaFailed: check(not e.stt and not e.frames, "a closed gate stops the job before any model work")

r = Router(dict(cfg, media_enabled=False), Eng())
try: r.run("video", long_clip, "video/mp4"); check(False, "disabled should raise")
except MediaUnavailable: check(True, "media off: unavailable")
try: Router(dict(cfg), Eng()).run("video", os.path.join(tmp, "nope.mp4"), "video/mp4"); check(False, "missing file should raise")
except MediaFailed: check(True, "missing file: failed (retried, then dead-lettered)")

print(f"\n{P} passed, {F} failed"); sys.exit(1 if F else 0)
