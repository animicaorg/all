"""
Common Training Runner
======================

Universal runner for quantum/GPU/CPU training workloads.
Handles plan loading, execution, proof generation, and submission.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

# Add aicf to path for ProofEnvelope
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from aicf.protocol.proof_envelope import ProofEnvelope, create_stub_quantum_envelope, create_training_envelope


class TrainingRunner:
    """
    Universal runner for training workloads.
    
    Usage:
        runner = TrainingRunner(plan_path="plans/cpu_lora_tiny.json")
        result = runner.run()
        runner.submit(result)
    """
    
    def __init__(
        self,
        plan_path: str,
        workdir: Optional[str] = None,
        rpc_url: Optional[str] = None,
        worker_id: Optional[str] = None,
    ):
        """
        Initialize the runner.
        
        Args:
            plan_path: Path to the training plan JSON
            workdir: Working directory for outputs (defaults to ~/.animica/workdir)
            rpc_url: RPC endpoint for submissions
            worker_id: Worker identifier (defaults to hostname)
        """
        self.plan_path = Path(plan_path)
        self.workdir = Path(workdir) if workdir else self._get_default_workdir()
        self.rpc_url = rpc_url or os.environ.get("ANIMICA_RPC_URL", "http://127.0.0.1:8545/rpc")
        self.worker_id = worker_id or os.environ.get("WORKER_ID", os.uname().nodename)
        
        # Ensure workdir exists and is writable
        self.workdir.mkdir(parents=True, exist_ok=True)
        if not os.access(self.workdir, os.W_OK):
            raise PermissionError(f"Workdir {self.workdir} is not writable")
        
        # Load plan
        self.plan = self._load_plan()
    
    def _get_default_workdir(self) -> Path:
        """Get default working directory."""
        data_dir = os.environ.get("ANIMICA_DATA_DIR", str(Path.home() / ".animica"))
        return Path(data_dir) / "workdir"
    
    def _load_plan(self) -> Dict[str, Any]:
        """Load and validate the training plan."""
        if not self.plan_path.exists():
            raise FileNotFoundError(f"Plan not found: {self.plan_path}")
        
        with open(self.plan_path, 'r') as f:
            plan = json.load(f)
        
        # Validate required fields
        required = ["type", "job_id", "unit_definition", "reward_rate_hint"]
        for field in required:
            if field not in plan:
                raise ValueError(f"Plan missing required field: {field}")
        
        return plan
    
    def run(self) -> ProofEnvelope:
        """
        Execute the training workload and generate proof envelope.
        
        Returns:
            ProofEnvelope ready for submission
        """
        plan_type = self.plan["type"]
        
        if plan_type == "quantum_stub":
            return self._run_quantum_stub()
        elif plan_type == "cpu_lora":
            return self._run_cpu_lora()
        elif plan_type == "cpu_eval":
            return self._run_cpu_eval()
        elif plan_type == "gpu_finetune":
            return self._run_gpu_finetune()
        elif plan_type == "gpu_eval":
            return self._run_gpu_eval()
        elif plan_type == "data_prep":
            return self._run_data_prep()
        else:
            raise ValueError(f"Unknown plan type: {plan_type}")
    
    def _run_quantum_stub(self) -> ProofEnvelope:
        """Run quantum stub workload (VDF-like sequential work)."""
        steps = self.plan.get("steps", 1000)
        
        print(f"Running quantum stub: {steps} steps")
        start_time = time.time()
        
        # Simulate sequential work (hash chain)
        state = hashlib.sha3_256(self.plan["job_id"].encode()).digest()
        for i in range(steps):
            state = hashlib.sha3_256(state).digest()
            if i % 100 == 0:
                print(f"  Step {i}/{steps}")
        
        runtime = time.time() - start_time
        print(f"Completed in {runtime:.2f}s")
        
        # Create manifest
        manifest = self._create_run_manifest(
            plan_type="quantum_stub",
            metrics={"steps": steps, "runtime_sec": runtime},
        )
        
        # Sign (placeholder - in real implementation, use wallet)
        signature = self._sign_manifest(manifest)
        
        return create_stub_quantum_envelope(
            job_id=self.plan["job_id"],
            worker_id=self.worker_id,
            steps=steps,
            runtime_sec=runtime,
            signature=signature,
        )
    
    def _run_cpu_lora(self) -> ProofEnvelope:
        """Run CPU LoRA training (placeholder)."""
        print(f"CPU LoRA training not yet implemented")
        print("This would:")
        print("  • Load small model and dataset")
        print("  • Train LoRA adapters with CPU-friendly batch sizes")
        print("  • Use gradient accumulation for memory efficiency")
        print("  • Generate deterministic output hash")
        
        # Placeholder metrics
        metrics = {
            "epochs": self.plan.get("epochs", 1),
            "steps": 100,
            "final_loss": 0.5,
            "tokens_per_sec": 10.0,
        }
        
        manifest = self._create_run_manifest("cpu_lora", metrics)
        signature = self._sign_manifest(manifest)
        
        inputs_hash = hashlib.sha3_256(f"{self.plan['job_id']}_inputs".encode()).hexdigest()
        outputs_hash = hashlib.sha3_256(f"{self.plan['job_id']}_outputs".encode()).hexdigest()
        attestation = hashlib.sha3_256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
        
        return create_training_envelope(
            job_id=self.plan["job_id"],
            worker_id=self.worker_id,
            kind="cpu_train",
            inputs_hash=inputs_hash,
            outputs_hash=outputs_hash,
            metrics=metrics,
            attestation_hash=attestation,
            signature=signature,
        )
    
    def _run_cpu_eval(self) -> ProofEnvelope:
        """Run CPU evaluation (placeholder)."""
        print(f"CPU eval not yet implemented")
        return self._run_cpu_lora()  # Same structure for now
    
    def _run_gpu_finetune(self) -> ProofEnvelope:
        """Run GPU fine-tuning (placeholder)."""
        try:
            import torch
            has_gpu = torch.cuda.is_available()
        except ImportError:
            has_gpu = False
        
        if not has_gpu:
            print("[WARNING] No GPU available, using CPU fallback")
        
        print(f"GPU fine-tuning not yet implemented")
        return self._run_cpu_lora()  # Same structure for now
    
    def _run_gpu_eval(self) -> ProofEnvelope:
        """Run GPU evaluation (placeholder)."""
        return self._run_gpu_finetune()
    
    def _run_data_prep(self) -> ProofEnvelope:
        """Run data preparation (tokenization, etc.)."""
        print(f"Data prep not yet implemented")
        return self._run_cpu_lora()  # Same structure for now
    
    def _create_run_manifest(self, plan_type: str, metrics: Dict[str, Any]) -> Dict[str, Any]:
        """Create deterministic run manifest."""
        return {
            "plan_type": plan_type,
            "job_id": self.plan["job_id"],
            "worker_id": self.worker_id,
            "timestamp": int(time.time()),
            "metrics": metrics,
            "python_version": sys.version.split()[0],
            "workdir": str(self.workdir),
        }
    
    def _sign_manifest(self, manifest: Dict[str, Any]) -> str:
        """Sign manifest (placeholder - real implementation would use wallet)."""
        manifest_str = json.dumps(manifest, sort_keys=True).encode()
        signature_hash = hashlib.sha3_256(manifest_str).digest()
        # Pad to typical signature length (64 bytes for ed25519, etc.)
        return (signature_hash + b'\x00' * 32).hex()
    
    def submit(self, envelope: ProofEnvelope) -> Dict[str, Any]:
        """
        Submit proof envelope to RPC.
        
        Args:
            envelope: The proof envelope to submit
        
        Returns:
            RPC response
        """
        import requests
        
        # Validate envelope
        is_valid, error = envelope.validate_schema()
        if not is_valid:
            raise ValueError(f"Invalid envelope: {error}")
        
        # Convert to CBOR hex
        cbor_hex = envelope.to_cbor_hex()
        
        print(f"\nSubmitting work to {self.rpc_url}")
        print(f"  Job ID: {envelope.job_id}")
        print(f"  Kind: {envelope.kind}")
        print(f"  Metrics: {envelope.metrics}")
        
        # Submit via RPC
        try:
            response = requests.post(
                self.rpc_url,
                json={
                    "jsonrpc": "2.0",
                    "method": "aicf.submitWork",
                    "params": [cbor_hex],
                    "id": 1,
                },
                timeout=30,
            )
            response.raise_for_status()
            result = response.json()
            
            if "error" in result:
                raise RuntimeError(f"RPC error: {result['error']}")
            
            print(f"✓ Work submitted successfully")
            print(f"  Receipt ID: {result.get('result', {}).get('receipt_id', 'N/A')}")
            
            return result.get("result", {})
        
        except requests.RequestException as e:
            print(f"[ERROR] Failed to submit: {e}")
            print(f"[INFO] Proof envelope saved to {self.workdir / 'last_envelope.cbor.hex'}")
            
            # Save envelope for manual submission
            envelope_path = self.workdir / "last_envelope.cbor.hex"
            envelope_path.write_text(cbor_hex)
            
            raise
    
    def run_and_submit(self) -> Dict[str, Any]:
        """Convenience method to run and submit in one call."""
        envelope = self.run()
        return self.submit(envelope)


def main():
    """CLI entry point for direct script execution."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Run training workload and submit proof")
    parser.add_argument("--plan", required=True, help="Path to plan JSON")
    parser.add_argument("--workdir", help="Working directory")
    parser.add_argument("--rpc-url", help="RPC endpoint")
    parser.add_argument("--worker-id", help="Worker identifier")
    parser.add_argument("--no-submit", action="store_true", help="Run but don't submit")
    
    args = parser.parse_args()
    
    runner = TrainingRunner(
        plan_path=args.plan,
        workdir=args.workdir,
        rpc_url=args.rpc_url,
        worker_id=args.worker_id,
    )
    
    if args.no_submit:
        envelope = runner.run()
        print(f"\nEnvelope generated but not submitted:")
        print(f"  CBOR hex: {envelope.to_cbor_hex()}")
    else:
        runner.run_and_submit()


if __name__ == "__main__":
    main()
