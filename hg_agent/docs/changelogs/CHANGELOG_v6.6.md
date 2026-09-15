# Changelog — v6.6

Two enhancements, then lock-down.

## 1. Semantic embeddings enabled by default

`embed_model` now defaults to **"all-MiniLM-L6-v2"** (~90MB, 384-dim), so note
relationships use meaning-based grouping out of the box — notes about the same
thing link even when they share no keywords.

- Still fail-safe: if `sentence-transformers` isn't installed or the model can't
  load, the agent automatically falls back to lexical similarity. It never
  breaks; you just won't get semantic grouping until the package is present.
- The startup log now reports the active backend up front:
  `relationships: semantic (embeddings)` or `relationships: lexical (keyword)`.
- To use semantic grouping, install once: `pip install sentence-transformers`.
  To force lexical, set `embed_model: ""`.

## 2. Empty "Action items" section is suppressed

Previously a note with no real tasks could still show:
```
Action items:
- [ ]
```
This came from the model occasionally returning a blank entry (e.g. `[""]`),
which slipped past the "is there a list?" check and rendered an empty checkbox.

Fixed with defense in depth:
- **Engine:** `action_items` and `tags` are now cleaned to drop empty/blank/None
  entries during normalization, so `[""]`, `["  "]`, `[None]` all become `[]`.
- **Formatting:** the Action items section re-filters at render time and only
  appears when at least one real item remains.

Result: notes with no tasks have no "Action items" header and no stray `- [ ]`.
Genuine action items render exactly as before.

No new config keys. `embed_model` default changed from `""` to
`"all-MiniLM-L6-v2"`.
