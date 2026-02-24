"""{{CONTRACT_NAME}} — DAO treasury with multi-approver spend control.

Author: {{AUTHOR}}
Required approvals: {{REQUIRED_APPROVALS}}

Approvers submit spend requests; once required_approvals signatures are
collected the spend is executable. Deposits are open to anyone.
"""

STORAGE = {
    "owner": "address",
    "approvers": "map(address, bool)",
    "approver_count": "int",
    "required_approvals": "int",
    "balance": "int",
    "requests": "map(int, dict)",
    "request_count": "int",
    "approvals": "map(string, bool)",  # "req_id:approver" -> approved
}

ABI = [
    {"name": "deploy",          "type": "constructor", "inputs": [{"name": "owner", "type": "address"}, {"name": "required_approvals", "type": "int"}]},
    {"name": "add_approver",    "type": "function",    "inputs": [{"name": "approver", "type": "address"}]},
    {"name": "deposit",         "type": "function",    "inputs": [{"name": "amount", "type": "int"}]},
    {"name": "request_spend",   "type": "function",    "inputs": [{"name": "recipient", "type": "address"}, {"name": "amount", "type": "int"}, {"name": "memo", "type": "string"}], "outputs": [{"type": "int"}]},
    {"name": "approve",         "type": "function",    "inputs": [{"name": "request_id", "type": "int"}]},
    {"name": "execute_spend",   "type": "function",    "inputs": [{"name": "request_id", "type": "int"}]},
    {"name": "get_balance",     "type": "function",    "inputs": [], "outputs": [{"type": "int"}], "stateMutability": "view"},
    {"name": "Deposited",       "type": "event",       "inputs": [{"name": "from", "type": "address"}, {"name": "amount", "type": "int"}]},
    {"name": "SpendRequested",  "type": "event",       "inputs": [{"name": "id", "type": "int"}, {"name": "recipient", "type": "address"}, {"name": "amount", "type": "int"}]},
    {"name": "Approved",        "type": "event",       "inputs": [{"name": "id", "type": "int"}, {"name": "by", "type": "address"}, {"name": "count", "type": "int"}]},
    {"name": "SpendExecuted",   "type": "event",       "inputs": [{"name": "id", "type": "int"}, {"name": "recipient", "type": "address"}, {"name": "amount", "type": "int"}]},
]


def deploy(ctx, owner: str, required_approvals: int) -> None:
    if required_approvals < 1:
        raise ValueError("required_approvals >= 1")
    ctx.storage["owner"] = owner
    ctx.storage["required_approvals"] = int("{{REQUIRED_APPROVALS}}" or required_approvals)
    ctx.storage["approvers"] = {}
    ctx.storage["approver_count"] = 0
    ctx.storage["balance"] = 0
    ctx.storage["requests"] = {}
    ctx.storage["request_count"] = 0
    ctx.storage["approvals"] = {}


def add_approver(ctx, approver: str) -> None:
    if ctx.caller != ctx.storage.get("owner"):
        raise PermissionError("only owner can add approvers")
    approvers = ctx.storage.get("approvers") or {}
    if not approvers.get(approver):
        approvers[approver] = True
        ctx.storage["approvers"] = approvers
        ctx.storage["approver_count"] = ctx.storage.get("approver_count", 0) + 1


def deposit(ctx, amount: int) -> None:
    if amount <= 0:
        raise ValueError("amount must be > 0")
    ctx.storage["balance"] = ctx.storage.get("balance", 0) + amount
    ctx.emit("Deposited", {"from": ctx.caller, "amount": amount})


def request_spend(ctx, recipient: str, amount: int, memo: str) -> int:
    approvers = ctx.storage.get("approvers") or {}
    if not approvers.get(ctx.caller):
        raise PermissionError("only approvers can request spends")
    if amount <= 0:
        raise ValueError("amount must be > 0")
    if amount > ctx.storage.get("balance", 0):
        raise ValueError("insufficient treasury balance")
    requests = ctx.storage.get("requests") or {}
    rid = ctx.storage.get("request_count", 0) + 1
    requests[rid] = {"id": rid, "recipient": recipient, "amount": amount, "memo": memo, "approvals": 0, "executed": False}
    ctx.storage["requests"] = requests
    ctx.storage["request_count"] = rid
    ctx.emit("SpendRequested", {"id": rid, "recipient": recipient, "amount": amount})
    return rid


def approve(ctx, request_id: int) -> None:
    approvers = ctx.storage.get("approvers") or {}
    if not approvers.get(ctx.caller):
        raise PermissionError("not an approver")
    requests = ctx.storage.get("requests") or {}
    req = requests.get(request_id)
    if req is None:
        raise ValueError(f"request {request_id} not found")
    if req.get("executed"):
        raise RuntimeError("already executed")
    key = f"{request_id}:{ctx.caller}"
    appr = ctx.storage.get("approvals") or {}
    if appr.get(key):
        raise RuntimeError("already approved")
    appr[key] = True
    req["approvals"] = req.get("approvals", 0) + 1
    ctx.storage["approvals"] = appr
    requests[request_id] = req
    ctx.storage["requests"] = requests
    ctx.emit("Approved", {"id": request_id, "by": ctx.caller, "count": req["approvals"]})


def execute_spend(ctx, request_id: int) -> None:
    approvers = ctx.storage.get("approvers") or {}
    if not approvers.get(ctx.caller):
        raise PermissionError("not an approver")
    requests = ctx.storage.get("requests") or {}
    req = requests.get(request_id)
    if req is None:
        raise ValueError(f"request {request_id} not found")
    if req.get("executed"):
        raise RuntimeError("already executed")
    needed = ctx.storage.get("required_approvals", 2)
    if req.get("approvals", 0) < needed:
        raise RuntimeError(f"need {needed} approvals, have {req.get('approvals', 0)}")
    amount = req["amount"]
    balance = ctx.storage.get("balance", 0)
    if amount > balance:
        raise RuntimeError("insufficient balance")
    req["executed"] = True
    requests[request_id] = req
    ctx.storage["requests"] = requests
    ctx.storage["balance"] = balance - amount
    ctx.emit("SpendExecuted", {"id": request_id, "recipient": req["recipient"], "amount": amount})


def get_balance(ctx) -> int:
    return ctx.storage.get("balance", 0)

# CURSOR
