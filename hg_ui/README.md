# పాదరసం (hg_ui) — AI-powered note taking PWA

Flask + SQLite + single-file PWA. The Hg agent (hg_agent) reads/writes the
same SQLite database directly and pushes live updates via /api/webhook → SSE.

## Quick start (Termux)

```bash
pip install flask setproctitle
./bin/hg_ui start          # → http://localhost:5230
./bin/hg_ui status|logs|stop|restart
```

Open in Chrome → three-dot menu → "Add to Home Screen".

Both processes are identifiable in `ps -ef` as `hg_ui` and `hg_agent`.

## Environment

| Var      | Default                   |
|----------|---------------------------|
| QS_PORT  | 5230                      |
| QS_HOST  | 0.0.0.0                   |
| QS_DB    | ~/.quicksilver/notes.db   |

## Note lifecycle

raw → (agent enriches) → pending → (you tap ✓ Approve) → approved
Edit an approved note's body → status flips to force → agent re-enriches.
Tap ⟳ Redo any time to force reprocessing.

All AI output (title, summary, tags, tasks, relationships) lives in dedicated
DB tables — your note body stays exactly as you wrote it.
