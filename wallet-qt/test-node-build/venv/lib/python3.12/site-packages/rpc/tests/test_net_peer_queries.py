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
