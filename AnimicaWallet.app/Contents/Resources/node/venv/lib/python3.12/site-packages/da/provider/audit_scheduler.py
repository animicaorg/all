"""
Animica DA • Audit Scheduler

This module implements automated audit scheduling:
- Select random sample of provider-blob pairs
- Send challenges, collect responses, verify
- Update scores: +100 for pass, -200 for fail
- Jail providers with score < 1000 for 24 hours

Design:
- Configurable sample size and frequency
- Fair random selection using deterministic seed
- Automatic jailing and score updates
- Metrics tracking for monitoring
"""

from __future__ import annotations

import hashlib
import random
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Set, Tuple

from da.provider.audit import (
    AuditDatabase,
    SCORE_DELTA_FAIL,
    SCORE_DELTA_PASS,
    create_challenge,
    update_provider_score,
    verify_response,
)
from da.provider.registry import (
    AuditChallenge,
    AuditResponse,
    AuditResult,
    ProviderEntry,
    ProviderRegistry,
)


# -------------------------------- Constants -----------------------------------

DEFAULT_SAMPLE_SIZE = 10  # Number of audits per round
DEFAULT_JAIL_THRESHOLD = 1000  # Jail if score falls below this
DEFAULT_JAIL_DURATION_SECONDS = 24 * 3600  # 24 hours
MIN_AUDIT_INTERVAL_SECONDS = 300  # 5 minutes between audits of same provider


# -------------------------------- Configuration -------------------------------


@dataclass
class AuditSchedulerConfig:
    """Configuration for audit scheduler."""
    
    sample_size: int = DEFAULT_SAMPLE_SIZE
    jail_threshold: int = DEFAULT_JAIL_THRESHOLD
    jail_duration_seconds: int = DEFAULT_JAIL_DURATION_SECONDS
    min_audit_interval_seconds: int = MIN_AUDIT_INTERVAL_SECONDS
    
    # Challenge parameters
    challenge_type: str = "byte-range"
    deadline_seconds: int = 3600  # 1 hour


# -------------------------------- Scheduler -----------------------------------


class AuditScheduler:
    """
    Audit scheduler for DA providers.
    
    Runs periodic audit rounds:
    1. Select random provider-blob pairs
    2. Create and send challenges
    3. Collect and verify responses
    4. Update scores and jail if needed
    """
    
    def __init__(
        self,
        registry: ProviderRegistry,
        audit_db: AuditDatabase,
        config: Optional[AuditSchedulerConfig] = None,
    ) -> None:
        self.registry = registry
        self.audit_db = audit_db
        self.config = config or AuditSchedulerConfig()
        self._last_audit_times: dict[bytes, int] = {}
    
    def run_audit_round(
        self,
        blob_data_provider: Optional[callable] = None,
    ) -> List[AuditResult]:
        """
        Run a single audit round.
        
        Args:
            blob_data_provider: Optional function(blob_commitment) -> bytes
                                to fetch actual blob data for verification
        
        Returns:
            List of AuditResult objects
        """
        # Select provider-blob pairs to audit
        pairs = self._select_audit_pairs()
        
        if not pairs:
            return []
        
        results: List[AuditResult] = []
        now = int(time.time())
        
        for provider_id, blob_commitment in pairs:
            # Create challenge
            challenge = create_challenge(
                provider_id=provider_id,
                blob_commitment=blob_commitment,
                challenge_type=self.config.challenge_type,
                deadline_seconds=self.config.deadline_seconds,
            )
            
            # Store challenge
            self.audit_db.store_challenge(challenge)
            
            # Simulate immediate response (in production, would wait for provider)
            # For now, we mark as failed if no response
            response = self._get_or_simulate_response(challenge)
            
            if response is None:
                # No response - fail
                result = self._create_failed_result(
                    challenge=challenge,
                    reason="no response",
                )
            else:
                # Verify response
                result = self._verify_and_create_result(
                    challenge=challenge,
                    response=response,
                    blob_data_provider=blob_data_provider,
                )
            
            # Store result
            self.audit_db.store_result(result)
            results.append(result)
            
            # Update provider score
            update_provider_score(
                registry=self.registry,
                provider_id=provider_id,
                passed=result.passed,
            )
            
            # Update last audit time
            self._last_audit_times[provider_id] = now
        
        # Jail providers with low scores
        self._jail_low_score_providers()
        
        return results
    
    def _select_audit_pairs(self) -> List[Tuple[bytes, bytes]]:
        """
        Select random provider-blob pairs for auditing.
        
        Returns:
            List of (provider_id, blob_commitment) tuples
        """
        candidates: List[Tuple[bytes, bytes]] = []
        now = int(time.time())
        
        # Get all active providers
        providers = self.registry.list_providers(active_only=True)
        
        for _, provider in providers:
            # Skip if recently audited
            last_audit = self._last_audit_times.get(provider.provider_id, 0)
            if now - last_audit < self.config.min_audit_interval_seconds:
                continue
            
            # Skip if jailed
            if provider.jailed_until is not None and provider.jailed_until > now:
                continue
            
            # Get assignments for this provider
            assignments = self.registry.get_assignments_for_provider(
                provider.provider_id
            )
            
            for assignment in assignments:
                candidates.append((
                    provider.provider_id,
                    assignment.blob_commitment,
                ))
        
        # Random sample
        if len(candidates) <= self.config.sample_size:
            return candidates
        
        # Use deterministic random seed based on current hour
        # This ensures all nodes in network select same pairs
        hour_seed = int(time.time() // 3600)
        seed = hashlib.sha3_256(hour_seed.to_bytes(8, byteorder='big')).digest()
        rng = random.Random(int.from_bytes(seed[:8], byteorder='big'))
        
        return rng.sample(candidates, self.config.sample_size)
    
    def _get_or_simulate_response(
        self,
        challenge: AuditChallenge,
    ) -> Optional[AuditResponse]:
        """
        Get response from database or simulate for testing.
        
        In production, this would query the response database.
        For testing, we simulate no response.
        """
        # Check if response exists
        response = self.audit_db.get_response(challenge.challenge_id)
        return response
    
    def _create_failed_result(
        self,
        challenge: AuditChallenge,
        reason: str,
    ) -> AuditResult:
        """Create a failed audit result."""
        return AuditResult(
            challenge_id=challenge.challenge_id,
            provider_id=challenge.provider_id,
            passed=False,
            verified_at=int(time.time()),
            failure_reason=reason,
            score_delta=SCORE_DELTA_FAIL,
        )
    
    def _verify_and_create_result(
        self,
        challenge: AuditChallenge,
        response: AuditResponse,
        blob_data_provider: Optional[callable] = None,
    ) -> AuditResult:
        """Verify response and create result."""
        # Get provider entry for pubkey
        provider = self.registry.get_provider(challenge.provider_id)
        if not provider:
            return self._create_failed_result(
                challenge=challenge,
                reason="provider not found",
            )
        
        # Get actual blob data if provider available
        actual_blob_data = None
        if blob_data_provider is not None:
            try:
                actual_blob_data = blob_data_provider(challenge.blob_commitment)
            except Exception as e:
                # If we can't get blob data, skip data verification
                pass
        
        # Verify response
        passed, failure_reason = verify_response(
            challenge=challenge,
            response=response,
            provider=provider,
            actual_blob_data=actual_blob_data,
        )
        
        score_delta = SCORE_DELTA_PASS if passed else SCORE_DELTA_FAIL
        
        return AuditResult(
            challenge_id=challenge.challenge_id,
            provider_id=challenge.provider_id,
            passed=passed,
            verified_at=int(time.time()),
            failure_reason=failure_reason,
            score_delta=score_delta,
        )
    
    def _jail_low_score_providers(self) -> None:
        """Jail providers with scores below threshold."""
        now = int(time.time())
        
        for _, provider in self.registry.list_providers(active_only=True):
            if provider.uptime_score < self.config.jail_threshold:
                # Jail provider
                provider.jailed_until = now + self.config.jail_duration_seconds
                provider.active = False
                self.registry.register_provider(provider)
    
    def get_audit_stats(self, provider_id: bytes) -> dict:
        """
        Get audit statistics for a provider.
        
        Returns:
            Dictionary with stats: total, passed, failed, pass_rate, avg_score_delta
        """
        results = self.audit_db.get_results_for_provider(provider_id)
        
        if not results:
            return {
                "total": 0,
                "passed": 0,
                "failed": 0,
                "pass_rate": 0.0,
                "avg_score_delta": 0.0,
            }
        
        passed = sum(1 for r in results if r.passed)
        failed = len(results) - passed
        pass_rate = passed / len(results) if results else 0.0
        avg_score_delta = sum(r.score_delta for r in results) / len(results)
        
        return {
            "total": len(results),
            "passed": passed,
            "failed": failed,
            "pass_rate": pass_rate,
            "avg_score_delta": avg_score_delta,
        }


# -------------------------------- Helpers -------------------------------------


def jail_provider(
    registry: ProviderRegistry,
    provider_id: bytes,
    duration_seconds: int = DEFAULT_JAIL_DURATION_SECONDS,
    reason: Optional[str] = None,
) -> None:
    """
    Jail a provider for a specified duration.
    
    Args:
        registry: Provider registry
        provider_id: Provider ID to jail
        duration_seconds: Jail duration in seconds
        reason: Optional reason for jailing
    """
    provider = registry.get_provider(provider_id)
    if not provider:
        return
    
    now = int(time.time())
    provider.jailed_until = now + duration_seconds
    provider.active = False
    if reason:
        provider.notes = f"Jailed: {reason}"
    
    registry.register_provider(provider)


def unjail_provider(
    registry: ProviderRegistry,
    provider_id: bytes,
) -> None:
    """
    Unjail a provider.
    
    Args:
        registry: Provider registry
        provider_id: Provider ID to unjail
    """
    provider = registry.get_provider(provider_id)
    if not provider:
        return
    
    provider.jailed_until = None
    provider.active = True
    provider.notes = None
    
    registry.register_provider(provider)


def get_jailed_providers(
    registry: ProviderRegistry,
) -> List[Tuple[bytes, ProviderEntry]]:
    """
    Get all currently jailed providers.
    
    Returns:
        List of (provider_id, ProviderEntry) tuples
    """
    jailed = []
    now = int(time.time())
    
    for provider_id, provider in registry.list_providers(active_only=False):
        if provider.jailed_until is not None and provider.jailed_until > now:
            jailed.append((provider_id, provider))
    
    return jailed


__all__ = [
    "AuditScheduler",
    "AuditSchedulerConfig",
    "jail_provider",
    "unjail_provider",
    "get_jailed_providers",
    "DEFAULT_SAMPLE_SIZE",
    "DEFAULT_JAIL_THRESHOLD",
    "DEFAULT_JAIL_DURATION_SECONDS",
]
