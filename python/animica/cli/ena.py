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

# Add ena module to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../"))

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore

console = Console()
app = typer.Typer(help="ENA LLM inference commands")

# Default configuration
DEFAULT_ENA_ENDPOINT = os.getenv("ENA_ENDPOINT", "https://ena.animica.org")
DEFAULT_RPC_URL = os.getenv("ANIMICA_RPC_URL", "https://mainnet.animica.org/rpc")

ANM_BASE_UNITS = 1_000_000_000  # 1 ANM = 1e9 base units


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


@app.command("models")
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
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
):
    """Run inference with payment including AICF contribution."""
    _ensure_httpx()
    
    ena_endpoint = endpoint or _get_ena_endpoint()
    animica_rpc = rpc_url or _get_rpc_url()
    
    # Load wallet address
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


@app.command("status")
def check_status(
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


# AICF commands group
aicf_app = typer.Typer(help="AICF (AI Compute Fund) commands")
app.add_typer(aicf_app, name="aicf")


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


if __name__ == "__main__":
    app()
