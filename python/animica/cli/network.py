"""
Network management CLI for Animica.

Allows switching between different blockchain networks (mainnet, testnet, devnet)
and persisting the active network choice for subsequent CLI commands.
"""

from __future__ import annotations

from typing import Optional

import typer

from .state import get_cli_state

app = typer.Typer(help="Manage network settings for Animica CLI.")

# Valid network names
VALID_NETWORKS = ["mainnet", "testnet", "devnet", "local-devnet"]
STATE_KEY_NETWORK = "active_network"


@app.command(name="set")
def set_network(
    network: str = typer.Argument(
        ...,
        help=f"Network to switch to. Valid options: {', '.join(VALID_NETWORKS)}",
    )
) -> None:
    """
    Set the active network for Animica CLI operations.

    This sets the default network for all subsequent CLI commands unless
    overridden by --network flag or ANIMICA_NETWORK environment variable.

    Examples:
        animica network set mainnet
        animica network set testnet
        animica network set devnet
    """
    # Validate network name
    if network not in VALID_NETWORKS:
        typer.echo(
            f"Error: Invalid network '{network}'. "
            f"Valid options: {', '.join(VALID_NETWORKS)}",
            err=True,
        )
        raise typer.Exit(code=1)

    # Save to state
    state = get_cli_state()
    state.set(STATE_KEY_NETWORK, network)

    typer.secho(f"✓ Active network set to: {network}", fg=typer.colors.GREEN, bold=True)
    typer.echo(
        f"\nAll subsequent commands will use '{network}' unless overridden with "
        f"--network flag or ANIMICA_NETWORK environment variable."
    )


@app.command(name="get")
def get_network() -> None:
    """
    Show the currently active network.

    Displays the network that will be used by default for CLI commands.
    The actual network used is determined by:
      1. --network command-line flag (highest priority)
      2. ANIMICA_NETWORK environment variable
      3. Persisted setting from 'animica network set'
      4. Default (devnet)

    Examples:
        animica network get
    """
    state = get_cli_state()
    network = state.get(STATE_KEY_NETWORK)

    if network:
        typer.secho(f"Active network: {network}", fg=typer.colors.CYAN, bold=True)
    else:
        typer.echo("No network has been explicitly set.")
        typer.echo("Using default: devnet")
        typer.echo("\nSet a network with: animica network set <network>")


@app.command(name="list")
def list_networks() -> None:
    """
    List all available networks.

    Shows all valid network options that can be used with 'animica network set'.

    Examples:
        animica network list
    """
    state = get_cli_state()
    active = state.get(STATE_KEY_NETWORK)

    typer.echo("Available networks:")
    typer.echo()
    for network in VALID_NETWORKS:
        marker = "●" if network == active else "○"
        color = typer.colors.GREEN if network == active else None
        typer.secho(f"  {marker} {network}", fg=color, bold=(network == active))

    typer.echo()
    if active:
        typer.echo(f"Current active network: {active}")
    else:
        typer.echo("No network explicitly set (using default: devnet)")


if __name__ == "__main__":
    app()
