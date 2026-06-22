import asyncio
import json
import logging
import sqlite3
import threading
import time

log = logging.getLogger(__name__)


class Queue:

    def __init__(self, path: str):
        self._path = path
        self._local = threading.local()
        self._lock = threading.Lock()
        self._init_db()

    @property
    def _conn(self):
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(self._path)
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
        return self._local.conn

    def _init_db(self):
        with self._lock:
            conn = sqlite3.connect(self._path)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT UNIQUE NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 5,
                    last_error TEXT,
                    retry_at REAL,
                    lease_expires_at REAL,
                    data TEXT,
                    created_at REAL NOT NULL DEFAULT ((julianday('now') - 2440587.5) * 86400.0),
                    updated_at REAL NOT NULL DEFAULT ((julianday('now') - 2440587.5) * 86400.0)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL,
                    url TEXT NOT NULL,
                    data TEXT NOT NULL,
                    collected_at REAL NOT NULL DEFAULT ((julianday('now') - 2440587.5) * 86400.0),
                    FOREIGN KEY (task_id) REFERENCES tasks(id)
                )
            """)
            conn.commit()
            conn.close()

    def enqueue(self, url: str, data: dict | None = None):
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT OR IGNORE INTO tasks (url, data) VALUES (?, ?)",
                    (url, json.dumps(data) if data else None),
                )
                self._conn.commit()
            except Exception as exc:
                log.error("Enqueue failed for %s: %s", url, exc)

    def enqueue_many(self, urls: list[str]):
        with self._lock:
            for url in urls:
                try:
                    self._conn.execute(
                        "INSERT OR IGNORE INTO tasks (url) VALUES (?)",
                        (url,),
                    )
                except Exception as exc:
                    log.error("Enqueue failed for %s: %s", url, exc)
            self._conn.commit()

    def claim(self, lease_seconds: int = 300) -> dict | None:
        now = time.time()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            row = self._conn.execute(
                """
                UPDATE tasks SET
                    status = 'active',
                    lease_expires_at = ?,
                    updated_at = ?
                WHERE id = (
                    SELECT id FROM tasks
                    WHERE status = 'pending'
                        AND (retry_at IS NULL OR retry_at <= ?)
                    LIMIT 1
                )
                RETURNING id, url, data
                """,
                (now + lease_seconds, now, now),
            ).fetchone()
            self._conn.commit()
            if row:
                return {
                    "id": row["id"],
                    "url": row["url"],
                    "data": json.loads(row["data"]) if row["data"] else None,
                }
            self._recover_expired(now)
            return None

    def _recover_expired(self, now: float):
        self._conn.execute(
            "UPDATE tasks SET status = 'pending', lease_expires_at = NULL, updated_at = ? "
            "WHERE status = 'active' AND lease_expires_at <= ?",
            (now, now),
        )
        self._conn.commit()

    def complete(self, task_id: int, result: dict):
        data = json.dumps(result, default=str)
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            self._conn.execute(
                "INSERT INTO results (task_id, url, data) VALUES (?, ?, ?)",
                (task_id, result.get("url", ""), data),
            )
            self._conn.execute(
                "UPDATE tasks SET status = 'done', updated_at = ? WHERE id = ?",
                (time.time(), task_id),
            )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def fail(self, task_id: int, error: str | None = None):
        now = time.time()
        self._conn.execute(
            "UPDATE tasks SET status = 'failed', last_error = ?, updated_at = ? WHERE id = ?",
            (error, now, task_id),
        )
        self._conn.commit()

    def retry_later(self, task_id: int, delay: float = 60.0, error: str | None = None):
        now = time.time()
        row = self._conn.execute(
            "SELECT attempt_count, max_attempts FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if row and row["attempt_count"] >= row["max_attempts"]:
            self._conn.execute(
                "UPDATE tasks SET status = 'failed', last_error = ?, updated_at = ? WHERE id = ?",
                (error, now, task_id),
            )
        else:
            self._conn.execute(
                "UPDATE tasks SET status = 'pending', retry_at = ?, attempt_count = attempt_count + 1, "
                "last_error = ?, updated_at = ? WHERE id = ?",
                (now + delay, error, now, task_id),
            )
        self._conn.commit()

    def count_by_status(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT status, COUNT(*) as cnt FROM tasks GROUP BY status"
        ).fetchall()
        return {r["status"]: r["cnt"] for r in rows}

    def close(self):
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None

    # --- Async wrappers ---

    async def enqueue_async(self, url: str, data: dict | None = None):
        await asyncio.to_thread(self.enqueue, url, data)

    async def enqueue_many_async(self, urls: list[str]):
        await asyncio.to_thread(self.enqueue_many, urls)

    async def claim_async(self, lease_seconds: int = 300) -> dict | None:
        return await asyncio.to_thread(self.claim, lease_seconds)

    async def complete_async(self, task_id: int, result: dict):
        await asyncio.to_thread(self.complete, task_id, result)

    async def fail_async(self, task_id: int, error: str | None = None):
        await asyncio.to_thread(self.fail, task_id, error)

    async def retry_later_async(self, task_id: int, delay: float = 60.0, error: str | None = None):
        await asyncio.to_thread(self.retry_later, task_id, delay, error)

    async def count_by_status_async(self) -> dict[str, int]:
        return await asyncio.to_thread(self.count_by_status)
