"""correlation.py — on-device note correlation (no network, no extra model).

Finds notes related to a given note using a lightweight lexical similarity:
TF-IDF-weighted token overlap with cosine similarity. This is deliberately
cheap — it runs on the CPU in microseconds per note pair, needs no embedding
model, and works in any language that splits on whitespace/punctuation.

The daemon uses it two ways:
  1. Per-note: when enriching a note, find its top-N related notes and inject
     deep links into the enriched body ("**Related:** …").
  2. Aggregate: build a "🔗 Note Relationships" dashboard that groups notes into
     clusters of mutual relatedness, each linking to its members.

Why lexical, not embeddings: on a phone with ~3-4 GB free and one Gemma model
already resident, adding a sentence-embedding model is costly. Lexical overlap
captures "these notes are about the same things" well enough to be useful, and
can be swapped for embeddings later behind this same interface.
"""

import re, math
from collections import Counter, defaultdict
from .logging_setup import log

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)

# Common English stopwords; for other languages the IDF weighting naturally
# down-weights frequent tokens, so this list is a light assist, not a crutch.
_STOP = set(
    "the a an and or but if then else of to in on at for with from by as is are "
    "was were be been being this that these those it its they them their we you i "
    "he she him her his my your our not no do does did done have has had will would "
    "can could should may might must about into over under again more most".split()
)


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text or "")
            if len(t) > 2 and t.lower() not in _STOP]


class Corpus:
    """Holds notes and computes pairwise similarity for relationship finding.

    Similarity backend is pluggable:
      • If an Embedder is provided AND available, similarity is SEMANTIC (cosine
        over sentence embeddings) — notes about the same idea link even with no
        shared words.
      • Otherwise it falls back to LEXICAL TF-IDF cosine over tokens.
    Either way the public methods (related/clusters/shared_terms/cohesion/
    anchor_note) are identical, so callers don't change.

    For embedding, we encode a semantic distillation of each note — title +
    tags + summary + body — rather than raw text alone, reusing the structure
    the enricher already produced when available.
    """

    def __init__(self, embedder=None):
        self._docs: dict[str, dict] = {}
        self._df: Counter = Counter()      # document frequency per token
        self._idf: dict[str, float] = {}
        self._dirty = True
        self._embedder = embedder
        self._use_embed = bool(embedder and embedder.available())
        # Vectorised-similarity cache. _version is bumped by add(); the stacked
        # embedding matrix is rebuilt only when it changes, so a full
        # relationship pass reuses one matrix across every related() call.
        self._version = 0
        self._mat = None
        self._mat_names: list[str] = []
        self._mat_key = None

    def names(self) -> list[str]:
        """All note names currently in the corpus."""
        return list(self._docs.keys())

    def add(self, name: str, title: str, text: str,
            tags: list | None = None, summary: str = "", emb: list | None = None):
        toks = tokenize(text)
        if not toks:
            return
        tf = Counter(toks)
        doc = {"title": title or "(untitled)", "tf": tf,
               # Keep tags for the confirmation guard in related(): a semantic
               # score in the uncertain band is only trusted when the two notes
               # also share a tag (see _tags_agree).
               "tags": {str(t).strip().lower().lstrip("#")
                        for t in (tags or []) if str(t).strip()}}
        if self._use_embed:
            if emb:
                # Precomputed vector (persisted in notes.embedding). Reusing it
                # skips a model inference per note — the whole point of storing
                # embeddings. The stored vector was built from the same
                # title+tags+summary+text (see QuicksilverClient._embed_text), so
                # it is interchangeable with a fresh encode().
                doc["emb"] = emb
            else:
                # Distill the note's meaning: title + tags + summary + body.
                parts = [p for p in [title, " ".join(tags or []), summary, text] if p]
                doc["emb"] = self._embedder.encode("\n".join(parts))
        self._docs[name] = doc
        self._dirty = True
        self._version += 1        # invalidates the cached embedding matrix

    def _recompute_idf(self):
        n = len(self._docs)
        self._df.clear()
        for d in self._docs.values():
            for tok in d["tf"]:
                self._df[tok] += 1
        self._idf = {
            tok: math.log((n + 1) / (df + 1)) + 1.0
            for tok, df in self._df.items()
        }
        # Precompute tf-idf vectors and norms.
        for d in self._docs.values():
            vec = {tok: (1 + math.log(cnt)) * self._idf.get(tok, 1.0)
                   for tok, cnt in d["tf"].items()}
            d["vec"] = vec
            d["norm"] = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        self._dirty = False

    def _cosine(self, a: dict, b: dict) -> float:
        va, vb = a["vec"], b["vec"]
        # iterate over the smaller vector
        if len(va) > len(vb):
            va, vb = vb, va
        dot = sum(w * vb.get(tok, 0.0) for tok, w in va.items())
        return dot / (a["norm"] * b["norm"])

    def _sim(self, a: dict, b: dict) -> float:
        """Similarity between two docs: semantic if embeddings are present on
        both, else lexical TF-IDF cosine."""
        if self._use_embed and a.get("emb") and b.get("emb"):
            from .embeddings import cosine as _emb_cos
            return _emb_cos(a["emb"], b["emb"])
        return self._cosine(a, b)

    def _tags_agree(self, a: dict, b: dict) -> bool:
        """True when two notes share at least one tag."""
        ta, tb = a.get("tags") or set(), b.get("tags") or set()
        return bool(ta & tb)

    def related(self, name: str, top_n: int = 3,
                threshold: float = 0.12,
                confirm_band: float = 0.0,
                tag_assist: float = 0.0) -> list[tuple[str, str, float]]:
        """Return up to top_n (name, title, score) most related to `name`.

        Two tag-aware adjustments, both driven by whether the notes share a tag:

        * confirm_band — a pair scoring just above `threshold` (within the band)
          is only kept when the notes ALSO share a tag. This vetoes weak-model
          false positives (a vet visit matching a blood-pressure note on generic
          "medical" vocabulary).
        * tag_assist — a pair that shares a tag may link at a LOWER score
          (threshold - tag_assist). A shared tag is corroborating evidence, so
          two #telecom bills can connect even when the embedding is lukewarm.

        Both default to 0 (pure score behaviour).
        """
        if self._dirty:
            self._recompute_idf()
        if name not in self._docs:
            return []
        target = self._docs[name]

        # Score every candidate. In semantic mode this is one vectorised op over
        # the whole corpus instead of N Python loops (see _sim_all); the scores
        # are identical either way, so the filtering below is unchanged.
        names, sims = self._sim_all(name)

        scored = []
        for other_name, s in zip(names, sims):
            other = self._docs[other_name]
            shares_tag = self._tags_agree(target, other)
            # A shared tag lowers the bar; otherwise the full threshold applies.
            floor = threshold - tag_assist if (tag_assist > 0 and shares_tag) else threshold
            if s < floor:
                continue
            # Uncertain band → require a shared tag to confirm the link.
            if confirm_band > 0 and s < threshold + confirm_band and not shares_tag:
                continue
            scored.append((other_name, other["title"], s))
        scored.sort(key=lambda x: x[2], reverse=True)
        return scored[:top_n]

    def _sim_all(self, name: str):
        """Similarity of `name` against every OTHER doc.

        Returns (names, scores) as parallel lists. Uses one batched matrix
        multiply when embeddings + numpy are available, else falls back to the
        scalar loop. Both paths must agree to within float noise — the batch
        path is an optimisation, never a behaviour change.
        """
        target = self._docs[name]
        others = [n for n in self._docs if n != name]
        if not others:
            return [], []
        if self._use_embed and target.get("emb"):
            from .embeddings import cosine_batch
            mat, mat_names = self._embed_matrix()
            if mat is not None:
                sims = cosine_batch(target["emb"], mat)
                if sims is not None:
                    # Drop the target's own row (self-similarity = 1.0).
                    out_n, out_s = [], []
                    for n, s in zip(mat_names, sims):
                        if n != name:
                            out_n.append(n); out_s.append(s)
                    return out_n, out_s
        # Scalar fallback: lexical mode, missing numpy, or a ragged corpus.
        return others, [self._sim(target, self._docs[n]) for n in others]

    def _embed_matrix(self):
        """(matrix, names) for the WHOLE corpus, cached until the corpus changes.

        Building one matrix for every doc — rather than one per query with the
        target excluded — means a full relationship pass (related() once per
        note) reuses a single matrix instead of rebuilding N of them. The
        target's own row is filtered out by the caller, which is O(n) on an
        already-materialised list and far cheaper than restacking.

        Cache is keyed on the corpus version, which add() bumps, so any change
        invalidates it.
        """
        if self._mat_key == self._version and self._mat is not None:
            return self._mat, self._mat_names
        from .embeddings import as_matrix
        names = list(self._docs.keys())
        vecs = [self._docs[n].get("emb") for n in names]
        if not all(v for v in vecs):
            self._mat, self._mat_names, self._mat_key = None, [], self._version
            return None, []
        self._mat = as_matrix(vecs)
        self._mat_names = names
        self._mat_key = self._version
        return self._mat, self._mat_names

    def clusters(self, threshold: float = 0.18) -> list[list[str]]:
        """Group notes into connected components where any pair exceeds the
        threshold. Returns a list of clusters (each a list of note names),
        largest first, singletons dropped."""
        if self._dirty:
            self._recompute_idf()
        names = list(self._docs.keys())
        # Union-Find over related pairs.
        parent = {n: n for n in names}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        # Find all above-threshold pairs. In semantic mode this is one matrix
        # multiply (X @ X.T) instead of an O(n²) Python loop of scalar cosines —
        # the same optimisation as related(), applied to clustering. For a
        # 500-note corpus that is ~one BLAS op versus 125k interpreted calls.
        pairs = self._all_pairs_above(threshold, names)
        for a, b in pairs:
            union(a, b)

        groups: dict[str, list[str]] = defaultdict(list)
        for n in names:
            groups[find(n)].append(n)
        out = [g for g in groups.values() if len(g) > 1]
        out.sort(key=len, reverse=True)
        return out

    def _all_pairs_above(self, threshold: float, names: list[str]):
        """All unordered (a, b) pairs with similarity >= threshold.

        Vectorised path: stack embeddings, compute the full similarity matrix in
        one op, and read off the upper triangle above the threshold. Falls back
        to the scalar pair loop for lexical mode / missing numpy / a ragged
        corpus — identical results either way, this is purely a speed path.
        """
        if self._use_embed and names:
            from .embeddings import HAVE_NUMPY
            if HAVE_NUMPY:
                mat, mat_names = self._embed_matrix()
                if mat is not None and len(mat_names) == len(names):
                    import numpy as _np
                    # Cosine matrix. Compute in float64 and scrub non-finite
                    # values first: real MiniLM vectors occasionally contain a
                    # zero-norm row (a note that embedded to ~zeros) or float32
                    # values that overflow when squared, which otherwise produce
                    # divide-by-zero / overflow / invalid warnings in the matmul
                    # and can yield NaN similarities. float64 + nan_to_num + an
                    # epsilon norm floor (not an exact ==0 test, which misses
                    # underflowed-but-nonzero norms) makes this bulletproof.
                    m = _np.asarray(mat, dtype=_np.float64)
                    m = _np.nan_to_num(m, nan=0.0, posinf=0.0, neginf=0.0)
                    norms = _np.linalg.norm(m, axis=1, keepdims=True)
                    norms = _np.where(norms < 1e-12, 1.0, norms)
                    unit = m / norms
                    sim = _np.clip(_np.nan_to_num(unit @ unit.T, nan=0.0), -1.0, 1.0)
                    idx = _np.argwhere(_np.triu(sim >= threshold, k=1))
                    return [(mat_names[i], mat_names[j]) for i, j in idx]
        # Scalar fallback.
        out = []
        for i, a in enumerate(names):
            da = self._docs[a]
            for b in names[i + 1:]:
                if self._sim(da, self._docs[b]) >= threshold:
                    out.append((a, b))
        return out

    def title_of(self, name: str) -> str:
        d = self._docs.get(name)
        return d["title"] if d else name

    # ── Explainability: WHY are notes grouped? ────────────────────────────────
    def shared_terms(self, names: list[str], top_k: int = 5) -> list[str]:
        """The distinctive terms common to most notes in the group — i.e. the
        actual reason they were grouped. Ranked by how many group members share
        the term, weighted by the term's corpus-wide distinctiveness (IDF)."""
        if self._dirty:
            self._recompute_idf()
        from collections import Counter
        share = Counter()
        for nm in names:
            d = self._docs.get(nm, {})
            for tok in set(d.get("tf", {})):
                share[tok] += 1
        if not share:
            return []
        scored = sorted(
            share.items(),
            key=lambda kv: (kv[1] >= 2, kv[1] * self._idf.get(kv[0], 1.0)),
            reverse=True,
        )
        # Prefer terms shared by at least two notes; fall back to top singles.
        multi = [t for t, c in scored if c >= 2]
        result = multi[:top_k] if multi else [t for t, _ in scored[:top_k]]
        return result

    def cohesion(self, names: list[str]) -> float:
        """Average pairwise similarity within the group — a 0..1 strength score
        that distinguishes a tight cluster from a loosely-linked chain."""
        if self._dirty:
            self._recompute_idf()
        pairs, total = 0, 0.0
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                if a in self._docs and b in self._docs:
                    total += self._sim(self._docs[a], self._docs[b])
                    pairs += 1
        return (total / pairs) if pairs else 0.0

    def anchor_note(self, names: list[str]) -> str:
        """The most central note in the group (highest summed similarity to the
        others) — a good representative to lead the group with."""
        if self._dirty:
            self._recompute_idf()
        best, best_score = names[0], -1.0
        for a in names:
            if a not in self._docs:
                continue
            score = sum(
                self._sim(self._docs[a], self._docs[b])
                for b in names if b != a and b in self._docs
            )
            if score > best_score:
                best, best_score = a, score
        return best

    def __len__(self):
        return len(self._docs)
