"""Health, version, stats, and the daemon's webhook and relationship updates."""
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from flask import jsonify, request

import mediastore as _ms

from .core import AGENT_ALIVE_SECONDS, BASE_DIR, DB_PATH, _broadcast, _settings_json, app, get_db
from .home import _agent_status
from .notes_common import _note_row


# ── System health ────────────────────────────────────────────────────────────
@app.route("/api/health", methods=["GET"])
def system_health():
    """One screen's worth of 'is everything working?': agent liveness, which engine is
    answering, versions (and whether they match), queue, recent failures, storage."""
    import shutil
    db = get_db()
    st = _agent_status(db)
    tel = _settings_json(db, "agent_telemetry", {})
    emb = _settings_json(db, "embed_active", {})
    agent_v = tel.get("version") or ""
    jobs = {r["status"]: r["c"] for r in db.execute("SELECT status, COUNT(*) c FROM agent_jobs GROUP BY status")}
    failed = [{"key": r["key"], "kind": r["kind"], "error": (r["last_error"] or "")[:160],
               "at": r["updated_at"]} for r in db.execute(
        "SELECT kind, key, last_error, updated_at FROM agent_jobs WHERE status='dead' "
        "ORDER BY updated_at DESC LIMIT 5")]
    p = Path(DB_PATH)
    def _size(path):
        try: return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
        except Exception: return 0
    bdir = p.parent / "backups"
    backups = sorted(bdir.glob("auto-*.db")) if bdir.exists() else []
    try: free = shutil.disk_usage(str(p.parent)).free
    except Exception: free = None
    return jsonify({
        "agent": {"alive": bool(st.get("alive")), "state": st.get("state"), "version": agent_v,
                  "engine": tel.get("engine") or "", "medha_ok": tel.get("medha_ok"), "model": tel.get("model") or "",
                  "media": tel.get("media") or "", "thermal": tel.get("thermal_state") or "",
                  "queue": tel.get("queue_depth"), "started_at": tel.get("started_at"),
                  "last_latency_s": tel.get("last_latency_s")},
        "versions": {"app": APP_VERSION, "agent": agent_v, "build": _build_id(),
                     "match": (not agent_v) or agent_v == APP_VERSION},
        "search": {"embed_model": emb.get("model") or "", "mode": tel.get("relationships_mode") or ""},
        "jobs": jobs, "failed": failed,
        "storage": {"db_bytes": p.stat().st_size if p.exists() else 0,
                    "attachments_bytes": _size(_ms.att_dir(DB_PATH)),
                    "free_bytes": free, "backups": len(backups),
                    "last_backup": datetime.fromtimestamp(backups[-1].stat().st_mtime, timezone.utc)
                                   .strftime("%Y-%m-%dT%H:%M:%SZ") if backups else None},
        "notes": db.execute("SELECT COUNT(*) FROM notes").fetchone()[0],
    })

# ── API: Stats ────────────────────────────────────────────────────────────────
APP_VERSION = "1.6.7"      # bump when you ship a user-visible release
# Bump when the page starts needing new routes; the page warns if the server is
# older. This lives in Python ON PURPOSE -- it is the one thing in /api/version
# that reports the RUNNING code. `build` next to it is read out of about.html at
# request time, so after an unzip-without-restart it already shows the new stamp
# while the process is still serving the old routes. Only this number can tell
# the two apart. 7 = the error log can be dismissed (DELETE /api/errors[/<id>])
# and a follow-up can be accepted onto a date you pick. 8 = the saved-from-Medha
# inbox (/api/saved, /decide, /undo).
API_LEVEL = 8

def _build_id() -> str:
    """The build stamp written into static/about.html at packaging time."""
    try:
        t = (BASE_DIR / "static" / "about.html").read_text(encoding="utf-8")
        m = re.search(r"build (\d{4}-\d{2}-\d{2}-\d{4})", t)
        return m.group(1) if m else ""
    except Exception:
        return ""

@app.route("/api/version", methods=["GET"])
def app_version():
    """Real version info for the drawer/About: app release, build stamp, and the
    agent's own version + embedding model as last reported in its telemetry."""
    db = get_db()
    tel = _settings_json(db, "agent_telemetry", {})
    emb = _settings_json(db, "embed_active", {})
    return jsonify({"app": APP_VERSION, "build": _build_id(), "api": API_LEVEL,
                    "agent": tel.get("version") or "",
                    "embed_model": emb.get("model") or ""})

def _pending_followups(db):
    from .followups import live_pending
    try: return len(live_pending(db))
    except Exception: return 0

def _today(tz):
    """The person's calendar day, from their offset in minutes east of UTC."""
    return (datetime.now(timezone.utc) + timedelta(minutes=tz)).date().isoformat()

@app.route("/api/stats", methods=["GET"])
def stats():
    db = get_db()
    def _c(sql, *p): return db.execute(sql, p).fetchone()[0]
    try: tz = max(-720, min(840, int(request.args.get("tz", 0))))
    except Exception: tz = 0
    # Agent liveness: the agent writes a heartbeat timestamp to settings
    # every ~10s. Stale (>60s) or missing → agent is down.
    hb = db.execute("SELECT value FROM settings WHERE key='agent_heartbeat'").fetchone()
    agent_alive, agent_last = False, None
    if hb:
        agent_last = hb["value"]
        try:
            then = datetime.fromisoformat(agent_last.replace("Z", "+00:00"))
            agent_alive = (datetime.now(timezone.utc) - then).total_seconds() < AGENT_ALIVE_SECONDS
        except Exception:
            pass
    return jsonify({
        "agent_alive":    agent_alive,
        "agent_last_seen": agent_last,
        "total_notes":    _c("SELECT COUNT(*) FROM notes"),
        "approved":       _c("SELECT COUNT(*) FROM note_state WHERE status='approved'"),
        "pending":        _c("SELECT COUNT(*) FROM note_state WHERE status='pending'"),
        "raw":            _c("SELECT COUNT(*) FROM note_state WHERE status='raw'"),
        "action_pending": _c("SELECT COUNT(*) FROM action_items a JOIN notes n ON n.id=a.note_id "
                             "WHERE a.done=0 AND COALESCE(a.dismissed,0)=0 AND n.hidden=0"),
        "action_done":    _c("SELECT COUNT(*) FROM action_items WHERE done=1 AND COALESCE(dismissed,0)=0"),
        "reminders_active": _c("SELECT COUNT(*) FROM reminders WHERE COALESCE(dismissed,0)=0"),
        "total_tags":     _c("SELECT COUNT(DISTINCT tag) FROM tags"),
        "followups":      _pending_followups(db),
        # What the tab badges count. A badge is a request for attention, so it
        # counts what needs you TODAY, not everything that exists: the old
        # "every reminder ever set" read 14 on a quiet day and taught a person
        # to ignore it. Due today or already past, in the person's own day
        # (tz = minutes east of UTC, the same convention as /api/pulse).
        "reminders_due":  _c("SELECT COUNT(*) FROM reminders WHERE COALESCE(dismissed,0)=0 "
                             "AND COALESCE(datetime_resolved,'')<>'' AND substr(datetime_resolved,1,10) <= ?", _today(tz)),
    })

# ── API: Daemon webhook (replaces Memos webhook) ──────────────────────────────
@app.route("/api/webhook", methods=["POST"])
def daemon_webhook():
    """Broadcast-only. The daemon (Quicksilver backend) already writes
    enrichment results directly to SQLite before it ever calls this — see
    qs_client.py's write_enrichment()/_notify_webhook(). This endpoint's only
    job is pushing the resulting note to connected PWA clients over SSE, per
    its own docstring and README.md's description of the flow.

    This used to ALSO re-insert enrichment/action_items/tags/corrections/
    note_state a second time from the posted payload — a duplicate of what
    write_enrichment had just persisted directly. Harmless for the UPDATE-
    based tables, but enrichments is append-only with no dedup key, so every
    real enrichment silently produced two rows. Fixed: read the
    already-persisted state and broadcast it, nothing more."""
    db = get_db()
    data = request.get_json(force=True) or {}
    if data.get("event") in ("bills_changed", "spend_changed"):     # the agent read new bill messages
        _broadcast("bills_changed", {})
        return jsonify({"ok": True})
    nid = data.get("note_id")
    if not nid:
        return jsonify({"error": "note_id required"}), 400
    row = db.execute("SELECT * FROM notes WHERE id=?", (nid,)).fetchone()
    if not row:
        return jsonify({"error": "note not found"}), 404
    note = _note_row(row)
    ev = data.get("event")
    # Quiet refreshes (e.g. a transcript landed) must not show the "✨ enriched" toast.
    _broadcast(ev if ev in ("attachment_ready", "note_updated") else "note_enriched", note)
    return jsonify({"ok": True, "note_id": nid, "status": note.get("status")})

# ── API: Relationships (daemon posts bulk updates) ────────────────────────────
@app.route("/api/relationships", methods=["POST"])
def update_relationships():
    db = get_db()
    data = request.get_json(force=True) or {}
    pairs = data.get("pairs", [])
    for pair in pairs:
        a, b, score = pair.get("a"), pair.get("b"), pair.get("score", 0)
        if a and b and a != b:
            db.execute("""INSERT INTO relationships (note_id_a, note_id_b, score)
                          VALUES (?,?,?)
                          ON CONFLICT(note_id_a, note_id_b) DO UPDATE SET
                            score=excluded.score, computed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
                       """, (a, b, score))
            db.execute("""INSERT INTO relationships (note_id_a, note_id_b, score)
                          VALUES (?,?,?)
                          ON CONFLICT(note_id_a, note_id_b) DO UPDATE SET
                            score=excluded.score, computed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
                       """, (b, a, score))
    db.commit()
    _broadcast("relationships_updated", {"count": len(pairs)})
    return jsonify({"ok": True, "pairs": len(pairs)})
