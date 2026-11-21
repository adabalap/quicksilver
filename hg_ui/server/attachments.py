"""Photos, voice notes, sketches and videos: upload, serve, edit, thumbnails, transcription requests."""
import json
import time
from pathlib import Path
from flask import jsonify, request, abort

import mediastore as _ms

from .core import DB_PATH, _broadcast, app, get_db, log
from .notes_common import _note_row


# ── API: Attachments (photos, voice notes, sketches) ─────────────────────────
MAX_ATTACH_PER_NOTE = 24

def _att_or_404(db, aid):
    row = db.execute("SELECT * FROM attachments WHERE id=?", (aid,)).fetchone()
    if not row:
        abort(404)
    return row

def _note_changed(db, nid):
    row = db.execute("SELECT * FROM notes WHERE id=?", (nid,)).fetchone()
    if row:
        _broadcast("note_updated", _note_row(row))

def _read_upload(db, nid, kind, replacing=None):
    """Shared by create + replace. Returns (aid, filename, size, meta)."""
    f = request.files.get("file")
    if not f:
        raise _ms.MediaError("no file in the upload")
    if kind not in _ms.KINDS:
        raise _ms.MediaError("kind must be image, audio, sketch or video")
    try:
        meta = json.loads(request.form.get("meta") or "{}")
        if not isinstance(meta, dict): meta = {}
    except Exception:
        meta = {}
    mime = f.mimetype or request.form.get("mime") or ""
    if replacing:
        aid = replacing
    else:
        cand = str(meta.get("id") or "")
        aid = cand if _ms.valid_id(cand) else _ms.new_id()
    fn, size = _ms.save_stream(DB_PATH, aid, kind, mime, f.stream)
    return aid, fn, size, meta, _ms.norm_mime(mime)

def _num(v, lo, hi, cast=float):
    try:
        x = cast(v)
        return x if lo <= x <= hi else None
    except Exception:
        return None

@app.errorhandler(_ms.MediaError)
def _media_err(e):
    return jsonify({"error": str(e)}), e.status

@app.route("/api/notes/<nid>/attachments", methods=["POST"])
def add_attachment(nid):
    db = get_db()
    if not db.execute("SELECT 1 FROM notes WHERE id=?", (nid,)).fetchone():
        abort(404)
    kind = request.form.get("kind", "")
    cand = ""
    try: cand = str(json.loads(request.form.get("meta") or "{}").get("id") or "")
    except Exception: pass
    if cand and _ms.valid_id(cand):                 # outbox replay: already stored?
        ex = db.execute("SELECT * FROM attachments WHERE id=?", (cand,)).fetchone()
        if ex:
            if ex["note_id"] != nid:
                return jsonify({"error": "attachment id in use"}), 409
            return jsonify(_ms.public(ex)), 200
    n = db.execute("SELECT COUNT(*) FROM attachments WHERE note_id=?", (nid,)).fetchone()[0]
    if n >= MAX_ATTACH_PER_NOTE:
        return jsonify({"error": f"a note can hold {MAX_ATTACH_PER_NOTE} attachments"}), 400
    if kind == "video":
        from .dbinit import migrate_attachments
        if not migrate_attachments(db):
            return jsonify({"error": "The database is busy upgrading. Try the video again in a moment."}), 503
        try: dur = float(json.loads(request.form.get("meta") or "{}").get("duration_s") or 0)
        except Exception: dur = 0
        if dur > _ms.MAX_VIDEO_SECONDS * 1.25:
            return jsonify({"error": f"videos can be up to {_ms.MAX_VIDEO_SECONDS // 60} minutes"}), 413
    aid, fn, size, meta, mime = _read_upload(db, nid, kind)
    strokes = _ms.clean_strokes(meta.get("strokes")) if kind == "sketch" else None
    text = str(meta.get("transcript") or "")[:20000].strip()
    status = "done" if text else ("none" if meta.get("transcribe") is False else "pending")
    db.execute(
        "INSERT INTO attachments (id, note_id, kind, mime, filename, size, width, height, duration_s, "
        "strokes, transcript, transcript_status, transcript_model) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (aid, nid, kind, mime, fn, size, _num(meta.get("width"), 1, 20000, int),
         _num(meta.get("height"), 1, 20000, int), _num(meta.get("duration_s"), 0, 86400),
         strokes, text, status, "browser" if text else None))
    db.commit()
    _note_changed(db, nid)
    _broadcast("attachment_added", {"id": aid, "note_id": nid, "kind": kind})
    return jsonify(_ms.public(db.execute("SELECT * FROM attachments WHERE id=?", (aid,)).fetchone())), 201

@app.route("/api/attachments/<aid>", methods=["PUT"])
def replace_attachment(aid):
    """Re-save a sketch after editing it: new image + strokes, text re-read."""
    db = get_db()
    row = _att_or_404(db, aid)
    if row["kind"] != "sketch":
        return jsonify({"error": "only sketches can be edited"}), 400
    _, fn, size, meta, mime = _read_upload(db, row["note_id"], "sketch", replacing=aid)
    strokes = _ms.clean_strokes(meta.get("strokes"))
    db.execute("UPDATE attachments SET mime=?, filename=?, size=?, width=?, height=?, strokes=?, "
               "transcript='', transcript_status=?, transcript_error=NULL, transcript_model=NULL, "
               "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",
               (mime, fn, size, _num(meta.get("width"), 1, 20000, int),
                _num(meta.get("height"), 1, 20000, int), strokes,
                "none" if meta.get("transcribe") is False else "pending", aid))
    db.commit()
    _note_changed(db, row["note_id"])
    return jsonify(_ms.public(_att_or_404(db, aid)))

@app.route("/api/attachments/<aid>/file", methods=["GET"])
def attachment_file(aid):
    from flask import send_file
    db = get_db()
    row = _att_or_404(db, aid)
    p = _ms.path_for(DB_PATH, row["filename"])
    if not p.exists():
        abort(404)
    resp = send_file(p, mimetype=row["mime"], conditional=True, max_age=31536000)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Cache-Control"] = "private, max-age=31536000, immutable"
    resp.headers["Content-Disposition"] = "inline"
    return resp

@app.route("/api/attachments/<aid>/strokes", methods=["GET"])
def attachment_strokes(aid):
    row = _att_or_404(get_db(), aid)
    return jsonify({"strokes": json.loads(row["strokes"]) if row["strokes"] else None,
                    "width": row["width"], "height": row["height"]})

@app.route("/api/attachments/<aid>", methods=["PATCH"])
def edit_attachment(aid):
    """The user corrects (or writes) the transcript by hand."""
    db = get_db()
    row = _att_or_404(db, aid)
    data = request.get_json(force=True) or {}
    if "transcript" not in data:
        return jsonify({"error": "nothing to change"}), 400
    text = str(data.get("transcript") or "")[:20000].strip()
    db.execute("UPDATE attachments SET transcript=?, transcript_status='done', transcript_error=NULL, "
               "transcript_model='user', updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",
               (text, aid))
    db.commit()
    _note_changed(db, row["note_id"])
    return jsonify(_ms.public(_att_or_404(db, aid)))

@app.route("/api/attachments/<aid>", methods=["DELETE"])
def delete_attachment(aid):
    db = get_db()
    row = _att_or_404(db, aid)
    db.execute("DELETE FROM attachments WHERE id=?", (aid,))
    db.commit()
    _ms.remove_files(DB_PATH, [row["filename"]])
    _note_changed(db, row["note_id"])
    return jsonify({"deleted": aid})

@app.route("/api/attachments/<aid>/retranscribe", methods=["POST"])
def retranscribe_attachment(aid):
    db = get_db()
    row = _att_or_404(db, aid)
    db.execute("UPDATE attachments SET transcript_status='pending', transcript_error=NULL, "
               "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?", (aid,))
    db.commit()
    _broadcast("attachment_added", {"id": aid, "note_id": row["note_id"], "kind": row["kind"]})
    _note_changed(db, row["note_id"])
    return jsonify(_ms.public(_att_or_404(db, aid)))

@app.route("/api/media/summary", methods=["GET"])
def media_summary():
    db = get_db()
    r = db.execute("SELECT COUNT(*) c, COALESCE(SUM(size),0) b, "
                   "SUM(transcript_status IN ('pending','running')) p, "
                   "SUM(transcript_status='failed') f, SUM(transcript_status='skipped') s "
                   "FROM attachments").fetchone()
    by = {k["kind"]: k["c"] for k in db.execute("SELECT kind, COUNT(*) c FROM attachments GROUP BY kind")}
    return jsonify({"count": r["c"], "bytes": r["b"], "pending": r["p"] or 0,
                    "failed": r["f"] or 0, "skipped": r["s"] or 0, "by_kind": by})

def _sweep_orphan_files():
    """Delete attachment files whose row is gone (e.g. after `hg_ui clear`),
    but only ones older than a day so an in-flight upload is never touched."""
    try:
        d = _ms.att_dir(DB_PATH)
        with app.app_context():
            keep = {r["filename"] for r in get_db().execute("SELECT filename FROM attachments")}
        now = time.time()
        n = 0
        for p in d.iterdir():
            if p.is_file() and p.name not in keep and now - p.stat().st_mtime > 86400:
                p.unlink(); n += 1
        if n:
            log.info(f"removed {n} orphaned attachment file(s)")
    except Exception as e:
        log.debug(f"orphan sweep skipped: {e}")

# ── Thumbnails ───────────────────────────────────────────────────────────────
THUMB_PX = 360

def _make_thumb(src: Path, dst: Path, video: bool = False) -> bool:
    """Small JPEG for list cards. Pillow if present, else ffmpeg; False = give up."""
    if video:
        return _video_thumb(src, dst)
    try:
        from PIL import Image, ImageOps
        with Image.open(src) as im:
            im = ImageOps.exif_transpose(im)
            im.thumbnail((THUMB_PX, THUMB_PX))
            if im.mode in ("RGBA", "LA", "P"):
                im = im.convert("RGBA")
                bg = Image.new("RGB", im.size, (255, 255, 255))
                bg.paste(im, mask=im.split()[-1]); im = bg
            else:
                im = im.convert("RGB")
            im.save(dst, "JPEG", quality=72, optimize=True)
        return True
    except ImportError:
        pass
    except Exception as e:
        log.debug(f"thumb (Pillow) failed for {src.name}: {e}")
    import shutil, subprocess
    ff = shutil.which("ffmpeg")
    if not ff:
        return False
    try:
        r = subprocess.run([ff, "-y", "-loglevel", "error", "-i", str(src), "-frames:v", "1", "-vf",
                            f"scale='if(gt(iw,ih),min({THUMB_PX},iw),-2)':'if(gt(iw,ih),-2,min({THUMB_PX},ih))'",
                            "-q:v", "5", str(dst)], capture_output=True, timeout=30,
                           stdin=subprocess.DEVNULL)
        return r.returncode == 0 and dst.exists() and dst.stat().st_size > 0
    except Exception:
        return False

def _video_thumb(src: Path, dst: Path) -> bool:
    """One frame about a second in (the first frame is often black), scaled for cards."""
    import shutil, subprocess
    ff = shutil.which("ffmpeg")
    if not ff:
        return False
    vf = f"scale='if(gt(iw,ih),min({THUMB_PX},iw),-2)':'if(gt(iw,ih),-2,min({THUMB_PX},ih))'"
    for ss in ("1", "0"):
        try:
            r = subprocess.run([ff, "-y", "-loglevel", "error", "-ss", ss, "-i", str(src), "-frames:v", "1",
                                "-vf", vf, "-q:v", "5", str(dst)], capture_output=True, timeout=45, stdin=subprocess.DEVNULL)
            if r.returncode == 0 and dst.exists() and dst.stat().st_size > 0:
                return True
        except Exception:
            pass
    return False

@app.route("/api/attachments/<aid>/thumb", methods=["GET"])
def attachment_thumb(aid):
    """A ~360 px JPEG of a photo or sketch for cards (made once, then cached on disk).
    Falls back to the original when no image tool is available."""
    from flask import send_file, redirect
    db = get_db()
    row = _att_or_404(db, aid)
    if row["kind"] not in ("image", "sketch", "video"):
        abort(404)
    src = _ms.path_for(DB_PATH, row["filename"])
    if not src.exists():
        abort(404)
    tdir = _ms.att_dir(DB_PATH) / "_thumbs"
    dst = tdir / f"{row['id']}.jpg"
    if not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime:
        tdir.mkdir(parents=True, exist_ok=True)
        if not _make_thumb(src, dst, video=(row["kind"] == "video")):
            return redirect(f"/api/attachments/{aid}/file")
    resp = send_file(dst, mimetype="image/jpeg", conditional=True)
    resp.headers["Cache-Control"] = "private, max-age=86400"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp
