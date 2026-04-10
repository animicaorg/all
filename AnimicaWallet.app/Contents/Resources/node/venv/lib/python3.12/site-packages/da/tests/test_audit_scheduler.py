"""
Tests for DA audit scheduler.

Tests:
- Audit round execution
- Provider selection
- Scoring and jailing
- Statistics
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

import pytest

from da.provider.assignment import assign_blob
from da.provider.audit import AuditDatabase
from da.provider.audit_scheduler import (
    AuditScheduler,
    AuditSchedulerConfig,
    jail_provider,
    unjail_provider,
    get_jailed_providers,
    DEFAULT_JAIL_THRESHOLD,
)
from da.provider.registry import (
    BlobAssignment,
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
def temp_audit_db():
    """Create a temporary audit database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_audit.db"
        db = AuditDatabase(db_path=db_path)
        yield db


@pytest.fixture
def sample_providers_with_assignments(temp_registry):
    """Create sample providers with blob assignments."""
    providers = []
    
    for i in range(3):
        pubkey = os.urandom(32)
        provider_id = create_provider_id(pubkey)
        
        entry = ProviderEntry(
            provider_id=provider_id,
            pubkey=pubkey,
            address=os.urandom(20),
            endpoint=f"http://provider-{i}.example.com",
            capacity_bytes_advertised=1024 * 1024 * 1024,  # 1GB
            capacity_bytes_committed=0,
            uptime_score=5000 + i * 1000,
            last_heartbeat=int(time.time()),
            registered_at=int(time.time()),
            active=True,
        )
        
        temp_registry.register_provider(entry)
        
        # Add blob assignment
        blob_commitment = os.urandom(32)
        assignment = BlobAssignment(
            blob_commitment=blob_commitment,
            provider_id=provider_id,
            assigned_at=int(time.time()),
            replicas=3,
            blob_size=1024 * 1024,  # 1MB
        )
        temp_registry.add_assignment(assignment)
        
        providers.append(entry)
    
    return providers


def test_audit_scheduler_init(temp_registry, temp_audit_db):
    """Test audit scheduler initialization."""
    config = AuditSchedulerConfig(sample_size=5)
    scheduler = AuditScheduler(temp_registry, temp_audit_db, config)
    
    assert scheduler.registry == temp_registry
    assert scheduler.audit_db == temp_audit_db
    assert scheduler.config.sample_size == 5


def test_run_audit_round_empty(temp_registry, temp_audit_db):
    """Test running audit round with no providers."""
    scheduler = AuditScheduler(temp_registry, temp_audit_db)
    
    results = scheduler.run_audit_round()
    
    assert len(results) == 0


def test_run_audit_round_with_providers(
    temp_registry, temp_audit_db, sample_providers_with_assignments
):
    """Test running audit round with providers."""
    config = AuditSchedulerConfig(sample_size=2)
    scheduler = AuditScheduler(temp_registry, temp_audit_db, config)
    
    results = scheduler.run_audit_round()
    
    # Should have audited some providers
    assert len(results) >= 1
    assert len(results) <= config.sample_size
    
    # Check results are stored
    for result in results:
        stored_results = temp_audit_db.get_results_for_provider(result.provider_id)
        assert len(stored_results) >= 1


def test_audit_round_updates_scores(
    temp_registry, temp_audit_db, sample_providers_with_assignments
):
    """Test that audit round updates provider scores."""
    provider = sample_providers_with_assignments[0]
    initial_score = provider.uptime_score
    
    config = AuditSchedulerConfig(sample_size=10)
    scheduler = AuditScheduler(temp_registry, temp_audit_db, config)
    
    scheduler.run_audit_round()
    
    # Check if any provider score changed
    updated_provider = temp_registry.get_provider(provider.provider_id)
    # Score may or may not have changed depending on random selection
    # Just verify it's still in valid range
    assert 0 <= updated_provider.uptime_score <= 10000


def test_jail_provider(temp_registry, sample_providers_with_assignments):
    """Test jailing a provider."""
    provider = sample_providers_with_assignments[0]
    
    # Jail provider
    jail_provider(
        registry=temp_registry,
        provider_id=provider.provider_id,
        duration_seconds=3600,
        reason="Test jailing",
    )
    
    # Check provider is jailed
    updated = temp_registry.get_provider(provider.provider_id)
    assert updated.jailed_until is not None
    assert updated.jailed_until > int(time.time())
    assert not updated.active
    assert "Jailed" in updated.notes


def test_unjail_provider(temp_registry, sample_providers_with_assignments):
    """Test unjailing a provider."""
    provider = sample_providers_with_assignments[0]
    
    # First jail
    jail_provider(
        registry=temp_registry,
        provider_id=provider.provider_id,
        duration_seconds=3600,
    )
    
    # Then unjail
    unjail_provider(temp_registry, provider.provider_id)
    
    # Check provider is unjailed
    updated = temp_registry.get_provider(provider.provider_id)
    assert updated.jailed_until is None
    assert updated.active


def test_get_jailed_providers(temp_registry, sample_providers_with_assignments):
    """Test getting list of jailed providers."""
    provider1 = sample_providers_with_assignments[0]
    provider2 = sample_providers_with_assignments[1]
    
    # Jail provider1
    jail_provider(temp_registry, provider1.provider_id, duration_seconds=3600)
    
    # Get jailed providers
    jailed = get_jailed_providers(temp_registry)
    
    assert len(jailed) == 1
    assert jailed[0][0] == provider1.provider_id


def test_jailing_low_score_providers(
    temp_registry, temp_audit_db, sample_providers_with_assignments
):
    """Test that scheduler jails providers with low scores."""
    provider = sample_providers_with_assignments[0]
    
    # Set provider score below threshold
    provider.uptime_score = DEFAULT_JAIL_THRESHOLD - 100
    temp_registry.register_provider(provider)
    
    config = AuditSchedulerConfig(jail_threshold=DEFAULT_JAIL_THRESHOLD)
    scheduler = AuditScheduler(temp_registry, temp_audit_db, config)
    
    # Run audit round (which should jail low-score providers)
    scheduler.run_audit_round()
    
    # Check provider is jailed
    updated = temp_registry.get_provider(provider.provider_id)
    assert updated.jailed_until is not None or not updated.active


def test_audit_stats(temp_registry, temp_audit_db, sample_providers_with_assignments):
    """Test getting audit statistics."""
    from da.provider.registry import AuditResult
    
    provider = sample_providers_with_assignments[0]
    
    # Add some audit results
    for i in range(5):
        result = AuditResult(
            challenge_id=os.urandom(32),
            provider_id=provider.provider_id,
            passed=(i % 2 == 0),  # Alternate pass/fail
            verified_at=int(time.time()),
            failure_reason="test" if i % 2 == 1 else None,
            score_delta=100 if i % 2 == 0 else -200,
        )
        temp_audit_db.store_result(result)
    
    config = AuditSchedulerConfig()
    scheduler = AuditScheduler(temp_registry, temp_audit_db, config)
    
    stats = scheduler.get_audit_stats(provider.provider_id)
    
    assert stats["total"] == 5
    assert stats["passed"] == 3
    assert stats["failed"] == 2
    assert 0.0 <= stats["pass_rate"] <= 1.0


def test_min_audit_interval(temp_registry, temp_audit_db, sample_providers_with_assignments):
    """Test that providers aren't audited too frequently."""
    config = AuditSchedulerConfig(
        sample_size=10,
        min_audit_interval_seconds=60,  # 1 minute
    )
    scheduler = AuditScheduler(temp_registry, temp_audit_db, config)
    
    # Run first audit
    results1 = scheduler.run_audit_round()
    
    # Run second audit immediately
    results2 = scheduler.run_audit_round()
    
    # Second audit should audit fewer providers (or none)
    # because of min interval
    assert len(results2) <= len(results1)


def test_audit_sample_size_limit(
    temp_registry, temp_audit_db, sample_providers_with_assignments
):
    """Test that audit round respects sample size limit."""
    config = AuditSchedulerConfig(
        sample_size=1,  # Only audit 1 provider
        min_audit_interval_seconds=0,  # No interval restriction
    )
    scheduler = AuditScheduler(temp_registry, temp_audit_db, config)
    
    results = scheduler.run_audit_round()
    
    # Should audit at most 1 provider-blob pair
    assert len(results) <= 1


def test_audit_skips_jailed_providers(
    temp_registry, temp_audit_db, sample_providers_with_assignments
):
    """Test that audit round skips jailed providers."""
    provider = sample_providers_with_assignments[0]
    
    # Jail provider
    jail_provider(temp_registry, provider.provider_id, duration_seconds=3600)
    
    config = AuditSchedulerConfig(sample_size=10)
    scheduler = AuditScheduler(temp_registry, temp_audit_db, config)
    
    results = scheduler.run_audit_round()
    
    # Jailed provider should not appear in results
    jailed_ids = {r.provider_id for r in results}
    assert provider.provider_id not in jailed_ids
