"""
Animica CLI - ENA subcommand for LLM inference.

Provides commands to interact with the ENA inference service.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Optional, Dict, Any

import typer
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn

# Add ena module to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../"))

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore

console = Console()
app = typer.Typer(help="ENA LLM inference commands")

try:
    from animica.ena.smoke import run_ena_smoke_test
except Exception:
    run_ena_smoke_test = None  # type: ignore

# Models commands group – must be defined before any @models_app decorators below.
models_app = typer.Typer(help="ENA model management commands")
app.add_typer(models_app, name="models")

# Import upgrade CLI
try:
    from . import ena_upgrade
    app.add_typer(ena_upgrade.app, name="upgrade", help="Model upgrade management")
except ImportError as e:
    # Upgrade CLI is optional if dependencies missing
    pass

# Import artifact CLI
try:
    from . import ena_artifact
    app.add_typer(ena_artifact.app, name="artifact", help="Artifact verification and management")
except ImportError as e:
    # Artifact CLI is optional if dependencies missing
    pass

# Default configuration
DEFAULT_ENA_ENDPOINT = os.getenv("ENA_ENDPOINT", "https://ena.animica.org")
DEFAULT_RPC_URL = os.getenv("ANIMICA_RPC_URL", "https://mainnet.animica.org/rpc")

ANM_BASE_UNITS = 1_000_000_000  # 1 ANM = 1e9 base units


@app.command("smoke-test")
def smoke_test(
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON report"),
    work_dir: Optional[Path] = typer.Option(None, "--work-dir", help="Working directory for artifacts"),
    rpc_url: Optional[str] = typer.Option(None, "--rpc-url", help="RPC URL for optional DA discovery"),
):
    """Run ENA end-to-end smoke test (deterministic CPU dev flow)."""
    if run_ena_smoke_test is None:
        console.print("[red]Error: smoke test module unavailable[/red]")
        raise typer.Exit(1)

    try:
        report = run_ena_smoke_test(work_dir=work_dir, rpc_url=rpc_url)
    except Exception as exc:
        console.print(f"[red]ENA smoke test failed: {exc}[/red]")
        raise typer.Exit(1)

    if json_output:
        console.print(json.dumps(report, indent=2))
    else:
        console.print("[green]ENA smoke test passed[/green]")
        console.print(f"Hash: {report['hashes']['full_snapshot_hash']}")
        console.print(f"DA mode: {report['da_mode']}")
        console.print(f"Work dir: {report['work_dir']}")


def _ensure_httpx():
    """Ensure httpx is available."""
    if httpx is None:
        console.print("[red]Error: httpx not installed[/red]")
        console.print("Install with: pip install httpx")
        raise typer.Exit(1)


def _get_ena_endpoint() -> str:
    """Get ENA endpoint URL."""
    return os.getenv("ENA_ENDPOINT", DEFAULT_ENA_ENDPOINT)


def _get_rpc_url() -> str:
    """Get Animica RPC URL."""
    return os.getenv("ANIMICA_RPC_URL", DEFAULT_RPC_URL)


def _format_amount(amount: int) -> str:
    """Format amount in base units to ANM."""
    anm = amount / ANM_BASE_UNITS
    return f"{anm:.9f}".rstrip("0").rstrip(".")


def _parse_amount(amount_str: str) -> int:
    """Parse amount string to base units."""
    try:
        if "." in amount_str:
            parts = amount_str.split(".")
            whole = int(parts[0])
            fraction = parts[1] if len(parts) > 1 else "0"
            
            if len(fraction) > 9:
                raise ValueError("Too many decimal places (max 9)")
            
            fraction = fraction.ljust(9, "0")
            return whole * ANM_BASE_UNITS + int(fraction)
        else:
            return int(float(amount_str) * ANM_BASE_UNITS)
    except (ValueError, IndexError) as e:
        console.print(f"[red]Error: Invalid amount: {amount_str}[/red]")
        raise typer.Exit(1)


def _load_wallet_address(from_identifier: Optional[str]) -> str:
    """Load wallet address from identifier or default."""
    wallet_path = Path.home() / ".animica" / "wallets.json"
    
    if not wallet_path.exists():
        console.print(f"[red]Error: Wallet file not found: {wallet_path}[/red]")
        console.print("Create a wallet with: animica wallet new")
        raise typer.Exit(1)
    
    try:
        data = json.loads(wallet_path.read_text())
        wallets = data.get("wallets", [])
        
        if not wallets:
            console.print("[red]Error: No wallets found[/red]")
            raise typer.Exit(1)
        
        if from_identifier:
            # Find wallet by identifier
            for w in wallets:
                if (w.get("address") == from_identifier or
                    w.get("label") == from_identifier or
                    str(wallets.index(w)) == from_identifier):
                    return w["address"]
            
            console.print(f"[red]Error: Wallet not found: {from_identifier}[/red]")
            raise typer.Exit(1)
        else:
            # Use first wallet
            return wallets[0]["address"]
    
    except Exception as e:
        console.print(f"[red]Error loading wallet: {e}[/red]")
        raise typer.Exit(1)


def _send_payment_tx(
    from_address: str,
    to_address: str,
    amount: int,
    rpc_url: str,
) -> str:
    """
    Send a payment transaction.
    
    Returns:
        Transaction hash
    """
    import subprocess
    
    # Use animica CLI to send transaction
    cmd = [
        "animica",
        "tx",
        "send",
        "--from", from_address,
        "--to", to_address,
        "--value", str(amount),
        "--json",
    ]
    
    if rpc_url:
        cmd.extend(["--rpc-url", rpc_url])
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )
        
        if result.returncode != 0:
            console.print(f"[red]Error: Transaction failed[/red]")
            console.print(result.stderr)
            raise typer.Exit(1)
        
        # Parse JSON output
        output = json.loads(result.stdout)
        tx_hash = output.get("hash") or output.get("txHash")
        
        if not tx_hash:
            console.print("[red]Error: No transaction hash in response[/red]")
            raise typer.Exit(1)
        
        return tx_hash
    
    except subprocess.TimeoutExpired:
        console.print("[red]Error: Transaction timed out[/red]")
        raise typer.Exit(1)
    except json.JSONDecodeError as e:
        console.print(f"[red]Error: Failed to parse transaction response: {e}[/red]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@models_app.command("list")
def list_models(
    endpoint: Optional[str] = typer.Option(
        None,
        "--endpoint",
        help="ENA endpoint URL",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
):
    """List available models."""
    _ensure_httpx()
    
    ena_endpoint = endpoint or _get_ena_endpoint()
    url = f"{ena_endpoint}/v1/models"
    
    try:
        with httpx.Client(timeout=30) as client:
            response = client.get(url)
            response.raise_for_status()
            data = response.json()
        
        if json_output:
            console.print(json.dumps(data, indent=2))
        else:
            # Display as table
            table = Table(title="Available Models")
            table.add_column("Name", style="cyan")
            table.add_column("Version", style="green")
            table.add_column("Max Tokens", style="yellow")
            table.add_column("Description", style="white")
            
            for model in data.get("models", []):
                table.add_row(
                    model.get("name", ""),
                    model.get("version", ""),
                    str(model.get("max_tokens", "")),
                    model.get("description", ""),
                )
            
            console.print(table)
            
            # Show aliases
            aliases = data.get("aliases", {})
            if aliases:
                console.print("\n[bold]Aliases:[/bold]")
                for alias, target in aliases.items():
                    console.print(f"  {alias} → {target}")
            
            # Show default
            default = data.get("default", "")
            if default:
                console.print(f"\n[bold]Default:[/bold] {default}")
    
    except httpx.HTTPError as e:
        console.print(f"[red]Error: Failed to fetch models: {e}[/red]")
        raise typer.Exit(1)


@app.command("pricing")
def get_pricing(
    endpoint: Optional[str] = typer.Option(
        None,
        "--endpoint",
        help="ENA endpoint URL",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
):
    """Get pricing information including AICF contribution."""
    _ensure_httpx()
    
    ena_endpoint = endpoint or _get_ena_endpoint()
    url = f"{ena_endpoint}/v1/pricing"
    
    try:
        with httpx.Client(timeout=30) as client:
            response = client.get(url)
            response.raise_for_status()
            data = response.json()
        
        if json_output:
            console.print(json.dumps(data, indent=2))
        else:
            fee_per_call = data.get("fee_per_call", 0)
            fee_per_token = data.get("fee_per_token", 0)
            currency = data.get("currency", "ANM")
            
            # AICF info
            aicf_address = data.get("aicf_address", "N/A")
            aicf_bp = data.get("aicf_bp", 0)
            
            console.print(f"[bold]ENA Pricing:[/bold]")
            console.print(f"  Base fee per call: {_format_amount(fee_per_call)} {currency}")
            console.print(f"  Fee per output token: {_format_amount(fee_per_token)} {currency}")
            
            console.print(f"\n[bold]AICF (AI Compute Fund):[/bold]")
            console.print(f"  Address: {aicf_address}")
            console.print(f"  Contribution: {aicf_bp} basis points ({aicf_bp / 100}%)")
            
            # Example breakdown
            example_total = data.get("example_call_cost", fee_per_call)
            example_aicf = data.get("example_aicf_cost", 0)
            example_service = data.get("example_service_cost", 0)
            
            console.print(f"\n[bold]Example Payment (100 token response):[/bold]")
            total_example = fee_per_call + (100 * fee_per_token)
            aicf_example = (total_example * aicf_bp + 9999) // 10000
            service_example = total_example - aicf_example
            
            console.print(f"  Total cost: {_format_amount(total_example)} {currency}")
            console.print(f"  → Service fee: {_format_amount(service_example)} {currency}")
            console.print(f"  → AICF contribution: {_format_amount(aicf_example)} {currency}")
    
    except httpx.HTTPError as e:
        console.print(f"[red]Error: Failed to fetch pricing: {e}[/red]")
        raise typer.Exit(1)


@app.command("infer")
def run_inference(
    prompt: str = typer.Argument(..., help="Input prompt"),
    model: Optional[str] = typer.Option(
        None,
        "--model",
        help="Model name or alias (default: ena.latest)",
    ),
    max_tokens: int = typer.Option(
        100,
        "--max-tokens",
        help="Maximum tokens to generate",
    ),
    fee_mode: str = typer.Option(
        "per_call_tx",
        "--fee-mode",
        help="Payment mode: per_call_tx or credit",
    ),
    from_wallet: Optional[str] = typer.Option(
        None,
        "--from",
        help="Wallet identifier (address, label, or index)",
    ),
    endpoint: Optional[str] = typer.Option(
        None,
        "--endpoint",
        help="ENA endpoint URL",
    ),
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="Animica RPC URL",
    ),
    local: bool = typer.Option(
        False,
        "--local",
        help="Use local inference daemon (no payment required)",
    ),
    network: bool = typer.Option(
        False,
        "--network",
        help="Force network inference (with payment)",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
):
    """Run inference with payment including AICF contribution."""
    _ensure_httpx()
    
    # Determine endpoint based on flags
    if local and network:
        console.print("[red]Error: Cannot specify both --local and --network[/red]")
        raise typer.Exit(1)
    
    if local:
        ena_endpoint = "http://127.0.0.1:8000"
    else:
        ena_endpoint = endpoint or _get_ena_endpoint()
    
    animica_rpc = rpc_url or _get_rpc_url()
    
    # Skip payment for local inference
    if local:
        if not json_output:
            console.print("[dim]Using local inference (no payment)[/dim]\n")
        
        # Make direct inference request
        inference_url = f"{ena_endpoint}/v1/inference"
        request_data = {
            "prompt": prompt,
            "model": model or "ena.latest",
            "max_tokens": max_tokens,
        }
        
        try:
            with httpx.Client(timeout=60) as client:
                response = client.post(inference_url, json=request_data)
                response.raise_for_status()
                result = response.json()
            
            if json_output:
                console.print(json.dumps(result, indent=2))
            else:
                console.print(f"[bold]Response:[/bold]")
                console.print(result.get("text", ""))
                
                if "usage" in result:
                    usage = result["usage"]
                    console.print(f"\n[dim]Tokens: {usage.get('prompt_tokens', 0)} prompt + {usage.get('completion_tokens', 0)} completion = {usage.get('total_tokens', 0)} total[/dim]")
            
            return
        
        except httpx.HTTPError as e:
            console.print(f"[red]Error: Local inference failed: {e}[/red]")
            console.print("[yellow]Is the local daemon running? Start with: animica ena serve start[/yellow]")
            raise typer.Exit(1)
    
    # Load wallet address for network inference
    payer_address = _load_wallet_address(from_wallet)
    
    # Get pricing and AICF information
    pricing_url = f"{ena_endpoint}/v1/pricing"
    try:
        with httpx.Client(timeout=30) as client:
            response = client.get(pricing_url)
            response.raise_for_status()
            pricing = response.json()
    except httpx.HTTPError as e:
        console.print(f"[red]Error: Failed to fetch pricing: {e}[/red]")
        raise typer.Exit(1)
    
    fee_per_call = pricing.get("fee_per_call", 10000000)
    aicf_address = pricing.get("aicf_address")
    aicf_bp = pricing.get("aicf_bp", 2500)
    
    # Calculate service and AICF fees
    total_fee = fee_per_call
    # AICF fee (rounded up)
    aicf_fee = (total_fee * aicf_bp + 9999) // 10000
    service_fee = total_fee - aicf_fee
    
    # Get ENA service address
    ena_service_address = os.getenv(
        "ENA_SERVICE_ADDRESS",
        "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq000000"
    )
    
    tx_hash_service = None
    tx_hash_aicf = None
    
    if fee_mode == "per_call_tx":
        # Send two separate payment transactions (service + AICF)
        if not json_output:
            console.print(f"[bold]Payment Details:[/bold]")
            console.print(f"  Total required: {_format_amount(total_fee)} ANM")
            console.print(f"  Service fee: {_format_amount(service_fee)} ANM")
            console.print(f"  AICF contribution: {_format_amount(aicf_fee)} ANM ({aicf_bp / 100}%)")
            console.print()
            console.print(f"[yellow]Sending service payment...[/yellow]")
            console.print(f"  From: {payer_address}")
            console.print(f"  To: {ena_service_address}")
            console.print(f"  Amount: {_format_amount(service_fee)} ANM")
        
        # Send service payment
        tx_hash_service = _send_payment_tx(
            from_address=payer_address,
            to_address=ena_service_address,
            amount=service_fee,
            rpc_url=animica_rpc,
        )
        
        if not json_output:
            console.print(f"[green]✓ Service payment: {tx_hash_service}[/green]")
            console.print()
            console.print(f"[yellow]Sending AICF contribution...[/yellow]")
            console.print(f"  From: {payer_address}")
            console.print(f"  To: {aicf_address}")
            console.print(f"  Amount: {_format_amount(aicf_fee)} ANM")
        
        # Send AICF payment
        tx_hash_aicf = _send_payment_tx(
            from_address=payer_address,
            to_address=aicf_address,
            amount=aicf_fee,
            rpc_url=animica_rpc,
        )
        
        if not json_output:
            console.print(f"[green]✓ AICF contribution: {tx_hash_aicf}[/green]")
            console.print(f"[yellow]Waiting for transaction confirmations...[/yellow]")
        
        # Wait a bit for txs to propagate
        time.sleep(2)
    
    # Prepare inference request
    request_data = {
        "prompt": prompt,
        "max_tokens": max_tokens,
        "payment": {
            "mode": fee_mode,
            "payer": payer_address,
        },
    }
    
    if model:
        request_data["model"] = model
    
    if fee_mode == "per_call_tx":
        # Use two-transaction format
        request_data["payment"]["tx_hash_service"] = tx_hash_service
        request_data["payment"]["tx_hash_aicf"] = tx_hash_aicf
    
    # Call inference API
    infer_url = f"{ena_endpoint}/v1/infer"
    
    try:
        if not json_output:
            console.print(f"[yellow]Running inference...[/yellow]")
        
        with httpx.Client(timeout=60) as client:
            response = client.post(infer_url, json=request_data)
            response.raise_for_status()
            result = response.json()
        
        if json_output:
            console.print(json.dumps(result, indent=2))
        else:
            # Display result
            console.print("\n[bold green]✓ Inference complete![/bold green]")
            console.print(f"\n[bold]Response:[/bold]")
            console.print(result.get("answer", ""))
            
            # Display usage
            usage = result.get("usage", {})
            console.print(f"\n[bold]Usage:[/bold]")
            console.print(f"  Prompt tokens: {usage.get('prompt_tokens', 0)}")
            console.print(f"  Completion tokens: {usage.get('completion_tokens', 0)}")
            console.print(f"  Total tokens: {usage.get('total_tokens', 0)}")
            
            # Display receipt
            receipt = result.get("receipt", {})
            console.print(f"\n[bold]Receipt:[/bold]")
            console.print(f"  ID: {receipt.get('id', '')}")
            console.print(f"  Mode: {receipt.get('mode', '')}")
            
            # Show AICF contribution details
            if receipt.get('aicf_paid'):
                console.print(f"\n[bold green]AICF Contribution:[/bold green]")
                console.print(f"  Amount: {_format_amount(receipt.get('aicf_paid', 0))} ANM")
                console.print(f"  Required: {_format_amount(receipt.get('aicf_required', 0))} ANM")
                if receipt.get('aicf_explicit'):
                    console.print(f"  Status: ✓ Verified on-chain")
                else:
                    console.print(f"  Status: ⚠ Not explicitly verified (single tx)")
                if receipt.get('tx_hash_aicf'):
                    console.print(f"  Transaction: {receipt.get('tx_hash_aicf', '')}")
            
            console.print(f"\n[bold]Total Paid:[/bold]")
            console.print(f"  Amount: {_format_amount(receipt.get('amount', 0))} ANM")
            if receipt.get('tx_hash_service'):
                console.print(f"  Service tx: {receipt.get('tx_hash_service', '')}")
            elif receipt.get('tx_hash'):
                console.print(f"  Transaction: {receipt.get('tx_hash', '')}")
    
    except httpx.HTTPStatusError as e:
        try:
            error_detail = e.response.json()
            detail = error_detail.get("detail", str(e))
        except:
            detail = str(e)
        
        console.print(f"[red]Error: Inference failed: {detail}[/red]")
        raise typer.Exit(1)
    except httpx.HTTPError as e:
        console.print(f"[red]Error: Request failed: {e}[/red]")
        raise typer.Exit(1)


@app.command("deposit")
def deposit_credits(
    amount: str = typer.Argument(..., help="Amount to deposit (in ANM)"),
    from_wallet: Optional[str] = typer.Option(
        None,
        "--from",
        help="Wallet identifier (address, label, or index)",
    ),
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="Animica RPC URL",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
):
    """Deposit credits for credit mode."""
    # Load wallet address
    payer_address = _load_wallet_address(from_wallet)
    animica_rpc = rpc_url or _get_rpc_url()
    
    # Parse amount
    amount_base = _parse_amount(amount)
    
    # Get ENA service address
    ena_service_address = os.getenv(
        "ENA_SERVICE_ADDRESS",
        "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq000000"
    )
    
    if not json_output:
        console.print(f"[yellow]Depositing credits...[/yellow]")
        console.print(f"  From: {payer_address}")
        console.print(f"  To: {ena_service_address}")
        console.print(f"  Amount: {_format_amount(amount_base)} ANM")
    
    # Send deposit transaction
    tx_hash = _send_payment_tx(
        from_address=payer_address,
        to_address=ena_service_address,
        amount=amount_base,
        rpc_url=animica_rpc,
    )
    
    if json_output:
        result = {
            "ok": True,
            "tx_hash": tx_hash,
            "amount": amount_base,
            "from": payer_address,
            "to": ena_service_address,
        }
        console.print(json.dumps(result, indent=2))
    else:
        console.print(f"\n[bold green]✓ Deposit transaction sent![/bold green]")
        console.print(f"  Transaction: {tx_hash}")
        console.print(f"  Amount: {_format_amount(amount_base)} ANM")
        console.print(f"\n[dim]Credits will be available once the transaction is confirmed.[/dim]")


@app.command("tx-status")
def check_tx_status(
    tx: str = typer.Argument(..., help="Transaction hash to check"),
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="Animica RPC URL",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
):
    """Check transaction status."""
    import subprocess
    
    animica_rpc = rpc_url or _get_rpc_url()
    
    # Use animica CLI to check status
    cmd = [
        "animica",
        "tx",
        "status",
        tx,
        "--json",
    ]
    
    if rpc_url:
        cmd.extend(["--rpc-url", rpc_url])
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        
        if result.returncode != 0:
            console.print(f"[red]Error: Failed to get status[/red]")
            console.print(result.stderr)
            raise typer.Exit(1)
        
        if json_output:
            console.print(result.stdout)
        else:
            data = json.loads(result.stdout)
            console.print(f"[bold]Transaction Status:[/bold]")
            console.print(json.dumps(data, indent=2))
    
    except subprocess.TimeoutExpired:
        console.print("[red]Error: Request timed out[/red]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@app.command("status")
def ena_status(
    endpoint: Optional[str] = typer.Option(
        None,
        "--endpoint",
        help="ENA endpoint URL",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
):
    """Show ENA service status (network/local availability)."""
    _ensure_httpx()
    
    ena_endpoint = endpoint or _get_ena_endpoint()
    
    status_data = {
        "endpoint": ena_endpoint,
        "network_available": False,
        "local_available": False,
        "models": [],
        "version": None,
    }
    
    # Check network endpoint
    try:
        with httpx.Client(timeout=10) as client:
            # Try /health endpoint first
            try:
                response = client.get(f"{ena_endpoint}/health")
                response.raise_for_status()
                health_data = response.json()
                status_data["network_available"] = True
                status_data["version"] = health_data.get("version")
            except:
                # Fallback to /v1/models
                response = client.get(f"{ena_endpoint}/v1/models")
                response.raise_for_status()
                models_data = response.json()
                status_data["network_available"] = True
                status_data["models"] = [m.get("name") for m in models_data.get("models", [])]
    except httpx.HTTPError:
        pass
    
    # Check local endpoint if different
    local_endpoint = "http://127.0.0.1:8000"
    if local_endpoint != ena_endpoint:
        try:
            with httpx.Client(timeout=5) as client:
                response = client.get(f"{local_endpoint}/health")
                response.raise_for_status()
                status_data["local_available"] = True
        except httpx.HTTPError:
            pass
    
    if json_output:
        console.print(json.dumps(status_data, indent=2))
    else:
        console.print(f"[bold]ENA Service Status[/bold]\n")
        console.print(f"Endpoint: {ena_endpoint}")
        
        if status_data["network_available"]:
            console.print(f"Network: [green]✓ Available[/green]")
            if status_data["version"]:
                console.print(f"Version: {status_data['version']}")
            if status_data["models"]:
                console.print(f"Models: {len(status_data['models'])} available")
        else:
            console.print(f"Network: [red]✗ Unavailable[/red]")
        
        if local_endpoint != ena_endpoint:
            if status_data["local_available"]:
                console.print(f"Local ({local_endpoint}): [green]✓ Available[/green]")
            else:
                console.print(f"Local ({local_endpoint}): [yellow]○ Not running[/yellow]")
                console.print("[dim]Start with: animica ena serve start[/dim]")


# Train commands group
train_app = typer.Typer(help="ENA training job commands")
app.add_typer(train_app, name="train")


# Checkpoints commands group
checkpoints_app = typer.Typer(help="ENA model checkpoint commands")
app.add_typer(checkpoints_app, name="checkpoints")


# Serve commands group
serve_app = typer.Typer(help="ENA local inference daemon commands")
app.add_typer(serve_app, name="serve")


# AICF commands group
aicf_app = typer.Typer(help="AICF (AI Compute Fund) commands")
app.add_typer(aicf_app, name="aicf")


# Image commands group
image_app = typer.Typer(help="ENA media image commands")
app.add_typer(image_app, name="image")


@aicf_app.command("info")
def aicf_info(
    endpoint: Optional[str] = typer.Option(
        None,
        "--endpoint",
        help="ENA endpoint URL",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
):
    """Show AICF (AI Compute Fund) information."""
    _ensure_httpx()
    
    ena_endpoint = endpoint or _get_ena_endpoint()
    pricing_url = f"{ena_endpoint}/v1/pricing"
    
    try:
        with httpx.Client(timeout=30) as client:
            response = client.get(pricing_url)
            response.raise_for_status()
            pricing = response.json()
        
        if json_output:
            # Output AICF-relevant fields
            aicf_data = {
                "address": pricing.get("aicf_address"),
                "basis_points": pricing.get("aicf_bp"),
                "percentage": pricing.get("aicf_bp", 0) / 100,
                "description": pricing.get("aicf_description"),
                "example": {
                    "total_cost": pricing.get("example_call_cost"),
                    "aicf_contribution": pricing.get("example_aicf_cost"),
                    "service_fee": pricing.get("example_service_cost"),
                },
            }
            console.print(json.dumps(aicf_data, indent=2))
        else:
            aicf_address = pricing.get("aicf_address", "N/A")
            aicf_bp = pricing.get("aicf_bp", 0)
            aicf_desc = pricing.get("aicf_description", "")
            
            console.print("[bold]AICF (AI Compute Fund) Information:[/bold]")
            console.print(f"  Address: {aicf_address}")
            console.print(f"  Contribution: {aicf_bp} basis points ({aicf_bp / 100}%)")
            if aicf_desc:
                console.print(f"  Description: {aicf_desc}")
            
            # Show example breakdown
            example_total = pricing.get("example_call_cost", 0)
            example_aicf = pricing.get("example_aicf_cost", 0)
            example_service = pricing.get("example_service_cost", 0)
            
            if example_total > 0:
                console.print(f"\n[bold]Example Payment Breakdown:[/bold]")
                console.print(f"  Total fee: {_format_amount(example_total)} ANM")
                console.print(f"  → Service: {_format_amount(example_service)} ANM")
                console.print(f"  → AICF: {_format_amount(example_aicf)} ANM")
    
    except httpx.HTTPError as e:
        console.print(f"[red]Error: Failed to fetch AICF info: {e}[/red]")
        raise typer.Exit(1)


@aicf_app.command("verify")
def aicf_verify(
    tx: str = typer.Argument(..., help="AICF transaction hash to verify"),
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="Animica RPC URL",
    ),
    endpoint: Optional[str] = typer.Option(
        None,
        "--endpoint",
        help="ENA endpoint URL",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
):
    """Verify an AICF contribution transaction."""
    import subprocess
    
    animica_rpc = rpc_url or _get_rpc_url()
    ena_endpoint = endpoint or _get_ena_endpoint()
    
    # Get AICF address from ENA endpoint
    try:
        _ensure_httpx()
        with httpx.Client(timeout=30) as client:
            response = client.get(f"{ena_endpoint}/v1/pricing")
            response.raise_for_status()
            pricing = response.json()
            aicf_address = pricing.get("aicf_address")
    except httpx.HTTPError:
        aicf_address = None
    
    # Get transaction details
    cmd = [
        "animica",
        "tx",
        "status",
        tx,
        "--json",
    ]
    
    if rpc_url:
        cmd.extend(["--rpc-url", rpc_url])
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        
        if result.returncode != 0:
            console.print(f"[red]Error: Failed to get transaction details[/red]")
            console.print(result.stderr)
            raise typer.Exit(1)
        
        tx_data = json.loads(result.stdout)
        
        # Check if it's an AICF contribution
        tx_to = tx_data.get("to", "")
        tx_value = tx_data.get("value", 0)
        
        if isinstance(tx_value, str):
            if tx_value.startswith("0x"):
                tx_value = int(tx_value, 16)
            else:
                tx_value = int(tx_value)
        
        is_aicf = (aicf_address and tx_to == aicf_address)
        
        if json_output:
            verification = {
                "transaction": tx,
                "to": tx_to,
                "value": tx_value,
                "is_aicf_contribution": is_aicf,
                "aicf_address": aicf_address,
            }
            console.print(json.dumps(verification, indent=2))
        else:
            console.print(f"[bold]AICF Contribution Verification:[/bold]")
            console.print(f"  Transaction: {tx}")
            console.print(f"  Recipient: {tx_to}")
            console.print(f"  Amount: {_format_amount(tx_value)} ANM")
            
            if is_aicf:
                console.print(f"  Status: [green]✓ Valid AICF contribution[/green]")
            else:
                console.print(f"  Status: [red]✗ Not an AICF contribution[/red]")
                if aicf_address:
                    console.print(f"  Expected recipient: {aicf_address}")
    
    except subprocess.TimeoutExpired:
        console.print("[red]Error: Request timed out[/red]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@aicf_app.command("doctor")
def aicf_doctor(
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="Animica RPC URL",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
):
    """Diagnose AICF configuration and connectivity issues."""
    import subprocess
    
    animica_rpc = rpc_url or _get_rpc_url()
    issues = []
    fixes = []
    
    console.print("[bold]AICF Doctor - Running Diagnostics...[/bold]\n")
    
    # Check 1: RPC connectivity
    console.print("[yellow]→ Checking RPC connectivity...[/yellow]")
    try:
        result = subprocess.run(
            ["animica", "rpc", "call", "chain.getChainId", "--rpc-url", animica_rpc],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            console.print("[green]  ✓ RPC is reachable[/green]")
        else:
            issues.append("RPC not reachable")
            fixes.append(f"Check if node is running and accessible at {animica_rpc}")
            console.print(f"[red]  ✗ RPC not reachable at {animica_rpc}[/red]")
    except subprocess.TimeoutExpired:
        issues.append("RPC timeout")
        fixes.append(f"RPC timeout - check network connection or use local RPC")
        console.print("[red]  ✗ RPC request timed out[/red]")
    except Exception as e:
        issues.append(f"RPC error: {e}")
        fixes.append("Ensure animica CLI is installed correctly")
        console.print(f"[red]  ✗ Error: {e}[/red]")
    
    # Check 2: Wallet exists
    console.print("\n[yellow]→ Checking wallet configuration...[/yellow]")
    wallet_path = Path.home() / ".animica" / "wallets.json"
    if wallet_path.exists():
        try:
            data = json.loads(wallet_path.read_text())
            wallets = data.get("wallets", [])
            if wallets:
                console.print(f"[green]  ✓ Found {len(wallets)} wallet(s)[/green]")
            else:
                issues.append("No wallets configured")
                fixes.append("Create a wallet: animica wallet new")
                console.print("[red]  ✗ No wallets found[/red]")
        except Exception as e:
            issues.append(f"Wallet file corrupted: {e}")
            fixes.append("Check wallet file format or create new wallet")
            console.print(f"[red]  ✗ Error reading wallets: {e}[/red]")
    else:
        issues.append("Wallet file not found")
        fixes.append("Create a wallet: animica wallet new")
        console.print(f"[red]  ✗ Wallet file not found: {wallet_path}[/red]")
    
    # Check 3: Data directory writable
    console.print("\n[yellow]→ Checking data directory permissions...[/yellow]")
    data_dir = Path.home() / ".animica"
    if data_dir.exists():
        if os.access(data_dir, os.W_OK):
            console.print(f"[green]  ✓ Data directory is writable: {data_dir}[/green]")
        else:
            issues.append("Data directory not writable")
            fixes.append(f"Fix permissions: chmod 755 {data_dir}")
            console.print(f"[red]  ✗ Data directory not writable: {data_dir}[/red]")
    else:
        console.print(f"[yellow]  ⚠ Data directory does not exist: {data_dir}[/yellow]")
        console.print(f"[dim]    (Will be created on first use)[/dim]")
    
    # Check 4: AICF endpoint connectivity
    console.print("\n[yellow]→ Checking AICF/ENA endpoint...[/yellow]")
    ena_endpoint = _get_ena_endpoint()
    try:
        if httpx is not None:
            with httpx.Client(timeout=10) as client:
                response = client.get(f"{ena_endpoint}/v1/pricing")
                response.raise_for_status()
                console.print(f"[green]  ✓ ENA endpoint is reachable: {ena_endpoint}[/green]")
        else:
            console.print("[yellow]  ⚠ httpx not installed, skipping endpoint check[/yellow]")
    except Exception as e:
        issues.append(f"ENA endpoint not reachable: {e}")
        fixes.append(f"Check network or use --endpoint to specify a different URL")
        console.print(f"[red]  ✗ ENA endpoint not reachable: {e}[/red]")
    
    # Summary
    console.print("\n" + "=" * 60)
    if not issues:
        console.print("[bold green]✓ All checks passed![/bold green]")
        console.print("\n[dim]You're ready to use AICF commands.[/dim]")
    else:
        console.print(f"[bold red]✗ Found {len(issues)} issue(s):[/bold red]\n")
        for i, issue in enumerate(issues, 1):
            console.print(f"  {i}. {issue}")
        
        console.print("\n[bold]Suggested Fixes:[/bold]\n")
        for i, fix in enumerate(fixes, 1):
            console.print(f"  {i}. {fix}")
    
    if json_output:
        console.print("\n" + json.dumps({
            "status": "ok" if not issues else "error",
            "issues": issues,
            "fixes": fixes,
        }, indent=2))


@aicf_app.command("worker-register")
def aicf_worker_register(
    address: str = typer.Argument(..., help="Worker payout address (anim1...)"),
    name: Optional[str] = typer.Option(
        None,
        "--name",
        help="Display name for worker",
    ),
    endpoint: Optional[str] = typer.Option(
        None,
        "--endpoint",
        help="ENA endpoint URL",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
):
    """Register as a GPU worker for AICF."""
    _ensure_httpx()
    
    ena_endpoint = endpoint or _get_ena_endpoint()
    
    # Validate address format
    if not address.startswith("anim1"):
        console.print(f"[red]Error: Invalid address format (must start with 'anim1')[/red]")
        raise typer.Exit(1)
    
    # Prepare registration data
    registration_data = {
        "address": address,
        "displayName": name or f"Worker {address[:12]}...",
    }
    
    try:
        if not json_output:
            console.print("[yellow]Registering worker...[/yellow]")
            console.print(f"  Address: {address}")
            console.print(f"  Name: {registration_data['displayName']}")
        
        with httpx.Client(timeout=30) as client:
            response = client.post(
                f"{ena_endpoint}/v1/aicf/workers/register",
                json=registration_data,
            )
            response.raise_for_status()
            result = response.json()
        
        if json_output:
            console.print(json.dumps(result, indent=2))
        else:
            console.print("\n[bold green]✓ Worker registered successfully![/bold green]")
            console.print(f"  Worker ID: {result.get('workerId', 'N/A')}")
            console.print(f"  Status: {result.get('status', 'PENDING')}")
            console.print(f"\n[dim]You can now start accepting jobs with:[/dim]")
            console.print(f"[dim]  animica ena aicf worker-run --worker-id {result.get('workerId', 'YOUR_ID')}[/dim]")
    
    except httpx.HTTPStatusError as e:
        try:
            error_detail = e.response.json()
            detail = error_detail.get("detail", str(e))
        except:
            detail = str(e)
        console.print(f"[red]Error: {detail}[/red]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@aicf_app.command("worker-run")
def aicf_worker_run(
    worker_id: str = typer.Argument(..., help="Worker ID from registration"),
    loop: bool = typer.Option(
        False,
        "--loop",
        help="Run continuously, polling for jobs",
    ),
    endpoint: Optional[str] = typer.Option(
        None,
        "--endpoint",
        help="ENA endpoint URL",
    ),
):
    """Run AICF worker to process jobs."""
    _ensure_httpx()
    
    ena_endpoint = endpoint or _get_ena_endpoint()
    
    console.print(f"[bold]Starting AICF Worker[/bold]")
    console.print(f"  Worker ID: {worker_id}")
    console.print(f"  Endpoint: {ena_endpoint}")
    console.print(f"  Mode: {'Continuous' if loop else 'Single job'}\n")
    
    iteration = 0
    while True:
        iteration += 1
        console.print(f"[yellow]→ Iteration {iteration}: Checking for jobs...[/yellow]")
        
        try:
            with httpx.Client(timeout=30) as client:
                # Poll for available job
                response = client.get(
                    f"{ena_endpoint}/v1/aicf/jobs/available",
                    params={"worker_id": worker_id},
                )
                response.raise_for_status()
                job_data = response.json()
            
            if job_data.get("job_id"):
                job_id = job_data["job_id"]
                console.print(f"[green]  ✓ Got job: {job_id}[/green]")
                console.print(f"    Type: {job_data.get('type', 'unknown')}")
                console.print(f"    Difficulty: {job_data.get('difficulty', 'N/A')}")
                
                # Process job (dummy implementation)
                console.print(f"[yellow]  → Processing job...[/yellow]")
                time.sleep(1)  # Simulate work
                
                # Submit result
                result_data = {
                    "worker_id": worker_id,
                    "job_id": job_id,
                    "result": {"status": "completed", "output": "dummy_output"},
                }
                
                with httpx.Client(timeout=30) as client:
                    response = client.post(
                        f"{ena_endpoint}/v1/aicf/jobs/submit",
                        json=result_data,
                    )
                    response.raise_for_status()
                    submit_result = response.json()
                
                console.print(f"[green]  ✓ Job submitted![/green]")
                console.print(f"    Credits earned: {submit_result.get('credits', 0)}")
            else:
                console.print("[dim]  No jobs available[/dim]")
        
        except httpx.HTTPError as e:
            console.print(f"[red]  ✗ Error: {e}[/red]")
        except Exception as e:
            console.print(f"[red]  ✗ Unexpected error: {e}[/red]")
        
        if not loop:
            break
        
        # Wait before next poll
        console.print("[dim]  Waiting 10s before next check...[/dim]\n")
        time.sleep(10)


@aicf_app.command("worker-claim")
def aicf_worker_claim(
    worker_id: str = typer.Argument(..., help="Worker ID"),
    epoch: int = typer.Argument(..., help="Epoch number to claim"),
    endpoint: Optional[str] = typer.Option(
        None,
        "--endpoint",
        help="ENA endpoint URL",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
):
    """Claim AICF rewards for a completed epoch."""
    _ensure_httpx()
    
    ena_endpoint = endpoint or _get_ena_endpoint()
    
    try:
        if not json_output:
            console.print(f"[yellow]Claiming rewards...[/yellow]")
            console.print(f"  Worker ID: {worker_id}")
            console.print(f"  Epoch: {epoch}")
        
        with httpx.Client(timeout=30) as client:
            response = client.post(
                f"{ena_endpoint}/v1/aicf/rewards/claim",
                json={"worker_id": worker_id, "epoch": epoch},
            )
            response.raise_for_status()
            result = response.json()
        
        if json_output:
            console.print(json.dumps(result, indent=2))
        else:
            if result.get("claimed"):
                console.print("\n[bold green]✓ Rewards claimed![/bold green]")
                console.print(f"  Amount: {_format_amount(result.get('amount', 0))} ANM")
                console.print(f"  Transaction: {result.get('tx_hash', 'N/A')}")
                console.print(f"  Status: {result.get('status', 'PENDING')}")
            else:
                console.print("\n[yellow]No rewards to claim[/yellow]")
                console.print(f"  Reason: {result.get('reason', 'Unknown')}")
    
    except httpx.HTTPStatusError as e:
        try:
            error_detail = e.response.json()
            detail = error_detail.get("detail", str(e))
        except:
            detail = str(e)
        console.print(f"[red]Error: {detail}[/red]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@app.command("doctor")
def ena_doctor(
    endpoint: Optional[str] = typer.Option(
        None,
        "--endpoint",
        help="ENA endpoint URL",
    ),
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="Animica RPC URL",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
):
    """Diagnose ENA configuration and connectivity issues."""
    import subprocess
    
    ena_endpoint = endpoint or _get_ena_endpoint()
    animica_rpc = rpc_url or _get_rpc_url()
    issues = []
    fixes = []
    
    console.print("[bold]ENA Doctor - Running Diagnostics...[/bold]\n")
    
    # Check 1: ENA endpoint connectivity
    console.print("[yellow]→ Checking ENA endpoint...[/yellow]")
    try:
        if httpx is not None:
            with httpx.Client(timeout=10) as client:
                response = client.get(f"{ena_endpoint}/v1/models")
                response.raise_for_status()
                models_data = response.json()
                console.print(f"[green]  ✓ ENA endpoint is reachable: {ena_endpoint}[/green]")
                console.print(f"[dim]    Available models: {len(models_data.get('models', []))}[/dim]")
        else:
            issues.append("httpx not installed")
            fixes.append("Install httpx: pip install httpx")
            console.print("[red]  ✗ httpx not installed[/red]")
    except Exception as e:
        issues.append(f"ENA endpoint not reachable: {e}")
        fixes.append(f"Check network or use --endpoint to specify different URL")
        console.print(f"[red]  ✗ ENA endpoint not reachable: {e}[/red]")
    
    # Check 2: RPC connectivity
    console.print("\n[yellow]→ Checking RPC connectivity...[/yellow]")
    try:
        result = subprocess.run(
            ["animica", "rpc", "call", "chain.getChainId", "--rpc-url", animica_rpc],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            console.print(f"[green]  ✓ RPC is reachable: {animica_rpc}[/green]")
        else:
            issues.append("RPC not reachable")
            fixes.append(f"Start node: animica node up")
            console.print(f"[red]  ✗ RPC not reachable: {animica_rpc}[/red]")
    except subprocess.TimeoutExpired:
        issues.append("RPC timeout")
        fixes.append("Check network connection or use local RPC")
        console.print("[red]  ✗ RPC request timed out[/red]")
    except Exception as e:
        issues.append(f"RPC error: {e}")
        fixes.append("Ensure animica CLI is installed correctly")
        console.print(f"[red]  ✗ Error: {e}[/red]")
    
    # Check 3: Wallet exists
    console.print("\n[yellow]→ Checking wallet configuration...[/yellow]")
    wallet_path = Path.home() / ".animica" / "wallets.json"
    if wallet_path.exists():
        try:
            data = json.loads(wallet_path.read_text())
            wallets = data.get("wallets", [])
            if wallets:
                console.print(f"[green]  ✓ Found {len(wallets)} wallet(s)[/green]")
                # Check balance of first wallet
                first_addr = wallets[0]["address"]
                try:
                    result = subprocess.run(
                        ["animica", "wallet", "balance", "--address", first_addr, "--rpc-url", animica_rpc],
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    if result.returncode == 0:
                        console.print(f"[dim]    First wallet ({first_addr[:12]}...): Balance query successful[/dim]")
                    else:
                        console.print(f"[yellow]    ⚠ Could not query balance for first wallet[/yellow]")
                except:
                    pass
            else:
                issues.append("No wallets configured")
                fixes.append("Create a wallet: animica wallet new")
                console.print("[red]  ✗ No wallets found[/red]")
        except Exception as e:
            issues.append(f"Wallet file corrupted: {e}")
            fixes.append("Check wallet file or create new wallet")
            console.print(f"[red]  ✗ Error reading wallets: {e}[/red]")
    else:
        issues.append("Wallet file not found")
        fixes.append("Create a wallet: animica wallet new")
        console.print(f"[red]  ✗ Wallet file not found: {wallet_path}[/red]")
    
    # Check 4: AICF pricing
    console.print("\n[yellow]→ Checking AICF pricing configuration...[/yellow]")
    try:
        if httpx is not None:
            with httpx.Client(timeout=10) as client:
                response = client.get(f"{ena_endpoint}/v1/pricing")
                response.raise_for_status()
                pricing = response.json()
                aicf_address = pricing.get("aicf_address")
                aicf_bp = pricing.get("aicf_bp", 0)
                console.print(f"[green]  ✓ AICF configuration loaded[/green]")
                console.print(f"[dim]    AICF address: {aicf_address}[/dim]")
                console.print(f"[dim]    Contribution: {aicf_bp / 100}%[/dim]")
        else:
            console.print("[yellow]  ⚠ Skipping (httpx not installed)[/yellow]")
    except Exception as e:
        console.print(f"[yellow]  ⚠ Could not load AICF config: {e}[/yellow]")
    
    # Summary
    console.print("\n" + "=" * 60)
    if not issues:
        console.print("[bold green]✓ All checks passed![/bold green]")
        console.print("\n[dim]You're ready to use ENA inference:[/dim]")
        console.print("[dim]  animica ena infer \"hello world\"[/dim]")
    else:
        console.print(f"[bold red]✗ Found {len(issues)} issue(s):[/bold red]\n")
        for i, issue in enumerate(issues, 1):
            console.print(f"  {i}. {issue}")
        
        console.print("\n[bold]Suggested Fixes:[/bold]\n")
        for i, fix in enumerate(fixes, 1):
            console.print(f"  {i}. {fix}")
    
    if json_output:
        console.print("\n" + json.dumps({
            "status": "ok" if not issues else "error",
            "issues": issues,
            "fixes": fixes,
        }, indent=2))


@app.command("ask")
def ask_alias(
    prompt: str = typer.Argument(..., help="Your question or prompt"),
    model: Optional[str] = typer.Option(
        None,
        "--model",
        help="Model name or alias",
    ),
    max_tokens: int = typer.Option(
        100,
        "--max-tokens",
        help="Maximum tokens to generate",
    ),
    from_wallet: Optional[str] = typer.Option(
        None,
        "--from",
        help="Wallet identifier",
    ),
    endpoint: Optional[str] = typer.Option(
        None,
        "--endpoint",
        help="ENA endpoint URL",
    ),
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="Animica RPC URL",
    ),
    local: bool = typer.Option(
        False,
        "--local",
        help="Use local CPU inference (not implemented yet)",
    ),
    remote: Optional[str] = typer.Option(
        None,
        "--remote",
        help="Remote inference endpoint (alias for --endpoint)",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
):
    """
    Ask ENA a question (alias for 'infer' command).
    
    This is the recommended user-friendly command for running inference.
    It automatically handles AICF payments and wallet selection.
    
    Examples:
        animica ena ask "What is Animica?"
        animica ena ask "Explain blockchain" --max-tokens 200
        animica ena ask "Hello" --local  (future: local CPU inference)
        animica ena ask "Test" --remote https://custom.ena.org
    """
    # Handle local flag
    if local:
        console.print("[yellow]⚠ Local inference not yet implemented[/yellow]")
        console.print("Using remote endpoint for now...")
    
    # Remote flag is alias for endpoint
    if remote and not endpoint:
        endpoint = remote
    
    # Call the main infer command
    run_inference(
        prompt=prompt,
        model=model,
        max_tokens=max_tokens,
        fee_mode="per_call_tx",
        from_wallet=from_wallet,
        endpoint=endpoint,
        rpc_url=rpc_url,
        json_output=json_output,
    )


@app.command("install")
def install_models(
    model: Optional[str] = typer.Option(
        None,
        "--model",
        help="Specific model to install (default: install all recommended)",
    ),
    data_dir: Optional[str] = typer.Option(
        None,
        "--data-dir",
        help="Directory for model assets",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Force reinstall even if already present",
    ),
):
    """
    Install ENA model assets for local inference.
    
    Downloads and sets up model weights, tokenizers, and other assets
    needed for local CPU/GPU inference.
    
    Note: This is a placeholder for future local inference support.
    Currently, ENA runs remotely via the inference endpoint.
    """
    console.print("[bold]ENA Model Installation[/bold]\n")
    
    # Default data directory
    if not data_dir:
        data_dir = str(Path.home() / ".animica" / "ena_models")
    
    data_path = Path(data_dir)
    
    console.print(f"Installation directory: {data_path}")
    console.print(f"Model filter: {model or 'all recommended'}")
    console.print()
    
    # Check if directory exists
    if data_path.exists():
        if force:
            console.print("[yellow]⚠ Directory exists, but --force specified[/yellow]")
        else:
            console.print("[yellow]⚠ Directory already exists[/yellow]")
            if not typer.confirm("Reinstall anyway?"):
                console.print("Installation cancelled")
                raise typer.Exit(0)
    else:
        console.print(f"Creating directory: {data_path}")
        data_path.mkdir(parents=True, exist_ok=True)
    
    # Future implementation: download model assets
    console.print("\n[yellow]⚠ Local model installation not yet implemented[/yellow]")
    console.print()
    console.print("For now, ENA uses remote inference endpoints:")
    console.print("  • Mainnet: https://ena.animica.org")
    console.print("  • Custom: Use --endpoint flag")
    console.print()
    console.print("Local inference will be available in a future release.")
    console.print()
    console.print("To use ENA now, run:")
    console.print('  animica ena ask "Your question here"')
    console.print()
    
    # Create a marker file to indicate install was attempted
    marker_file = data_path / ".ena_install_pending"
    marker_file.write_text(json.dumps({
        "version": "future",
        "installed_at": time.time(),
        "status": "pending_implementation",
    }))
    
    console.print(f"[dim]Created marker file: {marker_file}[/dim]")


# ============================================================================
# Image CLI Commands
# ============================================================================

# Constants for image handling
ENA_MEDIA_NAMESPACE = 7
CHUNK_SIZE = 1024 * 1024  # 1MB chunks
CHUNK_THRESHOLD = 1024 * 1024  # Files > 1MB get chunked

# Image MIME type mappings
IMAGE_FORMATS = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
}

# Magic byte signatures for validation
IMAGE_SIGNATURES = {
    b"\x89\x50\x4E\x47\x0D\x0A\x1A\x0A": "png",
    b"\xFF\xD8\xFF": "jpg",
    b"RIFF": "webp",  # WEBP has RIFF header followed by WEBP
}


def _detect_image_format(file_path: Path) -> Optional[str]:
    """
    Detect image format by magic bytes.
    
    Returns:
        Format string (png, jpg, webp) or None if unknown
    """
    with open(file_path, "rb") as f:
        header = f.read(16)
    
    # Check PNG
    if header.startswith(b"\x89\x50\x4E\x47\x0D\x0A\x1A\x0A"):
        return "png"
    
    # Check JPEG
    if header.startswith(b"\xFF\xD8\xFF"):
        return "jpg"
    
    # Check WEBP
    if header.startswith(b"RIFF") and b"WEBP" in header:
        return "webp"
    
    return None


def _validate_image_file(file_path: Path) -> tuple[str, str]:
    """
    Validate image file and return format and MIME type.
    
    Returns:
        Tuple of (format, mime_type)
    
    Raises:
        ValueError if validation fails
    """
    if not file_path.exists():
        raise ValueError(f"File not found: {file_path}")
    
    if not file_path.is_file():
        raise ValueError(f"Not a file: {file_path}")
    
    # Check extension
    ext = file_path.suffix.lower().lstrip(".")
    if ext not in IMAGE_FORMATS:
        raise ValueError(f"Unsupported file extension: {ext}")
    
    # Validate magic bytes
    detected_format = _detect_image_format(file_path)
    if detected_format is None:
        raise ValueError(f"File does not appear to be a valid image")
    
    # Normalize jpg/jpeg
    if detected_format == "jpg":
        detected_format = "jpeg" if ext == "jpeg" else "jpg"
    
    mime_type = IMAGE_FORMATS.get(ext)
    if mime_type is None:
        raise ValueError(f"No MIME type mapping for extension: {ext}")
    
    return detected_format, mime_type


def _get_media_cache_path() -> Path:
    """Get path to media cache file."""
    cache_dir = Path.home() / ".animica"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / "media_cache.json"


def _load_media_cache() -> dict:
    """Load media cache from disk."""
    cache_path = _get_media_cache_path()
    if not cache_path.exists():
        return {"images": []}
    
    try:
        with open(cache_path, "r") as f:
            return json.load(f)
    except Exception as e:
        console.print(f"[yellow]Warning: Failed to load cache: {e}[/yellow]")
        return {"images": []}


def _save_media_cache(cache: dict):
    """Save media cache to disk."""
    cache_path = _get_media_cache_path()
    try:
        with open(cache_path, "w") as f:
            json.dump(cache, f, indent=2)
    except Exception as e:
        console.print(f"[yellow]Warning: Failed to save cache: {e}[/yellow]")


def _add_to_cache(manifest_data: dict):
    """Add manifest to local cache."""
    cache = _load_media_cache()
    
    # Add to images list (avoid duplicates by media_id)
    media_id_hex = manifest_data["media_id"]
    cache["images"] = [
        img for img in cache.get("images", [])
        if img.get("media_id") != media_id_hex
    ]
    cache["images"].append(manifest_data)
    
    _save_media_cache(cache)


def _get_image_dimensions(file_path: Path, fmt: str) -> tuple[Optional[int], Optional[int]]:
    """
    Extract image dimensions.
    
    Returns:
        Tuple of (width, height) or (None, None) if unable to extract
    """
    try:
        # Try using PIL if available
        from PIL import Image
        with Image.open(file_path) as img:
            return img.width, img.height
    except ImportError:
        pass
    except Exception:
        pass
    
    return None, None


@image_app.command("put")
def image_put(
    file: str = typer.Argument(..., help="Path to image file"),
    name: Optional[str] = typer.Option(
        None,
        "--name",
        help="Display name for the image",
    ),
    tag: Optional[list[str]] = typer.Option(
        None,
        "--tag",
        help="Tag for categorization (can specify multiple times)",
    ),
    da_url: Optional[str] = typer.Option(
        None,
        "--da-url",
        help="DA service URL",
    ),
    from_wallet: Optional[str] = typer.Option(
        None,
        "--from",
        help="Wallet identifier (address, label, or index)",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
):
    """
    Upload an image to DA with automatic chunking.
    
    Supports PNG, JPG, JPEG, and WEBP formats.
    Files larger than 1MB are automatically chunked.
    """
    file_path = Path(file).expanduser().resolve()
    
    try:
        # Validate image
        fmt, mime_type = _validate_image_file(file_path)
        file_size = file_path.stat().st_size
        
        if not json_output:
            console.print(f"[bold]Uploading Image to DA[/bold]")
            console.print(f"  File: {file_path.name}")
            console.print(f"  Format: {fmt.upper()}")
            console.print(f"  Size: {file_size:,} bytes")
            console.print(f"  MIME: {mime_type}")
        
        # Read file and compute hashes
        with open(file_path, "rb") as f:
            file_data = f.read()
        
        import hashlib
        sha256_hash = hashlib.sha256(file_data).digest()
        sha3_256_hash = hashlib.sha3_256(file_data).digest()
        
        if not json_output:
            console.print(f"\n[yellow]→ Computing hashes...[/yellow]")
            console.print(f"  SHA-256: {sha256_hash.hex()}")
            console.print(f"  SHA3-256: {sha3_256_hash.hex()}")
        
        # Get uploader address
        if from_wallet:
            uploader_address = _load_wallet_address(from_wallet)
        else:
            uploader_address = _load_wallet_address(None)
        
        # Convert address to bytes (assuming hex format)
        if uploader_address.startswith("0x"):
            uploader_address_bytes = bytes.fromhex(uploader_address[2:])
        else:
            uploader_address_bytes = bytes.fromhex(uploader_address)
        
        if len(uploader_address_bytes) != 20:
            console.print(f"[red]Error: Invalid address length: {len(uploader_address_bytes)} bytes[/red]")
            raise typer.Exit(1)
        
        # Determine if chunking is needed
        needs_chunking = file_size > CHUNK_THRESHOLD
        
        if needs_chunking:
            # Upload with chunking
            if not json_output:
                console.print(f"\n[yellow]→ File exceeds {CHUNK_THRESHOLD:,} bytes, using chunking...[/yellow]")
            
            num_chunks = (file_size + CHUNK_SIZE - 1) // CHUNK_SIZE
            chunk_commitments = []
            
            for i in range(num_chunks):
                start = i * CHUNK_SIZE
                end = min((i + 1) * CHUNK_SIZE, file_size)
                chunk_data = file_data[start:end]
                
                if not json_output:
                    console.print(f"  Uploading chunk {i+1}/{num_chunks} ({len(chunk_data):,} bytes)...")
                
                # Upload chunk using da.cli.put_blob
                import tempfile
                with tempfile.NamedTemporaryFile(mode="wb", delete=False) as tmp:
                    tmp.write(chunk_data)
                    tmp_path = tmp.name
                
                try:
                    from da.cli import put_blob
                    result = put_blob.main([
                        "--ns", str(ENA_MEDIA_NAMESPACE),
                        "--mime", "application/octet-stream",
                        "--name", f"{file_path.name}.chunk{i}",
                        "--json",
                        tmp_path,
                    ])
                    
                    if result != 0:
                        raise RuntimeError(f"Failed to upload chunk {i+1}")
                    
                    # Parse commitment from output (captured via subprocess)
                    import subprocess
                    result = subprocess.run(
                        [
                            sys.executable, "-m", "da.cli.put_blob",
                            "--ns", str(ENA_MEDIA_NAMESPACE),
                            "--mime", "application/octet-stream",
                            "--name", f"{file_path.name}.chunk{i}",
                            "--json",
                            tmp_path,
                        ],
                        capture_output=True,
                        text=True,
                    )
                    
                    if result.returncode != 0:
                        raise RuntimeError(f"Failed to upload chunk {i+1}: {result.stderr}")
                    
                    chunk_response = json.loads(result.stdout)
                    commitment_hex = chunk_response["commitment"]
                    if commitment_hex.startswith("0x"):
                        commitment_hex = commitment_hex[2:]
                    chunk_commitments.append(bytes.fromhex(commitment_hex))
                
                finally:
                    os.unlink(tmp_path)
            
            # Create chunking params
            from da.media.manifest import ChunkingParams
            content = ChunkingParams(
                chunk_size=CHUNK_SIZE,
                num_chunks=num_chunks,
                chunk_commitments=chunk_commitments,
            )
            
            if not json_output:
                console.print(f"[green]  ✓ All {num_chunks} chunks uploaded[/green]")
        
        else:
            # Upload as single blob
            if not json_output:
                console.print(f"\n[yellow]→ Uploading to DA...[/yellow]")
            
            import tempfile
            with tempfile.NamedTemporaryFile(mode="wb", delete=False) as tmp:
                tmp.write(file_data)
                tmp_path = tmp.name
            
            try:
                import subprocess
                result = subprocess.run(
                    [
                        sys.executable, "-m", "da.cli.put_blob",
                        "--ns", str(ENA_MEDIA_NAMESPACE),
                        "--mime", mime_type,
                        "--name", name or file_path.name,
                        "--json",
                        tmp_path,
                    ],
                    capture_output=True,
                    text=True,
                )
                
                if result.returncode != 0:
                    raise RuntimeError(f"Failed to upload: {result.stderr}")
                
                blob_response = json.loads(result.stdout)
                commitment_hex = blob_response["commitment"]
                if commitment_hex.startswith("0x"):
                    commitment_hex = commitment_hex[2:]
                content = bytes.fromhex(commitment_hex)
                
                if not json_output:
                    console.print(f"[green]  ✓ Uploaded to DA[/green]")
                    console.print(f"  Commitment: {commitment_hex}")
            
            finally:
                os.unlink(tmp_path)
        
        # Get image dimensions
        width, height = _get_image_dimensions(file_path, fmt)
        
        # Create manifest
        from da.media.manifest import MediaKind, create_manifest
        
        manifest = create_manifest(
            content_commitment=content,
            kind=MediaKind.IMAGE,
            content_type=mime_type,
            byte_size=file_size,
            sha256=sha256_hash,
            sha3_256=sha3_256_hash,
            uploader_address=uploader_address_bytes,
            name=name or file_path.name,
            tags=list(tag) if tag else [],
            width=width,
            height=height,
        )
        
        if not json_output:
            console.print(f"\n[yellow]→ Creating manifest...[/yellow]")
            console.print(f"  Media ID: {manifest.media_id.hex()}")
            if width and height:
                console.print(f"  Dimensions: {width}x{height}")
        
        # Upload manifest
        manifest_cbor = manifest.to_cbor()
        
        import tempfile
        with tempfile.NamedTemporaryFile(mode="wb", delete=False) as tmp:
            tmp.write(manifest_cbor)
            tmp_path = tmp.name
        
        try:
            import subprocess
            result = subprocess.run(
                [
                    sys.executable, "-m", "da.cli.put_blob",
                    "--ns", str(ENA_MEDIA_NAMESPACE),
                    "--mime", "application/cbor",
                    "--name", f"{manifest.media_id.hex()}.manifest",
                    "--json",
                    tmp_path,
                ],
                capture_output=True,
                text=True,
            )
            
            if result.returncode != 0:
                raise RuntimeError(f"Failed to upload manifest: {result.stderr}")
            
            manifest_response = json.loads(result.stdout)
            manifest_commitment_hex = manifest_response["commitment"]
            
            if not json_output:
                console.print(f"[green]  ✓ Manifest uploaded[/green]")
                console.print(f"  Manifest commitment: {manifest_commitment_hex}")
        
        finally:
            os.unlink(tmp_path)
        
        # Add to local cache
        cache_entry = {
            "media_id": manifest.media_id.hex(),
            "name": manifest.name,
            "kind": manifest.kind.value,
            "content_type": manifest.content_type,
            "byte_size": manifest.byte_size,
            "tags": manifest.tags,
            "created_at": manifest.created_at,
            "width": manifest.width,
            "height": manifest.height,
            "manifest_commitment": manifest_commitment_hex,
        }
        _add_to_cache(cache_entry)
        
        # Output results
        if json_output:
            output = {
                "media_id": manifest.media_id.hex(),
                "name": manifest.name,
                "size": file_size,
                "format": fmt,
                "mime_type": mime_type,
                "sha256": sha256_hash.hex(),
                "sha3_256": sha3_256_hash.hex(),
                "manifest_commitment": manifest_commitment_hex,
                "chunked": needs_chunking,
                "num_chunks": num_chunks if needs_chunking else 1,
                "width": width,
                "height": height,
                "tags": manifest.tags,
            }
            console.print(json.dumps(output, indent=2))
        else:
            console.print(f"\n[bold green]✓ Image uploaded successfully![/bold green]")
            console.print(f"\n[bold]Media ID:[/bold] {manifest.media_id.hex()}")
            console.print(f"\n[dim]Retrieve with:[/dim]")
            console.print(f"[dim]  animica ena image get {manifest.media_id.hex()}[/dim]")
    
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        import traceback
        if not json_output:
            console.print(f"[dim]{traceback.format_exc()}[/dim]")
        raise typer.Exit(1)


@image_app.command("get")
def image_get(
    media_id: str = typer.Argument(..., help="Media ID (hex)"),
    output: Optional[str] = typer.Option(
        None,
        "--output", "-o",
        help="Output file path (default: use name from manifest)",
    ),
    da_url: Optional[str] = typer.Option(
        None,
        "--da-url",
        help="DA service URL",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output metadata as JSON (image saved to file)",
    ),
):
    """
    Retrieve an image by media_id.
    
    Downloads the manifest and content from DA, reassembling chunks if needed.
    """
    try:
        # Normalize media_id
        if media_id.startswith("0x"):
            media_id = media_id[2:]
        
        if len(media_id) != 64:
            raise ValueError("Media ID must be 32 bytes (64 hex chars)")
        
        media_id_bytes = bytes.fromhex(media_id)
        
        if not json_output:
            console.print(f"[bold]Retrieving Image[/bold]")
            console.print(f"  Media ID: {media_id}")
        
        # First, try to get manifest from cache
        cache = _load_media_cache()
        cached_manifest = None
        for img in cache.get("images", []):
            if img.get("media_id") == media_id:
                cached_manifest = img
                break
        
        if cached_manifest and not json_output:
            console.print(f"  Name: {cached_manifest.get('name', 'unknown')}")
            manifest_commitment = cached_manifest.get("manifest_commitment")
            if manifest_commitment:
                console.print(f"  Manifest commitment: {manifest_commitment}")
        
        # Download manifest
        if not json_output:
            console.print(f"\n[yellow]→ Downloading manifest...[/yellow]")
        
        # We need the manifest commitment - if not in cache, we can't retrieve
        if not cached_manifest or not cached_manifest.get("manifest_commitment"):
            console.print(f"[red]Error: Manifest commitment not found in cache[/red]")
            console.print(f"[dim]Note: Only images uploaded with this CLI can be retrieved automatically[/dim]")
            raise typer.Exit(1)
        
        manifest_commitment = cached_manifest["manifest_commitment"]
        
        import subprocess
        result = subprocess.run(
            [
                sys.executable, "-m", "da.cli.get_blob",
                "--commit", manifest_commitment,
                "--out", "-",
            ],
            capture_output=True,
        )
        
        if result.returncode != 0:
            raise RuntimeError(f"Failed to download manifest: {result.stderr.decode()}")
        
        manifest_cbor = result.stdout
        
        # Parse manifest
        from da.media.manifest import MediaManifest
        manifest = MediaManifest.from_cbor(manifest_cbor)
        
        if not json_output:
            console.print(f"[green]  ✓ Manifest retrieved[/green]")
            console.print(f"  Name: {manifest.name}")
            console.print(f"  Size: {manifest.byte_size:,} bytes")
            console.print(f"  Type: {manifest.content_type}")
            if manifest.width and manifest.height:
                console.print(f"  Dimensions: {manifest.width}x{manifest.height}")
        
        # Download content
        if not json_output:
            console.print(f"\n[yellow]→ Downloading content...[/yellow]")
        
        from da.media.manifest import ChunkingParams
        
        if isinstance(manifest.content, ChunkingParams):
            # Download and reassemble chunks
            num_chunks = manifest.content.num_chunks
            if not json_output:
                console.print(f"  Chunked: {num_chunks} chunks")
            
            content_data = b""
            for i, commitment in enumerate(manifest.content.chunk_commitments):
                if not json_output:
                    console.print(f"  Downloading chunk {i+1}/{num_chunks}...")
                
                result = subprocess.run(
                    [
                        sys.executable, "-m", "da.cli.get_blob",
                        "--commit", "0x" + commitment.hex(),
                        "--out", "-",
                    ],
                    capture_output=True,
                )
                
                if result.returncode != 0:
                    raise RuntimeError(f"Failed to download chunk {i+1}: {result.stderr.decode()}")
                
                content_data += result.stdout
            
            if not json_output:
                console.print(f"[green]  ✓ All chunks downloaded and reassembled[/green]")
        
        else:
            # Download single blob
            commitment_hex = manifest.content.hex()
            
            result = subprocess.run(
                [
                    sys.executable, "-m", "da.cli.get_blob",
                    "--commit", "0x" + commitment_hex,
                    "--out", "-",
                ],
                capture_output=True,
            )
            
            if result.returncode != 0:
                raise RuntimeError(f"Failed to download content: {result.stderr.decode()}")
            
            content_data = result.stdout
            
            if not json_output:
                console.print(f"[green]  ✓ Content downloaded[/green]")
        
        # Verify integrity
        from da.media.manifest import verify_manifest
        if not verify_manifest(manifest, content_data):
            console.print(f"[red]Warning: Content failed integrity check![/red]")
        elif not json_output:
            console.print(f"[green]  ✓ Integrity verified[/green]")
        
        # Save to file
        if output:
            output_path = Path(output).expanduser().resolve()
        else:
            output_path = Path(manifest.name or f"{media_id}.bin")
        
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(content_data)
        
        if json_output:
            output_data = {
                "media_id": media_id,
                "name": manifest.name,
                "size": manifest.byte_size,
                "content_type": manifest.content_type,
                "width": manifest.width,
                "height": manifest.height,
                "tags": manifest.tags,
                "output_file": str(output_path),
                "verified": verify_manifest(manifest, content_data),
            }
            console.print(json.dumps(output_data, indent=2))
        else:
            console.print(f"\n[bold green]✓ Image saved to: {output_path}[/bold green]")
    
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        import traceback
        if not json_output:
            console.print(f"[dim]{traceback.format_exc()}[/dim]")
        raise typer.Exit(1)


@image_app.command("verify")
def image_verify(
    media_id: str = typer.Argument(..., help="Media ID (hex)"),
    file: str = typer.Argument(..., help="Path to local file to verify"),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
):
    """
    Verify an image matches the manifest.
    
    Computes hashes of the local file and compares with the manifest.
    """
    try:
        # Normalize media_id
        if media_id.startswith("0x"):
            media_id = media_id[2:]
        
        if len(media_id) != 64:
            raise ValueError("Media ID must be 32 bytes (64 hex chars)")
        
        file_path = Path(file).expanduser().resolve()
        if not file_path.exists():
            raise ValueError(f"File not found: {file_path}")
        
        if not json_output:
            console.print(f"[bold]Verifying Image[/bold]")
            console.print(f"  Media ID: {media_id}")
            console.print(f"  File: {file_path}")
        
        # Get manifest from cache
        cache = _load_media_cache()
        cached_manifest = None
        for img in cache.get("images", []):
            if img.get("media_id") == media_id:
                cached_manifest = img
                break
        
        if not cached_manifest or not cached_manifest.get("manifest_commitment"):
            console.print(f"[red]Error: Manifest not found in cache[/red]")
            raise typer.Exit(1)
        
        manifest_commitment = cached_manifest["manifest_commitment"]
        
        # Download manifest
        if not json_output:
            console.print(f"\n[yellow]→ Downloading manifest...[/yellow]")
        
        import subprocess
        result = subprocess.run(
            [
                sys.executable, "-m", "da.cli.get_blob",
                "--commit", manifest_commitment,
                "--out", "-",
            ],
            capture_output=True,
        )
        
        if result.returncode != 0:
            raise RuntimeError(f"Failed to download manifest: {result.stderr.decode()}")
        
        manifest_cbor = result.stdout
        
        # Parse manifest
        from da.media.manifest import MediaManifest, verify_manifest
        manifest = MediaManifest.from_cbor(manifest_cbor)
        
        # Read local file
        with open(file_path, "rb") as f:
            file_data = f.read()
        
        # Verify
        if not json_output:
            console.print(f"\n[yellow]→ Verifying integrity...[/yellow]")
        
        is_valid = verify_manifest(manifest, file_data)
        
        # Check individual fields
        import hashlib
        sha256_match = hashlib.sha256(file_data).digest() == manifest.integrity.sha256
        sha3_256_match = hashlib.sha3_256(file_data).digest() == manifest.integrity.sha3_256
        size_match = len(file_data) == manifest.byte_size
        
        if json_output:
            output = {
                "media_id": media_id,
                "file": str(file_path),
                "valid": is_valid,
                "checks": {
                    "size": size_match,
                    "sha256": sha256_match,
                    "sha3_256": sha3_256_match,
                },
                "manifest": {
                    "name": manifest.name,
                    "size": manifest.byte_size,
                    "content_type": manifest.content_type,
                },
            }
            console.print(json.dumps(output, indent=2))
        else:
            if is_valid:
                console.print(f"\n[bold green]✓ Verification passed![/bold green]")
                console.print(f"  Size: {size_match} ({'✓' if size_match else '✗'})")
                console.print(f"  SHA-256: {sha256_match} ({'✓' if sha256_match else '✗'})")
                console.print(f"  SHA3-256: {sha3_256_match} ({'✓' if sha3_256_match else '✗'})")
            else:
                console.print(f"\n[bold red]✗ Verification failed![/bold red]")
                console.print(f"  Size: {size_match} ({'✓' if size_match else '✗'})")
                console.print(f"  SHA-256: {sha256_match} ({'✓' if sha256_match else '✗'})")
                console.print(f"  SHA3-256: {sha3_256_match} ({'✓' if sha3_256_match else '✗'})")
                raise typer.Exit(1)
    
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@image_app.command("list")
def image_list(
    tag: Optional[str] = typer.Option(
        None,
        "--tag",
        help="Filter by tag",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
):
    """
    List uploaded images.
    
    Shows images from the local cache with optional tag filtering.
    """
    try:
        cache = _load_media_cache()
        images = cache.get("images", [])
        
        # Filter by tag if specified
        if tag:
            images = [img for img in images if tag in img.get("tags", [])]
        
        if json_output:
            console.print(json.dumps({"images": images}, indent=2))
        else:
            if not images:
                if tag:
                    console.print(f"[yellow]No images found with tag: {tag}[/yellow]")
                else:
                    console.print("[yellow]No images in cache[/yellow]")
                    console.print("[dim]Upload an image with: animica ena image put <file>[/dim]")
                return
            
            console.print(f"[bold]Uploaded Images[/bold] ({len(images)} total)\n")
            
            # Create table
            table = Table(show_header=True, header_style="bold cyan")
            table.add_column("Media ID", style="yellow", no_wrap=True)
            table.add_column("Name", style="white")
            table.add_column("Size", style="green", justify="right")
            table.add_column("Type", style="cyan")
            table.add_column("Dimensions", style="magenta")
            table.add_column("Tags", style="blue")
            table.add_column("Created", style="dim")
            
            for img in images:
                media_id = img.get("media_id", "")[:16] + "..."
                name = img.get("name", "")
                size = img.get("byte_size", 0)
                size_str = f"{size:,}" if size < 1024 else f"{size / 1024:.1f}K" if size < 1024 * 1024 else f"{size / (1024 * 1024):.1f}M"
                content_type = img.get("content_type", "").replace("image/", "")
                
                width = img.get("width")
                height = img.get("height")
                dims = f"{width}x{height}" if width and height else "-"
                
                tags_str = ", ".join(img.get("tags", [])) or "-"
                
                import datetime
                created = img.get("created_at", 0)
                created_str = datetime.datetime.fromtimestamp(created).strftime("%Y-%m-%d %H:%M") if created else "-"
                
                table.add_row(
                    media_id,
                    name,
                    size_str,
                    content_type,
                    dims,
                    tags_str,
                    created_str,
                )
            
            console.print(table)
            
            console.print(f"\n[dim]Retrieve an image with:[/dim]")
            console.print(f"[dim]  animica ena image get <media_id>[/dim]")
    
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


# ============================================================================
# Train Commands
# ============================================================================

@train_app.command("submit")
def train_submit(
    plan: str = typer.Option(..., "--plan", help="Training plan JSON file path"),
    budget: str = typer.Option(..., "--budget", help="Budget in ANM (e.g., '10.5')"),
    from_wallet: Optional[str] = typer.Option(
        None,
        "--from",
        help="Wallet identifier (address, label, or index)",
    ),
    endpoint: Optional[str] = typer.Option(
        None,
        "--endpoint",
        help="ENA endpoint URL",
    ),
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="Animica RPC URL",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
):
    """Submit a training job with plan and budget."""
    _ensure_httpx()
    
    ena_endpoint = endpoint or _get_ena_endpoint()
    animica_rpc = rpc_url or _get_rpc_url()
    
    # Load training plan
    plan_path = Path(plan)
    if not plan_path.exists():
        console.print(f"[red]Error: Plan file not found: {plan}[/red]")
        raise typer.Exit(1)
    
    try:
        with open(plan_path) as f:
            plan_data = json.load(f)
    except json.JSONDecodeError as e:
        console.print(f"[red]Error: Invalid JSON in plan file: {e}[/red]")
        raise typer.Exit(1)
    
    # Parse budget
    budget_units = _parse_amount(budget)
    
    # Load wallet address
    payer_address = _load_wallet_address(from_wallet)
    
    if not json_output:
        console.print(f"[bold]Submitting Training Job[/bold]\n")
        console.print(f"Plan: {plan}")
        console.print(f"Budget: {_format_amount(budget_units)} ANM")
        console.print(f"From: {payer_address}\n")
    
    # Submit training job
    submit_url = f"{ena_endpoint}/v1/training/submit"
    request_data = {
        "plan": plan_data,
        "budget": budget_units,
        "payer": payer_address,
    }
    
    try:
        with httpx.Client(timeout=30) as client:
            response = client.post(submit_url, json=request_data)
            response.raise_for_status()
            result = response.json()
        
        if json_output:
            console.print(json.dumps(result, indent=2))
        else:
            job_id = result.get("job_id")
            console.print(f"[green]✓ Job submitted successfully[/green]")
            console.print(f"Job ID: {job_id}")
            console.print(f"\n[dim]Watch progress with:[/dim]")
            console.print(f"[dim]  animica ena train watch {job_id}[/dim]")
    
    except httpx.HTTPError as e:
        console.print(f"[red]Error: Failed to submit job: {e}[/red]")
        raise typer.Exit(1)


@train_app.command("list")
def train_list(
    endpoint: Optional[str] = typer.Option(
        None,
        "--endpoint",
        help="ENA endpoint URL",
    ),
    status: Optional[str] = typer.Option(
        None,
        "--status",
        help="Filter by status (pending, running, completed, failed)",
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
    """List training jobs."""
    _ensure_httpx()
    
    ena_endpoint = endpoint or _get_ena_endpoint()
    
    # Build query parameters
    params = {"limit": limit}
    if status:
        params["status"] = status
    
    list_url = f"{ena_endpoint}/v1/training/list"
    
    try:
        with httpx.Client(timeout=30) as client:
            response = client.get(list_url, params=params)
            response.raise_for_status()
            data = response.json()
        
        jobs = data.get("jobs", [])
        
        if json_output:
            console.print(json.dumps(data, indent=2))
        else:
            if not jobs:
                console.print("[yellow]No training jobs found[/yellow]")
                console.print("[dim]Submit a job with: animica ena train submit --plan <file> --budget <amount>[/dim]")
                return
            
            console.print(f"[bold]Training Jobs[/bold] ({len(jobs)} jobs)\n")
            
            table = Table(show_header=True, header_style="bold cyan")
            table.add_column("Job ID", style="yellow", no_wrap=True)
            table.add_column("Status", style="white")
            table.add_column("Progress", style="green", justify="right")
            table.add_column("Budget", style="cyan", justify="right")
            table.add_column("Spent", style="magenta", justify="right")
            table.add_column("Created", style="dim")
            
            for job in jobs:
                job_id = job.get("job_id", "")[:16] + "..."
                status = job.get("status", "unknown")
                progress = job.get("progress", 0)
                budget = _format_amount(job.get("budget", 0))
                spent = _format_amount(job.get("spent", 0))
                
                import datetime
                created = job.get("created_at", 0)
                created_str = datetime.datetime.fromtimestamp(created).strftime("%Y-%m-%d %H:%M") if created else "-"
                
                # Color code status
                status_colored = status
                if status == "completed":
                    status_colored = f"[green]{status}[/green]"
                elif status == "failed":
                    status_colored = f"[red]{status}[/red]"
                elif status == "running":
                    status_colored = f"[yellow]{status}[/yellow]"
                
                table.add_row(
                    job_id,
                    status_colored,
                    f"{progress}%",
                    f"{budget} ANM",
                    f"{spent} ANM",
                    created_str,
                )
            
            console.print(table)
    
    except httpx.HTTPError as e:
        console.print(f"[red]Error: Failed to list jobs: {e}[/red]")
        raise typer.Exit(1)


@train_app.command("watch")
def train_watch(
    job_id: str = typer.Argument(..., help="Job ID to watch"),
    endpoint: Optional[str] = typer.Option(
        None,
        "--endpoint",
        help="ENA endpoint URL",
    ),
    interval: int = typer.Option(
        5,
        "--interval",
        help="Update interval in seconds",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON (single update)",
    ),
):
    """Watch training job status."""
    _ensure_httpx()
    
    ena_endpoint = endpoint or _get_ena_endpoint()
    status_url = f"{ena_endpoint}/v1/training/status/{job_id}"
    
    if json_output:
        # Single update for JSON mode
        try:
            with httpx.Client(timeout=30) as client:
                response = client.get(status_url)
                response.raise_for_status()
                data = response.json()
            
            console.print(json.dumps(data, indent=2))
        
        except httpx.HTTPError as e:
            console.print(f"[red]Error: Failed to get job status: {e}[/red]")
            raise typer.Exit(1)
        
        return
    
    # Live watching mode
    console.print(f"[bold]Watching Job: {job_id}[/bold]")
    console.print("[dim]Press Ctrl+C to stop[/dim]\n")
    
    try:
        while True:
            try:
                with httpx.Client(timeout=30) as client:
                    response = client.get(status_url)
                    response.raise_for_status()
                    job = response.json()
                
                status = job.get("status", "unknown")
                progress = job.get("progress", 0)
                budget = _format_amount(job.get("budget", 0))
                spent = _format_amount(job.get("spent", 0))
                message = job.get("message", "")
                
                # Clear previous line
                console.print("\033[F\033[K" * 3, end="")
                
                console.print(f"Status: {status}")
                console.print(f"Progress: {progress}%")
                console.print(f"Budget: {budget} ANM | Spent: {spent} ANM")
                
                if message:
                    console.print(f"[dim]{message}[/dim]")
                
                # Stop watching if job is complete
                if status in ["completed", "failed", "cancelled"]:
                    console.print(f"\n[green]✓ Job {status}[/green]")
                    break
                
                time.sleep(interval)
            
            except httpx.HTTPError as e:
                console.print(f"[red]Error: Failed to get job status: {e}[/red]")
                raise typer.Exit(1)
    
    except KeyboardInterrupt:
        console.print("\n[yellow]Stopped watching[/yellow]")


# ============================================================================
# Checkpoints Commands
# ============================================================================

@checkpoints_app.command("list")
def checkpoints_list(
    endpoint: Optional[str] = typer.Option(
        None,
        "--endpoint",
        help="ENA endpoint URL",
    ),
    model: Optional[str] = typer.Option(
        None,
        "--model",
        help="Filter by model name",
    ),
    limit: int = typer.Option(
        20,
        "--limit",
        help="Maximum number of checkpoints to show",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
):
    """List available checkpoints."""
    _ensure_httpx()
    
    ena_endpoint = endpoint or _get_ena_endpoint()
    
    # Build query parameters
    params = {"limit": limit}
    if model:
        params["model"] = model
    
    list_url = f"{ena_endpoint}/v1/checkpoints/list"
    
    try:
        with httpx.Client(timeout=30) as client:
            response = client.get(list_url, params=params)
            response.raise_for_status()
            data = response.json()
        
        checkpoints = data.get("checkpoints", [])
        
        if json_output:
            console.print(json.dumps(data, indent=2))
        else:
            if not checkpoints:
                console.print("[yellow]No checkpoints found[/yellow]")
                return
            
            console.print(f"[bold]Available Checkpoints[/bold] ({len(checkpoints)} checkpoints)\n")
            
            table = Table(show_header=True, header_style="bold cyan")
            table.add_column("Version", style="yellow", no_wrap=True)
            table.add_column("Model", style="white")
            table.add_column("Epoch", style="green", justify="right")
            table.add_column("Size", style="cyan", justify="right")
            table.add_column("Published", style="dim")
            
            for ckpt in checkpoints:
                version = ckpt.get("version", "")
                model_name = ckpt.get("model", "")
                epoch = str(ckpt.get("epoch", ""))
                size = ckpt.get("size_bytes", 0)
                size_str = f"{size / (1024**3):.2f} GB" if size > 1024**3 else f"{size / (1024**2):.2f} MB"
                
                import datetime
                published = ckpt.get("published_at", 0)
                published_str = datetime.datetime.fromtimestamp(published).strftime("%Y-%m-%d %H:%M") if published else "-"
                
                table.add_row(
                    version,
                    model_name,
                    epoch,
                    size_str,
                    published_str,
                )
            
            console.print(table)
            console.print(f"\n[dim]Fetch a checkpoint with:[/dim]")
            console.print(f"[dim]  animica ena checkpoints fetch <version>[/dim]")
    
    except httpx.HTTPError as e:
        console.print(f"[red]Error: Failed to list checkpoints: {e}[/red]")
        raise typer.Exit(1)


@checkpoints_app.command("publish")
def checkpoints_publish(
    job_id: str = typer.Argument(..., help="Training job ID"),
    endpoint: Optional[str] = typer.Option(
        None,
        "--endpoint",
        help="ENA endpoint URL",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
):
    """Manually trigger checkpoint publish."""
    _ensure_httpx()
    
    ena_endpoint = endpoint or _get_ena_endpoint()
    
    if not json_output:
        console.print(f"[bold]Publishing Checkpoint[/bold]\n")
        console.print(f"Job ID: {job_id}")
    
    publish_url = f"{ena_endpoint}/v1/checkpoints/publish"
    request_data = {"job_id": job_id}
    
    try:
        with httpx.Client(timeout=60) as client:
            response = client.post(publish_url, json=request_data)
            response.raise_for_status()
            result = response.json()
        
        if json_output:
            console.print(json.dumps(result, indent=2))
        else:
            version = result.get("version")
            console.print(f"[green]✓ Checkpoint published successfully[/green]")
            console.print(f"Version: {version}")
    
    except httpx.HTTPError as e:
        console.print(f"[red]Error: Failed to publish checkpoint: {e}[/red]")
        raise typer.Exit(1)


@checkpoints_app.command("fetch")
def checkpoints_fetch(
    version: str = typer.Argument(..., help="Checkpoint version to fetch"),
    output: Optional[str] = typer.Option(
        None,
        "--output",
        "-o",
        help="Output directory (default: ./checkpoints)",
    ),
    endpoint: Optional[str] = typer.Option(
        None,
        "--endpoint",
        help="ENA endpoint URL",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
):
    """Fetch a specific checkpoint version."""
    _ensure_httpx()
    
    ena_endpoint = endpoint or _get_ena_endpoint()
    output_dir = Path(output) if output else Path("./checkpoints")
    
    # Create output directory
    output_dir.mkdir(parents=True, exist_ok=True)
    
    if not json_output:
        console.print(f"[bold]Fetching Checkpoint[/bold]\n")
        console.print(f"Version: {version}")
        console.print(f"Output: {output_dir}\n")
    
    fetch_url = f"{ena_endpoint}/v1/checkpoints/fetch/{version}"
    
    try:
        with httpx.Client(timeout=300) as client:
            with console.status("[bold green]Downloading checkpoint..."):
                response = client.get(fetch_url)
                response.raise_for_status()
            
            # Save checkpoint file
            checkpoint_file = output_dir / f"{version}.ckpt"
            with open(checkpoint_file, "wb") as f:
                f.write(response.content)
        
        if json_output:
            result = {
                "version": version,
                "path": str(checkpoint_file),
                "size_bytes": len(response.content),
            }
            console.print(json.dumps(result, indent=2))
        else:
            size_mb = len(response.content) / (1024**2)
            console.print(f"[green]✓ Checkpoint downloaded successfully[/green]")
            console.print(f"Size: {size_mb:.2f} MB")
            console.print(f"Path: {checkpoint_file}")
    
    except httpx.HTTPError as e:
        console.print(f"[red]Error: Failed to fetch checkpoint: {e}[/red]")
        raise typer.Exit(1)


# ============================================================================
# Models Commands
# ============================================================================

@models_app.command("pull")
def models_pull(
    model: str = typer.Argument(..., help="Model name to pull"),
    endpoint: Optional[str] = typer.Option(
        None,
        "--endpoint",
        help="ENA endpoint URL",
    ),
    output: Optional[str] = typer.Option(
        None,
        "--output",
        "-o",
        help="Output directory (default: ./models)",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
):
    """Pull/download a model."""
    _ensure_httpx()
    
    ena_endpoint = endpoint or _get_ena_endpoint()
    output_dir = Path(output) if output else Path("./models")
    
    # Create output directory
    output_dir.mkdir(parents=True, exist_ok=True)
    
    if not json_output:
        console.print(f"[bold]Pulling Model[/bold]\n")
        console.print(f"Model: {model}")
        console.print(f"Output: {output_dir}\n")
    
    pull_url = f"{ena_endpoint}/v1/models/pull/{model}"
    
    try:
        with httpx.Client(timeout=600) as client:
            with console.status("[bold green]Downloading model..."):
                response = client.get(pull_url)
                response.raise_for_status()
            
            # Save model file
            model_file = output_dir / f"{model.replace('/', '_')}.model"
            with open(model_file, "wb") as f:
                f.write(response.content)
        
        if json_output:
            result = {
                "model": model,
                "path": str(model_file),
                "size_bytes": len(response.content),
            }
            console.print(json.dumps(result, indent=2))
        else:
            size_mb = len(response.content) / (1024**2)
            console.print(f"[green]✓ Model downloaded successfully[/green]")
            console.print(f"Size: {size_mb:.2f} MB")
            console.print(f"Path: {model_file}")
    
    except httpx.HTTPError as e:
        console.print(f"[red]Error: Failed to pull model: {e}[/red]")
        raise typer.Exit(1)


@models_app.command("export")
def models_export(
    model: str = typer.Argument(..., help="Model name to export"),
    format: str = typer.Option(
        "onnx",
        "--format",
        help="Export format (onnx, tensorrt, safetensors)",
    ),
    endpoint: Optional[str] = typer.Option(
        None,
        "--endpoint",
        help="ENA endpoint URL",
    ),
    output: Optional[str] = typer.Option(
        None,
        "--output",
        "-o",
        help="Output file path",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
):
    """Export a model to different format."""
    _ensure_httpx()
    
    ena_endpoint = endpoint or _get_ena_endpoint()
    
    if not json_output:
        console.print(f"[bold]Exporting Model[/bold]\n")
        console.print(f"Model: {model}")
        console.print(f"Format: {format}\n")
    
    export_url = f"{ena_endpoint}/v1/models/export"
    request_data = {
        "model": model,
        "format": format,
    }
    
    try:
        with httpx.Client(timeout=600) as client:
            with console.status("[bold green]Exporting model..."):
                response = client.post(export_url, json=request_data)
                response.raise_for_status()
            
            # Save exported model
            if output:
                output_file = Path(output)
            else:
                output_file = Path(f"{model.replace('/', '_')}.{format}")
            
            with open(output_file, "wb") as f:
                f.write(response.content)
        
        if json_output:
            result = {
                "model": model,
                "format": format,
                "path": str(output_file),
                "size_bytes": len(response.content),
            }
            console.print(json.dumps(result, indent=2))
        else:
            size_mb = len(response.content) / (1024**2)
            console.print(f"[green]✓ Model exported successfully[/green]")
            console.print(f"Format: {format}")
            console.print(f"Size: {size_mb:.2f} MB")
            console.print(f"Path: {output_file}")
    
    except httpx.HTTPError as e:
        console.print(f"[red]Error: Failed to export model: {e}[/red]")
        raise typer.Exit(1)


# ============================================================================
# Serve Commands
# ============================================================================

@serve_app.command("start")
def serve_start(
    model: Optional[str] = typer.Option(
        None,
        "--model",
        help="Model to serve (default: ena.latest)",
    ),
    port: int = typer.Option(
        8000,
        "--port",
        help="Port to listen on",
    ),
    host: str = typer.Option(
        "127.0.0.1",
        "--host",
        help="Host to bind to",
    ),
    daemon: bool = typer.Option(
        False,
        "--daemon",
        "-d",
        help="Run in background",
    ),
):
    """Start local inference daemon."""
    import subprocess
    
    model_arg = model or "ena.latest"
    
    console.print(f"[bold]Starting ENA Inference Daemon[/bold]\n")
    console.print(f"Model: {model_arg}")
    console.print(f"Host: {host}")
    console.print(f"Port: {port}\n")
    
    # Build command
    cmd = [
        "python",
        "-m",
        "ena.server",
        "--model",
        model_arg,
        "--host",
        host,
        "--port",
        str(port),
    ]
    
    try:
        if daemon:
            # Run in background
            console.print("[yellow]Starting daemon...[/yellow]")
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            console.print(f"[green]✓ Daemon started successfully[/green]")
            console.print(f"Endpoint: http://{host}:{port}")
            console.print(f"\n[dim]Test with:[/dim]")
            console.print(f"[dim]  animica ena infer --local 'Hello, world!'[/dim]")
        else:
            # Run in foreground
            console.print("[yellow]Starting server (Ctrl+C to stop)...[/yellow]\n")
            subprocess.run(cmd)
    
    except FileNotFoundError:
        console.print("[red]Error: ENA server module not found[/red]")
        console.print("[yellow]Install ENA server dependencies with:[/yellow]")
        console.print("[dim]  pip install animica[ena][/dim]")
        raise typer.Exit(1)
    except KeyboardInterrupt:
        console.print("\n[yellow]Server stopped[/yellow]")


if __name__ == "__main__":
    app()
