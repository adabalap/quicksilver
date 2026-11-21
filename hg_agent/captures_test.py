#!/usr/bin/env python3
"""Medha's notification stream: the client walk, the store, the noise rule, corroboration, and the real daemon hook.

No real notification is used anywhere. The wordings below imitate what Teams,
Outlook, a bank app and Android itself actually post.

The checks that matter are the ones about LOSS, because this is the only
source with a cursor the server will not replay:

  - the cursor is saved only after the rows are committed, so a crash replays
    a page rather than losing it
  - a replayed page dedupes on Medha's row id
  - a page of nothing but noise still advances the cursor, or a phone posting
    progress bars would re-read the same page forever
  - `listening: false` is reported rather than read as a quiet phone

    python captures_test.py        (needs ../hg_ui beside the agent)
"""
import os, sys, json, sqlite3, tempfile, importlib.util, logging
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
G, R, Y, X = "\033[32m", "\033[31m", "\033[33m", "\033[0m"
_ok = _bad = 0


def check(name, cond, detail=""):
    global _ok, _bad
    if cond: _ok += 1; print(f"  {G}✓{X} {name}")
    else: _bad += 1; print(f"  {R}✗ {name}{X}" + (f"  → {detail}" if detail else ""))


def section(t): print(f"\n{Y}▸ {t}{X}")


from memos_daemon import captures as cap
from memos_daemon import medha_brief as mb

NOW = datetime(2026, 11, 19, 11, 0, 0)


def ms(dt):
    return int(dt.timestamp() * 1000)


def row(mid, title, text="", pkg="com.microsoft.teams", label="Teams", cat="", at=None):
    return {"id": mid, "at": ms(at or NOW), "source_pkg": pkg, "source_label": label,
            "category": cat, "title": title, "text": text}


# ───────────────────────────── the noise rule

section("What is worth keeping")
check("a meeting notice is kept",
      not cap.is_noise("com.microsoft.teams", "Teams", "", "Design review", "starts in 10 minutes"))
check("so is a bank bill",
      not cap.is_noise("com.icicibank.imobile", "iMobile", "", "Card statement", "Rs 14,250 due 25 Nov"))
check("a screenshot is not",
      cap.is_noise("com.android.systemui", "System UI", "", "Screenshot saved", "Tap to view"))
check("nor is a charging notice",
      cap.is_noise("com.android.systemui", "System UI", "", "Charging rapidly", ""))
check("nor a download",
      cap.is_noise("com.android.providers.downloads", "Downloads", "", "Download complete", "file.pdf"))
check("nor anything in the progress category",
      cap.is_noise("com.whatsapp", "WhatsApp", "progress", "Backing up", "40%"))
check("an empty notification is nothing at all",
      cap.is_noise("com.whatsapp", "WhatsApp", "", "", ""))
check("a bare unread count is not an obligation",
      cap.is_noise("com.google.android.gm", "Gmail", "", "", "3 new messages"))
# The rule removes the useless, NOT the sensitive: Medha's own filter has
# already dropped anything passcode-shaped, and a second copy of that rule
# here is a second copy that can disagree with it.
check("a message with real content from a chat app is kept",
      not cap.is_noise("com.whatsapp", "WhatsApp", "", "Amma", "Did you pay the water bill?"))
# The first version of the noise rule matched single generic words at the
# START of a title, so these were all dropped as device status. A keyword
# that generic cannot carry the decision.
for t in ("Battery project kickoff", "USB-C supplier review", "Media plan sign-off",
          "Casting call for the school play", "Charging station vendor quote",
          "Screenshot review with design", "Update from Priya", "Scanning team standup"):
    check(f"a real notification titled {t!r} is kept",
          not cap.is_noise("com.microsoft.teams", "Teams", "", t, "at 3pm"))
for t in ("Charging rapidly", "Battery saver is on", "Screenshot saved",
          "Download complete", "Sync complete", "Now playing", "Hotspot is on",
          "Notifications paused", "System update available", "Backing up your photos",
          "Scanning for devices", "Downloading…", "Syncing", "Updating…",
          "Screen recording", "USB debugging connected", "Casting your screen"):
    check(f"but device status titled {t!r} is not",
          cap.is_noise("com.android.vending", "Play", "", t, ""))

section("The same notification posted again")
a = cap.fingerprint("com.microsoft.teams", "Standup", "starts in 10 minutes")
b = cap.fingerprint("com.microsoft.teams", "Standup", "starts in 9 minutes")
check("a ticking countdown is one thing, not nine", a == b)
check("a different meeting is a different thing",
      a != cap.fingerprint("com.microsoft.teams", "Design review", "starts in 10 minutes"))
check("and the same words from another app are too",
      a != cap.fingerprint("us.zoom.videomeetings", "Standup", "starts in 10 minutes"))

# ───────────────────────────── the client walk

section("drain_captures walks the backlog")


class FakeClient:
    """Pages, with a scripted `more`. Records what was asked for."""

    def __init__(self, pages):
        self.pages, self.asked = list(pages), []

    def captures(self, since=0, limit=None):
        self.asked.append(since)
        return self.pages.pop(0) if self.pages else {"items": [], "cursor": since, "more": False}


c = FakeClient([
    {"items": [row(1, "a"), row(2, "b")], "cursor": 2, "more": True, "head": 9, "listening": True},
    {"items": [row(5, "c"), row(7, "d")], "cursor": 7, "more": True, "head": 9, "listening": True},
    {"items": [row(9, "e")], "cursor": 9, "more": False, "head": 9, "listening": True},
])
items, cursor, listening = mb.drain_captures(c, 0)
check("every row, once, in order", [i["id"] for i in items] == [1, 2, 5, 7, 9], str(items))
check("the cursor lands at the head", cursor == 9)
check("each page resumes after the last", c.asked == [0, 2, 7], str(c.asked))
check("listening reported", listening is True)

c = FakeClient([{"items": [row(1, "a")], "cursor": 1, "more": True, "head": 99}] * 60)
items, cursor, _ = mb.drain_captures(c, 0, max_pages=5)
check("max_pages is a hard bound even if the server says 'more' forever", len(c.asked) == 5)
check("and it still returns what it got", len(items) == 5)

c = FakeClient([{"items": [], "cursor": 0, "more": False, "head": 0, "listening": True}])
items, cursor, _ = mb.drain_captures(c, 5000)
check("a cursor LOWER than ours is accepted: the log was deleted on revocation", cursor == 0)

c = FakeClient([{"items": [row(3, "x")], "cursor": "7", "more": True, "head": 90}])
items, cursor, _ = mb.drain_captures(c, 1)
check("a malformed cursor does not advance one", cursor == 1)
check("but the page's rows are still returned", [i["id"] for i in items] == [3])
c = FakeClient([{"items": [row(3, "x")], "cursor": True, "more": False, "head": 90}])
_, cursor, _ = mb.drain_captures(c, 1)
check("and a bool is not a cursor", cursor == 1)

section("protocol_of")
check("v0.20.0 advertises 2", mb.protocol_of({"briefProtocol": 2}) == 2)
check("an older Medha has no such field, which reads as 1", mb.protocol_of({}) == 1)
check("and None is not a reply", mb.protocol_of(None) == 1)
check("a bool is not a version", mb.protocol_of({"briefProtocol": True}) == 1)
check("nor a string", mb.protocol_of({"briefProtocol": "2"}) == 1)

# ───────────────────────────── the v2 shape

section("Obligations carry what a rendered row could not")
o = mb._obligation({"key": "rem-41", "kind": "event", "title": "Design review",
                    "text": "At 5:30 PM.", "dueAt": ms(NOW + timedelta(hours=6)),
                    "link": "https://hg.example/n/4312"})
check("the candidate key becomes the stable id", o.get("id") == "rem-41", str(o))
check("the date is DECLARED by default", (o.get("due") or {}).get("provenance") == "declared", str(o))
check("and links is a list, because v1 had room for one", o.get("links") == ["https://hg.example/n/4312"])
o2 = mb._obligation({"key": "cap-9", "kind": "note", "title": "Delayed employees",
                     "text": "mentioned 11:30", "dueAt": ms(NOW), "due_declared": False})
# The v0.19.1 failure, in one assertion: a date read out of someone else's
# prose is marked as a guess, and Medha will not promote a guess to the widget.
check("a capture-derived date says so", (o2.get("due") or {}).get("provenance") == "inferred", str(o2))
o3 = mb._obligation({"key": "bill-7", "kind": "bill", "title": "ICICI", "text": "paid",
                     "dueAt": ms(NOW), "state": "done"})
check("state passes through", o3.get("state") == "done")
check("an unknown state does not",
      "state" not in mb._obligation({"key": "x", "kind": "note", "title": "t", "text": "",
                                     "state": "snoozed"}))
check("no dueAt means no due block",
      "due" not in mb._obligation({"key": "x", "kind": "note", "title": "t", "text": ""}))
check("a long id is capped, not dropped",
      len(mb._obligation({"key": "k" * 400, "kind": "note", "title": "t", "text": ""})["id"]) == mb.MAX_ID)

section("committed — what we vouch for versus what we relay")
# Medha v0.21.0's primary test for the home screen. Everything this agent
# produces today is a bill, a reminder or a task the user created, so the
# default carries it and nothing had to change.
check("a bill, a reminder or a task is committed by default",
      "committed" not in mb._obligation({"key": "bill-1", "kind": "bill",
                                         "title": "ICICI card", "text": ""}))
# A capture-derived candidate is a third-party app's words relayed. Sending
# it as committed would make it look like the user's own list, which is the
# mistake that put four emails on the agenda.
check("a capture-derived candidate says it is not",
      mb._obligation({"key": "cap-9", "kind": "note", "title": "Delayed employees",
                      "text": "", "committed": False}).get("committed") is False)
# Both flags together, because a capture sets both and Medha needs both to
# keep it off the agenda for the right reason.
_relayed = mb._obligation({"key": "cap-9", "kind": "note", "title": "x", "text": "",
                           "dueAt": ms(NOW), "due_declared": False, "committed": False})
check("and carries an inferred date alongside it",
      _relayed.get("committed") is False and
      (_relayed.get("due") or {}).get("provenance") == "inferred", str(_relayed))

section("The pointer row keeps its tap on both protocols")
# It used to be built inline as a v1 dict. On v2 Medha reads `links` and
# ignores what it does not know, so the ONE row whose entire purpose is to be
# tapped would have lost its target, silently, with the post succeeding.
cands = [{"key": f"k{i}", "kind": "note", "title": f"t{i}", "text": "",
          "score": 100 - i} for i in range(10)]
v1 = mb.pick(cands, limit=3, app_base="https://hg.example")
v2 = mb.pick(cands, limit=3, app_base="https://hg.example", protocol=2)
check("v1 ends in a +N more row with a link",
      v1[-1]["title"].startswith("+") and v1[-1].get("link"), str(v1[-1]))
check("v2 ends in one with links[]",
      v2[-1]["title"].startswith("+") and v2[-1].get("links"), str(v2[-1]))
check("and the same count either way",
      v1[-1]["title"] == v2[-1]["title"], f"{v1[-1]['title']} vs {v2[-1]['title']}")
check("the real rows are obligations on v2", all("link" not in i for i in v2), str(v2))
# MAX_ITEMS is only pick()'s default, and brief_source always overrides it
# with medha_brief_max_items, so asserting its value tests nothing the daemon
# does. What matters is that the limit it is GIVEN is honoured.
check("a limit above the default is honoured", len(mb.pick(cands, limit=8)) == 8, str(len(mb.pick(cands, limit=8))))

# ───────────────────────────── on a real database

PWA = os.path.abspath(os.path.join(HERE, "..", "hg_ui", "app.py"))
if not os.path.exists(PWA):
    print("hg_ui not found beside the agent — database tests skipped")
    print(f"\n{_ok} passed, {_bad} failed"); sys.exit(1 if _bad else 0)

tmp = tempfile.mkdtemp()
dbp = os.path.join(tmp, "n.db")
os.environ["QS_DB"] = dbp
spec = importlib.util.spec_from_file_location("pwa_app", PWA)
pwa = importlib.util.module_from_spec(spec); sys.modules["pwa_app"] = pwa; spec.loader.exec_module(pwa)
pwa.init_db()
db = sqlite3.connect(dbp); db.row_factory = sqlite3.Row
cap.ensure(db); db.commit()

section("The store")
st = cap.store(db, [row(1, "Design review", "starts in 10 minutes"),
                    row(2, "Screenshot saved", "Tap to view", pkg="com.android.systemui"),
                    row(3, "Card statement", "Rs 14,250 due 25 Nov", pkg="com.icicibank.imobile")])
db.commit()
check("real observations are kept, noise is counted not kept",
      st["added"] == 2 and st["noise"] == 1 and st["seen"] == 3, str(st))
check("two rows on disk", db.execute("SELECT COUNT(*) FROM captures").fetchone()[0] == 2)

st = cap.store(db, [row(1, "Design review", "starts in 10 minutes"), row(4, "New thing")])
db.commit()
# The case this exists for: the cursor is saved after the rows, so a crash
# between the two replays a page. A replay must not duplicate.
check("a replayed page adds only what is new", st["added"] == 1, str(st))
check("still three rows", db.execute("SELECT COUNT(*) FROM captures").fetchone()[0] == 3)

st = cap.store(db, [{"id": "not an int", "title": "x"}, None, "nope", {"title": "no id"}])
db.commit()
check("rows without a usable id are skipped, not raised", st["skipped"] == 4 and st["added"] == 0, str(st))
check("an empty batch is fine", cap.store(db, [])["seen"] == 0)
check("and None is too", cap.store(db, None)["seen"] == 0)

section("Retention")
old = ms(NOW - timedelta(days=cap.RETAIN_DAYS + 2))
db.execute("INSERT INTO captures (medha_id, at, pkg, label, category, title, text) "
           "VALUES (900, ?, 'com.x', 'X', '', 'Ancient', '')", (old,))
db.commit()
n = cap.prune(db, NOW)
db.commit()
check("something past the window goes", n >= 1 and not db.execute(
    "SELECT 1 FROM captures WHERE medha_id=900").fetchone())
check("and what is inside it stays", db.execute("SELECT COUNT(*) FROM captures").fetchone()[0] == 3)

section("Corroboration — captures annotate, never add")
db.execute("DELETE FROM captures"); db.commit()
cap.store(db, [row(10, "Canceled: DACH Knowledge hour",
                   "This event has been cancelled by the organiser.")])
cap.store(db, [row(11, "Payment received",
                   "Thank you for your payment of Rs 14,250 towards your ICICI card.",
                   pkg="com.icicibank.imobile", label="iMobile")])
db.commit()
cands = [
    {"key": "rem-1", "kind": "event", "title": "DACH Knowledge hour", "text": "At 9:30 PM.", "score": 700},
    {"key": "bill-7", "kind": "bill", "title": "ICICI card", "text": "Card bill due Tue 25 Nov.", "score": 800},
    {"key": "rem-2", "kind": "event", "title": "Milestone Program", "text": "At 5:30 PM.", "score": 700},
]
n = cap.corroborate(db, cands, NOW)
by_key = {c["key"]: c for c in cands}
# The exact row from the screenshots: a cancellation drawn at the cancelled
# meeting's hour. Medha could only read that off a subject line before, which
# worked when the cancellation WAS the row and not when it was a separate one.
check("a cancellation notice settles the meeting we were posting",
      by_key["rem-1"].get("state") == "cancelled", str(by_key["rem-1"]))
check("a payment notice settles the bill", by_key["bill-7"].get("state") == "done", str(by_key["bill-7"]))
check("an unrelated meeting is untouched", "state" not in by_key["rem-2"], str(by_key["rem-2"]))
check("and it says how many it changed", n == 2, str(n))
check("nothing was ADDED -- the candidate list is the same length", len(cands) == 3)

# Only ever demotes, on both sides: Medha enforces the same rule, so a false
# match costs one tap (the row is on the full brief) and a false negative
# costs nothing at all.
cands2 = [{"key": "rem-1", "kind": "event", "title": "DACH Knowledge hour", "text": "x",
           "score": 700, "state": "open"}]
cap.corroborate(db, cands2, NOW)
check("a state the source already set is never overridden", cands2[0]["state"] == "open")
check("an empty candidate list is a no-op", cap.corroborate(db, [], NOW) == 0)
db.execute("DELETE FROM captures"); db.commit()
check("no captures means no annotations", cap.corroborate(db, list(cands), NOW) == 0)

section("A window, because a cancellation is news")
cap.store(db, [row(20, "Cancelled: Quarterly review", "called off",
                   at=NOW - timedelta(days=10))])
db.commit()
stale = [{"key": "rem-9", "kind": "event", "title": "Quarterly review", "text": "x", "score": 700}]
check("a fortnight-old notice is not believed about something still on the list",
      cap.corroborate(db, stale, NOW, window_h=72) == 0, str(stale))
check("but it is inside a wide enough window",
      cap.corroborate(db, stale, NOW, window_h=24 * 30) == 1, str(stale))

# ───────────────────────────── the real daemon hook

section("Daemon hook")
from memos_daemon.config import DEFAULT_CONFIG
from memos_daemon.daemon import MemosDaemon

dcfg = dict(DEFAULT_CONFIG)
dcfg.update(db_path=dbp, backend="quicksilver", state_file=os.path.join(tmp, "state.json"),
            pending_writes_file=os.path.join(tmp, "pending"), todo_file=os.path.join(tmp, "todos.json"),
            thermal_enabled=False, embed_model="", engine="auto", model_path="", calendar_enabled=False,
            dashboard_enabled=False, connections_enabled=False, todo_enabled=False,
            medha_url="http://127.0.0.1:8001", medha_token="TOK")
logging.disable(logging.CRITICAL)
d = MemosDaemon(dcfg)
logging.disable(logging.NOTSET)
d.gemma = None

served = {"n": 0, "pages": None, "listening": True}


class StubClient:
    def captures(self, since=0, limit=None):
        served["n"] += 1
        if served["pages"] is not None:
            return served["pages"].pop(0) if served["pages"] else {
                "items": [], "cursor": since, "more": False, "listening": served["listening"]}
        return {"items": [], "cursor": since, "more": False, "listening": served["listening"]}


d._brief_client = lambda: StubClient()


def setting(k):
    r = db.execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone()
    return r[0] if r else None


def set_pref(**kw):
    p = json.loads(setting("user_prefs") or "{}"); p.update(kw)
    db.execute("INSERT INTO settings (key,value) VALUES ('user_prefs',?) "
               "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(p),))
    db.commit()


db.execute("DELETE FROM captures")
db.execute("DELETE FROM settings WHERE key LIKE 'captures_%'")
db.commit()

d._maybe_captures()
check("off by default: nothing is read", served["n"] == 0)
check("and it says so once", json.loads(setting("captures_status") or "{}").get("state") in (None, "off"),
      str(setting("captures_status")))

set_pref(captures="on")
served["pages"] = [{"items": [row(100, "Design review", "starts in 10 minutes"),
                              row(101, "Charging rapidly", "", pkg="com.android.systemui")],
                    "cursor": 101, "more": False, "head": 101, "listening": True}]
d._maybe_captures()
st = json.loads(setting("captures_status") or "{}")
check("switched on: reads, stores, reports ok",
      st.get("state") == "ok" and st.get("stored") == 1 and st.get("noise") == 1, str(st))
check("the cursor is saved", int(float(setting("captures_cursor") or 0)) == 101)
check("the real row is on disk",
      db.execute("SELECT COUNT(*) FROM captures WHERE medha_id=100").fetchone()[0] == 1)

n_before = served["n"]
d._maybe_captures()
check("not due again for a few minutes", served["n"] == n_before)
db.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('captures_scan_requested','1')")
db.commit()
served["pages"] = None
d._maybe_captures()
check("a requested scan runs now", served["n"] > n_before)

# A page of pure noise must still advance the cursor, or a phone that posts
# nothing but progress bars re-reads the same page forever.
db.execute("UPDATE settings SET value='0' WHERE key='captures_last_poll'"); db.commit()
served["pages"] = [{"items": [row(200, "Screenshot saved", "", pkg="com.android.systemui")],
                    "cursor": 200, "more": False, "head": 200, "listening": True}]
d._maybe_captures()
check("a page of nothing but noise still moves the cursor",
      int(float(setting("captures_cursor") or 0)) == 200, str(setting("captures_cursor")))

db.execute("UPDATE settings SET value='0' WHERE key='captures_last_poll'"); db.commit()
served["listening"] = False
served["pages"] = [{"items": [], "cursor": 200, "more": False, "head": 200, "listening": False}]
d._maybe_captures()
st = json.loads(setting("captures_status") or "{}")
# The distinction that matters: an empty page looks exactly like a quiet
# phone, and an agent that cannot tell them apart reports a clear day when it
# has gone deaf.
check("a deaf phone is reported, not read as a quiet one",
      st.get("state") == "not_listening", str(st))

db.execute("UPDATE settings SET value='0' WHERE key='captures_last_poll'"); db.commit()
served["listening"] = True


class RefusingClient:
    def __init__(self, status, code="forbidden"):
        self.status, self.code = status, code

    def captures(self, since=0, limit=None):
        raise mb.BriefError(self.status, self.code, "nope")


d._brief_client = lambda: RefusingClient(403)
d._maybe_captures()
st = json.loads(setting("captures_status") or "{}")
check("a 403 names the box the user has to tick",
      st.get("state") == "no_capability" and "Read your notifications" in st.get("detail", ""), str(st))

db.execute("UPDATE settings SET value='0' WHERE key='captures_last_poll'"); db.commit()
d._brief_client = lambda: RefusingClient(404, "not_found")
d._maybe_captures()
st = json.loads(setting("captures_status") or "{}")
check("an older Medha is reported as unsupported, not as an error",
      st.get("state") == "unsupported", str(st))

db.execute("UPDATE settings SET value='0' WHERE key='captures_last_poll'"); db.commit()
d._brief_client = lambda: None
d._maybe_captures()
st = json.loads(setting("captures_status") or "{}")
check("no token configured is said plainly", st.get("state") == "no_token", str(st))

print(f"\n{_ok} passed, {_bad} failed")
sys.exit(1 if _bad else 0)
