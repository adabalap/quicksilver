import json
from pathlib import Path
from .logging_setup import log
from .anchor import _now_iso

_DEFAULT_STATS = {
    "suggested": 0, "approved": 0, "reenriched": 0,
    "errors": 0, "skipped": 0, "needs_review": 0,
}


class StateStore:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data = self._load()

    def _load(self) -> dict:
        base = {
            "notes": {}, "watermark": "", "dashboard_id": "",
            "connections_id": "", "todo_id": "", "stats": dict(_DEFAULT_STATS),
            "metrics": {"last_latency_s": 0.0, "avg_latency_s": 0.0,
                        "enrich_count": 0},
        }
        if self.path.exists():
            try:
                d = json.loads(self.path.read_text())
                base.update(d)
                base.setdefault("notes", {})
                base.setdefault("connections_id", "")
                base.setdefault("todo_id", "")
                # backfill any missing stat keys
                for k, v in _DEFAULT_STATS.items():
                    base["stats"].setdefault(k, v)
                base.setdefault("metrics", {"last_latency_s": 0.0,
                                            "avg_latency_s": 0.0,
                                            "enrich_count": 0})
                return base
            except Exception:
                log.warning("State unreadable — fresh start.")
        return base

    def save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2, ensure_ascii=False))
        tmp.replace(self.path)

    # ── note records ──────────────────────────────────────────────────────────
    def get(self, nid):
        return self._data["notes"].get(nid)

    def set(self, nid, **kw):
        e = self._data["notes"].setdefault(nid, {})
        e.update(kw)
        e["updated_at"] = _now_iso()

    def note_failed(self, nid):
        e = self._data["notes"].setdefault(nid, {})
        e["failures"] = e.get("failures", 0) + 1
        e["updated_at"] = _now_iso()

    def note_succeeded(self, nid):
        e = self._data["notes"].setdefault(nid, {})
        e["failures"] = 0

    # ── stats / metrics ─────────────────────────────────────────────────────--
    def bump(self, key, n=1):
        self._data["stats"][key] = self._data["stats"].get(key, 0) + n

    def record_latency(self, seconds: float):
        m = self._data["metrics"]
        n = m.get("enrich_count", 0)
        avg = m.get("avg_latency_s", 0.0)
        m["last_latency_s"] = round(seconds, 3)
        m["avg_latency_s"] = round((avg * n + seconds) / (n + 1), 3)
        m["enrich_count"] = n + 1

    def metrics(self):
        return self._data["metrics"]

    # ── watermark / dashboards ─────────────────────────────────────────────────
    @property
    def watermark(self): return self._data["watermark"]
    @watermark.setter
    def watermark(self, v): self._data["watermark"] = v

    @property
    def dashboard_id(self): return self._data["dashboard_id"]
    @dashboard_id.setter
    def dashboard_id(self, v): self._data["dashboard_id"] = v

    @property
    def connections_id(self): return self._data["connections_id"]
    @connections_id.setter
    def connections_id(self, v): self._data["connections_id"] = v

    @property
    def todo_id(self): return self._data["todo_id"]
    @todo_id.setter
    def todo_id(self, v): self._data["todo_id"] = v

    def stats(self): return self._data["stats"]

    def all_notes(self): return self._data["notes"]
