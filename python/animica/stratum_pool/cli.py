from __future__ import annotations

import argparse
import asyncio
import logging
from typing import Optional

import uvicorn

from .api import create_app
from .asic import Sha256PoolServer, Sha256RpcAdapter
from .config import PoolConfig, load_config_from_env
from .core import MiningCoreAdapter
from .job_manager import JobManager
from .metrics import PoolMetrics
from .payouts import PoolPayoutScheduler
from .stratum_server import StratumPoolServer


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Animica Stratum pool")
    parser.add_argument(
        "--host",
        default=None,
        help="Host to bind (default: ANIMICA_STRATUM_HOST or 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port to bind (default: ANIMICA_STRATUM_PORT or 3333)",
    )
    parser.add_argument(
        "--rpc-url",
        dest="rpc_url",
        default=None,
        help="Animica node RPC URL (default: ANIMICA_RPC_URL)",
    )
    parser.add_argument(
        "--rpc-timeout",
        dest="rpc_timeout",
        type=float,
        default=None,
        help="RPC request timeout in seconds (default: ANIMICA_STRATUM_RPC_TIMEOUT or 15.0)",
    )
    parser.add_argument(
        "--chain-id", dest="chain_id", type=int, default=None, help="Chain id"
    )
    parser.add_argument(
        "--pool-address", dest="pool_address", default=None, help="Pool payout address"
    )
    parser.add_argument(
        "--min-difficulty",
        dest="min_difficulty",
        type=float,
        default=None,
        help="Minimum share threshold (theta micro; legacy ratio if <= 1.0)",
    )
    parser.add_argument(
        "--max-difficulty",
        dest="max_difficulty",
        type=float,
        default=None,
        help="Maximum share threshold (theta micro; legacy ratio if <= 1.0)",
    )
    parser.add_argument(
        "--poll-interval",
        dest="poll_interval",
        type=float,
        default=None,
        help="Polling interval for new work",
    )
    parser.add_argument("--log-level", dest="log_level", default=None, help="Log level")
    parser.add_argument(
        "--api-host",
        dest="api_host",
        default=None,
        help="Host for the metrics API server",
    )
    parser.add_argument(
        "--api-port",
        dest="api_port",
        type=int,
        default=None,
        help="Port for the metrics API server",
    )
    parser.add_argument(
        "--mode",
        dest="pool_mode",
        default=None,
        help="Payout/accounting mode (pps|solo)",
    )
    parser.add_argument(
        "--payout-interval-seconds",
        dest="payout_interval_seconds",
        type=float,
        default=None,
        help="Automatic payout interval in seconds (0 disables timer payouts)",
    )
    parser.add_argument(
        "--payout-min-amount",
        dest="payout_min_amount",
        type=int,
        default=None,
        help="Minimum credited amount (base units) before a worker address is paid",
    )
    parser.add_argument(
        "--payout-wallet",
        dest="payout_wallet",
        default=None,
        help="Wallet label/address used to sign pool payouts (defaults to pool address)",
    )
    parser.add_argument(
        "--profile",
        dest="profile",
        default=None,
        help="Profile to run (hashshare|asic_sha256)",
    )
    parser.add_argument(
        "--extranonce2-size",
        dest="extranonce2_size",
        type=int,
        default=None,
        help="Size of extranonce2 for ASIC profile",
    )
    return parser


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def build_config(args: argparse.Namespace) -> PoolConfig:
    overrides = {k: v for k, v in vars(args).items() if v is not None}
    return load_config_from_env(overrides=overrides)


async def run_pool(config: PoolConfig, logger: Optional[logging.Logger] = None) -> None:
    logger = logger or logging.getLogger("animica.stratum_pool.cli")
    logger.info(
        "pool_startup",
        extra={
            "mode": config.pool_mode,
            "profile": config.profile,
            "rpc_url": config.rpc_url,
            "stratum_bind": f"{config.host}:{config.port}",
            "api_bind": f"{config.api_host}:{config.api_port}",
            "pool_address": config.pool_address,
            "min_difficulty": config.min_difficulty,
            "max_difficulty": config.max_difficulty,
            "payout_interval_seconds": config.payout_interval_seconds,
            "payout_min_amount": config.payout_min_amount,
            "payout_wallet": config.payout_wallet,
        },
    )

    if config.profile.startswith("asic"):
        adapter = Sha256RpcAdapter(
            config.rpc_url,
            config.pool_address,
            config.rpc_timeout,
            logger=logger,
        )
        server = Sha256PoolServer(
            adapter,
            host=config.host,
            port=config.port,
            extranonce2_size=config.extranonce2_size,
            default_difficulty=config.min_difficulty,
            logger=logger,
        )
        metrics = PoolMetrics(config, server.job_manager, server.stratum)
    else:
        adapter = MiningCoreAdapter(
            config.rpc_url,
            config.chain_id,
            config.pool_address,
            rpc_timeout_s=config.rpc_timeout,
            logger=logger,
        )
        job_manager = JobManager(adapter, config, logger=logger)
        server = StratumPoolServer(adapter, config, job_manager, logger=logger)
        metrics = PoolMetrics(config, job_manager, server.stratum)
        server.set_submit_hook(metrics.record_share)
    if config.profile.startswith("asic"):
        server.stratum.set_submit_hook(metrics.record_share)
    api_app = create_app(metrics)
    api_server = uvicorn.Server(
        uvicorn.Config(
            api_app,
            host=config.api_host,
            port=config.api_port,
            loop="asyncio",
            log_level=config.log_level.lower(),
        )
    )

    api_task = asyncio.create_task(api_server.serve())
    payout_task: Optional[asyncio.Task[None]] = None
    if float(config.payout_interval_seconds or 0.0) > 0:
        payout_runner = PoolPayoutScheduler(config=config, metrics=metrics, logger=logger)
        payout_task = asyncio.create_task(payout_runner.run())
    await server.start()
    logger.info(
        "Stratum pool listening",
        extra={
            "host": config.host,
            "port": config.port,
            "rpc": config.rpc_url,
            "api_port": config.api_port,
            "mode": config.pool_mode,
            "payout_interval_seconds": config.payout_interval_seconds,
            "payout_min_amount": config.payout_min_amount,
        },
    )
    try:
        await server.wait_closed()
    except asyncio.CancelledError:  # noqa: BLE001
        pass
    finally:
        if payout_task is not None:
            payout_task.cancel()
            try:
                await payout_task
            except asyncio.CancelledError:  # noqa: BLE001
                pass
        await server.stop()
        api_server.should_exit = True
        await api_task


def main(argv: Optional[list[str]] = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    config = build_config(args)
    _configure_logging(config.log_level)
    logger = logging.getLogger("animica.stratum_pool")
    try:
        asyncio.run(run_pool(config, logger=logger))
    except KeyboardInterrupt:
        logger.info("shutting down")


if __name__ == "__main__":
    main()
