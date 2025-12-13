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

from .rpc import call_rpc

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
    result = call_rpc("mempool.getPending", [], rpc_url=rpc_url)
    
    if json:
        typer.echo(json_lib.dumps(result, indent=2))
        return
    
    # Pretty print
    if isinstance(result, list):
        if not result:
            typer.echo("Mempool is empty (no pending transactions)")
        else:
            typer.echo(f"Pending transactions ({len(result)}):")
            for i, tx_hash in enumerate(result, 1):
                typer.echo(f"  {i:3d}. {tx_hash}")
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
