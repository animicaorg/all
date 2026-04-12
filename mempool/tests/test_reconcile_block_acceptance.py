from __future__ import annotations

from types import SimpleNamespace

from mempool.reconcile import on_block_accepted
from rpc.methods import tx as tx_methods


class _FakeMempool:
    def __init__(self, known_hashes: list[str]) -> None:
        self._known = set(known_hashes)
        self.removed_calls: list[list[str]] = []
        self.revalidated = False

    def remove_included(self, tx_hashes):
        tx_hashes = [str(h).lower() for h in tx_hashes]
        self.removed_calls.append(tx_hashes)
        removed = 0
        for tx_hash in tx_hashes:
            if tx_hash in self._known:
                self._known.remove(tx_hash)
                removed += 1
        return removed

    def snapshot(self, *, limit: int = 1000):  # pragma: no cover - compatibility
        return SimpleNamespace(entries=[])

    def revalidate(self):
        self.revalidated = True
        return {"evicted": 0}


class _FakeRelay:
    def __init__(self) -> None:
        self.confirmed_calls: list[list[str]] = []

    def on_block_accepted(self, txids):
        normalized = [str(txid).lower() for txid in txids]
        self.confirmed_calls.append(normalized)
        return {"confirmed": len(normalized)}


def test_block_acceptance_reconciles_mempool_legacy_and_relay(monkeypatch) -> None:
    raw_a = b"reconcile-a"
    raw_b = b"reconcile-b"
    hash_a = "0x" + ("11" * 32)
    hash_b = "0x" + ("22" * 32)

    tx_methods._FALLBACK_PENDING.clear()
    tx_methods._FALLBACK_PENDING_TS.clear()
    tx_methods._FALLBACK_PENDING[hash_a] = raw_a
    tx_methods._FALLBACK_PENDING[hash_b] = raw_b
    tx_methods._FALLBACK_PENDING_TS[hash_a] = 1.0
    tx_methods._FALLBACK_PENDING_TS[hash_b] = 1.0
    monkeypatch.setattr(tx_methods, "_PEND", None, raising=False)

    fake_mempool = _FakeMempool([hash_a, hash_b])
    fake_relay = _FakeRelay()
    fake_p2p = SimpleNamespace(tx_relay_service=fake_relay)
    fake_ctx = SimpleNamespace(
        mempool=fake_mempool,
        p2p_service=fake_p2p,
        core_p2p_service=None,
    )

    from rpc import deps

    monkeypatch.setattr(deps, "get_ctx", lambda: fake_ctx)

    result = on_block_accepted({"tx_hashes": [hash_a, hash_b]}, None)

    assert result["relay_confirmed"] == 2
    assert fake_relay.confirmed_calls and fake_relay.confirmed_calls[0] == [hash_a, hash_b]
    assert fake_mempool.revalidated is True
    # Included txs were removed from both authoritative mempool and legacy fallback.
    assert hash_a not in fake_mempool._known
    assert hash_b not in fake_mempool._known
    assert hash_a not in tx_methods._FALLBACK_PENDING
    assert hash_b not in tx_methods._FALLBACK_PENDING
