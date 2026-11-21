"""Task follow-through API: the agent proposes a move for a stale task; these routes apply the answer.

Rows live in `followups` (written by the agent, see hg_agent/memos_daemon/followups.py). A proposal is
keyed by note + normalised task text, because task rows are deleted and re-created on every
re-enrichment — so every route finds the live task by text, never by a remembered id.

Applying a plan creates an ordinary reminder (source 'user'), which survives re-enrichment and which the
agent turns into an all-day calendar event when Google Calendar is connected. "Done" and "Drop" use the
same fields the task list uses (done / dismissed), so nothing here is a second source of truth.
"""
import json
import sqlite3

from datetime import datetime

from flask import abort, jsonify, request

from .core import _broadcast, _nanoid, _now, app, get_db


def _norm(t):
    return " ".join((t or "").lower().split()).rstrip(".!?")


def _task_rows(db, note_id, text, state):
    """Task rows of a note whose normalised text matches. state: 'open' | 'done' | 'dismissed'."""
    cond = {"open": "done=0 AND COALESCE(dismissed,0)=0",
            "done": "done=1 AND COALESCE(dismissed,0)=0",
            "dismissed": "COALESCE(dismissed,0)=1"}[state]
    want = _norm(text)
    return [r for r in db.execute(f"SELECT id, text FROM action_items WHERE note_id=? AND {cond}",
                                  (note_id,)) if _norm(r["text"]) == want]


def _row(db, fid):
    r = db.execute("SELECT * FROM followups WHERE id=?", (fid,)).fetchone()
    if not r:
        abort(404)
    return r


def _close(db, fid, status):
    db.execute("UPDATE followups SET status=?, resolved_at=? WHERE id=?", (status, _now(), fid))


def _gone(db, fid):
    _close(db, fid, "expired")
    db.commit()
    _broadcast("followups_changed", {"id": fid})
    return jsonify({"error": "gone", "message": "That task is already finished or removed."}), 409


def _public(r):
    return {"id": r["id"], "note_id": r["note_id"], "note_title": r["note_title"], "text": r["text"],
            "move": r["move"], "move_date": r["move_date"], "step": r["step"], "reason": r["reason"]}


def live_pending(db):
    """Pending decisions whose task is still open. Ones whose task finished or went away are closed on the
    spot, so neither the card nor the drawer badge ever counts a ghost."""
    try:
        rows = db.execute("SELECT * FROM followups WHERE status='pending' ORDER BY id").fetchall()
    except sqlite3.OperationalError:
        return []
    live, changed = [], False
    for r in rows:
        if _task_rows(db, r["note_id"], r["text"], "open"):
            live.append(r)
        else:
            _close(db, r["id"], "expired")
            changed = True
    if changed:
        db.commit()
    return live


@app.route("/api/followups", methods=["GET"])
def list_followups():
    db = get_db()
    items = [_public(r) for r in live_pending(db)]
    learning = {}
    try:
        row = db.execute("SELECT value FROM settings WHERE key='followups_learning'").fetchone()
        if row:
            learning = json.loads(row["value"]) or {}
    except Exception:
        learning = {}
    return jsonify({"items": items, "learning": learning})


@app.route("/api/followups/refresh", methods=["POST"])
def refresh_followups():
    """"Check now": the agent clears its once-a-day gate on its next tick (within a minute)."""
    db = get_db()
    db.execute("INSERT INTO settings (key, value) VALUES ('followups_refresh_requested','1') "
               "ON CONFLICT(key) DO UPDATE SET value='1', updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')")
    db.commit()
    return jsonify({"ok": True, "message": "Checking your tasks — back in a minute"})


@app.route("/api/followups/<int:fid>/accept", methods=["POST"])
def accept_followup(fid):
    db = get_db()
    r = _row(db, fid)
    if r["status"] == "expired":      # the card was stale: the task finished or went away meanwhile
        return jsonify({"error": "gone", "message": "That task is already finished or removed."}), 409
    if r["status"] != "pending":
        return jsonify({"ok": True, "status": r["status"]})
    tasks = _task_rows(db, r["note_id"], r["text"], "open")
    if not tasks:
        return _gone(db, fid)
    if r["move"] == "drop":
        db.execute("UPDATE action_items SET dismissed=1 WHERE id=?", (tasks[0]["id"],))
        _close(db, fid, "accepted")
        db.commit()
        _broadcast("action_updated", {"id": tasks[0]["id"], "dismissed": 1})
        _broadcast("followups_changed", {"id": fid})
        return jsonify({"ok": True, "status": "accepted", "move": "drop"})
    title = (r["text"] or "")[:120]
    # The card suggests one day and used to be able to offer only that one, so
    # "move this" and "move this to the day the agent picked" were the same
    # button. An explicit date in the body overrides it; anything that is not a
    # plain ISO day is ignored rather than rejected, so a malformed value falls
    # back to the suggestion instead of failing the whole action.
    date = r["move_date"] or ""
    want = ((request.get_json(silent=True) or {}).get("date") or "").strip()
    if want:
        try:
            datetime.strptime(want, "%Y-%m-%d")
            date = want
        except ValueError:
            pass
    desc = f"Start with: {r['step']}" if r["step"] else ""
    reminder_id = None
    dup = [x for x in db.execute(
        "SELECT title FROM reminders WHERE note_id=? AND COALESCE(dismissed,0)=0 AND "
        "substr(COALESCE(NULLIF(datetime_resolved,''), datetime_hint),1,10)=?", (r["note_id"], date))
        if _norm(x["title"]) == _norm(title)]
    if not dup:                       # never double-book the same task on the same day
        reminder_id = _nanoid(10)
        db.execute("INSERT INTO reminders (id, note_id, title, datetime_hint, datetime_resolved, "
                   "description, source) VALUES (?,?,?,?,?,?, 'user')",
                   (reminder_id, r["note_id"], title, date, date, desc))
    db.execute("UPDATE followups SET reminder_id=? WHERE id=?", (reminder_id, fid))
    _close(db, fid, "accepted")
    db.commit()
    _broadcast("followups_changed", {"id": fid})
    return jsonify({"ok": True, "status": "accepted", "move": r["move"], "date": date})


@app.route("/api/followups/<int:fid>/done", methods=["POST"])
def done_followup(fid):
    db = get_db()
    r = _row(db, fid)
    tasks = _task_rows(db, r["note_id"], r["text"], "open")
    if not tasks:
        return _gone(db, fid)
    db.execute("UPDATE action_items SET done=1, done_at=? WHERE id=?", (_now(), tasks[0]["id"]))
    _close(db, fid, "done")
    db.commit()
    _broadcast("action_updated", {"id": tasks[0]["id"], "done": 1})
    _broadcast("followups_changed", {"id": fid})
    return jsonify({"ok": True, "status": "done"})


@app.route("/api/followups/<int:fid>/drop", methods=["POST"])
def drop_followup(fid):
    """"This isn't a task any more" — the same dismissal the task list uses, so it is not re-extracted."""
    db = get_db()
    r = _row(db, fid)
    tasks = _task_rows(db, r["note_id"], r["text"], "open")
    if not tasks:
        return _gone(db, fid)
    db.execute("UPDATE action_items SET dismissed=1 WHERE id=?", (tasks[0]["id"],))
    _close(db, fid, "dropped")
    db.commit()
    _broadcast("action_updated", {"id": tasks[0]["id"], "dismissed": 1})
    _broadcast("followups_changed", {"id": fid})
    return jsonify({"ok": True, "status": "dropped"})


@app.route("/api/followups/<int:fid>/dismiss", methods=["POST"])
def dismiss_followup(fid):
    """"Not now": hides this task for a week; asked twice and it stays hidden. Also a signal the agent learns from."""
    db = get_db()
    r = _row(db, fid)
    if r["status"] == "pending":
        _close(db, fid, "dismissed")
        db.commit()
        _broadcast("followups_changed", {"id": fid})
    return jsonify({"ok": True, "status": "dismissed"})


@app.route("/api/followups/<int:fid>/undo", methods=["POST"])
def undo_followup(fid):
    """Put a decision back on the card and reverse what it did."""
    db = get_db()
    r = _row(db, fid)
    st, calendar = r["status"], "none"
    if st == "done":
        for t in _task_rows(db, r["note_id"], r["text"], "done")[:1]:
            db.execute("UPDATE action_items SET done=0, done_at=NULL WHERE id=?", (t["id"],))
    elif st == "dropped" or (st == "accepted" and r["move"] == "drop"):
        for t in _task_rows(db, r["note_id"], r["text"], "dismissed")[:1]:
            db.execute("UPDATE action_items SET dismissed=0 WHERE id=?", (t["id"],))
    elif st == "accepted" and r["reminder_id"]:
        rem = db.execute("SELECT calendar_event_id FROM reminders WHERE id=?", (r["reminder_id"],)).fetchone()
        db.execute("UPDATE reminders SET dismissed=1 WHERE id=?", (r["reminder_id"],))
        if rem and (rem["calendar_event_id"] or "") not in ("", "dup:skipped"):
            calendar = "kept"          # an event already exists; we never delete calendar data unasked
    if st != "pending":
        db.execute("UPDATE followups SET status='pending', resolved_at=NULL, reminder_id=NULL, "
                   "cal_synced=0, cal_tries=0 WHERE id=?", (fid,))
    db.commit()
    _broadcast("action_updated", {"id": r["note_id"]})
    _broadcast("followups_changed", {"id": fid})
    return jsonify({"ok": True, "status": "pending", "calendar": calendar})
