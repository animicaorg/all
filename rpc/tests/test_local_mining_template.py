import pytest

from consensus.rewards import MAINNET_PREMINE_DISTRIBUTION
from rpc.tests import new_test_client, rpc_call
import p2p


class _Snapshot:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def to_dict(self) -> dict:
        return dict(self._payload)


class _DummyP2PService:
    def status_snapshot(self) -> _Snapshot:
        return _Snapshot({"peers_outbound": 1, "peers_total": 1})

    def sync_status_snapshot(self) -> _Snapshot:
        return _Snapshot({"phase": "HEADERS", "head_height": 5, "best_header_height": 5})


def test_local_mining_template_available_when_headers_phase(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(p2p, "get_service", lambda: _DummyP2PService())
    client, _, _ = new_test_client()
    payout_address = MAINNET_PREMINE_DISTRIBUTION[0][0]

    res = rpc_call(
        client,
        "miner.getBlockTemplate",
        {"address": payout_address, "allow_offline_mining": True},
    )

    assert res["result"]["enabled"] is True
    assert "header" in res["result"]
