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
from ena.telemetry.config import (
    load_telemetry_config,
    save_telemetry_config,
    enable_telemetry,
    disable_telemetry,
    TelemetryConfig,
)
from ena.telemetry.collector import TelemetryCollector
from ena.telemetry.curator import TelemetryCurator

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
    
    # Phase 2: Resume logic (state persistence integration pending)
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


# ===== Telemetry Commands =====

data_app = typer.Typer(help="Data collection and curation")
app.add_typer(data_app, name="data")

config_app = typer.Typer(help="Configuration management")
app.add_typer(config_app, name="config")


@data_app.command("curate")
def data_curate(
    auto: bool = typer.Option(False, "--auto", help="Auto-approve based on quality"),
    threshold: float = typer.Option(0.5, "--threshold", help="Quality threshold for auto mode"),
    max_samples: Optional[int] = typer.Option(None, "--max-samples", help="Max samples to process"),
    mock: bool = typer.Option(False, "--mock", help="Mock mode (don't upload to DA)"),
):
    """
    Curate telemetry buffer and upload to DA.
    
    Reviews collected samples, filters for quality, and uploads approved
    data to help improve ENA.
    """
    # Check if telemetry is enabled
    config = load_telemetry_config()
    if not config.opt_in:
        console.print("[yellow]Telemetry is not enabled. Enable with:[/yellow]")
        console.print("  animica config set telemetry.opt_in true")
        return
    
    console.print(Panel.fit(
        "[bold cyan]ENA Telemetry Curation[/bold cyan]\n"
        f"Mode: {'Auto' if auto else 'Manual'}\n"
        f"Quality threshold: {threshold}\n"
        f"Mock mode: {mock}",
        title="Data Curation"
    ))
    
    # Create curator
    curator = TelemetryCurator(mock_mode=mock)
    
    # Curate
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        if not auto:
            # Manual mode doesn't need progress spinner
            progress.stop()
        else:
            task = progress.add_task("Curating samples...", total=None)
        
        result = curator.curate(
            auto=auto,
            quality_threshold=threshold,
            max_samples=max_samples,
        )
    
    # Display results
    console.print("\n[bold green]Curation Complete[/bold green]\n")
    
    table = Table(show_header=True)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="yellow")
    
    table.add_row("Total Samples", str(result.total_samples))
    table.add_row("Approved", str(result.approved_samples))
    table.add_row("Rejected", str(result.rejected_samples))
    table.add_row("Approval Rate", f"{result.curation_stats.get('approval_rate', 0):.1%}")
    table.add_row("Avg Quality Score", f"{result.curation_stats.get('avg_quality_score', 0):.2f}")
    table.add_row("Total Redactions", str(result.curation_stats.get('total_redactions', 0)))
    
    console.print(table)
    
    if result.uploaded_commitments:
        console.print("\n[bold]Uploaded Commitments:[/bold]")
        for commitment in result.uploaded_commitments[:5]:
            console.print(f"  • {commitment}")
        if len(result.uploaded_commitments) > 5:
            console.print(f"  ... and {len(result.uploaded_commitments) - 5} more")


@data_app.command("inspect")
def data_inspect(
    limit: int = typer.Option(10, "--limit", help="Max samples to show"),
    sample_id: Optional[str] = typer.Option(None, "--id", help="Show specific sample"),
):
    """
    Inspect telemetry buffer.
    
    View collected samples before curation.
    """
    collector = TelemetryCollector()
    
    # Get buffer stats
    stats = collector.get_buffer_stats()
    
    console.print(Panel.fit(
        f"[bold cyan]Telemetry Buffer[/bold cyan]\n"
        f"Total samples: {stats['total_samples']}\n"
        f"Buffer size: {stats['total_size_mb']:.2f} MB\n"
        f"Max capacity: {stats['max_buffer_size']} samples\n"
        f"Opt-in: {stats['opt_in']}",
        title="Buffer Stats"
    ))
    
    if sample_id:
        # Show specific sample
        samples = collector.inspect(limit=1000)
        sample = next((s for s in samples if s.sample_id == sample_id), None)
        
        if sample:
            console.print("\n[bold]Sample Details[/bold]\n")
            console.print(f"ID: {sample.sample_id}")
            console.print(f"Timestamp: {sample.timestamp}")
            console.print(f"Model: {sample.model_version}")
            console.print(f"Redacted: {sample.redacted} ({sample.redaction_count} redactions)")
            console.print(f"Feedback score: {sample.feedback_score}")
            console.print(f"Flagged: {sample.flagged}")
            
            console.print("\n[bold]Prompt:[/bold]")
            console.print(Panel(sample.prompt, border_style="blue"))
            
            console.print("\n[bold]Response:[/bold]")
            console.print(Panel(sample.response, border_style="green"))
        else:
            console.print(f"[red]Sample not found: {sample_id}[/red]")
    else:
        # Show sample list
        samples = collector.inspect(limit=limit)
        
        if not samples:
            console.print("[yellow]No samples in buffer[/yellow]")
            return
        
        table = Table(show_header=True)
        table.add_column("ID", style="cyan")
        table.add_column("Timestamp", style="white")
        table.add_column("Model", style="yellow")
        table.add_column("Redactions", style="red")
        table.add_column("Feedback", style="green")
        
        for sample in samples:
            table.add_row(
                sample.sample_id[:12] + "...",
                sample.timestamp[:19],
                sample.model_version,
                str(sample.redaction_count),
                f"{sample.feedback_score:.2f}" if sample.feedback_score else "-",
            )
        
        console.print("\n")
        console.print(table)
        console.print(f"\nShowing {len(samples)} of {stats['total_samples']} samples")


@data_app.command("clear")
def data_clear(
    sample_id: Optional[str] = typer.Option(None, "--id", help="Delete specific sample"),
    force: bool = typer.Option(False, "--force", help="Skip confirmation"),
):
    """
    Clear telemetry buffer.
    
    Delete collected samples (does not affect uploaded data).
    """
    collector = TelemetryCollector()
    
    if sample_id:
        # Delete specific sample
        count = collector.delete(sample_id)
        if count > 0:
            console.print(f"[green]✓ Deleted sample: {sample_id}[/green]")
        else:
            console.print(f"[red]Sample not found: {sample_id}[/red]")
    else:
        # Delete all samples
        stats = collector.get_buffer_stats()
        
        if stats['total_samples'] == 0:
            console.print("[yellow]Buffer is already empty[/yellow]")
            return
        
        if not force:
            console.print(f"[yellow]About to delete {stats['total_samples']} samples[/yellow]")
            confirm = typer.confirm("Are you sure?")
            if not confirm:
                console.print("Aborted")
                return
        
        count = collector.delete()
        console.print(f"[green]✓ Deleted {count} samples[/green]")


@config_app.command("set")
def config_set(
    key: str = typer.Argument(..., help="Config key (e.g., telemetry.opt_in)"),
    value: str = typer.Argument(..., help="Config value"),
):
    """
    Set configuration value.
    
    Examples:
      animica config set telemetry.opt_in true
      animica config set telemetry.opt_in false
      animica config set telemetry.collect_prompts false
    """
    # Parse key
    parts = key.split(".")
    
    if parts[0] == "telemetry":
        config = load_telemetry_config()
        
        if len(parts) != 2:
            console.print(f"[red]Invalid key: {key}[/red]")
            console.print("Expected format: telemetry.<field>")
            raise typer.Exit(1)
        
        field = parts[1]
        
        # Parse value
        if value.lower() in ("true", "1", "yes"):
            parsed_value = True
        elif value.lower() in ("false", "0", "no"):
            parsed_value = False
        else:
            try:
                parsed_value = int(value)
            except ValueError:
                parsed_value = value
        
        # Set field
        if not hasattr(config, field):
            console.print(f"[red]Unknown field: {field}[/red]")
            raise typer.Exit(1)
        
        setattr(config, field, parsed_value)
        save_telemetry_config(config)
        
        console.print(f"[green]✓ Set {key} = {parsed_value}[/green]")
        
        # Special handling for opt_in
        if field == "opt_in":
            if parsed_value:
                # Generate user ID hash
                import hashlib
                import uuid
                user_id = str(uuid.uuid4())
                user_id_hash = hashlib.sha256(user_id.encode()).hexdigest()
                config.user_id_hash = user_id_hash
                from datetime import datetime
                config.collection_start_date = datetime.utcnow().isoformat()
                save_telemetry_config(config)
                
                console.print("\n[bold green]Thank you for helping improve ENA![/bold green]")
                console.print("Your data will be redacted and you can inspect/delete it at any time.")
                console.print("\nCommands:")
                console.print("  animica data inspect       - View collected samples")
                console.print("  animica data curate --auto - Upload approved samples")
                console.print("  animica data clear         - Delete all samples")
            else:
                console.print("\n[yellow]Telemetry disabled[/yellow]")
                console.print("To delete your buffer: animica data clear")
    else:
        console.print(f"[red]Unknown config section: {parts[0]}[/red]")
        raise typer.Exit(1)


@config_app.command("get")
def config_get(
    key: str = typer.Argument(..., help="Config key (e.g., telemetry.opt_in)"),
):
    """Get configuration value."""
    # Parse key
    parts = key.split(".")
    
    if parts[0] == "telemetry":
        config = load_telemetry_config()
        
        if len(parts) == 1:
            # Show all telemetry config
            console.print(Panel(
                json.dumps(config.to_dict(), indent=2),
                title="Telemetry Configuration",
                border_style="cyan",
            ))
        else:
            field = parts[1]
            if not hasattr(config, field):
                console.print(f"[red]Unknown field: {field}[/red]")
                raise typer.Exit(1)
            
            value = getattr(config, field)
            console.print(f"{key} = {value}")
    else:
        console.print(f"[red]Unknown config section: {parts[0]}[/red]")
        raise typer.Exit(1)


@config_app.command("show")
def config_show():
    """Show all configuration."""
    config = load_telemetry_config()
    
    console.print(Panel(
        json.dumps(config.to_dict(), indent=2),
        title="ENA Configuration",
        border_style="cyan",
    ))


if __name__ == "__main__":
    app()
