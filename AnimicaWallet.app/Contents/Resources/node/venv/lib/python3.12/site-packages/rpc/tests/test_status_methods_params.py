from __future__ import annotations

from rpc.tests import new_test_client, rpc_call


def test_aicf_status_accepts_no_params() -> None:
    client, _cfg, _ = new_test_client()
    res = rpc_call(client, "aicf.status")
    assert res["result"]["enabled"] in (True, False)
    assert "details" in res["result"]


def test_aicf_status_accepts_empty_array() -> None:
    client, _cfg, _ = new_test_client()
    res = rpc_call(client, "aicf.status", params=[])
    assert "result" in res
    assert "enabled" in res["result"]


def test_aicf_status_accepts_empty_object() -> None:
    client, _cfg, _ = new_test_client()
    res = rpc_call(client, "aicf.status", params={})
    assert "result" in res
    assert "enabled" in res["result"]


def test_aicf_status_accepts_single_object_array() -> None:
    client, _cfg, _ = new_test_client()
    res = rpc_call(client, "aicf.status", params=[{}])
    assert "result" in res
    assert "enabled" in res["result"]


def test_da_status_non_error_when_not_configured() -> None:
    client, _cfg, _ = new_test_client()
    res = rpc_call(client, "da.status")
    out = res["result"]
    assert out["enabled"] is False
    assert out["ok"] is False
    assert out["reason"] in {"not_configured", "not_supported"}


def test_quantum_status_non_error_when_disabled() -> None:
    client, _cfg, _ = new_test_client()
    res = rpc_call(client, "quantum.status")
    out = res["result"]
    assert out["enabled"] is False
    assert out["ok"] is False
    assert out["reason"] in {"disabled", "not_supported"}
