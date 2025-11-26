from __future__ import annotations

import asyncio
import sqlite3

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

