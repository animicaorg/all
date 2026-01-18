"""CLI commands for exporting and managing wallet balances."""

from __future__ import annotations

import json
import os
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
        # Use $HOME environment variable if available, falling back to Path.home()
        # This ensures correct behavior in Docker where HOME=/data but passwd says /root
        home = os.environ.get("HOME")
        wallet_file = Path(home) / ".animica" / "wallets.json" if home else Path.home() / ".animica" / "wallets.json"
    
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


@app.command("restore")
def restore_balances(
    network: Optional[str] = typer.Option(
        None,
        "--network",
        help="Network to restore balances to (defaults to active network)",
    ),
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="RPC endpoint URL (default: network default)",
        envvar="ANIMICA_RPC_URL",
    ),
    backup_file: Optional[Path] = typer.Option(
        None,
        "--backup-file",
        "-f",
        help="Balance backup file to restore from (default: auto-detect)",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Skip confirmation prompt",
    ),
) -> None:
    """
    Restore balances from a backup file after node reset.
    
    This command reads a balance backup file (created during node reset) and
    restores all balances to their pre-reset values using the admin.setBalance
    RPC method.
    
    ⚠️ REQUIREMENTS:
    - The node must be running
    - Admin RPC must be enabled (ANIMICA_ADMIN_RPC_ENABLED=1)
    - This should only be used in dev/test environments
    
    Examples:
        # Restore from automatic backup (created during reset)
        animica balance restore
        
        # Restore from specific backup file
        animica balance restore --backup-file ~/my-balances.json
        
        # Restore without confirmation
        animica balance restore --yes
        
    To enable admin RPC when starting node:
        ANIMICA_ADMIN_RPC_ENABLED=1 animica node up
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
    
    if backup_file is None:
        backup_file = _get_balance_backup_path(data_dir)
    
    if not backup_file.exists():
        typer.secho(
            f"Error: Backup file not found: {backup_file}",
            fg=typer.colors.RED,
            err=True
        )
        typer.echo("\nTo create a backup before reset:")
        typer.echo("  animica balance export")
        raise typer.Exit(code=1)
    
    if rpc_url is None:
        rpc_url = net_cfg.rpc_url
    
    # Load backup to show what will be restored
    try:
        data = json.loads(backup_file.read_text())
        balances = data.get("balances", [])
        non_zero = [b for b in balances if b.get("balance", 0) > 0]
    except Exception as e:
        typer.secho(f"Error: Failed to load backup file: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    
    if not non_zero:
        typer.echo("No balances to restore (all balances are zero).")
        raise typer.Exit(code=0)
    
    # Show what will be restored
    typer.secho(f"\nRestore plan for network: {net_cfg.name}", fg=typer.colors.CYAN, bold=True)
    typer.echo(f"Backup file: {backup_file}")
    typer.echo(f"RPC URL: {rpc_url}")
    typer.echo(f"\nAddresses to restore: {len(non_zero)}")
    
    total_balance = sum(b.get("balance", 0) for b in non_zero)
    total_balance_anm = total_balance / 1_000_000_000
    typer.echo(f"Total balance: {total_balance_anm:.9f} ANM\n")
    
    # Show preview of addresses (max 10)
    preview_count = min(10, len(non_zero))
    typer.secho(f"Preview (showing {preview_count} of {len(non_zero)}):", fg=typer.colors.YELLOW)
    for entry in non_zero[:preview_count]:
        label = entry.get("label", "unlabeled")
        balance = entry.get("balance", 0)
        balance_anm = balance / 1_000_000_000
        typer.echo(f"  {label:20} {balance_anm:15.9f} ANM")
    
    if len(non_zero) > preview_count:
        typer.echo(f"  ... and {len(non_zero) - preview_count} more")
    
    # Warning about admin RPC
    typer.secho(
        "\n⚠️  WARNING: This requires admin RPC to be enabled!",
        fg=typer.colors.YELLOW,
        bold=True
    )
    typer.echo("If the node was not started with ANIMICA_ADMIN_RPC_ENABLED=1,")
    typer.echo("this command will fail. Admin RPC should only be used in dev/test.")
    
    # Confirmation
    if not yes:
        confirm = typer.confirm("\nProceed with balance restoration?")
        if not confirm:
            typer.echo("Restoration cancelled.")
            raise typer.Exit(code=0)
    
    # Restore balances
    typer.secho("\nRestoring balances...", fg=typer.colors.CYAN)
    
    try:
        from animica.cli.wallet_balances import restore_wallet_balances_sync
        
        restored, failed = restore_wallet_balances_sync(
            data_dir=data_dir,
            rpc_url=rpc_url,
            backup_file=backup_file,
            timeout=10.0,
            quiet=False,
        )
        
        # Show results
        typer.secho(f"\n✓ Restore complete!", fg=typer.colors.GREEN, bold=True)
        typer.echo(f"Successfully restored: {restored} addresses")
        typer.echo(f"Failed: {failed} addresses")
        
        if failed > 0:
            typer.secho(
                "\n⚠️  Some addresses failed to restore. Check the output above for details.",
                fg=typer.colors.YELLOW
            )
            raise typer.Exit(code=1)
    
    except RuntimeError as e:
        error_str = str(e)
        if "disabled" in error_str.lower() or "not enabled" in error_str.lower():
            typer.secho(f"\n✗ Error: {e}", fg=typer.colors.RED, err=True)
            typer.echo("\nTo enable admin RPC, restart the node with:")
            typer.echo("  animica node down")
            typer.echo("  ANIMICA_ADMIN_RPC_ENABLED=1 animica node up")
            typer.echo("  animica balance restore")
        else:
            typer.secho(f"\n✗ Error: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    
    except Exception as e:
        typer.secho(f"\n✗ Error: Failed to restore balances: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
