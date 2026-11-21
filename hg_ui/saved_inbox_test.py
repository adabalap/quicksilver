#!/usr/bin/env python3
"""The saved-from-Medha inbox: every choice creates exactly what it says.

The agent hands over a capture verbatim and stops. These checks are about the
person's choice being HONOURED, which is more than being recorded:

  - each of note / checklist / reminder / task creates that thing and nothing else
  - the body arrives untouched, and the note is marked so the agent cannot
    polish it later (meta_only)
  - choosing "note" stops the agent pulling tasks out of it afterwards
  - user_title is set from what the person saw and could edit
  - a choice can be undone, and undo takes the reminder or task with it
  - nothing can be decided twice

    python saved_inbox_test.py
"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from e2e_test import load_app

P = F = 0
def check(c, m, d=""):
    global P, F
    if c: P += 1; print(f"  \033[32m✓\033[0m {m}")
    else: F += 1; print(f"  \033[31m✗ {m}\033[0m" + (f"  → {d}" if d else ""))
def section(t): print(f"\n\033[33m▸ {t}\033[0m")

qs = load_app(os.path.join(tempfile.mkdtemp(), "t.db"))
cl = qs.app.test_client()
import sqlite3
db = sqlite3.connect(os.environ["QS_DB"]); db.row_factory = sqlite3.Row

LIST = "Onion\nAmla komma\nBanana 1/2 dozen\nFlowers\nattukulu\nBellam"


def seed(fp, title="కళ్యాణి", body=LIST, origin="WhatsApp"):
    db.execute("INSERT INTO saved_captures (fp, origin, title, body, posted_at, saved_at) "
               "VALUES (?,?,?,?,?,?)", (fp, origin, title, body, 1_700_000_000_000, 1_700_000_900_000))
    db.commit()


def decide(fp, **body):
    r = cl.post(f"/api/saved/{fp}/decide", json=body)
    return r.status_code, r.get_json()


def note(nid):
    return db.execute("SELECT * FROM notes WHERE id=?", (nid,)).fetchone()


def waiting():
    return cl.get("/api/saved").get_json()


section("The inbox")
check(waiting() == {"items": [], "count": 0}, "an empty inbox is empty, not an error")
seed("fp_note"); seed("fp_check"); seed("fp_rem"); seed("fp_task"); seed("fp_drop")
w = waiting()
check(w["count"] == 5, "every save is waiting", str(w["count"]))
it = next(i for i in w["items"] if i["fp"] == "fp_note")
check(it["title"] == "కళ్యాణి" and it["body"] == LIST and it["origin"] == "WhatsApp",
      "each one shows exactly what arrived")
check(it["lines"] == 6, "and how many lines it has, so the app can suggest a checklist", str(it["lines"]))

section("note")
code, j = decide("fp_note", **{"as": "note"})
check(code == 200 and j["note_id"], "choosing note creates one", str(j))
n = note(j["note_id"])
check(n["body"] == LIST, "the body is exactly the notification", repr(n["body"]))
check(n["user_title"] == "కళ్యాణి", "the title is the one the person saw")
check(n["note_type"] == "text", "it is a plain note")
check(n["meta_only"] == 1, "the agent may tag it but not rewrite it — the verbatim choice")
check(n["no_actions"] == 1 and n["no_reminders"] == 1,
      "and may not pull tasks or reminders out of it — that would be deciding again")
check(db.execute("SELECT COUNT(*) FROM action_items WHERE note_id=?", (n["id"],)).fetchone()[0] == 0,
      "no task was created")
check(db.execute("SELECT status FROM note_state WHERE note_id=?", (n["id"],)).fetchone()[0] == "raw",
      "it enters the normal pipeline, so it is indexed and searchable")
check(waiting()["count"] == 4, "and it leaves the inbox")

section("checklist")
code, j = decide("fp_check", **{"as": "checklist", "title": "Groceries"})
n = note(j["note_id"])
check(n["note_type"] == "checklist", "choosing checklist makes a checklist")
check(n["body"].split("\n") == [f"- [ ] {x}" for x in LIST.split("\n")],
      "one open item per line, in order", repr(n["body"]))
check(n["user_title"] == "Groceries", "an edited title is the one used")
check(n["no_actions"] == 1, "a checklist's items are not extracted again as tasks")
seed("fp_bullets", body="- Onion\n* [x] Ghee\n\n  [ ] Bellam  \n")
code, j = decide("fp_bullets", **{"as": "checklist"})
check(note(j["note_id"])["body"] == "- [ ] Onion\n- [ ] Ghee\n- [ ] Bellam",
      "existing bullets and boxes are not carried in as text — the same rule as pasting a list",
      repr(note(j["note_id"])["body"]))

section("reminder")
code, j = decide("fp_rem", **{"as": "reminder"})
check(code == 400 and j["error"] == "bad_date", "a reminder needs a day", str(j))
check(waiting()["count"] >= 2 and any(i["fp"] == "fp_rem" for i in waiting()["items"]),
      "and refusing it leaves the capture in the inbox")
code, j = decide("fp_rem", **{"as": "reminder", "date": "2026-13-45"})
check(code == 400, "an impossible day is refused too")
code, j = decide("fp_rem", **{"as": "reminder", "date": "2026-11-28", "title": "Groceries for Kalyani"})
check(code == 200 and j["date"] == "2026-11-28", "with a day, it is made", str(j))
n = note(j["note_id"])
check(n["note_type"] == "event", "as an event note, which the agent approves without review")
rem = db.execute("SELECT * FROM reminders WHERE note_id=?", (n["id"],)).fetchone()
check(rem and rem["datetime_resolved"] == "2026-11-28" and rem["datetime_hint"] == "2026-11-28",
      "the reminder lands on the chosen day", str(dict(rem) if rem else None))
check(rem and rem["title"] == "Groceries for Kalyani" and rem["source"] == "user",
      "titled as the person wrote it, and marked as theirs")
check(n["body"] == LIST, "the note keeps the full text")

section("task")
code, j = decide("fp_task", **{"as": "task", "title": "   "})
check(code == 400 and j["error"] == "no_title", "a task needs a name", str(j))
code, j = decide("fp_task", **{"as": "task", "title": "Buy groceries for Kalyani"})
n = note(j["note_id"])
tasks = db.execute("SELECT text, done FROM action_items WHERE note_id=?", (n["id"],)).fetchall()
check([tuple(t) for t in tasks] == [("Buy groceries for Kalyani", 0)], "exactly one open task, named", str([tuple(t) for t in tasks]))
check(n["no_reminders"] == 1 and n["no_actions"] == 0,
      "the agent may still find other tasks in it, but not reminders")

section("discard")
before = db.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
code, j = decide("fp_drop", **{"as": "discard"})
check(code == 200 and j["note_id"] is None, "discarding creates nothing", str(j))
check(db.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == before, "not even an empty note")
check(db.execute("SELECT decision FROM saved_captures WHERE fp='fp_drop'").fetchone()[0] == "discarded",
      "and it is recorded as discarded, not deleted")
check(not any(i["fp"] == "fp_drop" for i in waiting()["items"]), "and it leaves the inbox")

section("Nothing is decided twice")
code, j = decide("fp_note", **{"as": "checklist"})
check(code == 409 and j["error"] == "decided", "a second choice is refused", str(j))
check(j.get("decision") == "note", "and says what the first one was")
code, j = decide("no-such-fp", **{"as": "note"})
check(code == 404, "an unknown save is 404")
seed("fp_bad")
code, j = decide("fp_bad", **{"as": "spreadsheet"})
check(code == 400 and j["error"] == "bad_kind", "an unknown kind is refused, not guessed at")

section("Polish is on request, and the original is never touched")
# "Keep it verbatim" means the SOURCE is never altered and the agent does not
# rewrite the note on its own. It does not mean the note can never be
# polished: the person can ask, and that request must get through.
seed("fp_pol")
code, j = decide("fp_pol", **{"as": "note"})
pid = j["note_id"]
r = cl.post(f"/api/notes/{pid}/polish")
check(r.status_code == 200, "asking to polish a note made from a save is accepted", str(r.status_code))
flag = db.execute("SELECT value FROM settings WHERE key=?", (f"polish_once:{pid}",)).fetchone()
check(flag and flag[0] == "1", "…and the one-shot request is queued for the agent, which lets it past meta_only")
check(note(pid)["meta_only"] == 1, "…while the automatic pass stays switched off")
db.execute("UPDATE notes SET body=? WHERE id=?", ("Polished: onions, amla, bananas…", pid)); db.commit()
src = db.execute("SELECT body, title FROM saved_captures WHERE fp='fp_pol'").fetchone()
check(src["body"] == LIST and src["title"] == "కళ్యాణి",
      "rewriting the note leaves the original capture exactly as it arrived")

section("A capture with a title and no body")
seed("fp_bare", title="Pick up the dry cleaning", body="")
code, j = decide("fp_bare", **{"as": "note"})
n = note(j["note_id"])
check(n["body"] == "Pick up the dry cleaning", "the words become the body, so the note is not empty")
check(n["user_title"] == "", "and are not repeated as a title over them")
seed("fp_bare2", title="Pick up the dry cleaning", body="")
code, j = decide("fp_bare2", **{"as": "checklist"})
check(note(j["note_id"])["body"] == "- [ ] Pick up the dry cleaning", "as a checklist it becomes one item")

section("Undo")
rem_note = db.execute("SELECT note_id FROM saved_captures WHERE fp='fp_rem'").fetchone()[0]
r = cl.post("/api/saved/fp_rem/undo").get_json()
check(r["ok"], "a choice can be undone")
check(note(rem_note) is None, "the note it made is gone")
check(db.execute("SELECT COUNT(*) FROM reminders WHERE note_id=?", (rem_note,)).fetchone()[0] == 0,
      "and the reminder went with it — undo does not leave a reminder for a deleted note")
check(any(i["fp"] == "fp_rem" for i in waiting()["items"]), "the capture is back in the inbox")
row = db.execute("SELECT decision, note_id, decided_at FROM saved_captures WHERE fp='fp_rem'").fetchone()
check(tuple(row) == (None, None, None), "with no trace of the undone choice", str(tuple(row)))
code, j = decide("fp_rem", **{"as": "note"})
check(code == 200, "and it can be decided again, differently")
task_note = db.execute("SELECT note_id FROM saved_captures WHERE fp='fp_task'").fetchone()[0]
cl.post("/api/saved/fp_task/undo")
check(db.execute("SELECT COUNT(*) FROM action_items WHERE note_id=?", (task_note,)).fetchone()[0] == 0,
      "undoing a task takes the task with it")
r = cl.post("/api/saved/fp_task/undo").get_json()
check(r.get("already") is True, "undoing twice is harmless")
r = cl.post("/api/saved/fp_drop/undo").get_json()
check(r["ok"] and any(i["fp"] == "fp_drop" for i in waiting()["items"]),
      "a discard can be undone too")

section("Before the table exists")
qs2 = load_app(os.path.join(tempfile.mkdtemp(), "t2.db"))
c2 = qs2.app.test_client()
d2 = sqlite3.connect(os.environ["QS_DB"]); d2.execute("DROP TABLE saved_captures"); d2.commit()
check(c2.get("/api/saved").get_json() == {"items": [], "count": 0}, "the inbox reads as empty, not a 500")
# The dashboard's guard is an `except` that only runs in exactly this state,
# so a NameError inside it passes every test on a normal database.
r = c2.get("/api/pulse")
check(r.status_code == 200 and (r.get_json() or {}).get("digest", {}).get("saved_waiting") == 0,
      "the dashboard still opens, and counts nothing waiting", str(r.status_code))
r = c2.post("/api/saved/x/decide", json={"as": "note"})
check(r.status_code == 409, "a decision explains that the app needs restarting", str(r.status_code))

print(f"\n  {'\033[32m' if not F else '\033[31m'}{P}/{P+F} passed{'  all green ✨' if not F else '  FAILED'}\033[0m")
sys.exit(1 if F else 0)
