#!/usr/bin/env python3
"""Every scheduled path of the real daemon, called directly.

The main loop wraps each periodic job in try/except and logs failures at debug level, so a broken job
(a wrong import, a renamed column) just stops doing its work and nothing says so. This test builds the real
MemosDaemon on a throwaway database with a stub model and a fake embedder, calls each job WITHOUT those
wrappers, and checks that it did its work.

    python ticks_test.py        (needs ../hg_ui beside the agent)
"""
import os, sys, json, sqlite3, tempfile, shutil, importlib.util, hashlib, logging
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
G, R, Y, X = "\033[32m", "\033[31m", "\033[33m", "\033[0m"
_ok = _bad = 0


def check(name, cond, detail=""):
    global _ok, _bad
    if cond: _ok += 1; print(f"  {G}✓{X} {name}")
    else: _bad += 1; print(f"  {R}✗ {name}{X}" + (f"  → {detail}" if detail else ""))


def section(t): print(f"\n{Y}▸ {t}{X}")


PWA = os.path.abspath(os.path.join(HERE, "..", "hg_ui", "app.py"))
if not os.path.exists(PWA):
    print("hg_ui not found beside the agent — scheduled-path tests skipped"); sys.exit(0)

tmp = tempfile.mkdtemp()
dbp = os.path.join(tmp, "n.db")
os.environ["QS_DB"] = dbp
spec = importlib.util.spec_from_file_location("pwa_app", PWA)
pwa = importlib.util.module_from_spec(spec); sys.modules["pwa_app"] = pwa; spec.loader.exec_module(pwa)
pwa.init_db()

from memos_daemon.config import DEFAULT_CONFIG
from memos_daemon.daemon import MemosDaemon
from memos_daemon.embeddings import Embedder

# ── a quiet, deterministic daemon: real code, stub model, fake embedder ───────
cfg = dict(DEFAULT_CONFIG)
cfg.update(db_path=dbp, backend="quicksilver", state_file=os.path.join(tmp, "state.json"),
           pending_writes_file=os.path.join(tmp, "pending"), todo_file=os.path.join(tmp, "todos.json"),
           thermal_enabled=False, embed_model="", engine="auto", model_path="", calendar_enabled=False,
           dashboard_enabled=False, connections_enabled=False, todo_enabled=False)


class Model:
    """Answers each kind of prompt the periodic jobs send."""
    active = "stub"
    def __init__(self): self.prompts = []
    def complete(self, prompt, max_tokens=200, system_prompt=None):
        self.prompts.append(prompt)
        if "daily brief" in prompt:
            return "GREETING: A busy day of chores and plans.\nFOCUS: Settle the water bill.\nBULLETS:\n- Passport appointment\n- Bank call"
        if "consolidated summary" in prompt:
            return "- Point one\n- Point two"
        if "Task:" in prompt:
            return "Open the folder"
        return "A calm week of small chores."
    # the heavier entry points the worker calls
    _ask_kept = 2
    def enrich(self, text, hints=None):
        return {"title": "Water bill and bank", "summary": "Pay the bill and call the bank.", "tags": ["finance", "chores"],
                "action_items": ["Call the bank about the card"], "reminders": [{"title": "Pay water bill", "datetime_hint": "tomorrow"}],
                "polished": None, "confidence": 0.9, "priority": "medium", "corrections": [], "_latency_s": 0.2}
    def ask(self, q, excerpts, history=None): return "The bill is due this week [1].\nSOURCES: [1]"
    def write(self, q, excerpts): return "A short piece about the bill.\nSOURCES: [1]"
    def chat(self, q, history=None): return "Hello from the model."
    def story(self, desc, outline="", genre="", length="short", lang="en", note_text="", revise="", instruction=""):
        return ("# The Fox\n\nA revised story." if revise else "# The Fox\n\nOnce upon a time, " + desc[:20])


class FakeST:
    def encode(self, text, normalize_embeddings=True):
        h = hashlib.sha256(text.encode()).digest()
        v = [(b - 127.5) / 127.5 for b in h[:8]]
        n = sum(x * x for x in v) ** 0.5 or 1.0
        return [x / n for x in v]


logging.disable(logging.CRITICAL)
d = MemosDaemon(cfg)
logging.disable(logging.NOTSET)
d.gemma = Model()
emb = Embedder("fake-model"); emb._tried = True; emb.ok = True; emb._dim = 8; emb.tag = "fake-model"; emb._model = FakeST()
d.embedder = emb; d.memos.embedder = emb

db = sqlite3.connect(dbp); db.row_factory = sqlite3.Row
iso = lambda days: (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def note(nid, body, days=1, tags=(), title=None, status="approved"):
    db.execute("INSERT INTO notes (id, body, created_at, updated_at) VALUES (?,?,?,?)", (nid, body, iso(days), iso(days)))
    db.execute("INSERT INTO note_state (note_id, status) VALUES (?,?)", (nid, status))
    if title:
        db.execute("INSERT INTO enrichments (id, note_id, title, summary) VALUES (?,?,?,?)", ("e" + nid, nid, title, "A summary of " + title))
    for t in tags:
        db.execute("INSERT OR IGNORE INTO tags (note_id, tag) VALUES (?,?)", (nid, t))
    db.commit()


for i in range(5):
    note(f"in{i}", f"Inspiration number {i}: an idea about gardens, light and the sea that is long enough", days=i % 3, tags=("inspiration",), title=f"Idea {i}")
note("dupA", "Buy milk, eggs and bread from the corner shop on the way home tonight", title="Groceries")
note("dupB", "Buy milk, eggs and bread from the corner shop on the way home tonight", title="Groceries again")
note("task1", "Pay the water bill before the end of the week and call the bank about the card", days=12, title="Chores")
db.execute("INSERT INTO action_items (id, note_id, text) VALUES ('a1','task1','Pay the water bill')")
db.execute("INSERT INTO relationships (note_id_a, note_id_b, score) VALUES ('dupA','dupB',0.97)")
db.commit()


def setting(key):
    r = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r[0] if r else None


def run(name, fn, *a):
    """Call a job directly. A raised exception is a failed check, never swallowed."""
    try:
        return fn(*a), None
    except Exception as e:
        import traceback
        return None, f"{type(e).__name__}: {e}\n{traceback.format_exc().splitlines()[-3]}"


# ── daily brief ──────────────────────────────────────────────────────────────
section("daily brief")
db.execute("INSERT INTO settings (key, value) VALUES ('brief_refresh_requested','1')"); db.commit()
_, err = run("brief", d._maybe_daily_brief)
b = json.loads(setting("daily_brief") or "{}")
check("composes without raising", err is None, err)
check("stores a structured brief", b.get("greeting") and b.get("focus") and len(b.get("bullets", [])) == 2, str(b)[:120])
check("the refresh request is cleared", setting("brief_refresh_requested") == "0")
check("the prompt carries today's notes and open tasks", "Idea" in d.gemma.prompts[-1] and "Pay the water bill" in d.gemma.prompts[-1])

# ── weekly digest ────────────────────────────────────────────────────────────
section("weekly digest")
_, err = run("digest", d._maybe_weekly_digest)
dg = json.loads(setting("weekly_digest") or "{}")
check("composes without raising", err is None, err)
check("stores this week's digest with counts", dg.get("counts", {}).get("notes", 0) >= 1, str(dg)[:140])

# ── duplicates ───────────────────────────────────────────────────────────────
section("duplicate scan")
_, err = run("dups", d._maybe_find_duplicates)
dups = db.execute("SELECT note_a, note_b, status FROM dup_suggestions").fetchall()
check("runs without raising", err is None, err)
check("finds the identical pair", len(dups) == 1 and {dups[0]["note_a"], dups[0]["note_b"]} == {"dupA", "dupB"} and dups[0]["status"] == "pending")

# ── consolidation ────────────────────────────────────────────────────────────
section("consolidation")
_, err = run("consol", d._maybe_consolidation)
offer = json.loads(setting("consolidation_offer") or "{}")
check("offers a theme with four or more notes", err is None and offer.get("tag") == "inspiration" and offer.get("note_count", 0) >= 4, f"{err} {offer}")
db.execute("INSERT INTO settings (key, value) VALUES ('consolidation_accepted','1')"); db.commit()
_, err = run("consol2", d._maybe_consolidation)
made = db.execute("SELECT n.id, n.body FROM notes n JOIN tags t ON t.note_id=n.id WHERE t.tag='consolidated'").fetchall()
check("accepting drafts a consolidated note", err is None and len(made) == 1 and "Consolidated: #inspiration" in made[0]["body"] and "Point one" in made[0]["body"], f"{err} {[m['body'][:50] for m in made]}")
check("the offer is cleared once drafted", setting("consolidation_offer") is None)

# ── calendar deletions ───────────────────────────────────────────────────────
section("calendar deletions")
class Cal:
    def __init__(self): self.deleted = []
    def delete_event(self, ev): self.deleted.append(ev); return ev != "bad"
d.calendar = Cal(); cfg["calendar_enabled"] = True
db.execute("INSERT INTO calendar_deletions (event_id, note_id, title) VALUES ('ev1','x','Dentist')")
db.execute("INSERT INTO calendar_deletions (event_id, note_id, title) VALUES ('bad','x','Stuck')")
db.execute("INSERT INTO reminders (id, note_id, title, calendar_event_id, calendar_delete_requested) VALUES ('r9','task1','Dentist','ev1',1)")
db.commit()
_, err = run("caldel", d._process_calendar_deletions)
check("runs without raising", err is None, err)
check("a removed event is marked done and unlinked", db.execute("SELECT done FROM calendar_deletions WHERE event_id='ev1'").fetchone()[0] == 1
      and db.execute("SELECT calendar_event_id FROM reminders WHERE id='r9'").fetchone()[0] == "")
check("a failing one is counted, not lost", db.execute("SELECT attempts, done FROM calendar_deletions WHERE event_id='bad'").fetchone()[:] == (1, 0))
cfg["calendar_enabled"] = False

# ── search index ─────────────────────────────────────────────────────────────
section("search index")
n, err = run("reindex", d._reindex_batch, 30.0, 50)
p = d.memos.index_progress()
check("indexes notes without raising", err is None and n and n > 0, f"{err} n={n}")
check("every note ends up on the active model", p["done"] == p["total"] and p["model"] == "fake-model", str(p))
check("passages exist for search", db.execute("SELECT COUNT(*) FROM note_chunks").fetchone()[0] >= p["total"])
_, err = run("pub", d._publish_embed_active)
check("the active model is published for the app", err is None and json.loads(setting("embed_active") or "{}").get("model") == "fake-model", err)
n2, _ = run("reindex2", d._reindex_batch, 30.0, 50)
check("a second pass has nothing left to do", n2 == 0)

# ── relationships ────────────────────────────────────────────────────────────
section("relationships")
_, err = run("conn", d._update_connections)
check("computes relationships without raising", err is None, err)
check("writes relationship pairs", db.execute("SELECT COUNT(*) FROM relationships").fetchone()[0] >= 1)

# ── telemetry ────────────────────────────────────────────────────────────────
section("telemetry and heartbeat")
t, err = run("tel", d._telemetry)
check("snapshot builds", err is None and t and t["version"] and "thermal_state" in t and t["index"], err)
_, err = run("hb", d.memos.heartbeat, t)
check("heartbeat is stored for the dashboard", err is None and setting("agent_heartbeat") and json.loads(setting("agent_telemetry"))["version"] == t["version"], err)

# ── queue housekeeping ───────────────────────────────────────────────────────
section("queue housekeeping")
for nm in ("_jobs_tick", "_rescue_stuck_force", "_promote_stranded_dirty", "_enqueue_changed_since_watermark",
           "_enqueue_stale_ask_requests", "_media_scan"):
    fn = getattr(d, nm)
    _, err = run(nm, fn)
    check(f"{nm} runs without raising", err is None, err)
_, err = run("prune", d.jobs.prune)
check("pruning old jobs runs", err is None, err)

# ── follow-through + the loop's own helpers ──────────────────────────────────
section("follow-through")
d.gemma.prompts.clear()
_, err = run("fu", d._maybe_followups)
check("the daily pass raises the stale task", err is None and db.execute("SELECT COUNT(*) FROM followups WHERE status='pending'").fetchone()[0] == 1, err)
check("user prefs read through the mixin", d._user_prefs().get("followups") == "on")

# ── a failed write must not leave a half-finished note or hold the database lock ──
section("failed write is rolled back")
note("half1", "Two tasks live here, and a bad model answer arrives while they are being replaced", days=0, title="Before")
db.execute("INSERT INTO action_items (id, note_id, text) VALUES ('h1','half1','Keep me'), ('h2','half1','Keep me too')")
db.execute("INSERT INTO tags (note_id, tag) VALUES ('half1','keepertag')"); db.commit()
bad = {"title": "After", "summary": "x", "tags": ["newtag"], "action_items": [{"task": ["not", "text"]}], "priority": "low"}
try:
    d.memos.write_enrichment("half1", bad, "pending"); raised = False
except Exception:
    raised = True
check("a bad answer from the model makes the write fail loudly", raised)
probe = sqlite3.connect(dbp, timeout=1.0, isolation_level=None)
try:
    probe.execute("BEGIN IMMEDIATE"); probe.execute("ROLLBACK"); free = True
except sqlite3.OperationalError:
    free = False
finally:
    probe.close()
check("…and the database is not left locked for the app", free)
check("…the note's tasks are exactly as before", sorted(r[0] for r in db.execute("SELECT text FROM action_items WHERE note_id='half1'")) == ["Keep me", "Keep me too"])
check("…its tags are exactly as before", [r[0] for r in db.execute("SELECT tag FROM tags WHERE note_id='half1'")] == ["keepertag"])
check("…and no half-written enrichment appeared", [r[0] for r in db.execute("SELECT title FROM enrichments WHERE note_id='half1'")] == ["Before"])

# ── the main pipeline: a raw note becomes title, tags, tasks and a reminder ───
section("enrichment pipeline")
note("pipe1", "Need to pay the water bill tomorrow and ring the bank about the card that stopped working", days=0, status="raw")
_, err = run("one", d._process_one, "pipe1")
enr = db.execute("SELECT title, summary FROM enrichments WHERE note_id='pipe1'").fetchone()
check("a raw note is processed without raising", err is None, err)
check("the title is stored (a short note gets no summary: it is its own)", enr and enr["title"] == "Water bill and bank" and not enr["summary"], str(dict(enr) if enr else None))
check("tags are stored", {r[0] for r in db.execute("SELECT tag FROM tags WHERE note_id='pipe1'")} >= {"finance", "chores"})
check("the task is stored", [r[0] for r in db.execute("SELECT text FROM action_items WHERE note_id='pipe1'")] == ["Call the bank about the card"])
check("the reminder is stored", [r[0] for r in db.execute("SELECT title FROM reminders WHERE note_id='pipe1'")] == ["Pay water bill"])
check("the note moves on from 'raw'", db.execute("SELECT status FROM note_state WHERE note_id='pipe1'").fetchone()[0] in ("pending", "approved"))


# ── typing while the model works: the stale result is dropped, nothing is stored ──
note("typ1", "Need to pay the water bill tomorrow and ring the bank about the card that stopped working again", days=0, status="raw")
_orig_enrich = d.gemma.enrich
def _enrich_then_edit(text, hints=None):
    r = _orig_enrich(text, hints)
    db.execute("UPDATE notes SET body=body||' and one more thing I just typed' WHERE id='typ1'"); db.commit()
    return r
d.gemma.enrich = _enrich_then_edit
_, err = run("typ", d._process_one, "typ1")
d.gemma.enrich = _orig_enrich
check("a note edited while the model works raises nothing", err is None, err)
check("…and the result for the old text is not stored", db.execute("SELECT COUNT(*) FROM enrichments WHERE note_id='typ1'").fetchone()[0] == 0)

# ── Ask: all four modes ──────────────────────────────────────────────────────
section("Ask your notes")
ctx = json.dumps([{"id": "task1", "title": "Chores", "text": "Pay the water bill before the end of the week."},
                  {"id": "pipe1", "title": "Water bill and bank", "text": "Ring the bank about the card."}])
def ask(rid, mode, q, history="[]", context=ctx):
    db.execute("INSERT INTO ask_requests (id, question, context_json, mode, history_json) VALUES (?,?,?,?,?)", (rid, q, context, mode, history)); db.commit()
    _, e = run(rid, d._process_ask, rid)
    r = db.execute("SELECT status, answer, cited_note_ids, error FROM ask_requests WHERE id=?", (rid,)).fetchone()
    return e, r
e, r = ask("q1", "notes", "When is the water bill due?")
check("Notes: answered with the cited note", e is None and r["status"] == "done" and "due this week" in r["answer"] and json.loads(r["cited_note_ids"]) == ["task1"], f"{e} {dict(r)}")
e, r = ask("q2", "chat", "Say hello")
check("Chat: answered with no notes", e is None and r["status"] == "done" and r["answer"] == "Hello from the model.", f"{e} {dict(r)}")
e, r = ask("q3", "write", "Write about the bill")
check("Write: answered", e is None and r["status"] == "done" and "short piece" in r["answer"], f"{e} {dict(r)}")
e, r = ask("q4", "story", "a fox finds a lantern", history=json.dumps({"genre": "mystery", "length": "short"}),
           context=json.dumps([{"note_id": "x", "text": "A red fox in the snow."}]))
check("Story: written from the photo description", e is None and r["status"] == "done" and r["answer"].startswith("# The Fox") and "red fox" in r["answer"].lower(), f"{e} {dict(r)}")
e, r = ask("q5", "story", "make it funnier", history=json.dumps({"revise": "# The Fox\n\nOnce.", "outline": "a fox"}), context="[]")
check("Story: a revision needs no description", e is None and r["status"] == "done" and "revised story" in r["answer"], f"{e} {dict(r)}")
e, r = ask("q6", "story", "", context="[]")
check("Story: no description, no idea and no note words is a clear error", e is None and r["status"] == "error" and "description" in (r["error"] or "").lower(), f"{e} {dict(r)}")
e, r = ask("q6b", "story", "I am running late for a movie", context="[]")
check("Story: an idea alone is enough when the photo has no description", e is None and r["status"] == "done" and r["answer"].startswith("# "), f"{e} {dict(r)}")
e, r = ask("q7", "notes", "anything", context="[]")
check("Notes: no excerpts is an error, not a hang", e is None and r["status"] == "error", f"{e} {dict(r)}")
_, err = run("gone", d._process_ask, "nope")
check("an unknown request id is skipped quietly", err is None, err)

# ── the tick itself ──────────────────────────────────────────────────────────
section("the main-loop tick calls the jobs it should")
src = open(os.path.join(HERE, "memos_daemon", "parts", "sources.py"), encoding="utf-8").read()
called = [m for m in ("_maybe_daily_brief", "_maybe_weekly_digest", "_maybe_find_duplicates", "_maybe_consolidation",
                      "_maybe_followups", "_process_calendar_deletions", "_reindex_batch") if m + "()" in src]
check("every scheduled job above is called from the tick", len(called) == 7, str(called))

shutil.rmtree(tmp, ignore_errors=True)
print(f"\n{G if not _bad else R}{_ok} passed, {_bad} failed{X}")
sys.exit(1 if _bad else 0)
