"""Operator-friendly Stratum pool lifecycle commands."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

import httpx
import typer
from rich.console import Console

from animica.stratum_pool.config import load_config_from_env
from mining.template_block import (hash_candidate_header,
                                   header_sign_bytes_from_template_view)

from .service_runtime import (is_running, read_metadata, read_pid,
                              service_state, start_daemon, stop_daemon)

app = typer.Typer(help="Stratum pool lifecycle commands.")
console = Console()
POOL_DB_URL_ENV = "ANIMICA_MINING_POOL_DB_URL"
DEFAULT_POOL_DB_URL = "sqlite:///animica_pool.db"


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


def _resolve_pool_db_url(explicit: Optional[str] = None) -> str:
    candidate = explicit if explicit is not None else os.environ.get(POOL_DB_URL_ENV)
    if candidate is None:
        return DEFAULT_POOL_DB_URL
    value = str(candidate).strip()
    return value or DEFAULT_POOL_DB_URL


def _resolve_sqlite_db_path(db_url: str) -> Path:
    if not db_url.startswith("sqlite"):
        raise ValueError("only sqlite URLs are supported")
    # Keep path parsing aligned with PoolMetrics._init_db.
    path = db_url.replace("sqlite:///", "", 1)
    if path.startswith("//"):
        path = path[1:]
    if not path.strip():
        raise ValueError("database path cannot be empty")
    return Path(path).expanduser()


def _pool_db_candidates(db_path: Path) -> list[Path]:
    base = str(db_path)
    return [
        db_path,
        Path(f"{base}-wal"),
        Path(f"{base}-shm"),
        Path(f"{base}-journal"),
    ]


def _rpc_json_call(
    rpc_url: str,
    method: str,
    params: Any | None = None,
    *,
    timeout: float = 5.0,
) -> Any:
    payload: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        payload["params"] = params
    with httpx.Client(timeout=timeout) as client:
        response = client.post(rpc_url, json=payload)
        response.raise_for_status()
    body = response.json()
    if body.get("error"):
        error = body["error"]
        detail = error.get("data")
        raise RuntimeError(f"{error.get('message', 'rpc error')} ({detail})")
    return body.get("result")


def _api_json_get(api_url: str, path: str, *, timeout: float = 5.0) -> Any:
    with httpx.Client(timeout=timeout) as client:
        response = client.get(f"{api_url}{path}")
        response.raise_for_status()
    return response.json()


def _resolve_pool_address(explicit: Optional[str] = None) -> str:
    cfg = load_config_from_env()
    address = explicit or cfg.pool_address
    if not address:
        raise RuntimeError(
            "Pool payout address is not configured. Set ANIMICA_POOL_ADDRESS or pass --pool-address."
        )
    return address


def _template_probe(
    *,
    rpc_url: str,
    pool_address: str,
    timeout: float,
) -> dict[str, Any]:
    template = _rpc_json_call(
        rpc_url,
        "miner.getBlockTemplate",
        {"address": pool_address, "include_mempool": True},
        timeout=timeout,
    )
    if not isinstance(template, dict):
        raise RuntimeError("miner.getBlockTemplate returned an unexpected payload")
    header = template.get("header")
    if not isinstance(header, dict):
        raise RuntimeError("template is missing header")
    required = (
        "chainId",
        "height",
        "parentHash",
        "timestamp",
        "stateRoot",
        "txsRoot",
        "receiptsRoot",
        "proofsRoot",
        "daRoot",
        "mixSeed",
        "poiesPolicyRoot",
        "pqAlgPolicyRoot",
        "thetaMicro",
        "nonce",
        "extra",
    )
    missing = [field for field in required if field not in header]
    if missing:
        raise RuntimeError(f"template header is missing fields: {', '.join(missing)}")
    sign_bytes = header_sign_bytes_from_template_view(header)
    candidate_hash = hash_candidate_header(
        header,
        nonce=int(header.get("nonce", 0) or 0),
    )
    return {
        "template": template,
        "header": header,
        "template_id": template.get("templateId") or template.get("template_id"),
        "height": int(header.get("height") or header.get("number") or 0),
        "tx_count": len(template.get("txs") or []),
        "target": template.get("target"),
        "sign_bytes_len": len(sign_bytes),
        "candidate_hash": "0x" + candidate_hash.digest.hex(),
    }


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
        console.print("[dim]Start with: animica pool up --daemon[/dim]")


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
    console.print(f"Pool mode: {cfg.pool_mode}")
    console.print(f"Difficulty: {cfg.min_difficulty} -> {cfg.max_difficulty}")


@app.command("show-config")
def show_config() -> None:
    """Alias for `animica stratum config`."""
    config()


@app.command("reset")
def reset(
    force: bool = typer.Option(
        False,
        "--force",
        help="Actually delete pool DB files (default is dry-run)",
    ),
    stop: bool = typer.Option(
        False,
        "--stop",
        help="Stop managed pool before deleting DB files if it is running",
    ),
    db_url: Optional[str] = typer.Option(
        None,
        "--db-url",
        help=f"Override DB URL (default: {POOL_DB_URL_ENV} or {DEFAULT_POOL_DB_URL})",
    ),
) -> None:
    """Reset Stratum pool accounting DB files (shares, blocks, payouts)."""
    resolved_db_url = _resolve_pool_db_url(db_url)
    try:
        db_path = _resolve_sqlite_db_path(resolved_db_url)
    except ValueError as exc:
        console.print(f"[red]Unsupported DB URL[/red] {resolved_db_url} ({exc})")
        raise typer.Exit(2) from exc

    files = _pool_db_candidates(db_path)
    state = _service_state()
    pid = read_pid(state)
    running = is_running(pid)

    console.print("[bold]Pool DB Reset Plan[/bold]\n")
    console.print(f"DB URL: {resolved_db_url}")
    console.print(f"DB path: {db_path}")
    if running:
        console.print(f"Pool process: [yellow]running[/yellow] (pid {pid})")
    else:
        console.print("Pool process: [green]stopped[/green]")
    for candidate in files:
        status = "exists" if candidate.exists() else "missing"
        console.print(f" - [{status}] {candidate}")

    if not force:
        console.print(
            "[yellow]Dry-run only.[/yellow] Re-run with [bold]--force[/bold] to delete these files."
        )
        return

    if running:
        if not stop:
            console.print(
                "[red]Refusing to reset while pool is running.[/red] Use [bold]animica pool down[/bold] or pass [bold]--stop[/bold]."
            )
            raise typer.Exit(1)
        stop_daemon(state)
        console.print(f"[green]✓ Stopped Stratum pool (pid {pid})[/green]")

    removed: list[Path] = []
    for candidate in files:
        if not candidate.exists():
            continue
        if candidate.is_dir():
            console.print(f"[red]Refusing to delete directory[/red] {candidate}")
            raise typer.Exit(1)
        candidate.unlink()
        removed.append(candidate)

    if not removed:
        console.print("[green]✓ Pool DB already clean[/green] (no files removed)")
        return

    for candidate in removed:
        console.print(f"[green]removed[/green] {candidate}")
    console.print(f"[green]✓ Pool DB reset complete[/green] ({len(removed)} files removed)")


@app.command("init")
def init(
    path: Path = typer.Option(
        Path("animica-pool.env"),
        "--path",
        help="Where to write a starter pool env file",
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing file"),
) -> None:
    """Write a starter env file for local pool operation."""
    cfg = load_config_from_env()
    if path.exists() and not force:
        console.print(
            f"[yellow]{path} already exists[/yellow] (use [bold]--force[/bold] to overwrite)"
        )
        raise typer.Exit(1)

    payload = [
        "# Animica Stratum pool starter configuration",
        f"ANIMICA_RPC_URL={cfg.rpc_url}",
        f"ANIMICA_CHAIN_ID={cfg.chain_id}",
        f"ANIMICA_NETWORK={cfg.network}",
        f"ANIMICA_POOL_ADDRESS={cfg.pool_address or 'anim1...'}",
        f"ANIMICA_STRATUM_HOST={cfg.host}",
        f"ANIMICA_STRATUM_PORT={cfg.port}",
        f"ANIMICA_STRATUM_API_HOST={cfg.api_host}",
        f"ANIMICA_STRATUM_API_PORT={cfg.api_port}",
        f"ANIMICA_MINING_POOL_DB_URL={cfg.db_url}",
        f"ANIMICA_STRATUM_MIN_DIFFICULTY={cfg.min_difficulty}",
        f"ANIMICA_STRATUM_MAX_DIFFICULTY={cfg.max_difficulty}",
        f"ANIMICA_STRATUM_POLL_INTERVAL={cfg.poll_interval}",
        f"ANIMICA_POOL_PROFILE={cfg.profile}",
        f"ANIMICA_POOL_MODE={cfg.pool_mode}",
    ]
    path.write_text("\n".join(payload) + "\n", encoding="utf-8")
    console.print(f"[green]✓ Wrote pool env template[/green] {path}")


@app.command("list-workers")
def list_workers() -> None:
    """List workers currently known to the managed pool API."""
    metadata = read_metadata(_service_state())
    api_url = _api_url(metadata)
    try:
        payload = _api_json_get(api_url, "/miners")
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Failed to query pool API[/red] {api_url} ({exc})")
        raise typer.Exit(1) from exc

    items = payload.get("items") if isinstance(payload, dict) else None
    if not items:
        console.print("No workers reported by the pool.")
        return

    console.print("[bold]Pool Workers[/bold]\n")
    for item in items:
        worker_id = item.get("worker_id") or item.get("worker_name") or "unknown"
        console.print(
            f"{worker_id} | "
            f"addr={item.get('address') or ''} | "
            f"accepted={item.get('shares_accepted', 0)} | "
            f"rejected={item.get('shares_rejected', 0)} | "
            f"blocks={item.get('blocks_found', 0)} | "
            f"hashrate_1m={item.get('hashrate_1m', 0)}"
        )


@app.command("test-job")
def test_job(
    pool_address: Optional[str] = typer.Option(
        None,
        "--pool-address",
        help="Override the configured pool payout address",
    ),
    rpc_url: Optional[str] = typer.Option(None, "--rpc-url", help="Override node RPC URL"),
    timeout: float = typer.Option(5.0, "--timeout", help="RPC timeout in seconds"),
) -> None:
    """Fetch and validate a real mining template from the node RPC."""
    cfg = load_config_from_env()
    resolved_rpc_url = rpc_url or cfg.rpc_url
    resolved_address = _resolve_pool_address(pool_address)
    try:
        probe = _template_probe(
            rpc_url=resolved_rpc_url,
            pool_address=resolved_address,
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Template probe failed[/red] ({exc})")
        raise typer.Exit(1) from exc

    console.print("[bold]Template Probe[/bold]\n")
    console.print(f"RPC URL: {resolved_rpc_url}")
    console.print(f"Pool address: {resolved_address}")
    console.print(f"Template ID: {probe['template_id'] or '(none)'}")
    console.print(f"Height: {probe['height']}")
    console.print(f"Tx count: {probe['tx_count']}")
    console.print(f"Target: {probe['target']}")
    console.print(f"Derived signBytes length: {probe['sign_bytes_len']} bytes")
    console.print(f"Candidate hash (nonce={probe['header'].get('nonce', 0)}): {probe['candidate_hash']}")


@app.command("doctor")
def doctor(
    pool_address: Optional[str] = typer.Option(
        None,
        "--pool-address",
        help="Override the configured pool payout address",
    ),
    timeout: float = typer.Option(5.0, "--timeout", help="HTTP/RPC timeout in seconds"),
) -> None:
    """Run pool/operator diagnostics against the configured node and managed pool."""
    cfg = load_config_from_env()
    state = _service_state()
    metadata = read_metadata(state)
    pid = read_pid(state)
    api_url = _api_url(metadata)
    checks: list[tuple[str, bool, str]] = []

    address_ok = False
    try:
        resolved_address = _resolve_pool_address(pool_address)
        address_ok = True
        checks.append(("pool_address", True, resolved_address))
    except Exception as exc:  # noqa: BLE001
        resolved_address = ""
        checks.append(("pool_address", False, str(exc)))

    checks.append(
        (
            "pool_process",
            is_running(pid),
            f"pid={pid}" if is_running(pid) else "managed pool is not running",
        )
    )

    try:
        head = _rpc_json_call(cfg.rpc_url, "chain.getHead", timeout=timeout)
        height = int((head or {}).get("height", (head or {}).get("number", 0)) or 0)
        head_hash = (head or {}).get("hash")
        checks.append(("node_rpc", True, f"height={height} hash={head_hash}"))
    except Exception as exc:  # noqa: BLE001
        checks.append(("node_rpc", False, str(exc)))

    if address_ok:
        try:
            probe = _template_probe(
                rpc_url=cfg.rpc_url,
                pool_address=resolved_address,
                timeout=timeout,
            )
            checks.append(
                (
                    "template",
                    True,
                    f"height={probe['height']} txs={probe['tx_count']} target={probe['target']}",
                )
            )
        except Exception as exc:  # noqa: BLE001
            checks.append(("template", False, str(exc)))
    else:
        checks.append(("template", False, "skipped: no usable pool payout address"))

    if is_running(pid):
        try:
            health = _api_json_get(api_url, "/healthz", timeout=timeout)
            checks.append(("pool_api", True, f"status={health.get('status', 'ok')}"))
        except Exception as exc:  # noqa: BLE001
            checks.append(("pool_api", False, str(exc)))

        try:
            summary = _api_json_get(api_url, "/summary", timeout=timeout)
            checks.append(
                (
                    "pool_summary",
                    True,
                    f"workers={summary.get('num_workers', 0)} blocks={summary.get('blocks_found_total', 0)} height={summary.get('height', 0)}",
                )
            )
        except Exception as exc:  # noqa: BLE001
            checks.append(("pool_summary", False, str(exc)))

    console.print("[bold]Stratum Doctor[/bold]\n")
    failures = 0
    for name, ok, detail in checks:
        status = "[green]PASS[/green]" if ok else "[red]FAIL[/red]"
        if not ok:
            failures += 1
        console.print(f"{status} {name}: {detail}")

    if failures:
        raise typer.Exit(1)
