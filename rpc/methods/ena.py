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
    """The state DB, or None.

    The `ctx` injected into a handler is ``rpc.jsonrpc.Context`` — a per-request
    TRANSPORT object (request/received_at_ms/client/headers). It has never
    carried a database handle. Those live on ``rpc.deps.RpcContext`` and are
    reached through ``deps.get_ctx()``, which is what every working namespace
    does (see rpc/methods/state.py). Reading state off the transport ctx is why
    this returned None on every deployed node. The passed ctx stays as a
    fallback for embeddings/tests that supply a real RpcContext.
    """
    try:
        from rpc import deps

        state = getattr(deps.get_ctx(), "state_db", None)
        if state is not None:
            return state
    except Exception:  # context not initialised (tests, tooling) — fall through
        pass
    if ctx is None:
        return None
    return getattr(ctx, "state_db", None) or getattr(ctx, "state", None)


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


##############################################################################
# ENA Artifact Integration Methods
##############################################################################


def _get_da_store():
    """Return the local DA node store, or None if unavailable."""
    try:
        from da.node_store import get_store  # type: ignore
        import os

        base = os.getenv("ANIMICA_DATA_DIR") or os.path.expanduser("~/.animica")
        chain_id = os.getenv("ANIMICA_CHAIN_ID", "1")
        da_dir = os.path.join(base, f"chain-{chain_id}", "da")
        return get_store(da_dir)
    except Exception:
        return None


def _get_aicf_state():
    """Return AICF protocol state, or None if unavailable."""
    try:
        from aicf.protocol.state import AICFProtocolState  # type: ignore
        import os

        base = os.getenv("ANIMICA_DATA_DIR") or os.path.expanduser("~/.animica")
        chain_id = os.getenv("ANIMICA_CHAIN_ID", "1")
        db_path = os.path.join(base, f"chain-{chain_id}", "aicf_credits.db")
        return AICFProtocolState(db_path)
    except Exception:
        return None


# In-memory pending artifact records (manifest_blob_id → metadata dict)
_PENDING_ARTIFACTS: Dict[str, Dict] = {}


@method(
    "ena.submitArtifact",
    aliases=("ena_submitArtifact", "ena.submit_artifact"),
    desc="Submit an ENA artifact manifest blob for credit processing",
)
def ena_submit_artifact(params=None, *_args, **_kwargs) -> Dict[str, Any]:
    """
    ena.submitArtifact(manifest_blob_id, job_metadata?) → {ok, manifest_blob_id, status}

    Registers a manifest blob (already stored in DA) as a pending artifact.
    Call ena.verifyArtifact afterwards to trigger credit award.

    Params (dict or positional):
      manifest_blob_id : str  — hex blob_id of the manifest in DA
      job_metadata     : dict — optional extra metadata stored alongside
    """
    import hashlib

    if isinstance(params, (list, tuple)):
        manifest_blob_id = _extract_param(params, 0, "manifest_blob_id")
        job_metadata: Optional[Dict] = params[1] if len(params) > 1 else None
    elif isinstance(params, dict):
        manifest_blob_id = params.get("manifest_blob_id")
        if not manifest_blob_id:
            raise InvalidParams("Missing required parameter: 'manifest_blob_id'")
        job_metadata = params.get("job_metadata")
    else:
        raise InvalidParams("params must be a dict or list")

    if not isinstance(manifest_blob_id, str) or not manifest_blob_id.strip():
        raise InvalidParams("manifest_blob_id must be a non-empty string")

    manifest_blob_id = manifest_blob_id.strip().lower()

    # Validate format (64-char hex)
    try:
        bytes.fromhex(manifest_blob_id)
    except ValueError:
        raise InvalidParams(f"manifest_blob_id is not valid hex: {manifest_blob_id!r}")
    if len(manifest_blob_id) != 64:
        raise InvalidParams(f"manifest_blob_id must be 64 hex chars, got {len(manifest_blob_id)}")

    # Check DA availability
    store = _get_da_store()
    da_available = False
    if store is not None:
        try:
            da_available = store.has(manifest_blob_id)
        except Exception:
            pass

    import time

    record: Dict[str, Any] = {
        "manifest_blob_id": manifest_blob_id,
        "status": "pending",
        "da_available": da_available,
        "submitted_at": time.time(),
        "job_metadata": job_metadata or {},
    }
    _PENDING_ARTIFACTS[manifest_blob_id] = record

    return {
        "ok": True,
        "manifest_blob_id": manifest_blob_id,
        "status": "pending",
        "da_available": da_available,
    }


@method(
    "ena.verifyArtifact",
    aliases=("ena_verifyArtifact", "ena.verify_artifact"),
    desc="Verify an ENA artifact manifest and award AICF credits on success",
)
def ena_verify_artifact(params=None, *_args, **_kwargs) -> Dict[str, Any]:
    """
    ena.verifyArtifact(manifest_blob_id) → VerificationResult

    Steps:
      1. Pull manifest from DA (da.get).
      2. Parse and validate ArtifactManifest schema.
      3. Verify each referenced blob exists (da.has) and sha256 matches.
      4. If all checks pass, create an idempotent AICF credit event.

    Returns:
      {ok, manifest_blob_id, missing_blobs, errors, credit_event_id}
    """
    import hashlib
    import json
    import time

    if isinstance(params, (list, tuple)):
        manifest_blob_id = _extract_param(params, 0, "manifest_blob_id")
    elif isinstance(params, dict):
        manifest_blob_id = params.get("manifest_blob_id")
        if not manifest_blob_id:
            raise InvalidParams("Missing required parameter: 'manifest_blob_id'")
    elif isinstance(params, str):
        manifest_blob_id = params
    else:
        raise InvalidParams("params must be a dict, list, or string")

    if not isinstance(manifest_blob_id, str) or not manifest_blob_id.strip():
        raise InvalidParams("manifest_blob_id must be a non-empty string")

    manifest_blob_id = manifest_blob_id.strip().lower()

    try:
        bytes.fromhex(manifest_blob_id)
    except ValueError:
        raise InvalidParams(f"manifest_blob_id is not valid hex: {manifest_blob_id!r}")

    store = _get_da_store()
    if store is None:
        raise InternalError("DA store not available on this node")

    # Step 1: fetch manifest from DA
    try:
        manifest_bytes = store.get(manifest_blob_id)
    except Exception as exc:
        return {
            "ok": False,
            "manifest_blob_id": manifest_blob_id,
            "missing_blobs": [],
            "errors": [f"DA get failed: {exc}"],
            "credit_event_id": None,
        }

    if manifest_bytes is None:
        return {
            "ok": False,
            "manifest_blob_id": manifest_blob_id,
            "missing_blobs": [],
            "errors": ["manifest_blob_id not found in DA"],
            "credit_event_id": None,
        }

    # Step 2: parse manifest
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except Exception as exc:
        return {
            "ok": False,
            "manifest_blob_id": manifest_blob_id,
            "missing_blobs": [],
            "errors": [f"manifest parse error: {exc}"],
            "credit_event_id": None,
        }

    produced_files: List[Dict] = manifest.get("produced_files", [])
    if not isinstance(produced_files, list):
        produced_files = []

    # Step 3: verify each referenced blob
    missing_blobs: List[str] = []
    hash_errors: List[str] = []
    for entry in produced_files:
        blob_id = entry.get("blob_id", "")
        sha256_expected = entry.get("sha256", "")
        name = entry.get("name", "")
        if not blob_id:
            continue
        try:
            if not store.has(blob_id):
                missing_blobs.append(blob_id)
                continue
            # Verify sha256 if provided
            if sha256_expected:
                blob_bytes = store.get(blob_id)
                if blob_bytes is not None:
                    actual_sha256 = hashlib.sha256(blob_bytes).hexdigest()
                    if actual_sha256 != sha256_expected:
                        hash_errors.append(
                            f"{name} ({blob_id[:12]}…): sha256 mismatch"
                        )
        except Exception as exc:
            hash_errors.append(f"{name} ({blob_id[:12]}…): check error: {exc}")

    all_errors = hash_errors
    ok = not missing_blobs and not all_errors

    credit_event_id: Optional[str] = None
    if ok:
        # Step 4: create idempotent AICF credit event
        import os

        node_id = os.getenv("ANIMICA_NODE_ID", "local")
        idempotency_key = hashlib.sha3_256(
            (manifest_blob_id + node_id).encode()
        ).hexdigest()

        aicf = _get_aicf_state()
        if aicf is not None:
            try:
                event_payload = json.dumps(
                    {
                        "manifest_blob_id": manifest_blob_id,
                        "verifier": node_id,
                        "idempotency_key": idempotency_key,
                        "ts": time.time(),
                    },
                    sort_keys=True,
                ).encode()
                event_id = hashlib.sha3_256(event_payload).hexdigest()
                aicf.log_credit_event(
                    ledger_id=event_id,
                    event_type="artifact_verified",
                    block_height=0,
                    block_hash="0x" + "00" * 32,
                    amount="1000000",
                    metadata={
                        "manifest_blob_id": manifest_blob_id,
                        "idempotency_key": idempotency_key,
                        "job_id": manifest.get("job_id"),
                    },
                )
                credit_event_id = event_id
            except Exception:
                pass  # Credit logging is best-effort; don't fail verification

        # Mark artifact as verified
        if manifest_blob_id in _PENDING_ARTIFACTS:
            _PENDING_ARTIFACTS[manifest_blob_id]["status"] = "verified"
            _PENDING_ARTIFACTS[manifest_blob_id]["credit_event_id"] = credit_event_id

    return {
        "ok": ok,
        "manifest_blob_id": manifest_blob_id,
        "missing_blobs": missing_blobs,
        "errors": all_errors,
        "credit_event_id": credit_event_id,
        "manifest": {
            "job_id": manifest.get("job_id"),
            "model_id": manifest.get("model_id"),
            "file_count": len(produced_files),
        },
    }


@method(
    "ena.listArtifacts",
    aliases=("ena_listArtifacts", "ena.list_artifacts"),
    desc="List pending/verified artifact submissions",
)
def ena_list_artifacts(params=None, *_args, **_kwargs) -> Dict[str, Any]:
    """
    ena.listArtifacts([limit]) → {artifacts: [...]}

    Returns recently submitted artifact records (in-memory since node start).
    """
    limit = 20
    if isinstance(params, (list, tuple)) and params:
        try:
            limit = int(params[0])
        except (TypeError, ValueError):
            pass
    elif isinstance(params, dict):
        try:
            limit = int(params.get("limit", 20))
        except (TypeError, ValueError):
            pass

    items = list(_PENDING_ARTIFACTS.values())
    items.sort(key=lambda x: x.get("submitted_at", 0), reverse=True)
    return {"artifacts": items[:limit]}


__all__ = [
    "ena_submit_request",
    "ena_get_request",
    "ena_get_request_status",
    "ena_get_result",
    "ena_get_result_receipt",
    "ena_list_models",
    "ena_get_active_model",
    "ena_explain_reject",
    "ena_submit_artifact",
    "ena_verify_artifact",
    "ena_list_artifacts",
]
