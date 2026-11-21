"""pending.py — durable queue of completed-but-unwritten enrichments.

When Memos is briefly down, the agent may finish a 30s+ inference and then fail
to PATCH the result back. Throwing that away is wasteful and means the user
never sees the enrichment. Instead we persist the finished content here and
re-apply it once Memos is reachable again.

Each entry is keyed by note name and holds everything needed to complete the
write and update in-memory state afterwards:

    {
      "name": "memos/abc",
      "content": "<the full rendered note body+anchor>",
      "status": "suggested" | "approved",
      "hash": "<text_hash of the human text>",
      "kind": "note" | "dashboard" | "connections",
      "state_attr": "dashboard_id" | "connections_id" | "",
      "ts": "<iso>"
    }

Last-write-wins per note: a newer enrichment for the same note replaces an older
pending one. The store is small (only notes pending during an outage) and is
flushed opportunistically by the daemon.
"""

import json
from pathlib import Path
from .anchor import _now_iso
from .logging_setup import log


class PendingWrites:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict[str, dict] = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text())
            except Exception:
                log.warning("pending-writes file unreadable — starting empty.")
        return {}

    def _save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2, ensure_ascii=False))
        tmp.replace(self.path)

    def add(self, name: str, content: str, *, status: str = "", hash: str = "",
            kind: str = "note", state_attr: str = ""):
        self._data[name] = {
            "name": name, "content": content, "status": status,
            "hash": hash, "kind": kind, "state_attr": state_attr,
            "ts": _now_iso(),
        }
        self._save()
        log.info(f"  💾 enrichment for {name} saved to retry when Memos returns "
                 f"({len(self._data)} pending)")

    def discard(self, name: str):
        if name in self._data:
            del self._data[name]
            self._save()

    def discard_permanent(self, name: str, reason: str):
        """Discard a pending entry that failed permanently (e.g. 404, too long).
        Unlike discard(), this logs a clear explanation so operators understand
        why the entry was dropped rather than silently disappearing."""
        if name in self._data:
            del self._data[name]
            self._save()
            log.warning(f"  🗑 dropped pending write for {name}: {reason} "
                        f"({len(self._data)} remaining)")

    def items(self) -> list[dict]:
        return list(self._data.values())

    def __len__(self):
        return len(self._data)

    def __contains__(self, name):
        return name in self._data
