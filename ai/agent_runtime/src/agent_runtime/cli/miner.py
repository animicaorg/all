"""`animica miner pool` and `animica miner aicf-worker` subcommands.

Registered into the existing animica miner CLI via a typer sub-app the
animica python package adds at startup. Coexists with all existing
mining commands (no regression).
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from agent_runtime.aicf_worker import (
    AICFWorker, is_disabled, pull_bundle, resolve_tiers,
)
from agent_runtime.config import load_config
from agent_runtime.descriptors import describe_distributed
from agent_runtime.errors import AgentRuntimeError
from agent_runtime.hardware import attach_eligible_tiers, detect_hardware


pool_app = typer.Typer(
    help="Connect this machine to the Animica mining pool.",
    no_args_is_help=True,
)
aicf_worker_app = typer.Typer(
    help="Run an AICF compute worker alongside (or instead of) PoW mining.",
    no_args_is_help=True,
)


def _state_dir() -> Path:
    p = Path(os.environ.get("ANIMICA_DATA_DIR", "~/.animica")).expanduser() \
        / "miner_pool"
    p.mkdir(parents=True, exist_ok=True)
    return p


# --------------------------------------------------------------------------- #
# `animica miner pool` group                                                  #
# --------------------------------------------------------------------------- #

@pool_app.command("connect")
def pool_connect(
    address: str = typer.Argument(...,
                                   help="Pool URL, e.g. stratum+tcp://pool.animica.org:5333"),
    wallet: str = typer.Option(..., "--wallet",
                                help="Payout address (anim1...)"),
    name: Optional[str] = typer.Option(None, "--name",
                                        help="Worker name registered with pool"),
    threads: int = typer.Option(os.cpu_count() or 1, "--threads"),
    no_aicf: bool = typer.Option(False, "--no-aicf",
                                  help="Disable AICF worker auto-registration."),
) -> None:
    """Persist the pool connection details locally. Existing mining
    commands (`animica miner cpu --pool ...`) honor these defaults."""
    state = _state_dir() / "pool.json"
    state.write_text(json.dumps({
        "schema": 1,
        "address": address,
        "wallet": wallet,
        "name": name or os.uname().nodename,
        "threads": threads,
        "no_aicf": no_aicf,
        "connected_at": int(time.time()),
    }, indent=2), encoding="utf-8")

    profile = detect_hardware()
    cfg = load_config()
    attach_eligible_tiers(profile, dict(cfg.model_catalog))
    console = Console()
    console.print(f"[green]✓[/green] pool connection recorded: {address}")
    t = Table(show_header=False, box=None)
    t.add_row("wallet:", wallet)
    t.add_row("name:", state.read_text(encoding="utf-8") and name or "")
    t.add_row("threads:", str(threads))
    t.add_row("accelerator:", profile.accelerator_preferred)
    t.add_row("eligible tiers:", ", ".join(profile.eligible_tiers) or "<none>")
    t.add_row("aicf worker:",
              "[red]disabled[/red]" if no_aicf else "[green]enabled[/green]")
    console.print(t)
    if not no_aicf and not is_disabled():
        console.print(
            "\n[dim]start the AICF worker with:\n"
            "  animica miner aicf-worker start --address "
            f"{wallet}[/dim]",
        )


@pool_app.command("status")
def pool_status() -> None:
    """Show the recorded pool connection + hardware capabilities."""
    state = _state_dir() / "pool.json"
    console = Console()
    if not state.is_file():
        console.print("[yellow]no pool connection recorded.[/yellow] "
                       "run `animica miner pool connect ...` first.")
        raise typer.Exit(code=1)
    data = json.loads(state.read_text(encoding="utf-8"))
    profile = detect_hardware()
    cfg = load_config()
    attach_eligible_tiers(profile, dict(cfg.model_catalog))
    console.print(f"pool:         {data['address']}")
    console.print(f"wallet:       {data['wallet']}")
    console.print(f"worker name:  {data.get('name', '')}")
    console.print(f"threads:      {data.get('threads', 1)}")
    console.print(f"connected at: {data.get('connected_at', 0)}")
    console.print()
    console.print(f"accelerator:  {profile.accelerator_preferred}")
    console.print(f"ram_gb:       {profile.ram_gb:.1f}")
    gpus = profile.gpus or []
    console.print(f"gpus:         {len(gpus)}")
    for g in gpus:
        console.print(f"  - {g.name} ({g.vram_gb:.1f} GB, {g.backend})")
    console.print(f"eligible tiers: "
                   f"{', '.join(profile.eligible_tiers) or '<none>'}")


@pool_app.command("disconnect")
def pool_disconnect() -> None:
    state = _state_dir() / "pool.json"
    if state.is_file():
        state.unlink()
        typer.echo("disconnected.")
    else:
        typer.echo("nothing to disconnect.")


# --------------------------------------------------------------------------- #
# `animica miner aicf-worker` group                                           #
# --------------------------------------------------------------------------- #

@aicf_worker_app.command("start")
def worker_start(
    address: str = typer.Option(..., "--address",
                                 help="Payout address (anim1...)"),
    tiers: Optional[str] = typer.Option(
        None, "--tiers",
        help="Comma-separated tier ids to advertise (default: auto-detect)."),
    no_aicf: bool = typer.Option(False, "--no-aicf",
                                  help="Refuse to start (parity with pool flag)."),
) -> None:
    if no_aicf or is_disabled():
        typer.echo("AICF worker disabled.")
        raise typer.Exit(code=0)
    cfg = load_config()
    tiers_list = [t.strip() for t in tiers.split(",")] if tiers else None
    try:
        worker = AICFWorker(cfg=cfg, address=address,
                             tiers_override=tiers_list)
    except AgentRuntimeError as exc:
        typer.echo(exc.render(), err=True)
        raise typer.Exit(code=2)

    def _stop(*_: object) -> None:
        worker.stop()
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    typer.echo(f"AICF worker started: address={address} "
                f"tiers={worker.tiers} accel={worker.profile.accelerator_preferred}")
    try:
        worker.run()
    finally:
        worker.close()
    typer.echo("AICF worker stopped.")


@aicf_worker_app.command("status")
def worker_status() -> None:
    state_path = Path(os.environ.get(
        "ANIMICA_DATA_DIR", "~/.animica")).expanduser() / \
        "aicf_worker" / "state.json"
    if not state_path.is_file():
        typer.echo("no AICF worker state recorded.")
        raise typer.Exit(code=1)
    typer.echo(state_path.read_text(encoding="utf-8"))


@aicf_worker_app.command("hardware")
def worker_hardware() -> None:
    """Print detected hardware + eligible model tiers."""
    profile = detect_hardware()
    cfg = load_config()
    attach_eligible_tiers(profile, dict(cfg.model_catalog))
    typer.echo(json.dumps(profile.to_dict(), indent=2))


@aicf_worker_app.command("pull")
def worker_pull(
    cid: str = typer.Argument(..., help="IPFS CID of the bundle."),
    tier: str = typer.Option(..., "--tier",
                              help="Tier id (tiny|small|flagship|large)."),
    sha256: Optional[str] = typer.Option(None, "--sha256",
                                          help="Expected bundle sha256."),
) -> None:
    """Download a flagship bundle by CID and stage it for serving."""
    # Stage under the CATALOG id so the serving worker (which canonicalizes) finds
    # it — accept a stratum name ('standard') too. Identity for catalog names.
    try:
        from agent_runtime.hardware import canonical_tier
        tier = canonical_tier(tier)
    except Exception:  # noqa: BLE001
        pass
    try:
        path = pull_bundle(cid, tier=tier, verify_sha256=sha256)
    except AgentRuntimeError as exc:
        typer.echo(exc.render(), err=True)
        raise typer.Exit(code=2)
    typer.echo(f"installed at {path}")


@aicf_worker_app.command("descriptors")
def worker_descriptors() -> None:
    """Print the agent_runtime provider descriptors (useful for ops)."""
    from agent_runtime.descriptors import describe_all
    typer.echo(json.dumps(describe_all(), indent=2))


# --------------------------------------------------------------------------- #
# Top-level Typer app glue                                                    #
# --------------------------------------------------------------------------- #

def attach_to(miner_app) -> None:
    """Hook called by python/animica/cli/miner.py to mount the new groups.

    Names chosen to NOT collide with existing miner commands:
      pool-client    (vs existing server-side `pool` and `run-pool` commands)
      aicf-worker    (new, no collision)
    """
    miner_app.add_typer(pool_app, name="pool-client")
    miner_app.add_typer(aicf_worker_app, name="aicf-worker")
