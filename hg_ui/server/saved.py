"""Saved from Medha: the inbox where a capture becomes whatever you choose.

The agent copies each Save into `saved_captures` verbatim and stops. Nothing
here runs until a person picks what it is, and the pick is honoured exactly:

    note       a text note, body as it arrived
    checklist  one item per line, by the same rule as pasting a list
    reminder   a note plus a reminder on the day you choose
    task       a note plus one open task
    discard    nothing is created; the capture leaves the inbox

THE CHOICE IS ENFORCED, NOT JUST RECORDED

Two of your answers when this was designed are carried into the note itself,
because recording them in this table alone would mean the agent ignores them
the moment the note exists:

  - "keep it verbatim" sets meta_only: the agent may tag and index the note,
    but its polishing pass may not rewrite the body.
  - "the choice is mine" sets no_actions / no_reminders on the kinds you did
    not pick. Choose "note" for a grocery list and the agent does not then
    pull six tasks out of it -- that would be it deciding again, after you.

user_title is written here and only here. Its contract is "a title the
person typed ... the agent never writes it", and this is a person pressing
Apply on a title they could have edited -- which is why the agent's side of
this feature does not create notes at all.

UNDO puts the capture back in the inbox and removes what was created. A
choice that cannot be taken back is one people stop making quickly.
"""
import re
import sqlite3
from datetime import datetime

from flask import jsonify, request

from .core import _broadcast, _hash, _nanoid, _now, app, get_db, log
from .notes_common import _note_row

KINDS = ("note", "checklist", "reminder", "task", "discard")

# The app's own rule for turning lines into items -- addChecklistLines in
# 99-boot.js. Kept identical so a saved list behaves like a pasted one: an
# existing "- " or "[ ]" is not carried in as part of the item's text.
_PREFIX = re.compile(r"^\s*(?:[-*]\s*)?(?:\[[ xX]\]\s*)?")


def checklist_items(raw):
    return [t for t in (_PREFIX.sub("", ln).strip() for ln in re.split(r"\r?\n", raw or "")) if t]


def _table_ready(db):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                      "AND name='saved_captures'").fetchone() is not None


def _row(db, fp):
    return db.execute("SELECT * FROM saved_captures WHERE fp=?", (fp,)).fetchone()


def _item(r):
    return {"fp": r["fp"], "origin": r["origin"], "title": r["title"], "body": r["body"],
            "posted_at": r["posted_at"], "saved_at": r["saved_at"],
            "truncated": bool(r["truncated"]),
            "lines": len(checklist_items(r["body"]))}


@app.route("/api/saved", methods=["GET"])
def list_saved():
    db = get_db()
    if not _table_ready(db):
        return jsonify({"items": [], "count": 0})
    rows = db.execute("SELECT * FROM saved_captures WHERE decision IS NULL "
                      "ORDER BY COALESCE(saved_at, 0) DESC, created_at DESC LIMIT 100").fetchall()
    return jsonify({"items": [_item(r) for r in rows], "count": len(rows)})


def _create(db, body, title, note_type, no_actions, no_reminders):
    """A note, created the way routes_notes.create_note does it, minus the
    strip(): the capture record is verbatim and so is the note made from it."""
    nid = _nanoid(8)
    db.execute(
        "INSERT INTO notes (id, body, note_type, user_title, meta_only, no_actions, no_reminders) "
        "VALUES (?,?,?,?,1,?,?)",
        (nid, body, note_type, title, 1 if no_actions else 0, 1 if no_reminders else 0))
    db.execute("INSERT INTO note_state (note_id, status, body_hash) VALUES (?,?,?)",
               (nid, "raw", _hash(body)))
    for tag in set(t.lower() for t in re.findall(r"#(\w+)", body)):
        db.execute("INSERT OR IGNORE INTO tags (note_id, tag) VALUES (?,?)", (nid, tag))
    return nid


@app.route("/api/saved/<fp>/decide", methods=["POST"])
def decide_saved(fp):
    db = get_db()
    if not _table_ready(db):
        return jsonify({"error": "not_ready", "message": "Restart hg_ui to finish updating."}), 409
    r = _row(db, fp)
    if not r:
        return jsonify({"error": "gone", "message": "That save is no longer here."}), 404
    if r["decision"] is not None:
        return jsonify({"error": "decided", "message": "That one has already been handled.",
                        "decision": r["decision"], "note_id": r["note_id"]}), 409

    data = request.get_json(silent=True) or {}
    kind = data.get("as")
    if kind not in KINDS:
        return jsonify({"error": "bad_kind", "message": f"Choose one of: {', '.join(KINDS)}"}), 400

    # The title as shown in the inbox, which the person may have edited. Outer
    # whitespace only: a title is one line someone typed, unlike the body.
    title = (data["title"] if isinstance(data.get("title"), str) else r["title"]).strip()
    body = r["body"]

    date = ""
    if kind == "reminder":
        date = (data.get("date") or "").strip() if isinstance(data.get("date"), str) else ""
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            return jsonify({"error": "bad_date", "message": "Pick a day for the reminder."}), 400
    if kind == "task" and not title:
        return jsonify({"error": "no_title", "message": "Give the task a name."}), 400

    nid = None
    if kind != "discard":
        # A title-only capture ("Pick up the dry cleaning") has no body. Rather
        # than a note whose title and body are the same words, the words become
        # the body and the title is left to be derived from it.
        if not body.strip():
            body, title = title, ""
        if kind == "checklist":
            items = checklist_items(body) or ([title] if title else [])
            body = "\n".join(f"- [ ] {t}" for t in items)
            nid = _create(db, body, title, "checklist", no_actions=True, no_reminders=True)
        elif kind == "reminder":
            nid = _create(db, body, title, "event", no_actions=True, no_reminders=False)
            db.execute("INSERT INTO reminders (id, note_id, title, datetime_hint, datetime_resolved, "
                       "description, source) VALUES (?,?,?,?,?,?, 'user')",
                       (_nanoid(10), nid, title or body.split("\n", 1)[0][:120], date, date, ""))
        elif kind == "task":
            nid = _create(db, body, title, "text", no_actions=False, no_reminders=True)
            db.execute("INSERT INTO action_items (id, note_id, text) VALUES (?,?,?)",
                       (_nanoid(10), nid, title[:300]))
        else:
            nid = _create(db, body, title, "text", no_actions=True, no_reminders=True)

    db.execute("UPDATE saved_captures SET decision=?, note_id=?, decided_at=? WHERE fp=?",
               ("discarded" if kind == "discard" else kind, nid, _now(), fp))
    db.commit()
    if nid:
        _broadcast("note_created", _note_row(db.execute("SELECT * FROM notes WHERE id=?", (nid,)).fetchone()))
    _broadcast("saved_changed", {"fp": fp})
    log.info(f"saved capture {fp[:8]} -> {kind}" + (f" ({nid})" if nid else ""))
    return jsonify({"ok": True, "as": kind, "note_id": nid, "date": date or None})


@app.route("/api/saved/<fp>/undo", methods=["POST"])
def undo_saved(fp):
    """Puts a capture back in the inbox and removes the note it became.

    The note is deleted outright rather than sent to trash: it was created a
    moment ago by the choice being undone, so it holds nothing of the person's
    that the capture does not -- and a trashed copy would resurface as a
    duplicate the next time the same choice is made.
    """
    db = get_db()
    if not _table_ready(db):
        return jsonify({"error": "not_ready"}), 409
    r = _row(db, fp)
    if not r:
        return jsonify({"error": "gone"}), 404
    if r["decision"] is None:
        return jsonify({"ok": True, "already": True})
    if r["note_id"]:
        db.execute("DELETE FROM notes WHERE id=?", (r["note_id"],))
    db.execute("UPDATE saved_captures SET decision=NULL, note_id=NULL, decided_at=NULL WHERE fp=?", (fp,))
    db.commit()
    if r["note_id"]:
        _broadcast("note_deleted", {"id": r["note_id"]})
    _broadcast("saved_changed", {"fp": fp})
    return jsonify({"ok": True})
