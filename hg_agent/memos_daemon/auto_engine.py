"""auto_engine.py — Medha first, the in-process model as the fallback, never both.

  • Medha healthy            → every call goes to Medha; the agent holds no model.
  • Medha down / unreachable → the in-process model is loaded (lazily, on the first
                               call that needs it) and used.
  • Medha comes back         → the next call goes to Medha and the in-process model
                               is closed, freeing its memory.

Only one of them is ever resident. Everything above generation (prompts, Ask, Chat,
Write, enrichment, parsing) is inherited from GemmaEngine unchanged.
"""
from __future__ import annotations
import gc, threading, time
from .engine import GemmaEngine
from .logging_setup import log
from .medha_engine import MedhaEngine, MedhaDown, InteractiveMixin


class AutoEngine(InteractiveMixin, GemmaEngine):
    def __init__(self, cfg: dict):
        # Not calling GemmaEngine.__init__ (it would load the model right now).
        self._cfg = cfg
        self._medha = MedhaEngine(cfg)
        self._local = None
        self._lock = threading.RLock()
        self._recheck = float(cfg.get("engine_recheck_s", 30))
        self._backoff = float(cfg.get("medha_error_backoff_s", 600))
        self._bad_until = 0.0      # Medha answered with a server error — leave it alone until then
        self._ok = None            # last Medha health verdict
        self._checked = 0.0
        self._local_used = 0.0     # last time the built-in model was handed work
        self._max_tokens = self._medha._max_tokens
        self._max_context = self._medha._max_context
        self._gen_mechanism = None
        self._engine = None
        if not cfg.get("model_path"):
            log.warning("engine=auto but model_path is not set — there is no fallback if Medha is down")

    # ── which backend is live ────────────────────────────────────────────────
    def _medha_usable(self, force: bool = False) -> bool:
        now = time.monotonic()
        if now < self._bad_until:
            return False
        # Healthy: trust it between calls (a failed call re-checks). Down: look again every N s.
        if not force and self._ok is not None and (now - self._checked) < (300 if self._ok else self._recheck):
            return self._ok
        h = self._medha.health()
        ok = h is not None and h.get("modelLoaded", h.get("model_loaded", True)) is not False
        if ok != self._ok:
            log.info("engine: Medha is " + ("up — using it" if ok else "not available — using the built-in model"))
        self._ok, self._checked = ok, now
        return ok

    def _release_local(self):
        if self._local is not None:
            log.info("engine: Medha is back — unloading the built-in model to free memory")
            try:
                self._local.close()
            except Exception:
                pass
            self._local = None
            gc.collect()

    def release_if_idle(self) -> bool:
        """Free the built-in model's memory (about 3 GB) once it has sat unused for engine_idle_unload_s.
        It loads again on the next job. Never blocks: a running generation holds the lock."""
        idle = float(self._cfg.get("engine_idle_unload_s", 300) or 0)
        if idle <= 0 or self._local is None:
            return False
        if not self._lock.acquire(blocking=False):
            return False
        try:
            if self._local is not None and time.monotonic() - self._local_used > idle:
                log.info(f"engine: built-in model idle for {int(idle)}s — unloading it to free memory")
                try:
                    self._local.close()
                except Exception:
                    pass
                self._local = None
                gc.collect()
                return True
        finally:
            self._lock.release()
        return False

    def _backend(self):
        with self._lock:
            if self._medha_usable():
                self._release_local()
                return self._medha
            if self._local is None:
                log.info("engine: loading the built-in model (fallback)…")
                self._local = GemmaEngine(self._cfg["model_path"], cfg=self._cfg)
            self._local_used = time.monotonic()
            return self._local

    def refresh_health(self, max_age: float = 60.0) -> bool:
        """Re-probe Medha when the last verdict is older than max_age (used by telemetry)."""
        if self._local is None and (self._ok is None or (time.monotonic() - self._checked) > max_age):
            try:
                return self._medha_usable(force=True)
            except Exception:
                self._ok, self._checked = False, time.monotonic()
        return bool(self._ok)

    @property
    def active(self) -> str:
        return "medha" if self._local is None and self._ok else ("local" if self._local is not None else "idle")

    # ── generation ───────────────────────────────────────────────────────────
    def _generate(self, messages, prompt, cap=None):
        with self._lock:
            b = self._backend()
            if b is self._medha:
                try:
                    return b._generate(messages, prompt, cap=cap)
                except MedhaDown as e:
                    log.warning(f"engine: Medha dropped mid-call ({e}) — switching to the built-in model")
                    self._ok, self._checked = False, time.monotonic()
                    b = self._backend()
                except RuntimeError as e:
                    if not self._is_server_error(e):
                        raise
                    b = self._medha_failed(e)
            try:
                return b._generate(messages, prompt, cap=cap)
            finally:
                if b is self._local:
                    self._local_used = time.monotonic()

    @staticmethod
    def _is_server_error(e: Exception) -> bool:
        return str(e).startswith("Medha returned HTTP 5")

    def _medha_failed(self, e: Exception):
        """Medha is up but erroring (e.g. a model it can't run). Use the built-in model for a
        while rather than failing every note; try Medha again after the back-off."""
        log.warning(f"engine: Medha returned an error ({str(e)[:140]}) — using the built-in model for "
                    f"{int(self._backoff // 60)} min")
        self._bad_until = time.monotonic() + self._backoff
        self._ok, self._checked = False, time.monotonic()
        return self._backend()

    # ── images / audio ───────────────────────────────────────────────────────
    @property
    def _mm_enabled(self) -> bool:
        # Answered without loading anything: Medha's flag while it's up, else the local setting.
        if self._local is not None:
            return bool(getattr(self._local, "_mm_enabled", False))
        if self._ok:
            return bool(self._medha._mm_enabled)
        return bool(self._cfg.get("media_litert_multimodal", False)) and bool(self._cfg.get("model_path"))

    def can_multimodal(self, kind: str, fresh: bool = False) -> bool:
        """Per kind ('audio' | 'image'): Medha's own report while it's up, else the local setting."""
        if self._local is not None:
            return bool(getattr(self._local, "_mm_enabled", False))
        if self._ok is None:
            self._medha_usable()
        if self._ok:
            return self._medha.can_multimodal(kind, fresh=fresh)
        return bool(self._cfg.get("media_litert_multimodal", False)) and bool(self._cfg.get("model_path"))

    def transcribe_audio(self, path: str, language: str = "auto", prompt: str = "") -> str:
        with self._lock:
            b = self._backend()
            if b is self._medha:
                try:
                    return b.transcribe_audio(path, language=language, prompt=prompt)
                except MedhaDown:
                    self._ok, self._checked = False, time.monotonic()
                    b = self._backend()
                except RuntimeError as e:
                    if not self._is_server_error(e):
                        raise
                    b = self._medha_failed(e)
            return b.multimodal([{"type": "audio", "path": path}],
                                "Transcribe this recording exactly as spoken, in the language spoken. "
                                "Output only the transcript, no commentary.")

    def multimodal(self, parts: list, prompt: str, max_tokens=None) -> str:
        with self._lock:
            b = self._backend()
            if b is self._medha:
                try:
                    return b.multimodal(parts, prompt, max_tokens=max_tokens)
                except MedhaDown:
                    self._ok, self._checked = False, time.monotonic()
                    b = self._backend()
                except RuntimeError as e:
                    if not self._is_server_error(e):
                        raise
                    b = self._medha_failed(e)
            return b.multimodal(parts, prompt)

    def health(self):
        return self._medha.health()

    def close(self):
        with self._lock:
            self._medha.close()
            if self._local is not None:
                try:
                    self._local.close()
                except Exception:
                    pass
                self._local = None
