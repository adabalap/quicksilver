"""calendar_client.py — Google Calendar integration for memos_daemon.

Reads the `reminders` list produced by GemmaEngine (via the updated prompt)
and creates Google Calendar events for any item that has a parseable datetime.

Architecture notes
──────────────────
• Uses google-auth + google-api-python-client (pure-Python, installable inside
  the proot with pip).
• Credentials come from a service-account JSON or an OAuth2 token file
  (path set via config key `calendar_credentials_file`).
• Events are created in the user's primary calendar (or a named one via
  `calendar_id` config key, default "primary").
• Relative dates (tomorrow, today, next Monday) are now resolved to real
  datetimes by a deterministic post-processor against the real clock — no
  model guessing involved. datetime_hint="" means genuinely no date/time.
• Reminders with a date but NO time (e.g. "call John tomorrow") become
  all-day events: cleaner than an arbitrary default time.
• The client is a no-op (all methods return immediately) when
  `calendar_enabled` is False, so no code paths change for users who haven't
  set it up.
• Thread-safety: CalendarClient is constructed once in MemosDaemon.__init__
  and called only from the single worker thread, so no locking is needed.

Setup (one-time, inside the proot)
────────────────────────────────────
1.  pip install --break-system-packages \\
        google-auth google-auth-oauthlib google-auth-httplib2 \\
        google-api-python-client python-dateutil

2.  Go to console.cloud.google.com → create a project → enable
    "Google Calendar API" → create OAuth 2.0 credentials (Desktop app) →
    download the client_secret_*.json.

3.  Run the one-time auth flow (generates token.json):
        python3 app.py calendar-auth --headless
    (--headless prints a URL to open on any device, then paste the code back —
    use it on Termux/Android where there's no local browser)

4.  In config.json set:
        "calendar_enabled": true,
        "calendar_credentials_file": "/root/.config/memos_daemon/token.json",
        "calendar_client_secret_file": "/root/.config/memos_daemon/client_secret.json"
"""

import json
import os
import re
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from .logging_setup import log

# ── optional heavy imports ────────────────────────────────────────────────────
try:
    from dateutil import parser as _du_parser
    _DATEUTIL = True
except ImportError:
    _DATEUTIL = False

try:
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build as _gcal_build
    _GCAL_AVAILABLE = True
except ImportError:
    _GCAL_AVAILABLE = False

_SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

# ── Relative-date post-processor ──────────────────────────────────────────────
# Maps weekday names to Python weekday() integers (Mon=0 … Sun=6).
_WEEKDAY_MAP = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
    "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
}

_MONTH_MAP = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Simple 12-hour time pattern, e.g. "10am", "3:30 PM", "9 am"
_TIME_RE = re.compile(
    r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", re.IGNORECASE
)
# 24-hour pattern, e.g. "14:30", "09:00"
_TIME24_RE = re.compile(r"\b(\d{1,2}):(\d{2})\b")


def _parse_time(hint: str) -> tuple[int, int] | None:
    """Return (hour, minute) in 24-hour format from hint, or None."""
    m = _TIME_RE.search(hint)
    if m:
        h, mn, meridiem = int(m.group(1)), int(m.group(2) or 0), m.group(3).lower()
        if meridiem == "pm" and h != 12:
            h += 12
        elif meridiem == "am" and h == 12:
            h = 0
        return h, mn
    m = _TIME24_RE.search(hint)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None


def resolve_relative_date(hint: str, reference: date | None = None) -> tuple[date | None, tuple[int, int] | None]:
    """Deterministic relative-date resolver against the real clock.

    Returns (resolved_date, time_hm) where:
      • resolved_date: a datetime.date or None if the hint can't be resolved.
      • time_hm: (hour, minute) 24-hr tuple or None if no time was specified.

    Patterns supported (case-insensitive):
      today, tonight, tomorrow, yesterday,
      next <weekday>, this <weekday>, <weekday> (nearest future occurrence),
      in N day(s)/week(s).

    If the hint is already an ISO 8601 string or something dateutil handles,
    None is returned for resolved_date so the caller falls back to _parse_dt.
    """
    if reference is None:
        reference = datetime.now(timezone.utc).date()

    low = hint.strip().lower()
    time_hm = _parse_time(low)

    # Already an ISO 8601 date/datetime (e.g. "2026-07-31" or
    # "2026-07-31T00:00:00"). Parse it directly so a resolved hint round-trips
    # to the same date — otherwise a reworded reminder carrying an ISO string
    # wouldn't match its relative-worded twin and would duplicate.
    m_iso = re.match(r"(\d{4})-(\d{2})-(\d{2})(?:[t ](\d{2}):(\d{2}))?", low)
    if m_iso:
        try:
            d = date(int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3)))
            if m_iso.group(4):
                time_hm = time_hm or (int(m_iso.group(4)), int(m_iso.group(5)))
            return d, time_hm
        except ValueError:
            pass

    if low.startswith("today") or low.startswith("tonight"):
        return reference, time_hm
    if low.startswith("tomorrow"):
        return reference + timedelta(days=1), time_hm
    if low.startswith("yesterday"):
        return reference - timedelta(days=1), time_hm

    # "next <weekday>" or "this <weekday>"
    for prefix in ("next ", "this "):
        if low.startswith(prefix):
            day_word = low[len(prefix):].split()[0].rstrip(",.")
            if day_word in _WEEKDAY_MAP:
                target_wd = _WEEKDAY_MAP[day_word]
                days_ahead = (target_wd - reference.weekday()) % 7
                if prefix == "next " and days_ahead == 0:
                    days_ahead = 7
                elif days_ahead == 0:
                    days_ahead = 7
                return reference + timedelta(days=days_ahead), time_hm

    # bare weekday name: nearest future occurrence
    first_word = low.split()[0].rstrip(",.")
    if first_word in _WEEKDAY_MAP:
        target_wd = _WEEKDAY_MAP[first_word]
        days_ahead = (target_wd - reference.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        return reference + timedelta(days=days_ahead), time_hm

    # "this/next weekend" and "this/next week" — phrases the model emits often
    # enough that leaving them unresolved meant real reminders never became
    # events. A weekend resolves to the coming Saturday (the start of it), which
    # is what people mean when they say "let's do X next weekend".
    m = re.match(r"(this|next)\s+(weekend|week)\b", low)
    if m:
        which, unit = m.group(1), m.group(2)
        if unit == "weekend":
            days_ahead = (5 - reference.weekday()) % 7          # 5 = Saturday
            if days_ahead == 0 and which == "next":
                days_ahead = 7
            elif days_ahead == 0:
                days_ahead = 7
            if which == "next" and reference.weekday() >= 5:
                days_ahead += 7        # already the weekend → mean the NEXT one
            return reference + timedelta(days=days_ahead), time_hm
        # "next week" → the coming Monday; "this week" → tomorrow as a nudge
        if which == "next":
            days_ahead = (0 - reference.weekday()) % 7 or 7      # 0 = Monday
            return reference + timedelta(days=days_ahead), time_hm
        return reference + timedelta(days=1), time_hm

    # "in N day(s)" / "in N week(s)"
    m = re.match(r"in\s+(\d+)\s+(day|days|week|weeks)", low)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        delta = timedelta(days=n) if "day" in unit else timedelta(weeks=n)
        return reference + delta, time_hm

    # "before/by <date>" — a deadline. The event should land ON the named date
    # (the last day it can happen), so strip the qualifier and resolve the rest.
    m = re.match(r"(?:before|by|due(?:\s+on)?|no later than)\s+(.+)", low)
    if m:
        inner, t2 = resolve_relative_date(m.group(1), reference)
        if inner is not None:
            return inner, time_hm or t2

    # "end/start of the month/quarter/week/year" — defined points, not vague.
    m = re.match(r"(end|start|beginning)\s+of\s+(?:the\s+)?"
                 r"(month|quarter|week|year)", low)
    if m:
        which, unit = m.group(1), m.group(2)
        end = which == "end"
        y, mo = reference.year, reference.month
        if unit == "month":
            if end:
                nm = date(y + (mo == 12), (mo % 12) + 1, 1)
                return nm - timedelta(days=1), time_hm
            return date(y, mo, 1), time_hm
        if unit == "quarter":
            q_end_month = ((mo - 1) // 3 + 1) * 3          # 3,6,9,12
            q_start_month = q_end_month - 2
            if end:
                nm = date(y + (q_end_month == 12), (q_end_month % 12) + 1, 1)
                return nm - timedelta(days=1), time_hm
            return date(y, q_start_month, 1), time_hm
        if unit == "year":
            return (date(y, 12, 31) if end else date(y, 1, 1)), time_hm
        if unit == "week":
            monday = reference - timedelta(days=reference.weekday())
            return (monday + timedelta(days=6) if end else monday), time_hm

    # Absolute "<Month> <day>" (e.g. "August 10", "Aug 10th") with no year —
    # assume the next occurrence (this year, or next year if already past).
    m = re.match(r"(?:on\s+)?([a-z]{3,9})\s+(\d{1,2})(?:st|nd|rd|th)?\b", low)
    if m and m.group(1) in _MONTH_MAP:
        mon = _MONTH_MAP[m.group(1)]
        day = int(m.group(2))
        try:
            cand = date(reference.year, mon, day)
            if cand < reference:
                cand = date(reference.year + 1, mon, day)
            return cand, time_hm
        except ValueError:
            pass

    # Not a relative pattern — let _parse_dt handle it
    return None, time_hm


# ─────────────────────────────────────────────────────────────────────────────

class CalendarClient:
    """Thin wrapper around the Google Calendar API.

    All public methods are no-ops when calendar_enabled=False or the required
    libraries are not installed, making the class safe to construct
    unconditionally in MemosDaemon.
    """

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._enabled = cfg.get("calendar_enabled", False)
        self._svc = None          # lazy-initialised Google Calendar service
        self._svc_lock = threading.Lock()
        self._tz = cfg.get("calendar_timezone", "Asia/Kolkata")
        self._dur = int(cfg.get("calendar_default_duration_minutes", 30))
        self._lookahead = int(cfg.get("calendar_lookahead_days", 30))
        self._cal_id = cfg.get("calendar_id", "primary")
        self._add_desc = cfg.get("calendar_add_description", True)
        self._token_path = cfg.get("calendar_credentials_file", "")
        self._secret_path = cfg.get("calendar_client_secret_file", "")

        if self._enabled:
            if not _GCAL_AVAILABLE:
                log.warning(
                    "calendar_enabled=true but google-api-python-client is not "
                    "installed. Run: pip install --break-system-packages "
                    "google-auth google-auth-oauthlib google-auth-httplib2 "
                    "google-api-python-client python-dateutil"
                )
                self._enabled = False
            elif not _DATEUTIL:
                log.warning(
                    "calendar_enabled=true but python-dateutil is not installed. "
                    "Run: pip install --break-system-packages python-dateutil"
                )
                self._enabled = False

    # ── Public API ────────────────────────────────────────────────────────────

    def create_events_from_enrichment(self, enrichment: dict, note_name: str):
        """Called after a note is approved. Reads the `reminders` list from
        the enrichment dict and creates calendar events for each one that has
        a parseable or resolvable datetime. Items with no datetime are logged
        as pending. Items with a date but no time become all-day events."""
        if not self._enabled:
            return 0

        reminders = enrichment.get("reminders") or []
        if not reminders:
            return 0

        # Check auth ONCE per note, up front. If it's unavailable (revoked token,
        # headless), keep every reminder as pending and log a single actionable
        # line — rather than attempting each event, failing, and spamming the log.
        if self._get_service() is None:
            note_title0 = (enrichment.get("title") or "").strip()
            self._write_pending_to_memos(list(reminders), note_name, note_title0)
            log.warning(
                f"  📅 calendar unavailable (auth) — kept {len(reminders)} "
                f"reminder(s) for {note_name} as pending. Re-authorize with "
                f"'hg_agent calendar-auth --headless' and they'll sync on the "
                f"next enrichment.")
            return 0

        note_title = (enrichment.get("title") or "").strip()
        note_summary = (enrichment.get("summary") or "").strip()

        pending = []
        created = []
        today = datetime.now(timezone.utc).date()

        for r in reminders:
            item_title = (r.get("title") or "").strip()
            dt_hint = (r.get("datetime_hint") or "").strip()
            description = (r.get("description") or "").strip()

            if not item_title:
                continue

            # ── Step 1: try relative-date resolver first ───────────────────
            resolved_date, time_hm = resolve_relative_date(dt_hint, reference=today)

            if resolved_date is not None:
                # We resolved a relative date. time_hm may or may not be present.
                if time_hm:
                    # Full datetime: create a timed event
                    try:
                        tz_obj = self._tz_obj()
                        naive = datetime(
                            resolved_date.year, resolved_date.month, resolved_date.day,
                            time_hm[0], time_hm[1]
                        )
                        dt = naive.replace(tzinfo=tz_obj)
                        self._guard_and_create(
                            item_title, dt, description,
                            note_title, note_summary, note_name,
                            created, pending, all_day=False
                        )
                    except Exception as e:
                        log.warning(f"  📅 calendar: could not build datetime for '{item_title}': {e}")
                        pending.append(r)
                else:
                    # Date-only → all-day event (no time specified in note)
                    log.info(
                        f"  📅 calendar: '{item_title}' has date but no time — "
                        f"creating all-day event on {resolved_date}"
                    )
                    event_id = self._create_all_day_event(
                        title=item_title,
                        event_date=resolved_date,
                        description=self._build_description(
                            note_title, note_summary, description, note_name),
                    )
                    if event_id:
                        created.append(item_title)
                        log.info(
                            f"  📅 calendar: created all-day event '{item_title}' "
                            f"on {resolved_date} (id={event_id})"
                        )
                continue

            # ── Step 2: fall through to absolute datetime parsing ──────────
            if not dt_hint:
                pending.append(r)
                log.info(
                    f"  📅 calendar: no datetime for reminder "
                    f"'{item_title}' — added to pending"
                )
                continue

            dt = self._parse_dt(dt_hint)
            if dt is None:
                pending.append(r)
                log.info(
                    f"  📅 calendar: unparseable datetime '{dt_hint}' for "
                    f"'{item_title}' — added to pending"
                )
                continue

            self._guard_and_create(
                item_title, dt, description,
                note_title, note_summary, note_name,
                created, pending, all_day=False
            )

        if pending:
            self._write_pending_to_memos(pending, note_name, note_title)

        if created:
            log.info(
                f"  📅 calendar: {len(created)} event(s) created for {note_name}"
            )
        return len(created)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _guard_and_create(self, item_title, dt, description,
                           note_title, note_summary, note_name,
                           created, pending, all_day=False):
        """Lookahead guard + event creation, appending to created or pending."""
        if self._lookahead > 0:
            now_utc = datetime.now(timezone.utc)
            dt_utc = dt.astimezone(timezone.utc) if hasattr(dt, "tzinfo") else dt
            if (dt_utc - now_utc).days > self._lookahead:
                log.info(
                    f"  📅 calendar: reminder '{item_title}' is "
                    f"{(dt_utc - now_utc).days}d away (> lookahead "
                    f"{self._lookahead}d) — skipped"
                )
                return

        desc = self._build_description(note_title, note_summary, description, note_name)
        event = self._build_event(title=item_title, start_dt=dt, description=desc)
        event_id = self._create_event(event)
        if event_id:
            created.append(item_title)
            log.info(
                f"  📅 calendar: created event '{item_title}' "
                f"at {dt.isoformat()} (id={event_id})"
            )

    def _build_description(self, note_title, note_summary, context, note_name) -> str:
        if not self._add_desc:
            return ""
        parts = []
        if note_title:
            parts.append(f"Note: {note_title}")
        if note_summary:
            parts.append(f"Summary: {note_summary}")
        if context:
            parts.append(f"Context: {context}")
        parts.append(f"Source: {note_name}")
        return "\n".join(parts)

    def _tz_obj(self):
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(self._tz)
        except Exception:
            return timezone(timedelta(hours=5, minutes=30))

    def _trip_auth_broken(self, token_path):
        """Mark auth as broken so we stop retrying every note until the token
        file changes (i.e. the user re-runs calendar-auth). Recovery needs no
        restart — _get_service re-arms when the file's mtime moves."""
        self._auth_broken = True
        try:
            self._auth_broken_mtime = (token_path.stat().st_mtime
                                       if token_path and token_path.exists() else 0)
        except Exception:
            self._auth_broken_mtime = 0

    def _get_service(self):
        """Lazy-initialise and return the Google Calendar API service object,
        refreshing credentials if they have expired."""
        with self._svc_lock:
            if self._svc is not None:
                return self._svc

            # Circuit breaker: once auth is known-broken (revoked token, no
            # browser), stop re-attempting on every single note. Retrying the
            # full OAuth flow per enrichment spams the log, wastes seconds, and
            # cannot succeed until the token file actually changes. We re-arm
            # only when the token file's mtime changes (i.e. the user ran
            # calendar-auth), so recovery is automatic without a restart.
            token_path = Path(self._token_path) if self._token_path else None
            if getattr(self, "_auth_broken", False):
                try:
                    mtime = token_path.stat().st_mtime if token_path and token_path.exists() else 0
                except Exception:
                    mtime = 0
                if mtime == getattr(self, "_auth_broken_mtime", 0):
                    return None                     # still broken, don't retry
                # token changed → give it another chance
                self._auth_broken = False

            secret_path = Path(self._secret_path) if self._secret_path else None

            creds = None
            if token_path and token_path.exists():
                try:
                    creds = Credentials.from_authorized_user_file(
                        str(token_path), _SCOPES
                    )
                except Exception as e:
                    log.error(f"calendar: failed to load token from {token_path}: {e}")

            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                    self._save_token(creds, token_path)
                    log.info("calendar: token auto-refreshed successfully")
                except Exception as e:
                    # invalid_grant means Google REVOKED the refresh token — the
                    # code can't recover from that, only a fresh authorization
                    # can. The #1 cause is an OAuth consent screen left in
                    # "Testing" mode, where Google expires refresh tokens after
                    # 7 days. Give an actionable message rather than a bare error.
                    msg = str(e).lower()
                    if "invalid_grant" in msg or "expired or revoked" in msg:
                        log.error(
                            "calendar: refresh token was revoked by Google "
                            "(invalid_grant). Auto-refresh cannot recover this. "
                            "FIX: (1) in Google Cloud Console, set the OAuth "
                            "consent screen to 'In production' (Testing mode "
                            "expires refresh tokens after 7 days — the usual "
                            "cause); then (2) re-authorize once: "
                            "hg_agent calendar-auth --headless")
                    else:
                        log.error(f"calendar: token refresh failed: {e}")
                    creds = None
                    self._trip_auth_broken(token_path)

            if not creds or not creds.valid:
                # An interactive browser flow cannot run in Termux / headless.
                # Detect that and fail fast with a clear instruction instead of
                # throwing "could not locate runnable browser" once per note.
                import shutil as _sh
                headless = not (os.environ.get("DISPLAY")
                                or _sh.which("xdg-open")
                                or _sh.which("google-chrome")
                                or _sh.which("chromium"))
                if not creds and secret_path and secret_path.exists() and not headless:
                    log.info("calendar: launching OAuth flow (check your terminal)…")
                    try:
                        flow = InstalledAppFlow.from_client_secrets_file(
                            str(secret_path), _SCOPES
                        )
                        creds = flow.run_local_server(port=0)
                        self._save_token(creds, token_path)
                    except Exception as e:
                        log.error(f"calendar: OAuth flow failed: {e}")
                        self._trip_auth_broken(token_path)
                        return None
                else:
                    if headless and secret_path and secret_path.exists():
                        log.error(
                            "calendar: credentials invalid and no browser is "
                            "available in this environment. Re-authorize with: "
                            "hg_agent calendar-auth --headless  (this prints a "
                            "URL to open on another device). Calendar events are "
                            "paused until then; reminders are kept as pending.")
                    else:
                        log.error(
                            "calendar: no valid credentials. Set "
                            "calendar_credentials_file and calendar_client_secret_file "
                            "in config, then run: hg_agent calendar-auth --headless")
                    self._trip_auth_broken(token_path)
                    return None

            try:
                self._svc = _gcal_build("calendar", "v3", credentials=creds,
                                        cache_discovery=False)
            except Exception as e:
                log.error(f"calendar: failed to build service: {e}")
                return None

            return self._svc

    def _save_token(self, creds, path: Path | None):
        if not path:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(creds.to_json())
        except Exception as e:
            log.warning(f"calendar: could not save refreshed token: {e}")

    def _parse_dt(self, hint: str) -> datetime | None:
        """Parse an ISO 8601 string or a human-readable date string.
        Returns a timezone-aware datetime in UTC, or None if unparseable."""
        if not hint:
            return None
        try:
            dt = datetime.fromisoformat(hint)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=self._tz_obj())
            return dt.astimezone(timezone.utc)
        except ValueError:
            pass

        if _DATEUTIL:
            try:
                dt = _du_parser.parse(hint, dayfirst=False)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=self._tz_obj())
                return dt.astimezone(timezone.utc)
            except Exception:
                pass

        log.debug(f"calendar: could not parse datetime hint: {hint!r}")
        return None

    def _build_event(self, title: str, start_dt: datetime,
                     description: str = "") -> dict:
        end_dt = start_dt + timedelta(minutes=self._dur)
        fmt = "%Y-%m-%dT%H:%M:%S"
        tz_obj = self._tz_obj()
        start_local = start_dt.astimezone(tz_obj)
        end_local = end_dt.astimezone(tz_obj)

        event: dict = {
            "summary": title,
            "start": {
                "dateTime": start_local.strftime(fmt),
                "timeZone": self._tz,
            },
            "end": {
                "dateTime": end_local.strftime(fmt),
                "timeZone": self._tz,
            },
            "reminders": {
                "useDefault": False,
                "overrides": [
                    {"method": "popup", "minutes": 10},
                ],
            },
        }
        if description:
            event["description"] = description
        return event

    def _create_all_day_event(self, title: str, event_date: date,
                               description: str = "") -> str | None:
        """Create an all-day event (no time component). Used when a note has
        a date but no time, e.g. 'call John tomorrow'."""
        svc = self._get_service()
        if svc is None:
            return None
        date_str = event_date.isoformat()
        body: dict = {
            "summary": title,
            "start": {"date": date_str},
            "end": {"date": date_str},
            "reminders": {
                "useDefault": False,
                "overrides": [{"method": "popup", "minutes": 30}],
            },
        }
        if description:
            body["description"] = description
        try:
            result = svc.events().insert(calendarId=self._cal_id, body=body).execute()
            return result.get("id")
        except Exception as e:
            log.error(f"calendar: all-day event insert failed: {e}")
            return None

    def delete_event(self, event_id: str) -> bool:
        """Delete a calendar event by id. Returns True if it's gone.

        An event the user already removed by hand returns 404/410 from Google —
        that's success from our point of view (the desired end state holds), so
        we treat it as True rather than retrying forever. Any other failure
        (auth expired, offline) returns False so the caller can retry later
        instead of silently dropping the request.
        """
        if not (event_id or "").strip():
            return False
        svc = self._get_service()
        if svc is None:
            return False
        try:
            svc.events().delete(calendarId=self._cal_id, eventId=event_id).execute()
            log.info(f"calendar: deleted event {event_id}")
            return True
        except Exception as e:
            msg = str(e)
            if "404" in msg or "410" in msg or "notFound" in msg or "deleted" in msg.lower():
                log.info(f"calendar: event {event_id} already gone — treating as deleted")
                return True
            log.error(f"calendar: event delete failed: {e}")
            return False

    def _create_event(self, event: dict) -> str | None:
        """Insert a timed event into the calendar. Returns the event id or None."""
        svc = self._get_service()
        if svc is None:
            return None
        try:
            result = (
                svc.events()
                .insert(calendarId=self._cal_id, body=event)
                .execute()
            )
            return result.get("id")
        except Exception as e:
            log.error(f"calendar: event insert failed: {e}")
            return None

    def _write_pending_to_memos(self, pending: list, note_name: str,
                                 note_title: str):
        """Time-ambiguous reminders (a date the resolver couldn't pin down) can't
        become a calendar event yet. On the Quicksilver backend these rows stay
        in the reminders table with their original datetime_hint and no
        calendar_event_id, so they're naturally retried on the next enrichment
        once the user clarifies the date in the note. Nothing to write elsewhere
        — we just log so the user knows why an event didn't appear.

        (The name is kept for backwards compatibility with older call sites.)
        """
        if not pending:
            return
        titles = ", ".join((r.get("title") or "reminder") for r in pending)
        log.info(
            f"  📅 calendar: {len(pending)} reminder(s) for '{note_title or note_name}' "
            f"have no clear date/time ({titles}) — left pending; add an explicit "
            f"date to the note (e.g. 2026-07-15 10:00) to create the event"
        )


# ── One-time authorization helper (used by `app.py calendar-auth`) ────────────
def authorize(cfg: dict, headless: bool = False) -> bool:
    """Run the one-time OAuth flow and write token.json.

    On a machine WITH a browser, pass headless=False (default): a browser opens,
    you approve, the token is saved automatically.

    On a HEADLESS device (Termux/Android/SSH) with no browser, pass
    headless=True: a URL is printed, you open it on ANY device, approve, and
    paste the resulting authorization code back here.

    Returns True on success.
    """
    if not _GCAL_AVAILABLE:
        log.error("calendar: google-auth-oauthlib not installed. Run: "
                  "pip install --break-system-packages google-auth "
                  "google-auth-oauthlib google-auth-httplib2 "
                  "google-api-python-client python-dateutil")
        return False

    secret_path = cfg.get("calendar_client_secret_file", "")
    token_path = cfg.get("calendar_credentials_file", "")
    if not secret_path or not Path(secret_path).exists():
        log.error(f"calendar: client secret file not found at "
                  f"'{secret_path}'. Download an OAuth 'Desktop app' "
                  f"client_secret JSON from Google Cloud Console and set "
                  f"calendar_client_secret_file to its path.")
        return False
    if not token_path:
        log.error("calendar: set calendar_credentials_file (where token.json "
                  "will be written) in config.")
        return False

    try:
        flow = InstalledAppFlow.from_client_secrets_file(secret_path, _SCOPES)
        if headless:
            # Google DISABLED the old out-of-band (oob) copy-paste flow
            # (urn:ietf:wg:oauth:2.0:oob) in 2023 — using it now returns
            # "400 invalid_request". The supported replacement is a loopback
            # redirect: we run a tiny local server on 127.0.0.1, print the auth
            # URL, and capture the redirect automatically. On Termux/Android this
            # works because the phone HAS a browser and can reach its own
            # localhost — open the printed URL, approve, and the redirect lands
            # back on the local server. No copy-paste, no deprecated oob.
            #
            # open_browser=False: we don't assume a browser can be launched from
            # the daemon; we print the URL for the user to open manually. The
            # local server still catches the redirect on whatever port it binds.
            print("\n── Google Calendar authorization (headless / Termux) ──")
            print("A local server will start on 127.0.0.1 to catch the redirect.")
            print("When the URL is printed below, open it in your phone's browser,")
            print("approve access, and it will redirect back automatically.\n")
            try:
                creds = flow.run_local_server(
                    host="127.0.0.1", port=0, open_browser=False,
                    authorization_prompt_message=(
                        "Open this URL in your browser to authorize:\n\n{url}\n"),
                    success_message=(
                        "Authorization complete — you can close this tab and "
                        "return to the terminal."))
            except Exception as loop_err:
                # If the loopback server can't bind or the redirect can't be
                # captured (unusual on Termux, but possible over plain SSH with no
                # local browser), fall back to manual code entry against a
                # loopback redirect — still NOT the dead oob endpoint. The user
                # opens the URL, approves, and pastes the code from the redirected
                # URL's ?code= parameter.
                log.warning(f"calendar: loopback capture failed ({loop_err}); "
                            f"falling back to manual code entry")
                flow2 = InstalledAppFlow.from_client_secrets_file(
                    secret_path, _SCOPES)
                flow2.redirect_uri = "http://127.0.0.1:1"  # loopback, not oob
                auth_url, _ = flow2.authorization_url(prompt="consent")
                print("\nOpen this URL, approve, then copy the FULL redirected")
                print("URL (it will start with http://127.0.0.1:1/?code=…):\n")
                print(f"   {auth_url}\n")
                redirected = input("Paste the full redirected URL (or just the "
                                   "code= value): ").strip()
                # Accept either the whole URL or a bare code.
                import urllib.parse as _up
                if "code=" in redirected:
                    code = _up.parse_qs(
                        _up.urlparse(redirected).query).get("code", [""])[0]
                else:
                    code = redirected
                flow2.fetch_token(code=code)
                creds = flow2.credentials
        else:
            creds = flow.run_local_server(port=0)

        p = Path(token_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(creds.to_json())
        log.info(f"calendar: authorization successful — token saved to {p}")
        print(f"\n✅ Saved token to {p}")
        print("Set \"calendar_enabled\": true in config and restart the agent.")
        return True
    except Exception as e:
        log.error(f"calendar: authorization failed: {e}")
        print(f"\n❌ Authorization failed: {e}")
        return False
