"""Medha's brief widget: post the bills that need attention, replace on change, delete when none are left.

Medha owns the widget; each paired client owns one section. PUT /widget/content replaces only ours,
DELETE removes it. The token must belong to a Medha client with the `notify` capability.
"""
import hashlib
import json
import re
import time
import urllib.error
import urllib.request
from datetime import date, datetime, time as dtime, timedelta

from . import sms

# MAX_ITEMS is `pick`'s DEFAULT, not a cap on anything: brief_source always
# passes limit=medha_brief_max_items (50), and _put_fitting shortens the list
# if Medha refuses it. So this number never trims a real post, and it is left
# at 5 deliberately -- raising it changes only what a direct pick() call does,
# which is what brief_test.py exercises.
#
# Medha's own sample clients were a different story: theirs used MAX_ITEMS
# unconditionally and stayed at 5 after BriefPolicy.MAX_ITEMS_PER_SECTION
# went to 20, so every post was trimmed to five client-side and came back a
# success. Medha v0.20.0 added tools/check_mirrored_caps.py for that. The
# numbers below ARE mirrored, so check them against docs/BRIEF-API.md's
# limits table when upgrading Medha.
MAX_ITEMS, MAX_TITLE, MAX_TEXT, MAX_LINK = 5, 80, 200, 2000
MAX_ID = 128                      # BriefJson.MAX_ID_LENGTH
OVERDUE_SHOW_DAYS = 14
_URL = re.compile(r"https?://[^\s<>\"']+")


class BriefError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code = status, code


def _clip(s, n):
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[:n - 1].rstrip() + "…"


class MedhaBrief:
    def __init__(self, base, token, timeout=10, sleep=time.sleep):
        self.base = (base or "").rstrip("/")
        self.token = token or ""
        self.timeout = timeout
        self._sleep = sleep

    def _call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Authorization": f"Bearer {self.token}"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        last = None
        for attempt in range(3):
            req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    return json.loads(r.read().decode("utf-8", "replace") or "{}")
            except urllib.error.HTTPError as e:
                try:
                    j = json.loads(e.read().decode("utf-8", "replace") or "{}")
                except Exception:
                    j = {}
                msg = (j.get("message") or (j.get("error") if isinstance(j.get("error"), str) else "") or e.reason or "")
                if e.code in (429, 503) and attempt < 2:
                    try:
                        wait = float(e.headers.get("Retry-After") or 2)
                    except (TypeError, ValueError):
                        wait = 2
                    self._sleep(min(max(wait, 1), 30))
                    last = BriefError(e.code, j.get("code", ""), msg)
                    continue
                raise BriefError(e.code, j.get("code", ""), str(msg))      # 400/401/403 are deterministic
            except (urllib.error.URLError, OSError, TimeoutError) as e:
                last = BriefError(0, "transport", str(e))
                if attempt < 2:
                    self._sleep(1 + attempt)
                    continue
                raise last
        raise last or BriefError(0, "transport", "no response")

    def notify(self, nid, title, text, silent=True, link=""):
        """Medha v0.12 takes `link` (the tap opens it) and answers {tapOpens: "link"|"medha", linkDropped}."""
        body = {"id": nid, "title": _clip(title, 200) or "Quicksilver", "text": str(text or "")[:2000],
                "ongoing": False, "silent": bool(silent), "progressCurrent": -1, "progressMax": -1}
        if link:
            body["link"] = link
        return self._call("POST", "/notify", body)

    def notify_cancel(self, nid):
        return self._call("DELETE", "/notify/" + urllib.request.quote(str(nid), safe=""))

    def capabilities(self):
        return self._call("GET", "/notify/capabilities")

    def put(self, items, ttl_seconds):
        return self._call("PUT", "/widget/content", {"items": items, "ttlSeconds": int(ttl_seconds)})

    def put_obligations(self, obligations, ttl_seconds):
        """Medha v0.20.0's shape. Needs briefProtocol >= 2; see `protocol_of`.

        The difference from `put` is where the judgement lives. A v1 item is a
        rendered row, so Medha could not tell a reschedule from a new meeting,
        a dismissal dissolved the moment we reworded a title, and the only way
        it could learn something was cancelled was to read the word off the
        front of our text. An obligation carries identity, provenance and
        state, and Medha draws it.
        """
        return self._call("PUT", "/widget/content",
                          {"obligations": obligations, "ttlSeconds": int(ttl_seconds)})

    def clear(self):
        return self._call("DELETE", "/widget/content")

    def read(self):
        return self._call("GET", "/widget/content")

    def captures(self, since=0, limit=None):
        """One page of the phone's notification stream, raw. Medha >= v0.20.0.

        Needs the `captures` capability, which is NOT `notify` and cannot be
        asked for through a consent dialog -- it is ticked as "Read your
        notifications" in Medha's own client editor. Without it: 403.

        This is the only route that runs towards us. Medha has the sensors and
        no memory, so it was guessing -- a kind from the package, a dueAt
        scraped out of an email subject -- and those guesses reached the home
        screen as facts. We have the corpus. Prefer `drain_captures`.
        """
        q = f"?since={max(0, int(since))}"
        if limit is not None:
            q += f"&limit={int(limit)}"
        return self._call("GET", "/captures" + q)

    def saved(self, since=0, limit=None):
        """Things a person pressed Save on in Medha's brief, verbatim. Medha >= v0.22.0.

        Same capability as `captures`. Unlike captures these are instructions,
        not observations: Medha keeps each one until `ack_saved` says we have
        it. Prefer `saved.drain`, which acks only after storing.
        """
        q = f"?since={max(0, int(since))}"
        if limit is not None:
            q += f"&limit={int(limit)}"
        return self._call("GET", "/saved" + q)

    def ack_saved(self, through):
        """Tell Medha every save up to and including `through` is stored here.

        The id goes in the query string -- the route reads nothing else -- but
        an empty JSON body is sent anyway: a POST with no body carries no
        Content-Length, and that is the kind of request a strict server or a
        proxy in the middle is entitled to refuse.
        """
        return self._call("POST", f"/saved/ack?through={max(0, int(through))}", {})


def protocol_of(caps) -> int:
    """The brief protocol a /notify/capabilities reply advertises: 1 or 2.

    A Medha older than v0.20.0 sends no `briefProtocol` at all, which reads as
    1 -- so this never claims v2 against a build that would refuse it. `True`
    is an int in Python, which is exactly the sort of thing that gets through
    an isinstance check written in a hurry.
    """
    raw = (caps or {}).get("briefProtocol")
    return raw if isinstance(raw, int) and not isinstance(raw, bool) else 1


def drain_captures(client, cursor=0, max_pages=20):
    """Every observation after `cursor`, oldest first: (items, next_cursor, listening).

    PERSIST next_cursor ONLY AFTER the items are durably stored. This is the
    one way to lose data here and it is easy to get backwards: save the cursor
    first, crash before writing, and those notifications are gone -- Medha
    prunes on its own schedule and will not hand them over twice. Re-reading a
    page is cheap; missing one is permanent. Duplicates after a crash dedupe
    on `id`, which is stable and never reused.

    `listening` False means nothing is being recorded at all -- Android's
    notification access is off, or Medha's own switch is. An empty list looks
    identical to a quiet phone otherwise, and an agent that cannot tell them
    apart reports a clear day when it has simply gone deaf.

    A next_cursor LOWER than the one passed in is not an error: the capability
    was revoked, which deletes the log, so the rows it pointed past are gone.
    """
    cursor = max(0, int(cursor or 0))
    items, listening = [], True
    for _ in range(max(1, int(max_pages))):
        page = client.captures(since=cursor)
        items.extend(page.get("items") or [])
        listening = bool(page.get("listening", True))
        nxt = page.get("cursor")
        # Only advance on a cursor the server actually sent. Deriving one from
        # the rows would skip whatever Medha holds that this page did not show.
        if not isinstance(nxt, int) or isinstance(nxt, bool):
            break
        cursor = nxt
        if not page.get("more"):
            break
    return items, cursor, listening


# ───────────────────────────── what to post
#
# Every candidate is {key, kind, title, text, dueAt?, link?, score, note_id, ai?}. Medha computes "overdue 2d",
# "today", "in 3d" itself at draw time from dueAt, so `text` never carries a distance: it carries the *why* or the
# next step, which stays true for as long as the line is on the screen.

def _ms(d):
    """Epoch milliseconds of local midnight of a date, or of a naive local datetime."""
    if isinstance(d, datetime):
        return int(d.timestamp() * 1000)
    return int(datetime.combine(d, dtime.min).timestamp() * 1000)


def app_url(base, note_id="", bill_id=None):
    """Quicksilver deep links: ?note=<id> opens a note, ?bill=<id> opens a bill with its Mark as paid button."""
    base = (base or "").strip().rstrip("/")
    if not base:
        return ""
    if bill_id is not None:
        return f"{base}/?bill={bill_id}"
    return f"{base}/?note={note_id}" if note_id else base


def _first_line(body, n=60):
    for ln in str(body or "").splitlines():
        ln = ln.strip().lstrip("#-*• ").strip()
        if ln:
            return _clip(ln, n)
    return ""


_KINDS = (("credit card", "Credit card"), ("emi", "Loan EMI"), ("loan", "Loan"), ("electric", "Electricity"),
          ("bescom", "Electricity"), ("water", "Water"), ("gas", "Gas"), ("broadband", "Broadband"), ("fibre", "Broadband"),
          ("postpaid", "Mobile"), ("mobile", "Mobile"), ("recharge", "Recharge"), ("dth", "TV"), ("insurance", "Insurance"),
          ("premium", "Insurance"), ("policy", "Insurance"), ("rent", "Rent"), ("sip", "SIP"), ("card", "Card"))


def _kind_of_bill(merchant, body):
    hay = f"{merchant} {body}".lower()
    for needle, label in _KINDS:
        if needle in hay:
            return label
    return "Bill"


def inr(n):
    """₹2,50,000 (Indian digit grouping)."""
    try:
        v = int(round(float(n)))
    except (TypeError, ValueError):
        return ""
    s = str(abs(v))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:]); head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts) + "," + tail
    return ("-" if v < 0 else "") + "₹" + s


# ── the second line of a brief row ──────────────────────────────────────────
#
# Every row used to end with the same instruction -- "Tap to open the note and
# tick it off." -- so a brief of thirteen rows said it thirteen times, and the
# age went in the text while Medha's right-hand mark showed when the brief was
# POSTED ("40m" down the whole column). Now the age travels as `since`, the
# mark shows it, and the second line carries the one thing the title does not:
# which note this belongs to. Rows the title already explains get no second
# line at all, which reads better than a filler sentence.
#
# These are the fallbacks. When the model is on, a cached AI line replaces the
# text (see pick); these are what a person reads when it is not, so they have
# to be worth reading without it.

def _iso_ms(raw):
    """Epoch ms from a stored ISO timestamp (UTC 'Z' or naive UTC), or None."""
    s = str(raw or "").strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00")[:32])
    except ValueError:
        try:
            dt = datetime.fromisoformat(s[:19])
        except ValueError:
            return None
    if dt.tzinfo is None:
        from datetime import timezone as _tz
        dt = dt.replace(tzinfo=_tz.utc)
    return int(dt.timestamp() * 1000)


def _note_title(db, note_id, body=""):
    """What the person calls the note: their own title, else the agent's, else its first line."""
    try:
        r = db.execute("SELECT COALESCE(NULLIF(user_title,''),'') FROM notes WHERE id=?", (note_id,)).fetchone()
        if r and r[0]:
            return _clip(r[0], 80)
    except Exception:
        pass                                    # an older database without user_title
    try:
        r = db.execute("SELECT title FROM enrichments WHERE note_id=? AND COALESCE(title,'')<>'' "
                       "ORDER BY enriched_at DESC, rowid DESC LIMIT 1", (note_id,)).fetchone()
        if r and r[0]:
            return _clip(r[0], 80)
    except Exception:
        pass
    return _first_line(body)


def _also(ctx, title):
    """The note's name, unless the row's title already says it."""
    c, t = (ctx or "").strip(), (title or "").strip()
    if not c or c.lower() == t.lower() or c.lower() in t.lower():
        return ""
    return c


def bill_candidates(db, today, amounts=True, pay_links=False, app_base="", manual_only=False):
    lo = (today - timedelta(days=OVERDUE_SHOW_DAYS)).isoformat()
    month_end = (today.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    soon = max(month_end, today + timedelta(days=7)).isoformat()
    out = []
    for r in db.execute("SELECT id, merchant, amount, due_date, body FROM sms_bills "
                        "WHERE status='open' AND due_date BETWEEN ? AND ? " + ("AND key LIKE 'manual-%' " if manual_only else "") +
                        "ORDER BY due_date, id", (lo, soon)):
        try:
            due = datetime.strptime(r["due_date"], "%Y-%m-%d").date()
        except ValueError:
            continue
        days = (due - today).days
        pay = ""
        if pay_links:
            m = _URL.search(r["body"] or "")
            pay = m.group(0).rstrip(".,;)") if m else ""
        kind = _kind_of_bill(r["merchant"] or "", r["body"] or "")
        when = due.strftime("%a %d %b")
        text = f"{kind} bill · due {when}" + (" · pay link in the message" if pay else "")
        title = r["merchant"] or "Bill"
        if amounts and r["amount"]:
            title = f"{title} · {inr(r['amount'])}"
        # Always the bill itself (its message, links and Mark as paid). An overdue bill has no reminders and so
        # no note to open: linking to a note sent the tap to the home screen.
        sheet = app_url(app_base, bill_id=r["id"])
        out.append({"key": f"bill-{r['id']}", "kind": "bill", "title": _clip(title, MAX_TITLE),
                    "text": text, "dueAt": _ms(due), "link": sheet, "note_url": sheet,
                    "score": 1000 - days * 10 if days < 0 else 800 - days * 5, "days": days})
    return out


def reminder_candidates(db, now, app_base="", back=7, ahead=14):
    """Dated reminders from notes: from `back` days overdue to `ahead` days out, plus a couple of undated ones. Bill reminders are skipped
    (the bill itself is the line), as are dismissed ones, which is how "done" is recorded."""
    billrem = set()
    for r in db.execute("SELECT reminder_ids FROM sms_bills"):
        try:
            billrem.update(json.loads(r["reminder_ids"] or "[]"))
        except ValueError:
            pass
    lo = (now - timedelta(days=back)).strftime("%Y-%m-%dT00:00")
    hi = (now + timedelta(days=ahead)).strftime("%Y-%m-%dT23:59")
    out = []
    for r in db.execute(
            "SELECT x.id, x.note_id, x.title, x.datetime_resolved, n.body FROM reminders x JOIN notes n ON n.id=x.note_id "
            "WHERE COALESCE(x.dismissed,0)=0 AND n.archived=0 AND n.hidden=0 AND x.datetime_resolved BETWEEN ? AND ? "
            "ORDER BY x.datetime_resolved", (lo, hi)):
        if r["id"] in billrem:
            continue
        raw = (r["datetime_resolved"] or "").strip()
        try:
            dt = datetime.fromisoformat(raw[:16] if len(raw) >= 16 else raw[:10])
        except ValueError:
            continue
        has_time = len(raw) >= 16 and raw[11:16] != "00:00"
        days = (dt.date() - now.date()).days
        title = _clip(r["title"], MAX_TITLE) or "Reminder"
        ctx = _also(_note_title(db, r["note_id"], r["body"]), title)
        at = f"At {dt.strftime('%I:%M %p').lstrip('0')}" if has_time else ""
        fb = " · ".join(x for x in (at, ctx) if x)
        url = app_url(app_base, r["note_id"])
        out.append({"key": f"rem-{r['id']}", "kind": "event" if has_time else "date", "title": title, "text": fb,
                    "dueAt": _ms(dt), "link": url, "note_url": url, "note_id": r["note_id"],
                    "score": 900 - days * 10 if days < 0 else 700 - days * 5,
                    "ai_ctx": (r["body"] or "")[:600], "days": days})
    for r in db.execute(          # reminders with no resolvable date: still worth a line, never above a dated one
            "SELECT x.id, x.note_id, x.title, x.datetime_hint, n.body FROM reminders x JOIN notes n ON n.id=x.note_id "
            "WHERE COALESCE(x.dismissed,0)=0 AND n.archived=0 AND n.hidden=0 AND COALESCE(x.datetime_resolved,'')='' "
            "ORDER BY x.rowid DESC LIMIT 20"):
        if r["id"] in billrem:
            continue
        url = app_url(app_base, r["note_id"])
        hint = _clip(r["datetime_hint"], 60)
        rtitle = _clip(r["title"], MAX_TITLE) or "Reminder"
        rctx = _also(_note_title(db, r["note_id"], r["body"]), rtitle)
        out.append({"key": f"rem-{r['id']}", "kind": "note", "title": rtitle,
                    "text": _clip(" · ".join(x for x in ((f"When: {hint}" if hint else ""), rctx) if x), MAX_TEXT),
                    "link": url, "note_url": url, "note_id": r["note_id"], "score": 250,
                    "ai_ctx": (r["body"] or "")[:600], "days": 0})
    return out


def action_candidates(db, now, app_base="", limit=2):
    """Open action items from live notes, the longest-waiting first. Done or dismissed ones are not here, which is how
    ticking one off in the app takes it off the brief."""
    out = []
    try:
        rows = db.execute(
            "SELECT a.id, a.note_id, a.text, a.created_at, n.body FROM action_items a JOIN notes n ON n.id=a.note_id "
            "WHERE a.done=0 AND COALESCE(a.dismissed,0)=0 AND n.archived=0 AND n.hidden=0 "
            "ORDER BY a.created_at LIMIT ?", (int(limit),)).fetchall()
    except Exception:
        return out
    for r in rows:
        try:
            age = max(0, (now - datetime.fromisoformat((r["created_at"] or "")[:19])).days)
        except ValueError:
            age = 0
        url = app_url(app_base, r["note_id"])
        atitle = _clip(r["text"], MAX_TITLE) or "To do"
        out.append({"key": f"act-{r['id']}", "kind": "note", "title": atitle,
                    "text": _clip(_also(_note_title(db, r["note_id"], r["body"]), atitle), MAX_TEXT),
                    "since": _iso_ms(r["created_at"]),
                    "link": url, "note_url": url, "note_id": r["note_id"], "score": 150 + min(age, 30),
                    "ai_ctx": (r["body"] or "")[:600], "days": 0})
    return out


def followup_candidates(db, app_base=""):
    out = []
    try:
        rows = db.execute("SELECT id, note_id, note_title, text, move, step, reason FROM followups "
                          "WHERE status='pending' ORDER BY id LIMIT 3").fetchall()
    except Exception:
        return out
    for r in rows:
        url = app_url(app_base, r["note_id"])
        title = _clip("Stalled: " + (r["text"] or ""), MAX_TITLE)
        # The task's own start, so the mark says how long it has waited.
        since = None
        try:
            got = db.execute("SELECT MIN(created_at) FROM action_items WHERE note_id=? AND text=?",
                             (r["note_id"], r["text"])).fetchone()
            since = _iso_ms(got[0]) if got else None
        except Exception:
            pass
        reason = (r["reason"] or "").strip()
        # "Open 48 days" is the mark's job once `since` is known; "Planned for
        # Mon 23 Nov — still open" is not, and stays.
        if since and reason.startswith("Open "):
            reason = reason.split("·", 1)[1].strip() if "·" in reason else ""
        step = (r["step"] or "").strip()
        ctx = _also(r["note_title"] or "", title)
        text = f"Start with: {step}" if step else " · ".join(x for x in (reason, ctx) if x)
        out.append({"key": f"fu-{r['id']}", "kind": "alert", "title": title,
                    "text": _clip(text, MAX_TEXT), "since": since,
                    "link": url, "note_url": url, "note_id": r["note_id"], "score": 300, "days": 0})
    return out


_STALLED = re.compile(r"^\s*stalled:\s*", re.I)


def _same_words(title):
    """A title reduced to what identifies it: no "Stalled:" prefix, no case,
    no runs of whitespace, no trailing ellipsis or full stop from clipping."""
    t = _STALLED.sub("", str(title or "")).lower()
    return re.sub(r"\s+", " ", t).strip().rstrip(".…").strip()


def collapse_same(cands):
    """One candidate per obligation, where WE can tell two are the same one.

    Four generators look at four tables, and one task in one note can surface
    in three of them at once: as the action item, as the reminder made when a
    follow-up was accepted onto a day, and as a "Stalled:" follow-up. Each is
    keyed by its own table's id, so nothing downstream could see they were one
    thing, and the brief showed "Pick up dry cleaner clothes" three times.

    This is the identity-based fix, which is why it lives here and not only in
    Medha: we know two rows came from the same note, Medha only knows their
    words look alike. Same note_id AND the same words -- equal, or one a
    prefix of the other where the shorter is long enough that the difference
    is clipping rather than meaning ("Apply for Sriram Nagar 3rd Electric" is
    a clipped "...Electric Meter"; "Call Ravi" is not a clipped "Call Ravi
    about the bill").

    The survivor is the highest score, which is already the right order: a
    reminder on a day beats a stalled follow-up beats an undated reminder
    beats a bare task. Candidates without a note_id -- bills -- pass through.
    """
    best = {}
    order = []
    for c in cands:
        nid = c.get("note_id")
        w = _same_words(c.get("title"))
        if not nid or not w:
            order.append(("solo", id(c), c))
            continue
        hit = None
        for k in best:
            if k[0] != nid:
                continue
            a, b = sorted((k[1], w), key=len)
            if a == b or (len(a) >= 20 and b.startswith(a)):
                hit = k
                break
        if hit is None:
            best[(nid, w)] = c
            order.append(("grp", (nid, w), None))
        elif c.get("score", 0) > best[hit].get("score", 0):
            best[hit] = c
    return [best[k] if kind == "grp" else c for kind, k, c in order]


def pick(cands, ai=None, limit=MAX_ITEMS, app_base="", protocol=1):
    """Rank, apply cached AI lines, keep `limit`. The first line is the one most often seen, so urgency decides.

    `protocol` 2 returns obligations instead of rendered rows -- same ranking,
    same crowd-out protection, different shape at the last step. Only the
    final mapping differs, so the editorial rule cannot diverge between the
    two protocols."""
    shape = _obligation if protocol >= 2 else _item
    ai = ai or {}
    # Before ranking and before the limit: a duplicate that survives to here
    # costs a slot that something real should have had.
    cands = sorted(collapse_same(cands), key=lambda c: -c["score"])
    for c in cands:
        got = ai.get(c["key"])
        if got and got.get("sig") == sig(c) and got.get("text"):
            c["text"] = got["text"]
    out = []
    keep = cands[:limit if len(cands) <= limit else limit - 1]
    if len(cands) > limit:
        # Urgent bills must not crowd out everything else: one slot each is held for the best dated reminder and
        # the longest-waiting task, whenever there is one.
        for pre in ("rem-", "act-"):
            if not any(str(c.get("key", "")).startswith(pre) for c in keep):
                best = next((c for c in cands if str(c.get("key", "")).startswith(pre)), None)
                if best is not None:
                    drop = next((i for i in range(len(keep) - 1, -1, -1)
                                 if not str(keep[i].get("key", "")).startswith(("rem-", "act-"))), None)
                    if drop is not None:
                        keep[drop] = best
        keep.sort(key=lambda c: -c["score"])
    for c in keep:
        out.append(shape(c))
    if len(cands) > limit:
        out.append(_pointer(len(cands) - (limit - 1), app_url(app_base) if app_base else "", shape))
    return out


def _pointer(extra, link, shape=None):
    """The "+N more" row, built through the same shape function as a real row.

    `shape=None` rather than `shape=_item`: a default argument is evaluated
    when the function is DEFINED, and `_item` is declared further down this
    file, so naming it here is a NameError at import.

    It used to be assembled inline as a v1 dict with a `link` key. On the v2
    protocol Medha reads `links` and ignores anything it does not know, so the
    pointer row -- the ONE row whose entire purpose is to be tapped -- would
    have lost its tap target, silently, with the post still succeeding.
    """
    c = {"key": "", "kind": "note", "title": f"+{extra} more",
         "text": "More need you. Open Quicksilver."}
    if link:
        c["link"] = link
    return (shape or _item)(c)


def _item(c):
    it = {"title": _clip(c["title"], MAX_TITLE) or "Note", "text": _clip(c["text"], MAX_TEXT), "kind": c["kind"]}
    if c.get("dueAt"):
        it["dueAt"] = int(c["dueAt"])
    if c.get("link") and len(c["link"]) <= MAX_LINK and not re.search(r"\s", c["link"]):
        it["link"] = c["link"]
    return it


def _obligation(c):
    """A candidate as Medha v0.20.0's obligation shape.

    Three fields a v1 item could not carry:

    `id` is the candidate key -- `bill-7`, `rem-41`, `cap-...` -- which is
    already stable across a reword, so a dismissal now survives one. Before
    this, identity was (client, title, dueAt): renaming a line un-dismissed
    it, and that looked like our bug rather than the key's.

    `due.provenance` says who vouched for the date. Everything this file
    produced before captures is `declared` and that is not laziness: a bill
    with no date found in its own message is rejected outright by
    sms.bill(), and a reminder's date comes from the note the user wrote. A
    capture-derived date is the one that is `inferred`, and candidates from
    that source set `due_declared` False.

    `state` is what we know. `done` for a bill already paid, `cancelled`
    where the source said so. Medha only lets these DEMOTE -- `open` will
    not rescue a line whose own title announces a cancellation -- so there is
    no way to use this to force something onto the home screen.
    """
    o = {"title": _clip(c["title"], MAX_TITLE) or "Note",
         "text": _clip(c["text"], MAX_TEXT), "kind": c["kind"]}
    key = str(c.get("key") or "").strip()
    if key:
        o["id"] = key[:MAX_ID]
    if c.get("dueAt"):
        # Default True rather than False. Marking a real due date a guess
        # would quietly take the user's bills off their home screen, which is
        # the opposite of the failure this field exists for -- and every
        # source here except captures genuinely declares its dates.
        declared = c.get("due_declared", True)
        o["due"] = {"at": int(c["dueAt"]),
                    "provenance": "declared" if declared else "inferred"}
    state = str(c.get("state") or "").strip().lower()
    if state in ("open", "done", "cancelled", "canceled"):
        o["state"] = state
    # Medha v0.21.0's primary test for the home screen, replacing "has a
    # date". Default True and sent only when False, because posting to
    # someone's brief IS the assertion that it needs them -- and everything
    # this file produces today is a bill, a reminder or a task the user
    # created, which is exactly that.
    #
    # Candidates derived from a CAPTURE set `committed=False`: those are a
    # third-party app's words relayed, not something the user decided, and
    # they reach the agenda only with a declared date. Without this they
    # would arrive looking like the user's own list, which is the mistake
    # that put four emails on the agenda in the first place.
    if not c.get("committed", True):
        o["committed"] = False
    # Medha v0.23.0: when the obligation began, for the age on undated rows.
    # Older Medhas ignore the unknown key, which leaves them as they were.
    if isinstance(c.get("since"), int) and not isinstance(c.get("since"), bool) and c["since"] > 0:
        o["since"] = c["since"]
    link = c.get("link")
    if link and len(link) <= MAX_LINK and not re.search(r"\s", link):
        o["links"] = [link]
    return o


def sig(c):
    """What an AI line was written from; when this changes the line is stale and is written again."""
    return hashlib.sha1(f"{c['title']}|{c.get('dueAt', '')}|{(c.get('ai_ctx') or '')[:300]}".encode()).hexdigest()[:12]


def gather(db, now=None, amounts=True, pay_links=False, app_base="", bills=True, reminders=True, followups=True,
           actions=False, rem_back=7, rem_ahead=14, actions_max=2, manual_bills_only=False):
    now = now or datetime.now()
    c = []
    if bills:
        c += bill_candidates(db, now.date(), amounts, pay_links, app_base, manual_only=manual_bills_only)
    if reminders:
        c += reminder_candidates(db, now, app_base, rem_back, rem_ahead)
    if followups:
        c += followup_candidates(db, app_base)
    if actions:
        c += action_candidates(db, now, app_base, actions_max)
    return c


AI_PROMPT = (
    "You write one line for a phone home-screen widget.\n"
    "Item: {title}\nNote it came from:\n{ctx}\n\n"
    "Write ONE line, at most 110 characters, that tells the person the single most useful next step or the detail from "
    "the note they would otherwise have to open it to find. Use only facts present in the note. No greeting, no emoji, "
    "no quotes, do not repeat the item title, do not mention dates or how many days.")


def clean_ai(text):
    t = " ".join(str(text or "").split()).strip(" \"'“”`")
    if not t or len(t) < 8:
        return ""
    t = re.sub(r"^(next step|tip|note)\s*[:\-]\s*", "", t, flags=re.I)
    return _clip(t, 140)


# ───────────────────────────── notifications: only what is due today or late, one per item, cancelled when gone

def notify_wanted(cands, limit=3, days=0):
    """Items due `days` from now or sooner (0 = today and anything late)."""
    return [c for c in sorted(cands, key=lambda c: -c["score"]) if c.get("days", 99) <= days and c["kind"] in ("bill", "event", "date")][:limit]


def notify_text(c, with_url=False):
    lines = [c["text"]]
    if with_url and c.get("note_url"):
        lines.append("Open the note to update or finish it: " + c["note_url"])
    lines.append("It leaves the brief and this notification once it is done.")
    return "\n".join(lines)


def sync_notifications(db, client, cands, limit=3, days=0):
    """Post a silent notification for each item due today or overdue, whose tap opens the note, and cancel the
    ones whose item is done, dismissed or gone. Returns (posted, cancelled). If this Medha does not honour
    `link` (older build, or the link was dropped) the address goes in the text instead."""
    state = json.loads(sms._get_setting(db, "medha_notif_state", "{}") or "{}")
    want = {f"hg-{c['key']}": c for c in notify_wanted(cands, limit, days)}
    posted = cancelled = 0
    for nid in list(state):
        if nid not in want:
            try:
                client.notify_cancel(nid)
            except BriefError as e:
                if e.status not in (404,):
                    raise
            state.pop(nid, None)
            cancelled += 1
    for nid, c in want.items():
        url = c.get("note_url") or ""
        h = hashlib.sha1((c["title"] + notify_text(c) + url).encode()).hexdigest()[:10]
        if state.get(nid) == h:
            continue
        r = client.notify(nid, c["title"], notify_text(c), link=url)
        if isinstance(r, dict) and r.get("posted") is False:
            sms._set_setting(db, "medha_notif_state", json.dumps(state)); db.commit()
            raise BriefError(200, "not_posted", str(r.get("reason") or "Medha could not show the notification (permission?)"))
        if url and not (isinstance(r, dict) and r.get("tapOpens") == "link"):
            client.notify(nid, c["title"], notify_text(c, with_url=True))          # tap cannot open it: say where it is
        state[nid] = h
        posted += 1
    sms._set_setting(db, "medha_notif_state", json.dumps(state))
    db.commit()
    return posted, cancelled


def clear_notifications(db, client):
    state = json.loads(sms._get_setting(db, "medha_notif_state", "{}") or "{}")
    for nid in list(state):
        try:
            client.notify_cancel(nid)
        except BriefError as e:
            if e.status != 404:
                raise
        state.pop(nid, None)
    sms._set_setting(db, "medha_notif_state", json.dumps(state))
    db.commit()


def digest(items):
    return hashlib.sha1(json.dumps(items, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


# ───────────────────────────── reconcile

def _put_fitting(client, items, ttl_s, protocol=1):
    """Post every row. If Medha refuses or silently trims a long list, post as many as it takes and end with one
    "+N more" row, so nothing is lost without a trace.

    `protocol` 2 sends the obligations shape. The trimming ladder is shared
    deliberately: whichever shape is in use, a list Medha will not take has to
    come back as a shorter list plus a pointer row, and having two copies of
    that logic is how one of them stops being tested."""
    sizes = [len(items)] + [n for n in (40, 30, 20, 15, 10, 7, 5) if n < len(items)]
    last = None
    for n in sizes:
        send = items if n == len(items) else items[:n - 1] + [_more_row(items, n - 1)]
        try:
            res = client.put_obligations(send, ttl_s) if protocol >= 2 else client.put(send, ttl_s)
        except BriefError as e:
            if e.status not in (400, 413, 422) or n == sizes[-1]:
                raise
            last = e
            continue
        stored = res.get("items") if isinstance(res, dict) else None
        if isinstance(stored, int) and 0 < stored < len(send) and n != sizes[-1]:
            keep = stored
            send = items[:keep - 1] + [_more_row(items, keep - 1)]
            res = client.put(send, ttl_s)
        elif isinstance(stored, int) and stored != len(send):
            raise BriefError(200, "dropped", f"Medha stored {stored} of {len(send)} items")
        return res, send
    raise last


def _more_row(items, shown):
    # The already-built rows are the only place the app's URL is to hand here,
    # and they carry it under `link` on v1 and `links` on v2. Reading both so
    # the trimming ladder keeps its tap target on either protocol.
    def link_of(i):
        got = i.get("link")
        if got:
            return got
        many = i.get("links")
        return many[0] if isinstance(many, list) and many else ""

    link = next((link_of(i) for i in items
                 if str(i.get("title", "")).startswith("+") and link_of(i)), "")
    v2 = any("links" in i or "due" in i for i in items)
    return _pointer(len(items) - shown, link, _obligation if v2 else _item)


def reconcile(db, client, items, refresh_s=6 * 3600, ttl_s=24 * 3600, now=None, force=False,
              protocol=1):
    """Make Medha's section match `items`: 'posted' | 'cleared' | 'same' | 'empty'.

    Posts when the content changed or the last post is old, deletes once when nothing is left. dueAt lines do not
    need a post at midnight: Medha recomputes the distance each time it draws. Deterministic failures
    (400/401/403) raise BriefError for the caller to log and back off.

    `protocol` 2 posts the obligations shape. The hash covers the shape too,
    via the items themselves -- a v1 item and the v2 obligation built from the
    same candidate are different dicts, so upgrading Medha reposts once rather
    than leaving the old rows in place and reporting 'same' forever."""
    now = now if now is not None else time.time()
    h = digest(items) if items else ""
    last_h = sms._get_setting(db, "medha_brief_hash", "")
    posted = float(sms._get_setting(db, "medha_brief_posted_at", "0") or 0)
    if not items:
        if not last_h:
            return "empty"
        client.clear()
        sms._set_setting(db, "medha_brief_hash", "")
        sms._set_setting(db, "medha_brief_posted_at", "0")
        db.commit()
        return "cleared"
    if not force and h == last_h and now - posted < refresh_s:
        return "same"
    res, items = _put_fitting(client, items, ttl_s, protocol)
    sms._set_setting(db, "medha_brief_hash", h)
    sms._set_setting(db, "medha_brief_posted_at", str(now))
    db.commit()
    return "posted"
