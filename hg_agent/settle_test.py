#!/usr/bin/env python3
"""A note being typed is not processed mid-typing, and a result for text that has since changed is dropped.

    python settle_test.py
"""
import os, sys, sqlite3, time, logging
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
G, R, X = "\033[32m", "\033[31m", "\033[0m"
_ok = _bad = 0


def check(name, cond, detail=""):
    global _ok, _bad
    if cond: _ok += 1; print(f"  {G}✓{X} {name}")
    else: _bad += 1; print(f"  {R}✗ {name}{X}" + (f"  → {detail}" if detail else ""))


from memos_daemon.parts.sources import SourcesMixin
from memos_daemon.daemon import MemosDaemon
logging.disable(logging.CRITICAL)

db = sqlite3.connect(":memory:", check_same_thread=False); db.row_factory = sqlite3.Row
db.execute("CREATE TABLE note_state (note_id TEXT PRIMARY KEY, status TEXT)")


def status(nid, st):
    db.execute("INSERT OR REPLACE INTO note_state VALUES (?,?)", (nid, st)); db.commit()


class D(SourcesMixin):
    running = True
    class memos:
        @staticmethod
        def _db(): return db

    def __init__(self, quiet):
        self.cfg = {"enrich_quiet_seconds": quiet}
        self.queued = []

    def enqueue(self, nid, reason="", **k): self.queued.append((nid, reason))


print("\n▸ settle window")
d = D(0.4); status("a", "raw")
for _ in range(6):
    d._note_event("a", "sse:note_updated"); time.sleep(0.1)
check("a burst of autosaves queues nothing while typing continues", d.queued == [])
check("and the note reads as still typing", d._still_typing("a", "raw"))
time.sleep(0.8)
check("after it goes quiet it is queued exactly once", [q[0] for q in d.queued] == ["a"], str(d.queued))
check("no longer typing", not d._still_typing("a", "raw"))

d = D(5); status("b", "force")
d._note_event("b", "sse:note_updated")
check("a committed note (editor closed / Run AI now) goes straight through", [q[0] for q in d.queued] == ["b"])
check("and is never held back", not d._still_typing("b", "force"))

d = D(0.3); status("c", "dirty")
d._note_event("c", "sse:note_updated"); time.sleep(0.6)
check("an open editor (dirty) is not queued at all; the commit will do it", d.queued == [])

d = D(0.3); status("e", "raw")
d._note_event("e", "sse:note_updated"); status("e", "force"); d._note_event("e", "sse:note_updated")
time.sleep(0.6)
check("a commit during the wait queues once now and cancels the timer", [q[0] for q in d.queued] == ["e"], str(d.queued))

d = D(0); status("f", "raw"); d._note_event("f", "x")
check("enrich_quiet_seconds=0 turns the wait off", [q[0] for q in d.queued] == ["f"])

print("\n▸ processing path")
d = D(30); status("g", "raw"); d._note_event("g", "sse:note_created")
check("a safety-poll pass during the window defers instead of running the model", d._still_typing("g", "raw"))


class FakeSelf(D):
    def __init__(self):
        super().__init__(30)
        self.ran = False
        class M:
            def get_memo(s, name): return {"name": name, "status": "raw", "content": "text"}
        self.memos = M()

    def _still_typing(self, nid, st): return True

    def __getattr__(self, k):
        if k == "ran": return False
        raise AttributeError(k)


f = FakeSelf()
check("_process_one returns before touching the model while the note is being typed", MemosDaemon._process_one(f, "g") is None)

print("\n▸ stale result")
class M2:
    def __init__(self, body): self.body = body
    def get_memo(self, name): return None if self.body is None else {"content": self.body}


class H:
    def __init__(self, body): self.memos = M2(body)

    _changed_during_inference = MemosDaemon._changed_during_inference


check("same text (whitespace aside): the result is kept", not H("Buy milk and eggs")._changed_during_inference("n", "Buy milk and eggs "))
check("text changed while the model worked: the result is dropped", H("Buy milk and eggs and bread")._changed_during_inference("n", "Buy milk and eggs"))
check("note deleted meanwhile: dropped", H(None)._changed_during_inference("n", "x"))

print(f"\n{G if not _bad else R}{_ok} passed, {_bad} failed{X}")
sys.exit(1 if _bad else 0)
