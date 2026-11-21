"""Medha's notification stream: what the agent collected, and the switch.

The agent (hg_agent/memos_daemon/captures.py) polls Medha's `GET /captures`
and writes the `captures` table. This module reports on it and lets you ask
for a read now; it never writes a capture itself.

WHY THERE IS A SCREEN FOR THIS AT ALL

This is the only source that reads data the user did not hand to the app, and
the honest version of that sentence does not fit on a toggle label. So the
screen says what is collected, from which apps, and for how long -- and it
reports the two states that otherwise look identical: "nothing new" and
"nothing is being recorded".

What the agent stores is notification title and text. Medha's own filter has
already dropped anything passcode-shaped before this route ever sees it, and
the agent drops device-status noise on top of that. Nothing here is posted to
the brief: captures only settle a line that was going to be posted anyway.
"""
import json
from datetime import datetime, timedelta

from flask import jsonify, request

from .core import app, get_db
from .settings import _get_prefs

RETAIN_DAYS = 21                 # must match hg_agent captures.RETAIN_DAYS


def _setting(db, key, default=""):
    r = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def _put(db, key, value):
    db.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
               "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def _enabled(db):
    return _get_prefs(db).get("captures", "off") == "on"


def _have_table(db):
    """The agent creates the table on its first poll, so it may not exist yet.

    Reported rather than created here: a UI that creates another component's
    schema is a UI that can create the wrong version of it.
    """
    return bool(db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='captures'").fetchone())


@app.route("/api/captures", methods=["GET"])
def captures_overview():
    """The switch, the agent's last status, and which apps the stream came from."""
    db = get_db()
    on = _enabled(db)
    try:
        st = json.loads(_setting(db, "captures_status", "") or "{}")
    except Exception:
        st = {}
    out = {"enabled": on, "status": st, "retainDays": RETAIN_DAYS,
           "total": 0, "apps": [], "recent": []}
    if not _have_table(db):
        return jsonify(out)

    lo = int((datetime.now() - timedelta(days=7)).timestamp() * 1000)
    out["total"] = db.execute("SELECT COUNT(*) AS n FROM captures").fetchone()["n"]
    out["apps"] = [{"label": r["label"] or r["pkg"], "n": r["n"]} for r in db.execute(
        "SELECT COALESCE(NULLIF(label,''), pkg) AS label, pkg, COUNT(*) AS n FROM captures "
        "WHERE at >= ? GROUP BY 1 ORDER BY n DESC LIMIT 12", (lo,))]
    # A sample, so the user can see what this actually collects rather than
    # taking a sentence's word for it. Only with the feature on: showing rows
    # from a switched-off source invites the question of why they are there.
    if on and request.args.get("sample") == "1":
        out["recent"] = [{"at": r["at"], "app": r["label"] or r["pkg"],
                          "title": r["title"], "text": r["text"]}
                         for r in db.execute(
            "SELECT at, label, pkg, title, text FROM captures "
            "ORDER BY at DESC, id DESC LIMIT 20")]
    return jsonify(out)


@app.route("/api/captures/scan", methods=["POST"])
def captures_scan():
    """Ask the agent to read the stream on its next tick, rather than waiting."""
    db = get_db()
    _put(db, "captures_last_poll", "0")
    _put(db, "captures_scan_requested", "1")
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/captures/forget", methods=["POST"])
def captures_forget():
    """Delete every capture and reset the cursor.

    The cursor goes with the rows, deliberately. Keeping it would mean the
    agent never re-reads what it just deleted, so "forget" would quietly mean
    "forget and never collect that window again"; clearing it lets the next
    poll start over from whatever Medha still holds, which is what someone
    pressing this button in order to start clean would expect.

    Medha keeps its own copy for its own retention window. Turning the
    capability off in the Medha app is what deletes that.
    """
    db = get_db()
    n = 0
    if _have_table(db):
        n = db.execute("DELETE FROM captures").rowcount or 0
    for k in ("captures_cursor", "captures_status", "captures_last_poll",
              "captures_scan_requested"):
        db.execute("DELETE FROM settings WHERE key=?", (k,))
    db.commit()
    return jsonify({"ok": True, "deleted": n})
