from __future__ import annotations

import sys
import types
from types import SimpleNamespace

from animica.stratum_pool.config import PoolConfig
from animica.stratum_pool.payouts import PoolPayoutScheduler


class StubMetrics:
    def __init__(
        self,
        budget: int,
        due_items: list[dict[str, object]] | None = None,
    ) -> None:
        self._budget = int(budget)
        self._due_items = list(due_items or [])
        self.budget_calls = 0
        self.due_calls: list[dict[str, int]] = []
        self.sent_calls: list[dict[str, object]] = []
        self.failed_calls: list[dict[str, object]] = []

    def payout_available_budget(self) -> int:
        self.budget_calls += 1
        return self._budget

    def payout_due_addresses(
        self,
        *,
        min_amount: int,
        limit: int = 50,
        max_total_amount: int | None = None,
    ) -> list[dict[str, object]]:
        self.due_calls.append(
            {
                "min_amount": int(min_amount),
                "limit": int(limit),
                "max_total_amount": int(max_total_amount or 0),
            }
        )
        return list(self._due_items)

    def record_payout_sent(self, *, address: str, amount: int, tx_hash: str) -> int:
        payload = {
            "address": str(address),
            "amount": int(amount),
            "tx_hash": str(tx_hash),
        }
        self.sent_calls.append(payload)
        return int(amount)

    def record_payout_failed(self, *, address: str, amount: int, error: str) -> None:
        self.failed_calls.append(
            {
                "address": str(address),
                "amount": int(amount),
                "error": str(error),
            }
        )


def _config() -> PoolConfig:
    return PoolConfig(
        db_url="",
        payout_interval_seconds=60,
        payout_min_amount=10,
        payout_wallet="anim1poolwallet",
        pool_address="anim1poolwallet",
    )


def _install_omni_sdk_stubs(monkeypatch, *, submit_raw, request_handler=None):
    class _RpcClient:
        def __init__(self, _url: str, timeout: float = 0.0) -> None:
            self.timeout = float(timeout)

        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _tb):
            return False

        def request(self, method: str, params: object) -> object:
            if callable(request_handler):
                return request_handler(method, params)
            if method == "state.getNonce":
                return 7
            raise RuntimeError(f"unexpected method: {method}")

    rpc_http_module = types.ModuleType("omni_sdk.rpc.http")
    rpc_http_module.RpcClient = _RpcClient

    tx_build_module = types.ModuleType("omni_sdk.tx.build")
    tx_build_module.transfer = lambda **kwargs: dict(kwargs)

    tx_send_module = types.ModuleType("omni_sdk.tx.send")
    tx_send_module.submit_raw = submit_raw

    signing_module = types.ModuleType("omni_sdk.tx.signing")
    signing_module.sign_transaction_with_rpc_context = (
        lambda tx_obj, _signer, chain_id, rpc: SimpleNamespace(
            raw_tx=(
                f"raw:{int(chain_id)}:"
                f"{tx_obj.get('from_addr')}:{tx_obj.get('to_addr')}:"
                f"{int(tx_obj.get('amount') or 0)}:{int(tx_obj.get('nonce') or 0)}:"
                f"{int(getattr(rpc, 'timeout', 0))}:{int(tx_obj.get('max_fee') or 0)}"
            )
        )
    )

    tx_module = types.ModuleType("omni_sdk.tx")
    tx_module.build = tx_build_module
    tx_module.send = tx_send_module

    rpc_module = types.ModuleType("omni_sdk.rpc")
    rpc_module.http = rpc_http_module

    omni_module = types.ModuleType("omni_sdk")
    omni_module.rpc = rpc_module
    omni_module.tx = tx_module

    monkeypatch.setitem(sys.modules, "omni_sdk", omni_module)
    monkeypatch.setitem(sys.modules, "omni_sdk.rpc", rpc_module)
    monkeypatch.setitem(sys.modules, "omni_sdk.rpc.http", rpc_http_module)
    monkeypatch.setitem(sys.modules, "omni_sdk.tx", tx_module)
    monkeypatch.setitem(sys.modules, "omni_sdk.tx.build", tx_build_module)
    monkeypatch.setitem(sys.modules, "omni_sdk.tx.send", tx_send_module)
    monkeypatch.setitem(sys.modules, "omni_sdk.tx.signing", signing_module)


def test_process_once_skips_due_lookup_when_available_budget_is_zero():
    metrics = StubMetrics(budget=0)
    scheduler = PoolPayoutScheduler(config=_config(), metrics=metrics)

    sent = scheduler._process_once()  # noqa: SLF001

    assert sent == 0
    assert metrics.budget_calls == 1
    assert metrics.due_calls == []


def test_process_once_limits_due_lookup_by_available_budget():
    metrics = StubMetrics(budget=250)
    scheduler = PoolPayoutScheduler(config=_config(), metrics=metrics)

    sent = scheduler._process_once()  # noqa: SLF001

    assert sent == 0
    assert metrics.budget_calls == 1
    assert metrics.due_calls == [
        {
            "min_amount": 10,
            "limit": 100,
            "max_total_amount": 250,
        }
    ]


def test_process_once_retries_transient_submission_failure(monkeypatch):
    monkeypatch.setenv("ANIMICA_POOL_PAYOUT_RETRY_ATTEMPTS", "2")
    monkeypatch.setenv("ANIMICA_POOL_PAYOUT_RETRY_BACKOFF_SECONDS", "0")

    submit_calls: list[str] = []

    def _submit_raw(_rpc, raw_tx: str) -> str:
        submit_calls.append(str(raw_tx))
        if len(submit_calls) == 1:
            raise RuntimeError("temporary rpc timeout")
        return "0x" + ("ab" * 32)

    _install_omni_sdk_stubs(monkeypatch, submit_raw=_submit_raw)

    metrics = StubMetrics(
        budget=500,
        due_items=[{"address": "anim1miner", "amount": 25}],
    )
    scheduler = PoolPayoutScheduler(config=_config(), metrics=metrics)
    scheduler._signer_resolution = SimpleNamespace(  # noqa: SLF001
        sender="anim1poolwallet",
        signer=SimpleNamespace(address="anim1poolwallet"),
    )

    sent = scheduler._process_once()  # noqa: SLF001

    assert sent == 1
    assert len(submit_calls) == 2
    assert submit_calls[0] == submit_calls[1]
    assert metrics.sent_calls == [
        {
            "address": "anim1miner",
            "amount": 25,
            "tx_hash": "0x" + ("ab" * 32),
        }
    ]
    assert metrics.failed_calls == []


def test_process_once_rebroadcasts_dropped_submitted_payout(monkeypatch):
    monkeypatch.setenv("ANIMICA_POOL_PAYOUT_DROP_GRACE_SECONDS", "0")
    monkeypatch.setenv("ANIMICA_POOL_PAYOUT_RETRY_BACKOFF_SECONDS", "0")

    rebroadcast_submit_calls: list[str] = []

    def _submit_raw(_rpc, raw_tx: str) -> str:
        rebroadcast_submit_calls.append(str(raw_tx))
        return "0x" + ("cd" * 32)

    def _request_handler(method: str, params: object) -> object:
        if method == "state.getNonce":
            return 7
        if method == "tx.getStatus":
            return {
                "hash": str((params or [""])[0]),
                "status": "evicted",
                "state": "evicted",
            }
        raise RuntimeError(f"unexpected method: {method}")

    _install_omni_sdk_stubs(
        monkeypatch,
        submit_raw=_submit_raw,
        request_handler=_request_handler,
    )

    class _ReconcileMetrics(StubMetrics):
        def __init__(self) -> None:
            super().__init__(budget=0, due_items=[])
            self.rebroadcast_calls: list[dict[str, object]] = []

        def pending_payout_submissions(self, *, limit: int = 200) -> list[dict[str, object]]:
            _ = limit
            return [
                {
                    "tx_hash": "0x" + ("ab" * 32),
                    "address": "anim1miner",
                    "amount": 25,
                    "raw_tx": "raw:1:anim1poolwallet:anim1miner:25:7:300",
                    "retry_count": 0,
                    "timestamp": 0.0,
                    "nonce": 7,
                    "next_retry_ts": None,
                }
            ]

        def record_payout_rebroadcast(self, **kwargs: object) -> bool:
            self.rebroadcast_calls.append(dict(kwargs))
            return True

    metrics = _ReconcileMetrics()
    scheduler = PoolPayoutScheduler(config=_config(), metrics=metrics)
    scheduler._signer_resolution = SimpleNamespace(  # noqa: SLF001
        sender="anim1poolwallet",
        signer=SimpleNamespace(address="anim1poolwallet"),
    )

    sent = scheduler._process_once()  # noqa: SLF001

    assert sent == 0
    assert len(rebroadcast_submit_calls) == 1
    assert rebroadcast_submit_calls[0] != "raw:1:anim1poolwallet:anim1miner:25:7:300:0"
    assert rebroadcast_submit_calls[0].startswith(
        "raw:1:anim1poolwallet:anim1miner:25:7:300:"
    )
    assert len(metrics.rebroadcast_calls) == 1
    assert metrics.rebroadcast_calls[0]["tx_hash"] == "0x" + ("ab" * 32)
    assert metrics.rebroadcast_calls[0]["new_tx_hash"] == "0x" + ("cd" * 32)
