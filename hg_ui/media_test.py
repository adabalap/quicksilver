#!/usr/bin/env python3
"""Attachments (photo / voice / sketch), search over transcripts, import, export."""
import sys, os, io, json, zipfile, tempfile, base64
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from e2e_test import load_app
P = F = 0
def check(c, m):
    global P, F
    if c: P += 1
    else: F += 1; print(f"  \033[31m✗ {m}\033[0m")

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 60 + b"\xff\xd9"
WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 200
tmp = tempfile.mkdtemp()
qs = load_app(os.path.join(tmp, "m.db"))
cl = qs.app.test_client()
db = qs.sqlite3.connect(os.path.join(tmp, "m.db")); db.row_factory = qs.sqlite3.Row

def up(nid, data, mime, kind, meta=None, name="x"):
    return cl.post(f"/api/notes/{nid}/attachments", data={
        "file": (io.BytesIO(data), name, mime), "kind": kind, "meta": json.dumps(meta or {})},
        content_type="multipart/form-data")

# ── media notes may start empty; text notes still may not
check(cl.post("/api/notes", json={"body": ""}).status_code == 400, "empty text note is still rejected")
n = cl.post("/api/notes", json={"body": "", "media": "voice"})
check(n.status_code == 201, "an empty voice note is accepted")
nid = n.get_json()["id"]

# ── upload validation
r = up(nid, WEBM, "audio/webm;codecs=opus", "audio", {"duration_s": 12.5})
check(r.status_code == 201, "voice upload accepted (codecs param tolerated)")
a = r.get_json()
check(a["kind"] == "audio" and a["transcript_status"] == "pending" and a["duration_s"] == 12.5, "voice starts pending with duration")
check(up(nid, b"not audio at all", "audio/webm", "audio").status_code == 415, "wrong magic bytes rejected")
check(up(nid, WEBM, "audio/webm", "image").status_code == 415, "audio can't be filed as image")
check(up(nid, b"<svg onload=alert(1)>", "image/svg+xml", "image").status_code == 415, "SVG refused")
check(up(nid, b"", "image/png", "image").status_code in (400, 415), "empty upload refused")
check(up(nid, PNG, "image/png", "bogus").status_code in (400, 415), "unknown kind refused")
check(not [f for f in os.listdir(os.path.join(tmp, "attachments")) if f.startswith(".")], "no partial files left behind")
# idempotent replay (offline outbox retry)
r1 = up(nid, PNG, "image/png", "image", {"id": "Abc12345XY", "width": 1, "height": 1})
r2 = up(nid, PNG, "image/png", "image", {"id": "Abc12345XY"})
check(r1.status_code == 201 and r2.status_code == 200 and r1.get_json()["id"] == r2.get_json()["id"], "replaying an upload doesn't duplicate")
check(db.execute("SELECT COUNT(*) FROM attachments WHERE id='Abc12345XY'").fetchone()[0] == 1, "one row after replay")
check(".." not in db.execute("SELECT filename FROM attachments WHERE id='Abc12345XY'").fetchone()[0], "stored filename is generated")

# ── serving
f = cl.get("/api/attachments/Abc12345XY/file")
check(f.status_code == 200 and f.data == PNG and f.headers["Content-Type"].startswith("image/png"), "file served with its type")
check(f.headers.get("X-Content-Type-Options") == "nosniff", "nosniff header set")
rg = cl.get(f"/api/attachments/{a['id']}/file", headers={"Range": "bytes=0-9"})
check(rg.status_code == 206 and len(rg.data) == 10, "Range requests work (audio seeking)")
check(cl.get("/api/attachments/nope/file").status_code == 404, "unknown attachment 404s")

# ── note JSON carries attachments
note = cl.get(f"/api/notes/{nid}").get_json()
check(len(note["attachments"]) == 2 and "filename" not in json.dumps(note["attachments"]), "note lists attachments without file paths")

# ── transcripts: edit, search, retranscribe
p = cl.patch(f"/api/attachments/{a['id']}", json={"transcript": "remember to renew the passport in March"})
check(p.get_json()["transcript_status"] == "done" and p.get_json()["transcript_model"] == "user", "hand-edited transcript is final")
hits = [x["id"] for x in cl.get("/api/search?q=passport").get_json()["notes"]]
check(nid in hits, "search finds a note by its voice transcript")
hits2 = [x["id"] for x in cl.get("/api/notes?q=renew").get_json()["notes"]] if cl.get("/api/notes?q=renew").status_code == 200 else []
rt = cl.post(f"/api/attachments/{a['id']}/retranscribe").get_json()
check(rt["transcript_status"] == "pending", "re-transcribe puts it back in the queue")
# browser-supplied transcript is stored as done
r3 = up(nid, WEBM, "audio/webm", "audio", {"transcript": "hello from the browser"})
check(r3.get_json()["transcript_status"] == "done", "browser dictation arrives as done")
r4 = up(nid, WEBM, "audio/webm", "audio", {"transcribe": False})
check(r4.get_json()["transcript_status"] == "none", "transcribe:false → none")

# ── sketches: strokes + replace
sk = up(nid, PNG, "image/png", "sketch", {"strokes": {"w": 100, "h": 80, "s": [[[1, 2, .5], [3, 4, .5]]]}, "width": 100, "height": 80}).get_json()
check(sk["has_strokes"], "sketch keeps its vector strokes")
check(cl.get(f"/api/attachments/{sk['id']}/strokes").get_json()["strokes"]["w"] == 100, "strokes can be fetched for editing")
rp = cl.put(f"/api/attachments/{sk['id']}", data={"file": (io.BytesIO(PNG), "s", "image/png"), "kind": "sketch",
            "meta": json.dumps({"strokes": {"w": 100, "h": 80, "s": []}})}, content_type="multipart/form-data")
check(rp.status_code == 200 and rp.get_json()["transcript_status"] == "pending", "editing a sketch re-queues reading it")
check(cl.put("/api/attachments/Abc12345XY", data={"file": (io.BytesIO(PNG), "s", "image/png"), "kind": "sketch"},
             content_type="multipart/form-data").status_code == 400, "photos can't be 'edited' as sketches")
check(up(nid, PNG, "image/png", "sketch", {"strokes": "x" * 3_000_000}).status_code == 413, "oversized stroke data refused")

# ── per-note cap
n2 = cl.post("/api/notes", json={"body": "cap test note with enough words"}).get_json()["id"]
codes = [up(n2, PNG, "image/png", "image").status_code for _ in range(26)]
check(codes.count(201) == 24 and codes[-1] == 400, "a note holds at most 24 attachments")

# ── delete removes the file; deleting a note removes all its files
aid = r4.get_json()["id"]; fn = db.execute("SELECT filename FROM attachments WHERE id=?", (aid,)).fetchone()[0]
check(cl.delete(f"/api/attachments/{aid}").status_code == 200 and not os.path.exists(os.path.join(tmp, "attachments", fn)), "deleting an attachment removes its file")
_nf = lambda: len([f for f in os.listdir(os.path.join(tmp, "attachments")) if os.path.isfile(os.path.join(tmp, "attachments", f))])
files_before = _nf()
cl.delete(f"/api/notes/{n2}")
check(_nf() == files_before - 24, "deleting a note takes its attachment files out of the live folder")
check(db.execute("SELECT COUNT(*) FROM attachments WHERE note_id=?", (n2,)).fetchone()[0] == 0, "…and rows")
s = cl.get("/api/media/summary").get_json()
check(s["count"] >= 4 and s["bytes"] > 0 and "audio" in s["by_kind"], "media summary counts")


# ── EXPORT → IMPORT round trip
db.execute("INSERT INTO tags (note_id, tag) VALUES (?, 'trip')", (nid,))
db.execute("INSERT INTO action_items (id, note_id, text, done) VALUES ('t1', ?, 'renew passport', 0)", (nid,))
db.commit()
t = cl.post("/api/notes", json={"body": "Plain note about the Goa trip #travel"}).get_json()["id"]
ex = cl.get("/api/export")
check(ex.status_code == 200 and ex.headers["Content-Type"] == "application/zip", "export downloads a zip")
z = zipfile.ZipFile(io.BytesIO(ex.data))
names = z.namelist()
check("notes.json" in names and any(x.startswith("markdown/") for x in names) and any(x.startswith("attachments/") for x in names), "export holds notes.json, markdown and attachments")
data = json.loads(z.read("notes.json"))
check(data["format"] == "quicksilver-export" and data["count"] == 2, "export manifest")
vn = next(x for x in data["notes"] if x["id"] == nid)
check(vn["tags"] == ["trip"] and vn["action_items"][0]["text"] == "renew passport" and len(vn["attachments"]) >= 3, "export carries tags, tasks, attachments")
md = next(z.read(x).decode() for x in names if x.startswith("markdown/") and nid in x)
check("passport" in md and "attachments/" in md, "markdown twin links media and quotes the transcript")
check(cl.get("/api/export?hidden=0&media=0").status_code == 200, "export options accepted")
nm = len([x for x in zipfile.ZipFile(io.BytesIO(cl.get("/api/export?media=0").data)).namelist() if x.startswith("attachments/")])
check(nm == 0, "media=0 leaves the files out")

# import into a FRESH app from that export
tmp2 = tempfile.mkdtemp()
qs2 = load_app(os.path.join(tmp2, "m2.db"))
c2 = qs2.app.test_client()
up_r = c2.post("/api/import/upload", data={"file": (io.BytesIO(ex.data), "export.zip")}, content_type="multipart/form-data")
pv = up_r.get_json()
check(up_r.status_code == 200 and pv["format"] == "quicksilver" and pv["to_import"] == 2, "export is recognised on import")
cm = c2.post(f"/api/import/{pv['token']}/commit", json={}).get_json()
check(cm["imported"] == 2 and cm["attachments"] >= 3 and cm["tasks"] == 1, "round trip restores notes, attachments, tasks")
db2 = qs2.sqlite3.connect(os.path.join(tmp2, "m2.db")); db2.row_factory = qs2.sqlite3.Row
check(db2.execute("SELECT COUNT(*) FROM tags WHERE tag='trip'").fetchone()[0] == 1, "tags restored")
check(db2.execute("SELECT COUNT(*) FROM attachments WHERE transcript LIKE '%hello from the browser%'").fetchone()[0] == 1, "transcripts restored")
check(len(os.listdir(os.path.join(tmp2, "attachments"))) >= 3, "files restored")
up2 = c2.post("/api/import/upload", data={"file": (io.BytesIO(ex.data), "export.zip")}, content_type="multipart/form-data").get_json()
check(up2["to_import"] == 0, "importing the same export again adds nothing (dedupe)")
check(c2.post(f"/api/import/{pv['token']}/commit", json={}).status_code == 404, "an import token is single-use")

# ── Google Keep Takeout (with a photo and a voice memo)
def keep_zip():
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as zf:
        zf.writestr("Takeout/Keep/Groceries.json", json.dumps({
            "title": "Groceries", "isPinned": True, "createdTimestampUsec": 1_700_000_000_000_000,
            "userEditedTimestampUsec": 1_700_000_100_000_000, "labels": [{"name": "Home Stuff"}],
            "listContent": [{"text": "milk", "isChecked": False}, {"text": "eggs", "isChecked": True}]}))
        zf.writestr("Takeout/Keep/Whiteboard.json", json.dumps({
            "title": "Whiteboard", "textContent": "", "createdTimestampUsec": 1_690_000_000_000_000,
            "attachments": [{"filePath": "board.jpg", "mimetype": "image/jpeg"},
                            {"filePath": "memo.3gp", "mimetype": "audio/3gpp"}]}))
        zf.writestr("Takeout/Keep/board.jpg", JPG)
        zf.writestr("Takeout/Keep/memo.3gp", b"\x00\x00\x00\x18ftyp3gp4" + b"\x00" * 50)
        zf.writestr("Takeout/Keep/Old.json", json.dumps({"title": "Old", "textContent": "bin me", "isTrashed": True,
                                                         "createdTimestampUsec": 1_600_000_000_000_000}))
        zf.writestr("Takeout/Keep/Empty.json", json.dumps({"title": "", "textContent": ""}))
        zf.writestr("Takeout/Keep/Broken.json", "{not json")
        zf.writestr("Takeout/Keep/Link.json", json.dumps({"title": "Read later", "textContent": "interesting",
            "annotations": [{"url": "https://example.org/x", "title": "Example"}], "createdTimestampUsec": 1_680_000_000_000_000}))
    return b.getvalue()
tmp3 = tempfile.mkdtemp(); qs3 = load_app(os.path.join(tmp3, "k.db")); c3 = qs3.app.test_client()
kp = c3.post("/api/import/upload", data={"file": (io.BytesIO(keep_zip()), "takeout.zip"), "tag_labels": "1"},
             content_type="multipart/form-data").get_json()
check(kp["format"] == "keep" and kp["to_import"] == 3, "Keep export recognised; trash/empty/broken skipped")
check(kp["stats"]["skipped_trashed"] == 1 and kp["stats"]["unparseable"] == 1 and kp["stats"]["skipped_empty"] == 1, "Keep skip counts")
check(kp["stats"]["photos"] == 1 and kp["stats"]["voice"] == 1 and kp["attachments"] == 2, "Keep photo + voice memo found")
rp2 = c3.post(f"/api/import/{kp['token']}/preview", json={"include_trashed": True}).get_json()
check(rp2["to_import"] == 4, "changing options re-previews without re-uploading")
kc = c3.post(f"/api/import/{kp['token']}/commit", json={"tag_labels": True, "transcribe": True}).get_json()
check(kc["imported"] == 3 and kc["attachments"] == 2, "Keep import commits notes + media")
db3 = qs3.sqlite3.connect(os.path.join(tmp3, "k.db")); db3.row_factory = qs3.sqlite3.Row
g = db3.execute("SELECT * FROM notes WHERE body LIKE 'Groceries%'").fetchone()
check(g["pinned"] == 1 and g["note_type"] == "checklist" and "- [x] eggs" in g["body"] and g["created_at"].startswith("2023-11-14"), "checklist, pin and Keep timestamps kept")
check(db3.execute("SELECT tag FROM tags WHERE note_id=?", (g["id"],)).fetchone()[0] == "home_stuff", "labels become tags")
check(db3.execute("SELECT COUNT(*) FROM attachments WHERE transcript_status='pending'").fetchone()[0] == 2, "transcribe option queues the media")
check("https://example.org/x" in db3.execute("SELECT body FROM notes WHERE body LIKE 'Read later%'").fetchone()[0], "Keep link annotations kept")
check(db3.execute("SELECT COUNT(*) FROM notes WHERE enrich_optout=1").fetchone()[0] == 3, "imports don't trigger a flood of AI enrichment")
check(any(f.startswith("pre-import") for f in os.listdir(os.path.join(tmp3, "backups"))), "a safety backup is taken before importing")
k2 = c3.post("/api/import/upload", data={"file": (io.BytesIO(keep_zip()), "takeout.zip")}, content_type="multipart/form-data").get_json()
check(k2["to_import"] == 0, "re-importing Keep adds nothing")

# ── Markdown files
md_up = c3.post("/api/import/upload", data={"file": [(io.BytesIO(b"---\ntags: [ideas, work]\ncreated: 2024-02-03\n---\nBody of the first file"), "First idea.md"),
                                                      (io.BytesIO(b"# Second\nplain text"), "second.txt")]},
                content_type="multipart/form-data").get_json()
check(md_up["format"] == "markdown" and md_up["to_import"] == 2, "loose Markdown/text files import")
c3.post(f"/api/import/{md_up['token']}/commit", json={})
fi = db3.execute("SELECT * FROM notes WHERE body LIKE '%Body of the first file%'").fetchone()
check(fi["created_at"].startswith("2024-02-03") and fi["body"].startswith("First idea"), "front-matter date and filename title used")
check({r[0] for r in db3.execute("SELECT tag FROM tags WHERE note_id=?", (fi["id"],))} == {"ideas", "work"}, "front-matter tags used")

# ── hostile archives
bad = io.BytesIO(); 
with zipfile.ZipFile(bad, "w") as zf: zf.writestr("readme.pdf", "nope")
check(c3.post("/api/import/upload", data={"file": (io.BytesIO(bad.getvalue()), "a.zip")}, content_type="multipart/form-data").status_code == 400, "an archive with nothing importable is refused")
check(c3.post("/api/import/upload", data={"file": (io.BytesIO(b"not a zip"), "a.zip")}, content_type="multipart/form-data").status_code == 400, "a non-zip is refused")
check(c3.post("/api/import/" + "0" * 32 + "/commit", json={}).status_code == 404, "unknown token 404s")
check(c3.post("/api/import/../../etc/passwd/commit", json={}).status_code in (404, 405), "no path games with tokens")

# ── TRASH: delete keeps a snapshot until restored / purged
check(cl.get("/api/trash").get_json()["items"][0]["id"] == n2, "deleted note is listed in trash")
check(cl.get(f"/api/notes/{n2}").status_code == 404, "…and gone from the notes")
tl = cl.get("/api/trash").get_json()["items"][0]
check(tl["attachments"] == 24 and tl["days_left"] >= 29, "trash shows attachment count and days left")
check(len(os.listdir(os.path.join(tmp, "attachments", "_trash"))) == 24, "attachment files are parked in _trash")
tn = cl.post("/api/notes", json={"body": "Trash round trip note with a #keep tag"}).get_json()["id"]
db.execute("INSERT INTO action_items (id, note_id, text, done) VALUES ('tr1', ?, 'call mum', 0)", (tn,)); db.commit()
tp = up(tn, PNG, "image/png", "image").get_json()["id"]
cl.delete(f"/api/notes/{tn}")
check(db.execute("SELECT COUNT(*) FROM action_items WHERE note_id=?", (tn,)).fetchone()[0] == 0, "trashed note's tasks leave the live task list")
r = cl.post(f"/api/trash/{tn}/restore")
check(r.status_code == 200, "restore succeeds")
g = cl.get(f"/api/notes/{tn}").get_json()
check(g["body"].startswith("Trash round trip") and len(g["attachments"]) == 1, "restored note has body and attachment")
check(cl.get(f"/api/attachments/{tp}/file").status_code == 200, "restored attachment file is served again")
check(db.execute("SELECT COUNT(*) FROM action_items WHERE note_id=?", (tn,)).fetchone()[0] == 1, "restored note has its tasks")
check(db.execute("SELECT COUNT(*) FROM tags WHERE note_id=?", (tn,)).fetchone()[0] >= 1, "restored note has its tags")
check(cl.post(f"/api/trash/{tn}/restore").status_code == 404, "restored note is no longer in trash")
check(cl.delete(f"/api/trash/{n2}").status_code == 200 and not os.listdir(os.path.join(tmp, "attachments", "_trash")), "delete forever removes snapshot and files")
pn = cl.post("/api/notes", json={"body": "gone for good"}).get_json()["id"]
check(cl.delete(f"/api/notes/{pn}?permanent=1").get_json()["trashed"] is False, "permanent delete skips trash")
a1 = cl.post("/api/notes", json={"body": "empty me 1"}).get_json()["id"]; a2 = cl.post("/api/notes", json={"body": "empty me 2"}).get_json()["id"]
cl.delete(f"/api/notes/{a1}"); cl.delete(f"/api/notes/{a2}")
check(cl.delete("/api/trash").get_json()["emptied"] >= 2 and cl.get("/api/trash").get_json()["items"] == [], "empty trash clears everything")
oldn = cl.post("/api/notes", json={"body": "ancient"}).get_json()["id"]; cl.delete(f"/api/notes/{oldn}")
db.execute("UPDATE trash SET deleted_at='2020-01-01T00:00:00.000Z' WHERE id=?", (oldn,)); db.commit()
check(cl.get("/api/trash").get_json()["items"] == [], "items older than 30 days are purged automatically")


# ── Ask modes: chat (no notes) and write (long-form from notes)
r = cl.post("/api/ask", json={"question": "Explain dnf vs rpm", "mode": "chat"}).get_json()
row = db.execute("SELECT mode, context_json FROM ask_requests WHERE id=?", (r["request_id"],)).fetchone()
check(r["status"] == "pending" and row["mode"] == "chat" and row["context_json"] == "[]", "chat mode sends no notes")
for i, b in enumerate(["Fedora tip: dnf history undo reverts the last transaction safely.",
                       "Fedora notes: enable rpmfusion free and nonfree repositories for codecs.",
                       "Fedora upgrade: dnf system-upgrade download --releasever=44 then reboot."]):
    cl.post("/api/notes", json={"body": b})
w = cl.post("/api/ask", json={"question": "Build me a white paper based on my notes about Fedora", "mode": "write"}).get_json()
wrow = db.execute("SELECT mode, context_json FROM ask_requests WHERE id=?", (w["request_id"],)).fetchone()
ctx = json.loads(wrow["context_json"])
check(wrow["mode"] == "write" and len(ctx) >= 2 and all("Fedora" in c["text"] or "fedora" in c["text"].lower() for c in ctx),
      "write mode retrieves the matching notes (instruction words ignored)")
check(cl.get("/api/ask/" + w["request_id"]).get_json()["mode"] == "write", "mode is reported on re-fetch")
none = cl.post("/api/ask", json={"question": "Write a blog post about zzqxv", "mode": "write"}).get_json()
check(none["status"] == "done" and "couldn't find notes" in none["answer"], "write with no matching notes answers honestly")

# ── CLI import still works (same engine)
import subprocess
kz = os.path.join(tmp3, "t.zip"); open(kz, "wb").write(keep_zip().replace(b"Groceries", b"Groceriez"))
env = dict(os.environ, QS_DB=os.path.join(tmp3, "k.db"))
out = subprocess.run([sys.executable, "app.py", "import-keep", kz, "--dry-run"], cwd=os.path.dirname(os.path.abspath(__file__)),
                     env=env, capture_output=True, text=True)
check(out.returncode == 0 and "to import" in out.stdout and "dry run" in out.stdout, "CLI dry run works")

t = P + F
print(f"\n  {'\033[32m' if not F else '\033[31m'}{P}/{t} passed{'  all green ✨' if not F else '  FAILED'}\033[0m")
sys.exit(1 if F else 0)
