"""{{CONTRACT_NAME}} — Pausable contract pattern.

Author: {{AUTHOR}}

Allows the owner to pause/unpause all user-facing operations.
Use _require_not_paused() as a guard in every write method.
"""

STORAGE = {
    "owner": "address",
    "paused": "bool",
}

ABI = [
    {"name": "deploy",   "type": "constructor", "inputs": [{"name": "owner", "type": "address"}]},
    {"name": "pause",    "type": "function",    "inputs": [], "outputs": []},
    {"name": "unpause",  "type": "function",    "inputs": [], "outputs": []},
    {"name": "is_paused","type": "function",    "inputs": [], "outputs": [{"type": "bool"}], "stateMutability": "view"},
    {"name": "Paused",   "type": "event",       "inputs": [{"name": "by", "type": "address"}]},
    {"name": "Unpaused", "type": "event",       "inputs": [{"name": "by", "type": "address"}]},
]


def deploy(ctx, owner: str) -> None:
    ctx.storage["owner"] = owner
    ctx.storage["paused"] = False


def _require_owner(ctx) -> None:
    if ctx.caller != ctx.storage.get("owner"):
        raise PermissionError("Pausable: caller is not the owner")


def _require_not_paused(ctx) -> None:
    """Call this at the top of every write method you want to gate."""
    if ctx.storage.get("paused", False):
        raise RuntimeError("Pausable: contract is paused")


def pause(ctx) -> None:
    """Pause the contract (owner only)."""
    _require_owner(ctx)
    if ctx.storage.get("paused", False):
        raise RuntimeError("Pausable: already paused")
    ctx.storage["paused"] = True
    ctx.emit("Paused", {"by": ctx.caller})


def unpause(ctx) -> None:
    """Resume operations (owner only)."""
    _require_owner(ctx)
    if not ctx.storage.get("paused", False):
        raise RuntimeError("Pausable: not paused")
    ctx.storage["paused"] = False
    ctx.emit("Unpaused", {"by": ctx.caller})


def is_paused(ctx) -> bool:
    """Return True if the contract is currently paused."""
    return ctx.storage.get("paused", False)


# Example user-facing method that respects the pause guard:
def do_something(ctx) -> str:
    _require_not_paused(ctx)
    # ... your logic here
    return "ok"

# CURSOR
