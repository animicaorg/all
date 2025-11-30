# QuantumWorkers contract skeleton
# Deterministic Python contract for the VM

from stdlib import abi, events, storage

K_INIT = b"qw/init"
K_WORKER_PREFIX = b"qw/worker/"  # + worker_id -> struct
K_WORKER_SEQ = b"qw/seq"

UINT256_MAX = (1 << 256) - 1

# Helpers


def _get_uint(k):
    v = storage.get(k)
    if v is None:
        return 0
    if not isinstance(v, int):
        abi.revert(b"ERR_TYPE_UINT")
    return v


def _set_uint(k, v):
    storage.set(k, int(v))


def _worker_key(worker_id: bytes) -> bytes:
    return K_WORKER_PREFIX + worker_id


# Views


def get_worker(worker_id: bytes) -> dict:
    w = storage.get(_worker_key(worker_id))
    if w is None:
        return {"exists": False}
    return w


# State-changing


def register_worker(pubkey: bytes, metadata: bytes) -> bytes:
    # Assign simple incrementing worker id (bytes)
    seq = _get_uint(K_WORKER_SEQ)
    worker_id = seq.to_bytes(8, "big")

    # Minimal validation
    if not isinstance(pubkey, bytes):
        abi.revert(b"ERR_INVALID_PUBKEY")

    storage.set(
        _worker_key(worker_id),
        {
            "pubkey": pubkey,
            "stake": 0,
            "status": b"active",
            "reputation": 0,
            "metadata": metadata,
        },
    )

    _set_uint(K_WORKER_SEQ, seq + 1)

    events.emit(b"WorkerRegistered", [worker_id, pubkey])
    return worker_id


def stake(worker_id: bytes, amount: int) -> None:
    # TODO: integrate with token transfer & escrow
    w = storage.get(_worker_key(worker_id))
    if w is None:
        abi.revert(b"ERR_UNKNOWN_WORKER")
    w["stake"] = w.get("stake", 0) + amount
    storage.set(_worker_key(worker_id), w)
    events.emit(b"WorkerStaked", [worker_id, amount])


def slash(worker_id: bytes, reason: bytes) -> None:
    # Only governance / admin should call this in production
    w = storage.get(_worker_key(worker_id))
    if w is None:
        abi.revert(b"ERR_UNKNOWN_WORKER")
    w["status"] = b"slashed"
    storage.set(_worker_key(worker_id), w)
    events.emit(b"WorkerSlashed", [worker_id, reason])


def update_reputation(worker_id: bytes, delta: int) -> None:
    w = storage.get(_worker_key(worker_id))
    if w is None:
        abi.revert(b"ERR_UNKNOWN_WORKER")
    w["reputation"] = w.get("reputation", 0) + delta
    storage.set(_worker_key(worker_id), w)
    events.emit(b"ReputationUpdated", [worker_id, delta])


# Main dispatcher


def main(action: bytes, **kwargs) -> bytes:
    if action == b"register_worker":
        return register_worker(kwargs["pubkey"], kwargs.get("metadata", b""))
    if action == b"stake":
        stake(kwargs["worker_id"], kwargs["amount"])
        return b""
    if action == b"slash":
        slash(kwargs["worker_id"], kwargs.get("reason", b""))
        return b""
    if action == b"update_reputation":
        update_reputation(kwargs["worker_id"], kwargs["delta"])
        return b""
    if action == b"get_worker":
        return abi.encode_struct(get_worker(kwargs["worker_id"]))
    abi.revert(b"ERR_UNKNOWN_ACTION")
