"""Stuck-note recovery: release, settle, rescue and promote notes that stalled mid-flight."""
from ..anchor import content_hash
from ..logging_setup import log


class RecoveryMixin:
    """Stuck-note recovery: release, settle, rescue and promote notes that stalled mid-flight."""

    # ── 'force' (Processing) limbo recovery ──────────────────────────────────
    FORCE_MAX_ATTEMPTS = 3

    def _release_force(self, name: str, why: str):
        """Deterministic no-op outcome (skipped note): stop showing Processing."""
        if self._backend != "quicksilver":
            return
        rest = self.memos.release_force(name)
        if rest:
            log.info(f"    ↩ {name}: not enriched ({why}) → {rest}")

    def _settle_force(self, name: str):
        """Called after every note job. If the note is STILL 'force', this run
        produced no enrichment (inference failed / returned nothing). Count the
        attempt; after FORCE_MAX_ATTEMPTS give up cleanly (resting status + a
        visible agent-error) instead of leaving it blinking forever. Below the
        cap it stays 'force' and the sweeper retries it after a backoff."""
        if self._backend != "quicksilver":
            return
        with self._qlock:
            if name in self._queued:          # re-queued (e.g. thermal) → still live
                return
        if not any(n == name for n, _ in self.memos.list_force(0)):
            return                            # resolved normally
        n = self.memos.force_failed(name)
        if n >= self.FORCE_MAX_ATTEMPTS:
            rest = self.memos.release_force(name)
            try: self.memos.record_error(name, f"gave up after {n} attempts")
            except Exception: pass
            log.warning(f"  ✗ {name}: gave up after {n} attempts → {rest or 'released'}")
        else:
            log.info(f"  ↻ {name}: no result (attempt {n}/{self.FORCE_MAX_ATTEMPTS}) "
                     f"— will retry")

    def _rescue_stuck_force(self, older_than_s: int = None):
        """Sweep: any 'force' note that nothing is working on gets re-queued
        (bounded retries) or released. Catches the agent being killed mid-run
        and Re-run taps made while the agent was down (those don't touch
        notes.updated_at, so the watermark scan can't see them)."""
        if self._backend != "quicksilver":
            return
        if older_than_s is None:
            older_than_s = int(self.cfg.get("force_stuck_seconds", 120))
        for name, failures in self.memos.list_force(older_than_s):
            with self._qlock:
                if name in self._queued or name == self._inflight:
                    continue
            if failures >= self.FORCE_MAX_ATTEMPTS:
                rest = self.memos.release_force(name)
                try: self.memos.record_error(name, "stalled in Processing")
                except Exception: pass
                log.warning(f"  ✗ {name}: stuck in Processing, released → {rest}")
            else:
                self.enqueue(name, f"rescue: stuck in Processing (try {failures+1})",
                             respect_backoff=True)

    def _promote_stranded_dirty(self):
        """Safety net: promote notes left 'dirty' for a long time to 'force'.

        The normal path enriches a note when the user CLOSES the editor (commit).
        But a session can end without a clean close — app killed, phone died,
        tab lost. Such a note would sit 'dirty' (durable but never enriched)
        forever. This sweep rescues it once it has clearly been abandoned: no
        edit for `enrich_stranded_seconds` (default 30 min). A note still being
        actively edited keeps updating its timestamp and is never swept, so this
        can't fire mid-session — it only catches genuine orphans.
        """
        if self._backend != "quicksilver":
            return
        threshold = int(self.cfg.get("enrich_stranded_seconds", 1800))
        if threshold <= 0:
            return
        try:
            db = self.memos._db()
            rows = db.execute(
                "SELECT note_id FROM note_state ns "
                "JOIN notes n ON n.id = ns.note_id "
                "WHERE ns.status = 'dirty' "
                "AND n.updated_at < strftime('%Y-%m-%dT%H:%M:%fZ','now', ?)",
                (f'-{threshold} seconds',)).fetchall()
        except Exception:
            return
        for r in rows:
            db.execute("UPDATE note_state SET status='force' WHERE note_id=? "
                       "AND status='dirty'", (r["note_id"],))
            log.info(f"  🕰️  promoting stranded note {r['note_id']} "
                     f"(dirty > {threshold}s, session likely lost) → enrich")
        if rows:
            db.commit()

    def _changed_during_inference(self, name: str, body0: str) -> bool:
        """The note's text moved on while the model was working (you kept typing, or edited it elsewhere)."""
        try:
            cur = self.memos.get_memo(name)
        except Exception:
            return False
        if cur is None:
            return True                                   # deleted meanwhile: nothing to write to
        now = (cur.get("content") or cur.get("body") or "").strip()
        return content_hash(now) != content_hash(body0)
