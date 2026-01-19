"""
Test that P2P handshake properly consumes network manifest.

Validates that:
1. IdentifyService uses manifest.p2p_network_id
2. IdentifyService uses manifest.pinned_genesis_hash_hex
3. Handshake validation detects network mismatches
4. Handshake validation detects genesis mismatches
"""

import pytest

from core.network_manifest import MAINNET_MANIFEST, TESTNET_MANIFEST
from p2p.peer.identify import IdentifyError, IdentifyService, validate_handshake


def test_identify_service_uses_manifest_network_id():
    """Test that IdentifyService uses network_id from manifest."""
    
    class MockConnMgr:
        pass
    
    # Create service with manifest values
    service = IdentifyService(
        connmgr=MockConnMgr(),
        peer_id=b"test_peer_id_12345678",
        network_id=MAINNET_MANIFEST.p2p_network_id,
        genesis_hash=MAINNET_MANIFEST.pinned_genesis_hash_hex,
    )
    
    # Verify service uses the manifest values
    assert service.network_id == "animica:0"
    assert service.genesis_hash == MAINNET_MANIFEST.pinned_genesis_hash_hex
    
    # Verify describe_local includes these values
    local_info = service.describe_local()
    assert local_info["network_id"] == "animica:0"
    assert local_info["head_hash"] == MAINNET_MANIFEST.pinned_genesis_hash_hex


def test_handshake_validation_accepts_matching_network():
    """Test that handshake validation accepts matching network."""
    
    peer_response = {
        "network_id": "animica:0",
        "head_hash": MAINNET_MANIFEST.pinned_genesis_hash_hex,
        "height": 100,
        "peer_id": "test_peer",
    }
    
    # Should not raise
    result = validate_handshake(
        local_network_id="animica:0",
        local_genesis_hash=MAINNET_MANIFEST.pinned_genesis_hash_hex,
        peer_response=peer_response,
        strict=True,
    )
    assert result is True


def test_handshake_validation_rejects_network_mismatch():
    """Test that handshake validation rejects network mismatch."""
    
    peer_response = {
        "network_id": "animica:2",  # Testnet
        "head_hash": TESTNET_MANIFEST.pinned_genesis_hash_hex,
        "height": 100,
        "peer_id": "test_peer",
    }
    
    # Should raise IdentifyError
    with pytest.raises(IdentifyError) as exc_info:
        validate_handshake(
            local_network_id="animica:0",  # Mainnet
            local_genesis_hash=MAINNET_MANIFEST.pinned_genesis_hash_hex,
            peer_response=peer_response,
            strict=True,
        )
    
    assert "network mismatch" in str(exc_info.value).lower()
    assert "animica:0" in str(exc_info.value)
    assert "animica:2" in str(exc_info.value)


def test_handshake_validation_rejects_genesis_mismatch():
    """Test that handshake validation rejects genesis mismatch."""
    
    peer_response = {
        "network_id": "animica:0",
        "head_hash": "0x" + "00" * 32,  # Wrong genesis hash
        "height": 100,
        "peer_id": "test_peer",
    }
    
    # Should raise IdentifyError
    with pytest.raises(IdentifyError) as exc_info:
        validate_handshake(
            local_network_id="animica:0",
            local_genesis_hash=MAINNET_MANIFEST.pinned_genesis_hash_hex,
            peer_response=peer_response,
            strict=True,
        )
    
    assert "genesis mismatch" in str(exc_info.value).lower()


def test_handshake_validation_with_strict_false():
    """Test that handshake validation returns False when strict=False."""
    
    peer_response = {
        "network_id": "animica:2",  # Testnet
        "head_hash": TESTNET_MANIFEST.pinned_genesis_hash_hex,
        "height": 100,
        "peer_id": "test_peer",
    }
    
    # Should return False instead of raising
    result = validate_handshake(
        local_network_id="animica:0",  # Mainnet
        local_genesis_hash=MAINNET_MANIFEST.pinned_genesis_hash_hex,
        peer_response=peer_response,
        strict=False,
    )
    assert result is False


def test_handshake_validation_allows_missing_fields():
    """Test that handshake validation allows missing network_id/genesis."""
    
    # Peer doesn't send network_id or genesis
    peer_response = {
        "height": 100,
        "peer_id": "test_peer",
    }
    
    # Should pass (no comparison possible)
    result = validate_handshake(
        local_network_id="animica:0",
        local_genesis_hash=MAINNET_MANIFEST.pinned_genesis_hash_hex,
        peer_response=peer_response,
        strict=True,
    )
    assert result is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
