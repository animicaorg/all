"""
Debugging utilities for Animica nodes.

Provides detailed diagnostic output for sync stalls and peer state.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from typing import Any, Dict, Optional

import httpx
import typer

from animica.config import load_network_config
from .timeouts import DEFAULT_RPC_TIMEOUT, RPC_TIMEOUT_ENV, resolve_timeout

app = typer.Typer(name="debug", help="Debugging utilities.", no_args_is_help=True)

DEFAULT_RPC_URL = load_network_config().rpc_url
RPC_ENV = "ANIMICA_RPC_URL"


async def rpc_call(
    method: str,
    params: Optional[list[Any] | dict[str, Any]] = None,
    *,
    rpc_url: str,
    timeout: Optional[float] = None,
) -> Any:
    resolved_timeout = resolve_timeout(
        "RPC timeout", timeout, env_var=RPC_TIMEOUT_ENV, default=DEFAULT_RPC_TIMEOUT
    )
    payload: Dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params or [],
    }
    async with httpx.AsyncClient(timeout=resolved_timeout) as client:
        response = await client.post(rpc_url, json=payload)
        data = response.json()
    if "error" in data:
        error_info = data["error"]
        if isinstance(error_info, dict):
            error_msg = error_info.get("message", str(error_info))
        else:
            error_msg = str(error_info)
        raise RuntimeError(error_msg)
    return data.get("result")


def _resolve_rpc_url(rpc_url: Optional[str]) -> str:
    if rpc_url and rpc_url.strip():
        return rpc_url.strip()
    env_url = os.environ.get(RPC_ENV)
    if env_url and env_url.strip():
        return env_url.strip()
    return DEFAULT_RPC_URL


def _best_peer_head(peers: list[dict[str, Any]]) -> tuple[Optional[int], Optional[str], Optional[str]]:
    best_height = None
    best_hash = None
    best_peer = None
    for peer in peers:
        try:
            height = int(peer.get("head_height") or 0)
        except (TypeError, ValueError):
            continue
        if best_height is None or height > best_height:
            best_height = height
            best_hash = peer.get("head_hash")
            best_peer = peer.get("remote") or peer.get("peer_id")
    return best_height, best_hash, best_peer


def _format_timestamp(ts: Optional[float]) -> str:
    """Format a Unix timestamp as human-readable string."""
    if ts is None:
        return "N/A"
    try:
        dt = datetime.fromtimestamp(ts)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return f"{ts}"


@app.command("sync-dump")
def sync_dump(
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc", envvar=RPC_ENV, help="RPC endpoint URL"
    ),
    timeout: Optional[float] = typer.Option(
        None, "--timeout", help="RPC timeout in seconds"
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Output JSON instead of formatted text"
    ),
) -> None:
    """
    Dump detailed sync diagnostics to help debug stalls.
    """
    url = _resolve_rpc_url(rpc_url)
    try:
        sync_status = asyncio.run(
            rpc_call("sync.getStatus", {}, rpc_url=url, timeout=timeout)
        )
        p2p_debug = asyncio.run(
            rpc_call("p2p.syncDebug", {}, rpc_url=url, timeout=timeout)
        )
    except Exception as exc:
        typer.secho(f"❌ Failed to query sync diagnostics: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    peers = p2p_debug.get("connected_peers", []) if isinstance(p2p_debug, dict) else []
    best_peer_height, best_peer_hash, best_peer = _best_peer_head(peers)

    dump = {
        "rpc_url": url,
        "local_head_height": sync_status.get("head_height"),
        "local_head_hash": sync_status.get("head_hash"),
        "best_peer_height": best_peer_height,
        "best_peer_hash": best_peer_hash,
        "best_peer": best_peer,
        "sync_phase": sync_status.get("phase") or sync_status.get("state"),
        "in_flight_headers": sync_status.get("in_flight_headers"),
        "in_flight_blocks": sync_status.get("in_flight_blocks"),
        "queued_blocks_count": sync_status.get("queued_blocks_count"),
        "pending_header_batches": sync_status.get("pending_header_batches"),
        "last_progress_at": sync_status.get("last_progress_at"),
        "last_header_error": sync_status.get("last_header_error"),
        "last_block_error": sync_status.get("last_block_error"),
        "last_block_error_peer": sync_status.get("last_block_error_peer"),
        "stall_reason": sync_status.get("stall_reason"),
        "stall_elapsed_s": sync_status.get("stall_elapsed_s"),
        "eligible_header_peers": sync_status.get("eligible_peers_for_headers"),
        "eligible_block_peers": sync_status.get("eligible_peers_for_blocks"),
        "active_block_peer": sync_status.get("active_peer_for_blocks"),
        "peer_error_summary": sync_status.get("block_error_summary"),
        "sync_recovery": {
            "attempts": sync_status.get("recovery_attempts"),
            "last_action": sync_status.get("last_recovery_action"),
        },
    }

    if json_output:
        typer.echo(json.dumps(dump, indent=2))
        return

    typer.echo("\n🧪 Sync Debug Dump\n")
    typer.echo("━" * 60)
    typer.echo(f"RPC URL:          {url}")
    typer.echo(f"Local head:       {dump['local_head_height']} ({dump['local_head_hash']})")
    typer.echo(f"Best peer head:   {best_peer_height} ({best_peer_hash}) from {best_peer}")
    typer.echo(f"Sync phase:       {dump['sync_phase']}")
    typer.echo(
        "In-flight:        "
        f"headers={dump['in_flight_headers']} blocks={dump['in_flight_blocks']}"
    )
    typer.echo(
        "Queues:           "
        f"pending_headers={dump['pending_header_batches']} queued_blocks={dump['queued_blocks_count']}"
    )
    typer.echo(f"Last progress:    {_format_timestamp(dump['last_progress_at'])}")
    if dump["stall_reason"]:
        typer.echo(f"Stall reason:     {dump['stall_reason']}")
        typer.echo(f"Stall elapsed:    {dump['stall_elapsed_s']}s")
    if dump["last_header_error"]:
        if dump["last_header_error"] == "at_tip":
            typer.echo("Last header status: at_tip (no higher headers reported)")
            typer.echo(
                "Workaround: run 'animica sync force --boost-seconds 30' to re-scan peers."
            )
        else:
            typer.echo(f"Last header error: {dump['last_header_error']}")
    if dump["last_block_error"]:
        typer.echo(f"Last block error:  {dump['last_block_error']}")
    if dump["last_block_error_peer"]:
        typer.echo(f"Block error peer:  {dump['last_block_error_peer']}")
    if dump["sync_recovery"]["last_action"]:
        typer.echo(
            f"Last recovery:    {dump['sync_recovery']['last_action']} "
            f"(attempt {dump['sync_recovery']['attempts']})"
        )
    typer.echo("━" * 60)
