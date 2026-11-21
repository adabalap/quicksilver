"""Embeddings and the search index: reindex, backfill, embed sidecar."""
import time, threading

from ..logging_setup import log


class IndexingMixin:
    """Embeddings and the search index: reindex, backfill, embed sidecar."""

    # ── Legacy polling mode (unchanged behaviour) ────────────────────────────
    def _index_quietly(self, note_id: str):
        """Best-effort passage index for one note; never raises."""
        try:
            if self._backend == "quicksilver" and self.embedder and self.embedder.available():
                self.memos.index_note_chunks(note_id)
        except Exception as e:
            log.debug(f"chunk index skipped for {note_id}: {e}")

    def _publish_embed_active(self):
        """Tell the app (separate process) which model the index uses and how to
        gate its scores, so both sides always rank in the same vector space."""
        e = self.embedder
        if not (e and e.available()):
            return
        try:
            self.memos.set_embed_active({
                "model": e.tag, "dim": e.dim,
                "profile": {k: e.profile.get(k) for k in
                            ("floor_search", "floor_ask", "margin", "z_min")},
            })
        except Exception as ex:
            log.warning(f"could not publish embed_active: {ex}")

    def _reindex_one(self, nid: str):
        """Bring one note fully onto the active model: note-level vector (for
        relationships) and passage chunks (for search / Ask)."""
        db = self.memos._db()
        r = db.execute("SELECT body, embedding, embed_model FROM notes WHERE id=?", (nid,)).fetchone()
        if r is None:
            return
        have = r["embed_model"] or ("all-MiniLM-L6-v2" if r["embedding"] else None)
        if r["embedding"] and have == self.embedder.tag:
            # Note-level vector is already from this model: don't rewrite it
            # (just make sure it is tagged), only (re)build the passages.
            if r["embed_model"] != self.embedder.tag:
                db.execute("UPDATE notes SET embed_model=? WHERE id=?", (self.embedder.tag, nid))
                db.commit()
            self.memos.index_note_chunks(nid, force=True)
            return
        tags = [x["tag"] for x in db.execute(
            "SELECT tag FROM tags WHERE note_id=? ORDER BY tag", (nid,))]
        e = db.execute("SELECT title, summary FROM enrichments WHERE note_id=? "
                       "ORDER BY enriched_at DESC, rowid DESC LIMIT 1", (nid,)).fetchone()
        text = self.memos._embed_text((e["title"] if e else "") or "", tags,
                                      (e["summary"] if e else "") or "", r["body"] or "")
        if text.strip():
            vec = self.embedder.encode(text, kind="passage")
            if vec is not None:
                self.memos.store_embedding(
                    nid, vec.tolist() if hasattr(vec, "tolist") else list(vec))
        self.memos.index_note_chunks(nid, force=True)

    def _reindex_check(self, nid: str, failed: bool = False):
        """A note that still has no chunks after an attempt is not stuck for ever: after three tries in this
        run it is marked as skipped, so the work list drains and the progress bar can finish. An edit retries it."""
        db = self.memos._db()
        tag = getattr(self.embedder, "tag", "")
        ok = db.execute("SELECT 1 FROM note_chunks WHERE note_id=? AND model=? LIMIT 1", (nid, tag)).fetchone() or \
            db.execute("SELECT 1 FROM index_skips WHERE note_id=? AND model=? LIMIT 1", (nid, tag)).fetchone()
        fails = getattr(self, "_reindex_fails", None)
        if fails is None:
            fails = self._reindex_fails = {}
        if ok and not failed:
            fails.pop(nid, None)
            return
        fails[nid] = fails.get(nid, 0) + 1
        if fails[nid] >= 3:
            self.memos.mark_index_skip(nid, "failed")
            fails.pop(nid, None)
            log.warning(f"  🔎 note {nid} could not be indexed after 3 tries; counting it as done (an edit retries)")

    def _reindex_batch(self, max_seconds: float = 20.0, limit: int = 25) -> int:
        """Index notes that lack chunks from the active model, in small
        time-boxed slices so a model switch or first install never monopolises
        the phone. Yields to the thermal guard and to real work in the queue."""
        if not (self._backend == "quicksilver" and self.embedder and self.embedder.available()):
            return 0
        done, t0 = 0, time.time()
        for nid in self.memos.notes_needing_index(limit):
            if not self.running or time.time() - t0 > max_seconds:
                break
            try:
                if self.cfg.get("thermal_enabled") and self.thermal.read_celsius() is not None \
                        and self.thermal.read_celsius() >= self.cfg.get("thermal_hot_celsius", 47):
                    break
                if hasattr(self, "_q") and self._q.qsize() > 0:
                    break                      # enrichment / Ask come first
                self._reindex_one(nid); done += 1
                self._reindex_check(nid)
            except Exception as e:
                log.warning(f"reindex failed for {nid}: {e}")
                if "locked" in str(e).lower():
                    break                          # the app is writing; try again next minute, not a bad note
                try:
                    self._reindex_check(nid, failed=True)   # three real failures and it is counted, not retried for ever
                except Exception:
                    pass
                continue
        if done:
            p = self.memos.index_progress()
            log.info(f"  🔎 indexed {done} note(s) — {p['done']}/{p['total']} on '{p['model']}'")
        return done

    def _backfill_embeddings(self):
        """Startup: publish the active model, then index. The first slice runs
        inline (so a small corpus is searchable immediately); the rest continues
        in the background loop."""
        self._publish_embed_active()
        n = self._reindex_batch(max_seconds=float(self.cfg.get("reindex_boot_seconds", 30)),
                                limit=int(self.cfg.get("reindex_boot_limit", 50)))
        p = self.memos.index_progress()
        log.info(f"  🔎 retrieval index: {p['done']}/{p['total']} notes on '{p['model']}'"
                 + ("" if p["done"] >= p["total"] else " (continuing in background)"))

    def _start_embed_sidecar(self):
        """Tiny HTTP endpoint (default :5232) that embeds a query string, so the
        PWA can run semantic search without loading its own model. Localhost
        only, single purpose, no state. Runs in a daemon thread."""
        from http.server import BaseHTTPRequestHandler, HTTPServer
        import json as _j
        embedder = self.embedder
        port = int(self.cfg.get("embed_sidecar_port", 5232))

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass
            def do_POST(self):
                try:
                    n = int(self.headers.get("Content-Length", 0))
                    req = _j.loads(self.rfile.read(n))
                    text = req.get("text", "")
                    kind = "passage" if req.get("kind") == "passage" else "query"
                    vec = embedder.encode(text, kind=kind) if text else None
                    body = _j.dumps({"embedding": [float(x) for x in vec] if vec is not None else None,
                                     "model": embedder.tag, "dim": embedder.dim})
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(body.encode())
                except Exception as e:
                    self.send_response(500); self.end_headers()
                    self.wfile.write(_j.dumps({"error": str(e)}).encode())

        def _serve():
            try:
                HTTPServer(("127.0.0.1", port), H).serve_forever()
            except Exception as e:
                log.warning(f"embed sidecar failed to start on :{port}: {e}")

        threading.Thread(target=_serve, name="embed-sidecar", daemon=True).start()
        log.info(f"  🔎 embed sidecar on 127.0.0.1:{port} (semantic search ready)")
