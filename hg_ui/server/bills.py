"""Bills from messages: read-only views over what the agent derived from bill SMS, plus the switches.

The agent (hg_agent/memos_daemon/sms.py) reads messages through Termux:API and writes `sms_bills`, plus two
reminders per bill (two days before and on the day, 09:00). This module lists open bills, lets you mark one
paid, and holds scan-now and forget-everything. The text of a bill message is kept only on that bill's reminders, and is cleared when the bill is paid.
"""
import json
from datetime import datetime, timedelta

from flask import jsonify, request

from .core import _broadcast, app, get_db
from .settings import _get_prefs

OVERDUE_SHOW_DAYS = 14


def _setting(db, key, default=""):
    r = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def _put(db, key, value):
    db.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (key, value))


def _enabled(db):
    return _get_prefs(db).get("sms", "off") == "on"


def open_bills(db, today=None, limit=30, window=True):
    """Open bills due this month or still overdue (up to two weeks), soonest first. window=False lists every open bill."""
    today = today or datetime.now().date()
    lo = (today - timedelta(days=OVERDUE_SHOW_DAYS)).isoformat()
    month_end = (today.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    soon = max(month_end, today + timedelta(days=7)).isoformat()
    if not window:
        lo, soon = "0000-01-01", "9999-12-31"
    out = []
    for r in db.execute("SELECT id, key, merchant, amount, due_date, account, reminder_ids, body FROM sms_bills "
                        "WHERE status='open' AND due_date BETWEEN ? AND ? ORDER BY due_date, id LIMIT ?",
                        (lo, soon, limit)):
        try:
            days = (datetime.strptime(r["due_date"], "%Y-%m-%d").date() - today).days
        except ValueError:
            continue
        note_id = None
        for rid in json.loads(r["reminder_ids"] or "[]")[:1]:
            n = db.execute("SELECT note_id FROM reminders WHERE id=?", (rid,)).fetchone()
            note_id = n["note_id"] if n else None
        out.append({"id": r["id"], "merchant": r["merchant"], "amount": r["amount"], "due_date": r["due_date"],
                    "days": days, "overdue": days < 0, "note_id": note_id, "body": r["body"] or "",
                    "manual": str(r["key"]).startswith("manual-")})
    return out


@app.route("/api/bills", methods=["GET"])
def bills():
    db = get_db()
    try:
        st = json.loads(_setting(db, "sms_status", "") or "{}")
    except Exception:
        st = {}
    allb = request.args.get("all") == "1"
    on = _enabled(db)
    items = open_bills(db, window=not allb)
    if not on:
        items = [b for b in items if b["manual"]]
    return jsonify({"enabled": on, "status": st, "bills": items})


@app.route("/api/bills", methods=["POST"])
def bill_add():
    """Add a bill by hand: merchant, amount, due date. Two reminders (two days before, and the day) like a message bill."""
    from .core import _nanoid
    import hashlib
    d = request.get_json(silent=True) or {}
    merchant = " ".join(str(d.get("merchant") or "").split())[:60]
    try:
        amount = round(float(d.get("amount") or 0), 2)
        due = datetime.strptime(str(d.get("due_date") or ""), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return jsonify({"error": "bad input", "message": "Need a name, an amount and a due date."}), 400
    if not merchant or amount < 0:
        return jsonify({"error": "bad input", "message": "Need a name, an amount and a due date."}), 400
    db = get_db()
    host = _setting(db, "sms_host_note", "")
    if not host or not db.execute("SELECT 1 FROM notes WHERE id=?", (host,)).fetchone():
        host = _nanoid(8)
        body = ("Bills picked up from your messages.\n\nEach reminder on this note came from a bill message, and shows "
                "that message until the bill is paid.")
        db.execute("INSERT INTO notes (id, body, visibility, protected, enrich_optout, no_actions, no_reminders) "
                   "VALUES (?,?,?,1,1,1,1)", (host, body, "private"))
        db.execute("INSERT INTO note_state (note_id, status, body_hash) VALUES (?,?,?)",
                   (host, "approved", hashlib.sha1(body.encode()).hexdigest()[:16]))
        _put(db, "sms_host_note", host)
    amt = f"₹{amount:,.0f}" if amount == int(amount) else f"₹{amount:,.2f}"
    now = datetime.now()
    rids = []
    for when, label in ((due - timedelta(days=2), "due in 2 days"), (due, "due today")):
        stamp = f"{when.isoformat()}T09:00"
        if datetime.strptime(stamp, "%Y-%m-%dT%H:%M") < now - timedelta(hours=12):
            continue
        rid = _nanoid(10)
        db.execute("INSERT INTO reminders (id, note_id, title, datetime_hint, datetime_resolved, description, source) "
                   "VALUES (?,?,?,?,?,?, 'user')", (rid, host, f"Pay {merchant} {amt} · {label}", stamp, stamp, ""))
        rids.append(rid)
    key = "manual-" + _nanoid(10)
    db.execute("INSERT INTO sms_bills (key, received_at, sender, merchant, merchant_key, amount, account, due_date, reminder_ids, body) "
               "VALUES (?,?,?,?,?,?,?,?,?,?)",
               (key, now.isoformat(timespec="seconds"), "you", merchant, merchant.lower(), amount, "", due.isoformat(), json.dumps(rids), ""))
    db.commit()
    _broadcast("bills_changed", {})
    _broadcast("reminders_changed", {})
    return jsonify({"ok": True})


@app.route("/api/bills/<int:bid>/paid", methods=["POST"])
@app.route("/api/bills/<int:bid>/dismiss", methods=["POST"])
def bill_close(bid):
    """Paid, or 'not a bill': the bill leaves the list, its reminders are dismissed and its message text is deleted.
    The row stays (without text) so the same message is never turned into a bill again."""
    db = get_db()
    row = db.execute("SELECT id, reminder_ids FROM sms_bills WHERE id=?", (bid,)).fetchone()
    if not row:
        return jsonify({"error": "not found"}), 404
    for rid in json.loads(row["reminder_ids"] or "[]"):
        db.execute("UPDATE reminders SET dismissed=1, description='' WHERE id=?", (rid,))
    db.execute("UPDATE sms_bills SET status=?, body='' WHERE id=?", ("dismissed" if request.path.endswith("/dismiss") else "paid", bid))
    db.commit()
    _broadcast("bills_changed", {})
    _broadcast("jobs_changed", {})
    return jsonify({"ok": True})


@app.route("/api/sms/scan", methods=["POST"])
def sms_scan():
    db = get_db()
    if not _enabled(db):
        return jsonify({"error": "off", "message": "Switch it on first."}), 409
    _put(db, "sms_scan_requested", "1")
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/sms/forget", methods=["POST"])
def sms_forget():
    """Delete every bill read from messages, their reminders, and the note that holds them."""
    db = get_db()
    for r in db.execute("SELECT reminder_ids FROM sms_bills").fetchall():
        for rid in json.loads(r["reminder_ids"] or "[]"):
            db.execute("DELETE FROM reminders WHERE id=?", (rid,))
    db.execute("DELETE FROM sms_bills")
    host = _setting(db, "sms_host_note", "")
    if host:
        left = db.execute("SELECT COUNT(*) FROM reminders WHERE note_id=?", (host,)).fetchone()[0]
        if not left:
            db.execute("DELETE FROM notes WHERE id=? AND protected=1 AND enrich_optout=1", (host,))
            db.execute("DELETE FROM settings WHERE key='sms_host_note'")
    for k in ("sms_watermark", "sms_status", "sms_last_poll", "sms_scan_requested"):
        db.execute("DELETE FROM settings WHERE key=?", (k,))
    db.commit()
    _broadcast("bills_changed", {})
    _broadcast("jobs_changed", {})
    return jsonify({"ok": True})
