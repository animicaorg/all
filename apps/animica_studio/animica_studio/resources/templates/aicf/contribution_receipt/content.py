"""{{CONTRACT_NAME}} — AICF compute contribution receipt registry.

Author: {{AUTHOR}}

Records on-chain receipts for compute contributions (AI/Quantum/Storage proofs).
The settlement authority submits receipts; contributors can query their history.
"""

STORAGE = {
    "owner": "address",
    "settler": "address",
    "receipts": "map(string, dict)",  # task_id -> receipt
    "contributor_tasks": "map(address, list)",  # contributor -> [task_id]
    "receipt_count": "int",
}

ABI = [
    {"name": "deploy",           "type": "constructor",  "inputs": [{"name": "owner", "type": "address"}, {"name": "settler", "type": "address"}]},
    {"name": "record_receipt",   "type": "function",     "inputs": [{"name": "task_id", "type": "string"}, {"name": "contributor", "type": "address"}, {"name": "proof_hash", "type": "string"}, {"name": "units", "type": "int"}, {"name": "amount", "type": "int"}]},
    {"name": "get_receipt",      "type": "function",     "inputs": [{"name": "task_id", "type": "string"}], "outputs": [{"type": "dict"}], "stateMutability": "view"},
    {"name": "contributor_total","type": "function",     "inputs": [{"name": "contributor", "type": "address"}], "outputs": [{"type": "int"}], "stateMutability": "view"},
    {"name": "set_settler",      "type": "function",     "inputs": [{"name": "settler", "type": "address"}]},
    {"name": "ReceiptRecorded",  "type": "event",        "inputs": [{"name": "task_id", "type": "string"}, {"name": "contributor", "type": "address"}, {"name": "amount", "type": "int"}]},
]


def deploy(ctx, owner: str, settler: str) -> None:
    ctx.storage["owner"] = owner
    ctx.storage["settler"] = settler
    ctx.storage["receipts"] = {}
    ctx.storage["contributor_tasks"] = {}
    ctx.storage["receipt_count"] = 0


def _require_settler(ctx) -> None:
    if ctx.caller not in (ctx.storage.get("owner"), ctx.storage.get("settler")):
        raise PermissionError("only settler or owner can record receipts")


def record_receipt(ctx, task_id: str, contributor: str, proof_hash: str, units: int, amount: int) -> None:
    _require_settler(ctx)
    if not task_id:
        raise ValueError("task_id required")
    receipts = ctx.storage.get("receipts") or {}
    if task_id in receipts:
        raise RuntimeError(f"receipt for task {task_id} already recorded")
    if units <= 0:
        raise ValueError("units must be > 0")
    if amount < 0:
        raise ValueError("amount must be >= 0")
    receipt = {
        "task_id": task_id,
        "contributor": contributor,
        "proof_hash": proof_hash,
        "units": units,
        "amount": amount,
    }
    receipts[task_id] = receipt
    ctx.storage["receipts"] = receipts
    ctx.storage["receipt_count"] = ctx.storage.get("receipt_count", 0) + 1
    # Track per-contributor
    ctasks = ctx.storage.get("contributor_tasks") or {}
    ctasks.setdefault(contributor, []).append(task_id)
    ctx.storage["contributor_tasks"] = ctasks
    ctx.emit("ReceiptRecorded", {"task_id": task_id, "contributor": contributor, "amount": amount})


def get_receipt(ctx, task_id: str) -> dict:
    receipts = ctx.storage.get("receipts") or {}
    r = receipts.get(task_id)
    if r is None:
        raise ValueError(f"no receipt for task {task_id}")
    return r


def contributor_total(ctx, contributor: str) -> int:
    """Sum of all settled amounts for a contributor."""
    ctasks = ctx.storage.get("contributor_tasks") or {}
    task_ids = ctasks.get(contributor, [])
    receipts = ctx.storage.get("receipts") or {}
    return sum(receipts.get(tid, {}).get("amount", 0) for tid in task_ids)


def set_settler(ctx, settler: str) -> None:
    if ctx.caller != ctx.storage.get("owner"):
        raise PermissionError("only owner can change settler")
    ctx.storage["settler"] = settler

# CURSOR
