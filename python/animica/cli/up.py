"""``animica up`` — one command to run everything (mine + AI), one pool, one
global model, all bound to a single ANM payout address. See animica.unified.

5.2.0 adds component selection (``--profile`` / ``--only`` / ``--without``), a
``--serve-port`` flag, and a richer ``--plan`` view — all additive, so every
existing invocation behaves exactly as before.
"""

from __future__ import annotations

import json as _json
from typing import List, Optional

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(
    help="Run everything with one command: SHA3 mining + ENA useful-work + "
         "training + serving (+ Bittensor for qualified GPUs).",
    invoke_without_command=True)
console = Console()

# Friendly aliases → canonical component names produced by unified.build_plan.
_CANON = {"node", "miner", "useful-work", "studio", "trainer", "server", "bittensor"}
_ALIASES = {
    "node": "node",
    "miner": "miner", "mine": "miner", "mining": "miner", "pow": "miner",
    "useful-work": "useful-work", "ai": "useful-work", "uw": "useful-work",
    "studio": "studio",
    "trainer": "trainer", "train": "trainer",
    "server": "server", "serve": "server",
    "bittensor": "bittensor", "bt": "bittensor",
}
# Named presets — the canonical components each profile keeps. "all" = no filter.
_PROFILES = {
    "all": None,
    "miner": {"node", "miner"},
    "ai": {"node", "useful-work", "studio", "trainer", "server"},
    "provider": {"node", "useful-work", "studio", "server"},
}


def _resolve_names(values: List[str]) -> set[str]:
    """Map user-supplied component names/aliases to canonical names; raise on unknown."""
    out: set[str] = set()
    for v in values:
        key = (v or "").strip().lower()
        if not key:
            continue
        canon = _ALIASES.get(key)
        if canon is None:
            raise typer.BadParameter(
                f"unknown component {v!r}. Valid: {', '.join(sorted(_CANON))} "
                f"(aliases: mine, ai, train, serve, bt)")
        out.add(canon)
    return out


def _apply_selection(components, profile: str, only: List[str], without: List[str]) -> list[str]:
    """Disable components excluded by --profile/--only/--without. Returns notes."""
    notes: list[str] = []
    profile = (profile or "all").lower()
    if profile not in _PROFILES:
        raise typer.BadParameter(
            f"unknown profile {profile!r}. Valid: {', '.join(sorted(_PROFILES))}")
    allowed_profile = _PROFILES[profile]
    only_set = _resolve_names(only) if only else None
    without_set = _resolve_names(without) if without else set()

    for c in components:
        if allowed_profile is not None and c.name not in allowed_profile:
            if c.enabled:
                c.enabled = False
                c.reason = f"disabled by --profile {profile}"
        if only_set is not None and c.name not in only_set:
            if c.enabled:
                c.enabled = False
                c.reason = "not selected by --only"
        if c.name in without_set:
            if c.enabled:
                c.enabled = False
            c.reason = "disabled by --without"
    if profile != "all":
        notes.append(f"profile={profile}")
    if only_set is not None:
        notes.append(f"only={','.join(sorted(only_set))}")
    if without_set:
        notes.append(f"without={','.join(sorted(without_set))}")
    return notes


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
       serve_port: int = typer.Option(8799, "--serve-port",
           help="port for the GPU model server (ena pool serve)"),
       profile: str = typer.Option("all", "--profile",
           help="component preset: all | miner | ai | provider"),
       only: Optional[List[str]] = typer.Option(None, "--only",
           help="run ONLY these components (repeatable; e.g. --only miner --only studio)"),
       without: Optional[List[str]] = typer.Option(None, "--without",
           help="disable these components (repeatable; e.g. --without bittensor)"),
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
                        run_node=with_node, threads=threads, serve_port=serve_port,
                        bittensor_token=bittensor_token)
    components = build_plan(caps, cfg)
    # Apply component selection (additive — default profile=all keeps prior behavior).
    sel_notes = _apply_selection(components, profile, only or [], without or [])
    summary = plan_summary(caps, cfg, components)
    if sel_notes:
        summary["selection"] = sel_notes

    if plan:
        if json_output:
            console.print_json(_json.dumps(summary))
            raise typer.Exit(0)
        console.print(f"[bold cyan]animica up — plan[/bold cyan] "
                      f"(unified v{summary['version']})")
        console.print(f"address [bold]{addr}[/] ({addr_source}) · pool {pool_host}:{pool_port}"
                      + (f" · model {pool_id}" if pool_id else ""))
        console.print(f"hardware: gpu={caps.gpu} ({caps.gpu_name or 'none'}, "
                      f"{caps.device_kind or 'cpu'}, {caps.vram_gb} GB) · "
                      f"cpu={caps.cpu_count} cores · bittensor-qualified={caps.qualified_bittensor}")
        if sel_notes:
            console.print(f"[dim]selection: {' '.join(sel_notes)}[/dim]")
        table = Table(show_lines=False)
        table.add_column("", justify="center", width=3)
        table.add_column("Component", style="bold")
        table.add_column("Status")
        table.add_column("Why")
        for c in components:
            if c.enabled and c.available:
                mark, status = "[green]▶[/]", "[green]run[/]"
            elif c.enabled:
                mark, status = "[yellow]…[/]", "[yellow]pending[/]"
            else:
                mark, status = "[dim]·[/]", "[dim]off[/]"
            table.add_row(mark, c.name, status, c.reason)
        console.print(table)
        will = summary["will_run"]
        console.print(f"\n[bold]will run:[/] {', '.join(will) or '[red]nothing[/]'}")
        if summary["enabled_but_pending"]:
            console.print(f"[yellow]pending (enabled, not yet runnable):[/] "
                          f"{', '.join(summary['enabled_but_pending'])}")
        console.print("[dim]tip: --profile miner|ai|provider, --only/--without <component>, "
                      "--plan to preview. Nothing launches until you run without --plan.[/dim]")
        raise typer.Exit(0)

    console.print(f"[bold green]animica up[/bold green] v{summary['version']} — "
                  f"running: {', '.join(summary['will_run']) or 'nothing'}")
    if summary["enabled_but_pending"]:
        console.print(f"[yellow]enabled but not yet runnable: "
                      f"{', '.join(summary['enabled_but_pending'])}[/yellow]")
    _ensure_media_models(caps, components, console)
    Supervisor(components).run()


def _ensure_media_models(caps, components, console) -> None:
    """Auto-install the generative-media model matched to this rig, in the BACKGROUND.

    Runs before the supervisor but never blocks it (a daemon thread downloads if missing). Disk-guarded
    and env-gated. Only fires when a miner/AICF-serving component is enabled and the media extra is
    present. Picks a model by VRAM tier; CPU rigs still get sd-turbo (slow but functional).
    """
    import os
    import shutil
    import threading

    if os.environ.get("ANIMICA_MEDIA_AUTOINSTALL", "1") == "0":
        return
    if os.environ.get("ANIMICA_AICF_PREFETCH", "1") == "0":
        return
    # Only relevant if this node serves work (miner / aicf-worker / provider-ish component enabled).
    enabled = {getattr(c, "name", "") for c in components if getattr(c, "enabled", True)}
    if not (enabled & {"miner", "aicf-worker", "server", "provider", "useful-work"}):
        return
    try:
        from animica.media.base import media_available
    except Exception:
        return
    avail, _why = media_available()
    if not avail:
        console.print("[dim]media: install 'animica[media]' to serve image/video jobs[/dim]")
        return

    # Pick a model + footprint by VRAM (CPU rigs -> sd-turbo).
    vram = float(getattr(caps, "vram_gb", 0) or 0)
    if vram >= 24:
        model_id, gb = "stabilityai/sdxl-turbo", 7.0  # keep it modest; FLUX is opt-in via `animica media install --tier elite`
    elif vram >= 10:
        model_id, gb = "stabilityai/sdxl-turbo", 7.0
    else:
        model_id, gb = "stabilityai/sd-turbo", 5.0
    model_id = os.environ.get("ANIMICA_IMAGE_MODEL", model_id)

    total, used, free = shutil.disk_usage(os.path.expanduser("~"))
    if free / 1e9 < gb * 1.3:
        console.print(f"[yellow]media: skipping model prefetch — only {round(free/1e9,1)}GB free "
                      f"(need ~{round(gb*1.3,1)}GB for {model_id})[/yellow]")
        return

    def _dl():
        try:
            from huggingface_hub import snapshot_download
            snapshot_download(model_id, allow_patterns=["*.json", "*.txt", "*.safetensors", "*.png"])
        except Exception:
            pass  # non-fatal; `animica media doctor` will report status

    console.print(f"[dim]media: ensuring image model {model_id} (~{gb}GB) in background…[/dim]")
    threading.Thread(target=_dl, name="animica-media-prefetch", daemon=True).start()
