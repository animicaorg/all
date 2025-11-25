"""
Determinism & input caps for capability syscalls.

This module centralizes defensive checks that keep contract-facing syscalls
deterministic and resource-bounded. It's intentionally dependency-light and
only relies on the Python stdlib.

Public API:
- enforce_limits(name, payloads, limits=None): validate size/shape by syscall
- sanitize_model_name(model: str, *, max_len: int = 64) -> str
- canonical_json_bytes(obj) -> bytes  (sorted keys, strict JSON, UTF-8)

`abi_bindings.build_stdlib_bindings` calls `enforce_limits(...)` before
delegating to the host provider.
"""

from __future__ import annotations

import json
import unicodedata
from typing import Any, Mapping, Optional

__all__ = [
    "enforce_limits",
    "sanitize_model_name",
    "canonical_json_bytes",
]

# -------- Defaults (can be overridden per-process via `limits`) --------

_DEFAULT_CAPS: dict[str, int] = {
    "max_prompt_bytes": 64 * 1024,       # AI prompts
    "max_circuit_bytes": 128 * 1024,     # Quantum circuits (JSON or bytes)
    "max_blob_bytes": 4 * 1024 * 1024,   # DA blob pin from contracts
    "max_read_bytes": 2 * 1024 * 1024,   # Generic read bounds (e.g., random())
    "max_zk_bytes": 512 * 1024,          # zk.verify proof / public input
    "max_task_id_bytes": 64,             # read_result task id max
    "max_model_len": 64,                 # model identifier (ai_enqueue)
}

# ASCII control ranges except tab/newline/carriage-return
# Allow: 0x09 (TAB), 0x0A (LF), 0x0D (CR)
_FORBIDDEN_CTRL = {*range(0x00, 0x09), *range(0x0B, 0x0C), *range(0x0E, 0x20)}


# -------- Helpers --------

def _caps(limits: Optional[Mapping[str, int]] | None) -> dict[str, int]:
    if not limits:
        return dict(_DEFAULT_CAPS)
    merged = dict(_DEFAULT_CAPS)
    for k, v in limits.items():
        if isinstance(v, int) and v > 0:
            merged[k] = v
    return merged


def _ensure_bytes(x: Any, *, label: str) -> bytes:
    if isinstance(x, (bytes, bytearray, memoryview)):
        return bytes(x)
    if isinstance(x, str):
        # only used where string → bytes policy is acceptable
        return x.encode("utf-8")
    raise TypeError(f"{label}: expected bytes/str, got {type(x).__name__}")


def _reject_ctrl_bytes(b: bytes, *, label: str) -> None:
    for ch in b:
        if ch in _FORBIDDEN_CTRL:
            raise ValueError(f"{label}: contains forbidden control byte 0x{ch:02x}")


def _bounded(b: bytes, *, max_len: int, label: str) -> None:
    if len(b) > max_len:
        raise ValueError(f"{label}: {len(b)} bytes exceeds limit {max_len}")


def canonical_json_bytes(obj: Any) -> bytes:
    """
    Deterministic JSON → UTF-8 bytes:
      - sort_keys=True
      - no whitespace (compact separators)
      - allow_nan=False (reject NaN/Inf)
      - ensure_ascii=False (UTF-8)
    """
    try:
        s = json.dumps(
            obj,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
            ensure_ascii=False,
        )
    except (TypeError, ValueError) as e:
        raise ValueError(f"non-serializable/invalid JSON payload: {e}") from e
    return s.encode("utf-8")


def _check_json_payload(obj: Any, *, max_len: int, label: str) -> None:
    jb = canonical_json_bytes(obj)
    _bounded(jb, max_len=max_len, label=label)
    # UTF-8 is by construction; still reject ASCII control chars present as literals.
    _reject_ctrl_bytes(jb, label=label)


def sanitize_model_name(model: Any, *, max_len: int = _DEFAULT_CAPS["max_model_len"]) -> str:
    """
    Normalize model identifiers to NFC, trim surrounding whitespace, and
    reject control characters & overly long names. Returns the sanitized value.
    """
    if not isinstance(model, str):
        model = str(model)
    s = unicodedata.normalize("NFC", model).strip()
    if not s:
        raise ValueError("model name must be non-empty")
    if len(s.encode("utf-8")) > max_len:
        raise ValueError(f"model name too long (> {max_len} bytes UTF-8)")
    for ch in s:
        if ord(ch) in _FORBIDDEN_CTRL:
            raise ValueError("model name contains control characters")
    return s


# -------- Main entrypoint used by abi_bindings --------

def enforce_limits(
    *,
    name: str,
    payloads: Mapping[str, Any],
    limits: Optional[Mapping[str, int]] = None,
) -> None:
    """
    Validate syscall inputs for determinism & length caps.
    This function is PURE (no mutation) and raises on violation.

    Args:
        name: syscall logical name (blob_pin|ai_enqueue|quantum_enqueue|zk_verify|read_result)
        payloads: mapping of fields relevant to the syscall (bytes/mapping per field)
        limits: optional overrides for _DEFAULT_CAPS keys
    """
    caps = _caps(limits)

    if name == "blob_pin":
        # payloads: {"data": bytes}
        data = _ensure_bytes(payloads.get("data"), label="blob_pin.data")
        _bounded(data, max_len=caps["max_blob_bytes"], label="blob_pin.data")

    elif name == "ai_enqueue":
        # payloads: {"prompt": bytes}
        prompt = _ensure_bytes(payloads.get("prompt"), label="ai_enqueue.prompt")
        _bounded(prompt, max_len=caps["max_prompt_bytes"], label="ai_enqueue.prompt")
        _reject_ctrl_bytes(prompt, label="ai_enqueue.prompt")
        # Optional model string may be sanitized by caller via sanitize_model_name

    elif name == "quantum_enqueue":
        # payloads: {"circuit": mapping|bytes}
        circuit = payloads.get("circuit")
        if isinstance(circuit, (bytes, bytearray, memoryview, str)):
            circ_b = _ensure_bytes(circuit, label="quantum_enqueue.circuit")
            _bounded(circ_b, max_len=caps["max_circuit_bytes"], label="quantum_enqueue.circuit")
            _reject_ctrl_bytes(circ_b, label="quantum_enqueue.circuit")
        else:
            # JSON-like mapping; check canonical size
            _check_json_payload(circuit, max_len=caps["max_circuit_bytes"], label="quantum_enqueue.circuit")

    elif name == "zk_verify":
        # payloads: {"proof": bytes, "public_input": bytes, (optional) "circuit": mapping|bytes}
        proof = _ensure_bytes(payloads.get("proof"), label="zk_verify.proof")
        pub = _ensure_bytes(payloads.get("public_input"), label="zk_verify.public_input")
        _bounded(proof, max_len=caps["max_zk_bytes"], label="zk_verify.proof")
        _bounded(pub, max_len=caps["max_zk_bytes"], label="zk_verify.public_input")
        _reject_ctrl_bytes(proof, label="zk_verify.proof")
        _reject_ctrl_bytes(pub, label="zk_verify.public_input")
        if "circuit" in payloads and payloads["circuit"] is not None:
            circuit = payloads["circuit"]
            if isinstance(circuit, (bytes, bytearray, memoryview, str)):
                circ_b = _ensure_bytes(circuit, label="zk_verify.circuit")
                _bounded(circ_b, max_len=caps["max_circuit_bytes"], label="zk_verify.circuit")
                _reject_ctrl_bytes(circ_b, label="zk_verify.circuit")
            else:
                _check_json_payload(circuit, max_len=caps["max_circuit_bytes"], label="zk_verify.circuit")

    elif name == "read_result":
        # payloads: {"task_id": bytes}
        tid = _ensure_bytes(payloads.get("task_id"), label="read_result.task_id")
        _bounded(tid, max_len=caps["max_task_id_bytes"], label="read_result.task_id")
        _reject_ctrl_bytes(tid, label="read_result.task_id")

    else:
        # Unknown syscall name — be strict by default.
        raise ValueError(f"unknown syscall name for limits enforcement: {name}")
