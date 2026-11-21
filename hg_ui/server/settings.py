"""User preferences API."""
from flask import jsonify, request

from .core import app, get_db


# Default user preferences — the agent + UI read these.
DEFAULT_PREFS = {
    "enrich_min_words": 6,       # skip enrichment for notes shorter than this
    "summary_mode": "long",      # always | long | never
    "summary_min_words": 100,    # 'long' threshold: below this a note IS its own
                                 # summary, so repeating it just adds noise.
    "brief_mode": "ondemand",    # ondemand | auto
    "default_note_type": "text", # text | checklist
    "highlight_color": "y",      # default highlight colour
    # Semantic search: minimum cosine similarity (0-1) for a note to count as a
    # match. Higher = stricter/cleaner results (fewer loosely-related notes);
    # lower = looser/more inclusive. 0.30 is a good balance; drop toward 0.20 if
    # search feels too strict, raise toward 0.40 if it surfaces unrelated notes.
    "search_min_similarity": 0.30,
    # Ask's retrieval floor is deliberately looser than search's. A false
    # positive here just gives the model an extra excerpt it's instructed to
    # ignore if irrelevant; a false negative means a genuinely relevant note
    # never reaches the model at all, and no amount of good generation can
    # recover from that. See search_min_similarity above for the contrast.
    "ask_min_similarity": 0.20,
    # In-app mute for notifications. Independent of the browser's Notification
    # permission — JS has no way to revoke a granted permission, so this is the
    # actual off-switch the Settings UI offers: notify() checks it before ever
    # calling the service worker, even though the browser stays "granted".
    "notify_muted": 0,
    # Daily task follow-through: the agent proposes one move for each stale task. on | off
    "followups": "on",
    # Bills from messages (bill SMS read through Termux:API → reminders). Off until you switch it on.
    "sms": "off",
    # Medha's notification stream (GET /captures, Medha v0.20.0+). Off until
    # you switch it on, AND Medha refuses it until this client is given "Read
    # your notifications" in the Medha app -- the one capability an app cannot
    # ask for through a consent dialog. Captures never add a line to the
    # brief: they only settle one that was going to be posted anyway.
    "captures": "off",
}

def _get_prefs(db):
    import json as _j
    row = db.execute("SELECT value FROM settings WHERE key='user_prefs'").fetchone()
    prefs = dict(DEFAULT_PREFS)
    if row:
        try:
            prefs.update(_j.loads(row["value"]) or {})
        except Exception:
            pass
    return prefs

@app.route("/api/settings", methods=["GET", "POST"])
def user_settings():
    import json as _j
    db = get_db()
    if request.method == "POST":
        data = request.get_json(force=True) or {}
        prefs = _get_prefs(db)
        # Only accept known keys; coerce types defensively.
        for k, v in data.items():
            if k not in DEFAULT_PREFS:
                continue
            if k in ("enrich_min_words", "summary_min_words"):
                try: prefs[k] = max(0, int(v))
                except Exception: pass
            elif k == "search_min_similarity":
                try: prefs[k] = min(1.0, max(0.0, float(v)))
                except Exception: pass
            elif k == "ask_min_similarity":
                try: prefs[k] = min(1.0, max(0.0, float(v)))
                except Exception: pass
            elif k == "summary_mode" and v in ("always", "long", "never"):
                prefs[k] = v
            elif k == "brief_mode" and v in ("ondemand", "auto"):
                prefs[k] = v
            elif k == "default_note_type" and v in ("text", "checklist"):
                prefs[k] = v
            elif k == "highlight_color" and v in ("y", "g", "p", "b"):
                prefs[k] = v
            elif k in ("followups", "sms", "captures") and v in ("on", "off"):
                prefs[k] = v
            elif k == "notify_muted":
                try: prefs[k] = 1 if int(v) else 0
                except Exception: pass
        db.execute("INSERT INTO settings (key, value) VALUES ('user_prefs', ?) "
                   "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (_j.dumps(prefs),))
        db.commit()
        return jsonify({"ok": True, "prefs": prefs})
    # Also surface agent-reported calendar status so the settings UI can show
    # whether Google Calendar sync is actually wired up.
    cal = False
    try:
        import json as _j2
        trow = db.execute("SELECT value FROM settings WHERE key='agent_telemetry'").fetchone()
        if trow:
            cal = bool((_j2.loads(trow["value"]) or {}).get("calendar_enabled"))
    except Exception:
        pass
    return jsonify({"prefs": _get_prefs(db), "calendar_enabled": cal})
