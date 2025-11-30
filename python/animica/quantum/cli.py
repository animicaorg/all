"""Small demo CLI for the quantum experiment layer."""
from __future__ import annotations

import argparse
import json
import sys

from .experiment import QuantumExperiment, simulate_from_pow_input


def _demo(payload: dict, prefer_qiskit: bool) -> None:
    exp = QuantumExperiment(seed=payload.get("seed"), prefer_qiskit=prefer_qiskit)
    result = exp.run(payload)
    print("Quantum simulation result:")
    print(json.dumps(result.__dict__, indent=2))


def _pow_demo(block_hash: str, nonce: int, difficulty: int, prefer_qiskit: bool, seed: int | None) -> None:
    result = simulate_from_pow_input(block_hash, nonce, difficulty, prefer_qiskit=prefer_qiskit, seed=seed)
    print("Quantum simulation seeded from PoW input:")
    print(json.dumps(result.__dict__, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run quantum-inspired simulations on a local backend.")
    parser.add_argument("mode", choices=["demo", "pow"], help="demo: run with synthetic payload; pow: use block hash + nonce")
    parser.add_argument("block_hash", nargs="?", help="Block hash when using pow mode")
    parser.add_argument("nonce", nargs="?", type=int, help="Nonce when using pow mode")
    parser.add_argument("--difficulty", type=int, default=1, help="Fake difficulty value for PoW demo")
    parser.add_argument("--seed", type=int, help="Deterministic seed for jitter")
    parser.add_argument("--prefer-qiskit", action="store_true", help="Use Qiskit if installed (falls back to builtin simulator)")
    args = parser.parse_args(argv)

    if args.mode == "demo":
        payload = {
            "block_hash": args.block_hash or "0x-demo-block",
            "nonce": args.nonce or 0,
            "difficulty": args.difficulty,
            "seed": args.seed,
        }
        _demo(payload, args.prefer_qiskit)
    else:
        if not args.block_hash or args.nonce is None:
            parser.error("pow mode requires block_hash and nonce")
        _pow_demo(args.block_hash, args.nonce, args.difficulty, args.prefer_qiskit, args.seed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
