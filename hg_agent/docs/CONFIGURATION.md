# Configuration reference

Every setting the agent understands, with its default, what it does and an example.
**This file is generated** from `memos_daemon/config.py` and `tools/config_docs_data.py`
(`python3 tools/gen_config_doc.py`); a test fails if a setting is added without a description.

## How to set things

Put only the values you want to change in a JSON file. Anything you leave out keeps its default.

```json
{
  "db_path": "/root/.quicksilver/notes.db",
  "engine": "auto",
  "medha_url": "http://127.0.0.1:8001",
  "medha_token": "mk_live_xxxxxxxx",
  "medha_brief_link": "http://127.0.0.1:5230",
  "media_audio_lang": "te",
  "display_timezone": "Asia/Kolkata"
}
```

The file is found in this order: `--config <path>`, the `HG_AGENT_CONFIG` environment variable, then
`~/.quicksilver/config.quicksilver.json`. A path you name that does not exist, or a file that does not parse, stops the
agent with a clear error instead of silently running on defaults. **Restart the agent after any change.**
Environment shortcuts: `MEMOS_DAEMON_MODEL_PATH`, `MEMOS_DAEMON_DRY_RUN` (and the legacy Memos ones).

Settings you change **in the app** (Settings screen) are separate and are listed in the hg_ui README. They are stored in
the database and take effect at once.

## Recipes

| Goal | Set |
|---|---|
| Save battery and heat | `"embeddings_enabled": false`, `"medha_brief_ai": false`, `"main_loop_tick_seconds": 15`, `"thermal_hot_celsius": 65.0` |
| Snappier reaction to typing | `"enrich_quiet_seconds": 20` |
| A calmer, shorter Now brief | `"medha_brief_reminder_ahead_days": 3`, `"medha_brief_actions_max": 1`, `"medha_brief_ai": false` |
| Told the day before a bill is due | `"medha_brief_notify_days": 1` |
| Keep amounts off the lock screen | `"medha_brief_amounts": false` |
| Telugu voice notes | `"media_audio_lang": "te"`, `"media_audio_prompt": "your names and terms"` |
| Always keep your exact words | `"enrich_polish_by_default": false` |
| Fix typos in short prose too | `"enrich_quick_note_chars": 60` |
| Bills inside Fedora/proot | run `bin/sms-bridge.sh` in native Termux and set `"sms_file": "/path/to/sms.json"` |
| Search and links keyword-only | `"embeddings_enabled": false` |


## Where things live

| Setting | Default | What it does | Example |
|---|---|---|---|
| `backend` | `"quicksilver"` | `quicksilver` reads and writes the hg_ui SQLite database directly. `memos` is the old Memos HTTP API and is kept only for history. | `"backend": "quicksilver"` |
| `db_path` | `""` | Path of the hg_ui database (`notes.db`). Blank means the agent looks in `~/.quicksilver/notes.db`. Must be the same file hg_ui uses (`QS_DB`). | `"db_path": "/root/.quicksilver/notes.db"` |
| `quicksilver_url` | `"http://127.0.0.1:5230"` | Address of the hg_ui server. The agent subscribes to its live event stream here and pings it after writing, so the app updates at once. Wrong port means notes only get picked up by the slow safety poll. | `"quicksilver_url": "http://127.0.0.1:5230"` |
| `state_file` | `"~/.cache/memos_daemon/state.json"` | Small JSON file where the agent keeps its own bookkeeping. Other agent files (pending writes, todos) default to names next to it. | `"state_file": "/root/.quicksilver/daemon_state.json"` |
| `log_file` | `"~/.cache/memos_daemon/daemon.log"` | Where the agent logs. With a supervisor (runit/termux-services) logs go to stdout instead and this is ignored. | `"log_file": "/root/.quicksilver/agent.log"` |
| `pending_writes_file` | `""` | A finished enrichment that could not be written (database busy or app down) is parked here and re-applied later, so a finished inference is never thrown away. Blank means `<state_file>.pending`. | `"pending_writes_file": ""` |
| `display_timezone` | `"Asia/Kolkata"` | IANA zone used for every human-readable time the agent writes (dashboards, brief text, digest). It does not change how dates in your notes are understood. | `"display_timezone": "Asia/Kolkata"`, or `"UTC"` |
| `dry_run` | `false` | `true` runs everything except writing results. Use it to watch what the agent would do. | `"dry_run": true` |

## Choosing the model engine

| Setting | Default | What it does | Example |
|---|---|---|---|
| `engine` | `"auto"` | Which engine runs the model. `auto` uses Medha when it answers and falls back to the built-in LiteRT model otherwise. `medha` always sends text to Medha. `litert` always loads `model_path` in this process. | `"engine": "auto"` |
| `model_path` | `""` | Path of the `.litertlm` model for the built-in engine. Not needed when you only use Medha. | `"model_path": "/root/models/gemma-4-E2B-it.litertlm"` |
| `medha_url` | `"http://127.0.0.1:8080"` | Where Medha listens. Change only if you moved its port or run it on another device. | `"medha_url": "http://127.0.0.1:8001"` |
| `medha_token` | `""` | Bearer token for Medha. Create one in Medha with the capabilities you need: inference, plus `notify` for the Now brief and notifications. A token without `notify` gives a 403 and the brief backs off for 15 minutes. | `"medha_token": "mk_live_..."` |
| `medha_timeout_s` | `300` | Seconds to wait for one Medha call before giving up. | `300` |
| `medha_busy_wait_s` | `120` | When Medha says it is busy (HTTP 429 with a Retry-After), the agent waits up to this long before failing the call. Raise it if Medha is shared with other apps. | `120` |
| `medha_error_backoff_s` | `600` | In `auto` mode, after a Medha server error the built-in model is used for this many seconds before Medha is tried again. | `600` |
| `engine_recheck_s` | `30` | In `auto` mode while running on the built-in model, how often to look for Medha again. | `30` |
| `engine_idle_unload_s` | `300` | In `auto` mode, the built-in model is unloaded after this many idle seconds, freeing about 3 GB of RAM (the camera app and other apps need it). It reloads on the next job, which adds a short delay. 0 keeps it loaded. | `300` |
| `medha_multimodal` | `"auto"` | Whether to send images and audio to Medha. `auto` follows what Medha reports it can do. `true` or `false` force it. | `"auto"` |
| `model_max_num_tokens` | `6144` | Context size (input plus output) the built-in model is loaded with. Bigger fits longer notes but makes every call slower and uses more memory. If you raise it, raise `enrich_skip_polish_words` with it. | `6144`; for very long documents try `12288` |
| `use_session_generation` | `false` | Uses the lower-level generation path that enforces the output limit exactly. Leave off unless output is being cut off oddly. | `false` |
| `prompt_file` | `""` | Path of a text file that replaces the built-in system prompt. Read once at start. Blank is recommended; see `memos_daemon/prompt.py` before editing because the reply format is coupled to the code. | `"prompt_file": ""` |

## Medha Now brief and notifications

| Setting | Default | What it does | Example |
|---|---|---|---|
| `medha_brief` | `true` | Master switch for the Medha Now brief widget. Needs `medha_token` with the `notify` capability. Off, and anything already posted is cleared. | `true` |
| `medha_brief_link` | `""` | Address of Quicksilver that brief lines and notifications open. Must be reachable from the phone's browser: `http://127.0.0.1:5230` on the same device or `https://hg.adabala.com` through the tunnel. **Not** the embedding sidecar port 5232 (that answers 501). Blank means a tap just opens Medha. | `"medha_brief_link": "http://127.0.0.1:5230"` |
| `medha_brief_bills` | `true` | Include bills due soon or overdue. Bills read from messages need Bills switched on in Settings; bills you add by hand always count. | `true` |
| `medha_brief_reminders` | `true` | Include dated reminders from your notes (not bill reminders, which show as the bill). | `true` |
| `medha_brief_followups` | `true` | Include stalled tasks the follow-up pass raised. | `true` |
| `medha_brief_actions` | `true` | Include your longest-waiting open action items, below anything dated. Ticking one off in the app removes it. | `true` |
| `medha_brief_actions_max` | `25` | Most action items on the brief, longest-waiting first. | `25` |
| `medha_brief_max_items` | `50` | Rows on the brief in all. Every item is its own row and opens its own note. Only a list longer than this ends in a "+N more" row. If Medha refuses or trims a long list, the agent posts as many as it accepts and adds that row. | `50` |
| `medha_brief_reminder_back_days` | `7` | A reminder this many days overdue still appears. Lower to 2 or 3 for a tidier brief. | `7` |
| `medha_brief_reminder_ahead_days` | `14` | Reminders up to this many days ahead appear. Raise to 30 to see further out, lower to 3 for a calm brief. | `14` |
| `medha_brief_notify` | `true` | Post a silent phone notification, with a link that opens the note or bill, for what is due today or late. It is cancelled automatically when the item is done. | `true` |
| `medha_brief_notify_max` | `3` | Most notifications at once. | `3` |
| `medha_brief_notify_days` | `0` | How far ahead notifications reach. 0 is due today or late, 1 adds tomorrow, and so on. | `0`; `1` to be told the day before |
| `medha_brief_ai` | `true` | Let the model write one useful line per item from its note, in the background and only when the phone is cool. Off, lines are plain facts like the due date. | `true` |
| `medha_brief_ai_top` | `3` | How many of the top items get a model-written line. | `3` |
| `medha_brief_ai_tokens` | `48` | Output limit for each of those lines (about 110 characters fit in 48). | `48` |
| `medha_brief_amounts` | `true` | Show the amount in a bill's title (for example `ICICI card · ₹2,50,000`). Turn off if the lock screen is visible to others. | `true` |
| `medha_brief_pay_links` | `false` | Deprecated, no effect. A bill row's tap always opens the bill in Quicksilver, where the message and its payment link are. | `false` |
| `medha_brief_refresh_hours` | `6` | An unchanged brief is posted again this often so Medha keeps it fresh and in place. | `6` |
| `medha_brief_ttl_hours` | `24` | How long Medha keeps the brief if the agent stops posting (Medha clamps to 60 seconds to 7 days). | `24` |

## Medha notification stream (captures)

| Setting | Default | What it does | Example |
|---|---|---|---|
| `captures_enabled` | `true` | Makes Medha's notification stream available (Medha v0.20.0 and later, `GET /captures`). It still stays off until you switch it on in Settings, and Medha refuses it until this token's client is given **Read your notifications** — the one capability an app cannot ask for through a consent dialog. | `true` |
| `captures_poll_seconds` | `300` | How often the stream is read. Medha keeps 14 days, so a missed poll loses nothing. | `300` |
| `captures_max_pages` | `20` | A bound on one drain. A backlog larger than this is read over several polls rather than in one burst. | `20` |
| `captures_corroborate_hours` | `72` | How far back a cancellation or payment notice is believed about something still on your brief. A notice older than this is likelier to be about a different meeting than a late-arriving cancellation. Captures never ADD a brief row — they only settle one that was going to be posted anyway. | `72` |

## Saved from Medha's brief

| Setting | Default | What it does | Example |
|---|---|---|---|
| `saved_poll_seconds` | `60` | How often things you pressed **Save** on in Medha's brief are collected into Quicksilver's inbox. Short on purpose: you may open the app straight after pressing it. Medha keeps each save until Quicksilver confirms it has it, so a missed poll delays a save and never loses one. Needs the same **Read your notifications** grant as captures, but not the captures switch in Settings — a save is one notification you chose, not the whole stream. | `60` |

## What enrichment does to a note

| Setting | Default | What it does | Example |
|---|---|---|---|
| `enrich_polish_by_default` | `true` | `true`: the agent writes metadata (title, summary, tags, links) and a cleaned-up rewrite of short prose. `false`: metadata only by default. Per note, Compose's Metadata only and the Polish button override this. | `true` |
| `enrich_quick_note_chars` | `120` | Notes at or below this many characters are quick captures kept exactly as written. Lower it (80) so short prose also gets typos fixed; raise it (300) to leave more notes alone; 0 means only list-shaped notes are left alone. | `120` |
| `enrich_skip_polish_enabled` | `true` | Long notes skip the rewrite and get only metadata and a summary, because your own wording in a long note is authoritative. `false` always attempts a rewrite. | `true` |
| `enrich_skip_polish_words` | `450` | Word count at which a note counts as long. Keep consistent with `model_max_num_tokens` (about 450 words fit in 6144). | `450` |
| `long_note_threshold_words` | `100` | Notes shorter than this get no summary, since they are their own summary. 0 always writes one. | `100`; 80 to 150 is typical |
| `enrich_long_summary_sentences` | `2` | Most sentences in a long note's summary. | `2` |
| `max_tags` | `3` | Most tags the agent adds to a note. | `3` |
| `add_title_if_missing` | `true` | Give every note a title when it has none. | `true` |
| `reenrich_similarity_threshold` | `0.9` | After you edit an approved note, if the new text is at least this similar (0 to 1) to the old one the agent just re-locks it without calling the model. Lower means more edits trigger a fresh run. | `0.90` |
| `confidence_review_threshold` | `0.6` | A suggestion the model is less sure of than this is labelled low confidence for your review. | `0.6` |
| `priority_queue_enabled` | `true` | When several notes wait, enrich the likely most useful first (dates, tasks) so good output lands early. | `true` |
| `enrichment_timeout_seconds` | `60` | Seconds before a single enrichment call is abandoned. | `60` |

## When the agent reads a note (typing, edits, rescue)

| Setting | Default | What it does | Example |
|---|---|---|---|
| `enrich_quiet_seconds` | `60` | A note still being typed is queued only after this long without an edit; each edit restarts the wait. Closing the editor or pressing Run AI skips the wait. 0 reads at once. | `60`; `20` for a snappier feel |
| `enrich_stranded_seconds` | `1800` | If a session ended without closing the editor (app killed, phone died), the agent enriches the stranded note after this long. | `1800` |
| `force_stuck_seconds` | `120` | A note shown as Processing with nothing working on it is rescued after this long. | `120` |

## Token budgets

| Setting | Default | What it does | Example |
|---|---|---|---|
| `enrich_max_tokens` | `3072` | Most tokens the model may write for one enrichment (metadata plus rewrite). Too low cuts long output mid-way. | `3072` |
| `enrich_input_headroom_polish` | `600` | Context left free for the reply when deciding if a note is too long to rewrite. | `600` |
| `enrich_input_headroom_meta` | `300` | Same, for metadata-only runs. | `300` |
| `ask_max_tokens` | `300` | Longest answer to a question in Ask your notes. | `300` |
| `ask_output_headroom` | `350` | Context kept free for an Ask answer. | `350` |
| `ask_stale_pending_seconds` | `45` | How old a pending question must be before the safety poll picks it up again; long enough that a question being answered right now is never taken twice. | `45` |
| `chat_max_tokens` | `600` | Longest plain reply in Ask → Chat. | `600` |
| `write_section_tokens` | `320` | Output limit for each section in Ask → Write. | `320` |
| `write_sections_max` | `6` | Most sections Write plans. | `6` |
| `write_max_tokens` | `900` | Output limit when Write falls back to a single pass. | `900` |
| `story_tokens_short` | `380` | Length of a short Story from a photo (about 180 words). | `380` |
| `story_tokens_medium` | `800` | Length of a medium Story (about 380 words). | `800` |
| `followups_step_tokens` | `40` | Output limit for the one-line first step in a follow-up. | `40` |

## Related notes and search (embeddings)

| Setting | Default | What it does | Example |
|---|---|---|---|
| `correlation_enabled` | `true` | Link related notes. Off, notes show no related links and Connections stays empty. | `true` |
| `related_top_n` | `3` | Most related notes linked from each note. | `3`; `5` for a denser graph |
| `correlation_corpus_size` | `2000` | How many recent notes are compared. Memory use is small (about 1.5 KB per note), so raise it as your collection grows. | `2000` |
| `correlation_cache_seconds` | `120` | How long a similarity result is reused before recomputing. | `120` |
| `related_threshold` | `0.12` | Keyword-mode link strength (0 to 1). Used only when embeddings are off or not loaded. | `0.12` |
| `cluster_threshold` | `0.12` | Keyword-mode grouping. Lower makes more groups; 0.18 or more is stricter. | `0.12` |
| `embeddings_enabled` | `true` | `false` loads no embedding model at all: saves memory and heat, and search becomes keyword-only. | `true` |
| `embed_model` | `"all-MiniLM-L6-v2"` | Sentence-embedding model, a name or local path. The default is small and separated topics best on real notes. Blank forces keyword mode. | `"all-MiniLM-L6-v2"` |
| `embed_model_fallback` | `""` | Used if `embed_model` cannot load. Vectors from different models are never mixed, so this only means a re-index later. | `""` |
| `embed_backend` | `"auto"` | `auto` tries ONNX first (no PyTorch needed), then sentence-transformers. Force `onnx` or `sentence-transformers`. | `"auto"` |
| `embed_onnx_dir` | `""` | A folder already holding `model.onnx` and `tokenizer.json`, which skips the one-time download. | `"/root/models/minilm"` |
| `embed_cache_dir` | `""` | Optional folder for downloaded embedding models. | `""` |
| `embed_related_threshold` | `0.45` | How similar two notes must be to be linked (embedding scale). | `0.45` |
| `embed_cluster_threshold` | `0.55` | How similar to be grouped into one cluster. | `0.55` |
| `embed_confirm_band` | `0.1` | A borderline pair just above the threshold is linked only if the notes share a tag; above the band a strong score links alone. 0 turns the guard off. | `0.10` |
| `embed_tag_assist` | `0.2` | The opposite help: a pair sharing a tag may link at a lower score (threshold minus this). | `0.20` |
| `embed_floor_search` | `null` | Override the model's built-in minimum score for search. `null` uses its profile. | `null` |
| `embed_floor_ask` | `null` | Same, for Ask retrieval. | `null` |
| `embed_margin` | `null` | Override the score gap a top result needs over the rest. `null` uses the profile. | `null` |
| `embed_z_min` | `null` | Override the statistical cut-off for a result to count. `null` uses the profile. | `null` |
| `embed_chunk_chars` | `500` | Passage size for the search index. | `500` |
| `embed_chunk_overlap` | `80` | Overlap between passages so a sentence is not split away from its context. | `80` |
| `embed_max_chunks` | `40` | Most passages indexed per note. | `40` |
| `reminder_dupe_similarity` | `0.68` | Two reminders on the same date for one note are treated as the same when their titles are at least this similar, so a reworded reminder does not pile up. | `0.68` |
| `reindex_boot_seconds` | `30` | Seconds after start before the first catch-up indexing pass. | `30` |
| `reindex_boot_limit` | `50` | Most notes indexed in that first pass. | `50` |

## Photos, voice notes, sketches and videos

| Setting | Default | What it does | Example |
|---|---|---|---|
| `media_enabled` | `true` | Read photos, voice notes and sketches. Off, attachments are stored but not transcribed or described. | `true` |
| `media_backend` | `"auto"` | `auto` picks the best available: Medha, then tesseract/whisper commands, then the built-in model. `command` uses your commands, `litert` the built-in model, `off` nothing. | `"auto"` |
| `media_audio_lang` | `"auto"` | Language of voice notes: `te`, `en`, or `auto`. With Medha, set `te` if you speak Telugu, because auto sometimes answers in Latin script. | `"te"` |
| `media_audio_prompt` | `""` | Spelling hints for the speech-to-text (names and terms it should get right). | `"Adabala, Quicksilver, Medha"` |
| `media_audio_chunk_s` | `28` | Long recordings are cut into pieces this long (Medha accepts 30 s). | `28` |
| `media_image_max_px` | `1024` | Photos are shrunk so the longest side is this many pixels before the model sees them. Smaller is faster and uses fewer vision tokens. | `1024` |
| `media_describe` | `true` | Also write a short what-is-this description for photos and sketches. Needs a vision-capable model. | `true` |
| `media_video_frames` | `3` | For a video note: how many still frames, evenly spaced, the model describes. Each frame is one vision call, so more is slower and warmer. 0 reads the sound only. | `3` |
| `media_video_max_s` | `300` | Only the first this-many seconds of a video are read (the app already limits clips to 2 minutes). | `300` |
| `media_ocr_langs` | `"eng"` | Languages for text in pictures (tesseract codes joined with +). | `"eng+tel"` |
| `media_audio_cmd` | `""` | Your own speech-to-text command. Placeholders `{file}`, `{wav}` (16 kHz mono), `{lang}`. Blank auto-detects. | `"whisper-cli -m /m/base.bin -f {wav} -l {lang} -nt"` |
| `media_image_cmd` | `""` | Your own OCR command. Placeholders `{file}`, `{ocr_langs}`. Blank auto-detects tesseract. | `"tesseract {file} - -l {ocr_langs}"` |
| `media_whisper_model` | `""` | Path of a whisper.cpp model, which switches on local audio transcription. | `"/root/models/ggml-base.bin"` |
| `media_litert_multimodal` | `false` | Load the built-in model's vision and audio parts. Uses much more memory. | `false` |
| `media_audio_compare` | `true` | While judging Medha's Telugu, also run whisper on each clip and log both to a local file. Turn off to save battery. | `true` |
| `media_compare_file` | `""` | Where those comparisons go. Blank means `media_compare.jsonl` in the state folder. | `""` |
| `media_job_timeout_s` | `600` | Seconds before one media job is abandoned. | `600` |
| `media_scan_batch` | `20` | Most attachments queued per catch-up scan. | `20` |

## Bills from messages

| Setting | Default | What it does | Example |
|---|---|---|---|
| `sms_enabled` | `true` | Makes bill reading available. It still stays off until you switch it on in Settings → Bills. | `true` |
| `sms_command` | `"termux-sms-list"` | Termux:API command that lists messages. Works only where Termux binaries run. | `"termux-sms-list"` |
| `sms_file` | `""` | Inside Fedora/proot Termux commands do not run, so point this at the JSON file that `bin/sms-bridge.sh` keeps fresh in native Termux. Blank uses `sms_command`. | `"/sdcard/Download/sms.json"` |
| `sms_poll_seconds` | `300` | How often new messages are read. | `300` |
| `sms_days_back` | `60` | The first read looks this many days back. | `60` |
| `sms_page_size` | `100` | Messages per page read. | `100` |
| `sms_first_pages` | `8` | Pages read on the first scan. | `8` |
| `sms_pages` | `3` | Pages read on later scans. | `3` |

## Follow-ups, digest and duplicates

| Setting | Default | What it does | Example |
|---|---|---|---|
| `followups_enabled` | `true` | Once a day, raise stale tasks with one suggested move each (do it, move it, drop it). Also needs the Follow-ups setting on in the app. | `true` |
| `followups_max` | `3` | Decisions shown at once. | `3` |
| `followups_min_age_days` | `7` | A task must be at least this old before it is raised. | `7`; `3` to nag sooner |
| `digest_enabled` | `true` | Write the weekly Your week summary. | `true` |
| `digest_weekday` | `6` | Day it is written: 0 Monday to 6 Sunday. | `6` |
| `digest_hour` | `18` | Hour (local, 0 to 23). | `18` |
| `duplicates_enabled` | `true` | Suggest merging near-identical notes (you always decide). | `true` |
| `dup_min_score` | `0.85` | How alike two notes must be to be suggested. Higher is stricter. | `0.85` |

## Google Calendar

| Setting | Default | What it does | Example |
|---|---|---|---|
| `calendar_enabled` | `false` | Create Google Calendar events for dated tasks in approved notes. Turn on only after the OAuth token exists (see CALENDAR_SETUP.md). | `true` |
| `calendar_timezone` | `"Asia/Kolkata"` | IANA zone for events. | `"Asia/Kolkata"` |
| `calendar_default_duration_minutes` | `30` | Event length when no end time is found. | `30` |
| `calendar_lookahead_days` | `30` | Only events within this many days are created. 0 means no limit. | `30` |
| `calendar_add_description` | `true` | Put the note's title and summary in the event. | `true` |
| `calendar_id` | `"primary"` | Which calendar. `primary` is your main one. | `"primary"` |
| `calendar_client_secret_file` | `""` | OAuth client JSON. Blank means `gcal_client_secret.json` next to the database. | `""` |
| `calendar_credentials_file` | `""` | Token the auth step writes. Blank means `gcal_token.json` next to the database. | `""` |

## Heat, battery and timers

| Setting | Default | What it does | Example |
|---|---|---|---|
| `thermal_enabled` | `true` | Pause heavy work when the phone is hot. | `true` |
| `thermal_hot_celsius` | `70.0` | Pause at or above this temperature. | `70.0`; `65.0` in a warm room |
| `thermal_resume_celsius` | `60.0` | Resume at or below this temperature. | `60.0` |
| `thermal_zone_glob` | `"/sys/class/thermal/thermal_zone*/temp"` | Where the sensors are read from. | `"/sys/class/thermal/thermal_zone*/temp"` |
| `thermal_poll_seconds` | `10` | How often temperature is checked while paused. | `10` |
| `thermal_max_wait_seconds` | `600` | Longest a job waits for cooling before carrying on. | `600` |
| `idle_backoff_max_seconds` | `300` | When nothing happens the polling slows down to at most this interval. | `300` |
| `main_loop_tick_seconds` | `5` | How often the background loop wakes to check its timers. Higher saves battery; note reactions are instant either way. | `25` |
| `heartbeat_interval_seconds` | `30` | How often the agent-is-live dot is refreshed. 0 turns the heartbeat off. | `30` |

## Reliability: queue and retries

| Setting | Default | What it does | Example |
|---|---|---|---|
| `durable_queue` | `true` | Keep work in a database queue that survives restarts, with retries and a dead-letter state you can retry from the app. | `true` |
| `job_lease_seconds` | `1200` | How long a worker owns a job before it is considered lost and retried. | `1200` |
| `job_backoff_seconds` | `[30, 120, 600]` | Wait before each retry, in order. After the last one the job is parked as failed. | `[30, 120, 600]` |
| `write_max_retries` | `4` | Retries for a database write that hit a transient error. | `4` |
| `write_backoff_base` | `0.5` | First wait, seconds. Doubles each time. | `0.5` |
| `write_backoff_cap` | `8.0` | Longest single wait. | `8.0` |

## Live link and polling

| Setting | Default | What it does | Example |
|---|---|---|---|
| `webhook_enabled` | `true` | Run a small local server that receives events. Quicksilver normally uses its live event stream instead, so the sample config sets this to `false`. | `false` |
| `webhook_host` | `"0.0.0.0"` | Address the webhook server listens on. | `"0.0.0.0"` |
| `webhook_port` | `19283` | Webhook port. | `19283` |
| `webhook_path` | `"/memos-hook"` | Webhook URL path. | `"/memos-hook"` |
| `webhook_advertise_url` | `""` | The address senders should call, when not derivable from host and port. | `"http://192.168.1.50:19283/memos-hook"` |
| `webhook_secret` | `""` | If set, requests must carry `?token=<secret>`. | `"change-me"` |
| `safety_poll_seconds` | `600` | A slow reconciling check that catches anything an event missed. | `600`; `30` while testing |
| `poll_interval_seconds` | `30` | Polling interval when the live link is off. | `30` |
| `page_size` | `50` | Notes fetched per page when scanning. | `50` |
| `max_retries` | `3` | Retries for a failed call to the app. | `3` |

## Maintained notes and labels (legacy dashboards)

| Setting | Default | What it does | Example |
|---|---|---|---|
| `dashboard_enabled` | `true` | Legacy: keep an agent-dashboard note. The app's own dashboard replaces it. | `true` |
| `dashboard_title` | `"🤖 AI Agent Dashboard"` | Title of that note. | `"🤖 AI Agent Dashboard"` |
| `dashboard_tag` | `"agent-dashboard"` | Tag on that note. | `"agent-dashboard"` |
| `connections_enabled` | `true` | Legacy: keep a relationships note. | `true` |
| `connections_title` | `"🔗 Note Relationships"` | Title of that note. | `"🔗 Note Relationships"` |
| `connections_tag` | `"note-relationships"` | Tag on that note. | `"note-relationships"` |
| `todo_enabled` | `true` | Legacy: keep an Action Items note. | `true` |
| `todo_title` | `"📋 Action Items"` | Title of that note. | `"📋 Action Items"` |
| `todo_tag` | `"todo-dashboard"` | Tag on that note. | `"todo-dashboard"` |
| `todo_file` | `""` | State file for it. Blank means `<state_file>.todos.json`. | `""` |
| `pin_maintained_notes` | `false` | Pin those maintained notes. Tags find them anyway. | `false` |
| `enriched_label` | `"Enriched Note"` | Heading shown above an enriched body under review. | `"Enriched Note"` |
| `note_label` | `"Note"` | Heading above your editable body. | `"Note"` |
| `review_tag` | `"pending-approval"` | Tag put on every suggestion and removed on approval. | `"pending-approval"` |

## Legacy Memos backend (unused by Quicksilver)

| Setting | Default | What it does | Example |
|---|---|---|---|
| `memos_base_url` | `"http://localhost:5230"` | Legacy Memos server address. Ignored by Quicksilver. | `"http://localhost:5230"` |
| `memos_token` | `""` | Legacy Memos token. | `""` |
| `note_url_template` | `"{base}/memos/{uid}"` | Legacy: link template, `{base}` and `{uid}` are filled in. | `"{base}/memos/{uid}"` |
