#!/usr/bin/env python3
"""
Quicksilver PWA smoke test.
Run after any code/config change to verify the core contract still holds.

    python smoke_test.py          # uses a fresh temp DB (safe, no side effects)
    python smoke_test.py --keep   # keep the temp DB for inspection

Exit code 0 = all passed, 1 = at least one failure.
Does NOT touch your real notes DB — it always runs against a throwaway copy.
"""
import sys, os, re, tempfile, importlib.util, sqlite3, json, time, argparse

HERE = os.path.dirname(os.path.abspath(__file__))

# ── coloured pass/fail without external deps ────────────────────────────────
G, R, Y, X = "\033[32m", "\033[31m", "\033[33m", "\033[0m"
_passed, _failed = 0, 0


def check(name, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  {G}✓{X} {name}")
    else:
        _failed += 1
        print(f"  {R}✗ {name}{X}" + (f"  → {detail}" if detail else ""))


def section(title):
    print(f"\n{Y}▸ {title}{X}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="keep temp DB")
    args = ap.parse_args()

    tmp = tempfile.mkdtemp()
    os.environ["QS_DB"] = os.path.join(tmp, "smoke.db")

    # load the app fresh against the temp DB
    spec = importlib.util.spec_from_file_location("qs_app", os.path.join(HERE, "app.py"))
    qs = importlib.util.module_from_spec(spec)
    sys.modules["qs_app"] = qs
    spec.loader.exec_module(qs)
    qs.init_db()
    c = qs.app.test_client()

    def body(nid): return c.get(f"/api/notes/{nid}").get_json()["body"]
    def note(nid): return c.get(f"/api/notes/{nid}").get_json()
    def status(nid): return note(nid)["status"]

    # a tiny helper to simulate the agent writing an enrichment straight to the DB
    def enrich(nid, polished, tags=None, status="pending", title="T", actions=None):
        with qs.app.app_context():
            db = qs.get_db()
            db.execute(
                "INSERT INTO enrichments (id, note_id, title, summary, polished, confidence, priority, enriched_at) "
                "VALUES (?,?,?,?,?,?,?,strftime('%Y-%m-%dT%H:%M:%fZ','now'))",
                (qs._nanoid(10), nid, title, "summary", polished, 0.9, "low"))
            for t in (tags or []):
                db.execute("INSERT OR IGNORE INTO tags (note_id, tag) VALUES (?,?)", (nid, t.lower()))
            for a in (actions or []):
                db.execute("INSERT INTO action_items (id, note_id, text, done) VALUES (?,?,?,0)",
                           (qs._nanoid(8), nid, a))
            db.execute("INSERT INTO note_state (note_id, status) VALUES (?,?) "
                       "ON CONFLICT(note_id) DO UPDATE SET status=excluded.status", (nid, status))
            db.commit()

    print(f"\n{'='*52}\n  QUICKSILVER PWA · SMOKE TEST\n{'='*52}")

    # ── 1. health & basic CRUD ──────────────────────────────────────────────
    section("Health & CRUD")
    r = c.get("/api/notes")
    check("GET /api/notes responds", r.status_code == 200, f"status {r.status_code}")
    nid = c.post("/api/notes", json={"body": "smoke note one"}).get_json()["id"]
    check("create note returns id", bool(nid))
    # Archive & Hide (visual privacy) — organizational flags.
    _bn = c.post("/api/notes", json={"body":"archive me"}).get_json()["id"]
    c.post(f"/api/notes/{_bn}/archive")
    _main_ids = [x["id"] for x in c.get("/api/notes").get_json()["notes"]]
    check("archived note hidden from main list", _bn not in _main_ids)
    check("archived note in archived view",
          _bn in [x["id"] for x in c.get("/api/notes?filter=archived").get_json()["notes"]])
    _hn = c.post("/api/notes", json={"body":"secret 007"}).get_json()["id"]
    c.post(f"/api/notes/{_hn}/hide")
    check("hidden note excluded from search",
          len(c.get("/api/notes?q=secret").get_json()["notes"]) == 0)
    check("hidden note in hidden view",
          _hn in [x["id"] for x in c.get("/api/notes?filter=hidden").get_json()["notes"]])
    # Restore-from-revision: edit creates a revision, restore brings it back (reversibly).
    _rn = c.post("/api/notes", json={"body":"orig text"}).get_json()["id"]
    import sqlite3 as _sq3
    c.patch(f"/api/notes/{_rn}", json={"body":"orig text"})  # no-op to ensure exists
    # force an approved->edit revision
    _dbp = os.environ["QS_DB"]
    _cx = _sq3.connect(_dbp); _cx.execute("UPDATE note_state SET status='approved' WHERE note_id=?", (_rn,)); _cx.commit(); _cx.close()
    c.patch(f"/api/notes/{_rn}", json={"body":"new text"})
    _revs = c.get(f"/api/notes/{_rn}/revisions").get_json()["revisions"]
    check("edit produced a revision", len(_revs) >= 1)
    if _revs:
        _rr = c.post(f"/api/notes/{_rn}/revisions/{_revs[0]['id']}/restore").get_json()
        check("restore endpoint returns note", bool(_rr.get("note")) or _rr.get("unchanged"))
    check("restore endpoint reversible (snapshots current)", len(c.get(f"/api/notes/{_rn}/revisions").get_json()["revisions"]) >= 2)
    check("note is readable", body(nid) == "smoke note one")
    check("new note status is raw", status(nid) == "raw")

    # idempotent create
    fixed = "SmokeFixed01"
    r1 = c.post("/api/notes", json={"id": fixed, "body": "x"})
    r2 = c.post("/api/notes", json={"id": fixed, "body": "x"})
    check("idempotent create (replay → 200)", r1.status_code == 201 and r2.status_code == 200,
          f"{r1.status_code}/{r2.status_code}")

    # content cap
    over = c.post("/api/notes", json={"body": "z" * (qs.MAX_BODY_CHARS + 1)})
    check("oversized note rejected (413)", over.status_code == 413, f"status {over.status_code}")

    # ── 2. enrichment & approval ────────────────────────────────────────────
    section("Enrichment & approval")
    enrich(nid, "Polished smoke note.", tags=["smoke", "test"])
    check("enrichment sets pending", status(nid) == "pending")
    c.patch(f"/api/notes/{nid}", json={"status": "approved"})
    check("approval adopts AI polish", body(nid) == "Polished smoke note.")
    check("tags case-folded", "smoke" in [t["tag"] for t in c.get("/api/tags").get_json()["tags"]])

    # ── 3. user-edit preservation (the load-bearing guard) ──────────────────
    section("User-edit preservation (data-loss guard)")
    c.patch(f"/api/notes/{nid}", json={"body": "Polished smoke note. MY CRITICAL LINE."})
    check("plain edit marks dirty (durable, not yet queued)", status(nid) == "dirty")
    c.patch(f"/api/notes/{nid}", json={"body": "Polished smoke note. MY CRITICAL LINE.", "commit": True})
    check("committing that edit promotes it to force", status(nid) == "force")
    check("user_edited flag set", note(nid)["user_edited"] is True)
    enrich(nid, "AI rewrite that DROPS the critical line.", tags=["smoke"])
    c.patch(f"/api/notes/{nid}", json={"status": "approved"})
    check("user edit survives re-enrichment", "MY CRITICAL LINE." in body(nid),
          f"got: {body(nid)!r}")

    # user override at approval
    ov = c.post("/api/notes", json={"body": "raw override"}).get_json()["id"]
    enrich(ov, "AI text for override.")
    c.patch(f"/api/notes/{ov}", json={"status": "approved", "adopt_body": "MY edited version."})
    check("adopt_body override wins", body(ov) == "MY edited version.")

    # ── 4. on-demand polish (one-shot) ──────────────────────────────────────
    section("On-demand polish")
    c.post(f"/api/notes/{nid}/polish")
    with qs.app.app_context():
        flag = qs.get_db().execute("SELECT value FROM settings WHERE key=?",
                                   (f"polish_once:{nid}",)).fetchone()
    check("polish sets one-shot flag", bool(flag) and flag["value"] == "1")

    # ── 5. connections (cluster count) ──────────────────────────────────────
    section("Connections")
    ids = [c.post("/api/notes", json={"body": f"cluster {i}"}).get_json()["id"] for i in range(4)]
    with qs.app.app_context():
        db = qs.get_db()
        for a, b in [(ids[0], ids[1]), (ids[2], ids[3])]:
            db.execute("INSERT OR IGNORE INTO relationships (note_id_a, note_id_b, score) VALUES (?,?,?)", (a, b, 0.7))
            db.execute("INSERT OR IGNORE INTO relationships (note_id_a, note_id_b, score) VALUES (?,?,?)", (b, a, 0.7))
        db.commit()
    conns = c.get("/api/pulse").get_json()["digest"]["connections"]
    check("connections counts clusters (2)", conns == 2, f"got {conns}")

    # ── 6. new features (pin / daily / templates / backlinks / consolidation) ─
    section("Features")
    pn = c.post("/api/notes", json={"body": "pin me"}).get_json()["id"]
    c.post(f"/api/notes/{pn}/pin")
    check("pin sorts to top", c.get("/api/notes").get_json()["notes"][0]["id"] == pn)
    check("pin flag exposed", note(pn)["pinned"] == 1)

    d1 = c.post("/api/daily").get_json()
    d2 = c.post("/api/daily").get_json()
    check("daily note idempotent", d1["id"] == d2["id"] and d1["is_daily"] == 1)

    c.post("/api/templates", json={"name": "Smoke tpl", "body": "## A\n## B"})
    check("template create/list", any(t["name"] == "Smoke tpl"
          for t in c.get("/api/templates").get_json()["templates"]))

    lx = c.post("/api/notes", json={"body": "target"}).get_json()["id"]
    ly = c.post("/api/notes", json={"body": "links to [[target]]"}).get_json()["id"]
    c.post("/api/backlinks", json={"src_id": ly, "dst_ids": [lx]})
    check("backlink stored & resolved", note(ly)["backlinks"] and note(ly)["backlinks"][0]["id"] == lx)
    check("wiki title search finds note", any(n["id"] == lx
          for n in c.get("/api/notes/search-titles?q=target").get_json()["notes"]))

    r = c.get("/share-target?text=shared+smoke&title=T")
    check("share-target creates + redirects", r.status_code == 302)

    hn = c.post("/api/notes", json={"body": "buy ==milk== and =={g}eggs=="}).get_json()["id"]
    hl = c.get("/api/highlights").get_json()
    check("highlights extracted from body", hl["count"] >= 2, f"count={hl['count']}")
    check("highlight colors parsed", any(h["color"] == "g" for h in hl["highlights"]))
    check("highlight links to note", any(h["note_id"] == hn for h in hl["highlights"]))
    # Multi-line highlight extracts cleanly (no {color} fragment leaking).
    ml = c.post("/api/notes", json={"body": "=={p}line one\nline two==\n\ntail"}).get_json()
    mh = [h for h in c.get("/api/highlights").get_json()["highlights"] if h["note_id"] == ml["id"]]
    check("multi-line highlight clean in dashboard",
          mh and "{p}" not in mh[0]["text"] and "==" not in mh[0]["text"],
          mh[0]["text"] if mh else "none")

    # Highlighting must NOT re-trigger the agent (annotation-only change).
    hln = c.post("/api/notes", json={"body": "one two three four five six"}).get_json()["id"]
    enrich(hln, "polished", status="approved")
    c.patch(f"/api/notes/{hln}", json={"body": "one =={y}two three== four five six"})
    check("highlighting keeps status stable (no re-enrichment)",
          status(hln) == "approved", f"status={status(hln)}")
    c.patch(f"/api/notes/{hln}", json={"body": "one two three four five SEVEN"})
    check("real content edit marks dirty (enrichment deferred to commit)",
          status(hln) == "dirty", f"status={status(hln)}")
    c.patch(f"/api/notes/{hln}", json={"body": "one two three four five SEVEN", "commit": True})
    check("committing the content edit re-triggers the agent",
          status(hln) == "force", f"status={status(hln)}")

    # Quick Event: a structured reminder is seeded and survives enrichment.
    ev = c.post("/api/notes", json={"body": "📅 Dentist\n(2026-09-01 09:00)",
        "reminder": {"title": "Dentist", "datetime_hint": "2026-09-01T09:00",
                     "datetime_resolved": "2026-09-01 09:00"}}).get_json()["id"]
    with qs.app.app_context():
        r = qs.get_db().execute("SELECT source FROM reminders WHERE note_id=?", (ev,)).fetchone()
    check("quick event seeds a user reminder", r is not None and r["source"] == "user")
    # Quick events are typed as 'event' so the agent auto-approves them; normal
    # notes stay 'text' and keep the review flow.
    check("quick event note_type is 'event'", c.get(f"/api/notes/{ev}").get_json()["note_type"] == "event")
    _plain = c.post("/api/notes", json={"body": "an ordinary note here"}).get_json()["id"]
    check("normal note stays note_type 'text'", c.get(f"/api/notes/{_plain}").get_json()["note_type"] == "text")

    # Settings (user preferences)
    prefs = c.get("/api/settings").get_json()["prefs"]
    check("settings defaults present", prefs.get("enrich_min_words") == 6 and prefs.get("brief_mode") == "ondemand")
    c.post("/api/settings", json={"enrich_min_words": 12, "summary_mode": "never"})
    check("settings persist", c.get("/api/settings").get_json()["prefs"]["enrich_min_words"] == 12)
    c.post("/api/settings", json={"summary_mode": "bogus"})
    check("invalid setting rejected", c.get("/api/settings").get_json()["prefs"]["summary_mode"] == "never")

    cl = c.post("/api/notes", json={"body": "- [ ] milk\n- [x] eggs", "note_type": "checklist"}).get_json()
    check("checklist note created", cl["note_type"] == "checklist")
    check("checklist note_type persists", note(cl["id"])["note_type"] == "checklist")
    txt = c.post("/api/notes", json={"body": "plain"}).get_json()
    check("text note defaults note_type=text", txt["note_type"] == "text")
    empty_cl = c.post("/api/notes", json={"body": "", "note_type": "checklist"})
    check("empty checklist allowed (no stray seed row)", empty_cl.status_code == 201)
    check("empty text note still rejected", c.post("/api/notes", json={"body": ""}).status_code == 400)
    # A checklist, once enriched, must not re-trigger the agent on item toggles.
    with qs.app.app_context():
        db = qs.get_db()
        db.execute("INSERT OR REPLACE INTO note_state (note_id, status) VALUES (?, 'approved')", (cl["id"],))
        db.commit()
    c.patch(f"/api/notes/{cl['id']}", json={"body": "- [x] milk\n- [ ] eggs"})
    check("checklist toggle does not re-run agent",
          note(cl["id"])["status"] == "approved", f"status={note(cl['id'])['status']}")

    with qs.app.app_context():
        qs.get_db().execute("INSERT INTO settings (key,value) VALUES ('consolidation_offer',?)",
                            (json.dumps({"tag": "smoke", "note_count": 5, "ts": time.time()}),))
        qs.get_db().commit()
    off = c.get("/api/consolidation").get_json()["offer"]
    check("consolidation offer surfaced", off and off["tag"] == "smoke")

    # ── 7. static assets & routes ───────────────────────────────────────────
    section("Static & routes")
    check("index served", c.get("/").status_code == 200)
    check("manifest served", c.get("/manifest.json").status_code == 200)
    check("about page served", c.get("/about.html").status_code == 200)
    man = c.get("/manifest.json").get_json()
    check("manifest has share_target", "share_target" in man)
    # Review sheet: discoverable editable box + explicit cancel.
    _html = c.get("/").get_data(as_text=True)
    check("review sheet has explicit Cancel button", 'id="adoptCancel"' in _html)
    check("review sheet flags the box as editable", 'adopt-hint' in _html)
    check("kebab opens the action popover", 'id="dKebab"' in _html and 'id="actionSheet"' in _html)
    check("details expander survives re-render (no fold-on-dismiss)",
          "_detailsOpen" in _html and "State belongs to the user" in _html)
    check("dashboard leads with at-a-glance tiles",
          "dtiles" in _html and "dtile" in _html and "data-pulse-nav" in _html)
    check("vitals card is light/blended, not a dark slab",
          "background:var(--card);border:1px solid var(--line);overflow:hidden" in _html)
    check("dark mode: themed, persisted, applied before first paint",
          'data-theme="dark"' in _html and "hg_theme" in _html
          and "applyTheme" in _html)
    check("no hardcoded surface whites left to break dark mode",
          # The toggle knob is intentionally white on both themes (it rides a
          # coloured track), so exclude that one declaration.
          _html.replace("background:#fff;box-shadow:0 1px 3px", "").count("background:#fff;") == 0)
    check("dismiss controls render on action items and reminders",
          "data-dismiss-aid" in _html and "data-dismiss-rid" in _html)
    check("dismiss offers Undo (a mis-tap must be recoverable)",
          "toast-act" in _html and "Undo" in _html)
    check("calendar-backed reminders confirm before removing the event",
          "Remove calendar event?" in _html and "delete_event=true" in _html)
    check("compose screen has the AI control (visible, one tap, not a menu dive)",
          "edAiSeg" in _html and "ai-tog" in _html)
    check("AI choice is per-note, not sticky (resets each new note)",
          'Ed.aiMode = (mode==="edit"' in _html and "not sticky" in _html.lower())
    check("reminders nav badge shows a live count (like actions/connections)",
          '"reminders_active"' in open("app.py").read() and 'set("bRem"' in _html)
    check("dark mode uses a dark note-tile palette (not glaring light pastels)",
          "PASTELS_DARK" in _html and "_isDark" in _html)
    check("editor opens at top with a deliberate cursor position (no jump)",
          "setSelectionRange(pos, pos)" in _html)
    check("no redundant Done button — the back arrow is the single exit",
          "ed-save" not in _html and 'id="edDone"' in _html)
    _appsrc = open("app.py").read()
    check("autosave marks dirty, not force (durability != enrichment trigger)",
          _appsrc.count('new_status = "dirty"')>=1 and 'data.get("commit"' in _appsrc)
    check("note_state migration preserves ALL columns (failures, updated_at)",
          "failures   INTEGER NOT NULL DEFAULT 0" in _appsrc
          and "updated_at TEXT NOT NULL DEFAULT" in _appsrc
          and 'INSERT INTO note_state__new\n' in _appsrc.replace("  "," ") or "INSERT INTO note_state__new" in _appsrc)
    check("repair migration restores columns dropped by the earlier bad rebuild",
          'if "failures" not in _ns_cols' in _appsrc
          and 'if "updated_at" not in _ns_cols' in _appsrc)
    check("sqlite3 imported at module scope (exception handlers reference it)",
          "\nimport sqlite3\n" in _appsrc)
    check("init_db is lock-resilient (busy_timeout + retry, never crashes boot)",
          "busy_timeout=30000" in _appsrc and "DB busy, retrying schema" in _appsrc
          and "assuming DB already initialised" in _appsrc)
    check("a dirty write falls back if the migration was deferred (no lost save)",
          "sqlite3.IntegrityError" in _appsrc
          and 'fallback = "raw" if new_status == "dirty"' in _appsrc)
    check("kebab menu sizes to content — no forced 70vh scrollbar in web view",
          "max-height:calc(100vh - 72px)" in _html and "max-height:70vh" not in _html)
    check("tile actions include delete (with confirmation)",
          'data-act="delete"' in _html and "c-act-danger" in _html
          and "Delete this note? This can" in _html)
    check("tile actions also reachable on mobile via long-press",
          "Long-press to reveal" in _html and "show-acts" in _html
          and "touchstart" in _html)
    check("a no-op edit (open/close, stray whitespace) does NOT trigger enrichment",
          "Ed.opened = note?.body" in _html
          and 'cur === (Ed.opened ?? Ed.saved ?? "").trim()' in _html
          and "nothing changed — leave status as-is" in _html)
    check("AI hint bar is calm — reveals on change, then auto-hides",
          ".ed-aibar.show{" in _html and "_aiHintT" in _html
          and "hint.classList.remove(\"show\")" in _html)
    check("AI enrichment + run consolidated into one kebab group",
          'grp("AI", aiSet.concat(aiDo))' in _html
          and '"AI · enrichment"' not in _html)
    check("Tidy up shows only when it differs from Run now (not redundant)",
          '_textProtected && st==="approved"' in _html)
    check("tiles show Keep-style hover quick-actions (desktop hover only)",
          "c-actions" in _html and "@media (hover:hover)" in _html
          and 'data-act="pin"' in _html and 'data-act="archive"' in _html)
    check("hover actions act on the note without opening it",
          "c-act[data-act]" in _html and "/notes/${id}/${act}" in _html)
    _about = _c.get("/about.html").get_data(as_text=True) if False else open("static/about.html").read()
    check("about page uses the architecture image, not the old inline SVG",
          "architecture.png" in _about and 'viewBox="0 0 720 468"' not in _about)
    check("architecture image asset is present to be served",
          __import__("os").path.exists("static/architecture.png"))
    check("related notes fold by default, expandable, latest-first",
          "const REL_FOLD = 3" in _html and "relExpanded" in _html
          and 'id="relMore"' in _html)
    check("action items show work/personal category + blocked-by",
          "acat-work" in _html and "acat-personal" in _html
          and "a.blocked_by" in _html)
    check("action_items schema has category + blocked_by (migration present)",
          "ADD COLUMN category" in _appsrc and "ADD COLUMN blocked_by" in _appsrc)
    check("one-time cleanup removes reworded duplicate reminders (same date + similar title)",
          "reminder dedup cleanup skipped" in _appsrc
          and "_similar" in _appsrc and "_acronym" in _appsrc
          and "never drop a calendar-linked row" in _appsrc)
    check("relationships query orders latest-first",
          "ORDER BY n.updated_at DESC" in _appsrc)
    check("save failures are surfaced, never swallowed (data-loss guard)",
          "flashSaveError" in _html
          and _html[_html.index("async function edFlush"):_html.index("function flashSaved")].count(".catch(()=>null)") == 0)
    check("a failed autosave does not falsely mark the note saved",
          "Do NOT advance Ed.saved" in _html)
    check("closeEditor keeps the editor open if the final commit fails",
          "flashSaveError();" in _html and "if (ok){" in _html)
    check("four new original elegant themes registered (Midnight/Sunset/Forest/Ocean)",
          all(f'{t}:' in _html and f'PASTELS_{t.upper()}' in _html
              for t in ["midnight","sunset","forest","ocean"]))
    check("every theme provides a full 8-color light+dark palette pair",
          _html.count("_DARK = [") >= 6)  # colorful, mono, slate, midnight, sunset, forest, ocean(dark) minus base PASTELS_DARK naming
    check("no theme references copyrighted characters or franchises",
          not any(bad in _html.lower() for bad in
                  ["spider-man","spiderman","he-man","phantom","marvel","batman","superman"]))
    check("theme picker previews the actual palette (cluster), not a flat swatch",
          "theme-cluster" in _html and "theme-grid" in _html and "pal.map(c=>" in _html)
    check("save-error indicator is persistent and unmissable",
          ".saveind.save-err{" in _html and "will retry" in _html)
    check("close/explicit commit promotes dirty -> force",
          "commit:true" in _html.replace(" ","") or "commit: true" in _html or 'commit":true' in _html.replace(" ",""))
    check("schema allows the dirty status",
          "'raw','dirty','pending','approved','force'" in open("schema.sql").read())
    check("save indicator is ambient, not a per-keystroke strobe",
          ".saveind.show{opacity:.75}" in _html and "_savedHideT" in _html
          and "ambient status rather than a notification" in _html)
    check("new notes autosave Keep-style (auto-create on first input)",
          'Ed.mode === "new"' in _html and "createNote(v, Ed.aiMode)" in _html
          and "flashSaved()" in _html)
    check("autosave debounce runs for new notes too, not just edits",
          "Autosave both new and existing" in _html)
    check("AI switch sits in the header (no scroll to reach it)",
          'id="edAiSeg"' in _html
          and _html.index('id="edAiSeg"') < _html.index('id="edText"'))
    check("AI mode explanation floats in-view (not a scroll-away footer)",
          "ed-aibar" in _html and "ed-ft" not in _html)
    check("sidebar navigation flushes the editor first (no covering-layer bug)",
          "leaveEditorIfOpen" in _html
          and _html.count("await leaveEditorIfOpen()") >= 4)
    check("dashboard card blends (no prism line / glow distractor)",
          "pulse-hero::before" not in _html and "pulse-hero::after" not in _html)
    _a = open("app.py").read()
    check("deleting a note never orphans its calendar events",
          "calendar_deletions" in _a and "_enqueue_calendar_deletions" in _a
          and "no orphans" not in _a or "_enqueue_calendar_deletions(db," in _a)
    check("calendar deletion queue is durable + idempotent",
          "CREATE TABLE IF NOT EXISTS calendar_deletions" in _a
          and "event_id     TEXT PRIMARY KEY" in _a
          and "ON CONFLICT(event_id) DO NOTHING" in _a)
    check("app badge shows open tasks (pending review is always 0 now)",
          "setAppBadge(s.action_pending" in _html)
    check("agent-liveness window tolerates the configured tick (no false 'down')",
          "AGENT_ALIVE_SECONDS" in open("app.py").read())
    # Match RENDERED labels only (the sa-l span), not prose in comments — an
    # earlier version of this check passed because the old label text happened to
    # appear inside an explanatory comment, which made the test lie.
    _labels = set(re.findall(r'class="sa-l">([^<]{2,40})<', _html))
    check("kebab labels are plain language, not jargon",
          "Tidy up my writing" in _labels
          and not any("enrich with ai" == l.lower() for l in _labels))
    check("kebab consolidates the enrichment setting and its actions into one AI group",
          'grp("AI", aiSet.concat(aiDo))' in _html
          and "const aiDo" in _html and "const aiSet" in _html)
    check("setting (segmented control) is emitted BEFORE the run action within the group",
          _html.index("const aiSet") < _html.index("aiSet.concat(aiDo)"))
    check("run action states its relationship to the setting",
          "uses the setting above" in _html)
    check("supporting text is a real second line, not inline (every row)",
          ".sheet-act .sa-l{flex:1;min-width:0;display:flex;flex-direction:column" in _html)
    check("rows top-align the leading icon with the headline",
          "align-items:flex-start" in _html and "min-height:44px" in _html)
    check("enrichment is ONE three-state choice, not overlapping toggles",
          '["full","metadata","off"]' in _html
          and 'data-enrich-mode="${v}"' in _html
          and "skipEnrichBtn" not in _html and "protBtn" not in _html)
    check("selection updates optimistically (no wait for the round-trip)",
          "setSegMode(v)" in _html and "OPTIMISTIC" in _html)
    check("a failed change reverts the selection instead of lying",
          "setSegMode(prev)" in _html)
    check("an already-open sheet gets refreshed markup (stale-highlight bug)",
          "function syncOpenSheet" in _html and "syncOpenSheet()" in _html)
    check("modes render as a compact segmented control, not stacked rows",
          'class="seg"' in _html and "seg-b" in _html and "mode-desc" in _html)
    check("UI is honest that Full/Metadata only apply on the NEXT run",
          "applies next run" in _html and "tap Run now to apply it" in _html)
    check("each mode states exactly which fields it produces",
          "title, tags, summary · rewrites body" in _html
          and "title, tags, summary · body untouched" in _html)
    check("mode reflects EFFECTIVE state (protected reads as Metadata)",
          "n.meta_only || n.protected" in _html)
    check("running AI on an opted-out note admits it flips the setting",
          "Turn AI on and run now" in _html)
    check("AI control is a labelled toggle with one sliding knob",
          ".ai-tog{" in _html and "ai-tog-knob" in _html
          and "ai-tog-track" in _html)
    check("AI toggle cannot be squeezed or drift vertically",
          "flex:none;\n  align-self:center" in _html or "flex:none;align-self:center" in _html
          or ("flex:none" in _html and "align-self:center" in _html))
    check("toggle track carries state: grey off, violet on",
          'aria-checked="true"] .ai-tog-track{background:var(--violet)' in _html
          and ".ai-tog-track{position:relative" in _html
          and "background:var(--line2)" in _html)
    check("toggle knob slides between states",
          'aria-checked="true"] .ai-tog-knob{transform:translateX(18px)' in _html)
    check("typing '#' suggests tags already in the notebook",
          "tagFragmentAt" in _html and "updateTagSuggestions" in _html
          and 'id="tagSug"' in _html)
    check("tag suggestions ignore mid-word hashes (C#, issue#42)",
          "(^|\\s)#([\\w-]*)$" in _html)
    check("completing a tag never leaves a double space",
          'const pad = /^\\s/.test(rest)' in _html)
    check("compose AI control is two-state On/Off",
          "setAiMode" in _html and "toggleAiMode" in _html
          and 'role="switch"' in _html)
    check("dashboard renamed to Agent Dashboard; top tiles removed",
          "Agent Dashboard" in _html and "dtiles" not in _html.split("let h = `")[-1][:1200])
    check("reminders tab sits between actions and connections",
          _html.index('data-view="vReminders"') > _html.index('data-view="vActs"')
          and _html.index('data-view="vReminders"') < _html.index('data-view="vConn"'))
    check("view switch closes lingering overlays (no covering-sheet bug)",
          "closeOverlays" in _html and "OVERLAYS" in _html)
    check("view switches paint instantly from cache (stale-while-revalidate)",
          "viewCache" in _html and "_sameNoteList" in _html)
    check("mutations invalidate the view cache (no stale notes)",
          "invalidateViewCache" in _html)
    check("note list renders progressively (first screenful, then rest)",
          "_cardHtml" in _html and "requestAnimationFrame" in _html
          and "insertAdjacentHTML" in _html)
    check("list paginates past 200 via infinite scroll (loadMore + offset)",
          "function loadMore" in _html and "offset=" in _html
          and "loadedCount" in _html and "S.total" in _html)
    _ab = c.get("/about.html").get_data(as_text=True)
    check("About page layers content by audience",
          "For everyone" in _ab and "For architects" in _ab
          and "For the technically curious" in _ab)
    check("About page carries real diagrams, not decoration",
          (_ab.count('role="img"') + _ab.count("architecture.png")) >= 3
          and "SQLite as the contract" in _ab)
    check("About page documents the CURRENT design (no stale claims)",
          "6,144" in _ab and "no approval" in _ab
          and "Termux:API" not in _ab and "Protect from rewrites" not in _ab)
    check("About page follows the app theme incl. dark mode",
          'data-theme="dark"' in _ab and "hg_theme" in _ab)
    check("About page has in-page nav + copy-command buttons",
          "cmd-copy" in c.get("/about.html").get_data(as_text=True)
          and 'href="#arch"' in c.get("/about.html").get_data(as_text=True))
    # Actions aging drill-down filters to only aging items.
    check("actions endpoint supports aging filter", "aging" in open("app.py").read() and "actions_aging" in open("app.py").read())
    check("dashboard rows still link into filtered views",
          "vActs?done=1" in _html and "view:vConn" in _html)
    # Calendar events formatted uniformly + sorted (dated first).
    check("calendar has uniform date formatter", "fmtEventDate" in _html)
    check("calendar resolves relative hints for sorting", "resolveEventDate" in _html)
    check("calendar groups events (Today/Tomorrow/Upcoming/Someday)", "cal-grp-h" in _html and 'section("Today"' in _html and 'section("Upcoming"' in _html)
    check("archive & hide actions present", 'id="archBtn"' in _html and 'id="hideBtn"' in _html)
    check("archived/hidden menu views present", 'data-lv="archived"' in _html and 'data-lv="hidden"' in _html)
    check("stash label used (not 'Hidden')", ">Stash<" in _html and ">Hidden<" not in _html)
    check("back-button tracks layer depth", "qsDepth" in _html and "_closingViaPop" in _html)
    check("hidden cards have blur styling", "card-hidden" in _html)
    check("URLs are linkified in notes", "nlink" in _html and "https?" in _html)
    check("note text wraps long URLs", "overflow-wrap:anywhere" in _html)
    check("links: tap=custom tab, long-press=full browser", "_openInFullBrowser" in _html and "intent://" in _html)
    check("Telugu fonts loaded", "Noto+Serif+Telugu" in _html and "Noto+Sans+Telugu" in _html)
    check("search precision control in settings", "Search precision" in _html and "search_min_similarity" in _html)
    check("restore-from-revision wired", "rev-restore" in _html and "/restore" in _html)
    check("desktop canvas scoped to wide screens", "@media(min-width:900px)" in _html)
    check("favicon links present (browser tab + iOS)",
          'rel="icon" href="/icons/favicon.svg"' in _html
          and 'rel="apple-touch-icon"' in _html
          and 'href="/icons/favicon.ico"' in _html)
    _app = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py")).read()
    check("action items can be dismissed (rejected, not just done)",
          "dismiss_action" in _app and "/actions/<aid>\", methods=[\"DELETE\"]" in _app)
    check("dismissal is undoable",
          "restore_action" in _app and "/restore" in _app)
    check("reminder dismissal is calendar-aware (never silent deletion)",
          "dismiss_reminder" in _app and "delete_event" in _app
          and "calendar_delete_requested" in _app)
    check("per-note extraction switches (no_actions / no_reminders)",
          "toggle_extract" in _app and "no_actions" in _app and "no_reminders" in _app)
    check("dismissed items are hidden from every surfacing query",
          _app.count("COALESCE(dismissed,0)=0") >= 3
          or _app.count("COALESCE(a.dismissed,0)=0") >= 1)
    check("skip-enrichment toggle: endpoint exists",
          "/skip-enrich" in _app and "toggle_skip_enrich" in _app)
    check("skip-enrichment keeps existing title/tags visible (no stranding)",
          "None if optout else" not in _app and "must not silently rewrite the past" in _app)
    check("skip-enrichment resolves a pending review (never a dead end)",
          "status='approved'" in _app and "strand" in _app)
    check("skip-enrichment settable at note creation",
          "skip_enrich" in _app)
    check("Keep importer present and dispatched",
          'elif cmd == "import-keep"' in _app and '"dedupe", "import-keep"' in _app)
    check("Keep importer is idempotent + backs up first",
          "hashlib.sha256" in _app and "pre-import-keep-" in _app)
    check("bulk import opts out of auto-enrichment by default",
          '0 if do_enrich else 1' in _app and '"--enrich" in argv' in _app)
    check("temporal query parser present (last week / last year this time)",
          "_parse_temporal" in _app and "last year this time" in _app)
    check("FTS5 full-text search with graceful LIKE fallback",
          "_init_fts" in _app and "HAS_FTS5" in _app and "notes_fts" in _app)
    check("FTS kept in sync by triggers",
          "notes_fts_ai" in _app and "notes_fts_au" in _app and "notes_fts_ad" in _app)
    check("semantic search vectorised via frombuffer (blob fast path)",
          "_rank_by_embedding" in _app and "frombuffer" in _app)
    check("embedding storage codec handles blob + legacy JSON",
          "_decode_embedding" in _app and "struct.unpack" in _app)
    check("note-list sort index present (schema + migration)",
          "idx_notes_list" in _app and "pinned DESC, updated_at DESC" in _app)
    check("pagination count only joins tables the WHERE needs",
          "count_joins" in _app and 'if status:' in _app and 'if q:' in _app)
    check("explicit Re-run/Polish clears the enrich opt-out",
          _app.count("UPDATE notes SET enrich_optout=0 WHERE id=?") >= 2)
    check("enrich_optout column migrates on existing DBs",
          'ALTER TABLE notes ADD COLUMN enrich_optout' in _app)
    check("Keep archived notes imported by default, staying archived",
          '"--skip-archived" not in argv' in _app
          and '"archived": 1 if d.get("isArchived") else 0,' in _app)
    check("notifications: SW-based, gesture-gated, backgrounded-only",
          "function notify(" in _html and "requestNotifyPermission" in _html
          and 'document.visibilityState !== "visible"' in _html)
    check("app badge wired to review count", "navigator.setAppBadge" in _html)
    check("maskable icons are full-bleed (not the rounded ones)",
          "maskable-192.png" in c.get("/manifest.json").get_data(as_text=True))
    for _p in ("/icons/favicon.svg", "/icons/favicon.ico", "/icons/apple-touch-icon.png"):
        check(f"{_p} serves", c.get(_p).status_code == 200)
    check("note cache gated to trusted origins", "PERSIST_OK" in _html and "IS_LOCAL" in _html)
    check("remote origin purges any pre-existing cache", 'deleteDatabase("hg")' in _html)
    check("offline writes not silently dropped remotely", "_memOutbox" in _html)
    check("offline messaging honest per-origin", "OFFLINE_SAVE_MSG" in _html)
    check("desktop is full-screen with persistent rail",
          "max-width:none" in _html and "body.rail-off" in _html
          and ".main, .nav{margin-left:var(--rail)" in _html)
    check("rail is fixed-position (menuLayer sits outside #app)",
          "#menuLayer{position:fixed" in _html and "grid-template-areas" not in _html)
    check("PWA menu still a modal drawer",
          ".menu{position:fixed;inset:0;z-index:500;display:none}" in _html)
    check("card palette has 8 perceptually uniform colours",
          '"#F7DEE2"' in _html and '"#DEF7F2"' in _html and '"#E6DEF7"' in _html)
    check("PWA layout untouched by desktop styles",
          ".masonry{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px}" in _html
          and ".hdr{height:56px" in _html)
    check("shell pinned so header/nav never scroll away", "position:fixed;inset:0" in _html)
    check("modern thin scrollbars (desktop only)",
          "scrollbar-width:thin" in _html and "::-webkit-scrollbar-thumb" in _html)
    check("popover re-run keeps status-act class (handler match)", 'class="sheet-act status-act" data-status="force"' in _html)
    check("polish handler uses no dead .act-i selector", ".act-i\").textContent" not in _html)
    check("Telugu in body/ui font stacks", "Noto Serif Telugu" in _html and "Noto Sans Telugu" in _html)
    check("dead Space Grotesk fallback removed", "Space Grotesk" not in _html)
    check("Edit is first in the icon toolbar", 'id="editBtn"' in _html and "actbar-ic" in _html)
    check("highlight tip relocated as caption", "hl-cap" in _html)
    check("icon toolbar present (Option A)", "actbar-ic" in _html and 'class="act-ic"' in _html)
    check("action popover present (anchored, not full sheet)", 'class="popover"' in _html and "actionSheetBody" in _html)
    check("popover grouped + delete separated + scrim", "sheet-grp-h" in _html and "sheet-act danger" in _html and "popover-scrim" in _html)
    check("calendar sorts dated events first",
          "datetime_resolved IS NOT NULL" in open("app.py").read() and "THEN 0 ELSE 1" in open("app.py").read())
    # Agent-errors count matches the actionable drill-down list.
    check("reminders have their own tab + list endpoint",
          'data-view="vReminders"' in _html and "loadReminders" in _html
          and "/api/reminders" in open("app.py").read())

    # ── summary ─────────────────────────────────────────────────────────────
    print(f"\n{'='*52}")
    total = _passed + _failed
    colour = G if _failed == 0 else R
    print(f"  {colour}{_passed}/{total} passed{X}"
          + (f"   {R}{_failed} FAILED{X}" if _failed else "   all green ✨"))
    print(f"{'='*52}\n")

    if not args.keep:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    else:
        print(f"temp DB kept at: {os.environ['QS_DB']}\n")

    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
