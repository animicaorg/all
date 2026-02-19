"""
CPU Evaluation Script
=====================

Run model evaluation on CPU.
Optimized for CPU execution with small batch sizes.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure we can import from tools/ena_training
sys.path.insert(0, str(Path(__file__).parent))

from runner import TrainingRunner


def main():
    """Run CPU evaluation."""
    import argparse
    
    parser = argparse.ArgumentParser(description="CPU evaluation script")
    parser.add_argument("--plan", required=True, help="Path to plan JSON")
    parser.add_argument("--workdir", help="Working directory")
    parser.add_argument("--rpc-url", help="RPC endpoint")
    parser.add_argument("--worker-id", help="Worker identifier")
    
    args = parser.parse_args()
    
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
