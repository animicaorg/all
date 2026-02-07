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
            return {
                "requested": 1,
                "tx_state_sample": [
                    {
                        "txid": "0xabc123",
                        "state": "requested",
                        "last_peer": "0xpeer",
                        "last_reason": None,
                        "attempts": 1,
                    }
                ]
            }
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


def test_mempool_list_shows_rejection_reasons_when_transactions_fail(monkeypatch) -> None:
    """Test that specific rejection reasons are displayed for failed transactions."""
    calls: list[tuple[str, tuple]] = []

    def fake_resolve_rpc_url(url):
        return "http://test/rpc"

    def fake_call_rpc(method, params, rpc_url=None, no_cache=True):
        calls.append((method, tuple(params or [])))
        if method == "mempool.getPending":
            # Always return empty - transactions never arrive
            return []
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
                        "txrelay_known_txids": 2,
                        "txrelay_known_txids_sample": ["0xabc123", "0xdef456"],
                    }
                ]
            }
        if method == "mempool.getInfo":
            return {"mempool_id": "mp1", "mempool_path": "/tmp/pending.jsonl"}
        if method == "p2p.importPeerKnownTxs":
            return {
                "requested": 2,
                "tx_state_sample": [
                    {
                        "txid": "0xabc123",
                        "state": "received_invalid",
                        "last_peer": "0xpeer",
                        "last_reason": "invalid_signature",
                        "attempts": 1,
                    },
                    {
                        "txid": "0xdef456",
                        "state": "dropped_evicted",
                        "last_peer": "0xpeer",
                        "last_reason": "insufficient_balance",
                        "attempts": 2,
                    }
                ]
            }
        raise AssertionError(f"Unexpected RPC method: {method}")

    def fake_sleep(duration):
        # Skip actual sleep in tests
        pass

    monkeypatch.setattr("animica.cli.mempool._resolve_rpc_url", fake_resolve_rpc_url)
    monkeypatch.setattr("animica.cli.mempool.call_rpc", fake_call_rpc)
    monkeypatch.setattr("time.sleep", fake_sleep)

    result = runner.invoke(app, ["mempool", "list"])

    assert result.exit_code == 0
    assert "Auto-imported peer transactions: requested=2, newly_visible=0" in result.stdout
    assert "Rejection details:" in result.stdout
    assert "0xabc123" in result.stdout
    assert "state=received_invalid" in result.stdout
    assert "reason=invalid_signature" in result.stdout
    assert "0xdef456" in result.stdout
    assert "state=dropped_evicted" in result.stdout
    assert "reason=insufficient_balance" in result.stdout
