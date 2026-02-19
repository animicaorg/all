"""
GPU Fine-tuning Script
======================

Fine-tune a language model on GPU using transformers + accelerate.
Falls back gracefully if no GPU is available.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure we can import from tools/ena_training
sys.path.insert(0, str(Path(__file__).parent))

from runner import TrainingRunner


def check_gpu():
    """Check if GPU is available."""
    try:
        import torch
        if torch.cuda.is_available():
            device = torch.cuda.get_device_name(0)
            memory = torch.cuda.get_device_properties(0).total_memory / 1e9
            print(f"GPU available: {device} ({memory:.1f} GB)")
            return True
        else:
            print("No GPU available - will use CPU (slower)")
            return False
    except ImportError:
        print("PyTorch not installed - cannot check GPU")
        return False


def main():
    """Run GPU fine-tuning."""
    import argparse
    
    parser = argparse.ArgumentParser(description="GPU fine-tuning script")
    parser.add_argument("--plan", required=True, help="Path to plan JSON")
    parser.add_argument("--workdir", help="Working directory")
    parser.add_argument("--rpc-url", help="RPC endpoint")
    parser.add_argument("--worker-id", help="Worker identifier")
    parser.add_argument("--skip-gpu-check", action="store_true", help="Skip GPU availability check")
    
    args = parser.parse_args()
    
    # Check GPU
    if not args.skip_gpu_check:
        has_gpu = check_gpu()
        if not has_gpu:
            print("\n[WARNING] Continuing without GPU (performance will be degraded)")
            response = input("Continue? [y/N] ")
            if response.lower() != 'y':
                print("Aborted")
                return 1
    
    # Run training
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
