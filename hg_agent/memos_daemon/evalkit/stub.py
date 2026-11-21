"""stub.py — deterministic, dependency-free stand-ins for the embedder and LLM.

They exist so the retrieval/eval PIPELINE can be tested anywhere (CI, a laptop,
this sandbox) without a model download. They prove the plumbing — chunking,
indexing, fusion, gating, scoring — not retrieval QUALITY: quality numbers must
come from the real embedder on the device (see evalkit/__main__.py).
"""
import re, math, hashlib

_TOK = re.compile(r"\w+", re.U)


class StubEmbedder:
    """Hashing-trick bag of words + char trigrams → unit vector. Same text → same
    vector; shared words/spellings → positive cosine; unrelated → ~0."""
    def __init__(self, dim: int = 256, tag: str = "stub-hash-256", profile: dict | None = None):
        self.dim, self.tag = dim, tag
        self.profile = profile or {"query_prefix": "", "passage_prefix": "",
                                   "floor_search": 0.12, "floor_ask": 0.08,
                                   "margin": None, "z_min": None}
        self.ok = True

    def available(self) -> bool:
        return True

    def _h(self, tok: str) -> int:
        return int(hashlib.md5(tok.encode("utf-8")).hexdigest()[:8], 16) % self.dim

    def encode(self, text: str, kind: str = "passage"):
        v = [0.0] * self.dim
        for w in _TOK.findall((text or "").lower()):
            if len(w) < 2:
                continue
            v[self._h(w)] += 1.0
            pad = f"^{w}$"
            for i in range(len(pad) - 2):
                v[self._h(pad[i:i + 3])] += 0.35
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]


class StubLLM:
    """Answers extractively: quotes the first excerpt line that shares the most
    words with the question and cites it. Abstains when nothing overlaps."""
    def ask(self, question, excerpts, history=None):
        qw = {w for w in _TOK.findall(question.lower()) if len(w) > 3}
        best, bi = 0, -1
        for i, e in enumerate(excerpts):
            ew = set(_TOK.findall((e.get("text") or e.get("excerpt") or "").lower()))
            sc = len(qw & ew)
            if sc > best:
                best, bi = sc, i
        if bi < 0:
            return "I couldn't find that in your notes."
        t = (excerpts[bi].get("text") or excerpts[bi].get("excerpt") or "").strip()
        return f"{t[:200]} [{bi + 1}]\n\nSOURCES: {bi + 1}"
