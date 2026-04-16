"""Mining operations CLI for Animica.

Provides commands for:
  - Mining blocks via RPC (mine-blocks)
  - Running the Stratum pool server (run-pool)
  - Inspecting pool configuration (show-config)
  - Generating payout addresses (generate-payout-address)
"""

from __future__ import annotations

import importlib
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

import asyncio
import logging

import typer
from animica.coin import COIN_UNIT
from animica.config import load_network_config
from animica.cli.rpc_guard import guard_bootstrap_rpc
from animica.cli.rpc import call_rpc
from mining.template_block import (build_submit_block_payload,
                                   hash_candidate_header,
                                   header_from_template_view)
from .timeouts import DEFAULT_RPC_TIMEOUT, RPC_TIMEOUT_ENV, resolve_timeout

app = typer.Typer(help="Mining operations and Stratum pool management.")

RPC_ENV = "ANIMICA_RPC_URL"
DB_ENV = "ANIMICA_MINING_POOL_DB_URL"
LOG_LEVEL_ENV = "ANIMICA_MINING_POOL_LOG_LEVEL"
STRATUM_BIND_ENV = "ANIMICA_STRATUM_BIND"
API_BIND_ENV = "ANIMICA_POOL_API_BIND"
POOL_MODE_ENV = "ANIMICA_POOL_MODE"
POOL_ADDRESS_ENV = "ANIMICA_POOL_ADDRESS"

# Supported mining device backends
SUPPORTED_DEVICES = ["cpu", "cuda", "rocm", "opencl", "metal", "auto"]

# Mining warning message suffix for verifier seed constraints
VERIFIER_MINING_WARNING_SUFFIX = "mined blocks may be reorged."


class _StratumRuntimeLoadError(RuntimeError):
    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class _StratumRuntime:
    pool_cli: Any
    pool_config_type: Any
    load_config_from_env: Callable[..., Any]


_STRATUM_RUNTIME: Optional[_StratumRuntime] = None
_STRATUM_IMPORT_ERROR: Optional[_StratumRuntimeLoadError] = None
HAVE_STRATUM = False
pool_cli: Any = None


def _format_import_exception(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def _build_stratum_load_error(exc: BaseException) -> _StratumRuntimeLoadError:
    if isinstance(exc, ModuleNotFoundError):
        missing_name = getattr(exc, "name", "") or ""
        if missing_name == "animica.stratum_pool" or missing_name.startswith(
            "animica.stratum_pool."
        ):
            summary = "Stratum pool not installed; run: pip install 'animica[stratum]'"
            kind = "missing_package"
        else:
            summary = "Stratum pool failed to import because a required dependency is missing"
            kind = "runtime_import_error"
    elif isinstance(exc, (ImportError, AttributeError)):
        summary = "Stratum pool import symbol mismatch"
        kind = "symbol_mismatch"
    else:
        summary = "Stratum pool failed during import"
        kind = "runtime_import_error"
    return _StratumRuntimeLoadError(
        kind,
        f"{summary}. Underlying error: {_format_import_exception(exc)}",
    )


def _load_stratum_runtime() -> _StratumRuntime:
    global _STRATUM_IMPORT_ERROR, _STRATUM_RUNTIME, HAVE_STRATUM, pool_cli

    if _STRATUM_RUNTIME is not None:
        return _STRATUM_RUNTIME
    if _STRATUM_IMPORT_ERROR is not None:
        raise _STRATUM_IMPORT_ERROR

    try:
        pool_cli_module = importlib.import_module("animica.stratum_pool.cli")
        config_module = importlib.import_module("animica.stratum_pool.config")
        pool_config_type = getattr(config_module, "PoolConfig")
        load_config = getattr(config_module, "load_config_from_env")
    except Exception as exc:
        error = _build_stratum_load_error(exc)
        _STRATUM_IMPORT_ERROR = error
        HAVE_STRATUM = False
        pool_cli = None
        raise error from exc

    runtime = _StratumRuntime(
        pool_cli=pool_cli_module,
        pool_config_type=pool_config_type,
        load_config_from_env=load_config,
    )
    _STRATUM_RUNTIME = runtime
    _STRATUM_IMPORT_ERROR = None
    HAVE_STRATUM = True
    pool_cli = pool_cli_module
    return runtime


def _probe_stratum_support() -> None:
    try:
        _load_stratum_runtime()
    except _StratumRuntimeLoadError:
        return


_probe_stratum_support()


def _is_truthy_env(var_name: str, default: bool = False) -> bool:
    raw = os.environ.get(var_name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _mine_header(
    header: "Header",
    target_int: int,
    *,
    workers: int | None = None,
) -> tuple[int | None, bytes | None]:
    # Increased from 1000000 to 10000000 for better PoW success rate
    max_nonce = max(1, int(os.getenv("ANIMICA_MINER_MAX_NONCE", "10000000")))
    retry_windows = max(1, int(os.getenv("ANIMICA_MINER_POW_RETRY_WINDOWS", "4")))
    # Increased from 5000000 to 50000000 for extended search space
    default_total = max(max_nonce * retry_windows, 50_000_000)
    max_total_nonce = max(
        1,
        int(os.getenv("ANIMICA_MINER_MAX_TOTAL_NONCE", str(default_total))),
    )
    total_windows = max(retry_windows, math.ceil(max_total_nonce / max_nonce))
    resolved_workers = 1
    if workers is not None:
        from mining.parallel_nonce_search import resolve_worker_count

        resolved_workers = resolve_worker_count(workers)

    def _scan_window(start_nonce: int, end_nonce: int) -> tuple[int | None, bytes | None]:
        if resolved_workers > 1:
            from mining.parallel_nonce_search import parallel_nonce_search, pow_check_nonce

            result = parallel_nonce_search(
                pow_check_nonce,
                (header, target_int),
                start_nonce,
                end_nonce - start_nonce,
                resolved_workers,
            )
            if result and isinstance(result.payload, tuple):
                digest = result.payload[0]
                if isinstance(digest, (bytes, bytearray)):
                    return result.nonce, bytes(digest)
            return None, None

        for nonce in range(start_nonce, end_nonce):
            try:
                candidate_hash = hash_candidate_header(header, nonce=nonce)
            except Exception:
                continue
            if candidate_hash.digest_int <= target_int:
                return nonce, candidate_hash.digest
        return None, None

    # Use random starting nonce for each block to prevent nonce growth issues
    # This makes mining time-based and more about hash power rather than sequential nonce counting
    # Randomize in 32-bit space for better distribution and to avoid large nonce values
    import secrets
    start_nonce = secrets.randbelow(2**32)
    
    for _ in range(max(1, total_windows)):
        nonce, digest = _scan_window(start_nonce, start_nonce + max_nonce)
        if nonce is not None and digest is not None:
            return nonce, digest
        # Wrap around at 64-bit boundary to prevent overflow
        start_nonce = (start_nonce + max_nonce) & 0xFFFFFFFFFFFFFFFF
    return None, None


def _format_rpc_error(error: Exception) -> str:
    code = getattr(error, "code", None)
    message = getattr(error, "message", None)
    data = getattr(error, "data", None)
    parts = []
    if code is not None:
        parts.append(f"code={code}")
    if message is not None:
        parts.append(f"message={message}")
    if data is not None:
        parts.append(f"data={data}")
    return " ".join(parts) if parts else str(error)


def _emit_mining_summary(summary: dict, *, verbose: bool, force: bool = False) -> None:
    if not (verbose or force):
        return
    payload = json.dumps(summary, sort_keys=True)
    typer.echo(f"  Mining summary: {payload}")


def _lookup_recent_mining_credit(client: Any, address: str, height: int | None = None) -> dict | None:
    try:
        params = {"address": address, "last": 10}
        if height is not None:
            params["from_height"] = height
            params["to_height"] = height
        result = client.request("mining.getCredits", params)
        if not isinstance(result, dict):
            return None
        credits = result.get("credits", [])
        if not isinstance(credits, list):
            return None
        if height is not None:
            for credit in credits:
                try:
                    if int(credit.get("height", -1)) == int(height):
                        return credit
                except Exception:
                    continue
        return credits[0] if credits else None
    except Exception:
        return None


def _ensure_network_env() -> None:
    cfg = load_network_config()
    os.environ.setdefault("ANIMICA_NETWORK", cfg.name)
    os.environ.setdefault(RPC_ENV, cfg.rpc_url)


def _ensure_stratum_available() -> _StratumRuntime:
    try:
        return _load_stratum_runtime()
    except _StratumRuntimeLoadError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc


def _validate_bech32_address(address: str) -> bool:
    """
    Validate that a string is a valid Animica Bech32 address.
    
    Args:
        address: Address string to validate
        
    Returns:
        bool: True if valid Animica Bech32 address, False otherwise
    """
    try:
        from pq.py.address import validate_address
        
        # Must start with 'anim1' prefix
        if not address.startswith("anim1"):
            return False
        
        # Use PQ library validation
        validate_address(address, expect_hrp="anim")
        return True
    except (ValueError, ImportError, AttributeError):
        # ValueError: invalid address format
        # ImportError: PQ library not available
        # AttributeError: validate_address function not found
        return False


def _resolve_wallet_label_to_address(label: str, wallet_file: Optional[Path] = None) -> Optional[str]:
    """
    Resolve a wallet label to its Bech32 address.
    
    Args:
        label: Wallet label to look up
        wallet_file: Optional wallet file path (uses default if None)
        
    Returns:
        str: Bech32 address if found, None otherwise
    """
    try:
        from animica.cli.wallet import _load_store, _wallet_file_path
        
        path = _wallet_file_path(wallet_file)
        store = _load_store(path)
        
        # Search for wallet by label
        for entry in store.get("wallets", []):
            if entry.get("label") == label:
                return entry.get("address")
        
        return None
    except (ImportError, FileNotFoundError, KeyError, TypeError, ValueError):
        # ImportError: wallet module not available
        # FileNotFoundError: wallet file doesn't exist
        # KeyError/TypeError: malformed wallet store
        # ValueError: invalid JSON in wallet file
        return None


def _resolve_payout_address(address_or_label: str) -> str:
    """
    Resolve a payout address from either a wallet label or raw Bech32 address.
    
    Priority:
    1. If it's a valid Bech32 address (starts with 'anim1' and passes validation), use it directly
    2. Otherwise, try to resolve as a wallet label
    3. If both fail, raise an error
    
    Args:
        address_or_label: Either a Bech32 address or wallet label
        
    Returns:
        str: Resolved Bech32 address
        
    Raises:
        typer.Exit: If address cannot be resolved
    """
    # First check if it's a valid Bech32 address
    if _validate_bech32_address(address_or_label):
        return address_or_label
    
    # Try to resolve as a wallet label
    resolved_address = _resolve_wallet_label_to_address(address_or_label)
    if resolved_address:
        return resolved_address
    
    # Could not resolve - fail fast with clear error
    typer.secho(
        f"Error: '{address_or_label}' is neither a valid Animica Bech32 address "
        f"(must start with 'anim1') nor a known wallet label.",
        fg=typer.colors.RED,
        err=True,
    )
    typer.secho(
        "Use 'animica wallet list' to see available wallet labels, "
        "or provide a valid Bech32 address.",
        fg=typer.colors.YELLOW,
        err=True,
    )
    raise typer.Exit(2)


def _check_sync(rpc_url: str, *, force: bool) -> None:
    try:
        head = call_rpc("chain_getHead", [], rpc_url)
    except Exception as exc:  # noqa: BLE001
        if force:
            typer.echo(f"Warning: sync status unavailable ({exc}); mining forced.")
            return
        raise typer.Exit(1)
    height = int(head.get("height") or head.get("number") or 0)
    if height == 0:
        typer.echo("Mining allowed at height 0 (bootstrap).")
        return
    if force:
        typer.echo("Warning: mining forced; sync gating bypassed.")


def _warn_if_unsynced(rpc_url: str, *, threshold: int = 5) -> bool:
    """
    Check if the node is behind the network and warn the user.
    
    With verifier seeds enabled, this check ensures mining is only allowed when:
    1. Node is at the verifier seed height, OR
    2. Node is at verifier_height + 1 (actively mining the next block)
    
    Args:
        rpc_url: RPC endpoint URL
        threshold: Height difference threshold for warnings (used for non-verifier checks)
        
    Returns:
        True if node is behind and should not mine, False otherwise
    """
    # First check verifier seed status if available
    try:
        verifier_status = call_rpc("p2p.getVerifierSeeds", [], rpc_url)
        if isinstance(verifier_status, dict) and verifier_status.get("enabled"):
            # can_mine defaults to False for safety - if the key is missing or None,
            # we should block mining rather than allow it
            can_mine = verifier_status.get("can_mine", False)
            local_height = verifier_status.get("local_height", 0)
            max_verifier_height = verifier_status.get("max_verifier_height")
            max_allowed_height = verifier_status.get("max_allowed_height")
            
            if not can_mine:
                # Format warning message based on available data
                if max_verifier_height is not None and max_allowed_height is not None:
                    typer.echo(
                        f"Warning: You are ahead of verifier seeds "
                        f"(local: {local_height}, max_verifier: {max_verifier_height}, "
                        f"max_allowed: {max_allowed_height}); "
                        f"{VERIFIER_MINING_WARNING_SUFFIX}"
                    )
                else:
                    typer.echo(
                        f"Warning: Mining blocked by verifier seed constraints "
                        f"(local: {local_height}); {VERIFIER_MINING_WARNING_SUFFIX}"
                    )
                return True
            
            # If verifier seeds are enabled and node can mine, allow it
            # regardless of other sync status checks
            if max_verifier_height is not None:
                # Verifier seeds are connected and we're within allowed range
                return False
    except Exception:
        # If verifier seed check fails, fall back to traditional sync check
        pass
    
    # Fall back to traditional sync status check
    try:
        status = call_rpc("sync.getStatus", [], rpc_url)
    except Exception:
        return False

    if not isinstance(status, dict):
        return False

    phase = status.get("phase") or status.get("state")
    synchronized = status.get("synchronized")
    head_height = status.get("head_height")
    best_header_height = status.get("best_header_height")
    network_best = status.get("network_best_height")
    try:
        head_height = int(head_height) if head_height is not None else None
    except Exception:
        head_height = None
    try:
        best_header_height = int(best_header_height) if best_header_height is not None else None
    except Exception:
        best_header_height = None
    try:
        network_best = int(network_best) if network_best is not None else None
    except Exception:
        network_best = None

    if synchronized is True:
        return False

    behind = False
    lag_known = False
    if network_best is not None and head_height is not None:
        lag_known = True
        if network_best - head_height > threshold:
            behind = True
    if best_header_height is not None and head_height is not None:
        lag_known = True
        if best_header_height - head_height > threshold:
            behind = True
    if not lag_known and phase and phase not in {"SYNCED", "IDLE", "TARGET_REACHED"}:
        behind = True

    if behind:
        typer.echo(
            "Warning: You are behind the network; mined blocks/tx confirmations may be reorged."
        )
    return behind


async def _run_solo(
    *,
    rpc_url: str,
    proof_type: str,
    device: str,
    threads: int,
    count: Optional[int],
    stats_interval: int,
    address: str,
) -> None:
    from mining.orchestrator import MinerOrchestrator, OrchestratorConfig
    from mining.rpc_adapter import RpcTemplateProvider
    from mining.share_submitter import ShareSubmitter, SubmitterConfig

    provider = RpcTemplateProvider(
        rpc_url=rpc_url, proof_type=proof_type, solo_address=address
    )
    submitter = ShareSubmitter(SubmitterConfig(rpc_url=rpc_url))
    cfg = OrchestratorConfig(device_kind=device, threads=threads)
    orchestrator = MinerOrchestrator(template_provider=provider, submitter=submitter, config=cfg)
    await orchestrator.start()
    try:
        while True:
            stats = submitter.stats()
            if count and stats.blocks_accepted >= count:
                break
            if stats_interval:
                typer.echo(
                    f"shares ok={stats.shares_accepted} rej={stats.shares_rejected} "
                    f"blocks={stats.blocks_accepted} errors={stats.shares_errors} "
                    f"last_error={stats.last_error}"
                )
            await asyncio.sleep(max(1, stats_interval))
    finally:
        await orchestrator.stop()


async def _run_pool(
    *,
    rpc_url: str,
    listen: str,
    port: int,
    share_target: float,
    proof_type: str,
    no_p2p: bool,
    p2p_port: int,
) -> None:
    from mining.pool import PoolConfig, StratumPool

    cfg = PoolConfig(
        rpc_url=rpc_url,
        listen_host=listen,
        listen_port=port,
        share_target=share_target,
        proof_type=proof_type,
        no_p2p=no_p2p,
        p2p_port=p2p_port,
    )
    pool = StratumPool(cfg)
    await pool.start()
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await pool.stop()


@app.command("run-pool")
def run_pool(
    mode: str = typer.Option(
        "pps",
        "--mode",
        help="Payout/accounting mode (pps|solo)",
        envvar=POOL_MODE_ENV,
    ),
    pool_address: Optional[str] = typer.Option(
        None,
        "--pool-address",
        "--coinbase-address",
        help="Pool payout address (used for block template generation)",
        envvar=POOL_ADDRESS_ENV,
    ),
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc-url", help="Animica node RPC URL", envvar=RPC_ENV
    ),
    allow_remote_rpc: bool = typer.Option(
        False,
        "--allow-remote-rpc",
        help="Allow using bootstrap RPC (requires ANIMICA_I_UNDERSTAND_REMOTE_RISK=1)",
    ),
    db_url: Optional[str] = typer.Option(
        None, "--db-url", help="Database URL", envvar=DB_ENV
    ),
    stratum_bind: Optional[str] = typer.Option(
        None, "--stratum-bind", help="Stratum bind address", envvar=STRATUM_BIND_ENV
    ),
    api_bind: Optional[str] = typer.Option(
        None, "--api-bind", help="API bind address", envvar=API_BIND_ENV
    ),
    log_level: Optional[str] = typer.Option(
        None, "--log-level", help="Log level", envvar=LOG_LEVEL_ENV
    ),
    host: Optional[str] = typer.Option(
        None, "--host", help="Stratum bind host", envvar="ANIMICA_STRATUM_HOST"
    ),
    port: Optional[int] = typer.Option(
        None, "--port", help="Stratum bind port", envvar="ANIMICA_STRATUM_PORT"
    ),
    api_host: Optional[str] = typer.Option(
        None,
        "--api-host",
        help="Pool API bind host",
        envvar="ANIMICA_STRATUM_API_HOST",
    ),
    api_port: Optional[int] = typer.Option(
        None,
        "--api-port",
        help="Pool API bind port",
        envvar="ANIMICA_STRATUM_API_PORT",
    ),
    min_difficulty: Optional[float] = typer.Option(
        None,
        "--min-difficulty",
        help="Minimum share threshold (theta micro; legacy ratio if <= 1.0)",
        envvar="ANIMICA_STRATUM_MIN_DIFFICULTY",
    ),
    max_difficulty: Optional[float] = typer.Option(
        None,
        "--max-difficulty",
        help="Maximum share threshold (theta micro; legacy ratio if <= 1.0)",
        envvar="ANIMICA_STRATUM_MAX_DIFFICULTY",
    ),
    poll_interval: Optional[float] = typer.Option(
        None,
        "--poll-interval",
        help="Template polling interval seconds",
        envvar="ANIMICA_STRATUM_POLL_INTERVAL",
    ),
    rpc_timeout: Optional[float] = typer.Option(
        None,
        "--rpc-timeout",
        help="Node RPC timeout seconds",
        envvar="ANIMICA_STRATUM_RPC_TIMEOUT",
    ),
    chain_id: Optional[int] = typer.Option(
        None,
        "--chain-id",
        help="Chain id override",
        envvar="ANIMICA_CHAIN_ID",
    ),
    profile: Optional[str] = typer.Option(
        None,
        "--profile",
        help="Pool profile (hashshare|asic_sha256)",
        envvar="ANIMICA_POOL_PROFILE",
    ),
) -> None:
    """Start the Animica Stratum mining pool with validated PPS/SOLO settings.

    Examples:
      animica miner run-pool --mode pps --pool-address anim1... --rpc-url http://127.0.0.1:8545/rpc
      animica miner run-pool --mode solo --pool-address anim1... --rpc-url http://127.0.0.1:8545/rpc
    """
    _ensure_network_env()
    runtime = _ensure_stratum_available()

    normalized_mode = str(mode or "").strip().lower()
    if normalized_mode not in {"pps", "solo"}:
        typer.secho(
            "Error: --mode must be either 'pps' or 'solo'.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2)

    if not str(pool_address or "").strip():
        typer.secho(
            "Error: pool payout address is required. Set --pool-address or ANIMICA_POOL_ADDRESS.",
            fg=typer.colors.RED,
            err=True,
        )
        typer.secho(
            "Example: animica miner run-pool --mode pps --pool-address anim1... --rpc-url http://127.0.0.1:8545/rpc",
            fg=typer.colors.YELLOW,
            err=True,
        )
        raise typer.Exit(2)
    resolved_pool_address = _resolve_payout_address(str(pool_address))

    effective_rpc = rpc_url or os.environ.get(RPC_ENV) or load_network_config().rpc_url
    guard_bootstrap_rpc(effective_rpc, allow_remote=allow_remote_rpc, method="miner.runPool")

    cfg_overrides: dict[str, object] = {
        "pool_mode": normalized_mode,
        "pool_address": resolved_pool_address,
    }
    env_overrides = {
        RPC_ENV: rpc_url or effective_rpc,
        DB_ENV: db_url,
        STRATUM_BIND_ENV: stratum_bind,
        API_BIND_ENV: api_bind,
        LOG_LEVEL_ENV: log_level,
        POOL_MODE_ENV: normalized_mode,
        POOL_ADDRESS_ENV: resolved_pool_address,
        "ANIMICA_POOL_ENABLED": "1",
    }
    if stratum_bind is not None:
        cfg_overrides["stratum_bind"] = stratum_bind
    if api_bind is not None:
        cfg_overrides["api_bind"] = api_bind
    if host is not None:
        cfg_overrides["host"] = host
        env_overrides["ANIMICA_STRATUM_HOST"] = host
    if port is not None:
        cfg_overrides["port"] = int(port)
        env_overrides["ANIMICA_STRATUM_PORT"] = str(int(port))
    if api_host is not None:
        cfg_overrides["api_host"] = api_host
        env_overrides["ANIMICA_STRATUM_API_HOST"] = api_host
    if api_port is not None:
        cfg_overrides["api_port"] = int(api_port)
        env_overrides["ANIMICA_STRATUM_API_PORT"] = str(int(api_port))
    if min_difficulty is not None:
        cfg_overrides["min_difficulty"] = float(min_difficulty)
        env_overrides["ANIMICA_STRATUM_MIN_DIFFICULTY"] = str(float(min_difficulty))
    if max_difficulty is not None:
        cfg_overrides["max_difficulty"] = float(max_difficulty)
        env_overrides["ANIMICA_STRATUM_MAX_DIFFICULTY"] = str(float(max_difficulty))
    if poll_interval is not None:
        cfg_overrides["poll_interval"] = float(poll_interval)
        env_overrides["ANIMICA_STRATUM_POLL_INTERVAL"] = str(float(poll_interval))
    if rpc_timeout is not None:
        cfg_overrides["rpc_timeout"] = float(rpc_timeout)
        env_overrides["ANIMICA_STRATUM_RPC_TIMEOUT"] = str(float(rpc_timeout))
    if chain_id is not None:
        cfg_overrides["chain_id"] = int(chain_id)
        env_overrides["ANIMICA_CHAIN_ID"] = str(int(chain_id))
    if profile is not None:
        cfg_overrides["profile"] = profile
        env_overrides["ANIMICA_POOL_PROFILE"] = profile

    try:
        resolved_cfg = runtime.load_config_from_env(overrides=cfg_overrides)
    except Exception as exc:
        typer.secho(
            f"Error: invalid pool configuration: {exc}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2) from exc

    typer.secho(
        "Starting pool "
        f"(mode={resolved_cfg.pool_mode}, profile={resolved_cfg.profile}, "
        f"stratum={resolved_cfg.host}:{resolved_cfg.port}, api={resolved_cfg.api_host}:{resolved_cfg.api_port})",
        fg=typer.colors.GREEN,
    )

    for key, value in env_overrides.items():
        if value is not None:
            os.environ[key] = str(value)
    try:
        runtime.pool_cli.main([])
    except RuntimeError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@app.command("solo")
def solo(
    address: str = typer.Option(
        ..., "--address", help="Payout address (anim1...)"
    ),
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc-url", help="Animica node RPC URL", envvar=RPC_ENV
    ),
    proof: str = typer.Option(
        "sha256d", "--proof", help="Proof type (sha256d|aicf|quantum|auto)"
    ),
    device: str = typer.Option(
        "cpu", "--device", help="Device backend", show_default=True
    ),
    count: Optional[int] = typer.Option(
        None, "--count", help="Stop after N blocks"
    ),
    threads: int = typer.Option(
        os.cpu_count() or 1, "--threads", help="CPU threads"
    ),
    affinity: Optional[str] = typer.Option(
        None, "--affinity", help="CPU affinity mask"
    ),
    force: bool = typer.Option(
        False, "--force", help="Bypass sync gating"
    ),
    log_json: bool = typer.Option(False, "--log-json", help="Emit JSON logs"),
    stats_interval: int = typer.Option(5, "--stats-interval", help="Stats interval (sec)"),
) -> None:
    _ensure_network_env()
    effective_rpc = rpc_url or os.environ.get(RPC_ENV) or load_network_config().rpc_url
    guard_bootstrap_rpc(effective_rpc, allow_remote=False, method="miner.solo")
    _check_sync(effective_rpc, force=force)
    logging.basicConfig(level=logging.INFO)
    if affinity:
        os.environ["ANIMICA_CPU_AFFINITY"] = affinity
    if log_json:
        os.environ["ANIMICA_LOG_JSON"] = "1"
    if device not in SUPPORTED_DEVICES:
        raise typer.Exit(2)
    asyncio.run(
        _run_solo(
            rpc_url=effective_rpc,
            proof_type=proof,
            device=device,
            threads=threads,
            count=count,
            stats_interval=stats_interval,
            address=address,
        )
    )


@app.command("pool")
def pool(
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc-url", help="Animica node RPC URL", envvar=RPC_ENV
    ),
    listen: str = typer.Option("0.0.0.0", "--listen", help="Stratum bind host"),
    port: int = typer.Option(5333, "--port", help="Stratum port"),
    mode: str = typer.Option("solo", "--mode", help="Payout mode (pps|pplns|solo)"),
    coinbase_address: Optional[str] = typer.Option(
        None, "--coinbase-address", "--payout-address", help="Pool payout address"
    ),
    proof: str = typer.Option(
        "sha256d", "--proof", help="Proof type (sha256d|aicf|quantum|auto)"
    ),
    device: str = typer.Option(
        "cpu", "--device", help="Device backend", show_default=True
    ),
    threads: int = typer.Option(
        os.cpu_count() or 1, "--threads", help="CPU threads"
    ),
    no_p2p: bool = typer.Option(False, "--no-p2p", help="Disable in-process P2P"),
    p2p_port: int = typer.Option(30333, "--p2p-port", help="P2P port"),
) -> None:
    _ensure_network_env()
    effective_rpc = rpc_url or os.environ.get(RPC_ENV) or load_network_config().rpc_url
    guard_bootstrap_rpc(effective_rpc, allow_remote=False, method="miner.pool")
    _check_sync(effective_rpc, force=True)
    logging.basicConfig(level=logging.INFO)
    if coinbase_address:
        _resolve_payout_address(coinbase_address)
    typer.echo(f"Pool mode={mode} device={device} threads={threads}")
    asyncio.run(
        _run_pool(
            rpc_url=effective_rpc,
            listen=listen,
            port=port,
            share_target=float(os.getenv("ANIMICA_SHARE_TARGET", "0.01")),
            proof_type=proof,
            no_p2p=no_p2p,
            p2p_port=p2p_port,
        )
    )


@app.command("cpu")
def cpu(
    address: str = typer.Option(..., "--address", help="Payout address (anim1...)"),
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc-url", help="Animica node RPC URL", envvar=RPC_ENV
    ),
    count: Optional[int] = typer.Option(None, "--count", help="Stop after N blocks"),
    threads: int = typer.Option(os.cpu_count() or 1, "--threads", help="CPU threads"),
) -> None:
    solo(
        address=address,
        rpc_url=rpc_url,
        proof="sha256d",
        device="cpu",
        count=count,
        threads=threads,
        affinity=None,
        force=False,
        log_json=False,
        stats_interval=5,
    )


@app.command("aicf")
def aicf(
    address: str = typer.Option(..., "--address", help="Payout address (anim1...)"),
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc-url", help="Animica node RPC URL", envvar=RPC_ENV
    ),
    count: Optional[int] = typer.Option(None, "--count", help="Stop after N blocks"),
    device: str = typer.Option("auto", "--device", help="Device backend"),
    threads: int = typer.Option(os.cpu_count() or 1, "--threads", help="CPU threads"),
) -> None:
    if device == "auto":
        device = "cpu"
        typer.echo("GPU AICF backend not available; falling back to CPU.")
    solo(
        address=address,
        rpc_url=rpc_url,
        proof="aicf",
        device=device,
        count=count,
        threads=threads,
        affinity=None,
        force=False,
        log_json=False,
        stats_interval=5,
    )


@app.command("quantum")
def quantum(
    address: str = typer.Option(..., "--address", help="Payout address (anim1...)"),
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc-url", help="Animica node RPC URL", envvar=RPC_ENV
    ),
    count: Optional[int] = typer.Option(None, "--count", help="Stop after N blocks"),
    device: str = typer.Option("cpu", "--device", help="Device backend"),
    threads: int = typer.Option(os.cpu_count() or 1, "--threads", help="CPU threads"),
) -> None:
    try:
        from mining.quantum_worker import SimulatedQuantumProvider

        _ = SimulatedQuantumProvider()
    except Exception:
        typer.echo("Quantum simulator unavailable; cannot start quantum miner.")
        raise typer.Exit(1)
    solo(
        address=address,
        rpc_url=rpc_url,
        proof="quantum",
        device=device,
        count=count,
        threads=threads,
        affinity=None,
        force=False,
        log_json=False,
        stats_interval=5,
    )


da_app = typer.Typer(help="Data availability utilities")
app.add_typer(da_app, name="da")


@da_app.command("push")
def da_push(
    file: Path = typer.Argument(..., help="File to commit to DA root"),
) -> None:
    from mining.da_adapter import compute_da_root, set_da_root

    data = file.read_bytes()
    root = compute_da_root(data)
    set_da_root(root)
    typer.echo(f"DA root set to 0x{root.hex()}")


@da_app.command("run")
def da_run() -> None:
    from mining.storage_worker import StorageWorker

    async def _run() -> None:
        worker = StorageWorker.create_from_env()
        await worker.start()
        typer.echo("DA worker running (Ctrl+C to stop).")
        try:
            while True:
                for rec in worker.pop_ready():
                    typer.echo(
                        f"DA result {rec.task_id} qos={rec.metrics.get('qos')} root={rec.output_digest.hex()}"
                    )
                await asyncio.sleep(1.0)
        finally:
            await worker.stop()

    asyncio.run(_run())


@app.command("show-config")
def show_config() -> None:
    """Display the effective pool configuration."""
    _ensure_network_env()
    try:
        runtime = _load_stratum_runtime()
    except _StratumRuntimeLoadError as exc:
        if exc.kind != "missing_package":
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(1) from exc

        # Stratum not installed; show what's available from env vars
        rpc_url = os.getenv(RPC_ENV, "(not set)")
        db_url = os.getenv(DB_ENV, "(not set)")
        stratum_bind = os.getenv(STRATUM_BIND_ENV, "(not set)")
        api_bind = os.getenv(API_BIND_ENV, "(not set)")
        log_level = os.getenv(LOG_LEVEL_ENV, "info")
        pool_mode = os.getenv(POOL_MODE_ENV, "pps")
        pool_address = os.getenv(POOL_ADDRESS_ENV, "(not set)")
        typer.echo(
            f"RPC URL: {rpc_url}\n"
            f"DB URL: {db_url}\n"
            f"Pool address: {pool_address}\n"
            f"Pool mode: {pool_mode}\n"
            f"Stratum bind: {stratum_bind}\n"
            f"API bind: {api_bind}\n"
            f"Log level: {log_level}\n"
            f"Note: {exc}; full pool config unavailable."
        )
        return

    try:
        cfg = runtime.load_config_from_env()
    except Exception as exc:
        typer.echo(
            f"Error: could not load pool config: {_format_import_exception(exc)}",
            err=True,
        )
        raise typer.Exit(1) from exc

    typer.echo(
        f"RPC URL: {cfg.rpc_url}\n"
        f"DB URL: {cfg.db_url}\n"
        f"Chain ID: {cfg.chain_id}\n"
        f"Pool address: {cfg.pool_address}\n"
        f"Pool mode: {cfg.pool_mode}\n"
        f"Stratum bind: {cfg.host}:{cfg.port}\n"
        f"API bind: {cfg.api_host}:{cfg.api_port}\n"
        f"Profile: {cfg.profile}\n"
        f"Log level: {cfg.log_level}"
    )


@app.command("generate-payout-address")
def generate_payout_address(
    wallet_file: Optional[Path] = typer.Option(
        None, "--wallet-file", help="Wallet store for generated address"
    ),
    label: str = typer.Option(
        "pool-payout", "--label", help="Label for the generated wallet"
    ),
) -> None:
    """Generate a dev wallet for pool payouts using the wallet CLI helpers."""
    # Delegate to wallet module for key generation (no stratum dependency required)
    try:
        from animica.cli.wallet import (_generate_entry, _load_store,
                                        _resolve_signature_alg,
                                        _save_store, _wallet_file_path)

        path = _wallet_file_path(wallet_file)
        store = _load_store(path)
        alg_info = _resolve_signature_alg(None)
        entry = _generate_entry(
            label,
            allow_fallback=True,
            alg_info=alg_info,
            allow_default_fallback=True,
        )
        store.setdefault("wallets", []).append(entry.to_dict())
        _save_store(path, store)
        typer.echo(f"Generated payout address {entry.address} (label: {entry.label})")
    except Exception as e:
        typer.echo(f"Error generating payout address: {e}", err=True)
        raise typer.Exit(1)


@app.command("mine-blocks")
def mine_blocks(
    address: Optional[str] = typer.Argument(
        None,
        help="Payout address (positional): wallet label or Bech32 address",
    ),
    count: int = typer.Option(
        ...,
        "--count",
        help="Number of blocks to mine (must be > 0)",
    ),
    address_opt: Optional[str] = typer.Option(
        None,
        "--address",
        help="Payout address (option, for backward compat): wallet label or Bech32 address",
    ),
    threads: int = typer.Option(
        1,
        "--threads",
        help="CPU threads for PoW search (0=auto)",
    ),
    allow_remote_rpc: bool = typer.Option(
        False,
        "--allow-remote-rpc",
        help="Allow using bootstrap RPC (requires ANIMICA_I_UNDERSTAND_REMOTE_RISK=1)",
    ),
    device: str = typer.Option(
        "auto",
        "--device",
        help="Mining device backend (cpu, cuda, rocm, opencl, metal, auto). Default: auto (auto-detect best device)",
        envvar="ANIMICA_MINER_DEVICE",
    ),
    gpu: bool = typer.Option(
        False,
        "--gpu",
        help="Use CUDA GPU backend (alias for --device cuda)",
    ),
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="Node JSON-RPC endpoint URL",
        envvar="ANIMICA_RPC_URL",
    ),
    use_proxy: bool = typer.Option(
        False,
        "--use-proxy/--no-proxy",
        help="(DEPRECATED) Use external proxy endpoint (requires ANIMICA_TRUSTED_RPC_URL). Default: disabled (use P2P)",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Enable verbose output (show tx selection details)",
    ),
    no_timeout: bool = typer.Option(
        False,
        "--no-timeout",
        help="Disable RPC timeout (wait indefinitely). Useful for high-load or slow network conditions.",
    ),
    include_mempool: bool = typer.Option(
        True,
        "--include-mempool/--no-include-mempool",
        help="Include pending mempool transactions when mining (default: include).",
    ),
    template_ttl_s: int = typer.Option(15, "--template-ttl-s", help="Template lease TTL in seconds."),
) -> None:
    """
    Mine blocks with proof-of-work to a specified payout address.
    
    This command performs actual mining by iterating through nonces until finding
    block hashes that meet the current difficulty target (derived from the network's
    theta parameter). Block rewards are credited to the specified payout address.
    
    The miner will continue until the requested number of non-duplicate blocks are mined.
    If a block is found to be a duplicate (already mined by another miner), it is skipped
    and mining continues. Stale templates are retried once before moving to the next block.
    
    Pending mempool transactions are included in mined blocks and executed to update
    balances and nonces. After mining, included transactions are removed from the mempool.
    
    Address Resolution:
      The address can be provided as a positional argument or via --address option:
      1. A wallet label (e.g., 'premine') - resolved from ~/.animica/wallets.json
      2. A raw Animica Bech32 address (e.g., 'anim1...') - used directly
      If neither is valid, the command fails with exit code 2.
    
    Device Selection:
      The --device flag specifies the mining backend to use (CLI-only, not sent to RPC):
      - cpu: CPU backend (pure Python, always available)
      - cuda: NVIDIA CUDA backend (requires CUDA-capable GPU)
      - rocm: AMD ROCm backend (requires ROCm-capable GPU)
      - opencl: OpenCL backend (requires OpenCL-capable device)
      - metal: Apple Metal backend (requires Metal-capable device)
      - auto: Automatically select best available device (default)
      
      When 'auto' is selected (or no device specified), the system automatically detects
      and uses the best available device in priority order: CUDA > ROCm > OpenCL > Metal > CPU.
      Falls back to CPU if no GPU is detected or if detection fails.
      
      Note: Device selection is a local CLI feature for future use. The RPC node
      handles mining execution and does not receive the device parameter.
      
      Default is 'auto'. Can also be set via ANIMICA_MINER_DEVICE environment variable.
      The --gpu flag is a shortcut for --device cuda.

    Threads:
      Use --threads to control the number of CPU workers for PoW search.
      Set --threads 0 to auto-detect a reasonable default (CPU count minus one).
    
    The mining process:
    1. Selects pending transactions from mempool (nonce-ordered, fee policy enforced)
    2. Executes transactions to update state (balances, nonces)
    3. Iterates through nonces to find a valid block hash
    4. Includes transactions and receipts in the mined block
    5. Credits the block reward to the payout address
    6. Removes included transactions from the mempool
    
    P2P-First Mining (default):
      By default, mining uses local node validation via P2P consensus (no proxy).
      The node syncs state with peers and validates blocks locally.
      
    Legacy Proxy Mode (DEPRECATED):
      Use --use-proxy to enable the legacy proxy (requires ANIMICA_TRUSTED_RPC_URL):
      - Forwards requests to external endpoint (NOT recommended for production)
      - Automatically retries on transient failures (3 attempts by default)
      - Falls back to local node if external endpoint is unreachable
      - Only for specialized testing scenarios
    
    Persistence:
      - Chain state is stored under ~/.animica/chain-{chain_id}/ by default
      - Use ANIMICA_RPC_DB_URI to specify a custom database location
    
    Difficulty:
      - Target is calculated from the network's theta (acceptance threshold)
      - Set ANIMICA_MINER_MAX_NONCE to limit nonce iterations (default: 100000)
      - Higher theta means harder mining (lower target)
    
    Examples:
        # Mine 5 blocks to a wallet label (uses local P2P validation by default)
        animica miner mine-blocks --count 5 premine
        
        # Mine with --address option (backward compatible)
        animica miner mine-blocks --address premine --count 5
        
        # Mine to a bech32 address with verbose output
        animica miner mine-blocks --count 10 --verbose anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz
        
        # Mine with custom RPC endpoint (local P2P validation)
        animica miner mine-blocks --address premine --count 10 --rpc-url http://localhost:8545
        
        # Mine with CUDA backend
        animica miner mine-blocks --address premine --count 5 --device cuda

        # Mine with the CUDA shortcut
        animica miner mine-blocks --address premine --count 5 --gpu

        # Mine with 4 CPU threads
        animica miner mine-blocks --address premine --count 5 --threads 4
        
        # Mine with auto device selection
        animica miner mine-blocks --address premine --count 5 --device auto
        
        # Mine without timeout (useful for high-load scenarios)
        animica miner mine-blocks --address premine --count 10 --no-timeout

        # Mine payout-only blocks (skip mempool)
        animica miner mine-blocks --address premine --count 3 --no-include-mempool
    
    Environment variables:
        ANIMICA_RPC_URL             - Node RPC endpoint (default: http://127.0.0.1:8545/rpc)
        ANIMICA_MINER_ADDRESS       - Default payout address if --address not specified
        ANIMICA_MINER_DEVICE        - Default mining device (default: cpu)
        ANIMICA_MINER_MAX_NONCE     - Max nonce iterations per block (default: 100000)
        ANIMICA_TRUSTED_RPC_URL     - (DEPRECATED) External proxy endpoint (only for --use-proxy)
        ANIMICA_PROXY_MAX_RETRIES   - (DEPRECATED) Max proxy retries (default: 3)
        ANIMICA_PROXY_RETRY_DELAY_MS - (DEPRECATED) Delay between retries in ms (default: 1000)
    
    Note: For backward compatibility with older nodes, if the node doesn't support
    payout address selection, blocks will be mined to the node's default miner address.
    """
    # Note: This repository uses a custom stub implementation of Typer
    # (see python/typer/__init__.py) that doesn't automatically parse type annotations.
    # The stub Typer passes string values for integer options, so we need to convert manually.
    # This is intentional to keep the stub lightweight and avoid external dependencies.
    # When using the real Typer library, this conversion would be automatic.
    if isinstance(count, str):
        try:
            count = int(count)
        except ValueError:
            typer.secho(
                f"Error: count must be a valid integer, got {count}",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(2)
    if isinstance(threads, str):
        try:
            threads = int(threads)
        except ValueError:
            typer.secho(
                f"Error: threads must be a valid integer, got {threads}",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(2)

    if threads < 0:
        typer.secho(
            f"Error: threads must be >= 0, got {threads}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2)
    
    # Validate device parameter
    if gpu:
        device_normalized = "cuda"
    else:
        device_normalized = device.strip().lower() if isinstance(device, str) else "cpu"
    
    if device_normalized not in SUPPORTED_DEVICES:
        typer.secho(
            f"Error: unsupported device '{device}'. "
            f"Supported devices: {', '.join(SUPPORTED_DEVICES)}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2)
    
    # Auto-detect device if requested
    if device_normalized == "auto":
        try:
            import sys
            # Add mining module to path if needed
            repo_root = Path(__file__).resolve().parents[3]
            if str(repo_root) not in sys.path:
                sys.path.insert(0, str(repo_root))
            
            from mining.device import auto_detect_device
            
            device_normalized = auto_detect_device()
            typer.secho(
                f"✓ Auto-detected device: {device_normalized}",
                fg=typer.colors.GREEN,
            )
        except Exception as e:
            typer.secho(
                f"Warning: Could not auto-detect device ({e}). Falling back to CPU.",
                fg=typer.colors.YELLOW,
            )
            device_normalized = "cpu"

    from mining.parallel_nonce_search import resolve_worker_count

    resolved_workers = resolve_worker_count(threads)
    
    # Validate count
    if count <= 0:
        typer.secho(
            f"Error: count must be greater than 0, got {count}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2)
    
    # Resolve address: positional takes precedence over --address option
    # Strip once and reuse; treat empty strings as None
    address_stripped = address.strip() if address and address.strip() else None
    address_opt_stripped = address_opt.strip() if address_opt and address_opt.strip() else None
    final_address = address_stripped or address_opt_stripped
    
    # Validate and resolve address (label or raw Bech32)
    if not final_address:
        typer.secho(
            "Error: address is required (provide as positional arg or --address option)",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2)
    
    # Resolve address from label or validate raw Bech32 address
    resolved_address = _resolve_payout_address(final_address)
    
    # Resolve RPC URL
    url = rpc_url or os.environ.get("ANIMICA_RPC_URL") or load_network_config().rpc_url
    guard_bootstrap_rpc(url, allow_remote=allow_remote_rpc, method="miner.getBlockTemplate")
    behind = _warn_if_unsynced(url)
    if behind:
        typer.secho(
            "Error: refusing to mine while behind the network.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)
    
    # Initialize proxy if enabled (DEPRECATED - proxy is disabled by default)
    proxy = None
    if use_proxy:
        try:
            import sys
            # Add rpc module to path if needed
            repo_root = Path(__file__).resolve().parents[3]
            if str(repo_root) not in sys.path:
                sys.path.insert(0, str(repo_root))
            
            from rpc.proxy import create_proxy, ProxyConfig
            
            # Try to create proxy - will fail if ANIMICA_TRUSTED_RPC_URL is not set
            proxy = create_proxy()
            
            typer.secho(
                f"⚠ DEPRECATED: Proxy mode enabled - forwarding to {proxy.config.trusted_rpc_url}",
                fg=typer.colors.YELLOW,
            )
            typer.secho(
                "  WARNING: Proxy is for testing only. Use P2P networking for production.",
                fg=typer.colors.YELLOW,
            )
            if verbose:
                typer.echo(
                    f"  Max retries: {proxy.config.max_retries}, "
                    f"Retry delay: {proxy.config.retry_delay_ms}ms, "
                    f"Timeout: {proxy.config.timeout_seconds}s"
                )
        except ValueError as e:
            # Proxy not configured (expected - it's disabled by default)
            typer.secho(
                f"Error: Proxy not configured. {e}",
                fg=typer.colors.RED,
                err=True,
            )
            typer.secho(
                "To use proxy: export ANIMICA_TRUSTED_RPC_URL=<endpoint>",
                fg=typer.colors.YELLOW,
                err=True,
            )
            raise typer.Exit(1)
        except ImportError as e:
            typer.secho(
                f"Error: Could not load proxy module ({e}). Mining directly to {url}",
                fg=typer.colors.YELLOW,
            )
            proxy = None
    
    # Try to import RPC client
    rpc_client = None
    try:
        from sdk.python.omni_sdk.rpc.http import RpcClient
        rpc_client = RpcClient
    except (ImportError, ModuleNotFoundError, RuntimeError):
        try:
            from omni_sdk.rpc.http import RpcClient  # type: ignore
            rpc_client = RpcClient
        except (ImportError, ModuleNotFoundError, RuntimeError):
            pass
    
    if rpc_client is None:
        typer.secho(
            "Error: RpcClient not available. Please install omni_sdk: pip install -e sdk/python",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(3)
    
    mode_str = "with DEPRECATED proxy" if proxy else "with local P2P validation"
    typer.echo(
        f"Mining {count} block(s) {mode_str} with payout to address {resolved_address} via RPC {url}"
    )
    typer.secho(
        f"Using device: {device_normalized}",
        fg=typer.colors.CYAN,
    )
    typer.echo(
        f"Using {resolved_workers} CPU thread(s) for PoW search",
    )
    
    if no_timeout:
        typer.secho(
            "⚠ RPC timeout disabled - operations will wait indefinitely",
            fg=typer.colors.YELLOW,
        )
    
    # Import time for sleep between blocks
    import time
    
    # CLI-only throttling: minimum interval between blocks (not consensus-related)
    # This ensures we don't overwhelm the node when mining multiple blocks.
    # The value is based on target_block_interval_ms from params (2000ms = 2s).
    # Note: This is a fixed delay for simplicity in the CLI. The actual consensus
    # retargeting is handled by the node's PoIES implementation.
    MIN_BLOCK_INTERVAL_SECONDS = 2.0
    
    # JSON-RPC error code constant for invalid params (JSON-RPC 2.0 spec)
    JSONRPC_INVALID_PARAMS = -32602
    
    try:
        # Import RpcError for proper exception handling
        try:
            from omni_sdk.errors import RpcError, JsonRpcCode
        except ImportError:
            # Fallback if SDK not available or older version
            RpcError = None  # type: ignore
            JsonRpcCode = None  # type: ignore
        
        # Set timeout based on --no-timeout flag (default: no timeout)
        base_timeout = resolve_timeout("RPC timeout", None, env_var=RPC_TIMEOUT_ENV, default=DEFAULT_RPC_TIMEOUT)
        # None means no timeout (wait indefinitely)
        timeout_value = None if no_timeout else base_timeout
        
        with rpc_client(url, timeout=timeout_value) as client:
            strict_credit = _is_truthy_env("ANIMICA_MINER_STRICT_CREDIT", default=False)
            total_mined = 0
            final_height = 0
            total_reward = 0
            total_included = 0
            pending_before = 0
            aggregated_rejected: dict[str, int] = {}
            rejected_by_hash_sample: dict[str, str] = {}
            last_accepted_height: int | None = None
            
            # Mine blocks one at a time with delay between them
            # Continue until we mine the requested number of non-duplicate blocks
            # Add a safety limit to prevent infinite loops
            blocks_attempted = 0
            MAX_TOTAL_ATTEMPTS = count * 10  # Allow up to 10x attempts
            while total_mined < count and blocks_attempted < MAX_TOTAL_ATTEMPTS:
                i = blocks_attempted
                stale_attempts = 0
                submit_result = None
                _stale_cooldown_applied = False
                _block_accepted = False
                while True:
                    def _rpc_error_details(error: Exception) -> tuple[int | None, str, object | None]:
                        code = getattr(error, "code", None)
                        message = getattr(error, "message", None) or str(error)
                        data = getattr(error, "data", None)
                        return code, message, data

                    def _rpc_error_detail_text(message: str, data: object | None) -> str:
                        detail = ""
                        if isinstance(data, dict):
                            detail = str(
                                data.get("detail")
                                or data.get("reason")
                                or data.get("message")
                                or ""
                            )
                        return f"{message} {detail}".strip().lower()

                    def _apply_stale_template_cooldown() -> None:
                        """Apply cooldown period after exhausting stale template retries."""
                        cooldown_seconds = MIN_BLOCK_INTERVAL_SECONDS * 2
                        typer.secho(
                            f"  Exhausted stale template retries. Waiting {cooldown_seconds}s for blockchain to stabilize...",
                            fg=typer.colors.YELLOW,
                        )
                        time.sleep(cooldown_seconds)

                    def _emit_stale_diff_debug() -> None:
                        try:
                            head_now = client.request("chain.getHead", [])
                        except Exception:
                            head_now = {}
                        if not isinstance(head_now, dict):
                            head_now = {}

                        template_id_dbg = (
                            summary.get("template", {}).get("id")
                            or template.get("id")
                            or template_view.get("templateId")
                            or template_view.get("jobId")
                        )
                        template_height_dbg = (
                            template.get("height")
                            or template.get("number")
                            or template_view.get("height")
                            or template_view.get("number")
                        )
                        template_parent_dbg = (
                            template.get("parent", {}).get("hash")
                            or template.get("parent_hash")
                            or template_view.get("parentHash")
                            or template_view.get("parent_hash")
                        )
                        head_height_dbg = head_now.get("height")
                        if head_height_dbg is None:
                            head_height_dbg = head_now.get("number")

                        typer.secho(
                            "  STALE_DIFF: "
                            f"template_id={template_id_dbg} "
                            f"template_height={template_height_dbg} "
                            f"template_parent={template_parent_dbg} "
                            f"head_height={head_height_dbg} "
                            f"head_hash={head_now.get('hash')}",
                            fg=typer.colors.YELLOW,
                        )

                    def _handle_template_rpc_error(error: Exception) -> None:
                        code, message, data = _rpc_error_details(error)
                        detail_text = _rpc_error_detail_text(message, data)
                        if code == -32601:
                            typer.secho(
                                "Error: Your node is missing mining RPC methods; node needs miner.getWork + miner.submitWork.",
                                fg=typer.colors.RED,
                                err=True,
                            )
                            raise typer.Exit(5)
                        if code == -32603:
                            detail = message
                            if isinstance(data, dict):
                                detail = str(
                                    data.get("detail")
                                    or data.get("reason")
                                    or data.get("message")
                                    or message
                                )
                            typer.secho(
                                f"Error: miner.getWork failed with internal error: {detail}",
                                fg=typer.colors.RED,
                                err=True,
                            )
                            typer.secho(
                                "Check node logs for the full stack trace (rpc/jsonrpc).",
                                fg=typer.colors.YELLOW,
                                err=True,
                            )

                    def get_template_via_local():
                        if verbose:
                            typer.echo(f"  [Fallback] Fetching work via local RPC at {url}")

                        attempts = [
                            [resolved_address],
                            {"address": resolved_address},
                            {"payout_address": resolved_address},
                            {"payoutAddress": resolved_address},
                            [resolved_address, include_mempool],
                        ]

                        last_exc = None
                        for payload in attempts:
                            try:
                                return client.request("miner.getWork", payload)
                            except Exception as exc:
                                last_exc = exc
                                code, message, data = _rpc_error_details(exc)
                                detail_text = _rpc_error_detail_text(message, data)
                                if code == -32602 and any(
                                    token in detail_text
                                    for token in (
                                        "unexpected",
                                        "unknown",
                                        "keyword",
                                        "too many positional arguments",
                                        "missing",
                                        "address",
                                        "payout_address",
                                        "payoutAddress",
                                    )
                                ):
                                    continue
                                _handle_template_rpc_error(exc)
                                raise

                        if last_exc is not None:
                            _handle_template_rpc_error(last_exc)
                            raise last_exc

                    if proxy:
                        if verbose:
                            typer.echo("  [Proxy] Forwarding work request to trusted RPC")
                        template = proxy.sync_forward_request(
                            "miner.getWork",
                            {
                                "address": resolved_address,
                                "include_mempool": include_mempool,
                                "ttlSeconds": int(template_ttl_s),
                            },
                            fallback_handler=get_template_via_local,
                        )
                    else:
                        template = get_template_via_local()

                    if (
                        not isinstance(template, dict)
                        or not template.get("enabled", True)
                    ):
                        reason = (
                            template.get("reason")
                            if isinstance(template, dict)
                            else "unknown"
                        )
                        
                        # NEW: Handle execution head lagging (wait and retry)
                        # This is the only reason we should wait-and-retry
                        if (
                            isinstance(reason, str)
                            and reason.startswith("exec_head_lagging:")
                        ):
                            # Extract lag info from reason string (format: "exec_head_lagging:N_blocks")
                            lag_blocks = reason.split(":")[-1].replace("_blocks", "")
                            typer.secho(
                                f"Info: Execution head is lagging by {lag_blocks}; "
                                f"waiting for block execution to catch up...",
                                fg=typer.colors.YELLOW,
                            )
                            time.sleep(MIN_BLOCK_INTERVAL_SECONDS)
                            continue

                        if isinstance(reason, str) and reason == "min_block_spacing":
                            wait_s = (
                                float(template.get("waitSeconds", MIN_BLOCK_INTERVAL_SECONDS))
                                if isinstance(template, dict)
                                else float(MIN_BLOCK_INTERVAL_SECONDS)
                            )
                            wait_s = max(0.05, wait_s)
                            typer.secho(
                                f"Info: Waiting {wait_s:.3f}s for min block spacing...",
                                fg=typer.colors.YELLOW,
                            )
                            time.sleep(wait_s)
                            continue
                        
                        # REMOVED: sync_phase:* wait loop (lines 1218-1228 in original)
                        # The node no longer blocks templates based on sync_phase.
                        # If we get here, template is genuinely unavailable (not just "syncing headers").
                        
                        # Template unavailable for other reason - stop mining this iteration
                        if (
                            not isinstance(template, dict)
                            or not template.get("enabled", True)
                        ):
                            reason = (
                                template.get("reason")
                                if isinstance(template, dict)
                                else reason
                            )
                            typer.secho(
                                f"Warning: Block template unavailable ({reason})",
                                fg=typer.colors.YELLOW,
                            )
                            blocks_attempted += 1
                            stale_attempts = 0
                            break

                    mempool_info = template.get("mempool", {}) if isinstance(template, dict) else {}
                    pending_current = int(
                        mempool_info.get("pending", mempool_info.get("mempoolTotal", 0) or 0)
                    )
                    pending_before = pending_before or pending_current
                    selected = int(mempool_info.get("selected", 0) or 0)
                    total_included += selected
                    rejected = mempool_info.get("rejected", {})
                    if isinstance(rejected, dict):
                        for reason, count in rejected.items():
                            aggregated_rejected[reason] = aggregated_rejected.get(reason, 0) + int(count)
                    rejected_by_hash = mempool_info.get("rejectedByHash", {})
                    if isinstance(rejected_by_hash, dict):
                        for tx_hash, reason in rejected_by_hash.items():
                            if tx_hash not in rejected_by_hash_sample:
                                rejected_by_hash_sample[tx_hash] = str(reason)
                            if len(rejected_by_hash_sample) >= 10:
                                break
                    if include_mempool:
                        rejected_total = (
                            sum(int(value) for value in rejected.values())
                            if isinstance(rejected, dict)
                            else 0
                        )
                        top_reasons = ""
                        if isinstance(rejected, dict) and rejected:
                            top_reasons = ", ".join(
                                f"{reason}={count}"
                                for reason, count in sorted(rejected.items())
                            )
                        else:
                            top_reasons = "none"
                        typer.echo(
                            "  Template: mempool_total="
                            f"{pending_current} included={selected} rejected={rejected_total} "
                            f"(top reasons: {top_reasons})"
                        )

                    header_view = template.get("header", {})
                    header = header_from_template_view(header_view)
                    target_hex = template.get("target")
                    target_int = int(target_hex, 16) if isinstance(target_hex, str) else int(target_hex or 0)
                    nonce, digest = _mine_header(
                        header,
                        target_int,
                        workers=resolved_workers,
                    )
                    if nonce is None or digest is None:
                        typer.secho(
                            f"Warning: Block {total_mined + 1}/{count} failed to find PoW",
                            fg=typer.colors.YELLOW,
                        )
                        typer.secho(
                            "Hint: Increase ANIMICA_MINER_MAX_NONCE or "
                            "ANIMICA_MINER_MAX_TOTAL_NONCE for more PoW attempts.",
                            fg=typer.colors.YELLOW,
                        )
                        blocks_attempted += 1
                        stale_attempts = 0
                        break
                    
                    # PoW FOUND - hash meets target
                    digest_int = int.from_bytes(digest, "big")
                    pow_valid = digest_int <= target_int
                    display_height = header.height
                    if last_accepted_height is not None and display_height <= last_accepted_height:
                        display_height = last_accepted_height + 1
                    typer.secho(
                        f"  FOUND: Block {total_mined + 1}/{count} PoW (height: {display_height}, "
                        f"nonce: {nonce}, hash: 0x{digest.hex()[:16]}...)",
                        fg=typer.colors.CYAN,
                    )

                    parent_info = template.get("parent", {}) if isinstance(template, dict) else {}
                    parent_hash = parent_info.get("hash") or template.get("parentHash")
                    if not parent_hash:
                        parent_hash = "0x" + header.parentHash.hex()

                    try:
                        head_snapshot = client.request("chain.getHead", [])
                    except Exception as exc:
                        if verbose:
                            typer.secho(
                                f"  Warning: Unable to verify head before submit ({exc})",
                                fg=typer.colors.YELLOW,
                            )
                        head_snapshot = None

                    head_hash = None
                    head_height_pre_submit = None
                    if isinstance(head_snapshot, dict):
                        head_hash = (
                            head_snapshot.get("hash")
                            or head_snapshot.get("block_hash")
                            or head_snapshot.get("head")
                        )
                        head_height_pre_submit = (
                            head_snapshot.get("height")
                            or head_snapshot.get("number")
                            or head_snapshot.get("block_number")
                        )

                    if verbose:
                        typer.echo(
                            "  pre-submit check: "
                            f"head_hash={head_hash} "
                            f"head_height={head_height_pre_submit} "
                            f"job_parent={parent_hash} "
                            f"template_height={header.height}"
                        )

                    # Do not reject locally here.
                    # Let miner.submitWork be the source of truth for stale-vs-accepted.

                    header = hash_candidate_header(header, nonce=nonce).header
                    parent_height = parent_info.get("height")
                    block_payload = build_submit_block_payload(template, nonce=nonce)
                    template_id = block_payload.get("templateId")
                    parent_hash = block_payload.get("parentHash") or parent_hash

                    summary = {
                        "template": {
                            "id": template_id,
                            "parent_hash": parent_hash,
                            "parent_height": parent_height,
                            "target": target_hex,
                            "timestamp_min": template.get("timestampMin"),
                            "timestamp_max": template.get("timestampMax"),
                        },
                        "header": {
                            "height": header.height,
                            "parent": "0x" + header.parentHash.hex(),
                            "timestamp": header.timestamp,
                            "theta_micro": header.thetaMicro,
                            "nonce": nonce,
                        },
                        "pow": {
                            "hash": "0x" + digest.hex(),
                            "valid": pow_valid,
                        },
                    }
                    _emit_mining_summary(summary, verbose=verbose)

                    # Submit block to node
                    balance_before_submit = None
                    try:
                        bal_hex = client.request("state.getBalance", [resolved_address])
                        balance_before_submit = int(bal_hex, 16) if isinstance(bal_hex, str) else int(bal_hex)
                    except Exception:
                        balance_before_submit = None

                    try:
                        submit_payload = {
                            "jobId": (
                                summary.get("template", {}).get("id")
                                or template.get("id")
                                or template_view.get("templateId")
                                or template_view.get("jobId")
                            ),
                            "nonce": nonce,
                        }

                        if verbose:
                            typer.echo(f"  submitWork payload: {submit_payload}")

                        if proxy:
                            submit_result = proxy.sync_forward_request(
                                "miner.submitWork",
                                submit_payload,
                                fallback_handler=lambda: client.request("miner.submitWork", submit_payload),
                            )
                        else:
                            submit_result = client.request("miner.submitWork", submit_payload)
                    except Exception as submit_error:
                        error_str = _format_rpc_error(submit_error)
                        error_data = getattr(submit_error, "data", None)
                        rejection_reason = None
                        if isinstance(error_data, dict):
                            rejection_reason = error_data.get("reason")

                        is_stale = (
                            isinstance(rejection_reason, str) and "stale" in rejection_reason.lower()
                        ) or ("stale" in error_str.lower())

                        if is_stale:
                            _emit_stale_diff_debug()

                        _emit_mining_summary(summary, verbose=verbose, force=True)
                        typer.secho(
                            f"  REJECTED: Block {total_mined + 1}/{count} (reason: {rejection_reason or error_str})",
                            fg=typer.colors.RED,
                        )

                        if is_stale and stale_attempts < 1:
                            stale_attempts += 1
                            typer.secho(
                                f"  Retrying with fresh template (stale attempt {stale_attempts}/1)",
                                fg=typer.colors.YELLOW,
                            )
                            continue

                        if is_stale:
                            _apply_stale_template_cooldown()
                            _stale_cooldown_applied = True

                        blocks_attempted += 1
                        stale_attempts = 0
                        break

                    if not submit_result or not submit_result.get("accepted", False):
                        rejection_reason = submit_result.get("reason") if isinstance(submit_result, dict) else None
                        is_stale = isinstance(rejection_reason, str) and "stale" in rejection_reason.lower()

                        if is_stale:
                            _emit_stale_diff_debug()

                        _emit_mining_summary(summary, verbose=verbose, force=True)
                        typer.secho(
                            f"  REJECTED: Block {total_mined + 1}/{count} by node (reason: {rejection_reason})",
                            fg=typer.colors.RED,
                        )

                        if is_stale and stale_attempts < 1:
                            stale_attempts += 1
                            typer.secho(
                                f"  Retrying with fresh template (stale attempt {stale_attempts}/1)",
                                fg=typer.colors.YELLOW,
                            )
                            continue

                        if is_stale:
                            _apply_stale_template_cooldown()
                            _stale_cooldown_applied = True

                        blocks_attempted += 1
                        stale_attempts = 0
                        break

                    # Check if block is a duplicate (already found by another miner)
                    is_duplicate = submit_result.get("duplicate", False)
                    
                    if is_duplicate:
                        # Block was already found - skip it and continue mining
                        typer.secho(
                            f"  DUPLICATE: Block already found by another miner (skipping, progress: {total_mined}/{count})",
                            fg=typer.colors.YELLOW,
                        )
                        # Don't count this in total_mined, just move to next iteration
                        blocks_attempted += 1
                        stale_attempts = 0
                        break
                    
                    # ACCEPTED - block fully validated and committed to canonical state
                    total_mined += 1
                    blocks_attempted += 1
                    _block_accepted = True

                    # Re-read canonical head after acceptance and wait for the miner work
                    # template to advance past the block we just accepted.
                    head_after = {}
                    accepted_parent_hash = parent_hash
                    accepted_height = int(getattr(header, "height", 0) or 0)
                    accepted_deadline = time.time() + 8.0

                    while time.time() < accepted_deadline:
                        try:
                            candidate_head = client.request("chain.getHead", [])
                            if not isinstance(candidate_head, dict):
                                candidate_head = {}
                        except Exception:
                            candidate_head = {}

                        candidate_hash = (
                            candidate_head.get("hash")
                            or candidate_head.get("block_hash")
                            or candidate_head.get("head")
                        )
                        candidate_height = (
                            candidate_head.get("height")
                            or candidate_head.get("number")
                            or candidate_head.get("block_number")
                            or 0
                        )

                        try:
                            candidate_height = int(candidate_height or 0)
                        except Exception:
                            candidate_height = 0

                        template_advanced = False
                        try:
                            next_work = client.request("miner.getWork", [resolved_address])
                            if isinstance(next_work, dict):
                                next_height = next_work.get("height")
                                if next_height is None and isinstance(next_work.get("header"), dict):
                                    next_height = (
                                        next_work["header"].get("height")
                                        or next_work["header"].get("number")
                                    )
                                next_parent = (
                                    next_work.get("parentHash")
                                    or ((next_work.get("parent") or {}).get("hash") if isinstance(next_work.get("parent"), dict) else None)
                                )
                                try:
                                    next_height_int = int(next_height or 0)
                                except Exception:
                                    next_height_int = 0

                                if next_height_int >= accepted_height + 1:
                                    template_advanced = True

                                if verbose:
                                    typer.echo(
                                        "  post-accept settle: "
                                        f"head_hash={candidate_hash} "
                                        f"head_height={candidate_height} "
                                        f"next_height={next_height_int} "
                                        f"next_parent={next_parent}"
                                    )
                        except Exception:
                            next_work = None

                        if (
                            (candidate_hash and accepted_parent_hash and str(candidate_hash).lower() != str(accepted_parent_hash).lower())
                            or candidate_height >= accepted_height
                            or template_advanced
                        ):
                            head_after = candidate_head
                            break

                        time.sleep(0.25)

                    if not isinstance(head_after, dict) or not head_after:
                        try:
                            head_after = client.request("chain.getHead", [])
                            if not isinstance(head_after, dict):
                                head_after = {}
                        except Exception:
                            head_after = {}

                    try:
                        final_height = int(
                            head_after.get("height")
                            or head_after.get("number")
                            or head_after.get("block_number")
                            or accepted_height
                        )
                    except Exception:
                        final_height = accepted_height

                    if final_height > 0:
                        last_accepted_height = final_height

                    new_head_hash = (
                        head_after.get("hash")
                        or head_after.get("block_hash")
                        or (
                            submit_result.get("newHead", {}).get("hash")
                            if isinstance(submit_result.get("newHead"), dict)
                            else None
                        )
                        or submit_result.get("new_head_hash")
                        or submit_result.get("block_hash")
                    )

                    balance_now = None
                    credited_delta = None
                    try:
                        balance_now_hex = client.request("state.getBalance", [resolved_address])
                        balance_now = int(balance_now_hex, 16) if isinstance(balance_now_hex, str) else int(balance_now_hex)
                    except Exception:
                        balance_now = submit_result.get("balance_now")
                        if balance_now is not None:
                            try:
                                balance_now = int(balance_now)
                            except Exception:
                                balance_now = None

                    if isinstance(balance_now, int) and isinstance(balance_before_submit, int):
                        credited_delta = balance_now - balance_before_submit

                    credit_height = final_height if final_height > 0 else getattr(header, "height", None)
                    credit_record = _lookup_recent_mining_credit(
                        client,
                        resolved_address,
                        credit_height,
                    )

                    credited_display = None
                    credited_amount_raw = submit_result.get("credited_amount")
                    if credited_amount_raw is not None:
                        try:
                            credited_display = int(credited_amount_raw)
                        except Exception:
                            credited_display = None

                    if credited_display is None and isinstance(credited_delta, int) and credited_delta > 0:
                        credited_display = int(credited_delta)

                    if credited_display is None:
                        try:
                            credited_from_submit_delta = submit_result.get("credited_delta")
                            if credited_from_submit_delta is not None:
                                credited_candidate = int(credited_from_submit_delta)
                                if credited_candidate >= 0:
                                    credited_display = credited_candidate
                        except Exception:
                            credited_display = None

                    if credited_display is None and isinstance(credit_record, dict):
                        try:
                            credited_display = int(
                                credit_record.get("credited_reward")
                                or credit_record.get("credited_amount")
                                or 0
                            )
                        except Exception:
                            credited_display = None

                    block_reward = None
                    for candidate in (
                            submit_result.get("reward"),
                            submit_result.get("reward_nano"),
                            submit_result.get("reward_nanom"),
                            submit_result.get("expected_reward"),
                            submit_result.get("credited_amount"),
                        ):
                            if candidate is not None:
                                try:
                                    parsed = int(candidate)
                                    if parsed > 0:
                                        block_reward = parsed
                                        break
                                except Exception:
                                    pass

                    if block_reward is None and isinstance(credit_record, dict):
                        for candidate in (
                            credit_record.get("expected_reward"),
                            credit_record.get("reward"),
                            credit_record.get("credited_reward"),
                        ):
                            if candidate is not None:
                                try:
                                    parsed = int(candidate)
                                    if parsed > 0:
                                        block_reward = parsed
                                        break
                                except Exception:
                                    pass

                    if block_reward is None and isinstance(credited_delta, int) and credited_delta > 0:
                        block_reward = int(credited_delta)

                    if block_reward is not None and block_reward > 0:
                        total_reward += int(block_reward)

                    reward_text = (
                        f"{(block_reward / COIN_UNIT):.9f} ANM = {block_reward} nANM"
                        if isinstance(block_reward, int) and block_reward > 0
                        else "unknown"
                    )
                    credited_text = (
                        f"{credited_display} nANM"
                        if isinstance(credited_display, int) and credited_display >= 0
                        else "unknown"
                    )

                    typer.secho(
                        f"  ACCEPTED: Block {total_mined}/{count} (height: {final_height}, "
                        f"head: {new_head_hash or 'unknown'}, "
                        f"reward: {reward_text}, "
                        f"credited: {credited_text}, "
                        f"balance_now: {balance_now if balance_now is not None else 'unknown'})",
                        fg=typer.colors.GREEN,
                        bold=True,
                    )

                    if strict_credit and isinstance(block_reward, int) and block_reward > 0:
                        if balance_now is None or credited_display is None or credited_display < block_reward:
                            typer.secho(
                                "  WARNING: strict credit check failed; RPC balance/credits did not reflect expected reward delta",
                                fg=typer.colors.RED,
                                err=True,
                            )
                            raise typer.Exit(1)

                    if include_mempool and pending_before > 0 and selected == 0:
                        exclusions = template.get("excluded", []) or []
                        if exclusions:
                            typer.secho(
                                f"Mempool had {pending_before} tx(s) but none were mineable:",
                                fg=typer.colors.YELLOW,
                            )
                            for entry in exclusions[:5]:
                                reason = entry.get("reason", "unknown")
                                details = entry.get("details")
                                if details:
                                    typer.echo(f"  {entry.get('hash')}: {reason} {details}")
                                else:
                                    typer.echo(f"  {entry.get('hash')}: {reason}")
                        try:
                            pending_hashes = client.request("mempool.getPending", [])
                            for tx_hash in pending_hashes[:5]:
                                explain = client.request("mempool.explain", [tx_hash])
                                typer.echo(f"  explain {tx_hash}: {explain.get('reason')}")
                        except Exception:
                            pass

                    break

                # Continue to next block even if this one failed after retries
                # The inner loop has already handled retry logic (up to 1 attempt for stale)
                # and decided to break, so we just move on to the next block in the sequence

                # Sleep between attempts to avoid overwhelming the node.
                # - Skip if a stale cooldown was already applied (it already served as the delay)
                # - Sleep only when a block was accepted (pacing between accepted blocks) or when the
                #   attempt did not result in acceptance (light throttle on persistent errors)
                # - For generic non-stale errors: use a shorter delay to fail fast
                if total_mined < count and not _stale_cooldown_applied:
                    if _block_accepted:
                        time.sleep(max(1.0, MIN_BLOCK_INTERVAL_SECONDS))
                    else:
                        # Short delay to avoid hammering the node on repeated errors
                        time.sleep(0.1)
            
            # Check if we exceeded maximum attempts
            if blocks_attempted >= MAX_TOTAL_ATTEMPTS and total_mined < count:
                typer.secho(
                    f"Warning: Reached maximum attempt limit ({MAX_TOTAL_ATTEMPTS}) without mining {count} blocks",
                    fg=typer.colors.YELLOW,
                )
            
            if total_mined == 0:
                typer.secho(
                    "Warning: No blocks were mined (may have failed)",
                    fg=typer.colors.YELLOW,
                )
                raise typer.Exit(4)
            elif total_mined < count:
                typer.secho(
                    f"Warning: Only {total_mined} of {count} requested blocks were mined",
                    fg=typer.colors.YELLOW,
                )
            
            # Display total reward summary
            total_reward_text = (
                f"{(total_reward / COIN_UNIT):.9f} ANM ({total_reward} nANM)"
                if total_reward > 0
                else "unknown"
            )
            typer.secho(
                f"✓ Successfully mined {total_mined} block(s). "
                f"New chain height: {final_height}. "
                f"Total reward: {total_reward_text}",
                fg=typer.colors.GREEN,
                bold=True,
            )

            typer.echo(f"Included mempool txs: {total_included}")
            if include_mempool and total_included == 0 and pending_before > 0:
                typer.secho(
                    f"Note: {pending_before} pending txs were excluded from block assembly.",
                    fg=typer.colors.YELLOW,
                )
                if aggregated_rejected:
                    summary_parts = [
                        f"{reason}={count}" for reason, count in sorted(aggregated_rejected.items())
                    ]
                    typer.echo(f"Exclusion summary: {', '.join(summary_parts)}")
                if rejected_by_hash_sample:
                    typer.echo("Sample exclusions:")
                    for tx_hash, reason in list(rejected_by_hash_sample.items())[:5]:
                        typer.echo(f"  {tx_hash}: {reason}")
    
    except typer.Exit:
        raise
    except (RuntimeError, ConnectionError, OSError, TimeoutError) as e:
        typer.secho(
            f"Error: Failed to connect to RPC: {e}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(5)
    except Exception as e:
        error_str = str(e)
        typer.secho(
            f"Error: Failed to mine blocks via RPC: {error_str}",
            fg=typer.colors.RED,
            err=True,
        )
        # Provide hint about --no-timeout if this is a timeout error
        # Check for timeout indicators: RpcError with code -32098 or "timed out" in message
        is_timeout = False
        if RpcError is not None and isinstance(e, RpcError):
            # Check if this is a timeout error (code -32098 with timeout message)
            is_timeout = e.code == -32098 and "timed out" in error_str.lower()
        elif "timed out" in error_str.lower():
            # Fallback: check error message for timeout indication
            is_timeout = True
        
        if is_timeout and not no_timeout:
            typer.secho(
                "Hint: For long-running operations, consider using --no-timeout flag",
                fg=typer.colors.YELLOW,
                err=True,
            )
        raise typer.Exit(5)


@app.command("credits")
def show_mining_credits(
    address: Optional[str] = typer.Option(
        None,
        "--address",
        help="Filter by miner address (wallet label or Bech32 address)",
    ),
    last: int = typer.Option(
        50,
        "--last",
        help="Show last N records (default: 50)",
    ),
    from_height: Optional[int] = typer.Option(
        None,
        "--from-height",
        help="Filter by minimum block height",
    ),
    to_height: Optional[int] = typer.Option(
        None,
        "--to-height",
        help="Filter by maximum block height",
    ),
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="Node JSON-RPC endpoint URL",
        envvar="ANIMICA_RPC_URL",
    ),
    format: str = typer.Option(
        "table",
        "--format",
        help="Output format: table (default), json, or csv",
    ),
) -> None:
    """
    Show mining credits audit trail.
    
    Displays a record of locally mined blocks with expected vs credited rewards.
    Useful for debugging and verifying that mining rewards are being credited correctly.
    
    Examples:
        # Show last 50 mined blocks
        animica miner credits
        
        # Show credits for a specific address
        animica miner credits --address premine
        
        # Show last 100 blocks
        animica miner credits --last 100
        
        # Show blocks in a height range
        animica miner credits --from-height 100 --to-height 200
        
        # Output as JSON
        animica miner credits --format json
    """
    # Resolve RPC URL
    url = rpc_url or os.environ.get("ANIMICA_RPC_URL") or load_network_config().rpc_url
    
    # Resolve address if it's a wallet label
    resolved_address = None
    if address:
        try:
            resolved_address = _resolve_payout_address(address)
        except typer.Exit:
            # Address resolution failed, use as-is (might be hex address)
            resolved_address = address
    
    try:
        # Call RPC method
        params = {}
        if resolved_address:
            params["address"] = resolved_address
        if from_height is not None:
            params["from_height"] = from_height
        if to_height is not None:
            params["to_height"] = to_height
        if last is not None:
            params["last"] = last
        
        result = call_rpc("mining.getCredits", params, url)
        
        if not isinstance(result, dict):
            typer.secho(
                f"Error: Unexpected response format: {result}",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(1)
        
        credits = result.get("credits", [])
        count = result.get("count", 0)
        
        if count == 0:
            typer.echo("No mining credits found matching the filters.")
            return
        
        # Output based on format
        if format == "json":
            typer.echo(json.dumps(result, indent=2))
        elif format == "csv":
            # CSV format
            if credits:
                typer.echo("height,hash,miner_address,expected_reward,credited_reward,timestamp")
                for credit in credits:
                    typer.echo(
                        f"{credit['height']},"
                        f"{credit['hash']},"
                        f"{credit['miner_address']},"
                        f"{credit['expected_reward']},"
                        f"{credit['credited_reward']},"
                        f"{credit['timestamp']}"
                    )
        else:  # table format (default)
            typer.secho(
                f"\nMining Credits Audit Trail ({count} records)",
                fg=typer.colors.CYAN,
                bold=True,
            )
            typer.echo("=" * 80)
            
            for credit in credits:
                height = credit.get("height", 0)
                block_hash = credit.get("hash", "")
                miner_addr = credit.get("miner_address", "")
                expected = credit.get("expected_reward", 0)
                credited = credit.get("credited_reward", 0)
                timestamp_unix = credit.get("timestamp", 0)
                
                # Convert timestamp to readable format
                try:
                    from datetime import datetime
                    timestamp_str = datetime.fromtimestamp(timestamp_unix).strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    timestamp_str = str(timestamp_unix)
                
                # Convert rewards to ANM
                expected_anm = expected / COIN_UNIT
                credited_anm = credited / COIN_UNIT
                
                typer.echo(f"\nHeight: {height}")
                typer.echo(f"  Block Hash:     {block_hash}")
                typer.echo(f"  Miner Address:  {miner_addr[:42]}...")
                typer.echo(f"  Expected Reward: {expected_anm:.9f} ANM ({expected} nANM)")
                typer.echo(f"  Balance After:   {credited_anm:.9f} ANM ({credited} nANM)")
                typer.echo(f"  Timestamp:      {timestamp_str}")
                
                # Warn if there's a mismatch (balance should be >= expected for fresh addresses)
                if expected > 0 and credited == 0:
                    typer.secho(
                        "  ⚠ WARNING: Expected reward but balance is zero!",
                        fg=typer.colors.RED,
                    )
            
            typer.echo("\n" + "=" * 80)
            
            # Show filter summary
            filters_applied = result.get("filters", {})
            if any(v is not None for v in filters_applied.values()):
                typer.echo("\nFilters applied:")
                if filters_applied.get("address"):
                    typer.echo(f"  Address: {filters_applied['address']}")
                if filters_applied.get("from_height") is not None:
                    typer.echo(f"  From Height: {filters_applied['from_height']}")
                if filters_applied.get("to_height") is not None:
                    typer.echo(f"  To Height: {filters_applied['to_height']}")
                if filters_applied.get("last"):
                    typer.echo(f"  Last: {filters_applied['last']}")
    
    except Exception as e:
        typer.secho(
            f"Error: Failed to retrieve mining credits: {e}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)


if __name__ == "__main__":  # pragma: no cover
    app()
