# Deterministic Escrow (demo)
# - One-slot escrow that locks a specified amount and releases it to a beneficiary.
# - Contract must be pre-funded (via a separate transfer) before calling lock().
# - No wall clock, no randomness, no network I/O; all effects are deterministic.

from stdlib import abi, events, storage, treasury  # type: ignore

KEY_BENEFICIARY = b"beneficiary"
KEY_AMOUNT = b"amount"


def _require(cond: bool, msg: str) -> None:
    if not cond:
        abi.revert(msg.encode("utf-8"))


def _get_amount() -> int:
    try:
        return storage.get_int(KEY_AMOUNT, 0)  # type: ignore[attr-defined]
    except AttributeError:
        # Fallback if only raw get/set exist: stored as big-endian bytes
        raw = storage.get(KEY_AMOUNT) or b""
        return int.from_bytes(raw, "big") if raw else 0


def _set_amount(val: int) -> None:
    try:
        storage.set_int(KEY_AMOUNT, val)  # type: ignore[attr-defined]
    except AttributeError:
        storage.set(KEY_AMOUNT, int(val).to_bytes(32, "big"))


def _get_beneficiary() -> bytes:
    return storage.get(KEY_BENEFICIARY) or b""


def _set_beneficiary(addr: bytes) -> None:
    storage.set(KEY_BENEFICIARY, addr)


def status() -> dict:
    """
    Returns the current escrow status:
    {
      "amount": int,
      "beneficiary": bytes,
      "contract_balance": int
    }
    """
    return {
        "amount": _get_amount(),
        "beneficiary": _get_beneficiary(),
        "contract_balance": treasury.balance(),
    }


def lock(beneficiary: bytes, amount: int) -> int:
    """
    Lock `amount` for `beneficiary`.
    Requires:
      - No existing active lock (amount == 0)
      - amount > 0
      - treasury.balance() >= amount (contract pre-funded)
    Effects:
      - Stores beneficiary and amount
      - Emits Locked(by, amount)
    """
    _require(amount > 0, "LOCK_AMOUNT_MUST_BE_POSITIVE")
    _require(_get_amount() == 0, "ESCROW_ALREADY_LOCKED")
    _require(treasury.balance() >= amount, "INSUFFICIENT_CONTRACT_FUNDS")

    _set_beneficiary(beneficiary)
    _set_amount(amount)

    events.emit(b"Locked", {"to": beneficiary, "amount": amount})
    return amount


def release() -> int:
    """
    Release the currently locked amount to the beneficiary.
    Requires:
      - An active lock (amount > 0)
      - Beneficiary set (non-empty)
    Effects:
      - Transfers amount to beneficiary
      - Clears lock
      - Emits Released(to, amount)
    """
    amount = _get_amount()
    _require(amount > 0, "NO_ACTIVE_LOCK")

    beneficiary = _get_beneficiary()
    _require(len(beneficiary) > 0, "NO_BENEFICIARY_SET")

    treasury.transfer(beneficiary, amount)
    _set_amount(0)
    _set_beneficiary(b"")

    events.emit(b"Released", {"to": beneficiary, "amount": amount})
    return amount


def cancel(refund_to: bytes) -> int:
    """
    Cancel the current escrow and refund the locked amount to `refund_to`.
    Suitable for demos where the funder and refund destination are chosen off-chain.
    Requires:
      - An active lock (amount > 0)
    Effects:
      - Transfers amount to refund_to
      - Clears lock
      - Emits Canceled(to, amount)
    """
    amount = _get_amount()
    _require(amount > 0, "NO_ACTIVE_LOCK")

    treasury.transfer(refund_to, amount)
    _set_amount(0)
    _set_beneficiary(b"")

    events.emit(b"Canceled", {"to": refund_to, "amount": amount})
    return amount
