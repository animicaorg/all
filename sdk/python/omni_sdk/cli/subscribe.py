"""
omni_sdk.cli.subscribe
======================

Live subscriptions over WebSocket:

- `heads`    : stream `newHeads`
- `pending`  : stream `pendingTxs` (if node supports it)
- `events`   : tail contract logs per block by following heads; optionally decode via ABI

Notes
-----
The `events` command does not require a dedicated "logs" WS topic on the node.
It subscribes to `newHeads` and, for each new block, fetches receipts via HTTP
(`chain.getBlockByNumber` with receipts=True), then filters & decodes logs.

Environment / Flags
-------------------
Inherit global settings from `omni-sdk` root (rpc url, chain id, timeout).
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import typer

from ..rpc.http import RpcClient

# WS client is imported only inside commands that need it
# from ..rpc.ws import WsClient

# Optional event decoder from ABI
_EventDecoder = None
try:
    from ..contracts.events import EventDecoder  # type: ignore
    _EventDecoder = EventDecoder
except Exception:
    pass

# Root context (rpc/chain_id/timeout)
try:
    from .main import Ctx  # type: ignore
except Exception:  # pragma: no cover
    Ctx = object  # type: ignore[misc, assignment]

app = typer.Typer(help="Subscribe to heads/pending/events via WebSocket")
__all__ = ["app"]


# --------------------------- utilities ---------------------------------------

def _print_json(obj: Any, compact: bool = False) -> None:
    if compact:
        typer.echo(json.dumps(obj, separators=(",", ":"), ensure_ascii=False))
    else:
        typer.echo(json.dumps(obj, indent=2, ensure_ascii=False))


def _normalize_addr(addr: Optional[str]) -> Optional[str]:
    if addr is None:
        return None
    return addr.strip().lower()


def _load_json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise typer.BadParameter(f"File not found: {path}") from e
    except Exception as e:
        raise typer.BadParameter(f"Invalid JSON in file: {path} ({e})") from e


def _ws_url_from_http(http_url: str) -> str:
    return http_url.replace("http://", "ws://").replace("https://", "wss://")


def _iter_blocks_from(start: int, stop_inclusive: int) -> Iterable[int]:
    n = start
    while n <= stop_inclusive:
        yield n
        n += 1


# --------------------------- heads -------------------------------------------

@app.command("heads")
def heads(
    ctx: typer.Context,
    compact: bool = typer.Option(False, "--compact", help="Compact one-line JSON output"),
    throttle_ms: int = typer.Option(50, "--throttle-ms", help="Min ms between prints to reduce spam"),
) -> None:
    """
    Subscribe to `newHeads` via WebSocket and print each head.
    """
    try:
        from ..rpc.ws import WsClient  # type: ignore
    except Exception as e:  # pragma: no cover
        raise typer.BadParameter("WebSocket client not available. Install the WS dependencies.") from e

    c: Ctx = ctx.obj  # type: ignore[assignment]
    ws_url = _ws_url_from_http(c.rpc)
    client = WsClient(ws_url, timeout=c.timeout)

    typer.echo(f"Connecting to {ws_url} … (Ctrl+C to exit)")
    last = 0.0
    try:
        with client:
            sub = client.subscribe("newHeads")
            for evt in client.events(sub):
                now = time.time()
                if (now - last) * 1000.0 >= throttle_ms:
                    _print_json(evt, compact=compact)
                    last = now
    except KeyboardInterrupt:
        typer.echo("bye")


# --------------------------- pending -----------------------------------------

@app.command("pending")
def pending(
    ctx: typer.Context,
    compact: bool = typer.Option(True, "--compact/--pretty", help="Compact one-line JSON (default on)"),
    throttle_ms: int = typer.Option(0, "--throttle-ms", help="Min ms between prints (0 = unthrottled)"),
) -> None:
    """
    Subscribe to `pendingTxs` via WebSocket (if supported by the node).
    """
    try:
        from ..rpc.ws import WsClient  # type: ignore
    except Exception as e:  # pragma: no cover
        raise typer.BadParameter("WebSocket client not available. Install the WS dependencies.") from e

    c: Ctx = ctx.obj  # type: ignore[assignment]
    ws_url = _ws_url_from_http(c.rpc)
    client = WsClient(ws_url, timeout=c.timeout)

    typer.echo(f"Connecting to {ws_url} … (Ctrl+C to exit)")
    last = 0.0
    try:
        with client:
            sub = client.subscribe("pendingTxs")
            for evt in client.events(sub):
                now = time.time()
                if throttle_ms <= 0 or (now - last) * 1000.0 >= throttle_ms:
                    _print_json(evt, compact=compact)
                    last = now
    except KeyboardInterrupt:
        typer.echo("bye")


# --------------------------- events ------------------------------------------

@app.command("events")
def events(
    ctx: typer.Context,
    address: str = typer.Option(..., "--address", "-a", help="Contract address (bech32m) to filter logs"),
    abi: Path = typer.Option(..., "--abi", help="Path to ABI JSON (or manifest containing 'abi')"),
    event: Optional[str] = typer.Option(None, "--event", "-e", help="Filter by event name (requires ABI)"),
    from_block: Optional[int] = typer.Option(None, "--from-block", help="Start block number (default: current head)"),
    compact: bool = typer.Option(False, "--compact", help="Compact JSON output"),
) -> None:
    """
    Tail contract logs by following new heads. For each new block from
    --from-block (or the current head if omitted), fetch receipts and print
    matching logs. If an ABI is provided, logs are decoded.

    Filtering:
    - Address is required (--address).
    - --event filters by event name (uses ABI); if not provided, prints all for the address.
    """
    try:
        from ..rpc.ws import WsClient  # type: ignore
    except Exception as e:  # pragma: no cover
        raise typer.BadParameter("WebSocket client not available. Install the WS dependencies.") from e

    c: Ctx = ctx.obj  # type: ignore[assignment]
    http = RpcClient(c.rpc, timeout=c.timeout)
    ws_url = _ws_url_from_http(c.rpc)

    # Load ABI (supports manifest with {"abi": [...]})
    abi_obj = _load_json_file(abi)
    abi_def = abi_obj["abi"] if isinstance(abi_obj, dict) and "abi" in abi_obj else abi_obj

    # Prepare decoder if available
    decoder = None
    if _EventDecoder:
        try:
            decoder = _EventDecoder.from_abi(abi_def)  # type: ignore[attr-defined]
        except Exception:
            decoder = None  # fall back to raw

    addr_norm = _normalize_addr(address)

    # Determine starting block
    if from_block is None:
        head = http.call("chain.getHead", [])
        # head could be {"number": 123, ...}
        from_block = int(head.get("number", 0))

    # Helper: process a single block number
    def process_block(n: int) -> None:
        try:
            block = http.call("chain.getBlockByNumber", [n, False, True])  # include_receipts=True
        except Exception as e:
            typer.echo(f"warn: failed to fetch block {n}: {e}", err=True)
            return
        receipts = block.get("receipts") or []
        for r in receipts:
            logs = r.get("logs") or []
            for lg in logs:
                if _normalize_addr(lg.get("address")) != addr_norm:
                    continue
                out: Dict[str, Any] = {"blockNumber": n, "txHash": r.get("transactionHash"), "address": lg.get("address")}
                decoded_ok = False
                if decoder:
                    try:
                        # Expected to return {"event": "Name", "args": {...}, ...} or similar mapping
                        decoded = decoder.decode(lg)  # type: ignore[attr-defined]
                        if isinstance(decoded, dict):
                            out.update(decoded)
                            decoded_ok = True
                    except Exception:
                        decoded_ok = False
                if not decoded_ok:
                    # Fallback: print raw log fields
                    out.update({"topics": lg.get("topics"), "data": lg.get("data")})
                if event and out.get("event") != event:
                    continue
                _print_json(out, compact=compact)

    # Process the starting block immediately (in case a new head hasn't arrived yet)
    process_block(from_block)

    # Follow future heads
    client = WsClient(ws_url, timeout=c.timeout)
    typer.echo(f"Tailing events at {addr_norm} from block {from_block} via {ws_url} … (Ctrl+C to exit)")
    last_seen = from_block
    try:
        with client:
            sub = client.subscribe("newHeads")
            for evt in client.events(sub):
                n = int(evt.get("number", 0))
                if n <= last_seen:
                    continue
                # Handle potential skipped numbers (e.g., on reconnect)
                for bn in _iter_blocks_from(last_seen + 1, n):
                    process_block(bn)
                last_seen = n
    except KeyboardInterrupt:
        typer.echo("bye")


# --------------------------- module end ---------------------------------------

