import asyncio

import p2p
from rpc.methods import miner as miner_methods
from rpc.methods import p2p as p2p_methods


class _Snapshot:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def to_dict(self) -> dict:
        return dict(self._payload)


class _DummyP2PService:
    def __init__(self, status: dict, sync: dict) -> None:
        self._status = status
        self._sync = sync

    def status_snapshot(self):
        return _Snapshot(self._status)

    def sync_status_snapshot(self):
        return _Snapshot(self._sync)

    def doctor_snapshot(self, *, limit: int = 10) -> dict:
        return dict(self._status)


def test_mining_gate_matches_p2p_doctor_counts(monkeypatch):
    status = {
        "peers_connected": 1,
        "peers_total": 1,
        "peers_handshaking": 0,
        "peers_connected_outbound": 1,
        "connection_events": [],
    }
    sync = {
        "head_height": 1,
        "best_header_height": 1,
        "best_block_height": 1,
        "best_remote_height": 1,
        "network_best_height": 1,
        "phase": "SYNCED",
    }
    dummy = _DummyP2PService(status, sync)
    monkeypatch.setattr(p2p, "get_service", lambda: dummy)

    allowed, reason = miner_methods._mining_gate()
    assert allowed is True, reason

    doctor = asyncio.run(p2p_methods.doctor())
    assert doctor.get("peers_connected") == status["peers_connected"]
