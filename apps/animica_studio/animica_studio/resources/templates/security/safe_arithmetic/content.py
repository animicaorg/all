"""{{CONTRACT_NAME}} — Safe arithmetic helpers for VM contracts.

Author: {{AUTHOR}}

Python integers are arbitrary precision, but the Animica VM serialises storage
values as fixed-width integers.  Use these helpers to detect range violations
before storing values or passing them in events.

Copy the SafeMath class or individual functions into your contract.
"""

# ---------------------------------------------------------------------------
# Integer bounds (match VM/ABI type widths)
# ---------------------------------------------------------------------------

UINT8_MAX  = (1 << 8) - 1
UINT16_MAX = (1 << 16) - 1
UINT32_MAX = (1 << 32) - 1
UINT64_MAX = (1 << 64) - 1
UINT128_MAX = (1 << 128) - 1
UINT256_MAX = (1 << 256) - 1

INT64_MIN = -(1 << 63)
INT64_MAX =  (1 << 63) - 1

# One unit of fixed-point.  Adjust FIXED_POINT_SCALE to change precision:
#   1_000_000  → 6 decimal places  (e.g. 1.500000 stored as 1_500_000)
#   1_000      → 3 decimal places
#   10**18     → 18 decimal places (wei-style)
FIXED_POINT_SCALE = 1_000_000


# ---------------------------------------------------------------------------
# Safe integer operations
# ---------------------------------------------------------------------------

def safe_add(a: int, b: int, *, max_val: int = UINT256_MAX) -> int:
    """Add a + b with overflow check."""
    result = a + b
    if result > max_val:
        raise OverflowError(f"safe_add overflow: {a} + {b} = {result} > {max_val}")
    return result


def safe_sub(a: int, b: int, *, min_val: int = 0) -> int:
    """Subtract a - b with underflow check."""
    result = a - b
    if result < min_val:
        raise ArithmeticError(f"safe_sub underflow: {a} - {b} = {result} < {min_val}")
    return result


def safe_mul(a: int, b: int, *, max_val: int = UINT256_MAX) -> int:
    """Multiply a * b with overflow check."""
    if a == 0 or b == 0:
        return 0
    result = a * b
    if result > max_val:
        raise OverflowError(f"safe_mul overflow: {a} * {b} > {max_val}")
    return result


def safe_div(a: int, b: int) -> int:
    """Integer division with zero-divisor guard."""
    if b == 0:
        raise ZeroDivisionError("safe_div: divisor is zero")
    return a // b


def safe_mod(a: int, b: int) -> int:
    """Modulo with zero-divisor guard."""
    if b == 0:
        raise ZeroDivisionError("safe_mod: divisor is zero")
    return a % b


def clamp(value: int, lo: int, hi: int) -> int:
    """Clamp value into [lo, hi]."""
    if lo > hi:
        raise ValueError(f"clamp: lo ({lo}) > hi ({hi})")
    return max(lo, min(hi, value))


def to_uint(value: int, bits: int) -> int:
    """Cast value to an unsigned integer of `bits` width, raising on overflow."""
    max_v = (1 << bits) - 1
    if value < 0 or value > max_v:
        raise OverflowError(f"to_uint{bits}: value {value} out of range [0, {max_v}]")
    return value


# ---------------------------------------------------------------------------
# Fixed-point helpers (scale = FIXED_POINT_SCALE)
# ---------------------------------------------------------------------------

def fp_mul(a: int, b: int, scale: int = FIXED_POINT_SCALE) -> int:
    """Multiply two fixed-point values (result stays in fixed-point)."""
    return safe_div(safe_mul(a, b), scale)


def fp_div(a: int, b: int, scale: int = FIXED_POINT_SCALE) -> int:
    """Divide two fixed-point values (result stays in fixed-point)."""
    return safe_div(safe_mul(a, scale), b)


def fp_from_int(n: int, scale: int = FIXED_POINT_SCALE) -> int:
    """Convert integer n to fixed-point."""
    return safe_mul(n, scale)


def fp_to_int(fp: int, scale: int = FIXED_POINT_SCALE) -> int:
    """Truncate fixed-point to integer (floor)."""
    return safe_div(fp, scale)


# ---------------------------------------------------------------------------
# Example contract using safe arithmetic
# ---------------------------------------------------------------------------

STORAGE = {
    "owner": "address",
    "balance": "int",
    "total_supply": "int",
}

ABI = [
    {"name": "deploy",    "type": "constructor",  "inputs": [{"name": "owner", "type": "address"}, {"name": "supply", "type": "int"}]},
    {"name": "transfer",  "type": "function",     "inputs": [{"name": "to", "type": "address"}, {"name": "amount", "type": "int"}]},
    {"name": "get_balance","type":"function",      "inputs": [], "outputs": [{"type": "int"}], "stateMutability": "view"},
]


def deploy(ctx, owner: str, supply: int) -> None:
    supply = to_uint(supply, 128)  # fits in uint128
    ctx.storage["owner"] = owner
    ctx.storage["balance"] = supply
    ctx.storage["total_supply"] = supply


def transfer(ctx, to: str, amount: int) -> None:
    amount = to_uint(amount, 128)
    balance = ctx.storage.get("balance", 0)
    new_balance = safe_sub(balance, amount)  # raises on underflow
    ctx.storage["balance"] = new_balance


def get_balance(ctx) -> int:
    return ctx.storage.get("balance", 0)

# CURSOR
