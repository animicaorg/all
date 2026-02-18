"""
AICF Credit Flow CLI Commands
==============================

Commands for managing AICF credits minted from mining rewards.
"""

from __future__ import annotations

import json
import os
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

console = Console()

aicf_app = typer.Typer(
    name="aicf",
    help="AICF credit and job management commands",
    no_args_is_help=True,
)


def _format_amount(amount_nano: int) -> str:
    """Format nANM amount as ANM with proper decimal places."""
    anm = amount_nano / 1_000_000_000
    return f"{anm:,.9f}".rstrip('0').rstrip('.')


def _get_rpc_url() -> str:
    """Get RPC URL from environment or default."""
    return os.getenv("ANIMICA_RPC_URL", "http://127.0.0.1:8545")


def _rpc_call(method: str, params: Optional[list] = None) -> any:
    """Make a JSON-RPC call."""
    import requests
    
    url = _get_rpc_url()
    payload = {
        "jsonrpc": "2.0",
        "method": method,
        "params": params or [],
        "id": 1,
    }
    
    try:
        resp = requests.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        
        if "error" in data:
            error = data["error"]
            raise Exception(f"RPC error: {error.get('message', str(error))}")
        
        return data.get("result")
    except requests.exceptions.RequestException as e:
        raise Exception(f"RPC request failed: {e}")


@aicf_app.command("status")
def aicf_status(
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
):
    """Show global AICF credit totals and statistics."""
    try:
        result = _rpc_call("state.getAicfSummary")
        
        if json_output:
            console.print(json.dumps(result, indent=2))
        else:
            console.print("[bold]AICF Credit Summary:[/bold]")
            console.print(f"  Total Balance: {_format_amount(int(result['balance_total']))} credits")
            console.print(f"  Total Minted: {_format_amount(int(result['minted_total']))} credits")
            console.print(f"  Total Spent: {_format_amount(int(result['spent_total']))} credits")
            
            if result.get('last_update_height'):
                console.print(f"\n[bold]Last Update:[/bold]")
                console.print(f"  Block Height: {result['last_update_height']}")
                if result.get('last_update_hash'):
                    console.print(f"  Block Hash: {result['last_update_hash']}")
            
            console.print(f"\n[dim]Credits are minted from block rewards and can fund AI/Quantum training jobs.[/dim]")
    
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@aicf_app.command("miner-credits")
def miner_credits(
    address: str = typer.Argument(..., help="Miner address (hex or bech32)"),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
):
    """Show AICF credit balance for a specific miner."""
    try:
        result = _rpc_call("state.getAicfMinerCredits", [address])
        
        if json_output:
            console.print(json.dumps(result, indent=2))
        else:
            console.print(f"[bold]Miner Credits: {result['miner_address']}[/bold]")
            console.print(f"  Current Balance: {_format_amount(int(result['balance']))} credits")
            console.print(f"  Lifetime Earned: {_format_amount(int(result['lifetime_earned']))} credits")
            console.print(f"  Lifetime Spent: {_format_amount(int(result['lifetime_spent']))} credits")
            
            if result.get('last_mint_height'):
                console.print(f"\n[bold]Last Mint:[/bold]")
                console.print(f"  Block Height: {result['last_mint_height']}")
                if result.get('last_mint_hash'):
                    console.print(f"  Block Hash: {result['last_mint_hash'][:18]}...")
            
            if int(result['balance']) > 0:
                console.print(f"\n[dim]Use credits to fund training jobs with: animica aicf jobs submit[/dim]")
    
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


# Job marketplace commands (placeholder for future implementation)

jobs_app = typer.Typer(
    name="jobs",
    help="AI/Quantum job marketplace commands",
    no_args_is_help=True,
)

aicf_app.add_typer(jobs_app, name="jobs")


@jobs_app.command("list")
def jobs_list(
    status: Optional[str] = typer.Option(
        None,
        "--status",
        help="Filter by status (OPEN, ASSIGNED, COMPLETED, EXPIRED)",
    ),
    job_type: Optional[str] = typer.Option(
        None,
        "--type",
        help="Filter by type (TRAINING, EVAL, FINETUNE)",
    ),
    limit: int = typer.Option(
        20,
        "--limit",
        help="Maximum number of jobs to show",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
):
    """List AICF-funded training/eval jobs."""
    console.print("[yellow]Job marketplace not yet implemented.[/yellow]")
    console.print("This feature will allow:")
    console.print("  • Submitting AI/Quantum training jobs funded by AICF credits")
    console.print("  • Workers claiming and executing jobs")
    console.print("  • Verification and payout of completed work")
    raise typer.Exit(0)


@jobs_app.command("submit")
def jobs_submit(
    plan: str = typer.Option(..., "--plan", help="Path to training plan JSON or DA hash"),
    budget: int = typer.Option(..., "--budget", help="AICF credits to allocate"),
    job_type: str = typer.Option("train", "--type", help="Job type: train|eval|distill"),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
):
    """Submit a new AICF-funded training/eval job."""
    console.print("[yellow]Job submission not yet implemented.[/yellow]")
    console.print(f"Would submit {job_type} job with {budget} credits")
    console.print(f"Plan: {plan}")
    raise typer.Exit(0)


@jobs_app.command("watch")
def jobs_watch(
    job_id: str = typer.Argument(..., help="Job ID to watch"),
):
    """Watch progress of a specific job."""
    console.print(f"[yellow]Job watching not yet implemented for job: {job_id}[/yellow]")
    raise typer.Exit(0)


__all__ = ["aicf_app"]
