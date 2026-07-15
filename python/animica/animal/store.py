"""Local durable state for the engine: per-platform daily post counts (cap enforcement) and a
content-dedup log so the mascot doesn't repeat itself."""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager


@contextmanager
def _db(path: str):
    conn = sqlite3.connect(path, timeout=30)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""CREATE TABLE IF NOT EXISTS post_count(
            day TEXT, platform TEXT, n INTEGER DEFAULT 0, PRIMARY KEY(day, platform))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS seen(
            chash TEXT PRIMARY KEY, ts INTEGER)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT)""")
        yield conn
        conn.commit()
    finally:
        conn.close()


def _day() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def counts(path: str, platform: str) -> tuple[int, int]:
    """(this-platform-today, all-platforms-today)."""
    with _db(path) as c:
        day = _day()
        row = c.execute("SELECT n FROM post_count WHERE day=? AND platform=?", (day, platform)).fetchone()
        tot = c.execute("SELECT COALESCE(SUM(n),0) FROM post_count WHERE day=?", (day,)).fetchone()
        return (row[0] if row else 0, tot[0] if tot else 0)


def bump(path: str, platform: str) -> None:
    with _db(path) as c:
        day = _day()
        c.execute(
            "INSERT INTO post_count(day, platform, n) VALUES(?,?,1) "
            "ON CONFLICT(day, platform) DO UPDATE SET n = n + 1",
            (day, platform),
        )


def is_seen(path: str, chash: str) -> bool:
    with _db(path) as c:
        return c.execute("SELECT 1 FROM seen WHERE chash=?", (chash,)).fetchone() is not None


def mark_seen(path: str, chash: str) -> None:
    with _db(path) as c:
        c.execute("INSERT OR IGNORE INTO seen(chash, ts) VALUES(?,?)", (chash, int(time.time())))


def get_kv(path: str, k: str, default: str = "") -> str:
    with _db(path) as c:
        row = c.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return row[0] if row else default


def set_kv(path: str, k: str, v: str) -> None:
    with _db(path) as c:
        c.execute("INSERT INTO kv(k, v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=?", (k, v, v))
