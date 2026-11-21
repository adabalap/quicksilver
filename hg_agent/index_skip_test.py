#!/usr/bin/env python3
"""A note with nothing indexable must not leave the "getting your notes ready" counter one short for ever."""
import os, sys, tempfile, importlib.util, sqlite3, logging, hashlib
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
P = F = 0
def check(c, m):
    global P, F
    if c: P += 1
    else: F += 1; print(f"  \033[31m✗ {m}\033[0m")
PWA = os.path.abspath(os.path.join(HERE, "..", "hg_ui", "app.py"))
if not os.path.exists(PWA): print("hg_ui not found — skipped"); sys.exit(0)
tmp = tempfile.mkdtemp(); dbp = os.path.join(tmp, "n.db"); os.environ["QS_DB"] = dbp
spec = importlib.util.spec_from_file_location("pwa_app", PWA); pwa = importlib.util.module_from_spec(spec)
sys.modules["pwa_app"] = pwa; spec.loader.exec_module(pwa); pwa.init_db()
from memos_daemon.qs_client import QuicksilverClient
class Emb:
    tag = "fake"; dim = 4
    def available(self): return True
    def encode(self, text, kind="passage"):
        h = hashlib.sha256(text.encode()).digest(); return [b / 255 for b in h[:4]]
qc = QuicksilverClient(dbp); qc.embedder = Emb()
db = sqlite3.connect(dbp); db.row_factory = sqlite3.Row
def mk(nid, body):
    db.execute("INSERT INTO notes (id, body) VALUES (?,?)", (nid, body)); db.execute("INSERT INTO note_state (note_id,status) VALUES (?, 'approved')", (nid,)); db.commit()
mk("a", "A normal note with a sentence in it"); mk("b", "Another normal note about the garden")
mk("hl", "   \n  ")           # whitespace only after highlight stripping
db.execute("UPDATE notes SET body='==  ==' WHERE id='hl'"); db.commit()
import memos_daemon.qs_client as q
orig = q.strip_highlights
q.strip_highlights = lambda b: "" if (b or "").startswith("==") else orig(b)
check(set(qc.notes_needing_index(50)) == {"a", "b", "hl"}, "all three are waiting at first")
for nid in qc.notes_needing_index(50): qc.index_note_chunks(nid, force=True)
prog = qc.index_progress()
check(prog["done"] == prog["total"] == 3, f"progress reaches 3/3, not 2/3 ({prog})")
check(qc.notes_needing_index(50) == [], "nothing stays on the work list")
check(db.execute("SELECT reason FROM index_skips WHERE note_id='hl'").fetchone()["reason"] == "empty", "the empty note is recorded as skipped")
db.execute("UPDATE notes SET body='Now this note has real words in it' WHERE id='hl'"); db.commit()
check(qc.index_note_chunks("hl") > 0, "editing it makes it indexable again")
check(db.execute("SELECT COUNT(*) FROM index_skips").fetchone()[0] == 0 and qc.index_progress()["done"] == 3, "the skip is cleared once it has chunks")
# repeated failure path
mk("bad", "Fine text here")
qc.mark_index_skip("bad", "failed")
check("bad" not in qc.notes_needing_index(50) and qc.index_progress()["done"] == 4, "a note that keeps failing is counted so progress can finish")
check(qc.index_note_chunks("bad") > 0 and db.execute("SELECT COUNT(*) FROM index_skips WHERE note_id='bad'").fetchone()[0] == 0, "and an ordinary re-index still works for it")
db.execute("DELETE FROM notes WHERE id='hl'"); db.commit()
check(qc.index_progress()["total"] == 3, "deleting a note keeps the counts honest")
# the batch loop: a note that raises every time is counted after three tries and does not block the rest
from memos_daemon.daemon import MemosDaemon
from memos_daemon.config import DEFAULT_CONFIG
cfg = dict(DEFAULT_CONFIG); cfg.update(db_path=dbp, backend="quicksilver", state_file=os.path.join(tmp, "st.json"),
    pending_writes_file=os.path.join(tmp, "pw"), todo_file=os.path.join(tmp, "td.json"), thermal_enabled=False, embed_model="",
    engine="auto", model_path="", calendar_enabled=False, dashboard_enabled=False, connections_enabled=False, todo_enabled=False)
logging.disable(logging.CRITICAL); d = MemosDaemon(cfg); logging.disable(logging.NOTSET)
d.embedder = Emb(); d.memos.embedder = d.embedder; d.running = True
mk("boom", "This note always explodes when indexed"); mk("fine", "A healthy note that should still be indexed")
orig_one = d._reindex_one
def one(nid):
    if nid == "boom": raise RuntimeError("encoder exploded")
    return orig_one(nid)
d._reindex_one = one
for _ in range(4): d._reindex_batch(max_seconds=5, limit=10)
p = d.memos.index_progress()
check(p["done"] == p["total"], f"a note that always raises no longer holds the counter back ({p})")
check(db.execute("SELECT reason FROM index_skips WHERE note_id='boom'").fetchone()["reason"] == "failed", "and is recorded as failed")
check(db.execute("SELECT COUNT(*) FROM note_chunks WHERE note_id='fine'").fetchone()[0] > 0, "the healthy note was indexed despite it")
print(f"\n  {P} passed, {F} failed"); sys.exit(1 if F else 0)
