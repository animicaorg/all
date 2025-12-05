"""
animica.cli.rpc — Raw JSON-RPC method calls.

Implements:
  - animica rpc call <method> [params]

Allows direct JSON-RPC calls for debugging and scripting.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Optional

import typer

try:
    from omni_sdk.rpc.http import RpcClient

    HAVE_RPC = True
except Exception:
    HAVE_RPC = False

from animica.config import load_network_config

app = typer.Typer(help="Raw JSON-RPC calls")


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


@app.command()
def call(
    method: str = typer.Argument(..., help="JSON-RPC method name"),
    params_arg: Optional[str] = typer.Argument(
        None, help='JSON params (e.g. \'["param1", 123]\' or \'{"key":"value"}\')'
    ),
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="Override RPC URL",
        envvar="ANIMICA_RPC_URL",
    ),
) -> None:
    """
    Make a raw JSON-RPC 2.0 call to the node.

    Examples:
      animica rpc call chain_getHead
      animica rpc call chain_getBlock '[0]'
      animica rpc call chain_getBlockByHeight '[100]'
      animica rpc call chain_getTx '["0x..."]'

    The params argument can be a JSON array or object. If omitted, an empty
    array is used.
    """
    _ensure_rpc_available()

    try:
        url = _resolve_rpc_url(rpc_url)
        client = RpcClient(url, timeout=10.0)

        # Parse params
        params: Any = None
        if params_arg:
            try:
                params = json.loads(params_arg)
            except json.JSONDecodeError as e:
                typer.echo(f"Error parsing params JSON: {e}", err=True)
                raise typer.Exit(1)

        # Make request
        result = client.request(method, params)

        # Output
        typer.echo(json.dumps(result, indent=2, ensure_ascii=False))

    except Exception as e:
        typer.echo(f"RPC error: {e}", err=True)
        raise typer.Exit(1)
