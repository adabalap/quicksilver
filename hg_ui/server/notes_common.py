"""Helpers shared by the note routes: row → JSON, list filters, calendar clean-up on delete."""
import re

import mediastore as _ms

from .core import _ISO_TS, get_db


# ── Note helpers ─────────────────────────────────────────────────────────────
def _note_row(row) -> dict:
    """Assemble a full note object from the DB (note + enrichment + tags + actions)."""
    db = get_db()
    nid = row["id"]

    # enrichment
    enr = db.execute(
        "SELECT * FROM enrichments WHERE note_id=? ORDER BY enriched_at DESC, rowid DESC LIMIT 1",
        (nid,)).fetchone()

    # state
    st = db.execute("SELECT * FROM note_state WHERE note_id=?", (nid,)).fetchone()

    # tags
    tags = [r["tag"] for r in
            db.execute("SELECT tag FROM tags WHERE note_id=? ORDER BY tag", (nid,))]

    # action items
    actions = [dict(r) for r in
               db.execute("SELECT * FROM action_items WHERE note_id=? AND COALESCE(dismissed,0)=0 "
                          "ORDER BY created_at",
                          (nid,))]

    # relationships
    rels = db.execute("""
        SELECT r.note_id_b as related_id,
               (SELECT title FROM enrichments WHERE note_id = n.id
                ORDER BY enriched_at DESC, rowid DESC LIMIT 1) as rel_title,
               n.body as rel_body,
               n.updated_at as rel_updated,
               r.score
        FROM relationships r
        JOIN notes n ON n.id = r.note_id_b
        WHERE r.note_id_a = ?
        ORDER BY n.updated_at DESC, r.score DESC LIMIT 20
    """, (nid,)).fetchall()

    def _rel_label(r):
        t = (r["rel_title"] or "").strip()
        if t:
            return t
        # No title yet — use a short body snippet, never the raw id.
        body = (r["rel_body"] or "").strip().replace("\n", " ")
        return (body[:48] + "…") if len(body) > 48 else (body or "Untitled note")

    # Skip-enrichment opt-out. This is a statement about FUTURE work — "don't
    # spend another ~72s Gemma call on this note" — not about existing content.
    # We deliberately do NOT hide what the agent already produced: the title and
    # tags are what make a note findable in a list of thousands, so hiding them
    # makes the note worse, not cleaner. An earlier version hid everything, which
    # also stranded notes mid-review (nothing visible to approve → stuck in
    # 'pending' forever, with Re-run and Polish both hidden). A toggle about the
    # future must not silently rewrite the past.
    optout = (row["enrich_optout"] if "enrich_optout" in row.keys() else 0)

    return {
        "id":          nid,
        "body":        row["body"],
        "protected":   (row["protected"] if "protected" in row.keys() else 0),
        "pinned":      (row["pinned"] if "pinned" in row.keys() else 0),
        "archived":    (row["archived"] if "archived" in row.keys() else 0),
        "hidden":      (row["hidden"] if "hidden" in row.keys() else 0),
        "enrich_optout": optout,
        "meta_only":   (row["meta_only"] if "meta_only" in row.keys() else 0),
        "color_idx":   (row["color_idx"] if "color_idx" in row.keys() else None),
        "no_actions":  (row["no_actions"] if "no_actions" in row.keys() else 0),
        "no_reminders":(row["no_reminders"] if "no_reminders" in row.keys() else 0),
        "reminders":   [dict(r) for r in db.execute(
            "SELECT id, title, datetime_hint, datetime_resolved, calendar_event_id, "
            "source, CASE WHEN source='user' THEN description ELSE '' END AS description FROM reminders WHERE note_id=? AND COALESCE(dismissed,0)=0 "
            "ORDER BY created_at", (nid,))],
        "note_type":   (row["note_type"] if "note_type" in row.keys() else "text"),
        "is_daily":    (row["is_daily"] if "is_daily" in row.keys() else 0),
        "user_edited": bool(db.execute(
            "SELECT 1 FROM revisions WHERE note_id=? AND kind='edit' LIMIT 1",
            (nid,)).fetchone()),
        "backlinks":   [{"id": r["dst_id"],
                         "title": (r["t"] or (r["b"] or "")[:40] or "Untitled")}
                        for r in db.execute(
            "SELECT b.dst_id, (SELECT title FROM enrichments WHERE note_id=b.dst_id "
            "ORDER BY enriched_at DESC, rowid DESC LIMIT 1) t, n.body b "
            "FROM backlinks b JOIN notes n ON n.id=b.dst_id WHERE b.src_id=?", (nid,))],
        "attachments": [_ms.public(a) for a in db.execute(
            "SELECT * FROM attachments WHERE note_id=? ORDER BY created_at", (nid,))],
        "visibility":  row["visibility"],
        "created_at":  row["created_at"],
        "updated_at":  row["updated_at"],
        "status":      st["status"] if st else "raw",
        "failures":    st["failures"] if st else 0,
        "state_updated_at": st["updated_at"] if st else None,
        "tags":        tags,
        "action_items": actions,
        "related":     [{"id": r["related_id"],
                         "title": _rel_label(r),
                         "updated": r["rel_updated"],
                         "score": round(r["score"], 3)} for r in rels],
        "enrichment":  dict(enr) if enr else None,
        "user_title":  (row["user_title"] if "user_title" in row.keys() else "") or "",
    }

# ── API: Notes ────────────────────────────────────────────────────────────────
_FILTER_TYPES = {
    "photo":  "EXISTS (SELECT 1 FROM attachments a WHERE a.note_id=n.id AND a.kind='image')",
    "voice":  "EXISTS (SELECT 1 FROM attachments a WHERE a.note_id=n.id AND a.kind='audio')",
    "sketch": "EXISTS (SELECT 1 FROM attachments a WHERE a.note_id=n.id AND a.kind='sketch')",
    "video":  "EXISTS (SELECT 1 FROM attachments a WHERE a.note_id=n.id AND a.kind='video')",
    "media":  "EXISTS (SELECT 1 FROM attachments a WHERE a.note_id=n.id)",
    "text":   "NOT EXISTS (SELECT 1 FROM attachments a WHERE a.note_id=n.id)",
    "task":   "EXISTS (SELECT 1 FROM action_items x WHERE x.note_id=n.id AND x.done=0 AND COALESCE(x.dismissed,0)=0)",
}

def _iso_bound(v: str, end: bool = False):
    """'2026-10-01' or a full ISO timestamp → the comparable ISO string, else None."""
    v = (v or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        return v + ("T23:59:59.999Z" if end else "T00:00:00.000Z")
    return v if _ISO_TS.match(v) else None

def _note_filters(args):
    """The list/search filters (type, tag, since, until) as SQL fragments on alias n.
    Shared by /api/notes and /api/search so the two always filter the same way."""
    frags, params = [], []
    t = (args.get("type") or "").strip().lower()
    if t in _FILTER_TYPES:
        frags.append(_FILTER_TYPES[t])
    tag = (args.get("tag") or "").strip().lstrip("#").lower()
    if tag:
        frags.append("n.id IN (SELECT note_id FROM tags WHERE tag=?)"); params.append(tag)
    lo = _iso_bound(args.get("since"))
    hi = _iso_bound(args.get("until"), end=True)
    if lo:
        frags.append("n.created_at >= ?"); params.append(lo)
    if hi:
        frags.append("n.created_at <= ?"); params.append(hi)
    return frags, params

def _enqueue_calendar_deletions(db, rows):
    """Queue Google Calendar events for removal by the agent.

    `rows` is an iterable of (event_id, note_id, title). Only rows with a real
    event id are queued — we can only delete events WE created and recorded, and
    must never touch anything the user made directly in their calendar.

    Idempotent: event_id is the primary key, so re-queueing is a no-op rather
    than a duplicate. Returns how many were queued.
    """
    n = 0
    for ev, note_id, title in rows:
        ev = (ev or "").strip()
        if not ev:
            continue
        db.execute(
            "INSERT INTO calendar_deletions (event_id, note_id, title) "
            "VALUES (?,?,?) ON CONFLICT(event_id) DO NOTHING",
            (ev, note_id, (title or "")[:200]))
        n += 1
    return n
