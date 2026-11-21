import os, json
from pathlib import Path
from .logging_setup import log

DEFAULT_CONFIG = {
    "model_path": "",
    # Which engine runs the model: "litert" (load model_path in this process) or
    # "medha" (send text to the Medha inference service — see medha_engine.py).
    "engine": "auto",
    "medha_error_backoff_s": 600, # auto: after a Medha server error, use the built-in model this long
    "engine_idle_unload_s": 300, # auto: unload the built-in model after this many idle seconds (0 = keep it loaded)
    "engine_recheck_s": 30,      # auto: how often to look for Medha again while using the built-in model
    "medha_url": "http://127.0.0.1:8080",
    "medha_token": "",
    "medha_timeout_s": 300,
    "medha_multimodal": "auto",  # "auto" follows Medha's /health capabilities (vision/audio); true/false force it
    "medha_brief": True,            # post bills that need attention to Medha's brief widget (needs medha_token with 'notify')
    "medha_brief_bills": True,      # sources: bills (also needs Bills on in Settings), dated reminders, stalled tasks
    "medha_brief_reminders": True,
    "medha_brief_followups": True,
    "medha_brief_actions": True,
    "medha_brief_actions_max": 25,
    "medha_brief_max_items": 50,    # rows on the brief; only a list longer than this ends in a "+N more" row
    "medha_brief_reminder_back_days": 7,
    "medha_brief_reminder_ahead_days": 14,
    "medha_brief_notify": True,     # silent notification with the note's link for what is due today or late; cancelled when done
    "medha_brief_notify_max": 3,
    "medha_brief_notify_days": 0,   # 0 = due today or late; 1 also tomorrow; and so on
    "medha_brief_ai": True,         # the model writes a useful line per item from its note, in the background, only when cool
    "medha_brief_ai_top": 3,
    "medha_brief_ai_tokens": 48,
    "medha_brief_amounts": True,    # show the amount on the lock-screen/home line
    "medha_brief_pay_links": False, # deprecated, no effect: a bill row's tap always opens the bill in Quicksilver
    "medha_brief_link": "",         # your Quicksilver address, e.g. https://hg.adabala.com : lines and notifications open the note there
    "medha_brief_refresh_hours": 6, # re-post an unchanged brief this often so it keeps its place
    "medha_brief_ttl_hours": 24,
    "medha_busy_wait_s": 120,    # how long to wait out a 429 "busy" (Retry-After) before giving up on a call
    "digest_enabled": True,          # weekly "Your week" summary
    "digest_weekday": 6,             # 0=Mon … 6=Sun
    "digest_hour": 18,               # local time
    "duplicates_enabled": True,      # suggest merging near-identical notes
    "dup_min_score": 0.85,
    "story_tokens_short": 380,        # Story from a photo: ~180 words
    "story_tokens_medium": 800,       # ~380 words
    # Medha's notification stream (Medha >= v0.20.0, GET /captures). Available
    # here but OFF until switched on in Settings, and refused by Medha until
    # this token's client is given "Read your notifications" -- the one
    # capability an app cannot request through a consent dialog.
    #
    # Every date derived from a capture is INFERRED: we are reading prose a
    # third-party app wrote, which is exactly the signal that put four emails
    # on the agenda in v0.19.1. Medha will not promote those to the widget.
    "captures_enabled": True,
    "captures_poll_seconds": 300,     # how often the stream is read
    "captures_max_pages": 20,         # a bound on one drain; the backlog is drained over several polls
    "captures_corroborate_hours": 72, # how far back a cancellation notice is believed about something still on the list
    "saved_poll_seconds": 60,         # how often things pressed Save on in Medha are collected
    "sms_enabled": True,              # the SMS source is available (it still stays off until switched on in Settings)
    "sms_command": "termux-sms-list", # path to the Termux:API command (works only where Termux binaries run)
    "sms_file": "",                   # OR a JSON file kept fresh by bin/sms-bridge.sh in native Termux (use this inside Fedora/proot)
    "sms_poll_seconds": 300,          # how often new messages are read
    "sms_days_back": 60,              # first scan looks this far back
    "sms_page_size": 100,
    "sms_first_pages": 8,             # first scan: up to this many pages
    "sms_pages": 3,                   # later scans
    "followups_enabled": True,        # daily task follow-through (stale tasks → one proposed move each)
    "followups_max": 3,               # decisions shown at once
    "followups_min_age_days": 7,      # a task must be this old (note age) before it is raised
    "followups_step_tokens": 40,      # output cap for the one-line first step
    "chat_max_tokens": 600,          # Ask → Chat: longest plain reply
    "write_section_tokens": 320,     # Ask → Write: per section
    "write_sections_max": 6,
    "write_max_tokens": 900,         # single-pass fallback when planning fails
    "media_image_max_px": 1024,  # photos are shrunk to this before being sent to a model (fewer vision tokens)
    "media_audio_chunk_s": 28,   # long recordings are cut into pieces of this length
    # Timezone for human-readable timestamps shown in notes/dashboards.
    # IANA name (e.g. "Asia/Kolkata") preferred; "UTC" for UTC. The short label
    # (IST, UTC, …) is derived automatically.
    "display_timezone": "Asia/Kolkata",
    # ── Backend selection ─────────────────────────────────────────────────────
    # "quicksilver" → direct SQLite read/write via QuicksilverClient (default).
    # "memos"       → legacy Memos HTTP API via MemosClient.
    "backend": "quicksilver",

    # Quicksilver settings (used when backend="quicksilver")
    "db_path": "",                          # e.g. "/root/.quicksilver/notes.db"
    "quicksilver_url": "http://127.0.0.1:5230",   # PWA Flask app URL for webhooks

    # Legacy Memos settings (used when backend="memos")
    "memos_base_url": "http://localhost:5230",
    # URL template for linking to a single note. {base} and {uid} are filled in.
    # This instance serves notes at /memos/<uid>; some deployments use /m/<uid>.
    "note_url_template": "{base}/memos/{uid}",
    "memos_token": "",
    "poll_interval_seconds": 30,
    "max_retries": 3,
    "page_size": 50,
    "state_file": str(Path.home() / ".cache/memos_daemon/state.json"),
    "log_file": str(Path.home() / ".cache/memos_daemon/daemon.log"),
    "dry_run": False,
    "max_tags": 3,
    "add_title_if_missing": True,
    "enrichment_timeout_seconds": 60,

    # Proportional re-enrichment: when an APPROVED note is edited, compare the
    # new user text to the preserved raw baseline. If they are at least this
    # similar (0..1), treat the edit as negligible and just re-lock without
    # re-running the model. Below this, do a full fresh enrichment.
    "reenrich_similarity_threshold": 0.90,

    # Dashboards
    "dashboard_enabled": True,
    "dashboard_title": "🤖 AI Agent Dashboard",
    "dashboard_tag": "agent-dashboard",
    "connections_enabled": True,
    "connections_title": "🔗 Note Relationships",
    "connections_tag": "note-relationships",
    # TODO dashboard — cross-note action items
    # When enabled, a "📋 Action Items" note is maintained aggregating all
    # pending action items across approved notes, grouped by tag/category.
    # Checking an item on the dashboard checks it in the source note too.
    # Completed items are archived (collapsible) rather than deleted.
    "todo_enabled": True,
    "todo_title": "📋 Action Items",
    "todo_tag": "todo-dashboard",
    "todo_file": "",          # blank → <state_file>.todos.json

    # Long note threshold: a note with at least this many words is considered
    # "long enough" to benefit from an AI-generated summary. Notes below this
    # threshold already are their own summary — the AI adding a paraphrase is
    # pure noise. Set to 0 to always generate a summary regardless of length.
    # Recommended range: 80–150 words. Default: 100.
    "long_note_threshold_words": 100,
    # Max output tokens for the model. The litert_lm default truncates long-note
    # enrichments mid-JSON; raise it so the response completes. With a 32K
    # context we can give the response real room — a long note's full polished
    # rewrite plus metadata fits comfortably now instead of being capped.
    "enrich_max_tokens": 3072,
    # Input-fit headroom: how much context to leave FREE for the response when
    # deciding whether a note's INPUT is too big. This is headroom, not a
    # pre-reservation of max output — the model streams into whatever's left.
    # Kept modest so ordinary notes are never trimmed; only a note that genuinely
    # crowds model_max_num_tokens gets shortened. (An earlier version subtracted
    # the full 2048 output up front, which — with a ~2400-token system prompt —
    # trimmed even tiny notes to a floor. These replace that.)
    "enrich_input_headroom_polish": 600,
    "enrich_input_headroom_meta": 300,
    # Ask your notes: a synthesized answer is a few sentences, not a polished
    # rewrite — so its output cap and headroom are both much smaller than
    # enrichment's. ask_stale_pending_seconds is how old a 'pending' row must
    # be before the safety poll will re-enqueue it — long enough that a
    # request actively mid-inference (still 'pending' in the DB until
    # write_ask_answer commits) is never mistaken for a dropped SSE event.
    "ask_max_tokens": 300,
    "ask_output_headroom": 350,
    "ask_stale_pending_seconds": 45,
    # Total context budget (input + output), set at model LOAD. This is a speed
    # knob, not just a capacity one: on litert_lm a larger context makes EVERY
    # operation slower (bigger KV cache, more memory bandwidth per token) — even
    # for a short note. Loading at the full 32K made small notes drag.
    #
    # So we load at a moderate size that comfortably fits the vast majority of
    # notes (a ~2.5k-token system prompt + a long note + its enrichment), which
    # keeps short notes fast. The model CARD supports 32K, but we don't pay the
    # 32K cost on every note to get it. Genuinely huge notes are trimmed to fit
    # by _fit_input (relative to this value) rather than slowing everything down.
    # Raise this only if you routinely enrich very long documents AND accept the
    # per-note slowdown that comes with it.
    "model_max_num_tokens": 6144,
    # Opt-in: generate via the low-level Session (run_prefill/run_decode) which
    # enforces max_output_tokens precisely. Off by default — the proven
    # create_conversation path + raised model_max_num_tokens covers most cases.
    "use_session_generation": False,
    # ── Long-note handling ────────────────────────────────────────────────
    # ── What enrichment does by DEFAULT ─────────────────────────────────────
    # Full enrichment by default: the agent writes metadata (title, summary,
    # tags, connections) AND a polished rewrite of the body. Two ways a note
    # still avoids the rewrite:
    #   • the length guard below (enrich_skip_polish_*) — a long note's own
    #     wording is authoritative, so big notes get metadata + summary only;
    #   • a per-note choice — the compose control's "Metadata only" mode sets
    #     notes.meta_only, and protected / user-edited notes are treated the same
    #     way (see daemon._process_one).
    # Set false to make metadata-only the global default instead.
    "enrich_polish_by_default": True,


    #
    #   enrich_skip_polish_enabled   true/false — master switch. Set false to
    #                                ALWAYS attempt a full polished rewrite,
    #                                regardless of note length.
    #   enrich_skip_polish_words     word count at/above which a note counts as
    #                                "long" and skips the rewrite. Raise it to
    #                                only skip for very large notes; lower it to
    #                                skip sooner. (0 also disables the behavior.)
    #   enrich_long_summary_sentences  max sentences in a long note's summary.
    #
    # NOTE on 300: measured on real notes, a ~300+ word note's polished rewrite
    # can approach the output budget on its own. With the schema reordered so
    # metadata (title/tags/summary) is emitted BEFORE polished, a truncation is
    # now graceful — but skipping the rewrite entirely for genuinely long notes
    # is still better than a half-written one.
    #
    # This MUST stay consistent with model_max_num_tokens: a polished note has to
    # fit  system-prompt + note + output  inside the context, or the note gets
    # trimmed and we'd polish text the model only partly saw. With a 6144 context
    # (~2400 system prompt + 3072 output) roughly 480 words fit, so the threshold
    # is 450 — beyond that a note gets metadata + summary only, which is the right
    # answer anyway for long-form text the author wrote deliberately.
    # If you raise model_max_num_tokens, raise this too (and vice-versa).
    # On-demand Polish (⋯ → Polish) always overrides this.
    "enrich_skip_polish_enabled": True,
    "enrich_skip_polish_words": 450,
    "enrich_long_summary_sentences": 2,

    # Quick-capture threshold (the OTHER end of the scale from the "long note"
    # knobs above). Notes at/below this many CHARACTERS are treated as quick
    # captures and kept verbatim — no polished rewrite — on the assumption a
    # terse capture ("milk, eggs", "call mum") is already exactly as intended.
    # List-shaped notes are always treated as quick regardless of this value.
    #
    #   Lower it (e.g. 80)  → short PROSE gets polished (typos/grammar fixed),
    #                         only very terse captures stay untouched.
    #   Raise it (e.g. 300) → more short notes stay exactly as you wrote them.
    #   Set 0               → length never makes a note "quick"; only list shape
    #                         does, so all prose is eligible for polish.
    #
    # Note: on-demand Polish (the button) always overrides this.
    "enrich_quick_note_chars": 120,

    # Custom prompt override. Set to a path of a plain UTF-8 text file
    # containing the full system prompt to use instead of the built-in default.
    # Leave blank to use the built-in prompt (recommended).
    # The file is loaded once at startup; restart the daemon to pick up changes.
    # See memos_daemon/prompt.py for schema coupling notes before editing.
    "prompt_file": "",

    # Pin the maintained notes? With tags + Memos shortcuts you can find them by
    # filter instead, so pinning is optional. Default off — use the tags.
    "pin_maintained_notes": False,

    # Subtitle for the enriched body while a note is under review.
    "enriched_label": "Enriched Note",
    # Subtitle for the editable body in an approved note (also the :note region).
    "note_label": "Note",
    # Review-queue tag added to every suggestion and removed on approval, so the
    # user can see everything awaiting their review via one Memos shortcut.
    "review_tag": "pending-approval",

    # Write resilience. Transient Memos failures (connection refused, 5xx,
    # timeouts) are retried with exponential backoff; if they still fail, the
    # completed enrichment is saved to pending_writes_file and re-applied when
    # Memos is reachable again — so a brief outage never discards a finished
    # inference.
    "write_max_retries": 4,
    "write_backoff_base": 0.5,
    "write_backoff_cap": 8.0,
    "pending_writes_file": "",   # blank → <state_file>.pending

    # Note correlation. Two backends: SEMANTIC (embeddings) when embed_model is
    # set and loadable, else LEXICAL (TF-IDF keyword overlap). Embedding and
    # lexical cosine live on different scales, so each has its own thresholds.
    # related_top_n: max related notes linked from each enriched note.
    # *_threshold (lexical): min TF-IDF cosine to relate/cluster (~0.1-0.2).
    # embed_*_threshold: min embedding cosine to relate/cluster (~0.4-0.6).
    # correlation_corpus_size: how many recent notes to consider when correlating.
    "correlation_enabled": True,
    "related_top_n": 3,

    # Reminder de-duplication. When re-enrichment produces a reminder that shares
    # the same resolved DATE as an existing one for the same note AND whose title
    # is at least this cosine-similar (MiniLM embeddings), it's treated as a
    # reworded duplicate and skipped — so "ITR Filing Deadline" doesn't pile up
    # next to "Income Tax Return Deadline". Same date alone won't merge distinct
    # tasks; this needs semantic title agreement too.
    "reminder_dupe_similarity": 0.68,
    "related_threshold": 0.12,
    "cluster_threshold": 0.12,  # lexical: lower = more groups; raise to 0.18+ for stricter grouping
    # Embedding thresholds, tuned for all-MiniLM-L6-v2's score scale. Measured
    # on a real corpus: a genuinely-related pair (two notes about the same visa
    # application) scored ~0.47, while unrelated notes sharing generic
    # vocabulary reached ~0.42. That gap is narrow, which is why the
    # tag-confirm guard below matters more than the threshold itself.
    # Check real scores with `hg_agent relations` before changing these.
    "embed_related_threshold": 0.45,
    "embed_cluster_threshold": 0.55,
    # Tag-confirmation guard. A pair scoring between the related threshold and
    # (threshold + this band) is only linked when the notes SHARE A TAG. Above
    # the band, a strong score links on its own. This kills false positives from
    # generic vocabulary overlap (a vet visit vs a blood-pressure note) while
    # keeping real pairs that agree topically (two #telecom bills). 0 disables.
    "embed_confirm_band": 0.10,
    # Tag assist — the complement of the guard. A pair that SHARES A TAG may
    # link at a lower score (threshold - this). A shared tag is corroborating
    # evidence the embedding alone is too weak to see: two #telecom bills in
    # different languages score poorly yet are obviously related. 0 disables.
    # Sized from real data: measured pairs that SHOULD link scored 0.28-0.32
    # (two telecom bills in different scripts; three notes about one visa
    # application), so 0.20 links from 0.25 with margin. This is safe because
    # the SHARED TAG — not the score — is what qualifies the pair.
    "embed_tag_assist": 0.20,
    # How many recent notes the correlation corpus holds. This was 200 when both
    # hot paths were interpreted Python loops: related() looped over every note,
    # and clusters() did a scalar sim on every PAIR (O(n²) — ~7s at 500 notes).
    # Both are now vectorised (one BLAS matmul each), so the cap can rise a long
    # way. Measured: a full pass at 1000 notes is ~1.3s of correlation; clusters
    # at 1000 is ~80ms. 2000 is comfortable on-device.
    #
    # The ceiling now is RAM, not CPU: each note's 384-dim float32 vector is
    # ~1.5 KB, so 2000 notes ≈ 3 MB of matrix — trivial. Even 100k would be
    # ~150 MB. If you ever hold a truly large corpus (>100k), the next step is a
    # pluggable ANN backend (hnswlib) behind Corpus — deliberately NOT added now,
    # since it needs a C++ build that may not compile on Termux and brute-force
    # numpy stays fast well past any near-term note count. Raise this as your
    # collection grows; there is no longer an O(n²) term waiting to bite.
    "correlation_corpus_size": 2000,
    "correlation_cache_seconds": 120,

    # Semantic embeddings (fail-safe). embed_model is a sentence-transformers
    # model name or local path. "all-MiniLM-L6-v2" (~90MB, 384-dim) is the
    # default: small, fast, and — measured on a real corpus — it separated
    # topics BETTER than the 5x-larger multilingual variant, which compressed
    # every score into a narrow band and surfaced worse false positives.
    # (Tried and reverted: paraphrase-multilingual-MiniLM-L12-v2.)
    # If the package isn't installed or the model can't load, the agent falls
    # back to lexical similarity automatically — it never breaks. Set to "" to
    # force lexical. embed_cache_dir is an optional local cache/download folder.
    "embeddings_enabled": True,       # false = no embedding model at all (saves memory and heat; search becomes keyword-only)
    "embed_model": "all-MiniLM-L6-v2",
    "embed_cache_dir": "",
    # "auto" (onnx first, then sentence-transformers) | "onnx" | "sentence-transformers".
    # onnx needs only: pip install onnxruntime tokenizers  (no PyTorch). embed_onnx_dir is an
    # optional folder already holding model.onnx + tokenizer.json (skips the one-time download).
    "embed_backend": "auto",
    "embed_onnx_dir": "",
    # Model to use if embed_model can't be loaded (offline first run, bad name).
    # Notes indexed under one model are never compared with another model's
    # vectors, so a fallback is always safe — it just means a re-index later.
    "embed_model_fallback": "",
    # Per-model gating overrides (None = use the model's built-in profile; see
    # embeddings.PROFILES). Set after running the eval harness on real notes.
    "embed_floor_search": None,
    "embed_floor_ask": None,
    "embed_margin": None,
    "embed_z_min": None,
    # Passage index: ~500-char chunks, 80-char overlap, at most 40 per note.
    "embed_chunk_chars": 500,
    "embed_chunk_overlap": 80,
    "embed_max_chunks": 40,
    # Durable job queue (agent_jobs): leases, retries with backoff, dead-letter.
    "durable_queue": True,
    "job_lease_seconds": 1200,
    "job_backoff_seconds": [30, 120, 600],
    "reindex_boot_seconds": 30,
    "reindex_boot_limit": 50,

    # Photos, voice notes and sketches (see media.py). The agent transcribes
    # recordings and reads text in pictures, on the phone, one job at a time.
    #   media_backend: "auto" | "command" | "litert" | "off"
    #   media_audio_cmd / media_image_cmd: command templates (no shell). Placeholders:
    #       {file} the stored file, {wav} a 16 kHz mono wav made with ffmpeg,
    #       {lang} audio language, {ocr_langs} OCR languages.
    #     Leave empty to auto-detect tesseract (images) and, when
    #     media_whisper_model is set, whisper.cpp (audio).
    #   media_litert_multimodal: load the model's vision/audio towers so Gemma can do it.
    "media_enabled": True,
    "media_backend": "auto",
    "media_audio_cmd": "",
    "media_image_cmd": "",
    "media_whisper_model": "",
    "media_audio_lang": "auto",        # "te" / "en" / "auto". With Medha, set "te" if your voice notes are Telugu: left on auto it sometimes answers in Latin script
    "media_ocr_langs": "eng",
    "media_litert_multimodal": False,
    "media_audio_prompt": "",          # Medha speech-to-text: optional spelling / name hints (e.g. "Adabala, Quicksilver")
    "media_audio_compare": True,       # while Medha's Telugu is being judged: also run whisper on each clip and log both (local jsonl)
    "media_compare_file": "",          # blank → <state dir>/media_compare.jsonl
    "media_video_frames": 3,    # videos: how many still frames the model describes (evenly spaced); 0 = sound only
    "media_video_max_s": 300,   # videos: only the first this-many seconds are read
    "media_describe": True,   # photos/sketches: also write a short "what is this" description (needs multimodal)
    "media_job_timeout_s": 600,
    "media_scan_batch": 20,

    # Confidence-gated human review. When the model's confidence for an
    # enrichment is below this, the suggestion's approve box is labeled
    # "low confidence" as a visible cue (it still shares the one review queue).
    "confidence_review_threshold": 0.6,

    # Priority queue: when several notes are pending, enrich high-priority
    # candidates first (cheap keyword pre-pass) so useful output lands first.
    "priority_queue_enabled": True,

    # Thermal guard (Celsius)
    "thermal_enabled": True,
    "thermal_hot_celsius": 70.0,
    "thermal_resume_celsius": 60.0,
    "thermal_zone_glob": "/sys/class/thermal/thermal_zone*/temp",
    "thermal_poll_seconds": 10,
    "thermal_max_wait_seconds": 600,

    "idle_backoff_max_seconds": 300,

    # ── Idle power tuning (event mode) ────────────────────────────────────────
    # The agent reacts to notes in real time via the SSE link, so these timers
    # are only for background bookkeeping — raising them saves battery with
    # almost no downside; lowering them makes status/briefs more real-time.
    #
    # main_loop_tick_seconds: how often the background loop wakes to check its
    #   timers. Lower = snappier brief/backup checks but more CPU wakes (worse
    #   for deep sleep). Higher = the CPU sleeps longer between wakes. Real-time
    #   note reactions are unaffected (those come via SSE). Default 5.
    "main_loop_tick_seconds": 5,
    # heartbeat_interval_seconds: how often the "agent live" dot + telemetry are
    #   written to the DB. Lower = fresher status dot, more tiny disk writes.
    #   Higher = fewer writes, slightly staler dot. Set to 0 to DISABLE the
    #   periodic heartbeat entirely (the dot then only updates on activity).
    #   Default 30.
    # Re-enrichment settle window. A living note is re-enriched on a body-hash
    # change; this holds the run back until the note has been quiet this long,
    # so editing in bursts produces ONE enrichment after you stop rather than
    # one per pause. First-time enrichment ignores this and fires promptly.
    # Safety net for the commit-based enrichment model. A note is normally
    # enriched when the user closes the editor; if a session ends uncleanly
    # (app killed, phone died) the note is left 'dirty'. The agent promotes any
    # note stranded 'dirty' longer than this to enrich it anyway. Set high
    # enough that it never fires during an active note-taking session.
    "enrich_quiet_seconds": 60,   # a note being typed is queued only after this long without an edit (0 = at once); a commit skips the wait
    "enrich_stranded_seconds": 1800,
    "force_stuck_seconds": 120,   # a note "Processing" this long with no worker on it is rescued

    "heartbeat_interval_seconds": 30,

    # Google Calendar integration.
    # When enabled, action items extracted from APPROVED notes that contain a
    # recognisable date/time hint are created as Google Calendar events.
    # calendar_timezone must be a valid IANA tz string (e.g. "Asia/Kolkata").
    # calendar_default_duration_minutes is used when no end-time is detectable.
    # calendar_lookahead_days: only schedule events within this many days from
    # today (prevents far-future junk); 0 = no limit.
    "calendar_enabled": False,          # set to true once token.json exists
    "calendar_timezone": "Asia/Kolkata",
    "calendar_default_duration_minutes": 30,
    "calendar_lookahead_days": 30,
    "calendar_add_description": True,   # include note title + summary in event
    "calendar_id": "primary",          # target calendar; "primary" = your main one
    # OAuth files (see docs/CALENDAR_SETUP.md):
    #   client secret JSON downloaded from Google Cloud Console (Desktop app),
    #   and the token.json that `calendar-auth` generates from it.
    # LEAVE THESE BLANK to auto-derive them from your DB's directory:
    #   <db dir>/gcal_client_secret.json and <db dir>/gcal_token.json
    #   (typically ~/.quicksilver/). Only set them to override that location.
    "calendar_client_secret_file": "",
    "calendar_credentials_file": "",

    # Event-driven (hybrid) mode. When enabled, the daemon runs a small local
    # HTTP server that Memos posts webhook events to, plus a slow safety poll to
    # catch anything missed. When disabled, it falls back to interval polling.
    "webhook_enabled": True,
    # The daemon BINDS (listens) on this host. 0.0.0.0 = all interfaces, which
    # is fine. Do NOT use this value as the URL you give Memos — see below.
    "webhook_host": "0.0.0.0",
    "webhook_port": 19283,
    "webhook_path": "/memos-hook",
    # The URL Memos should CONNECT to. This must be a reachable address, NOT
    # 0.0.0.0 (you cannot dial 0.0.0.0). Leave blank to auto-derive a sane
    # connectable URL: 127.0.0.1 when bound to 0.0.0.0/localhost, else the bind
    # host. If Memos runs on another machine/container, set this to the LAN IP
    # or hostname that reaches this daemon, e.g. http://192.168.1.50:19283/memos-hook
    "webhook_advertise_url": "",
    "webhook_secret": "",  # optional shared secret; if set, required as ?token=
    "safety_poll_seconds": 600,  # reconciling poll interval in webhook mode
}

CONFIG_PATH = Path.home() / ".quicksilver/config.quicksilver.json"


def load_config(config_path: str | None = None) -> dict:
    """Load config, merging DEFAULT_CONFIG with a JSON file.

    Priority for the config file path:
      1. Explicit config_path argument (from --config CLI flag).
      2. HG_AGENT_CONFIG environment variable.
      3. The default ~/.quicksilver/config.quicksilver.json.

    A missing file at an EXPLICIT path is a hard error (the operator clearly
    intended to use it) — we log loudly rather than silently using defaults,
    which is exactly the failure mode that made the daemon fall back to the
    Memos backend when a Quicksilver config wasn't picked up.
    """
    import os
    cfg = DEFAULT_CONFIG.copy()

    explicit = config_path or os.environ.get("HG_AGENT_CONFIG", "")
    path = Path(explicit) if explicit else CONFIG_PATH

    if path.exists():
        try:
            cfg.update(json.loads(path.read_text()))
            log.info(f"Loaded config from {path}")
        except Exception as e:
            log.error(f"Failed to parse config {path}: {e}")
            if explicit:
                # An explicitly requested config that won't parse is fatal —
                # don't silently run with wrong defaults.
                raise
            log.warning("Using built-in defaults")
    elif explicit:
        log.error(f"Config file not found at explicit path: {path}")
        raise FileNotFoundError(
            f"--config pointed to '{path}' but it does not exist. "
            f"Check the path.")
    # else: no explicit path and default doesn't exist → use defaults silently
    env_map = {
        "MEMOS_DAEMON_MODEL_PATH": ("model_path", str),
        "MEMOS_DAEMON_BASE_URL": ("memos_base_url", str),
        "MEMOS_DAEMON_TOKEN": ("memos_token", str),
        "MEMOS_DAEMON_POLL_INTERVAL": ("poll_interval_seconds", int),
        "MEMOS_DAEMON_DRY_RUN": ("dry_run", lambda v: v.lower() in ("1", "true", "yes")),
    }
    for env_key, (cfg_key, caster) in env_map.items():
        val = os.environ.get(env_key)
        if val is not None:
            cfg[cfg_key] = caster(val)

    # Smart defaults for calendar file paths: keep all persistent state in one
    # directory. If the user hasn't set these explicitly, derive them from the
    # DB's directory (typically ~/.quicksilver), so a fresh setup only needs to
    # drop the client secret in place — no path configuration required. Any
    # value the user DID set always wins (we only fill in blanks).
    state_dir = ""
    if cfg.get("db_path"):
        state_dir = os.path.dirname(os.path.abspath(os.path.expanduser(cfg["db_path"])))
    if not state_dir:
        state_dir = str(Path.home() / ".quicksilver")
    if not (cfg.get("calendar_client_secret_file") or "").strip():
        cfg["calendar_client_secret_file"] = os.path.join(state_dir, "gcal_client_secret.json")
    if not (cfg.get("calendar_credentials_file") or "").strip():
        cfg["calendar_credentials_file"] = os.path.join(state_dir, "gcal_token.json")

    return cfg
