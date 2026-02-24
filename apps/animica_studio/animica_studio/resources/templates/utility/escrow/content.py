"""{{CONTRACT_NAME}} — Two-party escrow with arbitrator.

Author: {{AUTHOR}}

Workflow:
1. deployer (buyer) deploys with seller + arbitrator addresses
2. buyer calls deposit(amount) to lock funds
3. buyer calls confirm_receipt() OR arbitrator calls resolve(winner)
4. funds released to seller on confirm; refunded to buyer on refund
"""

STORAGE = {
    "buyer": "address",
    "seller": "address",
    "arbitrator": "address",
    "amount": "int",
    "state": "string",  # "open" | "funded" | "released" | "refunded" | "disputed"
}

ABI = [
    {"name": "deploy",          "type": "constructor",  "inputs": [{"name": "seller", "type": "address"}, {"name": "arbitrator", "type": "address"}]},
    {"name": "deposit",         "type": "function",     "inputs": [{"name": "amount", "type": "int"}]},
    {"name": "confirm_receipt", "type": "function",     "inputs": []},
    {"name": "refund",          "type": "function",     "inputs": []},
    {"name": "dispute",         "type": "function",     "inputs": []},
    {"name": "resolve",         "type": "function",     "inputs": [{"name": "winner", "type": "string"}]},
    {"name": "get_state",       "type": "function",     "inputs": [], "outputs": [{"type": "dict"}], "stateMutability": "view"},
    {"name": "Funded",          "type": "event",        "inputs": [{"name": "amount", "type": "int"}]},
    {"name": "Released",        "type": "event",        "inputs": [{"name": "to", "type": "address"}, {"name": "amount", "type": "int"}]},
    {"name": "Refunded",        "type": "event",        "inputs": [{"name": "to", "type": "address"}, {"name": "amount", "type": "int"}]},
    {"name": "Disputed",        "type": "event",        "inputs": [{"name": "by", "type": "address"}]},
    {"name": "Resolved",        "type": "event",        "inputs": [{"name": "winner", "type": "address"}, {"name": "amount", "type": "int"}]},
]


def deploy(ctx, seller: str, arbitrator: str) -> None:
    if not seller or not arbitrator:
        raise ValueError("seller and arbitrator addresses required")
    ctx.storage["buyer"] = ctx.caller
    ctx.storage["seller"] = seller
    ctx.storage["arbitrator"] = arbitrator
    ctx.storage["amount"] = 0
    ctx.storage["state"] = "open"


def deposit(ctx, amount: int) -> None:
    if ctx.caller != ctx.storage.get("buyer"):
        raise PermissionError("only buyer can deposit")
    if ctx.storage.get("state") != "open":
        raise RuntimeError("escrow not in open state")
    if amount <= 0:
        raise ValueError("amount must be > 0")
    ctx.storage["amount"] = amount
    ctx.storage["state"] = "funded"
    ctx.emit("Funded", {"amount": amount})


def confirm_receipt(ctx) -> None:
    if ctx.caller != ctx.storage.get("buyer"):
        raise PermissionError("only buyer can confirm receipt")
    if ctx.storage.get("state") != "funded":
        raise RuntimeError("escrow not funded")
    amount = ctx.storage["amount"]
    seller = ctx.storage["seller"]
    ctx.storage["state"] = "released"
    ctx.emit("Released", {"to": seller, "amount": amount})


def refund(ctx) -> None:
    if ctx.caller not in (ctx.storage.get("buyer"), ctx.storage.get("arbitrator")):
        raise PermissionError("only buyer or arbitrator can refund")
    if ctx.storage.get("state") not in ("funded", "disputed"):
        raise RuntimeError("cannot refund in current state")
    amount = ctx.storage["amount"]
    buyer = ctx.storage["buyer"]
    ctx.storage["state"] = "refunded"
    ctx.emit("Refunded", {"to": buyer, "amount": amount})


def dispute(ctx) -> None:
    if ctx.caller != ctx.storage.get("buyer"):
        raise PermissionError("only buyer can raise a dispute")
    if ctx.storage.get("state") != "funded":
        raise RuntimeError("escrow not funded")
    ctx.storage["state"] = "disputed"
    ctx.emit("Disputed", {"by": ctx.caller})


def resolve(ctx, winner: str) -> None:
    if ctx.caller != ctx.storage.get("arbitrator"):
        raise PermissionError("only arbitrator can resolve")
    if ctx.storage.get("state") != "disputed":
        raise RuntimeError("escrow not disputed")
    buyer = ctx.storage.get("buyer")
    seller = ctx.storage.get("seller")
    if winner not in ("buyer", "seller"):
        raise ValueError("winner must be 'buyer' or 'seller'")
    amount = ctx.storage["amount"]
    recipient = buyer if winner == "buyer" else seller
    ctx.storage["state"] = "refunded" if winner == "buyer" else "released"
    ctx.emit("Resolved", {"winner": recipient, "amount": amount})


def get_state(ctx) -> dict:
    return {
        "buyer": ctx.storage.get("buyer"),
        "seller": ctx.storage.get("seller"),
        "arbitrator": ctx.storage.get("arbitrator"),
        "amount": ctx.storage.get("amount", 0),
        "state": ctx.storage.get("state", "open"),
    }

# CURSOR
