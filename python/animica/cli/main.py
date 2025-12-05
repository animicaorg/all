"""
animica - Unified CLI for Animica blockchain operations.

A comprehensive command-line interface for:
  - Node lifecycle management (run, status, logs)
  - Wallet & key management (create, import, list, export)
  - Transaction building, signing, and broadcasting
  - Chain queries (heads, blocks, transactions, accounts, events)
  - RPC method calls
  - Mining operations (start, stop, status)
  - Data Availability (submit, retrieve, verify)

Global options:
  --network TEXT          Network profile (local-devnet, devnet, testnet, mainnet)
  --rpc-url TEXT         Override RPC endpoint URL
  --chain-id INTEGER     Override chain ID
  --config PATH          Path to config file
  --json                 Output JSON instead of human-readable text
  --verbose / --no-verbose  Increase verbosity

Examples:
  animica --help
  animica node status
  animica wallet new
  animica key list
  animica tx send --from 0 --to anim1... --value 1.5
  animica chain head
  animica rpc call chain_getHead
  animica da submit < blob.bin
"""

from __future__ import annotations

import os
import sys
from typing import Optional

import typer

# Import subcommand apps
from . import chain, da, key, mining, node, rpc, tx, wallet

app = typer.Typer(
    name="animica",
    help="Animica blockchain command-line interface",
    no_args_is_help=True,
    add_completion=False,
)


# Global state for context
class GlobalContext:
    def __init__(self):
        self.network: Optional[str] = None
        self.rpc_url: Optional[str] = None
        self.chain_id: Optional[int] = None
        self.config_path: Optional[str] = None
        self.json_output: bool = False
        self.verbose: bool = False


_ctx = GlobalContext()


@app.callback()
def main_callback(
    network: Optional[str] = typer.Option(
        None,
        "--network",
        help="Network profile (local-devnet, devnet, testnet, mainnet)",
        envvar="ANIMICA_NETWORK",
    ),
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="Override RPC endpoint URL",
        envvar="ANIMICA_RPC_URL",
    ),
    chain_id: Optional[int] = typer.Option(
        None,
        "--chain-id",
        help="Override chain ID",
        envvar="ANIMICA_CHAIN_ID",
    ),
    config: Optional[str] = typer.Option(
        None,
        "--config",
        help="Path to config file",
        envvar="ANIMICA_CONFIG",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output JSON instead of human-readable text",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Increase verbosity",
    ),
) -> None:
    """
    Animica CLI — blockchain operations for developers and operators.

    This is the main entry point. Most commands are organized into subgroups
    (node, wallet, key, tx, rpc, chain, miner, da) with their own --help.

    Configuration is resolved in this order (highest to lowest priority):
      1. Command-line flags (--rpc-url, --chain-id, etc.)
      2. Environment variables (ANIMICA_RPC_URL, ANIMICA_CHAIN_ID, etc.)
      3. Config file (~/.config/animica/config.toml or ANIMICA_CONFIG)
      4. Built-in defaults (devnet on http://127.0.0.1:8545/rpc)
    """
    _ctx.network = network
    _ctx.rpc_url = rpc_url
    _ctx.chain_id = chain_id
    _ctx.config_path = config
    _ctx.json_output = json_output
    _ctx.verbose = verbose


# Register subcommand groups
app.add_typer(node.app, name="node")
app.add_typer(wallet.app, name="wallet")
app.add_typer(mining.app, name="miner")
app.add_typer(key.app, name="key")
app.add_typer(tx.app, name="tx")
app.add_typer(rpc.app, name="rpc")
app.add_typer(chain.app, name="chain")
app.add_typer(da.app, name="da")


# ============================================================================
# Placeholder subcommands (marked as not yet implemented)
# ============================================================================
# Note: All actual implementations are in separate modules
# (key.py, tx.py, rpc.py, chain.py, da.py)


def main() -> None:
    """Entry point for the animica CLI."""
    app()


if __name__ == "__main__":
    main()
