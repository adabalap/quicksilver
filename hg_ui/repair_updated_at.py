#!/usr/bin/env python3
"""Repair notes.updated_at after the re-index incident (build 2026-10-25-0134).

What happened: the agent's passage re-index rewrote notes.embedding for every
note, and the `notes_updated` trigger treated that as an edit — every note was
re-dated to "when it was indexed", scrambling the sort order. (The cause is fixed
in later builds; this repairs the data.)

How it decides: a note's real last-change time is at least the latest of its
created_at, its newest enrichment, and its newest user-edit revision. If a note's
updated_at lies AFTER that and coincides (±10 s) with when its passages were
indexed, the bump came from indexing, not from you — it is reset to that estimate.
Notes you really edited are left alone (their updated_at doesn't coincide with
indexing, or their edit revision is later).

    python3 repair_updated_at.py                     # dry run: shows what would change
    python3 repair_updated_at.py --apply             # backs up the DB, then repairs
    python3 repair_updated_at.py --db PATH [--apply]
"""
import argparse, os, shutil, sqlite3, sys, time
from datetime import datetime

TRIGGER = ("CREATE TRIGGER notes_updated AFTER UPDATE ON notes "
           "WHEN NEW.embedding IS OLD.embedding AND NEW.embed_model IS OLD.embed_model "
           "BEGIN UPDATE notes SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') "
           "WHERE id = NEW.id; END;")


def _t(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def plan(db):
    out = []
    rows = db.execute("""
        SELECT n.id, n.created_at, n.updated_at,
               (SELECT MAX(enriched_at) FROM enrichments e WHERE e.note_id=n.id) enr,
               (SELECT MAX(created_at) FROM revisions r WHERE r.note_id=n.id AND r.kind='edit') edt,
               (SELECT MIN(created_at) FROM note_chunks c WHERE c.note_id=n.id) idx,
               (SELECT MAX(created_at) FROM note_chunks c WHERE c.note_id=n.id) idx2
        FROM notes n""").fetchall()
    for r in rows:
        cands = [x for x in (r["created_at"], r["enr"], r["edt"]) if x]
        if not cands or not r["idx"]:
            continue
        est = max(cands)
        cur, e, a, b = _t(r["updated_at"] or ""), _t(est), _t(r["idx"]), _t(r["idx2"])
        if None in (cur, e, a, b):
            continue
        coincides = (a - 10) <= cur <= (b + 10)
        if cur > e + 1 and coincides:
            out.append((r["id"], r["updated_at"], est))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.expanduser("~/.quicksilver/notes.db"))
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    if not os.path.exists(a.db):
        sys.exit(f"no database at {a.db}")
    db = sqlite3.connect(a.db, timeout=30); db.row_factory = sqlite3.Row
    todo = plan(db)
    total = db.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
    print(f"{len(todo)} of {total} notes have an updated_at caused by re-indexing.")
    for nid, cur, est in todo[:8]:
        print(f"  {nid}: {cur}  →  {est}")
    if len(todo) > 8:
        print(f"  … and {len(todo) - 8} more")
    if not a.apply:
        print("\nDry run. Re-run with --apply to back up and repair.")
        return
    if not todo:
        return
    bak = f"{a.db}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
    src = sqlite3.connect(a.db); dst = sqlite3.connect(bak); src.backup(dst); dst.close(); src.close()
    print(f"backup → {bak}")
    try:
        db.execute("BEGIN IMMEDIATE")
        db.execute("DROP TRIGGER IF EXISTS notes_updated")        # or it re-dates the row again
        db.executemany("UPDATE notes SET updated_at=? WHERE id=?", [(est, nid) for nid, _, est in todo])
        db.execute(TRIGGER)
        db.commit()
    except Exception:
        db.rollback(); raise
    print(f"repaired {len(todo)} notes. Restart the app/agent (or just pull to refresh).")


if __name__ == "__main__":
    main()
