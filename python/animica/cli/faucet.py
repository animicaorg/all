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
import logging
import os
import sys
from typing import Optional

import requests
import typer

from animica.coin import COIN_UNIT
from animica.config import load_network_config
from .state import get_cli_state

logger = logging.getLogger(__name__)

app = typer.Typer(
    name="faucet",
    help="Request test funds from the faucet (devnet/testnet only)",
    no_args_is_help=True,
)


def _resolve_network() -> str:
    """
    Resolve the active network name.
    
    Priority:
    1. ANIMICA_NETWORK environment variable
    2. Persisted setting from 'animica network set'
    3. Default (mainnet)
    
    Returns:
        Active network name
    """
    # Check environment variable first (highest priority)
    network = os.environ.get("ANIMICA_NETWORK")
    if network and network.strip():
        logger.debug(f"Network resolved from ANIMICA_NETWORK env var: {network}")
        return network.strip()
    
    # Check persisted state
    state = get_cli_state()
    network = state.get("active_network")
    if network:
        logger.debug(f"Network resolved from CLI state: {network}")
        return network
    
    # Fall back to default
    logger.debug("Network resolved to default: mainnet")
    return "mainnet"


def _resolve_rpc_url(rpc_url: Optional[str], network: Optional[str] = None) -> str:
    """
    Resolve RPC URL from option, env, persisted network, or config.
    
    Priority:
    1. Explicit rpc_url parameter (CLI flag)
    2. ANIMICA_RPC_URL environment variable
    3. Network config based on active network
    
    Empty strings are treated as unset and fall back to the next priority level.
    
    Args:
        rpc_url: Optional RPC URL from CLI flag
        network: Optional network name to override resolution
        
    Returns:
        Resolved RPC URL
    """
    # Check CLI argument first
    if rpc_url and rpc_url.strip():
        logger.debug(f"RPC URL resolved from CLI flag: {rpc_url}")
        return rpc_url.strip()
    
    # Check environment variable
    env_url = os.environ.get("ANIMICA_RPC_URL")
    if env_url and env_url.strip():
        logger.debug(f"RPC URL resolved from ANIMICA_RPC_URL env var: {env_url}")
        return env_url.strip()
    
    # Fall back to network config
    # If network is explicitly provided, use it; otherwise resolve it
    if network:
        active_network = network
    else:
        active_network = _resolve_network()
    cfg = load_network_config(active_network)
    logger.debug(f"RPC URL resolved from network config ({active_network}): {cfg.rpc_url}")
    return cfg.rpc_url


def _rpc_call(method: str, params: dict | list | None = None, rpc_url: Optional[str] = None, network: Optional[str] = None) -> dict:
    """
    Make a JSON-RPC call to the node.
    
    Args:
        method: RPC method name
        params: RPC method parameters
        rpc_url: Optional RPC URL (resolved if None)
        network: Optional network name for RPC URL resolution
        
    Returns:
        RPC response data
    """
    url = _resolve_rpc_url(rpc_url, network)
    logger.debug(f"Making RPC call to {url}: {method}")
    
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
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="Override RPC URL",
        envvar="ANIMICA_RPC_URL",
    ),
    network: Optional[str] = typer.Option(
        None,
        "--network",
        help="Override network (mainnet, testnet, devnet, local-devnet)",
        envvar="ANIMICA_NETWORK",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Enable verbose logging",
    ),
) -> None:
    """
    Request test funds from the faucet.
    
    The faucet is ONLY available on non-mainnet networks (devnet, testnet).
    On mainnet, this command will fail with an error.
    
    Default amount: 500,000,000 ANM (500000000000000000 base units)
    
    The active network is resolved in this order:
      1. --network flag (highest priority)
      2. ANIMICA_NETWORK environment variable
      3. Persisted setting from 'animica network set'
      4. Default (mainnet)
    
    Examples:
      # Request default amount (500M ANM) using active network
      animica faucet request anim1zqp2nx50902d7jgrzk0ep798r2vhpgt3rhtmn89gadzdgyhf9hmln7g9e4xt9
      
      # Request with explicit network override
      animica faucet request anim1... --network testnet
      
      # Request custom amount (1M ANM = 1,000,000,000,000,000 base units)
      animica faucet request anim1... --amount 1000000000000000
    """
    # Configure logging
    if verbose:
        logging.basicConfig(level=logging.DEBUG, format='%(levelname)s: %(message)s')
    
    # Resolve network - use the explicit network parameter if provided
    if network:
        active_network = network
    else:
        active_network = _resolve_network()
    
    if verbose or True:  # Always show network for transparency
        typer.echo(f"Using network: {active_network}", err=True)
    
    # Build params
    params = {"address": address}
    if amount is not None:
        params["amount"] = amount
    
    # Call RPC (pass both rpc_url and network for proper resolution)
    result = _rpc_call("faucet.request", params, rpc_url, active_network)
    
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
        amount_anm = amount_dec / COIN_UNIT
        balance_anm = balance_dec / COIN_UNIT
        
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
  animica faucet request ADDRESS [OPTIONS]

Arguments:
  ADDRESS           Recipient address (bech32m anim1... or hex 0x...)

Options:
  --amount, -a      Amount in base units (default: 500,000,000 ANM)
  --network         Override network (mainnet, testnet, devnet, local-devnet)
  --rpc-url         Override RPC endpoint URL
  --json            Output JSON format
  --verbose, -v     Enable verbose logging

Examples:
  # Request default amount (500 million ANM) using active network
  animica faucet request anim1zqp2nx50902d7jgrzk0ep798r2vhpgt3rhtmn89gadzdgyhf9hmln7g9e4xt9
  
  # Request with explicit network override
  animica faucet request anim1... --network testnet
  
  # Request 1 million ANM (1,000,000,000,000,000 base units)
  animica faucet request anim1... --amount 1000000000000000
  
  # Get JSON output
  animica faucet request anim1... --json

Network Configuration:
  The active network is resolved in this order:
    1. --network flag (highest priority)
    2. ANIMICA_NETWORK environment variable
    3. Persisted setting from 'animica network set'
    4. Default (mainnet)
  
  Set persistent network with:
    animica network set testnet
  
  Or use environment variable:
    export ANIMICA_NETWORK=testnet
    animica faucet request anim1...

Notes:
  - ANM has 9 decimals: 1 ANM = 1,000,000,000 base units
  - No rate limits on testnet/devnet
  - Funds are credited directly (no block production required)
  - Mainnet requests will fail with an error
  - Use --verbose flag to see which network and RPC URL is being used
"""
    typer.echo(help_text)


if __name__ == "__main__":
    app()
