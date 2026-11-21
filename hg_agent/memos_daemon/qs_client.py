import functools
"""qs_client.py — Quicksilver SQLite client for the AI daemon.

Presents the same interface as MemosClient so the daemon needs zero logic
changes. Notes are identified by their SQLite id (e.g. "k7TNhdXp"). The
"content" field on returned note dicts is always the raw note body — no
markdown, no anchors — because all state lives in the DB, not in the text.

Key differences from MemosClient:
  • Direct SQLite read/write — no HTTP, no 8192-char limit, no PATCH API.
  • Note identity: note.id (8-char nanoid), not "memos/xxx".
  • Watermark: uses notes.updated_at ISO string (same semantics as updateTime).
  • update_content: writes only to notes.body + note_state — clean, atomic.
  • State machine: daemon reads note_state.status directly from DB.
  • Enrichment output: daemon calls write_enrichment() to populate all tables
    instead of serialising into markdown and PATCHing text back.
  • Webhook notification: POSTs to the Flask app's /api/webhook so SSE clients
    get live updates — the one HTTP call the daemon makes to the PWA.

The daemon's _handle() still calls self.memos.update_content() for backward
compat, but for Quicksilver that call writes to the DB, not to a PATCH API.
All formatting / anchor / markdown code in the daemon is bypassed — the
daemon's output (enrichment dict) is written directly into the tables.
"""

from .anchor import strip_highlights
import json
import hashlib
import random
import sqlite3
import string
import threading
import time
from pathlib import Path
from typing import Iterator

import requests

from .logging_setup import log


def _nanoid(size: int = 8) -> str:
    return "".join(random.choices(string.ascii_letters + string.digits, k=size))


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _now_iso_minus(seconds: float) -> str:
    from datetime import datetime, timezone, timedelta
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)) \
        .strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _hash(text: str) -> str:
    return hashlib.sha256((text or "").encode()).hexdigest()[:16]


# ── Sentinel (mirrors MemosClient.PATCH_PERMANENT_ERROR) ─────────────────────
class _PermanentError:
    """Singleton sentinel — use `is QuicksilverClient.PATCH_PERMANENT_ERROR`."""
PATCH_PERMANENT_ERROR = _PermanentError()


def _atomic(fn):
    """Run a database-writing method as one unit: if anything raises part-way, roll back.

    sqlite3 opens a transaction on the first write and keeps it (and the database's write lock) until a
    commit or rollback. A method that fails between its first write and its commit would otherwise leave
    the half-finished work pending on this thread's connection — the app can't save anything meanwhile —
    and the next successful commit on that connection would then publish the half-finished work."""
    @functools.wraps(fn)
    def wrapper(self, *a, **k):
        try:
            return fn(self, *a, **k)
        except Exception:
            try:
                self._db().rollback()
            except Exception:
                pass
            raise
    return wrapper


class QuicksilverClient:
    """Drop-in replacement for MemosClient. The daemon uses self.memos which
    is assigned either a MemosClient or a QuicksilverClient depending on config.

    Thread safety: SQLite connections are per-thread (check_same_thread=False
    with WAL mode); we use a threading.local() pool so each worker thread gets
    its own connection.
    """

    # Expose sentinel at class level so existing `MemosClient.PATCH_PERMANENT_ERROR`
    # checks work if callers do `type(self.memos).PATCH_PERMANENT_ERROR`.
    PATCH_PERMANENT_ERROR = PATCH_PERMANENT_ERROR

    def __init__(self, db_path: str, webhook_url: str = "",
                 max_retries: int = 3, backoff_base: float = 0.5):
        self.db_path     = str(db_path)
        self.webhook_url = (webhook_url or "").rstrip("/")
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self._local      = threading.local()
        # Optional embedder. When set (by the daemon), write_enrichment persists
        # each note's embedding to notes.embedding, so:
        #   1. the correlation corpus reads vectors back instead of recomputing
        #      one model inference per note on every rebuild, and
        #   2. the PWA's semantic search — which reads notes.embedding but which
        #      nothing was populating — actually works.
        # Left None on the CLI path (diagnostics), where persistence isn't needed.
        self.embedder = None
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        # Verify the DB is reachable and the core schema exists
        db = self._db()
        tables = {r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        required = {"notes", "note_state", "enrichments", "action_items", "tags"}
        missing = required - tables
        if missing:
            raise RuntimeError(
                f"Quicksilver DB at {db_path} is missing tables: {missing}. "
                f"Run schema.sql first or start the PWA (it auto-initialises).")
        self._ensure_schema(db)
        log.info(f"QuicksilverClient: connected to {db_path}")

    @_atomic
    def _ensure_schema(self, db: sqlite3.Connection):
        """Self-heal auxiliary tables the agent writes to, so the agent never
        depends on the PWA having migrated the schema first. IF NOT EXISTS
        makes this a no-op on an up-to-date DB; process start order no longer
        matters. Core tables (notes, enrichments, …) stay the PWA's job."""
        db.executescript("""
            CREATE TABLE IF NOT EXISTS revisions (
                id          TEXT PRIMARY KEY,
                note_id     TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
                body        TEXT NOT NULL,
                kind        TEXT NOT NULL DEFAULT 'pre_adopt'
                                CHECK(kind IN ('pre_adopt','edit')),
                created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            );
            CREATE INDEX IF NOT EXISTS idx_revisions_note ON revisions(note_id);

            CREATE TABLE IF NOT EXISTS reminders (
                id              TEXT PRIMARY KEY,
                note_id         TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
                title           TEXT NOT NULL,
                datetime_hint   TEXT,
                datetime_resolved TEXT,
                description     TEXT,
                calendar_event_id TEXT,
                created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            );

            CREATE TABLE IF NOT EXISTS relationships (
                note_id_a   TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
                note_id_b   TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
                score       REAL NOT NULL,
                computed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                PRIMARY KEY (note_id_a, note_id_b)
            );

            CREATE TABLE IF NOT EXISTS settings (
                key         TEXT PRIMARY KEY,
                value       TEXT NOT NULL,
                updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            );

            -- Ask-your-notes (see hg_ui's schema.sql for the full flow comment).
            -- The agent reads pending rows directly and writes the answer back
            -- both directly (guaranteed persistence even if the PWA's Flask
            -- process is down) and via the webhook (for the live SSE push).
            CREATE TABLE IF NOT EXISTS ask_requests (
                id              TEXT PRIMARY KEY,
                question        TEXT NOT NULL,
                context_json    TEXT NOT NULL DEFAULT '[]',
                status          TEXT NOT NULL DEFAULT 'pending'
                                    CHECK(status IN ('pending','done','error')),
                answer          TEXT,
                cited_note_ids  TEXT NOT NULL DEFAULT '[]',
                error           TEXT,
                created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                answered_at     TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_ask_status ON ask_requests(status);

            -- Passage-level retrieval index (see chunks.py). One row per ~500-char
            -- chunk of a note, tagged with the model that embedded it so vectors
            -- from different models are never compared. body_hash identifies what
            -- the chunks were built from (model+title+tags+body+chunking params):
            -- if it still matches, re-indexing is a no-op.
            CREATE TABLE IF NOT EXISTS note_chunks (
                id          TEXT PRIMARY KEY,
                note_id     TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
                idx         INTEGER NOT NULL,
                text        TEXT NOT NULL,
                embedding   BLOB NOT NULL,
                model       TEXT NOT NULL,
                body_hash   TEXT NOT NULL,
                created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            );
            CREATE INDEX IF NOT EXISTS idx_chunks_note  ON note_chunks(note_id);
            CREATE INDEX IF NOT EXISTS idx_chunks_model ON note_chunks(model);

            -- Notes that have nothing indexable (only highlight marks, or the model kept failing on them).
            -- Counted as done so the "getting your notes ready" progress can finish; re-tried when edited.
            CREATE TABLE IF NOT EXISTS index_skips (
                note_id    TEXT PRIMARY KEY REFERENCES notes(id) ON DELETE CASCADE,
                model      TEXT NOT NULL,
                reason     TEXT NOT NULL DEFAULT 'empty',
                body_hash  TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            );
        """)
        # Follow-up questions carry the last couple of Q/A turns (added after the
        # table first shipped, so an existing DB needs the column).
        try:
            db.execute("ALTER TABLE ask_requests ADD COLUMN "
                       "history_json TEXT NOT NULL DEFAULT '[]'")
        except sqlite3.OperationalError:
            pass   # already there
        from .insights import DUP_TABLE
        db.execute(DUP_TABLE)
        try:
            db.execute("ALTER TABLE ask_requests ADD COLUMN mode TEXT NOT NULL DEFAULT 'notes'")
        except sqlite3.OperationalError:
            pass
        try:
            db.execute("ALTER TABLE notes ADD COLUMN protected INTEGER NOT NULL DEFAULT 0")
        except Exception:
            pass
        try:
            db.execute("ALTER TABLE notes ADD COLUMN embedding TEXT")
        except Exception:
            pass
        # Which model produced notes.embedding (NULL = legacy, MiniLM era).
        try:
            db.execute("ALTER TABLE notes ADD COLUMN embed_model TEXT")
        except Exception:
            pass
        # Photos / voice notes / sketches (mirror of hg_ui/schema.sql).
        db.executescript("""
            CREATE TABLE IF NOT EXISTS attachments (
                id                TEXT PRIMARY KEY,
                note_id           TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
                kind              TEXT NOT NULL CHECK(kind IN ('image','audio','sketch','video')),
                mime              TEXT NOT NULL,
                filename          TEXT NOT NULL,
                size              INTEGER NOT NULL DEFAULT 0,
                width             INTEGER,
                height            INTEGER,
                duration_s        REAL,
                strokes           TEXT,
                transcript        TEXT NOT NULL DEFAULT '',
                transcript_status TEXT NOT NULL DEFAULT 'pending'
                                      CHECK(transcript_status IN ('none','pending','running','done','skipped','failed')),
                transcript_error  TEXT,
                transcript_model  TEXT,
                created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                updated_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            );
            CREATE INDEX IF NOT EXISTS idx_att_note ON attachments(note_id);
            CREATE INDEX IF NOT EXISTS idx_att_status ON attachments(transcript_status);
        """)
        db.commit()
        # Embedding bookkeeping must never touch notes.updated_at (it drives the
        # list order and the change watermark). Mirror of hg_ui's migration.
        try:
            row = db.execute("SELECT sql FROM sqlite_master WHERE type='trigger' "
                             "AND name='notes_updated'").fetchone()
            if not (row and "embed_model" in (row[0] or "")):
                db.execute("DROP TRIGGER IF EXISTS notes_updated")
                db.execute(
                    "CREATE TRIGGER notes_updated AFTER UPDATE ON notes "
                    "WHEN NEW.embedding IS OLD.embedding AND NEW.embed_model IS OLD.embed_model "
                    "BEGIN UPDATE notes SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                    "WHERE id = NEW.id; END;")
                db.commit()
        except Exception as e:
            log.warning(f"notes_updated trigger migration skipped: {e}")

    # ── DB connection pool (per-thread) ───────────────────────────────────────
    def _db(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = conn
        return self._local.conn

    def _exec(self, sql: str, params=()):
        db = self._db()
        cur = db.execute(sql, params)
        db.commit()
        return cur

    # ── Note dict shape (mirrors what daemon expects from MemosClient) ─────────
    def _note_dict(self, row: sqlite3.Row, state_row: sqlite3.Row | None = None) -> dict:
        """Convert a DB row to the dict shape the daemon expects.

        Daemon accesses:
          note["name"]        → unique string identifier
          note["content"]     → the text to enrich (pure user body)
          note["updateTime"]  → ISO string for watermarking
          note["tags"]        → the note's tags (from the tags table)
        """
        nid = row["id"]
        st  = state_row or self._db().execute(
            "SELECT * FROM note_state WHERE note_id=?", (nid,)).fetchone()
        # Tags live in their own table in Quicksilver (unlike the Memos-era
        # anchor block parsed out of the note body), so read them here. Without
        # this the correlation corpus sees no tags at all and any tag-aware
        # relationship logic silently no-ops.
        try:
            tags = [r["tag"] for r in self._db().execute(
                "SELECT tag FROM tags WHERE note_id=? ORDER BY tag", (nid,))]
        except Exception:
            tags = []
        # Enrichment title/summary, likewise stored relationally.
        title, summary = "", ""
        try:
            e = self._db().execute(
                "SELECT title, summary FROM enrichments WHERE note_id=? "
                "ORDER BY rowid DESC LIMIT 1", (nid,)).fetchone()
            if e:
                title = e["title"] or ""
                summary = e["summary"] or ""
        except Exception:
            pass
        return {
            "name":       nid,                          # identity the daemon uses
            "content":    strip_highlights(row["body"] or ""),   # pure user text, no markup
            "updateTime": row["updated_at"] or "",      # watermark field
            "id":         nid,
            "body":       strip_highlights(row["body"] or ""),
            # Persisted embedding (JSON string) so the corpus can reuse it
            # instead of recomputing. Absent on older rows → corpus recomputes.
            "embedding":  (row["embedding"] if "embedding" in row.keys() else None),
            "embed_model": (row["embed_model"] if "embed_model" in row.keys() else None),
            "tags":       tags,
            "title":      title,
            "summary":    summary,
            "visibility": row["visibility"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "status":     (st["status"] if st else "raw"),
            "note_type":  (row["note_type"] if "note_type" in row.keys() else "text"),
            "protected":  (row["protected"] if "protected" in row.keys() else 0),
            # Consent flag for auto-enrichment. Defaults to 0 (enrich normally)
            # when the column is absent, so an older DB behaves exactly as before.
            "enrich_optout": (row["enrich_optout"]
                              if "enrich_optout" in row.keys() else 0),
            "meta_only": (row["meta_only"]
                          if "meta_only" in row.keys() else 0),
            "user_edited": bool(self._db().execute(
                "SELECT 1 FROM revisions WHERE note_id=? AND kind='edit' LIMIT 1",
                (nid,)).fetchone()),
            "body_hash":  (st["body_hash"] if st else ""),
            # What the agent read out of the note's photos / recordings. Kept
            # OUT of "content" on purpose: the body stays the user's own words.
            "media_text": self.media_note_text(nid, row["body"] or ""),
        }

    # ── MemosClient interface ─────────────────────────────────────────────────

    def health(self) -> bool:
        try:
            self._db().execute("SELECT 1 FROM notes LIMIT 1")
            return True
        except Exception:
            return False

    def ping(self) -> bool:
        """Extended health check — also logs note count."""
        try:
            n = self._db().execute("SELECT COUNT(*) FROM notes").fetchone()[0]
            log.info(f"Quicksilver DB reachable — {n} note(s)")
            return True
        except Exception as e:
            log.error(f"Quicksilver DB not reachable: {e}")
            return False

    def get_memo(self, note_id: str) -> dict | None:
        """Fetch a single note by id."""
        row = self._db().execute(
            "SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
        if not row:
            log.warning(f"get_memo {note_id}: not found")
            return None
        return self._note_dict(row)

    def iter_changed_since(self, watermark: str, page_size: int) -> Iterator[dict]:
        """Yield notes updated after `watermark`, newest first.
        The daemon uses this for the safety poll and watermark tracking."""
        if watermark:
            rows = self._db().execute(
                "SELECT n.* FROM notes n "
                "WHERE n.updated_at > ? "
                "ORDER BY n.updated_at DESC LIMIT ?",
                (watermark, page_size)).fetchall()
        else:
            rows = self._db().execute(
                "SELECT n.* FROM notes n "
                "ORDER BY n.updated_at DESC LIMIT ?",
                (page_size,)).fetchall()
        for row in rows:
            yield self._note_dict(row)

    def iter_all_memos(self, page_size: int = 50) -> Iterator[dict]:
        """Yield all notes for corpus building (no watermark filter)."""
        offset = 0
        while True:
            rows = self._db().execute(
                "SELECT * FROM notes ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                (page_size, offset)).fetchall()
            if not rows:
                return
            for row in rows:
                yield self._note_dict(row)
            offset += len(rows)
            if len(rows) < page_size:
                return

    @_atomic
    def update_content(self, note_id: str, new_content: str) -> str | None | _PermanentError:
        """Write new body text to a note.

        In Quicksilver this is a clean DB write — no PATCH API, no size limit.
        Returns the note's updated_at timestamp on success (mirrors MemosClient's
        updateTime return value), or PATCH_PERMANENT_ERROR if the note is gone.
        """
        db = self._db()
        row = db.execute("SELECT id FROM notes WHERE id=?", (note_id,)).fetchone()
        if not row:
            log.warning(f"update_content {note_id}: note not found — permanent error")
            return PATCH_PERMANENT_ERROR

        new_body = new_content or ""
        db.execute("UPDATE notes SET body=? WHERE id=?", (new_body, note_id))
        db.commit()

        updated = db.execute(
            "SELECT updated_at FROM notes WHERE id=?", (note_id,)).fetchone()
        ut = updated["updated_at"] if updated else _now_iso()
        log.debug(f"update_content {note_id}: body written ({len(new_body)} chars)")
        return ut

    @staticmethod
    def _embed_text(title: str, tags: list, summary: str, body: str) -> str:
        """The text an embedding is computed from.

        MUST match Corpus.add() exactly (title + tags + summary + body, joined by
        newlines), or a stored vector won't equal a freshly-computed one and the
        two code paths would silently disagree. If you change one, change both.
        """
        parts = [p for p in [title, " ".join(tags or []), summary, body] if p]
        return "\n".join(parts)

    def _store_embedding(self, db, note_id: str, enrichment: dict, body: str):
        """Compute and persist notes.embedding for a note, best-effort.

        No-op when no embedder is attached (CLI path) or the model is
        unavailable — persistence is an optimisation, never a requirement, so a
        failure here must not fail the enrichment write.
        """
        emb = getattr(self, "embedder", None)
        if emb is None or not getattr(emb, "available", lambda: False)():
            return
        try:
            # Read the tags actually stored for this note (write_enrichment has
            # already merged them above), so the vector reflects the final tags,
            # not just the ones in this enrichment payload.
            tags = [r["tag"] for r in db.execute(
                "SELECT tag FROM tags WHERE note_id=? ORDER BY tag", (note_id,))]
            title = (enrichment.get("title") or "").strip()
            summary = (enrichment.get("summary") or "").strip()
            text = self._embed_text(title, tags, summary, body)
            if not text.strip():
                return
            vec = emb.encode(text)
            if vec:
                from .embeddings import encode_embedding
                db.execute("UPDATE notes SET embedding=?, embed_model=? WHERE id=?",
                           (encode_embedding(vec), getattr(emb, "tag", None), note_id))
        except Exception as e:
            log.debug(f"embedding persist skipped for {note_id}: {e}")

    @_atomic
    def write_enrichment(self, note_id: str, enrichment: dict,
                          new_status: str = "pending") -> bool:
        """Write AI enrichment output directly to the DB tables.

        This is the Quicksilver-specific call that replaces the daemon's
        build_suggested/build_approved + update_content pattern. The daemon
        calls this after inference instead of serialising output to markdown.

        Tables written:
          enrichments  — title, summary, polished, confidence, priority
          action_items — replaces all items for this note
          tags         — merges (never removes user-added tags)
          corrections  — replaces all for this note
          note_state   — updates status and body_hash
        """
        db = self._db()
        if not db.execute("SELECT 1 FROM notes WHERE id=?", (note_id,)).fetchone():
            log.warning(f"write_enrichment {note_id}: note not found")
            return False

        self.clear_error(note_id)   # a successful enrichment clears any prior error
        prot = db.execute("SELECT protected FROM notes WHERE id=?",
                          (note_id,)).fetchone()
        edited = db.execute(
            "SELECT 1 FROM revisions WHERE note_id=? AND kind='edit' LIMIT 1",
            (note_id,)).fetchone()
        # The daemon marks the enrichment when the user requested an on-demand
        # polish (see _consume_polish_request). We trust that single signal here
        # rather than re-reading any flag — one source of truth, no double-check
        # that can drift.
        allow_polish = bool(enrichment.get("_user_requested_polish"))
        if ((prot and prot["protected"]) or edited) and not allow_polish:
            # Protected OR user-edited → body is authoritative; never store a
            # polished rewrite unless the user explicitly asked for one.
            enrichment = dict(enrichment); enrichment["polished"] = None

        # Enrichment row. user_polish records whether this polished text was an
        # explicit on-demand request, so adoption can honour it durably.
        eid = _nanoid(12)
        db.execute("""
            INSERT INTO enrichments
                (id, note_id, title, summary, polished, confidence, priority, schema_version, user_polish)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (eid, note_id,
              (enrichment.get("title") or "").strip() or None,
              (enrichment.get("summary") or "").strip() or None,
              (enrichment.get("polished") or "").strip() or None,
              enrichment.get("confidence"),
              enrichment.get("priority"),
              enrichment.get("_schema_version", "2.1"),
              1 if allow_polish else 0))

        # Action items — replace, but NEVER resurrect what the user dismissed.
        # The AI re-extracts the same tasks from unchanged text on every run, so
        # a plain full-replace would bring a dismissed item straight back and the
        # user would be dismissing it forever. We keep dismissed rows and skip
        # re-inserting any item whose text matches one. Matching is on normalised
        # text: exact-ish, so a genuine rewording ("Pay bill" → "Pay the telecom
        # bill") will reappear — an honest limitation the user can dismiss again,
        # far better than fuzzy matching that silently swallows real tasks.
        # Also honours the per-note "don't extract tasks here" switch.
        no_actions_row = db.execute(
            "SELECT no_actions FROM notes WHERE id=?", (note_id,)).fetchone()
        no_actions = bool(no_actions_row["no_actions"]) if (
            no_actions_row and "no_actions" in no_actions_row.keys()) else False

        def _norm(t):
            return " ".join((t or "").lower().split()).rstrip(".!?")

        dismissed_texts = {
            _norm(r["text"]) for r in db.execute(
                "SELECT text FROM action_items WHERE note_id=? AND dismissed=1",
                (note_id,)).fetchall()}
        # Drop only the live (non-dismissed) rows; dismissed ones are the memory.
        db.execute("DELETE FROM action_items WHERE note_id=? AND COALESCE(dismissed,0)=0",
                   (note_id,))
        if not no_actions:
            for item in (enrichment.get("action_items") or []):
                # Accept BOTH the legacy string form and the newer categorized
                # object form {task, category, blocked_by}. Extract the task text
                # (that's what the `text` column and the whole UI expect); the
                # category/blocker are stored when the columns exist, ignored
                # otherwise, so this never breaks an un-migrated DB.
                if isinstance(item, dict):
                    task = (item.get("task") or item.get("text") or "").strip()
                    category = (item.get("category") or "").strip().lower()
                    if category not in ("work", "personal"):
                        category = ""
                    blocked_by = (item.get("blocked_by") or "").strip()
                else:
                    task = item.strip() if isinstance(item, str) else ""
                    _m = (enrichment.get("action_meta") or {}).get(task) or {}
                    category = _m.get("category", ""); blocked_by = _m.get("blocked_by", "")
                    if category not in ("work", "personal"):
                        category = ""
                if task.lower() in ("task", "text", "category", "work", "personal", "blocked_by"):
                    continue          # schema keys leaked from a malformed model reply
                if not task:
                    continue
                if _norm(task) in dismissed_texts:
                    continue              # user rejected this — don't bring it back
                # Prefer the extended insert; fall back if the columns are absent.
                try:
                    db.execute(
                        "INSERT INTO action_items (id, note_id, text, category, blocked_by) "
                        "VALUES (?,?,?,?,?)",
                        (_nanoid(10), note_id, task, category, blocked_by))
                except Exception:
                    db.execute(
                        "INSERT INTO action_items (id, note_id, text) VALUES (?,?,?)",
                        (_nanoid(10), note_id, task))

        # Tags — the AI's tag set fully REPLACES the previous AI tags, so a
        # tag the model no longer considers relevant disappears on re-run
        # (no stale accretion). User-authored #hashtags in the body are
        # preserved: we recompute them from the current body and never delete
        # them. Net set = {body hashtags} ∪ {fresh AI tags}.
        import re as _re
        body_txt = db.execute("SELECT body FROM notes WHERE id=?",
                              (note_id,)).fetchone()
        user_tags = set()
        if body_txt:
            user_tags = {m.lower() for m in
                         _re.findall(r"#(\w+)", body_txt["body"] or "")}
        ai_tags = set()
        for tag in (enrichment.get("tags") or []):
            t = str(tag).strip().lower().lstrip("#")
            if t:
                ai_tags.add(t)
        db.execute("DELETE FROM tags WHERE note_id=?", (note_id,))
        for t in (user_tags | ai_tags):
            db.execute("INSERT OR IGNORE INTO tags (note_id, tag) VALUES (?,?)",
                       (note_id, t))

        # Reminders — replace only AGENT-generated rows without a created
        # calendar event, so re-enrichment never duplicates events already on
        # the calendar AND never wipes a user's own Quick Event reminder
        # (source='user'), which must survive to become a calendar event.
        # Dismissed reminders are ALSO preserved and never re-suggested, for the
        # same reason as action items: the AI would otherwise re-extract them
        # from unchanged text on every run.
        no_rem_row = db.execute(
            "SELECT no_reminders FROM notes WHERE id=?", (note_id,)).fetchone()
        no_reminders = bool(no_rem_row["no_reminders"]) if (
            no_rem_row and "no_reminders" in no_rem_row.keys()) else False

        dismissed_rem = {
            _norm(r["title"]) for r in db.execute(
                "SELECT title FROM reminders WHERE note_id=? AND dismissed=1",
                (note_id,)).fetchall()}
        # Collect what's already live for this note: (normalized title,
        # resolved date, title text, embedding). New reminders are compared
        # against these. The rule (per design): a NEW reminder is a duplicate of
        # an EXISTING one when they share the same resolved DATE *and* their
        # titles are semantically similar (embedding cosine over threshold).
        # Same date alone is not enough — a note may legitimately have two
        # different tasks due the same day. Similar title alone is not enough —
        # two real deadlines can be worded alike. Both must hold.
        from .calendar_client import resolve_relative_date as _rrd
        from datetime import date as _date
        from .embeddings import cosine as _cos
        _emb = getattr(self, "embedder", None)
        _emb_ok = _emb is not None and getattr(_emb, "available", lambda: False)()
        def _date_of(hint, resolved):
            d = (resolved or "").strip()
            if not d and (hint or "").strip():
                try:
                    rd, _ = _rrd(hint.strip(), reference=_date.today())
                    d = rd.isoformat() if rd else ""
                except Exception:
                    d = ""
            return d[:10]
        _emb_cache = {}
        def _vec(text):
            if not _emb_ok or not text:
                return None
            if text not in _emb_cache:
                try: _emb_cache[text] = _emb.encode(text) or None
                except Exception: _emb_cache[text] = None
            return _emb_cache[text]
        # Similarity threshold for "same reminder, reworded". Titles are short,
        # so we want a fairly high bar to avoid merging distinct tasks. Reuses
        # the embedding cosine scale (~0.4–0.6 relates notes); 0.68 is stricter,
        # appropriate for near-paraphrase title matching. Configurable.
        _sim_thr = float(self.cfg.get("reminder_dupe_similarity", 0.68)) \
            if hasattr(self, "cfg") and isinstance(getattr(self, "cfg", None), dict) else 0.68

        live_rem = []
        for r in db.execute(
                "SELECT title, datetime_hint, datetime_resolved FROM reminders "
                "WHERE note_id=? AND COALESCE(dismissed,0)=0 AND ("
                "  (calendar_event_id IS NOT NULL AND calendar_event_id!='') "
                "  OR COALESCE(source,'agent')!='agent')", (note_id,)).fetchall():
            live_rem.append({"title": r["title"],
                             "date": _date_of(r["datetime_hint"], r["datetime_resolved"])})

        db.execute("DELETE FROM reminders WHERE note_id=? AND "
                   "(calendar_event_id IS NULL OR calendar_event_id='') "
                   "AND COALESCE(source,'agent')='agent' "
                   "AND COALESCE(dismissed,0)=0",
                   (note_id,))

        def _is_dupe(title, rdate, pool):
            """Same resolved date AND semantically-similar title = duplicate."""
            if not rdate:
                return False           # no date → never auto-merge
            tv = _vec(title)
            for ex in pool:
                if ex["date"] != rdate:
                    continue           # different day — not a duplicate
                if _norm(ex["title"]) == _norm(title):
                    return True        # identical title, same day
                if tv is not None:
                    ev = _vec(ex["title"])
                    if ev is not None and _cos(tv, ev) >= _sim_thr:
                        return True     # reworded same-day duplicate
            return False

        if not no_reminders:
            accepted = list(live_rem)
            for r in (enrichment.get("reminders") or []):
                if isinstance(r, dict) and (r.get("title") or "").strip():
                    title = r["title"].strip()
                    _t = _norm(title)
                    if _t in dismissed_rem:
                        continue      # user dismissed this reminder — respect it
                    _hint = (r.get("datetime_hint", "") or "").strip()
                    _rdate = _date_of(_hint, "")
                    if _is_dupe(title, _rdate, accepted):
                        continue      # same date + similar title → duplicate
                    accepted.append({"title": title, "date": _rdate})
                    db.execute(
                        "INSERT INTO reminders (id, note_id, title, datetime_hint, "
                        "datetime_resolved, description) VALUES (?,?,?,?,?,?)",
                        (_nanoid(10), note_id, title,
                         _hint, _rdate,
                         r.get("description", "") or ""))

        # Corrections
        db.execute("DELETE FROM corrections WHERE note_id=?", (note_id,))
        for c in (enrichment.get("corrections") or []):
            db.execute("""
                INSERT INTO corrections (id, note_id, from_text, to_text, confidence)
                VALUES (?,?,?,?,?)
            """, (_nanoid(10), note_id,
                  c.get("from", ""), c.get("to", ""), c.get("confidence")))

        # State
        body = db.execute(
            "SELECT body FROM notes WHERE id=?", (note_id,)).fetchone()["body"] or ""
        db.execute("""
            INSERT INTO note_state (note_id, status, body_hash, seen_at)
            VALUES (?,?,?,?)
            ON CONFLICT(note_id) DO UPDATE SET
                status    = excluded.status,
                body_hash = excluded.body_hash,
                seen_at   = excluded.seen_at,
                failures  = 0
        """, (note_id, new_status, _hash(body), _now_iso()))

        # Persist the embedding so neither the correlation corpus nor semantic
        # search has to recompute it. The vector is derived from the SAME text
        # the corpus uses (title + tags + summary + body) — see _embed_text — so
        # a stored vector is interchangeable with a freshly computed one. This is
        # computed here, right after enrichment, precisely because that is the
        # one moment title/tags/summary are known-fresh; storing it any later
        # risks embedding stale metadata.
        self._store_embedding(db, note_id, enrichment, body)

        db.commit()

        # Passage index for retrieval — AFTER the commit: index_note_chunks
        # commits its own transaction and must not flush a half-written
        # enrichment. Best-effort, like the note-level vector.
        try:
            self.index_note_chunks(note_id)
        except Exception as e:
            log.debug(f"chunk index skipped for {note_id}: {e}")

        # Notify PWA via webhook (best-effort, non-blocking)
        self._notify_webhook(note_id, enrichment, new_status)

        log.info(f"write_enrichment {note_id}: status={new_status}, "
                 f"title={enrichment.get('title')!r}")
        return True

    @_atomic
    def write_relationships(self, pairs: list[tuple[str, str, float]]):
        """Rebuild the relationships table from the current pair set.

        Relationships are a pure function of the corpus, so the correct model
        is replace-all, not append: clear every existing pair, then write the
        freshly-computed ones. This prevents stale links from lingering after
        notes are edited, deleted, or re-clustered (the accretion that showed
        up as a note relating to many near-identical duplicates)."""
        db = self._db()
        db.execute("DELETE FROM relationships")
        # Deduplicate at the source: one canonical (min,max) key per pair, and
        # keep the highest score if the same pair arrives more than once.
        best = {}
        for a, b, score in pairs:
            if not a or not b or a == b:
                continue
            k = (a, b) if a < b else (b, a)
            if k not in best or score > best[k]:
                best[k] = score
        pairs = [(a, b, s) for (a, b), s in best.items()]
        for a, b, score in pairs:
            if a == b:
                continue
            # Skip pairs where either note doesn't exist (FK constraint)
            if not db.execute("SELECT 1 FROM notes WHERE id=?", (a,)).fetchone():
                continue
            if not db.execute("SELECT 1 FROM notes WHERE id=?", (b,)).fetchone():
                continue
            db.execute("""
                INSERT INTO relationships (note_id_a, note_id_b, score)
                VALUES (?,?,?)
                ON CONFLICT(note_id_a, note_id_b) DO UPDATE SET
                    score=excluded.score,
                    computed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
            """, (a, b, score))
            db.execute("""
                INSERT INTO relationships (note_id_a, note_id_b, score)
                VALUES (?,?,?)
                ON CONFLICT(note_id_a, note_id_b) DO UPDATE SET
                    score=excluded.score,
                    computed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
            """, (b, a, score))
        db.commit()
        log.info(f"write_relationships: {len(pairs)} pair(s) written")

    @_atomic
    def heartbeat(self, telemetry: dict | None = None):
        """Write a liveness timestamp + optional telemetry blob the PWA surfaces
        as the agent-status dot and the Pulse dashboard (thermal, latencies,
        counts, model). One JSON row keeps it a single cheap write."""
        try:
            db = self._db()
            db.execute(
                "INSERT INTO settings (key, value) VALUES ('agent_heartbeat', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')", (_now_iso(),))
            if telemetry is not None:
                import json as _j
                db.execute(
                    "INSERT INTO settings (key, value) VALUES ('agent_telemetry', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                    "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')",
                    (_j.dumps(telemetry),))
            db.commit()
        except Exception as e:
            log.debug(f"heartbeat write failed: {e}")

    def auto_backup(self, keep: int = 7, min_interval_h: float = 24):
        """Daily on-device backup with retention — data durability without the
        user having to remember anything. No-op if the newest auto-backup is
        fresher than min_interval_h. Uses SQLite's online backup API (WAL-safe
        while both hg_ui and hg_agent are running)."""
        import time as _t, sqlite3 as s3
        try:
            bdir = Path(self.db_path).parent / "backups"
            bdir.mkdir(parents=True, exist_ok=True)
            autos = sorted(bdir.glob("auto-*.db"))
            if autos and (_t.time() - autos[-1].stat().st_mtime) < min_interval_h * 3600:
                return
            dest = bdir / f"auto-{_t.strftime('%Y%m%d-%H%M%S')}.db"
            src_c = s3.connect(self.db_path); dst_c = s3.connect(str(dest))
            with dst_c:
                src_c.backup(dst_c)
            src_c.close(); dst_c.close()
            for old_f in sorted(bdir.glob("auto-*.db"))[:-keep]:
                old_f.unlink(missing_ok=True)
            log.info(f"🗄 auto-backup: {dest.name} (keeping last {keep})")
        except Exception as e:
            log.warning(f"auto-backup failed: {e}")

    @_atomic
    def record_error(self, note_id: str, reason: str):
        """Append a note to the agent-errors log (settings key 'agent_errors'),
        de-duplicated by note id. The PWA reads this to show WHICH notes failed."""
        import json as _j, time as _t
        try:
            db = self._db()
            row = db.execute("SELECT value FROM settings WHERE key='agent_errors'").fetchone()
            errs = []
            if row:
                try: errs = _j.loads(row["value"])
                except Exception: errs = []
            errs = [e for e in errs if e.get("id") != note_id]   # de-dup
            errs.insert(0, {"id": note_id, "reason": reason, "ts": _t.time()})
            errs = errs[:50]
            db.execute(
                "INSERT INTO settings (key, value) VALUES ('agent_errors', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (_j.dumps(errs),))
            db.commit()
        except Exception as e:
            log.debug(f"record_error failed: {e}")

    # ── 'force' (Processing) limbo recovery ──────────────────────────────────
    # A note is 'force' from the moment the user asks for (re)processing until
    # the agent writes an enrichment. If the run ends WITHOUT one — inference
    # failed, the note was skipped, or the agent was killed mid-run — nothing
    # else ever moves it, and the PWA shows "Processing" forever. These three
    # helpers let the daemon always resolve it one way or the other.

    def list_force(self, older_than_s: int = 0) -> list:
        """Notes currently 'force' whose state hasn't moved for `older_than_s`."""
        try:
            rows = self._db().execute(
                "SELECT note_id, failures FROM note_state "
                "WHERE status='force' AND "
                "updated_at <= strftime('%Y-%m-%dT%H:%M:%fZ','now', ?)",
                (f'-{int(older_than_s)} seconds',)).fetchall()
            return [(r["note_id"], r["failures"] or 0) for r in rows]
        except Exception as e:
            log.debug(f"list_force failed: {e}")
            return []

    @_atomic
    def force_failed(self, note_id: str) -> int:
        """Count one failed attempt on a 'force' note; returns the new total.
        (Touching the row also refreshes updated_at → natural retry backoff.)"""
        try:
            db = self._db()
            db.execute("UPDATE note_state SET failures=COALESCE(failures,0)+1 "
                       "WHERE note_id=? AND status='force'", (note_id,))
            db.commit()
            r = db.execute("SELECT failures FROM note_state WHERE note_id=?",
                           (note_id,)).fetchone()
            return int(r["failures"]) if r else 0
        except Exception as e:
            log.debug(f"force_failed failed: {e}")
            return 0

    @_atomic
    def release_force(self, note_id: str) -> str:
        """Leave 'force' for a sensible resting status so the UI stops showing
        "Processing": 'approved' if the note already has an enrichment (it was
        a living document being re-run), else 'raw'. Only acts on a note that is
        still 'force', so it can never undo a real result."""
        try:
            db = self._db()
            has_enr = db.execute("SELECT 1 FROM enrichments WHERE note_id=? LIMIT 1",
                                 (note_id,)).fetchone()
            rest = "approved" if has_enr else "raw"
            cur = db.execute("UPDATE note_state SET status=? "
                             "WHERE note_id=? AND status='force'", (rest, note_id))
            db.commit()
            return rest if cur.rowcount else ""
        except Exception as e:
            log.debug(f"release_force failed: {e}")
            return ""

    @_atomic
    def clear_error(self, note_id: str):
        """Remove a note from the error log once it enriches successfully."""
        import json as _j
        try:
            db = self._db()
            row = db.execute("SELECT value FROM settings WHERE key='agent_errors'").fetchone()
            if not row:
                return
            errs = _j.loads(row["value"])
            new_errs = [e for e in errs if e.get("id") != note_id]
            if len(new_errs) != len(errs):
                db.execute("UPDATE settings SET value=? WHERE key='agent_errors'",
                           (_j.dumps(new_errs),))
                db.commit()
        except Exception:
            pass

    @_atomic
    def store_embedding(self, note_id: str, vector):
        """Persist a note's embedding vector (JSON float[]) so the PWA can run
        semantic search without loading an embedding model itself."""
        if not vector:
            return
        try:
            import json as _j
            db = self._db()
            emb = getattr(self, "embedder", None)
            db.execute("UPDATE notes SET embedding=?, embed_model=? WHERE id=?",
                       (_j.dumps([round(float(x), 6) for x in vector]),
                        getattr(emb, "tag", None), note_id))
            db.commit()
        except Exception as e:
            log.debug(f"store_embedding failed for {note_id}: {e}")

    # ── Passage index ─────────────────────────────────────────────────────────
    CHUNK_PARAMS = None   # set from config by the daemon: (max_chars, overlap, max_chunks)

    def _chunk_params(self):
        return self.CHUNK_PARAMS or (500, 80, 40)

    @_atomic
    def _skips_ready(self):
        """An app older than this agent may not have created index_skips yet."""
        if not getattr(self, "_skips_ok", False):
            db = self._db()
            db.execute('''CREATE TABLE IF NOT EXISTS index_skips (
    note_id    TEXT PRIMARY KEY REFERENCES notes(id) ON DELETE CASCADE,
    model      TEXT NOT NULL,
    reason     TEXT NOT NULL DEFAULT 'empty',
    body_hash  TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
)''')
            db.commit()
            self._skips_ok = True

    def mark_index_skip(self, note_id: str, reason: str = "failed"):
        """Count a note as indexed-as-far-as-possible so progress can finish. An empty hash means the next
        normal (non-forced) index attempt always runs, e.g. after the note is edited."""
        self._skips_ready()
        emb = getattr(self, "embedder", None)
        db = self._db()
        db.execute("INSERT OR REPLACE INTO index_skips (note_id, model, reason, body_hash) VALUES (?,?,?,'')",
                   (note_id, getattr(emb, "tag", "") if emb else "", reason))
        db.commit()

    def index_note_chunks(self, note_id: str, force: bool = False) -> int:
        """(Re)build the chunk index for one note. Returns chunks written, or -1
        when skipped (no embedder, or already up to date).

        Each chunk is embedded WITH the note's title and tags as a header — a
        passage like "Call back on Thursday" is meaningless alone but retrievable
        once it carries "Dr. Rao appointment". The stored `text` is the bare chunk
        (that is what Ask quotes). All chunks are computed first and written in
        one transaction: a failure midway leaves the previous index intact.
        """
        emb = getattr(self, "embedder", None)
        if emb is None or not getattr(emb, "available", lambda: False)():
            return -1
        from .chunks import chunk_text, content_hash
        from .embeddings import encode_embedding
        db = self._db()
        row = db.execute("SELECT body FROM notes WHERE id=?", (note_id,)).fetchone()
        if row is None:
            return -1
        body = strip_highlights(row["body"] or "")
        mt = self.media_note_text(note_id, body)
        if mt:
            body = (body + "\n\n" + mt).strip()
        tags = [r["tag"] for r in db.execute(
            "SELECT tag FROM tags WHERE note_id=? ORDER BY tag", (note_id,))]
        e = db.execute("SELECT title FROM enrichments WHERE note_id=? "
                       "ORDER BY enriched_at DESC, rowid DESC LIMIT 1", (note_id,)).fetchone()
        title = ((e["title"] if e else "") or "").strip()
        mx, ov, cap = self._chunk_params()
        h = content_hash(emb.tag, title, tags, body, f"{mx}/{ov}/{cap}")
        self._skips_ready()
        if not force:
            cur = db.execute("SELECT body_hash FROM note_chunks WHERE note_id=? LIMIT 1",
                             (note_id,)).fetchone()
            if cur and cur["body_hash"] == h:
                return -1
            sk = db.execute("SELECT body_hash FROM index_skips WHERE note_id=? AND model=?",
                            (note_id, emb.tag)).fetchone()
            if sk and sk["body_hash"] == h:
                return -1
        chunks = chunk_text(body, mx, ov, cap)
        if not chunks:
            # Nothing to index (a body that is only highlight marks, say). Say so, instead of leaving the
            # note on the work list for ever and the progress bar one short.
            try:
                db.execute("DELETE FROM note_chunks WHERE note_id=?", (note_id,))
                db.execute("INSERT OR REPLACE INTO index_skips (note_id, model, reason, body_hash) VALUES (?,?,?,?)",
                           (note_id, emb.tag, "empty", h))
                db.commit()
            except Exception:
                db.rollback()
                raise
            return 0
        header = "\n".join(p for p in (title, " ".join(tags)) if p)
        rows = []
        for i, c in enumerate(chunks):
            vec = emb.encode((header + "\n" + c) if header else c, kind="passage")
            if vec is None:
                return -1                     # model went away: keep old index
            rows.append((f"{note_id}:{i}", note_id, i, c, encode_embedding(vec), emb.tag, h))
        try:
            db.execute("DELETE FROM note_chunks WHERE note_id=?", (note_id,))
            db.execute("DELETE FROM index_skips WHERE note_id=?", (note_id,))
            db.executemany("INSERT INTO note_chunks (id,note_id,idx,text,embedding,model,body_hash) "
                           "VALUES (?,?,?,?,?,?,?)", rows)
            db.commit()
        except Exception:
            db.rollback()
            raise
        return len(rows)

    # ── photos / voice notes / sketches ───────────────────────────────────────
    def media_dir(self):
        import os
        from pathlib import Path
        d = os.environ.get("QS_ATT_DIR")
        return Path(d) if d else Path(self.db_path).parent / "attachments"

    def media_note_text(self, note_id: str, body: str | None = None) -> str:
        """The text read out of a note's attachments, one labelled line each.
        A transcript already contained in the body (a voice note that became its
        own text) is left out so nothing is counted twice."""
        try:
            rows = self._db().execute(
                "SELECT kind, transcript FROM attachments WHERE note_id=? AND transcript<>'' "
                "ORDER BY created_at", (note_id,)).fetchall()
            if body is None:
                r = self._db().execute("SELECT body FROM notes WHERE id=?", (note_id,)).fetchone()
                body = (r["body"] if r else "") or ""
        except Exception:
            return ""
        label = {"audio": "Voice note", "image": "Photo text", "sketch": "Sketch text", "video": "Video"}
        return "\n".join(f"[{label.get(r['kind'], 'Attachment')}] {r['transcript'].strip()}"
                         for r in rows if r["transcript"].strip() not in body)

    def media_pending(self, limit: int = 20) -> list:
        try:
            rows = self._db().execute(
                "SELECT id, note_id, kind, mime, filename FROM attachments "
                "WHERE transcript_status='pending' ORDER BY created_at LIMIT ?", (int(limit),)).fetchall()
        except Exception:
            return []
        return [dict(r) for r in rows]

    def media_get(self, aid: str) -> dict | None:
        r = self._db().execute("SELECT * FROM attachments WHERE id=?", (aid,)).fetchone()
        return dict(r) if r else None

    @_atomic
    def media_mark(self, aid: str, status: str, text: str | None = None,
                   error: str | None = None, model: str | None = None) -> None:
        """Record where an attachment's reading stands. `text` (when given) replaces the transcript."""
        sets, vals = ["transcript_status=?", "transcript_error=?", "updated_at=?"], [status, error, _now_iso()]
        if text is not None:
            sets.append("transcript=?"); vals.append(text)
        if model is not None:
            sets.append("transcript_model=?"); vals.append(model)
        vals.append(aid)
        self._exec(f"UPDATE attachments SET {', '.join(sets)} WHERE id=?", tuple(vals))

    def notes_needing_index(self, limit: int = 20) -> list:
        """Ids of notes with a non-empty body but no chunks from the ACTIVE model —
        the work list for the background (re-)index. Newest first, so recent notes
        become searchable first after a model switch."""
        emb = getattr(self, "embedder", None)
        tag = getattr(emb, "tag", "") if emb else ""
        self._skips_ready()
        rows = self._db().execute(
            "SELECT n.id FROM notes n WHERE (TRIM(COALESCE(n.body,''))<>'' OR EXISTS "
            "(SELECT 1 FROM attachments a WHERE a.note_id=n.id AND a.transcript<>'')) AND NOT EXISTS "
            "(SELECT 1 FROM note_chunks c WHERE c.note_id=n.id AND c.model=?) AND NOT EXISTS "
            "(SELECT 1 FROM index_skips k WHERE k.note_id=n.id AND k.model=?) "
            "ORDER BY n.updated_at DESC LIMIT ?", (tag, tag, int(limit))).fetchall()
        return [r["id"] for r in rows]

    def index_progress(self) -> dict:
        emb = getattr(self, "embedder", None)
        tag = getattr(emb, "tag", "") if emb else ""
        db = self._db()
        total = db.execute("SELECT COUNT(*) c FROM notes n WHERE TRIM(COALESCE(n.body,''))<>'' OR EXISTS "
                           "(SELECT 1 FROM attachments a WHERE a.note_id=n.id AND a.transcript<>'')").fetchone()["c"]
        self._skips_ready()
        done = db.execute("SELECT COUNT(DISTINCT note_id) c FROM note_chunks WHERE model=?",
                          (tag,)).fetchone()["c"]
        done += db.execute("SELECT COUNT(*) c FROM index_skips WHERE model=? AND note_id NOT IN "
                           "(SELECT note_id FROM note_chunks WHERE model=?)", (tag, tag)).fetchone()["c"]
        return {"model": tag, "done": min(done, total), "total": total}

    @_atomic
    def set_embed_active(self, info: dict):
        """Publish the active embedder (model, dim, profile) so the app — a
        separate process — ranks against the same model and gates by its profile."""
        import json as _j
        self._exec("INSERT INTO settings (key,value,updated_at) VALUES ('embed_active',?,?) "
                   "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                   "updated_at=excluded.updated_at", (_j.dumps(info), _now_iso()))

    @_atomic
    def adopt_polished(self, note_id: str) -> str | None:
        """Adoption-on-approve: make the latest enrichment's polished text the
        note's new body, archiving the previous body as a 'pre_adopt' revision.
        Returns the new body (for hash bookkeeping), or None if there was
        nothing to adopt (no polished text, or identical to current body)."""
        db = self._db()
        row = db.execute("SELECT body FROM notes WHERE id=?", (note_id,)).fetchone()
        if not row:
            return None
        enr = db.execute(
            "SELECT polished, user_polish FROM enrichments WHERE note_id=? "
            "ORDER BY enriched_at DESC, rowid DESC LIMIT 1", (note_id,)).fetchone()
        polished = (enr["polished"] if enr else "") or ""
        polished = polished.strip()
        user_polish = bool(enr and enr["user_polish"])
        cur_body = (row["body"] or "").strip()
        prot = db.execute("SELECT protected FROM notes WHERE id=?",
                          (note_id,)).fetchone()
        if prot and prot["protected"]:
            return None                      # user asked: keep original verbatim
        if "```" in cur_body:
            log.info(f"adopt_polished {note_id}: code fence detected — keeping original")
            return None
        # User-authored bodies are authoritative: if this note has ever been
        # manually edited, never overwrite it with the AI polish — UNLESS this
        # enrichment carries the durable user_polish marker (an explicit
        # on-demand Polish request). Mirrors the write_enrichment gate.
        edited = db.execute(
            "SELECT 1 FROM revisions WHERE note_id=? AND kind='edit' LIMIT 1",
            (note_id,)).fetchone()
        if edited and not user_polish:
            log.info(f"adopt_polished {note_id}: user-edited — keeping their text")
            return None
        if not polished or polished == cur_body:
            return None
        db.execute(
            "INSERT INTO revisions (id, note_id, body, kind) VALUES (?,?,?,?)",
            (_nanoid(10), note_id, cur_body, "pre_adopt"))
        db.execute("UPDATE notes SET body=? WHERE id=?", (polished, note_id))
        db.commit()
        log.info(f"adopt_polished {note_id}: polished text is now the note body "
                 f"({len(cur_body)} → {len(polished)} chars, original archived)")
        return polished

    def create_note(self, content: str) -> str:
        """Create a note and return its id (used by consolidation drafting)."""
        m = self.create_memo(content)
        return m["name"] if m and "name" in m else (m["id"] if m and "id" in m else "")

    @_atomic
    def create_memo(self, content: str, pinned: bool = False) -> dict | None:
        """Create a new note (used by daemon for dashboard notes).
        Quicksilver doesn't have pinned notes — the flag is ignored."""
        nid = _nanoid(8)
        db = self._db()
        db.execute(
            "INSERT INTO notes (id, body, visibility) VALUES (?,?,?)",
            (nid, content or "", "private"))
        db.execute(
            "INSERT INTO note_state (note_id, status, body_hash) VALUES (?,?,?)",
            (nid, "raw", _hash(content or "")))
        db.commit()
        row = db.execute("SELECT * FROM notes WHERE id=?", (nid,)).fetchone()
        return self._note_dict(row)

    def update_pinned(self, note_id: str, pinned: bool):
        """No-op for Quicksilver (no pinning concept — use tags instead)."""
        pass

    # ── Webhook notification (SSE push to PWA) ────────────────────────────────
    def _notify_webhook(self, note_id: str, enrichment: dict, status: str, event: str | None = None):
        """POST to the PWA's /api/webhook so connected browsers get a live SSE
        push. Best-effort: failures are logged but never propagate to the
        caller. The enrichment itself was already persisted directly above —
        /api/webhook only reads note_id (it looks up current state itself and
        broadcasts that), so there's nothing to gain from re-sending the full
        enrichment dict or recomputing a body_hash over the wire."""
        if not self.webhook_url:
            return
        body = {"note_id": note_id}
        if event:
            body["event"] = event        # e.g. "attachment_ready": refresh quietly, no toast
        # Fire in a daemon thread so we never block the worker
        import threading
        def _post():
            for attempt in range(self.max_retries):
                try:
                    r = requests.post(
                        f"{self.webhook_url}/api/webhook",
                        json=body, timeout=5)
                    if r.ok:
                        log.debug(f"webhook notified for {note_id}")
                        return
                    log.warning(f"webhook {r.status_code} for {note_id}")
                    return  # 4xx — don't retry
                except Exception as e:
                    delay = self.backoff_base * (2 ** attempt)
                    log.debug(f"webhook attempt {attempt+1} failed: {e} — retry in {delay}s")
                    time.sleep(delay)
            log.warning(f"webhook notification failed for {note_id} after {self.max_retries} attempts")
        threading.Thread(target=_post, daemon=True).start()

    # ── Ask your notes ─────────────────────────────────────────────────────
    def _ask_request_dict(self, row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "question": row["question"],
            "context": json.loads(row["context_json"] or "[]"),
            "history": json.loads((row["history_json"] if "history_json" in row.keys() else "") or "[]"),
            "mode": (row["mode"] if "mode" in row.keys() else "notes") or "notes",
            "status": row["status"],
            "created_at": row["created_at"],
        }

    def get_ask_request(self, request_id: str) -> dict | None:
        """Fetch a single ask_requests row. Direct SQLite read — same reasoning
        as get_memo: a cheap indexed SELECT needs no HTTP round-trip."""
        row = self._db().execute(
            "SELECT * FROM ask_requests WHERE id=?", (request_id,)).fetchone()
        return self._ask_request_dict(row) if row else None

    def get_pending_ask_requests(self, min_age_seconds: float = 0,
                                  limit: int = 10) -> list[dict]:
        """Rows still 'pending', for the safety-poll backstop (the SSE path is
        primary — see daemon._qs_sse_loop). min_age_seconds filters out a
        request that's merely mid-flight in the worker right now: the row
        stays 'pending' in the DB for the few seconds inference takes, and a
        safety-poll tick landing in that window would otherwise re-enqueue a
        duplicate job (the in-memory _queued dedup only covers the time a job
        sits WAITING in queue, not while it's actively being processed)."""
        cutoff = _now_iso_minus(min_age_seconds)
        rows = self._db().execute(
            "SELECT * FROM ask_requests WHERE status='pending' AND created_at <= ? "
            "ORDER BY created_at ASC LIMIT ?", (cutoff, limit)).fetchall()
        return [self._ask_request_dict(r) for r in rows]

    @_atomic
    def write_ask_answer(self, request_id: str, answer: str | None,
                          cited_note_ids: list | None, status: str = "done",
                          error: str | None = None) -> bool:
        """Persist the answer directly — guaranteed, independent of whether the
        PWA's Flask process is even running, same reasoning as write_enrichment
        writing tables itself rather than relying on the webhook. The webhook
        notify below is then purely for a live SSE push to any open PWA tab;
        unlike write_enrichment's webhook (which would INSERT a second
        enrichments row if it fired twice), this one is safe to be fully
        redundant with the direct write — it's an UPDATE either way, so a
        duplicate application is a no-op."""
        db = self._db()
        if not db.execute("SELECT 1 FROM ask_requests WHERE id=?",
                          (request_id,)).fetchone():
            log.warning(f"write_ask_answer {request_id}: request not found")
            return False
        db.execute(
            "UPDATE ask_requests SET status=?, answer=?, cited_note_ids=?, "
            "error=?, answered_at=? WHERE id=?",
            (status, answer, json.dumps(cited_note_ids or []), error,
             _now_iso(), request_id))
        db.commit()
        self._notify_ask_webhook(request_id, status, answer, cited_note_ids, error)
        log.info(f"write_ask_answer {request_id}: status={status}")
        return True

    def _notify_ask_webhook(self, request_id: str, status: str,
                             answer: str | None, cited_note_ids: list | None,
                             error: str | None):
        """POST the answer to /api/ask/answer so connected browsers get the
        live SSE push. Best-effort, mirrors _notify_webhook exactly."""
        if not self.webhook_url:
            return
        body = {"request_id": request_id, "status": status, "answer": answer,
                "cited_note_ids": cited_note_ids or [], "error": error}
        def _post():
            for attempt in range(self.max_retries):
                try:
                    r = requests.post(f"{self.webhook_url}/api/ask/answer",
                                      json=body, timeout=5)
                    if r.ok:
                        log.debug(f"ask webhook notified for {request_id}")
                        return
                    log.warning(f"ask webhook {r.status_code} for {request_id}")
                    return
                except Exception as e:
                    delay = self.backoff_base * (2 ** attempt)
                    log.debug(f"ask webhook attempt {attempt+1} failed: {e} — "
                             f"retry in {delay}s")
                    time.sleep(delay)
            log.warning(f"ask webhook notification failed for {request_id} "
                       f"after {self.max_retries} attempts")
        threading.Thread(target=_post, daemon=True).start()
