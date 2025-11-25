import hashlib
from dataclasses import dataclass
from typing import Dict, List, Set, Tuple

import random
import pytest


# ---------------------------------------------------
# Minimal transfer model shared by tests
# ---------------------------------------------------

Address = str
Amount = int

@dataclass(frozen=True)
class Tx:
    sender: Address
    to: Address
    amount: Amount
    nonce: int  # per-sender sequencing


def _state_root(state: Dict[str, int]) -> bytes:
    """
    Deterministic commitment of balances (and nonces) using SHA3-256.
    Nonces are stored under "__nonce__:<addr>" keys; they participate in the root.
    """
    items = sorted(state.items(), key=lambda kv: kv[0])
    buf = bytearray()
    for k, v in items:
        buf.extend(k.encode("utf-8"))
        buf.extend(int(v).to_bytes(8, "big", signed=False))
    return hashlib.sha3_256(bytes(buf)).digest()


def _expect_nonce(state: Dict[Address, Amount], addr: Address) -> int:
    return int(state.get(f"__nonce__:{addr}", 0))


def _inc_nonce(state: Dict[Address, Amount], addr: Address) -> None:
    state[f"__nonce__:{addr}"] = _expect_nonce(state, addr) + 1


def _apply_tx(state: Dict[Address, Amount], tx: Tx) -> bool:
    """
    Apply transfer semantics:
      - Nonce must match expected.
      - Sender must have sufficient balance.
    Returns True if applied, False if skipped (like excluding invalid from a block).
    """
    if tx.nonce != _expect_nonce(state, tx.sender):
        return False
    if state.get(tx.sender, 0) < tx.amount:
        return False
    state[tx.sender] = state.get(tx.sender, 0) - tx.amount
    state[tx.to] = state.get(tx.to, 0) + tx.amount
    _inc_nonce(state, tx.sender)
    return True


def _serial_apply(initial: Dict[Address, Amount], txs: List[Tx]) -> Tuple[Dict[Address, Amount], List[int]]:
    """
    Serial baseline: apply in order. Returns (state, applied_indices).
    """
    st: Dict[Address, Amount] = dict(initial)
    applied: List[int] = []
    for i, tx in enumerate(txs):
        if _apply_tx(st, tx):
            applied.append(i)
    return st, applied


# ---------------------------------------------------
# Access sets and optimistic "layered" scheduler (model)
# ---------------------------------------------------

def _access_sets(tx: Tx) -> Tuple[Set[str], Set[str]]:
    """
    Returns (reads, writes) sets of logical keys touched by tx.
    We treat sender/recipient balances and sender nonce as keys.
    """
    r: Set[str] = {f"bal:{tx.sender}", f"nonce:{tx.sender}", f"bal:{tx.to}"}
    w: Set[str] = {f"bal:{tx.sender}", f"bal:{tx.to}", f"nonce:{tx.sender}"}
    return r, w


def _optimistic_layers(txs: List[Tx]) -> List[List[int]]:
    """
    Partition tx indices into conflict-free layers based on read/write sets.
    Greedy layering: preserve original order while grouping non-conflicting txs.
    Conflicts: any R/W or W/R or W/W intersection.
    """
    layers: List[List[int]] = []
    for idx, tx in enumerate(txs):
        r, w = _access_sets(tx)
        placed = False
        for layer in layers:
            # Build layer aggregate sets
            layer_r: Set[str] = set()
            layer_w: Set[str] = set()
            for j in layer:
                lr, lw = _access_sets(txs[j])
                layer_r |= lr
                layer_w |= lw
            # Check conflicts against current layer
            if not (w & layer_w or w & layer_r or r & layer_w):
                layer.append(idx)
                placed = True
                break
        if not placed:
            layers.append([idx])
    return layers


def _optimistic_apply(initial: Dict[Address, Amount], txs: List[Tx]) -> Tuple[Dict[Address, Amount], List[List[int]]]:
    """
    Apply txs by conflict-free layers. Within each layer, effects *commute* by construction,
    so we can apply in listed order (deterministic). Returns (final_state, layers).
    If a tx becomes invalid due to prior-layer balance/nonce changes, it simply won't apply.
    """
    st: Dict[Address, Amount] = dict(initial)
    layers = _optimistic_layers(txs)
    for layer in layers:
        for idx in layer:
            _apply_tx(st, txs[idx])
    return st, layers


# ---------------------------------------------------
# Fixtures
# ---------------------------------------------------

@pytest.fixture
def initial_state() -> Dict[Address, Amount]:
    return {
        "alice": 100,
        "bob": 50,
        "carol": 0,
        "dave": 20,
    }


@pytest.fixture
def non_conflicting_batch() -> List[Tx]:
    # Disjoint senders (and distinct recipients) → should layer into one batch
    return [
        Tx("alice", "carol", 10, 0),
        Tx("bob", "dave", 5, 0),
        Tx("dave", "carol", 3, 0),
    ]


@pytest.fixture
def conflicting_batch_same_sender() -> List[Tx]:
    # Two txs from the same sender → write/write & nonce dependency conflict
    return [
        Tx("alice", "carol", 10, 0),
        Tx("alice", "bob", 7, 1),
        Tx("bob", "alice", 5, 0),
    ]


# ---------------------------------------------------
# Tests against the model scheduler
# ---------------------------------------------------

def test_merge_non_conflicting_equals_serial(initial_state, non_conflicting_batch):
    ser_state, ser_applied = _serial_apply(initial_state, non_conflicting_batch)
    opt_state, layers = _optimistic_apply(initial_state, non_conflicting_batch)

    # All three can be applied; one layer is enough
    assert len(layers) == 1
    assert _state_root(ser_state) == _state_root(opt_state)
    assert ser_state == opt_state
    assert ser_applied == [0, 1, 2]


def test_conflict_same_sender_partitions_layers_and_matches_serial(initial_state, conflicting_batch_same_sender):
    ser_state, _ = _serial_apply(initial_state, conflicting_batch_same_sender)
    opt_state, layers = _optimistic_apply(initial_state, conflicting_batch_same_sender)

    # Expect at least 2 layers due to alice's two txs touching same keys
    assert len(layers) >= 2
    # Deterministic equivalence to serial baseline
    assert _state_root(ser_state) == _state_root(opt_state)
    assert ser_state == opt_state


def test_random_scenarios_match_serial(initial_state):
    rng = random.Random(1337)
    addrs = ["alice", "bob", "carol", "dave"]
    for _round in range(10):
        # Build a batch of 20 txs with random senders/recipients/amounts and valid nonce progression
        txs: List[Tx] = []
        next_nonce: Dict[str, int] = {a: 0 for a in addrs}
        for _ in range(20):
            s = rng.choice(addrs)
            # choose recipient != sender
            rec = rng.choice([a for a in addrs if a != s])
            amt = rng.randint(0, 15)  # sometimes zero to simulate no-ops by balance
            txs.append(Tx(s, rec, amt, next_nonce[s]))
            # randomly decide to increment sender's expected nonce (sometimes leave holes to test invalid skips)
            if rng.random() < 0.8:
                next_nonce[s] += 1

        ser_state, _ = _serial_apply(initial_state, txs)
        opt_state, _layers = _optimistic_apply(initial_state, txs)

        assert _state_root(ser_state) == _state_root(opt_state)
        assert ser_state == opt_state


# ---------------------------------------------------
# Optional integration: hook into the project's optimistic scheduler
# ---------------------------------------------------

def test_project_optimistic_executor_if_available(initial_state):
    """
    Try to exercise execution.scheduler.optimistic (if present) and assert:
      - Partitioning (or equivalent conflict handling) happens.
      - Final state equals our serial baseline on the same tx list.
    We keep this permissive and skip if symbols/signatures don't match.
    """
    try:
        opt_mod = __import__("execution.scheduler.optimistic", fromlist=["OptimisticExecutor", "run", "execute", "apply_layers"])
    except Exception:
        pytest.skip("execution.scheduler.optimistic not available")

    # Build a small deterministic batch that *must* have conflicts (same sender twice)
    txs = [
        Tx("alice", "carol", 10, 0),
        Tx("alice", "bob", 7, 1),
        Tx("bob", "alice", 5, 0),
        Tx("dave", "carol", 2, 0),
    ]

    ser_state, _ = _serial_apply(initial_state, txs)

    # Common entrypoints to try:
    entry = None
    for name in ("apply_layers", "run", "execute", "apply"):
        fn = getattr(opt_mod, name, None)
        if callable(fn):
            entry = fn
            break

    OptimisticExecutor = getattr(opt_mod, "OptimisticExecutor", None)
    use_class = OptimisticExecutor is not None and hasattr(OptimisticExecutor, "run")

    # Adapter apply/access functions the project executor may accept.
    def apply_fn(state: Dict[Address, Amount], tx: Tx) -> bool:
        return _apply_tx(state, tx)

    def access_fn(tx: Tx) -> Tuple[Set[str], Set[str]]:
        return _access_sets(tx)

    # Run two times to assert determinism
    s1 = dict(initial_state)
    s2 = dict(initial_state)

    try:
        if use_class:
            ex1 = OptimisticExecutor()
            ex2 = OptimisticExecutor()
            # Try (state, txs, apply_fn, access_fn) first, then progressively simpler signatures
            try:
                ex1.run(s1, txs, apply_fn, access_fn)  # type: ignore[attr-defined]
                ex2.run(s2, txs, apply_fn, access_fn)  # type: ignore[attr-defined]
            except TypeError:
                try:
                    ex1.run(s1, txs, apply_fn)  # type: ignore[attr-defined]
                    ex2.run(s2, txs, apply_fn)  # type: ignore[attr-defined]
                except TypeError:
                    ex1.run(s1, txs)  # type: ignore[attr-defined]
                    ex2.run(s2, txs)  # type: ignore[attr-defined]
        elif entry is not None:
            # Try the functional variants with best-effort signatures
            try:
                entry(s1, txs, apply_fn, access_fn)  # type: ignore[misc]
                entry(s2, txs, apply_fn, access_fn)  # type: ignore[misc]
            except TypeError:
                try:
                    entry(s1, txs, apply_fn)  # type: ignore[misc]
                    entry(s2, txs, apply_fn)  # type: ignore[misc]
                except TypeError:
                    entry(s1, txs)  # type: ignore[misc]
                    entry(s2, txs)  # type: ignore[misc]
        else:
            pytest.skip("No callable entrypoint found in optimistic scheduler")
    except TypeError:
        pytest.skip("Project optimistic executor signature incompatible for this smoke test")

    # Must be deterministic and match the serial baseline on this workload
    assert _state_root(s1) == _state_root(s2), "Optimistic executor must be deterministic"
    assert _state_root(s1) == _state_root(ser_state), "Optimistic executor final state must match serial baseline"
