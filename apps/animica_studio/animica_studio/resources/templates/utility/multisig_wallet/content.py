"""{{CONTRACT_NAME}} — M-of-N multi-signature wallet skeleton.

Author: {{AUTHOR}}
Required signatures: {{REQUIRED_SIGS}}

Owners submit transaction proposals; after M approvals the tx can be executed.
"""

STORAGE = {
    "owners": "map(address, bool)",
    "owner_count": "int",
    "required": "int",
    "transactions": "map(int, dict)",
    "tx_count": "int",
    "confirmations": "map(string, bool)",  # "tx_id:owner" -> confirmed
}

ABI = [
    {"name": "deploy",         "type": "constructor", "inputs": [{"name": "owners", "type": "address[]"}, {"name": "required", "type": "int"}]},
    {"name": "submit_tx",      "type": "function",    "inputs": [{"name": "to", "type": "address"}, {"name": "value", "type": "int"}, {"name": "data", "type": "string"}], "outputs": [{"type": "int"}]},
    {"name": "confirm_tx",     "type": "function",    "inputs": [{"name": "tx_id", "type": "int"}]},
    {"name": "revoke_confirm", "type": "function",    "inputs": [{"name": "tx_id", "type": "int"}]},
    {"name": "execute_tx",     "type": "function",    "inputs": [{"name": "tx_id", "type": "int"}]},
    {"name": "get_tx",         "type": "function",    "inputs": [{"name": "tx_id", "type": "int"}], "outputs": [{"type": "dict"}], "stateMutability": "view"},
    {"name": "Submitted",      "type": "event",       "inputs": [{"name": "id", "type": "int"}, {"name": "to", "type": "address"}, {"name": "value", "type": "int"}]},
    {"name": "Confirmed",      "type": "event",       "inputs": [{"name": "id", "type": "int"}, {"name": "by", "type": "address"}]},
    {"name": "Revoked",        "type": "event",       "inputs": [{"name": "id", "type": "int"}, {"name": "by", "type": "address"}]},
    {"name": "Executed",       "type": "event",       "inputs": [{"name": "id", "type": "int"}]},
]


def deploy(ctx, owners: list, required: int) -> None:
    if not owners:
        raise ValueError("at least one owner required")
    required = int("{{REQUIRED_SIGS}}" or required)
    if required < 1 or required > len(owners):
        raise ValueError(f"required must be 1..{len(owners)}")
    owners_map: dict = {}
    for o in owners:
        if o in owners_map:
            raise ValueError(f"duplicate owner {o}")
        owners_map[o] = True
    ctx.storage["owners"] = owners_map
    ctx.storage["owner_count"] = len(owners)
    ctx.storage["required"] = required
    ctx.storage["transactions"] = {}
    ctx.storage["tx_count"] = 0
    ctx.storage["confirmations"] = {}


def _require_owner(ctx) -> None:
    owners = ctx.storage.get("owners") or {}
    if not owners.get(ctx.caller):
        raise PermissionError("not an owner")


def submit_tx(ctx, to: str, value: int, data: str) -> int:
    _require_owner(ctx)
    txs = ctx.storage.get("transactions") or {}
    tid = ctx.storage.get("tx_count", 0) + 1
    txs[tid] = {"id": tid, "to": to, "value": value, "data": data, "confirmations": 0, "executed": False}
    ctx.storage["transactions"] = txs
    ctx.storage["tx_count"] = tid
    ctx.emit("Submitted", {"id": tid, "to": to, "value": value})
    return tid


def confirm_tx(ctx, tx_id: int) -> None:
    _require_owner(ctx)
    txs = ctx.storage.get("transactions") or {}
    tx = txs.get(tx_id)
    if tx is None:
        raise ValueError(f"tx {tx_id} not found")
    if tx.get("executed"):
        raise RuntimeError("already executed")
    key = f"{tx_id}:{ctx.caller}"
    confs = ctx.storage.get("confirmations") or {}
    if confs.get(key):
        raise RuntimeError("already confirmed")
    confs[key] = True
    tx["confirmations"] = tx.get("confirmations", 0) + 1
    txs[tx_id] = tx
    ctx.storage["confirmations"] = confs
    ctx.storage["transactions"] = txs
    ctx.emit("Confirmed", {"id": tx_id, "by": ctx.caller})


def revoke_confirm(ctx, tx_id: int) -> None:
    _require_owner(ctx)
    key = f"{tx_id}:{ctx.caller}"
    confs = ctx.storage.get("confirmations") or {}
    if not confs.get(key):
        raise RuntimeError("no confirmation to revoke")
    txs = ctx.storage.get("transactions") or {}
    tx = txs.get(tx_id)
    if tx and tx.get("executed"):
        raise RuntimeError("already executed")
    confs[key] = False
    if tx:
        tx["confirmations"] = max(0, tx.get("confirmations", 1) - 1)
        txs[tx_id] = tx
    ctx.storage["confirmations"] = confs
    ctx.storage["transactions"] = txs
    ctx.emit("Revoked", {"id": tx_id, "by": ctx.caller})


def execute_tx(ctx, tx_id: int) -> None:
    _require_owner(ctx)
    txs = ctx.storage.get("transactions") or {}
    tx = txs.get(tx_id)
    if tx is None:
        raise ValueError(f"tx {tx_id} not found")
    if tx.get("executed"):
        raise RuntimeError("already executed")
    required = ctx.storage.get("required", 1)
    if tx.get("confirmations", 0) < required:
        raise RuntimeError(f"need {required} confirmations, have {tx.get('confirmations', 0)}")
    tx["executed"] = True
    txs[tx_id] = tx
    ctx.storage["transactions"] = txs
    ctx.emit("Executed", {"id": tx_id})


def get_tx(ctx, tx_id: int) -> dict:
    txs = ctx.storage.get("transactions") or {}
    tx = txs.get(tx_id)
    if tx is None:
        raise ValueError(f"tx {tx_id} not found")
    return tx

# CURSOR
