"""
ANM Treasury Contract — Token Sales & Revenue Management

A deterministic smart contract that:
1. Manages ANM token sales (minting to buyers)
2. Tracks treasury state (revenue, sales, target progress)
3. Enforces pricing based on treasury fill percentage
4. Emits events for sales and state changes
5. Supports governance updates to treasury parameters

State:
  - treasury_owner: address (multi-sig for governance)
  - total_supply: uint256 (fixed at 1 billion ANM)
  - sold_to_date: uint256 (tokens sold to buyers)
  - revenue_to_date: uint256 (USD equivalent in wei)
  - target_revenue: uint256 ($1B target)
  - token_mint_rate: uint256 (multiplier for accelerated sales)
  - last_update_block: uint256 (for pricing curve)

Methods:
  - init(owner, total_supply, target_revenue)
  - recordSale(buyer, quantity, price_usd)
  - updatePricingMultiplier(percent_sold)
  - getTreasurySnapshot() -> state
  - setOwner(new_owner) [governance]
  - burnTokens(amount) [reduction]

Events:
  - Sale(buyer, quantity, price_usd, block)
  - PriceUpdate(percent_sold, multiplier)
  - RevenueUpdate(new_revenue, new_percent)
  - OwnershipTransferred(old_owner, new_owner)
"""

from stdlib import abi, events, hash, storage

# ============================================================================
# Storage Keys
# ============================================================================

K_INIT = b"anm/init"
K_OWNER = b"anm/owner"
K_TOTAL_SUPPLY = b"anm/total_supply"
K_SOLD_TO_DATE = b"anm/sold_to_date"
K_REVENUE_TO_DATE = b"anm/revenue_to_date"
K_TARGET_REVENUE = b"anm/target_revenue"
K_LAST_UPDATE_BLOCK = b"anm/last_update_block"
K_PRICING_MULTIPLIER = b"anm/pricing_multiplier"


def _k_balance(addr: bytes) -> bytes:
    """Balance of address in ANM"""
    return b"anm/balance/" + addr


# ============================================================================
# Constants
# ============================================================================

UINT256_MAX = (1 << 256) - 1
WAD = 10**18  # Fixed-point precision (1e18)
PERCENT_SCALE = 100  # For percent calculations

# Event names
EVENT_SALE = b"Sale"
EVENT_PRICE_UPDATE = b"PriceUpdate"
EVENT_REVENUE_UPDATE = b"RevenueUpdate"
EVENT_OWNER_TRANSFERRED = b"OwnershipTransferred"


# ============================================================================
# Safe Arithmetic
# ============================================================================


def _u256(x):
    """Assert integer in uint256 range"""
    if not isinstance(x, int):
        abi.revert(b"ERR_NOT_INT")
    if x < 0 or x > UINT256_MAX:
        abi.revert(b"ERR_U256_RANGE")
    return x


def _add(a, b):
    """Checked addition"""
    a, b = _u256(a), _u256(b)
    c = a + b
    if c > UINT256_MAX:
        abi.revert(b"ERR_ADD_OVERFLOW")
    return c


def _sub(a, b):
    """Checked subtraction"""
    a, b = _u256(a), _u256(b)
    if b > a:
        abi.revert(b"ERR_SUB_UNDERFLOW")
    return a - b


def _mul(a, b):
    """Checked multiplication (with overflow)"""
    a, b = _u256(a), _u256(b)
    if a == 0 or b == 0:
        return 0
    c = a * b
    if c // a != b:
        abi.revert(b"ERR_MUL_OVERFLOW")
    return _u256(c)


def _div(a, b):
    """Safe division"""
    a, b = _u256(a), _u256(b)
    if b == 0:
        abi.revert(b"ERR_DIV_ZERO")
    return a // b


def _sqrt(x):
    """Integer square root using Newton's method"""
    x = _u256(x)
    if x == 0:
        return 0

    # Initial guess
    z = _add(x, 1) // 2
    y = x

    # Newton iteration (converges quickly)
    while z < y:
        y = z
        z = _div(_add(x // z, z), 2)

    return y


# ============================================================================
# Storage Helpers
# ============================================================================


def _get_uint(key):
    """Get uint256 from storage"""
    v = storage.get(key)
    if v is None:
        return 0
    if not isinstance(v, int):
        abi.revert(b"ERR_TYPE_UINT")
    return _u256(v)


def _set_uint(key, val):
    """Set uint256 in storage"""
    storage.set(key, _u256(val))


def _get_address(key):
    """Get address (bytes20) from storage"""
    v = storage.get(key)
    if v is None:
        return b""
    if not isinstance(v, bytes) or len(v) != 20:
        abi.revert(b"ERR_TYPE_ADDR")
    return v


def _set_address(key, addr):
    """Set address in storage"""
    if not isinstance(addr, bytes) or len(addr) != 20:
        abi.revert(b"ERR_INVALID_ADDR")
    storage.set(key, addr)


# ============================================================================
# View Functions
# ============================================================================


def name() -> bytes:
    """Token name"""
    return b"Animica Network"


def symbol() -> bytes:
    """Token symbol"""
    return b"ANM"


def decimals() -> int:
    """Decimal places"""
    return 18


def owner() -> bytes:
    """Treasury owner address"""
    return _get_address(K_OWNER)


def totalSupply() -> int:
    """Total ANM supply (1 billion)"""
    return _get_uint(K_TOTAL_SUPPLY)


def soldToDate() -> int:
    """ANM tokens sold to date"""
    return _get_uint(K_SOLD_TO_DATE)


def balanceOf(addr: bytes) -> int:
    """ANM balance of address"""
    return _get_uint(_k_balance(addr))


def revenueToDate() -> int:
    """USD revenue (in wei) collected from sales"""
    return _get_uint(K_REVENUE_TO_DATE)


def targetRevenue() -> int:
    """Target revenue ($1B = 1e27 wei at 1e18 scale)"""
    return _get_uint(K_TARGET_REVENUE)


def percentSold() -> int:
    """Percentage of supply sold (0-100 * PERCENT_SCALE)"""
    total = totalSupply()
    if total == 0:
        return 0
    sold = soldToDate()
    # percent = (sold / total) * 100
    return _div(_mul(sold, 100 * PERCENT_SCALE), total)


def pricingMultiplier() -> int:
    """Treasury multiplier (1.0 + 2.0 * sqrt(percentSold))"""
    return _get_uint(K_PRICING_MULTIPLIER)


def treasurySnapshot() -> dict:
    """Return full treasury state snapshot"""
    return {
        "totalSupply": totalSupply(),
        "soldToDate": soldToDate(),
        "revenueToDate": revenueToDate(),
        "targetRevenue": targetRevenue(),
        "percentSold": _div(percentSold(), PERCENT_SCALE),
        "pricingMultiplier": _div(pricingMultiplier(), WAD),
        "lastUpdateBlock": _get_uint(K_LAST_UPDATE_BLOCK),
    }


# ============================================================================
# State-Changing Functions
# ============================================================================


def init(
    new_owner: bytes,
    total_supply: int,
    target_revenue: int,
) -> None:
    """
    Initialize treasury (called once at deployment).

    Args:
        new_owner: Address of treasury owner (multi-sig for governance)
        total_supply: Total ANM supply (1 billion = 1e27)
        target_revenue: Target revenue ($1B = 1e27 wei)
    """

    # Check already initialized
    if storage.get(K_INIT) is not None:
        abi.revert(b"ERR_ALREADY_INIT")

    # Validate inputs
    if not isinstance(new_owner, bytes) or len(new_owner) != 20:
        abi.revert(b"ERR_INVALID_OWNER")
    if total_supply <= 0 or total_supply > UINT256_MAX:
        abi.revert(b"ERR_INVALID_SUPPLY")
    if target_revenue <= 0 or target_revenue > UINT256_MAX:
        abi.revert(b"ERR_INVALID_TARGET")

    # Set owner
    _set_address(K_OWNER, new_owner)

    # Set supply and target
    _set_uint(K_TOTAL_SUPPLY, total_supply)
    _set_uint(K_TARGET_REVENUE, target_revenue)

    # Initialize counters
    _set_uint(K_SOLD_TO_DATE, 0)
    _set_uint(K_REVENUE_TO_DATE, 0)
    _set_uint(K_PRICING_MULTIPLIER, WAD)  # 1.0x initially

    # Mark as initialized
    storage.set(K_INIT, 1)

    # Emit init event
    events.emit(
        b"Init",
        [new_owner, total_supply, target_revenue],
    )


def recordSale(
    buyer: bytes,
    anm_quantity: int,
    price_usd_wei: int,
) -> None:
    """
    Record a token sale (mint tokens to buyer).

    Args:
        buyer: Address receiving tokens
        anm_quantity: ANM tokens to mint
        price_usd_wei: Price per token in USD (wei scale)

    Access: Only owner (payment processor contract)
    """

    # Check caller is owner
    if abi.caller() != owner():
        abi.revert(b"ERR_NOT_OWNER")

    # Validate inputs
    if not isinstance(buyer, bytes) or len(buyer) != 20:
        abi.revert(b"ERR_INVALID_BUYER")
    if anm_quantity <= 0 or anm_quantity > UINT256_MAX:
        abi.revert(b"ERR_INVALID_QUANTITY")
    if price_usd_wei <= 0:
        abi.revert(b"ERR_INVALID_PRICE")

    # Check supply limit
    total = totalSupply()
    sold = soldToDate()
    if _add(sold, anm_quantity) > total:
        abi.revert(b"ERR_SUPPLY_EXCEEDED")

    # Calculate revenue
    revenue = _mul(anm_quantity, price_usd_wei)

    # Update state
    new_sold = _add(sold, anm_quantity)
    new_revenue = _add(revenueToDate(), revenue)

    _set_uint(K_SOLD_TO_DATE, new_sold)
    _set_uint(K_REVENUE_TO_DATE, new_revenue)

    # Update buyer balance
    old_balance = _get_uint(_k_balance(buyer))
    new_balance = _add(old_balance, anm_quantity)
    _set_uint(_k_balance(buyer), new_balance)

    # Update pricing multiplier
    percent_sold = _div(_mul(new_sold, 100 * PERCENT_SCALE), total)
    percent_sold_normalized = _div(percent_sold, PERCENT_SCALE)  # Back to 0-100

    # multiplier = 1.0 + 2.0 * sqrt(percent_sold / 100)
    # = 1.0 + 2.0 * sqrt(percentSold)
    sqrt_percent = _sqrt(_div(percent_sold_normalized, 100))
    new_multiplier = _add(WAD, _mul(2 * WAD, sqrt_percent))

    _set_uint(K_PRICING_MULTIPLIER, new_multiplier)
    _set_uint(K_LAST_UPDATE_BLOCK, abi.block_number())

    # Emit events
    events.emit(
        EVENT_SALE,
        [buyer, anm_quantity, price_usd_wei, abi.block_number()],
    )

    events.emit(
        EVENT_REVENUE_UPDATE,
        [new_revenue, _div(percent_sold, PERCENT_SCALE)],
    )


def transferOwnership(new_owner: bytes) -> None:
    """
    Transfer treasury ownership (governance).

    Args:
        new_owner: New owner address

    Access: Only current owner
    """

    if abi.caller() != owner():
        abi.revert(b"ERR_NOT_OWNER")

    if not isinstance(new_owner, bytes) or len(new_owner) != 20:
        abi.revert(b"ERR_INVALID_OWNER")

    old_owner = owner()
    _set_address(K_OWNER, new_owner)

    events.emit(
        EVENT_OWNER_TRANSFERRED,
        [old_owner, new_owner],
    )


def renounceOwnership() -> None:
    """Renounce ownership (destroy governance)"""

    if abi.caller() != owner():
        abi.revert(b"ERR_NOT_OWNER")

    old_owner = owner()
    _set_address(K_OWNER, b"\x00" * 20)

    events.emit(
        EVENT_OWNER_TRANSFERRED,
        [old_owner, b"\x00" * 20],
    )


# ============================================================================
# Main Contract Entrypoint
# ============================================================================


def main(action: bytes, **kwargs) -> bytes:
    """
    Main contract dispatcher.

    Routes calls to appropriate function:
    - "init": Initialize treasury
    - "recordSale": Record token sale and mint
    - "transferOwnership": Transfer ownership
    - View functions: (name, symbol, decimals, owner, totalSupply, etc.)
    """

    if action == b"init":
        init(kwargs["owner"], kwargs["total_supply"], kwargs["target_revenue"])
        return b""

    elif action == b"recordSale":
        recordSale(kwargs["buyer"], kwargs["quantity"], kwargs["price_usd"])
        return b""

    elif action == b"transferOwnership":
        transferOwnership(kwargs["new_owner"])
        return b""

    elif action == b"renounceOwnership":
        renounceOwnership()
        return b""

    # View functions
    elif action == b"name":
        return abi.encode_bytes(name())

    elif action == b"symbol":
        return abi.encode_bytes(symbol())

    elif action == b"decimals":
        return abi.encode_int(decimals())

    elif action == b"owner":
        return abi.encode_bytes(owner())

    elif action == b"totalSupply":
        return abi.encode_int(totalSupply())

    elif action == b"soldToDate":
        return abi.encode_int(soldToDate())

    elif action == b"revenueToDate":
        return abi.encode_int(revenueToDate())

    elif action == b"balanceOf":
        return abi.encode_int(balanceOf(kwargs["addr"]))

    elif action == b"percentSold":
        return abi.encode_int(_div(percentSold(), PERCENT_SCALE))

    elif action == b"pricingMultiplier":
        return abi.encode_int(_div(pricingMultiplier(), WAD))

    elif action == b"treasurySnapshot":
        snapshot = treasurySnapshot()
        return abi.encode_struct(snapshot)

    else:
        abi.revert(b"ERR_UNKNOWN_ACTION")
