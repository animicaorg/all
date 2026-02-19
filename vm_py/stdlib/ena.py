"""
vm_py.stdlib.ena — Deterministic ENA contract API
==================================================

This module provides the contract-facing surface for ENA (Embedded Neural Agent)
integration. All functions are deterministic and consensus-safe.

DESIGN: asynchronous oracle / receipt-based pattern.
- Inference NEVER runs directly inside contract execution.
- Contracts submit a request and receive a deterministic request_id.
- Results are read back after the off-chain worker submits a proof-attested
  receipt and the chain finalises the result (next-block or later).
- Contracts can only consume: request_id, status, result_hash, and DA pointer.
  They CANNOT trigger live inference inside the VM.

Gas charges (approximate, governance-adjustable):
  ena.request(...)      : GAS_ENA_REQUEST   (default: 5000)
  ena.get_status(...)   : GAS_ENA_STATUS    (default: 200)
  ena.get_result_hash(.): GAS_ENA_RESULT    (default: 200)
  ena.read_result(...)  : GAS_ENA_READ      (default: 1000)
  ena.verify_receipt(..)  : GAS_ENA_VERIFY  (default: 500)

Limits (governance-adjustable):
  MAX_MODEL_LEN        : 128 bytes
  MAX_TASK_TYPE_LEN    : 64 bytes
  MAX_INPUT_BYTES      : 4096 bytes (from chain policy)
  MAX_OUTPUT_BYTES     : 8192 bytes (from chain policy)
  MAX_CALLBACK_LEN     : 128 bytes

Events emitted:
  EnaRequested(request_id, creator, model_version, task_type, input_hash, fee_locked)
  EnaCompleted(request_id, result_hash, da_ptr)
  EnaFailed(request_id, reason)
"""

from __future__ import annotations

import hashlib
import json
from typing import Optional

# ---------------------------------------------------------------------------
# Gas constants (matches vm_py/gas_table.json entries; governance-adjustable)
# ---------------------------------------------------------------------------

GAS_ENA_REQUEST = 5000
GAS_ENA_STATUS = 200
GAS_ENA_RESULT = 200
GAS_ENA_READ = 1000
GAS_ENA_VERIFY = 500

# ---------------------------------------------------------------------------
# Size limits
# ---------------------------------------------------------------------------

MAX_MODEL_LEN = 128
MAX_TASK_TYPE_LEN = 64
MAX_INPUT_BYTES = 4096   # conservative default; policy may lower/raise
MAX_OUTPUT_BYTES = 8192  # conservative default; policy may lower/raise
MAX_CALLBACK_LEN = 128

# ---------------------------------------------------------------------------
# Lazy imports of runtime host / syscalls layer
# ---------------------------------------------------------------------------

def _get_runtime():
    """
    Return the runtime host module that bridges stdlib calls to the chain.

    In a full node: vm_py.runtime.ena_api provides live chain access.
    In tests / local dev: a thin deterministic simulator is returned.
    """
    try:
        from vm_py.runtime import ena_api  # type: ignore[import]
        return ena_api
    except ImportError:
        return _EnaSimulator()


def _get_events():
    try:
        from vm_py.stdlib import events as _events  # type: ignore[import]
        return _events
    except ImportError:
        return None


def _get_gas_meter():
    try:
        from vm_py.runtime import gas_meter as _gm  # type: ignore[import]
        return _gm
    except ImportError:
        return None


def _charge_gas(amount: int) -> None:
    gm = _get_gas_meter()
    if gm is not None and hasattr(gm, "charge"):
        gm.charge(amount)


# ---------------------------------------------------------------------------
# Deterministic request ID derivation (mirrors execution.state.ena_state)
# ---------------------------------------------------------------------------

def _sha3_hex(data: bytes) -> str:
    return hashlib.sha3_256(data).hexdigest()


def _derive_request_id(
    creator: bytes,
    model_version: str,
    task_type: str,
    input_hash: str,
    height: int,
    nonce: int = 0,
) -> str:
    canonical = json.dumps(
        {
            "creator": creator.hex(),
            "height": height,
            "input_hash": input_hash,
            "model_version": model_version,
            "nonce": nonce,
            "task_type": task_type,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "ena-" + hashlib.sha3_256(canonical).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Minimal ENA simulator (for tests / local dev)
# ---------------------------------------------------------------------------

class _EnaSimulator:
    """
    Thin deterministic simulator for the ENA runtime API.

    Used in tests and local-dev environments where the full chain host
    is not available. Returns deterministic placeholder values.
    """

    def __init__(self) -> None:
        self._requests: dict = {}
        self._results: dict = {}

    def request(
        self,
        creator: bytes,
        model_version: str,
        task_type: str,
        input_payload: bytes,
        fee_limit: int,
        current_height: int,
        callback: str = "",
        max_output_bytes: int = MAX_OUTPUT_BYTES,
        nonce: int = 0,
    ) -> dict:
        input_hash = _sha3_hex(input_payload)
        request_id = _derive_request_id(
            creator, model_version, task_type, input_hash, current_height, nonce
        )
        rec = {
            "request_id": request_id,
            "status": "queued",
            "input_hash": input_hash,
            "fee_locked": fee_limit,
        }
        self._requests[request_id] = rec
        return rec

    def get_status(self, request_id: str) -> str:
        rec = self._requests.get(request_id)
        if rec is None:
            return ""
        return rec.get("status", "queued")

    def get_result_hash(self, request_id: str) -> str:
        result = self._results.get(request_id)
        if result is None:
            return ""
        return result.get("result_hash", "")

    def read_result(self, request_id: str) -> Optional[bytes]:
        result = self._results.get(request_id)
        if result is None:
            return None
        inline = result.get("output_inline")
        if inline:
            return inline if isinstance(inline, bytes) else inline.encode("utf-8")
        return None

    def verify_receipt(
        self, request_id: str, result_hash: str, receipt_hash: str, worker_id: str
    ) -> tuple:
        if not request_id or not request_id.startswith("ena-"):
            return False, "invalid request_id"
        if not result_hash or len(result_hash) != 64:
            return False, "invalid result_hash"
        return True, "ok"

    def get_da_ptr(self, request_id: str) -> str:
        result = self._results.get(request_id)
        if result is None:
            return ""
        return result.get("da_ptr", "")

    # Test helper: inject a simulated result
    def _inject_result(
        self, request_id: str, result_hash: str, output_inline: bytes = b"", da_ptr: str = ""
    ) -> None:
        if request_id in self._requests:
            self._requests[request_id]["status"] = "completed"
        self._results[request_id] = {
            "result_hash": result_hash,
            "output_inline": output_inline,
            "da_ptr": da_ptr,
        }


# ---------------------------------------------------------------------------
# Type helpers
# ---------------------------------------------------------------------------

def _to_bytes(name: str, val) -> bytes:
    if isinstance(val, bytes):
        return val
    if isinstance(val, str):
        return val.encode("utf-8")
    if isinstance(val, (bytearray, memoryview)):
        return bytes(val)
    raise TypeError(f"{name} must be bytes-like or str, got {type(val).__name__}")


def _check_size(name: str, data: bytes, limit: int) -> None:
    if len(data) > limit:
        raise ValueError(f"{name} exceeds limit: {len(data)} > {limit} bytes")


def _check_nonempty(name: str, data) -> None:
    if not data:
        raise ValueError(f"{name} must not be empty")


def _to_creator_bytes(creator) -> bytes:
    if creator is None:
        return b"\x00" * 32
    return _to_bytes("creator", creator)


def _get_current_height() -> int:
    """Return current block height from the runtime, or 0 in local/test mode."""
    try:
        from vm_py.runtime import block_env as _be  # type: ignore[import]
        return int(getattr(_be, "height", 0) or 0)
    except ImportError:
        return 0


def _get_caller() -> bytes:
    """Return caller address from the runtime, or zero bytes in local/test mode."""
    try:
        from vm_py.runtime import execution_env as _ee  # type: ignore[import]
        caller = getattr(_ee, "caller", b"\x00" * 32)
        return _to_bytes("caller", caller)
    except ImportError:
        return b"\x00" * 32


# ---------------------------------------------------------------------------
# Public contract API
# ---------------------------------------------------------------------------


def request(
    model_version: str,
    task_type: str,
    input_payload,
    fee_limit: int,
    *,
    callback: str = "",
    max_output_bytes: int = MAX_OUTPUT_BYTES,
    nonce: int = 0,
) -> str:
    """
    Create an ENA inference request (asynchronous).

    This function is DETERMINISTIC and CONSENSUS-SAFE. It does NOT run any
    AI inference. It records the request on-chain and returns a deterministic
    request_id that can be used to poll for results later.

    Args:
        model_version: Required. ENA model version id (e.g., "ena-v0.9.0-h10000").
                       Must be a registered, active model version.
        task_type:     Required. Task category ("classify", "embed", "summarize", "custom").
        input_payload: Required. Input bytes or UTF-8 string. Max MAX_INPUT_BYTES.
        fee_limit:     Required. Maximum ANM nano-units to spend on this request.
                       Excess is refunded after result finalisation.
        callback:      Optional. Contract method name to call when result is ready.
        max_output_bytes: Optional. Maximum output bytes to store inline.
        nonce:         Optional. Extra nonce for request_id uniqueness.

    Returns:
        request_id (str): deterministic identifier for this request.

    Raises:
        TypeError: if types are wrong.
        ValueError: if limits are exceeded or policy rejects the request.

    Gas: GAS_ENA_REQUEST units are charged.

    Events emitted:
        EnaRequested(request_id, model_version, task_type, input_hash, fee_locked)
    """
    _charge_gas(GAS_ENA_REQUEST)

    # Validate and normalise inputs
    model_b = _to_bytes("model_version", model_version)
    _check_nonempty("model_version", model_b)
    _check_size("model_version", model_b, MAX_MODEL_LEN)

    task_b = _to_bytes("task_type", task_type)
    _check_nonempty("task_type", task_b)
    _check_size("task_type", task_b, MAX_TASK_TYPE_LEN)

    input_b = _to_bytes("input_payload", input_payload)
    _check_nonempty("input_payload", input_b)
    _check_size("input_payload", input_b, MAX_INPUT_BYTES)

    if not isinstance(fee_limit, int) or fee_limit <= 0:
        raise ValueError("fee_limit must be a positive integer (ANM nano-units)")

    if callback:
        cb_b = _to_bytes("callback", callback)
        _check_size("callback", cb_b, MAX_CALLBACK_LEN)

    if max_output_bytes < 0:
        raise ValueError("max_output_bytes must be non-negative")

    creator = _get_caller()
    current_height = _get_current_height()

    rt = _get_runtime()
    result = rt.request(
        creator=creator,
        model_version=model_version,
        task_type=task_type,
        input_payload=input_b,
        fee_limit=fee_limit,
        current_height=current_height,
        callback=callback,
        max_output_bytes=max_output_bytes,
        nonce=nonce,
    )

    request_id = result.get("request_id", "") if isinstance(result, dict) else str(result)

    # Emit canonical event
    evts = _get_events()
    if evts is not None:
        try:
            evts.emit(
                b"EnaRequested",
                {
                    b"request_id": request_id.encode("utf-8"),
                    b"model_version": model_b,
                    b"task_type": task_b,
                    b"input_hash": result.get("input_hash", "").encode("utf-8") if isinstance(result, dict) else b"",
                    b"fee_locked": fee_limit,
                },
            )
        except Exception:
            pass

    return request_id


def get_status(request_id: str) -> str:
    """
    Read-only status lookup for an ENA request.

    Returns one of: "queued", "running", "completed", "failed", "expired",
    or "" if not found.

    Gas: GAS_ENA_STATUS units are charged.
    """
    _charge_gas(GAS_ENA_STATUS)

    if not request_id:
        raise ValueError("request_id must not be empty")

    rt = _get_runtime()
    return rt.get_status(request_id)


def get_result_hash(request_id: str) -> str:
    """
    Return the SHA3-256 hex hash of the result payload for a completed request.

    This is the primary deterministic way for contracts to verify a result.
    Returns "" if the result is not yet available.

    Gas: GAS_ENA_RESULT units are charged.
    """
    _charge_gas(GAS_ENA_RESULT)

    if not request_id:
        raise ValueError("request_id must not be empty")

    rt = _get_runtime()
    return rt.get_result_hash(request_id)


def read_result(request_id: str) -> Optional[bytes]:
    """
    Read the inline result payload for a completed request.

    This function returns the raw result bytes ONLY if:
    1. The result is committed on-chain (status == "completed").
    2. The result was stored inline (not via DA pointer only).
    3. The result size is within the safe inline limit (MAX_OUTPUT_BYTES).

    For large results, use get_result_hash() + get_da_ptr() to retrieve
    the DA commitment and fetch the full result off-chain.

    Returns None if result is not yet available or is stored only as DA pointer.

    Gas: GAS_ENA_READ units are charged.
    """
    _charge_gas(GAS_ENA_READ)

    if not request_id:
        raise ValueError("request_id must not be empty")

    rt = _get_runtime()
    return rt.read_result(request_id)


def get_da_ptr(request_id: str) -> str:
    """
    Return the DA commitment pointer for the result of a completed request.

    For large outputs, the actual result is stored in the DA layer and only
    the commitment is stored on-chain. Use this to retrieve the DA commitment
    for off-chain fetching.

    Returns "" if not available.

    Gas: GAS_ENA_RESULT units are charged.
    """
    _charge_gas(GAS_ENA_RESULT)

    if not request_id:
        raise ValueError("request_id must not be empty")

    rt = _get_runtime()
    if hasattr(rt, "get_da_ptr"):
        return rt.get_da_ptr(request_id)
    return ""


def verify_receipt(
    request_id: str,
    result_hash: str,
    receipt_hash: str,
    worker_id: str,
) -> tuple:
    """
    Deterministically validate a worker receipt/proof format.

    This function performs structural/format validation of a worker receipt.
    Full cryptographic proof verification is handled by the proof subsystem.

    Args:
        request_id:   The ENA request ID.
        result_hash:  SHA3-256 hex hash of the result payload.
        receipt_hash: Worker-provided proof/receipt hash.
        worker_id:    Worker / provider identifier.

    Returns:
        (is_valid: bool, reason: str) tuple.

    Gas: GAS_ENA_VERIFY units are charged.
    """
    _charge_gas(GAS_ENA_VERIFY)

    rt = _get_runtime()
    if hasattr(rt, "verify_receipt"):
        return rt.verify_receipt(request_id, result_hash, receipt_hash, worker_id)

    # Structural fallback
    if not request_id or not request_id.startswith("ena-"):
        return False, "invalid request_id format"
    if not result_hash or len(result_hash) != 64:
        return False, "invalid result_hash (expected 64-char hex)"
    if not receipt_hash or len(receipt_hash) < 16:
        return False, "invalid receipt_hash (too short)"
    if not worker_id:
        return False, "worker_id must not be empty"
    return True, "ok"


__all__ = [
    "request",
    "get_status",
    "get_result_hash",
    "read_result",
    "get_da_ptr",
    "verify_receipt",
    # Gas constants (readable by contracts/tools)
    "GAS_ENA_REQUEST",
    "GAS_ENA_STATUS",
    "GAS_ENA_RESULT",
    "GAS_ENA_READ",
    "GAS_ENA_VERIFY",
    # Size limits (readable by contracts/tools)
    "MAX_MODEL_LEN",
    "MAX_TASK_TYPE_LEN",
    "MAX_INPUT_BYTES",
    "MAX_OUTPUT_BYTES",
    "MAX_CALLBACK_LEN",
    # Internal simulator (for testing)
    "_EnaSimulator",
    "_derive_request_id",
    "_sha3_hex",
]
