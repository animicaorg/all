from __future__ import annotations

import dataclasses
from typing import Any, Callable, Iterable, List, Optional, Tuple, Union

import pytest

seq_mod = pytest.importorskip(
    "mempool.sequence", reason="mempool.sequence module not found"
)


# -------------------------
# Test scaffolding / helpers
# -------------------------


@dataclasses.dataclass(frozen=True)
class FakeTx:
    sender: bytes
    nonce: int
    size_bytes: int = 100
    fee: int = 0  # optional: effective priority
    tx_hash: bytes = b"\xab" * 32  # placeholder

    # Many pools expect a bytes-like encoding for size checks
    def __bytes__(self) -> bytes:
        return b"\xff" * self.size_bytes

    # Some pools expect attributes named differently; provide a few aliases
    @property
    def from_(self) -> bytes:
        return self.sender

    @property
    def account(self) -> bytes:
        return self.sender

    @property
    def nonce_value(self) -> int:
        return self.nonce


Sender = bytes

ALICE: Sender = b"A" * 20
BOB: Sender = b"B" * 20


def _install_account_nonce_getter(
    monkeypatch: pytest.MonkeyPatch, table: dict[Sender, int]
) -> Callable[[Sender], int]:
    """
    Many implementations read 'current on-chain nonce' via a module-level function
    or dependency. We attempt to patch a few common names.
    """

    def get_nonce(addr: Sender) -> int:
        return table.get(addr, 0)

    for name in (
        "get_account_nonce",
        "account_nonce",
        "read_account_nonce",
        "fetch_account_nonce",
    ):
        if hasattr(seq_mod, name):
            monkeypatch.setattr(seq_mod, name, get_nonce, raising=True)
    # Also try nested deps if present
    if hasattr(seq_mod, "deps"):
        for name in ("get_account_nonce", "account_nonce"):
            if hasattr(seq_mod.deps, name):
                monkeypatch.setattr(seq_mod.deps, name, get_nonce, raising=True)

    return get_nonce


def _new_sequencer(monkeypatch: pytest.MonkeyPatch) -> Any:
    """
    Instantiate whichever queue/sequencer type the module exposes.
    Fallback to a module-level singleton if that's the pattern.
    """
    # First: classes to try
    classes = [
        "NonceQueues",
        "Sequencer",
        "Sequence",
        "SenderQueues",
        "SequencePool",
    ]
    for cls_name in classes:
        if hasattr(seq_mod, cls_name):
            cls = getattr(seq_mod, cls_name)
            try:
                return cls()  # most implementations take no args
            except TypeError:
                # Try passing a simple config if constructor expects it
                try:
                    return cls(config=None)  # type: ignore
                except Exception:
                    pass

    # Second: module-scope instance
    for inst_name in ("queues", "sequencer", "sequence", "pool"):
        if hasattr(seq_mod, inst_name):
            return getattr(seq_mod, inst_name)

    pytest.skip("No known sequencer/queue entrypoint found in mempool.sequence")
    raise RuntimeError  # pragma: no cover


def _add_tx(q: Any, tx: FakeTx) -> Optional[str]:
    """
    Add/admit a tx using whichever method exists.
    Return a best-effort status string: "ready" / "held" / None.
    """
    for name in ("add", "admit", "ingest", "push", "put"):
        if hasattr(q, name):
            fn = getattr(q, name)
            try:
                res = fn(tx)
            except TypeError:
                # Some APIs expect (sender, nonce, tx)
                try:
                    res = fn(tx.sender, tx.nonce, tx)
                except Exception:
                    res = fn(tx)  # fallback re-raise
            # Normalize common return shapes
            if isinstance(res, str):
                return res.lower()
            if isinstance(res, tuple) and res and isinstance(res[0], str):
                return res[0].lower()
            if res is True:
                return "ready"
            if res is False:
                return "held"
            return None
    pytest.skip("No known add()/admit() API found on sequencer")
    return None  # pragma: no cover


def _pop_one_ready(q: Any) -> Optional[FakeTx]:
    """
    Pop a single ready tx, if any. Returns None if none available.
    Supports several method names and return shapes.
    """
    candidates = [
        "pop_ready",
        "pop",
        "next_ready",
        "take_ready",
    ]
    for name in candidates:
        if hasattr(q, name):
            fn = getattr(q, name)
            try:
                item = fn()
            except TypeError:
                # Some APIs accept a max parameter; try 1
                try:
                    item = fn(1)
                except Exception:
                    raise
            if item is None:
                return None
            # Return shapes:
            # - tx
            # - (tx,) or [tx]
            # - (sender, tx) or (sender, nonce, tx)
            if isinstance(item, (list, tuple)):
                if not item:
                    return None
                # If first is sender and second is tx
                if len(item) >= 2 and isinstance(item[1], FakeTx):
                    return item[1]
                # If first is tx
                if isinstance(item[0], FakeTx):
                    return item[0]
                # Unknown list shape; fall through
                maybe_tx = next((x for x in item if isinstance(x, FakeTx)), None)
                return maybe_tx
            # Single object
            if isinstance(item, FakeTx):
                return item
            # If the tx is wrapped, try attribute access
            for attr in ("tx", "transaction", "value"):
                if hasattr(item, attr) and isinstance(getattr(item, attr), FakeTx):
                    return getattr(item, attr)
            return None
    # Some APIs provide an iterator + explicit consume. Try iterator (read-only) first.
    for name in ("iter_ready", "ready_iter"):
        if hasattr(q, name):
            it = getattr(q, name)()
            try:
                item = next(iter(it))
            except StopIteration:
                return None
            # No consume path known; treat as peek (not pop), so do not return to avoid looping forever
            return None  # pragma: no cover
    pytest.skip("No known pop-ready API found on sequencer")
    return None  # pragma: no cover


def _drain_ready(q: Any) -> list[FakeTx]:
    out: list[FakeTx] = []
    while True:
        tx = _pop_one_ready(q)
        if tx is None:
            break
        out.append(tx)
    return out


# -------------------------
# Tests
# -------------------------


def test_nonce_gap_then_fill_transitions(monkeypatch: pytest.MonkeyPatch):
    """
    Base nonce(ALICE)=5. Insert nonce=7 first -> held (gap).
    Then insert 5 -> 5 becomes ready; 7 still held.
    Then insert 6 -> 6 becomes ready, and 7 either:
      - becomes ready immediately (contiguous chain 5,6,7), or
      - becomes ready after 5 and 6 are popped/committed.
    In both cases, draining ready after step 3 must eventually yield 5 and 6,
    and then 7 without further inserts.
    """
    _install_account_nonce_getter(monkeypatch, {ALICE: 5})
    q = _new_sequencer(monkeypatch)

    tx7 = FakeTx(sender=ALICE, nonce=7)
    tx5 = FakeTx(sender=ALICE, nonce=5)
    tx6 = FakeTx(sender=ALICE, nonce=6)

    status_7 = _add_tx(q, tx7)
    # It's okay if the API doesn't return a status; enforce via emptiness of ready set
    ready_after_7 = _drain_ready(q)
    assert (
        ready_after_7 == []
    ), "Nonce gap: adding nonce=7 with base=5 must not produce ready txs"

    status_5 = _add_tx(q, tx5)
    ready_after_5 = _drain_ready(q)
    assert any(t.nonce == 5 for t in ready_after_5), "nonce=5 must be ready when base=5"

    status_6 = _add_tx(q, tx6)

    # After adding 6, 5/6 must be drainable; 7 may or may not be ready yet, but
    # draining again should eventually produce 7 without further inserts.
    first_drain = _drain_ready(q)
    nonces = [t.nonce for t in first_drain]
    assert 6 in nonces, "nonce=6 must be ready once present and contiguous with base"
    # If 7 not yet ready, a subsequent drain (after internal base advance) should yield it.
    if 7 not in nonces:
        second_drain = _drain_ready(q)
        nonces2 = [t.nonce for t in second_drain]
        assert (
            7 in nonces2
        ), "nonce=7 should become ready after contiguous 5 and 6 are consumed"


def test_per_sender_independence(monkeypatch: pytest.MonkeyPatch):
    """
    Readiness is tracked per-sender. A gap for ALICE must not block BOB's ready txs.
    Base nonces: ALICE=10, BOB=0.
    """
    _install_account_nonce_getter(monkeypatch, {ALICE: 10, BOB: 0})
    q = _new_sequencer(monkeypatch)

    # ALICE: insert nonce 12 (gap), should not yield ready.
    _add_tx(q, FakeTx(sender=ALICE, nonce=12))
    assert _drain_ready(q) == [], "Gap for ALICE must not produce ready txs for ALICE"

    # BOB: insert nonce 0 (base), should be immediately ready.
    _add_tx(q, FakeTx(sender=BOB, nonce=0))
    ready = _drain_ready(q)
    assert any(
        t.sender == BOB and t.nonce == 0 for t in ready
    ), "BOB's nonce=0 should be ready despite ALICE's gap"


def test_advance_base_unblocks_held(monkeypatch: pytest.MonkeyPatch):
    """
    If the 'on-chain' base nonce advances (e.g., after including a tx),
    previously held txs should become ready without re-insertion.
    We simulate base advance by patching the account nonce getter and then draining again.
    """
    table = {ALICE: 3}
    _install_account_nonce_getter(monkeypatch, table)
    q = _new_sequencer(monkeypatch)

    # Insert txs with nonces 3,4,5; but start with a gap (skip 3 initially)
    _add_tx(q, FakeTx(sender=ALICE, nonce=4))
    _add_tx(q, FakeTx(sender=ALICE, nonce=5))
    assert (
        _drain_ready(q) == []
    ), "With base=3 and no nonce=3 tx present, 4/5 should be held"

    # Simulate external inclusion of nonce=3 (e.g., mined elsewhere) by advancing base to 4
    table[ALICE] = 4  # our getter will now report 4
    # Depending on implementation, queues may re-check readiness on pop or via a tick.
    # Draining now should produce at least nonce=4; if not, try a no-op add to trigger recompute.
    ready = _drain_ready(q)
    if not any(t.nonce == 4 for t in ready):
        # Nudge: add a harmless tx with a higher nonce to trigger internal recompute
        _add_tx(q, FakeTx(sender=ALICE, nonce=6))
        ready = _drain_ready(q)

    assert any(
        t.nonce == 4 for t in ready
    ), "After base advance to 4, nonce=4 must become ready"
    # Advancing base to 5 should free the next held
    table[ALICE] = 5
    ready2 = _drain_ready(q)
    assert any(
        t.nonce == 5 for t in ready2
    ), "After base advance to 5, nonce=5 must become ready"
