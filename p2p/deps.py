"""
p2p.deps
========

Thin glue between the P2P stack and core/consensus modules.

It centralizes how P2P reads the canonical head, fetches/puts blocks and
performs cheap header/chain sanity.  Designed to be dependency-light at import
time: heavy imports happen lazily inside methods so other p2p/* modules can be
imported without pulling DBs immediately.

This module exposes two main adapters:

- P2PDeps:  synchronous API used by transports/handlers that are already off
  the event loop (or inside a worker thread).
- AsyncP2PDeps: asyncio-friendly wrapper that executes the same operations in a
  threadpool executor to keep the loop responsive.

Both speak in terms of core dataclasses (Header/Block/Tx) and raise P2PError on
user-facing failures.

Environment
-----------
- ANIMICA_DB_URI        (e.g. "sqlite:///animica.db")
- ANIMICA_CHAIN_ID      (overrides params.chainId if set)
- ANIMICA_GENESIS_PATH  (optional; if db empty, used to finalize genesis)
"""
from __future__ import annotations

import os
import json
import time
import asyncio
from dataclasses import dataclass
from typing import Callable, Iterable, Optional, Sequence, Tuple, List, Any, TYPE_CHECKING

from .errors import P2PError
from .constants import DEFAULT_TCP_PORT  # only to ensure constants import works
# Type hints (no heavy imports at module import time)
if TYPE_CHECKING:  # pragma: no cover
    from core.types.block import Block
    from core.types.header import Header
    from core.types.tx import Tx
    from core.types.params import ChainParams


# --------------------------------------------------------------------------- #
# Helper: lazy imports for core components
# --------------------------------------------------------------------------- #

def _lazy_core() -> dict[str, Any]:
    """
    Import core components lazily to avoid import-time cost/cycles.
    """
    # DBs
    from core.db.sqlite import SQLiteKV
    from core.db.rocksdb import RocksDBKV  # guarded import internally
    from core.db.block_db import BlockDB
    from core.db.state_db import StateDB
    from core.db.tx_index import TxIndex

    # Types & helpers
    from core.types.params import ChainParams
    from core.chain.head import get_head as core_get_head, finalize_genesis_if_needed
    from core.chain.block_import import import_block as core_import_block

    return dict(
        SQLiteKV=SQLiteKV,
        RocksDBKV=RocksDBKV,
        BlockDB=BlockDB,
        StateDB=StateDB,
        TxIndex=TxIndex,
        ChainParams=ChainParams,
        core_get_head=core_get_head,
        finalize_genesis_if_needed=finalize_genesis_if_needed,
        core_import_block=core_import_block,
    )


def _open_kv(db_uri: str):
    c = _lazy_core()
    if db_uri.startswith("sqlite:///"):
        path = db_uri[len("sqlite:///") :]
        return c["SQLiteKV"](path)
    if db_uri.startswith("rocksdb:///"):
        path = db_uri[len("rocksdb:///") :]
        return c["RocksDBKV"](path)
    # default to sqlite path-like
    return c["SQLiteKV"](db_uri)


# --------------------------------------------------------------------------- #
# Locators & small helpers
# --------------------------------------------------------------------------- #

def _build_header_locator(
    head_height: int,
    get_hash_by_height: Callable[[int], Optional[bytes]],
    max_entries: int = 32,
) -> list[bytes]:
    """
    Bitcoin-like exponential-backoff locator:
      h, h-1, h-2, h-4, ..., 0  (capped to max_entries; always includes genesis)
    """
    if head_height < 0:
        return []
    steps = 1
    height = head_height
    out: list[bytes] = []
    while height >= 0 and len(out) < max_entries:
        h = get_hash_by_height(height)
        if h is None:
            break
        out.append(h)
        if height == 0:
            break
        height = max(0, height - steps)
        if len(out) > 10:  # after first 10, exponentially back off faster
            steps *= 2
    if out and out[-1] != get_hash_by_height(0):
        g = get_hash_by_height(0)
        if g:
            out.append(g)
    return out


# --------------------------------------------------------------------------- #
# Core glue (sync)
# --------------------------------------------------------------------------- #

@dataclass(slots=True)
class P2PDeps:
    """
    Synchronous adapter over core DBs and chain logic.

    Typically constructed via `P2PDeps.from_env()` or `P2PDeps.open(db_uri, genesis_path=None)`.
    """

    db_uri: str
    chain_id: int
    _kv: Any
    _block_db: Any
    _state_db: Any
    _tx_index: Any
    _core_import_block: Callable[..., Any]
    _core_get_head: Callable[[Any], Tuple[int, "Header"]]

    @classmethod
    def from_env(cls) -> "P2PDeps":
        db_uri = os.getenv("ANIMICA_DB_URI", "sqlite:///animica.db")
        genesis_path = os.getenv("ANIMICA_GENESIS_PATH")
        inst = cls.open(db_uri, genesis_path)
        # env chain override
        env_chain = os.getenv("ANIMICA_CHAIN_ID")
        if env_chain:
            try:
                object.__setattr__(inst, "chain_id", int(env_chain))
            except Exception as e:
                raise P2PError(f"Invalid ANIMICA_CHAIN_ID: {env_chain}") from e
        return inst

    @classmethod
    def open(cls, db_uri: str, genesis_path: Optional[str] = None) -> "P2PDeps":
        c = _lazy_core()
        kv = _open_kv(db_uri)
        block_db = c["BlockDB"](kv)
        state_db = c["StateDB"](kv)
        tx_index = c["TxIndex"](kv)

        # Ensure genesis finalized (idempotent)
        c["finalize_genesis_if_needed"](block_db, state_db, genesis_path)

        # Load chain params from state/meta; fall back to genesis file if exposed there
        # We try common locations in BlockDB/StateDB meta; exact path depends on core implementation.
        chain_id = _read_chain_id(block_db, state_db)

        return cls(
            db_uri=db_uri,
            chain_id=chain_id,
            _kv=kv,
            _block_db=block_db,
            _state_db=state_db,
            _tx_index=tx_index,
            _core_import_block=c["core_import_block"],
            _core_get_head=c["core_get_head"],
        )

    # ---- Head & headers -----------------------------------------------------

    def head(self) -> Tuple[int, "Header"]:
        """Return (height, header) for canonical head."""
        return self._core_get_head(self._block_db)

    def header_by_number(self, height: int) -> Optional["Header"]:
        return self._block_db.get_header_by_height(height)

    def header_by_hash(self, h: bytes) -> Optional["Header"]:
        return self._block_db.get_header_by_hash(h)

    def header_locator(self, max_entries: int = 32) -> list[bytes]:
        height, _ = self.head()
        return _build_header_locator(height, lambda n: self._block_db.get_hash_by_height(n), max_entries=max_entries)

    # ---- Blocks -------------------------------------------------------------

    def block_by_hash(self, h: bytes) -> Optional["Block"]:
        return self._block_db.get_block_by_hash(h)

    def block_by_number(self, height: int) -> Optional["Block"]:
        h = self._block_db.get_hash_by_height(height)
        if not h:
            return None
        return self._block_db.get_block_by_hash(h)

    def import_block(self, block: "Block") -> Tuple[bool, Optional[str]]:
        """
        Import a fully-formed block via core.chain.block_import.
        Returns (accepted, reason). On acceptance, canonical head may advance.
        """
        try:
            res = self._core_import_block(self._block_db, self._state_db, self._tx_index, block)
            # res is expected to be a small object/tuple; support both shapes:
            if isinstance(res, tuple) and len(res) == 2:
                return bool(res[0]), res[1]
            if isinstance(res, bool):
                return res, None
            if hasattr(res, "accepted"):
                return bool(getattr(res, "accepted")), getattr(res, "reason", None)
            return True, None
        except Exception as e:
            return (False, f"import_error: {e}")

    # ---- Transactions -------------------------------------------------------

    def tx_by_hash(self, tx_hash: bytes) -> Optional["Tx"]:
        loc = self._tx_index.get(tx_hash)
        if not loc:
            return None
        height, idx = loc
        blk = self.block_by_number(height)
        if not blk:
            return None
        try:
            return blk.txs[idx]
        except Exception:
            return None

    # Admission to mempool is handled by mempool module; P2P attaches to it via adapters.
    # Here we only provide a placeholder hook that higher-level wiring can replace.
    def admit_tx(self, tx: "Tx") -> Tuple[bool, Optional[str]]:
        """
        Placeholder admission hook. Replaced at runtime by p2p.adapters.mempool if present.
        """
        return False, "no_mempool_wired"

    # ---- Cheap validation surfaces -----------------------------------------

    def cheap_header_sanity(self, header: "Header") -> Tuple[bool, Optional[str]]:
        """
        Lightweight stateless checks suitable for pre-admission:
        - chainId match
        - parent known (or is genesis)
        - monotonically non-decreasing height
        DOES NOT perform PoIES or full policy checks (consensus/validator handles that).
        """
        try:
            if getattr(header, "chainId", None) not in (None, self.chain_id):
                return False, f"chain_mismatch:{getattr(header, 'chainId', None)}!= {self.chain_id}"
            if header.height == 0:
                # genesis hash must match stored genesis
                g = self._block_db.get_hash_by_height(0)
                if g and getattr(header, "hash", None) and header.hash != g:
                    return False, "genesis_hash_mismatch"
                return True, None
            # parent must exist
            parent = getattr(header, "parentHash", None)
            if not parent:
                return False, "no_parent_hash"
            if not self._block_db.get_header_by_hash(parent):
                return False, "unknown_parent"
            # height ought to be parent.height + 1
            ph = self._block_db.get_header_by_hash(parent)
            if ph and getattr(ph, "height", None) is not None:
                if header.height != ph.height + 1:
                    return False, "bad_height"
            return True, None
        except Exception as e:
            return False, f"sanity_error:{e}"


# --------------------------------------------------------------------------- #
# Async wrapper
# --------------------------------------------------------------------------- #

class AsyncP2PDeps:
    """
    Async wrapper around P2PDeps using a shared threadpool.
    """

    def __init__(self, sync: P2PDeps, executor: Optional[asyncio.AbstractEventLoop] = None):
        self._sync = sync
        self._loop = asyncio.get_event_loop()

    @property
    def chain_id(self) -> int:
        return self._sync.chain_id

    async def head(self) -> Tuple[int, "Header"]:
        return await self._loop.run_in_executor(None, self._sync.head)

    async def header_locator(self, max_entries: int = 32) -> list[bytes]:
        return await self._loop.run_in_executor(None, self._sync.header_locator, max_entries)

    async def header_by_hash(self, h: bytes) -> Optional["Header"]:
        return await self._loop.run_in_executor(None, self._sync.header_by_hash, h)

    async def header_by_number(self, height: int) -> Optional["Header"]:
        return await self._loop.run_in_executor(None, self._sync.header_by_number, height)

    async def block_by_hash(self, h: bytes) -> Optional["Block"]:
        return await self._loop.run_in_executor(None, self._sync.block_by_hash, h)

    async def block_by_number(self, height: int) -> Optional["Block"]:
        return await self._loop.run_in_executor(None, self._sync.block_by_number, height)

    async def import_block(self, block: "Block") -> Tuple[bool, Optional[str]]:
        return await self._loop.run_in_executor(None, self._sync.import_block, block)

    async def tx_by_hash(self, tx_hash: bytes) -> Optional["Tx"]:
        return await self._loop.run_in_executor(None, self._sync.tx_by_hash, tx_hash)

    async def cheap_header_sanity(self, header: "Header") -> Tuple[bool, Optional[str]]:
        return await self._loop.run_in_executor(None, self._sync.cheap_header_sanity, header)


# --------------------------------------------------------------------------- #
# Utilities
# --------------------------------------------------------------------------- #

def _read_chain_id(block_db: Any, state_db: Any) -> int:
    """
    Best-effort chainId reader. Prefers BlockDB meta; falls back to params in state.
    """
    # Try BlockDB meta space
    if hasattr(block_db, "get_meta"):
        cid = block_db.get_meta("chain_id")
        if isinstance(cid, int):
            return cid
        if isinstance(cid, (bytes, bytearray)):
            try:
                return int(cid)
            except Exception:
                pass
        if isinstance(cid, str) and cid.isdigit():
            return int(cid)

    # Try StateDB params
    if hasattr(state_db, "get_params"):
        params = state_db.get_params()
        if params and hasattr(params, "chain_id"):
            return int(params.chain_id)

    # Last resort: look at height-0 header if present
    if hasattr(block_db, "get_header_by_height"):
        g = block_db.get_header_by_height(0)
        if g and hasattr(g, "chainId"):
            return int(getattr(g, "chainId"))

    # Default devnet id
    return 1337


# Small CLI for debugging
if __name__ == "__main__":
    deps = P2PDeps.from_env()
    h, hdr = deps.head()
    info = {
        "db_uri": deps.db_uri,
        "chain_id": deps.chain_id,
        "head_height": h,
        "head_hash": getattr(hdr, "hash", None).hex() if getattr(hdr, "hash", None) else None,
        "locator_len_16": len(deps.header_locator(16)),
    }
    print(json.dumps(info, indent=2))
