"""
animica ai — the first-class AI command namespace (5.2.0).

A single, friendly entry point for AI users and providers: detect what this
machine can do, set it up, chat, serve an OpenAI-compatible endpoint, submit/track
compute jobs, run as a provider, benchmark, and see earnings.

Design rules (load-bearing):
  * This module is imported EAGERLY by cli/main.py, so it must import only light
    deps at module scope (typer, rich, stdlib). Every heavy/optional dependency
    (torch, transformers, the ENA stack, provider runtime) is imported lazily
    INSIDE the command that needs it, with a friendly `pip install "animica[...]"`
    hint on ImportError — never a stack trace.
  * Commands are safe by default: nothing spends ANM without an explicit quote or
    a configured cap (see `animica ai` payment UX, 5.2.0 §9).

Implemented so far: `doctor`. Further commands (setup/chat/models/serve/job/
provider/benchmark/earnings) land incrementally in the 5.2.0 series.
"""

from __future__ import annotations

import json as _json
import os
import platform
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

console = Console()

app = typer.Typer(
    name="ai",
    help="Animica AI: doctor, chat, serve, models, jobs, provider, earnings.",
    no_args_is_help=True,
)

# Status levels for doctor checks.
PASS, WARN, FAIL = "pass", "warn", "fail"
_ICON = {PASS: "[green]✔[/]", WARN: "[yellow]⚠[/]", FAIL: "[red]✘[/]"}
_PLAIN = {PASS: "ok", WARN: "warn", FAIL: "FAIL"}


@app.callback()
def _ai_main(
    no_color: bool = typer.Option(False, "--no-color", help="Disable colored output."),
) -> None:
    """Animica AI commands. Use --no-color for plain output (also honors $NO_COLOR)."""
    # Reassign the shared console so every `ai` subcommand (and sub-group) renders
    # without color. Subcommands reference the module-global `console` at call time,
    # so this takes effect before any of them run.
    global console
    if no_color or os.environ.get("NO_COLOR"):
        console = Console(no_color=True, highlight=False)


def _check(name: str, status: str, detail: str = "", fix: str = "") -> dict[str, Any]:
    return {"name": name, "status": status, "detail": detail, "fix": fix}


def _module_version(mod: str) -> str | None:
    """Return an installed dist version without importing the (heavy) module."""
    try:
        from importlib.metadata import PackageNotFoundError, version
        try:
            return version(mod)
        except PackageNotFoundError:
            return None
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Individual checks — each is best-effort and must never raise.
# --------------------------------------------------------------------------- #
def _chk_python() -> dict[str, Any]:
    v = sys_version = platform.python_version()
    major, minor = map(int, v.split(".")[:2])
    if (major, minor) >= (3, 10):
        return _check("Python", PASS, f"{v} on {platform.python_implementation()}")
    return _check("Python", FAIL, f"{v} — Animica needs >= 3.10",
                  fix="Install Python 3.10+ and reinstall: pip install --upgrade animica")


def _chk_os() -> dict[str, Any]:
    return _check("OS", PASS, f"{platform.system()} {platform.release()} ({platform.machine()})")


def _chk_cpu() -> dict[str, Any]:
    n = os.cpu_count() or 0
    status = PASS if n >= 2 else WARN
    return _check("CPU", status, f"{n} logical cores",
                  fix="" if n >= 2 else "A multi-core CPU is recommended for useful-work/inference.")


def _chk_ram() -> dict[str, Any]:
    gb = None
    try:
        gb = (os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")) / (1024 ** 3)
    except (ValueError, OSError, AttributeError):
        try:
            import psutil  # type: ignore
            gb = psutil.virtual_memory().total / (1024 ** 3)
        except Exception:
            gb = None
    if gb is None:
        return _check("RAM", WARN, "could not detect")
    status = PASS if gb >= 4 else WARN
    return _check("RAM", status, f"{gb:.1f} GiB",
                  fix="" if gb >= 4 else "Low RAM — large models may not load; use small models or CPU useful-work.")


def _chk_gpu() -> dict[str, Any]:
    # Prefer torch if present (authoritative for CUDA/ROCm/MPS); else nvidia-smi.
    try:
        import torch  # type: ignore
        if torch.cuda.is_available():
            try:
                name = torch.cuda.get_device_name(0)
                vram = torch.cuda.get_device_properties(0).total_memory / (1024 ** 3)
                return _check("GPU", PASS, f"CUDA: {name} ({vram:.0f} GiB VRAM), torch {torch.__version__}")
            except Exception:
                return _check("GPU", PASS, f"CUDA available, torch {torch.__version__}")
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return _check("GPU", PASS, f"Apple Metal (MPS), torch {torch.__version__}")
        return _check("GPU", WARN, "torch present but no CUDA/MPS — CPU only",
                      fix="GPU jobs need CUDA (NVIDIA) or MPS (Apple). CPU useful-work still works.")
    except Exception:
        pass
    smi = shutil.which("nvidia-smi")
    if smi:
        try:
            out = subprocess.run([smi, "--query-gpu=name,memory.total", "--format=csv,noheader"],
                                 capture_output=True, text=True, timeout=8)
            line = (out.stdout or "").strip().splitlines()
            if line:
                return _check("GPU", WARN, f"NVIDIA GPU: {line[0].strip()} (torch not installed)",
                              fix='Install GPU stack: pip install "animica[gpu]"')
        except Exception:
            pass
        return _check("GPU", WARN, "nvidia-smi present (torch not installed)",
                      fix='Install GPU stack: pip install "animica[gpu]"')
    return _check("GPU", WARN, "no GPU detected — CPU only",
                  fix="CPU useful-work works without a GPU; GPU train/serve needs CUDA or Apple MPS.")


def _chk_torch() -> dict[str, Any]:
    ver = _module_version("torch")
    if ver:
        return _check("PyTorch", PASS, f"torch {ver}")
    return _check("PyTorch", WARN, "not installed",
                  fix='For AI train/serve: pip install "animica[ai]" (CPU) or "animica[gpu]" (CUDA)')


def _chk_extras() -> dict[str, Any]:
    # Map a representative dist -> the extra that provides it.
    probes = {
        "transformers": "ai", "sentence-transformers": "ai", "trl": "ai",
        "bitsandbytes": "gpu", "fastapi": "backend", "mcp": "mcp",
        "cloudpickle": "studio",
    }
    present = sorted({extra for dist, extra in probes.items() if _module_version(dist)})
    if present:
        return _check("Extras", PASS, "installed: " + ", ".join(present))
    return _check("Extras", WARN, "none of ai/gpu/mcp/studio detected",
                  fix='Install what you need, e.g. pip install "animica[ai]" or "animica[all]"')


def _chk_ollama() -> dict[str, Any]:
    base = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
    if not base.startswith("http"):
        base = "http://" + base
    try:
        import urllib.request
        with urllib.request.urlopen(base + "/api/tags", timeout=4) as r:  # noqa: S310
            data = _json.loads(r.read().decode("utf-8"))
        models = [m.get("name") for m in data.get("models", [])]
        return _check("Ollama", PASS, f"running at {base} ({len(models)} models)")
    except Exception:
        return _check("Ollama", WARN, f"not reachable at {base}",
                      fix="Optional local model runtime. Install from https://ollama.com then `ollama serve`.")


def _chk_network() -> dict[str, Any]:
    for host, port in (("1.1.1.1", 53), ("8.8.8.8", 53)):
        try:
            with socket.create_connection((host, port), timeout=3):
                return _check("Network", PASS, "internet reachable")
        except OSError:
            continue
    return _check("Network", WARN, "no outbound connectivity detected",
                  fix="Remote provider routing + model pulls need internet; local/offline modes still work.")


def _resolve_rpc_url() -> str:
    url = os.environ.get("ANIMICA_RPC_URL", "").strip()
    return url or "http://127.0.0.1:8545/rpc"


def _rpc(url: str, method: str, params: list[Any], timeout: float = 5.0) -> Any:
    import urllib.request
    body = _json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(url, data=body, headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
        out = _json.loads(r.read().decode("utf-8"))
    if out.get("error"):
        raise RuntimeError(out["error"])
    return out.get("result")


def _chk_rpc() -> dict[str, Any]:
    url = _resolve_rpc_url()
    try:
        head = _rpc(url, "chain.getHead", [])
        h = head.get("height") if isinstance(head, dict) else head
        return _check("Node RPC", PASS, f"{url} (head height {h})")
    except Exception:
        return _check("Node RPC", WARN, f"{url} unreachable",
                      fix="Start a node: `animica up` or `animica node up`; or set ANIMICA_RPC_URL to a remote node.")


def _chk_wallet() -> dict[str, Any]:
    try:
        from .paths import get_animica_home
        home = get_animica_home()
        candidates = [home / "wallets", home / "keystore", home / "wallet.json"]
        found = [str(p) for p in candidates if p.exists()]
        # Also honor an explicitly configured payout wallet.
        try:
            from . import app_config
            payout = app_config.get("payout_wallet", section="ai")
        except Exception:
            payout = None
        if found or payout:
            detail = (f"payout wallet configured" if payout else "wallet store present")
            return _check("Wallet", PASS, detail)
        return _check("Wallet", WARN, "no wallet found",
                      fix="Create/import one: `animica wallet new` (then `animica ai setup` to set a payout address).")
    except Exception:
        return _check("Wallet", WARN, "could not check wallet store")


def _chk_pool() -> dict[str, Any]:
    base = os.environ.get("ANIMICA_POOL_API", "http://127.0.0.1:8550").rstrip("/")
    try:
        import urllib.request
        with urllib.request.urlopen(base + "/api/mining/status", timeout=4) as r:  # noqa: S310
            data = _json.loads(r.read().decode("utf-8"))
        return _check("Pool", PASS, f"reachable at {base} (miners {data.get('miners', '?')})")
    except Exception:
        return _check("Pool", WARN, "no local pool API",
                      fix="Optional. To mine to a pool, point your miner at a stratum endpoint (e.g. pool.animica.org:3333).")


def _chk_studio() -> dict[str, Any]:
    ver = _module_version("cloudpickle")
    if ver:
        return _check("Studio", PASS, "serverless SDK ready (local mode available)")
    return _check("Studio", WARN, "serverless extras not installed",
                  fix='For remote functions: pip install "animica[studio]"')


def _chk_bittensor() -> dict[str, Any]:
    # Eligibility is CUDA-gated (the SN51 GPU pool needs an NVIDIA GPU).
    cuda = False
    try:
        import torch  # type: ignore
        cuda = bool(torch.cuda.is_available())
    except Exception:
        cuda = shutil.which("nvidia-smi") is not None
    if cuda:
        return _check("Bittensor", PASS, "CUDA present — eligible for GPU routing",
                      fix="See: `animica bittensor` for SN51 participation.")
    return _check("Bittensor", WARN, "no CUDA — not eligible for GPU routing",
                  fix="Bittensor GPU routing needs an NVIDIA GPU; other earning modes are unaffected.")


_CHECKS = [
    _chk_python, _chk_os, _chk_cpu, _chk_ram, _chk_gpu, _chk_torch, _chk_extras,
    _chk_ollama, _chk_network, _chk_rpc, _chk_wallet, _chk_pool, _chk_studio, _chk_bittensor,
]


@app.command("doctor")
def doctor(
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Only print warnings/failures."),
) -> None:
    """Diagnose this machine's AI readiness with exact fix commands.

    Exit code: 0 if no failures, 1 if any check FAILED (warnings do not fail).
    """
    results = []
    for fn in _CHECKS:
        try:
            results.append(fn())
        except Exception as e:  # noqa: BLE001 — a probe must never crash doctor
            results.append(_check(fn.__name__.replace("_chk_", ""), WARN, f"probe error: {e}"))

    failed = sum(1 for r in results if r["status"] == FAIL)
    warned = sum(1 for r in results if r["status"] == WARN)

    if json_output:
        typer.echo(_json.dumps({
            "ok": failed == 0,
            "failed": failed, "warnings": warned,
            "checks": results,
        }, indent=2))
        raise typer.Exit(code=1 if failed else 0)

    table = Table(title="animica ai doctor", show_lines=False)
    table.add_column("", justify="center", width=3)
    table.add_column("Check", style="bold")
    table.add_column("Detail")
    for r in results:
        if quiet and r["status"] == PASS:
            continue
        table.add_row(_ICON.get(r["status"], "?"), r["name"], r["detail"])
    console.print(table)

    # Fix hints for anything not passing — "what to do next". Escape the fix text:
    # it contains pip extras like `animica[studio]` whose `[studio]` would
    # otherwise be parsed (and silently dropped) as Rich markup.
    from rich.markup import escape
    todo = [r for r in results if r["status"] != PASS and r["fix"]]
    if todo:
        console.print("\n[bold]Next steps:[/]")
        for r in todo:
            color = "red" if r["status"] == FAIL else "yellow"
            console.print(f"  [{color}]{_PLAIN[r['status']]}[/] "
                          f"{escape(r['name'])}: {escape(r['fix'])}")

    if failed:
        console.print(f"\n[red]✘ {failed} check(s) failed[/], {warned} warning(s).")
    elif warned:
        console.print(f"\n[green]✔ ready[/] (with {warned} optional warning(s)). "
                      f"Next: [bold]animica ai setup[/] then [bold]animica ai chat[/].")
    else:
        console.print("\n[green]✔ all checks passed[/] — try [bold]animica ai chat[/].")
    raise typer.Exit(code=1 if failed else 0)


# --------------------------------------------------------------------------- #
# Shared model resolution — reuses the ENA provider stack (cli/ena.py uses the
# same path). The ollama/openai adapters are pure HTTP, so this works on the
# base install (no torch / no [ai] extra needed for hosted/local-server models).
# --------------------------------------------------------------------------- #
def _list_ollama_models(base: str | None = None) -> list[str]:
    base = (base or os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")).rstrip("/")
    if not base.startswith("http"):
        base = "http://" + base
    try:
        import urllib.request
        with urllib.request.urlopen(base + "/api/tags", timeout=4) as r:  # noqa: S310
            data = _json.loads(r.read().decode("utf-8"))
        return [m.get("name") for m in data.get("models", []) if m.get("name")]
    except Exception:
        return []


# Substrings that mark an embedding-only model — these cannot serve /api/chat,
# so they must never be auto-picked as the default *chat* model.
_EMBED_HINTS = ("embed", "bge", "gte-", "e5-", "mxbai", "nomic-embed", "all-minilm")


def is_embedding_model(name: str) -> bool:
    n = (name or "").lower()
    return any(h in n for h in _EMBED_HINTS)


def _pick_chat_model(models: list[str]) -> str | None:
    """Pick a sensible default chat model: first non-embedding model, else first."""
    for m in models:
        if not is_embedding_model(m):
            return m
    return models[0] if models else None


def _resolve_adapter(provider: str | None, model: str | None, local: bool):
    """Resolve (adapter, ModelProviderConfig, provider_key) from flags > config.toml > ENA.

    Precedence — provider key: --local(=ollama) > --provider > config.toml[ai].provider
    > ENA default_model_provider. Model: --model > config.toml[ai].default_model >
    the provider's configured model. Returns a built ModelAdapter ready to generate().
    """
    from . import app_config
    from animica.ena import config as ena_config
    from animica.ena.models import ModelProviderConfig
    from animica.ena.providers import build_model_adapter

    cfg_ena = ena_config.load_config()
    if local:
        key = "ollama"
    else:
        key = provider or app_config.get("provider", section="ai") or cfg_ena.default_model_provider

    mp = cfg_ena.model_provider(key)
    # If ollama is requested but not configured in the ENA config, synthesize it
    # against the local server so `--local` works out of the box.
    if key == "ollama" and mp.provider != "ollama":
        mp = ModelProviderConfig(
            name="ollama", provider="ollama", transport="http",
            model=model or app_config.get("default_model", section="ai") or "qwen2.5:7b",
            base_url=os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434"),
        )
    # 7.1.1 mesh backends: synthesize a config so `--provider anthropic|claude|
    # chutes|bittensor|ena_served` works without a config.toml entry.
    _MESH_DEFAULTS = {
        "anthropic": ("claude-opus-4-8", ["ANTHROPIC_API_KEY"]),
        "claude": ("claude-opus-4-8", ["ANTHROPIC_API_KEY"]),
        "chutes": ("", ["CHUTES_API_TOKEN"]),
        "bittensor": ("", ["CHUTES_API_TOKEN"]),
        "ena_served": ("", []),
    }
    if key in _MESH_DEFAULTS and mp.provider != key:
        default_model, env_vars = _MESH_DEFAULTS[key]
        mp = ModelProviderConfig(name=key, provider=key, transport="http",
                                 model=model or default_model,
                                 api_key_env_vars=env_vars)
    # Model override precedence.
    chosen_model = model or (None if provider else app_config.get("default_model", section="ai"))
    if chosen_model:
        mp.model = chosen_model
    return build_model_adapter(mp), mp, key


@app.command("setup")
def setup(
    mode: str = typer.Option(None, "--mode",
                             help="consumer | provider | both (skip the prompt)."),
    provider: str = typer.Option(None, "--provider", help="Default model provider key (e.g. ollama)."),
    model: str = typer.Option(None, "--model", "-m", help="Default model (e.g. qwen2.5:7b)."),
    payout_wallet: str = typer.Option(None, "--payout-wallet", help="Address to receive provider earnings."),
    max_spend: float = typer.Option(None, "--max-spend",
                                    help="Spending cap in ANM for consumer jobs (0 = ask every time)."),
    use_gpu: bool = typer.Option(None, "--gpu/--no-gpu", help="Offer GPU for provider workloads."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Accept detected defaults; no prompts."),
    show: bool = typer.Option(False, "--show", help="Print the current config and exit."),
    json_output: bool = typer.Option(False, "--json", help="Emit the resulting config as JSON."),
) -> None:
    """Configure Animica AI — writes ~/.animica/config.toml. Safe: never spends ANM."""
    from . import app_config

    if show:
        cfg = app_config.load_config()
        if json_output:
            typer.echo(_json.dumps(cfg, indent=2))
        else:
            console.print(f"[bold]config:[/] {app_config.config_path()}")
            if cfg:
                console.print_json(data=cfg)
            else:
                console.print("[yellow](empty — run `animica ai setup`)[/]")
        raise typer.Exit(0)

    existing = app_config.load_config()
    ai_tbl = dict(existing.get("ai") or {})
    import sys as _sys
    interactive = not yes and not json_output and (mode is None) and _sys.stdin.isatty()

    # Detect a sensible default provider/model from the environment. Prefer a
    # chat-capable model — never an embedding-only model like nomic-embed-text.
    ollama_models = _list_ollama_models()
    detected_provider = "ollama" if ollama_models else "deterministic"
    detected_model = _pick_chat_model(ollama_models) or "deterministic"

    if interactive:
        console.print("[bold cyan]Animica AI setup[/] — configure once, change anytime.\n")
        mode = typer.prompt("Use AI as consumer, provider, or both?",
                            default=existing.get("mode") or "consumer")
        if ollama_models:
            console.print(f"Detected local Ollama models: {', '.join(ollama_models[:6])}")
        provider = typer.prompt("Default model provider", default=ai_tbl.get("provider") or detected_provider)
        model = typer.prompt("Default model", default=ai_tbl.get("default_model") or detected_model)
        if mode in ("provider", "both"):
            payout_wallet = typer.prompt("Payout wallet (blank to set later)",
                                         default=ai_tbl.get("payout_wallet") or "", show_default=False) or None
            use_gpu = typer.confirm("Offer GPU for workloads?", default=bool(ai_tbl.get("use_gpu")))
        if mode in ("consumer", "both"):
            cap = typer.prompt("Spend cap in ANM per session (0 = ask every time)",
                               default=str(ai_tbl.get("max_spend_anm", 0)))
            try:
                max_spend = float(cap)
            except ValueError:
                max_spend = 0.0

    # Apply with sane fallbacks (non-interactive / --yes path).
    new = dict(existing)
    new["mode"] = mode or existing.get("mode") or "consumer"
    ai_tbl["provider"] = provider or ai_tbl.get("provider") or detected_provider
    ai_tbl["default_model"] = model or ai_tbl.get("default_model") or detected_model
    if payout_wallet is not None:
        ai_tbl["payout_wallet"] = payout_wallet
    if max_spend is not None:
        ai_tbl["max_spend_anm"] = max_spend
    if use_gpu is not None:
        ai_tbl["use_gpu"] = bool(use_gpu)
    new["ai"] = ai_tbl
    path = app_config.save_config(new)

    if json_output:
        typer.echo(_json.dumps(new, indent=2))
        raise typer.Exit(0)
    console.print(f"\n[green]✔ saved[/] {path}")
    console.print(f"  mode: [bold]{new['mode']}[/]   provider: [bold]{ai_tbl['provider']}[/]   "
                  f"model: [bold]{ai_tbl['default_model']}[/]")
    if ai_tbl.get("payout_wallet"):
        console.print(f"  payout wallet: {ai_tbl['payout_wallet']}")
    if "max_spend_anm" in ai_tbl:
        cap = ai_tbl["max_spend_anm"]
        console.print(f"  spend cap: {'ask every time' if not cap else str(cap) + ' ANM'}")
    console.print("\nNext: [bold]animica ai chat[/] (or `animica ai models` to see what's available).")


@app.command("models")
def models(
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """List available models — local (Ollama) and configured providers — and the default."""
    from . import app_config
    from animica.ena import config as ena_config

    cfg_ena = ena_config.load_config()
    default_provider = app_config.get("provider", section="ai") or cfg_ena.default_model_provider
    default_model = app_config.get("default_model", section="ai")

    rows: list[dict[str, Any]] = []
    # Configured ENA providers (these are routable today).
    for key, mp in (cfg_ena.model_providers or {}).items():
        rows.append({"name": mp.model, "provider": mp.provider, "key": key,
                     "kind": "embed" if is_embedding_model(mp.model) else "chat",
                     "source": "configured", "endpoint": mp.base_url or mp.endpoint or "",
                     "default": key == default_provider})
    # Live local Ollama models (pullable/runnable now).
    for name in _list_ollama_models():
        if not any(r["provider"] == "ollama" and r["name"] == name for r in rows):
            rows.append({"name": name, "provider": "ollama", "key": "ollama",
                         "kind": "embed" if is_embedding_model(name) else "chat",
                         "source": "ollama-local", "endpoint": "http://127.0.0.1:11434",
                         "default": False})

    if json_output:
        typer.echo(_json.dumps({"default_provider": default_provider,
                                "default_model": default_model, "models": rows}, indent=2))
        raise typer.Exit(0)

    if not rows:
        console.print("[yellow]No models discovered.[/] Install Ollama and pull a model, or run "
                      "[bold]animica ai setup[/] to configure a provider.")
        raise typer.Exit(0)
    table = Table(title="animica ai models")
    table.add_column("Model", style="bold")
    table.add_column("Kind")
    table.add_column("Provider")
    table.add_column("Source")
    table.add_column("Endpoint")
    for r in sorted(rows, key=lambda x: (x["provider"], x["name"])):
        star = " [green]★[/]" if r["default"] else ""
        kind = "[dim]embed[/]" if r["kind"] == "embed" else "chat"
        table.add_row(r["name"] + star, kind, r["provider"], r["source"], r["endpoint"])
    console.print(table)
    console.print(f"\ndefault provider: [bold]{default_provider}[/]"
                  + (f", default model: [bold]{default_model}[/]" if default_model else "")
                  + "  (★ = default). Use: [bold]animica ai chat -m <model>[/]")


@app.command("chat")
def chat(
    message: str = typer.Argument(None, help="One-shot prompt. Omit for an interactive session."),
    model: str = typer.Option(None, "--model", "-m", help="Model override (e.g. qwen2.5:7b)."),
    provider: str = typer.Option(None, "--provider", help="Provider key override (e.g. ollama)."),
    local: bool = typer.Option(False, "--local", help="Force the local Ollama runtime."),
    system: str = typer.Option(None, "--system", "-s", help="System prompt."),
    temperature: float = typer.Option(None, "--temperature", "-t", help="Sampling temperature."),
    max_tokens: int = typer.Option(None, "--max-tokens", help="Max tokens to generate."),
    json_output: bool = typer.Option(False, "--json", help="One-shot: emit JSON {provider,model,response}."),
) -> None:
    """Chat with a model through Animica's AI layer (local or configured provider)."""
    from animica.ena.providers import ProviderError

    try:
        adapter, mp, key = _resolve_adapter(provider, model, local)
    except Exception as e:  # noqa: BLE001
        console.print(f"[red]Could not resolve a model:[/] {e}", markup=False)
        console.print('Try [bold]animica ai setup[/] or install Ollama (see [bold]animica ai doctor[/]).')
        raise typer.Exit(code=2)

    def _ask(prompt: str, history: list[dict[str, str]]) -> str:
        return adapter.generate(prompt, system=system, history=history,
                                max_tokens=max_tokens, temperature=temperature)

    # One-shot mode.
    if message is not None:
        try:
            out = _ask(message, [])
        except ProviderError as e:
            console.print(f"[red]Model error:[/] {e}", markup=False)
            console.print(f"Is the provider reachable? Check [bold]animica ai doctor[/] "
                          f"(provider={mp.provider}, model={mp.model}).")
            raise typer.Exit(code=1)
        if json_output:
            typer.echo(_json.dumps({"provider": mp.provider, "model": mp.model, "response": out}, indent=2))
        else:
            console.print(out)
        raise typer.Exit(0)

    # Interactive REPL.
    console.print(f"[bold cyan]animica ai chat[/] — provider [bold]{mp.provider}[/], "
                  f"model [bold]{mp.model}[/]. /exit, /reset, /system <text>.")
    history: list[dict[str, str]] = []
    while True:
        try:
            line = console.input("[bold green]you ›[/] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\nbye.")
            break
        if not line:
            continue
        if line in ("/exit", "/quit", ":q"):
            break
        if line == "/reset":
            history = []
            console.print("[dim](history cleared)[/]")
            continue
        if line.startswith("/system"):
            system = line[len("/system"):].strip() or None
            console.print(f"[dim](system prompt {'set' if system else 'cleared'})[/]")
            continue
        try:
            out = _ask(line, history)
        except ProviderError as e:
            console.print(f"[red]Model error:[/] {e}", markup=False)
            continue
        console.print(f"[bold magenta]ai ›[/] {out}")
        history.append({"role": "user", "content": line})
        history.append({"role": "assistant", "content": out})


@app.command("serve")
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind host."),
    port: int = typer.Option(8080, "--port", "-p", help="Bind port."),
    api_key: str = typer.Option(None, "--api-key",
                                help="Require this bearer token on /v1/* requests (recommended for 0.0.0.0)."),
    rate_limit: int = typer.Option(None, "--rate-limit",
                                   help="Max requests/min per client (per api-key or IP). Off by default."),
    provider: str = typer.Option(None, "--provider", help="Default provider key for unrouted models."),
    model: str = typer.Option(None, "--model", "-m", help="Default model name."),
) -> None:
    """Serve an OpenAI-compatible API (/v1/chat/completions, /completions, /embeddings, /models).

    Point any OpenAI client at http://<host>:<port>/v1. Routes to the same
    local/configured providers as `animica ai chat`.
    """
    from animica.ai.gateway import GatewayNotInstalled, build_app

    # Env fallbacks let a managed service (systemd) supply the bearer key via a
    # 0600 EnvironmentFile instead of the command line, keeping it out of argv/ps.
    api_key = api_key or os.environ.get("ANIMICA_AI_GATEWAY_API_KEY") or None
    if rate_limit is None:
        _rl = (os.environ.get("ANIMICA_AI_GATEWAY_RATE_LIMIT") or "").strip()
        rate_limit = int(_rl) if _rl.isdigit() else None

    try:
        app_obj = build_app(api_key=api_key, provider=provider, model=model,
                            rate_limit_per_min=rate_limit)
    except GatewayNotInstalled as e:
        console.print(str(e), style="bold red", markup=False)
        raise typer.Exit(code=2)
    try:
        import uvicorn
    except Exception:
        console.print('animica ai serve needs uvicorn — install with: pip install "animica[backend]"',
                      style="bold red", markup=False)
        raise typer.Exit(code=2)

    if host == "0.0.0.0" and not api_key:
        console.print("[yellow]⚠ binding 0.0.0.0 without --api-key — the endpoint is open to your network.[/]")
    console.print(f"[cyan]Animica AI Gateway[/] → http://{host}:{port}/v1  "
                  f"({'auth required' if api_key else 'open, no auth'})")
    console.print(f"  try: [bold]curl http://{host}:{port}/v1/models[/]")
    uvicorn.run(app_obj, host=host, port=port, log_level="info")


# =========================================================================== #
# AI jobs marketplace — consumer (`ai job`) + provider (`ai provider`) + earnings
# + benchmark. These talk to the LIVE aicf.* RPC protocol via animica.ai.market.
# Only `ai job submit` spends ANM, and only after a quote + explicit confirmation
# (5.2.0 §9: no surprise spending).
# =========================================================================== #
job_app = typer.Typer(name="job", help="Submit & track AICF inference jobs.", no_args_is_help=True)
provider_app = typer.Typer(name="provider", help="Run as an AICF compute provider.", no_args_is_help=True)


def _est_prompt_tokens(text: str) -> int:
    return max(1, len(text or "") // 4)


@job_app.command("estimate")
def job_estimate(
    prompt: str = typer.Argument(..., help="Prompt to price."),
    tier: str = typer.Option(None, "--tier", help="Preferred tier (e.g. fast, standard, elite)."),
    max_tokens: int = typer.Option(256, "--max-tokens", help="Max output tokens."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Quote a job's cost WITHOUT spending (aicf.estimateJobCost)."""
    from animica.ai import market
    try:
        est = market.estimate(_est_prompt_tokens(prompt), max_tokens, tier)
    except market.MarketError as e:
        console.print(f"[red]{e}[/]", markup=False)
        console.print("Is a node running? [bold]animica ai doctor[/] checks Node RPC.")
        raise typer.Exit(code=1)
    if json_output:
        typer.echo(_json.dumps(est, indent=2))
        raise typer.Exit(0)
    console.print(f"[bold]cost:[/] {est.get('cost_animica')} ANM   "
                  f"[bold]tier:[/] {est.get('tier')}   "
                  f"[bold]providers:[/] {est.get('providers')}   "
                  f"[bold]~latency:[/] {est.get('latency_ms')} ms")


@job_app.command("submit")
def job_submit(
    prompt: str = typer.Argument(..., help="Prompt to run."),
    tier: str = typer.Option(None, "--tier", help="Preferred tier."),
    max_tokens: int = typer.Option(256, "--max-tokens", help="Max output tokens."),
    from_address: str = typer.Option(None, "--from", help="Wallet address to pay from."),
    wallet: str = typer.Option(None, "--wallet", help="Path to wallets.json (default ~/.animica/wallets.json)."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the spend confirmation prompt."),
    estimate_only: bool = typer.Option(False, "--estimate-only", help="Quote only; never spend."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Submit a paid inference job. Quotes first, then asks before spending ANM."""
    from . import app_config
    from .paths import get_animica_home
    from animica.ai import market

    # 1) Quote (always, before any spend).
    try:
        est = market.estimate(_est_prompt_tokens(prompt), max_tokens, tier)
    except market.MarketError as e:
        console.print(f"[red]{e}[/]", markup=False)
        raise typer.Exit(code=1)
    cost = float(est.get("cost_animica") or 0)
    resolved_tier = est.get("tier")
    if not json_output:
        console.print(f"[bold]Quote:[/] {cost} ANM (tier {resolved_tier}, "
                      f"{est.get('providers')} providers, ~{est.get('latency_ms')} ms)")
    if estimate_only:
        if json_output:
            typer.echo(_json.dumps({"quote": est}, indent=2))
        raise typer.Exit(0)

    # 2) Spend-cap guard (config.toml [ai].max_spend_anm; 0/unset = ask every time).
    cap = app_config.get("max_spend_anm", section="ai")
    if cap and cost > float(cap) and not yes:
        console.print(f"[red]✘ cost {cost} ANM exceeds your spend cap of {cap} ANM.[/] "
                      f"Re-run with --yes to override, or raise the cap via `animica ai setup`.")
        raise typer.Exit(code=1)

    # 3) Explicit confirmation — NEVER spend silently.
    if not yes:
        import sys as _sys
        if not _sys.stdin.isatty():
            console.print("[red]✘ refusing to spend without confirmation[/] (no TTY). "
                          "Pass --yes to authorize, or --estimate-only to just quote.")
            raise typer.Exit(code=1)
        if not typer.confirm(f"Spend {cost} ANM to run this job?", default=False):
            console.print("aborted — no funds moved.")
            raise typer.Exit(code=1)

    # 4) Build + sign the payment envelope (reuses the canonical signer; ANM→base
    #    units handled by sign_payment_tx, and it salts the tx so it can't collide).
    try:
        from animica.wallet.payment import PaymentSigningError, sign_payment_tx
    except Exception as e:  # noqa: BLE001
        console.print(f"[red]wallet/payment unavailable: {e}[/]", markup=False)
        raise typer.Exit(code=2)
    wallet_path = wallet or str(get_animica_home() / "wallets.json")
    try:
        treasury = market.treasury_address()
    except market.MarketError as e:
        console.print(f"[red]{e}[/]", markup=False)
        raise typer.Exit(code=1)
    if not treasury:
        console.print("[red]✘ node did not return an AICF treasury address.[/]")
        raise typer.Exit(code=1)
    chain_id = int(os.environ.get("ANIMICA_CHAIN_ID", "1"))
    # Resolve a mempool-aware nonce (best-effort; payment tx is salted regardless).
    try:
        sender_for_nonce = from_address or ""
        nonce = int(market.call("state.getNonce", [sender_for_nonce, "pending"])) if sender_for_nonce else 0
    except Exception:
        nonce = 0
    try:
        txn_hex = sign_payment_tx(wallet_path=wallet_path, recipient=treasury, amount=cost,
                                  nonce=nonce, chain_id=chain_id, from_address=from_address)
    except PaymentSigningError as e:
        console.print(f"[red]could not sign payment:[/] {e}", markup=False)
        console.print("Create/import a wallet first: [bold]animica wallet new[/].")
        raise typer.Exit(code=1)

    # 5) Submit with the signed payment.
    spec = {"prompt": prompt, "tier_preferred": tier, "max_output_tokens": max_tokens}
    try:
        res = market.submit_job(spec, {"txn_hex": txn_hex})
    except market.MarketError as e:
        console.print(f"[red]submit failed:[/] {e}", markup=False)
        raise typer.Exit(code=1)
    job_id = res.get("job_id")
    if res.get("payment_accepted") is False:
        console.print(f"[red]⚠ payment was not accepted:[/] {res.get('payment_status')}", markup=False)
    # 6) Record locally (there is no per-user list RPC).
    market.record_job({"job_id": job_id, "tier": res.get("accepted_tier") or resolved_tier,
                       "cost_animica": cost, "tx_hash": res.get("payment_tx_hash"),
                       "provider_id": res.get("provider_id")})
    if json_output:
        typer.echo(_json.dumps(res, indent=2))
        raise typer.Exit(0)
    console.print(f"[green]✔ submitted[/] job [bold]{job_id}[/] (tier {res.get('accepted_tier')}, "
                  f"paid {cost} ANM, tx {res.get('payment_tx_hash')})")
    console.print(f"  track: [bold]animica ai job result {job_id}[/]")


@job_app.command("status")
def job_status_cmd(
    job_id: str = typer.Argument(..., help="Job id."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Get a job's current state (aicf.jobStatus)."""
    from animica.ai import market
    try:
        st = market.job_status(job_id)
    except market.MarketError as e:
        console.print(f"[red]{e}[/]", markup=False)
        raise typer.Exit(code=1)
    typer.echo(_json.dumps(st, indent=2) if json_output else f"{job_id}: {st.get('state')}")


@job_app.command("result")
def job_result(
    job_id: str = typer.Argument(..., help="Job id."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Settle and print a completed job's result (aicf.settleJob)."""
    from animica.ai import market
    try:
        res = market.settle_job(job_id)
    except market.MarketError as e:
        console.print(f"[red]{e}[/]", markup=False)
        raise typer.Exit(code=1)
    if json_output:
        typer.echo(_json.dumps(res, indent=2))
        raise typer.Exit(0)
    if res.get("error"):
        console.print(f"[red]job {job_id} error:[/] {res['error']}", markup=False)
        raise typer.Exit(code=1)
    console.print(res.get("text") or f"[yellow]no text yet (state: {res.get('state')})[/]")


@job_app.command("list")
def job_list(json_output: bool = typer.Option(False, "--json")) -> None:
    """List jobs you've submitted from this machine (tracked locally)."""
    from animica.ai import market
    jobs = market.local_jobs()
    if json_output:
        typer.echo(_json.dumps(jobs, indent=2))
        raise typer.Exit(0)
    if not jobs:
        console.print("[yellow]no jobs submitted from this machine yet.[/] Try `animica ai job submit`.")
        raise typer.Exit(0)
    table = Table(title="animica ai jobs (local)")
    table.add_column("Job ID", style="bold")
    table.add_column("Tier")
    table.add_column("Cost (ANM)")
    table.add_column("Payment tx")
    for j in jobs[-30:]:
        table.add_row(str(j.get("job_id"))[:24], str(j.get("tier")),
                      str(j.get("cost_animica")), str(j.get("tx_hash") or "")[:20])
    console.print(table)


@provider_app.command("register")
def provider_register(
    address: str = typer.Option(None, "--address", help="Provider payout address (default: config payout_wallet)."),
    tier: list[str] = typer.Option(None, "--tier", help="Tier(s) to serve (repeatable)."),
    endpoint: str = typer.Option(None, "--endpoint", help="Optional direct http(s) endpoint."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Register this machine as an AICF provider (aicf.workerRegister)."""
    from . import app_config
    from animica.ai import market
    addr = address or app_config.get("payout_wallet", section="ai")
    if not addr:
        console.print("[red]✘ no provider address.[/] Pass --address or set one via `animica ai setup`.")
        raise typer.Exit(code=1)
    # Advertise detected hardware so the scheduler can route appropriately.
    hw: dict[str, Any] = {"cpu_cores": os.cpu_count() or 0}
    try:
        import torch  # type: ignore
        hw["cuda"] = bool(torch.cuda.is_available())
    except Exception:
        hw["cuda"] = False
    try:
        res = market.register_worker(addr, tiers=list(tier or []), hardware=hw, direct_endpoint=endpoint)
    except market.MarketError as e:
        console.print(f"[red]{e}[/]", markup=False)
        raise typer.Exit(code=1)
    if json_output:
        typer.echo(_json.dumps(res, indent=2))
        raise typer.Exit(0)
    console.print(f"[green]✔ registered[/] {res.get('address')} (tiers {res.get('tiers')})")
    console.print("  start serving jobs: [bold]animica ai provider start[/]")


@provider_app.command("status")
def provider_status(
    address: str = typer.Option(None, "--address", help="Provider address (default: config payout_wallet)."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Show this provider's registration + queue status (aicf.workerStatus)."""
    from . import app_config
    from animica.ai import market
    addr = address or app_config.get("payout_wallet", section="ai")
    if not addr:
        console.print("[red]✘ no provider address.[/] Pass --address or run `animica ai setup`.")
        raise typer.Exit(code=1)
    try:
        st = market.worker_status(addr)
    except market.MarketError as e:
        console.print(f"[red]{e}[/]", markup=False)
        raise typer.Exit(code=1)
    typer.echo(_json.dumps(st, indent=2) if json_output
               else f"registered: {st.get('registered')}  address: {st.get('address')}")


@provider_app.command("start")
def provider_start(
    address: str = typer.Option(None, "--address", help="Provider address (default: config payout_wallet)."),
) -> None:
    """Start the provider worker loop (delegates to `animica miner aicf-worker`)."""
    from . import app_config
    addr = address or app_config.get("payout_wallet", section="ai")
    if not addr:
        console.print("[red]✘ no provider address.[/] Pass --address or run `animica ai setup`.")
        raise typer.Exit(code=1)
    # The worker runtime already exists under `animica miner aicf-worker` (attached
    # from agent_runtime). Delegate rather than reimplement the claim/serve loop.
    import sys as _sys
    argv = [_sys.executable, "-m", "animica", "miner", "aicf-worker", "start", "--address", addr]
    console.print(f"[cyan]Starting AICF provider worker[/] for {addr} …")
    console.print(f"  (delegating to: {' '.join(argv[2:])})")
    os.execvp(_sys.executable, argv)


@app.command("earnings")
def earnings(
    address: str = typer.Option(None, "--address", help="Provider address (default: config payout_wallet)."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Show provider earnings (aicf.workerEarnings)."""
    from . import app_config
    from animica.ai import market
    addr = address or app_config.get("payout_wallet", section="ai")
    if not addr:
        console.print("[red]✘ no provider address.[/] Pass --address or run `animica ai setup`.")
        raise typer.Exit(code=1)
    try:
        e = market.worker_earnings(addr)
    except market.MarketError as exc:
        console.print(f"[red]{exc}[/]", markup=False)
        raise typer.Exit(code=1)
    if json_output:
        typer.echo(_json.dumps(e, indent=2))
        raise typer.Exit(0)
    console.print(f"[bold]earnings for {addr}:[/]")
    console.print_json(data=e)


@app.command("benchmark")
def benchmark(
    runs: int = typer.Option(3, "--runs", "-n", help="Number of generations to time."),
    model: str = typer.Option(None, "--model", "-m", help="Model override."),
    local: bool = typer.Option(False, "--local", help="Force the local Ollama runtime."),
    max_tokens: int = typer.Option(64, "--max-tokens", help="Tokens to generate per run."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Benchmark local inference throughput (real generations; no ANM spent)."""
    import time as _time
    from animica.ena.providers import ProviderError
    try:
        adapter, mp, key = _resolve_adapter(None, model, local)
    except Exception as e:  # noqa: BLE001
        console.print(f"[red]could not resolve a model:[/] {e}", markup=False)
        raise typer.Exit(code=2)
    prompt = "Write a short paragraph about distributed computing."
    lat: list[float] = []
    chars = 0
    for i in range(max(1, runs)):
        t0 = _time.time()
        try:
            out = adapter.generate(prompt, max_tokens=max_tokens, temperature=0.2)
        except ProviderError as e:
            console.print(f"[red]model error:[/] {e}", markup=False)
            raise typer.Exit(code=1)
        lat.append(_time.time() - t0)
        chars += len(out or "")
    avg = sum(lat) / len(lat)
    approx_tok = chars / 4
    tok_per_s = (approx_tok / sum(lat)) if sum(lat) > 0 else 0
    result = {"model": mp.model, "provider": mp.provider, "runs": len(lat),
              "avg_latency_s": round(avg, 3), "approx_tokens_per_s": round(tok_per_s, 1)}
    if json_output:
        typer.echo(_json.dumps(result, indent=2))
        raise typer.Exit(0)
    console.print(f"[bold]{mp.provider}/{mp.model}[/]: {len(lat)} runs, "
                  f"avg [bold]{avg:.2f}s[/], ~[bold]{tok_per_s:.0f} tok/s[/] (approx)")


app.add_typer(job_app, name="job")
app.add_typer(provider_app, name="provider")


# =========================================================================== #
# Embeddings + RAG (5.2.0 §6). `ai embed` returns vectors; `ai rag` indexes local
# files and answers questions grounded in them. Both reuse the gateway's ENA
# embedding-adapter routing (hashing works offline; ollama/openai when configured).
# =========================================================================== #
rag_app = typer.Typer(name="rag", help="Local retrieval-augmented generation.", no_args_is_help=True)


def _embed_texts(texts: list[str], model: str | None, provider: str | None):
    """Return (vectors, resolved_model). Lazy-imports the gateway's resolver."""
    from animica.ai.gateway import resolve_embedding_adapter
    adapter, key, resolved = resolve_embedding_adapter(model, provider)
    return adapter.embed(texts), resolved


@app.command("embed")
def embed(
    text: list[str] = typer.Argument(None, help="Text(s) to embed. Omit to read stdin."),
    model: str = typer.Option(None, "--model", "-m", help="Embedding model override."),
    provider: str = typer.Option(None, "--provider", help="Embedding provider key override."),
    json_output: bool = typer.Option(False, "--json", help="Emit vectors as JSON."),
) -> None:
    """Embed text into vectors via the configured embedding provider."""
    from animica.ena.providers import ProviderError
    import sys as _sys
    texts = list(text) if text else ([_sys.stdin.read().strip()] if not _sys.stdin.isatty() else [])
    texts = [t for t in texts if t]
    if not texts:
        console.print("[yellow]no input.[/] Pass text or pipe via stdin.")
        raise typer.Exit(code=1)
    try:
        vectors, resolved = _embed_texts(texts, model, provider)
    except ProviderError as e:
        console.print(f"[red]embedding failed:[/] {e}", markup=False)
        raise typer.Exit(code=1)
    except Exception as e:  # noqa: BLE001
        console.print(f"[red]could not embed:[/] {e}", markup=False)
        raise typer.Exit(code=2)
    if json_output:
        typer.echo(_json.dumps({"model": resolved,
                                "data": [{"index": i, "embedding": v} for i, v in enumerate(vectors)]},
                               indent=2))
        raise typer.Exit(0)
    for i, v in enumerate(vectors):
        console.print(f"[{i}] {resolved}: dim={len(v)}  [{', '.join(f'{x:.4f}' for x in v[:5])}, …]")


@rag_app.command("index")
def rag_index(
    paths: list[str] = typer.Argument(..., help="Files or directories to index."),
    name: str = typer.Option("default", "--name", help="Index name."),
    model: str = typer.Option(None, "--model", "-m", help="Embedding model override."),
    provider: str = typer.Option(None, "--provider", help="Embedding provider override."),
    chunk_size: int = typer.Option(800, "--chunk-size", help="Chunk size in characters."),
) -> None:
    """Chunk + embed local text files into a named RAG index."""
    from animica.ai.rag import RagStore, chunk_text
    from animica.ena.providers import ProviderError

    # Collect files (recurse directories for common text types).
    exts = {".txt", ".md", ".markdown", ".rst", ".py", ".json", ".yaml", ".yml", ".toml", ".csv"}
    files: list[Path] = []
    for p in paths:
        path = Path(p).expanduser()
        if path.is_dir():
            files += [f for f in path.rglob("*") if f.is_file() and f.suffix.lower() in exts]
        elif path.is_file():
            files.append(path)
        else:
            console.print(f"[yellow]skip (not found):[/] {p}")
    if not files:
        console.print("[red]no indexable files found.[/]")
        raise typer.Exit(code=1)

    store = RagStore(name).load()
    chunks: list[tuple[str, str]] = []  # (text, source)
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for c in chunk_text(text, size=chunk_size):
            chunks.append((c, str(f)))
    if not chunks:
        console.print("[red]no text to index.[/]")
        raise typer.Exit(code=1)

    try:
        vectors, resolved = _embed_texts([c for c, _ in chunks], model, provider)
    except ProviderError as e:
        console.print(f"[red]embedding failed:[/] {e}", markup=False)
        raise typer.Exit(code=1)
    store.model = resolved
    for (text_c, src), vec in zip(chunks, vectors):
        store.add(text_c, src, vec)
    path = store.save()
    console.print(f"[green]✔ indexed[/] {len(chunks)} chunks from {len(files)} file(s) "
                  f"into '{name}' (model {resolved}) → {path}")
    console.print(f"  ask: [bold]animica ai rag query \"...\" --name {name}[/]")


@rag_app.command("query")
def rag_query(
    question: str = typer.Argument(..., help="Question to answer from the index."),
    name: str = typer.Option("default", "--name", help="Index name."),
    k: int = typer.Option(4, "--k", help="Number of chunks to retrieve."),
    answer: bool = typer.Option(False, "--answer", help="Generate a grounded answer with the chat model."),
    model: str = typer.Option(None, "--model", "-m", help="Embedding/answer model override."),
    provider: str = typer.Option(None, "--provider", help="Provider override."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Retrieve the most relevant chunks (and optionally answer) from a RAG index."""
    from animica.ai.rag import RagStore
    from animica.ena.providers import ProviderError

    store = RagStore(name).load()
    if not store.items:
        console.print(f"[red]index '{name}' is empty or missing.[/] Run `animica ai rag index` first.")
        raise typer.Exit(code=1)
    try:
        qvec, _ = _embed_texts([question], model or store.model, provider)
    except Exception as e:  # noqa: BLE001
        console.print(f"[red]could not embed query:[/] {e}", markup=False)
        raise typer.Exit(code=1)
    hits = store.search(qvec[0], k)

    grounded = None
    if answer:
        context = "\n\n".join(f"[{i+1}] {h['text']}" for i, h in enumerate(hits))
        sys_prompt = ("Answer the question using ONLY the context below. Cite chunk "
                      "numbers like [1]. If the answer isn't in the context, say so.\n\n"
                      f"Context:\n{context}")
        try:
            adapter, mp, _ = _resolve_adapter(provider, model, False)
            grounded = adapter.generate(question, system=sys_prompt, max_tokens=512)
        except ProviderError as e:
            console.print(f"[yellow]retrieval ok, but answer failed:[/] {e}", markup=False)

    if json_output:
        typer.echo(_json.dumps({"question": question, "hits": hits, "answer": grounded}, indent=2))
        raise typer.Exit(0)
    if grounded:
        console.print(f"[bold magenta]answer ›[/] {grounded}\n")
    console.print(f"[dim]top {len(hits)} chunks from '{name}':[/]")
    for i, h in enumerate(hits):
        src = Path(h["source"]).name if h.get("source") else "?"
        console.print(f"  [{i+1}] [dim]{src} (score {h['score']:.3f})[/] "
                      f"{h['text'][:160].strip().replace(chr(10), ' ')}…")


@rag_app.command("list")
def rag_list(json_output: bool = typer.Option(False, "--json")) -> None:
    """List local RAG indexes."""
    from animica.ai.rag import RagStore
    stores = RagStore.list_stores()
    if json_output:
        typer.echo(_json.dumps({"indexes": stores}, indent=2))
        raise typer.Exit(0)
    if not stores:
        console.print("[yellow]no RAG indexes yet.[/] Build one: `animica ai rag index <files>`.")
        raise typer.Exit(0)
    for s in stores:
        n = len(RagStore(s).load().items)
        console.print(f"  • [bold]{s}[/] ({n} chunks)")


app.add_typer(rag_app, name="rag")


@app.command("balance")
def balance(
    address: str = typer.Option(None, "--address",
        help="Address to check (default: config payout_wallet, else your default wallet)."),
    watch: bool = typer.Option(False, "--watch", help="Refresh continuously until Ctrl-C."),
    interval: float = typer.Option(5.0, "--interval", help="Seconds between refreshes with --watch."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Show an ANM wallet balance (base units + ANM) and your configured spend cap."""
    import time as _time
    from . import app_config
    from animica.ai import market

    addr = address or app_config.get("payout_wallet", section="ai")
    if not addr:
        try:
            from animica.unified import resolve_address
            addr, _ = resolve_address(None, create=False)
        except Exception:
            addr = None
    if not addr:
        console.print("[red]✘ no wallet/address.[/] Pass --address, run `animica ai setup`, "
                      "or create one with `animica wallet new`.")
        raise typer.Exit(code=1)

    cap = app_config.get("max_spend_anm", section="ai")

    def _fetch() -> int:
        res = market.call("state.getBalance", [addr])
        if isinstance(res, str) and res.startswith("0x"):
            return int(res, 16)
        return int(res or 0)

    def _cap_str() -> str:
        if cap is None:
            return ""
        return "   spend cap: ask every time" if not cap else f"   spend cap: {cap} ANM"

    if not watch or json_output:
        try:
            base = _fetch()
        except market.MarketError as e:
            console.print(f"[red]{e}[/]", markup=False)
            raise typer.Exit(code=1)
        anm = base / 1_000_000_000
        if json_output:
            typer.echo(_json.dumps({"address": addr, "base_units": base, "anm": anm,
                                    "max_spend_anm": cap}, indent=2))
        else:
            console.print(f"[bold]{addr}[/]\n  {base:,} base units  (= {anm:,.9f} ANM){_cap_str()}")
        raise typer.Exit(0)

    # --watch: refresh until interrupted.
    console.print(f"[dim]watching {addr} (Ctrl-C to stop)…[/]")
    try:
        while True:
            try:
                base = _fetch()
                console.print(f"  {base:,} base units  (= {base / 1_000_000_000:,.9f} ANM)")
            except market.MarketError as e:
                console.print(f"[red]{e}[/]", markup=False)
            _time.sleep(max(1.0, interval))
    except KeyboardInterrupt:
        console.print("\nstopped.")


# ===========================================================================
# 7.1.1 — Verifiable Inference Engine: receipt verify / replay (fully offline)
# ===========================================================================

def _load_receipt(path: str) -> dict[str, Any]:
    import sys as _sys
    raw = _sys.stdin.read() if path == "-" else Path(path).expanduser().read_text(encoding="utf-8")
    obj = _json.loads(raw)
    # Accept a full chat response, a {'receipt': …} envelope, or a bare receipt.
    return obj.get("animica_receipt") or obj.get("receipt") or obj


@app.command("verify")
def verify(
    receipt_file: str = typer.Argument(..., help="Receipt JSON path (or '-' for stdin)."),
    json_output: bool = typer.Option(False, "--json", help="Emit the verdict as JSON."),
) -> None:
    """Verify a Proof-of-Inference receipt offline: recompute its content hash and
    check the ML-DSA-65 signature. Needs no node and no model."""
    from animica.ena.inference_receipt import validate_inference_receipt
    try:
        receipt = _load_receipt(receipt_file)
    except Exception as e:  # noqa: BLE001
        console.print(f"[red]could not read receipt: {e}[/]", markup=False)
        raise typer.Exit(code=1)
    v = validate_inference_receipt(receipt)
    if json_output:
        typer.echo(_json.dumps(v, indent=2))
        raise typer.Exit(0 if v["valid"] else 2)
    ok = "[green]✔[/]" if v["valid"] else "[red]✘[/]"
    console.print(f"{ok} receipt {receipt.get('receipt_hash','')[:16]}…  "
                  f"valid=[bold]{v['valid']}[/]")
    console.print(f"    hash_ok={v['hash_ok']}  signed={v['signed']}  signature_ok={v['signature_ok']}")
    console.print(f"    seed_applied={v['seed_applied']}  attestation={v['attestation_backend']}")
    if v.get("signer_address"):
        console.print(f"    signer={v['signer_address']}")
    raise typer.Exit(0 if v["valid"] else 2)


@app.command("replay")
def replay_cmd(
    receipt_file: str = typer.Argument(..., help="Receipt JSON path (or '-' for stdin)."),
    prompt: str = typer.Option(..., "--prompt", "-p", help="The original prompt (receipts are hash-only)."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Re-run a receipt's generation with its recorded seed and check the output
    hash. Reports 'verified' only for reproducible local backends, else 'best_effort'."""
    from animica.ai.replay import replay_receipt
    try:
        receipt = _load_receipt(receipt_file)
    except Exception as e:  # noqa: BLE001
        console.print(f"[red]could not read receipt: {e}[/]", markup=False)
        raise typer.Exit(code=1)
    res = replay_receipt(receipt, prompt)
    if json_output:
        typer.echo(_json.dumps(res, indent=2))
        raise typer.Exit(0 if res.get("match") else 2)
    tag = {"verified": "[green]", "best_effort": "[yellow]", "unsupported": "[red]"}.get(
        res["reproducible"], "[white]")
    console.print(f"reproducible: {tag}{res['reproducible']}[/]   match={res['match']}   "
                  f"prompt_ok={res['prompt_ok']}   backend={res['backend_class']}")
    raise typer.Exit(0 if res.get("match") else 2)


receipt_app = typer.Typer(name="receipt", help="Inspect + verify Proof-of-Inference receipts.",
                          no_args_is_help=True)
app.add_typer(receipt_app, name="receipt")


@receipt_app.command("show")
def receipt_show(receipt_file: str = typer.Argument(..., help="Receipt JSON path (or '-').")) -> None:
    """Pretty-print the salient fields of a receipt."""
    try:
        r = _load_receipt(receipt_file)
    except Exception as e:  # noqa: BLE001
        console.print(f"[red]could not read receipt: {e}[/]", markup=False)
        raise typer.Exit(code=1)
    for k in ("receipt_hash", "model_id", "provider_key", "prompt_hash", "output_hash",
              "tokens_in", "tokens_out", "signed", "signature_alg_name", "signer_address",
              "seed_applied", "attested", "attestation_backend", "beacon_round"):
        if k in r:
            console.print(f"  [bold]{k}[/]: {r[k]}")


@receipt_app.command("verify")
def receipt_verify(
    receipt_file: str = typer.Argument(..., help="Receipt JSON path (or '-')."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Alias of `animica ai verify`."""
    verify(receipt_file, json_output=json_output)
