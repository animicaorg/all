from __future__ import annotations

import asyncio
import sqlite3
import time

import pytest

aiosqlite = pytest.importorskip("aiosqlite")

from studio_services.tasks.queue import SQLiteTaskQueue


CREATE_SQL = """
CREATE TABLE IF NOT EXISTS queue (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  idempotency_key TEXT UNIQUE,
  payload TEXT NOT NULL,
  priority INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 8,
  available_at INTEGER NOT NULL,
  lease_owner TEXT,
  lease_until INTEGER,
  result TEXT,
  error TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
"""


def test_poll_rolls_back_on_error(tmp_path, monkeypatch):
    async def _run():
        db_path = tmp_path / "queue.sqlite"
        queue = SQLiteTaskQueue(str(db_path))
        await queue.connect()
        assert queue._conn is not None  # noqa: SLF001 - test needs direct access for setup
        await queue._conn.execute(CREATE_SQL)
        await queue._conn.commit()

        await queue.enqueue(kind="k", payload={"x": 1})

        original = queue._conn.execute_fetchone
        raised = False

        async def fail_once(*args, **kwargs):
            nonlocal raised
            if not raised:
                raised = True
                raise sqlite3.OperationalError("boom")
            return await original(*args, **kwargs)

        monkeypatch.setattr(queue._conn, "execute_fetchone", fail_once)

        with pytest.raises(sqlite3.OperationalError):
            await queue.poll(worker_id="w", lease_seconds=1)

        task = await queue.poll(worker_id="w", lease_seconds=1)
        assert task is not None
        assert task.lease_owner == "w"

        await queue.close()

    asyncio.run(_run())


def test_connect_creates_schema(tmp_path):
    async def _run():
        db_path = tmp_path / "queue.sqlite"
        queue = SQLiteTaskQueue(str(db_path))

        await queue.connect()
        assert queue._conn is not None  # noqa: SLF001 - test needs direct access
        cur = await queue._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='queue';"
        )
        row = await cur.fetchone()
        assert row is not None

        await queue.close()

    asyncio.run(_run())


def test_requeue_recovers_from_open_transaction(tmp_path):
    async def _run():
        db_path = tmp_path / "queue.sqlite"
        queue = SQLiteTaskQueue(str(db_path))

        await queue.connect()
        assert queue._conn is not None  # noqa: SLF001 - direct access for setup
        await queue._conn.execute(CREATE_SQL)

        # Seed an expired running task
        now = int(time.time())
        await queue._conn.execute(
            """
            INSERT INTO queue (
              id, kind, idempotency_key, payload, priority, status,
              attempts, max_attempts, available_at, lease_owner, lease_until,
              result, error, created_at, updated_at
            ) VALUES (?, 'k', NULL, '{"x":1}', 0, 'running', 1, 3, ?, 'w', ?, NULL, NULL, ?, ?)
            """,
            ("task1", now - 10, now - 5, now - 10, now - 10),
        )
        await queue._conn.commit()

        # Leave an open transaction to mimic an interrupted call site
        await queue._conn.execute("BEGIN;")

        moved = await queue.requeue_expired_leases(limit=10)
        assert moved == 1

        row = await queue._execute_fetchone(
            "SELECT status, lease_owner, lease_until FROM queue WHERE id = ?;", ("task1",)
        )
        assert row is not None
        assert row["status"] == "queued"
        assert row["lease_owner"] is None
        assert row["lease_until"] is None

        await queue.close()

    asyncio.run(_run())

