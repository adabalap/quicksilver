# Captures: Medha's notification stream as a Quicksilver source

For Medha **v0.20.0** and later. Both trees stay fully working against an
older Medha — the source reports `unsupported` and the brief keeps posting the
v1 shape.

---

## What this is for

Medha can see every notification the phone shows and has no memory to reason
about it. Quicksilver has the corpus, the embeddings and the correlation, and
could not see the stream at all. So Medha was guessing — a `kind` from the
package name, a `dueAt` scraped out of an email subject — and those guesses
arrived on the home screen as facts. One real evening: four of five agenda
rows were emails, one of them *"Re: Delayed employees for 11:30 on
17/11/2026"*, read as an obligation due tomorrow from a date already past.

Medha v0.20.0 stops interpreting and reports instead.

**Medha is sensors, model and surface. Quicksilver is memory and judgement.**

---

## The decision worth your veto: captures do **not** become brief rows

The obvious thing to do with a notification stream is put it on the brief.
That is exactly the state the triage rule was written to end — 19 items, 17
undated, a ₹2,50,000 instalment sharing a scroll with a shopping list and
"Thanq thanq". Pouring the stream in would undo the definition the brief just
acquired.

So captures are used the other way round: as **corroboration** for
obligations Quicksilver already knows about from your own notes and bills.
That cannot add noise, because it never adds a row. It can only change what we
say about a row that was going to be there anyway.

The case it exists for is the one from your screenshots: a Teams or Outlook
notification saying a meeting is cancelled, matched to the reminder we were
already posting, so the row goes out with `state: "cancelled"` instead of
sitting at the cancelled meeting's hour.

Three properties make this safe by construction:

- **It only ever demotes.** Medha enforces the same rule on `state`, so
  neither side can use it to force something onto the home screen. A false
  match moves a row from the agenda to the full brief, one tap away. A false
  negative leaves things exactly as they were before captures existed.
- **Two shared words minimum.** A ratio alone is not identification: a
  candidate titled "Rent" has one token, so any notification mentioning rent
  would score 1.0 against it. A single-word title therefore can never be
  corroborated, which is the safe failure.
- **A 72-hour window.** A cancellation is news. A fortnight-old notice about
  something still on your list is likelier to be a different meeting than a
  late-arriving cancellation.

If you want captures to become rows later, the honest way in is the *state*
the agent can now assert, not the stream itself.

---

## hg_agent

### New: `memos_daemon/captures.py`

The table, the retention, the noise rule and the corroboration matcher.

- `captures` table, `UNIQUE` on Medha's row id. That constraint is the whole
  dedupe strategy and it has to be in the schema: the cursor is saved only
  *after* the rows commit, so a crash between the two replays a page.
- 21 days / 20,000 rows, whichever bites first. A little past Medha's own 14
  so a gap in polling cannot lose a week.
- `is_noise()` removes the **useless, not the sensitive** — Medha's filter has
  already dropped anything passcode-shaped, and a second copy of that rule
  here is a second copy that can disagree with it.
- `fingerprint()` collapses a ticking countdown ("in 10 minutes", "in 9
  minutes") to one thing by matching on the text's *shape* rather than value.

### New: `memos_daemon/parts/captures_source.py`

The poll, modelled on `sms_source.py` — minutely tick, a user switch that
starts off, a watermark, a thermal check, a status record for the UI.

The one thing that differs is cursor discipline: the watermark goes in the
**same transaction** as the rows. Committing the rows and then the cursor
separately leaves a window where a crash loses a page; committing the cursor
first loses it for certain.

Medha's refusals each name the thing to do about them:

| Status | Means |
|---|---|
| `no_capability` | 403 — tick **Read your notifications** on this client in the Medha app |
| `unsupported` | 404 — this Medha predates v0.20.0 |
| `not_listening` | Medha isn't recording at all. An empty stream and a deaf phone look identical otherwise, and an agent that cannot tell them apart reports a clear day |

### `memos_daemon/medha_brief.py`

- `put_obligations()`, `captures()`, `drain_captures()`, `protocol_of()`
- `_obligation()` — the v2 mapping. `id` from the candidate key (so a
  dismissal survives a reword and a reschedule), `due.provenance`, `state`,
  `links[]`
- `pick(..., protocol=2)` and `reconcile(..., protocol=2)` — same ranking and
  same crowd-out protection, different shape at the last step only
- `_pointer()` — the "+N more" row, now built through the same shape function
  as a real row. It used to be assembled inline with a `link` key; on v2 Medha
  reads `links` and ignores what it doesn't know, so the one row whose entire
  purpose is to be tapped would have lost its target, silently, with the post
  still succeeding

**On provenance:** everything hg_agent posted before captures is genuinely
`declared`, and that is not laziness. `sms.bill()` rejects a bill outright if
no date is found in its own message (`no_due_date`), and a reminder's date
comes from the note you wrote. Capture-derived dates are the ones marked
`inferred`, which is exactly the signal that failed in v0.19.1.

**`MAX_ITEMS` left at 5.** I raised it to 20 and then reverted: in your tree
it is only `pick()`'s default, and `brief_source` always passes
`medha_brief_max_items` (50), so it never trimmed a real post. Changing it
broke three of your tests for no benefit. Medha's *own* sample clients were a
different story — see below.

### Config (documented, `docs/CONFIGURATION.md` regenerated)

```
captures_enabled           true    # available; still off until switched on
captures_poll_seconds      300
captures_max_pages         20      # bound on one drain
captures_corroborate_hours 72
```

### `smoke_test.py`

Three checks added: the mixin is on the daemon, the poll runs from the tick,
and it runs **before** the brief — otherwise a notice that arrived this minute
corroborates nothing until the next one.

### New: `captures_test.py` — 96 checks

The ones that matter are about loss, because this is the only source with a
cursor the server will not replay: the cursor saved after the rows, a replayed
page deduping, a page of pure noise still advancing the cursor (or a phone
posting progress bars re-reads the same page forever), and `listening: false`
reported rather than read as a quiet phone.

---

## hg_ui

- **New `server/captures.py`** — `GET /api/captures` (switch, status, per-app
  counts, optional sample), `POST /api/captures/scan`,
  `POST /api/captures/forget`. Read-only over the agent's table; it never
  creates the schema, because a UI that creates another component's schema can
  create the wrong version of it.
- **`captures` pref**, default `off`.
- **Settings → Phone notifications** — the switch, a status line, and three
  buttons: Check now, **See what was kept**, Forget everything. The sample
  exists because a sentence about what is collected is a claim, and this is the
  one source reading data you did not hand over; twenty rows lets the claim be
  checked rather than believed.
- `forget` clears the cursor **with** the rows. Keeping it would make "forget"
  quietly mean "forget and never collect that window again".
- `structure_test.py`'s route-count tripwire bumped 95 → 98, with a note that
  it is a tripwire rather than a fact about the design.

---

## Two things to do on your side

1. **Tick "Read your notifications"** for Quicksilver's client in the Medha
   app (API clients → the client → capabilities). It is the only capability an
   app cannot request through a consent dialog, by design: `notify` announces
   itself by writing to your shade, and a client *reading* the stream looks
   identical to one that never asked. You will get a confirmation stating the
   scope and the 14-day retention the first time.
2. **Switch it on** in Quicksilver → Settings → Phone notifications.

Neither alone does anything.

---

## Two pre-existing failures, confirmed against your untouched export

Neither is mine — both fail identically on a clean extraction of the zips.

1. **`hg_agent/sms_test.py` — "there is data before forgetting".** The
   Forgetting section scans a fake inbox whose bill due dates are
   2026-11-04…2026-11-12, but the daemon hook runs against the real
   `datetime.now()`. Every one of those dates is now in the past, so the scan
   creates 3 bills and **0 reminders**, and the check wants at least one
   `source='user'` reminder. It would have started failing around 13 November.
   The fix is to pin the clock in that section the way the parser tests pin
   `NOW`; I have left it alone rather than change a test expectation in your
   repo without asking.
2. **`hg_agent/onnx_embed_test.py`** — `ModuleNotFoundError: onnx`. Just a
   missing package in my container.

---

## For reference: a bug this found in Medha's own clients

`samples/medha_brief.py` and `samples/medha-brief.js` — the files every local
client and PWA copies — kept `MAX_ITEMS = 5` after
`BriefPolicy.MAX_ITEMS_PER_SECTION` was raised to 20, and *those* use it
unconditionally. So every post was trimmed to five client-side and came back a
success. The tests missed it because they asserted "capped at five" against
literals, so they kept passing for the wrong reason after the cap moved.

Fixed in Medha v0.20.0, fixtures now derive from the constant, and
`tools/check_mirrored_caps.py` fails CI if any mirrored limit drifts again.
Your copy's numbers **are** mirrored, so check them against
`docs/BRIEF-API.md`'s limits table when you upgrade Medha.
