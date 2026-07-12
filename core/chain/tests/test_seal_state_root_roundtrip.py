"""7.1.9 miner execute-then-seal: the sealed stateRoot must equal the root a
validator computes when it applies the same block to the same parent state.

compute_sealed_state_root() runs the *identical* _apply_block_state path on a
snapshot and reverts, so seal == validate by construction. This locks in:
  * revert-clean  — the live state is byte-identical after sealing;
  * determinism   — repeated seals of the same block match;
  * seal==validate — the real apply's compute_state_root equals the sealed root;
  * non-zero      — a block that mutates state seals a non-zero root (so the
                    FORK_STATE_COMMITMENT self-gate on non-zero actually fires).
"""
from __future__ import annotations

from pathlib import Path

from core.chain.block_import import BlockImporter
from core.chain.state_commit import compute_state_root
from core.db.block_db import BlockDB
from core.db.sqlite import SQLiteKV
from core.db.state_db import StateDB
from core.types.block import Block
from core.types.header import Header
from core.types.params import BlockLimits, ChainParams, RetargetBounds, RetargetParams
from core.types.tx import Tx, UnsignedTx
from core.utils.hash import ZERO32

CHAIN_ID = 1337
SENDER = b"\x11" * 32
RECIPIENT = b"\x22" * 32


def _params() -> ChainParams:
    return ChainParams(
        chain_id=CHAIN_ID,
        chain_name="Test Chain",
        genesis_time="2025-01-01T00:00:00Z",
        genesis_hash=b"\x00" * 32,
        alg_policy_root=b"\x01" * 32,
        poies_policy_root=b"\x02" * 32,
        theta_initial=100,
        theta_min=100,
        theta_max=1_000_000,
        gamma_total_cap=1_000_000,
        retarget=RetargetParams(window=24, ema_alpha=0.2, bounds=RetargetBounds(min=0.5, max=2.0)),
        block=BlockLimits(
            target_seconds=12.0, max_bytes=1_500_000, max_gas=20_000_000,
            tx_max_bytes=131_072, min_gas_price=0,
        ),
    )


def _plain_header(height: int) -> Header:
    # No PoW: we call _apply_block_state directly (not import_block), which does
    # not check the nonce/target or the txs root.
    return Header(
        v=1, chainId=CHAIN_ID, height=height, parentHash=b"\x00" * 32, timestamp=1000,
        stateRoot=ZERO32, txsRoot=ZERO32, receiptsRoot=ZERO32, proofsRoot=ZERO32,
        daRoot=ZERO32, mixSeed=ZERO32, poiesPolicyRoot=ZERO32, pqAlgPolicyRoot=ZERO32,
        thetaMicro=100, nonce=0, extra=b"",
    )


def _transfer(amount: int) -> Tx:
    # v2: nonce omitted (chain is nonce-less).
    unsigned = UnsignedTx.build_transfer(
        chain_id=CHAIN_ID, sender=SENDER, gas_price=0, gas_limit=21_000,
        to=RECIPIENT, amount=amount, valid_after=0, valid_until=10_000_000,
        salt=b"\x07" * 32,
    )
    return Tx(unsigned=unsigned, sigs=())


def _fresh(tmp_path: Path, sender_balance: int) -> BlockImporter:
    kv = SQLiteKV(tmp_path / "chain.db")
    imp = BlockImporter(params=_params(), block_db=BlockDB(kv), state_db=StateDB(kv))
    imp.state_db.set_balance(SENDER, sender_balance)
    return imp


def test_seal_equals_validate_revert_clean_and_deterministic(tmp_path: Path) -> None:
    imp = _fresh(tmp_path, 10_000_000)
    block = Block(header=_plain_header(1), txs=(_transfer(1_000_000),), proofs=(), receipts=None)

    # Seal against the parent state (dry-run).
    r1 = imp.compute_sealed_state_root(block)
    assert r1 != ZERO32 and len(r1) == 32          # non-zero: self-gate will fire

    # Revert-clean: sealing left the live state untouched.
    assert imp.state_db.get_balance(SENDER) == 10_000_000
    assert imp.state_db.get_balance(RECIPIENT) == 0

    # Deterministic across repeated seals.
    assert imp.compute_sealed_state_root(block) == r1

    # seal == validate: the REAL apply produces the identical root.
    assert imp._apply_block_state(block) is True
    assert compute_state_root(imp.state_db) == r1
    # ...and the real apply actually moved the funds.
    assert imp.state_db.get_balance(RECIPIENT) == 1_000_000
    assert imp.state_db.get_balance(SENDER) == 9_000_000


def test_seal_reflects_block_contents(tmp_path: Path) -> None:
    # Different block contents ⇒ different sealed root (the root commits execution).
    imp = _fresh(tmp_path, 10_000_000)
    r_a = imp.compute_sealed_state_root(
        Block(header=_plain_header(1), txs=(_transfer(1_000_000),), proofs=(), receipts=None)
    )
    r_b = imp.compute_sealed_state_root(
        Block(header=_plain_header(1), txs=(_transfer(2_000_000),), proofs=(), receipts=None)
    )
    assert r_a != r_b
    # Still revert-clean after both seals.
    assert imp.state_db.get_balance(SENDER) == 10_000_000


if __name__ == "__main__":
    import pytest, sys
    sys.exit(pytest.main([__file__, "-q"]))
