# Operations Runbook

How to run the agent persistently, watch it, and fix it when something looks
wrong. The reference deployment is **Android (Termux + proot)** with Memos and
the model running locally, but the principles apply anywhere.

---

## Running persistently

### Logs
The agent logs to `log_file` (default `~/.cache/memos_daemon/daemon.log`), every
line timestamped. Tail it:
```bash
tail -f ~/.cache/memos_daemon/daemon.log
```
Healthy steady-state looks like periodic heartbeats:
```
heartbeat — idle, webhook live, SoC 33°C
```
and, when a note arrives, a clean pipeline trace:
```
▸ processing memos/abc
  decision: suggest
  running inference (suggest) on memos/abc…
  inference returned in 34.2s
  🔗 2 related note(s) linked
  → title='…' tags=[…] priority=… conf=0.90
  ✓ suggested: memos/abc
▸ done memos/abc
```

### As a supervised service (auto-restart)
On Termux, runit supervises the agent so it restarts on crash:
```bash
mkdir -p $PREFIX/var/service/memos-agent/log
cp svc/run     $PREFIX/var/service/memos-agent/run
cp svc/log/run $PREFIX/var/service/memos-agent/log/run
chmod +x $PREFIX/var/service/memos-agent/run $PREFIX/var/service/memos-agent/log/run

sv status  memos-agent
sv restart memos-agent
sv down    memos-agent   # stop
sv up      memos-agent   # start
```
**Prerequisite:** Memos must be reachable when the agent starts, or it exits and
runit restart-loops until Memos is up. Start Memos first (or let the loop catch
up). `start-stack.sh` handles ordering for the manual path.

### On boot
Install Termux:Boot, then drop a startup script in `~/.termux/boot/` that takes a
wake-lock and brings up the stack (Memos, then the agent). See
[`RUN_PERSISTENT.md`](RUN_PERSISTENT.md) for the exact boot script.

### Timezone
The agent's own log and note timestamps render in `display_timezone` regardless,
but for a consistent environment set the proot timezone too:
```bash
ln -sf /usr/share/zoneinfo/Asia/Kolkata /etc/localtime
echo "Asia/Kolkata" > /etc/timezone
```

---

## Monitoring

### Quick health check
```bash
python3 app.py status
```
Shows: activity stats, performance metrics (last/avg inference latency, count),
the watermark, the two dashboard note IDs, **pending writes** count, Memos API
reachability, and SoC temperature.

### The in-Memos dashboard
Open your `tag:agent-dashboard` shortcut. It shows activity counts with bars, a
performance panel (latency, temperature), a health indicator, and an "awaiting
your approval" note. It refreshes as the agent works.

### What to watch during the data-collection phase
- **Avg inference latency** — is it stable around 30–45s, or creeping up
  (thermal throttling)?
- **Errors / pending writes** — are writes failing (Memos flaky)?
- **Needs-review rate** — how often is the model low-confidence? High rates may
  mean the notes are unusually messy or the model is struggling.
- **Relationship quality** — are the groups in `tag:note-relationships`
  meaningful, or too loose/too tight? Tune the thresholds if needed.

---

## Troubleshooting

### Nothing happens after I add a note
1. Check the log: do you see `⟶ queued …`? If not, the webhook isn't reaching
   the agent and the poll hasn't run yet.
2. Verify the webhook URL in Memos is **connectable** — `127.0.0.1`, not
   `0.0.0.0`. The agent prints the correct URL at startup (`➜ In Memos, set the
   webhook URL to: …`).
3. Even with a broken webhook, the safety poll should pick it up within
   `safety_poll_seconds` (default 10 min). If it never does, check Memos API
   reachability with `python3 app.py status`.

### The worker seems stuck / silent after "queued"
Each pipeline step logs a breadcrumb. If it stops at `running inference …`, the
model call is slow or hung — confirm the model loads in isolation:
```bash
echo "test note" | python3 app.py enrich
```
If that returns JSON, the model is fine. (First load can take ~30s; subsequent
ones are faster due to OS file caching.)

### "Connection refused" errors in the log
Memos was briefly down/restarting. The agent retries writes with backoff and, if
the outage outlasts the retries, **persists the completed enrichment** and
re-applies it when Memos returns — check `pending writes` in `status`. No work is
lost. If it's constant, Memos isn't running or `memos_base_url` is wrong.

### A dashboard shows up in my review filter
It shouldn't (fixed in v6.2) — the dashboards must never emit a live
`#pending-approval` tag. If you see it, you're likely on an older build.

### Re-processing an approved note mixes in old text
Fixed in v6.1 via the `:note` markers — re-processing uses the current body as
the source of truth. Older approved notes (pre-marker) use a fallback that
extracts just the body; the first reprocess upgrades them to the new format.

### The device gets hot / throughput drops
Expected under sustained inference. The thermal guard pauses above
`thermal_hot_celsius` and resumes below `thermal_resume_celsius`. If it pauses
too eagerly or not enough, tune those. Watch SoC temp in `status`.

### Reset everything
```bash
python3 app.py reset          # clears state.json (re-discovers notes via anchors)
```
The agent will re-recognize its own already-enriched notes from their hidden
anchors, so a reset is safe — it won't redo finished work.

---

## Common tuning

| You want… | Change |
|---|---|
| Faster reaction without webhook | lower `poll_interval_seconds` (costs battery) |
| Looser/tighter note groups | adjust `*_threshold` (lexical) or `embed_*_threshold` (semantic) |
| Fewer notes flagged for review | lower `confidence_review_threshold` (less caution) |
| More/fewer related links per note | `related_top_n` |
| Calendar events from notes | set `calendar_enabled: true` + configure gcal |
| Less aggressive thermal pausing | raise `thermal_hot_celsius` (carefully) |

---

## Future toggles (known, not yet enabled by default)

These are deliberately off pending the data-collection phase:

- **Semantic embeddings.** `embed_model` defaults to `all-MiniLM-L6-v2`, but it
  only activates if `sentence-transformers` is installed. On a RAM-limited phone,
  that pulls in PyTorch (~1–2GB) — weigh it against the value of meaning-based
  grouping. Lighter alternatives (ONNX runtime, or reusing the Gemma stack to
  produce embeddings) are designed-for but not yet wired; the `Embedder`
  interface is the seam they'll drop into. Until then the agent runs lexical and
  says so at startup.
- **Google Calendar.** Off until `gcal_token` is configured.
- **Shared inference service.** The long-term plan: one resident model behind a
  local endpoint serving this agent and future ones — the right home for an
  embedding model on a constrained device.

The current data-collection run is meant to inform which of these to turn on
next, based on real memory headroom, latency, and how useful the relationship
map proves to be.
