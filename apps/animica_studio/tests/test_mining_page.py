from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from animica_studio.services.wallet_store import WalletRecord
from animica_studio.storage.config import Config
from animica_studio.ui.pages.mining_page import SpacingBackoff, _validate_animica_address, MiningPage


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])  # type: ignore[return-value]


def test_validate_address_rejects_rpc_error_string() -> None:
    bad = "Error: RpcError(-32601) aicf.claimCredits is neither a valid address nor a known wallet label"
    assert _validate_animica_address(bad) is False


def test_spacing_backoff_throttles_warnings() -> None:
    b = SpacingBackoff()
    delay1, warn1 = b.on_spacing_detected()
    delay2, warn2 = b.on_spacing_detected()
    assert delay1 == 1.5
    assert delay2 == 3.0
    assert warn1 is True
    assert warn2 is False


def test_mining_page_resolve_payout_address_blocks_non_address(monkeypatch) -> None:
    _app()
    page = MiningPage(Config())

    monkeypatch.setattr(
        page._wallet_store,
        "reload_local_wallets",
        lambda _path: [WalletRecord(wallet_id="1", address="anim1zqp2rdpnhwvvfe03ts9tf9rnp7p449xhnvh0u0wpvle3wahtce64zwgz8m208", label="premine")],
    )

    page._payout_input.setCurrentText("Error: RpcError(-32601)")
    assert page.resolve_payout_address() is None

    page._payout_input.setCurrentText("premine")
    assert page.resolve_payout_address() is not None


def test_mining_loop_progress_with_spacing_then_success(monkeypatch) -> None:
    _app()
    page = MiningPage(Config())
    page._target_blocks = 2
    page._mined_blocks = 0

    scheduled: list[float] = []
    done = {"ok": False}

    monkeypatch.setattr(page, "_schedule_next_attempt", lambda d: scheduled.append(d))
    monkeypatch.setattr(page, "_finish_success", lambda: done.__setitem__("ok", True))

    page._on_job_finished("job", 0, None)
    assert page._mined_blocks == 1
    assert scheduled[-1] == 0.25

    page._spacing_seen_this_attempt = True
    page._on_job_finished("job", 4, None)
    assert scheduled[-1] >= 1.5

    page._on_job_finished("job", 0, None)
    assert done["ok"] is True
