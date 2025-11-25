from __future__ import annotations

import types
from typing import Any, Iterable, Optional

import pytest

pool_mod = pytest.importorskip("mempool.pool", reason="mempool.pool module not found")
reorg_mod = pytest.importorskip("mempool.reorg", reason="mempool.reorg module not found")


# -------------------------
# Helpers & scaffolding
# -------------------------

class FakeTx:
    """
    Minimal tx object for mempool tests.
    """
    def __init__(self, sender: bytes, nonce: int, fee: int, gas: int = 21_000, size_bytes: int = 120):
        self.sender = sender
        self.nonce = nonce
        self.fee = fee
        # gas synonyms
        self.gas = gas
        self.gas_limit = gas
        self.intrinsic_gas = gas
        # encoded size
        self.size_bytes = size_bytes
        # a stable-ish hash for lookups
        self.hash = (sender + nonce.to_bytes(8, "big"))[:32] or b"\xF1" * 32
        self.tx_hash = self.hash  # common alias

    def __bytes__(self) -> bytes:
        return b"\xEE" * self.size_bytes


ALICE = b"A" * 20
BOB = b"B" * 20
CARL = b"C" * 20


def _get_attr_any(obj: Any, names: Iterable[str]) -> Optional[Any]:
    for n in names:
        if hasattr(obj, n):
            return getattr(obj, n)
    return None


def _monkeypatch_validation_and_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Allow tx admission in tests; define priority as 'fee'.
    """
    try:
        validate = pytest.importorskip("mempool.validate")
        for name in (
            "validate_tx", "fast_stateless_check", "stateless_validate", "validate",
            "check_size", "check_chain_id", "check_gas_limits",
        ):
            if hasattr(validate, name):
                monkeypatch.setattr(validate, name, lambda *a, **k: True, raising=True)
        for name in ("estimate_encoded_size", "encoded_size", "tx_encoded_size", "get_encoded_size"):
            if hasattr(validate, name):
                monkeypatch.setattr(validate, name, lambda tx: len(bytes(tx)), raising=True)
        for name in ("precheck_pq_signature", "pq_precheck_verify", "verify_pq_signature", "pq_verify"):
            if hasattr(validate, name):
                monkeypatch.setattr(validate, name, lambda *a, **k: True, raising=True)
    except Exception:
        pass

    try:
        priority = pytest.importorskip("mempool.priority")
        for name in ("effective_priority", "priority_of", "calc_effective_priority"):
            if hasattr(priority, name):
                monkeypatch.setattr(priority, name, lambda tx: getattr(tx, "fee", 0), raising=True)
    except Exception:
        pass


def _make_config(*, max_txs: int | None = None, max_bytes: int | None = None) -> Any:
    try:
        from mempool import config as mp_config  # type: ignore
    except Exception:
        mp_config = None

    fields = {
        "max_txs": max_txs, "max_pool_txs": max_txs, "max_items": max_txs, "capacity": max_txs,
        "max_bytes": max_bytes, "max_pool_bytes": max_bytes, "capacity_bytes": max_bytes, "max_mem_bytes": max_bytes,
    }

    if mp_config:
        for Tname in ("MempoolConfig", "PoolConfig", "Limits", "MempoolLimits"):
            if hasattr(mp_config, Tname):
                T = getattr(mp_config, Tname)
                try:
                    return T(**{k: v for k, v in fields.items() if v is not None})  # type: ignore[call-arg]
                except Exception:
                    continue

    ns = types.SimpleNamespace()
    for k, v in fields.items():
        if v is not None:
            setattr(ns, k, v)
    return ns


def _new_pool(config: Any | None) -> Any:
    for cls_name in ("Pool", "Mempool", "TxPool", "PendingPool", "MemPool"):
        if hasattr(pool_mod, cls_name):
            cls = getattr(pool_mod, cls_name)
            try:
                if config is None:
                    return cls()
                try:
                    return cls(config=config)  # type: ignore[call-arg]
                except TypeError:
                    return cls(config)  # type: ignore[misc]
            except Exception:
                continue
    for name in ("pool", "mempool", "pending", "instance"):
        if hasattr(pool_mod, name):
            return getattr(pool_mod, name)
    pytest.skip("No known pool class/instance found in mempool.pool")
    raise RuntimeError  # pragma: no cover


def _admit(pool: Any, tx: FakeTx) -> None:
    for name in ("add", "admit", "ingest", "push", "put", "insert"):
        if hasattr(pool, name):
            fn = getattr(pool, name)
            try:
                fn(tx)
                return
            except TypeError:
                try:
                    fn(tx.sender, tx.nonce, tx)
                    return
                except Exception:
                    continue
    pytest.skip("No known add/admit method on pool")


def _pool_items(pool: Any) -> list[FakeTx]:
    # Iteration path
    try:
        seq = list(iter(pool))  # type: ignore[arg-type]
        if seq:
            out: list[FakeTx] = []
            for item in seq:
                if isinstance(item, FakeTx):
                    out.append(item); continue
                if hasattr(item, "tx") and isinstance(item.tx, FakeTx):
                    out.append(item.tx); continue
                if isinstance(item, (tuple, list)):
                    for x in item:
                        if isinstance(x, FakeTx):
                            out.append(x); break
            if out:
                return out
    except Exception:
        pass
    # Accessors
    for name in ("iter_all", "all", "txs", "items", "values", "pending"):
        if hasattr(pool, name):
            it = getattr(pool, name)
            try:
                data = list(it() if callable(it) else it)
            except Exception:
                continue
            out: list[FakeTx] = []
            for item in data:
                if isinstance(item, FakeTx):
                    out.append(item); continue
                if hasattr(item, "tx") and isinstance(item.tx, FakeTx):
                    out.append(item.tx); continue
                if isinstance(item, (tuple, list)):
                    for x in item:
                        if isinstance(x, FakeTx):
                            out.append(x); break
            return out
    pytest.skip("Could not enumerate items from pool; unknown API")
    return []  # pragma: no cover


def _contains_sender_nonce(pool: Any, sender: bytes, nonce: int) -> bool:
    for name in ("get", "get_tx", "by_sender_nonce", "lookup", "contains", "has"):
        if hasattr(pool, name):
            fn = getattr(pool, name)
            try:
                if name in ("contains", "has"):
                    try:
                        return bool(fn((sender, nonce)))  # type: ignore[misc]
                    except Exception:
                        pass
                got = fn(sender, nonce)  # type: ignore[misc]
                return bool(got)
            except Exception:
                continue
    return any(t.sender == sender and t.nonce == nonce for t in _pool_items(pool))


def _present_fee_for(pool: Any, sender: bytes, nonce: int) -> Optional[int]:
    best: Optional[int] = None
    for t in _pool_items(pool):
        if t.sender == sender and t.nonce == nonce:
            if best is None or t.fee > best:
                best = t.fee
    return best


def _count_by_sender_nonce(pool: Any) -> dict[tuple[bytes, int], int]:
    counts: dict[tuple[bytes, int], int] = {}
    for t in _pool_items(pool):
        key = (t.sender, t.nonce)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _make_block_like(txs: list[FakeTx]) -> Any:
    """
    Create a structure compatible with possible reorg APIs:
      - object with .txs
      - also expose .transactions, .body, and tuple/list variants
    """
    blk = types.SimpleNamespace()
    setattr(blk, "txs", txs)
    setattr(blk, "transactions", txs)
    setattr(blk, "body", txs)
    return blk


def _reinject(pool: Any, reverted_txs: list[FakeTx]) -> None:
    """
    Try various reorg reinjection APIs before falling back to naive re-add.
    """
    blk = _make_block_like(reverted_txs)

    # Module-level functions with pool + txs
    for name in ("reinject_txs", "reinject", "restore_txs", "readd_txs", "reinject_transactions"):
        fn = _get_attr_any(reorg_mod, [name])
        if callable(fn):
            try:
                fn(pool, reverted_txs)  # type: ignore[misc]
                return
            except TypeError:
                try:
                    fn(reverted_txs)  # type: ignore[misc]
                    return
                except Exception:
                    pass

    # Functions that take a block-like
    for name in ("reinject_from_block", "restore_from_block", "readd_from_block"):
        fn = _get_attr_any(reorg_mod, [name])
        if callable(fn):
            try:
                fn(pool, blk)  # type: ignore[misc]
                return
            except TypeError:
                try:
                    fn(blk)  # type: ignore[misc]
                    return
                except Exception:
                    pass

    # Higher-level "handle_reorg" taking reverted blocks
    for name in ("handle_reorg", "on_reorg", "process_reorg"):
        fn = _get_attr_any(reorg_mod, [name])
        if callable(fn):
            for kwargs in (
                dict(reverted_blocks=[blk], new_blocks=[]),
                dict(old_blocks=[blk], new_blocks=[]),
                dict(disconnected=[blk], connected=[]),
            ):
                try:
                    fn(pool, **kwargs)  # type: ignore[misc]
                    return
                except TypeError:
                    try:
                        fn(**kwargs)  # type: ignore[misc]
                        return
                    except Exception:
                        continue

    # Pool methods
    for name in ("reinject", "restore", "on_reorg", "readd"):
        if hasattr(pool, name):
            m = getattr(pool, name)
            try:
                m(reverted_txs)  # type: ignore[misc]
                return
            except Exception:
                pass

    # Fallback: naive re-admission
    for tx in reverted_txs:
        try:
            _admit(pool, tx)
        except Exception:
            # ignore per-sender/nonce duplicates — policy will decide
            pass


# -------------------------
# Tests
# -------------------------

def test_reorg_reinject_basic(monkeypatch: pytest.MonkeyPatch):
    """
    Reorg reinjects reverted transactions back into the pool (no duplicates).
    """
    _monkeypatch_validation_and_priority(monkeypatch)

    pool = _new_pool(config=_make_config(max_txs=100, max_bytes=10_000_000))

    a0 = FakeTx(ALICE, 0, fee=10)
    b0 = FakeTx(BOB, 0, fee=20)

    # Simulate that these were mined and removed; pool starts empty.
    assert not _contains_sender_nonce(pool, ALICE, 0)
    assert not _contains_sender_nonce(pool, BOB, 0)

    _reinject(pool, [a0, b0])

    assert _contains_sender_nonce(pool, ALICE, 0), "ALICE/0 should be back after reorg reinject"
    assert _contains_sender_nonce(pool, BOB, 0), "BOB/0 should be back after reorg reinject"

    # No duplicate entries per (sender, nonce)
    counts = _count_by_sender_nonce(pool)
    assert counts.get((ALICE, 0), 0) == 1
    assert counts.get((BOB, 0), 0) == 1


def test_reorg_does_not_downgrade_replacements(monkeypatch: pytest.MonkeyPatch):
    """
    If a higher-fee replacement is already in the pool for (sender, nonce),
    reinjecting a lower-fee reverted tx must not replace it.
    """
    _monkeypatch_validation_and_priority(monkeypatch)

    pool = _new_pool(config=_make_config(max_txs=100, max_bytes=10_000_000))

    high = FakeTx(ALICE, 0, fee=1_000)
    low = FakeTx(ALICE, 0, fee=10)

    # High-fee already present (e.g., arrived after the reverted block)
    _admit(pool, high)
    assert _contains_sender_nonce(pool, ALICE, 0)
    assert _present_fee_for(pool, ALICE, 0) == 1_000

    # Reorg tries to re-inject the lower-fee tx
    _reinject(pool, [low])

    # The pool should still reflect the higher fee for (ALICE, 0)
    fee_now = _present_fee_for(pool, ALICE, 0)
    assert fee_now is not None and fee_now >= high.fee, f"replacement downgraded: had {high.fee}, now {fee_now}"

    # Also ensure we don't end up with two entries for the same (sender, nonce)
    counts = _count_by_sender_nonce(pool)
    assert counts.get((ALICE, 0), 0) == 1, f"duplicate entries for ALICE/0 after reinject: {counts.get((ALICE,0),0)}"


def test_reorg_reinject_multiple_blocks_and_conflicts(monkeypatch: pytest.MonkeyPatch):
    """
    Reinject handles multiple reverted blocks; non-conflicting txs are restored,
    conflicting ones respect pool policy (keep highest fee).
    """
    _monkeypatch_validation_and_priority(monkeypatch)

    pool = _new_pool(config=_make_config(max_txs=100, max_bytes=10_000_000))

    # First reverted block: ALICE/0 (fee 100), BOB/0 (fee 50)
    a0_blk = FakeTx(ALICE, 0, fee=100)
    b0_blk = FakeTx(BOB, 0, fee=50)

    # Second reverted block: CARL/0 (fee 70), ALICE/0 lower-fee duplicate (fee 30)
    c0_blk = FakeTx(CARL, 0, fee=70)
    a0_low = FakeTx(ALICE, 0, fee=30)

    # Meanwhile, pool already got a replacement for ALICE/0 with even higher fee
    a0_high = FakeTx(ALICE, 0, fee=200)
    _admit(pool, a0_high)

    # Reinject from two "blocks"
    _reinject(pool, [a0_blk, b0_blk])
    _reinject(pool, [c0_blk, a0_low])

    # Expect BOB/0 and CARL/0 present
    assert _contains_sender_nonce(pool, BOB, 0)
    assert _contains_sender_nonce(pool, CARL, 0)

    # ALICE/0 should remain at highest fee seen (200), not downgraded by 100 or 30
    fee_alice = _present_fee_for(pool, ALICE, 0)
    assert fee_alice is not None and fee_alice >= 200

    # No duplicate (sender, nonce) entries
    counts = _count_by_sender_nonce(pool)
    assert counts.get((ALICE, 0), 0) == 1
    assert counts.get((BOB, 0), 0) == 1
    assert counts.get((CARL, 0), 0) == 1
