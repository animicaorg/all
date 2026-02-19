# -*- coding: utf-8 -*-
"""
ENA Embedding Contract

Demonstrates storing embedding result hashes and DA pointers
for semantic search and similarity verification use cases.

Lifecycle:
  1. Call embed(key, text) → stores request_id under key
  2. Off-chain worker processes and submits embedding result
  3. Call get_embedding_hash(key) → returns result_hash
  4. Use result_hash for similarity verification

Large embedding vectors are stored in DA layer; only the
SHA3-256 hash is stored inline on-chain.
"""

from stdlib import ena, storage, events, abi  # type: ignore[reportMissingImports]

# ---- configuration -----------------------------------------------------------

MODEL_VERSION = "ena-v0.9.0-h10000"
TASK_TYPE = "embed"
FEE_LIMIT = 5_000       # 5,000 ANM nano-units
MAX_TEXT_LEN = 2048
MAX_KEY_LEN = 64

_KEY_REQ = b"embed_req:"
_KEY_HASH = b"embed_hash:"
_KEY_DA = b"embed_da:"


# ---- helpers -----------------------------------------------------------------

def _req_storage_key(key: bytes) -> bytes:
    return _KEY_REQ + key


def _hash_storage_key(key: bytes) -> bytes:
    return _KEY_HASH + key


def _da_storage_key(key: bytes) -> bytes:
    return _KEY_DA + key


# ---- contract API ------------------------------------------------------------

def embed(key: bytes, text: bytes) -> bytes:
    """
    Submit an ENA embedding request for the given text.

    Args:
        key:  Storage key to associate with this embedding (max MAX_KEY_LEN bytes).
        text: UTF-8 text to embed (max MAX_TEXT_LEN bytes).

    Returns:
        request_id bytes.
    """
    if len(key) == 0 or len(key) > MAX_KEY_LEN:
        abi.revert(b"key_len_out_of_range")
    if len(text) == 0 or len(text) > MAX_TEXT_LEN:
        abi.revert(b"text_len_out_of_range")

    request_id = ena.request(
        model_version=MODEL_VERSION,
        task_type=TASK_TYPE,
        input_payload=text,
        fee_limit=FEE_LIMIT,
    )

    storage.set(_req_storage_key(key), request_id.encode("utf-8"))

    events.emit(
        b"EmbedRequested",
        {
            b"key": key,
            b"request_id": request_id.encode("utf-8"),
            b"text_len": len(text),
        },
    )

    return request_id.encode("utf-8")


def get_embedding_status(key: bytes) -> bytes:
    """
    Get the status of the embedding request associated with a key.

    Returns status bytes: b"queued", b"completed", b"failed", etc.
    """
    req_raw = storage.get(_req_storage_key(key))
    if not req_raw:
        return b"not_found"
    return ena.get_status(req_raw.decode("utf-8")).encode("utf-8")


def settle_embedding(key: bytes) -> bytes:
    """
    Cache the result hash and DA pointer once the embedding is complete.

    Call this after the worker has submitted the result.
    Idempotent: calling multiple times is safe.

    Returns the result_hash bytes (64-char hex), or b"" if not ready.
    """
    req_raw = storage.get(_req_storage_key(key))
    if not req_raw:
        return b""

    # Check cached hash
    cached = storage.get(_hash_storage_key(key))
    if cached:
        return cached

    req_id_str = req_raw.decode("utf-8")
    status = ena.get_status(req_id_str)

    if status != "completed":
        return b""

    result_hash = ena.get_result_hash(req_id_str)
    da_ptr = ena.get_da_ptr(req_id_str)

    if result_hash:
        storage.set(_hash_storage_key(key), result_hash.encode("utf-8"))
    if da_ptr:
        storage.set(_da_storage_key(key), da_ptr.encode("utf-8"))

    events.emit(
        b"EmbedSettled",
        {
            b"key": key,
            b"result_hash": result_hash.encode("utf-8") if result_hash else b"",
            b"da_ptr": da_ptr.encode("utf-8") if da_ptr else b"",
        },
    )

    return result_hash.encode("utf-8") if result_hash else b""


def get_embedding_hash(key: bytes) -> bytes:
    """
    Return the stored SHA3-256 hash of the embedding result.

    Returns b"" if not yet available. Call settle_embedding() first.
    """
    return storage.get(_hash_storage_key(key)) or b""


def get_embedding_da_ptr(key: bytes) -> bytes:
    """
    Return the DA pointer for the embedding vector.

    Use this to fetch the actual embedding vector off-chain:
        animica da get <da_ptr>
    """
    return storage.get(_da_storage_key(key)) or b""


def verify_similarity(key_a: bytes, key_b: bytes) -> tuple:
    """
    Return the two embedding hashes for off-chain similarity computation.

    On-chain code cannot compute cosine similarity (requires floating-point);
    this function returns the two hashes for off-chain verification.

    Returns:
        (hash_a: bytes, hash_b: bytes, da_ptr_a: bytes, da_ptr_b: bytes)
    """
    hash_a = get_embedding_hash(key_a)
    hash_b = get_embedding_hash(key_b)
    da_a = get_embedding_da_ptr(key_a)
    da_b = get_embedding_da_ptr(key_b)
    return hash_a, hash_b, da_a, da_b
