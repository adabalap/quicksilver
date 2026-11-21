#!/usr/bin/env python3
"""Filters, saved searches, thumbnails, digest, duplicates + merge, system health."""
import sys, os, io, json, tempfile, base64
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from e2e_test import load_app
P = F = 0
def check(c, m):
    global P, F
    if c: P += 1
    else: F += 1; print(f"  \033[31m✗ {m}\033[0m")

tmp = tempfile.mkdtemp()
qs = load_app(os.path.join(tmp, "f.db"))
cl = qs.app.test_client()
db = qs.sqlite3.connect(os.path.join(tmp, "f.db")); db.row_factory = qs.sqlite3.Row
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")

def mk(body, **kw):
    r = cl.post("/api/notes", json={"body": body, **kw}); assert r.status_code == 201, r.data
    return r.get_json()["id"]
a = mk("Fedora tip #linux: dnf history undo reverts the last transaction safely.")
b = mk("Grocery list #home milk eggs bread")
c = mk("", media="photo")
cl.post(f"/api/notes/{c}/attachments", data={"file": (io.BytesIO(PNG), "p.png", "image/png"), "kind": "image", "meta": "{}"},
        content_type="multipart/form-data")
v = mk("", media="voice")
cl.post(f"/api/notes/{v}/attachments", data={"file": (io.BytesIO(b"\x1a\x45\xdf\xa3" + b"\0" * 100), "a.webm", "audio/webm"), "kind": "audio", "meta": "{}"},
        content_type="multipart/form-data")
ids = lambda url: {n["id"] for n in cl.get(url).get_json()["notes"]}

# ── filters
check(ids("/api/notes?type=photo") == {c}, "type=photo lists only notes with a photo")
check(ids("/api/notes?type=voice") == {v}, "type=voice lists only voice notes")
check(ids("/api/notes?type=text") == {a, b}, "type=text excludes notes with attachments")
check(ids("/api/notes?tag=linux") == {a}, "tag filter")
check(ids("/api/notes?tag=%23LINUX") == {a}, "tag filter ignores # and case")
check(ids("/api/notes?since=2999-01-01") == set(), "since in the future matches nothing")
check(ids("/api/notes?since=2000-01-01&until=2999-01-01") == {a, b, c, v}, "date range keeps everything inside it")
check(ids("/api/notes?type=bogus") == {a, b, c, v}, "unknown type is ignored, not an error")
check(ids("/api/search?q=dnf&type=photo") == set(), "search honours the type filter")
check(a in ids("/api/search?q=dnf&tag=linux"), "search + tag filter finds the note")
check(ids("/api/search?q=dnf&since=2999-01-01") == set(), "search honours the date filter")

# ── saved searches
r = cl.post("/api/saved-searches", json={"q": "dnf", "tag": "linux", "type": "text"})
check(r.status_code == 201 and r.get_json()["item"]["name"] == "dnf", "saved search is named from its query")
check(cl.post("/api/saved-searches", json={}).status_code == 400, "an empty search can't be saved")
sid = r.get_json()["item"]["id"]
check(len(cl.get("/api/saved-searches").get_json()["items"]) == 1, "saved searches list")
check(cl.delete(f"/api/saved-searches/{sid}").get_json()["items"] == [], "saved search deleted")

# ── thumbnails
atts = cl.get(f"/api/notes/{c}").get_json()["attachments"]
t = cl.get(f"/api/attachments/{atts[0]['id']}/thumb")
check(t.status_code in (200, 302), "thumbnail route answers")
if t.status_code == 200:
    check(t.mimetype == "image/jpeg", "thumbnail is a JPEG")
vid = cl.get(f"/api/notes/{v}").get_json()["attachments"][0]["id"]
check(cl.get(f"/api/attachments/{vid}/thumb").status_code == 404, "audio has no thumbnail")

# ── duplicates + merge
d1 = mk("Call the dentist on Tuesday to book a cleaning appointment")
d2 = mk("Call the dentist on Tuesday to book a cleaning appointment")
d3 = mk("Call the dentist on Tuesday to book a cleaning and also ask about braces")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hg_agent"))
from memos_daemon import insights
db.execute(insights.DUP_TABLE)
n = insights.find_duplicates(db)
check(n >= 1, "agent finds identical notes")
unrelated = insights.is_duplicate("Grocery list milk eggs bread", "Fedora tip dnf history undo reverts")
check(not unrelated, "unrelated notes are not duplicates")
check(insights.is_duplicate("Call the dentist Tuesday to book a cleaning", "Call the dentist Tuesday to book a cleaning and ask about braces"),
      "a note that contains the other is a duplicate")
cl.post(f"/api/notes/{d1}/attachments", data={"file": (io.BytesIO(PNG), "p.png", "image/png"), "kind": "image", "meta": "{}"},
        content_type="multipart/form-data")
items = cl.get("/api/duplicates").get_json()["items"]
check(items and {items[0]["a"]["id"], items[0]["b"]["id"]} <= {d1, d2, d3}, "duplicates list returns both notes")
check(cl.get("/api/home").get_json()["dups"] >= 1, "home reports pending duplicates")
pair = next(x for x in items if {x["a"]["id"], x["b"]["id"]} == {d1, d2})
m = cl.post(f"/api/duplicates/{pair['id']}/merge", json={"keep": d1})
check(m.status_code == 200, "merge succeeds")
check(cl.get(f"/api/notes/{d2}").status_code == 404, "the folded note is gone")
check(len(cl.get(f"/api/notes/{d1}").get_json()["attachments"]) == 1, "the kept note still has its photo")
check(cl.get(f"/api/notes/{d1}").get_json()["body"].count("dentist") == 1, "identical text is not doubled")
check(any(x["id"] == d2 for x in cl.get("/api/trash").get_json()["items"]), "the folded note is in Trash")
check(cl.post(f"/api/duplicates/{pair['id']}/merge", json={"keep": d1}).status_code == 404, "a merged suggestion can't merge twice")
rest = [x for x in cl.get("/api/duplicates").get_json()["items"] if d2 in (x["a"]["id"], x["b"]["id"])]
check(not rest, "suggestions involving the folded note are cleared")
check(cl.post("/api/duplicates/999/dismiss").get_json()["ok"], "dismiss is harmless for unknown ids")

# ── weekly digest
from datetime import datetime, timedelta
now = datetime.now()
obj = insights.compose_digest(db, now, complete=lambda p, max_tokens=0: "A busy week about Fedora and errands.")
check(obj and obj["counts"]["notes"] >= 4 and obj["generated_by"] == "model", "digest counts the week's notes")
obj2 = insights.compose_digest(db, now, complete=lambda p, max_tokens=0: "")
check(obj2["generated_by"] == "facts" and "note" in obj2["text"], "digest falls back to facts when the model returns nothing")
check(any(t["tag"] == "linux" for t in obj["top_tags"]), "digest names the week's topics")
check(insights.due(db, now.replace(hour=19) if now.weekday() == 6 else now, 6, 18) in (True, False), "due() runs")
monday = now - timedelta(days=now.weekday())
check(insights.due(db, monday.replace(hour=9), 6, 18), "due when no digest exists yet")
db.execute("INSERT INTO settings (key,value) VALUES ('weekly_digest', ?)", (json.dumps(obj),)); db.commit()
check(not insights.due(db, now, 6, 18) or now.weekday() == 6, "not due again right after one was written (before the next Sunday 18:00)")
home = cl.get("/api/home").get_json()
check(home["digest"] and home["digest"]["text"].startswith("A busy week"), "home carries the digest")
check(cl.get("/api/digest").get_json()["digest"]["week"] == obj["week"], "digest endpoint returns it")
cl.post("/api/digest/dismiss")
check(cl.get("/api/home").get_json()["digest"] is None, "dismissed digest leaves the home screen")
cl.post("/api/digest/refresh")
check(db.execute("SELECT value FROM settings WHERE key='digest_refresh_requested'").fetchone()[0] == "1", "refresh flags the agent")
check(insights.due(db, now, 6, 18), "a refresh request makes the digest due")

# ── health
h = cl.get("/api/health").get_json()
check({"agent", "versions", "jobs", "storage", "notes"} <= set(h), "health has its sections")
check(h["storage"]["db_bytes"] > 0 and h["notes"] >= 4, "health reports storage and note count")
db.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('agent_telemetry', ?)", (json.dumps({"version": "0.0.1", "engine": "medha"}),)); db.commit()
h = cl.get("/api/health").get_json()
check(h["versions"]["match"] is False and h["agent"]["engine"] == "medha", "version mismatch and engine are reported")

# ── story from a photo
def post_story(nid, **kw): return cl.post(f"/api/notes/{nid}/story", json=kw)
check(post_story("nope").status_code == 404, "story: unknown note is 404")
check(post_story(a, outline="x").status_code == 400, "story: note without a photo is 400")
check(post_story(c, outline="an idea").status_code == 409, "story: a photo still being read waits (409 pending)")
db.execute("UPDATE attachments SET transcript='', transcript_status='done' WHERE note_id=?", (c,)); db.commit()   # OCR ran, found no text
r = post_story(c)
check(r.status_code == 409, "story: photo with no description, no idea and no note words is 409")
r = post_story(c, outline="I am late for the movie")
ctx0 = json.loads(db.execute("SELECT context_json FROM ask_requests WHERE id=?", (r.get_json()["request_id"],)).fetchone()[0])
check(r.status_code == 201 and ctx0[0]["text"] == "", "story: an idea alone is enough when the photo has no description")
db.execute("UPDATE attachments SET transcript='Description: A red fox on a snowy hill.', transcript_status='done' WHERE note_id=?", (c,)); db.commit()
r = post_story(c, outline="A fox who finds a lantern", genre="mystery", length="medium", lang="te")
check(r.status_code == 201, "story: 201 once the photo is described")
rid = r.get_json()["request_id"]
row = db.execute("SELECT * FROM ask_requests WHERE id=?", (rid,)).fetchone()
opts = json.loads(row["history_json"]); ctx = json.loads(row["context_json"])
check(row["mode"] == "story" and row["status"] == "pending", "story: row is a pending story request")
check(row["question"] == "A fox who finds a lantern", "story: outline stored as the question")
check(opts["genre"] == "mystery" and opts["length"] == "medium" and opts["lang"] == "te", "story: options stored")
check("red fox" in ctx[0]["text"], "story: photo description is the context")
r = post_story(c, genre="bogus", length="huge", lang="xx")
o = json.loads(db.execute("SELECT history_json FROM ask_requests WHERE id=?", (r.get_json()["request_id"],)).fetchone()[0])
check(o["genre"] == "" and o["length"] == "short" and o["lang"] == "en", "story: invalid options fall back to defaults")
check(cl.get("/api/version").get_json()["api"] >= 3, "story: API level is 3+")

# ── story revision
r = post_story(c, revise="# T\n\nOnce.", instruction="make it funnier")
check(r.status_code == 201, "revise: 201")
row = db.execute("SELECT question, history_json FROM ask_requests WHERE id=?", (r.get_json()["request_id"],)).fetchone()
check(row["question"] == "make it funnier" and json.loads(row["history_json"])["revise"].startswith("# T"), "revise: instruction + story stored")
check(post_story(c, revise="# T\n\nOnce.").status_code == 400, "revise: instruction required")
db.execute("UPDATE attachments SET transcript='', transcript_status='skipped'"); db.commit()
check(post_story(c, revise="# T\n\nOnce.", instruction="x").status_code == 201, "revise works even without a description")
# ── highlights: `==` in code is not a highlight
code = 'def inside():\n    return git_out("rev-parse","--is-inside-work-tree")=="true"\ndef g(y):\n    return y[0]=="a" and d["k"]=="b" and (m)=="c"\ndef f(x):\n    if x == "true" or y=="no":\n        return a != b and c <= d\n    z = (p == q) and (r == s)'
hc = mk(code)
hr = mk("Remember \u2063\u2060the milk\u2063\u2063 and =={g}the eggs== today, plus a == b, ==plain== and `x == y` inline.")
hl = cl.get("/api/highlights").get_json()["highlights"]
check(not [h for h in hl if h["note_id"] == hc], "highlights: code with == yields none")
txt = sorted((h["text"], h["color"]) for h in hl if h["note_id"] == hr)
check(txt == [("the eggs", "g"), ("the milk", "y")], f"highlights: real markers still found {txt}")
sys.path.insert(0, "/tmp/hg_agent")
from memos_daemon.anchor import strip_highlights
check(strip_highlights(code) == code, "agent strip_highlights leaves code alone")
check(strip_highlights("a \u2063\u2060b c\u2063\u2063 d =={p}e f==") == "a b c d e f", "agent strip_highlights unwraps real ones")
check(strip_highlights("keep ==plain== as is") == "keep ==plain== as is", "plain == is no longer a highlight")
# ── migration of the old =={c}text== form
import importlib
m1 = mk("old =={g}green== and code `=={y}x==` and a == b")
db.execute("DELETE FROM settings WHERE key='hl_migrated_v2'"); db.execute("UPDATE notes SET body='old =={g}green== and `=={y}x==` here' WHERE id=?", (m1,)); db.commit()
before = db.execute("SELECT updated_at FROM notes WHERE id=?", (m1,)).fetchone()[0]
with qs.app.app_context():
    n_mig = qs._migrate_highlights(qs.get_db())
row = db.execute("SELECT body, updated_at FROM notes WHERE id=?", (m1,)).fetchone()
check(n_mig >= 1 and row["body"] == "old \u2063\u2061green\u2063\u2063 and `=={y}x==` here", f"migration converts real highlights, skips code: {row['body']!r}")
check(row["updated_at"] == before, "migration keeps updated_at")
with qs.app.app_context():
    check(qs._migrate_highlights(qs.get_db()) == 0, "migration runs once")
# ── a pile of real code: nothing may be read as a highlight
import re as _r
codes = ['if a == b:\n    pass', 'x=="y"', 'return git_out("a")=="true"', 'if (a === b && c == d) {}', "[ \"$a\" == \"$b\" ]",
         'SELECT * FROM t WHERE a == 1', 'a ==b== c', 'assert x==1 and y !== 2', 'v=d["k"]=="b" and (m)=="c"', '==== header ====', 'a <== b']
for cs in codes:
    check(qs.strip_hl(cs) == cs and not list(qs.HL_RE.finditer(cs)), f"code is not a highlight: {cs!r}")
# ── list caps: open tasks always complete, finished history bounded
cn = mk("task holder note")
for i in range(230):
    db.execute("INSERT INTO action_items (id, note_id, text, done, done_at) VALUES (?,?,?,?,?)",
               (f"cap{i}", cn, f"finished {i}", 1, f"2024-01-01T00:{i//60:02d}:{i%60:02d}.000Z"))
for i in range(12):
    db.execute("INSERT INTO action_items (id, note_id, text, done) VALUES (?,?,?,0)", (f"open{i}", cn, f"open {i}"))
db.commit()
acts = cl.get("/api/actions").get_json()["actions"]
check(sum(1 for x in acts if not x["done"]) >= 12 and sum(1 for x in acts if x["done"]) == 200, "actions: all open + newest 200 finished")
check(acts[0]["done"] == 0 and acts[-1]["done"] == 1, "actions: open first, finished after")
check(next(x for x in acts if x["done"])["text"] == "finished 229", "actions: finished are newest-first")
check(all(not x["done"] for x in cl.get("/api/actions?done=0").get_json()["actions"]), "actions: done=0 still works")
check(len(cl.get("/api/actions?done=1").get_json()["actions"]) == 200, "actions: done=1 is capped too")
for i in range(130):
    db.execute("INSERT INTO reminders (id, note_id, title, datetime_resolved) VALUES (?,?,?,?)", (f"rem{i}", cn, f"old {i}", f"2020-01-01T00:{i//60:02d}:{i%60:02d}.000Z"))
db.execute("INSERT INTO reminders (id, note_id, title, datetime_resolved) VALUES ('remnew', ?, 'upcoming', '2999-01-01T00:00:00.000Z')", (cn,))
db.execute("INSERT INTO reminders (id, note_id, title) VALUES ('remnone', ?, 'undated')", (cn,))
db.commit()
rems = cl.get("/api/reminders").get_json()["reminders"]
check(len([r for r in rems if r["title"].startswith("old ")]) == 100, "reminders: old ones capped at the newest 100")
check(any(r["title"] == "upcoming" for r in rems) and any(r["title"] == "undated" for r in rems), "reminders: upcoming and undated always kept")

# ── typing: a never-enriched note is held until the editor commits ──
st = lambda i: db.execute("SELECT status FROM note_state WHERE note_id=?", (i,)).fetchone()["status"]
t1 = mk("Plumber coming Thursday after five, ask about the tap washer too")
check(st(t1) == "raw", "a new note starts raw")
cl.patch(f"/api/notes/{t1}", json={"body": "Plumber coming Thursday after five, ask about the tap washer too, and the geyser"})
check(st(t1) == "raw", "a write from elsewhere (API, shortcut) still leaves it raw, so it is enriched as before")
cl.patch(f"/api/notes/{t1}", json={"body": "Plumber coming Thursday after five, ask about the tap washer too, and the geyser thermostat", "autosave": True})
check(st(t1) == "dirty", "the editor's autosave holds a never-enriched note as dirty (not queued mid-typing)")
cl.patch(f"/api/notes/{t1}", json={"body": "Plumber coming Thursday after five, ask about the tap washer too, and the geyser thermostat. Done.", "commit": True})
check(st(t1) == "force", "closing the editor commits it: now it is queued")
t = P + F
print(f"\n  {'\033[32m' if not F else '\033[31m'}{P}/{t} passed{'  all green ✨' if not F else '  FAILED'}\033[0m")
sys.exit(1 if F else 0)
