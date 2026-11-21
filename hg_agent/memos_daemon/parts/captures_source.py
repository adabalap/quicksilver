"""Captures source: reads Medha's notification stream and keeps what matters.

Built on the same shape as the SMS source -- a minutely tick, a user switch
that starts off, a watermark, a thermal check and a status record the UI can
show -- because the failure modes are the same and a second pattern for the
same job is a second pattern to maintain.

What is different is the cursor discipline, which is the one way to lose data
here: the watermark is saved only AFTER the rows are committed. Save it first,
crash before the insert, and those notifications are gone -- Medha prunes on
its own schedule and will not hand them over twice. Re-reading a page is
cheap; a replayed page dedupes on Medha's row id, which is why
`captures.CAPTURES` has UNIQUE on it.
"""
import json
import time
from datetime import datetime

from ..logging_setup import log
from ..engine import _cfg_bool
from .. import sms
from .. import captures as cap
from .. import medha_brief as mb


class CapturesSourceMixin:
    """Polls Medha's `GET /captures` while the feature is switched on (it is off until you do)."""

    def _captures_status(self, db, state: str, detail: str = "", **extra):
        rec = {"ts": int(time.time()), "state": state, "detail": detail}
        rec.update(extra)
        sms._set_setting(db, "captures_status", json.dumps(rec))
        db.commit()

    def _captures_on(self) -> bool:
        """Both switches. The config key is the deployment's decision and the
        pref is the user's; neither alone reads anything, which mirrors how
        Medha itself gates its listener."""
        if self._backend != "quicksilver" or not _cfg_bool(self.cfg.get("captures_enabled"), True):
            return False
        try:
            return self._user_prefs().get("captures", "off") == "on"
        except Exception:
            return False

    def _maybe_captures(self):
        """From the minutely tick. Cheap when off, when not due, or when the phone is hot."""
        db = self.memos._db()
        if not self._captures_on():
            # Say so once rather than on every tick: a status that rewrites
            # itself sixty times an hour is a status nobody can read a history
            # out of.
            try:
                cur = json.loads(sms._get_setting(db, "captures_status", "{}") or "{}")
            except ValueError:
                cur = {}
            if cur.get("state") not in (None, "", "off"):
                self._captures_status(db, "off", "Switched off in Settings")
            return

        client = self._brief_client()
        if client is None:
            self._captures_status(db, "no_token", "Set medha_url and medha_token")
            return

        now = time.time()
        every = int(self.cfg.get("captures_poll_seconds", 300))
        last = float(sms._get_setting(db, "captures_last_poll", "0") or 0)
        forced = sms._get_setting(db, "captures_scan_requested", "0") == "1"
        if not forced and now - last < every:
            return
        if not forced:
            try:
                t = self.thermal.read_celsius()
                if t is not None and t >= self.cfg.get("thermal_hot_celsius", 47):
                    return                      # a hot phone waits; Medha keeps them for 14 days
            except Exception:
                pass

        cap.ensure(db)
        sms._set_setting(db, "captures_last_poll", str(now))
        if forced:
            sms._set_setting(db, "captures_scan_requested", "0")
        db.commit()

        cursor = int(float(sms._get_setting(db, "captures_cursor", "0") or 0))
        try:
            rows, nxt, listening = mb.drain_captures(
                client, cursor, max_pages=int(self.cfg.get("captures_max_pages", 20)))
        except mb.BriefError as e:
            self._captures_fail(db, e)
            return
        except Exception as e:
            self._captures_status(db, "error", f"{type(e).__name__}: {e}"[:200])
            log.warning(f"  👂 captures read failed: {e}")
            return

        try:
            stats = cap.store(db, rows)
            removed = cap.prune(db, datetime.now())
            # The cursor goes in the SAME transaction as the rows. Committing
            # the rows and then the cursor separately leaves a window where a
            # crash loses a page; committing the cursor first loses it for
            # certain.
            sms._set_setting(db, "captures_cursor", str(int(nxt)))
            db.commit()
        except Exception as e:
            db.rollback()
            self._captures_status(db, "error", f"{type(e).__name__}: {e}"[:200])
            log.warning(f"  👂 captures store failed: {e}")
            return

        if not listening:
            # The distinction that matters. An empty page looks exactly like a
            # quiet phone, and an agent that cannot tell them apart reports a
            # clear day when it has gone deaf.
            self._captures_status(
                db, "not_listening",
                "Medha is not reading notifications. Turn on notification access for Medha, "
                "and check its own switch.", stored=stats["added"])
            return

        self._captures_status(db, "ok", "", seen=stats["seen"], stored=stats["added"],
                              noise=stats["noise"], skipped=stats["skipped"],
                              pruned=removed, cursor=int(nxt))
        if stats["added"]:
            log.info(f"  👂 captures: {stats['added']} kept of {stats['seen']} "
                     f"({stats['noise']} noise)")

    def _captures_fail(self, db, e):
        """Medha's refusals, each with the thing to actually do about it."""
        if e.status == 403:
            # The one capability an app cannot request through a consent
            # dialog: it has to be ticked in Medha's own client editor.
            self._captures_status(
                db, "no_capability",
                "This token's client needs 'Read your notifications'. "
                "Medha app -> API clients -> this client -> tick it.")
            log.warning("  👂 Medha refused /captures: tick 'Read your notifications' on this client")
        elif e.status == 401:
            self._captures_status(db, "no_token", "Medha does not know this token; check Copy API token")
        elif e.status == 404:
            # An older Medha has no such route at all.
            self._captures_status(db, "unsupported", "This Medha is older than v0.20.0 and has no /captures")
            log.info("  👂 captures: this Medha predates v0.20.0")
        else:
            self._captures_status(db, "error", f"{e.status} {e.code}: {e}"[:200])
            log.warning(f"  👂 captures not read ({e.status} {e.code}): {e}")
