#!/usr/bin/env python3
"""Saved captures: moving a Save from Medha's outbox into Quicksilver's inbox.

The checks that matter are about LOSS and ALTERATION, because those are the
two promises this path makes:

  - a save is acked only AFTER it is committed here, so a crash re-delivers
    rather than loses it
  - a re-delivered save lands on the row it already made (fp is the key)
  - the text arrives byte for byte -- not even trailing whitespace is touched
  - nothing is written into `notes`: what a save becomes is the person's call
  - with no table yet, nothing is drained and nothing is acked, so the saves
    wait safely in Medha instead of being taken and dropped

    python saved_test.py        (needs ../hg_ui beside the agent)
"""
import os, sys, sqlite3, tempfile, importlib.util, logging

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
G, R, Y, X = "\033[32m", "\033[31m", "\033[33m", "\033[0m"
_ok = _bad = 0


def check(name, cond, detail=""):
    global _ok, _bad
    if cond: _ok += 1; print(f"  {G}✓{X} {name}")
    else: _bad += 1; print(f"  {R}✗ {name}{X}" + (f"  → {detail}" if detail else ""))


def section(t): print(f"\n{Y}▸ {t}{X}")


logging.disable(logging.CRITICAL)
from memos_daemon import saved as sv
from memos_daemon.medha_brief import BriefError

LIST = "Onion\nAmla komma\nBanana 1/2 dozen\nFlowers\nattukulu\nBellam"


def item(i, fp=None, title="కళ్యాణి", text=LIST, origin="WhatsApp", truncated=False):
    return {"id": i, "fp": fp or f"fp{i:014d}", "origin": origin, "title": title,
            "text": text, "posted_at": 1_700_000_000_000 + i, "saved_at": 1_700_000_900_000 + i,
            "truncated": truncated}


class FakeMedha:
    """Holds an outbox the way Medha does: rows leave only on ack."""

    def __init__(self, rows, page=100, fail_ack=False, err=None):
        self.rows = list(rows)
        self.page = page
        self.fail_ack = fail_ack
        self.err = err
        self.calls = []

    def saved(self, since=0, limit=None):
        self.calls.append(("saved", since))
        if self.err:
            raise self.err
        pending = [r for r in self.rows if isinstance(r, dict) and r.get("id", 0) > since] \
            + [r for r in self.rows if not isinstance(r, dict)]
        page = pending[: self.page]
        last = max([r["id"] for r in page if isinstance(r, dict) and isinstance(r.get("id"), int)] or [0])
        head = max([r["id"] for r in self.rows if isinstance(r, dict) and isinstance(r.get("id"), int)] or [0])
        return {"items": page, "cursor": last or head, "more": len(page) >= self.page and last < head, "head": head}

    def ack_saved(self, through):
        self.calls.append(("ack", through))
        if self.fail_ack:
            raise BriefError(0, "transport", "connection reset")
        before = len(self.rows)
        self.rows = [r for r in self.rows if not (isinstance(r, dict) and isinstance(r.get("id"), int)
                                                  and r["id"] <= through)]
        return {"acked": before - len(self.rows), "pending": len(self.rows)}


# ───────────────────────────── normalise

section("What a save looks like on the way in")
n = sv.normalise(item(1))
check("a real item is accepted", n is not None)
check("the title is kept verbatim", n and n["title"] == "కళ్యాణి")
check("the body is kept verbatim", n and n["body"] == LIST)
ws = sv.normalise(item(2, text="  Onion  \n\n  Bellam  \n"))
check("not even surrounding whitespace is touched", ws and ws["body"] == "  Onion  \n\n  Bellam  \n",
      repr(ws and ws["body"]))
check("a missing fingerprint is refused", sv.normalise({**item(3), "fp": ""}) is None)
check("a missing id is refused", sv.normalise({**item(4), "id": None}) is None)
check("a boolean is not an id", sv.normalise({**item(5), "id": True}) is None)
check("a non-object is refused", sv.normalise("nope") is None and sv.normalise(None) is None)
check("truncated only counts when it is literally true",
      sv.normalise({**item(6), "truncated": "yes"})["truncated"] == 0
      and sv.normalise({**item(7), "truncated": True})["truncated"] == 1)
check("missing strings become empty, not None",
      sv.normalise({"id": 8, "fp": "x"})["title"] == "" and sv.normalise({"id": 8, "fp": "x"})["origin"] == "")

# ───────────────────────────── on a real database

PWA = os.path.abspath(os.path.join(HERE, "..", "hg_ui", "app.py"))
if not os.path.exists(PWA):
    print("hg_ui not found beside the agent — database tests skipped")
    print(f"\n{_ok} passed, {_bad} failed"); sys.exit(1 if _bad else 0)

tmp = tempfile.mkdtemp()
dbp = os.path.join(tmp, "n.db")
os.environ["QS_DB"] = dbp
spec = importlib.util.spec_from_file_location("pwa_app", PWA)
pwa = importlib.util.module_from_spec(spec); sys.modules["pwa_app"] = pwa; spec.loader.exec_module(pwa)
pwa.init_db()
db = sqlite3.connect(dbp); db.row_factory = sqlite3.Row


def count(t="saved_captures"):
    return db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]


section("The app's schema is the one this writes into")
check("the app created saved_captures", sv.table_ready(db))
cols = {r[1] for r in db.execute("PRAGMA table_info(saved_captures)")}
check("with every column this module writes",
      {"fp", "origin", "title", "body", "posted_at", "saved_at", "truncated"} <= cols, str(cols))
check("and the decision columns the app uses", {"decision", "note_id", "decided_at"} <= cols)
notes_before = count("notes")

section("Draining")
m = FakeMedha([item(1), item(2), item(3)])
out = sv.drain(m, db)
check("every save lands in the inbox", out["stored"] == 3 and count() == 3, str(out))
check("Medha is told it can drop them", out["acked"] == 3 and m.rows == [], str(out))
check("…in one ack through the highest id", ("ack", 3) in m.calls, str(m.calls))
check("nothing was written into notes — that is the person's decision",
      count("notes") == notes_before)
check("every save is waiting for a decision",
      db.execute("SELECT COUNT(*) FROM saved_captures WHERE decision IS NULL").fetchone()[0] == 3)
r = db.execute("SELECT * FROM saved_captures WHERE fp=?", (item(1)["fp"],)).fetchone()
check("the stored body is byte-for-byte the notification", r["body"] == LIST)
check("the stored title is too", r["title"] == "కళ్యాణి")
check("the origin and both instants are kept",
      r["origin"] == "WhatsApp" and r["posted_at"] == 1_700_000_000_001 and r["saved_at"] == 1_700_000_900_001)

section("A re-delivered save does not duplicate")
# The ack failed last time, so Medha sends the same rows again.
m = FakeMedha([item(1), item(2), item(9)])
out = sv.drain(m, db)
check("only the new one is stored", out["stored"] == 1 and count() == 4, str(out))
check("…but all of them are acked, so Medha stops resending", m.rows == [], str(m.rows))

section("Ack comes only after the rows are on disk")
m = FakeMedha([item(20), item(21)], fail_ack=True)
out = sv.drain(m, db)
check("rows are stored even though the ack failed", count() == 6, str(out))
check("Medha still holds them, so nothing is lost", len(m.rows) == 2)
m2 = FakeMedha([item(20), item(21)])
out = sv.drain(m2, db)
check("the next drain re-reads them and adds nothing", out["stored"] == 0 and count() == 6, str(out))
check("…and this time they are acked", m2.rows == [])


class ExplodingDB:
    """A database that fails on commit, to prove the ack never runs."""

    def __init__(self, real): self.real = real
    def execute(self, *a, **k): return self.real.execute(*a, **k)
    def commit(self): raise sqlite3.OperationalError("database is locked")


m = FakeMedha([item(30)])
try:
    sv.drain(m, ExplodingDB(db))
    blew = False
except sqlite3.OperationalError:
    blew = True
db.rollback()
check("a failed commit surfaces rather than being swallowed", blew)
check("…and no ack was sent, so Medha still has the save",
      not any(c[0] == "ack" for c in m.calls) and len(m.rows) == 1, str(m.calls))

section("When Medha cannot help")
out = sv.drain(FakeMedha([], err=BriefError(404, "", "Not Found")), db)
check("an older Medha is reported, not raised", "no saved route" in (out["skipped"] or ""), str(out))
out = sv.drain(FakeMedha([], err=BriefError(403, "forbidden", "")), db)
check("a missing capability is named", "capability" in (out["skipped"] or ""), str(out))
out = sv.drain(FakeMedha([], err=BriefError(0, "transport", "refused")), db)
check("an unreachable Medha is reported", "could not reach" in (out["skipped"] or ""), str(out))
m = FakeMedha([])
out = sv.drain(m, db)
check("an empty outbox reads once and acks nothing",
      out["seen"] == 0 and not any(c[0] == "ack" for c in m.calls), str(m.calls))

section("A malformed row is dropped, not left to wedge the queue")
m = FakeMedha([item(40), {"id": 41, "title": "no fp"}, item(42)])
before = count()
out = sv.drain(m, db)
check("the good rows around it are stored", count() == before + 2, str(out))
check("the outbox is emptied, bad row included", m.rows == [], str(m.rows))

section("Paging")
m = FakeMedha([item(100 + i) for i in range(7)], page=3)
before = count()
out = sv.drain(m, db)
check("a backlog bigger than a page is drained across pages", count() == before + 7, str(out))
check("…in three reads", sum(1 for c in m.calls if c[0] == "saved") == 3, str(m.calls))
check("every read starts from 0, because acked rows are gone",
      all(c[1] == 0 for c in m.calls if c[0] == "saved"))
m = FakeMedha([item(200 + i) for i in range(50)], page=1)
out = sv.drain(m, db, max_rounds=4)
check("a drain is bounded even when Medha keeps saying more",
      sum(1 for c in m.calls if c[0] == "saved") == 4, str(len(m.calls)))

section("Before the app has migrated")
bare = sqlite3.connect(":memory:")
bare.execute("CREATE TABLE notes (id TEXT)")
m = FakeMedha([item(300)])
out = sv.drain(m, bare)
check("no table means no drain", out["stored"] == 0 and "migrate" in (out["skipped"] or ""), str(out))
check("…Medha is not even asked", m.calls == [], str(m.calls))
check("…so the save is still safe in Medha's outbox", len(m.rows) == 1)

print(f"\n{_ok} passed, {_bad} failed")
sys.exit(1 if _bad else 0)
