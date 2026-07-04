"""
ANM-H07 / ANM-H10 / ANM-M08 / ANM-M09 — mempool admission hardening.

These tests prove BOTH the fail-closed behavior (spam/forgery rejected) AND
backward-compatibility (the tx shapes the live wallet/miner actually produce —
maxFee/gasPrice>=1, gasLimit=21000, signed Tx objects — are still admitted).

All of the enforcement here is LOCAL mempool admission/relay policy: it does not
change block validity (core/chain/block_import.py is untouched).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.types.tx import Tx
from mempool.pool import Pool, PoolConfig
from mempool.types import TxMeta
from mempool.errors import AdmissionError, FeeTooLow, InsufficientFundsPending
from rpc.mempool_service import MempoolService


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_service(
    *,
    chain_id: int = 1,
    min_gas_price_wei: int = 0,
    state_db=None,
    per_sender_max_txs: int = 0,
    verify_signatures: bool | None = False,
) -> MempoolService:
    pool = Pool(cfg=PoolConfig(max_txs=1000, max_bytes=8 * 1024 * 1024))
    return MempoolService(
        pool=pool,
        chain_id=chain_id,
        min_gas_price_wei=min_gas_price_wei,
        state_db=state_db,
        tx_index=None,
        persist_enabled=False,
        per_sender_max_txs=per_sender_max_txs,
        verify_signatures=verify_signatures,
    )


def _tx(
    *,
    chain_id: int = 1,
    nonce: int = 0,
    gas_price: int = 1,
    gas_limit: int = 21000,
    sender: bytes = b"\x11" * 32,
    to: bytes = b"\x22" * 32,
    amount: int = 1,
):
    tx = Tx.transfer(
        chain_id=chain_id,
        nonce=nonce,
        gas_price=gas_price,
        gas_limit=gas_limit,
        sender=sender,
        to=to,
        amount=amount,
        version=1,
    )
    return tx, tx.to_cbor()


class _FakeState:
    def __init__(self, balance: int, nonce: int = 0) -> None:
        self._balance = balance
        self._nonce = nonce

    def get_balance(self, _addr) -> int:
        return self._balance

    def get_nonce(self, _addr) -> int:
        return self._nonce


# ---------------------------------------------------------------------------
# ANM-H07 — minimum-fee floor
# ---------------------------------------------------------------------------


def test_h07_zero_fee_rejected_on_mainnet() -> None:
    """A zero-fee tx must be rejected by the default 1-wei floor on chain_id=1."""
    svc = _make_service(chain_id=1, min_gas_price_wei=0)
    assert svc.min_gas_price_wei == 1  # floor defaulted for mainnet
    tx, raw = _tx(gas_price=0)
    with pytest.raises(FeeTooLow):
        svc.submit(tx=tx, raw=raw, local=True)


def test_h07_live_wallet_fee_is_accepted() -> None:
    """Backward-compat: the live wallet's default maxFee/gasPrice=1 is admitted."""
    svc = _make_service(chain_id=1, min_gas_price_wei=0)
    tx, raw = _tx(gas_price=1)
    h = svc.submit(tx=tx, raw=raw, local=True)
    assert svc.has_hash(h)


def test_h07_floor_opt_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """Backward-compat escape hatch: MEMPOOL_MIN_FEE_WEI=0 disables the floor."""
    monkeypatch.setenv("MEMPOOL_MIN_FEE_WEI", "0")
    svc = _make_service(chain_id=1, min_gas_price_wei=0)
    assert svc.min_gas_price_wei == 0
    tx, raw = _tx(gas_price=0)
    # No floor => zero-fee admitted (state_db=None so no funds gate either).
    assert svc.submit(tx=tx, raw=raw, local=True) == "0x" + tx.txid().hex()


def test_h07_floor_not_applied_off_mainnet() -> None:
    """The default floor is scoped to mainnet; other chains keep min=0."""
    svc = _make_service(chain_id=1337, min_gas_price_wei=0)
    assert svc.min_gas_price_wei == 0


# ---------------------------------------------------------------------------
# ANM-H07 — per-sender pending cap
# ---------------------------------------------------------------------------


def test_h07_per_sender_cap_enforced() -> None:
    state = _FakeState(balance=10**18, nonce=0)
    svc = _make_service(chain_id=1, state_db=state, per_sender_max_txs=2)

    for n in (0, 1):
        tx, raw = _tx(nonce=n)
        assert svc.submit(tx=tx, raw=raw, local=True)

    # Third distinct nonce from the same sender exceeds the cap.
    tx, raw = _tx(nonce=2)
    with pytest.raises(AdmissionError) as exc:
        svc.submit(tx=tx, raw=raw, local=True)
    assert exc.value.context.get("reason") == "too_many_pending_from_sender"


def test_h07_per_sender_cap_disabled_by_default() -> None:
    """Backward-compat: with no cap wired (=0), many pending txs are fine."""
    state = _FakeState(balance=10**18, nonce=0)
    svc = _make_service(chain_id=1, state_db=state, per_sender_max_txs=0)
    for n in range(5):
        tx, raw = _tx(nonce=n)
        assert svc.submit(tx=tx, raw=raw, local=True)


# ---------------------------------------------------------------------------
# ANM-H07 — funded-balance requirement
# ---------------------------------------------------------------------------


def test_h07_unfunded_sender_rejected() -> None:
    state = _FakeState(balance=0, nonce=0)
    svc = _make_service(chain_id=1, state_db=state)
    tx, raw = _tx(gas_price=1, nonce=0)
    with pytest.raises(InsufficientFundsPending):
        svc.submit(tx=tx, raw=raw, local=True)


def test_h07_funded_sender_accepted() -> None:
    state = _FakeState(balance=10**18, nonce=0)
    svc = _make_service(chain_id=1, state_db=state)
    tx, raw = _tx(gas_price=1, nonce=0)
    assert svc.has_hash(svc.submit(tx=tx, raw=raw, local=True))


# ---------------------------------------------------------------------------
# ANM-M09 — intrinsic-gas lower bound + block-gas upper bound
# ---------------------------------------------------------------------------


def test_m09_below_intrinsic_rejected() -> None:
    svc = _make_service(chain_id=1)
    tx, raw = _tx(gas_limit=1)  # < intrinsic (21000)
    with pytest.raises(AdmissionError) as exc:
        svc.submit(tx=tx, raw=raw, local=True)
    assert exc.value.context.get("reason") == "intrinsic_gas_too_low"


def test_m09_above_block_gas_rejected() -> None:
    svc = _make_service(chain_id=1)
    tx, raw = _tx(gas_limit=2_000_000_000)  # > default 1e9 cap
    with pytest.raises(AdmissionError) as exc:
        svc.submit(tx=tx, raw=raw, local=True)
    assert exc.value.context.get("reason") == "exceeds_block_gas"


def test_m09_normal_gas_accepted() -> None:
    """Backward-compat: the live 21000 gas limit sits exactly at intrinsic."""
    svc = _make_service(chain_id=1)
    tx, raw = _tx(gas_limit=21000)
    assert svc.has_hash(svc.submit(tx=tx, raw=raw, local=True))


# ---------------------------------------------------------------------------
# ANM-H10 — mandatory in-mempool signature verification
# ---------------------------------------------------------------------------


def test_h10_submit_rejects_invalid_signature_regardless_of_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    submit() must reject an invalid-signature tx even when the caller did not
    verify and even with the optional-verify env flags set.
    """
    monkeypatch.setenv("ANIMICA_PQ_VERIFY_OPTIONAL", "1")
    monkeypatch.setenv("ANIMICA_SKIP_PQ_VERIFY", "1")

    from rpc.methods import tx as tx_methods

    def _boom(*_a, **_k):
        raise ValueError("verification failed")

    monkeypatch.setattr(tx_methods, "_verify_pq_signature", _boom)

    # Mandatory verification is enabled by default for mainnet.
    svc = _make_service(chain_id=1, verify_signatures=None)
    tx, raw = _tx(gas_price=1)
    with pytest.raises(AdmissionError) as exc:
        svc.submit(tx=tx, raw=raw, local=True)
    assert exc.value.context.get("reason") == "invalid_signature"


def test_h10_submit_admits_when_verification_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Backward-compat: a tx whose signature verifies is still admitted."""
    from rpc.methods import tx as tx_methods

    monkeypatch.setattr(tx_methods, "_verify_pq_signature", lambda *a, **k: None)
    svc = _make_service(chain_id=1, verify_signatures=None)
    tx, raw = _tx(gas_price=1)
    assert svc.has_hash(svc.submit(tx=tx, raw=raw, local=True))


def test_h10_escape_hatch_removed_for_mainnet(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    ANIMICA_PQ_VERIFY_OPTIONAL must NOT bypass verification on chain_id=1 when
    the PQ backend is unavailable — it fails closed there, but still honors the
    dev flag on non-mainnet chains.
    """
    from rpc.methods import tx as tx_methods
    from rpc import errors as rpc_errors

    monkeypatch.setattr(tx_methods, "_pq_verify", None)
    monkeypatch.setattr(tx_methods, "_PQ_VERIFY_OPTIONAL", True)

    # Mainnet: fail closed.
    with pytest.raises(rpc_errors.InternalError):
        tx_methods._verify_pq_signature(None, {"tx": {}, "sigs": []}, chain_id=1)

    # Non-mainnet dev chain: escape hatch still honored (returns None).
    assert (
        tx_methods._verify_pq_signature(None, {"tx": {}, "sigs": []}, chain_id=2)
        is None
    )


# ---------------------------------------------------------------------------
# ANM-M08 — nonce-aware eviction (never strand a middle nonce)
# ---------------------------------------------------------------------------


def _add_pool_tx(pool: Pool, sender: str, nonce: int, fee: int) -> bytes:
    h = bytes([nonce]) + bytes.fromhex(sender[2:])[:31]
    tx = SimpleNamespace(hash=h, tx_hash=h)
    meta = TxMeta(
        sender=sender,
        gas_limit=21000,
        size_bytes=1000,
        nonce=nonce,
        effective_fee_wei=fee,
    )
    pool.add(tx, meta, is_local=True)
    return h


def _remaining_nonces(pool: Pool, sender: str) -> list[int]:
    return sorted(
        int(e.meta.nonce)
        for e in pool.index.get_by_sender(sender)
        if getattr(e.meta, "nonce", None) is not None
    )


def test_m08_eviction_never_strands_middle_nonce() -> None:
    """
    Under capacity pressure, the *highest* nonce is evicted first so the
    surviving nonces stay a contiguous prefix — a middle nonce is never dropped
    while a higher nonce for the same sender remains.
    """
    pool = Pool(cfg=PoolConfig(max_txs=2, max_bytes=64 * 1024 * 1024))
    sender = "0x" + "ab" * 32
    # Middle nonce (1) has the LOWEST fee: a naive fee/score eviction would drop
    # it, opening an unfillable gap. Nonce-aware eviction must not.
    _add_pool_tx(pool, sender, nonce=0, fee=100)
    _add_pool_tx(pool, sender, nonce=1, fee=1)
    _add_pool_tx(pool, sender, nonce=2, fee=50)

    remaining = _remaining_nonces(pool, sender)
    assert len(remaining) == 2
    # Contiguous prefix from the lowest nonce, no gap.
    assert remaining == [0, 1], remaining
    assert 2 not in remaining  # the tail was evicted, not the middle


def test_m08_multi_sender_single_eviction_pass_preserves_prefix() -> None:
    """
    A single eviction pass (over both senders at once) evicts highest nonces
    first, so each sender's survivors remain a gap-free prefix — even when the
    lowest-fee tx is a middle nonce that a naive score-only pass would drop.
    """
    # Populate above the eventual cap without per-add eviction, then run one
    # eviction pass. This isolates the eviction invariant from admission order.
    pool = Pool(cfg=PoolConfig(max_txs=100, max_bytes=64 * 1024 * 1024))
    a = "0x" + "aa" * 32
    b = "0x" + "bb" * 32
    for n, fee in ((0, 100), (1, 2), (2, 60)):
        _add_pool_tx(pool, a, nonce=n, fee=fee)
    for n, fee in ((0, 90), (1, 3), (2, 70)):
        _add_pool_tx(pool, b, nonce=n, fee=fee)

    # Tighten capacity and force one eviction pass (6 -> 4).
    pool.cfg = PoolConfig(max_txs=4, max_bytes=64 * 1024 * 1024)
    pool._eviction_pressure()

    assert len(pool.index) == 4
    for sender in (a, b):
        rem = _remaining_nonces(pool, sender)
        # Whatever survived is a gap-free prefix starting at 0.
        assert rem == list(range(len(rem))), (sender, rem)
