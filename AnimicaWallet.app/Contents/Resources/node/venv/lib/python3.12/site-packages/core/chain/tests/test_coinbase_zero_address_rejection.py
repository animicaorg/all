from types import SimpleNamespace

import pytest

from core.chain.block_import import BlockImportError, _validate_coinbase_outputs_nonzero


def _coinbase_tx(to_addr: bytes):
    return SimpleNamespace(
        unsigned=SimpleNamespace(
            kind=3,
            payload=SimpleNamespace(to=to_addr, amount=1),
        )
    )


def test_rejects_zero_address_coinbase_output():
    block = SimpleNamespace(txs=[_coinbase_tx(b"\x00" * 32)])
    with pytest.raises(BlockImportError, match="zero-address"):
        _validate_coinbase_outputs_nonzero(block)


def test_accepts_non_zero_coinbase_output():
    block = SimpleNamespace(txs=[_coinbase_tx(b"\x11" * 32)])
    _validate_coinbase_outputs_nonzero(block)
