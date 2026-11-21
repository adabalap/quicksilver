#!/usr/bin/env python3
"""Quicksilver speed check.

  python3 tools/perf.py --url http://127.0.0.1:5230        # time a RUNNING server (your real notes)
  python3 tools/perf.py --seed 3000                        # build a throwaway database of N notes and time it

Prints, for each screen's request: median and slowest-of-N milliseconds, and the size of the reply.
Also lists the database size, the page's first-load weight, and anything that looks slow.
Read-only against a live server (only GETs). Nothing is changed in your notes.
"""
import sys, os, time, json, argparse, statistics, tempfile, random, struct, string, sqlite3, glob
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)

ENDPOINTS = [   # (label, path) — what the app asks for, in the order it asks
    ("version",             "/api/version"),
    ("home strips",         "/api/home"),
    ("notes list (first 40)", "/api/notes?limit=40"),
    ("notes list (200)",    "/api/notes?limit=200"),
    ("tags",                "/api/tags"),
    ("stats",               "/api/stats"),
    ("search: keyword",     "/api/search?q=meeting"),
    ("search: two words",   "/api/search?q=project+plan"),
    ("filter: photos",      "/api/notes?type=photo"),
    ("actions",             "/api/actions"),
    ("reminders",           "/api/reminders"),
    ("highlights",          "/api/highlights"),
    ("pulse dashboard",     "/api/pulse"),
    ("saved searches",      "/api/saved-searches"),
    ("digest",              "/api/digest"),
    ("duplicates",          "/api/duplicates"),
    ("system health",       "/api/health"),
    ("trash",               "/api/trash"),
]
SLOW_MS = 250

def make_getter(url):
    if url:
        import urllib.request
        def get(path):
            t = time.perf_counter()
            with urllib.request.urlopen(url.rstrip("/") + path, timeout=60) as r:
                body = r.read()
            return (time.perf_counter() - t) * 1000, len(body), r.status
        return get, None
    return None, None

def seed_db(n, path):
    os.environ["QS_DB"] = path
    sys.path.insert(0, ROOT)
    import importlib.util
    for k in [k for k in sys.modules if k == "server" or k.startswith("server.")]: del sys.modules[k]
    spec = importlib.util.spec_from_file_location("qs_perf", os.path.join(ROOT, "app.py"))
    qs = importlib.util.module_from_spec(spec); sys.modules["qs_perf"] = qs; spec.loader.exec_module(qs); qs.init_db()
    rnd = random.Random(7)
    words = ("meeting project plan budget travel recipe idea fedora linux gemma notes agent photo dog garden "
             "story reminder call invoice report design sprint review launch family school trip cricket music").split()
    tags = ["work", "home", "ideas", "linux", "travel", "recipes", "family", "finance", "reading", "health"]
    db = sqlite3.connect(path); db.execute("PRAGMA journal_mode=WAL")
    cols = [r[1] for r in db.execute("PRAGMA table_info(notes)")]
    now = time.time(); dim = 384
    for i in range(n):
        nid = "".join(rnd.choices(string.ascii_letters + string.digits, k=8))
        body = " ".join(rnd.choices(words, k=rnd.randint(8, 120)))
        ts = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(now - rnd.randint(0, 3 * 365 * 86400)))
        emb = struct.pack(f"<{dim}f", *[rnd.random() - 0.5 for _ in range(dim)]) if rnd.random() < .6 else None
        db.execute("INSERT INTO notes (id, body, created_at, updated_at, embedding, embed_model) VALUES (?,?,?,?,?,?)",
                   (nid, body, ts, ts, emb, "all-MiniLM-L6-v2" if emb else None))
        db.execute("INSERT INTO enrichments (id, note_id, title, summary, priority) VALUES (?,?,?,?,?)",
                   (nid + "e", nid, " ".join(body.split()[:5]).title(), " ".join(body.split()[:25]), rnd.choice(["high", "medium", "low"])))
        db.execute("INSERT INTO note_state (note_id, status) VALUES (?, 'approved')", (nid,))
        for t in rnd.sample(tags, rnd.randint(0, 3)): db.execute("INSERT OR IGNORE INTO tags VALUES (?,?)", (nid, t))
        if rnd.random() < .25:
            db.execute("INSERT INTO action_items (id, note_id, text, done) VALUES (?,?,?,?)", (nid + "a", nid, "Follow up on " + words[rnd.randrange(len(words))], rnd.random() < .4))
        if rnd.random() < .08:
            db.execute("INSERT INTO reminders (id, note_id, title, datetime_resolved) VALUES (?,?,?,?)", (nid + "r", nid, "Reminder " + words[rnd.randrange(len(words))], ts))
    ids = [r[0] for r in db.execute("SELECT id FROM notes")]
    for _ in range(min(len(ids) * 4, 20000)):
        a, b = rnd.sample(ids, 2)
        db.execute("INSERT OR IGNORE INTO relationships (note_id_a, note_id_b, score) VALUES (?,?,?)", (a, b, round(rnd.random(), 3)))
    db.commit()
    try: db.execute("INSERT INTO notes_fts(notes_fts) VALUES ('rebuild')"); db.commit()
    except Exception: pass
    db.close()
    return qs

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url"); ap.add_argument("--seed", type=int); ap.add_argument("--runs", type=int, default=7)
    ap.add_argument("--json", help="also write the results to this file")
    a = ap.parse_args()
    if not a.url and not a.seed: ap.error("give --url (a running server) or --seed N")
    qs = None; get = None
    if a.url:
        get, _ = make_getter(a.url); where = a.url
    else:
        tmp = tempfile.mkdtemp(); path = os.path.join(tmp, "perf.db")
        t0 = time.time(); qs = seed_db(a.seed, path); where = f"throwaway database with {a.seed} notes (built in {time.time()-t0:.1f}s)"
        cl = qs.app.test_client()
        def get(p):
            t = time.perf_counter(); r = cl.get(p); body = r.get_data()
            return (time.perf_counter() - t) * 1000, len(body), r.status_code
    print(f"\nQuicksilver speed check — {where}\n")
    rows = []
    print(f"{'request':26} {'median':>8} {'slowest':>9} {'size':>9}")
    for label, p in ENDPOINTS:
        ts = []; size = 0; code = 0
        for _ in range(a.runs):
            try: ms, size, code = get(p)
            except Exception as e: ms, size, code = 0, 0, str(e)[:30]; break
            ts.append(ms)
        if not ts: print(f"{label:26} failed ({code})"); continue
        med = statistics.median(ts); mx = max(ts)
        flag = "  ◄ slow" if med > SLOW_MS else ""
        print(f"{label:26} {med:6.0f}ms {mx:7.0f}ms {size/1024:7.1f}KB{flag}" + ("" if code == 200 else f"  [HTTP {code}]"))
        rows.append(dict(label=label, path=p, median_ms=round(med, 1), max_ms=round(mx, 1), kb=round(size / 1024, 1), status=code))
    # first-load weight
    js = sum(os.path.getsize(f) for f in glob.glob(os.path.join(ROOT, "static", "js", "*.js")))
    css = sum(os.path.getsize(f) for f in glob.glob(os.path.join(ROOT, "static", "css", "*.css")))
    html = os.path.getsize(os.path.join(ROOT, "static", "index.html"))
    print(f"\nfirst load: page {html/1024:.0f} KB + scripts {js/1024:.0f} KB + styles {css/1024:.0f} KB  (uncompressed; cached after the first visit)")
    dbp = os.environ.get("QS_DB") or os.path.expanduser("~/.quicksilver/notes.db")
    if not a.url and qs is not None: dbp = os.environ["QS_DB"]
    if os.path.exists(dbp): print(f"database: {os.path.getsize(dbp)/1048576:.1f} MB at {dbp}")
    slow = [r for r in rows if r["median_ms"] > SLOW_MS]
    print("\n" + ("Everything answers within %d ms." % SLOW_MS if not slow else "Slow (> %d ms): " % SLOW_MS + ", ".join(f"{r['label']} {r['median_ms']:.0f}ms" for r in slow)))
    if a.json: json.dump(rows, open(a.json, "w"), indent=1)

if __name__ == "__main__":
    main()
