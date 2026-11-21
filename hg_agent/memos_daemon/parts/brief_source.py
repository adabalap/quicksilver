"""Medha's brief widget and notifications: what needs you today, kept in step with what you finish."""
import json
import threading
import time
from datetime import datetime

from ..logging_setup import log
from ..engine import _cfg_bool
from .. import sms
from .. import medha_brief as mb
from .. import captures as cap


class BriefSourceMixin:
    """Posts bills, dated reminders and stalled tasks to Medha's brief (replaced on change, deleted when empty),
    a silent notification with the note's link for what is due today or late (cancelled once it is done), and
    asks the model, in the background and only when the phone is cool, for a useful line per item."""

    def _brief_client(self):
        tok = (self.cfg.get("medha_token") or "").strip()
        base = (self.cfg.get("medha_url") or "").strip()
        if not tok or not base:
            return None
        c = getattr(self, "_brief_c", None)
        if c is None or (c.base, c.token) != (base.rstrip("/"), tok):
            c = self._brief_c = mb.MedhaBrief(base, tok)
        return c

    def _brief_sources(self):
        try:
            sms_on = self._user_prefs().get("sms", "off") == "on"
        except Exception:
            sms_on = False
        fu_on = True
        try:
            fu_on = self._user_prefs().get("followups", "on") != "off"
        except Exception:
            pass
        return dict(bills=_cfg_bool(self.cfg.get("medha_brief_bills"), True), manual_bills_only=not sms_on,
                    reminders=_cfg_bool(self.cfg.get("medha_brief_reminders"), True),
                    followups=fu_on and _cfg_bool(self.cfg.get("medha_brief_followups"), True),
                    actions=_cfg_bool(self.cfg.get("medha_brief_actions"), True))

    def _maybe_medha_brief(self, force_check: bool = False):
        """Cheap when nothing changed (a few queries and a hash compare). Called from the minutely tick, after an
        SMS scan, and when the app says a bill or reminder was closed."""
        if not _cfg_bool(self.cfg.get("medha_brief"), True):
            return
        client = self._brief_client()
        if client is None:
            return
        now = time.time()
        if now < getattr(self, "_brief_retry_at", 0) and not force_check:
            return
        db = self.memos._db()
        sms.ensure(db)
        src = self._brief_sources()
        posted_before = bool(sms._get_setting(db, "medha_brief_hash", "") or sms._get_setting(db, "medha_notif_state", "{}") not in ("", "{}"))
        if not any(src.values()) and not posted_before:
            return
        base = (self.cfg.get("medha_brief_link") or "").strip()
        cands = mb.gather(db, datetime.now(), amounts=_cfg_bool(self.cfg.get("medha_brief_amounts"), True),
                          pay_links=_cfg_bool(self.cfg.get("medha_brief_pay_links"), False), app_base=base,
                          rem_back=int(self.cfg.get("medha_brief_reminder_back_days", 7)),
                          rem_ahead=int(self.cfg.get("medha_brief_reminder_ahead_days", 14)),
                          actions_max=int(self.cfg.get("medha_brief_actions_max", 25)), **src) \
            if any(src.values()) else []
        # What the notification stream says about what we already know. Never
        # adds a row -- only annotates one that was going to be posted anyway,
        # and only in the demoting direction. See captures.corroborate for why
        # captures are used this way round rather than becoming rows.
        # getattr, not self._captures_on(): the brief must not require the
        # captures mixin to exist. brief_test.py builds a stub that mixes in
        # BriefSourceMixin alone, which is the right thing for it to do -- a
        # hard call here made the whole brief raise AttributeError in a test
        # that has nothing to do with captures.
        captures_on = getattr(self, "_captures_on", None)
        if cands and captures_on is not None and captures_on():
            try:
                cap.ensure(db)
                n = cap.corroborate(db, cands, datetime.now(),
                                    window_h=int(self.cfg.get("captures_corroborate_hours", 72)))
                if n:
                    log.info(f"  👂 captures: {n} item(s) settled or cancelled by a notification")
            except Exception as e:
                log.debug(f"  👂 corroboration skipped: {e}")
        ai = self._brief_ai_load(db)
        try:
            # Capabilities BEFORE pick(), because which protocol Medha speaks
            # decides what shape pick() builds. Asked once an hour rather than
            # per post, and gated on there being something to post at all.
            if cands and now >= getattr(self, "_brief_caps_at", 0):
                caps = client.capabilities()
                self._brief_caps_at = now + 3600
                self._brief_notif_ok = caps.get("notifications", True) is not False
                # Reads as 1 against anything older than Medha v0.20.0, which
                # has no such field and would refuse an obligations body for a
                # missing `items`.
                was = getattr(self, "_brief_protocol", 1)
                self._brief_protocol = mb.protocol_of(caps)
                if self._brief_protocol != was:
                    log.info(f"  📋 Medha brief protocol: v{self._brief_protocol}")
                if caps.get("brief") is False:
                    log.warning("  📋 Medha says the brief widget is not available on this device")
                    self._brief_retry_at = now + 3600
                    return
            protocol = getattr(self, "_brief_protocol", 1)
            items = mb.pick(cands, ai, limit=max(3, int(self.cfg.get("medha_brief_max_items", 50))),
                            app_base=base, protocol=protocol)
            did = mb.reconcile(db, client, items,
                               refresh_s=float(self.cfg.get("medha_brief_refresh_hours", 6)) * 3600,
                               ttl_s=int(self.cfg.get("medha_brief_ttl_hours", 24)) * 3600,
                               protocol=protocol)
            if did in ("posted", "cleared"):
                log.info(f"  📋 Medha brief {did}: {len(items)} item(s)")
            self._brief_retry_at = 0
        except mb.BriefError as e:
            self._brief_fail(e, "brief")
            return
        except Exception as e:
            self._brief_retry_at = now + 900
            log.warning(f"  📋 Medha brief error: {e}")
            return
        if _cfg_bool(self.cfg.get("medha_brief_notify"), True) and getattr(self, "_brief_notif_ok", True):
            try:
                if not items:
                    mb.clear_notifications(db, client)
                else:
                    p, c = mb.sync_notifications(db, client, cands, int(self.cfg.get("medha_brief_notify_max", 3)),
                                                int(self.cfg.get("medha_brief_notify_days", 0)))
                    if p or c:
                        log.info(f"  🔔 Medha notifications: {p} posted, {c} cancelled")
            except mb.BriefError as e:
                self._brief_fail(e, "notification")
            except Exception as e:
                log.debug(f"  🔔 Medha notifications skipped: {e}")
        self._brief_ai_kick(cands, ai)

    def _brief_fail(self, e, what):
        now = time.time()
        self._brief_retry_at = now + (900 if e.status in (0, 429, 503) else 3600)
        if e.status == 403:
            log.warning("  📋 Medha refused the brief: this token's client needs the 'notify' capability")
        elif e.status == 401:
            log.warning("  📋 Medha does not know this token (medha_token); check Copy API token")
        elif e.code == "not_posted":
            log.warning(f"  🔔 Medha did not show the notification: {e}")
        else:
            log.warning(f"  📋 Medha {what} not updated ({e.status} {e.code}): {e}")

    # ── AI lines: written in the background, cached by what they were written from ──────────────────────────
    def _brief_ai_load(self, db) -> dict:
        try:
            return json.loads(sms._get_setting(db, "medha_brief_ai", "{}") or "{}")
        except ValueError:
            return {}

    def _brief_ai_kick(self, cands, ai):
        if not _cfg_bool(self.cfg.get("medha_brief_ai"), True) or getattr(self, "gemma", None) is None:
            return
        todo = [c for c in sorted(cands, key=lambda c: -c["score"])[:int(self.cfg.get("medha_brief_ai_top", 3))]
                if c.get("ai_ctx") and (ai.get(c["key"]) or {}).get("sig") != mb.sig(c)
                and (ai.get(c["key"]) or {}).get("fail") != mb.sig(c)]
        if not todo or getattr(self, "_brief_ai_busy", False):
            return
        try:
            t = self.thermal.read_celsius()
            if t is not None and t >= self.cfg.get("thermal_hot_celsius", 47):
                return
        except Exception:
            pass
        self._brief_ai_busy = True
        threading.Thread(target=self._brief_ai_run, args=(todo[:2],), daemon=True, name="brief-ai").start()

    def _brief_ai_run(self, todo):
        try:
            db = self.memos._db()
            ai = self._brief_ai_load(db)
            cap = int(self.cfg.get("medha_brief_ai_tokens", 48))
            changed = False
            for c in todo:
                raw = self.gemma.complete(mb.AI_PROMPT.format(title=c["title"], ctx=c["ai_ctx"]), max_tokens=cap)
                text = mb.clean_ai(raw)
                ai[c["key"]] = {"sig": mb.sig(c), "text": text} if text else {"fail": mb.sig(c)}
                changed = True
            if changed:
                live = {k for k in ai}
                if len(live) > 60:
                    for k in sorted(live)[:len(live) - 60]:
                        ai.pop(k, None)
                sms._set_setting(db, "medha_brief_ai", json.dumps(ai))
                db.commit()
        except Exception as e:
            log.debug(f"  📋 brief AI line skipped: {e}")
        finally:
            self._brief_ai_busy = False
        try:
            self._maybe_medha_brief(force_check=True)
        except Exception:
            pass
