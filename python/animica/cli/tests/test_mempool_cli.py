from __future__ import annotations

import typer.testing

from animica.cli.main import app

runner = typer.testing.CliRunner()


def test_mempool_list_auto_imports_peer_transactions_and_displays_them(monkeypatch) -> None:
    calls: list[tuple[str, tuple]] = []

    def fake_resolve_rpc_url(url):
        return "http://test/rpc"

    def fake_call_rpc(method, params, rpc_url=None, no_cache=True):
        calls.append((method, tuple(params or [])))
        if method == "mempool.getPending":
            # First call sees empty local mempool, second call is refresh after import.
            if sum(1 for m, _ in calls if m == "mempool.getPending") == 1:
                return []
            return [
                {
                    "hash": "0xabc123",
                    "from": "0xsender",
                    "nonce": 7,
                    "fee": 1,
                    "size": 120,
                    "status": "pending",
                }
            ]
        if method == "chain.getChainIdentity":
            return {"chainId": 1, "genesisHash": "0xdeadbeef"}
        if method == "chain.getHead":
            return {"height": 42, "hash": "0xhead"}
        if method == "p2p.getStatus":
            return {"peer_id": "0xnode"}
        if method == "p2p.debugStatus":
            return {
                "peers": [
                    {
                        "peer_id": "0xpeer",
                        "conn_id": "0xconn",
                        "txrelay_known_txids": 3,
                        "txrelay_known_txids_sample": ["0xabc123"],
                    }
                ]
            }
        if method == "mempool.getInfo":
            return {"mempool_id": "mp1", "mempool_path": "/tmp/pending.jsonl"}
        if method == "p2p.importPeerKnownTxs":
            return {"requested": 1}
        raise AssertionError(f"Unexpected RPC method: {method}")

    def fake_sleep(duration):
        # Skip actual sleep in tests
        pass

    monkeypatch.setattr("animica.cli.mempool._resolve_rpc_url", fake_resolve_rpc_url)
    monkeypatch.setattr("animica.cli.mempool.call_rpc", fake_call_rpc)
    monkeypatch.setattr("time.sleep", fake_sleep)

    result = runner.invoke(app, ["mempool", "list"])

    assert result.exit_code == 0
    assert "Auto-imported peer transactions: requested=1, newly_visible=1" in result.stdout
    assert "Pending transactions (1):" in result.stdout
    assert "0xabc123" in result.stdout
    assert any(method == "p2p.importPeerKnownTxs" for method, _ in calls)
