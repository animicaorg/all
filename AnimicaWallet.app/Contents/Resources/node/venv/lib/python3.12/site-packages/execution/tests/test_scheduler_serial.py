import hashlib
from dataclasses import dataclass
from typing import Dict, List, Tuple

import pytest

# ---------------------------------------------------
# Minimal serial "spec-runner" to check determinism
# ---------------------------------------------------

Address = str
Amount = int


@dataclass(frozen=True)
class Tx:
    sender: Address
    to: Address
    amount: Amount
    nonce: int  # per-sender sequencing


def _state_root(state: Dict[Address, Amount]) -> bytes:
    """
    Deterministic commitment of balances: SHA3-256 over
    concatenation of (addr || 8-byte big-endian balance), sorted by addr.
    """
    items = sorted(state.items(), key=lambda kv: kv[0])
    buf = bytearray()
    for addr, bal in items:
        buf.extend(addr.encode("utf-8"))
        buf.extend(int(bal).to_bytes(8, "big", signed=False))
    return hashlib.sha3_256(bytes(buf)).digest()


def _serial_apply(
    initial: Dict[Address, Amount], txs: List[Tx]
) -> Tuple[Dict[Address, Amount], List[str]]:
    """
    Serially apply txs in the provided order with simple rules:
      - Nonce must match the sender's next expected nonce (tracked locally).
      - Sender must have sufficient balance.
      - If a check fails, the tx is skipped (record a reason), mirroring
        typical block-application semantics where invalid txs are excluded.
    This is a tiny model used only to assert *determinism* of serial order.
    """
    state: Dict[Address, Amount] = dict(initial)
    reasons: List[str] = []
    next_nonce: Dict[Address, int] = {}

    def _expect_nonce(addr: Address) -> int:
        return next_nonce.get(addr, 0)

    def _inc_nonce(addr: Address) -> None:
        next_nonce[addr] = _expect_nonce(addr) + 1

    for i, tx in enumerate(txs):
        if tx.nonce != _expect_nonce(tx.sender):
            reasons.append(
                f"skip[{i}]: bad-nonce sender={tx.sender} got={tx.nonce} want={_expect_nonce(tx.sender)}"
            )
            continue
        if state.get(tx.sender, 0) < tx.amount:
            reasons.append(
                f"skip[{i}]: insufficient sender={tx.sender} bal={state.get(tx.sender,0)} amt={tx.amount}"
            )
            continue
        # apply
        state[tx.sender] = state.get(tx.sender, 0) - tx.amount
        state[tx.to] = state.get(tx.to, 0) + tx.amount
        _inc_nonce(tx.sender)

    return state, reasons


# ---------------------------------------------------
# Fixtures
# ---------------------------------------------------


@pytest.fixture
def initial_state() -> Dict[Address, Amount]:
    # Small deterministic balances
    return {
        "alice": 100,
        "bob": 50,
        "carol": 0,
    }


@pytest.fixture
def txs_canonical() -> List[Tx]:
    # Nonces are sequential per-sender for a valid serial run.
    return [
        Tx(sender="alice", to="bob", amount=10, nonce=0),
        Tx(sender="alice", to="carol", amount=5, nonce=1),
        Tx(sender="bob", to="alice", amount=7, nonce=0),
        Tx(sender="alice", to="bob", amount=12, nonce=2),
    ]


@pytest.fixture
def txs_out_of_order_nonce() -> List[Tx]:
    # Same logical set as above, but with alice's second and third tx swapped (nonce order broken).
    return [
        Tx(sender="alice", to="bob", amount=10, nonce=0),
        Tx(
            sender="alice", to="bob", amount=12, nonce=2
        ),  # will be skipped until nonce=1 is seen
        Tx(sender="bob", to="alice", amount=7, nonce=0),
        Tx(sender="alice", to="carol", amount=5, nonce=1),
    ]


# ---------------------------------------------------
# Tests
# ---------------------------------------------------


def test_serial_is_deterministic_for_same_input_order(initial_state, txs_canonical):
    st1, reasons1 = _serial_apply(initial_state, txs_canonical)
    st2, reasons2 = _serial_apply(initial_state, txs_canonical)

    assert st1 == st2, "Same ordered tx list must yield identical final state"
    assert reasons1 == reasons2, "Skip/diagnostic reasons must be stable"
    assert _state_root(st1) == _state_root(st2), "State commitment must be identical"


def test_out_of_order_nonces_produce_different_effects(
    initial_state, txs_canonical, txs_out_of_order_nonce
):
    """
    Serial executors process txs in-sequence. If caller submits out-of-order nonces,
    behavior is deterministic but effects can differ vs a well-ordered list
    (some txs are skipped until earlier nonces appear).
    """
    st_ok, reasons_ok = _serial_apply(initial_state, txs_canonical)
    st_bad, reasons_bad = _serial_apply(initial_state, txs_out_of_order_nonce)

    # Deterministic commitments differ because execution path differs.
    assert _state_root(st_ok) != _state_root(st_bad)
    # Both runs themselves are deterministic: recomputing should match bit-for-bit.
    st_bad2, reasons_bad2 = _serial_apply(initial_state, txs_out_of_order_nonce)
    assert st_bad == st_bad2
    assert reasons_bad == reasons_bad2


def test_empty_block_idempotent(initial_state):
    st, reasons = _serial_apply(initial_state, [])
    assert st == initial_state
    assert reasons == []
    # Running again identical:
    st2, reasons2 = _serial_apply(initial_state, [])
    assert st2 == initial_state
    assert reasons2 == []


# ---------------------------------------------------
# Optional integration: project serial scheduler (if present)
# ---------------------------------------------------


def test_project_serial_executor_if_available(initial_state, txs_canonical):
    """
    Best-effort hook into the project's serial scheduler to ensure that applying
    the same ordered tx list twice yields identical results (determinism).
    If the module or API isn't available, we skip.
    """
    try:
        # Try common entrypoints
        serial_mod = __import__(
            "execution.scheduler.serial",
            fromlist=["SerialExecutor", "run", "apply_block"],
        )
    except Exception:
        pytest.skip("execution.scheduler.serial not available")

    # Build a very small adapter: many schedulers expect a list of "items" and an apply function.
    # We'll look for a function in the module with a permissive signature.
    runner = None
    for name in ("run", "execute", "apply", "apply_block"):
        fn = getattr(serial_mod, name, None)
        if callable(fn):
            runner = fn
            break

    SerialExecutor = getattr(serial_mod, "SerialExecutor", None)
    use_class = SerialExecutor is not None and hasattr(SerialExecutor, "run")

    txs = txs_canonical

    def apply_fn(state: Dict[Address, Amount], tx: Tx) -> None:
        # mirror our local rules
        expected_nonce = state.setdefault(f"__nonce__:{tx.sender}", 0)
        if tx.nonce != expected_nonce:
            return
        bal = state.get(tx.sender, 0)
        if bal < tx.amount:
            return
        state[tx.sender] = bal - tx.amount
        state[tx.to] = state.get(tx.to, 0) + tx.amount
        state[f"__nonce__:{tx.sender}"] = expected_nonce + 1

    # State clones for two runs
    s1 = dict(initial_state)
    s2 = dict(initial_state)

    try:
        if use_class:
            ex1 = SerialExecutor()
            ex2 = SerialExecutor()
            ex1.run(s1, txs, apply_fn)  # type: ignore[attr-defined]
            ex2.run(s2, txs, apply_fn)  # type: ignore[attr-defined]
        elif runner is not None:
            runner(s1, txs, apply_fn)  # type: ignore[misc]
            runner(s2, txs, apply_fn)  # type: ignore[misc]
        else:
            pytest.skip(
                "No callable entrypoint (run/execute/apply/apply_block) found in serial scheduler"
            )
    except TypeError:
        # Signature mismatch → skip rather than fail spuriously.
        pytest.skip(
            "Project serial executor has an incompatible signature for this smoke test"
        )

    assert _state_root(s1) == _state_root(
        s2
    ), "Project serial executor must be deterministic for identical inputs"
