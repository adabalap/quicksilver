# hg_agent — on-device AI note enrichment agent

Enriches notes in the పాదరసం (hg_ui) SQLite database using a local Gemma
model via LiteRT-LM. Runs entirely on-device.

## Quick start (Termux)

```bash
pip install setproctitle          # optional: names the process in ps
# edit config.quicksilver.json — db_path and model_path must be correct
./bin/hg_agent start
./bin/hg_agent status|logs|stop|restart
```

Config resolution order: `--config` flag → `$HG_AGENT_CONFIG` → 
`~/.config/memos_daemon/config.json`. The bin script defaults to
`config.quicksilver.json` next to app.py.

Manual run: `python3 app.py --config config.quicksilver.json run`

## Startup verification

The first log lines announce the active backend:
```
Backend: Quicksilver (SQLite) · db=/home/you/.quicksilver/notes.db
Hg agent v8 starting…
  Quicksilver DB … · mode db-poll
```
If you see "Backend: Memos (HTTP)" your config was not picked up — check the
--config path.

## What the agent does per note

status=raw or force → runs Gemma inference → writes title/summary/polished/
tags/action-items/corrections to the DB → sets status=pending (or approved
for re-enrichment) → POSTs /api/webhook so the PWA updates live → computes
note relationships and writes pairs to the relationships table.

No markdown is ever written into your note body.
