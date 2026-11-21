"""todo_store.py — persistent index of action items across approved notes.

Stores a flat list of TODO items extracted from approved notes, keyed by
(note_name, item_text). The index is persisted to a JSON file alongside the
state file and rebuilt incrementally on each approval.

Data structure (JSON):
{
  "items": {
    "<note_name>|||<item_text>": {
      "note_name":  "memos/abc123",
      "note_title": "Buy groceries",
      "note_url":   "http://localhost:5230/memos/abc123",
      "text":       "Call dentist",
      "tags":       ["health"],
      "done":       false,
      "done_at":    null,
      "added_at":   "2026-06-30T10:00:00+00:00"
    }
  }
}

Keying by (note_name + item_text) is collision-resistant in practice: the same
note never repeats the same exact item text. Using a composite string key avoids
nested dicts and makes JSON diffs easy to read.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from .logging_setup import log


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _item_key(note_name: str, text: str) -> str:
    return f"{note_name}|||{text}"


class TodoStore:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict = self._load()

    def _load(self) -> dict:
        base: dict = {"items": {}}
        if self.path.exists():
            try:
                d = json.loads(self.path.read_text())
                base.update(d)
                base.setdefault("items", {})
            except Exception:
                log.warning("todo_store: unreadable — fresh start.")
        return base

    def save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2, ensure_ascii=False))
        tmp.replace(self.path)

    # ── Write API ─────────────────────────────────────────────────────────────

    def upsert_note(self, note_name: str, note_title: str, note_url: str,
                    tags: list[str], items: list[str]):
        """Replace all TODO items for a note with the current approved set.

        Existing items for this note that are NOT in the new list are removed
        (the model no longer sees them). Done items that ARE still present keep
        their done state — unchecking in the source note doesn't auto-reset them,
        since the user may have checked them on the dashboard.
        """
        existing = {k: v for k, v in self._data["items"].items()
                    if v.get("note_name") == note_name}
        # Remove items no longer present in this note's action list
        for k in list(existing):
            text = existing[k]["text"]
            if text not in items:
                del self._data["items"][k]

        # Upsert current items
        for text in items:
            key = _item_key(note_name, text)
            if key in self._data["items"]:
                # Update metadata but preserve done state
                self._data["items"][key]["note_title"] = note_title
                self._data["items"][key]["note_url"] = note_url
                self._data["items"][key]["tags"] = tags
            else:
                self._data["items"][key] = {
                    "note_name": note_name,
                    "note_title": note_title,
                    "note_url": note_url,
                    "text": text,
                    "tags": tags,
                    "done": False,
                    "done_at": None,
                    "added_at": _now_iso(),
                }
        self.save()

    def remove_note(self, note_name: str):
        """Remove all items for a note (e.g. note deleted or no more items)."""
        removed = [k for k, v in self._data["items"].items()
                   if v.get("note_name") == note_name]
        for k in removed:
            del self._data["items"][k]
        if removed:
            self.save()

    def mark_done(self, note_name: str, item_text: str, done: bool = True):
        """Mark a single item done or undone."""
        key = _item_key(note_name, item_text)
        if key in self._data["items"]:
            self._data["items"][key]["done"] = done
            self._data["items"][key]["done_at"] = _now_iso() if done else None
            self.save()
            return True
        return False

    # ── Read API ──────────────────────────────────────────────────────────────

    def pending_items(self) -> list[dict]:
        """Return all not-done items, sorted by tag then note_title."""
        items = [v for v in self._data["items"].values() if not v.get("done")]
        return sorted(items, key=lambda x: (
            (x.get("tags") or ["zzz"])[0],
            x.get("note_title", "")
        ))

    def done_items(self) -> list[dict]:
        """Return done items, most recently completed first."""
        items = [v for v in self._data["items"].values() if v.get("done")]
        return sorted(items, key=lambda x: x.get("done_at") or "", reverse=True)

    def all_items(self) -> dict:
        return self._data["items"]

    def get_items_for_note(self, note_name: str) -> list[dict]:
        return [v for v in self._data["items"].values()
                if v.get("note_name") == note_name]

    def pending_count(self) -> int:
        return sum(1 for v in self._data["items"].values() if not v.get("done"))
