#!/usr/bin/env python3
"""
Test script for ENA workers and telemetry system.

Tests Phase 6 (Workers) and Phase 7 (Telemetry).
"""

import json
import logging
import sys
import tempfile
from pathlib import Path

# Add project to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from ena.workers import TrainingWorker, EvaluationWorker, DistillationWorker
from ena.telemetry import TelemetryCollector, TelemetryCurator, TelemetryConfig
from ena.telemetry.config import save_telemetry_config

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def test_training_worker():
    """Test training worker in MOCK mode."""
    print("\n" + "="*60)
    print("TEST: Training Worker (MOCK)")
    print("="*60)
    
    # Create job spec
    job_spec = {
        "job_id": "test_train_001",
        "job_type": "ena.train.sft",
        "base_model": "da://mock_base_model",
        "dataset_hashes": ["da://mock_dataset_1", "da://mock_dataset_2"],
        "hyperparams": {
            "learning_rate": 2e-5,
            "batch_size": 4,
            "epochs": 3,
        },
    }
    
    # Create worker
    with tempfile.TemporaryDirectory() as tmpdir:
        worker = TrainingWorker(
            job_spec=job_spec,
            output_dir=Path(tmpdir),
            mock_mode=True,
        )
        
        # Execute
        result = worker.execute()
        
        # Verify
        assert result.status == "success", f"Expected success, got {result.status}"
        assert "model" in result.artifacts, "Missing model artifact"
        assert "metrics" in result.artifacts, "Missing metrics artifact"
        assert result.metrics["train_loss"] > 0, "Invalid train_loss"
        
        print(f"\n✓ Training worker test passed")
        print(f"  Status: {result.status}")
        print(f"  Artifacts: {list(result.artifacts.keys())}")
        print(f"  Train loss: {result.metrics['train_loss']:.3f}")
        print(f"  Execution time: {result.execution_time_seconds:.1f}s")
    
    return True


def test_evaluation_worker():
    """Test evaluation worker in MOCK mode."""
    print("\n" + "="*60)
    print("TEST: Evaluation Worker (MOCK)")
    print("="*60)
    
    # Create job spec
    job_spec = {
        "job_id": "test_eval_001",
        "job_type": "ena.eval",
        "model_hash": "da://mock_model",
        "eval_suite_hash": "da://mock_eval_suite",
        "eval_tasks": ["accuracy", "perplexity", "toxicity", "regression"],
    }
    
    # Create worker
    with tempfile.TemporaryDirectory() as tmpdir:
        worker = EvaluationWorker(
            job_spec=job_spec,
            output_dir=Path(tmpdir),
            mock_mode=True,
        )
        
        # Execute
        result = worker.execute()
        
        # Verify
        assert result.status == "success", f"Expected success, got {result.status}"
        assert "metrics" in result.artifacts, "Missing metrics artifact"
        assert "accuracy" in result.metrics["tasks"], "Missing accuracy task"
        assert result.metrics["aggregate"]["average_accuracy"] > 0, "Invalid accuracy"
        
        print(f"\n✓ Evaluation worker test passed")
        print(f"  Status: {result.status}")
        print(f"  Tasks: {list(result.metrics['tasks'].keys())}")
        print(f"  Average accuracy: {result.metrics['aggregate']['average_accuracy']:.3f}")
        print(f"  Total samples: {result.metrics['aggregate']['total_samples']}")
    
    return True


def test_distillation_worker():
    """Test distillation worker in MOCK mode."""
    print("\n" + "="*60)
    print("TEST: Distillation Worker (MOCK)")
    print("="*60)
    
    # Create job spec
    job_spec = {
        "job_id": "test_distill_001",
        "job_type": "ena.distill.cpu",
        "teacher_model_hash": "da://mock_teacher",
        "student_config": {
            "hidden_size": 384,
            "num_layers": 6,
        },
        "distill_dataset_hash": "da://mock_distill_dataset",
        "quantization": {
            "format": "gguf",
            "bits": 4,
        },
    }
    
    # Create worker
    with tempfile.TemporaryDirectory() as tmpdir:
        worker = DistillationWorker(
            job_spec=job_spec,
            output_dir=Path(tmpdir),
            mock_mode=True,
        )
        
        # Execute
        result = worker.execute()
        
        # Verify
        assert result.status == "success", f"Expected success, got {result.status}"
        assert "student_model" in result.artifacts, "Missing student model"
        assert "quantized_gguf" in result.artifacts, "Missing GGUF"
        assert result.metrics["quantization"]["compression_ratio"] > 1, "Invalid compression"
        
        print(f"\n✓ Distillation worker test passed")
        print(f"  Status: {result.status}")
        print(f"  Student perplexity: {result.metrics['evaluation']['student_perplexity']:.1f}")
        print(f"  Compression ratio: {result.metrics['quantization']['compression_ratio']:.1f}x")
        print(f"  Speedup: {result.metrics['performance']['speedup_factor']:.2f}x")
    
    return True


def test_telemetry_collector():
    """Test telemetry collector."""
    print("\n" + "="*60)
    print("TEST: Telemetry Collector")
    print("="*60)
    
    # Create test config (opt-in enabled)
    with tempfile.TemporaryDirectory() as tmpdir:
        buffer_dir = Path(tmpdir) / "buffer"
        
        # Create collector with opt-in enabled
        config = TelemetryConfig(opt_in=True, user_id_hash="test_user_hash")
        collector = TelemetryCollector(buffer_dir=buffer_dir)
        collector.config = config  # Override
        
        # Collect some samples
        sample_id_1 = collector.collect(
            prompt="What is the capital of France?",
            response="The capital of France is Paris.",
            model_version="ena-v1.0",
            feedback_score=0.9,
        )
        
        sample_id_2 = collector.collect(
            prompt="My email is test@example.com and my phone is 12345678901",
            response="I can help with that.",
            model_version="ena-v1.0",
        )
        
        # Verify collection
        assert sample_id_1 is not None, "Sample 1 should be collected"
        assert sample_id_2 is not None, "Sample 2 should be collected"
        
        # Inspect buffer
        samples = collector.inspect(limit=10)
        assert len(samples) == 2, f"Expected 2 samples, got {len(samples)}"
        
        # Check redaction
        sample_2 = next(s for s in samples if s.sample_id == sample_id_2)
        assert "[EMAIL_REDACTED]" in sample_2.prompt, "Email not redacted"
        assert "[NUMBER_REDACTED]" in sample_2.prompt, "Long number not redacted"
        assert sample_2.redaction_count == 2, f"Expected 2 redactions, got {sample_2.redaction_count}"
        
        # Get stats
        stats = collector.get_buffer_stats()
        assert stats["total_samples"] == 2, f"Expected 2 samples in stats, got {stats['total_samples']}"
        
        # Delete one sample
        count = collector.delete(sample_id_1)
        assert count == 1, f"Expected 1 deletion, got {count}"
        
        stats = collector.get_buffer_stats()
        assert stats["total_samples"] == 1, f"Expected 1 sample after deletion, got {stats['total_samples']}"
        
        print(f"\n✓ Telemetry collector test passed")
        print(f"  Collected: 2 samples")
        print(f"  Redactions: {sample_2.redaction_count}")
        print(f"  Remaining after delete: {stats['total_samples']}")


def test_telemetry_curator():
    """Test telemetry curator."""
    print("\n" + "="*60)
    print("TEST: Telemetry Curator")
    print("="*60)
    
    # Create test config (opt-in enabled)
    with tempfile.TemporaryDirectory() as tmpdir:
        buffer_dir = Path(tmpdir) / "buffer"
        
        # Create collector with opt-in enabled
        config = TelemetryConfig(opt_in=True, user_id_hash="test_user_hash")
        collector = TelemetryCollector(buffer_dir=buffer_dir)
        collector.config = config  # Override
        
        # Collect samples with different quality
        collector.collect(
            prompt="Good quality prompt",
            response="Good quality response",
            model_version="ena-v1.0",
            feedback_score=0.9,  # High quality
        )
        
        collector.collect(
            prompt="Low quality",
            response="Low quality",
            model_version="ena-v1.0",
            feedback_score=0.2,  # Low quality
        )
        
        collector.collect(
            prompt="Medium quality prompt",
            response="Medium quality response",
            model_version="ena-v1.0",
            feedback_score=0.6,
        )
        
        # Create curator in mock mode
        curator = TelemetryCurator(collector=collector, mock_mode=True)
        
        # Auto-curate with threshold 0.5
        result = curator.curate(auto=True, quality_threshold=0.5)
        
        # Verify
        assert result.total_samples == 3, f"Expected 3 samples, got {result.total_samples}"
        assert result.approved_samples == 2, f"Expected 2 approved, got {result.approved_samples}"
        assert result.rejected_samples == 1, f"Expected 1 rejected, got {result.rejected_samples}"
        assert len(result.uploaded_commitments) == 2, f"Expected 2 uploads, got {len(result.uploaded_commitments)}"
        
        # Verify buffer is cleared
        stats = collector.get_buffer_stats()
        assert stats["total_samples"] == 0, f"Buffer should be empty, got {stats['total_samples']}"
        
        print(f"\n✓ Telemetry curator test passed")
        print(f"  Total samples: {result.total_samples}")
        print(f"  Approved: {result.approved_samples}")
        print(f"  Rejected: {result.rejected_samples}")
        print(f"  Approval rate: {result.curation_stats['approval_rate']:.1%}")


def main():
    """Run all tests."""
    print("\n" + "="*60)
    print("ENA WORKERS AND TELEMETRY TEST SUITE")
    print("="*60)
    
    tests = [
        ("Training Worker", test_training_worker),
        ("Evaluation Worker", test_evaluation_worker),
        ("Distillation Worker", test_distillation_worker),
        ("Telemetry Collector", test_telemetry_collector),
        ("Telemetry Curator", test_telemetry_curator),
    ]
    
    passed = 0
    failed = 0
    
    for name, test_func in tests:
        try:
            test_func()
            passed += 1
        except Exception as e:
            logger.error(f"Test failed: {name}")
            logger.error(f"Error: {e}", exc_info=True)
            failed += 1
    
    # Summary
    print("\n" + "="*60)
    print("TEST SUMMARY")
    print("="*60)
    print(f"Passed: {passed}/{len(tests)}")
    print(f"Failed: {failed}/{len(tests)}")
    print("="*60)
    
    if failed > 0:
        print("\n❌ Some tests failed")
        sys.exit(1)
    else:
        print("\n✅ All tests passed!")
        sys.exit(0)


if __name__ == "__main__":
    main()
