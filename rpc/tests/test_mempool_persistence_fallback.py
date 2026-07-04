from __future__ import annotations

import errno
from pathlib import Path

import pytest

from core.types.tx import Tx
from mempool.errors import PersistenceFailed
from rpc.mempool_service import MempoolService
from rpc.methods import mempool as mempool_methods


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


def test_mempool_falls_back_to_memory_on_erofs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tx, raw = _build_transfer_tx()
    original_mkdir = Path.mkdir

    def failing_mkdir(self: Path, *args, **kwargs):
        if str(self).endswith("/mempool"):
            raise OSError(errno.EROFS, "Read-only file system")
        return original_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", failing_mkdir)
    monkeypatch.setenv("ANIMICA_MEMPOOL_REQUIRE_PERSIST", "0")
    # This test exercises the EROFS persistence fallback, not signature
    # admission policy (ANM-H10). Use the intended test kill-switch so the
    # unsigned fixture tx is admitted and we can observe the fallback state.
    monkeypatch.setenv("ANIMICA_MEMPOOL_VERIFY_SIGS", "0")

    service = MempoolService.create(
        chain_id=1,
        min_gas_price_wei=0,
        state_db=None,
        tx_index=None,
        data_dir=str(tmp_path),
    )
    tx_hash = service.submit(tx=tx, raw=raw, local=True)

    status = service.persistence_status()
    assert tx_hash.startswith("0x")
    assert status["fallback_active"] is True
    assert status["backend"] == "memory"
    assert status["error"]["kind"] == "mempool_unavailable"

    monkeypatch.setattr(mempool_methods.tx_methods, "_get_mempool_service", lambda: service)
    debug = mempool_methods.debug_mempool_status()
    assert debug["fallback_enabled"] is True


def test_mempool_require_persist_raises_on_erofs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tx, raw = _build_transfer_tx()
    original_mkdir = Path.mkdir

    def failing_mkdir(self: Path, *args, **kwargs):
        if str(self).endswith("/mempool"):
            raise OSError(errno.EROFS, "Read-only file system")
        return original_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", failing_mkdir)
    monkeypatch.setenv("ANIMICA_MEMPOOL_REQUIRE_PERSIST", "1")
    # This test exercises the require-persist EROFS path, not signature
    # admission policy (ANM-H10). Use the intended test kill-switch so the
    # unsigned fixture tx reaches persistence and raises PersistenceFailed.
    monkeypatch.setenv("ANIMICA_MEMPOOL_VERIFY_SIGS", "0")

    service = MempoolService.create(
        chain_id=1,
        min_gas_price_wei=0,
        state_db=None,
        tx_index=None,
        data_dir=str(tmp_path),
    )
    with pytest.raises(PersistenceFailed):
        service.submit(tx=tx, raw=raw, local=True)
