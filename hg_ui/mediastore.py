"""mediastore.py — attachment storage for Quicksilver (photos, voice, sketches, video).

Files live on disk next to the database (<db dir>/attachments/<id>.<ext>); the
`attachments` table holds the metadata, the transcript / recognised text, and
the state of the agent's work on it.

Safety rules this module owns:
  • The stored filename is ALWAYS generated here (<id>.<ext from a whitelist>) —
    a client-supplied name never reaches the filesystem.
  • The declared MIME type must be on the whitelist for the attachment kind AND
    the first bytes must look like that format (magic-number sniff).
  • Size is capped per kind; writes go to a temp file and are renamed into place.
"""
from __future__ import annotations
import os, re, json, secrets, string
from pathlib import Path

IMAGE_MIMES = {"image/jpeg": "jpg", "image/png": "png",
               "image/webp": "webp", "image/gif": "gif"}
SKETCH_MIMES = {"image/png": "png", "image/webp": "webp"}
AUDIO_MIMES = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "m4a",
               "audio/x-m4a": "m4a", "audio/m4a": "m4a", "audio/mpeg": "mp3",
               "audio/mp3": "mp3", "audio/wav": "wav", "audio/x-wav": "wav",
               "audio/wave": "wav", "audio/aac": "aac", "audio/3gpp": "3gp",
               "video/webm": "webm",       # some Android builds label audio-only webm this way
               "video/mp4": "m4a"}         # ...and audio-only mp4
VIDEO_MIMES = {"video/mp4": "mp4", "video/webm": "webm", "video/quicktime": "mov", "video/3gpp": "3gp", "video/x-matroska": "mkv"}
KIND_MIMES = {"image": IMAGE_MIMES, "sketch": SKETCH_MIMES, "audio": AUDIO_MIMES, "video": VIDEO_MIMES}
KIND_LIMIT = {"image": 25 * 1024 * 1024, "sketch": 8 * 1024 * 1024,
              "audio": 80 * 1024 * 1024, "video": 100 * 1024 * 1024}
MAX_VIDEO_SECONDS = 120
MAX_STROKES_BYTES = 2 * 1024 * 1024
KINDS = tuple(KIND_MIMES)

_ID_RE = re.compile(r"^[A-Za-z0-9]{6,24}$")


class MediaError(Exception):
    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.status = status


def att_dir(db_path) -> Path:
    d = Path(os.environ.get("QS_ATT_DIR") or (Path(db_path).parent / "attachments"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def new_id(size: int = 10) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(size))


def valid_id(s: str) -> bool:
    return bool(s and _ID_RE.match(s))


def norm_mime(m: str) -> str:
    return (m or "").split(";")[0].strip().lower()


def _sniff_ok(kind: str, head: bytes) -> bool:
    if kind in ("image", "sketch"):
        if head.startswith(b"\xff\xd8\xff"):                       return kind == "image"
        if head.startswith(b"\x89PNG\r\n\x1a\n"):                  return True
        if head[:4] == b"RIFF" and head[8:12] == b"WEBP":          return True
        if head[:4] == b"GIF8":                                    return kind == "image"
        return False
    if kind == "video":
        return head[4:8] == b"ftyp" or head[:4] == b"\x1a\x45\xdf\xa3"      # mp4 / mov / 3gp, or webm / mkv
    if kind == "audio":
        if head[:4] == b"\x1a\x45\xdf\xa3":                        return True   # webm / matroska
        if head[:4] == b"OggS":                                    return True
        if head[4:8] == b"ftyp":                                   return True   # mp4 / m4a / 3gp
        if head[:3] == b"ID3" or (head[:2] and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0):
            return True                                                          # mp3
        if head[:4] == b"RIFF" and head[8:12] == b"WAVE":          return True
        if head[:2] == b"\xff\xf1" or head[:2] == b"\xff\xf9":     return True   # aac adts
        return False
    return False


def ext_for(kind: str, mime: str) -> str:
    table = KIND_MIMES.get(kind)
    if table is None:
        raise MediaError(f"unknown attachment kind '{kind}'")
    ext = table.get(norm_mime(mime))
    if not ext:
        raise MediaError(f"'{mime}' isn't an accepted {kind} format", 415)
    return ext


def save_stream(db_path, aid: str, kind: str, mime: str, stream) -> tuple[str, int]:
    """Write an upload to disk. Returns (filename, size). Raises MediaError."""
    ext = ext_for(kind, mime)
    limit = KIND_LIMIT[kind]
    d = att_dir(db_path)
    final = d / f"{aid}.{ext}"
    tmp = d / f".{aid}.{secrets.token_hex(4)}.part"
    size = 0
    try:
        with open(tmp, "wb") as f:
            first = True
            while True:
                buf = stream.read(256 * 1024)
                if not buf:
                    break
                if first:
                    if not _sniff_ok(kind, buf[:16]):
                        raise MediaError(f"the file doesn't look like a real {kind}", 415)
                    first = False
                size += len(buf)
                if size > limit:
                    raise MediaError(f"{kind} is too large (max {limit // (1024 * 1024)} MB)", 413)
                f.write(buf)
            if size == 0:
                raise MediaError("empty upload")
        # Replacing (sketch edit): remove any older file of this id with another extension.
        for old in d.glob(f"{aid}.*"):
            if old != final:
                try: old.unlink()
                except OSError: pass
        os.replace(tmp, final)
    finally:
        if tmp.exists():
            try: tmp.unlink()
            except OSError: pass
    return final.name, size


def path_for(db_path, filename: str) -> Path:
    # filename comes from our own table, but never trust a path component anyway.
    return att_dir(db_path) / Path(filename).name


def remove_files(db_path, filenames) -> int:
    n = 0
    for fn in filenames:
        try:
            p = path_for(db_path, fn)
            if p.exists():
                p.unlink(); n += 1
        except OSError:
            pass
    return n


def clean_strokes(raw) -> str | None:
    """Validate sketch vector data: JSON, bounded size. Returns a JSON string."""
    if raw in (None, ""):
        return None
    s = raw if isinstance(raw, str) else json.dumps(raw, separators=(",", ":"))
    if len(s.encode("utf-8")) > MAX_STROKES_BYTES:
        raise MediaError("sketch data too large", 413)
    try:
        v = json.loads(s)
    except Exception:
        raise MediaError("sketch data isn't valid JSON")
    if not isinstance(v, (dict, list)):
        raise MediaError("sketch data has the wrong shape")
    return json.dumps(v, separators=(",", ":"))


def public(row) -> dict:
    """What the browser sees about an attachment (never the file path)."""
    keys = row.keys()
    return {
        "id": row["id"], "kind": row["kind"], "mime": row["mime"],
        "size": row["size"], "width": row["width"], "height": row["height"],
        "duration_s": row["duration_s"],
        "has_strokes": bool(row["strokes"]) if "strokes" in keys else False,
        "transcript": row["transcript"] or "",
        "transcript_status": row["transcript_status"],
        "transcript_error": (row["transcript_error"] if "transcript_error" in keys else None),
        "transcript_model": (row["transcript_model"] if "transcript_model" in keys else None),
        "created_at": row["created_at"], "updated_at": row["updated_at"],
    }


def media_text(rows) -> str:
    """Searchable text of a note's attachments, one labelled line per item."""
    label = {"audio": "Voice note", "image": "Photo text", "sketch": "Sketch text"}
    out = []
    for r in rows:
        t = (r["transcript"] or "").strip()
        if t:
            out.append(f"[{label.get(r['kind'], 'Attachment')}] {t}")
    return "\n".join(out)
