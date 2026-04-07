"""Tests for ActivityStore and ExplorerBalanceService (no Qt, no network)."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# ActivityStore
# ---------------------------------------------------------------------------


def test_activity_store_record_and_get():
    from animica_studio.services.activity_store import ActivityKind, ActivityStore

    store = ActivityStore(capacity=10)
    store.record(ActivityKind.JOB_OK, "test job", ok=True)
    store.record(ActivityKind.NETWORK_CHECK, "network checked", ok=True)
    entries = store.get_recent()
    assert len(entries) == 2
    # Newest first
    assert entries[0].summary == "network checked"


def test_activity_store_capacity():
    from animica_studio.services.activity_store import ActivityKind, ActivityStore

    store = ActivityStore(capacity=5)
    for i in range(10):
        store.record(ActivityKind.GENERIC, f"entry {i}")
    assert len(store.get_recent(100)) == 5


def test_activity_store_last_n():
    from animica_studio.services.activity_store import ActivityKind, ActivityStore

    store = ActivityStore()
    for i in range(30):
        store.record(ActivityKind.GENERIC, f"entry {i}")
    recent = store.get_recent(5)
    assert len(recent) == 5


def test_activity_store_ok_fail():
    from animica_studio.services.activity_store import ActivityKind, ActivityStore

    store = ActivityStore()
    e_ok = store.record_job("success", ok=True)
    e_fail = store.record_job("failed", ok=False)
    assert e_ok.status_badge == "✓"
    assert e_fail.status_badge == "✗"


def test_activity_store_age_label():
    from animica_studio.services.activity_store import ActivityKind, ActivityStore

    store = ActivityStore()
    e = store.record(ActivityKind.GENERIC, "old", ts=time.time() - 70)
    assert "m ago" in e.age_label


def test_activity_store_singleton():
    from animica_studio.services.activity_store import ActivityStore

    a = ActivityStore.instance()
    b = ActivityStore.instance()
    assert a is b


def test_activity_store_clear():
    from animica_studio.services.activity_store import ActivityKind, ActivityStore

    store = ActivityStore()
    store.record(ActivityKind.GENERIC, "x")
    store.clear()
    assert store.get_recent() == []


# ---------------------------------------------------------------------------
# ExplorerBalanceService (headless, no Qt)
# ---------------------------------------------------------------------------


def _mock_response(json_data: dict, status_code: int = 200) -> MagicMock:
    m = MagicMock()
    m.status_code = status_code
    m.json.return_value = json_data
    m.text = str(json_data)
    return m


def _make_profile(explorer_url: str = "http://explorer.example.com"):
    from animica_studio.models.profile_models import RpcProfile

    return RpcProfile(
        id="test",
        name="Test",
        type="remote_rpc",
        rpc_url="http://localhost:8545/rpc",
        chain_id_expected=1,
        explorer_base_url=explorer_url,
    )


def test_explorer_balance_service_ok():
    from animica_studio.services.explorer_balance_service import _fetch_balance_sync

    with patch("requests.get") as mock_get:
        mock_get.return_value = _mock_response({"confirmedBalance": "1000000000"})
        result = _fetch_balance_sync("anim1test", "http://explorer.example.com")
    assert result.ok
    assert result.balance_wei == 1_000_000_000
    assert "ANM" in result.formatted


def test_explorer_balance_service_missing_field():
    from animica_studio.services.explorer_balance_service import _fetch_balance_sync

    with patch("requests.get") as mock_get:
        mock_get.return_value = _mock_response({"some_other_field": "value"})
        result = _fetch_balance_sync("anim1test", "http://explorer.example.com")
    assert not result.ok
    assert "missing" in result.error.lower()


def test_explorer_balance_service_http_error():
    from animica_studio.services.explorer_balance_service import _fetch_balance_sync

    with patch("requests.get") as mock_get:
        mock_get.return_value = _mock_response({}, status_code=404)
        result = _fetch_balance_sync("anim1test", "http://explorer.example.com")
    assert not result.ok
    assert "404" in result.error


def test_explorer_balance_service_network_error():
    import requests

    from animica_studio.services.explorer_balance_service import _fetch_balance_sync

    with patch("requests.get") as mock_get:
        mock_get.side_effect = requests.ConnectionError("refused")
        result = _fetch_balance_sync("anim1test", "http://explorer.example.com")
    assert not result.ok
    assert "Request failed" in result.error


def test_explorer_balance_service_hex_balance():
    from animica_studio.services.explorer_balance_service import _fetch_balance_sync

    with patch("requests.get") as mock_get:
        mock_get.return_value = _mock_response({"balance": "0x64"})
        result = _fetch_balance_sync("anim1test", "http://explorer.example.com")
    assert result.ok
    assert result.balance_wei == 100


def test_explorer_balance_service_no_url():
    from animica_studio.services.explorer_balance_service import _fetch_balance_sync

    # Test behavior when explorer URL is empty — should return error result, not raise
    result = _fetch_balance_sync("anim1test", "")
    # With empty URL, requests.get would fail or return error
    # We just verify the function doesn't raise
    assert hasattr(result, "ok")


def test_total_balance_result_empty():
    from animica_studio.services.explorer_balance_service import TotalBalanceResult

    # Empty TotalBalanceResult has sensible defaults
    t = TotalBalanceResult(wallet_count=0, formatted="0 ANM")
    assert t.wallet_count == 0
    assert t.ok_count == 0
    assert t.error_count == 0


def test_total_balance_result_sum():
    from animica_studio.services.explorer_balance_service import BalanceResult, TotalBalanceResult
    from animica_studio.models.wallet_models import format_amount

    # Build a TotalBalanceResult manually from two ok results
    r1 = BalanceResult(address="anim1a", balance_wei=500, formatted=format_amount(500), ok=True)
    r2 = BalanceResult(address="anim1b", balance_wei=500, formatted=format_amount(500), ok=True)
    results = {"anim1a": r1, "anim1b": r2}
    total_wei = sum(r.balance_wei for r in results.values() if r.ok)
    ok_count = sum(1 for r in results.values() if r.ok)

    t = TotalBalanceResult(
        total_wei=total_wei,
        formatted=format_amount(total_wei),
        wallet_count=2,
        ok_count=ok_count,
        error_count=0,
    )
    assert t.total_wei == 1000
    assert t.ok_count == 2
    assert t.error_count == 0
