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
                                 _resolve_serve_pool, build_plan,
                                 detect_capabilities, plan_summary,
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
    # Serving is independent of joining an OPEN training round: a GPU rig serves
    # the promoted ENA checkpoint (animica-knowledge) regardless. Resolve a serve
    # pool even when no training pool was selected, so `animica up` brings ENA
    # online on its own rather than leaving the model with nothing to answer it.
    serve_pool_id = pool_id
    if caps.gpu and not serve_pool_id:
        serve_pool_id = _resolve_serve_pool(pool_host)
        if serve_pool_id:
            console.print(f"[green]serving ENA model[/green] → {serve_pool_id} "
                          f"[dim](promoted checkpoint)[/dim]")
    # Optional third-party GPU compute (Clore). Off unless the machine's operator
    # says yes on first run — it lets an anonymous renter run their own code here,
    # so it is never enabled silently. See cli/compute_consent.py.
    compute_enabled = False
    if caps.gpu:
        from .compute_consent import resolve_compute_consent
        compute_enabled = resolve_compute_consent(console, has_gpu=caps.gpu, plan=plan)
    # If the operator consented and we are actually running (not --plan), enroll
    # the Clore agent now. Fully isolated: any failure logs and mining/serving
    # continue untouched.
    if compute_enabled and not plan:
        try:
            from animica.compute.clore_agent import enroll, fetch_pool_token, is_enrolled
            if not is_enrolled():
                tok = fetch_pool_token(pool_host, worker_id or "", addr)
                enroll(console, token=tok or "", assume_yes=True)
            # The normal up stack (mine ANM + serve inference) keeps running below;
            # Clore only takes the GPU while a rental is active. So the priority is:
            #   rented on Clore  >  otherwise mine ANM + serve inference.
            console.print("[dim]compute: Clore rentals take priority; when idle this GPU "
                          "mines ANM and serves inference as usual.[/dim]")
        except Exception as exc:  # pragma: no cover - never break `up`
            console.print(f"[yellow]compute: enrollment skipped ({exc})[/yellow]")

    cfg = UnifiedConfig(address=addr, pool_host=pool_host, pool_port=pool_port,
                        pool_id=pool_id, serve_pool_id=serve_pool_id,
                        worker_id=worker_id or "",
                        run_node=with_node, threads=threads, serve_port=serve_port,
                        bittensor_token=bittensor_token,
                        compute_enabled=compute_enabled)
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
    _ensure_llm_model(caps, components, console)
    _ensure_media_miner(components, console)
    _ensure_inference_worker(components, console, addr)
    _ensure_animal(console)
    Supervisor(components).run()


# ── opt-in model selection (10.4.0) ─────────────────────────────────────────
# Per-precision bytes/param, used to estimate on-disk download size from the
# catalog's parameter counts (the catalog carries no size field).
_PRECISION_BYTES = {"fp32": 4.0, "float32": 4.0, "fp16": 2.0, "float16": 2.0,
                    "bf16": 2.0, "bfloat16": 2.0, "int8": 1.0, "q8": 1.0,
                    "int4": 0.5, "q4": 0.5}


def _model_sel_path():
    import os
    from pathlib import Path
    return Path(os.path.expanduser(
        os.environ.get("ANIMICA_DATA_DIR", "~/.animica"))) / "aicf-models.json"


def _load_model_selection() -> Optional[list[str]]:
    """Return the persisted tier selection, or None if never chosen."""
    try:
        import json
        p = _model_sel_path()
        if p.is_file():
            tiers = json.loads(p.read_text()).get("tiers")
            if isinstance(tiers, list):
                return [str(t) for t in tiers]
    except Exception:  # noqa: BLE001 — unreadable/corrupt state ⇒ treat as unset
        pass
    return None


def _save_model_selection(tiers: list[str]) -> None:
    try:
        import json
        import time
        p = _model_sel_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps({"tiers": list(tiers), "asked": int(time.time())},
                                  indent=1, sort_keys=True))
        tmp.replace(p)
    except Exception:  # noqa: BLE001 — persistence is best-effort
        pass


def _tier_dl_size_gb(tier: dict) -> float:
    """Rough on-disk download size for a tier's weights, in GB.

    bytes ≈ total_params × bytes_per_param; GB = bytes / 1e9. ``total_params_b``
    is in billions, so GB = params_b × bytes_per_param (the 1e9 cancels). +8%
    for tokenizer/config/index shards. Deliberately an over-estimate so the disk
    guard errs safe. For MoE tiers the download is ALL params (every expert), so
    total_params_b is the right figure, not active_params_b.
    """
    try:
        params_b = float(tier.get("total_params_b") or tier.get("active_params_b") or 0)
    except (TypeError, ValueError):
        params_b = 0.0
    bpp = _PRECISION_BYTES.get(str(tier.get("precision", "")).strip().lower(), 2.0)
    return params_b * bpp * 1.08


def _select_aicf_models(console) -> Optional[list[str]]:
    """Let the operator opt in to specific model tiers to serve, at startup.

    Shows each catalog tier with its hardware needs, estimated download size,
    eligibility and installed state, plus current disk free and projected disk
    after — then serves ONLY the tiers the operator picks. The choice persists
    to ~/.animica/aicf-models.json and drives BOTH what is downloaded
    (ANIMICA_AICF_AUTOPULL_TIERS) and what the worker serves
    (ANIMICA_AICF_SERVE_TIERS — NOT the stratum-vocab ANIMICA_AICF_TIERS), so a
    de-selected tier stops being served even if its bundle is still on disk.

    Precedence — fail-safe, never blocks a scripted/systemd run:
      explicit env pin  >  stored selection  >  interactive prompt  >  default.

    Returns: a non-empty tier list (chosen), ``[]`` (operator declined serving),
    or ``None`` (no selection made — fall through to the legacy hardware-eligible
    autopull unchanged).
    """
    import os
    import shutil
    import sys

    # 1. explicit operator pin always wins — no prompt, no persistence churn.
    #    Key only on the catalog-vocab vars this picker owns. NOT
    #    ANIMICA_AICF_TIERS — that is a separate stratum-vocab
    #    (free/standard/premium/elite) reference-miner var, unrelated to which
    #    model bundles the serving worker loads.
    if (os.environ.get("ANIMICA_AICF_AUTOPULL_TIERS", "").strip()
            or os.environ.get("ANIMICA_AICF_SERVE_TIERS", "").strip()):
        return None

    # 2. load the catalog; without it there is nothing to choose from.
    try:
        from agent_runtime.config import load_config
        catalog = dict(load_config().model_catalog)
    except Exception:  # noqa: BLE001
        return None
    tiers = [t for t in (catalog.get("tiers") or [])
             if isinstance(t, dict) and t.get("id")]
    if not tiers:
        return None

    try:
        from agent_runtime.hardware import detect_hardware, eligible_tiers
        eligible = set(eligible_tiers(detect_hardware(), catalog))
    except Exception:  # noqa: BLE001 — probe absent ⇒ CPU-runnable floor
        eligible = {str(t["id"]) for t in tiers
                    if float(t.get("min_vram_gb", 0) or 0) == 0}
    try:
        from agent_runtime.aicf_worker import _has_servable_bundle
    except Exception:  # noqa: BLE001
        def _has_servable_bundle(_t):  # type: ignore[misc]
            return False

    def _apply(selection: list[str]) -> list[str]:
        sel = [t for t in selection if t]
        # Catalog-vocab (tiny/small/flagship/large). Use dedicated vars, NOT
        # ANIMICA_AICF_TIERS: that is the stratum-vocab (free/standard/premium/
        # elite) reference-miner var, and the unified miner subprocess bakes it
        # into its own env (unified.py) where c.env overrides os.environ — so
        # setting it here would be shadowed anyway AND, if read by the serving
        # worker, would advertise zero servable tiers (no models/<stratum> dir).
        # ANIMICA_AICF_SERVE_TIERS is only written here and only read by
        # _start_aicf_worker; it is not in the miner c.env, so it propagates to
        # the subprocess cleanly.
        os.environ["ANIMICA_AICF_AUTOPULL_TIERS"] = ",".join(sel)  # what to download
        os.environ["ANIMICA_AICF_SERVE_TIERS"] = ",".join(sel)     # what to advertise
        if not sel:  # declined ⇒ don't pull, don't advertise
            os.environ["ANIMICA_AICF_NO_AUTOPULL"] = "1"
            os.environ["ANIMICA_DISABLE_AICF_WORKER"] = "1"
        return sel

    reselect = os.environ.get("ANIMICA_AICF_RESELECT", "").strip().lower() in {
        "1", "true", "yes", "on"}

    # 3. stored selection from a previous run — respect it silently.
    if not reselect:
        stored = _load_model_selection()
        if stored is not None:
            console.print(
                f"[dim]inference: serving your saved model selection: "
                f"{', '.join(stored) or '(none)'} "
                f"— re-choose with ANIMICA_AICF_RESELECT=1[/dim]")
            return _apply(stored)

    # 4. no TTY (systemd, CI, piped) → never block; legacy eligible default.
    #    isatty() can raise on a closed-but-non-None stdin (in-process embedders
    #    that close fd0); treat any failure as "not interactive", never raise.
    try:
        interactive = bool(sys.stdin and sys.stdin.isatty())
    except Exception:  # noqa: BLE001
        interactive = False
    if not interactive:
        return None

    # 5. interactive picker.
    cache_root = os.path.expanduser(os.environ.get("ANIMICA_DATA_DIR", "~/.animica"))
    try:
        probe = cache_root if os.path.isdir(cache_root) else "/"
        free_gb = shutil.disk_usage(probe).free / (1024 ** 3)
    except Exception:  # noqa: BLE001
        free_gb = float("inf")

    console.print("\n[bold]Select the AI models to serve[/bold] "
                  "[dim](inference jobs from animica.dev pay you in ANM; you "
                  "download & serve only what you pick)[/dim]")
    table = Table(show_lines=False)
    table.add_column("#", justify="right", width=2)
    table.add_column("Model / tier", style="bold")
    table.add_column("Params")
    table.add_column("Needs")
    table.add_column("Download", justify="right")
    table.add_column("Status")
    idx_map: dict[str, str] = {}
    default_ids: list[str] = []
    for i, t in enumerate(tiers, 1):  # catalog order = smallest → largest
        tid = str(t["id"])
        idx_map[str(i)] = tid
        idx_map[tid.lower()] = tid
        min_vram = float(t.get("min_vram_gb", 0) or 0)
        needs = ("CPU ok" if min_vram == 0 else f"{int(min_vram)}GB VRAM")
        needs += f" · {int(float(t.get('min_ram_gb', 0) or 0))}GB RAM"
        if _has_servable_bundle(tid):
            status = "[green]installed[/green]"
        elif tid in eligible:
            status = "[cyan]ready to pull[/cyan]"
        else:
            status = "[yellow]too big for this box[/yellow]"
        if tid in eligible:
            default_ids.append(tid)
        table.add_row(str(i), f"{tid} — {str(t.get('description', ''))[:40]}",
                      f"{t.get('total_params_b', '?')}B", needs,
                      f"~{_tier_dl_size_gb(t):.0f} GB", status)
    console.print(table)
    free_txt = "unknown" if free_gb == float("inf") else f"{free_gb:,.0f} GB"
    console.print(f"[dim]disk free at {cache_root}: [bold]{free_txt}[/bold][/dim]")

    default_str = ",".join(default_ids) if default_ids else "tiny"
    try:
        raw = typer.prompt(
            "\nModels to serve (numbers or names, comma-separated; 'none' to "
            "mine without serving)", default=default_str)
    except Exception:  # noqa: BLE001 — non-interactive fallthrough
        return _apply(default_ids)

    raw = (raw or "").strip()
    if raw.lower() in {"none", "skip", "-", "no"}:
        selection: list[str] = []
    else:
        picked: list[str] = []
        for tok in raw.replace(" ", ",").split(","):
            tok = tok.strip().lower()
            tid = idx_map.get(tok)
            if tid and tid not in picked:
                picked.append(tid)
        selection = picked or list(default_ids)

    # 6. disk / eligibility feedback + projected disk after.
    to_pull = sum(_tier_dl_size_gb(t) for t in tiers
                  if str(t["id"]) in selection and not _has_servable_bundle(str(t["id"])))
    ineligible = [s for s in selection if s not in eligible]
    if ineligible:
        console.print(
            f"[yellow]note:[/yellow] {', '.join(ineligible)} exceed this "
            f"machine's detected hardware — they'll download but may fail to "
            f"load. [dim]Deselect to skip.[/dim]")
    if selection:
        after_txt = ("unknown" if free_gb == float("inf")
                     else f"{max(0.0, free_gb - to_pull):,.0f} GB")
        console.print(
            f"[green]serving:[/green] {', '.join(selection)}  "
            f"[dim]· ~{to_pull:.0f} GB to download · disk after: ~{after_txt} free[/dim]")
        if free_gb != float("inf") and to_pull > free_gb:
            console.print(
                f"[red]warning:[/red] selection needs ~{to_pull:.0f} GB but only "
                f"{free_gb:,.0f} GB free — the pull stops when disk runs low.")
    else:
        console.print("[dim]serving: none — this node mines only, no inference.[/dim]")

    _save_model_selection(selection)
    return _apply(selection)


def _aicf_autopull_tiers(catalog: dict) -> list[str]:
    """Every catalog tier THIS machine can actually serve, smallest first.

    Hardware-gated on purpose. ``eligible_tiers`` requires RAM >= min_ram_gb and
    a GPU with vram_gb >= min_vram_gb (0 meaning CPU-runnable), which is the
    same rule ``AICFWorker`` uses to decide what to advertise. Pulling a tier
    past that gate would burn the disk on weights the worker will never load —
    ``large`` is DeepSeek-Coder-V2-Instruct at 236B parameters and wants 80 GB
    of VRAM, so on anything smaller it is hundreds of gigabytes of dead files.

    ANIMICA_AICF_AUTOPULL_TIERS pins the list explicitly (comma-separated) and
    skips the hardware check, for an operator who knows better than the probe.
    """
    import os

    override = os.environ.get("ANIMICA_AICF_AUTOPULL_TIERS", "").strip()
    if override:
        # Normalize any vocabulary (stratum 'standard' etc.) to catalog ids so
        # the bundle downloads to the dir the serving worker looks in.
        try:
            from agent_runtime.hardware import canonical_tiers
            return canonical_tiers([t.strip() for t in override.split(",") if t.strip()])
        except Exception:  # noqa: BLE001
            return [t.strip() for t in override.split(",") if t.strip()]

    order = [str(t.get("id")) for t in (catalog.get("tiers") or []) if isinstance(t, dict)]
    try:
        from agent_runtime.hardware import detect_hardware, eligible_tiers
        want = list(eligible_tiers(detect_hardware(), catalog))
    except Exception:  # noqa: BLE001 — probe unavailable; the CPU-runnable floor still applies
        want = [str(t.get("id")) for t in (catalog.get("tiers") or [])
                if isinstance(t, dict) and float(t.get("min_vram_gb", 0) or 0) == 0]
    return [t for t in order if t in set(want)]


def _ensure_aicf_bundle(console) -> None:
    """Install every model bundle this machine can serve, so it actually serves.

    THE PROBLEM THIS SOLVES. ``AICFWorker`` qualifies every tier through
    ``_has_servable_bundle()`` before advertising, and a tier with no bundle in
    ``$ANIMICA_DATA_DIR/models/<tier>/*/{manifest,inference}.json`` is dropped.
    A machine that has never installed one therefore registers with
    ``tiers: []`` — it heartbeats forever and can never be given work. That is
    not a bug in the gate: advertising a tier we cannot load just forces the
    node to grace-fallback to the stub on every claim. It is a bug in setup,
    and until now ``up`` only PRINTED the fix ("run aicf-worker pull") without
    ever performing it, which is why the live network has zero serving workers.

    WHY NOT ``aicf-worker pull``. That command takes an IPFS CID and a tier;
    ``up`` has no CID to pass. ``bootstrap_bundle_from_hf`` is the CID-free
    equivalent: it reads the tier's ``base_model`` from ai/configs/
    model_catalog.yaml, fetches it, and writes the manifest.json +
    inference.json pair that ``_has_servable_bundle`` looks for. Same outcome,
    no operator-supplied hash.

    SCOPE. Every tier the hardware can serve, not just the smallest — a rig that
    can run flagship should be earning on flagship. Ordered smallest-first so
    the node becomes servable at the first completed tier instead of after the
    whole queue, and each tier is skipped individually if it already has a
    bundle (so an interrupted run resumes where it stopped).

    COST, STATED PLAINLY. These are multi-gigabyte downloads and on an 80 GB
    GPU the eligible set runs to hundreds of gigabytes. It therefore runs on a
    background thread (never blocks startup), stops early when free disk falls
    below ANIMICA_AICF_AUTOPULL_MIN_FREE_GB (default 20), and reports every
    decision rather than silently doing either more or less than asked.

    Opt out with ANIMICA_AICF_NO_AUTOPULL=1 (or any of the AICF opt-outs, which
    are checked by the caller before this runs).
    """
    import os
    import shutil
    import threading

    if os.environ.get("ANIMICA_AICF_NO_AUTOPULL", "").strip().lower() in {"1", "true", "yes", "on"}:
        return
    # Pipeline / bare-capacity modes serve without a local bundle, so a download
    # would be pure waste.
    if (os.environ.get("ANIMICA_AICF_PIPELINE_MODEL_ID", "").strip()
            or os.environ.get("ANIMICA_AICF_ADVERTISE_WITHOUT_BUNDLE", "").strip().lower()
            in {"1", "true", "yes", "on"}):
        return

    try:
        from agent_runtime.aicf_worker import _has_servable_bundle, bootstrap_bundle_from_hf
        from agent_runtime.config import load_config
    except Exception:  # noqa: BLE001 — agent_runtime absent; nothing to do
        return

    try:
        catalog = dict(load_config().model_catalog)
        tiers = _aicf_autopull_tiers(catalog)
        missing = [t for t in tiers if not _has_servable_bundle(t)]
    except Exception:  # noqa: BLE001
        return
    if not missing:
        return

    try:
        min_free_gb = float(os.environ.get("ANIMICA_AICF_AUTOPULL_MIN_FREE_GB", "20") or 20)
    except ValueError:
        min_free_gb = 20.0
    cache_root = os.path.expanduser(
        os.environ.get("ANIMICA_DATA_DIR", "~/.animica"))

    have = [t for t in tiers if t not in missing]
    console.print(
        f"[dim]inference: fetching model bundles for tier(s) {', '.join(missing)} in the "
        f"background so this node can serve"
        + (f" (already installed: {', '.join(have)})" if have else "")
        + " — set ANIMICA_AICF_NO_AUTOPULL=1 to skip[/dim]"
    )

    def _free_gb() -> float:
        probe = cache_root
        while probe and not os.path.isdir(probe):
            parent = os.path.dirname(probe)
            if parent == probe:
                break
            probe = parent
        return shutil.disk_usage(probe or "/").free / (1024 ** 3)

    def _fetch() -> None:
        for tier in missing:
            try:
                free = _free_gb()
            except Exception:  # noqa: BLE001 — unreadable filesystem; just try
                free = float("inf")
            if free < min_free_gb:
                console.print(
                    f"[dim]inference: stopping before '{tier}' — only {free:.1f} GB free "
                    f"(need {min_free_gb:.0f} GB). Remaining tiers: {', '.join(missing[missing.index(tier):])}[/dim]")
                return
            try:
                path = bootstrap_bundle_from_hf(tier)
                console.print(f"[dim]inference: '{tier}' bundle ready at {path}[/dim]")
            except Exception as exc:  # noqa: BLE001 — must never break `up`
                console.print(f"[dim]inference: '{tier}' bundle fetch failed ({exc}) — "
                              f"continuing with the remaining tiers[/dim]")
        console.print("[dim]inference: bundle install finished — "
                      "restart `animica up` to advertise the new tiers[/dim]")

    threading.Thread(target=_fetch, name="animica.aicf_bundle_bootstrap", daemon=True).start()


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

    # Opt-in model selection (10.4.0): ask which model tiers to serve before
    # anything downloads or advertises. Sets ANIMICA_AICF_AUTOPULL_TIERS +
    # ANIMICA_AICF_TIERS from the choice (which propagate to the miner subprocess
    # via os.environ), so both the pull and the advertise honor exactly what the
    # operator picked. Returns [] only when serving was explicitly declined.
    try:
        _sel = _select_aicf_models(console)
    except Exception:  # noqa: BLE001 — the picker must NEVER break `up` startup
        _sel = None
    if _sel == []:
        console.print("[dim]inference: serving declined at model selection — "
                      "this node mines only. Re-choose: ANIMICA_AICF_RESELECT=1[/dim]")
        return

    # Make this node SERVABLE before anything advertises it. Must run on both
    # branches below: the miner subprocess starts its own --aicf worker and
    # qualifies tiers exactly the same way, so a missing bundle strands that
    # path too. Best-effort and non-blocking.
    _ensure_aicf_bundle(console)

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
    # Accept any vocabulary (stratum 'standard' etc.) — the bundle dir keys on
    # the catalog id. Identity for catalog names, so no change to documented use.
    try:
        from agent_runtime.hardware import canonical_tier
        tier = canonical_tier(tier)
    except Exception:  # noqa: BLE001
        pass
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
