"""media.py — reading photos and transcribing voice notes, on the phone.

Two ways to do the work, picked by `media_backend` in config:

  "command"  Run a program you already have (the dependable route on Termux):
               • images → tesseract  (pkg install tesseract; add language data
                 for Telugu etc. with `pkg install tesseract-data-tel` or the
                 tessdata_best files)
               • audio  → whisper.cpp  (whisper-cli -m <model> -f {wav} …)
             Programs are run WITHOUT a shell, with the file path substituted
             into each argument, so a filename can never inject a command.

  "litert"   Ask the Gemma model itself (needs a multimodal model and a
             litert_lm build with vision/audio support; set
             media_litert_multimodal=true so the engine loads those towers).

  "auto"     (default) use "command" for whatever is installed, then "litert"
             if multimodal is on, else report that nothing can do the job.

Everything here is local: files never leave the device.
"""
from __future__ import annotations
import difflib, inspect, json, os, re, shlex, shutil, subprocess, tempfile, threading, time
from pathlib import Path
from .logging_setup import log


class MediaUnavailable(Exception):
    """This backend can't do that kind of work at all (don't retry)."""


class MediaFailed(Exception):
    """It tried and failed (worth a retry, then give up)."""


_TS = re.compile(r"^\s*\[\d{1,2}:\d{2}[:.\d]*\s*-->\s*[\d:.]+\]\s*", re.M)
_MAX_TEXT = 20_000


def clean_text(s: str) -> str:
    s = (s or "").replace("\r\n", "\n")
    s = _TS.sub("", s)                                  # whisper timestamps
    s = re.sub(r"\[(?:BLANK_AUDIO|MUSIC|Music|SILENCE)\]", "", s)
    s = re.sub(r"[ \t]+\n", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s).strip()
    return s[:_MAX_TEXT]


_NO_TEXT = re.compile(
    r"^\W*(?:none|nothing|n/?a|no text|no writing)\W*$"
    r"|^\W*(?:no|there (?:is|are) no|i (?:cannot|can't|could not|couldn't) (?:see|find|read)|did not find|didn't find)\b"
    r"[^\n]{0,60}\b(?:text|writing|handwriting|words|legible|readable)\b[^\n]{0,50}$", re.I)


def _json_obj(raw: str):
    """Pull a JSON object out of a model reply (tolerates code fences / chatter)."""
    import json as _j
    t = (raw or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.I)
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        return _j.loads(t[i:j + 1], strict=False)
    except ValueError:
        return None


def no_text(s: str) -> bool:
    """True when a model 'reads' an image by saying there is nothing to read."""
    t = (s or "").strip()
    return bool(t) and len(t) < 120 and "\n" not in t and bool(_NO_TEXT.match(t))


def _text(b) -> str:
    """Decode a program's output without ever raising. whisper.cpp prints a Telugu/Hindi/… character
    token by token and can cut it mid-sequence (a UTF-8 'unexpected end of data' crash), so
    broken bytes are dropped instead of failing the whole recording."""
    if b is None:
        return ""
    if isinstance(b, str):
        return b
    return b.decode("utf-8", errors="replace").replace("\ufffd", "")


def _run(argv: list[str], timeout: int) -> str:
    try:
        p = subprocess.run(argv, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        raise MediaUnavailable(f"'{argv[0]}' isn't installed")
    except subprocess.TimeoutExpired:
        raise MediaFailed(f"'{argv[0]}' took longer than {timeout}s")
    out, err = _text(p.stdout), _text(p.stderr)
    if p.returncode != 0:
        tail = (err or out).strip().splitlines()[-1:] or [""]
        raise MediaFailed(f"'{argv[0]}' exited {p.returncode}: {tail[0][:200]}")
    return out


class CommandBackend:
    name = "command"

    def __init__(self, cfg: dict):
        self.timeout = int(cfg.get("media_job_timeout_s", 600))
        self.audio_cmd = (cfg.get("media_audio_cmd") or "").strip()
        self.image_cmd = (cfg.get("media_image_cmd") or "").strip()
        self.lang = (cfg.get("media_audio_lang") or "auto").strip()
        self.ocr_langs = (cfg.get("media_ocr_langs") or "eng").strip()
        model = os.path.expanduser((cfg.get("media_whisper_model") or "").strip())
        if not self.audio_cmd and model and Path(model).exists():
            exe = shutil.which("whisper-cli") or shutil.which("whisper-cpp") or shutil.which("main")
            if exe:
                self.audio_cmd = f"{shlex.quote(exe)} -m {shlex.quote(model)} -f {{wav}} -l {{lang}} -nt -np"
        if not self.image_cmd and shutil.which("tesseract"):
            self.image_cmd = "tesseract {file} stdout -l {ocr_langs}"
        self.hint = ""

    def can(self, kind: str) -> bool:
        return bool(self.audio_cmd if kind == "audio" else self.image_cmd)

    def _argv(self, template: str, **subs) -> list[str]:
        out = []
        for tok in shlex.split(template):
            for k, v in subs.items():
                tok = tok.replace("{" + k + "}", str(v))
            out.append(tok)
        return out

    def transcribe(self, path: str, mime: str = "") -> str:
        if not self.audio_cmd:
            raise MediaUnavailable("no speech-to-text command configured (media_audio_cmd / media_whisper_model)")
        with tempfile.TemporaryDirectory(prefix="qs-media-") as td:
            wav = path
            if "{wav}" in self.audio_cmd:
                ff = shutil.which("ffmpeg")
                if not ff:
                    raise MediaUnavailable("ffmpeg isn't installed (pkg install ffmpeg) — needed to prepare audio")
                wav = os.path.join(td, "in.wav")
                _run([ff, "-y", "-loglevel", "error", "-i", path, "-ar", "16000", "-ac", "1",
                      "-c:a", "pcm_s16le", wav], self.timeout)
            out = _run(self._argv(self.audio_cmd, file=path, wav=wav, lang=self.lang), self.timeout)
        return clean_text(out)

    def read_image(self, path: str, mime: str = "", handwriting: bool = False) -> str:
        if not self.image_cmd:
            raise MediaUnavailable("no text-recognition command found (install tesseract, or set media_image_cmd)")
        out = _run(self._argv(self.image_cmd, file=path, ocr_langs=self.ocr_langs, lang=self.ocr_langs),
                   self.timeout)
        return clean_text(out)


def _plausible_text(t: str) -> bool:
    """Is this OCR output real text rather than noise (tesseract invents characters on a plain photo)?"""
    t = (t or "").strip()
    chars = [c for c in t if not c.isspace()]
    if len(chars) < 8:
        return False
    good = sum(1 for c in chars if c.isalnum())
    return good / len(chars) >= 0.6


_LABEL = re.compile(r"(?im)^[\s*#_>]*(description|text)[\s*_]*[:\-][\s*_]*")


def _parse_analysis(raw: str):
    """A model's reply to the photo/sketch prompt -> {"text","description"} (or None).
    Asked for `DESCRIPTION: …` / `TEXT: …`; also accepts JSON and plain prose, since a small
    model doesn't always follow the format."""
    t = (raw or "").strip()
    t = re.sub(r"^```[a-z]*\s*|\s*```$", "", t, flags=re.I).strip()
    if not t:
        return None
    norm = _LABEL.sub(lambda m: m.group(1).upper() + ": ", t)
    m = re.search(r"(?s)\bDESCRIPTION: (.*?)(?:\n\s*TEXT: (.*))?\Z", norm)
    if m and m.group(1).strip():
        desc, text = m.group(1).strip(), (m.group(2) or "").strip()
    else:
        d = _json_obj(t)
        if isinstance(d, dict) and (d.get("description") or d.get("text")):
            desc, text = str(d.get("description") or ""), str(d.get("text") or "")
        else:
            m2 = re.search(r"(?s)\bTEXT: (.*)\Z", norm)
            if m2:
                desc, text = "", m2.group(1)
            else:
                desc, text = t, ""                      # just prose: it is the description
    text = clean_text(text)
    text = "" if no_text(text) else text
    desc = clean_text(desc)[:1500]
    if not text and not desc:
        return None
    return {"text": text, "description": desc}


class LiteRTBackend:
    """The model itself — Medha (GPU, already resident) or the in-process model.
    Voice, photos and sketches."""

    def __init__(self, engine):
        self.engine = engine

    @property
    def name(self) -> str:
        e = self.engine
        return "medha" if (getattr(e, "active", None) == "medha" or type(e).__name__ == "MedhaEngine") else "litert"

    def can(self, kind: str, fresh: bool = False) -> bool:
        fn = getattr(self.engine, "can_multimodal", None)      # Medha reports vision and audio separately
        k = "audio" if kind == "audio" else "image"
        if callable(fn):
            try:
                return bool(fn(k, fresh=fresh))
            except TypeError:
                return bool(fn(k))
        return bool(getattr(self.engine, "_mm_enabled", False))

    def _mm(self, parts, prompt, max_tokens=None):
        fn = self.engine.multimodal
        if max_tokens and "max_tokens" in inspect.signature(fn).parameters:
            return fn(parts, prompt, max_tokens=max_tokens)
        return fn(parts, prompt)

    def transcribe(self, path: str, mime: str = "") -> str:
        if not self.can("audio"):
            raise MediaUnavailable("the model wasn't loaded with audio support")
        cfg = getattr(self.engine, "_cfg", None) or {}
        fn = getattr(self.engine, "transcribe_audio", None)
        try:
            if callable(fn):                                   # Medha: POST /v1/audio/transcriptions
                t = fn(path, language=str(cfg.get("media_audio_lang") or "auto").strip().lower(),
                       prompt=str(cfg.get("media_audio_prompt") or ""))
            else:
                t = self.engine.multimodal(
                    [{"type": "audio", "path": path}],
                    "Transcribe this recording exactly as spoken, in the language spoken. "
                    "Output only the transcript, no commentary.")
        except RuntimeError as e:
            m = str(e)
            if "doesn't accept" in m or "doesn't report" in m or "wasn't loaded" in m:
                raise MediaUnavailable(m)
            raise MediaFailed(m)                               # busy / dropped / refused: retry, then give up
        return clean_text(t)

    def read_image(self, path: str, mime: str = "", handwriting: bool = False) -> str:
        if not self.can("image"):
            raise MediaUnavailable("the model wasn't loaded with vision support")
        what = "handwriting" if handwriting else "printed or handwritten text"
        try:
            t = self._mm([{"type": "image", "path": path}],
                         f"Read all the {what} in this image and write it out exactly as written, "
                         "keeping line breaks and the original language/script. If there is no text, "
                         "reply with nothing at all. Output only the text.", 700)
        except RuntimeError as e:
            raise MediaUnavailable(str(e))
        t = clean_text(t)
        return "" if no_text(t) else t

    def analyze(self, path: str, sketch: bool = False):
        """ONE model call that returns a description AND the text the model reads in the image
        (instead of two calls ≈ 2× the time and heat). -> {"text","description"} or None."""
        if not self.can("image"):
            return None
        if sketch:
            prompt = ("This is a hand-drawn sketch. Reply in exactly this format and nothing else:\n"
                      "DESCRIPTION: <1 to 3 plain sentences saying what it depicts — for example a floor plan, "
                      "a diagram, a flower, a to-do list — and what its main parts are>\n"
                      "TEXT: <every label or word written in it, exactly as written, in its original language "
                      "and script, or NONE if there is no writing>")
        else:
            prompt = ("Look at this image. Reply in exactly this format and nothing else:\n"
                      "DESCRIPTION: <1 to 3 plain sentences saying what the image shows: the main subject, "
                      "notable details, the setting>\n"
                      "TEXT: <all text visible in the image written out exactly as it appears, keeping line breaks "
                      "and the original language and script (Telugu stays in Telugu script), or NONE if there "
                      "is no text>")
        try:
            raw = self._mm([{"type": "image", "path": path}], prompt, 700)
        except RuntimeError:
            return None
        return _parse_analysis(raw)

    def describe(self, path: str, sketch: bool = False) -> str:
        r = self.analyze(path, sketch)
        if r is None:
            raise MediaUnavailable("the model couldn't describe this image")
        return r["description"]


_COMPARE_LOCK = threading.Lock()


def _letters(t: str):
    tel = sum(1 for c in t if "\u0c00" <= c <= "\u0c7f")
    lat = sum(1 for c in t if c.isascii() and c.isalpha())
    return tel, lat


class Router:
    """Chooses a backend per kind of work and runs it.

    Voice:  Medha (`/v1/audio/transcriptions`) whenever its /health says `capabilities.audio`
            — checked before every recording — else whisper.cpp. If Medha fails on a clip,
            whisper takes that clip.
    Photos: Medha reads the picture and describes it in one call; tesseract only double-checks
            when the model found no text. Sketches are always described (and their labels read).
    """

    def __init__(self, cfg: dict, engine=None):
        self.cfg = cfg
        mode = (cfg.get("media_backend") or "auto").lower()
        self.enabled = bool(cfg.get("media_enabled", True)) and mode != "off"
        self.cmd = CommandBackend(cfg) if mode in ("auto", "command") else None
        self.llm = LiteRTBackend(engine) if (engine is not None and mode in ("auto", "litert")) else None
        self._last_audio_secs = 0.0

    def _pick(self, kind: str):
        order = (self.llm, self.cmd) if self.llm is not None and self.llm.can(kind) else (self.cmd, self.llm)
        for b in order:
            if b is not None and b.can(kind):
                return b
        return None

    def describe(self) -> str:
        if not self.enabled:
            return "off"
        parts = []
        for kind, label in (("audio", "voice"), ("image", "photos")):
            b = self._pick(kind)
            s = b.name if b else "unavailable"
            if b is self.llm and self.cmd is not None and self.cmd.can(kind):
                s += f" (fallback: {self.cmd.name})"
            parts.append(f"{label}: {s}")
        return ", ".join(parts)

    def run(self, kind: str, path: str, mime: str, gate=None, progress=None):
        """-> (text, backend name). kind: 'audio' | 'image' | 'sketch' | 'video'.
        `gate()` is called before each heavy step of a video (it waits while the phone is hot and returns False to
        stop); `progress(msg)` reports what the video job is doing."""
        if not self.enabled:
            raise MediaUnavailable("media processing is turned off (media_enabled)")
        if kind == "audio":
            return self._run_audio(path, mime)
        if kind == "video":
            return self._run_video(path, mime, gate, progress)
        return self._run_image(kind, path, mime)

    # ── voice ────────────────────────────────────────────────────────────────
    def _run_audio(self, path: str, mime: str):
        cmd_ok = self.cmd is not None and self.cmd.can("audio")
        if self.llm is not None and self.llm.can("audio", fresh=True):       # /health says audio, right now
            t0 = time.monotonic()
            try:
                text = self.llm.transcribe(path, mime)
                if text.strip():
                    self._last_audio_secs = time.monotonic() - t0
                    return text, self.llm.name
                log.info("    Medha returned an empty transcript" + (" — trying whisper" if cmd_ok else ""))
            except MediaUnavailable as e:
                log.info(f"    Medha can't transcribe ({e})" + (" — using whisper" if cmd_ok else ""))
            except MediaFailed as e:
                if not cmd_ok:
                    raise
                log.info(f"    Medha transcription failed ({str(e)[:120]}) — using whisper for this clip")
        if cmd_ok:
            return self.cmd.transcribe(path, mime), self.cmd.name
        raise MediaUnavailable("no speech-to-text engine configured (Medha doesn't report audio, and no whisper command is set)")

    # ── video ────────────────────────────────────────────────────────────────
    def _probe(self, ff: str, path: str):
        """-> (duration seconds or 0, has_audio) from ffmpeg's own banner (ffprobe isn't always installed)."""
        try:
            p = subprocess.run([ff, "-hide_banner", "-i", path], capture_output=True, timeout=30, stdin=subprocess.DEVNULL)
        except Exception:
            return 0.0, True
        err = _text(p.stderr)
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", err)
        dur = (int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))) if m else 0.0
        return dur, bool(re.search(r"Stream #[^\n]*Audio:", err))

    def _run_video(self, path: str, mime: str, gate=None, progress=None):
        """A video note = its sound (transcribed) + a few frames (described). Either half may be missing."""
        if not Path(path).exists():
            raise MediaFailed("the file is missing from disk")
        ff = shutil.which("ffmpeg")
        if not ff:
            raise MediaUnavailable("ffmpeg isn't installed (pkg install ffmpeg), which is needed to read videos")
        max_s = max(5, int(self.cfg.get("media_video_max_s", 300)))
        want_frames = max(0, int(self.cfg.get("media_video_frames", 4)))
        timeout = int(self.cfg.get("media_job_timeout_s", 600))
        dur, has_audio = self._probe(ff, path)
        dur = min(dur, max_s) if dur else 0.0
        audio_ok = (self.llm is not None and self.llm.can("audio")) or (self.cmd is not None and self.cmd.can("audio"))
        vision_ok = self.llm is not None and self.llm.can("image") and bool(self.cfg.get("media_describe", True)) and want_frames > 0
        if not (has_audio and audio_ok) and not vision_ok:
            raise MediaUnavailable("no engine can read this video (needs speech-to-text or a vision-capable model)")
        speech, seen, backend = "", [], ""
        def _step(msg):
            if progress:
                progress(msg)
            if gate and gate() is False:
                raise MediaFailed("stopped before the video was finished")
        with tempfile.TemporaryDirectory(prefix="qs-video-") as td:
            if has_audio and audio_ok:
                wav = os.path.join(td, "sound.wav")
                _step(f"sound ({int(dur) if dur else '?'} s)")
                try:
                    _run([ff, "-y", "-loglevel", "error", "-i", path, "-vn", "-t", str(max_s), "-ar", "16000", "-ac", "1",
                          "-c:a", "pcm_s16le", wav], timeout)
                    speech, backend = self._run_audio(wav, "audio/wav")
                except (MediaUnavailable, MediaFailed) as e:
                    log.info(f"    video sound skipped ({str(e)[:120]})")
            if vision_ok:
                n = max(1, min(want_frames, int(dur // 8) + 1)) if dur else 1
                times = [dur * (i + 0.5) / n for i in range(n)] if dur else [1.0]
                px = int(self.cfg.get("media_image_max_px", 1024))
                for i, t in enumerate(times):
                    jpg = os.path.join(td, f"f{i}.jpg")
                    _step(f"frame {i + 1} of {len(times)}")
                    try:
                        _run([ff, "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", path, "-frames:v", "1",
                              "-vf", f"scale='min({px},iw)':-2", "-q:v", "4", jpg], timeout)
                        res = self.llm.analyze(jpg)
                    except (MediaUnavailable, MediaFailed):
                        continue
                    if res and (res["description"] or res["text"]):
                        seen.append((t, res["description"], res["text"]))
                        backend = backend or self.llm.name
        parts = []
        if speech.strip():
            parts.append("Spoken: " + speech.strip())
        if seen:
            lines = []
            for t, desc, text in seen:
                line = f"- {int(t) // 60}:{int(t) % 60:02d} {desc}".rstrip()
                if text.strip():
                    line += f" Text: {' '.join(text.split())[:300]}"
                lines.append(line)
            parts.append("On screen:\n" + "\n".join(lines))
        if not parts:
            raise MediaFailed("nothing could be made out in this video")
        return "\n\n".join(parts)[:_MAX_TEXT], backend or "command"

    # ── photos and sketches ──────────────────────────────────────────────────
    def _run_image(self, kind: str, path: str, mime: str):
        llm_ok = self.llm is not None and self.llm.can("image")
        cmd_ok = self.cmd is not None and self.cmd.can("image")
        if not llm_ok and not cmd_ok:
            raise MediaUnavailable("no text-recognition engine found (install tesseract or enable the multimodal model)")
        if not Path(path).exists():
            raise MediaFailed("the file is missing from disk")
        describe = bool(self.cfg.get("media_describe", True))
        if llm_ok and describe:
            res = self.llm.analyze(path, sketch=(kind == "sketch"))
            if res:
                text, desc = res["text"], res["description"]
                if not text.strip() and kind == "image" and cmd_ok:      # the model saw no text — let OCR double-check
                    try:
                        t2 = self.cmd.read_image(path, mime)
                    except (MediaUnavailable, MediaFailed):
                        t2 = ""
                    if _plausible_text(t2):
                        text = t2
                out = (f"Description: {desc}" if desc else "") + (
                    (("\n\n" if desc else "") + f"Text: {text.strip()}") if text.strip() else "")
                if out.strip():
                    return out, self.llm.name
            log.info("    the model couldn't read this picture" + (" — trying tesseract" if cmd_ok else ""))
        elif llm_ok and not cmd_ok:
            t = self.llm.read_image(path, mime, handwriting=(kind == "sketch"))
            if t.strip():
                return t, self.llm.name
        if cmd_ok:
            text = self.cmd.read_image(path, mime, handwriting=(kind == "sketch"))
            if text.strip() or not llm_ok:
                return text, self.cmd.name
        raise MediaFailed("nothing could be read from this image")

    # ── Medha vs whisper, side by side ──────────────────────────────────────
    def wants_compare(self) -> bool:
        return bool(self.cfg.get("media_audio_compare", True)) and self.cmd is not None and self.cmd.can("audio")

    def compare_file(self) -> Path:
        f = (self.cfg.get("media_compare_file") or "").strip()
        if f:
            return Path(os.path.expanduser(f))
        return Path(os.path.expanduser(str(self.cfg.get("state_file") or "~/.cache/memos_daemon/state.json"))).parent / "media_compare.jsonl"

    def compare_audio(self, aid: str, path: str, mime: str, medha_text: str, medha_secs: float = 0.0):
        """Run whisper on a clip Medha just did and append both transcripts to a local jsonl, so the
        two can be compared on real Telugu audio. Never changes the transcript the note uses."""
        with _COMPARE_LOCK:
            t0 = time.monotonic()
            try:
                w = self.cmd.transcribe(path, mime)
            except (MediaUnavailable, MediaFailed, Exception) as e:
                log.info(f"    compare {aid}: whisper skipped ({str(e)[:100]})")
                return
            secs = time.monotonic() - t0
            a, b = " ".join(medha_text.split()).casefold(), " ".join(w.split()).casefold()
            mt, ml = _letters(medha_text); wt, wl = _letters(w)
            rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "id": aid, "lang": str(self.cfg.get("media_audio_lang") or "auto"),
                   "medha": {"text": medha_text, "secs": round(medha_secs, 1), "telugu": mt, "latin": ml},
                   "whisper": {"text": w, "secs": round(secs, 1), "telugu": wt, "latin": wl},
                   "similarity": round(difflib.SequenceMatcher(None, a, b).ratio(), 3) if a and b else None}
            try:
                f = self.compare_file(); f.parent.mkdir(parents=True, exist_ok=True)
                with open(f, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                log.info(f"    compare {aid}: medha {medha_secs:.0f}s vs whisper {secs:.0f}s · similarity "
                         f"{rec['similarity'] if rec['similarity'] is not None else 'n/a'} → {f.name}")
            except OSError as e:
                log.info(f"    compare {aid}: couldn't write the log ({e})")
