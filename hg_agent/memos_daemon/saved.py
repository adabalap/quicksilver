"""Saved captures: what a person pressed Save on in Medha's brief.

The whole job here is to move a save from Medha's outbox into Quicksilver's
`saved_captures` inbox without losing it, duplicating it, or changing it.

WHAT THIS DELIBERATELY DOES NOT DO

It does not create a note. It does not pick a type, write a title, split a
list into items, or run the model over the text. The person chose to decide
what a capture becomes in Quicksilver, and to keep it verbatim, and both of
those are enforced by this module doing less: anything it wrote into
`notes` would be the agent deciding, and anything it changed would be an
edit nobody asked for. The note is created later, by the app, when someone
picks "note", "checklist", "reminder" or "task".

ORDER OF OPERATIONS, BECAUSE IT IS THE WHOLE GUARANTEE

    1. read a page from Medha
    2. insert it here and COMMIT
    3. only then ack it, which is what lets Medha delete its copy

Reverse 2 and 3 and a crash between them loses the save for good: Medha has
forgotten it and we never wrote it. In this order a crash between them means
Medha sends the same rows again next time, and `fp` -- the primary key --
makes the second insert a no-op. At-least-once delivery, exactly-once effect.

WHEN THE TABLE IS NOT THERE YET

The app owns the schema. If this agent is newer than the app's last start,
`saved_captures` does not exist, and the right move is to not drain at all:
the saves wait in Medha, which keeps them for months, and nothing is lost by
waiting for the app to migrate. Creating the table here instead would put a
second copy of its DDL in a second repo, and the two would drift.
"""

import logging
import sqlite3

log = logging.getLogger(__name__)

# One drain never runs away: at Medha's page cap this is far more than its
# whole outbox, so a loop that has not finished by now is a loop that will not.
MAX_ROUNDS = 20


def table_ready(db) -> bool:
    try:
        return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                          "AND name='saved_captures'").fetchone() is not None
    except sqlite3.Error:
        return False


def _int(v):
    """An integer from JSON, or None. bool is an int in Python and is refused."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    return None


def normalise(item):
    """One item from GET /saved as a row to store, or None if it is not one.

    Verbatim means the strings go through untouched -- no strip(), not even of
    trailing whitespace. A row is refused only for what would make it
    unstorable or unidentifiable: no fingerprint, or no id to ack it by.
    """
    if not isinstance(item, dict):
        return None
    fp = item.get("fp")
    rid = _int(item.get("id"))
    if not isinstance(fp, str) or not fp or rid is None:
        return None
    s = lambda k: item.get(k) if isinstance(item.get(k), str) else ""
    return {
        "id": rid,
        "fp": fp,
        "origin": s("origin"),
        "title": s("title"),
        "body": s("text"),
        "posted_at": _int(item.get("posted_at")),
        "saved_at": _int(item.get("saved_at")),
        "truncated": 1 if item.get("truncated") is True else 0,
    }


def store(db, rows) -> int:
    """Insert rows, ignoring any already here. Returns how many were new.

    Commits before returning, so a caller that acks afterwards is acking
    rows that are on disk.
    """
    new = 0
    for r in rows:
        cur = db.execute(
            "INSERT OR IGNORE INTO saved_captures "
            "(fp, origin, title, body, posted_at, saved_at, truncated) "
            "VALUES (?,?,?,?,?,?,?)",
            (r["fp"], r["origin"], r["title"], r["body"],
             r["posted_at"], r["saved_at"], r["truncated"]))
        new += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
    db.commit()
    return new


def drain(client, db, max_rounds=MAX_ROUNDS):
    """Move everything Medha is holding into the inbox. Returns a summary dict.

    {"stored": new rows, "seen": rows read, "acked": rows Medha dropped,
     "skipped": reason or None}

    Errors from Medha are reported, not raised: a 404 is a Medha older than
    the route, a 403 is the capture capability not granted, and neither is
    something to retry every tick at the top of the log.
    """
    out = {"stored": 0, "seen": 0, "acked": 0, "skipped": None}
    if not table_ready(db):
        out["skipped"] = "no saved_captures table yet -- restart the app to migrate"
        return out

    for _ in range(max(1, int(max_rounds))):
        try:
            page = client.saved(since=0)
        except Exception as e:  # BriefError or transport
            status = getattr(e, "status", 0)
            out["skipped"] = {404: "this Medha has no saved route",
                              403: "the capture capability is not granted"}.get(
                                  status, f"could not reach Medha: {e}")
            return out

        if not isinstance(page, dict):
            out["skipped"] = "Medha answered with something that is not an object"
            return out
        raw = page.get("items") or []
        if not isinstance(raw, list):
            raw = []
        rows = [r for r in (normalise(i) for i in raw) if r is not None]
        out["seen"] += len(raw)
        if not raw:
            break

        out["stored"] += store(db, rows)       # committed before the ack below

        # Ack through the highest id READ, not the highest id stored -- and
        # note what that means: a malformed item is DROPPED. That is the one
        # place a save can be lost here, so the reasoning matters.
        #
        # An ack is cumulative ("everything up to N"), so there is no way to
        # ack the good rows around a bad one and leave the bad one waiting.
        # The only alternative to dropping it is to stop acking at it, which
        # re-delivers it and everything after it forever and wedges every
        # later save behind it. One unreadable row versus all future saves.
        # Medha computes `fp` for every row it serves, so this should not
        # happen; if it does, it is a shape change, and the log says so.
        ids = [_int(i.get("id")) for i in raw if isinstance(i, dict)]
        ids = [i for i in ids if i is not None]
        if len(rows) < len(raw):
            log.warning("saved: %d malformed item(s) dropped", len(raw) - len(rows))
        if not ids:
            break
        try:
            ack = client.ack_saved(max(ids))
            out["acked"] += int((ack or {}).get("acked") or 0)
        except Exception as e:
            # Stored but not acked: next drain re-reads and fp absorbs it.
            log.warning("saved: stored %d but ack failed (%s); will re-read", len(rows), e)
            break
        if not page.get("more"):
            break
    return out
