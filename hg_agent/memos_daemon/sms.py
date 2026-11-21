"""sms.py — bill messages → reminders. Pure functions over the SQLite database.

Why it exists: utility, card and loan messages already say what a person would otherwise type into a note
("electricity bill ₹2,140 due 5 Nov"). This module reads them on the phone and keeps only the derived bill.

What it does
  * Finds bills (an amount, a due date, bill wording) and makes two reminders per bill: two days before and on
    the day, at 09:00.
  * Closes a bill by itself when a later message says the same amount was paid, and drops it after it has been
    overdue for two weeks.

What it keeps
  * The bill (merchant, amount, due date, last four digits of an account) and, on the bill's reminders, the
    message text, so the reminder shows the details and any payment link. The text is cleared as soon as the bill
    is paid, expires, or you press Forget.

What it never does
  * Store an OTP, or any message that is not a bill. Payment alerts are read only to close a bill and are dropped.
  * Track spending. Payment messages are used only to recognise that a bill was paid, and are not kept.
  * Call the model. Rules parse the regular templates: faster, cooler and exactly repeatable.

Flow: `scan()` takes raw messages (the JSON shape `termux-sms-list` prints) and returns counts. The daemon part
(parts/sms_source.py) only runs the command, tracks a watermark and reports status.
"""
from __future__ import annotations
import hashlib
import json
import re
import secrets
from datetime import datetime, timedelta

LEAD_DAYS = 2                  # the early reminder
REMIND_HOUR = "09:00"          # local time both reminders fire
OVERDUE_KEEP_DAYS = 14         # an unpaid bill stays visible this long after its due date

SMS_BILLS = """
CREATE TABLE IF NOT EXISTS sms_bills (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    key          TEXT NOT NULL UNIQUE,            -- hash of sender + time + text; one message → one bill
    received_at  TEXT NOT NULL,
    sender       TEXT NOT NULL DEFAULT '',        -- the bank/utility header (AD-BESCOM), never a phone number
    merchant     TEXT NOT NULL DEFAULT '',
    merchant_key TEXT NOT NULL DEFAULT '',
    amount       REAL NOT NULL DEFAULT 0,
    account      TEXT NOT NULL DEFAULT '',        -- last four digits only
    due_date     TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','paid','expired','dismissed')),
    reminder_ids TEXT NOT NULL DEFAULT '[]',
    body         TEXT NOT NULL DEFAULT '',        -- the bill message, kept only while the bill is open
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
)"""
_OLD_TABLES = ("sms_events", "sms_merchant_cat", "sms_skipped")


def ensure(db) -> None:
    """Create the bills table. The earlier spend tables (v1.4) are removed, with the reminders they made."""
    db.execute(SMS_BILLS)
    try:
        db.execute("ALTER TABLE sms_bills ADD COLUMN body TEXT NOT NULL DEFAULT ''")
    except Exception:
        pass                                                       # already there
    db.execute("CREATE INDEX IF NOT EXISTS idx_sms_bills_due ON sms_bills(due_date, status)")
    old = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sms_events'").fetchone()
    if old:
        host = _get_setting(db, "sms_host_note", "")
        if host:
            db.execute("DELETE FROM reminders WHERE note_id=?", (host,))
        for t in _OLD_TABLES:
            db.execute(f"DROP TABLE IF EXISTS {t}")
        for k in ("sms_watermark", "sms_status", "sms_last_poll", "sms_config"):
            db.execute("DELETE FROM settings WHERE key=?", (k,))


# ───────────────────────────── text helpers

def _nanoid(n: int = 10) -> str:
    return secrets.token_urlsafe(n)[:n]


def _mkey(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())[:40]


def _set_setting(db, key: str, value: str) -> None:
    db.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
               "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def _get_setting(db, key: str, default: str = "") -> str:
    r = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


# ───────────────────────────── message classification

_OTP_RE = re.compile(
    r"\b(otp|one[- ]time (?:password|pin|code)|verification code|security code|do not share|never share|"
    r"cvv|login code|passcode|mpin|upi pin)\b", re.I)
_PROMO_RE = re.compile(
    r"\b(offer|win|congratulations|discount|% ?off|flat \d+|coupon|voucher|sale|apply now|pre-?approved|"
    r"loan up to|limited time|t&c|click|visit|download the app|install|cashback up to|lowest price)\b", re.I)
# A bill message talks about something still to be paid …
_DUE_RE = re.compile(r"\b(due|payable|pay by|pay before|last date|minimum amount|min\.? due|outstanding|overdue|"
                     r"will be (?:auto[- ]?)?debited|to be (?:auto[- ]?)?debited|auto[- ]?debit|mandate)\b", re.I)
_BILLISH_RE = re.compile(r"\b(bill|premium|emi|statement|instal?ment|renewal|subscription|membership|recharge|loan|policy|due|"
                         r"auto[- ]?debit|mandate|sip)\b", re.I)
_FUTURE_DEBIT_RE = re.compile(r"\b(will be (?:auto[- ]?)?debited|to be (?:auto[- ]?)?debited|auto[- ]?debit|mandate)\b", re.I)
# … while a paid message talks about something already done.
_DONE_RE = re.compile(r"\b(debited|paid|received|credited|successful(?:ly)?|thank you for (?:your )?payment|"
                      r"payment of .* (?:received|successful)|has been (?:paid|debited|credited|received))\b", re.I)
_PAID_RE = re.compile(r"\b(debited|paid|payment (?:of|received|successful)|successfully (?:paid|debited)|"
                      r"thank you for (?:your )?payment|has been (?:paid|debited|received))\b", re.I)

_AMOUNT_RE = re.compile(r"(?:rs\.?|inr|₹)\s*([\d,]+(?:\.\d{1,2})?)", re.I)
_AMOUNT_AFTER_RE = re.compile(r"([\d,]+(?:\.\d{1,2})?)\s*(?:rs\.?|inr|₹)(?![a-z])", re.I)
_BAL_BEFORE_RE = re.compile(r"(?:avl|avail(?:able)?|bal(?:ance)?|limit|total)\.?\s*(?:bal\w*|limit)?\s*[:\-]?\s*$", re.I)
_ACCOUNT_RE = re.compile(r"(?:a/?c|acct|account|card|policy|ac no\.?)\s*(?:no\.?\s*)?(?:ending|xx+|x+|\*+|#)?\s*"
                         r"(?:[xX*]+)?(\d{3,4})\b", re.I)
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
_DATE_NUM_RE = re.compile(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})\b")
_DATE_MON_RE = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?[\s\-/]*([A-Za-z]{3,9})[\s\-/,]*(\d{2,4})?\b")

_MERCHANT_PATTERNS = (
    re.compile(r"\b(?:your|ur)\s+([A-Za-z][A-Za-z0-9 .&'_-]{2,28}?)\s+(?:bill|emi|premium|statement|policy|subscription|membership|plan|renewal|"
               r"postpaid|prepaid|broadband|account)\b", re.I),
    re.compile(r"\b(?:to|towards|for)\s+([A-Za-z][A-Za-z0-9 .&'*_-]{2,32}?)(?=\s+(?:on|via|using|ref|txn|avl|bal|"
               r"upi|dated|from|a/?c|\d{1,2}[-/])|[.,;]|$)", re.I),
    re.compile(r"\b(?:at|@)\s+([A-Za-z0-9][A-Za-z0-9 .&'*_-]{1,32}?)(?=\s+(?:on|via|using|ref|txn|avl|bal|"
               r"upi|dated|for|from|\d{1,2}[-/])|[.,;]|$)", re.I),
)
_MERCHANT_NOISE = re.compile(r"\b(?:your|the|a/?c|account|bank|ltd|limited|pvt|private|india|online|"
                             r"payment|txn|transaction)\b", re.I)
# Words that are never a merchant name. A fragment like "Debit" or "of INR 56" falls back to the sender's name.
_JUNK_MERCHANT = re.compile(r"^(?:of|inr|rs|payment|debit|credit|bill|due|amount|a/?c|account|your|the|dear|"
                            r"customer|total|min|minimum|date|on|by|rs\.?|\d)", re.I)


def is_otp(body: str) -> bool:
    return bool(_OTP_RE.search(body or ""))


def _num(s: str) -> float:
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return 0.0


def _first_amount(body: str) -> float:
    """First rupee amount that is not a balance or limit figure."""
    for rx in (_AMOUNT_RE, _AMOUNT_AFTER_RE):
        for m in rx.finditer(body):
            if _BAL_BEFORE_RE.search(body[max(0, m.start() - 24):m.start()]):
                continue
            v = _num(m.group(1))
            if v > 0:
                return v
    return 0.0


def _parse_date(body: str, ref: datetime) -> str | None:
    """The due date inside a bill message, as YYYY-MM-DD. Missing years take the next occurrence."""
    best = None
    for m in _DATE_NUM_RE.finditer(body):
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000
        if 1 <= d <= 31 and 1 <= mo <= 12 and 2000 <= y <= 2100:
            try:
                best = datetime(y, mo, d).date()
                break
            except ValueError:
                continue
    if best is None:
        for m in _DATE_MON_RE.finditer(body):
            mon = _MONTHS.get(m.group(2)[:3].lower())
            if not mon or not m.group(2).lower().startswith(m.group(2)[:3].lower()):
                continue
            d = int(m.group(1))
            y = m.group(3)
            if not 1 <= d <= 31:
                continue
            try:
                if y:
                    yy = int(y) + (2000 if len(y) == 2 else 0)
                    best = datetime(yy, mon, d).date()
                else:
                    cand = datetime(ref.year, mon, d).date()
                    if cand < (ref - timedelta(days=45)).date():
                        cand = datetime(ref.year + 1, mon, d).date()
                    best = cand
                break
            except ValueError:
                continue
    return best.isoformat() if best else None


def _clean_merchant(raw: str) -> str:
    s = re.sub(r"\s+", " ", raw or "").strip(" .,-*/_")
    s = re.split(r"\s+(?:ref|txn|upi|avl|bal)\b", s, flags=re.I)[0]
    if "@" in s:                                    # a UPI id: keep the readable handle
        s = s.split("@")[0]
        s = re.sub(r"[._\-]+", " ", s)
        s = re.sub(r"\d{5,}", "", s)
    s = _MERCHANT_NOISE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip(" .,-*/_")
    if len(s) < 3 or re.fullmatch(r"[\d\W_]+", s) or _JUNK_MERCHANT.match(s):
        return ""
    return s.title() if s.isupper() or s.islower() else s


def _merchant(body: str) -> str:
    for rx in _MERCHANT_PATTERNS:
        m = rx.search(body)
        if m:
            c = _clean_merchant(m.group(1))
            if c:
                return c[:32]
    return ""


_GENERIC_WORDS = {"electricity", "water", "gas", "broadband", "postpaid", "prepaid", "mobile", "phone", "credit",
                  "insurance", "policy", "loan", "emi", "rent", "premium", "sip", "dth", "internet", "card"}


_BANKS = (("icici", "ICICI"), ("hdfc", "HDFC"), ("sbi", "SBI"), ("axis", "Axis"), ("kotak", "Kotak"), ("yes", "Yes Bank"),
          ("idfc", "IDFC First"), ("pnb", "PNB"), ("bob", "Bank of Baroda"), ("canara", "Canara"), ("union", "Union Bank"),
          ("indus", "IndusInd"), ("hsbc", "HSBC"), ("citi", "Citi"), ("amex", "Amex"), ("rbl", "RBL"), ("au", "AU Bank"),
          ("sc", "Standard Chartered"), ("hdb", "HDB"), ("bajaj", "Bajaj"), ("tata", "Tata"))


def _sender_name(sender: str, body: str = "") -> str:
    name = (sender.split("-", 1)[1] if "-" in sender else "").split("-")[0]
    low = name.lower()
    for pre, pretty in _BANKS:
        if low.startswith(pre) and (pre not in ("au", "sc", "yes", "bob", "tata") or len(low) <= len(pre) + 3):
            kind = ("credit card" if re.search(r"credit card|card", body, re.I) else
                    "loan" if re.search(r"loan|emi", body, re.I) else "")
            return f"{pretty} {kind}".strip() if kind else ""      # a bank alone is not a payee: fall through to the wording
    if len(name) >= 4 and not re.search(r"(BK|BNK|INB|BANK|CARD)$", name, re.I):
        return name.title()
    return ""


def _mask(text: str) -> str:
    """The text we keep never holds a long number: 9 or more digits (account, card, reference) show only the last four."""
    return re.sub(r"\d{9,}", lambda m: "•" * (len(m.group()) - 4) + m.group()[-4:], text)


def parse(body: str, sender: str, received: datetime) -> tuple[dict | None, str]:
    """(bill, reason). bill is None when the message is not a bill; reason says why."""
    body = " ".join((body or "").split())
    if not body:
        return None, "empty"
    if is_otp(body):
        return None, "otp"
    if _PROMO_RE.search(body) and not _DUE_RE.search(body):
        return None, "promo"
    amount = _first_amount(body)
    if amount <= 0:
        return None, "no_amount"
    future = bool(_FUTURE_DEBIT_RE.search(body))
    if not _DUE_RE.search(body) or not _BILLISH_RE.search(body):
        return None, "not_a_bill"
    if _DONE_RE.search(body) and not future:
        return None, "already_paid"                  # "debited", "payment received": that is evidence, not a bill
    due = _parse_date(body, received)
    if not due:
        return None, "no_due_date"
    acct = _ACCOUNT_RE.search(body)
    merchant = _merchant(body)
    if not merchant or merchant.split()[0].lower() in _GENERIC_WORDS:
        merchant = _sender_name(sender, body) or merchant
    if not merchant:
        m = re.search(r"\b(electricity|water|gas|broadband|postpaid|credit card|insurance|dth|"
                      r"mobile|phone|loan|emi|rent|premium|sip)\b", body, re.I)
        merchant = m.group(1).title() if m else ("Auto-debit" if future else "Bill")
    return {"amount": amount, "merchant": merchant[:32], "merchant_key": _mkey(merchant),
            "account": acct.group(1)[-4:] if acct else "", "due_date": due, "body": _mask(body)[:600]}, ""


def payment_evidence(body: str) -> dict | None:
    """What a 'paid' message says, only to close a matching bill. Nothing from it is stored."""
    body = " ".join((body or "").split())
    if not body or is_otp(body) or _FUTURE_DEBIT_RE.search(body) or not _PAID_RE.search(body):
        return None
    amt = _first_amount(body)
    if amt <= 0:
        return None
    acct = _ACCOUNT_RE.search(body)
    return {"amount": amt, "hay": re.sub(r"[^a-z0-9]", "", body.lower()), "account": acct.group(1)[-4:] if acct else ""}


# ───────────────────────────── sender gate

_HEADER_RE = re.compile(r"^[A-Za-z]{2}-[A-Za-z0-9]{3,9}(?:-([A-Za-z]))?$")


def sender_ok(sender: str, cfg: dict | None = None) -> tuple[bool, str]:
    """Transactional headers (AX-HDFCBK, VM-SBIINB-S) pass; promotional (-P) and phone numbers do not."""
    s = (sender or "").strip()
    m = _HEADER_RE.match(s)
    if not m:
        return False, "personal_or_unknown_sender"
    if (m.group(1) or "").upper() == "P":
        return False, "promo_header"
    return True, ""


# ───────────────────────────── the scan

def _received(msg: dict, now: datetime) -> datetime:
    v = msg.get("received")
    try:
        if isinstance(v, (int, float)):
            ts = float(v) / (1000.0 if v > 1e11 else 1.0)
            return datetime.fromtimestamp(ts).replace(tzinfo=None)
        if isinstance(v, str) and v.strip():
            s = v.strip().replace("T", " ").replace("Z", "")
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
                try:
                    return datetime.strptime(s[:19], fmt)
                except ValueError:
                    continue
            if s.isdigit():
                return _received({"received": int(s)}, now)
    except Exception:
        pass
    return now.replace(tzinfo=None)


def _msg_key(sender: str, rec: datetime, body: str) -> str:
    norm = " ".join((body or "").lower().split())
    return hashlib.sha1(f"{sender.lower()}|{rec.strftime('%Y%m%d%H%M%S')}|{norm}".encode()).hexdigest()[:20]


def host_note(db) -> str:
    """The one note that holds bill reminders. Reminders need a note; this keeps them out of the feed's way
    and stops the agent from re-reading it (the note is protected and opted out of enrichment)."""
    nid = _get_setting(db, "sms_host_note", "")
    if nid and db.execute("SELECT 1 FROM notes WHERE id=?", (nid,)).fetchone():
        return nid
    nid = _nanoid(8)
    body = ("Bills picked up from your messages.\n\nEach reminder on this note came from a bill message, and shows "
            "that message until the bill is paid.")
    db.execute("INSERT INTO notes (id, body, visibility, protected, enrich_optout, no_actions, no_reminders) "
               "VALUES (?,?,?,1,1,1,1)", (nid, body, "private"))
    db.execute("INSERT INTO note_state (note_id, status, body_hash) VALUES (?,?,?)",
               (nid, "approved", hashlib.sha1(body.encode()).hexdigest()[:16]))
    _set_setting(db, "sms_host_note", nid)
    return nid


def _amt(v: float) -> str:
    return f"₹{v:,.0f}" if v == int(v) else f"₹{v:,.2f}"


def _make_reminders(db, bill: dict, now: datetime) -> list[str]:
    """Two reminders: LEAD_DAYS before and on the day, at 09:00 local. One already in the past is not made."""
    try:
        dd = datetime.strptime(bill["due_date"], "%Y-%m-%d")
    except (TypeError, ValueError):
        return []
    nid = host_note(db)
    base = f"Pay {bill['merchant']} {_amt(bill['amount'])}"
    out = []
    for when, label in ((dd - timedelta(days=LEAD_DAYS), f"due in {LEAD_DAYS} days"), (dd, "due today")):
        stamp = f"{when.strftime('%Y-%m-%d')}T{REMIND_HOUR}"
        if datetime.strptime(stamp, "%Y-%m-%dT%H:%M") < now.replace(tzinfo=None) - timedelta(hours=12):
            continue                                  # already well past
        rid = _nanoid(10)
        db.execute("INSERT INTO reminders (id, note_id, title, datetime_hint, datetime_resolved, description, source) "
                   "VALUES (?,?,?,?,?,?, 'user')",
                   (rid, nid, f"{base} · {label}", stamp, stamp, bill.get("body") or ""))
        out.append(rid)
    return out


def _close(db, row, status: str) -> None:
    for rid in json.loads(row["reminder_ids"] or "[]"):
        db.execute("UPDATE reminders SET dismissed=1, description='' WHERE id=?", (rid,))   # the message text goes too
    db.execute("UPDATE sms_bills SET status=?, body='' WHERE id=?", (status, row["id"]))


def mark_paid(db, bill_id: int) -> bool:
    row = db.execute("SELECT * FROM sms_bills WHERE id=?", (bill_id,)).fetchone()
    if not row:
        return False
    _close(db, row, "paid")
    return True


def scan(db, messages: list, cfg: dict | None = None, now: datetime | None = None, learn=None) -> dict:
    """Parse raw messages; write bills and reminders; close bills that were paid. Returns counts."""
    now = (now or datetime.now()).replace(tzinfo=None)
    ensure(db)
    cfg = cfg or {}
    stats = {"seen": 0, "added": 0, "skipped": 0, "bills": 0, "reminders": 0, "paid": 0, "newest": 0}
    since = now - timedelta(days=int(cfg["days_back"])) if cfg.get("days_back") else None
    evidence = []
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        sender = str(msg.get("number") or msg.get("sender") or "").strip()
        body = str(msg.get("body") or "")
        rec = _received(msg, now)
        if str(msg.get("type") or "inbox").lower() not in ("inbox", ""):
            continue
        stats["seen"] += 1
        stats["newest"] = max(stats["newest"], int(rec.timestamp() * 1000))
        if since and rec < since:
            continue
        if not sender_ok(sender)[0]:
            stats["skipped"] += 1
            continue
        ev = payment_evidence(body)
        if ev:
            ev["at"] = rec
            evidence.append(ev)
        bill, _why = parse(body, sender, rec)
        if bill is None:
            stats["skipped"] += 1
            continue
        key = _msg_key(sender, rec, body)
        old = db.execute("SELECT id, body FROM sms_bills WHERE key=? AND status='open'", (key,)).fetchone()
        if old is not None and not old["body"] and bill.get("body"):
            db.execute("UPDATE sms_bills SET body=? WHERE id=?", (bill["body"], old["id"]))      # an older bill: keep its text now
            stats["backfilled"] = stats.get("backfilled", 0) + 1
        if db.execute("SELECT 1 FROM sms_bills WHERE key=?", (key,)).fetchone() or \
           db.execute("SELECT 1 FROM sms_bills WHERE merchant_key=? AND amount=? AND due_date=?",
                      (bill["merchant_key"], bill["amount"], bill["due_date"])).fetchone():
            continue
        rids = _make_reminders(db, bill, now)
        db.execute("INSERT INTO sms_bills (key, received_at, sender, merchant, merchant_key, amount, account, due_date, "
                   "reminder_ids, body) VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (key, rec.isoformat(timespec="seconds"), sender[:24], bill["merchant"], bill["merchant_key"],
                    bill["amount"], bill["account"], bill["due_date"], json.dumps(rids), bill.get("body") or ""))
        stats["added"] += 1
        stats["bills"] += 1
        stats["reminders"] += len(rids)
    # A bill is paid when a later message names it (or its account) and carries the same amount.
    for row in db.execute("SELECT * FROM sms_bills WHERE status='open'").fetchall():
        try:
            got = datetime.fromisoformat(row["received_at"])
        except ValueError:
            continue
        for ev in evidence:
            if ev["at"] < got or abs(ev["amount"] - row["amount"]) > 0.99:
                continue
            same_name = row["merchant_key"] and len(row["merchant_key"]) >= 4 and row["merchant_key"] in ev["hay"]
            same_acct = row["account"] and row["account"] == ev["account"]
            if same_name or same_acct:
                _close(db, row, "paid")
                stats["paid"] += 1
                break
    # Long overdue and still open: stop nagging.
    cut = (now.date() - timedelta(days=OVERDUE_KEEP_DAYS)).isoformat()
    for row in db.execute("SELECT * FROM sms_bills WHERE status='open' AND due_date < ?", (cut,)).fetchall():
        _close(db, row, "expired")
    return stats


def forget_all(db) -> None:
    """Remove everything derived from messages: bills, their reminders, and the sync state."""
    ensure(db)
    for r in db.execute("SELECT reminder_ids FROM sms_bills").fetchall():
        for rid in json.loads(r["reminder_ids"] or "[]"):
            db.execute("DELETE FROM reminders WHERE id=?", (rid,))
    db.execute("DELETE FROM sms_bills")
    host = _get_setting(db, "sms_host_note", "")
    if host and not db.execute("SELECT 1 FROM reminders WHERE note_id=?", (host,)).fetchone():
        db.execute("DELETE FROM notes WHERE id=? AND protected=1 AND enrich_optout=1", (host,))
        db.execute("DELETE FROM settings WHERE key='sms_host_note'")
    for k in ("sms_watermark", "sms_status", "sms_last_poll", "sms_scan_requested"):
        db.execute("DELETE FROM settings WHERE key=?", (k,))
