#!/usr/bin/env python3
"""
mining.cli.mine - Convenience wrapper for mining.cli.miner

This module provides a simpler interface for the dev scripts to invoke
the miner CLI with sensible defaults for direct mining (not pool mining).

Usage from dev scripts:
    python -m mining.cli.mine --rpc URL --threads N --device DEVICE --address ADDR

This is equivalent to calling:
    python -m mining.cli.miner start --rpc-url URL --threads N --device DEVICE

For mining blocks to a specific address, use mine-blocks instead:
    python -m mining.cli.miner mine-blocks --address ADDR --count N
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional


def _parse_args(argv: List[str]) -> argparse.Namespace:
    """Parse arguments in the simplified format expected by dev scripts."""
    parser = argparse.ArgumentParser(
        prog="python -m mining.cli.mine",
        description="Simplified miner CLI for dev scripts",
    )
    parser.add_argument(
        "--rpc",
        type=str,
        default=os.environ.get("ANIMICA_RPC_URL", "http://127.0.0.1:8547"),
        help="RPC URL (default: http://127.0.0.1:8547)",
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=1,
        help="Number of worker threads (default: 1)",
    )
    parser.add_argument(
        "--device",
        type=str,
        default="cpu",
        choices=["cpu", "cuda", "rocm", "opencl", "metal", "gpu", "quantum"],
        help="Mining device (default: cpu)",
    )
    parser.add_argument(
        "--address",
        type=str,
        default=None,
        help="Miner payout address (optional, uses node default if not specified)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default=os.environ.get("ANIMICA_LOG_LEVEL", "info"),
        help="Logging level (default: info)",
    )
    
    # Parse known args and pass through any extras
    args, unknown = parser.parse_known_args(argv)
    args.extra_args = unknown
    return args


def main() -> int:
    """Main entry point - translates simplified args to miner CLI format."""
    args = _parse_args(sys.argv[1:])
    
    # Import the actual miner CLI
    try:
        from . import miner
    except ImportError as e:
        print(f"Error: Could not import mining.cli.miner: {e}", file=sys.stderr)
        return 1
    
    # Map device names to miner CLI device choices
    # quantum -> cpu (quantum work is handled by workers)
    # gpu -> auto (let miner detect GPU backend)
    device_map = {
        "quantum": "cpu",  # quantum mining uses CPU with quantum workers
        "gpu": "auto",     # auto-detect GPU backend (cuda/rocm/opencl/metal)
    }
    device = device_map.get(args.device, args.device)
    
    # Build arguments for the actual miner CLI
    # We always use 'start' command for continuous mining
    miner_args = [
        "start",
        "--rpc-url", args.rpc,
        "--threads", str(args.threads),
        "--device", device,
        "--log-level", args.log_level,
    ]
    
    # Note: The 'start' command doesn't accept --address directly
    # The address is configured through the node's miner settings
    # If the user provides --address, we'll warn them
    if args.address:
        print(
            f"Warning: --address {args.address} specified, but 'start' command "
            f"uses the node's configured miner address. To mine blocks to a specific "
            f"address, use: python -m mining.cli.miner mine-blocks --address {args.address}",
            file=sys.stderr,
        )
    
    # Add any extra args passed through
    miner_args.extend(args.extra_args)
    
    # Replace sys.argv and call the miner CLI
    original_argv = sys.argv
    try:
        sys.argv = ["mining.cli.miner"] + miner_args
        miner.main()
        return 0
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 1
    except KeyboardInterrupt:
        return 130
    except Exception as e:
        print(f"Error running miner: {e}", file=sys.stderr)
        return 1
    finally:
        sys.argv = original_argv


if __name__ == "__main__":
    sys.exit(main())
