"""GUI subcommands for Animica CLI.

Provides graphical user interface launchers:
  - animica gui miner          : Launch the Qt GUI miner (proof-of-work by default)
  - animica gui miner --unified: Launch straight into unified "mine + AI" mode
  - animica gui full           : Alias for `gui miner --unified`
"""

from __future__ import annotations

import os
import sys
import subprocess
from pathlib import Path

import typer

app = typer.Typer(help="Graphical user interface tools")


def _check_gui_available() -> None:
    """Ensure PySide6 and the GUI package are importable, else exit cleanly."""
    try:
        import PySide6  # noqa: F401
    except ImportError:
        typer.echo(
            "Error: PySide6 not installed. The GUI miner requires Qt for Python.",
            err=True,
        )
        typer.echo("\nInstall with: pip install PySide6", err=True)
        raise typer.Exit(code=1)

    try:
        import animica_miner_gui  # noqa: F401
    except ImportError:
        typer.echo("Error: animica-miner-gui package not installed.", err=True)
        typer.echo("\nInstall from apps/miner-gui/:", err=True)
        typer.echo("  cd apps/miner-gui && pip install -e .", err=True)
        raise typer.Exit(code=1)


def _launch(unified: bool) -> None:
    """Launch the GUI, optionally straight into unified (mine + AI) mode."""
    _check_gui_available()

    if unified:
        typer.echo("Launching Animica GUI Miner (unified: mine + AI)...")
        # main() reads --unified from sys.argv to pick the launch mode.
        if "--unified" not in sys.argv:
            sys.argv.append("--unified")
    else:
        typer.echo("Launching Animica GUI Miner...")

    try:
        from animica_miner_gui.main import main as gui_main
        sys.exit(gui_main())
    except Exception as e:  # noqa: BLE001
        typer.echo(f"Error launching GUI miner: {e}", err=True)
        raise typer.Exit(code=1)


@app.command("miner")
def launch_gui_miner(
    unified: bool = typer.Option(
        False,
        "--unified",
        "--full",
        help="Launch directly into unified 'mine + AI' mode (PoW + AI compute), "
             "instead of proof-of-work only.",
    ),
) -> None:
    """Launch the Animica GUI Miner application.

    This is a production-quality Qt desktop application for mining with:
    - First-run wizard for easy setup
    - Real-time dashboard with mining stats
    - Device configuration (CPU/GPU/ASIC)
    - Pool and solo mining modes
    - Unified "mine + AI" mode (PoW + AICF inference + ENA useful-work, plus
      GPU training/serving where available), all bound to your ANM address
    - Real-time logs and graphs
    - Dark theme and system tray support

    The GUI miner can also be launched directly with:
      animica-miner-gui            (proof-of-work by default)
      animica-miner-gui --unified  (mine + AI)
    """
    _launch(unified=unified)


@app.command("full")
def launch_gui_full() -> None:
    """Launch the GUI directly into unified 'mine + AI' mode.

    Equivalent to `animica gui miner --unified`: the GUI drives the whole
    unified flow (SHA3 proof-of-work + AICF inference, ENA useful-work, and —
    on capable GPUs — training / serving / Bittensor), all paid to one ANM
    payout address.
    """
    _launch(unified=True)
