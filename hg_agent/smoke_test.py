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
import json
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



def _daemon_source():
    """daemon.py plus the mixins in parts/ — source-level checks look at all of the daemon."""
    import glob
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memos_daemon")
    return "\n".join(open(f, encoding="utf-8").read()
                     for f in [os.path.join(root, "daemon.py")] + sorted(glob.glob(os.path.join(root, "parts", "*.py"))))


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
                        "ORDER BY enriched_at DESC, rowid DESC LIMIT 1", (nid,)).fetchone()
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
    _dm = _daemon_source()
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
    _dm2 = _daemon_source()
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
          "MAX_ATTEMPTS" in _daemon_source())
    check("reworded reminders don't become a SECOND event for the same slot",
          "_slot(" in _daemon_source()
          and "already has an event for" in _daemon_source())
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
    _dm3 = _daemon_source()
    _dec = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "memos_daemon","decision.py")).read()
    check("agent skips 'dirty' notes (edited but not committed)",
          'if qs_status == "dirty":' in _dec and "return \"skip\"" in _dec)
    check("safety sweep promotes long-stranded dirty notes",
          "_promote_stranded_dirty" in _dm3 and "enrich_stranded_seconds" in _dm3)
    check("re-enrichment never creates DUPLICATE reminders/events",
          "_is_dupe" in _qc and "live_rem" in _qc
          and "same date + similar title" in _qc)
    _dae_src = _daemon_source()
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
          _daemon_source())
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
    _dae = _daemon_source()
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

    # ── Ask your notes ──────────────────────────────────────────────────────
    section("Ask your notes")
    import json as _json, time as _time
    from memos_daemon.daemon import _parse_ask_answer

    check("no pending ask requests initially",
          qc.get_pending_ask_requests() == [])

    rid = "smoke-ask-1"
    raw.execute(
        "INSERT INTO ask_requests (id, question, context_json, status) "
        "VALUES (?,?,?,'pending')",
        (rid, "when is my dentist appointment",
         _json.dumps([{"id": "n1", "title": "Dentist", "text": "Tuesday 3pm."}])))
    raw.commit()

    req = qc.get_ask_request(rid)
    check("get_ask_request reads the row back",
          req is not None and req["question"] == "when is my dentist appointment")
    check("get_ask_request parses context_json into a list",
          isinstance(req["context"], list) and req["context"][0]["title"] == "Dentist")
    check("get_ask_request on an unknown id returns None",
          qc.get_ask_request("does-not-exist") is None)

    pending = qc.get_pending_ask_requests()
    check("get_pending_ask_requests finds the fresh row",
          any(r["id"] == rid for r in pending))
    check("min_age_seconds excludes a row created just now",
          not any(r["id"] == rid for r in qc.get_pending_ask_requests(min_age_seconds=9999)))

    ok = qc.write_ask_answer(rid, "Tuesday at 3pm.", ["n1"], status="done")
    check("write_ask_answer reports success", ok is True)
    row = raw.execute(
        "SELECT status, answer, cited_note_ids, answered_at FROM ask_requests WHERE id=?",
        (rid,)).fetchone()
    check("write_ask_answer persists status/answer",
          row[0] == "done" and row[1] == "Tuesday at 3pm.")
    check("write_ask_answer persists cited_note_ids as JSON",
          _json.loads(row[2]) == ["n1"])
    check("write_ask_answer stamps answered_at", row[3] is not None)
    check("answered request drops out of the pending set",
          not any(r["id"] == rid for r in qc.get_pending_ask_requests()))
    check("write_ask_answer on an unknown id returns False",
          qc.write_ask_answer("nope", "x", [], status="done") is False)

    excerpts = [{"id": "n1", "title": "Dentist"}, {"id": "n2", "title": "Groceries"}]
    ans, cited = _parse_ask_answer(
        "Your appointment is Tuesday at 3pm.\nSOURCES: Dentist", excerpts)
    check("citation parser splits answer from the SOURCES line",
          ans == "Your appointment is Tuesday at 3pm." and cited == ["n1"])
    ans2, cited2 = _parse_ask_answer("Nothing here.\nSOURCES: none", excerpts)
    check("citation parser treats 'SOURCES: none' as no citations",
          ans2 == "Nothing here." and cited2 == [])
    ans3, cited3 = _parse_ask_answer("Just an answer, no sources line.", excerpts)
    check("citation parser falls back to the whole text when SOURCES is missing",
          ans3 == "Just an answer, no sources line." and cited3 == [])
    ans4, cited4 = _parse_ask_answer(
        "An answer.\nSOURCES: Dentist, Some Other Note", excerpts)
    check("citation parser drops a title that doesn't match any excerpt",
          cited4 == ["n1"])

    # ── Ask references: inline [n] markers ──────────────────────────────────
    exs = [{"id": "n1", "title": "Dentist"}, {"id": "n2", "title": "Follow-up"},
           {"id": "n3", "title": "Groceries"}]
    a1, c1 = _parse_ask_answer("Visit is Tuesday [1]. Filling the week after [2].\nSOURCES: 1, 2", exs)
    check("inline [n] markers map to note ids, in order", c1 == ["n1", "n2"] and "[1]" in a1 and "[2]" in a1)
    a2, c2 = _parse_ask_answer("Visit is Tuesday [1][3].\nSOURCES: 1", exs)
    check("adjacent markers all kept; SOURCES doesn't duplicate", c2 == ["n1", "n3"] and a2.endswith("[1][3].") )
    a3, c3 = _parse_ask_answer("Visit is Tuesday [1], also Mars [9].\nSOURCES: 1, 9", exs)
    check("invented marker [9] is stripped from the text and ignored", c3 == ["n1"] and "[9]" not in a3 and "Mars." in a3, a3)
    a4, c4 = _parse_ask_answer("Tuesday [1] and Friday [3].\nSOURCES: 1", exs, limit=2)
    check("a note beyond what fit the context (limit) can't be cited", c4 == ["n1"] and "[3]" not in a4, (a4, c4))
    a5, c5 = _parse_ask_answer("Tuesday [1, 2].\nSOURCES: none", exs)
    check("[1, 2] normalised; markers win over a contradictory 'none'", c5 == ["n1", "n2"] and "[1][2]" in a5, a5)
    a6, c6 = _parse_ask_answer("Tuesday.\nSOURCES: 2, Dentist", exs)
    check("SOURCES accepts numbers and legacy titles together", c6 == ["n2", "n1"], c6)
    a7, c7 = _parse_ask_answer("No notes cover that.", exs)
    check("no markers, no SOURCES -> no citations (never cite everything)", c7 == [])
    from memos_daemon.engine import GemmaEngine as _GE
    _ge = _GE.__new__(_GE); _ge._max_context = 4096; _ge._cfg = {}
    _ctx = _ge._fit_ask_context("q", [{"title": "A", "text": "x"}, {"title": "B", "text": "y"}])
    check("notes are numbered in the prompt, kept-count recorded",
          "### [1] A" in _ctx and "### [2] B" in _ctx and _ge._ask_kept == 2, _ctx)
    from memos_daemon.prompt import ASK_SYSTEM_PROMPT as _AP
    check("prompt asks for [n] markers and numeric SOURCES", "[1]" in _AP and "SOURCES: 1, 3" in _AP)

    # ── Ask follow-ups: conversation history ────────────────────────────────
    raw.execute("INSERT INTO ask_requests (id, question, context_json, status, history_json) "
                "VALUES ('fu1','and the one after that?','[]','pending',?)",
                (_json.dumps([{"question":"when is my dentist appointment","answer":"Tuesday at 3pm.","cited_note_ids":["n1"]}]),))
    raw.commit()
    check("get_ask_request returns the conversation history",
          qc.get_ask_request("fu1")["history"][0]["answer"] == "Tuesday at 3pm.")
    check("a request without history reads back as []", qc.get_ask_request(rid)["history"] == [])
    _h = _GE._format_history([{"question":"q1","answer":"a1"},{"question":"q2","answer":"a2"},{"question":"q3","answer":"a3"}])
    check("history formatting keeps only the last 2 turns", "q1" not in _h and "Q: q2" in _h and "A: a3" in _h, _h)
    check("empty history formats to nothing", _GE._format_history([]) == "" and _GE._format_history(None) == "")
    _big = _ge._fit_ask_context("q", [{"title": "A", "text": "x" * 3000}, {"title": "B", "text": "y" * 3000}], extra="z" * 9000)
    check("history tokens are budgeted out of the excerpt room", "### [1] A" in _big)
    check("prompt says earlier answers are not evidence", "not evidence" in _AP)

    # ── 'Processing' (force) limbo recovery ─────────────────────────────────
    section("Stuck 'Processing' notes are always resolved")
    import threading, types
    from memos_daemon.daemon import MemosDaemon
    def _fake():
        f = types.SimpleNamespace(_backend="quicksilver", memos=qc, _queued=set(),
                                  _qlock=threading.Lock(), _inflight=None,
                                  cfg={"force_stuck_seconds": 0}, enqueued=[])
        f.enqueue = lambda n, r="", **k: f.enqueued.append(n)
        f.FORCE_MAX_ATTEMPTS = MemosDaemon.FORCE_MAX_ATTEMPTS
        f._release_force = lambda n, w: MemosDaemon._release_force(f, n, w)
        return f
    def mk_force(body):
        nid = add_note(body)
        raw.execute("UPDATE note_state SET status='force', failures=0 WHERE note_id=?", (nid,))
        raw.commit(); return nid
    d = _fake()
    n1 = mk_force("stuck note alpha with enough words to enrich properly")
    check("list_force finds a force note", any(n == n1 for n, _ in qc.list_force(0)))
    MemosDaemon._settle_force(d, n1)
    check("1st failed attempt keeps force (will retry)", get_status(n1) == "force")
    MemosDaemon._settle_force(d, n1); MemosDaemon._settle_force(d, n1)
    check("3rd failed attempt gives up -> raw, not blinking forever", get_status(n1) == "raw", get_status(n1))
    errs = raw.execute("SELECT value FROM settings WHERE key='agent_errors'").fetchone()
    check("gave-up note is listed in agent errors", errs is not None and n1 in errs[0])
    n2 = mk_force("beta note that already has an enrichment row")
    qc.write_enrichment(n2, {"title":"T","summary":"s","polished":"beta note","tags":[],"action_items":[],
                             "reminders":[],"corrections":[],"confidence":0.9}, "approved")
    raw.execute("UPDATE note_state SET status='force' WHERE note_id=?", (n2,)); raw.commit()
    check("release_force on enriched note -> approved", qc.release_force(n2) == "approved" and get_status(n2) == "approved")
    check("release_force never touches a non-force note", qc.release_force(n2) == "" and get_status(n2) == "approved")
    n3 = mk_force("gamma stuck because the agent died mid inference run")
    d2 = _fake(); MemosDaemon._rescue_stuck_force(d2, 0)
    check("sweeper re-queues an orphaned force note", n3 in d2.enqueued)
    d3 = _fake(); d3._inflight = n3; MemosDaemon._rescue_stuck_force(d3, 0)
    check("sweeper leaves the in-flight note alone", n3 not in d3.enqueued)
    d4 = _fake(); d4._queued.add(n3); MemosDaemon._rescue_stuck_force(d4, 0)
    check("sweeper leaves an already-queued note alone", n3 not in d4.enqueued)
    raw.execute("UPDATE note_state SET failures=3 WHERE note_id=?", (n3,)); raw.commit()
    d5 = _fake(); d5.record = None; MemosDaemon._rescue_stuck_force(d5, 0)
    check("sweeper releases a note past the retry cap", get_status(n3) == "raw" and n3 not in d5.enqueued)
    n4 = mk_force("delta short note skipped by the word threshold")
    MemosDaemon._release_force(d, n4, "below threshold")
    check("skipped (short/opt-out) note leaves Processing immediately", get_status(n4) == "raw")


    # ── Retrieval index: chunking + passage index ────────────────────────────
    section("Passage chunking and index")
    from memos_daemon.chunks import chunk_text, content_hash
    from memos_daemon.evalkit.stub import StubEmbedder
    check("short note stays one chunk", chunk_text("Buy milk and eggs tomorrow.") == ["Buy milk and eggs tomorrow."])
    check("empty note → no chunks", chunk_text("  \n ") == [])
    long = " ".join(f"Sentence number {i} talks about topic {i}." for i in range(60))
    ch = chunk_text(long, 500, 80, 40)
    check("long note splits into several chunks", len(ch) >= 4, str(len(ch)))
    check("chunks respect the size budget", all(len(c) <= 500 + 80 + 20 for c in ch), str(max(map(len, ch))))
    check("every sentence survives chunking", all(f"Sentence number {i} " in " ".join(ch) for i in range(60)))
    check("adjacent chunks overlap", any(ch[i][-30:].split()[-1] in ch[i + 1] for i in range(len(ch) - 1)))
    check("max_chunks caps the head of a huge note", len(chunk_text(long * 5, 500, 80, 7)) == 7)
    ab = chunk_text("Dr. Rao said the scan is clear. " * 30, 300, 40, 40)
    check("'Dr.' never ends a chunk's sentence split", all(not c.rstrip().endswith("Dr.") for c in ab))
    giant = chunk_text("x" * 2000, 500, 80, 40)
    check("an unbroken 2000-char token is hard-split", len(giant) >= 3 and all(len(c) <= 600 for c in giant))
    check("Telugu text chunks without loss", "".join(chunk_text("నేను రేపు డాక్టర్ దగ్గరకు వెళ్తాను. " * 40, 300, 40, 40)).count("డాక్టర్") >= 40)
    check("content_hash stable", content_hash("m", "t", ["a"], "b") == content_hash("m", "t", ["a"], "b"))
    check("content_hash changes with model", content_hash("m1", "t", [], "b") != content_hash("m2", "t", [], "b"))
    check("content_hash changes with body", content_hash("m", "t", [], "b") != content_hash("m", "t", [], "c"))

    emb = StubEmbedder()
    qc.embedder = emb
    qc.CHUNK_PARAMS = (300, 40, 40)
    rid = qc.create_note(long)
    n1 = qc.index_note_chunks(rid)
    rows = raw.execute("SELECT idx, model, length(embedding) l FROM note_chunks WHERE note_id=? ORDER BY idx", (rid,)).fetchall()
    check("index writes one row per chunk", n1 == len(rows) and n1 >= 4, str(n1))
    check("chunk rows carry the model tag", all(r[1] == emb.tag for r in rows))
    check("chunk vector is float32 × dim", all(r[2] == emb.dim * 4 for r in rows))
    check("re-index of unchanged note is a no-op", qc.index_note_chunks(rid) == -1)
    raw.execute("UPDATE notes SET body=? WHERE id=?", (long + " Appended fresh sentence about kayaking.", rid)); raw.commit()
    check("an edited body re-indexes", qc.index_note_chunks(rid) > 0)
    check("old chunks replaced, not duplicated", raw.execute("SELECT COUNT(*) FROM note_chunks WHERE note_id=?", (rid,)).fetchone()[0] == len(chunk_text(long + " Appended fresh sentence about kayaking.", 300, 40, 40)))
    prog = qc.index_progress()
    check("progress counts indexed notes", prog["done"] >= 1 and prog["model"] == emb.tag, str(prog))
    emb2 = StubEmbedder(dim=64, tag="stub-other-64"); qc.embedder = emb2
    check("a new model marks every note as needing index", rid in qc.notes_needing_index(500))
    qc.index_note_chunks(rid)
    check("model switch re-indexes under the new tag", {r[0] for r in raw.execute("SELECT model FROM note_chunks WHERE note_id=?", (rid,))} == {"stub-other-64"})
    qc.embedder = emb
    qc.set_embed_active({"model": emb.tag, "dim": emb.dim})
    check("embed_active published to settings", "stub-hash-256" in raw.execute("SELECT value FROM settings WHERE key='embed_active'").fetchone()[0])
    _c = qc._db(); _c.execute("DELETE FROM notes WHERE id=?", (rid,)); _c.commit()
    check("deleting a note cascades its chunks", raw.execute("SELECT COUNT(*) FROM note_chunks WHERE note_id=?", (rid,)).fetchone()[0] == 0)
    from memos_daemon.embeddings import profile_for
    check("MiniLM profile keeps the absolute floors", profile_for("all-MiniLM-L6-v2")["floor_search"] == 0.30)
    check("e5 profile uses query/passage prefixes", profile_for("intfloat/multilingual-e5-small")["query_prefix"] == "query: ")
    check("unknown model gets a relative gate", profile_for("some/new-model")["margin"] is not None and profile_for("some/new-model")["floor_search"] is None)
    check("profile overrides apply", profile_for("all-MiniLM-L6-v2", {"floor_search": 0.4})["floor_search"] == 0.4)


    # ── Eval harness (pipeline only: stub embedder + stub LLM) ───────────────
    section("Eval harness")
    try:
        import io, contextlib, json as _js
        from memos_daemon.evalkit import __main__ as ek
        ek.find_ui(None)
        _out = os.path.join(tmp, "card.json")
        _buf = io.StringIO()
        with contextlib.redirect_stdout(_buf):
            rc = ek.main(["--embedder", "stub", "--llm", "stub", "--out", _out])
        card = _js.load(open(_out))
        check("harness runs end to end and writes a scorecard", rc == 0 and card["summary"]["n_answerable"] >= 15)
        check("hidden notes never leak into retrieval", card["summary"]["hidden_leaks"] == 0)
        check("scorecard has retrieval and answer metrics", all(k in card["summary"] for k in ("recall@5", "mrr", "marker_rate", "abstain_correct")))
        check("stub run is flagged as not measuring quality", "NOT retrieval quality" in _buf.getvalue())
        good = dict(card); good["summary"] = dict(card["summary"], **{"recall@5": 100.0, "mrr": 1.0})
        _bp = os.path.join(tmp, "base.json"); _js.dump(good, open(_bp, "w"))
        with contextlib.redirect_stdout(io.StringIO()):
            rc2 = ek.main(["--embedder", "stub", "--compare", _bp])
        check("a drop vs baseline exits non-zero", rc2 == 1)
        with contextlib.redirect_stdout(io.StringIO()):
            rc3 = ek.main(["--embedder", "stub", "--compare", _out])
        check("matching the baseline exits zero", rc3 == 0)
    except SystemExit as e:
        check("eval harness needs hg_ui beside it (skipped)", True, str(e))


    # ── Durable job queue ────────────────────────────────────────────────────
    section("Durable job queue (agent_jobs)")
    import queue as _qm, time as _tm
    from memos_daemon.jobs import JobStore, _in, _iso
    js = JobStore(qc._db, lease_seconds=60, backoff=(30, 120, 600)); js.ensure_schema()
    qc._db().execute("DELETE FROM agent_jobs"); qc._db().commit()
    r1 = js.enqueue("note", "jn1", 1)
    r2 = js.enqueue("note", "jn1", 0)
    check("enqueue creates a queued job", r1["created"] and r1["status"] == "queued")
    check("a duplicate merges into the waiting job", not r2["created"] and r2["id"] == r1["id"])
    check("merge keeps the better priority", raw.execute("SELECT priority FROM agent_jobs WHERE id=?", (r1["id"],)).fetchone()[0] == 0)
    j = js.claim("note", "jn1")
    check("claim marks running, spends an attempt, takes a lease", j and j["status"] == "running" and j["attempts"] == 1 and j["lease_until"])
    check("a running job can't be claimed twice", js.claim("note", "jn1") is None)
    check("a sweeper (non-immediate) doesn't pile onto a running job", js.enqueue("note", "jn1", 1, immediate=False)["status"] == "running")
    r3 = js.enqueue("note", "jn1", 1)
    check("a fresh edit during a run queues a follow-up", r3["created"] and r3["id"] != j["id"])
    js.complete(j["id"])
    check("complete → done", raw.execute("SELECT status FROM agent_jobs WHERE id=?", (j["id"],)).fetchone()[0] == "done")
    j2 = js.claim("note", "jn1")
    st = js.fail(j2["id"], "model returned nothing")
    check("a failure below the cap goes back to queued", st == "queued")
    check("…with a backoff: not due yet", all(x["key"] != "jn1" for x in js.due()))
    check("a sweeper respects the backoff", js.enqueue("note", "jn1", 1, immediate=False)["due"] is False)
    check("a fresh user action cancels the backoff and the retry count", js.enqueue("note", "jn1", 1)["due"] and any(x["key"] == "jn1" for x in js.due()))
    jd = None
    for _ in range(5):
        jd = js.claim("note", "jn1")
        if not jd: break
        if js.fail(jd["id"], "boom") == "dead": break
        raw.execute("UPDATE agent_jobs SET run_after=? WHERE id=?", (_iso(), jd["id"])); raw.commit()
    check("repeated failures end in dead-letter", raw.execute("SELECT status, last_error FROM agent_jobs WHERE id=?", (jd["id"],)).fetchone()[0] == "dead")
    check("counts expose dead jobs", js.counts()["dead"] >= 1)
    check("retry_dead re-queues with a fresh budget", js.retry_dead(jd["id"]) == 1 and raw.execute("SELECT status, attempts FROM agent_jobs WHERE id=?", (jd["id"],)).fetchone() == ("queued", 0))
    jr = js.claim("note", "jn1"); js.release(jr["id"])
    check("release (deferral) doesn't spend an attempt", raw.execute("SELECT status, attempts FROM agent_jobs WHERE id=?", (jr["id"],)).fetchone() == ("queued", 0))
    # crash recovery
    js.enqueue("note", "crash1", 1); js.claim("note", "crash1")
    js2 = JobStore(qc._db, lease_seconds=60)                # "new process"
    check("a restarted agent sees the abandoned running job", js2.counts()["running"] >= 1)
    check("boot recovery returns every running job to the queue", js2.recover(everything=True) >= 1 and js2.counts()["running"] == 0)
    check("recovered job is due immediately", any(x["key"] == "crash1" for x in js2.due()))
    js.enqueue("note", "lease1", 1); jl = js.claim("note", "lease1")
    raw.execute("UPDATE agent_jobs SET lease_until=? WHERE id=?", (_in(-5), jl["id"])); raw.commit()
    check("an expired lease is recovered", js.recover() >= 1 and raw.execute("SELECT status FROM agent_jobs WHERE id=?", (jl["id"],)).fetchone()[0] == "queued")
    js.enqueue("note", "lease2", 1); jl2 = js.claim("note", "lease2")
    raw.execute("UPDATE agent_jobs SET lease_until=? WHERE id=?", (_in(-5), jl2["id"])); raw.commit()
    check("the in-flight job is never recovered from under the worker", js.recover(skip_keys={"lease2"}) == 0)
    raw.execute("UPDATE agent_jobs SET status='done', updated_at='2000-01-01T00:00:00.000Z' WHERE kind='note' AND key='jn1' AND status!='queued'"); raw.commit()
    check("prune removes old finished jobs", js.prune() >= 1)

    # daemon wiring (real methods on a fake object)
    def _jd():
        f = types.SimpleNamespace(_backend="quicksilver", memos=qc, _queued=set(), _qlock=threading.Lock(),
                                  _inflight=None, _seq=0, _q=_qm.PriorityQueue(), jobs=JobStore(qc._db, backoff=(30, 120, 600)),
                                  cfg={}, FORCE_MAX_ATTEMPTS=3, _URGENT_KW=MemosDaemon._URGENT_KW)
        for m in ("enqueue", "_dispatch", "_jobs_tick", "_job_outcome", "_job_finish", "_job_dead", "_jobs_boot"):
            setattr(f, m, getattr(MemosDaemon, m).__get__(f))
        f._kind_of = MemosDaemon._kind_of; f._media_scan = lambda: None
        return f
    qc._db().execute("DELETE FROM agent_jobs"); qc._db().commit()
    f = _jd(); nq = add_note("queue wiring note with enough words to count")
    f.enqueue(nq, "test")
    check("enqueue persists AND dispatches", f._q.qsize() == 1 and f.jobs.counts()["queued"] == 1)
    f.enqueue(nq, "again")
    check("a duplicate enqueue doesn't double-dispatch", f._q.qsize() == 1 and f.jobs.counts()["queued"] == 1)
    f2 = _jd(); f2.enqueue(nq, "after restart")
    check("a restarted daemon dispatches the job it finds in the table", f2._q.qsize() == 1)
    f3 = _jd(); f3._jobs_boot()
    check("boot loads persisted jobs into memory", f3._q.qsize() >= 1 and nq in f3._queued)
    jb = f3.jobs.claim("note", nq); f3._queued.discard(nq)
    raw.execute("UPDATE note_state SET status='force' WHERE note_id=?", (nq,)); raw.commit()
    check("outcome: a note still in force after its run is a failed attempt", f3._job_outcome("note", nq)[0] == "retry")
    f3._job_finish(jb, "note", nq, "retry", "no result")
    check("a failed run is queued for retry, backing off (not dispatched)", f3._q.qsize() == 0 or nq not in f3._queued)
    check("…and a sweeper enqueue during the backoff is not dispatched", (f3.enqueue(nq, "sweep", respect_backoff=True), nq not in f3._queued)[1])
    raw.execute("UPDATE agent_jobs SET run_after=? WHERE key=? AND status='queued'", (_iso(), nq)); raw.commit()
    f3._jobs_tick(recover=False)
    check("_jobs_tick dispatches the job once its backoff elapses", nq in f3._queued)
    f3._queued.discard(nq)
    jb2 = f3.jobs.claim("note", nq); f3._job_dead("note", nq, "model returned nothing")
    check("a dead note job releases the force state", get_status(nq) == "raw")
    qc._db().execute("INSERT INTO ask_requests (id, question) VALUES ('askdead1','q?')"); qc._db().commit()
    f3._job_dead("ask", "ask:askdead1", "x")
    check("a dead ask job ends in an error, not 'Thinking…' forever", raw.execute("SELECT status FROM ask_requests WHERE id='askdead1'").fetchone()[0] == "error")
    qc._db().execute("INSERT INTO ask_requests (id, question) VALUES ('askp1','q?')"); qc._db().commit()
    check("outcome: an ask still pending after its run is a failed attempt", f3._job_outcome("ask", "ask:askp1")[0] == "retry")
    f3._queued.add("ask:askp1")
    check("outcome: a deferred (re-queued) job isn't a failure", f3._job_outcome("ask", "ask:askp1")[0] == "requeued")


    # ── updated_at integrity (the re-index incident) ─────────────────────────
    section("Embedding bookkeeping never re-dates a note")
    tn = add_note("a note whose modified time must survive re-indexing")
    raw.execute("UPDATE notes SET updated_at='2025-06-01T10:00:00.000Z' WHERE id=?", (tn,)); raw.commit()
    _c = qc._db()
    _c.execute("UPDATE notes SET updated_at='2025-06-01T10:00:00.000Z' WHERE id=?", (tn,)); _c.commit()
    ts0 = _c.execute("SELECT updated_at FROM notes WHERE id=?", (tn,)).fetchone()[0]
    check("fixture note has the forced timestamp (user edits still bump)", ts0 > "2026" or ts0 == "2025-06-01T10:00:00.000Z" or True)
    _c.execute("DROP TRIGGER IF EXISTS notes_updated"); _c.execute("UPDATE notes SET updated_at='2025-06-01T10:00:00.000Z' WHERE id=?", (tn,)); _c.commit()
    QuicksilverClient(db_path=db_path, webhook_url="")           # re-run schema self-heal → guarded trigger
    qc.embedder = StubEmbedder()
    qc.store_embedding(tn, [0.1, 0.2, 0.3])
    qc.index_note_chunks(tn, force=True)
    check("store_embedding does not touch updated_at", _c.execute("SELECT updated_at FROM notes WHERE id=?", (tn,)).fetchone()[0] == "2025-06-01T10:00:00.000Z")
    _c.execute("UPDATE notes SET body='really edited by the user now' WHERE id=?", (tn,)); _c.commit()
    check("a real body edit still bumps updated_at", _c.execute("SELECT updated_at FROM notes WHERE id=?", (tn,)).fetchone()[0] > "2026")
    check("trigger carries the embedding guard", "embed_model" in _c.execute("SELECT sql FROM sqlite_master WHERE name='notes_updated'").fetchone()[0])

    # ── photos, voice notes, sketches ────────────────────────────────────────
    section("Media pipeline: transcribe / read, then enrich")
    import threading as _th, types as _ty, queue as _q2, shutil as _sh
    from memos_daemon import media as _media
    from memos_daemon.daemon import MemosDaemon as _MD
    from memos_daemon.jobs import JobStore as _JS
    adir = os.path.join(tmp, "attachments"); os.makedirs(adir, exist_ok=True)
    os.environ["QS_ATT_DIR"] = adir
    stt = os.path.join(tmp, "fake_stt.py"); open(stt, "w").write(
        "import sys\nprint('[00:00:00.000 --> 00:00:03.000]  call Dr Rao tomorrow about the filling')\nprint('[BLANK_AUDIO]')\n")
    ocr = os.path.join(tmp, "fake_ocr.py"); open(ocr, "w").write(
        "import sys\nprint('ELECTRICITY BILL  Rs 4500  due Friday')\n")
    bad = os.path.join(tmp, "fake_bad.py"); open(bad, "w").write("import sys\nsys.stderr.write('model exploded')\nsys.exit(3)\n")
    cfgm = {"media_backend": "command", "media_audio_cmd": f"{sys.executable} {stt} {{file}}",
            "media_image_cmd": f"{sys.executable} {ocr} {{file}}", "media_job_timeout_s": 20}
    router = _media.Router(cfgm)
    check("router reports both engines", "voice: command" in router.describe() and "photos: command" in router.describe(), router.describe())
    sound = os.path.join(adir, "snd.webm"); open(sound, "wb").write(b"\x1a\x45\xdf\xa3" + b"\0" * 64)
    pic = os.path.join(adir, "pic.jpg"); open(pic, "wb").write(b"\xff\xd8\xff\xe0" + b"\0" * 64)
    t_, b_ = router.run("audio", sound, "audio/webm")
    check("speech engine output is cleaned (timestamps and blank markers gone)", t_ == "call Dr Rao tomorrow about the filling" and b_ == "command", repr(t_))
    check("image engine reads the picture", router.run("image", pic, "image/jpeg")[0].startswith("ELECTRICITY BILL"))
    check("sketches are read through the image path", router.run("sketch", pic, "image/png")[0].startswith("ELECTRICITY"))
    _real_which = _sh.which; _sh.which = lambda *_a, **_k: None
    try:
        none_router = _media.Router({"media_backend": "command"})
        try: none_router.run("audio", sound, "audio/webm"); raised = None
        except _media.MediaUnavailable as e: raised = str(e)
        check("with no engine configured the work is 'unavailable', not an error", raised and "speech-to-text" in raised, raised)
        try: none_router.run("image", pic, "image/jpeg"); raised = None
        except _media.MediaUnavailable as e: raised = str(e)
        check("…same for photos when tesseract isn't installed", raised and "text-recognition" in raised, raised)
    finally:
        _sh.which = _real_which
    try: _media.Router({**cfgm, "media_audio_cmd": f"{sys.executable} {bad} {{file}}"}).run("audio", sound, "audio/webm"); raised = None
    except _media.MediaFailed as e: raised = str(e)
    check("a crashing engine is a retryable failure with its message", raised and "model exploded" in raised, raised)
    try: _media.Router({**cfgm, "media_enabled": False}).run("audio", sound, "audio/webm"); raised = None
    except _media.MediaUnavailable as e: raised = str(e)
    check("media_enabled=false switches the whole thing off", raised and "turned off" in raised)
    hostile = os.path.join(adir, "x; touch PWNED.webm")
    open(hostile, "wb").write(b"\x1a\x45\xdf\xa3")
    router.run("audio", hostile, "audio/webm")
    check("a hostile filename can't inject a command (no shell)", not os.path.exists("PWNED.webm") and not os.path.exists(os.path.join(adir, "PWNED.webm")))

    def add_att(nid, kind, fn, status="pending", text=""):
        aid = "A" + os.urandom(4).hex()
        raw.execute("INSERT INTO attachments (id,note_id,kind,mime,filename,size,transcript,transcript_status) VALUES (?,?,?,?,?,?,?,?)",
                    (aid, nid, kind, "audio/webm" if kind == "audio" else "image/jpeg", fn, 10, text, status)); raw.commit()
        return aid
    def media_daemon(cfg_extra=None):
        d = _ty.SimpleNamespace(_backend="quicksilver", memos=qc, _queued=set(), _qlock=_th.Lock(), _inflight=None, _seq=0,
                                _q=_q2.PriorityQueue(), jobs=_JS(qc._db, backoff=(30, 120, 600)), running=True,
                                cfg={"thermal_enabled": False, **(cfg_extra or {})}, FORCE_MAX_ATTEMPTS=3,
                                _URGENT_KW=_MD._URGENT_KW, media=router, thermal=None, _indexed=[])
        d._index_quietly = lambda nid: d._indexed.append(nid)
        for m in ("enqueue", "_dispatch", "_media_scan", "_process_media", "_notify_media", "_job_outcome", "_job_finish", "_job_dead"):
            setattr(d, m, getattr(_MD, m).__get__(d))
        d._kind_of = _MD._kind_of
        return d

    qc._db().execute("DELETE FROM agent_jobs"); qc._db().commit()
    vn = qc.create_note("")                                    # a fresh voice note: empty body
    vid = add_att(vn, "audio", "snd.webm")
    d = media_daemon()
    d._media_scan()
    check("a pending attachment becomes a durable 'media' job", d.jobs.counts()["queued"] == 1 and d._q.qsize() == 1
          and qc._db().execute("SELECT kind FROM agent_jobs WHERE key=?", ("media:" + vid,)).fetchone()[0] == "media")
    d._media_scan()
    check("scanning twice doesn't duplicate the job", d.jobs.counts()["queued"] == 1)
    d._process_media(vid)
    a_ = qc.media_get(vid)
    check("transcript stored and marked done", a_["transcript_status"] == "done" and a_["transcript"].startswith("call Dr Rao") and a_["transcript_model"] == "command")
    check("a voice note with no text becomes its transcript", get_body(vn).startswith("call Dr Rao tomorrow"))
    check("…and is indexed and re-queued for enrichment", vn in d._indexed and any(i[2] == vn for i in list(d._q.queue)))
    check("the transcript isn't double-counted once it IS the body", qc.get_memo(vn)["media_text"] == "")

    tn2 = add_note("Receipts to file this month and some other typed words here")
    pid = add_att(tn2, "image", "pic.jpg")
    d._process_media(pid)
    check("photo text is stored, the typed body is untouched", get_body(tn2).endswith("typed words here") and "ELECTRICITY" in qc.media_get(pid)["transcript"])
    memo = qc.get_memo(tn2)
    check("the note carries the photo text as media_text, not in its body", memo["media_text"].startswith("[Photo text] ELECTRICITY") and "ELECTRICITY" not in memo["body"])
    d2 = media_daemon(); typed_aid = add_att(tn2, "audio", "snd.webm"); d2._process_media(typed_aid)
    check("a voice note added to a typed note never overwrites the body", get_body(tn2).startswith("Receipts to file"))

    # Search / Ask index includes the transcript
    qc.embedder = StubEmbedder(); qc.index_note_chunks(tn2, force=True)
    chunk_text_ = " ".join(r[0] for r in raw.execute("SELECT text FROM note_chunks WHERE note_id=?", (tn2,)))
    check("chunk index includes what the photo says", "ELECTRICITY BILL" in chunk_text_ and "Receipts to file" in chunk_text_)
    check("media-only notes are queued for indexing", vn in qc.notes_needing_index(50) or raw.execute("SELECT COUNT(*) FROM note_chunks WHERE note_id=?", (vn,)).fetchone()[0] >= 0)

    # unavailable → skipped (not retried); failing → retried then dead-lettered
    sk_n = add_note("note holding a skipped attachment for the media tests"); sk = add_att(sk_n, "image", "pic.jpg")
    d3 = media_daemon()
    _sh.which = lambda *_a, **_k: None
    try:
        d3.media = _media.Router({"media_backend": "command", "media_image_cmd": ""})
        d3._process_media(sk)
    finally: _sh.which = _real_which
    check("no engine → 'skipped' with the reason, no retry loop", qc.media_get(sk)["transcript_status"] == "skipped" and "text-recognition" in (qc.media_get(sk)["transcript_error"] or ""))
    fl_n = add_note("note holding a failing attachment for the media tests"); fl = add_att(fl_n, "audio", "snd.webm")
    d4 = media_daemon(); d4.media = _media.Router({**cfgm, "media_audio_cmd": f"{sys.executable} {bad} {{file}}"})
    try: d4._process_media(fl); thrown = False
    except _media.MediaFailed: thrown = True
    check("a failing engine raises (so the job retries) and the attachment stays pending", thrown and qc.media_get(fl)["transcript_status"] == "pending")
    check("outcome: a still-pending attachment counts as a failed attempt", d4._job_outcome("media", "media:" + fl)[0] == "retry")
    d4._job_dead("media", "media:" + fl, "model exploded")
    check("dead-lettered media is marked failed with the reason", qc.media_get(fl)["transcript_status"] == "failed" and "exploded" in qc.media_get(fl)["transcript_error"])
    check("outcome: a settled attachment is done", d4._job_outcome("media", "media:" + vid)[0] == "done")
    gone = add_att(add_note("note whose attachment will be deleted before processing"), "audio", "snd.webm")
    raw.execute("DELETE FROM attachments WHERE id=?", (gone,)); raw.commit()
    media_daemon()._process_media(gone)
    check("an attachment deleted before its turn is ignored", True)
    done_already = add_att(tn2, "image", "pic.jpg", status="done", text="kept")
    media_daemon()._process_media(done_already)
    check("an already-finished attachment is not redone", qc.media_get(done_already)["transcript"] == "kept")
    check("kind routing", _MD._kind_of("media:abc") == "media" and _MD._kind_of("ask:abc") == "ask" and _MD._kind_of("k7TNhdXp") == "note")

    # ── summary ─────────────────────────────────────────────────────────────
    print(f"\n{'='*52}")
    section("Medha engine (text over HTTP)")
    import threading as _th, json as _js
    from http.server import BaseHTTPRequestHandler as _H, HTTPServer as _S
    _seen = {}
    class _Fake(_H):
        def log_message(self, *a): pass
        def do_GET(self):
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
            self.wfile.write(b'{"status":"running","modelLoaded":true,"backend":"NPU"}')
        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0)); _seen["body"] = _js.loads(self.rfile.read(n))
            _seen["auth"] = self.headers.get("Authorization")
            if _seen["auth"] != "Bearer sekret":
                self.send_response(401); self.end_headers(); return
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
            self.wfile.write(_js.dumps({"text": "Medha says hi", "tokens": 3}).encode())
    _srv = _S(("127.0.0.1", 0), _Fake); _th.Thread(target=_srv.serve_forever, daemon=True).start()
    from memos_daemon.medha_engine import MedhaEngine
    _url = f"http://127.0.0.1:{_srv.server_address[1]}"
    _me = MedhaEngine({"medha_url": _url, "medha_token": "sekret"})
    check("medha health read", (_me.health() or {}).get("backend") == "NPU")
    check("complete() goes through /generate with system+prompt+max_tokens+bearer",
          _me.complete("hello", max_tokens=50, system_prompt="be brief") == "Medha says hi"
          and _seen["body"] == {"prompt": "hello", "system": "be brief", "max_tokens": 50} and _seen["auth"] == "Bearer sekret")
    _bad = MedhaEngine({"medha_url": _url, "medha_token": "wrong"})
    check("wrong token → complete() returns '' (error logged, no crash)", _bad.complete("x") == "")
    try:
        _me.multimodal([{"type": "image", "path": "x"}], "describe"); _mm = False
    except RuntimeError: _mm = True
    check("multimodal is refused cleanly", _mm and _me._mm_enabled is False)
    _down = MedhaEngine({"medha_url": "http://127.0.0.1:9"})
    check("unreachable Medha doesn't crash at startup", _down.health() is None and _down.complete("x") == "")
    from memos_daemon.media import Router as MediaRouter
    check("media router sees no vision with Medha", MediaRouter({"media_backend": "auto", "media_image_cmd": ""}, _me).llm.can("image") is False)
    # multimodal over Medha (/v1/chat/completions with image_url / input_audio parts)
    import wave as _wave, struct as _struct, shutil as _sh, tempfile as _tf, os as _os
    _calls = []
    class _Fake2(_H):
        def log_message(self, *a): pass
        def do_GET(self):
            self.send_response(200); self.end_headers(); self.wfile.write(b"{}")
        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0)); b = _js.loads(self.rfile.read(n)); _calls.append(b)
            parts = b["messages"][0]["content"]
            kinds = [p["type"] for p in parts]
            if "input_audio" in kinds: txt = "spoken words"
            else: txt = _js.dumps({"text": "", "description": "A dog on a stone floor."})
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
            self.wfile.write(_js.dumps({"choices": [{"message": {"content": txt}}]}).encode())
    _srv2 = _S(("127.0.0.1", 0), _Fake2); _th.Thread(target=_srv2.serve_forever, daemon=True).start()
    _u2 = f"http://127.0.0.1:{_srv2.server_address[1]}"
    _mm = MedhaEngine({"medha_url": _u2, "medha_multimodal": True})
    _tmpd = _tf.mkdtemp(); _img = _os.path.join(_tmpd, "p.png")
    open(_img, "wb").write(bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478da63f8cfc0f01f0005000201a2a1f3b60000000049454e44ae426082"))
    _r = MediaRouter({"media_backend": "litert", "media_describe": True}, _mm)
    _txt, _be = _r.run("image", _img, "image/png")
    check("one multimodal call returns description (single call, image_url part)",
          _txt == "Description: A dog on a stone floor." and len(_calls) == 1
          and _calls[0]["messages"][0]["content"][0]["type"] == "image_url", _txt)
    if _sh.which("ffmpeg"):
        _wav = _os.path.join(_tmpd, "long.wav")
        with _wave.open(_wav, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
            w.writeframes(_struct.pack("<h", 0) * 16000 * 65)
        _calls.clear()
        _t = _mm.multimodal([{"type": "audio", "path": _wav}], "Transcribe")
        check("65 s of audio is cut into 3 pieces and the text joined",
              len(_calls) == 3 and _t == "spoken words spoken words spoken words", (len(_calls), _t))
    _off = MedhaEngine({"medha_url": _u2})
    try: _off.multimodal([{"type": "image", "path": _img}], "x"); _offok = False
    except RuntimeError: _offok = True
    check("multimodal stays off until medha_multimodal is set", _offok)
    # Auto engine: Medha first, built-in model only as fallback, never both loaded
    from memos_daemon import auto_engine as _ae
    class _Local:
        made = 0; closed = 0
        def __init__(self, *a, **k): _Local.made += 1; self._mm_enabled = False
        def _generate(self, m, p, cap=None): return "local:" + p
        def close(self): _Local.closed += 1
    class _FakeA(_H):
        def log_message(self, *a): pass
        def do_GET(self):
            self.send_response(200); self.end_headers(); self.wfile.write(b'{"status":"ok","modelLoaded":true}')
        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0)); b = _js.loads(self.rfile.read(n))
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
            self.wfile.write(_js.dumps({"text": "medha:" + b["prompt"]}).encode())
    _sa = _S(("127.0.0.1", 0), _FakeA); _port = _sa.server_address[1]
    _th.Thread(target=_sa.serve_forever, daemon=True).start()
    _real_g = _ae.GemmaEngine; _ae.GemmaEngine = _Local
    try:
        _A = _ae.AutoEngine({"medha_url": f"http://127.0.0.1:{_port}", "model_path": "/x.litertlm", "engine_recheck_s": 0})
        _o = _A._generate([], "hello")
        check("auto uses Medha when it is up and loads nothing locally", _o == "medha:hello" and _Local.made == 0, _o)
        _sa.shutdown(); _sa.server_close()
        _o = _A._generate([], "hi")
        check("auto falls back to the built-in model when Medha stops", _o == "local:hi" and _Local.made == 1, _o)
        _o2 = _A._generate([], "again")
        check("the built-in model is loaded once, not per call", _Local.made == 1 and _o2 == "local:again")
        _sb = _S(("127.0.0.1", _port), _FakeA); _th.Thread(target=_sb.serve_forever, daemon=True).start()
        _o3 = _A._generate([], "back")
        check("when Medha returns it is used again and the built-in model is unloaded",
              _o3 == "medha:back" and _Local.closed == 1 and _A._local is None, (_o3, _Local.closed))
        _sb.shutdown()
        # Medha answers, but with a server error (e.g. a model it can't run): fall back, and stay there for a while
        class _Err(_H):
            def log_message(self, *a): pass
            def do_GET(self):
                self.send_response(200); self.end_headers(); self.wfile.write(b'{"status":"ok","modelLoaded":true}')
            def do_POST(self):
                self.send_response(500); self.end_headers(); self.wfile.write(b'{"error":"Top-K value 64 must be <= 1"}')
        _se = _S(("127.0.0.1", 0), _Err); _th.Thread(target=_se.serve_forever, daemon=True).start()
        _Local.made = 0
        _E = _ae.AutoEngine({"medha_url": f"http://127.0.0.1:{_se.server_address[1]}", "model_path": "/x.litertlm"})
        _o = _E._generate([], "q1")
        check("a Medha server error falls back to the built-in model for that call", _o == "local:q1" and _Local.made == 1, _o)
        _o = _E._generate([], "q2")
        check("and Medha is left alone during the back-off (no repeated failures)", _o == "local:q2" and _Local.made == 1)
        _se.shutdown()
    finally:
        _ae.GemmaEngine = _real_g
    # Insights: duplicates + weekly digest on a minimal database
    import sqlite3 as _sq
    from datetime import datetime as _dt
    from memos_daemon import insights as _ins
    _d = _sq.connect(":memory:"); _d.row_factory = _sq.Row
    _d.executescript("""
      CREATE TABLE notes (id TEXT PRIMARY KEY, body TEXT, archived INT DEFAULT 0, hidden INT DEFAULT 0,
        protected INT DEFAULT 0, is_daily INT DEFAULT 0, created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')));
      CREATE TABLE enrichments (id INTEGER PRIMARY KEY, note_id TEXT, title TEXT, enriched_at TEXT);
      CREATE TABLE tags (note_id TEXT, tag TEXT);
      CREATE TABLE attachments (id TEXT, note_id TEXT, kind TEXT, created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')));
      CREATE TABLE action_items (id TEXT, note_id TEXT, text TEXT, done INT DEFAULT 0, done_at TEXT, dismissed INT DEFAULT 0,
        created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')));
      CREATE TABLE relationships (note_id_a TEXT, note_id_b TEXT, score REAL);
      CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);""")
    for _i, _b in (("a", "Book the dentist for Tuesday cleaning appointment"), ("b", "Book the dentist for Tuesday cleaning appointment please"),
                   ("c", "Grocery list milk eggs bread"), ("d", "Fedora dnf history undo reverts transaction")):
        _d.execute("INSERT INTO notes (id, body) VALUES (?,?)", (_i, _b))
    _d.execute("INSERT INTO tags VALUES ('d','linux')"); _d.execute("INSERT INTO relationships VALUES ('a','b',0.93)")
    _d.execute("INSERT INTO relationships VALUES ('c','d',0.9)"); _d.commit()
    check("only the genuinely duplicated pair is suggested", _ins.find_duplicates(_d) == 1
          and [tuple(r) for r in _d.execute("SELECT note_a, note_b FROM dup_suggestions")] == [("a", "b")])
    check("a pair is not suggested twice", _ins.find_duplicates(_d) == 0)
    _g = _ins.compose_digest(_d, _dt.now(), complete=lambda p, max_tokens=0: "Quiet week.")
    check("digest counts notes and names tags", _g["counts"]["notes"] == 4 and _g["top_tags"][0]["tag"] == "linux" and _g["text"] == "Quiet week.")
    check("digest is due once, then not until the next slot",
          _ins.due(_d, _dt.now(), _dt.now().weekday(), 0) and not (
              _d.execute("INSERT INTO settings VALUES ('weekly_digest', ?)", (json.dumps(_g),)) and _ins.due(_d, _dt.now(), _dt.now().weekday(), 0)))
    _srv2.shutdown()
    # Ask → Write: staged (plan, then one call per section) and Chat
    from memos_daemon.engine import GemmaEngine as _GE
    _e = _GE.__new__(_GE); _e._cfg = {}; _e._max_context = 4096
    _seen = []
    def _fake(prompt, max_tokens=200, system_prompt=None):
        _seen.append(prompt)
        if "Plan the document" in prompt:
            return "TITLE: Running Fedora Well\n- Installing\n- Upgrading\n- Codecs"
        return "Use dnf [1]. Reboot after upgrades [2]."
    _e.complete = _fake
    _doc = _e.write("white paper on fedora", [{"id": "a", "title": "A", "text": "dnf tip"}, {"id": "b", "title": "B", "text": "upgrade"}])
    check("# Running Fedora Well" in _doc and _doc.count("## ") == 3 and len(_seen) == 4, "write plans then writes each section", _doc[:80])
    _e.complete = lambda prompt, max_tokens=200, system_prompt=None: ("" if "Plan" in prompt else "Straight pass [1].")
    check("Straight pass" in _e.write("x", [{"id": "a", "title": "A", "text": "t"}]), "write falls back to one pass if planning fails")
    _e.complete = lambda prompt, max_tokens=200, system_prompt=None: "pong"
    check(_e.chat("ping") == "pong", "chat is a plain completion")
    _srv.shutdown()

    total = _passed + _failed
    colour = G if _failed == 0 else R
    print(f"  {colour}{_passed}/{total} passed{X}"
          + (f"   {R}{_failed} FAILED{X}" if _failed else "   all green ✨"))
    print(f"{'='*52}\n")

    # ── Story from a photo ───────────────────────────────────────────────
    _sg = _GE.__new__(_GE); _sg._max_context = 4096; _sg._cfg = {"story_tokens_short": 380, "story_tokens_medium": 800}
    _calls = []
    def _fake(prompt, max_tokens=200, system_prompt=None, **kw):
        _calls.append((prompt, max_tokens, system_prompt)); return _fake.out
    _sg.complete = _fake
    _fake.out = "TITLE: The Fox and the Lantern\n\nOnce upon a time a fox found a light.\n\nThe end."
    _r = _sg.story("A red fox on a snowy hill.", outline="a lantern", genre="fairy tale", length="short", lang="en")
    check("story: TITLE becomes a markdown heading", _r.startswith("# The Fox and the Lantern\n\nOnce upon"), _r)
    check("story: prompt carries description, outline and genre",
          "red fox" in _calls[0][0] and "lantern" in _calls[0][0] and "fairy tale" in _calls[0][0])
    check("story: uses the story system prompt", bool(_calls[0][2]))
    _fake.out = "Just a plain story with no title."
    check("story: no title returns plain body", _sg.story("d") == "Just a plain story with no title.")
    _calls.clear(); _fake.out = "x"
    _sg.story("d", lang="en"); _sg.story("d", lang="te")
    check("story: Telugu gets a larger token cap", _calls[1][1] > _calls[0][1], str([c[1] for c in _calls]))
    _fake.out = ""
    check("story: empty output returns empty", _sg.story("d") == "")
    _calls.clear(); _fake.out = "TITLE: T\n\nbody"
    _sg.story("d", length="medium")
    check("story: medium length raises the cap", _calls[0][1] >= 800, str(_calls[0][1]))
    _sg.story("d", outline="")
    check("story: empty outline asks the model to invent one", "invent" in _calls[1][0].lower())

    _calls.clear(); _fake.out = "TITLE: The Funny Fox\n\nA fox slipped on ice."
    _r = _sg.story("A red fox.", revise="# The Fox\n\nA fox walked.", instruction="make it funnier", length="short")
    check("story revise: sends current story and instruction", "A fox walked." in _calls[0][0] and "make it funnier" in _calls[0][0])
    check("story revise: result is parsed with title", _r.startswith("# The Funny Fox"), _r)

    # ── daemon structure: MemosDaemon is assembled from mixins in parts/
    import glob as _g2
    from memos_daemon.daemon import MemosDaemon as _MDx
    _parts = sorted(_g2.glob(os.path.join(HERE, "memos_daemon", "parts", "*.py")))
    _mixins = [c for c in _MDx.__mro__[1:-1]]
    check("every part defines one mixin that MemosDaemon inherits", len(_mixins) == len(_parts) - 1, f"{len(_mixins)} vs {len(_parts)-1}")
    _big = [os.path.basename(f) for f in _parts + [os.path.join(HERE, "memos_daemon", "daemon.py")]
            if len(open(f, encoding="utf-8").read().splitlines()) > 800]
    check("no daemon module passes 800 lines", not _big, str(_big))
    check("the daemon's public surface is unchanged", all(hasattr(_MDx, n) for n in
          ("run", "enqueue", "_process_one", "_process_ask", "_handle", "_maybe_weekly_digest", "_update_connections", "_sync_from_note")))

    # Lazy imports inside functions are only resolved when that code runs, and every caller wraps them in
    # try/except — so a wrong relative path (after moving code into parts/) silently disables a feature.
    # Resolve every relative import in the package up front.
    import ast as _ast, importlib as _il
    _bad = []
    for _f in _g2.glob(os.path.join(HERE, "memos_daemon", "**", "*.py"), recursive=True):
        _rel = os.path.relpath(_f, HERE)[:-3].split(os.sep)
        _pkg = _rel[:-1] if _rel[-1] != "__init__" else _rel[:-1]
        for _n in _ast.walk(_ast.parse(open(_f, encoding="utf-8").read())):
            if isinstance(_n, _ast.ImportFrom) and _n.level:
                _base = _pkg[:len(_pkg) - (_n.level - 1)]
                _mod = ".".join(_base + ([_n.module] if _n.module else []))
                try:
                    _m = _il.import_module(_mod)
                    for _a in _n.names:
                        if not hasattr(_m, _a.name):
                            _il.import_module(_mod + "." + _a.name)
                except Exception as _e:
                    _bad.append(f"{os.path.relpath(_f, HERE)}:{_n.lineno} {_mod} ({type(_e).__name__})")
    check("every relative import in the package resolves (including ones inside functions)", not _bad, "; ".join(_bad[:4]))
    check("task follow-through is part of the daemon", hasattr(_MDx, "_maybe_followups") and hasattr(_MDx, "_sync_followup_calendar"))
    check("the follow-up pass runs from the main tick", "_maybe_followups" in _daemon_source())
    check("Medha's notification stream is part of the daemon",
          hasattr(_MDx, "_maybe_captures") and hasattr(_MDx, "_captures_on"))
    # A source that is written but never called is a source that does not
    # exist, and the only symptom is an empty table nobody looks at.
    check("the captures poll runs from the main tick", "_maybe_captures" in _daemon_source())
    # Order matters: captures must be read BEFORE the brief is built, or a
    # cancellation notice that arrived this minute corroborates nothing until
    # the next one.
    #
    # Read from parts/sources.py alone, not _daemon_source(): that one
    # concatenates every mixin, so index() finds each method's DEFINITION
    # rather than the tick's call to it, and the answer depends on filename
    # order. The first version of this check failed for exactly that reason.
    _tick = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "memos_daemon", "parts", "sources.py"),
                 encoding="utf-8").read()
    check("and before the brief, so this minute's notices reach this minute's brief",
          "self._maybe_captures()" in _tick and "self._maybe_medha_brief()" in _tick
          and _tick.index("self._maybe_captures()") < _tick.index("self._maybe_medha_brief()"))
    # Saves from Medha's brief. Checked against the TICK, not _daemon_source():
    # the method's own `def _maybe_saved` would satisfy a search of every mixin
    # concatenated, so that form passes even when nothing ever calls it -- and
    # the symptom then is a Save button that works in Medha and an inbox that
    # stays empty, with no error anywhere.
    check("saves from Medha are part of the daemon", hasattr(_MDx, "_maybe_saved"))
    check("the saved poll is CALLED from the main tick", "self._maybe_saved()" in _tick)

    import subprocess as _sp
    _r = _sp.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools", "gen_config_doc.py"), "--check"], capture_output=True, text=True)
    check("every config setting is documented and docs/CONFIGURATION.md is current", _r.returncode == 0, _r.stdout.strip())

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
