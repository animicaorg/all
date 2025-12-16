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

import json
import logging
import os
import re
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


# ---- Config glue ------------------------------------------------------------


@dataclass
class _ConfigView:
    db_uri: str
    chain_id: int
    genesis_path: Path | None
    log_level: str


def _coerce_config(cfg: t.Any) -> _ConfigView:
    """Normalize various rpc.config structures into a lightweight view.

    Accepts rpc.config.Config, rpc.config.RpcConfig, or any object exposing
    db_uri/chain_id/genesis_path/log_level attributes (case-insensitive). This
    keeps the RPC server resilient to config refactors.
    """

    def _get(name: str, default: t.Any = None) -> t.Any:
        return getattr(cfg, name, getattr(cfg, name.upper(), default))

    genesis = _get("genesis_path", None)
    if isinstance(genesis, str):
        genesis = Path(genesis).expanduser()

    return _ConfigView(
        db_uri=str(_get("db_uri", "sqlite:///animica.db")),
        chain_id=int(_get("chain_id", 1)),
        genesis_path=genesis,
        log_level=str(_get("log_level", "INFO")),
    )


def _load_rpc_config() -> _ConfigView:
    # rpc.config.load_config() → object with db_uri/chain_id/genesis/logging
    cfg_mod = _import("rpc.config")
    load_config = getattr(cfg_mod, "load_config")
    cfg = load_config()
    return _coerce_config(cfg)


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
                            getter_input = head[1] if isinstance(head[1], bytes) else hash_val
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
                return {"height": h.get("height") if isinstance(h, dict) else None, "hash": hash_val, "header": h}
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
            # DB already has a head; do not reinitialize
            return

        if genesis_path is None:
            # Default to repo genesis
            genesis_path = _repo_root() / "core" / "genesis" / "genesis.json"

        loader = _import("core.genesis.loader")
        head_mod = _import("core.chain.head")
        
        # CRITICAL: Use load_genesis with existing KV, NOT load_and_init_genesis
        # which would open a second connection to the same DB file
        if hasattr(loader, "load_genesis"):
            params, header = loader.load_genesis(
                genesis_path, kv=bundle.kv, block_db=bundle.block_db, log=True
            )
            if hasattr(head_mod, "finalize_genesis"):
                head_mod.finalize_genesis(bundle.block_db, params, header)  # type: ignore[arg-type]
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
    kv: t.Any
    state_db: t.Any
    block_db: t.Any
    tx_index: t.Any
    head: _HeadAccessor
    p2p_service: t.Any = None  # Optional P2P service for peer management

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
    
    log.info(f"Building RPC context for network: {network} (chain_id={cfg_view.chain_id})")
    log.info(f"Using database: {cfg_view.db_uri}")
    if cfg_view.genesis_path:
        log.info(f"Genesis file: {cfg_view.genesis_path}")
    
    params = _params_from_spec(cfg_view.chain_id)
    kv = _open_kv(cfg_view.db_uri)
    bundle = _build_db_facades(kv)
    
    # Check if genesis bootstrap is needed (only if no head exists)
    _maybe_bootstrap_genesis(
        bundle, cfg_view.chain_id, cfg_view.genesis_path, cfg_view.db_uri
    )
    
    head = _HeadAccessor(bundle)
    head_info = head.get()
    # head_info is a dict with 'height', 'hash', 'header' keys
    if head_info and head_info.get('height') is not None:
        log.info(f"RPC context ready: head_height={head_info.get('height')}, head_hash={head_info.get('hash')}")
    else:
        log.info("RPC context ready: no head set yet (genesis will be initialized on first use)")
    
    # Initialize P2P service if enabled
    p2p_service = None
    enable_p2p = os.environ.get("ANIMICA_P2P_ENABLE", "true").lower() in ("1", "true", "yes", "on")
    if enable_p2p:
        try:
            from p2p.node.service import P2PService
            import p2p
            
            # Determine peer store path based on network
            peerstore_path = os.environ.get("ANIMICA_PEER_STORE_PATH")
            if not peerstore_path:
                network_name = {1: "mainnet", 2: "testnet", 1337: "devnet"}.get(
                    cfg_view.chain_id, "custom"
                )
                peerstore_path = os.path.expanduser(f"~/.animica/p2p/{network_name}")
            
            # Create simple deps object for P2P service
            class _P2PDeps:
                def __init__(self, block_db):
                    self.block_db = block_db
            
            p2p_deps = _P2PDeps(bundle.block_db)
            
            # Read P2P configuration from environment
            p2p_listen = os.environ.get("P2P_LISTEN", "")
            p2p_seeds = os.environ.get("P2P_SEEDS", "")
            
            # Parse listen address to multiaddr format
            listen_addrs = None
            if p2p_listen:
                if ":" in p2p_listen and not p2p_listen.startswith("/"):
                    # Format is "host:port", convert to multiaddr
                    host, port = p2p_listen.rsplit(":", 1)
                    listen_addrs = [f"/ip4/{host}/tcp/{port}"]
                else:
                    # Already in multiaddr format
                    listen_addrs = [p2p_listen]
            
            # Parse seeds (comma-separated multiaddrs)
            seeds = [s.strip() for s in p2p_seeds.split(",") if s.strip()] if p2p_seeds else []
            
            # Initialize P2P service with persistent peer store
            # Only pass listen_addrs and seeds if they are explicitly configured
            p2p_kwargs = {
                "chain_id": cfg_view.chain_id,
                "deps": p2p_deps,
                "peerstore_path": peerstore_path,
            }
            if listen_addrs is not None:
                p2p_kwargs["listen_addrs"] = listen_addrs
            if seeds:
                p2p_kwargs["seeds"] = seeds
            
            p2p_service = P2PService(**p2p_kwargs)
            
            # Register P2P service with global registry so RPC methods can access it
            p2p.register_service(p2p_service)
            log_msg = f"Initialized P2P service: peer_store={peerstore_path}"
            if listen_addrs:
                log_msg += f", listen_addrs={listen_addrs}"
            if seeds:
                log_msg += f", seeds={len(seeds)} configured"
            log.info(log_msg)
        except Exception as e:
            log.warning(f"Failed to initialize P2P service: {e}", exc_info=True)
            p2p_service = None
    
    return RpcContext(
        cfg=cfg_view,
        params=params,
        kv=bundle.kv,
        state_db=bundle.state_db,
        block_db=bundle.block_db,
        tx_index=bundle.tx_index,
        head=head,
        p2p_service=p2p_service,
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
                logging.getLogger("animica.rpc.deps").info("P2P service started successfully")
            except Exception as e:
                logging.getLogger("animica.rpc.deps").warning(
                    f"Failed to start P2P service: {e}", exc_info=True
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
        with _CTX_LOCK:
            if cfg is None:
                cfg = _load_rpc_config()
            global _CTX
            _CTX = build_context(cfg)

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        with _CTX_LOCK:
            global _CTX
            if _CTX is not None:
                try:
                    _CTX.close()
                finally:
                    _CTX = None

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
