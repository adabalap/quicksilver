# Running the agent persistently (IST logs, auto-restart, boot)

## 1. Set the proot timezone to IST (run INSIDE the proot)
```bash
ln -sf /usr/share/zoneinfo/Asia/Kolkata /etc/localtime
echo "Asia/Kolkata" > /etc/timezone
date     # should now show IST
```
The daemon's own log is forced to IST in code regardless, but this makes the
whole proot (and `date`, cron, etc.) consistent.

## 2. The agent's log file (inside the proot)
The daemon logs to the path in config `log_file`, default:
    /root/.cache/memos_daemon/daemon.log
Every line is timestamped in IST and ends with " IST", e.g.:
    2026-06-22 18:27:22,378 IST [INFO] webhook listening on ...
Tail it with:
    tail -f /root/.cache/memos_daemon/daemon.log

## 3. Run as a runit service (auto-restarts on crash) — run in TERMUX
runit is the same supervisor that ran Ollama. These steps register the agent.

```bash
# create the service directory
mkdir -p $PREFIX/var/service/memos-agent/log

# copy the two run scripts in (from wherever you unzipped them)
cp svc/run            $PREFIX/var/service/memos-agent/run
cp svc/log/run        $PREFIX/var/service/memos-agent/log/run
chmod +x $PREFIX/var/service/memos-agent/run $PREFIX/var/service/memos-agent/log/run

# runsvdir auto-detects the new service within ~5s and starts it.
# Check status:
sv status memos-agent

# Control it:
sv down memos-agent     # stop
sv up   memos-agent     # start
sv restart memos-agent  # restart
```

The agent's console output is mirrored (with svlogd timestamps) to:
    ~/.var-log/memos-agent/current
The authoritative IST log remains the in-proot daemon.log from step 2.

### IMPORTANT prerequisites for the service
- Memos must be reachable when the agent starts, or it exits and runit will
  restart it in a loop until Memos is up. Either start Memos first, or rely on
  the restart loop catching up once Memos is running. (start-stack.sh handles
  ordering for the manual path.)
- Adjust PROOT_DISTRO ("ubuntu") and AGENT_DIR ("/root/memos") at the top of
  `run` if yours differ. Check distro name with: proot-distro list

## 4. Start on boot
Install the Termux:Boot app (F-Droid). Then:
```bash
mkdir -p ~/.termux/boot
cat > ~/.termux/boot/10-stack.sh <<'EOF'
#!/data/data/com.termux/files/usr/bin/sh
termux-wake-lock
# runit services (incl. memos-agent) start automatically with runsvdir,
# but ensure Memos server is up too:
sv up memos-agent 2>/dev/null
EOF
chmod +x ~/.termux/boot/10-stack.sh
```
If you run Memos itself under runit too, it will also auto-start. Otherwise add
your Memos start command to this boot script before the agent.

## 5. Stop / remove the service (mirror of how Ollama was removed)
```bash
sv down memos-agent
rm -rf $PREFIX/var/service/memos-agent
```
