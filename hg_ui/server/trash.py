"""Trash and revisions: soft delete, restore, expiry, and note history."""
import os
import json
import sqlite3
from pathlib import Path
from flask import jsonify, abort

import mediastore as _ms

from .core import DB_PATH, _broadcast, _nanoid, app, get_db, log
from .notes_common import _note_row


# ── Trash ────────────────────────────────────────────────────────────────────
TRASH_DAYS = 30
_TRASH_TABLES = ("enrichments", "note_state", "tags", "action_items", "reminders",
                 "corrections", "revisions", "attachments")
_SKIP_COLS = {"notes": {"embedding", "embed_model"}}

def _trash_dir():
    d = _ms.att_dir(DB_PATH) / "_trash"
    d.mkdir(parents=True, exist_ok=True)
    return d

def _jsonable(v):
    import base64
    if isinstance(v, (bytes, bytearray)):
        return {"__b64": base64.b64encode(bytes(v)).decode()}
    return v

def _unjson(v):
    import base64
    if isinstance(v, dict) and "__b64" in v:
        return base64.b64decode(v["__b64"])
    return v

def _trash_put(db, nid, files):
    note = db.execute("SELECT * FROM notes WHERE id=?", (nid,)).fetchone()
    snap = {"notes": [{k: _jsonable(note[k]) for k in note.keys() if k not in _SKIP_COLS["notes"]}]}
    for t in _TRASH_TABLES:
        try:
            rows = db.execute(f"SELECT * FROM {t} WHERE note_id=?", (nid,)).fetchall()
            snap[t] = [{k: _jsonable(r[k]) for k in r.keys()} for r in rows]
        except sqlite3.OperationalError as e:      # an older DB may lack a table — nothing to keep
            log.debug(f"trash: skipping {t}: {e}")
            snap[t] = []
    for r in snap["reminders"]:
        r["calendar_event_id"] = None          # the calendar event is removed on delete
    enr = db.execute("SELECT title FROM enrichments WHERE note_id=?", (nid,)).fetchone()
    body = (note["body"] or "").strip()
    title = (enr["title"] if enr and enr["title"] else "") or (body.splitlines()[0][:80] if body else "")
    moved = []
    td = _trash_dir()
    for fn in files:
        src = _ms.path_for(DB_PATH, fn)
        if src.exists():
            os.replace(src, td / src.name); moved.append(src.name)
    db.execute("INSERT OR REPLACE INTO trash (id, title, preview, n_attach, snapshot, files) VALUES (?,?,?,?,?,?)",
               (nid, title, body[:200], len(snap["attachments"]), json.dumps(snap), json.dumps(moved)))
    return True

def _trash_purge_files(files):
    td = _trash_dir()
    for fn in files:
        try: (td / Path(fn).name).unlink()
        except OSError: pass

def _trash_expire(db):
    rows = db.execute("SELECT id, files FROM trash WHERE deleted_at < strftime('%Y-%m-%dT%H:%M:%fZ','now',?)",
                      (f"-{TRASH_DAYS} days",)).fetchall()
    for r in rows:
        _trash_purge_files(json.loads(r["files"] or "[]"))
        db.execute("DELETE FROM trash WHERE id=?", (r["id"],))
    if rows:
        db.commit()
        log.info(f"trash: removed {len(rows)} note(s) older than {TRASH_DAYS} days")

@app.route("/api/trash", methods=["GET"])
def trash_list():
    db = get_db()
    _trash_expire(db)
    out = []
    for r in db.execute("SELECT id, title, preview, n_attach, deleted_at, "
                        "CAST(julianday('now') - julianday(deleted_at) AS REAL) AS age FROM trash ORDER BY deleted_at DESC"):
        out.append({"id": r["id"], "title": r["title"] or "Untitled", "preview": r["preview"],
                    "attachments": r["n_attach"], "deleted_at": r["deleted_at"],
                    "days_left": max(0, int(TRASH_DAYS - r["age"]))})
    return jsonify({"items": out, "days": TRASH_DAYS})

@app.route("/api/trash/<nid>/restore", methods=["POST"])
def trash_restore(nid):
    db = get_db()
    row = db.execute("SELECT * FROM trash WHERE id=?", (nid,)).fetchone()
    if not row:
        abort(404)
    if db.execute("SELECT 1 FROM notes WHERE id=?", (nid,)).fetchone():
        return jsonify({"error": "a note with this id already exists"}), 409
    snap = json.loads(row["snapshot"])
    def ins(table, rec):
        rec = {k: _unjson(v) for k, v in rec.items()}
        cols = ",".join(rec); qs = ",".join("?" * len(rec))
        db.execute(f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({qs})", list(rec.values()))
    try:
        for rec in snap["notes"]:
            ins("notes", rec)
        for t in _TRASH_TABLES:
            for rec in snap.get(t, []):
                ins(t, rec)
        db.commit()
    except Exception as e:
        db.rollback()
        log.warning(f"trash restore of {nid} failed: {e}")
        return jsonify({"error": "couldn't restore this note"}), 500
    td = _trash_dir(); ad = _ms.att_dir(DB_PATH)
    for fn in json.loads(row["files"] or "[]"):
        p = td / Path(fn).name
        if p.exists():
            os.replace(p, ad / p.name)
    db.execute("DELETE FROM trash WHERE id=?", (nid,))
    db.commit()
    _broadcast("note_created", {"id": nid})
    return jsonify({"restored": nid})

@app.route("/api/trash/<nid>", methods=["DELETE"])
def trash_delete(nid):
    db = get_db()
    row = db.execute("SELECT files FROM trash WHERE id=?", (nid,)).fetchone()
    if not row:
        abort(404)
    _trash_purge_files(json.loads(row["files"] or "[]"))
    db.execute("DELETE FROM trash WHERE id=?", (nid,)); db.commit()
    return jsonify({"deleted": nid})

@app.route("/api/trash", methods=["DELETE"])
def trash_empty():
    db = get_db()
    n = 0
    for r in db.execute("SELECT files FROM trash").fetchall():
        _trash_purge_files(json.loads(r["files"] or "[]")); n += 1
    db.execute("DELETE FROM trash"); db.commit()
    return jsonify({"emptied": n})

@app.route("/api/notes/<nid>/revisions", methods=["GET"])
def note_revisions(nid):
    db = get_db()
    rows = db.execute(
        "SELECT id, body, kind, created_at FROM revisions "
        "WHERE note_id=? ORDER BY created_at DESC LIMIT 30", (nid,)).fetchall()
    return jsonify({"revisions": [dict(r) for r in rows]})

@app.route("/api/notes/<nid>/revisions/<rid>/restore", methods=["POST"])
def restore_revision(nid, rid):
    """Restore a previous version's text as the note's current body. This is
    non-destructive: the CURRENT body is first snapshotted as a new 'edit'
    revision, so a restore can itself be undone from history. Restoring queues
    re-enrichment (the agent re-processes the restored text like any edit)."""
    db = get_db()
    note = db.execute("SELECT body FROM notes WHERE id=?", (nid,)).fetchone()
    if not note:
        return jsonify({"error": "not found"}), 404
    rev = db.execute("SELECT body FROM revisions WHERE id=? AND note_id=?",
                     (rid, nid)).fetchone()
    if not rev:
        return jsonify({"error": "revision not found"}), 404
    cur_body = note["body"] or ""
    restored = rev["body"] or ""
    if restored == cur_body:
        return jsonify({"ok": True, "unchanged": True})
    # Snapshot the current body first so the restore is reversible.
    db.execute("INSERT INTO revisions (id, note_id, body, kind) VALUES (?,?,?,?)",
               (_nanoid(10), nid, cur_body, "edit"))
    db.execute("UPDATE notes SET body=? WHERE id=?", (restored, nid))
    # Treat the restored text like a user edit: queue re-enrichment, mark edited.
    db.execute("UPDATE note_state SET status='force', failures=0 WHERE note_id=?", (nid,))
    db.commit()
    note_row = db.execute("SELECT * FROM notes WHERE id=?", (nid,)).fetchone()
    _broadcast("note_updated", _note_row(note_row))
    return jsonify({"ok": True, "note": _note_row(note_row)})
