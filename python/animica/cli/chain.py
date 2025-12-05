"""
animica.cli.chain — Blockchain query subcommands.

Implements:
  - animica chain head       Current chain head
  - animica chain block      Query block by height or hash
  - animica chain tx         Query transaction
  - animica chain account    Query account state
  - animica chain events     Query events/logs
"""

from __future__ import annotations

import json
from typing import Optional

import typer

try:
    from omni_sdk.rpc.http import RpcClient

    HAVE_RPC = True
except Exception:
    HAVE_RPC = False

from animica.config import load_network_config

app = typer.Typer(help="Chain queries (head, blocks, transactions, accounts)")


def _resolve_rpc_url(rpc_url: Optional[str]) -> str:
    """Resolve RPC URL from option, env, or config."""
    if rpc_url:
        return rpc_url
    cfg = load_network_config()
    return cfg.rpc_url


def _ensure_rpc_available() -> None:
    if not HAVE_RPC:
        typer.echo(
            "Error: omni_sdk.rpc.http.RpcClient required. "
            "Ensure 'omni_sdk' is installed.",
            err=True,
        )
        raise typer.Exit(1)


def _request_rpc(method: str, params: Optional[list], rpc_url: Optional[str]):
    """Perform an RPC request using RpcClient if available, otherwise httpx."""
    url = _resolve_rpc_url(rpc_url)
    if HAVE_RPC:
        client = RpcClient(url, timeout=10.0)
        return client.request(method, params)
    else:
        import httpx

        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}
        resp = httpx.post(url, json=payload, timeout=10.0)
        resp.raise_for_status()
        parsed = resp.json()
        if "error" in parsed:
            raise RuntimeError(parsed.get("error"))
        return parsed.get("result")


def _pretty(obj: dict) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False)


@app.command()
def head(
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="Override RPC URL",
        envvar="ANIMICA_RPC_URL",
    ),
) -> None:
    """Display the current chain head (height, hash, timestamp)."""
    # RPC availability handled by _request_rpc fallback

    try:
        # Try different method names
        head_data = None
        for method in ("chain_getHead", "chain.getHead", "eth_blockNumber"):
            try:
                head_data = _request_rpc(method, None, rpc_url)
                break
            except Exception:
                continue

        if head_data is None:
            typer.echo("Could not fetch head from node", err=True)
            raise typer.Exit(1)

        # Pretty-print
        typer.echo("Chain Head:")
        typer.echo("-" * 60)
        height = head_data.get("height") or head_data.get("number") or "?"
        hash_val = head_data.get("hash") or head_data.get("blockHash") or "?"
        timestamp = head_data.get("timestamp") or "?"

        typer.echo(f"Height:    {height}")
        typer.echo(f"Hash:      {hash_val}")
        typer.echo(f"Timestamp: {timestamp}")

        # Additional fields if present
        if "parentHash" in head_data:
            typer.echo(f"Parent:    {head_data['parentHash']}")
        if "proposer" in head_data:
            typer.echo(f"Proposer:  {head_data['proposer']}")
        if "stateRoot" in head_data:
            typer.echo(f"State:     {head_data['stateRoot']}")
        if "txsRoot" in head_data:
            typer.echo(f"Txs Root:  {head_data['txsRoot']}")

    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@app.command()
def block(
    height_or_hash: str = typer.Argument(..., help="Block height or hash (0x...)"),
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="Override RPC URL",
        envvar="ANIMICA_RPC_URL",
    ),
) -> None:
    """Display block details (transactions, receipts, state changes)."""
    # RPC availability handled by _request_rpc fallback

    try:
        # Determine if it's a height or hash
        is_hash = height_or_hash.startswith("0x")

        if is_hash:
            method = "chain_getBlockByHash"
            params = [height_or_hash]
        else:
            method = "chain_getBlockByHeight"
            params = [int(height_or_hash)]

        block_data = _request_rpc(method, params, rpc_url)

        if block_data is None:
            typer.echo(f"Block not found: {height_or_hash}", err=True)
            raise typer.Exit(1)

        typer.echo("Block:")
        typer.echo(_pretty(block_data))

    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@app.command()
def tx(
    tx_hash: str = typer.Argument(..., help="Transaction hash (0x...)"),
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="Override RPC URL",
        envvar="ANIMICA_RPC_URL",
    ),
) -> None:
    """Display transaction details and receipt."""
    # RPC availability handled by _request_rpc fallback

    try:
        # Fetch tx and receipt
        tx_data = _request_rpc("chain_getTx", [tx_hash], rpc_url)
        receipt = _request_rpc("chain_getReceipt", [tx_hash], rpc_url)

        if tx_data is None:
            typer.echo(f"Transaction not found: {tx_hash}", err=True)
            raise typer.Exit(1)

        typer.echo("Transaction:")
        typer.echo(_pretty(tx_data))

        if receipt:
            typer.echo("\nReceipt:")
            typer.echo(_pretty(receipt))

    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@app.command()
def account(
    address: str = typer.Argument(..., help="Account address (anim1...)"),
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="Override RPC URL",
        envvar="ANIMICA_RPC_URL",
    ),
) -> None:
    """Display account balance and state."""
    # RPC availability handled by _request_rpc fallback

    try:
        # Try different balance methods
        balance = None
        for method in ("chain_getBalance", "eth_getBalance"):
            try:
                balance = _request_rpc(method, [address], rpc_url)
                break
            except Exception:
                continue

        if balance is None:
            typer.echo("Could not fetch account balance", err=True)
            raise typer.Exit(1)

        typer.echo(f"Address: {address}")
        typer.echo(f"Balance: {balance}")

        # Try to get nonce
        try:
            nonce = _request_rpc("chain_getTransactionCount", [address], rpc_url)
            if nonce is not None:
                typer.echo(f"Nonce:   {nonce}")
        except Exception:
            pass

    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@app.command()
def events(
    from_height: int = typer.Option(0, "--from", help="Start block height"),
    to_height: Optional[int] = typer.Option(
        None, "--to", help="End block height (default: latest)"
    ),
    filter_type: Optional[str] = typer.Option(
        None, "--type", help="Filter by event type"
    ),
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="Override RPC URL",
        envvar="ANIMICA_RPC_URL",
    ),
) -> None:
    """Query chain events/logs in a height range."""
    # RPC availability handled by _request_rpc fallback

    try:
        # Build filter
        filter_params = {
            "fromHeight": from_height,
        }
        if to_height is not None:
            filter_params["toHeight"] = to_height
        if filter_type:
            filter_params["type"] = filter_type

        # Try different method names
        events_data = None
        for method in ("chain_getLogs", "eth_getLogs"):
            try:
                events_data = _request_rpc(method, [filter_params], rpc_url)
                break
            except Exception:
                continue

        if events_data is None:
            typer.echo("No events found or method not supported", err=True)
            raise typer.Exit(1)

        if isinstance(events_data, list):
            typer.echo(f"Found {len(events_data)} events:")
            typer.echo(_pretty(events_data))
        else:
            typer.echo(_pretty(events_data))

    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
