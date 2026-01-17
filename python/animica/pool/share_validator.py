"""
Share validation and work weight calculation for the mining pool.

Validates submitted shares and calculates work weight for PPLNS.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class ShareSubmission:
    """A share submission from a miner."""

    job_id: str
    miner_address: str
    worker_name: str
    nonce: int
    extranonce2: str
    difficulty: float
    height: int
    timestamp: float


@dataclass
class ShareValidationResult:
    """Result of share validation."""

    accepted: bool
    reason: Optional[str]  # Rejection reason if not accepted
    work_weight: int  # Integer work weight for PPLNS
    meets_network_target: bool  # Did this share find a block?
    block_hash: Optional[str]  # Block hash if found


class ShareValidator:
    """
    Validates shares and calculates work weights.
    
    Work weight is calculated as: difficulty * WORK_MULTIPLIER
    This ensures deterministic integer arithmetic for PPLNS.
    """

    WORK_MULTIPLIER = 1_000_000  # Scale factor for work weights

    def __init__(
        self,
        *,
        max_stale_job_age_sec: float = 120.0,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._max_stale_job_age = max_stale_job_age_sec
        self._log = logger or logging.getLogger("animica.pool.share_validator")
        
        # Duplicate detection (keep recent submissions)
        self._recent_submissions: dict[str, float] = {}
        self._duplicate_window_sec = 60.0

    def validate(
        self,
        submission: ShareSubmission,
        current_job_id: str,
        network_difficulty: float,
    ) -> ShareValidationResult:
        """
        Validate a share submission.
        
        Args:
            submission: Share submission to validate
            current_job_id: Current active job ID
            network_difficulty: Current network difficulty
        
        Returns:
            ShareValidationResult with acceptance status and work weight
        """
        now = time.time()
        
        # Check for duplicate
        dedup_key = f"{submission.job_id}:{submission.miner_address}:{submission.worker_name}:{submission.nonce}:{submission.extranonce2}"
        if dedup_key in self._recent_submissions:
            return ShareValidationResult(
                accepted=False,
                reason="duplicate",
                work_weight=0,
                meets_network_target=False,
                block_hash=None,
            )
        
        # Check if job is stale
        job_age = now - submission.timestamp
        if submission.job_id != current_job_id:
            if job_age > self._max_stale_job_age:
                return ShareValidationResult(
                    accepted=False,
                    reason="stale",
                    work_weight=0,
                    meets_network_target=False,
                    block_hash=None,
                )
        
        # Calculate work weight (deterministic integer)
        work_weight = self._calculate_work_weight(submission.difficulty)
        
        # Check if meets network target (block found)
        meets_network_target = submission.difficulty >= network_difficulty
        
        # Record this submission to prevent duplicates
        self._recent_submissions[dedup_key] = now
        self._cleanup_old_submissions(now)
        
        # Accept the share
        return ShareValidationResult(
            accepted=True,
            reason=None,
            work_weight=work_weight,
            meets_network_target=meets_network_target,
            block_hash=None,  # Will be filled by block tracker
        )

    def _calculate_work_weight(self, difficulty: float) -> int:
        """
        Calculate integer work weight from difficulty.
        
        Work weight = difficulty * WORK_MULTIPLIER
        This allows for deterministic integer math in PPLNS calculations.
        """
        return int(difficulty * self.WORK_MULTIPLIER)

    def _cleanup_old_submissions(self, now: float) -> None:
        """Remove old submissions from duplicate detection cache."""
        cutoff = now - self._duplicate_window_sec
        to_remove = [
            key for key, timestamp in self._recent_submissions.items()
            if timestamp < cutoff
        ]
        for key in to_remove:
            del self._recent_submissions[key]

    def validate_difficulty(
        self,
        submitted_diff: float,
        required_diff: float,
    ) -> tuple[bool, Optional[str]]:
        """
        Check if submitted share meets required difficulty.
        
        Returns:
            (valid, rejection_reason)
        """
        if submitted_diff < required_diff:
            return False, "low_difficulty"
        return True, None
