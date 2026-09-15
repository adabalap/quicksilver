# Design Rationale

This document explains *why* the agent is built the way it is. Each section is a
decision, the alternatives considered, and the reasoning — including the
real-world bugs and constraints that shaped the choice. If `ARCHITECTURE.md` is
the "what," this is the "why."

---

## Why on-device, with a small local model

**Decision:** run a small model (Gemma via LiteRT) locally, never call a cloud API.

Notes are among the most personal data people keep. Sending them to a hosted LLM
to be "cleaned up" trades privacy for convenience in a way that's hard to undo.
A small on-device model is less capable than a frontier cloud model, but for the
task — tidying prose, summarizing, extracting tasks — it is good enough, and the
privacy property is absolute: the data never leaves the device.

This single constraint drives much of the rest of the design: inference is
**slow** (30–45s/note) and **memory is scarce** (a few GB), so we must be
economical with tokens, serialize inference, guard the device's temperature, and
think hard before adding a second model.

---

## Why "propose, don't overwrite" (human-in-the-loop)

**Decision:** every enrichment is a *suggestion* the user approves; the agent
never silently rewrites a note.

An agent that edits your notes autonomously is only trustworthy if it's always
right — which a small model isn't. The cost of a wrong silent edit (a corrupted
fact, a lost nuance) is high and invisible. The cost of a suggestion you can
reject is trivial. So the agent proposes, shows its work, and waits. Trust is
earned by being inspectable, not assumed.

This is why the note keeps your **original on top** while under review, labels
the AI's version **Enriched Note**, lists any word-level **Corrections**, and
tags the note `#pending-approval` until you act.

---

## Why explicit boundary markers in the note

**Decision:** fence the human text with `<!-- memos-ai:original -->` (suggestions)
and `<!-- memos-ai:note -->` (approved), rather than guessing where it is.

**The bug that forced this:** an earlier version treated "everything above the
first `---`" as the user's text. But the layout also placed the AI title and
summary above a divider, so on every re-edit the AI's own output got folded back
in and re-summarized — a slow corruption loop. Explicit markers make extraction
unambiguous: the human text is exactly what's between the markers, no heuristics.

**The second bug it fixed:** when you "uncheck to reprocess" an approved note,
the agent used to reconstruct the source from a stale baseline stored at creation
time, mixing it with your edits. With the `:note` markers, re-processing uses
*exactly what's in the body now* — your edits included — as the source of truth.

---

## Why state lives both on the note and in a state file

**Decision:** keep a hidden JSON "anchor" inside each note **and** a `state.json`
record off to the side.

- The **anchor** lets the agent recognize its own work — its status, the hash of
  the text it enriched — even after a restart or a wiped state file. Without it,
  a fresh agent would re-enrich every note it had already done.
- The **state file** lets the agent detect change (compare hashes), throttle
  retries (failure counts), and advance a watermark — cheaply, without parsing
  every note's anchor on every poll.

Earlier designs embedded human-visible status markers (`🔒 Approved`) that the
user had to hand-delete to re-trigger. Hidden anchors + an explicit approve
checkbox are cleaner: the user toggles one checkbox, the agent reads structured
state.

---

## Why a hybrid webhook + safety-poll model

**Decision:** process notes on Memos webhooks for low latency, but also run a
slow reconciling poll.

Webhooks are fast but unreliable — they can be misconfigured, the listener can be
down, events can be dropped. A poll is reliable but slow. Running both gives the
best of each: instant in the common case, correct even when an event is missed.

**The bug that proved its worth:** a webhook was once registered with a
`0.0.0.0` URL (un-connectable), so every event failed — yet notes still got
processed, because the safety poll caught them. Webhook failure degraded latency,
not correctness. (We then split "bind address" from "advertise URL" so the agent
prints a connectable URL and warns on `0.0.0.0`.)

---

## Why one worker thread and a priority queue

**Decision:** a single worker drains a priority queue; multiple producers feed it.

There is one in-memory model and limited RAM. Two concurrent inferences would
contend for memory and likely thrash. Serializing through one worker keeps memory
predictable. The **priority queue** (cheap keyword pre-pass for "urgent/today/
deadline") ensures that if you've just dictated something time-sensitive and are
about to lock the phone, it gets processed first.

---

## Why so much resilience plumbing

**Decision:** retries with backoff, a durable pending-writes queue, a thermal
guard, output normalization, a retry ceiling.

Each of these traces to a real failure mode of the deployment target:

- **Phones get hot.** A thermal runaway during sustained inference is real; the
  guard pauses work above a threshold and resumes on cooldown.
- **Local servers blink.** Memos restarting for a few seconds once discarded a
  completed 33-second inference. Now writes retry, and if the outage outlasts the
  retries, the finished enrichment is persisted and re-applied — never wasted.
- **Small models emit junk.** Sometimes invalid JSON, sometimes an empty action
  item, sometimes a "correction" that's the whole note pasted twice. The engine
  normalizes and guards output so a model misstep can't crash the agent or
  produce garbage notes.
- **Some notes just fail repeatedly.** A retry ceiling stops the agent from
  hammering a pathological note forever; it tries again only once the note
  changes.

The throughline: **the deployment environment is hostile (constrained, flaky),
so robustness is a feature, not a nicety.**

---

## Why the transcription reconstruction is "asymmetric"

**Decision:** aggressively fix ordinary words, but be cautious with proper nouns,
brands, acronyms, and numbers.

Voice-to-text errors are phonetic ("terran" → "team", "Sand" → "status"). A good
agent reconstructs intended meaning rather than preserving garble. But aggressive
guessing is exactly what produces a confident wrong answer on a *name* — and a
mis-corrected proper noun or number silently changes a fact, which is costly and
hard to notice. A mis-flagged correct guess, by contrast, costs you a glance.

So the rule is asymmetric by risk: be bold where errors are cheap and
self-evident (grammar, filler, phonetics), conservative where they're costly and
silent (names, numbers). Uncertain fixes are surfaced in a **Corrections** block
for you to verify, and they route the note into the review queue.

---

## Why corrections are word-level only (and guarded in code)

**Decision:** the Corrections list logs only discrete word/phrase substitutions,
never formatting or whole-text rewrites — enforced by both prompt and code.

**The bug:** on a clean run-on note, the model "corrected" it by adding
punctuation and flow, then logged the *entire note* as one correction — the whole
original vs. the whole rewrite. That's redundant (both versions are already
visible) and doubles output tokens on an already-slow device.

The fix is defense in depth: the prompt forbids it, and a code guard drops any
"correction" longer than 15 words or that differs only in case/punctuation. The
principle — **never make the model regenerate text the user can already see** —
is also a token-economy principle, which matters a lot at 30–45s/note.

---

## Why relationships started lexical and moved toward semantic

**Decision:** group related notes by similarity; prefer semantic embeddings, but
fall back to lexical keyword overlap.

Keyword overlap (TF-IDF) is free and needs no extra model, but it misses notes
about the same idea in different words ("VDI latency" vs "Zscaler slowness").
Semantic embeddings capture meaning and link those — at the cost of a second
model in memory. On a phone with little RAM, that cost is real.

So the engine is pluggable: embeddings when available, lexical otherwise, **same
interface either way**. The agent never breaks if the embedding model is absent;
it just groups a little less cleverly. The choice of whether to pay the memory
cost is left to the operator and the data we're now collecting.

The relationship dashboard is also built to *explain itself* — showing the
shared terms behind each group, a strength meter, and the central note — because
an opaque "these are related, trust me" is far less useful than "these are
related because they share X, and the link is strong."

---

## Why tags + shortcuts instead of pinning

**Decision:** the maintained dashboards and the review queue are reachable via
tags (`#agent-dashboard`, `#note-relationships`, `#pending-approval`) and Memos
Shortcuts, not by pinning.

Pinning is manual, position-based, and clutters the top of the note list; it
doesn't scale past a couple of items. Tag + saved-filter shortcuts are the
idiomatic Memos way: one click, survive note recreation (matched by tag, not ID),
and stay out of the way.

**The bug it created and we fixed:** the agent dashboard once printed
`#pending-approval` as literal text, so Memos parsed it as a real tag and the
dashboard showed up in the user's review queue. Lesson: a maintained note must
never emit a *live* tag it doesn't intend to belong to. The dashboard now
describes the queue in plain text.

---

## Recurring principles

1. **Privacy is non-negotiable** → on-device, always.
2. **The user is in control** → propose, show work, wait for approval.
3. **Degrade, don't break** → every dependency can fail; none should be fatal.
4. **Never lose work** → not the user's words, not a completed inference.
5. **Be economical** → tokens and memory are scarce; don't waste either.
6. **Be inspectable** → log breadcrumbs, explain groupings, surface corrections.
7. **Defense in depth** → prompt *and* code enforce the important invariants.
