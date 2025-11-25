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

This CLI is a thin wrapper around the mining.orchestrator. It builds a config,
initializes the device backend, and runs the orchestrator until interrupted.

Signals:
  - SIGINT/SIGTERM: graceful shutdown.

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

from .. import errors as miner_errors
from .. import config as miner_config
from .. import device as miner_device

# Orchestrator has async entrypoints; we support multiple shapes for forward-compat.
from .. import orchestrator as miner_orchestrator


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
    start.add_argument("--threads", type=int, default=os.cpu_count() or 1,
                       help="number of worker threads (default: CPU count)")
    start.add_argument("--device", type=str, default=_env_default("ANIMICA_MINER_DEVICE", "auto"),
                       choices=["auto", "cpu", "cuda", "rocm", "opencl", "metal"],
                       help="compute backend")
    start.add_argument("--rpc-url", type=str,
                       default=_env_default("ANIMICA_RPC_URL", "http://127.0.0.1:8547"),
                       help="node JSON-RPC base URL")
    start.add_argument("--ws-url", type=str,
                       default=_env_default("ANIMICA_WS_URL", "ws://127.0.0.1:8547/ws"),
                       help="node WebSocket URL for newHeads/getwork")
    start.add_argument("--stratum-listen", type=_parse_host_port,
                       default=("0.0.0.0", int(_env_default("ANIMICA_STRATUM_PORT", "3333"))),
                       help="bind address for Stratum server (HOST:PORT), default 0.0.0.0:3333")
    start.add_argument("--getwork-enable", dest="getwork_enable", default=True, action=argparse.BooleanOptionalAction,
                       help="enable WS getwork service mounted into RPC app (default: enabled)")
    start.add_argument("--ai", action="store_true", default=True,
                       help="enable AI proof worker (AICF enqueue) [default true]")
    start.add_argument("--no-ai", dest="ai", action="store_false")
    start.add_argument("--quantum", action="store_true", default=True,
                       help="enable Quantum proof worker (AICF enqueue) [default true]")
    start.add_argument("--no-quantum", dest="quantum", action="store_false")
    start.add_argument("--storage", action="store_true", default=True,
                       help="enable Storage heartbeat worker [default true]")
    start.add_argument("--no-storage", dest="storage", action="store_false")
    start.add_argument("--vdf", action="store_true", default=True,
                       help="enable VDF bonus worker [default true]")
    start.add_argument("--no-vdf", dest="vdf", action="store_false")
    start.add_argument("--target", type=float, default=float(_env_default("ANIMICA_SHARE_TARGET", "0.25")),
                       help="share target as fraction of Θ (0<target<=1); lower => harder shares")
    start.add_argument("--chain-id", type=int, default=int(_env_default("ANIMICA_CHAIN_ID", "1")),
                       help="chain id (default: 1 animica main)")
    start.add_argument("--metrics", type=str, default=_env_default("ANIMICA_METRICS", ""),
                       help="Prometheus endpoint bind ':PORT' or 'HOST:PORT' (empty=disabled)")
    start.add_argument("--log-level", type=str, default=_env_default("ANIMICA_LOG_LEVEL", "info"),
                       help="logging level (debug, info, warning, error)")
    start.add_argument("--dry-run", action="store_true", help="print config then exit")

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
        "stratum": {"listen_host": ns.stratum_listen[0], "listen_port": ns.stratum_listen[1]},
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


async def _amain(argv: list[str]) -> int:
    args = _build_arg_parser().parse_args(argv)
    _setup_logging(args.log_level)
    log = logging.getLogger("miner.cli")

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
