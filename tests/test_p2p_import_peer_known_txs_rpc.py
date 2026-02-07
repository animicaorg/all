import pytest

from rpc.methods import p2p as p2p_methods


class _FakeP2PService:
    def __init__(self, request_result):
        self._request_result = request_result

    async def request_missing_txids(self, **_kwargs):
        return self._request_result


class _FakeRelay:
    def __init__(self, state_by_txid):
        self._state_by_txid = state_by_txid

    def tx_state_for(self, txid: bytes):
        return self._state_by_txid.get("0x" + txid.hex())

    def tx_state_snapshot(self, limit: int = 20):
        return list(self._state_by_txid.values())[:limit]


@pytest.mark.asyncio
async def test_import_peer_known_txs_reports_admitted(monkeypatch):
    req = {
        "requested": 2,
        "requested_txids": ["0x" + "11" * 32, "0x" + "22" * 32],
        "requested_peers": ["peer-a"],
    }
    relay = _FakeRelay(
        {
            "0x" + "11" * 32: {"state": "accepted_in_mempool", "last_peer": "peer-a"},
            "0x" + "22" * 32: {"state": "accepted_in_mempool", "last_peer": "peer-a"},
        }
    )

    monkeypatch.setattr(p2p_methods, "_get_p2p_service", lambda: _FakeP2PService(req))
    monkeypatch.setattr(p2p_methods, "_get_tx_relay_service", lambda _svc: relay)

    result = await p2p_methods.import_peer_known_txs(limit=2, timeout_s=2.5)

    assert result["success"] is True
    assert result["requested"] == 2
    assert result["summary"]["admitted"] == 2
    assert result["summary"]["pending"] == 0


@pytest.mark.asyncio
async def test_import_peer_known_txs_reports_reject_reason(monkeypatch):
    txid = "0x" + "33" * 32
    req = {
        "requested": 1,
        "requested_txids": [txid],
        "requested_peers": ["peer-b"],
    }
    relay = _FakeRelay(
        {
            txid: {
                "state": "rejected",
                "last_peer": "peer-b",
                "last_reason": "invalid_signature",
            }
        }
    )

    monkeypatch.setattr(p2p_methods, "_get_p2p_service", lambda: _FakeP2PService(req))
    monkeypatch.setattr(p2p_methods, "_get_tx_relay_service", lambda _svc: relay)

    result = await p2p_methods.import_peer_known_txs(limit=1, timeout_s=2.5)

    assert result["success"] is True
    assert result["summary"]["rejected"] == 1
    assert result["outcomes"]["rejected"][0]["reason"] == "invalid_signature"
