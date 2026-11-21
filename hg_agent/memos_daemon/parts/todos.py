"""To-do sync: enrichment -> to-do store, checkbox events, to-do dashboard."""
from ..logging_setup import log
from ..anchor import make_anchor, display_timestamp
from ..checkbox_sync import extract_note_checkbox_states, extract_dashboard_checkbox_states, patch_checkbox_in_text


class TodosMixin:
    """To-do sync: enrichment -> to-do store, checkbox events, to-do dashboard."""

    def _sync_todos_from_enrichment(self, name: str, enrichment: dict):
        if self._backend == "quicksilver":
            return  # write_enrichment already stored action items in the DB
        """Update the TODO index from a freshly approved enrichment."""
        items = [a.strip() for a in (enrichment.get("action_items") or [])
                 if isinstance(a, str) and a.strip()]
        title = (enrichment.get("title") or "").strip()
        tags = enrichment.get("tags") or []
        url = self._note_url(name)
        if items:
            self.todos.upsert_note(name, title, url, tags, items)
            log.info(f"  ✅ todo index: {len(items)} item(s) indexed for {name}")
        else:
            # Note approved with no action items — remove any stale entries
            self.todos.remove_note(name)

    def _intercept_checkbox_event(self, name: str, note: dict) -> bool:
        if self._backend == "quicksilver":
            return False  # action toggles happen in the DB via the PWA
        content = note.get("content", "") or ""

        if self.cfg.get("todo_enabled", True) and name == self.state.todo_id:
            return self._sync_from_dashboard(content)

        if self.cfg.get("todo_enabled", True) and self.todos.get_items_for_note(name):
            return self._sync_from_note(name, content)

        return False

    def _sync_from_dashboard(self, dashboard_content: str) -> bool:
        """Read checkbox states off the TODO dashboard and propagate any
        mismatch (vs. the TodoStore) to the matching source note. Always
        returns True — the dashboard is exclusively our own rendered surface,
        so there is nothing else for the pipeline to do with it."""
        live_items = extract_dashboard_checkbox_states(dashboard_content)
        any_change = False
        for item in live_items:
            text, done, note_name = item["text"], item["done"], item["note_name"]
            if not note_name:
                continue
            stored = self.todos.all_items().get(f"{note_name}|||{text}")
            if stored is None or stored.get("done") == done:
                continue  # already in sync, or not a tracked item

            self.todos.mark_done(note_name, text, done=done)
            any_change = True
            log.info(f"  ☑ dashboard: '{text}' → {'done' if done else 'undone'} "
                     f"— propagating to {note_name}")
            self._patch_checkbox_in_note(note_name, text, done)

        if any_change:
            self._update_todo_dashboard()
        return True

    def _sync_from_note(self, name: str, content: str) -> bool:
        """Read checkbox states off a source note's Action Items section and
        propagate any mismatch (vs. the TodoStore) to the dashboard.

        Returns False — does NOT short-circuit _process_one — because a source
        note can simultaneously have a ticked checkbox AND other genuine content
        edits. The normal decide()/enrich pipeline runs afterward.
        This function only patches the dashboard; it never writes back to the
        source note (that would cause an infinite re-evaluation loop)."""
        live_items = extract_note_checkbox_states(content)
        if not live_items:
            return False

        any_change = False
        for text, done in live_items.items():
            stored = self.todos.all_items().get(f"{name}|||{text}")
            if stored is None or stored.get("done") == done:
                continue  # already in sync with store — nothing to do

            self.todos.mark_done(name, text, done=done)
            any_change = True
            log.info(f"  ☑ note {name}: '{text}' → {'done' if done else 'undone'} "
                     f"— synced to dashboard")
            self._patch_checkbox_in_dashboard(text, done, name)

        if any_change:
            self._update_todo_dashboard()
        return False

    def _patch_checkbox_in_note(self, note_name: str, item_text: str, done: bool):
        """Tick or untick a checkbox in a source note's Action Items section."""
        note = self.memos.get_memo(note_name)
        if not note:
            log.debug(f"    checkbox: could not fetch source note {note_name}")
            return
        content = note.get("content", "") or ""
        new_content, changed = patch_checkbox_in_text(content, item_text, done)
        if not changed:
            log.debug(f"    checkbox: '{item_text}' not found in {note_name}")
            return
        from ..memos_client import MemosClient as _MC
        ut = self.memos.update_content(note_name, new_content)
        if ut and ut is not _MC.PATCH_PERMANENT_ERROR:
            log.info(f"    ☑ patched source note {note_name}")
        else:
            log.warning(f"    checkbox: patch to source note {note_name} failed")

    def _patch_checkbox_in_dashboard(self, item_text: str, done: bool,
                                      source_note_name: str):
        """Tick or untick a checkbox on the TODO dashboard note."""
        if not self.state.todo_id:
            return
        note = self.memos.get_memo(self.state.todo_id)
        if not note:
            return
        content = note.get("content", "") or ""
        new_content, changed = patch_checkbox_in_text(content, item_text, done)
        if not changed:
            log.debug(f"    checkbox: '{item_text}' not found on dashboard")
            return
        from ..memos_client import MemosClient as _MC
        ut = self.memos.update_content(self.state.todo_id, new_content)
        if ut and ut is not _MC.PATCH_PERMANENT_ERROR:
            log.info(f"    ☑ patched dashboard for '{item_text}'")
        else:
            log.warning(f"    checkbox: dashboard patch failed for '{item_text}'")

    def _update_todo_dashboard(self):
        if self._backend == "quicksilver":
            return  # PWA renders Actions natively from the action_items table
        """Render and upsert the cross-note TODO dashboard note."""
        now = display_timestamp(self.cfg.get("display_timezone", "Asia/Kolkata"))
        pending = self.todos.pending_items()
        done_items = self.todos.done_items()

        # Group pending items by primary tag (or "general" if untagged)
        groups: dict[str, list] = {}
        for item in pending:
            tag = (item.get("tags") or ["general"])[0]
            groups.setdefault(tag, []).append(item)

        # Tag → emoji mapping for visual grouping
        _TAG_EMOJI = {
            "work": "💼", "finance": "💰", "health": "❤️", "shopping": "🛒",
            "grocery": "🛒", "groceries": "🛒", "personal": "👤",
            "family": "👨‍👩‍👧", "travel": "✈️", "tech": "💻",
            "education": "📚", "fitness": "🏃",
        }

        lines = [
            f"# {self.cfg.get('todo_title', '📋 Action Items')}",
            "",
            f"> **{len(pending)} pending** · Updated {now}",
            "",
        ]

        if not pending:
            lines += [
                "No pending action items. When approved notes contain action "
                "items, they appear here grouped by topic.",
                "",
            ]
        else:
            for tag, items in sorted(groups.items()):
                emoji = _TAG_EMOJI.get(tag, "📌")
                lines.append(f"## {emoji} {tag.title()}")
                lines.append("")
                for item in items:
                    note_name = item["note_name"]
                    text = item["text"]
                    note_title = item.get("note_title") or "note"
                    note_url = item.get("note_url", "")
                    # Embed note_name in a hidden data span for checkbox sync
                    link = f'[{note_title}]({note_url})' if note_url else note_title
                    hidden = f'<span data-note="{note_name}" style="display:none"></span>'
                    lines.append(f"- [ ] **{text}** · {link}{hidden}")
                lines.append("")

        # Archived / done items — collapsible
        if done_items:
            lines += [
                "<details>",
                f"<summary>✅ {len(done_items)} completed item(s)</summary>",
                "",
            ]
            for item in done_items[:50]:  # cap at 50 in the collapsible
                note_title = item.get("note_title") or "note"
                note_url = item.get("note_url", "")
                text = item["text"]
                note_name = item["note_name"]
                link = f'[{note_title}]({note_url})' if note_url else note_title
                hidden = f'<span data-note="{note_name}" style="display:none"></span>'
                lines.append(f"- [x] ~~{text}~~ · {link}{hidden}")
            lines.append("")
            lines.append("</details>")
            lines.append("")

        todo_tag = self.cfg.get("todo_tag", "todo-dashboard")
        lines += [
            f"#{todo_tag}",
            "",
            "_Maintained automatically by the AI agent. Checking an item here "
            "will check it in the source note too. Do not edit the hidden spans._",
        ]

        content = "\n".join(lines)
        content = f"{content}\n{make_anchor('todo', '', '', role='todo')}"
        self._upsert_maintained("todo_id", content, "todo")
