"""
Quantum Stub Script
===================

Produces deterministic "useful-work" proof envelope.
Uses VDF-like sequential work (hash chain) as a placeholder for real quantum proofs.

This stub is CPU-runnable but labeled "quantum" as a placeholder for future
real quantum verification. It produces verifiable, deterministic envelopes that
can be upgraded later to true quantum proofs.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure we can import from tools/ena_training
sys.path.insert(0, str(Path(__file__).parent))

from runner import TrainingRunner


def main():
    """Run quantum stub workload."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Quantum stub (VDF-like) script")
    parser.add_argument("--plan", required=True, help="Path to plan JSON")
    parser.add_argument("--workdir", help="Working directory")
    parser.add_argument("--rpc-url", help="RPC endpoint")
    parser.add_argument("--worker-id", help="Worker identifier")
    
    args = parser.parse_args()
    
    print("Quantum Stub Workload")
    print("=" * 50)
    print("This is a placeholder for real quantum proofs.")
    print("Characteristics:")
    print("  • Sequential work (hash chain)")
    print("  • Deterministic outputs")
    print("  • Verifiable commitment scheme")
    print("  • Will be upgraded to true quantum verification later")
    print()
    print("Proof type: stub_quantum_v1")
    print()
    
    runner = TrainingRunner(
        plan_path=args.plan,
        workdir=args.workdir,
        rpc_url=args.rpc_url,
        worker_id=args.worker_id,
    )
    
    runner.run_and_submit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
