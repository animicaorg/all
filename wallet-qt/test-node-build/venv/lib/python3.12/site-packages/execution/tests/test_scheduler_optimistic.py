import hashlib
import random
from dataclasses import dataclass
from typing import Dict, List, Set, Tuple

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


def _serial_apply(
    initial: Dict[Address, Amount], txs: List[Tx]
) -> Tuple[Dict[Address, Amount], List[int]]:
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
    
    Note: Recipient balance is both read and written, since in a full VM
    the recipient's current balance must be loaded before adding to it.
    This conservative approach ensures serial equivalence.
    """
    r: Set[str] = {f"bal:{tx.sender}", f"nonce:{tx.sender}", f"bal:{tx.to}"}
    w: Set[str] = {f"bal:{tx.sender}", f"bal:{tx.to}", f"nonce:{tx.sender}"}
    return r, w


def _optimistic_layers(txs: List[Tx]) -> List[List[int]]:
    """
    Partition tx indices into conflict-free layers based on read/write sets.
    Greedy layering: preserve original order while grouping non-conflicting txs.
    Conflicts: any R/W or W/R or W/W intersection.
    
    Key invariant: if tx_i conflicts with tx_j and i < j, then tx_i must be in
    a layer that executes before tx_j's layer (to preserve input order).
    """
    layers: List[List[int]] = []
    # Track which layer each transaction index was placed in
    tx_layer: Dict[int, int] = {}
    
    for idx, tx in enumerate(txs):
        r, w = _access_sets(tx)
        
        # Find the minimum layer index for this tx by checking all previously
        # placed transactions for conflicts. If this tx conflicts with a previous
        # tx that's in layer L, this tx must go in layer L+1 or later.
        min_layer_idx = 0
        for prev_idx in range(idx):
            if prev_idx not in tx_layer:
                continue  # Previous tx wasn't placed (shouldn't happen in this model)
            prev_r, prev_w = _access_sets(txs[prev_idx])
            # Check if current tx conflicts with previous tx
            if (w & prev_w) or (w & prev_r) or (r & prev_w):
                # Conflict: current tx must go AFTER prev tx's layer
                min_layer_idx = max(min_layer_idx, tx_layer[prev_idx] + 1)
        
        # Now try to place in the earliest layer >= min_layer_idx where there's no conflict
        placed = False
        for layer_idx in range(min_layer_idx, len(layers)):
            layer = layers[layer_idx]
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
                tx_layer[idx] = layer_idx
                placed = True
                break
        
        if not placed:
            # Create new layer at index min_layer_idx or later
            new_layer_idx = len(layers)
            layers.append([idx])
            tx_layer[idx] = new_layer_idx
    
    return layers


def _optimistic_apply(
    initial: Dict[Address, Amount], txs: List[Tx]
) -> Tuple[Dict[Address, Amount], List[List[int]]]:
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
        "eve": 0,
        "frank": 0,
    }


@pytest.fixture
def non_conflicting_batch() -> List[Tx]:
    # Disjoint senders and distinct recipients → should layer into one batch
    # alice→carol, bob→carol, dave→eve are all non-conflicting (disjoint address sets for writes)
    return [
        Tx("alice", "eve", 10, 0),    # alice writes bal:alice, eve writes bal:eve
        Tx("bob", "carol", 5, 0),     # bob writes bal:bob, carol writes bal:carol
        Tx("dave", "frank", 3, 0),    # dave writes bal:dave, frank writes bal:frank
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


def test_conflict_same_sender_partitions_layers_and_matches_serial(
    initial_state, conflicting_batch_same_sender
):
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
        opt_mod = __import__(
            "execution.scheduler.optimistic",
            fromlist=["OptimisticExecutor", "run", "execute", "apply_layers"],
        )
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
        pytest.skip(
            "Project optimistic executor signature incompatible for this smoke test"
        )

    # Must be deterministic and match the serial baseline on this workload
    assert _state_root(s1) == _state_root(
        s2
    ), "Optimistic executor must be deterministic"
    assert _state_root(s1) == _state_root(
        ser_state
    ), "Optimistic executor final state must match serial baseline"
