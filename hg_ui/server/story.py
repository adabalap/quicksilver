"""Story from a photo: request endpoint."""
import json
from flask import jsonify, request, abort

from .core import _broadcast, _nanoid, app, get_db


# ── Story from a photo ───────────────────────────────────────────────────────
STORY_GENRES = {"fairy tale", "mystery", "funny", "heartfelt", "adventure", "sci-fi"}

@app.route("/api/notes/<nid>/story", methods=["POST"])
def story_request(nid):
    """Ask the agent for a story inspired by this note's photo(s): built from the
    description the model already wrote, plus an optional outline from the user."""
    db = get_db()
    note = db.execute("SELECT * FROM notes WHERE id=?", (nid,)).fetchone()
    if not note:
        abort(404)
    d = request.get_json(force=True) or {}
    outline = str(d.get("outline") or "").strip()[:600]
    genre = str(d.get("genre") or "").strip().lower()
    genre = genre if genre in STORY_GENRES else ""
    length = "medium" if d.get("length") == "medium" else "short"
    lang = "te" if d.get("lang") == "te" else "en"
    revise = str(d.get("revise") or "").strip()[:6000]
    instruction = str(d.get("instruction") or "").strip()[:300]
    if revise and not instruction:
        return jsonify({"error": "say what to change"}), 400
    atts = db.execute("SELECT kind, transcript, transcript_status FROM attachments "
                      "WHERE note_id=? AND kind IN ('image','sketch') ORDER BY created_at", (nid,)).fetchall()
    if not atts:
        return jsonify({"error": "this note has no photo or sketch"}), 400
    parts = [(a["transcript"] or "").strip() for a in atts]
    parts = [t for t in parts if t]
    body = (note["body"] or "").strip()[:600]
    if not parts and not revise:
        pending = any(a["transcript_status"] in ("pending", "running") for a in atts)
        # No description (photos are only OCR'd unless a vision model is set up): the user's own
        # idea or the note's words are enough to write from. Only refuse when there is nothing at all.
        if pending or not (outline or len(body.split()) >= 3):
            return jsonify({"error": "the photo hasn't been described yet" if pending
                            else "there's no description of this photo to write from",
                            "pending": pending}), 409
    desc = "\n\n".join(parts)[:1800]
    ctx = [{"id": nid, "title": "Photo", "text": desc}]
    rid = _nanoid(12)
    db.execute("""INSERT INTO ask_requests (id, question, context_json, status, history_json, mode)
                  VALUES (?,?,?,'pending',?,'story')""",
               (rid, instruction if revise else outline, json.dumps(ctx),
                json.dumps({"genre": genre, "length": length, "lang": lang, "note_text": body,
                            **({"revise": revise, "outline": outline} if revise else {})}), ))
    db.commit()
    _broadcast("ask_requested", {"request_id": rid})
    return jsonify({"request_id": rid, "status": "pending"}), 201
