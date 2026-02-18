"""
ENA upgrade and registry CLI commands.

Provides commands for managing model upgrades and the model registry.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.panel import Panel
from rich.tree import Tree

# Add ena module to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../"))

from ena.upgrade.state_machine import (
    UpgradeStateMachine,
    UpgradeState,
    UpgradeStatus,
)
from ena.upgrade.coordinator import UpgradeCoordinator
from ena.upgrade.verifier import ResultVerifier, SafetyGates
from ena.registry.storage import RegistryStorage
from ena.registry.schema import ModelManifest

console = Console()
app = typer.Typer(help="ENA upgrade and registry management")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def _get_state_file() -> Path:
    """Get default state file path."""
    ena_dir = Path.home() / ".animica" / "ena"
    ena_dir.mkdir(parents=True, exist_ok=True)
    return ena_dir / "upgrade_state.json"


def _get_registry_dir() -> Path:
    """Get default registry directory."""
    registry_dir = Path.home() / ".animica" / "ena" / "registry"
    registry_dir.mkdir(parents=True, exist_ok=True)
    return registry_dir


def _get_work_dir() -> Path:
    """Get default work directory."""
    work_dir = Path.home() / ".animica" / "ena" / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    return work_dir


def _create_coordinator() -> UpgradeCoordinator:
    """Create coordinator with default configuration."""
    state_machine = UpgradeStateMachine(_get_state_file())
    registry = RegistryStorage(_get_registry_dir())
    verifier = ResultVerifier()
    safety_gates = SafetyGates(
        min_accuracy=0.9,
        max_perplexity=3.0,
        max_toxicity_score=0.1,
        min_regression_pass_rate=0.95,
    )
    
    return UpgradeCoordinator(
        state_machine=state_machine,
        registry=registry,
        verifier=verifier,
        safety_gates=safety_gates,
        work_dir=_get_work_dir(),
    )


@app.command("auto")
def upgrade_auto(
    model_id: str = typer.Option("ena", "--model-id", help="Model identifier"),
    target_version: str = typer.Option(..., "--version", help="Target version"),
    creator: str = typer.Option(..., "--creator", help="Creator address"),
    datasets: str = typer.Option(..., "--datasets", help="Comma-separated dataset hashes"),
    base_model: str = typer.Option("qwen2.5-coder-1.5b", "--base-model", help="Base model"),
    auto_promote: bool = typer.Option(False, "--auto-promote", help="Auto-promote canary"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would happen"),
):
    """
    Run full automatic upgrade workflow.
    
    This will:
    1. Create training plan
    2. Allocate budget (stub)
    3. Submit jobs (stub)
    4. Monitor progress (stub)
    5. Verify results
    6. Publish model
    7. Deploy canary
    8. Optionally promote to 100%
    """
    console.print(Panel.fit(
        f"[bold cyan]ENA Upgrade Workflow[/bold cyan]\n"
        f"Model: {model_id}\n"
        f"Version: {target_version}\n"
        f"Creator: {creator}",
        title="Auto Upgrade"
    ))
    
    dataset_hashes = [h.strip() for h in datasets.split(",")]
    
    if dry_run:
        console.print("\n[yellow]DRY RUN - No changes will be made[/yellow]\n")
        console.print("Would execute:")
        console.print(f"  1. Create training plan for {model_id} v{target_version}")
        console.print(f"  2. Use {len(dataset_hashes)} datasets")
        console.print(f"  3. Base model: {base_model}")
        console.print(f"  4. Auto-promote: {auto_promote}")
        return
    
    # Create coordinator
    coordinator = _create_coordinator()
    
    # Create new upgrade
    upgrade_id = f"{model_id}_upgrade_{int(datetime.utcnow().timestamp())}"
    
    # Get previous version for rollback
    previous_version = coordinator.registry.get_pinned_version(model_id)
    
    coordinator.state_machine.create_upgrade(
        upgrade_id=upgrade_id,
        model_id=model_id,
        target_version=target_version,
        previous_version=previous_version,
    )
    
    # Run workflow with progress
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Running upgrade workflow...", total=None)
        
        try:
            success = coordinator.run_full_workflow(
                model_id=model_id,
                target_version=target_version,
                creator=creator,
                dataset_hashes=dataset_hashes,
                base_model=base_model,
                auto_promote=auto_promote,
            )
            
            if success:
                console.print("\n[green]✓ Upgrade completed successfully![/green]")
                
                if not auto_promote:
                    console.print("\n[yellow]Canary deployed. Run 'animica ena upgrade promote' to complete rollout.[/yellow]")
            else:
                console.print("\n[red]✗ Upgrade failed. Check logs for details.[/red]")
                raise typer.Exit(1)
        
        except Exception as e:
            console.print(f"\n[red]Error: {e}[/red]")
            raise typer.Exit(1)


@app.command("status")
def upgrade_status():
    """Show current upgrade status."""
    state_machine = UpgradeStateMachine(_get_state_file())
    status = state_machine.get_status()
    
    if not status:
        console.print("[yellow]No upgrade in progress[/yellow]")
        return
    
    # Create status panel
    status_text = (
        f"[bold]Upgrade ID:[/bold] {status.upgrade_id}\n"
        f"[bold]Model:[/bold] {status.model_id}\n"
        f"[bold]Target Version:[/bold] {status.target_version}\n"
        f"[bold]Current State:[/bold] {status.current_state.value}\n"
        f"[bold]Created:[/bold] {status.created_at}\n"
        f"[bold]Updated:[/bold] {status.updated_at}\n"
    )
    
    if status.plan_id:
        status_text += f"[bold]Plan ID:[/bold] {status.plan_id}\n"
    
    if status.budget_allocated > 0:
        budget_anm = status.budget_allocated / 1_000_000_000
        used_anm = status.budget_used / 1_000_000_000
        status_text += f"[bold]Budget:[/bold] {used_anm:.2f} / {budget_anm:.2f} ANM\n"
    
    console.print(Panel(status_text, title="Upgrade Status"))
    
    # Show job statuses
    if status.job_statuses:
        console.print("\n[bold]Job Status:[/bold]")
        
        table = Table(show_header=True)
        table.add_column("Job ID", style="cyan")
        table.add_column("State", style="yellow")
        table.add_column("AICF Job ID", style="green")
        table.add_column("Started", style="white")
        table.add_column("Completed", style="white")
        
        for job_id, job_status in status.job_statuses.items():
            table.add_row(
                job_id[:40] + "..." if len(job_id) > 40 else job_id,
                job_status.state,
                job_status.aicf_job_id or "-",
                job_status.started_at or "-",
                job_status.completed_at or "-",
            )
        
        console.print(table)
    
    # Show errors if any
    if status.errors:
        console.print("\n[bold red]Errors:[/bold red]")
        for error in status.errors:
            console.print(f"  • {error}")


@app.command("resume")
def upgrade_resume():
    """Resume upgrade from last checkpoint."""
    state_machine = UpgradeStateMachine(_get_state_file())
    
    if not state_machine.can_resume():
        console.print("[yellow]No upgrade to resume[/yellow]")
        return
    
    status = state_machine.get_status()
    if not status:
        console.print("[yellow]No upgrade in progress[/yellow]")
        return
    
    console.print(f"[cyan]Resuming upgrade from state: {status.current_state.value}[/cyan]")
    
    # TODO: Implement resume logic based on current state
    console.print("[yellow]Resume functionality not yet fully implemented[/yellow]")
    console.print("Current state allows manual intervention:")
    console.print(f"  State: {status.current_state.value}")
    console.print(f"  Upgrade ID: {status.upgrade_id}")


@app.command("promote")
def upgrade_promote():
    """Promote canary to 100% traffic."""
    coordinator = _create_coordinator()
    status = coordinator.state_machine.get_status()
    
    if not status:
        console.print("[red]No upgrade in progress[/red]")
        raise typer.Exit(1)
    
    if status.current_state != UpgradeState.CANARY:
        console.print(f"[red]Cannot promote: current state is {status.current_state.value}[/red]")
        raise typer.Exit(1)
    
    console.print("[cyan]Promoting canary to 100% traffic...[/cyan]")
    
    success = coordinator.promote_canary()
    
    if success:
        console.print("[green]✓ Canary promoted successfully![/green]")
    else:
        console.print("[red]✗ Failed to promote canary[/red]")
        raise typer.Exit(1)


@app.command("rollback")
def upgrade_rollback():
    """Rollback to previous version."""
    coordinator = _create_coordinator()
    status = coordinator.state_machine.get_status()
    
    if not status:
        console.print("[red]No upgrade in progress[/red]")
        raise typer.Exit(1)
    
    if not status.previous_version:
        console.print("[red]No previous version to rollback to[/red]")
        raise typer.Exit(1)
    
    console.print(f"[yellow]Rolling back to version: {status.previous_version}[/yellow]")
    
    success = coordinator.rollback()
    
    if success:
        console.print("[green]✓ Rollback completed successfully![/green]")
    else:
        console.print("[red]✗ Failed to rollback[/red]")
        raise typer.Exit(1)


# Registry commands
registry_app = typer.Typer(help="Model registry commands")
app.add_typer(registry_app, name="registry")


@registry_app.command("list")
def registry_list(
    model_id: Optional[str] = typer.Option(None, "--model-id", help="Filter by model ID"),
):
    """List all model versions in registry."""
    registry = RegistryStorage(_get_registry_dir())
    
    models = registry.list_all_models()
    
    if not models:
        console.print("[yellow]No models in registry[/yellow]")
        return
    
    for mid, versions in models.items():
        # Skip if filtering and doesn't match
        if model_id and mid != model_id:
            continue
        
        console.print(f"\n[bold cyan]{mid}[/bold cyan]")
        
        # Check pinned version
        pinned = registry.get_pinned_version(mid)
        
        table = Table(show_header=True)
        table.add_column("Version", style="green")
        table.add_column("Pinned", style="yellow")
        table.add_column("Type", style="cyan")
        table.add_column("Created", style="white")
        
        for version in versions:
            manifest = registry.load_manifest(mid, version)
            if manifest:
                is_pinned = "✓" if version == pinned else ""
                table.add_row(
                    version,
                    is_pinned,
                    manifest.model_type.value,
                    manifest.created_at,
                )
        
        console.print(table)


@registry_app.command("show")
def registry_show(
    model_id: str = typer.Argument(..., help="Model ID"),
    version: str = typer.Argument(..., help="Version"),
):
    """Show details for a specific model version."""
    registry = RegistryStorage(_get_registry_dir())
    
    manifest = registry.load_manifest(model_id, version)
    
    if not manifest:
        console.print(f"[red]Model not found: {model_id} v{version}[/red]")
        raise typer.Exit(1)
    
    # Display manifest details
    console.print(Panel.fit(
        f"[bold]{model_id}[/bold] v{version}",
        title="Model Manifest"
    ))
    
    console.print(f"\n[bold]Type:[/bold] {manifest.model_type.value}")
    console.print(f"[bold]Quantization:[/bold] {manifest.quantization.value}")
    console.print(f"[bold]Creator:[/bold] {manifest.creator}")
    console.print(f"[bold]Created:[/bold] {manifest.created_at}")
    console.print(f"[bold]Description:[/bold] {manifest.description}")
    
    # Eval metrics
    console.print("\n[bold]Evaluation Metrics:[/bold]")
    if manifest.eval_metrics.accuracy is not None:
        console.print(f"  Accuracy: {manifest.eval_metrics.accuracy:.4f}")
    if manifest.eval_metrics.perplexity is not None:
        console.print(f"  Perplexity: {manifest.eval_metrics.perplexity:.4f}")
    if manifest.eval_metrics.toxicity_score is not None:
        console.print(f"  Toxicity: {manifest.eval_metrics.toxicity_score:.4f}")
    if manifest.eval_metrics.regression_pass_rate is not None:
        console.print(f"  Regression Pass Rate: {manifest.eval_metrics.regression_pass_rate:.4f}")
    
    # Provenance
    console.print("\n[bold]Training Provenance:[/bold]")
    console.print(f"  Base Model: {manifest.training_provenance.base_model}")
    console.print(f"  Datasets: {len(manifest.training_provenance.dataset_hashes)}")
    console.print(f"  AICF Jobs: {len(manifest.training_provenance.aicf_job_ids)}")
    
    if manifest.training_provenance.gpu_hours:
        console.print(f"  GPU Hours: {manifest.training_provenance.gpu_hours:.2f}")
    
    if manifest.training_provenance.cost_anm:
        cost_anm = manifest.training_provenance.cost_anm / 1_000_000_000
        console.print(f"  Cost: {cost_anm:.2f} ANM")


@registry_app.command("pin")
def registry_pin(
    model_id: str = typer.Argument(..., help="Model ID"),
    version: str = typer.Argument(..., help="Version to pin"),
):
    """Pin a specific model version as active."""
    registry = RegistryStorage(_get_registry_dir())
    
    success = registry.pin_version(model_id, version)
    
    if success:
        console.print(f"[green]✓ Pinned {model_id} to v{version}[/green]")
    else:
        console.print(f"[red]Failed to pin version: {model_id} v{version}[/red]")
        raise typer.Exit(1)


@registry_app.command("pinned")
def registry_pinned(
    model_id: str = typer.Argument(..., help="Model ID"),
):
    """Show currently pinned version."""
    registry = RegistryStorage(_get_registry_dir())
    
    version = registry.get_pinned_version(model_id)
    
    if version:
        console.print(f"[green]Pinned version: {version}[/green]")
    else:
        console.print(f"[yellow]No version pinned for {model_id}[/yellow]")


if __name__ == "__main__":
    app()
