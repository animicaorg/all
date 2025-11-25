from __future__ import annotations

"""
CoreChainAdapter
================

Glue between the miner and the local node's core components:

• get_head()                 → read canonical head (height, hash, header object)
• get_mempool_snapshot(...)  → pull ready txs (ordered) from the mempool (optional)
• submit_block(block)        → import/persist a mined candidate block; update head on success

This adapter is import-resilient: if optional modules (mempool, rocksdb) are absent,
it degrades gracefully so single-node CPU-mining demos still work.

Typical usage (single-process miner+node):

    from mining.adapters.core_chain import CoreChainAdapter
    core = CoreChainAdapter.from_sqlite(path="animica.db")
    head = core.get_head()
    txs  = core.get_mempool_snapshot(limit=500, gas_limit=head["gas_limit"])

    # ... build candidate Block (header + txs + proofs) ...
    ok = core.submit_block(block)

When running a separate miner process that talks over JSON-RPC instead of direct DB
bindings, use `mining/share_submitter.py` which targets the RPC surface, not this adapter.
"""

from dataclasses import asdict
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union, Callable

# --- Logging (best-effort) ----------------------------------------------------
try:
    from core.logging import get_logger
    log = get_logger("mining.adapters.core_chain")
except Exception:  # noqa: BLE001
    import logging
    log = logging.getLogger("mining.adapters.core_chain")
    if not log.handlers:
        logging.basicConfig(level=logging.INFO)

# --- Core DB backends ---------------------------------------------------------
KVLike = Any
BlockDBLike = Any
StateDBLike = Any

try:
    from core.db.sqlite import SQLiteKV  # default embedded KV
except Exception:  # noqa: BLE001
    SQLiteKV = None  # type: ignore[assignment]

try:
    from core.db.block_db import BlockDB
except Exception:  # noqa: BLE001
    BlockDB = None  # type: ignore[assignment]

try:
    from core.db.state_db import StateDB
except Exception:  # noqa: BLE001
    StateDB = None  # type: ignore[assignment]

# --- Core types/encoding ------------------------------------------------------
try:
    from core.types.block import Block
    from core.types.header import Header
    from core.types.tx import Tx
except Exception:  # noqa: BLE001
    Block = Any  # type: ignore[assignment]
    Header = Any  # type: ignore[assignment]
    Tx = Any  # type: ignore[assignment]

# Head helpers
_head_readers: List[Callable[..., Dict[str, Any]]] = []

# Attempt to use the canonical head module if available
try:
    from core.chain import head as head_mod  # read/write best head, finalize genesis
    def _read_head_via_module(block_db: BlockDBLike) -> Dict[str, Any]:
        hdr: Header = head_mod.read_head(block_db)  # type: ignore[attr-defined]
        return {
            "height": getattr(hdr, "height", None) or getattr(hdr, "number", None),
            "hash": getattr(hdr, "hash_hex", None) or getattr(hdr, "hash", None),
            "gas_limit": getattr(hdr, "gas_limit", None),
            "obj": hdr,
        }
    if hasattr(head_mod, "read_head"):
        _head_readers.append(_read_head_via_module)
except Exception:  # noqa: BLE001
    pass

# Fallback: ask BlockDB for head if it exposes a getter
def _read_head_via_blockdb(block_db: BlockDBLike) -> Dict[str, Any]:
    if hasattr(block_db, "get_head"):
        hdr = block_db.get_head()  # type: ignore[no-any-return]
        return {
            "height": getattr(hdr, "height", None) or getattr(hdr, "number", None),
            "hash": getattr(hdr, "hash_hex", None) or getattr(hdr, "hash", None),
            "gas_limit": getattr(hdr, "gas_limit", None),
            "obj": hdr,
        }
    raise RuntimeError("BlockDB does not expose get_head(); cannot read head")
_head_readers.append(_read_head_via_blockdb)

# --- Block import wiring (class or function) ----------------------------------
_BlockImporterCtor: Optional[Callable[..., Any]] = None
_import_block_fn: Optional[Callable[..., Any]] = None
try:
    from core.chain.block_import import BlockImporter  # preferred OO API
    _BlockImporterCtor = BlockImporter  # type: ignore[assignment]
except Exception:  # noqa: BLE001
    try:
        # Functional API fallback: import_block(block, block_db, state_db) -> result
        from core.chain.block_import import import_block as _ib  # type: ignore[no-redef]
        _import_block_fn = _ib
    except Exception:  # noqa: BLE001
        pass

# --- Mempool feed (optional) --------------------------------------------------
MinerFeedLike = Any
try:
    # Provides a ready-ordered iterator for block building
    from mempool.adapters.miner_feed import MinerFeed  # type: ignore[assignment]
except Exception:  # noqa: BLE001
    MinerFeed = None  # type: ignore[assignment]


class CoreChainAdapter:
    """
    Adapter bound to local core DBs (and optional mempool feed) for miners.
    """

    def __init__(
        self,
        kv: KVLike,
        block_db: BlockDBLike,
        state_db: Optional[StateDBLike] = None,
        miner_feed: Optional[MinerFeedLike] = None,
    ) -> None:
        self.kv = kv
        self.block_db = block_db
        self.state_db = state_db
        self.miner_feed = miner_feed
        if self.state_db is None and StateDB is not None:
            try:
                self.state_db = StateDB(self.kv)  # type: ignore[call-arg]
            except Exception:  # noqa: BLE001
                self.state_db = None

    # --- Constructors ---------------------------------------------------------
    @classmethod
    def from_sqlite(
        cls,
        path: str = "animica.db",
        attach_mempool: bool = True,
    ) -> "CoreChainAdapter":
        """
        Open (or create) a SQLite-backed KV and attach BlockDB/StateDB. Optionally bind a
        MinerFeed if mempool is importable.
        """
        if SQLiteKV is None or BlockDB is None:
            raise ImportError("core.db.sqlite or core.db.block_db not available")

        kv = SQLiteKV(f"sqlite:///{path}")
        bdb = BlockDB(kv)
        sdb = None
        try:
            if StateDB is not None:
                sdb = StateDB(kv)
        except Exception:  # noqa: BLE001
            sdb = None

        feed = None
        if attach_mempool and MinerFeed is not None:
            try:
                feed = MinerFeed(kv=kv)
            except Exception:  # noqa: BLE001
                feed = None

        return cls(kv=kv, block_db=bdb, state_db=sdb, miner_feed=feed)

    # --- Queries --------------------------------------------------------------
    def get_head(self) -> Dict[str, Any]:
        """
        Return a dict:
            { "height": int, "hash": "0x…", "gas_limit": int|None, "obj": Header }
        """
        last_err: Optional[Exception] = None
        for reader in _head_readers:
            try:
                out = reader(self.block_db)
                if out.get("hash") and out.get("height") is not None:
                    return out
            except Exception as e:  # noqa: BLE001
                last_err = e
        raise RuntimeError(f"unable to read head (tried {len(_head_readers)} strategies): {last_err}")

    def get_mempool_snapshot(
        self,
        limit: int = 1000,
        gas_limit: Optional[int] = None,
    ) -> List[Tx]:
        """
        Fetch up to `limit` ready transactions ordered by the mempool's miner priority,
        subject to (optional) block gas_limit. Returns an empty list if no mempool feed.
        """
        if self.miner_feed is None:
            log.debug("mempool feed not attached; returning empty snapshot")
            return []

        # Preferred fast path: feed exposes an ordered pop/peek API
        if hasattr(self.miner_feed, "peek_ready"):
            try:
                txs: List[Tx] = list(self.miner_feed.peek_ready(limit=limit, gas_limit=gas_limit))  # type: ignore[no-any-return]
                log.debug("fetched mempool snapshot", extra={"count": len(txs)})
                return txs
            except Exception as e:  # noqa: BLE001
                log.warning("peek_ready failed on miner_feed; falling back to empty", extra={"err": str(e)})
                return []

        # Fallback: try a simple attribute that returns an iterable of Tx
        try:
            iter_ready = getattr(self.miner_feed, "iter_ready", None)
            if callable(iter_ready):
                txs = []
                gas_used = 0
                for tx in iter_ready():  # type: ignore[call-arg]
                    if gas_limit is not None:
                        g = getattr(tx, "gas_limit", None) or getattr(tx, "gas", 0)
                        if gas_used + int(g) > int(gas_limit):
                            break
                        gas_used += int(g)
                    txs.append(tx)
                    if len(txs) >= limit:
                        break
                log.debug("fetched mempool snapshot (iter_ready)", extra={"count": len(txs)})
                return txs
        except Exception as e:  # noqa: BLE001
            log.warning("iter_ready failed on miner_feed", extra={"err": str(e)})

        return []

    # --- Mutations ------------------------------------------------------------
    def submit_block(self, block: Block) -> bool:
        """
        Import/persist a mined candidate block into the local chain database.
        Returns True on acceptance (canonical head may advance), False if rejected.

        This uses whichever import API is available:
            • BlockImporter(kv/… ).import_block(block)          (preferred)
            • import_block(block, block_db, state_db)           (fallback)
        """
        # Prefer OO importer
        if _BlockImporterCtor is not None:
            try:
                importer = _BlockImporterCtor(kv=self.kv, block_db=self.block_db, state_db=self.state_db)
            except TypeError:
                # Some implementations may expect only kv
                importer = _BlockImporterCtor(self.kv)  # type: ignore[misc]
            try:
                result = importer.import_block(block)  # type: ignore[attr-defined]
                accepted = bool(getattr(result, "accepted", True)) if result is not None else True
                if accepted:
                    log.info("block accepted by importer (class API)")
                else:
                    log.info("block rejected by importer (class API)")
                return accepted
            except Exception as e:  # noqa: BLE001
                log.error("BlockImporter.import_block raised", extra={"err": str(e)})
                return False

        # Functional fallback
        if _import_block_fn is not None:
            try:
                res = _import_block_fn(block, self.block_db, self.state_db)  # type: ignore[misc]
                accepted = bool(res) if not isinstance(res, dict) else bool(res.get("accepted", True))
                if accepted:
                    log.info("block accepted by importer (function API)")
                else:
                    log.info("block rejected by importer (function API)")
                return accepted
            except Exception as e:  # noqa: BLE001
                log.error("import_block(block, …) raised", extra={"err": str(e)})
                return False

        log.error("No block import implementation available (core.chain.block_import missing)")
        return False

    # --- Utility --------------------------------------------------------------
    def head_summary(self) -> Dict[str, Any]:
        """
        A JSON-serializable head summary for APIs/metrics/UIs.
        """
        h = self.get_head()
        obj = h.pop("obj", None)
        if obj is not None:
            # Try to make it nicely serializable without leaking huge payloads
            try:
                h["header"] = _header_to_view(obj)
            except Exception:  # noqa: BLE001
                h["header"] = None
        return h


def _header_to_view(h: Header) -> Dict[str, Any]:
    """
    Convert a Header dataclass/object into a compact dict for views.
    """
    # Dataclass fast-path
    try:
        return asdict(h)  # type: ignore[arg-type]
    except Exception:  # noqa: BLE001
        pass

    # Generic attribute extraction
    fields = (
        "chain_id", "height", "number", "parent_hash", "hash", "hash_hex", "timestamp",
        "state_root", "txs_root", "receipts_root", "proofs_root", "da_root",
        "theta", "mix_seed", "nonce", "gas_limit",
    )
    out: Dict[str, Any] = {}
    for f in fields:
        v = getattr(h, f, None)
        if v is not None:
            out[f] = v
    return out
