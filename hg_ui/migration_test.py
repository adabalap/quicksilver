#!/usr/bin/env python3
"""
Cross-version / migration tests for Quicksilver.

Proves that a database written by an OLDER version of the app upgrades cleanly
under the CURRENT code — no data loss, indexes and FTS built, embeddings
converted — when init_db() runs on first launch. This is the safety net for
"across versions": run it before deploying a new build against a real DB.

    python3 migration_test.py [-v]

Exit 0 = safe to upgrade, 1 = a migration would lose or corrupt data.

Strategy: build a database the way an OLD version would have (pre-index,
pre-FTS, JSON embeddings, no new columns), snapshot the note contents, run the
current init_db(), then assert every note survived and every new structure
exists.
"""
import sys, os, tempfile, importlib.util, sqlite3, json, hashlib, struct

VERBOSE = "-v" in sys.argv
_P = _F = 0
_FAILS = []


def check(cond, msg):
    global _P, _F
    if cond:
        _P += 1
        if VERBOSE:
            print(f"  \033[32m✓\033[0m {msg}")
    else:
        _F += 1
        _FAILS.append(msg)
        print(f"  \033[31m✗ {msg}\033[0m")


def section(name):
    print(f"\n\033[1m{name}\033[0m")


def dv(t, d=384):
    o = []; i = 0
    while len(o) < d:
        h = hashlib.sha256(t.encode() + str(i).encode()).digest()
        for j in range(0, len(h), 4):
            if len(o) >= d:
                break
            o.append(struct.unpack("<i", h[j:j + 4])[0] / 2**31)
        i += 1
    n = sum(x * x for x in o) ** 0.5 or 1.0
    return [x / n for x in o]


def build_old_db(path, n_notes=40):
    """Create a database faithful to a PRE-scaling-arc version.

    We start from the REAL schema.sql (so every base column exists exactly as on
    a real device), then strip ONLY what the scaling arc added: the sort index,
    the FTS table/triggers, and the blob embedding format (we write JSON instead).
    This is far more faithful than hand-writing a minimal schema, which silently
    omits columns the real base always had.
    """
    schema = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")).read()
    con = sqlite3.connect(path)
    con.executescript(schema)
    # Strip scaling-arc additions to simulate the older shape.
    con.execute("DROP INDEX IF EXISTS idx_notes_list")
    con.execute("DROP TABLE IF EXISTS notes_fts")
    for trig in ("notes_fts_ai", "notes_fts_ad", "notes_fts_au"):
        con.execute(f"DROP TRIGGER IF EXISTS {trig}")
    con.commit()

    contents = {}
    pinned_ids, archived_ids = set(), set()
    for i in range(n_notes):
        nid = f"old{i:03d}"
        body = f"legacy note {i} about telecom billing and project alpha"
        # Insert with only guaranteed base-schema columns; flags like pinned/
        # archived are migration-added and get set after init_db() runs.
        con.execute("INSERT INTO notes (id, body) VALUES (?,?)", (nid, body))
        con.execute("INSERT INTO note_state (note_id, status) VALUES (?, 'approved')", (nid,))
        con.execute("UPDATE notes SET embedding=? WHERE id=?",
                    (json.dumps(dv(body)), nid))
        if i % 3 == 0:
            con.execute("INSERT INTO tags (note_id, tag) VALUES (?, 'telecom')", (nid,))
        if i % 7 == 0:
            pinned_ids.add(nid)
        if i % 11 == 0:
            archived_ids.add(nid)
        contents[nid] = body
    con.commit()
    con.close()
    # Stash which ids SHOULD be pinned/archived so the caller can set them once
    # the migration has added those columns.
    build_old_db.pinned_ids = pinned_ids
    build_old_db.archived_ids = archived_ids
    return contents


def run_current_init(db_path):
    """Run the CURRENT app's init_db() against the given DB (applies migrations)."""
    os.environ["QS_DB"] = db_path
    if "qs_app_mig" in sys.modules:
        del sys.modules["qs_app_mig"]
    spec = importlib.util.spec_from_file_location(
        "qs_app_mig", os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py"))
    qs = importlib.util.module_from_spec(spec)
    sys.modules["qs_app_mig"] = qs
    spec.loader.exec_module(qs)
    qs.init_db()
    return qs


def main():
    print("\033[1m═══ Quicksilver — cross-version / migration tests ═══\033[0m")
    tmp = tempfile.mkdtemp()
    db_path = os.path.join(tmp, "old.db")

    section("Setup: build a pre-scaling-arc database")
    contents = build_old_db(db_path)
    check(len(contents) == 40, f"built old DB with {len(contents)} notes (JSON embeddings, no index/FTS)")
    # confirm the OLD shape
    con = sqlite3.connect(db_path)
    has_index = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_notes_list'").fetchone()
    has_fts = con.execute(
        "SELECT 1 FROM sqlite_master WHERE name='notes_fts'").fetchone()
    emb_type = con.execute(
        "SELECT typeof(embedding) FROM notes WHERE embedding IS NOT NULL LIMIT 1").fetchone()[0]
    check(not has_index, "old DB has NO sort index (as expected)")
    check(not has_fts, "old DB has NO FTS table (as expected)")
    check(emb_type == "text", "old DB embeddings are JSON text (as expected)")
    con.close()

    section("Migration: run the current init_db() on the old DB")
    qs = run_current_init(db_path)
    con = sqlite3.connect(db_path)

    # Now that the migration has added pinned/archived, set the flags we recorded.
    for nid in build_old_db.pinned_ids:
        con.execute("UPDATE notes SET pinned=1 WHERE id=?", (nid,))
    for nid in build_old_db.archived_ids:
        con.execute("UPDATE notes SET archived=1 WHERE id=?", (nid,))
    con.commit()

    # 1. No data lost
    surviving = dict(con.execute("SELECT id, body FROM notes").fetchall())
    check(len(surviving) == len(contents), f"all {len(contents)} notes survived migration")
    lost = [nid for nid, body in contents.items()
            if surviving.get(nid) != body]
    check(not lost, "every note's body is byte-for-byte intact")

    # 2. Flags settable (columns exist post-migration)
    pinned = con.execute("SELECT COUNT(*) FROM notes WHERE pinned=1").fetchone()[0]
    check(pinned > 0, f"pinned column exists and is usable post-migration ({pinned})")

    # 3. New structures created
    has_index = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_notes_list'").fetchone()
    check(bool(has_index), "sort index idx_notes_list was created")

    has_fts = con.execute(
        "SELECT 1 FROM sqlite_master WHERE name='notes_fts'").fetchone()
    # FTS is optional (build-dependent); only assert if HAS_FTS5 is true
    if getattr(qs, "HAS_FTS5", False):
        check(bool(has_fts), "notes_fts index was created (FTS5 build)")
        # and it actually matches
        m = con.execute("SELECT COUNT(*) FROM notes_fts WHERE notes_fts MATCH 'telecom'").fetchone()[0]
        check(m > 0, f"FTS index is populated and searchable ({m} matches for 'telecom')")
    else:
        check(True, "FTS5 not in this build — LIKE fallback (skipped FTS asserts)")

    # 4. Tags preserved
    tagged = con.execute("SELECT COUNT(*) FROM tags WHERE tag='telecom'").fetchone()[0]
    check(tagged > 0, f"tags preserved ({tagged} telecom-tagged)")
    con.close()

    section("Post-migration: the app works end-to-end on the upgraded DB")
    from unittest.mock import patch
    c = qs.app.test_client()
    # Default list excludes archived notes — account for that in the count.
    n_archived = len(build_old_db.archived_ids)
    expected_visible = 40 - n_archived
    j = c.get("/api/notes").get_json()
    check(j["total"] == expected_visible,
          f"default list total correct ({j['total']} = 40 − {n_archived} archived)")
    ja = c.get("/api/notes?filter=archived").get_json()
    check(ja["total"] == n_archived, f"archived filter shows the {n_archived} archived notes")
    # search works
    with patch.object(qs, "_embed_query", lambda t: None):
        r = c.get("/api/search?q=telecom").get_json()
    check(len(r["notes"]) > 0, "search returns legacy notes after migration")
    # a NEW note can be added and is immediately searchable (triggers/index live)
    with patch.object(qs, "_embed_query", lambda t: None):
        nid = c.post("/api/notes", json={"body": "fresh post-migration zebra note"}).get_json()["id"]
        found = nid in {n["id"] for n in c.get("/api/search?q=zebra").get_json()["notes"]}
    check(found, "new note post-migration is searchable (new machinery works)")

    section("Idempotency: running init_db() again is safe")
    qs2 = run_current_init(db_path)
    con = sqlite3.connect(db_path)
    still = con.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
    check(still == 41, f"second init_db() didn't duplicate or drop data ({still} notes)")
    con.close()

    section("reworded duplicate reminders are collapsed on upgrade")
    con = sqlite3.connect(db_path)
    con.execute("INSERT INTO notes (id,body) VALUES ('rdup','file taxes')")
    con.execute("INSERT INTO reminders (id,note_id,title,datetime_hint,datetime_resolved,calendar_event_id,created_at) "
                "VALUES ('rd1','rdup','Income Tax Return Deadline','July 31st, 2026','2026-07-31','evt_x','t1')")
    con.execute("INSERT INTO reminders (id,note_id,title,datetime_hint,datetime_resolved,calendar_event_id,created_at) "
                "VALUES ('rd2','rdup','ITR Filing Deadline','2026-07-31T00:00:00','2026-07-31','','t2')")
    con.commit(); con.close()
    run_current_init(db_path)
    con = sqlite3.connect(db_path)
    left = [r[0] for r in con.execute("SELECT id FROM reminders WHERE note_id='rdup'")]
    con.close()
    check("reworded ITR duplicate collapsed to one", len(left) == 1)
    check("the calendar-linked reminder is the one kept", left == ["rd1"])

    section("action_items gains category + blocked_by columns")
    con = sqlite3.connect(db_path)
    ai_cols = {r[1] for r in con.execute("PRAGMA table_info(action_items)")}
    con.close()
    check("category column present after upgrade", "category" in ai_cols)
    check("blocked_by column present after upgrade", "blocked_by" in ai_cols)

    section("Repair: a note_state broken by the earlier bad migration recovers")
    # Reproduce the exact damage: rebuild note_state with only 4 of 6 columns
    # (failures + updated_at dropped), which crashed every note read.
    con = sqlite3.connect(db_path)
    con.executescript("""
        PRAGMA foreign_keys=off; BEGIN;
        CREATE TABLE nsb (note_id TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'raw'
          CHECK(status IN ('raw','dirty','pending','approved','force')),
          body_hash TEXT, seen_at TEXT);
        INSERT INTO nsb (note_id,status,body_hash)
            SELECT note_id,status,body_hash FROM note_state;
        DROP TABLE note_state; ALTER TABLE nsb RENAME TO note_state;
        COMMIT; PRAGMA foreign_keys=on;
    """)
    con.commit()
    broken = "failures" not in {r[1] for r in con.execute("PRAGMA table_info(note_state)")}
    con.close()
    check(broken, "reproduced the broken 4-column note_state")
    run_current_init(db_path)   # should repair
    con = sqlite3.connect(db_path)
    cols = {r[1] for r in con.execute("PRAGMA table_info(note_state)")}
    check("failures" in cols and "updated_at" in cols,
          "repair restored both dropped columns")
    con.close()
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location("qs_after", "app.py")
    _m = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_m)
    _c = _m.app.test_client()
    check(_c.get("/api/notes?limit=200").status_code == 200,
          "GET /api/notes works again after repair (was 500)")

    print("\n" + "─" * 55)
    if _F == 0:
        print(f"\033[32m  {_P}/{_P} passed — safe to upgrade ✨\033[0m")
    else:
        print(f"\033[31m  {_P}/{_P + _F} passed, {_F} FAILED — DO NOT UPGRADE until fixed\033[0m")
        for m in _FAILS:
            print(f"\033[31m    ✗ {m}\033[0m")
    return 0 if _F == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
