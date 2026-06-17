"""state.py — local SQLite state store (stdlib sqlite3, no deps).

Tracks each document through the pipeline for resume, dedup (content_hash),
idempotency and a human-review queue.

Thread-safe: the connection is opened with check_same_thread=False and every
access is guarded by a single lock, so the document-level worker pool (parallel
ingest) can share one State without corrupting it. WAL mode lets readers and the
writer proceed without blocking each other.
"""
from __future__ import annotations
import sqlite3
import threading
import time
from typing import Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    doc_id        TEXT PRIMARY KEY,   -- instrument_key or path-hash
    path          TEXT NOT NULL,
    content_hash  TEXT,
    status        TEXT NOT NULL DEFAULT 'pending',  -- pending|processing|done|review|error
    stage         TEXT,               -- last completed stage
    confidence    REAL,
    error         TEXT,
    created_at    REAL,
    updated_at    REAL
);
CREATE INDEX IF NOT EXISTS idx_status ON documents(status);
CREATE INDEX IF NOT EXISTS idx_hash   ON documents(content_hash);
"""


class State:
    def __init__(self, db_path: str):
        # check_same_thread=False: the worker pool shares one connection, guarded
        # by self._lock below. A short busy_timeout absorbs brief WAL contention.
        self.db = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
        self.db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            try:
                self.db.execute("PRAGMA journal_mode=WAL")
                self.db.execute("PRAGMA busy_timeout=30000")
            except sqlite3.Error:
                pass                              # WAL unsupported -> fall back silently
            self.db.executescript(SCHEMA)
            self.db.commit()

    def seen_hash(self, content_hash: str) -> bool:
        with self._lock:
            cur = self.db.execute(
                "SELECT 1 FROM documents WHERE content_hash=? AND status='done'",
                (content_hash,))
            return cur.fetchone() is not None

    def upsert(self, doc_id: str, path: str, content_hash: str = "") -> None:
        now = time.time()
        with self._lock:
            self.db.execute(
                """INSERT INTO documents(doc_id,path,content_hash,status,created_at,updated_at)
                   VALUES(?,?,?,'pending',?,?)
                   ON CONFLICT(doc_id) DO UPDATE SET path=excluded.path,
                     content_hash=excluded.content_hash, updated_at=excluded.updated_at""",
                (doc_id, path, content_hash, now, now))
            self.db.commit()

    def claim(self, doc_id: str, path: str, content_hash: str = "") -> bool:
        """Atomically claim a doc for processing. Returns False if it is already
        done (dedup) or currently being processed by another worker (no double
        work). Used by the parallel ingest so two workers never embed the same law.
        """
        now = time.time()
        with self._lock:
            # already completed for this content -> skip (resume / dedup)
            if content_hash and self.db.execute(
                    "SELECT 1 FROM documents WHERE content_hash=? AND status='done'",
                    (content_hash,)).fetchone():
                return False
            row = self.db.execute(
                "SELECT status FROM documents WHERE doc_id=?", (doc_id,)).fetchone()
            if row and row["status"] == "processing":
                return False                      # another worker holds it
            self.db.execute(
                """INSERT INTO documents(doc_id,path,content_hash,status,created_at,updated_at)
                   VALUES(?,?,?,'processing',?,?)
                   ON CONFLICT(doc_id) DO UPDATE SET path=excluded.path,
                     content_hash=excluded.content_hash, status='processing',
                     updated_at=excluded.updated_at""",
                (doc_id, path, content_hash, now, now))
            self.db.commit()
            return True

    def set_status(self, doc_id: str, status: str, stage: str = None,
                   confidence: float = None, error: str = None) -> None:
        with self._lock:
            self.db.execute(
                """UPDATE documents SET status=?, stage=COALESCE(?,stage),
                     confidence=COALESCE(?,confidence), error=?, updated_at=?
                   WHERE doc_id=?""",
                (status, stage, confidence, error, time.time(), doc_id))
            self.db.commit()

    def by_status(self, status: str) -> list[sqlite3.Row]:
        with self._lock:
            return self.db.execute(
                "SELECT * FROM documents WHERE status=?", (status,)).fetchall()

    def pending(self) -> list[sqlite3.Row]:
        with self._lock:
            return self.db.execute(
                "SELECT * FROM documents WHERE status IN ('pending','error')").fetchall()

    def requeue_stale(self) -> int:
        """Reset any 'processing' rows left by an interrupted run back to 'pending'.

        A fatal stop (or crash) can leave docs marked 'processing'; claim() treats
        'processing' as held-by-another-worker and would skip them forever. Calling
        this at the start of a run makes those docs resumable. Returns rows requeued.
        """
        with self._lock:
            cur = self.db.execute(
                "UPDATE documents SET status='pending', updated_at=? "
                "WHERE status='processing'", (time.time(),))
            self.db.commit()
            return cur.rowcount

    def counts(self) -> dict:
        with self._lock:
            rows = self.db.execute(
                "SELECT status, COUNT(*) c FROM documents GROUP BY status").fetchall()
            return {r["status"]: r["c"] for r in rows}

    def reset(self) -> int:
        """Clear the ingest history (dedup/resume), returning rows removed.

        After this, previously-'done' documents are no longer skipped and will be
        re-processed on the next ingest. Does NOT touch Weaviate — vectors there
        are upserted by deterministic UUID, so re-ingesting overwrites them.
        """
        with self._lock:
            n = self.db.execute("SELECT COUNT(*) c FROM documents").fetchone()["c"]
            self.db.execute("DELETE FROM documents")
            self.db.commit()
            return int(n)

    def close(self):
        with self._lock:
            self.db.close()
