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
    meta_only    INTEGER NOT NULL DEFAULT 0
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
    error           TEXT,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    answered_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_ask_status ON ask_requests(status);

-- ── Triggers: auto-update updated_at ─────────────────────────────────────────
CREATE TRIGGER IF NOT EXISTS notes_updated
    AFTER UPDATE ON notes
    BEGIN UPDATE notes SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
          WHERE id = NEW.id; END;

CREATE TRIGGER IF NOT EXISTS state_updated
    AFTER UPDATE ON note_state
    BEGIN UPDATE note_state SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
          WHERE note_id = NEW.note_id; END;
