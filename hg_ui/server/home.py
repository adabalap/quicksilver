"""Home strips: agent status, what's new, resurfaced note, retry of failed jobs."""
import hashlib
from datetime import datetime, timezone
from flask import jsonify, request

from .core import (AGENT_ALIVE_SECONDS, _ISO_TS, _broadcast, _iso_ago, _settings_json, _settings_put, app, get_db)
from .highlights import _plain_snip
from .insights import _digest_for_home, _pending_dups
from .search import _embed_active
from .settings import _get_prefs


STALL_SECONDS = 300          # keep in step with STALL_MS in the PWA

def _agent_status(db) -> dict:
    """What the agent is doing right now, for a status strip that only speaks
    when there's something worth saying (idle = silence)."""
    hb = db.execute("SELECT value FROM settings WHERE key='agent_heartbeat'").fetchone()
    alive = False
    if hb:
        try:
            then = datetime.fromisoformat(hb["value"].replace("Z", "+00:00"))
            alive = (datetime.now(timezone.utc) - then).total_seconds() < AGENT_ALIVE_SECONDS
        except Exception:
            pass
    tel = _settings_json(db, "agent_telemetry", {}) if alive else {}
    processing = db.execute(
        "SELECT COUNT(*) FROM note_state WHERE status='force' AND updated_at > ?",
        (_iso_ago(STALL_SECONDS),)).fetchone()[0]
    stalled_rows = db.execute(
        "SELECT note_id FROM note_state WHERE status='force' AND updated_at <= ? "
        "ORDER BY updated_at LIMIT 5", (_iso_ago(STALL_SECONDS),)).fetchall()
    # Brand-new notes the agent hasn't reached yet. Short notes the agent
    # deliberately skips (below enrich_min_words) must not count as "waiting".
    try: min_words = int(_get_prefs(db).get("enrich_min_words", 6))
    except Exception: min_words = 6
    waiting = 0
    for r in db.execute(
            "SELECT n.body FROM notes n JOIN note_state ns ON ns.note_id=n.id "
            "WHERE ns.status='raw' AND n.created_at > ? AND n.archived=0 AND n.hidden=0 "
            "AND NOT EXISTS (SELECT 1 FROM enrichments e WHERE e.note_id=n.id)",
            (_iso_ago(900),)):
        if len((r["body"] or "").split()) >= max(1, min_words):
            waiting += 1
    asks = db.execute("SELECT COUNT(*) FROM ask_requests WHERE status='pending' AND created_at > ?",
                      (_iso_ago(180),)).fetchone()[0]
    try:
        media = db.execute("SELECT COUNT(*) FROM attachments WHERE transcript_status IN ('pending','running') "
                           "AND created_at > ? OR transcript_status='running'", (_iso_ago(3600),)).fetchone()[0]
    except Exception:
        media = 0
    queue = int(tel.get("queue_depth") or 0)
    thermal = tel.get("thermal_state") or "cool"
    busy = processing + waiting + asks + queue + media
    if not alive:
        state = "offline" if (busy or stalled_rows) else "idle"
    elif thermal in ("warm", "hot") and busy:
        state = "cooling"
    elif busy:
        state = "working"
    else:
        state = "idle"
    # Passage-index progress (only meaningful once an upgraded agent has
    # published embed_active). Shown quietly so a re-index after a model switch
    # doesn't look like search is broken.
    index = None
    try:
        model = _embed_active(db).get("model")
        if alive and model:
            total = db.execute("SELECT COUNT(*) FROM notes n WHERE TRIM(COALESCE(n.body,''))<>'' OR EXISTS "
                               "(SELECT 1 FROM attachments a WHERE a.note_id=n.id AND a.transcript<>'')").fetchone()[0]
            done = db.execute("SELECT COUNT(DISTINCT note_id) FROM note_chunks WHERE model=?",
                              (model,)).fetchone()[0]
            done += db.execute("SELECT COUNT(*) FROM index_skips WHERE model=? AND note_id NOT IN "
                               "(SELECT note_id FROM note_chunks WHERE model=?)", (model, model)).fetchone()[0]
            if total and done < total:
                index = {"done": min(done, total), "total": total}
    except Exception:
        index = None
    # Durable-queue health: jobs that failed every retry (dead-letter) are shown
    # with a Retry button; jobs waiting out a backoff count as "working".
    jobs = {"queued": 0, "dead": 0, "dead_notes": 0, "dead_asks": 0, "dead_ids": []}
    try:
        for r in db.execute("SELECT status, kind, key FROM agent_jobs WHERE status IN ('queued','dead')"):
            if r["status"] == "queued":
                jobs["queued"] += 1
            else:
                jobs["dead"] += 1
                jobs["dead_asks" if r["kind"] == "ask" else "dead_notes"] += 1
                if r["kind"] == "note" and len(jobs["dead_ids"]) < 3:
                    jobs["dead_ids"].append(r["key"])
    except Exception:
        pass
    return {"alive": alive, "state": state, "processing": processing, "waiting": waiting, "media": media,
            "index": index, "jobs": jobs,
            "queue_depth": queue, "asks_pending": asks, "thermal": thermal,
            "stalled": len(stalled_rows),
            "stalled_ids": [r["note_id"] for r in stalled_rows][:3]}

@app.route("/api/agent/jobs/retry", methods=["POST"])
def retry_dead_jobs():
    """Re-queue every dead-letter job with a fresh retry budget, and nudge the
    agent (SSE) so it picks them up now instead of at its next tick."""
    db = get_db()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    # A dead media job leaves its attachment 'failed'; put it back to 'pending' so
    # the re-queued job has something to do.
    db.execute("UPDATE attachments SET transcript_status='pending', transcript_error=NULL WHERE id IN "
               "(SELECT substr(key, 7) FROM agent_jobs WHERE kind='media' AND status='dead')")
    cur = db.execute("UPDATE agent_jobs SET status='queued', attempts=0, run_after=?, "
                     "updated_at=? WHERE status='dead'", (now, now))
    db.commit()
    _broadcast("jobs_changed", {"n": cur.rowcount})
    return jsonify({"retried": cur.rowcount})

def _whats_new(db, since) -> dict | None:
    """Agent work since the user's last visit on THIS device (client supplies the
    timestamp). Archived/stashed notes are left out of titles and counts."""
    if not since:
        return None
    live = "n.archived=0 AND n.hidden=0"
    enriched = db.execute(
        f"SELECT COUNT(DISTINCT e.note_id) FROM enrichments e JOIN notes n ON n.id=e.note_id "
        f"WHERE e.enriched_at > ? AND {live}", (since,)).fetchone()[0]
    items = [{"id": r["note_id"], "title": (r["title"] or "").strip() or "Untitled note"}
             for r in db.execute(
        f"SELECT e.note_id, "
        f"(SELECT title FROM enrichments WHERE note_id=e.note_id ORDER BY enriched_at DESC, rowid DESC LIMIT 1) title, "
        f"MAX(e.enriched_at) t FROM enrichments e JOIN notes n ON n.id=e.note_id "
        f"WHERE e.enriched_at > ? AND {live} GROUP BY e.note_id ORDER BY t DESC LIMIT 3", (since,))]
    tasks = db.execute(
        f"SELECT COUNT(*) FROM action_items a JOIN notes n ON n.id=a.note_id "
        f"WHERE a.created_at > ? AND a.done=0 AND COALESCE(a.dismissed,0)=0 AND {live}",
        (since,)).fetchone()[0]
    rems = db.execute(
        f"SELECT COUNT(*) FROM reminders r JOIN notes n ON n.id=r.note_id "
        f"WHERE r.created_at > ? AND COALESCE(r.dismissed,0)=0 AND {live}", (since,)).fetchone()[0]
    return {"since": since, "enriched": enriched, "tasks": tasks, "reminders": rems, "items": items}

_RESURFACE_WINDOWS = [(365, "A year ago"), (182, "Six months ago"),
                      (91, "Three months ago"), (30, "A month ago")]

def _resurface(db) -> dict | None:
    """At most ONE card a day: a note from a meaningful distance back (a year /
    six months / three months / a month ago), else one that still has an open
    task, else a long-untouched one. Deterministic per day (hash of id+date), so
    it doesn't flicker between loads. Protected, stashed, archived and daily
    notes are never surfaced. Pure date/SQL logic — no model, no battery."""
    from datetime import timedelta
    st = _settings_json(db, "resurface_state", {})
    today = datetime.now().strftime("%Y-%m-%d")
    if st.get("dismissed_day") == today:
        return None
    snooze = st.get("snooze", {}) if isinstance(st.get("snooze"), dict) else {}
    now_iso = _iso_ago(0)
    ok = lambda nid: snooze.get(nid, "") <= now_iso
    base = ("n.archived=0 AND n.hidden=0 AND n.protected=0 AND COALESCE(n.is_daily,0)=0 "
            "AND length(trim(n.body)) >= 40")
    pick = lambda rows: sorted(rows, key=lambda r: hashlib.md5((r["id"] + today).encode()).hexdigest())

    chosen, label, open_tasks = None, "", 0
    utc_now = datetime.now(timezone.utc)
    for days, lbl in _RESURFACE_WINDOWS:
        lo = (utc_now - timedelta(days=days + 3)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        hi = (utc_now - timedelta(days=max(days - 3, 0))).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        rows = [r for r in db.execute(
            f"SELECT n.* FROM notes n WHERE {base} AND n.created_at BETWEEN ? AND ?", (lo, hi))
            if ok(r["id"])]
        if rows:
            chosen, label = pick(rows)[0], lbl
            break
    if not chosen:
        cutoff = _iso_ago(30 * 86400)
        rows = [r for r in db.execute(
            f"SELECT n.* FROM notes n WHERE {base} AND n.created_at < ? AND EXISTS "
            f"(SELECT 1 FROM action_items a WHERE a.note_id=n.id AND a.done=0 "
            f"AND COALESCE(a.dismissed,0)=0)", (cutoff,)) if ok(r["id"])]
        if rows:
            chosen = pick(rows)[0]
            label = "Still open from " + _ago_label(chosen["created_at"])
    if not chosen:
        cutoff = _iso_ago(60 * 86400)
        rows = [r for r in db.execute(
            f"SELECT n.* FROM notes n WHERE {base} AND n.created_at < ? AND n.updated_at < ? "
            f"AND length(trim(n.body)) >= 60", (cutoff, cutoff)) if ok(r["id"])]
        if rows:
            chosen = pick(rows)[0]
            label = "From " + _ago_label(chosen["created_at"])
    if not chosen:
        return None
    enr = db.execute("SELECT title, summary FROM enrichments WHERE note_id=? "
                     "ORDER BY enriched_at DESC, rowid DESC LIMIT 1", (chosen["id"],)).fetchone()
    body = chosen["body"] or ""
    title = ((enr["title"] if enr else "") or "").strip() or _plain_snip(body.split("\n")[0], 60)
    open_tasks = db.execute("SELECT COUNT(*) FROM action_items WHERE note_id=? AND done=0 "
                            "AND COALESCE(dismissed,0)=0", (chosen["id"],)).fetchone()[0]
    return {"id": chosen["id"], "label": label, "title": title or "Untitled note",
            "snippet": _plain_snip(((enr["summary"] if enr else "") or "").strip() or body),
            "created_at": chosen["created_at"], "open_tasks": open_tasks}

def _ago_label(created_at: str) -> str:
    try:
        then = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        days = (datetime.now(timezone.utc) - then).days
    except Exception:
        return "a while ago"
    if days >= 700: return f"{days // 365} years ago"
    if days >= 365: return "a year ago"
    if days >= 60:  return f"{days // 30} months ago"
    return "last month"

def _bills_for_home(db):
    """Bills due within a week (or overdue), only while the feature is on. Silent otherwise."""
    try:
        from .bills import open_bills
        on = _get_prefs(db).get("sms", "off") == "on"
        due = [b for b in open_bills(db) if -3 <= b["days"] <= 7 and (on or b["manual"])]
        return {"items": due[:3], "more": max(0, len(due) - 3)} if due else None
    except Exception:
        return None

@app.route("/api/home", methods=["GET"])
def home_strips():
    db = get_db()
    since = (request.args.get("since") or "").strip()
    since = since if _ISO_TS.match(since) else None
    return jsonify({"agent": _agent_status(db),
                    "bills": _bills_for_home(db),
                    "whatsnew": _whats_new(db, since),
                    "resurface": _resurface(db),
                    "digest": _digest_for_home(db),
                    "dups": len(_pending_dups(db, 50))})

@app.route("/api/resurface/dismiss", methods=["POST"])
def resurface_dismiss():
    """'Not now': hide the card for the rest of today and snooze that note for
    60 days, so the same note doesn't keep coming back."""
    from datetime import timedelta
    db = get_db()
    nid = str((request.get_json(force=True) or {}).get("id") or "")[:32]
    st = _settings_json(db, "resurface_state", {})
    snooze = st.get("snooze") if isinstance(st.get("snooze"), dict) else {}
    now_iso = _iso_ago(0)
    snooze = {k: v for k, v in snooze.items() if v > now_iso}      # drop expired
    if nid:
        snooze[nid] = (datetime.now(timezone.utc) + timedelta(days=60)
                       ).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    _settings_put(db, "resurface_state",
                  {"dismissed_day": datetime.now().strftime("%Y-%m-%d"), "snooze": snooze})
    db.commit()
    return jsonify({"ok": True})
