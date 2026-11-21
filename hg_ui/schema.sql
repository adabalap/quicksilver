-- Quicksilver / పాదరసం — note-taking PWA schema
-- Clean separation: notes are pure text, AI output lives in dedicated tables.
-- The daemon reads/writes these tables directly — no PATCH API, no content parsing.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ── Core note ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS notes (
    id          TEXT PRIMARY KEY,          -- nanoid, e.g. "k7TNhdXp"
    body        TEXT NOT NULL DEFAULT '',  -- pure user text, nothing else
    protected   INTEGER NOT NULL DEFAULT 0, -- 1 = never rewrite/adopt this body
    pinned      INTEGER NOT NULL DEFAULT 0, -- 1 = keep at top of the feed
    note_type   TEXT NOT NULL DEFAULT 'text', -- 'text' | 'checklist'
    is_daily    INTEGER NOT NULL DEFAULT 0, -- 1 = auto-created daily journal note
    daily_date  TEXT,                       -- 'YYYY-MM-DD' for daily notes (unique)
    embedding   TEXT,                       -- JSON float[] for semantic search
    enrich_optout INTEGER NOT NULL DEFAULT 0, -- 1 = agent must not auto-enrich (bulk import)
    visibility  TEXT NOT NULL DEFAULT 'private' CHECK(visibility IN ('private','public')),
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    -- Per-note extraction switches: stop the agent looking for tasks/reminders
    -- in notes that structurally aren't about those things.
    no_actions   INTEGER NOT NULL DEFAULT 0,
    no_reminders INTEGER NOT NULL DEFAULT 0,
    -- "Metadata only" mode: agent describes/tags but leaves the body verbatim.
    meta_only    INTEGER NOT NULL DEFAULT 0,
    -- Manual colour override: an index (0-7) into whichever theme's palette is
    -- active, NOT a literal hex colour — so a note's chosen colour stays the
    -- same relative swatch (and adapts in hue/lightness) when the theme
    -- changes, rather than clashing with a different palette. NULL = automatic
    -- (hash-derived from the note's id, the long-standing default).
    color_idx    INTEGER
);
-- Note-list sort index: the main list, archive/hidden views, and wiki
-- autocomplete all ORDER BY (pinned DESC, updated_at DESC). Without it SQLite
-- scans the whole table into a temp B-tree on every load; with it the sort is
-- an index seek. Simple (not partial) so it serves every view.
CREATE INDEX IF NOT EXISTS idx_notes_list ON notes(pinned DESC, updated_at DESC);

-- ── AI enrichment (one row per approved enrichment cycle) ────────────────────
CREATE TABLE IF NOT EXISTS enrichments (
    id              TEXT PRIMARY KEY,
    note_id         TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    title           TEXT,
    summary         TEXT,
    polished        TEXT,
    confidence      REAL,
    priority        TEXT CHECK(priority IN ('high','medium','low')),
    schema_version  TEXT DEFAULT '2.1',
    enriched_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_enrichments_note ON enrichments(note_id);

-- ── Processing state machine ─────────────────────────────────────────────────
-- status: raw → pending → approved | rejected
-- raw        = never seen by daemon
-- pending    = daemon is working / has suggested, awaiting user approval
-- approved   = user approved, enrichment is final
-- force      = user wants reprocess
-- dirty      = edited & autosaved, but not yet committed (editor open);
--              durable but NOT queued for enrichment until the user closes
--              the note or taps Run AI now. Prevents mid-session enrichment.
CREATE TABLE IF NOT EXISTS note_state (
    note_id     TEXT PRIMARY KEY REFERENCES notes(id) ON DELETE CASCADE,
    status      TEXT NOT NULL DEFAULT 'raw'
                    CHECK(status IN ('raw','dirty','pending','approved','force')),
    body_hash   TEXT,                      -- sha256[:16] of note body at last processing
    seen_at     TEXT,                      -- daemon's last confirmed write time
    failures    INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

-- ── Tags ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tags (
    note_id     TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    tag         TEXT NOT NULL,
    PRIMARY KEY (note_id, tag)
);
CREATE INDEX IF NOT EXISTS idx_tags_tag ON tags(tag);

-- ── Backlinks (explicit [[wiki]] links between notes) ────────────────────────
CREATE TABLE IF NOT EXISTS backlinks (
    src_id      TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    dst_id      TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    PRIMARY KEY (src_id, dst_id)
);
CREATE INDEX IF NOT EXISTS idx_backlinks_dst ON backlinks(dst_id);

-- ── Templates (reusable note scaffolds) ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS templates (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    body        TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

-- ── Action items ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS action_items (
    id          TEXT PRIMARY KEY,
    note_id     TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    text        TEXT NOT NULL,
    done        INTEGER NOT NULL DEFAULT 0,
    done_at     TEXT,
    -- dismissed = "this isn't a task" (rejected), distinct from done = "I did it".
    -- Kept (not deleted) so re-enrichment can avoid re-suggesting the same text.
    dismissed   INTEGER NOT NULL DEFAULT 0,
    category    TEXT NOT NULL DEFAULT '',      -- 'work' | 'personal' | '' (uncategorized)
    blocked_by  TEXT NOT NULL DEFAULT '',      -- who/what this task waits on, if any
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_actions_note ON action_items(note_id);
CREATE INDEX IF NOT EXISTS idx_actions_done ON action_items(done);

-- ── Reminders / calendar events ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS reminders (
    id              TEXT PRIMARY KEY,
    note_id         TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    title           TEXT NOT NULL,
    datetime_hint   TEXT,
    datetime_resolved TEXT,
    description     TEXT,
    calendar_event_id TEXT,
    source          TEXT NOT NULL DEFAULT 'agent',  -- 'agent' | 'user'
    dismissed       INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

-- ── Note relationships (computed by daemon) ───────────────────────────────────
CREATE TABLE IF NOT EXISTS relationships (
    note_id_a   TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    note_id_b   TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    score       REAL NOT NULL,
    computed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    PRIMARY KEY (note_id_a, note_id_b)
);

-- ── Corrections (transcription fixes suggested by AI) ────────────────────────
CREATE TABLE IF NOT EXISTS corrections (
    id          TEXT PRIMARY KEY,
    note_id     TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    from_text   TEXT NOT NULL,
    to_text     TEXT NOT NULL,
    confidence  REAL
);

-- ── Revisions: every meaningful body version, kept for history ───────────────
-- kind: 'pre_adopt' = the text that existed before an approved enrichment was
--        adopted as the new body; 'edit' = the approved text as it was before
--        the user started editing it again. One row per lifecycle transition.
CREATE TABLE IF NOT EXISTS revisions (
    id          TEXT PRIMARY KEY,
    note_id     TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    body        TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'pre_adopt' CHECK(kind IN ('pre_adopt','edit')),
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_revisions_note ON revisions(note_id);

-- ── App settings / daemon config mirroring ───────────────────────────────────
CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

-- ── Ask-your-notes: retrieval-augmented Q&A over the local corpus ────────────
-- hg_ui does retrieval (same ranking as /api/search) and writes the question +
-- trimmed excerpts here as 'pending'. The daemon polls this table directly
-- (same pattern as its dirty-note poll for enrichment — no HTTP round-trip for
-- the read), generates an answer with the local model, and POSTs the result to
-- /api/ask/answer, which updates this row and pushes it to the UI over SSE.
CREATE TABLE IF NOT EXISTS ask_requests (
    id              TEXT PRIMARY KEY,
    question        TEXT NOT NULL,
    context_json    TEXT NOT NULL DEFAULT '[]',  -- [{id,title,text}, ...] excerpts given to the model
    status          TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','done','error')),
    answer          TEXT,
    cited_note_ids  TEXT NOT NULL DEFAULT '[]',  -- JSON array of note ids the answer actually cites
    mode            TEXT NOT NULL DEFAULT 'notes', -- notes | chat | write
    error           TEXT,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    answered_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_ask_status ON ask_requests(status);

-- Passage-level retrieval index, written by the agent (hg_agent: chunks.py /
-- qs_client.index_note_chunks). One row per ~500-char chunk, tagged with the
-- embedding model so vectors from different models are never compared.
-- body_hash identifies what the chunks were built from; unchanged -> no re-embed.
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

-- ── Triggers: auto-update updated_at ─────────────────────────────────────────
CREATE TRIGGER IF NOT EXISTS notes_updated
    AFTER UPDATE ON notes
    BEGIN UPDATE notes SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
          WHERE id = NEW.id; END;

CREATE TRIGGER IF NOT EXISTS state_updated
    AFTER UPDATE ON note_state
    BEGIN UPDATE note_state SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
          WHERE note_id = NEW.note_id; END;


-- Durable job queue, owned by the agent (hg_agent: jobs.py). The app only reads
-- it for the status strip and re-queues dead jobs when the user taps Retry.
CREATE TABLE IF NOT EXISTS agent_jobs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kind          TEXT NOT NULL,
    key           TEXT NOT NULL,
    payload       TEXT NOT NULL DEFAULT '{}',
    status        TEXT NOT NULL DEFAULT 'queued'
                      CHECK(status IN ('queued','running','done','dead')),
    priority      INTEGER NOT NULL DEFAULT 1,
    attempts      INTEGER NOT NULL DEFAULT 0,
    max_attempts  INTEGER NOT NULL DEFAULT 3,
    run_after     TEXT NOT NULL,
    lease_until   TEXT,
    last_error    TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_due ON agent_jobs(status, run_after, priority);
CREATE INDEX IF NOT EXISTS idx_jobs_key ON agent_jobs(kind, key, status);

-- ── Attachments: photos, voice recordings, sketches ─────────────────────────
-- Files live on disk (<db dir>/attachments/<filename>); this table holds their
-- metadata plus what the on-device agent read out of them. `transcript` is the
-- spoken words (audio) or the text recognised in the picture (image/sketch);
-- it feeds search, Ask and enrichment but is NEVER written into the note body
-- unless the user taps "Insert into note" (or a voice note has no other text).
--   transcript_status: none    = nobody asked for it (e.g. bulk import)
--                      pending = waiting for the agent
--                      running = the agent is on it
--                      done    = finished (transcript may be empty: nothing to read)
--                      skipped = the agent can't do this (no engine configured)
--                      failed  = tried and gave up (see transcript_error)
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

-- Trash: a deleted note is kept here as a snapshot (its row, tags, tasks,
-- reminders, enrichment, attachments) until restored or purged (30 days).
CREATE TABLE IF NOT EXISTS trash (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL DEFAULT '',
    preview     TEXT NOT NULL DEFAULT '',
    n_attach    INTEGER NOT NULL DEFAULT 0,
    snapshot    TEXT NOT NULL,
    files       TEXT NOT NULL DEFAULT '[]',
    deleted_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

-- Possible duplicate notes, found by the agent (insights.py); merged or dismissed from the app.
CREATE TABLE IF NOT EXISTS dup_suggestions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    note_a     TEXT NOT NULL,
    note_b     TEXT NOT NULL,
    score      REAL NOT NULL,
    status     TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','merged','dismissed')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE(note_a, note_b)
);

-- Task follow-through: stale tasks the agent proposes a move for (followups.py in the agent).
-- Keyed by note + normalised task text because task rows are re-created on every re-enrichment.
CREATE TABLE IF NOT EXISTS followups (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    note_id     TEXT NOT NULL,
    note_title  TEXT NOT NULL DEFAULT '',
    text        TEXT NOT NULL,
    key         TEXT NOT NULL,
    move        TEXT NOT NULL CHECK(move IN ('reschedule','today','first_step','drop')),
    move_date   TEXT,
    step        TEXT NOT NULL DEFAULT '',
    reason      TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending','accepted','done','dropped','dismissed','expired')),
    reminder_id TEXT,
    cal_synced  INTEGER NOT NULL DEFAULT 0,
    cal_tries   INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    resolved_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_followups_status ON followups(status, key);

-- ── Saved captures: things pressed Save on in Medha's brief ─────────────────
-- An INBOX, not notes. The agent copies each save here verbatim and stops;
-- what it becomes -- a note, a checklist, a reminder, a task -- is decided by
-- the person in the app, and only that decision creates anything in `notes`.
--
-- Why the agent does not create the note itself: notes.user_title is "a title
-- the person typed ... the agent never writes it", and a capture's title is
-- the sender's name off the shade. Writing it would break that promise;
-- leaving it out would put a note in the feed titled by its first 60
-- characters. Holding the capture here until someone decides avoids both.
--
-- `fp` is Medha's fingerprint and the primary key, so INSERT OR IGNORE makes
-- a re-delivered save land on the row it already made. Medha deletes its copy
-- only after this insert commits, so a crash in between re-delivers and this
-- key absorbs it.
--
-- No CHECK on `decision`. note_state's CHECK had to be rebuilt in place to
-- admit one new value -- an exclusive-lock DROP/RENAME at startup, while the
-- agent may be writing. The allowed values are enforced in server/saved.py.
CREATE TABLE IF NOT EXISTS saved_captures (
    fp          TEXT PRIMARY KEY,
    origin      TEXT NOT NULL DEFAULT '',   -- the app it came from, e.g. "WhatsApp"
    title       TEXT NOT NULL DEFAULT '',   -- verbatim
    body        TEXT NOT NULL DEFAULT '',   -- verbatim
    posted_at   INTEGER,                    -- ms: when the app posted it
    saved_at    INTEGER,                    -- ms: when Save was pressed
    truncated   INTEGER NOT NULL DEFAULT 0, -- 1 = Medha cut the text at its ceiling
    decision    TEXT,                       -- NULL = waiting; see server/saved.py
    note_id     TEXT REFERENCES notes(id) ON DELETE SET NULL,
    decided_at  TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_saved_captures_waiting ON saved_captures(decision, saved_at);

-- Bills from messages (the agent's sms.py writes these; the app only reads them). Message bodies are never stored.
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

-- Notes with nothing indexable (only highlight marks, or the model kept failing): counted as indexed so the
-- "getting your notes ready" progress can finish. Written by the agent; an edit makes it retry.
CREATE TABLE IF NOT EXISTS index_skips (
    note_id    TEXT PRIMARY KEY REFERENCES notes(id) ON DELETE CASCADE,
    model      TEXT NOT NULL,
    reason     TEXT NOT NULL DEFAULT 'empty',
    body_hash  TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
