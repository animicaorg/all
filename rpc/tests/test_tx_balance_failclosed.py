"""7.1.8 node-local phantom-deposit guard: _validate_sufficient_balance must
reject unfundable transactions at submission (before mempool / force-chain),
reading the AUTHORITATIVE balance and failing CLOSED on the force-chain path.
"""
from __future__ import annotations

import pytest

from rpc.methods import tx as txmod
from rpc import errors as rpc_errors

SENDER = "anim1zqq3ayju98m6dv24emx9z6s03wllnsvn6m6exp5fe3cz263vmpqx4uc0yaxy7"


def _obj(value=1000, gas_limit=21000, max_fee=1):
    return {"value": value, "gasLimit": gas_limit, "maxFee": max_fee}


@pytest.fixture(autouse=True)
def _stub_sender(monkeypatch):
    monkeypatch.setattr(txmod, "_extract_sender_address", lambda obj: SENDER)


def _set_balance(monkeypatch, bal):
    monkeypatch.setattr(txmod, "_authoritative_sender_balance", lambda addr: bal)


def _set_force_chain(monkeypatch, on):
    monkeypatch.setattr(txmod, "_TX_SEND_FORCE_CHAIN", on)


# ------------------------------ core behavior ------------------------------

def test_sufficient_balance_passes(monkeypatch):
    _set_balance(monkeypatch, 10_000_000)
    _set_force_chain(monkeypatch, True)
    txmod._validate_sufficient_balance(_obj(value=1000, gas_limit=21000, max_fee=1))  # required=22000


def test_insufficient_balance_rejected(monkeypatch):
    # The phantom case: sender cannot cover value+fee. Must reject on BOTH paths.
    _set_balance(monkeypatch, 500)  # required = 1000 + 21000*1 = 22000
    _set_force_chain(monkeypatch, False)
    with pytest.raises(rpc_errors.InsufficientFunds):
        txmod._validate_sufficient_balance(_obj())


def test_unfundable_large_transfer_rejected(monkeypatch):
    # chen's exact shape: "send" 2.55M ANM from a ~1M ANM account.
    _set_balance(monkeypatch, 1_000_019_345_808_012)
    _set_force_chain(monkeypatch, True)
    with pytest.raises(rpc_errors.InsufficientFunds):
        txmod._validate_sufficient_balance(
            _obj(value=2_552_073_433_000_000, gas_limit=21000, max_fee=1)
        )


# --------------------- fail-closed vs skip on unverifiable ------------------

def test_unverifiable_balance_force_chain_rejects(monkeypatch):
    _set_balance(monkeypatch, None)  # state unavailable
    _set_force_chain(monkeypatch, True)
    with pytest.raises(rpc_errors.InsufficientFunds):
        txmod._validate_sufficient_balance(_obj())


def test_unverifiable_balance_no_force_chain_skips(monkeypatch):
    _set_balance(monkeypatch, None)
    _set_force_chain(monkeypatch, False)
    txmod._validate_sufficient_balance(_obj())  # skip (stateless front-end); no raise


# --------------------------- unresolved sender -----------------------------

def test_unresolved_sender_force_chain_rejects(monkeypatch):
    monkeypatch.setattr(txmod, "_extract_sender_address", lambda obj: None)
    _set_force_chain(monkeypatch, True)
    with pytest.raises(rpc_errors.InvalidTx):
        txmod._validate_sufficient_balance(_obj())


def test_unresolved_sender_no_force_chain_skips(monkeypatch):
    monkeypatch.setattr(txmod, "_extract_sender_address", lambda obj: None)
    _set_force_chain(monkeypatch, False)
    txmod._validate_sufficient_balance(_obj())  # no raise


# ------------------- authoritative reader prefers state_service -------------

def test_authoritative_reader_uses_state_service(monkeypatch):
    import rpc.state_service as ss
    monkeypatch.setattr(ss, "get_balance", lambda addr: 777)
    assert txmod._authoritative_sender_balance(SENDER) == 777


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
