"""
Animica DA • Blob Assignment

This module implements blob assignment logic for DA providers:
- Assign blobs to R providers based on capacity, uptime, and diversity
- Update provider capacity commitments
- Store BlobAssignment records in registry

Design:
- Prefer providers with higher uptime scores (>= 5000)
- Ensure diversity (different providers for redundancy)
- Track capacity commitments to avoid over-assignment
"""

from __future__ import annotations

import hashlib
import time
from typing import List, Optional

from da.provider.registry import (
    BlobAssignment,
    DEFAULT_REPLICATION_FACTOR,
    ProviderEntry,
    ProviderRegistry,
)


class AssignmentError(Exception):
    """Raised when blob assignment fails."""
    pass


def assign_blob(
    registry: ProviderRegistry,
    blob_commitment: bytes,
    size: int,
    replication_factor: int = DEFAULT_REPLICATION_FACTOR,
    min_uptime_score: int = 5000,
) -> List[BlobAssignment]:
    """
    Assign a blob to R providers for redundant storage.
    
    Args:
        registry: Provider registry
        blob_commitment: 32-byte blob commitment (hash)
        size: Blob size in bytes
        replication_factor: Number of replicas (default: 3)
        min_uptime_score: Minimum uptime score required (default: 5000)
    
    Returns:
        List of BlobAssignment records created
    
    Raises:
        AssignmentError: If insufficient providers available
    """
    if len(blob_commitment) != 32:
        raise ValueError("blob_commitment must be 32 bytes")
    if size <= 0:
        raise ValueError("size must be positive")
    if replication_factor < 1:
        raise ValueError("replication_factor must be at least 1")
    
    # Get eligible providers
    candidates = _get_eligible_providers(
        registry=registry,
        blob_size=size,
        min_uptime_score=min_uptime_score,
    )
    
    if len(candidates) < replication_factor:
        raise AssignmentError(
            f"Insufficient providers: need {replication_factor}, found {len(candidates)}"
        )
    
    # Select providers using diversity criteria
    selected = _select_diverse_providers(
        candidates=candidates,
        blob_commitment=blob_commitment,
        count=replication_factor,
    )
    
    # Create assignments
    now = int(time.time())
    assignments: List[BlobAssignment] = []
    
    for provider in selected:
        assignment = BlobAssignment(
            blob_commitment=blob_commitment,
            provider_id=provider.provider_id,
            assigned_at=now,
            replicas=replication_factor,
            blob_size=size,
        )
        
        # Store assignment
        registry.add_assignment(assignment)
        
        # Update provider capacity
        provider.capacity_bytes_committed += size
        registry.register_provider(provider)
        
        assignments.append(assignment)
    
    return assignments


def _get_eligible_providers(
    registry: ProviderRegistry,
    blob_size: int,
    min_uptime_score: int,
) -> List[ProviderEntry]:
    """
    Get providers eligible for blob assignment.
    
    Criteria:
    - Active (not jailed)
    - Uptime score >= min_uptime_score
    - Available capacity >= blob_size
    """
    eligible = []
    now = int(time.time())
    
    for _, provider in registry.list_providers(active_only=True):
        # Check if jailed
        if provider.jailed_until is not None and provider.jailed_until > now:
            continue
        
        # Check uptime score
        if provider.uptime_score < min_uptime_score:
            continue
        
        # Check available capacity
        available = provider.capacity_bytes_advertised - provider.capacity_bytes_committed
        if available < blob_size:
            continue
        
        eligible.append(provider)
    
    return eligible


def _select_diverse_providers(
    candidates: List[ProviderEntry],
    blob_commitment: bytes,
    count: int,
) -> List[ProviderEntry]:
    """
    Select diverse providers for redundancy.
    
    Strategy:
    1. Sort by uptime score (descending) for quality
    2. Prefer different regions (region_tags diversity)
    3. Use deterministic shuffling based on blob_commitment for fairness
    """
    if len(candidates) <= count:
        return candidates[:count]
    
    # Score each provider
    scored = []
    for provider in candidates:
        score = _compute_provider_score(provider, blob_commitment)
        scored.append((score, provider))
    
    # Sort by score (descending) and take top N
    scored.sort(key=lambda x: x[0], reverse=True)
    selected = [provider for _, provider in scored[:count]]
    
    # Ensure diversity: no duplicate provider_ids
    seen_ids = set()
    diverse = []
    for provider in selected:
        if provider.provider_id not in seen_ids:
            diverse.append(provider)
            seen_ids.add(provider.provider_id)
    
    # Ensure diversity: prefer different regions
    diverse = _maximize_region_diversity(diverse, scored[count:], count)
    
    return diverse


def _compute_provider_score(
    provider: ProviderEntry,
    blob_commitment: bytes,
) -> float:
    """
    Compute assignment score for a provider.
    
    Components:
    - Uptime score (primary)
    - Deterministic shuffle based on blob_commitment (fairness)
    - Available capacity (prefer less loaded providers)
    """
    # Base: uptime score (0-10000)
    score = float(provider.uptime_score)
    
    # Deterministic shuffle: hash(blob_commitment + provider_id)
    combined = blob_commitment + provider.provider_id
    hash_val = int.from_bytes(
        hashlib.sha3_256(combined).digest()[:8],
        byteorder='big',
    )
    # Normalize to [0, 1000]
    shuffle_bonus = (hash_val % 1000)
    score += shuffle_bonus
    
    # Capacity: prefer providers with more available space
    available = provider.capacity_bytes_advertised - provider.capacity_bytes_committed
    if provider.capacity_bytes_advertised > 0:
        capacity_ratio = available / provider.capacity_bytes_advertised
        score += capacity_ratio * 500  # up to 500 bonus points
    
    return score


def _maximize_region_diversity(
    selected: List[ProviderEntry],
    remaining: List[tuple[float, ProviderEntry]],
    target_count: int,
) -> List[ProviderEntry]:
    """
    Ensure region diversity by swapping if needed.
    """
    if len(selected) >= target_count:
        return selected[:target_count]
    
    # Collect regions in selected providers
    selected_regions = set()
    for provider in selected:
        for tag in provider.region_tags:
            selected_regions.add(tag)
    
    # Try to fill remaining slots with providers from different regions
    for _, provider in remaining:
        if len(selected) >= target_count:
            break
        
        # Check if this provider adds region diversity
        adds_diversity = False
        for tag in provider.region_tags:
            if tag not in selected_regions:
                adds_diversity = True
                break
        
        if adds_diversity or len(selected) < target_count:
            # Check if not already selected
            if provider.provider_id not in {p.provider_id for p in selected}:
                selected.append(provider)
                for tag in provider.region_tags:
                    selected_regions.add(tag)
    
    return selected[:target_count]


def get_blob_providers(
    registry: ProviderRegistry,
    blob_commitment: bytes,
) -> List[ProviderEntry]:
    """
    Get all providers assigned to a blob.
    
    Args:
        registry: Provider registry
        blob_commitment: 32-byte blob commitment
    
    Returns:
        List of ProviderEntry objects
    """
    providers = []
    
    # Query all assignments for this blob
    with registry._init_db.__self__ as conn:  # Access DB connection
        import sqlite3
        conn = sqlite3.connect(str(registry.db_path))
        cursor = conn.execute(
            "SELECT provider_id FROM blob_assignments WHERE blob_commitment = ?",
            (blob_commitment,),
        )
        rows = cursor.fetchall()
    
    for (provider_id,) in rows:
        provider = registry.get_provider(provider_id)
        if provider:
            providers.append(provider)
    
    return providers


__all__ = [
    "assign_blob",
    "get_blob_providers",
    "AssignmentError",
]
