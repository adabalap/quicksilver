#!/usr/bin/env python3
"""Bills from messages, app side: the list, Paid, the home strip, scan now, forget, and the v1.4 spend-table removal."""
import sys, os, json, tempfile
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from e2e_test import load_app
P = F = 0
def check(c, m):
    global P, F
    if c: P += 1
    else: F += 1; print(f"  ✗ {m}")

tmp = tempfile.mkdtemp(); dbp = os.path.join(tmp, "b.db")
qs = load_app(dbp)
cl = qs.app.test_client()
db = qs.sqlite3.connect(dbp); db.row_factory = qs.sqlite3.Row
db.execute("PRAGMA foreign_keys=ON")
today = datetime.now().date()
day = lambda n: (today + timedelta(days=n)).isoformat()
db.execute("INSERT INTO notes (id, body, protected, enrich_optout) VALUES ('host','Bills',1,1)")
db.execute("INSERT INTO note_state (note_id, status) VALUES ('host','approved')")
def bill(i, name, amt, due, status="open", rids=()):
    db.execute("INSERT INTO sms_bills (key, received_at, sender, merchant, merchant_key, amount, due_date, status, reminder_ids) VALUES (?,?,?,?,?,?,?,?,?)",
               (f"k{i}", day(-3) + "T09:00:00", "AD-X", name, name.lower(), amt, due, status, json.dumps(list(rids))))
for i, (rid) in enumerate(("r1", "r2")):
    db.execute("INSERT INTO reminders (id, note_id, title, source) VALUES (?, 'host', ?, 'user')", (rid, f"Pay {i}"))
bill(1, "Bescom", 2140, day(1), rids=("r1", "r2"))
bill(2, "Tata Play", 454, day(-2))
bill(3, "Old", 300, day(-40))
db.execute("UPDATE sms_bills SET body='Your bill is due. Pay: https://pay.example.in/x' WHERE merchant='Bescom'")
bill(6, "Late", 77, day(-10))
bill(4, "Far", 999, day(60))
bill(5, "Done", 100, day(2), status="paid")
db.commit()

r = cl.get("/api/bills").get_json()
check(r["enabled"] is False and r["bills"] == [], "off by default: nothing is listed")
cl.post("/api/settings", json={"sms": "on"})
r = cl.get("/api/bills").get_json()
names = [b["merchant"] for b in r["bills"]]
check(r["enabled"] is True and names == ["Late", "Tata Play", "Bescom"], f"open bills this month or recently overdue, soonest first ({names})")
check([b for b in r["bills"] if b["merchant"] == "Bescom"][0]["body"].startswith("Your bill is due"), "the list carries each bill's message")
by = {b["merchant"]: b for b in r["bills"]}
check(by["Tata Play"]["overdue"] and by["Tata Play"]["days"] == -2 and by["Bescom"]["days"] == 1, "days and overdue are computed")
check(by["Bescom"]["note_id"] == "host", "a bill knows the note holding its reminders")
check("Old" not in names and "Far" not in names and "Done" not in names, "long overdue, far away and paid bills are not listed")

h = cl.get("/api/home").get_json()
check(h["bills"] and [b["merchant"] for b in h["bills"]["items"]] == ["Tata Play", "Bescom"], "the home strip lists bills due within a week or up to 3 days overdue (older ones stay in Reminders)")
cl.post("/api/settings", json={"sms": "off"})
check(cl.get("/api/home").get_json()["bills"] is None, "the home strip is silent when the feature is off")
cl.post("/api/settings", json={"sms": "on"})

db.execute("UPDATE reminders SET description='Your bill is due. Pay: https://pay.example.in/x?id=1.' WHERE id='r1'"); db.commit()
n1 = cl.get("/api/notes/host").get_json()
check(any("https://pay.example.in" in (r.get("description") or "") for r in n1["reminders"]), "the note shows a bill reminder's message")
lst = cl.get("/api/reminders").get_json()["reminders"]
check(any("https://pay.example.in" in (r.get("description") or "") for r in lst), "the Reminders list carries the message")
tag = {r["id"]: r for r in lst}
check(tag.get("r1", {}).get("bill_id") and tag["r1"]["bill_id"] == tag.get("r2", {}).get("bill_id")
      and tag["r1"].get("bill_due") == day(1),
      "both of a bill's reminders say which bill they belong to, and when it is due — so the list can show it once")
check(all("bill_id" not in r for r in lst if r["id"] not in ("r1", "r2")), "reminders that are not a bill's carry no bill")
db.execute("INSERT INTO reminders (id, note_id, title, source, description) VALUES ('ag','n1x','x','agent','model-written text')") if False else None
bid = db.execute("SELECT id FROM sms_bills WHERE merchant='Bescom'").fetchone()[0]
check(cl.post(f"/api/bills/{bid}/paid").get_json()["ok"], "Paid answers ok")
check(db.execute("SELECT status FROM sms_bills WHERE id=?", (bid,)).fetchone()[0] == "paid"
      and all(db.execute("SELECT dismissed FROM reminders WHERE id=?", (x,)).fetchone()[0] == 1 for x in ("r1", "r2")), "Paid closes the bill and dismisses its reminders")
check(all(db.execute("SELECT description FROM reminders WHERE id=?", (x,)).fetchone()[0] == "" for x in ("r1", "r2")), "Paid clears the message text")
check(db.execute("SELECT body FROM sms_bills WHERE id=?", (bid,)).fetchone()[0] == "", "Paid clears the stored bill message")
lid = db.execute("SELECT id FROM sms_bills WHERE merchant='Late'").fetchone()[0]
check(cl.post(f"/api/bills/{lid}/dismiss").get_json()["ok"] and db.execute("SELECT status FROM sms_bills WHERE id=?", (lid,)).fetchone()[0] == "dismissed", "Not a bill removes it and remembers it")
check(cl.post("/api/bills/9999/paid").status_code == 404, "an unknown bill is a 404")

cl.post("/api/settings", json={"sms": "off"})
check(cl.post("/api/sms/scan").status_code == 409, "Check now needs the feature on")
cl.post("/api/settings", json={"sms": "on"})
check(cl.post("/api/sms/scan").get_json()["ok"] and db.execute("SELECT value FROM settings WHERE key='sms_scan_requested'").fetchone()[0] == "1", "Check now asks the agent to scan")

db.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('sms_host_note','host'), ('sms_watermark','5')")
db.execute("UPDATE sms_bills SET reminder_ids='[\"r1\"]' WHERE merchant='Bescom'"); db.commit()
check(cl.post("/api/sms/forget").get_json()["ok"], "forget answers ok")
check(db.execute("SELECT COUNT(*) FROM sms_bills").fetchone()[0] == 0 and db.execute("SELECT COUNT(*) FROM reminders WHERE id='r1'").fetchone()[0] == 0
      and db.execute("SELECT 1 FROM settings WHERE key='sms_watermark'").fetchone() is None, "forget removes bills, reminders and the watermark")
check("tables" and not any(db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (t,)).fetchone() for t in ("sms_events", "sms_merchant_cat", "sms_skipped")),
      "the spend tables do not exist")
check(not any(str(r).startswith("/api/spend") for r in qs.app.url_map.iter_rules()), "the Spend API is gone")

# upgrade from v1.4.0: old tables dropped along with the reminders they made
db.execute("CREATE TABLE sms_events (id INTEGER PRIMARY KEY, reminder_id TEXT)")
db.execute("INSERT INTO reminders (id, note_id, title, source) VALUES ('old', 'host', 'Pay Debit', 'user')")
db.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('sms_host_note','host'), ('sms_watermark','5')"); db.commit()
qs.init_db()
db2 = qs.sqlite3.connect(dbp)
check(db2.execute("SELECT 1 FROM sqlite_master WHERE name='sms_events'").fetchone() is None
      and db2.execute("SELECT COUNT(*) FROM reminders WHERE id='old'").fetchone()[0] == 0
      and db2.execute("SELECT 1 FROM settings WHERE key='sms_watermark'").fetchone() is None, "an old database loses its spend tables and their reminders on start")

js = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static/js/20-home.js")).read()
util = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static/js/00-util.js")).read()
check("function linkText" in util and "noopener" in util and "https?" in util, "message links are made tappable, http(s) only, with noopener")
check("bill-open" in js and "bills-hide" in js and "openBill" in js and "billNotices" in js and "/bills/" in js, "the front end has the tappable strip, a way to hide it, the detail sheet and the notice")
ix = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static/index.html")).read()
check('id="billSheet"' in ix and 'id="billBack"' in ix, "the bill sheet exists")

# v1.5.0: add a bill by hand, list every open bill, rename a note
r = cl.post("/api/bills", json={"merchant": "Rent", "amount": 12000, "due_date": (__import__("datetime").date.today() + __import__("datetime").timedelta(days=5)).isoformat()})
check(r.status_code == 200, "a bill can be added by hand")
allb = cl.get("/api/bills?all=1").get_json()["bills"]
check(any(b["merchant"] == "Rent" and b["manual"] for b in allb), "it appears in the full list, marked as added by you")
check(cl.post("/api/bills", json={"merchant": "", "amount": 5, "due_date": "2026-12-01"}).status_code == 400, "a bill without a name is refused")
rid = [b["id"] for b in allb if b["merchant"] == "Rent"][0]
check(cl.post(f"/api/bills/{rid}/paid").status_code == 200 and not any(b["merchant"] == "Rent" for b in cl.get("/api/bills?all=1").get_json()["bills"]), "a hand-added bill can be marked paid")
nid = cl.post("/api/notes", json={"body": "Some thought about gardens"}).get_json()["id"]
cl.patch(f"/api/notes/{nid}", json={"title": "  My   garden "})
n = cl.get(f"/api/notes/{nid}").get_json()
check(n.get("user_title") == "My garden", "a note can be given a title of your own")
cl.patch(f"/api/notes/{nid}", json={"title": ""})
check(cl.get(f"/api/notes/{nid}").get_json().get("user_title") == "", "an empty title hands it back")
tj = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static/js/00-util.js")).read()
check("PASTELS_M3" in tj and 'material:' in tj and "--nt-sticker" in tj and "themes.css" in ix, "the Material and rebuilt Cartoon themes are wired in")
import glob
leftovers = [f for f in glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static/**/*"), recursive=True)
             if os.path.isfile(f) and f.endswith((".js", ".html", ".css")) and any(w in open(f, errors="ignore").read() for w in ("openSpend", "spendSheet", "moneySlot", "/api/spend"))]
check(not leftovers, f"no Spend code is left in the front end ({leftovers})")
print(f"\n  {P} passed, {F} failed"); sys.exit(1 if F else 0)
