"""Pulse dashboard, daily brief, agent errors, consolidation, calendar events and the highlights list."""
import sqlite3
from datetime import datetime, timezone
from flask import jsonify, request

from .core import AGENT_ALIVE_SECONDS, _broadcast, _iso_ago, app, get_db
from .highlights import HL_COLOR_OF, HL_RE
from .notes_common import _note_row
from .search import _embed_active


def _connection_groups(db):
    """Count connection CLUSTERS (connected components of the relationship
    graph) — the same unit the Connections tab shows the user, so the numbers
    match. A cluster is a set of notes transitively linked by relationships.
    Robust to directed-row asymmetry (unlike COUNT(*)//2)."""
    edges = db.execute(
        "SELECT DISTINCT r.note_id_a, r.note_id_b FROM relationships r "
        "JOIN notes a ON a.id=r.note_id_a JOIN notes b ON b.id=r.note_id_b "
        "WHERE a.hidden=0 AND a.archived=0 AND b.hidden=0 AND b.archived=0").fetchall()
    if not edges:
        return 0
    # union-find over note ids
    parent = {}
    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb: parent[ra] = rb
    for e in edges:
        union(e["note_id_a"], e["note_id_b"])
    roots = {find(n) for n in parent}
    return len(roots)

@app.route("/api/pulse", methods=["GET"])
def pulse():
    """Agent vitals + a lightweight weekly digest for the Pulse dashboard."""
    db = get_db()
    import json as _j
    tel = {}
    row = db.execute("SELECT value FROM settings WHERE key='agent_telemetry'").fetchone()
    if row:
        try: tel = _j.loads(row["value"])
        except Exception: tel = {}
    hb = db.execute("SELECT value FROM settings WHERE key='agent_heartbeat'").fetchone()
    alive = False
    if hb:
        try:
            then = datetime.fromisoformat(hb["value"].replace("Z","+00:00"))
            alive = (datetime.now(timezone.utc)-then).total_seconds() < AGENT_ALIVE_SECONDS
        except Exception: pass

    def _c(q, *a): return db.execute(q, a).fetchone()[0]
    week_iso = _iso_ago(7 * 86400)
    week = f"'{week_iso}'"
    digest = {
        "notes_total":      _c("SELECT COUNT(*) FROM notes"),
        "notes_this_week":  _c(f"SELECT COUNT(*) FROM notes WHERE created_at >= {week}"),
        "pending_review":   _c("SELECT COUNT(*) FROM note_state WHERE status='pending'"),
        "never_approved":   _c("SELECT COUNT(*) FROM note_state WHERE status IN ('raw','pending')"),
        "open_actions":     _c("SELECT COUNT(*) FROM action_items a JOIN notes n ON n.id=a.note_id "
                               "WHERE a.done=0 AND COALESCE(a.dismissed,0)=0 AND n.hidden=0"),
        "actions_aging":    _c("SELECT COUNT(*) FROM action_items ai JOIN notes n ON n.id=ai.note_id "
                               "WHERE ai.done=0 AND COALESCE(ai.dismissed,0)=0 AND n.hidden=0 AND n.created_at < ?", week_iso),
        "done_this_week":   _c("SELECT COUNT(*) FROM action_items a JOIN notes n ON n.id=a.note_id "
                               "WHERE a.done=1 AND COALESCE(a.dismissed,0)=0 AND n.hidden=0 "
                               "AND a.done_at >= ?", week_iso),
        "connections":      _connection_groups(db),
        "protected":        _c("SELECT COUNT(*) FROM notes WHERE protected=1"),
    }
    # Current (actionable) agent errors: the stored error list, minus any whose
    # note was since deleted — so the dashboard count matches the drill-down
    # list exactly (no "11 errors" that opens to an empty screen).
    import json as _je
    _erow = db.execute("SELECT value FROM settings WHERE key='agent_errors'").fetchone()
    _errs = []
    if _erow:
        try: _errs = _je.loads(_erow["value"])
        except Exception: _errs = []
    errors_current = sum(
        1 for e in _errs
        if db.execute("SELECT 1 FROM notes WHERE id=?", (e.get("id"),)).fetchone())
    digest["errors_current"] = errors_current
    # Saves from Medha that nobody has decided on. Guarded: on the first start
    # after an update the table may not exist until init_db has run, and the
    # dashboard is the one screen that must never fail to open.
    try:
        digest["saved_waiting"] = db.execute(
            "SELECT COUNT(*) FROM saved_captures WHERE decision IS NULL").fetchone()[0]
    except sqlite3.OperationalError:
        digest["saved_waiting"] = 0
    top_tags = [dict(r) for r in db.execute(
        f"SELECT t.tag, COUNT(*) c FROM tags t JOIN notes n ON n.id=t.note_id "
        f"WHERE n.created_at >= {week} GROUP BY t.tag ORDER BY c DESC LIMIT 5").fetchall()]
    # Queue health, index health, a 7-day capture series and the agent's recent
    # work — everything the dashboard shows beyond the weekly digest.
    jobs = {"queued": 0, "running": 0, "retrying": 0, "dead": 0}
    try:
        for r in db.execute("SELECT status, attempts, COUNT(*) c FROM agent_jobs "
                            "WHERE status IN ('queued','running','dead') GROUP BY status, attempts>0"):
            jobs[r["status"]] += r["c"]
            if r["status"] == "queued" and r["attempts"]:
                jobs["retrying"] += r["c"]
    except Exception:
        pass
    index = None
    try:
        model = _embed_active(db).get("model")
        if model:
            total = _c("SELECT COUNT(*) FROM notes n WHERE TRIM(COALESCE(n.body,''))<>'' OR EXISTS "
                       "(SELECT 1 FROM attachments a WHERE a.note_id=n.id AND a.transcript<>'')")
            done = _c("SELECT COUNT(DISTINCT note_id) FROM note_chunks WHERE model=?", model) \
                + _c("SELECT COUNT(*) FROM index_skips WHERE model=? AND note_id NOT IN "
                     "(SELECT note_id FROM note_chunks WHERE model=?)", model, model)
            index = {"model": model, "done": min(done, total), "total": total}
            if done < total and total - done <= 5:       # name the stragglers, so a stuck count is never a mystery
                index["waiting"] = [{"id": r["id"], "title": (r["t"] or r["b"] or "Untitled")[:50]} for r in db.execute(
                    "SELECT n.id, TRIM(SUBSTR(n.body,1,50)) b, (SELECT title FROM enrichments WHERE note_id=n.id "
                    "ORDER BY enriched_at DESC LIMIT 1) t FROM notes n WHERE (TRIM(COALESCE(n.body,''))<>'' OR EXISTS "
                    "(SELECT 1 FROM attachments a WHERE a.note_id=n.id AND a.transcript<>'')) AND NOT EXISTS "
                    "(SELECT 1 FROM note_chunks c WHERE c.note_id=n.id AND c.model=?) AND NOT EXISTS "
                    "(SELECT 1 FROM index_skips k WHERE k.note_id=n.id AND k.model=?) LIMIT 5", (model, model))]
    except Exception:
        index = None
    try: tz = max(-720, min(840, int(request.args.get("tz", 0))))
    except Exception: tz = 0
    mod = f"{tz:+d} minutes"
    byday = {r["d"]: r["c"] for r in db.execute(
        "SELECT date(created_at, ?) d, COUNT(*) c FROM notes WHERE created_at >= ? GROUP BY d",
        (mod, _iso_ago(8 * 86400)))}
    today = (datetime.now(timezone.utc) + __import__("datetime").timedelta(minutes=tz)).date()
    capture = []
    for i in range(6, -1, -1):
        day = today - __import__("datetime").timedelta(days=i)
        capture.append({"d": day.isoformat(), "n": byday.get(day.isoformat(), 0)})
    activity = []
    for r in db.execute(
            "SELECT e.note_id, e.title, e.enriched_at t FROM enrichments e "
            "JOIN notes n ON n.id=e.note_id WHERE n.archived=0 AND n.hidden=0 "
            "ORDER BY e.enriched_at DESC LIMIT 8"):
        activity.append({"t": r["t"], "kind": "organised", "id": r["note_id"], "title": r["title"] or ""})
    for r in db.execute(
            "SELECT a.note_id, a.text, a.created_at t FROM action_items a JOIN notes n ON n.id=a.note_id "
            "WHERE COALESCE(a.dismissed,0)=0 AND a.created_at >= ? AND n.archived=0 AND n.hidden=0 "
            "ORDER BY a.created_at DESC LIMIT 8", (_iso_ago(7 * 86400),)):
        activity.append({"t": r["t"], "kind": "task", "id": r["note_id"], "title": (r["text"] or "")[:80]})
    for r in db.execute(
            "SELECT x.note_id, x.title, x.created_at t FROM reminders x JOIN notes n ON n.id=x.note_id "
            "WHERE COALESCE(x.dismissed,0)=0 AND x.source='agent' AND x.created_at >= ? "
            "AND n.archived=0 AND n.hidden=0 ORDER BY x.created_at DESC LIMIT 8", (_iso_ago(7 * 86400),)):
        activity.append({"t": r["t"], "kind": "reminder", "id": r["note_id"], "title": (r["title"] or "")[:80]})
    activity.sort(key=lambda a: a["t"], reverse=True)
    # Exact counts for the user's local day (the feed above is capped at 8 rows).
    td = today.isoformat()
    def _n(sql):
        try: return db.execute(sql, (mod, td)).fetchone()[0]
        except Exception: return 0
    today_counts = {
        "organised": _n("SELECT COUNT(*) FROM enrichments e JOIN notes n ON n.id=e.note_id WHERE n.archived=0 AND n.hidden=0 AND date(e.enriched_at, ?) = ?"),
        "tasks": _n("SELECT COUNT(*) FROM action_items a JOIN notes n ON n.id=a.note_id WHERE COALESCE(a.dismissed,0)=0 AND n.archived=0 AND n.hidden=0 AND date(a.created_at, ?) = ?"),
        "reminders": _n("SELECT COUNT(*) FROM reminders x JOIN notes n ON n.id=x.note_id WHERE COALESCE(x.dismissed,0)=0 AND x.source='agent' AND n.archived=0 AND n.hidden=0 AND date(x.created_at, ?) = ?"),
        "planned": _n("SELECT COUNT(*) FROM followups WHERE status='accepted' AND date(resolved_at, ?) = ?"),
    }
    return jsonify({"agent_alive": alive, "telemetry": tel,
                    "agent_last_seen": hb["value"] if hb else None,
                    "digest": digest, "top_tags_week": top_tags,
                    "jobs": jobs, "index": index, "capture": capture,
                    "activity": activity[:8], "today": today_counts})

@app.route("/api/brief/refresh", methods=["POST"])
def brief_refresh():
    """Request an immediate brief regeneration; the agent clears the interval
    gate by seeing this flag and composes on its next tick (within ~1 min)."""
    db = get_db()
    db.execute("INSERT INTO settings (key, value) VALUES ('brief_refresh_requested','1') "
               "ON CONFLICT(key) DO UPDATE SET value='1', "
               "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')")
    db.commit()
    return jsonify({"ok": True, "message": "Brief will refresh within a minute"})

@app.route("/api/errors", methods=["GET"])
def agent_errors():
    """Notes the agent failed to enrich, with reason — so the count is
    actionable, not just alarming."""
    db = get_db()
    import json as _j
    row = db.execute("SELECT value FROM settings WHERE key='agent_errors'").fetchone()
    errs = []
    if row:
        try: errs = _j.loads(row["value"])
        except Exception: errs = []
    # hydrate each with the note's current title/body for display
    out = []
    for e in errs:
        n = db.execute("SELECT id, body FROM notes WHERE id=?", (e.get("id"),)).fetchone()
        if not n:
            continue   # note was deleted — drop the stale error
        title = db.execute("SELECT title FROM enrichments WHERE note_id=? "
                           "ORDER BY enriched_at DESC, rowid DESC LIMIT 1", (e.get("id"),)).fetchone()
        out.append({"id": n["id"],
                    "title": (title["title"] if title else None) or (n["body"] or "")[:50],
                    "reason": e.get("reason", "failed"), "ts": e.get("ts")})
    return jsonify({"errors": out})

@app.route("/api/errors/<nid>/retry", methods=["POST"])
def retry_error(nid):
    """Re-queue a failed note by forcing re-enrichment."""
    db = get_db()
    db.execute("INSERT INTO note_state (note_id, status) VALUES (?, 'force') "
               "ON CONFLICT(note_id) DO UPDATE SET status='force'", (nid,))
    db.commit()
    note = db.execute("SELECT * FROM notes WHERE id=?", (nid,)).fetchone()
    if note:
        _broadcast("note_updated", _note_row(note))
    return jsonify({"ok": True})

def _write_errors(db, errs):
    import json as _j
    db.execute("INSERT INTO settings (key, value) VALUES ('agent_errors', ?) "
               "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (_j.dumps(errs),))
    db.commit()

def _read_errors(db):
    import json as _j
    row = db.execute("SELECT value FROM settings WHERE key='agent_errors'").fetchone()
    if not row:
        return []
    try:
        return _j.loads(row["value"])
    except Exception:
        return []

@app.route("/api/errors/<nid>", methods=["DELETE"])
def dismiss_error(nid):
    """Give up on one note: take it off the error log and leave it alone.

    Retry was the only action here, which meant a note that will never enrich
    could not leave the list -- the count sat above zero forever and stopped
    meaning anything. This removes the log entry and nothing else, which is
    the whole fix: the agent dead-letters a note after three failures, so it
    is not re-queued on its own and the dismissal holds. Editing the note
    makes it dirty again and it gets another go, which is right -- by then it
    is a different note.
    """
    db = get_db()
    errs = _read_errors(db)
    kept = [e for e in errs if e.get("id") != nid]
    _write_errors(db, kept)
    return jsonify({"ok": True, "dismissed": len(errs) - len(kept), "remaining": len(kept)})

@app.route("/api/errors", methods=["DELETE"])
def dismiss_all_errors():
    """Clear the whole error log. Same reasoning as dismissing one, in bulk:
    after a model outage every queued note fails at once, and dismissing a
    dozen transient failures one at a time is not a reasonable ask."""
    db = get_db()
    n = len(_read_errors(db))
    _write_errors(db, [])
    return jsonify({"ok": True, "dismissed": n, "remaining": 0})

@app.route("/api/highlights", methods=["GET"])
def list_highlights():
    """Every highlight (==text==) across all notes, newest note first."""
    db = get_db()
    rows = db.execute(
        "SELECT n.id, n.body, n.updated_at, "
        "(SELECT title FROM enrichments WHERE note_id=n.id ORDER BY enriched_at DESC, rowid DESC LIMIT 1) t "
        "FROM notes n WHERE n.body LIKE '%\u2063%' OR n.body LIKE '%=={%' ORDER BY n.updated_at DESC").fetchall()
    out = []
    for r in rows:
        title = r["t"] or (r["body"] or "").strip().split("\n")[0][:40] or "Untitled"
        for m in HL_RE.finditer(r["body"] or ""):
            color = HL_COLOR_OF.get(m.group(1) or "", m.group(3) or "y")
            text = (m.group(2) if m.group(2) is not None else (m.group(4) or "")).strip()
            if text:
                out.append({"note_id": r["id"], "note_title": title,
                            "color": color, "text": text})
    return jsonify({"highlights": out, "count": len(out)})

@app.route("/api/consolidation", methods=["GET"])
def get_consolidation():
    """The agent's current consolidation offer, if any (theme with 4+ notes)."""
    db = get_db()
    import json as _j
    row = db.execute("SELECT value FROM settings WHERE key='consolidation_offer'").fetchone()
    if not row:
        return jsonify({"offer": None})
    try:
        offer = _j.loads(row["value"])
    except Exception:
        return jsonify({"offer": None})
    # hydrate note count still valid
    tag = offer.get("tag")
    if tag:
        cnt = db.execute(
            "SELECT COUNT(DISTINCT note_id) FROM tags WHERE tag=?", (tag,)).fetchone()[0]
        offer["note_count"] = cnt
    return jsonify({"offer": offer})

@app.route("/api/consolidation/accept", methods=["POST"])
def accept_consolidation():
    """User accepts — agent will draft a consolidated note on its next tick."""
    db = get_db()
    db.execute("INSERT INTO settings (key, value) VALUES ('consolidation_accepted','1') "
               "ON CONFLICT(key) DO UPDATE SET value='1'")
    db.commit()
    return jsonify({"ok": True, "message": "Drafting a consolidated note…"})

@app.route("/api/consolidation/dismiss", methods=["POST"])
def dismiss_consolidation():
    db = get_db()
    db.execute("DELETE FROM settings WHERE key='consolidation_offer'")
    db.commit()
    return jsonify({"ok": True})

@app.route("/api/brief", methods=["GET"])
def daily_brief():
    """The agent's most recent natural-language daily brief (if any)."""
    db = get_db()
    row = db.execute("SELECT value, updated_at FROM settings WHERE key='daily_brief'").fetchone()
    if not row:
        return jsonify({"brief": None})
    import json as _j
    try:
        data = _j.loads(row["value"])
    except Exception:
        data = {"text": row["value"]}
    data["updated_at"] = row["updated_at"]
    return jsonify({"brief": data})

@app.route("/api/calendar-events", methods=["GET"])
def calendar_events():
    """Reminders that became Google Calendar events, for the note detail view."""
    db = get_db()
    nid = request.args.get("note_id")
    if nid:
        rows = db.execute(
            "SELECT id, note_id, title, datetime_hint, datetime_resolved, "
            "calendar_event_id FROM reminders WHERE note_id=? AND "
            "calendar_event_id IS NOT NULL AND calendar_event_id != ''", (nid,)).fetchall()
    else:
        rows = db.execute(
            "SELECT id, note_id, title, datetime_hint, datetime_resolved, "
            "calendar_event_id FROM reminders WHERE calendar_event_id IS NOT NULL "
            "AND calendar_event_id != '' "
            # Dated events first (a resolved timestamp), newest-first; undated
            # ('next Monday' style hints we couldn't resolve) fall to the end.
            "ORDER BY CASE WHEN datetime_resolved IS NOT NULL "
            "AND datetime_resolved != '' THEN 0 ELSE 1 END, "
            "datetime_resolved DESC, id DESC").fetchall()
    return jsonify({"events": [dict(r) for r in rows]})
