import time

from core.utils.hash import sha3_256
from rpc.mempool_service import MempoolSnapshot
from mempool.select import PendingTxEntry
from rpc.methods.miner import _collect_mempool_entries


def test_collect_mempool_entries_includes_relayed_tx() -> None:
    raw = b"tx-relayed-to-miner"
    tx_hash_hex = "0x" + sha3_256(raw).hex()
    entry = PendingTxEntry(
        hash_hex=tx_hash_hex,
        raw=raw,
        tx={},
        received_at=time.time(),
        expires_at=None,
    )

    snapshot = MempoolSnapshot(entries=[entry], raw_by_hash={tx_hash_hex: raw}, total=1)

    class FakeMempool:
        def snapshot(self, limit: int = 1000) -> MempoolSnapshot:
            assert limit >= 1
            return snapshot

    class FakeCtx:
        mempool = FakeMempool()

    class DummyAdapter:
        pass

    pending_entries, pending_raw_by_hash, total = _collect_mempool_entries(
        ctx=FakeCtx(), adapter=DummyAdapter(), limit=1000
    )

    assert total == 1
    assert pending_raw_by_hash[tx_hash_hex] == raw
    assert any(entry.hash_hex == tx_hash_hex for entry in pending_entries)
