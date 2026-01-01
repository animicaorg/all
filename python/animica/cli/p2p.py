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
