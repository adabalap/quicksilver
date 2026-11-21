"""Full-text search index (FTS5) setup and tokenizer migration."""

from .core import log


# Module flag: does this SQLite build have FTS5? Set by _init_fts. Search
# consults it to pick the FTS path vs the LIKE fallback, so a build WITHOUT FTS5
# (some Termux/Android SQLite builds) still searches, just without ranking.
HAS_FTS5 = False

# FTS5's default unicode61 tokenizer treats combining marks (Unicode Mn/Mc) as
# separators, which shatters Indic-script words at every vowel sign: Telugu
# "గమనికల" (of-notes) is indexed as "గమన" + "కల", so a search for "కల" (dream)
# wrongly matches it. Adding Mn/Mc to the token categories keeps words whole.
# It's a strict superset of the default (L* N* Co), so English is unaffected.
FTS_TOKENIZE = "unicode61 categories 'L* N* Mn Mc Co'"

def _migrate_fts_tokenizer(db) -> bool:
    """Create notes_fts with the Indic-safe tokenizer, upgrading an existing
    index built with the old default. Returns True when the index was (re)created
    and therefore needs a rebuild. Falls back to the default tokenizer on SQLite
    builds too old to know the `categories` option, so FTS is never lost."""
    row = db.execute("SELECT sql FROM sqlite_master WHERE name='notes_fts'").fetchone()
    existing = (row[0] if row and not hasattr(row, "keys") else (row["sql"] if row else None))
    if existing and "categories" in existing:
        return False                      # already migrated
    if existing:
        # Old-tokenizer index: drop it and its sync triggers (recreated below).
        for trig in ("notes_fts_ai", "notes_fts_ad", "notes_fts_au"):
            db.execute(f"DROP TRIGGER IF EXISTS {trig}")
        db.execute("DROP TABLE notes_fts")
    try:
        db.execute("CREATE VIRTUAL TABLE notes_fts USING fts5("
                   "body, content='notes', content_rowid='rowid', "
                   f'tokenize="{FTS_TOKENIZE}")')
    except Exception as e:
        log.info(f"FTS5 without categories option ({e}); using default tokenizer.")
        db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5("
                   "body, content='notes', content_rowid='rowid')")
    return True

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
        migrated = _migrate_fts_tokenizer(db)
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
    needs_build = migrated
    probe = db.execute("SELECT body FROM notes WHERE body != '' LIMIT 1").fetchone()
    if probe and not needs_build:
        body = probe[0] if not hasattr(probe, "keys") else probe["body"]
        # Split on whitespace and trim punctuation — NOT on \W, which treats
        # Telugu/Indic vowel signs (combining marks) as separators and would
        # hand the index a mangled fragment to look for.
        term = ((body or "").split() or [""])[0].strip(".,;:!?()[]{}\"'“”‘’-–—/\\")
        if term:
            try:
                hit = db.execute("SELECT 1 FROM notes_fts WHERE notes_fts MATCH ? LIMIT 1",
                                 ('"' + term.replace('"', '""') + '"',)).fetchone()
                needs_build = hit is None
            except Exception:
                needs_build = True
    if needs_build:
        db.execute("INSERT INTO notes_fts (notes_fts) VALUES ('rebuild')")
        db.commit()
    return
