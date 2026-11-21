"""retrieval.py — pure ranking helpers for search and Ask (no Flask, no DB).

Pipeline (orchestrated in app.py `_retrieve`):
  1. Vector leg:  best-matching CHUNK per note, cosine vs the query vector,
                  gated per the active embedding model's profile.
  2. Keyword leg: FTS5 BM25 order (+ tag hits).
  3. Fusion:      Reciprocal Rank Fusion. Rank-based, so it needs no score
                  calibration between a cosine and a BM25 number — which is the
                  whole point: the old "+0.20 if keyword hit" bonus was a
                  hand-tuned constant that only made sense for one model.
  4. Gating is model-aware: an absolute floor where the model is calibrated
     (MiniLM), otherwise relative (within `margin` of the best hit and
     `z_min` std-devs above the corpus mean) because cosine bands differ
     wildly between models.
"""
import math

RRF_K = 60

try:                                   # numpy is optional; scalar path is identical
    import numpy as _np
except Exception:                      # pragma: no cover
    _np = None


def rrf(ranked_lists, k: int = RRF_K, weights=None) -> dict:
    """Reciprocal Rank Fusion: score(id) = Σ w / (k + rank), rank starting at 1."""
    out = {}
    for li, lst in enumerate(ranked_lists):
        w = (weights[li] if weights else 1.0)
        for rank, i in enumerate(lst, 1):
            out[i] = out.get(i, 0.0) + w / (k + rank)
    return out


def _sims(qvec, blobs, dim):
    """Cosine of qvec against each float32 blob (all `dim` long)."""
    if _np is not None:
        q = _np.asarray(qvec, dtype=_np.float32)
        qn = float(_np.linalg.norm(q)) or 1.0
        mat = _np.frombuffer(b"".join(blobs), dtype=_np.float32).reshape(len(blobs), dim)
        rn = _np.linalg.norm(mat, axis=1); rn[rn == 0] = 1.0
        return ((mat @ q) / (rn * qn)).tolist()
    import struct
    qn = math.sqrt(sum(x * x for x in qvec)) or 1.0
    out = []
    for b in blobs:
        v = struct.unpack(f"<{dim}f", b)
        vn = math.sqrt(sum(x * x for x in v)) or 1.0
        out.append(sum(a * c for a, c in zip(qvec, v)) / (vn * qn))
    return out


def best_chunk_per_note(qvec, rows, dim):
    """rows: iterable of (chunk_id, note_id, idx, embedding_blob).
    Returns {note_id: (sim, idx, chunk_id)} — each note's best chunk.
    Rows whose blob isn't exactly dim float32s are skipped (stale/other model)."""
    good = [r for r in rows if isinstance(r[3], (bytes, bytearray, memoryview))
            and len(bytes(r[3])) == dim * 4]
    if not good:
        return {}
    sims = _sims(qvec, [bytes(r[3]) for r in good], dim)
    best = {}
    for (cid, nid, idx, _), s in zip(good, sims):
        if nid not in best or s > best[nid][0]:
            best[nid] = (s, idx, cid)
    return best


def stats(sims):
    """(mean, std) of a list of similarities."""
    n = len(sims)
    if n == 0:
        return 0.0, 0.0
    m = sum(sims) / n
    return m, math.sqrt(sum((x - m) ** 2 for x in sims) / n)


def gate(best, floor=None, margin=None, z_min=None, min_n_for_z: int = 8):
    """Notes that pass the vector gate, best first.

    floor   absolute cosine floor (None = off)
    margin  keep hits within this cosine of the top hit (None = off)
    z_min   the top hit must sit this many std-devs above the mean note
            similarity (None = off). Skipped on tiny corpora where a std-dev
            means nothing. This is what lets a relative gate still ABSTAIN on a
            nonsense query: a flat similarity profile has no standout hit.
    Returns (ordered_note_ids, mean, top).
    """
    if not best:
        return [], 0.0, 0.0
    ordered = sorted(best.items(), key=lambda kv: kv[1][0], reverse=True)
    sims = [v[0] for _, v in ordered]
    mean, std = stats(sims)
    top = sims[0]
    if z_min is not None and len(sims) >= min_n_for_z and std > 1e-9:
        if (top - mean) / std < z_min:
            return [], mean, top
    keep = []
    for nid, (s, _, _) in ordered:
        if floor is not None and s < floor:
            continue
        if margin is not None and s < top - margin:
            continue
        keep.append(nid)
    return keep, mean, top


def relevance(sim, mean, top):
    """Model-agnostic 0–1 relevance: where the hit sits between the average note
    (0) and the best note (1). Comparable across models, unlike raw cosine."""
    span = top - mean
    if span <= 1e-9:
        return 1.0 if sim >= top else 0.0
    return max(0.0, min(1.0, (sim - mean) / span))


def _trim_overlap(cur: str, nxt: str, max_k: int = 160) -> str:
    """Drop from `nxt` the head it shares with the tail of `cur` (chunk overlap)."""
    for k in range(min(max_k, len(cur), len(nxt)), 11, -1):
        if cur.endswith(nxt[:k]):
            return nxt[k:].lstrip()
    return nxt


def excerpt_from_chunks(chunks, best_idx: int, max_chars: int) -> str:
    """The evidence text for Ask: the best chunk, extended with the chunks that
    follow it (overlap removed) while the budget allows, so an answer that
    straddles a boundary still arrives whole. `chunks` = [(idx, text)] ordered."""
    by = dict(chunks)
    if best_idx not in by:
        return ""
    text = by[best_idx].strip()
    nxt = best_idx + 1
    while len(text) < max_chars * 0.6 and nxt in by:
        add = _trim_overlap(text, by[nxt].strip())
        if not add:
            break
        text = f"{text} {add}"
        nxt += 1
    if len(text) > max_chars:
        text = text[:max_chars].rsplit(" ", 1)[0] + "…"
    return text
