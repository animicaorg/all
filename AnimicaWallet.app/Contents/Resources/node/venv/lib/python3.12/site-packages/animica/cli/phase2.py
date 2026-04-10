"""
Phase 2 AICF+ENA CLI Commands
==============================

GPU provider registration, compute receipts, payout claims, and training receipts.
"""

from __future__ import annotations

import json
import os
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

console = Console()

phase2_app = typer.Typer(
    name="phase2",
    help="Phase 2: GPU providers, receipts, and payout commands",
    no_args_is_help=True,
)

# Sub-apps for organization
provider_app = typer.Typer(
    name="provider",
    help="GPU provider management commands",
    no_args_is_help=True,
)

ena_app = typer.Typer(
    name="ena",
    help="ENA compute receipt commands",
    no_args_is_help=True,
)

payout_app = typer.Typer(
    name="payout",
    help="Provider payout and reward commands",
    no_args_is_help=True,
)

training_app = typer.Typer(
    name="training",
    help="Training receipt commands (mining→AI link)",
    no_args_is_help=True,
)

# Mount sub-apps
phase2_app.add_typer(provider_app, name="provider")
phase2_app.add_typer(ena_app, name="ena")
phase2_app.add_typer(payout_app, name="payout")
phase2_app.add_typer(training_app, name="training")


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


def _format_amount(amount_nano: int) -> str:
    """Format nANM amount as ANM with proper decimal places."""
    anm = amount_nano / 1_000_000_000
    return f"{anm:,.9f}".rstrip('0').rstrip('.')


# ============================================================================
# Provider Commands
# ============================================================================

@provider_app.command("register")
def provider_register(
    address: str = typer.Argument(..., help="Provider address"),
    caps_file: str = typer.Option(
        None,
        "--caps",
        help="Path to JSON file with GPU capabilities",
    ),
    model_family: str = typer.Option(
        None,
        "--model-family",
        help="GPU model family (e.g., nvidia-h100)",
    ),
    max_context: int = typer.Option(
        None,
        "--max-context",
        help="Maximum context length (tokens)",
    ),
    throughput: int = typer.Option(
        None,
        "--throughput",
        help="Tokens per second",
    ),
    memory_gb: int = typer.Option(
        None,
        "--memory-gb",
        help="VRAM in GB",
    ),
    payout_address: Optional[str] = typer.Option(
        None,
        "--payout-addr",
        help="Payout address (defaults to provider address)",
    ),
    bond: Optional[int] = typer.Option(
        None,
        "--bond",
        help="Bond amount in nano-ANM",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
):
    """Register as an AICF GPU provider."""
    try:
        # Build capabilities object
        if caps_file:
            with open(caps_file, 'r') as f:
                capabilities = json.load(f)
        else:
            if not all([model_family, max_context, throughput, memory_gb]):
                console.print("[red]Error: Either provide --caps file or all of --model-family, --max-context, --throughput, --memory-gb[/red]")
                raise typer.Exit(1)
            
            capabilities = {
                "model_family": model_family,
                "max_context": max_context,
                "throughput": throughput,
                "memory_gb": memory_gb,
            }
        
        # Call RPC
        params = [address, capabilities]
        if payout_address:
            params.append(payout_address)
        if bond is not None:
            if payout_address is None:
                params.append(None)  # Placeholder for payout_address
            params.append(bond)
        
        result = _rpc_call("aicf.registerProvider", params)
        
        if json_output:
            console.print(json.dumps(result, indent=2))
        else:
            console.print(f"[green]✓[/green] Provider registered successfully!")
            console.print(f"  Provider ID: {result['provider_id']}")
            console.print(f"  Status: {result['status']}")
            console.print(f"  Registered at: {result['registered_at']}")
            if result.get('bond_required'):
                console.print(f"  Bond Required: {_format_amount(result['bond_required'])} ANM")
    
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@provider_app.command("status")
def provider_status(
    provider_id: str = typer.Argument(..., help="Provider ID"),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
):
    """Get provider status and reputation."""
    try:
        result = _rpc_call("aicf.getProvider", [provider_id])
        
        if json_output:
            console.print(json.dumps(result, indent=2))
        else:
            console.print(f"[bold]Provider {result['id']}[/bold]")
            console.print(f"  Status: {result['status']}")
            console.print(f"  Payout Address: {result.get('payout_address', 'N/A')}")
            console.print(f"  Stake: {_format_amount(result['stake'])} ANM")
            console.print(f"  Bond: {_format_amount(result.get('bond', 0))} ANM")
            
            # GPU Capabilities
            caps = result.get('capabilities', {})
            if caps:
                console.print(f"\n[bold]GPU Capabilities:[/bold]")
                console.print(f"  Model Family: {caps.get('model_family', 'N/A')}")
                console.print(f"  Max Context: {caps.get('max_context', 'N/A')} tokens")
                console.print(f"  Throughput: {caps.get('throughput', 'N/A')} tokens/sec")
                console.print(f"  VRAM: {caps.get('memory_gb', 'N/A')} GB")
            
            # Reputation
            rep = result.get('reputation', {})
            if rep:
                console.print(f"\n[bold]Reputation:[/bold]")
                console.print(f"  Success Rate: {rep.get('success_rate', 0) * 100:.2f}%")
                console.print(f"  Successful Jobs: {rep.get('successful_jobs', 0)}")
                console.print(f"  Failed Jobs: {rep.get('failed_jobs', 0)}")
                console.print(f"  Avg Latency: {rep.get('avg_latency_ms', 0):.2f} ms")
                console.print(f"  Overall Score: {rep.get('overall_score', 0) * 100:.2f}/100")
    
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@provider_app.command("list")
def provider_list(
    offset: int = typer.Option(0, "--offset", help="Pagination offset"),
    limit: int = typer.Option(100, "--limit", help="Results per page"),
    status_filter: Optional[str] = typer.Option(
        None,
        "--status",
        help="Filter by status (active, jailed, etc.)",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
):
    """List registered GPU providers."""
    try:
        params = [offset, limit]
        if status_filter:
            params.append(status_filter)
        
        result = _rpc_call("aicf.listProviders", params)
        
        if json_output:
            console.print(json.dumps(result, indent=2))
        else:
            providers = result.get('providers', [])
            console.print(f"[bold]Registered Providers ({result.get('total', 0)} total)[/bold]\n")
            
            if not providers:
                console.print("[dim]No providers found.[/dim]")
                return
            
            table = Table(show_header=True, header_style="bold")
            table.add_column("Provider ID", style="cyan")
            table.add_column("Status")
            table.add_column("Model Family")
            table.add_column("Success Rate")
            table.add_column("Jobs")
            table.add_column("Score")
            
            for p in providers:
                rep = p.get('reputation', {})
                caps = p.get('capabilities', {})
                table.add_row(
                    p['id'][:16] + "...",
                    p['status'],
                    caps.get('model_family', 'N/A'),
                    f"{rep.get('success_rate', 0) * 100:.1f}%",
                    str(rep.get('successful_jobs', 0)),
                    f"{rep.get('overall_score', 0) * 100:.0f}/100",
                )
            
            console.print(table)
    
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


# ============================================================================
# ENA Receipt Commands
# ============================================================================

@ena_app.command("quote")
def ena_quote(
    tokens_in: int = typer.Option(..., "--tokens-in", help="Input tokens"),
    tokens_out: int = typer.Option(..., "--tokens-out", help="Output tokens"),
    model_id: Optional[str] = typer.Option(
        None,
        "--model",
        help="Model ID (defaults to current ENA model)",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
):
    """Get fee quote for an ENA inference call."""
    try:
        params = [tokens_in, tokens_out]
        if model_id:
            params.append(model_id)
        
        result = _rpc_call("ena.getQuote", params)
        
        if json_output:
            console.print(json.dumps(result, indent=2))
        else:
            console.print(f"[bold]ENA Fee Quote:[/bold]")
            console.print(f"  Total Fee: {_format_amount(result['fee_estimate'])} ANM")
            console.print(f"  AICF Cut: {_format_amount(result['aicf_cut'])} ANM")
            console.print(f"  Provider Cut: {_format_amount(result['provider_cut'])} ANM")
            
            providers = result.get('recommended_providers', [])
            if providers:
                console.print(f"\n[bold]Recommended Providers ({len(providers)}):[/bold]")
                table = Table(show_header=True, header_style="bold")
                table.add_column("Provider ID")
                table.add_column("Price/1K In")
                table.add_column("Price/1K Out")
                table.add_column("Availability")
                table.add_column("Est. Latency")
                
                for p in providers[:5]:  # Top 5
                    table.add_row(
                        p['provider_id'][:16] + "...",
                        _format_amount(p.get('price_per_1k_input', 0)),
                        _format_amount(p.get('price_per_1k_output', 0)),
                        f"{p.get('availability_score', 0) * 100:.0f}%",
                        f"{p.get('latency_estimate_ms', 0):.0f} ms",
                    )
                
                console.print(table)
    
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@ena_app.command("submit-receipt")
def ena_submit_receipt(
    receipt_file: str = typer.Argument(..., help="Path to CBOR receipt file"),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
):
    """Submit a compute receipt for anchoring."""
    try:
        # Read CBOR file
        with open(receipt_file, 'rb') as f:
            receipt_bytes = f.read()
        
        # Hex encode for RPC
        receipt_hex = receipt_bytes.hex()
        
        result = _rpc_call("ena.submitReceipt", [receipt_hex])
        
        if json_output:
            console.print(json.dumps(result, indent=2))
        else:
            console.print(f"[green]✓[/green] Receipt submitted successfully!")
            console.print(f"  Receipt Hash: {result['receipt_hash']}")
            console.print(f"  TX Hash: {result['tx_hash']}")
            console.print(f"  Anchored at Height: {result['anchored_at_height']}")
            console.print(f"  Status: {result['status']}")
    
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


# ============================================================================
# Payout Commands
# ============================================================================

@payout_app.command("rewards")
def payout_rewards(
    provider_id: str = typer.Argument(..., help="Provider ID"),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
):
    """Get provider's accrued and claimable rewards."""
    try:
        result = _rpc_call("aicf.getProviderRewards", [provider_id])
        
        if json_output:
            console.print(json.dumps(result, indent=2))
        else:
            console.print(f"[bold]Provider {result['provider_id']}[/bold]")
            console.print(f"  Total Accrued: {_format_amount(result['total_accrued'])} ANM")
            console.print(f"  Total Claimed: {_format_amount(result['total_claimed'])} ANM")
            console.print(f"  [green]Claimable: {_format_amount(result['claimable'])} ANM[/green]")
            
            epochs = result.get('epochs', [])
            if epochs:
                console.print(f"\n[bold]Epoch Breakdown ({len(epochs)} epochs):[/bold]")
                table = Table(show_header=True, header_style="bold")
                table.add_column("Epoch")
                table.add_column("Accrued")
                table.add_column("Claimed")
                table.add_column("Finalized")
                table.add_column("Receipts")
                table.add_column("Tokens")
                
                for ep in epochs[-10:]:  # Last 10 epochs
                    table.add_row(
                        str(ep['epoch']),
                        _format_amount(ep['accrued']),
                        _format_amount(ep['claimed']),
                        "✓" if ep['is_finalized'] else "✗",
                        str(ep['receipt_count']),
                        str(ep['tokens_processed']),
                    )
                
                console.print(table)
    
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@payout_app.command("claim")
def payout_claim(
    provider_id: str = typer.Argument(..., help="Provider ID"),
    to_addr: str = typer.Option(..., "--to", help="Destination address"),
    amount: int = typer.Option(..., "--amount", help="Amount to claim (nano-ANM)"),
    epochs: Optional[str] = typer.Option(
        None,
        "--epochs",
        help="Comma-separated list of epochs to claim from",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
):
    """Claim accrued provider rewards."""
    try:
        params = [provider_id, to_addr, amount]
        if epochs:
            epoch_list = [int(e.strip()) for e in epochs.split(',')]
            params.append(epoch_list)
        
        result = _rpc_call("aicf.claimProviderRewards", params)
        
        if json_output:
            console.print(json.dumps(result, indent=2))
        else:
            console.print(f"[green]✓[/green] Claim transaction prepared!")
            console.print(f"  Claimable Amount: {_format_amount(result['claimable_amount'])} ANM")
            console.print(f"  Claim Count: {result['claim_count']}")
            console.print(f"  Epochs: {result['epochs_claimed']}")
            console.print(f"\n[bold]Transaction Data (hex):[/bold]")
            console.print(f"  {result['tx_data']}")
            console.print(f"\n[dim]Sign and broadcast this transaction to claim your rewards.[/dim]")
    
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@payout_app.command("epoch-status")
def payout_epoch_status(
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
):
    """Get current epoch and payout status."""
    try:
        result = _rpc_call("aicf.getEpochStatus", [])
        
        if json_output:
            console.print(json.dumps(result, indent=2))
        else:
            console.print(f"[bold]Current Epoch Status:[/bold]")
            console.print(f"  Epoch: {result['current_epoch']}")
            console.print(f"  Height: {result['current_height']}")
            console.print(f"  Epoch Range: {result['epoch_start_height']} - {result['epoch_end_height']}")
            console.print(f"  Blocks Until Finalization: {result['blocks_until_finalization']}")
            
            console.print(f"\n[bold]Pool Accounting:[/bold]")
            console.print(f"  Pool Balance: {_format_amount(result['pool_balance'])} ANM")
            console.print(f"  Epoch Inflow: {_format_amount(result['epoch_inflow'])} ANM")
            console.print(f"  Epoch Distributed: {_format_amount(result['epoch_distributed'])} ANM")
            console.print(f"  Reserve Held: {_format_amount(result['reserve_held'])} ANM")
            
            console.print(f"\n[bold]Configuration:[/bold]")
            console.print(f"  Maturity Depth: {result['maturity_depth']} blocks")
            console.print(f"  Epoch Length: {result['epoch_length']} blocks")
    
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


# ============================================================================
# Training Receipt Commands
# ============================================================================

@training_app.command("submit")
def training_submit(
    receipt_file: str = typer.Argument(..., help="Path to CBOR training receipt"),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
):
    """Submit a training receipt for miner AICF credit."""
    try:
        # Read CBOR file
        with open(receipt_file, 'rb') as f:
            receipt_bytes = f.read()
        
        # Hex encode for RPC
        receipt_hex = receipt_bytes.hex()
        
        result = _rpc_call("aicf.submitTrainingReceipt", [receipt_hex])
        
        if json_output:
            console.print(json.dumps(result, indent=2))
        else:
            console.print(f"[green]✓[/green] Training receipt submitted!")
            console.print(f"  Receipt Hash: {result['receipt_hash']}")
            console.print(f"  Training Credit: {_format_amount(result['training_credit'])} ANM")
            console.print(f"  Miner Address: {result['miner_address']}")
            console.print(f"  Provider: {result['provider_id']}")
            console.print(f"  Anchored at Height: {result['anchored_at_height']}")
    
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@training_app.command("get")
def training_get(
    receipt_hash: str = typer.Argument(..., help="Training receipt hash (hex)"),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
):
    """Get training receipt details."""
    try:
        result = _rpc_call("aicf.getTrainingReceipt", [receipt_hash])
        
        if json_output:
            console.print(json.dumps(result, indent=2))
        else:
            console.print(f"[bold]Training Receipt {result['receipt_hash'][:16]}...[/bold]")
            console.print(f"  Task ID: {result['task_id']}")
            console.print(f"  Job Type: {result['job_type']}")
            console.print(f"  Miner: {result['miner_address']}")
            console.print(f"  Provider: {result['provider_id']}")
            
            console.print(f"\n[bold]Training Metrics:[/bold]")
            console.print(f"  GPU Hours: {result['gpu_hours']:.2f}")
            console.print(f"  Cost Paid: {_format_amount(result['cost_paid'])} ANM")
            console.print(f"  Training Credit: {_format_amount(result['training_credit'])} ANM")
            console.print(f"  Epochs Completed: {result['epochs_completed']}")
            console.print(f"  Samples Processed: {result['samples_processed']}")
            
            console.print(f"\n[bold]Verification:[/bold]")
            console.print(f"  Verified: {'✓' if result['is_verified'] else '✗'}")
    
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


if __name__ == "__main__":
    phase2_app()
