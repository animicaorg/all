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
    typer.echo(f"Last progress:    {dump['last_progress_at']}")
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


@app.command("sync-bench")
def sync_bench(
    duration: int = typer.Option(60, "--duration", help="Benchmark duration in seconds"),
    from_peers: int = typer.Option(0, "--from-peers", help="Minimum peer count required"),
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc", envvar=RPC_ENV, help="RPC endpoint URL"
    ),
    interval: float = typer.Option(2.0, "--interval", help="Sampling interval (seconds)"),
    json_output: bool = typer.Option(
        False, "--json", help="Output JSON instead of formatted text"
    ),
    timeout: Optional[float] = typer.Option(
        None, "--timeout", help="RPC timeout in seconds"
    ),
) -> None:
    """
    Run a sync throughput benchmark by sampling sync metrics over time.
    """
    url = _resolve_rpc_url(rpc_url)

    async def _run() -> dict[str, Any]:
        samples: list[dict[str, float]] = []
        start = time.time()
        while time.time() - start < duration:
            snapshot = await rpc_call(
                "p2p.debugStatus", {}, rpc_url=url, timeout=timeout
            )
            sync_metrics = (
                snapshot.get("sync_metrics", {}) if isinstance(snapshot, dict) else {}
            )
            peers = snapshot.get("peers", []) if isinstance(snapshot, dict) else []
            if from_peers and len(peers) < from_peers:
                typer.secho(
                    f"⚠ Only {len(peers)} peer(s) connected (requested {from_peers})",
                    fg=typer.colors.YELLOW,
                )
            samples.append(
                {
                    "committed_bps": float(sync_metrics.get("blocks_committed_per_s") or 0.0),
                    "verify_ms": float(
                        (sync_metrics.get("verify_ms_per_block") or {}).get("avg", 0.0)
                    ),
                    "db_commit_ms": float(
                        (sync_metrics.get("db_commit_ms") or {}).get("avg", 0.0)
                    ),
                    "net_mb_s": float(sync_metrics.get("net_mb_per_s") or 0.0),
                }
            )
            await asyncio.sleep(max(0.1, interval))

        def _summarize(key: str) -> dict[str, float]:
            vals = [s[key] for s in samples if key in s]
            if not vals:
                return {"avg": 0.0, "p95": 0.0}
            vals.sort()
            avg = sum(vals) / len(vals)
            idx = int(round(0.95 * (len(vals) - 1)))
            return {"avg": avg, "p95": vals[max(0, min(idx, len(vals) - 1))]}

        return {
            "duration_s": duration,
            "sample_count": len(samples),
            "blocks_committed_per_s": _summarize("committed_bps"),
            "verify_ms_per_block": _summarize("verify_ms"),
            "db_commit_ms": _summarize("db_commit_ms"),
            "net_mb_per_s": _summarize("net_mb_s"),
        }

    try:
        results = asyncio.run(_run())
    except Exception as exc:
        typer.secho(f"❌ Sync bench failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    if json_output:
        typer.echo(json.dumps(results, indent=2))
        return

    typer.echo("\n⚡ Sync Benchmark\n")
    typer.echo("━" * 60)
    typer.echo(f"RPC URL:          {url}")
    typer.echo(f"Duration:         {results['duration_s']}s ({results['sample_count']} samples)")
    typer.echo(
        "Blocks/sec:       "
        f"avg={results['blocks_committed_per_s']['avg']:.2f} "
        f"p95={results['blocks_committed_per_s']['p95']:.2f}"
    )
    typer.echo(
        "Verify ms/block:  "
        f"avg={results['verify_ms_per_block']['avg']:.2f} "
        f"p95={results['verify_ms_per_block']['p95']:.2f}"
    )
    typer.echo(
        "DB commit ms:     "
        f"avg={results['db_commit_ms']['avg']:.2f} "
        f"p95={results['db_commit_ms']['p95']:.2f}"
    )
    typer.echo(
        "Net MB/s:         "
        f"avg={results['net_mb_per_s']['avg']:.2f} "
        f"p95={results['net_mb_per_s']['p95']:.2f}"
    )
    typer.echo("━" * 60)


@app.command("tx-relay")
def tx_relay(
    txid: str = typer.Argument(..., help="Transaction hash (hex, with or without 0x)"),
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
    Dump tx relay state machine info for a specific txid.
    """
    url = _resolve_rpc_url(rpc_url)
    try:
        relay = asyncio.run(
            rpc_call("debug.txRelay", [txid], rpc_url=url, timeout=timeout)
        )
    except Exception as exc:
        typer.secho(f"❌ Failed to query tx relay state: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    if json_output:
        typer.echo(json.dumps(relay, indent=2))
        return

    typer.echo("\n🧪 TX Relay Debug\n")
    typer.echo("━" * 60)
    typer.echo(f"RPC URL:          {url}")
    typer.echo(f"TXID:             {relay.get('tx_hash')}")
    typer.echo(f"Has bytes:        {relay.get('has_tx_bytes')}")
    typer.echo(f"In mempool:       {relay.get('in_mempool')}")
    typer.echo(f"In chain:         {relay.get('in_chain')}")
    tx_state = relay.get("tx_state") or {}
    if isinstance(tx_state, dict) and tx_state:
        typer.echo("Global relay state:")
        typer.echo(f"  state:          {tx_state.get('state')}")
        typer.echo(f"  source:         {tx_state.get('source')}")
        typer.echo(f"  validation:     {tx_state.get('validation_status')}")
        if tx_state.get("validation_reason"):
            typer.echo(f"  val reason:     {tx_state.get('validation_reason')}")
        typer.echo(f"  mempool:        {tx_state.get('mempool_status')}")
        if tx_state.get("mempool_reason"):
            typer.echo(f"  mem reason:     {tx_state.get('mempool_reason')}")
        typer.echo(f"  last peer:      {tx_state.get('last_peer')}")
    peer_states = relay.get("peer_states") or []
    if peer_states:
        typer.echo("\nPer-peer relay states:")
        for entry in peer_states:
            typer.echo(
                f"  - {entry.get('peer')}: {entry.get('state')}"
                + (f" reason={entry.get('reason')}" if entry.get("reason") else "")
            )
    typer.echo("━" * 60)


@app.command("p2p-health")
def p2p_health(
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
    Diagnose P2P connectivity health and dial pipeline.
    """
    url = _resolve_rpc_url(rpc_url)
    try:
        status = asyncio.run(
            rpc_call("p2p.getStatus", {}, rpc_url=url, timeout=timeout)
        )
    except Exception as exc:
        typer.secho(f"❌ Failed to query P2P status: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    if json_output:
        typer.echo(json.dumps(status, indent=2))
        return

    seed_list = status.get("seed_list", [])
    seed_sources = status.get("seed_sources", {})
    peerstore_size = status.get("persisted_peer_count")
    addrman_size = status.get("addrman_size")
    dial_history = status.get("dial_attempt_history", [])[-20:]
    caps_config = status.get("caps_config", {})
    listen_addrs = status.get("listen_addrs", [])
    bound_addrs = status.get("bound_listen_addrs", [])
    outbound_enabled = status.get("outbound_dialing_enabled")
    outbound_target = status.get("outbound_target")

    typer.echo("\n🧭 P2P Health\n")
    typer.echo("━" * 60)
    typer.echo(f"RPC URL:              {url}")
    typer.echo(f"Outbound dialing:     {outbound_enabled} (target={outbound_target})")
    typer.echo(f"Seeds configured:     {len(seed_list)}")
    if seed_list:
        typer.echo("  " + ", ".join(seed_list))
    if seed_sources:
        typer.echo(f"Seed sources:         {json.dumps(seed_sources)}")
    typer.echo(f"Peerstore size:       {peerstore_size}")
    typer.echo(f"Addrman size:         {addrman_size}")
    typer.echo(f"Listen addrs:         {listen_addrs}")
    typer.echo(f"Bound sockets:        {bound_addrs}")
    typer.echo("Caps config:")
    typer.echo(
        f"  tx_relay_v2_enabled={caps_config.get('tx_relay_v2_enabled')} "
        f"required_caps={caps_config.get('required_caps')}"
    )

    typer.echo("\nLast 20 dial attempts:")
    if not dial_history:
        typer.echo("  (none)")
    else:
        for entry in dial_history:
            if not isinstance(entry, dict):
                continue
            at = entry.get("at")
            addr = entry.get("addr")
            stage = entry.get("stage")
            success = entry.get("success")
            reason = entry.get("reason")
            typer.echo(
                f"  - addr={addr} stage={stage} success={success} reason={reason} at={at}"
            )
    typer.echo("━" * 60)
