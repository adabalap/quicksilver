"""medha_engine.py — use Medha (the on-device NPU/GPU inference service) as the
text engine instead of loading the model inside the agent.

Everything above generation (the enrichment prompt, parsing, token fitting, Ask,
daily brief) is inherited unchanged from GemmaEngine; only the call that turns a
prompt into text is replaced by `POST <medha_url>/generate`.

Text uses `POST /generate` (with a real `max_tokens` cap). Photos and voice use
OpenAI-style multimodal content on `/v1/chat/completions` (`image_url` data-URL parts
and `input_audio` parts). `"medha_multimodal"` is "auto" by default: Medha's `/health`
reports `capabilities.vision` / `.audio` from a runtime probe, and the agent follows it —
so it stays on tesseract / whisper.cpp until Medha genuinely supports the input, then
switches by itself. `true` forces it on, `false` forces it off.
Every call carries `X-Medha-Priority`: `interactive` for Ask / Chat / Write / Story (a
person is waiting), `batch` for enrichment, media and digests. A 429 is honoured with its
`Retry-After` (Medha is busy or thermally gated — not broken, so no fallback).
"""
from __future__ import annotations
import base64, contextlib, json, os, shutil, subprocess, tempfile, threading, time, wave
import requests
from .engine import GemmaEngine
from .logging_setup import log


# ── request priority (thread-local, set by the interactive entry points) ─────
_prio = threading.local()

@contextlib.contextmanager
def priority(level: str):
    old = getattr(_prio, "v", None)
    _prio.v = level
    try:
        yield
    finally:
        _prio.v = old


class InteractiveMixin:
    """Ask / Chat / Write / Story are things a person is waiting on."""
    def ask(self, *a, **k):
        with priority("interactive"):
            return super().ask(*a, **k)
    def chat(self, *a, **k):
        with priority("interactive"):
            return super().chat(*a, **k)
    def write(self, *a, **k):
        with priority("interactive"):
            return super().write(*a, **k)
    def story(self, *a, **k):
        with priority("interactive"):
            return super().story(*a, **k)


def _mm_mode(v) -> str:
    """medha_multimodal: True/'true'/'on' → 'on', False/'false'/'off' → 'off', else 'auto'."""
    if isinstance(v, bool):
        return "on" if v else "off"
    t = str(v if v is not None else "auto").strip().lower()
    return "on" if t in ("1", "true", "on", "yes") else "off" if t in ("0", "false", "off", "no") else "auto"


def _err_code(r) -> str:
    """Medha's machine-readable error code, wherever it sits in the JSON ('' if none)."""
    try:
        j = r.json()
    except Exception:
        return ""
    if isinstance(j, dict):
        e = j.get("error")
        for d in (j, e if isinstance(e, dict) else {}):
            c = d.get("code")
            if isinstance(c, str):
                return c
    return ""


class MedhaDown(RuntimeError):
    """Medha couldn't be reached at all (not running / timed out) — as opposed to
    answering with an error. The auto engine falls back to the in-process model on this."""


class MedhaEngine(InteractiveMixin, GemmaEngine):
    def __init__(self, cfg: dict | None = None):
        # Deliberately NOT calling GemmaEngine.__init__ (it loads a model file).
        self._cfg = cfg or {}
        self._url = (self._cfg.get("medha_url") or "http://127.0.0.1:8080").rstrip("/")
        self._token = (self._cfg.get("medha_token") or "").strip()
        self._timeout = float(self._cfg.get("medha_timeout_s", 300))
        self._max_tokens = int(self._cfg.get("enrich_max_tokens", 2048))
        self._max_context = int(self._cfg.get("model_max_num_tokens", 4096))
        self._mm_cfg = _mm_mode(self._cfg.get("medha_multimodal", "auto"))
        self._busy_wait = float(self._cfg.get("medha_busy_wait_s", 120))
        self._caps_cache = (0.0, {})
        self._img_px = int(self._cfg.get("media_image_max_px", 1024))
        self._aud_chunk_s = int(self._cfg.get("media_audio_chunk_s", 28))
        self._gen_mechanism = None
        self._engine = None
        self._http = requests.Session()
        info = self.health()
        if info is None:
            log.warning(f"Medha not reachable at {self._url} yet — jobs will retry until it is up")
        else:
            log.info(f"Medha engine at {self._url}: {info.get('status', '?')} · model loaded="
                     f"{info.get('modelLoaded', info.get('model_loaded', '?'))} · backend="
                     f"{info.get('backend', '?')}")

    def _headers(self):
        h = {"Content-Type": "application/json",
             "X-Medha-Priority": getattr(_prio, "v", None) or "batch"}
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    # ── what this Medha can do (from its own runtime probe) ─────────────────
    def capabilities(self, fresh: bool = False) -> dict:
        t, c = self._caps_cache
        now = time.monotonic()
        if not fresh and c and (now - t) < 60:
            return c
        h = self.health()
        caps = {}
        if isinstance(h, dict) and isinstance(h.get("capabilities"), dict):
            caps = {k: bool(v) for k, v in h["capabilities"].items()}
            if caps != c:
                log.info("Medha capabilities: " + ", ".join(f"{k}={'yes' if v else 'no'}" for k, v in caps.items()))
        self._caps_cache = (now if h is not None else now - 45, caps)   # a failed look is retried in ~15 s
        return caps

    def can_multimodal(self, kind: str, fresh: bool = False) -> bool:
        """kind: 'audio' or 'image'/'vision'. fresh=True asks /health again instead of using the 60 s cache."""
        if self._mm_cfg == "off":
            return False
        if self._mm_cfg == "on":
            return True
        return bool(self.capabilities(fresh=fresh).get("audio" if kind == "audio" else "vision"))

    @property
    def _mm_enabled(self) -> bool:
        return self.can_multimodal("image") or self.can_multimodal("audio")

    def _wait_busy(self, r, waited: float) -> float:
        """429/503: honour Retry-After (capped). Returns the new total waited, or -1 to give up."""
        try:
            ra = float(r.headers.get("Retry-After", "") or 5)
        except ValueError:
            ra = 5.0
        ra = max(1.0, min(ra, 30.0))
        if waited + ra > self._busy_wait:
            return -1.0
        log.info(f"    Medha is busy ({r.status_code}) — waiting {ra:.0f}s")
        time.sleep(ra)
        return waited + ra

    def refresh_health(self, max_age: float = 60.0) -> bool:
        """Probe /health (cached max_age seconds) so telemetry reports whether Medha is really up."""
        now = time.monotonic()
        if now - getattr(self, "_hc_at", -1e9) > max_age:
            self._hc_ok = self.health() is not None
            self._hc_at = now
        return bool(getattr(self, "_hc_ok", False))

    def health(self):
        try:
            r = self._http.get(self._url + "/health", timeout=5)
            return r.json() if r.ok else None
        except Exception:
            return None

    def _generate(self, messages, prompt, cap=None):
        sys_text = ""
        for m in (messages or []):
            if m.get("role") == "system":
                c = m.get("content")
                if isinstance(c, list) and c and isinstance(c[0], dict):
                    sys_text = c[0].get("text", "") or ""
                elif isinstance(c, str):
                    sys_text = c
                break
        body = {"prompt": prompt}
        if sys_text:
            body["system"] = sys_text
        if cap:
            body["max_tokens"] = int(cap)            # a real cap now: Medha stops generating at it
        waited = 0.0
        while True:
            try:
                r = self._http.post(self._url + "/generate", data=json.dumps(body),
                                    headers=self._headers(), timeout=self._timeout)
            except (requests.ConnectionError, requests.Timeout) as e:
                raise MedhaDown(f"Medha request failed: {e}")
            except requests.RequestException as e:
                raise RuntimeError(f"Medha request failed: {e}")
            if r.status_code in (429, 503):
                waited = self._wait_busy(r, waited)
                if waited < 0:
                    raise RuntimeError(f"Medha is busy (HTTP {r.status_code}) — try again later")
                continue
            break
        if r.status_code in (401, 403):
            raise RuntimeError("Medha rejected the token (set medha_token)")
        if r.status_code == 413 or _err_code(r) == "context_overflow":
            raise RuntimeError("Medha returned HTTP 413: the prompt is too long — token limit exceeded (context_overflow)")
        if not r.ok:
            raise RuntimeError(f"Medha returned HTTP {r.status_code}: {r.text[:160]}")
        try:
            return (r.json().get("text") or "")
        except ValueError:
            raise RuntimeError("Medha sent a reply that isn't JSON")

    # ── images / audio (needs a multimodal Medha) ───────────────────────────
    def _ffmpeg(self):
        ff = shutil.which("ffmpeg")
        if not ff:
            raise RuntimeError("ffmpeg isn't installed (needed to prepare photos/audio for Medha)")
        return ff

    def _prep_image(self, path: str) -> str:
        """Shrink to ≤ media_image_max_px (fewer vision tokens = faster + cooler) → data URL."""
        raw = None
        ff = shutil.which("ffmpeg")
        if ff:
            with tempfile.TemporaryDirectory(prefix="qs-img-") as td:
                out = os.path.join(td, "i.jpg"); n = self._img_px
                r = subprocess.run([ff, "-y", "-loglevel", "error", "-i", path, "-frames:v", "1", "-vf",
                                    f"scale='if(gt(iw,ih),min({n},iw),-2)':'if(gt(iw,ih),-2,min({n},ih))'",
                                    "-q:v", "3", out], capture_output=True, timeout=60, stdin=subprocess.DEVNULL)
                if r.returncode == 0 and os.path.exists(out):
                    raw = open(out, "rb").read()
        mime = "image/jpeg"
        if raw is None:
            raw = open(path, "rb").read()
            if raw[:8] == b"\x89PNG\r\n\x1a\n":
                mime = "image/png"
            elif raw[:3] != b"\xff\xd8\xff":
                raise RuntimeError("Medha takes JPEG or PNG and ffmpeg is unavailable to convert this image")
            if len(raw) > 4 * 1024 * 1024:
                raise RuntimeError("image is over Medha's 4 MB limit and ffmpeg is unavailable to shrink it")
        return f"data:{mime};base64," + base64.b64encode(raw).decode()

    def _audio_chunks(self, path: str, td: str):
        """16 kHz mono WAV pieces of ≤ media_audio_chunk_s seconds (the model's audio window)."""
        out = os.path.join(td, "a%04d.wav")
        r = subprocess.run([self._ffmpeg(), "-y", "-loglevel", "error", "-i", path, "-ar", "16000", "-ac", "1",
                            "-c:a", "pcm_s16le", "-f", "segment", "-segment_time", str(self._aud_chunk_s), out],
                           capture_output=True, timeout=600, stdin=subprocess.DEVNULL)
        if r.returncode != 0:
            raise RuntimeError("ffmpeg couldn't read this audio")
        return sorted(os.path.join(td, f) for f in os.listdir(td) if f.startswith("a") and f.endswith(".wav"))

    def _chat(self, content: list, max_tokens: int = 400, language: str = "") -> str:
        body = {"messages": [{"role": "user", "content": content}], "stream": False, "max_tokens": max_tokens}
        if language and language != "auto":
            body["language"] = language               # carried for the audio path; Medha ignores it until it has one
        waited = 0.0
        while True:
            try:
                r = self._http.post(self._url + "/v1/chat/completions", data=json.dumps(body),
                                    headers=self._headers(), timeout=self._timeout)
            except (requests.ConnectionError, requests.Timeout) as e:
                raise MedhaDown(f"Medha request failed: {e}")
            except requests.RequestException as e:
                raise RuntimeError(f"Medha request failed: {e}")
            if r.status_code in (429, 503):
                waited = self._wait_busy(r, waited)
                if waited < 0:
                    raise RuntimeError(f"Medha is busy (HTTP {r.status_code}) — try again later")
                continue
            break
        code = _err_code(r)
        if r.status_code == 501 or code == "multimodal_unsupported":
            raise RuntimeError("this Medha build doesn't accept images/audio yet")
        if r.status_code in (400, 413, 415, 422):
            raise RuntimeError(f"Medha refused this input ({code or r.status_code}): {r.text[:140]}")
        if not r.ok:
            raise RuntimeError(f"Medha returned HTTP {r.status_code}: {r.text[:160]}")
        try:
            return (r.json()["choices"][0]["message"]["content"] or "").strip()
        except (ValueError, KeyError, IndexError, TypeError):
            raise RuntimeError("Medha sent a reply in an unexpected shape")

    def multimodal(self, parts, prompt, max_tokens=None):
        audio = [p for p in parts if p.get("type") == "audio"]
        if not self.can_multimodal("audio" if audio else "image"):
            raise RuntimeError("Medha doesn't report " + ("audio" if audio else "vision")
                               + " support (medha_multimodal=auto follows its /health; true forces it on)")
        lang = str(self._cfg.get("media_audio_lang") or "auto").strip().lower()
        content = []
        for p in [q for q in parts if q.get("type") == "image"][:4]:         # Medha takes at most 4 per request
            content.append({"type": "image_url", "image_url": {"url": self._prep_image(p["path"])}})
        if not audio:
            return self._chat(content + [{"type": "text", "text": prompt}], max_tokens=int(max_tokens or 400))
        outs = []
        with tempfile.TemporaryDirectory(prefix="qs-aud-") as td:
            for wav in self._audio_chunks(audio[0]["path"], td):
                data = base64.b64encode(open(wav, "rb").read()).decode()
                outs.append(self._chat(content + [{"type": "input_audio", "input_audio": {"data": data, "format": "wav"}},
                                                  {"type": "text", "text": prompt}], max_tokens=300, language=lang))
        return " ".join(o for o in outs if o).strip()

    # ── speech-to-text: POST /v1/audio/transcriptions (OpenAI-compatible multipart) ──
    def _wav_chunks(self, path: str, td: str):
        """≤ media_audio_chunk_s pieces of 16 kHz mono 16-bit WAV. A WAV already in that format is cut
        in pure Python; anything else (m4a, webm, …) goes through ffmpeg — Medha refuses compressed audio."""
        try:
            with wave.open(path, "rb") as w:
                if w.getsampwidth() == 2 and w.getnchannels() == 1 and w.getframerate() == 16000:
                    per = 16000 * self._aud_chunk_s
                    if w.getnframes() <= per:
                        return [path]
                    out, i = [], 0
                    while True:
                        frames = w.readframes(per)
                        if not frames:
                            break
                        q = os.path.join(td, f"a{i:04d}.wav"); i += 1
                        with wave.open(q, "wb") as o:
                            o.setnchannels(1); o.setsampwidth(2); o.setframerate(16000); o.writeframes(frames)
                        out.append(q)
                    return out
        except (wave.Error, EOFError, OSError):
            pass
        return self._audio_chunks(path, td)

    def _transcribe_chunk(self, wav: str, language: str, prompt: str) -> str:
        blob = open(wav, "rb").read()
        data = {"model": "medha", "response_format": "json"}
        if language and language != "auto":
            data["language"] = language                  # always pass te for Telugu — autodetect can answer in Latin script
        if prompt:
            data["prompt"] = prompt[:400]
        headers = self._headers(); headers.pop("Content-Type", None)     # requests sets the multipart boundary
        waited = 0.0
        while True:
            try:
                r = self._http.post(self._url + "/v1/audio/transcriptions", data=data, headers=headers,
                                    files={"file": ("audio.wav", blob, "audio/wav")}, timeout=self._timeout)
            except (requests.ConnectionError, requests.Timeout) as e:
                raise MedhaDown(f"Medha request failed: {e}")
            except requests.RequestException as e:
                raise RuntimeError(f"Medha request failed: {e}")
            if r.status_code in (429, 503):
                waited = self._wait_busy(r, waited)
                if waited < 0:
                    raise RuntimeError(f"Medha is busy (HTTP {r.status_code}) — try again later")
                continue
            break
        if r.status_code in (401, 403):
            raise RuntimeError("Medha rejected the token (set medha_token)")
        code = _err_code(r)
        if r.status_code in (404, 501) or code == "multimodal_unsupported":
            raise RuntimeError("this Medha build doesn't accept audio yet")
        if not r.ok:
            raise RuntimeError(f"Medha refused this audio ({code or r.status_code}): {r.text[:140]}")
        try:
            return (r.json().get("text") or "").strip()
        except ValueError:
            return (r.text or "").strip()               # response_format=text

    def transcribe_audio(self, path: str, language: str = "auto", prompt: str = "") -> str:
        language = (language or "auto").strip().lower()
        outs = []
        with tempfile.TemporaryDirectory(prefix="qs-aud-") as td:
            for wav in self._wav_chunks(path, td):
                outs.append(self._transcribe_chunk(wav, language, prompt))
        return " ".join(o for o in outs if o).strip()

    def close(self):
        try:
            self._http.close()
        except Exception:
            pass
