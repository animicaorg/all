import hashlib
from unittest.mock import AsyncMock, MagicMock

import pytest

from p2p.txrelay import TxRelayService


def _mk_service(peer_ids):
    has_tx = AsyncMock(return_value=False)
    has_chain_tx = AsyncMock(return_value=False)
    get_tx = AsyncMock(return_value=None)
    admit_tx = AsyncMock(return_value=(True, None))
    list_mempool_hashes = AsyncMock(return_value=[])
    send_tx_get = AsyncMock()
    service = TxRelayService(
        max_tx_bytes=1_000_000,
        peer_ids=MagicMock(return_value=peer_ids),
        peer_eligible=MagicMock(return_value=True),
        send_tx_inv=AsyncMock(),
        send_tx_get=send_tx_get,
        send_tx_data=AsyncMock(),
        send_tx_notfound=AsyncMock(),
        send_mempool_req=AsyncMock(),
        send_mempool_resp=AsyncMock(),
        has_tx=has_tx,
        has_chain_tx=has_chain_tx,
        get_tx_raw=get_tx,
        admit_tx=admit_tx,
        list_mempool_hashes=list_mempool_hashes,
    )
    return service, send_tx_get


@pytest.mark.asyncio
async def test_request_missing_known_prefers_peers_with_advertised_txids():
    # Reproduces requested=0 when max_peers truncates before non-empty known_txids.
    service, send_tx_get = _mk_service(["peer-empty-1", "peer-empty-2", "peer-has-tx"])
    txid = hashlib.sha3_256(b"import-me").digest()

    async with service._lock:
        service._ensure_peer("peer-empty-1")
        service._ensure_peer("peer-empty-2")
        service._ensure_peer("peer-has-tx").known_txids.add(txid)

    result = await service.request_missing_known(
        limit=1,
        trigger="test",
        max_peers=2,
        include_details=True,
    )

    assert isinstance(result, dict)
    assert result["requested"] == 1
    assert result["requested_peers"] == ["peer-has-tx"]
    send_tx_get.assert_awaited_once()


@pytest.mark.asyncio
async def test_notfound_with_node_id_source_retries_on_mapped_conn_id():
    service, send_tx_get = _mk_service(["conn-a", "conn-b"])
    txid = hashlib.sha3_256(b"retry-me").digest()

    async with service._lock:
        a = service._ensure_peer("conn-a")
        b = service._ensure_peer("conn-b")
        a.peer_node_id = "node-a"
        b.peer_node_id = "node-b"
        a.known_txids.add(txid)
        b.known_txids.add(txid)

    # Store sources as node ids to exercise identity normalization.
    service._tx_sources[txid] = {"node-a", "node-b"}
    service._tx_sources_order[txid] = ["node-a", "node-b"]

    await service.on_tx_notfound("conn-a", [txid])

    # Should retry from conn-b and not get stuck because source is node_id.
    assert send_tx_get.await_count == 1
    assert send_tx_get.await_args_list[0].args[0] == "conn-b"
    st = service._request_mgr.get_state(txid)
    assert st is not None
    assert st.state == "requested"


def test_mark_announced_does_not_override_invalid_final_reason():
    service, _ = _mk_service([])
    txid = hashlib.sha3_256(b"invalid-final").digest()
    now = 1234.5

    service._request_mgr.mark_received_invalid(txid, peer="peer-a", reason="verify_failed:pq", now=now)
    service._request_mgr.mark_announced(txid, peer="peer-a", now=now + 1)

    st = service._request_mgr.get_state(txid)
    assert st is not None
    assert st.state == "invalid_final"
    assert st.last_reason == "verify_failed:pq"


def test_debug_tx_import_includes_peer_mapping():
    service, _ = _mk_service([])
    txid = hashlib.sha3_256(b"debug-me").digest()

    service.register_peer("conn-1", peer_node_id="node-1")
    service._tx_sources[txid] = {"node-1"}
    service._tx_sources_order[txid] = ["node-1"]

    payload = service.debug_tx_import(txid)
    assert payload["txid"].startswith("0x")
    assert payload["peers_advertised"][0]["resolved_conn_id"] == "conn-1"


@pytest.mark.asyncio
async def test_no_duplicate_tx_state_records():
    service, _ = _mk_service([])
    txid = hashlib.sha3_256(b"same-txid").digest()
    service.register_peer("123e4567-e89b-12d3-a456-426614174000", peer_node_id="0x" + "ab" * 32)

    service._request_mgr.mark_requested(txid, peer="123e4567-e89b-12d3-a456-426614174000", now=1.0)
    service._request_mgr.mark_received_invalid(txid, peer="0x" + "ab" * 32, reason="pq_verify", now=2.0)

    state = service.tx_state_for(txid)
    assert state is not None
    assert state["state"] == "invalid_final"
    # single authoritative record keyed by txid
    assert len(service._request_mgr._states) == 1


@pytest.mark.asyncio
async def test_validated_fail_transitions_remove_from_pending():
    service, _ = _mk_service([])
    txid = hashlib.sha3_256(b"fail-not-pending").digest()

    service._request_mgr.mark_requested(txid, peer="peer-a", now=1.0)
    assert service._request_mgr.get_state(txid).state == "requested"
    service._request_mgr.mark_received_invalid(txid, peer="peer-a", reason="pq_verify", now=2.0)

    st = service._request_mgr.get_state(txid)
    assert st is not None
    assert st.state == "invalid_final"
    assert service._request_mgr.can_request(txid, now=3.0) is False


@pytest.mark.asyncio
async def test_race_requested_cannot_override_invalid_final():
    service, _ = _mk_service([])
    txid = hashlib.sha3_256(b"race").digest()

    service._request_mgr.mark_received_invalid(txid, peer="peer-a", reason="pq_verify", now=2.0)
    service._request_mgr.mark_requested(txid, peer="peer-a", now=1.0)

    st = service._request_mgr.get_state(txid)
    assert st is not None
    assert st.state == "invalid_final"


@pytest.mark.asyncio
async def test_import_only_does_not_rerequest_terminal_invalid():
    service, send_tx_get = _mk_service(["peer-a"])
    txid = hashlib.sha3_256(b"invalid-terminal").digest()

    async with service._lock:
        service._ensure_peer("peer-a").known_txids.add(txid)

    service._request_mgr.mark_received_invalid(txid, peer="peer-a", reason="verify_failed:pq", now=1.0)

    result = await service.request_missing_known(limit=1, trigger="test", include_details=True)

    assert result["requested"] == 0
    assert result["skip_reasons"][f"0x{txid.hex()}"] == "terminal_invalid"
    assert send_tx_get.await_count == 0


@pytest.mark.asyncio
async def test_announced_only_is_not_terminal_and_requests_bytes():
    service, send_tx_get = _mk_service(["peer-a"])
    txid = hashlib.sha3_256(b"announced-only").digest()

    async with service._lock:
        service._ensure_peer("peer-a").known_txids.add(txid)
    service._request_mgr.mark_announced(txid, peer="peer-a", now=1.0)

    result = await service.request_missing_known(limit=1, trigger="test", include_details=True)

    assert result["requested"] == 1
    assert send_tx_get.await_count == 1


@pytest.mark.asyncio
async def test_canonicalization_reused_when_cached(monkeypatch):
    service, _ = _mk_service([])
    txid = hashlib.sha3_256(b"canonical-reuse").digest()
    raw = b"rawtx"
    service._touch_tx_store(txid, tx_bytes=raw, canonical_bytes=raw)
    service._request_mgr.mark_requested(txid, peer="peer-a", now=1.0)

    called = {"n": 0}

    def _boom(_: bytes) -> bytes:
        called["n"] += 1
        raise AssertionError("normalize should not be called when canonical bytes cached")

    monkeypatch.setattr("core.utils.tx.normalize_tx_bytes", _boom)

    await service.on_tx_data("peer-a", [{"txid": txid, "tx_bytes": raw}])

    assert called["n"] == 0


def test_outcomes_disjoint_invalid_not_pending():
    txid = hashlib.sha3_256(b"disjoint").digest()
    service, _ = _mk_service([])
    service._request_mgr.mark_received_invalid(txid, peer="peer-a", reason="pq_verify", now=2.0)
    st = service.tx_state_for(txid)
    assert st is not None
    assert st["state"] == "invalid_final"
    assert st["terminal"] is True
