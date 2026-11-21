"""Task follow-through: a daily pass that proposes one move per stale task, and calendar sync for accepted plans."""
from datetime import datetime

from ..logging_setup import log
from ..engine import _cfg_bool
from .. import followups


class FollowupsMixin:
    """Task follow-through: a daily pass that proposes one move per stale task, and calendar sync for accepted plans."""

    def _maybe_followups(self):
        """Once a day (after 06:00 local), or when the user taps "Check now", raise up to three stale
        tasks as decisions. Runs from the minutely tick; cheap when there is nothing to do."""
        if self._backend != "quicksilver" or not _cfg_bool(self.cfg.get("followups_enabled"), True):
            return
        db = self.memos._db()
        followups.ensure(db)
        self._sync_followup_calendar(db)
        try:
            if self._user_prefs().get("followups", "on") == "off":
                return
        except Exception:
            pass
        now = datetime.now().astimezone()
        today = now.date().isoformat()
        forced = db.execute("SELECT value FROM settings WHERE key='followups_refresh_requested'").fetchone()
        force = bool(forced and forced["value"] == "1")
        if not force:
            if now.hour < 6:
                return
            last = db.execute("SELECT value FROM settings WHERE key='followups_last_run'").fetchone()
            if last and last["value"] == today:
                return
        if force:
            db.execute("UPDATE settings SET value='0' WHERE key='followups_refresh_requested'")
            db.commit()
        draft = None
        if getattr(self, "gemma", None) is not None:
            hot = False
            try:
                t = self.thermal.read_celsius()
                hot = t is not None and t >= self.cfg.get("thermal_hot_celsius", 47)
            except Exception:
                pass
            if not hot:                       # a hot phone gets plain reschedules, no model call
                cap = int(self.cfg.get("followups_step_tokens", 40))
                draft = lambda p: self.gemma.complete(p, max_tokens=cap)
        n = followups.run(db, now, self.cfg, draft=draft, force=force)
        followups._set_setting(db, "followups_last_run", today)
        db.commit()
        if n:
            log.info(f"  🧭 {n} task follow-up(s) need a decision")

    def _sync_followup_calendar(self, db):
        """An accepted plan is a normal reminder; this turns it into an all-day calendar event, using the same
        path as a Quick Event. Bounded retries, and nothing happens while the calendar is not connected."""
        if not _cfg_bool(self.cfg.get("calendar_enabled"), False):
            return
        rows = db.execute(
            "SELECT id, note_id, reminder_id FROM followups WHERE status='accepted' AND cal_synced=0 "
            "AND reminder_id IS NOT NULL AND cal_tries < 5 ORDER BY id LIMIT 10").fetchall()
        for r in rows:
            try:
                self._create_calendar_from_db(r["note_id"], db, user_only=True)
            except Exception as e:
                log.debug(f"followup calendar sync failed: {e}")
            rem = db.execute("SELECT calendar_event_id, dismissed FROM reminders WHERE id=?",
                             (r["reminder_id"],)).fetchone()
            if rem is None or rem["dismissed"] or (rem["calendar_event_id"] or ""):
                db.execute("UPDATE followups SET cal_synced=1 WHERE id=?", (r["id"],))
            else:
                db.execute("UPDATE followups SET cal_tries=cal_tries+1 WHERE id=?", (r["id"],))
        if rows:
            db.commit()
