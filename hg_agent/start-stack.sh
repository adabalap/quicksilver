#!/data/data/com.termux/files/usr/bin/bash
# start-stack.sh — bring up the Memos server (Termux) and the AI agent (proot),
# in the correct order, with logging for each.
#
# Run this from a TERMUX shell (not inside the proot):
#     bash ~/start-stack.sh
# or alias it:  alias boot-stack='bash ~/start-stack.sh'
#
# It is idempotent-ish: it will not start a second Memos or agent if one is
# already running (basic pgrep guard). Re-running brings up whatever is down.

set -u

# ── Config — adjust these to your paths ──────────────────────────────────────
MEMOS_DIR="$HOME/sandbox/memos"
MEMOS_BIN="./memos"
MEMOS_PORT=5230
MEMOS_DATA="$HOME/.memos"
MEMOS_LOG="$HOME/.memos/memos.log"

PROOT_DISTRO="ubuntu"          # name shown by: proot-distro list
AGENT_DIR_IN_PROOT="/root/memos"   # where app.py lives INSIDE the proot
AGENT_LOG_IN_PROOT="/root/.cache/memos_daemon/daemon.log"  # log path INSIDE proot
# ─────────────────────────────────────────────────────────────────────────────

log() { echo "[start-stack $(date '+%H:%M:%S')] $*"; }

# 1) Acquire a wake lock so Android doesn't suspend the processes.
#    (Harmless if termux-wake-lock isn't available; the user may also tap
#     "Acquire wakelock" in the Termux notification.)
if command -v termux-wake-lock >/dev/null 2>&1; then
    termux-wake-lock && log "wake lock acquired"
else
    log "termux-wake-lock not found — acquire it from the Termux notification"
fi

# 2) Start Memos in Termux if not already running.
if pgrep -f "$MEMOS_BIN --port $MEMOS_PORT" >/dev/null 2>&1; then
    log "Memos already running."
else
    mkdir -p "$MEMOS_DATA"
    ( cd "$MEMOS_DIR" && nohup "$MEMOS_BIN" --port "$MEMOS_PORT" \
        --data "$MEMOS_DATA" --log-level info \
        >> "$MEMOS_LOG" 2>&1 </dev/null & )
    log "Memos starting (log: $MEMOS_LOG)"
fi

# 3) Wait for Memos to be reachable before launching the agent, so the agent's
#    startup ping succeeds. Up to ~30s.
log "waiting for Memos on port $MEMOS_PORT…"
ready=0
for i in $(seq 1 30); do
    if (command -v curl >/dev/null 2>&1 && \
        curl -sf "http://127.0.0.1:$MEMOS_PORT/api/v1/memos?pageSize=1" >/dev/null 2>&1) \
       || (command -v nc >/dev/null 2>&1 && nc -z 127.0.0.1 "$MEMOS_PORT" 2>/dev/null); then
        ready=1; break
    fi
    sleep 1
done
if [ "$ready" -eq 1 ]; then
    log "Memos is up."
else
    log "WARNING: Memos not reachable after 30s — starting agent anyway."
fi

# 4) Start the agent INSIDE the proot if not already running.
#    We check from Termux by grepping the proot's process list is unreliable,
#    so we let the agent's own single-instance behaviour and the runit/manual
#    guard handle dupes; here we do a best-effort pgrep on the host namespace.
if pgrep -f "app.py run" >/dev/null 2>&1; then
    log "Agent already running."
else
    log "starting agent in proot '$PROOT_DISTRO' (log: $AGENT_LOG_IN_PROOT)"
    # Launch through proot-distro login, running app.py and appending to the
    # in-proot log. nohup + & so it survives this script exiting.
    nohup proot-distro login "$PROOT_DISTRO" -- bash -lc \
        "mkdir -p \$(dirname $AGENT_LOG_IN_PROOT); cd $AGENT_DIR_IN_PROOT && exec python3 app.py run >> $AGENT_LOG_IN_PROOT 2>&1" \
        >> "$HOME/.agent-launch.log" 2>&1 </dev/null &
    log "agent launch issued."
fi

log "done. Memos log: $MEMOS_LOG ; agent log (inside proot): $AGENT_LOG_IN_PROOT"
