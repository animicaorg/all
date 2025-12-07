"""
CLI for hash worker daemon.

Usage:
    python -m python.animica.hash_worker.cli start --backend cpu
    python -m python.animica.hash_worker.cli test-job --algorithm SHA256 --target 16
"""

from __future__ import annotations

import argparse
import logging
import sys

from python.animica.hash_work.algorithms import HashAlgorithm

from .daemon import DaemonConfig, HashWorkerDaemon, load_config_from_env


def setup_logging(verbose: bool = False) -> None:
    """Setup logging configuration."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )


def cmd_start(args: argparse.Namespace) -> int:
    """Start the hash worker daemon."""
    setup_logging(args.verbose)

    # Load config from env or override with CLI args
    config = load_config_from_env()

    if args.backend:
        config.backend_type = args.backend
    if args.rpc_url:
        config.rpc_url = args.rpc_url
    if args.chain_id:
        config.chain_id = args.chain_id
    if args.worker_address:
        config.worker_address = args.worker_address
    if args.state_file:
        config.state_file = args.state_file

    # Create and start daemon
    daemon = HashWorkerDaemon(config)

    try:
        daemon.start()
        return 0
    except KeyboardInterrupt:
        print("\nShutting down...")
        daemon.stop()
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_test_job(args: argparse.Namespace) -> int:
    """Test job execution without daemon."""
    setup_logging(args.verbose)

    from .backends import get_backend

    backend = get_backend(args.backend)

    # Create test job
    import hashlib

    input_data = args.input_data.encode() if args.input_data else b"test input data"
    input_commitment = hashlib.sha256(input_data).digest()

    print(f"Testing hash work with backend: {backend.get_backend_id()}")
    print(f"Device type: {backend.get_device_type().value}")
    print(f"Algorithm: {args.algorithm}")
    print(f"Target bits: {args.target}")
    print(f"Max iterations: {args.max_iterations}")
    print(f"Input commitment: {input_commitment.hex()}\n")

    # Execute work
    import time

    start = time.time()

    try:
        algorithm = HashAlgorithm(args.algorithm.upper())
    except ValueError:
        print(f"Error: Unknown algorithm '{args.algorithm}'", file=sys.stderr)
        return 1

    result = backend.execute_hash_work(
        algorithm=algorithm,
        input_commitment=input_commitment,
        target_bits=args.target,
        max_iterations=args.max_iterations,
    )

    elapsed = time.time() - start

    if result.success:
        print(f"✓ Success! Found solution in {elapsed:.3f}s")
        print(f"  Output hash: {result.output_hash.hex()}")
        print(f"  Nonce: {result.nonce.hex()}")
        print(f"  Iterations: {result.iterations}")
        print(f"  Hash rate: {result.iterations / elapsed:.0f} H/s")
        return 0
    else:
        print(f"✗ Failed: {result.error}")
        print(f"  Time elapsed: {elapsed:.3f}s")
        return 1


def cmd_benchmark(args: argparse.Namespace) -> int:
    """Benchmark backend performance."""
    setup_logging(args.verbose)

    from .backends import get_backend

    backend = get_backend(args.backend)

    print(f"Benchmarking {backend.get_backend_id()}...")
    print(f"Device type: {backend.get_device_type().value}\n")

    import hashlib
    import time

    input_commitment = hashlib.sha256(b"benchmark data").digest()

    # Test different difficulties
    difficulties = [8, 12, 16, 20] if not args.target else [args.target]

    for target_bits in difficulties:
        print(f"Testing target={target_bits} bits...")

        try:
            algorithm = HashAlgorithm(args.algorithm.upper())
        except ValueError:
            print(f"Error: Unknown algorithm '{args.algorithm}'", file=sys.stderr)
            return 1

        start = time.time()
        result = backend.execute_hash_work(
            algorithm=algorithm,
            input_commitment=input_commitment,
            target_bits=target_bits,
            max_iterations=args.max_iterations,
        )
        elapsed = time.time() - start

        if result.success:
            hashrate = result.iterations / elapsed
            print(
                f"  ✓ {result.iterations} iterations in {elapsed:.3f}s "
                f"({hashrate:.0f} H/s)\n"
            )
        else:
            print(f"  ✗ {result.error}\n")

    return 0


def main(argv: list[str] | None = None) -> int:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Animica hash worker daemon",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # start command
    start_parser = subparsers.add_parser("start", help="Start the hash worker daemon")
    start_parser.add_argument(
        "--backend",
        choices=["cpu", "gpu", "asic", "quantum"],
        help="Backend type (default: from HASH_BACKEND_TYPE env)",
    )
    start_parser.add_argument("--rpc-url", help="RPC endpoint URL")
    start_parser.add_argument("--chain-id", type=int, help="Chain ID")
    start_parser.add_argument("--worker-address", help="Worker address")
    start_parser.add_argument("--state-file", help="State file path")

    # test-job command
    test_parser = subparsers.add_parser("test-job", help="Test job execution")
    test_parser.add_argument(
        "--backend",
        default="cpu",
        choices=["cpu", "gpu", "asic", "quantum"],
        help="Backend to test (default: cpu)",
    )
    test_parser.add_argument(
        "--algorithm",
        default="SHA256",
        help="Hash algorithm (default: SHA256)",
    )
    test_parser.add_argument(
        "--target",
        type=int,
        default=16,
        help="Target difficulty in bits (default: 16)",
    )
    test_parser.add_argument(
        "--max-iterations",
        type=int,
        default=1000000,
        help="Maximum iterations (default: 1000000)",
    )
    test_parser.add_argument(
        "--input-data",
        help="Input data string (default: 'test input data')",
    )

    # benchmark command
    bench_parser = subparsers.add_parser("benchmark", help="Benchmark backend")
    bench_parser.add_argument(
        "--backend",
        default="cpu",
        choices=["cpu", "gpu", "asic", "quantum"],
        help="Backend to benchmark (default: cpu)",
    )
    bench_parser.add_argument(
        "--algorithm",
        default="SHA256",
        help="Hash algorithm (default: SHA256)",
    )
    bench_parser.add_argument(
        "--target",
        type=int,
        help="Specific target to test (default: test multiple)",
    )
    bench_parser.add_argument(
        "--max-iterations",
        type=int,
        default=1000000,
        help="Maximum iterations per test (default: 1000000)",
    )

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 1

    if args.command == "start":
        return cmd_start(args)
    elif args.command == "test-job":
        return cmd_test_job(args)
    elif args.command == "benchmark":
        return cmd_benchmark(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
