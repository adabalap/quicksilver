"""Scheduled work: daily brief, weekly digest, duplicate scan, consolidation, telemetry."""
from ..logging_setup import log
from ..engine import _cfg_bool
from ..anchor import _now_iso
from ..version import AGENT_VERSION


class PeriodicMixin:
    """Scheduled work: daily brief, weekly digest, duplicate scan, consolidation, telemetry."""

    def _maybe_daily_brief(self, min_interval_h: float = 20.0):
        """Once a day, have the local model write a short, human brief of the
        last 24h: what was captured, what needs attention, notable threads.
        This is the digest made *agentic* — the model composes prose, not just
        counts. Stored in settings['daily_brief'] for the PWA's Pulse screen."""
        if self._backend != "quicksilver":
            return
        import json as _j, time as _t
        db = self.memos._db()
        forced = db.execute(
            "SELECT value FROM settings WHERE key='brief_refresh_requested'").fetchone()
        force = bool(forced and forced["value"] == "1")
        # Respect the brief_mode preference: in 'ondemand' mode the brief is only
        # composed when the user explicitly taps Refresh (force). No idle work.
        if not force:
            try:
                if self._user_prefs().get("brief_mode", "ondemand") == "ondemand":
                    return
            except Exception:
                pass
        row = db.execute("SELECT value FROM settings WHERE key='daily_brief'").fetchone()
        if row and not force:
            try:
                prev = _j.loads(row["value"])
                if prev.get("ts") and (_t.time() - prev["ts"]) < min_interval_h * 3600:
                    return
            except Exception:
                pass
        if force:
            db.execute("UPDATE settings SET value='0' WHERE key='brief_refresh_requested'")
            db.commit()
        # Gather the raw material — recent notes + open actions.
        recent = db.execute(
            "SELECT n.id, n.body, "
            "(SELECT title FROM enrichments WHERE note_id=n.id ORDER BY enriched_at DESC, rowid DESC LIMIT 1) t "
            "FROM notes n WHERE n.created_at >= datetime('now','-1 day') "
            "ORDER BY n.created_at DESC LIMIT 12").fetchall()
        actions = db.execute(
            "SELECT text FROM action_items WHERE done=0 AND COALESCE(dismissed,0)=0 "
            "ORDER BY created_at DESC, rowid DESC LIMIT 10").fetchall()
        if not recent and not actions:
            return
        titles = [r["t"] or (r["body"] or "")[:40] for r in recent]
        tasks = [a["text"] for a in actions]
        # Ask for a lightly-structured brief the UI can render richly:
        #   one-line greeting, a single "focus" highlight, then 2-3 bullets.
        prompt = (
            "You are a warm, concise personal assistant writing a daily brief "
            "for the user, in the first person addressed to them. Use ONLY the "
            "facts provided — never invent names, experiments, or events. "
            "Respond in EXACTLY this format, nothing else:\n"
            "GREETING: <one short friendly sentence naming the day's main themes>\n"
            "FOCUS: <one sentence on the single most important thing to attend to>\n"
            "BULLETS:\n"
            "- <a note or task worth remembering>\n"
            "- <another>\n"
            "- <optional third>\n\n"
            f"Notes captured today: {'; '.join(t for t in titles if t) or 'none'}\n"
            f"Open tasks: {'; '.join(tasks) if tasks else 'none'}\n")
        try:
            raw = self.gemma.complete(prompt, max_tokens=220).strip()
        except Exception as e:
            log.debug(f"brief generation failed: {e}")
            return
        if not raw:
            return
        # Parse the structured shape; fall back to plain text if the model
        # didn't follow the format (older/smaller models sometimes won't).
        greeting, focus, bullets = "", "", []
        section = None
        for line in raw.splitlines():
            s = line.strip()
            if s.upper().startswith("GREETING:"):
                greeting = s.split(":", 1)[1].strip(); section = None
            elif s.upper().startswith("FOCUS:"):
                focus = s.split(":", 1)[1].strip(); section = None
            elif s.upper().startswith("BULLETS:"):
                section = "bullets"
            elif s.startswith("- ") and section == "bullets":
                b = s[2:].strip()
                if b:
                    bullets.append(b)
        brief_obj = {"ts": _t.time(), "note_count": len(recent),
                     "task_count": len(tasks)}
        if greeting or focus or bullets:
            brief_obj.update({"greeting": greeting, "focus": focus,
                              "bullets": bullets[:3]})
            # keep a plain-text fallback too, for older clients
            brief_obj["text"] = " ".join(
                x for x in [greeting, focus] + bullets if x)
        else:
            brief_obj["text"] = raw
        db.execute(
            "INSERT INTO settings (key, value) VALUES ('daily_brief', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
            "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')",
            (_j.dumps(brief_obj),))
        db.commit()
        log.info(f"  📰 daily brief composed "
                 f"({'structured' if bullets else 'plain'}, {len(raw)} chars)")

    def _maybe_weekly_digest(self):
        """Once a week (default Sunday 18:00 local), write the 'Your week' digest the
        app shows on the home screen. Also runs when the user taps Refresh."""
        if self._backend != "quicksilver" or not self.cfg.get("digest_enabled", True):
            return
        import json as _j
        from datetime import datetime as _dt
        from .. import insights
        db = self.memos._db()
        now = _dt.now()
        if not insights.due(db, now, int(self.cfg.get("digest_weekday", 6)), int(self.cfg.get("digest_hour", 18))):
            return
        if self.cfg["thermal_enabled"] and not self.thermal.wait_until_cool(lambda: self.running):
            return
        obj = insights.compose_digest(db, now, complete=self.gemma.complete)
        db.execute("UPDATE settings SET value='0' WHERE key='digest_refresh_requested'")
        if obj is None:
            db.commit(); return
        db.execute("INSERT INTO settings (key, value) VALUES ('weekly_digest', ?) ON CONFLICT(key) "
                   "DO UPDATE SET value=excluded.value, updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')",
                   (_j.dumps(obj),))
        db.commit()
        log.info(f"  🗓 weekly digest ready ({obj['generated_by']}, {obj['counts']['notes']} notes)")

    def _maybe_find_duplicates(self):
        """Hourly: record likely duplicate notes for the app's merge suggestions."""
        if self._backend != "quicksilver" or not self.cfg.get("duplicates_enabled", True):
            return
        from .. import insights
        n = insights.find_duplicates(self.memos._db(), float(self.cfg.get("dup_min_score", 0.85)))
        if n:
            log.info(f"  ⧉ {n} possible duplicate pair(s) found")

    def _consume_polish_request(self, name: str) -> bool:
        """Return True if the user requested an on-demand polish for this note,
        clearing the one-shot flag so it fires exactly once. Stateless by design
        — no flag lingers to accidentally re-polish on future enrichments."""
        try:
            db = self.memos._db()
            row = db.execute("SELECT value FROM settings WHERE key=?",
                             (f"polish_once:{name}",)).fetchone()
            requested = bool(row and row["value"] == "1")
            if requested:
                db.execute("DELETE FROM settings WHERE key=?", (f"polish_once:{name}",))
                db.commit()
            return requested
        except Exception as e:
            log.debug(f"polish flag check failed for {name}: {e}")
            return False

    def _user_prefs(self):
        """Read user preferences (JSON) from the shared settings table."""
        import json as _j
        defaults = {"enrich_min_words": 6, "summary_mode": "long",
                    "summary_min_words": 100, "brief_mode": "ondemand",
                    "followups": "on", "sms": "off"}
        try:
            row = self.memos._db().execute(
                "SELECT value FROM settings WHERE key='user_prefs'").fetchone()
            if row:
                defaults.update(_j.loads(row["value"]) or {})
        except Exception:
            pass
        return defaults

    def _maybe_consolidation(self):
        """Notice when a theme has accumulated 4+ notes this week and offer to
        draft a consolidated summary. On acceptance, compose it with the model
        and create a new note. Observation → offered action (the agentic leap)."""
        if self._backend != "quicksilver":
            return
        import json as _j, time as _t
        db = self.memos._db()

        # If the user accepted a prior offer, draft the note now.
        accepted = db.execute(
            "SELECT value FROM settings WHERE key='consolidation_accepted'").fetchone()
        if accepted and accepted["value"] == "1":
            db.execute("DELETE FROM settings WHERE key='consolidation_accepted'")
            offer_row = db.execute(
                "SELECT value FROM settings WHERE key='consolidation_offer'").fetchone()
            db.commit()
            if not offer_row:
                return
            try:
                offer = _j.loads(offer_row["value"])
            except Exception:
                return
            self._draft_consolidation(offer.get("tag"))
            db.execute("DELETE FROM settings WHERE key='consolidation_offer'")
            db.commit()
            return

        # Otherwise, look for a theme worth offering (once per ~24h).
        existing = db.execute(
            "SELECT value FROM settings WHERE key='consolidation_offer'").fetchone()
        if existing:
            try:
                prev = _j.loads(existing["value"])
                if prev.get("ts") and (_t.time() - prev["ts"]) < 24 * 3600:
                    return
            except Exception:
                pass
        # Themes with 4+ notes created in the last 7 days.
        rows = db.execute(
            "SELECT t.tag, COUNT(DISTINCT t.note_id) c FROM tags t "
            "JOIN notes n ON n.id=t.note_id "
            "WHERE n.created_at >= datetime('now','-7 days') AND n.is_daily=0 "
            "GROUP BY t.tag HAVING c >= 4 ORDER BY c DESC LIMIT 1").fetchall()
        if not rows:
            return
        tag, count = rows[0]["tag"], rows[0]["c"]
        offer = {"tag": tag, "note_count": count, "ts": _t.time()}
        db.execute(
            "INSERT INTO settings (key, value) VALUES ('consolidation_offer', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (_j.dumps(offer),))
        db.commit()
        log.info(f"  🧩 consolidation offer: #{tag} ({count} notes)")

    def _draft_consolidation(self, tag: str):
        """Compose a consolidated summary note from all notes sharing a tag."""
        if not tag:
            return
        db = self.memos._db()
        notes = db.execute(
            "SELECT n.body, (SELECT title FROM enrichments WHERE note_id=n.id "
            "ORDER BY enriched_at DESC, rowid DESC LIMIT 1) t FROM notes n JOIN tags tg ON tg.note_id=n.id "
            "WHERE tg.tag=? AND n.is_daily=0 ORDER BY n.created_at", (tag,)).fetchall()
        if not notes:
            return
        material = "\n\n".join(
            f"- {(r['t'] or '')}: {(r['body'] or '')[:300]}" for r in notes)
        prompt = (
            f"You are a helpful assistant. The user has {len(notes)} notes tagged "
            f"#{tag}. Write a single consolidated summary note that synthesises the "
            f"key points, themes, and any open threads across them. Use clear "
            f"Markdown with a short intro and bullet points. Be faithful to the "
            f"source — do not invent facts.\n\nNotes:\n{material}\n\nConsolidated note:")
        try:
            text = self.gemma.complete(prompt, max_tokens=400).strip()
        except Exception as e:
            log.error(f"consolidation draft failed: {e}")
            return
        if not text:
            return
        header = f"# Consolidated: #{tag}\n\n"
        nid = self.memos.create_note(header + text)
        # tag it so it's findable; mark it clearly
        try:
            db.execute("INSERT OR IGNORE INTO tags (note_id, tag) VALUES (?,?)", (nid, tag))
            db.execute("INSERT OR IGNORE INTO tags (note_id, tag) VALUES (?,?)", (nid, "consolidated"))
            db.commit()
        except Exception:
            pass
        log.info(f"  🧩 drafted consolidated note for #{tag}: {nid}")

    def _telemetry(self) -> dict:
        """Snapshot for the PWA Pulse dashboard: thermal, timing, counts, model."""
        stats = self.state.stats() if hasattr(self.state, "stats") else {}
        metrics = self.state.metrics() if hasattr(self.state, "metrics") else {}
        m = {**stats, **metrics}   # counts live in stats, latencies in metrics
        try:
            t = self.thermal.read_celsius()
        except Exception:
            t = None
        thermal_state = "cool"
        if t is not None:
            if t >= self.cfg.get("thermal_hot_celsius", 47):
                thermal_state = "hot"
            elif t >= self.cfg.get("thermal_resume_celsius", 43):
                thermal_state = "warm"
        model_name = getattr(self, "_model_label", None) or "model"
        medha_ok = None
        _ri = getattr(self.gemma, "release_if_idle", None)
        if callable(_ri):
            try:
                _ri()
            except Exception:
                pass
        _rh = getattr(self.gemma, "refresh_health", None)
        if callable(_rh):
            try:
                medha_ok = bool(_rh())
            except Exception:
                medha_ok = False
        return {
            "medha_ok": medha_ok,
            "soc_temp_c": round(t, 1) if t is not None else None,
            "thermal_state": thermal_state,
            "last_latency_s": round(m.get("last_latency_s", 0.0), 1),
            "avg_latency_s": round(m.get("avg_latency_s", 0.0), 1),
            "suggested": m.get("suggested", 0),
            "approved": m.get("approved", 0),
            "reenriched": m.get("reenriched", 0),
            "errors": m.get("errors", 0),
            "queue_depth": self._q.qsize() if hasattr(self, "_q") else 0,
            "jobs": (self.jobs.counts() if getattr(self, "jobs", None) is not None else None),
            "model": model_name,
            "engine": getattr(self.gemma, "active", None) or str(self.cfg.get("engine", "litert")),
            "media": (self.media.describe() if getattr(self, "media", None) else ""),
            "started_at": getattr(self, "_started_at", None),
            "version": AGENT_VERSION,
            "index": (self.memos.index_progress()
                      if (getattr(self, "embedder", None) and self.embedder.available()
                          and hasattr(self.memos, "index_progress")) else None),
            "relationships_mode": ("semantic" if (getattr(self, "embedder", None) and self.embedder.available()) else "lexical"),
            "calendar_enabled": _cfg_bool(self.cfg.get("calendar_enabled"), False),
            "updated_at": _now_iso(),
        }
