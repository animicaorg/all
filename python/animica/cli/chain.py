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
from typing import Iterable, List, Optional

import typer

from animica.config import load_network_config
from animica.cli.rpc import call_rpc

app = typer.Typer(help="Chain queries (head, blocks, transactions, accounts)")


def _resolve_rpc_url(rpc_url: Optional[str]) -> str:
    """Resolve RPC URL from option, env, or config."""
    if rpc_url and rpc_url.strip():
        return rpc_url.strip()
    cfg = load_network_config()
    return cfg.rpc_url


def _try_rpc(methods: Iterable[str], params: Optional[list], rpc_url: Optional[str]):
    """
    Try a list of RPC method names until one succeeds.

    Falls back only on "method not found" errors, and raises the last
    non-fallback error if everything fails.
    """

    last_error: Exception | None = None
    for method in methods:
        try:
            return call_rpc(method, params or [], rpc_url)
        except Exception as exc:  # noqa: BLE001 - best-effort fallback handling
            msg = str(exc).lower()
            if "method not found" in msg or "-32601" in msg:
                last_error = exc
                continue
            last_error = exc
            break
    if last_error:
        raise last_error
    return None


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
        head_data = _try_rpc(("chain_getHead", "chain.getHead"), None, rpc_url)
        if head_data is None:
            head_data = _try_rpc(
                ("block_getBlockByNumber", "chain_getBlockByHeight", "chain.getBlockByNumber"),
                ["latest", False, False],
                rpc_url,
            )

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
            method_options: List[str] = [
                "block_getBlockByHash",
                "chain_getBlockByHash",
                "chain.getBlockByHash",
            ]
            params = [height_or_hash, False, True]
        else:
            method_options = [
                "block_getBlockByNumber",
                "chain_getBlockByHeight",
                "chain.getBlockByNumber",
            ]
            params = [height_or_hash, False, True]

        block_data = _try_rpc(method_options, params, rpc_url)

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
        tx_data = _try_rpc(
            ["tx_getTransactionByHash", "tx.getTransactionByHash", "chain_getTx"],
            [tx_hash],
            rpc_url,
        )
        receipt = _try_rpc(
            ["tx_getTransactionReceipt", "tx.getTransactionReceipt", "chain_getReceipt"],
            [tx_hash],
            rpc_url,
        )

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
        for method in (
            "state.getBalance",
            "state_getBalance",
            "chain_getBalance",
            "eth_getBalance",
        ):
            try:
                params = [address] if method != "eth_getBalance" else [address, "latest"]
                balance = _try_rpc([method], params, rpc_url)
                break
            except Exception:
                continue

        if balance is None:
            typer.echo("Could not fetch account balance", err=True)
            raise typer.Exit(1)

        typer.echo(f"Address: {address}")
        # Balance may be a hex quantity; normalize for readability
        try:
            numeric_balance = int(str(balance), 0)
            typer.echo(f"Balance: {numeric_balance} ({balance})")
        except Exception:
            typer.echo(f"Balance: {balance}")

        # Try to get nonce
        try:
            nonce = _try_rpc(
                ["state.getNonce", "state_getNonce", "chain_getTransactionCount"],
                [address],
                rpc_url,
            )
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
        # First, try native log endpoints if available
        filter_params = {"fromHeight": from_height}
        if to_height is not None:
            filter_params["toHeight"] = to_height
        if filter_type:
            filter_params["type"] = filter_type

        events_data = None
        try:
            events_data = _try_rpc(("chain_getLogs", "eth_getLogs"), [filter_params], rpc_url)
        except Exception:
            events_data = None

        # Fallback: scan blocks and gather logs from receipts
        if events_data is None:
            latest_height = to_height
            if latest_height is None:
                try:
                    head = _try_rpc(["chain_getHead", "chain.getHead"], None, rpc_url)
                    latest_height = int(head.get("height") or head.get("number") or 0)
                except Exception:
                    latest_height = from_height

            collected: list[dict] = []
            for h in range(from_height, (latest_height or from_height) + 1):
                blk = _try_rpc(
                    ["block_getBlockByNumber", "chain_getBlockByHeight", "chain.getBlockByNumber"],
                    [h, False, True],
                    rpc_url,
                )
                if not blk:
                    continue
                tx_hashes = blk.get("transactions") or blk.get("txs") or []
                receipts = blk.get("receipts") or []
                for idx, rec in enumerate(receipts):
                    tx_hash = tx_hashes[idx] if idx < len(tx_hashes) else None
                    logs = rec.get("logs") if isinstance(rec, dict) else None
                    if not logs:
                        continue
                    for log_index, log in enumerate(logs):
                        if filter_type:
                            event_name = None
                            if isinstance(log, dict):
                                event_name = log.get("type") or log.get("event")
                            if event_name and str(event_name).lower() != filter_type.lower():
                                continue
                        entry = {
                            "blockNumber": blk.get("number"),
                            "blockHash": blk.get("hash"),
                            "txHash": tx_hash,
                            "logIndex": log_index,
                            "event": log,
                        }
                        collected.append(entry)
            events_data = collected

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
