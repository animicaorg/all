import pytest

from core.types.tx import Tx

from rpc import deps as rpc_deps
import rpc.instant_tx as instant_tx
from rpc.mempool_service import MempoolService


@pytest.fixture(autouse=True)
def _disable_mempool_sig_verification(monkeypatch):
    # These tests exercise the mempool persist/restore roundtrip using
    # deliberately UNSIGNED fixture txs. Under 6.0.0 (ANM-H10) the mempool
    # performs mandatory in-mempool PQ signature verification on mainnet
    # admission, which correctly rejects the unsigned fixtures. Signature
    # policy is covered by dedicated admission tests; here we opt out via the
    # intended kill-switch env flag so the persistence behavior can be tested
    # in isolation without weakening any production/security code.
    monkeypatch.setenv("ANIMICA_MEMPOOL_VERIFY_SIGS", "0")


def _build_transfer_tx() -> tuple[Tx, bytes]:
    sender = b"\x11" * 32
    to = b"\x22" * 32
    tx = Tx.transfer(
        chain_id=1,
        nonce=0,
        gas_price=1,
        gas_limit=21_000,
        sender=sender,
        to=to,
        amount=1,
        version=1,
    )
    return tx, tx.to_cbor()


def test_mempool_persists_and_restores(tmp_path):
    tx, raw = _build_transfer_tx()
    service = MempoolService.create(
        chain_id=1,
        min_gas_price_wei=0,
        state_db=None,
        tx_index=None,
        data_dir=str(tmp_path),
    )
    tx_hash = service.submit(tx=tx, raw=raw, local=True)
    persist_path = tmp_path / "mempool" / "pending.jsonl"
    assert persist_path.exists()

    restored = MempoolService.create(
        chain_id=1,
        min_gas_price_wei=0,
        state_db=None,
        tx_index=None,
        data_dir=str(tmp_path),
    )
    assert restored.has_hash(tx_hash)
    assert restored.get_raw(tx_hash) == raw


def test_restore_does_not_reenter_startup_for_instant_receipts(tmp_path, monkeypatch):
    tx, raw = _build_transfer_tx()

    class _FakeInstantService:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def emit_local(self, *, txid: str, anchor_hash: str):
            self.calls.append((txid, anchor_hash))
            return {"txid": txid, "anchor_hash": anchor_hash}

    fake_instant = _FakeInstantService()
    monkeypatch.setattr(instant_tx, "_singleton", fake_instant)
    monkeypatch.setattr(instant_tx, "instant_enabled", lambda: True)

    ensure_started_calls = 0

    class _Ctx:
        def get_head(self):
            return {"height": 1, "hash": "0x" + "11" * 32}

    def _ensure_started_stub(*_args, **_kwargs):
        nonlocal ensure_started_calls
        ensure_started_calls += 1
        return _Ctx()

    monkeypatch.setattr(rpc_deps, "ensure_started", _ensure_started_stub)

    service = MempoolService.create(
        chain_id=1,
        min_gas_price_wei=0,
        state_db=None,
        tx_index=None,
        data_dir=str(tmp_path),
    )
    tx_hash = service.submit(tx=tx, raw=raw, local=True)

    calls_before_restore = ensure_started_calls
    restored = MempoolService.create(
        chain_id=1,
        min_gas_price_wei=0,
        state_db=None,
        tx_index=None,
        data_dir=str(tmp_path),
    )

    assert restored.has_hash(tx_hash)
    assert ensure_started_calls == calls_before_restore
    assert len(fake_instant.calls) >= 2
