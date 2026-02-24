"""{{CONTRACT_NAME}} — Deterministic payment splitter.

Author: {{AUTHOR}}

Payees and their shares are set at deploy time.
Total shares must be > 0; each payee's release is proportional to their share.
"""

STORAGE = {
    "owner": "address",
    "payees": "list[address]",
    "shares": "map(address, int)",
    "total_shares": "int",
    "released": "map(address, int)",
    "total_received": "int",
}

ABI = [
    {"name": "deploy",       "type": "constructor", "inputs": [{"name": "owner", "type": "address"}, {"name": "payees", "type": "address[]"}, {"name": "shares", "type": "int[]"}]},
    {"name": "deposit",      "type": "function",    "inputs": [{"name": "amount", "type": "int"}]},
    {"name": "release",      "type": "function",    "inputs": [{"name": "payee", "type": "address"}], "outputs": [{"type": "int"}]},
    {"name": "pending",      "type": "function",    "inputs": [{"name": "payee", "type": "address"}], "outputs": [{"type": "int"}], "stateMutability": "view"},
    {"name": "Deposited",    "type": "event",       "inputs": [{"name": "from", "type": "address"}, {"name": "amount", "type": "int"}]},
    {"name": "Released",     "type": "event",       "inputs": [{"name": "to", "type": "address"}, {"name": "amount", "type": "int"}]},
]


def deploy(ctx, owner: str, payees: list, shares: list) -> None:
    if len(payees) != len(shares):
        raise ValueError("payees and shares length mismatch")
    if not payees:
        raise ValueError("at least one payee required")
    total = 0
    shares_map: dict = {}
    for addr, sh in zip(payees, shares):
        if sh <= 0:
            raise ValueError(f"share for {addr} must be > 0")
        if addr in shares_map:
            raise ValueError(f"duplicate payee {addr}")
        shares_map[addr] = sh
        total += sh
    ctx.storage["owner"] = owner
    ctx.storage["payees"] = list(payees)
    ctx.storage["shares"] = shares_map
    ctx.storage["total_shares"] = total
    ctx.storage["released"] = {addr: 0 for addr in payees}
    ctx.storage["total_received"] = 0


def deposit(ctx, amount: int) -> None:
    if amount <= 0:
        raise ValueError("amount must be > 0")
    ctx.storage["total_received"] = ctx.storage.get("total_received", 0) + amount
    ctx.emit("Deposited", {"from": ctx.caller, "amount": amount})


def pending(ctx, payee: str) -> int:
    """Calculate how much payee can release right now."""
    shares_map = ctx.storage.get("shares") or {}
    my_shares = shares_map.get(payee, 0)
    if not my_shares:
        return 0
    total_shares = ctx.storage.get("total_shares", 1)
    total_received = ctx.storage.get("total_received", 0)
    already_released = (ctx.storage.get("released") or {}).get(payee, 0)
    entitled = (total_received * my_shares) // total_shares
    return max(0, entitled - already_released)


def release(ctx, payee: str) -> int:
    """Release the pending amount for payee."""
    amount = pending(ctx, payee)
    if amount <= 0:
        raise RuntimeError(f"no pending payment for {payee}")
    released = ctx.storage.get("released") or {}
    released[payee] = released.get(payee, 0) + amount
    ctx.storage["released"] = released
    ctx.emit("Released", {"to": payee, "amount": amount})
    return amount

# CURSOR
