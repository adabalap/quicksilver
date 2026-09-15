# Changelog — v6.2

Three fixes/features from run feedback: the webhook 0.0.0.0 error, a review-
queue tag, and a rebuilt Note Relationships dashboard.

## 1. Webhook "connection refused" (0.0.0.0) — fixed

**Cause:** the webhook URL registered in Memos was
`http://0.0.0.0:19283/memos-hook`. `0.0.0.0` is a valid *bind* address ("listen
on all interfaces") but is **not connectable** — Memos cannot dial it, so every
dispatch failed with `connection refused`. (Your notes still processed because
the safety poll is a reconciler; the webhook just adds low latency.)

**Fix:**
- Separated the two concepts. `webhook_host` is what the daemon LISTENS on
  (`0.0.0.0` is fine). New `webhook_advertise_url` is what Memos CONNECTS to.
- When `webhook_advertise_url` is blank, the daemon auto-derives a connectable
  URL: `0.0.0.0`/`localhost` → `127.0.0.1`, otherwise the bind host.
- At startup the daemon now prints the exact URL to paste into Memos and warns
  if it would contain `0.0.0.0`.

**Your action:** in Memos → Settings → Webhooks, change the URL to
`http://127.0.0.1:19283/memos-hook` (same host). For Memos on another
machine/container, set `webhook_advertise_url` to the reachable LAN IP.

## 2. Review-queue tag: `#pending-approval`

Every AI suggestion is now tagged **`#pending-approval`**, and the tag is removed
automatically when you approve (approved notes don't carry it). Create a Memos
**Shortcut** with filter `tag:pending-approval` for a one-click review queue of
everything waiting on you.

Design note: this replaces the old `#needs-review` tag. There is now ONE queue
tag for all suggestions (every suggestion is, by definition, awaiting your
approval). Low-confidence suggestions still get a visible cue — the approve box
reads "Review & approve (low confidence)" — but they share the single queue tag
rather than fragmenting it.

## 3. Note Relationships dashboard — rebuilt

The old version listed flat bullet groups labeled with bare keywords, with no
indication of why notes were grouped or how strongly. Rebuilt to be meaningful:

- **Theme heading** per group from the shared distinctive terms.
- **"Grouped because they share: …"** — the actual overlapping terms that caused
  the grouping, so the criteria is transparent.
- **Strength meter** (●●●○○) and a word (strong/moderate/loose) from the group's
  average pairwise similarity — tells a tight cluster from a loose chain.
- **⭐ anchor note** — the most central note leads each group; the rest are
  ordered by similarity to it.
- **Collapsible groups** (`<details>`) so the dashboard scaffolds cleanly and
  expands on demand instead of being a wall of bullets.
- **"How groups are formed"** explainer so the method (keyword/TF-IDF overlap,
  not semantic) is no longer a mystery.

### What the grouping criteria actually is
The agent tokenizes each note's human text, weights terms by how distinctive
they are across all your notes (TF-IDF), and measures overlap with cosine
similarity. Notes above `cluster_threshold` are linked; transitively linked
notes form a group. It is keyword-based, not semantic: "car" and "automobile"
won't link, but "agent link bug" and "agent 404 bug" will. To group more
aggressively, lower `cluster_threshold`; to require tighter groups, raise it.

## New config keys

```
webhook_advertise_url   ""               # blank = auto-derive connectable URL
review_tag              "pending-approval"
```
