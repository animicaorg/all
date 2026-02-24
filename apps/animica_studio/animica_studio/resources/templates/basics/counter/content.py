"""{{CONTRACT_NAME}} — Deterministic counter contract.

Author: {{AUTHOR}}

Provides increment, decrement, and reset operations.
All state mutations require the caller to be the owner.
"""

# ---------------------------------------------------------------------------
# Storage schema
# ---------------------------------------------------------------------------
# counter: int   — current counter value
# owner: address — contract owner (set at deploy time)

STORAGE = {
    "counter": "int",
    "owner": "address",
}

ABI = [
    {"name": "deploy",     "type": "constructor", "inputs": [{"name": "owner", "type": "address"}]},
    {"name": "increment",  "type": "function",    "inputs": [],                              "outputs": [{"type": "int"}]},
    {"name": "decrement",  "type": "function",    "inputs": [],                              "outputs": [{"type": "int"}]},
    {"name": "reset",      "type": "function",    "inputs": [],                              "outputs": []},
    {"name": "get",        "type": "function",    "inputs": [],                              "outputs": [{"type": "int"}], "stateMutability": "view"},
    {"name": "Incremented","type": "event",       "inputs": [{"name": "value", "type": "int"}]},
    {"name": "Decremented","type": "event",       "inputs": [{"name": "value", "type": "int"}]},
    {"name": "Reset",      "type": "event",       "inputs": []},
]


def deploy(ctx, owner: str) -> None:
    """Deploy: set owner and initial counter value."""
    ctx.storage["owner"] = owner
    ctx.storage["counter"] = int("{{INITIAL_VALUE}}" or "0")


def _require_owner(ctx) -> None:
    if ctx.caller != ctx.storage.get("owner"):
        raise PermissionError("Only the owner can call this method")


def increment(ctx) -> int:
    """Increment the counter by 1 and return the new value."""
    _require_owner(ctx)
    val = ctx.storage.get("counter", 0) + 1
    ctx.storage["counter"] = val
    ctx.emit("Incremented", {"value": val})
    return val


def decrement(ctx) -> int:
    """Decrement the counter by 1 and return the new value."""
    _require_owner(ctx)
    val = ctx.storage.get("counter", 0) - 1
    ctx.storage["counter"] = val
    ctx.emit("Decremented", {"value": val})
    return val


def reset(ctx) -> None:
    """Reset the counter to 0."""
    _require_owner(ctx)
    ctx.storage["counter"] = 0
    ctx.emit("Reset", {})


def get(ctx) -> int:
    """Return the current counter value (read-only)."""
    return ctx.storage.get("counter", 0)

# CURSOR
