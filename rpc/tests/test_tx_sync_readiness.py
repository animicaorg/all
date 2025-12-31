from __future__ import annotations

import pytest

from rpc import deps
from rpc import errors as rpc_errors
from rpc.methods import tx as tx_methods


class _Snap:
    def __init__(self, data: dict[str, object]):
        self._data = data

    def to_dict(self) -> dict[str, object]:
        return dict(self._data)


class _Svc:
    def __init__(self, cached: dict[str, object], refreshed: dict[str, object]):
        self._cached = cached
        self._refreshed = refreshed
        self.calls: list[bool] = []

    def sync_status_snapshot(self, refresh: bool = False) -> _Snap:
        self.calls.append(refresh)
        data = self._refreshed if refresh else self._cached
        return _Snap(data)


class _Ctx:
    def __init__(self, svc: _Svc):
        self.p2p_service = svc
        self.core_p2p_service = None


def test_tx_sync_gate_uses_refresh_status(monkeypatch: pytest.MonkeyPatch) -> None:
    cached = {
        "phase": "HEADERS",
        "synchronized": False,
        "head_height": 120,
        "best_header_height": 130,
        "in_flight_headers": 1,
        "queued_blocks_count": 3,
    }
    refreshed = {
        "phase": "SYNCED",
        "synchronized": True,
        "head_height": 130,
        "best_header_height": 130,
        "in_flight_headers": 0,
        "queued_blocks_count": 0,
    }
    svc = _Svc(cached, refreshed)
    monkeypatch.setattr(deps, "get_ctx", lambda: _Ctx(svc))

    tx_methods._sync_gate_tx_submit()

    assert svc.calls == [True]


def test_tx_sync_gate_blocks_when_behind(monkeypatch: pytest.MonkeyPatch) -> None:
    status = {
        "phase": "HEADERS",
        "synchronized": False,
        "head_height": 5,
        "best_header_height": 20,
        "in_flight_headers": 0,
        "in_flight_blocks": 0,
        "queued_blocks_count": 0,
    }
    svc = _Svc(status, status)
    monkeypatch.setattr(deps, "get_ctx", lambda: _Ctx(svc))

    with pytest.raises(rpc_errors.TemporarilyUnavailable):
        tx_methods._sync_gate_tx_submit()
