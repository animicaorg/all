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
