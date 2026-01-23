from p2p.node.peer_registry import PeerRegistry
import time
import pytest


def test_peer_registry_deduplicates_and_enforces_limits():
    registry = PeerRegistry(max_inbound_per_ip=2, handshake_timeout_s=0.05)

    s1 = registry.register("1.1.1.1:1000", "inbound")
    s2 = registry.register("1.1.1.1:1001", "inbound")
    with pytest.raises(ValueError):
        registry.register("1.1.1.1:1002", "inbound")

    # Identify first peer
    dropped = registry.mark_identified(s1.session_id, "peer-A")
    assert dropped == []

    # New connection for same peer replaces the old one
    s3 = registry.register("2.2.2.2:2000", "outbound")
    dropped = registry.mark_identified(s3.session_id, "peer-A")
    assert dropped == []
    
    # FIX: peer_count() now requires identity_ok=True, not just peer_id
    # Before the fix, identified peers were counted even without identity validation
    # After the fix, only peers with identity_ok=True are counted as "connected"
    assert registry.peer_count() == 0  # No identity_ok set yet
    
    # Set identity_ok for both sessions to mark them as fully validated
    # Use mark_identity_validated to properly set identity_ok and state=CONNECTED
    registry.mark_identity_validated(s1.session_id, chain_id=1, genesis_hash="0" * 64)
    registry.mark_identity_validated(s3.session_id, chain_id=1, genesis_hash="0" * 64)
    
    # Now count should be 2 (inbound + outbound for peer-A)
    assert registry.peer_count() == 2

    # Unknown sessions time out and are purged
    time.sleep(0.1)
    expired = registry.purge_stale()
    assert s2.session_id in expired
    # Count remains 2 (only identified + identity_ok peers counted)
    assert registry.peer_count() == 2


def test_peer_registry_enforces_handshake_rate_limits():
    registry = PeerRegistry(
        max_inbound_per_ip=10,
        handshake_timeout_s=0.5,
        handshake_rate_limit_per_ip=2,
        handshake_rate_limit_per_netgroup=3,
        handshake_rate_window_s=0.05,
        handshake_rate_netgroup_v4_bits=24,
    )

    registry.register("198.51.100.1:1000", "inbound")
    registry.register("198.51.100.1:1001", "inbound")
    with pytest.raises(ValueError):
        registry.register("198.51.100.1:1002", "inbound")

    # Sleep longer than rate window to allow next connection
    time.sleep(0.10)  # Increased from 0.06 to 0.10 for reliability
    registry.register("198.51.100.1:1003", "inbound")

    registry.register("198.51.100.2:1004", "inbound")
    registry.register("198.51.100.3:1005", "inbound")
    with pytest.raises(ValueError):
        registry.register("198.51.100.4:1006", "inbound")
