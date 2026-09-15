# Module Map

One line per source file, plus the internal dependency graph. All modules live
under `memos_daemon/`. Entry point is `app.py` → `cli.main()`.

---

## Files

| Module | Lines | Responsibility |
|---|---:|---|
| `daemon.py` | ~808 | The orchestrator: run loop, webhook server, safety poller, work queue, per-note pipeline, dashboards. |
| `calendar_client.py` | ~382 | Optional Google Calendar tool: turn time-bound action items into events. |
| `anchor.py` | ~272 | Hidden on-note state (anchors), boundary markers (`:original`, `:note`), hashing, similarity, human-text extraction, display timestamps. |
| `correlation.py` | ~224 | `Corpus` — pairwise note similarity (semantic or lexical), related notes, clusters, shared terms, cohesion, central note. |
| `formatting.py` | ~227 | Render the Markdown body for suggested and approved notes. |
| `memos_client.py` | ~181 | Resilient Memos REST client (read/write/create/pin) with retry + backoff. |
| `config.py` | ~159 | `DEFAULT_CONFIG`, file + env-var overrides. |
| `engine.py` | ~155 | LiteRT/Gemma wrapper: prompt → inference → parse → normalize → guard. |
| `state.py` | ~98 | Durable `state.json`: per-note records, stats, metrics, dashboard IDs. |
| `embeddings.py` | ~90 | Optional semantic embedding backend, lazy-loaded and fail-safe. |
| `cli.py` | ~84 | Subcommands: `run`, `enrich`, `status`, `thermal`, `reset`, `init-config`. |
| `decision.py` | ~77 | The state machine: `decide()` → `skip/suggest/reenrich/finalize`. |
| `pending.py` | ~74 | Durable queue of completed-but-unwritten enrichments (outage insurance). |
| `logging_setup.py` | ~73 | Logging configuration (file + console, consistent format). |
| `thermal.py` | ~72 | Thermal guard: read SoC temp, pause/resume heavy work. |
| `prompt.py` | ~48 | The system prompt (schema, reconstruction reasoning, correction rules). |
| `__init__.py` | ~4 | Package init; exposes `cli`. |
| `app.py` | ~6 | Thin entry point → `cli.main()`. |

---

## Dependency graph (internal imports)

```
app.py
 └─ cli ──┬─ config ──── logging_setup
          ├─ state ───── logging_setup, anchor
          ├─ memos_client ─ logging_setup
          ├─ thermal ──── logging_setup
          ├─ engine ───── logging_setup, prompt
          └─ daemon ──┬─ logging_setup
                      ├─ state
                      ├─ memos_client
                      ├─ pending ──── anchor, logging_setup
                      ├─ thermal
                      ├─ engine
                      ├─ decision ─── anchor
                      ├─ anchor
                      ├─ formatting ─ anchor
                      ├─ calendar_client ─ logging_setup
                      ├─ correlation ─ logging_setup
                      └─ embeddings ─ logging_setup
```

### Reading the graph
- **`anchor.py` is the foundation** of the note format: `decision`, `formatting`,
  `state`, and `pending` all build on it. It has no internal dependencies.
- **`logging_setup.py` and `prompt.py`** are leaves (no internal deps), imported
  widely.
- **`daemon.py` is the hub** — it wires every collaborator together. Nothing
  imports `daemon` except `cli`.
- **No cycles.** The graph is a clean DAG: leaves (anchor, logging_setup, prompt)
  → mid-level collaborators → daemon → cli → app.

### Layering, conceptually
```
 entry:        app → cli
 orchestration: daemon
 collaborators: engine, memos_client, correlation+embeddings,
                calendar_client, thermal, decision, formatting,
                state, pending
 foundation:   anchor, prompt, config, logging_setup
```
