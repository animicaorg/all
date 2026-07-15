"""Engine-side SQLite persistence (loop artifacts only — subscribers live in the marketplace DB).
Holds metric snapshots, generated drafts, human approvals, and durable send counters for the
per-hour/per-day caps (survive restarts)."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any, List, Optional

from .config import GrowthConfig


def _conn(cfg: GrowthConfig) -> sqlite3.Connection:
    c = sqlite3.connect(cfg.db_path(), timeout=15)
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS snapshots(ts INTEGER PRIMARY KEY, json TEXT);
        CREATE TABLE IF NOT EXISTS drafts(
            id TEXT PRIMARY KEY, kind TEXT, channel TEXT, audience TEXT,
            content_hash TEXT, title TEXT, body TEXT, meta TEXT, created INTEGER);
        CREATE TABLE IF NOT EXISTS approvals(content_hash TEXT PRIMARY KEY, approver TEXT, ts INTEGER);
        CREATE TABLE IF NOT EXISTS send_counter(window TEXT PRIMARY KEY, count INTEGER);
        CREATE TABLE IF NOT EXISTS listing_log(target TEXT, method TEXT, status TEXT, detail TEXT, ts INTEGER);
        """
    )
    return c


def save_snapshot(cfg: GrowthConfig, snap: dict) -> None:
    with _conn(cfg) as c:
        c.execute("INSERT OR REPLACE INTO snapshots(ts, json) VALUES(?,?)", (snap["ts"], json.dumps(snap)))


def last_snapshot(cfg: GrowthConfig, before: Optional[int] = None) -> Optional[dict]:
    with _conn(cfg) as c:
        if before:
            row = c.execute("SELECT json FROM snapshots WHERE ts<? ORDER BY ts DESC LIMIT 1", (before,)).fetchone()
        else:
            row = c.execute("SELECT json FROM snapshots ORDER BY ts DESC LIMIT 1").fetchone()
    return json.loads(row[0]) if row else None


def save_draft(cfg: GrowthConfig, draft: dict) -> None:
    with _conn(cfg) as c:
        c.execute(
            "INSERT OR REPLACE INTO drafts(id,kind,channel,audience,content_hash,title,body,meta,created) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (draft["id"], draft.get("kind"), draft.get("channel"), draft.get("audience"),
             draft.get("content_hash"), draft.get("title"), draft.get("body"),
             json.dumps(draft.get("meta", {})), int(time.time())),
        )


def list_drafts(cfg: GrowthConfig, limit: int = 50) -> List[dict]:
    with _conn(cfg) as c:
        rows = c.execute(
            "SELECT id,kind,channel,audience,content_hash,title,body,meta,created FROM drafts "
            "ORDER BY created DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        out.append({"id": r[0], "kind": r[1], "channel": r[2], "audience": r[3], "content_hash": r[4],
                    "title": r[5], "body": r[6], "meta": json.loads(r[7] or "{}"), "created": r[8]})
    return out


def record_approval(cfg: GrowthConfig, content_hash: str, approver: str) -> None:
    with _conn(cfg) as c:
        c.execute("INSERT OR REPLACE INTO approvals(content_hash,approver,ts) VALUES(?,?,?)",
                  (content_hash, approver, int(time.time())))


def get_approval(cfg: GrowthConfig, content_hash: str) -> Optional[dict]:
    with _conn(cfg) as c:
        row = c.execute("SELECT approver,ts FROM approvals WHERE content_hash=?", (content_hash,)).fetchone()
    return {"content_hash": content_hash, "approver": row[0], "ts": row[1]} if row else None


def bump_send_counter(cfg: GrowthConfig, n: int = 1) -> None:
    day = time.strftime("d:%Y-%m-%d", time.gmtime())
    hour = time.strftime("h:%Y-%m-%d-%H", time.gmtime())
    with _conn(cfg) as c:
        for w in (day, hour):
            c.execute("INSERT INTO send_counter(window,count) VALUES(?,?) "
                      "ON CONFLICT(window) DO UPDATE SET count=count+?", (w, n, n))


def sent_today(cfg: GrowthConfig) -> int:
    day = time.strftime("d:%Y-%m-%d", time.gmtime())
    with _conn(cfg) as c:
        row = c.execute("SELECT count FROM send_counter WHERE window=?", (day,)).fetchone()
    return row[0] if row else 0


def sent_this_hour(cfg: GrowthConfig) -> int:
    hour = time.strftime("h:%Y-%m-%d-%H", time.gmtime())
    with _conn(cfg) as c:
        row = c.execute("SELECT count FROM send_counter WHERE window=?", (hour,)).fetchone()
    return row[0] if row else 0


def log_listing(cfg: GrowthConfig, target: str, method: str, status: str, detail: str) -> None:
    with _conn(cfg) as c:
        c.execute("INSERT INTO listing_log(target,method,status,detail,ts) VALUES(?,?,?,?,?)",
                  (target, method, status, detail, int(time.time())))
