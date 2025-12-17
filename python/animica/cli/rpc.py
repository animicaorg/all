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
from .timeouts import DEFAULT_RPC_TIMEOUT, RPC_TIMEOUT_ENV, describe_timeout, resolve_timeout

app = typer.Typer(help="Raw JSON-RPC calls")


def _resolve_rpc_url(rpc_url: Optional[str]) -> str:
    """Resolve RPC URL from option, env, or config.
    
    Empty strings are treated as unset and fall back to network config defaults.
    """
    if rpc_url and rpc_url.strip():
        return rpc_url.strip()
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


def call_rpc(method: str, params: Any, rpc_url: Optional[str] = None, timeout: Optional[float] = None) -> Any:
    """
    Helper function to make RPC calls from other CLI modules.
    
    Args:
        method: JSON-RPC method name
        params: Method parameters (list or dict)
        rpc_url: Optional RPC URL override
        
    Returns:
        Result from the RPC call
        
    Raises:
        RuntimeError: If the RPC call fails with error details
    """
    url = _resolve_rpc_url(rpc_url)
    resolved_timeout = resolve_timeout("RPC timeout", timeout, env_var=RPC_TIMEOUT_ENV, default=DEFAULT_RPC_TIMEOUT)
    
    try:
        if HAVE_RPC:
            client = RpcClient(url, timeout=resolved_timeout)
            return client.request(method, params)
        else:
            import httpx

            payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
            resp = httpx.post(url, json=payload, timeout=resolved_timeout)
            resp.raise_for_status()
            parsed = resp.json()
            if "error" in parsed:
                error_detail = parsed.get("error")
                raise RuntimeError(
                    f"RPC call to '{method}' failed: {error_detail}"
                )
            return parsed.get("result")
    except Exception as e:
        # Re-raise with more context
        if isinstance(e, RuntimeError):
            raise
        raise RuntimeError(
            f"RPC call to '{method}' at {url} failed: {e}"
        ) from e


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
    timeout: Optional[float] = typer.Option(
        None,
        "--timeout",
        help=f"Request timeout in seconds (default: {describe_timeout(DEFAULT_RPC_TIMEOUT)})",
        envvar=RPC_TIMEOUT_ENV,
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
    try:
        url = _resolve_rpc_url(rpc_url)
        resolved_timeout = resolve_timeout("RPC timeout", timeout, env_var=RPC_TIMEOUT_ENV, default=DEFAULT_RPC_TIMEOUT)

        # Parse params
        params: Any = []
        if params_arg:
            try:
                params = json.loads(params_arg)
            except json.JSONDecodeError as e:
                typer.echo(f"Error parsing params JSON: {e}", err=True)
                raise typer.Exit(1)

        # Use RpcClient when available, otherwise fall back to httpx
        if HAVE_RPC:
            client = RpcClient(url, timeout=resolved_timeout)
            result = client.request(method, params)
        else:
            import httpx

            payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
            resp = httpx.post(url, json=payload, timeout=resolved_timeout)
            resp.raise_for_status()
            parsed = resp.json()
            if "error" in parsed:
                raise RuntimeError(parsed.get("error"))
            result = parsed.get("result")

        typer.echo(json.dumps(result, indent=2, ensure_ascii=False))

    except Exception as e:
        typer.echo(f"RPC error: {e}", err=True)
        raise typer.Exit(1)
