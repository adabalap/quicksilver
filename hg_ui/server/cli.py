"""Command-line data operations (backup, restore, clear, dedupe, import-keep) and process title."""
import sys
import zipfile
from pathlib import Path

import mediastore as _ms
import interchange as _ix

from .core import DB_PATH, _nanoid


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
        dest = do_backup(argv[2] if len(argv) > 2 else None)
        # Photos / recordings aren't in the .db — copy them next to it (only what
        # changed, so a second backup is quick).
        adir = _ms.att_dir(db_path)
        files = [p for p in adir.iterdir() if p.is_file() and not p.name.startswith(".")]
        if files:
            tdir = Path(dest + ".attachments"); tdir.mkdir(exist_ok=True)
            import shutil
            n = 0
            for p in files:
                t = tdir / p.name
                if not t.exists() or t.stat().st_size != p.stat().st_size:
                    shutil.copy2(p, t); n += 1
            print(f"✓ attachments: {len(files)} file(s) ({n} copied) → {tdir}")

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
        adir_b = Path(srcf + ".attachments")
        if adir_b.is_dir():
            import shutil
            adir = _ms.att_dir(db_path); n = 0
            for p in adir_b.iterdir():
                if p.is_file() and not (adir / p.name).exists():
                    shutil.copy2(p, adir / p.name); n += 1
            print(f"✓ restored {n} attachment file(s)")
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
        # Import a Google Keep Takeout (takeout.google.com → Keep), a Quicksilver
        # export, or a folder/zip of Markdown files. The same engine backs the
        # in-app importer (Settings → Import).
        #
        #   hg_ui import-keep <export.zip> [--dry-run] [--skip-archived]
        #                     [--include-trashed] [--tag-from-labels]
        #                     [--enrich] [--transcribe]
        import io as _io
        if len(argv) < 3:
            sys.exit("usage: import-keep <takeout.zip> [--dry-run] [--skip-archived] "
                     "[--include-trashed] [--tag-from-labels] [--enrich] [--transcribe]\n"
                     "  Photos and voice recordings in a Keep export are imported too.\n"
                     "  Notes are NOT auto-enriched by default (--enrich queues them all);\n"
                     "  --transcribe asks the agent to read the photos / transcribe the voice notes.")
        src = Path(argv[2])
        if not src.exists():
            sys.exit(f"not found: {src}")
        opts = {"skip_archived": "--skip-archived" in argv, "include_trashed": "--include-trashed" in argv,
                "tag_labels": "--tag-from-labels" in argv, "enrich": "--enrich" in argv,
                "transcribe": "--transcribe" in argv}
        dry = "--dry-run" in argv
        if src.is_dir():                       # a folder: zip it up in memory
            buf = _io.BytesIO()
            with zipfile.ZipFile(buf, "w") as zf:
                for p in sorted(src.rglob("*")):
                    if p.is_file():
                        zf.write(p, p.relative_to(src).as_posix())
            zsrc = buf
        else:
            zsrc = str(src)
        try:
            z = _ix.open_zip(zsrc)
        except Exception as e:
            sys.exit(f"can't read {src}: {e}")
        con = s3.connect(db_path); con.row_factory = s3.Row
        con.execute("PRAGMA foreign_keys=ON")
        with z:
            pl = _ix.plan(z, opts)
            if pl["format"] == "unknown":
                sys.exit("nothing importable found. Export via takeout.google.com → select "
                         "'Keep' → download the .zip, then point this at it.")
            st = pl["stats"]
            rows = _ix.dedupe(con, pl["rows"], st)
            print(f"\nImport — {src}  ({pl['format']})")
            print(f"  found          {st.get('found', 0)}")
            print(f"  to import      {len(rows)}")
            print(f"  duplicates     {st.get('skipped_dupe', 0)} (already in Quicksilver)")
            for k, lbl in (("skipped_empty", "empty"), ("skipped_trashed", "trashed"),
                           ("skipped_archived", "archived (skipped)"), ("unparseable", "unreadable"),
                           ("attachments_unsupported", "attachments not importable")):
                if st.get(k):
                    print(f"  {lbl:<14} {st[k]}")
            print(f"  ├ photos       {st.get('photos', 0)}")
            print(f"  ├ voice notes  {st.get('voice', 0)}")
            print(f"  └ checklists   {st.get('checklists', 0)}")
            if dry:
                print("\n(dry run — nothing written)")
                for r in rows[:5]:
                    print(f"    · {(r['body'] or '(attachment only)')[:60].replace(chr(10), ' ')}…")
                if len(rows) > 5: print(f"    … and {len(rows)-5} more")
                con.close(); return
            if not rows:
                print("\nnothing to import."); con.close(); return
            do_backup(str(bdir / f"pre-import-{ts}.db"))
            res = _ix.commit(con, db_path, z, rows, pl["format"], opts, _nanoid)
        con.close()
        print(f"\n✓ imported {res['imported']} note(s), {res['attachments']} attachment(s)"
              + (f" ({res['attachments_failed']} couldn't be read)" if res["attachments_failed"] else ""))
        if opts["enrich"]:
            print("  Queued for AI enrichment — expect hours for a big import; the thermal guard paces it.")
        else:
            print("  Not queued for AI enrichment (that's one local Gemma call per note). They're "
                  "searchable now; enrich the ones you care about via ⋯ → Re-run, or use --enrich.")
        if res["attachments"] and not opts["transcribe"]:
            print("  Photos / voice notes aren't being read yet — add --transcribe, or tap "
                  "'Read text' / 'Transcribe' on any of them.")

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
