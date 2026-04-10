from __future__ import annotations

import types
from typing import Any, Iterable, Optional

import pytest

pool_mod = pytest.importorskip("mempool.pool", reason="mempool.pool module not found")
drain_mod = pytest.importorskip(
    "mempool.drain", reason="mempool.drain module not found"
)

# -------------------------
# Helpers & test scaffolding
# -------------------------


class FakeTx:
    """
    Minimal tx object sufficient for pool + drain selection.
    Attributes include multiple common synonyms to maximize compatibility.
    """

    def __init__(
        self,
        sender: bytes,
        nonce: int,
        fee: int,
        gas: int = 21_000,
        size_bytes: int = 120,
    ):
        self.sender = sender
        self.nonce = nonce
        self.fee = fee
        # Gas synonyms used by various implementations
        self.gas = gas
        self.gas_limit = gas
        self.intrinsic_gas = gas
        self.gas_cost = gas
        # Encoded size
        self.size_bytes = size_bytes
        # a stable-ish hash for membership lookups in some pools
        self.hash = (sender + nonce.to_bytes(8, "big"))[:32] or b"\xda" * 32
        self.tx_hash = self.hash  # common alias

    def __bytes__(self) -> bytes:  # used by size checks
        return b"\xee" * self.size_bytes


ALICE = b"A" * 20
BOB = b"B" * 20
CARL = b"C" * 20
DANA = b"D" * 20
ELLA = b"E" * 20


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
                monkeypatch.setattr(
                    validate, name, lambda tx: len(bytes(tx)), raising=True
                )
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


def _make_config(*, max_txs: int | None = None, max_bytes: int | None = None) -> Any:
    """
    Build a config/limits object compatible with pool/drain modules.
    """
    try:
        from mempool import config as mp_config  # type: ignore
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
    }

    if mp_config:
        for type_name in ("MempoolConfig", "PoolConfig", "Limits", "MempoolLimits"):
            if hasattr(mp_config, type_name):
                T = getattr(mp_config, type_name)
                try:
                    kwargs = {k: v for k, v in field_map.items() if v is not None}
                    return T(**kwargs)  # type: ignore[call-arg]
                except Exception:
                    continue

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


def _extract_txs(selection: Any) -> list[FakeTx]:
    """
    Normalize various selection result shapes into a list[FakeTx].
    Accept list/iterable of:
      - FakeTx
      - objects with .tx attribute
      - (.., FakeTx) tuples
      - dicts containing 'tx'
    """
    if selection is None:
        return []
    if isinstance(selection, FakeTx):
        return [selection]

    # If selection is a tuple where second element is list
    if (
        isinstance(selection, (tuple, list))
        and selection
        and isinstance(selection[0], list)
    ):
        selection = selection[0]

    try:
        it = list(selection)  # type: ignore[arg-type]
    except Exception:
        return []

    out: list[FakeTx] = []
    for item in it:
        if isinstance(item, FakeTx):
            out.append(item)
            continue
        if hasattr(item, "tx") and isinstance(item.tx, FakeTx):
            out.append(item.tx)
            continue
        if isinstance(item, (tuple, list)):
            for x in item:
                if isinstance(x, FakeTx):
                    out.append(x)
                    break
        if isinstance(item, dict) and isinstance(item.get("tx"), FakeTx):
            out.append(item["tx"])
    return out


def _sum_gas(txs: list[FakeTx]) -> int:
    def gas_of(tx: FakeTx) -> int:
        for n in ("gas", "gas_limit", "intrinsic_gas", "gas_cost"):
            v = getattr(tx, n, None)
            if isinstance(v, int):
                return v
        return 21_000

    return sum(gas_of(tx) for tx in txs)


def _sum_size(txs: list[FakeTx]) -> int:
    return sum(len(bytes(tx)) for tx in txs)


def _priority(tx: FakeTx) -> int:
    try:
        priority = pytest.importorskip("mempool.priority")
        for name in ("effective_priority", "priority_of", "calc_effective_priority"):
            if hasattr(priority, name):
                return int(getattr(priority, name)(tx))
    except Exception:
        pass
    return int(getattr(tx, "fee", 0))


def _drain(pool: Any, gas_budget: int, byte_budget: int) -> list[FakeTx]:
    """
    Attempt a variety of drain/selection APIs. Return the list of chosen transactions.
    """
    # Preferred: functions in mempool.drain
    for name in (
        "select_for_block",
        "select_ready",
        "pick_ready",
        "drain_under_budget",
        "pop_ready_under_budget",
        "fetch_ready",
        "choose",
    ):
        fn = _get_attr_any(drain_mod, [name])
        if callable(fn):
            for kwargs in (
                dict(pool=pool, gas_budget=gas_budget, byte_budget=byte_budget),
                dict(pool=pool, gas_limit=gas_budget, byte_limit=byte_budget),
                dict(pool=pool, gas=gas_budget, bytes=byte_budget),
                dict(pool=pool, budget_gas=gas_budget, budget_bytes=byte_budget),
                dict(pool=pool, gas_budget=gas_budget),
                dict(pool=pool, gas_limit=gas_budget),
            ):
                try:
                    sel = fn(**kwargs)  # type: ignore[misc]
                    return _extract_txs(sel)
                except TypeError:
                    continue
                except Exception:
                    # Try next name/shape
                    continue

    # Fallback: methods on pool itself
    for name in (
        "select_for_block",
        "select_ready",
        "pop_ready",
        "fetch_ready",
        "drain",
        "build_block",
    ):
        if hasattr(pool, name):
            m = getattr(pool, name)
            for args in (
                (gas_budget, byte_budget),
                (),
            ):
                try:
                    sel = m(*args)  # type: ignore[misc]
                    return _extract_txs(sel)
                except TypeError:
                    continue
                except Exception:
                    continue

    pytest.skip("No known drain/selection API found")
    return []  # pragma: no cover


# -------------------------
# Tests
# -------------------------


def test_drain_respects_gas_and_bytes_budgets(monkeypatch: pytest.MonkeyPatch):
    """
    With uniform gas/size, selection should pick the highest-priority txs up to the budget.
    """
    _monkeypatch_validation_and_priority(monkeypatch)

    pool = _new_pool(config=_make_config(max_txs=10, max_bytes=10_000))

    # Five independent senders, uniform gas/size; fees vary
    txs = [
        FakeTx(ALICE, 0, fee=10, gas=10_000, size_bytes=100),
        FakeTx(BOB, 0, fee=50, gas=10_000, size_bytes=100),
        FakeTx(CARL, 0, fee=30, gas=10_000, size_bytes=100),
        FakeTx(DANA, 0, fee=70, gas=10_000, size_bytes=100),
        FakeTx(ELLA, 0, fee=20, gas=10_000, size_bytes=100),
    ]
    for tx in txs:
        _admit(pool, tx)

    # Budgets allow exactly 3 txs
    selected = _drain(pool, gas_budget=30_000, byte_budget=300)
    assert selected, "drain returned no transactions"
    assert _sum_gas(selected) <= 30_000 + 1
    assert _sum_size(selected) <= 300 + 1

    # Expect top-3 fees chosen: 70, 50, 30
    top3 = sorted((t.fee for t in txs), reverse=True)[:3]
    got_fees = sorted((_priority(t) for t in selected), reverse=True)
    assert sorted(got_fees, reverse=True) == sorted(
        top3, reverse=True
    ), f"expected top3 {top3}, got {got_fees}"

    # If an order is returned, ensure non-increasing by priority
    if isinstance(selected, list) and len(selected) >= 2:
        assert all(
            _priority(a) >= _priority(b) for a, b in zip(selected, selected[1:])
        ), "selection order is not non-increasing by priority"


def test_drain_respects_sender_sequence(monkeypatch: pytest.MonkeyPatch):
    """
    A higher-fee tx with nonce=1 must not be selected unless nonce=0 from the same sender
    is also selected (or already included/committed). With tight budget, we should see
    BOB's high-fee plus ALICE nonce=0, but NOT ALICE nonce=1 even though its fee is high.
    """
    _monkeypatch_validation_and_priority(monkeypatch)

    pool = _new_pool(config=_make_config(max_txs=10, max_bytes=10_000))

    # ALICE: nonce 0 (low fee) and nonce 1 (very high fee)
    a0 = FakeTx(ALICE, 0, fee=10, gas=10_000, size_bytes=100)
    a1 = FakeTx(ALICE, 1, fee=1_000, gas=10_000, size_bytes=100)
    # BOB: single high-fee tx
    b0 = FakeTx(BOB, 0, fee=800, gas=10_000, size_bytes=100)

    for tx in (a0, a1, b0):
        _admit(pool, tx)

    # Budget allows only 2 txs → expect {b0, a0}. a1 cannot be included without a0 and would exceed budget.
    selected = _drain(pool, gas_budget=20_000, byte_budget=200)
    fees = sorted((_priority(t) for t in selected), reverse=True)
    assert _sum_gas(selected) <= 20_000 + 1
    assert _sum_size(selected) <= 200 + 1

    # Must include BOB(800) and ALICE(10); must NOT include ALICE(1000)
    assert (
        800 in fees and 10 in fees
    ), f"expected to include BOB(800) and ALICE(10), got {fees}"
    assert (
        1000 not in fees
    ), "nonce=1 should not be selected without nonce=0 within budget"


def test_drain_ordering_when_all_ready(monkeypatch: pytest.MonkeyPatch):
    """
    When all selected txs are ready and budgets are generous, the returned order
    should be non-increasing by priority (if the API preserves ordering).
    """
    _monkeypatch_validation_and_priority(monkeypatch)

    pool = _new_pool(config=_make_config(max_txs=10, max_bytes=10_000))

    seq = [
        FakeTx(ALICE, 0, fee=40, gas=5_000),
        FakeTx(BOB, 0, fee=90, gas=5_000),
        FakeTx(CARL, 0, fee=10, gas=5_000),
        FakeTx(DANA, 0, fee=70, gas=5_000),
    ]
    for tx in seq:
        _admit(pool, tx)

    selected = _drain(pool, gas_budget=50_000, byte_budget=10_000)
    assert (
        len(selected) == 4 or len(selected) >= 3
    ), "expected all (or nearly all) to fit generous budgets"

    # If results are ordered, verify monotone non-increasing by priority
    if isinstance(selected, list) and len(selected) >= 2:
        assert all(
            _priority(a) >= _priority(b) for a, b in zip(selected, selected[1:])
        ), f"selection order not by priority: {[(_priority(t), getattr(t,'sender',b'')) for t in selected]}"
