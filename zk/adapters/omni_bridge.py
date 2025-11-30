"""
Animica zk.adapters.omni_bridge
===============================

Bridge between *toolchain-native* proof bundles (SnarkJS/PlonkJS/STARK JSON)
and the **runtime verifiers** in `zk.verifiers.*`.

Goals
-----
1) Provide a small, *stable* ProofEnvelope shape used by the rest of the stack.
2) Detect/normalize third-party JSON into a ProofEnvelope.
3) Map a ProofEnvelope → concrete verifier call (module, function, args).
4) Offer a convenience dispatcher `dispatch_verify(envelope)`.

This module performs **light validation and normalization only**. It does
not implement the cryptography; that lives in `zk.verifiers.*`.

Supported kinds
---------------
- "groth16_bn254"      (SnarkJS compatible)
- "plonk_kzg_bn254"    (PlonkJS/SnarkJS KZG on BN254)
- "stark_fri_merkle"   (toy FRI/Merkle educational verifier)

If you need raw KZG openings or other primitives, prefer using the
specialized adapters (e.g., `plonkjs_loader.extract_kzg_opening`) and
call the corresponding verifier directly.

ProofEnvelope (canonical)
-------------------------
A *normalized* envelope looks like:

    {
      "kind": "groth16_bn254" | "plonk_kzg_bn254" | "stark_fri_merkle",
      "vk":     dict | None,    # verifying key (shape depends on kind)
      "proof":  dict,           # proof object (kind-specific)
      "public": list|dict|None, # public inputs (kind-specific)
      "meta":   dict|None       # optional auxiliary metadata
    }

Public API
----------
- detect_and_build_envelope(vk_src, proof_src, *, prefer=None) -> ProofEnvelope
- envelope_from_snarkjs_groth16(vk_src, proof_src)             -> ProofEnvelope
- envelope_from_plonkjs(vk_src, proof_src)                     -> ProofEnvelope
- envelope_from_stark_fri(proof_src, public_src=None)          -> ProofEnvelope

- build_verifier_call(envelope) -> VerifierCall
- dispatch_verify(envelope)     -> bool

Errors
------
All public functions raise `OmniError(code=ErrorCode.XXX, message=...)`
on user/actionable faults.

License: MIT
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple, Union

from .plonkjs_loader import is_plonkjs_proof, is_plonkjs_vk, load_plonkjs
from .snarkjs_loader import is_groth16_proof, is_groth16_vk, load_groth16
from .stark_loader import is_fri_proof, load_fri

# =============================================================================
# Error model
# =============================================================================


class ErrorCode:
    MALFORMED_INPUT = "MALFORMED_INPUT"
    UNSUPPORTED_PROOF_KIND = "UNSUPPORTED_PROOF_KIND"
    NORMALIZATION_FAILED = "NORMALIZATION_FAILED"
    MISSING_FIELDS = "MISSING_FIELDS"
    IMPORT_ERROR = "IMPORT_ERROR"
    VERIFY_PREP_ERROR = "VERIFY_PREP_ERROR"
    VERIFY_RUNTIME_ERROR = "VERIFY_RUNTIME_ERROR"


class OmniError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


# =============================================================================
# Envelope & call types
# =============================================================================

ProofEnvelope = Dict[str, Any]


@dataclass
class VerifierCall:
    """Descriptor for a concrete verifier invocation."""

    module: str
    func: str
    args: Dict[str, Any]

    def resolve(self) -> Callable[..., Any]:
        try:
            mod = import_module(self.module)
        except Exception as e:
            raise OmniError(
                ErrorCode.IMPORT_ERROR, f"Failed to import {self.module}: {e}"
            ) from e
        try:
            fn = getattr(mod, self.func)
        except AttributeError as e:
            raise OmniError(
                ErrorCode.IMPORT_ERROR,
                f"Function '{self.func}' not found in {self.module}",
            ) from e
        return fn


# =============================================================================
# Builders: third-party → envelope
# =============================================================================


def envelope_from_snarkjs_groth16(vk_src: Any, proof_src: Any) -> ProofEnvelope:
    """
    Normalize SnarkJS-style Groth16 {vk, proof} JSON sources into a canonical envelope.
    """
    try:
        vk, proof, publics = load_groth16(vk_src, proof_src)
    except Exception as e:
        raise OmniError(
            ErrorCode.NORMALIZATION_FAILED, f"SnarkJS Groth16 normalize failed: {e}"
        ) from e
    return {
        "kind": "groth16_bn254",
        "vk": vk,
        "proof": proof,
        "public": publics,
        "meta": {"source": "snarkjs"},
    }


def envelope_from_plonkjs(vk_src: Any, proof_src: Any) -> ProofEnvelope:
    """
    Normalize PlonkJS-style PLONK/KZG {vk, proof} JSON sources into a canonical envelope.
    """
    try:
        vk, proof, publics = load_plonkjs(vk_src, proof_src)
    except Exception as e:
        raise OmniError(
            ErrorCode.NORMALIZATION_FAILED, f"PlonkJS normalize failed: {e}"
        ) from e
    return {
        "kind": "plonk_kzg_bn254",
        "vk": vk,
        "proof": proof,
        "public": publics,
        "meta": {"source": "plonkjs"},
    }


def envelope_from_stark_fri(
    proof_src: Any, public_src: Optional[Any] = None
) -> ProofEnvelope:
    """
    Normalize a STARK FRI bundle (and optional public IO) into a canonical envelope.
    """
    try:
        proof, pub = load_fri(proof_src, public_src)
    except Exception as e:
        raise OmniError(
            ErrorCode.NORMALIZATION_FAILED, f"FRI normalize failed: {e}"
        ) from e
    return {
        "kind": "stark_fri_merkle",
        "vk": None,
        "proof": proof,
        "public": pub,
        "meta": {"source": "stark"},
    }


def detect_and_build_envelope(
    vk_src: Optional[Any],
    proof_src: Any,
    *,
    prefer: Optional[str] = None,
) -> ProofEnvelope:
    """
    Best-effort detection of the input format (SnarkJS Groth16 / PlonkJS / FRI).

    - If `prefer` is set, we try that first: one of
      {"groth16_bn254","plonk_kzg_bn254","stark_fri_merkle"}.
    - `vk_src` is optional for STARK FRI.

    Raises OmniError on failure.
    """
    # Try preferred path first
    if prefer == "groth16_bn254":
        return envelope_from_snarkjs_groth16(vk_src, proof_src)
    if prefer == "plonk_kzg_bn254":
        return envelope_from_plonkjs(vk_src, proof_src)
    if prefer == "stark_fri_merkle":
        return envelope_from_stark_fri(proof_src)

    # Otherwise inspect JSONs
    vk_json = None
    if vk_src is not None:
        try:
            from .snarkjs_loader import \
                load_json as _load_json  # local import to avoid cycle note

            vk_json = _load_json(vk_src)
        except Exception:
            vk_json = None

    try:
        from .snarkjs_loader import load_json as _load_json

        proof_json = _load_json(proof_src)
    except Exception as e:
        raise OmniError(
            ErrorCode.MALFORMED_INPUT, f"Could not parse proof JSON: {e}"
        ) from e

    # Groth16?
    if (vk_json and is_groth16_vk(vk_json)) or is_groth16_proof(proof_json):
        return envelope_from_snarkjs_groth16(vk_src, proof_src)

    # PLONK KZG?
    if (vk_json and is_plonkjs_vk(vk_json)) or is_plonkjs_proof(proof_json):
        return envelope_from_plonkjs(vk_src, proof_src)

    # STARK FRI?
    if is_fri_proof(proof_json):
        return envelope_from_stark_fri(proof_src)

    raise OmniError(
        ErrorCode.UNSUPPORTED_PROOF_KIND,
        "Could not detect proof format (not Groth16/PLONK(FKZG)/FRI). "
        "Provide 'prefer=' hint or ensure inputs are valid.",
    )


# =============================================================================
# Envelope → verifier call
# =============================================================================

# Registry of verifier modules and entrypoints (must match zk/verifiers/*)
_VERIFIER_REGISTRY: Dict[str, Tuple[str, str]] = {
    "groth16_bn254": ("zk.verifiers.groth16_bn254", "verify"),
    "plonk_kzg_bn254": ("zk.verifiers.plonk_kzg_bn254", "verify"),
    "stark_fri_merkle": ("zk.verifiers.stark_fri", "verify"),
}


def _require_fields(env: ProofEnvelope, fields: List[str]) -> None:
    missing = [f for f in fields if env.get(f) is None]
    if missing:
        raise OmniError(
            ErrorCode.MISSING_FIELDS, f"Envelope missing required fields: {missing}"
        )


def build_verifier_call(envelope: ProofEnvelope) -> VerifierCall:
    """
    Build a concrete verifier call from a canonical envelope.
    Validates required fields per kind and prepares argument dicts.
    """
    kind = envelope.get("kind")
    if not isinstance(kind, str):
        raise OmniError(ErrorCode.MISSING_FIELDS, "Envelope must include 'kind'")

    if kind not in _VERIFIER_REGISTRY:
        raise OmniError(
            ErrorCode.UNSUPPORTED_PROOF_KIND, f"Unsupported proof kind: {kind}"
        )

    module, func = _VERIFIER_REGISTRY[kind]

    if kind == "groth16_bn254":
        _require_fields(envelope, ["vk", "proof", "public"])
        args = {
            "vk": envelope["vk"],
            "proof": envelope["proof"],
            "public_inputs": envelope["public"],
        }

    elif kind == "plonk_kzg_bn254":
        _require_fields(envelope, ["vk", "proof"])
        # 'public' is optional in some flows (can be empty list)
        args = {
            "vk": envelope["vk"],
            "proof": envelope["proof"],
            "public_inputs": envelope.get("public", []),
        }

    elif kind == "stark_fri_merkle":
        _require_fields(envelope, ["proof"])
        args = {
            "proof": envelope["proof"],
            "public": envelope.get("public"),  # may be None
        }

    else:
        # Should not happen due to registry check
        raise OmniError(ErrorCode.UNSUPPORTED_PROOF_KIND, f"Unknown proof kind: {kind}")

    return VerifierCall(module=module, func=func, args=args)


# =============================================================================
# Dispatcher
# =============================================================================


def dispatch_verify(envelope: ProofEnvelope) -> bool:
    """
    Resolve and invoke the concrete verifier for the given envelope.
    Returns True/False. Any internal exception is wrapped into OmniError.
    """
    try:
        call = build_verifier_call(envelope)
    except OmniError:
        raise
    except Exception as e:
        raise OmniError(
            ErrorCode.VERIFY_PREP_ERROR, f"Failed to prepare verifier call: {e}"
        ) from e

    fn = call.resolve()
    try:
        result = fn(**call.args)
    except OmniError:
        raise
    except Exception as e:
        raise OmniError(ErrorCode.VERIFY_RUNTIME_ERROR, f"Verifier crashed: {e}") from e

    # Verifiers should return bool; coerce defensively.
    return bool(result)


# =============================================================================
# Public exports
# =============================================================================

__all__ = [
    # envelope builders
    "detect_and_build_envelope",
    "envelope_from_snarkjs_groth16",
    "envelope_from_plonkjs",
    "envelope_from_stark_fri",
    # call build/dispatch
    "build_verifier_call",
    "dispatch_verify",
    # errors
    "ErrorCode",
    "OmniError",
]
