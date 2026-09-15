# Changelog — v6.9

Fixes found while testing calendar event creation in the field.

## 1. Webhook responses now valid JSON (fixes Memos "unexpected end of JSON input")

The webhook handler acknowledged events with a `200` but an **empty body**.
Memos' webhook client parses the response as JSON, so an empty body made it log
`unexpected end of JSON input` and treat every dispatch as failed — even though
the agent received and queued the event correctly. The handler now returns a
valid `{}` JSON body with proper `Content-Type`/`Content-Length`. (This was
cosmetic for the agent — processing always worked via the queue — but it spammed
Memos' log and could make Memos consider the webhook unhealthy.)

## 2. Calendar failures can no longer disturb note finalization

Event creation is a non-critical side effect that runs after a note is already
finalized. The call is now wrapped so that any calendar error (API disabled,
quota, network) is logged and skipped — the note stays finalized regardless.
`_create_event` already caught its own API errors; this adds a belt-and-suspenders
guard around the whole calendar step at the call site.

## 3. Docs: enabling the Calendar API is now called out explicitly

A common first-event failure is `accessNotConfigured` — "Calendar API has not
been used in project … or it is disabled." Enabling the **API** is a separate
step from creating **credentials**, and easy to miss. `CALENDAR_SETUP.md` now
flags this step prominently and adds a troubleshooting row with the fix (open the
link in the error, click Enable, wait ~2 min, re-approve the note).

## Field note

Calendar auth confirmed working end-to-end in testing: the agent authenticated,
extracted a dated reminder, and attempted the event insert. The only blocker was
the Calendar API not being enabled on the project — a one-click Google Console
fix, not a code issue.

No new config keys.
