"""{{CONTRACT_NAME}} — Input validation reference patterns.

Author: {{AUTHOR}}

Demonstrates canonical input validation helpers for VM contracts.
Import or copy the helpers into your own contract.

All checks raise ValueError (invalid input) or TypeError (wrong type).
"""

# ---------------------------------------------------------------------------
# Validation helpers — copy these into your contract
# ---------------------------------------------------------------------------

_MAX_STRING_LEN = 1024
_MAX_UINT256 = (1 << 256) - 1
_ADDRESS_LEN = 42  # "0x" + 40 hex chars (Ethereum-style)


def require(condition: bool, message: str) -> None:
    """Assert-style helper; raises ValueError on failure."""
    if not condition:
        raise ValueError(message)


def require_address(value: str, name: str = "address") -> str:
    """Validate and normalise an address string."""
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string, got {type(value).__name__}")
    v = value.strip()
    if not v:
        raise ValueError(f"{name} must not be empty")
    # Accept either full 0x-prefixed hex or bare addresses
    if v.startswith("0x") or v.startswith("0X"):
        if len(v) != _ADDRESS_LEN:
            raise ValueError(f"{name} must be {_ADDRESS_LEN} chars (0x + 40 hex), got {len(v)}")
        hex_part = v[2:]
    else:
        if len(v) < 1:
            raise ValueError(f"{name} must not be empty")
        hex_part = v
    if not all(c in "0123456789abcdefABCDEF" for c in hex_part):
        raise ValueError(f"{name} contains non-hex characters")
    return v.lower()


def require_uint(value: int, name: str = "value", *, max_val: int = _MAX_UINT256, min_val: int = 0) -> int:
    """Validate an unsigned integer within [min_val, max_val]."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be an integer, got {type(value).__name__}")
    if value < min_val:
        raise ValueError(f"{name} must be >= {min_val}, got {value}")
    if value > max_val:
        raise ValueError(f"{name} must be <= {max_val}, got {value}")
    return value


def require_string(value: str, name: str = "value", *, max_len: int = _MAX_STRING_LEN, allow_empty: bool = False) -> str:
    """Validate a string field."""
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string, got {type(value).__name__}")
    if not allow_empty and not value.strip():
        raise ValueError(f"{name} must not be empty")
    if len(value) > max_len:
        raise ValueError(f"{name} must be at most {max_len} chars, got {len(value)}")
    return value


def require_bool(value: bool, name: str = "value") -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{name} must be a bool, got {type(value).__name__}")
    return value


def require_list(value: list, name: str = "value", *, max_len: int = 1000, min_len: int = 0) -> list:
    if not isinstance(value, list):
        raise TypeError(f"{name} must be a list, got {type(value).__name__}")
    if len(value) < min_len:
        raise ValueError(f"{name} must have at least {min_len} elements")
    if len(value) > max_len:
        raise ValueError(f"{name} must have at most {max_len} elements")
    return value


# ---------------------------------------------------------------------------
# Example contract using the helpers above
# ---------------------------------------------------------------------------

STORAGE = {
    "owner": "address",
    "registry": "map(address, dict)",
}

ABI = [
    {"name": "deploy",    "type": "constructor", "inputs": [{"name": "owner", "type": "address"}]},
    {"name": "register",  "type": "function",    "inputs": [{"name": "name", "type": "string"}, {"name": "score", "type": "int"}]},
    {"name": "get_entry", "type": "function",    "inputs": [{"name": "addr", "type": "address"}], "outputs": [{"type": "dict"}], "stateMutability": "view"},
]


def deploy(ctx, owner: str) -> None:
    ctx.storage["owner"] = require_address(owner, "owner")
    ctx.storage["registry"] = {}


def register(ctx, name: str, score: int) -> None:
    validated_name = require_string(name, "name", max_len=64)
    validated_score = require_uint(score, "score", max_val=1_000_000)
    registry = ctx.storage.get("registry") or {}
    registry[ctx.caller] = {"name": validated_name, "score": validated_score}
    ctx.storage["registry"] = registry


def get_entry(ctx, addr: str) -> dict:
    addr_v = require_address(addr, "addr")
    registry = ctx.storage.get("registry") or {}
    entry = registry.get(addr_v)
    if entry is None:
        raise ValueError(f"no entry for {addr_v}")
    return entry

# CURSOR
