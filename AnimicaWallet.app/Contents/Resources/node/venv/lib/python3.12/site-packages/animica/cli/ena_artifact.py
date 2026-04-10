"""
ENA Artifact CLI - Commands for artifact verification and management.

Provides commands for:
- Verifying artifact manifests
- Inspecting artifacts
- Listing artifacts
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

# Add ena module to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

try:
    from ena.artifacts import (
        ArtifactManifest,
        ArtifactType,
        DatasetManifest,
        EvalReportManifest,
        ModelCheckpointManifest,
        RewardDataManifest,
        IndexShardManifest,
        hash_artifact,
        verify_artifact,
        ArtifactVerifier,
    )
except ImportError as e:
    console = Console()
    console.print(f"[red]Error: Could not import ENA artifacts: {e}[/red]")
    ArtifactManifest = None  # type: ignore

console = Console()
app = typer.Typer(help="Artifact verification and management commands")


def _load_manifest_from_file(manifest_path: Path) -> Optional[ArtifactManifest]:
    """Load artifact manifest from JSON file."""
    try:
        if not manifest_path.exists():
            console.print(f"[red]Error: File not found: {manifest_path}[/red]")
            return None
        
        data = json.loads(manifest_path.read_text())
        
        # Determine type and create appropriate manifest
        artifact_type = data.get("type", "")
        
        if artifact_type == "dataset_shard":
            return DatasetManifest(**data)
        elif artifact_type == "eval_report":
            return EvalReportManifest(**data)
        elif artifact_type == "model_checkpoint":
            return ModelCheckpointManifest(**data)
        elif artifact_type == "reward_data":
            return RewardDataManifest(**data)
        elif artifact_type == "index_shard":
            return IndexShardManifest(**data)
        else:
            # Generic manifest
            data["type"] = ArtifactType(artifact_type)
            return ArtifactManifest(**data)
    
    except Exception as e:
        console.print(f"[red]Error loading manifest: {e}[/red]")
        return None


@app.command("verify")
def verify_artifact_cmd(
    manifest_path: Path = typer.Argument(
        ...,
        help="Path to artifact manifest JSON file",
        exists=True,
    ),
    data_path: Optional[Path] = typer.Option(
        None,
        "--data",
        help="Path to artifact data file for spot-checking",
    ),
    expected_hash: Optional[str] = typer.Option(
        None,
        "--hash",
        help="Expected artifact hash",
    ),
    sample_size: int = typer.Option(
        10,
        "--samples",
        help="Number of samples to check (if data provided)",
    ),
    check_provenance: bool = typer.Option(
        True,
        "--provenance/--no-provenance",
        help="Verify input provenance chain",
    ),
):
    """
    Verify an artifact manifest.
    
    Performs:
    - Hash verification
    - Schema validation
    - Spot-checking samples (if data provided)
    - Provenance validation
    
    Examples:
        animica ena artifact verify manifest.json
        animica ena artifact verify manifest.json --data dataset.json --samples 20
        animica ena artifact verify manifest.json --hash abc123...
    """
    # Load manifest
    manifest = _load_manifest_from_file(manifest_path)
    if manifest is None:
        raise typer.Exit(1)
    
    # Load data if provided
    data = None
    if data_path:
        try:
            data = json.loads(data_path.read_text())
        except Exception as e:
            console.print(f"[red]Error loading data: {e}[/red]")
            raise typer.Exit(1)
    
    # Create verifier and verify
    verifier = ArtifactVerifier(sample_size=sample_size)
    result = verifier.verify(
        manifest,
        data=data,
        check_provenance=check_provenance,
    )
    
    # Display results
    if result.is_valid:
        console.print(Panel(
            f"[green]✓ Artifact verified successfully[/green]\n\n"
            f"Artifact ID: {result.artifact_id}\n"
            f"Samples checked: {result.samples_checked}\n"
            f"Message: {result.message}",
            title="Verification Result",
            border_style="green",
        ))
    else:
        error_text = f"[red]✗ Verification failed[/red]\n\n"
        error_text += f"Artifact ID: {result.artifact_id}\n"
        error_text += f"Status: {result.status.value}\n"
        error_text += f"Message: {result.message}\n"
        
        if result.errors:
            error_text += f"\nErrors:\n"
            for err in result.errors:
                error_text += f"  - {err}\n"
        
        console.print(Panel(
            error_text,
            title="Verification Result",
            border_style="red",
        ))
        raise typer.Exit(1)


@app.command("inspect")
def inspect_artifact(
    manifest_path: Path = typer.Argument(
        ...,
        help="Path to artifact manifest JSON file",
        exists=True,
    ),
):
    """
    Inspect an artifact manifest and display its details.
    
    Examples:
        animica ena artifact inspect manifest.json
    """
    # Load manifest
    manifest = _load_manifest_from_file(manifest_path)
    if manifest is None:
        raise typer.Exit(1)
    
    # Display manifest details
    table = Table(title=f"Artifact Manifest: {manifest.artifact_id[:16]}...")
    table.add_column("Field", style="cyan")
    table.add_column("Value", style="white")
    
    table.add_row("Artifact ID", manifest.artifact_id)
    table.add_row("Type", manifest.type.value if isinstance(manifest.type, ArtifactType) else str(manifest.type))
    table.add_row("Created By", manifest.created_by)
    table.add_row("Created At", manifest.created_at)
    table.add_row("Version", manifest.version)
    table.add_row("Inputs", str(len(manifest.inputs)))
    
    # Type-specific fields
    if isinstance(manifest, DatasetManifest):
        table.add_row("Source", manifest.source)
        table.add_row("Shard", f"{manifest.shard_index + 1}/{manifest.total_shards}")
        table.add_row("Samples", str(manifest.num_samples))
        table.add_row("Dedup Method", manifest.dedup_method)
        table.add_row("Safety Filtered", "Yes" if manifest.safety_filtered else "No")
    
    elif isinstance(manifest, EvalReportManifest):
        table.add_row("Model Hash", manifest.model_hash[:16] + "..." if manifest.model_hash else "N/A")
        table.add_row("Eval Suite", manifest.eval_suite)
        table.add_row("Total Score", f"{manifest.total_score:.2f}")
        table.add_row("Pass Rate", f"{manifest.pass_rate * 100:.1f}%")
        table.add_row("Tasks", str(manifest.num_tasks))
    
    elif isinstance(manifest, ModelCheckpointManifest):
        table.add_row("Model Name", manifest.model_name)
        table.add_row("Base Model", manifest.base_model)
        table.add_row("Training Method", manifest.training_method)
        table.add_row("Parameters", f"{manifest.num_parameters:,}")
        table.add_row("Is Delta", "Yes" if manifest.is_delta else "No")
    
    elif isinstance(manifest, RewardDataManifest):
        table.add_row("Pairs", str(manifest.num_pairs))
        table.add_row("Labeling Method", manifest.labeling_method)
        table.add_row("Quality Score", f"{manifest.quality_score:.3f}")
    
    elif isinstance(manifest, IndexShardManifest):
        table.add_row("Index Type", manifest.index_type)
        table.add_row("Vectors", str(manifest.num_vectors))
        table.add_row("Dimension", str(manifest.dimension))
    
    console.print(table)
    
    # Display metrics if present
    if manifest.metrics:
        console.print("\n[bold]Metrics:[/bold]")
        for key, value in manifest.metrics.items():
            console.print(f"  {key}: {value}")
    
    # Display input hashes if present
    if manifest.inputs:
        console.print(f"\n[bold]Inputs ({len(manifest.inputs)}):[/bold]")
        for i, input_hash in enumerate(manifest.inputs[:5], 1):
            console.print(f"  {i}. {input_hash}")
        if len(manifest.inputs) > 5:
            console.print(f"  ... and {len(manifest.inputs) - 5} more")


@app.command("hash")
def compute_hash(
    manifest_path: Path = typer.Argument(
        ...,
        help="Path to artifact manifest JSON file",
        exists=True,
    ),
):
    """
    Compute the hash of an artifact manifest.
    
    Examples:
        animica ena artifact hash manifest.json
    """
    # Load manifest
    manifest = _load_manifest_from_file(manifest_path)
    if manifest is None:
        raise typer.Exit(1)
    
    # Compute hash
    artifact_hash = hash_artifact(manifest)
    
    console.print(f"[bold]Artifact Hash:[/bold] {artifact_hash}")
    
    # Check if hash matches manifest
    if manifest.artifact_id == artifact_hash:
        console.print("[green]✓ Hash matches manifest artifact_id[/green]")
    else:
        console.print("[yellow]⚠ Hash does not match manifest artifact_id[/yellow]")
        console.print(f"  Manifest ID: {manifest.artifact_id}")
        console.print(f"  Computed:    {artifact_hash}")


if __name__ == "__main__":
    app()
