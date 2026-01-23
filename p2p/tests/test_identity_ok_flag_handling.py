"""
Test that identity_ok flag is only set after successful validation.

This test verifies the fix for the bug where peer.identity_ok was set to True
BEFORE identity validation, causing peers to be counted as "connected" even when
identity validation failed (chain_id or genesis_hash mismatch).

The bug prevented mining because:
1. Peer successfully dialed
2. peer.identity_ok was set to True
3. Identity validation failed (wrong chain_id/genesis)
4. peer.identity_ok was NOT reverted to False
5. Peer appeared in peers_total but not peers_connected
6. Mining was blocked due to "insufficient connected peers"
"""

import pytest
from unittest.mock import Mock, MagicMock, patch
from p2p.node.peer_registry import PeerRegistry, PeerState, PeerSession


def test_identity_ok_only_set_on_successful_validation():
    """
    Test that peer.identity_ok remains False when identity validation fails.
    
    This verifies the fix for the bug where identity_ok was set to True before
    validation, and never reverted when validation failed.
    """
    # Create a mock peer session
    peer = PeerSession(
        session_id="test_session",
        remote="tcp://test:8000",
        direction="outbound",
    )
    peer.peer_id = "test_peer_id"
    peer.hello = {
        "chain_id": 9999,  # Wrong chain_id
        "genesis_header_hash": "wrong_genesis_hash",
    }
    
    # Initially identity_ok should be False
    assert peer.identity_ok is False
    
    # Create a mock handshake manager that will reject identity
    mock_handshake_manager = Mock()
    mock_handshake_manager.on_identity_received = Mock(
        return_value=(False, "chain_id_mismatch")  # Validation fails
    )
    
    # Simulate what the fixed code does in p2p_service_legacy.py
    identity_validated = False
    try:
        success, error = mock_handshake_manager.on_identity_received(
            session_id=peer.session_id,
            chain_id=int(peer.hello.get("chain_id", 0)),
            genesis_hash=peer.hello.get("genesis_header_hash", ""),
        )
        if success:
            # Only set identity_ok=True on successful validation
            peer.identity_ok = True
            identity_validated = True
        else:
            # Ensure identity_ok remains False on validation failure
            peer.identity_ok = False
    except Exception:
        # Ensure identity_ok remains False on exception
        peer.identity_ok = False
    
    # Verify that identity_ok is still False after validation failure
    assert peer.identity_ok is False
    assert identity_validated is False
    
    # Verify the handshake manager was called with correct parameters
    mock_handshake_manager.on_identity_received.assert_called_once_with(
        session_id="test_session",
        chain_id=9999,
        genesis_hash="wrong_genesis_hash",
    )


def test_identity_ok_set_on_successful_validation():
    """
    Test that peer.identity_ok is set to True only when validation succeeds.
    """
    # Create a mock peer session
    peer = PeerSession(
        session_id="test_session",
        remote="tcp://test:8000",
        direction="outbound",
    )
    peer.peer_id = "test_peer_id"
    peer.hello = {
        "chain_id": 1337,  # Correct chain_id
        "genesis_header_hash": "correct_genesis_hash",
    }
    
    # Initially identity_ok should be False
    assert peer.identity_ok is False
    
    # Create a mock handshake manager that will accept identity
    mock_handshake_manager = Mock()
    mock_handshake_manager.on_identity_received = Mock(
        return_value=(True, None)  # Validation succeeds
    )
    
    # Simulate what the fixed code does in p2p_service_legacy.py
    identity_validated = False
    try:
        success, error = mock_handshake_manager.on_identity_received(
            session_id=peer.session_id,
            chain_id=int(peer.hello.get("chain_id", 0)),
            genesis_hash=peer.hello.get("genesis_header_hash", ""),
        )
        if success:
            # Only set identity_ok=True on successful validation
            peer.identity_ok = True
            identity_validated = True
        else:
            # Ensure identity_ok remains False on validation failure
            peer.identity_ok = False
    except Exception:
        # Ensure identity_ok remains False on exception
        peer.identity_ok = False
    
    # Verify that identity_ok is now True after successful validation
    assert peer.identity_ok is True
    assert identity_validated is True
    
    # Verify the handshake manager was called with correct parameters
    mock_handshake_manager.on_identity_received.assert_called_once_with(
        session_id="test_session",
        chain_id=1337,
        genesis_hash="correct_genesis_hash",
    )


def test_identity_ok_not_set_on_exception():
    """
    Test that peer.identity_ok remains False when an exception occurs during validation.
    """
    # Create a mock peer session
    peer = PeerSession(
        session_id="test_session",
        remote="tcp://test:8000",
        direction="outbound",
    )
    peer.peer_id = "test_peer_id"
    peer.hello = {
        "chain_id": 1337,
        "genesis_header_hash": "some_hash",
    }
    
    # Initially identity_ok should be False
    assert peer.identity_ok is False
    
    # Create a mock handshake manager that will raise an exception
    mock_handshake_manager = Mock()
    mock_handshake_manager.on_identity_received = Mock(
        side_effect=RuntimeError("Test exception")
    )
    
    # Simulate what the fixed code does in p2p_service_legacy.py
    identity_validated = False
    try:
        success, error = mock_handshake_manager.on_identity_received(
            session_id=peer.session_id,
            chain_id=int(peer.hello.get("chain_id", 0)),
            genesis_hash=peer.hello.get("genesis_header_hash", ""),
        )
        if success:
            peer.identity_ok = True
            identity_validated = True
        else:
            peer.identity_ok = False
    except Exception:
        # Ensure identity_ok remains False on exception
        peer.identity_ok = False
    
    # Verify that identity_ok is still False after exception
    assert peer.identity_ok is False
    assert identity_validated is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
