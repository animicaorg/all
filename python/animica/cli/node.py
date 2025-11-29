"""Node inspection CLI for Animica developers."""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Dict, Optional

import httpx
import typer

from animica.config import load_network_config

DEFAULT_RPC_URL = load_network_config().rpc_url
RPC_ENV = "ANIMICA_RPC_URL"

app = typer.Typer(help="Query Animica node JSON-RPC endpoints.")


async def rpc_call(method: str, params: Optional[list[Any]] = None, *, rpc_url: str) -> Any:
    payload: Dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(rpc_url, json=payload)
        data = response.json()
    if "error" in data:
        raise RuntimeError(data["error"])
    return data.get("result")


def _resolve_rpc_url(rpc_url: Optional[str]) -> str:
    return rpc_url or os.environ.get(RPC_ENV) or load_network_config().rpc_url


def _pretty(obj: Any) -> str:
    return json.dumps(obj, indent=2)


@app.command()
async def status(rpc_url: Optional[str] = typer.Option(None, "--rpc-url", help="JSON-RPC endpoint", envvar=RPC_ENV)) -> None:
    """Show chain head, block info and sync state."""
    url = _resolve_rpc_url(rpc_url)
    head = await rpc_call("chain.getHead", [], rpc_url=url)
    height = head.get("height") or head.get("number") or 0
    chain_id = head.get("chainId") or head.get("chain_id")
    head_hash = head.get("hash") or head.get("blockHash")

    block = None
    if height is not None:
        try:
            block = await rpc_call("chain.getBlockByHeight", [height], rpc_url=url)
        except Exception:  # noqa: BLE001
            block = None

    sync_status = None
    for method in ("node.syncStatus", "chain.syncing", "sync.isSyncing"):
        try:
            sync_status = await rpc_call(method, [], rpc_url=url)
            break
        except Exception:  # noqa: BLE001
            continue

    typer.echo(f"RPC URL: {url}")
    typer.echo(f"Chain ID: {chain_id}")
    typer.echo(f"Head height: {height}")
    typer.echo(f"Head hash: {head_hash}")
    typer.echo(f"Sync status: {sync_status}")
    if block is not None:
        typer.echo("Head block:")
        typer.echo(_pretty(block))


@app.command()
async def head(rpc_url: Optional[str] = typer.Option(None, "--rpc-url", help="JSON-RPC endpoint", envvar=RPC_ENV)) -> None:
    """Print the current chain head summary."""
    url = _resolve_rpc_url(rpc_url)
    head_info = await rpc_call("chain.getHead", [], rpc_url=url)
    typer.echo(_pretty(head_info))


@app.command()
async def block(
    height: Optional[int] = typer.Option(None, "--height", help="Block height"),
    hash: Optional[str] = typer.Option(None, "--hash", help="Block hash"),
    rpc_url: Optional[str] = typer.Option(None, "--rpc-url", help="JSON-RPC endpoint", envvar=RPC_ENV),
) -> None:
    """Fetch and display a block by height or hash."""
    if not height and not hash:
        raise typer.BadParameter("Provide --height or --hash")
    url = _resolve_rpc_url(rpc_url)
    if height is not None:
        result = await rpc_call("chain.getBlockByHeight", [height], rpc_url=url)
        if isinstance(result, dict) and "transactions" not in result and result.get("hash"):
            result = await rpc_call("chain.getBlockByHash", [result["hash"]], rpc_url=url)
    else:
        result = await rpc_call("chain.getBlockByHash", [hash], rpc_url=url)
    typer.echo(_pretty(result))


@app.command()
async def tx(
    hash: str = typer.Option(..., "--hash", help="Transaction hash"),
    rpc_url: Optional[str] = typer.Option(None, "--rpc-url", help="JSON-RPC endpoint", envvar=RPC_ENV),
) -> None:
    """Fetch and display a transaction by hash."""
    url = _resolve_rpc_url(rpc_url)
    result = await rpc_call("chain.getTransactionByHash", [hash], rpc_url=url)
    typer.echo(_pretty(result))


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(app())
