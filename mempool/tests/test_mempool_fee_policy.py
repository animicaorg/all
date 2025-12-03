from __future__ import annotations

import types
from typing import Any, Optional

import pytest

pool_mod = pytest.importorskip("mempool.pool", reason="mempool.pool module not found")
policy_mod = pytest.importorskip("mempool.policy", reason="mempool.policy module not found")
errors = pytest.importorskip("mempool.errors", reason="mempool.errors module not found")
evict_mod = pytest.importorskip("mempool.evict", reason="mempool.evict module not found")

# Prefer specific error types if the package defines them.
ERR_ADMISSION = getattr(errors, "AdmissionError", Exception)
ERR_FEE_TOO_LOW = getattr(errors, "FeeTooLow", ERR_ADMISSION)
ERR_REPLACEMENT = getattr(errors, "ReplacementError", ERR_ADMISSION)


class FakeTx:
    """Minimal tx object with sender, nonce, fee, and an encoded size."""

    def __init__(self, sender: bytes, nonce: int, fee: int, size_bytes: int = 100):
        self.sender = sender
        self.nonce = nonce
        self.fee = fee
        self.size_bytes = size_bytes
        self.hash = (sender + nonce.to_bytes(8, "big"))[:32] or b"\xcd" * 32
        self.tx_hash = self.hash

    def __bytes__(self) -> bytes:
        return b"\xee" * self.size_bytes


ALICE = b"A" * 20
BOB = b"B" * 20


def _monkeypatch_validation_and_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    try:
        validate = pytest.importorskip("mempool.validate")
        for name in (
            "validate_tx",
            "fast_stateless_check",
            "stateless_validate",
            "validate",
            "check_size",
            "check_chain_id",
            "check_gas_limits",
        ):
            if hasattr(validate, name):
                monkeypatch.setattr(validate, name, lambda *a, **k: True, raising=True)
        for name in (
            "estimate_encoded_size",
            "encoded_size",
            "tx_encoded_size",
            "get_encoded_size",
        ):
            if hasattr(validate, name):
                monkeypatch.setattr(validate, name, lambda tx: len(bytes(tx)), raising=True)
        for name in (
            "precheck_pq_signature",
            "pq_precheck_verify",
            "verify_pq_signature",
            "pq_verify",
        ):
            if hasattr(validate, name):
                monkeypatch.setattr(validate, name, lambda *a, **k: True, raising=True)
    except Exception:
        pass

    try:
        priority = pytest.importorskip("mempool.priority")
        for name in ("effective_priority", "priority_of", "calc_effective_priority"):
            if hasattr(priority, name):
                monkeypatch.setattr(
                    priority, name, lambda tx: getattr(tx, "fee", 0), raising=True
                )
    except Exception:
        pass


def _make_config(
    *,
    max_txs: int | None = None,
    max_bytes: int | None = None,
    per_sender_cap: int | None = None,
) -> Any:
    try:
        from mempool import config as mp_config
    except Exception:
        mp_config = None

    field_map = {
        "max_txs": max_txs,
        "max_pool_txs": max_txs,
        "max_items": max_txs,
        "capacity": max_txs,
        "max_bytes": max_bytes,
        "max_pool_bytes": max_bytes,
        "capacity_bytes": max_bytes,
        "max_mem_bytes": max_bytes,
        "per_sender_cap": per_sender_cap,
        "per_sender_limit": per_sender_cap,
        "max_per_sender": per_sender_cap,
        "sender_cap": per_sender_cap,
    }

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
                    kwargs = {k: v for k, v in field_map.items() if v is not None}
                    return T(**kwargs)
                except Exception:
                    continue

    ns = types.SimpleNamespace()
    for k, v in field_map.items():
        if v is not None:
            setattr(ns, k, v)
    return ns


def _new_pool(config: Any | None) -> Any:
    for cls_name in ("Pool", "Mempool", "TxPool", "PendingPool", "MemPool"):
        if hasattr(pool_mod, cls_name):
            cls = getattr(pool_mod, cls_name)
            for args in ((config,), tuple()):
                try:
                    if config is None:
                        return cls()
                    try:
                        return cls(config=config)
                    except TypeError:
                        return cls(*args)
                except Exception:
                    continue
    for name in ("pool", "mempool", "pending", "instance"):
        if hasattr(pool_mod, name):
            return getattr(pool_mod, name)
    pytest.skip("No known pool class/instance found in mempool.pool")
    raise RuntimeError


def _admit(pool: Any, tx: FakeTx) -> Optional[str]:
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
    return None


def _pool_items(pool: Any) -> list[FakeTx]:
    if hasattr(pool, "__iter__"):
        try:
            return list(pool)
        except Exception:
            pass
    for name in ("iter_all", "all", "txs", "items"):
        if hasattr(pool, name):
            it = getattr(pool, name)
            try:
                return list(it() if callable(it) else it)
            except Exception:
                continue
    return []


def _contains_tx(pool: Any, tx: FakeTx) -> bool:
    for name in ("__contains__", "has", "contains", "__getitem__"):
        if hasattr(pool, name):
            fn = getattr(pool, name)
            try:
                return bool(fn(tx))
            except Exception:
                try:
                    return bool(fn(tx.hash))
                except Exception:
                    continue
    return any(
        t is tx or (t.sender == tx.sender and t.nonce == tx.nonce) for t in _pool_items(pool)
    )


def _len_pool(pool: Any) -> int:
    if hasattr(pool, "__len__"):
        try:
            return int(len(pool))
        except Exception:
            pass
    return len(_pool_items(pool))


# -------------------------
# Tests
# -------------------------


def test_rejects_below_min_fee():
    policy = policy_mod.AdmissionPolicy(
        policy_mod.AdmissionConfig(min_effective_fee_override_wei=1_000)
    )

    tx = FakeTx(ALICE, 0, fee=500)
    meta = types.SimpleNamespace(
        effective_fee_wei=500, size_bytes=len(bytes(tx))
    )

    with pytest.raises(ERR_FEE_TOO_LOW):
        policy.check_admit(tx=tx, meta=meta, pool_size=0, capacity=100)

    meta_high = types.SimpleNamespace(
        effective_fee_wei=2_000, size_bytes=len(bytes(tx))
    )
    policy.check_admit(tx=tx, meta=meta_high, pool_size=0, capacity=100)


def test_eviction_drops_low_fee_when_capacity_hit(monkeypatch: pytest.MonkeyPatch):
    _monkeypatch_validation_and_priority(monkeypatch)

    config = _make_config(max_txs=3)
    pool = _new_pool(config)

    tx_low = FakeTx(ALICE, 0, 10)
    tx_mid = FakeTx(ALICE, 1, 20)
    tx_high = FakeTx(BOB, 0, 50)
    for tx in (tx_low, tx_mid, tx_high):
        _admit(pool, tx)

    tx_new = FakeTx(BOB, 1, 100)
    _admit(pool, tx_new)

    if hasattr(pool, "evict_if_needed"):
        try:
            pool.evict_if_needed()
        except Exception:
            pass
    for name in ("evict_if_needed", "evict_to_fit", "run_eviction", "evict"):
        if hasattr(evict_mod, name):
            fn = getattr(evict_mod, name)
            try:
                fn(pool, config)
            except Exception:
                continue

    assert _len_pool(pool) <= 3
    assert not _contains_tx(pool, tx_low), "lowest-fee tx should be evicted first"
    assert _contains_tx(pool, tx_new), "new high-fee tx should remain in pool"


def test_rbf_rules_enforced(monkeypatch: pytest.MonkeyPatch):
    try:
        bump_ratio = float(getattr(policy_mod, "REPLACE_BUMP_RATIO", 0.1) or 0.1)
    except Exception:
        bump_ratio = 0.1

    try:
        from mempool.tests import test_replacement as repl

        if hasattr(repl, "_monkeypatch_validation"):
            repl._monkeypatch_validation(monkeypatch)  # type: ignore[attr-defined]
        if hasattr(repl, "_monkeypatch_priority"):
            repl._monkeypatch_priority(monkeypatch)  # type: ignore[attr-defined]
    except Exception:
        _monkeypatch_validation_and_priority(monkeypatch)

    pool = _new_pool(None)

    base = FakeTx(ALICE, 0, fee=100)
    _admit(pool, base)

    insufficient = FakeTx(ALICE, 0, fee=int(base.fee * (1 + bump_ratio) - 1))
    rejected = False
    try:
        _admit(pool, insufficient)
    except ERR_REPLACEMENT:
        rejected = True
    else:
        rejected = _contains_tx(pool, base) and not _contains_tx(pool, insufficient)

    assert rejected, "replacement without sufficient bump should be rejected"

    sufficient = FakeTx(ALICE, 0, fee=int(base.fee * (1 + bump_ratio) + 10))
    _admit(pool, sufficient)

    assert _contains_tx(pool, sufficient)
    assert not _contains_tx(pool, base)


