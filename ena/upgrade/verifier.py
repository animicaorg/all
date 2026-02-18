"""
Result verification and safety gates for model upgrades.

Validates job outputs and checks quality thresholds.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

from ..registry.schema import EvalMetrics

logger = logging.getLogger(__name__)


@dataclass
class VerificationResult:
    """Result of verification check."""
    passed: bool
    reason: str
    details: Optional[Dict[str, Any]] = None


class ResultVerifier:
    """
    Verifies job outputs meet requirements.
    
    Checks:
    - Artifact hashes match reported values
    - Metrics JSON schema is valid
    - Eval suite used matches approved hash
    """
    
    def __init__(self, approved_eval_suite_hash: Optional[str] = None):
        """
        Initialize verifier.
        
        Args:
            approved_eval_suite_hash: Expected eval suite hash (optional)
        """
        self.approved_eval_suite_hash = approved_eval_suite_hash
    
    def verify_artifact_hash(
        self,
        artifact_path: Path,
        expected_hash: str,
    ) -> VerificationResult:
        """
        Verify an artifact's hash matches expected value.
        
        Args:
            artifact_path: Path to artifact file
            expected_hash: Expected SHA256 hash
        
        Returns:
            VerificationResult
        """
        if not artifact_path.exists():
            return VerificationResult(
                passed=False,
                reason=f"Artifact not found: {artifact_path}",
            )
        
        try:
            # Compute hash
            sha256 = hashlib.sha256()
            with open(artifact_path, "rb") as f:
                while chunk := f.read(8192):
                    sha256.update(chunk)
            
            actual_hash = sha256.hexdigest()
            
            if actual_hash != expected_hash:
                return VerificationResult(
                    passed=False,
                    reason="Hash mismatch",
                    details={
                        "expected": expected_hash,
                        "actual": actual_hash,
                    },
                )
            
            return VerificationResult(
                passed=True,
                reason="Hash verified",
                details={"hash": actual_hash},
            )
        
        except Exception as e:
            return VerificationResult(
                passed=False,
                reason=f"Failed to compute hash: {e}",
            )
    
    def verify_metrics_schema(self, metrics: Dict[str, Any]) -> VerificationResult:
        """
        Verify metrics JSON schema is valid.
        
        Args:
            metrics: Metrics dictionary
        
        Returns:
            VerificationResult
        """
        try:
            # Check required fields exist
            # For flexibility, we accept any numeric metrics
            # but validate they are reasonable
            
            for key, value in metrics.items():
                if not isinstance(value, (int, float)):
                    return VerificationResult(
                        passed=False,
                        reason=f"Metric '{key}' is not numeric: {type(value).__name__}",
                    )
                
                # Check for NaN or Inf
                if isinstance(value, float):
                    if not (-1e10 < value < 1e10):
                        return VerificationResult(
                            passed=False,
                            reason=f"Metric '{key}' out of reasonable range: {value}",
                        )
            
            return VerificationResult(
                passed=True,
                reason="Metrics schema valid",
                details={"metric_count": len(metrics)},
            )
        
        except Exception as e:
            return VerificationResult(
                passed=False,
                reason=f"Failed to validate metrics: {e}",
            )
    
    def verify_eval_suite(
        self,
        eval_suite_hash: str,
    ) -> VerificationResult:
        """
        Verify eval suite hash matches approved value.
        
        Args:
            eval_suite_hash: Hash of eval suite used
        
        Returns:
            VerificationResult
        """
        if not self.approved_eval_suite_hash:
            # No approved hash set, skip check
            return VerificationResult(
                passed=True,
                reason="No approved eval suite hash configured (skipped)",
            )
        
        if eval_suite_hash != self.approved_eval_suite_hash:
            return VerificationResult(
                passed=False,
                reason="Eval suite hash mismatch",
                details={
                    "expected": self.approved_eval_suite_hash,
                    "actual": eval_suite_hash,
                },
            )
        
        return VerificationResult(
            passed=True,
            reason="Eval suite verified",
            details={"hash": eval_suite_hash},
        )
    
    def verify_job_output(
        self,
        output_dir: Path,
        expected_artifacts: Dict[str, str],
        metrics: Optional[Dict[str, Any]] = None,
        eval_suite_hash: Optional[str] = None,
    ) -> VerificationResult:
        """
        Verify all outputs from a job.
        
        Args:
            output_dir: Directory containing job outputs
            expected_artifacts: Dict mapping artifact name to expected hash
            metrics: Optional metrics to verify
            eval_suite_hash: Optional eval suite hash to verify
        
        Returns:
            VerificationResult
        """
        # Verify all artifacts
        for artifact_name, expected_hash in expected_artifacts.items():
            artifact_path = output_dir / artifact_name
            result = self.verify_artifact_hash(artifact_path, expected_hash)
            
            if not result.passed:
                return VerificationResult(
                    passed=False,
                    reason=f"Artifact '{artifact_name}' verification failed: {result.reason}",
                    details=result.details,
                )
        
        # Verify metrics if provided
        if metrics:
            result = self.verify_metrics_schema(metrics)
            if not result.passed:
                return result
        
        # Verify eval suite if provided
        if eval_suite_hash:
            result = self.verify_eval_suite(eval_suite_hash)
            if not result.passed:
                return result
        
        return VerificationResult(
            passed=True,
            reason="All verifications passed",
            details={
                "artifacts_verified": len(expected_artifacts),
                "metrics_verified": bool(metrics),
                "eval_suite_verified": bool(eval_suite_hash),
            },
        )


class SafetyGates:
    """
    Safety gates for model deployment.
    
    Checks if metrics meet minimum quality thresholds.
    """
    
    def __init__(
        self,
        min_accuracy: Optional[float] = None,
        max_perplexity: Optional[float] = None,
        max_toxicity_score: Optional[float] = None,
        min_regression_pass_rate: Optional[float] = None,
        custom_thresholds: Optional[Dict[str, Tuple[str, float]]] = None,
    ):
        """
        Initialize safety gates.
        
        Args:
            min_accuracy: Minimum accuracy threshold
            max_perplexity: Maximum perplexity threshold
            max_toxicity_score: Maximum toxicity score threshold
            min_regression_pass_rate: Minimum regression pass rate threshold
            custom_thresholds: Dict mapping metric name to (comparison, threshold)
                              where comparison is "min" or "max"
        """
        self.min_accuracy = min_accuracy
        self.max_perplexity = max_perplexity
        self.max_toxicity_score = max_toxicity_score
        self.min_regression_pass_rate = min_regression_pass_rate
        self.custom_thresholds = custom_thresholds or {}
    
    def check_accuracy(self, metrics: EvalMetrics) -> VerificationResult:
        """Check accuracy threshold."""
        if self.min_accuracy is None:
            return VerificationResult(passed=True, reason="No accuracy threshold set")
        
        if metrics.accuracy is None:
            return VerificationResult(
                passed=False,
                reason="Accuracy metric missing",
            )
        
        if metrics.accuracy < self.min_accuracy:
            return VerificationResult(
                passed=False,
                reason=f"Accuracy below threshold: {metrics.accuracy:.4f} < {self.min_accuracy:.4f}",
                details={
                    "actual": metrics.accuracy,
                    "threshold": self.min_accuracy,
                },
            )
        
        return VerificationResult(
            passed=True,
            reason=f"Accuracy meets threshold: {metrics.accuracy:.4f} >= {self.min_accuracy:.4f}",
            details={"accuracy": metrics.accuracy},
        )
    
    def check_perplexity(self, metrics: EvalMetrics) -> VerificationResult:
        """Check perplexity threshold."""
        if self.max_perplexity is None:
            return VerificationResult(passed=True, reason="No perplexity threshold set")
        
        if metrics.perplexity is None:
            return VerificationResult(
                passed=False,
                reason="Perplexity metric missing",
            )
        
        if metrics.perplexity > self.max_perplexity:
            return VerificationResult(
                passed=False,
                reason=f"Perplexity above threshold: {metrics.perplexity:.4f} > {self.max_perplexity:.4f}",
                details={
                    "actual": metrics.perplexity,
                    "threshold": self.max_perplexity,
                },
            )
        
        return VerificationResult(
            passed=True,
            reason=f"Perplexity meets threshold: {metrics.perplexity:.4f} <= {self.max_perplexity:.4f}",
            details={"perplexity": metrics.perplexity},
        )
    
    def check_toxicity(self, metrics: EvalMetrics) -> VerificationResult:
        """Check toxicity threshold."""
        if self.max_toxicity_score is None:
            return VerificationResult(passed=True, reason="No toxicity threshold set")
        
        if metrics.toxicity_score is None:
            return VerificationResult(
                passed=False,
                reason="Toxicity metric missing",
            )
        
        if metrics.toxicity_score > self.max_toxicity_score:
            return VerificationResult(
                passed=False,
                reason=f"Toxicity above threshold: {metrics.toxicity_score:.4f} > {self.max_toxicity_score:.4f}",
                details={
                    "actual": metrics.toxicity_score,
                    "threshold": self.max_toxicity_score,
                },
            )
        
        return VerificationResult(
            passed=True,
            reason=f"Toxicity meets threshold: {metrics.toxicity_score:.4f} <= {self.max_toxicity_score:.4f}",
            details={"toxicity_score": metrics.toxicity_score},
        )
    
    def check_regression_tests(self, metrics: EvalMetrics) -> VerificationResult:
        """Check regression test pass rate."""
        if self.min_regression_pass_rate is None:
            return VerificationResult(passed=True, reason="No regression threshold set")
        
        if metrics.regression_pass_rate is None:
            return VerificationResult(
                passed=False,
                reason="Regression pass rate metric missing",
            )
        
        if metrics.regression_pass_rate < self.min_regression_pass_rate:
            return VerificationResult(
                passed=False,
                reason=f"Regression pass rate below threshold: {metrics.regression_pass_rate:.4f} < {self.min_regression_pass_rate:.4f}",
                details={
                    "actual": metrics.regression_pass_rate,
                    "threshold": self.min_regression_pass_rate,
                },
            )
        
        return VerificationResult(
            passed=True,
            reason=f"Regression tests meet threshold: {metrics.regression_pass_rate:.4f} >= {self.min_regression_pass_rate:.4f}",
            details={"regression_pass_rate": metrics.regression_pass_rate},
        )
    
    def check_all(self, metrics: EvalMetrics) -> List[VerificationResult]:
        """
        Run all safety gate checks.
        
        Args:
            metrics: Evaluation metrics to check
        
        Returns:
            List of VerificationResults for each check
        """
        results = []
        
        # Run standard checks
        results.append(self.check_accuracy(metrics))
        results.append(self.check_perplexity(metrics))
        results.append(self.check_toxicity(metrics))
        results.append(self.check_regression_tests(metrics))
        
        # Run custom checks
        for metric_name, (comparison, threshold) in self.custom_thresholds.items():
            value = metrics.custom.get(metric_name)
            
            if value is None:
                results.append(VerificationResult(
                    passed=False,
                    reason=f"Custom metric '{metric_name}' missing",
                ))
                continue
            
            if comparison == "min":
                passed = value >= threshold
                op = ">="
            elif comparison == "max":
                passed = value <= threshold
                op = "<="
            else:
                results.append(VerificationResult(
                    passed=False,
                    reason=f"Invalid comparison operator: {comparison}",
                ))
                continue
            
            results.append(VerificationResult(
                passed=passed,
                reason=f"Custom metric '{metric_name}': {value:.4f} {op} {threshold:.4f}",
                details={
                    "metric": metric_name,
                    "actual": value,
                    "threshold": threshold,
                    "comparison": comparison,
                },
            ))
        
        return results
    
    def passes_all_gates(self, metrics: EvalMetrics) -> Tuple[bool, List[str]]:
        """
        Check if metrics pass all safety gates.
        
        Args:
            metrics: Evaluation metrics to check
        
        Returns:
            (passed, reasons) where passed is True if all gates passed,
            and reasons is a list of failure reasons (empty if passed)
        """
        results = self.check_all(metrics)
        
        failures = [r.reason for r in results if not r.passed]
        
        return len(failures) == 0, failures
