"""anchor.py — hidden state, boundary markers, hashing, and text extraction.

State for each note lives in a hidden HTML comment ("anchor") plus explicit
section-boundary markers. The markers are the contract between the renderer
(formatting.py) and the extractors here, so the human-authored text is *never*
confused with the AI-generated title/summary/body.

Visible layout of an enriched (SUGGESTED) note:

    <!-- memos-ai:original -->
    <original user text — exactly what the human wrote>
    <!-- /memos-ai:original -->

    ---
    ### Title                         (AI, only if note had none)
    **Summary:** …
    <enriched / polished body>
    **Action items:** …
    **Tags:** #a #b                   (moved to the bottom)
    <details>…approve controls + anchor…</details>

On APPROVAL the `:original` block is removed (it is now obsolete) and only the
enriched content remains, with the anchor recording a hash of the final body.
"""

import re, json, base64, hashlib, difflib
from datetime import datetime, timezone

# ── Anchor (hidden JSON state) ────────────────────────────────────────────────
ANCHOR_OPEN  = "<!-- memos-ai"
ANCHOR_CLOSE = "-->"
ANCHOR_RE    = re.compile(r"<!--\s*memos-ai\s*(\{.*?\})\s*-->", re.DOTALL)

# ── Explicit section boundaries (the new contract) ────────────────────────────
ORIG_OPEN   = "<!-- memos-ai:original -->"
ORIG_CLOSE  = "<!-- /memos-ai:original -->"
ORIG_BLOCK_RE = re.compile(
    re.escape(ORIG_OPEN) + r"\s*(?P<body>.*?)\s*" + re.escape(ORIG_CLOSE),
    re.DOTALL,
)

# The editable note body in an APPROVED note. After approval the original block
# is gone and THIS region is the source of truth: the human edits inside it, and
# on "uncheck to reprocess" the agent re-enriches exactly this text (not the
# stale anchor baseline).
NOTE_OPEN   = "<!-- memos-ai:note -->"
NOTE_CLOSE  = "<!-- /memos-ai:note -->"
NOTE_BLOCK_RE = re.compile(
    re.escape(NOTE_OPEN) + r"\s*(?P<body>.*?)\s*" + re.escape(NOTE_CLOSE),
    re.DOTALL,
)

# Approve / review checkbox lines.
APPROVE_LINE_RE = re.compile(
    r"^[\-\*]\s*\[(?P<mark>[ xX])\]\s*\S*\s*Approv(?:e|ed)\b.*$", re.MULTILINE
)


# ── Hashing ───────────────────────────────────────────────────────────────────
import re as _re_hl

# Highlight markers are PRESENTATION only — adding,
# erasing, or recolouring a highlight changes zero words. The agent's change
# detection must ignore them so a highlight/erase never looks like a content
# edit (which would wastefully trigger a 30–40s re-enrichment). This MUST match
# the PWA's _strip_hl so both systems share one definition of "content".
# Highlights are stored as INVISIBLE marker characters that never occur in real text
# (U+2063 + a colour char ... U+2063 U+2063), so pasted code can never be mistaken for one.
# The old =={y}text== form is still read (and migrated by the server). Keep in sync with
# app.py and index.html.
_HL_MARK_RE = _re_hl.compile(r"\u2063([\u2060\u2061\u2062\u2064])([\s\S]*?)\u2063\u2063|(?<![=!<>])==\{([ygpb])\}(?![\s=])([\s\S]*?)(?<![\s=])==(?!=)")

def strip_highlights(s: str) -> str:
    """Remove highlight markers, keeping the highlighted words. Presentation-only
    annotations must not affect content identity."""
    if not s:
        return s
    out = _HL_MARK_RE.sub(lambda m: m.group(2) if m.group(2) is not None else (m.group(4) or ""), s)
    return _re_hl.sub("[\u2060-\u2064]", "", out)

# Checkbox toggles ([ ] ↔ [x]) are a STATUS change, not a content edit — ticking
# an item off a checklist must not look like an edit (which would wastefully
# re-enrich a 40s inference). We normalise the checkbox marker to a single form
# for hashing, but keep the item TEXT — so adding/removing/renaming an item
# still changes the hash and re-enriches, while a pure tick does not.
_CHECKBOX_RE = _re_hl.compile(r"^(\s*[-*]\s*)\[[ xX]\]", _re_hl.MULTILINE)

def normalize_checkboxes(s: str) -> str:
    if not s:
        return s
    return _CHECKBOX_RE.sub(r"\1[ ]", s)

def content_hash(s: str) -> str:
    """Hash of a note's MEANINGFUL content — highlight markers and checkbox
    state normalised away. Use this for change-detection so annotations and
    checkbox toggles don't count as content edits (which would re-enrich)."""
    return text_hash(normalize_checkboxes(strip_highlights(s or "")))

def text_hash(s: str) -> str:
    return hashlib.sha256(s.strip().encode("utf-8")).hexdigest()[:16]


# ── Anchor parse / build ──────────────────────────────────────────────────────
def parse_anchor(content: str) -> dict | None:
    m = ANCHOR_RE.search(content)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
        if "raw_b64" in data:
            data["raw"] = base64.b64decode(data["raw_b64"]).decode("utf-8")
        return data
    except Exception:
        return None


def strip_anchor(content: str) -> str:
    return ANCHOR_RE.sub("", content).rstrip()


def make_anchor(status: str, user_text: str, raw_baseline: str, role: str = "",
                summary: str = "", extra: dict | None = None) -> str:
    payload = {
        "v": 2,
        "status": status,
        "hash": text_hash(user_text),
        "raw_b64": base64.b64encode(raw_baseline.encode("utf-8")).decode("ascii"),
        "ts": _now_iso(),
    }
    if role:
        payload["role"] = role
    if summary:
        payload["summary"] = summary
    if extra:
        payload.update(extra)
    return f"{ANCHOR_OPEN}\n{json.dumps(payload, ensure_ascii=False)}\n{ANCHOR_CLOSE}"


# ── Original-block helpers ────────────────────────────────────────────────────
def wrap_original(user_text: str) -> str:
    """Wrap the human's text in explicit boundary markers."""
    return f"{ORIG_OPEN}\n{user_text.rstrip()}\n{ORIG_CLOSE}"


def extract_original(content: str) -> str | None:
    """Return the text inside the :original markers, or None if absent."""
    m = ORIG_BLOCK_RE.search(content)
    return m.group("body").strip() if m else None


def strip_original_block(content: str) -> str:
    """Remove the whole :original block (used on approval)."""
    return ORIG_BLOCK_RE.sub("", content).lstrip()


# ── Note-block helpers (approved editable body) ───────────────────────────────
def wrap_note(note_text: str) -> str:
    """Wrap the approved editable body in explicit boundary markers."""
    return f"{NOTE_OPEN}\n{note_text.rstrip()}\n{NOTE_CLOSE}"


def extract_note(content: str) -> str | None:
    """Return the text inside the :note markers, or None if absent."""
    m = NOTE_BLOCK_RE.search(content)
    return m.group("body").strip() if m else None


def has_note_block(content: str) -> bool:
    return bool(NOTE_BLOCK_RE.search(content))


# ── Approve-line detection ────────────────────────────────────────────────────
def is_approved_ticked(content: str) -> bool:
    m = APPROVE_LINE_RE.search(content)
    return bool(m and m.group("mark").lower() == "x")


def has_approve_line(content: str) -> bool:
    return bool(APPROVE_LINE_RE.search(content))


# ── The crucial extractor: human text only ────────────────────────────────────
def user_text_of(content: str) -> str:
    """The human-authored portion of a note — and ONLY that.

    Priority:
      1. :original markers (a SUGGESTED note under review) → their contents.
      2. :note markers (an APPROVED note's editable body) → their contents.
         This is the source of truth when you 'uncheck to reprocess': whatever
         is inside :note right now, including your edits.
      3. Legacy fallback: everything above the first enrichment '---' divider,
         minus the anchor. (Old-build notes and brand-new untouched notes.)
    """
    explicit = extract_original(content)
    if explicit is not None:
        return explicit.strip()

    note = extract_note(content)
    if note is not None:
        return note.strip()

    # Legacy approved notes (made before :note markers existed) have no markers
    # and no '---' divider — just labeled sections. Recover the body region:
    # the text between the Action items block and the Tags/Priority/details tail.
    legacy_body = _extract_legacy_approved_body(content)
    if legacy_body is not None:
        return legacy_body

    body = strip_anchor(content)
    # Legacy suggestion: our enriched block began at a '---' divider; the human
    # text was above it. A brand-new note has no divider → whole thing is text.
    parts = re.split(r"\n---\s*\n", body, maxsplit=1)
    return parts[0].strip()


# Section labels we recognize in a v6 approved note, used to locate the body.
_SECTION_LABEL_RE = re.compile(
    r"^\*\*(Summary|Action items|Tags|Priority|Related|Refined|Note)\:\*\*", re.MULTILINE
)


def _extract_legacy_approved_body(content: str) -> str | None:
    """For a pre-:note approved note: return the unlabeled body paragraph that
    sits after the Action items list and before Tags/Priority/Related/details.
    Returns None if this doesn't look like a v6 approved note."""
    a = ANCHOR_RE.search(content)
    if not a:
        return None
    try:
        status = json.loads(a.group(1)).get("status")
    except Exception:
        status = None
    if status != "approved":
        return None
    body = strip_anchor(content)
    # Cut off the collapsible AI controls.
    body = re.split(r"<details>", body, maxsplit=1)[0]
    lines = body.splitlines()
    # Find where the body starts: after the last action-item bullet, or after
    # the Summary block if there are no action items.
    start = 0
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s.startswith("**Action items:**") or s.startswith("- [ ]") or s.startswith("- ["):
            start = i + 1
        elif start == 0 and s.startswith("**Summary:**"):
            start = i + 1
        elif start == 0 and s.startswith("### "):
            start = i + 1
    # Body ends at the first trailing labeled section (Tags/Priority/Related).
    out = []
    for ln in lines[start:]:
        s = ln.strip()
        if s.startswith("**Tags:**") or s.startswith("**Priority:**") or s.startswith("**Related:**"):
            break
        out.append(ln)
    text = "\n".join(out).strip()
    return text or None


# ── Misc text utilities ───────────────────────────────────────────────────────
def strip_leading_title(text: str) -> str:
    """Remove a single leading markdown heading (and following blanks) so
    similarity compares body-to-body, not counting an AI-added title."""
    lines = text.splitlines()
    out, skipping = [], True
    for line in lines:
        if skipping and line.strip().startswith("#"):
            skipping = False
            continue
        if skipping and not line.strip():
            continue
        skipping = False
        out.append(line)
    return "\n".join(out).strip()


def similarity(a: str, b: str) -> float:
    a, b = strip_leading_title(a), strip_leading_title(b)
    if not a and not b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Common offsets for environments without IANA tz data (fallback only).
_TZ_FALLBACK = {
    "Asia/Kolkata": (5.5, "IST"),
    "UTC": (0.0, "UTC"),
    "America/New_York": (-5.0, "EST"),
    "Europe/London": (0.0, "GMT"),
}


def display_timestamp(tz_name: str = "Asia/Kolkata", fmt: str = "%Y-%m-%d %H:%M") -> str:
    """A human-readable 'YYYY-MM-DD HH:MM TZ' string in the configured timezone.
    Uses zoneinfo when available (correct DST + abbreviation); falls back to a
    fixed-offset table, then to UTC, so it never raises."""
    from datetime import timedelta
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo(tz_name))
        label = now.strftime("%Z") or tz_name
        return f"{now.strftime(fmt)} {label}"
    except Exception:
        offset, label = _TZ_FALLBACK.get(tz_name, (0.0, "UTC"))
        now = datetime.now(timezone.utc) + timedelta(hours=offset)
        return f"{now.strftime(fmt)} {label}"
