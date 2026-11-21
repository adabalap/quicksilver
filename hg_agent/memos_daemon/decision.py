"""decision.py — the per-note state machine.

Given a note's current content and our recorded state, decide what to do:
  skip · suggest · reenrich · finalize

The extractor user_text_of() now returns ONLY the human-authored text (via the
:original markers for new notes, or the legacy heuristic for old ones), so hash
comparisons here are reliable: editing the AI's summary no longer looks like the
user edited their own text.
"""

from .anchor import (parse_anchor, strip_anchor, user_text_of, text_hash,
                     content_hash, is_approved_ticked)


def decide(note: dict, state, cfg: dict) -> str:
    name = note.get("name", "")
    content = note.get("content", "") or ""
    # Quicksilver notes carry their status directly in the dict
    qs_status = note.get("status")   # "raw", "pending", "approved", "force", or None

    # ── Never process our own maintained notes ────────────────────────────────
    if name and name in (state.dashboard_id, state.connections_id, state.todo_id):
        return "skip"

    # ── Quicksilver backend: state-machine driven by DB status column ─────────
    if qs_status is not None:
        if not content.strip():
            return "skip"
        prior = state.get(name) or {}
        # Change detection ignores highlight markers: a highlight/erase must not
        # look like a content edit (see anchor.content_hash — shared definition
        # with the PWA's _strip_hl).
        cur_hash = content_hash(content.strip())

        if qs_status == "raw":
            # Never seen or changed since last processing
            if prior.get("hash") == cur_hash:
                return "skip"
            return "suggest"

        if qs_status == "dirty":
            # Edited and autosaved, but the user hasn't committed yet (editor
            # still open). Durability without interpretation: hold the note out
            # of the enrichment queue entirely. It is promoted to 'force' when
            # the user closes the editor or taps Run AI now — so someone taking
            # notes for an hour with pauses never triggers a mid-session run.
            # The agent's safety sweep promotes long-stranded 'dirty' notes (a
            # lost session) so nothing is left un-enriched forever.
            return "skip"

        if qs_status == "pending":
            # Daemon suggested, awaiting user action in PWA.
            # If body changed since last processing → re-suggest.
            if prior.get("hash") and prior.get("hash") != cur_hash:
                return "suggest"
            return "skip"

        if qs_status == "approved":
            # First transition into "approved" → run approval side-effects once
            # (adoption is already done by the PWA; here we do calendar events +
            # state bookkeeping). This MUST be checked before the body-changed
            # test: approval adopts the polished text, so the body legitimately
            # differs from what we last enriched — that is NOT a user edit and
            # must not trigger a re-enrichment loop.
            if prior.get("status") != "approved":
                return "approve_qs"
            # Already approved before: a genuine body change now means the user
            # edited the living document → re-enrich.
            if prior.get("hash") and prior.get("hash") != cur_hash:
                return "reenrich"
            return "skip"

        if qs_status == "force":
            # Explicit reprocess request from PWA status bar
            return "reenrich"

        return "skip"

    # ── Memos legacy backend: anchor-based state machine ─────────────────────
    _a = parse_anchor(content)
    if _a and _a.get("role") in ("dashboard", "connections", "todo"):
        if name and _a.get("role") == "dashboard" and not state.dashboard_id:
            state.dashboard_id = name
        if name and _a.get("role") == "connections" and not state.connections_id:
            state.connections_id = name
        if name and _a.get("role") == "todo" and not state.todo_id:
            state.todo_id = name
        return "skip"
    for title_key, attr in (("dashboard_title", "dashboard_id"),
                            ("connections_title", "connections_id"),
                            ("todo_title", "todo_id")):
        t = cfg.get(title_key)
        if t and content.lstrip().startswith(f"# {t}"):
            if name and not getattr(state, attr):
                setattr(state, attr, name)
            return "skip"

    if not strip_anchor(content).strip():
        return "skip"

    anchor = _a
    cur_user = user_text_of(content)

    if anchor is None:
        prior = state.get(name)
        if prior and prior.get("hash") == text_hash(cur_user):
            return "skip"
        return "suggest"

    prior = state.get(name) or {}
    if (prior.get("failures", 0) >= cfg.get("max_retries", 3)
            and prior.get("hash") == text_hash(cur_user)):
        return "skip"

    status = anchor.get("status")

    if status == "suggested":
        if is_approved_ticked(content):
            return "finalize"
        if anchor.get("hash") != text_hash(cur_user):
            return "suggest"
        return "skip"

    if status == "approved":
        if is_approved_ticked(content):
            return "skip"
        return "reenrich"

    return "skip"
