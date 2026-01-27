"""
P2P debug CLI for Animica.

Commands:
  animica p2p tx-debug
"""

from __future__ import annotations

import json as json_lib
from typing import Optional

import typer

from .rpc import _resolve_rpc_url, call_rpc

app = typer.Typer(name="p2p", help="P2P debugging utilities", no_args_is_help=True)


@app.command("tx-debug")
def tx_debug(
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="RPC endpoint URL",
        envvar="ANIMICA_RPC_URL",
    ),
    json: bool = typer.Option(
        False,
        "--json",
        help="Output raw JSON",
    ),
) -> None:
    """
    Show P2P tx relay debug status.
    """
    resolved_rpc_url = _resolve_rpc_url(rpc_url)
    result = call_rpc("p2p.debugStatus", [], rpc_url=resolved_rpc_url)
    if json:
        typer.echo(json_lib.dumps(result, indent=2))
        return

    tx_relay_v2 = result.get("tx_relay_v2", {}) if isinstance(result, dict) else {}
    peers = result.get("peers", []) if isinstance(result, dict) else []
    typer.echo(f"RPC_TARGET={resolved_rpc_url}")
    typer.echo(
        "TxRelay: enabled={enabled} inflight={inflight} sync_interval={sync_interval}s sync_limit={sync_limit}".format(
            enabled=tx_relay_v2.get("enabled"),
            inflight=tx_relay_v2.get("inflight"),
            sync_interval=tx_relay_v2.get("mempool_sync_interval_s"),
            sync_limit=tx_relay_v2.get("mempool_sync_limit"),
        )
    )

    if not peers:
        typer.echo("No peers connected.")
        return

    typer.echo("Peers:")
    for entry in peers:
        if not isinstance(entry, dict):
            continue
        remote = entry.get("remote")
        peer_id = entry.get("peer_id") or entry.get("peerId")
        direction = entry.get("direction")
        known = entry.get("txrelay_known_txids")
        known_sample = entry.get("txrelay_known_txids_sample") or []
        inv_queue = entry.get("txrelay_inv_queue")
        last_sync_sent = entry.get("txrelay_last_sync_sent_at")
        last_sync_recv = entry.get("txrelay_last_sync_recv_at")
        known_sample_text = ""
        if known_sample:
            known_sample_text = " sample=[{sample}]".format(
                sample=", ".join(known_sample)
            )
        typer.echo(
            "  peer={peer} remote={remote} direction={direction} known_txids={known}{sample} "
            "inv_queue={inv_queue} last_sync_sent={sent} last_sync_recv={recv}".format(
                peer=peer_id or "n/a",
                remote=remote or "n/a",
                direction=direction or "n/a",
                known=known,
                sample=known_sample_text,
                inv_queue=inv_queue,
                sent=last_sync_sent,
                recv=last_sync_recv,
            )
        )


@app.command("verifier-seeds")
def verifier_seeds(
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="RPC endpoint URL",
        envvar="ANIMICA_RPC_URL",
    ),
    json: bool = typer.Option(
        False,
        "--json",
        help="Output raw JSON",
    ),
) -> None:
    """
    Show verifier seed status and mining eligibility.
    
    Verifier seeds are trusted nodes that act as authoritative sources for
    the network's highest block height. Mining is only allowed when the local
    node is at the verifier height or at most 1 block ahead (for active miners).
    
    Examples:
        # Show verifier seed status
        animica p2p verifier-seeds
        
        # Show as JSON
        animica p2p verifier-seeds --json
    """
    resolved_rpc_url = _resolve_rpc_url(rpc_url)
    result = call_rpc("p2p.getVerifierSeeds", [], rpc_url=resolved_rpc_url)
    
    if json:
        typer.echo(json_lib.dumps(result, indent=2))
        return
    
    if not isinstance(result, dict):
        typer.secho("Error: Invalid response from RPC", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    
    # Check for errors
    if "error" in result:
        typer.secho(f"Error: {result['error']}", fg=typer.colors.YELLOW)
    
    # Display basic status
    enabled = result.get("enabled", False)
    can_mine = result.get("can_mine", False)
    local_height = result.get("local_height", 0)
    max_verifier_height = result.get("max_verifier_height")
    max_allowed_height = result.get("max_allowed_height")
    
    typer.echo(f"RPC endpoint: {resolved_rpc_url}")
    typer.echo(f"Verifier seeds: {'ENABLED' if enabled else 'DISABLED'}")
    typer.echo(f"Local height: {local_height}")
    
    if max_verifier_height is not None:
        typer.echo(f"Max verifier height: {max_verifier_height}")
        typer.echo(f"Max allowed height: {max_allowed_height}")
    else:
        typer.echo("Max verifier height: N/A (no verifiers connected)")
    
    # Display mining eligibility
    if can_mine:
        typer.secho("Mining eligibility: ✓ ALLOWED", fg=typer.colors.GREEN)
    else:
        typer.secho("Mining eligibility: ✗ BLOCKED", fg=typer.colors.RED)
        if max_verifier_height is not None and local_height > max_allowed_height:
            typer.echo(
                f"  Reason: Local height ({local_height}) exceeds "
                f"max allowed ({max_allowed_height})"
            )
    
    # Display configured IPs
    configured_ips = result.get("configured_ips", [])
    if configured_ips:
        typer.echo(f"\nConfigured verifier IPs:")
        for ip in configured_ips:
            typer.echo(f"  • {ip}")
    
    # Display connected verifiers
    connected_verifiers = result.get("connected_verifiers", [])
    if connected_verifiers:
        typer.echo(f"\nConnected verifiers: {len(connected_verifiers)}")
        for v in connected_verifiers:
            remote = v.get("remote", "unknown")
            height = v.get("height", 0)
            head_hash = v.get("head_hash", "N/A")
            typer.echo(f"  • {remote}")
            typer.echo(f"    Height: {height}")
            typer.echo(f"    Head: {head_hash}")
    else:
        typer.echo("\nConnected verifiers: None")
        if enabled:
            typer.secho(
                "  Warning: No verifier seeds connected. Mining allowed but may be risky.",
                fg=typer.colors.YELLOW,
            )

