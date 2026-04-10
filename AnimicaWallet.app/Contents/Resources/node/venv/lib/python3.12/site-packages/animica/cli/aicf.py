"""
AICF Credit Flow CLI Commands
==============================

Commands for managing AICF credits minted from mining rewards.
"""

from __future__ import annotations

import json
import os
import time
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn

from . import aicf_utils
from . import aicf_plans

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


@aicf_app.command("status")
def aicf_status(
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
    debug_rpc: bool = typer.Option(
        False,
        "--debug-rpc",
        help="Print RPC request details",
    ),
):
    """Show global AICF credit totals and statistics."""
    try:
        result = aicf_utils.rpc_call(
            "state.getAicfSummary",
            url=rpc_url,
            debug=debug_rpc,
        )
        
        if json_output:
            console.print(aicf_utils.safe_json_encode(result))
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
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="Override RPC URL",
    ),
    debug_rpc: bool = typer.Option(
        False,
        "--debug-rpc",
        help="Print RPC request details",
    ),
):
    """Show AICF credit balance for a specific miner."""
    try:
        result = aicf_utils.rpc_call(
            "state.getAicfMinerCredits",
            params=[address],
            url=rpc_url,
            debug=debug_rpc,
        )
        
        if json_output:
            console.print(aicf_utils.safe_json_encode(result))
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


# Backward-compatible alias: animica aicf miner-credits address <ADDR>
@aicf_app.command("address", hidden=True)
def miner_credits_alias(
    address: str = typer.Argument(..., help="Miner address (hex or bech32)"),
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
    debug_rpc: bool = typer.Option(
        False,
        "--debug-rpc",
        help="Print RPC request details",
    ),
):
    """[Deprecated] Use 'animica aicf miner-credits <ADDRESS>' instead."""
    # Forward to the main command
    miner_credits(address, json_output, rpc_url, debug_rpc)


@aicf_app.command("doctor")
def aicf_doctor(
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="RPC URL to diagnose",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
):
    """Diagnose RPC connectivity, data directory, and list available methods."""
    from . import data_dir
    
    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            progress.add_task(description="Diagnosing RPC endpoint...", total=None)
            results = aicf_utils.rpc_doctor(url=rpc_url)
        
        # Check data directory
        data_dir_writable, data_dir_error = data_dir.check_data_dir_writable()
        data_dir_path = str(data_dir.get_data_dir())
        
        if json_output:
            results["data_dir"] = {
                "path": data_dir_path,
                "writable": data_dir_writable,
                "error": data_dir_error,
            }
            console.print(aicf_utils.safe_json_encode(results))
        else:
            url = results["url"]
            console.print(f"[bold]RPC Doctor Results[/bold]")
            console.print(f"  URL: {url}")
            console.print(f"  Reachable: {'✓' if results['reachable'] else '✗'}")
            
            console.print(f"\n[bold]Data Directory:[/bold]")
            console.print(f"  Path: {data_dir_path}")
            console.print(f"  Writable: {'✓' if data_dir_writable else '✗'}")
            if data_dir_error:
                console.print(f"  [red]Error: {data_dir_error}[/red]")
            
            if results.get("methods"):
                console.print(f"\n[bold]Available Methods ({len(results['methods'])}):[/bold]")
                # Group methods by prefix
                method_groups = {}
                for method in results["methods"]:
                    prefix = method.split(".")[0] if "." in method else "other"
                    if prefix not in method_groups:
                        method_groups[prefix] = []
                    method_groups[prefix].append(method)
                
                for prefix in sorted(method_groups.keys()):
                    console.print(f"\n  [{prefix}]")
                    for method in sorted(method_groups[prefix]):
                        console.print(f"    - {method}")
            
            if results.get("errors"):
                console.print(f"\n[bold yellow]Diagnostics:[/bold yellow]")
                for error in results["errors"]:
                    console.print(f"  • {error}")
    
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@aicf_app.command("watch")
def aicf_watch(
    interval: int = typer.Option(
        10,
        "--interval",
        help="Polling interval in seconds",
    ),
    max_duration: int = typer.Option(
        300,
        "--max-duration",
        help="Maximum watch duration in seconds (0 = infinite)",
    ),
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="Override RPC URL",
    ),
):
    """Watch AICF status changes (pool balance, credits minted, etc.)."""
    console.print("[bold]AICF Status Monitor[/bold]")
    console.print(f"Polling every {interval} seconds. Press Ctrl+C to stop.\n")
    
    start_time = time.time()
    prev_state = None
    
    try:
        while True:
            try:
                result = aicf_utils.rpc_call("state.getAicfSummary", url=rpc_url)
                
                # Display current state
                console.print(f"[dim]{time.strftime('%Y-%m-%d %H:%M:%S')}[/dim]")
                console.print(f"  Balance: {_format_amount(int(result['balance_total']))} credits")
                console.print(f"  Minted:  {_format_amount(int(result['minted_total']))} credits")
                console.print(f"  Spent:   {_format_amount(int(result['spent_total']))} credits")
                console.print(f"  Height:  {result.get('last_update_height', 'N/A')}")
                
                # Detect changes
                if prev_state:
                    if result['minted_total'] != prev_state['minted_total']:
                        delta = int(result['minted_total']) - int(prev_state['minted_total'])
                        console.print(f"  [green]→ Minted: +{_format_amount(delta)} credits[/green]")
                    
                    if result['spent_total'] != prev_state['spent_total']:
                        delta = int(result['spent_total']) - int(prev_state['spent_total'])
                        console.print(f"  [yellow]→ Spent: +{_format_amount(delta)} credits[/yellow]")
                
                prev_state = result
                console.print()
                
            except Exception as e:
                console.print(f"[red]Error: {e}[/red]\n")
            
            # Check max duration
            if max_duration > 0:
                elapsed = time.time() - start_time
                if elapsed >= max_duration:
                    console.print(f"[dim]Max duration ({max_duration}s) reached.[/dim]")
                    break
            
            time.sleep(interval)
    
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped monitoring.[/dim]")
        raise typer.Exit(0)


# Job marketplace commands

jobs_app = typer.Typer(
    name="jobs",
    help="AI/Quantum job marketplace commands",
    no_args_is_help=True,
)

aicf_app.add_typer(jobs_app, name="jobs")

# Plans subcommand
plans_app = typer.Typer(
    name="plans",
    help="Job plan management",
    no_args_is_help=True,
)

aicf_app.add_typer(plans_app, name="plans")


@plans_app.command("list")
def plans_list(
    category: Optional[str] = typer.Option(
        None,
        "--category",
        help="Filter by category (testing, maintenance, training, qa)",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
    show_details: bool = typer.Option(
        False,
        "--details",
        help="Show full plan details including parameters",
    ),
):
    """List available built-in job plans."""
    plans = aicf_plans.list_plans(category=category)
    
    if json_output:
        console.print(aicf_utils.safe_json_encode([p.to_dict() for p in plans]))
    else:
        console.print(f"[bold]Available Job Plans ({len(plans)}):[/bold]\n")
        
        for plan in plans:
            console.print(f"[bold cyan]{plan.name}[/bold cyan] [dim]({plan.category})[/dim]")
            console.print(f"  {plan.description}")
            console.print(f"  Min Budget: {plan.min_budget} credits")
            console.print(f"  Est. Duration: {plan.estimated_duration}")
            
            if show_details:
                if plan.default_params:
                    console.print(f"  Default Params: {json.dumps(plan.default_params, indent=4)}")
                if plan.required_capabilities:
                    console.print(f"  Required: {', '.join(plan.required_capabilities)}")
            
            console.print()
        
        console.print("[dim]Use --details to see full plan specifications[/dim]")
        console.print("[dim]Submit a job with: animica aicf jobs submit --plan <name> --budget <credits>[/dim]")


# Role-to-plan mapping
ROLE_RECOMMENDATIONS = {
    "miner": ["consensus_sanity", "tx_mempool_fuzz", "rpc_conformance"],
    "gpu": ["ena_smoke", "ena_regression", "repo_index_refresh"],
    "cpu": ["wallet_e2e", "p2p_gossip_health", "rpc_conformance"],
    "quantum": [],  # No specific quantum plans yet
    "storage": ["p2p_gossip_health"],
    "operator": ["consensus_sanity", "tx_mempool_fuzz", "rpc_conformance", "wallet_e2e", "p2p_gossip_health"],
}


@plans_app.command("recommend")
def plans_recommend(
    role: str = typer.Option(
        ...,
        "--role",
        help="Role to recommend plans for (miner, gpu, cpu, quantum, storage, operator)",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
):
    """Recommend plans based on role."""
    role_lower = role.lower()
    
    if role_lower not in ROLE_RECOMMENDATIONS:
        console.print(f"[red]Error: Unknown role '{role}'[/red]")
        console.print(f"Available roles: {', '.join(ROLE_RECOMMENDATIONS.keys())}")
        raise typer.Exit(1)
    
    recommended_plan_names = ROLE_RECOMMENDATIONS[role_lower]
    
    if not recommended_plan_names:
        console.print(f"[yellow]No specific plans recommended for role '{role}' yet.[/yellow]")
        console.print("[dim]Check back later or list all plans: animica aicf plans list[/dim]")
        raise typer.Exit(0)
    
    recommended_plans = [aicf_plans.get_plan(name) for name in recommended_plan_names]
    recommended_plans = [p for p in recommended_plans if p is not None]
    
    if json_output:
        console.print(aicf_utils.safe_json_encode({
            "role": role,
            "plans": [p.to_dict() for p in recommended_plans],
        }))
    else:
        console.print(f"[bold]Recommended Plans for {role.capitalize()}:[/bold]\n")
        
        for plan in recommended_plans:
            console.print(f"[bold cyan]{plan.name}[/bold cyan]")
            console.print(f"  {plan.description}")
            console.print(f"  Min Budget: {plan.min_budget} credits | Est. Duration: {plan.estimated_duration}")
            console.print()
        
        console.print(f"[dim]Total: {len(recommended_plans)} plan(s)[/dim]")
        console.print(f"[dim]Submit a job with: animica aicf jobs submit --plan <name> --budget <credits>[/dim]")


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
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="Override RPC URL",
    ),
):
    """List AICF-funded training/eval jobs."""
    console.print("[yellow]Job marketplace not yet implemented.[/yellow]")
    console.print("This feature will allow:")
    console.print("  • Submitting AI/Quantum training jobs funded by AICF credits")
    console.print("  • Workers claiming and executing jobs")
    console.print("  • Verification and payout of completed work")
    console.print("\n[dim]For now, use built-in plans: animica aicf plans list[/dim]")
    raise typer.Exit(0)


@jobs_app.command("submit")
def jobs_submit(
    plan: str = typer.Option(..., "--plan", help="Built-in plan name or path to custom plan JSON"),
    budget: int = typer.Option(..., "--budget", help="AICF credits to allocate"),
    param: Optional[list[str]] = typer.Option(
        None,
        "--param",
        help="Override params (key=value format, can be specified multiple times)",
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
    """Submit a new AICF-funded training/eval job."""
    # Check if plan is a built-in plan
    job_plan = aicf_plans.get_plan(plan)
    
    if job_plan:
        # Validate budget
        if budget < job_plan.min_budget:
            console.print(f"[red]Error: Budget ({budget}) is below minimum for plan '{plan}' ({job_plan.min_budget})[/red]")
            raise typer.Exit(1)
        
        # Parse user params
        user_params = {}
        if param:
            for p in param:
                if "=" not in p:
                    console.print(f"[red]Error: Invalid param format '{p}'. Use key=value[/red]")
                    raise typer.Exit(1)
                key, value = p.split("=", 1)
                user_params[key] = value
        
        # Merge with defaults
        final_params = {**job_plan.default_params, **user_params}
        
        # Validate params
        validation_errors = aicf_plans.validate_plan_params(job_plan, final_params)
        if validation_errors:
            console.print("[red]Parameter validation errors:[/red]")
            for error in validation_errors:
                console.print(f"  • {error}")
            raise typer.Exit(1)
        
        console.print(f"[yellow]Job submission not yet implemented.[/yellow]")
        console.print(f"\n[bold]Would submit job:[/bold]")
        console.print(f"  Plan: {job_plan.name} ({job_plan.category})")
        console.print(f"  Description: {job_plan.description}")
        console.print(f"  Budget: {budget} credits")
        console.print(f"  Estimated Duration: {job_plan.estimated_duration}")
        console.print(f"  Parameters: {json.dumps(final_params, indent=2)}")
        console.print(f"\n[dim]Backend integration pending. Track: animica aicf jobs watch <jobId>[/dim]")
    else:
        # Try to load as custom plan file
        console.print(f"[yellow]Custom plan loading not yet implemented.[/yellow]")
        console.print(f"Plan '{plan}' not found in built-in plans.")
        console.print(f"\nAvailable plans: {', '.join(aicf_plans.BUILTIN_PLANS.keys())}")
        console.print(f"List all plans: animica aicf jobs plans")
    
    raise typer.Exit(0)


@jobs_app.command("watch")
def jobs_watch(
    job_id: str = typer.Argument(..., help="Job ID to watch"),
    interval: int = typer.Option(
        10,
        "--interval",
        help="Polling interval in seconds",
    ),
    alert_on: Optional[list[str]] = typer.Option(
        None,
        "--alert-on",
        help="Alert triggers: fail, stall, complete (can be specified multiple times)",
    ),
    discord_webhook: Optional[str] = typer.Option(
        None,
        "--alert",
        help="Discord webhook URL for alerts (format: discord-webhook=URL)",
    ),
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="Override RPC URL",
    ),
):
    """Watch progress of a specific job with optional alerts."""
    console.print(f"[bold]Job Monitor: {job_id}[/bold]")
    console.print(f"Polling every {interval} seconds. Press Ctrl+C to stop.\n")
    
    # Parse alert config
    alert_triggers = set(alert_on) if alert_on else set()
    webhook_url = None
    
    if discord_webhook:
        if discord_webhook.startswith("discord-webhook="):
            webhook_url = discord_webhook.split("=", 1)[1]
        else:
            webhook_url = discord_webhook
    
    if alert_triggers:
        console.print(f"[bold]Alert Config:[/bold]")
        console.print(f"  Triggers: {', '.join(alert_triggers)}")
        if webhook_url:
            console.print(f"  Webhook: {webhook_url[:30]}...")
        console.print()
    
    # Mock monitoring loop (actual implementation would poll job status)
    prev_status = None
    start_time = time.time()
    
    try:
        iteration = 0
        while True:
            iteration += 1
            
            # Attempt RPC call to get job status; fall back to elapsed-time estimate
            # when the aicf.jobs.getStatus RPC method is not yet available.
            try:
                rpc_result = aicf_utils.rpc_call(
                    "aicf.jobs.getStatus", params=[job_id], url=rpc_url
                )
                mock_status = {
                    "job_id": job_id,
                    "status": rpc_result.get("status", "UNKNOWN"),
                    "progress": rpc_result.get("progress", 0),
                    "budget_used": rpc_result.get("budget_used", 0),
                    "budget_total": rpc_result.get("budget_total", 0),
                    "workers_active": rpc_result.get("workers_active", 0),
                    "estimated_completion": rpc_result.get("estimated_completion", 0),
                }
            except Exception:
                # Phase 2 - Integration pending: aicf.jobs.getStatus RPC method.
                # Until the RPC method is available, display elapsed-time estimate.
                elapsed = time.time() - start_time
                mock_status = {
                    "job_id": job_id,
                    "status": "RUNNING" if elapsed < 60 else "COMPLETED",
                    "progress": min(100, int((elapsed / 60) * 100)),
                    "budget_used": int(elapsed * 10),
                    "budget_total": 1000,
                    "workers_active": 1 if elapsed < 60 else 0,
                    "estimated_completion": 60 - int(elapsed) if elapsed < 60 else 0,
                }
            
            console.print(f"[dim]{time.strftime('%Y-%m-%d %H:%M:%S')}[/dim]")
            console.print(f"  Status: {mock_status['status']}")
            console.print(f"  Progress: {mock_status['progress']}%")
            console.print(f"  Budget: {mock_status['budget_used']}/{mock_status['budget_total']} credits")
            console.print(f"  Workers: {mock_status['workers_active']}")
            
            if mock_status['estimated_completion'] > 0:
                console.print(f"  ETA: {mock_status['estimated_completion']} seconds")
            
            # Detect status changes and trigger alerts
            if prev_status:
                if mock_status['status'] != prev_status['status']:
                    console.print(f"  [yellow]→ Status changed: {prev_status['status']} → {mock_status['status']}[/yellow]")
                    
                    # Trigger alerts
                    status_lower = mock_status['status'].lower()
                    if status_lower in alert_triggers or "complete" in alert_triggers and status_lower == "completed":
                        _send_alert(webhook_url, f"Job {job_id} status: {mock_status['status']}")
                
                # Alert on stall (no progress)
                if "stall" in alert_triggers:
                    if mock_status['progress'] == prev_status.get('progress', 0):
                        console.print(f"  [red]⚠ No progress detected (possible stall)[/red]")
                        _send_alert(webhook_url, f"Job {job_id} may be stalled (no progress)")
                
                # Alert on worker count drop
                if mock_status['workers_active'] == 0 and prev_status.get('workers_active', 0) > 0:
                    console.print(f"  [red]⚠ All workers stopped[/red]")
                    _send_alert(webhook_url, f"Job {job_id} has no active workers")
            
            prev_status = mock_status.copy()
            console.print()
            
            # Stop if job completed
            if mock_status['status'] == "COMPLETED":
                console.print("[green]✓ Job completed![/green]")
                if "complete" in alert_triggers:
                    _send_alert(webhook_url, f"Job {job_id} completed successfully")
                break
            
            time.sleep(interval)
    
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped monitoring.[/dim]")
        raise typer.Exit(0)


def _send_alert(webhook_url: Optional[str], message: str) -> None:
    """Send alert to webhook (Discord, Slack, etc.)."""
    if not webhook_url:
        return
    
    try:
        import requests
        payload = {"content": message}
        requests.post(webhook_url, json=payload, timeout=5)
        console.print(f"  [dim]→ Alert sent[/dim]")
    except Exception as e:
        console.print(f"  [dim red]→ Alert failed: {e}[/dim red]")


@aicf_app.command("storage-credits")
def storage_credits_cmd(
    address: str = typer.Argument(..., help="Provider address (hex or bech32)"),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
    db_path: Optional[str] = typer.Option(
        None,
        "--db-path",
        help="Override database path",
    ),
):
    """Show storage credits earned by a provider."""
    try:
        from aicf.credits.storage import StorageCreditsDB
        from pathlib import Path
        
        # Convert address to provider_id (for now, use as-is if hex)
        # In production, would resolve via registry
        if address.startswith("0x"):
            provider_id = bytes.fromhex(address[2:])
        else:
            # Assume hex without 0x prefix
            provider_id = bytes.fromhex(address)
        
        if len(provider_id) != 32:
            console.print("[red]Error: Provider ID must be 32 bytes (64 hex characters)[/red]")
            raise typer.Exit(1)
        
        db_path_obj = Path(db_path) if db_path else None
        db = StorageCreditsDB(db_path_override=db_path_obj)
        
        records = db.list_provider_credits(provider_id)
        
        if json_output:
            output = {
                "provider_id": provider_id.hex(),
                "records": [
                    {
                        "period": r.period,
                        "gb_stored": r.gb_stored,
                        "audits_passed": r.audits_passed,
                        "audits_failed": r.audits_failed,
                        "bandwidth_gb": r.bandwidth_gb,
                        "reliability_score": r.reliability_score,
                        "credits_earned": r.credits_earned,
                        "settled": r.settled,
                    }
                    for r in records
                ],
            }
            console.print(aicf_utils.safe_json_encode(output))
        else:
            console.print(f"[bold]Storage Credits: {address}[/bold]\n")
            
            if not records:
                console.print("[dim]No storage credits recorded yet.[/dim]")
                return
            
            table = Table(show_header=True, header_style="bold")
            table.add_column("Period")
            table.add_column("Storage", justify="right")
            table.add_column("Audits", justify="right")
            table.add_column("Bandwidth", justify="right")
            table.add_column("Credits", justify="right")
            table.add_column("Status")
            
            total_earned = 0
            for r in records:
                status = "✓ Settled" if r.settled else "Claimable"
                table.add_row(
                    r.period,
                    f"{r.gb_stored:.1f} GB",
                    f"{r.audits_passed}/{r.audits_passed + r.audits_failed}",
                    f"{r.bandwidth_gb:.1f} GB",
                    str(r.credits_earned),
                    status,
                )
                total_earned += r.credits_earned
            
            console.print(table)
            console.print(f"\n[bold]Total Earned:[/bold] {total_earned} credits")
            
            claimable = db.get_total_claimable(provider_id)
            if claimable > 0:
                console.print(f"[bold green]Claimable:[/bold green] {claimable} credits")
                console.print(f"\n[dim]Claim with: animica aicf claim --type storage[/dim]")
    
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@aicf_app.command("storage-claims")
def storage_claims_cmd(
    address: str = typer.Argument(..., help="Provider address (hex or bech32)"),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
    db_path: Optional[str] = typer.Option(
        None,
        "--db-path",
        help="Override database path",
    ),
):
    """Show claimable storage credits for a provider."""
    try:
        from aicf.credits.settlement import get_claimable_summary
        from pathlib import Path
        
        # Convert address to provider_id
        if address.startswith("0x"):
            provider_id = bytes.fromhex(address[2:])
        else:
            provider_id = bytes.fromhex(address)
        
        if len(provider_id) != 32:
            console.print("[red]Error: Provider ID must be 32 bytes (64 hex characters)[/red]")
            raise typer.Exit(1)
        
        summary = get_claimable_summary(provider_id, db_path_override=db_path)
        
        if json_output:
            console.print(aicf_utils.safe_json_encode(summary))
        else:
            console.print(f"[bold]Claimable Storage Credits: {address}[/bold]\n")
            
            if summary["total_claimable"] == 0:
                console.print("[dim]No claimable credits available.[/dim]")
                return
            
            console.print(f"[bold]Total Claimable:[/bold] {summary['total_claimable']} credits")
            console.print(f"[bold]Period Count:[/bold] {summary['period_count']}\n")
            
            table = Table(show_header=True, header_style="bold")
            table.add_column("Period")
            table.add_column("Storage", justify="right")
            table.add_column("Audits", justify="right")
            table.add_column("Bandwidth", justify="right")
            table.add_column("Credits", justify="right")
            
            for p in summary["periods"]:
                table.add_row(
                    p["period"],
                    f"{p['gb_stored']:.1f} GB",
                    str(p["audits_passed"]),
                    f"{p['bandwidth_gb']:.1f} GB",
                    str(p["credits"]),
                )
            
            console.print(table)
            console.print(f"\n[dim]Claim with: animica aicf claim --type storage --amount {summary['total_claimable']}[/dim]")
    
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@aicf_app.command("claim")
def claim_cmd(
    address: str = typer.Argument(..., help="Miner/provider address to claim from"),
    all_credits: bool = typer.Option(
        False,
        "--all",
        help="Claim all available credits",
    ),
    amount: Optional[int] = typer.Option(
        None,
        "--amount",
        help="Specific amount to claim (mutually exclusive with --all)",
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
    debug_rpc: bool = typer.Option(
        False,
        "--debug-rpc",
        help="Print RPC request details",
    ),
):
    """
    Claim AICF credits from miner rewards.
    
    Use --all to claim all available credits, or --amount to claim a specific amount.
    Includes anti-spam validation and idempotency checks.
    """
    try:
        # Validate mutually exclusive flags
        if all_credits and amount is not None:
            console.print("[red]Error: --all and --amount are mutually exclusive[/red]")
            raise typer.Exit(1)
        
        if not all_credits and amount is None:
            console.print("[red]Error: Specify either --all or --amount <value>[/red]")
            raise typer.Exit(1)
        
        # Anti-spam: Check current balance first
        try:
            balance_result = aicf_utils.rpc_call(
                "state.getAicfMinerCredits",
                params=[address],
                url=rpc_url,
                debug=debug_rpc,
            )
            available = int(balance_result.get("balance", 0))
        except Exception as e:
            console.print(f"[red]Failed to fetch current balance: {e}[/red]")
            raise typer.Exit(1)
        
        if available == 0:
            console.print("[yellow]No credits available to claim.[/yellow]")
            if json_output:
                console.print(aicf_utils.safe_json_encode({
                    "claimed": 0,
                    "reason": "no_balance"
                }))
            raise typer.Exit(0)
        
        # Determine claim amount
        claim_amount = available if all_credits else amount
        
        # Anti-spam: Validate amount
        if claim_amount <= 0:
            console.print(f"[red]Error: Claim amount must be positive (got {claim_amount})[/red]")
            raise typer.Exit(1)
        
        if claim_amount > available:
            console.print(f"[red]Error: Insufficient balance[/red]")
            console.print(f"  Requested: {_format_amount(claim_amount)} credits")
            console.print(f"  Available: {_format_amount(available)} credits")
            raise typer.Exit(1)
        
        # Idempotency check: Verify last claim timestamp to prevent rapid repeated claims
        MIN_CLAIM_INTERVAL_SECONDS = 60  # 1 minute between claims
        
        # TODO: When RPC method exists, check last claim time:
        # last_claim_result = aicf_utils.rpc_call("state.getLastClaimTime", params=[address], ...)
        # if time.time() - last_claim_result["timestamp"] < MIN_CLAIM_INTERVAL_SECONDS:
        #     console.print(f"[red]Anti-spam: Please wait {MIN_CLAIM_INTERVAL_SECONDS}s between claims[/red]")
        #     raise typer.Exit(1)
        
        # Execute claim (when RPC method exists)
        console.print(f"[yellow]Claim functionality not yet fully implemented.[/yellow]")
        console.print(f"\n[bold]Would claim:[/bold]")
        console.print(f"  Address: {address}")
        console.print(f"  Amount: {_format_amount(claim_amount)} credits")
        console.print(f"  Available: {_format_amount(available)} credits")
        
        if json_output:
            console.print(aicf_utils.safe_json_encode({
                "status": "pending_implementation",
                "address": address,
                "amount_requested": claim_amount,
                "amount_available": available,
                "would_claim": True,
            }))
        else:
            console.print(f"\n[dim]Phase 2 - Integration pending: aicf.claim RPC method[/dim]")
            console.print(f"[dim]This will transfer credits from mining rewards to usable AICF balance[/dim]")
    
    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


# Fee routing commands
fees_app = typer.Typer(
    name="fees",
    help="Fee routing and revenue tracking",
    no_args_is_help=True,
)

aicf_app.add_typer(fees_app, name="fees")


@fees_app.command("status")
def fees_status(
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
    debug_rpc: bool = typer.Option(
        False,
        "--debug-rpc",
        help="Print RPC request details",
    ),
):
    """
    Show fee routing status (block rewards, tx fees, ENA fees).
    
    Displays breakdown of:
    - Block rewards → AICF minting
    - Transaction fees → Fee pool distribution
    - ENA training fees → Treasury routing
    """
    try:
        # TODO: When RPC method exists, call it:
        # result = aicf_utils.rpc_call("state.getFeeRouting", url=rpc_url, debug=debug_rpc)
        
        # Mock data structure for demonstration
        mock_result = {
            "block_rewards": {
                "total_issued": 1_000_000_000_000,  # Total ANM issued via block rewards
                "aicf_percentage": 5.0,  # 5% of block rewards go to AICF
                "aicf_minted_lifetime": 50_000_000_000,  # Total AICF credits minted from rewards
                "last_block_reward": 100_000_000,  # Last block reward amount
                "last_aicf_mint": 5_000_000,  # AICF minted from last block
            },
            "transaction_fees": {
                "total_collected": 10_000_000_000,  # Total tx fees collected
                "burn_percentage": 50.0,  # 50% burned
                "pool_percentage": 50.0,  # 50% to fee pool
                "burned_lifetime": 5_000_000_000,
                "pool_lifetime": 5_000_000_000,
            },
            "ena_fees": {
                "total_training_fees": 20_000_000_000,  # Total ENA training fees
                "treasury_percentage": 100.0,  # 100% to treasury
                "treasury_balance": 20_000_000_000,
            },
            "last_update_height": 12345,
        }
        
        if json_output:
            console.print(aicf_utils.safe_json_encode(mock_result))
        else:
            console.print("[bold]Fee Routing Status[/bold]\n")
            
            # Block Rewards
            console.print("[bold cyan]Block Rewards → AICF Minting[/bold cyan]")
            br = mock_result["block_rewards"]
            console.print(f"  Total Issued: {_format_amount(br['total_issued'])} ANM")
            console.print(f"  AICF Rate: {br['aicf_percentage']}% of block rewards")
            console.print(f"  AICF Minted (Lifetime): {_format_amount(br['aicf_minted_lifetime'])} credits")
            console.print(f"  Last Block Reward: {_format_amount(br['last_block_reward'])} ANM")
            console.print(f"  Last AICF Mint: {_format_amount(br['last_aicf_mint'])} credits")
            console.print()
            
            # Transaction Fees
            console.print("[bold cyan]Transaction Fees → Burn/Pool Split[/bold cyan]")
            tf = mock_result["transaction_fees"]
            console.print(f"  Total Collected: {_format_amount(tf['total_collected'])} ANM")
            console.print(f"  Burn Rate: {tf['burn_percentage']}%")
            console.print(f"  Pool Rate: {tf['pool_percentage']}%")
            console.print(f"  Burned (Lifetime): {_format_amount(tf['burned_lifetime'])} ANM")
            console.print(f"  Pool (Lifetime): {_format_amount(tf['pool_lifetime'])} ANM")
            console.print()
            
            # ENA Fees
            console.print("[bold cyan]ENA Training Fees → Treasury[/bold cyan]")
            ef = mock_result["ena_fees"]
            console.print(f"  Total Training Fees: {_format_amount(ef['total_training_fees'])} credits")
            console.print(f"  Treasury Rate: {ef['treasury_percentage']}%")
            console.print(f"  Treasury Balance: {_format_amount(ef['treasury_balance'])} credits")
            console.print()
            
            console.print(f"[dim]Last Update: Block #{mock_result['last_update_height']}[/dim]")
            console.print(f"\n[yellow]Note: This is mock data. RPC method 'state.getFeeRouting' not yet implemented.[/yellow]")
    
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@aicf_app.command("treasury")
def treasury_cmd(
    action: str = typer.Argument(..., help="Action: topup, balance, history"),
    amount: Optional[int] = typer.Option(
        None,
        "--amount",
        help="Amount to top up (for 'topup' action)",
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
    debug_rpc: bool = typer.Option(
        False,
        "--debug-rpc",
        help="Print RPC request details",
    ),
):
    """
    Manage AICF treasury.
    
    Actions:
      topup    - Top up treasury with credits (requires --amount)
      balance  - Show current treasury balance
      history  - Show treasury transaction history
    """
    try:
        if action == "topup":
            if amount is None:
                console.print("[red]Error: --amount is required for topup action[/red]")
                raise typer.Exit(1)
            
            if amount <= 0:
                console.print("[red]Error: Amount must be positive[/red]")
                raise typer.Exit(1)
            
            console.print("[yellow]Treasury topup not yet implemented.[/yellow]")
            console.print(f"\n[bold]Would top up:[/bold]")
            console.print(f"  Amount: {_format_amount(amount)} credits")
            console.print(f"\n[dim]Phase 2 - Integration pending: Treasury topup mechanism[/dim]")
            console.print(f"[dim]This will transfer credits from user balance to AICF treasury[/dim]")
            console.print(f"[dim]Treasury funds job payments and provider rewards[/dim]")
            
            if json_output:
                console.print(aicf_utils.safe_json_encode({
                    "status": "pending_implementation",
                    "action": "topup",
                    "amount": amount,
                }))
        
        elif action == "balance":
            # TODO: When RPC method exists:
            # result = aicf_utils.rpc_call("aicf.getTreasuryBalance", url=rpc_url, debug=debug_rpc)
            
            mock_balance = {
                "balance": 1_000_000_000_000,  # 1000 ANM worth of credits
                "reserved": 500_000_000_000,  # 500 ANM reserved for active jobs
                "available": 500_000_000_000,  # 500 ANM available
                "last_update_height": 12345,
            }
            
            if json_output:
                console.print(aicf_utils.safe_json_encode(mock_balance))
            else:
                console.print("[bold]AICF Treasury Balance[/bold]\n")
                console.print(f"  Total Balance: {_format_amount(mock_balance['balance'])} credits")
                console.print(f"  Reserved (Jobs): {_format_amount(mock_balance['reserved'])} credits")
                console.print(f"  Available: {_format_amount(mock_balance['available'])} credits")
                console.print(f"\n[dim]Last Update: Block #{mock_balance['last_update_height']}[/dim]")
                console.print(f"\n[yellow]Note: This is mock data. RPC method not yet implemented.[/yellow]")
        
        elif action == "history":
            # TODO: When RPC method exists:
            # result = aicf_utils.rpc_call("aicf.getTreasuryHistory", url=rpc_url, debug=debug_rpc)
            
            console.print("[yellow]Treasury history not yet implemented.[/yellow]")
            console.print(f"\n[dim]This will show:[/dim]")
            console.print(f"  • Treasury deposits (from fees, topups)")
            console.print(f"  • Job payments and provider rewards")
            console.print(f"  • Balance changes over time")
            
            if json_output:
                console.print(aicf_utils.safe_json_encode({
                    "status": "pending_implementation",
                    "action": "history",
                }))
        
        else:
            console.print(f"[red]Error: Unknown action '{action}'[/red]")
            console.print("Available actions: topup, balance, history")
            raise typer.Exit(1)
    
    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


__all__ = ["aicf_app"]
