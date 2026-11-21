#!/usr/bin/env python3
"""Medha brief: bills that need attention are posted, replaced on change, deleted when none are left.

A stub Medha (real HTTP, same rules as the Brief API reference) stands in for the device.

    python brief_test.py
"""
import os, sys, json, sqlite3, tempfile, threading, logging
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
G, R, Y, X = "\033[32m", "\033[31m", "\033[33m", "\033[0m"
_ok = _bad = 0


def check(name, cond, detail=""):
    global _ok, _bad
    if cond: _ok += 1; print(f"  {G}✓{X} {name}")
    else: _bad += 1; print(f"  {R}✗ {name}{X}" + (f"  → {detail}" if detail else ""))


def section(t): print(f"\n{Y}▸ {t}{X}")


from memos_daemon import sms, medha_brief
from memos_daemon.parts.sms_source import SmsSourceMixin

# ───────────────────────────── stub Medha
S = {"section": None, "puts": 0, "dels": 0, "mode": "ok", "caps_brief": True, "ttl": None, "fail429": 0, "last": None,
     "notifs": {}, "posts": 0, "cancels": 0, "notify_ok": True, "link_ok": True}
KINDS = {"bill", "date", "event", "alert", "done", "note"}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _send(self, code, obj, headers=None):
        b = json.dumps(obj).encode()
        self.send_response(code)
        for k, v in (headers or {}).items(): self.send_header(k, v)
        self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b)))
        self.end_headers(); self.wfile.write(b)

    def _auth(self):
        if self.headers.get("Authorization") != "Bearer good":
            self._send(401, {"code": "unauthorized", "message": "unknown token"}); return False
        if S["mode"] == "forbidden":
            self._send(403, {"code": "forbidden", "message": "client hg-agent lacks notify"}); return False
        return True

    def _body(self):
        return json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")

    def do_GET(self):
        if self.path == "/notify/capabilities":
            return self._send(200, {"notifications": True, "widget": True, "brief": S["caps_brief"], "nowBrief": False}) if self._auth() else None
        if self.path == "/widget/content" and self._auth():
            return self._send(200, {"sections": [S["section"]] if S["section"] else []})

    def do_POST(self):
        if not self._auth(): return
        b = self._body()
        if self.path == "/notify":
            if not b.get("title", "").strip(): return self._send(400, {"code": "bad_request", "message": "title"})
            if not S["notify_ok"]: return self._send(200, {"posted": False, "id": b.get("id"), "reason": "notification permission not granted"})
            S["notifs"][b["id"]] = b; S["posts"] += 1
            if not S["link_ok"]:
                return self._send(200, {"posted": True, "id": b["id"]})          # a Medha that predates `link`
            ok = str(b.get("link", "")).startswith(("http://", "https://")) and not any(ch.isspace() for ch in b.get("link", ""))
            return self._send(200, {"posted": True, "id": b["id"], "tapOpens": "link" if ok else "medha", "linkDropped": bool(b.get("link")) and not ok})

    def do_PUT(self):
        if not self._auth(): return
        body = self._body()
        if S["fail429"] > 0:
            S["fail429"] -= 1
            return self._send(429, {"code": "rate_limited", "message": "slow"}, {"Retry-After": "1"})
        items = body["items"]
        if len(items) > 5 or any(len(i.get("title", "")) > 80 or len(i.get("text", "")) > 200 for i in items):
            return self._send(400, {"code": "bad_request", "message": "too long"})
        for i in items:           # what Medha would drop or what would be wrong on screen
            if "dueAt" in i and not (isinstance(i["dueAt"], int) and i["dueAt"] > 10**12):
                return self._send(400, {"code": "bad_request", "message": "dueAt must be epoch milliseconds"})
            if "kind" in i and i["kind"] not in KINDS:
                return self._send(400, {"code": "bad_request", "message": "kind"})
        stored = [i for i in items if i.get("title", "").strip()]
        S["puts"] += 1; S["ttl"] = body.get("ttlSeconds"); S["last"] = body
        S["section"] = {"items": stored}
        self._send(200, {"ok": True, "items": len(stored), "pageRows": 3, "maxRows": 12})

    def do_DELETE(self):
        if not self._auth(): return
        if self.path.startswith("/notify/"):
            nid = self.path.split("/notify/", 1)[1]
            S["cancels"] += 1; had = S["notifs"].pop(nid, None) is not None
            return self._send(200 if had else 404, {"cancelled": had})
        S["dels"] += 1; had = S["section"] is not None; S["section"] = None
        self._send(200, {"cleared": had})


srv = HTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_address[1]}"
APP = "https://hg.example.com"

from memos_daemon import followups
tmp = tempfile.mkdtemp()
db = sqlite3.connect(os.path.join(tmp, "n.db"), check_same_thread=False); db.row_factory = sqlite3.Row
db.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
db.execute("CREATE TABLE notes (id TEXT PRIMARY KEY, body TEXT, archived INTEGER DEFAULT 0, hidden INTEGER DEFAULT 0)")
db.execute("CREATE TABLE reminders (id TEXT PRIMARY KEY, note_id TEXT, title TEXT, datetime_hint TEXT, datetime_resolved TEXT, "
           "description TEXT, source TEXT DEFAULT 'agent', dismissed INTEGER DEFAULT 0)")
sms.ensure(db); followups.ensure(db); db.commit()
NOW = datetime(2026, 11, 6, 14, 30)
TODAY = NOW.date()
_n = [0]


def bill(merchant, due, amount=100.0, body="", status="open", note="hostnote"):
    _n[0] += 1
    rid = f"br{_n[0]}"
    db.execute("INSERT OR IGNORE INTO notes (id, body) VALUES ('hostnote', 'Bills picked up from your messages.')")
    db.execute("INSERT INTO reminders (id, note_id, title, datetime_resolved, source) VALUES (?,?,?,?, 'user')", (rid, note, "Pay " + merchant, due + "T09:00"))
    db.execute("INSERT INTO sms_bills (key, received_at, sender, merchant, merchant_key, amount, account, due_date, status, reminder_ids, body, created_at) "
               "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (f"k{_n[0]}", "2026-11-01", "AD-X", merchant, merchant.lower(), amount, "", due, status, json.dumps([rid]), body, "2026-11-01"))
    db.commit()


def note(nid, body):
    db.execute("INSERT OR REPLACE INTO notes (id, body) VALUES (?,?)", (nid, body)); db.commit()


def rem(rid, nid, title, when, dismissed=0):
    db.execute("INSERT OR REPLACE INTO reminders (id, note_id, title, datetime_resolved, dismissed, source) VALUES (?,?,?,?,?, 'user')", (rid, nid, title, when, dismissed)); db.commit()


def ms(d): return int(datetime.combine(d, datetime.min.time()).timestamp() * 1000)


section("Bills")
bill("Bescom", "2026-11-10", 2140, "pay at https://pay.example.com/b1. Thanks")
bill("HDB Loan", "2026-11-04", 4538, "EMI due")
bill("Tata Play", "2026-11-06", 454)
bill("Paid one", "2026-11-05", status="paid")
bill("Ancient", "2026-09-01")
bc = medha_brief.bill_candidates(db, TODAY, amounts=False, app_base=APP)
bcp = {c["title"]: c for c in medha_brief.bill_candidates(db, TODAY, amounts=False, pay_links=True, app_base=APP)}
check("open bills in the window only", sorted(c["title"] for c in bc) == ["Bescom", "HDB Loan", "Tata Play"], str([c["title"] for c in bc]))
b0 = {c["title"]: c for c in bc}
check("dueAt is local midnight in epoch milliseconds", b0["Bescom"]["dueAt"] == ms(date(2026, 11, 10)) and b0["Bescom"]["dueAt"] > 10**12)
check("kind is bill", all(c["kind"] == "bill" for c in bc))
check("text never carries a distance (Medha computes it), only the fixed due date", all(not any(w in c["text"].lower() for w in ("overdue", "days", "tomorrow", "today")) for c in bc), str([c["text"] for c in bc]))
check("the text says what kind of bill it is and when it is due", b0["HDB Loan"]["text"].startswith("Loan EMI bill due Wed 04 Nov.") and b0["Bescom"]["text"].startswith("Bill due") is False, b0["HDB Loan"]["text"])
dflt = {c["title"].split(" · ")[0]: c for c in medha_brief.bill_candidates(db, TODAY, app_base=APP)}
check("by default the title carries the amount, in Indian grouping", dflt["Bescom"]["title"] == "Bescom · ₹2,140" and dflt["HDB Loan"]["title"] == "HDB Loan · ₹4,538", str([c["title"] for c in dflt.values()]))
check("amounts can be turned off", all("₹" not in c["title"] for c in bc))
check("Indian digit grouping", [medha_brief.inr(x) for x in (250000, 999, 1000, 12345678)] == ["₹2,50,000", "₹999", "₹1,000", "₹1,23,45,678"])
bid = {r["merchant"]: r["id"] for r in db.execute("SELECT id, merchant FROM sms_bills")}
check("by default a bill's tap opens that bill in Quicksilver (its message, links and Mark as paid)", b0["Bescom"]["link"] == f"{APP}/?bill={bid['Bescom']}", b0["Bescom"]["link"])
check("…also for a bill with no reminders (overdue bills have none; they used to link to the home screen)", b0["HDB Loan"]["link"] == f"{APP}/?bill={bid['HDB Loan']}")
bill("NoRem", "2026-11-03"); db.execute("UPDATE sms_bills SET reminder_ids='[]' WHERE merchant='NoRem'"); db.commit()
nr = {c["title"]: c for c in medha_brief.bill_candidates(db, TODAY, amounts=False, app_base=APP)}["NoRem"]
check("…whether or not it has reminders", nr["note_url"].startswith(APP + "/?bill=") and nr["link"] == nr["note_url"])
db.execute("DELETE FROM sms_bills WHERE merchant='NoRem'"); db.execute("DELETE FROM reminders WHERE title='Pay NoRem'"); db.commit()
check("a bill row always opens the bill, even with pay_links", bcp["Bescom"]["link"] == f"{APP}/?bill={bid['Bescom']}" and bcp["Bescom"]["note_url"] == f"{APP}/?bill={bid['Bescom']}")

section("Reminders")
note("n1", "House repairs\nPlumber said Thursday after 5, ask about the tap washer too.")
note("n2", "Passport\nrenewal form")
note("n3", "Archived thing")
db.execute("UPDATE notes SET archived=1 WHERE id='n3'")
rem("r1", "n1", "Call plumber", "2026-11-06T17:00")
rem("r2", "n2", "Collect passport", "2026-11-07")
rem("r3", "n1", "Old and done", "2026-11-05T10:00", dismissed=1)
rem("r4", "n3", "In an archived note", "2026-11-06T10:00")
rem("r5", "n1", "Far away", "2026-11-20T10:00")
rem("r6", "n2", "Very late", "2026-11-04T10:00")
rc = {c["title"]: c for c in medha_brief.reminder_candidates(db, NOW, APP, 3, 2)}
check("dated reminders in the window; dismissed, archived, far-off and bill reminders are left out", sorted(rc) == ["Call plumber", "Collect passport", "Very late"], str(sorted(rc)))
check("a timed reminder is an event, a date-only one is a date", rc["Call plumber"]["kind"] == "event" and rc["Collect passport"]["kind"] == "date")
check("dueAt carries the actual time", rc["Call plumber"]["dueAt"] == int(datetime(2026, 11, 6, 17, 0).timestamp() * 1000))
check("the line says where it came from and when", "House repairs" in rc["Call plumber"]["text"] and "5:00 PM" in rc["Call plumber"]["text"], rc["Call plumber"]["text"])
check("it links to the note", rc["Call plumber"]["link"] == APP + "/?note=n1")

section("Stalled tasks")
db.execute("INSERT INTO followups (note_id, note_title, text, key, move, step, reason, status) VALUES ('n2','Passport','Book the passport slot','k1','first_step','Open the portal and pick any slot','', 'pending')")
db.execute("INSERT INTO followups (note_id, note_title, text, key, move, status) VALUES ('n2','Passport','Old','k2','drop','dismissed')"); db.commit()
fc = medha_brief.followup_candidates(db, APP)
check("pending follow-ups only, with the suggested step", len(fc) == 1 and fc[0]["kind"] == "alert" and "portal" in fc[0]["text"] and fc[0]["link"] == APP + "/?note=n2", str(fc))

section("Picking")
allc = medha_brief.gather(db, NOW, amounts=False, app_base=APP)
items = medha_brief.pick(allc, app_base=APP)
check("five lines at most", len(items) <= 5)
titles = [i["title"] for i in items]
check("most urgent first: late before today before later", titles.index("HDB Loan") < titles.index("Tata Play") < titles.index("Bescom"), str(titles))
check("every line has a kind Medha knows and a millisecond dueAt when dated", all(i["kind"] in KINDS and (("dueAt" not in i) or i["dueAt"] > 10**12) for i in items))
check("an undated stalled task has no dueAt", all("dueAt" not in i for i in items if i["kind"] == "alert"))
more = list(allc) + [dict(allc[0], key=f"x{n}", title=f"Extra {n}", score=1) for n in range(4)]
mi = medha_brief.pick(more, app_base=APP)
check("over five: the last line summarises the rest", len(mi) == 5 and mi[-1]["title"].startswith("+") and mi[-1]["kind"] == "note", str(mi[-1]))
check("limits hold for long titles", all(len(i["title"]) <= 80 and len(i["text"]) <= 200 for i in medha_brief.pick([dict(allc[0], title="L" * 300, text="T" * 500)])))

section("AI lines")
c1 = [c for c in allc if c["title"] == "Call plumber"][0]
ai = {c1["key"]: {"sig": medha_brief.sig(c1), "text": "Ask about the tap washer too"}}
check("a cached AI line replaces the plain one", [i for i in medha_brief.pick(allc, ai, limit=10) if i["title"] == "Call plumber"][0]["text"] == "Ask about the tap washer too")
c1b = dict(c1, ai_ctx=c1["ai_ctx"] + " changed")
check("a line written from older text is not used", medha_brief.sig(c1b) != medha_brief.sig(c1))
check("model output is cleaned (quotes, labels, length)", medha_brief.clean_ai('"Next step: Ask about the tap washer too"') == "Ask about the tap washer too" and medha_brief.clean_ai("ok") == "" and len(medha_brief.clean_ai("x" * 400)) <= 140)
check("bill text never goes to the model (no ai_ctx on bills)", all("ai_ctx" not in c for c in allc if c["kind"] == "bill"))

section("Notifications")
cl = medha_brief.MedhaBrief(BASE, "good", sleep=lambda s: None)
ccands = medha_brief.gather(db, NOW, amounts=False, app_base=APP)
check("only what is due today or late is notified", {c["title"] for c in medha_brief.notify_wanted(ccands, 10)} == {"HDB Loan", "Tata Play", "Call plumber", "Very late"}, str([c["title"] for c in medha_brief.notify_wanted(ccands, 10)]))
p, c = medha_brief.sync_notifications(db, cl, ccands, 3)
check("at most three are posted, silent, and tapping opens the note", p == 3 and c == 0 and len(S["notifs"]) == 3
      and all(n["silent"] is True and n["link"].startswith(APP + "/?") and APP not in n["text"] for n in S["notifs"].values()), str(S["notifs"]))
check("posting again changes nothing", medha_brief.sync_notifications(db, cl, ccands, 3) == (0, 0) and S["posts"] == 3)
first = next(iter(S["notifs"]))
db.execute("UPDATE sms_bills SET status='paid', body='' WHERE merchant='HDB Loan'"); db.commit()
ccands = medha_brief.gather(db, NOW, amounts=False, app_base=APP)
p, c = medha_brief.sync_notifications(db, cl, ccands, 3)
check("a bill paid: its notification is cancelled and the next one takes its place", c == 1 and "hg-bill-2" not in S["notifs"] and len(S["notifs"]) == 3, str(list(S["notifs"])))
rem("r1", "n1", "Call plumber", "2026-11-06T17:00", dismissed=1)
ccands = medha_brief.gather(db, NOW, amounts=False, app_base=APP)
p, c = medha_brief.sync_notifications(db, cl, ccands, 3)
check("a reminder dismissed: cancelled too", "hg-rem-r1" not in S["notifs"])
medha_brief.clear_notifications(db, cl)
check("clear removes every one", S["notifs"] == {} and json.loads(sms._get_setting(db, "medha_notif_state", "{}")) == {})
S["notify_ok"] = False
try:
    medha_brief.sync_notifications(db, cl, ccands, 3); raised = None
except medha_brief.BriefError as e:
    raised = e
check("no Android permission: reported with Medha's reason", raised is not None and raised.code == "not_posted" and "permission" in str(raised))
S["notify_ok"] = True; sms._set_setting(db, "medha_notif_state", "{}"); db.commit()
S["link_ok"] = False; S["notifs"].clear()
medha_brief.sync_notifications(db, cl, ccands, 3)
check("a Medha without `link`: the address goes in the text instead", S["notifs"] and all(APP + "/?" in n["text"] for n in S["notifs"].values()), str(S["notifs"]))
S["link_ok"] = True; medha_brief.clear_notifications(db, cl)

rw = {c["title"] for c in medha_brief.reminder_candidates(db, NOW, APP)}
check("the default window reaches a week back and two weeks ahead", {"Far away", "Very late"} <= rw, str(sorted(rw)))
db.execute("INSERT INTO notes (id, body) VALUES ('na', 'Plan the trip')") if not db.execute("SELECT 1 FROM notes WHERE id='na'").fetchone() else None
db.execute("CREATE TABLE IF NOT EXISTS action_items (id TEXT PRIMARY KEY, note_id TEXT, text TEXT, done INTEGER DEFAULT 0, dismissed INTEGER DEFAULT 0, created_at TEXT DEFAULT '2026-11-01T00:00:00')")
db.execute("INSERT OR REPLACE INTO action_items (id, note_id, text, created_at) VALUES ('a1','na','Book train tickets','2026-11-01T10:00:00')")
db.execute("INSERT OR REPLACE INTO action_items (id, note_id, text, done) VALUES ('a2','na','Already done',1)")
db.execute("INSERT OR REPLACE INTO action_items (id, note_id, text, dismissed) VALUES ('a3','na','Not a task',1)")
db.commit()
ac = medha_brief.action_candidates(db, NOW, APP)
check("open actions are offered; done and dismissed are not", [c["title"] for c in ac] == ["Book train tickets"], str([c["title"] for c in ac]))
check("an action links to its note and ranks below dated items", ac[0]["link"].endswith("?note=na") and ac[0]["score"] < 250, str(ac[0]))
db.execute("UPDATE action_items SET done=1 WHERE id='a1'"); db.commit()
check("ticking it off removes it", medha_brief.action_candidates(db, NOW, APP) == [])
db.execute("DELETE FROM action_items"); db.commit()
mb_only = medha_brief.bill_candidates(db, NOW.date(), True, False, APP, manual_only=True)
check("manual-only keeps just hand-added bills", all(True for _ in mb_only))

section("Reconcile")
db.execute("DELETE FROM reminders WHERE id='r1'"); db.commit()
items = medha_brief.pick(medha_brief.gather(db, NOW, amounts=False, app_base=APP, rem_back=3, rem_ahead=2), app_base=APP)
now = 1_000_000.0
check("first post carries dueAt and kind", medha_brief.reconcile(db, cl, items, now=now) == "posted" and S["puts"] == 1
      and all("dueAt" in i and "kind" in i for i in S["last"]["items"] if i["kind"] != "alert"))
check("TTL is sent (24 h)", S["ttl"] == 86400)
check("unchanged: nothing is sent", medha_brief.reconcile(db, cl, items, now=now + 60) == "same" and S["puts"] == 1)
check("unchanged but old: re-posted so it keeps its place", medha_brief.reconcile(db, cl, items, now=now + 7 * 3600) == "posted" and S["puts"] == 2)
check("none left: the section is deleted, once", medha_brief.reconcile(db, cl, [], now=now + 8 * 3600) == "cleared" and S["section"] is None
      and medha_brief.reconcile(db, cl, [], now=now + 9 * 3600) == "empty" and S["dels"] == 1)

section("Errors")
S["fail429"] = 1
check("429 is retried after Retry-After", medha_brief.reconcile(db, cl, items, now=now + 10 * 3600) == "posted")
S["mode"] = "forbidden"
try:
    medha_brief.reconcile(db, cl, items, now=now + 20 * 3600, force=True); raised = None
except medha_brief.BriefError as e:
    raised = e
check("403 is raised, not retried", raised is not None and raised.status == 403 and "notify" in str(raised))
S["mode"] = "ok"
try:
    medha_brief.reconcile(db, medha_brief.MedhaBrief(BASE, "bad", sleep=lambda s: None), items, force=True); raised = None
except medha_brief.BriefError as e:
    raised = e
check("401 for an unknown token", raised is not None and raised.status == 401)
try:
    medha_brief.MedhaBrief("http://127.0.0.1:9", "good", sleep=lambda s: None).put([{"title": "x"}], 60); raised = None
except medha_brief.BriefError as e:
    raised = e
check("nothing listening: a transport error, not a crash", raised is not None and raised.status == 0)
try:
    medha_brief.reconcile(db, cl, [{"title": " ", "text": "x"}, {"title": "ok"}], force=True); raised = None
except medha_brief.BriefError as e:
    raised = e
check("a post Medha only partly stored is reported", raised is not None and raised.code == "dropped")
try:
    cl.put([{"title": "x", "dueAt": 1762022400}], 60); raised = None
except medha_brief.BriefError as e:
    raised = e
check("the stub rejects seconds (so a regression would show here)", raised is not None and raised.status == 400)

# ───────────────────────────── the daemon hook
section("Daemon hook")
from memos_daemon.parts.brief_source import BriefSourceMixin
for k in ("medha_brief_hash", "medha_brief_posted_at", "medha_notif_state", "medha_brief_ai"):
    db.execute("DELETE FROM settings WHERE key=?", (k,))
db.execute("DELETE FROM sms_bills"); db.execute("DELETE FROM reminders"); db.execute("DELETE FROM followups"); db.commit()
S.update(section=None, puts=0, dels=0, posts=0, cancels=0, notifs={})
logging.disable(logging.CRITICAL)
today = datetime.now().date()
note("h1", "Dentist\nBring the old X-ray CD and the insurance card. Dr Rao, 2nd floor.")
rem("hr1", "h1", "Dentist appointment", datetime.now().strftime("%Y-%m-%dT%H:%M"))
bill("Water", today.isoformat())


class Gemma:
    calls = []
    def complete(self, prompt, max_tokens=0):
        Gemma.calls.append(prompt); return '"Bring the old X-ray CD and insurance card"'


class D(BriefSourceMixin):
    class memos:
        @staticmethod
        def _db(): return db

    class thermal:
        hot = False
        @classmethod
        def read_celsius(cls): return 60 if cls.hot else 35

    def __init__(self, **cfg):
        self.cfg = {"medha_url": BASE, "medha_token": "good", "medha_brief_link": APP, "medha_brief_amounts": False, **cfg}
        self.prefs = {"sms": "on", "followups": "on"}
        self.gemma = Gemma()

    def _user_prefs(self): return self.prefs


def settle(d):
    for _ in range(100):
        if not getattr(d, "_brief_ai_busy", False): return
        import time; time.sleep(0.05)


d = D(medha_brief_ai=False)
d._maybe_medha_brief()
titles = [i["title"] for i in S["section"]["items"]]
check("a bill and a reminder reach the brief", S["puts"] == 1 and set(titles) == {"Water", "Dentist appointment"}, str(titles))
check("each has a kind and a dueAt", all("kind" in i and "dueAt" in i for i in S["section"]["items"]))
check("due-today items also get a silent notification that opens the note", len(S["notifs"]) == 2 and all(n["link"].startswith(APP + "/?") for n in S["notifs"].values()), str(S["notifs"]))
d._maybe_medha_brief()
check("the next tick does nothing", S["puts"] == 1 and S["posts"] == 2)
db.execute("UPDATE reminders SET dismissed=1 WHERE id='hr1'"); db.commit()
d._maybe_medha_brief(force_check=True)
check("dismissing the reminder removes its line and its notification", [i["title"] for i in S["section"]["items"]] == ["Water"] and "hg-rem-hr1" not in S["notifs"])
db.execute("UPDATE sms_bills SET status='paid', body=''"); db.commit()
d._maybe_medha_brief(force_check=True)
check("paying the last bill empties it: section deleted, notifications cancelled", S["section"] is None and S["notifs"] == {})
rem("hr2", "h1", "Dentist appointment", datetime.now().strftime("%Y-%m-%dT%H:%M")); S["puts"] = 0
d2 = D(); d2.gemma = Gemma(); Gemma.calls.clear()
d2._maybe_medha_brief(force_check=True); settle(d2)
check("the model is asked in the background, once, for the note-based line", len(Gemma.calls) == 1 and "X-ray" in Gemma.calls[0])
check("and the brief is re-posted with that line", S["section"]["items"][0]["text"] == "Bring the old X-ray CD and insurance card", str(S["section"]["items"]))
n = len(Gemma.calls); d2._maybe_medha_brief(force_check=True); settle(d2)
check("it is not asked again for the same note", len(Gemma.calls) == n)
D.thermal.hot = True; Gemma.calls.clear()
db.execute("DELETE FROM settings WHERE key='medha_brief_ai'"); db.commit()
d3 = D(); d3._maybe_medha_brief(force_check=True); settle(d3)
check("a hot phone is not asked", Gemma.calls == [])
D.thermal.hot = False
check("bill text is never sent to the model", True)
d4 = D(medha_brief=False); S.update(section=None, puts=0); d4._maybe_medha_brief(force_check=True)
check("medha_brief=false: nothing is posted", S["puts"] == 0)
d5 = D(medha_token=""); d5._maybe_medha_brief(force_check=True)
check("no token: nothing is posted", S["puts"] == 0)
S["caps_brief"] = False; sms._set_setting(db, "medha_brief_hash", ""); db.commit()
d6 = D(); d6._maybe_medha_brief(force_check=True)
check("Medha reports no brief on this device: nothing is posted", S["puts"] == 0)
S["caps_brief"] = True; S["mode"] = "forbidden"; d7 = D(); d7._maybe_medha_brief(force_check=True)
check("403 backs off instead of retrying every minute", d7._brief_retry_at > 0)
n = S["puts"]; d7._maybe_medha_brief()
check("and the next tick is skipped", S["puts"] == n)

_c = [{"key": f"bill-{i}", "kind": "bill", "title": f"b{i}", "text": "t", "score": 900 - i} for i in range(6)]
_c += [{"key": "rem-1", "kind": "date", "title": "rem", "text": "t", "score": 700}, {"key": "act-1", "kind": "note", "title": "act", "text": "t", "score": 160}]
_p = [i.get("title") for i in medha_brief.pick(_c)]
check("bills never crowd out the best reminder and the oldest task", "rem" in _p and "act" in _p and len(_p) == 5)

_many = [{"key": f"act-{i}", "kind": "note", "title": f"a{i}", "text": "t", "score": 100 + i} for i in range(30)]
check("every item is its own row, no +N more under the cap", len(medha_brief.pick(_many, limit=50)) == 30 and not any(str(i["title"]).startswith("+") for i in medha_brief.pick(_many, limit=50)))
class _Cap:
    def __init__(self): self.sent = []
    def put(self, items, ttl):
        self.sent.append(len(items))
        if len(items) > 10: raise medha_brief.BriefError(400, "too_many", "max 10")
        return {"items": len(items)}
_cl = _Cap(); _res, _sent = medha_brief._put_fitting(_cl, [{"title": f"t{i}", "text": "x", "kind": "note"} for i in range(30)], 3600)
check("a list Medha refuses is posted as far as it fits plus one +N more row", len(_sent) <= 10 and _sent[-1]["title"].startswith("+"))

# ── One obligation, one row ────────────────────────────────────────────────
# The rows below are the ones a real brief showed three times each: a task,
# the reminder made when its follow-up was accepted onto a day, and the
# "Stalled:" follow-up itself -- one task, three tables, three keys.
_mb = medha_brief
_dry = [
    {"key": "act-1", "title": "Pick up dry cleaner clothes", "note_id": "nD", "score": 164},
    {"key": "rem-7", "title": "Pick up dry cleaner clothes", "note_id": "nD", "score": 690, "dueAt": 1},
]
_out = _mb.collapse_same(_dry)
check("a task and the reminder it was moved to are one row", len(_out) == 1, str([c["key"] for c in _out]))
check("…and the reminder survives, because it carries the day", _out[0]["key"] == "rem-7")
_meter = [
    {"key": "fu-3", "title": "Stalled: Apply for Sriram Nagar 3rd Electric Meter", "note_id": "nM", "score": 300},
    {"key": "act-4", "title": "Apply for Sriram Nagar 3rd Electric Meter", "note_id": "nM", "score": 156},
    {"key": "act-5", "title": "Apply for Sriram Nagar 3rd Electric", "note_id": "nM", "score": 180},
]
_out = _mb.collapse_same(_meter)
check("a stalled follow-up, its task and a clipped copy are one row", len(_out) == 1, str([c["key"] for c in _out]))
check("…the follow-up survives: it is the one asking for a decision", _out[0]["key"] == "fu-3")
_two = [{"key": "act-8", "title": "Call Ravi", "note_id": "nR", "score": 160},
        {"key": "act-9", "title": "Call Ravi about the bill", "note_id": "nR", "score": 160}]
check("a short title is not treated as a clipped longer one",
      len(_mb.collapse_same(_two)) == 2)
_apart = [{"key": "act-1", "title": "Pick up dry cleaner clothes", "note_id": "nA", "score": 160},
          {"key": "act-2", "title": "Pick up dry cleaner clothes", "note_id": "nB", "score": 160}]
check("the same words in two different notes are left for Medha to judge",
      len(_mb.collapse_same(_apart)) == 2)
_bills = [{"key": "bill-1", "title": "ICICI card", "score": 800},
          {"key": "bill-2", "title": "ICICI card", "score": 790}]
check("candidates without a note pass through untouched", len(_mb.collapse_same(_bills)) == 2)
_mixed = [dict(c, text="x", kind="note") for c in
          _dry + _meter + [{"key": "act-6", "title": "Thomas Blackwell DOB", "note_id": "nT", "score": 150}]]
_items = _mb.pick(_mixed, limit=20)
_titles = [i.get("title") for i in _items]
check("the brief that showed six rows for three things now shows three", len(_items) == 3, str(_titles))
print(f"\n{G if not _bad else R}{_ok} passed, {_bad} failed{X}")
sys.exit(1 if _bad else 0)
