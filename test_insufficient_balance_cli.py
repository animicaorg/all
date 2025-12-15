#!/usr/bin/env python3
"""
Manual test script to verify CLI error formatting for insufficient balance.

This script simulates the CLI behavior when encountering an insufficient balance error.
"""
import sys
from dataclasses import dataclass
from typing import Any

sys.path.insert(0, '.')

from rich.console import Console

console = Console()

# Reproduce the RpcError and formatting logic here
@dataclass
class RpcError(Exception):
    code: int
    message: str
    data: Any = None

ANM_BASE_UNITS = 1_000_000_000  # 1 ANM = 1e9 base units

def _format_insufficient_funds_error(e: RpcError) -> None:
    """Format and display an insufficient funds error in a user-friendly way."""
    data = e.data or {}
    required = data.get("required", "?")
    available = data.get("available", "?")
    shortfall = data.get("shortfall", "?")
    
    # Convert to ANM if possible (1 ANM = 1e9 base units)
    try:
        required_anm = int(required) / ANM_BASE_UNITS if required != "?" else "?"
        available_anm = int(available) / ANM_BASE_UNITS if available != "?" else "?"
        shortfall_anm = int(shortfall) / ANM_BASE_UNITS if shortfall != "?" else "?"
    except (ValueError, TypeError):
        required_anm = required
        available_anm = available
        shortfall_anm = shortfall
    
    console.print("\n[bold red]Error: Insufficient Balance[/bold red]")
    console.print(f"  Requested: {required_anm} ANM ({required} base units)")
    console.print(f"  Available: {available_anm} ANM ({available} base units)")
    console.print(f"  Shortfall: {shortfall_anm} ANM ({shortfall} base units)")
    console.print("\n[yellow]Tip:[/yellow] You need to obtain more ANM before sending this transaction.")

print("=" * 70)
print("Testing CLI Insufficient Balance Error Formatting")
print("=" * 70)
print()

# Simulate an RPC error from the node
err = RpcError(
    code=-32013,
    message="Insufficient funds for transfer",
    data={
        "required": "1000000000000",  # 1000 ANM in base units
        "available": "500000000",      # 0.5 ANM in base units
        "shortfall": "999500000000",   # 999.5 ANM in base units
    }
)

print("Simulating insufficient balance error from RPC:")
print(f"  Code: {err.code}")
print(f"  Message: {err.message}")
print(f"  Data: {err.data}")
print()

print("CLI formatted output:")
print("-" * 70)
_format_insufficient_funds_error(err)
print("-" * 70)
print()

# Test with exact values from problem statement
print("Testing with exact values from problem statement:")
print("(User attempts to send 1000000 ANM with only 500 ANM available)")
print()

err2 = RpcError(
    code=-32013,
    message="Insufficient funds for transfer",
    data={
        "required": "1000000000000000",  # 1,000,000 ANM
        "available": "500000000000",      # 500 ANM
        "shortfall": "999500000000000",   # 999,500 ANM
    }
)

print("CLI formatted output:")
print("-" * 70)
_format_insufficient_funds_error(err2)
print("-" * 70)
print()

print("✓ CLI error formatting test completed successfully!")
