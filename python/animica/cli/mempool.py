"""
Mempool management CLI commands.

Commands:
  animica mempool list      List pending transaction hashes
  animica mempool stats     Show mempool statistics (count, size, age)
"""

from __future__ import annotations

import json as json_lib
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
        p2p_status = call_rpc("p2p.getStatus", [], rpc_url=resolved_rpc_url, no_cache=no_cache)
    except Exception:
        p2p_status = None

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
    if isinstance(result, list):
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
                    typer.echo(
                        "  {idx:3d}. {hash} nonce={nonce} received_at={received} origin={origin} status={status} from={sender} fee={fee} size={size}".format(
                            idx=i,
                            hash=tx_hash,
                            nonce=nonce,
                            received=received_at,
                            origin=origin,
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
