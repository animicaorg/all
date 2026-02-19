"""
Animica DA • Provider CLI

Commands for managing storage provider operations.
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
    print("Error: typer and rich are required for provider CLI", file=sys.stderr)
    sys.exit(1)

try:
    from da.provider.registry import (
        DEFAULT_REGISTRY_DB,
        DEFAULT_REPLICATION_FACTOR,
        DEFAULT_UPTIME_SCORE,
        MAX_UPTIME_SCORE,
        ProviderEntry,
        ProviderRegistry,
        create_provider_entry,
        create_provider_id,
    )
    from da.provider.service import ProviderService
except ImportError as e:
    print(f"Error importing DA provider modules: {e}", file=sys.stderr)
    sys.exit(1)


console = Console()
provider_app = typer.Typer(
    name="provider",
    help="Storage provider management commands",
    no_args_is_help=True,
)


def _get_registry(db_path: Optional[Path] = None) -> ProviderRegistry:
    """Get or create provider registry."""
    return ProviderRegistry(db_path=db_path)


def _parse_capacity(capacity_str: str) -> int:
    """Parse capacity string like '100GB', '1TB', '500MB' to bytes."""
    capacity_str = capacity_str.strip().upper()
    
    multipliers = {
        "B": 1,
        "KB": 1000,
        "MB": 1000**2,
        "GB": 1000**3,
        "TB": 1000**4,
        "KIB": 1024,
        "MIB": 1024**2,
        "GIB": 1024**3,
        "TIB": 1024**4,
    }
    
    for suffix, mult in multipliers.items():
        if capacity_str.endswith(suffix):
            value = float(capacity_str[:-len(suffix)])
            return int(value * mult)
    
    # Assume bytes if no suffix
    try:
        return int(capacity_str)
    except ValueError:
        raise ValueError(f"Invalid capacity format: {capacity_str}")


def _load_or_generate_keypair(
    keystore_path: Optional[Path] = None,
) -> tuple[bytes, bytes]:
    """
    Load or generate a keypair for the provider.
    Returns (pubkey, privkey).
    
    In production, this would use actual PQ keypair generation.
    For now, we use a simple placeholder.
    """
    import hashlib
    
    if keystore_path is None:
        keystore_path = Path.home() / ".animica" / "provider_key.json"
    
    keystore_path.parent.mkdir(parents=True, exist_ok=True)
    
    if keystore_path.exists():
        # Load existing keypair
        with open(keystore_path, "r") as f:
            data = json.load(f)
            pubkey = bytes.fromhex(data["pubkey"])
            privkey = bytes.fromhex(data["privkey"])
            return pubkey, privkey
    else:
        # Generate new keypair (placeholder implementation)
        # In production, use actual Dilithium3 keypair generation
        import secrets
        
        privkey = secrets.token_bytes(32)
        pubkey = hashlib.sha3_256(privkey).digest() + secrets.token_bytes(32)
        
        # Save to keystore
        with open(keystore_path, "w") as f:
            json.dump(
                {
                    "pubkey": pubkey.hex(),
                    "privkey": privkey.hex(),
                },
                f,
                indent=2,
            )
        
        console.print(f"[green]Generated new keypair and saved to {keystore_path}[/green]")
        return pubkey, privkey


@provider_app.command("register")
def register(
    path: Path = typer.Option(
        ...,
        "--path",
        help="Storage path for blobs",
    ),
    capacity: str = typer.Option(
        ...,
        "--capacity",
        help="Storage capacity (e.g., '100GB', '1TB')",
    ),
    endpoint: str = typer.Option(
        ...,
        "--endpoint",
        help="HTTP(S) endpoint URL",
    ),
    address: Optional[str] = typer.Option(
        None,
        "--address",
        help="Payment address (20-byte hex), auto-generated if not provided",
    ),
    region: Optional[str] = typer.Option(
        None,
        "--region",
        help="Comma-separated region tags (e.g., 'us-west,ssd')",
    ),
    db_path: Optional[Path] = typer.Option(
        None,
        "--db",
        help=f"Registry database path (default: {DEFAULT_REGISTRY_DB})",
    ),
    keystore: Optional[Path] = typer.Option(
        None,
        "--keystore",
        help="Keystore path (default: ~/.animica/provider_key.json)",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
) -> None:
    """Register as a storage provider."""
    try:
        # Parse capacity
        capacity_bytes = _parse_capacity(capacity)
        
        # Load or generate keypair
        pubkey, _ = _load_or_generate_keypair(keystore)
        
        # Generate address if not provided
        if address is None:
            import hashlib
            address_bytes = hashlib.sha3_256(pubkey).digest()[:20]
        else:
            address_hex = address.strip()
            if address_hex.startswith("0x"):
                address_hex = address_hex[2:]
            if len(address_hex) != 40:
                console.print("[red]Error: Address must be 20 bytes (40 hex chars)[/red]")
                raise typer.Exit(1)
            address_bytes = bytes.fromhex(address_hex)
        
        # Parse region tags
        region_tags = []
        if region:
            region_tags = [tag.strip() for tag in region.split(",")]
        
        # Create provider entry
        provider_id = create_provider_id(pubkey)
        entry = create_provider_entry(
            pubkey=pubkey,
            address=address_bytes,
            endpoint=endpoint,
            capacity_bytes=capacity_bytes,
            region_tags=region_tags,
        )
        
        # Register in database
        registry = _get_registry(db_path)
        registry.register_provider(entry)
        
        if json_output:
            print(
                json.dumps(
                    {
                        "provider_id": provider_id.hex(),
                        "pubkey": pubkey.hex(),
                        "address": address_bytes.hex(),
                        "endpoint": endpoint,
                        "capacity_bytes": capacity_bytes,
                        "region_tags": region_tags,
                        "uptime_score": entry.uptime_score,
                        "registered_at": entry.registered_at,
                    },
                    indent=2,
                )
            )
        else:
            console.print("[green]✓[/green] Provider registered successfully")
            console.print(f"Provider ID: {provider_id.hex()}")
            console.print(f"Endpoint: {endpoint}")
            console.print(f"Capacity: {capacity_bytes:,} bytes")
            console.print(f"Storage Path: {path}")
            if region_tags:
                console.print(f"Regions: {', '.join(region_tags)}")
    
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@provider_app.command("status")
def status(
    db_path: Optional[Path] = typer.Option(
        None,
        "--db",
        help=f"Registry database path (default: {DEFAULT_REGISTRY_DB})",
    ),
    keystore: Optional[Path] = typer.Option(
        None,
        "--keystore",
        help="Keystore path (default: ~/.animica/provider_key.json)",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
) -> None:
    """Show provider status."""
    try:
        # Load keypair to get provider ID
        pubkey, _ = _load_or_generate_keypair(keystore)
        provider_id = create_provider_id(pubkey)
        
        # Get provider entry
        registry = _get_registry(db_path)
        entry = registry.get_provider(provider_id)
        
        if entry is None:
            console.print("[yellow]Provider not registered[/yellow]")
            raise typer.Exit(1)
        
        if json_output:
            print(
                json.dumps(
                    {
                        "provider_id": provider_id.hex(),
                        "endpoint": entry.endpoint,
                        "capacity_advertised": entry.capacity_bytes_advertised,
                        "capacity_committed": entry.capacity_bytes_committed,
                        "capacity_available": entry.capacity_bytes_advertised
                        - entry.capacity_bytes_committed,
                        "uptime_score": entry.uptime_score,
                        "uptime_percentage": f"{entry.uptime_score / 100:.2f}%",
                        "last_heartbeat": entry.last_heartbeat,
                        "registered_at": entry.registered_at,
                        "active": entry.active,
                        "region_tags": entry.region_tags,
                    },
                    indent=2,
                )
            )
        else:
            table = Table(title="Provider Status")
            table.add_column("Field", style="cyan")
            table.add_column("Value", style="white")
            
            table.add_row("Provider ID", provider_id.hex())
            table.add_row("Endpoint", entry.endpoint or "(not set)")
            table.add_row(
                "Capacity (Advertised)",
                f"{entry.capacity_bytes_advertised:,} bytes",
            )
            table.add_row(
                "Capacity (Committed)",
                f"{entry.capacity_bytes_committed:,} bytes",
            )
            table.add_row(
                "Capacity (Available)",
                f"{entry.capacity_bytes_advertised - entry.capacity_bytes_committed:,} bytes",
            )
            table.add_row(
                "Uptime Score", f"{entry.uptime_score}/{MAX_UPTIME_SCORE} ({entry.uptime_score / 100:.2f}%)"
            )
            table.add_row(
                "Last Heartbeat",
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(entry.last_heartbeat)),
            )
            table.add_row(
                "Registered",
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(entry.registered_at)),
            )
            table.add_row("Active", "Yes" if entry.active else "No")
            if entry.region_tags:
                table.add_row("Regions", ", ".join(entry.region_tags))
            
            console.print(table)
    
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@provider_app.command("heartbeat")
def heartbeat(
    db_path: Optional[Path] = typer.Option(
        None,
        "--db",
        help=f"Registry database path (default: {DEFAULT_REGISTRY_DB})",
    ),
    keystore: Optional[Path] = typer.Option(
        None,
        "--keystore",
        help="Keystore path (default: ~/.animica/provider_key.json)",
    ),
) -> None:
    """Send heartbeat to update last_heartbeat timestamp."""
    try:
        # Load keypair to get provider ID
        pubkey, _ = _load_or_generate_keypair(keystore)
        provider_id = create_provider_id(pubkey)
        
        # Update heartbeat
        registry = _get_registry(db_path)
        now = int(time.time())
        registry.update_heartbeat(provider_id, now)
        
        console.print(f"[green]✓[/green] Heartbeat updated at {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now))}")
    
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@provider_app.command("list")
def list_providers(
    db_path: Optional[Path] = typer.Option(
        None,
        "--db",
        help=f"Registry database path (default: {DEFAULT_REGISTRY_DB})",
    ),
    active_only: bool = typer.Option(
        False,
        "--active-only",
        help="Show only active providers",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
) -> None:
    """List all registered providers."""
    try:
        registry = _get_registry(db_path)
        providers = registry.list_providers(active_only=active_only)
        
        if not providers:
            console.print("[yellow]No providers registered[/yellow]")
            return
        
        if json_output:
            output = []
            for provider_id, entry in providers:
                output.append(
                    {
                        "provider_id": provider_id.hex(),
                        "endpoint": entry.endpoint,
                        "capacity_advertised": entry.capacity_bytes_advertised,
                        "capacity_committed": entry.capacity_bytes_committed,
                        "uptime_score": entry.uptime_score,
                        "active": entry.active,
                        "region_tags": entry.region_tags,
                    }
                )
            print(json.dumps(output, indent=2))
        else:
            table = Table(title="Registered Providers")
            table.add_column("Provider ID", style="cyan")
            table.add_column("Endpoint", style="white")
            table.add_column("Capacity", style="green")
            table.add_column("Uptime", style="yellow")
            table.add_column("Active", style="magenta")
            
            for provider_id, entry in providers:
                capacity = f"{entry.capacity_bytes_advertised // (1024**3)}GB"
                uptime = f"{entry.uptime_score / 100:.1f}%"
                active = "✓" if entry.active else "✗"
                
                table.add_row(
                    provider_id.hex()[:16] + "...",
                    entry.endpoint or "(not set)",
                    capacity,
                    uptime,
                    active,
                )
            
            console.print(table)
            console.print(f"\nTotal providers: {len(providers)}")
            
            # Show total capacity
            total_adv, total_comm = registry.get_total_capacity()
            console.print(
                f"Total capacity: {total_adv // (1024**3)}GB advertised, "
                f"{total_comm // (1024**3)}GB committed"
            )
    
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@provider_app.command("sync")
def sync_blobs(
    path: Path = typer.Option(
        ...,
        "--path",
        help="Storage path for blobs",
    ),
    da_url: str = typer.Option(
        "http://127.0.0.1:8648",
        "--da-url",
        help="DA service URL to fetch blobs from",
    ),
    db_path: Optional[Path] = typer.Option(
        None,
        "--db",
        help=f"Registry database path (default: {DEFAULT_REGISTRY_DB})",
    ),
    keystore: Optional[Path] = typer.Option(
        None,
        "--keystore",
        help="Keystore path (default: ~/.animica/provider_key.json)",
    ),
    verify: bool = typer.Option(
        True,
        "--verify/--no-verify",
        help="Verify blob hashes after download",
    ),
) -> None:
    """
    Sync assigned blobs from DA to local storage.
    
    This command:
    1. Fetches all assigned blobs for this provider
    2. Downloads missing blobs from DA service
    3. Stores in local content-addressed storage
    4. Verifies hashes match assignments
    """
    try:
        import hashlib
        
        # Load keypair to get provider ID
        pubkey, _ = _load_or_generate_keypair(keystore)
        provider_id = create_provider_id(pubkey)
        
        # Get assignments
        registry = _get_registry(db_path)
        assignments = registry.get_assignments_for_provider(provider_id)
        
        if not assignments:
            console.print("[yellow]No blob assignments found[/yellow]")
            return
        
        console.print(f"Found {len(assignments)} blob assignment(s)")
        
        # Create provider service for local storage
        service = ProviderService(storage_path=path)
        
        # Sync each blob
        synced = 0
        skipped = 0
        errors = 0
        verified = 0
        
        for assignment in assignments:
            commit_hex = assignment.blob_commitment.hex()
            
            # Check if already exists
            if service.has_blob(assignment.blob_commitment):
                # Verify existing blob if requested
                if verify:
                    try:
                        existing_data = service.get_blob(assignment.blob_commitment)
                        actual_hash = hashlib.sha3_256(existing_data).digest()
                        if actual_hash == assignment.blob_commitment:
                            verified += 1
                        else:
                            console.print(
                                f"[yellow]⚠[/yellow] Hash mismatch for {commit_hex[:16]}... (re-downloading)"
                            )
                            # Re-download if hash mismatch
                            service.delete_blob(assignment.blob_commitment)
                    except Exception as e:
                        console.print(
                            f"[yellow]⚠[/yellow] Failed to verify {commit_hex[:16]}...: {e}"
                        )
                else:
                    skipped += 1
                    continue
            
            # Fetch from DA service
            try:
                import requests
                
                url = f"{da_url.rstrip('/')}/da/blob/0x{commit_hex}"
                response = requests.get(url, timeout=30)
                response.raise_for_status()
                
                blob_data = response.content
                
                # Verify hash before storing
                if verify:
                    actual_hash = hashlib.sha3_256(blob_data).digest()
                    if actual_hash != assignment.blob_commitment:
                        raise ValueError(
                            f"Hash mismatch: expected {commit_hex}, "
                            f"got {actual_hash.hex()}"
                        )
                    verified += 1
                
                # Store locally in content-addressed storage
                service.store_blob(assignment.blob_commitment, blob_data)
                synced += 1
                
                size_mb = len(blob_data) / (1024 * 1024)
                console.print(
                    f"[green]✓[/green] Synced {commit_hex[:16]}... "
                    f"({size_mb:.2f} MB)"
                )
            
            except Exception as e:
                errors += 1
                console.print(f"[red]✗[/red] Failed to sync {commit_hex[:16]}...: {e}")
        
        console.print(f"\nSync complete:")
        console.print(f"  {synced} synced")
        console.print(f"  {skipped} skipped")
        if verify:
            console.print(f"  {verified} verified")
        console.print(f"  {errors} errors")
    
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


# Plan management commands
plan_app = typer.Typer(
    name="plan",
    help="Provider plan management",
    no_args_is_help=True,
)
provider_app.add_typer(plan_app, name="plan")


@plan_app.command("apply")
def plan_apply(
    plan_name: str = typer.Argument(..., help="Plan name (starter-100gb, serious-1tb, datacenter-10tb)"),
    path: Path = typer.Option(
        ...,
        "--path",
        help="Storage path for provider",
    ),
    keystore: Optional[Path] = typer.Option(
        None,
        "--keystore",
        help="Keystore path (default: ~/.animica/provider_key.json)",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
) -> None:
    """Apply a plan to this provider."""
    try:
        from aicf.credits.plans import apply_plan, format_capacity
        
        # Load keypair to get provider ID
        pubkey, _ = _load_or_generate_keypair(keystore)
        provider_id = create_provider_id(pubkey)
        
        # Apply plan
        plan_info = apply_plan(provider_id, plan_name)
        
        if json_output:
            print(
                json.dumps(
                    {
                        "provider_id": provider_id.hex(),
                        "plan_name": plan_info.name,
                        "capacity": plan_info.capacity_bytes,
                        "heartbeat_interval": plan_info.heartbeat_interval_seconds,
                        "audit_target": plan_info.audit_target_per_day,
                        "port": plan_info.port,
                    },
                    indent=2,
                )
            )
        else:
            console.print(f"[green]✓[/green] Applied plan: {plan_info.name}")
            console.print(f"\n[bold]Plan Configuration:[/bold]")
            console.print(f"  Capacity: {format_capacity(plan_info.capacity_bytes)}")
            console.print(f"  Heartbeat Interval: {plan_info.heartbeat_interval_seconds}s")
            console.print(f"  Audit Target: {plan_info.audit_target_per_day} audits/day")
            console.print(f"  Port: {plan_info.port}")
            console.print(f"\n[dim]Storage path: {path}[/dim]")
    
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@plan_app.command("info")
def plan_info_cmd(
    plan_name: str = typer.Argument(..., help="Plan name"),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
) -> None:
    """Show information about a specific plan."""
    try:
        from aicf.credits.plans import get_plan_info, format_capacity
        
        plan_info = get_plan_info(plan_name)
        
        if json_output:
            print(
                json.dumps(
                    {
                        "name": plan_info.name,
                        "capacity": plan_info.capacity_bytes,
                        "heartbeat_interval": plan_info.heartbeat_interval_seconds,
                        "audit_target": plan_info.audit_target_per_day,
                        "port": plan_info.port,
                    },
                    indent=2,
                )
            )
        else:
            console.print(f"[bold]Plan: {plan_info.name}[/bold]\n")
            console.print(f"  Capacity: {format_capacity(plan_info.capacity_bytes)}")
            console.print(f"  Heartbeat Interval: {plan_info.heartbeat_interval_seconds}s")
            console.print(f"  Audit Target: {plan_info.audit_target_per_day} audits/day")
            console.print(f"  Port: {plan_info.port}")
    
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@plan_app.command("list")
def plan_list(
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
) -> None:
    """List all available plans."""
    try:
        from aicf.credits.plans import list_plans, format_capacity
        
        plans = list_plans()
        
        if json_output:
            print(
                json.dumps(
                    [
                        {
                            "name": p.name,
                            "capacity": p.capacity_bytes,
                            "heartbeat_interval": p.heartbeat_interval_seconds,
                            "audit_target": p.audit_target_per_day,
                            "port": p.port,
                        }
                        for p in plans
                    ],
                    indent=2,
                )
            )
        else:
            table = Table(title="Available Plans")
            table.add_column("Name", style="cyan")
            table.add_column("Capacity", justify="right")
            table.add_column("Heartbeat", justify="right")
            table.add_column("Audits/Day", justify="right")
            table.add_column("Port", justify="right")
            
            for p in plans:
                table.add_row(
                    p.name,
                    format_capacity(p.capacity_bytes),
                    f"{p.heartbeat_interval_seconds}s",
                    str(p.audit_target_per_day),
                    str(p.port),
                )
            
            console.print(table)
            console.print(f"\n[dim]Apply a plan with: animica da provider plan apply <plan_name> --path <storage_path>[/dim]")
    
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


# Alerts commands
alerts_app = typer.Typer(
    name="alerts",
    help="Provider alert monitoring",
    no_args_is_help=True,
)
provider_app.add_typer(alerts_app, name="alerts")


@provider_app.command("alerts")
def alerts_cmd(
    watch: bool = typer.Option(
        False,
        "--watch",
        help="Watch mode (refresh every 10s)",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
    keystore: Optional[Path] = typer.Option(
        None,
        "--keystore",
        help="Keystore path (default: ~/.animica/provider_key.json)",
    ),
) -> None:
    """Show active alerts for this provider."""
    try:
        from aicf.credits.alerts import get_active_alerts
        
        # Load keypair to get provider ID
        pubkey, _ = _load_or_generate_keypair(keystore)
        provider_id = create_provider_id(pubkey)
        
        if watch:
            console.print("[bold]Provider Alerts (Watch Mode)[/bold]")
            console.print("[dim]Press Ctrl+C to stop[/dim]\n")
            
            try:
                while True:
                    alerts = get_active_alerts(provider_id)
                    
                    console.print(f"[dim]{time.strftime('%Y-%m-%d %H:%M:%S')}[/dim]")
                    
                    if not alerts:
                        console.print("[green]✓ No active alerts[/green]\n")
                    else:
                        for alert in alerts:
                            severity_color = "red" if alert.severity == "critical" else "yellow"
                            console.print(f"[{severity_color}]• {alert.alert_type.value}:[/{severity_color}] {alert.message}")
                        console.print()
                    
                    time.sleep(10)
            
            except KeyboardInterrupt:
                console.print("\n[dim]Stopped monitoring.[/dim]")
                raise typer.Exit(0)
        
        else:
            alerts = get_active_alerts(provider_id)
            
            if json_output:
                print(
                    json.dumps(
                        [
                            {
                                "type": a.alert_type.value,
                                "severity": a.severity,
                                "message": a.message,
                                "created_at": a.created_at,
                            }
                            for a in alerts
                        ],
                        indent=2,
                    )
                )
            else:
                if not alerts:
                    console.print("[green]✓ No active alerts[/green]")
                else:
                    console.print(f"[bold]Active Alerts ({len(alerts)}):[/bold]\n")
                    
                    table = Table()
                    table.add_column("Type", style="cyan")
                    table.add_column("Severity")
                    table.add_column("Message")
                    table.add_column("Created")
                    
                    for alert in alerts:
                        severity_style = "red" if alert.severity == "critical" else "yellow"
                        created = time.strftime('%Y-%m-%d %H:%M', time.localtime(alert.created_at))
                        table.add_row(
                            alert.alert_type.value,
                            f"[{severity_style}]{alert.severity}[/{severity_style}]",
                            alert.message,
                            created,
                        )
                    
                    console.print(table)
    
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


def main() -> None:
    """CLI entry point."""
    provider_app()


if __name__ == "__main__":
    main()
