# Smoke tests

Fast sanity checks to run after **any** code or config change, before deploying
to the device. Neither test touches your real notes database — both run against
a throwaway temp DB and clean up after themselves.

## Run them

```bash
# PWA / UI backend  (27 checks)
cd ~/hg_ui        # wherever you deployed the PWA
python smoke_test.py

# Agent            (25 checks)
cd ~/hg_agent     # wherever you deployed the agent
python smoke_test.py
```

Exit code `0` = all green, `1` = something broke. Both print a coloured
pass/fail list grouped by area.

## What they cover

**PWA (`hg_ui/smoke_test.py`)**
- Health & CRUD, idempotent create, content-size cap
- Enrichment → approval, tag case-folding
- **User-edit preservation** (the data-loss guard) + adopt-body override
- On-demand polish one-shot flag
- Connections cluster count
- Pin, daily note, templates, backlinks, wiki-search, share-target, consolidation
- Static assets (index, manifest, about) + `share_target` in manifest

**Agent (`hg_agent/smoke_test.py`)**
- Client basics, `get_memo` exposing `user_edited`
- Enrichment write + tag de-dup
- Agent-side adoption path
- **User-edit guard** (nulls polished, `adopt_polished` refuses to overwrite)
- On-demand polish override, protected notes
- Decision state machine (raw→suggest, first-approval→approve_qs, force→reenrich)
- Relationships (bidirectional, deduped), error record/clear

## When a test fails

The failing line names the exact behaviour and shows what it got vs expected.
Because these mirror the load-bearing invariants (especially user-edit
preservation), a red line here almost always means a real regression — fix it
before shipping to the phone.

> Tip: the agent smoke test reuses the PWA's real schema if `hg_ui/app.py` is
> alongside `hg_agent/`. If you keep them apart, it falls back to a built-in
> minimal schema, so it still runs standalone.

## Production server

The PWA now runs on **waitress** (a production WSGI server) instead of Flask's
dev server — no more "development server" warning. Install it once:

```bash
pip install waitress
```

Then start normally: `python app.py` serves via waitress automatically. The
startup log will read *"Quicksilver PWA (production · waitress)"*. If waitress
isn't installed it falls back to the dev server with a clear warning. To force
the dev server for debugging, pass `--dev`.
