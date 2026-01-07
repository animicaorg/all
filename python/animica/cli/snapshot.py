"""
CLI commands for chain snapshot management.

Provides commands to create, list, verify, import, and manage snapshots
for fast chain synchronization.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

import httpx
import typer
from animica.config import load_network_config
from animica.cli.rpc_utils import candidate_rpc_urls
from .timeouts import DEFAULT_RPC_TIMEOUT, RPC_TIMEOUT_ENV, resolve_timeout

app = typer.Typer(help="Manage chain snapshots for fast sync.")

DEFAULT_RPC_URL = load_network_config().rpc_url
RPC_ENV = "ANIMICA_RPC_URL"


async def rpc_call(
    method: str,
    params: Optional[list[Any] | dict[str, Any]] = None,
    *,
    rpc_url: str,
    timeout: Optional[float] = None,
) -> Any:
    """Make a JSON-RPC call."""
    resolved_timeout = resolve_timeout(
        "RPC timeout", timeout, env_var=RPC_TIMEOUT_ENV, default=DEFAULT_RPC_TIMEOUT
    )
    payload: dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params or [],
    }
    async with httpx.AsyncClient(timeout=resolved_timeout) as client:
        response = await client.post(rpc_url, json=payload)
        data = response.json()
    if "error" in data:
        error_info = data["error"]
        if isinstance(error_info, dict):
            error_msg = error_info.get("message", str(error_info))
        else:
            error_msg = str(error_info)
        raise RuntimeError(error_msg)
    return data.get("result")


def _resolve_rpc_url(rpc_url: Optional[str]) -> str:
    """Resolve RPC URL from argument, environment, or config."""
    if rpc_url and rpc_url.strip():
        return rpc_url.strip()
    env_url = os.environ.get(RPC_ENV)
    if env_url and env_url.strip():
        return env_url.strip()
    return DEFAULT_RPC_URL


@app.command("create")
def create(
    height: Optional[int] = typer.Option(
        None, "--height", "-h", help="Checkpoint height (default: current head)"
    ),
    compress: bool = typer.Option(
        True, "--compress/--no-compress", help="Compress snapshot chunks"
    ),
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc", envvar=RPC_ENV, help="RPC endpoint URL"
    ),
    timeout: Optional[float] = typer.Option(
        None, "--timeout", help="RPC timeout in seconds"
    ),
):
    """
    Create a chain snapshot at the specified checkpoint height.
    
    If height is not specified, creates a snapshot at the current chain head.
    """
    url = _resolve_rpc_url(rpc_url)
    
    try:
        typer.echo(f"Creating snapshot at height {height or 'current head'}...")
        
        params = {"compress": compress}
        if height is not None:
            params["height"] = height
        
        result = asyncio.run(
            rpc_call("snapshot.create", params, rpc_url=url, timeout=timeout)
        )
        
        if not result.get("success"):
            typer.echo(f"❌ Error: {result.get('error', 'Unknown error')}", err=True)
            raise typer.Exit(code=1)
        
        typer.echo("✅ Snapshot created successfully!")
        typer.echo(f"  Chain ID: {result['chain_id']}")
        typer.echo(f"  Height: {result['checkpoint_height']}")
        typer.echo(f"  Hash: {result['checkpoint_hash']}")
        typer.echo(f"  Blocks: {result['blocks_count']}")
        typer.echo(f"  Accounts: {result['accounts_count']}")
        typer.echo(f"  Storage keys: {result['storage_keys_count']}")
        typer.echo(f"  Elapsed: {result['elapsed_seconds']}s")
        typer.echo(f"  Path: {result['path']}")
        
    except Exception as e:
        typer.echo(f"❌ Error creating snapshot: {e}", err=True)
        raise typer.Exit(code=1)


@app.command("list")
def list_snapshots(
    chain_id: Optional[int] = typer.Option(
        None, "--chain-id", "-c", help="Filter by chain ID"
    ),
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc", envvar=RPC_ENV, help="RPC endpoint URL"
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Output as JSON"
    ),
    timeout: Optional[float] = typer.Option(
        None, "--timeout", help="RPC timeout in seconds"
    ),
):
    """
    List all available snapshots.
    """
    url = _resolve_rpc_url(rpc_url)
    
    try:
        params = {}
        if chain_id is not None:
            params["chain_id"] = chain_id
        
        result = asyncio.run(
            rpc_call("snapshot.list", params, rpc_url=url, timeout=timeout)
        )
        
        if not result.get("success"):
            typer.echo(f"❌ Error: {result.get('error', 'Unknown error')}", err=True)
            raise typer.Exit(code=1)
        
        snapshots = result.get("snapshots", [])
        
        if json_output:
            typer.echo(json.dumps(snapshots, indent=2))
            return
        
        if not snapshots:
            typer.echo("No snapshots found.")
            return
        
        typer.echo(f"Found {len(snapshots)} snapshot(s):\n")
        
        for snap in snapshots:
            typer.echo(f"Chain {snap['chain_id']} - Height {snap['checkpoint_height']}")
            typer.echo(f"  Hash: {snap['checkpoint_hash']}")
            typer.echo(f"  Blocks: {snap['blocks_count']}")
            typer.echo(f"  Accounts: {snap['accounts_count']}")
            typer.echo(f"  Size: {snap['size_mb']:.2f} MB")
            typer.echo(f"  Path: {snap['path']}")
            typer.echo("")
        
    except Exception as e:
        typer.echo(f"❌ Error listing snapshots: {e}", err=True)
        raise typer.Exit(code=1)


@app.command("get")
def get(
    height: int = typer.Argument(..., help="Checkpoint height"),
    chain_id: Optional[int] = typer.Option(
        None, "--chain-id", "-c", help="Chain ID"
    ),
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc", envvar=RPC_ENV, help="RPC endpoint URL"
    ),
    timeout: Optional[float] = typer.Option(
        None, "--timeout", help="RPC timeout in seconds"
    ),
):
    """
    Get manifest for a specific snapshot.
    """
    url = _resolve_rpc_url(rpc_url)
    
    try:
        params = {"height": height}
        if chain_id is not None:
            params["chain_id"] = chain_id
        
        result = asyncio.run(
            rpc_call("snapshot.get", params, rpc_url=url, timeout=timeout)
        )
        
        if not result.get("success"):
            typer.echo(f"❌ Error: {result.get('error', 'Unknown error')}", err=True)
            raise typer.Exit(code=1)
        
        manifest = result.get("manifest", {})
        typer.echo(json.dumps(manifest, indent=2))
        
    except Exception as e:
        typer.echo(f"❌ Error getting snapshot: {e}", err=True)
        raise typer.Exit(code=1)


@app.command("verify")
def verify(
    height: int = typer.Argument(..., help="Checkpoint height"),
    chain_id: Optional[int] = typer.Option(
        None, "--chain-id", "-c", help="Chain ID"
    ),
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc", envvar=RPC_ENV, help="RPC endpoint URL"
    ),
    timeout: Optional[float] = typer.Option(
        None, "--timeout", help="RPC timeout in seconds"
    ),
):
    """
    Verify the integrity of a snapshot.
    """
    url = _resolve_rpc_url(rpc_url)
    
    try:
        typer.echo(f"Verifying snapshot at height {height}...")
        
        params = {"height": height}
        if chain_id is not None:
            params["chain_id"] = chain_id
        
        result = asyncio.run(
            rpc_call("snapshot.verify", params, rpc_url=url, timeout=timeout)
        )
        
        if not result.get("success"):
            typer.echo(f"❌ Error: {result.get('error', 'Unknown error')}", err=True)
            raise typer.Exit(code=1)
        
        if result.get("valid"):
            typer.echo("✅ Snapshot is valid")
        else:
            typer.echo("❌ Snapshot is invalid:")
            for error in result.get("errors", []):
                typer.echo(f"  - {error}")
            raise typer.Exit(code=1)
        
    except Exception as e:
        typer.echo(f"❌ Error verifying snapshot: {e}", err=True)
        raise typer.Exit(code=1)


@app.command("import")
def import_snapshot(
    path: str = typer.Argument(..., help="Path to snapshot directory"),
    verify_hashes: bool = typer.Option(
        True, "--verify/--no-verify", help="Verify chunk hashes"
    ),
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc", envvar=RPC_ENV, help="RPC endpoint URL"
    ),
    timeout: Optional[float] = typer.Option(
        None, "--timeout", help="RPC timeout in seconds (default: 600)"
    ),
):
    """
    Import a snapshot from a directory.
    
    WARNING: This will overwrite existing chain data!
    """
    url = _resolve_rpc_url(rpc_url)
    
    # Use longer default timeout for import
    if timeout is None:
        timeout = 600.0
    
    try:
        typer.echo(f"⚠️  WARNING: This will overwrite existing chain data!")
        if not typer.confirm("Are you sure you want to continue?"):
            raise typer.Exit(code=0)
        
        typer.echo(f"Importing snapshot from {path}...")
        
        result = asyncio.run(
            rpc_call(
                "snapshot.import",
                {"path": path, "verify_hashes": verify_hashes},
                rpc_url=url,
                timeout=timeout,
            )
        )
        
        if not result.get("success"):
            typer.echo(f"❌ Error: {result.get('error', 'Unknown error')}", err=True)
            raise typer.Exit(code=1)
        
        typer.echo("✅ Snapshot imported successfully!")
        typer.echo(f"  Chain ID: {result['chain_id']}")
        typer.echo(f"  Height: {result['checkpoint_height']}")
        typer.echo(f"  Hash: {result['checkpoint_hash']}")
        typer.echo(f"  Blocks: {result['blocks_count']}")
        typer.echo(f"  Accounts: {result['accounts_count']}")
        typer.echo(f"  Elapsed: {result['elapsed_seconds']}s")
        
    except Exception as e:
        typer.echo(f"❌ Error importing snapshot: {e}", err=True)
        raise typer.Exit(code=1)


@app.command("delete")
def delete(
    height: int = typer.Argument(..., help="Checkpoint height"),
    chain_id: Optional[int] = typer.Option(
        None, "--chain-id", "-c", help="Chain ID"
    ),
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc", envvar=RPC_ENV, help="RPC endpoint URL"
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Skip confirmation prompt"
    ),
    timeout: Optional[float] = typer.Option(
        None, "--timeout", help="RPC timeout in seconds"
    ),
):
    """
    Delete a snapshot.
    """
    url = _resolve_rpc_url(rpc_url)
    
    try:
        if not force:
            if not typer.confirm(f"Delete snapshot at height {height}?"):
                raise typer.Exit(code=0)
        
        params = {"height": height}
        if chain_id is not None:
            params["chain_id"] = chain_id
        
        result = asyncio.run(
            rpc_call("snapshot.delete", params, rpc_url=url, timeout=timeout)
        )
        
        if not result.get("success"):
            typer.echo(f"❌ Error: {result.get('error', 'Unknown error')}", err=True)
            raise typer.Exit(code=1)
        
        typer.echo(f"✅ {result.get('message', 'Snapshot deleted')}")
        
    except Exception as e:
        typer.echo(f"❌ Error deleting snapshot: {e}", err=True)
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
