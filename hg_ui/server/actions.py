"""Action items and reminders API."""
from flask import jsonify, request, abort

from .core import _broadcast, _iso_ago, _now, app, get_db
from .notes_common import _enqueue_calendar_deletions


# ── API: Action items ─────────────────────────────────────────────────────────
@app.route("/api/actions/<aid>", methods=["PATCH"])
def update_action(aid):
    db = get_db()
    row = db.execute("SELECT * FROM action_items WHERE id=?", (aid,)).fetchone()
    if not row: abort(404)
    data = request.get_json(force=True) or {}
    done = bool(data.get("done", row["done"]))
    done_at = _now() if done else None
    db.execute("UPDATE action_items SET done=?, done_at=? WHERE id=?",
               (int(done), done_at, aid))
    db.commit()
    result = dict(db.execute("SELECT * FROM action_items WHERE id=?", (aid,)).fetchone())
    _broadcast("action_updated", result)
    return jsonify(result)

@app.route("/api/actions/<aid>", methods=["DELETE"])
def dismiss_action(aid):
    """Dismiss an action item — "this isn't a task", distinct from marking it done.

    The row is KEPT with dismissed=1 rather than deleted, because the agent
    re-extracts action items from the note text on every enrichment. Without a
    record of the rejection, the next run would resurrect it and the user would
    dismiss the same item forever. write_enrichment consults these rows and skips
    re-inserting matching text.
    """
    db = get_db()
    row = db.execute("SELECT * FROM action_items WHERE id=?", (aid,)).fetchone()
    if not row:
        abort(404)
    db.execute("UPDATE action_items SET dismissed=1 WHERE id=?", (aid,))
    db.commit()
    _broadcast("action_updated", {"id": aid, "dismissed": 1})
    return jsonify({"ok": True, "id": aid, "dismissed": 1, "text": row["text"]})

@app.route("/api/actions/<aid>/restore", methods=["POST"])
def restore_action(aid):
    """Undo a dismissal (the Undo affordance in the UI)."""
    db = get_db()
    if not db.execute("SELECT 1 FROM action_items WHERE id=?", (aid,)).fetchone():
        abort(404)
    db.execute("UPDATE action_items SET dismissed=0 WHERE id=?", (aid,))
    db.commit()
    _broadcast("action_updated", {"id": aid, "dismissed": 0})
    return jsonify({"ok": True, "id": aid, "dismissed": 0})

@app.route("/api/reminders/<rid>", methods=["DELETE"])
def dismiss_reminder(rid):
    """Dismiss a reminder, optionally also deleting the real calendar event.

    Query/body flag `delete_event=true` asks us to remove the Google Calendar
    event too. That touches data in an external system the user may have shared
    or already acted on, so it is NEVER the default — the caller must ask for it
    explicitly, and the UI confirms first.

    The response always states what actually happened to the calendar so the UI
    can tell the truth rather than assume:
      calendar: "none"              — there was no event
      calendar: "kept"              — an event exists and was left alone
      calendar: "delete_requested"  — queued for the agent to remove
    """
    db = get_db()
    row = db.execute("SELECT * FROM reminders WHERE id=?", (rid,)).fetchone()
    if not row:
        abort(404)
    data = request.get_json(silent=True) or {}
    want_delete = str(data.get("delete_event",
                               request.args.get("delete_event", ""))).lower() in ("1", "true", "yes")
    ev_id = (row["calendar_event_id"] or "").strip() if "calendar_event_id" in row.keys() else ""

    calendar = "none"
    if ev_id:
        if want_delete:
            # Queue the deletion for the agent rather than calling Google here.
            # The UI holds no calendar credentials by design — the agent owns
            # them — and SQLite is already the contract between the two. The
            # agent picks this up on its next pass and clears the marker on
            # success, so a failure is visible rather than silently swallowed.
            _enqueue_calendar_deletions(db, [(ev_id, row["note_id"], row["title"])])
            db.execute("UPDATE reminders SET calendar_delete_requested=1 WHERE id=?",
                       (rid,))
            calendar = "delete_requested"
        else:
            calendar = "kept"

    db.execute("UPDATE reminders SET dismissed=1, description=CASE WHEN source='user' THEN '' ELSE description END WHERE id=?", (rid,))
    db.commit()
    _broadcast("reminders_changed", {"id": rid})          # the agent drops it from Medha's brief and notification
    return jsonify({"ok": True, "id": rid, "dismissed": 1,
                    "calendar": calendar, "title": row["title"]})

@app.route("/api/notes/<nid>/extract", methods=["POST"])
def toggle_extract(nid):
    """Per-note switches for what the agent extracts.

    Body: {"actions": true|false, "reminders": true|false} — either or both.
    These stop the agent looking for that kind of output in this note at all,
    which is the right tool when a note structurally isn't a task list (a
    journal entry, a transcript) and dismissing items one by one would be
    endless.
    """
    db = get_db()
    if not db.execute("SELECT 1 FROM notes WHERE id=?", (nid,)).fetchone():
        abort(404)
    data = request.get_json(force=True) or {}
    sets, params = [], []
    if "actions" in data:
        sets.append("no_actions=?"); params.append(0 if data["actions"] else 1)
    if "reminders" in data:
        sets.append("no_reminders=?"); params.append(0 if data["reminders"] else 1)
    if not sets:
        return jsonify({"error": "nothing to set"}), 400
    db.execute(f"UPDATE notes SET {', '.join(sets)} WHERE id=?", params + [nid])
    db.commit()
    row = db.execute("SELECT no_actions, no_reminders FROM notes WHERE id=?",
                     (nid,)).fetchone()
    return jsonify({"ok": True, "no_actions": row["no_actions"],
                    "no_reminders": row["no_reminders"]})

ACTIONS_DONE_CAP = 200          # finished tasks returned per request (open ones are never capped)
REMINDERS_OLD_CAP = 100         # reminders dated more than 30 days ago: the newest this many

@app.route("/api/actions", methods=["GET"])
def list_actions():
    db = get_db()
    done_filter = request.args.get("done")
    aging = request.args.get("aging")   # '1' → only actions whose note is >7 days old
    # Dismissed items are rejections, not tasks — they never appear in the list.
    # (The rows persist only so the agent won't re-suggest the same text.)
    base = ["COALESCE(a.dismissed,0)=0",
            "n.hidden=0"]               # stashed notes' tasks stay stashed (and match the dashboard)
    params = []
    if aging == "1":
        base.append("a.done=0 AND n.created_at < ?"); params.append(_iso_ago(7 * 86400))
    sql = """
        SELECT a.*, n.id as note_id,
               COALESCE(e.title, substr(n.body,1,60)) as note_title,
               t_agg.tags
        FROM action_items a
        JOIN notes n ON n.id = a.note_id
        LEFT JOIN enrichments e ON e.note_id = n.id
            AND e.id=(SELECT id FROM enrichments WHERE note_id=n.id ORDER BY enriched_at DESC, rowid DESC LIMIT 1)
        LEFT JOIN (
            SELECT note_id, GROUP_CONCAT(tag) as tags FROM tags GROUP BY note_id
        ) t_agg ON t_agg.note_id = n.id
        WHERE %s
        %s
    """
    def run(extra, order, limit=None):
        q = sql % (" AND ".join(base + extra), order + (f" LIMIT {int(limit)}" if limit else ""))
        return [dict(r) for r in db.execute(q, params).fetchall()]
    # Every open task is returned. The history of finished ones is capped (newest first) so the
    # reply doesn't grow without bound as years of completed tasks pile up.
    open_first = "ORDER BY a.created_at ASC"
    done_recent = "ORDER BY a.done_at DESC, a.created_at DESC"
    if done_filter == "0" or aging == "1":
        out = run(["a.done=0"], open_first)
    elif done_filter == "1":
        out = run(["a.done=1"], done_recent, ACTIONS_DONE_CAP)
    else:
        out = run(["a.done=0"], open_first) + run(["a.done=1"], done_recent, ACTIONS_DONE_CAP)
    return jsonify({"actions": out})

@app.route("/api/reminders", methods=["GET"])
def list_reminders():
    """All non-dismissed reminders across notes, for the Reminders tab.

    Ordered so the actionable ones lead: dated reminders first (soonest at top),
    then undated hints. Carries the source note's title so the list is legible.
    """
    db = get_db()
    rows = db.execute("""
        SELECT r.id, r.note_id, r.title, r.datetime_hint, r.datetime_resolved,
               r.calendar_event_id, r.source,
               CASE WHEN r.source='user' THEN r.description ELSE '' END AS description,
               COALESCE(NULLIF(n.user_title,''), e.title, substr(n.body,1,60)) AS note_title
        FROM reminders r
        JOIN notes n ON n.id = r.note_id
        LEFT JOIN enrichments e ON e.note_id = n.id
            AND e.id = (SELECT id FROM enrichments WHERE note_id=n.id
                        ORDER BY enriched_at DESC, rowid DESC LIMIT 1)
        WHERE COALESCE(r.dismissed,0)=0
          AND (r.datetime_resolved IS NULL OR r.datetime_resolved = '' OR r.datetime_resolved >= :cut
               OR r.id IN (SELECT id FROM reminders WHERE COALESCE(dismissed,0)=0
                              AND datetime_resolved < :cut ORDER BY datetime_resolved DESC LIMIT :cap))
        ORDER BY CASE WHEN r.datetime_resolved IS NOT NULL
                      AND r.datetime_resolved != '' THEN 0 ELSE 1 END,
                 r.datetime_resolved ASC, r.created_at DESC
    """, {"cut": _iso_ago(30 * 86400), "cap": REMINDERS_OLD_CAP}).fetchall()
    return jsonify({"reminders": [dict(r) for r in rows]})
