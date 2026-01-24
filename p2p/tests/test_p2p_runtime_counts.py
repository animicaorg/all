from p2p.node.peer_registry import PeerRegistry
from p2p.node.runtime import ConnectionStage, P2PRuntime


def test_connected_count_only_ready_stage() -> None:
    registry = PeerRegistry()
    runtime = P2PRuntime(registry)

    session = registry.register("tcp://peer:30333", "outbound")
    registry.mark_tcp_connected(session.session_id)
    registry.mark_identified(session.session_id, "peer1")
    registry.mark_identity_validated(
        session.session_id,
        chain_id=1,
        genesis_hash="0x" + ("00" * 32),
    )

    counts = runtime.counts()
    assert counts.connected_total == 0
    assert counts.handshaking >= 1

    runtime.set_stage(session.session_id, ConnectionStage.PEER_READY)
    counts = runtime.counts()
    assert counts.connected_total == 1
    assert counts.handshaking == 0
