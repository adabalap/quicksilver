"""Calendar side effects: queued event deletions and event creation from reminders."""
from ..logging_setup import log
from ..engine import _cfg_bool


class CalendarSyncMixin:
    """Calendar side effects: queued event deletions and event creation from reminders."""

    def _process_calendar_deletions(self):
        """Delete Google Calendar events the user asked to remove.

        Reads the durable `calendar_deletions` queue. That table is deliberately
        independent of `reminders` so a deleted note's events still get cleaned
        up rather than orphaned in the calendar forever.

        Robustness rules:
          • Only events WE created and recorded are ever touched — we never
            enumerate or delete anything else in the user's calendar.
          • "Already gone" (404/410) counts as SUCCESS: the desired end state
            holds, so we stop rather than retry forever.
          • Retries are BOUNDED. A permanent failure (revoked auth, deleted
            calendar) would otherwise retry every minute for eternity, burning
            battery and filling the log. After MAX_ATTEMPTS we give up, record
            the error, and leave it visible to the UI.
          • Idempotent: the queue is keyed by event_id.
        """
        MAX_ATTEMPTS = 5
        if not _cfg_bool(self.cfg.get("calendar_enabled"), False):
            return
        try:
            db = self.memos._db()
            rows = db.execute(
                "SELECT event_id, title, attempts FROM calendar_deletions "
                "WHERE done=0 AND attempts < ? ORDER BY requested_at LIMIT 25",
                (MAX_ATTEMPTS,)).fetchall()
        except Exception:
            return                      # table may not exist on an old DB yet
        if not rows:
            return
        for r in rows:
            ev = r["event_id"]
            try:
                ok = self.calendar.delete_event(ev)
                err = None if ok else "delete returned failure"
            except Exception as e:
                ok, err = False, str(e)[:300]
            if ok:
                db.execute("UPDATE calendar_deletions SET done=1, last_error=NULL "
                           "WHERE event_id=?", (ev,))
                # Clear the pointer on any reminder still referencing it, so the
                # UI stops showing a calendar badge for an event that's gone.
                db.execute("UPDATE reminders SET calendar_event_id='', "
                           "calendar_delete_requested=0 WHERE calendar_event_id=?", (ev,))
                log.info(f"  📅 removed calendar event: {r['title'] or ev}")
            else:
                attempts = (r["attempts"] or 0) + 1
                db.execute("UPDATE calendar_deletions SET attempts=?, last_error=? "
                           "WHERE event_id=?", (attempts, err, ev))
                if attempts >= MAX_ATTEMPTS:
                    log.error(f"  📅 giving up removing calendar event "
                              f"'{r['title'] or ev}' after {attempts} attempts: {err}"
                              f" — remove it in Google Calendar directly")
                else:
                    log.warning(f"  📅 couldn't remove '{r['title'] or ev}' "
                                f"(attempt {attempts}/{MAX_ATTEMPTS}) — will retry")
        db.commit()

    def _create_calendar_from_db(self, name, db, user_only=False):
        """Create Google Calendar events from the reminders stored in the DB for
        this note — the source of truth, which includes user-seeded Quick Event
        reminders (source='user') AND agent-extracted ones, with their resolved
        dates. Marks each as created so re-approval never duplicates events.

        This is used by ALL approval paths so a seeded reminder is never dropped
        just because the model didn't re-emit it in the enrichment dict.

        user_only=True restricts to user-created reminders — used to create a
        Quick Event's calendar entry immediately on note creation, without
        waiting for the enrich/approve cycle (agent-guessed reminders still wait
        for approval since they may be wrong).
        """
        src_clause = " AND COALESCE(source,'agent')='user'" if user_only else ""
        if not _cfg_bool(self.cfg.get("calendar_enabled"), False):
            if db.execute("SELECT 1 FROM reminders WHERE note_id=? AND "
                          "(calendar_event_id IS NULL OR calendar_event_id='')"
                          + src_clause,
                          (name,)).fetchone():
                log.info("  📅 reminders present but calendar_enabled=false "
                         "— set it true (+ run calendar-auth) to create events")
            return
        enr = db.execute("SELECT title FROM enrichments WHERE note_id=? "
                         "ORDER BY enriched_at DESC, rowid DESC LIMIT 1", (name,)).fetchone()
        rows = db.execute(
            "SELECT id, title, datetime_hint, datetime_resolved, description "
            "FROM reminders WHERE note_id=? AND "
            "(calendar_event_id IS NULL OR calendar_event_id='') "
            # A dismissed reminder must never become a calendar event — the user
            # already said no. Without this a dismissal would still fire an event
            # on the next calendar pass.
            "AND COALESCE(dismissed,0)=0" + src_clause,
            (name,)).fetchall()
        if not rows:
            return
        # ── One event per note per TIME SLOT ────────────────────────────────
        # Re-enrichment often re-words a reminder ("Buy milk on the way home" →
        # "Get milk this evening"). Text-matching alone lets that through as a
        # new reminder, and it would become a SECOND calendar event for the same
        # real-world thing — external state the user then has to clean up by
        # hand. Matching on the resolved moment instead catches rewordings,
        # because both phrasings resolve to the same slot.
        try:
            from ..calendar_client import resolve_relative_date as _rrd
            from datetime import date as _date

            def _slot(hint, resolved):
                """A comparable (date, hour, minute) for a reminder, or None."""
                if resolved:
                    return str(resolved)[:16]          # 'YYYY-MM-DDTHH:MM'
                d, hm = _rrd(hint or "", reference=_date.today())
                if not d:
                    return None
                return f"{d.isoformat()}T{(hm or (0, 0))[0]:02d}:{(hm or (0, 0))[1]:02d}"

            taken = set()
            for r in db.execute(
                    "SELECT datetime_hint, datetime_resolved FROM reminders "
                    "WHERE note_id=? AND calendar_event_id IS NOT NULL "
                    "AND calendar_event_id != '' AND COALESCE(dismissed,0)=0",
                    (name,)).fetchall():
                s = _slot(r["datetime_hint"], r["datetime_resolved"])
                if s:
                    taken.add(s)
            if taken:
                keep = []
                for r in rows:
                    s = _slot(r["datetime_hint"], r["datetime_resolved"])
                    if s and s in taken:
                        log.info(f"  📅 '{r['title']}' resolves to a slot this note "
                                 f"already has an event for — not duplicating")
                        # Mark it handled so it isn't retried every pass.
                        db.execute("UPDATE reminders SET calendar_event_id='dup:skipped' "
                                   "WHERE id=?", (r["id"],))
                    else:
                        keep.append(r)
                        if s:
                            taken.add(s)      # guard against dupes within one batch
                db.commit()
                rows = keep
            if not rows:
                return
        except Exception as e:
            log.debug(f"slot-dedup skipped ({e}) — proceeding without it")
        payload = {
            "title": (enr["title"] if enr else "") or "",
            "reminders": [{"title": r["title"],
                           # Prefer the pre-resolved date if present (user events
                           # carry an exact ISO), else the hint for the resolver.
                           "datetime_hint": (r["datetime_resolved"] or r["datetime_hint"] or ""),
                           "description": r["description"] or ""}
                          for r in rows]}
        try:
            n_created = self.calendar.create_events_from_enrichment(payload, name)
            # CRITICAL: only mark reminders as 'created' if the calendar actually
            # created events. On credential failure the calendar creates 0 events
            # but doesn't raise — marking them 'created' anyway would permanently
            # lose the event (it'd be skipped forever after you fix the token).
            if n_created and n_created > 0:
                for r in rows:
                    db.execute("UPDATE reminders SET calendar_event_id='created' "
                               "WHERE id=?", (r["id"],))
                db.commit()
                log.info(f"  📅 {n_created} reminder(s) → calendar for {name}")
            else:
                log.warning(f"  📅 calendar created 0 events for {name} — "
                            f"leaving reminder(s) pending for retry (check "
                            f"credentials: run calendar-auth)")
        except Exception as e:
            log.error(f"  calendar: skipped for {name}: {e}")
