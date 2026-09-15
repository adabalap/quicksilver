# Configuration Reference

All settings live in `DEFAULT_CONFIG` (in `config.py`) and can be overridden by a
JSON file at `~/.config/memos_daemon/config.json`. Generate that file with:

```bash
python3 app.py init-config        # add --force to overwrite
```

A few settings can also be set via environment variables (handy for
containers/services):

| Env var | Overrides |
|---|---|
| `MEMOS_DAEMON_MODEL_PATH` | `model_path` |
| `MEMOS_DAEMON_BASE_URL` | `memos_base_url` |
| `MEMOS_DAEMON_TOKEN` | `memos_token` |
| `MEMOS_DAEMON_POLL_INTERVAL` | `poll_interval_seconds` |
| `MEMOS_DAEMON_DRY_RUN` | `dry_run` |

Precedence: **env var → config.json → built-in default.**

---

## Core (you must set these)

| Key | Default | Meaning |
|---|---|---|
| `model_path` | `""` | Path to the Gemma `.litertlm` model file. **Required.** |
| `memos_base_url` | `http://localhost:5230` | Your Memos server URL. |
| `memos_token` | `""` | Memos API access token. **Required.** |

---

## Display & behavior

| Key | Default | Meaning |
|---|---|---|
| `display_timezone` | `Asia/Kolkata` | Timezone for human-readable timestamps in notes/dashboards. IANA name; short label (IST/UTC) derived automatically. |
| `note_url_template` | `{base}/memos/{uid}` | How related-note links are built. This instance serves `/memos/<uid>`; some use `/m/<uid>`. |
| `dry_run` | `false` | Decide and log, but don't write anything back. Great for first runs. |
| `max_tags` | `3` | Max tags shown per note. |
| `add_title_if_missing` | `true` | Add an AI title when the note has no heading. |
| `enrichment_timeout_seconds` | `60` | Cap on a single inference. |
| `state_file` | `~/.cache/memos_daemon/state.json` | Durable agent state. |
| `log_file` | `~/.cache/memos_daemon/daemon.log` | Log destination. |

---

## Note layout labels

| Key | Default | Meaning |
|---|---|---|
| `enriched_label` | `Enriched Note` | Subtitle for the enriched body on a suggestion. |
| `note_label` | `Note` | Subtitle for the editable body on an approved note (also the `:note` region). |
| `review_tag` | `pending-approval` | Tag added to every suggestion, removed on approval. Make a shortcut for `tag:pending-approval`. |

---

## Re-enrichment

| Key | Default | Meaning |
|---|---|---|
| `reenrich_similarity_threshold` | `0.90` | When an approved note is edited, if the new text is at least this similar to the baseline, treat the edit as negligible and re-lock without re-running the model. |
| `max_retries` | `3` | Stop re-attempting a repeatedly-failing note until it changes. |

---

## Dashboards

| Key | Default | Meaning |
|---|---|---|
| `dashboard_enabled` | `true` | Maintain the status dashboard. |
| `dashboard_title` | `🤖 AI Agent Dashboard` | Its title. |
| `dashboard_tag` | `agent-dashboard` | Its discovery tag (make a shortcut for `tag:agent-dashboard`). |
| `connections_enabled` | `true` | Maintain the relationship map. |
| `connections_title` | `🔗 Note Relationships` | Its title. |
| `connections_tag` | `note-relationships` | Its discovery tag. |
| `pin_maintained_notes` | `false` | Pin the dashboards? Off by default — use tags + shortcuts instead. |

---

## Note relationships (correlation)

| Key | Default | Meaning |
|---|---|---|
| `correlation_enabled` | `true` | Find and link related notes. |
| `related_top_n` | `3` | Max related notes linked from each note. |
| `related_threshold` | `0.12` | Min **lexical** similarity to relate. |
| `cluster_threshold` | `0.18` | Min **lexical** similarity to group. |
| `embed_related_threshold` | `0.45` | Min **semantic** similarity to relate. |
| `embed_cluster_threshold` | `0.55` | Min **semantic** similarity to group. |
| `correlation_corpus_size` | `200` | How many recent notes to consider. |
| `correlation_cache_seconds` | `120` | Cache the corpus this long to avoid re-paging every note. |

The agent auto-selects the lexical vs. embedding thresholds based on whether the
embedding backend is active. Lexical and embedding cosine live on different
scales, hence two sets.

---

## Semantic embeddings (fail-safe)

| Key | Default | Meaning |
|---|---|---|
| `embed_model` | `all-MiniLM-L6-v2` | sentence-transformers model name or local path. **Requires `pip install sentence-transformers`** to actually activate; otherwise the agent falls back to lexical similarity automatically. Set to `""` to force lexical. |
| `embed_cache_dir` | `""` | Optional local cache/download folder for the model. |

> **Note on cost:** `sentence-transformers` pulls in PyTorch (large on ARM). On a
> RAM-limited phone, weigh this against keeping lexical similarity. The startup
> log shows which backend is active: `relationships: semantic (embeddings)` vs
> `relationships: lexical (keyword)`. See `OPERATIONS.md` for the lighter-weight
> alternatives under consideration.

---

## Quality controls

| Key | Default | Meaning |
|---|---|---|
| `confidence_review_threshold` | `0.6` | Below this overall model confidence (or if there are uncertain corrections), the suggestion's approve box is labeled "low confidence." |
| `priority_queue_enabled` | `true` | Process likely-urgent notes first. |

---

## Thermal guard

| Key | Default | Meaning |
|---|---|---|
| `thermal_enabled` | `true` | Pause heavy work when the device is hot. |
| `thermal_hot_celsius` | `70.0` | Pause at/above this SoC temperature. |
| `thermal_resume_celsius` | `60.0` | Resume at/below this (hysteresis). |
| `thermal_zone_glob` | `/sys/class/thermal/thermal_zone*/temp` | Where to read temperature. |
| `thermal_poll_seconds` | `10` | How often to recheck while paused. |
| `thermal_max_wait_seconds` | `600` | Give up waiting after this long. |
| `idle_backoff_max_seconds` | `300` | Max idle sleep between polls when nothing's happening. |

---

## Write resilience

| Key | Default | Meaning |
|---|---|---|
| `write_max_retries` | `4` | Retry transient write failures this many times. |
| `write_backoff_base` | `0.5` | First backoff delay (seconds), doubling each retry. |
| `write_backoff_cap` | `8.0` | Max backoff delay (seconds). |
| `pending_writes_file` | `""` | Where to persist unwritten enrichments; blank → `<state_file>.pending`. |

---

## Google Calendar (optional)

See **[CALENDAR_SETUP.md](CALENDAR_SETUP.md)** for the full step-by-step.

| Key | Default | Meaning |
|---|---|---|
| `calendar_enabled` | `false` | Create calendar events from time-bound action items in approved notes. Turn on after authorizing. |
| `calendar_client_secret_file` | `""` | Path to the OAuth "Desktop app" client-secret JSON from Google Cloud Console. |
| `calendar_credentials_file` | `""` | Path where `token.json` is written by `app.py calendar-auth` (and read at runtime). |
| `calendar_id` | `primary` | Target calendar; `primary` is your main one, or a specific calendar ID. |
| `calendar_timezone` | `Asia/Kolkata` | IANA tz for events. |
| `calendar_default_duration_minutes` | `30` | Event length when no end time is detectable. |
| `calendar_lookahead_days` | `30` | Only schedule within this many days (0 = no limit). |
| `calendar_add_description` | `true` | Include the note's title + summary in the event. |

One-time authorization:
```bash
python3 app.py calendar-auth            # browser flow (laptop/desktop)
python3 app.py calendar-auth --headless # copy/paste flow (Termux/Android/SSH)
```

---

## Webhook (event-driven mode)

| Key | Default | Meaning |
|---|---|---|
| `webhook_enabled` | `true` | Hybrid mode (webhook + safety poll). If false, interval polling only. |
| `webhook_host` | `0.0.0.0` | Address the agent **listens** on. `0.0.0.0` = all interfaces (fine). |
| `webhook_port` | `19283` | Listen port. |
| `webhook_path` | `/memos-hook` | Listen path. |
| `webhook_advertise_url` | `""` | The URL **Memos connects to**. Blank → auto-derive a connectable URL (`0.0.0.0`/`localhost` → `127.0.0.1`). Set to a LAN IP/host if Memos is on another machine. **Never `0.0.0.0`.** |
| `webhook_secret` | `""` | Optional shared secret; if set, required as `?token=` on the URL. |
| `safety_poll_seconds` | `600` | Reconciling poll cadence in webhook mode. |
| `poll_interval_seconds` | `30` | Poll cadence when webhook mode is off. |
| `page_size` | `50` | How many notes to fetch per API page. |

---

## Minimal working config

```json
{
  "model_path": "/path/to/gemma.litertlm",
  "memos_base_url": "http://localhost:5230",
  "memos_token": "YOUR_TOKEN",
  "display_timezone": "Asia/Kolkata"
}
```

Everything else has a sensible default.
