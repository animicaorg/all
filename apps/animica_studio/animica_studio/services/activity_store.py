"""ActivityStore — in-memory ring-buffer of recent Studio events.

Records job completions, balance fetches, network checks, and tx sends
so the Dashboard "Recent Activity" panel always has entries to show.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from threading import Lock
from typing import ClassVar


class ActivityKind(str, Enum):
    JOB_OK = "job_ok"
    JOB_FAIL = "job_fail"
    BALANCE_FETCH = "balance_fetch"
    NETWORK_CHECK = "network_check"
    TX_SEND = "tx_send"
    WALLET_LOAD = "wallet_load"
    GENERIC = "generic"


@dataclass
class ActivityEntry:
    kind: ActivityKind
    summary: str
    ts: float = field(default_factory=time.time)
    ok: bool = True
    detail: str = ""

    @property
    def status_badge(self) -> str:
        return "✓" if self.ok else "✗"

    @property
    def age_label(self) -> str:
        """Human-readable relative age, e.g. '3s ago', '2m ago'."""
        delta = time.time() - self.ts
        if delta < 60:
            return f"{int(delta)}s ago"
        if delta < 3600:
            return f"{int(delta // 60)}m ago"
        return f"{int(delta // 3600)}h ago"


class ActivityStore:
    """Thread-safe singleton ring-buffer of recent Studio activity entries."""

    _instance: ClassVar[ActivityStore | None] = None
    _CAPACITY = 50

    def __init__(self, capacity: int = _CAPACITY) -> None:
        self._lock = Lock()
        self._entries: list[ActivityEntry] = []
        self._capacity = capacity

    # ------------------------------------------------------------------
    # Singleton
    # ------------------------------------------------------------------

    @classmethod
    def instance(cls) -> "ActivityStore":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def record(
        self,
        kind: ActivityKind,
        summary: str,
        *,
        ok: bool = True,
        detail: str = "",
        ts: float | None = None,
    ) -> ActivityEntry:
        entry = ActivityEntry(
            kind=kind,
            summary=summary,
            ok=ok,
            detail=detail,
            ts=ts if ts is not None else time.time(),
        )
        with self._lock:
            self._entries.append(entry)
            if len(self._entries) > self._capacity:
                self._entries = self._entries[-self._capacity :]
        return entry

    def record_job(self, summary: str, *, ok: bool, detail: str = "") -> ActivityEntry:
        kind = ActivityKind.JOB_OK if ok else ActivityKind.JOB_FAIL
        return self.record(kind, summary, ok=ok, detail=detail)

    def record_balance_fetch(self, summary: str, *, ok: bool, detail: str = "") -> ActivityEntry:
        return self.record(ActivityKind.BALANCE_FETCH, summary, ok=ok, detail=detail)

    def record_network_check(self, summary: str, *, ok: bool, detail: str = "") -> ActivityEntry:
        return self.record(ActivityKind.NETWORK_CHECK, summary, ok=ok, detail=detail)

    def record_tx_send(self, summary: str, *, ok: bool, detail: str = "") -> ActivityEntry:
        return self.record(ActivityKind.TX_SEND, summary, ok=ok, detail=detail)

    def record_wallet_load(self, summary: str, *, ok: bool, detail: str = "") -> ActivityEntry:
        return self.record(ActivityKind.WALLET_LOAD, summary, ok=ok, detail=detail)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_recent(self, last_n: int = 20) -> list[ActivityEntry]:
        """Return up to *last_n* most recent entries, newest first."""
        with self._lock:
            items = list(self._entries)
        items.sort(key=lambda e: e.ts, reverse=True)
        return items[:last_n]

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


__all__ = ["ActivityStore", "ActivityEntry", "ActivityKind"]
