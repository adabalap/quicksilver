# Changelog — v6.8

Makes the Google Calendar integration actually usable, and adds a proper setup
guide. (The calendar module existed since v4 but its one-time authorization path
was broken/undocumented.)

## What was wrong

- The calendar module's docstring and error messages told you to run
  `python3 app.py calendar-auth`, but **that command didn't exist** in the CLI.
- The only auth path in code was `flow.run_local_server(port=0)`, which needs a
  local browser — unusable on a headless Termux/Android device.
- The config keys it reads (`calendar_client_secret_file`,
  `calendar_credentials_file`, `calendar_id`) **weren't in `DEFAULT_CONFIG`**, so
  `init-config` didn't surface them.

## What's fixed

- **New `calendar-auth` CLI command** (now real):
  ```bash
  python3 app.py calendar-auth             # browser flow (laptop/desktop)
  python3 app.py calendar-auth --headless  # copy/paste flow (Termux/Android/SSH)
  ```
  The `--headless` flow prints a URL, you approve on any browser, and paste the
  code back — no local browser needed. (You can also authorize on a laptop and
  copy the portable `token.json` to the device.)
- **`authorize()` helper** added to `calendar_client.py` supporting both flows,
  with clear errors when the client-secret file or config paths are missing.
- **Config keys added** to `DEFAULT_CONFIG`: `calendar_client_secret_file`,
  `calendar_credentials_file`, `calendar_id` (default `"primary"`), with comments
  pointing to the setup guide. (`calendar_enabled` comment corrected — it
  referenced a non-existent `gcal_token`.)
- **New doc: `docs/CALENDAR_SETUP.md`** — full walkthrough: enabling the Calendar
  API, creating OAuth Desktop credentials, the three authorization options
  (browser / headless / copy-token), enabling, how events are created, a
  troubleshooting table, and security notes. Linked from the README and the
  config reference.

## No behavior change unless you opt in

Calendar remains off by default and a complete no-op until you install the Google
libraries, authorize, and set `calendar_enabled: true`. Nothing in the
enrichment/relationship paths is affected.

## Auth model (summary)

OAuth 2.0 Desktop-app flow, `calendar.events` scope only. You download a
client-secret JSON once; `calendar-auth` exchanges it for a `token.json` that the
agent uses and auto-refreshes. No passwords stored; revoke anytime via your
Google account or by deleting `token.json`.
