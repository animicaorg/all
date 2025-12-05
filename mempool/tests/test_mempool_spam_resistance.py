from __future__ import annotations

import random
import types

import pytest

from mempool.tests.test_mempool_fee_policy import (
    FakeTx, _admit, _contains_tx, _len_pool, _make_config,
    _monkeypatch_validation_and_priority, _new_pool, _pool_items)


def test_random_spam_resilience(monkeypatch: pytest.MonkeyPatch):
    _monkeypatch_validation_and_priority(monkeypatch)

    random.seed(1337)
    config = _make_config(max_txs=50)
    pool = _new_pool(config)

    senders = [bytes([i]) * 20 for i in range(1, 21)]

    for i in range(200):
        sender = random.choice(senders)
        nonce = random.randint(0, 100)
        fee = random.randint(1, 200)
        tx = FakeTx(sender, nonce, fee)
        _admit(pool, tx)

        if i % 10 == 0 and hasattr(pool, "evict_if_needed"):
            try:
                pool.evict_if_needed()
            except Exception:
                pass

    len_before = _len_pool(pool)
    bad_tx = types.SimpleNamespace()
    bad_tx.sender = None
    bad_tx.nonce = 0
    bad_tx.fee = 0
    bad_tx.size_bytes = 1
    bad_tx.hash = b"bad".ljust(32, b"\x00")
    bad_tx.tx_hash = bad_tx.hash
    bad_tx.__bytes__ = lambda self: b""

    with pytest.raises(Exception):
        _admit(pool, bad_tx)  # type: ignore[arg-type]

    assert _len_pool(pool) == len_before, "invalid transaction should not be admitted"

    assert _len_pool(pool) <= 50, "mempool should not exceed configured capacity"
    for tx in _pool_items(pool):
        assert isinstance(tx.sender, (bytes, bytearray)) and len(tx.sender) > 0
        assert isinstance(tx.nonce, int) and tx.nonce >= 0
        assert _contains_tx(pool, tx)
