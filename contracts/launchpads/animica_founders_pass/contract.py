"""
Animica Founders Pass — the first launch drop on the Animica NFT
marketplace.

Design constraints:

  - Fixed supply: 1000 passes.
  - Fixed price: 25_000 ANM (= 25_000 * 10^9 nanos).
  - Payment in native ANM, attached as transaction value to `mint()`.
  - One pass per wallet (anti-bot, anti-flip).
  - Whitelist phase precedes public sale.
  - All proceeds go to the configured treasury address.

This contract is the *minter* on an underlying ANM-721 collection. The
collection itself is a regular `animica_nft721` deployment, owned by
the Animica admin, with `set_minter(founders_pass_addr)` called once
at deploy time so the launchpad can hand out tokens without
transferring contract ownership.

Storage layout
--------------
  fp:init                  u1   initialised
  fp:owner                 bytes  admin (can flip phases, withdraw)
  fp:collection            bytes  underlying ANM-721 contract address
  fp:treasury              bytes  receives ANM proceeds
  fp:price                 uint   nanos per pass
  fp:supply_cap            uint   total passes ever (1000 by default)
  fp:minted_total          uint   current mint count
  fp:phase                 bytes  PREVIEW | WHITELIST | PUBLIC | SOLD_OUT | PAUSED
  fp:wl:{addr}             u1     whitelisted? (1 = yes)
  fp:minted_by:{addr}      u1     has this wallet already minted?

  fp:_reent                u1     reentrancy lock

Events
------
  PhaseChanged(prev, new)
  Whitelisted(addr, value)
  PassMinted(buyer, token_id, price_paid)
  TreasuryWithdraw(to, amount)
"""

from __future__ import annotations

from stdlib import abi, events, storage, treasury


# Phases
PHASE_PREVIEW = b"PREVIEW"
PHASE_WHITELIST = b"WHITELIST"
PHASE_PUBLIC = b"PUBLIC"
PHASE_SOLD_OUT = b"SOLD_OUT"
PHASE_PAUSED = b"PAUSED"

K_INIT = b"fp:init"
K_OWNER = b"fp:owner"
K_COLLECTION = b"fp:collection"
K_TREASURY = b"fp:treasury"
K_PRICE = b"fp:price"
K_SUPPLY_CAP = b"fp:supply_cap"
K_MINTED = b"fp:minted_total"
K_PHASE = b"fp:phase"
K_REENT = b"fp:_reent"


def _u_to_b(v: int) -> bytes:
    v = int(v)
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


def _us(key: bytes, v: int) -> None:
    v = int(v)
    abi.require(v >= 0, b"negative")
    if v == 0:
        storage.delete(key)
        return
    storage.set(key, _u_to_b(v))


def _bg(key: bytes) -> bytes:
    return storage.get(key, b"")


def _bs(key: bytes, v: bytes) -> None:
    storage.set(key, bytes(v))


def _k_wl(addr: bytes) -> bytes:
    return b"fp:wl:" + addr


def _k_minted_by(addr: bytes) -> bytes:
    return b"fp:minted_by:" + addr


def _ensure_init() -> None:
    abi.require(_ug(K_INIT) == 1, b"not_initialized")


def _ensure_owner() -> None:
    abi.require(abi.caller() == _bg(K_OWNER), b"not_owner")


def _enter() -> None:
    abi.require(_ug(K_REENT) == 0, b"reentrant")
    _us(K_REENT, 1)


def _exit() -> None:
    storage.delete(K_REENT)


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


def init(
    owner_: bytes,
    collection: bytes,
    treasury_addr: bytes,
    price_nanos: int,
    supply_cap: int,
) -> None:
    abi.require(_ug(K_INIT) == 0, b"already_initialized")
    abi.require(
        isinstance(owner_, (bytes, bytearray)) and len(owner_) > 0,
        b"bad_owner",
    )
    abi.require(
        isinstance(collection, (bytes, bytearray)) and len(collection) > 0,
        b"bad_collection",
    )
    abi.require(
        isinstance(treasury_addr, (bytes, bytearray)) and len(treasury_addr) > 0,
        b"bad_treasury",
    )
    abi.require(
        isinstance(price_nanos, int) and price_nanos > 0,
        b"bad_price",
    )
    abi.require(
        isinstance(supply_cap, int) and 0 < supply_cap <= 1_000_000,
        b"bad_supply_cap",
    )
    _bs(K_OWNER, owner_)
    _bs(K_COLLECTION, collection)
    _bs(K_TREASURY, treasury_addr)
    _us(K_PRICE, price_nanos)
    _us(K_SUPPLY_CAP, supply_cap)
    _us(K_MINTED, 0)
    _bs(K_PHASE, PHASE_PREVIEW)
    _us(K_INIT, 1)
    events.emit(b"PhaseChanged", {"prev": b"", "new": PHASE_PREVIEW})


# ---------------------------------------------------------------------------
# View
# ---------------------------------------------------------------------------


def owner() -> bytes:
    return _bg(K_OWNER)


def collection() -> bytes:
    return _bg(K_COLLECTION)


def treasury_address() -> bytes:
    return _bg(K_TREASURY)


def price() -> int:
    return _ug(K_PRICE)


def supply_cap() -> int:
    return _ug(K_SUPPLY_CAP)


def minted_total() -> int:
    return _ug(K_MINTED)


def remaining() -> int:
    cap = _ug(K_SUPPLY_CAP)
    minted = _ug(K_MINTED)
    return cap - minted if cap >= minted else 0


def phase() -> bytes:
    return _bg(K_PHASE)


def is_whitelisted(addr: bytes) -> bool:
    return _ug(_k_wl(addr)) == 1


def has_minted(addr: bytes) -> bool:
    return _ug(_k_minted_by(addr)) == 1


# ---------------------------------------------------------------------------
# Mutating: public mint flow
# ---------------------------------------------------------------------------


def mint() -> int:
    """Mint exactly one Founders Pass to the caller. Caller must attach
    exactly `price()` ANM. Returns the minted token id.

    No try/finally: an abi.require() failure reverts the entire tx,
    which also rolls back the `_enter()` write. The success path
    explicitly calls `_exit()` at the bottom.
    """
    _ensure_init()
    _enter()

    ph = _bg(K_PHASE)
    abi.require(
        ph == PHASE_WHITELIST or ph == PHASE_PUBLIC,
        b"sale_not_open",
    )

    caller = abi.caller()
    abi.require(
        _ug(_k_minted_by(caller)) == 0,
        b"already_minted",
    )

    if ph == PHASE_WHITELIST:
        abi.require(_ug(_k_wl(caller)) == 1, b"not_whitelisted")

    minted = _ug(K_MINTED)
    cap = _ug(K_SUPPLY_CAP)
    abi.require(minted < cap, b"sold_out")

    p = _ug(K_PRICE)
    attached = int(abi.value())
    abi.require(attached == p, b"bad_value_attached")

    # Mark first so a re-entry can't double-mint to the same wallet.
    _us(_k_minted_by(caller), 1)
    _us(K_MINTED, minted + 1)

    # Forward proceeds to treasury (any failure reverts the whole tx).
    treasury_addr = _bg(K_TREASURY)
    treasury.transfer(treasury_addr, p)

    # Mint via the underlying ANM-721 collection. This contract must
    # have been registered as the collection's minter via
    # collection.set_minter(this_addr).
    coll = _bg(K_COLLECTION)
    new_id = abi.call(coll, b"mint", [caller, b""])
    token_id = int(new_id)

    # Flip to SOLD_OUT when the final pass is minted.
    if minted + 1 == cap:
        _bs(K_PHASE, PHASE_SOLD_OUT)
        events.emit(
            b"PhaseChanged",
            {"prev": ph, "new": PHASE_SOLD_OUT},
        )

    events.emit(
        b"PassMinted",
        {"buyer": caller, "token_id": token_id, "price_paid": int(p)},
    )
    _exit()
    return token_id


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------


def set_phase(new_phase: bytes) -> None:
    _ensure_init()
    _ensure_owner()
    abi.require(
        new_phase
        in (
            PHASE_PREVIEW,
            PHASE_WHITELIST,
            PHASE_PUBLIC,
            PHASE_SOLD_OUT,
            PHASE_PAUSED,
        ),
        b"bad_phase",
    )
    prev = _bg(K_PHASE)
    _bs(K_PHASE, new_phase)
    events.emit(b"PhaseChanged", {"prev": prev, "new": new_phase})


def add_whitelist(addrs: list) -> int:
    """Add a batch of addresses to the whitelist. Returns count added.

    Uses an indexed while loop because the Animica VM compiler does not
    yet support `for` statements; the public API stays identical (caller
    passes a list).
    """
    _ensure_init()
    _ensure_owner()
    n = len(addrs)
    abi.require(0 < n <= 500, b"bad_batch")
    added = 0
    i = 0
    while i < n:
        a = addrs[i]
        abi.require(
            isinstance(a, (bytes, bytearray)) and len(a) > 0,
            b"bad_addr",
        )
        if _ug(_k_wl(a)) == 0:
            _us(_k_wl(a), 1)
            events.emit(b"Whitelisted", {"addr": a, "value": True})
            added = added + 1
        i = i + 1
    return added


def remove_whitelist(addrs: list) -> int:
    _ensure_init()
    _ensure_owner()
    n = len(addrs)
    abi.require(0 < n <= 500, b"bad_batch")
    removed = 0
    i = 0
    while i < n:
        a = addrs[i]
        abi.require(
            isinstance(a, (bytes, bytearray)) and len(a) > 0,
            b"bad_addr",
        )
        if _ug(_k_wl(a)) == 1:
            storage.delete(_k_wl(a))
            events.emit(b"Whitelisted", {"addr": a, "value": False})
            removed = removed + 1
        i = i + 1
    return removed


def set_treasury(new_treasury: bytes) -> None:
    _ensure_init()
    _ensure_owner()
    abi.require(
        isinstance(new_treasury, (bytes, bytearray)) and len(new_treasury) > 0,
        b"bad_treasury",
    )
    _bs(K_TREASURY, new_treasury)


def set_price(new_price: int) -> None:
    """Owner-only — used only during PREVIEW to correct the launch price.
    Reverts once any pass has been minted, so post-launch buyers know
    the price they signed cannot move under them.
    """
    _ensure_init()
    _ensure_owner()
    abi.require(_ug(K_MINTED) == 0, b"sale_started")
    abi.require(isinstance(new_price, int) and new_price > 0, b"bad_price")
    _us(K_PRICE, new_price)


def transfer_ownership(new_owner: bytes) -> None:
    _ensure_init()
    _ensure_owner()
    abi.require(
        isinstance(new_owner, (bytes, bytearray)) and len(new_owner) > 0,
        b"bad_new_owner",
    )
    _bs(K_OWNER, new_owner)
