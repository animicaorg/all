import pytest

from rpc.methods import miner as miner_mod
from rpc import errors as rpc_errors


def test_validate_payout_address_rejects_zero_hex():
    with pytest.raises(rpc_errors.InvalidParams):
        miner_mod._validate_payout_address("0x" + ("0" * 64))


def test_validate_payout_address_rejects_invalid():
    with pytest.raises(rpc_errors.InvalidParams):
        miner_mod._validate_payout_address("not-an-address")


def test_get_miner_address_requires_explicit_value(monkeypatch):
    monkeypatch.delenv("ANIMICA_MINER_ADDRESS", raising=False)
    with pytest.raises(rpc_errors.InvalidParams, match="Select payout address"):
        miner_mod._get_miner_address()
