from rpc.tests import new_test_client, rpc_call
import rpc.methods.net as net


def test_net_peer_count_returns_error_instead_of_disconnect(monkeypatch):
    def boom():
        raise RuntimeError("registry exploded")

    monkeypatch.setattr(net, "_active_peer_snapshot", boom)
    client, _, _ = new_test_client()
    res = rpc_call(client, "net.peerCount", expect_error=True)
    assert res["error"]["code"] == -32603
    assert "registry exploded" in res["error"]["message"]


def test_net_peers_uses_snapshot(monkeypatch):
    monkeypatch.setattr(net, "_active_peer_snapshot", lambda: [{"peer_id": "p1"}])
    client, _, _ = new_test_client()
    res = rpc_call(client, "net.peers")
    assert res["result"] == [{"peer_id": "p1"}]


def test_net_peer_count_prefers_connected(monkeypatch):
    import p2p

    class _Snap:
        def to_dict(self):
            return {"peers_connected": 0, "peers_total": 2}

    class _Svc:
        def status_snapshot(self):
            return _Snap()

        def bootstrap_peer_bonus(self):
            return 0

    monkeypatch.setattr(p2p, "get_service", lambda: _Svc())
    monkeypatch.setattr(net, "_active_peer_snapshot", lambda: [{"peer_id": "p1"}])
    client, _, _ = new_test_client()
    res = rpc_call(client, "net.peerCount")
    assert res["result"] == 0
