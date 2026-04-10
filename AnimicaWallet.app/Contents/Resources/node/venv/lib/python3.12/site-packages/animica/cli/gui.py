"""GUI subcommands for Animica CLI.

Provides graphical user interface launchers:
  - animica gui miner  : Launch the Qt GUI miner application
"""

from __future__ import annotations

import os
import sys
import subprocess
from pathlib import Path

import typer

app = typer.Typer(help="Graphical user interface tools")


@app.command("miner")
def launch_gui_miner() -> None:
    """Launch the Animica GUI Miner application.
    
    This is a production-quality Qt desktop application for mining with:
    - First-run wizard for easy setup
    - Real-time dashboard with mining stats
    - Device configuration (CPU/GPU/ASIC)
    - Pool and solo mining modes
    - Real-time logs and graphs
    - Dark theme and system tray support
    
    The GUI miner can also be launched directly with:
      animica-miner-gui
    """
    # Check if PySide6 is available
    try:
        import PySide6
    except ImportError:
        typer.echo(
            "Error: PySide6 not installed. The GUI miner requires Qt for Python.",
            err=True
        )
        typer.echo(
            "\nInstall with: pip install PySide6",
            err=True
        )
        raise typer.Exit(code=1)
    
    # Check if the GUI miner package is available
    try:
        import animica_miner_gui
    except ImportError:
        typer.echo(
            "Error: animica-miner-gui package not installed.",
            err=True
        )
        typer.echo(
            "\nInstall from apps/miner-gui/:",
            err=True
        )
        typer.echo(
            "  cd apps/miner-gui && pip install -e .",
            err=True
        )
        raise typer.Exit(code=1)
    
    # Launch the GUI miner
    typer.echo("Launching Animica GUI Miner...")
    
    try:
        from animica_miner_gui.main import main as gui_main
        sys.exit(gui_main())
    except Exception as e:
        typer.echo(f"Error launching GUI miner: {e}", err=True)
        raise typer.Exit(code=1)
