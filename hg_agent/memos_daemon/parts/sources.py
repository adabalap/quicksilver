"""How changes arrive: webhook server, Quicksilver event stream, polling, tick."""
import json
import threading, time, traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ..logging_setup import log
from ..decision import decide


class SourcesMixin:
    """How changes arrive: webhook server, Quicksilver event stream, polling, tick."""

    def _start_webhook_server(self):
        daemon = self
        path = self.cfg["webhook_path"]
        secret = self.cfg["webhook_secret"]

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # silence default noisy logging
                pass

            def _ok(self, code=200):
                # Send a valid JSON body. Memos' webhook client parses the
                # response as JSON; an empty body makes it log "unexpected end of
                # JSON input" and treat the dispatch as failed even though we
                # received it fine.
                body = b"{}"
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except Exception:
                    pass

            def do_POST(self):
                if not self.path.startswith(path):
                    self._ok(404); return
                if secret and f"token={secret}" not in (self.path.split("?", 1)[-1] if "?" in self.path else ""):
                    self._ok(401); return
                length = int(self.headers.get("Content-Length", 0) or 0)
                raw = self.rfile.read(length) if length else b""
                self._ok(200)  # ack fast
                try:
                    payload = json.loads(raw.decode("utf-8")) if raw else {}
                except Exception:
                    payload = {}
                memo = payload.get("memo") or {}
                nm = memo.get("name") or ""
                act = payload.get("activityType", "")
                if nm:
                    daemon.enqueue(nm, f"webhook {act or 'event'}",
                                   hint_text=memo.get("content", "") or "")

        self._httpd = ThreadingHTTPServer(
            (self.cfg["webhook_host"], self.cfg["webhook_port"]), Handler)
        t = threading.Thread(target=self._httpd.serve_forever, name="webhook", daemon=True)
        t.start()

    def _webhook_advertise_url(self) -> str:
        """The URL Memos should POST to. Uses webhook_advertise_url if set,
        otherwise derives a connectable one (0.0.0.0/localhost → 127.0.0.1)."""
        explicit = (self.cfg.get("webhook_advertise_url") or "").strip()
        if explicit:
            return explicit
        host = self.cfg["webhook_host"]
        if host in ("0.0.0.0", "", "localhost", "::"):
            host = "127.0.0.1"
        return f"http://{host}:{self.cfg['webhook_port']}{self.cfg['webhook_path']}"

    def _run_qs_events(self):
        """Event-driven QS mode: the agent subscribes to the PWA's own SSE
        stream (/api/events), so a note created / edited / status-changed in
        the app is enqueued within milliseconds — no waiting for a poll tick.
        A slow safety poll (safety_poll_seconds) catches anything missed while
        the SSE link was down; the watermark makes it idempotent."""
        worker = threading.Thread(target=self._worker_loop, name="worker", daemon=True)
        worker.start()
        threading.Thread(target=self._qs_sse_loop, name="sse", daemon=True).start()
        log.info(f"  ⚡ event mode: SSE listener + safety poll every "
                 f"{self.cfg['safety_poll_seconds']}s")
        _beat = int(self.cfg.get("heartbeat_interval_seconds", 30))
        log.info(f"  idle tuning: loop tick {self.cfg.get('main_loop_tick_seconds',5)}s · "
                 f"heartbeat {'disabled' if _beat==0 else str(_beat)+'s'}")
        # Durable queue first: re-load jobs the last run left queued/running.
        self._jobs_boot()
        # Prime once so anything changed while we were down is caught.
        self._enqueue_changed_since_watermark()
        self._rescue_stuck_force(0)       # nothing is in flight at boot
        self._enqueue_stale_ask_requests()
        self.memos.heartbeat(self._telemetry())
        self.memos.auto_backup()          # gate inside: at most one per 24h
        last_poll = time.time()
        last_beat = time.time()
        last_bk = time.time()
        last_brief = 0.0   # 0 → first brief attempt happens on the next tick
        last_dup = 0.0
        tick = max(1, int(self.cfg.get("main_loop_tick_seconds", 5)))
        beat_every = int(self.cfg.get("heartbeat_interval_seconds", 30))
        while self.running:
            time.sleep(tick)
            now = time.time()
            # Liveness vs telemetry are separated on purpose.
            #
            # The UI marks the agent "down" if the heartbeat is older than ~60s.
            # Bundling the liveness stamp with telemetry meant a battery-friendly
            # heartbeat_interval_seconds (e.g. 3600) showed "Agent down" for 59
            # minutes of every hour, even though the agent was working fine.
            #
            # So: stamp liveness on EVERY tick — one tiny settings write, cheap
            # enough to do often — and gather the expensive telemetry (thermal
            # zones, latency stats) only on the configured interval.
            self.memos.heartbeat()
            self._jobs_tick(recover=(now - last_brief >= 60))   # backoffs / app Retry
            if beat_every > 0 and now - last_beat >= beat_every:
                self.memos.heartbeat(self._telemetry())
                last_beat = now
            if now - last_bk >= 3600:     # hourly: backup
                self.memos.auto_backup()
                last_bk = now
            if now - last_brief >= 60:     # minutely: brief + consolidation checks
                try:
                    self._maybe_daily_brief()
                except Exception as e:
                    log.debug(f"daily brief skipped: {e}")
                try:
                    self._maybe_weekly_digest()
                except Exception as e:
                    log.debug(f"weekly digest skipped: {e}")
                if now - last_dup >= 3600:
                    last_dup = now
                    try:
                        self._maybe_find_duplicates()
                    except Exception as e:
                        log.debug(f"duplicate scan skipped: {e}")
                try:
                    self._maybe_consolidation()
                except Exception as e:
                    log.debug(f"consolidation skipped: {e}")
                try:
                    self._maybe_followups()
                except Exception as e:
                    log.debug(f"task follow-ups skipped: {e}")
                try:
                    self._maybe_sms()
                except Exception as e:
                    log.debug(f"sms source skipped: {e}")
                try:
                    self._maybe_captures()
                except Exception as e:
                    log.debug(f"captures source skipped: {e}")
                try:
                    self._maybe_saved()
                except Exception as e:
                    log.debug(f"saved source skipped: {e}")
                try:
                    self._maybe_medha_brief()
                except Exception as e:
                    log.debug(f"medha brief skipped: {e}")
                # Act on any calendar deletions the user requested from the UI.
                try:
                    self._process_calendar_deletions()
                except Exception as e:
                    log.debug(f"calendar deletions skipped: {e}")
                try:
                    self._reindex_batch()          # passage index catch-up (bounded, thermal-aware)
                    if getattr(self, "jobs", None) is not None:
                        self.jobs.prune()
                except Exception as e:
                    log.debug(f"reindex/prune skipped: {e}")
                last_brief = now
            if now - last_poll >= self.cfg["safety_poll_seconds"]:
                self._enqueue_changed_since_watermark()
                self._promote_stranded_dirty()
                self._rescue_stuck_force()
                self._enqueue_stale_ask_requests()
                last_poll = now

    # ── typing settle window ────────────────────────────────────────────────────────────────────────────
    # An autosave sends a note_updated every few seconds while you type. Each used to queue the note, and the
    # first one past the word threshold ran the model on a half-written note. Now a note is queued only after it
    # has been quiet for `enrich_quiet_seconds` (every new edit restarts the wait), unless it was committed
    # (status 'force': editor closed or "Run AI now"), which goes straight through.
    def _edit_state(self):
        st = getattr(self, "_edit_st", None)
        if st is None:
            st = self._edit_st = {"lock": threading.Lock(), "at": {}, "timers": {}}
        return st

    def _note_status(self, nid):
        try:
            r = self.memos._db().execute("SELECT status FROM note_state WHERE note_id=?", (nid,)).fetchone()
            return (r["status"] if r else "") or ""
        except Exception:
            return ""

    def _note_event(self, nid, reason=""):
        quiet = float(self.cfg.get("enrich_quiet_seconds", 60))
        status = self._note_status(nid)
        st = self._edit_state()
        with st["lock"]:
            t = st["timers"].pop(nid, None)
            if t is not None:
                t.cancel()
            if quiet <= 0 or status == "force":
                st["at"].pop(nid, None)
                go_now = True
            else:
                st["at"][nid] = time.monotonic()
                go_now = False
                if status != "dirty":                       # dirty = editor open, waits for the commit
                    t = threading.Timer(quiet + 0.5, self._note_settled, args=(nid, reason))
                    t.daemon = True
                    st["timers"][nid] = t
                    t.start()
        if go_now:
            self.enqueue(nid, reason)

    def _note_settled(self, nid, reason=""):
        st = self._edit_state()
        with st["lock"]:
            st["timers"].pop(nid, None)
        if self.running:
            self.enqueue(nid, (reason or "sse") + " (settled)")

    def _still_typing(self, nid, status) -> bool:
        """True (and a timer set) when the note was edited within the quiet window and is not committed."""
        quiet = float(self.cfg.get("enrich_quiet_seconds", 60))
        if quiet <= 0 or status == "force":
            return False
        st = self._edit_state()
        with st["lock"]:
            last = st["at"].get(nid)
            if last is None:
                return False
            left = quiet - (time.monotonic() - last)
            if left <= 0:
                st["at"].pop(nid, None)
                return False
            if nid not in st["timers"]:
                t = threading.Timer(left + 0.5, self._note_settled, args=(nid, "deferred"))
                t.daemon = True
                st["timers"][nid] = t
                t.start()
        return True

    def _qs_sse_loop(self):
        """Long-lived SSE client against the PWA. Enqueues note ids on
        note_created / note_updated (user actions), and ask: jobs on
        ask_requested (a new Ask-your-notes question). Our own enrichment
        writes arrive as note_enriched and are deliberately ignored —
        otherwise the agent would react to itself. Reconnects with capped
        backoff."""
        import requests as _rq
        url = (self.cfg.get("quicksilver_url", "http://127.0.0.1:5230")
               .rstrip("/") + "/api/events")
        backoff = 1.0
        while self.running:
            try:
                with _rq.get(url, stream=True, timeout=(5, None)) as r:
                    r.raise_for_status()
                    log.info(f"  ⚡ SSE connected: {url}")
                    backoff = 1.0
                    event = None
                    for line in r.iter_lines(decode_unicode=True):
                        if not self.running:
                            return
                        if line is None:
                            continue
                        if line.startswith("event:"):
                            event = line[6:].strip()
                        elif line.startswith("data:"):
                            if event in ("note_created", "note_updated"):
                                try:
                                    nid = json.loads(line[5:].strip()).get("id", "")
                                except Exception:
                                    nid = ""
                                if nid:
                                    self._note_event(nid, f"sse:{event}")
                            elif event == "attachment_added":
                                self._media_scan()               # new capture / "Try again"
                            elif event in ("bills_changed", "reminders_changed", "followups_changed", "action_updated"):
                                self._maybe_medha_brief(force_check=True)   # bill paid or dismissed in the app
                            elif event == "jobs_changed":
                                self._jobs_tick(recover=False)   # user hit Retry in the app
                            elif event == "ask_requested":
                                try:
                                    rid = json.loads(line[5:].strip()).get("request_id", "")
                                except Exception:
                                    rid = ""
                                if rid:
                                    self.enqueue(f"ask:{rid}", "sse:ask_requested",
                                                force_priority=0)
                        elif line == "":
                            event = None
            except Exception as e:
                if not self.running:
                    return
                log.warning(f"  ⚡ SSE down ({e}) — retry in {backoff:.0f}s "
                            f"(safety poll still active)")
                time.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    def _run_polling(self):
        idle_cycles = 0
        while self.running:
            try:
                saw_changes = self._tick()
                if saw_changes:
                    self._idle = self.cfg["poll_interval_seconds"]
                    idle_cycles = 0
                else:
                    idle_cycles += 1
                    base = self.cfg["poll_interval_seconds"]
                    self._idle = min(max(self._idle, base) * 2,
                                     self.cfg["idle_backoff_max_seconds"])
                    if idle_cycles % self.cfg.get("heartbeat_every_idle_cycles", 5) == 0:
                        t = self.thermal.read_celsius()
                        log.info(f"idle — next poll in {self._idle}s"
                                 + (f", SoC {t:.0f}°C" if t is not None else ""))
            except Exception as e:
                log.error(f"tick error: {e}\n{traceback.format_exc()}")
                self._idle = self.cfg["poll_interval_seconds"]
                idle_cycles = 0
            self._sleep(self._idle)

    def _sleep(self, secs):
        for _ in range(int(secs)):
            if not self.running:
                return
            time.sleep(1)

    def _tick(self) -> bool:
        changed = list(self.memos.iter_changed_since(self.state.watermark, self.cfg["page_size"]))
        if not changed:
            return False
        newest = changed[0].get("updateTime", "")
        log.info(f"{len(changed)} changed note(s).")
        any_change = False
        for note in changed:
            if not self.running:
                break
            # Checkbox sync first — same logic as the webhook path, so the
            # legacy polling fallback (used when the webhook port is busy)
            # also gets bi-directional todo checkbox sync.
            if self.cfg.get("todo_enabled", True):
                nm = note.get("name", "")
                if self._intercept_checkbox_event(nm, note):
                    any_change = True
                    continue
            action = decide(note, self.state, self.cfg)
            if action == "skip":
                self.state.bump("skipped")
                continue
            if action in ("suggest", "reenrich", "finalize"):
                if self.cfg["thermal_enabled"]:
                    if not self.thermal.wait_until_cool(lambda: self.running):
                        log.info("Deferring rest until cooler.")
                        self.state.save()
                        return True
            self._handle(action, note)
            any_change = True
            self.state.save()
        if self.running and newest:
            self.state.watermark = newest
            self.state.save()
        if any_change and self.cfg["dashboard_enabled"]:
            self._update_dashboard()
        if any_change and self.cfg.get("connections_enabled"):
            self._update_connections()
        if any_change and self.cfg.get("todo_enabled", True):
            self._update_todo_dashboard()
        # Return True because we saw changed notes this poll → stay responsive.
        return True
