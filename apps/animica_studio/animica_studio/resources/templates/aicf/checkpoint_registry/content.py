"""{{CONTRACT_NAME}} — ENA model checkpoint registry.

Author: {{AUTHOR}}

Records training checkpoints on-chain, linking:
- model_id + epoch -> DA URI (off-chain weights blob)
- parameter count, metric (e.g. loss)
- training run hash for reproducibility
"""

STORAGE = {
    "owner": "address",
    "checkpoints": "map(string, dict)",  # "model_id:epoch" -> checkpoint
    "model_latest": "map(string, int)",  # model_id -> latest epoch
    "count": "int",
}

ABI = [
    {"name": "deploy",         "type": "constructor", "inputs": [{"name": "owner", "type": "address"}]},
    {"name": "record",         "type": "function",    "inputs": [{"name": "model_id", "type": "string"}, {"name": "epoch", "type": "int"}, {"name": "da_uri", "type": "string"}, {"name": "weights_hash", "type": "string"}, {"name": "params", "type": "int"}, {"name": "metric", "type": "string"}]},
    {"name": "get",            "type": "function",    "inputs": [{"name": "model_id", "type": "string"}, {"name": "epoch", "type": "int"}], "outputs": [{"type": "dict"}], "stateMutability": "view"},
    {"name": "latest_epoch",   "type": "function",    "inputs": [{"name": "model_id", "type": "string"}], "outputs": [{"type": "int"}], "stateMutability": "view"},
    {"name": "Recorded",       "type": "event",       "inputs": [{"name": "model_id", "type": "string"}, {"name": "epoch", "type": "int"}, {"name": "da_uri", "type": "string"}]},
]


def deploy(ctx, owner: str) -> None:
    ctx.storage["owner"] = owner
    ctx.storage["checkpoints"] = {}
    ctx.storage["model_latest"] = {}
    ctx.storage["count"] = 0


def record(ctx, model_id: str, epoch: int, da_uri: str, weights_hash: str, params: int, metric: str) -> None:
    if ctx.caller != ctx.storage.get("owner"):
        raise PermissionError("only owner can record checkpoints")
    if not model_id:
        raise ValueError("model_id required")
    if epoch < 0:
        raise ValueError("epoch must be >= 0")
    if not da_uri:
        raise ValueError("da_uri required")
    key = f"{model_id}:{epoch}"
    checkpoints = ctx.storage.get("checkpoints") or {}
    if key in checkpoints:
        raise RuntimeError(f"checkpoint {key} already recorded")
    checkpoints[key] = {
        "model_id": model_id,
        "epoch": epoch,
        "da_uri": da_uri,
        "weights_hash": weights_hash,
        "params": params,
        "metric": metric,
        "recorder": ctx.caller,
    }
    ctx.storage["checkpoints"] = checkpoints
    ctx.storage["count"] = ctx.storage.get("count", 0) + 1
    latest = ctx.storage.get("model_latest") or {}
    if epoch > latest.get(model_id, -1):
        latest[model_id] = epoch
        ctx.storage["model_latest"] = latest
    ctx.emit("Recorded", {"model_id": model_id, "epoch": epoch, "da_uri": da_uri})


def get(ctx, model_id: str, epoch: int) -> dict:
    key = f"{model_id}:{epoch}"
    checkpoints = ctx.storage.get("checkpoints") or {}
    c = checkpoints.get(key)
    if c is None:
        raise ValueError(f"no checkpoint for {key}")
    return c


def latest_epoch(ctx, model_id: str) -> int:
    latest = ctx.storage.get("model_latest") or {}
    return latest.get(model_id, -1)

# CURSOR
