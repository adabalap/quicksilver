#!/usr/bin/env python3
"""Render docs/CONFIGURATION.md from config.py defaults + tools/config_docs_data.py.
Run:  python3 tools/gen_config_doc.py          (writes the file)
      python3 tools/gen_config_doc.py --check  (exit 1 if a key is undocumented/stale, or the file is out of date)"""
import json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT); sys.path.insert(0, HERE)
from memos_daemon.config import DEFAULT_CONFIG
from pathlib import Path
from config_docs_data import GROUPS, D

def shown(k, v):
    if isinstance(v, str) and v.startswith(str(Path.home())):
        v = "~" + v[len(str(Path.home())):]
    return json.dumps(v, ensure_ascii=False)

def problems():
    grouped = [k for _, ks in GROUPS for k in ks]
    out = []
    for k in DEFAULT_CONFIG:
        if k not in D: out.append(f"undocumented key: {k}")
        if k not in grouped: out.append(f"key not in any group: {k}")
    for k in D:
        if k not in DEFAULT_CONFIG: out.append(f"documented but not in config.py: {k}")
    for k in grouped:
        if grouped.count(k) > 1: out.append(f"key in two groups: {k}")
    return sorted(set(out))

HEAD = '''# Configuration reference

Every setting the agent understands, with its default, what it does and an example.
**This file is generated** from `memos_daemon/config.py` and `tools/config_docs_data.py`
(`python3 tools/gen_config_doc.py`); a test fails if a setting is added without a description.

## How to set things

Put only the values you want to change in a JSON file. Anything you leave out keeps its default.

```json
{
  "db_path": "/root/.quicksilver/notes.db",
  "engine": "auto",
  "medha_url": "http://127.0.0.1:8001",
  "medha_token": "mk_live_xxxxxxxx",
  "medha_brief_link": "http://127.0.0.1:5230",
  "media_audio_lang": "te",
  "display_timezone": "Asia/Kolkata"
}
```

The file is found in this order: `--config <path>`, the `HG_AGENT_CONFIG` environment variable, then
`~/.quicksilver/config.quicksilver.json`. A path you name that does not exist, or a file that does not parse, stops the
agent with a clear error instead of silently running on defaults. **Restart the agent after any change.**
Environment shortcuts: `MEMOS_DAEMON_MODEL_PATH`, `MEMOS_DAEMON_DRY_RUN` (and the legacy Memos ones).

Settings you change **in the app** (Settings screen) are separate and are listed in the hg_ui README. They are stored in
the database and take effect at once.

## Recipes

| Goal | Set |
|---|---|
| Save battery and heat | `"embeddings_enabled": false`, `"medha_brief_ai": false`, `"main_loop_tick_seconds": 15`, `"thermal_hot_celsius": 65.0` |
| Snappier reaction to typing | `"enrich_quiet_seconds": 20` |
| A calmer, shorter Now brief | `"medha_brief_reminder_ahead_days": 3`, `"medha_brief_actions_max": 1`, `"medha_brief_ai": false` |
| Told the day before a bill is due | `"medha_brief_notify_days": 1` |
| Keep amounts off the lock screen | `"medha_brief_amounts": false` |
| Telugu voice notes | `"media_audio_lang": "te"`, `"media_audio_prompt": "your names and terms"` |
| Always keep your exact words | `"enrich_polish_by_default": false` |
| Fix typos in short prose too | `"enrich_quick_note_chars": 60` |
| Bills inside Fedora/proot | run `bin/sms-bridge.sh` in native Termux and set `"sms_file": "/path/to/sms.json"` |
| Search and links keyword-only | `"embeddings_enabled": false` |

'''

def render():
    s = [HEAD]
    for title, keys in GROUPS:
        s.append(f"## {title}\n")
        s.append("| Setting | Default | What it does | Example |\n|---|---|---|---|")
        for k in keys:
            what, ex = D[k]
            s.append(f"| `{k}` | `{shown(k, DEFAULT_CONFIG[k])}` | {what.replace('|', '\\|')} | {ex.replace('|', '\\|')} |")
        s.append("")
    return "\n".join(s)

if __name__ == "__main__":
    p = problems()
    if p:
        print("\n".join(p)); sys.exit(1)
    out = os.path.join(ROOT, "docs", "CONFIGURATION.md")
    text = render()
    if "--check" in sys.argv:
        ok = os.path.exists(out) and open(out).read() == text
        print("up to date" if ok else "docs/CONFIGURATION.md is out of date: run tools/gen_config_doc.py"); sys.exit(0 if ok else 1)
    open(out, "w").write(text); print("wrote", out, len(DEFAULT_CONFIG), "settings")
