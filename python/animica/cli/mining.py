"""Mining operations CLI for Animica.

Provides commands for:
  - Mining blocks via RPC (mine-blocks)
  - Running the Stratum pool server (run-pool)
  - Inspecting pool configuration (show-config)
  - Generating payout addresses (generate-payout-address)
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Optional

import asyncio
import logging

import typer
from animica.coin import COIN_UNIT
from animica.config import load_network_config
from animica.cli.rpc_guard import guard_bootstrap_rpc
from animica.cli.rpc import call_rpc
from .timeouts import DEFAULT_RPC_TIMEOUT, RPC_TIMEOUT_ENV, resolve_timeout

try:
    from animica.cli.wallet import (WalletEntry, _wallet_file_path,
                                    create_wallet)
    from animica.stratum_pool import cli as pool_cli
    from animica.stratum_pool.config import PoolConfig, load_config_from_env

    HAVE_STRATUM = True
except Exception:
    HAVE_STRATUM = False

app = typer.Typer(help="Mining operations and Stratum pool management.")

RPC_ENV = "ANIMICA_RPC_URL"
DB_ENV = "ANIMICA_MINING_POOL_DB_URL"
LOG_LEVEL_ENV = "ANIMICA_MINING_POOL_LOG_LEVEL"
STRATUM_BIND_ENV = "ANIMICA_STRATUM_BIND"
API_BIND_ENV = "ANIMICA_POOL_API_BIND"

# Supported mining device backends
SUPPORTED_DEVICES = ["cpu", "cuda", "rocm", "opencl", "metal", "auto"]


def _parse_hex_bytes(value: str) -> bytes:
    hex_value = value[2:] if value.startswith("0x") else value
    if len(hex_value) % 2:
        hex_value = "0" + hex_value
    return bytes.fromhex(hex_value)


def _header_from_template(header_view: dict) -> "Header":
    from core.types.header import Header

    return Header(
        v=int(header_view.get("v", 1)),
        chainId=int(header_view.get("chainId", header_view.get("chain_id", 0))),
        height=int(header_view.get("height", header_view.get("number", 0))),
        parentHash=_parse_hex_bytes(header_view["parentHash"]),
        timestamp=int(header_view.get("timestamp", 0)),
        stateRoot=_parse_hex_bytes(header_view.get("stateRoot", "0x" + "00" * 32)),
        txsRoot=_parse_hex_bytes(header_view.get("txsRoot", "0x" + "00" * 32)),
        receiptsRoot=_parse_hex_bytes(header_view.get("receiptsRoot", "0x" + "00" * 32)),
        proofsRoot=_parse_hex_bytes(header_view.get("proofsRoot", "0x" + "00" * 32)),
        daRoot=_parse_hex_bytes(header_view.get("daRoot", "0x" + "00" * 32)),
        mixSeed=_parse_hex_bytes(header_view.get("mixSeed", "0x" + "00" * 32)),
        poiesPolicyRoot=_parse_hex_bytes(
            header_view.get("poiesPolicyRoot", "0x" + "00" * 32)
        ),
        pqAlgPolicyRoot=_parse_hex_bytes(
            header_view.get("pqAlgPolicyRoot", "0x" + "00" * 32)
        ),
        thetaMicro=int(header_view.get("thetaMicro", 0)),
        workType=int(header_view.get("workType", 0)),
        nonce=int(header_view.get("nonce", 0)),
        extra=_parse_hex_bytes(header_view.get("extra", "0x")),
    )


def _mine_header(header: "Header", target_int: int) -> tuple[int | None, bytes | None]:
    max_nonce = max(1, int(os.getenv("ANIMICA_MINER_MAX_NONCE", "1000000")))
    retry_windows = max(1, int(os.getenv("ANIMICA_MINER_POW_RETRY_WINDOWS", "4")))
    default_total = max(max_nonce * retry_windows, 5_000_000)
    max_total_nonce = max(
        1,
        int(os.getenv("ANIMICA_MINER_MAX_TOTAL_NONCE", str(default_total))),
    )
    total_windows = max(retry_windows, math.ceil(max_total_nonce / max_nonce))

    def _scan_window(start_nonce: int, end_nonce: int) -> tuple[int | None, bytes | None]:
        for nonce in range(start_nonce, end_nonce):
            try:
                candidate = header.__class__(
                    v=header.v,
                    chainId=header.chainId,
                    height=header.height,
                    parentHash=header.parentHash,
                    timestamp=header.timestamp,
                    stateRoot=header.stateRoot,
                    txsRoot=header.txsRoot,
                    receiptsRoot=header.receiptsRoot,
                    proofsRoot=header.proofsRoot,
                    daRoot=header.daRoot,
                    mixSeed=header.mixSeed,
                    poiesPolicyRoot=header.poiesPolicyRoot,
                    pqAlgPolicyRoot=header.pqAlgPolicyRoot,
                    thetaMicro=header.thetaMicro,
                    workType=header.workType,
                    nonce=nonce,
                    extra=header.extra,
                )
            except Exception:
                candidate = header
            try:
                from core.types.header import serialize_header
                from core.utils.hash import sha3_256

                digest = sha3_256(serialize_header(candidate))
            except Exception:
                digest = candidate.hash()
            digest_int = int.from_bytes(digest, "big")
            if digest_int <= target_int:
                return nonce, digest
        return None, None

    start_nonce = 0
    for _ in range(max(1, total_windows)):
        nonce, digest = _scan_window(start_nonce, start_nonce + max_nonce)
        if nonce is not None and digest is not None:
            return nonce, digest
        start_nonce += max_nonce
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


def _ensure_network_env() -> None:
    cfg = load_network_config()
    os.environ.setdefault("ANIMICA_NETWORK", cfg.name)
    os.environ.setdefault(RPC_ENV, cfg.rpc_url)


def _ensure_stratum_available() -> None:
    if not HAVE_STRATUM:
        typer.echo(
            "Error: Stratum pool modules required. "
            "Ensure 'animica[stratum]' is installed.",
            err=True,
        )
        raise typer.Exit(1)


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
) -> None:
    from mining.orchestrator import MinerOrchestrator, OrchestratorConfig
    from mining.rpc_adapter import RpcTemplateProvider
    from mining.share_submitter import ShareSubmitter, SubmitterConfig

    provider = RpcTemplateProvider(rpc_url=rpc_url, proof_type=proof_type)
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
) -> None:
    """Start the Animica Stratum mining pool."""
    _ensure_stratum_available()
    _ensure_network_env()
    effective_rpc = rpc_url or os.environ.get(RPC_ENV) or load_network_config().rpc_url
    guard_bootstrap_rpc(effective_rpc, allow_remote=allow_remote_rpc, method="miner.runPool")
    env_overrides = {
        RPC_ENV: rpc_url,
        DB_ENV: db_url,
        STRATUM_BIND_ENV: stratum_bind,
        API_BIND_ENV: api_bind,
        LOG_LEVEL_ENV: log_level,
    }
    for key, value in env_overrides.items():
        if value is not None:
            os.environ[key] = value
    pool_cli.main([])


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
    _ensure_stratum_available()
    _ensure_network_env()
    # load_config_from_env is provided by animica.stratum_pool when installed
    try:
        cfg: PoolConfig = load_config_from_env()
    except Exception as e:
        typer.echo(
            "Error: could not load pool config; ensure animica[stratum] is installed",
            err=True,
        )
        raise typer.Exit(1)
    typer.echo(
        f"RPC URL: {cfg.rpc_url}\n"
        f"DB URL: {cfg.db_url}\n"
        f"Chain ID: {cfg.chain_id}\n"
        f"Pool address: {cfg.pool_address}\n"
        f"Stratum bind: {cfg.host}:{cfg.port}\n"
        f"API bind: {cfg.api_host}:{cfg.api_port}\n"
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
                                        _save_store, _wallet_file_path)

        path = _wallet_file_path(wallet_file)
        store = _load_store(path)
        entry = _generate_entry(label, allow_fallback=True)
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
    allow_offline_mining: bool = typer.Option(
        False,
        "--allow-offline-mining",
        help="Allow mining when offline or unsynced (overrides mainnet safety checks).",
    ),
    unsafe_mine_while_syncing: bool = typer.Option(
        False,
        "--unsafe-mine-while-syncing",
        help="Allow mining while the node is behind the network (unsafe on mainnet).",
    ),
) -> None:
    """
    Mine blocks with proof-of-work to a specified payout address.
    
    This command performs actual mining by iterating through nonces until finding
    block hashes that meet the current difficulty target (derived from the network's
    theta parameter). Block rewards are credited to the specified payout address.
    
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
    
    # Validate device parameter
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
    effective_allow_offline = allow_offline_mining or unsafe_mine_while_syncing
    behind = _warn_if_unsynced(url)
    if behind and not effective_allow_offline:
        typer.secho(
            "Error: refusing to mine while behind the network. "
            "Use --unsafe-mine-while-syncing to override.",
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
            total_mined = 0
            final_height = 0
            total_reward = 0
            total_included = 0
            pending_before = 0
            aggregated_rejected: dict[str, int] = {}
            rejected_by_hash_sample: dict[str, str] = {}
            
            # Mine blocks one at a time with delay between them
            for i in range(count):
                stale_attempts = 0
                submit_result = None
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

                    def _handle_template_rpc_error(error: Exception) -> None:
                        code, message, data = _rpc_error_details(error)
                        detail_text = _rpc_error_detail_text(message, data)
                        if code == -32601:
                            typer.secho(
                                "Error: Your node is missing mining RPC methods; update the node image or enable miner RPC.",
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
                                f"Error: miner.getBlockTemplate failed with internal error: {detail}",
                                fg=typer.colors.RED,
                                err=True,
                            )
                            typer.secho(
                                "Check node logs for the full stack trace (rpc/jsonrpc).",
                                fg=typer.colors.YELLOW,
                                err=True,
                            )

                    def get_template_via_local(*, allow_offline_override: bool = False):
                        if verbose:
                            typer.echo(f"  [Fallback] Fetching block template via local RPC at {url}")
                        payload = {
                            "address": resolved_address,
                            "include_mempool": include_mempool,
                            "allow_offline_mining": effective_allow_offline
                            or allow_offline_override,
                        }
                        try:
                            return client.request("miner.getBlockTemplate", payload)
                        except Exception as exc:
                            code, message, data = _rpc_error_details(exc)
                            detail_text = _rpc_error_detail_text(message, data)
                            if code == -32602 and any(
                                token in detail_text
                                for token in (
                                    "unexpected",
                                    "unknown",
                                    "keyword",
                                    "address",
                                    "payout_address",
                                )
                            ):
                                legacy_payload = {
                                    "payout_address": resolved_address,
                                    "include_mempool": include_mempool,
                                    "allow_offline_mining": effective_allow_offline
                                    or allow_offline_override,
                                }
                                try:
                                    return client.request("miner.getBlockTemplate", legacy_payload)
                                except Exception as legacy_exc:
                                    legacy_code, legacy_message, legacy_data = _rpc_error_details(legacy_exc)
                                    legacy_detail = _rpc_error_detail_text(legacy_message, legacy_data)
                                    if legacy_code == -32602 and any(
                                        token in legacy_detail
                                        for token in ("unexpected", "unknown", "keyword")
                                    ):
                                        return client.request(
                                            "miner.getBlockTemplate",
                                            [resolved_address, include_mempool],
                                        )
                                    _handle_template_rpc_error(legacy_exc)
                                    raise
                            _handle_template_rpc_error(exc)
                            raise

                    if proxy:
                        if verbose:
                            typer.echo("  [Proxy] Forwarding block template request to trusted RPC")
                        template = proxy.sync_forward_request(
                            "miner.getBlockTemplate",
                            {
                                "address": resolved_address,
                                "include_mempool": include_mempool,
                                "allow_offline_mining": effective_allow_offline,
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
                        if (
                            not proxy
                            and isinstance(reason, str)
                            and reason.startswith("sync_phase:")
                            and not effective_allow_offline
                        ):
                            typer.secho(
                                f"Info: Node is {reason}; retrying with local/offline template",
                                fg=typer.colors.YELLOW,
                            )
                            template = get_template_via_local(
                                allow_offline_override=True
                            )
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
                    header = _header_from_template(header_view)
                    target_hex = template.get("target")
                    target_int = int(target_hex, 16) if isinstance(target_hex, str) else int(target_hex or 0)
                    nonce, digest = _mine_header(header, target_int)
                    if nonce is None or digest is None:
                        typer.secho(
                            f"Warning: Block {i + 1}/{count} failed to mine",
                            fg=typer.colors.YELLOW,
                        )
                        typer.secho(
                            "Hint: Increase ANIMICA_MINER_MAX_NONCE or "
                            "ANIMICA_MINER_MAX_TOTAL_NONCE for more PoW attempts.",
                            fg=typer.colors.YELLOW,
                        )
                        stale_attempts = 0
                        break

                    header = header.__class__(
                        v=header.v,
                        chainId=header.chainId,
                        height=header.height,
                        parentHash=header.parentHash,
                        timestamp=header.timestamp,
                        stateRoot=header.stateRoot,
                        txsRoot=header.txsRoot,
                        receiptsRoot=header.receiptsRoot,
                        proofsRoot=header.proofsRoot,
                        daRoot=header.daRoot,
                        mixSeed=header.mixSeed,
                        poiesPolicyRoot=header.poiesPolicyRoot,
                        pqAlgPolicyRoot=header.pqAlgPolicyRoot,
                        thetaMicro=header.thetaMicro,
                        workType=header.workType,
                        nonce=nonce,
                        extra=header.extra,
                    )

                    txs_raw = [tx.get("raw") for tx in template.get("txs", []) if isinstance(tx, dict)]
                    header_payload = {
                        k: ("0x" + v.hex() if isinstance(v, (bytes, bytearray)) else v)
                        for k, v in header.to_obj().items()
                    }
                    parent_info = template.get("parent", {}) if isinstance(template, dict) else {}
                    parent_hash = parent_info.get("hash") or template.get("parentHash")
                    if not parent_hash:
                        parent_hash = "0x" + header.parentHash.hex()
                    parent_height = parent_info.get("height")
                    template_id = template.get("templateId") or template.get("template_id")
                    block_payload = {
                        "header": header_payload,
                        "txs": txs_raw,
                        "proofs": [],
                        "parentHash": parent_hash,
                        "templateId": template_id,
                    }

                    digest_int = int.from_bytes(digest, "big")
                    pow_valid = digest_int <= target_int
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

                    try:
                        if proxy:
                            submit_result = proxy.sync_forward_request(
                                "miner.submitBlock",
                                block_payload,
                                fallback_handler=lambda: client.request("miner.submitBlock", block_payload),
                            )
                        else:
                            submit_result = client.request("miner.submitBlock", block_payload)
                    except Exception as submit_error:
                        error_str = _format_rpc_error(submit_error)
                        error_data = getattr(submit_error, "data", None)
                        reason = None
                        if isinstance(error_data, dict):
                            reason = error_data.get("reason")
                        is_stale = (
                            isinstance(reason, str) and reason == "stale_template"
                        ) or "stale template" in error_str.lower()
                        _emit_mining_summary(summary, verbose=verbose, force=True)
                        typer.secho(
                            f"Warning: Block {i + 1}/{count} rejected by node ({error_str})",
                            fg=typer.colors.YELLOW,
                        )
                        if is_stale and stale_attempts < 3:
                            stale_attempts += 1
                            typer.secho(
                                f"  Retrying with fresh template (stale attempt {stale_attempts}/3)",
                                fg=typer.colors.YELLOW,
                            )
                            continue
                        stale_attempts = 0
                        break

                    if not submit_result or not submit_result.get("accepted", False):
                        rejection_reason = submit_result.get("reason")
                        _emit_mining_summary(summary, verbose=verbose, force=True)
                        typer.secho(
                            f"Warning: Block {i + 1}/{count} rejected by node (reason={rejection_reason})",
                            fg=typer.colors.YELLOW,
                        )
                        if isinstance(rejection_reason, str) and "stale" in rejection_reason and stale_attempts < 3:
                            stale_attempts += 1
                            typer.secho(
                                f"  Retrying with fresh template (stale attempt {stale_attempts}/3)",
                                fg=typer.colors.YELLOW,
                            )
                            continue
                        stale_attempts = 0
                        break

                    total_mined += 1
                    final_height = int(template.get("header", {}).get("height", 0))
                    block_reward = template.get("coinbase", {}).get("amount") or 0
                    total_reward += int(block_reward or 0)
                    reward_anm = int(block_reward or 0) / COIN_UNIT

                    typer.echo(
                        f"  Block {i + 1}/{count} mined (height: {final_height}, "
                        f"reward: {reward_anm:.9f} ANM = {block_reward} nANM)"
                    )

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

                if stale_attempts == 0 and (not submit_result or not submit_result.get("accepted", False)):
                    break

                # Sleep between blocks (except after the last one)
                if i < count - 1:
                    time.sleep(MIN_BLOCK_INTERVAL_SECONDS)
            
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
            total_reward_anm = total_reward / COIN_UNIT
            typer.secho(
                f"✓ Successfully mined {total_mined} block(s). "
                f"New chain height: {final_height}. "
                f"Total reward: {total_reward_anm:.9f} ANM ({total_reward} nANM)",
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


if __name__ == "__main__":  # pragma: no cover
    app()
