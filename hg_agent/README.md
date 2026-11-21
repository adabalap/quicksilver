# hg_agent: the on-device brain of పాదరసం Quicksilver

Version **1.6.7**. A small Python daemon that reads your notes in the Quicksilver (hg_ui) database and quietly adds what
makes them useful: a title, tags, a summary, links to related notes, the tasks and dates buried in the text, a cleaned-up
rewrite of short prose, and text pulled out of photos and voice notes. It answers questions about your notes, writes longer
pieces from them, reads bills from your messages, and keeps Medha's Now brief and notifications in step with what needs you.

Everything runs on the phone. No note, message or photo is sent to any cloud service.

## The picture

```
 Chrome PWA  ──►  hg_ui (Flask + SQLite, port 5230)  ◄──►  hg_agent (this daemon)
 your phone        notes.db, attachments/                     │  reads and writes notes.db directly
 hg.adabala.com    live events (SSE)  ─────────────────────►  │  hears about changes at once
 (Cloudflare Tunnel)                                          ▼
                                            Medha  (http://127.0.0.1:8001)   ← first choice
                                            or the built-in LiteRT Gemma model  ← automatic fallback
                                                              │
                                                              ▼
                                      Medha Now brief + silent notifications (links back into hg_ui)
```

- **hg_ui** owns the database and the screens. **hg_agent** owns the thinking.
- They never call each other's code. They share `notes.db`, and the app streams events (note saved, bill paid, task ticked)
  that wake the agent immediately; a slow safety poll catches anything missed.
- **Medha** is the on-device model service. With `engine: auto` the agent uses Medha when it answers and drops to the
  built-in model by itself when it does not, then looks for Medha again.

## Quick start (Termux, Fedora proot guest)

```bash
pip install setproctitle onnxruntime tokenizers      # optional extras; see "Embeddings without PyTorch"
./bin/hg_agent start          # first run copies config.quicksilver.json to ~/.quicksilver/ so redeploys never overwrite it
./bin/hg_agent status | logs | stop | restart
```

Edit **`~/.quicksilver/config.quicksilver.json`**: at least `db_path`, and `medha_url` + `medha_token` (or `model_path` for the
built-in model). The first log lines announce what was picked up. You want to see:

```
Backend: Quicksilver (SQLite) · db=/root/.quicksilver/notes.db
```

If it says `Backend: Memos (HTTP)` your config file was not found: check `--config` or `HG_AGENT_CONFIG`.
Manual run: `python3 app.py --config ~/.quicksilver/config.quicksilver.json run`.

Every setting, with default, behaviour and an example, is in **[docs/CONFIGURATION.md](docs/CONFIGURATION.md)**.
**Restart the agent after changing the config file.**

### A good day-one config

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

`medha_brief_link` is the address your phone's browser can open (never the embedding sidecar on 5232). Use
`https://hg.adabala.com` if you want the links to work through the tunnel too.

## What happens to a note

```
you type ─► autosave marks it "being edited"
close the editor / Run AI ─► queued at once           still typing? wait enrich_quiet_seconds (60), each edit restarts it
queued ─► model runs ─► text changed meanwhile? result is thrown away and the note runs again when it settles
        ─► title · summary · tags · tasks · reminders · related notes · optional polished rewrite written to the DB
        ─► status "pending" (you review: ✓ Approve) ─► "approved"
edit an approved note ─► small change (≥ 90% similar): just re-locked, no model call; bigger change: fresh run
```

- Your note body is **never overwritten** with AI text. The agent writes into separate tables; you choose what to adopt.
- A title you set yourself (tap the title in the app) always wins and is never replaced.
- Short captures (under 120 characters) and lists are kept exactly as written. Long notes (450+ words) get metadata and a
  summary but no rewrite, because your own wording is authoritative. Polish on demand overrides both rules.
- Hot phone? Heavy work pauses at 70 °C and resumes at 60 °C. Failed jobs retry after 30 s, 2 min and 10 min, then park with a
  reason and a Retry button in the app. A restart loses nothing.

## What the agent does, by feature

### Ask your notes, Chat and Write
Questions are answered from retrieved passages (about 500-character chunks, vector plus keyword search fused by rank) and cite
the exact notes. **Chat** is a plain prompt to the model. **Write** plans sections and writes each one from your notes.
Token limits: `ask_max_tokens`, `chat_max_tokens`, `write_section_tokens`.

### Related notes and search
Embeddings (all-MiniLM-L6-v2 by default, ONNX, no PyTorch needed) link related notes and group clusters. A borderline pair
links only if the notes share a tag; a pair that shares a tag may link at a slightly lower score. Without embeddings
(`embeddings_enabled: false`) everything falls back to keywords and nothing heavy is loaded.

### Video notes
A video is read in two halves. **Sound:** ffmpeg extracts the audio track (first `media_video_max_s` seconds, 16 kHz mono) and it goes through the same voice path as a voice note (Medha, else whisper.cpp). **Picture:** `media_video_frames` evenly spaced stills (fewer for short clips: one per roughly 8 seconds, at least one) are each described by the vision model, with any visible text. The result is one text, `Spoken: …` then `On screen: - 0:05 …`, saved as the video's transcript so search, Ask and enrichment use it. If one half is impossible (no speech engine, no vision model, a silent clip) the other is used; with neither the video is skipped. Needs `ffmpeg` (`pkg install ffmpeg`). `media_video_frames: 0` reads the sound only. Each frame is one vision call, so a long clip on a warm phone waits for it to cool like any other job.

### Photos, voice notes and sketches
Medha transcribes voice (16 kHz mono, 28 s pieces) and describes photos while reading the text in them; whisper.cpp and
tesseract are the fallback when Medha cannot. Set `media_audio_lang` to `te` for Telugu. Details are below.

### Bills from messages (optional, off until you switch it on)
Every 5 minutes the agent reads new SMS (`termux-sms-list`, or a JSON file from `bin/sms-bridge.sh` inside proot), keeps only
transactional bill messages, and finds the amount and due date with plain rules. Each bill gets two reminders (two days
before, and on the day, 09:00). It closes itself when the payment message arrives and is dropped two weeks after its due date.
While a bill is open its message (up to 600 characters, any run of 9+ digits shown as ••••1234) is kept in the local database
so you can see it and tap the payment link; paying, "Not a bill" or Forget removes it. In the app you can also add a bill by
hand; it is treated the same and appears in the brief even when message reading is off.

### Medha Now brief and notifications
The agent keeps its section of Medha's Now brief in step with what needs you, in order of urgency:

1. **Bills** overdue up to two weeks, or due this month / within a week.
2. **Dated reminders** from your notes: `medha_brief_reminder_back_days` (7) days late to `..._ahead_days` (14) ahead, plus up
   to two with no date. Dismissing a reminder in the app is "done".
3. **Stalled tasks** the daily follow-up pass raised.
4. **Open action items**, longest-waiting first, at most `medha_brief_actions_max` (25), always below dated items.

Each row sends `dueAt` and `kind`, so Medha computes "10d over" or "in 3d" itself and the text never goes stale. A row says
something useful (whether a pay link is in the message, which note a reminder came from) and, for the top three, the model
writes one line from the note when the phone is cool. For anything due today or late, one silent notification per item
(three at most) opens the exact note or bill (`<medha_brief_link>/?note=<id>` or `/?bill=<id>`). When the item is done,
paid, dismissed or deleted, the notification is cancelled and the row leaves the brief. The section is deleted when nothing
is left, and re-posted every 6 h so it keeps its place.

Setup: a Medha API token whose client has the **`notify`** capability, `medha_brief_link`, and the Android notification
permission for Medha. A 403 in the log means the capability is missing; a 401 means Medha does not know the token. Either
backs the brief off for 15 to 60 minutes instead of retrying every minute. Test: `python brief_test.py`.

### Task follow-through, your week, duplicates
Once a day tasks older than 7 days are raised, at most 3 at a time, each with one suggested move (plan it, do it today, start
with a first step, drop it). Sunday 18:00 the agent writes "Your week". Near-identical notes are offered for merging; you
always decide.

### Google Calendar (optional)
Dated tasks in approved notes can become calendar events within the next 30 days. See `docs/CALENDAR_SETUP.md`.

## Medha: photos, voice, Telugu

`medha_multimodal: auto` reads `capabilities.vision` / `.audio` from Medha's `/health` before every job and follows it per
kind. Voice goes to `POST /v1/audio/transcriptions` as WAV in pieces of at most 28 s (anything not 16 kHz mono is re-encoded
with ffmpeg); if audio is unavailable or Medha returns nothing for a clip, whisper.cpp does it (keep `media_whisper_model` or
`media_audio_cmd` set). One vision call returns a description and the text it reads (JPEG/PNG only; photos are shrunk to
`media_image_max_px`, at most 4 images per request). Every call carries `X-Medha-Priority` (`interactive` for Ask/Chat/Write/
Story, `batch` for enrichment, media and digests), and a 429 is waited out for up to `medha_busy_wait_s`.

With `media_audio_compare` on, each clip is also run through whisper and both transcripts are appended to
`media_compare.jsonl` (kept on the phone). `hg_agent media_compare` summarises speed, script, and agreement. Turn it off when
you are satisfied: it doubles the audio work.

Check a Medha build with one command (checks without sample files are skipped):

```bash
hg_agent medha_check --image car.jpg --sketch plan.jpg --telugu-image board.jpg \
    --audio-en hello.m4a --audio-te namaste.m4a --expect-te "నమస్తే ..."
```

## Bills inside Fedora/proot

Termux's own commands do not exist inside proot. Run `bin/sms-bridge.sh` in native Termux: it keeps `~/quicksilver-sms.json`
fresh. Point `sms_file` at that file as the agent sees it. For cron instead of a background loop:
`pkg install cronie termux-services`, `sv-enable crond`, then `crontab -e` and add
`*/5 * * * * bash ~/sms-bridge.sh ~/quicksilver-sms.json 0`.
The app shows "The SMS bridge isn't running" or "last wrote N minutes ago" when it stalls.

## Retrieval, evaluation, embeddings

**Switching the embedding model.** Set `embed_model` (for example `intfloat/multilingual-e5-small` for Telugu) and restart.
The agent re-indexes in the background (time-boxed, thermal-aware); until a note is re-indexed it is found by keyword only.
Scores are not comparable between models, so each has a gating profile (`embeddings.PROFILES`, override with `embed_floor_*`,
`embed_margin`, `embed_z_min`). **Do not switch on a hunch; run the eval first:**

```bash
python -m memos_daemon.evalkit --out base.json
python -m memos_daemon.evalkit --model intfloat/multilingual-e5-small --compare base.json
python -m memos_daemon.evalkit --compare-models all-MiniLM-L6-v2,intfloat/multilingual-e5-small
python -m memos_daemon.evalkit --sweep          # tune the gate
python -m memos_daemon.evalkit --llm gemma --out full.json
```

It seeds a throwaway database from `evalkit/golden.json` (English, Telugu, cross-lingual, buried answers, near-duplicates,
hidden-note and abstain traps), runs the app's real retrieval and reports recall@k, MRR, abstention and leaks.
`--compare` exits non-zero on a regression. `hg_agent relations` prints the real similarity scores for tuning thresholds.

**Embeddings without PyTorch.** `embed_backend: auto` uses ONNX Runtime when `onnxruntime` and `tokenizers` are installed
(`pip install onnxruntime tokenizers`); the first start downloads about 90 MB once into `embed_cache_dir`. Set
`embed_onnx_dir` if you already have `model.onnx` and `tokenizer.json`. `"embeddings_enabled": false` loads nothing at all.

## Reliability notes

- **Durable queue.** Work lives in the `agent_jobs` table; the in-memory queue is rebuilt from it. Kill or restart at any time.
- **Parked writes.** A finished enrichment that cannot be written (database busy) is saved and re-applied later.
- **Typing guard.** A note being typed is not processed halfway, and a result for text that has since changed is discarded.
- **Rescue.** A note stuck on Processing with no worker is rescued after 120 s; a session that ended without closing the editor
  is enriched after 30 minutes.
- **Brief housekeeping.** Retries only on 429/503/transport errors, never on a permanent refusal.

## Layout

```
app.py                      entry point
memos_daemon/daemon.py      the daemon (kept under 800 lines; everything else is a mixin in parts/)
memos_daemon/parts/         sources (SSE, settle window), sms_source, brief_source, followups, recovery, ...
memos_daemon/engine.py      prompts, JSON parsing, enrichment; medha_engine.py and the LiteRT engine
memos_daemon/medha_brief.py Now-brief client, candidates, notifications, housekeeping
memos_daemon/sms.py         bill parsing (pure functions)
tools/gen_config_doc.py     renders docs/CONFIGURATION.md and checks it is complete
docs/                       CONFIGURATION, ARCHITECTURE, OPERATIONS, CALENDAR_SETUP, MODULE_MAP, DESIGN_RATIONALE
```

## Tests

```bash
python smoke_test.py               # structure, imports, public surface
python brief_test.py               # Medha brief, notifications, housekeeping (stub Medha)
python settle_test.py              # typing guard and stale results
python sms_test.py                 # bill parsing and storage (made-up messages only)
python captures_test.py            # Medha's notification stream: the cursor walk, the store, corroboration
python saved_test.py               # Saves from Medha's brief: never lost, never duplicated, never rewritten
python ticks_test.py               # the main-loop tick and scheduled jobs (follow-ups run after 06:00 local)
python medha_integration_test.py   python medha_media_test.py   python followups_test.py
python onnx_embed_test.py          python embed_off_test.py     python index_skip_test.py
python tools/gen_config_doc.py --check      # every setting is documented and the reference is current
```

## Version history

| Version | Highlights |
|---|---|
| **1.6.2** | The dashboard no longer says "Medha" when Medha is down (health is re-probed every minute); a bill row in Medha's Now brief always opens the bill in Quicksilver, not the payment link |
| **1.6.3** | Medha health is reported whichever engine mode is set (`medha` or `auto`) |
| **1.6.4** | Medha's Now brief holds a slot for the best dated reminder and the longest-waiting task, so a run of bills no longer pushes them out |
| **1.6.5** | The Now brief lists up to 12 rows (`medha_brief_max_items`), up to 5 actions and 5 undated reminders, each opening its own note; "+N more" appears only beyond that |
| **1.6.6** | Every item in the Now brief is its own row that opens its note (up to 50, 25 actions, 20 undated reminders); if Medha trims a long list the agent posts what it accepts plus one "+N more" row |
| **1.6.7** | The built-in model is unloaded after 5 idle minutes (`engine_idle_unload_s`), freeing about 3 GB so the camera app is not starved |
| **1.6.1** | Video jobs wait for a cool phone before the sound and before each frame, log their progress, run after photos, voice and notes, and default to 3 frames; `hg_agent brief-check` explains what is (and isn't) in Medha's Now brief and notifications |
| 1.6.0 | Video notes (sound transcribed, frames described); task extraction fixed so a cut-off model reply no longer turns `category`, `work` or `blocked_by` into tasks, and categories are saved; new `media_video_frames` and `media_video_max_s` |
| 1.5.0 | Medha brief covers reminders (7 days back to 14 ahead), undated reminders and open actions; hand-added bills; reference docs rebuilt |
| 1.4.10 | Brief lines say useful things (`ICICI card · ₹2,50,000`); bill message text kept while open, long digit runs masked |
| 1.4.9 | Bill rows and notifications open the bill (`/?bill=`) instead of a missing reminder note |
| 1.4.8 | Typing guard: quiet window, stale-result drop; commit skips the wait |
| 1.4.6–1.4.7 | Medha Now brief with `dueAt`/`kind`, silent notifications with links, housekeeping; model-written lines |
| 1.4.3 | Voice and photos through Medha with whisper/tesseract fallback; comparison log |
| 1.4.2 | `medha_multimodal: auto`, priority header, busy handling |
| 1.4.0 | Bills from messages |
