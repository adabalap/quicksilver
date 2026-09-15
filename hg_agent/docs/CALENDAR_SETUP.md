# Google Calendar Setup

This guide walks through connecting the agent to Google Calendar so that dated
items become real calendar events.

How it works at a glance: there are two sources of calendar events.

1. **Quick Events** (the `📅 Event` option on the app's `+` menu) — you type an
   exact title, date, and time. This is authoritative, so the agent creates the
   calendar event **immediately**, without waiting for enrichment or approval.
2. **Agent-extracted reminders** — the model finds a date/time in a free-text
   note. On approval, the agent creates an event for each one with a resolvable
   absolute date. Vague dates ("tomorrow", "next Monday") that can't be pinned
   down are left **pending** in the note's reminders (no event is created from a
   guess); add an explicit date to the note and it'll be picked up.

Authentication uses **OAuth 2.0 (Desktop app)**: you download a client-secret
file from Google once, run a one-time authorization that produces a `token.json`,
and the agent uses (and auto-refreshes) that token thereafter. No passwords are
stored; the token can be revoked anytime from your Google account.

---

## Prerequisites

Install the Google client libraries in the agent's environment (the same Python
that runs the daemon — inside the proot on Android):

```bash
pip install --break-system-packages \
    google-auth google-auth-oauthlib google-auth-httplib2 \
    google-api-python-client python-dateutil
```

If these aren't installed, the agent logs a clear warning and simply skips
calendar features — nothing else breaks.

---

## Step 1 — Create OAuth credentials in Google Cloud

1. Go to <https://console.cloud.google.com> and create (or select) a project.
2. **APIs & Services → Library →** search **"Google Calendar API" → Enable**.
   *(This is a distinct step from creating credentials below. If you skip it,
   event creation fails later with `accessNotConfigured`. Make sure it's enabled
   on the same project your credentials belong to.)*
3. **APIs & Services → OAuth consent screen:**
   - User type: **External** (unless you have a Workspace org).
   - Fill the required app name / support email / developer email.
   - **Scopes:** you can leave the scope list empty here; the agent requests
     `calendar.events` at auth time.
   - **Test users:** add the Google account whose calendar you'll use. (While the
     app is in "Testing" status, only listed test users can authorize — that's
     fine for personal use and avoids the verification process.)
4. **APIs & Services → Credentials → Create Credentials → OAuth client ID:**
   - Application type: **Desktop app**.
   - Create, then **Download JSON**. This is your `client_secret_*.json`.

Copy that file to the device, e.g.:
```
/home/adabalap/.quicksilver/gcal_client_secret.json
```

---

## Step 2 — Put the client secret where it's auto-found

The simplest setup: drop the downloaded client secret into your `.quicksilver`
directory (next to your DB) with the exact name the agent looks for, and you
don't have to configure any paths at all.

```bash
mkdir -p /home/adabalap/.quicksilver
# copy your downloaded client_secret_XXXX.json to this exact path/name:
cp <downloaded>.json /home/adabalap/.quicksilver/gcal_client_secret.json
```

Then all you need in `~/.quicksilver/config.quicksilver.json` is:

```json
  "calendar_enabled": true
```

The agent auto-derives the file paths from your DB's directory:
- `calendar_client_secret_file` → `<db dir>/gcal_client_secret.json`
- `calendar_credentials_file`   → `<db dir>/gcal_token.json` (created on auth)

**Only if you want them elsewhere**, set the paths explicitly (an explicit value
always wins over the auto-derived one):

```json
  "calendar_enabled": true,
  "calendar_client_secret_file": "/some/other/place/client_secret.json",
  "calendar_credentials_file":   "/some/other/place/token.json"
```

> Keep everything in `~/.quicksilver/` — that's where your DB, token, config,
> and state all live, so nothing is inside the redeployable code directory.

---

## Step 3 — Authorize (one time)

This produces the token file. The easiest way is via the `hg_agent` launcher,
which points at your config automatically. Choose the path for your device.

### Option A — Device has a browser (laptop/desktop)
```bash
hg_agent/bin/hg_agent calendar-auth
```
A browser opens, you approve access, and the token is saved automatically to
the `calendar_credentials_file` path.

### Option B — Headless device (Termux / Android / SSH, no browser) ← recommended on your phone
```bash
hg_agent/bin/hg_agent calendar-auth --headless
```
The command prints a URL. Open it on **any** device with a browser (your phone's
normal browser or a laptop), approve access, copy the authorization code Google
shows you, and paste it back into the terminal. The token is then written.

> Both commands also work as `python3 app.py calendar-auth [--headless]` if you
> prefer calling the entry point directly.

### Option C — Authorize on a laptop, copy the token to the device
The token is portable. On a laptop with the libraries installed and the **same**
`client_secret.json`, run Option A, then copy the resulting `token.json` to the
device at the `calendar_credentials_file` path. This is often the smoothest route
for an Android/Termux setup.

> Note: the OAuth scope is `calendar.events` — the agent can create/manage
> events it makes, not read your whole calendar.

---

## Step 4 — Enable and restart

Once `token.json` exists:

```json
{ "calendar_enabled": true }
```

Restart the agent:
```bash
sv restart memos-agent      # or however you run it
```

Verify in the log that calendar features are active (no "not installed" or
"no valid credentials" warnings). From here on, the token auto-refreshes; you
won't need to re-authorize unless you revoke access or delete `token.json`.

---

## How events get created (so expectations are right)

- **Quick Events** (app `+` menu → `📅 Event`) create a calendar event
  **immediately** on creation — no approval needed. You gave the exact date, so
  there's nothing to review.
- **Agent-extracted reminders** create events **on approval**, and **only** for
  reminders with a resolvable absolute date/time (ISO-like, e.g.
  `2026-07-01T10:00`).
- Vague/relative dates the resolver can't pin down do **not** create events.
  They're left **pending** on the note (no event from a guess). Add an explicit
  date to the note (e.g. `2026-07-15 10:00`) and it'll be created on the next
  pass.
- If calendar creation fails (e.g. an expired token), the reminder is **left
  pending, never falsely marked done** — so once you fix the token it retries
  and the event is not lost.
- `calendar_lookahead_days` (default 30) prevents far-future junk; events beyond
  that window are skipped. Set 0 for no limit.
- `calendar_default_duration_minutes` (default 30) sets the event length when no
  end time is detectable.
- `calendar_add_description` includes the note's title + summary in the event
  body.

---

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `accessNotConfigured` / "Calendar API has not been used in project … or it is disabled" | The **Calendar API itself isn't enabled** on your project (separate from creating credentials). Open the link in the error (it has your project baked in), click **Enable**, wait ~2 min, then re-approve the note. |
| Log: "google-api-python-client is not installed" | Run the pip install in Prerequisites (in the agent's env). |
| Log: "no valid credentials" | Token missing or invalid — run `hg_agent/bin/hg_agent calendar-auth --headless`. Check `calendar_credentials_file` path. |
| `client secret file not found at ''` | `calendar_client_secret_file` is empty/missing in the config, or the file wasn't copied to the device. This is the most common first-time error — see Step 2. |
| `invalid_grant: Token has been expired or revoked` | Token expired — re-run `calendar-auth --headless`. If it recurs every ~7 days, the OAuth app is in "Testing" mode; publish it to "In production" (consent screen) so tokens don't expire weekly. |
| `calendar-auth` opens no browser on the phone | Use `--headless` (Option B) or authorize on a laptop and copy `token.json` (Option C). |
| "access_denied" during consent | Your Google account isn't in the **Test users** list on the OAuth consent screen. Add it. |
| Events created in the wrong zone | Set `calendar_timezone` to your IANA zone (e.g. `Asia/Kolkata`). |
| Want a non-default calendar | Set `calendar_id` to the target calendar's ID (from its settings in Google Calendar). |

---

## Security notes

- `gcal_client_secret.json` and `gcal_token.json` are sensitive — keep them in
  `~/.quicksilver/` with normal user-only permissions; don't commit them.
- The token grants only `calendar.events` scope on the account you authorized.
- Revoke anytime at <https://myaccount.google.com/permissions>, or just delete
  `gcal_token.json` and set `calendar_enabled: false`.
