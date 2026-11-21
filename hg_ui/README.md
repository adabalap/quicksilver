# పాదరసం Quicksilver (hg_ui): private, on-device AI notes

Version **1.6.7**. A Progressive Web App on Flask + SQLite. You write the way you would on paper: messy, fast, unpunctuated.
A local AI agent ([hg_agent](../hg_agent/README.md)) reads each note on your phone and quietly adds a title, tags, a summary,
related notes, the tasks buried in the text and the dates that belong on a calendar. Later you can ask your notes a question
in plain language and get an answer drawn only from what you wrote, with the exact notes cited.

**Nothing leaves the device.** No cloud, no account, no analytics.

> **Watch the tour:** [`demo/quicksilver-demo.mp4`](demo/quicksilver-demo.mp4) (narrated; script in
> [`demo/SCRIPT.md`](demo/SCRIPT.md)). Share it freely: it contains only made-up sample notes.
> **How it stays private:** [`demo/quicksilver-privacy.mp4`](demo/quicksilver-privacy.mp4), a 65-second architecture walkthrough.

## Quick start (Termux)

```bash
pip install flask setproctitle
./bin/hg_ui start            # http://127.0.0.1:5230
./bin/hg_ui status | logs | stop | restart
./bin/hg_ui backup [dest] | restore <file> | clear [--yes] | dedupe | import-keep <takeout.zip>
```

Open it in Chrome, menu, **Add to Home Screen**. Start the agent as well (`hg_agent start`) so notes get enriched. Both
processes show up in `ps -ef` as `hg_ui` and `hg_agent`. To reach it from anywhere, put a Cloudflare Tunnel in front
(for example `https://hg.adabala.com`).

### Environment

| Variable | Default | Meaning |
|---|---|---|
| `QS_PORT` | `5230` | Port the server listens on |
| `QS_HOST` | `127.0.0.1` | Bind address. Use `0.0.0.0` only if you want other devices on your network to reach it |
| `QS_DB` | `~/.quicksilver/notes.db` | The database. The agent must use the same file (`db_path`) |
| `QS_SECRET` | random each start | Flask session secret. Set a fixed value if you want sessions to survive restarts |
| `QS_DEBUG` | `0` | `1` turns on Flask debug (never on a tunnelled server) |
| `HG_EMBED_URL` | `http://127.0.0.1:5232/embed` | Where the embedding sidecar listens |

Agent settings live in a separate JSON file; see the [agent configuration reference](../hg_agent/docs/CONFIGURATION.md).

## A tour of the app

| Screen | What it does |
|---|---|
| **Home** | A morning read: greeting, a brief, **Bills due** (only when something is due within a week or overdue), what needs a decision, what the agent did today, then a few numbers. Nothing is shown when there is nothing to say. |
| **Notes** | A masonry feed of cards with a quick-action tray (pin, archive, delete). Search, filters (type, when, tag) and saved searches. ✦ switches search to **Ask**. |
| **Note** | Read view with the title, summary, tags, tasks, reminders, related notes and attachments. **Tap the title to rename it.** Your title is never overwritten by the agent; clear it to get the suggested one back. |
| **Actions** | Every task from every note. Swipe right for done, left to drop (Undo for everything). |
| **Reminders** | Dated reminders. **Latest first** by default; one tap flips to soonest first. Dismissing is "done". |
| **Connections** | A graph of related notes and clusters. |
| **Side menu** | Views, a **collapsible Tags** list (your choice is remembered), Your week, Trash and more. |
| **Settings** | The agent, Bills, photos and voice, appearance, your data, help. |

### Capture from anywhere
The **+** button adds text, a checklist, 🎙 voice, 📷 photo, 🎬 video or ✏️ sketch. Long-press the app icon for **New note, Voice,
Photo or Ask**. Share text, photos or audio from any app and they arrive as a note; if you are offline, shared text is saved
and syncs later. Offline captures are queued and uploaded in order.

### Ask: Notes, Chat, Write
Tap ✦ in the search bar and pick a mode. **Notes** gives short answers with sources. **Chat** is a plain prompt to the
on-device model. **Write** ("a blog post from my notes on Fedora") retrieves up to 14 notes, plans sections and writes each,
citing notes; **Save as note** or **Copy** the result.

### Bills from your messages (optional)
Off until you switch it on in **Settings, Bills**. Reads bill messages on this phone and turns each into two reminders (two
days before, and on the day, 9:00). OTPs, promotions and personal messages are never stored; only a bill's own message is kept,
on that bill, until it is paid. A bill closes itself when its payment message arrives.

- **View and add bills** (Settings, Bills, or "All bills" on the home card) lists every open bill. **Paid** or **Not a bill**
  removes it and deletes its message text. **Add a bill** takes a name, an amount and a due date and sets the same two reminders;
  it works even with message reading off.
- Tapping a bill notification or a Medha brief row opens that bill (`/?bill=<id>`): the original message, its links and Mark as paid.

### Video notes
**+, Video** records with the camera or picks a clip from the gallery: **up to 2 minutes and 100 MB** (mp4, webm, mov, 3gp, mkv). It plays in the note (seeking works), cards show a poster frame cut with ffmpeg, and you can share a video into the app. The agent pulls out the sound and transcribes it, describes a few evenly spaced frames, and stores one text ("Spoken: …", "On screen: …") that feeds search, Ask and enrichment. If the phone has no speech engine or no vision model, whichever half is possible is used; with neither the video is just kept. Videos count in Settings, Photos, voice & video and in System health storage, and are included in `hg_ui backup` and exports, so mind the size. Filter the feed with **Filters, Videos**.

### Photos, voice notes and sketches
Files are stored beside the database in `attachments/`; the agent transcribes and describes them on the phone and the text feeds
search, Ask and enrichment. It is never written into the note body unless you tap **Insert into note** (or the note is a voice
note with no text). Limits: image 25 MB, sketch 8 MB, audio 80 MB, 24 attachments per note. Optional per-device *live
captions* use the browser's speech service, which may send audio to the browser vendor, so they are off by default.
Photo and sketch cards load small cached thumbnails (Pillow, else ffmpeg, else the original). **Write a story from this photo**
offers styles (Fairy tale, Mystery, Funny, Heartfelt, Adventure, Sci-fi), length and language.

### Themes
**Settings, Appearance** has Light, Dark or Auto, plus **Note color** themes that change the feed (per device):
Colorful (default), Black and white, Slate, Midnight, Sunset, Forest, Ocean, **Cartoon** (pop colours, inked outlines, a hard
offset shadow and a small sticker in the card's footer corner) and **Material** (Material 3 tonal surfaces, flat, large radius).

### Trash, import and export
Deleting moves a note to **Trash** for 30 days (text, tags, tasks, reminders, enrichment and attachments kept; **Restore**
brings it back; calendar events are not re-created). **Settings, Your data**: export a `.zip` (notes.json, Markdown copies,
attachments) or import Google Keep Takeout, Quicksilver exports and `.md`/`.txt` files, with a preview, duplicate skipping and a
safety backup. Uploads over about 95 MB will not pass the Cloudflare tunnel; on the phone run `hg_ui import-keep <takeout.zip>`.
Automatic safety copies cover the database only; `hg_ui backup` includes attachments.

### Other things worth knowing
Your week (a Sunday digest), possible duplicates (you choose what to keep), task follow-through (week-old tasks raised, at most
three, each with one suggested move and Undo), System health (agent, engine in use, versions, queue, storage, backups), share and
export (Markdown, PDF via print, copy), highlights (stored as invisible markers, so pasted code like `a == b` is never mistaken
for one).

## Settings you can change in the app

Stored in the database; they take effect at once.

| Setting | Default | What it does |
|---|---|---|
| `enrich_min_words` | `6` | Notes shorter than this are not sent to the agent. Raise to skip quick scraps. |
| `summary_mode` | `long` | `always`, `long` or `never`: when a summary is written. |
| `summary_min_words` | `100` | In `long` mode, notes shorter than this are their own summary. |
| `brief_mode` | `ondemand` | `ondemand` (daily brief when you ask) or `auto`. |
| `default_note_type` | `text` | `text` or `checklist` for a new note. |
| `highlight_color` | `y` | Default highlight colour. |
| `search_min_similarity` | `0.30` | How close a note must be to count as a search match. Raise toward 0.40 if unrelated notes appear; lower toward 0.20 if search feels strict. |
| `ask_min_similarity` | `0.20` | Looser on purpose for Ask: an extra excerpt is harmless, a missed one is fatal. |
| `notify_muted` | `0` | `1` mutes in-app notifications even if the browser permission is granted. |
| `followups` | `on` | Daily task follow-through (`on` or `off`). |
| `sms` | `off` | Bill reminders from messages (`on` or `off`). |

Per-device choices (kept in the browser, not the database): colour mode, **Note color** theme (`hg_note_theme`), connection
mode (`hg_conn_mode` remote or local, `hg_local_port` default 5230), whether the Tags menu is open (`hg_tags_open`), the
Reminders order (`hg_rem_sort`), and the first-run tour (`hg_tour`).

## Links into the app

| URL | Opens |
|---|---|
| `/?note=<id>` | That note (used by Medha brief rows and notifications) |
| `/?bill=<id>` | That bill |
| `/?new=text`, `/?new=voice`, `/?new=photo` | A new note of that kind (the app-icon shortcuts) |
| `/?ask=1` | Ask |
| `/?shared=<id>` | The note a share just created |

## Note lifecycle

```
raw ─► (agent enriches) ─► pending ─► (you tap ✓ Approve) ─► approved
Autosave while typing marks a never-enriched note "dirty"; closing the editor or Run AI makes it "force".
Editing an approved note re-runs the agent (small edits are just re-locked). ⟳ Redo forces it any time.
```

All AI output (title, summary, tags, tasks, relationships) lives in its own tables; your note body stays exactly as you wrote it.

## Code layout

`static/index.html` is a thin shell. The app is plain scripts sharing one global scope, loaded in filename order
(`static/js/00-util.js` to `99-boot.js`), and styles in `static/css/*.css` (`themes.css` holds the Cartoon and Material shapes).
Every script and stylesheet URL carries a build token (`?v=…`) the server fills in, so browsers never mix old and new files.

| Area | Files |
|---|---|
| Helpers, themes, offline store, network | `00-util`, `10-store` |
| Home strips (incl. bills), Ask, lists, graph | `20-home`, `30-ask`, `40-render`, `45-graph` |
| Note detail, operations, editor | `50-detail`, `55-notes` (rename, patch), `60-editor` |
| Live updates, media, settings | `65-realtime`, `70-media`, `72-attachments`, `74-settings` |
| Views, filters, share, insights, story | `76-views` (tags menu), `80-filters`, `82-share`, `84-insights`, `86-story` |
| Events, dashboard and settings screen, boot | `90-events`, `91-ui`, `92-pulse`, `99-boot` |

The server is the `server/` package, one module per area (importing registers routes; dependencies run one way,
`structure_test.py` checks). `core` (app, DB, SSE), `dbinit` (schema and migrations), `notes_common`/`routes_notes` (notes API,
incl. `user_title`), `actions` (tasks, reminders), `bills` (list, add, paid, scan, forget), `home`, `dashboard`, `settings`,
`search`, `ask`, `story`, `attachments`, `importexport`, `trash`, `followups`, `insights`, `system`, `cli`.

## Tests and speed

```bash
python structure_test.py   python smoke_test.py    python feature_test.py   python e2e_test.py
python ui_polish_test.py   python ui_load_test.py  python bills_test.py     python followups_test.py
python media_test.py       python video_test.py       python retrieval_test.py  python queue_test.py   python migration_test.py   node boot_test.js
python saved_inbox_test.py   # Saved from Medha: every choice creates exactly what it says, and can be undone
python3 tools/perf.py --url http://127.0.0.1:5230     # times every screen's request; flags anything over 250 ms
python3 tools/perf.py --seed 3000                     # builds a throwaway database of that size and times it
```

## Version history

| Version | Highlights |
|---|---|
| **1.6.1** | Ask sources are ordered by the % shown beside them; a video upload upgrades the database table itself if startup could not |
| **1.6.2** | The dashboard and Health say "Medha not reachable" when Medha is down instead of a stale "medha" |
| **1.6.3** | Service-worker cache bumped so the new status wording loads instead of the cached old script |
| **1.6.4** | Version kept in step with the agent (Now brief fix) |
| **1.6.5** | Version kept in step with the agent (longer Now brief) |
| **1.6.6** | Version kept in step with the agent (full Now brief) |
| **1.6.7** | Photos are decoded already shrunk and the canvas is freed at once, so taking a picture needs far less memory |
| 1.6.0 | Video notes (record or pick up to 2 minutes; the agent transcribes the sound and describes a few frames); borderless writing canvas; tap anywhere to type; tag-confusion fix in task extraction |
| 1.5.x | Bills list and add, collapsible Tags, Reminders order, rename, Cartoon and Material themes, demo videos |
| 1.5.0 | All-bills list and add-a-bill; collapsible Tags; Reminders sorted latest first; rename any note; Cartoon theme rebuilt and a Material theme; About page and docs rebuilt; demo video |
| 1.4.x | Bill links open the bill; typing guard (autosave marks a note dirty, agent waits); Medha Now brief deep links (`/?note=`) |
| 1.4.0 | Bills from messages |
| 1.3.x | Dashboard that reads like a morning, swipe gestures, capture from anywhere, flicker-free refresh, redesigned Your week and System health |
