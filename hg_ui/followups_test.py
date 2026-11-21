#!/usr/bin/env python3
"""Task follow-through API: list, accept (plan / drop), done, drop, not-now, undo, expiry, prefs, stats."""
import sys, os, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from e2e_test import load_app
P = F = 0
def check(c, m):
    global P, F
    if c: P += 1
    else: F += 1; print(f"  \033[31m✗ {m}\033[0m")

tmp = tempfile.mkdtemp()
qs = load_app(os.path.join(tmp, "f.db"))
cl = qs.app.test_client()
db = qs.sqlite3.connect(os.path.join(tmp, "f.db")); db.row_factory = qs.sqlite3.Row
db.execute("PRAGMA foreign_keys=ON")

def note(body="A note about the water bill and other chores"):
    r = cl.post("/api/notes", json={"body": body}); assert r.status_code == 201
    return r.get_json()["id"]

def task(nid, text, tid):
    db.execute("INSERT INTO action_items (id, note_id, text) VALUES (?,?,?)", (tid, nid, text)); db.commit()

def fu(nid, text, move="reschedule", date="2026-10-29", step="", title="Chores"):
    db.execute("INSERT INTO followups (note_id, note_title, text, key, move, move_date, step, reason) VALUES (?,?,?,?,?,?,?,?)",
               (nid, title, text, f"{nid}|{' '.join(text.lower().split())}", move, date, step, "Open 9 days"))
    db.commit()
    return db.execute("SELECT MAX(id) FROM followups").fetchone()[0]

st = lambda fid: db.execute("SELECT status FROM followups WHERE id=?", (fid,)).fetchone()[0]
tk = lambda tid: dict(db.execute("SELECT done, dismissed FROM action_items WHERE id=?", (tid,)).fetchone())
rems = lambda nid: [dict(r) for r in db.execute("SELECT * FROM reminders WHERE note_id=? AND COALESCE(dismissed,0)=0", (nid,))]

n = note()
check(cl.get("/api/followups").get_json() == {"items": [], "learning": {}}, "empty list is empty, not an error")
check(cl.get("/api/stats").get_json()["followups"] == 0, "stats carry a follow-up count")
_g = fu(note("ghost"), "A task that was never there")
check(cl.get("/api/stats").get_json()["followups"] == 0 and st(_g) == "expired", "a proposal with no live task is never counted")

# ── listing
task(n, "Pay the water bill", "t1"); task(n, "Call the bank", "t2")
f1 = fu(n, "Pay the water bill"); f2 = fu(n, "Call the bank", "first_step", step="Find the number")
items = cl.get("/api/followups").get_json()["items"]
check([i["id"] for i in items] == [f1, f2], "pending decisions listed in order")
check(items[1]["step"] == "Find the number" and items[0]["reason"] == "Open 9 days" and items[0]["note_title"] == "Chores", "fields reach the app")
check(cl.get("/api/stats").get_json()["followups"] == 2, "the drawer badge count is the pending count")
db.execute("INSERT INTO settings (key, value) VALUES ('followups_learning', ?)", ('{"disabled":["drop"],"paused_until":null}',)); db.commit()
check(cl.get("/api/followups").get_json()["learning"]["disabled"] == ["drop"], "what the agent has learned is shown")

# ── accept a plan
r = cl.post(f"/api/followups/{f1}/accept").get_json()
rm = rems(n)
check(r["ok"] and r["move"] == "reschedule" and r["date"] == "2026-10-29" and st(f1) == "accepted", "accepting a plan resolves it")
check(len(rm) == 1 and rm[0]["title"] == "Pay the water bill" and rm[0]["datetime_resolved"] == "2026-10-29"
      and rm[0]["source"] == "user", "…and creates a reminder the agent will turn into a calendar event")
check(tk("t1") == {"done": 0, "dismissed": 0}, "the task itself stays open")
check(db.execute("SELECT reminder_id FROM followups WHERE id=?", (f1,)).fetchone()[0] == rm[0]["id"], "the reminder is remembered for undo")
check(cl.post(f"/api/followups/{f1}/accept").get_json()["status"] == "accepted" and len(rems(n)) == 1, "accepting twice does not double-book")
check([i["id"] for i in cl.get("/api/followups").get_json()["items"]] == [f2], "an answered decision leaves the card")

# first-step plan carries the step into the reminder
r = cl.post(f"/api/followups/{f2}/accept").get_json()
check(any(x["title"] == "Call the bank" and x["description"] == "Start with: Find the number" for x in rems(n)), "a first-step plan carries the step into the reminder")

# ── undo of a plan
r = cl.post(f"/api/followups/{f1}/undo").get_json()
check(r["ok"] and st(f1) == "pending" and not [x for x in rems(n) if x["title"] == "Pay the water bill"], "undo takes the plan back")
check([i["id"] for i in cl.get("/api/followups").get_json()["items"]] == [f1], "…and the decision returns to the card")
db.execute("UPDATE reminders SET calendar_event_id='created' WHERE note_id=?", (n,)); db.commit()
cl.post(f"/api/followups/{f1}/accept")
db.execute("UPDATE reminders SET calendar_event_id='created' WHERE note_id=? AND title='Pay the water bill'", (n,)); db.commit()
check(cl.post(f"/api/followups/{f1}/undo").get_json()["calendar"] == "kept", "undo says when a calendar event already exists (it is never deleted unasked)")

# ── same task, same day: reuse the existing reminder
db.execute("UPDATE reminders SET dismissed=0"); db.commit()
f1b = fu(n, "Pay the water bill")
before = len(rems(n))
cl.post(f"/api/followups/{f1b}/accept")
check(len(rems(n)) == before and db.execute("SELECT reminder_id FROM followups WHERE id=?", (f1b,)).fetchone()[0] is None, "an identical reminder for that day is reused, and undo won't touch it")

# ── done, drop, not now
n2 = note("Another note with several jobs in it"); task(n2, "File the report", "t3"); task(n2, "Clean the garage", "t4"); task(n2, "Water the plants", "t5")
f3 = fu(n2, "File the report"); f4 = fu(n2, "Clean the garage"); f5 = fu(n2, "Water the plants")
check(cl.post(f"/api/followups/{f3}/done").get_json()["status"] == "done" and tk("t3")["done"] == 1 and st(f3) == "done", "'Already done' completes the task")
check(db.execute("SELECT done_at FROM action_items WHERE id='t3'").fetchone()[0], "…with a completion time")
check(cl.post(f"/api/followups/{f4}/drop").get_json()["status"] == "dropped" and tk("t4")["dismissed"] == 1, "'Drop' dismisses the task (so it is not re-extracted)")
check(cl.post(f"/api/followups/{f5}/dismiss").get_json()["status"] == "dismissed" and tk("t5") == {"done": 0, "dismissed": 0}, "'Not now' only closes the proposal")
check(db.execute("SELECT resolved_at FROM followups WHERE id=?", (f5,)).fetchone()[0], "…and records when, so the agent can learn")
cl.post(f"/api/followups/{f3}/undo"); cl.post(f"/api/followups/{f4}/undo"); cl.post(f"/api/followups/{f5}/undo")
check(tk("t3")["done"] == 0 and tk("t4")["dismissed"] == 0 and {st(f3), st(f4), st(f5)} == {"pending"}, "undo reverses done, drop and not-now")
check(cl.post(f"/api/followups/{f3}/undo").get_json()["status"] == "pending", "undo on something already pending is harmless")

# ── a drop proposal
n3 = note("Old note with a stale chore"); task(n3, "Sort the old photos", "t6")
f6 = fu(n3, "Sort the old photos", "drop", None)
r = cl.post(f"/api/followups/{f6}/accept").get_json()
check(r["move"] == "drop" and tk("t6")["dismissed"] == 1 and st(f6) == "accepted" and not rems(n3), "accepting a drop dismisses the task and makes no reminder")
cl.post(f"/api/followups/{f6}/undo")
check(tk("t6")["dismissed"] == 0 and st(f6) == "pending", "undoing a drop restores the task")

# ── the task changed under the card
n4 = note("Note whose task gets finished elsewhere"); task(n4, "Renew the licence", "t7")
f7 = fu(n4, "Renew the licence"); f8 = fu(n4, "Renew the licence")
db.execute("UPDATE action_items SET done=1 WHERE id='t7'"); db.commit()
check(f7 not in [i["id"] for i in cl.get("/api/followups").get_json()["items"]] and st(f7) == "expired", "a task finished elsewhere vanishes from the card")
r = cl.post(f"/api/followups/{f8}/accept")
check(r.status_code == 409 and r.get_json()["error"] == "gone" and st(f8) == "expired" and not rems(n4), "acting on a stale card says so and changes nothing")
check(cl.post("/api/followups/99999/accept").status_code == 404, "unknown id is a 404")

# ── re-enrichment replaces task rows; the proposal survives
n5 = note("Note that gets re-enriched"); task(n5, "Book the dentist", "t8")
f9 = fu(n5, "Book the dentist")
db.execute("DELETE FROM action_items WHERE note_id=?", (n5,)); task(n5, "Book the dentist.", "t9")      # new id, trailing full stop
check(f9 in [i["id"] for i in cl.get("/api/followups").get_json()["items"]], "a re-created task row keeps its proposal alive")
cl.post(f"/api/followups/{f9}/done")
check(tk("t9")["done"] == 1, "…and 'done' finds the new row")

# ── refresh + preference
cl.post("/api/followups/refresh")
check(db.execute("SELECT value FROM settings WHERE key='followups_refresh_requested'").fetchone()[0] == "1", "Check now asks the agent to run")
check(cl.get("/api/settings").get_json()["prefs"]["followups"] == "on", "follow-ups default to on")
check(cl.post("/api/settings", json={"followups": "off"}).get_json()["prefs"]["followups"] == "off", "follow-ups can be turned off")
check(cl.post("/api/settings", json={"followups": "banana"}).get_json()["prefs"]["followups"] == "off", "nonsense is ignored")
check(cl.get("/api/version").get_json()["api"] >= 4, "the app level is bumped so an old server is flagged")

t = P + F
print(f"\n  {'\033[32m' if not F else '\033[31m'}{P}/{t} passed{'  all green ✨' if not F else '  FAILED'}\033[0m")
sys.exit(1 if F else 0)
