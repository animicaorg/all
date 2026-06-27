"""
randomness.qrng.contribution
=============================

The heart of the Quantum Useful Work (QUW) lane: a worker BUILDS an attested
quantum-entropy contribution; every node VERIFIES it deterministically and maps
it to ``ProofType.QUANTUM`` reward metrics.

Wire object (``QuantumContribution``) is all-hex/JSON-safe so it travels over the
JSON-RPC ``rand.contributeQuantumEntropy`` method and can be re-checked by any
node. Verification reuses:
  - ``randomness.qrng.attest.transcript_hash`` (domain ``animica/qrng/attest/v1``)
  - ``randomness.qrng.health.evaluate`` (SP 800-90B gate)
  - ``randomness.qrng.hsm_tpm.verify_signature`` (HSM/TPM/software signatures)

Accept gate = signature valid AND health passes AND min-entropy >= threshold AND
nonce matches the round challenge AND timestamp fresh AND nullifier unseen.
Only attested (HSM/TPM) contributions earn full reward; software-fallback is
accepted (so the lane is testable) but scored at a heavy discount.
"""

from __future__ import annotations

import dataclasses
import json
import time
from typing import Any, Dict, Mapping, Optional, Tuple

from . import attest as _attest
from . import health as _health
from . import hsm_tpm as _sig

# Reward shaping (kept here so consensus stays the single source for scoring,
# but the units/qos derivation is QUW-specific and documented).
_UNITS_PER_KILOBIT = 1.0          # base quantum_units per (1000 attested entropy bits * H)
_ATTESTED_FACTOR = 1.0
_UNATTESTED_FACTOR = 0.05         # software-fallback: testable but near-zero reward
_DEFAULT_MAX_AGE_S = 120.0


@dataclasses.dataclass(frozen=True)
class QuantumContribution:
    """JSON/hex-safe wire form of an attested quantum entropy contribution."""

    round_id: int
    nonce_hex: str
    address: str
    entropy_hex: str
    provider: str
    model: str
    serial: str
    public_key_hex: str
    alg: str
    signature_hex: str
    report_json: str          # serialized HealthReport (signed via transcript)
    timestamp_s: int
    source: Dict[str, Any]    # SourceInfo.as_dict()
    quote_hex: str = ""       # optional TPM quote
    attested_claim: bool = False

    # --- serialization ---
    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> "QuantumContribution":
        return QuantumContribution(
            round_id=int(d["round_id"]),
            nonce_hex=str(d["nonce_hex"]),
            address=str(d["address"]),
            entropy_hex=str(d["entropy_hex"]),
            provider=str(d["provider"]),
            model=str(d["model"]),
            serial=str(d["serial"]),
            public_key_hex=str(d["public_key_hex"]),
            alg=str(d["alg"]),
            signature_hex=str(d["signature_hex"]),
            report_json=str(d["report_json"]),
            timestamp_s=int(d["timestamp_s"]),
            source=dict(d.get("source") or {}),
            quote_hex=str(d.get("quote_hex", "")),
            attested_claim=bool(d.get("attested_claim", False)),
        )

    # --- derived ---
    def entropy(self) -> bytes:
        return bytes.fromhex(self.entropy_hex)

    def identity(self) -> _attest.DeviceIdentity:
        return _attest.DeviceIdentity(
            provider=self.provider, model=self.model, serial=self.serial,
            public_key=bytes.fromhex(self.public_key_hex) if self.public_key_hex else None,
            metadata={"source": self.source, "alg": self.alg},
        )

    def evidence(self) -> _attest.AttestationEvidence:
        return _attest.AttestationEvidence(
            nonce=bytes.fromhex(self.nonce_hex),
            report=self.report_json.encode("utf-8"),
            signature=bytes.fromhex(self.signature_hex) if self.signature_hex else b"",
            timestamp_s=float(self.timestamp_s),
            auxiliary={"tpm_quote": self.quote_hex} if self.quote_hex else {},
        )


# --------------------------------------------------------------------------- #
# Build (worker side)
# --------------------------------------------------------------------------- #


def build_contribution(
    source,                       # randomness.qrng.providers.QuantumEntropySource
    signer: _sig.EntropySigner,
    *,
    round_id: int,
    nonce: bytes,
    address: str,
    n_bytes: int = 4096,
    now_s: Optional[int] = None,
    min_entropy_per_byte: float = _health.DEFAULT_MIN_ENTROPY_PER_BYTE,
) -> QuantumContribution:
    """
    Read ``n_bytes`` from ``source``, run the health gate, and produce a signed,
    attested contribution bound to ``round_id`` and the verifier ``nonce``.

    Raises ``randomness.qrng.providers.EntropyHealthError``-style failure only if
    the source is itself health-gated; here we evaluate and embed the report and
    let the verifier enforce the gate (so a bad batch is provably rejected, not
    silently fixed). Callers should re-read until a passing batch is obtained.
    """
    ts = int(now_s if now_s is not None else time.time())
    data = source.random_bytes(n_bytes)
    report = _health.evaluate(data, min_entropy_per_byte=min_entropy_per_byte)
    report_json = json.dumps(report.as_dict(), sort_keys=True, separators=(",", ":"))

    sinfo = source.info().as_dict() if hasattr(source, "info") else {}
    siginfo = signer.info()

    identity = _attest.DeviceIdentity(
        provider=siginfo.provider, model=siginfo.model, serial=siginfo.serial,
        public_key=signer.public_key(),
        metadata={"source": sinfo, "alg": siginfo.alg},
    )
    evidence = _attest.AttestationEvidence(
        nonce=nonce, report=report_json.encode("utf-8"), signature=b"",
        timestamp_s=float(ts), auxiliary={},
    )
    transcript = _attest.transcript_hash(identity, evidence)
    signature = signer.sign(transcript)
    quote = signer.quote(nonce)

    return QuantumContribution(
        round_id=int(round_id),
        nonce_hex=nonce.hex(),
        address=str(address),
        entropy_hex=data.hex(),
        provider=siginfo.provider,
        model=siginfo.model,
        serial=siginfo.serial,
        public_key_hex=signer.public_key().hex(),
        alg=siginfo.alg,
        signature_hex=signature.hex(),
        report_json=report_json,
        timestamp_s=ts,
        source=sinfo,
        quote_hex=quote.hex() if quote else "",
        attested_claim=bool(siginfo.attested),
    )


# --------------------------------------------------------------------------- #
# Verify (consensus side)
# --------------------------------------------------------------------------- #


@dataclasses.dataclass(frozen=True)
class VerifyResult:
    verified: bool
    attested: bool
    reason: str
    nullifier: str
    min_entropy_per_byte: float
    health_passed: bool
    n_bytes: int


def quw_nullifier(contribution: QuantumContribution) -> str:
    """Anti-replay id = domain-separated transcript hash (hex)."""
    return _attest.transcript_hash(contribution.identity(), contribution.evidence()).hex()


class QuantumWorkVerifier(_attest.AttestationVerifier):
    """
    Concrete AttestationVerifier for QUW: recomputes the transcript and
    cryptographically verifies the signature against the device public key.
    (Replaces the placeholder MinimalX509Verifier for this lane.)
    """

    def verify(self, identity, evidence, *, policy=None):
        alg = str((identity.metadata or {}).get("alg", _sig.ALG_ED25519))
        pub = identity.public_key or b""
        transcript = _attest.transcript_hash(identity, evidence)
        ok = bool(pub) and _sig.verify_signature(alg, pub, transcript, evidence.signature)
        fp = "pubkey/sha256:" + (__import__("hashlib").sha256(pub).hexdigest() if pub else "none")
        return _attest.TrustReport(
            verified=ok,
            reason="signature ok" if ok else "signature invalid or missing",
            device_fingerprint=fp,
            measurements={"alg": alg, "attested": "true" if (policy or {}).get("attested_claim") else "false"},
            policy_version="quw/v1",
            created_at_s=time.time(),
        )


def verify_contribution(
    contribution: QuantumContribution,
    *,
    expected_nonce: bytes,
    now_s: Optional[float] = None,
    max_age_s: float = _DEFAULT_MAX_AGE_S,
    min_entropy_per_byte: float = _health.DEFAULT_MIN_ENTROPY_PER_BYTE,
    seen_nullifiers: Optional[set] = None,
    verifier: Optional[_attest.AttestationVerifier] = None,
) -> VerifyResult:
    """
    Deterministically verify a contribution. Pure function of its inputs (plus an
    optional replay set), so every node reaches the same accept/reject decision.
    """
    now = float(now_s if now_s is not None else time.time())
    verifier = verifier or QuantumWorkVerifier()
    nul = quw_nullifier(contribution)

    def fail(reason: str, attested: bool = False, h: float = 0.0, hp: bool = False) -> VerifyResult:
        return VerifyResult(False, attested, reason, nul, h, hp, 0)

    # 1) nonce freshness binding
    if contribution.nonce_hex.lower() != expected_nonce.hex().lower():
        return fail("nonce mismatch")
    # 2) timestamp freshness
    if abs(now - float(contribution.timestamp_s)) > max_age_s:
        return fail("stale or future timestamp")
    # 3) replay
    if seen_nullifiers is not None and nul in seen_nullifiers:
        return fail("replayed contribution")

    # 4) re-run health on the actual submitted bytes (don't trust the report)
    data = contribution.entropy()
    report = _health.evaluate(data, min_entropy_per_byte=min_entropy_per_byte)
    if not report.passed:
        return fail("health gate failed: " + "; ".join(report.reasons),
                    h=report.min_entropy_per_byte, hp=False)

    # 5) the embedded report must match (the signature covers it)
    try:
        embedded = json.loads(contribution.report_json)
        if int(embedded.get("n_samples", -1)) != len(data):
            return fail("embedded report n_samples mismatch", h=report.min_entropy_per_byte, hp=True)
    except Exception:
        return fail("malformed embedded report", h=report.min_entropy_per_byte, hp=True)

    # 6) signature / attestation
    trust = verifier.verify(
        contribution.identity(), contribution.evidence(),
        policy={"attested_claim": contribution.attested_claim},
    )
    if not trust.verified:
        return fail("attestation failed: " + (trust.reason or "?"),
                    h=report.min_entropy_per_byte, hp=True)

    attested = bool(contribution.attested_claim) and contribution.alg in (
        _sig.ALG_ED25519, _sig.ALG_ECDSA_P256,
    ) and contribution.source.get("is_hardware", False)

    if seen_nullifiers is not None:
        seen_nullifiers.add(nul)

    return VerifyResult(
        verified=True, attested=attested, reason="ok", nullifier=nul,
        min_entropy_per_byte=report.min_entropy_per_byte, health_passed=True,
        n_bytes=len(data),
    )


# --------------------------------------------------------------------------- #
# Reward mapping -> consensus.scorer.score_quantum metrics
# --------------------------------------------------------------------------- #


def contribution_to_quantum_metrics(
    contribution: QuantumContribution, vr: VerifyResult
) -> Dict[str, float]:
    """
    Map a *verified* contribution to {quantum_units, traps_ratio, qos} consumed
    unchanged by ``consensus.scorer.score_quantum`` (ProofType.QUANTUM).

      quantum_units = UNITS_PER_KILOBIT * (n_bits/1000) * H_min_per_bit * factor
      traps_ratio   = 1.0 on full health pass (drives the t_min/t_target ramp)
      qos           = source/attestation quality in [0,1]
    """
    if not vr.verified:
        return {"quantum_units": 0.0, "traps_ratio": 0.0, "qos": 0.0}
    n_bits = vr.n_bytes * 8
    h_per_bit = max(0.0, min(1.0, vr.min_entropy_per_byte / 8.0))
    factor = _ATTESTED_FACTOR if vr.attested else _UNATTESTED_FACTOR
    units = _UNITS_PER_KILOBIT * (n_bits / 1000.0) * h_per_bit * factor
    qos = 0.95 if vr.attested else 0.3
    return {
        "quantum_units": round(units, 6),
        "traps_ratio": 1.0 if vr.health_passed else 0.0,
        "qos": qos,
    }
