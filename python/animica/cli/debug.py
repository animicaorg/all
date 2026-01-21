"""
Debugging utilities for Animica nodes.

Provides detailed diagnostic output for sync stalls and peer state.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
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


@app.command("health")
def health(
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
    Display node health diagnostics: peer state, tip freshness, sync status.
    
    This command provides a comprehensive view of:
    - Peer connection states (DIALING/HANDSHAKING/CONNECTED/FAILED)
    - Peer tip freshness and staleness
    - Sync queue depths and in-flight requests
    - Recent errors and backoff timers
    """
    url = _resolve_rpc_url(rpc_url)
    
    try:
        # Fetch all diagnostic data
        node_status = asyncio.run(rpc_call("node.getStatus", {}, rpc_url=url, timeout=timeout))
        sync_dump = asyncio.run(rpc_call("sync.dump", {}, rpc_url=url, timeout=timeout))
        peers_list = asyncio.run(rpc_call("p2p.listPeers", {}, rpc_url=url, timeout=timeout))
    except Exception as exc:
        typer.secho(
            f"❌ Failed to query node health: {exc}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)
    
    # Extract key metrics
    chain = node_status.get("chain", {})
    p2p = node_status.get("p2p", {})
    sync = node_status.get("sync", {})
    
    head_height = chain.get("head", {}).get("height", 0)
    head_hash = chain.get("head", {}).get("hash", "")
    
    peers_total = p2p.get("peers_total", 0)
    peers_connected = p2p.get("peers_connected", 0)
    peers_handshaking = p2p.get("peers_handshaking", 0)
    peers_inbound = p2p.get("peers_inbound", 0)
    peers_outbound = p2p.get("peers_outbound", 0)
    
    sync_phase = sync.get("phase", "UNKNOWN")
    target_height = sync.get("targetHeight")
    
    # Analyze peer states
    peer_states = {}
    peer_tips = {"total": 0, "fresh": 0, "stale": 0, "missing": 0}
    
    if isinstance(peers_list, list):
        for peer in peers_list:
            state = peer.get("state", "UNKNOWN")
            peer_states[state] = peer_states.get(state, 0) + 1
            
            # Check tip freshness
            if peer.get("tip_height") is not None:
                peer_tips["total"] += 1
                tip_updated_at = peer.get("tip_updated_at")
                if tip_updated_at:
                    age = time.time() - tip_updated_at
                    if age < 600:  # 10 minutes
                        peer_tips["fresh"] += 1
                    else:
                        peer_tips["stale"] += 1
            else:
                peer_tips["missing"] += 1
    
    # Compile health report
    health_report = {
        "timestamp": sync_dump.get("timestamp"),
        "rpc_url": url,
        "chain": {
            "head_height": head_height,
            "head_hash": head_hash[:18] + "..." if head_hash else None,
        },
        "peers": {
            "total": peers_total,
            "connected": peers_connected,
            "handshaking": peers_handshaking,
            "inbound": peers_inbound,
            "outbound": peers_outbound,
            "by_state": peer_states,
        },
        "peer_tips": peer_tips,
        "sync": {
            "phase": sync_phase,
            "head_height": head_height,
            "target_height": target_height,
            "behind_by": (target_height - head_height) if target_height and target_height > head_height else 0,
        },
        "queues": sync_dump.get("queues", {}),
        "errors": {
            "last_header_error": sync_dump.get("sync", {}).get("last_header_error"),
            "last_block_error": sync_dump.get("sync", {}).get("last_block_error"),
        },
    }
    
    if json_output:
        typer.echo(json.dumps(health_report, indent=2))
        return
    
    # Display formatted health report
    typer.echo("\n🏥 Node Health Diagnostics\n")
    typer.echo("━" * 60)
    typer.echo(f"RPC URL:          {url}")
    typer.echo(f"Timestamp:        {health_report['timestamp']}")
    
    typer.echo("\n📊 Chain Status:")
    typer.echo(f"  Head Height:    {head_height}")
    typer.echo(f"  Head Hash:      {health_report['chain']['head_hash']}")
    
    typer.echo("\n🔗 Peer Connections:")
    typer.echo(f"  Total:          {peers_total}")
    typer.echo(f"  Connected:      {peers_connected} (identity verified)")
    typer.echo(f"  Handshaking:    {peers_handshaking} (in progress)")
    typer.echo(f"  Inbound:        {peers_inbound}")
    typer.echo(f"  Outbound:       {peers_outbound}")
    
    if peer_states:
        typer.echo("\n  By State:")
        for state, count in sorted(peer_states.items()):
            icon = "✅" if state == "CONNECTED" else "🔄" if state == "HANDSHAKING" else "🚫" if state == "FAILED" else "📞"
            typer.echo(f"    {icon} {state:15s} {count}")
    
    typer.echo("\n📡 Peer Tips:")
    typer.echo(f"  Total:          {peer_tips['total']}")
    typer.echo(f"  Fresh (<10m):   {peer_tips['fresh']}")
    typer.echo(f"  Stale (>10m):   {peer_tips['stale']}")
    typer.echo(f"  Missing:        {peer_tips['missing']}")
    
    # Highlight issues
    if peer_tips['fresh'] == 0 and peers_connected > 0:
        typer.secho(
            "  ⚠️  WARNING: No fresh peer tips! Tip polling may not be working.",
            fg=typer.colors.YELLOW,
        )
    
    typer.echo("\n🔄 Sync Status:")
    typer.echo(f"  Phase:          {sync_phase}")
    typer.echo(f"  Target Height:  {target_height or 'Unknown'}")
    typer.echo(f"  Behind By:      {health_report['sync']['behind_by']} blocks")
    
    queues = health_report.get("queues", {})
    typer.echo("\n📦 Sync Queues:")
    typer.echo(f"  Pending Headers: {queues.get('pending_header_batches', 0)}")
    typer.echo(f"  Queued Blocks:   {queues.get('queued_blocks', 0)}")
    typer.echo(f"  Orphan Pool:     {queues.get('orphan_pool_size', 0)}")
    
    errors = health_report.get("errors", {})
    if errors.get("last_header_error") or errors.get("last_block_error"):
        typer.echo("\n❌ Recent Errors:")
        if errors.get("last_header_error"):
            typer.echo(f"  Header Error:   {errors['last_header_error']}")
        if errors.get("last_block_error"):
            typer.echo(f"  Block Error:    {errors['last_block_error']}")
    
    typer.echo("━" * 60)
    
    # Health assessment
    typer.echo()
    issues = []
    
    if peers_connected == 0:
        issues.append("No connected peers (all peers still handshaking or failed)")
    
    if peers_connected > 0 and peer_tips['fresh'] == 0:
        issues.append("No fresh peer tips (tip polling not working or all tips stale)")
    
    if head_height == 0 and peers_connected > 0:
        issues.append("At genesis with connected peers (sync may be stalled)")
    
    if sync_phase == "ERROR":
        issues.append("Sync phase is ERROR (check logs for exceptions)")
    
    if issues:
        typer.secho("⚠️  Health Issues Detected:", fg=typer.colors.YELLOW, bold=True)
        for issue in issues:
            typer.echo(f"   • {issue}")
        typer.echo()
        typer.secho("💡 Recommended Actions:", fg=typer.colors.CYAN)
        typer.echo("   1. Check logs for errors: docker logs animica-node")
        typer.echo("   2. Force sync restart: animica sync force --clear-cache")
        typer.echo("   3. Review peer list: animica peer list")
        typer.echo("   4. Check full diagnostics: animica debug sync-dump")
    else:
        typer.secho("✅ Node appears healthy!", fg=typer.colors.GREEN, bold=True)


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
