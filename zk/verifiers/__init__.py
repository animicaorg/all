# zk/verifiers/__init__.py
"""
Animica ZK Verifiers — high-level facade

This package exposes a small, stable interface for verifying zero-knowledge proofs
produced by common proof systems used in Animica components and examples:
Groth16 (BN254/BLS12-381), PLONK(KZG), and toy STARKs (for tests).

Design goals:
- Keep the public surface area tiny and well-typed.
- Load concrete verifier backends lazily (only when used).
- Accept either a fully-formed “Animica envelope” (recommended) or raw
  `(protocol, proof, public, vk)` tuples.
- Be explicit and friendly with errors.

Adapters
--------
Concrete verifier adapters live in sibling modules:

- `zk.verifiers.groth16`     → verifies Groth16 proofs (snarkjs JSON format)
- `zk.verifiers.plonk_kzg`   → verifies PLONK(KZG) proofs (snarkjs JSON format)
- `zk.verifiers.stark`       → verifies toy JSON STARK proofs (used in tests)

Each adapter must implement:

    def verify(proof: dict, public: list | dict, vk: dict) -> bool:
        ...
    # optionally:
    def normalize_inputs(proof: Any, public: Any, vk: Any) -> tuple[dict, list | dict, dict]:
        ...

The facade in this file does lazy imports and routes calls accordingly.

Schemas (lightweight)
---------------------
We avoid heavy JSON-Schema dependencies at runtime. Instead, we perform
minimal shape checks (presence of keys / expected container types).
For robust format conversion, use:
  `zk/scripts/convert_snarkjs_to_animica.py`

Animica Envelope (recommended)
------------------------------
The preferred container is an “envelope” dict with the following keys:

{
  "scheme": { "protocol": "groth16" | "plonk_kzg" | "stark", "curve": "bn128" | "bls12-381" | "fri", ... },
  "proof": { ... },    # JSON per scheme
  "public": [ ... ] | { ... },  # public signals / instance
  "vk": { ... }        # verifying key JSON per scheme
}

Usage
-----
>>> from zk.verifiers import verify_envelope
>>> env = {...}  # as above
>>> res = verify_envelope(env)
>>> res.ok
True

Or explicit routing:
>>> from zk.verifiers import verify
>>> ok = verify("plonk_kzg", proof, public, vk).ok

"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Tuple, Union, TypedDict, Literal, Final

# ---- Public types ---------------------------------------------------------------------------

ProtocolName = Literal["groth16", "plonk_kzg", "stark"]


class Scheme(TypedDict, total=False):
    protocol: ProtocolName
    curve: str  # e.g., "bn128", "bls12-381", "fri" (informative)


@dataclass(slots=True, frozen=True)
class VerificationResult:
    """Result of a verification attempt."""
    ok: bool
    protocol: Optional[ProtocolName] = None
    message: Optional[str] = None

    def __bool__(self) -> bool:  # allows: if result: ...
        return self.ok


class ZKError(RuntimeError):
    """Raised for malformed inputs or missing verifier backends."""


# ---- Constants & adapter registry ------------------------------------------------------------

SUPPORTED_PROTOCOLS: Final[Tuple[ProtocolName, ...]] = ("groth16", "plonk_kzg", "stark")

# Map normalized protocol → adapter module suffix (zk.verifiers.<module>)
_ADAPTER_MODULE: Final[Mapping[ProtocolName, str]] = {
    "groth16": "groth16",
    "plonk_kzg": "plonk_kzg",
    "stark": "stark",
}


# ---- Helpers --------------------------------------------------------------------------------

def _normalize_protocol(p: str | ProtocolName) -> ProtocolName:
    """Accept common aliases, return canonical `ProtocolName`."""
    if not isinstance(p, str):
        raise ZKError("Protocol name must be a string.")
    key = p.strip().lower().replace("-", "_")
    if key in ("groth16", "g16"):
        return "groth16"
    if key in ("plonk_kzg", "plonk", "plonk_kate", "plonk_kate_kzg", "plonk_kzg_bn254"):
        return "plonk_kzg"
    if key in ("stark", "fri", "air"):
        return "stark"
    raise ZKError(f"Unsupported protocol '{p}'. Supported: {', '.join(SUPPORTED_PROTOCOLS)}")


def _import_adapter(protocol: ProtocolName):
    """Lazy import the adapter module for a protocol."""
    modname = _ADAPTER_MODULE[protocol]
    try:
        return import_module(f".{modname}", __name__)
    except ModuleNotFoundError as e:
        raise ZKError(
            f"Verifier backend for '{protocol}' is not available "
            f"(missing module 'zk.verifiers.{modname}')."
        ) from e


def _shape_check_envelope(envelope: Mapping[str, Any]) -> tuple[ProtocolName, Mapping[str, Any], Any, Mapping[str, Any]]:
    """Perform minimal shape checks and extract fields from an envelope."""
    if not isinstance(envelope, Mapping):
        raise ZKError("Envelope must be a mapping/dict.")
    scheme = envelope.get("scheme")
    if not isinstance(scheme, Mapping):
        raise ZKError("Envelope missing 'scheme' mapping.")
    protocol_raw = scheme.get("protocol")
    if not isinstance(protocol_raw, str):
        raise ZKError("Envelope 'scheme.protocol' must be a string.")
    protocol = _normalize_protocol(protocol_raw)

    proof = envelope.get("proof")
    public = envelope.get("public")
    vk = envelope.get("vk")

    if not isinstance(proof, Mapping):
        raise ZKError("Envelope 'proof' must be a JSON object (mapping).")
    if not isinstance(vk, Mapping):
        raise ZKError("Envelope 'vk' must be a JSON object (mapping).")
    # public can be a list OR mapping, keep it flexible:
    if not (isinstance(public, list) or isinstance(public, Mapping)):
        raise ZKError("Envelope 'public' must be an array or object.")

    return protocol, proof, public, vk


def _shape_check_tuple(protocol: str | ProtocolName, proof: Any, public: Any, vk: Any) -> ProtocolName:
    p = _normalize_protocol(protocol)
    if not isinstance(proof, Mapping):
        raise ZKError("`proof` must be a JSON object (mapping).")
    if not (isinstance(public, list) or isinstance(public, Mapping)):
        raise ZKError("`public` must be an array or object.")
    if not isinstance(vk, Mapping):
        raise ZKError("`vk` (verifying key) must be a JSON object (mapping).")
    return p


# ---- Public API -----------------------------------------------------------------------------

def verify_envelope(envelope: Mapping[str, Any]) -> VerificationResult:
    """
    Verify a proof from an Animica envelope.

    Parameters
    ----------
    envelope :
        A mapping with keys: 'scheme' (contains 'protocol'), 'proof', 'public', 'vk'.

    Returns
    -------
    VerificationResult
        ok=True if verification succeeded, else ok=False with message.

    Raises
    ------
    ZKError
        If inputs are malformed or the appropriate backend is not available.

    Notes
    -----
    - For stable conversion of snarkjs outputs into an Animica envelope,
      see `zk/scripts/convert_snarkjs_to_animica.py`.
    """
    protocol, proof, public, vk = _shape_check_envelope(envelope)
    return verify(protocol, proof, public, vk)


def verify(
    protocol: str | ProtocolName,
    proof: Mapping[str, Any],
    public: Union[List[Any], Mapping[str, Any]],
    vk: Mapping[str, Any],
) -> VerificationResult:
    """
    Verify a proof for the given protocol.

    Parameters
    ----------
    protocol :
        'groth16' | 'plonk_kzg' | 'stark' (aliases accepted, e.g., 'plonk').
    proof :
        Proof JSON (per the protocol adapter).
    public :
        Public inputs / signals (array or object).
    vk :
        Verifying key JSON.

    Returns
    -------
    VerificationResult :
        ok=True on success; ok=False with message on failure.

    Raises
    ------
    ZKError
        If the protocol is unsupported or adapter is missing.
    """
    p = _shape_check_tuple(protocol, proof, public, vk)
    adapter = _import_adapter(p)

    try:
        # Allow adapters to normalize looser inputs
        if hasattr(adapter, "normalize_inputs"):
            proof, public, vk = adapter.normalize_inputs(proof, public, vk)  # type: ignore[attr-defined]

        ok = bool(adapter.verify(proof, public, vk))  # type: ignore[attr-defined]
        return VerificationResult(ok=ok, protocol=p, message=None if ok else "verification failed")
    except Exception as e:  # noqa: BLE001 — surface adapter errors as messages
        return VerificationResult(ok=False, protocol=p, message=str(e))


def has_adapter(protocol: str | ProtocolName) -> bool:
    """
    Return True if a verifier adapter for `protocol` can be imported.
    """
    try:
        p = _normalize_protocol(protocol)
        _import_adapter(p)
        return True
    except ZKError:
        return False


def list_adapters() -> Dict[ProtocolName, bool]:
    """
    Return a map of supported protocol names → availability (bool).
    """
    return {p: has_adapter(p) for p in SUPPORTED_PROTOCOLS}


__all__ = [
    "ProtocolName",
    "Scheme",
    "VerificationResult",
    "ZKError",
    "SUPPORTED_PROTOCOLS",
    "verify_envelope",
    "verify",
    "has_adapter",
    "list_adapters",
]
