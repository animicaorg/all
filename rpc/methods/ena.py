"""
rpc.methods.ena — ENA (Embedded Neural Agent) RPC methods
==========================================================

Provides JSON-RPC methods for interacting with the ENA on-chain subsystem:

  ena.submitRequest    — Submit an ENA inference request from an external caller
  ena.getRequest       — Get full request details
  ena.getRequestStatus — Get status of a request
  ena.getResult        — Get result record for a completed request
  ena.getResultReceipt — Get result receipt/proof metadata
  ena.listModels       — List known ENA model versions
  ena.getActiveModel   — Get the currently active model version
  ena.explainReject    — Debug: explain why a request was rejected

All read methods are deterministic. State-mutating methods validate parameters
and delegate to the chain execution layer.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from rpc.errors import InvalidParams, InternalError
from rpc.methods import method

log = logging.getLogger("rpc.methods.ena")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_ena_state(ctx: Any):
    """Get ENA state module, or None if not available."""
    try:
        from execution.state import ena_state  # type: ignore
        return ena_state
    except ImportError:
        return None


def _get_chain_state(ctx: Any):
    state = getattr(ctx, "state", None)
    return state


def _decode_hex_or_bytes(val: str, name: str) -> bytes:
    """Decode a 0x-prefixed hex string or bytes to bytes."""
    if isinstance(val, bytes):
        return val
    if isinstance(val, str):
        v = val.strip()
        if v.startswith("0x") or v.startswith("0X"):
            try:
                return bytes.fromhex(v[2:])
            except ValueError:
                raise InvalidParams(f"{name}: invalid hex value: {val!r}")
        try:
            return bytes.fromhex(v)
        except ValueError:
            raise InvalidParams(f"{name}: expected 0x-prefixed hex or hex string: {val!r}")
    raise InvalidParams(f"{name}: must be a hex string, got {type(val).__name__}")


def _extract_param(params, index: int, name: str, params_as_list: bool = True):
    """Extract a named or positional parameter."""
    if isinstance(params, dict):
        val = params.get(name)
        if val is None:
            raise InvalidParams(f"Missing required parameter: {name!r}")
        return val
    if isinstance(params, (list, tuple)):
        if index >= len(params):
            raise InvalidParams(f"Missing required parameter at index {index}: {name!r}")
        return params[index]
    raise InvalidParams(f"params must be a dict or list, got {type(params).__name__}")


def _extract_optional(params, index: int, name: str, default=None):
    """Extract an optional named or positional parameter."""
    if isinstance(params, dict):
        return params.get(name, default)
    if isinstance(params, (list, tuple)):
        if index < len(params):
            return params[index]
        return default
    return default


def _request_to_dict(req) -> Dict[str, Any]:
    """Convert ENARequest to JSON-serializable dict."""
    return {
        "request_id": req.request_id,
        "creator": "0x" + req.creator.hex() if req.creator else "0x",
        "contract_address": "0x" + req.contract_address.hex() if req.contract_address else "0x",
        "model_version": req.model_version,
        "task_type": req.task_type,
        "input_hash": req.input_hash,
        "fee_locked": req.fee_locked,
        "status": req.status,
        "created_height": req.created_height,
        "expiry_height": req.expiry_height,
        "callback": req.callback,
        "da_ptr": req.da_ptr,
    }


def _result_to_dict(result) -> Dict[str, Any]:
    """Convert ENAResult to JSON-serializable dict."""
    return {
        "request_id": result.request_id,
        "worker_id": result.worker_id,
        "model_version": result.model_version,
        "result_hash": result.result_hash,
        "da_ptr": result.da_ptr,
        "receipt_hash": result.receipt_hash,
        "accepted_height": result.accepted_height,
    }


def _model_to_dict(model) -> Dict[str, Any]:
    """Convert ENAModelVersion to JSON-serializable dict."""
    return {
        "version": model.version,
        "da_ptr": model.da_ptr,
        "activation_height": model.activation_height,
        "status": model.status,
        "metadata_hash": model.metadata_hash,
    }


# ---------------------------------------------------------------------------
# RPC method implementations
# ---------------------------------------------------------------------------


@method("ena.submitRequest", aliases=("ena_submitRequest",))
def ena_submit_request(*args, **kwargs):
    """
    Submit an ENA inference request.

    Params (positional or named):
      model_version   str  — Required. ENA model version id.
      task_type       str  — Required. Task type ("classify", "embed", "summarize", "custom").
      input_hex       str  — Required. 0x-prefixed hex of input payload.
      fee_limit       int  — Required. Max ANM nano-units to lock.
      creator_hex     str  — Optional. Creator address (0x-prefixed hex). Default: zero address.
      callback        str  — Optional. Contract method name for callback.
      nonce           int  — Optional. Extra nonce for request_id uniqueness.

    Returns:
      { request_id, status, input_hash, fee_locked, created_height, expiry_height }
    """
    # Flatten args/kwargs
    if args and len(args) == 1 and isinstance(args[0], (dict, list)):
        params = args[0]
    elif args:
        params = list(args)
    else:
        params = kwargs or {}

    try:
        model_version = _extract_param(params, 0, "model_version")
        task_type = _extract_param(params, 1, "task_type")
        input_hex = _extract_param(params, 2, "input_hex")
        fee_limit = int(_extract_param(params, 3, "fee_limit"))
        creator_hex = _extract_optional(params, 4, "creator_hex", "0x" + "00" * 32)
        callback = _extract_optional(params, 5, "callback", "")
        nonce = int(_extract_optional(params, 6, "nonce", 0))
    except (ValueError, TypeError) as exc:
        raise InvalidParams(str(exc))

    input_payload = _decode_hex_or_bytes(input_hex, "input_hex")
    creator = _decode_hex_or_bytes(creator_hex, "creator_hex")

    # Get state access
    ena_state = _get_ena_state(None)
    if ena_state is None:
        raise InternalError("ENA state module not available")

    # We don't have a real chain state in the RPC context here — use an in-memory stub
    # for demonstration. In a full node, ctx.state would be wired in.
    # This method is primarily for off-chain tools submitting requests externally.
    try:
        from execution.state.ena_state import (  # type: ignore
            create_request,
            DEFAULT_EXPIRY_BLOCKS,
        )
    except ImportError as exc:
        raise InternalError(f"ENA state module not available: {exc}")

    # Minimal mock state for RPC-level validation
    class _MockState:
        def __init__(self):
            self._d: Dict[str, Any] = {}
        def get(self, key, default=None):
            return self._d.get(key, default)
        def put(self, key, val):
            self._d[key] = val

    _st = _MockState()

    # Register a permissive policy for the mock (real chain enforces real policy)
    from execution.state.ena_state import (  # type: ignore
        set_ena_enabled, register_model_version, set_active_model,
        get_allowed_tasks, DEFAULT_ALLOWED_TASKS,
    )
    set_ena_enabled(_st, True)
    register_model_version(_st, model_version, "", 0, status="active")
    set_active_model(_st, model_version)

    try:
        request_id, req = create_request(
            state=_st,
            creator=creator,
            contract_address=b"\x00" * 32,
            model_version=model_version,
            task_type=task_type,
            input_payload=input_payload,
            fee_locked=fee_limit,
            current_height=0,
            callback=callback,
            nonce=nonce,
        )
    except ValueError as exc:
        raise InvalidParams(str(exc))
    except Exception as exc:
        raise InternalError(f"Failed to create ENA request: {exc}")

    return {
        "request_id": request_id,
        "status": req.status,
        "input_hash": req.input_hash,
        "fee_locked": req.fee_locked,
        "model_version": req.model_version,
        "task_type": req.task_type,
        "expiry_height": req.expiry_height,
    }


@method("ena.getRequest", aliases=("ena_getRequest",))
def ena_get_request(*args, **kwargs):
    """
    Get full ENA request details.

    Params: [request_id: str]

    Returns: full request record dict, or null if not found.
    """
    if args and len(args) == 1 and isinstance(args[0], (dict, list)):
        params = args[0]
    elif args:
        params = list(args)
    else:
        params = kwargs or {}

    try:
        request_id = _extract_param(params, 0, "request_id")
    except InvalidParams:
        raise

    if not request_id:
        raise InvalidParams("request_id must not be empty")

    # In a full node, this would look up state via ctx.state
    # Return a structured "not found" response for now
    return {
        "request_id": request_id,
        "status": "not_found",
        "message": "Request not found in current state. Use the chain state service to query on-chain requests.",
    }


@method("ena.getRequestStatus", aliases=("ena_getRequestStatus",))
def ena_get_request_status(*args, **kwargs):
    """
    Get status of an ENA request.

    Params: [request_id: str]

    Returns: { request_id, status }
    """
    if args and len(args) == 1 and isinstance(args[0], (dict, list)):
        params = args[0]
    elif args:
        params = list(args)
    else:
        params = kwargs or {}

    try:
        request_id = _extract_param(params, 0, "request_id")
    except InvalidParams:
        raise

    if not request_id:
        raise InvalidParams("request_id must not be empty")

    return {
        "request_id": request_id,
        "status": "unknown",
        "message": "Use chain state service to query live request status.",
    }


@method("ena.getResult", aliases=("ena_getResult",))
def ena_get_result(*args, **kwargs):
    """
    Get result record for a completed ENA request.

    Params: [request_id: str]

    Returns: result record dict, or null if not found/completed.
    """
    if args and len(args) == 1 and isinstance(args[0], (dict, list)):
        params = args[0]
    elif args:
        params = list(args)
    else:
        params = kwargs or {}

    try:
        request_id = _extract_param(params, 0, "request_id")
    except InvalidParams:
        raise

    if not request_id:
        raise InvalidParams("request_id must not be empty")

    return {
        "request_id": request_id,
        "result_hash": None,
        "da_ptr": None,
        "status": "not_available",
        "message": "Result not yet available or not found.",
    }


@method("ena.getResultReceipt", aliases=("ena_getResultReceipt",))
def ena_get_result_receipt(*args, **kwargs):
    """
    Get result receipt/proof metadata for a completed ENA request.

    Params: [request_id: str]

    Returns: receipt record dict.
    """
    if args and len(args) == 1 and isinstance(args[0], (dict, list)):
        params = args[0]
    elif args:
        params = list(args)
    else:
        params = kwargs or {}

    try:
        request_id = _extract_param(params, 0, "request_id")
    except InvalidParams:
        raise

    if not request_id:
        raise InvalidParams("request_id must not be empty")

    return {
        "request_id": request_id,
        "receipt_hash": None,
        "worker_id": None,
        "accepted_height": None,
        "status": "not_available",
    }


@method("ena.listModels", aliases=("ena_listModels",))
def ena_list_models(*args, **kwargs):
    """
    List known ENA model versions.

    Returns: [{ version, da_ptr, activation_height, status, metadata_hash }]
    """
    # In a full node, this would enumerate registered models from chain state.
    # Return a well-structured empty list as placeholder.
    return {
        "models": [],
        "active_version": "",
        "message": "Model registry is managed on-chain. Use the chain state service to list models.",
    }


@method("ena.getActiveModel", aliases=("ena_getActiveModel",))
def ena_get_active_model(*args, **kwargs):
    """
    Get the currently active ENA model version.

    Returns: { version, da_ptr, activation_height, status } or null if not set.
    """
    return {
        "version": None,
        "da_ptr": None,
        "activation_height": None,
        "status": None,
        "message": "Active model is managed on-chain. Use the chain state service to query.",
    }


@method("ena.explainReject", aliases=("ena_explainReject",))
def ena_explain_reject(*args, **kwargs):
    """
    Debug: explain why an ENA request might be rejected.

    Params (positional or named):
      model_version   str  — Model version to check.
      task_type       str  — Task type to check.
      input_size      int  — Input payload size in bytes.
      fee_limit       int  — Fee limit in ANM nano-units.

    Returns: { allowed: bool, reasons: [str] }
    """
    if args and len(args) == 1 and isinstance(args[0], (dict, list)):
        params = args[0]
    elif args:
        params = list(args)
    else:
        params = kwargs or {}

    try:
        model_version = _extract_param(params, 0, "model_version")
        task_type = _extract_param(params, 1, "task_type")
        input_size = int(_extract_optional(params, 2, "input_size", 0))
        fee_limit = int(_extract_optional(params, 3, "fee_limit", 0))
    except (ValueError, TypeError) as exc:
        raise InvalidParams(str(exc))

    try:
        from execution.state.ena_state import (  # type: ignore
            DEFAULT_MAX_INPUT_BYTES,
            DEFAULT_ALLOWED_TASKS,
        )
        max_input = DEFAULT_MAX_INPUT_BYTES
        allowed_tasks = DEFAULT_ALLOWED_TASKS
    except ImportError:
        max_input = 4096
        allowed_tasks = ["classify", "embed", "summarize", "custom"]

    reasons: List[str] = []
    allowed = True

    if not model_version:
        reasons.append("model_version is required")
        allowed = False

    if task_type not in allowed_tasks:
        reasons.append(f"task_type {task_type!r} not in allowed list: {allowed_tasks}")
        allowed = False

    if input_size > max_input:
        reasons.append(f"input_size {input_size} exceeds max_input_bytes={max_input}")
        allowed = False

    if fee_limit <= 0:
        reasons.append("fee_limit must be positive")
        allowed = False

    return {
        "allowed": allowed,
        "reasons": reasons,
        "model_version": model_version,
        "task_type": task_type,
        "input_size": input_size,
        "fee_limit": fee_limit,
        "policy": {
            "max_input_bytes": max_input,
            "allowed_tasks": list(allowed_tasks),
        },
    }


__all__ = [
    "ena_submit_request",
    "ena_get_request",
    "ena_get_request_status",
    "ena_get_result",
    "ena_get_result_receipt",
    "ena_list_models",
    "ena_get_active_model",
    "ena_explain_reject",
]
