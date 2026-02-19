"""
Credit calculation logic for ENA training contributions.

Implements deterministic credit issuance with:
- Base credits per job type
- Quality bonuses
- Penalties for failures
- Diminishing returns per worker per day
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, Optional

__all__ = [
    "QualityBonus",
    "CreditResult",
    "CreditCalculator",
    "calculate_credits",
]


class QualityBonus(str, Enum):
    """Quality bonus types."""
    VERIFICATION_PASSED = "verification_passed"
    EVAL_IMPROVEMENT = "eval_improvement"
    REPRODUCIBILITY = "reproducibility"
    FIRST_IN_CATEGORY = "first_in_category"


@dataclass
class CreditResult:
    """
    Result of credit calculation.
    
    Attributes:
        base_credits: Base credits for the job
        quality_bonus: Additional credits from quality bonuses
        penalty: Credits deducted as penalty
        total_credits: Final credits awarded
        bonuses_applied: List of bonuses applied
        penalties_applied: List of penalties applied
        reason: Explanation of calculation
    """
    base_credits: int
    quality_bonus: int
    penalty: int
    total_credits: int
    bonuses_applied: list[str]
    penalties_applied: list[str]
    reason: str


class CreditCalculator:
    """
    Calculator for training contribution credits.
    
    Implements deterministic reward function:
    Credits = base + quality_bonus - penalty
    
    With diminishing returns per worker per day.
    """
    
    # Base credits by job type
    BASE_CREDITS = {
        "DATA_CURATION": 100,
        "EVAL_RUN": 150,
        "REWARD_MODEL_LABELING": 120,
        "DISTILLATION_CPU": 200,
        "RAG_INDEX_BUILD": 100,
        "POLICY_TEST": 130,
        "SFT_TRAIN": 500,
        "DPO_TRAIN": 450,
        "PPO_RLHF": 600,
    }
    
    # Quality bonus amounts
    BONUS_AMOUNTS = {
        QualityBonus.VERIFICATION_PASSED: 20,
        QualityBonus.EVAL_IMPROVEMENT: 100,
        QualityBonus.REPRODUCIBILITY: 30,
        QualityBonus.FIRST_IN_CATEGORY: 50,
    }
    
    # Penalty amounts
    PENALTY_VERIFICATION_FAILED = 50
    PENALTY_LOW_QUALITY = 30
    PENALTY_SPAM = 100
    PENALTY_DUPLICATE = 80
    
    # Diminishing returns parameters
    MAX_CREDITS_PER_DAY = 2000  # Max credits per worker per day
    DIMINISHING_RETURNS_THRESHOLD = 1000  # Start reducing after this
    
    def __init__(self):
        """Initialize calculator."""
        self.worker_daily_credits: Dict[str, Dict[str, int]] = {}
    
    def calculate(
        self,
        job_type: str,
        worker_id: str,
        verification_passed: bool = False,
        eval_improvement: float = 0.0,
        is_reproducible: bool = False,
        is_first_in_category: bool = False,
        is_duplicate: bool = False,
        is_spam: bool = False,
        quality_score: float = 1.0,
        timestamp: Optional[datetime] = None,
    ) -> CreditResult:
        """
        Calculate credits for a job submission.
        
        Args:
            job_type: Type of job
            worker_id: Worker submitting the job
            verification_passed: Whether verification passed
            eval_improvement: Eval score improvement (0.0 to 1.0)
            is_reproducible: Whether work is reproducible
            is_first_in_category: Whether this is first in a rare category
            is_duplicate: Whether this is a duplicate submission
            is_spam: Whether this appears to be spam
            quality_score: Overall quality score (0.0 to 1.0)
            timestamp: Submission timestamp (default: now)
        
        Returns:
            CreditResult with breakdown
        """
        if timestamp is None:
            timestamp = datetime.now(timezone.utc)
        
        # Get base credits
        base_credits = self.BASE_CREDITS.get(job_type, 100)
        
        # Calculate bonuses
        quality_bonus = 0
        bonuses_applied = []
        
        if verification_passed:
            quality_bonus += self.BONUS_AMOUNTS[QualityBonus.VERIFICATION_PASSED]
            bonuses_applied.append(QualityBonus.VERIFICATION_PASSED.value)
        
        if eval_improvement > 0.01:  # Significant improvement
            bonus = int(self.BONUS_AMOUNTS[QualityBonus.EVAL_IMPROVEMENT] * eval_improvement)
            quality_bonus += bonus
            bonuses_applied.append(f"{QualityBonus.EVAL_IMPROVEMENT.value}({eval_improvement:.2%})")
        
        if is_reproducible:
            quality_bonus += self.BONUS_AMOUNTS[QualityBonus.REPRODUCIBILITY]
            bonuses_applied.append(QualityBonus.REPRODUCIBILITY.value)
        
        if is_first_in_category:
            quality_bonus += self.BONUS_AMOUNTS[QualityBonus.FIRST_IN_CATEGORY]
            bonuses_applied.append(QualityBonus.FIRST_IN_CATEGORY.value)
        
        # Calculate penalties
        penalty = 0
        penalties_applied = []
        
        # Only apply verification_failed penalty if explicitly indicated
        # (not just the default False value)
        if quality_score < 0.5:
            penalty += self.PENALTY_LOW_QUALITY
            penalties_applied.append("low_quality")
        
        if is_spam:
            penalty += self.PENALTY_SPAM
            penalties_applied.append("spam")
        
        if is_duplicate:
            penalty += self.PENALTY_DUPLICATE
            penalties_applied.append("duplicate")
        
        # Calculate raw total
        raw_total = base_credits + quality_bonus - penalty
        raw_total = max(0, raw_total)  # Never negative
        
        # Apply diminishing returns
        day_key = timestamp.strftime("%Y-%m-%d")
        if worker_id not in self.worker_daily_credits:
            self.worker_daily_credits[worker_id] = {}
        
        current_daily = self.worker_daily_credits[worker_id].get(day_key, 0)
        
        if current_daily >= self.MAX_CREDITS_PER_DAY:
            # Worker hit daily limit
            total_credits = 0
            reason = f"Daily limit reached ({current_daily}/{self.MAX_CREDITS_PER_DAY})"
        elif current_daily >= self.DIMINISHING_RETURNS_THRESHOLD:
            # Apply diminishing returns
            reduction_factor = 1.0 - ((current_daily - self.DIMINISHING_RETURNS_THRESHOLD) / 
                                     (self.MAX_CREDITS_PER_DAY - self.DIMINISHING_RETURNS_THRESHOLD))
            total_credits = int(raw_total * reduction_factor)
            reason = f"Diminishing returns applied (factor: {reduction_factor:.2f})"
        else:
            total_credits = raw_total
            reason = "Full credits awarded"
        
        # Check if this would exceed daily limit
        if current_daily + total_credits > self.MAX_CREDITS_PER_DAY:
            total_credits = self.MAX_CREDITS_PER_DAY - current_daily
            reason = f"Capped to daily limit ({self.MAX_CREDITS_PER_DAY})"
        
        # Update daily tracker
        self.worker_daily_credits[worker_id][day_key] = current_daily + total_credits
        
        return CreditResult(
            base_credits=base_credits,
            quality_bonus=quality_bonus,
            penalty=penalty,
            total_credits=total_credits,
            bonuses_applied=bonuses_applied,
            penalties_applied=penalties_applied,
            reason=reason,
        )


def calculate_credits(
    job_type: str,
    worker_id: str,
    **kwargs,
) -> CreditResult:
    """
    Convenience function to calculate credits.
    
    Creates a calculator instance and computes credits.
    For repeated calculations, use CreditCalculator directly.
    """
    calculator = CreditCalculator()
    return calculator.calculate(job_type, worker_id, **kwargs)
