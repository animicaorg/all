"""
Tests for DA provider registry module.
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

import pytest

try:
    import cbor2
except ImportError:
    cbor2 = None

from da.provider.registry import (
    DEFAULT_UPTIME_SCORE,
    BlobAssignment,
    ProviderEntry,
    ProviderRegistry,
    create_provider_entry,
    create_provider_id,
    register_provider,
)


@pytest.fixture
def temp_db():
    """Create temporary database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_registry.db"
        yield db_path


def test_create_provider_id():
    """Test provider ID generation from pubkey."""
    pubkey = b"test_pubkey_32_bytes_long_enough!!"[:32]
    provider_id = create_provider_id(pubkey)
    
    assert len(provider_id) == 32
    assert isinstance(provider_id, bytes)
    
    # Same pubkey should produce same ID
    provider_id2 = create_provider_id(pubkey)
    assert provider_id == provider_id2


def test_create_provider_entry():
    """Test creating a provider entry."""
    pubkey = b"test_pubkey_32_bytes_long_enough!!"[:32]
    address = b"12345678901234567890"
    endpoint = "https://provider.example.com:9090"
    capacity = 1000000000  # 1GB
    
    entry = create_provider_entry(
        pubkey=pubkey,
        address=address,
        endpoint=endpoint,
        capacity_bytes=capacity,
        region_tags=["us-west", "ssd"],
    )
    
    assert entry.pubkey == pubkey
    assert entry.address == address
    assert entry.endpoint == endpoint
    assert entry.capacity_bytes_advertised == capacity
    assert entry.capacity_bytes_committed == 0
    assert entry.region_tags == ["us-west", "ssd"]
    assert entry.uptime_score == DEFAULT_UPTIME_SCORE
    assert entry.active is True


def test_provider_entry_validation():
    """Test provider entry validation."""
    pubkey = b"test_pubkey_32_bytes_long_enough!!"[:32]
    address = b"12345678901234567890"
    
    # Valid entry
    entry = create_provider_entry(
        pubkey=pubkey,
        address=address,
        endpoint="http://example.com",
        capacity_bytes=1000,
    )
    entry.validate()  # Should not raise
    
    # Invalid: committed > advertised
    entry.capacity_bytes_committed = 2000
    with pytest.raises(ValueError, match="committed capacity exceeds advertised"):
        entry.validate()
    
    # Invalid: uptime score out of range
    entry.capacity_bytes_committed = 500
    entry.uptime_score = 15000
    with pytest.raises(ValueError, match="uptime_score must be"):
        entry.validate()


@pytest.mark.skipif(cbor2 is None, reason="cbor2 not available")
def test_provider_entry_cbor_roundtrip():
    """Test CBOR serialization/deserialization."""
    pubkey = b"test_pubkey_32_bytes_long_enough!!"[:32]
    address = b"12345678901234567890"
    
    entry = create_provider_entry(
        pubkey=pubkey,
        address=address,
        endpoint="http://example.com",
        capacity_bytes=1000000,
        region_tags=["us-east"],
    )
    
    # To CBOR
    cbor_dict = entry.to_cbor_dict()
    assert isinstance(cbor_dict, dict)
    assert 0 in cbor_dict  # provider_id
    assert 1 in cbor_dict  # pubkey
    
    # From CBOR
    entry2 = ProviderEntry.from_cbor_dict(cbor_dict)
    assert entry2.pubkey == entry.pubkey
    assert entry2.address == entry.address
    assert entry2.endpoint == entry.endpoint
    assert entry2.capacity_bytes_advertised == entry.capacity_bytes_advertised
    assert entry2.region_tags == entry.region_tags


def test_provider_registry_register(temp_db):
    """Test registering a provider."""
    registry = ProviderRegistry(db_path=temp_db)
    
    pubkey = b"test_pubkey_32_bytes_long_enough!!"[:32]
    address = b"12345678901234567890"
    
    entry = register_provider(
        registry=registry,
        pubkey=pubkey,
        address=address,
        endpoint="http://example.com",
        capacity_bytes=1000000,
    )
    
    # Retrieve
    retrieved = registry.get_provider(entry.provider_id)
    assert retrieved is not None
    assert retrieved.pubkey == pubkey
    assert retrieved.address == address


def test_provider_registry_list(temp_db):
    """Test listing providers."""
    registry = ProviderRegistry(db_path=temp_db)
    
    # Register two providers
    for i in range(2):
        pubkey = f"test_pubkey_{i}_32_bytes_long!!".encode()[:32]
        address = f"address_{i}_20bytes!!".encode()[:20]
        register_provider(
            registry=registry,
            pubkey=pubkey,
            address=address,
            endpoint=f"http://provider{i}.com",
            capacity_bytes=1000000 * (i + 1),
        )
    
    # List all
    providers = registry.list_providers()
    assert len(providers) == 2
    
    # List active only
    active_providers = registry.list_providers(active_only=True)
    assert len(active_providers) == 2


def test_provider_registry_heartbeat(temp_db):
    """Test heartbeat update."""
    registry = ProviderRegistry(db_path=temp_db)
    
    pubkey = b"test_pubkey_32_bytes_long_enough!!"[:32]
    address = b"12345678901234567890"
    
    entry = register_provider(
        registry=registry,
        pubkey=pubkey,
        address=address,
        endpoint="http://example.com",
        capacity_bytes=1000000,
    )
    
    original_heartbeat = entry.last_heartbeat
    
    # Wait a bit and update
    time.sleep(0.1)
    new_time = int(time.time())
    registry.update_heartbeat(entry.provider_id, new_time)
    
    # Verify update
    retrieved = registry.get_provider(entry.provider_id)
    assert retrieved.last_heartbeat == new_time
    assert retrieved.last_heartbeat >= original_heartbeat


def test_blob_assignment(temp_db):
    """Test blob assignment."""
    registry = ProviderRegistry(db_path=temp_db)
    
    pubkey = b"test_pubkey_32_bytes_long_enough!!"[:32]
    address = b"12345678901234567890"
    
    entry = register_provider(
        registry=registry,
        pubkey=pubkey,
        address=address,
        endpoint="http://example.com",
        capacity_bytes=1000000,
    )
    
    # Create assignment
    blob_commitment = b"blob_commitment_32_bytes_long!!"[:32]
    assignment = BlobAssignment(
        blob_commitment=blob_commitment,
        provider_id=entry.provider_id,
        assigned_at=int(time.time()),
        replicas=3,
        blob_size=4096,
    )
    
    registry.add_assignment(assignment)
    
    # Retrieve assignments
    assignments = registry.get_assignments_for_provider(entry.provider_id)
    assert len(assignments) == 1
    assert assignments[0].blob_commitment == blob_commitment
    assert assignments[0].replicas == 3


def test_total_capacity(temp_db):
    """Test total capacity calculation."""
    registry = ProviderRegistry(db_path=temp_db)
    
    # Register providers with different capacities
    capacities = [1000000, 2000000, 3000000]
    for i, capacity in enumerate(capacities):
        pubkey = f"test_pubkey_{i}_32_bytes_long!!".encode()[:32]
        address = f"address_{i}_20bytes!!".encode()[:20]
        register_provider(
            registry=registry,
            pubkey=pubkey,
            address=address,
            endpoint=f"http://provider{i}.com",
            capacity_bytes=capacity,
        )
    
    total_adv, total_comm = registry.get_total_capacity()
    assert total_adv == sum(capacities)
    assert total_comm == 0  # No committed capacity yet
