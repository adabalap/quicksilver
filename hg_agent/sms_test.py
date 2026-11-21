#!/usr/bin/env python3
"""SMS source: made-up bill messages through the real parser, the scan, the reminders and the real daemon hook.

No real message is used anywhere. The wording below imitates the regular templates Indian banks, cards,
UPI apps and utilities send. The first run on a real phone will turn up wordings these rules miss — the
app lists what it skipped (digits masked) so a new rule can be added from the wording alone.

    python sms_test.py        (needs ../hg_ui beside the agent)
"""
import os, re, sys, json, sqlite3, tempfile, importlib.util, logging
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


from memos_daemon import sms

NOW = datetime(2026, 11, 6, 11, 0, 0)
P = lambda body, sender="AD-BESCOM": sms.parse(body, sender, NOW)

section("Parsing: bills")
ev, why = P("Dear Customer, your electricity bill of Rs 2,140.00 is due on 10-Nov-2026. Pay now to avoid late fee.")
check("bill: amount and due date", ev and ev["amount"] == 2140.0 and ev["due_date"] == "2026-11-10", f"{ev} {why}")
check("bill: merchant falls back to the sender's name", ev and ev["merchant"] == "Bescom", str(ev))
ev, _ = P("Your Airtel postpaid bill of Rs 799 is payable by 8/11/2026.", "VM-AIRTEL")
check("bill: dd/mm/yyyy", ev and ev["due_date"] == "2026-11-08" and ev["merchant"] == "Airtel", str(ev))
ev, _ = P("HDFC Bank Credit Card: Total amount due Rs 14,250.00, minimum due Rs 710. Pay by 12 Nov.", "AX-HDFCBK")
check("bill: card statement, first amount is the total", ev and ev["amount"] == 14250.0 and ev["due_date"] == "2026-11-12", str(ev))
ev, _ = P("LIC premium of Rs 5,000 due on 20-11-2026 for policy ending 1234. Pay to avoid lapse.", "VK-LICIND")
check("bill: insurance premium, last four of the policy only", ev and ev["amount"] == 5000.0 and ev["account"] == "1234", str(ev))
ev, _ = P("Your HDB Loan EMI of Rs 4,538 is due on 04-11-2026. Keep your account funded.", "VM-HDBFSL")
check("bill: loan EMI", ev and ev["amount"] == 4538.0 and ev["due_date"] == "2026-11-04", str(ev))
ev, _ = P("Dear customer, Rs 10,000 will be debited from A/c XX1234 on 10-11-2026 towards your SIP mandate.", "AX-HDFCBK")
check("an upcoming auto-debit is a reminder, named Auto-debit or by its mandate", ev and ev["due_date"] == "2026-11-10" and ev["amount"] == 10000.0, str(ev))

section("Merchant names are never junk")
for body, sender in (("Rs 56,410 payment of INR 56 is due on 10-11-2026 bill", "VM-TATAPL"),
                     ("Debit Rs 10,000 bill due 10-11-2026", "AX-BESCOM"),
                     ("INR 50 bill is due on 10-11-2026", "AD-ACTFIB")):
    ev, _ = P(body, sender)
    m = ev["merchant"] if ev else ""
    check(f"'{body[:28]}…' has a clean merchant ({m})", ev and not re.match(r"(?i)^(of|inr|rs|debit|payment|\d)", m) and len(m) >= 3, str(ev))

section("Things that are not bills")
for name, body in (("a debit alert", "Rs 480.00 debited from A/c XX1234 on 30-10-26 at SWIGGY. Avl Bal Rs 12,345.67."),
                   ("a credit alert", "Rs 52,000.00 credited to your A/c XX1234 on 31-10-26 by NEFT."),
                   ("a bill that was paid", "Rs 2,140.00 debited towards BESCOM electricity bill on 02-11-26 from A/c XX1234"),
                   ("a payment receipt", "Thank you for your payment of Rs 799 towards your Airtel bill due on 8/11/2026."),
                   ("an OTP", "123456 is your OTP for HDFC Bank transaction of Rs 2,500. Do not share it."),
                   ("a promotion", "Congratulations! Win Rs 50,000 cashback. Click to apply now. T&C apply."),
                   ("a balance message", "Your account balance is good. Thank you for banking with us."),
                   ("a bill with no date", "Your electricity bill of Rs 900 is pending, please pay.")):
    ev, why = P(body, "AX-HDFCBK")
    check(f"{name} is ignored ({why})", ev is None, str(ev))
check("OTP detector", sms.is_otp("Your OTP is 123456") and not sms.is_otp("Rs 100 debited at Swiggy"))

section("Payment evidence")
check("a debit alert is evidence", sms.payment_evidence("Rs 4,538 debited from A/c XX1234 towards HDB Loan EMI")["amount"] == 4538.0)
check("an upcoming debit is not evidence", sms.payment_evidence("Rs 10,000 will be debited from A/c XX1234 on 10-11-2026") is None)
check("an OTP is not evidence", sms.payment_evidence("OTP 123456 for payment of Rs 100 debited") is None)

section("Sender gate")
check("bank header passes", sms.sender_ok("AX-HDFCBK")[0])
check("transactional suffix passes", sms.sender_ok("VM-SBIINB-S")[0])
check("promotional suffix blocked", not sms.sender_ok("VM-OFFERS-P")[0])
check("a phone number is a person, not a bank", not sms.sender_ok("+919876543210")[0])

# ───────────────────────────── the scan on a real database
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
db.execute("PRAGMA foreign_keys=ON")


def msg(body, sender="AD-BESCOM", when="2026-11-05 08:00:00", typ="inbox", mid=0):
    return {"_id": mid, "threadid": 1, "type": typ, "read": True, "number": sender, "sender": sender, "received": when, "body": body}


INBOX = [
    msg("Dear Customer, your electricity bill of Rs 2,140.00 is due on 10-Nov-2026.", "AD-BESCOM", when="2026-11-05 08:00:00"),
    msg("Your Tata Play bill of Rs 454 is due on 17-11-2026.", "VM-TATAPL", when="2026-11-05 09:00:00"),
    msg("Your HDB Loan EMI of Rs 4,538 is due on 04-11-2026.", "VM-HDBFSL", when="2026-11-01 09:00:00"),
    msg("Rs 480.00 debited from A/c XX1234 on 30-10-26 at SWIGGY. Avl Bal Rs 12,345.67.", "AX-HDFCBK", when="2026-11-05 14:00:00"),
    msg("123456 is your OTP for a transaction of Rs 2,500. Do not share.", "AX-HDFCBK", when="2026-11-05 15:00:00"),
    msg("Win Rs 50,000 cashback now! Click to apply. T&C", "VM-OFFERS-P", when="2026-11-05 16:00:00"),
    msg("Hey, can you send me Rs 500 for lunch? due tomorrow", "+919876543210", when="2026-11-05 17:00:00"),
    msg("Rs 500 sent.", typ="sent", when="2026-11-05 18:00:00"),
    msg("Your old water bill of Rs 300 is due on 01-08-2026.", "AD-BWSSB", when="2026-08-01 10:00:00"),
]

# ───────────────────────────── the live-clock inbox
#
# INBOX above is read with NOW pinned to 2026-11-06, so its due dates are
# literals and the parser assertions can name them exactly. That is right for
# the parser and wrong for the daemon hook further down, which runs against
# the REAL clock: those same literals went stale on their own, and from about
# 13 November the hook produced three bills and zero reminders, failing
# "there is data before forgetting" on a tree nobody had touched.
#
# So the hook gets its own inbox, with due dates computed from today. Same
# wordings, same senders; only the dates move. A test whose result depends on
# the day it is run is not a test.
def _due(days):
    return (datetime.now() + timedelta(days=days)).strftime("%d-%m-%Y")


def live_inbox():
    return [
        msg(f"Dear Customer, your electricity bill of Rs 2,140.00 is due on {_due(5)}.", "AD-BESCOM"),
        msg(f"Your Tata Play bill of Rs 454 is due on {_due(9)}.", "VM-TATAPL"),
        msg(f"Your HDB Loan EMI of Rs 4,538 is due on {_due(2)}.", "VM-HDBFSL"),
        msg("123456 is your OTP for a transaction of Rs 2,500. Do not share.", "AX-HDFCBK"),
        msg("Win Rs 50,000 cashback now! Click to apply. T&C", "VM-OFFERS-P"),
    ]


LIVE = live_inbox()

section("Scan")
st = sms.scan(db, INBOX, {"days_back": 60}, NOW); db.commit()
bills = {r["merchant_key"]: r for r in db.execute("SELECT * FROM sms_bills")}
check("only bills are recorded", set(bills) == {"bescom", "tataplay", "hdbloan"}, str(list(bills)))
check("nothing about spending is kept: no debit, OTP, promo or personal text",
      db.execute("SELECT COUNT(*) FROM sms_bills").fetchone()[0] == 3)
cols = [c[1] for c in db.execute("PRAGMA table_info(sms_bills)")]
check("a bill row keeps its message only while it is open", all(r["body"] == "" for r in db.execute("SELECT * FROM sms_bills WHERE status<>'open'")) and all(r["body"] for r in db.execute("SELECT * FROM sms_bills WHERE status='open'")), str(cols))
dump = " ".join(str(tuple(r)) for r in db.execute("SELECT * FROM sms_bills"))
alltext = " ".join(str(r[0]) for r in db.execute("SELECT description FROM reminders"))
check("no OTP, payment alert or personal text is kept anywhere", all(w not in dump + alltext for w in ("123456", "lunch", "9876543210", "SWIGGY", "cashback")))
check("the old spend tables do not exist", not any(db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (t,)).fetchone()
      for t in ("sms_events", "sms_merchant_cat", "sms_skipped")))

section("Two reminders per bill, at 09:00")
b = db.execute("SELECT * FROM sms_bills WHERE merchant_key='bescom'").fetchone()
rids = json.loads(b["reminder_ids"])
rems = [dict(db.execute("SELECT * FROM reminders WHERE id=?", (r,)).fetchone()) for r in rids]
check("bescom: one reminder two days before and one on the day", [r["datetime_resolved"] for r in rems] ==
      ["2026-11-08T09:00", "2026-11-10T09:00"], str([r["datetime_resolved"] for r in rems]))
check("titles say what and when", "Pay Bescom ₹2,140 · due in 2 days" == rems[0]["title"] and rems[1]["title"].endswith("due today"), str([r["title"] for r in rems]))
check("reminders are user-sourced, on one protected host note",
      all(r["source"] == "user" for r in rems) and db.execute("SELECT COUNT(DISTINCT note_id) FROM reminders").fetchone()[0] == 1)
host = db.execute("SELECT * FROM notes WHERE id=?", (rems[0]["note_id"],)).fetchone()
check("each bill reminder carries the original message, so its details and any payment link are on hand",
      all("electricity bill of Rs 2,140.00 is due on 10-Nov-2026" in r["description"] for r in rems), str([r["description"] for r in rems]))
check("host note is protected and opted out of enrichment", host["protected"] == 1 and host["enrich_optout"] == 1 and host["no_reminders"] == 1)
hb = db.execute("SELECT * FROM sms_bills WHERE merchant_key='hdbloan'").fetchone()
check("a bill already overdue gets no new reminders but is kept", json.loads(hb["reminder_ids"]) == [] and hb["status"] == "open", str(dict(hb)))
tb = db.execute("SELECT * FROM sms_bills WHERE merchant_key='tataplay'").fetchone()
check("a bill due in 11 days gets both reminders", len(json.loads(tb["reminder_ids"])) == 2)

section("Running it again changes nothing")
n_rem = db.execute("SELECT COUNT(*) FROM reminders").fetchone()[0]
st2 = sms.scan(db, INBOX, {"days_back": 60}, NOW); db.commit()
check("same inbox, no new bills or reminders", st2["added"] == 0 and db.execute("SELECT COUNT(*) FROM sms_bills").fetchone()[0] == 3
      and db.execute("SELECT COUNT(*) FROM reminders").fetchone()[0] == n_rem, str(st2))
dup = msg("Reminder: your electricity bill of Rs 2,140.00 is due on 10-Nov-2026. Pay now.", "AD-BESCOM", when="2026-11-06 07:00:00")
st3 = sms.scan(db, [dup], {}, NOW); db.commit()
check("a second reminder message for the same bill adds nothing", st3["added"] == 0 and db.execute("SELECT COUNT(*) FROM reminders").fetchone()[0] == n_rem)

section("A payment closes the bill")
paid = msg("Rs 2,140.00 debited from A/c XX1234 towards BESCOM electricity bill on 07-11-26.", "AX-HDFCBK", when="2026-11-07 10:00:00")
st4 = sms.scan(db, [paid], {}, datetime(2026, 11, 7, 11, 0), ); db.commit()
b = db.execute("SELECT * FROM sms_bills WHERE merchant_key='bescom'").fetchone()
check("the matching bill is marked paid", b["status"] == "paid" and st4["paid"] == 1, str(dict(b)))
check("the message text is cleared once the bill is paid", all(db.execute("SELECT description FROM reminders WHERE id=?", (r,)).fetchone()[0] == "" for r in json.loads(b["reminder_ids"])))
check("its reminders are dismissed", all(db.execute("SELECT dismissed FROM reminders WHERE id=?", (r,)).fetchone()[0] == 1 for r in json.loads(b["reminder_ids"])))
check("other bills stay open", db.execute("SELECT COUNT(*) FROM sms_bills WHERE status='open'").fetchone()[0] == 2)
other = msg("Rs 454.00 debited from A/c XX1234 on 07-11-26 at SOMEWHERE", "AX-HDFCBK", when="2026-11-07 11:00:00")
sms.scan(db, [other], {}, datetime(2026, 11, 7, 12, 0)); db.commit()
check("the same amount at an unrelated name does not close a bill", db.execute("SELECT status FROM sms_bills WHERE merchant_key='tataplay'").fetchone()[0] == "open")
emi = msg("Rs 4,538.00 debited from A/c XX1234 towards HDB FSL EMI on 05-11-26", "AX-HDFCBK", when="2026-11-05 10:00:00")
sms.scan(db, [emi], {}, datetime(2026, 11, 7, 12, 0)); db.commit()
check("a payment made BEFORE the bill message does not close it", db.execute("SELECT status FROM sms_bills WHERE merchant_key='hdbloan'").fetchone()[0] == "open")

section("Overdue bills are not kept forever")
sms.scan(db, [], {}, datetime(2026, 11, 25, 9, 0)); db.commit()
check("two weeks after the due date an unpaid bill is dropped",
      db.execute("SELECT status FROM sms_bills WHERE merchant_key='hdbloan'").fetchone()[0] == "expired")

section("Upgrading from the spend version")
db.execute("CREATE TABLE sms_events (id INTEGER PRIMARY KEY, reminder_id TEXT)")
db.execute("INSERT INTO reminders (id, note_id, title, source) VALUES ('old1', ?, 'Pay Debit ₹10,000', 'user')", (host["id"],))
db.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('sms_watermark','123'), ('sms_host_note', ?)", (host["id"],))
db.commit()
sms.ensure(db); db.commit()
check("old tables are dropped, their reminders removed, and the watermark reset so bills are re-read with the new rules",
      not db.execute("SELECT 1 FROM sqlite_master WHERE name='sms_events'").fetchone()
      and db.execute("SELECT COUNT(*) FROM reminders WHERE id='old1'").fetchone()[0] == 0
      and db.execute("SELECT 1 FROM settings WHERE key='sms_watermark'").fetchone() is None)
sms.forget_all(db); db.commit()

# ───────────────────────────── the real daemon hook
section("Daemon hook")
from memos_daemon.config import DEFAULT_CONFIG
from memos_daemon.daemon import MemosDaemon

dcfg = dict(DEFAULT_CONFIG)
dcfg.update(db_path=dbp, backend="quicksilver", state_file=os.path.join(tmp, "state.json"),
            pending_writes_file=os.path.join(tmp, "pending"), todo_file=os.path.join(tmp, "todos.json"),
            thermal_enabled=False, embed_model="", engine="auto", model_path="", calendar_enabled=False,
            dashboard_enabled=False, connections_enabled=False, todo_enabled=False)
logging.disable(logging.CRITICAL)
d = MemosDaemon(dcfg)
logging.disable(logging.NOTSET)
d.gemma = None
sms.forget_all(db); db.commit()


def setting(k):
    r = db.execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone()
    return r[0] if r else None


def set_pref(**kw):
    p = json.loads(setting("user_prefs") or "{}"); p.update(kw)
    db.execute("INSERT INTO settings (key,value) VALUES ('user_prefs',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(p),))
    db.commit()


calls = {"n": 0, "mode": "ok"}


def fake_run(args, timeout=40):
    calls["n"] += 1
    if calls["mode"] == "missing":
        return 127, "", "termux-sms-list not found"
    if calls["mode"] == "denied":
        return 1, "", "Permission Denial: READ_SMS requires android.permission.READ_SMS"
    if calls["mode"] == "garbage":
        return 0, "not json", ""
    off = int(args[args.index("-o") + 1]); lim = int(args[args.index("-l") + 1])
    recent = [dict(m, received=(datetime.now() - timedelta(hours=i + 1)).strftime("%Y-%m-%d %H:%M:%S")) for i, m in enumerate(LIVE[:5])]
    return 0, json.dumps(recent[off:off + lim]), ""


d._sms_run = fake_run
d._broadcast_spend = lambda: calls.setdefault("pushed", 0) or calls.__setitem__("pushed", 1)

# The property that keeps this section from expiring again: every bill the
# hook will see is due in the FUTURE relative to the real clock. Without it
# the section still runs, still reads three bills, and quietly stops making
# reminders -- which is how it failed for a week before anyone looked.
for _m in LIVE:
    _ev, _ = sms.parse(_m["body"], _m["sender"], datetime.now())
    if _ev:
        check(f"the hook's {_ev['merchant']} bill is still due in the future",
              _ev["due_date"] > datetime.now().strftime("%Y-%m-%d"), _ev["due_date"])

d._maybe_sms()
check("off by default: nothing is read", calls["n"] == 0 and db.execute("SELECT COUNT(*) FROM sms_bills").fetchone()[0] == 0)

set_pref(sms="on")
d._maybe_sms()
st = json.loads(setting("sms_status") or "{}")
check("switched on: reads, records, reports ok", st.get("state") == "ok" and st.get("added", 0) >= 2 and calls["n"] >= 1, str(st))
check("watermark saved", int(float(setting("sms_watermark") or 0)) > 0)
check("the app is told something changed", calls.get("pushed") == 1)
n_before = calls["n"]
d._maybe_sms()
check("not due again for a few minutes", calls["n"] == n_before)
db.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('sms_scan_requested','1')"); db.commit()
d._maybe_sms()
check("Scan now forces a read and clears the request", calls["n"] > n_before and setting("sms_scan_requested") == "0")
rows_now = db.execute("SELECT COUNT(*) FROM sms_bills").fetchone()[0]
db.execute("UPDATE settings SET value='0' WHERE key='sms_last_poll'"); db.commit()
d._maybe_sms()
check("repeat reads are idempotent", db.execute("SELECT COUNT(*) FROM sms_bills").fetchone()[0] == rows_now)

for mode, want in (("missing", "no_termux_api"), ("denied", "no_permission"), ("garbage", "error")):
    calls["mode"] = mode
    db.execute("UPDATE settings SET value='0' WHERE key='sms_last_poll'"); db.commit()
    d._maybe_sms()
    check(f"{mode}: status says {want}", json.loads(setting("sms_status")).get("state") == want, setting("sms_status"))
calls["mode"] = "ok"

set_pref(sms="off")
db.execute("UPDATE settings SET value='0' WHERE key='sms_last_poll'"); db.commit()
n0 = calls["n"]; d._maybe_sms()
check("switched off again: stops reading", calls["n"] == n0 and json.loads(setting("sms_status")).get("state") == "off")

section("Bridge file (agent inside Fedora, Termux outside)")
import time as _t, tempfile as _tf
bf = os.path.join(tmp, "bridge.json")
real_run = MemosDaemon._sms_run
d.cfg["sms_file"] = bf
d._sms_run = lambda a, timeout=40: real_run(d, a, timeout)
code, out, err = d._sms_run(["-t", "inbox", "-l", "100", "-o", "0"])
check("missing bridge file is reported, not crashed on", code == 127 and "bridge file not found" in err, err)
open(bf + ".err", "w").write("Permission Denial: READ_SMS")
code, out, err = d._sms_run(["-o", "0"])
check("the bridge's own error text is passed through", code == 1 and "Permission" in err, err)
os.remove(bf + ".err")
json.dump([dict(m, received=(datetime.now() - timedelta(hours=i + 1)).strftime("%Y-%m-%d %H:%M:%S")) for i, m in enumerate(LIVE[:5])], open(bf, "w"))
check("page one is the file, later pages are empty", len(json.loads(d._sms_run(["-o", "0"])[1])) == 5 and d._sms_run(["-o", "100"])[1] == "[]")
sms.forget_all(db); db.commit(); set_pref(sms="on")
db.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('sms_last_poll','0')"); db.commit()
d._maybe_sms()
check("scan works from the bridge file", json.loads(setting("sms_status")).get("state") == "ok" and db.execute("SELECT COUNT(*) FROM sms_bills").fetchone()[0] >= 3, setting("sms_status"))
os.utime(bf, (_t.time() - 7200, _t.time() - 7200))
db.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('sms_last_poll','0')"); db.commit()
d._maybe_sms()
check("a bridge that stopped writing is flagged", json.loads(setting("sms_status")).get("state") == "error" and "last wrote" in setting("sms_status"), setting("sms_status"))
os.remove(bf)
db.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('sms_last_poll','0')"); db.commit()
d._maybe_sms()
check("no file at all says the bridge isn't running", json.loads(setting("sms_status")).get("state") == "no_bridge", setting("sms_status"))
d.cfg["sms_file"] = ""
d._sms_run = fake_run

section("Forgetting")
set_pref(sms="on")
db.execute("UPDATE settings SET value='0' WHERE key='sms_last_poll'"); db.commit()
d._maybe_sms()
check("there is data before forgetting", db.execute("SELECT COUNT(*) FROM sms_bills").fetchone()[0] > 0
      and db.execute("SELECT COUNT(*) FROM reminders WHERE source='user'").fetchone()[0] >= 1)
sms.forget_all(db); db.commit()
check("forget wipes bills, their reminders and the watermark",
      db.execute("SELECT COUNT(*) FROM sms_bills").fetchone()[0] == 0
      and db.execute("SELECT COUNT(*) FROM reminders WHERE source='user'").fetchone()[0] == 0
      and setting("sms_watermark") is None)


section("Stored text: masked, and filled in for older bills")
ev, _ = sms.parse("Your ICICI credit card 4111111111111111 bill of Rs 250000 is due on 21-11-2026. Ref 987654321098.", "VM-ICICIT", NOW)
check("a long number is masked in the text we keep (last four only)", ev and "4111111111111111" not in ev["body"] and "••••••••••••1111" in ev["body"] and "987654321098" not in ev["body"], str(ev and ev["body"]))
check("short amounts and dates are left alone", ev and "250000" in ev["body"] and "21-11-2026" in ev["body"])
sms.forget_all(db); db.commit()
m1 = msg("Your ICICI credit card bill of Rs 250000 is due on 21-11-2026.", "VM-ICICIT", when="2026-11-05 10:00:00")
sms.scan(db, [m1], {"days_back": 60}, NOW); db.commit()
db.execute("UPDATE sms_bills SET body=''"); db.commit()                      # a bill stored before text was kept
st = sms.scan(db, [m1], {"days_back": 60}, NOW); db.commit()
rows = db.execute("SELECT body FROM sms_bills").fetchall()
check("an older open bill gets its message text when the inbox is read again, and is not duplicated",
      len(rows) == 1 and "250000" in rows[0]["body"] and st.get("backfilled") == 1, str([tuple(r) for r in rows]))
m1_live = msg(f"Your ICICI credit card bill of Rs 250000 is due on {_due(4)}.", "VM-ICICIT")
set_pref(sms="on"); LIVE_SAVE = list(LIVE); LIVE[:] = [m1_live]; calls["mode"] = "ok"
db.execute("UPDATE settings SET value='0' WHERE key='sms_last_poll'"); db.commit()
d._maybe_sms()
db.execute("UPDATE sms_bills SET body=''"); sms._set_setting(db, "sms_watermark", "999"); sms._set_setting(db, "sms_body_backfill", "")
sms._set_setting(db, "sms_last_poll", "0"); db.commit()
n0 = calls["n"]; d._maybe_sms()
check("the first poll after the upgrade starts from the beginning once when open bills have no text",
      setting("sms_body_backfill") == "1" and setting("sms_watermark") not in ("999", None), str(setting("sms_watermark")))
sms._set_setting(db, "sms_watermark", "999"); sms._set_setting(db, "sms_last_poll", "0"); db.commit()
d._maybe_sms()
check("and only once", setting("sms_watermark") != "0" and calls["n"] > n0)
LIVE[:] = LIVE_SAVE

print(f"\n{_ok} passed, {_bad} failed")
sys.exit(1 if _bad else 0)
