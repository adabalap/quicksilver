"""Import (Keep, Markdown, Quicksilver) and export, with the pre-import backup."""
import os
import secrets
import time
import re
import zipfile
from pathlib import Path
from flask import jsonify, request, abort

import interchange as _ix

from .core import DB_PATH, _broadcast, _nanoid, app, get_db, log


# ── API: Import / Export ─────────────────────────────────────────────────────
IMPORT_DIR = Path(DB_PATH).parent / "import_tmp"
_IMPORT_OPTS = ("skip_archived", "include_trashed", "tag_labels", "enrich", "transcribe")

def _import_opts(src):
    return {k: bool((src or {}).get(k)) for k in _IMPORT_OPTS}

def _import_gc():
    try:
        IMPORT_DIR.mkdir(parents=True, exist_ok=True)
        now = time.time()
        for p in IMPORT_DIR.iterdir():
            if p.is_file() and now - p.stat().st_mtime > 3600:
                p.unlink()
    except Exception:
        pass

def _import_path(token):
    if not re.fullmatch(r"[0-9a-f]{32}", token or ""):
        abort(404)
    p = IMPORT_DIR / f"{token}.zip"
    if not p.exists():
        abort(404)
    return p

def _import_preview(db, path, opts):
    try:
        z = _ix.open_zip(str(path))
    except (zipfile.BadZipFile, ValueError) as e:
        return {"error": str(e) if isinstance(e, ValueError) else "that isn't a readable .zip"}
    with z:
        pl = _ix.plan(z, opts)
        if pl["format"] == "unknown":
            return {"error": "Nothing importable found. Quicksilver reads Google Keep exports "
                             "(takeout.google.com → Keep), its own export, and Markdown/text files."}
        stats = pl["stats"]
        rows = _ix.dedupe(db, pl["rows"], stats)
        return {"format": pl["format"], "to_import": len(rows), "stats": stats,
                "samples": [((r["body"] or "(attachment only)").split("\n", 1)[0][:70]) for r in rows[:5]],
                "attachments": sum(len(r.get("attachments", [])) for r in rows)}

@app.route("/api/import/upload", methods=["POST"])
def import_upload():
    """Step 1: stash the upload, return a token and a preview. Nothing is imported."""
    _import_gc()
    IMPORT_DIR.mkdir(parents=True, exist_ok=True)
    files = request.files.getlist("file")
    if not files:
        return jsonify({"error": "choose a file"}), 400
    token = secrets.token_hex(16)
    path = IMPORT_DIR / f"{token}.zip"
    first = files[0]
    name = (first.filename or "").lower()
    if len(files) == 1 and name.endswith(".zip"):
        first.save(path)
    else:                                       # loose .md / .txt files → wrap in a zip
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            for i, f in enumerate(files[:2000]):
                nm = re.sub(r"[^\w.\- ]", "_", Path(f.filename or f"note{i}.md").name) or f"note{i}.md"
                if not nm.lower().endswith((".md", ".markdown", ".txt")):
                    continue
                zf.writestr(nm, f.read(2_000_000))
    opts = _import_opts(request.form)
    pv = _import_preview(get_db(), path, opts)
    if "error" in pv:
        path.unlink(missing_ok=True)
        return jsonify(pv), 400
    pv["token"] = token
    return jsonify(pv)

@app.route("/api/import/<token>/preview", methods=["POST"])
def import_repreview(token):
    path = _import_path(token)
    pv = _import_preview(get_db(), path, _import_opts(request.get_json(silent=True)))
    return (jsonify(pv), 400) if "error" in pv else jsonify(pv)

@app.route("/api/import/<token>/commit", methods=["POST"])
def import_commit(token):
    path = _import_path(token)
    db = get_db()
    opts = _import_opts(request.get_json(silent=True))
    try:
        z = _ix.open_zip(str(path))
    except Exception:
        abort(400)
    with z:
        pl = _ix.plan(z, opts)
        rows = _ix.dedupe(db, pl["rows"], pl["stats"])
        if not rows:
            path.unlink(missing_ok=True)
            return jsonify({"imported": 0, "attachments": 0, "attachments_failed": 0, "tasks": 0})
        _quick_backup("pre-import")
        res = _ix.commit(db, DB_PATH, z, rows, pl["format"], opts, _nanoid)
    path.unlink(missing_ok=True)
    _broadcast("note_created", {"bulk": res["imported"]})
    log.info(f"imported {res['imported']} note(s) from {pl['format']} ({res['attachments']} attachment(s))")
    return jsonify(res)

def _quick_backup(label):
    """Safety copy before any bulk write. Never blocks the operation."""
    try:
        import sqlite3
        bdir = Path(DB_PATH).parent / "backups"
        bdir.mkdir(parents=True, exist_ok=True)
        dest = bdir / f"{label}-{time.strftime('%Y%m%d-%H%M%S')}.db"
        src = sqlite3.connect(str(DB_PATH)); dst = sqlite3.connect(str(dest))
        with dst:
            src.backup(dst)
        src.close(); dst.close()
    except Exception as e:
        log.warning(f"pre-import backup skipped: {e}")

@app.route("/api/export", methods=["GET"])
def export_all():
    from flask import send_file, after_this_request
    db = get_db()
    path = _ix.build_export(db, DB_PATH,
                            include_hidden=request.args.get("hidden", "1") != "0",
                            include_media=request.args.get("media", "1") != "0")
    @after_this_request
    def _rm(resp):
        try: os.unlink(path)
        except OSError: pass
        return resp
    return send_file(path, mimetype="application/zip", as_attachment=True,
                     download_name=f"quicksilver-export-{time.strftime('%Y-%m-%d')}.zip")
