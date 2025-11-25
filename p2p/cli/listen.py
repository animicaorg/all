#!/usr/bin/env python3
"""
Animica P2P CLI — listen
========================

Boot a standalone P2P node wired to a local DB and chain view.

This is a thin orchestration wrapper around the P2P service. It:
 - Parses CLI flags (listen addresses, seeds, chain-id, db uri, logging)
 - Wires minimal deps to the core database adapters
 - Starts transports (TCP/QUIC/WS as enabled)
 - Handles graceful shutdown on SIGINT/SIGTERM

Examples
--------
python -m p2p.cli.listen \
  --db sqlite:///animica.db \
  --chain-id 1 \
  --listen /ip4/0.0.0.0/tcp/42069 \
  --seed /dns/bootstrap.animica.example/tcp/42069 \
  --enable-quic \
  --enable-ws

Notes
-----
This CLI is resilient to partial installs. If optional transports or the full
P2P stack are not present, it will degrade gracefully and still boot with TCP.
"""
from __future__ import annotations

import argparse
import asyncio as _asyncio
import contextlib
import os
import signal
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---- Optional faster event loop -------------------------------------------------------
try:  # pragma: no cover - optional
    import uvloop  # type: ignore
    uvloop.install()
except Exception:
    pass

# ---- Logging --------------------------------------------------------------------------
def _setup_logging(level: str = "INFO") -> None:
    try:
        # Prefer project logger if available
        from core.logging import setup_logging  # type: ignore
        setup_logging(level=level, fmt="text")
        return
    except Exception:
        import logging, sys
        logging.basicConfig(
            level=getattr(logging, level.upper(), logging.INFO),
            stream=sys.stdout,
            format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        )

# ---- Config ---------------------------------------------------------------------------
DEFAULT_HOME = Path(os.environ.get("ANIMICA_HOME", Path.home() / ".animica"))
DEFAULT_DB_URI = os.environ.get("ANIMICA_DB", f"sqlite:///{DEFAULT_HOME / 'animica.db'}")

@dataclass
class ListenConfig:
    db_uri: str
    chain_id: int
    listen_addrs: List[str]
    seeds: List[str]
    enable_quic: bool
    enable_ws: bool
    nat: bool
    log_level: str

def _load_default_listen_config(args: argparse.Namespace) -> ListenConfig:
    # Attempt to import richer config (optional)
    try:
        from p2p.config import P2PConfig  # type: ignore
        cfg = P2PConfig.load()  # type: ignore[attr-defined]
        return ListenConfig(
            db_uri=args.db or getattr(cfg, "db_uri", DEFAULT_DB_URI),
            chain_id=int(args.chain_id or getattr(cfg, "chain_id", 1)),
            listen_addrs=list(args.listen or getattr(cfg, "listen_addrs", [])),
            seeds=list(args.seed or getattr(cfg, "seeds", [])),
            enable_quic=bool(args.enable_quic if args.enable_quic is not None else getattr(cfg, "enable_quic", False)),
            enable_ws=bool(args.enable_ws if args.enable_ws is not None else getattr(cfg, "enable_ws", False)),
            nat=bool(args.nat if args.nat is not None else getattr(cfg, "nat", False)),
            log_level=args.log_level or getattr(cfg, "log_level", "INFO"),
        )
    except Exception:
        return ListenConfig(
            db_uri=args.db or DEFAULT_DB_URI,
            chain_id=int(args.chain_id or 1),
            listen_addrs=list(args.listen or []),
            seeds=list(args.seed or []),
            enable_quic=bool(args.enable_quic),
            enable_ws=bool(args.enable_ws),
            nat=bool(args.nat),
            log_level=args.log_level or "INFO",
        )

# ---- Minimal deps wiring --------------------------------------------------------------
@dataclass
class _Deps:
    """Minimal dependency bundle passed to the P2P service."""
    db_uri: str
    chain_id: int
    # Optional handles; the P2P service may only need a subset
    state_db: Any = None
    block_db: Any = None
    tx_index: Any = None
    params: Any = None

def _build_deps(db_uri: str, chain_id: int) -> _Deps:
    """
    Try to instantiate the project's DB views; degrade gracefully if modules are missing.
    """
    deps = _Deps(db_uri=db_uri, chain_id=chain_id)
    try:
        from core.db.sqlite import SQLiteKV  # type: ignore
        from core.db.state_db import StateDB  # type: ignore
        from core.db.block_db import BlockDB  # type: ignore
        from core.db.tx_index import TxIndex  # type: ignore
        from core.types.params import ChainParams  # type: ignore
        # Open common KV and layer views
        kv = SQLiteKV(db_uri)
        deps.state_db = StateDB(kv)
        deps.block_db = BlockDB(kv)
        deps.tx_index = TxIndex(kv)
        # Load chain params from genesis/meta if possible
        try:
            from core.genesis.loader import load_genesis  # type: ignore
            params, _gen_hdr = load_genesis(None, kv)  # tolerant: genesis path optional if DB already init
            deps.params = params if isinstance(params, ChainParams) else None
        except Exception:
            deps.params = None
    except Exception:
        # Fall back to a skinny deps bundle with just the URI/ids
        pass
    # If a richer P2P deps builder exists, prefer it.
    try:
        from p2p.deps import build_deps as _p2p_build  # type: ignore
        maybe = _p2p_build(db_uri=db_uri, chain_id=chain_id)
        # If the builder returns something truthy, wrap/replace
        if maybe:
            # Try to copy known fields
            for k in ("state_db", "block_db", "tx_index", "params"):
                if hasattr(maybe, k):
                    setattr(deps, k, getattr(maybe, k))
    except Exception:
        pass
    return deps

# ---- P2P Service bootstrap ------------------------------------------------------------
class _ServiceBridge:
    """
    Tiny adapter that normalizes service construction across versions:
    prefers p2p.node.service.P2PService(config=..., deps=...), but supports fallbacks.
    """
    def __init__(self, cfg: ListenConfig, deps: _Deps):
        self.cfg = cfg
        self.deps = deps
        self.impl = None

    async def start(self) -> None:
        # Preferred API
        try:
            from p2p.node.service import P2PService  # type: ignore
            self.impl = P2PService(
                listen_addrs=self.cfg.listen_addrs,
                seeds=self.cfg.seeds,
                chain_id=self.cfg.chain_id,
                enable_quic=self.cfg.enable_quic,
                enable_ws=self.cfg.enable_ws,
                nat=self.cfg.nat,
                deps=self.deps,
            )
            await self.impl.start()
            return
        except Exception as e:
            # Fallback to a minimal TCP-only server if full service unavailable
            import logging
            logging.getLogger("p2p.cli.listen").warning("Falling back to minimal TCP listener: %s", e)
            from p2p.transport.tcp import TCPTransport  # type: ignore
            self.impl = TCPTransport(self.cfg.listen_addrs or ["/ip4/0.0.0.0/tcp/42069"])
            await self.impl.start()

    async def stop(self) -> None:
        with contextlib.suppress(Exception):
            if self.impl and hasattr(self.impl, "stop"):
                await self.impl.stop()

# ---- CLI -----------------------------------------------------------------------------
def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="animica-p2p listen", add_help=True)
    p.add_argument("--db", default=DEFAULT_DB_URI, help=f"DB URI (default: {DEFAULT_DB_URI})")
    p.add_argument("--chain-id", type=int, default=1, help="Chain ID (default: 1)")
    p.add_argument(
        "--listen", action="append", default=[],
        help="Listen multiaddr (repeatable), e.g. /ip4/0.0.0.0/tcp/42069",
    )
    p.add_argument("--seed", action="append", default=[], help="Seed multiaddr (repeatable)")
    p.add_argument("--enable-quic", action="store_true", help="Enable QUIC transport (if available)")
    p.add_argument("--enable-ws", action="store_true", help="Enable WebSocket transport (if available)")
    p.add_argument("--nat", action="store_true", help="Attempt NAT traversal (UPnP/NAT-PMP) if available")
    p.add_argument("--log-level", default="INFO", help="Logging level (DEBUG, INFO, WARN, ERROR)")
    return p

async def _amain(args: argparse.Namespace) -> int:
    cfg = _load_default_listen_config(args)
    _setup_logging(cfg.log_level)

    # Fill default listen addr if none provided
    if not cfg.listen_addrs:
        cfg.listen_addrs = ["/ip4/0.0.0.0/tcp/42069"]

    deps = _build_deps(cfg.db_uri, cfg.chain_id)
    bridge = _ServiceBridge(cfg, deps)

    # Start service
    await bridge.start()

    # Wait for signals
    loop = _asyncio.get_running_loop()
    stop_evt = _asyncio.Event()

    def _on_signal(*_a: Any) -> None:
        stop_evt.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _on_signal)

    # Keep-alive message
    print(
        "animica p2p listening\n"
        f"  chain_id={cfg.chain_id}\n"
        f"  db_uri={cfg.db_uri}\n"
        f"  listens={', '.join(cfg.listen_addrs)}\n"
        f"  seeds={', '.join(cfg.seeds) if cfg.seeds else '(none)'}\n"
        f"  quic={'on' if cfg.enable_quic else 'off'} ws={'on' if cfg.enable_ws else 'off'} nat={'on' if cfg.nat else 'off'}",
        flush=True,
    )

    await stop_evt.wait()
    await bridge.stop()
    return 0

def main(argv: Optional[List[str]] = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    args = _build_argparser().parse_args(argv)
    try:
        return _asyncio.run(_amain(args))
    except KeyboardInterrupt:
        return 130

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
