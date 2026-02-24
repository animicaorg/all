"""{{CONTRACT_NAME}} — Ownership pattern.

Author: {{AUTHOR}}

Provides single-owner access control with ownership transfer and renounce.
Extend this contract to gate admin-only methods.
"""

STORAGE = {
    "owner": "address",
    "pending_owner": "address",
}

ABI = [
    {"name": "deploy",           "type": "constructor",  "inputs": [{"name": "owner", "type": "address"}]},
    {"name": "owner",            "type": "function",     "inputs": [], "outputs": [{"type": "address"}], "stateMutability": "view"},
    {"name": "transfer_ownership","type": "function",    "inputs": [{"name": "new_owner", "type": "address"}], "outputs": []},
    {"name": "accept_ownership", "type": "function",     "inputs": [], "outputs": []},
    {"name": "renounce_ownership","type": "function",    "inputs": [], "outputs": []},
    {"name": "OwnershipTransferred","type": "event",     "inputs": [{"name": "previous", "type": "address"}, {"name": "next", "type": "address"}]},
    {"name": "OwnershipRenounced","type": "event",       "inputs": [{"name": "previous", "type": "address"}]},
]


def deploy(ctx, owner: str) -> None:
    """Deploy with an initial owner."""
    if not owner:
        raise ValueError("owner address required")
    ctx.storage["owner"] = owner
    ctx.storage["pending_owner"] = ""


def _require_owner(ctx) -> None:
    if ctx.caller != ctx.storage.get("owner"):
        raise PermissionError("Ownable: caller is not the owner")


def owner(ctx) -> str:
    """Return the current owner address."""
    return ctx.storage.get("owner", "")


def transfer_ownership(ctx, new_owner: str) -> None:
    """Initiate ownership transfer to new_owner (two-step)."""
    _require_owner(ctx)
    if not new_owner:
        raise ValueError("new_owner address required")
    ctx.storage["pending_owner"] = new_owner


def accept_ownership(ctx) -> None:
    """Accept ownership transfer (called by pending_owner)."""
    pending = ctx.storage.get("pending_owner", "")
    if ctx.caller != pending:
        raise PermissionError("Ownable: caller is not the pending owner")
    previous = ctx.storage.get("owner", "")
    ctx.storage["owner"] = pending
    ctx.storage["pending_owner"] = ""
    ctx.emit("OwnershipTransferred", {"previous": previous, "next": pending})


def renounce_ownership(ctx) -> None:
    """Renounce ownership — contract becomes ownerless."""
    _require_owner(ctx)
    previous = ctx.storage.get("owner", "")
    ctx.storage["owner"] = ""
    ctx.storage["pending_owner"] = ""
    ctx.emit("OwnershipRenounced", {"previous": previous})

# CURSOR
