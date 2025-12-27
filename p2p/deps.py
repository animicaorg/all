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

import asyncio
import contextlib
import json
import os
import time
from dataclasses import dataclass
import shutil
from pathlib import Path
from typing import (TYPE_CHECKING, Any, Callable, Iterable, List, Optional,
                    Sequence, Tuple)

from .constants import \
    DEFAULT_TCP_PORT  # only to ensure constants import works
from .errors import P2PError

# Type hints (no heavy imports at module import time)
if TYPE_CHECKING:  # pragma: no cover
    from core.types.block import Block
    from core.types.header import Header
    from core.types.params import ChainParams
    from core.types.tx import Tx


# --------------------------------------------------------------------------- #
# Helper: lazy imports for core components
# --------------------------------------------------------------------------- #


def _lazy_core() -> dict[str, Any]:
    """
    Import core components lazily to avoid import-time cost/cycles.
    """
    # DBs
    from core.chain.block_import import import_block as core_import_block
    from core.chain.head import finalize_genesis_if_needed
    from core.chain.head import get_head as core_get_head
    from core.db.block_db import BlockDB
    from core.db.rocksdb import RocksDBKV  # guarded import internally
    from core.db.sqlite import SQLiteKV
    from core.db.state_db import StateDB
    from core.db.tx_index import TxIndex
    # Types & helpers
    from core.types.params import ChainParams

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


def _db_uri_hint(db_uri: str) -> str:
    if db_uri.startswith("sqlite:///"):
        return db_uri[len("sqlite:///") :]
    if db_uri.startswith("rocksdb:///"):
        return db_uri[len("rocksdb:///") :]
    return db_uri


def _volume_name_for_chain(
    network: Optional[str],
    chain_id: Optional[int],
    genesis_tag: Optional[str] = None,
) -> Optional[str]:
    if not network or chain_id is None:
        return None
    safe_network = network.replace("-", "_")
    tag = genesis_tag or os.getenv("ANIMICA_GENESIS_TAG")
    suffix = f"_{tag}" if tag else ""
    return f"animica_{safe_network}_chain_{chain_id}{suffix}_data"


def _format_genesis_reset_guidance(data_dir: str, chain_id: Optional[int]) -> str:
    network = os.getenv("ANIMICA_NETWORK")
    compose_file = os.getenv("ANIMICA_COMPOSE_FILE")
    genesis_tag = os.getenv("ANIMICA_GENESIS_TAG")
    data_dir_path = data_dir
    data_dir_hint = data_dir_path or "<unknown>"
    is_docker_mount = data_dir_path.startswith("/data")
    lines: list[str] = []
    lines.append("Reset guidance:")
    backend = "docker volume" if is_docker_mount else "host path"
    lines.append(f"- Data backend: {backend} ({data_dir_hint})")
    if network:
        lines.append(f"- Network: {network}")
    if compose_file:
        lines.append(f"- Compose file: {compose_file}")
    lines.append("Suggested recovery commands:")
    lines.append("  animica node down --volumes")
    if is_docker_mount:
        volume_name = _volume_name_for_chain(network, chain_id, genesis_tag)
        if volume_name:
            lines.append(f"  docker volume ls | grep {volume_name}")
            lines.append(f"  docker volume rm {volume_name}")
        if compose_file:
            lines.append(f"  docker compose -f {compose_file} down -v --remove-orphans")
    else:
        data_path = Path(data_dir_path)
        if data_path.suffix and data_path.name.endswith(".db"):
            data_path = data_path.parent
        lines.append(f"  rm -rf {data_path}")
    return "\n".join(lines)


def _allow_genesis_reset() -> bool:
    return (
        os.getenv("ANIMICA_AUTO_RESET_GENESIS_MISMATCH", "").lower()
        in {"1", "true", "yes", "on"}
        or os.getenv("ANIMICA_ALLOW_GENESIS_RESET", "").lower()
        in {"1", "true", "yes", "on"}
    )


def _close_if_possible(*handles: Any) -> None:
    for handle in handles:
        if handle is None:
            continue
        close = getattr(handle, "close", None)
        if callable(close):
            with contextlib.suppress(Exception):
                close()


def _wipe_db(db_uri: str) -> None:
    path = _db_uri_hint(db_uri)
    if db_uri.startswith("sqlite:///"):
        for suffix in ("", "-wal", "-shm"):
            candidate = f"{path}{suffix}"
            with contextlib.suppress(FileNotFoundError):
                os.remove(candidate)
        return
    if db_uri.startswith("rocksdb:///"):
        with contextlib.suppress(FileNotFoundError):
            shutil.rmtree(path)
        return
    with contextlib.suppress(FileNotFoundError):
        os.remove(path)


def _compute_genesis_identity(
    genesis_path: Optional[str],
) -> tuple[
    Optional[bytes],
    Optional[bytes],
    Optional[str],
    Optional[int],
    Optional[str],
    Optional[str],
]:
    try:
        from core.genesis.loader import compute_genesis_identity

        identity = compute_genesis_identity(genesis_path)
        return (
            identity.genesis_block_hash,
            identity.genesis_file_hash,
            str(identity.genesis_path),
            int(identity.fork_id),
            str(identity.consensus_id),
            str(identity.protocol_version),
        )
    except Exception:
        return None, None, genesis_path, None, None, None


def _db_genesis_hash(block_db: Any) -> Optional[bytes]:
    try:
        if hasattr(block_db, "get_canonical_hash"):
            h0 = block_db.get_canonical_hash(0)
            if h0:
                return bytes(h0)
    except Exception:
        pass
    try:
        if hasattr(block_db, "get_genesis_hash"):
            h0 = block_db.get_genesis_hash()
            if h0:
                return bytes(h0)
    except Exception:
        pass
    return None


def _db_genesis_file_hash(block_db: Any) -> Optional[bytes]:
    try:
        if hasattr(block_db, "get_genesis_sha256"):
            h0 = block_db.get_genesis_sha256()
            if h0:
                return bytes(h0)
    except Exception:
        pass
    return None


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
    genesis_path: Optional[str]
    chain_id: int
    _kv: Any
    _block_db: Any
    _state_db: Any
    _tx_index: Any
    _core_import_block: Callable[..., Any]
    _core_get_head: Callable[[Any], Tuple[int, "Header"]]
    expected_genesis_hash: Optional[bytes]
    expected_genesis_file_hash: Optional[bytes]
    db_genesis_hash: Optional[bytes]
    db_genesis_file_hash: Optional[bytes]
    fork_id: Optional[int]
    consensus_id: Optional[str]
    protocol_version: Optional[str]

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
    def open(
        cls,
        db_uri: str,
        genesis_path: Optional[str] = None,
        *,
        allow_genesis_reset: Optional[bool] = None,
    ) -> "P2PDeps":
        c = _lazy_core()
        kv = _open_kv(db_uri)
        block_db = c["BlockDB"](kv)
        state_db = c["StateDB"](kv)
        tx_index = c["TxIndex"](kv)

        allow_reset = _allow_genesis_reset() if allow_genesis_reset is None else allow_genesis_reset
        (
            expected_from_file,
            expected_file_hash,
            resolved_genesis_path,
            fork_id,
            consensus_id,
            protocol_version,
        ) = _compute_genesis_identity(genesis_path)
        if resolved_genesis_path:
            genesis_path = resolved_genesis_path

        # Ensure genesis finalized (idempotent)
        try:
            c["finalize_genesis_if_needed"](block_db, state_db, genesis_path)
        except Exception as exc:
            from core.errors import GenesisError, GenesisMismatchError

            if isinstance(exc, (GenesisError, GenesisMismatchError)):
                data_dir = _db_uri_hint(db_uri)
                chain_id = _read_chain_id(block_db, state_db)
                db_genesis_hash = _db_genesis_hash(block_db)
                db_genesis_file_hash = _db_genesis_file_hash(block_db)
                expected = exc.data.get("expected") if hasattr(exc, "data") else None
                found = exc.data.get("found") if hasattr(exc, "data") else None
                expected_hex = (
                    "0x" + expected_from_file.hex()
                    if expected_from_file
                    else expected
                    or "<unknown>"
                )
                found_hex = (
                    "0x" + db_genesis_hash.hex()
                    if db_genesis_hash
                    else found
                    or "<unknown>"
                )
                expected_file_hex = (
                    "0x" + expected_file_hash.hex()
                    if expected_file_hash
                    else "<unknown>"
                )
                db_file_hex = (
                    "0x" + db_genesis_file_hash.hex()
                    if db_genesis_file_hash
                    else "<unknown>"
                )
                if allow_reset:
                    _close_if_possible(kv, block_db, state_db, tx_index)
                    _wipe_db(db_uri)
                    return cls.open(
                        db_uri,
                        genesis_path,
                        allow_genesis_reset=False,
                    )
                guidance = _format_genesis_reset_guidance(data_dir, chain_id)
                raise P2PError(
                    f"GENESIS_MISMATCH expected={expected_hex} got={found_hex} "
                    f"chain_id={chain_id} genesis_path={genesis_path} data_dir={data_dir}. "
                    f"genesis_file_hash expected={expected_file_hex} got={db_file_hex}. "
                    "Refusing to sync. Reset the data dir for this chain "
                    "(e.g., delete ~/.animica/chain-<id> or docker volumes). "
                    "To auto-reset on startup, set ANIMICA_AUTO_RESET_GENESIS_MISMATCH=1 "
                    "or use `animica node up --auto-reset-genesis-mismatch`.\n"
                    f"{guidance}"
                ) from exc
            raise

        # Load chain params from state/meta; fall back to genesis file if exposed there
        # We try common locations in BlockDB/StateDB meta; exact path depends on core implementation.
        chain_id = _read_chain_id(block_db, state_db)
        expected_genesis_hash = expected_from_file
        db_genesis_hash = _db_genesis_hash(block_db)
        db_genesis_file_hash = _db_genesis_file_hash(block_db)
        if expected_genesis_hash and db_genesis_hash and expected_genesis_hash != db_genesis_hash:
            expected_hex = "0x" + expected_genesis_hash.hex()
            found_hex = "0x" + db_genesis_hash.hex()
            expected_file_hex = (
                "0x" + expected_file_hash.hex()
                if expected_file_hash
                else "<unknown>"
            )
            db_file_hex = (
                "0x" + db_genesis_file_hash.hex()
                if db_genesis_file_hash
                else "<unknown>"
            )
            data_dir = _db_uri_hint(db_uri)
            if allow_reset:
                _close_if_possible(kv, block_db, state_db, tx_index)
                _wipe_db(db_uri)
                return cls.open(
                    db_uri,
                    genesis_path,
                    allow_genesis_reset=False,
                )
            guidance = _format_genesis_reset_guidance(data_dir, chain_id)
            raise P2PError(
                f"GENESIS_MISMATCH expected={expected_hex} got={found_hex} "
                f"chain_id={chain_id} genesis_path={genesis_path} data_dir={data_dir}. "
                f"genesis_file_hash expected={expected_file_hex} got={db_file_hex}. "
                "Refusing to sync. Reset the data dir for this chain "
                "(e.g., delete ~/.animica/chain-<id> or docker volumes). "
                "To auto-reset on startup, set ANIMICA_AUTO_RESET_GENESIS_MISMATCH=1 "
                "or use `animica node up --auto-reset-genesis-mismatch`.\n"
                f"{guidance}"
            )

        if expected_genesis_hash is None:
            expected_genesis_hash = db_genesis_hash

        return cls(
            db_uri=db_uri,
            genesis_path=genesis_path,
            chain_id=chain_id,
            _kv=kv,
            _block_db=block_db,
            _state_db=state_db,
            _tx_index=tx_index,
            _core_import_block=c["core_import_block"],
            _core_get_head=c["core_get_head"],
            expected_genesis_hash=expected_genesis_hash,
            expected_genesis_file_hash=expected_file_hash,
            db_genesis_hash=db_genesis_hash,
            db_genesis_file_hash=db_genesis_file_hash,
            fork_id=fork_id,
            consensus_id=consensus_id,
            protocol_version=protocol_version,
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
        return _build_header_locator(
            height,
            lambda n: self._block_db.get_canonical_hash(n),
            max_entries=max_entries,
        )

    # ---- Blocks -------------------------------------------------------------

    def block_by_hash(self, h: bytes) -> Optional["Block"]:
        return self._block_db.get_block_by_hash(h)

    def block_by_number(self, height: int) -> Optional["Block"]:
        h = self._block_db.get_canonical_hash(height)
        if not h:
            return None
        return self._block_db.get_block_by_hash(h)

    def import_block(self, block: "Block") -> Tuple[bool, Optional[str]]:
        """
        Import a fully-formed block via core.chain.block_import.
        Returns (accepted, reason). On acceptance, canonical head may advance.
        """
        try:
            res = self._core_import_block(
                self._block_db,
                self._state_db,
                self._tx_index,
                block,
                genesis_path=self.genesis_path,
            )
            # res is expected to be a small object/tuple; support both shapes:
            if isinstance(res, tuple) and len(res) == 2:
                accepted, reason = bool(res[0]), res[1]
                if accepted:
                    try:
                        from mempool import on_block_accepted

                        on_block_accepted(block, self._state_db)
                    except Exception:
                        pass
                return accepted, reason
            if isinstance(res, bool):
                if res:
                    try:
                        from mempool import on_block_accepted

                        on_block_accepted(block, self._state_db)
                    except Exception:
                        pass
                return res, None
            if hasattr(res, "accepted"):
                accepted = bool(getattr(res, "accepted"))
                if accepted:
                    try:
                        from mempool import on_block_accepted

                        on_block_accepted(block, self._state_db)
                    except Exception:
                        pass
                return accepted, getattr(res, "reason", None)
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
        Admit a transaction received from P2P gossip to the pending pool.

        This allows transactions gossiped by peers to be added to the local
        mempool/pending pool so they can be included in blocks mined by this node.

        Notes:
            - Accepts either a decoded Tx object or raw CBOR bytes.
            - When raw bytes are provided, we avoid re-encoding to preserve the
              canonical hash and skip heavy decoding while syncing.
        """
        try:
            # Import RPC tx methods to access pending pool admission
            from rpc.methods import tx as tx_methods

            # Verify required methods are available
            if not (
                hasattr(tx_methods, "_pending_get")
                and hasattr(tx_methods, "_pending_put")
            ):
                return False, "no_pending_pool_available"

            # Encode the tx to CBOR (canonical format) or accept raw bytes
            try:
                if isinstance(tx, (bytes, bytearray)):
                    raw_cbor = bytes(tx)
                elif hasattr(tx, "to_cbor") and callable(tx.to_cbor):
                    raw_cbor = tx.to_cbor()
                elif hasattr(tx, "to_obj") and callable(tx.to_obj):
                    from core.encoding.cbor import dumps as cbor_encode

                    raw_cbor = cbor_encode(tx.to_obj())
                elif isinstance(tx, dict):
                    from core.encoding.cbor import dumps as cbor_encode

                    raw_cbor = cbor_encode(tx)
                else:
                    return False, "unsupported_tx_type"
            except Exception as e:
                return False, f"cbor_encode_failed:{e}"

            # Compute tx hash for deduplication
            try:
                from core.utils.hash import sha3_256

                tx_hash_hex = "0x" + sha3_256(raw_cbor).hex()
            except Exception as e:
                return False, f"hash_failed:{e}"

            # Check if already in pending pool (dedupe)
            existing = tx_methods._pending_get(tx_hash_hex)
            if existing is not None:
                return True, "duplicate"  # Already have it; treat as success

            # Add to pending pool using the same path as RPC submissions
            tx_methods._pending_put(tx_hash_hex, raw_cbor)
            return True, None

        except Exception as e:
            return False, f"admit_error:{e}"

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
                return (
                    False,
                    f"chain_mismatch:{getattr(header, 'chainId', None)}!= {self.chain_id}",
                )
            if header.height == 0:
                # genesis hash must match expected
                g = (
                    self._block_db.get_canonical_hash(0)
                    or self._block_db.get_genesis_hash()
                )
                expected = self.expected_genesis_hash or g
                if (
                    expected
                    and getattr(header, "hash", None)
                    and callable(getattr(header, "hash"))
                ):
                    hh = header.hash()  # type: ignore[operator]
                    if hh != expected:
                        return False, "genesis_hash_mismatch"
                elif (
                    expected
                    and getattr(header, "hash", None)
                    and isinstance(getattr(header, "hash"), (bytes, bytearray))
                ):
                    if bytes(getattr(header, "hash")) != expected:
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

    def __init__(
        self, sync: P2PDeps, executor: Optional[asyncio.AbstractEventLoop] = None
    ):
        self._sync = sync
        self._loop = asyncio.get_event_loop()

    @property
    def chain_id(self) -> int:
        return self._sync.chain_id

    async def head(self) -> Tuple[int, "Header"]:
        return await self._loop.run_in_executor(None, self._sync.head)

    async def header_locator(self, max_entries: int = 32) -> list[bytes]:
        return await self._loop.run_in_executor(
            None, self._sync.header_locator, max_entries
        )

    async def header_by_hash(self, h: bytes) -> Optional["Header"]:
        return await self._loop.run_in_executor(None, self._sync.header_by_hash, h)

    async def header_by_number(self, height: int) -> Optional["Header"]:
        return await self._loop.run_in_executor(
            None, self._sync.header_by_number, height
        )

    async def block_by_hash(self, h: bytes) -> Optional["Block"]:
        return await self._loop.run_in_executor(None, self._sync.block_by_hash, h)

    async def block_by_number(self, height: int) -> Optional["Block"]:
        return await self._loop.run_in_executor(
            None, self._sync.block_by_number, height
        )

    async def import_block(self, block: "Block") -> Tuple[bool, Optional[str]]:
        return await self._loop.run_in_executor(None, self._sync.import_block, block)

    async def tx_by_hash(self, tx_hash: bytes) -> Optional["Tx"]:
        return await self._loop.run_in_executor(None, self._sync.tx_by_hash, tx_hash)

    async def admit_tx(self, tx: "Tx") -> Tuple[bool, Optional[str]]:
        return await self._loop.run_in_executor(None, self._sync.admit_tx, tx)

    async def cheap_header_sanity(self, header: "Header") -> Tuple[bool, Optional[str]]:
        return await self._loop.run_in_executor(
            None, self._sync.cheap_header_sanity, header
        )


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
        "head_hash": (
            getattr(hdr, "hash", None).hex() if getattr(hdr, "hash", None) else None
        ),
        "locator_len_16": len(deps.header_locator(16)),
    }
    print(json.dumps(info, indent=2))
