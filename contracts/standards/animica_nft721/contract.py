"""
ANM-721 — Animica non-fungible token standard.

Production-ready ERC-721-style NFT contract for the Animica Python-VM.
Used as the base contract for every marketplace-listed collection.

Storage layout (all keys are opaque byte strings; values are length-prefix-
free byte strings, with integers encoded big-endian, minimal-length):

  anm721:init                       u1   contract initialized? (0/1)
  anm721:name                       bytes  collection display name
  anm721:symbol                     bytes  short symbol (e.g. b"AFP")
  anm721:owner                      bytes  contract owner (admin address)
  anm721:base_uri                   bytes  prefix joined with token_id to form tokenURI
  anm721:max_supply                 uint   hard cap (0 = unbounded)
  anm721:total_supply               uint   current minted - burned
  anm721:next_id                    uint   next token id to mint
  anm721:minter                     bytes  authorised mint relayer (0 = owner only)
  anm721:royalty_receiver           bytes  EIP-2981-style royalty recipient
  anm721:royalty_bps                uint   royalty (basis points, 0–1000 = 0–10%)

  anm721:owner_of:{id}              bytes  current holder of token id
  anm721:bal:{addr}                 uint   balance of address
  anm721:approve:{id}               bytes  single-token approval
  anm721:opapprove:{owner}:{op}     u1     operator approval flag
  anm721:token_uri:{id}             bytes  per-token URI override (optional)

Events:
  Transfer(from, to, token_id)
  Approval(owner, approved, token_id)
  ApprovalForAll(owner, operator, approved)
  Minted(to, token_id, uri)
  Burned(from, token_id)
  BaseURIUpdated(uri)
  RoyaltyUpdated(receiver, bps)
  OwnershipTransferred(prev, new)

Authorisation model:
  - The contract `owner` can change metadata, royalty, base URI, and
    assign a `minter` relayer.
  - Mints can be made by the `owner` OR by the assigned `minter` address.
    For public-sale contracts (e.g. the Founders Pass) the `minter` is
    set to the FoundersPass contract address.
  - Transfers/approvals follow ERC-721 semantics: only the current
    owner OR an approved operator can move a token.
"""

from __future__ import annotations

from stdlib import abi, events, storage


# ---------------------------------------------------------------------------
# Storage key helpers
# ---------------------------------------------------------------------------

K_INIT = b"anm721:init"
K_NAME = b"anm721:name"
K_SYMBOL = b"anm721:symbol"
K_OWNER = b"anm721:owner"
K_BASE_URI = b"anm721:base_uri"
K_MAX_SUPPLY = b"anm721:max_supply"
K_TOTAL_SUPPLY = b"anm721:total_supply"
K_NEXT_ID = b"anm721:next_id"
K_MINTER = b"anm721:minter"
K_ROYALTY_RECEIVER = b"anm721:royalty_receiver"
K_ROYALTY_BPS = b"anm721:royalty_bps"

# Max royalty: 10% (1000 bps). Hard cap so a malicious owner can't poison
# the market by forwarding all proceeds to a royalty wallet.
MAX_ROYALTY_BPS = 1000


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


def _k_owner_of(token_id: int) -> bytes:
    return b"anm721:owner_of:" + _u_to_b(token_id)


def _k_balance(addr: bytes) -> bytes:
    return b"anm721:bal:" + addr


def _k_approval(token_id: int) -> bytes:
    return b"anm721:approve:" + _u_to_b(token_id)


def _k_op_approval(owner_addr: bytes, operator: bytes) -> bytes:
    return b"anm721:opapprove:" + owner_addr + b":" + operator


def _k_token_uri(token_id: int) -> bytes:
    return b"anm721:token_uri:" + _u_to_b(token_id)


# ---------------------------------------------------------------------------
# Storage primitives
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Authorisation helpers
# ---------------------------------------------------------------------------


def _ensure_init() -> None:
    abi.require(_ug(K_INIT) == 1, b"not_initialized")


def _ensure_owner_caller() -> None:
    abi.require(abi.caller() == _bg(K_OWNER), b"not_owner")


def _ensure_mint_authority() -> None:
    """Owner OR designated minter relayer can mint."""
    caller = abi.caller()
    owner = _bg(K_OWNER)
    minter = _bg(K_MINTER)
    abi.require(
        caller == owner or (len(minter) > 0 and caller == minter),
        b"not_mint_authority",
    )


def _ensure_token_exists(token_id: int) -> bytes:
    holder = _bg(_k_owner_of(token_id))
    abi.require(len(holder) > 0, b"nonexistent_token")
    return holder


def _is_approved_or_owner(spender: bytes, token_id: int) -> bool:
    holder = _bg(_k_owner_of(token_id))
    if len(holder) == 0:
        return False
    if spender == holder:
        return True
    if _bg(_k_approval(token_id)) == spender:
        return True
    if _ug(_k_op_approval(holder, spender)) == 1:
        return True
    return False


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


def init(
    name_: bytes,
    symbol_: bytes,
    owner_: bytes,
    base_uri: bytes,
    max_supply: int,
    royalty_receiver: bytes,
    royalty_bps: int,
) -> None:
    """One-time initialiser. Subsequent calls revert.

    max_supply=0 means uncapped. royalty_bps must be ≤ MAX_ROYALTY_BPS.
    """
    abi.require(_ug(K_INIT) == 0, b"already_initialized")
    abi.require(isinstance(name_, (bytes, bytearray)) and len(name_) > 0, b"bad_name")
    abi.require(
        isinstance(symbol_, (bytes, bytearray)) and 0 < len(symbol_) <= 32,
        b"bad_symbol",
    )
    abi.require(
        isinstance(owner_, (bytes, bytearray)) and len(owner_) > 0,
        b"bad_owner",
    )
    abi.require(isinstance(max_supply, int) and max_supply >= 0, b"bad_max_supply")
    abi.require(
        isinstance(royalty_bps, int) and 0 <= royalty_bps <= MAX_ROYALTY_BPS,
        b"bad_royalty_bps",
    )
    _bs(K_NAME, name_)
    _bs(K_SYMBOL, symbol_)
    _bs(K_OWNER, owner_)
    _bs(K_BASE_URI, base_uri or b"")
    _us(K_MAX_SUPPLY, max_supply)
    _us(K_TOTAL_SUPPLY, 0)
    _us(K_NEXT_ID, 1)    # token ids start at 1; 0 is reserved as "null"
    _bs(K_MINTER, b"")
    _bs(K_ROYALTY_RECEIVER, royalty_receiver or owner_)
    _us(K_ROYALTY_BPS, royalty_bps)
    _us(K_INIT, 1)
    events.emit(b"OwnershipTransferred", {"prev": b"", "new": owner_})


# ---------------------------------------------------------------------------
# View functions
# ---------------------------------------------------------------------------


def name() -> bytes:
    return _bg(K_NAME)


def symbol() -> bytes:
    return _bg(K_SYMBOL)


def owner() -> bytes:
    return _bg(K_OWNER)


def base_uri() -> bytes:
    return _bg(K_BASE_URI)


def max_supply() -> int:
    return _ug(K_MAX_SUPPLY)


def total_supply() -> int:
    return _ug(K_TOTAL_SUPPLY)


def totalSupply() -> int:    # camelCase alias
    return total_supply()


def next_id() -> int:
    return _ug(K_NEXT_ID)


def minter() -> bytes:
    return _bg(K_MINTER)


def royalty_info(token_id: int, sale_price: int) -> tuple[bytes, int]:
    """EIP-2981-style: (receiver, royalty_amount) for `sale_price`."""
    abi.require(isinstance(sale_price, int) and sale_price >= 0, b"bad_price")
    receiver = _bg(K_ROYALTY_RECEIVER)
    bps = _ug(K_ROYALTY_BPS)
    amount = (int(sale_price) * bps) // 10_000
    return (receiver, amount)


def royaltyInfo(token_id: int, sale_price: int) -> tuple[bytes, int]:
    return royalty_info(token_id, sale_price)


def owner_of(token_id: int) -> bytes:
    return _ensure_token_exists(token_id)


def ownerOf(token_id: int) -> bytes:
    return owner_of(token_id)


def balance_of(addr: bytes) -> int:
    abi.require(isinstance(addr, (bytes, bytearray)), b"bad_addr")
    return _ug(_k_balance(addr))


def balanceOf(addr: bytes) -> int:
    return balance_of(addr)


def get_approved(token_id: int) -> bytes:
    _ensure_token_exists(token_id)
    return _bg(_k_approval(token_id))


def getApproved(token_id: int) -> bytes:
    return get_approved(token_id)


def is_approved_for_all(owner_addr: bytes, operator: bytes) -> bool:
    return _ug(_k_op_approval(owner_addr, operator)) == 1


def isApprovedForAll(owner_addr: bytes, operator: bytes) -> bool:
    return is_approved_for_all(owner_addr, operator)


def token_uri(token_id: int) -> bytes:
    """Per-token URI override, else base_uri + decimal(id)."""
    _ensure_token_exists(token_id)
    override = _bg(_k_token_uri(token_id))
    if len(override) > 0:
        return override
    base = _bg(K_BASE_URI)
    if len(base) == 0:
        return b""
    return base + str(int(token_id)).encode("utf-8")


def tokenURI(token_id: int) -> bytes:
    return token_uri(token_id)


# ---------------------------------------------------------------------------
# Mutating: transfers / approvals
# ---------------------------------------------------------------------------


def _do_transfer(from_addr: bytes, to: bytes, token_id: int) -> None:
    abi.require(isinstance(to, (bytes, bytearray)) and len(to) > 0, b"bad_to")
    holder = _ensure_token_exists(token_id)
    abi.require(holder == from_addr, b"from_mismatch")

    # Clear single-token approval on transfer (ERC-721 spec).
    storage.delete(_k_approval(token_id))

    # Adjust balances.
    src_bal = _ug(_k_balance(from_addr))
    abi.require(src_bal >= 1, b"balance_underflow")
    _us(_k_balance(from_addr), src_bal - 1)
    _us(_k_balance(to), _ug(_k_balance(to)) + 1)

    _bs(_k_owner_of(token_id), to)
    events.emit(b"Transfer", {"from": from_addr, "to": to, "token_id": int(token_id)})


def transfer_from(from_addr: bytes, to: bytes, token_id: int) -> bool:
    _ensure_init()
    abi.require(
        _is_approved_or_owner(abi.caller(), int(token_id)),
        b"caller_not_approved",
    )
    _do_transfer(from_addr, to, int(token_id))
    return True


def transferFrom(from_addr: bytes, to: bytes, token_id: int) -> bool:
    return transfer_from(from_addr, to, token_id)


def safe_transfer_from(from_addr: bytes, to: bytes, token_id: int) -> bool:
    # Same as transfer_from in the VM — there's no contract-recipient hook
    # to invoke on Animica's Python-VM (no IERC721Receiver equivalent),
    # so "safe" semantics reduce to the standard transfer. Kept for ABI
    # compatibility with tooling that expects this name.
    return transfer_from(from_addr, to, token_id)


def safeTransferFrom(from_addr: bytes, to: bytes, token_id: int) -> bool:
    return safe_transfer_from(from_addr, to, token_id)


def approve(approved: bytes, token_id: int) -> bool:
    _ensure_init()
    holder = _ensure_token_exists(int(token_id))
    caller = abi.caller()
    # Caller must be owner OR an authorised operator.
    abi.require(
        caller == holder or _ug(_k_op_approval(holder, caller)) == 1,
        b"not_authorised",
    )
    _bs(_k_approval(int(token_id)), approved or b"")
    events.emit(
        b"Approval",
        {"owner": holder, "approved": approved or b"", "token_id": int(token_id)},
    )
    return True


def set_approval_for_all(operator: bytes, approved: bool) -> bool:
    _ensure_init()
    caller = abi.caller()
    abi.require(operator != caller, b"self_approval")
    abi.require(isinstance(approved, bool), b"bad_approved")
    key = _k_op_approval(caller, operator)
    if approved:
        _us(key, 1)
    else:
        storage.delete(key)
    events.emit(
        b"ApprovalForAll",
        {"owner": caller, "operator": operator, "approved": bool(approved)},
    )
    return True


def setApprovalForAll(operator: bytes, approved: bool) -> bool:
    return set_approval_for_all(operator, approved)


# ---------------------------------------------------------------------------
# Mutating: mint / burn
# ---------------------------------------------------------------------------


def mint(to: bytes, uri: bytes) -> int:
    """Mint the next token to `to`, with an optional per-token uri override.

    Returns the newly minted token id. Auth: owner OR designated minter.
    """
    _ensure_init()
    _ensure_mint_authority()
    abi.require(isinstance(to, (bytes, bytearray)) and len(to) > 0, b"bad_to")

    total = _ug(K_TOTAL_SUPPLY)
    cap = _ug(K_MAX_SUPPLY)
    if cap > 0:
        abi.require(total + 1 <= cap, b"max_supply_exceeded")

    token_id = _ug(K_NEXT_ID)
    abi.require(token_id > 0, b"bad_next_id")

    _bs(_k_owner_of(token_id), to)
    _us(_k_balance(to), _ug(_k_balance(to)) + 1)
    _us(K_TOTAL_SUPPLY, total + 1)
    _us(K_NEXT_ID, token_id + 1)
    if uri is not None and len(uri) > 0:
        _bs(_k_token_uri(token_id), uri)

    events.emit(
        b"Minted",
        {"to": to, "token_id": int(token_id), "uri": uri or b""},
    )
    events.emit(b"Transfer", {"from": b"", "to": to, "token_id": int(token_id)})
    return int(token_id)


def burn(token_id: int) -> bool:
    """Permanently destroy a token; caller must be owner or approved."""
    _ensure_init()
    tid = int(token_id)
    holder = _ensure_token_exists(tid)
    abi.require(_is_approved_or_owner(abi.caller(), tid), b"caller_not_approved")

    # Clear approval and ownership.
    storage.delete(_k_approval(tid))
    storage.delete(_k_owner_of(tid))
    storage.delete(_k_token_uri(tid))
    bal = _ug(_k_balance(holder))
    abi.require(bal >= 1, b"balance_underflow")
    _us(_k_balance(holder), bal - 1)
    total = _ug(K_TOTAL_SUPPLY)
    abi.require(total >= 1, b"supply_underflow")
    _us(K_TOTAL_SUPPLY, total - 1)

    events.emit(b"Burned", {"from": holder, "token_id": tid})
    events.emit(b"Transfer", {"from": holder, "to": b"", "token_id": tid})
    return True


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------


def set_base_uri(new_uri: bytes) -> None:
    _ensure_init()
    _ensure_owner_caller()
    _bs(K_BASE_URI, new_uri or b"")
    events.emit(b"BaseURIUpdated", {"uri": new_uri or b""})


def setBaseUri(new_uri: bytes) -> None:
    set_base_uri(new_uri)


def set_token_uri(token_id: int, new_uri: bytes) -> None:
    _ensure_init()
    _ensure_owner_caller()
    _ensure_token_exists(int(token_id))
    if new_uri is None or len(new_uri) == 0:
        storage.delete(_k_token_uri(int(token_id)))
    else:
        _bs(_k_token_uri(int(token_id)), new_uri)


def set_minter(new_minter: bytes) -> None:
    _ensure_init()
    _ensure_owner_caller()
    _bs(K_MINTER, new_minter or b"")


def set_royalty(receiver: bytes, bps: int) -> None:
    _ensure_init()
    _ensure_owner_caller()
    abi.require(
        isinstance(bps, int) and 0 <= bps <= MAX_ROYALTY_BPS,
        b"bad_royalty_bps",
    )
    _bs(K_ROYALTY_RECEIVER, receiver or _bg(K_OWNER))
    _us(K_ROYALTY_BPS, bps)
    events.emit(b"RoyaltyUpdated", {"receiver": receiver or b"", "bps": int(bps)})


def transfer_ownership(new_owner: bytes) -> None:
    _ensure_init()
    _ensure_owner_caller()
    abi.require(
        isinstance(new_owner, (bytes, bytearray)) and len(new_owner) > 0,
        b"bad_new_owner",
    )
    prev = _bg(K_OWNER)
    _bs(K_OWNER, new_owner)
    events.emit(b"OwnershipTransferred", {"prev": prev, "new": new_owner})


def transferOwnership(new_owner: bytes) -> None:
    transfer_ownership(new_owner)
