from __future__ import annotations

import argparse
import asyncio
import logging
from typing import Optional

from .config import PoolConfig, load_config_from_env
from .core import MiningCoreAdapter
from .job_manager import JobManager
from .stratum_server import StratumPoolServer


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Animica Stratum pool")
    parser.add_argument("--host", default=None, help="Host to bind (default: ANIMICA_STRATUM_HOST or 0.0.0.0)")
    parser.add_argument("--port", type=int, default=None, help="Port to bind (default: ANIMICA_STRATUM_PORT or 3333)")
    parser.add_argument("--rpc-url", dest="rpc_url", default=None, help="Animica node RPC URL (default: ANIMICA_RPC_URL)")
    parser.add_argument("--chain-id", dest="chain_id", type=int, default=None, help="Chain id")
    parser.add_argument("--pool-address", dest="pool_address", default=None, help="Pool payout address")
    parser.add_argument("--min-difficulty", dest="min_difficulty", type=float, default=None, help="Minimum share target")
    parser.add_argument("--max-difficulty", dest="max_difficulty", type=float, default=None, help="Maximum share target")
    parser.add_argument("--poll-interval", dest="poll_interval", type=float, default=None, help="Polling interval for new work")
    parser.add_argument("--log-level", dest="log_level", default=None, help="Log level")
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
    adapter = MiningCoreAdapter(config.rpc_url, config.chain_id, config.pool_address, logger=logger)
    job_manager = JobManager(adapter, config, logger=logger)
    server = StratumPoolServer(adapter, config, job_manager, logger=logger)
    await server.start()
    logger = logger or logging.getLogger("animica.stratum_pool.cli")
    logger.info("Stratum pool listening", extra={"host": config.host, "port": config.port, "rpc": config.rpc_url})
    try:
        await server.wait_closed()
    except asyncio.CancelledError:  # noqa: BLE001
        pass
    finally:
        await server.stop()


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
