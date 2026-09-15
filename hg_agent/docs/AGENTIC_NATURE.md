# The Agentic Nature of This System

A common question: is this "really an agent," or just a script that calls an LLM?
This document answers that directly, mapping the system to the properties that
distinguish an autonomous agent from a one-shot tool — and pointing to exactly
where each property lives in the code.

---

## The short answer

This is an agent in the meaningful sense: it **perceives** its environment,
**decides** on its own what to do, **acts** through tools, **remembers** across
time, **recovers** from failure, and **maintains** artifacts in the world without
being asked each time. A user never types "summarize note X." The agent notices,
judges, acts, and reports — continuously, on its own.

Below, each agentic property and where it shows up.

---

## 1. Autonomy & continuous operation

The agent runs as a persistent process with its own life cycle. It is not
invoked per request by a human; it **watches** Memos and acts when something
changes.

- **Perception:** a webhook server and a safety poller both detect note changes
  (`daemon.py`).
- **Continuous loop:** a worker thread processes work indefinitely, idling
  between events, surviving restarts via durable state.

A script runs once and exits. This runs until you stop it, doing work whenever
the world changes.

---

## 2. Goal-directed decision-making (not a fixed script)

For every note, the agent **decides** among several actions based on context —
it does not blindly run the same operation.

- `decision.py` is a genuine **state machine**: `skip | suggest | reenrich |
  finalize`, chosen from the note's content, its hidden state, whether the user
  ticked approve, whether the text changed, and how many times it has failed.
- The decision incorporates *intent signals*: a ticked approve box means
  "finalize"; an un-ticked one on an approved note means "the user wants this
  redone."

The agent is reasoning about *what should happen next*, not executing a fixed
recipe.

---

## 3. Tool use

The agent acts on the world through several tools, choosing when each is
appropriate:

- **The Memos API** (`memos_client.py`) — read, write, create, pin notes.
- **The local model** (`engine.py`) — its core reasoning tool, invoked with a
  structured prompt and parsed back into structured data.
- **Google Calendar** (`calendar_client.py`) — when an approved note contains a
  time-bound action item, the agent creates a real calendar event. This is the
  agent reaching outside its own sandbox to take a consequential action in
  another system.
- **The device's thermal sensors** (`thermal.py`) — it reads hardware state to
  decide whether it's safe to work.

Tool use with judgment about *when* to use each is a hallmark of agency.

---

## 4. Memory & state across time

The agent has both short- and long-term memory:

- **Per-note memory** (`state.py`): what it has seen, what it did, the hash of
  the text it enriched, how many times a note failed.
- **On-note memory** (`anchor.py`): hidden anchors let it recognize its own prior
  work directly from the note, even if the state file is lost.
- **Performance memory** (`state.py` metrics): rolling inference latency, counts.
- **Deferred work** (`pending.py`): enrichments it completed but couldn't write
  yet — remembered until it can.

It does not start from zero each time; it builds on what it knows.

---

## 5. Planning & prioritization

Faced with multiple pending notes, the agent does not process blindly FIFO:

- A **priority queue** (`daemon.py`) runs a cheap keyword pre-pass and pushes
  likely-urgent notes ("urgent", "today", "deadline") ahead of routine ones.
- It **sequences** dependent steps within a note: decide → cool-down check →
  enrich → render → write → update state → refresh dashboards.

It orders its work to deliver the most valuable result first.

---

## 6. Reflection & self-correction

The agent inspects and corrects its own outputs and behavior:

- **Output normalization & guards** (`engine.py`): it doesn't trust the model's
  raw output — it validates and repairs it (coerces types, drops empty action
  items, rejects whole-note "corrections").
- **Confidence-gating:** when the model is unsure, the agent flags the note for
  human review rather than presenting a shaky result as confident.
- **Self-recognition:** it refuses to process its own dashboards (by anchor role
  or title), avoiding an infinite loop of enriching its own output.
- **Retry ceiling:** it notices when it keeps failing on a note and backs off.

This is the agent reasoning about the quality and consequences of its own
actions.

---

## 7. Robustness & graceful degradation

An agent operating unattended in a hostile environment must handle failure
itself — there's no human to catch errors in the moment:

- Network blips → retry with backoff (`memos_client.py`).
- Longer outages → persist completed work, re-apply later (`pending.py`).
- Device too hot → pause and resume (`thermal.py`).
- Missing optional model → fall back, don't break (`embeddings.py`).

The agent keeps its goals intact across conditions it didn't choose.

---

## 8. Maintaining artifacts in the world

Beyond per-note work, the agent **curates** standing artifacts on its own
initiative:

- A **status dashboard** it keeps current (activity, performance, health).
- A **relationship map** it continuously rebuilds as notes change — and which
  *explains its own reasoning* (shared terms, link strength, central note).

These aren't requested; the agent maintains them as part of pursuing its goal of
keeping your knowledge base useful.

---

## What it deliberately is *not*

Being honest about the boundaries is part of good design:

- It is **not fully autonomous over your content** — by deliberate choice, it
  proposes and waits for approval. This is a safety property, not a limitation of
  capability.
- It does **not** do open-ended multi-tool planning toward arbitrary goals; its
  goal is fixed (enrich and organize notes) and its tool set is curated.
- Its reasoning is bounded by a **small local model**, so it leans on guardrails,
  confidence-gating, and human review rather than raw model intelligence.

These constraints are intentional. The objective was never "maximally
autonomous" — it was **a trustworthy, private, resilient agent that makes your
notes better while leaving you in control.** Within that objective, it exhibits
the full set of agentic behaviors: perceive, decide, act, remember, plan,
reflect, recover, and maintain.

---

## Map: agentic property → where to look

| Property | Module(s) |
|---|---|
| Perception (events + polling) | `daemon.py` (webhook server, poller) |
| Decision-making (state machine) | `decision.py` |
| Tool use (Memos, model, calendar, sensors) | `memos_client.py`, `engine.py`, `calendar_client.py`, `thermal.py` |
| Memory (per-note, on-note, metrics, deferred) | `state.py`, `anchor.py`, `pending.py` |
| Planning & prioritization | `daemon.py` (priority queue, pipeline) |
| Reflection & self-correction | `engine.py` (guards), `decision.py` (self-skip, retry ceiling) |
| Robustness | `memos_client.py`, `pending.py`, `thermal.py`, `embeddings.py` |
| Maintaining artifacts | `daemon.py` (dashboards), `correlation.py` |
