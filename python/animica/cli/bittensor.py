"""
Bittensor GPU pool CLI commands
===============================

Earn TAO with your GPU rig by backing Animica Pool's Bittensor SN51 (Celium)
miner. Your rig runs an SN51 executor under the pool's miner hotkey; the pool
splits on-chain earnings with you (default 70% to rig owners, paid out in
ANM/XMR/SOL/BTC/USDT via pool.animica.org payouts).

Commands:
  - animica bittensor overview                  # public pool status (no token)
  - animica bittensor status  --token TOKEN     # this rig: eligibility + enrollment
  - animica bittensor enroll  --token TOKEN     # join the GPU pool
  - animica bittensor up      --token TOKEN     # fetch + run the SN51 executor installer

The token is the worker token from https://pool.animica.org/workers
(also read from ANIMICA_WORKER_TOKEN). Requires the worker agent to be
installed and heartbeating — eligibility needs uptime history (default:
97% over 7 days) because Bittensor slashes collateral for flaky rigs.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
from typing import Any, Optional

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(
    name="bittensor",
    help="Earn TAO: back Animica Pool's Bittensor SN51 GPU miner with your rig",
    no_args_is_help=True,
)

console = Console()

DEFAULT_API = "https://pool.animica.org"
TOKEN_ENV = "ANIMICA_WORKER_TOKEN"


def _api_base(api: Optional[str]) -> str:
    return (api or os.environ.get("ANIMICA_API_BASE") or DEFAULT_API).rstrip("/")


def _token(token: Optional[str]) -> str:
    tok = token or os.environ.get(TOKEN_ENV) or ""
    if not tok:
        console.print(f"[red]Missing worker token[/red] — pass --token or set {TOKEN_ENV}.")
        console.print("Create one at https://pool.animica.org/workers (Workers → New worker).")
        raise typer.Exit(1)
    return tok


def _request(method: str, url: str, token: Optional[str] = None, body: Any = None) -> Any:
    import httpx

    headers = {"accept": "application/json"}
    if token:
        headers["authorization"] = f"Bearer {token}"
    try:
        resp = httpx.request(method, url, headers=headers, json=body, timeout=30.0)
    except httpx.HTTPError as exc:  # network-level failure
        console.print(f"[red]Request failed:[/red] {exc}")
        raise typer.Exit(1)
    try:
        data = resp.json()
    except json.JSONDecodeError:
        data = {"message": resp.text[:200]}
    if resp.status_code >= 400:
        msg = data.get("message") if isinstance(data, dict) else None
        console.print(f"[red]HTTP {resp.status_code}[/red] {msg or data}")
        # Surface the eligibility breakdown on enroll rejections.
        if isinstance(msg, dict) and "eligibility" in msg:
            _print_eligibility(msg["eligibility"])
        elif isinstance(data, dict) and isinstance(data.get("message"), dict) and "eligibility" in data["message"]:
            _print_eligibility(data["message"]["eligibility"])
        raise typer.Exit(1)
    return data


def _print_eligibility(elig: dict) -> None:
    checks = elig.get("checks", {})
    table = Table(title="Eligibility", show_header=True, header_style="bold")
    table.add_column("Check")
    table.add_column("OK")
    labels = {
        "hasGpu": f"NVIDIA CUDA GPU ({elig.get('gpuModel') or 'none detected'})",
        "vramOk": f"VRAM ≥ {elig.get('thresholds', {}).get('minVramGb', 16)} GB (have {elig.get('vramGb') or 0})",
        "historyOk": f"history ≥ {elig.get('thresholds', {}).get('minHistoryDays', 7)} days (have {elig.get('historyDays', 0)})",
        "uptimeOk": f"uptime ≥ {elig.get('thresholds', {}).get('minUptimePct', 97)}% (have {elig.get('uptimePct', 0)}%)",
        "notBanned": "worker in good standing",
    }
    for key, label in labels.items():
        ok = checks.get(key)
        table.add_row(label, "[green]✓[/green]" if ok else "[red]✗[/red]")
    console.print(table)
    # Server explains detected-but-unusable GPUs (e.g. Apple Silicon: Metal
    # can't serve SN51's CUDA executors, but the rig still earns elsewhere).
    if elig.get("gpuNote"):
        console.print(f"[yellow]{elig['gpuNote']}[/yellow]")


@app.command()
def overview(
    api: Optional[str] = typer.Option(None, "--api", help=f"Pool API base (default {DEFAULT_API})"),
) -> None:
    """Public pool status: demand side, GPU pool, treasury progress, earnings."""
    data = _request("GET", f"{_api_base(api)}/api/bittensor/overview")
    supply = data.get("supplySide", {})
    treasury = data.get("treasury", {})
    earnings = data.get("earnings", {})
    console.print("[bold]Animica × Bittensor[/bold]")
    console.print(f"  AI inference via Bittensor: {'[green]LIVE[/green]' if data.get('demandSide', {}).get('enabled') else '[yellow]off[/yellow]'}")
    live = supply.get("miningEnabled") and supply.get("hotkeySet")
    console.print(f"  GPU pool (SN{supply.get('netuid')}): {'[green]LIVE[/green]' if live else '[yellow]warming up[/yellow]'} (miner {supply.get('minerStatus')})")
    console.print(f"  Registration treasury: ${treasury.get('accruedUsd', 0):.2f} / ${treasury.get('targetUsd', 0):.0f} ({treasury.get('pct', 0):.1f}%)")
    console.print(f"  Pool TAO earnings: ${earnings.get('totalUsd', 0):.2f} (rig owners paid ${earnings.get('ownerPaidUsd', 0):.2f})")
    console.print(f"  Rig owner share: {supply.get('ownerSharePercent', 70)}% after {supply.get('holdbackDays', 7)}-day holdback")


@app.command()
def status(
    token: Optional[str] = typer.Option(None, "--token", help=f"Worker token (or {TOKEN_ENV})"),
    api: Optional[str] = typer.Option(None, "--api", help=f"Pool API base (default {DEFAULT_API})"),
) -> None:
    """This rig: enrollment state + eligibility for the SN51 GPU pool."""
    data = _request("GET", f"{_api_base(api)}/api/bittensor/executor/me", token=_token(token))
    if data.get("enrolled"):
        ex = data.get("executor") or {}
        console.print(f"[green]Enrolled[/green] — executor {ex.get('status')} on SN{ex.get('netuid')} "
                      f"({ex.get('gpuType')}{' ×' + str(ex['gpuCount']) if ex.get('gpuCount', 1) > 1 else ''})")
        if not data.get("miningLive"):
            console.print("[yellow]Pool miner not live yet[/yellow] — you're queued; earnings start the moment it registers on-chain.")
        elif not ex.get("containerOk"):
            console.print("[yellow]Executor container not running[/yellow] — run: animica bittensor up")
    else:
        console.print("Not enrolled.")
    elig = data.get("eligibility") or {}
    _print_eligibility(elig)
    if not data.get("enrolled") and elig.get("eligible"):
        console.print("Eligible — run: [bold]animica bittensor enroll[/bold]")


@app.command()
def enroll(
    token: Optional[str] = typer.Option(None, "--token", help=f"Worker token (or {TOKEN_ENV})"),
    api: Optional[str] = typer.Option(None, "--api", help=f"Pool API base (default {DEFAULT_API})"),
    gpu_type: Optional[str] = typer.Option(None, "--gpu-type", help="Override detected GPU type"),
    gpu_count: int = typer.Option(1, "--gpu-count", help="GPUs on this rig"),
) -> None:
    """Enroll this rig in the Animica SN51 GPU pool (eligibility-gated)."""
    body: dict[str, Any] = {"gpuCount": gpu_count}
    if gpu_type:
        body["gpuType"] = gpu_type
    data = _request("POST", f"{_api_base(api)}/api/bittensor/executor/enroll", token=_token(token), body=body)
    ex = data.get("executor", {})
    console.print(f"[green]Enrolled[/green] — executor {ex.get('id')} ({ex.get('gpuType')})")
    if data.get("miningLive"):
        console.print("Next: [bold]animica bittensor up[/bold] to install the SN51 executor.")
    else:
        console.print("[yellow]Pool miner not registered on-chain yet[/yellow] — you're queued at the front. "
                      "Keep the worker agent running (uptime history matters); 'up' unlocks at launch.")


@app.command()
def up(
    token: Optional[str] = typer.Option(None, "--token", help=f"Worker token (or {TOKEN_ENV})"),
    api: Optional[str] = typer.Option(None, "--api", help=f"Pool API base (default {DEFAULT_API})"),
    run: bool = typer.Option(False, "--run", help="Execute the installer now (needs root, Ubuntu 22.04+, NVIDIA driver)"),
) -> None:
    """Fetch the SN51 executor provisioning script for this rig (and optionally run it)."""
    data = _request("GET", f"{_api_base(api)}/api/bittensor/executor/provision", token=_token(token))
    script = data.get("script", "")
    if not script:
        console.print("[red]No script returned[/red]")
        raise typer.Exit(1)
    path = os.path.join(tempfile.gettempdir(), "animica-sn51-executor.sh")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(script)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
    console.print(f"Provisioning script saved: [bold]{path}[/bold]")
    if run:
        console.print("Running installer (this installs docker sysbox runtime + the SN51 executor container)…")
        raise typer.Exit(subprocess.call(["bash", path]))
    console.print("Review it, then run:  [bold]sudo bash " + path + "[/bold]")
    console.print("Afterwards set BITTENSOR_EXECUTOR=1 in the worker agent env so the pool monitors the container.")
