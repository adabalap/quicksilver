# Changelog — v6.3

Driven by run feedback: write resilience during Memos outages, far better
transcription/reasoning on garbled voice notes, and the "Refined" → "Enriched
Note" rename.

## 1. Write resilience — completed enrichments are never lost (highest value)

**The problem (from your logs):** Memos was briefly unreachable
(`connection refused` on localhost:5230) for a few seconds. The agent had just
spent 33s enriching a note, but every write — PATCH, dashboard, create — failed,
and the finished enrichment was discarded. By the next heartbeat Memos was fine.
This was a transient Memos outage, not a config error.

**The fix, two layers:**
1. **Retry with exponential backoff.** All writes (and reads) now retry transient
   failures — connection refused, timeouts, 5xx — with backoff
   (`write_backoff_base` → `write_backoff_cap`, up to `write_max_retries`).
   A multi-second blip is ridden out transparently. Permanent errors (4xx) are
   NOT retried.
2. **Durable pending-writes store.** If writes still fail after retries (longer
   outage), the completed enrichment is persisted to `<state_file>.pending` and
   re-applied automatically once Memos is reachable again. The 33s inference is
   never wasted. `status` shows the pending count; the daemon flushes pending
   writes before each note and skips the flush when Memos is still down.

## 2. Enrichment quality — voice-to-text reconstruction

**The problem:** notes dictated by voice contain phonetic transcription errors
("terrain"→"team", "Sound liner er are"→"Sounds like they're", "Sand"→"status").
The old prompt explicitly said "never change meaning," which told the model to
PRESERVE the garbled text. Wrong instruction for this use case.

**The fix — a reasoning-first prompt:**
- The model is told the note is likely voice dictation and to read it "aloud,"
  using phonetic + contextual + parallel-structure cues to recover intent
  (e.g. a duplicated team list where one item looks wrong → same category).
- It now AGGRESSIVELY reconstructs intended meaning — real rewriting, not a light
  touch-up — while still never inventing facts and preserving unverifiable proper
  nouns.
- **Safety against wrong guesses:** every meaning-level change is recorded in a
  new `corrections` array with a per-item confidence. Uncertain corrections
  (<0.8) are marked in the note and surfaced under a **Corrections** block on the
  suggestion, so you can verify the agent's reconstruction before approving
  rather than discovering a silent error later. Notes with uncertain corrections
  are auto-flagged into the review queue.

This gives the aggressive reconstruction you asked for, made safe by visibility.

### Why this is the right design (vs. just "try harder")
A single 2B on-device model will sometimes guess wrong on badly garbled audio.
Best practice isn't to pretend it won't — it's to make corrections explicit,
confidence-scored, and human-verifiable, and to route uncertain ones to review.
That keeps trust high and errors catchable. The same `corrections` data is a
foundation for future learning (e.g. remembering your domain terms like
"Zscaler", "VDI").

## 3. "Refined:" → "Enriched Note:"

The enriched-body subtitle on suggestions is now **Enriched Note**
(config: `enriched_label`). Approved notes still label the editable body
**Note** (`note_label`).

## New config keys

```
write_max_retries     4
write_backoff_base    0.5      # seconds
write_backoff_cap     8.0      # seconds
pending_writes_file   ""       # blank → <state_file>.pending
enriched_label        "Enriched Note"   # was refined_label / "Refined"
```

## Forward-looking note
The `corrections` array is the seed for two future capabilities: a per-user
**domain glossary** (learn that "zsclar" = "Zscaler" and stop flagging it), and,
once the shared inference service exists, a **higher-reasoning pass** for
heavily-garbled notes (escalate only low-confidence ones to a bigger model).
Both fit behind today's interfaces without rework.
