"""chunks.py — split a note into passages for retrieval.

One vector per whole note forces a compromise: a long note is either truncated
(the old backfill embedded only the first 800 characters) or averaged into a
blur where the one relevant paragraph barely moves the vector. Retrieval works
far better one passage at a time: embed each ~500-character chunk, find the best
chunk per note, and hand THAT passage to Ask as evidence.

Pure functions, no model, no I/O — so they are cheap to test and identical on
the phone and in CI.
"""
import re
import hashlib

# Sentence ends: Latin . ! ?  plus the danda (।) used in Devanagari-script text.
# Telugu normally ends sentences with "." so the Latin rule covers it too.
_SENT = re.compile(r"(?<=[.!?।])\s+")
_PARA = re.compile(r"\n\s*\n")
_ABBR = re.compile(r"\b(Dr|Mr|Mrs|Ms|Prof|St|Sr|Jr|vs|No)\.", re.I)


def _units(text: str) -> list[str]:
    """Paragraphs, each broken into sentences. Sentence splitting must not break
    after 'Dr.' / 'Mr.' — protect those, split, restore."""
    out = []
    for para in _PARA.split(text):
        para = para.strip()
        if not para:
            continue
        # Single newlines inside a paragraph are list items / line breaks: treat
        # each line as its own unit (checklists and bullet notes read per line).
        for line in para.split("\n"):
            line = line.strip()
            if not line:
                continue
            prot = _ABBR.sub(lambda m: m.group(1) + "\x00", line)
            for s in _SENT.split(prot):
                s = s.replace("\x00", ".").strip()
                if s:
                    out.append(s)
    return out


def _hard_split(unit: str, max_chars: int) -> list[str]:
    """A single sentence longer than a chunk (pasted URL, log line, run-on):
    split at whitespace, or hard-cut if there is none."""
    parts, cur = [], ""
    for w in unit.split(" "):
        while len(w) > max_chars:                    # one giant token
            if cur:
                parts.append(cur); cur = ""
            parts.append(w[:max_chars]); w = w[max_chars:]
        if cur and len(cur) + 1 + len(w) > max_chars:
            parts.append(cur); cur = w
        else:
            cur = f"{cur} {w}" if cur else w
    if cur:
        parts.append(cur)
    return parts


def _tail(text: str, overlap: int) -> str:
    """The last ~`overlap` characters, starting at a word boundary, so adjacent
    chunks share context and an answer straddling a boundary isn't lost."""
    if overlap <= 0 or len(text) <= overlap:
        return text if overlap > 0 else ""
    cut = text[-overlap:]
    sp = cut.find(" ")
    return cut[sp + 1:] if 0 <= sp < len(cut) - 1 else cut


def chunk_text(text: str, max_chars: int = 500, overlap: int = 80,
               max_chunks: int = 40) -> list[str]:
    """Split `text` into passages of at most ~max_chars (overlap included in the
    budget loosely — a chunk may exceed it by at most `overlap`).

    A short note (≤ 1.2 × max_chars) stays ONE chunk: splitting a paragraph a
    reader would take as a single thought only hurts. At most `max_chunks` are
    returned (the head of very long notes); the cap bounds embedding cost.
    """
    text = (text or "").replace("\r\n", "\n").strip()
    if not text:
        return []
    if len(text) <= int(max_chars * 1.2):
        return [text]
    units = []
    for u in _units(text):
        units.extend([u] if len(u) <= max_chars else _hard_split(u, max_chars))
    chunks, cur = [], ""
    for u in units:
        if cur and len(cur) + 1 + len(u) > max_chars:
            chunks.append(cur)
            carry = _tail(cur, overlap)
            cur = f"{carry} {u}".strip() if carry else u
        else:
            cur = f"{cur} {u}" if cur else u
    if cur and (not chunks or cur != chunks[-1]):
        chunks.append(cur)
    return chunks[:max_chunks]


def content_hash(model: str, title: str, tags, body: str, params: str = "") -> str:
    """Identity of 'what the chunk index for this note was built from'. If any of
    it changes (model, title, tags, body, chunking params) the note is re-indexed;
    if none did, indexing is a no-op."""
    h = hashlib.sha1()
    for part in (model, title or "", " ".join(tags or []), body or "", params):
        h.update(part.encode("utf-8", "replace")); h.update(b"\x00")
    return h.hexdigest()[:16]
