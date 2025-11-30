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
    """
    p = _repo_root() / "spec" / "params.yaml"
    if not p.exists():
        return {}
    raw = _load_yaml(p)

    # Normalize a few fields commonly referenced by RPC:
    out: dict[str, t.Any] = {}

    # Chain identity/name fallbacks
    cid = raw.get("chainId")
    if cid is None and chain_id is not None:
        cid = chain_id
    if cid is not None:
        out["chainId"] = cid

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
    out.setdefault("chainId", chain_id)
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
    raise RuntimeError("core.db.sqlite does not export open_kv/open_sqlite_kv/SQLiteKV/SqliteKV")


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
                    return {"height": head.get("height"), "hash": head.get("hash"), "header": head.get("header") or head}
                if isinstance(head, (tuple, list)) and len(head) >= 2:
                    height_val, hash_val = head[0], head[1]
                    header_obj = None
                    getter = getattr(self._bundle.block_db, "get_header_by_hash", None)
                    if callable(getter) and hash_val is not None:
                        try:
                            header_obj = getter(hash_val)
                        except Exception:
                            header_obj = None
                    return {"height": height_val, "hash": hash_val, "header": header_obj}
                # Fallback: try to decode via BlockDB if head is a hash/height
            # Fallback path via block_db facade:
            if hasattr(self._block_db_mod, "get_canonical_head"):
                h = self._block_db_mod.get_canonical_head(self._bundle.block_db)  # type: ignore[arg-type]
                if not h:
                    return {"height": None, "hash": None, "header": None}
                return {"height": h.get("height"), "hash": h.get("hash"), "header": h}
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
    bundle: _DbBundle, chain_id: int, genesis_path: Path | None, db_uri: str | None = None
) -> None:
    """
    Light-touch genesis bootstrap: if the DB appears empty (no head), try to
    initialize it using core.genesis.loader. If anything is missing, this is a
    no-op (RPC can still serve read-only methods with null head).
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
            return

        if genesis_path is None:
            # Default to repo genesis
            genesis_path = _repo_root() / "core" / "genesis" / "genesis.json"

        loader = _import("core.genesis.loader")
        head_mod = _import("core.chain.head")
        if hasattr(loader, "load_genesis"):
            params, header = loader.load_genesis(genesis_path, kv=bundle.kv, block_db=bundle.block_db)
            if hasattr(head_mod, "finalize_genesis"):
                head_mod.finalize_genesis(bundle.block_db, params, header)  # type: ignore[arg-type]
            return
        # Prefer an explicit bootstrap signature if present
        if hasattr(loader, "bootstrap"):
            loader.bootstrap(bundle.kv, genesis_path, chain_id)  # type: ignore
        elif hasattr(loader, "init_from_genesis"):
            loader.init_from_genesis(bundle.kv, genesis_path, chain_id)  # type: ignore
        elif hasattr(loader, "load_and_init"):
            loader.load_and_init(bundle.kv, genesis_path)  # type: ignore
        elif hasattr(loader, "load_and_init_genesis"):
            target_uri = db_uri or "sqlite:///:memory:"
            loader.load_and_init_genesis(str(genesis_path), target_uri, override_chain_id=chain_id)  # type: ignore
        # else: silently ignore (RPC will report null head)
    except Exception:
        # We deliberately swallow errors here to avoid bringing down the RPC
        # process if core/genesis evolves. The node CLI (core.boot) handles
        # authoritative bootstrapping for production.
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
            writer = getattr(bundle.block_db, "write_header", None) or getattr(bundle.block_db, "put_header", None)
            if callable(writer):
                try:
                    writer(0, header)  # type: ignore[misc]
                except Exception:
                    pass
            set_head = getattr(bundle.block_db, "set_head", None) or getattr(bundle.block_db, "set_canonical_head", None)
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
    cfg_view = _coerce_config(cfg) if cfg is not None else _load_rpc_config()
    params = _params_from_spec(cfg_view.chain_id)
    kv = _open_kv(cfg_view.db_uri)
    bundle = _build_db_facades(kv)
    _maybe_bootstrap_genesis(bundle, cfg_view.chain_id, cfg_view.genesis_path, cfg_view.db_uri)
    head = _HeadAccessor(bundle)
    return RpcContext(
        cfg=cfg_view,
        params=params,
        kv=bundle.kv,
        state_db=bundle.state_db,
        block_db=bundle.block_db,
        tx_index=bundle.tx_index,
        head=head,
    )


def get_ctx() -> RpcContext:
    with _CTX_LOCK:
        if _CTX is None:
            raise RuntimeError("RPC context not initialized. Call attach_lifecycle(...), or build_context() first.")
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
        return _CTX


async def shutdown() -> None:
    """Release process-wide resources held by the cached RpcContext."""
    with _CTX_LOCK:
        global _CTX
        if _CTX is not None:
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
    return True, {"height": head.get("height"), "hash": head.get("hash"), "db": ctx.cfg.db_uri}


# ---- FastAPI lifecycle wiring ----------------------------------------------

def attach_lifecycle(app, cfg: _ConfigView | None = None) -> None:
    """
    Attach startup/shutdown hooks to a FastAPI app so RPC handlers can call get_ctx().

    If `cfg` is not provided, it is loaded from rpc.config.load_config().
    """
    from fastapi import Request

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
    "ready",
    "shutdown",
    "startup",
    "RpcContext",
    "cbor_dumps",
    "cbor_loads",
]
