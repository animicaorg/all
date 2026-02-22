"""
animica.cli.da — Data Availability subcommands.

Implements:
  - animica da submit <data>   Submit blob and get commitment
  - animica da put <data>      Alias for submit
  - animica da get <commit>    Retrieve blob by commitment
  - animica da verify <commit> Verify blob matches commitment
  - animica da proof <commit>  Generate/verify DA proof for a commitment
  - animica da storage register --bytes <n> --endpoint <url|local-path>
  - animica da storage list
  - animica da storage heartbeat
  - animica da checkpoints list --namespace <ns>
  - animica da checkpoints verify <commitment>
"""

from __future__ import annotations

import json
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
from .aicf_utils import normalize_rpc_url
from .timeouts import DEFAULT_RPC_TIMEOUT, RPC_TIMEOUT_ENV, describe_timeout, resolve_timeout

app = typer.Typer(help="Data Availability (submit, retrieve, verify blobs)")
storage_app = typer.Typer(help="Storage contributor management")
checkpoints_app = typer.Typer(help="DA checkpoint operations")

app.add_typer(storage_app, name="storage")
app.add_typer(checkpoints_app, name="checkpoints")


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
    timeout: Optional[float] = typer.Option(
        None,
        "--timeout",
        help=f"RPC timeout in seconds (default: {describe_timeout(DEFAULT_RPC_TIMEOUT)})",
        envvar=RPC_TIMEOUT_ENV,
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Output result as JSON"
    ),
) -> None:
    """
    Submit a blob to the Data Availability layer and return commitment.

    Examples:
      echo "hello world" | animica da submit
      animica da submit --file blob.bin --namespace 1
      animica da submit --file data.bin --json
    """
    _ensure_da_available()

    try:
        url = normalize_rpc_url(_resolve_rpc_url(rpc_url))
        resolved_timeout = resolve_timeout("RPC timeout", timeout, env_var=RPC_TIMEOUT_ENV, default=DEFAULT_RPC_TIMEOUT)

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
            rpc = RpcClient(url, timeout=resolved_timeout)
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
                    resp = httpx.post(url, json=payload, timeout=resolved_timeout)
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

        if json_output:
            result = {
                "commitment": commit,
                "receipt": receipt,
                "size": len(data),
                "namespace": namespace,
            }
            typer.echo(json.dumps(result, indent=2))
        else:
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
    timeout: Optional[float] = typer.Option(
        None,
        "--timeout",
        help=f"RPC timeout in seconds (default: {describe_timeout(DEFAULT_RPC_TIMEOUT)})",
        envvar=RPC_TIMEOUT_ENV,
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
        resolved_timeout = resolve_timeout("RPC timeout", timeout, env_var=RPC_TIMEOUT_ENV, default=DEFAULT_RPC_TIMEOUT)
        if HAVE_DA:
            rpc = RpcClient(url, timeout=resolved_timeout)
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
                    resp = httpx.post(url, json=payload, timeout=resolved_timeout)
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
    timeout: Optional[float] = typer.Option(
        None,
        "--timeout",
        help=f"RPC timeout in seconds (default: {describe_timeout(DEFAULT_RPC_TIMEOUT)})",
        envvar=RPC_TIMEOUT_ENV,
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
        resolved_timeout = resolve_timeout("RPC timeout", timeout, env_var=RPC_TIMEOUT_ENV, default=DEFAULT_RPC_TIMEOUT)
        data = data_file.read_bytes()

        if HAVE_DA:
            rpc = RpcClient(url, timeout=resolved_timeout)
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
                    resp = httpx.post(url, json=payload, timeout=resolved_timeout)
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


@app.command()
def put(
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
    timeout: Optional[float] = typer.Option(
        None,
        "--timeout",
        help=f"RPC timeout in seconds (default: {describe_timeout(DEFAULT_RPC_TIMEOUT)})",
        envvar=RPC_TIMEOUT_ENV,
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Output result as JSON"
    ),
) -> None:
    """
    Put a blob to the Data Availability layer (alias for submit).

    Examples:
      echo "hello world" | animica da put
      animica da put --file blob.bin --namespace 1
      animica da put --file data.bin --json
    """
    _ensure_da_available()

    try:
        url = normalize_rpc_url(_resolve_rpc_url(rpc_url))
        resolved_timeout = resolve_timeout("RPC timeout", timeout, env_var=RPC_TIMEOUT_ENV, default=DEFAULT_RPC_TIMEOUT)

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
            rpc = RpcClient(url, timeout=resolved_timeout)
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
                    resp = httpx.post(url, json=payload, timeout=resolved_timeout)
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

        if json_output:
            result = {
                "commitment": commit,
                "receipt": receipt,
                "size": len(data),
                "namespace": namespace,
            }
            typer.echo(json.dumps(result, indent=2))
        else:
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
def proof(
    commitment: str = typer.Argument(..., help="DA commitment hash (0x...)"),
    verify_only: bool = typer.Option(
        False, "--verify", help="Verify the proof instead of generating"
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
        help=f"RPC timeout in seconds (default: {describe_timeout(DEFAULT_RPC_TIMEOUT)})",
        envvar=RPC_TIMEOUT_ENV,
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Output result as JSON"
    ),
) -> None:
    """
    Generate or verify DA proof for a commitment.

    Examples:
      animica da proof 0x...
      animica da proof 0x... --verify
      animica da proof 0x... --json
    """
    _ensure_da_available()

    try:
        url = normalize_rpc_url(_resolve_rpc_url(rpc_url))
        resolved_timeout = resolve_timeout("RPC timeout", timeout, env_var=RPC_TIMEOUT_ENV, default=DEFAULT_RPC_TIMEOUT)

        if HAVE_DA:
            rpc = RpcClient(url, timeout=resolved_timeout)
            da = DAClient(rpc)
            if verify_only:
                ok = da.verify_availability(commitment)
                if json_output:
                    typer.echo(json.dumps({"verified": ok, "commitment": commitment}))
                else:
                    if ok:
                        typer.echo("✓ Proof verified")
                        typer.echo(f"  Commitment: {commitment}")
                    else:
                        typer.echo("✗ Proof verification failed", err=True)
                        raise typer.Exit(1)
            else:
                proof_data = da.get_proof(commitment)
                if json_output:
                    typer.echo(json.dumps(proof_data, indent=2))
                else:
                    typer.echo(f"✓ Proof generated for {commitment}")
                    typer.echo(json.dumps(proof_data, indent=2))
        else:
            import httpx

            # Try RPC fallback
            method = "da.getProof" if not verify_only else "da.verifyAvailability"
            candidate_methods = [
                method,
                "da_getProof" if not verify_only else "da_verifyAvailability",
                "get_proof" if not verify_only else "verify_availability",
            ]
            
            parsed = None
            for m in candidate_methods:
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": m,
                    "params": [commitment],
                }
                try:
                    resp = httpx.post(url, json=payload, timeout=resolved_timeout)
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
            if verify_only:
                ok = result if isinstance(result, bool) else bool(result)
                if json_output:
                    typer.echo(json.dumps({"verified": ok, "commitment": commitment}))
                else:
                    if ok:
                        typer.echo("✓ Proof verified")
                        typer.echo(f"  Commitment: {commitment}")
                    else:
                        typer.echo("✗ Proof verification failed", err=True)
                        raise typer.Exit(1)
            else:
                if json_output:
                    typer.echo(json.dumps(result, indent=2))
                else:
                    typer.echo(f"✓ Proof generated for {commitment}")
                    typer.echo(json.dumps(result, indent=2))

    except typer.Exit:
        raise
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


# ============================================================================
# Storage contributor commands
# ============================================================================

@storage_app.command("register")
def storage_register(
    bytes_capacity: int = typer.Option(..., "--bytes", help="Storage capacity in bytes"),
    endpoint: str = typer.Option(..., "--endpoint", help="Storage endpoint URL or local path"),
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="Override RPC URL",
        envvar="ANIMICA_RPC_URL",
    ),
    timeout: Optional[float] = typer.Option(
        None,
        "--timeout",
        help=f"RPC timeout in seconds (default: {describe_timeout(DEFAULT_RPC_TIMEOUT)})",
        envvar=RPC_TIMEOUT_ENV,
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Output result as JSON"
    ),
) -> None:
    """
    Register as a storage contributor.

    Examples:
      animica da storage register --bytes 1000000000 --endpoint http://storage.example.com
      animica da storage register --bytes 500000000 --endpoint /mnt/storage/da
    """
    try:
        # Validate endpoint
        endpoint_path = Path(endpoint)
        is_local = False
        
        # Check if it's a local path
        if not endpoint.startswith(("http://", "https://")):
            # Treat as local path
            is_local = True
            if not endpoint_path.exists():
                typer.echo(f"Error: Local path does not exist: {endpoint}", err=True)
                raise typer.Exit(1)
            if not endpoint_path.is_dir():
                typer.echo(f"Error: Local path is not a directory: {endpoint}", err=True)
                raise typer.Exit(1)
            # Security check: ensure writable
            try:
                test_file = endpoint_path / ".write_test"
                test_file.touch()
                test_file.unlink()
            except Exception as e:
                typer.echo(f"Error: Directory not writable: {endpoint} ({e})", err=True)
                raise typer.Exit(1)
        
        if bytes_capacity <= 0:
            typer.echo("Error: Storage capacity must be positive", err=True)
            raise typer.Exit(1)

        url = normalize_rpc_url(_resolve_rpc_url(rpc_url))
        resolved_timeout = resolve_timeout("RPC timeout", timeout, env_var=RPC_TIMEOUT_ENV, default=DEFAULT_RPC_TIMEOUT)

        # Call RPC method to register storage contributor
        import httpx

        candidate_methods = [
            "da.storage.register",
            "da_storage_register",
            "storage.register",
            "registerStorage",
        ]
        
        params = {
            "capacity_bytes": bytes_capacity,
            "endpoint": endpoint,
            "is_local": is_local,
        }
        
        parsed = None
        for method in candidate_methods:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": method,
                "params": [params],
            }
            try:
                resp = httpx.post(url, json=payload, timeout=resolved_timeout)
                resp.raise_for_status()
                parsed = resp.json()
                if parsed and (parsed.get("result") is not None):
                    break
            except Exception:
                parsed = None
                continue

        if not parsed:
            typer.echo(
                "Error: Storage registration RPC method not available",
                err=True,
            )
            raise typer.Exit(1)

        result = parsed.get("result")
        if json_output:
            typer.echo(json.dumps(result, indent=2))
        else:
            typer.echo("✓ Storage contributor registered")
            typer.echo(f"  Capacity: {bytes_capacity:,} bytes")
            typer.echo(f"  Endpoint: {endpoint}")
            typer.echo(f"  Type: {'local' if is_local else 'remote'}")
            if isinstance(result, dict):
                if "contributor_id" in result:
                    typer.echo(f"  ID: {result['contributor_id']}")

    except typer.Exit:
        raise
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@storage_app.command("list")
def storage_list(
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="Override RPC URL",
        envvar="ANIMICA_RPC_URL",
    ),
    timeout: Optional[float] = typer.Option(
        None,
        "--timeout",
        help=f"RPC timeout in seconds (default: {describe_timeout(DEFAULT_RPC_TIMEOUT)})",
        envvar=RPC_TIMEOUT_ENV,
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Output result as JSON"
    ),
) -> None:
    """
    List registered storage contributors.

    Examples:
      animica da storage list
      animica da storage list --json
    """
    try:
        url = normalize_rpc_url(_resolve_rpc_url(rpc_url))
        resolved_timeout = resolve_timeout("RPC timeout", timeout, env_var=RPC_TIMEOUT_ENV, default=DEFAULT_RPC_TIMEOUT)

        import httpx

        candidate_methods = [
            "da.storage.list",
            "da_storage_list",
            "storage.list",
            "listStorage",
        ]
        
        parsed = None
        for method in candidate_methods:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": method,
                "params": [],
            }
            try:
                resp = httpx.post(url, json=payload, timeout=resolved_timeout)
                resp.raise_for_status()
                parsed = resp.json()
                if parsed and (parsed.get("result") is not None):
                    break
            except Exception:
                parsed = None
                continue

        if not parsed:
            typer.echo(
                "Error: Storage list RPC method not available",
                err=True,
            )
            raise typer.Exit(1)

        result = parsed.get("result", [])
        
        if json_output:
            typer.echo(json.dumps(result, indent=2))
        else:
            if not result:
                typer.echo("No storage contributors registered")
            else:
                typer.echo(f"Storage Contributors ({len(result)}):")
                for i, contrib in enumerate(result, 1):
                    typer.echo(f"\n{i}. {contrib.get('id', 'unknown')}")
                    typer.echo(f"   Capacity: {contrib.get('capacity_bytes', 0):,} bytes")
                    typer.echo(f"   Endpoint: {contrib.get('endpoint', 'N/A')}")
                    typer.echo(f"   Status: {contrib.get('status', 'unknown')}")
                    if 'last_heartbeat' in contrib:
                        typer.echo(f"   Last heartbeat: {contrib['last_heartbeat']}")

    except typer.Exit:
        raise
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@storage_app.command("heartbeat")
def storage_heartbeat(
    contributor_id: Optional[str] = typer.Option(
        None, "--id", help="Storage contributor ID (auto-detected if not provided)"
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
        help=f"RPC timeout in seconds (default: {describe_timeout(DEFAULT_RPC_TIMEOUT)})",
        envvar=RPC_TIMEOUT_ENV,
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Output result as JSON"
    ),
) -> None:
    """
    Send heartbeat for storage contributor.

    Examples:
      animica da storage heartbeat
      animica da storage heartbeat --id contributor-123
    """
    try:
        url = normalize_rpc_url(_resolve_rpc_url(rpc_url))
        resolved_timeout = resolve_timeout("RPC timeout", timeout, env_var=RPC_TIMEOUT_ENV, default=DEFAULT_RPC_TIMEOUT)

        import httpx

        candidate_methods = [
            "da.storage.heartbeat",
            "da_storage_heartbeat",
            "storage.heartbeat",
            "storageHeartbeat",
        ]
        
        params = []
        if contributor_id:
            params = [{"contributor_id": contributor_id}]
        
        parsed = None
        for method in candidate_methods:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": method,
                "params": params,
            }
            try:
                resp = httpx.post(url, json=payload, timeout=resolved_timeout)
                resp.raise_for_status()
                parsed = resp.json()
                if parsed and (parsed.get("result") is not None):
                    break
            except Exception:
                parsed = None
                continue

        if not parsed:
            typer.echo(
                "Error: Storage heartbeat RPC method not available",
                err=True,
            )
            raise typer.Exit(1)

        result = parsed.get("result")
        
        if json_output:
            typer.echo(json.dumps(result, indent=2))
        else:
            typer.echo("✓ Heartbeat sent")
            if isinstance(result, dict):
                if "timestamp" in result:
                    typer.echo(f"  Timestamp: {result['timestamp']}")
                if "next_heartbeat" in result:
                    typer.echo(f"  Next heartbeat: {result['next_heartbeat']}")

    except typer.Exit:
        raise
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


# ============================================================================
# Checkpoint commands
# ============================================================================

@checkpoints_app.command("list")
def checkpoints_list(
    namespace: Optional[int] = typer.Option(
        None, "--namespace", help="Filter by namespace (e.g., ena namespace)"
    ),
    limit: int = typer.Option(
        10, "--limit", help="Maximum number of checkpoints to return"
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
        help=f"RPC timeout in seconds (default: {describe_timeout(DEFAULT_RPC_TIMEOUT)})",
        envvar=RPC_TIMEOUT_ENV,
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Output result as JSON"
    ),
) -> None:
    """
    List DA checkpoints.

    Examples:
      animica da checkpoints list
      animica da checkpoints list --namespace ena
      animica da checkpoints list --limit 20 --json
    """
    try:
        url = normalize_rpc_url(_resolve_rpc_url(rpc_url))
        resolved_timeout = resolve_timeout("RPC timeout", timeout, env_var=RPC_TIMEOUT_ENV, default=DEFAULT_RPC_TIMEOUT)

        import httpx

        candidate_methods = [
            "da.checkpoints.list",
            "da_checkpoints_list",
            "checkpoints.list",
            "listCheckpoints",
        ]
        
        params = {"limit": limit}
        if namespace is not None:
            params["namespace"] = namespace
        
        parsed = None
        for method in candidate_methods:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": method,
                "params": [params],
            }
            try:
                resp = httpx.post(url, json=payload, timeout=resolved_timeout)
                resp.raise_for_status()
                parsed = resp.json()
                if parsed and (parsed.get("result") is not None):
                    break
            except Exception:
                parsed = None
                continue

        if not parsed:
            typer.echo(
                "Error: Checkpoints list RPC method not available",
                err=True,
            )
            raise typer.Exit(1)

        result = parsed.get("result", [])
        
        if json_output:
            typer.echo(json.dumps(result, indent=2))
        else:
            if not result:
                typer.echo("No checkpoints found")
            else:
                ns_filter = f" (namespace {namespace})" if namespace is not None else ""
                typer.echo(f"DA Checkpoints{ns_filter} ({len(result)}):")
                for i, ckpt in enumerate(result, 1):
                    typer.echo(f"\n{i}. {ckpt.get('commitment', 'unknown')}")
                    typer.echo(f"   Namespace: {ckpt.get('namespace', 'N/A')}")
                    typer.echo(f"   Height: {ckpt.get('height', 'N/A')}")
                    typer.echo(f"   Timestamp: {ckpt.get('timestamp', 'N/A')}")
                    if 'size' in ckpt:
                        typer.echo(f"   Size: {ckpt['size']:,} bytes")

    except typer.Exit:
        raise
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@checkpoints_app.command("verify")
def checkpoints_verify(
    commitment: str = typer.Argument(..., help="Checkpoint commitment to verify"),
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="Override RPC URL",
        envvar="ANIMICA_RPC_URL",
    ),
    timeout: Optional[float] = typer.Option(
        None,
        "--timeout",
        help=f"RPC timeout in seconds (default: {describe_timeout(DEFAULT_RPC_TIMEOUT)})",
        envvar=RPC_TIMEOUT_ENV,
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Output result as JSON"
    ),
) -> None:
    """
    Verify checkpoint commitment.

    Examples:
      animica da checkpoints verify 0x...
      animica da checkpoints verify 0x... --json
    """
    try:
        url = normalize_rpc_url(_resolve_rpc_url(rpc_url))
        resolved_timeout = resolve_timeout("RPC timeout", timeout, env_var=RPC_TIMEOUT_ENV, default=DEFAULT_RPC_TIMEOUT)

        import httpx

        candidate_methods = [
            "da.checkpoints.verify",
            "da_checkpoints_verify",
            "checkpoints.verify",
            "verifyCheckpoint",
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
                resp = httpx.post(url, json=payload, timeout=resolved_timeout)
                resp.raise_for_status()
                parsed = resp.json()
                if parsed and (parsed.get("result") is not None):
                    break
            except Exception:
                parsed = None
                continue

        if not parsed:
            typer.echo(
                "Error: Checkpoint verify RPC method not available",
                err=True,
            )
            raise typer.Exit(1)

        result = parsed.get("result")
        
        if isinstance(result, bool):
            verified = result
            details = {}
        elif isinstance(result, dict):
            verified = result.get("verified", False)
            details = result
        else:
            verified = bool(result)
            details = {}
        
        if json_output:
            output = {"verified": verified, "commitment": commitment}
            if details:
                output["details"] = details
            typer.echo(json.dumps(output, indent=2))
        else:
            if verified:
                typer.echo("✓ Checkpoint verified")
                typer.echo(f"  Commitment: {commitment}")
                if details:
                    for key, value in details.items():
                        if key != "verified":
                            typer.echo(f"  {key.capitalize()}: {value}")
            else:
                typer.echo("✗ Checkpoint verification failed", err=True)
                typer.echo(f"  Commitment: {commitment}", err=True)
                raise typer.Exit(1)

    except typer.Exit:
        raise
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


# ============================================================================
# Node-side DA commands (da.status, da.configure, da.has, da.list,
# da.delete, da.prune) — thin wrappers over the node RPC methods
# ============================================================================


def _rpc_call(url: str, method_name: str, params: object, timeout: float) -> object:
    """
    Call a JSON-RPC method and return the ``result`` field, or raise on error.
    """
    import httpx

    payload = {"jsonrpc": "2.0", "id": 1, "method": method_name, "params": params}
    try:
        resp = httpx.post(url, json=payload, timeout=timeout)
        resp.raise_for_status()
        body = resp.json()
    except Exception as exc:
        typer.echo(f"RPC transport error: {exc}", err=True)
        raise typer.Exit(1)

    if "error" in body:
        err = body["error"]
        msg = err.get("message", "RPC error") if isinstance(err, dict) else str(err)
        typer.echo(f"RPC error: {msg}", err=True)
        raise typer.Exit(1)

    return body.get("result")


@app.command("status")
def node_da_status(
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc-url", help="Override RPC URL", envvar="ANIMICA_RPC_URL"
    ),
    timeout: Optional[float] = typer.Option(
        None,
        "--timeout",
        help=f"RPC timeout in seconds (default: {describe_timeout(DEFAULT_RPC_TIMEOUT)})",
        envvar=RPC_TIMEOUT_ENV,
    ),
    json_output: bool = typer.Option(False, "--json", help="Output result as JSON"),
) -> None:
    """
    Show node-side DA store status.

    Examples:
      animica da status
      animica da status --json
    """
    url = normalize_rpc_url(_resolve_rpc_url(rpc_url))
    resolved_timeout = resolve_timeout(
        "RPC timeout", timeout, env_var=RPC_TIMEOUT_ENV, default=DEFAULT_RPC_TIMEOUT
    )
    result = _rpc_call(url, "da.status", {}, resolved_timeout)

    if json_output:
        typer.echo(json.dumps(result, indent=2))
        return

    if not isinstance(result, dict):
        typer.echo(str(result))
        return

    enabled = result.get("enabled", False)
    status_icon = "✓" if enabled else "✗"
    typer.echo(f"{status_icon} DA store {'enabled' if enabled else 'disabled'}")
    typer.echo(f"  Dir       : {result.get('dir', 'N/A')}")
    used = result.get("used_bytes", 0) or 0
    max_b = result.get("max_bytes", 0) or 0
    typer.echo(f"  Used      : {used:,} bytes")
    if max_b > 0:
        typer.echo(f"  Max       : {max_b:,} bytes")
    typer.echo(f"  Blobs     : {result.get('blob_count', 0):,}")
    typer.echo(f"  Free (FS) : {result.get('free_bytes_fs', 0):,} bytes")
    typer.echo(f"  Remote GET: {result.get('allow_remote_get', False)}")
    typer.echo(f"  Remote PUT: {result.get('allow_remote_put', False)}")
    typer.echo(f"  On full   : {result.get('on_full', 'evict')}")
    if result.get("last_error"):
        typer.echo(f"  Last error: {result['last_error']}", err=True)


@app.command("configure")
def node_da_configure(
    enabled: Optional[bool] = typer.Option(
        None, "--enabled/--disabled", help="Enable or disable the DA store"
    ),
    da_dir: Optional[str] = typer.Option(
        None, "--dir", help="DA store directory path"
    ),
    max_gb: Optional[float] = typer.Option(
        None, "--max-gb", help="Maximum storage in GB (e.g. 10)"
    ),
    max_bytes_opt: Optional[int] = typer.Option(
        None, "--max-bytes", help="Maximum storage in bytes (overrides --max-gb)"
    ),
    on_full: Optional[str] = typer.Option(
        None, "--on-full", help="Behavior when quota full: evict (default) or reject"
    ),
    allow_remote_put: Optional[bool] = typer.Option(
        None,
        "--allow-remote-put/--no-remote-put",
        help="Allow remote peers to upload blobs (default: off)",
    ),
    allow_remote_get: Optional[bool] = typer.Option(
        None,
        "--allow-remote-get/--no-remote-get",
        help="Allow remote peers to fetch blobs (default: on)",
    ),
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc-url", help="Override RPC URL", envvar="ANIMICA_RPC_URL"
    ),
    timeout: Optional[float] = typer.Option(
        None,
        "--timeout",
        help=f"RPC timeout in seconds (default: {describe_timeout(DEFAULT_RPC_TIMEOUT)})",
        envvar=RPC_TIMEOUT_ENV,
    ),
    json_output: bool = typer.Option(False, "--json", help="Output result as JSON"),
) -> None:
    """
    Configure the node-side DA store.

    Examples:
      animica da configure --enabled --dir ~/.animica/da_store --max-gb 10
      animica da configure --disabled
      animica da configure --on-full reject --allow-remote-put
    """
    params: dict = {}
    if enabled is not None:
        params["enabled"] = enabled
    if da_dir is not None:
        params["dir"] = da_dir
    if max_bytes_opt is not None:
        params["max_bytes"] = max_bytes_opt
    elif max_gb is not None:
        params["max_bytes"] = int(max_gb * 1024 * 1024 * 1024)
    if on_full is not None:
        if on_full not in ("evict", "reject"):
            typer.echo("Error: --on-full must be 'evict' or 'reject'", err=True)
            raise typer.Exit(1)
        params["on_full"] = on_full
    if allow_remote_put is not None:
        params["allow_remote_put"] = allow_remote_put
    if allow_remote_get is not None:
        params["allow_remote_get"] = allow_remote_get

    if not params:
        typer.echo("Error: Provide at least one option to configure", err=True)
        raise typer.Exit(1)

    url = normalize_rpc_url(_resolve_rpc_url(rpc_url))
    resolved_timeout = resolve_timeout(
        "RPC timeout", timeout, env_var=RPC_TIMEOUT_ENV, default=DEFAULT_RPC_TIMEOUT
    )
    result = _rpc_call(url, "da.configure", params, resolved_timeout)

    if json_output:
        typer.echo(json.dumps(result, indent=2))
        return

    typer.echo("✓ DA store configured")
    if isinstance(result, dict):
        typer.echo(f"  Enabled   : {result.get('enabled', False)}")
        typer.echo(f"  Dir       : {result.get('dir', 'N/A')}")
        max_b = result.get("max_bytes", 0) or 0
        if max_b > 0:
            typer.echo(f"  Max       : {max_b:,} bytes ({max_b / 1e9:.2f} GB)")
        typer.echo(f"  On full   : {result.get('on_full', 'evict')}")
        typer.echo(f"  Remote GET: {result.get('allow_remote_get', True)}")
        typer.echo(f"  Remote PUT: {result.get('allow_remote_put', False)}")


@app.command("has")
def node_da_has(
    blob_id: str = typer.Option(..., "--id", help="Blob ID to check"),
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc-url", help="Override RPC URL", envvar="ANIMICA_RPC_URL"
    ),
    timeout: Optional[float] = typer.Option(
        None,
        "--timeout",
        help=f"RPC timeout in seconds (default: {describe_timeout(DEFAULT_RPC_TIMEOUT)})",
        envvar=RPC_TIMEOUT_ENV,
    ),
    json_output: bool = typer.Option(False, "--json", help="Output result as JSON"),
) -> None:
    """
    Check whether a blob exists in the node DA store.

    Exits with code 0 if present, 1 if not found.

    Examples:
      animica da has --id <blob_id>
      animica da has --id <blob_id> --json
    """
    url = normalize_rpc_url(_resolve_rpc_url(rpc_url))
    resolved_timeout = resolve_timeout(
        "RPC timeout", timeout, env_var=RPC_TIMEOUT_ENV, default=DEFAULT_RPC_TIMEOUT
    )
    result = _rpc_call(url, "da.has", {"blob_id": blob_id}, resolved_timeout)

    exists = result.get("exists", False) if isinstance(result, dict) else bool(result)

    if json_output:
        typer.echo(json.dumps({"blob_id": blob_id, "exists": exists}))
    else:
        typer.echo("true" if exists else "false")

    if not exists:
        raise typer.Exit(1)


@app.command("list")
def node_da_list(
    limit: int = typer.Option(50, "--limit", help="Maximum number of blobs to list"),
    cursor: Optional[str] = typer.Option(
        None, "--cursor", help="Pagination cursor from previous call"
    ),
    order: str = typer.Option(
        "newest", "--order", help="Sort order: newest (default) or lru"
    ),
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc-url", help="Override RPC URL", envvar="ANIMICA_RPC_URL"
    ),
    timeout: Optional[float] = typer.Option(
        None,
        "--timeout",
        help=f"RPC timeout in seconds (default: {describe_timeout(DEFAULT_RPC_TIMEOUT)})",
        envvar=RPC_TIMEOUT_ENV,
    ),
    json_output: bool = typer.Option(False, "--json", help="Output result as JSON"),
) -> None:
    """
    List blobs stored in the node DA store.

    Examples:
      animica da list
      animica da list --limit 20 --json
      animica da list --order lru
    """
    url = normalize_rpc_url(_resolve_rpc_url(rpc_url))
    resolved_timeout = resolve_timeout(
        "RPC timeout", timeout, env_var=RPC_TIMEOUT_ENV, default=DEFAULT_RPC_TIMEOUT
    )
    params: dict = {"limit": limit, "order": order}
    if cursor:
        params["cursor"] = cursor

    result = _rpc_call(url, "da.list", params, resolved_timeout)

    if json_output:
        typer.echo(json.dumps(result, indent=2))
        return

    items = result.get("items", []) if isinstance(result, dict) else []
    next_cur = result.get("next_cursor") if isinstance(result, dict) else None

    if not items:
        typer.echo("No blobs found in DA store")
        return

    typer.echo(f"Blobs ({len(items)}):")
    for item in items:
        typer.echo(
            f"  {item.get('blob_id', '?')}  "
            f"{item.get('size_bytes', 0):>10,} bytes  "
            f"created={item.get('created_at', 0)}"
        )

    if next_cur:
        typer.echo(f"\nNext cursor: {next_cur}")


@app.command("delete")
def node_da_delete(
    blob_id: str = typer.Option(..., "--id", help="Blob ID to delete"),
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc-url", help="Override RPC URL", envvar="ANIMICA_RPC_URL"
    ),
    timeout: Optional[float] = typer.Option(
        None,
        "--timeout",
        help=f"RPC timeout in seconds (default: {describe_timeout(DEFAULT_RPC_TIMEOUT)})",
        envvar=RPC_TIMEOUT_ENV,
    ),
    json_output: bool = typer.Option(False, "--json", help="Output result as JSON"),
) -> None:
    """
    Delete a blob from the node DA store.

    Examples:
      animica da delete --id <blob_id>
    """
    url = normalize_rpc_url(_resolve_rpc_url(rpc_url))
    resolved_timeout = resolve_timeout(
        "RPC timeout", timeout, env_var=RPC_TIMEOUT_ENV, default=DEFAULT_RPC_TIMEOUT
    )
    result = _rpc_call(url, "da.delete", {"blob_id": blob_id}, resolved_timeout)

    deleted = result.get("deleted", False) if isinstance(result, dict) else bool(result)

    if json_output:
        typer.echo(json.dumps({"blob_id": blob_id, "deleted": deleted}))
    else:
        if deleted:
            typer.echo(f"✓ Blob deleted: {blob_id}")
        else:
            typer.echo(f"Blob not found: {blob_id}", err=True)
            raise typer.Exit(1)


@app.command("prune")
def node_da_prune(
    target_gb: Optional[float] = typer.Option(
        None, "--target-gb", help="Free at least this many GB via LRU eviction"
    ),
    target_bytes_opt: Optional[int] = typer.Option(
        None,
        "--target-bytes",
        help="Free at least this many bytes (overrides --target-gb)",
    ),
    older_than: Optional[int] = typer.Option(
        None, "--older-than", help="Remove blobs older than this many seconds"
    ),
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc-url", help="Override RPC URL", envvar="ANIMICA_RPC_URL"
    ),
    timeout: Optional[float] = typer.Option(
        None,
        "--timeout",
        help=f"RPC timeout in seconds (default: {describe_timeout(DEFAULT_RPC_TIMEOUT)})",
        envvar=RPC_TIMEOUT_ENV,
    ),
    json_output: bool = typer.Option(False, "--json", help="Output result as JSON"),
) -> None:
    """
    Prune (garbage-collect) blobs from the node DA store.

    Examples:
      animica da prune --target-gb 2
      animica da prune --older-than 86400
      animica da prune --target-gb 1 --older-than 3600
    """
    params: dict = {}
    if target_bytes_opt is not None:
        params["target_bytes"] = target_bytes_opt
    elif target_gb is not None:
        params["target_bytes"] = int(target_gb * 1024 * 1024 * 1024)
    if older_than is not None:
        params["older_than_seconds"] = older_than

    if not params:
        typer.echo(
            "Error: Provide at least one of --target-gb, --target-bytes, or --older-than",
            err=True,
        )
        raise typer.Exit(1)

    url = normalize_rpc_url(_resolve_rpc_url(rpc_url))
    resolved_timeout = resolve_timeout(
        "RPC timeout", timeout, env_var=RPC_TIMEOUT_ENV, default=DEFAULT_RPC_TIMEOUT
    )
    result = _rpc_call(url, "da.gc", params, resolved_timeout)

    if json_output:
        typer.echo(json.dumps(result, indent=2))
        return

    freed = result.get("freed_bytes", 0) if isinstance(result, dict) else 0
    removed = result.get("removed_count", 0) if isinstance(result, dict) else 0
    typer.echo(f"✓ Pruned {removed} blob(s), freed {freed:,} bytes")
