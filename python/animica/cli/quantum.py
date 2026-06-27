"""
Quantum CLI commands for Animica.

Provides quantum computation, job management, and contribution commands.
"""

from __future__ import annotations

import json
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from . import aicf_utils
from .quantum_contribute import quantum_contribute_app

console = Console()

app = typer.Typer(
    name="quantum",
    help="Quantum computation and contribution commands",
    no_args_is_help=True,
)

# Add the contribute subcommand
app.add_typer(quantum_contribute_app, name="contribute")

# Quantum Useful Work (QUW): attested-QRNG entropy contribution lane
from .quantum_quw import quw_app
app.add_typer(quw_app, name="quw")

# Create jobs subcommand group
jobs_app = typer.Typer(
    name="jobs",
    help="Quantum job management commands",
    no_args_is_help=True,
)
app.add_typer(jobs_app, name="jobs")


@app.command("status")
def quantum_status(
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="Override RPC URL",
    ),
):
    """Show quantum service status and availability."""
    try:
        # Get quantum service status from RPC
        result = aicf_utils.rpc_call(
            "aicf.getQuantumServiceStatus",
            url=rpc_url,
        )
        
        if json_output:
            console.print(aicf_utils.safe_json_encode(result))
        else:
            status_color = "green" if result.get("available", False) else "red"
            console.print(f"[bold]Quantum Service Status:[/bold]")
            console.print(f"  Status: [{status_color}]{result.get('status', 'UNKNOWN')}[/{status_color}]")
            console.print(f"  Available: [{status_color}]{result.get('available', False)}[/{status_color}]")
            
            if result.get("active_workers"):
                console.print(f"\n[bold]Active Workers:[/bold]")
                console.print(f"  Total: {result['active_workers']}")
            
            if result.get("queue_depth") is not None:
                console.print(f"\n[bold]Queue Status:[/bold]")
                console.print(f"  Pending Jobs: {result['queue_depth']}")
                console.print(f"  Processing Jobs: {result.get('processing_jobs', 0)}")
            
            if result.get("last_job_height"):
                console.print(f"\n[bold]Last Job:[/bold]")
                console.print(f"  Block Height: {result['last_job_height']}")
    
    except Exception as e:
        if json_output:
            console.print(aicf_utils.safe_json_encode({"error": str(e)}))
        else:
            console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@jobs_app.command("list")
def jobs_list(
    limit: int = typer.Option(
        50,
        "--limit",
        help="Maximum number of jobs to list",
    ),
    offset: int = typer.Option(
        0,
        "--offset",
        help="Offset for pagination",
    ),
    status: Optional[str] = typer.Option(
        None,
        "--status",
        help="Filter by status (PENDING, RUNNING, COMPLETED, FAILED)",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="Override RPC URL",
    ),
):
    """List quantum jobs."""
    try:
        params = [{"limit": limit, "offset": offset}]
        if status:
            params[0]["status"] = status
        
        result = aicf_utils.rpc_call(
            "explorer_list_quantum_jobs",
            params=params,
            url=rpc_url,
        )
        
        if json_output:
            console.print(aicf_utils.safe_json_encode(result))
        else:
            if not result:
                console.print("[dim]No quantum jobs found.[/dim]")
                return
            
            table = Table(title="Quantum Jobs")
            table.add_column("Job ID", style="cyan", no_wrap=True)
            table.add_column("Status", style="magenta")
            table.add_column("Type", style="green")
            table.add_column("Budget", justify="right")
            table.add_column("Progress", justify="right")
            
            for job in result:
                job_id = job.get("job_id", "N/A")[:16] + "..."
                job_status = job.get("status", "UNKNOWN")
                job_type = job.get("job_type", "quantum")
                budget = str(job.get("budget", 0))
                progress = f"{job.get('progress', 0)}%"
                
                status_style = ""
                if job_status == "COMPLETED":
                    status_style = "green"
                elif job_status == "FAILED":
                    status_style = "red"
                elif job_status == "RUNNING":
                    status_style = "yellow"
                
                table.add_row(
                    job_id,
                    f"[{status_style}]{job_status}[/{status_style}]" if status_style else job_status,
                    job_type,
                    budget,
                    progress
                )
            
            console.print(table)
            console.print(f"\n[dim]Showing {len(result)} job(s)[/dim]")
    
    except Exception as e:
        if json_output:
            console.print(aicf_utils.safe_json_encode({"error": str(e)}))
        else:
            console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@jobs_app.command("submit")
def jobs_submit(
    problem: str = typer.Option(
        ...,
        "--problem",
        help="Quantum problem specification (JSON string or file path)",
    ),
    budget: int = typer.Option(
        ...,
        "--budget",
        help="AICF credits to allocate for this job",
    ),
    qubits: Optional[int] = typer.Option(
        None,
        "--qubits",
        help="Number of qubits required",
    ),
    shots: Optional[int] = typer.Option(
        None,
        "--shots",
        help="Number of measurement shots",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="Override RPC URL",
    ),
):
    """Submit a quantum computation job."""
    from . import aicf_plans
    import os
    
    try:
        # Parse problem specification
        problem_spec = {}
        if os.path.isfile(problem):
            with open(problem, 'r') as f:
                problem_spec = json.load(f)
        else:
            try:
                problem_spec = json.loads(problem)
            except json.JSONDecodeError:
                console.print(f"[red]Error: Problem must be valid JSON or a file path[/red]")
                raise typer.Exit(1)
        
        # Build job parameters
        job_params = {
            "problem": problem_spec,
            "budget": budget,
        }
        
        if qubits is not None:
            job_params["qubits"] = qubits
        if shots is not None:
            job_params["shots"] = shots
        
        # Submit job via RPC
        result = aicf_utils.rpc_call(
            "aicf.submitQuantumJob",
            params=[job_params],
            url=rpc_url,
        )
        
        if json_output:
            console.print(aicf_utils.safe_json_encode(result))
        else:
            console.print(f"[green]✓ Quantum job submitted successfully![/green]")
            console.print(f"\n[bold]Job Details:[/bold]")
            console.print(f"  Job ID: {result.get('job_id', 'N/A')}")
            console.print(f"  Status: {result.get('status', 'PENDING')}")
            console.print(f"  Budget: {budget} credits")
            
            if result.get('estimated_time'):
                console.print(f"  Estimated Time: {result['estimated_time']}")
            
            console.print(f"\n[dim]Monitor job: animica quantum contribute watch {result.get('job_id', '')}[/dim]")
    
    except FileNotFoundError:
        console.print(f"[red]Error: Problem specification file not found: {problem}[/red]")
        raise typer.Exit(1)
    except Exception as e:
        if json_output:
            console.print(aicf_utils.safe_json_encode({"error": str(e)}))
        else:
            console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@app.command("credits")
def quantum_credits(
    address: str = typer.Argument(..., help="Address to query (hex or bech32)"),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="Override RPC URL",
    ),
):
    """Show quantum contribution credits for an address."""
    try:
        result = aicf_utils.rpc_call(
            "aicf.getQuantumCredits",
            params=[address],
            url=rpc_url,
        )
        
        if json_output:
            console.print(aicf_utils.safe_json_encode(result))
        else:
            console.print(f"[bold]Quantum Credits for {address}:[/bold]")
            console.print(f"  Total Earned: {result.get('total_earned', 0)} credits")
            console.print(f"  Available: {result.get('available', 0)} credits")
            console.print(f"  Spent: {result.get('spent', 0)} credits")
            console.print(f"  Pending: {result.get('pending', 0)} credits")
            
            if result.get('last_contribution_height'):
                console.print(f"\n[bold]Last Contribution:[/bold]")
                console.print(f"  Block Height: {result['last_contribution_height']}")
                console.print(f"  Credits Earned: {result.get('last_contribution_credits', 0)}")
            
            if result.get('total_jobs_completed'):
                console.print(f"\n[bold]Statistics:[/bold]")
                console.print(f"  Jobs Completed: {result['total_jobs_completed']}")
                console.print(f"  Total Computation Time: {result.get('total_computation_time', 0)} seconds")
    
    except Exception as e:
        if json_output:
            console.print(aicf_utils.safe_json_encode({"error": str(e)}))
        else:
            console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


__all__ = ["app"]
