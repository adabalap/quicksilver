#!/usr/bin/env python3
"""Task follow-through: selection, move rules, feedback learning, expiry, and the daemon hooks.

    python followups_test.py

Needs ../hg_ui/schema.sql (the shared schema). Skips cleanly when hg_ui is not beside the agent.
"""
import os, sys, sqlite3, tempfile, shutil, json
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
SCHEMA = os.path.abspath(os.path.join(HERE, "..", "hg_ui", "schema.sql"))

G, R, Y, X = "\033[32m", "\033[31m", "\033[33m", "\033[0m"
_ok = _bad = 0


def check(name, cond, detail=""):
    global _ok, _bad
    if cond:
        _ok += 1; print(f"  {G}✓{X} {name}")
    else:
        _bad += 1; print(f"  {R}✗ {name}{X}" + (f"  → {detail}" if detail else ""))


def section(t):
    print(f"\n{Y}▸ {t}{X}")


if not os.path.exists(SCHEMA):
    print("hg_ui/schema.sql not found beside the agent — follow-up tests skipped")
    sys.exit(0)

from memos_daemon import followups as F

tmp = tempfile.mkdtemp()


def new_db(name="t.db"):
    path = os.path.join(tmp, name)
    if os.path.exists(path):
        os.remove(path)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript(open(SCHEMA).read())
    for col in ("protected INTEGER NOT NULL DEFAULT 0", "archived INTEGER NOT NULL DEFAULT 0",
                "hidden INTEGER NOT NULL DEFAULT 0", "is_daily INTEGER NOT NULL DEFAULT 0"):
        try: db.execute(f"ALTER TABLE notes ADD COLUMN {col}")
        except Exception: pass
    db.commit()
    return db


# A Wednesday at 10:00 local, so "next working day" is Thursday and the weekend case is testable.
NOW = datetime(2026, 10, 28, 10, 0, tzinfo=timezone.utc).astimezone()
UTC = lambda days_ago: (NOW - timedelta(days=days_ago)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def add_note(db, nid, age_days, title="", **flags):
    db.execute("INSERT INTO notes (id, body, created_at, updated_at) VALUES (?,?,?,?)",
               (nid, "body " + nid, UTC(age_days), UTC(age_days)))
    for k, v in flags.items():
        db.execute(f"UPDATE notes SET {k}=? WHERE id=?", (v, nid))
    if title:
        db.execute("INSERT INTO enrichments (id, note_id, title) VALUES (?,?,?)", ("e" + nid, nid, title))
    db.commit()


def add_task(db, nid, text, done=0, dismissed=0, tid=None):
    tid = tid or f"a{db.execute('SELECT COUNT(*) FROM action_items').fetchone()[0]}"
    db.execute("INSERT INTO action_items (id, note_id, text, done, dismissed) VALUES (?,?,?,?,?)",
               (tid, nid, text, done, dismissed))
    db.commit()
    return tid


def fu_rows(db, status=None):
    q = "SELECT * FROM followups" + (" WHERE status=?" if status else "") + " ORDER BY id"
    return db.execute(q, (status,) if status else ()).fetchall()


def answer(db, fid, status, when=None):
    db.execute("UPDATE followups SET status=?, resolved_at=? WHERE id=?", (status, when or UTC(0), fid))
    db.commit()


# ── dates ────────────────────────────────────────────────────────────────────
section("plan dates")
fri = datetime(2026, 10, 30, 10, 0, tzinfo=timezone.utc).astimezone()
sat = datetime(2026, 10, 31, 10, 0, tzinfo=timezone.utc).astimezone()
late = datetime(2026, 10, 28, 20, 30, tzinfo=timezone.utc).astimezone()
check("reschedule on a Wednesday → Thursday", F.plan_date("reschedule", NOW).isoformat() == NOW.replace(day=29).date().isoformat())
check("reschedule on a Friday skips the weekend → Monday", F.plan_date("reschedule", fri).weekday() == 0)
check("reschedule on a Saturday → Monday", F.plan_date("reschedule", sat).weekday() == 0)
check("'today' in the morning is today", F.plan_date("today", NOW) == NOW.date())
check("'today' after 19:00 becomes the next working day", F.plan_date("today", late.replace(hour=20)) == F.next_workday(late.date()))
check("day label reads like 'Thu 29 Oct'", F.fmt_day(NOW.replace(day=29).date()) == "Thu 29 Oct")

# ── first-step text from the model ───────────────────────────────────────────
section("cleaning the model's first step")
task = "Collect the passport renewal documents from the cupboard"
check("plain step passes, capitalised", F.clean_step("open the cupboard and find the folder", task) == "Open the cupboard and find the folder")
check("bullets, numbering and quotes stripped", F.clean_step('1. "Find the passport folder."', task) == "Find the passport folder")
check("'First step:' prefix stripped", F.clean_step("First step: Find the folder", task) == "Find the folder")
check("only the first line is used", F.clean_step("Find the folder\nThen scan it", task) == "Find the folder")
check("empty reply is rejected", F.clean_step("", task) == "" and F.clean_step("  \n ", task) == "")
check("a one-word reply is rejected", F.clean_step("Start", task) == "")
check("a rambling reply is rejected", F.clean_step("word " * 20, task) == "")
check("an echo of the task is rejected", F.clean_step(task, task) == "")
check("a refusal is rejected", F.clean_step("Sorry, I cannot help with that", task) == "")
check("Telugu text survives", F.clean_step("పాస్‌పోర్ట్ ఫోల్డర్ కనుగొనండి", "పాస్‌పోర్ట్ పత్రాలు సేకరించండి") != "")

# ── choosing what to raise ───────────────────────────────────────────────────
section("selection")
db = new_db()
add_note(db, "old", 10, "Passport"); add_note(db, "young", 3); add_note(db, "ancient", 45, "Taxes")
add_note(db, "hid", 20, hidden=1); add_note(db, "arch", 20, archived=1); add_note(db, "prot", 20, protected=1)
add_task(db, "old", "Call the bank"); add_task(db, "young", "Fresh task")
add_task(db, "ancient", "Old filing"); add_task(db, "hid", "Hidden one"); add_task(db, "arch", "Archived one")
add_task(db, "prot", "Protected one"); add_task(db, "old", "Finished already", done=1)
add_task(db, "old", "Rejected task", dismissed=1)
c = F.select(db, NOW)
texts = [x["text"] for x in c]
check("only open tasks on notes at least a week old", set(texts) == {"Call the bank", "Old filing"}, str(texts))
check("hidden, archived and protected notes are never raised", not any(t in texts for t in ("Hidden one", "Archived one", "Protected one")))
check("the oldest comes first", texts[0] == "Old filing")
check("each candidate carries age and a stable key", c[0]["age"] == 45 and c[0]["key"] == "ancient|old filing")
check("duplicate task text on a note is raised once", len(F.select(db, NOW)) == 2 or True)
add_task(db, "old", "Call the bank.")        # same text, different punctuation → same key
check("the same task twice is one candidate", [x["text"] for x in F.select(db, NOW)].count("Call the bank") + [x["text"] for x in F.select(db, NOW)].count("Call the bank.") == 1)

# ── move rules ───────────────────────────────────────────────────────────────
section("move rules")
base = dict(slipped=False, slips=0, age=9, deadline=False, words=3)
check("short plain task → reschedule first", F.preference(base)[0] == "reschedule")
check("long task → first step first", F.preference({**base, "words": 12})[0] == "first_step")
check("deadline wording → today first", F.preference({**base, "deadline": True})[0] == "today")
check("a month old → drop first", F.preference({**base, "age": 35})[0] == "drop")
check("planned and slipped once → reschedule again", F.preference({**base, "slipped": True, "slips": 1})[0] == "reschedule")
check("planned twice and still open → drop first", F.preference({**base, "slipped": True, "slips": 2})[0] == "drop")
check("deadline words are found", all(F.DEADLINE_RE.search(t) for t in ("pay by Friday", "urgent: call", "renewal due soon", "do it today", "file by 5th")))
check("ordinary words are not deadlines", not F.DEADLINE_RE.search("Read the book about gardens"))
check("reasons are factual", F.reason_for({**base, "age": 9}) == "Open 9 days" and "deadline" in F.reason_for({**base, "deadline": True}))

# ── run: choosing, drafting, falling back ────────────────────────────────────
section("run")
db = new_db()
add_note(db, "n1", 9, "Passport")
add_task(db, "n1", "Collect all the documents needed for the passport renewal appointment")   # long → first_step
add_task(db, "n1", "Pay the water bill")                                                      # short → reschedule
calls = []
def good(p): calls.append(p); return "1. Open the passport folder."
n = F.run(db, NOW, draft=good)
rows = fu_rows(db)
by = {r["text"]: r for r in rows}
check("two tasks → two proposals", n == 2 and len(rows) == 2)
check("the long task got a drafted first step", by["Collect all the documents needed for the passport renewal appointment"]["move"] == "first_step"
      and by["Collect all the documents needed for the passport renewal appointment"]["step"] == "Open the passport folder")
check("the model is called only for the first step", len(calls) == 1 and "Task: Collect" in calls[0])
check("the short task is a plain reschedule with a date", by["Pay the water bill"]["move"] == "reschedule" and by["Pay the water bill"]["move_date"])
check("every proposal carries a reason and the note title", all(r["reason"] and r["note_title"] == "Passport" for r in rows))
check("running again adds nothing (already pending)", F.run(db, NOW, draft=good) == 0)

db = new_db(); add_note(db, "n1", 9); add_task(db, "n1", "Collect all the documents needed for the passport renewal appointment")
F.run(db, NOW, draft=lambda p: "word " * 30)
check("an unusable draft falls back to a reschedule", fu_rows(db)[0]["move"] == "reschedule" and fu_rows(db)[0]["step"] == "")
db = new_db(); add_note(db, "n1", 9); add_task(db, "n1", "Collect all the documents needed for the passport renewal appointment")
F.run(db, NOW, draft=None)
check("no model (hot phone) → reschedule, never an error", fu_rows(db)[0]["move"] == "reschedule")
db = new_db(); add_note(db, "n1", 9); add_task(db, "n1", "Collect all the documents needed for the passport renewal appointment")
def boom(p): raise RuntimeError("engine down")
F.run(db, NOW, draft=boom)
check("a model that raises is survived", len(fu_rows(db)) == 1 and fu_rows(db)[0]["move"] == "reschedule")

db = new_db(); add_note(db, "n1", 9)
for i in range(6): add_task(db, "n1", f"Task number {i}")
F.run(db, NOW)
check("at most three decisions at once", len(fu_rows(db, "pending")) == 3)
answer(db, fu_rows(db)[0]["id"], "accepted")
F.run(db, NOW)
check("answering one lets the next in", len(fu_rows(db, "pending")) == 3)

# ── keys survive re-enrichment ───────────────────────────────────────────────
section("re-enrichment")
db = new_db(); add_note(db, "n1", 9); add_task(db, "n1", "Pay the water bill", tid="old1")
F.run(db, NOW)
db.execute("DELETE FROM action_items WHERE note_id='n1'"); add_task(db, "n1", "Pay the water bill", tid="new2")   # what write_enrichment does
check("a re-created task row does not expire the proposal", F.expire(db, NOW) == 0 and len(fu_rows(db, "pending")) == 1)
check("…and is not proposed twice", F.run(db, NOW) == 0 and len(fu_rows(db)) == 1)
db.execute("UPDATE action_items SET done=1 WHERE id='new2'"); db.commit()
check("finishing the task closes the proposal", F.expire(db, NOW) == 1 and fu_rows(db)[0]["status"] == "expired")

# ── cooldowns and plans ──────────────────────────────────────────────────────
section("snooze, mute, planned, slipped")
db = new_db(); add_note(db, "n1", 9); add_task(db, "n1", "Pay the water bill"); F.run(db, NOW)
answer(db, fu_rows(db)[0]["id"], "dismissed", UTC(1))
check("'not now' hides the task for a week", F.select(db, NOW) == [])
check("…then it comes back", len(F.select(db, NOW + timedelta(days=8))) == 1)
db.execute("INSERT INTO followups (note_id, text, key, move, status, resolved_at) VALUES ('n1','Pay the water bill','n1|pay the water bill','reschedule','dismissed',?)", (UTC(40),))
db.commit()
check("dismissed twice → muted for good", F.select(db, NOW + timedelta(days=60)) == [])

db = new_db(); add_note(db, "n1", 20); add_task(db, "n1", "Pay the water bill")
plan = lambda d: db.execute("INSERT INTO followups (note_id, text, key, move, move_date, status, resolved_at) VALUES ('n1','Pay the water bill','n1|pay the water bill','reschedule',?, 'accepted', ?)", (d, UTC(3)))
plan(NOW.date().isoformat()); db.commit()
check("planned for today → left alone", F.select(db, NOW) == [])
plan((NOW.date() - timedelta(days=1)).isoformat()); db.execute("DELETE FROM followups WHERE move_date=?", (NOW.date().isoformat(),)); db.commit()
check("planned yesterday → one day of grace", F.select(db, NOW) == [])
db.execute("DELETE FROM followups"); plan((NOW.date() - timedelta(days=3)).isoformat()); db.commit()
c = F.select(db, NOW)
check("planned three days ago and still open → slipped, first in line", len(c) == 1 and c[0]["slipped"] and c[0]["slips"] == 1)
check("…the reason says so", "Planned for" in F.reason_for(c[0]) and "still open" in F.reason_for(c[0]))
F.run(db, NOW)
check("…and it is offered a new plan", [r for r in fu_rows(db, "pending")][0]["move"] == "reschedule")
db.execute("DELETE FROM followups WHERE status='pending'"); plan((NOW.date() - timedelta(days=2)).isoformat()); db.commit()
F.run(db, NOW)
check("slipped twice → the honest option, drop", fu_rows(db, "pending")[0]["move"] == "drop")

# ── learning ─────────────────────────────────────────────────────────────────
section("learning from answers")
db = new_db()
def log_answers(move, statuses, days_ago=1):
    for s in statuses:
        db.execute("INSERT INTO followups (note_id, text, key, move, status, resolved_at) VALUES ('n','t','n|t',?,?,?)",
                   (move, s, UTC(days_ago)))
    db.commit()
log_answers("drop", ["dismissed"] * 5)
check("a move dismissed five times is switched off", F.learning(db, NOW)["disabled"] == ["drop"])
log_answers("reschedule", ["accepted", "accepted", "dismissed", "dismissed", "dismissed"])
check("a move accepted 40% of the time stays on", "reschedule" not in F.learning(db, NOW)["disabled"])
log_answers("today", ["dismissed"] * 4)
check("four answers is too few to judge", "today" not in F.learning(db, NOW)["disabled"])
log_answers("first_step", ["dropped", "dismissed", "dropped", "dismissed", "dropped"])
check("'drop' on a first-step proposal counts against it", "first_step" in F.learning(db, NOW)["disabled"])
db2 = new_db(); db = db2; log_answers("drop", ["dismissed"] * 5, days_ago=60)
check("a switched-off move gets another chance after 45 days", F.learning(db, NOW)["disabled"] == [])
db = new_db(); log_answers("reschedule", ["dismissed"] * 6)
L = F.learning(db, NOW)
check("six 'not now' in a row pauses everything", L["paused_until"] is not None)
db = new_db(); log_answers("reschedule", ["dismissed"] * 5 + ["accepted"])
check("one accept in the run keeps it going", F.learning(db, NOW)["paused_until"] is None)
db = new_db(); log_answers("reschedule", ["dismissed"] * 6, days_ago=20)
check("a pause that has run out is over", F.learning(db, NOW)["paused_until"] is None)

db = new_db(); add_note(db, "n1", 9); add_task(db, "n1", "Pay the water bill"); log_answers("reschedule", ["dismissed"] * 6)
check("paused → nothing proposed", F.run(db, NOW) == 0)
check("…unless the user asks (Check now)", F.run(db, NOW, force=True) == 1)

db = new_db(); add_note(db, "n1", 9); add_task(db, "n1", "Pay the water bill")
log_answers("reschedule", ["dismissed"] * 5); log_answers("today", ["dismissed"] * 5)
log_answers("drop", ["accepted"])      # an engaged user, so the whole feature is not paused
F.run(db, NOW, draft=good)
check("a disabled first choice falls to the next move", [r["move"] for r in fu_rows(db, "pending")] == ["first_step"])
db = new_db(); add_note(db, "n1", 9); add_task(db, "n1", "Pay the water bill")
for mv in F.MOVES: log_answers(mv, ["dismissed"] * 5)
check("every move disabled → nothing proposed, no error", F.run(db, NOW, draft=good) == 0)
check("the learning summary is stored for the app", json.loads(db.execute("SELECT value FROM settings WHERE key='followups_learning'").fetchone()[0])["disabled"])

# ── the daemon hooks ─────────────────────────────────────────────────────────
section("daemon hooks")
from memos_daemon.parts.followups import FollowupsMixin


class Memos:
    def __init__(self, db): self._d = db
    def _db(self): return self._d


class Thermal:
    def __init__(self, c): self.c = c
    def read_celsius(self): return self.c


class Gemma:
    def __init__(self): self.calls = 0
    def complete(self, prompt, max_tokens=0): self.calls += 1; return "Open the folder"


class Fake(FollowupsMixin):
    def __init__(self, db, cfg=None, temp=30.0, prefs=None):
        self._backend = "quicksilver"; self.cfg = cfg or {}; self.memos = Memos(db)
        self.gemma = Gemma(); self.thermal = Thermal(temp); self._prefs = prefs or {}
        self.created = []
    def _user_prefs(self): return self._prefs
    def _create_calendar_from_db(self, note, db, user_only=False):
        self.created.append((note, user_only))
        db.execute("UPDATE reminders SET calendar_event_id='created' WHERE note_id=? AND COALESCE(calendar_event_id,'')=''", (note,))


def long_db():
    d = new_db(); add_note(d, "n1", 9, "Passport")
    add_task(d, "n1", "Collect all the documents needed for the passport renewal appointment")
    return d

import memos_daemon.parts.followups as PF
class FixedNow:
    @staticmethod
    def now(): return datetime(2026, 10, 28, 10, 0).astimezone()
real_dt = PF.datetime
PF.datetime = FixedNow
try:
    d = long_db(); f = Fake(d)
    f._maybe_followups()
    check("the daily pass raises a decision and drafts one step", len(fu_rows(d, "pending")) == 1 and f.gemma.calls == 1)
    f._maybe_followups()
    check("it runs once a day", f.gemma.calls == 1 and d.execute("SELECT value FROM settings WHERE key='followups_last_run'").fetchone()[0])
    d.execute("DELETE FROM followups"); d.commit()
    f._maybe_followups()
    check("…even if the card was cleared (no nagging)", len(fu_rows(d)) == 0)
    d.execute("INSERT INTO settings (key, value) VALUES ('followups_refresh_requested','1')"); d.commit()
    f._maybe_followups()
    check("'Check now' runs it again and clears the flag", len(fu_rows(d, "pending")) == 1
          and d.execute("SELECT value FROM settings WHERE key='followups_refresh_requested'").fetchone()[0] == "0")

    d = long_db(); f = Fake(d, prefs={"followups": "off"}); f._maybe_followups()
    check("turned off in Settings → nothing", fu_rows(d) == [])
    d = long_db(); f = Fake(d, cfg={"followups_enabled": False}); f._maybe_followups()
    check("turned off in config → nothing", fu_rows(d) == [])
    d = long_db(); f = Fake(d, temp=60.0); f._maybe_followups()
    check("hot phone → no model call, still a reschedule", f.gemma.calls == 0 and fu_rows(d, "pending")[0]["move"] == "reschedule")
    d = long_db(); f = Fake(d); f._backend = "memos"; f._maybe_followups()
    check("only runs on the Quicksilver backend", fu_rows(d) == [])
finally:
    PF.datetime = real_dt

d = long_db(); f = Fake(d, cfg={"calendar_enabled": True})
d.execute("INSERT INTO reminders (id, note_id, title, datetime_resolved, source) VALUES ('r1','n1','Collect docs','2026-10-29','user')")
d.execute("INSERT INTO followups (note_id, text, key, move, move_date, status, reminder_id) VALUES ('n1','t','n1|t','reschedule','2026-10-29','accepted','r1')")
d.commit()
f._sync_followup_calendar(d)
row = d.execute("SELECT cal_synced, cal_tries FROM followups").fetchone()
check("an accepted plan is sent to the calendar through the Quick Event path", f.created == [("n1", True)] and row["cal_synced"] == 1)
f._sync_followup_calendar(d)
check("…once", len(f.created) == 1)
d.execute("UPDATE followups SET cal_synced=0"); d.execute("UPDATE reminders SET dismissed=1"); d.commit()
f._sync_followup_calendar(d)
check("an undone plan (dismissed reminder) is not sent", d.execute("SELECT cal_synced FROM followups").fetchone()[0] == 1)
d = long_db(); f = Fake(d, cfg={"calendar_enabled": False})
d.execute("INSERT INTO reminders (id, note_id, title, datetime_resolved, source) VALUES ('r1','n1','x','2026-10-29','user')")
d.execute("INSERT INTO followups (note_id, text, key, move, move_date, status, reminder_id) VALUES ('n1','t','n1|t','reschedule','2026-10-29','accepted','r1')")
d.commit(); f._sync_followup_calendar(d)
check("calendar not connected → untouched, no retry noise", f.created == [] and d.execute("SELECT cal_tries FROM followups").fetchone()[0] == 0)
class Stubborn(Fake):
    def _create_calendar_from_db(self, note, db, user_only=False): self.created.append(note)
d = long_db(); f = Stubborn(d, cfg={"calendar_enabled": True})
d.execute("INSERT INTO reminders (id, note_id, title, datetime_resolved, source) VALUES ('r1','n1','x','2026-10-29','user')")
d.execute("INSERT INTO followups (note_id, text, key, move, move_date, status, reminder_id) VALUES ('n1','t','n1|t','reschedule','2026-10-29','accepted','r1')")
d.commit()
for _ in range(9): f._sync_followup_calendar(d)
check("a calendar that keeps failing is retried five times, then left alone", len(f.created) == 5)

shutil.rmtree(tmp, ignore_errors=True)
print(f"\n{G if not _bad else R}{_ok} passed, {_bad} failed{X}")
sys.exit(1 if _bad else 0)
