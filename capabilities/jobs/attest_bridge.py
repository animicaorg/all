from __future__ import annotations

"""
capabilities.jobs.attest_bridge
--------------------------------

Normalize provider attestation bundles (TEE / Quantum) into canonical
structures that downstream verifiers (the `proofs/` package) expect.

Design goals
- Accept heterogeneous, provider-specific "bundles" (dicts or objects).
- Produce a **stable, minimal** dict (or dataclass) with well-known keys.
- Optionally call into `proofs.attestations.*` if available for fast
  parsing/validation. If those modules are not present, we still return a
  sanitized structure suitable for later on-chain or offline verification.
- Be *defensive*: normalize hex/base64/bytes, clamp sizes, and surface
  clear exceptions.

Outputs (for AI/TEE):
    {
      "vendor": "intel_sgx" | "amd_sev_snp" | "arm_cca",
      "evidence": <bytes>,              # raw quote/report/token
      "workload_digest": <bytes>,       # sha3 digest of workload/output binding
      "redundancy": {...} | None,       # optional redundancy receipts
      "traps": {...} | None,            # optional trap receipts (AI traps)
      "qos": {"latency_ms": int, ...}   # optional QoS metrics
    }

Outputs (for Quantum):
    {
      "provider_cert": {...}|<bytes>,   # provider identity material
      "trap_outcomes": [ {...}, ... ],  # trap-circuit outcomes / stats
      "circuit_digest": <bytes>,        # digest of circuit JSON/IR
      "shots": int,                     # number of shots executed
      "qos": {"latency_ms": int, ...}   # optional QoS metrics
    }

These keys mirror the high-level shapes referenced by:
- proofs/schemas/ai_attestation.schema.json
- proofs/schemas/quantum_attestation.schema.json

If `proofs/` modules are present, we may additionally enrich/validate
fields but we will NOT fail the normalization step on importer absence.
"""

from dataclasses import dataclass, asdict
from typing import Any, Dict, Optional, List, Tuple, Union, overload
import base64
import hashlib
import json
import logging

from capabilities.errors import AttestationError

log = logging.getLogger(__name__)

# ----- Optional integrations with `proofs/` (best-effort) --------------------
try:
    from proofs.attestations.tee import sgx as _sgx  # type: ignore
except Exception:  # pragma: no cover - optional
    _sgx = None  # type: ignore[assignment]

try:
    from proofs.attestations.tee import sev_snp as _sev  # type: ignore
except Exception:  # pragma: no cover - optional
    _sev = None  # type: ignore[assignment]

try:
    from proofs.attestations.tee import cca as _cca  # type: ignore
except Exception:  # pragma: no cover - optional
    _cca = None  # type: ignore[assignment]

try:
    from proofs.quantum_attest import provider_cert as _qcert, traps as _qtraps  # type: ignore
except Exception:  # pragma: no cover - optional
    _qcert = None  # type: ignore[assignment]
    _qtraps = None  # type: ignore[assignment]


# ----- Helpers ---------------------------------------------------------------

def _sha3_256(b: bytes) -> bytes:
    return hashlib.sha3_256(b).digest()


def _sha3_512(b: bytes) -> bytes:
    return hashlib.sha3_512(b).digest()


def _as_bytes(x: Any) -> bytes:
    """Accept bytes/bytearray/memoryview/hex string/base64 string/int/json-able."""
    if x is None:
        return b""
    if isinstance(x, (bytes, bytearray, memoryview)):
        return bytes(x)
    if isinstance(x, int):
        if x < 0:
            raise AttestationError("negative integer cannot be converted to bytes")
        if x == 0:
            return b"\x00"
        out = bytearray()
        while x:
            out.append(x & 0xFF)
            x >>= 8
        return bytes(reversed(out))
    if isinstance(x, str):
        s = x.strip()
        # Try hex (0x… or raw hex)
        try:
            if s.startswith(("0x", "0X")):
                return bytes.fromhex(s[2:])
            if all(c in "0123456789abcdefABCDEF" for c in s) and len(s) % 2 == 0:
                return bytes.fromhex(s)
        except Exception:
            pass
        # Try base64 (urlsafe tolerant)
        try:
            return base64.urlsafe_b64decode(s + "===")
        except Exception:
            # raw utf-8
            return s.encode("utf-8")
    # Fallback to json then bytes
    try:
        return json.dumps(x, separators=(",", ":"), sort_keys=True).encode("utf-8")
    except Exception:
        return str(x).encode("utf-8")


def _coerce_bool(x: Any) -> bool:
    if isinstance(x, bool):
        return x
    if isinstance(x, (int,)):
        return bool(x)
    if isinstance(x, str):
        return x.strip().lower() in {"1", "true", "yes", "y", "on"}
    return False


def _get(m: Union[Dict[str, Any], Any], key: str, default: Any = None) -> Any:
    if isinstance(m, dict):
        return m.get(key, default)
    return getattr(m, key, default)


def _vendor_name(v: Any) -> str:
    s = (_get(v, "vendor", v) or "").strip().lower() if isinstance(v, (dict,)) else str(v).strip().lower()
    aliases = {
        "sgx": "intel_sgx",
        "intel": "intel_sgx",
        "intel_sgx": "intel_sgx",
        "tdx": "intel_sgx",  # treat TDX in same family for now (policy may differ)
        "sev": "amd_sev_snp",
        "sev_snp": "amd_sev_snp",
        "amd_sev": "amd_sev_snp",
        "amd_sev_snp": "amd_sev_snp",
        "cca": "arm_cca",
        "arm": "arm_cca",
        "arm_cca": "arm_cca",
    }
    return aliases.get(s, s)


def _bound_len(b: bytes, *, max_len: int, label: str) -> bytes:
    if len(b) > max_len:
        raise AttestationError(f"{label} exceeds maximum length ({len(b)} > {max_len})")
    return b


# ----- Canonical normalized shapes -------------------------------------------

@dataclass
class NormalizedTEE:
    vendor: str
    evidence: bytes                  # raw quote/report/token
    workload_digest: bytes           # sha3 digest (caller-selected domain)
    redundancy: Optional[Dict[str, Any]] = None
    traps: Optional[Dict[str, Any]] = None
    qos: Optional[Dict[str, Any]] = None

    def to_proofs_input(self) -> Dict[str, Any]:
        return {
            "vendor": self.vendor,
            "evidence": self.evidence,
            "workload_digest": self.workload_digest,
            "redundancy": self.redundancy,
            "traps": self.traps,
            "qos": self.qos,
        }


@dataclass
class NormalizedQuantum:
    provider_cert: Union[Dict[str, Any], bytes]
    trap_outcomes: List[Dict[str, Any]]
    circuit_digest: bytes
    shots: int
    qos: Optional[Dict[str, Any]] = None

    def to_proofs_input(self) -> Dict[str, Any]:
        return {
            "provider_cert": self.provider_cert,
            "trap_outcomes": self.trap_outcomes,
            "circuit_digest": self.circuit_digest,
            "shots": int(self.shots),
            "qos": self.qos,
        }


# ----- Public API ------------------------------------------------------------

def digest_workload(payload: Union[bytes, str, Dict[str, Any]], *, domain: str = "cap.attest.workload") -> bytes:
    """
    Compute a domain-separated SHA3-512 digest for AI workload binding.

        H = sha3_512( b"cap.attest.workload\\x00" || payload )

    Note: callers may choose a different domain string if policy dictates.
    """
    prefix = (domain.encode("utf-8") if isinstance(domain, str) else bytes(domain)) + b"\x00"
    return _sha3_512(prefix + _as_bytes(payload))


def digest_circuit(circuit: Union[bytes, str, Dict[str, Any]], *, domain: str = "cap.attest.circuit") -> bytes:
    """
    Compute a domain-separated SHA3-512 digest for Quantum circuit binding.

        H = sha3_512( b"cap.attest.circuit\\x00" || circuit )
    """
    prefix = (domain.encode("utf-8") if isinstance(domain, str) else bytes(domain)) + b"\x00"
    return _sha3_512(prefix + _as_bytes(circuit))


def normalize_tee_bundle(
    bundle: Union[Dict[str, Any], Any],
    *,
    expected_workload_digest: Optional[bytes] = None,
    max_evidence_len: int = 64 * 1024,
) -> NormalizedTEE:
    """
    Normalize a provider TEE bundle into `NormalizedTEE`.

    `bundle` may contain the following flexible keys (case-insensitive aliases allowed):
      vendor:        "sgx"|"sev_snp"|"cca"|aliases
      evidence:      bytes|hex|b64 (quote/report/token)
      workload_digest | workloadHash | workloadDigest
      redundancy:    object with receipts/replicas, optional
      traps:         object for AI traps receipts, optional
      qos:           object with latency/availability/etc, optional

    If `proofs.attestations.tee.*` modules are present, we may parse/validate:
      - SGX/TDX quote parsing; QE identity chain check (offline form).
      - SEV-SNP report parsing.
      - Arm CCA token parsing.
    """
    if bundle is None:
        raise AttestationError("empty TEE attestation bundle")

    vendor = _vendor_name(_get(bundle, "vendor", ""))
    evidence = _as_bytes(_get(bundle, "evidence"))
    workload_digest = _as_bytes(
        _get(bundle, "workload_digest", _get(bundle, "workloadHash", _get(bundle, "workloadDigest")))
    )

    redundancy = _get(bundle, "redundancy")
    traps = _get(bundle, "traps")
    qos = _get(bundle, "qos")

    if not vendor:
        raise AttestationError("TEE bundle missing 'vendor'")
    if not evidence:
        raise AttestationError("TEE bundle missing 'evidence'")
    evidence = _bound_len(evidence, max_len=max_evidence_len, label="TEE evidence")

    if not workload_digest:
        # Allow callers to provide a raw 'workload' to digest if digest absent
        raw_workload = _get(bundle, "workload")
        if raw_workload is None:
            raise AttestationError("TEE bundle missing 'workload_digest'")
        workload_digest = digest_workload(raw_workload)

    if expected_workload_digest and workload_digest != expected_workload_digest:
        raise AttestationError("workload_digest mismatch vs expected binding")

    # Optional best-effort structural checks with proofs parsers
    try:
        if vendor == "intel_sgx" and _sgx is not None:
            # Will raise on parse/structural issues
            _sgx.parse_quote(evidence)  # type: ignore[attr-defined]
        elif vendor == "amd_sev_snp" and _sev is not None:
            _sev.parse_report(evidence)  # type: ignore[attr-defined]
        elif vendor == "arm_cca" and _cca is not None:
            _cca.parse_token(evidence)  # type: ignore[attr-defined]
    except Exception as e:  # pragma: no cover - environment dependent
        raise AttestationError(f"TEE evidence failed structural parse for {vendor}: {e}") from e

    return NormalizedTEE(
        vendor=vendor,
        evidence=evidence,
        workload_digest=workload_digest,
        redundancy=redundancy if isinstance(redundancy, (dict, list)) else None,
        traps=traps if isinstance(traps, (dict, list)) else None,
        qos=qos if isinstance(qos, dict) else None,
    )


def normalize_quantum_bundle(
    bundle: Union[Dict[str, Any], Any],
    *,
    expected_circuit_digest: Optional[bytes] = None,
    min_traps: int = 0,
) -> NormalizedQuantum:
    """
    Normalize a provider Quantum bundle into `NormalizedQuantum`.

    Flexible input keys:
      provider_cert | providerCert | cert:    object or bytes
      trap_outcomes | traps | trapResults:   list of trap results (objects)
      circuit_digest | circuitHash | circuit: digest or raw circuit (JSON) to be digested
      shots: int
      qos:   dict

    If `circuit` provided but no digest, we'll compute digest_circuit(circuit).
    If `_qcert` / `_qtraps` modules are present, we perform light structure checks.
    """
    if bundle is None:
        raise AttestationError("empty Quantum attestation bundle")

    provider_cert = _get(bundle, "provider_cert", _get(bundle, "providerCert", _get(bundle, "cert")))
    trap_outcomes = _get(bundle, "trap_outcomes", _get(bundle, "traps", _get(bundle, "trapResults", [])))
    circuit_digest = _as_bytes(_get(bundle, "circuit_digest", _get(bundle, "circuitHash")))
    raw_circuit = _get(bundle, "circuit")
    shots = int(_get(bundle, "shots", 0))
    qos = _get(bundle, "qos")

    if not circuit_digest:
        if raw_circuit is None:
            raise AttestationError("Quantum bundle missing 'circuit_digest' (or 'circuit' to digest)")
        circuit_digest = digest_circuit(raw_circuit)

    if expected_circuit_digest and circuit_digest != expected_circuit_digest:
        raise AttestationError("circuit_digest mismatch vs expected binding")

    if not isinstance(trap_outcomes, list):
        raise AttestationError("'trap_outcomes' must be a list")
    if len(trap_outcomes) < min_traps:
        raise AttestationError(f"insufficient trap outcomes (got {len(trap_outcomes)} < {min_traps})")
    if shots < 0:
        raise AttestationError("shots must be non-negative")

    # Optional: validate provider cert/traps structure with proofs helpers
    try:
        if _qcert is not None and provider_cert is not None:
            # Accept bytes or dict; the helper may parse JSON bytes as well.
            _qcert.parse_provider_cert(provider_cert)  # type: ignore[attr-defined]
        if _qtraps is not None and trap_outcomes:
            _qtraps.validate_trap_outcomes(trap_outcomes)  # type: ignore[attr-defined]
    except Exception as e:  # pragma: no cover - environment dependent
        raise AttestationError(f"Quantum bundle failed structural checks: {e}") from e

    return NormalizedQuantum(
        provider_cert=provider_cert,
        trap_outcomes=trap_outcomes,
        circuit_digest=circuit_digest,
        shots=shots,
        qos=qos if isinstance(qos, dict) else None,
    )


__all__ = [
    "NormalizedTEE",
    "NormalizedQuantum",
    "digest_workload",
    "digest_circuit",
    "normalize_tee_bundle",
    "normalize_quantum_bundle",
]
