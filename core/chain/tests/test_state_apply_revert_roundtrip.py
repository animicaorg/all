from __future__ import annotations

from pathlib import Path

from core.db.sqlite import SQLiteKV
from core.db.state_db import StateDB
from core.types.params import BlockLimits, ChainParams, RetargetBounds, RetargetParams
from core.types.tx import Tx, UnsignedTx
from execution.runtime.env import make_block_env
from execution.runtime.executor import apply_tx


def _params() -> ChainParams:
    return ChainParams(
        chain_id=1337,
        chain_name="Test Chain",
        genesis_time="2025-01-01T00:00:00Z",
        genesis_hash=b"\x00" * 32,
        alg_policy_root=b"\x01" * 32,
        poies_policy_root=b"\x02" * 32,
        theta_initial=100,
        gamma_total_cap=1_000_000,
        retarget=RetargetParams(
            window=24,
            ema_alpha=0.2,
            bounds=RetargetBounds(min=0.5, max=2.0),
        ),
        block=BlockLimits(
            target_seconds=12.0,
            max_bytes=1_500_000,
            max_gas=20_000_000,
            tx_max_bytes=131_072,
            min_gas_price=0,
        ),
    )


def _state_db(tmp_path: Path) -> StateDB:
    kv = SQLiteKV(tmp_path / "state.db")
    return StateDB(kv)


def _transfer_tx(*, chain_id: int, sender: bytes, to: bytes, nonce: int, amount: int) -> Tx:
    unsigned = UnsignedTx.build_transfer(
        chain_id=chain_id,
        sender=sender,
        nonce=nonce,
        gas_price=0,
        gas_limit=21_000,
        to=to,
        amount=amount,
    )
    return Tx(unsigned=unsigned, sigs=())


def test_apply_and_revert_roundtrip(tmp_path: Path) -> None:
    params = _params()
    state_db = _state_db(tmp_path)

    sender = b"\x11" * 32
    recipient = b"\x22" * 32
    state_db.set_balance(sender, 1000)

    snap = state_db.snapshot()
    tx = _transfer_tx(chain_id=params.chain_id, sender=sender, to=recipient, nonce=0, amount=250)
    block_env = make_block_env({"height": 1, "timestamp": 1000}, params)

    result = apply_tx(tx, state_db, block_env, params=params)
    assert result.is_success
    assert state_db.get_balance(sender) == 750
    assert state_db.get_balance(recipient) == 250

    state_db.revert(snap)
    assert state_db.get_balance(sender) == 1000
    assert state_db.get_balance(recipient) == 0
