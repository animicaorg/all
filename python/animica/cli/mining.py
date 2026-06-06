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
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

import asyncio
import logging
import threading

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
POOL_PAYOUT_INTERVAL_ENV = "ANIMICA_POOL_PAYOUT_INTERVAL_SECONDS"
POOL_PAYOUT_MIN_AMOUNT_ENV = "ANIMICA_POOL_PAYOUT_MIN_AMOUNT"
POOL_PAYOUT_WALLET_ENV = "ANIMICA_POOL_PAYOUT_WALLET"

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
    stats: dict | None = None,
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

    if stats is not None:
        stats.setdefault("hashes", 0)
        stats.setdefault("workers", resolved_workers)
        stats["t_start"] = time.monotonic()

    def _scan_window(start_nonce: int, end_nonce: int) -> tuple[int | None, bytes | None]:
        window_size = end_nonce - start_nonce
        if resolved_workers > 1:
            from mining.parallel_nonce_search import parallel_nonce_search, pow_check_nonce

            result = parallel_nonce_search(
                pow_check_nonce,
                (header, target_int),
                start_nonce,
                window_size,
                resolved_workers,
            )
            if result and isinstance(result.payload, tuple):
                digest = result.payload[0]
                if isinstance(digest, (bytes, bytearray)):
                    if stats is not None:
                        # When found: workers shared the window; total
                        # actual probes ≈ winning worker's attempts × workers.
                        stats["hashes"] += int(result.attempts) * resolved_workers
                    return result.nonce, bytes(digest)
            if stats is not None:
                # Not found: every worker scanned its full stride.
                stats["hashes"] += window_size
            return None, None

        attempts = 0
        for nonce in range(start_nonce, end_nonce):
            try:
                candidate_hash = hash_candidate_header(header, nonce=nonce)
            except Exception:
                attempts += 1
                continue
            attempts += 1
            if candidate_hash.digest_int <= target_int:
                if stats is not None:
                    stats["hashes"] += attempts
                return nonce, candidate_hash.digest
        if stats is not None:
            stats["hashes"] += attempts
        return None, None

    # Use random starting nonce for each block to prevent nonce growth issues
    # This makes mining time-based and more about hash power rather than sequential nonce counting
    # Randomize in 32-bit space for better distribution and to avoid large nonce values
    import secrets
    start_nonce = secrets.randbelow(2**32)

    try:
        for _ in range(max(1, total_windows)):
            nonce, digest = _scan_window(start_nonce, start_nonce + max_nonce)
            if nonce is not None and digest is not None:
                return nonce, digest
            # Wrap around at 64-bit boundary to prevent overflow
            start_nonce = (start_nonce + max_nonce) & 0xFFFFFFFFFFFFFFFF
        return None, None
    finally:
        if stats is not None:
            stats["elapsed_s"] = max(1e-6, time.monotonic() - stats["t_start"])


def _fmt_hashrate(rate: float) -> str:
    """Format a hashes/sec rate with a sensible SI prefix."""
    if rate >= 1e9:
        return f"{rate / 1e9:.2f} GH/s"
    if rate >= 1e6:
        return f"{rate / 1e6:.2f} MH/s"
    if rate >= 1e3:
        return f"{rate / 1e3:.2f} kH/s"
    return f"{rate:.0f} H/s"


def _start_aicf_worker(address: str) -> tuple[Callable[[], None], dict]:
    """Spawn the agent_runtime AICF worker in a background thread.

    This is the real network-AICF compute path: the worker registers with the
    AICF endpoint, advertises hardware-eligible tiers, claims inference jobs,
    runs them against a locally installed model bundle, and submits results.

    Returns ``(stop_fn, stats)``. ``stats["started"]`` indicates whether the
    worker actually started — when False, the caller should print a hint
    pointing at ``animica miner setup``.
    """
    stats: dict[str, object] = {
        "started": False,
        "tiers": [],
        "endpoint": None,
        "reason": None,
    }

    try:
        from agent_runtime.aicf_worker import AICFWorker, is_disabled
        from agent_runtime.config import load_config
        from agent_runtime.errors import AgentRuntimeError
    except Exception as exc:  # noqa: BLE001 — agent_runtime is optional
        stats["reason"] = f"agent_runtime not installed: {exc}"
        return (lambda: None), stats

    if is_disabled():
        stats["reason"] = "ANIMICA_DISABLE_AICF_WORKER=1"
        return (lambda: None), stats

    try:
        cfg = load_config()
        # Allow the miner to point at a *remote* AICF endpoint instead of a
        # local node — that's the path that lets a miner serve AI requests
        # without running a full Animica node locally.
        override = (
            os.environ.get("ANIMICA_AICF_ENDPOINT")
            or os.environ.get("AICF_URL")
        )
        if override:
            try:
                network = os.environ.get("ANIMICA_NETWORK") or "mainnet"
                cfg.integration["aicf"]["endpoint"][network] = override.strip()
            except Exception:  # noqa: BLE001
                pass
        worker = AICFWorker(cfg=cfg, address=address)
    except AgentRuntimeError as exc:
        stats["reason"] = exc.message if hasattr(exc, "message") else str(exc)
        return (lambda: None), stats
    except Exception as exc:  # noqa: BLE001
        stats["reason"] = f"failed to construct AICFWorker: {exc}"
        return (lambda: None), stats

    stats["started"] = True
    stats["tiers"] = list(worker.tiers)
    stats["endpoint"] = worker.endpoint

    def _runner() -> None:
        try:
            worker.run()
        except Exception:  # noqa: BLE001
            pass
        finally:
            try:
                worker.close()
            except Exception:  # noqa: BLE001
                pass

    thread = threading.Thread(
        target=_runner, name="animica.aicf_worker", daemon=True,
    )
    thread.start()

    stopped = {"v": False}

    def _stop() -> None:
        if stopped["v"]:
            return
        stopped["v"] = True
        try:
            worker.stop()
        except Exception:  # noqa: BLE001
            pass
        thread.join(timeout=5.0)

    return _stop, stats


def _start_solo_useful_work() -> tuple[Callable[[], None], dict]:
    """Spawn AI/quantum/storage/VDF useful-work workers in their own
    asyncio loop running in a background thread, so solo mining keeps the
    machine busy with verifiable AI/Quantum/Storage/VDF compute alongside
    the PoW search.

    Returns ``(stop_fn, stats)``. ``stats`` is a dict with integer counters
    keyed by ``"ai" / "quantum" / "storage" / "vdf"``, plus ``"started"``
    (bool) so callers can tell whether any worker actually came up.

    The counters are updated by transparently wrapping
    ``mining.uw_inbox.push_result`` — every successful enqueue (i.e., a
    completed job that produced a valid receipt envelope) bumps the
    matching kind. The patch is idempotent across calls.
    """
    stats: dict[str, int | bool] = {
        "ai": 0, "quantum": 0, "storage": 0, "vdf": 0, "started": False,
    }

    try:
        from mining import uw_inbox as _uw_inbox
    except Exception:
        return (lambda: None), stats

    if not getattr(_uw_inbox, "_animica_count_patched", False):
        _orig_push = _uw_inbox.push_result

        def _counting_push(record):
            kind = ""
            try:
                raw_kind = (
                    getattr(record, "kind", None)
                    if not isinstance(record, dict)
                    else record.get("kind")
                )
                kind = (raw_kind or "").lower()
            except Exception:
                kind = ""
            ok = _orig_push(record)
            if ok:
                bucket = None
                if kind == "ai":
                    bucket = "ai"
                elif kind in {"quantum", "qrng"}:
                    bucket = "quantum"
                elif kind in {"storage", "po_st"}:
                    bucket = "storage"
                elif kind == "vdf":
                    bucket = "vdf"
                if bucket is not None:
                    stats[bucket] = int(stats.get(bucket, 0)) + 1
            return ok

        _uw_inbox.push_result = _counting_push       # type: ignore[assignment]
        _uw_inbox._animica_count_patched = True      # type: ignore[attr-defined]
        _uw_inbox._animica_stats = stats             # type: ignore[attr-defined]
    else:
        # Reuse counters from the prior call so everything aggregates.
        prior = getattr(_uw_inbox, "_animica_stats", None)
        if isinstance(prior, dict):
            stats = prior

    loop = asyncio.new_event_loop()
    stop_evt_holder: dict[str, Optional[asyncio.Event]] = {"e": None}
    ready_evt = threading.Event()

    def _runner() -> None:
        asyncio.set_event_loop(loop)
        stop_evt = asyncio.Event()
        stop_evt_holder["e"] = stop_evt

        async def _gather() -> None:
            tasks = []
            for mod_name in ("ai_worker", "quantum_worker",
                             "storage_worker", "vdf_worker"):
                try:
                    mod = importlib.import_module(f"mining.{mod_name}")
                except Exception:
                    continue
                run_fn = getattr(mod, "run", None)
                if not callable(run_fn):
                    continue
                tasks.append(asyncio.create_task(
                    run_fn(stop_evt), name=f"uw.{mod_name}",
                ))
            stats["started"] = bool(tasks)
            ready_evt.set()
            if not tasks:
                await stop_evt.wait()
                return
            await asyncio.gather(*tasks, return_exceptions=True)

        try:
            loop.run_until_complete(_gather())
        except Exception:
            pass
        finally:
            try:
                loop.close()
            except Exception:
                pass

    thread = threading.Thread(
        target=_runner, name="animica.useful_work", daemon=True,
    )
    thread.start()
    ready_evt.wait(timeout=5.0)

    def _stop() -> None:
        evt = stop_evt_holder.get("e")
        if evt is None:
            return
        try:
            loop.call_soon_threadsafe(evt.set)
        except Exception:
            pass
        thread.join(timeout=5.0)

    return _stop, stats


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


def _resolve_default_miner_address() -> Optional[str]:
    """Resolve the default miner payout address without exposing wallet secrets."""
    for env_name in ("ANIMICA_MINER_ADDRESS", "ANIMICA_DEFAULT_ADDRESS"):
        value = os.environ.get(env_name)
        if value and value.strip():
            return _resolve_payout_address(value.strip())

    try:
        from animica.cli.wallet import _load_store, _wallet_file_path

        store = _load_store(_wallet_file_path(None))
    except (ImportError, FileNotFoundError, KeyError, TypeError, ValueError):
        return None

    default_address = str(store.get("default_address") or "").strip()
    if default_address:
        return _resolve_payout_address(default_address)

    default_label = str(store.get("default") or "").strip()
    if default_label:
        return _resolve_payout_address(default_label)

    return None


# ── --llm flag helpers ─────────────────────────────────────────────────
# A user-friendly resolver: maps a short alias, a tier name, or a model id
# into the right env var(s) for the AICF inference engine.
#
# Three input shapes:
#   1. A standard tier name (tiny / small / standard / premium / xl).
#      → set ANIMICA_AICF_TIERS to that single tier, leaving the engine to
#        pick the per-tier default model. The pool sees the worker as that
#        tier and routes matching jobs to it.
#   2. A well-known model alias (phi3, qwen2.5-0.5b, llama3-8b, ...).
#      → expand to the canonical Hugging Face repo id.
#   3. Anything that looks like a HF repo id or a local path.
#      → pass through verbatim.
_AICF_TIERS = {"tiny", "small", "standard", "premium", "xl"}
_AICF_ALIASES = {
    "phi3":          "microsoft/Phi-3-mini-4k-instruct",
    "phi-3":         "microsoft/Phi-3-mini-4k-instruct",
    "phi4":          "microsoft/Phi-4",
    "qwen2.5-0.5b":  "Qwen/Qwen2.5-0.5B-Instruct",
    "qwen2.5-1.5b":  "Qwen/Qwen2.5-1.5B-Instruct",
    "qwen2.5-7b":    "Qwen/Qwen2.5-7B-Instruct",
    "llama3-8b":     "meta-llama/Meta-Llama-3-8B-Instruct",
    "llama3.1-8b":   "meta-llama/Meta-Llama-3.1-8B-Instruct",
    "mistral-7b":    "mistralai/Mistral-7B-Instruct-v0.3",
    "gemma2-2b":     "google/gemma-2-2b-it",
    "smollm-1.7b":   "HuggingFaceTB/SmolLM-1.7B-Instruct",
}


def _resolve_llm_alias(value: str) -> str:
    """Return the canonical HF repo id (or pass-through path) for a --llm input.

    Tier names short-circuit to "" so the caller knows to set
    ANIMICA_AICF_TIERS instead of ANIMICA_AICF_MODEL.
    """
    v = (value or "").strip()
    if not v:
        return ""
    if v.lower() in _AICF_TIERS:
        return ""  # signal: tier path
    return _AICF_ALIASES.get(v.lower(), v)


def _apply_llm_flag(llm: Optional[str]) -> None:
    """Set the env vars the AICF inference engine reads."""
    if not llm:
        return
    v = llm.strip()
    if not v:
        return
    if v.lower() in _AICF_TIERS:
        # Tier path: advertise just this tier, let the engine resolve per-tier defaults.
        os.environ["ANIMICA_AICF_TIERS"] = v.lower()
        # Don't override ANIMICA_AICF_MODEL — let the per-tier default apply
        # unless the user already pinned a model another way.
        return
    resolved = _resolve_llm_alias(v)
    if resolved:
        os.environ["ANIMICA_AICF_MODEL"] = resolved


def _require_miner_address(address_or_label: Optional[str]) -> str:
    if address_or_label and address_or_label.strip():
        return _resolve_payout_address(address_or_label.strip())

    resolved = _resolve_default_miner_address()
    if resolved:
        return resolved

    typer.secho(
        "Error: no miner payout address is configured.",
        fg=typer.colors.RED,
        err=True,
    )
    typer.secho(
        "Set one with `animica wallet set-default <label>`, export "
        "ANIMICA_MINER_ADDRESS, or pass `--address anim1...`.",
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
    if not lag_known and phase and phase not in {"SYNCED", "TARGET_REACHED"}:
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
        help="Payout/accounting mode (pps|solo|both)",
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
    payout_interval_seconds: Optional[float] = typer.Option(
        None,
        "--payout-interval-seconds",
        help="Automatic payout interval seconds (0 disables timer payouts)",
        envvar=POOL_PAYOUT_INTERVAL_ENV,
    ),
    payout_min_amount: Optional[int] = typer.Option(
        None,
        "--payout-min-amount",
        help="Minimum credited amount (base units) before an address is paid",
        envvar=POOL_PAYOUT_MIN_AMOUNT_ENV,
    ),
    payout_wallet: Optional[str] = typer.Option(
        None,
        "--payout-wallet",
        help="Wallet label/address used to sign payouts (defaults to pool address)",
        envvar=POOL_PAYOUT_WALLET_ENV,
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
    """Start the Animica Stratum mining pool with validated PPS/SOLO/BOTH settings.

    Examples:
      animica miner run-pool --mode pps --pool-address anim1... --rpc-url http://127.0.0.1:8545/rpc
      animica miner run-pool --mode solo --pool-address anim1... --rpc-url http://127.0.0.1:8545/rpc
      animica miner run-pool --mode both --pool-address anim1... --rpc-url http://127.0.0.1:8545/rpc
    """
    _ensure_network_env()
    runtime = _ensure_stratum_available()

    normalized_mode = str(mode or "").strip().lower()
    if normalized_mode not in {"pps", "solo", "both"}:
        typer.secho(
            "Error: --mode must be one of 'pps', 'solo', or 'both'.",
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
    if payout_interval_seconds is not None:
        cfg_overrides["payout_interval_seconds"] = float(payout_interval_seconds)
        env_overrides[POOL_PAYOUT_INTERVAL_ENV] = str(float(payout_interval_seconds))
    if payout_min_amount is not None:
        cfg_overrides["payout_min_amount"] = int(payout_min_amount)
        env_overrides[POOL_PAYOUT_MIN_AMOUNT_ENV] = str(int(payout_min_amount))
    if payout_wallet is not None:
        cfg_overrides["payout_wallet"] = str(payout_wallet)
        env_overrides[POOL_PAYOUT_WALLET_ENV] = str(payout_wallet)
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
    public_stratum_url = os.getenv("ANIMICA_PUBLIC_STRATUM_URL")
    if public_stratum_url:
        stratum_url = public_stratum_url
    else:
        display_host = str(resolved_cfg.host)
        if display_host in {"0.0.0.0", "::", "[::]"}:
            display_host = (
                os.getenv("ANIMICA_PUBLIC_STRATUM_HOST")
                or os.getenv("ANIMICA_PUBLIC_DOMAIN")
                or "127.0.0.1"
            )
        stratum_url = f"stratum+tcp://{display_host}:{resolved_cfg.port}"

    api_host_display = str(resolved_cfg.api_host)
    if api_host_display in {"0.0.0.0", "::", "[::]"}:
        api_host_display = (
            os.getenv("ANIMICA_PUBLIC_POOL_API_HOST")
            or os.getenv("ANIMICA_PUBLIC_DOMAIN")
            or "127.0.0.1"
        )
    api_url = f"http://{api_host_display}:{resolved_cfg.api_port}"
    mode_notes = {
        "pps": "PPS credits accepted shares immediately (deterministic per-share accounting).",
        "solo": "SOLO credits only accepted full blocks to the submitting miner.",
        "both": "BOTH runs parallel PPS and SOLO accounting so miners can choose mode per connection.",
    }
    typer.echo(f"Stratum endpoint: {stratum_url}")
    typer.echo(f"Pool API: {api_url}")
    typer.echo(f"Pool payout address: {resolved_cfg.pool_address}")
    typer.echo(f"Payout mode: {resolved_cfg.pool_mode.upper()} - {mode_notes.get(resolved_cfg.pool_mode, '')}")
    payout_interval = float(getattr(resolved_cfg, "payout_interval_seconds", 0.0) or 0.0)
    payout_min = int(getattr(resolved_cfg, "payout_min_amount", 1) or 1)
    if payout_interval > 0:
        typer.echo(
            f"Automated payouts: enabled every {payout_interval:.0f}s "
            f"(minimum {payout_min} base units, wallet={resolved_cfg.payout_wallet})"
        )
    else:
        typer.echo("Automated payouts: disabled (set --payout-interval-seconds > 0 to enable)")
    typer.echo("Miner examples:")
    pool_url_for_examples = stratum_url
    if "://" not in pool_url_for_examples:
        parsed = urlsplit(f"stratum+tcp://{pool_url_for_examples}")
        pool_url_for_examples = (
            f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
            if parsed.hostname and parsed.port
            else stratum_url
        )
    example_modes = (
        ["pps", "solo"] if resolved_cfg.pool_mode == "both" else [resolved_cfg.pool_mode]
    )
    for example_mode in example_modes:
        label = f" ({example_mode.upper()})" if resolved_cfg.pool_mode == "both" else ""
        typer.echo(
            "  Linux/macOS"
            f"{label}: ./animica-miner --pool-url {pool_url_for_examples} --address <anim1...> "
            f"--worker worker-01 --threads 4 --pool-mode {example_mode}"
        )
        typer.echo(
            "  Windows"
            f"{label}: animica-miner.exe --pool-url {pool_url_for_examples} --address <anim1...> "
            f"--worker worker-01 --threads 4 --pool-mode {example_mode}"
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
    port: int = typer.Option(3333, "--port", help="Stratum port"),
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


@app.command("models")
def models(
    tier: Optional[str] = typer.Option(
        None, "--tier", help="If set, only show the resolved model for this tier."
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Emit JSON instead of a table."
    ),
) -> None:
    """List models the AICF miner has auto-detected and the per-tier picks.

    Resolution order: env override > local bundle > HF cache > fallback.
    Operator overrides per tier with ANIMICA_AICF_MODEL_<TIER>=<id>.
    Override the local bundle dir with ANIMICA_AICF_MODEL_DIR.
    """
    try:
        from animica.stratum_pool.aicf_inference import (
            discover_models, resolve_tier_model, _TIER_RANGES,
        )
    except Exception as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc

    discovered = discover_models()
    tiers = [t for t, _, _ in _TIER_RANGES]
    if tier:
        if tier not in tiers:
            typer.echo(f"unknown tier {tier!r} (known: {', '.join(tiers)})", err=True)
            raise typer.Exit(1)
        tiers = [tier]
    picks = {t: resolve_tier_model(t) for t in tiers}

    if json_output:
        import json
        payload = {
            "discovered": [
                {"identifier": m.identifier, "source": m.source,
                 "approx_billions": m.approx_billions}
                for m in discovered
            ],
            "picks": picks,
        }
        typer.echo(json.dumps(payload, indent=2))
        return

    typer.echo("Auto-detected models:")
    for m in discovered:
        sz = f"{m.approx_billions:>5.1f}B" if m.approx_billions else "    ?"
        typer.echo(f"  {m.source:<14s} {sz}  {m.identifier}")
    typer.echo("")
    typer.echo("Per-tier picks:")
    for t in tiers:
        typer.echo(f"  {t:<9s} -> {picks[t]}")


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


def _parse_pool_stratum_url(raw: str) -> tuple[str, int, bool]:
    """
    Parse a stratum pool endpoint into (host, port, tls).

    Accepts:
      - stratum+tcp://host:port  (plain TCP)
      - stratum://host:port      (plain TCP)
      - tcp://host:port          (plain TCP)
      - stratum+ssl://host:port  (TLS — also accepts stratum+tls://, ssl://, tls://)
      - host:port                (plain TCP)
    """
    if not raw or not isinstance(raw, str):
        raise ValueError("empty stratum URL")
    candidate = raw.strip()
    tls = False
    tls_prefixes = ("stratum+ssl://", "stratum+tls://", "ssl://", "tls://")
    plain_prefixes = ("stratum+tcp://", "stratum://", "tcp://")
    lowered = candidate.lower()
    for prefix in tls_prefixes:
        if lowered.startswith(prefix):
            candidate = candidate[len(prefix):]
            tls = True
            break
    else:
        for prefix in plain_prefixes:
            if lowered.startswith(prefix):
                candidate = candidate[len(prefix):]
                break
    # Strip optional path/query
    for sep in ("/", "?"):
        if sep in candidate:
            candidate = candidate.split(sep, 1)[0]
    if ":" not in candidate:
        raise ValueError(
            f"stratum URL must include a port (got {raw!r}); "
            "use e.g. stratum+tcp://pool.example.com:23454"
        )
    host, port_str = candidate.rsplit(":", 1)
    host = host.strip()
    if not host:
        raise ValueError(f"empty host in stratum URL {raw!r}")
    try:
        port = int(port_str)
    except ValueError as exc:
        raise ValueError(f"invalid stratum port in {raw!r}: {exc}") from None
    if not (0 < port < 65536):
        raise ValueError(f"stratum port out of range in {raw!r}: {port}")
    return host, port, tls


async def _run_pool_stratum_miner(
    *,
    host: str,
    port: int,
    worker: str,
    address: str,
    target_blocks: int,
    scan_window: int = 100_000,
    framing: str = "lines",
    enable_useful_work: bool = True,
    device: str = "auto",
    threads: int = 0,
    tls: bool = False,
    tls_verify: bool = True,
) -> int:
    """
    Run the CPU stratum miner against `host:port` and return the number of
    accepted blocks. Stops when `target_blocks` accepted blocks have been
    submitted or when the connection drops / SIGINT is received.

    When `enable_useful_work` is True (default), AI/quantum/storage/VDF
    workers are spawned alongside the miner so completed useful-work
    receipts are converted into `compute.receipt.v1` envelopes and
    attached to outgoing stratum shares (see `mining.uw_inbox`). The
    pool / node-side UWP verifier credits bonus AICF credits to the
    miner when each proof is accepted.
    """
    import asyncio as _asyncio
    import signal as _signal
    import time as _time

    # Lazy imports: the mining package is heavy and only needed in this branch.
    from mining.internal_cpu_miner import CpuStratumMiner

    stop = _asyncio.Event()
    accepted_blocks = 0
    accepted_shares = 0
    rejected_shares = 0
    rejected_by_reason: dict[str, int] = {}
    submitted_shares = 0
    notify_count = 0
    diff_changes = 0
    aicf_jobs_received = 0
    aicf_jobs_completed = 0
    aicf_jobs_failed = 0
    last_share_target: float = 0.0
    last_theta_micro: int = 0
    last_job_id: str = ""
    started_at = _time.monotonic()
    target_blocks = max(0, int(target_blocks))
    target_label = "unlimited" if target_blocks == 0 else str(target_blocks)

    uw_tasks: list[_asyncio.Task] = []
    if enable_useful_work:
        try:
            from mining import (
                ai_worker as _ai,
                quantum_worker as _quantum,
                storage_worker as _storage,
                vdf_worker as _vdf,
            )
        except Exception:
            _ai = _quantum = _storage = _vdf = None  # type: ignore[assignment]

        worker_runners = [
            ("ai_worker", getattr(_ai, "run", None)),
            ("quantum_worker", getattr(_quantum, "run", None)),
            ("storage_worker", getattr(_storage, "run", None)),
            ("vdf_worker", getattr(_vdf, "run", None)),
        ]
        for label, fn in worker_runners:
            if fn is None:
                continue
            uw_tasks.append(_asyncio.create_task(fn(stop), name=f"uw.{label}"))
        if uw_tasks:
            typer.secho(
                f"Useful-work workers active: {len(uw_tasks)} "
                "(AI/Quantum/Storage/VDF). Receipts will be attached to "
                "shares as compute.receipt.v1 envelopes.",
                fg=typer.colors.CYAN,
            )

    miner = CpuStratumMiner(
        host=host,
        port=port,
        agent="animica-cli/0.1",
        worker=worker,
        address=address,
        scan_window=int(scan_window),
        device=device,
        threads=int(threads),
        tls=tls,
        tls_verify=tls_verify,
    )

    # Wrap the underlying client's submit_share to capture accepted/isBlock so
    # the CLI can stop after `target_blocks` accepted blocks.
    original_submit = miner._client.submit_share

    async def _counting_submit(job_id, hashshare, proofs=None, txs=None, extranonce2="0x00"):
        nonlocal accepted_blocks, accepted_shares, rejected_shares, submitted_shares
        submitted_shares += 1
        res = await original_submit(
            job_id,
            hashshare,
            proofs=proofs,
            txs=txs,
            extranonce2=extranonce2,
        )
        result = res.get("result") if isinstance(res, dict) else None
        if isinstance(result, dict):
            if result.get("accepted"):
                accepted_shares += 1
            if result.get("isBlock") or result.get("is_block"):
                accepted_blocks += 1
                typer.secho(
                    f"  ✓ block accepted: height={result.get('height')} "
                    f"hash={result.get('hash')} "
                    f"({accepted_blocks}/{target_label})",
                    fg=typer.colors.GREEN,
                )
                if target_blocks and accepted_blocks >= target_blocks:
                    stop.set()
            elif result.get("accepted"):
                accept_pct = (accepted_shares / submitted_shares * 100.0) if submitted_shares else 0.0
                typer.echo(
                    f"  share accepted job={str(job_id)[:12]} "
                    f"shareTarget={last_share_target:.6f} "
                    f"accepted={accepted_shares}/{submitted_shares} ({accept_pct:.1f}%)"
                )
            else:
                rejected_shares += 1
                reason = str(result.get("reason") or "unknown")
                rejected_by_reason[reason] = rejected_by_reason.get(reason, 0) + 1
                typer.secho(
                    f"  share rejected: {reason} "
                    f"job={str(job_id)[:12]} "
                    f"(rejected={rejected_shares}, top reasons: "
                    f"{', '.join(f'{k}={v}' for k, v in sorted(rejected_by_reason.items(), key=lambda kv: -kv[1])[:3])})",
                    fg=typer.colors.YELLOW,
                )
        return res

    miner._client.submit_share = _counting_submit  # type: ignore[assignment]

    # Hook notify / difficulty / AICF events so operators see the full lifecycle.
    # CpuStratumMiner installs its own on_notify (drives PoW search) and
    # on_set_difficulty (updates target) inside start(). Replacing those would
    # silence mining entirely, so we *chain* — record the originals AFTER
    # start() runs and call them from the wrappers.
    _orig_notify_holder: list = [None]
    _orig_diff_holder: list = [None]

    async def _on_notify(params):
        nonlocal notify_count, last_job_id
        notify_count += 1
        jid = str(params.get("jobId") or "")
        last_job_id = jid
        clean = bool(params.get("cleanJobs"))
        height = params.get("height") or params.get("blockHeight")
        typer.echo(
            f"  job notify #{notify_count} id={jid[:12]} "
            f"height={height} clean={clean} "
            f"shareTarget={params.get('shareTarget')}"
        )
        orig = _orig_notify_holder[0]
        if orig is not None:
            await orig(params)

    async def _on_set_difficulty(share_target, theta_micro):
        nonlocal diff_changes, last_share_target, last_theta_micro
        diff_changes += 1
        last_share_target = float(share_target)
        last_theta_micro = int(theta_micro)
        typer.secho(
            f"  difficulty updated #{diff_changes}: "
            f"shareTarget={last_share_target:.6f} θμ={last_theta_micro}",
            fg=typer.colors.CYAN,
        )
        orig = _orig_diff_holder[0]
        if orig is not None:
            await orig(share_target, theta_micro)

    # Wrap AICF inference handler so operators see when the pool dispatches
    # an inference job to this miner and when results are submitted back.
    original_aicf_handler = miner._client._handle_aicf_notify

    async def _logging_aicf_handler(params):
        nonlocal aicf_jobs_received, aicf_jobs_completed, aicf_jobs_failed
        aicf_jobs_received += 1
        job_id = str(params.get("jobId") or "")
        tier = str(params.get("tier") or "?")
        prompt = ""
        spec = params.get("spec") or {}
        if isinstance(spec, dict):
            prompt = str(spec.get("prompt") or "")[:80]
        typer.secho(
            f"  AICF job received #{aicf_jobs_received} id={job_id[:12]} "
            f"tier={tier} prompt={prompt!r}",
            fg=typer.colors.MAGENTA,
        )
        t0 = _time.monotonic()
        try:
            await original_aicf_handler(params)
            aicf_jobs_completed += 1
            typer.secho(
                f"  AICF job completed id={job_id[:12]} "
                f"latency={int((_time.monotonic() - t0) * 1000)}ms "
                f"({aicf_jobs_completed}/{aicf_jobs_received})",
                fg=typer.colors.MAGENTA,
            )
        except Exception as exc:
            aicf_jobs_failed += 1
            typer.secho(
                f"  AICF job FAILED id={job_id[:12]} error={exc} "
                f"({aicf_jobs_failed} failures)",
                fg=typer.colors.RED,
            )
            raise

    miner._client._handle_aicf_notify = _logging_aicf_handler  # type: ignore[assignment]

    async def _stats_loop():
        """Print a one-line summary every 30s so operators see the miner is
        alive even when there are no accepted shares to report."""
        while not stop.is_set():
            try:
                await _asyncio.wait_for(stop.wait(), timeout=30.0)
                return
            except _asyncio.TimeoutError:
                pass
            uptime = int(_time.monotonic() - started_at)
            hrs, rem = divmod(uptime, 3600)
            mins, secs = divmod(rem, 60)
            uptime_str = f"{hrs}h{mins:02d}m{secs:02d}s" if hrs else f"{mins}m{secs:02d}s"
            accept_pct = (accepted_shares / submitted_shares * 100.0) if submitted_shares else 0.0
            typer.secho(
                f"  [stats] uptime={uptime_str} "
                f"notifies={notify_count} diff_updates={diff_changes} "
                f"submitted={submitted_shares} accepted={accepted_shares} "
                f"rejected={rejected_shares} accept_rate={accept_pct:.1f}% "
                f"blocks={accepted_blocks}/{target_label} "
                f"aicf=({aicf_jobs_completed} ok / {aicf_jobs_failed} fail / "
                f"{aicf_jobs_received} total) "
                f"shareTarget={last_share_target:.6f}",
                fg=typer.colors.BLUE,
            )

    stats_task = _asyncio.create_task(_stats_loop(), name="mine_blocks_stats")

    for sig in (_signal.SIGINT, _signal.SIGTERM):
        try:
            _asyncio.get_running_loop().add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            pass

    typer.echo(
        f"Connecting to stratum pool {host}:{port} as worker={worker} address={address}"
    )
    try:
        await miner.start()
    except Exception as exc:
        typer.secho(
            f"Error: failed to connect to stratum pool {host}:{port}: {exc}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)

    # Now that CpuStratumMiner has wired its own handlers in start(),
    # chain our verbose-logging wrappers in front so we get the operator
    # trace without disabling the underlying PoW loop.
    _orig_notify_holder[0] = miner._client.on_notify  # type: ignore[assignment]
    _orig_diff_holder[0] = miner._client.on_set_difficulty  # type: ignore[assignment]
    miner._client.on_notify = _on_notify  # type: ignore[assignment]
    miner._client.on_set_difficulty = _on_set_difficulty  # type: ignore[assignment]

    typer.echo(
        f"Stratum miner running. Target: {target_label} accepted block(s). "
        "Press Ctrl-C to stop."
    )
    try:
        await stop.wait()
    finally:
        try:
            await miner.stop()
        except Exception:
            pass
        # Stop useful-work workers and drain their tasks.
        if uw_tasks:
            for t in uw_tasks:
                t.cancel()
            await _asyncio.gather(*uw_tasks, return_exceptions=True)
        stats_task.cancel()
        try:
            await stats_task
        except (_asyncio.CancelledError, Exception):
            pass
    uptime = int(_time.monotonic() - started_at)
    hrs, rem = divmod(uptime, 3600)
    mins, secs = divmod(rem, 60)
    uptime_str = f"{hrs}h{mins:02d}m{secs:02d}s" if hrs else f"{mins}m{secs:02d}s"
    accept_pct = (accepted_shares / submitted_shares * 100.0) if submitted_shares else 0.0
    reasons = (
        ", ".join(f"{k}={v}" for k, v in sorted(rejected_by_reason.items(), key=lambda kv: -kv[1]))
        or "none"
    )
    typer.secho(
        "Final summary:\n"
        f"  uptime          = {uptime_str}\n"
        f"  blocks accepted = {accepted_blocks}/{target_label}\n"
        f"  shares submitted= {submitted_shares}\n"
        f"  shares accepted = {accepted_shares} ({accept_pct:.1f}%)\n"
        f"  shares rejected = {rejected_shares}\n"
        f"  reject reasons  = {reasons}\n"
        f"  notifies        = {notify_count}\n"
        f"  diff updates    = {diff_changes}\n"
        f"  aicf jobs       = {aicf_jobs_completed} ok / {aicf_jobs_failed} fail / "
        f"{aicf_jobs_received} total",
        fg=typer.colors.GREEN if (target_blocks == 0 or accepted_blocks >= target_blocks) else typer.colors.YELLOW,
    )
    return accepted_blocks


@app.command("start")
def start(
    pool: str = typer.Option(
        "pool.animica.org:3333",
        "--pool",
        help="Stratum pool endpoint, for example pool.animica.org:3333",
        envvar="ANIMICA_MINER_POOL",
    ),
    address: Optional[str] = typer.Option(
        None,
        "--address",
        help="Payout address or wallet label. Defaults to ANIMICA_MINER_ADDRESS or the default wallet.",
        envvar="ANIMICA_MINER_ADDRESS",
    ),
    worker: Optional[str] = typer.Option(
        None,
        "--worker",
        help="Worker name reported to the pool. Defaults to the payout address.",
        envvar="ANIMICA_MINER_WORKER",
    ),
    threads: int = typer.Option(
        0,
        "--threads",
        help="CPU threads for PoW search. 0 auto-detects.",
    ),
    device: str = typer.Option(
        "auto",
        "--device",
        help="Mining device backend (cpu, cuda, rocm, opencl, metal, auto).",
        envvar="ANIMICA_MINER_DEVICE",
    ),
    gpu: bool = typer.Option(
        False,
        "--gpu",
        help="Use CUDA GPU backend (alias for --device cuda).",
    ),
    aicf: bool = typer.Option(
        True,
        "--aicf/--no-aicf",
        help="Run the AICF compute worker alongside PoW when configured.",
    ),
    aicf_endpoint: Optional[str] = typer.Option(
        None,
        "--aicf-endpoint",
        envvar="ANIMICA_AICF_ENDPOINT",
        help="Override the AICF endpoint used by the worker.",
    ),
    llm: Optional[str] = typer.Option(
        None,
        "--llm",
        "--model",
        envvar="ANIMICA_AICF_MODEL",
        help=(
            "Pin which LLM the AICF inference worker should serve. Accepts a "
            "Hugging Face repo id, a local path, or one of the well-known "
            "short names. When omitted the worker falls back to the per-tier "
            "default."
        ),
    ),
    pool_scan_window: int = typer.Option(
        100_000,
        "--pool-scan-window",
        help="Nonce window size scanned per Stratum job.",
    ),
) -> None:
    """Start mining against a Stratum pool until stopped."""
    resolved_address = _require_miner_address(address)
    # Propagate the chosen model / tier into the env so the AICF inference
    # engine (and any subprocesses) pick it up.
    _apply_llm_flag(llm)
    mine_blocks(
        address=resolved_address,
        count=0,
        address_opt=None,
        pool_stratum=pool,
        pool_worker=worker,
        pool_scan_window=pool_scan_window,
        pool_useful_work=True,
        aicf=aicf,
        aicf_endpoint=aicf_endpoint,
        threads=threads,
        allow_remote_rpc=False,
        device=device,
        gpu=gpu,
        rpc_url=None,
        use_proxy=False,
        verbose=False,
        no_timeout=False,
        include_mempool=True,
        template_ttl_s=15,
    )


@app.command("setup")
def setup(
    tiers: Optional[str] = typer.Option(
        None,
        "--tiers",
        help=(
            "Comma-separated tier ids to install (e.g. 'tiny,small'). "
            "Default: install all tiers eligible for the detected hardware."
        ),
    ),
    bundles_file: Optional[Path] = typer.Option(
        None,
        "--bundles-file",
        help=(
            "Path to a JSON file mapping tier -> {cid, sha256}. Default: "
            "look at ~/.animica/aicf_bundles.json or env vars "
            "ANIMICA_AICF_TIER_<TIER>_CID / _SHA256."
        ),
    ),
    source: str = typer.Option(
        "auto",
        "--source",
        help=(
            "Where to fetch bundles from: 'auto' (IPFS CID if configured, "
            "else HuggingFace base model), 'ipfs' (require a CID), or "
            "'huggingface' (always pull the base model from HF)."
        ),
    ),
    skip_download: bool = typer.Option(
        False,
        "--skip-download",
        help="Detect hardware and prepare cache dirs but do not pull bundles.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit a JSON report."),
) -> None:
    """Bootstrap this machine for Animica AI compute (AICF).

    Runs four steps:

      1. Detect hardware (CPU, RAM, GPUs) and pick eligible model tiers.
      2. Prepare the on-disk model cache directory (``~/.animica/models``).
      3. For each selected tier, install a bundle:
           - If an IPFS CID is configured for the tier, pull from IPFS.
           - Otherwise (--source auto, the default), pull the tier's
             base model directly from HuggingFace Hub and wrap it as a
             local bundle so the miner can serve AICF jobs immediately.
      4. Print a one-line "next step" pointing at ``animica miner mine-blocks``.

    Tier → CID mapping is read in this order: ``--bundles-file``,
    ``~/.animica/aicf_bundles.json``, then env vars
    ``ANIMICA_AICF_TIER_<TIER>_CID`` (and optional ``_SHA256``). When no CID
    is configured and ``--source=auto`` (default), the tier's ``base_model``
    from the model catalog is downloaded from HuggingFace and shaped into a
    local bundle. Pass ``--source ipfs`` to require a CID; pass
    ``--source huggingface`` to always pull the base model from HF.
    """
    try:
        from agent_runtime.aicf_worker import (
            bootstrap_bundle_from_hf,
            pull_bundle,
            resolve_tiers,
        )
        from agent_runtime.config import load_config
        from agent_runtime.errors import AgentRuntimeError, BundleError
        from agent_runtime.hardware import attach_eligible_tiers, detect_hardware
    except Exception as exc:  # noqa: BLE001
        typer.secho(
            f"Error: agent_runtime is not installed ({exc}). "
            "Install the full animica package: `python3 -m pip install --upgrade animica`.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2)

    source = (source or "auto").strip().lower()
    if source not in {"auto", "ipfs", "huggingface", "hf"}:
        typer.secho(
            f"Error: --source must be one of auto|ipfs|huggingface (got {source!r}).",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2)
    if source == "hf":
        source = "huggingface"

    report: dict[str, object] = {
        "hardware": None,
        "eligible_tiers": [],
        "tiers_requested": None,
        "installed": [],
        "skipped": [],
        "errors": [],
    }

    cfg = load_config()
    profile = detect_hardware()
    attach_eligible_tiers(profile, dict(cfg.model_catalog))
    report["hardware"] = profile.to_dict()
    report["eligible_tiers"] = list(profile.eligible_tiers or [])

    override = None
    if tiers:
        override = [t.strip() for t in tiers.split(",") if t.strip()]
        report["tiers_requested"] = override
    resolved_tiers = resolve_tiers(profile, dict(cfg.model_catalog), override=override)

    cache_root_raw = (
        cfg.integration.get("aicf", {}).get("miner_cache_dir")
        or cfg.model_catalog["propagation"]["miner_cache_dir"]
    )
    cache_root = Path(str(cache_root_raw)).expanduser()
    cache_root.mkdir(parents=True, exist_ok=True)

    if not json_output:
        typer.secho("=== Animica miner setup ===", fg=typer.colors.CYAN)
        typer.echo(f"Detected: {profile.os}/{profile.arch}, "
                   f"{profile.cpu_cores_logical} cores, "
                   f"{profile.ram_gb:.1f} GB RAM, "
                   f"accel={profile.accelerator_preferred}")
        typer.echo(f"Eligible tiers: {', '.join(profile.eligible_tiers or ['(none)'])}")
        typer.echo(f"Cache dir: {cache_root}")
        typer.echo(f"Tiers to install: {', '.join(resolved_tiers)}")

    # ---- ML stack: torch / transformers / accelerate -----------------------
    # Real inference (vs the labeled stub) needs these three. Detect what is
    # already importable and pip-install only what is missing so re-running
    # `animica miner setup` is idempotent on a fully-provisioned box.
    ml_stack_status: dict[str, dict] = {}
    ml_missing: list[str] = []
    # sentence-transformers powers the RAG retriever in
    # animica.stratum_pool.aicf_rag — without it the index is shipped
    # but never queried, so the model loses its grounding excerpts.
    for pkg in ("torch", "transformers", "accelerate", "sentence_transformers"):
        try:
            mod = __import__(pkg)
            ml_stack_status[pkg] = {
                "installed": True,
                "version": getattr(mod, "__version__", "?"),
            }
        except Exception as exc:
            ml_stack_status[pkg] = {"installed": False, "error": str(exc)}
            ml_missing.append(pkg)
    report["ml_stack"] = ml_stack_status

    if not skip_download and ml_missing:
        if not json_output:
            typer.secho(
                f"ML stack missing: {', '.join(ml_missing)} — installing via pip…",
                fg=typer.colors.CYAN,
            )
        import subprocess as _sub
        # pip's default per-read timeout is 15s, which routinely times out on
        # the multi-GB torch wheel even from a fast connection. Bump it well
        # past any reasonable network stall, and retry on transient failures.
        # Map our import-name keys to the PyPI distribution names. Only
        # sentence_transformers differs (underscore vs hyphen on PyPI).
        _pip_name = {"sentence_transformers": "sentence-transformers"}
        cmd = [
            sys.executable, "-m", "pip", "install", "--upgrade",
            "--timeout", "300",
            "--retries", "5",
            *[_pip_name.get(p, p) for p in ml_missing],
        ]
        try:
            proc = _sub.run(cmd, capture_output=not json_output, text=True)
            if proc.returncode != 0:
                err = (proc.stderr or proc.stdout or "").strip()[-500:]
                report["errors"].append(f"pip install ml-stack failed: {err}")
                if not json_output:
                    typer.secho(
                        f"  pip install failed (rc={proc.returncode}): {err}",
                        fg=typer.colors.RED,
                    )
            else:
                # Re-detect after install so the report reflects reality.
                for pkg in ml_missing:
                    try:
                        # Drop any cached import failure so reimport works.
                        import importlib as _il
                        _il.invalidate_caches()
                        mod = _il.import_module(pkg)
                        ml_stack_status[pkg] = {
                            "installed": True,
                            "version": getattr(mod, "__version__", "?"),
                            "installed_by_setup": True,
                        }
                    except Exception as exc:
                        ml_stack_status[pkg] = {"installed": False, "error": str(exc)}
                if not json_output:
                    typer.secho(
                        "  ML stack installed: "
                        + ", ".join(
                            f"{p}=={ml_stack_status[p].get('version', '?')}"
                            for p in ml_missing
                            if ml_stack_status[p]["installed"]
                        ),
                        fg=typer.colors.GREEN,
                    )
        except Exception as exc:
            report["errors"].append(f"pip install ml-stack failed: {exc}")
            if not json_output:
                typer.secho(
                    f"  pip install failed: {exc}", fg=typer.colors.RED
                )
    elif not ml_missing and not json_output:
        typer.secho(
            "ML stack present: "
            + ", ".join(
                f"{p}=={ml_stack_status[p].get('version', '?')}"
                for p in ("torch", "transformers", "accelerate", "sentence_transformers")
            ),
            fg=typer.colors.GREEN,
        )

    # Load tier -> {cid, sha256} mapping.
    bundles_map: dict[str, dict] = {}
    candidate_paths: list[Path] = []
    if bundles_file:
        candidate_paths.append(bundles_file)
    candidate_paths.append(Path("~/.animica/aicf_bundles.json").expanduser())
    for path in candidate_paths:
        try:
            if path.is_file():
                import json as _json
                bundles_map = _json.loads(path.read_text(encoding="utf-8")) or {}
                if not json_output:
                    typer.echo(f"Loaded bundle map from {path}")
                break
        except Exception as exc:  # noqa: BLE001
            report["errors"].append(f"could not read {path}: {exc}")

    def _lookup_bundle(tier: str) -> Optional[dict]:
        entry = bundles_map.get(tier)
        if isinstance(entry, dict) and entry.get("cid"):
            return entry
        env_cid = os.environ.get(f"ANIMICA_AICF_TIER_{tier.upper()}_CID")
        env_sha = os.environ.get(f"ANIMICA_AICF_TIER_{tier.upper()}_SHA256")
        if env_cid:
            return {"cid": env_cid, "sha256": env_sha}
        return None

    def _tier_base_model(tier: str) -> str:
        for t in (cfg.model_catalog.get("tiers") or []):
            if isinstance(t, dict) and str(t.get("id")) == tier:
                return str(t.get("base_model") or "")
        return ""

    if skip_download:
        report["skipped"] = list(resolved_tiers)
        if not json_output:
            typer.secho("--skip-download set; not pulling any bundles.",
                        fg=typer.colors.YELLOW)
    else:
        for tier in resolved_tiers:
            entry = _lookup_bundle(tier) if source != "huggingface" else None
            use_hf = entry is None and source in {"auto", "huggingface"}
            if entry is None and not use_hf:
                report["skipped"].append(tier)
                if not json_output:
                    typer.secho(
                        f"  - tier {tier}: no bundle CID configured "
                        f"(set ANIMICA_AICF_TIER_{tier.upper()}_CID, add "
                        "an entry to ~/.animica/aicf_bundles.json, or "
                        "rerun with --source huggingface).",
                        fg=typer.colors.YELLOW,
                    )
                continue
            if use_hf:
                base_model = _tier_base_model(tier)
                if not base_model:
                    report["skipped"].append(tier)
                    if not json_output:
                        typer.secho(
                            f"  - tier {tier}: no base_model in catalog; "
                            "cannot bootstrap from HuggingFace.",
                            fg=typer.colors.YELLOW,
                        )
                    continue
                # Detect "already downloaded" state so we don't re-fetch a
                # multi-GB model on every `animica miner setup` invocation.
                # Mirrors the layout used by bootstrap_bundle_from_hf: see
                # ai/agent_runtime/src/agent_runtime/aicf_worker.py.
                slug = (
                    base_model.replace("/", "_")
                    .replace(":", "_")
                    .replace("\\", "_")
                )
                bundle_dir_probe = cache_root / tier / f"hf-{slug}"
                model_dir_probe = bundle_dir_probe / "model"
                manifest_probe = bundle_dir_probe / "manifest.json"
                inference_probe = bundle_dir_probe / "inference.json"
                already_cached = bool(
                    manifest_probe.is_file()
                    and inference_probe.is_file()
                    and model_dir_probe.is_dir()
                    and any(model_dir_probe.iterdir())
                )
                if not json_output:
                    if already_cached:
                        typer.secho(
                            f"  - tier {tier}: base model {base_model} "
                            f"already cached at {bundle_dir_probe} — "
                            "skipping download.",
                            fg=typer.colors.GREEN,
                        )
                    else:
                        typer.secho(
                            f"  - tier {tier}: pulling base model "
                            f"{base_model} from HuggingFace…",
                            fg=typer.colors.CYAN,
                        )
                try:
                    path = bootstrap_bundle_from_hf(tier, cfg=cfg)
                    report["installed"].append({
                        "tier": tier, "path": str(path),
                        "source": "huggingface", "repo_id": base_model,
                        "cached_before_setup": already_cached,
                    })
                    if not json_output and not already_cached:
                        typer.secho(f"      installed at {path}",
                                    fg=typer.colors.GREEN)
                except BundleError as exc:
                    report["errors"].append(
                        f"tier {tier}: HF bootstrap failed: "
                        f"{getattr(exc, 'message', str(exc))}"
                    )
                    if not json_output:
                        typer.secho(
                            f"      HF bootstrap failed: "
                            f"{getattr(exc, 'message', str(exc))}",
                            fg=typer.colors.RED,
                        )
                except AgentRuntimeError as exc:
                    report["errors"].append(
                        f"tier {tier}: {getattr(exc, 'message', str(exc))}"
                    )
                    if not json_output:
                        typer.secho(
                            f"      failed: {getattr(exc, 'message', str(exc))}",
                            fg=typer.colors.RED,
                        )
                continue
            cid = str(entry["cid"]).strip()
            sha256 = entry.get("sha256")
            if not json_output:
                typer.secho(f"  - tier {tier}: pulling {cid[:14]}…",
                            fg=typer.colors.CYAN)
            try:
                path = pull_bundle(cid, tier=tier, cfg=cfg,
                                   verify_sha256=sha256)
                report["installed"].append({"tier": tier, "path": str(path),
                                              "cid": cid, "source": "ipfs"})
                if not json_output:
                    typer.secho(f"      installed at {path}",
                                fg=typer.colors.GREEN)
            except BundleError as exc:
                report["errors"].append(
                    f"tier {tier}: bundle error: {getattr(exc, 'message', str(exc))}"
                )
                if not json_output:
                    typer.secho(
                        f"      bundle error: {getattr(exc, 'message', str(exc))}",
                        fg=typer.colors.RED,
                    )
            except AgentRuntimeError as exc:
                report["errors"].append(
                    f"tier {tier}: {getattr(exc, 'message', str(exc))}"
                )
                if not json_output:
                    typer.secho(
                        f"      failed: {getattr(exc, 'message', str(exc))}",
                        fg=typer.colors.RED,
                    )

    if json_output:
        import json as _json
        typer.echo(_json.dumps(report, indent=2, default=str))
        return

    typer.echo("")
    if report["installed"]:
        typer.secho(
            f"Installed {len(report['installed'])} bundle(s). "
            "Your machine is ready to serve AICF compute.",
            fg=typer.colors.GREEN,
        )
    else:
        typer.secho(
            "No bundles installed. The miner will still run, but only "
            "tiers with an existing local bundle will earn AICF credits.",
            fg=typer.colors.YELLOW,
        )
    typer.echo("")
    typer.secho("Next step:", fg=typer.colors.CYAN)
    typer.echo(
        "  animica miner mine-blocks --count 0 --pool-stratum stratum+tcp://pool.animica.org:3333"
    )
    typer.echo(
        "  (uses your default wallet; pass --address anim1... to override.)"
    )


@app.command("mine-blocks")
def mine_blocks(
    address: Optional[str] = typer.Argument(
        None,
        help="Payout address (positional): wallet label or Bech32 address",
    ),
    count: int = typer.Option(
        ...,
        "--count",
        help="Number of blocks to mine. With --pool-stratum, 0 mines until stopped.",
    ),
    address_opt: Optional[str] = typer.Option(
        None,
        "--address",
        help="Payout address (option, for backward compat): wallet label or Bech32 address",
    ),
    pool_stratum: Optional[str] = typer.Option(
        None,
        "--pool-stratum",
        help=(
            "Pool stratum endpoint. Plain TCP: stratum+tcp://host:port. "
            "TLS: stratum+ssl://host:port (or stratum+tls://, ssl://, tls://). "
            "When set, mining runs against the pool instead of the local RPC."
        ),
    ),
    pool_worker: Optional[str] = typer.Option(
        None,
        "--pool-worker",
        help="Worker name reported to the stratum pool. Defaults to the payout address.",
    ),
    pool_scan_window: int = typer.Option(
        100_000,
        "--pool-scan-window",
        help="Nonce window size scanned per stratum job (only used with --pool-stratum).",
    ),
    pool_useful_work: bool = typer.Option(
        True,
        "--pool-useful-work/--no-pool-useful-work",
        help=(
            "Attach AI/quantum/storage/VDF UsefulWorkProof envelopes to "
            "outgoing stratum shares (default: enabled). Disable if your "
            "pool doesn't credit bonus AICF credits."
        ),
    ),
    aicf: bool = typer.Option(
        True,
        "--aicf/--no-aicf",
        help=(
            "Also run the AICF compute worker in parallel: serves real "
            "inference jobs from the network and earns AICF credits on top "
            "of PoW block rewards. Requires `animica miner setup` to have "
            "installed at least one model bundle. Default: enabled."
        ),
    ),
    aicf_endpoint: Optional[str] = typer.Option(
        None,
        "--aicf-endpoint",
        envvar="ANIMICA_AICF_ENDPOINT",
        help=(
            "Override the AICF protocol endpoint URL used by the worker. "
            "Set this when the miner is NOT running a local Animica full "
            "node — point it at a public/remote node (e.g. "
            "https://rpc.pool.animica.org/aicf). Without this, AICF defaults "
            "to the network's local 127.0.0.1 endpoint."
        ),
    ),
    llm: Optional[str] = typer.Option(
        None,
        "--llm",
        "--model",
        envvar="ANIMICA_AICF_MODEL",
        help=(
            "Pin which LLM the AICF inference worker should serve. Accepts a "
            "Hugging Face repo id (e.g. 'Qwen/Qwen2.5-0.5B-Instruct'), a "
            "local path, or one of the well-known short names ('phi3', "
            "'qwen2.5-0.5b', 'llama3-8b'). When omitted the worker falls "
            "back to the per-tier default."
        ),
    ),
    threads: int = typer.Option(
        0,
        "--threads",
        help="CPU threads for PoW search. 0 (default) auto-detects all "
              "cores (minus one) so mining saturates the machine. Use a "
              "positive integer to cap.",
    ),
    xmr: bool = typer.Option(
        False,
        "--xmr/--no-xmr",
        help=(
            "Dual-mine Monero (RandomX) alongside Animica SHA3, 50/50 thread "
            "split. The pool's cryptonote port handles XMR shares; the same "
            "anim1 address you mine with also accrues XMR earnings — set the "
            "payout currency (xmr or anm) via POST /api/pool/xmr/register."
        ),
    ),
    xmr_only: bool = typer.Option(
        False,
        "--xmr-only",
        help="Skip Animica side and mine ONLY Monero through the pool's "
             "cryptonote port. Implies --xmr.",
    ),
    xmrig_gpu: Optional[str] = typer.Option(
        None,
        "--xmrig-gpu",
        help=(
            "When dual-mining via xmrig (with --xmr / --xmr-only), pick the "
            "GPU backend for xmrig: 'cuda' | 'opencl' | 'both'. Animica SHA3 "
            "can use GPU via the xmrig CUDA scaffolding (may fall back to "
            "CPU until the kernel lands). Monero RandomX is CPU-by-design — "
            "GPU on the XMR side runs but at <10%% of equivalent CPU "
            "hashrate. Different from --gpu, which only affects the Python "
            "miner's local backend."
        ),
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
    # Propagate the AICF model / tier choice early so any AICF worker that
    # starts under our process tree picks it up.
    _apply_llm_flag(llm)

    # XMR dual-mining short-circuit. When --xmr or --xmr-only is passed,
    # mine-blocks transforms into a thin xmrig orchestrator (same as
    # `animica miner dual-mine`) and never enters the Python miner loop.
    # The user gets one command for both algos.
    if xmr or xmr_only:
        only_mode = "xmr" if xmr_only else None
        addr_arg = address or address_opt
        if not addr_arg:
            typer.echo("--xmr requires a payout address (anim1…)", err=True)
            raise typer.Exit(2)
        return dual_mine(
            address=addr_arg,
            pool_host="pool.animica.org",
            animica_port=3333,
            xmr_port=3333,
            threads=int(threads) if isinstance(threads, int) else 0,
            xmrig_path="",  # auto-resolve (env / cache / PATH / bundle / download)
            worker=None,
            only=only_mode,
            gpu=xmrig_gpu,
        )

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
    
    # Validate count. Pool mining supports count=0 as "run until stopped";
    # local RPC mining keeps the historical finite-count behavior.
    if count < 0 or (count == 0 and not pool_stratum):
        typer.secho(
            f"Error: count must be greater than 0 for local mining, got {count}",
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

    # ─── AICF compute worker (parallel earnings stream) ─────────────────────
    # The AICF worker runs alongside PoW in a background thread, regardless of
    # whether we're mining solo or against a stratum pool. It registers with
    # the network's AICF endpoint, claims inference jobs that match the
    # locally detected hardware tier(s), runs them, and submits results.
    aicf_stop = (lambda: None)
    aicf_stats: dict[str, object] = {"started": False}
    if aicf:
        # The CLI flag wins over the env var, then the env var, then the
        # network-default 127.0.0.1 endpoint from integration.yaml.
        if aicf_endpoint:
            os.environ["ANIMICA_AICF_ENDPOINT"] = aicf_endpoint.strip()
        try:
            aicf_stop, aicf_stats = _start_aicf_worker(resolved_address)
        except Exception as _aicf_exc:  # noqa: BLE001 — best-effort
            aicf_stats = {"started": False, "reason": repr(_aicf_exc)}
        if aicf_stats.get("started"):
            tiers_str = ", ".join(str(t) for t in (aicf_stats.get("tiers") or [])) or "auto"
            typer.secho(
                f"AICF compute worker active (tiers={tiers_str}, "
                f"endpoint={aicf_stats.get('endpoint')}). "
                "Earnings accrue in parallel to PoW.",
                fg=typer.colors.CYAN,
            )
        else:
            reason = str(aicf_stats.get("reason") or "AICF worker unavailable")
            typer.secho(
                f"AICF compute worker did not start: {reason}",
                fg=typer.colors.YELLOW,
            )
            typer.secho(
                "  Tip: run `animica miner setup` to install a model bundle, "
                "then re-run with --aicf, or pass --no-aicf to silence this.",
                fg=typer.colors.YELLOW,
            )
        try:
            import atexit as _atexit
            _atexit.register(aicf_stop)
        except Exception:  # noqa: BLE001
            pass

    # ─── Stratum pool path ──────────────────────────────────────────────────
    # `--pool-stratum` shifts mining away from local RPC: the pool issues
    # job templates, drives difficulty, and credits payouts to the wallet
    # address we authorize with. We dispatch BEFORE the local-node sync
    # check because pool mining doesn't require a local RPC node.
    if pool_stratum:
        import asyncio as _asyncio

        try:
            host, port, tls = _parse_pool_stratum_url(pool_stratum)
        except ValueError as exc:
            typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(2)
        worker_name = (pool_worker or resolved_address).strip()
        if not worker_name:
            worker_name = resolved_address

        # Resolve the device once at the CLI level so we can announce it
        # before the miner starts. `--device auto` (the default) prefers
        # GPU and falls back to CPU; see mining.device.auto_detect_device.
        # The stratum scanner inside CpuStratumMiner does the same
        # detection internally, but resolving here gives the user a clear
        # banner before connection starts.
        pool_device = (device.strip().lower() if isinstance(device, str) else "auto")
        if gpu:
            pool_device = "cuda"
        if pool_device == "auto":
            try:
                import sys as _sys
                _repo_root = Path(__file__).resolve().parents[3]
                if str(_repo_root) not in _sys.path:
                    _sys.path.insert(0, str(_repo_root))
                from mining.device import auto_detect_device as _auto
                pool_device = _auto() or "cpu"
            except Exception:
                pool_device = "cpu"
        typer.secho(
            f"Pool mining: target={count} block(s), pool={host}:{port}"
            f"{' (TLS)' if tls else ''}, "
            f"worker={worker_name}, payout={resolved_address}, device={pool_device}",
            fg=typer.colors.CYAN,
        )
        try:
            scan_window_int = int(pool_scan_window)
        except (TypeError, ValueError):
            scan_window_int = 100_000
        accepted = _asyncio.run(
            _run_pool_stratum_miner(
                host=host,
                port=port,
                tls=tls,
                worker=worker_name,
                address=resolved_address,
                target_blocks=int(count),
                scan_window=scan_window_int,
                enable_useful_work=bool(pool_useful_work),
                device=pool_device,
                threads=int(resolved_workers) if resolved_workers else 0,
            )
        )
        if int(count) == 0:
            typer.secho(
                f"Pool mining stopped: {accepted} accepted block(s).",
                fg=typer.colors.GREEN,
            )
            return
        if accepted < int(count):
            typer.secho(
                f"Pool mining ended with {accepted}/{count} accepted block(s).",
                fg=typer.colors.YELLOW,
            )
            raise typer.Exit(1 if accepted == 0 else 0)
        typer.secho(
            f"Pool mining complete: {accepted}/{count} block(s) accepted.",
            fg=typer.colors.GREEN,
        )
        return

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

    # Spawn AI/Quantum/Storage/VDF useful-work workers for the duration of
    # this solo mining session. They run in a background asyncio loop and
    # consume CPU/GPU per their own configuration (env vars). Receipts go
    # into the standard `mining.uw_inbox` queue; we just instrument it to
    # report job counts in the final summary.
    uw_stop, uw_stats = (lambda: None), {"started": False,
                                          "ai": 0, "quantum": 0,
                                          "storage": 0, "vdf": 0}
    if pool_useful_work:
        try:
            uw_stop, uw_stats = _start_solo_useful_work()
            if uw_stats.get("started"):
                typer.secho(
                    "Useful-work workers active in background "
                    "(AI / Quantum / Storage / VDF). Job counts will be "
                    "shown in the final summary.",
                    fg=typer.colors.CYAN,
                )
        except Exception as _uw_exc:    # noqa: BLE001 — best-effort
            typer.secho(
                f"Note: useful-work workers did not start ({_uw_exc}); "
                "PoW search will still saturate CPU.",
                fg=typer.colors.YELLOW,
            )

    # Per-session hashrate accumulators.
    session_hashes = 0
    session_pow_seconds = 0.0

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
                            or template.get("templateId")
                            or template.get("jobId")
                        )
                        template_height_dbg = (
                            template.get("height")
                            or template.get("number")
                            or header_view.get("height")
                            or header_view.get("number")
                        )
                        template_parent_dbg = (
                            template.get("parent", {}).get("hash")
                            or template.get("parent_hash")
                            or header_view.get("parentHash")
                            or header_view.get("parent_hash")
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

                        def _is_param_shape_error(
                            code: int | None, detail_text: str
                        ) -> bool:
                            return code == -32602 and any(
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
                            )

                        def _is_usable_template_payload(response: object) -> bool:
                            if not isinstance(response, dict):
                                return False
                            if not response.get("enabled", True):
                                return True
                            return (
                                isinstance(response.get("header"), dict)
                                and response.get("target") is not None
                            )

                        get_work_attempts = [
                            [resolved_address],
                            {"address": resolved_address},
                            {"payout_address": resolved_address},
                            {"payoutAddress": resolved_address},
                            [resolved_address, include_mempool],
                        ]
                        block_template_attempts = [
                            {
                                "address": resolved_address,
                                "include_mempool": include_mempool,
                                "ttlSeconds": int(template_ttl_s),
                            },
                            {
                                "payout_address": resolved_address,
                                "include_mempool": include_mempool,
                                "ttlSeconds": int(template_ttl_s),
                            },
                            {"address": resolved_address},
                            [resolved_address],
                        ]

                        last_exc = None
                        for payload in get_work_attempts:
                            try:
                                response = client.request("miner.getWork", payload)
                                if _is_usable_template_payload(response):
                                    return response
                                # Some legacy/mock implementations return None for
                                # unsupported methods instead of raising.
                                if response is None:
                                    continue
                                if isinstance(response, dict):
                                    last_exc = ValueError(
                                        "miner.getWork returned dict without required "
                                        "template fields (enabled/header/target)"
                                    )
                                    continue
                                last_exc = TypeError(
                                    f"miner.getWork returned non-dict payload: {type(response).__name__}"
                                )
                                continue
                            except Exception as exc:
                                last_exc = exc
                                code, message, data = _rpc_error_details(exc)
                                detail_text = _rpc_error_detail_text(message, data)
                                if _is_param_shape_error(code, detail_text):
                                    continue

                        for payload in block_template_attempts:
                            try:
                                if verbose:
                                    typer.echo(
                                        "  [Fallback] miner.getWork unavailable; "
                                        "trying miner.getBlockTemplate"
                                    )
                                response = client.request("miner.getBlockTemplate", payload)
                                if _is_usable_template_payload(response):
                                    return response
                                if response is None:
                                    continue
                                if isinstance(response, dict):
                                    last_exc = ValueError(
                                        "miner.getBlockTemplate returned dict without "
                                        "required template fields (enabled/header/target)"
                                    )
                                    continue
                                last_exc = TypeError(
                                    "miner.getBlockTemplate returned non-dict payload: "
                                    f"{type(response).__name__}"
                                )
                                continue
                            except Exception as exc:
                                last_exc = exc
                                code, message, data = _rpc_error_details(exc)
                                detail_text = _rpc_error_detail_text(message, data)
                                if _is_param_shape_error(code, detail_text):
                                    continue

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
                    pow_stats: dict = {}
                    nonce, digest = _mine_header(
                        header,
                        target_int,
                        workers=resolved_workers,
                        stats=pow_stats,
                    )
                    block_hashes = int(pow_stats.get("hashes", 0))
                    block_pow_s = float(pow_stats.get("elapsed_s", 0.0))
                    session_hashes += block_hashes
                    session_pow_seconds += block_pow_s
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
                                or template.get("templateId")
                                or template.get("jobId")
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
                        if submit_result is None or (
                            isinstance(submit_result, dict) and not submit_result
                        ):
                            raise RuntimeError(
                                "miner.submitWork returned empty response (falling back)"
                            )
                    except Exception as submit_error:
                        code, message, data = _rpc_error_details(submit_error)
                        detail_text = _rpc_error_detail_text(message, data)
                        should_fallback_submit_block = (
                            code == -32601
                            or (
                                "submitwork" in detail_text
                                and any(
                                    token in detail_text
                                    for token in (
                                        "unexpected method",
                                        "method not found",
                                        "unknown method",
                                        "not supported",
                                        "empty response",
                                    )
                                )
                            )
                        )

                        if should_fallback_submit_block:
                            if verbose:
                                typer.echo(
                                    "  submitWork unavailable; falling back to miner.submitBlock"
                                )
                            try:
                                if proxy:
                                    submit_result = proxy.sync_forward_request(
                                        "miner.submitBlock",
                                        block_payload,
                                        fallback_handler=lambda: client.request(
                                            "miner.submitBlock", block_payload
                                        ),
                                    )
                                else:
                                    submit_result = client.request(
                                        "miner.submitBlock", block_payload
                                    )
                            except Exception as submit_block_error:
                                submit_error = submit_block_error
                            else:
                                submit_error = None

                        if submit_error is None:
                            pass
                        else:
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

                    if submit_result is None:
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

                    if block_reward is None:
                        try:
                            coinbase_amount = int(
                                (template.get("coinbase") or {}).get("amount") or 0
                            )
                            if coinbase_amount > 0:
                                block_reward = coinbase_amount
                        except Exception:
                            pass

                    if credited_display is None and isinstance(block_reward, int) and block_reward > 0:
                        credited_display = block_reward

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

                    block_rate = (
                        block_hashes / block_pow_s if block_pow_s > 0 else 0.0
                    )
                    typer.secho(
                        f"  ACCEPTED: Block {total_mined}/{count} (height: {final_height}, "
                        f"head: {new_head_hash or 'unknown'}, "
                        f"reward: {reward_text}, "
                        f"credited: {credited_text}, "
                        f"balance_now: {balance_now if balance_now is not None else 'unknown'}, "
                        f"hashrate: {_fmt_hashrate(block_rate)} over "
                        f"{block_hashes:,} hashes in {block_pow_s:.1f}s)",
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

            avg_rate = (
                session_hashes / session_pow_seconds
                if session_pow_seconds > 0 else 0.0
            )
            typer.secho(
                f"PoW work: {session_hashes:,} hashes in "
                f"{session_pow_seconds:.1f}s "
                f"({_fmt_hashrate(avg_rate)} avg, "
                f"{resolved_workers} thread(s))",
                fg=typer.colors.CYAN,
            )
            if uw_stats.get("started"):
                typer.secho(
                    "Useful-work jobs served: "
                    f"AI={uw_stats.get('ai', 0)}  "
                    f"Quantum={uw_stats.get('quantum', 0)}  "
                    f"Storage={uw_stats.get('storage', 0)}  "
                    f"VDF={uw_stats.get('vdf', 0)}",
                    fg=typer.colors.CYAN,
                )

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
    finally:
        try:
            uw_stop()
        except Exception:
            pass


def _xmrig_platform_tag() -> Optional[str]:
    """Return the '<os>-<arch>' tag used in download/bundle names, or None
    for unsupported platforms."""
    import platform as _plat
    sysname = sys.platform
    machine = (_plat.machine() or "").lower()
    if machine in ("x86_64", "amd64"):
        arch = "x86_64"
    elif machine in ("arm64", "aarch64"):
        arch = "arm64"
    else:
        arch = machine or "unknown"
    if sysname.startswith("linux"):
        return f"linux-{arch}"
    if sysname == "darwin":
        return f"macos-{arch}"
    if sysname.startswith("win"):
        return f"windows-{arch}"
    return None


def _download_animica_xmrig(dest: Path) -> Optional[str]:
    """Best-effort download of the Animica-fork xmrig for this platform from the
    pool's downloads. Returns the path on success, None otherwise."""
    tag = _xmrig_platform_tag()
    if not tag:
        return None
    base = os.environ.get("ANIMICA_DOWNLOADS_URL", "https://pool.animica.org/downloads").rstrip("/")
    suffix = ".exe" if tag.startswith("windows") else ""
    url = f"{base}/xmrig-animica-{tag}{suffix}"
    try:
        import urllib.request
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".part")
        typer.echo(f"[dual-mine] fetching Animica xmrig for {tag} from {url} ...", err=True)
        req = urllib.request.Request(url, headers={"User-Agent": "animica-cli"})
        with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310 (trusted host)
            if getattr(r, "status", 200) != 200:
                return None
            data = r.read()
        # Guard against an HTML 404 page being saved as a binary.
        if len(data) < 100_000 or data[:15].lstrip().startswith(b"<"):
            return None
        tmp.write_bytes(data)
        tmp.replace(dest)
        dest.chmod(0o755)
        typer.echo(f"[dual-mine] saved {dest}", err=True)
        return str(dest)
    except Exception:
        return None


def _cached_xmrig_stale(cache: Path) -> bool:
    """Cheap freshness probe for the cached fork binary: HEAD the pool's
    served asset and compare Content-Length to the local file size. A stale
    cached miner against an updated pool protocol connects, rejects the
    handshake, and retries forever — until xmrig gives up with
    "no active pools, stop mining". Offline / HEAD failure ⇒ keep the cache
    (best effort, never blocks mining)."""
    tag = _xmrig_platform_tag()
    if not tag:
        return False
    base = os.environ.get("ANIMICA_DOWNLOADS_URL", "https://pool.animica.org/downloads").rstrip("/")
    suffix = ".exe" if tag.startswith("windows") else ""
    url = f"{base}/xmrig-animica-{tag}{suffix}"
    try:
        import urllib.request
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "animica-cli"})
        with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310 (trusted host)
            served = int(r.headers.get("Content-Length") or 0)
        return served > 100_000 and served != cache.stat().st_size
    except Exception:
        return False


def _resolve_animica_xmrig(explicit: Optional[str]) -> Optional[str]:
    """Locate the Animica-fork xmrig (SHA3 'animica' algo). Order:
    explicit flag -> $ANIMICA_XMRIG -> cached download (refreshed if the pool
    serves a different build) -> PATH -> package bundle -> dev build dirs ->
    auto-download. Returns None if nothing works."""
    import shutil

    cache = Path.home() / ".animica" / "bin" / ("xmrig-animica" + (".exe" if sys.platform.startswith("win") else ""))
    # Self-heal a stale cache BEFORE the candidate scan would pick it up:
    # the cache wins over PATH/bundle, so an outdated download otherwise
    # sticks forever no matter how often the pool's build is updated.
    if not explicit and not os.environ.get("ANIMICA_XMRIG") and cache.is_file() and _cached_xmrig_stale(cache):
        typer.echo(
            "[dual-mine] cached Animica xmrig differs from the pool's current "
            "build — refreshing ...", err=True,
        )
        refreshed = _download_animica_xmrig(cache)
        if refreshed:
            return refreshed
    candidates = []
    if explicit:
        candidates.append(explicit)
    if os.environ.get("ANIMICA_XMRIG"):
        candidates.append(os.environ["ANIMICA_XMRIG"])
    candidates.append(str(cache))
    for name in ("xmrig-animica", "animica-xmrig"):
        found = shutil.which(name)
        if found:
            candidates.append(found)
    # Bundled inside the installed package (animica/bin/...).
    try:
        pkg_bin = Path(__file__).resolve().parents[1] / "bin"
        tag = _xmrig_platform_tag()
        if tag:
            candidates.append(str(pkg_bin / f"xmrig-animica-{tag}"))
        candidates.append(str(pkg_bin / "xmrig-animica"))
    except Exception:
        pass
    # Dev/source build locations.
    candidates.append("/root/animica/external/xmrig/build/xmrig-notls")
    candidates.append(str(Path.cwd() / "external" / "xmrig" / "build" / "xmrig-notls"))

    for c in candidates:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    # Last resort: download for this platform into the cache.
    return _download_animica_xmrig(cache)


def _download_stock_xmrig(dest_dir: Path) -> Optional[str]:
    """Download the official xmrig release for this platform (for the Monero
    side) and extract the binary into dest_dir. Returns the path or None."""
    import io
    import json as _json
    import tarfile
    import urllib.request
    import zipfile
    import platform as _plat

    machine = (_plat.machine() or "").lower()
    is_arm = machine in ("arm64", "aarch64")
    if sys.platform == "darwin":
        tokens = ["macos-arm64"] if is_arm else ["macos-x64"]
    elif sys.platform.startswith("linux"):
        tokens = ["linux-static-x64", "linux-x64"] if not is_arm else ["linux-static-arm64", "linux-arm64"]
    elif sys.platform.startswith("win"):
        tokens = ["msvc-win64", "gcc-win64"]
    else:
        return None
    ua = {"User-Agent": "animica-cli"}
    try:
        rel = _json.load(urllib.request.urlopen(
            urllib.request.Request("https://api.github.com/repos/xmrig/xmrig/releases/latest", headers=ua), timeout=30))
        url = name = None
        for tok in tokens:
            for a in rel.get("assets", []):
                n = a.get("name", "")
                if tok in n and (n.endswith(".tar.gz") or n.endswith(".zip")):
                    url, name = a.get("browser_download_url"), n
                    break
            if url:
                break
        if not url:
            return None
        typer.echo(f"[dual-mine] downloading stock xmrig ({name}) for the Monero side ...", err=True)
        data = urllib.request.urlopen(urllib.request.Request(url, headers=ua), timeout=180).read()
        dest_dir.mkdir(parents=True, exist_ok=True)
        is_win = sys.platform.startswith("win")
        out = dest_dir / ("xmrig.exe" if is_win else "xmrig")
        if name.endswith(".zip"):
            zf = zipfile.ZipFile(io.BytesIO(data))
            member = next((m for m in zf.namelist() if m.endswith("xmrig.exe") or m.endswith("/xmrig") or m == "xmrig"), None)
            if not member:
                return None
            out.write_bytes(zf.read(member))
        else:
            tf = tarfile.open(fileobj=io.BytesIO(data))
            member = next((m for m in tf.getmembers() if m.isfile() and (m.name.endswith("/xmrig") or m.name == "xmrig")), None)
            if not member:
                return None
            ex = tf.extractfile(member)
            if not ex:
                return None
            out.write_bytes(ex.read())
        out.chmod(0o755)
        typer.echo(f"[dual-mine] saved {out}", err=True)
        return str(out)
    except Exception:
        return None


def _resolve_stock_xmrig() -> Optional[str]:
    """Find stock xmrig for the Monero side: PATH -> cached -> auto-download."""
    import shutil
    found = shutil.which("xmrig")
    if found:
        return found
    cache = Path.home() / ".animica" / "bin"
    cached = cache / ("xmrig.exe" if sys.platform.startswith("win") else "xmrig")
    if cached.is_file() and os.access(cached, os.X_OK):
        return str(cached)
    return _download_stock_xmrig(cache)


def _start_hashrate_reporter(*, http_port: int, pool_host: str, address: str, worker_tag: str):
    """Poll the local xmrig HTTP API and POST measured H/s to the pool.

    Reads the Animica (SHA3) backend's own hashrate from xmrig's
    ``/2/summary`` and reports it to ``/api/pool/hashrate/report`` every ~30s.
    This is the accurate, smooth source for the pool's network hashrate
    (xmrig counts every hash). Runs in a daemon thread; all errors are
    swallowed so a reporting hiccup never interrupts mining. Override the pool
    HTTP base with ANIMICA_POOL_API_URL (e.g. for local testing).
    """
    import json as _json
    import os as _os
    import threading
    import time as _t
    import urllib.request as _u

    base = (_os.getenv("ANIMICA_POOL_API_URL") or f"https://{pool_host}").rstrip("/")
    summary_url = f"http://127.0.0.1:{http_port}/2/summary"
    report_url = f"{base}/api/pool/hashrate/report"

    def _loop() -> None:
        _t.sleep(15)  # let xmrig warm up + connect to the pool
        while True:
            try:
                with _u.urlopen(summary_url, timeout=5) as r:
                    summary = _json.loads(r.read().decode("utf-8"))
                total = (summary.get("hashrate") or {}).get("total") or []
                # total = [10s, 60s, 15m] avg (entries may be null early on).
                hps = 0.0
                for idx in (1, 0, 2):  # prefer 60s, then 10s, then 15m
                    if idx < len(total) and total[idx]:
                        hps = float(total[idx])
                        break
                if hps > 0:
                    body = _json.dumps({
                        "worker": worker_tag,
                        "address": address,
                        "hps": hps,
                        "algo": "animica",
                    }).encode("utf-8")
                    req = _u.Request(
                        report_url, data=body,
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    try:
                        _u.urlopen(req, timeout=5).read()
                    except Exception:
                        pass
            except Exception:
                pass
            _t.sleep(30)

    th = threading.Thread(target=_loop, name="animica-hashrate-reporter", daemon=True)
    th.start()
    return th


@app.command("dual-mine")
def dual_mine(
    address: str = typer.Argument(
        ...,
        help="Animica payout address (anim1…). Same address is used for XMR share credit; register XMR/ANM payout currency via the pool portal.",
    ),
    pool_host: str = typer.Option(
        "pool.animica.org",
        "--pool-host",
        help="Pool hostname (animica port + cryptonote port both live here).",
    ),
    animica_port: int = typer.Option(
        3333, "--animica-port",
        help="Pool's Animica stratum port (SHA3 hashshare).",
    ),
    xmr_port: int = typer.Option(
        3333, "--xmr-port",
        help=(
            "Pool port for the Monero cryptonote stratum. Defaults to "
            "the same port as Animica — the pool sniffs the first JSON "
            "message and routes by protocol. Pass a different port only "
            "if your pool runs separate-port mode."
        ),
    ),
    threads: int = typer.Option(
        0, "--threads",
        help="Total CPU threads to allocate across BOTH algos. 0 = auto. "
             "The launcher gives 50%% to each algo, rounded.",
    ),
    xmrig_path: str = typer.Option(
        "",
        "--xmrig-path",
        help="Path to the Animica-fork xmrig binary (SHA3 support). Default: "
             "auto-resolve ($ANIMICA_XMRIG, ~/.animica/bin, PATH, bundled, then "
             "download for your OS). The stock xmrig for the XMR side is found on PATH.",
    ),
    worker: Optional[str] = typer.Option(
        None, "--worker",
        help="Worker tag reported to both pools. Default: short hostname.",
    ),
    only: Optional[str] = typer.Option(
        None, "--only",
        help="Run a single algo only: 'animica' or 'xmr'. Useful when you "
             "want pure-XMR mining (with the same 95/5 split + ANM payout "
             "option) instead of dual mode.",
    ),
    gpu: Optional[str] = typer.Option(
        None, "--gpu",
        help=(
            "GPU backend to use. 'cuda' for NVIDIA, 'opencl' for AMD/Intel, "
            "'both' for both. Honest expectations: Animica SHA3 *can* benefit "
            "from GPU (the xmrig fork has CUDA scaffolding that may still "
            "fall back to CPU until the kernel lands). Monero RandomX is "
            "CPU-by-design — passing --gpu on the XMR side starts the GPU "
            "worker but expect <10%% of equivalent CPU hashrate. Most users "
            "should leave this off."
        ),
    ),
) -> None:
    """Launch both Animica + Monero miners side-by-side.

    Spawns two xmrig processes:
      • Animica-fork xmrig → SHA3 hashshare on the Animica port.
      • Stock xmrig (any modern build with RandomX) → cryptonote on the
        pool's XMR port. The pool keeps a 5% fee on XMR blocks; the rest
        is split among miners by share weight. To get paid in ANM
        instead of XMR, POST your payout preference to
        https://pool.animica.org/api/pool/xmr/register .

    See `animica miner dual-mine --only xmr` for pure-Monero mode.
    """
    import os as _os
    import shutil
    import socket
    import subprocess as _sp

    if only and only not in ("animica", "xmr"):
        typer.echo(f"--only must be 'animica' or 'xmr' (got {only!r})", err=True)
        raise typer.Exit(2)

    auto_threads = _os.cpu_count() or 4
    total = threads if threads > 0 else auto_threads
    worker_tag = worker or socket.gethostname().split(".")[0][:16]

    # (label, argv) per miner — spawned and supervised below.
    specs: list = []

    ran_animica = False
    ran_xmr = False
    if only != "xmr":
        resolved_xmrig = _resolve_animica_xmrig(xmrig_path or None)
        if resolved_xmrig:
            xmrig_path = resolved_xmrig
            animica_threads = max(1, total if only == "animica" else total // 2)
            # Local xmrig HTTP API so we can read the miner's own measured H/s
            # and report it to the pool (accurate network-hashrate source).
            http_port = int(_os.getenv("ANIMICA_XMRIG_HTTP_PORT", "18222") or 18222)
            animica_cmd = [
                xmrig_path,
                "--algo", "animica",
                "--url", f"{pool_host}:{animica_port}",
                "--user", f"{address}.{worker_tag}",
                "--threads", str(animica_threads),
                "--http-host", "127.0.0.1",
                "--http-port", str(http_port),
                "--no-color",
                "--keepalive",
            ]
            typer.echo(f"[dual-mine] starting animica miner: {' '.join(animica_cmd)}")
            specs.append(("animica", animica_cmd))
            ran_animica = True
            _start_hashrate_reporter(
                http_port=http_port,
                pool_host=pool_host,
                address=address,
                worker_tag=worker_tag,
            )
        else:
            tag = _xmrig_platform_tag() or sys.platform
            msg = (
                "Animica-fork xmrig (SHA3 'animica' side) is not available for your "
                f"platform ({tag}) and no prebuilt could be downloaded.\n"
                "  Provide it with --xmrig-path /path/to/xmrig, $ANIMICA_XMRIG, or\n"
                "  ~/.animica/bin/xmrig-animica (build from the SHA3 xmrig fork)."
            )
            if only == "animica":
                typer.echo(msg, err=True)
                raise typer.Exit(1)
            # Dual mode: don't fail — fall through and mine Monero only.
            typer.echo(msg + "\n[dual-mine] continuing with Monero (XMR) only.", err=True)

    if only != "animica":
        stock_xmrig = _resolve_stock_xmrig()
        if not stock_xmrig:
            typer.echo(
                "Could not find or auto-download stock xmrig for the Monero side. "
                "Install it from https://xmrig.com/ and re-run.",
                err=True,
            )
            if not specs:
                raise typer.Exit(1)
        else:
            # If the Animica side is running, split 50/50; otherwise (pure XMR,
            # or dual that fell back to XMR-only) give XMR all the threads.
            xmr_threads = total if (only == "xmr" or not ran_animica) else max(1, total - max(1, total // 2))
            xmr_threads = max(1, xmr_threads)
            xmr_cmd = [
                stock_xmrig,
                "--algo", "rx/0",
                "--url", f"{pool_host}:{xmr_port}",
                "--user", f"{address}.{worker_tag}",
                "--pass", "x",
                "--threads", str(xmr_threads),
                "--keepalive",
                "--no-color",
            ]
            typer.echo(f"[dual-mine] starting xmr miner: {' '.join(xmr_cmd)}")
            specs.append(("xmr", xmr_cmd))
            ran_xmr = True

    if not specs:
        typer.echo("[dual-mine] nothing started", err=True)
        raise typer.Exit(1)

    active = []
    if ran_animica:
        active.append("Animica/SHA3")
    if ran_xmr:
        active.append("Monero/RandomX")
    if ran_animica and ran_xmr:
        summary = "mining BOTH Animica + Monero (threads split 50/50)"
    elif active == ["Monero/RandomX"]:
        summary = ("mining MONERO ONLY — the Animica/SHA3 side is NOT running "
                   "(no Animica-fork xmrig for this platform). No ANM shares will "
                   "be reported until that binary is provided (--xmrig-path / "
                   "$ANIMICA_XMRIG / ~/.animica/bin/xmrig-animica).")
    else:
        summary = "mining " + ", ".join(active)
    typer.echo(f"[dual-mine] {summary}")
    typer.echo(
        f"[dual-mine] {len(specs)} miner(s) supervised — auto-restart on exit "
        "or 'no active pools'. Ctrl-C to stop."
    )

    import threading as _threading
    import time as _time

    def _spawn(label: str, cmd: list):
        """Start a miner with a line-forwarding watcher. xmrig never exits on
        its own when a pool dies — it pauses with 'no active pools, stop
        mining' and limps along — so the watcher terminates it on that line
        and the supervisor loop below brings it back with a fresh socket."""
        p = _sp.Popen(cmd, stdout=_sp.PIPE, stderr=_sp.STDOUT)

        def _pump(proc=p):
            try:
                for raw in iter(proc.stdout.readline, b""):
                    line = raw.decode(errors="replace").rstrip()
                    print(f"[{label}] {line}", flush=True)
                    if "no active pools" in line:
                        typer.secho(
                            f"[dual-mine] {label}: miner reports no active pools "
                            "— restarting it for a clean reconnect",
                            fg=typer.colors.YELLOW, err=True,
                        )
                        try:
                            proc.terminate()
                        except Exception:
                            pass
            except Exception:
                pass

        _threading.Thread(target=_pump, daemon=True, name=f"pump-{label}").start()
        return p

    running = {
        label: {"cmd": cmd, "proc": _spawn(label, cmd), "fails": 0, "started": _time.monotonic()}
        for label, cmd in specs
    }
    try:
        while True:
            _time.sleep(2.0)
            for label, st in running.items():
                p = st["proc"]
                if p.poll() is None:
                    # Stable for 10 minutes ⇒ forget past failures so a later
                    # blip restarts quickly instead of at the capped delay.
                    if st["fails"] and _time.monotonic() - st["started"] > 600:
                        st["fails"] = 0
                    continue
                st["fails"] += 1
                delay = min(10 * (2 ** (st["fails"] - 1)), 300)
                typer.secho(
                    f"[dual-mine] {label} miner exited (code {p.returncode}) — "
                    f"restarting in {delay}s (attempt {st['fails']})",
                    fg=typer.colors.YELLOW, err=True,
                )
                _time.sleep(delay)
                st["proc"] = _spawn(label, st["cmd"])
                st["started"] = _time.monotonic()
    except KeyboardInterrupt:
        typer.echo("[dual-mine] interrupt — stopping miners")
        for st in running.values():
            try:
                st["proc"].terminate()
            except Exception:
                pass


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
