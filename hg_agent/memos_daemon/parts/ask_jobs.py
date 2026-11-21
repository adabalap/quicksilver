"""Ask-your-notes jobs: answer a pending Notes / Chat / Write / Story request."""
import time

from ..logging_setup import log
from ..ask_parse import _parse_ask_answer


class AskJobsMixin:
    """Ask-your-notes jobs: answer a pending Notes / Chat / Write / Story request."""

    def _process_ask(self, request_id: str):
        """Answer one Ask-your-notes question. The PWA already retrieved and
        ranked the note excerpts (see hg_ui's /api/ask) — this only generates
        the answer, through the same queue/worker as enrichment so the two
        never run concurrently against the one in-memory model."""
        req = self.memos.get_ask_request(request_id)
        if req is None:
            log.info(f"    (ask {request_id} gone — skipping)")
            return
        if req["status"] != "pending":
            log.info(f"    (ask {request_id} already {req['status']} — skipping)")
            return  # answered already, e.g. a race with the safety poll
        if self.cfg["thermal_enabled"]:
            if not self.thermal.wait_until_cool(lambda: self.running):
                self.enqueue(f"ask:{request_id}", "deferred (hot)", force_priority=0)
                time.sleep(self.cfg["thermal_poll_seconds"])
                return
        mode = req.get("mode") or "notes"
        log.info(f"  ▸ answering ask:{request_id} [{mode}] — {req['question'][:60]!r}")
        excerpts = req["context"]
        if mode == "chat":
            try:
                text = (self.gemma.chat(req["question"], history=req.get("history")) or "").strip()
            except Exception as e:
                log.error(f"chat inference failed for {request_id}: {e}")
                text = ""
            if text:
                self.memos.write_ask_answer(request_id, text, [], status="done")
            else:
                self.memos.write_ask_answer(request_id, None, [], status="error",
                                            error="The local model didn't return an answer.")
            return
        if mode == "story":
            opts = req.get("history") if isinstance(req.get("history"), dict) else {}
            desc = "\n\n".join((c.get("text") or "") for c in excerpts).strip()
            if not desc and not opts.get("revise") and not (req["question"] or "").strip() \
                    and len((opts.get("note_text") or "").split()) < 3:
                self.memos.write_ask_answer(request_id, None, [], status="error",
                                            error="There's no description of the photo to write from.")
                return
            try:
                _rev = str(opts.get("revise") or "")
                text = self.gemma.story(desc, outline=(opts.get("outline", "") if _rev else req["question"]),
                                        genre=opts.get("genre", ""),
                                        length=opts.get("length", "short"), lang=opts.get("lang", "en"),
                                        note_text=opts.get("note_text", ""),
                                        revise=_rev, instruction=(req["question"] if _rev else ""))
            except Exception as e:
                log.error(f"story inference failed for {request_id}: {e}")
                text = ""
            if text:
                self.memos.write_ask_answer(request_id, text, [], status="done")
            else:
                self.memos.write_ask_answer(request_id, None, [], status="error",
                                            error="The local model didn't write a story. Try again.")
            return
        if not excerpts:
            # Shouldn't happen — /api/ask answers a no-match question itself,
            # without ever writing a 'pending' row — but a stale/malformed row
            # is possible, so this doesn't get stuck at "thinking…" forever.
            self.memos.write_ask_answer(request_id, None, [], status="error",
                                        error="No notes were retrieved for this question.")
            return
        try:
            raw = (self.gemma.write(req["question"], excerpts) if mode == "write"
                   else self.gemma.ask(req["question"], excerpts, history=req.get("history")))
        except Exception as e:
            log.error(f"ask inference failed for {request_id}: {e}")
            self.memos.write_ask_answer(request_id, None, [], status="error",
                                        error="The local model couldn't answer that.")
            return
        answer, cited_ids = _parse_ask_answer(
            raw, excerpts, limit=getattr(self.gemma, "_ask_kept", None))
        if not answer:
            self.memos.write_ask_answer(request_id, None, [], status="error",
                                        error="The local model didn't return an answer.")
            return
        self.memos.write_ask_answer(request_id, answer, cited_ids, status="done")
        log.info(f"  ▸ done ask:{request_id} ({len(cited_ids)} cited)")
