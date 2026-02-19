"""
Telemetry curator.

Reviews buffer, filters for quality, and uploads to DA.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any

from .collector import TelemetryCollector, TelemetrySample
from .config import load_telemetry_config

logger = logging.getLogger(__name__)


@dataclass
class CurationResult:
    """Result from curation process."""
    total_samples: int
    approved_samples: int
    rejected_samples: int
    uploaded_commitments: List[str]  # DA commitment hashes
    curation_stats: Dict[str, Any]
    
    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "total_samples": self.total_samples,
            "approved_samples": self.approved_samples,
            "rejected_samples": self.rejected_samples,
            "uploaded_commitments": self.uploaded_commitments,
            "curation_stats": self.curation_stats,
        }
    
    def to_json(self) -> str:
        """Convert to JSON."""
        return json.dumps(self.to_dict(), indent=2)


class TelemetryCurator:
    """
    Curator for telemetry data.
    
    Reviews buffer, filters for quality, and uploads approved data to DA.
    """
    
    def __init__(
        self,
        collector: Optional[TelemetryCollector] = None,
        mock_mode: bool = False,
    ):
        """
        Initialize curator.
        
        Args:
            collector: TelemetryCollector instance
            mock_mode: If True, don't actually upload to DA
        """
        self.collector = collector or TelemetryCollector()
        self.config = load_telemetry_config()
        self.mock_mode = mock_mode
        
        logger.info(f"Telemetry curator initialized: mock_mode={mock_mode}")
    
    def curate(
        self,
        auto: bool = False,
        quality_threshold: float = 0.5,
        max_samples: Optional[int] = None,
    ) -> CurationResult:
        """
        Curate telemetry buffer.
        
        Args:
            auto: If True, automatically approve/reject based on quality
            quality_threshold: Minimum quality score for auto-approval
            max_samples: Max samples to process (None = all)
            
        Returns:
            CurationResult
        """
        logger.info(f"Starting curation: auto={auto}, threshold={quality_threshold}")
        
        # Load samples from buffer
        samples = self.collector.inspect(limit=max_samples or 10000)
        
        if not samples:
            logger.info("No samples to curate")
            return CurationResult(
                total_samples=0,
                approved_samples=0,
                rejected_samples=0,
                uploaded_commitments=[],
                curation_stats={},
            )
        
        # Filter samples
        if auto:
            approved, rejected = self._auto_filter(samples, quality_threshold)
        else:
            approved, rejected = self._manual_review(samples)
        
        # Upload approved samples
        commitments = []
        if approved:
            commitments = self._upload_samples(approved)
        
        # Delete processed samples
        for sample in approved + rejected:
            self.collector.delete(sample.sample_id)
        
        # Compute stats
        stats = self._compute_stats(samples, approved, rejected)
        
        result = CurationResult(
            total_samples=len(samples),
            approved_samples=len(approved),
            rejected_samples=len(rejected),
            uploaded_commitments=commitments,
            curation_stats=stats,
        )
        
        logger.info(f"Curation complete: {result.to_json()}")
        return result
    
    def _auto_filter(
        self,
        samples: List[TelemetrySample],
        quality_threshold: float,
    ) -> tuple[List[TelemetrySample], List[TelemetrySample]]:
        """
        Automatically filter samples based on quality.
        
        Args:
            samples: Samples to filter
            quality_threshold: Minimum quality score
            
        Returns:
            (approved_samples, rejected_samples)
        """
        approved = []
        rejected = []
        
        for sample in samples:
            quality_score = self._compute_quality_score(sample)
            
            if quality_score >= quality_threshold:
                approved.append(sample)
                logger.debug(f"Sample {sample.sample_id} approved: quality={quality_score:.2f}")
            else:
                rejected.append(sample)
                logger.debug(f"Sample {sample.sample_id} rejected: quality={quality_score:.2f}")
        
        logger.info(f"Auto-filter: {len(approved)} approved, {len(rejected)} rejected")
        return approved, rejected
    
    def _manual_review(
        self,
        samples: List[TelemetrySample],
    ) -> tuple[List[TelemetrySample], List[TelemetrySample]]:
        """
        Manually review samples (interactive).
        
        Args:
            samples: Samples to review
            
        Returns:
            (approved_samples, rejected_samples)
        """
        approved = []
        rejected = []
        
        print("\n" + "="*60)
        print("MANUAL TELEMETRY REVIEW")
        print("="*60)
        print(f"Total samples: {len(samples)}")
        print("For each sample, choose: [a]pprove, [r]eject, [s]kip, [q]uit")
        print("="*60 + "\n")
        
        for i, sample in enumerate(samples, 1):
            print(f"\nSample {i}/{len(samples)} (ID: {sample.sample_id})")
            print(f"Timestamp: {sample.timestamp}")
            print(f"Model: {sample.model_version}")
            print(f"Redacted: {sample.redacted} ({sample.redaction_count} redactions)")
            print(f"Quality score: {self._compute_quality_score(sample):.2f}")
            print("\n--- PROMPT ---")
            print(sample.prompt[:200] + ("..." if len(sample.prompt) > 200 else ""))
            print("\n--- RESPONSE ---")
            print(sample.response[:200] + ("..." if len(sample.response) > 200 else ""))
            print()
            
            while True:
                choice = input("Decision [a/r/s/q]: ").strip().lower()
                if choice == 'a':
                    approved.append(sample)
                    print("✓ Approved")
                    break
                elif choice == 'r':
                    rejected.append(sample)
                    print("✗ Rejected")
                    break
                elif choice == 's':
                    print("⊘ Skipped")
                    break
                elif choice == 'q':
                    print("\nReview aborted")
                    return approved, rejected
                else:
                    print("Invalid choice, please enter a, r, s, or q")
        
        logger.info(f"Manual review: {len(approved)} approved, {len(rejected)} rejected")
        return approved, rejected
    
    def _compute_quality_score(self, sample: TelemetrySample) -> float:
        """
        Compute quality score for a sample.
        
        Higher is better. Range: 0.0 to 1.0
        
        Args:
            sample: Sample to score
            
        Returns:
            Quality score
        """
        score = 0.5  # Base score
        
        # Bonus for explicit feedback
        if sample.feedback_score is not None:
            score = sample.feedback_score
        
        # Penalty for user edits (implies low quality)
        if sample.edited_response is not None:
            score -= 0.3
        
        # Penalty for flagged samples
        if sample.flagged:
            score -= 0.5
        
        # Penalty for too many redactions (might have lost too much context)
        if sample.redaction_count > 5:
            score -= 0.2
        
        # Bonus for reasonable length (not too short, not too long)
        prompt_len = len(sample.prompt)
        response_len = len(sample.response)
        if 50 <= prompt_len <= 2000 and 50 <= response_len <= 5000:
            score += 0.1
        
        # Clamp to [0, 1]
        return max(0.0, min(1.0, score))
    
    def _upload_samples(self, samples: List[TelemetrySample]) -> List[str]:
        """
        Upload samples to DA.
        
        Args:
            samples: Samples to upload
            
        Returns:
            List of DA commitment hashes
        """
        logger.info(f"Uploading {len(samples)} samples to DA")
        
        if self.mock_mode:
            # Mock upload
            import hashlib
            commitments = []
            for sample in samples:
                content = sample.to_json().encode('utf-8')
                commitment = f"da://mock/{hashlib.sha256(content).hexdigest()[:16]}"
                commitments.append(commitment)
                logger.debug(f"MOCK: Uploaded {sample.sample_id} -> {commitment}")
            
            logger.info(f"MOCK: Uploaded {len(commitments)} samples")
            return commitments
        
        # Real DA upload (requires DA client integration)
        # 
        # Status: Integration pending (Phase 2)
        # - Mock mode is functional for testing (mock_mode=True)
        # - Real DA upload requires DA client to be wired up
        # 
        # When implemented:
        # 1. Batch samples into JSONL file
        # 2. Upload to DA layer via DA client
        # 3. Return DA commitment hashes
        # 
        # Example implementation:
        # from da.client import DAClient
        # import tempfile
        # 
        # # Create dataset file
        # with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
        #     for sample in samples:
        #         f.write(sample.to_json() + '\n')
        #     dataset_file = f.name
        # 
        # # Upload to DA
        # da_client = DAClient()
        # commitment = da_client.upload(dataset_file)
        # os.unlink(dataset_file)
        # 
        # return [commitment]
        
        raise NotImplementedError(
            "Real DA upload not yet implemented (Phase 2). "
            "Use mock_mode=True for testing. "
            "Integration requires DA client to be wired up."
        )
    
    def _compute_stats(
        self,
        samples: List[TelemetrySample],
        approved: List[TelemetrySample],
        rejected: List[TelemetrySample],
    ) -> Dict[str, Any]:
        """Compute curation statistics."""
        return {
            "total_samples": len(samples),
            "approved_samples": len(approved),
            "rejected_samples": len(rejected),
            "approval_rate": len(approved) / len(samples) if samples else 0.0,
            "avg_quality_score": sum(self._compute_quality_score(s) for s in samples) / len(samples) if samples else 0.0,
            "total_redactions": sum(s.redaction_count for s in samples),
            "samples_with_feedback": sum(1 for s in samples if s.feedback_score is not None),
            "samples_with_edits": sum(1 for s in samples if s.edited_response is not None),
            "flagged_samples": sum(1 for s in samples if s.flagged),
            "timestamp": datetime.utcnow().isoformat(),
        }


def main():
    """CLI entry point for curator."""
    import argparse
    import sys
    
    parser = argparse.ArgumentParser(description="ENA Telemetry Curator")
    parser.add_argument("--auto", action="store_true", help="Auto-approve/reject based on quality")
    parser.add_argument("--threshold", type=float, default=0.5, help="Quality threshold for auto mode")
    parser.add_argument("--max-samples", type=int, help="Max samples to process")
    parser.add_argument("--mock", action="store_true", help="Run in MOCK mode (don't upload to DA)")
    
    args = parser.parse_args()
    
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    
    # Create curator
    curator = TelemetryCurator(mock_mode=args.mock)
    
    # Curate
    result = curator.curate(
        auto=args.auto,
        quality_threshold=args.threshold,
        max_samples=args.max_samples,
    )
    
    # Print result
    print("\n" + "="*60)
    print("CURATION RESULT")
    print("="*60)
    print(result.to_json())
    print("="*60)
    
    sys.exit(0)


if __name__ == "__main__":
    main()
