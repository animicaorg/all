from __future__ import annotations

import types
from typing import Any, Optional

import pytest

pool_mod = pytest.importorskip("mempool.pool", reason="mempool.pool module not found")
policy_mod = pytest.importorskip(
    "mempool.policy", reason="mempool.policy module not found"
)
errors = pytest.importorskip("mempool.errors", reason="mempool.errors module not found")

# Prefer specific error types if the package defines them.
ERR_ADMISSION = getattr(errors, "AdmissionError", Exception)
ERR_REPLACEMENT = getattr(errors, "ReplacementError", ERR_ADMISSION)


# -------------------------
# Helpers & test scaffolding
# -------------------------


class FakeTx:
    """
    Minimal tx object with just enough surface area for pool + priority code.
    We will monkeypatch mempool.priority.effective_priority(tx) to return tx.fee,
    and mempool.validate.* to no-op so we can isolate replacement behavior.
    """

    def __init__(self, sender: bytes, nonce: int, fee: int, size_bytes: int = 100):
        self.sender = sender
        self.nonce = nonce
        self.fee = fee
        self.size_bytes = size_bytes
        self.hash = (sender + nonce.to_bytes(8, "big"))[
            :32
        ] or b"\xab" * 32  # best-effort
        self.tx_hash = self.hash  # common alias

    # If code asks for encoded size:
    def __bytes__(self) -> bytes:
        return b"\xee" * self.size_bytes


ALICE = b"A" * 20
BOB = b"B" * 20


def _bump_ratio() -> float:
    """
    Try to obtain the configured replacement bump threshold from policy.
    Fallback to 0.1 (10%) if not present.
    """
    # common names
    for name in (
        "REPLACE_BUMP_RATIO",
        "REPLACE_BUMP_PCT",
        "REPLACEMENT_BUMP_RATIO",
        "REPLACEMENT_BUMP_PCT",
        "BUMP_RATIO",
        "BUMP_PCT",
        "REPLACE_BUMP",
    ):
        if hasattr(policy_mod, name):
            val = getattr(policy_mod, name)
            # PCT values likely in percent form (e.g., 10 or 12.5)
            if "PCT" in name or (
                isinstance(val, (int, float)) and val > 1.0 and val <= 100
            ):
                return float(val) / 100.0
            return float(val)
    return 0.10


def _new_pool() -> Any:
    """
    Instantiate whichever pool class the module exposes.
    """
    for cls_name in ("Pool", "Mempool", "TxPool", "PendingPool", "MemPool"):
        if hasattr(pool_mod, cls_name):
            cls = getattr(pool_mod, cls_name)
            try:
                return cls()
            except TypeError:
                # Try passing a trivial config object
                try:
                    return cls(config=None)  # type: ignore[call-arg]
                except Exception:
                    pass
    # Fallback to module-scope instance if present
    for name in ("pool", "mempool", "pending", "instance"):
        if hasattr(pool_mod, name):
            return getattr(pool_mod, name)
    pytest.skip("No known pool class/instance found in mempool.pool")
    raise RuntimeError  # pragma: no cover


def _monkeypatch_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Make stateless validation permissive so replacement logic is the only gate.
    """
    try:
        validate = pytest.importorskip("mempool.validate")
    except Exception:
        return

    # Common entrypoints to bypass:
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

    # Size helper sometimes consulted by pools
    for name in (
        "estimate_encoded_size",
        "encoded_size",
        "tx_encoded_size",
        "get_encoded_size",
    ):
        if hasattr(validate, name):
            monkeypatch.setattr(validate, name, lambda tx: len(bytes(tx)), raising=True)

    # PQ precheck should succeed by default for these tests
    for name in (
        "precheck_pq_signature",
        "pq_precheck_verify",
        "verify_pq_signature",
        "pq_verify",
    ):
        if hasattr(validate, name):
            monkeypatch.setattr(validate, name, lambda *a, **k: True, raising=True)


def _monkeypatch_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Force effective priority to be 'tx.fee' so we can reason about thresholds.
    """
    try:
        priority = pytest.importorskip("mempool.priority")
    except Exception:
        return
    for name in ("effective_priority", "priority_of", "calc_effective_priority"):
        if hasattr(priority, name):
            monkeypatch.setattr(
                priority, name, lambda tx: getattr(tx, "fee", 0), raising=True
            )


def _admit(pool: Any, tx: FakeTx) -> Optional[str]:
    """
    Add/admit a tx using whichever method exists.
    Normalize return to a string when possible, else None.
    """
    for name in ("add", "admit", "ingest", "push", "put", "insert"):
        if hasattr(pool, name):
            fn = getattr(pool, name)
            try:
                res = fn(tx)
            except TypeError:
                try:
                    res = fn(tx.sender, tx.nonce, tx)  # some APIs
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


def _len_pool(pool: Any) -> int:
    if hasattr(pool, "__len__"):
        try:
            return int(len(pool))
        except Exception:
            pass
    # Try common iterators
    for name in ("__iter__", "iter_all", "all", "txs", "items"):
        if hasattr(pool, name):
            it = getattr(pool, name)
            try:
                seq = list(it() if callable(it) else it)
                return len(seq)
            except Exception:
                continue
    # Unknown length
    return -1


# -------------------------
# Tests
# -------------------------


def test_insufficient_bump_is_rejected(monkeypatch: pytest.MonkeyPatch):
    _monkeypatch_validation(monkeypatch)
    _monkeypatch_priority(monkeypatch)

    pool = _new_pool()
    base_fee = 1_000
    bump = _bump_ratio()
    # Pick a fee strictly between base and required bump
    insufficient = max(base_fee + 1, int(base_fee * (1.0 + bump * 0.5)))

    a1 = FakeTx(ALICE, 0, base_fee)
    _admit(pool, a1)

    a2 = FakeTx(ALICE, 0, insufficient)
    with pytest.raises((ERR_REPLACEMENT, ERR_ADMISSION)):
        _admit(pool, a2)


def test_equal_fee_does_not_replace(monkeypatch: pytest.MonkeyPatch):
    _monkeypatch_validation(monkeypatch)
    _monkeypatch_priority(monkeypatch)

    pool = _new_pool()
    base_fee = 2_000

    a1 = FakeTx(ALICE, 1, base_fee)
    _admit(pool, a1)
    n_before = _len_pool(pool)

    a_equal = FakeTx(ALICE, 1, base_fee)
    with pytest.raises((ERR_REPLACEMENT, ERR_ADMISSION)):
        _admit(pool, a_equal)

    n_after = _len_pool(pool)
    if n_before != -1 and n_after != -1:
        # Pool size should not increase due to failed replacement
        assert n_after == n_before


def test_sufficient_bump_replaces(monkeypatch: pytest.MonkeyPatch):
    _monkeypatch_validation(monkeypatch)
    _monkeypatch_priority(monkeypatch)

    pool = _new_pool()
    base_fee = 1_500
    bump = _bump_ratio()
    sufficient = int(base_fee * (1.0 + bump + 0.05))  # add 5% headroom
    a1 = FakeTx(ALICE, 2, base_fee)
    _admit(pool, a1)

    n_before = _len_pool(pool)

    a2 = FakeTx(ALICE, 2, sufficient)
    # Should not raise; many APIs return a status ("replaced") but we only rely on non-raise
    status = _admit(pool, a2)

    n_after = _len_pool(pool)
    if n_before != -1 and n_after != -1:
        assert (
            n_after == n_before
        ), "Replacement should not change total pool size for same sender+nonce"

    # If pool exposes a lookup by (sender, nonce), prefer it and assert we get the higher-fee tx
    for name in ("get", "get_tx", "by_sender_nonce", "lookup"):
        if hasattr(pool, name):
            fn = getattr(pool, name)
            try:
                got = fn(ALICE, 2)  # type: ignore[misc]
            except TypeError:
                # maybe returns a tuple or requires different args; skip strict check
                continue
            if got is not None and hasattr(got, "fee"):
                assert getattr(got, "fee") == sufficient
            break


def test_other_sender_not_affected(monkeypatch: pytest.MonkeyPatch):
    """
    Replacing ALICE's tx should not disturb BOB's distinct tx with same nonce.
    """
    _monkeypatch_validation(monkeypatch)
    _monkeypatch_priority(monkeypatch)

    pool = _new_pool()

    # Seed: both senders nonce 0
    _admit(pool, FakeTx(ALICE, 0, 1_000))
    _admit(pool, FakeTx(BOB, 0, 900))

    # Try (and fail) to replace ALICE with insufficient bump
    bump = _bump_ratio()
    insufficient = int(1_000 * (1.0 + bump * 0.5))
    with pytest.raises((ERR_REPLACEMENT, ERR_ADMISSION)):
        _admit(pool, FakeTx(ALICE, 0, insufficient))

    # Ensure BOB can still submit an upgraded tx independently
    ok_fee = int(900 * (1.0 + bump + 0.05))
    _admit(pool, FakeTx(BOB, 0, ok_fee))  # should succeed; not tied to ALICE's failure
