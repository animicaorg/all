"""Operator-friendly Stratum pool lifecycle commands."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

import httpx
import typer
from rich.console import Console

from animica.stratum_pool.config import load_config_from_env

from .service_runtime import (is_running, read_metadata, read_pid,
                              service_state, start_daemon, stop_daemon)

app = typer.Typer(help="Stratum pool lifecycle commands.")
console = Console()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _service_state():
    return service_state("stratum")


def _pythonpath() -> str:
    repo_root = _repo_root()
    entries = [str(repo_root / "python"), str(repo_root)]
    current = os.environ.get("PYTHONPATH")
    if current:
        entries.append(current)
    return os.pathsep.join(entries)


def _api_url(metadata: dict[str, object]) -> str:
    api_url = metadata.get("api_url")
    if isinstance(api_url, str) and api_url:
        return api_url
    cfg = load_config_from_env()
    return f"http://{cfg.api_host}:{cfg.api_port}"


@app.command("up")
def up(
    profile: Optional[str] = typer.Option(None, "--profile", help="Pool profile (hashshare|asic_sha256)"),
    host: Optional[str] = typer.Option(None, "--host", help="Stratum bind host"),
    port: Optional[int] = typer.Option(None, "--port", help="Stratum bind port"),
    api_host: Optional[str] = typer.Option(None, "--api-host", help="Pool API host"),
    api_port: Optional[int] = typer.Option(None, "--api-port", help="Pool API port"),
    rpc_url: Optional[str] = typer.Option(None, "--rpc-url", help="Animica RPC URL"),
    pool_address: Optional[str] = typer.Option(None, "--pool-address", help="Pool payout address"),
    min_difficulty: Optional[float] = typer.Option(None, "--min-difficulty", help="Minimum share difficulty"),
    max_difficulty: Optional[float] = typer.Option(None, "--max-difficulty", help="Maximum share difficulty"),
    poll_interval: Optional[float] = typer.Option(None, "--poll-interval", help="Work polling interval"),
    log_level: Optional[str] = typer.Option(None, "--log-level", help="Pool log level"),
    daemon: bool = typer.Option(False, "--daemon", "-d", help="Run in background"),
) -> None:
    """Start the Stratum pool."""
    state = _service_state()
    pid = read_pid(state)
    if is_running(pid):
        console.print(f"[yellow]Stratum pool already running (pid {pid})[/yellow]")
        raise typer.Exit(0)

    env = os.environ.copy()
    env["PYTHONPATH"] = _pythonpath()

    cmd = [sys.executable, "-m", "animica.stratum_pool"]
    if profile:
        cmd.extend(["--profile", profile])
        env["ANIMICA_POOL_PROFILE"] = profile
    if host:
        cmd.extend(["--host", host])
    if port is not None:
        cmd.extend(["--port", str(port)])
    if api_host:
        cmd.extend(["--api-host", api_host])
    if api_port is not None:
        cmd.extend(["--api-port", str(api_port)])
    if rpc_url:
        cmd.extend(["--rpc-url", rpc_url])
    if pool_address:
        cmd.extend(["--pool-address", pool_address])
    if min_difficulty is not None:
        cmd.extend(["--min-difficulty", str(min_difficulty)])
    if max_difficulty is not None:
        cmd.extend(["--max-difficulty", str(max_difficulty)])
    if poll_interval is not None:
        cmd.extend(["--poll-interval", str(poll_interval)])
    if log_level:
        cmd.extend(["--log-level", log_level])

    metadata = {
        "cmd": cmd,
        "profile": profile or os.environ.get("ANIMICA_POOL_PROFILE", "hashshare"),
        "rpc_url": rpc_url or os.environ.get("ANIMICA_RPC_URL"),
        "endpoint": f"stratum+tcp://{host or os.environ.get('ANIMICA_STRATUM_HOST', '0.0.0.0')}:{port or os.environ.get('ANIMICA_STRATUM_PORT', '3333')}",
        "api_url": f"http://{api_host or os.environ.get('ANIMICA_STRATUM_API_HOST', host or '127.0.0.1')}:{api_port or os.environ.get('ANIMICA_STRATUM_API_PORT', '8550')}",
    }

    if daemon:
        pid = start_daemon(
            state,
            cmd=cmd,
            env=env,
            cwd=_repo_root(),
            metadata=metadata,
        )
        console.print(f"[green]✓ Stratum pool started[/green] pid={pid}")
        console.print(f"Stratum: {metadata['endpoint']}")
        console.print(f"API: {metadata['api_url']}")
        console.print(f"Log: {state.log_file}")
        return

    console.print("[yellow]Starting pool in foreground (Ctrl+C to stop)...[/yellow]")
    subprocess.run(cmd, cwd=_repo_root(), env=env, check=False)


@app.command("down")
def down() -> None:
    """Stop the Stratum pool."""
    state = _service_state()
    pid = read_pid(state)
    if not is_running(pid):
        console.print("[yellow]Stratum pool is not running[/yellow]")
        raise typer.Exit(0)

    stop_daemon(state)
    console.print(f"[green]✓ Stopped Stratum pool (pid {pid})[/green]")


@app.command("status")
def status() -> None:
    """Show Stratum pool status."""
    state = _service_state()
    pid = read_pid(state)
    metadata = read_metadata(state)
    api_url = _api_url(metadata)

    console.print("[bold]Stratum Status[/bold]\n")
    if is_running(pid):
        console.print(f"State: [green]running[/green] (pid {pid})")
        console.print(f"Stratum: {metadata.get('endpoint', 'unknown')}")
        console.print(f"API: {api_url}")
        console.print(f"Log: {state.log_file}")
        try:
            with httpx.Client(timeout=3.0) as client:
                health = client.get(f"{api_url}/healthz")
                health.raise_for_status()
                console.print(f"API health: [green]{health.json().get('status', 'ok')}[/green]")
                summary = client.get(f"{api_url}/summary")
                summary.raise_for_status()
                summary_data = summary.json()
                workers = summary_data.get("workers") or summary_data.get("active_workers")
                console.print(f"Pool summary: workers={workers} uptime={summary_data.get('uptime_seconds', summary_data.get('uptime'))}")
        except Exception as exc:  # noqa: BLE001
            console.print(f"API health: [yellow]unreachable[/yellow] ({exc})")
    else:
        console.print("State: [yellow]stopped[/yellow]")
        console.print("[dim]Start with: animica stratum up --daemon[/dim]")


@app.command("config")
def config() -> None:
    """Show the resolved Stratum pool configuration."""
    cfg = load_config_from_env()
    console.print("[bold]Stratum Config[/bold]\n")
    console.print(f"RPC URL: {cfg.rpc_url}")
    console.print(f"Profile: {cfg.profile}")
    console.print(f"Stratum bind: {cfg.host}:{cfg.port}")
    console.print(f"API bind: {cfg.api_host}:{cfg.api_port}")
    console.print(f"Pool address: {cfg.pool_address or '(unset)'}")
    console.print(f"Difficulty: {cfg.min_difficulty} -> {cfg.max_difficulty}")
