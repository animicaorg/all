"""
execution.state.ena_state — ENA (Embedded Neural Agent) on-chain state
=======================================================================

Manages deterministic on-chain state for ENA inference requests and results.

Design: asynchronous oracle / receipt-based pattern.
- Contracts submit a request (async), get a request_id.
- Off-chain workers fetch queued requests, run inference, and submit results.
- Workers attach a receipt/proof hash; chain finalises the result.
- Contracts then read the result hash or DA pointer deterministically.

HARD REQUIREMENTS:
- No filesystem dependency; all state in chain state DB.
- Deterministic, consensus-safe.
- No nondeterministic AI inference inside validator execution.
- Overflow-safe integer arithmetic.
- Reorg-safe (all state keyed by request_id / height).
- Canonical serialization (sorted keys, no BigInt surprises).

State Keys Schema:
  ena.req.{request_id}.creator     bytes32 creator address
  ena.req.{request_id}.contract    bytes32 contract address (or empty)
  ena.req.{request_id}.model       str     model version id
  ena.req.{request_id}.task_type   str     task type (classify, embed, …)
  ena.req.{request_id}.input_hash  hex     SHA3-256 of raw input payload
  ena.req.{request_id}.fee_locked  int     ANM nano-units locked
  ena.req.{request_id}.status      str     queued|running|completed|failed|expired
  ena.req.{request_id}.created     int     block height
  ena.req.{request_id}.expiry      int     block height at which the request expires
  ena.req.{request_id}.callback    str     optional contract method name for callback
  ena.req.{request_id}.da_ptr      str     optional DA commitment for large inputs

  ena.result.{request_id}.worker   str     worker / provider id
  ena.result.{request_id}.model    str     model version id used
  ena.result.{request_id}.hash     hex     SHA3-256 of result payload
  ena.result.{request_id}.da_ptr   str     DA commitment for output (if large)
  ena.result.{request_id}.receipt  hex     proof/receipt hash
  ena.result.{request_id}.height   int     block height when accepted

  ena.fee.{request_id}.provider    int     provider payout (nano-units)
  ena.fee.{request_id}.aicf        int     AICF pool allocation (nano-units)
  ena.fee.{request_id}.treasury    int     treasury allocation (nano-units)
  ena.fee.{request_id}.refund      int     refund to creator (nano-units)

  ena.policy.max_input_bytes       int     policy: max raw input size
  ena.policy.max_output_bytes      int     policy: max inline output size
  ena.policy.expiry_blocks         int     policy: default request expiry window
  ena.policy.allowed_tasks         str     json list of allowed task types
  ena.policy.enabled               int     1=enabled, 0=disabled

  ena.model.active                 str     active model version id
  ena.model.{version}.da_ptr       str     DA commitment for model weights
  ena.model.{version}.height       int     activation block height
  ena.model.{version}.status       str     active|deprecated|experimental
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("execution.state.ena_state")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_BALANCE = (2**256) - 1

# Default policy values (can be overridden via governance)
DEFAULT_MAX_INPUT_BYTES = 4096
DEFAULT_MAX_OUTPUT_BYTES = 8192
DEFAULT_EXPIRY_BLOCKS = 1440  # ~4 hours at 10 second blocks
DEFAULT_ALLOWED_TASKS = ["classify", "embed", "summarize", "custom"]
DEFAULT_EXPIRY_HEIGHT_OFFSET = DEFAULT_EXPIRY_BLOCKS

# Fee split defaults (basis points; sum must be ≤ 10000)
DEFAULT_PROVIDER_BPS = 6000   # 60% to inference worker
DEFAULT_AICF_BPS = 3000       # 30% to AICF pool
DEFAULT_TREASURY_BPS = 1000   # 10% to treasury

# Request status constants
STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_EXPIRED = "expired"

# State key templates
_KEY_REQ = "ena.req.{req_id}.{field}"
_KEY_RESULT = "ena.result.{req_id}.{field}"
_KEY_FEE = "ena.fee.{req_id}.{field}"
_KEY_POLICY = "ena.policy.{field}"
_KEY_MODEL = "ena.model.{field}"
_KEY_MODEL_VER = "ena.model.{version}.{field}"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class ENARequest:
    """On-chain ENA inference request."""

    request_id: str
    creator: bytes
    contract_address: bytes
    model_version: str
    task_type: str
    input_hash: str       # hex-encoded SHA3-256 of raw input
    fee_locked: int       # locked ANM nano-units
    status: str
    created_height: int
    expiry_height: int
    callback: str = ""    # optional contract method name
    da_ptr: str = ""      # optional DA commitment for large inputs


@dataclass
class ENAResult:
    """On-chain ENA result record."""

    request_id: str
    worker_id: str
    model_version: str
    result_hash: str      # hex-encoded SHA3-256 of result payload
    da_ptr: str = ""      # DA commitment for large outputs
    receipt_hash: str = "" # proof/receipt hash
    accepted_height: int = 0


@dataclass
class ENAFeeSplit:
    """Fee split record for an ENA request."""

    request_id: str
    provider_amount: int = 0
    aicf_amount: int = 0
    treasury_amount: int = 0
    refund_amount: int = 0


@dataclass
class ENAModelVersion:
    """On-chain ENA model version record."""

    version: str
    da_ptr: str            # DA commitment hash
    activation_height: int
    status: str = "active"  # active|deprecated|experimental
    metadata_hash: str = "" # optional metadata DA pointer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sk_req(req_id: str, f: str) -> str:
    return _KEY_REQ.format(req_id=req_id, field=f)


def _sk_result(req_id: str, f: str) -> str:
    return _KEY_RESULT.format(req_id=req_id, field=f)


def _sk_fee(req_id: str, f: str) -> str:
    return _KEY_FEE.format(req_id=req_id, field=f)


def _sk_policy(f: str) -> str:
    return _KEY_POLICY.format(field=f)


def _sk_model(f: str) -> str:
    return _KEY_MODEL.format(field=f)


def _sk_model_ver(version: str, f: str) -> str:
    return _KEY_MODEL_VER.format(version=version, field=f)


def _state_get_int(state: Any, key: str, default: int = 0) -> int:
    raw = state.get(key, None)
    if raw is None:
        return default
    if isinstance(raw, int):
        return raw
    try:
        return int(raw)
    except (ValueError, TypeError):
        return default


def _state_get_str(state: Any, key: str, default: str = "") -> str:
    raw = state.get(key, None)
    if raw is None:
        return default
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return str(raw)


def _state_get_bytes(state: Any, key: str, default: bytes = b"") -> bytes:
    raw = state.get(key, None)
    if raw is None:
        return default
    if isinstance(raw, bytes):
        return raw
    if isinstance(raw, str):
        return raw.encode("utf-8")
    return default


def safe_add(a: int, b: int) -> int:
    result = a + b
    if result > MAX_BALANCE:
        raise OverflowError(f"Balance overflow: {a} + {b}")
    return result


def safe_sub(a: int, b: int) -> int:
    if b > a:
        raise ValueError(f"Insufficient balance: {a} < {b}")
    return a - b


def _compute_fee_split(
    fee_locked: int,
    provider_bps: int = DEFAULT_PROVIDER_BPS,
    aicf_bps: int = DEFAULT_AICF_BPS,
    treasury_bps: int = DEFAULT_TREASURY_BPS,
) -> Tuple[int, int, int, int]:
    """
    Compute fee split amounts from locked fee.

    Returns (provider, aicf, treasury, refund).
    Refund is any remainder not distributed.
    """
    total_bps = provider_bps + aicf_bps + treasury_bps
    if total_bps > 10000:
        raise ValueError(f"Fee split BPS sum exceeds 10000: {total_bps}")

    provider = fee_locked * provider_bps // 10000
    aicf = fee_locked * aicf_bps // 10000
    treasury = fee_locked * treasury_bps // 10000
    refund = fee_locked - provider - aicf - treasury
    return provider, aicf, treasury, refund


def _compute_refund_split(
    fee_locked: int,
) -> Tuple[int, int, int, int]:
    """
    On failure/expiry: return fee to creator minus a small slashing fee for AICF.
    """
    slash_bps = 100  # 1% slashing fee to AICF for failed/expired requests
    aicf = fee_locked * slash_bps // 10000
    refund = fee_locked - aicf
    return 0, aicf, 0, refund


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
    """
    Derive a deterministic request ID from its key parameters.

    Canonical form: SHA3-256 of sorted-key JSON → hex string.
    """
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
# Policy helpers
# ---------------------------------------------------------------------------


def get_ena_enabled(state: Any) -> bool:
    return _state_get_int(state, _sk_policy("enabled"), 1) != 0


def set_ena_enabled(state: Any, enabled: bool) -> None:
    state.put(_sk_policy("enabled"), 1 if enabled else 0)


def get_max_input_bytes(state: Any) -> int:
    return _state_get_int(state, _sk_policy("max_input_bytes"), DEFAULT_MAX_INPUT_BYTES)


def set_max_input_bytes(state: Any, limit: int) -> None:
    state.put(_sk_policy("max_input_bytes"), limit)


def get_max_output_bytes(state: Any) -> int:
    return _state_get_int(state, _sk_policy("max_output_bytes"), DEFAULT_MAX_OUTPUT_BYTES)


def set_max_output_bytes(state: Any, limit: int) -> None:
    state.put(_sk_policy("max_output_bytes"), limit)


def get_expiry_blocks(state: Any) -> int:
    return _state_get_int(state, _sk_policy("expiry_blocks"), DEFAULT_EXPIRY_BLOCKS)


def set_expiry_blocks(state: Any, blocks: int) -> None:
    state.put(_sk_policy("expiry_blocks"), blocks)


def get_allowed_tasks(state: Any) -> List[str]:
    raw = _state_get_str(state, _sk_policy("allowed_tasks"), "")
    if not raw:
        return list(DEFAULT_ALLOWED_TASKS)
    try:
        tasks = json.loads(raw)
        if isinstance(tasks, list):
            return [str(t) for t in tasks]
    except (json.JSONDecodeError, TypeError):
        pass
    return list(DEFAULT_ALLOWED_TASKS)


def set_allowed_tasks(state: Any, tasks: List[str]) -> None:
    state.put(_sk_policy("allowed_tasks"), json.dumps(tasks, sort_keys=True))


# ---------------------------------------------------------------------------
# Model version registry
# ---------------------------------------------------------------------------


def get_active_model(state: Any) -> str:
    return _state_get_str(state, _sk_model("active"), "")


def set_active_model(state: Any, version: str) -> None:
    state.put(_sk_model("active"), version)


def register_model_version(
    state: Any,
    version: str,
    da_ptr: str,
    activation_height: int,
    status: str = "active",
    metadata_hash: str = "",
) -> None:
    """Register a new ENA model version in the on-chain registry."""
    state.put(_sk_model_ver(version, "da_ptr"), da_ptr)
    state.put(_sk_model_ver(version, "height"), activation_height)
    state.put(_sk_model_ver(version, "status"), status)
    if metadata_hash:
        state.put(_sk_model_ver(version, "metadata_hash"), metadata_hash)
    log.info("Registered ENA model version %s at height %d (status=%s)", version, activation_height, status)


def get_model_version(state: Any, version: str) -> Optional[ENAModelVersion]:
    """Get model version record; returns None if not found."""
    # Use status as the existence sentinel (always stored by register_model_version)
    status = _state_get_str(state, _sk_model_ver(version, "status"), "")
    if not status:
        return None
    da_ptr = _state_get_str(state, _sk_model_ver(version, "da_ptr"), "")
    height = _state_get_int(state, _sk_model_ver(version, "height"), 0)
    metadata_hash = _state_get_str(state, _sk_model_ver(version, "metadata_hash"), "")
    return ENAModelVersion(
        version=version,
        da_ptr=da_ptr,
        activation_height=height,
        status=status,
        metadata_hash=metadata_hash,
    )


def is_model_allowed(state: Any, version: str) -> bool:
    """Check if a model version is allowed for on-chain requests."""
    model = get_model_version(state, version)
    if model is None:
        return False
    return model.status in ("active",)


# ---------------------------------------------------------------------------
# Request lifecycle
# ---------------------------------------------------------------------------


def create_request(
    state: Any,
    creator: bytes,
    contract_address: bytes,
    model_version: str,
    task_type: str,
    input_payload: bytes,
    fee_locked: int,
    current_height: int,
    callback: str = "",
    da_ptr: str = "",
    nonce: int = 0,
) -> Tuple[str, ENARequest]:
    """
    Create a new ENA inference request.

    Validates policy constraints, derives a deterministic request_id,
    stores request state, and returns (request_id, ENARequest).

    Raises:
        ValueError: if policy checks fail.
    """
    if not get_ena_enabled(state):
        raise ValueError("ENA requests are currently disabled by policy")

    # Policy: model version must be registered and active
    if not is_model_allowed(state, model_version):
        raise ValueError(f"Model version not allowed for on-chain requests: {model_version!r}")

    # Policy: task type must be allowed
    allowed_tasks = get_allowed_tasks(state)
    if task_type not in allowed_tasks:
        raise ValueError(f"Task type not allowed: {task_type!r} (allowed: {allowed_tasks})")

    # Policy: input size
    max_input = get_max_input_bytes(state)
    if len(input_payload) > max_input:
        raise ValueError(f"Input payload too large: {len(input_payload)} > {max_input}")

    # Policy: fee must be positive
    if fee_locked <= 0:
        raise ValueError("fee_locked must be positive")

    # Derive deterministic request ID
    input_hash = _sha3_hex(input_payload)
    expiry_blocks = get_expiry_blocks(state)
    expiry_height = current_height + expiry_blocks

    request_id = _derive_request_id(
        creator=creator,
        model_version=model_version,
        task_type=task_type,
        input_hash=input_hash,
        height=current_height,
        nonce=nonce,
    )

    req = ENARequest(
        request_id=request_id,
        creator=creator,
        contract_address=contract_address,
        model_version=model_version,
        task_type=task_type,
        input_hash=input_hash,
        fee_locked=fee_locked,
        status=STATUS_QUEUED,
        created_height=current_height,
        expiry_height=expiry_height,
        callback=callback,
        da_ptr=da_ptr,
    )

    # Persist to state
    _store_request(state, req)

    log.info(
        "ENA request created: %s (model=%s, task=%s, height=%d)",
        request_id, model_version, task_type, current_height,
    )
    return request_id, req


def _store_request(state: Any, req: ENARequest) -> None:
    """Persist an ENARequest to state."""
    state.put(_sk_req(req.request_id, "creator"), req.creator)
    state.put(_sk_req(req.request_id, "contract"), req.contract_address)
    state.put(_sk_req(req.request_id, "model"), req.model_version)
    state.put(_sk_req(req.request_id, "task_type"), req.task_type)
    state.put(_sk_req(req.request_id, "input_hash"), req.input_hash)
    state.put(_sk_req(req.request_id, "fee_locked"), req.fee_locked)
    state.put(_sk_req(req.request_id, "status"), req.status)
    state.put(_sk_req(req.request_id, "created"), req.created_height)
    state.put(_sk_req(req.request_id, "expiry"), req.expiry_height)
    state.put(_sk_req(req.request_id, "callback"), req.callback)
    state.put(_sk_req(req.request_id, "da_ptr"), req.da_ptr)


def get_request(state: Any, request_id: str) -> Optional[ENARequest]:
    """Load an ENARequest from state; returns None if not found."""
    status = _state_get_str(state, _sk_req(request_id, "status"), "")
    if not status:
        return None
    creator = _state_get_bytes(state, _sk_req(request_id, "creator"))
    contract = _state_get_bytes(state, _sk_req(request_id, "contract"))
    model = _state_get_str(state, _sk_req(request_id, "model"))
    task_type = _state_get_str(state, _sk_req(request_id, "task_type"))
    input_hash = _state_get_str(state, _sk_req(request_id, "input_hash"))
    fee_locked = _state_get_int(state, _sk_req(request_id, "fee_locked"))
    created = _state_get_int(state, _sk_req(request_id, "created"))
    expiry = _state_get_int(state, _sk_req(request_id, "expiry"))
    callback = _state_get_str(state, _sk_req(request_id, "callback"))
    da_ptr = _state_get_str(state, _sk_req(request_id, "da_ptr"))
    return ENARequest(
        request_id=request_id,
        creator=creator,
        contract_address=contract,
        model_version=model,
        task_type=task_type,
        input_hash=input_hash,
        fee_locked=fee_locked,
        status=status,
        created_height=created,
        expiry_height=expiry,
        callback=callback,
        da_ptr=da_ptr,
    )


def get_request_status(state: Any, request_id: str) -> str:
    """Return status string for a request, or empty string if not found."""
    return _state_get_str(state, _sk_req(request_id, "status"), "")


def set_request_status(state: Any, request_id: str, status: str) -> None:
    """Update status of an existing request."""
    valid_statuses = {STATUS_QUEUED, STATUS_RUNNING, STATUS_COMPLETED, STATUS_FAILED, STATUS_EXPIRED}
    if status not in valid_statuses:
        raise ValueError(f"Invalid status: {status!r}")
    state.put(_sk_req(request_id, "status"), status)


def submit_result(
    state: Any,
    request_id: str,
    worker_id: str,
    result_payload: bytes,
    receipt_hash: str,
    current_height: int,
    da_ptr: str = "",
) -> ENAResult:
    """
    Submit an inference result for a queued/running request.

    Validates that:
    - The request exists and is in a submittable state.
    - The result payload does not exceed max_output_bytes (if inline).
    - A receipt/proof hash is provided.

    Returns the stored ENAResult.
    Raises:
        ValueError: on policy/state violations.
    """
    req = get_request(state, request_id)
    if req is None:
        raise ValueError(f"ENA request not found: {request_id!r}")

    if req.status not in (STATUS_QUEUED, STATUS_RUNNING):
        raise ValueError(
            f"Cannot submit result for request in status {req.status!r}: {request_id!r}"
        )

    # Check not expired
    if req.expiry_height > 0 and current_height > req.expiry_height:
        # Expire it
        expire_request(state, request_id, current_height)
        raise ValueError(f"ENA request has expired: {request_id!r}")

    # Check output size
    if not da_ptr:
        max_output = get_max_output_bytes(state)
        if len(result_payload) > max_output:
            raise ValueError(
                f"Result payload too large for inline storage: {len(result_payload)} > {max_output}. "
                "Use da_ptr for large outputs."
            )

    result_hash = _sha3_hex(result_payload)

    result = ENAResult(
        request_id=request_id,
        worker_id=worker_id,
        model_version=req.model_version,
        result_hash=result_hash,
        da_ptr=da_ptr,
        receipt_hash=receipt_hash,
        accepted_height=current_height,
    )

    # Persist result
    _store_result(state, result)

    # Update request status
    set_request_status(state, request_id, STATUS_COMPLETED)

    log.info(
        "ENA result submitted: %s (worker=%s, height=%d)",
        request_id, worker_id, current_height,
    )
    return result


def _store_result(state: Any, result: ENAResult) -> None:
    """Persist an ENAResult to state."""
    state.put(_sk_result(result.request_id, "worker"), result.worker_id)
    state.put(_sk_result(result.request_id, "model"), result.model_version)
    state.put(_sk_result(result.request_id, "hash"), result.result_hash)
    state.put(_sk_result(result.request_id, "da_ptr"), result.da_ptr)
    state.put(_sk_result(result.request_id, "receipt"), result.receipt_hash)
    state.put(_sk_result(result.request_id, "height"), result.accepted_height)


def get_result(state: Any, request_id: str) -> Optional[ENAResult]:
    """Load an ENAResult from state; returns None if not found."""
    result_hash = _state_get_str(state, _sk_result(request_id, "hash"), "")
    if not result_hash:
        return None
    worker = _state_get_str(state, _sk_result(request_id, "worker"))
    model = _state_get_str(state, _sk_result(request_id, "model"))
    da_ptr = _state_get_str(state, _sk_result(request_id, "da_ptr"))
    receipt = _state_get_str(state, _sk_result(request_id, "receipt"))
    height = _state_get_int(state, _sk_result(request_id, "height"))
    return ENAResult(
        request_id=request_id,
        worker_id=worker,
        model_version=model,
        result_hash=result_hash,
        da_ptr=da_ptr,
        receipt_hash=receipt,
        accepted_height=height,
    )


def get_result_hash(state: Any, request_id: str) -> str:
    """Return only the result hash for a completed request, or empty string."""
    return _state_get_str(state, _sk_result(request_id, "hash"), "")


def finalize_fee_split(
    state: Any,
    request_id: str,
    provider_bps: int = DEFAULT_PROVIDER_BPS,
    aicf_bps: int = DEFAULT_AICF_BPS,
    treasury_bps: int = DEFAULT_TREASURY_BPS,
) -> ENAFeeSplit:
    """
    Compute and store the fee split for a completed request.

    Typically called after submit_result to distribute the locked fee.
    Returns the ENAFeeSplit record.
    """
    req = get_request(state, request_id)
    if req is None:
        raise ValueError(f"ENA request not found: {request_id!r}")

    provider, aicf, treasury, refund = _compute_fee_split(
        req.fee_locked, provider_bps, aicf_bps, treasury_bps
    )

    split = ENAFeeSplit(
        request_id=request_id,
        provider_amount=provider,
        aicf_amount=aicf,
        treasury_amount=treasury,
        refund_amount=refund,
    )
    _store_fee_split(state, split)
    return split


def _store_fee_split(state: Any, split: ENAFeeSplit) -> None:
    state.put(_sk_fee(split.request_id, "provider"), split.provider_amount)
    state.put(_sk_fee(split.request_id, "aicf"), split.aicf_amount)
    state.put(_sk_fee(split.request_id, "treasury"), split.treasury_amount)
    state.put(_sk_fee(split.request_id, "refund"), split.refund_amount)


def get_fee_split(state: Any, request_id: str) -> Optional[ENAFeeSplit]:
    """Load fee split record; returns None if not found."""
    provider = _state_get_int(state, _sk_fee(request_id, "provider"), -1)
    if provider == -1:
        return None
    aicf = _state_get_int(state, _sk_fee(request_id, "aicf"))
    treasury = _state_get_int(state, _sk_fee(request_id, "treasury"))
    refund = _state_get_int(state, _sk_fee(request_id, "refund"))
    return ENAFeeSplit(
        request_id=request_id,
        provider_amount=provider,
        aicf_amount=aicf,
        treasury_amount=treasury,
        refund_amount=refund,
    )


def fail_request(
    state: Any,
    request_id: str,
    current_height: int,
) -> ENAFeeSplit:
    """
    Mark a request as failed and compute refund split.

    On failure: creator gets fee back minus a small AICF slash.
    """
    req = get_request(state, request_id)
    if req is None:
        raise ValueError(f"ENA request not found: {request_id!r}")
    if req.status not in (STATUS_QUEUED, STATUS_RUNNING):
        raise ValueError(f"Cannot fail request in status {req.status!r}")

    set_request_status(state, request_id, STATUS_FAILED)

    _, aicf, _, refund = _compute_refund_split(req.fee_locked)
    split = ENAFeeSplit(
        request_id=request_id,
        provider_amount=0,
        aicf_amount=aicf,
        treasury_amount=0,
        refund_amount=refund,
    )
    _store_fee_split(state, split)
    log.info("ENA request failed: %s at height=%d", request_id, current_height)
    return split


def expire_request(
    state: Any,
    request_id: str,
    current_height: int,
) -> ENAFeeSplit:
    """
    Mark a request as expired and compute refund split.

    On expiry: creator gets fee back minus a small AICF slash.
    """
    req = get_request(state, request_id)
    if req is None:
        raise ValueError(f"ENA request not found: {request_id!r}")
    if req.status not in (STATUS_QUEUED, STATUS_RUNNING):
        raise ValueError(f"Cannot expire request in status {req.status!r}")

    set_request_status(state, request_id, STATUS_EXPIRED)

    _, aicf, _, refund = _compute_refund_split(req.fee_locked)
    split = ENAFeeSplit(
        request_id=request_id,
        provider_amount=0,
        aicf_amount=aicf,
        treasury_amount=0,
        refund_amount=refund,
    )
    _store_fee_split(state, split)
    log.info("ENA request expired: %s at height=%d", request_id, current_height)
    return split


def verify_receipt(
    request_id: str,
    result_hash: str,
    receipt_hash: str,
    worker_id: str,
) -> Tuple[bool, str]:
    """
    Deterministically validate a worker receipt format.

    This is a structural/format check only. Full cryptographic proof
    verification is handled by the proof subsystem (see proofs/).

    Returns:
        (is_valid, reason) tuple.
    """
    # TODO: Integrate with full proof/attestation system when available.
    # For now, perform structural validation only.

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
    # Data structures
    "ENARequest",
    "ENAResult",
    "ENAFeeSplit",
    "ENAModelVersion",
    # Status constants
    "STATUS_QUEUED",
    "STATUS_RUNNING",
    "STATUS_COMPLETED",
    "STATUS_FAILED",
    "STATUS_EXPIRED",
    # Policy
    "get_ena_enabled",
    "set_ena_enabled",
    "get_max_input_bytes",
    "set_max_input_bytes",
    "get_max_output_bytes",
    "set_max_output_bytes",
    "get_expiry_blocks",
    "set_expiry_blocks",
    "get_allowed_tasks",
    "set_allowed_tasks",
    # Model registry
    "get_active_model",
    "set_active_model",
    "register_model_version",
    "get_model_version",
    "is_model_allowed",
    # Request lifecycle
    "create_request",
    "get_request",
    "get_request_status",
    "set_request_status",
    "submit_result",
    "get_result",
    "get_result_hash",
    "finalize_fee_split",
    "get_fee_split",
    "fail_request",
    "expire_request",
    "verify_receipt",
    # Helpers (public)
    "_derive_request_id",
    "_compute_fee_split",
    "_sha3_hex",
    "safe_add",
    "safe_sub",
    # Defaults
    "DEFAULT_MAX_INPUT_BYTES",
    "DEFAULT_MAX_OUTPUT_BYTES",
    "DEFAULT_EXPIRY_BLOCKS",
    "DEFAULT_ALLOWED_TASKS",
    "DEFAULT_PROVIDER_BPS",
    "DEFAULT_AICF_BPS",
    "DEFAULT_TREASURY_BPS",
]
