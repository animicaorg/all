from __future__ import annotations

from types import SimpleNamespace

from execution.runtime.transfers import apply_transfer
from execution.types.status import TxStatus
from rpc.mempool_service import MempoolService


class _DummyPool:
    def __len__(self):
        return 0

    def get(self, _):
        return None


class _AcceptingService(MempoolService):
    def submit(self, **kwargs):  # type: ignore[override]
        return kwargs.get("tx_hash_hex") or "0x" + "11" * 32

    def submit_atomic(self, **kwargs):  # type: ignore[override]
        tx_hash = kwargs.get("tx_hash_hex") or "0x" + "11" * 32
        return True, None, tx_hash


class _State:
    def __init__(self, balances: dict[bytes, int], nonces: dict[bytes, int] | None = None):
        self._balances = dict(balances)
        self._nonces = dict(nonces or {})

    def get_balance(self, addr: bytes) -> int:
        return int(self._balances.get(addr, 0))

    def set_balance(self, addr: bytes, value: int) -> None:
        self._balances[addr] = int(value)

    def get_nonce(self, addr: bytes) -> int:
        return int(self._nonces.get(addr, 0))

    def set_nonce(self, addr: bytes, value: int) -> None:
        self._nonces[addr] = int(value)


def test_mempool_admission_then_execution_applies_state_once() -> None:
    sender = b"\x01" * 32
    recipient = b"\x02" * 32
    state = _State({sender: 1000, recipient: 0}, {sender: 0})

    svc = _AcceptingService(
        pool=_DummyPool(),
        chain_id=1,
        min_gas_price_wei=0,
        state_db=state,
        tx_index=None,
        persist_enabled=False,
    )

    ok, reject, tx_hash = svc.submit_atomic(tx_hash_hex="0x" + "22" * 32, simulate=True)
    assert ok is True
    assert reject is None
    assert tx_hash == "0x" + "22" * 32

    assert state.get_balance(sender) == 1000
    assert state.get_balance(recipient) == 0
    assert state.get_nonce(sender) == 0

    tx = {"to": recipient, "amount": 10, "gas_limit": 21_000, "nonce": 0}
    block_env = SimpleNamespace(coinbase=b"\x03" * 32, treasury=b"\x04" * 32)
    tx_env = SimpleNamespace(sender=sender, gas_price=0, base_price=0)

    result = apply_transfer(tx=tx, state=state, block_env=block_env, tx_env=tx_env)
    assert result.status == TxStatus.SUCCESS
    assert state.get_balance(sender) == 990
    assert state.get_balance(recipient) == 10
    assert state.get_nonce(sender) == 1
