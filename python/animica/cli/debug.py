"""
Debugging utilities for Animica nodes.

Provides detailed diagnostic output for sync stalls and peer state.
"""

from __future__ import annotations

import asyncio
import json
import os
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

    local_head_height = sync_status.get("head_height")
    at_genesis = local_head_height is not None and int(local_head_height) == 0
    
    dump = {
        "rpc_url": url,
        "local_head_height": local_head_height,
        "local_head_hash": sync_status.get("head_hash"),
        "at_genesis": at_genesis,
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
        "inflight_block_samples": sync_status.get("inflight_block_samples") or [],
        "orphan_block_samples": sync_status.get("orphan_block_samples") or [],
        "peer_scores": (p2p_debug.get("peer_scores") if isinstance(p2p_debug, dict) else []) or [],
        "timeouts_by_peer": (p2p_debug.get("timeouts_by_peer") if isinstance(p2p_debug, dict) else {}) or {},
        "retries_by_peer": (p2p_debug.get("retries_by_peer") if isinstance(p2p_debug, dict) else {}) or {},
    }

    if json_output:
        typer.echo(json.dumps(dump, indent=2))
        return

    typer.echo("\n🧪 Sync Debug Dump\n")
    typer.echo("━" * 60)
    typer.echo(f"RPC URL:          {url}")
    typer.echo(f"Local head:       {dump['local_head_height']} ({dump['local_head_hash']})")
    
    if at_genesis:
        typer.secho(
            "⚠️  AT GENESIS - Node is at height 0",
            fg=typer.colors.YELLOW,
            bold=True,
        )
        typer.echo("   This is a special case requiring aggressive sync recovery.")
    
    typer.echo(f"Best peer head:   {best_peer_height} ({best_peer_hash}) from {best_peer}")
    
    if best_peer_height and local_head_height is not None:
        gap = best_peer_height - int(local_head_height)
        if gap > 0:
            typer.secho(
                f"   Gap: {gap} blocks behind",
                fg=typer.colors.YELLOW if gap < 100 else typer.colors.RED,
            )
    
    typer.echo(f"Sync phase:       {dump['sync_phase']}")
    typer.echo(
        "In-flight:        "
        f"headers={dump['in_flight_headers']} blocks={dump['in_flight_blocks']}"
    )
    
    # Highlight stuck in-flight requests
    if dump['in_flight_headers'] and dump['in_flight_headers'] > 0:
        typer.secho(
            f"   ⚠️  {dump['in_flight_headers']} header request(s) in-flight",
            fg=typer.colors.YELLOW,
        )
        if at_genesis:
            typer.echo("   Genesis sync should clear these aggressively after 15s")
    
    typer.echo(
        "Queues:           "
        f"pending_headers={dump['pending_header_batches']} queued_blocks={dump['queued_blocks_count']}"
    )
    
    if dump["last_progress_at"]:
        typer.echo(f"Last progress:    {dump['last_progress_at']}")
    else:
        typer.secho("Last progress:    Never", fg=typer.colors.RED)
    
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

    if dump["inflight_block_samples"]:
        typer.echo("\nIn-flight blocks (sample):")
        for item in dump["inflight_block_samples"]:
            typer.echo(
                "  - {hash} parent={parent} requested_at={requested_at} peer={peer}".format(
                    hash=item.get("hash"),
                    parent=item.get("parent_hash"),
                    requested_at=item.get("requested_at"),
                    peer=item.get("from_peer"),
                )
            )

    if dump["orphan_block_samples"]:
        typer.echo("\nOrphan pool (waiting on parent):")
        for item in dump["orphan_block_samples"]:
            typer.echo(
                "  - {hash} parent={parent} age_s={age_s} peer={peer}".format(
                    hash=item.get("hash"),
                    parent=item.get("parent_hash"),
                    age_s=item.get("age_s"),
                    peer=item.get("from_peer"),
                )
            )

    if dump["peer_scores"]:
        typer.echo("\nPeer scores:")
        for peer in dump["peer_scores"]:
            typer.echo(
                "  - {remote} score={score} penalty={penalty} sync_penalties={sync_penalties} last_response={last}".format(
                    remote=peer.get("remote"),
                    score=peer.get("score"),
                    penalty=peer.get("penalty_score"),
                    sync_penalties=peer.get("sync_penalties"),
                    last=peer.get("last_response_at"),
                )
            )

    if dump["timeouts_by_peer"] or dump["retries_by_peer"]:
        typer.echo("\nPeer retry/timeout counters:")
        for peer, count in dump["timeouts_by_peer"].items():
            typer.echo(f"  - {peer}: timeouts={count} retries={dump['retries_by_peer'].get(peer, 0)}")
    
    typer.echo("━" * 60)
    
    # Add diagnostic recommendations
    if at_genesis:
        typer.echo()
        typer.secho("💡 Genesis Sync Diagnostics:", fg=typer.colors.CYAN, bold=True)
        typer.echo("   1. Check peer connections: animica peer list")
        typer.echo("   2. Verify peers have blocks: look for best_peer_height > 0")
        typer.echo("   3. Force sync restart: animica sync force --clear-cache")
        typer.echo("   4. If still stuck, check node logs for errors")
        typer.echo("   5. Watchdog will auto-recover after 15s of no progress")
    elif dump['in_flight_headers'] and dump['in_flight_headers'] > 0:
        typer.echo()
        typer.secho("💡 In-Flight Headers Detected:", fg=typer.colors.CYAN, bold=True)
        typer.echo("   Requests should timeout after 15-20s and retry")
        typer.echo("   If stuck > 30s, watchdog will force recovery")
        typer.echo("   You can manually force: animica sync force --clear-cache")
