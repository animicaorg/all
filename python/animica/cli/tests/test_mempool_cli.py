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
    assert "Transaction Status Summary" in result.stdout
    # Verify specific counts
    assert "✓ Accepted:  0" in result.stdout
    assert "⏳ Pending:   0" in result.stdout
    assert "✗ Rejected:  2" in result.stdout
    assert "Rejected Transaction Details:" in result.stdout
    assert "0xabc123" in result.stdout
    assert "[received_invalid]" in result.stdout
    assert "invalid_signature" in result.stdout
    assert "0xdef456" in result.stdout
    assert "[dropped_evicted]" in result.stdout
    assert "insufficient_balance" in result.stdout
    # Verify the enhanced empty mempool message
    assert "Local mempool is empty" in result.stdout
    assert "peers advertise 2 transaction(s)" in result.stdout


def test_mempool_list_shows_context_when_empty_with_peer_transactions(monkeypatch) -> None:
    """Test that enhanced messaging is shown when mempool is empty but peers have transactions."""
    calls: list[tuple[str, tuple]] = []

    def fake_resolve_rpc_url(url):
        return "http://test/rpc"

    def fake_call_rpc(method, params, rpc_url=None, no_cache=True):
        calls.append((method, tuple(params or [])))
        if method == "mempool.getPending":
            return []  # Empty mempool
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
                        "peer_id": "0xpeer1",
                        "conn_id": "0xconn1",
                        "txrelay_known_txids": 5,
                        "txrelay_known_txids_sample": ["0xtx1", "0xtx2"],
                    }
                ]
            }
        if method == "mempool.getInfo":
            return {"mempool_id": "mp1", "mempool_path": "/tmp/pending.jsonl"}
        if method == "p2p.importPeerKnownTxs":
            return {
                "requested": 5,
                "tx_state_sample": [],  # No state info available
            }
        raise AssertionError(f"Unexpected RPC method: {method}")

    def fake_sleep(duration):
        pass

    monkeypatch.setattr("animica.cli.mempool._resolve_rpc_url", fake_resolve_rpc_url)
    monkeypatch.setattr("animica.cli.mempool.call_rpc", fake_call_rpc)
    monkeypatch.setattr("time.sleep", fake_sleep)

    result = runner.invoke(app, ["mempool", "list"])

    assert result.exit_code == 0
    # Verify enhanced empty mempool message
    assert "Local mempool is empty (0 pending), but peers advertise 5 transaction(s)" in result.stdout
    assert "Possible reasons:" in result.stdout
    assert "Transactions were rejected" in result.stdout
    # Verify fallback message when no tx_state_sample
    assert "Transaction Status: Unable to retrieve detailed status" in result.stdout


def test_mempool_list_import_uses_all_eligible_peers_by_default(monkeypatch) -> None:
    calls: list[tuple[str, tuple]] = []

    def fake_resolve_rpc_url(url):
        return "http://test/rpc"

    def fake_call_rpc(method, params, rpc_url=None, no_cache=True):
        calls.append((method, tuple(params or [])))
        if method == "mempool.getPending":
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
                        "txrelay_known_txids": 1,
                        "txrelay_known_txids_sample": ["0xabc123"],
                    }
                ]
            }
        if method == "mempool.getInfo":
            return {"mempool_id": "mp1", "mempool_path": "/tmp/pending.jsonl"}
        if method == "p2p.importPeerKnownTxs":
            return {"requested": 0, "tx_state_sample": []}
        raise AssertionError(f"Unexpected RPC method: {method}")

    monkeypatch.setattr("animica.cli.mempool._resolve_rpc_url", fake_resolve_rpc_url)
    monkeypatch.setattr("animica.cli.mempool.call_rpc", fake_call_rpc)

    result = runner.invoke(app, ["mempool", "list"])

    assert result.exit_code == 0
    import_calls = [params for method, params in calls if method == "p2p.importPeerKnownTxs"]
    assert import_calls
    assert import_calls[0] == (128, 12.0, 0, 64)


def test_mempool_list_does_not_show_pending_for_invalid_final(monkeypatch) -> None:
    calls: list[tuple[str, tuple]] = []

    def fake_resolve_rpc_url(url):
        return "http://test/rpc"

    def fake_call_rpc(method, params, rpc_url=None, no_cache=True):
        calls.append((method, tuple(params or [])))
        if method == "mempool.getPending":
            return []
        if method == "chain.getChainIdentity":
            return {"chainId": 1, "genesisHash": "0xdeadbeef"}
        if method == "chain.getHead":
            return {"height": 42, "hash": "0xhead"}
        if method == "p2p.getStatus":
            return {"peer_id": "0xnode"}
        if method == "p2p.debugStatus":
            return {"peers": [{"peer_id": "0xpeer", "conn_id": "conn-1", "txrelay_known_txids": 1, "txrelay_known_txids_sample": ["0xabc123"]}]}
        if method == "mempool.getInfo":
            return {"mempool_id": "mp1", "mempool_path": "/tmp/pending.jsonl"}
        if method == "p2p.importPeerKnownTxs":
            return {
                "requested": 1,
                "summary": {"admitted": 0, "rejected": 1, "notfound": 0, "pending": 0},
                "tx_state_sample": [
                    {
                        "txid": "0xabc123",
                        "state": "invalid_final",
                        "last_peer_node_id": "0xpeer",
                        "last_peer_conn_id": "conn-1",
                        "last_reason": "pq_verify",
                        "attempts": 1,
                    }
                ],
            }
        raise AssertionError(f"Unexpected RPC method: {method}")

    monkeypatch.setattr("animica.cli.mempool._resolve_rpc_url", fake_resolve_rpc_url)
    monkeypatch.setattr("animica.cli.mempool.call_rpc", fake_call_rpc)

    result = runner.invoke(app, ["mempool", "list"])

    assert result.exit_code == 0
    assert "⏳ Pending:   0" in result.stdout
    assert "✗ Rejected:  1" in result.stdout
    assert "[invalid_final]" in result.stdout


def test_mempool_list_shows_invalid_final_details_even_when_requested_is_zero(monkeypatch) -> None:
    calls: list[tuple[str, tuple]] = []

    def fake_resolve_rpc_url(url):
        return "http://test/rpc"

    def fake_call_rpc(method, params, rpc_url=None, no_cache=True):
        calls.append((method, tuple(params or [])))
        if method == "mempool.getPending":
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
                        "txrelay_known_txids": 1,
                        "txrelay_known_txids_sample": ["0xc588"],
                    }
                ]
            }
        if method == "mempool.getInfo":
            return {"mempool_id": "mp1", "mempool_path": "/tmp/pending.jsonl"}
        if method == "p2p.importPeerKnownTxs":
            return {
                "requested": 0,
                "summary": {"admitted": 0, "rejected": 0, "notfound": 0, "pending": 0},
                "tx_state_sample": [
                    {
                        "txid": "0xc588bce9702bb39b21a0f06b648f43c540e8fdcb1df2ef4219909951c19f9152",
                        "state": "invalid_final",
                        "last_peer_node_id": "0x1369322564",
                        "last_peer_conn_id": "f81dad83-be31-45f4-89bb-ff12ae294907",
                        "last_reason": "verify_failed:[-32012] Invalid post-quantum signature",
                        "attempts": 1,
                    }
                ],
            }
        raise AssertionError(f"Unexpected RPC method: {method}")

    monkeypatch.setattr("animica.cli.mempool._resolve_rpc_url", fake_resolve_rpc_url)
    monkeypatch.setattr("animica.cli.mempool.call_rpc", fake_call_rpc)

    result = runner.invoke(app, ["mempool", "list"])

    assert result.exit_code == 0
    assert "Auto-imported peer transactions: requested=0, newly_visible=0" in result.stdout
    assert "Transaction Status Summary" in result.stdout
    assert "✗ Rejected:  1" in result.stdout
    assert "[invalid_final]" in result.stdout
    assert "Invalid post-quantum signature" in result.stdout
