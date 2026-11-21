"""followups.py — task follow-through: stale tasks → one proposed move each, learned from your answers.

Pure functions over the Quicksilver SQLite database. The code decides WHICH tasks need a
decision and WHAT to propose (rules below); the model only drafts the one-line first step for
long tasks, and a failed or unusable draft falls back to a plain reschedule. Nothing is changed
until the user taps: a proposal is a row in `followups` with status 'pending'.

Moves
  reschedule  plan it for the next working day (becomes a reminder → all-day calendar event)
  today       plan it for today
  first_step  plan it for today with a model-drafted first step
  drop        dismiss the task (the app remembers, so it is not re-extracted)

Feedback (see `learning`): a move you keep dismissing stops being proposed; a stretch of
unanswered "not now" taps pauses the whole feature for a week.
"""
from __future__ import annotations
import json
import re
from datetime import datetime, timedelta, timezone

FOLLOWUPS_TABLE = """
CREATE TABLE IF NOT EXISTS followups (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    note_id     TEXT NOT NULL,
    note_title  TEXT NOT NULL DEFAULT '',
    text        TEXT NOT NULL,
    key         TEXT NOT NULL,
    move        TEXT NOT NULL CHECK(move IN ('reschedule','today','first_step','drop')),
    move_date   TEXT,
    step        TEXT NOT NULL DEFAULT '',
    reason      TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending','accepted','done','dropped','dismissed','expired')),
    reminder_id TEXT,
    cal_synced  INTEGER NOT NULL DEFAULT 0,
    cal_tries   INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    resolved_at TEXT
)"""
FOLLOWUPS_INDEX = "CREATE INDEX IF NOT EXISTS idx_followups_status ON followups(status, key)"

MOVES = ("reschedule", "today", "first_step", "drop")
PLANNING = ("reschedule", "today", "first_step")

MIN_AGE_DAYS = 7            # a task must be at least this old (note age, like the "over a week" tile)
MAX_PENDING = 3             # a decision card, not a list
DISMISS_COOLDOWN_DAYS = 7   # "not now" hides that task this long; twice and it is muted for good
MOVE_WINDOW = 8             # a move's recent answers considered for the feedback gate
MOVE_MIN_SAMPLES = 5
MOVE_MIN_ACCEPT = 0.2
MOVE_MEMORY_DAYS = 45       # a gate older than this lapses, so a disabled move gets another chance
FATIGUE_RUN = 6             # this many "not now" in a row pauses the feature
FATIGUE_PAUSE_DAYS = 7

DEADLINE_RE = re.compile(
    r"\b(today|tonight|tomorrow|asap|urgent|deadline|due|expires?|last date|"
    r"by (?:mon|tue|wed|thu|fri|sat|sun)\w*|by \d+\w*|before \d+\w*)\b", re.I)


def ensure(db) -> None:
    db.execute(FOLLOWUPS_TABLE)
    db.execute(FOLLOWUPS_INDEX)
    db.commit()


def norm(text: str) -> str:
    """Same normalisation the agent uses to remember dismissed tasks."""
    return " ".join((text or "").lower().split()).rstrip(".!?")


def key_of(note_id: str, text: str) -> str:
    # Task rows are deleted and re-created on every re-enrichment, so ids are not stable;
    # the note plus the normalised text is.
    return f"{note_id}|{norm(text)}"


def _parse(ts: str):
    try:
        return datetime.fromisoformat((ts or "").replace("Z", "+00:00"))
    except Exception:
        return None


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def fmt_day(d) -> str:
    return d.strftime("%a %d %b").replace(" 0", " ")


def next_workday(d):
    d = d + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def plan_date(move: str, now: datetime):
    """The date a planning move proposes, in the phone's local time."""
    today = now.date()
    if move == "reschedule":
        return next_workday(today)
    # "today" late in the evening means tomorrow — nobody starts a task at 11pm.
    return today if now.hour < 19 else next_workday(today)


# ── learning from the answers ────────────────────────────────────────────────
def learning(db, now: datetime) -> dict:
    """Which moves to stop proposing, and whether the whole feature is paused."""
    rows = db.execute(
        "SELECT move, status, resolved_at FROM followups "
        "WHERE status IN ('accepted','done','dropped','dismissed') AND resolved_at IS NOT NULL "
        "ORDER BY resolved_at DESC, id DESC LIMIT 80").fetchall()
    disabled = []
    horizon = _iso(now - timedelta(days=MOVE_MEMORY_DAYS))
    for mv in MOVES:
        recent = [r for r in rows if r["move"] == mv][:MOVE_WINDOW]
        if len(recent) < MOVE_MIN_SAMPLES or recent[0]["resolved_at"] < horizon:
            continue
        good = sum(1 for r in recent if r["status"] in ("accepted", "done"))
        if good / len(recent) < MOVE_MIN_ACCEPT:
            disabled.append(mv)
    paused_until = None
    last = rows[:FATIGUE_RUN]
    if len(last) == FATIGUE_RUN and all(r["status"] == "dismissed" for r in last):
        until = (_parse(last[0]["resolved_at"]) or now) + timedelta(days=FATIGUE_PAUSE_DAYS)
        if until > now:
            paused_until = _iso(until)
    return {"disabled": disabled, "paused_until": paused_until}


# ── choosing what to raise ───────────────────────────────────────────────────
def _history(db) -> dict:
    out: dict[str, list] = {}
    for r in db.execute("SELECT key, move, move_date, status, resolved_at FROM followups"):
        out.setdefault(r["key"], []).append(r)
    return out


def select(db, now: datetime, min_age: int = MIN_AGE_DAYS, limit: int = MAX_PENDING) -> list[dict]:
    """Open tasks that need a decision, most pressing first, minus anything already
    pending, already planned, snoozed or muted."""
    cutoff = _iso(now - timedelta(days=min_age))
    rows = db.execute(
        "SELECT a.text, a.note_id, n.created_at, "
        "(SELECT title FROM enrichments WHERE note_id=n.id ORDER BY enriched_at DESC, rowid DESC LIMIT 1) AS t "
        "FROM action_items a JOIN notes n ON n.id=a.note_id "
        "WHERE a.done=0 AND COALESCE(a.dismissed,0)=0 AND n.hidden=0 AND n.archived=0 "
        "AND n.protected=0 AND n.created_at < ?", (cutoff,)).fetchall()
    hist = _history(db)
    today = now.date()
    seen, out = set(), []
    for r in rows:
        text = (r["text"] or "").strip()
        if len(text) < 3:
            continue
        k = key_of(r["note_id"], text)
        if k in seen:
            continue
        seen.add(k)
        h = hist.get(k, [])
        if any(x["status"] == "pending" for x in h):
            continue
        snooze = _iso(now - timedelta(days=DISMISS_COOLDOWN_DAYS))
        dismissals = [x for x in h if x["status"] == "dismissed"]
        if len(dismissals) >= 2 or any((x["resolved_at"] or "") >= snooze for x in dismissals):
            continue
        planned = [x for x in h if x["status"] == "accepted" and x["move"] in PLANNING and x["move_date"]]
        slipped, slips = False, 0
        for x in planned:
            if x["move_date"] >= (today - timedelta(days=1)).isoformat():
                slipped = None                       # still inside its planned day (plus a day's grace)
                break
            slips += 1
        if slipped is None:
            continue
        slipped = slips > 0
        created = _parse(r["created_at"]) or now
        age = max(0, (now - created).days)
        c = {"note_id": r["note_id"], "text": text, "note_title": (r["t"] or "").strip(), "key": k,
             "age": age, "slipped": slipped, "slips": slips,
             "planned_for": max((x["move_date"] for x in planned), default=""),
             "deadline": bool(DEADLINE_RE.search(text)), "words": len(text.split())}
        c["score"] = age + (20 if slipped else 0) + (15 if c["deadline"] else 0)
        out.append(c)
    out.sort(key=lambda c: (-c["score"], c["text"]))
    return out[:limit]


def preference(c: dict) -> list[str]:
    """The moves to try for this task, best first (the caller filters out disabled ones)."""
    if c["slips"] >= 2:
        return ["drop", "reschedule"]            # planned twice and still open: say so honestly
    if c["slipped"]:
        return ["reschedule", "first_step", "drop"]
    if c["age"] >= 30:
        return ["drop", "reschedule", "today"]
    if c["deadline"]:
        return ["today", "reschedule", "first_step"]
    if c["words"] >= 9:
        return ["first_step", "reschedule", "today"]
    return ["reschedule", "today", "first_step"]


def reason_for(c: dict) -> str:
    if c["slipped"] and c["planned_for"]:
        try:
            when = fmt_day(datetime.fromisoformat(c["planned_for"]).date())
        except Exception:
            when = c["planned_for"]
        return f"Planned for {when} — still open"
    r = f"Open {c['age']} days"
    return r + (" · mentions a deadline" if c["deadline"] else "")


# ── the one model call ───────────────────────────────────────────────────────
def step_prompt(task: str, note_title: str = "") -> str:
    ctx = f"From the note: {note_title}\n" if note_title else ""
    return (f"Task: {task}\n{ctx}"
            "Write ONE concrete first step for this task that takes under 10 minutes, as a short "
            "imperative phrase of at most 12 words, in the same language as the task. Use only what the "
            "task says; do not invent names, dates or numbers. Reply with the step only, no quotes, "
            "no numbering.")


_LEAD = re.compile(r"^\s*(?:[-*•>\d]+[.)]?\s*|(?:first\s+step|step\s*\d*)\s*[:\-–]\s*)+", re.I)


def clean_step(raw: str, task: str) -> str:
    """The model's reply as a single short step, or '' when it isn't usable."""
    line = next((l.strip() for l in (raw or "").splitlines() if l.strip()), "")
    line = _LEAD.sub("", line).strip().strip("\"'“”‘’`").strip()
    line = line.rstrip(" .") if len(line) > 3 else line
    if not line or len(line) > 100 or not (2 <= len(line.split()) <= 16):
        return ""
    if norm(line) == norm(task) or re.search(r"\b(as an ai|i cannot|i can't|sorry)\b", line, re.I):
        return ""
    return line[0].upper() + line[1:]


# ── housekeeping ─────────────────────────────────────────────────────────────
def _task_open(db, note_id: str, nkey: str) -> bool:
    for r in db.execute("SELECT text FROM action_items WHERE note_id=? AND done=0 "
                        "AND COALESCE(dismissed,0)=0", (note_id,)):
        if norm(r["text"]) == nkey:
            return True
    return False


def expire(db, now: datetime) -> int:
    """Close pending proposals whose task has since been finished, dropped or removed."""
    n = 0
    for r in db.execute("SELECT id, note_id, key FROM followups WHERE status='pending'").fetchall():
        nkey = r["key"].split("|", 1)[1] if "|" in r["key"] else ""
        if not _task_open(db, r["note_id"], nkey):
            db.execute("UPDATE followups SET status='expired', resolved_at=? WHERE id=?",
                       (_iso(now), r["id"]))
            n += 1
    if n:
        db.commit()
    return n


def _set_setting(db, key: str, value: str) -> None:
    db.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET "
               "value=excluded.value, updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')", (key, value))


def run(db, now: datetime, cfg: dict | None = None, draft=None, force: bool = False) -> int:
    """Propose up to MAX_PENDING decisions. `draft(prompt) -> str` is the model, or None to skip it.
    Returns how many proposals were added."""
    cfg = cfg or {}
    ensure(db)
    expire(db, now)
    learn = learning(db, now)
    _set_setting(db, "followups_learning", json.dumps(learn))
    db.commit()
    if learn["paused_until"] and not force:
        return 0
    pending = db.execute("SELECT COUNT(*) FROM followups WHERE status='pending'").fetchone()[0]
    cap = int(cfg.get("followups_max", MAX_PENDING))
    need = cap - pending
    if need <= 0:
        return 0
    added = 0
    for c in select(db, now, int(cfg.get("followups_min_age_days", MIN_AGE_DAYS)), need):
        chosen, step = None, ""
        for mv in preference(c):
            if mv in learn["disabled"]:
                continue
            if mv == "first_step":
                if draft is None:
                    continue
                try:
                    step = clean_step(draft(step_prompt(c["text"], c["note_title"])), c["text"])
                except Exception:
                    step = ""
                if not step:
                    continue
            chosen = mv
            break
        if not chosen:
            continue
        mdate = plan_date(chosen, now).isoformat() if chosen in PLANNING else None
        db.execute(
            "INSERT INTO followups (note_id, note_title, text, key, move, move_date, step, reason) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (c["note_id"], c["note_title"][:120], c["text"][:300], c["key"], chosen, mdate,
             step if chosen == "first_step" else "", reason_for(c)))
        added += 1
    db.commit()
    return added
