"""Database setup: schema script, additive migrations and first-run seeding."""
import sqlite3

from .core import BASE_DIR, DB_PATH, _NOTES_UPDATED_TRIGGER, app, get_db, log
from .fts import _init_fts
from .highlights import _migrate_highlights


def _fix_updated_trigger(db):
    """Replace a legacy notes_updated trigger (no embedding guard). Idempotent."""
    try:
        row = db.execute("SELECT sql FROM sqlite_master WHERE type='trigger' "
                         "AND name='notes_updated'").fetchone()
        if row and "embed_model" in (row[0] or ""):
            return
        cols = {r[1] for r in db.execute("PRAGMA table_info(notes)")}
        if not {"embedding", "embed_model"} <= cols:
            return
        db.execute("DROP TRIGGER IF EXISTS notes_updated")
        db.execute(_NOTES_UPDATED_TRIGGER)
        db.commit()
        log.info("migrated notes_updated trigger: embedding writes no longer touch updated_at")
    except Exception as e:
        log.warning(f"notes_updated trigger migration skipped: {e}")

_SMS_DDL = """CREATE TABLE IF NOT EXISTS index_skips (
    note_id    TEXT PRIMARY KEY REFERENCES notes(id) ON DELETE CASCADE,
    model      TEXT NOT NULL,
    reason     TEXT NOT NULL DEFAULT 'empty',
    body_hash  TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE IF NOT EXISTS sms_bills (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    key          TEXT NOT NULL UNIQUE,
    received_at  TEXT NOT NULL,
    sender       TEXT NOT NULL DEFAULT '',
    merchant     TEXT NOT NULL DEFAULT '',
    merchant_key TEXT NOT NULL DEFAULT '',
    amount       REAL NOT NULL DEFAULT 0,
    account      TEXT NOT NULL DEFAULT '',
    due_date     TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','paid','expired','dismissed')),
    reminder_ids TEXT NOT NULL DEFAULT '[]',
    body         TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_sms_bills_due ON sms_bills(due_date, status);
"""


_ATT_DDL = """CREATE TABLE attachments (
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
)"""

def migrate_attachments(db) -> bool:
    """attachments.kind gained 'video'. A CHECK can't be altered, so rebuild the table (every row and column
    kept). Returns True when the table accepts video. Never raises: if the agent holds the lock it returns False
    and is tried again on the next video upload."""
    try:
        a = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='attachments'").fetchone()
        if not a or "'video'" in (a[0] or ""):
            return True
        db.execute("PRAGMA foreign_keys=OFF")
        db.execute("BEGIN IMMEDIATE")
        db.execute("ALTER TABLE attachments RENAME TO attachments_old")
        db.execute(_ATT_DDL)
        cols = [r[1] for r in db.execute("PRAGMA table_info(attachments)").fetchall()]
        old = {r[1] for r in db.execute("PRAGMA table_info(attachments_old)").fetchall()}
        use = ", ".join(c for c in cols if c in old)
        db.execute(f"INSERT INTO attachments ({use}) SELECT {use} FROM attachments_old")
        db.execute("DROP TABLE attachments_old")
        db.execute("CREATE INDEX IF NOT EXISTS idx_att_note ON attachments(note_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_att_status ON attachments(transcript_status)")
        db.commit()
        db.execute("PRAGMA foreign_keys=ON")
        log.info("migrated attachments table: video notes enabled")
        return True
    except Exception as e:
        try: db.rollback()
        except Exception: pass
        try: db.execute("PRAGMA foreign_keys=ON")
        except Exception: pass
        log.warning(f"attachments migration deferred ({e})")
        return False

def init_db():
    schema = BASE_DIR / "schema.sql"
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
            migrate_attachments(db)
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
            try:   # which model produced notes.embedding (NULL = legacy MiniLM)
                db.execute("ALTER TABLE notes ADD COLUMN embed_model TEXT")
            except Exception:
                pass
            _fix_updated_trigger(db)
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
                             # user_title: a title the person typed. It wins over the agent's title and the agent never writes it.
                             ("user_title","ALTER TABLE notes ADD COLUMN user_title TEXT NOT NULL DEFAULT ''"),
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
                             ("meta_only","ALTER TABLE notes ADD COLUMN meta_only INTEGER NOT NULL DEFAULT 0"),
                             # Manual colour override — a palette index (0-7), not a
                             # hex, so it adapts across theme switches. NULL = automatic.
                             ("color_idx","ALTER TABLE notes ADD COLUMN color_idx INTEGER")]:
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
                "CREATE INDEX IF NOT EXISTS idx_ask_status ON ask_requests(status)",
                # Follow-up questions: the last couple of Q/A turns, so the agent can
                # resolve "and the one after that?" (see /api/ask).
                "ALTER TABLE ask_requests ADD COLUMN history_json TEXT NOT NULL DEFAULT '[]'",
                # Ask modes: notes (default) | chat (plain model prompt) | write (long-form from notes).
                "ALTER TABLE ask_requests ADD COLUMN mode TEXT NOT NULL DEFAULT 'notes'"]:
                try: db.execute(ddl)
                except Exception: pass
            # Bills from messages (and the search-index skip list): created here too so an older database upgrades in place.
            for stmt in [x.strip() for x in _SMS_DDL.split(";") if x.strip()]:
                try: db.execute(stmt)
                except Exception as e: log.warning(f"sms table setup skipped: {e}")
            try: db.execute("ALTER TABLE sms_bills ADD COLUMN body TEXT NOT NULL DEFAULT ''")
            except Exception: pass
            # The v1.4 spend tables are gone. Take their reminders with them and reset the agent's watermark so the
            # bills are read again with the stricter rules.
            try:
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='sms_events'").fetchone():
                    host = db.execute("SELECT value FROM settings WHERE key='sms_host_note'").fetchone()
                    if host:
                        db.execute("DELETE FROM reminders WHERE note_id=?", (host[0],))
                    for t in ("sms_events", "sms_merchant_cat", "sms_skipped"):
                        db.execute(f"DROP TABLE IF EXISTS {t}")
                    db.execute("DELETE FROM settings WHERE key IN ('sms_watermark','sms_status','sms_last_poll','sms_config')")
            except Exception as e: log.warning(f"spend table removal skipped: {e}")
            # Tags are case-insensitive: fold history to lowercase, dedupe
            db.execute("UPDATE OR IGNORE tags SET tag = lower(tag)")
            db.execute("DELETE FROM tags WHERE tag <> lower(tag)")
            db.commit()
            _init_fts(db)
            try: _migrate_highlights(db)
            except Exception as e: log.warning(f"highlight migration skipped: {e}")
            log.info(f"Database initialised at {DB_PATH}")
