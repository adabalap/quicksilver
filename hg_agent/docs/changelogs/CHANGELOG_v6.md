# Changelog — v6

Builds on v5 (which fixed the original/enriched separation, the thermal crash,
and added correlation). v6 is a correctness + design refinement release driven
by real run feedback.

## Bug fix

- **Related-note links 404'd.** Links used the `/m/<uid>` short path, which
  404s on this Memos instance; the working route is `/memos/<uid>`. Fixed, and
  the path is now configurable via `note_url_template` (default
  `{base}/memos/{uid}`) so other deployments can adjust it without code changes.

## Note layout (refined)

New section order, in both suggested and approved notes:

```
### Title
**Summary:** …
**Action items:**
- [ ] …
**Refined:** <enriched body>      ← review only; approved notes show the body
                                     with no subtitle (it simply IS the note)
**Tags:** #a #b
**Priority:** 🔴 high             ← only when high
**Related:** [note](…)
```

- **Confidence is no longer displayed.** Self-reported LLM confidence is poorly
  calibrated as a user-facing number and invites false trust. It is retained as
  an *internal* signal only: low-confidence enrichments still get the
  `#needs-review` tag. (`confidence_review_threshold` unchanged.)
- **Priority shows only when high** (🔴). Low/medium priority is the absence of
  a signal and was just noise on routine notes.
- **"Refined" subtitle** introduces the enriched body while a note is under
  review. Configurable via `refined_label`.

## Dashboards (renamed + tag-based access)

- **"AI Daemon Dashboard" → "🤖 AI Agent Dashboard".**
- **"AI Connections" → "🔗 Note Relationships".**
- Both maintained notes now carry a tag (`#agent-dashboard`,
  `#note-relationships`) at the bottom. **Pinning is now optional and OFF by
  default** (`pin_maintained_notes: false`) — use Memos **Shortcuts** instead
  (see setup below). Tag-based access is cleaner, survives note recreation, and
  doesn't clutter the pinned area.
- Cluster labels in Note Relationships now prefer *distinctive* shared terms
  (TF-IDF weighted) rather than raw frequency, so labels are more meaningful.

## Setup: one-time Memos shortcuts (replaces pinning)

In Memos, create two shortcuts (Settings → Shortcuts, or the shortcut UI):

- **Agent Dashboard** → filter: `tag:agent-dashboard`
- **Note Relationships** → filter: `tag:note-relationships`
- (optional) **Needs Review** → filter: `tag:needs-review`

Each becomes a one-click saved view. Because they match by tag, they keep
working even if the agent recreates a maintained note with a new ID.

## New / changed config keys

```
note_url_template      "{base}/memos/{uid}"
dashboard_title        "🤖 AI Agent Dashboard"
dashboard_tag          "agent-dashboard"
connections_title      "🔗 Note Relationships"
connections_tag        "note-relationships"
pin_maintained_notes   false
refined_label          "Refined"
```
