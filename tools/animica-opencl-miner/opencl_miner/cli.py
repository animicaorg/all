from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from .miner import run_miner


class FriendlyFormatter(logging.Formatter):
    LEVEL_COLORS = {
        logging.DEBUG: "\033[36m",  # cyan
        logging.INFO: "\033[32m",  # green
        logging.WARNING: "\033[33m",  # yellow
        logging.ERROR: "\033[31m",  # red
        logging.CRITICAL: "\033[41m",  # red background
    }
    RESET = "\033[0m"

    def __init__(self, *, use_color: bool) -> None:
        fmt = "[%(asctime)s] %(level_display)s %(shortname)s | %(message)s"
        super().__init__(fmt=fmt, datefmt="%H:%M:%S")
        self.use_color = use_color and sys.stdout.isatty()

    def format(self, record: logging.LogRecord) -> str:
        record.shortname = record.name.rsplit(".", 1)[-1]
        level_name = record.levelname
        if self.use_color:
            color = self.LEVEL_COLORS.get(record.levelno)
            if color:
                level_name = f"{color}{level_name}{self.RESET}"
        record.level_display = level_name.ljust(8)
        return super().format(record)


def setup_logging(level: int) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(FriendlyFormatter(use_color=sys.stdout.isatty()))
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.setLevel(level)
    root.addHandler(handler)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Animica OpenCL Stratum miner",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Stratum host")
    parser.add_argument("--port", type=int, default=23454, help="Stratum port")
    parser.add_argument("--worker", default="opencl.worker", help="Worker name")
    parser.add_argument(
        "--address",
        required=True,
        help="Payout address (Bech32 anim1... or 0x hex)",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=50_000_000,
        help="Nonce count per GPU dispatch",
    )
    parser.add_argument(
        "--max-found",
        type=int,
        default=4,
        help="Maximum shares to return per scan batch",
    )
    parser.add_argument(
        "--platform",
        type=int,
        default=None,
        help="OpenCL platform index (default: 0)",
    )
    parser.add_argument(
        "--device",
        type=int,
        default=None,
        help="OpenCL device index (default: 0)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log verbosity",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv or sys.argv[1:])
    setup_logging(getattr(logging, args.log_level))

    try:
        asyncio.run(run_miner(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
