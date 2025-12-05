"""
animica.cli.tx — Transaction subcommands.

Implements:
  - animica tx build      Build a transaction
  - animica tx sign       Sign a transaction
  - animica tx send       Build, sign, and broadcast
  - animica tx simulate   Dry-run a transaction
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import typer

try:
    from omni_sdk.rpc.http import RpcClient

    HAVE_RPC = True
except Exception:
    HAVE_RPC = False

try:
    from pq.py.signing import sign_message

    HAVE_SIGN = True
except Exception:
    HAVE_SIGN = False

from animica.config import load_network_config

app = typer.Typer(help="Transaction operations (build, sign, send, simulate)")


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


def _pretty(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False)


@app.command()
def build(
    from_addr: str = typer.Option(..., "--from", help="Sender address or key index"),
    to_addr: str = typer.Option(..., "--to", help="Recipient address"),
    value: float = typer.Option(0, "--value", help="Amount to transfer (in ANM)"),
    data: Optional[str] = typer.Option(
        None, "--data", help="Contract call data (hex, starts with 0x)"
    ),
    gas: int = typer.Option(200000, "--gas", help="Gas limit"),
    gas_price: Optional[float] = typer.Option(
        None, "--gas-price", help="Gas price (wei/gas)"
    ),
    nonce: Optional[int] = typer.Option(
        None, "--nonce", help="Transaction nonce (auto-fetched if omitted)"
    ),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Save transaction JSON to file"
    ),
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="Override RPC URL",
        envvar="ANIMICA_RPC_URL",
    ),
) -> None:
    """
    Build a transaction (does not sign or broadcast).

    Examples:
      animica tx build --from anim1... --to anim1... --value 1.5 --gas 200000
      animica tx build --from 0 --to anim1... --value 1 --output tx.json
    """
    # Resolve nonce via RPC (uses fallback helper)
    try:
        # Fetch nonce if not provided
        if nonce is None:
            try:
                nonce_result = _request_rpc(
                    "chain_getTransactionCount", [from_addr], rpc_url
                )
                nonce = int(nonce_result) if nonce_result else 0
            except Exception:
                nonce = 0

        # Build transaction
        tx_data = {
            "from": from_addr,
            "to": to_addr,
            "value": int(value * 1e18) if value else 0,  # Convert ANM to wei
            "data": data or "0x",
            "gas": gas,
            "gasPrice": int(gas_price * 1e9) if gas_price else 1000000000,
            "nonce": nonce,
            "chainId": 31337,  # Default to local devnet
        }

        if output:
            output.write_text(json.dumps(tx_data, indent=2))
            typer.echo(f"✓ Transaction saved to {output}")
        else:
            typer.echo("Transaction (unsigned):")
            typer.echo(_pretty(tx_data))

    except Exception as e:
        typer.echo(f"Error building transaction: {e}", err=True)
        raise typer.Exit(1)


@app.command()
def sign(
    tx_file: Path = typer.Option(..., "--file", "-f", help="Transaction JSON file"),
    key_id: Optional[str] = typer.Option(None, "--key", help="Key ID or wallet index"),
) -> None:
    """
    Sign a transaction with a key from the wallet.

    Examples:
      animica tx sign --file tx.json --key 0
      animica tx sign --file tx.json --key path/to/key.json
    """
    # Signing requires wallet integration; this command is intentionally
    # a placeholder until wallet signing is hooked up.

    try:
        if not tx_file.exists():
            typer.echo(f"File not found: {tx_file}", err=True)
            raise typer.Exit(1)

        tx_data = json.loads(tx_file.read_text())

        if not key_id:
            typer.echo("Error: --key is required", err=True)
            raise typer.Exit(1)

        # TODO: Implement actual signing with wallet integration
        typer.echo("Transaction signing not yet fully implemented", err=True)
        typer.echo("TODO: integrate with wallet keystore", err=True)
        raise typer.Exit(1)

    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@app.command()
def send(
    from_addr: str = typer.Option(..., "--from", help="Sender address or key index"),
    to_addr: str = typer.Option(..., "--to", help="Recipient address"),
    value: float = typer.Option(0, "--value", help="Amount to transfer (in ANM)"),
    gas: int = typer.Option(200000, "--gas", help="Gas limit"),
    key_file: Optional[Path] = typer.Option(
        None, "--key-file", help="Path to key file (for signing)"
    ),
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="Override RPC URL",
        envvar="ANIMICA_RPC_URL",
    ),
) -> None:
    """
    Build, sign, and broadcast a transaction in one step.

    Examples:
      animica tx send --from anim1... --to anim1... --value 1.5 --key-file key.json
    """
    # Sending requires a signer and a node; not implemented fully yet.

    try:
        # TODO: Implement full send workflow
        typer.echo("Transaction send not yet fully implemented", err=True)
        typer.echo("TODO: integrate with wallet and signing", err=True)
        raise typer.Exit(1)

    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@app.command()
def simulate(
    tx_file: Path = typer.Option(..., "--file", "-f", help="Transaction JSON file"),
    from_addr: Optional[str] = typer.Option(
        None, "--from", help="Override sender for simulation"
    ),
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="Override RPC URL",
        envvar="ANIMICA_RPC_URL",
    ),
) -> None:
    """
    Simulate a transaction without broadcasting (dry-run).

    Shows gas usage, return value, and logs.

    Examples:
      animica tx simulate --file tx.json
    """
    try:
        if not tx_file.exists():
            typer.echo(f"File not found: {tx_file}", err=True)
            raise typer.Exit(1)

        tx_data = json.loads(tx_file.read_text())

        if from_addr:
            tx_data["from"] = from_addr

        # Call eth_call (or animica_vm_call) via helper
        result = _request_rpc("eth_call", [tx_data, "latest"], rpc_url)

        typer.echo("Simulation result:")
        typer.echo(_pretty(result))

    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
