"""Related-notes corpus: build, touch, thresholds, tag assist, related-note injection."""
from ..logging_setup import log
from ..anchor import parse_anchor, user_text_of
from ..correlation import Corpus
from ..util import _first_line


class CorpusMixin:
    """Related-notes corpus: build, touch, thresholds, tag assist, related-note injection."""

    def _inject_related(self, name: str, source_text: str, enrichment: dict):
        """Find related notes and attach deep links to the enrichment dict so
        the renderer can show a **Related:** line."""
        if not self.cfg.get("correlation_enabled", True):
            return
        try:
            corpus = self._build_corpus(focus_name=name, focus_text=source_text)
            # Persist this note's embedding for the PWA's semantic search
            # (only when embeddings are actually in use).
            if self._backend == "quicksilver" and self.embedder and self.embedder.available():
                try:
                    doc = corpus._docs.get(name)
                    if doc and doc.get("emb") is not None:
                        vec = doc["emb"]
                        self.memos.store_embedding(
                            name, vec.tolist() if hasattr(vec, "tolist") else list(vec))
                except Exception as _e:
                    log.debug(f"embedding persist skipped for {name}: {_e}")
            if len(corpus) < 2:
                log.info(f"    🔗 correlation: only {len(corpus)} note(s) in corpus — "
                         f"need ≥2 for relationships")
                return
            rel = corpus.related(
                name,
                top_n=self.cfg.get("related_top_n", 3),
                threshold=self._rel_threshold(),
                confirm_band=self._confirm_band(),
                tag_assist=self._tag_assist(),
            )
            if rel:
                enrichment["_related_links"] = [
                    {"title": title or "note",
                     "url": self._note_url(rname),
                     "score": round(score, 3)}
                    for rname, title, score in rel
                ]
                log.info(f"    🔗 {len(rel)} related note(s) linked "
                         f"(top score: {rel[0][2]:.3f})")
            else:
                log.info(f"    🔗 no related notes above threshold "
                         f"({self._rel_threshold():.2f}) for {name}")
        except Exception as e:
            log.warning(f"    🔗 correlation failed for {name}: {e}")

    def _corpus_touch(self, name, text, title="", tags=None):
        """Fold one just-enriched note into the cached corpus.

        Approving a note used to set _corpus_dirty=True, which discarded the
        whole corpus. Now that EVERY note auto-approves, that meant a full
        rebuild (all ~400 notes, ~1.5s of CPU) twice per note — once for the
        related-notes lookup during enrichment and again after approval. Adding
        just the changed note to the existing corpus gives the same result for a
        fraction of the work: faster per note and easier on the battery.

        Falls back to a full rebuild if there's no cache to update.
        """
        cached = getattr(self, "_corpus_cache", None)
        if cached is None:
            self._corpus_dirty = True
            return
        try:
            cached.add(name, (title or _first_line(text or "")), text or "",
                       tags=list(tags or []), summary="")
        except Exception as e:
            log.debug(f"corpus incremental update failed ({e}); full rebuild next")
            self._corpus_dirty = True

    def _build_corpus(self, focus_name: str = "", focus_text: str = "") -> Corpus:
        """Build the note corpus for relationship detection.

        Uses iter_all_memos (no watermark) so every note is included, not just
        changed ones. Cache for `correlation_cache_seconds` (default 120s) to
        avoid re-paging on every enrichment; always overlay the focus note's
        freshest text on top of the cache so in-flight correlation stays current.

        Cache is invalidated whenever a note is approved (self._corpus_dirty=True)
        so newly approved notes show up in the next relationship render without
        waiting for the TTL.
        """
        import time as _t
        now = _t.monotonic()
        cache_ttl = self.cfg.get("correlation_cache_seconds", 120)
        cached = getattr(self, "_corpus_cache", None)
        cached_at = getattr(self, "_corpus_cache_at", 0.0)
        corpus_dirty = getattr(self, "_corpus_dirty", False)

        if (cached is not None
                and not corpus_dirty
                and (now - cached_at) < cache_ttl):
            if focus_name and focus_text:
                cached.add(focus_name, _first_line(focus_text), focus_text,
                           tags=[], summary="")
            return cached

        log.info("    building correlation corpus (all notes)…")
        corpus = Corpus(self.embedder)
        limit = self.cfg.get("correlation_corpus_size", 500)
        excluded = {self.state.dashboard_id, self.state.connections_id,
                    self.state.todo_id} - {""}
        count = 0
        skipped = 0
        for m in self.memos.iter_all_memos(page_size=self.cfg.get("page_size", 50)):
            nm = m.get("name", "")
            if nm in excluded:
                skipped += 1
                continue
            content = m.get("content", "") or ""
            a = parse_anchor(content)
            if a and a.get("role") in ("dashboard", "connections", "todo"):
                skipped += 1
                continue
            human = user_text_of(content)
            if not human or len(human.split()) < 3:
                skipped += 1
                continue  # too short to be useful for correlation
            title = (a.get("title") if a else "") or m.get("title") or _first_line(human)
            # Prefer the anchor block (Memos-era notes embed metadata in the
            # body); fall back to the relational fields the Quicksilver backend
            # provides. Without the fallback, Quicksilver notes reach the corpus
            # with NO tags, silently disabling all tag-aware relationship logic.
            tags  = (a.get("tags")  if a else None) or m.get("tags") or []
            summary = (a.get("summary") if a else "") or m.get("summary") or ""
            # Reuse the persisted embedding to skip a model inference per note.
            # EXCEPT the focus note: its text is overlaid fresh below, so any
            # stored vector is stale — recompute it (emb=None) to stay correct.
            emb = None
            if nm != focus_name:
                stored = m.get("embedding")
                # A vector is only reusable if the SAME model made it. Legacy rows
                # (embed_model NULL) predate tagging and were MiniLM.
                _tag = getattr(self.embedder, "tag", "") if self.embedder else ""
                _src = m.get("embed_model") or "all-MiniLM-L6-v2"
                if stored and _src == _tag:
                    from ..embeddings import decode_embedding
                    emb = decode_embedding(stored)
            if nm == focus_name and focus_text:
                human = focus_text
            corpus.add(nm, title, human, tags=tags, summary=summary, emb=emb)
            count += 1
            if count >= limit:
                log.info(f"    corpus limit ({limit}) reached — stopping early")
                break

        backend = "semantic" if corpus._use_embed else "lexical"
        log.info(f"    corpus: {len(corpus)} note(s) [{backend}]"
                 + (f", {skipped} skipped" if skipped else ""))
        self._corpus_cache = corpus
        self._corpus_cache_at = now
        self._corpus_dirty = False
        return corpus

    def _rel_threshold(self) -> float:
        if self.embedder and self.embedder.available():
            return self.cfg.get("embed_related_threshold", 0.40)
        return self.cfg.get("related_threshold", 0.12)

    def _confirm_band(self) -> float:
        """Width of the uncertain band where a shared tag is required to link.
        Only meaningful in semantic mode (lexical overlap already implies
        shared vocabulary, so tag confirmation adds nothing there)."""
        if self.embedder and self.embedder.available():
            return self.cfg.get("embed_confirm_band", 0.10)
        return 0.0

    def _tag_assist(self) -> float:
        """How far a SHARED TAG lowers the linking bar (semantic mode only)."""
        if self.embedder and self.embedder.available():
            return self.cfg.get("embed_tag_assist", 0.20)
        return 0.0

    def _clu_threshold(self) -> float:
        if self.embedder and self.embedder.available():
            return self.cfg.get("embed_cluster_threshold", 0.50)
        return self.cfg.get("cluster_threshold", 0.18)
