from __future__ import annotations

"""
rpc.deps
========
Wires the RPC layer to the running node's storage and chain view:

- Opens the configured KV (SQLite by default)
- Instantiates typed DB facades (state/blocks/tx-index)
- Exposes a light "head" accessor (height/hash/header)
- Loads canonical chain params (from spec/params.yaml), surfaced as a dict
- Provides FastAPI lifecycle hooks (startup/shutdown)
- Offers small helper methods used by rpc services

This module is intentionally defensive: it imports core/* adapters lazily and
works even if optional backends are missing. It also tolerates slightly different
symbol names in core/* (e.g., BlockDB vs block_db.BlockDB) to keep the system
robust as the repository evolves.

Typical usage
-------------
from fastapi import FastAPI
from rpc.config import load_config
from rpc.deps import attach_lifecycle, get_ctx

app = FastAPI()
attach_lifecycle(app, load_config())

# elsewhere (e.g. in handlers)
ctx = get_ctx()
head = ctx.get_head()           # {'height': int, 'hash': '0x..', 'header': <obj or dict>}
params = ctx.params             # dict (subset of spec/params.yaml)
"""

import contextlib
import json
import logging
import os
import re
import shutil
import threading
import time
import typing as t
from dataclasses import dataclass
from pathlib import Path

# ---- local imports (lazy patterns for resiliency) ---------------------------


def _import(path: str):
    """Import a module by dotted path with a crisp error if it fails."""
    import importlib

    try:
        return importlib.import_module(path)
    except Exception as e:
        raise RuntimeError(f"Failed to import {path}: {e}") from e


# ---- repo root & spec loading ----------------------------------------------


def _repo_root() -> Path:
    # repo_root/rpc/deps.py → repo_root
    return Path(__file__).resolve().parents[1]


def _load_yaml(path: Path) -> t.Dict[str, t.Any]:
    import yaml  # runtime dep present in this repo

    with path.open("rt", encoding="utf-8") as fh:
        return t.cast(dict, yaml.safe_load(fh) or {})


def _params_from_spec(chain_id: int | None = None) -> t.Dict[str, t.Any]:
    """
    Load canonical params from spec/params.yaml and return a dict view that is
    stable for RPC responses. We do not force a specific dataclass here to keep
    RPC loosely coupled to core/types. Handlers can shape/validate further.

    The params.yaml file uses a network-specific structure under a 'networks' key:
    networks:
      "animica:1": {...}    # mainnet
      "animica:2": {...}    # testnet
      "animica:1337": {...} # devnet
    """
    p = _repo_root() / "spec" / "params.yaml"
    if not p.exists():
        return {}
    raw = _load_yaml(p)

    # If chain_id is provided, try to load network-specific config
    network_key = f"animica:{chain_id}" if chain_id is not None else None

    # Check if params.yaml uses new network structure
    networks = raw.get("networks", {})
    if networks and network_key and network_key in networks:
        # Use network-specific config
        network_config = networks[network_key]
        out: dict[str, t.Any] = dict(network_config)
        # Ensure chain_id fields are set consistently
        out["chain_id"] = chain_id
        out["chainId"] = chain_id
        return out

    # Fallback: try old structure or return minimal config
    out: dict[str, t.Any] = {}

    # Chain identity/name fallbacks
    cid = raw.get("chainId")
    if cid is None and chain_id is not None:
        cid = chain_id
    if cid is not None:
        out["chainId"] = cid
        out["chain_id"] = cid

    name = raw.get("name") or raw.get("chainName")
    if name is not None:
        out["name"] = name

    # Copy selected top-level keys if present:
    for k in ("targetBlockTimeMs", "economics", "limits"):
        if k in raw:
            out[k] = raw[k]

    # Provide structured sections with safe defaults
    out["gas"] = raw.get("gas", {})
    out["block"] = raw.get("block", {})

    # Provide a compact "consensus" summary if available:
    if "pow" in raw:
        pow_ = raw["pow"]
        out["consensus"] = {
            "kind": "PoIES",
            "thetaInitial": pow_.get("thetaInitial"),
            "thetaBounds": pow_.get("thetaBounds"),
            "shareTarget": pow_.get("shareTarget"),
            "gammaCap": pow_.get("gammaCap"),
        }
    else:
        out["consensus"] = raw.get("consensus", {})

    # Ensure required keys exist even if params.yaml is skeletal
    # Set chain_id fields consistently
    if chain_id is not None:
        out["chainId"] = chain_id
        out["chain_id"] = chain_id
    out.setdefault("chainId", None)
    out.setdefault("chain_id", None)
    out.setdefault("name", "Animica")
    out.setdefault("gas", {})
    out.setdefault("block", {})
    out.setdefault("consensus", {})
    return out


def _db_uri_hint(db_uri: str) -> str:
    if db_uri.startswith("sqlite:///"):
        return db_uri[len("sqlite:///") :]
    if db_uri.startswith("rocksdb:///"):
        return db_uri[len("rocksdb:///") :]
    return db_uri


def _volume_name_for_chain(
    network: str | None, chain_id: int | None, genesis_tag: str | None = None
) -> str | None:
    if not network or chain_id is None:
        return None
    safe_network = network.replace("-", "_")
    tag = genesis_tag or os.getenv("ANIMICA_GENESIS_TAG")
    suffix = f"_{tag}" if tag else ""
    return f"animica_{safe_network}_chain_{chain_id}{suffix}_data"


def _format_genesis_reset_guidance(data_dir: str, chain_id: int | None) -> str:
    network = os.getenv("ANIMICA_NETWORK")
    compose_file = os.getenv("ANIMICA_COMPOSE_FILE")
    genesis_tag = os.getenv("ANIMICA_GENESIS_TAG")
    data_dir_hint = data_dir or "<unknown>"
    is_docker_mount = data_dir_hint.startswith("/data")
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
        data_path = Path(data_dir_hint)
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


def _close_if_possible(handle: t.Any) -> None:
    if handle is None:
        return
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


# ---- Config glue ------------------------------------------------------------


@dataclass
class _ConfigView:
    db_uri: str
    chain_id: int
    genesis_path: Path | None
    log_level: str
    p2p_required: bool


def _coerce_config(cfg: t.Any) -> _ConfigView:
    """Normalize various rpc.config structures into a lightweight view.

    Accepts rpc.config.Config, rpc.config.RpcConfig, or any object exposing
    db_uri/chain_id/genesis_path/log_level attributes (case-insensitive). This
    keeps the RPC server resilient to config refactors.
    """

    def _get(name: str, default: t.Any = None) -> t.Any:
        return getattr(cfg, name, getattr(cfg, name.upper(), default))

    def _parse_bool(value: t.Any, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)

    genesis = _get("genesis_path", None)
    if isinstance(genesis, str):
        genesis = Path(genesis).expanduser()

    return _ConfigView(
        db_uri=str(_get("db_uri", "sqlite:///animica.db")),
        chain_id=int(_get("chain_id", 1)),
        genesis_path=genesis,
        log_level=str(_get("log_level", "INFO")),
        p2p_required=_parse_bool(_get("p2p_required", None), False),
    )


def _infer_data_root(cfg: _ConfigView) -> Path:
    """Derive the canonical data root for the active chain.

    Preference order:
    1) ANIMICA_DATA_DIR (per chain)
    2) sqlite DB parent directory
    3) ~/.animica/chain-<id>
    """

    env_dir = os.environ.get("ANIMICA_DATA_DIR")
    base = Path(env_dir).expanduser() if env_dir else None

    if base is None:
        db_uri = getattr(cfg, "db_uri", "") or ""
        if isinstance(db_uri, str) and db_uri.startswith("sqlite:///"):
            db_path = Path(db_uri.split("sqlite:///")[1]).expanduser()
            if db_path != Path(":memory:"):
                base = db_path.parent

    if base is None:
        base = Path("~/.animica").expanduser()

    return base / f"chain-{cfg.chain_id}"


def _load_rpc_config() -> _ConfigView:
    # rpc.config.load_config() → object with db_uri/chain_id/genesis/logging
    cfg_mod = _import("rpc.config")
    load_config = getattr(cfg_mod, "load_config")
    cfg = load_config()
    return _coerce_config(cfg)


def _bool_env(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


# ---- KV open helpers --------------------------------------------------------


def _parse_sqlite_uri(db_uri: str) -> str:
    """
    sqlite:///absolute/path.db  → /absolute/path.db
    sqlite:///:memory:          → :memory:
    """
    m = re.match(r"^sqlite:///(.*)$", db_uri)
    if not m:
        raise ValueError(f"Unsupported DB URI (expected sqlite:///…): {db_uri}")
    path = m.group(1)
    return path if path == ":memory:" else os.path.expanduser(path)


def _open_kv(db_uri: str):
    """
    Open the backing KV store using core.db.sqlite (preferred). If RocksDB is
    configured in the future, you can extend this function to route by scheme.
    """
    path = _parse_sqlite_uri(db_uri)
    if path != ":memory":
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    db_sqlite = _import("core.db.sqlite")

    # Prefer a helper called `open_kv(path)` if present; else use the canonical
    # open_sqlite_kv() factory. Fall back to constructing the KV with a fresh
    # sqlite3 connection if no helpers exist.
    if hasattr(db_sqlite, "open_kv"):
        return db_sqlite.open_kv(path)  # type: ignore[attr-defined]
    if hasattr(db_sqlite, "open_sqlite_kv"):
        return db_sqlite.open_sqlite_kv(path)  # type: ignore[attr-defined]
    if hasattr(db_sqlite, "SQLiteKV"):
        import sqlite3

        conn = sqlite3.connect(
            path,
            isolation_level=None,
            check_same_thread=False,
        )
        return db_sqlite.SQLiteKV(conn)  # type: ignore[arg-type]
    if hasattr(db_sqlite, "SqliteKV"):
        return db_sqlite.SqliteKV(path)  # type: ignore[attr-defined]
    raise RuntimeError(
        "core.db.sqlite does not export open_kv/open_sqlite_kv/SQLiteKV/SqliteKV"
    )


# ---- DB facades & head access ----------------------------------------------


@dataclass
class _DbBundle:
    kv: t.Any
    state_db: t.Any
    block_db: t.Any
    tx_index: t.Any


def _build_db_facades(kv: t.Any) -> _DbBundle:
    db_state = _import("core.db.state_db")
    db_block = _import("core.db.block_db")
    db_txidx = _import("core.db.tx_index")

    # Accept either factory functions or classes
    state_db = getattr(db_state, "StateDB", None)
    if callable(state_db):
        state_db = state_db(kv)
    elif hasattr(db_state, "open"):
        state_db = db_state.open(kv)  # type: ignore

    block_db = getattr(db_block, "BlockDB", None)
    if callable(block_db):
        block_db = block_db(kv)
    elif hasattr(db_block, "open"):
        block_db = db_block.open(kv)  # type: ignore

    tx_index = getattr(db_txidx, "TxIndex", None)
    if callable(tx_index):
        tx_index = tx_index(kv)
    elif hasattr(db_txidx, "open"):
        tx_index = db_txidx.open(kv)  # type: ignore

    return _DbBundle(kv=kv, state_db=state_db, block_db=block_db, tx_index=tx_index)


class _HeadAccessor:
    """
    Small compatibility wrapper over core.chain.head & core.db.block_db to
    retrieve the canonical head, its height, and header object.
    """

    def __init__(self, bundle: _DbBundle) -> None:
        self._bundle = bundle
        self._head_mod = _import("core.chain.head")
        self._block_db_mod = _import("core.db.block_db")
        self._lock = threading.RLock()

    def get(self) -> dict[str, t.Any]:
        """
        Returns {'height': int|None, 'hash': '0x…'|None, 'header': <obj|dict>|None}
        """
        with self._lock:
            # Try the canonical helper path first
            if hasattr(self._head_mod, "read_head"):
                try:
                    head = self._head_mod.read_head(self._bundle.block_db)  # type: ignore[arg-type]
                except Exception:
                    head = None
                if not head:
                    return {"height": None, "hash": None, "header": None}
                # Common header shape: {'height': int, 'hash': '0x..', 'obj': header}
                if isinstance(head, dict) and "height" in head:
                    hash_val = head.get("hash")
                    # Ensure hash is hex string for JSON serialization
                    if isinstance(hash_val, bytes):
                        hash_val = "0x" + hash_val.hex()
                    return {
                        "height": head.get("height"),
                        "hash": hash_val,
                        "header": head.get("header") or head,
                    }
                if isinstance(head, (tuple, list)) and len(head) >= 2:
                    height_val, hash_val = head[0], head[1]
                    # Ensure hash is hex string for JSON serialization
                    if isinstance(hash_val, bytes):
                        hash_val = "0x" + hash_val.hex()
                    header_obj = None
                    getter = getattr(self._bundle.block_db, "get_header_by_hash", None)
                    if callable(getter) and hash_val is not None:
                        try:
                            # Pass original bytes to getter if hash_val was bytes
                            getter_input = (
                                head[1] if isinstance(head[1], bytes) else hash_val
                            )
                            header_obj = getter(getter_input)
                        except Exception:
                            header_obj = None
                    return {
                        "height": height_val,
                        "hash": hash_val,
                        "header": header_obj,
                    }
                # Fallback: try to decode via BlockDB if head is a hash/height
            # Fallback path via block_db facade:
            if hasattr(self._block_db_mod, "get_canonical_head"):
                h = self._block_db_mod.get_canonical_head(self._bundle.block_db)  # type: ignore[arg-type]
                if not h:
                    return {"height": None, "hash": None, "header": None}
                hash_val = h.get("hash") if isinstance(h, dict) else None
                # Ensure hash is hex string for JSON serialization
                if isinstance(hash_val, bytes):
                    hash_val = "0x" + hash_val.hex()
                return {
                    "height": h.get("height") if isinstance(h, dict) else None,
                    "hash": hash_val,
                    "header": h,
                }
            # Last resort: nothing known
            return {"height": None, "hash": None, "header": None}

    def height(self) -> int | None:
        return t.cast(t.Optional[int], self.get()["height"])

    def hash(self) -> str | None:
        return t.cast(t.Optional[str], self.get()["hash"])

    def header(self) -> t.Any | None:
        return self.get()["header"]


# ---- Genesis bootstrap (best-effort) ---------------------------------------


def _maybe_bootstrap_genesis(
    bundle: _DbBundle,
    chain_id: int,
    genesis_path: Path | None,
    db_uri: str | None = None,
) -> None:
    """
    Light-touch genesis bootstrap: if the DB appears empty (no head), try to
    initialize it using core.genesis.loader. If anything is missing, this is a
    no-op (RPC can still serve read-only methods with null head).

    CRITICAL: This function must NOT open a second connection to the DB that's
    already open in bundle.kv. Instead, it should use the existing KV instance
    to avoid conflicts and ensure state consistency.
    """
    try:
        head_mod = _import("core.chain.head")
        need_boot = True
        if hasattr(head_mod, "read_head"):
            try:
                h = head_mod.read_head(bundle.block_db)  # type: ignore[arg-type]
                need_boot = not bool(h)
            except Exception:
                # Absence of a head means we should attempt genesis bootstrap.
                need_boot = True

        if not need_boot:
            # DB already has a head; do not reinitialize or reseed state.
            return

        if genesis_path is None:
            genesis_loader = _import("core.genesis.genesis_loader")
            genesis_path = genesis_loader.resolve_genesis_path(None)

        loader = _import("core.genesis.loader")
        head_mod = _import("core.chain.head")

        # CRITICAL: Use load_genesis with existing KV, NOT load_and_init_genesis
        # which would open a second connection to the same DB file
        if hasattr(loader, "load_genesis"):
            params, header = loader.load_genesis(
                genesis_path, kv=bundle.kv, block_db=bundle.block_db, log=True
            )
            if hasattr(head_mod, "finalize_genesis"):
                try:
                    loader = _import("core.genesis.loader")
                    identity = loader.compute_genesis_identity(genesis_path)
                    genesis_sha256 = identity.genesis_file_hash
                except Exception:
                    genesis_sha256 = None
                head_mod.finalize_genesis(  # type: ignore[arg-type]
                    bundle.block_db,
                    params,
                    header,
                    genesis_sha256=genesis_sha256,
                    genesis_path=str(genesis_path),
                    created_at=int(time.time()),
                )
            return
        # Fallback to older bootstrap signatures that accept KV instance
        if hasattr(loader, "bootstrap"):
            loader.bootstrap(bundle.kv, genesis_path, chain_id)  # type: ignore
        elif hasattr(loader, "init_from_genesis"):
            loader.init_from_genesis(bundle.kv, genesis_path, chain_id)  # type: ignore
        elif hasattr(loader, "load_and_init"):
            loader.load_and_init(bundle.kv, genesis_path)  # type: ignore
        # DO NOT use load_and_init_genesis here as it opens a second DB connection
        # else: silently ignore (RPC will report null head)
    except Exception as e:
        from core.errors import GenesisError, GenesisMismatchError

        if isinstance(e, GenesisMismatchError):
            raise
        if isinstance(e, GenesisError):
            expected = e.data.get("expected") if hasattr(e, "data") else None
            found = e.data.get("found") if hasattr(e, "data") else None
            data_dir = _db_uri_hint(db_uri or "<unknown>")
            guidance = _format_genesis_reset_guidance(data_dir, chain_id)
            raise RuntimeError(
                "DB genesis mismatch (wrong network or corrupted DB). "
                f"expected={expected} found={found} data_dir={data_dir}. "
                "Fix: remove or reset the data dir for this chain "
                "(e.g., delete ~/.animica/chain-<id> or docker volumes).\n"
                f"{guidance}"
            ) from e
        # We deliberately swallow errors here to avoid bringing down the RPC
        # process if core/genesis evolves. The node CLI (core.boot) handles
        # authoritative bootstrapping for production.
        logging.warning(f"Genesis bootstrap failed: {type(e).__name__}: {e}")
        try:
            from core.types.header import Header
            from core.utils.hash import ZERO32

            header = Header.genesis(
                chain_id=chain_id,
                timestamp=int(time.time()),
                state_root=ZERO32,
                txs_root=ZERO32,
                receipts_root=ZERO32,
                proofs_root=ZERO32,
                da_root=ZERO32,
                mix_seed=ZERO32,
                poies_policy_root=ZERO32,
                pq_alg_policy_root=ZERO32,
                theta_micro=0,
            )
            writer = getattr(bundle.block_db, "write_header", None) or getattr(
                bundle.block_db, "put_header", None
            )
            if callable(writer):
                try:
                    writer(0, header)  # type: ignore[misc]
                except Exception:
                    pass
            set_head = getattr(bundle.block_db, "set_head", None) or getattr(
                bundle.block_db, "set_canonical_head", None
            )
            if callable(set_head):
                try:
                    set_head(0, header.hash())  # type: ignore[misc]
                except Exception:
                    pass
        except Exception:
            pass


# ---- Runtime context (singleton) -------------------------------------------


@dataclass
class RpcContext:
    cfg: _ConfigView
    params: dict[str, t.Any]
    chain_identity: t.Any | None
    kv: t.Any
    state_db: t.Any
    block_db: t.Any
    tx_index: t.Any
    head: _HeadAccessor
    data_root: Path
    mempool: t.Any | None = None
    init_error: str | None = None
    init_error_code: str | None = None
    p2p_service: t.Any = None  # Optional P2P service for peer management
    core_p2p_service: t.Any = None  # Optional core-style P2P service
    p2p_enabled: bool = False
    p2p_required: bool = False
    p2p_start_error: str | None = None

    def get_head(self) -> dict[str, t.Any]:
        return self.head.get()

    def close(self) -> None:
        # Close KV if it exposes a close() method
        try:
            close = getattr(self.kv, "close", None)
            if callable(close):
                close()
        except Exception:
            pass


_CTX: RpcContext | None = None
_CTX_LOCK = threading.RLock()


def _needs_rebuild(cfg: t.Any | None) -> bool:
    if _CTX is None:
        return True
    if cfg is None:
        return False
    try:
        cfg_view = _coerce_config(cfg)
    except Exception:
        return False
    current = getattr(_CTX, "cfg", None)
    if current is None:
        return True
    for attr in ("db_uri", "chain_id", "genesis_path"):
        if getattr(current, attr, None) != getattr(cfg_view, attr, None):
            return True
    return False


def build_context(cfg: t.Any | None = None) -> RpcContext:
    log = logging.getLogger("animica.rpc.deps")

    cfg_view = _coerce_config(cfg) if cfg is not None else _load_rpc_config()
    data_root = _infer_data_root(cfg_view)
    init_error: str | None = None
    init_error_code: str | None = None

    # Determine network name for logging
    network = os.environ.get("ANIMICA_NETWORK", "").strip().lower()
    if not network:
        # Infer from chain_id
        if cfg_view.chain_id == 1:
            network = "mainnet"
        elif cfg_view.chain_id == 2:
            network = "testnet"
        elif cfg_view.chain_id == 1337:
            network = "devnet"
        else:
            network = f"custom (chain_id={cfg_view.chain_id})"

    log.info(
        f"Building RPC context for network: {network} (chain_id={cfg_view.chain_id})"
    )
    log.info(f"Using database: {cfg_view.db_uri}")
    if cfg_view.genesis_path:
        log.info(f"Genesis file: {cfg_view.genesis_path}")

    params = _params_from_spec(cfg_view.chain_id)
    identity = None
    chain_identity = None
    try:
        from core.genesis.loader import compute_genesis_identity
        from core.genesis.loader import compute_chain_identity
        from core.network_params import enforce_pinned_genesis

        identity = compute_genesis_identity(
            cfg_view.genesis_path, chain_id=cfg_view.chain_id
        )
        chain_identity = compute_chain_identity(
            cfg_view.genesis_path, chain_id=cfg_view.chain_id
        )
        enforce_pinned_genesis(
            chain_id=identity.chain_id,
            genesis_block_hash=identity.genesis_block_hash,
            genesis_path=str(identity.genesis_path),
            network_name=network,
        )
        log.info(
            "Genesis identity: path=%s genesis_hash=0x%s genesis_file_hash=0x%s",
            identity.genesis_path,
            identity.genesis_block_hash.hex(),
            identity.genesis_file_hash.hex(),
        )
        if chain_identity is not None:
            log.info(
                "Chain identity: chain_id=%s genesis_hash=0x%s fork_id=%s consensus_id=%s protocol_version=%s",
                chain_identity.chain_id,
                chain_identity.genesis_hash.hex(),
                chain_identity.fork_id,
                chain_identity.consensus_id,
                chain_identity.protocol_version,
            )
    except Exception:
        log.exception(
            "Failed to compute genesis identity (network=%s chain_id=%s genesis_path=%s)",
            network,
            cfg_view.chain_id,
            cfg_view.genesis_path,
        )
        raise
    kv = _open_kv(cfg_view.db_uri)
    bundle = _build_db_facades(kv)

    # Check if genesis bootstrap is needed (only if no head exists)
    allow_reset = _allow_genesis_reset()
    for attempt in range(2):
        try:
            _maybe_bootstrap_genesis(
                bundle, cfg_view.chain_id, cfg_view.genesis_path, cfg_view.db_uri
            )
            break
        except Exception as exc:
            from core.errors import GenesisMismatchError

            if isinstance(exc, GenesisMismatchError):
                data_dir = _db_uri_hint(cfg_view.db_uri)
                guidance = _format_genesis_reset_guidance(data_dir, cfg_view.chain_id)
                init_error = (
                    f"NODE_INIT_FAILED: GENESIS_MISMATCH. {exc}. "
                    "Refusing to start with mismatched genesis. "
                    "Reset the data dir or set ANIMICA_AUTO_RESET_GENESIS_MISMATCH=1 "
                    "to wipe and reinitialize.\n"
                    f"{guidance}"
                )
                init_error_code = "GENESIS_MISMATCH"
                log.error(init_error)
                if allow_reset and attempt == 0:
                    log.warning(
                        "Genesis mismatch detected; auto-resetting local chain DB"
                    )
                    _close_if_possible(bundle.kv)
                    _wipe_db(cfg_view.db_uri)
                    kv = _open_kv(cfg_view.db_uri)
                    bundle = _build_db_facades(kv)
                    continue
                break
            raise

    head = _HeadAccessor(bundle)
    head_info = head.get()
    # head_info is a dict with 'height', 'hash', 'header' keys
    if head_info and head_info.get("height") is not None:
        log.info(
            f"RPC context ready: head_height={head_info.get('height')}, head_hash={head_info.get('hash')}"
        )
    else:
        log.info(
            "RPC context ready: no head set yet (genesis will be initialized on first use)"
        )

    mempool_service = None
    try:
        from rpc.mempool_service import MempoolService

        min_gas_price = 0
        try:
            min_gas_price = int(params.get("min_gas_price", 0))
        except Exception:
            min_gas_price = 0
        mempool_service = MempoolService.create(
            chain_id=cfg_view.chain_id,
            min_gas_price_wei=min_gas_price,
            state_db=bundle.state_db,
            tx_index=bundle.tx_index,
            data_dir=str(data_root),
        )
        persist_path = getattr(mempool_service, "_persist_path", None)
        log.info(
            "Mempool service initialized",
            extra={
                "min_gas_price": min_gas_price,
                "chain_id": cfg_view.chain_id,
                "mempool_id": hex(id(mempool_service)),
                "mempool_path": str(persist_path) if persist_path else None,
                "pending_path": str(persist_path) if persist_path else None,
            },
        )
    except Exception as exc:
        log.warning("Failed to initialize mempool service", exc_info=exc)

    # Initialize P2P services if enabled
    p2p_service = None
    core_p2p_service = None
    p2p_deps_sync = None
    p2p_start_error = None
    p2p_required = _bool_env("ANIMICA_P2P_REQUIRED", cfg_view.p2p_required)
    enable_p2p = _bool_env("ANIMICA_P2P_ENABLE", True)
    if init_error:
        p2p_required = False
        enable_p2p = False
        p2p_start_error = init_error
    if enable_p2p:
        try:
            import p2p
            from p2p.config import load_config as load_p2p_config
            try:
                from p2p.node.p2p_service import P2PService
            except Exception:  # pragma: no cover - legacy fallback
                from p2p.node.service import P2PServiceLegacy as P2PService
            import ipaddress

            # Set chain_id in environment so P2P config can auto-select network seeds
            os.environ.setdefault("ANIMICA_P2P_CHAIN_ID", str(cfg_view.chain_id))

            # Determine peer store path based on network
            peerstore_path = os.environ.get("ANIMICA_PEER_STORE_PATH")
            if not peerstore_path:
                peerstore_path = str((data_root / "p2p").expanduser())
            os.environ.setdefault("ANIMICA_P2P_DATA_DIR", str(peerstore_path))

            # Load P2P configuration which will automatically select network-specific seeds
            # based on chain_id (mainnet/testnet/devnet)
            p2p_config = load_p2p_config()

            # Use the P2P deps adapter (bridges to core DBs for block import + pending pool admission).
            from p2p.deps import AsyncP2PDeps, P2PDeps

            # Note: this opens its own KV handles (safe for SQLite/RocksDB in this repo).
            p2p_deps_sync = P2PDeps.open(cfg_view.db_uri, cfg_view.genesis_path)
            p2p_deps = AsyncP2PDeps(p2p_deps_sync)

            # Use config system for listen addresses and seeds
            # Allow legacy P2P_LISTEN and P2P_SEEDS env vars for backward compatibility
            p2p_listen = os.environ.get("P2P_LISTEN", "")
            p2p_seeds_legacy = os.environ.get("P2P_SEEDS", "")

            def _tcp_multiaddr(host: str, port: int) -> str:
                try:
                    ip_obj = ipaddress.ip_address(host)
                    ip_tag = "ip6" if ip_obj.version == 6 else "ip4"
                except ValueError:
                    ip_tag = "dns4"
                return f"/{ip_tag}/{host}/tcp/{port}"

            # Parse listen address to multiaddr format
            listen_addrs: list[str] = []
            if p2p_listen:
                for entry in [p.strip() for p in p2p_listen.split(",") if p.strip()]:
                    if ":" in entry and not entry.startswith("/"):
                        host, port = entry.rsplit(":", 1)
                        try:
                            listen_addrs.append(_tcp_multiaddr(host, int(port)))
                        except ValueError:
                            continue
                    else:
                        listen_addrs.append(entry)

            if not listen_addrs:
                host, port = p2p_config.listen_tcp
                listen_addrs = [_tcp_multiaddr(host, int(port))]

            # Get seeds from config (which auto-loads network-specific seeds based on chain_id)
            # or from legacy P2P_SEEDS env var for backward compatibility
            seeds = list(p2p_config.seeds) if p2p_config.seeds else []
            if not seeds and p2p_seeds_legacy:
                # Legacy fallback: parse P2P_SEEDS if no seeds from config
                seeds = [s.strip() for s in p2p_seeds_legacy.split(",") if s.strip()]

            # Initialize P2P service with persistent peer store
            p2p_kwargs = {
                "chain_id": cfg_view.chain_id,
                "deps": p2p_deps,
                "peerstore_path": peerstore_path,
            }
            if listen_addrs is not None:
                p2p_kwargs["listen_addrs"] = listen_addrs
            # Always pass seeds - either from config (network-specific) or legacy env var
            if seeds:
                p2p_kwargs["seeds"] = seeds

            p2p_service = P2PService(**p2p_kwargs)

            # Register P2P service with global registry so RPC methods can access it
            p2p.register_service(p2p_service)
            log_msg = f"Initialized P2P service: peer_store={peerstore_path}, chain_id={cfg_view.chain_id}"
            if listen_addrs:
                log_msg += f", listen_addrs={listen_addrs}"
            if seeds:
                log_msg += f", seeds={len(seeds)} configured"
            else:
                log_msg += ", no seeds configured"
            log.info(log_msg)
        except Exception as e:
            p2p_start_error = f"init_failed: {type(e).__name__}: {e}"
            log.error(f"Failed to initialize P2P service: {p2p_start_error}", exc_info=True)
            p2p_service = None
            p2p_deps_sync = None
            if not p2p_required:
                log.warning(
                    "P2P unavailable; continuing without P2P",
                    extra={"error": p2p_start_error},
                )
                enable_p2p = False
            if "GENESIS_MISMATCH" in str(e):
                if "ANIMICA_P2P_REQUIRED" not in os.environ:
                    p2p_required = False
                if not p2p_required:
                    enable_p2p = False

    enable_core_p2p = _bool_env("ANIMICA_P2P_CORE_ENABLE", True)
    if p2p_start_error and "GENESIS_MISMATCH" in p2p_start_error and not p2p_required:
        enable_core_p2p = False
    if enable_core_p2p:
        try:
            from p2p.config import load_config as load_p2p_config
            from p2p.core_p2p.chain_adapter import CoreChainAdapter
            from p2p.core_p2p.service import CoreP2PService
            from p2p.deps import P2PDeps
            from p2p.transport.multiaddr import parse_multiaddr

            if p2p_deps_sync is None:
                p2p_deps_sync = P2PDeps.open(cfg_view.db_uri, cfg_view.genesis_path)

            p2p_config = load_p2p_config()
            listen_host, listen_port = p2p_config.listen_tcp
            try:
                listen_port = int(
                    os.environ.get("ANIMICA_P2P_CORE_PORT", listen_port + 1)
                )
            except ValueError:
                listen_port = listen_port + 1
            core_listen = os.environ.get("ANIMICA_P2P_CORE_LISTEN", "").strip()
            if core_listen:
                if core_listen.startswith("/"):
                    parsed = parse_multiaddr(core_listen)
                    if parsed.transport == "tcp":
                        listen_host = parsed.host or listen_host
                        listen_port = int(parsed.port or listen_port)
                elif ":" in core_listen:
                    listen_host, port_str = core_listen.rsplit(":", 1)
                    try:
                        listen_port = int(port_str)
                    except ValueError:
                        listen_port = listen_port
                else:
                    listen_host = core_listen

            chain_adapter = CoreChainAdapter(p2p_deps_sync)
            core_p2p_service = CoreP2PService(
                chain=chain_adapter,
                listen_host=listen_host or "0.0.0.0",
                listen_port=listen_port,
                seeds=p2p_config.seeds,
                max_outbound=p2p_config.max_outbound,
                max_inbound=p2p_config.max_inbound,
            )
            log.info(
                "Initialized core P2P service",
                extra={
                    "listen": f"{listen_host}:{listen_port}",
                    "seeds": len(p2p_config.seeds),
                },
            )
        except Exception as e:
            log.warning(
                f"Failed to initialize core P2P service: {e}", exc_info=True
            )
            core_p2p_service = None

    return RpcContext(
        cfg=cfg_view,
        params=params,
        chain_identity=chain_identity,
        kv=bundle.kv,
        state_db=bundle.state_db,
        block_db=bundle.block_db,
        tx_index=bundle.tx_index,
        head=head,
        data_root=data_root,
        mempool=mempool_service,
        init_error=init_error,
        init_error_code=init_error_code,
        p2p_service=p2p_service,
        core_p2p_service=core_p2p_service,
        p2p_enabled=enable_p2p,
        p2p_required=p2p_required,
        p2p_start_error=p2p_start_error,
    )


def get_ctx() -> RpcContext:
    with _CTX_LOCK:
        if _CTX is None:
            raise RuntimeError(
                "RPC context not initialized. Call attach_lifecycle(...), or build_context() first."
            )
        return _CTX


def ensure_started(cfg: t.Any | None = None) -> RpcContext:
    """Synchronously initialize the RPC context if it is not already set."""

    with _CTX_LOCK:
        global _CTX
        if _needs_rebuild(cfg):
            if _CTX is not None:
                try:
                    _CTX.close()
                finally:
                    _CTX = None
            _CTX = build_context(cfg)
        return _CTX


async def startup(cfg: t.Any | None = None) -> RpcContext:
    """Idempotently build and cache the RPC context for the server lifecycle."""
    with _CTX_LOCK:
        global _CTX
        if _needs_rebuild(cfg):
            if _CTX is not None:
                try:
                    _CTX.close()
                finally:
                    _CTX = None
            _CTX = build_context(cfg)

        # Start P2P service if it was initialized
        if _CTX.p2p_service is not None:
            try:
                await _CTX.p2p_service.start()
                logging.getLogger("animica.rpc.deps").info(
                    "P2P service started successfully"
                )
            except Exception as e:
                _CTX.p2p_start_error = f"start_failed: {type(e).__name__}: {e}"
                log = logging.getLogger("animica.rpc.deps")
                if not _CTX.p2p_required:
                    log.warning(
                        "P2P unavailable; continuing without P2P",
                        exc_info=True,
                    )
                    _CTX.p2p_service = None
                    _CTX.p2p_enabled = False
                else:
                    log.error(
                        f"Failed to start P2P service: {_CTX.p2p_start_error}",
                        exc_info=True,
                    )
                    if _CTX.p2p_enabled:
                        raise RuntimeError("P2P enabled but failed to start")
        elif _CTX.p2p_enabled:
            error = _CTX.p2p_start_error or "P2P enabled but service not initialized"
            log = logging.getLogger("animica.rpc.deps")
            if _CTX.p2p_required:
                log.error(error)
                raise RuntimeError(error)
            log.warning(
                "P2P unavailable; continuing without P2P",
                extra={"error": error},
            )
            _CTX.p2p_enabled = False

        if _CTX.core_p2p_service is not None:
            try:
                await _CTX.core_p2p_service.start()
                logging.getLogger("animica.rpc.deps").info(
                    "Core P2P service started successfully"
                )
            except Exception as e:
                logging.getLogger("animica.rpc.deps").warning(
                    f"Failed to start core P2P service: {e}", exc_info=True
                )

        return _CTX


async def shutdown() -> None:
    """Release process-wide resources held by the cached RpcContext."""
    with _CTX_LOCK:
        global _CTX
        if _CTX is not None:
            # Stop P2P service before closing other resources
            if _CTX.p2p_service is not None:
                try:
                    await _CTX.p2p_service.stop()
                    logging.getLogger("animica.rpc.deps").info("P2P service stopped")
                except Exception as e:
                    logging.getLogger("animica.rpc.deps").warning(
                        f"Failed to stop P2P service: {e}", exc_info=True
                    )

            if _CTX.core_p2p_service is not None:
                try:
                    await _CTX.core_p2p_service.stop()
                    logging.getLogger("animica.rpc.deps").info(
                        "Core P2P service stopped"
                    )
                except Exception as e:
                    logging.getLogger("animica.rpc.deps").warning(
                        f"Failed to stop core P2P service: {e}", exc_info=True
                    )

            try:
                _CTX.close()
            finally:
                _CTX = None


async def ready() -> tuple[bool, dict[str, t.Any]]:
    """Return a readiness tuple consumed by /readyz."""
    try:
        ctx = get_ctx()
    except Exception as e:  # pragma: no cover - defensive path
        return False, {"error": str(e)}

    if ctx.init_error:
        return False, {"error": ctx.init_error, "code": ctx.init_error_code}

    head = ctx.get_head()
    hash_val = head.get("hash")

    # Convert bytes to hex string for JSON serialization
    if isinstance(hash_val, bytes):
        hash_val = "0x" + hash_val.hex()

    return True, {
        "height": head.get("height"),
        "hash": hash_val,
        "db": ctx.cfg.db_uri,
    }


# ---- FastAPI lifecycle wiring ----------------------------------------------


def attach_lifecycle(app, cfg: _ConfigView | None = None) -> None:
    """
    Attach startup/shutdown hooks to a FastAPI app so RPC handlers can call get_ctx().

    If `cfg` is not provided, it is loaded from rpc.config.load_config().
    """

    @app.on_event("startup")
    async def _startup() -> None:
        nonlocal cfg
        if cfg is None:
            cfg = _load_rpc_config()
        await startup(cfg)

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        await shutdown()

    # Optional: tiny health endpoints that do not require jsonrpc
    @app.get("/healthz", include_in_schema=False)
    async def _healthz() -> dict[str, t.Any]:
        try:
            ctx = get_ctx()
            head = ctx.get_head()
            return {"ok": True, "height": head["height"], "hash": head["hash"]}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @app.get("/readyz", include_in_schema=False)
    async def _readyz() -> dict[str, t.Any]:
        try:
            _ = get_ctx()
            return {"ready": True}
        except Exception as e:
            return {"ready": False, "error": str(e)}


# ---- Convenience helpers for handlers --------------------------------------


def get_params() -> dict[str, t.Any]:
    """Return the chain params loaded during startup (possibly empty)."""

    return ensure_started().params


def get_chain_identity() -> dict[str, t.Any]:
    """Return the chain identity (chain_id + genesis/fork/consensus/protocol bindings)."""
    ctx = ensure_started()
    identity = getattr(ctx, "chain_identity", None)
    if identity is None:
        return {
            "chainId": int(ctx.cfg.chain_id),
            "genesisHash": None,
            "genesisHeaderHash": None,
            "genesisBlockHash": None,
            "forkId": None,
            "consensusId": None,
            "protocolVersion": None,
        }
    genesis_hex = "0x" + bytes(identity.genesis_hash).hex()
    return {
        "chainId": int(identity.chain_id),
        "genesisHash": genesis_hex,
        "genesisHeaderHash": genesis_hex,
        "genesisBlockHash": genesis_hex,
        "forkId": int(identity.fork_id),
        "consensusId": str(identity.consensus_id),
        "protocolVersion": str(identity.protocol_version),
    }


def get_chain_id() -> int:
    """Return the configured chainId for this node."""

    return int(ensure_started().cfg.chain_id)


def get_head() -> dict[str, t.Any]:
    """Return the current head snapshot (height/hash/header view)."""

    return ensure_started().get_head()


def get_block_by_height(height: int) -> t.Any | None:
    """
    Retrieve a block by canonical height.

    Args:
        height: Block height (0-based)

    Returns:
        Block object if found, None otherwise
    """
    ctx = ensure_started()
    if hasattr(ctx.block_db, "get_block_by_height"):
        return ctx.block_db.get_block_by_height(height)  # type: ignore[attr-defined]
    return None


def get_block_by_hash(block_hash: bytes) -> t.Any | None:
    """
    Retrieve a block by hash.

    Args:
        block_hash: Block hash (32 bytes)

    Returns:
        Block object if found, None otherwise
    """
    ctx = ensure_started()
    if hasattr(ctx.block_db, "get_block_by_hash"):
        return ctx.block_db.get_block_by_hash(block_hash)  # type: ignore[attr-defined]
    return None


def cbor_dumps(obj: t.Any) -> bytes:
    """Expose core.encoding.cbor.dumps for handlers (with a safe fallback)."""
    try:
        cbor = _import("core.encoding.cbor")
        if hasattr(cbor, "dumps"):
            return cbor.dumps(obj)  # type: ignore
    except Exception:
        pass
    # Fallback to JSON (only for debugging; not wire-compatible)
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def cbor_loads(data: bytes) -> t.Any:
    """Expose core.encoding.cbor.loads for handlers (with a strict error if missing)."""
    cbor = _import("core.encoding.cbor")
    if hasattr(cbor, "loads"):
        return cbor.loads(data)  # type: ignore
    raise RuntimeError("core.encoding.cbor.loads not available")


__all__ = [
    "attach_lifecycle",
    "build_context",
    "ensure_started",
    "get_ctx",
    "get_chain_id",
    "get_head",
    "get_params",
    "get_block_by_height",
    "get_block_by_hash",
    "ready",
    "shutdown",
    "startup",
    "RpcContext",
    "cbor_dumps",
    "cbor_loads",
]
