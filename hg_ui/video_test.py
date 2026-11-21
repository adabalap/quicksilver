#!/usr/bin/env python3
"""Video notes: upload rules, playback ranges, thumbnails, filters, deletion, and the one-time table migration."""
import sys, os, io, json, tempfile, subprocess, shutil, sqlite3
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from e2e_test import load_app
P = F = 0
def check(c, m):
    global P, F
    if c: P += 1
    else: F += 1; print(f"  \033[31m✗ {m}\033[0m")

tmp = tempfile.mkdtemp()
# a real 1-second clip (picture + tone) so thumbnails can be cut from it
clip = os.path.join(tmp, "c.mp4"); HAVE_FF = bool(shutil.which("ffmpeg"))
if HAVE_FF:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=2",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", clip], check=True)
    MP4 = open(clip, "rb").read()
else:
    MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 400

# ── migration: an old database whose attachments table only allows photo / voice / sketch
old = os.path.join(tmp, "old.db"); o = sqlite3.connect(old)
o.executescript("""CREATE TABLE attachments (id TEXT PRIMARY KEY, note_id TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('image','audio','sketch')),
  mime TEXT NOT NULL, filename TEXT NOT NULL, size INTEGER NOT NULL DEFAULT 0, width INTEGER, height INTEGER, duration_s REAL, strokes TEXT,
  transcript TEXT NOT NULL DEFAULT '', transcript_status TEXT NOT NULL DEFAULT 'pending' CHECK(transcript_status IN ('none','pending','running','done','skipped','failed')),
  transcript_error TEXT, transcript_model TEXT, created_at TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT '');
  INSERT INTO attachments (id,note_id,kind,mime,filename,size,transcript) VALUES ('OldAtt0001','n1','audio','audio/webm','OldAtt0001.webm',5,'kept words');""")
o.commit(); o.close()
qo = load_app(old); d = sqlite3.connect(old)
sql = d.execute("SELECT sql FROM sqlite_master WHERE name='attachments'").fetchone()[0]
check("'video'" in sql, "old database is migrated to accept video")
check(d.execute("SELECT transcript FROM attachments WHERE id='OldAtt0001'").fetchone()[0] == "kept words", "migration keeps existing attachments")
check(d.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='idx_att_note'").fetchone()[0] == 1, "indexes recreated after migration")

# ── fresh app
qs = load_app(os.path.join(tmp, "v.db")); cl = qs.app.test_client()
db = qs.sqlite3.connect(os.path.join(tmp, "v.db")); db.row_factory = qs.sqlite3.Row
def up(nid, data, mime, kind="video", meta=None):
    return cl.post(f"/api/notes/{nid}/attachments", data={"file": (io.BytesIO(data), "x", mime), "kind": kind, "meta": json.dumps(meta or {})}, content_type="multipart/form-data")

n = cl.post("/api/notes", json={"body": "", "media": "video"})
check(n.status_code == 201, "an empty video note is accepted")
nid = n.get_json()["id"]
r = up(nid, MP4, "video/mp4", meta={"duration_s": 2, "width": 160, "height": 120})
check(r.status_code == 201, "mp4 upload accepted")
a = r.get_json()
check(a["kind"] == "video" and a["transcript_status"] == "pending" and a["duration_s"] == 2, "video starts pending with its length")
check(up(nid, b"not a video at all", "video/mp4").status_code == 415, "wrong magic bytes rejected")
check(up(nid, MP4, "video/x-flv").status_code == 415, "unlisted video type rejected")
check(up(nid, MP4, "video/mp4", meta={"duration_s": 900}).status_code == 413, "a video far over the length limit is refused")
check(up(nid, MP4, "video/mp4", "image").status_code == 415, "a video can't be filed as a photo")
check(up(nid, MP4, "audio/mp4", "audio").status_code == 201, "audio-only mp4 still files as a voice note")

# ── playback
f = cl.get(f"/api/attachments/{a['id']}/file")
check(f.status_code == 200 and f.data == MP4 and f.headers["Content-Type"].startswith("video/mp4"), "video served with its type")
rg = cl.get(f"/api/attachments/{a['id']}/file", headers={"Range": "bytes=0-99"})
check(rg.status_code == 206 and len(rg.data) == 100, "Range requests work (seeking)")
if HAVE_FF:
    t = cl.get(f"/api/attachments/{a['id']}/thumb")
    check(t.status_code == 200 and t.data[:3] == b"\xff\xd8\xff", "a video gets a JPEG thumbnail")
    check(os.path.exists(os.path.join(tmp, "attachments", "_thumbs", a["id"] + ".jpg")), "thumbnail is cached on disk")

# ── lists, filters, summary
check(len(cl.get("/api/notes?type=video").get_json()["notes"]) == 1, "type=video filter finds the note")
check(len(cl.get("/api/notes?type=photo").get_json()["notes"]) == 0, "photo filter does not")
s = cl.get("/api/media/summary").get_json()
check(s["by_kind"].get("video") == 1, "media summary counts videos")
note = cl.get(f"/api/notes/{nid}").get_json()
check(any(x["kind"] == "video" for x in note["attachments"]) and "filename" not in json.dumps(note["attachments"]), "note lists the video without its path")

# ── share target accepts a video file
sh = cl.post("/share-target", data={"title": "", "text": "clip", "media": (io.BytesIO(MP4), "c.mp4", "video/mp4")}, content_type="multipart/form-data")
check(sh.status_code in (302, 303), "share-target accepts a video")
check(db.execute("SELECT COUNT(*) FROM attachments WHERE kind='video'").fetchone()[0] == 2, "shared video stored as a video")


# ── a database that missed the startup migration (agent held the lock) is upgraded on the first video upload
late = os.path.join(tmp, "late.db"); ql = load_app(late); cll = ql.app.test_client()
dl = sqlite3.connect(late)
sqlx = dl.execute("SELECT sql FROM sqlite_master WHERE name='attachments'").fetchone()[0]
dl.execute("PRAGMA foreign_keys=OFF"); dl.execute("ALTER TABLE attachments RENAME TO attachments_new")
dl.execute(sqlx.replace(",'video'", "").replace("attachments", "attachments")); dl.execute("DROP TABLE attachments_new"); dl.commit()
check("'video'" not in dl.execute("SELECT sql FROM sqlite_master WHERE name='attachments'").fetchone()[0], "(setup) table is back to the old shape")
nl = cll.post("/api/notes", json={"body": "", "media": "video"}).get_json()["id"]
rl = cll.post(f"/api/notes/{nl}/attachments", data={"file": (io.BytesIO(MP4), "x", "video/mp4"), "kind": "video", "meta": "{}"}, content_type="multipart/form-data")
check(rl.status_code == 201, "the first video upload upgrades the table and succeeds")

# ── delete removes the file
fn = db.execute("SELECT filename FROM attachments WHERE id=?", (a["id"],)).fetchone()[0]
check(cl.delete(f"/api/attachments/{a['id']}").status_code in (200, 204), "delete works")
check(not os.path.exists(os.path.join(tmp, "attachments", fn)), "the video file is removed from disk")

print(f"\n\033[32m{P} passed, {F} failed\033[0m" if not F else f"\n\033[31m{P} passed, {F} failed\033[0m")
sys.exit(1 if F else 0)
