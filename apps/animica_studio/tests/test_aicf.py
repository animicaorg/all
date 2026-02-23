"""Tests for AICF page and AicfService — no real network required."""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_pyside6_available = importlib.util.find_spec("PySide6") is not None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _app():
    from PySide6.QtWidgets import QApplication  # noqa: PLC0415

    return QApplication.instance() or QApplication([])


def _make_mock_response(data: dict, status_code: int = 200):
    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = data
    return mock


# ---------------------------------------------------------------------------
# AicfPage smoke test — must construct without crashing
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _pyside6_available, reason="PySide6 not installed")
def test_aicf_page_smoke() -> None:
    """AicfPage can be constructed and shown without raising."""
    _app()
    from animica_studio.storage.config import Config  # noqa: PLC0415
    from animica_studio.ui.pages.aicf_page import AicfPage  # noqa: PLC0415

    cfg = Config()
    page = AicfPage(config=cfg)
    assert page is not None
    page.show()
    page.close()


@pytest.mark.skipif(not _pyside6_available, reason="PySide6 not installed")
def test_aicf_page_buttons_exist() -> None:
    """AicfPage exposes all expected button attributes."""
    _app()
    from animica_studio.storage.config import Config  # noqa: PLC0415
    from animica_studio.ui.pages.aicf_page import AicfPage  # noqa: PLC0415

    cfg = Config()
    page = AicfPage(config=cfg)

    assert hasattr(page, "_refresh_btn")
    assert hasattr(page, "_fetch_btn")
    assert hasattr(page, "_claim_btn")
    assert hasattr(page, "_list_jobs_btn")


def test_aicf_page_uses_job_runner() -> None:
    """AicfPage source must use JobRunner, not WorkerThread (static check)."""
    import inspect  # noqa: PLC0415

    # Read source directly without importing (avoids PySide6 requirement)
    src_path = Path(__file__).parent.parent / "animica_studio" / "ui" / "pages" / "aicf_page.py"
    src = src_path.read_text()
    assert "WorkerThread" not in src, "AicfPage must not use WorkerThread"
    assert "JobRunner" in src, "AicfPage must use JobRunner"


# ---------------------------------------------------------------------------
# AicfService unit tests — mocked RPC
# ---------------------------------------------------------------------------


def test_aicf_service_get_status_success() -> None:
    from animica_studio.storage.config import Config
    from animica_studio.services.aicf_service import AicfService

    cfg = Config()
    svc = AicfService(cfg)

    mock_result = {"epoch": 1, "credits_issued": 1000}
    with patch("requests.Session.post") as mock_post:
        mock_post.return_value = _make_mock_response(
            {"jsonrpc": "2.0", "id": 1, "result": mock_result}
        )
        result = svc.get_status()

    assert result["ok"] is True
    assert result["data"] == mock_result


def test_aicf_service_get_status_rpc_error() -> None:
    from animica_studio.storage.config import Config
    from animica_studio.services.aicf_service import AicfService

    cfg = Config()
    svc = AicfService(cfg)

    with patch("requests.Session.post") as mock_post:
        mock_post.return_value = _make_mock_response(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "error": {"code": -32601, "message": "Method not found"},
            }
        )
        result = svc.get_status()

    assert result["ok"] is False
    assert result["error"] is not None


def test_aicf_service_get_status_non_json_response() -> None:
    """Non-JSON RPC response (e.g. unrecognized token) returns graceful error."""
    import requests.exceptions
    from animica_studio.storage.config import Config
    from animica_studio.services.aicf_service import AicfService

    cfg = Config()
    svc = AicfService(cfg)

    with patch("requests.Session.post") as mock_post:
        mock = MagicMock()
        mock.status_code = 200
        mock.json.side_effect = ValueError("unrecognized token: ':'")
        mock_post.return_value = mock
        result = svc.get_status()

    assert result["ok"] is False
    assert result["error"] is not None


def test_aicf_service_get_status_connection_error() -> None:
    """Connection failure returns graceful error, does not raise."""
    import requests.exceptions
    from animica_studio.storage.config import Config
    from animica_studio.services.aicf_service import AicfService

    cfg = Config()
    svc = AicfService(cfg)

    with patch("requests.Session.post") as mock_post:
        mock_post.side_effect = requests.exceptions.ConnectionError("refused")
        result = svc.get_status()

    assert result["ok"] is False
    assert result["error"] is not None


def test_aicf_service_get_miner_credits_success() -> None:
    from animica_studio.storage.config import Config
    from animica_studio.services.aicf_service import AicfService

    cfg = Config()
    svc = AicfService(cfg)

    mock_result = {"address": "anim1test", "credits": 500}
    with patch("requests.Session.post") as mock_post:
        mock_post.return_value = _make_mock_response(
            {"jsonrpc": "2.0", "id": 1, "result": mock_result}
        )
        result = svc.get_miner_credits("anim1test")

    assert result["ok"] is True
    assert result["data"]["credits"] == 500



def test_aicf_service_get_miner_credits_fallback_method() -> None:
    from animica_studio.storage.config import Config
    from animica_studio.services.aicf_service import AicfService

    cfg = Config()
    svc = AicfService(cfg)

    first_error = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "Method not found"}}
    fallback_result = {"jsonrpc": "2.0", "id": 2, "result": {"address": "anim1test", "credits": 250}}
    with patch("requests.Session.post") as mock_post:
        mock_post.side_effect = [_make_mock_response(first_error), _make_mock_response(fallback_result)]
        result = svc.get_miner_credits("anim1test")

    assert result["ok"] is True
    assert result["data"]["credits"] == 250


def test_aicf_service_get_miner_credits_all_methods_missing() -> None:
    from animica_studio.storage.config import Config
    from animica_studio.services.aicf_service import AicfService

    cfg = Config()
    svc = AicfService(cfg)

    not_found = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "Method not found"}}
    with patch("requests.Session.post") as mock_post:
        mock_post.side_effect = [_make_mock_response(not_found) for _ in range(4)]
        result = svc.get_miner_credits("anim1test")

    assert result["ok"] is False
    assert result["error"] is not None

def test_aicf_service_list_jobs_success() -> None:
    from animica_studio.storage.config import Config
    from animica_studio.services.aicf_service import AicfService

    cfg = Config()
    svc = AicfService(cfg)

    mock_result = {"jobs": [], "total": 0}
    with patch("requests.Session.post") as mock_post:
        mock_post.return_value = _make_mock_response(
            {"jsonrpc": "2.0", "id": 1, "result": mock_result}
        )
        result = svc.list_jobs()

    assert result["ok"] is True
    assert result["data"]["total"] == 0


def test_aicf_service_list_jobs_timeout() -> None:
    """Timeout from RPC returns graceful error."""
    import requests.exceptions
    from animica_studio.storage.config import Config
    from animica_studio.services.aicf_service import AicfService

    cfg = Config()
    svc = AicfService(cfg)

    with patch("requests.Session.post") as mock_post:
        mock_post.side_effect = requests.exceptions.Timeout("timed out")
        result = svc.list_jobs()

    assert result["ok"] is False
    assert result["error"] is not None


def test_aicf_service_list_jobs_falls_back_to_da_list() -> None:
    from animica_studio.storage.config import Config
    from animica_studio.services.aicf_service import AicfService

    cfg = Config()
    svc = AicfService(cfg)

    not_found = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "Method not found"}}
    mock_result = {"jobs": [{"id": "job-1"}], "total": 1}
    with patch("requests.Session.post") as mock_post:
        mock_post.side_effect = [
            _make_mock_response(not_found),
            _make_mock_response({"jsonrpc": "2.0", "id": 2, "result": mock_result}),
        ]
        result = svc.list_jobs()

    assert result["ok"] is True
    assert result["data"]["total"] == 1


def test_aicf_service_list_jobs_falls_back_to_da_list_object_params() -> None:
    from animica_studio.storage.config import Config
    from animica_studio.services.aicf_service import AicfService

    cfg = Config()
    svc = AicfService(cfg)

    invalid_params = {"jsonrpc": "2.0", "id": 2, "error": {"code": -32602, "message": "Invalid params"}}
    not_found = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "Method not found"}}
    mock_result = {"jobs": [], "total": 0}
    with patch("requests.Session.post") as mock_post:
        mock_post.side_effect = [
            _make_mock_response(not_found),
            _make_mock_response(invalid_params),
            _make_mock_response({"jsonrpc": "2.0", "id": 3, "result": mock_result}),
        ]
        result = svc.list_jobs(limit=10, offset=5)

    assert result["ok"] is True
    assert result["data"]["total"] == 0

def test_aicf_service_claim_credits_success() -> None:
    from animica_studio.storage.config import Config
    from animica_studio.services.aicf_service import AicfService

    cfg = Config()
    svc = AicfService(cfg)

    with patch.object(svc, "_resolve_aicf_methods", return_value={"claim": "aicf.claim", "claimable": "aicf.getClaimable", "credits": "aicf.creditsByAddress"}):
        with patch.object(svc, "_client") as mock_client_factory:
            mock_client = MagicMock()
            mock_client_factory.return_value = mock_client

            def _call(method, params=None):
                if method == "aicf.getClaimable":
                    return {"claimable": "0x64", "epochs": [1]}
                if method == "aicf.claim":
                    return {"tx_hash": "0x" + "a" * 64, "claimed": 100}
                if method == "aicf.creditsByAddress":
                    return {"address": "anim1test", "balance": "0x0"}
                raise AssertionError(f"Unexpected method: {method}")

            mock_client.call.side_effect = _call
            result = svc.claim_credits("anim1test", amount=100)

    assert result["ok"] is True
    assert result["data"]["claimed"] == 100
    assert result["amount_ignored"] is True


def test_aicf_resolver_picks_claim_from_did_you_mean() -> None:
    from animica_studio.services.aicf_service import AicfService
    from animica_studio.services.rpc_client import RpcError, RpcResponseError

    err = RpcResponseError(
        RpcError(
            code=-32601,
            message="Method not found",
            data={
                "did_you_mean": [
                    "aicf.claim",
                    "aicf.creditsByAddress",
                    "aicf.credits_by_address",
                    "aicf.getClaimable",
                    "aicf_creditsByAddress",
                ]
            },
        )
    )
    picked = AicfService._resolve_method_from_error(("aicf.claim", "aicf.claimCredits"), err)
    assert picked == "aicf.claim"


def test_aicf_service_claim_flow_claimable_then_claim_then_refresh() -> None:
    from animica_studio.storage.config import Config
    from animica_studio.services.aicf_service import AicfService

    cfg = Config()
    svc = AicfService(cfg)

    with patch.object(svc, "_resolve_aicf_methods", return_value={"claim": "aicf.claim", "claimable": "aicf.getClaimable", "credits": "aicf.creditsByAddress"}):
        with patch.object(svc, "_client") as mock_client_factory:
            mock_client = MagicMock()
            mock_client_factory.return_value = mock_client

            def _call(method, params=None):
                if method == "aicf.getClaimable":
                    return {"claimable": "0x64", "epochs": [1]}
                if method == "aicf.claim":
                    return {"tx_hash": "0x" + "b" * 64}
                if method == "aicf.creditsByAddress":
                    return {"address": "anim1test", "balance": "0x0", "minted": "0x64", "spent": "0x64"}
                raise AssertionError(f"Unexpected method: {method}")

            mock_client.call.side_effect = _call

            result = svc.claim_credits("anim1test")

    assert result["ok"] is True
    assert result["tx_hash"] == "0x" + "b" * 64
    called_methods = [call.args[0] for call in mock_client.call.call_args_list]
    assert called_methods.count("aicf.getClaimable") >= 2
    assert "aicf.claim" in called_methods
    assert "aicf.creditsByAddress" in called_methods
