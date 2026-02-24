"""{{CONTRACT_NAME}} — Data Availability commitment registry.

Author: {{AUTHOR}}
Namespace: {{NAMESPACE}}

Stores mappings from a content_key (e.g. model hash, file hash) to:
- DA URI (e.g. ipfs://... or da://namespace/cid)
- content hash (sha256 hex)
- submitter address
- blob size (bytes)

Read by clients who need to locate off-chain artifacts.
"""

STORAGE = {
    "owner": "address",
    "namespace": "string",
    "commitments": "map(string, dict)",  # content_key -> commitment
    "count": "int",
}

ABI = [
    {"name": "deploy",           "type": "constructor", "inputs": [{"name": "owner", "type": "address"}, {"name": "namespace", "type": "string"}]},
    {"name": "register",         "type": "function",    "inputs": [{"name": "content_key", "type": "string"}, {"name": "da_uri", "type": "string"}, {"name": "content_hash", "type": "string"}, {"name": "size_bytes", "type": "int"}]},
    {"name": "get",              "type": "function",    "inputs": [{"name": "content_key", "type": "string"}], "outputs": [{"type": "dict"}], "stateMutability": "view"},
    {"name": "exists",           "type": "function",    "inputs": [{"name": "content_key", "type": "string"}], "outputs": [{"type": "bool"}], "stateMutability": "view"},
    {"name": "update_uri",       "type": "function",    "inputs": [{"name": "content_key", "type": "string"}, {"name": "new_uri", "type": "string"}]},
    {"name": "Registered",       "type": "event",       "inputs": [{"name": "content_key", "type": "string"}, {"name": "da_uri", "type": "string"}, {"name": "content_hash", "type": "string"}]},
    {"name": "UriUpdated",       "type": "event",       "inputs": [{"name": "content_key", "type": "string"}, {"name": "old_uri", "type": "string"}, {"name": "new_uri", "type": "string"}]},
]


def deploy(ctx, owner: str, namespace: str) -> None:
    ctx.storage["owner"] = owner
    ctx.storage["namespace"] = namespace or "{{NAMESPACE}}"
    ctx.storage["commitments"] = {}
    ctx.storage["count"] = 0


def register(ctx, content_key: str, da_uri: str, content_hash: str, size_bytes: int) -> None:
    if not content_key:
        raise ValueError("content_key required")
    if not da_uri:
        raise ValueError("da_uri required")
    if not content_hash:
        raise ValueError("content_hash required")
    if size_bytes < 0:
        raise ValueError("size_bytes must be >= 0")
    commitments = ctx.storage.get("commitments") or {}
    if content_key in commitments:
        raise RuntimeError(f"commitment for {content_key!r} already registered — use update_uri to change URI")
    commitments[content_key] = {
        "content_key": content_key,
        "da_uri": da_uri,
        "content_hash": content_hash,
        "size_bytes": size_bytes,
        "submitter": ctx.caller,
        "namespace": ctx.storage.get("namespace", ""),
    }
    ctx.storage["commitments"] = commitments
    ctx.storage["count"] = ctx.storage.get("count", 0) + 1
    ctx.emit("Registered", {"content_key": content_key, "da_uri": da_uri, "content_hash": content_hash})


def get(ctx, content_key: str) -> dict:
    commitments = ctx.storage.get("commitments") or {}
    c = commitments.get(content_key)
    if c is None:
        raise ValueError(f"no commitment for {content_key!r}")
    return c


def exists(ctx, content_key: str) -> bool:
    commitments = ctx.storage.get("commitments") or {}
    return content_key in commitments


def update_uri(ctx, content_key: str, new_uri: str) -> None:
    """Update the DA URI for an existing commitment (owner or submitter only)."""
    commitments = ctx.storage.get("commitments") or {}
    c = commitments.get(content_key)
    if c is None:
        raise ValueError(f"no commitment for {content_key!r}")
    if ctx.caller not in (ctx.storage.get("owner"), c.get("submitter")):
        raise PermissionError("only owner or original submitter can update URI")
    old_uri = c["da_uri"]
    c["da_uri"] = new_uri
    commitments[content_key] = c
    ctx.storage["commitments"] = commitments
    ctx.emit("UriUpdated", {"content_key": content_key, "old_uri": old_uri, "new_uri": new_uri})

# CURSOR
