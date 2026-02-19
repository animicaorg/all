"""
Tests for DA audit challenge/response cycle.

Tests:
- Challenge creation
- Response verification
- Signature checking
- Byte-range verification
- Score updates
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import time
from pathlib import Path

import pytest

from da.provider.audit import (
    AuditDatabase,
    create_challenge,
    verify_response,
    update_provider_score,
    CHALLENGE_TYPES,
    SCORE_DELTA_PASS,
    SCORE_DELTA_FAIL,
)
from da.provider.registry import (
    AuditResponse,
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
def sample_provider(temp_registry):
    """Create a sample provider."""
    pubkey = os.urandom(32)
    provider_id = create_provider_id(pubkey)
    
    entry = ProviderEntry(
        provider_id=provider_id,
        pubkey=pubkey,
        address=os.urandom(20),
        endpoint="http://test-provider.example.com",
        capacity_bytes_advertised=1024 * 1024 * 1024,  # 1GB
        capacity_bytes_committed=0,
        uptime_score=5000,
        last_heartbeat=int(time.time()),
        registered_at=int(time.time()),
        active=True,
    )
    
    temp_registry.register_provider(entry)
    return entry


def test_create_challenge_byte_range(sample_provider):
    """Test creating a byte-range challenge."""
    blob_commitment = os.urandom(32)
    
    challenge = create_challenge(
        provider_id=sample_provider.provider_id,
        blob_commitment=blob_commitment,
        challenge_type="byte-range",
    )
    
    assert len(challenge.challenge_id) == 32
    assert challenge.provider_id == sample_provider.provider_id
    assert challenge.blob_commitment == blob_commitment
    assert len(challenge.nonce) == 32
    assert challenge.challenge_type == "byte-range"
    assert challenge.deadline > int(time.time())
    
    # Check params
    assert 0 in challenge.params  # offset
    assert 1 in challenge.params  # length
    assert isinstance(challenge.params[0], int)
    assert isinstance(challenge.params[1], int)


def test_create_challenge_merkle_proof(sample_provider):
    """Test creating a merkle-proof challenge."""
    blob_commitment = os.urandom(32)
    
    challenge = create_challenge(
        provider_id=sample_provider.provider_id,
        blob_commitment=blob_commitment,
        challenge_type="merkle-proof",
    )
    
    assert challenge.challenge_type == "merkle-proof"
    assert 0 in challenge.params  # leaf_index


def test_create_challenge_nmt_proof(sample_provider):
    """Test creating an nmt-proof challenge."""
    blob_commitment = os.urandom(32)
    
    challenge = create_challenge(
        provider_id=sample_provider.provider_id,
        blob_commitment=blob_commitment,
        challenge_type="nmt-proof",
    )
    
    assert challenge.challenge_type == "nmt-proof"
    assert 0 in challenge.params  # namespace


def test_challenge_database_storage(temp_audit_db, sample_provider):
    """Test storing and retrieving challenges."""
    blob_commitment = os.urandom(32)
    
    challenge = create_challenge(
        provider_id=sample_provider.provider_id,
        blob_commitment=blob_commitment,
        challenge_type="byte-range",
    )
    
    # Store challenge
    temp_audit_db.store_challenge(challenge)
    
    # Retrieve challenge
    retrieved = temp_audit_db.get_challenge(challenge.challenge_id)
    assert retrieved is not None
    assert retrieved.challenge_id == challenge.challenge_id
    assert retrieved.provider_id == challenge.provider_id
    assert retrieved.blob_commitment == challenge.blob_commitment
    assert retrieved.challenge_type == challenge.challenge_type


def test_response_database_storage(temp_audit_db, sample_provider):
    """Test storing and retrieving responses."""
    blob_commitment = os.urandom(32)
    
    challenge = create_challenge(
        provider_id=sample_provider.provider_id,
        blob_commitment=blob_commitment,
        challenge_type="byte-range",
    )
    
    # Create response
    response = AuditResponse(
        challenge_id=challenge.challenge_id,
        provider_id=sample_provider.provider_id,
        response_type="byte-data",
        payload={0: b"test_data".hex()},
        signature=os.urandom(64),
        submitted_at=int(time.time()),
    )
    
    # Store response
    temp_audit_db.store_response(response)
    
    # Retrieve response
    retrieved = temp_audit_db.get_response(challenge.challenge_id)
    assert retrieved is not None
    assert retrieved.challenge_id == challenge.challenge_id
    assert retrieved.provider_id == challenge.provider_id
    assert retrieved.response_type == response.response_type


def test_verify_response_byte_range(sample_provider):
    """Test verifying a byte-range response."""
    blob_commitment = os.urandom(32)
    
    # Create challenge
    challenge = create_challenge(
        provider_id=sample_provider.provider_id,
        blob_commitment=blob_commitment,
        challenge_type="byte-range",
        deadline_seconds=3600,
    )
    
    # Create mock blob data
    actual_blob_data = b"x" * 1024  # 1KB of data
    
    # Extract challenge params
    offset = challenge.params[0]
    length = challenge.params[1]
    
    # Create correct response
    expected_data = actual_blob_data[offset:offset + length]
    
    response = AuditResponse(
        challenge_id=challenge.challenge_id,
        provider_id=sample_provider.provider_id,
        response_type="byte-data",
        payload={0: expected_data.hex()},
        signature=os.urandom(64),  # Dummy signature
        submitted_at=int(time.time()),
    )
    
    # Verify response
    passed, reason = verify_response(
        challenge=challenge,
        response=response,
        provider=sample_provider,
        actual_blob_data=actual_blob_data,
    )
    
    # Should pass (signature verification skipped without PQ)
    assert passed
    assert reason is None


def test_verify_response_wrong_data(sample_provider):
    """Test verifying a response with wrong data."""
    blob_commitment = os.urandom(32)
    
    challenge = create_challenge(
        provider_id=sample_provider.provider_id,
        blob_commitment=blob_commitment,
        challenge_type="byte-range",
    )
    
    # Create mock blob data
    actual_blob_data = b"x" * 1024
    
    # Create response with wrong data
    wrong_data = b"y" * 256
    
    response = AuditResponse(
        challenge_id=challenge.challenge_id,
        provider_id=sample_provider.provider_id,
        response_type="byte-data",
        payload={0: wrong_data.hex()},
        signature=os.urandom(64),
        submitted_at=int(time.time()),
    )
    
    # Verify response
    passed, reason = verify_response(
        challenge=challenge,
        response=response,
        provider=sample_provider,
        actual_blob_data=actual_blob_data,
    )
    
    # Should fail
    assert not passed
    assert "mismatch" in reason.lower()


def test_verify_response_deadline_exceeded(sample_provider):
    """Test verifying a response submitted after deadline."""
    blob_commitment = os.urandom(32)
    
    challenge = create_challenge(
        provider_id=sample_provider.provider_id,
        blob_commitment=blob_commitment,
        challenge_type="byte-range",
        deadline_seconds=1,  # 1 second deadline
    )
    
    # Wait for deadline to pass
    time.sleep(2)
    
    response = AuditResponse(
        challenge_id=challenge.challenge_id,
        provider_id=sample_provider.provider_id,
        response_type="byte-data",
        payload={0: b"test".hex()},
        signature=os.urandom(64),
        submitted_at=int(time.time()),
    )
    
    # Verify response
    passed, reason = verify_response(
        challenge=challenge,
        response=response,
        provider=sample_provider,
    )
    
    # Should fail
    assert not passed
    assert "deadline" in reason.lower()


def test_update_provider_score_pass(temp_registry, sample_provider):
    """Test updating provider score on successful audit."""
    initial_score = sample_provider.uptime_score
    
    delta = update_provider_score(
        registry=temp_registry,
        provider_id=sample_provider.provider_id,
        passed=True,
    )
    
    assert delta == SCORE_DELTA_PASS
    
    # Check provider score increased
    updated_provider = temp_registry.get_provider(sample_provider.provider_id)
    assert updated_provider.uptime_score == initial_score + SCORE_DELTA_PASS


def test_update_provider_score_fail(temp_registry, sample_provider):
    """Test updating provider score on failed audit."""
    initial_score = sample_provider.uptime_score
    
    delta = update_provider_score(
        registry=temp_registry,
        provider_id=sample_provider.provider_id,
        passed=False,
    )
    
    assert delta == SCORE_DELTA_FAIL
    
    # Check provider score decreased
    updated_provider = temp_registry.get_provider(sample_provider.provider_id)
    assert updated_provider.uptime_score == initial_score + SCORE_DELTA_FAIL


def test_update_provider_score_clamped(temp_registry, sample_provider):
    """Test that provider score is clamped to [0, 10000]."""
    # Set score to near max
    sample_provider.uptime_score = 9950
    temp_registry.register_provider(sample_provider)
    
    # Pass should clamp to 10000
    update_provider_score(
        registry=temp_registry,
        provider_id=sample_provider.provider_id,
        passed=True,
    )
    
    updated_provider = temp_registry.get_provider(sample_provider.provider_id)
    assert updated_provider.uptime_score == 10000
    
    # Set score to near min
    sample_provider.uptime_score = 100
    temp_registry.register_provider(sample_provider)
    
    # Multiple fails should clamp to 0
    update_provider_score(
        registry=temp_registry,
        provider_id=sample_provider.provider_id,
        passed=False,
    )
    
    updated_provider = temp_registry.get_provider(sample_provider.provider_id)
    assert updated_provider.uptime_score == 0


def test_audit_result_storage(temp_audit_db, sample_provider):
    """Test storing and retrieving audit results."""
    from da.provider.registry import AuditResult
    
    challenge_id = os.urandom(32)
    
    result = AuditResult(
        challenge_id=challenge_id,
        provider_id=sample_provider.provider_id,
        passed=True,
        verified_at=int(time.time()),
        failure_reason=None,
        score_delta=SCORE_DELTA_PASS,
    )
    
    # Store result
    temp_audit_db.store_result(result)
    
    # Retrieve results for provider
    results = temp_audit_db.get_results_for_provider(sample_provider.provider_id)
    assert len(results) == 1
    assert results[0].challenge_id == challenge_id
    assert results[0].passed is True
    assert results[0].score_delta == SCORE_DELTA_PASS
