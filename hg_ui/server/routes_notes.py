"""Notes API: list, create, read, update, delete, plus pin/archive/hide, enrichment mode, daily note, templates and share-target."""
import sqlite3
from flask import jsonify, request, abort

import mediastore as _ms

from .core import DB_PATH, MAX_BODY_CHARS, _broadcast, _hash, _nanoid, app, get_db, log
from .highlights import strip_hl
from .notes_common import _enqueue_calendar_deletions, _note_filters, _note_row
from .trash import _trash_put


@app.route("/api/notes", methods=["GET"])
def list_notes():
    db = get_db()
    tag    = request.args.get("tag")
    status = request.args.get("status")
    q      = request.args.get("q", "").strip()
    view   = request.args.get("filter", "")   # ""|archived|hidden
    limit  = min(int(request.args.get("limit", 50)), 200)
    offset = int(request.args.get("offset", 0))

    where, params = ["1=1"], []
    # Archive/Hidden visibility. Default main list shows neither. A search query
    # (q) searches across everything EXCEPT hidden (hidden stays private unless
    # you're explicitly in the Hidden view).
    if view == "archived":
        where.append("n.archived = 1")
    elif view == "hidden":
        where.append("n.hidden = 1")
    else:
        where.append("COALESCE(n.archived,0) = 0")
        where.append("COALESCE(n.hidden,0) = 0")
    _ff, _fp = _note_filters(request.args)
    where.extend(_ff); params.extend(_fp)
    if status:
        where.append("COALESCE(ns.status,'raw') = ?")
        params.append(status)
    if q:
        where.append("(n.body LIKE ? OR e.title LIKE ? OR e.summary LIKE ?)")
        lq = f"%{q}%"
        params.extend([lq, lq, lq])

    sql = f"""
        SELECT n.*, COALESCE(ns.status,'raw') as _status,
               e.title as _title
        FROM notes n
        LEFT JOIN note_state ns ON ns.note_id = n.id
        LEFT JOIN enrichments e ON e.note_id = n.id
            AND e.id = (SELECT id FROM enrichments WHERE note_id=n.id
                        ORDER BY enriched_at DESC, rowid DESC LIMIT 1)
        WHERE {' AND '.join(where)}
        ORDER BY n.pinned DESC, n.updated_at DESC
        LIMIT ? OFFSET ?
    """
    rows = db.execute(sql, params + [limit, offset]).fetchall()
    # Total count for pagination. The list SELECT joins note_state + enrichments
    # to decorate each row, but the COUNT only needs the tables the WHERE clause
    # actually references — joining the others makes this a full-scan with a
    # correlated subquery on EVERY list load (measured: 181ms at 100k vs 10ms).
    # Only `status` filters on the note_state join; only `q` filters on the
    # enrichments join. Add each join solely when its column is used.
    count_joins = ""
    if status:
        count_joins += " LEFT JOIN note_state ns ON ns.note_id = n.id"
    if q:
        count_joins += (" LEFT JOIN enrichments e ON e.note_id = n.id AND e.id = "
                        "(SELECT id FROM enrichments WHERE note_id=n.id "
                        "ORDER BY enriched_at DESC, rowid DESC LIMIT 1)")
    total = db.execute(
        f"SELECT COUNT(DISTINCT n.id) FROM notes n{count_joins} "
        f"WHERE {' AND '.join(where)}", params).fetchone()[0]

    notes = []
    for row in rows:
        n = _note_row(row)
        notes.append(n)

    return jsonify({"notes": notes, "total": total, "limit": limit, "offset": offset})

@app.route("/api/notes", methods=["POST"])
def create_note():
    db = get_db()
    data = request.get_json(force=True) or {}
    body = (data.get("body") or "").strip()
    nt_req = data.get("note_type")
    nt_req = nt_req if nt_req in ("text", "checklist", "event") else "text"
    # A Quick Event carries a structured reminder — treat it as an 'event' note
    # so the agent auto-approves it (the user authored the details; there's
    # nothing to review), while still enriching tags/title/links around it.
    if isinstance(data.get("reminder"), dict) and (data["reminder"].get("title") or "").strip():
        nt_req = "event"
    # A brand-new checklist may start empty (the user adds items after); text
    # notes still require content.
    # A voice / photo / sketch note starts empty — its content is the attachment
    # that is uploaded next — so `media` lifts the "needs text" rule.
    is_media = data.get("media") in ("voice", "photo", "sketch", "video", True)
    if not body and nt_req != "checklist" and not is_media:
        return jsonify({"error": "body is required"}), 400
    if len(body) > MAX_BODY_CHARS:
        return jsonify({"error": f"note too long (max {MAX_BODY_CHARS:,} characters)"}), 413

    # Offline-first clients supply their own id so outbox retries are
    # idempotent: replaying a create that already landed returns the existing
    # note instead of duplicating it.
    import re as _re
    req_id = (data.get("id") or "").strip()
    if req_id and _re.fullmatch(r"[A-Za-z0-9]{6,24}", req_id):
        existing = db.execute("SELECT * FROM notes WHERE id=?", (req_id,)).fetchone()
        if existing:
            return jsonify(_note_row(existing)), 200
        nid = req_id
    else:
        nid = _nanoid(8)
    db.execute("INSERT INTO notes (id, body, visibility, note_type, enrich_optout, meta_only) "
               "VALUES (?,?,?,?,?,?)",
               (nid, body, data.get("visibility", "private"), nt_req,
                1 if data.get("skip_enrich") else 0,
                1 if data.get("meta_only") else 0))
    db.execute("INSERT INTO note_state (note_id, status, body_hash) VALUES (?,?,?)",
               (nid, "raw", _hash(body)))
    # tags from body (#tag syntax)
    import re
    for tag in set(t.lower() for t in re.findall(r"#(\w+)", body)):
        db.execute("INSERT OR IGNORE INTO tags (note_id, tag) VALUES (?,?)", (nid, tag))

    # Structured reminder (from the Quick Event flow): seed the reminders table
    # directly so a calendar event doesn't depend on the model re-parsing the
    # date out of the note text. The agent's approval path picks this up and
    # creates the Google Calendar event (when calendar_enabled).
    rem = data.get("reminder")
    if isinstance(rem, dict) and (rem.get("title") or "").strip():
        db.execute(
            "INSERT INTO reminders (id, note_id, title, datetime_hint, "
            "datetime_resolved, description, source) VALUES (?,?,?,?,?,?,'user')",
            (_nanoid(10), nid, rem["title"].strip(),
             (rem.get("datetime_hint") or "").strip(),
             (rem.get("datetime_resolved") or "").strip(),
             (rem.get("description") or "").strip()))
    db.commit()

    note = _note_row(db.execute("SELECT * FROM notes WHERE id=?", (nid,)).fetchone())
    _broadcast("note_created", note)
    log.info(f"created note {nid}")
    return jsonify(note), 201

@app.route("/api/notes/<nid>", methods=["GET"])
def get_note(nid):
    db = get_db()
    row = db.execute("SELECT * FROM notes WHERE id=?", (nid,)).fetchone()
    if not row: abort(404)
    return jsonify(_note_row(row))

@app.route("/api/notes/<nid>", methods=["PATCH"])
def update_note(nid):
    db = get_db()
    row = db.execute("SELECT * FROM notes WHERE id=?", (nid,)).fetchone()
    if not row: abort(404)
    data = request.get_json(force=True) or {}

    if "title" in data:
        # The person's own title: wins over the agent's, is never overwritten by it, and an empty one hands the
        # title back to the agent. It does not touch the body, so it never queues the note for processing.
        ut = " ".join(str(data.get("title") or "").split())[:120]
        db.execute("UPDATE notes SET user_title=? WHERE id=?", (ut, nid))

    if "body" in data:
        new_body = data["body"].strip()
        if len(new_body) > MAX_BODY_CHARS:
            return jsonify({"error": f"note too long (max {MAX_BODY_CHARS:,} characters)"}), 413
        st = db.execute("SELECT status FROM note_state WHERE note_id=?", (nid,)).fetchone()
        cur_status = st["status"] if st else "raw"
        # Detect a HIGHLIGHT-only change up front: adding/erasing/recolouring a
        # ==highlight== changes only presentation markers, never the words. Such
        # a change should neither create an 'edit' revision nor re-run the agent.
        _strip_hl = strip_hl
        prev_body = (row["body"] or "")
        annotation_only = (prev_body != new_body
                           and _strip_hl(prev_body) == _strip_hl(new_body))
        # Archive the approved text once per edit-cycle: only when leaving the
        # 'approved' state (the debounced autosave means later keystrokes arrive
        # while status is already 'force', which must NOT create more snapshots).
        # Highlight-only changes never get a revision.
        if (not annotation_only) and cur_status == "approved" and new_body != prev_body:
            db.execute(
                "INSERT INTO revisions (id, note_id, body, kind) VALUES (?,?,?,?)",
                (_nanoid(10), nid, prev_body, "edit"))
        db.execute("UPDATE notes SET body=? WHERE id=?", (new_body, nid))
        new_hash = _hash(new_body)
        # Editing an enriched note (approved/pending/force) queues re-processing
        # → 'force'. Only a note that was never enriched stays 'raw'. Previously
        # this reset an in-flight 'force' note back to 'raw' on the next
        # keystroke, dropping it out of the processing queue.
        #
        # CHECKLISTS are exempt: once enriched (title/tags set), toggling or
        # adding items must NOT re-run the agent every time — a checklist isn't
        # a document being redrafted. It stays 'approved' so the agent leaves it
        # alone. (A brand-new checklist still gets its one enrichment pass.)
        #
        # HIGHLIGHTS (annotation_only, computed above) are exempt too: keep the
        # status stable so a highlight/erase never triggers re-enrichment.
        nt_row = db.execute("SELECT note_type FROM notes WHERE id=?", (nid,)).fetchone()
        is_checklist = bool(nt_row and (nt_row["note_type"] if "note_type" in nt_row.keys() else "text") == "checklist")
        if annotation_only:
            new_status = cur_status
        elif is_checklist and cur_status in ("approved", "pending", "force"):
            new_status = cur_status if cur_status != "force" else "approved"
        else:
            # DECOUPLE durability from interpretation. A plain autosave lands the
            # body and marks the note 'dirty' — persisted, but NOT queued for the
            # ~90 s enrichment. Enrichment is promoted to 'force' only on an
            # explicit commit (editor close or "Run AI now"), so a user taking
            # notes for an hour with pauses never triggers a mid-session run.
            #   raw       → still raw (first enrichment is promoted on commit too)
            #   approved/pending/force/dirty → dirty, unless this call commits
            _committing = str(data.get("commit",
                              request.args.get("commit", ""))).lower() in ("1","true","yes")
            if _committing:
                new_status = "force"          # close / explicit → enrich now
            elif cur_status == "raw":
                # Never enriched. The editor's autosave says so (`autosave`): the note is still being written, so
                # hold it as 'dirty' like any other open note and let the commit (editor close / Run AI now)
                # promote it. Other writers (API, shortcuts) leave it 'raw' and it is enriched as before.
                _auto = str(data.get("autosave", request.args.get("autosave", ""))).lower() in ("1", "true", "yes")
                new_status = "dirty" if _auto else "raw"
            else:
                new_status = "dirty"          # edited, durable, awaiting commit
        try:
            db.execute("""INSERT INTO note_state (note_id, status, body_hash)
                          VALUES (?,?,?)
                          ON CONFLICT(note_id) DO UPDATE SET
                            status=excluded.status, body_hash=excluded.body_hash""",
                       (nid, new_status, new_hash))
        except sqlite3.IntegrityError:
            # Old DB whose note_state CHECK predates 'dirty' (migration deferred
            # because the file was locked at startup). Don't lose the save — the
            # body is already written above; fall back to a legacy-valid status.
            # 'raw'/'force' still round-trips through the agent correctly.
            fallback = "raw" if new_status == "dirty" else new_status
            db.execute("""INSERT INTO note_state (note_id, status, body_hash)
                          VALUES (?,?,?)
                          ON CONFLICT(note_id) DO UPDATE SET
                            status=excluded.status, body_hash=excluded.body_hash""",
                       (nid, fallback, new_hash))
        # re-sync tags
        import re
        db.execute("DELETE FROM tags WHERE note_id=?", (nid,))
        for tag in set(t.lower() for t in re.findall(r"#(\w+)", new_body)):
            db.execute("INSERT OR IGNORE INTO tags (note_id, tag) VALUES (?,?)", (nid, tag))

    if "status" in data:
        allowed = ("raw", "pending", "approved", "force")
        if data["status"] in allowed:
            # An explicit Re-run overrides a bulk-import opt-out, for the same
            # reason Polish does: the agent's enrich_optout gate runs before any
            # decision, so leaving the flag set would make Re-run a no-op.
            if data["status"] == "force":
                db.execute("UPDATE notes SET enrich_optout=0 WHERE id=?", (nid,))
            # ── Adoption happens HERE, atomically with approval ────────────
            # Tapping Approve must transform the note immediately — it cannot
            # depend on the agent being alive. The latest polished text becomes
            # the body in the same transaction; the previous body is archived.
            # The agent's adopt_polished remains as an idempotent fallback and
            # still handles calendar events + state bookkeeping.
            if data["status"] == "approved":
                cur = db.execute("SELECT body, protected FROM notes WHERE id=?",
                                 (nid,)).fetchone()
                enr = db.execute(
                    "SELECT polished, enriched_at FROM enrichments "
                    "WHERE note_id=? ORDER BY enriched_at DESC, rowid DESC LIMIT 1",
                    (nid,)).fetchone()
                polished = ((enr["polished"] if enr else "") or "").strip()
                body_now = (cur["body"] or "").strip() if cur else ""
                keep_verbatim = bool(cur and (cur["protected"] or "```" in body_now))
                # User override: if the client sends an edited adoption body,
                # adopt exactly that (their tweak of the AI text). Falls back
                # to the AI's polished text when no override is provided.
                override = data.get("adopt_body")
                if override is not None:
                    override = str(override).strip()
                # If the user has manually edited this note before (an 'edit'
                # revision exists) and isn't sending a fresh override now, their
                # current body is authoritative — do NOT replace it with the AI
                # rewrite. EXCEPT when the user has explicitly requested an
                # on-demand polish (polish_once flag), which permits one rewrite.
                polish_flag = db.execute(
                    "SELECT value FROM settings WHERE key=?", (f"polish_once:{nid}",)).fetchone()
                polish_once = bool(polish_flag and polish_flag["value"] == "1")
                was_user_edited = (not polish_once) and bool(db.execute(
                    "SELECT 1 FROM revisions WHERE note_id=? AND kind='edit' LIMIT 1",
                    (nid,)).fetchone())
                if polish_once:
                    db.execute("DELETE FROM settings WHERE key=?", (f"polish_once:{nid}",))
                if override:
                    target = override
                elif was_user_edited:
                    target = body_now          # keep the user's text
                else:
                    target = polished          # first adoption of raw capture
                if target and target != body_now and not keep_verbatim:
                    # Label the snapshot by who authored the change: a user
                    # override is "edit" (before your edit); adopting the AI's
                    # polished text verbatim is "pre_adopt" (before AI rewrite).
                    rev_kind = "edit" if override else "pre_adopt"
                    db.execute(
                        "INSERT INTO revisions (id, note_id, body, kind) "
                        "VALUES (?,?,?,?)",
                        (_nanoid(10), nid, body_now, rev_kind))
                    db.execute("UPDATE notes SET body=? WHERE id=?",
                               (target, nid))
                    db.execute(
                        "UPDATE note_state SET body_hash=? WHERE note_id=?",
                        (_hash(target), nid))
                    log.info(f"adopted {'user-edited' if override else 'polished'} "
                             f"text on approval: {nid}")
            # A fresh Re-run (force) resets the agent's retry counter so a note
            # that previously exhausted its attempts gets a clean set of tries.
            db.execute("""INSERT INTO note_state (note_id, status)
                          VALUES (?,?)
                          ON CONFLICT(note_id) DO UPDATE SET status=excluded.status,
                            failures=CASE WHEN excluded.status='force' THEN 0
                                          ELSE failures END""",
                       (nid, data["status"]))

    if "protected" in data:
        db.execute("UPDATE notes SET protected=? WHERE id=?",
                   (1 if data["protected"] else 0, nid))

    if "visibility" in data:
        db.execute("UPDATE notes SET visibility=? WHERE id=?",
                   (data["visibility"], nid))

    if "color_idx" in data:
        v = data["color_idx"]
        if v is None:
            db.execute("UPDATE notes SET color_idx=NULL WHERE id=?", (nid,))
        elif isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 7:
            db.execute("UPDATE notes SET color_idx=? WHERE id=?", (v, nid))
        # else: silently ignored — an out-of-range or malformed value leaves
        # the existing colour (automatic or previously chosen) untouched.

    db.commit()
    note = _note_row(db.execute("SELECT * FROM notes WHERE id=?", (nid,)).fetchone())
    # A status change to "approved" (with or without adoption) is the agent's
    # own lifecycle event — broadcast it as note_enriched so the agent's SSE
    # listener ignores it (it only reacts to note_created / note_updated for
    # USER actions). Broadcasting note_updated here caused the agent to treat
    # approval as a fresh edit and re-run inference. Body edits by the user
    # still go out as note_updated so the agent re-enriches correctly.
    if data.get("status") == "approved":
        _broadcast("note_enriched", note)
    else:
        _broadcast("note_updated", note)
    return jsonify(note)

@app.route("/api/notes/<nid>", methods=["DELETE"])
def delete_note(nid):
    db = get_db()
    if not db.execute("SELECT 1 FROM notes WHERE id=?", (nid,)).fetchone():
        abort(404)
    # Deleting the note cascades its reminders away, so capture any calendar
    # events FIRST — otherwise they'd live on in Google Calendar with nothing
    # left in the app that knows they exist. Queued here, performed by the agent
    # (which owns the credentials).
    try:
        orphans = db.execute(
            "SELECT calendar_event_id, note_id, title FROM reminders "
            "WHERE note_id=? AND calendar_event_id IS NOT NULL "
            "AND calendar_event_id != ''", (nid,)).fetchall()
        if orphans:
            q = _enqueue_calendar_deletions(
                db, [(r["calendar_event_id"], r["note_id"], r["title"]) for r in orphans])
            log.info(f"note {nid} deleted — queued {q} calendar event(s) for removal")
    except Exception as e:
        log.warning(f"couldn't queue calendar cleanup for {nid}: {e}")
    files = [r["filename"] for r in db.execute(
        "SELECT filename FROM attachments WHERE note_id=?", (nid,))]
    permanent = request.args.get("permanent") == "1"
    trashed = False
    if not permanent:
        try:
            trashed = _trash_put(db, nid, files)
        except Exception as e:
            db.rollback()
            log.error(f"couldn't move {nid} to trash — NOT deleting: {e}")
            return jsonify({"error": "couldn't move this note to Trash"}), 500
    db.execute("DELETE FROM notes WHERE id=?", (nid,))
    db.commit()
    if files and not trashed:
        _ms.remove_files(DB_PATH, files)       # rows cascaded away; take the files too
    _broadcast("note_deleted", {"id": nid})
    return jsonify({"deleted": nid, "trashed": trashed})

@app.route("/share-target", methods=["GET"])
def share_target():
    """Android share-sheet entry: create a note from shared text, then open."""
    from flask import redirect
    text = (request.args.get("text") or "").strip()
    title = (request.args.get("title") or "").strip()
    url = (request.args.get("url") or "").strip()
    parts = [p for p in (title, text, url) if p]
    body = "\n\n".join(parts).strip()
    if body:
        db = get_db()
        nid = _nanoid()
        if len(body) > MAX_BODY_CHARS:
            body = body[:MAX_BODY_CHARS]
        db.execute("INSERT INTO notes (id, body) VALUES (?,?)", (nid, body))
        db.execute("INSERT INTO note_state (note_id, status) VALUES (?, 'raw')", (nid,))
        db.commit()
        note = db.execute("SELECT * FROM notes WHERE id=?", (nid,)).fetchone()
        _broadcast("note_created", _note_row(note))
        return redirect(f"/?shared={nid}")
    return redirect("/")

@app.route("/share-target", methods=["POST"])
def share_target_post():
    """Share sheet with files: photos and audio (plus any text) become one new note.
    Unsupported or oversized files are skipped, not fatal; the note is still created."""
    from flask import redirect
    f = request.form
    body = "\n\n".join(p for p in ((f.get("title") or "").strip(), (f.get("text") or "").strip(),
                                    (f.get("url") or "").strip()) if p)[:MAX_BODY_CHARS]
    files = request.files.getlist("media")[:8]
    if not body and not files:
        return redirect("/")
    db = get_db()
    nid = _nanoid()
    db.execute("INSERT INTO notes (id, body) VALUES (?,?)", (nid, body))
    db.execute("INSERT INTO note_state (note_id, status, body_hash) VALUES (?, 'raw', ?)", (nid, _hash(body)))
    saved = 0
    for fl in files:
        mime = _ms.norm_mime(fl.mimetype)
        kind = "image" if mime in _ms.IMAGE_MIMES else "video" if mime in _ms.VIDEO_MIMES else "audio" if mime in _ms.AUDIO_MIMES else None
        if not kind:
            continue
        aid = _ms.new_id()
        try:
            fn, size = _ms.save_stream(DB_PATH, aid, kind, mime, fl.stream)
        except _ms.MediaError as e:
            log.info(f"share-target: skipped a file ({e})")
            continue
        db.execute("INSERT INTO attachments (id, note_id, kind, mime, filename, size, transcript, "
                   "transcript_status) VALUES (?,?,?,?,?,?,?,?)",
                   (aid, nid, kind, mime, fn, size, "", "pending"))
        saved += 1
    if not body and not saved:         # nothing usable arrived: leave no empty note behind
        db.execute("DELETE FROM note_state WHERE note_id=?", (nid,))
        db.execute("DELETE FROM notes WHERE id=?", (nid,))
        db.commit()
        return redirect("/")
    db.commit()
    note = db.execute("SELECT * FROM notes WHERE id=?", (nid,)).fetchone()
    _broadcast("note_created", _note_row(note))
    return redirect(f"/?shared={nid}")

@app.route("/api/notes/<nid>/pin", methods=["POST"])
def toggle_pin(nid):
    db = get_db()
    cur = db.execute("SELECT pinned FROM notes WHERE id=?", (nid,)).fetchone()
    if not cur: return jsonify({"error": "not found"}), 404
    new_val = 0 if cur["pinned"] else 1
    db.execute("UPDATE notes SET pinned=? WHERE id=?", (new_val, nid))
    db.commit()
    return jsonify({"ok": True, "pinned": new_val})

@app.route("/api/notes/<nid>/archive", methods=["POST"])
def toggle_archive(nid):
    """Archive/unarchive a note. Organizational only — keeps the note but hides
    it from the main list. Archiving also unpins (an archived note shouldn't
    sit pinned at the top)."""
    db = get_db()
    cur = db.execute("SELECT archived FROM notes WHERE id=?", (nid,)).fetchone()
    if not cur: return jsonify({"error": "not found"}), 404
    new_val = 0 if cur["archived"] else 1
    if new_val:
        db.execute("UPDATE notes SET archived=1, pinned=0 WHERE id=?", (nid,))
    else:
        db.execute("UPDATE notes SET archived=0 WHERE id=?", (nid,))
    db.commit()
    return jsonify({"ok": True, "archived": new_val})

@app.route("/api/notes/<nid>/hide", methods=["POST"])
def toggle_hide(nid):
    """Hide/unhide a note. VISUAL PRIVACY ONLY — the note is blurred until
    tapped and kept out of the main list and search, but the body remains
    plaintext in the database (this is not encryption). Hiding also unpins."""
    db = get_db()
    cur = db.execute("SELECT hidden FROM notes WHERE id=?", (nid,)).fetchone()
    if not cur: return jsonify({"error": "not found"}), 404
    new_val = 0 if cur["hidden"] else 1
    if new_val:
        db.execute("UPDATE notes SET hidden=1, pinned=0 WHERE id=?", (nid,))
    else:
        db.execute("UPDATE notes SET hidden=0 WHERE id=?", (nid,))
    db.commit()
    return jsonify({"ok": True, "hidden": new_val})

@app.route("/api/notes/<nid>/enrich-mode", methods=["POST"])
def set_enrich_mode(nid):
    """Set the enrichment mode for a note: full | metadata | off.

    One control, three coherent states, mapped onto the two underlying flags:
      full     → enrich_optout=0, meta_only=0  (metadata + polished rewrite)
      metadata → enrich_optout=0, meta_only=1  (describe/tag, body verbatim)
      off      → enrich_optout=1                (no AI at all)
    Setting a note to 'full' after it was skipped is the "opt in later" path;
    the agent re-enriches on the next pass.
    """
    db = get_db()
    if not db.execute("SELECT 1 FROM notes WHERE id=?", (nid,)).fetchone():
        abort(404)
    mode = (request.get_json(force=True) or {}).get("mode")
    # This control is AUTHORITATIVE over every flag that affects enrichment,
    # including `protected` — which independently forces metadata-only in the
    # agent. Leaving it set while the user picks "Full" would make the control
    # lie: it would say Full and behave as Metadata.
    if mode == "full":
        db.execute("UPDATE notes SET enrich_optout=0, meta_only=0, protected=0 "
                   "WHERE id=?", (nid,))
    elif mode == "metadata":
        db.execute("UPDATE notes SET enrich_optout=0, meta_only=1 WHERE id=?", (nid,))
    elif mode == "off":
        db.execute("UPDATE notes SET enrich_optout=1 WHERE id=?", (nid,))
    else:
        return jsonify({"error": "mode must be full|metadata|off"}), 400
    # Turning enrichment on for a note that's mid-review shouldn't strand it —
    # settle any pending review, matching the skip-enrich behaviour.
    if mode in ("full", "metadata"):
        st = db.execute("SELECT status FROM note_state WHERE note_id=?", (nid,)).fetchone()
        if st and st["status"] == "pending":
            db.execute("UPDATE note_state SET status='approved' WHERE note_id=?", (nid,))
    db.commit()
    row = db.execute("SELECT enrich_optout, meta_only FROM notes WHERE id=?", (nid,)).fetchone()
    return jsonify({"ok": True, "mode": mode,
                    "enrich_optout": row["enrich_optout"], "meta_only": row["meta_only"]})

@app.route("/api/notes/<nid>/skip-enrich", methods=["POST"])
def toggle_skip_enrich(nid):
    """Toggle whether the agent may auto-enrich this note in FUTURE.

    Sets notes.enrich_optout, the same flag the bulk importer uses — the agent
    checks it in _process_one and skips the note entirely (no Gemma call, no
    ~72s of phone heat).

    Scope is deliberately narrow: this stops future work. It does NOT hide or
    delete anything already produced — the title and tags are what make a note
    findable, and a switch about the future shouldn't rewrite the past. If a
    suggestion is mid-review when this is turned on, the review is settled so
    the note is never stranded in 'pending' with no way forward.

    This is about ENRICHMENT (the expensive LLM pass), not embeddings. A skipped
    note is still embedded — milliseconds, not a minute — so it stays findable by
    semantic search and can still appear in connections. An explicit
    ⋯ → Re-run/Polish clears the flag, since that's an unambiguous "enrich now".
    """
    db = get_db()
    cur = db.execute("SELECT enrich_optout FROM notes WHERE id=?", (nid,)).fetchone()
    if not cur:
        return jsonify({"error": "not found"}), 404
    new_val = 0 if (cur["enrich_optout"] or 0) else 1
    db.execute("UPDATE notes SET enrich_optout=? WHERE id=?", (new_val, nid))
    if new_val:
        # Turning skip ON while a suggestion is awaiting review would strand the
        # note: 'pending' with no way forward (Re-run and Polish only appear once
        # a note is approved). Ticking "skip" reads as "I'm done deciding about
        # this note", so settle the review rather than freeze it. No action is
        # left with no exit.
        st = db.execute("SELECT status FROM note_state WHERE note_id=?",
                        (nid,)).fetchone()
        if st and st["status"] == "pending":
            db.execute("UPDATE note_state SET status='approved' WHERE note_id=?",
                       (nid,))
    db.commit()
    return jsonify({"ok": True, "enrich_optout": new_val})

@app.route("/api/notes/<nid>/polish", methods=["POST"])
def request_polish(nid):
    """On-demand: let the agent rewrite a user-edited note ONCE. Sets a
    one-shot marker (an enrichment-clearing 'edit' removal would be wrong, so
    we use a settings flag) and forces re-processing that WILL produce polished."""
    db = get_db()
    if not db.execute("SELECT 1 FROM notes WHERE id=?", (nid,)).fetchone():
        return jsonify({"error": "not found"}), 404
    db.execute("INSERT INTO settings (key, value) VALUES (?, '1') "
               "ON CONFLICT(key) DO UPDATE SET value='1'", (f"polish_once:{nid}",))
    # An explicit user request overrides a bulk-import opt-out — otherwise the
    # agent's enrich_optout gate would silently swallow this and Polish would
    # appear to do nothing.
    db.execute("UPDATE notes SET enrich_optout=0 WHERE id=?", (nid,))
    db.execute("INSERT INTO note_state (note_id, status) VALUES (?, 'force') "
               "ON CONFLICT(note_id) DO UPDATE SET status='force'", (nid,))
    db.commit()
    note = db.execute("SELECT * FROM notes WHERE id=?", (nid,)).fetchone()
    _broadcast("note_updated", _note_row(note))
    return jsonify({"ok": True})

@app.route("/api/backlinks", methods=["POST"])
def set_backlinks():
    """Replace a note's outgoing [[links]] (parsed client-side to note ids)."""
    db = get_db()
    data = request.get_json(force=True) or {}
    src = data.get("src_id"); dsts = data.get("dst_ids", [])
    if not src: return jsonify({"error": "src_id required"}), 400
    db.execute("DELETE FROM backlinks WHERE src_id=?", (src,))
    for d in dsts:
        if d and d != src and db.execute("SELECT 1 FROM notes WHERE id=?", (d,)).fetchone():
            db.execute("INSERT OR IGNORE INTO backlinks (src_id, dst_id) VALUES (?,?)", (src, d))
    db.commit()
    return jsonify({"ok": True})

@app.route("/api/notes/search-titles", methods=["GET"])
def search_titles():
    """Autocomplete for [[wiki]] linking — id + title/snippet for matching notes."""
    db = get_db()
    q = (request.args.get("q") or "").strip().lower()
    rows = db.execute(
        "SELECT n.id, (SELECT title FROM enrichments WHERE note_id=n.id "
        "ORDER BY enriched_at DESC, rowid DESC LIMIT 1) t, n.body b FROM notes n "
        "ORDER BY n.pinned DESC, n.updated_at DESC LIMIT 200").fetchall()
    out = []
    for r in rows:
        label = (r["t"] or (r["b"] or "").strip()[:50] or "Untitled")
        if not q or q in label.lower() or q in (r["b"] or "").lower():
            out.append({"id": r["id"], "title": label})
        if len(out) >= 12: break
    return jsonify({"notes": out})

@app.route("/api/daily", methods=["POST"])
def get_or_create_daily():
    """Get today's daily note, creating it if absent. One per calendar day."""
    db = get_db()
    from datetime import date
    today = date.today().isoformat()
    row = db.execute("SELECT * FROM notes WHERE is_daily=1 AND daily_date=?", (today,)).fetchone()
    if row:
        return jsonify(_note_row(row))
    nid = _nanoid()
    pretty = date.today().strftime("%A, %B %-d")
    db.execute("INSERT INTO notes (id, body, is_daily, daily_date) VALUES (?,?,?,?)",
               (nid, f"# {pretty}\n\n", 1, today))
    db.execute("INSERT INTO note_state (note_id, status) VALUES (?, 'raw')", (nid,))
    db.commit()
    note = db.execute("SELECT * FROM notes WHERE id=?", (nid,)).fetchone()
    _broadcast("note_created", _note_row(note))
    return jsonify(_note_row(note))

@app.route("/api/templates", methods=["GET", "POST"])
def templates():
    db = get_db()
    if request.method == "POST":
        data = request.get_json(force=True) or {}
        name = (data.get("name") or "").strip()
        body = data.get("body") or ""
        if not name: return jsonify({"error": "name required"}), 400
        tid = _nanoid()
        db.execute("INSERT INTO templates (id, name, body) VALUES (?,?,?)", (tid, name, body))
        db.commit()
        return jsonify({"ok": True, "id": tid})
    rows = db.execute("SELECT id, name, body FROM templates ORDER BY name").fetchall()
    return jsonify({"templates": [dict(r) for r in rows]})

@app.route("/api/templates/<tid>", methods=["DELETE"])
def delete_template(tid):
    db = get_db()
    db.execute("DELETE FROM templates WHERE id=?", (tid,))
    db.commit()
    return jsonify({"ok": True})
