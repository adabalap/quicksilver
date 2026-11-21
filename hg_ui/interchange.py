"""interchange.py — getting notes in and out of Quicksilver.

IN   Google Keep Takeout (.zip), Quicksilver's own export (.zip), and Markdown /
     plain-text files (.md/.txt, loose or zipped — Obsidian vaults, etc.).
OUT  One .zip with everything: notes.json (complete, re-importable), one
     Markdown file per note (readable anywhere), and the attached photos,
     recordings and sketches.

Nothing here touches the network and nothing is extracted to disk except
attachments, which go through mediastore's validation. The same parsing code
backs the in-app importer and the `hg_ui import-keep` command line.
"""
from __future__ import annotations
import io, re, json, time, zipfile, hashlib, posixpath, tempfile
from datetime import datetime, timezone

import mediastore as ms

MAX_MEMBERS = 60_000
MAX_UNCOMPRESSED = 3 * 1024 ** 3          # refuse zip bombs
EXPORT_FORMAT = "quicksilver-export"
EXPORT_VERSION = 1

_AUDIO_EXT = {".3gp": "audio/3gpp", ".m4a": "audio/mp4", ".mp3": "audio/mpeg",
              ".aac": "audio/aac", ".wav": "audio/wav", ".ogg": "audio/ogg",
              ".webm": "audio/webm", ".amr": "audio/amr"}
_IMAGE_EXT = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
              ".webp": "image/webp", ".gif": "image/gif"}


def _sha(s: str) -> str:
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _usec_iso(us):
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(int(us) / 1_000_000))
    except Exception:
        return None


def _classify(name: str, mime: str):
    """('image'|'audio'|None, normalised mime) for an attachment."""
    m = ms.norm_mime(mime)
    ext = posixpath.splitext(name or "")[1].lower()
    if not m or m in ("application/octet-stream",):
        m = _IMAGE_EXT.get(ext) or _AUDIO_EXT.get(ext) or ""
    if m in ms.IMAGE_MIMES:
        return "image", m
    if m in ms.AUDIO_MIMES or m.startswith("audio/"):
        # Normalise odd audio labels (audio/amr etc. we can't validate) to unsupported.
        return ("audio", m) if m in ms.AUDIO_MIMES else (None, m)
    return None, m


# ── zip handling ─────────────────────────────────────────────────────────────
def open_zip(fileobj) -> zipfile.ZipFile:
    z = zipfile.ZipFile(fileobj)
    infos = z.infolist()
    if len(infos) > MAX_MEMBERS:
        raise ValueError("that archive has too many files")
    if sum(i.file_size for i in infos) > MAX_UNCOMPRESSED:
        raise ValueError("that archive is too large to import")
    return z


def _members(z):
    return [i for i in z.infolist() if not i.is_dir()
            and ".." not in i.filename.replace("\\", "/").split("/")]


def detect(z) -> str:
    names = [i.filename.replace("\\", "/") for i in _members(z)]
    low = [n.lower() for n in names]
    for n in names:
        if posixpath.basename(n).lower() == "notes.json" and _looks_like_export(z, n):
            return "quicksilver"
    keepish = [n for n in low if n.endswith(".json") and "/keep/" in "/" + n]
    if keepish:
        return "keep"
    if any(n.endswith(".json") for n in low) and _first_json_is_keep(z, names, low):
        return "keep"
    if any(n.endswith((".md", ".markdown", ".txt")) for n in low):
        return "markdown"
    return "unknown"


def _looks_like_export(z, member) -> bool:
    try:
        with z.open(member) as f:
            head = f.read(400).decode("utf-8", "ignore")
        return EXPORT_FORMAT in head
    except Exception:
        return False


def _first_json_is_keep(z, names, low) -> bool:
    for n, l in zip(names, low):
        if l.endswith(".json"):
            try:
                d = json.loads(z.read(n).decode("utf-8"))
                return isinstance(d, dict) and ("createdTimestampUsec" in d or "textContent" in d
                                                or "listContent" in d)
            except Exception:
                continue
    return False


# ── parsing: Google Keep ─────────────────────────────────────────────────────
def keep_body(d: dict) -> str:
    parts = []
    title = (d.get("title") or "").strip()
    if title:
        parts.append(title)
    lc = d.get("listContent") or []
    if lc:
        for item in lc:
            txt = (item.get("text") or "").strip()
            if txt:
                parts.append(("- [x] " if item.get("isChecked") else "- [ ] ") + txt)
    txt = (d.get("textContent") or "").strip()
    if txt:
        parts.append(txt)
    for a in (d.get("annotations") or []):
        url = (a.get("url") or "").strip()
        if url and url not in "\n".join(parts):
            t = (a.get("title") or "").strip()
            parts.append(f"{t}\n{url}".strip() if t else url)
    return "\n\n".join(p for p in parts if p).strip()


def parse_keep(z, opts) -> tuple[list, dict]:
    """Keep Takeout → rows. `opts`: skip_archived, include_trashed, tag_labels."""
    members = _members(z)
    by_dir_base = {}
    for i in members:
        n = i.filename.replace("\\", "/")
        by_dir_base[(posixpath.dirname(n).lower(), posixpath.basename(n).lower())] = i.filename
    jsons = [i for i in members if i.filename.lower().endswith(".json")]
    keepish = [i for i in jsons if "/keep/" in "/" + i.filename.replace("\\", "/").lower()]
    jsons = keepish or jsons
    stats = {"found": len(jsons), "skipped_dupe": 0, "skipped_empty": 0, "skipped_trashed": 0,
             "skipped_archived": 0, "unparseable": 0, "checklists": 0, "pinned": 0,
             "with_labels": 0, "photos": 0, "voice": 0, "attachments_unsupported": 0}
    rows = []
    for info in jsons:
        try:
            d = json.loads(z.read(info.filename).decode("utf-8"))
            if not isinstance(d, dict):
                raise ValueError
        except Exception:
            stats["unparseable"] += 1
            continue
        if d.get("isTrashed") and not opts.get("include_trashed"):
            stats["skipped_trashed"] += 1; continue
        if d.get("isArchived") and opts.get("skip_archived"):
            stats["skipped_archived"] += 1; continue
        body = keep_body(d)
        folder = posixpath.dirname(info.filename.replace("\\", "/")).lower()
        atts = []
        for a in (d.get("attachments") or []):
            fp = (a.get("filePath") or "").strip()
            member = by_dir_base.get((folder, posixpath.basename(fp).lower()))
            kind, mime = _classify(fp, a.get("mimetype") or "")
            if not member or not kind:
                stats["attachments_unsupported"] += 1
                continue
            atts.append({"member": member, "kind": kind, "mime": mime})
            stats["photos" if kind == "image" else "voice"] += 1
        if not body and not atts:
            stats["skipped_empty"] += 1; continue
        labels = [(l.get("name") or "").strip() for l in (d.get("labels") or [])]
        labels = [l for l in labels if l]
        rows.append({
            "body": body, "pinned": 1 if d.get("isPinned") else 0,
            "archived": 1 if d.get("isArchived") else 0,
            "note_type": "checklist" if d.get("listContent") else "text",
            "created": _usec_iso(d.get("createdTimestampUsec")),
            "updated": _usec_iso(d.get("userEditedTimestampUsec")),
            "tags": [re.sub(r"\W+", "_", l.lower()).strip("_") for l in labels] if opts.get("tag_labels") else [],
            "attachments": atts, "_labels": labels,
            "_key": body or ("att:" + "|".join(a["member"] for a in atts)),
        })
        if d.get("listContent"): stats["checklists"] += 1
        if d.get("isPinned"):    stats["pinned"] += 1
        if labels:               stats["with_labels"] += 1
    return rows, stats


# ── parsing: Markdown / text ─────────────────────────────────────────────────
_FM = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.S)


def parse_markdown_text(name: str, text: str) -> dict | None:
    text = (text or "").replace("\r\n", "\n")
    tags, created = [], None
    m = _FM.match(text)
    if m:
        fm = m.group(1)
        text = text[m.end():]
        tm = re.search(r"^tags:\s*\[(.*?)\]\s*$", fm, re.M)
        if tm:
            tags = [t.strip().strip("'\"#").lower() for t in tm.group(1).split(",") if t.strip()]
        else:
            lm = re.search(r"^tags:\s*\n((?:\s*-\s*.+\n?)+)", fm, re.M)
            if lm:
                tags = [re.sub(r"^\s*-\s*", "", l).strip().strip("'\"#").lower()
                        for l in lm.group(1).splitlines() if l.strip()]
        cm = re.search(r"^(?:created|date)\s*:\s*['\"]?([0-9T:\-\.\+Z ]{10,32})['\"]?\s*$", fm, re.M)
        if cm:
            created = _norm_ts(cm.group(1))
    body = text.strip()
    if not body:
        return None
    stem = posixpath.splitext(posixpath.basename(name))[0].strip()
    first = body.split("\n", 1)[0].lstrip("# ").strip()
    if stem and first.lower() != stem.lower() and not re.fullmatch(r"[0-9a-f\-]{8,}", stem.lower()):
        body = f"{stem}\n\n{body}"
    return {"body": body, "pinned": 0, "archived": 0, "note_type": "text",
            "created": created, "updated": created, "tags": tags,
            "attachments": [], "_key": body}


def _norm_ts(s):
    s = (s or "").strip().replace(" ", "T")
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    except Exception:
        return None


def parse_markdown_zip(z) -> tuple[list, dict]:
    stats = {"found": 0, "skipped_empty": 0, "skipped_dupe": 0, "unparseable": 0}
    rows = []
    for i in _members(z):
        if not i.filename.lower().endswith((".md", ".markdown", ".txt")):
            continue
        stats["found"] += 1
        try:
            r = parse_markdown_text(i.filename, z.read(i.filename).decode("utf-8", "replace"))
        except Exception:
            stats["unparseable"] += 1; continue
        if r is None:
            stats["skipped_empty"] += 1; continue
        rows.append(r)
    return rows, stats


# ── parsing: Quicksilver export ──────────────────────────────────────────────
def parse_qs_export(z) -> tuple[list, dict]:
    member = next(i.filename for i in _members(z)
                  if posixpath.basename(i.filename.replace("\\", "/")) == "notes.json"
                  and _looks_like_export(z, i.filename))
    data = json.loads(z.read(member).decode("utf-8"))
    base = posixpath.dirname(member.replace("\\", "/"))
    stats = {"found": 0, "skipped_empty": 0, "skipped_dupe": 0, "unparseable": 0,
             "photos": 0, "voice": 0, "sketches": 0, "tasks": 0, "attachments_unsupported": 0}
    rows = []
    for n in data.get("notes", []):
        stats["found"] += 1
        body = (n.get("body") or "").strip()
        atts = []
        for a in n.get("attachments", []):
            f = posixpath.join(base, a.get("file", "")) if a.get("file") else ""
            f = f.lstrip("/")
            try:
                z.getinfo(f)
            except KeyError:
                stats["attachments_unsupported"] += 1
                continue
            k = a.get("kind")
            if k not in ms.KINDS:
                stats["attachments_unsupported"] += 1
                continue
            atts.append({"member": f, "kind": k, "mime": a.get("mime") or "",
                         "width": a.get("width"), "height": a.get("height"),
                         "duration_s": a.get("duration_s"), "strokes": a.get("strokes"),
                         "transcript": a.get("transcript") or "",
                         "transcript_status": a.get("transcript_status") or "none"})
            stats[{"image": "photos", "audio": "voice", "sketch": "sketches"}[k]] += 1
        if not body and not atts:
            stats["skipped_empty"] += 1; continue
        tasks = [{"text": t.get("text") or t.get("task") or "", "done": 1 if t.get("done") else 0,
                  "category": t.get("category") or ""} for t in n.get("action_items", [])
                 if (t.get("text") or t.get("task"))]
        stats["tasks"] += len(tasks)
        rows.append({"body": body, "pinned": 1 if n.get("pinned") else 0,
                     "archived": 1 if n.get("archived") else 0,
                     "hidden": 1 if n.get("hidden") else 0,
                     "protected": 1 if n.get("protected") else 0,
                     "note_type": n.get("note_type") if n.get("note_type") in ("text", "checklist", "event") else "text",
                     "created": _norm_ts(n.get("created_at")), "updated": _norm_ts(n.get("updated_at")),
                     "tags": [str(t).lower() for t in n.get("tags", [])], "attachments": atts,
                     "tasks": tasks, "_key": body or ("att:" + "|".join(a["member"] for a in atts))})
    return rows, stats


# ── plan + commit ────────────────────────────────────────────────────────────
def plan(z, opts) -> dict:
    """Parse an archive without writing anything."""
    fmt = detect(z)
    if fmt == "keep":
        rows, stats = parse_keep(z, opts)
    elif fmt == "quicksilver":
        rows, stats = parse_qs_export(z)
    elif fmt == "markdown":
        rows, stats = parse_markdown_zip(z)
    else:
        return {"format": "unknown", "rows": [], "stats": {}}
    return {"format": fmt, "rows": rows, "stats": stats}


def dedupe(db, rows, stats) -> list:
    """Drop rows already in the library (so re-running an import is harmless).
    Text notes match on body; attachment-only notes (no text to compare) match
    on their original creation time plus how many files they carry."""
    existing = {_sha(r["body"]) for r in db.execute("SELECT body FROM notes WHERE body<>''")}
    media_notes = {(r["created_at"], r["n"]) for r in db.execute(
        "SELECT n.created_at, (SELECT COUNT(*) FROM attachments a WHERE a.note_id=n.id) n "
        "FROM notes n WHERE n.body=''")}
    out, seen = [], set()
    for r in rows:
        if r["body"]:
            h = _sha(r["body"])
            dup = h in existing
        else:
            h = ("att", r.get("created"), len(r.get("attachments", [])), r["_key"])
            dup = (r.get("created"), len(r.get("attachments", []))) in media_notes
        if dup or h in seen:
            stats["skipped_dupe"] = stats.get("skipped_dupe", 0) + 1
            continue
        seen.add(h)
        out.append(r)
    return out


def commit(db, db_path, z, rows, fmt, opts, nanoid) -> dict:
    """Write the planned rows. One transaction for the notes; attachment files
    are validated and stored as we go (a bad one is counted, not fatal)."""
    enrich = bool(opts.get("enrich"))
    transcribe = bool(opts.get("transcribe"))
    done = {"imported": 0, "attachments": 0, "attachments_failed": 0, "tasks": 0}
    for r in rows:
        nid = nanoid(8)
        db.execute(
            "INSERT INTO notes (id, body, pinned, archived, hidden, protected, note_type, enrich_optout, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,"
            "COALESCE(?, strftime('%Y-%m-%dT%H:%M:%fZ','now')),"
            "COALESCE(?, strftime('%Y-%m-%dT%H:%M:%fZ','now')))",
            (nid, r["body"], r.get("pinned", 0), r.get("archived", 0), r.get("hidden", 0),
             r.get("protected", 0), r.get("note_type", "text"), 0 if enrich else 1,
             r.get("created"), r.get("updated")))
        db.execute("INSERT INTO note_state (note_id, status, body_hash) VALUES (?, 'raw', ?)",
                   (nid, hashlib.sha256((r["body"] or "").encode()).hexdigest()[:16]))
        for t in r.get("tags", []):
            if t:
                db.execute("INSERT OR IGNORE INTO tags (note_id, tag) VALUES (?,?)", (nid, t))
        for t in r.get("tasks", []):
            db.execute("INSERT INTO action_items (id, note_id, text, done, done_at, category) VALUES (?,?,?,?,?,?)",
                       (nanoid(10), nid, t["text"][:500], t["done"],
                        _iso_now() if t["done"] else None, t.get("category", "")))
            done["tasks"] += 1
        for a in r.get("attachments", []):
            try:
                aid = ms.new_id()
                with z.open(a["member"]) as src:
                    fn, size = ms.save_stream(db_path, aid, a["kind"], a["mime"], src)
                status = a.get("transcript_status") if fmt == "quicksilver" else None
                text = (a.get("transcript") or "") if fmt == "quicksilver" else ""
                if not status or status in ("running",):
                    status = "pending" if transcribe else "none"
                elif status == "pending" and not transcribe:
                    status = "none"
                strokes = ms.clean_strokes(a.get("strokes")) if a["kind"] == "sketch" else None
                db.execute(
                    "INSERT INTO attachments (id, note_id, kind, mime, filename, size, width, height, "
                    "duration_s, strokes, transcript, transcript_status, created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (aid, nid, a["kind"], ms.norm_mime(a["mime"]), fn, size, a.get("width"),
                     a.get("height"), a.get("duration_s"), strokes, text, status,
                     r.get("created") or _iso_now(), _iso_now()))
                done["attachments"] += 1
            except Exception:
                done["attachments_failed"] += 1
        done["imported"] += 1
    db.commit()
    return done


# ── export ───────────────────────────────────────────────────────────────────
def _slug(s: str, n: int = 40) -> str:
    s = re.sub(r"[^\w\- ]+", "", (s or ""), flags=re.U).strip().replace(" ", "-")
    return (s[:n].strip("-") or "note")


def _yaml(s) -> str:
    s = str(s if s is not None else "")
    if re.search(r"[:#\[\]{}&*!|>'\"%@`,\n]", s) or s != s.strip():
        return json.dumps(s, ensure_ascii=False)
    return s


def build_export(db, db_path, include_hidden: bool = True, include_media: bool = True) -> str:
    """Write the export to a temp file and return its path (caller streams + deletes)."""
    tmp = tempfile.NamedTemporaryFile(prefix="qs-export-", suffix=".zip", delete=False)
    tmp.close()
    where = "" if include_hidden else "WHERE COALESCE(n.hidden,0)=0"
    notes = db.execute(f"SELECT n.* FROM notes n {where} ORDER BY n.created_at").fetchall()
    out_notes, used = [], set()
    with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for n in notes:
            nid = n["id"]
            enr = db.execute("SELECT title, summary FROM enrichments WHERE note_id=? "
                             "ORDER BY enriched_at DESC, rowid DESC LIMIT 1", (nid,)).fetchone()
            tags = [r["tag"] for r in db.execute("SELECT tag FROM tags WHERE note_id=? ORDER BY tag", (nid,))]
            acts = [{"text": r["text"], "done": bool(r["done"]), "category": r["category"]}
                    for r in db.execute("SELECT text, done, category FROM action_items "
                                        "WHERE note_id=? AND COALESCE(dismissed,0)=0 ORDER BY created_at", (nid,))]
            rems = [{"title": r["title"], "when": r["datetime_resolved"] or r["datetime_hint"] or ""}
                    for r in db.execute("SELECT title, datetime_hint, datetime_resolved FROM reminders "
                                        "WHERE note_id=? AND COALESCE(dismissed,0)=0", (nid,))]
            atts = []
            for a in db.execute("SELECT * FROM attachments WHERE note_id=? ORDER BY created_at", (nid,)):
                arc = f"attachments/{a['filename']}"
                p = ms.path_for(db_path, a["filename"])
                if include_media and p.exists():
                    z.write(p, arc)
                    present = True
                else:
                    present = False
                atts.append({"id": a["id"], "kind": a["kind"], "mime": a["mime"],
                             "file": arc if present else "", "width": a["width"], "height": a["height"],
                             "duration_s": a["duration_s"], "strokes": json.loads(a["strokes"]) if a["strokes"] else None,
                             "transcript": a["transcript"] or "", "transcript_status": a["transcript_status"]})
            title = (enr["title"] if enr else "") or ""
            rec = {"id": nid, "title": title, "summary": (enr["summary"] if enr else "") or "",
                   "body": n["body"] or "", "created_at": n["created_at"], "updated_at": n["updated_at"],
                   "pinned": bool(n["pinned"]), "archived": bool(n["archived"]), "hidden": bool(n["hidden"]),
                   "protected": bool(n["protected"]), "note_type": n["note_type"], "tags": tags,
                   "action_items": acts, "reminders": rems, "attachments": atts}
            out_notes.append(rec)
            # Markdown twin
            base = f"{(n['created_at'] or '')[:10]}-{_slug(title or (n['body'] or '').split(chr(10),1)[0])}-{nid}"
            while base in used:
                base += "x"
            used.add(base)
            fm = ["---", f"id: {nid}"]
            if title: fm.append(f"title: {_yaml(title)}")
            fm += [f"created: {n['created_at']}", f"updated: {n['updated_at']}"]
            if tags: fm.append("tags: [" + ", ".join(_yaml(t) for t in tags) + "]")
            if n["pinned"]: fm.append("pinned: true")
            if n["archived"]: fm.append("archived: true")
            if n["hidden"]: fm.append("hidden: true")
            fm.append("---")
            md = ["\n".join(fm), "", (n["body"] or "").rstrip()]
            for a in atts:
                if not a["file"]:
                    continue
                if a["kind"] in ("image", "sketch"):
                    md += ["", f"![{a['kind']}](../{a['file']})"]
                else:
                    md += ["", f"[Voice recording](../{a['file']})"]
                if a["transcript"]:
                    md += ["", "> " + a["transcript"].replace("\n", "\n> ")]
            z.writestr(f"markdown/{base}.md", "\n".join(md).strip() + "\n")
        z.writestr("notes.json", json.dumps(
            {"format": EXPORT_FORMAT, "version": EXPORT_VERSION, "exported_at": _iso_now(),
             "count": len(out_notes), "notes": out_notes}, ensure_ascii=False, indent=1))
        z.writestr("README.txt",
                   "Quicksilver export\n==================\n"
                   "notes.json       everything, re-importable (Settings → Import)\n"
                   "markdown/        one .md file per note, readable in any editor\n"
                   "attachments/     photos, voice recordings and sketches\n")
    return tmp.name
