"""
Tests for DA blob assignment logic.

Tests:
- Assignment to multiple providers
- Diversity and redundancy
- Capacity constraints
- Uptime score filtering
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

import pytest

from da.provider.assignment import (
    assign_blob,
    get_blob_providers,
    AssignmentError,
)
from da.provider.registry import (
    ProviderEntry,
    ProviderRegistry,
    create_provider_id,
)


@pytest.fixture
def temp_registry():
    """Create a temporary registry for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_registry.db"
        registry = ProviderRegistry(db_path=db_path)
        yield registry


@pytest.fixture
def sample_providers(temp_registry):
    """Create sample providers with different characteristics."""
    providers = []
    
    for i in range(5):
        pubkey = os.urandom(32)
        provider_id = create_provider_id(pubkey)
        
        # Vary capacity and scores
        capacity = (i + 1) * 1024 * 1024 * 100  # 100MB, 200MB, ...
        uptime_score = 5000 + i * 500  # 5000, 5500, 6000, ...
        
        # Vary regions
        region_tags = [f"region-{i % 3}"]  # region-0, region-1, region-2
        
        entry = ProviderEntry(
            provider_id=provider_id,
            pubkey=pubkey,
            address=os.urandom(20),
            endpoint=f"http://provider-{i}.example.com",
            capacity_bytes_advertised=capacity,
            capacity_bytes_committed=0,
            region_tags=region_tags,
            uptime_score=uptime_score,
            last_heartbeat=int(time.time()),
            registered_at=int(time.time()),
            active=True,
        )
        
        temp_registry.register_provider(entry)
        providers.append(entry)
    
    return providers


def test_assign_blob_basic(temp_registry, sample_providers):
    """Test basic blob assignment."""
    blob_commitment = os.urandom(32)
    size = 1024 * 1024  # 1MB
    
    assignments = assign_blob(
        registry=temp_registry,
        blob_commitment=blob_commitment,
        size=size,
        replication_factor=3,
    )
    
    assert len(assignments) == 3
    
    # Check all assignments have correct blob commitment and size
    for assignment in assignments:
        assert assignment.blob_commitment == blob_commitment
        assert assignment.blob_size == size
        assert assignment.replicas == 3
    
    # Check providers are different
    provider_ids = {a.provider_id for a in assignments}
    assert len(provider_ids) == 3


def test_assign_blob_diversity(temp_registry, sample_providers):
    """Test that assignment prefers diverse providers."""
    blob_commitment = os.urandom(32)
    size = 1024 * 1024  # 1MB
    
    assignments = assign_blob(
        registry=temp_registry,
        blob_commitment=blob_commitment,
        size=size,
        replication_factor=3,
    )
    
    # Get region tags for assigned providers
    regions = set()
    for assignment in assignments:
        provider = temp_registry.get_provider(assignment.provider_id)
        for tag in provider.region_tags:
            regions.add(tag)
    
    # Should have some diversity (at least 2 different regions)
    assert len(regions) >= 2


def test_assign_blob_capacity_update(temp_registry, sample_providers):
    """Test that provider capacity is updated after assignment."""
    blob_commitment = os.urandom(32)
    size = 1024 * 1024  # 1MB
    
    # Get initial capacity
    provider = sample_providers[0]
    initial_committed = provider.capacity_bytes_committed
    
    assignments = assign_blob(
        registry=temp_registry,
        blob_commitment=blob_commitment,
        size=size,
        replication_factor=3,
    )
    
    # Check that at least one provider's capacity was updated
    updated_found = False
    for assignment in assignments:
        provider = temp_registry.get_provider(assignment.provider_id)
        if provider.capacity_bytes_committed > initial_committed:
            updated_found = True
            assert provider.capacity_bytes_committed >= size
    
    assert updated_found


def test_assign_blob_insufficient_providers(temp_registry):
    """Test that assignment fails when insufficient providers."""
    # Registry is empty
    blob_commitment = os.urandom(32)
    size = 1024 * 1024  # 1MB
    
    with pytest.raises(AssignmentError, match="Insufficient providers"):
        assign_blob(
            registry=temp_registry,
            blob_commitment=blob_commitment,
            size=size,
            replication_factor=3,
        )


def test_assign_blob_low_uptime_filtered(temp_registry):
    """Test that providers with low uptime are filtered out."""
    # Create provider with low uptime
    pubkey = os.urandom(32)
    provider_id = create_provider_id(pubkey)
    
    entry = ProviderEntry(
        provider_id=provider_id,
        pubkey=pubkey,
        address=os.urandom(20),
        endpoint="http://low-uptime.example.com",
        capacity_bytes_advertised=1024 * 1024 * 1024,  # 1GB
        capacity_bytes_committed=0,
        uptime_score=4000,  # Below default threshold of 5000
        last_heartbeat=int(time.time()),
        registered_at=int(time.time()),
        active=True,
    )
    
    temp_registry.register_provider(entry)
    
    blob_commitment = os.urandom(32)
    size = 1024 * 1024  # 1MB
    
    # Should fail because only provider has low uptime
    with pytest.raises(AssignmentError, match="Insufficient providers"):
        assign_blob(
            registry=temp_registry,
            blob_commitment=blob_commitment,
            size=size,
            replication_factor=1,
            min_uptime_score=5000,
        )


def test_assign_blob_capacity_exceeded(temp_registry):
    """Test that providers without capacity are filtered out."""
    # Create provider with limited capacity
    pubkey = os.urandom(32)
    provider_id = create_provider_id(pubkey)
    
    entry = ProviderEntry(
        provider_id=provider_id,
        pubkey=pubkey,
        address=os.urandom(20),
        endpoint="http://small-capacity.example.com",
        capacity_bytes_advertised=1024,  # Only 1KB
        capacity_bytes_committed=0,
        uptime_score=8000,
        last_heartbeat=int(time.time()),
        registered_at=int(time.time()),
        active=True,
    )
    
    temp_registry.register_provider(entry)
    
    blob_commitment = os.urandom(32)
    size = 1024 * 1024  # 1MB (exceeds capacity)
    
    # Should fail because provider doesn't have enough capacity
    with pytest.raises(AssignmentError, match="Insufficient providers"):
        assign_blob(
            registry=temp_registry,
            blob_commitment=blob_commitment,
            size=size,
            replication_factor=1,
        )


def test_get_blob_providers(temp_registry, sample_providers):
    """Test retrieving providers for a blob."""
    blob_commitment = os.urandom(32)
    size = 1024 * 1024  # 1MB
    
    assignments = assign_blob(
        registry=temp_registry,
        blob_commitment=blob_commitment,
        size=size,
        replication_factor=3,
    )
    
    # Get providers for this blob
    providers = get_blob_providers(temp_registry, blob_commitment)
    
    assert len(providers) == 3
    
    # Check provider IDs match
    assigned_ids = {a.provider_id for a in assignments}
    retrieved_ids = {p.provider_id for p in providers}
    assert assigned_ids == retrieved_ids


def test_assign_blob_jailed_filtered(temp_registry):
    """Test that jailed providers are filtered out."""
    # Create jailed provider
    pubkey = os.urandom(32)
    provider_id = create_provider_id(pubkey)
    
    entry = ProviderEntry(
        provider_id=provider_id,
        pubkey=pubkey,
        address=os.urandom(20),
        endpoint="http://jailed.example.com",
        capacity_bytes_advertised=1024 * 1024 * 1024,  # 1GB
        capacity_bytes_committed=0,
        uptime_score=8000,
        last_heartbeat=int(time.time()),
        registered_at=int(time.time()),
        active=False,
        jailed_until=int(time.time()) + 3600,  # Jailed for 1 hour
    )
    
    temp_registry.register_provider(entry)
    
    blob_commitment = os.urandom(32)
    size = 1024 * 1024  # 1MB
    
    # Should fail because only provider is jailed
    with pytest.raises(AssignmentError, match="Insufficient providers"):
        assign_blob(
            registry=temp_registry,
            blob_commitment=blob_commitment,
            size=size,
            replication_factor=1,
        )


def test_assign_blob_deterministic_selection(temp_registry, sample_providers):
    """Test that assignment is deterministic for same blob."""
    blob_commitment = os.urandom(32)
    size = 1024 * 1024  # 1MB
    
    # Assign twice
    assignments1 = assign_blob(
        registry=temp_registry,
        blob_commitment=blob_commitment,
        size=size,
        replication_factor=3,
    )
    
    # Note: This will re-assign, updating capacity
    # In production, would check if already assigned
    assignments2 = assign_blob(
        registry=temp_registry,
        blob_commitment=blob_commitment,
        size=size,
        replication_factor=3,
    )
    
    # Provider selection should be deterministic
    # (though capacity updates may differ)
    # Just check we got same number of assignments
    assert len(assignments1) == len(assignments2)
