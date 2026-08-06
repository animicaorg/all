"""``animica up`` — one command to run everything (mine + AI), one pool, one
global model, all bound to a single ANM payout address. See animica.unified.

5.2.0 adds component selection (``--profile`` / ``--only`` / ``--without``), a
``--serve-port`` flag, and a richer ``--plan`` view — all additive, so every
existing invocation behaves exactly as before.
"""

from __future__ import annotations

import json as _json
import os
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
    _report_subblock(components, console)
    _ensure_media_models(caps, components, console)
    _ensure_llm_model(caps, components, console)
    _ensure_media_miner(components, console)
    _ensure_inference_worker(components, console, addr)
    _ensure_animal(console)
    Supervisor(components).run()


def _report_subblock(components, console) -> None:
    """Tell the operator whether this miner will earn per share or only per block.

    The miner asks for a sub-block share target automatically (the stratum
    client advertises features.subblockShares), so there is nothing to enable —
    but on a pool that predates 9.1.0, or with ANIMICA_MINER_NO_SUBBLOCK set,
    payouts still only land when this machine finds a whole block. That is a
    big enough difference in earnings shape that it should not be silent.
    """
    try:
        enabled = {c.name for c in components if getattr(c, "enabled", False)}
        if "miner" not in enabled:
            return
        if os.environ.get("ANIMICA_MINER_NO_SUBBLOCK", "").strip():
            console.print(
                "[yellow]per-share payouts: OFF[/yellow] [dim](ANIMICA_MINER_NO_SUBBLOCK is set — "
                "you will only earn when this machine finds a whole block)[/dim]"
            )
            return
        console.print(
            "[green]per-share payouts: on[/green] [dim](requesting a sub-block share target; "
            "needs a pool on animica >=9.1.0 — otherwise you earn only on blocks you find)[/dim]"
        )
    except Exception:
        # Never let a status line stop mining.
        pass


def _ensure_inference_worker(components, console, address) -> None:
    """Direct this node's LLM/AICF inference at animica.dev's shared queue by default.

    animica.dev serves free chat by submitting on-chain AICF jobs to the canonical mainnet
    node (fronted by rpc.animica.org). Any inference-capable node — POOL or SOLO, with or
    without its own local node — should claim from THAT queue so its GPU/CPU serves the
    animica.dev network's demand instead of an empty local queue. We set ANIMICA_AICF_ENDPOINT,
    which the miner's ``--aicf`` worker (and any standalone worker) reads; os.environ propagates
    to the miner subprocess (Supervisor spawns it with ``{**os.environ, **c.env}``). Mirrors
    ``_ensure_media_miner``.

    Opt out:
      * ANIMICA_AICF_MINER=0 / ANIMICA_DISABLE_AICF_WORKER=1 / ANIMICA_AICF_DISABLE=1 — don't serve.
      * ANIMICA_AICF_LOCAL=1 — serve your OWN node's queue (127.0.0.1:8545) instead of animica.dev.
      * ANIMICA_AICF_ENDPOINT=… / AICF_URL=… — an explicit endpoint always wins.
    """
    import os

    if (os.environ.get("ANIMICA_AICF_MINER") == "0"
            or os.environ.get("ANIMICA_DISABLE_AICF_WORKER")
            or os.environ.get("ANIMICA_AICF_DISABLE")):
        # Make the opt-out actually reach the worker(s). The miner subprocess
        # starts its own ``--aicf`` worker, which is gated ONLY by
        # ANIMICA_DISABLE_AICF_WORKER=="1" (see agent_runtime.aicf_worker.
        # is_disabled); a bare ANIMICA_AICF_MINER=0 / ANIMICA_AICF_DISABLE=1
        # would otherwise be honored here but ignored by the subprocess, so
        # the node would keep serving AICF against the operator's wishes.
        # Canonicalize all three opt-outs into the flags every layer checks,
        # and propagate via os.environ (Supervisor spawns with {**os.environ}).
        os.environ["ANIMICA_DISABLE_AICF_WORKER"] = "1"
        os.environ["ANIMICA_AICF_DISABLE"] = "1"
        os.environ["ANIMICA_AICF_MINER"] = "0"
        return

    # Default the AICF claim endpoint to the animica.dev-fed canonical node, unless the operator
    # pinned an endpoint or asked to keep serving local. Explicit config always wins.
    default_gw = os.environ.get("ANIMICA_AICF_GATEWAY", "https://rpc.animica.org/rpc")
    explicit = os.environ.get("ANIMICA_AICF_ENDPOINT") or os.environ.get("AICF_URL")
    if not explicit and not os.environ.get("ANIMICA_AICF_LOCAL"):
        os.environ["ANIMICA_AICF_ENDPOINT"] = default_gw
    endpoint = (os.environ.get("ANIMICA_AICF_ENDPOINT")
                or os.environ.get("AICF_URL") or "127.0.0.1:8545 (local node)")

    enabled = {getattr(c, "name", "") for c in components if getattr(c, "enabled", True)}
    if "miner" in enabled:
        # The miner subprocess starts the AICF worker (--aicf) and inherits ANIMICA_AICF_ENDPOINT.
        console.print(f"[dim]inference: this node serves AICF chat (incl. Kimi K3 · kimi-k3) to {endpoint}[/dim]")
        return

    # No miner in the plan (e.g. --profile provider / ai): start a standalone AICF worker so the
    # node still serves inference to animica.dev. Best-effort — must never break `up`.
    if not address or address.startswith("<"):
        return
    try:
        from animica.cli.mining import _start_aicf_worker
        _stop, stats = _start_aicf_worker(address)
        tiers_list = stats.get("tiers") or []
        if stats.get("started") and tiers_list:
            tiers = ",".join(tiers_list)
            console.print(f"[dim]inference: serving AICF chat (incl. Kimi K3 · kimi-k3) to {endpoint} · tiers {tiers}[/dim]")
        elif stats.get("started"):
            # Worker constructed but qualified out every tier (no installed
            # flagship bundle to serve with). It won't advertise phantom
            # capacity; tell the operator how to actually serve.
            console.print("[dim]inference: worker idle (no installed model bundle) — "
                          "run 'animica miner aicf-worker pull' to serve chat[/dim]")
        else:
            console.print(f"[dim]inference: worker idle ({stats.get('reason')}) — "
                          f"run 'animica miner setup' to serve inference[/dim]")
    except Exception as exc:  # noqa: BLE001 — never let inference-enroll break the supervisor
        console.print(f"[dim]inference: worker not started ({exc})[/dim]")


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


def _ensure_llm_model(caps, components, console) -> None:
    """Auto-install this miner's AICF chat/coding model bundle, in the BACKGROUND.

    So ``animica up`` sets up a miner that actually serves network chat — including
    the "Kimi K3" (kimi-k3) flagship brand — out of the box, with zero manual
    ``animica miner setup``. Mirrors :func:`_ensure_media_models`: a daemon thread
    downloads if missing, disk-guarded, env-gated, and fully best-effort so it can
    never block or break ``up``.

    Env:
      * ANIMICA_AICF_AUTOINSTALL=0 — don't auto-install the LLM bundle.
      * ANIMICA_AICF_TIER=<tiny|small|flagship|large> — pin the tier (else picked by VRAM).
      * ANIMICA_AICF_MODEL=<hf repo id> — serve these exact weights instead of the tier
        default (e.g. set it to the Kimi K3 backend, moonshotai/Kimi-K2-Instruct, on a rig
        that can load it).
    """
    import os
    import shutil
    import threading

    if os.environ.get("ANIMICA_AICF_AUTOINSTALL", "1") == "0":
        return
    if os.environ.get("ANIMICA_AICF_PREFETCH", "1") == "0":
        return
    # Opt-outs that disable AICF serving entirely also skip the model install.
    if (os.environ.get("ANIMICA_AICF_MINER") == "0"
            or os.environ.get("ANIMICA_DISABLE_AICF_WORKER")
            or os.environ.get("ANIMICA_AICF_DISABLE")):
        return
    # Only relevant when this node serves work.
    enabled = {getattr(c, "name", "") for c in components if getattr(c, "enabled", True)}
    if not (enabled & {"miner", "aicf-worker", "server", "provider", "useful-work"}):
        return
    try:
        from agent_runtime.aicf_worker import bootstrap_bundle_from_hf
    except Exception:
        return
    # Optional fast-path skip: older agent_runtime builds don't export this, so
    # treat it as "unknown" (proceed to install; the HF cache makes it idempotent).
    try:
        from agent_runtime.aicf_worker import _has_servable_bundle
    except Exception:
        _has_servable_bundle = None

    # Pick a tier by hardware (catalog names tiny|small|flagship|large): a CPU / low-VRAM
    # box gets the light tiny model (still coder-tuned, CPU-runnable); bigger rigs get more.
    vram = float(getattr(caps, "vram_gb", 0) or 0)
    if vram >= 40:
        tier, gb = "flagship", 34.0
    elif vram >= 16:
        tier, gb = "small", 15.0
    else:
        tier, gb = "tiny", 4.0
    tier = os.environ.get("ANIMICA_AICF_TIER", tier)
    repo_override = os.environ.get("ANIMICA_AICF_MODEL", "").strip() or None

    try:
        if _has_servable_bundle is not None and _has_servable_bundle(tier):
            console.print(f"[dim]inference: {tier}-tier chat model already installed[/dim]")
            return
    except Exception:
        pass

    _total, _used, free = shutil.disk_usage(os.path.expanduser("~"))
    if free / 1e9 < gb * 1.3:
        console.print(
            f"[yellow]inference: skipping chat-model install — only {round(free/1e9, 1)}GB free "
            f"(need ~{round(gb * 1.3, 1)}GB for the {tier} model); "
            f"run 'animica miner setup' once you have space[/yellow]")
        return

    def _dl():
        try:
            bootstrap_bundle_from_hf(tier, repo_id=repo_override)
        except Exception:
            pass  # non-fatal; the worker / `animica miner setup` will retry on demand

    label = repo_override or f"{tier}-tier default"
    console.print(
        f"[dim]inference: installing chat/coding model ({label}) in background so this "
        f"miner serves Kimi K3 to the network…[/dim]")
    threading.Thread(target=_dl, name="animica-aicf-model-prefetch", daemon=True).start()


def _ensure_media_miner(components, console) -> None:
    """Serve generative-media jobs for the network from this node, in the BACKGROUND.

    `animica up` should serve media: when this node runs work and can render at least one media
    kind, join the Animica media QUEUE (register + claim loop) so image/video/multi-scene/
    image->video/music jobs GO THROUGH — rendered here, dispatched from the gateway. No model
    runs on the gateway. Env: ANIMICA_MEDIA_MINER=0 disables; ANIMICA_MEDIA_GATEWAY overrides the
    gateway (default https://animica.dev). Even a CPU box with ffmpeg serves image->video.
    """
    import os
    import threading

    if os.environ.get("ANIMICA_MEDIA_MINER", "1") == "0":
        return
    # `animica up` serves media by default: any node that CAN render (a GPU, or even a CPU with
    # ffmpeg for image->video) joins the media queue. Set ANIMICA_MEDIA_MINER=0 to opt out.
    try:
        from animica.media.miner import probe_capabilities, run_miner
    except Exception:
        return
    caps = probe_capabilities()
    if not caps:
        return  # nothing installed to render with; `animica media doctor` explains how to enable
    gateway = os.environ.get("ANIMICA_MEDIA_GATEWAY", "https://animica.dev")

    def _run():
        try:
            run_miner(gateway, caps=caps, poll_interval=float(os.environ.get("ANIMICA_MEDIA_POLL", "4")),
                      log=lambda m: console.print(f"[dim]media-miner: {m}[/dim]"))
        except Exception as e:  # never take down `up`
            console.print(f"[dim]media-miner: stopped ({e})[/dim]")

    console.print(f"[dim]media: serving jobs to {gateway} — capabilities: {', '.join(caps)}[/dim]")
    threading.Thread(target=_run, name="animica-media-miner", daemon=True).start()


def _ensure_animal(console) -> None:
    """Keep Animica Animal — the autonomous mascot — running, in the BACKGROUND.

    The mascot posts to the OWNED social accounts connected in the console (animica.dev/animal). It
    only makes sense on the box that runs the console, so it starts ONLY when ANIMAL_INTERNAL_TOKEN
    is configured (the gateway); miner rigs skip it. It is DRY-RUN by default — it renders previews
    until the operator connects accounts and flips ANIMAL_DRY_RUN=0 + ANIMAL_ALLOW_LIVE_POST=1.
    Env: ANIMICA_UP_ANIMAL=0 disables.
    """
    import os
    import threading

    if os.environ.get("ANIMICA_UP_ANIMAL", "1") == "0":
        return
    if not os.environ.get("ANIMAL_INTERNAL_TOKEN"):
        return  # no console link on this box — nothing for the mascot to read
    try:
        from animica.animal.engine import run_forever
        from animica.animal.config import load as _aload
    except Exception:
        return
    cfg = _aload()

    def _run():
        try:
            run_forever(cfg, log=lambda m: console.print(f"[dim]animal: {m}[/dim]"))
        except Exception as e:  # never take down `up`
            console.print(f"[dim]animal: stopped ({e})[/dim]")

    posture = "LIVE" if (not cfg.dry_run and cfg.allow_live_post) else "dry-run"
    console.print(f"[dim]animal: mascot ambassador running ({posture}, every {cfg.interval_secs}s)[/dim]")
    threading.Thread(target=_run, name="animica-animal", daemon=True).start()
