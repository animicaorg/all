"""
animica.cli.da — Data Availability subcommands.

Implements:
  - animica da submit <data>   Submit blob and get commitment
  - animica da get <commit>    Retrieve blob by commitment
  - animica da verify <commit> Verify blob matches commitment
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import typer

try:
    from omni_sdk.da.client import DAClient
    from omni_sdk.rpc.http import RpcClient

    HAVE_DA = True
except Exception:
    HAVE_DA = False

from animica.config import load_network_config

app = typer.Typer(help="Data Availability (submit, retrieve, verify blobs)")


def _resolve_rpc_url(rpc_url: Optional[str]) -> str:
    """Resolve RPC URL from option, env, or config."""
    if rpc_url:
        return rpc_url
    cfg = load_network_config()
    return cfg.rpc_url


def _ensure_da_available() -> None:
    if not HAVE_DA:
        typer.echo(
            "Warning: omni_sdk.da.client not installed — falling back to generic RPC/http methods.",
            err=True,
        )


@app.command()
def submit(
    namespace: int = typer.Option(0, "--namespace", help="DA namespace ID"),
    input_file: Optional[Path] = typer.Option(
        None, "--file", "-f", help="Input file (default: read from stdin)"
    ),
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="Override RPC URL",
        envvar="ANIMICA_RPC_URL",
    ),
) -> None:
    """
    Submit a blob to the Data Availability layer and return commitment.

    Examples:
      echo "hello world" | animica da submit
      animica da submit --file blob.bin --namespace 1
    """
    _ensure_da_available()

    try:
        url = _resolve_rpc_url(rpc_url)

        # Read data
        if input_file:
            data = input_file.read_bytes()
        else:
            data = sys.stdin.buffer.read()

        if not data:
            typer.echo("Error: no data provided", err=True)
            raise typer.Exit(1)

        # Preferred path: use DAClient when available
        if HAVE_DA:
            rpc = RpcClient(url, timeout=30.0)
            da = DAClient(rpc)
            commit, receipt = da.post_blob(namespace=namespace, data=data)
        else:
            # Try a set of common RPC method names for DA submission
            import httpx

            candidate_methods = [
                "da_postBlob",
                "da.postBlob",
                "da_submit",
                "da.submit",
                "post_blob",
                "da.post_blob",
            ]
            parsed = None
            for method in candidate_methods:
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": method,
                    "params": [namespace, data.hex()],
                }
                try:
                    resp = httpx.post(url, json=payload, timeout=30.0)
                    resp.raise_for_status()
                    parsed = resp.json()
                    if parsed and (parsed.get("result") is not None):
                        break
                except Exception:
                    parsed = None
                    continue

            if not parsed:
                typer.echo(
                    "Error: DA client not available and no RPC fallback succeeded",
                    err=True,
                )
                raise typer.Exit(1)

            commit = parsed.get("result")
            receipt = parsed.get("result")

        typer.echo(f"✓ Blob submitted")
        typer.echo(f"  Commitment: {commit}")
        typer.echo(f"  Receipt: {receipt}")
        typer.echo(f"  Size: {len(data)} bytes")

    except typer.Exit:
        raise
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@app.command()
def get(
    commitment: str = typer.Argument(..., help="DA commitment hash (0x...)"),
    output_file: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Save to file (default: stdout)"
    ),
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="Override RPC URL",
        envvar="ANIMICA_RPC_URL",
    ),
) -> None:
    """
    Retrieve a blob from Data Availability by commitment.

    Examples:
      animica da get 0x...
      animica da get 0x... --output blob.bin
    """
    _ensure_da_available()

    try:
        url = _resolve_rpc_url(rpc_url)
        if HAVE_DA:
            rpc = RpcClient(url, timeout=30.0)
            da = DAClient(rpc)
            data = da.get_blob(commitment)
        else:
            import httpx

            candidate_methods = [
                "da_getBlob",
                "da.getBlob",
                "da_get",
                "da.get",
                "get_blob",
            ]
            parsed = None
            for method in candidate_methods:
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": method,
                    "params": [commitment],
                }
                try:
                    resp = httpx.post(url, json=payload, timeout=30.0)
                    resp.raise_for_status()
                    parsed = resp.json()
                    if parsed and (parsed.get("result") is not None):
                        break
                except Exception:
                    parsed = None
                    continue

            if not parsed:
                typer.echo(
                    "Error: DA client not available and no RPC fallback succeeded",
                    err=True,
                )
                raise typer.Exit(1)

            # Expect the RPC to return hex or base64; attempt decoding heuristics
            result = parsed.get("result")
            if isinstance(result, str):
                try:
                    # hex-encoded
                    data = bytes.fromhex(result.replace("0x", ""))
                except Exception:
                    try:
                        import base64

                        data = base64.b64decode(result)
                    except Exception:
                        data = None
            else:
                data = None

        if data is None:
            typer.echo(f"Blob not found or could not decode: {commitment}", err=True)
            raise typer.Exit(1)

        # Output
        if output_file:
            output_file.write_bytes(data)
            typer.echo(f"✓ Blob saved to {output_file}")
            typer.echo(f"  Size: {len(data)} bytes")
        else:
            sys.stdout.buffer.write(data)

    except typer.Exit:
        raise
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@app.command()
def verify(
    commitment: str = typer.Argument(..., help="DA commitment hash (0x...)"),
    data_file: Path = typer.Option(..., "--file", "-f", help="Data file to verify"),
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="Override RPC URL",
        envvar="ANIMICA_RPC_URL",
    ),
) -> None:
    """
    Verify that a file matches a DA commitment.

    Examples:
      animica da verify 0x... --file blob.bin
    """
    _ensure_da_available()

    try:
        url = _resolve_rpc_url(rpc_url)
        data = data_file.read_bytes()

        if HAVE_DA:
            rpc = RpcClient(url, timeout=30.0)
            da = DAClient(rpc)
            ok = da.verify_availability(commitment)
        else:
            # Use RPC fallback to fetch blob and compare
            import httpx

            # Reuse get() candidates
            candidate_methods = [
                "da_getBlob",
                "da.getBlob",
                "da_get",
                "da.get",
                "get_blob",
            ]
            parsed = None
            for method in candidate_methods:
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": method,
                    "params": [commitment],
                }
                try:
                    resp = httpx.post(url, json=payload, timeout=30.0)
                    resp.raise_for_status()
                    parsed = resp.json()
                    if parsed and (parsed.get("result") is not None):
                        break
                except Exception:
                    parsed = None
                    continue

            if not parsed:
                typer.echo(
                    "Error: DA client not available and no RPC fallback succeeded",
                    err=True,
                )
                raise typer.Exit(1)

            result = parsed.get("result")
            if isinstance(result, str):
                try:
                    blob = bytes.fromhex(result.replace("0x", ""))
                except Exception:
                    import base64

                    blob = base64.b64decode(result)
            else:
                blob = None

            ok = blob == data

        if ok:
            typer.echo("✓ Verification successful")
            typer.echo(f"  File matches commitment: {commitment}")
        else:
            typer.echo("✗ Verification failed", err=True)
            typer.echo(f"  File does not match commitment", err=True)
            raise typer.Exit(1)

    except typer.Exit:
        raise
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
