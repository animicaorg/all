from __future__ import annotations

import types
from typing import Any, Iterable, Optional

import pytest

pool_mod = pytest.importorskip("mempool.pool", reason="mempool.pool module not found")
evict_mod = pytest.importorskip("mempool.evict", reason="mempool.evict module not found")
errors = pytest.importorskip("mempool.errors", reason="mempool.errors module not found")

# Prefer specific error types if the package defines them.
ERR_ADMISSION = getattr(errors, "AdmissionError", Exception)
ERR_EVICTION = getattr(errors, "DoSError", ERR_ADMISSION)


# -------------------------
# Helpers & test scaffolding
# -------------------------

class FakeTx:
    """
    Minimal tx object with sender, nonce, fee, and an encoded size.
    """
    def __init__(self, sender: bytes, nonce: int, fee: int, size_bytes: int = 100):
        self.sender = sender
        self.nonce = nonce
        self.fee = fee
        self.size_bytes = size_bytes
        # a stable-ish hash for membership lookups in some pools
        self.hash = (sender + nonce.to_bytes(8, "big"))[:32] or b"\xCD" * 32
        self.tx_hash = self.hash  # common alias

    def __bytes__(self) -> bytes:  # used for size checks
        return b"\xEE" * self.size_bytes


ALICE = b"A" * 20
BOB = b"B" * 20


def _get_attr_any(obj: Any, names: Iterable[str]) -> Optional[Any]:
    for n in names:
        if hasattr(obj, n):
            return getattr(obj, n)
    return None


def _monkeypatch_validation_and_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Make stateless validation permissive and priority deterministic (fee).
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


def _make_config(
    *,
    max_txs: int | None = None,
    max_bytes: int | None = None,
    per_sender_cap: int | None = None,
) -> Any:
    """
    Build a config/limits object compatible with the pool/evict modules.
    Sets a variety of synonym fields to maximize compatibility.
    """
    # Try dataclass/typed config in mempool.config
    try:
        from mempool import config as mp_config  # type: ignore
    except Exception:
        mp_config = None

    field_map = {
        "max_txs": max_txs, "max_pool_txs": max_txs, "max_items": max_txs, "capacity": max_txs,
        "max_bytes": max_bytes, "max_pool_bytes": max_bytes, "capacity_bytes": max_bytes, "max_mem_bytes": max_bytes,
        "per_sender_cap": per_sender_cap, "per_sender_limit": per_sender_cap,
        "max_per_sender": per_sender_cap, "sender_cap": per_sender_cap,
    }

    # patch module-level constants if present
    if mp_config:
        for name, val in field_map.items():
            if val is not None and hasattr(mp_config, name.upper()):
                try:
                    setattr(mp_config, name.upper(), val)
                except Exception:
                    pass

        for type_name in ("MempoolConfig", "PoolConfig", "Limits", "MempoolLimits"):
            if hasattr(mp_config, type_name):
                T = getattr(mp_config, type_name)
                try:
                    # Try to construct with keyword subset it accepts
                    kwargs = {k: v for k, v in field_map.items() if v is not None}
                    return T(**kwargs)  # type: ignore[call-arg]
                except Exception:
                    continue

    # Fallback: SimpleNamespace with a bunch of synonymous attributes
    ns = types.SimpleNamespace()
    for k, v in field_map.items():
        if v is not None:
            setattr(ns, k, v)
    return ns


def _new_pool(config: Any | None) -> Any:
    """
    Instantiate whichever pool class the module exposes, passing config if accepted.
    """
    for cls_name in ("Pool", "Mempool", "TxPool", "PendingPool", "MemPool"):
        if hasattr(pool_mod, cls_name):
            cls = getattr(pool_mod, cls_name)
            for args in ((config,), tuple()):
                try:
                    if config is None:
                        return cls()
                    try:
                        return cls(config=config)  # type: ignore[call-arg]
                    except TypeError:
                        return cls(*args)  # type: ignore[misc]
                except Exception:
                    continue
    # Fallback to module-scope instance if present
    for name in ("pool", "mempool", "pending", "instance"):
        if hasattr(pool_mod, name):
            return getattr(pool_mod, name)
    pytest.skip("No known pool class/instance found in mempool.pool")
    raise RuntimeError  # pragma: no cover


def _admit(pool: Any, tx: FakeTx) -> Optional[str]:
    """
    Add/admit a tx using whichever method exists. Normalize to a status string when possible.
    """
    for name in ("add", "admit", "ingest", "push", "put", "insert"):
        if hasattr(pool, name):
            fn = getattr(pool, name)
            try:
                res = fn(tx)
            except TypeError:
                try:
                    res = fn(tx.sender, tx.nonce, tx)
                except Exception:
                    res = fn(tx)
            if isinstance(res, str):
                return res.lower()
            if isinstance(res, tuple) and res and isinstance(res[0], str):
                return res[0].lower()
            if res is True:
                return "ok"
            return None
    pytest.skip("No known add/admit method on pool")
    return None  # pragma: no cover


def _evict_enforce_limits(pool: Any, config: Any | None) -> None:
    """
    Run eviction using whatever API exists. Repeat a few times to reach a fixed point.
    """
    funcs = [
        "evict_if_needed", "evict_to_fit", "enforce_limits", "run_eviction", "evict",
    ]
    meths = [
        "evict_if_needed", "evict_to_fit", "enforce_limits", "run_eviction", "evict",
    ]
    for _ in range(5):
        called = False
        for name in funcs:
            if hasattr(evict_mod, name):
                fn = getattr(evict_mod, name)
                try:
                    if config is None:
                        fn(pool)  # type: ignore[misc]
                    else:
                        try:
                            fn(pool, config)  # type: ignore[misc]
                        except TypeError:
                            fn(pool)
                    called = True
                except Exception:
                    # keep trying
                    continue
        for name in meths:
            if hasattr(pool, name):
                m = getattr(pool, name)
                try:
                    m()  # type: ignore[misc]
                    called = True
                except Exception:
                    continue
        if called:
            # continue to next round to allow cascaded evictions; after 5 rounds we assume stable
            continue
        # Nothing callable found; skip test politely
        pytest.skip("No eviction API found in mempool.evict or pool")
        return


def _pool_items(pool: Any) -> list[FakeTx]:
    """
    Attempt to enumerate txs in the pool.
    """
    # If pool is iterable:
    try:
        seq = list(iter(pool))  # type: ignore[arg-type]
        if seq:
            # Unwrap common shapes
            out: list[FakeTx] = []
            for item in seq:
                if isinstance(item, FakeTx):
                    out.append(item)
                    continue
                if isinstance(item, (tuple, list)):
                    # (sender, nonce, tx) or (hash, tx) etc.
                    for x in item:
                        if isinstance(x, FakeTx):
                            out.append(x)
                            break
                elif hasattr(item, "tx") and isinstance(item.tx, FakeTx):
                    out.append(item.tx)
            if out:
                return out
    except Exception:
        pass

    # Try explicit accessors
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
                    out.append(item)
                    continue
                if isinstance(item, (tuple, list)):
                    for x in item:
                        if isinstance(x, FakeTx):
                            out.append(x)
                            break
                elif hasattr(item, "tx") and isinstance(item.tx, FakeTx):
                    out.append(item.tx)
            return out

    # If we absolutely cannot enumerate, we can't perform membership tests reliably.
    pytest.skip("Could not enumerate items from pool; unknown API")
    return []  # pragma: no cover


def _contains_tx(pool: Any, tx: FakeTx) -> bool:
    """
    Membership check using lookups or enumeration.
    """
    for name in ("get", "get_tx", "by_sender_nonce", "lookup", "contains", "has"):
        if hasattr(pool, name):
            fn = getattr(pool, name)
            try:
                if name in ("contains", "has"):
                    try:
                        return bool(fn(tx))  # type: ignore[misc]
                    except Exception:
                        return bool(fn(tx.hash))  # type: ignore[misc]
                try:
                    got = fn(tx.sender, tx.nonce)  # type: ignore[misc]
                except TypeError:
                    try:
                        got = fn(tx.hash)  # type: ignore[misc]
                    except Exception:
                        continue
                return bool(got)
            except Exception:
                continue
    # Fallback to enumeration
    return any(t is tx or (t.sender == tx.sender and t.nonce == tx.nonce) for t in _pool_items(pool))


def _count_by_sender(pool: Any) -> dict[bytes, int]:
    counts: dict[bytes, int] = {}
    for t in _pool_items(pool):
        counts[t.sender] = counts.get(t.sender, 0) + 1
    return counts


# -------------------------
# Tests
# -------------------------

def test_memory_pressure_eviction_keeps_high_fee(monkeypatch: pytest.MonkeyPatch):
    """
    When the pool exceeds max_txs, eviction should drop lowest-priority (fee) txs first.
    """
    _monkeypatch_validation_and_priority(monkeypatch)

    config = _make_config(max_txs=5, per_sender_cap=100)  # disable per-sender cap influence
    pool = _new_pool(config)

    txs = [
        FakeTx(ALICE, 0, 10),
        FakeTx(ALICE, 1, 40),
        FakeTx(ALICE, 2, 30),
        FakeTx(ALICE, 3, 20),
        FakeTx(BOB, 0, 15),
        FakeTx(BOB, 1, 45),
        FakeTx(BOB, 2, 35),
        FakeTx(BOB, 3, 25),
    ]
    for tx in txs:
        _admit(pool, tx)

    _evict_enforce_limits(pool, config)

    present = [tx for tx in txs if _contains_tx(pool, tx)]
    fees_present = sorted([tx.fee for tx in present])
    assert len(present) <= 5, f"pool still over capacity after eviction (len={len(present)})"

    # Expect the top-5 fees to remain
    top5 = sorted([tx.fee for tx in txs])[-5:]
    # None of the bottom-3 should remain
    bottom3 = sorted([tx.fee for tx in txs])[:3]
    assert all(f in top5 for f in fees_present), f"eviction kept non-top fees: kept={fees_present}, top5={top5}"
    assert all(not _contains_tx(pool, tx) for tx in txs if tx.fee in bottom3), "lowest-fee txs should have been evicted"


def test_per_sender_cap_enforced(monkeypatch: pytest.MonkeyPatch):
    """
    With a per-sender cap=2 and generous max_txs, exceeding the cap should evict
    lowest-fee txs from that sender, keeping the highest-fee ones.
    """
    _monkeypatch_validation_and_priority(monkeypatch)

    config = _make_config(max_txs=10, per_sender_cap=2)
    pool = _new_pool(config)

    a1 = FakeTx(ALICE, 0, 10)
    a2 = FakeTx(ALICE, 1, 20)
    a3 = FakeTx(ALICE, 2, 30)  # should be evicted under cap=2
    b1 = FakeTx(BOB, 0, 15)

    for tx in (a1, a2, a3, b1):
        _admit(pool, tx)

    _evict_enforce_limits(pool, config)

    counts = _count_by_sender(pool)
    assert counts.get(ALICE, 0) <= 2, f"per-sender cap not enforced for ALICE: {counts}"
    # Highest two fees for ALICE should remain
    assert _contains_tx(pool, a2) and _contains_tx(pool, a3) or _contains_tx(pool, a2) and _contains_tx(pool, a3), \
        "Expected highest-fee ALICE txs to remain under cap"
    # The lowest-fee (a1) should be gone if three were present
    assert not _contains_tx(pool, a1), "Lowest-fee ALICE tx should be evicted under per-sender cap=2"


def test_fairness_under_global_eviction(monkeypatch: pytest.MonkeyPatch):
    """
    Under global pressure (max_txs=6) with per_sender_cap=3,
    final distribution should respect the cap so that one sender cannot dominate.
    """
    _monkeypatch_validation_and_priority(monkeypatch)

    config = _make_config(max_txs=6, per_sender_cap=3)
    pool = _new_pool(config)

    alice_txs = [FakeTx(ALICE, i, fee) for i, fee in enumerate([10, 20, 30, 40, 50])]
    bob_txs = [FakeTx(BOB, i, fee) for i, fee in enumerate([15, 30, 45, 60])]

    for tx in alice_txs + bob_txs:
        _admit(pool, tx)

    _evict_enforce_limits(pool, config)

    counts = _count_by_sender(pool)
    total = sum(counts.values())
    assert total <= 6, f"pool still over capacity after eviction (len={total})"
    assert counts.get(ALICE, 0) <= 3 and counts.get(BOB, 0) <= 3, f"per-sender cap not respected: {counts}"
    # With cap=3 and total limit=6, both sides should end up with <=3.
    # Typically this results in exactly 3/3; assert at least one from each remains.
    assert counts.get(ALICE, 0) >= 1 and counts.get(BOB, 0) >= 1, "eviction fairness should retain both senders"
