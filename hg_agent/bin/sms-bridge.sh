#!/data/data/com.termux/files/usr/bin/bash
# Keeps a JSON copy of your recent text messages where the agent can read it.
#
# Run this in Termux itself, NOT inside Fedora/Debian. Termux:API only works in Termux.
#   pkg install termux-api            (and allow SMS for the Termux:API app)
#   termux-wake-lock
#   nohup bash sms-bridge.sh ~/quicksilver-sms.json >/dev/null 2>&1 &
#
# Or run it from cron instead of a loop (one pass per call, then exit):
#   pkg install cronie termux-services     then   sv-enable crond      (restart Termux once)
#   crontab -e   →   */5 * * * * bash ~/sms-bridge.sh ~/quicksilver-sms.json 0
#
# Then point the agent at the same file in config.quicksilver.json:  "sms_file": "<path as the agent sees it>"
#
# Nothing here leaves the phone: it only writes one local file. The agent keeps the text of bill messages only (on their reminders, until paid); everything else is discarded after parsing.
OUT="${1:-$HOME/quicksilver-sms.json}"
EVERY="${2:-300}"
mkdir -p "$(dirname "$OUT")"
while true; do
  if termux-sms-list -t inbox -l 300 > "$OUT.tmp" 2> "$OUT.err.tmp" && [ -s "$OUT.tmp" ]; then
    mv "$OUT.tmp" "$OUT"; rm -f "$OUT.err" "$OUT.err.tmp"
  else
    mv -f "$OUT.err.tmp" "$OUT.err" 2>/dev/null; rm -f "$OUT.tmp"
  fi
  [ "$EVERY" = "0" ] && break      # cron mode: one pass, then exit
  sleep "$EVERY"
done
