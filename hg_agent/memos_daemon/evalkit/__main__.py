"""python -m memos_daemon.evalkit — on-device evaluation of Quicksilver's
retrieval and Ask pipeline.

WHY: every knob in retrieval (embedding model, gate thresholds, chunk size) was
tuned by eye on one person's notes. This turns "does it feel better?" into
numbers you can compare before/after a change, ON THE PHONE, with the real
model and the real code path:

  retrieval mode (default)   golden notes → real chunking + real embedder → the
                             app's real /api/ask retrieval → recall@k, MRR,
                             abstention, hidden-note leaks, passage hits.
  --llm gemma                additionally runs the real local Gemma on what was
                             retrieved, then the real citation parser: marker
                             rate, citation precision, abstention correctness.
  --sweep                    sweeps the gate (floor, or margin × z) and prints
                             the recall / abstention trade-off.
  --compare-models A,B,...   same suite across embedding models, side by side.
  --compare baseline.json    delta vs a saved scorecard; non-zero exit on a
                             regression (usable as a pre-deploy gate).

Examples (from the hg_agent folder):
  python -m memos_daemon.evalkit --out base.json
  python -m memos_daemon.evalkit --model intfloat/multilingual-e5-small --compare base.json
  python -m memos_daemon.evalkit --compare-models all-MiniLM-L6-v2,intfloat/multilingual-e5-small
  python -m memos_daemon.evalkit --llm gemma --out full.json
  python -m memos_daemon.evalkit --sweep

--embedder stub / --llm stub run the same pipeline with deterministic stand-ins
(CI, laptops). They validate the PLUMBING; their numbers say nothing about
real-model quality.
"""
import argparse, importlib.util, json, os, re, statistics, sys, tempfile, time, shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
ABSTAIN_RE = re.compile(r"(couldn'?t find|could not find|not found|no mention|not mentioned|"
                        r"doesn'?t (say|mention|include|contain)|nothing in your notes|"
                        r"isn'?t (in|mentioned)|no information|don'?t see|not in your notes)", re.I)


# ── world construction ───────────────────────────────────────────────────────
def find_ui(arg: str | None) -> Path:
    for c in [arg, os.environ.get("HG_UI_PATH"), HERE.parents[2] / "hg_ui",
              HERE.parents[1] / "hg_ui", Path("/tmp/hg_ui")]:
        if c and (Path(c) / "app.py").exists():
            return Path(c)
    sys.exit("evalkit: can't find hg_ui (pass --ui-path or set HG_UI_PATH)")


def load_ui(ui_path: Path, db_path: str):
    os.environ["QS_DB"] = db_path
    sys.path.insert(0, str(ui_path))
    name = "qs_app_eval"
    sys.modules.pop(name, None)
    for k in [k for k in sys.modules if k == "server" or k.startswith("server.")]:
        del sys.modules[k]                  # the UI's server package: fresh app + config per load
    spec = importlib.util.spec_from_file_location(name, ui_path / "app.py")
    ui = importlib.util.module_from_spec(spec)
    sys.modules[name] = ui
    spec.loader.exec_module(ui)
    ui.init_db()
    return ui


def make_embedder(kind: str, model: str, fallback: str, cache_dir: str):
    if kind == "stub":
        from .stub import StubEmbedder
        return StubEmbedder()
    from ..embeddings import Embedder
    e = Embedder(model, cache_dir, fallback_model=fallback)
    if not e.available():
        sys.exit(f"evalkit: embedding model '{model}' could not be loaded "
                 "(is sentence-transformers installed and the model downloaded?)")
    return e


class World:
    """A throwaway app + DB seeded with the golden notes and indexed by `emb`."""
    def __init__(self, golden: dict, emb, ui_path: Path, chunk=(500, 80, 40), keep=False):
        self.tmp = tempfile.mkdtemp(prefix="qs_eval_")
        self.keep = keep
        self.db_path = os.path.join(self.tmp, "eval.db")
        self.ui = load_ui(ui_path, self.db_path)
        from ..qs_client import QuicksilverClient
        self.emb = emb
        self.qc = QuicksilverClient(db_path=self.db_path, webhook_url="")
        self.qc.embedder = emb
        self.qc.CHUNK_PARAMS = chunk
        self.chunk = chunk
        db = self.qc._db()
        for n in golden["notes"]:
            db.execute("INSERT INTO notes (id, body, hidden) VALUES (?,?,?)",
                       (n["id"], n["body"], 1 if n.get("hidden") else 0))
            db.execute("INSERT OR IGNORE INTO note_state (note_id, status) VALUES (?, 'approved')", (n["id"],))
            for t in n.get("tags", []):
                db.execute("INSERT OR IGNORE INTO tags (note_id, tag) VALUES (?,?)", (n["id"], t))
            if n.get("title"):
                db.execute("INSERT INTO enrichments (id, note_id, title, summary) VALUES (?,?,?,?)",
                           (f"e_{n['id']}", n["id"], n["title"], n.get("summary", "")))
        db.commit()
        t0 = time.time()
        self.chunks_total = 0
        for n in golden["notes"]:
            self.chunks_total += max(0, self.qc.index_note_chunks(n["id"], force=True))
        self.index_s = time.time() - t0
        self.set_profile(dict(emb.profile))
        ui = self.ui
        # in-process stand-in for the sidecar; the function lives in ui.search (older UIs: on ui)
        setattr(getattr(ui, "search", ui), "_embed_query_info", lambda text: {
            "embedding": [float(x) for x in emb.encode(text, kind="query")],
            "model": emb.tag, "dim": emb.dim})
        self.client = ui.app.test_client()

    def set_profile(self, prof: dict):
        self.qc.set_embed_active({"model": self.emb.tag, "dim": self.emb.dim,
            "profile": {k: prof.get(k) for k in ("floor_search", "floor_ask", "margin", "z_min")}})

    def set_floor_pref(self, v: float):
        db = self.qc._db()
        db.execute("INSERT INTO settings (key,value) VALUES ('user_prefs',?) ON CONFLICT(key) "
                   "DO UPDATE SET value=excluded.value",
                   (json.dumps({"ask_min_similarity": v, "search_min_similarity": v}),))
        db.commit()

    def close(self):
        if not self.keep:
            shutil.rmtree(self.tmp, ignore_errors=True)


# ── running ──────────────────────────────────────────────────────────────────
def run_suite(world: World, golden: dict, llm=None, parse=None, log=print) -> list:
    out, by_id = [], {}
    for q in golden["questions"]:
        history = []
        if q.get("follow_of") and q["follow_of"] in by_id:
            prev = by_id[q["follow_of"]]
            history = [{"question": prev["q"], "answer": prev.get("answer") or "(answer)",
                        "cited_note_ids": prev.get("cited") or prev["ids"][:1]}]
        t0 = time.perf_counter()
        resp = world.client.post("/api/ask", json={"question": q["q"], "history": history}).get_json()
        ret_ms = (time.perf_counter() - t0) * 1000
        retrieved = resp.get("retrieved", []) or []
        rec = {"id": q["id"], "q": q["q"], "kind": q["kind"], "lang": q["lang"],
               "expect": q["expect"], "phrases": q["phrases"],
               "answer_present": q.get("answer_present", True),
               "ids": [e["id"] for e in retrieved], "ret_ms": round(ret_ms, 1),
               "texts": {e["id"]: e.get("text", "") for e in retrieved},
               "scores": {e["id"]: e.get("score") for e in retrieved},
               "passage": {e["id"]: bool(e.get("passage")) for e in retrieved}}
        if llm is not None and resp.get("status") == "pending":
            req = world.qc.get_ask_request(resp["request_id"])
            t1 = time.perf_counter()
            raw = llm.ask(req["question"], req["context"], history=req.get("history"))
            rec["llm_s"] = round(time.perf_counter() - t1, 2)
            ans, cited = parse(raw, req["context"], limit=getattr(llm, "_ask_kept", None))
            rec["answer"], rec["cited"] = ans, cited
        elif llm is not None:
            rec["answer"], rec["cited"] = resp.get("answer", ""), []
        by_id[q["id"]] = rec
        out.append(rec)
    return out


def _pct(xs):
    return round(100 * sum(xs) / len(xs), 1) if xs else None


def score(results: list, hidden_ids: set, full: bool) -> dict:
    ans = [r for r in results if r["kind"] in ("answer", "followup") and r["expect"]]
    abst = [r for r in results if r["kind"] == "abstain"]
    def rank(r):
        for i, nid in enumerate(r["ids"], 1):
            if nid in r["expect"]:
                return i
        return 0
    for r in ans:
        r["rank"] = rank(r)
        ph = [p.lower() for p in r["phrases"]]
        r["passage_hit"] = bool(ph) and any(
            any(p in (r["texts"].get(nid) or "").lower() for p in ph)
            for nid in r["ids"] if nid in r["expect"])
    s = {
        "n_answerable": len(ans), "n_abstain": len(abst),
        "recall@1": _pct([r["rank"] == 1 for r in ans]),
        "recall@3": _pct([0 < r["rank"] <= 3 for r in ans]),
        "recall@5": _pct([0 < r["rank"] <= 5 for r in ans]),
        "mrr": round(sum(1 / r["rank"] for r in ans if r["rank"]) / len(ans), 3) if ans else None,
        "passage_hit": _pct([r["passage_hit"] for r in ans if r["phrases"]]),
        "abstain_at_retrieval": _pct([not r["ids"] for r in abst]),
        "hidden_leaks": sum(1 for r in results if hidden_ids & set(r["ids"])),
        "retrieval_ms_p50": round(statistics.median(r["ret_ms"] for r in results), 1),
        "retrieval_ms_p95": round(sorted(r["ret_ms"] for r in results)[max(0, int(len(results) * .95) - 1)], 1),
    }
    groups = {}
    for r in ans:
        g = "cross-lingual" if "→" in r["lang"] else r["lang"]
        groups.setdefault(g, []).append(r)
    s["by_lang_recall@5"] = {g: _pct([0 < r["rank"] <= 5 for r in rs]) for g, rs in groups.items()}
    fu = [r for r in ans if r["kind"] == "followup"]
    s["followup_recall@5"] = _pct([0 < r["rank"] <= 5 for r in fu]) if fu else None
    if full:
        has = [r for r in ans if r.get("answer_present", True)]
        marked = [bool(re.search(r"\[\d+\]", r.get("answer") or "")) for r in has]
        s["marker_rate"] = _pct(marked)
        prec, hit = [], []
        for r in has:
            c = r.get("cited") or []
            if c:
                prec.append(len(set(c) & set(r["expect"])) / len(set(c)))
            hit.append(bool(set(c) & set(r["expect"])))
        s["cite_precision"] = round(100 * sum(prec) / len(prec), 1) if prec else None
        s["cite_recall"] = _pct(hit)
        s["answer_has_phrase"] = _pct([any(p.lower() in (r.get("answer") or "").lower() for p in r["phrases"])
                                       for r in has if r["phrases"]])
        absent = [r for r in results if r["kind"] == "abstain" or not r.get("answer_present", True)]
        def abstained(r): return (not r.get("cited")) or bool(ABSTAIN_RE.search(r.get("answer") or ""))
        s["abstain_correct"] = _pct([abstained(r) for r in absent])
        s["false_abstain"] = _pct([bool(ABSTAIN_RE.search(r.get("answer") or "")) and not r.get("cited")
                                   for r in has])
        ls = [r["llm_s"] for r in results if "llm_s" in r]
        if ls:
            s["llm_s_p50"] = round(statistics.median(ls), 2)
            s["llm_s_p95"] = round(sorted(ls)[max(0, int(len(ls) * .95) - 1)], 2)
    return s


def sweep(world: World, golden: dict, hidden_ids: set) -> list:
    prof = dict(world.emb.profile)
    rows = []
    if prof.get("floor_search") is not None:
        grid = [{"floor_search": f, "floor_ask": f} for f in (0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50)]
    else:
        grid = [{"margin": m, "z_min": z} for m in (0.03, 0.05, 0.07, 0.10, 0.15) for z in (None, 1.0, 1.5, 2.0)]
    for g in grid:
        p = {**prof, **g}
        world.set_profile(p)
        if "floor_ask" in g:
            world.set_floor_pref(g["floor_ask"])
        sc = score(run_suite(world, golden), hidden_ids, False)
        rows.append({**g, "recall@5": sc["recall@5"], "mrr": sc["mrr"],
                     "abstain": sc["abstain_at_retrieval"], "leaks": sc["hidden_leaks"]})
    world.set_profile(prof)
    return rows


# ── reporting ────────────────────────────────────────────────────────────────
HIGHER_BETTER = ["recall@1", "recall@3", "recall@5", "mrr", "passage_hit", "abstain_at_retrieval",
                 "marker_rate", "cite_precision", "cite_recall", "answer_has_phrase", "abstain_correct",
                 "followup_recall@5"]
LOWER_BETTER = ["hidden_leaks", "false_abstain"]


def print_card(card: dict, base: dict | None = None):
    m, s = card["meta"], card["summary"]
    print(f"\n── {card['suite']}  ·  model {m['model']} ({m['dim']}-dim)  ·  llm {m['llm']}  ·  "
          f"{m['chunks']} chunks, index {m['index_s']}s")
    if m["embedder"] == "stub" or m["llm"] == "stub":
        print("   ⚠ stand-in components: this validates the pipeline, NOT retrieval quality.")
    bs = (base or {}).get("summary", {})
    regress = []
    def line(k):
        v = s.get(k)
        if v is None:
            return
        d = ""
        if k in bs and bs[k] is not None:
            delta = v - bs[k]
            if abs(delta) > 1e-9:
                good = (delta > 0) == (k in HIGHER_BETTER)
                d = f"   {'▲' if delta > 0 else '▼'} {delta:+.3g} vs baseline" + ("" if good else "  ← worse")
                if not good and abs(delta) >= (0.05 if k == "mrr" else 5 if k not in LOWER_BETTER else 1):
                    regress.append(k)
        print(f"   {k:<24}{v}{d}")
    for k in HIGHER_BETTER + LOWER_BETTER + ["retrieval_ms_p50", "retrieval_ms_p95", "llm_s_p50", "llm_s_p95"]:
        line(k)
    if s.get("by_lang_recall@5"):
        print("   recall@5 by language    " + "  ".join(f"{g}={v}" for g, v in s["by_lang_recall@5"].items()))
    misses = [r for r in card["questions"] if r["kind"] != "abstain" and r["expect"] and not (0 < r.get("rank", 0) <= 5)]
    if misses:
        print("   missed: " + "; ".join(f"{r['id']} '{r['q'][:38]}'" for r in misses))
    leaks = [r for r in card["questions"] if r.get("leak")]
    return regress


def main(argv=None):
    ap = argparse.ArgumentParser(prog="evalkit", description=__doc__.split("\n\n")[0])
    ap.add_argument("--suite", default=str(HERE / "golden.json"))
    ap.add_argument("--ui-path"); ap.add_argument("--config", help="agent config json (for model/model_path)")
    ap.add_argument("--embedder", choices=["real", "stub"], default="real")
    ap.add_argument("--model", help="embedding model (default: config embed_model)")
    ap.add_argument("--fallback", default="")
    ap.add_argument("--llm", choices=["none", "stub", "gemma"], default="none")
    ap.add_argument("--chunk-chars", type=int, default=500); ap.add_argument("--chunk-overlap", type=int, default=80)
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--compare-models"); ap.add_argument("--compare"); ap.add_argument("--out")
    ap.add_argument("--keep", action="store_true", help="keep the temp DB for inspection")
    ap.add_argument("--verbose", "-v", action="store_true")
    a = ap.parse_args(argv)

    golden = json.load(open(a.suite, encoding="utf-8"))
    ui_path = find_ui(a.ui_path)
    cfg = {}
    if a.config and os.path.exists(a.config):
        cfg = json.load(open(a.config))
    from ..config import DEFAULT_CONFIG as _D
    try:
        base_cfg = dict(_D)
    except Exception:
        base_cfg = {}
    base_cfg.update(cfg)
    model = a.model or base_cfg.get("embed_model") or "all-MiniLM-L6-v2"
    hidden_ids = {n["id"] for n in golden["notes"] if n.get("hidden")}
    chunk = (a.chunk_chars, a.chunk_overlap, 40)

    def one(model_name):
        emb = make_embedder(a.embedder, model_name, a.fallback, base_cfg.get("embed_cache_dir", ""))
        world = World(golden, emb, ui_path, chunk, keep=a.keep)
        llm = parse = None
        if a.llm == "stub":
            from .stub import StubLLM
            llm = StubLLM()
        elif a.llm == "gemma":
            from ..engine import GemmaEngine
            llm = GemmaEngine(base_cfg["model_path"], cfg=base_cfg)
        if llm is not None:
            from ..daemon import _parse_ask_answer as parse
        results = run_suite(world, golden, llm, parse)
        sc = score(results, hidden_ids, llm is not None)
        for r in results:
            r["leak"] = bool(hidden_ids & set(r["ids"]))
        card = {"suite": golden["name"], "summary": sc, "questions": results,
                "meta": {"model": emb.tag, "dim": emb.dim, "embedder": a.embedder, "llm": a.llm,
                         "chunks": world.chunks_total, "index_s": round(world.index_s, 2),
                         "chunk": list(chunk), "date": time.strftime("%Y-%m-%d %H:%M")}}
        if a.sweep:
            card["sweep"] = sweep(world, golden, hidden_ids)
        world.close()
        return card

    if a.compare_models:
        cards = [one(m.strip()) for m in a.compare_models.split(",") if m.strip()]
        keys = HIGHER_BETTER[:6] + ["hidden_leaks", "retrieval_ms_p50"]
        print(f"\n{'metric':<24}" + "".join(f"{c['meta']['model'][-28:]:>30}" for c in cards))
        for k in keys:
            print(f"{k:<24}" + "".join(f"{str(c['summary'].get(k)):>30}" for c in cards))
        for c in cards:
            print(f"  recall@5 by language [{c['meta']['model'][-24:]}]: {c['summary']['by_lang_recall@5']}")
        if a.out:
            json.dump(cards, open(a.out, "w"), ensure_ascii=False, indent=1)
        return 0

    card = one(model)
    base = json.load(open(a.compare)) if a.compare else None
    regress = print_card(card, base)
    if card.get("sweep"):
        print("\n   gate sweep (recall@5 vs abstention; leaks must stay 0):")
        for r in card["sweep"]:
            print("   " + "  ".join(f"{k}={v}" for k, v in r.items()))
    if a.verbose:
        for r in card["questions"]:
            print(f"   {r['id']} rank={r.get('rank','-')} ids={r['ids']} "
                  + (f"cited={r.get('cited')} ans={str(r.get('answer'))[:70]!r}" if 'answer' in r else ""))
    if a.out:
        json.dump(card, open(a.out, "w"), ensure_ascii=False, indent=1)
        print(f"\n   scorecard → {a.out}")
    if regress:
        print(f"\n   ✗ regression in: {', '.join(regress)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
