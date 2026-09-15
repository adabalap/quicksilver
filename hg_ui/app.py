"""app.py — Quicksilver PWA backend (Flask + SQLite).

Runs entirely on-device in Termux. The AI daemon reads/writes the same SQLite
file directly — no HTTP round-trips between daemon and app, no content parsing.

Start:  python app.py
        python app.py --port 7070 --db /data/quicksilver.db
"""

import os, sys, json, hashlib, secrets, time, logging, re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from functools import wraps

from flask import (Flask, g, jsonify, request, send_from_directory,
                   render_template_string, abort)

# ── Tiny nanoid implementation (no npm needed) ─────────────────────────────
import random, string as _string
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

app = Flask(__name__, static_folder="static", template_folder="templates")

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

def init_db():
    schema = Path(__file__).parent / "schema.sql"
    if schema.exists():
        with app.app_context():
            db = get_db()
            # The agent writes to the same file. init_db must NEVER crash the
            # server on a momentary lock — a crashed UI is a blank app. Retry the
            # schema script a few times (busy_timeout already waits 30s each),
            # then, if still locked, carry on: the schema is CREATE-IF-NOT-EXISTS
            # so an already-initialised DB doesn't need it.
            import time as _t
            for _attempt in range(5):
                try:
                    db.executescript(schema.read_text())
                    break
                except sqlite3.OperationalError as e:
                    if "locked" in str(e).lower() and _attempt < 4:
                        log.warning(f"init_db: DB busy, retrying schema "
                                    f"({_attempt+1}/5)…")
                        _t.sleep(1.5)
                        continue
                    log.warning(f"init_db: schema script skipped ({e}); "
                                f"assuming DB already initialised")
                    break
            # Migrations for pre-existing databases (idempotent)
            # note_state.status gained a 'dirty' value. A CHECK constraint can't
            # be altered in place, so if an old DB still has the 4-value check,
            # rebuild the table with the widened constraint. Detected by probing
            # whether 'dirty' is accepted; the rebuild preserves all rows.
            #
            # This rebuild takes an EXCLUSIVE lock (DROP/RENAME), the most
            # lock-prone thing in startup. If the agent is mid-write we DEFER it
            # rather than crash — it's retried on the next UI start, and until
            # then the only cost is that 'dirty' writes fall back (handled below).
            try:
                _chk = db.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' "
                    "AND name='note_state'").fetchone()
                if _chk and "'dirty'" not in (_chk[0] or ""):
                    # Rebuild with the FULL column set. The prior version of this
                    # migration recreated only 4 of the 6 columns, dropping
                    # `failures` and `updated_at` — which both processes read,
                    # breaking every note load. Copy every column that exists on
                    # the old table (older DBs may legitimately lack the newer
                    # ones), defaulting the rest.
                    _cols = {r[1] for r in db.execute(
                        "PRAGMA table_info(note_state)").fetchall()}
                    _has_fail = "failures" in _cols
                    _has_upd  = "updated_at" in _cols
                    db.execute("PRAGMA foreign_keys=off")
                    db.execute("BEGIN IMMEDIATE")
                    db.execute("""
                        CREATE TABLE note_state__new (
                            note_id    TEXT PRIMARY KEY REFERENCES notes(id) ON DELETE CASCADE,
                            status     TEXT NOT NULL DEFAULT 'raw'
                                       CHECK(status IN ('raw','dirty','pending','approved','force')),
                            body_hash  TEXT,
                            seen_at    TEXT,
                            failures   INTEGER NOT NULL DEFAULT 0,
                            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                        )""")
                    db.execute(f"""
                        INSERT INTO note_state__new
                            (note_id, status, body_hash, seen_at, failures, updated_at)
                        SELECT note_id, status, body_hash, seen_at,
                               {'failures' if _has_fail else '0'},
                               {'updated_at' if _has_upd else "strftime('%Y-%m-%dT%H:%M:%fZ','now')"}
                        FROM note_state""")
                    db.execute("DROP TABLE note_state")
                    db.execute("ALTER TABLE note_state__new RENAME TO note_state")
                    db.execute("COMMIT")
                    db.execute("PRAGMA foreign_keys=on")
                    log.info("migrated note_state: 'dirty' status now allowed")
            except Exception as e:
                log.warning(f"note_state dirty migration skipped: {e}")
            # REPAIR: an earlier version of the migration above rebuilt note_state
            # with only 4 columns, dropping `failures` and `updated_at`. Any DB
            # that ran it now crashes on every note read. Restore the columns via
            # plain ALTER (cheap, no rebuild, no exclusive lock). Idempotent.
            try:
                _ns_cols = {r[1] for r in db.execute(
                    "PRAGMA table_info(note_state)").fetchall()}
                if "failures" not in _ns_cols:
                    db.execute("ALTER TABLE note_state ADD COLUMN "
                               "failures INTEGER NOT NULL DEFAULT 0")
                    log.info("repaired note_state: restored 'failures' column")
                if "updated_at" not in _ns_cols:
                    # SQLite can't ALTER-ADD a column with a non-constant default,
                    # so add it plainly and backfill.
                    db.execute("ALTER TABLE note_state ADD COLUMN updated_at TEXT")
                    db.execute("UPDATE note_state SET updated_at="
                               "strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                               "WHERE updated_at IS NULL")
                    log.info("repaired note_state: restored 'updated_at' column")
            except Exception as e:
                log.warning(f"note_state repair skipped: {e}")
            try:
                db.execute("ALTER TABLE notes ADD COLUMN protected INTEGER NOT NULL DEFAULT 0")
            except Exception:
                pass
            try:
                db.execute("ALTER TABLE notes ADD COLUMN embedding TEXT")
            except Exception:
                pass
            for col, ddl in [("note_type","ALTER TABLE notes ADD COLUMN note_type TEXT NOT NULL DEFAULT 'text'"),
                             ("pinned","ALTER TABLE notes ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0"),
                             # visibility is in the base schema, but add it
                             # defensively so a DB that somehow predates it (or a
                             # hand-restored one) doesn't crash _note_row, which
                             # reads row["visibility"] unconditionally.
                             ("visibility","ALTER TABLE notes ADD COLUMN visibility TEXT NOT NULL DEFAULT 'private'"),
                             ("is_daily","ALTER TABLE notes ADD COLUMN is_daily INTEGER NOT NULL DEFAULT 0"),
                             ("daily_date","ALTER TABLE notes ADD COLUMN daily_date TEXT"),
                             ("archived","ALTER TABLE notes ADD COLUMN archived INTEGER NOT NULL DEFAULT 0"),
                             ("hidden","ALTER TABLE notes ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0"),
                             # enrich_optout: 1 = the agent must NOT auto-enrich this note.
                             # Bulk-imported notes set this so a 500-note import doesn't
                             # queue 500 local Gemma calls. Orthogonal to `status` on
                             # purpose: status is the enrichment LIFECYCLE, this is
                             # consent. An explicit user action (⋯ → Re-run / Polish)
                             # clears it.
                             ("enrich_optout","ALTER TABLE notes ADD COLUMN enrich_optout INTEGER NOT NULL DEFAULT 0"),
                             # Per-note extraction switches. Some notes are
                             # structurally not task lists (a journal entry, a
                             # meeting transcript) — dismissing items one by one
                             # is the wrong tool there. These stop the agent
                             # extracting that kind of output for this note at
                             # all, which also saves output tokens.
                             ("no_actions","ALTER TABLE notes ADD COLUMN no_actions INTEGER NOT NULL DEFAULT 0"),
                             ("no_reminders","ALTER TABLE notes ADD COLUMN no_reminders INTEGER NOT NULL DEFAULT 0"),
                             # Per-note "metadata only" — the middle mode of the
                             # compose control. Agent writes title/summary/tags
                             # but leaves the body verbatim (no polished rewrite).
                             ("meta_only","ALTER TABLE notes ADD COLUMN meta_only INTEGER NOT NULL DEFAULT 0")]:
                try: db.execute(ddl)
                except Exception: pass
            try: db.execute("ALTER TABLE reminders ADD COLUMN source TEXT NOT NULL DEFAULT 'agent'")
            except Exception: pass
            # Dismissal ("this isn't a task" / "don't remind me"), which is a
            # DIFFERENT intent from `done` ("I did it"). Dismissed rows are kept
            # rather than deleted so the agent can avoid re-suggesting the same
            # text on the next enrichment — otherwise the AI's full-replace would
            # resurrect it and the user would dismiss it forever.
            try: db.execute("ALTER TABLE action_items ADD COLUMN dismissed INTEGER NOT NULL DEFAULT 0")
            except Exception: pass
            # Categorized action items (work/personal) + dependency tracking.
            # Plain ALTER, non-destructive, idempotent — safe under contention.
            try: db.execute("ALTER TABLE action_items ADD COLUMN category TEXT NOT NULL DEFAULT ''")
            except Exception: pass
            try: db.execute("ALTER TABLE action_items ADD COLUMN blocked_by TEXT NOT NULL DEFAULT ''")
            except Exception: pass
            try: db.execute("ALTER TABLE reminders ADD COLUMN dismissed INTEGER NOT NULL DEFAULT 0")
            except Exception: pass
            # One-time cleanup: collapse DUPLICATE reminders an earlier
            # title-only dedup let through — same note, same resolved date, and
            # a title that's clearly the same intent reworded ("Income Tax Return
            # Deadline" vs "ITR Filing Deadline", both 2026-07-31). The agent does
            # this semantically going forward; here in the UI (no embedder) we use
            # a conservative lexical test — significant shared word-stems OR one
            # title's acronym matching the other — gated by SAME DATE, so we only
            # merge same-day near-paraphrases and never distinct tasks. Keep the
            # row that has a calendar event; else the earliest.
            try:
                _rows = db.execute("""
                    SELECT id, note_id, title,
                           COALESCE(NULLIF(substr(datetime_resolved,1,10),''),
                                    CASE WHEN datetime_hint GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*'
                                         THEN substr(datetime_hint,1,10) END) AS d,
                           COALESCE(calendar_event_id,'') AS ev,
                           created_at
                    FROM reminders WHERE COALESCE(dismissed,0)=0
                """).fetchall()
                import re as _re2
                _stop = {"the","a","an","of","for","to","on","in","your","my",
                         "deadline","reminder","date","due","filing","file"}
                def _words(t):
                    return {w for w in _re2.findall(r"[a-z0-9]+", (t or "").lower())
                            if w not in _stop and len(w) > 2}
                def _acronym(t):
                    # Acronym of the MEANINGFUL words (skip stopwords like
                    # "deadline"), so "Income Tax Return Deadline" → "itr",
                    # which matches the literal token "ITR".
                    ws = [w for w in _re2.findall(r"[A-Za-z]+", t or "")
                          if w.lower() not in _stop]
                    return "".join(w[0] for w in ws).lower()
                def _tokens(t):
                    return set(_re2.findall(r"[a-z0-9]+", (t or "").lower()))
                def _similar(a, b):
                    wa, wb = _words(a), _words(b)
                    if wa and wb:
                        inter = len(wa & wb)
                        if inter and inter / min(len(wa), len(wb)) >= 0.6:
                            return True
                    # acronym match, BOTH directions: "ITR" ↔ "Income Tax Return".
                    aa, ab = _acronym(a), _acronym(b)
                    ta, tb = _tokens(a), _tokens(b)
                    if aa and len(aa) >= 2 and (aa == ab or aa in tb):
                        return True
                    if ab and len(ab) >= 2 and (ab == aa or ab in ta):
                        return True
                    return False
                from collections import defaultdict as _dd
                _by = _dd(list)
                for r in _rows:
                    if r["d"]:
                        _by[(r["note_id"], r["d"])].append(r)
                for (_nid, _d), grp in _by.items():
                    if len(grp) < 2:
                        continue
                    keep = sorted(grp, key=lambda r: (r["ev"] == "", r["created_at"] or ""))[0]
                    removed = 0
                    for r in grp:
                        if r["id"] == keep["id"] or r["ev"]:
                            continue          # never drop a calendar-linked row
                        if _similar(r["title"], keep["title"]):
                            db.execute("DELETE FROM reminders WHERE id=?", (r["id"],))
                            removed += 1
                    if removed:
                        log.info(f"deduped {removed} reworded reminder(s) for "
                                 f"note {_nid} on {_d}")
            except Exception as e:
                log.warning(f"reminder dedup cleanup skipped: {e}")
            # Set when the user asks to delete the real calendar event too. The
            # UI has no Google credentials — the agent owns them — so the request
            # is queued here and the agent performs it on its next pass.
            try: db.execute("ALTER TABLE reminders ADD COLUMN calendar_delete_requested INTEGER NOT NULL DEFAULT 0")
            except Exception: pass
            # Durable queue for Google Calendar deletions.
            #
            # Deliberately NOT on the reminders row: deleting a note cascades its
            # reminders away, which would orphan the calendar event forever with
            # nothing left to remember it. A standalone table survives that, and
            # gives us somewhere to record attempts and the last error so a
            # failing deletion can be bounded and shown to the user instead of
            # retrying silently forever.
            #
            # event_id is the PRIMARY KEY, which makes enqueueing idempotent —
            # asking twice for the same deletion is harmless.
            try:
                db.execute("""
                    CREATE TABLE IF NOT EXISTS calendar_deletions (
                        event_id     TEXT PRIMARY KEY,
                        note_id      TEXT,
                        title        TEXT,
                        requested_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                        attempts     INTEGER NOT NULL DEFAULT 0,
                        last_error   TEXT,
                        done         INTEGER NOT NULL DEFAULT 0
                    )""")
                db.execute("CREATE INDEX IF NOT EXISTS idx_caldel_pending "
                           "ON calendar_deletions(done, attempts)")
            except Exception: pass
            # Durable marker: this enrichment's polished text was explicitly
            # requested by the user (on-demand Polish). Lets adopt_polished honour
            # the request without re-checking a transient flag.
            try: db.execute("ALTER TABLE enrichments ADD COLUMN user_polish INTEGER NOT NULL DEFAULT 0")
            except Exception: pass
            for ddl in [
                "CREATE TABLE IF NOT EXISTS backlinks (src_id TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE, dst_id TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE, PRIMARY KEY (src_id, dst_id))",
                "CREATE INDEX IF NOT EXISTS idx_backlinks_dst ON backlinks(dst_id)",
                # Note-list sort index. The main list, archive/hidden views, and
                # wiki autocomplete all ORDER BY (pinned DESC, updated_at DESC).
                # Without this SQLite scans the whole table into a temp B-tree on
                # every load — measured at 100k notes: a full sort each time.
                # With it, the ORDER BY is satisfied by an index seek. Covers all
                # three query shapes; kept as a simple (not partial) index so it
                # serves every view, not just the default archived=0/hidden=0 one.
                "CREATE INDEX IF NOT EXISTS idx_notes_list ON notes(pinned DESC, updated_at DESC)",
                "CREATE TABLE IF NOT EXISTS templates (id TEXT PRIMARY KEY, name TEXT NOT NULL, body TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')))",
                # Ask-your-notes (see schema.sql for the full comment on the flow).
                "CREATE TABLE IF NOT EXISTS ask_requests (id TEXT PRIMARY KEY, question TEXT NOT NULL, "
                "context_json TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'pending' "
                "CHECK(status IN ('pending','done','error')), answer TEXT, cited_note_ids TEXT NOT NULL "
                "DEFAULT '[]', error TEXT, created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')), "
                "answered_at TEXT)",
                "CREATE INDEX IF NOT EXISTS idx_ask_status ON ask_requests(status)"]:
                try: db.execute(ddl)
                except Exception: pass
            # Tags are case-insensitive: fold history to lowercase, dedupe
            db.execute("UPDATE OR IGNORE tags SET tag = lower(tag)")
            db.execute("DELETE FROM tags WHERE tag <> lower(tag)")
            db.commit()
            _init_fts(db)
            log.info(f"Database initialised at {DB_PATH}")


# Module flag: does this SQLite build have FTS5? Set by _init_fts. Search
# consults it to pick the FTS path vs the LIKE fallback, so a build WITHOUT FTS5
# (some Termux/Android SQLite builds) still searches, just without ranking.
HAS_FTS5 = False


def _init_fts(db):
    """Create the FTS5 full-text index + sync triggers, if the build supports it.

    Search over note bodies used to be `LIKE %q%` — a full table scan, unranked,
    that degrades linearly (dead at 100k). FTS5 gives an inverted index with BM25
    ranking. But FTS5 is a compile-time option and some SQLite builds lack it, so
    this is entirely optional: on failure we leave HAS_FTS5 False and search
    falls back to LIKE. Never fatal.
    """
    global HAS_FTS5
    try:
        # Body-only index. Keeping it to one column makes the sync triggers
        # trivially correct (no title/tags mismatch to corrupt the external-
        # content index), and body is where nearly all searchable text lives.
        # Title/tags still participate in search via the keyword-id union in the
        # search endpoint, so nothing is lost.
        db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5("
                   "body, content='notes', content_rowid='rowid')")
    except Exception as e:
        log.info(f"FTS5 unavailable ({e}); search will use LIKE fallback.")
        HAS_FTS5 = False
        return
    HAS_FTS5 = True
    # With content='notes', FTS reads the body straight from the notes table;
    # triggers only need to tell it which rowids changed. This is the canonical,
    # corruption-proof external-content pattern from the SQLite docs.
    try:
        db.executescript("""
        CREATE TRIGGER IF NOT EXISTS notes_fts_ai AFTER INSERT ON notes BEGIN
          INSERT INTO notes_fts (rowid, body) VALUES (new.rowid, new.body);
        END;
        CREATE TRIGGER IF NOT EXISTS notes_fts_ad AFTER DELETE ON notes BEGIN
          INSERT INTO notes_fts (notes_fts, rowid, body) VALUES ('delete', old.rowid, old.body);
        END;
        CREATE TRIGGER IF NOT EXISTS notes_fts_au AFTER UPDATE OF body ON notes BEGIN
          INSERT INTO notes_fts (notes_fts, rowid, body) VALUES ('delete', old.rowid, old.body);
          INSERT INTO notes_fts (rowid, body) VALUES (new.rowid, new.body);
        END;
        """)
    except Exception as e:
        log.info(f"FTS triggers not created ({e}).")
    # Backfill from existing notes via 'rebuild' (canonical for external-content
    # FTS). We can't gate on `SELECT COUNT(*) FROM notes_fts` — for an external-
    # content table that returns the CONTENT table's row count even when the
    # index itself is empty, so it would falsely look "already built". Instead we
    # probe the index directly and rebuild if a known-present term finds nothing.
    needs_build = False
    probe = db.execute("SELECT body FROM notes WHERE body != '' LIMIT 1").fetchone()
    if probe:
        body = probe[0] if not hasattr(probe, "keys") else probe["body"]
        term = re.split(r"\W+", (body or "").strip())
        term = term[0] if term and term[0] else ""
        if term:
            try:
                hit = db.execute("SELECT 1 FROM notes_fts WHERE notes_fts MATCH ? LIMIT 1",
                                 (term,)).fetchone()
                needs_build = hit is None
            except Exception:
                needs_build = True
    if needs_build:
        db.execute("INSERT INTO notes_fts (notes_fts) VALUES ('rebuild')")
        db.commit()
    return


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]

# ── SSE event stream (daemon pushes live updates) ─────────────────────────────
import queue as _queue
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

# ── Note helpers ─────────────────────────────────────────────────────────────
def _note_row(row) -> dict:
    """Assemble a full note object from the DB (note + enrichment + tags + actions)."""
    db = get_db()
    nid = row["id"]

    # enrichment
    enr = db.execute(
        "SELECT * FROM enrichments WHERE note_id=? ORDER BY enriched_at DESC LIMIT 1",
        (nid,)).fetchone()

    # state
    st = db.execute("SELECT * FROM note_state WHERE note_id=?", (nid,)).fetchone()

    # tags
    tags = [r["tag"] for r in
            db.execute("SELECT tag FROM tags WHERE note_id=? ORDER BY tag", (nid,))]

    # action items
    actions = [dict(r) for r in
               db.execute("SELECT * FROM action_items WHERE note_id=? AND COALESCE(dismissed,0)=0 "
                          "ORDER BY created_at",
                          (nid,))]

    # relationships
    rels = db.execute("""
        SELECT r.note_id_b as related_id,
               (SELECT title FROM enrichments WHERE note_id = n.id
                ORDER BY enriched_at DESC LIMIT 1) as rel_title,
               n.body as rel_body,
               n.updated_at as rel_updated,
               r.score
        FROM relationships r
        JOIN notes n ON n.id = r.note_id_b
        WHERE r.note_id_a = ?
        ORDER BY n.updated_at DESC, r.score DESC LIMIT 20
    """, (nid,)).fetchall()

    def _rel_label(r):
        t = (r["rel_title"] or "").strip()
        if t:
            return t
        # No title yet — use a short body snippet, never the raw id.
        body = (r["rel_body"] or "").strip().replace("\n", " ")
        return (body[:48] + "…") if len(body) > 48 else (body or "Untitled note")

    # Skip-enrichment opt-out. This is a statement about FUTURE work — "don't
    # spend another ~72s Gemma call on this note" — not about existing content.
    # We deliberately do NOT hide what the agent already produced: the title and
    # tags are what make a note findable in a list of thousands, so hiding them
    # makes the note worse, not cleaner. An earlier version hid everything, which
    # also stranded notes mid-review (nothing visible to approve → stuck in
    # 'pending' forever, with Re-run and Polish both hidden). A toggle about the
    # future must not silently rewrite the past.
    optout = (row["enrich_optout"] if "enrich_optout" in row.keys() else 0)

    return {
        "id":          nid,
        "body":        row["body"],
        "protected":   (row["protected"] if "protected" in row.keys() else 0),
        "pinned":      (row["pinned"] if "pinned" in row.keys() else 0),
        "archived":    (row["archived"] if "archived" in row.keys() else 0),
        "hidden":      (row["hidden"] if "hidden" in row.keys() else 0),
        "enrich_optout": optout,
        "meta_only":   (row["meta_only"] if "meta_only" in row.keys() else 0),
        "no_actions":  (row["no_actions"] if "no_actions" in row.keys() else 0),
        "no_reminders":(row["no_reminders"] if "no_reminders" in row.keys() else 0),
        "reminders":   [dict(r) for r in db.execute(
            "SELECT id, title, datetime_hint, datetime_resolved, calendar_event_id, "
            "source FROM reminders WHERE note_id=? AND COALESCE(dismissed,0)=0 "
            "ORDER BY created_at", (nid,))],
        "note_type":   (row["note_type"] if "note_type" in row.keys() else "text"),
        "is_daily":    (row["is_daily"] if "is_daily" in row.keys() else 0),
        "user_edited": bool(db.execute(
            "SELECT 1 FROM revisions WHERE note_id=? AND kind='edit' LIMIT 1",
            (nid,)).fetchone()),
        "backlinks":   [{"id": r["dst_id"],
                         "title": (r["t"] or (r["b"] or "")[:40] or "Untitled")}
                        for r in db.execute(
            "SELECT b.dst_id, (SELECT title FROM enrichments WHERE note_id=b.dst_id "
            "ORDER BY enriched_at DESC LIMIT 1) t, n.body b "
            "FROM backlinks b JOIN notes n ON n.id=b.dst_id WHERE b.src_id=?", (nid,))],
        "visibility":  row["visibility"],
        "created_at":  row["created_at"],
        "updated_at":  row["updated_at"],
        "status":      st["status"] if st else "raw",
        "failures":    st["failures"] if st else 0,
        "tags":        tags,
        "action_items": actions,
        "related":     [{"id": r["related_id"],
                         "title": _rel_label(r),
                         "updated": r["rel_updated"],
                         "score": round(r["score"], 3)} for r in rels],
        "enrichment":  dict(enr) if enr else None,
    }

# ── API: Notes ────────────────────────────────────────────────────────────────
@app.route("/api/notes", methods=["GET"])
def list_notes():
    db = get_db()
    tag    = request.args.get("tag")
    status = request.args.get("status")
    q      = request.args.get("q", "").strip()
    view   = request.args.get("filter", "")   # ""|archived|hidden
    limit  = min(int(request.args.get("limit", 50)), 200)
    offset = int(request.args.get("offset", 0))

    where, params = ["1=1"], []
    # Archive/Hidden visibility. Default main list shows neither. A search query
    # (q) searches across everything EXCEPT hidden (hidden stays private unless
    # you're explicitly in the Hidden view).
    if view == "archived":
        where.append("n.archived = 1")
    elif view == "hidden":
        where.append("n.hidden = 1")
    else:
        where.append("COALESCE(n.archived,0) = 0")
        where.append("COALESCE(n.hidden,0) = 0")
    if tag:
        where.append("n.id IN (SELECT note_id FROM tags WHERE tag=?)")
        params.append(tag)
    if status:
        where.append("COALESCE(ns.status,'raw') = ?")
        params.append(status)
    if q:
        where.append("(n.body LIKE ? OR e.title LIKE ? OR e.summary LIKE ?)")
        lq = f"%{q}%"
        params.extend([lq, lq, lq])

    sql = f"""
        SELECT n.*, COALESCE(ns.status,'raw') as _status,
               e.title as _title
        FROM notes n
        LEFT JOIN note_state ns ON ns.note_id = n.id
        LEFT JOIN enrichments e ON e.note_id = n.id
            AND e.id = (SELECT id FROM enrichments WHERE note_id=n.id
                        ORDER BY enriched_at DESC LIMIT 1)
        WHERE {' AND '.join(where)}
        ORDER BY n.pinned DESC, n.updated_at DESC
        LIMIT ? OFFSET ?
    """
    rows = db.execute(sql, params + [limit, offset]).fetchall()
    # Total count for pagination. The list SELECT joins note_state + enrichments
    # to decorate each row, but the COUNT only needs the tables the WHERE clause
    # actually references — joining the others makes this a full-scan with a
    # correlated subquery on EVERY list load (measured: 181ms at 100k vs 10ms).
    # Only `status` filters on the note_state join; only `q` filters on the
    # enrichments join. Add each join solely when its column is used.
    count_joins = ""
    if status:
        count_joins += " LEFT JOIN note_state ns ON ns.note_id = n.id"
    if q:
        count_joins += (" LEFT JOIN enrichments e ON e.note_id = n.id AND e.id = "
                        "(SELECT id FROM enrichments WHERE note_id=n.id "
                        "ORDER BY enriched_at DESC LIMIT 1)")
    total = db.execute(
        f"SELECT COUNT(DISTINCT n.id) FROM notes n{count_joins} "
        f"WHERE {' AND '.join(where)}", params).fetchone()[0]

    notes = []
    for row in rows:
        n = _note_row(row)
        notes.append(n)

    return jsonify({"notes": notes, "total": total, "limit": limit, "offset": offset})

@app.route("/api/notes", methods=["POST"])
def create_note():
    db = get_db()
    data = request.get_json(force=True) or {}
    body = (data.get("body") or "").strip()
    nt_req = data.get("note_type")
    nt_req = nt_req if nt_req in ("text", "checklist", "event") else "text"
    # A Quick Event carries a structured reminder — treat it as an 'event' note
    # so the agent auto-approves it (the user authored the details; there's
    # nothing to review), while still enriching tags/title/links around it.
    if isinstance(data.get("reminder"), dict) and (data["reminder"].get("title") or "").strip():
        nt_req = "event"
    # A brand-new checklist may start empty (the user adds items after); text
    # notes still require content.
    if not body and nt_req != "checklist":
        return jsonify({"error": "body is required"}), 400
    if len(body) > MAX_BODY_CHARS:
        return jsonify({"error": f"note too long (max {MAX_BODY_CHARS:,} characters)"}), 413

    # Offline-first clients supply their own id so outbox retries are
    # idempotent: replaying a create that already landed returns the existing
    # note instead of duplicating it.
    import re as _re
    req_id = (data.get("id") or "").strip()
    if req_id and _re.fullmatch(r"[A-Za-z0-9]{6,24}", req_id):
        existing = db.execute("SELECT * FROM notes WHERE id=?", (req_id,)).fetchone()
        if existing:
            return jsonify(_note_row(existing)), 200
        nid = req_id
    else:
        nid = _nanoid(8)
    db.execute("INSERT INTO notes (id, body, visibility, note_type, enrich_optout, meta_only) "
               "VALUES (?,?,?,?,?,?)",
               (nid, body, data.get("visibility", "private"), nt_req,
                1 if data.get("skip_enrich") else 0,
                1 if data.get("meta_only") else 0))
    db.execute("INSERT INTO note_state (note_id, status, body_hash) VALUES (?,?,?)",
               (nid, "raw", _hash(body)))
    # tags from body (#tag syntax)
    import re
    for tag in set(t.lower() for t in re.findall(r"#(\w+)", body)):
        db.execute("INSERT OR IGNORE INTO tags (note_id, tag) VALUES (?,?)", (nid, tag))

    # Structured reminder (from the Quick Event flow): seed the reminders table
    # directly so a calendar event doesn't depend on the model re-parsing the
    # date out of the note text. The agent's approval path picks this up and
    # creates the Google Calendar event (when calendar_enabled).
    rem = data.get("reminder")
    if isinstance(rem, dict) and (rem.get("title") or "").strip():
        db.execute(
            "INSERT INTO reminders (id, note_id, title, datetime_hint, "
            "datetime_resolved, description, source) VALUES (?,?,?,?,?,?,'user')",
            (_nanoid(10), nid, rem["title"].strip(),
             (rem.get("datetime_hint") or "").strip(),
             (rem.get("datetime_resolved") or "").strip(),
             (rem.get("description") or "").strip()))
    db.commit()

    note = _note_row(db.execute("SELECT * FROM notes WHERE id=?", (nid,)).fetchone())
    _broadcast("note_created", note)
    log.info(f"created note {nid}")
    return jsonify(note), 201

@app.route("/api/notes/<nid>", methods=["GET"])
def get_note(nid):
    db = get_db()
    row = db.execute("SELECT * FROM notes WHERE id=?", (nid,)).fetchone()
    if not row: abort(404)
    return jsonify(_note_row(row))

@app.route("/api/notes/<nid>", methods=["PATCH"])
def update_note(nid):
    db = get_db()
    row = db.execute("SELECT * FROM notes WHERE id=?", (nid,)).fetchone()
    if not row: abort(404)
    data = request.get_json(force=True) or {}

    if "body" in data:
        new_body = data["body"].strip()
        if len(new_body) > MAX_BODY_CHARS:
            return jsonify({"error": f"note too long (max {MAX_BODY_CHARS:,} characters)"}), 413
        st = db.execute("SELECT status FROM note_state WHERE note_id=?", (nid,)).fetchone()
        cur_status = st["status"] if st else "raw"
        # Detect a HIGHLIGHT-only change up front: adding/erasing/recolouring a
        # ==highlight== changes only presentation markers, never the words. Such
        # a change should neither create an 'edit' revision nor re-run the agent.
        import re as _re_hl
        def _strip_hl(s):
            s = _re_hl.sub(r"==\{[ygpb]\}([\s\S]*?)==", r"\1", s or "")
            return _re_hl.sub(r"==([\s\S]*?)==", r"\1", s)
        prev_body = (row["body"] or "")
        annotation_only = (prev_body != new_body
                           and _strip_hl(prev_body) == _strip_hl(new_body))
        # Archive the approved text once per edit-cycle: only when leaving the
        # 'approved' state (the debounced autosave means later keystrokes arrive
        # while status is already 'force', which must NOT create more snapshots).
        # Highlight-only changes never get a revision.
        if (not annotation_only) and cur_status == "approved" and new_body != prev_body:
            db.execute(
                "INSERT INTO revisions (id, note_id, body, kind) VALUES (?,?,?,?)",
                (_nanoid(10), nid, prev_body, "edit"))
        db.execute("UPDATE notes SET body=? WHERE id=?", (new_body, nid))
        new_hash = _hash(new_body)
        # Editing an enriched note (approved/pending/force) queues re-processing
        # → 'force'. Only a note that was never enriched stays 'raw'. Previously
        # this reset an in-flight 'force' note back to 'raw' on the next
        # keystroke, dropping it out of the processing queue.
        #
        # CHECKLISTS are exempt: once enriched (title/tags set), toggling or
        # adding items must NOT re-run the agent every time — a checklist isn't
        # a document being redrafted. It stays 'approved' so the agent leaves it
        # alone. (A brand-new checklist still gets its one enrichment pass.)
        #
        # HIGHLIGHTS (annotation_only, computed above) are exempt too: keep the
        # status stable so a highlight/erase never triggers re-enrichment.
        nt_row = db.execute("SELECT note_type FROM notes WHERE id=?", (nid,)).fetchone()
        is_checklist = bool(nt_row and (nt_row["note_type"] if "note_type" in nt_row.keys() else "text") == "checklist")
        if annotation_only:
            new_status = cur_status
        elif is_checklist and cur_status in ("approved", "pending", "force"):
            new_status = cur_status if cur_status != "force" else "approved"
        else:
            # DECOUPLE durability from interpretation. A plain autosave lands the
            # body and marks the note 'dirty' — persisted, but NOT queued for the
            # ~90 s enrichment. Enrichment is promoted to 'force' only on an
            # explicit commit (editor close or "Run AI now"), so a user taking
            # notes for an hour with pauses never triggers a mid-session run.
            #   raw       → still raw (first enrichment is promoted on commit too)
            #   approved/pending/force/dirty → dirty, unless this call commits
            _committing = str(data.get("commit",
                              request.args.get("commit", ""))).lower() in ("1","true","yes")
            if _committing:
                new_status = "force"          # close / explicit → enrich now
            elif cur_status == "raw":
                new_status = "raw"            # never enriched, not committed yet
            else:
                new_status = "dirty"          # edited, durable, awaiting commit
        try:
            db.execute("""INSERT INTO note_state (note_id, status, body_hash)
                          VALUES (?,?,?)
                          ON CONFLICT(note_id) DO UPDATE SET
                            status=excluded.status, body_hash=excluded.body_hash""",
                       (nid, new_status, new_hash))
        except sqlite3.IntegrityError:
            # Old DB whose note_state CHECK predates 'dirty' (migration deferred
            # because the file was locked at startup). Don't lose the save — the
            # body is already written above; fall back to a legacy-valid status.
            # 'raw'/'force' still round-trips through the agent correctly.
            fallback = "raw" if new_status == "dirty" else new_status
            db.execute("""INSERT INTO note_state (note_id, status, body_hash)
                          VALUES (?,?,?)
                          ON CONFLICT(note_id) DO UPDATE SET
                            status=excluded.status, body_hash=excluded.body_hash""",
                       (nid, fallback, new_hash))
        # re-sync tags
        import re
        db.execute("DELETE FROM tags WHERE note_id=?", (nid,))
        for tag in set(t.lower() for t in re.findall(r"#(\w+)", new_body)):
            db.execute("INSERT OR IGNORE INTO tags (note_id, tag) VALUES (?,?)", (nid, tag))

    if "status" in data:
        allowed = ("raw", "pending", "approved", "force")
        if data["status"] in allowed:
            # An explicit Re-run overrides a bulk-import opt-out, for the same
            # reason Polish does: the agent's enrich_optout gate runs before any
            # decision, so leaving the flag set would make Re-run a no-op.
            if data["status"] == "force":
                db.execute("UPDATE notes SET enrich_optout=0 WHERE id=?", (nid,))
            # ── Adoption happens HERE, atomically with approval ────────────
            # Tapping Approve must transform the note immediately — it cannot
            # depend on the agent being alive. The latest polished text becomes
            # the body in the same transaction; the previous body is archived.
            # The agent's adopt_polished remains as an idempotent fallback and
            # still handles calendar events + state bookkeeping.
            if data["status"] == "approved":
                _adopted = False
                cur = db.execute("SELECT body, protected FROM notes WHERE id=?",
                                 (nid,)).fetchone()
                enr = db.execute(
                    "SELECT polished, enriched_at FROM enrichments "
                    "WHERE note_id=? ORDER BY enriched_at DESC LIMIT 1",
                    (nid,)).fetchone()
                polished = ((enr["polished"] if enr else "") or "").strip()
                body_now = (cur["body"] or "").strip() if cur else ""
                keep_verbatim = bool(cur and (cur["protected"] or "```" in body_now))
                # User override: if the client sends an edited adoption body,
                # adopt exactly that (their tweak of the AI text). Falls back
                # to the AI's polished text when no override is provided.
                override = data.get("adopt_body")
                if override is not None:
                    override = str(override).strip()
                # If the user has manually edited this note before (an 'edit'
                # revision exists) and isn't sending a fresh override now, their
                # current body is authoritative — do NOT replace it with the AI
                # rewrite. EXCEPT when the user has explicitly requested an
                # on-demand polish (polish_once flag), which permits one rewrite.
                polish_flag = db.execute(
                    "SELECT value FROM settings WHERE key=?", (f"polish_once:{nid}",)).fetchone()
                polish_once = bool(polish_flag and polish_flag["value"] == "1")
                was_user_edited = (not polish_once) and bool(db.execute(
                    "SELECT 1 FROM revisions WHERE note_id=? AND kind='edit' LIMIT 1",
                    (nid,)).fetchone())
                if polish_once:
                    db.execute("DELETE FROM settings WHERE key=?", (f"polish_once:{nid}",))
                if override:
                    target = override
                elif was_user_edited:
                    target = body_now          # keep the user's text
                else:
                    target = polished          # first adoption of raw capture
                if target and target != body_now and not keep_verbatim:
                    # Label the snapshot by who authored the change: a user
                    # override is "edit" (before your edit); adopting the AI's
                    # polished text verbatim is "pre_adopt" (before AI rewrite).
                    rev_kind = "edit" if override else "pre_adopt"
                    db.execute(
                        "INSERT INTO revisions (id, note_id, body, kind) "
                        "VALUES (?,?,?,?)",
                        (_nanoid(10), nid, body_now, rev_kind))
                    db.execute("UPDATE notes SET body=? WHERE id=?",
                               (target, nid))
                    db.execute(
                        "UPDATE note_state SET body_hash=? WHERE note_id=?",
                        (_hash(target), nid))
                    _adopted = True
                    log.info(f"adopted {'user-edited' if override else 'polished'} "
                             f"text on approval: {nid}")
            db.execute("""INSERT INTO note_state (note_id, status)
                          VALUES (?,?)
                          ON CONFLICT(note_id) DO UPDATE SET status=excluded.status""",
                       (nid, data["status"]))

    if "protected" in data:
        db.execute("UPDATE notes SET protected=? WHERE id=?",
                   (1 if data["protected"] else 0, nid))

    if "visibility" in data:
        db.execute("UPDATE notes SET visibility=? WHERE id=?",
                   (data["visibility"], nid))

    db.commit()
    note = _note_row(db.execute("SELECT * FROM notes WHERE id=?", (nid,)).fetchone())
    # A status change to "approved" (with or without adoption) is the agent's
    # own lifecycle event — broadcast it as note_enriched so the agent's SSE
    # listener ignores it (it only reacts to note_created / note_updated for
    # USER actions). Broadcasting note_updated here caused the agent to treat
    # approval as a fresh edit and re-run inference. Body edits by the user
    # still go out as note_updated so the agent re-enriches correctly.
    if data.get("status") == "approved":
        _broadcast("note_enriched", note)
    else:
        _broadcast("note_updated", note)
    return jsonify(note)

def _enqueue_calendar_deletions(db, rows):
    """Queue Google Calendar events for removal by the agent.

    `rows` is an iterable of (event_id, note_id, title). Only rows with a real
    event id are queued — we can only delete events WE created and recorded, and
    must never touch anything the user made directly in their calendar.

    Idempotent: event_id is the primary key, so re-queueing is a no-op rather
    than a duplicate. Returns how many were queued.
    """
    n = 0
    for ev, note_id, title in rows:
        ev = (ev or "").strip()
        if not ev:
            continue
        db.execute(
            "INSERT INTO calendar_deletions (event_id, note_id, title) "
            "VALUES (?,?,?) ON CONFLICT(event_id) DO NOTHING",
            (ev, note_id, (title or "")[:200]))
        n += 1
    return n


@app.route("/api/notes/<nid>", methods=["DELETE"])
def delete_note(nid):
    db = get_db()
    if not db.execute("SELECT 1 FROM notes WHERE id=?", (nid,)).fetchone():
        abort(404)
    # Deleting the note cascades its reminders away, so capture any calendar
    # events FIRST — otherwise they'd live on in Google Calendar with nothing
    # left in the app that knows they exist. Queued here, performed by the agent
    # (which owns the credentials).
    try:
        orphans = db.execute(
            "SELECT calendar_event_id, note_id, title FROM reminders "
            "WHERE note_id=? AND calendar_event_id IS NOT NULL "
            "AND calendar_event_id != ''", (nid,)).fetchall()
        if orphans:
            q = _enqueue_calendar_deletions(
                db, [(r["calendar_event_id"], r["note_id"], r["title"]) for r in orphans])
            log.info(f"note {nid} deleted — queued {q} calendar event(s) for removal")
    except Exception as e:
        log.warning(f"couldn't queue calendar cleanup for {nid}: {e}")
    db.execute("DELETE FROM notes WHERE id=?", (nid,))
    db.commit()
    _broadcast("note_deleted", {"id": nid})
    return jsonify({"deleted": nid})

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
    db.execute("UPDATE note_state SET status='force' WHERE note_id=?", (nid,))
    db.commit()
    note_row = db.execute("SELECT * FROM notes WHERE id=?", (nid,)).fetchone()
    _broadcast("note_updated", _note_row(note_row))
    return jsonify({"ok": True, "note": _note_row(note_row)})

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

    db.execute("UPDATE reminders SET dismissed=1 WHERE id=?", (rid,))
    db.commit()
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


@app.route("/api/actions", methods=["GET"])
def list_actions():
    db = get_db()
    done_filter = request.args.get("done")
    aging = request.args.get("aging")   # '1' → only actions whose note is >7 days old
    where = []
    params = []
    # Dismissed items are rejections, not tasks — they never appear in the list.
    # (The rows persist only so the agent won't re-suggest the same text.)
    where.append("COALESCE(a.dismissed,0)=0")
    if done_filter == "0": where.append("a.done=0")
    elif done_filter == "1": where.append("a.done=1")
    if aging == "1":
        where.append("a.done=0 AND n.created_at < datetime('now','-7 days')")
    sql = f"""
        SELECT a.*, n.id as note_id,
               COALESCE(e.title, substr(n.body,1,60)) as note_title,
               t_agg.tags
        FROM action_items a
        JOIN notes n ON n.id = a.note_id
        LEFT JOIN enrichments e ON e.note_id = n.id
            AND e.id=(SELECT id FROM enrichments WHERE note_id=n.id ORDER BY enriched_at DESC LIMIT 1)
        LEFT JOIN (
            SELECT note_id, GROUP_CONCAT(tag) as tags FROM tags GROUP BY note_id
        ) t_agg ON t_agg.note_id = n.id
        {'WHERE ' + ' AND '.join(where) if where else ''}
        ORDER BY a.done ASC, a.created_at ASC
    """
    rows = db.execute(sql, params).fetchall()
    return jsonify({"actions": [dict(r) for r in rows]})

# ── API: Tags ─────────────────────────────────────────────────────────────────
def _decode_embedding(raw):
    """Decode a stored embedding. Accepts the compact float32 BLOB (bytes) or a
    legacy JSON string, so old rows keep working until re-embedded. Returns None
    on anything unreadable. Mirrors the agent's embeddings.decode_embedding —
    the two components must agree on the wire format for notes.embedding.
    """
    if raw is None:
        return None
    if isinstance(raw, (bytes, bytearray, memoryview)):
        import struct
        b = bytes(raw)
        if len(b) % 4 != 0:
            return None
        try:
            return list(struct.unpack(f"<{len(b)//4}f", b))
        except Exception:
            return None
    if isinstance(raw, str):
        try:
            import json as _j
            v = _j.loads(raw)
            return [float(x) for x in v] if isinstance(v, list) else None
        except Exception:
            return None
    if isinstance(raw, list):
        return raw
    return None


def _parse_temporal(q):
    """Extract a date window and residual text from a natural-language query.

    Returns (lo_iso, hi_iso, residual_query). This is what makes "that bill from
    last week" and "what was I writing last year this time" work: the time
    phrase becomes a created_at filter (which vectors CANNOT encode — an
    embedding has no notion of "last week"), and the rest of the query is what we
    keyword/semantic rank within that window. Either bound may be None (open
    range); residual is the query with the time phrase stripped.

    Deliberately small and rule-based: a handful of phrases people actually type,
    not a general date grammar. Unmatched queries return (None, None, q) — i.e.
    plain search, unchanged.
    """
    import re
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    ql = q.lower()

    def iso(d):
        return d.strftime("%Y-%m-%dT%H:%M:%fZ")

    # Ordered longest-first so "last year this time" wins over "last year".
    patterns = [
        (r"\b(last year (?:around )?this time|this time last year)\b",
         lambda: (now.replace(year=now.year - 1) - timedelta(days=15),
                  now.replace(year=now.year - 1) + timedelta(days=15))),
        (r"\b(last year)\b",
         lambda: (now.replace(year=now.year - 1) - timedelta(days=30),
                  now.replace(year=now.year - 1) + timedelta(days=30))),
        (r"\b(yesterday)\b",       lambda: (now - timedelta(days=1), now)),
        (r"\b(today)\b",           lambda: (now - timedelta(days=1), now)),
        (r"\b(this week)\b",       lambda: (now - timedelta(days=now.weekday()), now)),
        (r"\b(last week)\b",       lambda: (now - timedelta(days=7), now)),
        (r"\b(this month)\b",      lambda: (now.replace(day=1), now)),
        (r"\b(last month)\b",      lambda: (now - timedelta(days=30), now)),
        (r"\b(recently|lately)\b", lambda: (now - timedelta(days=14), now)),
        (r"\b(last (\d+) days?)\b", None),  # handled specially below
    ]

    for pat, fn in patterns:
        m = re.search(pat, ql)
        if not m:
            continue
        if fn is None:
            # "last N days"
            n = int(m.group(2))
            lo, hi = now - timedelta(days=n), now
        else:
            lo, hi = fn()
        residual = re.sub(pat, " ", ql).strip()
        residual = re.sub(r"\s+", " ", residual)
        # Strip now-dangling connectors left behind ("from", "in", "about").
        residual = re.sub(r"\b(from|in|during|about|the)\b\s*$", "", residual).strip()
        residual = re.sub(r"^\s*\b(from|in|during|about|the)\b", "", residual).strip()
        return iso(lo), iso(hi), residual

    return None, None, q


def _cosine(a, b):
    import math
    dot = sum(x*y for x, y in zip(a, b))
    na = math.sqrt(sum(x*x for x in a)); nb = math.sqrt(sum(y*y for y in b))
    return dot/(na*nb) if na and nb else 0.0

def _embed_query(text):
    """Get a query embedding from the agent's embed sidecar (same host).
    Returns None if unavailable — caller falls back to keyword search."""
    import json as _j, urllib.request, urllib.error
    url = os.environ.get("HG_EMBED_URL", "http://127.0.0.1:5232/embed")
    try:
        req = urllib.request.Request(
            url, data=_j.dumps({"text": text}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=3) as r:
            return _j.loads(r.read()).get("embedding")
    except Exception:
        return None

def _keyword_ids(db, text, time_filter="", time_params=None):
    """Note ids matching `text` by keyword, honouring an optional time filter.

    Uses the FTS5 index (BM25-ranked, indexed) when the build has it, else falls
    back to LIKE (a full scan, but correct). Either way the result is the SET of
    ids that match; semantic re-ranking happens afterward in the caller. When
    `text` is empty (a pure temporal query like "yesterday"), returns everything
    in the window so the time filter alone drives the result.
    """
    time_params = time_params or []
    text = (text or "").strip()

    # Pure temporal query (no words to match): return all notes in the window.
    if not text:
        rows = db.execute(
            f"SELECT n.id FROM notes n WHERE 1=1{time_filter} "
            f"ORDER BY n.created_at DESC LIMIT 200", time_params).fetchall()
        return {r["id"] for r in rows}

    if HAS_FTS5:
        try:
            # Build an FTS query: prefix-match each term so "bill" finds
            # "billing". Quote to neutralise FTS operator characters in input.
            terms = [t for t in re.split(r"\s+", text.lower()) if t]
            fts_q = " OR ".join(f'"{t}"*' for t in terms) if terms else '""'
            rows = db.execute(
                f"SELECT n.id FROM notes_fts f JOIN notes n ON n.rowid = f.rowid "
                f"WHERE notes_fts MATCH ?{time_filter} ORDER BY f.rank LIMIT 200",
                [fts_q] + time_params).fetchall()
            ids = {r["id"] for r in rows}
            # Tags aren't in the body index; union tag matches so #telecom still
            # finds its notes.
            trows = db.execute(
                f"SELECT DISTINCT n.id FROM notes n JOIN tags t ON t.note_id=n.id "
                f"WHERE t.tag LIKE ?{time_filter}",
                [f"%{text.lower()}%"] + time_params).fetchall()
            ids |= {r["id"] for r in trows}
            return ids
        except Exception as e:
            log.debug(f"FTS query failed ({e}); falling back to LIKE.")

    # LIKE fallback (no FTS5 build, or a malformed FTS query).
    like = f"%{text.lower()}%"
    rows = db.execute(
        f"SELECT DISTINCT n.id FROM notes n LEFT JOIN tags t ON t.note_id=n.id "
        f"WHERE (lower(n.body) LIKE ? OR lower(t.tag) LIKE ?){time_filter}",
        [like, like] + time_params).fetchall()
    return {r["id"] for r in rows}


@app.route("/api/search", methods=["GET"])
def semantic_search():
    """Hybrid search: temporal filter + keyword (FTS5) + semantic re-rank.

    "that bill from last week" → time window + "bill" ranked within it.
    "what was I writing last year this time" → window around a year ago.
    Plain queries → keyword + semantic, unchanged. Degrades gracefully: no FTS5
    build → LIKE; no embeddings/sidecar → keyword only."""
    db = get_db()
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"notes": [], "mode": "empty"})

    # Temporal parse: pull any date window out of the query. "that bill from last
    # week" → window + residual "that bill". The window becomes a created_at
    # filter (which embeddings cannot express); the residual is what we rank.
    lo, hi, residual = _parse_temporal(q)
    search_text = residual if residual else q
    time_filter = ""
    time_params = []
    if lo and hi:
        time_filter = " AND n.created_at >= ? AND n.created_at <= ?"
        time_params = [lo, hi]

    # Keyword match: FTS5 (ranked, indexed) when available, else LIKE fallback.
    kw_ids = _keyword_ids(db, search_text, time_filter, time_params)

    qvec = _embed_query(search_text) if search_text else None
    mode = "temporal" if (lo and hi) else "keyword"
    ranked_ids = list(kw_ids)
    if qvec:
        min_sim = float(_get_prefs(db).get("search_min_similarity", 0.30))
        rows = db.execute(
            "SELECT id, embedding FROM notes WHERE embedding IS NOT NULL").fetchall()
        scored = _rank_by_embedding(qvec, rows, min_sim, kw_ids)
        ranked_ids = [i for i, _ in scored] or list(kw_ids)
        mode = "semantic" if scored else "keyword"

    notes = [_note_row(db.execute("SELECT * FROM notes WHERE id=?", (i,)).fetchone())
             for i in ranked_ids[:50]]
    notes = [n for n in notes if n]
    return jsonify({"notes": notes, "mode": mode})


def _rank_by_embedding(qvec, rows, min_sim, kw_ids):
    """Rank notes by cosine similarity to the query vector.

    Fast path: when every stored embedding is a float32 BLOB and numpy is
    available, the raw bytes are concatenated and read straight into one matrix
    with np.frombuffer — NO per-row Python decode — then a single matmul scores
    the whole corpus. This is the entire point of the blob format: a JSON corpus
    forces a per-row json.loads (measured ~1.5s at 50k just to decode), while
    frombuffer does it in ~40ms. Mixed/legacy JSON rows fall back to per-row
    decode; a fully-legacy corpus still works, just slower until re-embedded.
    A note is kept if it clears min_sim OR was a keyword hit; keyword hits get a
    +0.20 boost so exact matches rank above loose semantic ones.
    """
    EXPECTED = 384 * 4  # bytes per float32 embedding
    try:
        import numpy as np
        # Split rows into pure-blob (fast) and everything else (per-row decode).
        blob_ids, blob_bytes, other = [], [], []
        for r in rows:
            e = r["embedding"]
            if isinstance(e, (bytes, bytearray, memoryview)) and len(bytes(e)) == EXPECTED:
                blob_ids.append(r["id"]); blob_bytes.append(bytes(e))
            else:
                other.append(r)
        scored = []
        q = np.asarray(qvec, dtype=np.float32)
        qn = np.linalg.norm(q) or 1.0
        if blob_bytes:
            # One buffer → one matrix, zero per-row Python.
            mat = np.frombuffer(b"".join(blob_bytes), dtype=np.float32).reshape(
                len(blob_bytes), 384)
            rn = np.linalg.norm(mat, axis=1); rn[rn == 0] = 1.0
            sims = (mat @ q) / (rn * qn)
            for i, s in zip(blob_ids, sims.tolist()):
                if s > min_sim or i in kw_ids:
                    scored.append((i, s + (0.20 if i in kw_ids else 0)))
        # Legacy / odd rows: decode individually, same scoring.
        for r in other:
            v = _decode_embedding(r["embedding"])
            if v is None:
                continue
            s = float(_cosine(qvec, v))
            if s > min_sim or r["id"] in kw_ids:
                scored.append((r["id"], s + (0.20 if r["id"] in kw_ids else 0)))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored
    except Exception:
        # Full scalar fallback — identical semantics, no numpy.
        scored = []
        for r in rows:
            v = _decode_embedding(r["embedding"])
            if v is None:
                continue
            s = _cosine(qvec, v)
            if s > min_sim or r["id"] in kw_ids:
                scored.append((r["id"], s + (0.20 if r["id"] in kw_ids else 0)))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

# ── API: Ask your notes (retrieval-augmented Q&A, generated on-device) ───────
# hg_ui never calls the model. It does retrieval (identical ranking to
# /api/search — same helpers, so the two can never drift apart) and writes a
# pending row; the daemon polls ask_requests directly via SQLite, same as it
# already polls note_state for dirty notes, generates locally, and POSTs the
# result to /api/ask/answer below. See schema.sql for the full flow comment.
MAX_ASK_EXCERPTS      = 5     # notes handed to the model per question
MAX_ASK_EXCERPT_CHARS = 700   # per-excerpt cap — keeps 5 excerpts + the question
                               # well inside the daemon's clamped context budget

# _keyword_ids OR-matches every word in the query, stopwords included — correct
# for /api/search, where a human skims the result list and ignores noise. Ask
# hands the top hits straight to the model as ground truth, so a stopword like
# "the" pulling in an unrelated note (any question with "the" in it will
# prefix-match any note containing "the") is a real problem there in a way it
# isn't for a list a person can eyeball. Stripped only for this endpoint.
_ASK_STOPWORDS = frozenset("""a an the is are was were be been being of in on
    at to for with and or but if what when where who how why do does did
    my your his her its our their this that these those i you he she it we
    they""".split())

def _ask_search_text(text: str) -> str:
    words = [w for w in re.split(r"\s+", text.lower()) if w]
    stripped = [w for w in words if w not in _ASK_STOPWORDS]
    return " ".join(stripped) if stripped else text

def _ask_excerpt(n: dict) -> dict:
    """Condense a note to what the daemon needs: a title (enriched title, else
    the same short-body fallback _note_row already uses for related-note
    labels) and text (the enriched summary when there is one — already
    condensed — else the raw body), trimmed to MAX_ASK_EXCERPT_CHARS."""
    enr = n.get("enrichment") or {}
    title = (enr.get("title") or "").strip()
    body = (n.get("body") or "").strip()
    if not title:
        title = (body[:48] + "…") if len(body) > 48 else (body or "Untitled note")
    text = (enr.get("summary") or "").strip() or body
    if len(text) > MAX_ASK_EXCERPT_CHARS:
        text = text[:MAX_ASK_EXCERPT_CHARS] + "…"
    return {"id": n["id"], "title": title, "text": text}

@app.route("/api/ask", methods=["POST"])
def ask_notes():
    db = get_db()
    data = request.get_json(force=True) or {}
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify({"error": "question required"}), 400
    question = question[:500]

    # Same retrieval /api/search runs: temporal window, FTS keyword hit set,
    # then semantic re-rank over that residual query.
    lo, hi, residual = _parse_temporal(question)
    search_text = residual if residual else question
    time_filter, time_params = "", []
    if lo and hi:
        time_filter = " AND n.created_at >= ? AND n.created_at <= ?"
        time_params = [lo, hi]
    kw_ids = _keyword_ids(db, _ask_search_text(search_text), time_filter, time_params)
    qvec = _embed_query(search_text) if search_text else None
    if qvec:
        min_sim = float(_get_prefs(db).get("search_min_similarity", 0.30))
        rows = db.execute(
            "SELECT id, embedding FROM notes WHERE embedding IS NOT NULL").fetchall()
        scored = _rank_by_embedding(qvec, rows, min_sim, kw_ids)
        # A vector was available but nothing cleared the bar: for a synthesized
        # answer that's "no confident match", not "fall back to keyword noise"
        # — unlike /api/search, which shows keyword hits anyway for a human to
        # skim, Ask would otherwise hand the model an unrelated note as if it
        # were the answer.
        ranked_ids = [i for i, _ in scored]
    else:
        # No embedding vector at all (sidecar down, or an empty residual query)
        # — genuine keyword-only mode, same fallback /api/search uses.
        ranked_ids = list(kw_ids)

    top_ids = ranked_ids[:MAX_ASK_EXCERPTS]
    rid = _nanoid(12)

    if not top_ids:
        # Nothing plausible in the corpus — answer immediately, no model call,
        # no battery spent on a near-certain miss.
        answer = "Nothing in your notes seems to answer that."
        db.execute("""INSERT INTO ask_requests
                      (id, question, context_json, status, answer, cited_note_ids, answered_at)
                      VALUES (?,?,?,?,?,?,?)""",
                   (rid, question, "[]", "done", answer, "[]", _now()))
        db.commit()
        return jsonify({"request_id": rid, "status": "done",
                         "answer": answer, "cited_note_ids": []})

    excerpts = []
    for nid in top_ids:
        row = db.execute("SELECT * FROM notes WHERE id=?", (nid,)).fetchone()
        if row:
            excerpts.append(_ask_excerpt(_note_row(row)))

    db.execute("""INSERT INTO ask_requests (id, question, context_json, status)
                  VALUES (?,?,?,'pending')""",
               (rid, question, json.dumps(excerpts)))
    db.commit()
    return jsonify({"request_id": rid, "status": "pending"})

@app.route("/api/ask/<rid>", methods=["GET"])
def get_ask(rid):
    """Resilience path: if the client's SSE connection dropped the push (or the
    app was backgrounded when it arrived), it can re-fetch the request by id."""
    db = get_db()
    row = db.execute("SELECT * FROM ask_requests WHERE id=?", (rid,)).fetchone()
    if not row:
        return jsonify({"error": "not found"}), 404
    return jsonify({
        "request_id": rid, "question": row["question"], "status": row["status"],
        "answer": row["answer"], "error": row["error"],
        "cited_note_ids": json.loads(row["cited_note_ids"] or "[]"),
    })

@app.route("/api/ask/answer", methods=["POST"])
def ask_answer():
    """Daemon write-back after local generation — same shape as /api/webhook:
    persist the result, then broadcast so any open PWA client updates live."""
    db = get_db()
    data = request.get_json(force=True) or {}
    rid = data.get("request_id")
    if not rid:
        return jsonify({"error": "request_id required"}), 400
    if not db.execute("SELECT 1 FROM ask_requests WHERE id=?", (rid,)).fetchone():
        return jsonify({"error": "request not found"}), 404

    status = data.get("status", "done")
    answer = data.get("answer")
    cited  = data.get("cited_note_ids") or []
    error  = data.get("error")
    db.execute("""UPDATE ask_requests
                  SET status=?, answer=?, cited_note_ids=?, error=?, answered_at=?
                  WHERE id=?""",
               (status, answer, json.dumps(cited), error, _now(), rid))
    db.commit()
    _broadcast("ask_answered", {
        "request_id": rid, "status": status, "answer": answer,
        "cited_note_ids": cited, "error": error,
    })
    return jsonify({"ok": True, "request_id": rid})

@app.route("/share-target", methods=["GET"])
def share_target():
    """Android share-sheet entry: create a note from shared text, then open."""
    from flask import redirect
    text = (request.args.get("text") or "").strip()
    title = (request.args.get("title") or "").strip()
    url = (request.args.get("url") or "").strip()
    parts = [p for p in (title, text, url) if p]
    body = "\n\n".join(parts).strip()
    if body:
        db = get_db()
        nid = _nanoid()
        if len(body) > MAX_BODY_CHARS:
            body = body[:MAX_BODY_CHARS]
        db.execute("INSERT INTO notes (id, body) VALUES (?,?)", (nid, body))
        db.execute("INSERT INTO note_state (note_id, status) VALUES (?, 'raw')", (nid,))
        db.commit()
        note = db.execute("SELECT * FROM notes WHERE id=?", (nid,)).fetchone()
        _broadcast("note_created", _note_row(note))
        return redirect(f"/?shared={nid}")
    return redirect("/")

@app.route("/api/notes/<nid>/pin", methods=["POST"])
def toggle_pin(nid):
    db = get_db()
    cur = db.execute("SELECT pinned FROM notes WHERE id=?", (nid,)).fetchone()
    if not cur: return jsonify({"error": "not found"}), 404
    new_val = 0 if cur["pinned"] else 1
    db.execute("UPDATE notes SET pinned=? WHERE id=?", (new_val, nid))
    db.commit()
    return jsonify({"ok": True, "pinned": new_val})

@app.route("/api/notes/<nid>/archive", methods=["POST"])
def toggle_archive(nid):
    """Archive/unarchive a note. Organizational only — keeps the note but hides
    it from the main list. Archiving also unpins (an archived note shouldn't
    sit pinned at the top)."""
    db = get_db()
    cur = db.execute("SELECT archived FROM notes WHERE id=?", (nid,)).fetchone()
    if not cur: return jsonify({"error": "not found"}), 404
    new_val = 0 if cur["archived"] else 1
    if new_val:
        db.execute("UPDATE notes SET archived=1, pinned=0 WHERE id=?", (nid,))
    else:
        db.execute("UPDATE notes SET archived=0 WHERE id=?", (nid,))
    db.commit()
    return jsonify({"ok": True, "archived": new_val})

@app.route("/api/notes/<nid>/hide", methods=["POST"])
def toggle_hide(nid):
    """Hide/unhide a note. VISUAL PRIVACY ONLY — the note is blurred until
    tapped and kept out of the main list and search, but the body remains
    plaintext in the database (this is not encryption). Hiding also unpins."""
    db = get_db()
    cur = db.execute("SELECT hidden FROM notes WHERE id=?", (nid,)).fetchone()
    if not cur: return jsonify({"error": "not found"}), 404
    new_val = 0 if cur["hidden"] else 1
    if new_val:
        db.execute("UPDATE notes SET hidden=1, pinned=0 WHERE id=?", (nid,))
    else:
        db.execute("UPDATE notes SET hidden=0 WHERE id=?", (nid,))
    db.commit()
    return jsonify({"ok": True, "hidden": new_val})

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
               COALESCE(e.title, substr(n.body,1,60)) AS note_title
        FROM reminders r
        JOIN notes n ON n.id = r.note_id
        LEFT JOIN enrichments e ON e.note_id = n.id
            AND e.id = (SELECT id FROM enrichments WHERE note_id=n.id
                        ORDER BY enriched_at DESC LIMIT 1)
        WHERE COALESCE(r.dismissed,0)=0
        ORDER BY CASE WHEN r.datetime_resolved IS NOT NULL
                      AND r.datetime_resolved != '' THEN 0 ELSE 1 END,
                 r.datetime_resolved ASC, r.created_at DESC
    """).fetchall()
    return jsonify({"reminders": [dict(r) for r in rows]})


@app.route("/api/notes/<nid>/enrich-mode", methods=["POST"])
def set_enrich_mode(nid):
    """Set the enrichment mode for a note: full | metadata | off.

    One control, three coherent states, mapped onto the two underlying flags:
      full     → enrich_optout=0, meta_only=0  (metadata + polished rewrite)
      metadata → enrich_optout=0, meta_only=1  (describe/tag, body verbatim)
      off      → enrich_optout=1                (no AI at all)
    Setting a note to 'full' after it was skipped is the "opt in later" path;
    the agent re-enriches on the next pass.
    """
    db = get_db()
    if not db.execute("SELECT 1 FROM notes WHERE id=?", (nid,)).fetchone():
        abort(404)
    mode = (request.get_json(force=True) or {}).get("mode")
    # This control is AUTHORITATIVE over every flag that affects enrichment,
    # including `protected` — which independently forces metadata-only in the
    # agent. Leaving it set while the user picks "Full" would make the control
    # lie: it would say Full and behave as Metadata.
    if mode == "full":
        db.execute("UPDATE notes SET enrich_optout=0, meta_only=0, protected=0 "
                   "WHERE id=?", (nid,))
    elif mode == "metadata":
        db.execute("UPDATE notes SET enrich_optout=0, meta_only=1 WHERE id=?", (nid,))
    elif mode == "off":
        db.execute("UPDATE notes SET enrich_optout=1 WHERE id=?", (nid,))
    else:
        return jsonify({"error": "mode must be full|metadata|off"}), 400
    # Turning enrichment on for a note that's mid-review shouldn't strand it —
    # settle any pending review, matching the skip-enrich behaviour.
    if mode in ("full", "metadata"):
        st = db.execute("SELECT status FROM note_state WHERE note_id=?", (nid,)).fetchone()
        if st and st["status"] == "pending":
            db.execute("UPDATE note_state SET status='approved' WHERE note_id=?", (nid,))
    db.commit()
    row = db.execute("SELECT enrich_optout, meta_only FROM notes WHERE id=?", (nid,)).fetchone()
    return jsonify({"ok": True, "mode": mode,
                    "enrich_optout": row["enrich_optout"], "meta_only": row["meta_only"]})


@app.route("/api/notes/<nid>/skip-enrich", methods=["POST"])
def toggle_skip_enrich(nid):
    """Toggle whether the agent may auto-enrich this note in FUTURE.

    Sets notes.enrich_optout, the same flag the bulk importer uses — the agent
    checks it in _process_one and skips the note entirely (no Gemma call, no
    ~72s of phone heat).

    Scope is deliberately narrow: this stops future work. It does NOT hide or
    delete anything already produced — the title and tags are what make a note
    findable, and a switch about the future shouldn't rewrite the past. If a
    suggestion is mid-review when this is turned on, the review is settled so
    the note is never stranded in 'pending' with no way forward.

    This is about ENRICHMENT (the expensive LLM pass), not embeddings. A skipped
    note is still embedded — milliseconds, not a minute — so it stays findable by
    semantic search and can still appear in connections. An explicit
    ⋯ → Re-run/Polish clears the flag, since that's an unambiguous "enrich now".
    """
    db = get_db()
    cur = db.execute("SELECT enrich_optout FROM notes WHERE id=?", (nid,)).fetchone()
    if not cur:
        return jsonify({"error": "not found"}), 404
    new_val = 0 if (cur["enrich_optout"] or 0) else 1
    db.execute("UPDATE notes SET enrich_optout=? WHERE id=?", (new_val, nid))
    if new_val:
        # Turning skip ON while a suggestion is awaiting review would strand the
        # note: 'pending' with no way forward (Re-run and Polish only appear once
        # a note is approved). Ticking "skip" reads as "I'm done deciding about
        # this note", so settle the review rather than freeze it. No action is
        # left with no exit.
        st = db.execute("SELECT status FROM note_state WHERE note_id=?",
                        (nid,)).fetchone()
        if st and st["status"] == "pending":
            db.execute("UPDATE note_state SET status='approved' WHERE note_id=?",
                       (nid,))
    db.commit()
    return jsonify({"ok": True, "enrich_optout": new_val})

@app.route("/api/notes/<nid>/polish", methods=["POST"])
def request_polish(nid):
    """On-demand: let the agent rewrite a user-edited note ONCE. Sets a
    one-shot marker (an enrichment-clearing 'edit' removal would be wrong, so
    we use a settings flag) and forces re-processing that WILL produce polished."""
    db = get_db()
    if not db.execute("SELECT 1 FROM notes WHERE id=?", (nid,)).fetchone():
        return jsonify({"error": "not found"}), 404
    db.execute("INSERT INTO settings (key, value) VALUES (?, '1') "
               "ON CONFLICT(key) DO UPDATE SET value='1'", (f"polish_once:{nid}",))
    # An explicit user request overrides a bulk-import opt-out — otherwise the
    # agent's enrich_optout gate would silently swallow this and Polish would
    # appear to do nothing.
    db.execute("UPDATE notes SET enrich_optout=0 WHERE id=?", (nid,))
    db.execute("INSERT INTO note_state (note_id, status) VALUES (?, 'force') "
               "ON CONFLICT(note_id) DO UPDATE SET status='force'", (nid,))
    db.commit()
    note = db.execute("SELECT * FROM notes WHERE id=?", (nid,)).fetchone()
    _broadcast("note_updated", _note_row(note))
    return jsonify({"ok": True})

@app.route("/api/backlinks", methods=["POST"])
def set_backlinks():
    """Replace a note's outgoing [[links]] (parsed client-side to note ids)."""
    db = get_db()
    data = request.get_json(force=True) or {}
    src = data.get("src_id"); dsts = data.get("dst_ids", [])
    if not src: return jsonify({"error": "src_id required"}), 400
    db.execute("DELETE FROM backlinks WHERE src_id=?", (src,))
    for d in dsts:
        if d and d != src and db.execute("SELECT 1 FROM notes WHERE id=?", (d,)).fetchone():
            db.execute("INSERT OR IGNORE INTO backlinks (src_id, dst_id) VALUES (?,?)", (src, d))
    db.commit()
    return jsonify({"ok": True})

@app.route("/api/notes/search-titles", methods=["GET"])
def search_titles():
    """Autocomplete for [[wiki]] linking — id + title/snippet for matching notes."""
    db = get_db()
    q = (request.args.get("q") or "").strip().lower()
    rows = db.execute(
        "SELECT n.id, (SELECT title FROM enrichments WHERE note_id=n.id "
        "ORDER BY enriched_at DESC LIMIT 1) t, n.body b FROM notes n "
        "ORDER BY n.pinned DESC, n.updated_at DESC LIMIT 200").fetchall()
    out = []
    for r in rows:
        label = (r["t"] or (r["b"] or "").strip()[:50] or "Untitled")
        if not q or q in label.lower() or q in (r["b"] or "").lower():
            out.append({"id": r["id"], "title": label})
        if len(out) >= 12: break
    return jsonify({"notes": out})

@app.route("/api/daily", methods=["POST"])
def get_or_create_daily():
    """Get today's daily note, creating it if absent. One per calendar day."""
    db = get_db()
    from datetime import date
    today = date.today().isoformat()
    row = db.execute("SELECT * FROM notes WHERE is_daily=1 AND daily_date=?", (today,)).fetchone()
    if row:
        return jsonify(_note_row(row))
    nid = _nanoid()
    pretty = date.today().strftime("%A, %B %-d")
    db.execute("INSERT INTO notes (id, body, is_daily, daily_date) VALUES (?,?,?,?)",
               (nid, f"# {pretty}\n\n", 1, today))
    db.execute("INSERT INTO note_state (note_id, status) VALUES (?, 'raw')", (nid,))
    db.commit()
    note = db.execute("SELECT * FROM notes WHERE id=?", (nid,)).fetchone()
    _broadcast("note_created", _note_row(note))
    return jsonify(_note_row(note))

@app.route("/api/templates", methods=["GET", "POST"])
def templates():
    db = get_db()
    if request.method == "POST":
        data = request.get_json(force=True) or {}
        name = (data.get("name") or "").strip()
        body = data.get("body") or ""
        if not name: return jsonify({"error": "name required"}), 400
        tid = _nanoid()
        db.execute("INSERT INTO templates (id, name, body) VALUES (?,?,?)", (tid, name, body))
        db.commit()
        return jsonify({"ok": True, "id": tid})
    rows = db.execute("SELECT id, name, body FROM templates ORDER BY name").fetchall()
    return jsonify({"templates": [dict(r) for r in rows]})

@app.route("/api/templates/<tid>", methods=["DELETE"])
def delete_template(tid):
    db = get_db()
    db.execute("DELETE FROM templates WHERE id=?", (tid,))
    db.commit()
    return jsonify({"ok": True})

def _connection_groups(db):
    """Count connection CLUSTERS (connected components of the relationship
    graph) — the same unit the Connections tab shows the user, so the numbers
    match. A cluster is a set of notes transitively linked by relationships.
    Robust to directed-row asymmetry (unlike COUNT(*)//2)."""
    edges = db.execute(
        "SELECT DISTINCT note_id_a, note_id_b FROM relationships").fetchall()
    if not edges:
        return 0
    # union-find over note ids
    parent = {}
    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb: parent[ra] = rb
    for e in edges:
        union(e["note_id_a"], e["note_id_b"])
    roots = {find(n) for n in parent}
    return len(roots)

@app.route("/api/pulse", methods=["GET"])
def pulse():
    """Agent vitals + a lightweight weekly digest for the Pulse dashboard."""
    db = get_db()
    import json as _j
    tel = {}
    row = db.execute("SELECT value FROM settings WHERE key='agent_telemetry'").fetchone()
    if row:
        try: tel = _j.loads(row["value"])
        except Exception: tel = {}
    hb = db.execute("SELECT value FROM settings WHERE key='agent_heartbeat'").fetchone()
    alive = False
    if hb:
        try:
            then = datetime.fromisoformat(hb["value"].replace("Z","+00:00"))
            alive = (datetime.now(timezone.utc)-then).total_seconds() < AGENT_ALIVE_SECONDS
        except Exception: pass

    def _c(q, *a): return db.execute(q, a).fetchone()[0]
    week = "datetime('now','-7 days')"
    digest = {
        "notes_total":      _c("SELECT COUNT(*) FROM notes"),
        "notes_this_week":  _c(f"SELECT COUNT(*) FROM notes WHERE created_at >= {week}"),
        "pending_review":   _c("SELECT COUNT(*) FROM note_state WHERE status='pending'"),
        "never_approved":   _c("SELECT COUNT(*) FROM note_state WHERE status IN ('raw','pending')"),
        "open_actions":     _c("SELECT COUNT(*) FROM action_items WHERE done=0 AND COALESCE(dismissed,0)=0"),
        "actions_aging":    _c(f"SELECT COUNT(*) FROM action_items ai JOIN notes n ON n.id=ai.note_id "
                               f"WHERE ai.done=0 AND n.created_at < datetime('now','-7 days')"),
        "done_this_week":   _c(f"SELECT COUNT(*) FROM action_items WHERE done=1 AND COALESCE(dismissed,0)=0"),
        "connections":      _connection_groups(db),
        "protected":        _c("SELECT COUNT(*) FROM notes WHERE protected=1"),
    }
    # Current (actionable) agent errors: the stored error list, minus any whose
    # note was since deleted — so the dashboard count matches the drill-down
    # list exactly (no "11 errors" that opens to an empty screen).
    import json as _je
    _erow = db.execute("SELECT value FROM settings WHERE key='agent_errors'").fetchone()
    _errs = []
    if _erow:
        try: _errs = _je.loads(_erow["value"])
        except Exception: _errs = []
    errors_current = sum(
        1 for e in _errs
        if db.execute("SELECT 1 FROM notes WHERE id=?", (e.get("id"),)).fetchone())
    digest["errors_current"] = errors_current
    top_tags = [dict(r) for r in db.execute(
        f"SELECT t.tag, COUNT(*) c FROM tags t JOIN notes n ON n.id=t.note_id "
        f"WHERE n.created_at >= {week} GROUP BY t.tag ORDER BY c DESC LIMIT 5").fetchall()]
    return jsonify({"agent_alive": alive, "telemetry": tel,
                    "digest": digest, "top_tags_week": top_tags})

@app.route("/api/brief/refresh", methods=["POST"])
def brief_refresh():
    """Request an immediate brief regeneration; the agent clears the interval
    gate by seeing this flag and composes on its next tick (within ~1 min)."""
    db = get_db()
    db.execute("INSERT INTO settings (key, value) VALUES ('brief_refresh_requested','1') "
               "ON CONFLICT(key) DO UPDATE SET value='1', "
               "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')")
    db.commit()
    return jsonify({"ok": True, "message": "Brief will refresh within a minute"})

@app.route("/api/errors", methods=["GET"])
def agent_errors():
    """Notes the agent failed to enrich, with reason — so the count is
    actionable, not just alarming."""
    db = get_db()
    import json as _j
    row = db.execute("SELECT value FROM settings WHERE key='agent_errors'").fetchone()
    errs = []
    if row:
        try: errs = _j.loads(row["value"])
        except Exception: errs = []
    # hydrate each with the note's current title/body for display
    out = []
    for e in errs:
        n = db.execute("SELECT id, body FROM notes WHERE id=?", (e.get("id"),)).fetchone()
        if not n:
            continue   # note was deleted — drop the stale error
        title = db.execute("SELECT title FROM enrichments WHERE note_id=? "
                           "ORDER BY enriched_at DESC LIMIT 1", (e.get("id"),)).fetchone()
        out.append({"id": n["id"],
                    "title": (title["title"] if title else None) or (n["body"] or "")[:50],
                    "reason": e.get("reason", "failed"), "ts": e.get("ts")})
    return jsonify({"errors": out})

@app.route("/api/errors/<nid>/retry", methods=["POST"])
def retry_error(nid):
    """Re-queue a failed note by forcing re-enrichment."""
    db = get_db()
    db.execute("INSERT INTO note_state (note_id, status) VALUES (?, 'force') "
               "ON CONFLICT(note_id) DO UPDATE SET status='force'", (nid,))
    db.commit()
    note = db.execute("SELECT * FROM notes WHERE id=?", (nid,)).fetchone()
    if note:
        _broadcast("note_updated", _note_row(note))
    return jsonify({"ok": True})

# Default user preferences — the agent + UI read these.
DEFAULT_PREFS = {
    "enrich_min_words": 6,       # skip enrichment for notes shorter than this
    "summary_mode": "long",      # always | long | never
    "summary_min_words": 100,    # 'long' threshold: below this a note IS its own
                                 # summary, so repeating it just adds noise.
    "brief_mode": "ondemand",    # ondemand | auto
    "default_note_type": "text", # text | checklist
    "highlight_color": "y",      # default highlight colour
    # Semantic search: minimum cosine similarity (0-1) for a note to count as a
    # match. Higher = stricter/cleaner results (fewer loosely-related notes);
    # lower = looser/more inclusive. 0.30 is a good balance; drop toward 0.20 if
    # search feels too strict, raise toward 0.40 if it surfaces unrelated notes.
    "search_min_similarity": 0.30,
    # In-app mute for notifications. Independent of the browser's Notification
    # permission — JS has no way to revoke a granted permission, so this is the
    # actual off-switch the Settings UI offers: notify() checks it before ever
    # calling the service worker, even though the browser stays "granted".
    "notify_muted": 0,
}

def _get_prefs(db):
    import json as _j
    row = db.execute("SELECT value FROM settings WHERE key='user_prefs'").fetchone()
    prefs = dict(DEFAULT_PREFS)
    if row:
        try:
            prefs.update(_j.loads(row["value"]) or {})
        except Exception:
            pass
    return prefs

@app.route("/api/settings", methods=["GET", "POST"])
def user_settings():
    import json as _j
    db = get_db()
    if request.method == "POST":
        data = request.get_json(force=True) or {}
        prefs = _get_prefs(db)
        # Only accept known keys; coerce types defensively.
        for k, v in data.items():
            if k not in DEFAULT_PREFS:
                continue
            if k in ("enrich_min_words", "summary_min_words"):
                try: prefs[k] = max(0, int(v))
                except Exception: pass
            elif k == "search_min_similarity":
                try: prefs[k] = min(1.0, max(0.0, float(v)))
                except Exception: pass
            elif k == "summary_mode" and v in ("always", "long", "never"):
                prefs[k] = v
            elif k == "brief_mode" and v in ("ondemand", "auto"):
                prefs[k] = v
            elif k == "default_note_type" and v in ("text", "checklist"):
                prefs[k] = v
            elif k == "highlight_color" and v in ("y", "g", "p", "b"):
                prefs[k] = v
            elif k == "notify_muted":
                try: prefs[k] = 1 if int(v) else 0
                except Exception: pass
        db.execute("INSERT INTO settings (key, value) VALUES ('user_prefs', ?) "
                   "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (_j.dumps(prefs),))
        db.commit()
        return jsonify({"ok": True, "prefs": prefs})
    # Also surface agent-reported calendar status so the settings UI can show
    # whether Google Calendar sync is actually wired up.
    cal = False
    try:
        import json as _j2
        trow = db.execute("SELECT value FROM settings WHERE key='agent_telemetry'").fetchone()
        if trow:
            cal = bool((_j2.loads(trow["value"]) or {}).get("calendar_enabled"))
    except Exception:
        pass
    return jsonify({"prefs": _get_prefs(db), "calendar_enabled": cal})

@app.route("/api/highlights", methods=["GET"])
def list_highlights():
    """Every highlight (==text==) across all notes, newest note first."""
    import re
    db = get_db()
    rows = db.execute(
        "SELECT n.id, n.body, n.updated_at, "
        "(SELECT title FROM enrichments WHERE note_id=n.id ORDER BY enriched_at DESC LIMIT 1) t "
        "FROM notes n WHERE n.body LIKE '%==%' ORDER BY n.updated_at DESC").fetchall()
    pat = re.compile(r"==(?:\{([ygpb])\})?([\s\S]*?)==")
    out = []
    for r in rows:
        title = r["t"] or (r["body"] or "").strip().split("\n")[0][:40] or "Untitled"
        for m in pat.finditer(r["body"] or ""):
            color = m.group(1) or "y"
            text = m.group(2).strip()
            if text:
                out.append({"note_id": r["id"], "note_title": title,
                            "color": color, "text": text})
    return jsonify({"highlights": out, "count": len(out)})

@app.route("/api/consolidation", methods=["GET"])
def get_consolidation():
    """The agent's current consolidation offer, if any (theme with 4+ notes)."""
    db = get_db()
    import json as _j
    row = db.execute("SELECT value FROM settings WHERE key='consolidation_offer'").fetchone()
    if not row:
        return jsonify({"offer": None})
    try:
        offer = _j.loads(row["value"])
    except Exception:
        return jsonify({"offer": None})
    # hydrate note count still valid
    tag = offer.get("tag")
    if tag:
        cnt = db.execute(
            "SELECT COUNT(DISTINCT note_id) FROM tags WHERE tag=?", (tag,)).fetchone()[0]
        offer["note_count"] = cnt
    return jsonify({"offer": offer})

@app.route("/api/consolidation/accept", methods=["POST"])
def accept_consolidation():
    """User accepts — agent will draft a consolidated note on its next tick."""
    db = get_db()
    db.execute("INSERT INTO settings (key, value) VALUES ('consolidation_accepted','1') "
               "ON CONFLICT(key) DO UPDATE SET value='1'")
    db.commit()
    return jsonify({"ok": True, "message": "Drafting a consolidated note…"})

@app.route("/api/consolidation/dismiss", methods=["POST"])
def dismiss_consolidation():
    db = get_db()
    db.execute("DELETE FROM settings WHERE key='consolidation_offer'")
    db.commit()
    return jsonify({"ok": True})

@app.route("/api/brief", methods=["GET"])
def daily_brief():
    """The agent's most recent natural-language daily brief (if any)."""
    db = get_db()
    row = db.execute("SELECT value, updated_at FROM settings WHERE key='daily_brief'").fetchone()
    if not row:
        return jsonify({"brief": None})
    import json as _j
    try:
        data = _j.loads(row["value"])
    except Exception:
        data = {"text": row["value"]}
    data["updated_at"] = row["updated_at"]
    return jsonify({"brief": data})

@app.route("/api/calendar-events", methods=["GET"])
def calendar_events():
    """Reminders that became Google Calendar events, for the note detail view."""
    db = get_db()
    nid = request.args.get("note_id")
    if nid:
        rows = db.execute(
            "SELECT id, note_id, title, datetime_hint, datetime_resolved, "
            "calendar_event_id FROM reminders WHERE note_id=? AND "
            "calendar_event_id IS NOT NULL AND calendar_event_id != ''", (nid,)).fetchall()
    else:
        rows = db.execute(
            "SELECT id, note_id, title, datetime_hint, datetime_resolved, "
            "calendar_event_id FROM reminders WHERE calendar_event_id IS NOT NULL "
            "AND calendar_event_id != '' "
            # Dated events first (a resolved timestamp), newest-first; undated
            # ('next Monday' style hints we couldn't resolve) fall to the end.
            "ORDER BY CASE WHEN datetime_resolved IS NOT NULL "
            "AND datetime_resolved != '' THEN 0 ELSE 1 END, "
            "datetime_resolved DESC, id DESC").fetchall()
    return jsonify({"events": [dict(r) for r in rows]})

@app.route("/api/tags", methods=["GET"])
def list_tags():
    db = get_db()
    rows = db.execute("""
        SELECT tag, COUNT(*) as count FROM tags GROUP BY tag ORDER BY count DESC
    """).fetchall()
    return jsonify({"tags": [{"tag": r["tag"], "count": r["count"]} for r in rows]})

# ── API: Stats ────────────────────────────────────────────────────────────────
@app.route("/api/stats", methods=["GET"])
def stats():
    db = get_db()
    def _c(sql, *p): return db.execute(sql, p).fetchone()[0]
    # Agent liveness: the agent writes a heartbeat timestamp to settings
    # every ~10s. Stale (>60s) or missing → agent is down.
    hb = db.execute("SELECT value FROM settings WHERE key='agent_heartbeat'").fetchone()
    agent_alive, agent_last = False, None
    if hb:
        agent_last = hb["value"]
        try:
            then = datetime.fromisoformat(agent_last.replace("Z", "+00:00"))
            agent_alive = (datetime.now(timezone.utc) - then).total_seconds() < AGENT_ALIVE_SECONDS
        except Exception:
            pass
    return jsonify({
        "agent_alive":    agent_alive,
        "agent_last_seen": agent_last,
        "total_notes":    _c("SELECT COUNT(*) FROM notes"),
        "approved":       _c("SELECT COUNT(*) FROM note_state WHERE status='approved'"),
        "pending":        _c("SELECT COUNT(*) FROM note_state WHERE status='pending'"),
        "raw":            _c("SELECT COUNT(*) FROM note_state WHERE status='raw'"),
        "action_pending": _c("SELECT COUNT(*) FROM action_items WHERE done=0 AND COALESCE(dismissed,0)=0"),
        "action_done":    _c("SELECT COUNT(*) FROM action_items WHERE done=1 AND COALESCE(dismissed,0)=0"),
        "reminders_active": _c("SELECT COUNT(*) FROM reminders WHERE COALESCE(dismissed,0)=0"),
        "total_tags":     _c("SELECT COUNT(DISTINCT tag) FROM tags"),
    })

# ── API: Daemon webhook (replaces Memos webhook) ──────────────────────────────
@app.route("/api/webhook", methods=["POST"])
def daemon_webhook():
    """The daemon calls this after enriching a note to push results and broadcast
    live updates to all connected PWA clients via SSE."""
    db = get_db()
    data = request.get_json(force=True) or {}
    nid  = data.get("note_id")
    if not nid:
        return jsonify({"error": "note_id required"}), 400
    if not db.execute("SELECT 1 FROM notes WHERE id=?", (nid,)).fetchone():
        return jsonify({"error": "note not found"}), 404

    enr = data.get("enrichment") or {}
    if enr:
        eid = _nanoid(12)
        db.execute("""INSERT INTO enrichments
            (id, note_id, title, summary, polished, confidence, priority, schema_version)
            VALUES (?,?,?,?,?,?,?,?)""",
            (eid, nid, enr.get("title"), enr.get("summary"), enr.get("polished"),
             enr.get("confidence"), enr.get("priority"), enr.get("schema_version","2.1")))

        # action items — replace all for this note
        db.execute("DELETE FROM action_items WHERE note_id=?", (nid,))
        for item in (enr.get("action_items") or []):
            if isinstance(item, str) and item.strip():
                db.execute("INSERT INTO action_items (id, note_id, text) VALUES (?,?,?)",
                           (_nanoid(10), nid, item.strip()))

        # tags — merge (don't replace user tags)
        for tag in (enr.get("tags") or []):
            t = str(tag).strip().lower().lstrip("#")
            if t:
                db.execute("INSERT OR IGNORE INTO tags (note_id, tag) VALUES (?,?)",
                           (nid, t))

        # corrections
        db.execute("DELETE FROM corrections WHERE note_id=?", (nid,))
        for c in (enr.get("corrections") or []):
            db.execute("INSERT INTO corrections (id, note_id, from_text, to_text, confidence) VALUES (?,?,?,?,?)",
                       (_nanoid(10), nid, c.get("from",""), c.get("to",""), c.get("confidence")))

    # update state
    new_status = data.get("status", "pending")
    db.execute("""INSERT INTO note_state (note_id, status, body_hash, seen_at)
                  VALUES (?,?,?,?)
                  ON CONFLICT(note_id) DO UPDATE SET
                    status=excluded.status, body_hash=excluded.body_hash,
                    seen_at=excluded.seen_at, failures=0""",
               (nid, new_status, data.get("body_hash"), data.get("seen_at", _now())))

    db.commit()
    note = _note_row(db.execute("SELECT * FROM notes WHERE id=?", (nid,)).fetchone())
    _broadcast("note_enriched", note)
    return jsonify({"ok": True, "note_id": nid, "status": new_status})

# ── API: Relationships (daemon posts bulk updates) ────────────────────────────
@app.route("/api/relationships", methods=["POST"])
def update_relationships():
    db = get_db()
    data = request.get_json(force=True) or {}
    pairs = data.get("pairs", [])
    for pair in pairs:
        a, b, score = pair.get("a"), pair.get("b"), pair.get("score", 0)
        if a and b and a != b:
            db.execute("""INSERT INTO relationships (note_id_a, note_id_b, score)
                          VALUES (?,?,?)
                          ON CONFLICT(note_id_a, note_id_b) DO UPDATE SET
                            score=excluded.score, computed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
                       """, (a, b, score))
            db.execute("""INSERT INTO relationships (note_id_a, note_id_b, score)
                          VALUES (?,?,?)
                          ON CONFLICT(note_id_a, note_id_b) DO UPDATE SET
                            score=excluded.score, computed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
                       """, (b, a, score))
    db.commit()
    _broadcast("relationships_updated", {"count": len(pairs)})
    return jsonify({"ok": True, "pairs": len(pairs)})

# ── Shell (serves the PWA) ────────────────────────────────────────────────────
@app.route("/")
@app.route("/<path:path>")
def shell(path=""):
    # Serve static files directly
    if path and (Path(app.static_folder) / path).exists():
        return send_from_directory(app.static_folder, path)
    # Everything else → PWA shell
    return send_from_directory(app.static_folder, "index.html")

# ── Main ──────────────────────────────────────────────────────────────────────
def _data_ops(argv):
    """CLI data management: backup / restore / clear.

    Uses SQLite's online backup API — safe even while hg_ui/hg_agent are
    running (WAL mode). Every destructive operation backs up first, always.

      hg_ui backup [dest.db]        → ~/.quicksilver/backups/notes-<ts>.db
      hg_ui restore <file.db>       → auto-backup current, then restore
      hg_ui clear [--yes]           → auto-backup, then wipe all data
      hg_ui import-keep <zip|dir>   → import a Google Keep Takeout export
    """
    import sqlite3 as s3, time as _t
    cmd = argv[1]
    db_path = str(DB_PATH)
    bdir = Path(db_path).parent / "backups"
    bdir.mkdir(parents=True, exist_ok=True)
    ts = _t.strftime("%Y%m%d-%H%M%S")

    def do_backup(dest=None):
        dest = dest or str(bdir / f"notes-{ts}.db")
        src_c = s3.connect(db_path); dst_c = s3.connect(dest)
        with dst_c:
            src_c.backup(dst_c)
        src_c.close(); dst_c.close()
        n = s3.connect(dest).execute("SELECT COUNT(*) FROM notes").fetchone()[0]
        print(f"✓ backup written: {dest} ({n} notes)")
        return dest

    if cmd == "backup":
        do_backup(argv[2] if len(argv) > 2 else None)

    elif cmd == "restore":
        if len(argv) < 3:
            sys.exit("usage: restore <backup-file.db>")
        srcf = argv[2]
        if not Path(srcf).exists():
            sys.exit(f"file not found: {srcf}")
        do_backup(str(bdir / f"pre-restore-{ts}.db"))
        src_c = s3.connect(srcf); dst_c = s3.connect(db_path)
        with dst_c:
            src_c.backup(dst_c)
        src_c.close(); dst_c.close()
        print(f"✓ restored from {srcf}")
        print("  Restart hg_ui and hg_agent to pick up the restored data.")

    elif cmd == "dedupe":
        # Collapse notes with byte-identical bodies (keep the earliest), moving
        # any action items / enrichments / relationships onto the survivor and
        # deleting the duplicates. Backs up first.
        do_backup(str(bdir / f"pre-dedupe-{ts}.db"))
        con = s3.connect(db_path); con.row_factory = s3.Row
        con.execute("PRAGMA foreign_keys=ON")
        groups = con.execute(
            "SELECT body, MIN(created_at) keep_ct, COUNT(*) c "
            "FROM notes GROUP BY body HAVING c > 1").fetchall()
        removed = 0
        for g in groups:
            ids = [r["id"] for r in con.execute(
                "SELECT id FROM notes WHERE body=? ORDER BY created_at", (g["body"],)).fetchall()]
            keep, dupes = ids[0], ids[1:]
            for d in dupes:
                con.execute("DELETE FROM notes WHERE id=?", (d,))  # cascades children
                removed += 1
        con.commit(); con.execute("VACUUM"); con.close()
        print(f"✓ removed {removed} duplicate note(s) across {len(groups)} group(s)")
        print("  Restart hg_agent to recompute relationships on the deduped set.")

    elif cmd == "import-keep":
        # Import a Google Keep export (takeout.google.com → Keep). Accepts the
        # Takeout .zip directly, or a folder of the .json files it contains.
        #
        # Why Takeout and not the Keep API: the unofficial gkeepapi needs your
        # Google password, breaks whenever Google changes auth, and is exactly
        # what stalled a previous attempt. Takeout is official, offline, and
        # gives the full corpus in one shot.
        #
        #   hg_ui import-keep <export.zip|folder> [--dry-run] [--include-archived]
        #                     [--include-trashed] [--tag-from-labels]
        import json as _j, zipfile, hashlib, io
        if len(argv) < 3:
            sys.exit("usage: import-keep <takeout.zip|folder> [--dry-run] "
                     "[--skip-archived] [--include-trashed] [--tag-from-labels] "
                     "[--enrich]\n"
                     "  Archived Keep notes are imported (and stay archived) by default.\n"
                     "  Notes are NOT auto-enriched by default — --enrich queues them all.")
        src = Path(argv[2])
        if not src.exists():
            sys.exit(f"not found: {src}")
        dry          = "--dry-run" in argv
        # Archived notes ARE imported (and stay archived in Quicksilver) — Keep's
        # archive is a deliberate signal, not junk. --skip-archived opts out.
        inc_arch     = "--skip-archived" not in argv
        inc_trash    = "--include-trashed" in argv
        tag_labels   = "--tag-from-labels" in argv
        # Bulk import does NOT auto-enrich by default. One local Gemma call per
        # note means a few hundred notes would occupy the phone for hours and
        # trip the thermal guard. Imported notes are flagged enrich_optout=1;
        # the agent skips them, and you enrich the ones you care about on demand
        # via ⋯ → Re-run (which clears the flag). --enrich queues everything.
        do_enrich    = "--enrich" in argv

        # ── Collect the note JSONs ────────────────────────────────────────────
        blobs = []
        if src.is_file() and src.suffix.lower() == ".zip":
            with zipfile.ZipFile(src) as z:
                for n in z.namelist():
                    # Takeout nests them under Takeout/Keep/*.json
                    if n.lower().endswith(".json") and "/keep/" in n.lower().replace("\\", "/"):
                        blobs.append((n, z.read(n)))
                if not blobs:      # fall back: any json in the zip
                    for n in z.namelist():
                        if n.lower().endswith(".json"):
                            blobs.append((n, z.read(n)))
        else:
            for p in sorted(src.rglob("*.json")):
                blobs.append((p.name, p.read_bytes()))
        if not blobs:
            sys.exit("no Keep .json files found. Export via takeout.google.com → "
                     "select 'Keep' → download the .zip, then point this at it.")

        def keep_to_body(d):
            """Flatten one Keep note into plain text, preserving what matters."""
            parts = []
            title = (d.get("title") or "").strip()
            if title:
                parts.append(title)
            # Checklists come as listContent; render as markdown so the app's
            # checklist detection and the agent both understand them.
            lc = d.get("listContent") or []
            if lc:
                for item in lc:
                    txt = (item.get("text") or "").strip()
                    if not txt:
                        continue
                    parts.append(("- [x] " if item.get("isChecked") else "- [ ] ") + txt)
            txt = (d.get("textContent") or "").strip()
            if txt:
                parts.append(txt)
            # Attachments can't be imported (Takeout stores them as separate
            # image files) — leave an honest breadcrumb rather than silently
            # dropping the fact that the note HAD an image.
            atts = d.get("attachments") or []
            if atts:
                names = ", ".join(a.get("filePath", "?") for a in atts)
                parts.append(f"\n[Keep attachment not imported: {names}]")
            return "\n\n".join(p for p in parts if p).strip()

        def keep_ts(us):
            """Keep timestamps are microseconds since epoch."""
            try:
                return _t.strftime("%Y-%m-%dT%H:%M:%S.000Z", _t.gmtime(int(us) / 1_000_000))
            except Exception:
                return None

        con = s3.connect(db_path); con.row_factory = s3.Row
        con.execute("PRAGMA foreign_keys=ON")
        # Skip anything already imported: match on body hash so re-running is
        # safe and never duplicates (you WILL re-run this).
        existing = {hashlib.sha256((r["body"] or "").encode()).hexdigest()
                    for r in con.execute("SELECT body FROM notes")}

        stats = {"imported": 0, "skipped_dupe": 0, "skipped_empty": 0,
                 "skipped_trashed": 0, "skipped_archived": 0, "pinned": 0,
                 "checklists": 0, "labels": 0, "unparseable": 0}
        rows = []
        for name, raw in blobs:
            try:
                d = _j.loads(raw.decode("utf-8"))
            except Exception:
                stats["unparseable"] += 1
                continue
            if d.get("isTrashed") and not inc_trash:
                stats["skipped_trashed"] += 1; continue
            if d.get("isArchived") and not inc_arch:
                stats["skipped_archived"] += 1; continue
            body = keep_to_body(d)
            if not body:
                stats["skipped_empty"] += 1; continue
            h = hashlib.sha256(body.encode()).hexdigest()
            if h in existing:
                stats["skipped_dupe"] += 1; continue
            existing.add(h)
            labels = [l.get("name", "").strip() for l in (d.get("labels") or [])]
            labels = [l for l in labels if l]
            rows.append({
                "body": body,
                "pinned": 1 if d.get("isPinned") else 0,
                "archived": 1 if d.get("isArchived") else 0,
                "note_type": "checklist" if d.get("listContent") else "text",
                "created": keep_ts(d.get("createdTimestampUsec")),
                "updated": keep_ts(d.get("userEditedTimestampUsec")),
                "labels": labels,
            })
            if d.get("listContent"): stats["checklists"] += 1
            if d.get("isPinned"):    stats["pinned"] += 1
            if labels:               stats["labels"] += 1

        print(f"\nGoogle Keep import — {src}")
        print(f"  found          {len(blobs)} json file(s)")
        print(f"  to import      {len(rows)}")
        print(f"  duplicates     {stats['skipped_dupe']} (already in Quicksilver)")
        print(f"  empty          {stats['skipped_empty']}")
        print(f"  trashed        {stats['skipped_trashed']}"
              + ("" if inc_trash else "  (use --include-trashed to keep)"))
        print(f"  archived       {stats['skipped_archived']}"
              + ("" if inc_arch else "  (skipped via --skip-archived)"))
        if stats["unparseable"]:
            print(f"  unreadable     {stats['unparseable']}")
        print(f"  ├ checklists   {stats['checklists']}")
        print(f"  ├ pinned       {stats['pinned']}")
        print(f"  └ with labels  {stats['labels']}"
              + ("  → imported as tags" if tag_labels else "  (use --tag-from-labels to import as tags)"))

        if dry:
            print("\n(dry run — nothing written)")
            for r in rows[:5]:
                print(f"    · {r['body'][:60].replace(chr(10),' ')}…")
            if len(rows) > 5: print(f"    … and {len(rows)-5} more")
            con.close(); return
        if not rows:
            print("\nnothing to import."); con.close(); return

        do_backup(str(bdir / f"pre-import-keep-{ts}.db"))
        for r in rows:
            nid = _nanoid(8)
            con.execute(
                "INSERT INTO notes (id, body, pinned, archived, note_type, "
                "enrich_optout, created_at, updated_at) VALUES (?,?,?,?,?,?,"
                "COALESCE(?, strftime('%Y-%m-%dT%H:%M:%fZ','now')),"
                "COALESCE(?, strftime('%Y-%m-%dT%H:%M:%fZ','now')))",
                (nid, r["body"], r["pinned"], r["archived"], r["note_type"],
                 0 if do_enrich else 1, r["created"], r["updated"]))
            # status='raw' means "never enriched". With enrich_optout=1 the agent
            # skips it, so the note simply sits there until you ask for it —
            # 'raw' stays truthful either way (it HASN'T been enriched).
            con.execute("INSERT INTO note_state (note_id, status) VALUES (?, 'raw')", (nid,))
            if tag_labels:
                for l in r["labels"]:
                    try:
                        con.execute("INSERT OR IGNORE INTO tags (note_id, tag) VALUES (?,?)",
                                    (nid, l.lower()))
                    except Exception:
                        pass
            stats["imported"] += 1
        con.commit(); con.close()
        print(f"\n✓ imported {stats['imported']} note(s)")
        if do_enrich:
            print("  Queued for enrichment — the agent will work through them.")
            print(f"  That's {stats['imported']} local AI call(s); expect hours, not "
                  "minutes, and the thermal guard will pace it.")
            print("  Watch with: hg_agent logs")
        else:
            print("  NOT queued for AI enrichment (that would be "
                  f"{stats['imported']} local Gemma calls).")
            print("  They're searchable and browsable now. Enrich the ones you "
                  "care about via ⋯ → Re-run.")
            print("  Use --enrich to queue everything instead.")

    elif cmd == "clear":
        if "--yes" not in argv:
            if input("This wipes ALL notes, enrichments and history "
                     "(a backup is taken first). Type 'yes' to continue: ") != "yes":
                sys.exit("aborted")
        do_backup(str(bdir / f"pre-clear-{ts}.db"))
        con = s3.connect(db_path)
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("DELETE FROM notes")      # cascades to every child table
        con.execute("DELETE FROM settings")
        con.commit()
        con.execute("VACUUM")
        con.close()
        print("✓ all data cleared (backup kept in ~/.quicksilver/backups)")


def _set_process_title():
    try:
        from setproctitle import setproctitle
        setproctitle("hg_ui")
    except ImportError:
        pass


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("backup", "restore", "clear",
                                             "dedupe", "import-keep"):
        _data_ops(sys.argv)
        sys.exit(0)
    _set_process_title()
    init_db()
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
