"""
Quantum AICF Contribute CLI Commands
=====================================

Commands for contributors to participate in AICF by running quantum/GPU/CPU workloads.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn

from . import aicf_utils

console = Console()

quantum_contribute_app = typer.Typer(
    name="contribute",
    help="Quantum/GPU/CPU contribution commands for AICF",
    no_args_is_help=True,
)


@quantum_contribute_app.command("register")
def register(
    worker_type: Optional[str] = typer.Option(
        None,
        "--type",
        help="Worker type: gpu|cpu|quantum",
    ),
    caps: Optional[str] = typer.Option(
        None,
        "--caps",
        help="Path to capabilities JSON file or inline JSON",
    ),
    address: Optional[str] = typer.Option(
        None,
        "--address",
        help="Worker wallet address (defaults to default wallet)",
    ),
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="Override RPC URL",
    ),
    network: Optional[str] = typer.Option(
        None,
        "--network",
        help="Network to use",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Show what would be registered without actually registering",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
):
    """Register as a quantum/GPU/CPU contributor."""
    # Validate required args early (to stdout so CLI tests can capture them)
    if not worker_type:
        typer.echo("Error: Missing option '--type'")
        raise typer.Exit(1)
    if not caps:
        typer.echo("Error: Missing option '--caps'")
        raise typer.Exit(1)
    
    # Validate worker type
    valid_types = ["gpu", "cpu", "quantum"]
    if worker_type not in valid_types:
        console.print(f"[red]Error: Invalid worker type '{worker_type}'. Must be one of: {', '.join(valid_types)}[/red]")
        raise typer.Exit(1)
    
    # Load capabilities
    try:
        if os.path.isfile(caps):
            with open(caps, 'r') as f:
                capabilities = json.load(f)
        elif os.sep in caps or caps.startswith('~'):
            # Looks like a file path (contains path separator or home dir) but doesn't exist
            typer.echo(f"Error: Capabilities file not found: {caps}")
            raise typer.Exit(1)
        else:
            capabilities = json.loads(caps)
    except FileNotFoundError:
        typer.echo(f"Error: Capabilities file not found: {caps}")
        raise typer.Exit(1)
    except json.JSONDecodeError as e:
        typer.echo(f"Error: Invalid JSON in capabilities: {e}")
        raise typer.Exit(1)
    
    # Validate capabilities structure
    required_fields = ["worker_type", "resources"]
    for field in required_fields:
        if field not in capabilities:
            console.print(f"[red]Error: Missing required field '{field}' in capabilities[/red]")
            raise typer.Exit(1)
    
    if capabilities["worker_type"] != worker_type:
        console.print(f"[yellow]Warning: Capabilities file worker_type ({capabilities['worker_type']}) doesn't match --type ({worker_type})[/yellow]")
    
    # Show registration details
    if dry_run or not json_output:
        console.print(f"[bold]Worker Registration:[/bold]")
        console.print(f"  Type: {worker_type}")
        console.print(f"  Address: {address or '(default wallet)'}")
        console.print(f"\n[bold]Capabilities:[/bold]")
        console.print(f"  {json.dumps(capabilities['resources'], indent=2)}")
    
    if dry_run:
        console.print(f"\n[dim]Dry run mode - not actually registering[/dim]")
        raise typer.Exit(0)
    
    # Register worker
    try:
        result = aicf_utils.rpc_call(
            "aicf.workerRegister",
            params=[{
                "worker_type": worker_type,
                "address": address,
                "capabilities": capabilities,
            }],
            url=rpc_url,
        )
        
        if json_output:
            console.print(aicf_utils.safe_json_encode(result))
        else:
            console.print(f"\n[green]✓ Worker registered successfully![/green]")
            console.print(f"  Worker ID: {result.get('worker_id', 'N/A')}")
            console.print(f"  Status: {result.get('status', 'ACTIVE')}")
            console.print(f"\n[dim]Next: Run jobs with 'animica quantum contribute run --plan <PLAN>'[/dim]")
    
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@quantum_contribute_app.command("run")
def run(
    plan: str = typer.Option(
        ...,
        "--plan",
        help="Built-in plan name or path to custom plan JSON",
    ),
    budget: Optional[int] = typer.Option(
        None,
        "--budget",
        help="Maximum budget in ANM to spend (optional)",
    ),
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="Override RPC URL",
    ),
    network: Optional[str] = typer.Option(
        None,
        "--network",
        help="Network to use",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Show what would be executed without actually running",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
):
    """Run a quantum/GPU/CPU workload and submit proofs."""
    from . import aicf_utils, aicf_plans
    
    # Check if plan is a built-in plan
    job_plan = aicf_plans.get_plan(plan)
    
    if not job_plan:
        # Try to load as custom plan file
        if os.path.isfile(plan):
            try:
                with open(plan, 'r') as f:
                    custom_plan = json.load(f)
                console.print(f"[yellow]Custom plan loading not yet fully implemented.[/yellow]")
                console.print(f"Loaded plan from {plan}")
            except Exception as e:
                console.print(f"[red]Error loading custom plan: {e}[/red]")
                raise typer.Exit(1)
        else:
            console.print(f"[red]Error: Plan '{plan}' not found.[/red]")
            console.print(f"Available built-in plans: {', '.join(aicf_plans.BUILTIN_PLANS.keys())}")
            console.print(f"List all plans: animica aicf jobs plans")
            raise typer.Exit(1)
    
    if dry_run or not json_output:
        console.print(f"[bold]Contributor Run Configuration:[/bold]")
        if job_plan:
            console.print(f"  Plan: {job_plan.name} ({job_plan.category})")
            console.print(f"  Description: {job_plan.description}")
            console.print(f"  Min Budget: {job_plan.min_budget} credits")
            console.print(f"  Est. Duration: {job_plan.estimated_duration}")
        console.print(f"  Budget Limit: {budget if budget else 'Unlimited'} ANM")
    
    if dry_run:
        console.print(f"\n[dim]Dry run mode - not actually running[/dim]")
        raise typer.Exit(0)
    
    # Start worker loop
    console.print(f"\n[bold]Starting contributor worker...[/bold]")
    console.print(f"Press Ctrl+C to stop.\n")
    
    try:
        # This is a placeholder for the actual worker loop
        # In a full implementation, this would:
        # 1. Fetch available jobs matching the plan
        # 2. Execute the workload
        # 3. Generate ProofEnvelope
        # 4. Submit work receipt
        # 5. Track credits earned
        
        console.print("[yellow]Worker loop not yet fully implemented.[/yellow]")
        console.print("This feature will:")
        console.print("  • Poll for available jobs matching your capabilities")
        console.print("  • Execute training/eval/quantum workloads")
        console.print("  • Generate and submit verifiable proofs")
        console.print("  • Track credits earned deterministically")
        console.print(f"\n[dim]For now, use the AICF job submission: animica aicf jobs submit[/dim]")
    
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped worker.[/dim]")
        raise typer.Exit(0)


@quantum_contribute_app.command("status")
def status(
    address: Optional[str] = typer.Argument(
        None,
        help="Worker address (defaults to default wallet)",
    ),
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="Override RPC URL",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
):
    """Show worker registration status and earnings."""
    from . import aicf_utils
    
    try:
        result = aicf_utils.rpc_call(
            "aicf.workerStatus",
            params=[address] if address else [],
            url=rpc_url,
        )
        
        if json_output:
            console.print(aicf_utils.safe_json_encode(result))
        else:
            console.print(f"[bold]Worker Status: {result.get('worker_id', address or 'default')}[/bold]")
            console.print(f"  Status: {result.get('status', 'UNKNOWN')}")
            console.print(f"  Type: {result.get('worker_type', 'N/A')}")
            console.print(f"\n[bold]Current Jobs:[/bold]")
            
            jobs = result.get('current_jobs', [])
            if jobs:
                for job in jobs:
                    console.print(f"  • {job.get('job_id', 'N/A')} - {job.get('status', 'RUNNING')}")
            else:
                console.print(f"  [dim]No active jobs[/dim]")
            
            console.print(f"\n[bold]Credits:[/bold]")
            console.print(f"  Earned: {result.get('credits_earned', 0)} credits")
            console.print(f"  Pending: {result.get('credits_pending', 0)} credits")
            
            if result.get('last_work_height'):
                console.print(f"\n[bold]Last Work:[/bold]")
                console.print(f"  Block Height: {result['last_work_height']}")
    
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@quantum_contribute_app.command("watch")
def watch(
    job_id: str = typer.Argument(..., help="Job ID to watch"),
    interval: int = typer.Option(
        10,
        "--interval",
        help="Polling interval in seconds",
    ),
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="Override RPC URL",
    ),
):
    """Stream job progress and attribution events."""
    from . import aicf_utils
    
    console.print(f"[bold]Job Monitor: {job_id}[/bold]")
    console.print(f"Polling every {interval} seconds. Press Ctrl+C to stop.\n")
    
    try:
        while True:
            try:
                result = aicf_utils.rpc_call(
                    "aicf.getJobStatus",
                    params=[job_id],
                    url=rpc_url,
                )
                
                console.print(f"[dim]{time.strftime('%Y-%m-%d %H:%M:%S')}[/dim]")
                console.print(f"  Status: {result.get('status', 'UNKNOWN')}")
                console.print(f"  Progress: {result.get('progress', 0)}%")
                console.print(f"  Credits Used: {result.get('credits_used', 0)}")
                console.print(f"  Workers: {result.get('workers_active', 0)}")
                
                if result.get('your_contribution'):
                    contrib = result['your_contribution']
                    console.print(f"\n[bold green]Your Contribution:[/bold green]")
                    console.print(f"  Units: {contrib.get('units', 0)}")
                    console.print(f"  Credits Earned: {contrib.get('credits', 0)}")
                
                console.print()
                
                if result.get('status') in ['COMPLETED', 'FAILED', 'CANCELLED']:
                    console.print(f"[{'green' if result['status'] == 'COMPLETED' else 'red'}]Job {result['status']}[/]")
                    break
            
            except Exception as e:
                console.print(f"[red]Error: {e}[/red]\n")
            
            time.sleep(interval)
    
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped monitoring.[/dim]")
        raise typer.Exit(0)


@quantum_contribute_app.command("start")
def start(
    worker_type: str = typer.Option(
        "quantum",
        "--type",
        help="Worker type: gpu|cpu|quantum",
    ),
    address: Optional[str] = typer.Option(
        None,
        "--address",
        help="Worker wallet address (defaults to default wallet)",
    ),
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="Override RPC URL",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
):
    """Start quantum contribution worker."""
    from . import aicf_utils
    
    try:
        result = aicf_utils.rpc_call(
            "aicf.startWorker",
            params=[{
                "worker_type": worker_type,
                "address": address,
            }],
            url=rpc_url,
        )
        
        if json_output:
            console.print(aicf_utils.safe_json_encode(result))
        else:
            console.print(f"[green]✓ Quantum worker started successfully![/green]")
            console.print(f"\n[bold]Worker Details:[/bold]")
            console.print(f"  Worker ID: {result.get('worker_id', 'N/A')}")
            console.print(f"  Type: {worker_type}")
            console.print(f"  Status: {result.get('status', 'ACTIVE')}")
            
            if result.get('listening_for'):
                console.print(f"  Listening For: {result['listening_for']}")
            
            console.print(f"\n[dim]Stop worker: animica quantum contribute stop[/dim]")
    
    except Exception as e:
        if json_output:
            console.print(aicf_utils.safe_json_encode({"error": str(e)}))
        else:
            console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@quantum_contribute_app.command("stop")
def stop(
    address: Optional[str] = typer.Option(
        None,
        "--address",
        help="Worker address (defaults to default wallet)",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Force stop even if jobs are in progress",
    ),
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="Override RPC URL",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
):
    """Stop quantum contribution worker."""
    from . import aicf_utils
    
    try:
        result = aicf_utils.rpc_call(
            "aicf.stopWorker",
            params=[{
                "address": address,
                "force": force,
            }],
            url=rpc_url,
        )
        
        if json_output:
            console.print(aicf_utils.safe_json_encode(result))
        else:
            console.print(f"[green]✓ Quantum worker stopped successfully![/green]")
            console.print(f"\n[bold]Final Status:[/bold]")
            console.print(f"  Worker ID: {result.get('worker_id', 'N/A')}")
            console.print(f"  Status: {result.get('status', 'STOPPED')}")
            
            if result.get('jobs_completed'):
                console.print(f"  Jobs Completed: {result['jobs_completed']}")
            if result.get('credits_earned'):
                console.print(f"  Total Credits Earned: {result['credits_earned']}")
            
            if result.get('warning'):
                console.print(f"\n[yellow]Warning: {result['warning']}[/yellow]")
    
    except Exception as e:
        if json_output:
            console.print(aicf_utils.safe_json_encode({"error": str(e)}))
        else:
            console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


__all__ = ["quantum_contribute_app"]
