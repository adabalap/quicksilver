"""insights.py — weekly digest and duplicate-note detection.

Pure functions over the Quicksilver SQLite database. The model only writes the
short prose of the digest (and a deterministic fallback covers a failed call), so
counts, tags and lists in the digest are always facts, never model output."""
from __future__ import annotations
import hashlib, json, re, time
from datetime import datetime, timedelta, timezone

DUP_TABLE = """
CREATE TABLE IF NOT EXISTS dup_suggestions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    note_a     TEXT NOT NULL,
    note_b     TEXT NOT NULL,
    score      REAL NOT NULL,
    status     TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','merged','dismissed')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE(note_a, note_b)
)"""

_VISIBLE = "n.archived=0 AND n.hidden=0 AND n.protected=0 AND COALESCE(n.is_daily,0)=0"


def _iso(dt) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _title(row) -> str:
    t = (row["t"] or "").strip() if "t" in row.keys() else ""
    if t:
        return t
    b = (row["body"] or "").strip().replace("\n", " ")
    return (b[:48] + "…") if len(b) > 48 else (b or "Untitled")


_TITLE_SQL = ("(SELECT title FROM enrichments WHERE note_id=n.id "
              "ORDER BY enriched_at DESC, rowid DESC LIMIT 1) AS t")


# ── duplicates ───────────────────────────────────────────────────────────────
def _tokens(text: str) -> set:
    return set(re.findall(r"\w+", (text or "").lower()))


def text_overlap(a: str, b: str) -> tuple[float, float]:
    """(jaccard, containment) of the two notes' word sets."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0, 0.0
    inter = len(ta & tb)
    return inter / len(ta | tb), inter / min(len(ta), len(tb))


def is_duplicate(a: str, b: str, min_words: int = 3) -> bool:
    ta, tb = _tokens(a), _tokens(b)
    if min(len(ta), len(tb)) < min_words:
        return False
    j, c = text_overlap(a, b)
    return j >= 0.6 or (c >= 0.9 and min(len(ta), len(tb)) >= 5)


def find_duplicates(db, min_score: float = 0.85, limit: int = 50) -> int:
    """Record new likely-duplicate pairs in dup_suggestions. Candidates are the
    already-computed relationship pairs (semantic) plus notes with identical text;
    both must also pass a word-overlap check, so two merely similar notes (same
    template, same topic) are not proposed. Returns how many were added."""
    db.execute(DUP_TABLE)
    pairs: dict[tuple, float] = {}
    try:
        for r in db.execute("SELECT note_id_a, note_id_b, score FROM relationships WHERE score >= ?",
                            (min_score,)):
            k = tuple(sorted((r["note_id_a"], r["note_id_b"])))
            pairs[k] = max(pairs.get(k, 0), float(r["score"]))
    except Exception:
        pass
    for r in db.execute(
            f"SELECT group_concat(n.id) ids FROM notes n WHERE {_VISIBLE} AND length(trim(n.body)) >= 15 "
            "GROUP BY lower(trim(n.body)) HAVING count(*) > 1"):
        ids = sorted(r["ids"].split(","))
        for i in range(1, len(ids)):
            pairs[(ids[0], ids[i])] = 1.0
    if not pairs:
        return 0
    seen = {(r["note_a"], r["note_b"]) for r in db.execute("SELECT note_a, note_b FROM dup_suggestions")}
    added = 0
    for (a, b), sc in sorted(pairs.items(), key=lambda kv: -kv[1]):
        if (a, b) in seen or added >= limit:
            continue
        rows = {r["id"]: r for r in db.execute(
            f"SELECT n.id, n.body FROM notes n WHERE n.id IN (?,?) AND {_VISIBLE}", (a, b))}
        if len(rows) != 2:
            continue
        if sc < 1.0 and not is_duplicate(rows[a]["body"], rows[b]["body"]):
            continue
        db.execute("INSERT OR IGNORE INTO dup_suggestions (note_a, note_b, score) VALUES (?,?,?)",
                   (a, b, round(sc, 3)))
        added += 1
    db.commit()
    return added


# ── weekly digest ────────────────────────────────────────────────────────────
def week_key(now: datetime) -> str:
    y, w, _ = now.isocalendar()
    return f"{y}-W{w:02d}"


def gather(db, now: datetime, days: int = 7) -> dict:
    since = _iso(now - timedelta(days=days))
    rows = db.execute(
        f"SELECT n.id, n.body, {_TITLE_SQL} FROM notes n WHERE {_VISIBLE} AND n.created_at >= ? "
        "ORDER BY n.created_at DESC", (since,)).fetchall()
    one = lambda sql, *p: db.execute(sql, p).fetchone()[0]
    att = {k: one("SELECT COUNT(*) FROM attachments a JOIN notes n ON n.id=a.note_id "
                  f"WHERE {_VISIBLE} AND a.kind=? AND a.created_at >= ?", k, since)
           for k in ("image", "audio", "sketch")}
    tags = db.execute(
        "SELECT t.tag, COUNT(*) c FROM tags t JOIN notes n ON n.id=t.note_id "
        f"WHERE {_VISIBLE} AND n.created_at >= ? GROUP BY t.tag ORDER BY c DESC, t.tag LIMIT 5",
        (since,)).fetchall()
    open_rows = db.execute(
        "SELECT a.text, a.note_id FROM action_items a JOIN notes n ON n.id=a.note_id "
        f"WHERE {_VISIBLE} AND a.done=0 AND COALESCE(a.dismissed,0)=0 "
        "ORDER BY a.created_at DESC, a.rowid DESC LIMIT 8").fetchall()
    open_total = one("SELECT COUNT(*) FROM action_items a JOIN notes n ON n.id=a.note_id "
                     f"WHERE {_VISIBLE} AND a.done=0 AND COALESCE(a.dismissed,0)=0")
    done_week = one("SELECT COUNT(*) FROM action_items WHERE done=1 AND done_at >= ?", since)
    # Revisit: older notes sharing this week's top tags, so resurfaced notes are relevant.
    revisit = []
    top = [t["tag"] for t in tags[:3]]
    if top:
        q = ",".join("?" * len(top))
        old = db.execute(
            f"SELECT DISTINCT n.id, n.body, n.created_at, {_TITLE_SQL} FROM notes n "
            f"JOIN tags t ON t.note_id=n.id WHERE {_VISIBLE} AND t.tag IN ({q}) "
            "AND n.created_at < ? AND length(trim(n.body)) >= 40",
            (*top, _iso(now - timedelta(days=30)))).fetchall()
        wk = week_key(now)
        old = sorted(old, key=lambda r: hashlib.md5((r["id"] + wk).encode()).hexdigest())[:3]
        revisit = [{"id": r["id"], "title": _title(r), "date": r["created_at"]} for r in old]
    return {
        "since": since,
        "counts": {"notes": len(rows), "photos": att["image"], "voice": att["audio"],
                   "sketches": att["sketch"], "tasks_done": done_week, "tasks_open": open_total},
        "notes": [{"id": r["id"], "title": _title(r)} for r in rows[:12]],
        "top_tags": [{"tag": t["tag"], "count": t["c"]} for t in tags],
        "open_tasks": [{"text": r["text"], "note_id": r["note_id"]} for r in open_rows],
        "revisit": revisit,
    }


def fallback_text(g: dict) -> str:
    c = g["counts"]
    bits = [f"You captured {c['notes']} note{'s' if c['notes'] != 1 else ''} this week"]
    media = []
    if c["photos"]: media.append(f"{c['photos']} photo{'s' if c['photos'] != 1 else ''}")
    if c["voice"]: media.append(f"{c['voice']} voice note{'s' if c['voice'] != 1 else ''}")
    if c["sketches"]: media.append(f"{c['sketches']} sketch{'es' if c['sketches'] != 1 else ''}")
    if media:
        bits[0] += " (including " + ", ".join(media) + ")"
    if g["top_tags"]:
        bits.append("mostly about " + ", ".join("#" + t["tag"] for t in g["top_tags"][:3]))
    text = " — ".join(bits) + "."
    if c["tasks_open"]:
        text += f" {c['tasks_open']} task{'s are' if c['tasks_open'] != 1 else ' is'} still open."
    return text


def compose_digest(db, now: datetime, complete=None, max_tokens: int = 160) -> dict | None:
    g = gather(db, now)
    if not g["counts"]["notes"] and not g["counts"]["tasks_open"]:
        return None
    text = ""
    if complete and g["notes"]:
        prompt = ("Write a friendly 2-4 sentence summary of the user's week from these facts, addressed to "
                  "them. Mention the main themes. Use ONLY these facts; never invent names or events. "
                  "No lists, no headings.\n"
                  f"Notes this week: {'; '.join(n['title'] for n in g['notes'])}\n"
                  f"Top tags: {', '.join(t['tag'] for t in g['top_tags']) or 'none'}\n"
                  f"Open tasks: {'; '.join(t['text'] for t in g['open_tasks'][:5]) or 'none'}\n")
        try:
            text = (complete(prompt, max_tokens=max_tokens) or "").strip()
        except Exception:
            text = ""
    g.update({"ts": time.time(), "week": week_key(now), "text": text or fallback_text(g),
              "generated_by": "model" if text else "facts"})
    return g


def due(db, now: datetime, weekday: int = 6, hour: int = 18) -> bool:
    """True when the most recent digest moment (chosen weekday + hour, local time) has
    passed with no digest generated since — so a phone that was off on Sunday evening
    catches up on its next run — or when the user asked for a fresh one."""
    row = db.execute("SELECT value FROM settings WHERE key='digest_refresh_requested'").fetchone()
    if row and row["value"] == "1":
        return True
    moment = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    moment -= timedelta(days=(now.weekday() - weekday) % 7)
    if moment > now:
        moment -= timedelta(days=7)
    cur = db.execute("SELECT value FROM settings WHERE key='weekly_digest'").fetchone()
    if cur:
        try:
            return float(json.loads(cur["value"]).get("ts", 0)) < moment.timestamp()
        except Exception:
            return True
    return True
