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
