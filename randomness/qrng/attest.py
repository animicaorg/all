"""
randomness.qrng.attest
======================

Placeholder attestation hooks for QRNG devices.

This module defines light-weight types and a pluggable verifier interface so
that future hardware QRNGs (USB dongles, PCIe cards, network appliances, etc.)
can present signed evidence about their identity/health, and callers can produce
a structured *TrustReport*.

⚠️  Consensus boundary
    -------------------
    Anything here is **non-consensus**. Beacon mixing or protocol-critical use
    must *not* depend on specific attestation accept/reject outcomes unless
    there is an explicit, versioned on-chain policy. For now, these hooks are
    intended for operational hygiene, observability, and optional gating in
    off-critical paths (e.g., seeding non-critical PRNGs or enabling providers).

Design goals
------------
- Zero third-party deps (stdlib only). Vendors can supply out-of-tree verifiers.
- Stable, typed dataclasses for identity, evidence, and reports.
- A simple `AttestationVerifier` protocol with a safe, permissive default
  (`NoopAttestationVerifier`) and a minimal X.509-ish placeholder verifier
  (`MinimalX509Verifier`) that performs *best-effort* checks.

Future work (non-breaking):
- PQ/hybrid certificate parsing and verification (Dilithium3/SPHINCS+ + X.509
  or COSE/X5U equivalents).
- Vendor- and device-specific measurement parsing (SGX/TDX-style claims, TPM
  quotes, or custom QRNG telemetry).
- Policy engine with trust anchors, allowlists/denylists, expiry/OCSP logic.
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import time
from typing import Dict, List, Mapping, Optional, Protocol, runtime_checkable

try:
    # Prefer project domain-separated SHA3 helpers if available.
    from randomness.utils.hash import sha3_256 as _sha3_256  # type: ignore
except Exception:  # pragma: no cover - fallback for isolated usage

    def _sha3_256(data: bytes) -> bytes:
        return hashlib.sha3_256(data).digest()


# ---------------------------- Domain constants --------------------------------

_ATTEST_TRANSCRIPT_DOMAIN = b"animica/qrng/attest/v1"


# ------------------------------- Data types -----------------------------------


@dataclasses.dataclass(frozen=True)
class DeviceIdentity:
    """
    Minimal identity surface for a QRNG device.

    Fields:
        provider: Human-readable provider/manufacturer identifier (e.g., "AcmeQRNG").
        model: Model identifier string.
        serial: Device serial number (opaque; may be redacted upstream).
        public_key: Optional device public key bytes (format TBD / vendor-specific).
        cert_chain: Optional list of certificate blobs (PEM or DER as bytes).
        metadata: Free-form extra hints (e.g., {"subject": "...", "san": ["..."]}).
    """

    provider: str
    model: str
    serial: str
    public_key: Optional[bytes] = None
    cert_chain: Optional[List[bytes]] = None
    metadata: Mapping[str, object] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True)
class AttestationEvidence:
    """
    Evidence blob emitted by the device or an associated agent.

    Fields:
        nonce: Challenge nonce provided by the verifier/caller (bind freshness).
        report: Opaque device report bytes (measurements/health/entropy stats).
        signature: Opaque device or device-cert signature over a transcript.
        timestamp_s: Seconds since epoch (device- or agent-reported).
        auxiliary: Free-form extra fields (JSON-like) for vendor extensions.
    """

    nonce: bytes
    report: bytes
    signature: bytes
    timestamp_s: float
    auxiliary: Mapping[str, object] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True)
class TrustReport:
    """
    Result of verification.

    Fields:
        verified: Overall pass/fail.
        reason: Short human-readable reason if not verified (or informational note).
        device_fingerprint: Stable fingerprint (e.g., SHA256 over first cert or public key).
        measurements: Parsed/normalized measurement key→value pairs (if any).
        policy_version: Optional policy identifier string used for verification.
        created_at_s: Local time when the report was created.
    """

    verified: bool
    reason: Optional[str]
    device_fingerprint: str
    measurements: Mapping[str, str]
    policy_version: str
    created_at_s: float


# ------------------------------- Helpers --------------------------------------


def transcript_hash(identity: DeviceIdentity, evidence: AttestationEvidence) -> bytes:
    """
    Compute a domain-separated transcript digest that devices are expected to sign.

    NOTE: This is a placeholder transcript; vendors may require different layouts.
    """
    parts = [
        _ATTEST_TRANSCRIPT_DOMAIN,
        b"|prov:",
        identity.provider.encode("utf-8"),
        b"|model:",
        identity.model.encode("utf-8"),
        b"|serial:",
        identity.serial.encode("utf-8"),
        b"|nonce:",
        evidence.nonce,
        b"|report:",
        evidence.report,
        b"|ts:",
        str(int(evidence.timestamp_s)).encode("ascii"),
    ]
    return _sha3_256(b"".join(parts))


def _fingerprint_from_identity(identity: DeviceIdentity) -> str:
    """
    Derive a stable fingerprint string for display/logging.

    Priority:
      1) First certificate blob (sha256 over DER or normalized PEM)
      2) Public key bytes (sha256)
      3) Hash of provider|model|serial (coarse, not secure)
    """
    if identity.cert_chain:
        first = identity.cert_chain[0]
        # Normalize PEM → raw DER-ish bytes by stripping header/footer if present.
        if b"-----BEGIN" in first:
            # Keep as-is: hashing the PEM bytes is fine for a placeholder.
            raw = first
        else:
            raw = first
        h = hashlib.sha256(raw).hexdigest()
        return f"cert/sha256:{h}"
    if identity.public_key:
        h = hashlib.sha256(identity.public_key).hexdigest()
        return f"pubkey/sha256:{h}"
    coarse = "|".join((identity.provider, identity.model, identity.serial)).encode(
        "utf-8"
    )
    return f"id/sha256:{hashlib.sha256(coarse).hexdigest()}"


# -------------------------- Verifier interfaces --------------------------------


@runtime_checkable
class AttestationVerifier(Protocol):
    """
    A pluggable verifier for QRNG attestation evidence.
    """

    def verify(
        self,
        identity: DeviceIdentity,
        evidence: AttestationEvidence,
        *,
        policy: Optional[Mapping[str, object]] = None,
    ) -> TrustReport:
        """
        Verify evidence for `identity` under an optional `policy`.

        Implementations should:
          - Check transcript signature validity (if applicable)
          - Validate timing/freshness constraints
          - Optionally parse measurements and enforce policy thresholds
        """
        ...


# ------------------------- Default/placeholder verifiers -----------------------


class NoopAttestationVerifier(AttestationVerifier):
    """
    Permissive verifier that accepts everything and emits a minimal report.

    Useful for development and environments where device attestation is not
    available. **Do not** rely on this for any security property.
    """

    def verify(
        self,
        identity: DeviceIdentity,
        evidence: AttestationEvidence,
        *,
        policy: Optional[Mapping[str, object]] = None,
    ) -> TrustReport:
        fp = _fingerprint_from_identity(identity)
        note = "noop verifier (no cryptographic checks performed)"
        meas: Dict[str, str] = {}
        # Optionally expose a terse preview of the report for debugging
        if evidence.report:
            meas["report.b64sha256"] = hashlib.sha256(evidence.report).hexdigest()
            meas["report.preview_b64"] = base64.b64encode(evidence.report[:32]).decode(
                "ascii"
            )
        if identity.metadata.get("subject"):
            meas["x509.subject"] = str(identity.metadata["subject"])
        pv = str(policy.get("version")) if policy and "version" in policy else "none"
        return TrustReport(
            verified=True,
            reason=note,
            device_fingerprint=fp,
            measurements=meas,
            policy_version=pv,
            created_at_s=time.time(),
        )


class MinimalX509Verifier(AttestationVerifier):
    """
    Minimal, best-effort checks for environments where a device presents a
    certificate chain and signs the transcript with the leaf key.

    This *does not* perform full path building, revocation, EKU checking, or
    PQ/hybrid verification. It only:
      - Computes a transcript hash.
      - Optionally checks that policy['expected_subject_contains'] appears in
        identity.metadata['subject'] (if provided).
      - Optionally enforces a max age window for evidence.timestamp_s
        (policy['max_age_s'], default 120s).
      - Verifies signature FORMAT IS NOT CHECKED (placeholder: returns True).

    Vendors should supply a real implementation out-of-tree.
    """

    def verify(
        self,
        identity: DeviceIdentity,
        evidence: AttestationEvidence,
        *,
        policy: Optional[Mapping[str, object]] = None,
    ) -> TrustReport:
        pv = str(policy.get("version")) if policy and "version" in policy else "none"
        max_age_s = int(policy.get("max_age_s", 120)) if policy else 120
        expected_substr = (
            str(policy.get("expected_subject_contains", "")) if policy else ""
        )

        now = time.time()
        age = abs(now - float(evidence.timestamp_s))
        if age > max_age_s:
            return TrustReport(
                verified=False,
                reason=f"evidence too old (age {int(age)}s > {max_age_s}s)",
                device_fingerprint=_fingerprint_from_identity(identity),
                measurements={},
                policy_version=pv,
                created_at_s=now,
            )

        subj = str(identity.metadata.get("subject", ""))
        if expected_substr and expected_substr not in subj:
            return TrustReport(
                verified=False,
                reason=f"subject '{subj}' missing expected token '{expected_substr}'",
                device_fingerprint=_fingerprint_from_identity(identity),
                measurements={},
                policy_version=pv,
                created_at_s=now,
            )

        # Placeholder "verification" for signature: compute transcript digest but
        # do not actually validate cryptographically. Mark reason accordingly.
        digest = transcript_hash(identity, evidence)
        meas = {
            "transcript.sha3_256": digest.hex(),
            "timestamp.age_s": str(int(age)),
        }

        # If a cert is present, surface a fingerprint for operators.
        if identity.cert_chain:
            meas["leaf_cert.sha256"] = hashlib.sha256(
                identity.cert_chain[0]
            ).hexdigest()

        return TrustReport(
            verified=True,
            reason="minimal-x509 placeholder (no signature validation)",
            device_fingerprint=_fingerprint_from_identity(identity),
            measurements=meas,
            policy_version=pv,
            created_at_s=now,
        )


# ------------------------------ Factory ---------------------------------------


def default_verifier(kind: str = "noop") -> AttestationVerifier:
    """
    Convenience factory.

    Args:
        kind: "noop" (default) or "x509-min"
    """
    k = (kind or "noop").lower()
    if k in ("noop", "none", "permit-all"):
        return NoopAttestationVerifier()
    if k in ("x509-min", "x509", "minimal"):
        return MinimalX509Verifier()
    # Unknown kinds fall back to a safe, explicit default.
    return NoopAttestationVerifier()


__all__ = [
    "DeviceIdentity",
    "AttestationEvidence",
    "TrustReport",
    "AttestationVerifier",
    "NoopAttestationVerifier",
    "MinimalX509Verifier",
    "default_verifier",
    "transcript_hash",
]
