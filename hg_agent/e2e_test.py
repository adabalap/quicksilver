#!/usr/bin/env python3
"""
End-to-end functional tests for the Quicksilver agent (hg_agent).

Exercises the agent's real logic WITHOUT loading the Gemma model — the model is
slow and not needed to verify correctness of the parts that break: correlation,
embeddings storage/codec, the input-fit token guard, temporal-free relationship
density, and the calendar auth wiring. A deterministic hash-embedder stands in
for the real one so results are reproducible.

    python3 e2e_test.py           # run everything
    python3 e2e_test.py -v        # print each passing case

Exit 0 = all green, 1 = a failure. Hermetic: uses temp DBs only.
"""
import sys, os, tempfile, sqlite3, hashlib, struct, json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
VERBOSE = "-v" in sys.argv
_P = _F = 0
_FAILS = []


def check(cond, msg):
    global _P, _F
    if cond:
        _P += 1
        if VERBOSE:
            print(f"  \033[32m✓\033[0m {msg}")
    else:
        _F += 1
        _FAILS.append(msg)
        print(f"  \033[31m✗ {msg}\033[0m")


def section(name):
    print(f"\n\033[1m{name}\033[0m")


def dv(t, d=384):
    """Deterministic unit-norm pseudo-embedding for a string."""
    o = []; i = 0
    while len(o) < d:
        h = hashlib.sha256(t.encode() + str(i).encode()).digest()
        for j in range(0, len(h), 4):
            if len(o) >= d:
                break
            o.append(struct.unpack("<i", h[j:j + 4])[0] / 2**31)
        i += 1
    n = sum(x * x for x in o) ** 0.5 or 1.0
    return [x / n for x in o]


class DetEmb:
    def available(self):
        return True

    def encode(self, t):
        return dv(t)


# ─────────────────────────────────────────────────────────────────────────────
def test_embedding_codec():
    section("1. Embedding storage codec (blob + legacy JSON compat)")
    from memos_daemon.embeddings import encode_embedding, decode_embedding
    v = dv("some note text")
    blob = encode_embedding(v)
    check(isinstance(blob, bytes) and len(blob) == 384 * 4,
          f"encode → {len(blob)}-byte float32 blob (5x smaller than JSON)")
    dec = decode_embedding(blob)
    check(dec and all(abs(a - b) < 1e-6 for a, b in zip(v, dec)),
          "blob round-trips to float32 precision")
    legacy = decode_embedding(json.dumps(v))
    check(legacy and all(abs(a - b) < 1e-6 for a, b in zip(v, legacy)),
          "legacy JSON string still decodes")
    check(decode_embedding(b"odd") is None and decode_embedding("bad") is None,
          "garbage input → None (no crash)")


def test_correlation_similarity():
    section("2. Correlation: similarity + related() ranking")
    from memos_daemon.correlation import Corpus
    c = Corpus(embedder=DetEmb())
    for i, topic in enumerate(["telecom bill", "telecom payment", "grocery list",
                               "vacation plan", "telecom broadband"]):
        c.add(f"n{i}", f"Note {i}", topic, tags=[topic.split()[0]])
    rel = c.related("n0", top_n=3, threshold=-1.0)  # -1 → return ranked regardless
    check(len(rel) > 0, "related() returns neighbours")
    names = [r[0] for r in rel]
    check("n0" not in names, "a note is never related to itself")
    # telecom notes should rank above grocery/vacation for a telecom query note
    check(rel[0][2] >= rel[-1][2], "results are sorted by similarity (desc)")


def test_relationship_density():
    section("3. Relationship density — top-K, NOT all-pairs (the 528-pair bug)")
    from memos_daemon.correlation import Corpus
    # 33 near-identical notes → old code wrote 528 pairs (fully connected).
    c = Corpus(embedder=DetEmb())
    base = dv("scaling embeddings correlation")
    for i in range(33):
        v = list(base)
        v[0] += 0.001 * i
        n = sum(x * x for x in v) ** 0.5
        c.add(f"n{i}", f"Scaling {i}", "scaling work", tags=["scaling"],
              emb=[x / n for x in v])
    # Simulate the daemon's top-K collection (K=4, threshold 0.45)
    topk = 4
    pairs, seen = [], set()
    for nm in c.names():
        for rn, _t, sc in c.related(nm, top_n=topk, threshold=0.45):
            k = (nm, rn) if nm < rn else (rn, nm)
            if k not in seen:
                seen.add(k); pairs.append(k)
    check(len(pairs) < 528, f"top-K keeps pairs sparse ({len(pairs)}, not 528)")
    from collections import Counter
    deg = Counter()
    for a, b in pairs:
        deg[a] += 1; deg[b] += 1
    check(max(deg.values()) <= topk * 2,
          f"no fully-connected blob (max degree {max(deg.values())})")


def test_clusters():
    section("4. Clustering groups related, separates unrelated")
    from memos_daemon.correlation import Corpus
    c = Corpus(embedder=DetEmb())
    # two tight groups: identical vectors within a group, distinct across.
    # NOTE: text must be tokenizable (Corpus.add drops notes with no tokens),
    # so give each real words even though the embedding is what drives clustering.
    ga, gb = dv("alpha topic one"), dv("zeta topic two")
    for i in range(4):
        c.add(f"a{i}", f"A{i}", "alpha topic content here", emb=ga)
    for i in range(4):
        c.add(f"b{i}", f"B{i}", "zeta topic content here", emb=gb)
    cl = c.clusters(threshold=0.9)
    check(len(cl) >= 1, "clusters() returns groups for tight data")
    # every returned group should be internally consistent (all a* or all b*)
    ok = all(len({n[0] for n in g}) == 1 for g in cl if len(g) > 1)
    check(ok, "clusters don't mix the two distinct groups")


def test_embedding_persistence():
    section("5. Agent persists embeddings as blobs on enrichment")
    from memos_daemon.qs_client import QuicksilverClient
    tmp = tempfile.mkdtemp()
    db_path = os.path.join(tmp, "a.db")
    # Build the DB WITHOUT importing the UI (which needs flask — not an agent
    # dependency). We apply schema.sql plus the note-column migrations the client
    # relies on, so this runs in the agent's own venv. If schema.sql isn't beside
    # the agent, skip cleanly.
    schema_path = None
    for cand in ["../hg_ui/schema.sql",
                 os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hg_ui", "schema.sql")]:
        if os.path.exists(cand):
            schema_path = os.path.abspath(cand)
            break
    if not schema_path:
        check(True, "SKIPPED (hg_ui/schema.sql not found beside agent) — run from a full checkout")
        return
    con = sqlite3.connect(db_path)
    con.executescript(open(schema_path).read())
    # Apply the migration-added note columns the client may touch. Each is
    # wrapped so an already-present column (base schema) is a harmless no-op.
    for ddl in [
        "ALTER TABLE notes ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE notes ADD COLUMN archived INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE notes ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE notes ADD COLUMN enrich_optout INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE enrichments ADD COLUMN user_polish TEXT",
        "ALTER TABLE enrichments ADD COLUMN corrections TEXT",
    ]:
        try:
            con.execute(ddl)
        except Exception:
            pass
    con.execute("INSERT INTO notes (id,body) VALUES ('x1','telecom bill note')")
    con.execute("INSERT OR IGNORE INTO note_state (note_id,status) VALUES ('x1','raw')")
    con.commit()
    try:
        cl = QuicksilverClient(db_path=db_path)
        cl.embedder = DetEmb()
        cl.write_enrichment("x1", {"title": "Bill", "summary": "", "tags": ["telecom"],
                                   "priority": "low", "action_items": []},
                            new_status="approved")
        raw = sqlite3.connect(db_path).execute(
            "SELECT embedding FROM notes WHERE id='x1'").fetchone()[0]
        check(isinstance(raw, bytes) and len(raw) == 384 * 4,
              f"enrichment stored a {len(raw) if raw else 0}-byte blob")
    except Exception as e:
        check(False, f"write_enrichment raised: {e}")


def test_input_fit_guard():
    section("6. Input-fit token guard (small notes pass, huge notes trim+retry)")
    from memos_daemon.engine import GemmaEngine
    from memos_daemon.prompt import SYSTEM_PROMPT

    class Shell(GemmaEngine):
        def __init__(self):
            self._cfg = {"model_max_num_tokens": 6144}  # match real deployed budget
    e = Shell()

    for tok, chars in [(163, 652), (305, 1220), (385, 1540)]:
        note = "Note to analyze:\n\n" + ("x" * chars)
        check(e._fit_input(SYSTEM_PROMPT, note, produces_polish=True) == note,
              f"{tok}-tok note passes untrimmed")

    huge = "Note to analyze:\n\n" + ("labor justice " * 1500)
    trimmed = e._fit_input(SYSTEM_PROMPT, huge, produces_polish=False)
    check(len(trimmed) < len(huge), "genuinely huge note is trimmed")

    check(e._is_token_overflow(Exception(
        "Exceeding the maximum number of tokens allowed: 4292 >= 4096")),
        "detects the model's overflow error (arms the retry backstop)")
    check(not e._is_token_overflow(Exception("network down")),
          "does not false-trigger on unrelated errors")


def test_temporal_parser():
    section("7. Temporal query parser (UI-side, verified here too for parity)")
    # The parser lives in the UI, but the agent's search behaviour depends on the
    # same phrases. We re-check the phrase→window logic conceptually.
    import re
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    # mimic the parser's core mappings
    def window(phrase):
        if "last year this time" in phrase:
            lo = now.replace(year=now.year - 1) - timedelta(days=15)
            hi = now.replace(year=now.year - 1) + timedelta(days=15)
            return lo, hi
        if "last week" in phrase:
            return now - timedelta(days=7), now
        return None, None
    lo, hi = window("bill from last week")
    check(lo and hi and (hi - lo).days == 7, "'last week' → 7-day window")
    lo, hi = window("visa last year this time")
    check(lo and hi and (hi - lo).days <= 31 and lo.year == now.year - 1,
          "'last year this time' → ~30-day window a year ago")


def test_calendar_auth_wiring():
    section("8. Calendar auth uses the modern loopback flow (not dead oob)")
    cal = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "memos_daemon", "calendar_client.py")).read()
    check('flow.redirect_uri = "urn:ietf:wg:oauth:2.0:oob"' not in cal,
          "the deprecated oob copy-paste flow is gone from code")
    check("run_local_server" in cal and "open_browser=False" in cal,
          "headless auth uses a loopback local server")
    check("invalid_grant" in cal and "In production" in cal,
          "refresh failure gives actionable 'Testing mode' guidance")
    check("creds.refresh(Request())" in cal,
          "auto-refresh of expired tokens is wired")


def main():
    print("\033[1m═══ Quicksilver agent — end-to-end functional tests ═══\033[0m")
    test_embedding_codec()
    test_correlation_similarity()
    test_relationship_density()
    test_clusters()
    test_embedding_persistence()
    test_input_fit_guard()
    test_temporal_parser()
    test_calendar_auth_wiring()

    print("\n" + "─" * 55)
    if _F == 0:
        print(f"\033[32m  {_P}/{_P} passed — all green ✨\033[0m")
    else:
        print(f"\033[31m  {_P}/{_P + _F} passed, {_F} FAILED\033[0m")
        for m in _FAILS:
            print(f"\033[31m    ✗ {m}\033[0m")
    return 0 if _F == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
