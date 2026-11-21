"""app.py — Quicksilver PWA backend (Flask + SQLite).

Runs entirely on-device in Termux. The AI daemon reads/writes the same SQLite
file directly — no HTTP round-trips between daemon and app, no content parsing.

Start:  python app.py
        python app.py --port 7070 --db /data/quicksilver.db

This file is only the entry point. The server itself lives in the `server/` package,
one module per area (notes, trash, attachments, search, ask, insights, system …);
see server/__init__.py for the map. Importing each module registers its routes.
"""
import os, sys, hashlib, sqlite3          # sqlite3: also part of the compat surface used by tests
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import request, send_from_directory, make_response

import server
from server.core import app, log, DEBUG, HOST, PORT
import importlib
for _name in server.MODULES[1:]:                 # registers every route
    importlib.import_module(f"server.{_name}")

# Compatibility surface: tests and the command-line tools reach into this module
# (qs.get_db, qs._retrieve, qs.init_db …). Re-export every name from every module;
# patching a function in a test should target the module that owns it (qs.search …).
for _name in server.MODULES:
    _m = sys.modules[f"server.{_name}"]
    globals()[_name] = _m
    for _k, _v in vars(_m).items():
        if not _k.startswith("__") and _k not in globals():
            globals()[_k] = _v

from server.dbinit import init_db
from server.attachments import _sweep_orphan_files
from server.cli import _data_ops, _set_process_title


# ── Shell (serves the PWA) ────────────────────────────────────────────────────
@app.route("/")
@app.route("/<path:path>")
def shell(path=""):
    # Serve static files directly
    if path and (Path(app.static_folder) / path).is_file():
        resp = send_from_directory(app.static_folder, path)
        if request.args.get("v") and path.startswith(("js/", "css/")):
            # The URL carries the build token, so it can be cached for good; a new
            # build means a new URL.
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return resp
    # Everything else → PWA shell
    return _shell_response()

def _asset_version() -> str:
    """Short token that changes whenever any page, script or stylesheet changes.
    Put on every /js and /css URL so the browser and the service worker can cache
    them hard without ever serving a stale mix after an update."""
    root = Path(app.static_folder)
    h = hashlib.sha256()
    for f in sorted([root / "index.html", *root.glob("js/*.js"), *root.glob("css/*.css")]):
        try:
            st = f.stat()
            h.update(f"{f.name}:{st.st_mtime_ns}:{st.st_size};".encode())
        except OSError:
            pass
    return h.hexdigest()[:10]

def _shell_response():
    html = (Path(app.static_folder) / "index.html").read_text(encoding="utf-8")
    resp = make_response(html.replace("__V__", _asset_version()))
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    resp.headers["Cache-Control"] = "no-cache"
    return resp


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("backup", "restore", "clear",
                                             "dedupe", "import-keep"):
        _data_ops(sys.argv)
        sys.exit(0)
    _set_process_title()
    init_db()
    _sweep_orphan_files()
    force_dev = "--dev" in sys.argv or DEBUG
    if not force_dev:
        try:
            from waitress import serve
            log.info(f"Quicksilver PWA (production · waitress) on http://{HOST}:{PORT}")
            # threads: enough for the browser + the agent's SSE stream + API.
            # channel_timeout kept high so long-lived SSE connections aren't cut.
            serve(app, host=HOST, port=PORT, threads=8,
                  channel_timeout=3600, ident="Quicksilver")
            sys.exit(0)
        except ImportError:
            log.warning("waitress not installed — falling back to the Flask dev "
                        "server. For production run: pip install waitress")
    log.info(f"Quicksilver PWA (dev server) on http://{HOST}:{PORT}")
    app.run(host=HOST, port=PORT, debug=DEBUG, threaded=True,
            use_reloader=False)