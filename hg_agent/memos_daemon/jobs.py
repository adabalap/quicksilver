"""jobs.py — a durable job queue in SQLite (table `agent_jobs`).

Until now the work queue was a Python PriorityQueue: fast, but it lived in RAM.
If the agent was killed mid-run, whatever was queued or in flight was forgotten,
and "retry" was a pile of ad-hoc sweepers. This makes the queue a TABLE so that:

  • nothing is lost across a crash / restart / OOM kill;
  • every job has a lease while it runs — a dead worker's job comes back;
  • failures retry with exponential backoff, then land in a dead-letter state the
    user can SEE and retry, instead of blinking "Processing" forever;
  • the app (a separate process) can show queue health and retry dead jobs.

The in-memory PriorityQueue stays as the low-latency dispatcher; this table is
the source of truth it is rebuilt from. One agent process, one worker, so a
claim needs no cross-process locking — but every transition is a single atomic
UPDATE anyway so a stray second process can't double-run a job.

Job states:  queued → running → done
                 ↑        │ fail (attempts < max): back to queued, run_after = backoff
                 └────────┤
                          └ fail (attempts ≥ max): dead   (user can retry → queued)
"""
import json, time, logging, sqlite3
from datetime import datetime, timezone, timedelta

log = logging.getLogger("hg.jobs")

SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_jobs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kind          TEXT NOT NULL,                 -- 'note' | 'ask'
    key           TEXT NOT NULL,                 -- note id, or 'ask:<request id>'
    payload       TEXT NOT NULL DEFAULT '{}',
    status        TEXT NOT NULL DEFAULT 'queued'
                      CHECK(status IN ('queued','running','done','dead')),
    priority      INTEGER NOT NULL DEFAULT 1,    -- lower = sooner
    attempts      INTEGER NOT NULL DEFAULT 0,
    max_attempts  INTEGER NOT NULL DEFAULT 3,
    run_after     TEXT NOT NULL,
    lease_until   TEXT,
    last_error    TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_due    ON agent_jobs(status, run_after, priority);
CREATE INDEX IF NOT EXISTS idx_jobs_key    ON agent_jobs(kind, key, status);
"""


def _iso(dt=None) -> str:
    return (dt or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _in(seconds: float) -> str:
    return _iso(datetime.now(timezone.utc) + timedelta(seconds=seconds))


class JobStore:
    def __init__(self, get_db, lease_seconds: int = 1200,
                 backoff=(30, 120, 600)):
        self._db = get_db                    # callable → this thread's connection
        self.lease_seconds = lease_seconds
        self.backoff = tuple(backoff)

    def ensure_schema(self):
        db = self._db()
        db.executescript(SCHEMA)
        db.commit()

    # ── producing ────────────────────────────────────────────────────────────
    def enqueue(self, kind: str, key: str, priority: int = 1, payload: dict | None = None,
                max_attempts: int = 3, immediate: bool = True) -> dict:
        """Add a job, or merge into the waiting one for (kind, key).
        Returns {"id", "created", "status", "due"} where `due` says whether the
        job may run now. `immediate=True` (a real user action: new edit, new
        question) cancels any backoff and resets the retry budget; `False`
        (a sweeper noticing something stale) respects the backoff."""
        db = self._db()
        now = _iso()
        row = db.execute("SELECT * FROM agent_jobs WHERE kind=? AND key=? AND status='queued' "
                         "ORDER BY id DESC LIMIT 1", (kind, key)).fetchone()
        if row:                                   # merge into the waiting job
            prio = min(priority, row["priority"])
            if immediate:
                db.execute("UPDATE agent_jobs SET priority=?, run_after=?, attempts=0, "
                           "updated_at=? WHERE id=?", (prio, now, now, row["id"]))
            elif prio != row["priority"]:
                db.execute("UPDATE agent_jobs SET priority=?, updated_at=? WHERE id=?",
                           (prio, now, row["id"]))
            db.commit()
            return {"id": row["id"], "created": False, "status": "queued",
                    "due": immediate or row["run_after"] <= now}
        running = db.execute("SELECT id FROM agent_jobs WHERE kind=? AND key=? AND status='running' "
                             "LIMIT 1", (kind, key)).fetchone()
        if running and not immediate:             # a sweeper: it is already being handled
            return {"id": running["id"], "created": False, "status": "running", "due": False}
        # A real new request while one is running (a fresh edit, mid-inference)
        # gets its own queued job so the note is processed again afterwards.
        cur = db.execute(
            "INSERT INTO agent_jobs (kind,key,payload,status,priority,max_attempts,run_after,created_at,updated_at) "
            "VALUES (?,?,?,'queued',?,?,?,?,?)",
            (kind, key, json.dumps(payload or {}), priority, max_attempts, now, now, now))
        db.commit()
        return {"id": cur.lastrowid, "created": True, "status": "queued", "due": True}

    # ── consuming ────────────────────────────────────────────────────────────
    def claim(self, kind: str, key: str) -> dict | None:
        """Mark the queued job for (kind,key) running and take a lease. None if
        there is none (e.g. the in-memory queue held a name with no job row)."""
        db = self._db()
        now = _iso()
        cur = db.execute(
            "UPDATE agent_jobs SET status='running', attempts=attempts+1, lease_until=?, updated_at=? "
            "WHERE id=(SELECT id FROM agent_jobs WHERE kind=? AND key=? AND status='queued' "
            "ORDER BY id DESC LIMIT 1) AND status='queued'",
            (_in(self.lease_seconds), now, kind, key))
        db.commit()
        if cur.rowcount != 1:
            return None
        return dict(db.execute("SELECT * FROM agent_jobs WHERE kind=? AND key=? AND status='running' "
                               "ORDER BY id DESC LIMIT 1", (kind, key)).fetchone())

    def complete(self, job_id: int):
        db = self._db()
        db.execute("UPDATE agent_jobs SET status='done', lease_until=NULL, last_error=NULL, updated_at=? "
                   "WHERE id=?", (_iso(), job_id))
        db.commit()

    def release(self, job_id: int):
        """Put a running job back WITHOUT spending an attempt (it was deferred,
        e.g. the phone was hot — not a failure)."""
        db = self._db()
        db.execute("UPDATE agent_jobs SET status='queued', attempts=MAX(attempts-1,0), lease_until=NULL, "
                   "updated_at=? WHERE id=? AND status='running'", (_iso(), job_id))
        db.commit()

    def fail(self, job_id: int, error: str) -> str:
        """Record a failed attempt. Returns 'queued' (will retry after backoff) or
        'dead' (retry budget spent)."""
        db = self._db()
        row = db.execute("SELECT attempts, max_attempts FROM agent_jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            return "dead"
        err = (error or "failed")[:300]
        now = _iso()
        if row["attempts"] >= row["max_attempts"]:
            db.execute("UPDATE agent_jobs SET status='dead', lease_until=NULL, last_error=?, updated_at=? "
                       "WHERE id=?", (err, now, job_id))
            db.commit()
            return "dead"
        wait = self.backoff[min(row["attempts"] - 1, len(self.backoff) - 1)] if self.backoff else 0
        db.execute("UPDATE agent_jobs SET status='queued', lease_until=NULL, last_error=?, run_after=?, "
                   "updated_at=? WHERE id=?", (err, _in(wait), now, job_id))
        db.commit()
        return "queued"

    # ── recovery / housekeeping ──────────────────────────────────────────────
    def recover(self, everything: bool = False, skip_keys=()) -> int:
        """Return orphaned 'running' jobs to the queue. At boot (`everything`)
        every running job belongs to the process that just died. Later, only
        those whose lease expired — and never the one the worker holds now."""
        db = self._db()
        rows = db.execute("SELECT id, key, attempts, max_attempts FROM agent_jobs WHERE status='running'"
                          + ("" if everything else " AND lease_until < ?"),
                          () if everything else (_iso(),)).fetchall()
        n = 0
        for r in rows:
            if r["key"] in skip_keys:
                continue
            if r["attempts"] >= r["max_attempts"]:
                db.execute("UPDATE agent_jobs SET status='dead', lease_until=NULL, "
                           "last_error='agent stopped while running this (retries spent)', updated_at=? WHERE id=?",
                           (_iso(), r["id"]))
            else:
                db.execute("UPDATE agent_jobs SET status='queued', lease_until=NULL, run_after=?, "
                           "last_error=COALESCE(last_error,'interrupted'), updated_at=? WHERE id=?",
                           (_iso(), _iso(), r["id"]))
            n += 1
        db.commit()
        return n

    def due(self, limit: int = 200) -> list:
        """Queued jobs whose backoff has elapsed, best priority first."""
        rows = self._db().execute(
            "SELECT * FROM agent_jobs WHERE status='queued' AND run_after<=? "
            "ORDER BY priority ASC, id ASC LIMIT ?", (_iso(), limit)).fetchall()
        return [dict(r) for r in rows]

    def dead(self, limit: int = 50) -> list:
        return [dict(r) for r in self._db().execute(
            "SELECT * FROM agent_jobs WHERE status='dead' ORDER BY updated_at DESC LIMIT ?", (limit,))]

    def retry_dead(self, job_id: int | None = None) -> int:
        db = self._db()
        cur = db.execute("UPDATE agent_jobs SET status='queued', attempts=0, run_after=?, updated_at=? "
                         "WHERE status='dead'" + (" AND id=?" if job_id else ""),
                         (_iso(), _iso()) + ((job_id,) if job_id else ()))
        db.commit()
        return cur.rowcount

    def counts(self) -> dict:
        out = {"queued": 0, "running": 0, "dead": 0, "done": 0}
        for r in self._db().execute("SELECT status, COUNT(*) c FROM agent_jobs GROUP BY status"):
            out[r["status"]] = r["c"]
        return out

    def prune(self, done_hours: float = 24, dead_days: float = 14) -> int:
        db = self._db()
        c1 = db.execute("DELETE FROM agent_jobs WHERE status='done' AND updated_at < ?",
                        (_iso(datetime.now(timezone.utc) - timedelta(hours=done_hours)),)).rowcount
        c2 = db.execute("DELETE FROM agent_jobs WHERE status='dead' AND updated_at < ?",
                        (_iso(datetime.now(timezone.utc) - timedelta(days=dead_days)),)).rowcount
        db.commit()
        return c1 + c2
