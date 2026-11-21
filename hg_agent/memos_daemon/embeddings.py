"""embeddings.py — optional on-device semantic embeddings for note relationships.

This wraps a small sentence-embedding model so the relationship engine can group
notes by MEANING ("VDI latency meeting" ↔ "Zscaler rollout slowness") rather than
shared keywords. It is intentionally OPTIONAL and FAIL-SAFE:

  • If the model isn't configured, isn't installed, or fails to load, this
    returns an Embedder whose .ok is False. The Corpus then transparently falls
    back to lexical TF-IDF similarity — the agent never breaks.
  • The model is loaded LAZILY on first use, so startup cost is paid only if and
    when relationships are actually computed.

Backends (embed_backend: "auto" | "onnx" | "sentence-transformers"):
  1. onnx — onnxruntime + tokenizers, NO PyTorch. The same MiniLM weights as an
     ONNX file (~90MB) run on one CPU thread: far smaller than torch and free
     of the futex/semaphore problems torch has under proot. First use downloads
     model.onnx + tokenizer.json once into the cache dir (or point embed_onnx_dir
     at a folder that already holds them).
  2. sentence-transformers (if installed) — the original path.
  "auto" tries onnx first and falls back to sentence-transformers.

Memory note: a MiniLM-class model is ~90-130MB resident — modest next to Gemma,
but on a phone with ~3-4GB free, prefer loading it through the shared inference
service when that exists. This module is the drop-in that the service can later
back without changing the Corpus interface.
"""

import os as _os
# On Termux/Android the futex and POSIX-semaphore facilities are limited, so
# the parallel backends used by sentence-transformers/torch (loky, OpenMP) leak
# semaphores and emit "futex facility returned an unexpected error code" at
# shutdown. Pin everything to a single thread BEFORE those libraries import —
# this is both safer on-device and avoids the noisy warnings. Harmless on
# regular Linux too (embedding a handful of short notes is not CPU-bound).
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
           "NUMEXPR_NUM_THREADS", "TOKENIZERS_PARALLELISM"):
    _os.environ.setdefault(_v, "1" if _v != "TOKENIZERS_PARALLELISM" else "false")
_os.environ.setdefault("JOBLIB_MULTIPROCESSING", "0")

import math
from .logging_setup import log


# ── Model profiles ───────────────────────────────────────────────────────────
# Different embedding models need different handling, and their cosine scores are
# NOT comparable: a model can squeeze every pair into a narrow high band (the
# multilingual MiniLM tried earlier did exactly that), so an absolute threshold
# tuned for one model is meaningless for another. A profile carries what is
# model-specific:
#   query_prefix / passage_prefix  e5-family models are trained with "query: " /
#                                  "passage: " markers and degrade without them.
#   floor_search / floor_ask       absolute cosine floors (None = none).
#   margin                         keep only hits within this cosine of the best
#                                  hit (None = off). The right gate for models
#                                  with compressed score bands.
#   z_min                          keep only hits this many std-devs above the
#                                  corpus-wide mean similarity (None = off).
# The first matching substring wins. Unknown models get the conservative
# "relative" gate, because an absolute floor for an unknown model is a guess.
PROFILES = [
    ("all-minilm-l6", {"query_prefix": "", "passage_prefix": "",
                        "floor_search": 0.30, "floor_ask": 0.20,
                        "margin": None, "z_min": None}),
    ("multilingual-e5", {"query_prefix": "query: ", "passage_prefix": "passage: ",
                          "floor_search": None, "floor_ask": None,
                          "margin": 0.07, "z_min": 1.5}),
    ("e5-", {"query_prefix": "query: ", "passage_prefix": "passage: ",
              "floor_search": None, "floor_ask": None,
              "margin": 0.07, "z_min": 1.5}),
    ("bge-", {"query_prefix": "Represent this sentence for searching relevant passages: ",
               "passage_prefix": "", "floor_search": None, "floor_ask": None,
               "margin": 0.10, "z_min": 1.5}),
]
_DEFAULT_PROFILE = {"query_prefix": "", "passage_prefix": "",
                    "floor_search": None, "floor_ask": None,
                    "margin": 0.10, "z_min": 1.5}


def profile_for(model_name: str, overrides: dict | None = None) -> dict:
    name = (model_name or "").lower()
    prof = dict(_DEFAULT_PROFILE)
    for key, p in PROFILES:
        if key in name:
            prof = dict(p); break
    for k, v in (overrides or {}).items():
        if k in prof and v is not None:
            prof[k] = v
    return prof


class Embedder:
    def __init__(self, model_name: str = "", cache_dir: str = "",
                 fallback_model: str = "", overrides: dict | None = None,
                 backend: str = "auto", onnx_dir: str = "", onnx_file: str = "model.onnx"):
        self.backend = (backend or "auto").strip().lower()
        self.onnx_dir = (onnx_dir or "").strip()
        self.onnx_file = (onnx_file or "model.onnx").strip()
        self._kind = ""          # "onnx" | "st" once loaded
        self.model_name = (model_name or "").strip()
        self.fallback_model = (fallback_model or "").strip()
        self.cache_dir = cache_dir or None
        self._overrides = overrides or {}
        self._model = None
        self._tried = False
        self.ok = False          # True once a model is loaded successfully
        self._dim = 0
        self.tag = self.model_name   # the model ACTUALLY loaded (set in _ensure)
        self.profile = profile_for(self.model_name, self._overrides)

    @property
    def dim(self) -> int:
        self._ensure()
        return self._dim

    # ── lazy load ─────────────────────────────────────────────────────────────
    def _ensure(self):
        if self._tried:
            return
        self._tried = True
        if not self.model_name:
            log.info("  embeddings: no embed_model configured — using lexical "
                     "similarity for relationships.")
            return
        from_tried = []
        for name in [self.model_name] + ([self.fallback_model] if self.fallback_model
                                         and self.fallback_model != self.model_name else []):
            if self._load(name):
                if name != self.model_name:
                    log.warning(f"  embeddings: '{self.model_name}' unavailable — "
                                f"using fallback '{name}'.")
                return
            from_tried.append(name)

    # ── ONNX backend (no PyTorch) ─────────────────────────────────────────────
    def _onnx_files(self, name: str):
        """Folder holding model + tokenizer.json, downloading them once if needed."""
        import urllib.request
        base = self.onnx_dir or _os.path.join(self.cache_dir or _os.path.expanduser("~/.cache/hg_embed"),
                                              "onnx", name.replace("/", "__"))
        repo = name if "/" in name else f"sentence-transformers/{name}"
        want = [(self.onnx_file, f"onnx/{self.onnx_file}"), ("tokenizer.json", "tokenizer.json")]
        _os.makedirs(base, exist_ok=True)
        for local, remote in want:
            dst = _os.path.join(base, local)
            if _os.path.exists(dst) and _os.path.getsize(dst) > 0:
                continue
            url = f"https://huggingface.co/{repo}/resolve/main/{remote}"
            log.info(f"  embeddings: downloading {remote} (one time)…")
            tmp = dst + ".part"
            with urllib.request.urlopen(url, timeout=120) as r, open(tmp, "wb") as f:
                while True:
                    b = r.read(1 << 20)
                    if not b:
                        break
                    f.write(b)
            _os.replace(tmp, dst)
        return _os.path.join(base, self.onnx_file), _os.path.join(base, "tokenizer.json")

    def _load_onnx(self, name: str) -> bool:
        try:
            import numpy as np
            import onnxruntime as ort
            from tokenizers import Tokenizer
        except ImportError:
            log.info("  embeddings: onnxruntime/tokenizers not installed "
                     "(pip install onnxruntime tokenizers).")
            return False
        try:
            mpath, tpath = self._onnx_files(name)
            tok = Tokenizer.from_file(tpath)
            tok.enable_truncation(max_length=256)
            tok.enable_padding()
            so = ort.SessionOptions()
            so.intra_op_num_threads = 1
            so.inter_op_num_threads = 1
            sess = ort.InferenceSession(mpath, sess_options=so, providers=["CPUExecutionProvider"])
            self._onnx = {"np": np, "tok": tok, "sess": sess,
                          "inputs": {i.name for i in sess.get_inputs()}}
            self._model, self._kind, self.ok = True, "onnx", True
            self._dim = int(self._onnx_run("dimension probe").shape[-1])
            self.tag = name
            self.profile = profile_for(name, self._overrides)
            log.info(f"  embeddings: ready ({self._dim}-dim, '{name}', onnx — no PyTorch). "
                     "Relationships and search will use semantic similarity.")
            return True
        except Exception as e:
            self._model, self._kind, self.ok = None, "", False
            log.warning(f"  embeddings: onnx load of '{name}' failed: {e}.")
            return False

    def _onnx_run(self, text: str):
        o = self._onnx
        np = o["np"]
        enc = o["tok"].encode(text)
        ids = np.array([enc.ids], dtype=np.int64)
        mask = np.array([enc.attention_mask], dtype=np.int64)
        feed = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in o["inputs"]:
            feed["token_type_ids"] = np.zeros_like(ids)
        hidden = o["sess"].run(None, feed)[0]                  # [1, L, dim]
        m = mask[..., None].astype(hidden.dtype)
        vec = (hidden * m).sum(axis=1) / np.clip(m.sum(axis=1), 1e-9, None)
        vec = vec[0]
        n = float(np.linalg.norm(vec))
        return vec / n if n > 0 else vec

    def _load(self, name: str) -> bool:
        if self.backend in ("auto", "onnx") and self._load_onnx(name):
            return True
        if self.backend == "onnx":
            return False
        try:
            from sentence_transformers import SentenceTransformer
            log.info(f"  embeddings: loading '{name}' (first use)…")
            model = SentenceTransformer(name, cache_folder=self.cache_dir)
            # Method was renamed get_sentence_embedding_dimension →
            # get_embedding_dimension in newer sentence-transformers. Prefer the
            # new name, fall back to the old for older installs.
            if hasattr(model, "get_embedding_dimension"):
                dim = int(model.get_embedding_dimension())
            else:
                dim = int(model.get_sentence_embedding_dimension())
            self._model, self._dim, self.ok, self._kind = model, dim, True, "st"
            self.tag = name
            self.profile = profile_for(name, self._overrides)
            log.info(f"  embeddings: ready ({dim}-dim, '{name}'). Relationships "
                     "and search will use semantic similarity.")
            return True
        except ImportError:
            log.warning("  embeddings: sentence-transformers not installed "
                        "(pip install sentence-transformers). Falling back to "
                        "lexical similarity.")
        except Exception as e:
            log.warning(f"  embeddings: failed to load '{name}': {e}.")
        return False

    def available(self) -> bool:
        self._ensure()
        return self.ok

    # ── encoding ──────────────────────────────────────────────────────────────
    def encode(self, text: str, kind: str = "passage") -> list[float] | None:
        """Return a unit-normalized embedding vector, or None if unavailable.

        kind: "passage" (a note / chunk being indexed — the default, so existing
        callers are unchanged) or "query" (a search/Ask question). Models trained
        with asymmetric prefixes need the right one on each side."""
        self._ensure()
        if not self.ok:
            return None
        try:
            pre = self.profile.get("query_prefix" if kind == "query" else "passage_prefix", "")
            if self._kind == "onnx":
                return [float(x) for x in self._onnx_run(pre + text)]
            vec = self._model.encode(pre + text, normalize_embeddings=True)
            return [float(x) for x in vec]
        except Exception as e:
            log.debug(f"embeddings: encode failed: {e}")
            return None


# ── Embedding storage codec ──────────────────────────────────────────────────
# Embeddings persist to notes.embedding. Historically that was a JSON array of
# floats (~7.9 KB per 384-dim vector) which is both bloated (790 MB at 100k
# notes) and slow to parse (json.loads is ~200x slower than reading raw bytes).
# We now store a compact little-endian float32 BLOB (~1.5 KB), 5x smaller and
# far faster to read. decode_embedding accepts BOTH formats so old JSON rows
# keep working until re-embedded — the read path never breaks on legacy data.
import struct as _struct


def encode_embedding(vec) -> bytes:
    """Pack a float vector into a little-endian float32 blob for storage."""
    return _struct.pack(f"<{len(vec)}f", *[float(x) for x in vec])


def decode_embedding(raw):
    """Decode a stored embedding to a list[float]. Accepts the new float32 blob
    (bytes) OR a legacy JSON string. Returns None if it can't be read, so the
    caller falls back cleanly (recompute / skip).
    """
    if raw is None:
        return None
    # New format: raw bytes = float32 blob.
    if isinstance(raw, (bytes, bytearray, memoryview)):
        b = bytes(raw)
        if len(b) % 4 != 0:
            return None
        try:
            return list(_struct.unpack(f"<{len(b)//4}f", b))
        except Exception:
            return None
    # Legacy format: JSON string.
    if isinstance(raw, str):
        try:
            import json as _j
            v = _j.loads(raw)
            return [float(x) for x in v] if isinstance(v, list) else None
        except Exception:
            return None
    # Already a list (in-memory).
    if isinstance(raw, list):
        return raw
    return None


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two vectors (assumed roughly unit-norm, but we
    normalize defensively)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


# ── Vectorised similarity ────────────────────────────────────────────────────
# The scalar cosine() above is three pure-Python loops over 384 floats, run once
# per PAIR. That is the single biggest scaling ceiling in the correlation engine:
# comparing one note against N others costs N Python loops. numpy does the same
# arithmetic as one BLAS call, ~13x faster at 2k notes and the gap widens with N.
#
# numpy is optional: it arrives with sentence-transformers, but a lexical-only
# install may not have it. Same fail-safe posture as the model itself — if it is
# missing we fall back to the scalar path and nothing breaks.
try:
    import numpy as _np
    HAVE_NUMPY = True
except Exception:                                    # pragma: no cover
    _np = None
    HAVE_NUMPY = False


def as_matrix(vectors: list[list[float]]):
    """Stack unit-norm embeddings into one (N, dim) float32 matrix.

    Returns None when numpy is unavailable or the input is empty/ragged, so
    callers can fall back to the scalar path.
    """
    if not HAVE_NUMPY or not vectors:
        return None
    try:
        dim = len(vectors[0])
        if any(v is None or len(v) != dim for v in vectors):
            return None                              # ragged → not stackable
        return _np.asarray(vectors, dtype=_np.float32)
    except Exception:
        return None


def cosine_batch(query: list[float], matrix) -> list[float] | None:
    """Cosine of `query` against EVERY row of `matrix`, in one operation.

    Embeddings from Embedder.encode() are already unit-normalised
    (normalize_embeddings=True), so cosine reduces to a dot product. We still
    normalise defensively — a vector loaded from an older store, or a lexical
    vector, may not be unit-norm, and silently returning wrong scores would be
    far worse than the negligible cost of checking.
    """
    if matrix is None or not HAVE_NUMPY or not query:
        return None
    try:
        # float64 + scrub: the same hardening as the cluster matmul. Real MiniLM
        # vectors can carry a zero-norm row or float32 values that overflow when
        # squared, which produce divide-by-zero / overflow warnings and NaN
        # scores. Compute in float64, remove non-finite values, floor the norms
        # with an epsilon (not exact ==0), and clip the result to a valid cosine
        # range so a bad row can never poison the ranking.
        q = _np.asarray(query, dtype=_np.float64)
        m = _np.asarray(matrix, dtype=_np.float64)
        if q.shape[0] != m.shape[1]:
            return None
        q = _np.nan_to_num(q, nan=0.0, posinf=0.0, neginf=0.0)
        m = _np.nan_to_num(m, nan=0.0, posinf=0.0, neginf=0.0)
        qn = _np.linalg.norm(q)
        qn = qn if qn >= 1e-12 else 1.0
        rn = _np.linalg.norm(m, axis=1)
        rn = _np.where(rn < 1e-12, 1.0, rn)
        sims = _np.nan_to_num((m @ q) / (rn * qn), nan=0.0)
        return _np.clip(sims, -1.0, 1.0).tolist()
    except Exception:
        return None
