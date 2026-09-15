# Changelog — v6.1

A focused fix for the "uncheck to reprocess" behavior on approved notes, plus
the labeled note region that makes it work.

## The bug

When you unchecked "Approved" to reprocess a finalized note, the agent rebuilt
its source text from the hidden anchor's `raw` baseline — the *original* text
from when the note was first created — and tried to merge in detected changes.
If you had edited the refined body, your edits got mixed with stale text,
producing a muddled re-enrichment.

## The fix

The approved note now has a **labeled, machine-readable body region**. After
approval the editable content sits under a **Note:** subtitle inside hidden
`<!-- memos-ai:note -->` … `<!-- /memos-ai:note -->` markers:

```
### Title
**Summary:** …
**Action items:**
- [ ] …
**Note:**
<!-- memos-ai:note -->
<the editable note body — this is the source of truth>
<!-- /memos-ai:note -->
**Tags:** #a #b
**Priority:** 🔴 high
```

On **uncheck to reprocess**, the agent now takes **exactly what is in that
region right now** — including your edits — and re-enriches it. It no longer
reaches back to the stale baseline. Prior title and tags are passed to the model
as **soft hints** (for consistency), never as overrides; the current note text
is always the source of truth.

This addresses both parts of the request: the editable body now has the
subtitle that was asked for, and reprocessing reads only that body.

## Backward compatibility

Approved notes created by the previous build (no `:note` markers) still
reprocess correctly: a dedicated fallback extracts just the body paragraph
(between the action items and the Tags/Priority tail), so reprocessing an older
note won't feed the AI its own title/summary/action items.

## State machine — unchanged and verified

The full round-trip is covered by tests:
`suggest → approve (:note) → uncheck → reenrich (uses body) → suggest (:original) → approve`,
with no marker bleed between the suggestion region (`:original`) and the
approved region (`:note`).

## New config keys

```
note_label   "Note"     # subtitle for the approved editable body
```
