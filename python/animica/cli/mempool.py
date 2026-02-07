"""
Mempool management CLI commands.

Commands:
  animica mempool list      List pending transaction hashes
  animica mempool stats     Show mempool statistics (count, size, age)
  animica mempool drop      Drop a transaction by hash
  animica mempool sync-status  Show P2P mempool sync status
"""

from __future__ import annotations

import json as json_lib
import time
from typing import Optional

import typer

from .rpc import _resolve_rpc_url, call_rpc


def _short_id(value: Optional[str], length: int = 10) -> Optional[str]:
    if not value:
        return None
    text = value
    if text.startswith("0x"):
        text = text[2:]
    if len(text) <= length:
        return "0x" + text
    return "0x" + text[:length]

app = typer.Typer(
    name="mempool",
    help="Mempool inspection and management",
    no_args_is_help=True,
)


@app.command("sync-status")
def sync_status(
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="RPC endpoint URL",
        envvar="ANIMICA_RPC_URL",
    ),
    no_cache: bool = typer.Option(
        True,
        "--no-cache/--cache",
        help="Disable HTTP caching for mempool reads (default: no-cache).",
    ),
    json: bool = typer.Option(
        False,
        "--json",
        help="Output raw JSON",
    ),
) -> None:
    """
    Show mempool sync status and per-peer known transaction counts.

    Examples:
        animica mempool sync-status
        animica mempool sync-status --json
    """
    resolved_rpc_url = _resolve_rpc_url(rpc_url)
    result = call_rpc(
        "p2p.mempoolSyncStatus",
        [],
        rpc_url=resolved_rpc_url,
        no_cache=no_cache,
    )
    if json:
        typer.echo(json_lib.dumps(result, indent=2))
        return

    mempool = result.get("mempool") if isinstance(result, dict) else {}
    typer.echo(f"RPC_TARGET={resolved_rpc_url}")
    typer.echo(
        "Mempool: count={count} bytes={bytes} age_s={age}".format(
            count=mempool.get("count", "n/a"),
            bytes=mempool.get("totalBytes", "n/a"),
            age=mempool.get("age_s", "n/a"),
        )
    )
    typer.echo(
        "Sync: announced={announced} received={received} requested={requested} accepted={accepted} rejected={rejected} dropped={dropped} inflight={inflight} inv_queue={inv_queue}".format(
            announced=result.get("announced_count", 0),
            received=result.get("received_count", 0),
            requested=result.get("requested_count", 0),
            accepted=result.get("accepted_count", 0),
            rejected=result.get("rejected_count", 0),
            dropped=result.get("dropped_count", 0),
            inflight=result.get("inflight", 0),
            inv_queue=result.get("inv_queue_depth", 0),
        )
    )
    peers = result.get("peers") if isinstance(result, dict) else []
    if peers:
        typer.echo("Peers:")
        for entry in peers:
            if not isinstance(entry, dict):
                continue
            peer_id = entry.get("peer_node_id") or entry.get("conn_id") or "n/a"
            typer.echo(
                "  peer={peer} known_txids={known} inv_queue={inv_queue} last_sync_sent={sent} last_sync_recv={recv}".format(
                    peer=_short_id(peer_id) or "n/a",
                    known=entry.get("known_txids", 0),
                    inv_queue=entry.get("inv_queue", 0),
                    sent=entry.get("last_sync_sent_at"),
                    recv=entry.get("last_sync_recv_at"),
                )
            )


@app.command("list")
def list_pending(
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="RPC endpoint URL",
        envvar="ANIMICA_RPC_URL",
    ),
    no_cache: bool = typer.Option(
        True,
        "--no-cache/--cache",
        help="Disable HTTP caching for mempool reads (default: no-cache).",
    ),
    json: bool = typer.Option(
        False,
        "--json",
        help="Output raw JSON",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        help="Show origin and received timestamps for each tx.",
    ),
) -> None:
    """
    List pending transaction hashes in the mempool.
    
    Shows all transactions waiting to be included in the next block.
    
    Examples:
        animica mempool list
        animica mempool list --json
        animica mempool list --rpc-url http://127.0.0.1:18546/rpc
    """
    # Call RPC method
    resolved_rpc_url = _resolve_rpc_url(rpc_url)
    result = call_rpc(
        "mempool.getPending",
        [True],
        rpc_url=resolved_rpc_url,
        no_cache=no_cache,
    )
    chain_identity = None
    head = None
    p2p_status = None
    p2p_debug = None
    try:
        chain_identity = call_rpc(
            "chain.getChainIdentity",
            [],
            rpc_url=resolved_rpc_url,
            no_cache=no_cache,
        )
    except Exception:
        chain_identity = None
    try:
        head = call_rpc("chain.getHead", [], rpc_url=resolved_rpc_url, no_cache=no_cache)
    except Exception:
        head = None
    try:
        p2p_status = call_rpc(
            "p2p.getStatus", [], rpc_url=resolved_rpc_url, no_cache=no_cache
        )
    except Exception:
        p2p_status = None
    try:
        p2p_debug = call_rpc(
            "p2p.debugStatus", [], rpc_url=resolved_rpc_url, no_cache=no_cache
        )
    except Exception:
        p2p_debug = None
    mempool_info = None
    try:
        mempool_info = call_rpc("mempool.getInfo", [], rpc_url=resolved_rpc_url, no_cache=no_cache)
    except Exception:
        mempool_info = None

    chain_id = None
    genesis_hash = None
    if isinstance(chain_identity, dict):
        chain_id = chain_identity.get("chainId") or chain_identity.get("chain_id")
        genesis_hash = chain_identity.get("genesisHash") or chain_identity.get("genesis_hash")
    if chain_id is None and isinstance(head, dict):
        chain_id = head.get("chainId") or head.get("chain_id")
    head_height = head.get("height") if isinstance(head, dict) else None
    peer_id = None
    if isinstance(p2p_status, dict):
        peer_id = p2p_status.get("peer_id") or p2p_status.get("peerId") or p2p_status.get("id")

    rpc_source = "explicit" if rpc_url else "default"
    if json:
        payload = {
            "rpcTarget": resolved_rpc_url,
            "rpcSource": rpc_source,
            "nodeId": peer_id,
            "chain": {
                "chainId": chain_id,
                "genesisHash": genesis_hash,
            },
            "peer": {"id": peer_id},
            "mempool": mempool_info,
            "p2p": {
                "status": p2p_status,
                "debug": p2p_debug,
            },
            "head": {
                "height": head_height,
                "hash": head.get("hash") if isinstance(head, dict) else None,
            },
            "pending": result,
        }
        typer.echo(json_lib.dumps(payload, indent=2))
        return
    
    # Pretty print
    typer.echo(
        f"RPC_TARGET={resolved_rpc_url} NODE_ID={_short_id(peer_id) or 'n/a'} SOURCE={rpc_source}"
    )
    typer.echo(
        "Chain: id={chain_id} genesis={genesis}".format(
            chain_id=chain_id if chain_id is not None else "n/a",
            genesis=_short_id(genesis_hash) or "n/a",
        )
    )
    typer.echo(
        "Peer: {peer_id}  Head: {height}".format(
            peer_id=_short_id(peer_id) or "n/a",
            height=head_height if head_height is not None else "n/a",
        )
    )
    if isinstance(mempool_info, dict):
        typer.echo(
            "Mempool: id={mempool_id} path={mempool_path}".format(
                mempool_id=mempool_info.get("mempool_id") or "n/a",
                mempool_path=mempool_info.get("mempool_path") or "n/a",
            )
        )
    peer_known = None
    total_peer_known_txids = 0  # Initialize early for use later
    if isinstance(p2p_debug, dict):
        peer_known = p2p_debug.get("peers")
    
    if peer_known:
        # Count total known txids from peers
        typer.echo("Peer-known txids (sample):")
        for entry in peer_known:
            if not isinstance(entry, dict):
                continue
            peer = entry.get("peer_id") or entry.get("peerId") or "n/a"
            conn_id = entry.get("conn_id") or entry.get("txrelay_conn_id") or "n/a"
            known = entry.get("txrelay_known_txids")
            sample = entry.get("txrelay_known_txids_sample") or []
            sample_text = ", ".join(sample) if sample else "n/a"
            typer.echo(
                "  peer={peer} conn_id={conn_id} known_txids={known} sample=[{sample}]".format(
                    peer=_short_id(peer) or "n/a",
                    conn_id=_short_id(conn_id) or "n/a",
                    known=known,
                    sample=sample_text,
                )
            )
            if isinstance(known, int):
                total_peer_known_txids += known
    
    if isinstance(result, list):
        # Proactively import peer-known transactions whenever peers advertise
        # more txids than the local pending set currently contains.
        if total_peer_known_txids > len(result):
            try:
                import_result = call_rpc(
                    "p2p.importPeerKnownTxs",
                    [128],
                    rpc_url=resolved_rpc_url,
                    no_cache=True,
                )
                requested = import_result.get("requested", 0) if isinstance(import_result, dict) else 0
                if requested > 0:
                    # Poll for transactions to arrive with increasing delays
                    # TX_GET -> network roundtrip -> TX_DATA -> validation -> mempool admission
                    # Start with short delays to be responsive, increase if needed
                    delays = [0.05, 0.1, 0.15, 0.2]  # Total: 0.5 seconds max
                    for delay in delays:
                        time.sleep(delay)

                        refreshed = call_rpc(
                            "mempool.getPending",
                            [True],
                            rpc_url=resolved_rpc_url,
                            no_cache=True,
                        )
                        if isinstance(refreshed, list) and len(refreshed) > len(result):
                            # Transactions arrived!
                            added = max(0, len(refreshed) - len(result))
                            result = refreshed
                            typer.echo(
                                f"Auto-imported peer transactions: requested={requested}, newly_visible={added}"
                            )
                            break
                    else:
                        # Timeout: no new transactions after polling
                        typer.echo(
                            f"Auto-imported peer transactions: requested={requested}, newly_visible=0 (timed out after {sum(delays)}s)"
                        )
            except Exception as e:
                typer.echo(
                    f"⚠ Could not auto-import peer transactions: {e}\n"
                    "  You can manually trigger this with: animica rpc call p2p.importPeerKnownTxs"
                )

        if not result:
            typer.echo("Mempool is empty (no pending transactions)")
        else:
            typer.echo(f"Pending transactions ({len(result)}):")
            for i, entry in enumerate(result, 1):
                if isinstance(entry, dict):
                    tx_hash = entry.get("hash")
                    sender = entry.get("from") or "n/a"
                    nonce = entry.get("nonce")
                    fee = entry.get("fee")
                    size = entry.get("size")
                    status = entry.get("status", "unknown")
                    received_at = entry.get("received_at")
                    origin = entry.get("origin_peer") or entry.get("origin") or "unknown"
                    peer = entry.get("peer") or entry.get("origin_peer")
                    if verbose:
                        extra = f" received_at={received_at} origin={origin} peer={peer}"
                    else:
                        extra = ""
                    typer.echo(
                        "  {idx:3d}. {hash} nonce={nonce}{extra} status={status} from={sender} fee={fee} size={size}".format(
                            idx=i,
                            hash=tx_hash,
                            nonce=nonce,
                            extra=extra,
                            status=status,
                            sender=sender,
                            fee=fee,
                            size=size,
                        )
                    )
                else:
                    typer.echo(f"  {i:3d}. {entry}")
    else:
        typer.echo(json_lib.dumps(result, indent=2))


@app.command("drop")
def drop_tx(
    tx_hash: str = typer.Argument(..., help="Transaction hash to drop (0x...)"),
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
    Drop a pending transaction from the mempool.
    """
    result = call_rpc("mempool.dropTransaction", [tx_hash], rpc_url=rpc_url)
    if json:
        typer.echo(json_lib.dumps(result, indent=2))
        return
    if isinstance(result, dict):
        dropped = result.get("dropped")
        reason = result.get("reason")
        if dropped:
            typer.echo(f"Dropped {result.get('hash') or tx_hash}")
        else:
            typer.echo(f"Failed to drop {tx_hash}: {reason or 'not_found'}")
        return
    typer.echo(str(result))


@app.command("stats")
def show_stats(
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
    Show mempool statistics (count, total size, oldest transaction age).
    
    Provides a summary of the current mempool state.
    
    Examples:
        animica mempool stats
        animica mempool stats --json
    """
    # Call RPC method
    result = call_rpc("mempool.getStats", [], rpc_url=rpc_url)
    
    if json:
        typer.echo(json_lib.dumps(result, indent=2))
        return
    
    # Pretty print
    if isinstance(result, dict):
        count = result.get("count", 0)
        total_bytes = result.get("totalBytes", 0)
        oldest_age = result.get("oldestAgeSec")
        
        typer.echo("Mempool Statistics:")
        typer.echo(f"  Transaction count: {count}")
        typer.echo(f"  Total size:        {total_bytes:,} bytes ({total_bytes / 1024:.2f} KB)")
        
        if oldest_age is not None:
            if oldest_age < 60:
                age_str = f"{oldest_age:.1f} seconds"
            elif oldest_age < 3600:
                age_str = f"{oldest_age / 60:.1f} minutes"
            else:
                age_str = f"{oldest_age / 3600:.1f} hours"
            typer.echo(f"  Oldest transaction: {age_str} ago")
        else:
            typer.echo(f"  Oldest transaction: N/A")
    else:
        typer.echo(json_lib.dumps(result, indent=2))


if __name__ == "__main__":
    app()
