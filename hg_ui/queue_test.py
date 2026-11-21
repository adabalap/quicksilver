#!/usr/bin/env python3
"""App side of the durable queue: status strip data + Retry endpoint + schema."""
import sys, os, tempfile
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from e2e_test import load_app
P = F = 0
def check(c, m):
    global P, F
    if c: P += 1
    else: F += 1; print(f"  \033[31m✗ {m}\033[0m")

tmp = tempfile.mkdtemp()
qs = load_app(os.path.join(tmp, "q.db"))
now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
db = qs.sqlite3.connect(os.path.join(tmp, "q.db")); db.row_factory = qs.sqlite3.Row
check(db.execute("SELECT name FROM sqlite_master WHERE name='agent_jobs'").fetchone() is not None, "schema creates agent_jobs")
db.execute("INSERT INTO settings (key,value) VALUES ('agent_heartbeat',?)", (now,))
def job(kind, key, status):
    db.execute("INSERT INTO agent_jobs (kind,key,status,attempts,run_after,created_at,updated_at) VALUES (?,?,?,3,?,?,?)",
               (kind, key, status, now, now, now))
job("note", "n1", "dead"); job("note", "n2", "dead"); job("ask", "ask:x", "dead"); job("note", "n3", "queued"); job("note", "n4", "done")
db.commit()
cl = qs.app.test_client()
a = cl.get("/api/home").get_json()["agent"]
j = a["jobs"]
check(j["dead"] == 3 and j["dead_notes"] == 2 and j["dead_asks"] == 1, "status reports dead jobs by kind")
check(j["queued"] == 1, "status reports waiting jobs")
check(set(j["dead_ids"]) == {"n1", "n2"}, "status lists dead note ids for Review")
r = cl.post("/api/agent/jobs/retry").get_json()
check(r["retried"] == 3, "retry re-queues all dead jobs")
row = db.execute("SELECT status, attempts FROM agent_jobs WHERE key='n1'").fetchone()
check(tuple(row) == ("queued", 0), "retried job has a fresh budget")
check(cl.get("/api/home").get_json()["agent"]["jobs"]["dead"] == 0, "nothing dead after retry")
check(cl.post("/api/agent/jobs/retry").get_json()["retried"] == 0, "retry with nothing dead is a no-op")

# ── updated_at integrity + repair of the re-index incident ───────────────────
import repair_updated_at as rep
check("trigger is guarded against embedding writes",
      "embed_model" in db.execute("SELECT sql FROM sqlite_master WHERE name='notes_updated'").fetchone()[0])
db.execute("INSERT INTO notes (id, body, created_at, updated_at) VALUES ('u1','body words here','2025-01-01T10:00:00.000Z','2025-01-01T10:00:00.000Z')"); db.commit()
db.execute("UPDATE notes SET embedding=?, embed_model='m' WHERE id='u1'", (b"abcd",)); db.commit()
check("embedding write leaves updated_at alone", db.execute("SELECT updated_at FROM notes WHERE id='u1'").fetchone()[0] == "2025-01-01T10:00:00.000Z")
db.execute("UPDATE notes SET body='edited' WHERE id='u1'"); db.commit()
check("a real edit still bumps updated_at", db.execute("SELECT updated_at FROM notes WHERE id='u1'").fetchone()[0] > "2026")
db.execute("DROP TRIGGER notes_updated")
db.execute("UPDATE notes SET updated_at='2026-10-25T02:24:26.000Z' WHERE id='u1'")
db.execute("INSERT INTO note_chunks (id,note_id,idx,text,embedding,model,body_hash,created_at) VALUES ('u1:0','u1',0,'t',?,'m','h','2026-10-25T02:24:25.000Z')", (b"zzzz",))
db.commit()
p_ = rep.plan(db)
check("repair finds notes re-dated by indexing", [x[0] for x in p_] == ["u1"] and p_[0][2] == "2025-01-01T10:00:00.000Z")
db.execute("UPDATE notes SET updated_at='2026-10-26T09:00:00.000Z' WHERE id='u1'"); db.commit()
check("repair leaves a later genuine edit alone", rep.plan(db) == [])

t = P + F
print(f"\n  {'\033[32m' if not F else '\033[31m'}{P}/{t} passed{'  all green ✨' if not F else '  FAILED'}\033[0m")
sys.exit(1 if F else 0)
