from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlencode, urlsplit

from fastapi import Request

from .config import PoolConfig

PLACEHOLDER_ADDRESS = "YOUR_ANIMICA_ADDRESS"
DEFAULT_THREADS = 0
DEFAULT_SCAN_WINDOW = 200_000
DEFAULT_VERSION = os.getenv("ANIMICA_MINER_BUNDLE_VERSION", "0.1.2")
WILDCARD_HOSTS = {"", "0.0.0.0", "::", "[::]"}
PRIMARY_SITE_HOSTS = {"animica.org", "www.animica.org"}
STAGED_SITE_HOSTS = {"dev.animica.org", "staging.animica.org", "preview.animica.org"}


def _read_env(name: str) -> Optional[str]:
    value = os.getenv(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _env_bool(name: str, default: bool = False) -> bool:
    value = _read_env(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _env_int(name: str) -> Optional[int]:
    value = _read_env(name)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _env_float(name: str) -> Optional[float]:
    value = _read_env(name)
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _is_publicly_routable_host(host: str) -> bool:
    normalized = host.strip().lower()
    return normalized not in {"", "localhost", "127.0.0.1", "::1"}


def _public_pool_alias_for_request_host(host: Optional[str]) -> Optional[str]:
    if not host:
        return None
    normalized = host.strip().lower()
    if normalized in PRIMARY_SITE_HOSTS:
        return "pool.animica.org"
    if normalized in STAGED_SITE_HOSTS:
        return f"pool.{normalized}"
    return None


def _sanitize_worker_name(value: Optional[str]) -> str:
    raw = (value or "").strip()
    if not raw:
        raw = "animica-cpu"
    clean = re.sub(r"[^a-zA-Z0-9._-]+", "-", raw).strip(".-_")
    return clean or "animica-cpu"


def normalize_threads(value: Optional[int]) -> int:
    if value is None or value <= 0:
        return DEFAULT_THREADS
    return max(1, min(int(value), 256))


def normalize_address(value: Optional[str]) -> str:
    raw = (value or "").strip()
    return raw or PLACEHOLDER_ADDRESS


def _pool_mode_description(mode: Optional[str]) -> str:
    normalized = str(mode or "").strip().lower()
    if normalized == "solo":
        return "only accepted full blocks are credited to the submitting miner"
    if normalized == "both":
        return (
            "parallel PPS and SOLO accounting; miners select per connection via --pool-mode pps|solo"
        )
    return "accepted shares earn deterministic per-share credit"


def _parse_host_port(raw: str) -> tuple[Optional[str], Optional[int]]:
    candidate = raw.strip()
    if not candidate:
        return None, None
    parsed = urlsplit(candidate if "://" in candidate else f"//{candidate}")
    host = parsed.hostname
    try:
        port = parsed.port
    except ValueError:
        port = None
    return host, port


def _parse_host_port_and_scheme(raw: str) -> tuple[Optional[str], Optional[int], Optional[str]]:
    candidate = raw.strip()
    if not candidate:
        return None, None, None
    parsed = urlsplit(candidate if "://" in candidate else f"//{candidate}")
    host = parsed.hostname
    try:
        port = parsed.port
    except ValueError:
        port = None
    return host, port, parsed.scheme or None


def _request_host(request: Optional[Request]) -> tuple[Optional[str], Optional[int]]:
    if request is None:
        return None, None
    forwarded = request.headers.get("x-forwarded-host")
    raw = forwarded.split(",")[0].strip() if forwarded else request.headers.get("host", "")
    if not raw:
        raw = request.url.netloc
    host, port = _parse_host_port(raw)
    if host in WILDCARD_HOSTS:
        return None, None
    return host, port


def _request_scheme(request: Optional[Request]) -> Optional[str]:
    if request is None:
        return None
    forwarded = request.headers.get("x-forwarded-proto")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.url.scheme


def _join_url(base: Optional[str], path: str) -> Optional[str]:
    if not base:
        return None
    return f"{base.rstrip('/')}/{path.lstrip('/')}"


def _append_query(url: str, params: dict[str, Any]) -> str:
    filtered = {k: v for k, v in params.items() if v not in (None, "", 0)}
    if not filtered:
        return url
    return f"{url}?{urlencode(filtered)}"


@dataclass(frozen=True)
class ResolvedMiningConfig:
    network: str
    chain_id: int
    pool_enabled: bool
    bind_host: str
    bind_port: int
    public_host: str
    public_port: int
    public_scheme: str
    tls_enabled: bool
    host_source: str
    api_base_url: str
    profile: str
    pool_mode: str
    algorithm: str
    device_type: str
    fee_percent: Optional[float]
    payout_minimum: Optional[str]
    payout_interval_seconds: float
    payout_min_amount: int
    download_base_url: Optional[str]
    warnings: tuple[str, ...]

    @property
    def stratum_url(self) -> str:
        return f"{self.public_scheme}://{self.public_host}:{self.public_port}"


@dataclass(frozen=True)
class BundleInput:
    address: str
    worker: str
    threads: int
    scan_window: int = DEFAULT_SCAN_WINDOW
    log_level: str = "INFO"


def resolve_public_mining_config(
    config: PoolConfig,
    *,
    request: Optional[Request] = None,
) -> ResolvedMiningConfig:
    warnings: list[str] = []
    request_host, _ = _request_host(request)
    request_scheme = _request_scheme(request)

    public_url = _read_env("ANIMICA_PUBLIC_STRATUM_URL")
    explicit_host = _read_env("ANIMICA_PUBLIC_STRATUM_HOST")
    explicit_domain = _read_env("ANIMICA_PUBLIC_DOMAIN")
    explicit_port = _env_int("ANIMICA_PUBLIC_STRATUM_PORT")

    public_host: Optional[str] = None
    public_port = explicit_port or config.port
    public_scheme = _read_env("ANIMICA_PUBLIC_STRATUM_SCHEME") or "stratum+tcp"
    host_source = "bind_host"

    if public_url:
        host, port, scheme = _parse_host_port_and_scheme(public_url)
        if host:
            public_host = host
            host_source = "public_stratum_url"
        if port:
            public_port = port
        if scheme:
            public_scheme = scheme
    elif explicit_host:
        host, port, scheme = _parse_host_port_and_scheme(explicit_host)
        if host:
            public_host = host
            host_source = "public_stratum_host"
        if port:
            public_port = port
        if scheme:
            public_scheme = scheme
    elif explicit_domain:
        public_host = explicit_domain
        host_source = "public_domain"
    elif request_host:
        site_pool_alias = _public_pool_alias_for_request_host(request_host)
        if site_pool_alias:
            public_host = site_pool_alias
            host_source = "request_host_site_alias"
        else:
            public_host = request_host
            host_source = "request_host"
    elif config.host not in WILDCARD_HOSTS:
        public_host = config.host
        host_source = "stratum_bind"
    else:
        rpc_host, _ = _parse_host_port(config.rpc_url)
        public_host = rpc_host or "127.0.0.1"
        host_source = "rpc_host_fallback"

    if public_host in WILDCARD_HOSTS:
        public_host = "127.0.0.1"
        host_source = "localhost_fallback"

    tls_enabled = _env_bool(
        "ANIMICA_PUBLIC_STRATUM_TLS_ENABLED",
        default=_env_bool("ANIMICA_STRATUM_TLS_ENABLED", default=False),
    )
    if tls_enabled and public_scheme == "stratum+tcp":
        public_scheme = "stratum+tls"

    api_base_url = _read_env("ANIMICA_PUBLIC_POOL_API_URL")
    if not api_base_url:
        if request is not None:
            api_base_url = str(request.base_url).rstrip("/")
        else:
            scheme = "https" if request_scheme == "https" else "http"
            api_base_url = f"{scheme}://{config.api_host}:{config.api_port}"

    download_base_url = _read_env("ANIMICA_MINING_DOWNLOAD_BASE_URL") or api_base_url

    pool_enabled = _env_bool("ANIMICA_POOL_ENABLED", default=True)
    pool_mode = (
        _read_env("ANIMICA_POOL_MODE")
        or (config.pool_mode if getattr(config, "pool_mode", None) else None)
        or ("solo" if not pool_enabled else "pps")
    ).strip().lower()
    if pool_mode not in {"pps", "solo", "both"}:
        pool_mode = "pps"
    fee_percent = _env_float("ANIMICA_POOL_FEE_PERCENT")
    payout_minimum = _read_env("ANIMICA_POOL_PAYOUT_MINIMUM")
    payout_interval_seconds = max(
        0.0, float(getattr(config, "payout_interval_seconds", 0.0) or 0.0)
    )
    payout_min_amount = max(1, int(getattr(config, "payout_min_amount", 1) or 1))

    algorithm = "Animica HashShare"
    device_type = "CPU miner"
    if config.profile.startswith("asic"):
        algorithm = "SHA-256 bridge"
        device_type = "ASIC / SHA-256 miner"
        warnings.append(
            "The active pool profile is ASIC/SHA-256. The bundled CPU miner targets hashshare pools."
        )

    lowered_network = (config.network or f"chain-{config.chain_id}").lower()
    if "dev" in lowered_network:
        warnings.append("Devnet pool detected. Rewards and balances are not mainnet assets.")
    elif "test" in lowered_network:
        warnings.append("Testnet pool detected. Use a testnet payout address, not a mainnet wallet.")

    if not pool_enabled:
        warnings.append("Mining portal is enabled, but pool traffic is marked disabled by configuration.")

    if not _is_publicly_routable_host(public_host):
        warnings.append(
            "The resolved stratum host is local-only. Miners on other machines will need a public host override."
        )

    return ResolvedMiningConfig(
        network=config.network or f"chain-{config.chain_id}",
        chain_id=config.chain_id,
        pool_enabled=pool_enabled,
        bind_host=config.host,
        bind_port=config.port,
        public_host=public_host,
        public_port=public_port,
        public_scheme=public_scheme,
        tls_enabled=tls_enabled,
        host_source=host_source,
        api_base_url=api_base_url.rstrip("/"),
        profile=config.profile,
        pool_mode=pool_mode,
        algorithm=algorithm,
        device_type=device_type,
        fee_percent=fee_percent,
        payout_minimum=payout_minimum,
        payout_interval_seconds=payout_interval_seconds,
        payout_min_amount=payout_min_amount,
        download_base_url=download_base_url.rstrip("/") if download_base_url else None,
        warnings=tuple(warnings),
    )


def build_bundle_input(
    *,
    address: Optional[str] = None,
    worker: Optional[str] = None,
    threads: Optional[int] = None,
    scan_window: Optional[int] = None,
    log_level: Optional[str] = None,
) -> BundleInput:
    return BundleInput(
        address=normalize_address(address),
        worker=_sanitize_worker_name(worker),
        threads=normalize_threads(threads),
        scan_window=max(50_000, int(scan_window or DEFAULT_SCAN_WINDOW)),
        log_level=(log_level or "INFO").upper(),
    )


def build_config_document(resolved: ResolvedMiningConfig, bundle: BundleInput) -> str:
    lines = [
        "{",
        f'  "pool_url": "{resolved.stratum_url}",',
        f'  "host": "{resolved.public_host}",',
        f'  "port": {resolved.public_port},',
        f'  "scheme": "{resolved.public_scheme}",',
        f'  "tls": {"true" if resolved.tls_enabled else "false"},',
        f'  "address": "{bundle.address}",',
        f'  "worker": "{bundle.worker}",',
    ]
    if bundle.threads > 0:
        lines.append(f'  "threads": {bundle.threads},')
    lines.extend(
        [
            f'  "scan_window": {bundle.scan_window},',
            f'  "api_base_url": "{resolved.api_base_url}",',
            f'  "pool_mode": "{resolved.pool_mode}",',
            '  "entrypoint": "animica-miner",',
            '  "stats_interval_sec": 20,',
            f'  "log_level": "{bundle.log_level}"',
            "}",
        ]
    )
    return "\n".join(lines) + "\n"


def build_manual_commands(resolved: ResolvedMiningConfig, bundle: BundleInput) -> dict[str, str]:
    quoted_address = bundle.address
    quoted_worker = bundle.worker
    common_parts = [
        f"--pool-url {resolved.stratum_url}",
        f"--address {quoted_address}",
        f"--worker {quoted_worker}",
    ]
    if bundle.threads > 0:
        common_parts.append(f"--threads {bundle.threads}")
    common_parts.append(f"--pool-mode {resolved.pool_mode}")
    common_args = " ".join(common_parts)
    return {
        "windows": f"animica-miner.exe {common_args}",
        "macos": f"./animica-miner {common_args}",
        "linux": f"./animica-miner {common_args}",
    }


def build_launcher_script(
    platform: str,
    resolved: ResolvedMiningConfig,
    *,
    config_name: str = "animica-miner.config.json",
) -> str:
    """Bootstrap launcher: installs/updates the `animica` package and runs
    `animica up` — the UNIFIED fleet (SHA3 PoW mining + ENA useful-work + GPU
    train/serve + Studio serverless functions), all bound to one ANM address,
    with the native fast-hash path (animica-fastpow) when a wheel is available.

    Falls back to the bundled standalone PoW miner (animica_cpu_miner.py) only
    when pip / network is unavailable.
    """
    if platform == "windows":
        return (
            "@echo off\r\n"
            "setlocal enabledelayedexpansion\r\n"
            "cd /d %~dp0\r\n"
            "title Animica Unified Miner\r\n"
            "set CONFIG=animica-miner.config.json\r\n"
            "set PY=\r\n"
            "where py >nul 2>nul && set PY=py -3\r\n"
            "if not defined PY ( where python >nul 2>nul && set PY=python )\r\n"
            "if not defined PY (\r\n"
            "  echo Python 3.10+ is required. Install it from https://python.org and re-run.\r\n"
            "  pause & exit /b 1\r\n"
            ")\r\n"
            "echo [animica] Installing/updating the miner (first run can take a few minutes)...\r\n"
            "%PY% -m pip install --upgrade pip >nul 2>nul\r\n"
            '%PY% -m pip install --upgrade "animica[all]"\r\n'
            "if errorlevel 1 (\r\n"
            "  echo [animica] Full install failed; running the bundled CPU miner instead.\r\n"
            "  %PY% animica_cpu_miner.py --config %CONFIG%\r\n"
            "  pause & exit /b %ERRORLEVEL%\r\n"
            ")\r\n"
            "%PY% -m pip install --upgrade animica-fastpow >nul 2>nul\r\n"
            "set ADDR=\r\n"
            "for /f \"delims=\" %%a in ('%PY% -c \"import json;print((json.load(open('animica-miner.config.json')) or {}).get('address') or '')\" 2^>nul') do set ADDR=%%a\r\n"
            "echo [animica] Starting the unified miner (PoW + useful-work + GPU train/serve + Studio). Close this window to stop.\r\n"
            "if defined ADDR ( %PY% -m animica.cli.main up --address %ADDR% ) else ( %PY% -m animica.cli.main up )\r\n"
            "set EXIT_CODE=%ERRORLEVEL%\r\n"
            "echo.\r\n"
            "if not %EXIT_CODE%==0 echo Miner exited with status %EXIT_CODE%.\r\n"
            "pause\r\n"
            "exit /b %EXIT_CODE%\r\n"
        )

    # macOS (.command) and Linux (.sh) share the same bootstrap body.
    tail = (
        'read -r -p "Press Return to close this window."\n' if platform == "macos" else ""
    )
    shebang = "#!/bin/bash\n" if platform == "macos" else "#!/usr/bin/env bash\n"
    return (
        shebang
        + "set -u\n"
        'SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"\n'
        'cd "$SCRIPT_DIR"\n'
        'PY="$(command -v python3 || command -v python || true)"\n'
        'if [ -z "$PY" ]; then echo "Python 3.10+ is required. Install it and re-run."; ' + tail + "exit 1; fi\n"
        'echo "[animica] Installing/updating the miner (first run can take a few minutes)..."\n'
        '"$PY" -m pip install --user --upgrade pip >/dev/null 2>&1 || true\n'
        'if ! "$PY" -m pip install --user --upgrade "animica[all]"; then\n'
        '  echo "[animica] Full install failed; running the bundled CPU miner instead."\n'
        f'  exec "$PY" animica_cpu_miner.py --config "{config_name}"\n'
        "fi\n"
        '"$PY" -m pip install --user --upgrade animica-fastpow >/dev/null 2>&1 || true\n'
        'ADDR="$("$PY" -c "import json;print((json.load(open(\'animica-miner.config.json\')) or {}).get(\'address\') or \'\')" 2>/dev/null || true)"\n'
        'echo "[animica] Starting the unified miner (PoW + useful-work + GPU train/serve + Studio). Ctrl-C to stop."\n'
        'exec "$PY" -m animica.cli.main up ${ADDR:+--address "$ADDR"}\n'
    )


def build_bundle_readme(
    resolved: ResolvedMiningConfig,
    bundle: BundleInput,
    *,
    version: str = DEFAULT_VERSION,
) -> str:
    warnings = "\n".join(f"- {warning}" for warning in resolved.warnings) or "- None"
    return f"""# Animica CPU Miner Starter Bundle

Version: {version}
Network: {resolved.network}
Pool profile: {resolved.profile}
Pool mode: {resolved.pool_mode.upper()}
Algorithm: {resolved.algorithm}
Recommended device: {resolved.device_type}
Detected endpoint: {resolved.stratum_url}
Configured worker: {bundle.worker}

## Quick start

1. Put your Animica payout address into `animica-miner.config.json`.
2. Keep the detected pool host and port as-is unless your operator told you otherwise.
3. Run the included launcher for your platform:
   - Windows: `start_mining.bat`
   - macOS: `start_mining.command`
   - Ubuntu/Linux: `start_mining.sh`
4. The launcher opens a terminal, prints the target pool, and starts `animica-miner`.

## Manual command

Windows:
`{build_manual_commands(resolved, bundle)["windows"]}`

macOS:
`{build_manual_commands(resolved, bundle)["macos"]}`

Linux:
`{build_manual_commands(resolved, bundle)["linux"]}`

## Payout address

Use an address that matches the active network. The starter config ships with `{bundle.address}`.

## Worker naming

Worker names are informational. This bundle uses `{bundle.worker}` by default. Good patterns:
- `office-cpu`
- `mac-mini-01`
- `ubuntu-rig-a`

## Pool notes

- Mode: {resolved.pool_mode.upper()} ({_pool_mode_description(resolved.pool_mode)})
- Fee: {resolved.fee_percent if resolved.fee_percent is not None else "not published"}%
- Payout minimum: {resolved.payout_minimum or "not published"}
- Payout cadence: {f"every {int(resolved.payout_interval_seconds)} seconds" if resolved.payout_interval_seconds > 0 else "manual / disabled"}
- Host source: {resolved.host_source}

## Warnings

{warnings}

## Troubleshooting

- Bundles prefer `animica-miner` executable. If it is missing, the launcher falls back to `animica_cpu_miner.py` + Python 3.10+.
- If the miner cannot connect, confirm that `{resolved.public_host}:{resolved.public_port}` is reachable from this machine.
- If you are mining from another computer and the host resolves to `localhost` or `127.0.0.1`, ask the operator to set `ANIMICA_PUBLIC_STRATUM_HOST`.
- Personalized bundles can be re-generated from the mining portal with your payout address prefilled.
"""


class MiningPortalService:
    def __init__(self, config: PoolConfig, metrics: Any) -> None:
        self._config = config
        self._metrics = metrics

    def resolve(self, request: Optional[Request] = None) -> ResolvedMiningConfig:
        return resolve_public_mining_config(self._config, request=request)

    def status_payload(self, request: Optional[Request] = None) -> dict[str, Any]:
        resolved = self.resolve(request)
        summary = self._metrics.pool_summary()
        health = self._metrics.health()
        accounting = self._metrics.accounting_summary()
        payout_status = self._metrics.payout_status()
        return {
            "online": bool(resolved.pool_enabled and health.get("status") == "ok"),
            "health": health,
            "network": resolved.network,
            "chain_id": resolved.chain_id,
            "profile": resolved.profile,
            "pool_mode": resolved.pool_mode,
            "algorithm": resolved.algorithm,
            "device_type": resolved.device_type,
            "pool_hashrate": summary.get("pool_hashrate", 0),
            "hashrate_1m": summary.get("hashrate_1m", 0),
            "hashrate_15m": summary.get("hashrate_15m", 0),
            "hashrate_1h": summary.get("hashrate_1h", 0),
            "blocks_found_total": summary.get("blocks_found_total", 0),
            "miners": summary.get("num_miners", 0),
            "workers": summary.get("num_workers", 0),
            "height": summary.get("height", 0),
            "latest_block": summary.get("latest_block"),
            "stratum_endpoint": resolved.stratum_url,
            "payout_interval_seconds": summary.get(
                "payout_interval_seconds", payout_status.get("payout_interval_seconds")
            ),
            "payout_min_amount": summary.get(
                "payout_min_amount", payout_status.get("payout_min_amount")
            ),
            "payouts_enabled": summary.get(
                "payouts_enabled", payout_status.get("payouts_enabled")
            ),
            "next_payout_at": summary.get("next_payout_at", payout_status.get("next_payout_at")),
            "payout_countdown_seconds": summary.get(
                "payout_countdown_seconds", payout_status.get("payout_countdown_seconds")
            ),
            "last_payout_at": summary.get("last_payout_at", payout_status.get("last_payout_at")),
            "last_payout_count": summary.get(
                "last_payout_count", payout_status.get("last_payout_count")
            ),
            "last_payout_error": summary.get(
                "last_payout_error", payout_status.get("last_payout_error")
            ),
            "warnings": list(resolved.warnings),
            "last_update": summary.get("last_update"),
            "accounting": accounting,
        }

    def config_payload(self, request: Request) -> dict[str, Any]:
        resolved = self.resolve(request)
        defaults = build_bundle_input()
        commands = build_manual_commands(resolved, defaults)
        mode_examples = {
            "pps": {
                platform: cmd.replace(
                    f"--pool-mode {resolved.pool_mode}", "--pool-mode pps"
                )
                for platform, cmd in commands.items()
            },
            "solo": {
                platform: cmd.replace(
                    f"--pool-mode {resolved.pool_mode}", "--pool-mode solo"
                )
                for platform, cmd in commands.items()
            },
        }
        mode_summary = {
            "pps": "Accepted shares earn deterministic per-share credit.",
            "solo": "Only accepted full blocks are credited to the submitting miner.",
            "both": "Pool accepts both PPS and SOLO workers in parallel. Set miner `--pool-mode` to `pps` or `solo`.",
        }
        downloads_url = str(request.url_for("mining_downloads_manifest"))
        status_url = str(request.url_for("mining_status"))
        generate_url = str(request.url_for("mining_generate"))
        return {
            "network": resolved.network,
            "chain_id": resolved.chain_id,
            "pool_enabled": resolved.pool_enabled,
            "profile": resolved.profile,
            "pool_mode": resolved.pool_mode,
            "algorithm": resolved.algorithm,
            "device_type": resolved.device_type,
            "stratum_host": resolved.public_host,
            "stratum_port": resolved.public_port,
            "stratum_scheme": resolved.public_scheme,
            "stratum_url": resolved.stratum_url,
            "bind_host": resolved.bind_host,
            "bind_port": resolved.bind_port,
            "host_source": resolved.host_source,
            "tls_enabled": resolved.tls_enabled,
            "api_base_url": resolved.api_base_url,
            "api_endpoint": str(request.url_for("mining_config")),
            "status_endpoint": status_url,
            "downloads_endpoint": downloads_url,
            "generate_endpoint": generate_url,
            "manual_commands": commands,
            "mode_examples": mode_examples,
            "default_worker": defaults.worker,
            "default_threads": defaults.threads,
            "fee_percent": resolved.fee_percent,
            "payout_minimum": resolved.payout_minimum,
            "payout_interval_seconds": resolved.payout_interval_seconds,
            "payout_min_amount": resolved.payout_min_amount,
            "warnings": list(resolved.warnings),
            "payout_instructions": "Enter an address on the active Animica network. Pool rewards are credited to that payout address.",
            "worker_instructions": "Worker names are labels only. Use short, stable names like rig-01 or office-cpu.",
            "pool_mode_instructions": mode_summary.get(
                resolved.pool_mode, mode_summary["pps"]
            ),
            "miner_executable": "animica-miner",
            "status": self.status_payload(request),
        }

    def generated_payload(
        self,
        request: Request,
        *,
        address: Optional[str] = None,
        worker: Optional[str] = None,
        threads: Optional[int] = None,
    ) -> dict[str, Any]:
        resolved = self.resolve(request)
        bundle = build_bundle_input(address=address, worker=worker, threads=threads)
        commands = build_manual_commands(resolved, bundle)
        config_content = build_config_document(resolved, bundle)
        base_downloads: dict[str, str] = {}
        for platform in ("windows", "macos", "linux"):
            base_url = str(request.url_for("download_miner_bundle", platform=platform))
            base_downloads[platform] = _append_query(
                base_url,
                {
                    "address": bundle.address if bundle.address != PLACEHOLDER_ADDRESS else None,
                    "worker": bundle.worker,
                    "threads": bundle.threads,
                },
            )
        return {
            "network": resolved.network,
            "pool_mode": resolved.pool_mode,
            "stratum_url": resolved.stratum_url,
            "address": bundle.address,
            "worker": bundle.worker,
            "threads": bundle.threads,
            "miner_executable": "animica-miner",
            "commands": commands,
            "config": {
                "filename": "animica-miner.config.json",
                "content": config_content,
            },
            "starter_files": [
                {
                    "platform": "windows",
                    "filename": "start_mining.bat",
                    "content": build_launcher_script("windows", resolved),
                },
                {
                    "platform": "macos",
                    "filename": "start_mining.command",
                    "content": build_launcher_script("macos", resolved),
                },
                {
                    "platform": "linux",
                    "filename": "start_mining.sh",
                    "content": build_launcher_script("linux", resolved),
                },
            ],
            "download_urls": base_downloads,
        }


def build_download_query(address: str, worker: str, threads: int) -> dict[str, Any]:
    return {
        "address": address if address != PLACEHOLDER_ADDRESS else None,
        "worker": worker,
        "threads": threads if threads > 0 else None,
    }
