"""
Faucet CLI commands
===================

Request test funds from the devnet/testnet faucet.

Commands:
  - animica faucet request [address] [--amount AMOUNT]

Note: Faucet is ONLY available on non-mainnet networks (devnet, testnet).
"""

from __future__ import annotations

import json
import os
import sys
from typing import Optional

import requests
import typer

app = typer.Typer(
    name="faucet",
    help="Request test funds from the faucet (devnet/testnet only)",
    no_args_is_help=True,
)


def _get_rpc_url() -> str:
    """Get RPC URL from environment or use default."""
    return os.getenv("ANIMICA_RPC_URL", "http://127.0.0.1:8545/rpc")


def _rpc_call(method: str, params: dict | list | None = None) -> dict:
    """Make a JSON-RPC call to the node."""
    url = _get_rpc_url()
    payload = {
        "jsonrpc": "2.0",
        "method": method,
        "params": params or {},
        "id": 1
    }
    
    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        typer.secho(f"Failed to connect to RPC at {url}: {e}", fg=typer.colors.RED, err=True)
        sys.exit(1)
    
    try:
        data = resp.json()
    except json.JSONDecodeError as e:
        typer.secho(f"Failed to decode JSON response: {e}", fg=typer.colors.RED, err=True)
        sys.exit(1)
    
    if "error" in data:
        error = data["error"]
        msg = error.get("message", "Unknown error")
        code = error.get("code", -32000)
        err_data = error.get("data", {})
        
        typer.secho(f"Error: {msg}", fg=typer.colors.RED, err=True)
        if err_data:
            typer.secho(f"Details: {json.dumps(err_data, indent=2)}", fg=typer.colors.RED, err=True)
        sys.exit(1)
    
    return data


@app.command("request")
def request_funds(
    address: str = typer.Argument(
        ...,
        help="Recipient address (bech32m anim1... or hex 0x...)"
    ),
    amount: Optional[int] = typer.Option(
        None,
        "--amount",
        "-a",
        help="Amount in base units (default: 500,000,000 ANM = 500000000000000000 base units)"
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output JSON instead of human-readable text"
    ),
) -> None:
    """
    Request test funds from the faucet.
    
    The faucet is ONLY available on non-mainnet networks (devnet, testnet).
    On mainnet, this command will fail with an error.
    
    Default amount: 500,000,000 ANM (500000000000000000 base units)
    
    Examples:
      # Request default amount (500M ANM)
      animica faucet request anim1zqp8gjpns43wcy2p8rj3w3uvn2dwkxx99nkwg020u4ql6gu3yfqzgzglw560f
      
      # Request custom amount (1M ANM = 1,000,000,000,000,000 base units)
      animica faucet request anim1... --amount 1000000000000000
    """
    # Build params
    params = {"address": address}
    if amount is not None:
        params["amount"] = amount
    
    # Call RPC
    result = _rpc_call("faucet.request", params)
    
    if json_output:
        typer.echo(json.dumps(result.get("result", {}), indent=2))
    else:
        res = result.get("result", {})
        addr = res.get("address", "unknown")
        amount_hex = res.get("amount", "0x0")
        balance_hex = res.get("balance", "0x0")
        message = res.get("message", "")
        
        # Convert hex to decimal for display
        amount_dec = int(amount_hex, 16) if amount_hex.startswith("0x") else int(amount_hex)
        balance_dec = int(balance_hex, 16) if balance_hex.startswith("0x") else int(balance_hex)
        
        # Convert to ANM (9 decimals)
        amount_anm = amount_dec / 1_000_000_000
        balance_anm = balance_dec / 1_000_000_000
        
        typer.secho("✓ Faucet request successful!", fg=typer.colors.GREEN, bold=True)
        typer.echo(f"  Address:      {addr}")
        typer.echo(f"  Credited:     {amount_anm:,.1f} ANM ({amount_dec:,} base units)")
        typer.echo(f"  New balance:  {balance_anm:,.1f} ANM ({balance_dec:,} base units)")
        if message:
            typer.echo(f"  Message:      {message}")


@app.command("help")
def show_help() -> None:
    """Show detailed help for the faucet commands."""
    help_text = """
Animica Faucet - Testnet/Devnet Fund Request
=============================================

The faucet provides unlimited test funds on non-mainnet networks (devnet, testnet).

IMPORTANT: The faucet is NOT available on mainnet (chainId=1).

Usage:
  animica faucet request ADDRESS [--amount AMOUNT] [--json]

Arguments:
  ADDRESS           Recipient address (bech32m anim1... or hex 0x...)

Options:
  --amount, -a      Amount in base units (default: 500,000,000 ANM)
  --json            Output JSON format

Examples:
  # Request default amount (500 million ANM)
  animica faucet request anim1zqp8gjpns43wcy2p8rj3w3uvn2dwkxx99nkwg020u4ql6gu3yfqzgzglw560f
  
  # Request 1 million ANM (1,000,000,000,000,000 base units)
  animica faucet request anim1... --amount 1000000000000000
  
  # Get JSON output
  animica faucet request anim1... --json

Network Configuration:
  Set ANIMICA_RPC_URL to point to your target network:
    export ANIMICA_RPC_URL=http://127.0.0.1:8545/rpc  # devnet (default)
    export ANIMICA_RPC_URL=https://testnet-rpc.animica.org/rpc  # testnet

Notes:
  - ANM has 9 decimals: 1 ANM = 1,000,000,000 base units
  - No rate limits on testnet/devnet
  - Funds are credited directly (no block production required)
  - Mainnet requests will fail with an error
"""
    typer.echo(help_text)


if __name__ == "__main__":
    app()
