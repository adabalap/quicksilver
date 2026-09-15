# Changelog — v7.0

## Features

### 1. Relative date resolution for calendar reminders
- **Deterministic post-processor** (`calendar_client.py → resolve_relative_date`) resolves relative date phrases against the real system clock — no model guessing.
- Supported patterns: `today`, `tonight`, `tomorrow`, `yesterday`, `next <weekday>`, `this <weekday>`, bare `<weekday>`, `in N day(s)/week(s)`.
- Time extraction from the same phrase: `tomorrow at 10am`, `next Friday at 3:30 PM`, `today at 14:00`.
- **Date-only → all-day event**: when a reminder has a resolved date but no time (e.g. "call John tomorrow"), an all-day Google Calendar event is created instead of requiring a time or dumping to pending. Cleaner than inventing a default time.
- **Prompt updated**: model may now emit relative phrases in `datetime_hint` (previously only ISO 8601 was accepted); the post-processor handles them. This gives the model more latitude to express what the note actually says.

### 2. Summary suppression for short notes
- New config key `summary_min_words` (default `20`). When the note's user text is fewer than this many words, the `**Summary:**` section is omitted from both SUGGESTED and APPROVED note layouts.
- Rationale: on a 10-word note the summary is just a paraphrase of the note itself — noise. The note IS its own summary.
- Set to `0` to always include summaries (v6.x behaviour).
- Prompt rule updated to align: model should return `""` for very short notes.

### 3. Cross-note TODO Dashboard
A new maintained note (`📋 Action Items`) aggregates `action_items` across all approved notes.

**Architecture:**
- `todo_store.py`: lightweight JSON-backed index keyed by `(note_name, item_text)`. Persistent across restarts. Survives note re-enrichments (done state preserved when the same item reappears).
- Dashboard note is rendered and upserted after every approval + after every poll that sees changes.
- Items grouped by primary tag (work, finance, health, shopping, etc.) with tag-appropriate emoji headers.
- Hidden `<span data-note="...">` on each line carries the source note name for checkbox sync.

**Checkbox sync:**
- When user ticks/unticks an item on the TODO dashboard, the daemon detects the change (via webhook or next poll), propagates it to the matching `- [ ]` line in the source note, and updates the done state in the store.
- Completed items move to a collapsible `<details>` block (archived, not deleted). Max 50 shown there.
- Checking an item does NOT trigger re-enrichment of the source note (hash is updated to suppress it).

**New config keys:**
```json
"todo_enabled": true,
"todo_title": "📋 Action Items",
"todo_tag": "todo-dashboard",
"todo_file": ""
```

**State:** `todo_id` added to `state.json` (auto-migrated on first run).
**Decision:** todo dashboard note is skipped by `decide()` (same as agent-dashboard and note-relationships).

## Files changed
- `memos_daemon/todo_store.py` — **new**
- `memos_daemon/calendar_client.py` — relative date resolver + all-day events
- `memos_daemon/prompt.py` — summary gate rule; relative datetime_hint guidance
- `memos_daemon/formatting.py` — `summary_min_words` gate
- `memos_daemon/config.py` — new keys: `todo_*`, `summary_min_words`
- `memos_daemon/state.py` — `todo_id` property
- `memos_daemon/decision.py` — skip todo dashboard + adopt on startup
- `memos_daemon/daemon.py` — TodoStore wiring, `_sync_todos_from_enrichment`, `_check_todo_dashboard_interactions`, `_propagate_todo_check`, `_update_todo_dashboard`

## Migration
No manual migration needed. On first run:
- `todo_id` is added to `state.json` automatically.
- If a `📋 Action Items` note already exists (from a previous manual setup), the daemon adopts it.
- Existing approved notes will populate the TODO index on their next re-enrichment trigger.
