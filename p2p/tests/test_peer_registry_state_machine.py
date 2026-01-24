from p2p.node.peer_registry import PeerRegistry, PeerState


def test_peer_registry_state_machine() -> None:
    registry = PeerRegistry()
    session = registry.register("127.0.0.1:30333", "inbound")
    assert session.state == PeerState.DIALING

    registry.mark_tcp_connected(session.session_id)
    assert registry.snapshot()[0]["state"] == PeerState.TCP_CONNECTED.value

    registry.mark_identified(session.session_id, "a" * 64)
    assert registry.snapshot()[0]["state"] == PeerState.HANDSHAKING.value

    registry.mark_identity_validated(session.session_id, chain_id=1, genesis_hash="00" * 32)
    assert registry.snapshot()[0]["state"] == PeerState.CONNECTED.value
