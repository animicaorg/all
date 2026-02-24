"""{{CONTRACT_NAME}} — On-chain key-value store.

Author: {{AUTHOR}}

Stores arbitrary string values under string keys.
Only the owner may write; anyone may read.
"""

STORAGE = {
    "owner": "address",
    "data": "map(string, string)",
}

ABI = [
    {"name": "deploy", "type": "constructor", "inputs": [{"name": "owner", "type": "address"}]},
    {"name": "set",    "type": "function",    "inputs": [{"name": "key", "type": "string"}, {"name": "value", "type": "string"}], "outputs": []},
    {"name": "get",    "type": "function",    "inputs": [{"name": "key", "type": "string"}], "outputs": [{"type": "string"}], "stateMutability": "view"},
    {"name": "remove", "type": "function",    "inputs": [{"name": "key", "type": "string"}], "outputs": []},
    {"name": "has",    "type": "function",    "inputs": [{"name": "key", "type": "string"}], "outputs": [{"type": "bool"}], "stateMutability": "view"},
    {"name": "Set",    "type": "event",       "inputs": [{"name": "key", "type": "string"}, {"name": "value", "type": "string"}]},
    {"name": "Removed","type": "event",       "inputs": [{"name": "key", "type": "string"}]},
]


def deploy(ctx, owner: str) -> None:
    ctx.storage["owner"] = owner
    ctx.storage["data"] = {}


def _require_owner(ctx) -> None:
    if ctx.caller != ctx.storage.get("owner"):
        raise PermissionError("Only the owner can write to this store")


def set(ctx, key: str, value: str) -> None:
    """Set a key to a value (owner only)."""
    if not isinstance(key, str) or not key:
        raise ValueError("key must be a non-empty string")
    if not isinstance(value, str):
        raise ValueError("value must be a string")
    _require_owner(ctx)
    data = ctx.storage.get("data") or {}
    data[key] = value
    ctx.storage["data"] = data
    ctx.emit("Set", {"key": key, "value": value})


def get(ctx, key: str) -> str:
    """Return the value for key, or empty string if not set."""
    data = ctx.storage.get("data") or {}
    return data.get(key, "")


def remove(ctx, key: str) -> None:
    """Delete key from the store (owner only)."""
    _require_owner(ctx)
    data = ctx.storage.get("data") or {}
    data.pop(key, None)
    ctx.storage["data"] = data
    ctx.emit("Removed", {"key": key})


def has(ctx, key: str) -> bool:
    """Return True if key exists in the store."""
    data = ctx.storage.get("data") or {}
    return key in data

# CURSOR
