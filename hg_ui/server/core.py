"""Shared foundation: the Flask app, config, database access, ids/time helpers and the live-event (SSE) stream."""
import os
import json
import hashlib
import secrets
import logging
import re
import random
import string as _string
import queue as _queue
from datetime import datetime, timezone
from pathlib import Path
from flask import Flask, g, request


def _nanoid(size=8):
    alphabet = _string.ascii_letters + _string.digits
    return "".join(random.choices(alphabet, k=size))

# ── Config ────────────────────────────────────────────────────────────────────
DB_PATH   = os.environ.get("QS_DB",   Path.home() / ".quicksilver" / "notes.db")
PORT      = int(os.environ.get("QS_PORT", 5230))
HOST      = os.environ.get("QS_HOST", "127.0.0.1")
DEBUG     = os.environ.get("QS_DEBUG", "0") == "1"

Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)

# How stale the agent's liveness stamp may be before the UI calls it "down".
# The agent stamps liveness on every main-loop tick (default 60s, and operators
# often raise that for battery), so a 60s window flickered or read "down"
# permanently. 210s tolerates a 60s tick plus a slow cycle without lying about a
# genuinely stopped agent.
AGENT_ALIVE_SECONDS = 210

# The project root (where schema.sql, static/ and the helper modules live) is one level up.
BASE_DIR = Path(__file__).resolve().parent.parent
app = Flask(__name__, root_path=str(BASE_DIR), static_folder="static", template_folder="templates")

@app.after_request
def _cors(resp):
    origin = request.headers.get("Origin")
    if origin:
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Vary"] = "Origin"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, DELETE, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        resp.headers["Access-Control-Max-Age"] = "86400"   # cache the preflight a day
    return resp

MAX_BODY_CHARS = 100_000  # ~100k chars; guards against accidental huge pastes
app.secret_key = os.environ.get("QS_SECRET", secrets.token_hex(32))

logging.basicConfig(level=logging.DEBUG if DEBUG else logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")

log = logging.getLogger("quicksilver")

# ── Database ──────────────────────────────────────────────────────────────────
def get_db():
    if "db" not in g:
        import sqlite3
        db = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30.0)
        db.row_factory = sqlite3.Row
        # Wait up to 30s for a lock rather than failing instantly. The agent
        # writes to the same file; without this, any momentary overlap raises
        # "database is locked" and — during init_db — crashes the whole server.
        db.execute("PRAGMA busy_timeout=30000")
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        g.db = db
    return g.db

@app.teardown_appcontext
def close_db(_):
    db = g.pop("db", None)
    if db: db.close()

# The "touch updated_at on any UPDATE" trigger must NOT fire for embedding
# bookkeeping. updated_at means "the user's content changed": it drives the list
# sort, the agent's change watermark (re-enrichment!) and "what's new". The
# agent rewrites notes.embedding/embed_model during (re)indexing, and with the
# naive trigger that silently re-dated every note and re-queued them all.
_NOTES_UPDATED_TRIGGER = (
    "CREATE TRIGGER notes_updated AFTER UPDATE ON notes "
    "WHEN NEW.embedding IS OLD.embedding AND NEW.embed_model IS OLD.embed_model "
    "BEGIN UPDATE notes SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') "
    "WHERE id = NEW.id; END;")

def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]

_sse_clients: list[_queue.Queue] = []
_sse_lock = __import__("threading").Lock()

def _broadcast(event: str, data: dict):
    msg = f"event: {event}\ndata: {json.dumps(data)}\n\n"
    with _sse_lock:
        dead = []
        for q in _sse_clients:
            try: q.put_nowait(msg)
            except Exception: dead.append(q)   # full/closed queue → prune it
        for q in dead: _sse_clients.remove(q)

@app.route("/api/events")
def sse_stream():
    q = _queue.Queue(maxsize=50)
    with _sse_lock:
        _sse_clients.append(q)
    def gen():
        try:
            yield "event: connected\ndata: {}\n\n"
            while True:
                try:
                    msg = q.get(timeout=25)
                    yield msg
                except _queue.Empty:
                    yield ": heartbeat\n\n"
        finally:
            with _sse_lock:
                if q in _sse_clients:
                    _sse_clients.remove(q)
    from flask import Response, stream_with_context
    return Response(stream_with_context(gen()),
                    mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache",
                             "X-Accel-Buffering": "no"})

# ═══ Home strips: what's new · agent status · resurfacing ════════════════════
# One cheap read-only call the Notes home makes on open / resume. Everything is
# derived from tables we already have — no model call, no new writes except the
# tiny resurface_state row when the user dismisses a card.
_ISO_TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")

def _iso_ago(seconds: float) -> str:
    return (datetime.now(timezone.utc) - __import__("datetime").timedelta(seconds=seconds)
            ).strftime("%Y-%m-%dT%H:%M:%S.000Z")

def _settings_json(db, key, default):
    row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    if not row:
        return default
    try:
        v = json.loads(row["value"])
        return v if isinstance(v, type(default)) else default
    except Exception:
        return default

def _settings_put(db, key, value):
    db.execute("INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) "
               "DO UPDATE SET value=excluded.value, "
               "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')", (key, json.dumps(value)))
