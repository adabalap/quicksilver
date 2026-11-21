#!/usr/bin/env python3
"""Ask over a whole period ("summarise my notes from last week") and the search-index progress count."""
import sys, os, tempfile
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from e2e_test import load_app
P = F = 0
def check(c, m):
    global P, F
    if c: P += 1
    else: F += 1; print(f"  \033[31m✗ {m}\033[0m")

tmp = tempfile.mkdtemp()
qs = load_app(os.path.join(tmp, "a.db"))
cl = qs.app.test_client()
db = qs.sqlite3.connect(os.path.join(tmp, "a.db")); db.row_factory = qs.sqlite3.Row
db.execute("PRAGMA foreign_keys=ON")
ago = lambda d, h=0: (datetime.now(timezone.utc) - timedelta(days=d, hours=h)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
def mk(nid, body, days, hidden=0):
    db.execute("INSERT INTO notes (id, body, created_at, updated_at, hidden) VALUES (?,?,?,?,?)", (nid, body, ago(days), ago(days), hidden))
    db.execute("INSERT INTO note_state (note_id, status) VALUES (?,?)", (nid, "approved"))
for i in range(3): mk(f"w{i}", f"A note from this week number {i} about the garden and the bank", 1 + i)
mk("old", "A note from last year about nothing in particular at all", 200)
mk("hid", "A hidden note from this week that must never be used", 1, hidden=1)
db.commit()
import server.ask as ask
check(ask._is_window_only("summarise my notes") and ask._is_window_only("what did i write") and ask._is_window_only(""), "filler-only questions are whole-period questions")
check(not ask._is_window_only("bill") and not ask._is_window_only("summarise my notes about fedora"), "a topic word keeps the question topical")

r = cl.post("/api/ask", json={"question": "Summarise my notes from last week"}).get_json()
ids = [e["id"] for e in r["retrieved"]]
check(r["status"] == "pending" and set(ids) == {"w0", "w1", "w2"}, f"a whole-week question is answered from that week's notes ({ids})")
check("old" not in ids and "hid" not in ids, "older and hidden notes are left out")
check(ids == ["w0", "w1", "w2"], "newest first")
check(r["window"] is None, "no scope note when everything fit")
for q in ("What did I write yesterday", "show me everything from this week", "recap of the past week", "summarise the last 7 days"):
    rr = cl.post("/api/ask", json={"question": q}).get_json()
    check(rr["status"] == "pending" or "didn't write" in (rr.get("answer") or ""), f"'{q}' is understood as a period")
rr = cl.post("/api/ask", json={"question": "Summarise my notes from last year"}).get_json()
check(rr["status"] == "done" and "didn't write any notes in that period" in rr["answer"], "an empty period says so, in plain words")
rr = cl.post("/api/ask", json={"question": "dentist appointment last week"}).get_json()
print("   topical:", rr.get("status"), rr.get("answer"))
check(rr["status"] == "done" and "from that period" in rr["answer"], "a topical question with no match names the period")
for i in range(14): mk(f"m{i}", f"Another note number {i} written this week with some content in it", 1)
db.commit()
r = cl.post("/api/ask", json={"question": "summarise last week"}).get_json()
check(len(r["retrieved"]) == 10 and r["window"] == {"total": 17, "shown": 10}, f"a busy week is capped, and says how many were read ({r.get('window')})")
check(all(len(e["text"]) <= 451 for e in r["retrieved"]), "excerpts are kept short for the model")
w = cl.post("/api/ask", json={"question": "Write a summary of last week's notes", "mode": "write"}).get_json()
check(w["status"] == "pending", "write mode also works over a period")

wq = db.execute("SELECT question FROM ask_requests WHERE id=?", (r["request_id"],)).fetchone()
check(wq and "period is already satisfied" in wq["question"], "the model is told the period is already satisfied")
wr = db.execute("SELECT question FROM ask_requests WHERE id=?", (w["request_id"],)).fetchone()
check(wr and "already satisfied" not in wr["question"], "write mode question is untouched")

# time phrases
lo, hi, res = qs.search._parse_temporal("summarise my notes from last week")
check(lo and hi and res.startswith("summarise my notes"), "last week → window")
lo, hi, _ = qs.search._parse_temporal("what did I do yesterday")
check(lo and hi and (datetime.fromisoformat(hi[:-1]) - datetime.fromisoformat(lo[:-1])).total_seconds() == 86400, "yesterday is one whole local day")
for phrase in ("past week", "previous month", "past 3 days", "this year", "this month", "this week", "today"):
    check(qs.search._parse_temporal("notes " + phrase)[0] is not None, f"'{phrase}' is a time phrase")
check(qs.search._parse_temporal("notes about fedora")[0] is None, "no time phrase, no window")

print(f"\n  {P} passed, {F} failed")
sys.exit(1 if F else 0)
