# Architecture

This document describes how the Memos AI Agent is built: its components, how a
note flows through it, the state machine that governs decisions, the on-note
format, and the concurrency model.

---

## 1. The big picture

The agent is a long-running process that sits beside Memos and reacts to note
changes. It has one job per note: decide what (if anything) to do, and if
action is needed, run the local model and write back a result the user controls.

```
                          ┌────────────────────────────────────────────┐
                          │                MemosDaemon                  │
                          │  (orchestrator — daemon.py)                 │
   Memos  ──webhook──►  ┌─┤  HTTP webhook server  ─┐                    │
     ▲                  │ │  safety poller        ─┼─► work queue       │
     │                  │ │                         │   (priority,       │
     │   REST (read)    │ │                         │    dedup)          │
     │◄─────────────────┘ │                         ▼                    │
     │                    │                   single worker thread       │
     │   REST (write,     │                         │                    │
     │   retried)         │            ┌────────────┴───────────┐        │
     └────────────────────┤            │  per-note pipeline:    │        │
                          │            │  decide → (thermal) →  │        │
                          │            │  enrich → render →     │        │
                          │            │  write → update state  │        │
                          │            └────────────┬───────────┘        │
                          │                         │                    │
                          │   uses these collaborators:                  │
                          │   • GemmaEngine (local model inference)      │
                          │   • Corpus + Embedder (relationships)        │
                          │   • CalendarClient (optional gcal)           │
                          │   • ThermalGuard (don't cook the phone)      │
                          │   • StateStore + PendingWrites (durable)     │
                          └──────────────────────────────────────────────┘
```

---

## 2. Components (one paragraph each)

- **`daemon.py` — the orchestrator.** Owns the run loop, the webhook server, the
  safety poller, the work queue, and the per-note pipeline. Everything else is a
  collaborator it calls. Also builds the two maintained dashboards.

- **`decision.py` — the state machine.** Pure function `decide(note, state, cfg)`
  → one of `skip | suggest | reenrich | finalize`. No side effects; just looks at
  the note's content, hidden state, and our records, and returns the next action.

- **`engine.py` — the model.** Wraps the LiteRT/Gemma runtime. `enrich(text,
  hints)` builds the prompt, runs inference, parses and **normalizes** the JSON
  (coercing types, dropping junk like empty action items or whole-note
  "corrections"), and returns a clean enrichment dict with a latency stamp.

- **`prompt.py` — the instructions.** The system prompt that turns the model into
  a faithful note-enricher: schema, transcription-reconstruction reasoning,
  and the asymmetric correction rules.

- **`anchor.py` — state-on-the-note + text extraction.** Hidden HTML-comment
  "anchors" carry JSON state inside each note. Explicit boundary markers
  (`:original`, `:note`) fence off the human-authored text so it is never
  confused with the AI's additions. Also: hashing, similarity, and the IST/UTC
  display-timestamp helper.

- **`formatting.py` — the rendered note.** Turns an enrichment dict into the
  Markdown body for a suggestion or an approved note, in the agreed layout.

- **`correlation.py` — relationships.** A `Corpus` of notes with pluggable
  similarity (semantic embeddings if available, else lexical TF-IDF). Produces
  related-note links, clusters, "why grouped" shared terms, cohesion strength,
  and the central "anchor" note of a group.

- **`embeddings.py` — optional semantic backend.** Lazy-loaded sentence
  embeddings with a hard fail-safe: if the model/library is absent, it reports
  unavailable and the Corpus falls back to lexical similarity.

- **`memos_client.py` — the Memos API.** Resilient HTTP client: reads, writes
  (PATCH), creates, pins. Retries transient failures with exponential backoff;
  distinguishes transient (retry) from permanent (4xx, don't retry) errors.

- **`state.py` — durable memory.** `state.json`: per-note records (status, hash,
  failures, seen-time), counters/stats, performance metrics, and the IDs of the
  maintained dashboards.

- **`pending.py` — outage insurance.** A durable queue of completed-but-unwritten
  enrichments, re-applied when Memos comes back, so a finished inference is never
  lost to a transient outage.

- **`thermal.py` — the governor.** Reads SoC temperature; pauses heavy work when
  the device is too hot and resumes when it cools (hysteresis).

- **`calendar_client.py` — optional tool.** Turns time-bound action items from
  approved notes into Google Calendar events.

- **`cli.py` / `app.py` — entry points.** Argument parsing and the subcommands
  (`run`, `enrich`, `status`, `thermal`, `reset`, `init-config`).

- **`config.py` — settings.** One `DEFAULT_CONFIG` dict, overlaid by
  `~/.config/memos_daemon/config.json`, overlaid by a few env vars.

- **`logging_setup.py` — logs.** Rotating file + console logging in a consistent
  format.

---

## 3. How a note flows through (the pipeline)

For each note name pulled off the work queue, the worker runs `_process_one`:

1. **Flush pending writes.** If earlier enrichments failed to write during an
   outage and Memos is now reachable, re-apply them first.
2. **Fetch the note fresh** from Memos (never trust a stale copy from the queue).
3. **Decide** (`decide()`): `skip`, `suggest`, `reenrich`, or `finalize`.
4. If the action needs the model and the device is hot, **wait until cool**
   (or requeue and back off).
5. **Handle** the action:
   - *suggest / reenrich*: run the model on the human text, render a
     **suggestion**, write it back.
   - *finalize*: run the model, render the **approved** note (original dropped),
     write it back, and create any calendar events.
6. **Update state** (status, hash, server-confirmed update-time) and **advance
   the watermark**.
7. **Refresh the dashboards** (stats + relationships).

Each step logs a breadcrumb (`▸ processing`, `decision: …`, `running inference
…`, `inference returned in Ns`, `✓ suggested`, `▸ done`) so the agent is never a
black box.

---

## 4. The state machine (`decide`)

State lives in two places that must agree: a hidden **anchor** inside the note
(its `status`, and a hash of the human text at the time we wrote it), and our
**`state.json`** record (hash, failure count, last seen update-time).

```
                        ┌─────────── note has no anchor ───────────┐
                        │                                          │
                        ▼                                          │
            same hash as our record? ──yes──► skip                 │
                        │no                                         │
                        ▼                                          │
                     SUGGEST ───────────────────────────────────► writes a
                                                                   suggestion
   anchor.status == "suggested":
        approve box ticked?  ──yes──► FINALIZE ──► writes approved note
        human text changed?  ──yes──► SUGGEST  (re-suggest on the new text)
        else                         ──► skip

   anchor.status == "approved":
        approve box still ticked? ──yes──► skip   (settled)
        box un-ticked?            ──────► REENRICH (user asked to redo it)

   guard rails:
     • our own dashboards (by anchor role or title) ──► always skip
     • empty note ──► skip
     • failed >= max_retries AND unchanged ──► skip (stop hammering)
```

Why state on the note *and* off it: the anchor lets the agent recognize its own
work across restarts (even if `state.json` is wiped); the `state.json` record
lets it detect change and throttle retries without parsing every note.

---

## 5. The on-note format

The contract that makes everything reliable is **explicit boundary markers** so
the human's text is never confused with the AI's output.

**Suggested note (under review):**
```
<!-- memos-ai:original -->
<your exact text, untouched>
<!-- /memos-ai:original -->

---
### Title                         (added only if your note had no heading)
**Summary:** …
**Action items:**                 (only if real tasks exist)
- [ ] …
**Enriched Note:** <cleaned-up rewrite>
**Corrections:** …                (only if real word-level fixes were made)
**Tags:** #a #b
**Priority:** 🔴 high             (only when high)
**Related:** [note](url) · …      (semantically/【lexically related notes)
<details>… approve checkbox + hidden anchor …</details>
#pending-approval
```

**Approved note (final):**
```
### Title
**Summary:** …
**Action items:**
- [ ] …
**Note:**
<!-- memos-ai:note -->
<the editable note body — the source of truth on re-process>
<!-- /memos-ai:note -->
**Tags:** #a #b
**Priority:** 🔴 high
<details>🤖 approved + hidden anchor (Enriched: <time> IST)</details>
```

Key points:
- On **approval the `:original` block is deleted** — it's obsolete; the enriched
  content becomes the note.
- The approved body lives inside **`:note` markers**, so "uncheck to reprocess"
  re-enriches *exactly that text* (your edits included), never a stale baseline.
- The hidden **anchor** (`<!-- memos-ai {json} -->`) carries `status`, a hash,
  the title/tags (used as soft hints on re-enrichment), and the original raw
  text (base64).

---

## 6. Concurrency model

- **One worker thread** drains a **priority queue** of note names. Because only
  one worker runs, the single in-memory model is never asked to do two
  inferences at once.
- **Two producers** feed the queue: the **webhook server** (instant, on Memos
  events) and the **safety poller** (slow, catches anything missed). Both
  deduplicate by note name.
- **Priority**: a cheap keyword pre-pass ("urgent", "today", "deadline", …)
  lets likely-urgent notes jump ahead; ties break FIFO.
- **Backpressure & safety**: the thermal guard can pause the worker; the
  resilient client absorbs network blips; pending-writes absorb longer outages.

This hybrid (event-driven + reconciling poll) is the standard robust pattern:
fast in the common case, correct even when events are missed.

---

## 7. Failure handling, by layer

| Failure | Layer that handles it | Behavior |
|---|---|---|
| Device too hot | ThermalGuard | pause heavy work, resume on cooldown |
| Network blip on write | MemosClient | retry with exponential backoff |
| Longer Memos outage | PendingWrites | persist completed enrichment, re-apply later |
| Model returns junk JSON | engine `_normalize` | coerce/drop; never crash downstream |
| Model over-generates corrections | engine guard | drop oversized/no-op corrections |
| Note keeps failing | decision retry ceiling | stop after `max_retries` until it changes |
| `state.json` lost | anchors on notes | agent re-recognizes its own notes |
| Embedding model missing | Embedder fail-safe | fall back to lexical similarity |

The design principle throughout: **degrade, don't break; and never silently lose
the user's work or the model's completed effort.**
