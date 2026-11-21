"""formatting.py — render SUGGESTED and APPROVED note bodies.

Layout contract (matches anchor.py extractors):

SUGGESTED (under review)                    APPROVED (final)
──────────────────────────────────────     ──────────────────────────────────
<!-- memos-ai:original -->                  ### Title
<original user text>                        **Summary:** …
<!-- /memos-ai:original -->
                                            <enriched / polished body>
---
### Title              (AI, if missing)     **Action items:**   (if any)
**Summary:** …                              - [ ] item
<enriched / polished body>
                                            **Tags:** #a #b
**Action items:**     (if any)
- [ ] item                                  <details>🤖 approved + anchor</details>

**Tags:** #a #b       (bottom subtitle)
**Priority:** 🔴 high · **Confidence:** 0.82
**Related:** [link] [link]    (if correlated)

<details>🤖 approve controls + anchor</details>

Why original-on-top for suggestions: the human can see exactly what they wrote
versus what the AI proposes, side by side, before approving. On approval the
original block is dropped — it has served its purpose and is now noise.
"""

from datetime import datetime, timezone
from .anchor import (make_anchor, user_text_of, strip_leading_title,
                     similarity, wrap_original, wrap_note, display_timestamp)

PRIORITY_BADGE = {"high": "🔴 high", "medium": "🟡 medium", "low": "🟢 low"}


def first_line_is_title(text: str) -> bool:
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        return s.startswith("#")
    return False


def _enriched_section(enrichment: dict, cfg: dict, user_text: str,
                      *, include_title: bool, force_body: bool,
                      approved: bool) -> list[str]:
    """Build the shared enriched section in this order:
        Title → Summary → Action items → Refined body → Tags → Priority(high) → Related

    - Confidence is NOT displayed (poorly calibrated as a user-facing number);
      it lives only as the internal low-confidence flag (a visible cue in the
    - Priority is shown only when 'high' — low/medium is the absence of a signal
      and would just be noise on routine notes.
    - The enriched body carries a 'Refined' subtitle while under review; in an
      approved note the body simply IS the note, so no subtitle.
    """
    tags     = enrichment.get("tags", [])[:cfg["max_tags"]]
    tag_str  = " ".join(f"#{t}" for t in tags) if tags else ""
    summary  = (enrichment.get("summary") or "").strip()
    actions  = enrichment.get("action_items", []) or []
    title    = (enrichment.get("title") or "").strip()
    polished = (enrichment.get("polished") or "").strip()
    priority = (enrichment.get("priority") or "").strip().lower()
    related  = enrichment.get("_related_links") or []   # injected by daemon
    corrections = enrichment.get("corrections") or []
    body_label = cfg.get("enriched_label", "Enriched Note")

    lines: list[str] = []

    # ── Title ─────────────────────────────────────────────────────────────────
    if include_title and title and not first_line_is_title(user_text.strip()):
        lines += [f"### {title}", ""]

    # ── Summary ───────────────────────────────────────────────────────────────
    # Suppress summary for short notes: if the note is below long_note_threshold_words
    # (default 100), the note is its own summary — paraphrasing adds noise.
    # Backward-compat: fall back to legacy "summary_min_words" if present.
    _threshold = (cfg.get("long_note_threshold_words")
                  or cfg.get("summary_min_words")
                  or 100)
    _user_word_count = len(user_text.split())
    if summary and (_threshold == 0 or _user_word_count >= _threshold):
        lines += ["**Summary:**", summary, ""]

    # ── Action items ──────────────────────────────────────────────────────────
    # Second-layer guard: only render the section when there is at least one
    # non-empty item, so an empty/blank entry never produces a bare "- [ ]".
    actions = [a.strip() for a in actions if isinstance(a, str) and a.strip()]
    if actions:
        lines.append("**Action items:**")
        lines += [f"- [ ] {item}" for item in actions]
        lines.append("")

    # ── Body ──────────────────────────────────────────────────────────────────
    # Suggestion: show the enriched rewrite only when it improves on the original,
    #   under an 'Enriched Note' subtitle.
    # Approved: the body IS the note. Wrap it in :note markers under a 'Note'
    #   subtitle so it is a labeled, machine-extractable region — on
    #   'uncheck to reprocess' the agent re-enriches exactly this text.
    body = ""
    if polished and similarity(polished, strip_leading_title(user_text)) < 0.97:
        body = polished
    elif force_body:
        body = polished or strip_leading_title(user_text).strip()
    if body:
        if approved:
            note_label = cfg.get("note_label", "Note")
            lines += [f"**{note_label}:**", wrap_note(body), ""]
        else:
            lines += [f"**{body_label}:**", body, ""]

    # ── Corrections (transcription reconstruction) ─────────────────────────────
    # Shown on SUGGESTIONS only, so the user can verify the agent's reconstruction
    # of garbled voice-to-text before approving. Once approved, the corrected text
    # stands on its own and the change log is no longer needed.
    if corrections and not approved:
        lines.append("**Corrections:** *(AI reconstructed — please verify)*")
        for c in corrections:
            conf = c.get("confidence", 0.0)
            cue = "" if conf >= 0.8 else "  ⚠ uncertain"
            lines.append(f"- “{c['from']}” → “{c['to']}”{cue}")
        lines.append("")

    # ── Tags (bottom subtitle) ─────────────────────────────────────────────────
    if tag_str:
        lines += [f"**Tags:** {tag_str}", ""]

    # ── Priority — only when high ──────────────────────────────────────────────
    if priority == "high":
        lines += [f"**Priority:** {PRIORITY_BADGE['high']}", ""]

    # ── Related notes (correlation feature) ───────────────────────────────────
    if related:
        link_strs = [f"[{r['title']}]({r['url']})" for r in related]
        lines += ["**Related:** " + " · ".join(link_strs), ""]

    return lines


# ── SUGGESTED ─────────────────────────────────────────────────────────────────
def build_suggested(user_text: str, enrichment: dict, cfg: dict,
                    raw_baseline: str) -> str:
    """Render a note under review: original on top (in markers), enriched
    section below the divider, approve controls collapsed at the bottom.

    Every suggestion carries the review-queue tag (default #pending-approval)
    so the user can find everything awaiting approval via one Memos shortcut.
    The tag is removed on approval. Low-confidence notes get a small visible
    cue in the summary line but share the same single queue tag."""
    low_conf = bool(enrichment.get("_needs_review"))
    review_tag_name = cfg.get("review_tag", "pending-approval")

    original_block = wrap_original(user_text)

    enriched = _enriched_section(
        enrichment, cfg, user_text, include_title=True, force_body=False,
        approved=False
    )

    anchor_placeholder = "%%ANCHOR%%"
    summary_label = ("🟠 Review & approve (low confidence)" if low_conf
                     else "🟠 Approve these suggestions")
    details = [
        "<details>",
        f"<summary>🤖 AI — {'please review' if low_conf else 'approve or ignore'}</summary>",
        "",
        f"- [ ] {summary_label}",
        "",
        anchor_placeholder,
        "",
        "</details>",
        "",
    ]

    # Single review-queue tag at the very bottom so it reads as the note's
    # status and is easy to strip on approval.
    queue_tag = f"\n#{review_tag_name}"
    body_parts = [original_block, "", "---", ""] + enriched + details
    body = "\n".join(body_parts) + queue_tag

    # Anchor hash is over the human text the next poll will extract.
    anchor = make_anchor(
        "suggested",
        user_text,
        raw_baseline,
        summary=(enrichment.get("summary") or "").strip(),
        extra={"low_confidence": low_conf} if low_conf else None,
    )
    return body.replace(anchor_placeholder, anchor)


# ── APPROVED ──────────────────────────────────────────────────────────────────
def build_approved(user_text: str, enrichment: dict, cfg: dict,
                   raw_baseline: str) -> str:
    """Render a final note. The original block is intentionally NOT included —
    on approval it becomes obsolete. The enriched content stands alone."""
    title   = (enrichment.get("title") or "").strip()
    summary = (enrichment.get("summary") or "").strip()

    head: list[str] = []
    if title:
        head.append(f"### {title}")
    else:
        first = next((l.strip() for l in user_text.splitlines() if l.strip()), "")
        if first:
            head.append(f"### {first[:80]}")
    head.append("")

    enriched = _enriched_section(
        enrichment, cfg, user_text, include_title=False, force_body=True,
        approved=True
    )

    now_str = display_timestamp(cfg.get("display_timezone", "Asia/Kolkata"))
    anchor_placeholder = "%%ANCHOR%%"
    details = [
        "<details>",
        "<summary>🤖 AI — approved</summary>",
        "",
        "- [x] 🟢 Approved — uncheck to reprocess",
        f"*Enriched: {now_str}*",
        "",
        anchor_placeholder,
        "",
        "</details>",
        "",
    ]

    body = "\n".join(head + enriched + details)

    anchor = make_anchor("approved", user_text, raw_baseline, summary=summary)
    return body.replace(anchor_placeholder, anchor)
