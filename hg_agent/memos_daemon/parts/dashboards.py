"""Maintained dashboard notes: connections and the shared upsert."""
from ..logging_setup import log
from ..anchor import parse_anchor, make_anchor, display_timestamp


class DashboardsMixin:
    """Maintained dashboard notes: connections and the shared upsert."""

    # Memos enforces an 8 192-character content limit. When the enriched note
    # body exceeds this, we trim the least-important sections (polished body
    # first, then summary) until it fits, logging what was cut. This is better
    # than silently failing the PATCH and burning a pending-write retry loop.
    _MEMOS_MAX_CONTENT = 8000   # leave 192 chars of headroom for safety

    def _truncate_for_memos(self, content: str, name: str) -> str:
        """Trim content to fit within Memos 8192-char limit.

        Strategy (least-important first):
          1. Already fits -> return unchanged.
          2. Trim the polished/Note body section to 500 chars.
          3. Remove the Summary section entirely.
          4. Hard-truncate at the limit as a last resort.
        """
        if len(content) <= self._MEMOS_MAX_CONTENT:
            return content

        import re as _re
        trimmed = content

        # Step 2: shorten the Note/Enriched Note body block
        def _shorten_body(text):
            for label in ("**Note:**", "**Enriched Note:**"):
                pattern = _re.compile(
                    _re.escape(label) + r"\s*(.*?)(\n\n|\Z)", _re.DOTALL)
                def _cap(m, _label=label):
                    body = m.group(1).strip()
                    if len(body) > 500:
                        body = body[:497] + "..."
                    return _label + "\n" + body + "\n\n"
                text = pattern.sub(_cap, text, count=1)
            return text

        trimmed = _shorten_body(trimmed)
        if len(trimmed) <= self._MEMOS_MAX_CONTENT:
            log.info(f"  cut content (body shortened) for {name}: "
                     f"{len(content)} -> {len(trimmed)} chars")
            return trimmed

        # Step 3: remove Summary section
        trimmed = _re.sub(
            r"\*\*Summary:\*\*\s*.*?(\n\n|\Z)", "",
            trimmed, count=1, flags=_re.DOTALL)
        if len(trimmed) <= self._MEMOS_MAX_CONTENT:
            log.info(f"  cut content (summary removed) for {name}: "
                     f"{len(content)} -> {len(trimmed)} chars")
            return trimmed

        # Step 4: hard truncate - keep anchor tail, trim the middle
        anchor_tail = trimmed[-300:]
        keep_head = self._MEMOS_MAX_CONTENT - 300 - 5
        trimmed = trimmed[:keep_head] + "\n...\n" + anchor_tail
        log.warning(f"  content hard-truncated for {name}: "
                    f"{len(content)} -> {len(trimmed)} chars")
        return trimmed

    def _authoritative_update_time(self, name: str, patch_ut: str) -> str:
        """Return the note's true server-side updateTime after our PATCH.
        Prefer the value the PATCH response carried; if it was just 'OK', re-read
        the note so the watermark/seen value matches what the next poll sees.
        This closes the patch-then-rewatermark double-enrichment edge case."""
        if patch_ut and patch_ut != "OK":
            return patch_ut
        fresh = self.memos.get_memo(name)
        if fresh and fresh.get("updateTime"):
            return fresh["updateTime"]
        return patch_ut

    def _note_url(self, name: str) -> str:
        """Build a user-facing URL to a memo using note_url_template. This
        instance serves notes at /memos/<uid>; the /m/<uid> short form 404s.
        Falls back to the base URL if no uid is known."""
        uid = name.split("/")[-1] if name else ""
        base = self.cfg["memos_base_url"].rstrip("/")
        if not uid:
            return base
        template = self.cfg.get("note_url_template", "{base}/memos/{uid}")
        return template.format(base=base, uid=uid)

    def _find_existing_dashboard(self, role: str = "dashboard") -> str:
        """Scan recent notes for one of our maintained notes (role 'dashboard'
        or 'connections'), matched by anchor role OR by its distinctive title so
        notes created by older builds are adopted rather than duplicated."""
        _title_key_map = {
            "dashboard": "dashboard_title",
            "connections": "connections_title",
            "todo": "todo_title",
        }
        title_marker = self.cfg.get(_title_key_map.get(role, "dashboard_title"), "")
        try:
            for m in self.memos.iter_all_memos(page_size=self.cfg.get("page_size", 50)):
                content = m.get("content", "") or ""
                a = parse_anchor(content)
                if a and a.get("role") == role:
                    return m.get("name", "")
                if title_marker and content.lstrip().startswith(f"# {title_marker}"):
                    return m.get("name", "")
        except Exception as e:
            log.warning(f"{role} scan failed: {e}")
        return ""

    @staticmethod
    def _bar(value: int, total: int, width: int = 16) -> str:
        """A unicode progress bar for the markdown dashboard."""
        if total <= 0:
            return "─" * width
        filled = round(width * value / total)
        return "█" * filled + "░" * (width - filled)

    def _update_dashboard(self):
        if self._backend == "quicksilver":
            return  # PWA renders stats natively from /api/stats
        s = self.state.stats()
        m = self.state.metrics()
        now = display_timestamp(self.cfg.get("display_timezone", "Asia/Kolkata"))
        t = self.thermal.read_celsius()
        total_actions = (s.get("suggested", 0) + s.get("approved", 0)
                         + s.get("reenriched", 0))
        denom = max(total_actions, 1)

        # Health signal
        if t is None:
            health = "🟢 nominal"
        elif t >= self.cfg["thermal_hot_celsius"]:
            health = "🔴 throttling (hot)"
        elif t >= self.cfg["thermal_resume_celsius"]:
            health = "🟡 warm"
        else:
            health = "🟢 nominal"

        lines = [
            f"# {self.cfg['dashboard_title']}",
            "",
            f"> **Status:** {health}  ·  **Updated:** {now}",
            "",
            "## 📊 Activity",
            "",
            "| Outcome | Count | Share |",
            "|---|---:|:--|",
            f"| 🟠 Suggested | {s.get('suggested', 0)} | `{self._bar(s.get('suggested',0), denom)}` |",
            f"| ✅ Approved | {s.get('approved', 0)} | `{self._bar(s.get('approved',0), denom)}` |",
            f"| ✎ Re-enriched | {s.get('reenriched', 0)} | `{self._bar(s.get('reenriched',0), denom)}` |",
            f"| ⚠ Needs review | {s.get('needs_review', 0)} | `{self._bar(s.get('needs_review',0), denom)}` |",
            f"| ⏭ Skipped | {s.get('skipped', 0)} | — |",
            f"| ✗ Errors | {s.get('errors', 0)} | — |",
            "",
            "## ⚡ Performance",
            "",
            "| Metric | Value |",
            "|---|---:|",
            f"| Notes enriched | {m.get('enrich_count', 0)} |",
            f"| Last inference | {m.get('last_latency_s', 0):.2f}s |",
            f"| Avg inference | {m.get('avg_latency_s', 0):.2f}s |",
            f"| SoC temperature | {f'{t:.1f}°C' if t is not None else 'n/a'} |",
            "",
        ]
        review_tag = self.cfg.get("review_tag", "pending-approval")
        # NOTE: never write "#<review_tag>" here — Memos would parse it as a real
        # tag and this dashboard would then show up in the user's review filter.
        # Refer to the shortcut by name only, with the hash escaped/spaced.
        lines += [
            "## 🔎 Awaiting your approval",
            "",
            f"Every AI suggestion is added to your review queue until you approve "
            f"it. Open your saved **{review_tag}** shortcut (filter "
            f"`tag:{review_tag}`) in Memos to see everything waiting on you. "
            "Low-confidence suggestions are flagged inside the note's approve box.",
            "",
        ]
        lines.append(f"#{self.cfg.get('dashboard_tag', 'agent-dashboard')}")
        lines.append("")
        lines.append("_Maintained automatically by the AI agent. Do not edit._")

        content = "\n".join(lines)
        content = f"{content}\n{make_anchor('dashboard', '', '', role='dashboard')}"
        self._upsert_maintained("dashboard_id", content, "dashboard")

    def _update_connections(self):
        if self._backend == "quicksilver":
            # Quicksilver: compute clusters and write relationship pairs to the
            # DB. No markdown note is rendered — the PWA's Connections tab reads
            # the relationships table directly.
            try:
                corpus = self._build_corpus()
                if len(corpus) < 2:
                    log.info(f"    🔗 only {len(corpus)} note(s) — skipping relationships")
                    return
                clusters = corpus.clusters(threshold=self._clu_threshold())
                pairs, seen = [], set()
                def _add(a, b, s):
                    k = (a, b) if a < b else (b, a)
                    if k not in seen:
                        seen.add(k); pairs.append((a, b, s))
                # Each note links to its TOP-K nearest neighbours above the
                # related threshold — NOT to every note in its cluster. When the
                # corpus is topically homogeneous (e.g. lots of notes about one
                # project), clusters() can put everything in one component; the
                # old "all intra-cluster pairs" logic then wrote N*(N-1)/2 links
                # (528 pairs for 33 notes — a fully-connected blob that tells the
                # user nothing). Top-K keeps the graph sparse and meaningful, and
                # it's what the Connections tab actually needs: a note's closest
                # relatives, not a clique.
                topk = self.cfg.get("related_top_n", 4)
                for nm in corpus.names():
                    for rn, _t, sc in corpus.related(
                            nm, top_n=topk, threshold=self._rel_threshold()):
                        _add(nm, rn, sc)
                if pairs:
                    self.memos.write_relationships(pairs)
                    log.info(f"    🔗 {len(pairs)} relationship pair(s) written "
                             f"({len(clusters)} cluster(s))")
                else:
                    log.info("    🔗 no clusters above threshold")
            except Exception as e:
                log.warning(f"    🔗 relationships failed: {e}")
            return

        """Build the 🔗 Note Relationships dashboard.

        Groups are connected components of notes whose lexical similarity
        (TF-IDF cosine over their text) exceeds cluster_threshold. For each group
        we surface WHY it was grouped (shared distinctive terms), HOW tightly
        (cohesion strength), and scaffold the members under a collapsible heading
        led by the most central 'anchor' note."""
        try:
            corpus = self._build_corpus()
        except Exception as e:
            log.debug(f"connections: corpus build failed: {e}")
            return
        clusters = corpus.clusters(threshold=self._clu_threshold()) if len(corpus) >= 2 else []
        now = display_timestamp(self.cfg.get("display_timezone", "Asia/Kolkata"))
        linked = sum(len(g) for g in clusters)

        lines = [
            f"# {self.cfg['connections_title']}",
            "",
            f"> **{len(clusters)}** related group(s) · **{linked}** of "
            f"**{len(corpus)}** notes connected · Updated {now}",
            "",
        ]

        if not clusters:
            if len(corpus) < 2:
                lines += [
                    f"Not enough notes to find relationships yet — need at least "
                    f"2 notes ({len(corpus)} in corpus). Add more notes and the "
                    f"agent will group related ones here automatically.",
                    "",
                ]
            else:
                lines += [
                    f"No related groups found among {len(corpus)} notes at the "
                    f"current similarity threshold ({self._clu_threshold():.2f}). "
                    f"Notes may be too varied or too short. The dashboard updates "
                    f"automatically as you add more notes.",
                    "",
                ]
        else:
            for i, group in enumerate(clusters, 1):
                # Order members: anchor first, then by similarity to the anchor.
                anchor = corpus.anchor_note(group)
                rest = sorted(
                    [n for n in group if n != anchor],
                    key=lambda n: corpus.cohesion([anchor, n]),
                    reverse=True,
                )
                ordered = [anchor] + rest

                terms = corpus.shared_terms(group, top_k=5)
                theme = ", ".join(terms[:3]) if terms else "related notes"
                strength = corpus.cohesion(group)
                bar = self._strength_bar(strength)
                strength_word = ("strong" if strength >= 0.45 else
                                 "moderate" if strength >= 0.25 else "loose")

                # Collapsible group: heading shows theme + size; body explains
                # the link and scaffolds the member notes.
                lines.append("<details>")
                lines.append(
                    f"<summary><strong>🧩 {theme}</strong> — "
                    f"{len(group)} notes · {strength_word} link {bar}</summary>"
                )
                lines.append("")
                if terms:
                    lines.append(f"*Grouped because they share:* "
                                 + ", ".join(f"`{t}`" for t in terms))
                    lines.append("")
                for nm in ordered:
                    title = corpus.title_of(nm)
                    marker = "⭐ " if nm == anchor else ""
                    lines.append(f"- {marker}[{title}]({self._note_url(nm)})")
                lines.append("")
                lines.append("</details>")
                lines.append("")

        # Short, honest explainer so the grouping criteria isn't a mystery.
        lines += [
            "---",
            "",
            "<details>",
            "<summary>ℹ️ How groups are formed</summary>",
            "",
            (("The agent reads the meaning of each note (title, tags, summary, "
              "and text) using an on-device language model and groups notes whose "
              "meanings are close — so notes about the same thing are linked even "
              "when they use different words. ⭐ marks the most central note in "
              "each group.")
             if (self.embedder and self.embedder.available()) else
             ("The agent compares the words in your notes, giving more weight to "
              "distinctive terms and less to common ones. Notes with enough "
              "overlap are linked; chains of linked notes form a group. ⭐ marks "
              "the most central note. This is keyword-based, so notes about the "
              "same idea in different words may not link — enable semantic "
              "embeddings (embed_model) for meaning-based grouping.")),
            "",
            "</details>",
            "",
            f"#{self.cfg.get('connections_tag', 'note-relationships')}",
            "",
            "_Maintained automatically by the AI agent. Do not edit._",
        ]

        content = "\n".join(lines)
        content = f"{content}\n{make_anchor('connections', '', '', role='connections')}"
        self._upsert_maintained("connections_id", content, "connections")

        # For Quicksilver: also write relationship pairs directly to the DB.
        # Top-K per note (see _update_connections for the full rationale) — never
        # all intra-cluster pairs, which explodes to a fully-connected blob when
        # the corpus is topically homogeneous.
        if self._backend == "quicksilver":
            try:
                pairs, seen = [], set()
                topk = self.cfg.get("related_top_n", 4)
                for nm in corpus.names():
                    for rn, _t, sc in corpus.related(
                            nm, top_n=topk, threshold=self._rel_threshold()):
                        k = (nm, rn) if nm < rn else (rn, nm)
                        if k not in seen:
                            seen.add(k); pairs.append((nm, rn, sc))
                if pairs:
                    self.memos.write_relationships(pairs)
            except Exception as e:
                log.warning(f"QS relationship write failed: {e}")

    @staticmethod
    def _strength_bar(score: float, width: int = 5) -> str:
        """A tiny 0..1 strength meter, e.g. ●●●○○."""
        filled = max(0, min(width, round(score * width)))
        return "●" * filled + "○" * (width - filled)

    def _upsert_maintained(self, state_attr: str, content: str, label: str):
        """Update the maintained note if we know its id, else create it. Pinning
        is optional (pin_maintained_notes) — by default we rely on the note's tag
        plus a Memos shortcut for access, which is cleaner than pinning."""
        from ..memos_client import MemosClient as _MC
        content = self._truncate_for_memos(content, f"{label}-dashboard")
        nid = getattr(self.state, state_attr)
        if nid:
            ut = self.memos.update_content(nid, content)
            if ut and ut is not _MC.PATCH_PERMANENT_ERROR:
                return
            if ut is _MC.PATCH_PERMANENT_ERROR:
                # Note was deleted externally — clear the id so we recreate it
                log.warning(f"{label} note {nid} is gone (404/auth) — will recreate")
                setattr(self.state, state_attr, "")
                self.state.save()
            else:
                log.warning(f"{label} update failed; will recreate")
        pin = self.cfg.get("pin_maintained_notes", False)
        memo = self.memos.create_memo(content, pinned=pin)
        if memo and memo.get("name"):
            setattr(self.state, state_attr, memo["name"])
            self.state.save()
            how = "pinned" if pin else "tagged"
            log.info(f"  📌 {label} note created ({how}): {memo['name']}")
