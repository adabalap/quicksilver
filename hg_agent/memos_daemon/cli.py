import sys, json, argparse
from pathlib import Path

from .logging_setup import log, setup_logging
from .config import load_config, DEFAULT_CONFIG, CONFIG_PATH
from .state import StateStore
from .memos_client import MemosClient
from .thermal import ThermalGuard
from .engine import GemmaEngine
from .daemon import MemosDaemon

def cmd_run(args, cfg):
    if args.dry_run:
        cfg["dry_run"] = True
    MemosDaemon(cfg).run()


def cmd_enrich(args, cfg):
    text = args.text or sys.stdin.read()
    e = GemmaEngine(cfg["model_path"])
    print(json.dumps(e.enrich(text), indent=2, ensure_ascii=False))
    e.close()


def cmd_status(args, cfg):
    st = StateStore(cfg["state_file"])
    print("── stats ──")
    print(json.dumps(st.stats(), indent=2))
    print("── metrics ──")
    print(json.dumps(st.metrics(), indent=2))
    print("watermark:", st.watermark or "(none)")
    print("dashboard:", st.dashboard_id or "(none)")
    print("connections:", st.connections_id or "(none)")
    try:
        from .pending import PendingWrites
        pw = PendingWrites(cfg.get("pending_writes_file") or (cfg["state_file"] + ".pending"))
        if len(pw):
            print(f"pending writes: {len(pw)} (queued for retry when the backend returns)")
        else:
            print("pending writes: 0")
    except Exception:
        pass
    print("Memos API:", "reachable" if MemosClient(
        cfg["memos_base_url"], cfg["memos_token"]).ping() else "UNREACHABLE")
    t = ThermalGuard(cfg).read_celsius()
    print("SoC temp:", f"{t:.1f}°C" if t is not None else "(unavailable)")


def cmd_brief_check(args, cfg):
    """Why is (or isn't) something in Medha's Now brief and notifications? Prints the settings, what Medha says it
    supports, what the agent would post right now and why each item does or doesn't notify."""
    import sqlite3, time as _t
    from datetime import datetime
    from . import medha_brief as mb, sms
    ok = lambda b: "yes" if b else "NO"
    base, tok = (cfg.get("medha_url") or "").strip(), (cfg.get("medha_token") or "").strip()
    print("── settings ──")
    print(f"medha_brief: {cfg.get('medha_brief')}   medha_url: {base or '(blank)'}   medha_token: {'set' if tok else 'MISSING'}")
    print(f"notify: {cfg.get('medha_brief_notify')} (max {cfg.get('medha_brief_notify_max')}, days {cfg.get('medha_brief_notify_days')} "
          f"= {'due today or late only' if int(cfg.get('medha_brief_notify_days', 0)) == 0 else 'due within that many days'})")
    print(f"sources: bills={cfg.get('medha_brief_bills')} reminders={cfg.get('medha_brief_reminders')} "
          f"followups={cfg.get('medha_brief_followups')} actions={cfg.get('medha_brief_actions')}   link: {cfg.get('medha_brief_link') or '(blank, so no tap-through address)'}")
    client = None
    if base and tok:
        client = mb.MedhaBrief(base, tok)
        print("── Medha ──")
        try:
            print("capabilities:", json.dumps(client.capabilities()))
        except mb.BriefError as e:
            hint = {401: "Medha doesn't know this token", 403: "this token's client needs the 'notify' capability"}.get(e.status, "")
            print(f"capabilities call failed: {e.status} {e.code} {e} {('· ' + hint) if hint else ''}")
        try:
            cur = client.read()
            print(f"brief widget now holds {len(cur.get('items') or [])} item(s)")
        except mb.BriefError as e:
            print(f"couldn't read the widget: {e.status} {e}")
    else:
        print("Nothing can be posted: set medha_url and medha_token (Medha, Copy API token).")
    dbp = cfg.get("db_path")
    if not dbp:
        print("db_path is not set; can't list what would be posted."); return
    db = sqlite3.connect(str(Path(dbp).expanduser()), timeout=10); db.row_factory = sqlite3.Row
    sms.ensure(db)
    try:
        sms_on = (db.execute("SELECT value FROM settings WHERE key='sms'").fetchone() or ["off"])[0] == "on"
    except Exception:
        sms_on = False
    cands = mb.gather(db, datetime.now(), app_base=(cfg.get("medha_brief_link") or "").strip(),
                      rem_back=int(cfg.get("medha_brief_reminder_back_days", 7)), rem_ahead=int(cfg.get("medha_brief_reminder_ahead_days", 14)),
                      actions_max=int(cfg.get("medha_brief_actions_max", 25)), bills=bool(cfg.get("medha_brief_bills", True)),
                      manual_bills_only=not sms_on, reminders=bool(cfg.get("medha_brief_reminders", True)),
                      followups=bool(cfg.get("medha_brief_followups", True)), actions=bool(cfg.get("medha_brief_actions", True)))
    items = mb.pick(cands, {}, limit=max(3, int(cfg.get("medha_brief_max_items", 50))), app_base=(cfg.get("medha_brief_link") or "").strip())
    days_lim = int(cfg.get("medha_brief_notify_days", 0)); notif_max = int(cfg.get("medha_brief_notify_max", 3))
    wanted = {c["key"] for c in mb.notify_wanted(cands, notif_max, days_lim)}
    print(f"── candidates ({len(cands)}; the brief shows the top {len(items)}) ──")
    if not cands:
        print("Nothing needs you right now: no open bills, dated reminders in range, stalled tasks or open actions.")
    for c in sorted(cands, key=lambda c: -c["score"]):
        if c["key"] in wanted:
            why = "NOTIFIES"
        elif c["kind"] not in ("bill", "event", "date"):
            why = f"no notification: a '{c['kind']}' item (only bills and dated items notify)"
        elif c.get("days", 99) > days_lim:
            why = f"no notification yet: due in {c.get('days')} day(s); medha_brief_notify_days is {days_lim}"
        else:
            why = f"no notification: over the limit of {notif_max}"
        print(f"  [{c['kind']}] {c['title'][:50]:50} days={c.get('days','-')}  score={c['score']}  → {why}")
    st = json.loads(sms._get_setting(db, "medha_notif_state", "{}") or "{}")
    print(f"── already posted ──\nnotifications held: {len(st)}   last brief hash: {(sms._get_setting(db, 'medha_brief_hash', '') or '(never posted)')[:12]}")
    if args.test and client:
        print("── test ──")
        try:
            r = client.notify("hg-test", "Quicksilver test", "If you can see this, Medha notifications work.", silent=True)
            print("Medha answered:", json.dumps(r))
            _t.sleep(8); client.notify_cancel("hg-test"); print("(test notification removed)")
        except mb.BriefError as e:
            print(f"test failed: {e.status} {e.code} {e}")


def cmd_medha_check(args, cfg):
    """Pass/fail table for a Medha build: text, token cap, photos, sketches, voice (English and Telugu),
    and clean refusals. Photo/voice checks need sample files (--image, --sketch, --telugu-image,
    --audio-en, --audio-te); without one they're SKIPPED, never failed. Exit code 1 if anything fails."""
    import time, difflib, requests
    from .medha_engine import MedhaEngine
    cfg = dict(cfg); cfg["medha_multimodal"] = True          # test the endpoint itself, not the capability gate
    cfg["medha_busy_wait_s"] = 5
    eng = MedhaEngine(cfg)
    rows = []                                                 # (name, PASS|FAIL|SKIP, detail, seconds)
    def run(name, fn, skip_if=None):
        if skip_if:
            rows.append((name, "SKIP", skip_if, 0.0)); return None
        t = time.monotonic()
        try:
            ok, detail = fn()
            rows.append((name, "PASS" if ok else "FAIL", detail, time.monotonic() - t))
            return ok
        except Exception as e:
            rows.append((name, "FAIL", f"{type(e).__name__}: {str(e)[:150]}", time.monotonic() - t)); return False
    def one(s, n=90):
        s = " ".join((s or "").split()); return s if len(s) <= n else s[:n - 1] + "…"

    h = eng.health()
    caps = (h or {}).get("capabilities") or {}
    run("health", lambda: (h is not None, f"status={h.get('status')} backend={h.get('backend')} "
                           f"model_loaded={h.get('modelLoaded')} caps={caps or 'not reported'}") if h else (False, "unreachable at " + eng._url))
    if h is None:
        return _check_report(rows, None, eng)
    run("text generate", lambda: (lambda t: (len(t.strip()) > 0, one(t)))(
        eng._generate([], "Reply with one short sentence about the sea.", cap=60)))
    def cap_check():
        t = eng._generate([], "Count from 1 to 400, separated by spaces.", cap=24)
        n = len(t.split())
        return (0 < n < 60, f"asked for 400 numbers with max_tokens=24 → got {n} words")
    run("max_tokens is a real cap", cap_check)
    run("Telugu text", lambda: (lambda t: (any("\u0c00" <= ch <= "\u0c7f" for ch in t), one(t)))(
        eng._generate([], "తెలుగులో ఒక చిన్న వాక్యం రాయండి: సముద్రం గురించి.", cap=80)))
    from .media import LiteRTBackend
    lb = LiteRTBackend(eng)                                   # the same prompts the agent uses for real photos
    def img(path, sketch, want_text=False):
        r = lb.analyze(path, sketch=sketch)
        if not r:
            return (False, "no usable reply")
        if want_text:
            return (any("\u0c00" <= ch <= "\u0c7f" for ch in r["text"]), f"text: {one(r['text'])}")
        return (bool(r["description"]), f"{one(r['description'], 70)}" + (f" · text: {one(r['text'], 40)}" if r["text"] else ""))
    run("1 photo → description + text", lambda: img(args.image, False), None if args.image else "no --image given")
    run("2 sketch → described", lambda: img(args.sketch, True), None if args.sketch else "no --sketch given")
    run("5 Telugu text in a photo", lambda: img(args.telugu_image, False, True), None if args.telugu_image else "no --telugu-image given")
    def audio(path, lang, expect=None):
        t = eng.transcribe_audio(path, language=lang)
        if lang == "te" and not any("\u0c00" <= ch <= "\u0c7f" for ch in t):
            return (False, f"no Telugu script in the answer: {one(t)}")
        if expect:
            r = difflib.SequenceMatcher(None, " ".join(t.split()), " ".join(expect.split())).ratio()
            return (r >= 0.6, f"similarity to expected {r:.0%} · {one(t)}")
        return (len(t.strip()) > 0, one(t))
    run("audio capability (/health)", lambda: (bool(caps.get("audio")), f"capabilities.audio = {caps.get('audio')}"),
        None if (caps.get("audio") or args.audio_en or args.audio_te) else
        "capabilities.audio is false — voice stays on whisper until Medha reports it")
    run("3 English voice clip", lambda: audio(args.audio_en, "en", args.expect_en), None if args.audio_en else "no --audio-en given")
    run("4 Telugu voice clip", lambda: audio(args.audio_te, "te", args.expect_te), None if args.audio_te else "no --audio-te given")
    def refuses():
        bad = [{"messages": []},
               {"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "https://example.com/x.jpg"}}, {"type": "text", "text": "hi"}]}]}]
        codes = []
        for b in bad:
            r = requests.post(eng._url + "/v1/chat/completions", data=json.dumps(b), headers=eng._headers(), timeout=30)
            codes.append(r.status_code)
        return (all(400 <= c < 500 or c == 501 for c in codes), f"malformed → {codes[0]}, remote image → {codes[1]} (4xx/501 expected, never 5xx)")
    run("7 bad input is refused cleanly", refuses)
    return _check_report(rows, h, eng)


def _check_report(rows, health, eng):
    import requests
    w = max(len(r[0]) for r in rows)
    print(f"\nMedha check · {eng._url}\n" + "─" * 78)
    for name, st, detail, secs in rows:
        mark = {"PASS": "✓ PASS", "FAIL": "✗ FAIL", "SKIP": "· skip"}[st]
        print(f"{mark}  {name.ljust(w)}  {('%.1fs' % secs) if secs else '    '}  {detail}")
    fails = [r for r in rows if r[1] == "FAIL"]
    vision_or_audio_failed = any(r[1] == "FAIL" and r[0][:1] in "1235" or r[0].startswith(("3 ", "4 ")) and r[1] == "FAIL" for r in rows)
    caps = (health or {}).get("capabilities") or {}
    if health is not None and (vision_or_audio_failed or caps.get("vision") is False or caps.get("audio") is False):
        try:
            r = requests.get(eng._url + "/system", headers=eng._headers(), timeout=10)
            mm = (r.json() or {}).get("multimodal") if r.ok else None
        except Exception:
            mm = None
        print("\n/system → multimodal (send this to the Medha build):")
        print(json.dumps(mm, indent=2) if mm is not None else "  (not available — is /system exposed and the token right?)")
    print("─" * 78 + f"\n{len(rows) - len(fails) - sum(1 for r in rows if r[1] == 'SKIP')} passed · {len(fails)} failed · "
          f"{sum(1 for r in rows if r[1] == 'SKIP')} skipped")
    return 1 if fails else 0


def cmd_media_compare(args, cfg):
    """Summarise the Medha-vs-whisper voice log (media_compare.jsonl) so the two can be judged on real audio."""
    from .media import Router
    f = Router(cfg).compare_file()
    rows = []
    try:
        for line in open(f, encoding="utf-8"):
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    except OSError:
        print(f"No comparison log yet ({f}). It fills as voice notes are transcribed with media_audio_compare on.")
        return 0
    if not rows:
        print("The comparison log is empty."); return 0
    n = len(rows)
    sims = [r["similarity"] for r in rows if r.get("similarity") is not None]
    avg = lambda xs: (sum(xs) / len(xs)) if xs else 0.0
    def share(side, key):
        return sum(1 for r in rows if r[side][key] > r[side]["telugu" if key == "latin" else "latin"]) / n
    print(f"{n} clips compared · log: {f}")
    print(f"  time    medha {avg([r['medha']['secs'] for r in rows]):.1f}s   whisper {avg([r['whisper']['secs'] for r in rows]):.1f}s  (average per clip)")
    print(f"  script  Telugu-script answers: medha {sum(1 for r in rows if r['medha']['telugu'] > r['medha']['latin'])}/{n}   "
          f"whisper {sum(1 for r in rows if r['whisper']['telugu'] > r['whisper']['latin'])}/{n}")
    print(f"  Latin-script answers (transliterated or English): medha {sum(1 for r in rows if r['medha']['latin'] >= r['medha']['telugu'])}/{n}   "
          f"whisper {sum(1 for r in rows if r['whisper']['latin'] >= r['whisper']['telugu'])}/{n}")
    if sims:
        print(f"  agreement between the two (0–1): average {avg(sims):.2f}, lowest {min(sims):.2f}")
    print("\nlatest clips (medha / whisper):")
    for r in rows[-args.last:]:
        cut = lambda t: " ".join(t.split())[:150]
        print(f"  {r['id']}  sim={r.get('similarity')}  lang={r.get('lang')}\n    M: {cut(r['medha']['text'])}\n    W: {cut(r['whisper']['text'])}")
    return 0


def cmd_calendar_auth(args, cfg):
    from .calendar_client import authorize
    ok = authorize(cfg, headless=args.headless)
    sys.exit(0 if ok else 1)


def cmd_thermal(args, cfg):
    t = ThermalGuard(cfg).read_celsius()
    print(f"SoC temp: {t:.1f}°C" if t is not None else "thermal: unavailable")


def cmd_relations(args, cfg):
    """Diagnose why notes are (or aren't) connecting: build the real corpus from
    your notes and print every pairwise similarity score against the active
    thresholds. Use this to tune embed_related_threshold / embed_cluster_threshold
    from evidence rather than guesswork."""
    from .correlation import Corpus
    from .embeddings import Embedder
    from .qs_client import QuicksilverClient

    backend = cfg.get("backend", "quicksilver")
    if backend != "quicksilver":
        print("relations diagnostic supports the quicksilver backend only.")
        return
    qs = QuicksilverClient(db_path=cfg["db_path"], webhook_url="")

    embedder = None
    if cfg.get("correlation_enabled", True):
        try:
            embedder = Embedder(cfg.get("embed_model", ""),
                                cfg.get("embed_cache_dir", ""),
                                backend=cfg.get("embed_backend", "auto"),
                                onnx_dir=cfg.get("embed_onnx_dir", ""))
            if not embedder.available():
                embedder = None
        except Exception as e:
            print(f"(embedder unavailable: {e})")
            embedder = None
    semantic = embedder is not None
    rel_thr = cfg.get("embed_related_threshold", 0.45) if semantic else cfg.get("related_threshold", 0.12)
    clu_thr = cfg.get("embed_cluster_threshold", 0.55) if semantic else cfg.get("cluster_threshold", 0.18)

    corpus = Corpus(embedder=embedder)
    meta = {}
    for n in qs.iter_all_memos(page_size=100):
        nid = n.get("name") or n.get("id")
        body = (n.get("body") or n.get("content") or "").strip()
        if not nid or not body:
            continue
        enr = n.get("enrichment") or {}
        title = (n.get("title") or enr.get("title")
                 or body[:40]).replace("\n", " ")
        tags = n.get("tags") or enr.get("tags") or []
        summary = n.get("summary") or enr.get("summary") or ""
        corpus.add(nid, title, body, tags=tags, summary=summary)
        meta[nid] = {"title": title, "tags": {str(t).strip().lower().lstrip("#")
                                              for t in tags if str(t).strip()}}

    mode = "semantic (embeddings)" if semantic else "lexical (tf-idf)"
    band = cfg.get("embed_confirm_band", 0.10) if semantic else 0.0
    assist = cfg.get("embed_tag_assist", 0.20) if semantic else 0.0
    print(f"\nmode: {mode}")
    print(f"model: {cfg.get('embed_model','') or '(lexical)'}")
    print(f"thresholds: related ≥ {rel_thr:.2f} · cluster ≥ {clu_thr:.2f}")
    if band > 0:
        print(f"tag-confirm band: {rel_thr:.2f}–{rel_thr + band:.2f} "
              f"(needs a SHARED TAG to link)")
    if assist > 0:
        print(f"tag assist: a SHARED TAG links from {rel_thr - assist:.2f}")
    print(f"corpus: {len(meta)} note(s)")
    tagged = sum(1 for m in meta.values() if m["tags"])
    print(f"tagged: {tagged}/{len(meta)} note(s) have tags\n")
    if len(meta) < 2:
        print("Need at least 2 notes with text to compute relationships.")
        return

    # Every pair, best first, with a verdict against the active thresholds.
    names = list(meta.keys())
    pairs = []
    for i in range(len(names)):
        for nm, _t, sc in corpus.related(names[i], top_n=len(names), threshold=0.0):
            if names[i] < nm:            # each unordered pair once
                pairs.append((sc, names[i], nm))
    pairs.sort(reverse=True)

    if not pairs:
        print("No pairs scored.")
        return

    def verdict(sc, a, b):
        shared = meta[a]["tags"] & meta[b]["tags"]
        floor = rel_thr - assist if (assist > 0 and shared) else rel_thr
        if sc < floor:
            return ("· close" if sc >= floor - 0.10 else ""), shared
        if band > 0 and sc < rel_thr + band and not shared:
            return "✗ blocked", shared          # in band, no shared tag
        return ("✓ link+tag" if (shared and sc < rel_thr) else "✓ link"), shared

    print(f"{'SCORE':>6}  {'VERDICT':<11} {'SHARED TAG':<14} PAIR")
    print("-" * 88)
    for sc, a, b in pairs[: args.top]:
        v, shared = verdict(sc, a, b)
        tagtxt = ("#" + sorted(shared)[0]) if shared else "—"
        print(f"{sc:6.3f}  {v:<11} {tagtxt:<14} "
              f"{meta[a]['title'][:24]:<26} ~ {meta[b]['title'][:24]}")
    print("-" * 88)
    total_linked = sum(1 for sc, a, b in pairs if verdict(sc, a, b)[0].startswith("✓"))
    blocked = sum(1 for sc, a, b in pairs if verdict(sc, a, b)[0] == "✗ blocked")
    assisted = sum(1 for sc, a, b in pairs if verdict(sc, a, b)[0] == "✓ link+tag")
    print(f"\n{total_linked} of {len(pairs)} pair(s) link.")
    if assisted:
        print(f"  {assisted} of those linked thanks to a SHARED TAG (below the raw threshold).")
    if blocked:
        print(f"{blocked} pair(s) blocked by the tag-confirm guard "
              f"(uncertain band, no shared tag).")
    print(f"{len(corpus.clusters(threshold=clu_thr))} cluster(s) at the cluster "
          f"threshold ({clu_thr:.2f}).")
    if tagged < len(meta):
        print(f"\nNote: {len(meta) - tagged} note(s) have no tags — they can never "
              f"benefit from tag assist. Re-run enrichment on them to get tags.")
    if total_linked == 0 and pairs:
        best = pairs[0][0]
        print(f"\nBest pair scores {best:.3f} — below the {rel_thr:.2f} bar.")
        print(f"To connect your closest notes, set embed_related_threshold to about "
              f"{max(0.05, best - 0.02):.2f} in your config.")


def cmd_backfill_embeddings(args, cfg):
    """Compute and store embeddings for notes that don't have one yet.

    New notes get their embedding persisted at enrichment time. This backfills
    notes that existed BEFORE persistence was added — so the correlation corpus
    stops recomputing them and the PWA's semantic search can rank them.
    Idempotent: only touches rows where embedding IS NULL. Safe to re-run.
    """
    from .embeddings import Embedder
    from .qs_client import QuicksilverClient
    import json as _j, sqlite3 as _s3

    if cfg.get("backend", "quicksilver") != "quicksilver":
        print("backfill supports the quicksilver backend only.")
        return

    con = _s3.connect(cfg["db_path"]); con.row_factory = _s3.Row

    # First, migrate any legacy JSON embeddings to the compact float32 blob —
    # this needs NO model (just re-encode the same vector), so it runs before the
    # embedder check. Shrinks the DB ~5x per embedding and lets search read them
    # via the fast frombuffer path.
    from .embeddings import encode_embedding, decode_embedding
    legacy = con.execute(
        "SELECT id, embedding FROM notes WHERE embedding IS NOT NULL "
        "AND typeof(embedding) = 'text'").fetchall()
    if legacy:
        print(f"Converting {len(legacy)} legacy JSON embedding(s) to blob…")
        conv = 0
        for r in legacy:
            v = decode_embedding(r["embedding"])
            if v:
                con.execute("UPDATE notes SET embedding=? WHERE id=?",
                            (encode_embedding(v), r["id"])); conv += 1
        con.commit()
        print(f"  ✓ converted {conv} to compact blob format")

    emb = Embedder(cfg.get("embed_model", ""), cfg.get("embed_cache_dir", ""),
                   backend=cfg.get("embed_backend", "auto"), onnx_dir=cfg.get("embed_onnx_dir", ""))
    if not emb.available():
        print("embedder unavailable — skipping NULL backfill "
              "(JSON→blob conversion above still done). Check embed_model.")
        return

    qs = QuicksilverClient(db_path=cfg["db_path"], webhook_url="")
    rows = con.execute("SELECT id, body FROM notes WHERE embedding IS NULL").fetchall()
    total = len(rows)
    if not total:
        print("All notes already have embeddings. Nothing to do.")
        return
    print(f"Backfilling {total} note(s)…")
    done = skipped = 0
    for i, r in enumerate(rows, 1):
        nid, body = r["id"], (r["body"] or "").strip()
        if not body:
            skipped += 1
            continue
        # Match what enrichment stores: title + tags + summary + body.
        e = con.execute("SELECT title, summary FROM enrichments WHERE note_id=? "
                        "ORDER BY rowid DESC LIMIT 1", (nid,)).fetchone()
        title = (e["title"] if e else "") or ""
        summary = (e["summary"] if e else "") or ""
        tags = [t["tag"] for t in con.execute(
            "SELECT tag FROM tags WHERE note_id=? ORDER BY tag", (nid,))]
        text = qs._embed_text(title, tags, summary, body)
        try:
            vec = emb.encode(text)
            if vec:
                from .embeddings import encode_embedding
                con.execute("UPDATE notes SET embedding=? WHERE id=?",
                            (encode_embedding(vec), nid))
                done += 1
        except Exception as ex:
            print(f"  ! {nid}: {ex}")
            skipped += 1
        if i % 25 == 0 or i == total:
            con.commit()
            print(f"  {i}/{total}…")
    con.commit(); con.close()
    print(f"\n✓ backfilled {done} embedding(s)" + (f", skipped {skipped}" if skipped else ""))
    print("  The correlation corpus will reuse these instead of recomputing,")
    print("  and semantic search can rank against them.")


def cmd_reset(args, cfg):
    p = Path(cfg["state_file"])
    if p.exists():
        p.unlink(); print(f"Removed {p}")
    else:
        print("No state file.")


def cmd_init_config(args, cfg):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if CONFIG_PATH.exists() and not args.force:
        print(f"Config exists at {CONFIG_PATH}. Use --force."); return
    CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=2))
    print(f"Wrote {CONFIG_PATH}. Set model_path and memos_token.")


def _set_process_title(name: str):
    """Make the process identifiable in `ps -ef` as hg_agent.
    Uses setproctitle when installed (pip install setproctitle); otherwise
    falls back silently — the wrapper script's `exec -a` still names it."""
    try:
        from setproctitle import setproctitle
        setproctitle(name)
    except ImportError:
        pass


def main():
    _set_process_title("hg_agent")
    p = argparse.ArgumentParser(prog="hg_agent")
    p.add_argument("--config", "-c", default=None,
                   help="Path to config JSON (default: ~/.config/memos_daemon/config.json "
                        "or $HG_AGENT_CONFIG)")
    sub = p.add_subparsers(dest="command", required=True)
    pr = sub.add_parser("run"); pr.add_argument("--dry-run", action="store_true"); pr.set_defaults(func=cmd_run)
    pe = sub.add_parser("enrich"); pe.add_argument("--text"); pe.set_defaults(func=cmd_enrich)
    sub.add_parser("status").set_defaults(func=cmd_status)
    sub.add_parser("thermal").set_defaults(func=cmd_thermal)
    pbc = sub.add_parser("brief-check", aliases=["brief_check"], help="why is (or isn't) something in Medha's Now brief and notifications")
    pbc.add_argument("--test", action="store_true", help="also post a test notification for 8 seconds"); pbc.set_defaults(func=cmd_brief_check)
    pm = sub.add_parser("medha_check", aliases=["medha-check"], help="pass/fail table for the Medha service (text, photos, voice, Telugu)")
    for a, h in (("--image", "a photo"), ("--sketch", "a hand-drawn sketch"), ("--telugu-image", "a photo of Telugu text"),
                 ("--audio-en", "an English voice clip (≤ 28 s)"), ("--audio-te", "a Telugu voice clip (≤ 28 s)"),
                 ("--expect-en", "what the English clip says (adds a similarity score)"),
                 ("--expect-te", "what the Telugu clip says (adds a similarity score)")):
        pm.add_argument(a, help=h)
    pm.set_defaults(func=cmd_medha_check)
    pc = sub.add_parser("media_compare", aliases=["media-compare"], help="summary of the Medha vs whisper voice comparison log")
    pc.add_argument("--last", type=int, default=5, help="how many recent clips to show")
    pc.set_defaults(func=cmd_media_compare)
    pca = sub.add_parser("calendar-auth")
    pca.add_argument("--headless", action="store_true",
                     help="manual copy/paste flow for devices without a browser")
    pca.set_defaults(func=cmd_calendar_auth)
    sub.add_parser("reset").set_defaults(func=cmd_reset)
    prl = sub.add_parser("relations", help="diagnose note similarity scores vs thresholds")
    prl.add_argument("--top", type=int, default=25, help="how many pairs to show")
    prl.set_defaults(func=cmd_relations)
    sub.add_parser("backfill-embeddings",
                   help="store embeddings for notes that lack one").set_defaults(
                   func=cmd_backfill_embeddings)
    pic = sub.add_parser("init-config"); pic.add_argument("--force", action="store_true"); pic.set_defaults(func=cmd_init_config)
    args = p.parse_args()
    cfg = load_config(getattr(args, "config", None))
    setup_logging(cfg.get("log_file"))
    # Announce the active backend up front — this is the single most useful
    # line for diagnosing "why isn't the agent talking to my app".
    backend = cfg.get("backend", "quicksilver")
    if backend == "quicksilver":
        log.info(f"Backend: Quicksilver (SQLite) · db={cfg.get('db_path') or '(UNSET!)'}")
        if not cfg.get("db_path"):
            log.error("backend=quicksilver but db_path is empty. The agent will "
                      "fall back to Memos mode. Set db_path in your config.")
    else:
        log.info(f"Backend: Memos (HTTP) · {cfg.get('memos_base_url')}")
    args.func(args, cfg)


