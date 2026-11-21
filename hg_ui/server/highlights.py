"""Highlight markers: invisible-character storage, stripping, and the one-time migration from the old ==text== form."""
import re

from .core import _NOTES_UPDATED_TRIGGER, log


# ── Highlights ────────────────────────────────────────────────────────────────
# Stored as INVISIBLE marker characters that never occur in real text — U+2063 + a colour
# char … U+2063 U+2063 — so pasted code (`a == b`, `x=="y"`) can never be mistaken for one.
# The old =={y}text== form is still read and is migrated once at startup. Keep in sync with
# index.html and the agent's anchor.py.
HL_COLOR_OF = {"\u2060": "y", "\u2061": "g", "\u2062": "p", "\u2064": "b"}
HL_CHAR_OF = {v: k for k, v in HL_COLOR_OF.items()}
HL_RE = re.compile(r"\u2063([\u2060\u2061\u2062\u2064])([\s\S]*?)\u2063\u2063|"
                   r"(?<![=!<>])==\{([ygpb])\}(?![\s=])([\s\S]*?)(?<![\s=])==(?!=)")
_HL_CODE_SKIP = re.compile(r"(```[\s\S]*?```|`[^`\n]*`)|(?<![=!<>])==\{([ygpb])\}(?![\s=])([\s\S]*?)(?<![\s=])==(?!=)")

def strip_hl(s: str) -> str:
    out = HL_RE.sub(lambda m: m.group(2) if m.group(2) is not None else (m.group(4) or ""), s or "")
    return re.sub("[\u2060-\u2064]", "", out)

def _migrate_highlights(db) -> int:
    """One-time: =={c}text== -> invisible markers (code spans untouched). Keeps
    updated_at so the list order and the agent's change watermark don't move."""
    done = db.execute("SELECT value FROM settings WHERE key='hl_migrated_v2'").fetchone()
    if done:
        return 0
    rows = db.execute("SELECT id, body, updated_at FROM notes WHERE body LIKE '%=={%'").fetchall()
    todo = []
    for r in rows:
        def conv(m):
            if m.group(1) is not None:
                return m.group(1)
            return "\u2063" + HL_CHAR_OF[m.group(2)] + m.group(3) + "\u2063\u2063"
        nb = _HL_CODE_SKIP.sub(conv, r["body"] or "")
        if nb != r["body"]:
            todo.append((nb, r["updated_at"], r["id"]))
    if todo:
        db.execute("DROP TRIGGER IF EXISTS notes_updated")
        try:
            db.executemany("UPDATE notes SET body=?, updated_at=? WHERE id=?", todo)
        finally:
            db.execute(_NOTES_UPDATED_TRIGGER)
    db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('hl_migrated_v2','1')")
    db.commit()
    if todo:
        log.info(f"highlights: migrated {len(todo)} note(s) to the new marker format")
    return len(todo)

def _plain_snip(text: str, n: int = 170) -> str:
    t = re.sub(r"[#*_`>=\[\]]+", "", strip_hl(text or ""))
    t = re.sub(r"\s+", " ", t).strip()
    return t if len(t) <= n else t[:n].rsplit(" ", 1)[0] + "…"
