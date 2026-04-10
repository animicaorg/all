"""
Animica DA • Audit CLI

Commands for managing DA provider audits.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

try:
    import typer
    from rich.console import Console
    from rich.table import Table
except ImportError:
    print("Error: typer and rich are required for audit CLI", file=sys.stderr)
    sys.exit(1)

try:
    from da.provider.audit import (
        AuditDatabase,
        create_challenge,
        update_provider_score,
        verify_response,
        CHALLENGE_TYPES,
    )
    from da.provider.audit_scheduler import (
        AuditScheduler,
        AuditSchedulerConfig,
        jail_provider,
        unjail_provider,
        get_jailed_providers,
    )
    from da.provider.registry import (
        AuditResponse,
        ProviderRegistry,
    )
except ImportError as e:
    print(f"Error importing DA audit modules: {e}", file=sys.stderr)
    sys.exit(1)

try:
    from pq.py.sign import sign_message
    PQ_AVAILABLE = True
except ImportError:
    PQ_AVAILABLE = False
    sign_message = None


console = Console()
audit_app = typer.Typer(
    name="audit",
    help="DA provider audit commands",
    no_args_is_help=True,
)


def _get_registry(db_path: Optional[Path] = None) -> ProviderRegistry:
    """Get provider registry."""
    if db_path is None:
        db_path = Path.home() / ".animica" / "provider_registry.db"
    return ProviderRegistry(db_path=db_path)


def _get_audit_db(db_path: Optional[Path] = None) -> AuditDatabase:
    """Get audit database."""
    if db_path is None:
        db_path = Path.home() / ".animica" / "audit_results.db"
    return AuditDatabase(db_path=db_path)


@audit_app.command("challenge")
def challenge_command(
    provider_id: str = typer.Argument(..., help="Provider ID (hex)"),
    blob_commitment: str = typer.Argument(..., help="Blob commitment (hex)"),
    challenge_type: str = typer.Option("byte-range", help="Challenge type"),
    deadline: int = typer.Option(3600, help="Deadline in seconds"),
    registry_db: Optional[Path] = typer.Option(None, help="Registry DB path"),
    audit_db: Optional[Path] = typer.Option(None, help="Audit DB path"),
) -> None:
    """
    Create and send an audit challenge to a provider.
    """
    try:
        provider_id_bytes = bytes.fromhex(provider_id)
        blob_commitment_bytes = bytes.fromhex(blob_commitment)
    except ValueError as e:
        console.print(f"[red]Error: Invalid hex input: {e}[/red]")
        raise typer.Exit(1)
    
    if challenge_type not in CHALLENGE_TYPES:
        console.print(f"[red]Error: Invalid challenge type. Must be one of: {', '.join(CHALLENGE_TYPES)}[/red]")
        raise typer.Exit(1)
    
    # Get registry and audit DB
    registry = _get_registry(registry_db)
    db = _get_audit_db(audit_db)
    
    # Check provider exists
    provider = registry.get_provider(provider_id_bytes)
    if not provider:
        console.print(f"[red]Error: Provider {provider_id} not found[/red]")
        raise typer.Exit(1)
    
    # Create challenge
    challenge = create_challenge(
        provider_id=provider_id_bytes,
        blob_commitment=blob_commitment_bytes,
        challenge_type=challenge_type,
        deadline_seconds=deadline,
    )
    
    # Store challenge
    db.store_challenge(challenge)
    
    console.print("[green]✓[/green] Challenge created")
    console.print(f"Challenge ID: {challenge.challenge_id.hex()}")
    console.print(f"Type: {challenge.challenge_type}")
    console.print(f"Deadline: {challenge.deadline} ({time.ctime(challenge.deadline)})")
    console.print(f"Nonce: {challenge.nonce.hex()[:16]}...")
    
    # Display challenge params
    console.print("\nChallenge parameters:")
    for key, value in challenge.params.items():
        console.print(f"  {key}: {value}")


@audit_app.command("respond")
def respond_command(
    challenge_id: str = typer.Argument(..., help="Challenge ID (hex)"),
    provider_key: Optional[Path] = typer.Option(None, help="Provider private key file"),
    registry_db: Optional[Path] = typer.Option(None, help="Registry DB path"),
    audit_db: Optional[Path] = typer.Option(None, help="Audit DB path"),
) -> None:
    """
    Respond to an audit challenge (for providers).
    
    This command simulates a provider responding to a challenge.
    In production, providers would fetch blob data and create appropriate response.
    """
    try:
        challenge_id_bytes = bytes.fromhex(challenge_id)
    except ValueError as e:
        console.print(f"[red]Error: Invalid hex input: {e}[/red]")
        raise typer.Exit(1)
    
    # Get audit DB
    db = _get_audit_db(audit_db)
    
    # Get challenge
    challenge = db.get_challenge(challenge_id_bytes)
    if not challenge:
        console.print(f"[red]Error: Challenge {challenge_id} not found[/red]")
        raise typer.Exit(1)
    
    # Check deadline
    now = int(time.time())
    if now > challenge.deadline:
        console.print(f"[red]Error: Challenge deadline has passed[/red]")
        raise typer.Exit(1)
    
    # Get provider
    registry = _get_registry(registry_db)
    provider = registry.get_provider(challenge.provider_id)
    if not provider:
        console.print(f"[red]Error: Provider not found[/red]")
        raise typer.Exit(1)
    
    # Create response payload (simulated - in production, would fetch actual data)
    response_type = {
        "byte-range": "byte-data",
        "merkle-proof": "merkle-proof",
        "nmt-proof": "nmt-proof",
    }[challenge.challenge_type]
    
    payload = {}
    if challenge.challenge_type == "byte-range":
        # Simulate byte range response
        offset = challenge.params.get(0, 0)
        length = challenge.params.get(1, 256)
        # In production, would read actual blob data
        simulated_data = b"x" * length
        payload[0] = simulated_data.hex()
    else:
        # Simulate proof
        payload[0] = os.urandom(32).hex()
    
    # Sign response
    if PQ_AVAILABLE and provider_key is not None:
        # Load provider key and sign
        # For now, create dummy signature
        signature = os.urandom(64)
    else:
        # Dummy signature
        signature = os.urandom(64)
    
    # Create response
    response = AuditResponse(
        challenge_id=challenge_id_bytes,
        provider_id=challenge.provider_id,
        response_type=response_type,
        payload=payload,
        signature=signature,
        submitted_at=now,
    )
    
    # Store response
    db.store_response(response)
    
    console.print("[green]✓[/green] Response submitted")
    console.print(f"Response type: {response.response_type}")
    console.print(f"Submitted at: {time.ctime(response.submitted_at)}")


@audit_app.command("run")
def run_command(
    sample_size: int = typer.Option(10, help="Number of audits to run"),
    registry_db: Optional[Path] = typer.Option(None, help="Registry DB path"),
    audit_db: Optional[Path] = typer.Option(None, help="Audit DB path"),
) -> None:
    """
    Run an audit round manually.
    """
    registry = _get_registry(registry_db)
    db = _get_audit_db(audit_db)
    
    # Create scheduler
    config = AuditSchedulerConfig(sample_size=sample_size)
    scheduler = AuditScheduler(registry, db, config)
    
    console.print(f"Running audit round (sample size: {sample_size})...")
    
    # Run audit
    results = scheduler.run_audit_round()
    
    if not results:
        console.print("[yellow]No provider-blob pairs available for audit[/yellow]")
        return
    
    # Display results
    table = Table(title="Audit Results")
    table.add_column("Provider ID", style="cyan")
    table.add_column("Passed", style="green")
    table.add_column("Score Δ", style="yellow")
    table.add_column("Reason", style="red")
    
    for result in results:
        provider_id_str = result.provider_id.hex()[:16] + "..."
        passed_str = "✓" if result.passed else "✗"
        score_delta_str = f"{result.score_delta:+d}"
        reason_str = result.failure_reason or "—"
        
        table.add_row(provider_id_str, passed_str, score_delta_str, reason_str)
    
    console.print(table)
    console.print(f"\nTotal audits: {len(results)}")
    console.print(f"Passed: {sum(1 for r in results if r.passed)}")
    console.print(f"Failed: {sum(1 for r in results if not r.passed)}")


@audit_app.command("results")
def results_command(
    provider_id: str = typer.Argument(..., help="Provider ID (hex)"),
    limit: int = typer.Option(20, help="Number of results to show"),
    registry_db: Optional[Path] = typer.Option(None, help="Registry DB path"),
    audit_db: Optional[Path] = typer.Option(None, help="Audit DB path"),
) -> None:
    """
    Show audit history for a provider.
    """
    try:
        provider_id_bytes = bytes.fromhex(provider_id)
    except ValueError as e:
        console.print(f"[red]Error: Invalid hex input: {e}[/red]")
        raise typer.Exit(1)
    
    # Get databases
    registry = _get_registry(registry_db)
    db = _get_audit_db(audit_db)
    
    # Check provider exists
    provider = registry.get_provider(provider_id_bytes)
    if not provider:
        console.print(f"[red]Error: Provider {provider_id} not found[/red]")
        raise typer.Exit(1)
    
    # Get results
    results = db.get_results_for_provider(provider_id_bytes, limit=limit)
    
    if not results:
        console.print("[yellow]No audit results found[/yellow]")
        return
    
    # Display provider info
    console.print(f"Provider: {provider_id[:16]}...")
    console.print(f"Current uptime score: {provider.uptime_score}")
    console.print(f"Status: {'Active' if provider.active else 'Inactive'}")
    if provider.jailed_until:
        console.print(f"Jailed until: {time.ctime(provider.jailed_until)}")
    console.print()
    
    # Display results table
    table = Table(title=f"Audit History (last {len(results)} audits)")
    table.add_column("Challenge ID", style="cyan")
    table.add_column("Passed", style="green")
    table.add_column("Score Δ", style="yellow")
    table.add_column("Verified At", style="blue")
    table.add_column("Reason", style="red")
    
    for result in results:
        challenge_id_str = result.challenge_id.hex()[:16] + "..."
        passed_str = "✓" if result.passed else "✗"
        score_delta_str = f"{result.score_delta:+d}"
        verified_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(result.verified_at))
        reason_str = result.failure_reason or "—"
        
        table.add_row(challenge_id_str, passed_str, score_delta_str, verified_str, reason_str)
    
    console.print(table)
    
    # Calculate stats
    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed
    pass_rate = (passed / len(results) * 100) if results else 0
    
    console.print(f"\nStats: {passed} passed, {failed} failed ({pass_rate:.1f}% pass rate)")


@audit_app.command("jail")
def jail_command(
    provider_id: str = typer.Argument(..., help="Provider ID (hex)"),
    duration: int = typer.Option(86400, help="Jail duration in seconds (default: 24h)"),
    reason: Optional[str] = typer.Option(None, help="Reason for jailing"),
    registry_db: Optional[Path] = typer.Option(None, help="Registry DB path"),
) -> None:
    """
    Manually jail a provider.
    """
    try:
        provider_id_bytes = bytes.fromhex(provider_id)
    except ValueError as e:
        console.print(f"[red]Error: Invalid hex input: {e}[/red]")
        raise typer.Exit(1)
    
    registry = _get_registry(registry_db)
    
    # Check provider exists
    provider = registry.get_provider(provider_id_bytes)
    if not provider:
        console.print(f"[red]Error: Provider {provider_id} not found[/red]")
        raise typer.Exit(1)
    
    # Jail provider
    jail_provider(registry, provider_id_bytes, duration, reason)
    
    console.print(f"[green]✓[/green] Provider {provider_id[:16]}... jailed")
    console.print(f"Duration: {duration} seconds ({duration / 3600:.1f} hours)")
    if reason:
        console.print(f"Reason: {reason}")


@audit_app.command("unjail")
def unjail_command(
    provider_id: str = typer.Argument(..., help="Provider ID (hex)"),
    registry_db: Optional[Path] = typer.Option(None, help="Registry DB path"),
) -> None:
    """
    Unjail a provider.
    """
    try:
        provider_id_bytes = bytes.fromhex(provider_id)
    except ValueError as e:
        console.print(f"[red]Error: Invalid hex input: {e}[/red]")
        raise typer.Exit(1)
    
    registry = _get_registry(registry_db)
    
    # Check provider exists
    provider = registry.get_provider(provider_id_bytes)
    if not provider:
        console.print(f"[red]Error: Provider {provider_id} not found[/red]")
        raise typer.Exit(1)
    
    # Unjail provider
    unjail_provider(registry, provider_id_bytes)
    
    console.print(f"[green]✓[/green] Provider {provider_id[:16]}... unjailed")


@audit_app.command("jailed")
def jailed_command(
    registry_db: Optional[Path] = typer.Option(None, help="Registry DB path"),
) -> None:
    """
    List all currently jailed providers.
    """
    registry = _get_registry(registry_db)
    
    jailed = get_jailed_providers(registry)
    
    if not jailed:
        console.print("[green]No providers are currently jailed[/green]")
        return
    
    table = Table(title="Jailed Providers")
    table.add_column("Provider ID", style="cyan")
    table.add_column("Uptime Score", style="yellow")
    table.add_column("Jailed Until", style="red")
    table.add_column("Notes", style="blue")
    
    for provider_id, provider in jailed:
        provider_id_str = provider_id.hex()[:16] + "..."
        score_str = str(provider.uptime_score)
        jailed_until_str = time.ctime(provider.jailed_until) if provider.jailed_until else "—"
        notes_str = provider.notes or "—"
        
        table.add_row(provider_id_str, score_str, jailed_until_str, notes_str)
    
    console.print(table)
    console.print(f"\nTotal jailed: {len(jailed)}")


if __name__ == "__main__":
    audit_app()
