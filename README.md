# పాదరసం · Quicksilver

> **A notebook that thinks about your notes, without sending them anywhere.**

Quicksilver is a local-first, AI-assisted notebook that runs on Android through Termux. Write the way you would write on paper: messy, fast, and unpunctuated. A local AI agent reads each note in the background and quietly adds what makes it useful later: a meaningful title, tags, a summary, related notes, extracted tasks, reminders, and semantic search data.

Your note is saved immediately. AI enrichment happens asynchronously and never blocks capture.

<p align="center">
  <strong>On-device · No account · No cloud</strong>
</p>

> **పాదరసం** means *quicksilver*. It moves, it joins, it does not stain.

## Contents

- [What It Does](#what-it-does)
- [A Note's Journey](#a-notes-journey)
- [What You Control](#what-you-control)
- [Privacy](#privacy)
- [Architecture](#architecture)
- [Under the Hood](#under-the-hood)
- [Durability Decisions](#durability-decisions)
- [The Token Budget](#the-token-budget)
- [Design Principles](#design-principles)
- [Repository Structure](#repository-structure)
- [Requirements](#requirements)
- [Running It](#running-it)
- [Configuration](#configuration)
- [Project Status](#project-status)
- [Security and Responsible Use](#security-and-responsible-use)
- [Contributing](#contributing)
- [License](#license)

## What It Does

Capture is the only thing you have to do. Everything below happens automatically in the background on your device.

### 🏷️ Titles and tags

Every note receives a meaningful title and a few useful tags, so a year of notes stays searchable instead of becoming a wall of first lines.

### ✍️ Tidier writing

Typos, broken grammar, and dictation slips can be cleaned up while preserving names, numbers, and quotations. The original text is always retained.

### ✅ Tasks pulled out

A sentence such as:

> I should ping David.

can become a structured task in the Actions list without being entered twice.

### 🗓️ Dates on your calendar

A phrase such as:

> Next Monday at 2 PM.

can become a calendar event. Vague or unresolved dates remain pending rather than being guessed.

### 🔗 Notes that find each other

Notes about the same subject are connected by meaning, not merely by shared words. An older note can resurface when it becomes relevant again.

### 🔍 Search that understands

Search for an idea such as “that vendor conversation” and find the note even if you never used the word “vendor.”

## A Note's Journey

From the moment you press **Save** to the moment a note becomes part of your knowledge graph, enrichment happens entirely in the background.

1. **You capture**  
   Type or dictate. The note is saved and readable immediately. Nothing waits on the AI.

2. **The agent notices**  
   A background process watches the database through a live event stream and detects the note in under a second.

3. **The model reads it**  
   A 2.6 GB language model runs on the phone and produces a title, summary, tags, tasks, and reminders. If requested, the model also creates a cleaner version of the text.

4. **It is linked and filed**  
   The note is converted into an embedding so it can be compared by meaning with everything else you have written. Related notes are linked in both directions.

5. **It goes live**  
   There is no approval step. The enriched note becomes available automatically, while the original remains accessible in revision history.

The note remains usable throughout the process. Enrichment adds to it and never blocks it.

## What You Control

The AI is a collaborator with clear limits. Those limits can be set per note.

| Mode | What you get | Your words |
|---|---|---|
| **Full** | Title, tags, summary, tasks, reminders, and a rewrite | Rewritten, original retained |
| **Metadata** | Title, tags, summary, tasks, and reminders | Untouched |
| **Off** | No AI enrichment | Untouched |

Select the mode while writing from the control beside **Save**, or later from the note's **⋯** menu.

The choice is per note and is not sticky. Setting one note to **Off** does not silently change the mode of later notes.

- **Off** takes effect immediately.
- **Full** and **Metadata** apply to the next enrichment run.
- Changing the mode on an already-settled note does not alter it until enrichment runs again.

### Nothing is a dead end

- **Every rewrite is reversible.** The pre-polish text is stored as a revision and can be restored.
- **Hand-edited notes are protected.** The agent does not overwrite a note after you manually edit it.
- **Dismissals are remembered.** A rejected task does not return during the next run.
- **Calendar ownership remains clear.** Events are created only from dates in your notes. Deleting a note cleans up the events created from it.
- **Protected notes stay verbatim.** The agent may describe and tag a protected note, but it does not reword it.

## Privacy

This is where your notes actually live.

### 📵 No cloud and no account

There is no Quicksilver server to sign up for. Notes live in a single SQLite database on your device.

### 🧠 The model is local

Gemma runs using the phone's own compute. Enrichment continues to work in airplane mode because no model round trip is required.

### 🚪 Remote access is RAM-only

When the application is opened from another device, content is not permanently cached there. The local client cache is removed for a non-local origin by design.

### 🗓️ Calendar is the deliberate exception

If Google Calendar integration is enabled, event titles and times are sent to Google Calendar. No other note content is sent as part of that integration.

The light or dark theme preference may persist on the client. It carries no note content.

## Architecture

Quicksilver uses two independent processes connected through a shared SQLite database. The UI handles capture and retrieval, while the agent performs asynchronous enrichment using local inference.

<p align="center">
  <a href="docs/images/quicksilver-architecture.png">
    <img src="docs/images/quicksilver-architecture.png" alt="Quicksilver user journey and on-device architecture" width="100%">
  </a>
</p>

<p align="center">
  <em>A note is available immediately after capture. Enrichment happens asynchronously and on-device, with Google Calendar as the only optional external path.</em>
</p>

> Place the architecture image at `docs/images/quicksilver-architecture.png` in this repository.

### Two processes, one durable contract

The interface and agent do not communicate through a synchronous application API. Both operate against the same SQLite database:

- `hg_ui` saves notes and provides the notebook experience.
- `hg_agent` detects new work and writes enrichment back.
- SQLite stores notes, enrichments, embeddings, tasks, reminders, revisions, and durable deletion requests.
- Server-Sent Events reduce detection latency but are not required for correctness.
- Gemma performs local language-model inference.
- MiniLM generates embeddings for semantic retrieval and related-note discovery.
- Google Calendar is an optional external integration used only for calendar events.

Either process can be restarted, upgraded, or stopped independently. If the agent is unavailable, Quicksilver remains a usable notebook and pending notes are enriched later.

### Why SQLite is the interface

A synchronous HTTP API would make the agent a runtime dependency of the notebook. If the daemon were restarting, unavailable, or thermally constrained, the user could encounter an error while performing an operation that should not require AI.

Using SQLite changes the failure mode from **error** to **latency**. A note captured while the agent is unavailable remains safely stored and is enriched when the agent resumes. The database acts as both the source of truth and the durable work contract.

### How changes are detected

For lower latency, the interface publishes a **Server-Sent Events**, or SSE, stream that the agent subscribes to. A slower polling mechanism remains as a safety net, so SSE is an optimization rather than a requirement.

## Under the Hood

Two small processes, one database, and no dependency on a network connection.

| Piece | Implementation |
|---|---|
| **Interface** | Flask-served Progressive Web App, installable and offline-capable, with no build step |
| **Agent** | Python daemon that watches the database and performs enrichment |
| **Model** | Gemma-4-E2B-it, 2.6 GB, through LiteRT-LM, with 32K context capability |
| **Storage** | SQLite for notes, enrichments, tags, tasks, reminders, embeddings, and revisions |
| **Search** | SQLite FTS5 keyword search plus 384-dimensional MiniLM embeddings with hybrid ranking |
| **Runtime** | Termux on Android, without root or special permissions |
| **Calendar** | Optional Google Calendar integration for event titles and times |

### Why on-device is slower, and still right

A cloud-hosted model may respond in a few seconds. On a phone, enrichment can take closer to a minute or more.

In return, Quicksilver provides:

- Notes that remain on your device
- No Quicksilver account
- No subscription
- No model rate limit
- Offline operation
- Independence from a hosted AI service

The agent is asynchronous so inference latency stays outside the capture experience. You can continue writing while enrichment completes in the background.

## Durability Decisions

1. **Deletions outlive their rows**  
   Calendar events queued for removal live in a separate table. Deleting a note can remove its reminders while leaving enough information to clean up the corresponding calendar events.

2. **Retries are bounded and visible**  
   A permanently failing calendar deletion stops after five attempts and records the reason rather than retrying forever.

3. **Idempotency is keyed explicitly**  
   The deletion queue is keyed by calendar event ID, so enqueueing the same deletion twice is a no-op. Reminders are deduplicated by their resolved time slot.

4. **Configuration self-corrects**  
   A stale configuration value that exceeds the loaded model context is clamped at runtime and logged instead of silently truncating a note.

5. **The model is replaceable**  
   Components above the engine layer do not need to know which model is loaded. Changing the model requires a model path and context size rather than an application redesign.

## The Token Budget

The loaded configuration uses a 6,144-token context, although the model supports up to 32K.

```text
6,144 tokens loaded

System prompt       approximately 2,400 tokens
Typical note        approximately   130 tokens
Reserved response                 3,072 tokens
```

The governing rule is:

```text
system prompt + note + reserved output <= model context
```

At these settings, a note of approximately 480 words can be fully rewritten.

A short note is still accompanied by the full instruction set, which is why tiny notes are not instant. Increasing the context permits larger notes but slows every inference because the KV cache grows with it.

The current 6,144-token setting is a measured balance for the target hardware. Configuration values that govern context and polishing limits move together, and tests verify that they remain coherent.

## Design Principles

1. **Capture is sacred**  
   Nothing may delay or complicate writing a note. AI work happens after capture, in the background.

2. **Your words are yours**  
   The AI describes freely and rewrites carefully. Rewrites are reversible and can be disabled per note.

3. **A switch about the future must not rewrite the past**  
   Turning AI off stops the next run. It does not hide or remove enrichment already attached to a note.

4. **No action may be a dead end**  
   Dismissals can be undone, rewrites restored, and failures explain what happened.

5. **Silence earns trust**  
   The application does not generate empty notifications merely to announce that nothing happened.

6. **Do not ask twice**  
   When deleting a note, calendar events created by that note are cleaned up as part of the same intent.

7. **Optimistic, then honest**  
   Controls respond immediately and reconcile with persisted state. If an operation fails, the UI reports it and restores the correct state.

8. **Latency, not error**  
   When a component is unavailable, the system slows down rather than breaking.

## Repository Structure

```text
quicksilver/
├── docs/
│   └── images/
│       └── quicksilver-architecture.png
├── hg_ui/              # Flask PWA and user interface
├── hg_agent/           # Local enrichment agent and model integration
├── replay_archives.sh  # Archive replay utility
└── README.md
```

## Requirements

Quicksilver is designed for:

- Android
- Termux
- Python
- SQLite with FTS5 support
- LiteRT-LM-compatible local model
- Sufficient device storage and memory for the model runtime

Google Calendar credentials are required only if calendar synchronization is enabled.

## Running It

### UI commands

```bash
hg_ui start
hg_ui stop
hg_ui restart
hg_ui status
hg_ui logs
```

### UI data operations

```bash
hg_ui backup
hg_ui restore
hg_ui clear
```

### Agent commands

```bash
hg_agent start
hg_agent stop
hg_agent restart
hg_agent status
hg_agent logs
```

### Optional calendar authorization

```bash
hg_agent calendar-auth
```

This performs the one-time Google consent flow required for calendar integration.

### Maintenance commands

```bash
hg_agent relations
hg_agent backfill-embeddings
```

## Watching It Think

Follow the agent log in Termux:

```bash
tail -f "$PREFIX/var/log/sv/hg_agent/current"
```

A healthy enrichment flow includes messages similar to:

```text
decision: suggest
generating (80 words, polish=y)
inference returned in ...
adopted polished text as new body
auto-approved in QS
```

## Configuration

Configuration is stored at:

```text
~/.quicksilver/config.quicksilver.json
```

The configuration file is a **sparse override**. Values you omit use application defaults, allowing defaults to improve over time.

Override only the settings you need. A pinned value remains fixed until you change or remove it.

## Project Status

Quicksilver is currently **v1.0** and is built primarily as a personal, local-first notebook. Interfaces, configuration options, model choices, and setup procedures may evolve.

## Security and Responsible Use

Quicksilver reduces cloud exposure by keeping notes and inference local, but local-first does not mean risk-free.

Users should still:

- Protect the Android device with a strong screen lock
- Restrict remote access to trusted networks or a secure private tunnel
- Back up the SQLite database securely
- Protect Google Calendar credentials if calendar integration is enabled
- Review exposed ports and Termux services before allowing access outside the home network

## Contributing

Issues, ideas, documentation improvements, and code contributions are welcome.

Before submitting a change:

1. Keep capture fast and independent of AI availability.
2. Preserve original user content and revision history.
3. Prefer failure modes that create latency rather than data loss or user-visible errors.
4. Avoid introducing network dependencies into the core note workflow.
5. Include tests for changes affecting persistence, retries, token limits, or enrichment behavior.

## License

No license is currently declared in this README. Add a `LICENSE` file and update this section before encouraging redistribution or external contributions.

---

<p align="center">
  <strong>పాదరసం · Quicksilver</strong><br>
  A local-first thinking notebook<br>
  On-device · No account · No cloud
</p>

