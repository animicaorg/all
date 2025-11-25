# Deterministic Escrow example for the Animica Python VM
# ------------------------------------------------------
# This contract follows the deterministic subset and uses stdlib modules for
# storage, events, treasury, and ABI helpers. It implements a classic escrow
# with optional dispute & arbiter resolution.
#
# Interface (see README for details):
#   init(buyer, seller, arbiter, amount, deadline_height)
#   deposit()
#   release()
#   refund()
#   dispute(reason)
#   resolve(to_seller)
#   cancel_before_deposit()
#   state()      -> dict
#   balance()    -> int
#   parties()    -> dict
#
# Notes:
# - Addresses are 32-byte values at the ABI boundary (contracts & tooling pass
#   canonical address bytes; UI may present bech32m but ABI inputs are bytes).
# - Integers are non-negative and bounded (amount fits within 128-bit).
# - Time is modeled as block height only for determinism.

from stdlib import storage, events, treasury, abi  # provided by the Animica VM

# ---- storage keys (bytes constants; small & stable) ---------------------------------
K_INIT            = b"i"   # 1 if initialized
K_BUYER           = b"b"
K_SELLER          = b"s"
K_ARBITER         = b"a"
K_AMOUNT          = b"A"   # u128
K_DEADLINE        = b"D"   # u64 (block height)
K_DEPOSITED       = b"d"   # 0/1
K_DISPUTED        = b"X"   # 0/1
K_FINALIZED       = b"F"   # 0/1

# ---- small codec helpers (deterministic & total) ------------------------------------

def _require_address(x: bytes) -> None:
    # address is always 32 bytes at the VM ABI boundary
    abi.require(isinstance(x, (bytes, bytearray)) and len(x) == 32, b"bad address")

def _put_bool(k: bytes, v: bool) -> None:
    storage.set(k, b"\x01" if v else b"\x00")

def _get_bool(k: bytes) -> bool:
    v = storage.get(k)
    if v is None:
        return False
    # Accept any non-zero as True to be robust to historical values
    return len(v) > 0 and v[0] != 0

def _put_u128(k: bytes, n: int) -> None:
    abi.require(isinstance(n, int) and n >= 0, b"bad int")
    # 16-byte big-endian fixed width encoding
    storage.set(k, n.to_bytes(16, "big"))

def _get_u128(k: bytes) -> int:
    v = storage.get(k)
    if v is None:
        return 0
    abi.require(isinstance(v, (bytes, bytearray)) and len(v) == 16, b"corrupt u128")
    return int.from_bytes(v, "big")

def _put_u64(k: bytes, n: int) -> None:
    abi.require(isinstance(n, int) and 0 <= n <= 0xFFFFFFFFFFFFFFFF, b"bad height")
    storage.set(k, n.to_bytes(8, "big"))

def _get_u64(k: bytes) -> int:
    v = storage.get(k)
    if v is None:
        return 0
    abi.require(isinstance(v, (bytes, bytearray)) and len(v) == 8, b"corrupt u64")
    return int.from_bytes(v, "big")

def _put_addr(k: bytes, addr: bytes) -> None:
    _require_address(addr)
    storage.set(k, bytes(addr))

def _get_addr(k: bytes) -> bytes:
    v = storage.get(k)
    abi.require(isinstance(v, (bytes, bytearray)) and len(v) == 32, b"missing address")
    return bytes(v)

# ---- role checks & guards -----------------------------------------------------------

def _guard_not_finalized() -> None:
    abi.require(not _get_bool(K_FINALIZED), b"finalized")

def _guard_inited() -> None:
    abi.require(storage.get(K_INIT) == b"\x01", b"not inited")

def _guard_not_inited() -> None:
    abi.require(storage.get(K_INIT) is None, b"already inited")

def _is_party(caller: bytes) -> bool:
    return caller == _get_addr(K_BUYER) or caller == _get_addr(K_SELLER)

# ---- events (canonical names; encoded via stdlib.events) ----------------------------

EV_DEPOSITED = b"Deposited"
EV_RELEASED  = b"Released"
EV_REFUNDED  = b"Refunded"
EV_DISPUTED  = b"Disputed"
EV_RESOLVED  = b"Resolved"
EV_CANCELLED = b"Cancelled"

# ---- public entrypoints -------------------------------------------------------------

def init(buyer: bytes, seller: bytes, arbiter: bytes, amount: int, deadline_height: int) -> None:
    """
    One-time initializer. Sets parties, amount, and deadline (block height).
    """
    _guard_not_inited()
    _require_address(buyer)
    _require_address(seller)
    _require_address(arbiter)

    abi.require(isinstance(amount, int) and amount > 0, b"bad amount")
    abi.require(isinstance(deadline_height, int) and deadline_height >= 0, b"bad deadline")

    _put_addr(K_BUYER, buyer)
    _put_addr(K_SELLER, seller)
    _put_addr(K_ARBITER, arbiter)
    _put_u128(K_AMOUNT, amount)
    _put_u64(K_DEADLINE, deadline_height)

    _put_bool(K_DEPOSITED, False)
    _put_bool(K_DISPUTED, False)
    _put_bool(K_FINALIZED, False)
    storage.set(K_INIT, b"\x01")

def deposit() -> None:
    """
    Buyer acknowledges deposit. The call MUST be sent with value == amount so that
    treasury.balance() reflects the funds. We only mark the deposit and validate balance.
    """
    _guard_inited()
    _guard_not_finalized()
    abi.require(not _get_bool(K_DEPOSITED), b"already deposited")

    caller = abi.sender()
    abi.require(caller == _get_addr(K_BUYER), b"only buyer")

    amount = _get_u128(K_AMOUNT)
    abi.require(treasury.balance() >= amount, b"insufficient escrow balance")

    _put_bool(K_DEPOSITED, True)
    events.emit(EV_DEPOSITED, {b"buyer": caller, b"amount": amount})

def release() -> None:
    """
    Release funds to seller. Only buyer may release; only if deposited, not disputed.
    """
    _guard_inited()
    _guard_not_finalized()
    abi.require(_get_bool(K_DEPOSITED), b"not deposited")
    abi.require(not _get_bool(K_DISPUTED), b"in dispute")

    caller = abi.sender()
    abi.require(caller == _get_addr(K_BUYER), b"only buyer")

    amount = _get_u128(K_AMOUNT)
    seller = _get_addr(K_SELLER)

    abi.require(treasury.balance() >= amount, b"escrow underfunded")
    treasury.transfer(seller, amount)

    _put_bool(K_FINALIZED, True)
    events.emit(EV_RELEASED, {b"seller": seller, b"amount": amount})

def refund() -> None:
    """
    Refund buyer after deadline if no dispute. Only buyer can trigger.
    """
    _guard_inited()
    _guard_not_finalized()
    abi.require(_get_bool(K_DEPOSITED), b"not deposited")
    abi.require(not _get_bool(K_DISPUTED), b"in dispute")

    height = abi.block_height()
    deadline = _get_u64(K_DEADLINE)
    abi.require(height >= deadline, b"deadline not reached")

    caller = abi.sender()
    buyer = _get_addr(K_BUYER)
    abi.require(caller == buyer, b"only buyer")

    amount = _get_u128(K_AMOUNT)
    abi.require(treasury.balance() >= amount, b"escrow underfunded")

    treasury.transfer(buyer, amount)

    _put_bool(K_FINALIZED, True)
    events.emit(EV_REFUNDED, {b"buyer": buyer, b"amount": amount})

def dispute(reason: bytes) -> None:
    """
    Open a dispute; either Buyer or Seller may do so prior to finalization.
    Stores only a flag (reason is emitted for indexing; not stored).
    """
    _guard_inited()
    _guard_not_finalized()
    abi.require(_get_bool(K_DEPOSITED), b"not deposited")
    abi.require(not _get_bool(K_DISPUTED), b"already disputed")

    caller = abi.sender()
    abi.require(_is_party(caller), b"only parties")

    # Cap reason length to keep events bounded
    abi.require(isinstance(reason, (bytes, bytearray)), b"bad reason")
    abi.require(len(reason) <= 256, b"reason too long")

    _put_bool(K_DISPUTED, True)
    events.emit(EV_DISPUTED, {b"opener": caller, b"reason": bytes(reason)})

def resolve(to_seller: bool) -> None:
    """
    Arbiter resolution for an open dispute. Pays either Seller or Buyer.
    """
    _guard_inited()
    _guard_not_finalized()
    abi.require(_get_bool(K_DEPOSITED), b"not deposited")
    abi.require(_get_bool(K_DISPUTED), b"no dispute")

    caller = abi.sender()
    abi.require(caller == _get_addr(K_ARBITER), b"only arbiter")

    amount = _get_u128(K_AMOUNT)
    abi.require(treasury.balance() >= amount, b"escrow underfunded")

    if to_seller:
        dest = _get_addr(K_SELLER)
    else:
        dest = _get_addr(K_BUYER)

    treasury.transfer(dest, amount)

    _put_bool(K_FINALIZED, True)
    events.emit(EV_RESOLVED, {b"arbiter": caller, b"to_seller": bool(to_seller), b"amount": amount})

def cancel_before_deposit() -> None:
    """
    Optional convenience: either party may cancel before any deposit occurred.
    This does not move funds (balance must be zero at this stage).
    """
    _guard_inited()
    _guard_not_finalized()
    abi.require(not _get_bool(K_DEPOSITED), b"already deposited")

    caller = abi.sender()
    abi.require(_is_party(caller), b"only parties")

    # No funds should exist, but assert conservation anyway.
    abi.require(treasury.balance() == 0, b"nonzero balance")

    _put_bool(K_FINALIZED, True)
    events.emit(EV_CANCELLED, {})

# ---- views -------------------------------------------------------------------------

def state() -> dict:
    """
    Return a complete snapshot of escrow state for UIs & indexers.
    """
    inited = storage.get(K_INIT) == b"\x01"
    if not inited:
        # For pre-init calls, provide a consistent empty shape
        return {
            b"inited": False,
            b"buyer": b"\x00" * 32,
            b"seller": b"\x00" * 32,
            b"arbiter": b"\x00" * 32,
            b"amount": 0,
            b"deadline_height": 0,
            b"deposited": False,
            b"disputed": False,
            b"finalized": False,
        }

    return {
        b"inited": True,
        b"buyer": _get_addr(K_BUYER),
        b"seller": _get_addr(K_SELLER),
        b"arbiter": _get_addr(K_ARBITER),
        b"amount": _get_u128(K_AMOUNT),
        b"deadline_height": _get_u64(K_DEADLINE),
        b"deposited": _get_bool(K_DEPOSITED),
        b"disputed": _get_bool(K_DISPUTED),
        b"finalized": _get_bool(K_FINALIZED),
    }

def balance() -> int:
    """
    Current escrow balance (should be either 0 or 'amount').
    """
    return treasury.balance()

def parties() -> dict:
    """
    Return buyer/seller/arbiter addresses.
    """
    if storage.get(K_INIT) != b"\x01":
        return {b"buyer": b"\x00" * 32, b"seller": b"\x00" * 32, b"arbiter": b"\x00" * 32}
    return {b"buyer": _get_addr(K_BUYER), b"seller": _get_addr(K_SELLER), b"arbiter": _get_addr(K_ARBITER)}
