"""
animica.ai.telemetry — per-provider health for the router (7.1.1 P3).

Tracks EWMA latency, error rate, and a circuit breaker per provider key, backed
by a small SQLite file so state is correct across multiple uvicorn workers (an
in-memory dict would give each worker its own, wrong, view). Every operation
opens a short-lived autocommit connection — SQLite's file locking makes this
process- and thread-safe.

Circuit breaker: opens after ``fail_threshold`` consecutive ProviderErrors and
stays open for ``cooldown_s``; then a single half-open probe is allowed — success
closes it, failure re-opens it.
"""

from __future__ import annotations

import os
import sqlite3
import time
from typing import Any, Optional

_ALPHA = 0.3
_FAIL_THRESHOLD = 3
_COOLDOWN_S = 30.0


def _db_path() -> str:
    override = os.environ.get("ANIMICA_AI_TELEMETRY_DB")
    if override:
        return override
    from animica.ai.nodekey import ena_home
    home = ena_home()
    home.mkdir(parents=True, exist_ok=True)
    return str(home / "ai_telemetry.db")


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(_db_path(), timeout=5.0, isolation_level=None)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("""CREATE TABLE IF NOT EXISTS provider_health (
        provider TEXT PRIMARY KEY,
        ewma_latency REAL DEFAULT 0,
        ok_count INTEGER DEFAULT 0,
        err_count INTEGER DEFAULT 0,
        consecutive_err INTEGER DEFAULT 0,
        breaker_until REAL DEFAULT 0,
        cost_per_1k REAL,
        updated_at REAL DEFAULT 0)""")
    return c


def _now() -> float:
    return time.time()


def record(provider_key: str, *, latency_ms: float, ok: bool,
           cost_per_1k: Optional[float] = None) -> None:
    """Record one request outcome for ``provider_key``.

    The consecutive-error streak and breaker arming are computed INSIDE a single
    atomic ``BEGIN IMMEDIATE`` UPDATE (derived from the current row), so concurrent
    failers can't lost-update the streak and the breaker actually trips under the
    multi-worker concurrency it exists for.
    """
    okv = 1 if ok else 0
    now = _now()
    c = _conn()
    try:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT ewma_latency FROM provider_health WHERE provider=?",
                        (provider_key,)).fetchone()
        prev_lat = row[0] if row else 0.0
        ewma = latency_ms if not prev_lat else _ALPHA * latency_ms + (1 - _ALPHA) * prev_lat
        # Insert-or-update; the streak/breaker are derived atomically from the
        # existing row within this exclusive transaction.
        c.execute("""INSERT INTO provider_health
            (provider, ewma_latency, ok_count, err_count, consecutive_err, breaker_until, cost_per_1k, updated_at)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(provider) DO UPDATE SET
                ewma_latency=?,
                ok_count=provider_health.ok_count+?,
                err_count=provider_health.err_count+?,
                consecutive_err=CASE WHEN ?=1 THEN 0 ELSE provider_health.consecutive_err+1 END,
                breaker_until=CASE WHEN ?=0 AND provider_health.consecutive_err+1 >= ?
                                   THEN ? ELSE 0 END,
                cost_per_1k=COALESCE(?, provider_health.cost_per_1k),
                updated_at=?""",
            (provider_key, ewma, okv, 1 - okv, 0 if ok else 1,
             (now + _COOLDOWN_S) if (not ok and 1 >= _FAIL_THRESHOLD) else 0.0,
             cost_per_1k, now,
             ewma, okv, 1 - okv, okv, okv, _FAIL_THRESHOLD, now + _COOLDOWN_S,
             cost_per_1k, now))
        c.execute("COMMIT")
    finally:
        c.close()


def breaker_open(provider_key: str) -> bool:
    """True when ``provider_key`` should be skipped (open). When the cooldown has
    lapsed, exactly ONE concurrent caller wins the half-open probe (returns False)
    and re-arms the breaker so the others stay blocked until that probe resolves."""
    now = _now()
    c = _conn()
    try:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT consecutive_err, breaker_until FROM provider_health "
                        "WHERE provider=?", (provider_key,)).fetchone()
        if not row:
            c.execute("COMMIT")
            return False
        streak, until = row[0], (row[1] or 0)
        if streak < _FAIL_THRESHOLD:
            c.execute("COMMIT")
            return False
        if now < until:
            c.execute("COMMIT")
            return True  # still within the open window
        # Cooldown lapsed → grant this single caller the half-open probe and re-arm
        # (holding the write lock means concurrent callers see the new window → True).
        c.execute("UPDATE provider_health SET breaker_until=? WHERE provider=?",
                  (now + _COOLDOWN_S, provider_key))
        c.execute("COMMIT")
        return False
    finally:
        c.close()


def snapshot() -> dict[str, Any]:
    """All provider health rows, for GET /v1/router/status."""
    c = _conn()
    try:
        rows = c.execute("SELECT provider, ewma_latency, ok_count, err_count, "
                         "consecutive_err, breaker_until, cost_per_1k FROM provider_health").fetchall()
    finally:
        c.close()
    out = {}
    now = _now()
    for p, lat, ok, err, streak, until, cost in rows:
        out[p] = {"ewma_latency_ms": round(lat, 2), "ok": ok, "err": err,
                  "consecutive_err": streak, "cost_per_1k": cost,
                  "breaker": "open" if (streak >= _FAIL_THRESHOLD and now < (until or 0)) else "closed"}
    return out


def reset() -> None:
    """Drop all telemetry (tests)."""
    c = _conn()
    try:
        c.execute("DELETE FROM provider_health")
    finally:
        c.close()
