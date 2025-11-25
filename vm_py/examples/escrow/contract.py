"""
Simple Escrow contract (Animica Python-VM example).

Design (kept intentionally minimal for determinism and easy auditing):

- setup(depositor: bytes, beneficiary: bytes, amount: int)
    One-time initializer. Stores depositor/beneficiary and the required escrow
    amount (u64). No funds are moved here.

- status() -> bytes
    Returns the current state: b"INIT" | b"FUNDED" | b"RELEASED" | b"REFUNDED".

- ready() -> bool
    Returns True if the contract treasury balance is ≥ required amount.

- deposit_check() -> None
    Marks the escrow FUNDED iff treasury.balance() ≥ amount. Emits Escrow.Funded.

- release() -> None
    Transfers exactly `amount` to the beneficiary if FUNDED and sufficient
    balance is present; moves to RELEASED. Emits Escrow.Released.

- refund() -> None
    If not RELEASED, transfers min(balance, amount) back to depositor and moves
    to REFUNDED. Emits Escrow.Refunded.

Notes / Simplifications:
- This example does not enforce caller authorization inside the VM. In a full
  node, the execution layer can expose tx sender for auth. Here we focus on the
  treasury flow and deterministic state updates for demos/tests.
- Addresses are treated as opaque bytes (bech32-encoded addresses should be
  decoded by tooling before calling into the VM).
"""

from stdlib import storage, events, abi, treasury  # deterministic stdlib

# Storage keys
_KEY_STATE = b"escrow:state"
_KEY_DEPOSITOR = b"escrow:depositor"
_KEY_BENEFICIARY = b"escrow:beneficiary"
_KEY_AMOUNT = b"escrow:amount_u64"

# States
STATE_INIT = b"INIT"
STATE_FUNDED = b"FUNDED"
STATE_RELEASED = b"RELEASED"
STATE_REFUNDED = b"REFUNDED"


def _u64_to_bytes(x: int) -> bytes:
    abi.require(0 <= x <= 0xFFFFFFFFFFFFFFFF, b"escrow: amount u64")
    return x.to_bytes(8, "big", signed=False)


def _u64_from_bytes(b: bytes | None) -> int:
    if not b:
        return 0
    if len(b) != 8:
        return 0
    return int.from_bytes(b, "big", signed=False)


def _set_state(s: bytes) -> None:
    storage.set(_KEY_STATE, s)


def _get_state() -> bytes:
    b = storage.get(_KEY_STATE)
    return b if b is not None and len(b) > 0 else STATE_INIT


def _get_amount() -> int:
    return _u64_from_bytes(storage.get(_KEY_AMOUNT))


def _get_depositor() -> bytes:
    v = storage.get(_KEY_DEPOSITOR)
    return v if v is not None else b""


def _get_beneficiary() -> bytes:
    v = storage.get(_KEY_BENEFICIARY)
    return v if v is not None else b""


def setup(depositor: bytes, beneficiary: bytes, amount: int) -> None:
    """
    Initialize the escrow parameters. Callable exactly once.

    Invariants:
    - amount is a positive u64
    - depositor and beneficiary are non-empty (opaque) byte strings
    """
    abi.require(_get_state() == STATE_INIT, b"escrow: already inited")
    abi.require(isinstance(depositor, (bytes, bytearray)) and len(depositor) > 0, b"escrow: bad depositor")
    abi.require(isinstance(beneficiary, (bytes, bytearray)) and len(beneficiary) > 0, b"escrow: bad beneficiary")
    abi.require(isinstance(amount, int) and amount > 0, b"escrow: bad amount")

    storage.set(_KEY_DEPOSITOR, bytes(depositor))
    storage.set(_KEY_BENEFICIARY, bytes(beneficiary))
    storage.set(_KEY_AMOUNT, _u64_to_bytes(amount))
    _set_state(STATE_INIT)

    events.emit(b"Escrow.Setup", {b"amount": amount})


def status() -> bytes:
    """Return the current state label."""
    return _get_state()


def ready() -> bool:
    """True if the contract balance is ≥ required amount."""
    req = _get_amount()
    return treasury.balance() >= req if req > 0 else False


def deposit_check() -> None:
    """
    Mark FUNDED if contract balance covers required amount.
    No funds are moved here; deposit is assumed to have been made out-of-band.
    """
    abi.require(_get_state() in (STATE_INIT, STATE_FUNDED), b"escrow: immutable after finish")
    req = _get_amount()
    abi.require(req > 0, b"escrow: not configured")
    if treasury.balance() >= req:
        _set_state(STATE_FUNDED)
        events.emit(b"Escrow.Funded", {b"amount": req})


def release() -> None:
    """
    Transfer the escrowed amount to the beneficiary if FUNDED.

    Moves state to RELEASED on success.
    """
    abi.require(_get_state() == STATE_FUNDED, b"escrow: not funded")
    req = _get_amount()
    abi.require(treasury.balance() >= req, b"escrow: insufficient funds")

    beneficiary = _get_beneficiary()
    abi.require(len(beneficiary) > 0, b"escrow: no beneficiary")

    # Move funds from the contract treasury to the beneficiary.
    treasury.transfer(beneficiary, req)

    _set_state(STATE_RELEASED)
    events.emit(b"Escrow.Released", {b"to": beneficiary, b"amount": req})


def refund() -> None:
    """
    Refund depositor with min(balance, amount) if not RELEASED yet.

    Moves state to REFUNDED.
    """
    st = _get_state()
    abi.require(st != STATE_RELEASED, b"escrow: already released")

    depositor = _get_depositor()
    abi.require(len(depositor) > 0, b"escrow: no depositor")

    req = _get_amount()
    bal = treasury.balance()
    if bal == 0:
        # Nothing to refund; still mark as REFUNDED to prevent further actions.
        _set_state(STATE_REFUNDED)
        events.emit(b"Escrow.Refunded", {b"to": depositor, b"amount": 0})
        return

    amt = req if bal >= req else bal
    treasury.transfer(depositor, amt)

    _set_state(STATE_REFUNDED)
    events.emit(b"Escrow.Refunded", {b"to": depositor, b"amount": amt})

# --- Animica test-compat shim: disallow calling setup() twice ---

# Only install the wrapper once, even if this module is reloaded.
if "_setup_wrapped" not in globals():
    # Keep a handle to the original setup implementation.
    _orig_setup = setup  # type: ignore[name-defined]

    def _setup_wrapped(payer: bytes, payee: bytes, amount: int) -> None:
        """
        Wrapper around the original setup() that enforces single-use semantics.

        Calling setup() a second time must raise VmError so the escrow
        cannot be silently reconfigured.
        """
        # Treat a non-zero configured amount as "already configured".
        current = _get_amount()  # type: ignore[name-defined]
        abi.require(current == 0, b"escrow: already inited")

        return _orig_setup(payer, payee, amount)

    # Expose wrapped version as the public entrypoint.
    setup = _setup_wrapped  # type: ignore[assignment]
