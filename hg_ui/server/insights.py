"""Saved searches, weekly digest and duplicate suggestions."""
import time
import sqlite3
from flask import jsonify, request, abort

from .attachments import _note_changed
from .core import (MAX_BODY_CHARS, _broadcast, _hash, _nanoid, _settings_json, _settings_put, app, get_db, log)
from .notes_common import _FILTER_TYPES
from .trash import _trash_put


# ── Saved searches ───────────────────────────────────────────────────────────
@app.route("/api/saved-searches", methods=["GET"])
def saved_searches_list():
    return jsonify({"items": _settings_json(get_db(), "saved_searches", [])})

@app.route("/api/saved-searches", methods=["POST"])
def saved_searches_add():
    db = get_db()
    d = request.get_json(force=True) or {}
    item = {"id": _nanoid(8),
            "name": str(d.get("name") or "").strip()[:40],
            "q": str(d.get("q") or "").strip()[:200],
            "type": d.get("type") if d.get("type") in _FILTER_TYPES else "",
            "range": d.get("range") if d.get("range") in ("today", "week", "month", "year") else "",
            "tag": str(d.get("tag") or "").strip().lstrip("#").lower()[:40]}
    if not (item["q"] or item["type"] or item["range"] or item["tag"]):
        return jsonify({"error": "nothing to save — set a search or a filter first"}), 400
    if not item["name"]:
        item["name"] = (item["q"] or item["tag"] and "#" + item["tag"] or item["type"] or item["range"]).strip()[:40]
    items = _settings_json(db, "saved_searches", [])
    items = [i for i in items if i.get("name", "").lower() != item["name"].lower()]
    items.insert(0, item)
    _settings_put(db, "saved_searches", items[:20])
    db.commit()
    return jsonify({"item": item, "items": items[:20]}), 201

@app.route("/api/saved-searches/<sid>", methods=["DELETE"])
def saved_searches_delete(sid):
    db = get_db()
    items = [i for i in _settings_json(db, "saved_searches", []) if i.get("id") != sid]
    _settings_put(db, "saved_searches", items)
    db.commit()
    return jsonify({"items": items})

# ── Weekly digest ("Your week") ──────────────────────────────────────────────
DIGEST_FRESH_DAYS = 8

def _digest_for_home(db):
    d = _settings_json(db, "weekly_digest", {})
    if not d.get("ts") or time.time() - float(d["ts"]) > DIGEST_FRESH_DAYS * 86400:
        return None
    if _settings_json(db, "digest_dismissed", {}).get("week") == d.get("week"):
        return None
    return {"week": d.get("week"), "text": d.get("text", ""), "counts": d.get("counts", {})}

@app.route("/api/digest", methods=["GET"])
def digest_get():
    d = _settings_json(get_db(), "weekly_digest", {})
    return jsonify({"digest": d or None})

@app.route("/api/digest/refresh", methods=["POST"])
def digest_refresh():
    """Ask the agent to write a fresh digest now (it picks the flag up on its next tick)."""
    db = get_db()
    db.execute(
        "INSERT INTO settings (key, value) VALUES ('digest_refresh_requested','1') ON CONFLICT(key) "
        "DO UPDATE SET value='1'")
    db.commit()
    return jsonify({"ok": True})

@app.route("/api/digest/dismiss", methods=["POST"])
def digest_dismiss():
    db = get_db()
    d = _settings_json(db, "weekly_digest", {})
    _settings_put(db, "digest_dismissed", {"week": d.get("week")})
    db.commit()
    return jsonify({"ok": True})

# ── Duplicate suggestions ────────────────────────────────────────────────────
def _dup_note(db, nid):
    r = db.execute("SELECT n.id, n.body, n.created_at, "
                   "(SELECT title FROM enrichments WHERE note_id=n.id ORDER BY enriched_at DESC, rowid DESC LIMIT 1) t, "
                   "(SELECT COUNT(*) FROM attachments WHERE note_id=n.id) atts "
                   "FROM notes n WHERE n.id=? AND n.archived=0", (nid,)).fetchone()
    if not r:
        return None
    body = (r["body"] or "").strip()
    return {"id": r["id"], "title": (r["t"] or body.replace("\n", " ")[:48] or "Untitled"),
            "preview": body[:240], "length": len(body), "created_at": r["created_at"],
            "attachments": r["atts"]}

def _pending_dups(db, limit=20):
    try:
        rows = db.execute("SELECT * FROM dup_suggestions WHERE status='pending' "
                          "ORDER BY score DESC, id LIMIT ?", (limit * 3,)).fetchall()
    except sqlite3.OperationalError:
        return []
    out = []
    for r in rows:
        a, b = _dup_note(db, r["note_a"]), _dup_note(db, r["note_b"])
        if not a or not b:
            db.execute("UPDATE dup_suggestions SET status='dismissed' WHERE id=?", (r["id"],))
            continue
        out.append({"id": r["id"], "score": r["score"], "a": a, "b": b})
        if len(out) >= limit:
            break
    db.commit()
    return out

@app.route("/api/duplicates", methods=["GET"])
def duplicates_list():
    return jsonify({"items": _pending_dups(get_db())})

@app.route("/api/duplicates/<int:did>/dismiss", methods=["POST"])
def duplicates_dismiss(did):
    db = get_db()
    db.execute("UPDATE dup_suggestions SET status='dismissed' WHERE id=?", (did,))
    db.commit()
    return jsonify({"ok": True})

@app.route("/api/duplicates/<int:did>/merge", methods=["POST"])
def duplicates_merge(did):
    """Fold one note into the other: its text is added below (unless it is the same
    text), tags / tasks / reminders / attachments move across, and the folded note goes
    to Trash — so a merge can be undone from there."""
    db = get_db()
    row = db.execute("SELECT * FROM dup_suggestions WHERE id=? AND status='pending'", (did,)).fetchone()
    if not row:
        abort(404)
    keep = (request.get_json(force=True) or {}).get("keep")
    if keep not in (row["note_a"], row["note_b"]):
        return jsonify({"error": "keep must be one of the two notes"}), 400
    drop = row["note_b"] if keep == row["note_a"] else row["note_a"]
    kn = db.execute("SELECT * FROM notes WHERE id=?", (keep,)).fetchone()
    dn = db.execute("SELECT * FROM notes WHERE id=?", (drop,)).fetchone()
    if not kn or not dn:
        abort(404)
    kb, dbody = (kn["body"] or "").strip(), (dn["body"] or "").strip()
    same = " ".join(kb.lower().split()) == " ".join(dbody.lower().split()) or dbody.lower() in kb.lower()
    new_body = kb if same else (kb + "\n\n" + dbody).strip()
    if len(new_body) > MAX_BODY_CHARS:
        return jsonify({"error": "the merged note would be too long"}), 413
    try:
        if new_body != kb:
            db.execute("INSERT INTO revisions (id, note_id, body, kind) VALUES (?,?,?,?)",
                       (_nanoid(10), keep, kn["body"] or "", "edit"))
            db.execute("UPDATE notes SET body=? WHERE id=?", (new_body, keep))
            db.execute("INSERT INTO note_state (note_id, status, body_hash) VALUES (?,?,?) "
                       "ON CONFLICT(note_id) DO UPDATE SET status='force', body_hash=excluded.body_hash",
                       (keep, "force", _hash(new_body)))
        db.execute("INSERT OR IGNORE INTO tags (note_id, tag) SELECT ?, tag FROM tags WHERE note_id=?", (keep, drop))
        db.execute("UPDATE action_items SET note_id=? WHERE note_id=?", (keep, drop))
        db.execute("UPDATE attachments SET note_id=? WHERE note_id=?", (keep, drop))
        db.execute("UPDATE reminders SET note_id=? WHERE note_id=?", (keep, drop))
        _trash_put(db, drop, [])               # snapshot of the folded note (its attachments moved already)
        db.execute("DELETE FROM notes WHERE id=?", (drop,))
        db.execute("UPDATE dup_suggestions SET status='merged' WHERE id=?", (did,))
        db.execute("UPDATE dup_suggestions SET status='dismissed' WHERE status='pending' "
                   "AND (note_a=? OR note_b=?)", (drop, drop))
        db.commit()
    except Exception as e:
        db.rollback()
        log.error(f"merge {drop} → {keep} failed: {e}")
        return jsonify({"error": "couldn't merge these notes"}), 500
    _broadcast("note_deleted", {"id": drop})
    _note_changed(db, keep)
    return jsonify({"ok": True, "kept": keep, "folded": drop})
