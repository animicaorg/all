"""
Test handshake identity validation functionality.

Verifies that HandshakeManager properly validates chain_id and genesis_hash
during identity exchange.
"""

import pytest

from p2p.node.handshake import HandshakeManager
from p2p.node.peer_registry import PeerRegistry, PeerState


def test_valid_identity():
    """Verify that identity validation succeeds with matching chain_id and genesis_hash."""
    registry = PeerRegistry()
    manager = HandshakeManager(
        registry,
        chain_id=1337,
        genesis_hash="abc123" * 10 + "ab",  # 62 chars = 31 bytes hex
    )
    
    # Start handshake
    session_id = manager.start_handshake("tcp://peer1:8000", "outbound")
    
    # Complete Hello exchange
    manager.on_hello_received(
        session_id,
        peer_id="peer1" * 8,
        version="2",
        agent="test-peer/1.0",
    )
    
    # Validate identity with matching credentials
    success, error = manager.on_identity_received(
        session_id,
        chain_id=1337,
        genesis_hash="abc123" * 10 + "ab",
    )
    
    # Verify success
    assert success
    assert error is None
    
    # Verify state transitioned to CONNECTED
    session = registry._sessions.get(session_id)
    assert session is not None
    assert session.state == PeerState.CONNECTED
    assert session.identity_ok is True
    assert session.remote_chain_id == 1337


def test_chain_id_mismatch():
    """Verify that identity validation fails with mismatched chain_id."""
    registry = PeerRegistry()
    manager = HandshakeManager(
        registry,
        chain_id=1337,
        genesis_hash="abc123" * 10 + "ab",
    )
    
    # Start handshake
    session_id = manager.start_handshake("tcp://peer1:8000", "outbound")
    
    # Complete Hello exchange
    manager.on_hello_received(
        session_id,
        peer_id="peer1" * 8,
        version="2",
        agent="test-peer/1.0",
    )
    
    # Validate identity with mismatched chain_id
    success, error = manager.on_identity_received(
        session_id,
        chain_id=9999,  # Wrong chain_id
        genesis_hash="abc123" * 10 + "ab",
    )
    
    # Verify failure
    assert not success
    assert error == "chain_id_mismatch"
    
    # Verify state transitioned to FAILED
    session = registry._sessions.get(session_id)
    assert session is not None
    assert session.state == PeerState.FAILED
    assert session.identity_ok is False
    assert session.last_error == "chain_id_mismatch"
    assert session.remote_chain_id == 9999


def test_genesis_hash_mismatch():
    """Verify that identity validation fails with mismatched genesis_hash."""
    registry = PeerRegistry()
    manager = HandshakeManager(
        registry,
        chain_id=1337,
        genesis_hash="abc123" * 10 + "ab",
    )
    
    # Start handshake
    session_id = manager.start_handshake("tcp://peer1:8000", "outbound")
    
    # Complete Hello exchange
    manager.on_hello_received(
        session_id,
        peer_id="peer1" * 8,
        version="2",
        agent="test-peer/1.0",
    )
    
    # Validate identity with mismatched genesis_hash
    success, error = manager.on_identity_received(
        session_id,
        chain_id=1337,
        genesis_hash="def456" * 10 + "de",  # Wrong genesis_hash
    )
    
    # Verify failure
    assert not success
    assert error == "genesis_hash_mismatch"
    
    # Verify state transitioned to FAILED
    session = registry._sessions.get(session_id)
    assert session is not None
    assert session.state == PeerState.FAILED
    assert session.identity_ok is False
    assert session.last_error == "genesis_hash_mismatch"


def test_genesis_hash_0x_prefix_consistency():
    """
    Verify that genesis_hash validation works correctly when HandshakeManager
    is initialized with 0x-prefixed hash and peer provides 0x-prefixed hash.
    
    This tests the fix for the bug where HandshakeManager was initialized with
    genesis_hash_bytes.hex() (no prefix) but _canon_hash0x() added prefix when
    validating peer identity, causing all validations to fail.
    
    Bug: HandshakeManager("abc...") vs peer("0xabc...") → FAIL
    Fix: HandshakeManager("0xabc...") vs peer("0xabc...") → PASS
    """
    registry = PeerRegistry()
    
    # Real mainnet genesis hash
    genesis_hash = "0xcf08020c87d8c294e09e5a872d7a5a2f3ceb9b8576ba0cdbfd1daef6832cbbfb"
    
    # Initialize HandshakeManager with 0x-prefixed format (the fix)
    manager = HandshakeManager(
        registry,
        chain_id=1,
        genesis_hash=genesis_hash,  # With 0x prefix
    )
    
    # Start handshake
    session_id = manager.start_handshake("tcp://peer1:30333", "outbound")
    
    # Complete Hello exchange
    manager.on_hello_received(
        session_id,
        peer_id="peer1" * 8,
        version="2",
        agent="animica/1.0",
    )
    
    # Peer sends identity with 0x-prefixed genesis hash (from _canon_hash0x)
    success, error = manager.on_identity_received(
        session_id,
        chain_id=1,
        genesis_hash=genesis_hash,  # Peer also has 0x prefix
    )
    
    # Verify success - formats match
    assert success, f"Identity validation should succeed with matching 0x-prefixed hashes, but got error: {error}"
    assert error is None
    
    # Verify state transitioned to CONNECTED
    session = registry._sessions.get(session_id)
    assert session is not None
    assert session.state == PeerState.CONNECTED
    assert session.identity_ok is True


def test_genesis_hash_format_mismatch_bug():
    """
    Verify that the OLD buggy behavior (no 0x prefix in local, 0x prefix from peer) fails.
    
    This documents the bug that was fixed: when HandshakeManager was initialized
    with genesis_hash_bytes.hex() (no prefix) but peer provided _canon_hash0x()
    result (with prefix), validation would always fail.
    """
    registry = PeerRegistry()
    
    # Real mainnet genesis hash
    genesis_hash_bytes = bytes.fromhex("cf08020c87d8c294e09e5a872d7a5a2f3ceb9b8576ba0cdbfd1daef6832cbbfb")
    
    # OLD BUGGY WAY: Initialize HandshakeManager WITHOUT 0x prefix
    manager = HandshakeManager(
        registry,
        chain_id=1,
        genesis_hash=genesis_hash_bytes.hex(),  # No 0x prefix (the bug)
    )
    
    # Start handshake
    session_id = manager.start_handshake("tcp://peer1:30333", "outbound")
    
    # Complete Hello exchange
    manager.on_hello_received(
        session_id,
        peer_id="peer1" * 8,
        version="2",
        agent="animica/1.0",
    )
    
    # Peer sends identity with 0x-prefixed genesis hash (from _canon_hash0x)
    peer_genesis = "0x" + genesis_hash_bytes.hex()  # With 0x prefix
    
    success, error = manager.on_identity_received(
        session_id,
        chain_id=1,
        genesis_hash=peer_genesis,
    )
    
    # Verify failure - formats don't match (this was the bug)
    assert not success, "Identity validation should fail when formats don't match (local no 0x, peer with 0x)"
    assert error == "genesis_hash_mismatch"
    
    # Verify state transitioned to FAILED
    session = registry._sessions.get(session_id)
    assert session is not None
    assert session.state == PeerState.FAILED
    assert session.identity_ok is False


def test_case_insensitive_genesis_hash():
    """Verify that genesis_hash comparison is case-insensitive."""
    registry = PeerRegistry()
    manager = HandshakeManager(
        registry,
        chain_id=1337,
        genesis_hash="ABCDEF123456" * 5 + "AB",  # Uppercase
    )
    
    # Start handshake
    session_id = manager.start_handshake("tcp://peer1:8000", "outbound")
    
    # Complete Hello exchange
    manager.on_hello_received(
        session_id,
        peer_id="peer1" * 8,
        version="2",
        agent="test-peer/1.0",
    )
    
    # Validate identity with lowercase genesis_hash
    success, error = manager.on_identity_received(
        session_id,
        chain_id=1337,
        genesis_hash="abcdef123456" * 5 + "ab",  # Lowercase
    )
    
    # Verify success (case-insensitive match)
    assert success
    assert error is None
    
    # Verify state transitioned to CONNECTED
    session = registry._sessions.get(session_id)
    assert session is not None
    assert session.state == PeerState.CONNECTED


def test_empty_genesis_hash_skips_validation():
    """Verify that empty local genesis_hash skips genesis validation."""
    registry = PeerRegistry()
    manager = HandshakeManager(
        registry,
        chain_id=1337,
        genesis_hash="",  # Empty - skip genesis validation
    )
    
    # Start handshake
    session_id = manager.start_handshake("tcp://peer1:8000", "outbound")
    
    # Complete Hello exchange
    manager.on_hello_received(
        session_id,
        peer_id="peer1" * 8,
        version="2",
        agent="test-peer/1.0",
    )
    
    # Validate identity - any genesis_hash should work
    success, error = manager.on_identity_received(
        session_id,
        chain_id=1337,
        genesis_hash="any_hash_works" * 4,
    )
    
    # Verify success (genesis validation skipped)
    assert success
    assert error is None
    
    # Verify state transitioned to CONNECTED
    session = registry._sessions.get(session_id)
    assert session is not None
    assert session.state == PeerState.CONNECTED


def test_multiple_validation_failures():
    """Verify that multiple peers can fail validation independently."""
    registry = PeerRegistry()
    manager = HandshakeManager(
        registry,
        chain_id=1337,
        genesis_hash="correct" * 10 + "ab",
    )
    
    # Start two handshakes
    session1 = manager.start_handshake("tcp://peer1:8000", "outbound")
    session2 = manager.start_handshake("tcp://peer2:8000", "outbound")
    
    # Complete Hello exchange for both
    manager.on_hello_received(session1, "peer1" * 8, "2", "test-peer/1.0")
    manager.on_hello_received(session2, "peer2" * 8, "2", "test-peer/1.0")
    
    # Validate identities - both fail with different reasons
    success1, error1 = manager.on_identity_received(
        session1,
        chain_id=9999,  # Wrong chain_id
        genesis_hash="correct" * 10 + "ab",
    )
    
    success2, error2 = manager.on_identity_received(
        session2,
        chain_id=1337,
        genesis_hash="wrong" * 10 + "ab",  # Wrong genesis_hash
    )
    
    # Verify both failed
    assert not success1
    assert error1 == "chain_id_mismatch"
    assert not success2
    assert error2 == "genesis_hash_mismatch"
    
    # Verify both in FAILED state
    assert registry._sessions[session1].state == PeerState.FAILED
    assert registry._sessions[session2].state == PeerState.FAILED


def test_validation_before_hello():
    """Verify that identity validation handles sessions without Hello gracefully."""
    registry = PeerRegistry()
    manager = HandshakeManager(
        registry,
        chain_id=1337,
        genesis_hash="test" * 16,
    )
    
    # Start handshake but don't send Hello
    session_id = manager.start_handshake("tcp://peer1:8000", "outbound")
    
    # Try to validate identity without Hello exchange
    success, error = manager.on_identity_received(
        session_id,
        chain_id=1337,
        genesis_hash="test" * 16,
    )
    
    # Should still succeed (no protocol requirement to check Hello first)
    assert success
    assert error is None


def test_validation_stores_remote_credentials():
    """Verify that remote credentials are stored in registry after validation."""
    registry = PeerRegistry()
    manager = HandshakeManager(
        registry,
        chain_id=1337,
        genesis_hash="local" * 12 + "ab",
    )
    
    # Start handshake
    session_id = manager.start_handshake("tcp://peer1:8000", "outbound")
    manager.on_hello_received(session_id, "peer1" * 8, "2", "test-peer/1.0")
    
    # Validate identity
    success, _ = manager.on_identity_received(
        session_id,
        chain_id=1337,
        genesis_hash="local" * 12 + "ab",
    )
    
    assert success
    
    # Verify remote credentials stored
    session = registry._sessions.get(session_id)
    assert session is not None
    assert session.remote_chain_id == 1337
    assert session.remote_genesis_hash == "local" * 12 + "ab"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
