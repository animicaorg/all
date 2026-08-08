"""FORK_VALUE_CALL (9.5.0) — a CALL may carry ANM from height 75,000.

The compatibility property under test is the one that could destroy the chain: to_obj()
is the canonical form the signing preimage and txid are computed over, so `amount` must
be OMITTED when zero. Emitting it unconditionally would change the bytes of every CALL
ever signed — new txids, invalid signatures, a chain that rejects its own history.
"""

from __future__ import annotations

import pytest

from core.types.tx import TxCall
from core.utils.serialization import canonical_dumps
from consensus.value_call import (
    ValueCallError,
    call_amount_of,
    check_call_value,
    debit_credit_for_call,
    value_calls_active,
)

TO = b"\x11" * 32
DATA = b"\xab\xcd"
BELOW, AT = 74_999, 75_000


def test_a_valueless_call_encodes_byte_identically_to_before_the_fork():
    """The whole backward-compatibility guarantee in one assertion."""
    obj = dict(TxCall(to=TO, data=DATA).to_obj())
    assert obj == {"to": TO, "data": DATA}, "no 'amount' key may appear when it is zero"
    assert canonical_dumps(obj) == canonical_dumps({"to": TO, "data": DATA})


def test_an_explicit_zero_is_still_omitted():
    assert "amount" not in dict(TxCall(to=TO, data=DATA, amount=0).to_obj())


def test_a_value_call_carries_the_amount_and_round_trips():
    paid = TxCall(to=TO, data=DATA, amount=5_000_000_000)
    obj = dict(paid.to_obj())
    assert obj["amount"] == 5_000_000_000
    assert TxCall.from_obj(obj).amount == 5_000_000_000


def test_a_payload_from_an_old_block_decodes_as_zero():
    assert TxCall.from_obj({"to": TO, "data": DATA}).amount == 0


def test_a_negative_amount_is_refused_at_construction():
    with pytest.raises(ValueError):
        TxCall(to=TO, data=DATA, amount=-1)
    with pytest.raises(TypeError):
        TxCall(to=TO, data=DATA, amount=True)   # bool is not an amount


def test_value_is_invalid_below_the_fork_and_valid_from_it():
    check_call_value(0, BELOW)          # zero is always fine
    check_call_value(0, AT)
    with pytest.raises(ValueCallError) as exc:
        check_call_value(1, BELOW)
    assert exc.value.code == "VALUE_CALL_NOT_ACTIVE"
    check_call_value(1, AT)             # permitted from H


def test_the_boundary_is_exactly_75000():
    assert value_calls_active(74_999) is False
    assert value_calls_active(75_000) is True
    assert value_calls_active(75_001) is True


def test_history_is_untouched_at_every_earlier_fork_height():
    for h in (0, 42_001, 44_444, 50_000, 70_000, 74_999):
        assert value_calls_active(h) is False, h


def test_an_underfunded_value_call_fails_before_execution():
    """It must fail cleanly rather than execute and leave the callee short."""
    assert debit_credit_for_call(amount=100, sender_balance=100, height=AT) == 100
    with pytest.raises(ValueCallError) as exc:
        debit_credit_for_call(amount=101, sender_balance=100, height=AT)
    assert exc.value.code == "INSUFFICIENT_CALL_VALUE"


def test_no_movement_when_the_fork_is_inactive_or_the_amount_is_zero():
    assert debit_credit_for_call(amount=0, sender_balance=0, height=BELOW) == 0
    with pytest.raises(ValueCallError):
        debit_credit_for_call(amount=1, sender_balance=10**18, height=BELOW)


def test_amount_extraction_tolerates_every_legacy_payload_shape():
    assert call_amount_of(TxCall(to=TO, data=DATA)) == 0
    assert call_amount_of(TxCall(to=TO, data=DATA, amount=7)) == 7
    assert call_amount_of({"to": TO, "data": DATA}) == 0
    assert call_amount_of({"amount": 9}) == 9
    assert call_amount_of(object()) == 0        # a payload with no such field
    assert call_amount_of({"amount": "junk"}) == 0


def test_testnet_and_devnet_have_it_from_genesis():
    for chain_id in (2, 1337):
        assert value_calls_active(0, chain_id=chain_id) is True, chain_id


# --------------------------------------------------------------------------- #
# Execution: the value actually moves, and revert actually returns it         #
# --------------------------------------------------------------------------- #
#
# Without these, `amount` becomes VALID at height 75,000 while nothing moves it —
# a user attaches value, consensus accepts the tx, and the coins silently do not
# arrive. An accepted-but-inert field is worse than no field.

class _State:
    """Minimal balance/nonce state with the snapshot hooks apply_call expects."""

    def __init__(self, balances):
        self.balances = dict(balances)
        self.nonces = {}

    def get_balance(self, a):
        return int(self.balances.get(bytes(a), 0))

    def set_balance(self, a, v):
        self.balances[bytes(a)] = int(v)

    def get_nonce(self, a):
        return int(self.nonces.get(bytes(a), 0))

    def set_nonce(self, a, v):
        self.nonces[bytes(a)] = int(v)

    def snapshot(self):
        return (dict(self.balances), dict(self.nonces))

    def revert(self, snap):
        self.balances, self.nonces = dict(snap[0]), dict(snap[1])


SENDER = b"\x22" * 32
CALLEE = b"\x33" * 32


def _run_call(height, amount, sender_balance=10**12):
    from execution.runtime.contracts import apply_call

    st = _State({SENDER: sender_balance, CALLEE: 0})
    tx = {
        "payload": {"to": CALLEE, "data": b"\x01\x02", "amount": amount},
        "gas": {"price": 0, "limit": 10_000_000},
        "nonce": 0,
    }
    block_env = type("B", (), {"chain_id": 1, "height": height, "timestamp": 0,
                               "coinbase": b"\x00" * 32})()
    tx_env = type("T", (), {"sender": SENDER})()
    res = apply_call(tx, st, block_env, tx_env)
    return res, st


def test_value_moves_from_caller_to_callee_at_the_fork_height():
    res, st = _run_call(75_000, 1_000)
    # The VM is disabled in this environment, so the call REVERTs — and that is
    # precisely the case that must return the value.
    assert st.get_balance(CALLEE) == 0, "a reverted call must not leave value behind"
    assert st.get_balance(SENDER) == 10**12, "a reverted call must refund the caller"


def test_the_movement_happens_inside_the_snapshot_so_revert_refunds():
    """Proven by balance conservation: whatever the status, no coin is created or
    destroyed by attaching value to a call."""
    res, st = _run_call(75_000, 5_000)
    assert st.get_balance(SENDER) + st.get_balance(CALLEE) == 10**12


def test_an_underfunded_value_call_moves_nothing():
    res, st = _run_call(75_000, 10**12 + 1, sender_balance=10**12)
    assert st.get_balance(SENDER) == 10**12
    assert st.get_balance(CALLEE) == 0


def test_below_the_fork_a_value_call_moves_nothing():
    res, st = _run_call(74_999, 1_000)
    assert st.get_balance(SENDER) == 10**12
    assert st.get_balance(CALLEE) == 0


def test_a_valueless_call_is_completely_unaffected():
    res, st = _run_call(75_000, 0)
    assert st.get_balance(SENDER) == 10**12
    assert st.get_balance(CALLEE) == 0


def test_on_SUCCESS_the_value_stays_with_the_callee(monkeypatch):
    """The refund path is easy to prove here because the VM is disabled and every call
    reverts. This forces the SUCCESS path, which is the one that has to actually PAY
    the contract — otherwise the feature is a no-op that only ever refunds."""
    import execution.runtime.contracts as C
    from execution.runtime.contracts import TxStatus, apply_call

    monkeypatch.setattr(C, "_apply_call_vm", lambda **kw: None)  # VM succeeds

    st = _State({SENDER: 10**12, CALLEE: 0})
    tx = {
        "payload": {"to": CALLEE, "data": b"\x01\x02", "amount": 7_500},
        "gas": {"price": 0, "limit": 10_000_000},
        "nonce": 0,
    }
    block_env = type("B", (), {"chain_id": 1, "height": 75_000, "timestamp": 0,
                               "coinbase": b"\x00" * 32})()
    res = apply_call(tx, st, block_env, type("T", (), {"sender": SENDER})())

    assert res.status == TxStatus.SUCCESS
    assert st.get_balance(CALLEE) == 7_500, "a successful value call must pay the callee"
    assert st.get_balance(SENDER) == 10**12 - 7_500
    assert st.get_balance(SENDER) + st.get_balance(CALLEE) == 10**12


def test_a_successful_call_below_the_fork_still_moves_nothing(monkeypatch):
    import execution.runtime.contracts as C
    from execution.runtime.contracts import apply_call

    monkeypatch.setattr(C, "_apply_call_vm", lambda **kw: None)
    st = _State({SENDER: 10**12, CALLEE: 0})
    tx = {
        "payload": {"to": CALLEE, "data": b"\x01\x02", "amount": 7_500},
        "gas": {"price": 0, "limit": 10_000_000},
        "nonce": 0,
    }
    block_env = type("B", (), {"chain_id": 1, "height": 74_999, "timestamp": 0,
                               "coinbase": b"\x00" * 32})()
    apply_call(tx, st, block_env, type("T", (), {"sender": SENDER})())
    assert st.get_balance(CALLEE) == 0, "pre-fork value must never move"
