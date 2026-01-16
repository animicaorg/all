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
        dump = asyncio.run(rpc_call("sync.dump", {}, rpc_url=url, timeout=timeout))
    except Exception as exc:
        typer.secho(
            f"❌ Failed to query sync diagnostics: {exc}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)

    if not isinstance(dump, dict):
        typer.secho(
            "❌ Failed to query sync diagnostics: invalid response",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)

    peers = dump.get("peers", {}).get("connected", [])
    best_peer_height, best_peer_hash, best_peer = _best_peer_head(peers)

    head = dump.get("head", {})
    sync_status = dump.get("sync", {})
    queues = dump.get("queues", {})
    inflight = dump.get("in_flight", {})
    local_head_height = head.get("height")
    at_genesis = local_head_height is not None and int(local_head_height) == 0
    
    summary = {
        "rpc_url": url,
        "timestamp": dump.get("timestamp"),
        "local_head_height": local_head_height,
        "local_head_hash": head.get("hash"),
        "at_genesis": at_genesis,
        "best_peer_height": best_peer_height,
        "best_peer_hash": best_peer_hash,
        "best_peer": best_peer,
        "sync_phase": sync_status.get("phase") or sync_status.get("state"),
        "in_flight_headers": inflight.get("in_flight_headers"),
        "in_flight_blocks": inflight.get("in_flight_blocks"),
        "queued_blocks_count": queues.get("queued_blocks"),
        "pending_header_batches": queues.get("pending_header_batches"),
        "last_progress_at": sync_status.get("last_progress_at"),
        "last_header_error": sync_status.get("last_header_error"),
        "last_block_error": sync_status.get("last_block_error"),
        "last_block_error_peer": sync_status.get("last_block_error_peer"),
        "stall_reason": sync_status.get("stall_reason"),
        "stall_elapsed_s": sync_status.get("stall_elapsed_s"),
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
    typer.echo(f"Local head:       {summary['local_head_height']} ({summary['local_head_hash']})")
    
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
    
    typer.echo(f"Sync phase:       {summary['sync_phase']}")
    typer.echo(
        "In-flight:        "
        f"headers={summary['in_flight_headers']} blocks={summary['in_flight_blocks']}"
    )
    
    # Highlight stuck in-flight requests
    if summary['in_flight_headers'] and summary['in_flight_headers'] > 0:
        typer.secho(
            f"   ⚠️  {summary['in_flight_headers']} header request(s) in-flight",
            fg=typer.colors.YELLOW,
        )
        if at_genesis:
            typer.echo("   Genesis sync should clear these aggressively after 15s")
    
    typer.echo(
        "Queues:           "
        f"pending_headers={summary['pending_header_batches']} queued_blocks={summary['queued_blocks_count']}"
    )
    
    if summary["last_progress_at"]:
        typer.echo(f"Last progress:    {summary['last_progress_at']}")
    else:
        typer.secho("Last progress:    Never", fg=typer.colors.RED)
    
    if summary["stall_reason"]:
        typer.echo(f"Stall reason:     {summary['stall_reason']}")
    if summary.get("stall_elapsed_s") is not None:
        typer.echo(f"Stall elapsed:    {summary['stall_elapsed_s']}s")

    errors = dump.get("errors") or []
    if errors:
        typer.secho("\n⚠️  Partial dump (some sections unavailable):", fg=typer.colors.YELLOW)
        for err in errors:
            section = err.get("section")
            err_type = err.get("type")
            message = err.get("message")
            typer.echo(f"  - {section}: {err_type} ({message})")

    if summary["last_header_error"]:
        if summary["last_header_error"] == "at_tip":
            typer.echo("Last header status: at_tip (no higher headers reported)")
            typer.echo(
                "Workaround: run 'animica sync force --boost-seconds 30' to re-scan peers."
            )
        else:
            typer.echo(f"Last header error: {summary['last_header_error']}")

    if summary["last_block_error"]:
        typer.echo(f"Last block error:  {summary['last_block_error']}")

    if summary["last_block_error_peer"]:
        typer.echo(f"Block error peer:  {summary['last_block_error_peer']}")

    if summary["sync_recovery"]["last_action"]:
        typer.echo(
            f"Last recovery:    {summary['sync_recovery']['last_action']} "
            f"(attempt {summary['sync_recovery']['attempts']})"
        )

    inflight_samples = inflight.get("inflight_block_samples") or []
    if inflight_samples:
        typer.echo("\nIn-flight blocks (sample):")
        for item in inflight_samples:
            typer.echo(
                "  - {hash} parent={parent} requested_at={requested_at} peer={peer}".format(
                    hash=item.get("hash"),
                    parent=item.get("parent_hash"),
                    requested_at=item.get("requested_at"),
                    peer=item.get("from_peer"),
                )
            )

    orphan_samples = dump.get("orphans", {}).get("samples") or []
    if orphan_samples:
        typer.echo("\nOrphan pool (waiting on parent):")
        for item in orphan_samples:
            typer.echo(
                "  - {hash} parent={parent} age_s={age_s} peer={peer}".format(
                    hash=item.get("hash"),
                    parent=item.get("parent_hash"),
                    age_s=item.get("age_s"),
                    peer=item.get("from_peer"),
                )
            )

    peer_scores = dump.get("peers", {}).get("scores") or []
    if peer_scores:
        typer.echo("\nPeer scores:")
        for peer in peer_scores:
            typer.echo(
                "  - {remote} score={score} penalty={penalty} sync_penalties={sync_penalties} last_response={last}".format(
                    remote=peer.get("remote"),
                    score=peer.get("score"),
                    penalty=peer.get("penalty_score"),
                    sync_penalties=peer.get("sync_penalties"),
                    last=peer.get("last_response_at"),
                )
            )

    timeouts_by_peer = dump.get("peers", {}).get("timeouts_by_peer") or {}
    retries_by_peer = dump.get("peers", {}).get("retries_by_peer") or {}
    if timeouts_by_peer or retries_by_peer:
        typer.echo("\nPeer retry/timeout counters:")
        for peer, count in timeouts_by_peer.items():
            typer.echo(f"  - {peer}: timeouts={count} retries={retries_by_peer.get(peer, 0)}")
    
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
    elif summary['in_flight_headers'] and summary['in_flight_headers'] > 0:
        typer.echo()
        typer.secho("💡 In-Flight Headers Detected:", fg=typer.colors.CYAN, bold=True)
        typer.echo("   Requests should timeout after 15-20s and retry")
        typer.echo("   If stuck > 30s, watchdog will force recovery")
        typer.echo("   You can manually force: animica sync force --clear-cache")
