"""Ask your notes (Notes / Chat / Write): request, answer write-back, and reference checking."""
import json
import re
from flask import jsonify, request

from .core import _broadcast, _nanoid, _now, app, get_db
from .notes_common import _note_row
from .search import MAX_ASK_EXCERPT_CHARS, _parse_temporal, _retrieve


# ── API: Ask your notes (retrieval-augmented Q&A, generated on-device) ───────
# hg_ui never calls the model. It does retrieval (identical ranking to
# /api/search — same helpers, so the two can never drift apart) and writes a
# pending row; the daemon polls ask_requests directly via SQLite, same as it
# already polls note_state for dirty notes, generates locally, and POSTs the
# result to /api/ask/answer below. See schema.sql for the full flow comment.
MAX_ASK_EXCERPTS      = 5     # notes handed to the model per question
MAX_WRITE_EXCERPTS    = 14    # "write from my notes" reads more notes, and more of each
MAX_WRITE_EXCERPT_CHARS = 1400
_WRITE_NOISE = frozenset("""write build make create draft compose prepare generate
    blog article post white paper whitepaper essay report summary guide document
    tutorial based notes note treasured saved stored about around using from
    please me can could would some into""".split())

# _keyword_ids OR-matches every word in the query, stopwords included — correct
# for /api/search, where a human skims the result list and ignores noise. Ask
# hands the top hits straight to the model as ground truth, so a stopword like
# "the" pulling in an unrelated note (any question with "the" in it will
# prefix-match any note containing "the") is a real problem there in a way it
# isn't for a list a person can eyeball. Stripped only for this endpoint.
_ASK_STOPWORDS = frozenset("""a an the is are was were be been being of in on
    at to for with and or but if what when where who how why do does did
    my your his her its our their this that these those i you he she it we
    they""".split())

# Words that ask for "everything" rather than name a topic. "Summarise my notes from last week" is a request
# for the window itself: there is nothing to match against, so the window's notes ARE the answer.
_WINDOW_GENERIC = frozenset("""summarise summarize summary summaries recap overview review digest highlights
    highlight show list tell give me all everything anything notes note wrote written write writing captured
    capture saved save made make did done happened happening up down about from during any there been own
    thing things stuff more please can could would you whats what's find get see look read remind
    brief briefly short quick""".split())
MAX_WINDOW_EXCERPTS = 10      # notes handed to the model for a whole-period question
MAX_WINDOW_CHARS = 450

def _is_window_only(residual: str) -> bool:
    """True when nothing is left of the question, once the time phrase and filler words are removed."""
    words = [w for w in re.findall(r"[\w']+", (residual or "").lower())
             if w not in _ASK_STOPWORDS and w not in _WINDOW_GENERIC]
    return not words

def _ask_search_text(text: str) -> str:
    words = [w for w in re.split(r"\s+", text.lower()) if w]
    stripped = [w for w in words if w not in _ASK_STOPWORDS]
    return " ".join(stripped) if stripped else text

def _clean_ask_history(raw) -> list:
    """Validate the client-supplied conversation so far: at most the last 2
    turns, plain strings only, inline [n] markers stripped (they refer to a
    previous request's numbering and would confuse the model), lengths capped."""
    out = []
    if not isinstance(raw, list):
        return out
    for t in raw[-8:]:                      # bounded input, validated BEFORE taking the last 2
        if not isinstance(t, dict):
            continue
        q = str(t.get("question") or "").strip()[:300]
        a = re.sub(r"\s*\[\s*\d+(?:\s*[,;]\s*\d+)*\s*\]", "", str(t.get("answer") or "")).strip()[:600]
        ids = [str(x)[:32] for x in (t.get("cited_note_ids") or []) if isinstance(x, str)][:5]
        if q and a:
            out.append({"question": q, "answer": a, "cited_note_ids": ids})
    return out[-2:]

def _ask_excerpt(n: dict) -> dict:
    """Condense a note to what the daemon needs: a title (enriched title, else
    the same short-body fallback _note_row already uses for related-note
    labels) and text (the enriched summary when there is one — already
    condensed — else the raw body), trimmed to MAX_ASK_EXCERPT_CHARS."""
    enr = n.get("enrichment") or {}
    title = (enr.get("title") or "").strip()
    body = (n.get("body") or "").strip()
    if not title:
        title = (body[:48] + "…") if len(body) > 48 else (body or "Untitled note")
    text = (enr.get("summary") or "").strip() or body
    if len(text) > MAX_ASK_EXCERPT_CHARS:
        text = text[:MAX_ASK_EXCERPT_CHARS] + "…"
    return {"id": n["id"], "title": title, "text": text,
            "date": n.get("created_at"), "tags": (n.get("tags") or [])[:4]}

# ── Ask references: what each cited note actually says, and whether it backs
# the sentence that cites it. Deterministic and cheap (no model call): the
# model's [n] marker is a claim, this is the check. Lexical on purpose — it
# runs on the PWA side from data already stored (answer text + the excerpts the
# model was shown), so it works identically whether the answer arrived over SSE
# or was re-fetched, and needs no schema change.
_REF_MARK = re.compile(r"\[\s*\d+(?:\s*[,;]\s*\d+)*\s*\]")
_REF_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_ABBR = re.compile(r"\b(Dr|Mr|Mrs|Ms|Prof|St|Sr|Jr|vs|No|e\.g|i\.e)\.", re.I)

def _split_sents(text: str) -> list:
    """Sentence split that doesn't break after 'Dr.', 'Mr.', etc."""
    t = _ABBR.sub(lambda m: m.group(1) + "\x00", text or "")
    return [x.replace("\x00", ".").strip() for x in _REF_SPLIT.split(t) if x.strip()]

def _ref_words(text: str) -> dict:
    """{stem: original word} for the content words of `text`."""
    out = {}
    for w in re.findall(r"\w+", (text or "").lower()):
        if w in _ASK_STOPWORDS:
            continue
        if w.isascii():
            if len(w) < 3 and not any(ch.isdigit() for ch in w):
                continue
            stem = w if any(ch.isdigit() for ch in w) else w[:5]
        else:
            if len(w) < 2:
                continue
            stem = w[:4]            # Telugu etc.: crude suffix tolerance
        out.setdefault(stem, w)
    return out

def _ask_refs(answer: str, excerpts: list, cited_ids: list) -> list:
    """One reference per cited note, numbered by its position in `excerpts`
    (the same number the model was given). See block comment above."""
    refs = []
    if not answer or not excerpts:
        return refs
    sents = _split_sents(answer)
    for idx, ex in enumerate(excerpts):
        if ex.get("id") not in (cited_ids or []):
            continue
        n = idx + 1
        mine = [x for x in sents if re.search(r"\[[^\]]*\b%d\b[^\]]*\]" % n, x)]
        claim = " ".join(_REF_MARK.sub(" ", x) for x in (mine or sents))
        cw = _ref_words(claim)
        text = ex.get("text") or ""
        ex_stems = _ref_words((ex.get("title") or "") + " " + text)
        hit = [st for st in cw if st in ex_stems]
        score = (len(hit) / len(cw)) if cw else 0.0
        level = ("partial" if len(cw) < 2 else
                 "strong" if score >= 0.5 else "partial" if score >= 0.2 else "weak")
        # Best-matching sentence of the excerpt = the evidence snippet.
        best, best_n = "", -1
        for part in _split_sents(text) or [text]:
            k = sum(1 for st in _ref_words(part) if st in cw)
            if k > best_n:
                best, best_n = part.strip(), k
        if len(best) > 240:
            best = best[:240].rsplit(" ", 1)[0] + "…"
        sn = _ref_words(best)
        terms = [sn[st] for st in sn if st in cw][:12]
        refs.append({"n": n, "id": ex["id"], "title": ex.get("title") or "Untitled",
                     "date": ex.get("date"), "tags": ex.get("tags") or [],
                     "relevance": ex.get("score"), "snippet": best, "terms": terms,
                     "support": round(score, 2), "level": level})
    return refs

@app.route("/api/ask", methods=["POST"])
def ask_notes():
    db = get_db()
    data = request.get_json(force=True) or {}
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify({"error": "question required"}), 400
    mode = data.get("mode") if data.get("mode") in ("chat", "write") else "notes"
    question = question[:(1000 if mode == "write" else 500)]
    history = _clean_ask_history(data.get("history"))
    if mode == "chat":
        # A plain prompt to the model: no retrieval, nothing from the notes is sent.
        rid = _nanoid(12)
        db.execute("""INSERT INTO ask_requests (id, question, context_json, status, history_json, mode)
                      VALUES (?,?,?,'pending',?,'chat')""",
                   (rid, question, "[]", json.dumps(history)))
        db.commit()
        _broadcast("ask_requested", {"request_id": rid})
        return jsonify({"request_id": rid, "status": "pending", "mode": "chat", "retrieved": []})

    # Same retrieval /api/search runs: temporal window, FTS keyword hit set,
    # then semantic re-rank over that residual query.
    lo, hi, residual = _parse_temporal(question)
    search_text = residual if residual else question
    # A follow-up like "and the one after that?" has almost no searchable
    # content of its own. Borrow the previous question's words so retrieval
    # still lands on the right topic; a self-contained follow-up is left alone.
    if history and len([w for w in re.findall(r"\w+", search_text.lower())
                        if w not in _ASK_STOPWORDS]) <= 3:
        search_text = (history[-1]["question"] + " " + search_text).strip()
    time_filter, time_params = "", []
    if lo and hi:
        time_filter = " AND n.created_at >= ? AND n.created_at <= ?"
        time_params = [lo, hi]
    # Hybrid retrieval over passages: best chunk per note (vector) fused with the
    # FTS keyword order by RRF. With a working embedder, "nothing cleared the
    # gate" is an honest "no confident match" — Ask must not hand the model an
    # unrelated note as if it were the answer.
    kw = _ask_search_text(search_text)
    if mode == "write":
        # "build me a white paper based on my notes about fedora" → search on "fedora".
        topic = [w for w in kw.split() if w.strip(".,!?\"'") not in _WRITE_NOISE]
        kw = " ".join(topic) or kw
        search_text = kw
    window_total = 0
    if lo and hi and _is_window_only(residual) and not history:
        # A whole-period question ("summarise my notes from last week"): skip relevance ranking, which has
        # no topic to rank on, and hand the model the period's notes, newest first.
        rows = db.execute("SELECT id FROM notes n WHERE COALESCE(n.hidden,0)=0 AND COALESCE(n.archived,0)=0 "
                          "AND TRIM(COALESCE(n.body,''))<>'' AND n.created_at >= ? AND n.created_at <= ? "
                          "ORDER BY n.created_at DESC", (lo, hi)).fetchall()
        window_total = len(rows)
        ret = {"ids": [r["id"] for r in rows[:MAX_WINDOW_EXCERPTS]], "qvec": None, "info": {}}
    else:
        ret = _retrieve(db, search_text if search_text else None, kw, "ask", time_filter, time_params)
    ranked_ids = ret["ids"]
    qvec = ret["qvec"]
    rinfo = ret["info"]

    # Notes the previous answer relied on stay in play (≤2, most recent turn
    # first): a follow-up is usually ABOUT them, even if the new question's
    # words wouldn't rank them. They take the first slots; ranked notes fill the rest.
    carried = []
    if history:
        for nid in history[-1]["cited_note_ids"]:
            if nid not in carried and db.execute(
                    "SELECT 1 FROM notes WHERE id=? AND hidden=0", (nid,)).fetchone():
                carried.append(nid)
        carried = carried[:2]
    top_ids = carried + [i for i in ranked_ids if i not in carried]
    top_ids = top_ids[:(MAX_WRITE_EXCERPTS if mode == "write" else (MAX_WINDOW_EXCERPTS if window_total else MAX_ASK_EXCERPTS))]
    rid = _nanoid(12)

    if not top_ids:
        # Nothing plausible in the corpus — answer immediately, no model call,
        # no battery spent on a near-certain miss.
        if mode == "write":
            answer = "I couldn't find notes on that topic to write from."
        elif lo and hi and _is_window_only(residual):
            answer = "You didn't write any notes in that period."
        elif lo and hi:
            answer = "No notes from that period seem to answer that."
        else:
            answer = "Nothing in your notes seems to answer that."
        db.execute("""INSERT INTO ask_requests
                      (id, question, context_json, status, answer, cited_note_ids, answered_at, mode)
                      VALUES (?,?,?,?,?,?,?,?)""",
                   (rid, question, "[]", "done", answer, "[]", _now(), mode))
        db.commit()
        return jsonify({"request_id": rid, "status": "done",
                         "answer": answer, "cited_note_ids": []})

    excerpts = []
    for nid in top_ids:
        row = db.execute("SELECT * FROM notes WHERE id=? AND COALESCE(hidden,0)=0",
                         (nid,)).fetchone()
        if row:
            ex = _ask_excerpt(_note_row(row))
            if mode == "write":
                full = (row["body"] or "").strip()
                ex["text"] = full[:MAX_WRITE_EXCERPT_CHARS] + ("…" if len(full) > MAX_WRITE_EXCERPT_CHARS else "")
            ri = rinfo.get(nid) or {}
            # The best-matching PASSAGE is the evidence, not the note's summary
            # or its first 700 chars: the answer to a question about a long note
            # is rarely at the top of it. Notes with no chunk row (not indexed
            # yet, keyword-only) keep the old summary/body excerpt.
            if window_total and len(ex["text"]) > MAX_WINDOW_CHARS:
                ex["text"] = ex["text"][:MAX_WINDOW_CHARS] + "…"
            if ri.get("chunk_text") and mode != "write":
                ex["text"] = ri["chunk_text"]
                ex["passage"] = True
            if isinstance(ri.get("score"), (int, float)):
                ex["score"] = round(min(1.0, max(0.0, float(ri["score"]))), 3)
            excerpts.append(ex)

    model_q = question
    if window_total and mode != "write":
        # The model sees only excerpts, never dates, so "last week" reads as missing information.
        model_q = (question.rstrip() + "\n(Every note above was written in the period asked about, so the period "
                   "is already satisfied. Summarise what these notes cover, grouped by theme.)")
    db.execute("""INSERT INTO ask_requests (id, question, context_json, status, history_json, mode)
                  VALUES (?,?,?,'pending',?,?)""",
               (rid, model_q, json.dumps(excerpts), json.dumps(history), mode))
    db.commit()
    _broadcast("ask_requested", {"request_id": rid})
    return jsonify({"request_id": rid, "status": "pending", "mode": mode,
                    "window": {"total": window_total, "shown": len(excerpts)} if window_total > len(excerpts) else None,
                    "retrieved": [{k: v for k, v in e.items() if k != "text"} if mode == "write" else e
                                  for e in excerpts]})

@app.route("/api/ask/<rid>", methods=["GET"])
def get_ask(rid):
    """Resilience path: if the client's SSE connection dropped the push (or the
    app was backgrounded when it arrived), it can re-fetch the request by id."""
    db = get_db()
    row = db.execute("SELECT * FROM ask_requests WHERE id=?", (rid,)).fetchone()
    if not row:
        return jsonify({"error": "not found"}), 404
    return jsonify({
        "request_id": rid, "question": row["question"], "status": row["status"],
        "mode": row["mode"] if "mode" in row.keys() else "notes",
        "answer": row["answer"], "error": row["error"],
        "cited_note_ids": json.loads(row["cited_note_ids"] or "[]"),
        "retrieved": json.loads(row["context_json"] or "[]"),
        "refs": _ask_refs(row["answer"], json.loads(row["context_json"] or "[]"),
                          json.loads(row["cited_note_ids"] or "[]")),
    })

@app.route("/api/ask/answer", methods=["POST"])
def ask_answer():
    """Daemon write-back after local generation — same shape as /api/webhook:
    persist the result, then broadcast so any open PWA client updates live."""
    db = get_db()
    data = request.get_json(force=True) or {}
    rid = data.get("request_id")
    if not rid:
        return jsonify({"error": "request_id required"}), 400
    if not db.execute("SELECT 1 FROM ask_requests WHERE id=?", (rid,)).fetchone():
        return jsonify({"error": "request not found"}), 404

    status = data.get("status", "done")
    answer = data.get("answer")
    cited  = data.get("cited_note_ids") or []
    error  = data.get("error")
    db.execute("""UPDATE ask_requests
                  SET status=?, answer=?, cited_note_ids=?, error=?, answered_at=?
                  WHERE id=?""",
               (status, answer, json.dumps(cited), error, _now(), rid))
    db.commit()
    _ctx = json.loads(db.execute("SELECT context_json FROM ask_requests WHERE id=?",
                                 (rid,)).fetchone()["context_json"] or "[]")
    _broadcast("ask_answered", {
        "request_id": rid, "status": status, "answer": answer,
        "cited_note_ids": cited, "error": error,
        "refs": _ask_refs(answer, _ctx, cited),
    })
    return jsonify({"ok": True, "request_id": rid})
