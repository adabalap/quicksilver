"""The Quicksilver agent: pulls notes that need work, runs the on-device model, writes results back.

MemosDaemon is assembled from mixins in parts/ (job queue, periodic work, recovery, calendar,
media, ask, sources, indexing, corpus, dashboards, to-dos). This file keeps the lifecycle: startup,
the run loop, the worker loop and the per-note handler."""
import sys, json, time, queue, signal, threading, traceback

from .logging_setup import log
from .state import StateStore
from .memos_client import MemosClient
from .pending import PendingWrites
from .thermal import ThermalGuard
from .media import Router as MediaRouter
from .engine import GemmaEngine, _cfg_bool
from .decision import decide
from .anchor import parse_anchor, user_text_of, text_hash, content_hash, _now_iso
from .formatting import build_suggested, build_approved
from .calendar_client import CalendarClient
from .embeddings import Embedder
from .todo_store import TodoStore
from .qs_client import QuicksilverClient
from .jobs import JobStore
from .version import AGENT_VERSION
from .parts.job_queue import JobQueueMixin
from .parts.periodic import PeriodicMixin
from .parts.recovery import RecoveryMixin
from .parts.calendar_sync import CalendarSyncMixin
from .parts.media_jobs import MediaJobsMixin
from .parts.ask_jobs import AskJobsMixin
from .parts.sources import SourcesMixin
from .parts.indexing import IndexingMixin
from .parts.corpus import CorpusMixin
from .parts.dashboards import DashboardsMixin
from .parts.todos import TodosMixin
from .parts.followups import FollowupsMixin
from .parts.sms_source import SmsSourceMixin
from .parts.brief_source import BriefSourceMixin
from .parts.captures_source import CapturesSourceMixin
from .parts.saved_source import SavedSourceMixin
from .ask_parse import _ASK_MARK, _parse_ask_answer     # re-exported: tests and evalkit import it from here
from .version import AGENT_VERSION                       # re-exported


class MemosDaemon(JobQueueMixin, PeriodicMixin, RecoveryMixin, CalendarSyncMixin, MediaJobsMixin, AskJobsMixin, SourcesMixin, IndexingMixin, CorpusMixin, DashboardsMixin, TodosMixin, FollowupsMixin, SmsSourceMixin, BriefSourceMixin, CapturesSourceMixin, SavedSourceMixin):
    def __init__(self, cfg):
        self.cfg = cfg
        self.running = True
        self.state = StateStore(cfg["state_file"])
        # Client selection: QuicksilverClient (SQLite-direct, preferred) or
        # legacy MemosClient (HTTP API). Config key "backend" selects which.
        if cfg.get("backend", "quicksilver") == "quicksilver" and cfg.get("db_path"):
            self.memos = QuicksilverClient(
                db_path=cfg["db_path"],
                webhook_url=cfg.get("quicksilver_url", "http://127.0.0.1:5230"),
                max_retries=cfg.get("write_max_retries", 3),
                backoff_base=cfg.get("write_backoff_base", 0.5),
            )
            self._backend = "quicksilver"
        else:
            self.memos = MemosClient(
                cfg["memos_base_url"], cfg["memos_token"],
                max_retries=cfg.get("write_max_retries", 4),
                backoff_base=cfg.get("write_backoff_base", 0.5),
                backoff_cap=cfg.get("write_backoff_cap", 8.0),
            )
            self._backend = "memos"
        self.pending = PendingWrites(
            cfg.get("pending_writes_file") or (cfg["state_file"] + ".pending"))
        self.thermal = ThermalGuard(cfg)
        _eng = str(cfg.get("engine", "auto")).lower()
        if _eng == "auto":
            from .auto_engine import AutoEngine
            self.gemma = AutoEngine(cfg)
        elif _eng == "medha":
            from .medha_engine import MedhaEngine
            self.gemma = MedhaEngine(cfg)
        else:
            self.gemma = GemmaEngine(cfg["model_path"], cfg=cfg)
        self.media = MediaRouter(cfg, self.gemma)
        log.info(f"media (photos & voice): {self.media.describe()}")
        # Resolve a friendly model label once — used by Pulse telemetry.
        import os as _os
        _mp = ("medha" if str(cfg.get("engine", "auto")).lower() in ("medha", "auto")
               else (cfg.get("model_path") or ""))
        _nm = _os.path.basename(_mp.rstrip("/")) or "model"
        for _ext in (".litertlm", ".task", ".bin", ".gguf"):
            if _nm.endswith(_ext):
                _nm = _nm[:-len(_ext)]; break
        self._model_label = _nm or "model"
        self.calendar = CalendarClient(cfg)
        # One shared embedder for semantic relationships (fail-safe: if no model
        # is configured/loadable, Corpus falls back to lexical similarity).
        # embeddings_enabled=false turns semantic search off completely: nothing is loaded, no sidecar, no indexing.
        # Search, Ask and relationships then use keywords only. Switch it back on to rebuild the index.
        self.embedder = Embedder(cfg.get("embed_model", "") if _cfg_bool(cfg.get("embeddings_enabled"), True) else "",
                                 cfg.get("embed_cache_dir", ""),
                                 fallback_model=cfg.get("embed_model_fallback", ""),
                                 backend=cfg.get("embed_backend", "auto"),
                                 onnx_dir=cfg.get("embed_onnx_dir", ""),
                                 overrides={
                                     "floor_search": cfg.get("embed_floor_search"),
                                     "floor_ask":    cfg.get("embed_floor_ask"),
                                     "margin":       cfg.get("embed_margin"),
                                     "z_min":        cfg.get("embed_z_min"),
                                 }) \
            if cfg.get("correlation_enabled", True) else None
        QuicksilverClient.CHUNK_PARAMS = (
            int(cfg.get("embed_chunk_chars", 500)),
            int(cfg.get("embed_chunk_overlap", 80)),
            int(cfg.get("embed_max_chunks", 40)))
        # Hand the embedder to the client so write_enrichment can persist each
        # note's vector to notes.embedding (feeding both the corpus and search).
        if self.embedder is not None:
            self.memos.embedder = self.embedder
        _todo_file = (cfg.get("todo_file") or
                      (cfg["state_file"].replace(".json", "") + ".todos.json"))
        self.todos = TodoStore(_todo_file)
        self._idle = cfg["poll_interval_seconds"]
        # Work queue holds note NAMES ("memos/xxx") to (re)evaluate. Both the
        # webhook server and the poller enqueue; a single worker drains it so
        # the one in-memory model only ever runs one inference at a time.
        # A PriorityQueue lets likely-urgent notes jump ahead (cheap keyword
        # pre-pass) without starving others — a monotonic counter breaks ties
        # in FIFO order.
        self._q: queue.PriorityQueue = queue.PriorityQueue()
        self._seq = 0
        self._queued: set = set()           # dedup: names currently waiting
        self._inflight = None               # name the worker is running right now
        self._qlock = threading.Lock()
        # Durable queue (table agent_jobs): the source of truth the in-memory
        # PriorityQueue above is rebuilt from, so a kill/restart loses nothing and
        # failures retry with backoff → dead-letter instead of looping forever.
        self.jobs = None
        if self._backend == "quicksilver" and cfg.get("durable_queue", True):
            try:
                self.jobs = JobStore(self.memos._db,
                                     lease_seconds=int(cfg.get("job_lease_seconds", 1200)),
                                     backoff=tuple(cfg.get("job_backoff_seconds", (30, 120, 600))))
                self.jobs.ensure_schema()
            except Exception as e:
                log.warning(f"durable queue unavailable ({e}) — using in-memory queue only")
                self.jobs = None
        self._httpd = None
        signal.signal(signal.SIGINT, self._sig)
        signal.signal(signal.SIGTERM, self._sig)

    def run(self):
        # Quicksilver: the PWA writes to SQLite directly, so there is nothing
        # to receive webhooks FROM — DB polling is the event source and it is
        # cheap (an indexed SELECT). Webhook mode only applies to Memos.
        if self._backend == "quicksilver":
            mode = "db-poll"
        else:
            mode = "webhook+poll" if self.cfg["webhook_enabled"] else "poll"
        self._started_at = _now_iso()
        log.info(f"Hg agent v{AGENT_VERSION} starting…")
        if self._backend == "quicksilver":
            log.info(f"  Quicksilver DB {self.cfg.get('db_path')} · mode {mode} "
                     f"· dry_run {self.cfg['dry_run']} · thermal {'on' if self.cfg['thermal_enabled'] else 'off'}")
        else:
            log.info(f"  Memos {self.cfg['memos_base_url']} · mode {mode} "
                     f"· dry_run {self.cfg['dry_run']} · thermal {'on' if self.cfg['thermal_enabled'] else 'off'}")
        # Probe the relationship backend now so the operator sees the mode up
        # front (semantic vs lexical), rather than only on first correlation.
        if self.cfg.get("correlation_enabled", True) and self.embedder is not None:
            backend = "semantic (embeddings)" if self.embedder.available() else "lexical (keyword)"
            log.info(f"  relationships: {backend}")
        from .prompt import load_prompt, PROMPT_SCHEMA_VERSION
        _prompt = load_prompt(self.cfg)
        _prompt_src = (self.cfg.get("prompt_file") or "built-in")
        log.info(f"  prompt: schema v{PROMPT_SCHEMA_VERSION}, source={_prompt_src}, "
                 f"{len(_prompt)} chars")
        if not self.memos.ping():
            log.error("Backend unreachable "
                      + ("(Quicksilver DB)" if self._backend == "quicksilver"
                         else "(Memos API)") + ".")
            sys.exit(1)
        if self._backend == "quicksilver":
            # The PWA renders stats, actions, and connections natively from the
            # DB tables — maintained markdown "dashboard notes" are a Memos-era
            # concept and would just pollute the feed. Relationship pairs are
            # still computed and written to the DB (see _update_connections).
            pass
        elif self.cfg["dashboard_enabled"] and not self.state.dashboard_id:
            found = self._find_existing_dashboard("dashboard")
            if found:
                self.state.dashboard_id = found
                self.state.save()
                log.info(f"  📌 adopted existing dashboard: {found}")
        if self.cfg.get("connections_enabled") and self._backend != "quicksilver":
            if not self.state.connections_id:
                found = self._find_existing_dashboard("connections")
                if found:
                    self.state.connections_id = found
                    self.state.save()
                    log.info(f"  🔗 adopted existing connections map: {found}")
            # Always re-render on startup: catches notes added while the daemon
            # was down, and creates the note for the first time if needed.
            try:
                self._update_connections()
                log.info("  🔗 connections dashboard rendered")
            except Exception as e:
                log.warning(f"startup connections render failed: {e}")

        if self.cfg.get("todo_enabled", True) and self._backend != "quicksilver":
            if not self.state.todo_id:
                found = self._find_existing_dashboard("todo")
                if found:
                    self.state.todo_id = found
                    self.state.save()
                    log.info(f"  ✅ adopted existing todo dashboard: {found}")
            try:
                self._update_todo_dashboard()
                log.info("  ✅ todo dashboard rendered")
            except Exception as e:
                log.warning(f"startup todo render failed: {e}")

        if self._backend == "quicksilver":
            if self.cfg.get("connections_enabled", True):
                try:
                    self._update_connections()
                    log.info("  🔗 relationships computed")
                except Exception as e:
                    log.warning(f"startup relationships failed: {e}")
            if not (self.embedder and self.embedder.available()):
                # Embeddings are off (or could not load): drop the published index state so the app does not show
                # a "getting your notes ready for search" bar for an index nobody is building.
                try:
                    db = self.memos._db()
                    db.execute("DELETE FROM settings WHERE key='embed_active'")
                    db.commit()
                except Exception:
                    pass
                log.info("  🔎 semantic search is off — search, Ask and connections use keywords")
            if self.embedder and self.embedder.available():
                self._start_embed_sidecar()
                try:
                    self._backfill_embeddings()
                except Exception as e:
                    log.warning(f"embedding backfill failed: {e}")
            self._run_qs_events()
        elif self.cfg["webhook_enabled"]:
            self._run_hybrid()
        else:
            self._run_polling()

        self.state.save()
        log.info("Stopped. Stats: " + json.dumps(self.state.stats()))
        self.gemma.close()

    # ── Hybrid mode: webhook server + worker + slow safety poll ──────────────
    def _run_hybrid(self):
        worker = threading.Thread(target=self._worker_loop, name="worker", daemon=True)
        worker.start()
        try:
            self._start_webhook_server()
        except OSError as e:
            log.error(f"Could not start webhook server on "
                      f"{self.cfg['webhook_host']}:{self.cfg['webhook_port']}: {e}")
            log.error("Another process may be using that port. Falling back to "
                      "fast polling. (Free the port and restart for webhook mode.)")
            # Stop the worker and run the simple polling loop instead.
            self.running_webhook = False
            self._run_polling()
            return
        log.info(f"  webhook listening on http://{self.cfg['webhook_host']}:"
                 f"{self.cfg['webhook_port']}{self.cfg['webhook_path']}")
        advertise = self._webhook_advertise_url()
        log.info(f"  ➜ In Memos, set the webhook URL to: {advertise}")
        if "0.0.0.0" in advertise:
            log.warning("  ⚠ advertise URL contains 0.0.0.0 — Memos cannot dial "
                        "that. Set webhook_advertise_url to a reachable address.")
        log.info(f"  safety poll every {self.cfg['safety_poll_seconds']}s")
        # Prime once at startup so anything changed while we were down is caught.
        self._enqueue_changed_since_watermark()
        last_poll = time.time()
        tick = max(1, int(self.cfg.get("main_loop_tick_seconds", 5)))
        while self.running:
            time.sleep(tick)
            if time.time() - last_poll >= self.cfg["safety_poll_seconds"]:
                last_poll = time.time()
                try:
                    n = self._enqueue_changed_since_watermark()
                    if n:
                        log.info(f"  safety poll enqueued {n} note(s)")
                    else:
                        t = self.thermal.read_celsius()
                        log.info("  heartbeat — idle, webhook live"
                                 + (f", SoC {t:.0f}°C" if t is not None else ""))
                except Exception as e:
                    log.error(f"safety poll error: {e}")
        worker.join(timeout=5)

    def _worker_loop(self):
        """Drains the work queue one note (or ask: request) at a time, highest
        priority first — a single worker so only one Gemma inference ever
        runs at once."""
        while self.running:
            _prio, _seq, name = self._q.get()
            if name is None:
                break
            kind = self._kind_of(name)
            jobs = getattr(self, "jobs", None)
            job = None
            # Claim BEFORE leaving the dedup set, so a concurrent _jobs_tick can't
            # see "queued in the table, not in memory" and dispatch it twice.
            if jobs is not None:
                try: job = jobs.claim(kind, name)
                except Exception as e: log.debug(f"claim failed for {name}: {e}")
            with self._qlock:
                self._queued.discard(name)
                self._inflight = name
            outcome, err = "done", ""
            try:
                self._flush_pending()
                if kind == "ask":
                    self._process_ask(name[len("ask:"):])
                elif kind == "media":
                    self._process_media(name[len("media:"):])
                else:
                    self._process_one(name)
                    self._settle_force(name)
                if job is not None:
                    outcome, err = self._job_outcome(kind, name)
            except Exception as e:
                log.error(f"worker error on {name}: {e}\n{traceback.format_exc()}")
                outcome, err = "retry", f"{type(e).__name__}: {e}"
                if kind == "note":
                    try: self._settle_force(name)
                    except Exception: pass
            finally:
                self._inflight = None
                self._job_finish(job, kind, name, outcome, err)
                self._q.task_done()

    def _flush_pending(self):
        """Re-apply enrichments that failed to write during a Memos outage.
        Called opportunistically; only does work when something is pending and
        Memos is reachable, so it is cheap in the common case.

        Distinguishes:
          - Success → discard from pending, update state.
          - Transient failure (None) → keep and retry next time.
          - Permanent failure (PATCH_PERMANENT_ERROR: 404 deleted, 400 too long,
            401/403 auth) → discard from pending with a clear log message.
            Retrying these forever just fills the log with warnings — they will
            never succeed.
        """
        if len(self.pending) == 0:
            return
        if not self.memos.health():
            return  # still down; try again next time
        from .memos_client import MemosClient as _MC
        for entry in list(self.pending.items()):
            name = entry["name"]
            content = self._truncate_for_memos(entry["content"], name)
            ut = self.memos.update_content(name, content)
            if ut is _MC.PATCH_PERMANENT_ERROR:
                # Figure out why and give a human-readable reason
                reason = "permanent error (note deleted, too long, or auth failure)"
                self.pending.discard_permanent(name, reason)
                continue
            if not ut:
                log.warning(f"  ⏳ pending write for {name} still failing — keeping it")
                continue
            seen = self._authoritative_update_time(name, ut)
            if entry.get("kind") == "note":
                self.state.set(name, status=entry.get("status", "suggested"),
                               hash=entry.get("hash", ""), seen=seen)
                self.state.note_succeeded(name)
            self.pending.discard(name)
            log.info(f"  ✅ re-applied pending enrichment: {name} "
                     f"({len(self.pending)} left)")
        self.state.save()

    def _process_one(self, name: str):
        """Fetch a single note fresh and process it through the state machine."""
        log.info(f"  ▸ processing {name}")
        note = self.memos.get_memo(name)
        if note is None:
            log.info(f"    (note {name} unreadable/deleted — skipping)")
            return  # deleted or unreadable; nothing to do
        if self._still_typing(name, note.get("status")):
            log.debug(f"    ({name} edited moments ago — waiting for it to settle)")
            return
        body0 = (note.get("content") or note.get("body") or "").strip()
        note["_body0"] = body0                      # what the model will be given, for the edited-meanwhile check
        # Text the agent read from the note's photos / recordings counts as part
        # of what's being enriched (title, tags, tasks, dates come from it), but
        # the note body itself stays the user's words: the combined text is only
        # the enrichment INPUT, and meta_only keeps the rewrite ('polished') off
        # so none of it is ever pasted into the body behind their back.
        _mt = note.get("media_text") or ""
        if _mt:
            note = dict(note)
            note["content"] = ((note.get("content") or "").strip() + "\n\n" + _mt).strip()
            note["meta_only"] = 1
        # ── Checkbox-only change? Sync state, skip enrichment entirely ──────
        if self.cfg.get("todo_enabled", True):
            if self._intercept_checkbox_event(name, note):
                return
        # ── Bulk-import opt-out: never auto-enrich notes flagged enrich_optout ─
        # Set by `hg_ui import-keep` (see --enrich). One local Gemma call per
        # note means a 500-note import would occupy the phone for hours and trip
        # the thermal guard, so imports land inert and the user enriches what
        # they actually care about via ⋯ → Re-run (which clears the flag).
        #
        # The watermark MUST advance here. The agent enqueues on
        # `updated_at > watermark` and never consults status, so a skip that
        # doesn't advance it would re-enqueue this note on every single poll —
        # a busy-loop that burns battery forever.
        if note.get("enrich_optout"):
            log.info(f"    (note {name} opted out of auto-enrichment — skipping)")
            ut = note.get("updateTime", "")
            if ut and ut > self.state.watermark:
                self.state.watermark = ut
            self.state.bump("skipped"); self.state.save()
            self._release_force(name, "opted out of enrichment")
            return
        # ── User preference: skip enriching trivial (very short) notes ──────
        # A two-word note doesn't need a title, tags, and a summary. This is the
        # "don't run the agent when it isn't needed" control, tunable in settings.
        try:
            prefs = self._user_prefs()
            min_words = int(prefs.get("enrich_min_words", 6))
            content = (note.get("content") or note.get("body") or "").strip()
            # Event/date notes must ALWAYS be enriched regardless of length —
            # a short event note ("📅 Dentist\nWhen: ...") needs the agent to
            # extract the date and create the calendar event. Detect a date-ish
            # note by the 📅 marker or an ISO-ish date pattern.
            import re as _re
            looks_like_event = ("📅" in content
                or _re.search(r"\b\d{4}-\d{2}-\d{2}\b", content)
                or _re.search(r"\bwhen:\s", content, _re.I))
            # Never skip protected/checklist bookkeeping; only skip plain short notes.
            if (min_words > 0 and not note.get("protected")
                    and not looks_like_event
                    and note.get("note_type", "text") != "checklist"
                    and len(content.split()) < min_words):
                log.info(f"    (note {name} below {min_words}-word threshold — skipping enrichment)")
                ut = note.get("updateTime", "")
                if ut and ut > self.state.watermark:
                    self.state.watermark = ut
                self.state.bump("skipped"); self.state.save()
                self._index_quietly(name)     # not enriched, but still searchable
                self._release_force(name, f"below {min_words}-word threshold")
                return
        except Exception as e:
            log.debug(f"pref check skipped: {e}")
        action = decide(note, self.state, self.cfg)
        log.info(f"    decision: {action}")
        # Advance watermark for every note we look at, including skips — this
        # prevents the safety poll from re-queuing already-seen notes forever.
        ut = note.get("updateTime", "")
        if ut and ut > self.state.watermark:
            self.state.watermark = ut

        # QUICK EVENTS: a user-created reminder (source='user') is authoritative —
        # the user typed the exact date/time. Create its calendar event NOW, on
        # first sight, without waiting for enrichment + manual approval. Agent-
        # extracted reminders still wait for approval (they may be wrong). This
        # is idempotent: _create_calendar_from_db only touches rows without a
        # calendar_event_id, and marks them 'created' so it never duplicates.
        if self._backend == "quicksilver":
            try:
                db = self.memos._db()
                has_user_rem = db.execute(
                    "SELECT 1 FROM reminders WHERE note_id=? AND "
                    "COALESCE(source,'agent')='user' AND "
                    "(calendar_event_id IS NULL OR calendar_event_id='')",
                    (name,)).fetchone()
                if has_user_rem:
                    self._create_calendar_from_db(name, db, user_only=True)
            except Exception as e:
                log.error(f"  calendar (user reminder) skipped for {name}: {e}")

        if action == "skip":
            self.state.bump("skipped")
            self.state.save()
            return
        if action in ("suggest", "reenrich", "finalize") and self.cfg["thermal_enabled"]:
            if not self.thermal.wait_until_cool(lambda: self.running):
                self.enqueue(name, "deferred (hot)")
                time.sleep(self.cfg["thermal_poll_seconds"])
                return
        self._handle(action, note)
        self.state.save()
        # Dashboards update after every enrichment action (not on skip, which
        # is the common case and would be wasteful).
        if self.cfg["dashboard_enabled"]:
            self._update_dashboard()
        if self.cfg.get("connections_enabled"):
            self._update_connections()
        if self.cfg.get("todo_enabled", True):
            self._update_todo_dashboard()
        log.info(f"  ▸ done {name}")

    def _handle(self, action, note) -> bool:
        name = note.get("name", "?")
        content = note.get("content", "")

        # Quicksilver: content IS the pure user body (no anchors/markers).
        # Memos legacy: user_text_of() strips AI sections from the note body.
        if self._backend == "quicksilver":
            cur_user = content.strip()
        else:
            cur_user = user_text_of(content)

        # ── APPROVE (QS): user approved in the PWA — no inference needed ─────
        # One-time approval side-effects: calendar events from stored reminders,
        # state bookkeeping, corpus refresh for relationships.
        if action == "approve_qs":
            try:
                db = self.memos._db()
                # ── Adoption: the approved polished text becomes the new body.
                # The raw original is archived as a 'pre_adopt' revision. From
                # here the user edits the adopted text; the next edit → force →
                # re-enrich → approve cycle continues on the living document.
                new_body = self.memos.adopt_polished(name)
                if new_body is not None:
                    cur_user = new_body.strip()
                    log.info(f"  📝 adopted polished text as new body: {name}")
                    # Tell the PWA so open clients refresh instantly.
                    self.memos._notify_webhook(name, {}, "approved")
                self._create_calendar_from_db(name, db)
                # Store the MARKER-STRIPPED content hash so a later highlight /
                # erase (presentation-only) doesn't look like a content change.
                self.state.set(name, status="approved",
                               hash=content_hash(cur_user), seen=_now_iso())
                self.state.bump("approved")
                self._corpus_dirty = True
                log.info(f"  ✅ approval recorded: {name}")
                return True
            except Exception as e:
                log.error(f"  approve_qs failed for {name}: {e}")
                return False

        # ── Choose the SOURCE TEXT to enrich ──────────────────────────────────
        source_text = cur_user
        hints = None
        if action == "reenrich":
            if self._backend == "quicksilver":
                # Hints from last enrichment row in the DB
                try:
                    enr_row = self.memos._db().execute(
                        "SELECT title FROM enrichments WHERE note_id=? "
                        "ORDER BY enriched_at DESC, rowid DESC LIMIT 1", (name,)).fetchone()
                    tag_rows = self.memos._db().execute(
                        "SELECT tag FROM tags WHERE note_id=?", (name,)).fetchall()
                    hints = {
                        "prior_title": (enr_row["title"] if enr_row else ""),
                        "prior_tags":  [r["tag"] for r in tag_rows],
                    }
                except Exception:
                    hints = {}
            else:
                anchor = parse_anchor(content) or {}
                hints = {
                    "prior_title": (anchor.get("title") or "").strip(),
                    "prior_tags": anchor.get("tags") or [],
                }
            log.info(f"    ✎ reprocessing current note body: {name}")

        # ── FINALIZE (approve) ────────────────────────────────────────────────
        if action == "finalize":
            log.info(f"    running inference (finalize) on {name}…")
            enrichment = self.gemma.enrich(cur_user)
            if enrichment is None:
                self.state.set(name, status="pending")
                self.state.note_failed(name)
                self.state.bump("errors")
                log.warning(f"  ✗ finalize inference failed: {name}")
                return False
            self.state.record_latency(enrichment.get("_latency_s", 0.0))
            self._inject_related(name, source_text, enrichment)

            if self._backend == "quicksilver":
                # Write all enrichment output directly to the DB — clean, fast,
                # no markdown serialisation, no content size limit.
                ok = self.memos.write_enrichment(name, enrichment, "approved")
                if ok:
                    self.state.set(name, status="approved",
                                   hash=content_hash(cur_user), seen=_now_iso(),
                                   title=(enrichment.get("title") or "").strip(),
                                   tags=enrichment.get("tags") or [])
                    self.state.note_succeeded(name)
                    self.state.bump("approved")
                    self._corpus_dirty = True
                    log.info(f"  ✅ approved & finalized (QS): {name}")
                    try:
                        self._create_calendar_from_db(name, self.memos._db())
                    except Exception as e:
                        log.error(f"  calendar: skipped: {e}")
                    if self.cfg.get("todo_enabled", True):
                        self._sync_todos_from_enrichment(name, enrichment)
                    return True
                self.state.note_failed(name)
                self.state.bump("errors")
                return False

            # Memos legacy path — markdown serialisation
            new_content = build_approved(cur_user, enrichment, self.cfg, cur_user)
            new_content = self._truncate_for_memos(new_content, name)
            ut = self.memos.update_content(name, new_content)
            from .memos_client import MemosClient as _MC
            if ut and ut is not _MC.PATCH_PERMANENT_ERROR:
                seen = self._authoritative_update_time(name, ut)
                self.state.set(name, status="approved",
                               hash=text_hash(cur_user), seen=seen,
                               title=(enrichment.get("title") or "").strip(),
                               tags=enrichment.get("tags") or [])
                self.state.note_succeeded(name)
                self.state.bump("approved")
                self._corpus_dirty = True
                log.info(f"  ✅ approved & finalized: {name}")
                try:
                    self.calendar.create_events_from_enrichment(enrichment, name)
                except Exception as e:
                    log.error(f"  calendar: skipped due to error: {e}")
                if self.cfg.get("todo_enabled", True):
                    self._sync_todos_from_enrichment(name, enrichment)
                return True
            self.pending.add(name, new_content, status="approved",
                             hash=text_hash(cur_user), kind="note")
            self.state.note_failed(name)
            self.state.bump("errors")
            return False

        # ── SUGGEST / REENRICH ────────────────────────────────────────────────
        log.info(f"    running inference ({action}) on {name}…")
        if self._backend == "quicksilver":
            # On-demand Polish request? Read-and-clear the one-shot flag so it
            # fires exactly once (stateless — no lingering flag to leak).
            polish_req = self._consume_polish_request(name)
            if polish_req:
                hints = dict(hints or {}); hints["force_polish"] = True
            elif note.get("meta_only") or note.get("protected") or note.get("user_edited"):
                # Body is authoritative → metadata-only enrichment (the engine
                # keeps 'polished' empty). Three ways to land here: the user
                # chose "Metadata only" for this note (meta_only), the note is
                # protected, or they've hand-edited it. A polish request (above)
                # always overrides.
                hints = dict(hints or {}); hints["protected"] = True
            # Feed the existing tag vocabulary so the model reuses established
            # tags instead of inventing near-duplicates (work vs working etc.).
            try:
                rows = self.memos._db().execute(
                    "SELECT tag, COUNT(*) c FROM tags GROUP BY tag "
                    "ORDER BY c DESC LIMIT 30").fetchall()
                vocab = [r["tag"] for r in rows]
                if vocab:
                    hints = dict(hints or {})
                    hints["vocab_tags"] = vocab
            except Exception:
                pass
        enrichment = self.gemma.enrich(source_text, hints=hints)
        if enrichment is not None and note.get("_body0") is not None and self._changed_during_inference(name, note["_body0"]):
            log.info(f"    ↺ {name} was edited while the model was working — dropping that result, will run again once it settles")
            self._release_force(name, "edited during inference")
            self._note_event(name, "edited during inference")
            return False
        if enrichment is not None:
            # Propagate the on-demand polish request to write_enrichment so it
            # keeps the rewrite even on a protected/user-edited note.
            if hints and hints.get("force_polish"):
                enrichment["_user_requested_polish"] = True
            # Apply the summary_mode preference: 'never' drops summaries; 'long'
            # keeps them only for notes past the word threshold; 'always' keeps.
            try:
                prefs = self._user_prefs()
                mode = prefs.get("summary_mode", "long")
                if mode == "never":
                    enrichment["summary"] = ""
                elif mode == "long":
                    words = len((source_text or "").split())
                    if words < int(prefs.get("summary_min_words", 40)):
                        enrichment["summary"] = ""
            except Exception:
                pass
        if enrichment is None:
            self.state.set(name, status="error")
            self.state.note_failed(name)
            self.state.bump("errors")
            log.warning(f"  ✗ inference failed: {name}")
            # Record the failure in the shared DB so the PWA can list which
            # notes need attention and offer a retry.
            if self._backend == "quicksilver":
                try:
                    self.memos.record_error(name, "inference failed")
                except Exception:
                    pass
            return False
        log.info(f"    inference returned in {enrichment.get('_latency_s', 0):.1f}s")
        self.state.record_latency(enrichment.get("_latency_s", 0.0))

        # Confidence gate → flag low-confidence output for human review.
        conf = enrichment.get("confidence", 1.0)
        uncertain_fixes = [c for c in (enrichment.get("corrections") or [])
                           if c.get("confidence", 1.0) < 0.8]
        if conf < self.cfg.get("confidence_review_threshold", 0.6) or uncertain_fixes:
            enrichment["_needs_review"] = True
            self.state.bump("needs_review")
            why = (f"low confidence {conf:.2f}" if conf < self.cfg.get(
                   "confidence_review_threshold", 0.6)
                   else f"{len(uncertain_fixes)} uncertain correction(s)")
            log.info(f"    ⚠ {why} → flagged in approve box: {name}")

        self._inject_related(name, source_text, enrichment)
        log.info(f"    → title={enrichment.get('title')!r} "
                 f"tags={enrichment.get('tags')} "
                 f"priority={enrichment.get('priority')} conf={conf:.2f}")

        if _cfg_bool(self.cfg.get("dry_run"), False):
            log.info(f"  [DRY RUN] would {'re-' if action=='reenrich' else ''}suggest on {name}")
            self.state.set(name, status="pending", hash=text_hash(source_text))
            self.state.bump("reenriched" if action == "reenrich" else "suggested")
            return True

        if self._backend == "quicksilver":
            # Write enrichment to DB. Status "pending" = awaiting user approval
            # in the PWA. The user changes status via the status bar in the note
            # detail — no markdown checkbox gymnastics needed.
            #
            # AUTO-APPROVE everything. The review step only ever let the user
            # approve — there was nothing else to do there — so it was friction
            # without benefit. Enrichment (including a polished rewrite) now
            # lands as 'approved' directly. The safety net is revision history:
            # every enrichment keeps the prior version, so a bad rewrite is one
            # tap to restore rather than something the user had to pre-approve.
            # (Quick Events and Checklists were already auto-approved; this
            # extends the same treatment to plain notes.)
            #
            # Low-confidence output is still FLAGGED (_needs_review, set above)
            # so the UI can mark it, but it no longer blocks by sitting in
            # 'pending' — the user sees the flag on an already-usable note.
            target_status = "approved"
            ok = self.memos.write_enrichment(name, enrichment, target_status)
            if ok:
                self.state.set(name, status=target_status,
                               hash=content_hash(source_text), seen=_now_iso(),
                               title=(enrichment.get("title") or "").strip(),
                               tags=enrichment.get("tags") or [])
                self.state.note_succeeded(name)
                self.state.bump("reenriched" if action == "reenrich" else "suggested")
                # Everything auto-approves now (revision history is the safety
                # net), so there is no pending branch — the note is usable the
                # moment enrichment lands. But the approve side-effects that used
                # to run when a user tapped Approve (adopt the polished body,
                # create calendar events from reminders) must now run HERE, since
                # the note no longer transitions through a separate approve step.
                try:
                    new_body = self.memos.adopt_polished(name)
                    if new_body is not None:
                        # Re-hash on the adopted body so a later edit is detected
                        # correctly, and tell open PWA clients to refresh.
                        self.state.set(name, hash=content_hash(new_body.strip()))
                        self.memos._notify_webhook(name, {}, "approved")
                        log.info(f"  📝 adopted polished text as new body: {name}")
                except Exception as e:
                    log.error(f"  adopt_polished failed for {name}: {e}")
                try:
                    self._create_calendar_from_db(name, self.memos._db())
                except Exception as e:
                    log.error(f"  calendar creation failed for {name}: {e}")
                self.state.bump("approved")
                # Incremental, not a full discard: this is the hot path now that
                # every note auto-approves (see _corpus_touch).
                self._corpus_touch(name, source_text,
                                   title=(enrichment.get("title") or ""),
                                   tags=(enrichment.get("tags") or []))
                log.info(f"  ✅ auto-approved in QS: {name}")
                return True
            self.state.note_failed(name)
            self.state.bump("errors")
            return False

        # Memos legacy path — build and PATCH markdown content
        new_content = build_suggested(source_text, enrichment, self.cfg, source_text)
        new_content = self._truncate_for_memos(new_content, name)
        from .memos_client import MemosClient as _MC
        ut = self.memos.update_content(name, new_content)
        if ut and ut is not _MC.PATCH_PERMANENT_ERROR:
            seen = self._authoritative_update_time(name, ut)
            self.state.set(name, status="suggested",
                           hash=text_hash(source_text), seen=seen)
            self.state.note_succeeded(name)
            self.state.bump("reenriched" if action == "reenrich" else "suggested")
            log.info(f"  ✓ {'re-' if action=='reenrich' else ''}suggested: {name}")
            return True
        if ut is _MC.PATCH_PERMANENT_ERROR:
            self.state.note_failed(name)
            self.state.bump("errors")
            log.warning(f"  ✗ patch permanently failed for {name} — not retrying")
            return False
        self.pending.add(name, new_content, status="suggested",
                         hash=text_hash(source_text), kind="note")
        self.state.note_failed(name)
        self.state.bump("errors")
        log.warning(f"  ✗ patch failed (saved for retry): {name}")
        return False
