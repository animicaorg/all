from rpc.pending_pool import InMemoryPendingPool


def test_in_memory_pending_pool_remove() -> None:
    pool = InMemoryPendingPool()
    tx_hash = "0xabc123"
    raw = b"raw_tx"

    pool.add_raw(tx_hash, raw)
    assert pool.get_raw(tx_hash) == raw

    assert pool.remove(tx_hash) is True
    assert pool.get_raw(tx_hash) is None
    assert pool.remove(tx_hash) is False


def test_mempool_getPending_reflects_pending_pool_removal(monkeypatch) -> None:
    from rpc.methods import mempool as mempool_methods
    from rpc.methods import tx as tx_methods

    pool = InMemoryPendingPool()
    tx_hash = "0xdeadbeef"
    raw = b"raw_tx"

    pool.add_raw(tx_hash, raw)
    monkeypatch.setattr(tx_methods, "_PEND", pool)

    assert mempool_methods.mempool_get_pending() == [tx_hash]

    pool.remove(tx_hash)
    assert mempool_methods.mempool_get_pending() == []
