"""Photo / voice / sketch jobs: notify and process transcription and description."""
import threading, time

from ..logging_setup import log
from ..media import MediaUnavailable, MediaFailed


class MediaJobsMixin:
    """Photo / voice / sketch jobs: notify and process transcription and description."""

    def _notify_media(self, aid: str):
        """Tell open clients (quietly) that an attachment's text changed."""
        try:
            att = self.memos.media_get(aid)
            if att:
                self.memos._notify_webhook(att["note_id"], {}, "media", event="attachment_ready")
        except Exception as e:
            log.debug(f"media notify skipped: {e}")

    def _compare_audio_bg(self, aid, path, mime, text, secs):
        """Medha did this clip; run whisper on it too (after the note already has Medha's text) and log both."""
        try:
            if self.cfg.get("thermal_enabled") and not self.thermal.wait_until_cool(lambda: self.running):
                return
            self.media.compare_audio(aid, path, mime, text, secs)
        except Exception as e:
            log.debug(f"compare skipped for {aid}: {e}")

    def _process_media(self, aid: str):
        """Transcribe one recording / read one picture, then let the note's
        enrichment, search index and the user's screen catch up."""
        att = self.memos.media_get(aid)
        if not att or att["transcript_status"] not in ("pending", "running"):
            return                                   # deleted, or already settled
        self.memos.media_mark(aid, "running")
        path = str(self.memos.media_dir() / att["filename"])
        # One inference at a time, and not on a hot phone.
        if self.cfg.get("thermal_enabled") and not self.thermal.wait_until_cool(lambda: self.running):
            self.memos.media_mark(aid, "pending")
            self.enqueue("media:" + aid, "deferred (hot)", force_priority=1, respect_backoff=True)
            time.sleep(self.cfg.get("thermal_poll_seconds", 10))
            return
        log.info(f"    {'🎬' if att['kind'] == 'video' else '🎙'} {att['kind']} {aid}: reading…")
        t0 = time.time()
        try:
            gate = prog = None
            if att["kind"] == "video":
                def gate():
                    if not self.cfg.get("thermal_enabled"):
                        return True
                    return self.thermal.wait_until_cool(lambda: self.running)
                def prog(msg):
                    log.info(f"      video {aid}: {msg}…")
            text, backend = self.media.run(att["kind"], path, att["mime"], gate=gate, progress=prog)
        except MediaUnavailable as e:
            log.info(f"    (media {aid} skipped — {e})")
            self.memos.media_mark(aid, "skipped", error=str(e)[:300])
            self._notify_media(aid)
            return
        except MediaFailed as e:
            self.memos.media_mark(aid, "pending", error=str(e)[:300])
            raise                                   # worker retries with backoff, then dead-letters
        self.memos.media_mark(aid, "done", text=text, error=None, model=backend)
        log.info(f"    ✓ {att['kind']} {aid}: {len(text)} chars via {backend} in {time.time()-t0:.1f}s")
        nid = att["note_id"]
        if att["kind"] == "audio" and backend == "medha" and text.strip() and self.media.wants_compare():
            threading.Thread(target=self._compare_audio_bg, daemon=True,
                             args=(aid, path, att["mime"], text, getattr(self.media, "_last_audio_secs", 0.0))).start()
        try:
            row = self.memos._db().execute("SELECT body FROM notes WHERE id=?", (nid,)).fetchone()
            # A voice note with nothing else written IS its transcript: put the
            # words in the note, where they can be polished like any dictation.
            # Never touch a body the user has typed.
            if att["kind"] == "audio" and text and row is not None and not (row["body"] or "").strip():
                self.memos.update_content(nid, text)
        except Exception as e:
            log.debug(f"voice→body skipped for {nid}: {e}")
        self._index_quietly(nid)
        self._notify_media(aid)
        self.enqueue(nid, "media text ready", hint_text=text[:200])
