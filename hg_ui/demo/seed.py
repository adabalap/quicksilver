"""Made-up sample data and a stand-in agent for the demo video. Nothing here is real personal data."""
import hashlib, json, os, random, sqlite3, string, threading, time, urllib.request
from datetime import datetime, timedelta, timezone

def nid(n=8): return "".join(random.choice(string.ascii_letters + string.digits) for _ in range(n))
def iso(dt): return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
def post(base, path, body):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    return json.load(urllib.request.urlopen(req))

NOTES = [  # body, title, summary, tags, hours_ago, actions, reminders[(title, hint, resolved)], note_type
 ("Hampi in December!! hire a coracle, sunrise at Matanga hill, carry cash (no UPI there), book the stay by 6 Dec",
  "Hampi trip, December", "A December trip to Hampi: coracle ride, sunrise at Matanga hill, cash needed, stay to be booked by 6 December.",
  ["travel", "hampi"], 20, ["Book the stay", "Carry cash"], [("Book the Hampi stay", "6 Dec", "2026-12-06T10:00")], "text"),
 ("Book idea: a lighthouse keeper who collects other people's lost voicemails. Each chapter is one message.",
  "The lighthouse keeper's voicemails", "A story idea: a lighthouse keeper collects strangers' lost voicemails, one per chapter.",
  ["writing", "ideas"], 52, [], [], "text"),
 ("తొలకరి వాన మొదలైంది. మిద్దె మీద మట్టి వాసన, చిన్నప్పటి జ్ఞాపకాలు.",
  "తొలకరి వాన", "The first monsoon rain, the smell of earth on the terrace and childhood memories.",
  ["telugu", "poetry"], 30, [], [], "text"),
 ("Kitchen renovation: Ravi's quote 2.4L, start in Jan, ask about the chimney and the granite sample",
  "Kitchen renovation quote", "Ravi quoted ₹2.4 lakh for the kitchen, starting in January. Still to ask about the chimney and the granite sample.",
  ["home", "kitchen"], 75, ["Ask Ravi about the chimney", "See the granite sample"], [], "text"),
 ("- [ ] milk\n- [ ] eggs\n- [ ] coriander\n- [x] bread\n- [ ] tomatoes",
  "Groceries", "Groceries: milk, eggs, coriander and tomatoes still to buy.", ["groceries"], 6, [], [], "checklist"),
 ("Fedora on Termux: proot-distro login, dnf install python3, keep the agent in a tmux pane. Remember to back up notes.db weekly.",
  "Fedora on Termux setup", "Notes on running Fedora in proot on Termux with tmux, plus a weekly notes.db backup.",
  ["tech", "fedora"], 100, ["Back up notes.db weekly"], [], "text"),
]

def enrich(db, note_id, title, summary, tags, actions, rems, status="approved"):
    db.execute("INSERT INTO enrichments (id, note_id, title, summary, confidence, priority) VALUES (?,?,?,?,?,?)",
               (nid(10), note_id, title, summary, 0.93, "medium"))
    for t in tags: db.execute("INSERT OR IGNORE INTO tags (note_id, tag) VALUES (?,?)", (note_id, t))
    for a in actions: db.execute("INSERT INTO action_items (id, note_id, text) VALUES (?,?,?)", (nid(10), note_id, a))
    for (t, hint, res) in rems:
        db.execute("INSERT INTO reminders (id, note_id, title, datetime_hint, datetime_resolved, source) VALUES (?,?,?,?,?, 'agent')",
                   (nid(10), note_id, t, hint, res))
    body = db.execute("SELECT body FROM notes WHERE id=?", (note_id,)).fetchone()[0]
    db.execute("INSERT INTO note_state (note_id, status, body_hash) VALUES (?,?,?) ON CONFLICT(note_id) DO UPDATE SET status=excluded.status, body_hash=excluded.body_hash",
               (note_id, status, hashlib.sha256(body.encode()).hexdigest()[:16]))
    db.commit()

def seed(base, dbpath):
    db = sqlite3.connect(dbpath); db.row_factory = sqlite3.Row
    now = datetime.now(timezone.utc)
    ids = []
    for body, title, summ, tags, hrs, acts, rems, ntype in NOTES:
        n = post(base, "/api/notes", {"body": body, "note_type": ntype})["id"]
        ids.append(n)
        enrich(db, n, title, summ, tags, acts, rems)
        db.commit()
    # related pairs
    for a, b, s in ((ids[0], ids[1], .5), (ids[3], ids[4], .48), (ids[1], ids[2], .46)):
        db.execute("INSERT OR REPLACE INTO relationships (note_id_a, note_id_b, score) VALUES (?,?,?)", (a, b, s))
    db.commit()
    # bills: three open, with their reminders on a protected host note
    post(base, "/api/settings", {"sms": "on"})
    host = nid(8)
    db.execute("INSERT INTO notes (id, body, visibility, protected, enrich_optout, no_actions, no_reminders) VALUES (?,?,?,1,1,1,1)",
               (host, "Bills picked up from your messages.", "private"))
    db.execute("INSERT INTO note_state (note_id, status, body_hash) VALUES (?, 'approved', 'x')", (host,))
    db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('sms_host_note', ?)", (host,))
    today = datetime.now().date()
    bills = [("ACT Fibernet", 999, 1, "ACT Fibernet: Rs 999.00 is due on {d}. Pay at https://pay.example.com/act to avoid disconnection."),
             ("HDFC credit card", 12480, 3, "HDFC Bank Credit Card: total due Rs 12,480.00, due {d}. Pay via NetBanking or https://pay.example.com/card ."),
             ("BESCOM electricity", 1840, 6, "BESCOM: bill of Rs 1,840.00 for Oct is due {d}. Pay at https://pay.example.com/power .")]
    for m, amt, days, text in bills:
        due = today + timedelta(days=days)
        rids = []
        for when, label in ((due - timedelta(days=2), "due in 2 days"), (due, "due today")):
            if when >= today:
                rid = nid(10); stamp = f"{when.isoformat()}T09:00"
                db.execute("INSERT INTO reminders (id, note_id, title, datetime_hint, datetime_resolved, description, source) VALUES (?,?,?,?,?,?, 'user')",
                           (rid, host, f"Pay {m} ₹{amt:,} · {label}", stamp, stamp, text.format(d=due.strftime('%d %b'))))
                rids.append(rid)
        db.execute("INSERT INTO sms_bills (key, received_at, sender, merchant, merchant_key, amount, account, due_date, reminder_ids, body) VALUES (?,?,?,?,?,?,?,?,?,?)",
                   ("sms-" + nid(10), datetime.now().isoformat(timespec="seconds"), "AD-" + m.split()[0].upper()[:6], m, m.lower(), amt, "", due.isoformat(), json.dumps(rids), text.format(d=due.strftime('%d %b'))))
    db.execute("UPDATE notes SET hidden=1 WHERE id=?", (host,))
    for (body, title, summ, tags, hrs, acts, rems, ntype), n in zip(NOTES, ids):
        t = iso(now - timedelta(hours=hrs))
        db.execute("UPDATE notes SET created_at=?, updated_at=? WHERE id=?", (t, t, n))
    db.commit(); db.close()
    return ids

class StandInAgent(threading.Thread):
    """Heartbeat (so the app shows the agent live) and instant answers to Ask, written the same way the real agent does."""
    def __init__(self, base, dbpath):
        super().__init__(daemon=True); self.base, self.dbpath, self.stop = base, dbpath, False
    def run(self):
        db = sqlite3.connect(self.dbpath, timeout=10); db.row_factory = sqlite3.Row
        while not self.stop:
            try:
                db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('agent_heartbeat', ?)", (datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),))
                db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('agent_telemetry', ?)", (json.dumps({"queue_depth": 0, "thermal_state": "cool"}),))
                db.commit()
                for r in db.execute("SELECT id, question, context_json FROM ask_requests WHERE status='pending'").fetchall():
                    ctx = json.loads(r["context_json"] or "[]")
                    if not ctx: continue
                    time.sleep(1.6)
                    q = r["question"].lower(); ans = "Ask about the tap washer and the geyser thermostat, and keep ₹2,000 in cash ready [1]."
                    if "lighthouse" in q or "book" in q or "story" in q:
                        ans = "You imagined a lighthouse keeper who collects strangers' lost voicemails, with each chapter built around one message [1]."
                    elif "hampi" in q:
                        ans = "Book the stay by 6 December. Hire a coracle, catch sunrise at Matanga hill, and carry cash because UPI does not work there [1]."
                    post(self.base, "/api/ask/answer", {"request_id": r["id"], "status": "done", "cited_note_ids": [ctx[0]["id"]], "answer": ans})
            except Exception as e:
                print("stand-in agent:", e)
            time.sleep(1)

MESSY = ("mtg w priya tues 11am re launch deck. need pricing slide + ask dev abt demo env. send acme invoice by fri!! "
         "mums bday sun - dinner 8pm, book table + order cake. also plumber thurs after 5")
POLISHED = ("Launch prep\n- Meeting with Priya, Tuesday 11 am, about the launch deck.\n- Add the pricing slide.\n- Ask the dev team about the demo environment.\n\n"
            "Money\n- Send the Acme invoice by Friday.\n\nFamily\n- Mum's birthday on Sunday: dinner at 8 pm. Book a table and order the cake.\n\n"
            "Home\n- Plumber on Thursday after 5.")

def _next(weekday, hh, mm=0):
    d = datetime.now(); days = (weekday - d.weekday()) % 7 or 7
    return (d + timedelta(days=days)).strftime("%Y-%m-%d") + f"T{hh:02d}:{mm:02d}"

def enrich_typed(base, dbpath, note_id, status="pending"):
    db = sqlite3.connect(dbpath, timeout=10)
    enrich(db, note_id, "Launch prep, invoice and Mum's birthday",
           "A busy week: the launch deck meeting with Priya on Tuesday, the Acme invoice due Friday, Mum's birthday dinner on Sunday and the plumber on Thursday.",
           ["work", "family", "home"],
           ["Add the pricing slide", "Ask dev about the demo environment", "Send the Acme invoice", "Book a table for Mum's birthday", "Order the cake"],
           [("Meeting with Priya: launch deck", "Tuesday 11am", _next(1, 11)), ("Acme invoice due", "Friday", _next(4, 9)),
            ("Mum's birthday dinner", "Sunday 8pm", _next(6, 20)), ("Plumber visit", "Thursday after 5", _next(3, 17))], status=status)
    db.execute("UPDATE enrichments SET polished=? WHERE note_id=?", (POLISHED, note_id)); db.commit(); db.close()
    post(base, "/api/webhook", {"note_id": note_id, "event": "note_enriched"})
