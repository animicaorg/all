"""``animica up`` — one command to run everything (mine + AI), one pool, one
global model, all bound to a single ANM payout address. See animica.unified."""

from __future__ import annotations

import json as _json
from typing import Optional

import typer
from rich.console import Console

app = typer.Typer(
    help="Run everything with one command: SHA3 mining + ENA useful-work + "
         "training + serving (+ Bittensor for qualified GPUs).",
    invoke_without_command=True)
console = Console()


@app.callback(invoke_without_command=True)
def up(ctx: typer.Context,
       address: Optional[str] = typer.Option(None, "--address",
           help="ANM payout address (default: your wallet; auto-created if none)"),
       pool_host: str = typer.Option("pool.animica.org", "--pool-host"),
       pool_port: int = typer.Option(3333, "--pool-port"),
       pool_id: Optional[str] = typer.Option(None, "--pool-id",
           help="training pool / global model to train + serve"),
       worker_id: Optional[str] = typer.Option(None, "--worker-id"),
       with_node: bool = typer.Option(False, "--with-node",
           help="also run a local full node"),
       threads: int = typer.Option(0, "--threads", help="miner threads (0 = auto)"),
       bittensor_token: Optional[str] = typer.Option(None, "--bittensor-token",
           help="SN51 enrollment token from pool.animica.org/workers (qualified GPUs)",
           envvar="ANIMICA_WORKER_TOKEN"),
       plan: bool = typer.Option(False, "--plan",
           help="show the launch plan for this machine and exit"),
       json_output: bool = typer.Option(False, "--json")) -> None:
    if ctx.invoked_subcommand is not None:
        return
    from animica.unified import (Supervisor, UnifiedConfig, _resolve_best_pool,
                                 build_plan, detect_capabilities, plan_summary,
                                 resolve_address)
    # zero-config: resolve (or auto-create) the payout wallet. For --plan we never
    # create anything; we just show what a real run would use.
    try:
        addr, addr_source = resolve_address(address, create=not plan)
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    if not addr:
        addr, addr_source = "<auto: a wallet will be created on run>", "pending"
    if addr_source == "created":
        console.print(f"[green]created a new wallet[/green] → {addr}")
    caps = detect_capabilities()
    # ENA training is on by default on a GPU box: when no pool was named, pick
    # the highest-paying open training pool so `animica up` trains + serves the
    # one global model out of the box. Best-effort — if the pool API can't be
    # reached, training stays off with a clear reason in the plan.
    if pool_id is None and caps.gpu:
        pool_id = _resolve_best_pool(pool_host)
        if pool_id:
            console.print(f"[green]auto-selected training pool[/green] → "
                          f"{pool_id} [dim](highest-paying)[/dim]")
    cfg = UnifiedConfig(address=addr, pool_host=pool_host, pool_port=pool_port,
                        pool_id=pool_id, worker_id=worker_id or "",
                        run_node=with_node, threads=threads,
                        bittensor_token=bittensor_token)
    components = build_plan(caps, cfg)
    summary = plan_summary(caps, cfg, components)

    if plan:
        if json_output:
            console.print_json(_json.dumps(summary))
        else:
            console.print(f"[bold cyan]animica up — plan[/bold cyan] "
                          f"(unified v{summary['version']})")
            console.print(f"address {addr} ({addr_source}) · pool {pool_host} · "
                          f"gpu={caps.gpu} ({caps.gpu_name or 'none'}, "
                          f"{caps.device_kind or 'cpu'}, {caps.vram_gb} GB) · "
                          f"qualified={caps.qualified_bittensor}")
            for c in components:
                mark = "[green]▶[/green]" if (c.enabled and c.available) else (
                    "[yellow]…[/yellow]" if c.enabled else "[dim]·[/dim]")
                console.print(f"  {mark} [bold]{c.name:<12}[/bold] {c.reason}")
        raise typer.Exit(0)

    console.print(f"[bold green]animica up[/bold green] v{summary['version']} — "
                  f"running: {', '.join(summary['will_run']) or 'nothing'}")
    if summary["enabled_but_pending"]:
        console.print(f"[yellow]enabled but not yet runnable: "
                      f"{', '.join(summary['enabled_but_pending'])}[/yellow]")
    Supervisor(components).run()
