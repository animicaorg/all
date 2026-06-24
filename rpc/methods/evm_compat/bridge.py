"""EVM <-> Animica address bridge.

Ethereum tooling uses 20-byte 0x addresses + secp256k1; Animica uses
post-quantum anim1... addresses. This bridge gives every Animica account a
DETERMINISTIC 20-byte EVM alias = 0x || keccak256(anim1)[-20:], and remembers the
reverse mapping so eth_getBalance(0xalias) can resolve back to the anim1 account.

This is a facade alias, NOT key-compatibility: an EVM alias does not let an
secp256k1 key control the anim1 account. To actually send from an EVM wallet you
need an explicit binding (and a relayer that re-signs with the anim1 key) — see
eth_sendRawTransaction. Bindings are stored in a small SQLite DB so they survive
restarts and so any anim1 seen on-chain can be looked up by its alias once bound.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

from .formatters import keccak256

_LOCK = threading.RLock()
_CONN: Optional[sqlite3.Connection] = None
# In-process cache so deriving an alias also makes the reverse lookup work
# immediately without a DB round-trip.
_CACHE: dict[str, str] = {}


def _db_path() -> Path:
    base = os.environ.get("AICF_DB_DIR") or os.environ.get("ANIMICA_EVM_DIR")
    root = Path(base) if base else (Path.home() / ".animica" / "evm")
    root.mkdir(parents=True, exist_ok=True)
    return root / "bindings.sqlite3"


def _conn() -> sqlite3.Connection:
    global _CONN
    with _LOCK:
        if _CONN is None:
            _CONN = sqlite3.connect(str(_db_path()), check_same_thread=False, timeout=10)
            _CONN.execute("PRAGMA journal_mode=WAL")
            _CONN.execute("PRAGMA busy_timeout=5000")
            _CONN.execute(
                "CREATE TABLE IF NOT EXISTS bindings ("
                " evm_alias TEXT PRIMARY KEY, anim_address TEXT NOT NULL,"
                " bound INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL)"
            )
            _CONN.commit()
        return _CONN


def _alias_for(anim_address: str) -> str:
    """Deterministic 0x EVM alias for an Animica address."""
    digest = keccak256(str(anim_address).encode("utf-8"))
    return "0x" + digest[-20:].hex()


def anim_to_evm(anim_address: Optional[str]) -> str:
    """Animica address -> its 20-byte 0x EVM alias (also records the reverse map)."""
    if not anim_address:
        return "0x" + "00" * 20
    a = str(anim_address)
    if a.startswith(("0x", "0X")) and len(a) == 42:
        return a  # already an EVM-style address
    alias = _CACHE.get(a)
    if alias:
        return alias
    alias = _alias_for(a)
    _CACHE[a] = alias
    _CACHE[alias.lower()] = a  # reverse, in-process
    try:
        with _LOCK:
            c = _conn()
            c.execute(
                "INSERT OR IGNORE INTO bindings(evm_alias, anim_address, bound, created_at)"
                " VALUES(?,?,0,?)",
                (alias.lower(), a, int(time.time())),
            )
            c.commit()
    except Exception:
        pass
    return alias


def evm_to_anim(evm_alias: Optional[str]) -> Optional[str]:
    """0x EVM alias -> bound Animica address, or None if unknown."""
    if not evm_alias:
        return None
    key = str(evm_alias).lower()
    if key in _CACHE:
        return _CACHE[key]
    try:
        with _LOCK:
            row = _conn().execute(
                "SELECT anim_address FROM bindings WHERE evm_alias=?", (key,)
            ).fetchone()
        if row:
            _CACHE[key] = row[0]
            return row[0]
    except Exception:
        pass
    return None


def bind(anim_address: str, *, explicit: bool = True) -> dict:
    """Explicitly bind an Animica address to its EVM alias; returns both."""
    alias = anim_to_evm(anim_address)
    try:
        with _LOCK:
            c = _conn()
            c.execute(
                "INSERT INTO bindings(evm_alias, anim_address, bound, created_at)"
                " VALUES(?,?,?,?) ON CONFLICT(evm_alias) DO UPDATE SET"
                " anim_address=excluded.anim_address, bound=excluded.bound",
                (alias.lower(), anim_address, 1 if explicit else 0, int(time.time())),
            )
            c.commit()
    except Exception:
        pass
    return {"evm_alias": alias, "anim_address": anim_address}
