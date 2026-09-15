#!/usr/bin/env python3
"""
Quicksilver agent smoke test.
Run after any code/config change to the agent to verify its core contract.

    python smoke_test.py

Exit code 0 = all passed, 1 = at least one failure.

This tests the agent's DB-facing logic (QuicksilverClient + decision engine)
against a throwaway DB. It does NOT load the LLM — model inference is out of
scope for a smoke test (too slow, hardware-dependent). It focuses on the logic
that most often breaks on a code change: enrichment writes, the user-edit
guards, adoption paths, and the decision state machine.
"""
import sys, os, tempfile, importlib.util, sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

G, R, Y, X = "\033[32m", "\033[31m", "\033[33m", "\033[0m"
_passed, _failed = 0, 0


def check(name, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  {G}✓{X} {name}")
    else:
        _failed += 1
        print(f"  {R}✗ {name}{X}" + (f"  → {detail}" if detail else ""))


def section(title):
    print(f"\n{Y}▸ {title}{X}")


def _make_schema(db_path):
    """Build the real schema via the PWA's init_db (they share this DB in prod).
    Falls back to a minimal schema if the PWA app.py isn't alongside."""
    import importlib.util
    pwa = os.path.abspath(os.path.join(HERE, "..", "hg_ui", "app.py"))
    if os.path.exists(pwa):
        os.environ["QS_DB"] = db_path
        spec = importlib.util.spec_from_file_location("pwa_app", pwa)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["pwa_app"] = mod
        spec.loader.exec_module(mod)
        mod.init_db()
        return
    # Fallback: minimal schema (matches current PWA columns)
    con = sqlite3.connect(db_path)
    con.executescript("""
    CREATE TABLE IF NOT EXISTS notes (
        id TEXT PRIMARY KEY, body TEXT NOT NULL DEFAULT '',
        protected INTEGER NOT NULL DEFAULT 0, pinned INTEGER NOT NULL DEFAULT 0,
        is_daily INTEGER NOT NULL DEFAULT 0, daily_date TEXT,
        embedding TEXT, visibility TEXT DEFAULT 'private',
        created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')));
    CREATE TABLE IF NOT EXISTS enrichments (
        id TEXT PRIMARY KEY, note_id TEXT NOT NULL, title TEXT, summary TEXT,
        polished TEXT, confidence REAL, priority TEXT, schema_version TEXT DEFAULT '2.1',
        user_polish INTEGER NOT NULL DEFAULT 0,
        enriched_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')));
    CREATE TABLE IF NOT EXISTS note_state (
        note_id TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'raw',
        body_hash TEXT, failures INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS tags (note_id TEXT, tag TEXT, PRIMARY KEY(note_id, tag));
    CREATE TABLE IF NOT EXISTS action_items (
        id TEXT PRIMARY KEY, note_id TEXT, text TEXT, done INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')));
    CREATE TABLE IF NOT EXISTS relationships (
        note_id_a TEXT, note_id_b TEXT, score REAL, PRIMARY KEY(note_id_a, note_id_b));
    CREATE TABLE IF NOT EXISTS revisions (
        id TEXT PRIMARY KEY, note_id TEXT, body TEXT, kind TEXT DEFAULT 'pre_adopt'
        CHECK(kind IN ('pre_adopt','edit')),
        created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')));
    CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
    """)
    con.commit()
    con.close()


def main():
    tmp = tempfile.mkdtemp()
    db_path = os.path.join(tmp, "smoke.db")
    _make_schema(db_path)

    from memos_daemon.qs_client import QuicksilverClient
    from memos_daemon.decision import decide

    qc = QuicksilverClient(db_path=db_path, webhook_url="")
    raw = sqlite3.connect(db_path)

    def add_note(body):
        m = qc.create_memo(body)
        return m["name"] if "name" in m else m["id"]

    def get_body(nid):
        return raw.execute("SELECT body FROM notes WHERE id=?", (nid,)).fetchone()[0]

    def get_status(nid):
        r = raw.execute("SELECT status FROM note_state WHERE note_id=?", (nid,)).fetchone()
        return r[0] if r else None

    def get_polished(nid):
        r = raw.execute("SELECT polished FROM enrichments WHERE note_id=? "
                        "ORDER BY enriched_at DESC LIMIT 1", (nid,)).fetchone()
        return r[0] if r else None

    def enrich(nid, polished, tags=None, status="pending", user_polish=False):
        e = {
            "title": "T", "summary": "s", "polished": polished, "confidence": 0.9,
            "priority": "low", "action_items": [], "tags": tags or [], "corrections": []
        }
        if user_polish:
            e["_user_requested_polish"] = True
        qc.write_enrichment(nid, e, status)

    class StateShim:
        dashboard_id = connections_id = todo_id = ""
        def __init__(self, prior): self._p = prior
        def get(self, name): return self._p

    print(f"\n{'='*52}\n  QUICKSILVER AGENT · SMOKE TEST\n{'='*52}")

    # ── 1. client basics ────────────────────────────────────────────────────
    section("Client basics")
    nid = add_note("agent smoke note")
    check("create_memo returns id", bool(nid))
    check("note persisted", get_body(nid) == "agent smoke note")
    m = qc.get_memo(nid)
    check("get_memo returns note", m and m.get("content") == "agent smoke note")
    check("get_memo exposes user_edited", "user_edited" in m)

    # ── 2. enrichment write ─────────────────────────────────────────────────
    section("Enrichment write")
    enrich(nid, "Polished agent note.", tags=["Agent", "AGENT", "test"])
    check("enrichment stored", get_polished(nid) == "Polished agent note.")
    check("status set pending", get_status(nid) == "pending")
    ntags = [r[0] for r in raw.execute("SELECT tag FROM tags WHERE note_id=?", (nid,))]
    check("tags de-duplicated case-insensitively", ntags.count("agent") <= 1, f"tags={ntags}")
    _corr = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "memos_daemon", "correlation.py")).read()
    _emb = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "memos_daemon", "embeddings.py")).read()
    _dm = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "memos_daemon", "daemon.py")).read()
    check("queued calendar deletions are processed, with bounded retries",
          "_process_calendar_deletions" in _dm and "will retry" in _dm
          and "giving up removing calendar event" in _dm)
    _qc = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "memos_daemon", "qs_client.py")).read()
    _pr = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "memos_daemon", "prompt.py")).read()
    # Scope to the JSON braces only — the intro prose also mentions "polished".
    _js = _pr[_pr.find("{", _pr.find("Return EXACTLY")):_pr.find("THINKING")]
    # polished must appear AFTER all metadata fields, so a mid-polished
    # truncation still leaves title/tags/summary intact.
    _en = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "memos_daemon", "engine.py")).read()
    check("polish gets the full output budget (no truncation of the rewrite)",
          "gen_cap = self._max_tokens" in _en and "cap=gen_cap" in _en)
    check("session decode LOOPS (a single run_decode returns a partial response)",
          "while guard <" in _en and "run_decode()" in _en
          and "startswith(parts[-2])" in _en)
    check("unverified session path stays opt-in (proven path is the default)",
          '"use_session_generation": False' in open(os.path.join(
              os.path.dirname(os.path.abspath(__file__)),
              "memos_daemon", "config.py")).read())
    check("full enrichment is the default; per-note meta_only preserves the body",
          "meta_only" in _en and "enrich_polish_by_default" in _en)
    check("metadata-only is the default (body untouched unless Polish asked)",
          "enrich_polish_by_default" in _en and "metadata_only" in _en)
    _co2 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "memos_daemon", "correlation.py")).read()
    check("clusters() is vectorised (no O(n^2) scalar pair loop)",
          "_all_pairs_above" in _co2 and "np.triu" in _co2)
    _cfg2 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "memos_daemon", "config.py")).read()
    check("corpus cap raised now that hot paths are vectorised",
          '"correlation_corpus_size": 2000' in _cfg2)
    _en2 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "memos_daemon", "engine.py")).read()
    _cal = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "memos_daemon", "calendar_client.py")).read()
    check("calendar auth uses loopback flow, not the dead oob endpoint",
          'flow.redirect_uri = "urn:ietf:wg:oauth:2.0:oob"' not in _cal
          and "run_local_server" in _cal)
    check("calendar refresh gives actionable invalid_grant guidance",
          "invalid_grant" in _cal and "In production" in _cal)
    _dm2 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "memos_daemon", "daemon.py")).read()
    check("input-fit uses separate prompt/note token ratios",
          "note_token_room" in _en2 and "note_tokens" in _en2)
    check("relationships use top-K per note, not all intra-cluster pairs",
          "top_n=50, threshold=0.0" not in _dm2 and "related_top_n" in _dm2)
    check("input-fit guard prevents oversized-note hard-fails",
          "_fit_input" in _en and "produces_polish" in _en)
    check("token-overflow retry backstop reacts to the model's actual verdict",
          "_is_token_overflow" in _en and "_hard_trim" in _en)
    check("schema puts polished after metadata (truncation-safe)",
          _js.find('"polished"') > _js.find('"tags"')
          and _js.find('"polished"') > _js.find('"title"')
          and _js.find('"polished"') > _js.find('"action_items"'))
    check("prompt schema version present and current",
          'PROMPT_SCHEMA_VERSION = "2.5"' in _pr)
    _cfg = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "memos_daemon", "config.py")).read()
    # Coherence, not just presence: the token knobs interact, and a mismatch
    # silently degrades output (polishing a trimmed note, or truncated JSON).
    # Compute the worst polished case and assert it fits the context.
    from memos_daemon.config import DEFAULT_CONFIG as _DC
    _ctx = _DC["model_max_num_tokens"]; _out = _DC["enrich_max_tokens"]
    _skw = _DC["enrich_skip_polish_words"]
    _need = 2400 + int(_skw * 1.4) + _out      # system prompt + note + output
    from memos_daemon.engine import _cfg_bool as _cb
    check('config booleans tolerate JSON strings ("false" must not read as True)',
          _cb("false") is False and _cb("true") is True and _cb(False) is False)
    check("polish threshold self-clamps to what the context fits",
          "_fits_words" in _en and "clamping" in _en)
    check("calendar deletion retries are BOUNDED and record the failure",
          "MAX_ATTEMPTS" in open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
              "memos_daemon","daemon.py")).read())
    check("reworded reminders don't become a SECOND event for the same slot",
          "_slot(" in open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
              "memos_daemon","daemon.py")).read()
          and "already has an event for" in open(os.path.join(
              os.path.dirname(os.path.abspath(__file__)),
              "memos_daemon","daemon.py")).read())
    _eng_src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "memos_daemon", "engine.py")).read()
    # Assert the RUNTIME prompt, not the source: these strings are built by
    # implicit concatenation across lines, so a source grep silently misses them.
    _cal = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "memos_daemon","calendar_client.py")).read()
    check("OAuth failure trips a circuit breaker (no retry-every-note loop)",
          "_auth_broken" in _cal and "_trip_auth_broken" in _cal
          and "_auth_broken_mtime" in _cal)
    check("headless environment is detected — no impossible browser launch",
          "headless = not" in _cal
          and 'os.environ.get("DISPLAY")' in _cal
          and "not headless" in _cal)
    check("broken calendar auth keeps reminders pending with one clear line",
          "calendar unavailable (auth)" in _cal and "kept" in _cal)
    _pr = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "memos_daemon","prompt.py")).read()
    _qc = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "memos_daemon","qs_client.py")).read()
    check("over-fitting tag rule removed from the authoritative prompt",
          "you MUST reuse an existing tag" not in _pr
          and "WRONG tag is worse than a new one" in _pr)
    check("action items are categorized (work/personal) with dependency",
          '"category": "work|personal"' in _pr and '"blocked_by"' in _pr)
    check("action-item writer accepts BOTH string and object forms",
          "isinstance(item, dict)" in _qc and 'item.get("task")' in _qc
          and "category, blocked_by" in _qc)
    check("context-aware corrections: tense, colloquial, units",
          "TENSE agreement" in _pr and "this morning" in _pr and '"10%"' in _pr)
    check("priority is HIGH for financial/penalty deadlines",
          "financial or penalty consequence" in _pr)
    _qc2 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "memos_daemon","qs_client.py")).read()
    check("reminders dedup by SAME DATE + semantic title similarity (not date alone)",
          "_is_dupe" in _qc2 and "reminder_dupe_similarity" in _qc2
          and "from .embeddings import cosine" in _qc2)
    check("reminder dedup requires both date and similarity (distinct same-day tasks survive)",
          "different day — not a duplicate" in _qc2
          and "no date → never auto-merge" in _qc2)
    _cc = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "memos_daemon","calendar_client.py")).read()
    check("resolver parses ISO datetimes so resolved hints round-trip",
          "Already an ISO 8601" in _cc and "m_iso" in _cc)
    check("date resolver handles deadlines and period-ends",
          "before|by|due" in _cal and "end|start|beginning" in _cal
          and "_MONTH_MAP" in _cal)
    check("tag vocabulary is offered without forcing a bad fit",
          "WRONG tag is worse than " in _eng_src
          and "reach for a frequent tag because it is frequent" in _eng_src
          and "a personal " in _eng_src
          and "plausibly " not in _eng_src)
    _dm3 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "memos_daemon","daemon.py")).read()
    _dec = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "memos_daemon","decision.py")).read()
    check("agent skips 'dirty' notes (edited but not committed)",
          'if qs_status == "dirty":' in _dec and "return \"skip\"" in _dec)
    check("safety sweep promotes long-stranded dirty notes",
          "_promote_stranded_dirty" in _dm3 and "enrich_stranded_seconds" in _dm3)
    check("re-enrichment never creates DUPLICATE reminders/events",
          "_is_dupe" in _qc and "live_rem" in _qc
          and "same date + similar title" in _qc)
    _dae_src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "memos_daemon", "daemon.py")).read()
    check("liveness stamped every tick; costly telemetry only on its interval",
          "self.memos.heartbeat()" in _dae_src and "_telemetry()" in _dae_src)
    check("corpus updates incrementally on approve (no full rebuild per note)",
          "_corpus_touch" in _dae_src)
    check("input trimming measures token density (logs/code are not prose)",
          "_chars_per_token" in _en and "isalpha() or c.isspace()" in _en)
    check("overflow retry fires on the real native exception, not just clear wording",
          "send_message" in _en and "invalid_argument" in _en)
    check("token config is coherent: largest polished note fits the context",
          _out < _ctx and _need <= _ctx)
    check("skip-polish threshold fits the context (never polish a trimmed note)",
          '"enrich_skip_polish_words": 450' in _cfg
          and '"model_max_num_tokens": 6144' in _cfg)
    check("context sized for speed (moderate, not always-32K) so short notes stay fast",
          '"model_max_num_tokens": 6144' in _cfg
          and '"enrich_max_tokens": 3072' in _cfg)
    _qc = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "memos_daemon", "qs_client.py")).read()
    _co = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "memos_daemon", "correlation.py")).read()
    _cl = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "memos_daemon", "cli.py")).read()
    from memos_daemon.embeddings import encode_embedding, decode_embedding
    _v=[0.1,-0.2]*192
    _blob=encode_embedding(_v)
    check("embedding codec: blob is compact float32 (5x smaller than JSON)",
          isinstance(_blob, bytes) and len(_blob)==len(_v)*4)
    check("embedding codec round-trips and reads legacy JSON",
          all(abs(a-b)<1e-6 for a,b in zip(decode_embedding(_blob),_v))
          and decode_embedding('[0.1,-0.2]')==[0.1,-0.2])
    import memos_daemon.embeddings as _embmod
    _embsrc = open(_embmod.__file__).read()
    _corrsrc = open(os.path.join(os.path.dirname(_embmod.__file__), "correlation.py")).read()
    check("similarity matmuls hardened (float64 + nan_to_num + eps norm floor)",
          "nan_to_num" in _corrsrc and "nan_to_num" in _embsrc
          and "1e-12" in _corrsrc)
    check("dismissed action items are never resurrected by re-enrichment",
          "dismissed_texts" in _qc and "COALESCE(dismissed,0)=0" in _qc)
    check("dismissed reminders never become calendar events",
          "dismissed_rem" in _qc)
    check("per-note extraction switches honoured by the agent",
          "no_actions" in _qc and "no_reminders" in _qc)
    check("embeddings persist on enrichment (feeds corpus + search)",
          "_store_embedding" in _qc and "UPDATE notes SET embedding" in _qc)
    check("corpus reuses stored embeddings instead of recomputing",
          "emb: list | None = None" in _co and 'm.get("embedding")' in
          open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "memos_daemon", "daemon.py")).read())
    check("embed text is shared between store and corpus (must match)",
          "_embed_text" in _qc)
    check("backfill-embeddings command present",
          "cmd_backfill_embeddings" in _cl)
    check("agent honours the bulk-import enrich opt-out",
          'if note.get("enrich_optout"):' in _dm and '"enrich_optout"' in _qc)
    check("opt-out skip advances the watermark (no re-enqueue loop)",
          _dm.index('if note.get("enrich_optout"):') < _dm.index("action = decide(note"))
    check("similarity is vectorised (batch path present)",
          "cosine_batch" in _emb and "_sim_all" in _corr)
    check("numpy is optional with a scalar fallback",
          "HAVE_NUMPY" in _emb and "except Exception:" in _emb)
    check("embedding matrix cache invalidates on add()",
          "_version += 1" in _corr and "_mat_key" in _corr)

    # Fast-path detection: short/list notes should request empty polished.
    _eng = open(os.path.join(HERE, "memos_daemon", "engine.py")).read()
    check("fast-path heuristic present in engine",
          "quick =" in _eng and "list_like" in _eng)
    # prompt-leak stripping guard present
    check("prompt-leak stripper present in engine",
          "context hints" in _eng.lower() and "_LEAK" in _eng)
    # Event/date notes bypass the word-count threshold (so calendar events
    # always process even when the note is short).
    _dae = open(os.path.join(HERE, "memos_daemon", "daemon.py")).read()
    check("event notes bypass word threshold",
          "looks_like_event" in _dae and "📅" in _dae)
    # Calendar events are created from DB reminders (source of truth), not just
    # the model's enrichment dict — so seeded user reminders are never dropped.
    check("calendar created from DB reminders",
          "_create_calendar_from_db" in _dae)
    # Quick events create their calendar entry IMMEDIATELY (user-seeded reminder),
    # without waiting for enrich+approval; agent reminders still wait for approval.
    check("user reminders create calendar event immediately",
          "user_only" in _dae and "COALESCE(source,'agent')='user'" in _dae)
    # Failed calendar creation (e.g. expired token) must NOT mark reminders as
    # created — else the event is lost forever. Only mark on confirmed success.
    check("calendar only marks created on confirmed success",
          "n_created" in _dae and "n_created > 0" in _dae)
    _cal = open(os.path.join(HERE, "memos_daemon", "calendar_client.py")).read()
    check("calendar method returns created count", "return len(created)" in _cal)
    # Event notes auto-approve; every other type keeps the review flow.
    check("auto-approve runs adoption + calendar side-effects (not stranded)",
          "adopt_polished" in _dae and "_create_calendar_from_db" in _dae
          and "auto-approved in QS" in _dae)
    check("auto-approve everything (revision history is the safety net)",
          'target_status = "approved"' in _dae and "AUTO-APPROVE everything" in _dae)
    from memos_daemon.anchor import content_hash as _ch
    check("checkbox toggle does not change content_hash",
          _ch("- [ ] Milk\n- [ ] Eggs") == _ch("- [x] Milk\n- [ ] Eggs"))
    check("adding a checklist item DOES change content_hash",
          _ch("- [ ] Milk") != _ch("- [ ] Milk\n- [ ] Eggs"))
    # Large notes skip the polished rewrite and truncated JSON is salvaged, so a
    # long note never fails enrichment outright.
    check("large-note polish-skip + truncation salvage present",
          "enrich_skip_polish_words" in _eng and "salvage" in _eng.lower() and "truncated" in _eng.lower())
    check("long-note polish-skip is configurable (enable switch + threshold)",
          "enrich_skip_polish_enabled" in _eng and "enrich_long_summary_sentences" in _eng)
    # Calendar file paths auto-derive from the DB directory when left blank.
    import tempfile as _tf, json as _js
    from memos_daemon.config import load_config as _lc
    _f = _tf.NamedTemporaryFile("w", suffix=".json", delete=False)
    _js.dump({"db_path": "/home/adabalap/.quicksilver/notes.db",
              "calendar_client_secret_file": "", "calendar_credentials_file": ""}, _f); _f.close()
    _c = _lc(_f.name)
    check("calendar secret path auto-derived from db dir",
          _c["calendar_client_secret_file"] == "/home/adabalap/.quicksilver/gcal_client_secret.json")
    check("calendar token path auto-derived from db dir",
          _c["calendar_credentials_file"] == "/home/adabalap/.quicksilver/gcal_token.json")
    _f2 = _tf.NamedTemporaryFile("w", suffix=".json", delete=False)
    _js.dump({"db_path": "/x/notes.db", "calendar_client_secret_file": "/explicit/s.json"}, _f2); _f2.close()
    check("explicit calendar path always wins over derived",
          _lc(_f2.name)["calendar_client_secret_file"] == "/explicit/s.json")
    # Prompt-instruction leakage is stripped from action_items too.
    check("action_items leak stripper present",
          "set\\s+[\"']?polished" in _eng or "empty string" in _eng)
    # Content-hash ignores highlight markers so highlight/erase never re-enriches.
    from memos_daemon.anchor import content_hash, strip_highlights
    check("highlight markers stripped from content", strip_highlights("a =={y}b== c") == "a b c")
    check("content_hash ignores highlights",
          content_hash("The quick fox") == content_hash("The =={y}quick== fox"))
    check("content_hash respects real edits",
          content_hash("The quick fox") != content_hash("The slow fox"))
    from memos_daemon.decision import decide
    class _FS:
        def __init__(s, p): s._p=p; s.dashboard_id=s.connections_id=s.todo_id=None
        def get(s, n): return s._p
    _prior = {"status":"approved", "hash": content_hash("hello world text")}
    _hl = decide({"name":"x","content":"hello =={g}world== text","status":"approved"}, _FS(_prior), {})
    check("erase/add highlight on approved note → skip (no re-enrichment)", _hl == "skip", f"got {_hl}")
    _edit = decide({"name":"x","content":"hello world CHANGED","status":"approved"}, _FS(_prior), {})
    check("real content edit still re-enriches (no regression)", _edit == "reenrich", f"got {_edit}")

    # ── 3. adoption path (agent-side) ───────────────────────────────────────
    section("Agent adoption path")
    adopted = qc.adopt_polished(nid)
    check("adopt_polished returns text for fresh note", adopted == "Polished agent note.")
    check("body replaced with polish", get_body(nid) == "Polished agent note.")

    # ── 4. user-edit guard (the critical one) ───────────────────────────────
    section("User-edit guard (data-loss protection)")
    # simulate a user edit: write an 'edit' revision + change the body
    raw.execute("INSERT INTO revisions (id, note_id, body, kind) VALUES ('r1',?,?,'edit')",
                (nid, "Polished agent note."))
    raw.execute("UPDATE notes SET body=? WHERE id=?",
                ("Polished agent note. USER ADDED LINE.", nid))
    raw.commit()
    m = qc.get_memo(nid)
    check("get_memo sees user_edited=True", m.get("user_edited") is True)
    # agent enriches — must NULL the polished for a user-edited note
    enrich(nid, "AI rewrite that would drop the user line.")
    check("write_enrichment nulls polished for edited note", get_polished(nid) is None)
    # even if a polish existed, adopt_polished must refuse
    raw.execute("UPDATE enrichments SET polished=? WHERE note_id=?",
                ("Sneaky AI rewrite.", nid))
    raw.commit()
    result = qc.adopt_polished(nid)
    check("adopt_polished refuses to overwrite user edit", result is None,
          f"got {result!r}")
    check("user line intact", "USER ADDED LINE." in get_body(nid))

    # ── 5. on-demand polish (stateless request marker) ──────────────────────
    section("On-demand polish override")
    enrich(nid, "Explicitly requested rewrite.", user_polish=True)
    check("polish request allows polished to be stored", get_polished(nid) == "Explicitly requested rewrite.")
    result = qc.adopt_polished(nid)
    check("polish request allows adoption", result == "Explicitly requested rewrite.")
    # And the read-and-clear one-shot flag helper works.
    raw.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, '1')",
                (f"polish_once:{nid}",))
    raw.commit()
    from memos_daemon.daemon import MemosDaemon
    class _PM:
        def __init__(s, p): s.p = p
        def _db(s):
            import sqlite3 as _sq; c = _sq.connect(s.p); c.row_factory = _sq.Row; return c
    class _PH:
        _consume_polish_request = MemosDaemon._consume_polish_request
        def __init__(s, p): s.memos = _PM(p)
    _h = _PH(db_path)
    check("polish flag is one-shot (read-and-cleared)",
          _h._consume_polish_request(nid) is True and _h._consume_polish_request(nid) is False)

    # ── 6. protected notes ──────────────────────────────────────────────────
    section("Protected notes")
    pn = add_note("protected verbatim text")
    raw.execute("UPDATE notes SET protected=1 WHERE id=?", (pn,)); raw.commit()
    enrich(pn, "AI would rewrite this.")
    check("protected note: polished nulled", get_polished(pn) is None)
    check("protected note: adopt refuses", qc.adopt_polished(pn) is None)
    check("protected body untouched", get_body(pn) == "protected verbatim text")

    # ── 7. decision engine ──────────────────────────────────────────────────
    section("Decision engine")
    fresh = add_note("decide me")
    d = decide(qc.get_memo(fresh), StateShim({"status": None, "hash": ""}), {})
    check("raw note → suggest/reenrich", d in ("suggest", "reenrich"), f"got {d}")

    enrich(fresh, "polished decide", status="approved")
    # prior state was 'pending' (not yet approved) → first transition to approved
    d = decide(qc.get_memo(fresh), StateShim({"status": "pending", "hash": "old"}), {})
    check("first approval → approve_qs (no loop)", d == "approve_qs", f"got {d}")

    # force status → reenrich
    raw.execute("UPDATE note_state SET status='force' WHERE note_id=?", (fresh,)); raw.commit()
    d = decide(qc.get_memo(fresh), StateShim({"status": "approved", "hash": "old"}), {})
    check("force status → reenrich", d == "reenrich", f"got {d}")

    # ── 8. relationships (replace-all, deduped) ─────────────────────────────
    section("Relationships")
    a = add_note("rel a"); b = add_note("rel b"); cc = add_note("rel c")
    qc.write_relationships([(a, b, 0.8), (a, cc, 0.7)])
    cnt = raw.execute("SELECT COUNT(*) FROM relationships WHERE note_id_a=? OR note_id_b=?",
                      (a, a)).fetchone()[0]
    check("relationships written (bidirectional)", cnt >= 2, f"count={cnt}")
    # replace-all: rewriting shouldn't duplicate
    qc.write_relationships([(a, b, 0.9)])
    dup = raw.execute("SELECT COUNT(*) FROM relationships WHERE note_id_a=? AND note_id_b=?",
                      (a, b)).fetchone()[0]
    check("relationships de-duplicated on rewrite", dup == 1, f"count={dup}")

    # ── 9. error tracking ───────────────────────────────────────────────────
    section("Error tracking")
    en = add_note("will fail")
    qc.record_error(en, "smoke failure")
    errs = raw.execute("SELECT value FROM settings WHERE key='agent_errors'").fetchone()
    check("error recorded", errs is not None and "smoke failure" in errs[0])
    enrich(en, "recovered")
    errs2 = raw.execute("SELECT value FROM settings WHERE key='agent_errors'").fetchone()
    check("error cleared on successful enrichment",
          errs2 is None or en not in (errs2[0] or ""))

    # ── summary ─────────────────────────────────────────────────────────────
    print(f"\n{'='*52}")
    total = _passed + _failed
    colour = G if _failed == 0 else R
    print(f"  {colour}{_passed}/{total} passed{X}"
          + (f"   {R}{_failed} FAILED{X}" if _failed else "   all green ✨"))
    print(f"{'='*52}\n")

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
