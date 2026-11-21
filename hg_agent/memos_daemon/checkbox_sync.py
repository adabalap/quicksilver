"""checkbox_sync.py — bi-directional checkbox state sync, store-diffed.

DESIGN (v2 — corrected)
────────────────────────
The original v1 design tried to diff the note's OLD text against its NEW text
to find which checkboxes changed. This was fundamentally broken: action-item
checkboxes are rendered OUTSIDE the anchor's `:note`/`raw` baseline fields (see
anchor.py / formatting.py), so there is no reliable "old text" to diff against
without storing a second, separate baseline just for checkbox state.

The correct, much simpler design: the TodoStore IS the source of truth for
"what we last knew" about each item's done-state. We don't need old note text
at all — we read the CURRENT checkbox marks straight out of the note (or
dashboard) we just fetched, and compare each one against what the store
currently has recorded. Any mismatch is a state change to process.

This means:
  • No anchor/baseline dependency — works on every note shape, including ones
    with no anchor history at all.
  • No risk of "can't synthesise old text" — there is no old text to synthesise.
  • Naturally idempotent: re-processing the same note twice in a row sees no
    mismatches the second time (the store already matches reality), so the
    daemon won't loop.

Two extraction shapes are supported:
  1. Source-note shape:   "- [ ] <item text>" (plain, inside Action Items)
  2. Dashboard shape:     "- [ ] **<item text>** · [Note](url)<span data-note="...">"

extract_note_checkbox_states() and extract_dashboard_checkbox_states() each
return {item_text: done_bool} (dashboard also includes note_name per item).
"""

import re

# Source-note action-item line: "- [ ] Call John" or "- [x] Call John"
_NOTE_ITEM_RE = re.compile(r"^- \[(?P<mark>[ xX])\]\s+(?P<text>.+?)\s*$",
                            re.MULTILINE)

# Dashboard line, e.g.:
#   - [ ] **Send the deck** · [Note title](url)<span data-note="memos/abc" .../>
#   - [x] ~~Call John~~ · [Note title](url)<span data-note="memos/abc" .../>
# Split into two passes (text+mark, then the data-note span) rather than one
# combined regex — markdown bold (**) and strikethrough (~~) wrappers differ
# between pending and archived items, which made a single-pattern match
# fragile. Matching up to the literal " · " separator is reliable for both.
_DASHBOARD_LINE_RE = re.compile(r"^- \[(?P<mark>[ xX])\]\s+(?P<text>[^\n]+?)\s*·",
                                 re.MULTILINE)
_DASHBOARD_NOTE_RE = re.compile(r'data-note="([^"]+)"')


def extract_note_action_items_block(content: str) -> str:
    """Return just the Action Items section of a note's content, or "" if
    there isn't one. Bounded by the next '##'/'**'/details-open marker so we
    never accidentally pick up an unrelated checklist elsewhere in the note."""
    m = re.search(
        r"\*\*Action [Ii]tems?:\*\*\s*\n(.*?)(?=\n\*\*|\n##|\n<details|\Z)",
        content, re.DOTALL)
    return m.group(1) if m else ""


def extract_note_checkbox_states(content: str) -> dict:
    """Return {item_text: done_bool} for all action-item checkboxes in a
    source note's Action Items section."""
    block = extract_note_action_items_block(content)
    if not block:
        return {}
    states = {}
    for m in _NOTE_ITEM_RE.finditer(block):
        text = m.group("text").strip()
        done = m.group("mark").strip().lower() == "x"
        states[text] = done
    return states


def extract_dashboard_checkbox_states(content: str) -> list[dict]:
    """Return a list of {"text", "done", "note_name"} for every checkbox line
    on the TODO dashboard (covers both the pending section and the archived
    <details> block, since both carry the same line shape).

    Each dashboard line ends with a hidden <span data-note="..."> carrying the
    source note's name — found by scanning from the line's match position to
    the next newline (or end of content) rather than baking it into the main
    regex, since markdown wrappers (bold for pending, strikethrough for
    archived items) made a single combined pattern unreliable.
    """
    items = []
    for m in _DASHBOARD_LINE_RE.finditer(content):
        text = m.group("text").strip().strip("*~").strip()
        done = m.group("mark").strip().lower() == "x"
        if not text:
            continue
        line_end = content.find("\n", m.end())
        line_end = line_end if line_end != -1 else len(content)
        rest_of_line = content[m.end():line_end]
        note_match = _DASHBOARD_NOTE_RE.search(rest_of_line)
        note_name = note_match.group(1) if note_match else ""
        items.append({"text": text, "done": done, "note_name": note_name})
    return items


def patch_checkbox_in_text(content: str, item_text: str, done: bool) -> tuple[str, bool]:
    """Find the checkbox line for item_text in content and set its mark.

    Matches three line shapes:
      - "- [ ] text"                 (plain source-note item)
      - "- [ ] **text**"             (dashboard, pending item — bold)
      - "- [ ] ~~text~~"             (dashboard, archived item — strikethrough)

    Returns (new_content, changed).
    """
    mark = "x" if done else " "
    escaped = re.escape(item_text)
    pattern = re.compile(
        r"^(- \[)[ xX](\]\s+(?:\*{0,2}|~{0,2})" + escaped + r"(?:\*{0,2}|~{0,2}))",
        re.MULTILINE
    )
    new_content, n = pattern.subn(rf"\g<1>{mark}\g<2>", content)
    return new_content, n > 0
