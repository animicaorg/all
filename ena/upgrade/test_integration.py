#!/usr/bin/env python3
"""
Integration test for ENA upgrade system.

Tests the full workflow from plan creation to publication.
"""

import sys
import os
import tempfile
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from ena.upgrade import (
    UpgradeStateMachine,
    UpgradeCoordinator,
    ResultVerifier,
    SafetyGates,
    UpgradeState,
)
from ena.registry.storage import RegistryStorage
from ena.registry.schema import EvalMetrics


def test_upgrade_workflow():
    """Test complete upgrade workflow."""
    print("Testing ENA Upgrade Workflow\n")
    print("=" * 60)
    
    # Create temporary directories
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        
        # Setup components
        state_file = tmpdir_path / "state.json"
        registry_dir = tmpdir_path / "registry"
        work_dir = tmpdir_path / "work"
        
        print("\n1. Initializing components...")
        state_machine = UpgradeStateMachine(state_file)
        registry = RegistryStorage(registry_dir)
        verifier = ResultVerifier()
        safety_gates = SafetyGates(
            min_accuracy=0.9,
            max_perplexity=3.0,
            max_toxicity_score=0.1,
            min_regression_pass_rate=0.95,
        )
        
        coordinator = UpgradeCoordinator(
            state_machine=state_machine,
            registry=registry,
            verifier=verifier,
            safety_gates=safety_gates,
            work_dir=work_dir,
        )
        
        print("   ✓ Components initialized")
        
        # Create upgrade
        print("\n2. Creating upgrade...")
        state_machine.create_upgrade(
            upgrade_id="test_upgrade_001",
            model_id="ena",
            target_version="1.0.0",
            previous_version=None,
        )
        print("   ✓ Upgrade created")
        
        # Create plan
        print("\n3. Creating training plan...")
        plan = coordinator.create_plan(
            model_id="ena",
            target_version="1.0.0",
            creator="test_creator",
            dataset_hashes=["hash1", "hash2"],
            base_model="qwen2.5-coder-1.5b",
        )
        print(f"   ✓ Plan created: {plan.plan_id}")
        print(f"   ✓ Jobs: {len(plan.jobs)}")
        
        # Check state
        status = state_machine.get_status()
        assert status is not None
        assert status.current_state == UpgradeState.PLANNING
        print(f"   ✓ State: {status.current_state.value}")
        
        # Allocate budget
        print("\n4. Allocating budget...")
        coordinator.allocate_budget(plan.max_total_cost_anm)
        status = state_machine.get_status()
        assert status.current_state == UpgradeState.ALLOCATING_BUDGET
        print(f"   ✓ Budget allocated: {status.budget_allocated / 1_000_000_000} ANM")
        
        # Submit jobs
        print("\n5. Submitting jobs...")
        job_ids = coordinator.submit_jobs(plan)
        print(f"   ✓ Submitted {len(job_ids)} jobs")
        
        # Monitor progress
        print("\n6. Monitoring progress...")
        statuses = coordinator.monitor_progress()
        print(f"   ✓ Monitoring {len(statuses)} jobs")
        
        # Verify results (with dummy data)
        print("\n7. Verifying results...")
        job_outputs = {}
        metrics = {}
        
        for job in plan.jobs:
            # Create dummy output directory
            output_dir = work_dir / "outputs" / job.job_id
            output_dir.mkdir(parents=True, exist_ok=True)
            job_outputs[job.job_id] = output_dir
            
            # Create dummy metrics for eval jobs
            if "eval" in job.job_type.value:
                metrics[job.job_id] = EvalMetrics(
                    accuracy=0.95,
                    perplexity=2.5,
                    toxicity_score=0.05,
                    regression_pass_rate=0.98,
                )
        
        result = coordinator.verify_results(plan, job_outputs, metrics)
        assert result.passed
        print(f"   ✓ Verification passed")
        
        # Publish model
        print("\n8. Publishing model...")
        manifest = coordinator._create_manifest_from_plan(plan, metrics)
        manifest_hash = coordinator.publish_model(manifest)
        print(f"   ✓ Published: {manifest_hash[:16]}")
        
        # Rollout canary
        print("\n9. Rolling out canary...")
        coordinator.rollout_canary()
        status = state_machine.get_status()
        assert status.current_state == UpgradeState.CANARY
        print(f"   ✓ Canary deployed")
        
        # Promote canary
        print("\n10. Promoting canary...")
        coordinator.promote_canary()
        status = state_machine.get_status()
        assert status.current_state == UpgradeState.COMPLETED
        print(f"   ✓ Canary promoted")
        
        # Verify registry
        print("\n11. Verifying registry...")
        loaded_manifest = registry.load_manifest("ena", "1.0.0")
        assert loaded_manifest is not None
        assert loaded_manifest.version == "1.0.0"
        print(f"   ✓ Manifest in registry: ena v1.0.0")
        
        # Test pinning
        print("\n12. Testing version pinning...")
        success = registry.pin_version("ena", "1.0.0")
        assert success
        pinned = registry.get_pinned_version("ena")
        assert pinned == "1.0.0"
        print(f"   ✓ Pinned version: {pinned}")
        
        print("\n" + "=" * 60)
        print("✓ All tests passed!")
        print("\nWorkflow Summary:")
        print(f"  Model: {status.model_id}")
        print(f"  Version: {status.target_version}")
        print(f"  State: {status.current_state.value}")
        print(f"  Jobs: {len(status.job_statuses)}")
        print(f"  Published: {status.published_manifest_hash[:16]}")


def test_safety_gates():
    """Test safety gate checks."""
    print("\n\nTesting Safety Gates\n")
    print("=" * 60)
    
    safety_gates = SafetyGates(
        min_accuracy=0.9,
        max_perplexity=3.0,
        max_toxicity_score=0.1,
        min_regression_pass_rate=0.95,
    )
    
    # Test passing metrics
    print("\n1. Testing passing metrics...")
    good_metrics = EvalMetrics(
        accuracy=0.95,
        perplexity=2.5,
        toxicity_score=0.05,
        regression_pass_rate=0.98,
    )
    
    passed, failures = safety_gates.passes_all_gates(good_metrics)
    assert passed
    print(f"   ✓ Passed all gates")
    
    # Test failing accuracy
    print("\n2. Testing failing accuracy...")
    bad_accuracy = EvalMetrics(
        accuracy=0.85,  # Below threshold
        perplexity=2.5,
        toxicity_score=0.05,
        regression_pass_rate=0.98,
    )
    
    passed, failures = safety_gates.passes_all_gates(bad_accuracy)
    assert not passed
    assert len(failures) > 0
    print(f"   ✓ Correctly rejected: {failures[0][:50]}...")
    
    # Test failing toxicity
    print("\n3. Testing failing toxicity...")
    bad_toxicity = EvalMetrics(
        accuracy=0.95,
        perplexity=2.5,
        toxicity_score=0.15,  # Above threshold
        regression_pass_rate=0.98,
    )
    
    passed, failures = safety_gates.passes_all_gates(bad_toxicity)
    assert not passed
    print(f"   ✓ Correctly rejected: {failures[0][:50]}...")
    
    print("\n" + "=" * 60)
    print("✓ Safety gate tests passed!")


if __name__ == "__main__":
    test_upgrade_workflow()
    test_safety_gates()
    
    print("\n\n" + "=" * 60)
    print("ALL TESTS PASSED!")
    print("=" * 60)
