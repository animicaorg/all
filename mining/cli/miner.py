from __future__ import annotations

"""
Animica Miner CLI

Usage:
  python -m mining.cli.miner start [--threads N] [--device cpu|cuda|rocm|opencl|metal|auto]
                                   [--rpc-url URL] [--ws-url URL]
                                   [--stratum-listen HOST:PORT]
                                   [--getwork-enable/--no-getwork]
                                   [--ai] [--quantum] [--storage] [--vdf]
                                   [--target FLOAT] [--chain-id INT]
                                   [--metrics :PORT] [--log-level LEVEL]
                                   [--dry-run]

  python -m mining.cli.miner mine-blocks --address ADDR --count N [--threads N]
                                          [--rpc-url URL] [--log-level LEVEL]
                                          [--retry-delay SECONDS] [--no-timeout]
  
  The --threads option controls the number of CPU threads used for parallel nonce search.
  Higher thread counts can speed up mining on multi-core systems.

Commands:
  start       - Start the continuous miner (orchestrator)
  mine-blocks - Mine a specified number of blocks to a given address

The 'start' command is a thin wrapper around the mining.orchestrator. It builds
a config, initializes the device backend, and runs the orchestrator until interrupted.

The 'mine-blocks' command mines N blocks via the node's RPC interface, with block
rewards credited to the specified address. This is useful for testing and development.
RPC operations retry indefinitely on network errors with configurable delay between
attempts (default: 1.0 second).

Examples:
  # Start the miner
  python -m mining.cli.miner start --threads 4

  # Mine 5 blocks for testing with 4 threads
  python -m mining.cli.miner mine-blocks --address anim1test123 --count 5 --threads 4
  
  # Mine blocks with custom retry delay (2.5 seconds between retries)
  python -m mining.cli.miner mine-blocks --address anim1test123 --count 3 --retry-delay 2.5
  
  # Mine blocks without timeout (useful for high-load scenarios)
  python -m mining.cli.miner mine-blocks --address anim1test123 --count 5 --no-timeout

Retry Behavior:
  - The 'mine-blocks' command retries RPC operations indefinitely on connection/network errors
  - Each retry is logged with a timestamp and the error reason
  - Configure retry delay with --retry-delay (default: 1.0 second)
  - Non-retriable errors (e.g., invalid parameters) exit immediately
  - Set ANIMICA_RETRY_DELAY environment variable for default retry delay

Timeout Behavior:
  - Default timeout for RPC operations is 30 seconds
  - Use --no-timeout to disable timeout entirely (wait indefinitely)
  - Useful in high-load or slow network conditions where operations may take longer

Signals:
  - SIGINT/SIGTERM: graceful shutdown (start command).

Exit codes:
  0 on success/shutdown, non-zero on configuration or runtime errors.
"""

import argparse
import asyncio
import logging
import os
import signal
import sys
from typing import Any, Dict, Optional, Tuple

# Local imports
try:
    from ...version import __version__
except Exception:  # pragma: no cover
    __version__ = "0.0.0-dev"

# Orchestrator has async entrypoints; we support multiple shapes for forward-compat.
from .. import config as miner_config
from .. import device as miner_device
from .. import errors as miner_errors
from .. import orchestrator as miner_orchestrator

# RPC client - imported lazily in _run_mine_blocks to avoid import errors during collection
RpcClient = None


def _env_default(name: str, fallback: Optional[str] = None) -> Optional[str]:
    v = os.environ.get(name)
    return v if v is not None and v != "" else fallback


def _parse_host_port(value: str) -> Tuple[str, int]:
    if ":" not in value:
        raise argparse.ArgumentTypeError("expected HOST:PORT")
    host, port_str = value.rsplit(":", 1)
    try:
        return host, int(port_str)
    except ValueError as e:  # pragma: no cover
        raise argparse.ArgumentTypeError("invalid port") from e


def _setup_logging(level: str) -> None:
    lvl = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=lvl,
        format="%(asctime)s.%(msecs)03d %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="omni miner", description="Animica built-in miner")
    sub = p.add_subparsers(dest="cmd", required=True)

    start = sub.add_parser("start", help="start the miner")
    start.add_argument(
        "--threads",
        type=int,
        default=os.cpu_count() or 1,
        help="number of worker threads (default: CPU count)",
    )
    start.add_argument(
        "--device",
        type=str,
        default=_env_default("ANIMICA_MINER_DEVICE", "auto"),
        choices=["auto", "cpu", "cuda", "rocm", "opencl", "metal"],
        help="compute backend",
    )
    start.add_argument(
        "--rpc-url",
        type=str,
        default=_env_default("ANIMICA_RPC_URL", "http://127.0.0.1:8547"),
        help="node JSON-RPC base URL",
    )
    start.add_argument(
        "--ws-url",
        type=str,
        default=_env_default("ANIMICA_WS_URL", "ws://127.0.0.1:8547/ws"),
        help="node WebSocket URL for newHeads/getwork",
    )
    start.add_argument(
        "--stratum-listen",
        type=_parse_host_port,
        default=("0.0.0.0", int(_env_default("ANIMICA_STRATUM_PORT", "3333"))),
        help="bind address for Stratum server (HOST:PORT), default 0.0.0.0:3333",
    )
    start.add_argument(
        "--getwork-enable",
        dest="getwork_enable",
        default=True,
        action=argparse.BooleanOptionalAction,
        help="enable WS getwork service mounted into RPC app (default: enabled)",
    )
    start.add_argument(
        "--ai",
        action="store_true",
        default=True,
        help="enable AI proof worker (AICF enqueue) [default true]",
    )
    start.add_argument("--no-ai", dest="ai", action="store_false")
    start.add_argument(
        "--quantum",
        action="store_true",
        default=True,
        help="enable Quantum proof worker (AICF enqueue) [default true]",
    )
    start.add_argument("--no-quantum", dest="quantum", action="store_false")
    start.add_argument(
        "--storage",
        action="store_true",
        default=True,
        help="enable Storage heartbeat worker [default true]",
    )
    start.add_argument("--no-storage", dest="storage", action="store_false")
    start.add_argument(
        "--vdf",
        action="store_true",
        default=True,
        help="enable VDF bonus worker [default true]",
    )
    start.add_argument("--no-vdf", dest="vdf", action="store_false")
    start.add_argument(
        "--target",
        type=float,
        default=float(_env_default("ANIMICA_SHARE_TARGET", "0.25")),
        help="share target as fraction of Θ (0<target<=1); lower => harder shares",
    )
    start.add_argument(
        "--chain-id",
        type=int,
        default=int(_env_default("ANIMICA_CHAIN_ID", "1")),
        help="chain id (default: 1 animica main)",
    )
    start.add_argument(
        "--metrics",
        type=str,
        default=_env_default("ANIMICA_METRICS", ""),
        help="Prometheus endpoint bind ':PORT' or 'HOST:PORT' (empty=disabled)",
    )
    start.add_argument(
        "--log-level",
        type=str,
        default=_env_default("ANIMICA_LOG_LEVEL", "info"),
        help="logging level (debug, info, warning, error)",
    )
    start.add_argument("--dry-run", action="store_true", help="print config then exit")

    # mine-blocks subcommand
    mine_blocks = sub.add_parser(
        "mine-blocks",
        help="mine a specified number of blocks to a given address"
    )
    mine_blocks.add_argument(
        "--address",
        type=str,
        required=True,
        help="payout address for mined blocks (e.g., anim1...)",
    )
    mine_blocks.add_argument(
        "--count",
        type=int,
        required=True,
        help="number of blocks to mine (must be > 0)",
    )
    mine_blocks.add_argument(
        "--threads",
        type=int,
        default=os.cpu_count() or 1,
        help="number of CPU threads for mining (default: CPU count)",
    )
    mine_blocks.add_argument(
        "--rpc-url",
        type=str,
        default=_env_default("ANIMICA_RPC_URL", "http://127.0.0.1:8547"),
        help="node JSON-RPC base URL",
    )
    mine_blocks.add_argument(
        "--log-level",
        type=str,
        default=_env_default("ANIMICA_LOG_LEVEL", "info"),
        help="logging level (debug, info, warning, error)",
    )
    mine_blocks.add_argument(
        "--retry-delay",
        type=float,
        default=float(_env_default("ANIMICA_RETRY_DELAY", "1.0")),
        help="delay between RPC retry attempts in seconds (default: 1.0)",
    )
    mine_blocks.add_argument(
        "--no-timeout",
        action="store_true",
        default=False,
        help="disable RPC timeout (wait indefinitely). Useful for high-load or slow network conditions.",
    )

    return p


def _normalize_metrics_bind(spec: str | None) -> Optional[Tuple[str, int]]:
    if not spec:
        return None
    s = spec.strip()
    if s.startswith(":"):
        return ("0.0.0.0", int(s[1:]))
    return _parse_host_port(s)


def _device_from_choice(choice: str) -> str:
    if choice == "auto":
        # Ask device module to choose; it may fall back to CPU.
        return miner_device.Device.auto_select()
    return choice


def _build_orchestrator_config(ns: argparse.Namespace) -> Dict[str, Any]:
    device = _device_from_choice(ns.device)
    metrics_bind = _normalize_metrics_bind(ns.metrics)

    cfg = {
        "threads": int(max(1, ns.threads)),
        "device": device,
        "rpc_url": ns.rpc_url,
        "ws_url": ns.ws_url,
        "stratum": {
            "listen_host": ns.stratum_listen[0],
            "listen_port": ns.stratum_listen[1],
        },
        "getwork": {"enable": bool(ns.getwork_enable)},
        "enable_workers": {
            "ai": bool(ns.ai),
            "quantum": bool(ns.quantum),
            "storage": bool(ns.storage),
            "vdf": bool(ns.vdf),
        },
        "share_target_fraction": float(ns.target),
        "chain_id": int(ns.chain_id),
        "metrics": {"bind": metrics_bind} if metrics_bind else {"bind": None},
        "log_level": ns.log_level.upper(),
        # room for future knobs
    }
    return cfg


async def _run_orchestrator(cfg: Dict[str, Any]) -> None:
    """
    Try a few orchestrator entrypoint shapes to keep CLI forward/backward compatible.
    Priority:
      1) orchestrator.cli_main(cfg)
      2) orchestrator.run(cfg)
      3) orchestrator.Orchestrator(cfg).run_forever()
    """
    # Shape 1
    if hasattr(miner_orchestrator, "cli_main"):
        maybe = miner_orchestrator.cli_main  # type: ignore[attr-defined]
        if asyncio.iscoroutinefunction(maybe):
            await maybe(cfg)  # type: ignore[misc]
            return
        else:
            maybe(cfg)  # type: ignore[misc]
            return

    # Shape 2
    if hasattr(miner_orchestrator, "run"):
        run = miner_orchestrator.run  # type: ignore[attr-defined]
        if asyncio.iscoroutinefunction(run):
            await run(cfg)  # type: ignore[misc]
            return
        else:
            run(cfg)  # type: ignore[misc]
            return

    # Shape 3
    if hasattr(miner_orchestrator, "Orchestrator"):
        Orchestrator = miner_orchestrator.Orchestrator  # type: ignore[attr-defined]
        orch = Orchestrator(cfg)  # type: ignore[call-arg]
        # prefer async if available
        if hasattr(orch, "run_forever_async") and asyncio.iscoroutinefunction(orch.run_forever_async):  # type: ignore[attr-defined]
            await orch.run_forever_async()  # type: ignore[misc]
        elif hasattr(orch, "run_forever"):
            orch.run_forever()  # type: ignore[attr-defined]
        else:  # pragma: no cover
            raise RuntimeError("orchestrator entrypoint not found")
    else:  # pragma: no cover
        raise RuntimeError("mining.orchestrator module missing Orchestrator class")


async def _run_mine_blocks(args: argparse.Namespace, log: logging.Logger) -> int:
    """Execute the mine-blocks command."""
    # Validate count
    if args.count <= 0:
        log.error("count must be greater than 0, got %d", args.count)
        return 2

    # Validate address (basic check)
    if not args.address or not args.address.strip():
        log.error("address is required")
        return 2
    
    # Validate retry delay
    if args.retry_delay <= 0:
        log.error("retry-delay must be greater than 0, got %.3f", args.retry_delay)
        return 2

    # Lazy import RpcClient to avoid import errors during test collection
    rpc_client = None
    try:
        from sdk.python.omni_sdk.rpc.http import RpcClient as RPCClient
        rpc_client = RPCClient
    except (ImportError, ModuleNotFoundError, RuntimeError):
        try:
            from omni_sdk.rpc.http import RpcClient as RPCClient  # type: ignore
            rpc_client = RPCClient
        except (ImportError, ModuleNotFoundError, RuntimeError):  # pragma: no cover
            pass

    # Check if RpcClient is available
    if rpc_client is None:
        log.error(
            "RpcClient not available. Please install omni_sdk: pip install -e sdk/python"
        )
        return 3

    # Set timeout based on --no-timeout flag
    timeout_value = None if args.no_timeout else 30.0
    timeout_msg = "no timeout" if args.no_timeout else "30.0s timeout"
    
    log.info(
        "Mining %d block(s) with payout to address %s via RPC %s (threads=%d, retry_delay=%.1fs, %s)",
        args.count,
        args.address,
        args.rpc_url,
        args.threads,
        args.retry_delay,
        timeout_msg,
    )

    # JSON-RPC error code constant for invalid params (JSON-RPC 2.0 spec)
    JSONRPC_INVALID_PARAMS = -32602
    
    # Import RpcError for proper exception handling
    try:
        from omni_sdk.errors import RpcError, JsonRpcCode
    except ImportError:
        # Fallback if SDK not available or older version
        RpcError = None  # type: ignore
        JsonRpcCode = None  # type: ignore
    
    # Infinite retry loop for RPC operations
    attempt = 0
    while True:
        attempt += 1
        try:
            with rpc_client(args.rpc_url, timeout=timeout_value) as client:
                # Call miner.mine RPC method with address and threads parameters
                # For backward compatibility, try with full params first, fall back if not supported
                try:
                    result = client.request("miner.mine", {
                        "count": args.count,
                        "address": args.address,
                        "threads": args.threads
                    })
                except Exception as e:
                    # If the RPC rejects params (older node), try with just count and address
                    # Check for INVALID_PARAMS error code (preferred) or param names in error message (fallback)
                    # String matching is only used as last resort for compatibility with older SDK versions
                    is_param_error = False
                    if RpcError is not None and isinstance(e, RpcError):
                        # Preferred: Use structured error code
                        is_param_error = (
                            e.code == JsonRpcCode.INVALID_PARAMS if JsonRpcCode else e.code == JSONRPC_INVALID_PARAMS
                        )
                    elif "threads" in str(e).lower() or "address" in str(e).lower() or "unexpected" in str(e).lower():
                        # Fallback: String matching for older SDK versions
                        is_param_error = True
                    
                    if is_param_error:
                        # Try without threads parameter (node doesn't support it yet)
                        try:
                            result = client.request("miner.mine", {"count": args.count, "address": args.address})
                        except Exception as e2:
                            # Still failing, try legacy format without address
                            is_param_error2 = False
                            if RpcError is not None and isinstance(e2, RpcError):
                                is_param_error2 = (
                                    e2.code == JsonRpcCode.INVALID_PARAMS if JsonRpcCode else e2.code == JSONRPC_INVALID_PARAMS
                                )
                            elif "address" in str(e2).lower() or "unexpected" in str(e2).lower():
                                is_param_error2 = True
                            
                            if is_param_error2:
                                log.warning(
                                    "Node does not support payout address or threads (older version). "
                                    "Mining to node's default miner address with default threads."
                                )
                                result = client.request("miner.mine", [args.count])
                            else:
                                raise
                    else:
                        raise

                mined = result.get("mined", 0)
                height = result.get("height", 0)
                total_reward = result.get("totalReward", 0)
                rewards_list = result.get("rewards", [])

                if mined == 0:
                    log.warning("No blocks were mined (may have failed)")
                    return 4
                elif mined < args.count:
                    log.warning(
                        "Only %d of %d requested blocks were mined",
                        mined,
                        args.count,
                    )

                # Display per-block rewards
                if rewards_list:
                    for i, reward_info in enumerate(rewards_list, 1):
                        block_height = reward_info.get("height", "?")
                        block_reward = reward_info.get("reward", 0)
                        # Convert nANM to ANM for display (1 ANM = 10^9 nANM)
                        reward_anm = block_reward / 1_000_000_000
                        log.info(
                            "Block %d/%d mined (height %s, reward %.9f ANM = %d nANM)",
                            i,
                            mined,
                            block_height,
                            reward_anm,
                            block_reward,
                        )
                
                # Display total reward summary
                total_reward_anm = total_reward / 1_000_000_000
                log.info(
                    "Successfully mined %d block(s). New chain height: %d. Total reward: %.9f ANM (%d nANM)",
                    mined,
                    height,
                    total_reward_anm,
                    total_reward,
                )
                return 0

        except (RuntimeError, ConnectionError, OSError, TimeoutError) as e:
            # Network/connection/timeout errors - retry indefinitely
            from datetime import datetime
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            error_str = str(e)
            
            # Provide hint about --no-timeout on first timeout occurrence
            # Check for timeout indicators: TimeoutError type or "timed out" in message
            is_timeout = isinstance(e, TimeoutError) or "timed out" in error_str.lower()
            if is_timeout and not args.no_timeout and attempt == 1:
                log.warning("Timeout occurred. For long-running operations, consider using --no-timeout flag.")
            
            log.warning(
                "[%s] Retrying mining operation due to RPC error (attempt %d): %s. Retrying in %.1fs...",
                timestamp,
                attempt,
                e,
                args.retry_delay,
            )
            await asyncio.sleep(args.retry_delay)
            continue  # Retry indefinitely
        except Exception as e:
            # Non-retriable errors (e.g., invalid params, application logic errors)
            # Check if this is a non-retriable RPC error
            if RpcError is not None and isinstance(e, RpcError):
                # RpcError from the server indicates an application error, not a network issue
                log.exception("Failed to mine blocks via RPC (non-retriable): %s", e)
                return 5
            else:
                # Unknown exception - also retry (could be network-related)
                from datetime import datetime
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                log.warning(
                    "[%s] Retrying mining operation due to unexpected error (attempt %d): %s. Retrying in %.1fs...",
                    timestamp,
                    attempt,
                    e,
                    args.retry_delay,
                )
                await asyncio.sleep(args.retry_delay)
                continue  # Retry indefinitely


async def _amain(argv: list[str]) -> int:
    args = _build_arg_parser().parse_args(argv)
    _setup_logging(args.log_level)
    log = logging.getLogger("miner.cli")

    # Handle mine-blocks command
    if args.cmd == "mine-blocks":
        return await _run_mine_blocks(args, log)

    if args.cmd != "start":
        raise RuntimeError(f"unknown cmd {args.cmd}")  # pragma: no cover

    # Merge defaults with config module (so config.py stays the single source of truth).
    # We read config.py defaults and overlay CLI args.
    cfg = _build_orchestrator_config(args)

    # Enforce sane ranges
    if not (0.0 < cfg["share_target_fraction"] <= 1.0):
        log.error("share target must be in (0,1], got %s", cfg["share_target_fraction"])
        return 2

    # Device probe early to fail fast
    try:
        miner_device.Device.ensure_available(cfg["device"])
    except miner_errors.DeviceUnavailable as e:
        log.error("device unavailable: %s", e)
        return 3

    # Print config and exit if dry-run
    if args.dry_run:
        import json

        print(json.dumps(cfg, indent=2, sort_keys=True))
        return 0

    # Install signal handlers for graceful shutdown
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _signal_handler(sig: int, frame: Any | None) -> None:
        log.info("received signal %s: shutting down…", sig)
        stop_event.set()

    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(s, _signal_handler)  # type: ignore[arg-type]
        except Exception:  # pragma: no cover (on Windows/macos)
            pass

    # Run orchestrator until signaled
    log.info(
        "Animica miner %s starting | device=%s threads=%s rpc=%s ws=%s stratum=%s:%d workers(ai=%s,quantum=%s,storage=%s,vdf=%s) target=%.3f Θ",
        __version__,
        cfg["device"],
        cfg["threads"],
        cfg["rpc_url"],
        cfg["ws_url"],
        cfg["stratum"]["listen_host"],
        cfg["stratum"]["listen_port"],
        cfg["enable_workers"]["ai"],
        cfg["enable_workers"]["quantum"],
        cfg["enable_workers"]["storage"],
        cfg["enable_workers"]["vdf"],
        cfg["share_target_fraction"],
    )

    # Run orchestrator in a task so we can react to stop_event
    orch_task = asyncio.create_task(_run_orchestrator(cfg), name="orchestrator")

    # Wait for either orchestrator to finish (error or normal) or a stop signal
    done, pending = await asyncio.wait(
        {orch_task, asyncio.create_task(stop_event.wait(), name="stop_wait")},
        return_when=asyncio.FIRST_COMPLETED,
    )

    # If stop was triggered, try to cancel orchestrator
    if any(t.get_name() == "stop_wait" for t in done):
        log.info("stop requested; cancelling orchestrator…")
        orch_task.cancel()
        try:
            await orch_task
        except asyncio.CancelledError:
            pass

    # Check orchestrator outcome
    if orch_task in done:
        try:
            await orch_task
        except Exception as e:  # pragma: no cover
            log.exception("orchestrator crashed: %s", e)
            return 4

    log.info("miner stopped")
    return 0


def main() -> None:
    try:
        rc = asyncio.run(_amain(sys.argv[1:]))
    except KeyboardInterrupt:  # pragma: no cover
        rc = 130
    sys.exit(rc)


if __name__ == "__main__":
    main()
