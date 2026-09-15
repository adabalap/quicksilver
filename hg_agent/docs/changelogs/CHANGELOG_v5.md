# Changelog — v5

This release reworks how notes are organized, fixes correctness bugs, sharpens
the enrichment quality, and adds genuinely agentic capabilities. Nothing in your
config breaks; new keys have safe defaults.

## Bug fixes

- **Thermal guard crash (critical).** `thermal.py` called `time.sleep()` in its
  cool-down loop but never imported `time`. The guard would raise `NameError`
  the moment a phone reached the hot threshold — exactly when it mattered most.
  Now imports `time`. Verified the cool-down loop runs without error.

- **Original/enriched text bleed (the big one).** The old layout put the AI
  title and summary *above* the `---` divider, while `user_text_of()` treated
  everything above that divider as "the user's original text." So every re-edit
  folded the AI's own output back in and re-summarised it — a slow corruption
  loop, and the reason original and enriched content weren't cleanly separated.
  Fixed with an explicit boundary marker (see below).

- **Patch-then-rewatermark double-enrichment.** After patching a note, the
  daemon now records the server's *post-patch* `updateTime` (from the PATCH
  response, or by re-reading the note), so the next poll sees it unchanged
  instead of re-enriching it once more.

- **Output type safety.** `engine._normalize()` coerces the model's JSON into
  the expected types (lists stay lists, priority is clamped to a valid value,
  confidence to 0–1), so a malformed field can't crash the renderer.

## Note organization (your request)

- **Original on top, enriched below, clearly separated.** New and re-edited
  notes show your exact text first, wrapped in `<!-- memos-ai:original -->`
  markers, then a divider, then the enriched section: **Summary**, the polished
  body, action items.
- **Tags moved to the bottom** as a subtitle, just above the collapsed AI
  controls — in both suggested and approved notes.
- **On approval the original is deleted.** Once you tick approve, the now-
  obsolete original block is removed and the enriched content stands alone.
- A **Priority · Confidence** meta line and a **Related** links line round out
  the enriched section.

## Better enrichment

- Rewritten system prompt: stricter faithfulness rules for the polished rewrite,
  explicit empty-value handling, mandatory honest `confidence`, and clearer
  guidance on when to emit action items vs. leave them empty.

## New agentic capabilities

- **Note correlation (`correlation.py`).** On-device TF-IDF + cosine similarity
  over your notes' human text — no embedding model, no network. Powers:
  - a **Related:** line of deep links on each enriched note, and
  - a new pinned **🔗 AI Connections** note that groups related notes into
    clusters and links each member. Toggle with `connections_enabled`.
- **Confidence-gated review.** Low-confidence enrichments are tagged
  `#needs-review` (threshold: `confidence_review_threshold`) and surfaced on the
  dashboard, instead of being presented as confident suggestions.
- **Priority queue.** A keyword pre-pass pushes likely-urgent notes
  (`urgent`, `today`, `deadline`, …) to the front of the work queue.
  Toggle with `priority_queue_enabled`.
- **Per-note metrics.** Inference latency (last + rolling average) is recorded
  and shown in `status` and on the dashboard.

## Dashboard

- Rebuilt as clean, portable markdown (renders reliably in Memos): an activity
  table with proportion bars, a performance panel (latency, counts, live SoC
  temperature), a health indicator, and an "action needed" callout for notes
  awaiting review.

## New config keys (all with safe defaults)

```
connections_enabled (true), connections_title ("🔗 AI Connections")
correlation_enabled (true), related_top_n (3), related_threshold (0.12),
cluster_threshold (0.18), correlation_corpus_size (200)
confidence_review_threshold (0.6)
priority_queue_enabled (true)
```

## One-time setup for the Connections feature

Nothing to do — it uses the same notes and the same model. The first time the
daemon processes a note with `connections_enabled: true`, it creates and pins
the **🔗 AI Connections** note automatically.
