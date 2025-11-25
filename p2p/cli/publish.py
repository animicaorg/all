#!/usr/bin/env python3
"""
Animica P2P CLI — publish
=========================

Publish a single message to a gossip topic (txs, headers, blocks, shares, blobs).
Intended as a developer tool to quickly test the gossip path.

This tool starts a minimal P2P node, dials the provided seeds, publishes one
payload on the chosen topic, waits briefly for flush/acks, and exits.

It tolerates partial installs: if the full P2P service isn't available, it will
gracefully error with a helpful message.

Examples
--------
# Publish a raw hex-encoded transaction to the txs topic
python -m p2p.cli.publish --seed /ip4/127.0.0.1/tcp/42069 --topic txs --hex 0xdeadbeef

# Publish bytes from file to shares topic
python -m p2p.cli.publish --seed /ip4/127.0.0.1/tcp/42069 --topic shares --file /path/to/hashshare.cbor

# Publish JSON (encoded to CBOR) to a custom topic
python -m p2p.cli.publish --seed /ip4/127.0.0.1/tcp/42069 --topic custom.myTopic --json '{"hello":"world"}' --encode cbor

# Dry-run (parse + show) without actually publishing
python -m p2p.cli.publish --topic txs --hex 0xdeadbeef --dry-run
"""
from __future__ import annotations

import argparse
import asyncio as _asyncio
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

# Optional faster loop
try:  # pragma: no cover
    import uvloop  # type: ignore
    uvloop.install()
except Exception:
    pass

# ---- Logging --------------------------------------------------------------------------
def _setup_logging(level: str = "INFO") -> None:
    try:
        from core.logging import setup_logging  # type: ignore
        setup_logging(level=level, fmt="text")
        return
    except Exception:
        import logging
        logging.basicConfig(
            level=getattr(logging, level.upper(), 20),
            format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
            stream=sys.stdout,
        )

# ---- Args -----------------------------------------------------------------------------
def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="animica-p2p publish", add_help=True)
    p.add_argument("--chain-id", type=int, default=1, help="Chain ID (default: 1)")
    p.add_argument("--seed", action="append", default=[], help="Seed multiaddr (repeatable)")
    p.add_argument("--listen", action="append", default=[], help="Optional local listen addrs (repeatable)")
    p.add_argument("--enable-quic", action="store_true", help="Enable QUIC (if available)")
    p.add_argument("--enable-ws", action="store_true", help="Enable WebSocket transport (if available)")
    p.add_argument("--log-level", default="INFO", help="Logging level (DEBUG, INFO, WARN, ERROR)")

    # Topic & payload
    p.add_argument("--topic", required=True, help="Topic name (e.g. txs, headers, blocks, shares, blobs, or custom.foo)")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--hex", help="Hex payload (with or without 0x)")
    g.add_argument("--file", help="Read payload bytes from file")
    g.add_argument("--json", help="JSON string; use --encode to choose wire format")

    p.add_argument("--encode", choices=["raw", "cbor", "json"], default="raw",
                   help="When providing --json, choose on-wire encoding (default raw = UTF-8 bytes)")
    p.add_argument("--dry-run", action="store_true", help="Parse and print but do not publish")
    p.add_argument("--linger", type=float, default=2.0, help="Seconds to linger after publish (default: 2.0)")
    return p

# ---- Payload helpers ------------------------------------------------------------------
def _strip0x(h: str) -> str:
    return h[2:] if h.startswith(("0x", "0X")) else h

def _load_payload(args: argparse.Namespace) -> bytes:
    if args.hex is not None:
        return bytes.fromhex(_strip0x(args.hex.strip()))
    if args.file is not None:
        with open(args.file, "rb") as f:
            return f.read()
    if args.json is not None:
        # Encode JSON either as raw UTF-8, canonical JSON bytes, or CBOR (if available)
        obj = json.loads(args.json)
        if args.encode == "json":
            # Canonical-ish JSON: sorted keys, no whitespace
            return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if args.encode == "cbor":
            # Prefer msgspec for speed, fallback to cbor2
            try:
                import msgspec  # type: ignore
                return msgspec.dumps(obj)
            except Exception:
                try:
                    import cbor2  # type: ignore
                    return cbor2.dumps(obj)
                except Exception as e:
                    raise RuntimeError("CBOR encoding requested but neither msgspec nor cbor2 is available") from e
        # raw: just UTF-8 of the original JSON string (not the object)
        return args.json.encode("utf-8")
    raise AssertionError("unreachable: one of --hex/--file/--json is required")

def _resolve_topic(name: str, chain_id: int) -> str:
    """
    Map friendly shorthands to canonical topic strings if the module is available,
    else pass-through.
    """
    try:
        from p2p.gossip.topics import canonical_topic  # type: ignore
        return canonical_topic(name, chain_id=chain_id)  # may raise -> fall through
    except Exception:
        # Minimal mapping
        short = name.strip().lower()
        base = f"animica/{chain_id}/"
        if short in {"tx", "txs"}:
            return base + "txs"
        if short in {"hdr", "header", "headers"}:
            return base + "headers"
        if short in {"blk", "block", "blocks"}:
            return base + "blocks"
        if short in {"share", "shares"}:
            return base + "shares"
        if short in {"blob", "blobs"}:
            return base + "blobs"
        # Custom, use as-is
        return name

# ---- Publisher bridge -----------------------------------------------------------------
@dataclass
class _BootCfg:
    chain_id: int
    listen_addrs: list[str]
    seeds: list[str]
    enable_quic: bool
    enable_ws: bool

class _Publisher:
    def __init__(self, cfg: _BootCfg) -> None:
        self.cfg = cfg
        self.service = None

    async def start(self) -> None:
        """
        Start a small P2P service instance able to gossip.
        """
        # Try the full node service with gossip engine
        try:
            from p2p.node.service import P2PService  # type: ignore
            self.service = P2PService(
                listen_addrs=self.cfg.listen_addrs,
                seeds=self.cfg.seeds,
                chain_id=self.cfg.chain_id,
                enable_quic=self.cfg.enable_quic,
                enable_ws=self.cfg.enable_ws,
                nat=False,
                deps=None,
            )
            await self.service.start()
            return
        except Exception as e:
            # Surface helpful error if no seeds or implementation missing
            raise RuntimeError(
                f"Unable to start P2P service for publishing: {e}\n"
                "Ensure p2p.node.service is installed and provide at least one --seed."
            ) from e

    async def publish(self, topic: str, payload: bytes) -> None:
        """
        Publish on the gossip engine; tolerate different service shapes.
        """
        if self.service is None:
            raise RuntimeError("service not started")

        # Preferred: service.gossip.publish(...)
        try:
            gossip = getattr(self.service, "gossip")
            await gossip.publish(topic, payload)  # type: ignore
            return
        except Exception:
            pass

        # Fallback: service has a generic publish method
        try:
            await self.service.publish(topic, payload)  # type: ignore
            return
        except Exception as e:
            raise RuntimeError(f"P2P service does not expose a publish method: {e}") from e

    async def stop(self) -> None:
        try:
            if self.service and hasattr(self.service, "stop"):
                await self.service.stop()
        except Exception:
            pass

# ---- Main -----------------------------------------------------------------------------
async def _amain(args: argparse.Namespace) -> int:
    _setup_logging(args.log_level)

    topic = _resolve_topic(args.topic, args.chain_id)
    payload = _load_payload(args)

    if args.dry_run:
        print("DRY-RUN (not publishing)")
        print(f"  topic   : {topic}")
        print(f"  size    : {len(payload)} bytes")
        # Show a short hexdump preview
        preview = payload[:32].hex()
        print(f"  preview : {preview}{'…' if len(payload) > 32 else ''}")
        return 0

    if not args.seed:
        print("ERROR: At least one --seed is required to publish.", file=sys.stderr)
        return 2

    boot = _BootCfg(
        chain_id=args.chain_id,
        listen_addrs=args.listen or [],  # usually none; this node only dials seeds
        seeds=args.seed,
        enable_quic=bool(args.enable_quic),
        enable_ws=bool(args.enable_ws),
    )

    pub = _Publisher(boot)
    await pub.start()
    try:
        await pub.publish(topic, payload)
        print(f"Published {len(payload)} bytes to topic '{topic}'")
        # Linger a bit so the engine can flush
        await _asyncio.sleep(max(0.0, float(args.linger)))
    finally:
        await pub.stop()
    return 0

def main(argv: Optional[list[str]] = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    args = _build_argparser().parse_args(argv)
    try:
        return _asyncio.run(_amain(args))
    except KeyboardInterrupt:
        return 130

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
