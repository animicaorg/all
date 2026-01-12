"""CLI commands for exporting and managing wallet balances."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

from animica.config import load_network_config
from animica.cli.wallet_balances import (
    export_wallet_balances_sync,
    _get_balance_backup_path,
)

app = typer.Typer(help="Export and manage wallet balances")


@app.command("export")
def export_balances(
    network: Optional[str] = typer.Option(
        None,
        "--network",
        help="Network to export balances from (defaults to active network)",
    ),
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="RPC endpoint URL (default: network default)",
        envvar="ANIMICA_RPC_URL",
    ),
    wallet_file: Optional[Path] = typer.Option(
        None,
        "--wallet-file",
        help="Wallet file path (default: ~/.animica/wallets.json)",
        envvar="ANIMICA_WALLETS_FILE",
    ),
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        "-o",
        help="Output file for balance backup (default: auto-generated)",
    ),
) -> None:
    """
    Export wallet balances to a backup file.
    
    This command queries the current balances for all addresses in your wallet
    file and saves them to a JSON backup. This is useful before resetting a node
    or as a general backup practice.
    
    Examples:
        animica balance export
        animica balance export --network testnet
        animica balance export --output ~/my-balances.json
    """
    # Determine network
    if network:
        net_cfg = load_network_config(network)
    else:
        try:
            from animica.cli.state import get_cli_state
            state = get_cli_state()
            active_network = state.get("active_network")
            if not active_network:
                typer.echo(
                    "Error: No active network set. Use 'animica network set <network>' first "
                    "or specify --network explicitly.",
                    err=True,
                )
                raise typer.Exit(code=1)
            net_cfg = load_network_config(active_network)
        except Exception as e:
            typer.echo(f"Error: Could not determine network: {e}", err=True)
            raise typer.Exit(code=1)
    
    # Determine paths
    data_dir = Path(net_cfg.data_dir).expanduser()
    
    if wallet_file is None:
        wallet_file = Path.home() / ".animica" / "wallets.json"
    
    if rpc_url is None:
        rpc_url = net_cfg.rpc_url
    
    # Export balances
    typer.secho(f"Exporting balances for network: {net_cfg.name}", fg=typer.colors.CYAN)
    typer.echo(f"RPC URL: {rpc_url}")
    typer.echo(f"Wallet file: {wallet_file}")
    
    try:
        backup_file, total, non_zero = export_wallet_balances_sync(
            wallet_path=wallet_file,
            data_dir=data_dir,
            rpc_url=rpc_url,
            timeout=10.0,
            quiet=False,
        )
        
        # Move to custom output location if specified
        if output is not None:
            import shutil
            shutil.move(str(backup_file), str(output))
            backup_file = output
        
        typer.secho(f"\n✓ Export complete!", fg=typer.colors.GREEN, bold=True)
        typer.echo(f"Backup file: {backup_file}")
        typer.echo(f"Total addresses: {total}")
        typer.echo(f"Addresses with balance > 0: {non_zero}")
        
        if non_zero > 0:
            typer.echo(f"\nTo view balances: cat {backup_file}")
    
    except Exception as e:
        typer.secho(f"Error: Failed to export balances: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)


@app.command("show")
def show_backup(
    backup_file: Optional[Path] = typer.Argument(
        None,
        help="Balance backup file to display (default: latest for active network)",
    ),
    network: Optional[str] = typer.Option(
        None,
        "--network",
        help="Network to show backup for (defaults to active network)",
    ),
) -> None:
    """
    Display the contents of a balance backup file.
    
    Shows all addresses and their balances from a previous export.
    
    Examples:
        animica balance show
        animica balance show ~/my-balances.json
        animica balance show --network testnet
    """
    # Determine backup file
    if backup_file is None:
        if network:
            net_cfg = load_network_config(network)
        else:
            try:
                from animica.cli.state import get_cli_state
                state = get_cli_state()
                active_network = state.get("active_network")
                if not active_network:
                    typer.echo(
                        "Error: No active network set. Specify backup file explicitly or use "
                        "'animica network set <network>' first.",
                        err=True,
                    )
                    raise typer.Exit(code=1)
                net_cfg = load_network_config(active_network)
            except Exception as e:
                typer.echo(f"Error: Could not determine network: {e}", err=True)
                raise typer.Exit(code=1)
        
        data_dir = Path(net_cfg.data_dir).expanduser()
        backup_file = _get_balance_backup_path(data_dir)
    
    if not backup_file.exists():
        typer.echo(f"Error: Backup file not found: {backup_file}", err=True)
        raise typer.Exit(code=1)
    
    # Load and display backup
    try:
        data = json.loads(backup_file.read_text())
        
        typer.secho(f"Balance Backup: {backup_file}", fg=typer.colors.CYAN, bold=True)
        typer.echo(f"Exported: {data.get('exported_at', 'unknown')}")
        typer.echo(f"Network data dir: {data.get('data_dir', 'unknown')}")
        typer.echo(f"RPC URL: {data.get('rpc_url', 'unknown')}")
        
        balances = data.get("balances", [])
        non_zero = [b for b in balances if b.get("balance", 0) > 0]
        
        typer.echo(f"\nTotal addresses: {len(balances)}")
        typer.echo(f"Non-zero balances: {len(non_zero)}\n")
        
        if non_zero:
            typer.secho("Addresses with balances:", fg=typer.colors.GREEN)
            for entry in non_zero:
                label = entry.get("label", "unlabeled")
                balance = entry.get("balance", 0)
                address = entry.get("address", "unknown")[:50]
                
                # Format balance in ANM (divide by 1e9)
                balance_anm = balance / 1_000_000_000
                typer.echo(f"  {label:20} {balance_anm:15.9f} ANM  ({address}...)")
        else:
            typer.echo("No addresses with non-zero balances.")
    
    except Exception as e:
        typer.secho(f"Error: Failed to read backup file: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
