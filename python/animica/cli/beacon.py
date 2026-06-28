"""
animica beacon — run the public Verifiable Quantum Randomness Beacon service.

Serves a drand-style HTTP API plus the /beacon page (with an in-browser verifier)
backed by the chain's quantum randomness lane. Pseudo-quantum until a real
attested QRNG connects, then it auto-flips.
"""

from __future__ import annotations

import typer
from rich.console import Console

console = Console()

app = typer.Typer(
    name="beacon",
    help="Public verifiable quantum randomness beacon (HTTP API + /beacon page)",
    no_args_is_help=True,
)


@app.command("serve")
def serve(
    host: str = typer.Option("0.0.0.0", "--host"),
    port: int = typer.Option(8780, "--port"),
    interval: float = typer.Option(30.0, "--interval", help="seconds between beacon rounds"),
):
    """Run the public beacon API + /beacon page."""
    console.print(f"[cyan]Animica Quantum Randomness Beacon[/] → http://{host}:{port}/beacon "
                  f"(round every {interval:g}s)")
    from randomness.beacon_api.server import run
    run(host=host, port=port, interval_s=interval)
