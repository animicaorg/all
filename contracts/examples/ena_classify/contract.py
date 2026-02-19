# -*- coding: utf-8 -*-
"""
ENA Classification Contract

Demonstrates using ENA for on-chain text classification via the
asynchronous oracle pattern.

Lifecycle:
  1. Call classify(text) → returns request_id, status=queued
  2. Off-chain worker processes the request and submits result
  3. Call check_result(request_id) → returns (status, result_hash)
  4. Use result_hash for on-chain verification

State:
  req:{request_id} = b"1"        — tracks known requests
  res:{request_id} = result_hash — stores result hash when available
"""

from stdlib import ena, storage, events, abi  # type: ignore[reportMissingImports]

# ---- configuration -----------------------------------------------------------

MODEL_VERSION = "ena-v0.9.0-h10000"
TASK_TYPE = "classify"
FEE_LIMIT = 10_000      # 10,000 ANM nano-units (~0.00001 ANM)
MAX_TEXT_LEN = 3000     # conservative cap (below chain policy max)

_REQ_PREFIX = b"req:"
_RES_PREFIX = b"res:"


# ---- helpers -----------------------------------------------------------------

def _req_key(request_id: str) -> bytes:
    return _REQ_PREFIX + request_id.encode("utf-8")


def _res_key(request_id: str) -> bytes:
    return _RES_PREFIX + request_id.encode("utf-8")


# ---- contract API ------------------------------------------------------------

def classify(text: bytes) -> bytes:
    """
    Submit an ENA classification request for the given text.

    Args:
        text: UTF-8 text bytes to classify (max MAX_TEXT_LEN bytes).

    Returns:
        request_id bytes for result polling.

    Fee:
        Debits FEE_LIMIT ANM nano-units from caller.
    """
    if len(text) == 0 or len(text) > MAX_TEXT_LEN:
        abi.revert(b"text_len_out_of_range")

    # Submit async ENA request (NO inference happens here)
    request_id = ena.request(
        model_version=MODEL_VERSION,
        task_type=TASK_TYPE,
        input_payload=text,
        fee_limit=FEE_LIMIT,
    )

    # Record the request
    storage.set(_req_key(request_id), b"1")

    events.emit(
        b"ClassifyRequested",
        {
            b"request_id": request_id.encode("utf-8"),
            b"text_len": len(text),
            b"model_version": MODEL_VERSION.encode("utf-8"),
        },
    )

    return request_id.encode("utf-8")


def check_result(request_id: bytes) -> tuple:
    """
    Check the status and result of a previously submitted classification.

    Args:
        request_id: The bytes returned by classify().

    Returns:
        (status: str, result_hash: str)
        status is one of: queued, running, completed, failed, expired
        result_hash is the SHA3-256 hex hash of the inference output (or "")
    """
    req_id_str = request_id.decode("utf-8")

    if not storage.get(_req_key(req_id_str)):
        return "not_found", ""

    # Check cached result hash (avoid re-reading state on every call)
    cached = storage.get(_res_key(req_id_str))
    if cached:
        return "completed", cached.decode("utf-8")

    status = ena.get_status(req_id_str)

    if status == "completed":
        result_hash = ena.get_result_hash(req_id_str)
        if result_hash:
            # Cache the result hash for future reads
            storage.set(_res_key(req_id_str), result_hash.encode("utf-8"))
            events.emit(
                b"ClassifyCompleted",
                {
                    b"request_id": request_id,
                    b"result_hash": result_hash.encode("utf-8"),
                },
            )
        return status, result_hash

    return status, ""


def read_inline_result(request_id: bytes) -> bytes:
    """
    Read the inline result payload (if stored on-chain).

    For small classification outputs (< chain policy max_output_bytes),
    the result is stored inline. Otherwise, use get_da_ptr().

    Returns raw result bytes or b"" if not available.
    """
    req_id_str = request_id.decode("utf-8")
    if ena.get_status(req_id_str) != "completed":
        return b""
    result = ena.read_result(req_id_str)
    return result if result is not None else b""


def get_da_ptr(request_id: bytes) -> bytes:
    """Return DA commitment pointer for large results."""
    req_id_str = request_id.decode("utf-8")
    if ena.get_status(req_id_str) != "completed":
        return b""
    return ena.get_da_ptr(req_id_str).encode("utf-8")


def verify(request_id: bytes, claimed_result_hash: bytes) -> bool:
    """
    Verify that the on-chain committed result hash matches a claimed hash.

    Args:
        request_id: The classification request ID.
        claimed_result_hash: The hash to verify (hex string bytes).

    Returns:
        True if the claimed hash matches the committed result hash.
    """
    req_id_str = request_id.decode("utf-8")
    if ena.get_status(req_id_str) != "completed":
        return False
    on_chain_hash = ena.get_result_hash(req_id_str)
    if not on_chain_hash:
        return False
    return on_chain_hash == claimed_result_hash.decode("utf-8")
