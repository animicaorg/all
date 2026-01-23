from p2p.node.p2p_service_legacy import P2PStatusSnapshot


def test_p2p_status_snapshot_init() -> None:
    snapshot = P2PStatusSnapshot(
        p2p_running=False,
        listen_addrs=[],
        advertised_addrs=[],
        advertise_host=None,
        advertise_port=None,
        external_ip=None,
        peers_total=0,
        peers_inbound=0,
        peers_outbound=0,
    )

    assert snapshot.peers_inbound == 0
