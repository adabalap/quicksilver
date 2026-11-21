"""Search and retrieval: temporal parsing, keyword + semantic ranking, rank fusion, tag list."""
import os
import re
from flask import jsonify, request

import retrieval as _rv

from . import fts
from .core import _settings_json, app, get_db, log
from .notes_common import _note_filters, _note_row
from .settings import _get_prefs


# ── API: Tags ─────────────────────────────────────────────────────────────────
def _decode_embedding(raw):
    """Decode a stored embedding. Accepts the compact float32 BLOB (bytes) or a
    legacy JSON string, so old rows keep working until re-embedded. Returns None
    on anything unreadable. Mirrors the agent's embeddings.decode_embedding —
    the two components must agree on the wire format for notes.embedding.
    """
    if raw is None:
        return None
    if isinstance(raw, (bytes, bytearray, memoryview)):
        import struct
        b = bytes(raw)
        if len(b) % 4 != 0:
            return None
        try:
            return list(struct.unpack(f"<{len(b)//4}f", b))
        except Exception:
            return None
    if isinstance(raw, str):
        try:
            import json as _j
            v = _j.loads(raw)
            return [float(x) for x in v] if isinstance(v, list) else None
        except Exception:
            return None
    if isinstance(raw, list):
        return raw
    return None

def _parse_temporal(q):
    """Extract a date window and residual text from a natural-language query.

    Returns (lo_iso, hi_iso, residual_query). This is what makes "that bill from
    last week" and "what was I writing last year this time" work: the time
    phrase becomes a created_at filter (which vectors CANNOT encode — an
    embedding has no notion of "last week"), and the rest of the query is what we
    keyword/semantic rank within that window. Either bound may be None (open
    range); residual is the query with the time phrase stripped.

    Deliberately small and rule-based: a handful of phrases people actually type,
    not a general date grammar. Unmatched queries return (None, None, q) — i.e.
    plain search, unchanged.
    """
    import re
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    ql = q.lower()

    def iso(d):
        return d.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    # Calendar days are the person's own (this server's local time), then expressed in UTC like created_at.
    local = datetime.now().astimezone()
    def day0(offset=0):
        d = local.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=offset)
        return d.astimezone(timezone.utc).replace(tzinfo=None)

    # Ordered longest-first so "last year this time" wins over "last year".
    patterns = [
        (r"\b(last year (?:around )?this time|this time last year)\b",
         lambda: (now.replace(year=now.year - 1) - timedelta(days=15),
                  now.replace(year=now.year - 1) + timedelta(days=15))),
        (r"\b(last year)\b",
         lambda: (now.replace(year=now.year - 1) - timedelta(days=30),
                  now.replace(year=now.year - 1) + timedelta(days=30))),
        (r"\b(this year)\b",      lambda: (now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0), now)),
        (r"\b(yesterday)\b",      lambda: (day0(1), day0(0))),
        (r"\b(today)\b",          lambda: (day0(0), now)),
        (r"\b(this week)\b",      lambda: (day0(local.weekday()), now)),
        (r"\b((?:last|past|previous) week)\b", lambda: (now - timedelta(days=7), now)),
        (r"\b(this month)\b",     lambda: (day0(local.day - 1), now)),
        (r"\b((?:last|past|previous) month)\b", lambda: (now - timedelta(days=30), now)),
        (r"\b(recently|lately)\b", lambda: (now - timedelta(days=14), now)),
        (r"\b((?:last|past) (\d+) days?)\b", None),  # handled specially below
    ]

    for pat, fn in patterns:
        m = re.search(pat, ql)
        if not m:
            continue
        if fn is None:
            # "last N days"
            n = int(m.group(2))
            lo, hi = now - timedelta(days=n), now
        else:
            lo, hi = fn()
        residual = re.sub(pat, " ", ql).strip()
        residual = re.sub(r"\s+", " ", residual)
        # Strip now-dangling connectors left behind ("from", "in", "about").
        residual = re.sub(r"\b(from|in|during|about|the)\b\s*$", "", residual).strip()
        residual = re.sub(r"^\s*\b(from|in|during|about|the)\b", "", residual).strip()
        return iso(lo), iso(hi), residual

    return None, None, q

def _cosine(a, b):
    import math
    dot = sum(x*y for x, y in zip(a, b))
    na = math.sqrt(sum(x*x for x in a)); nb = math.sqrt(sum(y*y for y in b))
    return dot/(na*nb) if na and nb else 0.0

def _embed_query_info(text):
    """Query embedding from the agent's sidecar: {"embedding", "model", "dim"} or
    None when unavailable (caller falls back to keyword search). The sidecar
    applies the model's query prefix; `model` lets us verify we are ranking in
    the same vector space as the index."""
    import json as _j, urllib.request, urllib.error
    url = os.environ.get("HG_EMBED_URL", "http://127.0.0.1:5232/embed")
    try:
        req = urllib.request.Request(
            url, data=_j.dumps({"text": text, "kind": "query"}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=3) as r:
            d = _j.loads(r.read())
        return d if d.get("embedding") else None
    except Exception:
        return None

def _embed_query(text):
    """Back-compat: just the vector."""
    d = _embed_query_info(text)
    return d["embedding"] if d else None

def _transcript_hits(db, text, time_filter="", vis="", time_params=None):
    """Notes whose photo text / voice transcript contains any query word."""
    terms = [t for t in re.split(r"\s+", (text or "").lower()) if len(t) > 1][:6]
    if not terms:
        return []
    cond = " OR ".join("lower(a.transcript) LIKE ?" for _ in terms)
    try:
        rows = db.execute(
            f"SELECT DISTINCT n.id FROM notes n JOIN attachments a ON a.note_id=n.id "
            f"WHERE a.transcript<>'' AND ({cond}){time_filter}{vis}",
            [f"%{t}%" for t in terms] + list(time_params or [])).fetchall()
    except Exception:
        return []
    return [r["id"] for r in rows]

def _keyword_ranked(db, text, time_filter="", time_params=None):
    """Note ids matching `text` by keyword, BEST FIRST, honouring an optional
    time filter. Hidden notes never match (hidden stays private outside the
    Hidden view). FTS5 BM25 order when the build has it, else LIKE. When `text`
    is empty (a pure temporal query like "yesterday") returns the window newest
    first so the time filter alone drives the result."""
    time_params = time_params or []
    text = (text or "").strip()
    vis = " AND COALESCE(n.hidden,0)=0"

    if not text:
        rows = db.execute(
            f"SELECT n.id FROM notes n WHERE 1=1{time_filter}{vis} "
            f"ORDER BY n.created_at DESC LIMIT 200", time_params).fetchall()
        return [r["id"] for r in rows]

    if fts.HAS_FTS5:
        try:
            terms = [t for t in re.split(r"\s+", text.lower()) if t]
            fts_q = " OR ".join(f'"{t}"*' for t in terms) if terms else '""'
            rows = db.execute(
                f"SELECT n.id FROM notes_fts f JOIN notes n ON n.rowid = f.rowid "
                f"WHERE notes_fts MATCH ?{time_filter}{vis} ORDER BY f.rank LIMIT 200",
                [fts_q] + time_params).fetchall()
            ids = [r["id"] for r in rows]
            # Tags aren't in the body index; append tag matches so #telecom still
            # finds its notes (after the body hits: a tag is a weaker signal).
            trows = db.execute(
                f"SELECT DISTINCT n.id FROM notes n JOIN tags t ON t.note_id=n.id "
                f"WHERE t.tag LIKE ?{time_filter}{vis}",
                [f"%{text.lower()}%"] + time_params).fetchall()
            seen = set(ids)
            ids += [r["id"] for r in trows if r["id"] not in seen]
            seen = set(ids)
            ids += [i for i in _transcript_hits(db, text, time_filter, vis, time_params) if i not in seen]
            return ids
        except Exception as e:
            log.debug(f"FTS query failed ({e}); falling back to LIKE.")

    like = f"%{text.lower()}%"
    rows = db.execute(
        f"SELECT DISTINCT n.id FROM notes n LEFT JOIN tags t ON t.note_id=n.id "
        f"WHERE (lower(n.body) LIKE ? OR lower(t.tag) LIKE ?){time_filter}{vis}",
        [like, like] + time_params).fetchall()
    ids = [r["id"] for r in rows]
    seen = set(ids)
    return ids + [i for i in _transcript_hits(db, text, time_filter, vis, time_params) if i not in seen]

def _keyword_ids(db, text, time_filter="", time_params=None):
    """The SET of keyword-matching ids (see _keyword_ranked for the order)."""
    return set(_keyword_ranked(db, text, time_filter, time_params))

def _embed_active(db):
    """The model/dim/profile the agent indexed with (settings 'embed_active'),
    or {} before an upgraded agent has published it."""
    return _settings_json(db, "embed_active", {})

def _retrieve(db, text, kw_text, kind, time_filter="", time_params=None):
    """Hybrid retrieval for /api/search (kind="search") and Ask (kind="ask").

    Returns {"ids": [...best first], "info": {id: {...}}, "mode": str,
             "qvec": vec|None}. info[id] may hold: sim, score (0-1, model-
    agnostic relevance), chunk_idx, chunk_text (the best passage), src
    ("vec"|"kw"|"both"). Modes: "hybrid" (chunk vectors + FTS fused with RRF),
    "legacy" (note-level vectors; an agent that has not indexed chunks yet),
    "keyword" (no embedder reachable).
    """
    time_params = time_params or []
    kw = _keyword_ranked(db, kw_text, time_filter, time_params)
    info = {i: {"src": "kw"} for i in kw}
    qi = _embed_query_info(text) if text else None
    if not qi:
        return {"ids": kw, "info": info, "mode": "keyword", "qvec": None}
    qvec = qi["embedding"]
    prefs = _get_prefs(db)
    pref_key = "ask_min_similarity" if kind == "ask" else "search_min_similarity"
    active = _embed_active(db)
    model = active.get("model") or qi.get("model")
    dim = int(active.get("dim") or qi.get("dim") or len(qvec))
    # The index and the live model must be the same vector space.
    if (qi.get("model") and model and qi["model"] != model) or len(qvec) != dim:
        return {"ids": kw, "info": info, "mode": "keyword", "qvec": None}

    nchunks = db.execute("SELECT COUNT(*) c FROM note_chunks WHERE model=?",
                         (model,)).fetchone()["c"] if model else 0
    if nchunks == 0:
        # Chunk index not built yet (agent not upgraded / first boot): behave as
        # before — note-level vectors, +keyword union — so nothing regresses.
        min_sim = float(prefs.get(pref_key, 0.30 if kind == "search" else 0.20))
        # Only vectors made by the ACTIVE model are comparable (legacy untagged
        # rows were MiniLM). Same-dim vectors from different models would
        # otherwise be compared silently.
        if model:
            rows = db.execute(
                "SELECT id, embedding FROM notes WHERE embedding IS NOT NULL "
                "AND COALESCE(hidden,0)=0 AND (embed_model=? OR "
                "(embed_model IS NULL AND ?='all-MiniLM-L6-v2'))",
                (model, model)).fetchall()
        else:
            rows = db.execute(
                "SELECT id, embedding FROM notes WHERE embedding IS NOT NULL "
                "AND COALESCE(hidden,0)=0").fetchall()
        scored = _rank_by_embedding(qvec, rows, min_sim, set(kw))
        for i, sc in scored:
            d = info.setdefault(i, {"src": "vec"})
            d["sim"] = sc; d["score"] = round(min(1.0, max(0.0, sc)), 3)
        ids = [i for i, _ in scored]
        return {"ids": ids or list(kw), "info": info, "mode": "legacy", "qvec": qvec}

    rows = db.execute(
        "SELECT c.id, c.note_id, c.idx, c.embedding FROM note_chunks c "
        "JOIN notes n ON n.id=c.note_id WHERE c.model=? AND COALESCE(n.hidden,0)=0"
        + time_filter, [model] + list(time_params)).fetchall()
    best = _rv.best_chunk_per_note(qvec, [(r[0], r[1], r[2], r[3]) for r in rows], dim)

    prof = active.get("profile") or {}
    floor = prof.get("floor_ask" if kind == "ask" else "floor_search")
    if floor is not None:   # calibrated model: the user's slider is the floor
        floor = float(prefs.get(pref_key, floor))
    vec_ids, mean, top = _rv.gate(best, floor=floor, margin=prof.get("margin"),
                                  z_min=prof.get("z_min"))
    kwset = set(kw)
    fused = _rv.rrf([vec_ids, kw])
    ids = sorted(fused, key=lambda i: fused[i], reverse=True)

    for i in vec_ids:
        sim, idx, _cid = best[i]
        d = info.setdefault(i, {"src": "vec"})
        d.update(sim=sim, chunk_idx=idx,
                 score=round(_rv.relevance(sim, mean, top), 3),
                 src="both" if i in kwset else "vec")
    # Passage text for the winners only (the bulk query above skipped text).
    for i in ids[:60]:
        d = info.get(i)
        if d and "chunk_idx" in d:
            ch = db.execute("SELECT idx, text FROM note_chunks WHERE note_id=? "
                            "AND model=? ORDER BY idx", (i, model)).fetchall()
            d["chunk_text"] = _rv.excerpt_from_chunks(
                [(c[0], c[1]) for c in ch], d["chunk_idx"], MAX_ASK_EXCERPT_CHARS)
    if kind == "ask":
        # The % shown beside a source must agree with its position. Order by that same number: meaning
        # score, plus a bonus when the note also contains the asked words, and a flat 0.6 for a note found
        # by words alone. (Fusion order alone put a note with a passing keyword mention above the note that
        # was 100% the best meaning match, and the card then showed a lower % first.)
        def _eff(i):
            d = info.get(i) or {}
            sc = d.get("score")
            if sc is None:
                return 0.6
            return min(1.0, sc + (0.15 if d.get("src") == "both" else 0.0))
        pos = {i: n for n, i in enumerate(ids)}
        ids = sorted(ids, key=lambda i: (-_eff(i), pos[i]))
        for i in ids:
            if i in info:
                info[i]["score"] = round(_eff(i), 3)
    return {"ids": ids, "info": info, "mode": "hybrid", "qvec": qvec}

@app.route("/api/search", methods=["GET"])
def semantic_search():
    """Hybrid search: temporal filter + keyword (FTS5) + semantic re-rank.

    "that bill from last week" → time window + "bill" ranked within it.
    "what was I writing last year this time" → window around a year ago.
    Plain queries → keyword + semantic, unchanged. Degrades gracefully: no FTS5
    build → LIKE; no embeddings/sidecar → keyword only."""
    db = get_db()
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"notes": [], "mode": "empty"})
    _ff, _fp = _note_filters(request.args)

    # Temporal parse: pull any date window out of the query. "that bill from last
    # week" → window + residual "that bill". The window becomes a created_at
    # filter (which embeddings cannot express); the residual is what we rank.
    lo, hi, residual = _parse_temporal(q)
    search_text = residual if residual else q
    time_filter = ""
    time_params = []
    if lo and hi:
        time_filter = " AND n.created_at >= ? AND n.created_at <= ?"
        time_params = [lo, hi]
    if _ff:
        time_filter += " AND " + " AND ".join(_ff)
        time_params = list(time_params) + _fp

    r = _retrieve(db, search_text if search_text else None, search_text,
                  "search", time_filter, time_params)
    ranked_ids = r["ids"]
    mode = {"hybrid": "semantic", "legacy": "semantic", "keyword": "keyword"}[r["mode"]]
    if mode == "keyword" and lo and hi:
        mode = "temporal"

    notes = [_note_row(db.execute("SELECT * FROM notes WHERE id=?", (i,)).fetchone())
             for i in ranked_ids[:50]]
    notes = [n for n in notes if n]
    return jsonify({"notes": notes, "mode": mode})

def _rank_by_embedding(qvec, rows, min_sim, kw_ids):
    """Rank notes by cosine similarity to the query vector.

    Fast path: when every stored embedding is a float32 BLOB and numpy is
    available, the raw bytes are concatenated and read straight into one matrix
    with np.frombuffer — NO per-row Python decode — then a single matmul scores
    the whole corpus. This is the entire point of the blob format: a JSON corpus
    forces a per-row json.loads (measured ~1.5s at 50k just to decode), while
    frombuffer does it in ~40ms. Mixed/legacy JSON rows fall back to per-row
    decode; a fully-legacy corpus still works, just slower until re-embedded.
    A note is kept if it clears min_sim OR was a keyword hit; keyword hits get a
    +0.20 boost so exact matches rank above loose semantic ones.
    """
    DIM = len(qvec)
    EXPECTED = DIM * 4  # bytes per float32 embedding (the query's dimension)
    try:
        import numpy as np
        # Split rows into pure-blob (fast) and everything else (per-row decode).
        blob_ids, blob_bytes, other = [], [], []
        for r in rows:
            e = r["embedding"]
            if isinstance(e, (bytes, bytearray, memoryview)) and len(bytes(e)) == EXPECTED:
                blob_ids.append(r["id"]); blob_bytes.append(bytes(e))
            else:
                other.append(r)
        scored = []
        q = np.asarray(qvec, dtype=np.float32)
        qn = np.linalg.norm(q) or 1.0
        if blob_bytes:
            # One buffer → one matrix, zero per-row Python.
            mat = np.frombuffer(b"".join(blob_bytes), dtype=np.float32).reshape(
                len(blob_bytes), DIM)
            rn = np.linalg.norm(mat, axis=1); rn[rn == 0] = 1.0
            sims = (mat @ q) / (rn * qn)
            for i, s in zip(blob_ids, sims.tolist()):
                if s > min_sim or i in kw_ids:
                    scored.append((i, s + (0.20 if i in kw_ids else 0)))
        # Legacy / odd rows: decode individually, same scoring.
        for r in other:
            v = _decode_embedding(r["embedding"])
            if v is None or len(v) != DIM:
                continue
            s = float(_cosine(qvec, v))
            if s > min_sim or r["id"] in kw_ids:
                scored.append((r["id"], s + (0.20 if r["id"] in kw_ids else 0)))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored
    except Exception:
        # Full scalar fallback — identical semantics, no numpy.
        scored = []
        for r in rows:
            v = _decode_embedding(r["embedding"])
            if v is None or len(v) != DIM:
                continue
            s = _cosine(qvec, v)
            if s > min_sim or r["id"] in kw_ids:
                scored.append((r["id"], s + (0.20 if r["id"] in kw_ids else 0)))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

MAX_ASK_EXCERPT_CHARS = 700   # per-excerpt cap — keeps 5 excerpts + the question

@app.route("/api/tags", methods=["GET"])
def list_tags():
    db = get_db()
    rows = db.execute("""
        SELECT t.tag, COUNT(*) as count FROM tags t JOIN notes n ON n.id=t.note_id
        WHERE n.archived=0 AND n.hidden=0 GROUP BY t.tag ORDER BY count DESC, t.tag
    """).fetchall()
    return jsonify({"tags": [{"tag": r["tag"], "count": r["count"]} for r in rows]})
