#!/usr/bin/env python3
"""
End-to-end functional tests for the Quicksilver UI (hg_ui).

Runs the REAL Flask app against a throwaway database via Flask's test client —
no server, no network, no agent required. Exercises the full note lifecycle and
every scaling-arc feature we built, so a regression anywhere shows up as a red.

    python3 e2e_test.py            # run everything
    python3 e2e_test.py -v         # also print each passing case

Exit code 0 = all green, 1 = at least one failure (usable in CI / pre-deploy).

These tests are hermetic: they build their own DB in a temp dir and never touch
your real notes at ~/.quicksilver/notes.db.
"""
import sys, os, tempfile, importlib.util, sqlite3, json
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

VERBOSE = "-v" in sys.argv
_P = _F = 0
_FAILS = []


def check(cond, msg):
    global _P, _F
    if cond:
        _P += 1
        if VERBOSE:
            print(f"  \033[32m✓\033[0m {msg}")
    else:
        _F += 1
        _FAILS.append(msg)
        print(f"  \033[31m✗ {msg}\033[0m")


def section(name):
    print(f"\n\033[1m{name}\033[0m")


def load_app(db_path):
    """Load app.py fresh against a specific DB. Returns the module."""
    os.environ["QS_DB"] = db_path
    if "qs_app" in sys.modules:
        del sys.modules["qs_app"]
    spec = importlib.util.spec_from_file_location(
        "qs_app", os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py"))
    qs = importlib.util.module_from_spec(spec)
    sys.modules["qs_app"] = qs
    spec.loader.exec_module(qs)
    qs.init_db()
    return qs


def iso(d):
    return d.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


# ─────────────────────────────────────────────────────────────────────────────
def test_note_lifecycle(qs):
    section("1. Note lifecycle (create → read → edit → pin → archive → delete)")
    c = qs.app.test_client()

    r = c.post("/api/notes", json={"body": "buy groceries and pay telecom bill"})
    check(r.status_code == 201, "POST /api/notes creates a note (201)")
    nid = r.get_json()["id"]
    check(bool(nid), "created note returns an id")

    r = c.get(f"/api/notes/{nid}")
    check(r.status_code == 200 and "groceries" in r.get_json()["body"],
          "GET /api/notes/<id> returns the note body")

    r = c.patch(f"/api/notes/{nid}", json={"body": "edited: pay the electricity bill"})
    check(r.status_code == 200, "PATCH edits the body (200)")
    check("electricity" in c.get(f"/api/notes/{nid}").get_json()["body"],
          "edit persisted")

    r = c.post(f"/api/notes/{nid}/pin")
    check(r.status_code in (200, 201, 204), "pin endpoint responds OK")

    r = c.post(f"/api/notes/{nid}/archive")
    check(r.status_code in (200, 201, 204), "archive endpoint responds OK")
    # archived note should leave the default list
    default_ids = {n["id"] for n in c.get("/api/notes").get_json()["notes"]}
    check(nid not in default_ids, "archived note drops out of the default list")
    # …but appear in the archived filter
    arch_ids = {n["id"] for n in c.get("/api/notes?filter=archived").get_json()["notes"]}
    check(nid in arch_ids, "archived note appears under filter=archived")

    r = c.delete(f"/api/notes/{nid}")
    check(r.status_code in (200, 204), "DELETE removes the note")
    check(c.get(f"/api/notes/{nid}").status_code == 404, "deleted note is gone (404)")


def test_list_and_count(qs):
    section("2. List pagination + total count (the index + count-join fix)")
    c = qs.app.test_client()
    # seed a known number
    for i in range(25):
        c.post("/api/notes", json={"body": f"seed note {i} project alpha"})
    j = c.get("/api/notes").get_json()
    check("total" in j and "notes" in j, "list returns {notes, total}")
    check(j["total"] >= 25, f"total reflects seeded notes ({j['total']})")
    check(len(j["notes"]) <= 50, "page size capped (<=50)")
    # count must be consistent with a filter
    j2 = c.get("/api/notes?q=alpha").get_json()
    check(j2["total"] >= 25, "search total counts matches")


def test_search_modes(qs):
    section("3. Hybrid search (keyword / semantic / temporal)")
    c = qs.app.test_client()
    now = datetime.now(timezone.utc)
    db = sqlite3.connect(os.environ["QS_DB"])
    seeds = [
        ("bill_recent", "BSNL telecom broadband bill payment", now - timedelta(days=3)),
        ("bill_old", "Airtel telecom bill old one", now - timedelta(days=200)),
        ("visa_yr", "visa consulate appointment abroad", now - timedelta(days=365)),
        ("misc", "grocery list milk eggs bread", now - timedelta(days=1)),
    ]
    for nid, body, dt in seeds:
        db.execute("INSERT INTO notes (id,body,created_at,updated_at) VALUES (?,?,?,?)",
                   (nid, body, iso(dt), iso(dt)))
        db.execute("INSERT INTO note_state (note_id,status) VALUES (?,'approved')", (nid,))
    db.commit()
    qs._init_fts(db)  # ensure the FTS index sees the directly-inserted rows

    with patch.object(qs, "_embed_query", lambda t: None):  # keyword/temporal only
        # plain keyword
        ids = {n["id"] for n in c.get("/api/search?q=telecom").get_json()["notes"]}
        check("bill_recent" in ids and "bill_old" in ids, "keyword 'telecom' finds both bills")

        # empty query
        check(c.get("/api/search?q=").get_json()["mode"] == "empty",
              "empty query → mode 'empty'")

        # temporal: "last week" excludes the 200-day-old note
        r = c.get("/api/search?q=bill from last week").get_json()
        ids = {n["id"] for n in r["notes"]}
        check("bill_recent" in ids and "bill_old" not in ids,
              "'bill from last week' includes recent, EXCLUDES 200-day-old")
        check(r["mode"] == "temporal", "temporal query reports mode 'temporal'")

        # temporal: "last year this time"
        ids = {n["id"] for n in c.get("/api/search?q=visa last year this time").get_json()["notes"]}
        check("visa_yr" in ids, "'visa last year this time' finds the year-old note")


def test_fts_triggers(qs):
    section("4. FTS stays in sync via triggers (create/edit/delete)")
    c = qs.app.test_client()
    with patch.object(qs, "_embed_query", lambda t: None):
        nid = c.post("/api/notes", json={"body": "xenon telemetry probe"}).get_json()["id"]
        ids = {n["id"] for n in c.get("/api/search?q=xenon").get_json()["notes"]}
        check(nid in ids, "new note immediately searchable (insert trigger)")

        c.patch(f"/api/notes/{nid}", json={"body": "krypton observability probe"})
        ids = {n["id"] for n in c.get("/api/search?q=krypton").get_json()["notes"]}
        check(nid in ids, "edited note searchable by new term (update trigger)")
        ids = {n["id"] for n in c.get("/api/search?q=xenon").get_json()["notes"]}
        check(nid not in ids, "old term no longer matches after edit")

        c.delete(f"/api/notes/{nid}")
        ids = {n["id"] for n in c.get("/api/search?q=krypton").get_json()["notes"]}
        check(nid not in ids, "deleted note removed from index (delete trigger)")


def test_embedding_search(qs):
    section("5. Semantic search reads embeddings (blob + legacy JSON)")
    import struct, hashlib
    c = qs.app.test_client()

    def dv(t, d=384):
        o = []; i = 0
        while len(o) < d:
            h = hashlib.sha256(t.encode() + str(i).encode()).digest()
            for j in range(0, len(h), 4):
                if len(o) >= d:
                    break
                o.append(struct.unpack("<i", h[j:j + 4])[0] / 2**31)
            i += 1
        n = sum(x * x for x in o) ** 0.5 or 1.0
        return [x / n for x in o]

    n1 = c.post("/api/notes", json={"body": "telecom note blob-stored"}).get_json()["id"]
    n2 = c.post("/api/notes", json={"body": "telecom note legacy-json"}).get_json()["id"]
    db = sqlite3.connect(os.environ["QS_DB"])
    # n1 as compact blob, n2 as legacy JSON — both must be readable
    blob = struct.pack("<384f", *dv("blob"))
    db.execute("UPDATE notes SET embedding=? WHERE id=?", (blob, n1))
    db.execute("UPDATE notes SET embedding=? WHERE id=?", (json.dumps(dv("legacy")), n2))
    db.commit()
    check(isinstance(db.execute("SELECT embedding FROM notes WHERE id=?", (n1,)).fetchone()[0], bytes),
          "blob embedding stored as bytes")
    with patch.object(qs, "_embed_query", lambda t: dv("telecom")):
        ids = {n["id"] for n in c.get("/api/search?q=telecom").get_json()["notes"]}
    check(n1 in ids and n2 in ids, "search returns BOTH blob and legacy-JSON notes")


def test_supporting_endpoints(qs):
    section("6. Supporting endpoints respond")
    c = qs.app.test_client()
    for path in ["/api/stats", "/api/tags", "/api/templates", "/api/relationships",
                 "/api/settings", "/api/actions", "/api/daily"]:
        r = c.get(path)
        check(r.status_code == 200, f"GET {path} → 200")


def test_webhook_and_events(qs):
    section("7. Webhook + SSE endpoint exist (agent integration seam)")
    c = qs.app.test_client()
    # webhook accepts a note-changed ping (agent → UI or UI → agent contract)
    r = c.post("/api/webhook", json={"type": "ping"})
    check(r.status_code in (200, 202, 204, 400), "POST /api/webhook reachable")
    # SSE endpoint: verify it's REGISTERED without opening the stream (a GET
    # would block forever — it's an infinite event stream). Checking the URL map
    # confirms the agent's subscribe target exists.
    routes = {r.rule for r in qs.app.url_map.iter_rules()}
    check("/api/events" in routes, "/api/events (SSE) route is registered")


def main():
    print("\033[1m═══ Quicksilver UI — end-to-end functional tests ═══\033[0m")
    tmp = tempfile.mkdtemp()
    db_path = os.path.join(tmp, "e2e.db")
    qs = load_app(db_path)

    test_note_lifecycle(qs)
    test_list_and_count(qs)
    test_search_modes(qs)
    test_fts_triggers(qs)
    test_embedding_search(qs)
    test_supporting_endpoints(qs)
    test_webhook_and_events(qs)

    print("\n" + "─" * 55)
    if _F == 0:
        print(f"\033[32m  {_P}/{_P} passed — all green ✨\033[0m")
    else:
        print(f"\033[31m  {_P}/{_P + _F} passed, {_F} FAILED\033[0m")
        for m in _FAILS:
            print(f"\033[31m    ✗ {m}\033[0m")
    return 0 if _F == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
