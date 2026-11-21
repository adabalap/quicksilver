"""Medha's notification stream, stored and read.

WHAT THIS IS FOR

Medha can see every notification the phone shows and has no memory to reason
about it. We have the corpus, the embeddings and the correlation, and could
not see the stream at all. So Medha was guessing -- a `kind` from the package
name, a `dueAt` scraped out of an email subject -- and those guesses arrived
on the home screen as facts. One real evening: four of five agenda rows were
emails, one of them "Re: Delayed employees for 11:30 on 17/11/2026", read as
an obligation due tomorrow from a date already past.

Medha v0.20.0 stops interpreting and reports instead. `GET /captures` hands
over observations with ids; this file keeps them and decides which ones are
worth anybody's attention.

Medha is sensors, model and surface. We are memory and judgement.

WHAT IS AND IS NOT STORED HERE

A capture is notification title and text. That is personal material, and the
honest version of the sentence is that this file writes it to the agent's
database and keeps it for `RETAIN_DAYS`.

Two things bound it, and both are enforced rather than intended:

  - Medha applies its own NotificationFilter first, so anything that looks
    like a one-time passcode never reaches this route at all. We do not
    re-derive that rule; we rely on it, and `is_noise` below only removes
    what is useless rather than what is sensitive.
  - Nothing is fetched unless the feature is switched on AND Medha has been
    granted the capability. Revoking either stops it; revoking the Medha
    capability also deletes Medha's copy.

Two rules, stated once, for anything derived from a capture:

  - every date is INFERRED. We are reading prose a third-party app wrote,
    which is precisely the signal that failed on the home screen. Such
    candidates carry `due_declared=False`.
  - nothing is COMMITTED. A notification is an app mentioning something,
    not the user deciding it. Such candidates carry `committed=False`.

Together those mean a capture-derived row reaches Medha's agenda only when
some third party declared a real time for it, and never on the strength of
its own words. See medha_brief._obligation.

Pure apart from the sqlite handle: no HTTP, no clock of its own.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta

RETAIN_DAYS = 21               # a little past Medha's 14, so a gap in polling cannot lose a week
MAX_ROWS = 20000               # a busy phone; whichever bound bites first wins
MAX_TITLE, MAX_TEXT = 300, 1200

CAPTURES = """
CREATE TABLE IF NOT EXISTS captures (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    medha_id     INTEGER NOT NULL UNIQUE,         -- Medha's row id; the dedupe key across re-reads
    at           INTEGER NOT NULL,                -- epoch ms, when MEDHA saw it
    pkg          TEXT NOT NULL DEFAULT '',
    label        TEXT NOT NULL DEFAULT '',        -- the app as a person names it: "Outlook"
    category     TEXT NOT NULL DEFAULT '',        -- the notification's own category, or ''
    title        TEXT NOT NULL DEFAULT '',
    text         TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
)"""


def ensure(db) -> None:
    """Create the table. UNIQUE on medha_id is the whole dedupe strategy.

    The cursor is saved only after a batch is committed, so a crash between
    the two replays a page -- which is the safe direction and the reason this
    constraint has to be in the schema rather than in a check above it.
    """
    db.execute(CAPTURES)
    db.execute("CREATE INDEX IF NOT EXISTS idx_captures_at ON captures(at)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_captures_pkg ON captures(pkg, at)")


# ───────────────────────────── what is worth keeping

# Notifications that exist to tell you an app is running. They pass Medha's
# filter because they are not junk and not secrets; they are simply not
# observations about your life.
_NOISE_PKG = re.compile(
    r"(?:^|\.)(?:android|systemui|providers\.downloads|inputmethod|"
    r"googlequicksearchbox|gms|packageinstaller|bluetooth|nfc|"
    r"deskclock|intelliservice|soundpicker|wallpaper)(?:$|\.)", re.I)

# Device-status titles, matched against the WHOLE title rather than its start.
#
# The first version matched a prefix on single words -- `battery`, `usb`,
# `media`, `casting` -- and so dropped "Battery project kickoff", a real
# meeting, as a charging notice. A keyword that generic cannot carry the
# decision, so these are the specific phrases Android and its system apps
# actually post, anchored at both ends with room for a short tail
# ("Charging rapidly", "Battery saver is on").
#
# Narrow on purpose. A capture wrongly kept costs a row in a table nobody
# reads; a capture wrongly dropped is a meeting we never hear about, and
# this source exists precisely because the interesting half of the stream
# reaches no calendar provider.
_NOISE_TITLE = re.compile(
    r"^\s*(?:"
    r"screenshots?(?:\s+(?:saved|captured|taken))?"
    r"|screen\s+recording(?:\s+\w+)?"
    r"|usb\s+(?:debugging|file\s+transfer|connected|tethering)\w*"
    r"|charging(?:\s+(?:rapidly|slowly|paused|this\s+device(?:\s+via\s+usb)?))?"
    r"|battery\s+(?:low|full|saver|charged|optimi[sz]ation)\w*(?:\s+\w+){0,2}"
    # No `.*` tails anywhere. `scanning\b.*` was the generic-keyword mistake
    # in a new costume: it dropped "Scanning team standup". Every arm below
    # is bounded, and because the whole pattern is anchored at both ends, a
    # bare word matches only that word as the entire title.
    r"|downloading(?:\s*[.…]+)?|download\s+(?:complete|finished|failed)"
    r"|uploading(?:\s*[.…]+)?|upload\s+(?:complete|finished|failed)"
    r"|syncing(?:\s*[.…]+)?|sync(?:hronisation)?\s+(?:complete|finished|failed)"
    r"|backing\s+up(?:\s+your\s+\w+)?|backup\s+(?:complete|finished|failed)"
    r"|updating(?:\s*[.…]+)?|(?:system\s+)?update\s+available"
    r"|scanning(?:\s*[.…]+|\s+for\s+\w+(?:\s+\w+)?)?"
    r"|now\s+playing(?:\s+\w+){0,3}"
    r"|casting\s+(?:screen|your\s+screen)\w*"
    r"|(?:mobile\s+)?hotspot\s+is\s+(?:on|off|active)"
    r"|connected\s+to\s+wi-?fi\w*"
    r"|(?:notifications?\s+)?(?:paused|muted|silenced)"
    r")\s*$", re.I)

# An app saying "you have 3 unread" is a count, not a thing to do.
_COUNT_ONLY = re.compile(
    r"^\s*\d+\s+(?:new\s+)?(?:messages?|unread|notifications?|updates?|emails?|chats?)\s*$", re.I)


def is_noise(pkg: str, label: str, category: str, title: str, text: str) -> bool:
    """True for a capture that tells us nothing about the user's life.

    This removes the USELESS, not the sensitive -- Medha's NotificationFilter
    has already dropped anything passcode-shaped before it reaches us, and
    second-guessing that rule from here would mean maintaining two copies of
    it that can disagree.

    Deliberately narrow. A capture wrongly kept costs a row in a table
    nobody reads; a capture wrongly dropped is a meeting we never hear about,
    and this source exists precisely because the interesting half of the
    stream is the half that reaches no calendar provider.
    """
    p, t, x = (pkg or ""), (title or "").strip(), (text or "").strip()
    if not t and not x:
        return True
    if _NOISE_PKG.search(p):
        return True
    if (category or "").strip().lower() in ("progress", "service", "transport", "sys"):
        return True
    if _NOISE_TITLE.match(t):
        return True
    if _COUNT_ONLY.match(x) and not t:
        return True
    return False


def fingerprint(pkg: str, title: str, text: str) -> str:
    """Identity for the same notification posted again.

    Medha's own ids are per-POSTING, so an app that reposts a countdown
    ("starts in 10 minutes", then "in 9 minutes") produces a new capture every
    time. Keyed on package and title with the text's shape rather than its
    value, so a ticking countdown collapses to one thing while two genuinely
    different messages from one app stay apart.
    """
    t = " ".join((title or "").lower().split())
    # Digits out of the text: a countdown differs only in its numbers.
    x = re.sub(r"\d+", "#", " ".join((text or "").lower().split()))[:160]
    return hashlib.sha1(f"{pkg}|{t}|{x}".encode()).hexdigest()[:16]


# ───────────────────────────── storage

def store(db, rows) -> dict:
    """Insert what we have not seen. Returns counts; never raises on one bad row.

    `INSERT OR IGNORE` on medha_id rather than a pre-select: a replayed page
    after a crash is the expected case, not an exception, and two round trips
    per row for something that happens on every poll is not worth it.
    """
    # Materialised once. `rows` arrives from a JSON body today, but a caller
    # passing a generator would have it consumed by the loop and then counted
    # as zero at the end -- a stats line that says "seen: 0, added: 12".
    rows = list(rows or [])
    added = skipped = noise = 0
    for r in rows:
        if not isinstance(r, dict):
            skipped += 1
            continue
        mid = r.get("id")
        if not isinstance(mid, int) or isinstance(mid, bool) or mid <= 0:
            skipped += 1
            continue
        pkg = str(r.get("source_pkg") or "")[:120]
        label = str(r.get("source_label") or "")[:60]
        cat = str(r.get("category") or "")[:40]
        title = " ".join(str(r.get("title") or "").split())[:MAX_TITLE]
        text = " ".join(str(r.get("text") or "").split())[:MAX_TEXT]
        if is_noise(pkg, label, cat, title, text):
            # Counted, not stored. The cursor still moves past it -- otherwise
            # a phone that posts nothing but progress bars would make every
            # poll re-read the same page forever.
            noise += 1
            continue
        at = r.get("at")
        at = int(at) if isinstance(at, (int, float)) and not isinstance(at, bool) and at > 0 else 0
        try:
            cur = db.execute(
                "INSERT OR IGNORE INTO captures (medha_id, at, pkg, label, category, title, text) "
                "VALUES (?,?,?,?,?,?,?)", (mid, at, pkg, label, cat, title, text))
            added += 1 if cur.rowcount else 0
        except Exception:
            skipped += 1
    return {"added": added, "skipped": skipped, "noise": noise, "seen": len(rows)}


def prune(db, now=None) -> int:
    """Drop what has aged out, then anything over the row ceiling. Oldest first.

    By id, not by `at`: ids are monotonic here and `at` is Medha's clock,
    which is the phone's and can move.
    """
    now = now or datetime.now()
    cut = int((now - timedelta(days=RETAIN_DAYS)).timestamp() * 1000)
    n = db.execute("DELETE FROM captures WHERE at > 0 AND at < ?", (cut,)).rowcount or 0
    count = db.execute("SELECT COUNT(*) AS n FROM captures").fetchone()["n"]
    if count > MAX_ROWS:
        over = count - MAX_ROWS
        n += db.execute(
            "DELETE FROM captures WHERE id IN (SELECT id FROM captures ORDER BY id ASC LIMIT ?)",
            (over,)).rowcount or 0
    return n


def recent(db, limit=200, since_ms=0):
    """Newest first. What the UI and the correlation step read."""
    return db.execute(
        "SELECT id, medha_id, at, pkg, label, category, title, text FROM captures "
        "WHERE at >= ? ORDER BY at DESC, id DESC LIMIT ?", (int(since_ms or 0), int(limit))).fetchall()


# ───────────────────────────── corroboration
#
# WHY CAPTURES DO NOT BECOME BRIEF ROWS
#
# The obvious thing to do with a notification stream is put it on the brief.
# That is exactly the state the triage rule was written to end: 19 items, 17
# undated, a ₹2,50,000 instalment sharing a scroll with a shopping list and
# "Thanq thanq". Pouring the stream in would undo the definition the brief
# just acquired, and every date we could derive from a capture is a guess read
# out of prose a third-party app wrote -- the least reliable signal there is.
#
# So captures are used the other way round: as CORROBORATION for obligations
# we already know about from the user's own notes and bills. That cannot add
# noise, because it never adds a row; it can only change what we say about a
# row that was going to be there anyway.
#
# The case it exists for: a Teams or Outlook notification saying a meeting is
# cancelled, matched to the reminder we are already posting, so the row goes
# out with `state: "cancelled"` instead of sitting on the home screen at the
# cancelled meeting's hour. Medha had to read that off a subject line before,
# which only worked when the cancellation WAS the row.

_CANCELLED = re.compile(
    r"^\s*(?:re:\s*|fwd:\s*)*(?:meeting\s+)?(cancell?ed|declined|withdrawn|"
    r"no longer|called off)\b", re.I)

# "Paid", "payment received": a bill that has been settled.
_SETTLED = re.compile(
    r"\b(payment (?:received|successful|confirmed)|paid successfully|"
    r"thank you for your payment|has been (?:paid|settled)|"
    r"successfully paid|debited towards)\b", re.I)

_STOP = {
    "the", "a", "an", "and", "or", "of", "for", "to", "in", "on", "at", "by",
    "with", "your", "you", "is", "are", "was", "has", "have", "been", "this",
    "that", "it", "re", "fwd", "fw", "meeting", "invitation", "invite",
    "reminder", "bill", "payment", "due", "update", "updated", "cancelled",
    "canceled", "notice", "alert", "new", "my",
    # Dates and times carry no identity, and they are the words our own
    # candidate text is full of -- "due Tue 25 Nov" shares nothing with a
    # payment confirmation, so leaving them in made a real match look weak.
    "mon", "tue", "tues", "wed", "thu", "thur", "thurs", "fri", "sat", "sun",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "sept",
    "oct", "nov", "dec", "today", "tomorrow", "yesterday", "am", "pm",
}

# At least this many words in common. A RATIO alone is not identification: a
# candidate titled "Rent" has one token, so any capture mentioning rent scores
# 1.0 against it. Two shared words is the floor, which means a single-word
# title can never be corroborated -- and that is the safe failure, because a
# miss leaves things exactly as they were before captures existed.
MIN_SHARED = 2


def _tokens(s: str) -> set:
    """Words worth matching on. Lowercased, de-punctuated, stop words out.

    Deliberately crude. A real matcher here would use the embeddings we
    already have, and should -- but a cheap one that runs on every tick and
    is obviously auditable is the right first version for something that can
    take a row OFF the user's home screen.
    """
    words = re.findall(r"[a-z0-9]{3,}", (s or "").lower())
    return {w for w in words if w not in _STOP}


def _overlap(a: set, b: set) -> float:
    """Share of the smaller set that both contain. 0 when either is empty.

    Smaller-set rather than Jaccard: a four-word reminder title matched
    against a notification that quotes a whole thread should not be punished
    for the thread's length.
    """
    if not a or not b:
        return 0.0
    return len(a & b) / float(min(len(a), len(b)))


def corroborate(db, cands, now=None, window_h=72, min_overlap=0.6):
    """Annotate candidates from what the notification stream said about them.

    Sets `state` to `cancelled` or `done` on a candidate a recent capture
    plainly contradicts. Returns how many it changed.

    Only ever DEMOTES, which is not a coincidence: Medha enforces the same
    rule on `state`, so neither side can use this to force something onto the
    home screen. The worst a false match can do is move a row from the agenda
    to the full brief, one tap away. A false NEGATIVE leaves things exactly as
    they were before captures existed.

    `window_h` because a cancellation is news: a notice from a fortnight ago
    about a meeting still on our list is likelier to be a different meeting
    than a late-arriving cancellation.
    """
    if not cands:
        return 0
    now = now or datetime.now()
    lo = int((now - timedelta(hours=window_h)).timestamp() * 1000)
    rows = db.execute(
        "SELECT title, text, at FROM captures WHERE at >= ? ORDER BY at DESC LIMIT 500", (lo,)
    ).fetchall()
    if not rows:
        return 0

    signals = []
    for r in rows:
        title, text = r["title"] or "", r["text"] or ""
        head = f"{title} {text}".strip()
        if _CANCELLED.match(title) or _CANCELLED.match(text):
            signals.append(("cancelled", _tokens(head)))
        elif _SETTLED.search(head):
            signals.append(("done", _tokens(head)))
    if not signals:
        return 0

    changed = 0
    for c in cands:
        # Never override a state the candidate's own source already set: a
        # bill marked paid in the app is better evidence than a notification.
        if c.get("state"):
            continue
        # TITLE only, not title + text. A candidate's `text` is OUR prose --
        # "Card bill due Tue 25 Nov. Tap to open it and mark it paid." -- and
        # has nothing to do with what the app said. Matching on it diluted a
        # real payment confirmation below the threshold with our own words
        # about dates and tapping.
        want = _tokens(c.get("title", ""))
        if len(want) < MIN_SHARED:
            continue                    # cannot be identified; leave it alone
        for state, seen in signals:
            if len(want & seen) >= MIN_SHARED and _overlap(want, seen) >= min_overlap:
                c["state"] = state
                changed += 1
                break
    return changed


def by_app(db, days=7, now=None):
    """Counts per app over a window. For the Sources screen: which app is
    actually producing the stream, so the user can see what they switched on."""
    now = now or datetime.now()
    lo = int((now - timedelta(days=days)).timestamp() * 1000)
    return [(r["label"] or r["pkg"], r["n"]) for r in db.execute(
        "SELECT COALESCE(NULLIF(label,''), pkg) AS label, pkg, COUNT(*) AS n FROM captures "
        "WHERE at >= ? GROUP BY 1 ORDER BY n DESC", (lo,))]
