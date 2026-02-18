"""
AICF Protocol CLI Commands
===========================

Additional CLI commands for interacting with the AICF GPU redistribution protocol.

To be added to python/animica/cli/ena.py in the aicf_app group.
"""

# Add these imports at the top of ena.py:
# import tempfile
# from pathlib import Path

# Add these command implementations to the aicf_app group:

@aicf_app.command("protocol-status")
def protocol_status(
    db_path: Optional[str] = typer.Option(
        None,
        "--db",
        help="Path to AICF protocol database",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
):
    """Show AICF protocol status and configuration."""
    try:
        from aicf.protocol.state import ProtocolState
        from aicf.protocol.rpc import ProtocolRPCMethods
        from aicf.protocol.economics import EpochAccountant
    except ImportError:
        console.print("[red]Error: AICF protocol module not available[/red]")
        console.print("Install with: pip install -e .[aicf]")
        raise typer.Exit(1)
    
    # Use default path if not specified
    db = db_path or os.path.expanduser("~/.animica/aicf_protocol.db")
    
    if not os.path.exists(db):
        console.print(f"[red]Error: Protocol database not found: {db}[/red]")
        console.print("Hint: The protocol may not be initialized yet.")
        raise typer.Exit(1)
    
    try:
        state = ProtocolState(db)
        accountant = EpochAccountant(state)
        methods = ProtocolRPCMethods(state, accountant)
        
        # Get status
        rpc_methods = methods.make_methods()
        status = rpc_methods["aicf.protocol.getStatus"]()
        
        if json_output:
            console.print(json.dumps(status, indent=2))
        else:
            console.print("[bold]AICF Protocol Status:[/bold]")
            console.print(f"  Current Epoch: {status['currentEpoch']}")
            console.print(f"  Active Workers: {status['totalWorkers']}")
            
            console.print(f"\n[bold]Parameters:[/bold]")
            params = status['params']
            console.print(f"  Epoch Length: {params['epochLengthBlocks']} blocks")
            console.print(f"  Challenge Window: {params['challengeWindowBlocks']} blocks")
            console.print(f"  Min Stake: {_format_amount(int(params['minStake']))} ANM")
            console.print(f"  Max Workers: {params['maxWorkers']}")
            
            console.print(f"\n[bold]Reward Split:[/bold]")
            split = params['rewardSplit']
            console.print(f"  GPU Workers: {split['gpuWorkersBp']} bp ({split['gpuWorkersBp']/100}%)")
            console.print(f"  Treasury: {split['treasuryBp']} bp ({split['treasuryBp']/100}%)")
            console.print(f"  Dev Fund: {split['devBp']} bp ({split['devBp']/100}%)")
            console.print(f"  Burn: {split['burnBp']} bp ({split['burnBp']/100}%)")
    
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@aicf_app.command("register-worker")
def register_worker(
    address: str = typer.Argument(..., help="Worker payout address"),
    name: Optional[str] = typer.Option(
        None,
        "--name",
        help="Display name for worker",
    ),
    stake: Optional[str] = typer.Option(
        None,
        "--stake",
        help="Stake amount in ANM",
    ),
    stake_tx: Optional[str] = typer.Option(
        None,
        "--stake-tx",
        help="Stake transaction hash",
    ),
    region: Optional[str] = typer.Option(
        None,
        "--region",
        help="Region/locale tag",
    ),
    db_path: Optional[str] = typer.Option(
        None,
        "--db",
        help="Path to AICF protocol database",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
):
    """Register as a GPU worker."""
    try:
        from aicf.protocol.state import ProtocolState
        from aicf.protocol.rpc import ProtocolRPCMethods
        from aicf.protocol.economics import EpochAccountant
    except ImportError:
        console.print("[red]Error: AICF protocol module not available[/red]")
        raise typer.Exit(1)
    
    # Use default path if not specified
    db = db_path or os.path.expanduser("~/.animica/aicf_protocol.db")
    
    # Parse stake amount
    stake_amount = 0
    if stake:
        stake_amount = _parse_amount(stake)
    
    try:
        state = ProtocolState(db)
        accountant = EpochAccountant(state)
        methods = ProtocolRPCMethods(state, accountant)
        
        # Register worker
        rpc_methods = methods.make_methods()
        result = rpc_methods["aicf.protocol.registerWorker"](
            address=address,
            displayName=name,
            stakeAmount=stake_amount,
            stakeTxHash=stake_tx,
            region=region,
        )
        
        if json_output:
            console.print(json.dumps(result, indent=2))
        else:
            console.print("[bold green]✓ Worker registered![/bold green]")
            console.print(f"  Worker ID: {result['workerId']}")
            console.print(f"  Address: {result['address']}")
            console.print(f"  Status: {result['status']}")
            if stake_amount > 0:
                console.print(f"  Stake: {_format_amount(stake_amount)} ANM")
    
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@aicf_app.command("list-workers")
def list_workers(
    status: Optional[str] = typer.Option(
        None,
        "--status",
        help="Filter by status (ACTIVE, INACTIVE, JAILED, BANNED)",
    ),
    limit: int = typer.Option(
        50,
        "--limit",
        help="Maximum number of workers to show",
    ),
    db_path: Optional[str] = typer.Option(
        None,
        "--db",
        help="Path to AICF protocol database",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
):
    """List registered GPU workers."""
    try:
        from aicf.protocol.state import ProtocolState
        from aicf.protocol.rpc import ProtocolRPCMethods
        from aicf.protocol.economics import EpochAccountant
    except ImportError:
        console.print("[red]Error: AICF protocol module not available[/red]")
        raise typer.Exit(1)
    
    db = db_path or os.path.expanduser("~/.animica/aicf_protocol.db")
    
    try:
        state = ProtocolState(db)
        accountant = EpochAccountant(state)
        methods = ProtocolRPCMethods(state, accountant)
        
        # List workers
        rpc_methods = methods.make_methods()
        result = rpc_methods["aicf.protocol.listWorkers"](
            status=status,
            limit=limit,
        )
        
        if json_output:
            console.print(json.dumps(result, indent=2))
        else:
            workers = result['items']
            if not workers:
                console.print("[yellow]No workers found[/yellow]")
                return
            
            # Display as table
            table = Table(title="GPU Workers")
            table.add_column("Worker ID", style="cyan")
            table.add_column("Address", style="white")
            table.add_column("Name", style="green")
            table.add_column("Stake", style="yellow")
            table.add_column("Status", style="magenta")
            table.add_column("Region", style="blue")
            
            for w in workers:
                table.add_row(
                    w.get("workerId", ""),
                    w.get("address", "")[:20] + "...",
                    w.get("displayName") or "-",
                    _format_amount(w.get("stakeAmount", 0)) + " ANM",
                    w.get("status", ""),
                    w.get("region") or "-",
                )
            
            console.print(table)
            console.print(f"\nShowing {len(workers)} workers")
    
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@aicf_app.command("claim-rewards")
def claim_rewards(
    worker_id: str = typer.Argument(..., help="Worker ID"),
    epoch: int = typer.Argument(..., help="Epoch ID to claim"),
    db_path: Optional[str] = typer.Option(
        None,
        "--db",
        help="Path to AICF protocol database",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
):
    """Claim rewards for a completed epoch."""
    try:
        from aicf.protocol.state import ProtocolState
        from aicf.protocol.rpc import ProtocolRPCMethods
        from aicf.protocol.economics import EpochAccountant
    except ImportError:
        console.print("[red]Error: AICF protocol module not available[/red]")
        raise typer.Exit(1)
    
    db = db_path or os.path.expanduser("~/.animica/aicf_protocol.db")
    
    try:
        state = ProtocolState(db)
        accountant = EpochAccountant(state)
        methods = ProtocolRPCMethods(state, accountant)
        
        # Create claim
        rpc_methods = methods.make_methods()
        result = rpc_methods["aicf.protocol.claim"](
            epochId=epoch,
            workerId=worker_id,
        )
        
        if json_output:
            console.print(json.dumps(result, indent=2))
        else:
            if result.get("claimed"):
                console.print("[bold green]✓ Claim created![/bold green]")
                console.print(f"  Claim ID: {result['claimId']}")
                console.print(f"  Epoch: {result['epochId']}")
                console.print(f"  Worker: {result['workerId']}")
                console.print(f"  Amount: {_format_amount(int(result['amount']))} ANM")
                console.print(f"  Status: {result['status']}")
                console.print("\n[dim]Note: Claim is pending payout. The amount will be transferred to your address once processed.[/dim]")
            else:
                console.print("[yellow]No rewards to claim[/yellow]")
                console.print(f"  Reason: {result.get('reason', 'Unknown')}")
    
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@aicf_app.command("epoch-info")
def epoch_info(
    epoch: int = typer.Argument(..., help="Epoch ID"),
    db_path: Optional[str] = typer.Option(
        None,
        "--db",
        help="Path to AICF protocol database",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
):
    """Show epoch information and statistics."""
    try:
        from aicf.protocol.state import ProtocolState
        from aicf.protocol.rpc import ProtocolRPCMethods
        from aicf.protocol.economics import EpochAccountant
    except ImportError:
        console.print("[red]Error: AICF protocol module not available[/red]")
        raise typer.Exit(1)
    
    db = db_path or os.path.expanduser("~/.animica/aicf_protocol.db")
    
    try:
        state = ProtocolState(db)
        accountant = EpochAccountant(state)
        methods = ProtocolRPCMethods(state, accountant)
        
        # Get epoch info
        rpc_methods = methods.make_methods()
        result = rpc_methods["aicf.protocol.getEpoch"](epochId=epoch)
        
        if json_output:
            console.print(json.dumps(result, indent=2))
        else:
            console.print(f"[bold]Epoch {result['epoch_id']} Information:[/bold]")
            console.print(f"  Start Height: {result['start_height']}")
            if result.get('end_height'):
                console.print(f"  End Height: {result['end_height']}")
            console.print(f"  Finalized: {'Yes' if result.get('finalized') else 'No'}")
            
            console.print(f"\n[bold]Inflows:[/bold]")
            console.print(f"  Total: {_format_amount(int(result['inflow_total']))} ANM")
            console.print(f"  From ENA: {_format_amount(int(result['inflow_ena']))} ANM")
            console.print(f"  Other: {_format_amount(int(result['inflow_other']))} ANM")
            
            console.print(f"\n[bold]Distribution:[/bold]")
            console.print(f"  For Workers: {_format_amount(int(result['inflow_for_workers']))} ANM")
            console.print(f"  For Treasury: {_format_amount(int(result['inflow_for_treasury']))} ANM")
            console.print(f"  For Dev: {_format_amount(int(result['inflow_for_dev']))} ANM")
            console.print(f"  For Burn: {_format_amount(int(result['inflow_for_burn']))} ANM")
            
            console.print(f"\n[bold]Credits:[/bold]")
            console.print(f"  Total Credits: {result['total_credits']}")
            console.print(f"  Workers: {result['worker_count']}")
    
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)
