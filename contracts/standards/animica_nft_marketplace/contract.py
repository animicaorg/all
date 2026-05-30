"""
Animica NFT Marketplace contract (Python-VM).

The on-chain core of the Animica NFT marketplace. Holds listings,
escrows seller intent (NOT the NFT itself — the seller keeps custody and
just approves the marketplace as an operator), and atomically settles
sales in ANM with marketplace fee + EIP-2981 creator royalty.

Architecture
------------

Listings are pull-style — the seller does NOT transfer the NFT into
the marketplace contract. They only call
`set_approval_for_all(marketplace_addr, true)` on the collection
contract once, then call `list(collection, token_id, price)`. The NFT
stays in the seller's wallet until a buyer pays.

When a buyer calls `buy(listing_id)` with sufficient ANM attached,
the marketplace atomically:

  1. Validates the listing is ACTIVE and the seller still owns the NFT.
  2. Computes the fee split: marketplace fee, creator royalty, seller
     payout. Sum is exactly `price`; any rounding goes to the seller.
  3. Transfers ANM out of the contract escrow:
       - fee → fee_recipient (Animica treasury)
       - royalty → royalty receiver from the collection's royalty_info
       - rest → seller
  4. Invokes `transfer_from(seller, buyer, token_id)` on the collection
     contract. This requires the seller's prior operator approval.
  5. Marks the listing SOLD and emits Sold.

If any of those steps revert, the whole transaction reverts and no ANM
moves. There is no partial-fill state — listings are all-or-nothing.

Reentrancy
----------

The marketplace is the *caller* in step 4, so a malicious collection
could re-enter `buy()` of a different listing. We guard against this
with a coarse-grained boolean lock (`anm_mkt:reent`) that wraps every
mutating entry-point. A second mutating call inside the same outer
transaction reverts with `b"reentrant"`. The fee math is also done
before the external call to avoid TOCTOU issues even within a single
listing.

Sellers can cancel an active listing at any time with `cancel()`. A
buyer who calls `buy()` on a cancelled or sold listing reverts.

Storage layout
--------------

  anm_mkt:init                 u1   initialized?
  anm_mkt:owner                bytes  marketplace admin
  anm_mkt:fee_recipient        bytes  Animica treasury
  anm_mkt:fee_bps              uint   marketplace fee (basis points)
  anm_mkt:reent                u1    reentrancy lock
  anm_mkt:next_listing_id      uint   monotonic listing counter
  anm_mkt:paused               u1    emergency pause switch

  anm_mkt:l:{id}:collection    bytes  collection contract address
  anm_mkt:l:{id}:token_id      uint   token id
  anm_mkt:l:{id}:seller        bytes  seller address (snapshot)
  anm_mkt:l:{id}:price         uint   price in nanos (1 ANM = 1e9 nanos)
  anm_mkt:l:{id}:state         bytes  ACTIVE | SOLD | CANCELLED
  anm_mkt:l:{id}:created_at    uint   tx block timestamp at listing time

  anm_mkt:by_token:{coll}:{tid} uint  reverse index — at most one ACTIVE listing
                                       per (collection, token_id) at a time

Events
------
  Listed(listing_id, collection, token_id, seller, price)
  Sold(listing_id, buyer, fee, royalty, seller_proceeds, royalty_receiver)
  Cancelled(listing_id, seller)
  FeeUpdated(new_bps)
  Paused(paused)
"""

from __future__ import annotations

from stdlib import abi, events, storage, treasury


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_FEE_BPS = 1000          # 10% cap on marketplace fee
MAX_ROYALTY_BPS = 1000      # mirror of ANM-721 royalty cap

STATE_ACTIVE = b"ACTIVE"
STATE_SOLD = b"SOLD"
STATE_CANCELLED = b"CANCELLED"

K_INIT = b"anm_mkt:init"
K_OWNER = b"anm_mkt:owner"
K_FEE_RECIPIENT = b"anm_mkt:fee_recipient"
K_FEE_BPS = b"anm_mkt:fee_bps"
K_REENT = b"anm_mkt:reent"
K_NEXT_ID = b"anm_mkt:next_listing_id"
K_PAUSED = b"anm_mkt:paused"


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------


def _u_to_b(value: int) -> bytes:
    v = int(value)
    abi.require(v >= 0, b"negative")
    if v == 0:
        return b""
    return v.to_bytes(max(1, (v.bit_length() + 7) // 8), "big")


def _b_to_u(raw: bytes) -> int:
    if raw is None or raw == b"":
        return 0
    return int.from_bytes(raw, "big")


def _ug(key: bytes) -> int:
    return _b_to_u(storage.get(key, b""))


def _us(key: bytes, value: int) -> None:
    v = int(value)
    abi.require(v >= 0, b"negative")
    if v == 0:
        storage.delete(key)
        return
    storage.set(key, _u_to_b(v))


def _bg(key: bytes) -> bytes:
    return storage.get(key, b"")


def _bs(key: bytes, value: bytes) -> None:
    storage.set(key, bytes(value))


def _k_l(listing_id: int, suffix: bytes) -> bytes:
    return b"anm_mkt:l:" + _u_to_b(listing_id) + b":" + suffix


def _k_by_token(collection: bytes, token_id: int) -> bytes:
    return b"anm_mkt:by_token:" + collection + b":" + _u_to_b(token_id)


# ---------------------------------------------------------------------------
# Lifecycle guards
# ---------------------------------------------------------------------------


def _ensure_init() -> None:
    abi.require(_ug(K_INIT) == 1, b"not_initialized")


def _ensure_owner() -> None:
    abi.require(abi.caller() == _bg(K_OWNER), b"not_owner")


def _ensure_not_paused() -> None:
    abi.require(_ug(K_PAUSED) == 0, b"paused")


def _enter() -> None:
    """Coarse reentrancy guard wrapping every mutating entry-point."""
    abi.require(_ug(K_REENT) == 0, b"reentrant")
    _us(K_REENT, 1)


def _exit() -> None:
    storage.delete(K_REENT)


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


def init(owner_: bytes, fee_recipient: bytes, fee_bps: int) -> None:
    abi.require(_ug(K_INIT) == 0, b"already_initialized")
    abi.require(
        isinstance(owner_, (bytes, bytearray)) and len(owner_) > 0,
        b"bad_owner",
    )
    abi.require(
        isinstance(fee_recipient, (bytes, bytearray)) and len(fee_recipient) > 0,
        b"bad_fee_recipient",
    )
    abi.require(
        isinstance(fee_bps, int) and 0 <= fee_bps <= MAX_FEE_BPS,
        b"bad_fee_bps",
    )
    _bs(K_OWNER, owner_)
    _bs(K_FEE_RECIPIENT, fee_recipient)
    _us(K_FEE_BPS, fee_bps)
    _us(K_NEXT_ID, 1)
    _us(K_INIT, 1)


# ---------------------------------------------------------------------------
# View
# ---------------------------------------------------------------------------


def owner() -> bytes:
    return _bg(K_OWNER)


def fee_recipient() -> bytes:
    return _bg(K_FEE_RECIPIENT)


def fee_bps() -> int:
    return _ug(K_FEE_BPS)


def paused() -> bool:
    return _ug(K_PAUSED) == 1


def next_listing_id() -> int:
    return _ug(K_NEXT_ID)


def get_listing(listing_id: int) -> tuple[bytes, int, bytes, int, bytes, int]:
    """Return (collection, token_id, seller, price, state, created_at)."""
    lid = int(listing_id)
    state = _bg(_k_l(lid, b"state"))
    abi.require(len(state) > 0, b"unknown_listing")
    return (
        _bg(_k_l(lid, b"collection")),
        _ug(_k_l(lid, b"token_id")),
        _bg(_k_l(lid, b"seller")),
        _ug(_k_l(lid, b"price")),
        state,
        _ug(_k_l(lid, b"created_at")),
    )


def active_listing_for_token(collection: bytes, token_id: int) -> int:
    """Return the listing id for the currently-active listing of
    (collection, token_id), or 0 if none.
    """
    return _ug(_k_by_token(collection, int(token_id)))


# ---------------------------------------------------------------------------
# External call helpers (call into the collection contract)
# ---------------------------------------------------------------------------


def _coll_owner_of(collection: bytes, token_id: int) -> bytes:
    return abi.call(collection, b"owner_of", [int(token_id)])


def _coll_is_operator(
    collection: bytes, holder: bytes, operator: bytes
) -> bool:
    return bool(
        abi.call(collection, b"is_approved_for_all", [holder, operator])
    )


def _coll_transfer_from(
    collection: bytes, from_addr: bytes, to: bytes, token_id: int
) -> None:
    abi.call(
        collection, b"transfer_from", [from_addr, to, int(token_id)]
    )


def _coll_royalty(
    collection: bytes, token_id: int, sale_price: int
) -> tuple[bytes, int]:
    result = abi.call(
        collection, b"royalty_info", [int(token_id), int(sale_price)]
    )
    # royalty_info returns a 2-tuple from the contract; cope with both
    # tuple and list serialisations the VM might surface.
    if (
        isinstance(result, (list, tuple))
        and len(result) >= 2
        and isinstance(result[0], (bytes, bytearray))
        and isinstance(result[1], int)
    ):
        receiver = bytes(result[0])
        amount = int(result[1])
        abi.require(0 <= amount <= int(sale_price), b"royalty_too_high")
        return (receiver, amount)
    # Contracts that don't implement royalty_info fall through to no
    # royalty rather than reverting — the marketplace must still serve
    # legacy collections.
    return (b"", 0)


# ---------------------------------------------------------------------------
# list / cancel / buy
# ---------------------------------------------------------------------------


def list_nft(collection: bytes, token_id: int, price: int) -> int:
    """List an NFT for sale at `price` nanos. The marketplace must
    already be approved as an operator for the seller on `collection`.
    Returns the new listing id.

    No try/finally: any abi.require() failure inside this function
    reverts the whole tx, which also rolls back the `_enter()` storage
    write that set the lock. So the lock is naturally released on the
    failure path. On the success path we explicitly call _exit() at the
    bottom.
    """
    _ensure_init()
    _ensure_not_paused()
    _enter()

    abi.require(
        isinstance(collection, (bytes, bytearray)) and len(collection) > 0,
        b"bad_collection",
    )
    abi.require(
        isinstance(token_id, int) and token_id > 0, b"bad_token_id"
    )
    abi.require(isinstance(price, int) and price > 0, b"bad_price")

    caller = abi.caller()

    # 1. The caller must currently own the NFT.
    holder = _coll_owner_of(collection, token_id)
    abi.require(holder == caller, b"not_token_owner")

    # 2. The marketplace contract must be approved to transfer it.
    marketplace_addr = abi.self()
    abi.require(
        _coll_is_operator(collection, caller, marketplace_addr),
        b"not_approved_for_all",
    )

    # 3. At most one ACTIVE listing per (collection, token_id).
    existing = _ug(_k_by_token(collection, int(token_id)))
    abi.require(existing == 0, b"already_listed")

    lid = _ug(K_NEXT_ID)
    abi.require(lid > 0, b"bad_listing_id")
    _us(K_NEXT_ID, lid + 1)

    _bs(_k_l(lid, b"collection"), collection)
    _us(_k_l(lid, b"token_id"), int(token_id))
    _bs(_k_l(lid, b"seller"), caller)
    _us(_k_l(lid, b"price"), int(price))
    _bs(_k_l(lid, b"state"), STATE_ACTIVE)
    _us(_k_l(lid, b"created_at"), int(abi.block_timestamp()))
    _us(_k_by_token(collection, int(token_id)), lid)

    events.emit(
        b"Listed",
        {
            "listing_id": int(lid),
            "collection": collection,
            "token_id": int(token_id),
            "seller": caller,
            "price": int(price),
        },
    )
    _exit()
    return int(lid)


def cancel(listing_id: int) -> None:
    """Seller cancels their listing. NFT was never moved, so this is a
    pure storage-state update.
    """
    _ensure_init()
    _enter()

    lid = int(listing_id)
    state = _bg(_k_l(lid, b"state"))
    abi.require(state == STATE_ACTIVE, b"not_active")
    seller = _bg(_k_l(lid, b"seller"))
    abi.require(abi.caller() == seller, b"not_seller")

    _bs(_k_l(lid, b"state"), STATE_CANCELLED)
    collection = _bg(_k_l(lid, b"collection"))
    tid = _ug(_k_l(lid, b"token_id"))
    storage.delete(_k_by_token(collection, tid))

    events.emit(b"Cancelled", {"listing_id": lid, "seller": seller})
    _exit()


def buy(listing_id: int) -> None:
    """Atomic settlement of a listing. Buyer must attach exactly
    `price` ANM via the tx's value field; the VM forwards the value to
    this contract before the call, so `treasury.balance()` reflects it
    on entry.

    Fee split (computed and asserted before any external call):
      total       = price
      fee         = price * fee_bps / 10000     → fee_recipient
      royalty     = collection.royalty_info(token_id, price).amount
                                                → royalty receiver
      seller_pay  = price - fee - royalty       → seller

    All three transfers are native ANM moves via the treasury syscall;
    no token allowance is needed because the buyer attaches the ANM as
    transaction value.
    """
    _ensure_init()
    _ensure_not_paused()
    _enter()

    lid = int(listing_id)
    state = _bg(_k_l(lid, b"state"))
    abi.require(state == STATE_ACTIVE, b"not_active")

    price = _ug(_k_l(lid, b"price"))
    seller = _bg(_k_l(lid, b"seller"))
    collection = _bg(_k_l(lid, b"collection"))
    token_id = _ug(_k_l(lid, b"token_id"))

    # Buyer must have attached exactly `price` worth of ANM.
    attached = int(abi.value())
    abi.require(attached == price, b"bad_value_attached")

    # The seller must still hold the NFT and the marketplace must
    # still have operator approval — either could have been revoked
    # between listing and purchase.
    current_holder = _coll_owner_of(collection, token_id)
    abi.require(current_holder == seller, b"seller_no_longer_holder")
    abi.require(
        _coll_is_operator(collection, seller, abi.self()),
        b"approval_revoked",
    )

    buyer = abi.caller()
    abi.require(buyer != seller, b"buyer_is_seller")

    # Compute the fee split. Use deterministic floor division; any
    # tiny rounding remainder goes to the seller so the sum is
    # always exactly `price`.
    fee = (price * _ug(K_FEE_BPS)) // 10_000
    royalty_pair = _coll_royalty(collection, token_id, price)
    royalty_receiver = royalty_pair[0]
    royalty = royalty_pair[1]
    abi.require(fee + royalty <= price, b"split_overflow")
    seller_pay = price - fee - royalty

    fee_to = _bg(K_FEE_RECIPIENT)

    # Move ANM out first (we already have all of `price` in the
    # contract's treasury via the attached value).
    if fee > 0 and len(fee_to) > 0:
        treasury.transfer(fee_to, fee)
    if royalty > 0 and len(royalty_receiver) > 0:
        treasury.transfer(royalty_receiver, royalty)
    if seller_pay > 0:
        treasury.transfer(seller, seller_pay)

    # Finally transfer the NFT. If this reverts the whole tx
    # reverts and ANM moves are undone.
    _coll_transfer_from(collection, seller, buyer, token_id)

    # Mark the listing settled and clear the reverse index.
    _bs(_k_l(lid, b"state"), STATE_SOLD)
    storage.delete(_k_by_token(collection, token_id))

    events.emit(
        b"Sold",
        {
            "listing_id": lid,
            "buyer": buyer,
            "fee": int(fee),
            "royalty": int(royalty),
            "seller_proceeds": int(seller_pay),
            "royalty_receiver": royalty_receiver or b"",
        },
    )
    _exit()


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------


def set_fee_bps(new_bps: int) -> None:
    _ensure_init()
    _ensure_owner()
    abi.require(
        isinstance(new_bps, int) and 0 <= new_bps <= MAX_FEE_BPS,
        b"bad_fee_bps",
    )
    _us(K_FEE_BPS, int(new_bps))
    events.emit(b"FeeUpdated", {"new_bps": int(new_bps)})


def set_fee_recipient(new_recipient: bytes) -> None:
    _ensure_init()
    _ensure_owner()
    abi.require(
        isinstance(new_recipient, (bytes, bytearray)) and len(new_recipient) > 0,
        b"bad_fee_recipient",
    )
    _bs(K_FEE_RECIPIENT, new_recipient)


def set_paused(value: bool) -> None:
    _ensure_init()
    _ensure_owner()
    abi.require(isinstance(value, bool), b"bad_value")
    if value:
        _us(K_PAUSED, 1)
    else:
        storage.delete(K_PAUSED)
    events.emit(b"Paused", {"paused": bool(value)})


def transfer_ownership(new_owner: bytes) -> None:
    _ensure_init()
    _ensure_owner()
    abi.require(
        isinstance(new_owner, (bytes, bytearray)) and len(new_owner) > 0,
        b"bad_new_owner",
    )
    _bs(K_OWNER, new_owner)
