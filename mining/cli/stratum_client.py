#!/usr/bin/env python3
"""
mining.cli.stratum_client - Wrapper for GPU mining via Stratum pool

This module provides a CLI interface for GPU miners to connect to a Stratum
pool. It's used by dev/mine-gpu.sh to enable pool-based mining.

Usage from dev scripts:
    python -m mining.cli.stratum_client --pool POOL_URL --device gpu --address ADDR

The pool URL should be in format: stratum+tcp://host:port
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import List
from urllib.parse import urlparse


def _parse_pool_url(pool_url: str) -> tuple[str, int]:
    """Parse pool URL in format stratum+tcp://host:port."""
    # Handle stratum+tcp:// prefix
    if pool_url.startswith("stratum+tcp://"):
        pool_url = pool_url.replace("stratum+tcp://", "")
    elif pool_url.startswith("stratum://"):
        pool_url = pool_url.replace("stratum://", "")
    
    # Parse host:port
    if ":" in pool_url:
        host, port_str = pool_url.rsplit(":", 1)
        try:
            port = int(port_str)
            return host, port
        except ValueError:
            raise ValueError(
                f"Invalid port in pool URL: {pool_url}. "
                f"Port must be numeric (e.g., stratum+tcp://pool.example.com:3333)"
            )
    else:
        # Default stratum port
        return pool_url, 3333


def _parse_args(argv: List[str]) -> argparse.Namespace:
    """Parse arguments for stratum client."""
    parser = argparse.ArgumentParser(
        prog="python -m mining.cli.stratum_client",
        description="Stratum client for GPU pool mining",
    )
    parser.add_argument(
        "--pool",
        type=str,
        default=os.environ.get("POOL_URL", "stratum+tcp://127.0.0.1:3333"),
        help="Pool URL in format stratum+tcp://host:port (default: stratum+tcp://127.0.0.1:3333)",
    )
    parser.add_argument(
        "--device",
        type=str,
        default="gpu",
        help="Mining device (gpu, cuda, rocm, opencl, metal) - currently informational only",
    )
    parser.add_argument(
        "--address",
        type=str,
        default=None,
        help="Miner payout address (required for pool mining)",
    )
    parser.add_argument(
        "--worker",
        type=str,
        default=os.environ.get("WORKER_NAME", "rig1"),
        help="Worker name for pool (default: rig1)",
    )
    parser.add_argument(
        "--framing",
        type=str,
        default="lines",
        choices=["lines", "lenpref"],
        help="Stratum framing mode (default: lines)",
    )
    parser.add_argument(
        "--auto-submit",
        action="store_true",
        help="Auto-submit dummy shares (for testing)",
    )
    
    # Parse known args and pass through any extras
    args, unknown = parser.parse_known_args(argv)
    args.extra_args = unknown
    return args


def main() -> int:
    """Main entry point - connects to stratum pool for GPU mining."""
    args = _parse_args(sys.argv[1:])
    
    # Validate required arguments
    if not args.address:
        print("Error: --address is required for pool mining", file=sys.stderr)
        print("Example: python -m mining.cli.stratum_client --pool stratum+tcp://pool.example.com:3333 --address anim1...", file=sys.stderr)
        return 1
    
    # Parse pool URL
    try:
        host, port = _parse_pool_url(args.pool)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    
    # Import the actual stratum client
    try:
        from mining.stratum_client import main as stratum_main
    except ImportError as e:
        print(f"Error: Could not import mining.stratum_client: {e}", file=sys.stderr)
        return 1
    
    # Build arguments for the actual stratum client
    stratum_args = [
        "--host", host,
        "--port", str(port),
        "--framing", args.framing,
        "--worker", args.worker,
        "--address", args.address,
    ]
    
    if args.auto_submit:
        stratum_args.append("--auto-submit")
    
    # Add any extra args passed through
    stratum_args.extend(args.extra_args)
    
    # Device info is informational - log it
    print(f"Starting Stratum client for {args.device} mining...")
    print(f"Pool: {args.pool} (host={host}, port={port})")
    print(f"Worker: {args.worker}")
    print(f"Address: {args.address}")
    print(f"Device: {args.device} (GPU mining via pool)")
    print("")
    
    # Replace sys.argv and call the stratum client
    original_argv = sys.argv
    try:
        sys.argv = ["mining.stratum_client"] + stratum_args
        stratum_main()
        return 0
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 1
    except KeyboardInterrupt:
        return 130
    except Exception as e:
        print(f"Error running stratum client: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1
    finally:
        sys.argv = original_argv


if __name__ == "__main__":
    sys.exit(main())
