"""Durable job queue: enqueue, dispatch, lease outcomes, dead-letter, media scan."""
import threading

from ..logging_setup import log


class JobQueueMixin:
    """Durable job queue: enqueue, dispatch, lease outcomes, dead-letter, media scan."""

    def _sig(self, *_):
        log.info("Shutdown signal — stopping after current item…")
        self.running = False
        # Unblock the worker (highest priority sentinel) and stop the server.
        self._q.put((-1, -1, None))
        if self._httpd is not None:
            threading.Thread(target=self._httpd.shutdown, daemon=True).start()

    _URGENT_KW = ("urgent", "asap", "today", "tonight", "tomorrow", "deadline",
                  "due", "important", "now", "immediately", "call", "pay",
                  "submit", "reminder", "!!")

    @staticmethod
    def _kind_of(name: str) -> str:
        """Job kind from its key: 'ask:<id>', 'media:<attachment id>', else a note id."""
        return "ask" if name.startswith("ask:") else ("media" if name.startswith("media:") else "note")

    def enqueue(self, name: str, reason: str = "", hint_text: str = "",
                force_priority: int | None = None, respect_backoff: bool = False):
        """Add a note name / ask job to the work queue, deduplicating. A cheap
        keyword pre-pass assigns priority so likely-urgent notes are processed
        first, unless force_priority is given (Ask jobs: the user is watching a
        "thinking…" state right now, not waiting on background enrichment).

        The job is persisted first (agent_jobs), then handed to the in-memory
        dispatcher. A job still inside its retry backoff is NOT dispatched here —
        `_jobs_tick` does that when it falls due — unless this is a fresh user
        action (the default), which cancels the backoff. Sweepers pass
        respect_backoff=True."""
        if not name:
            return
        # priority: lower number = higher priority. 0 urgent, 1 normal.
        if force_priority is not None:
            prio = force_priority
        else:
            prio = 1
            if self.cfg.get("priority_queue_enabled", True) and hint_text:
                low = hint_text.lower()
                if any(k in low for k in self._URGENT_KW):
                    prio = 0
        jobs = getattr(self, "jobs", None)
        if jobs is not None:
            kind = self._kind_of(name)
            try:
                r = jobs.enqueue(kind, name, prio,
                                 max_attempts=(2 if kind == "ask" else 3 if kind == "media"
                                               else self.FORCE_MAX_ATTEMPTS),
                                 immediate=not respect_backoff)
                if not r["due"]:
                    return                       # backing off; _jobs_tick dispatches it
            except Exception as e:                # persistence must never block work
                log.debug(f"job persist failed for {name}: {e}")
        self._dispatch(name, prio, reason)

    def _dispatch(self, name: str, prio: int, reason: str = ""):
        """Hand a job to the in-memory worker queue (deduplicated)."""
        with self._qlock:
            if name in self._queued:
                return
            self._queued.add(name)
            self._seq += 1
            seq = self._seq
        self._q.put((prio, seq, name))
        if reason:
            log.info(f"  ⟶ queued {name} ({reason}{' · priority' if prio == 0 else ''})")

    # ── durable-queue plumbing ───────────────────────────────────────────────
    def _jobs_boot(self):
        """Startup recovery: every job the previous process left 'running' died
        with it — put them back, then load everything that is due."""
        if getattr(self, "jobs", None) is None:
            return
        try:
            n = self.jobs.recover(everything=True)
            if n:
                log.info(f"  ♻ recovered {n} interrupted job(s) from the last run")
            c = self.jobs.counts()
            if c["queued"] or c["dead"]:
                log.info(f"  📋 job queue: {c['queued']} waiting, {c['dead']} failed")
            self._jobs_tick(recover=False)
        except Exception as e:
            log.warning(f"job recovery skipped: {e}")

    def _jobs_tick(self, recover: bool = True):
        """Dispatch jobs whose backoff has elapsed (and any the app re-queued
        via Retry); return jobs whose lease expired to the queue."""
        jobs = getattr(self, "jobs", None)
        if jobs is None:
            return
        try:
            if recover:
                jobs.recover(everything=False, skip_keys={self._inflight} if self._inflight else ())
            for j in jobs.due():
                self._dispatch(j["key"], j["priority"], f"job #{j['id']} due"
                               + (f" · retry {j['attempts']}" if j["attempts"] else ""))
        except Exception as e:
            log.debug(f"jobs tick skipped: {e}")
        self._media_scan()

    def _media_scan(self):
        """New photos / recordings land as 'pending' rows (the app can't call the
        agent directly); turn each into a durable job. Cheap: one indexed query."""
        if self._backend != "quicksilver" or not getattr(self, "media", None) or not self.media.enabled:
            return
        try:
            for a in self.memos.media_pending(int(self.cfg.get("media_scan_batch", 20))):
                self.enqueue("media:" + a["id"], f"new {a['kind']}", force_priority=(2 if a["kind"] == "video" else 1),
                             respect_backoff=True)
        except Exception as e:
            log.debug(f"media scan skipped: {e}")

    def _job_outcome(self, kind: str, name: str):
        """Did the run that just finished actually accomplish the job?
        Returns ("done"|"requeued"|"retry", reason)."""
        with self._qlock:
            if name in self._queued:
                return "requeued", ""      # deferred (hot phone) / fresh edit
        try:
            if kind == "media":
                att = self.memos.media_get(name[len("media:"):])
                if att and att["transcript_status"] in ("pending", "running"):
                    return "retry", "no result was produced"
                return "done", ""
            if kind == "ask":
                req = self.memos.get_ask_request(name[len("ask:"):])
                if req and req["status"] == "pending":
                    return "retry", "no answer was produced"
                return "done", ""
            if self._backend == "quicksilver" and any(
                    n == name for n, _ in self.memos.list_force(0)):
                return "retry", "enrichment produced no result"
        except Exception as e:
            log.debug(f"job outcome check failed for {name}: {e}")
        return "done", ""

    def _job_finish(self, job, kind: str, name: str, outcome: str, err: str = ""):
        jobs = getattr(self, "jobs", None)
        if jobs is None or not job:
            return
        try:
            if outcome in ("done", "requeued"):
                jobs.complete(job["id"])      # 'requeued' has its own fresh queued row
                return
            state = jobs.fail(job["id"], err)
            if state == "dead":
                self._job_dead(kind, name, err)
            else:
                log.info(f"  ↻ {name}: attempt {job['attempts']}/{job['max_attempts']} "
                         f"failed ({err}) — retrying after backoff")
        except Exception as e:
            log.debug(f"job finish failed for {name}: {e}")

    def _job_dead(self, kind: str, name: str, err: str):
        """Retry budget spent: stop the spinner, say why, keep the job visible
        (dead-letter) so the user can retry it from the app."""
        log.warning(f"  ✗ {name}: gave up — {err}")
        try:
            if kind == "media":
                self.memos.media_mark(name[len("media:"):], "failed", error=(err or "gave up")[:300])
                self._notify_media(name[len("media:"):])
            elif kind == "ask":
                self.memos.write_ask_answer(
                    name[len("ask:"):], None, [], status="error",
                    error="The on-device model couldn't answer that, even after retrying.")
            else:
                rest = self.memos.release_force(name)
                try: self.memos.record_error(name, f"gave up after retries: {err}")
                except Exception: pass
                log.info(f"    ↩ {name} released → {rest or 'raw'}")
        except Exception as e:
            log.debug(f"dead-job cleanup failed for {name}: {e}")

    def _enqueue_stale_ask_requests(self):
        """Safety-poll backstop for Ask (see _run_qs_events): normally the SSE
        'ask_requested' event enqueues a job the instant hg_ui writes it, so
        this only fires anything if that push was missed (daemon briefly
        disconnected, etc). min_age_seconds guards against re-enqueuing a
        request that's simply still mid-inference right now."""
        min_age = self.cfg.get("ask_stale_pending_seconds", 45)
        for req in self.memos.get_pending_ask_requests(min_age_seconds=min_age):
            self.enqueue(f"ask:{req['id']}", "stale pending (safety poll)",
                        force_priority=0, respect_backoff=True)

    def _enqueue_changed_since_watermark(self) -> int:
        """Poll for notes newer than the watermark and enqueue them. Returns count."""
        changed = list(self.memos.iter_changed_since(self.state.watermark, self.cfg["page_size"]))
        for note in changed:
            self.enqueue(note.get("name", ""),
                         hint_text=note.get("content", "") or "")
        return len(changed)
