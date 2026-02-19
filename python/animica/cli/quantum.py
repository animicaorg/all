"""
Quantum CLI commands for Animica.

Provides quantum contribution and management commands.
"""

from __future__ import annotations

import typer

from .quantum_contribute import quantum_contribute_app

app = typer.Typer(
    name="quantum",
    help="Quantum computation and contribution commands",
    no_args_is_help=True,
)

# Add the contribute subcommand
app.add_typer(quantum_contribute_app, name="contribute")

__all__ = ["app"]
