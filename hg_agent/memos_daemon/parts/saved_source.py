"""Saved source: moves what a person pressed Save on in Medha into the inbox.

Deliberately NOT gated on the "Read your notifications" switch that governs
the captures source, though both read through Medha's capture capability.

That switch exists because reading the whole notification stream is a
standing decision about privacy, and it defaults off. A save is the opposite
case: one notification, picked by hand, with a button whose only meaning is
"put this in Quicksilver". Gating it on the ambient switch would make Save
silently do nothing for anyone who had not also found a setting in another
app -- and a button that appears to work and does not is worse than no
button.

What it does need is a configured Medha. Without one there is nothing to read
from, and the status says so rather than going quiet.
"""
import json
import time

from ..logging_setup import log
from .. import sms
from .. import saved as sv


class SavedSourceMixin:
    """Polls Medha's `GET /saved` and drains it into `saved_captures`."""

    def _saved_status(self, db, state: str, detail: str = "", **extra):
        rec = {"ts": int(time.time()), "state": state, "detail": detail}
        rec.update(extra)
        sms._set_setting(db, "saved_status", json.dumps(rec))
        db.commit()

    def _maybe_saved(self):
        """From the minutely tick. One cheap GET when there is nothing waiting."""
        if getattr(self, "_backend", "quicksilver") != "quicksilver":
            return
        db = self.memos._db()
        client = self._brief_client()
        if client is None:
            return                     # the brief source already reports a missing token

        now = time.time()
        # Short, because someone just pressed a button and may open the app
        # next. The request is one GET that usually returns nothing.
        every = int(self.cfg.get("saved_poll_seconds", 60))
        last = float(sms._get_setting(db, "saved_last_poll", "0") or 0)
        if now - last < every:
            return
        sms._set_setting(db, "saved_last_poll", str(now))
        db.commit()

        out = sv.drain(client, db)
        if out["skipped"]:
            self._saved_status(db, "skipped", str(out["skipped"]))
            return
        if out["stored"]:
            # No push to the app from here: the agent and the app share only
            # the database, and the app already re-reads its counts every 15
            # seconds while it is visible. A notify call would be a call to a
            # method this process does not have.
            log.info(f"📥 saved: {out['stored']} new from Medha, waiting for a decision")
            self._saved_status(db, "ok", f"{out['stored']} new", stored=out["stored"])
        else:
            self._saved_status(db, "ok", "nothing new")
