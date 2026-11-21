#!/usr/bin/env python3
"""Tests for hybrid retrieval (retrieval.py + app._retrieve): RRF fusion,
chunk-level ranking, per-model gating, hidden-note privacy, legacy fallback, and
Ask's best-passage excerpts. Hermetic: temp DB, deterministic hashing embedder,
the embed sidecar patched out."""
import sys, os, tempfile, math, hashlib, re, struct, json
from unittest.mock import patch
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import retrieval as rv
from e2e_test import load_app

P = F = 0
def check(c, m):
    global P, F
    if c: P += 1
    else:
        F += 1; print(f"  \033[31m✗ {m}\033[0m")

DIM = 128
def emb(text):
    v = [0.0] * DIM
    for w in re.findall(r"\w+", text.lower()):
        if len(w) < 2: continue
        v[int(hashlib.md5(w.encode()).hexdigest()[:8], 16) % DIM] += 1
    n = math.sqrt(sum(x * x for x in v)) or 1
    return [x / n for x in v]
blob = lambda v: struct.pack(f"<{len(v)}f", *v)

# ── pure helpers ────────────────────────────────────────────────────────────
r = rv.rrf([["a", "b", "c"], ["b", "a", "d"]])
check(r["a"] == r["b"] and r["a"] > r["c"] and r["c"] > r["d"] or r["c"] == r["d"], "rrf: agreement outranks single-list")
check(rv.rrf([["x"], ["x"]])["x"] > rv.rrf([["x"]])["x"], "rrf: two lists beat one")
best = {"n1": (0.80, 0, "c"), "n2": (0.74, 0, "c"), "n3": (0.40, 0, "c")}
check(rv.gate(best, floor=0.5)[0] == ["n1", "n2"], "gate: absolute floor")
check(rv.gate(best, margin=0.07)[0] == ["n1", "n2"], "gate: margin keeps near-top hits")
check(rv.gate(best, margin=0.01)[0] == ["n1"], "gate: tight margin keeps only the top")
flat = {f"n{i}": (0.50 + 0.01 * (i % 2), 0, "c") for i in range(12)}
check(rv.gate(flat, margin=0.1, z_min=1.5)[0] == [], "gate: z-score abstains on a flat profile")
peaky = dict(flat); peaky["hit"] = (0.9, 0, "c")
check(rv.gate(peaky, margin=0.1, z_min=1.5)[0] == ["hit"], "gate: z-score passes a standout hit")
check(rv.gate({"a": (0.5, 0, "c")}, margin=0.1, z_min=1.5)[0] == ["a"], "gate: z skipped on tiny corpora")
check(rv.relevance(0.8, 0.4, 0.8) == 1.0 and rv.relevance(0.4, 0.4, 0.8) == 0.0, "relevance: mean→0, top→1")
check(0 < rv.relevance(0.6, 0.4, 0.8) < 1, "relevance: monotone in between")
ch = [(0, "Alpha beta gamma delta epsilon zeta eta theta."), (1, "delta epsilon zeta eta theta. Iota kappa lambda mu nu xi omicron."), (2, "omicron pi rho sigma.")]
ex = rv.excerpt_from_chunks(ch, 0, 700)
check("Iota" in ex and ex.count("epsilon zeta") == 1, "excerpt: extends into next chunk with overlap removed")
check(len(rv.excerpt_from_chunks([(0, "w " * 600)], 0, 100)) <= 105, "excerpt: respects the cap")
check(rv.excerpt_from_chunks(ch, 9, 700) == "", "excerpt: unknown chunk → empty")
q = emb("dentist appointment")
bc = rv.best_chunk_per_note(q, [("c1", "n1", 0, blob(emb("dentist appointment friday"))),
                                 ("c2", "n1", 1, blob(emb("groceries milk"))),
                                 ("c3", "n2", 0, blob(emb("groceries milk"))),
                                 ("bad", "n3", 0, b"xx")], DIM)
check(bc["n1"][1] == 0 and bc["n1"][0] > bc["n2"][0] and "n3" not in bc, "best chunk per note; malformed blobs skipped")

# ── integration against the real app ────────────────────────────────────────
tmp = tempfile.mkdtemp()
qs = load_app(os.path.join(tmp, "r.db"))
conn = qs.sqlite3.connect(os.path.join(tmp, "r.db")); conn.row_factory = qs.sqlite3.Row
conn.execute("PRAGMA foreign_keys=ON")

def add(body, hidden=0, chunks=None, model="m1"):
    nid = qs._nanoid(8)
    conn.execute("INSERT INTO notes (id, body, hidden) VALUES (?,?,?)", (nid, body, hidden))
    for i, c in enumerate(chunks if chunks is not None else [body]):
        conn.execute("INSERT INTO note_chunks (id,note_id,idx,text,embedding,model,body_hash) VALUES (?,?,?,?,?,?,?)",
                     (f"{nid}:{i}", nid, i, c, blob(emb(c)), model, "h"))
    conn.commit(); return nid

long_chunks = ["Quarterly planning meeting notes about hiring and budget.",
               "Quarterly planning meeting notes about hiring and budget. The wifi password for the cabin is lakehouse42.",
               "Closing remarks and action items for next sprint."]
n_long = add(" ".join(long_chunks), chunks=long_chunks)
n_dent = add("Dentist appointment Friday at 3pm with Dr Rao")
n_milk = add("Buy milk eggs and bread from the market")
n_hid = add("Secret dentist appointment for a surprise party", hidden=1)
conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('embed_active',?)",
             (json.dumps({"model": "m1", "dim": DIM, "profile": {"floor_search": 0.2, "floor_ask": 0.1, "margin": None, "z_min": None}}),))
conn.commit()

def fake_qi(text, model="m1"):
    return {"embedding": emb(text), "model": model, "dim": DIM}

with qs.app.app_context():
    db = qs.get_db()
    with patch.object(qs.search, "_embed_query_info", side_effect=fake_qi):
        r = qs._retrieve(db, "dentist appointment", "dentist appointment", "search")
        check(r["mode"] == "hybrid", "uses the chunk index when present")
        check(r["ids"][0] == n_dent, "best note ranks first")
        check(n_hid not in r["ids"], "hidden notes are excluded from search")
        r = qs._retrieve(db, "wifi password cabin", "wifi password cabin", "ask")
        check(r["ids"][0] == n_long, "finds the answer buried in a long note")
        check("lakehouse42" in (r["info"][n_long].get("chunk_text") or ""), "Ask excerpt is the passage holding the answer")
        check(isinstance(r["info"][n_long].get("score"), float) and 0 <= r["info"][n_long]["score"] <= 1, "relevance is a 0-1 score")
        r = qs._retrieve(db, "zzzz qqqq xxxx", "zzzz qqqq xxxx", "ask")
        check(r["ids"] == [], "nonsense query abstains under an absolute floor")
    # model mismatch between sidecar and index → refuse to rank across spaces
    with patch.object(qs.search, "_embed_query_info", side_effect=lambda t: fake_qi(t, "other-model")):
        r = qs._retrieve(db, "dentist appointment", "dentist appointment", "search")
        check(r["mode"] == "keyword" and n_dent in r["ids"], "model mismatch falls back to keyword")
    # no sidecar
    with patch.object(qs.search, "_embed_query_info", return_value=None):
        r = qs._retrieve(db, "dentist", "dentist", "search")
        check(r["mode"] == "keyword" and n_dent in r["ids"] and n_hid not in r["ids"], "keyword-only when embedder is down (hidden still excluded)")
    # relative gate profile (e5-style) with a keyword hit that bypasses it
    conn.execute("UPDATE settings SET value=? WHERE key='embed_active'",
                 (json.dumps({"model": "m1", "dim": DIM, "profile": {"floor_search": None, "floor_ask": None, "margin": 0.05, "z_min": None}}),)); conn.commit()
    with patch.object(qs.search, "_embed_query_info", side_effect=fake_qi):
        r = qs._retrieve(db, "dentist appointment", "dentist appointment", "search")
        check(r["ids"][0] == n_dent and n_milk not in r["ids"], "relative gate drops far-below-top notes")
    # legacy: chunks absent for the active model → note-level path
    conn.execute("UPDATE settings SET value=? WHERE key='embed_active'",
                 (json.dumps({"model": "never-indexed", "dim": DIM, "profile": {}}),)); conn.commit()
    conn.execute("UPDATE notes SET embedding=? WHERE id=?", (blob(emb("Dentist appointment Friday at 3pm with Dr Rao")), n_dent)); conn.commit()
    with patch.object(qs.search, "_embed_query_info", side_effect=lambda t: fake_qi(t, "never-indexed")):
        r = qs._retrieve(db, "dentist appointment", "dentist appointment", "search")
        check(r["mode"] == "legacy" and n_dent in r["ids"], "legacy note-level fallback when no chunks")

    check(qs._rank_by_embedding(emb("x y"), [{"id": "z", "embedding": blob([0.1] * 384)}], 0.0, set()) == [],
          "legacy ranking skips vectors of a different dimension")
    # /api/search and /api/ask end to end
    conn.execute("UPDATE settings SET value=? WHERE key='embed_active'",
                 (json.dumps({"model": "m1", "dim": DIM, "profile": {"floor_search": 0.2, "floor_ask": 0.1}}),)); conn.commit()
cl = qs.app.test_client()
with patch.object(qs.search, "_embed_query_info", side_effect=fake_qi):
    js = cl.get("/api/search?q=dentist+appointment").get_json()
    check(js["mode"] == "semantic" and js["notes"][0]["id"] == n_dent, "/api/search returns hybrid results")
    check(all(n["id"] != n_hid for n in js["notes"]), "/api/search hides hidden notes")
    a = cl.post("/api/ask", json={"question": "what is the wifi password for the cabin"}).get_json()
    ids = [e["id"] for e in a.get("retrieved", [])]
    check(n_long in ids and n_hid not in ids, "/api/ask retrieves the long note, never a hidden one")
    ctx = next(e for e in a["retrieved"] if e["id"] == n_long)
    check("lakehouse42" in ctx["text"] and ctx.get("passage"), "/api/ask hands the model the best passage")

total = P + F
print(f"\n  {'\033[32m' if not F else '\033[31m'}{P}/{total} passed{'  all green ✨' if not F else '  FAILED'}\033[0m")
sys.exit(1 if F else 0)
