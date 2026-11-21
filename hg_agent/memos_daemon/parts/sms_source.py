"""SMS source: reads bill messages through Termux:API and turns them into reminders."""
import json
import os
import shutil
import subprocess
import time
from datetime import datetime

from ..logging_setup import log
from ..engine import _cfg_bool
from .. import sms


class SmsSourceMixin:
    """Polls `termux-sms-list` every few minutes while the feature is switched on (it is off until you do)."""

    # Where messages come from is a method so tests can replace it with canned messages.
    # Two sources: the Termux:API command itself, or a JSON file that bin/sms-bridge.sh keeps fresh from
    # native Termux. The file is for agents that run inside proot (Fedora/Debian), where Termux's own
    # commands are not available.
    def _sms_run(self, args: list, timeout: int = 40) -> tuple[int, str, str]:
        path = (self.cfg.get("sms_file") or "").strip()
        if path:
            path = os.path.expanduser(path)
            if not os.path.exists(path):
                err = ""
                try:
                    err = open(path + ".err").read().strip()[:200]
                except OSError:
                    pass
                return (1, "", err) if err else (127, "", f"sms bridge file not found: {path}")
            if "-o" in args and int(args[args.index("-o") + 1]) > 0:
                return 0, "[]", ""                      # the file holds the newest page; nothing beyond it
            self._sms_file_age = time.time() - os.path.getmtime(path)
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                return 0, fh.read(), ""
        exe = shutil.which(self.cfg.get("sms_command") or "termux-sms-list")
        if not exe:
            return 127, "", "termux-sms-list not found"
        p = subprocess.run([exe] + args, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr

    def _sms_status(self, db, state: str, detail: str = "", **extra):
        rec = {"ts": int(time.time()), "state": state, "detail": detail}
        rec.update(extra)
        sms._set_setting(db, "sms_status", json.dumps(rec))
        db.commit()

    def _sms_config(self, db) -> dict:
        return {"days_back": int(self.cfg.get("sms_days_back", 60))}

    def _fetch_sms(self, since_ms: int, page: int, max_pages: int):
        """Newest first, in pages, stopping once a page reaches messages we already have."""
        out, offset = [], 0
        for _ in range(max_pages):
            code, so, se = self._sms_run(["-t", "inbox", "-l", str(page), "-o", str(offset)])
            if code != 0:
                return None, code, (se or so or "").strip()[:200]
            try:
                batch = json.loads(so or "[]")
            except Exception:
                return None, 1, "termux-sms-list returned something that is not JSON"
            if not isinstance(batch, list):
                return None, 1, "unexpected output from termux-sms-list"
            out.extend(batch)
            if len(batch) < page:
                break
            oldest = min((int(sms._received(m, datetime.now()).timestamp() * 1000) for m in batch if isinstance(m, dict)),
                         default=0)
            if since_ms and oldest <= since_ms:
                break
            offset += page
        return out, 0, ""

    def _maybe_sms(self):
        """From the minutely tick. Cheap when off, when not due, or when the phone is hot."""
        if self._backend != "quicksilver" or not _cfg_bool(self.cfg.get("sms_enabled"), True):
            return
        db = self.memos._db()
        sms.ensure(db)
        try:
            on = self._user_prefs().get("sms", "off") == "on"
        except Exception:
            on = False
        forced = sms._get_setting(db, "sms_scan_requested", "0") == "1"
        if not on:
            if sms._get_setting(db, "sms_status", "") and json.loads(sms._get_setting(db, "sms_status", "{}") or "{}").get("state") != "off":
                self._sms_status(db, "off", "Switched off in Settings")
            return
        now = time.time()
        every = int(self.cfg.get("sms_poll_seconds", 300))
        last = float(sms._get_setting(db, "sms_last_poll", "0") or 0)
        if not forced and now - last < every:
            return
        try:
            t = self.thermal.read_celsius()
            if t is not None and t >= self.cfg.get("thermal_hot_celsius", 47) and not forced:
                return                                  # a hot phone waits; messages are not going anywhere
        except Exception:
            pass
        sms._set_setting(db, "sms_last_poll", str(now))
        if forced:
            sms._set_setting(db, "sms_scan_requested", "0")
        db.commit()
        cfg = self._sms_config(db)
        if sms._get_setting(db, "sms_body_backfill", "") != "1":
            # Bills stored before their message text was kept show "the original message isn't kept". Read the
            # inbox once more from the start so the open ones get their text (the scan fills it in, once).
            sms._set_setting(db, "sms_body_backfill", "1")
            if db.execute("SELECT 1 FROM sms_bills WHERE status='open' AND body=''").fetchone():
                sms._set_setting(db, "sms_watermark", "0")
            db.commit()
        wm = int(float(sms._get_setting(db, "sms_watermark", "0") or 0))
        first = wm == 0
        page = int(self.cfg.get("sms_page_size", 100))
        pages = int(self.cfg.get("sms_first_pages", 8)) if first else int(self.cfg.get("sms_pages", 3))
        msgs, code, err = self._fetch_sms(wm, page, pages)
        if msgs is None:
            low = (err or "").lower()
            if code == 127 and (self.cfg.get("sms_file") or "").strip():
                self._sms_status(db, "no_bridge", "Start bin/sms-bridge.sh in Termux (outside Fedora). " + (err or ""))
            elif code == 127 or "not found" in low:
                self._sms_status(db, "no_termux_api", "Not available here. If the agent runs inside Fedora, use the SMS bridge: see the README.")
            elif "permission" in low or "denied" in low or "security" in low:
                self._sms_status(db, "no_permission", "Allow SMS for the Termux:API app in Android settings")
            else:
                self._sms_status(db, "error", err or f"exit code {code}")
            log.warning(f"  💬 SMS read failed: {err or code}")
            return
        try:
            stats = sms.scan(db, msgs, cfg, datetime.now())
            if stats["newest"] > wm:
                sms._set_setting(db, "sms_watermark", str(stats["newest"]))
            db.commit()
        except Exception as e:
            db.rollback()
            self._sms_status(db, "error", f"{type(e).__name__}: {e}"[:200])
            log.warning(f"  💬 SMS scan failed: {e}")
            return
        age = getattr(self, "_sms_file_age", None)
        if (self.cfg.get("sms_file") or "").strip() and age is not None and age > 1800:
            self._sms_status(db, "error", f"The SMS bridge in Termux last wrote {int(age // 60)} minutes ago. Is it still running?")
            return
        self._sms_status(db, "ok", "", scanned=stats["seen"], added=stats["added"], skipped=stats["skipped"],
                         bills=stats["bills"], paid=stats["paid"])
        if stats["added"] or stats["paid"]:
            log.info(f"  💬 SMS: {stats['added']} new bill(s), {stats['reminders']} reminder(s), {stats['paid']} paid")
            if stats["reminders"]:
                try:                                # Google Calendar then notifies you even with the app closed
                    self._create_calendar_from_db(sms.host_note(db), db, user_only=True)
                    db.commit()
                except Exception as e:
                    log.debug(f"  💬 bill reminders not sent to the calendar: {e}")
            self._broadcast_spend()
        self._maybe_medha_brief(force_check=True)

    def _broadcast_spend(self):
        """Tell the open app something changed. Best effort, like every other push."""
        url = getattr(self.memos, "webhook_url", None)
        if not url:
            return
        try:
            import requests
            requests.post(f"{url}/api/webhook", json={"event": "bills_changed"}, timeout=3)
        except Exception:
            pass
