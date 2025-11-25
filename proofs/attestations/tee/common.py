"""
Animica | proofs.attestations.tee.common

Shared structures, measurement binding, and policy flags used by TEE
attestation verifiers (SGX/TDX, SEV-SNP, Arm CCA).

This module intentionally contains *no* vendor-specific parsing or
signature verification. Those live in:
 - proofs.attestations.tee.sgx
 - proofs.attestations.tee.sev_snp
 - proofs.attestations.tee.cca

Here we define:
 - Canonical dataclasses for evidence, measurements, and results
 - AttestationPolicy (feature flags and acceptance criteria)
 - Measurement binding helpers (bind expected code/config to a digest)
 - Lightweight TCB/version comparison helpers
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, IntEnum, auto
from typing import Dict, List, Mapping, Optional, Sequence, Tuple, Union
from datetime import datetime, timezone
import hmac
import json
import struct

from proofs.errors import AttestationError
from proofs.utils.hash import sha3_256, sha3_512

# ──────────────────────────────────────────────────────────────────────────────
# Domain tags (must match spec/domains.yaml)
# ──────────────────────────────────────────────────────────────────────────────

DOMAIN_TEE_MEASUREMENT_BIND_V1 = b"ANIMICA::TEE_MEASUREMENT_BINDING/v1"
DOMAIN_TEE_POLICY_BIND_V1 = b"ANIMICA::TEE_POLICY_BINDING/v1"

# ──────────────────────────────────────────────────────────────────────────────
# Basic enums & typed constants
# ──────────────────────────────────────────────────────────────────────────────


class TEEKind(str, Enum):
    SGX = "sgx"          # Intel SGX / TDX quotes
    SEV_SNP = "sev_snp"  # AMD SEV-SNP reports
    CCA = "cca"          # Arm CCA Realm tokens


class SecurityMode(Enum):
    PRODUCTION = auto()
    DEBUG = auto()  # Allowed only if policy permits


class TCBStatus(IntEnum):
    UNKNOWN = 0
    UP_TO_DATE = 1
    OUT_OF_DATE = 2
    REVOKED = 3


# ──────────────────────────────────────────────────────────────────────────────
# Canonical "expected" measurements
# ──────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ExpectedMeasurements:
    """
    Contract/toolchain-facing canonical expectations that we bind to the
    work-product (AI/Quantum job) and vendor report.

    The fields are optional because not every TEE kind exposes all of them.
    - For SGX/TDX: mrenclave and/or mrsigner (bytes), isvprodid (int), isvsvn (int)
    - For SEV-SNP: measurement (bytes), family_id (bytes), image_id (bytes), tcb_svn (int)
    - For CCA: realm_measurement (bytes), realm_public_key_hash (bytes)
    """
    mrenclave: Optional[bytes] = None
    mrsigner: Optional[bytes] = None
    isvprodid: Optional[int] = None
    isvsvn: Optional[int] = None

    sev_measurement: Optional[bytes] = None
    sev_family_id: Optional[bytes] = None
    sev_image_id: Optional[bytes] = None
    sev_tcb_svn: Optional[int] = None

    cca_realm_measurement: Optional[bytes] = None
    cca_pubkey_hash: Optional[bytes] = None

    # Extra bind-ins (toolchain level), e.g. code hash and manifest digest
    code_hash: Optional[bytes] = None        # sha3_256(code_bytes)
    manifest_hash: Optional[bytes] = None    # sha3_256(manifest JSON canonical)
    # Optional salt/version to avoid cross-network replays
    network_salt: Optional[bytes] = None     # chain-specific salt (e.g., spec/chains.json)


# ──────────────────────────────────────────────────────────────────────────────
# Evidence container (normalized view)
# ──────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TEEEvidence:
    """
    Minimal normalized evidence that each vendor-specific verifier will
    produce *before* policy evaluation. The vendor verifiers must ensure
    the *cryptographic* integrity of 'report' and 'claims' already.

    - kind: 'sgx' | 'sev_snp' | 'cca'
    - report: canonical binary report/quote/token (as produced on device)
    - claims: parsed high-level fields surfaced by the vendor module
    - chain_ok: vendor CA chain up to pinned root (was verified)
    - tcb_status: quick summary (UP_TO_DATE/OUT_OF_DATE/REVOKED/UNKNOWN)
    - not_before / not_after: validity window, if applicable
    """
    kind: TEEKind
    report: bytes
    claims: Mapping[str, Union[int, bytes, str, Sequence[int], Sequence[bytes]]]
    chain_ok: bool
    tcb_status: TCBStatus
    not_before: Optional[datetime] = None
    not_after: Optional[datetime] = None


# ──────────────────────────────────────────────────────────────────────────────
# Policy (what Animica requires from TEE evidence)
# ──────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AttestationPolicy:
    """
    Policy flags and thresholds Animica uses to accept/reject TEE evidence.

    - allow_debug: accept debug-mode reports (default False)
    - require_chain_ok: reject if vendor chain fails (default True)
    - require_tcb_up_to_date: require UP_TO_DATE, otherwise reject (default True)
    - allow_tcb_out_of_date_grace_s: optional grace period for OUT_OF_DATE
    - accepted_kinds: restrict to a set (None means any of SGX/SEV/CCA)
    - bind_manifest: require manifest hash bound into measurement binding
    - bind_code: require code hash bound
    - freshness_max_age_s: optional freshness bound on not_before/after
    """
    allow_debug: bool = False
    require_chain_ok: bool = True
    require_tcb_up_to_date: bool = True
    allow_tcb_out_of_date_grace_s: Optional[int] = None
    accepted_kinds: Optional[Tuple[TEEKind, ...]] = None
    bind_manifest: bool = True
    bind_code: bool = True
    freshness_max_age_s: Optional[int] = 86400  # 24h default


# ──────────────────────────────────────────────────────────────────────────────
# Result surface (fed to proofs.ai → ProofMetrics)
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class AttestationResult:
    ok: bool
    reason: str
    security_mode: SecurityMode
    tcb_status: TCBStatus
    measurement_binding: bytes  # sha3_256 digest binding expectations→report
    claims: Dict[str, Union[int, str, bytes]] = field(default_factory=dict)
    policy_violations: List[str] = field(default_factory=list)

    def require_ok(self) -> None:
        if not self.ok:
            raise AttestationError(self.reason)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers: canonicalization & measurement binding
# ──────────────────────────────────────────────────────────────────────────────

def _as_bytes(x: Optional[Union[str, bytes, bytearray]]) -> Optional[bytes]:
    if x is None:
        return None
    if isinstance(x, (bytes, bytearray)):
        return bytes(x)
    # Accept hex-strings with/without "0x"
    s = x.lower()
    if s.startswith("0x"):
        s = s[2:]
    return bytes.fromhex(s)


def canonicalize_expected(exp: ExpectedMeasurements) -> ExpectedMeasurements:
    """Return a copy with any hex-strings normalized to raw bytes."""
    return ExpectedMeasurements(
        mrenclave=_as_bytes(exp.mrenclave),
        mrsigner=_as_bytes(exp.mrsigner),
        isvprodid=exp.isvprodid,
        isvsvn=exp.isvsvn,
        sev_measurement=_as_bytes(exp.sev_measurement),
        sev_family_id=_as_bytes(exp.sev_family_id),
        sev_image_id=_as_bytes(exp.sev_image_id),
        sev_tcb_svn=exp.sev_tcb_svn,
        cca_realm_measurement=_as_bytes(exp.cca_realm_measurement),
        cca_pubkey_hash=_as_bytes(exp.cca_pubkey_hash),
        code_hash=_as_bytes(exp.code_hash),
        manifest_hash=_as_bytes(exp.manifest_hash),
        network_salt=_as_bytes(exp.network_salt),
    )


def _pack_u32(v: Optional[int]) -> bytes:
    return struct.pack(">I", v if v is not None else 0)


def _dpush(hstate: "hashlib._Hash", label: str, payload: Optional[bytes]) -> None:
    """
    Domain-separated push: len(label)||label || len(payload)||payload
    """
    lp = label.encode("utf-8")
    hstate.update(struct.pack(">H", len(lp)))
    hstate.update(lp)
    data = payload or b""
    hstate.update(struct.pack(">I", len(data)))
    hstate.update(data)


def build_measurement_binding(
    exp: ExpectedMeasurements,
    evidence: TEEEvidence,
) -> bytes:
    """
    Construct a *deterministic* binding digest that ties:
      - the expected measurements (toolchain-level),
      - vendor-reported raw fields (normalized),
      - optional code/manifest/network salts,
    into a single 32-byte sha3_256 commitment.

    This value is stored in the AIProof 'measurement_binding' field and
    is part of the nullifier domain to prevent replay across networks.
    """
    exp = canonicalize_expected(exp)

    h = sha3_256(DOMAIN_TEE_MEASUREMENT_BIND_V1)
    _dpush(h, "kind", evidence.kind.value.encode())
    _dpush(h, "report_sha3_512", sha3_512(evidence.report).digest())

    # Expected (toolchain) side
    _dpush(h, "mrenclave", exp.mrenclave)
    _dpush(h, "mrsigner", exp.mrsigner)
    _dpush(h, "isvprodid", _pack_u32(exp.isvprodid))
    _dpush(h, "isvsvn", _pack_u32(exp.isvsvn))

    _dpush(h, "sev_measurement", exp.sev_measurement)
    _dpush(h, "sev_family_id", exp.sev_family_id)
    _dpush(h, "sev_image_id", exp.sev_image_id)
    _dpush(h, "sev_tcb_svn", _pack_u32(exp.sev_tcb_svn))

    _dpush(h, "cca_realm_measurement", exp.cca_realm_measurement)
    _dpush(h, "cca_pubkey_hash", exp.cca_pubkey_hash)

    # Toolchain artifacts & network salt
    _dpush(h, "code_hash", exp.code_hash)
    _dpush(h, "manifest_hash", exp.manifest_hash)
    _dpush(h, "network_salt", exp.network_salt)

    # Selected public claims from vendor module (already integrity-protected)
    # We canonicalize to JSON with sorted keys then hash that.
    claims_public = _public_claims_subset(evidence)
    _dpush(h, "claims_sha3_512", sha3_512(claims_public).digest())

    return h.digest()


def _public_claims_subset(evidence: TEEEvidence) -> bytes:
    """
    Reduce vendor claims to a small, stable map that captures measurements
    without leaking unnecessary device details. The vendor-specific modules
    are responsible for naming keys predictably.
    """
    allow_keys = {
        # SGX / TDX
        "mrenclave", "mrsigner", "isvprodid", "isvsvn", "debug",
        # SEV-SNP
        "measurement", "family_id", "image_id", "tcb_svn",
        # Arm CCA
        "realm_measurement", "realm_pubkey_hash",
        # Common metadata
        "vendor", "product", "report_version",
    }
    out: Dict[str, Union[int, str]] = {}
    for k, v in evidence.claims.items():
        if k not in allow_keys:
            continue
        if isinstance(v, (bytes, bytearray)):
            out[k] = "0x" + bytes(v).hex()
        elif isinstance(v, (list, tuple)) and v and isinstance(v[0], (bytes, bytearray)):
            out[k] = ["0x" + bytes(b).hex() for b in v]  # type: ignore[assignment]
        else:
            out[k] = v  # type: ignore[assignment]
    return json.dumps(out, sort_keys=True, separators=(",", ":")).encode("utf-8")


# ──────────────────────────────────────────────────────────────────────────────
# Policy evaluation
# ──────────────────────────────────────────────────────────────────────────────

def evaluate_policy(
    evidence: TEEEvidence,
    exp: ExpectedMeasurements,
    policy: AttestationPolicy,
) -> AttestationResult:
    """
    Evaluate *policy-level* acceptance. Assumes vendor module already checked:
      - Signature/chain for evidence.report
      - Internal structure of claims + anti-rollback protections

    Returns AttestationResult with 'ok' and human-readable 'reason'.
    """
    violations: List[str] = []

    # Kind gate
    if policy.accepted_kinds is not None and evidence.kind not in policy.accepted_kinds:
        violations.append(f"kind {evidence.kind.value} not in accepted set")

    # Chain
    if policy.require_chain_ok and not evidence.chain_ok:
        violations.append("vendor chain not trusted")

    # Freshness window
    now = datetime.now(timezone.utc)
    if policy.freshness_max_age_s is not None and evidence.not_before:
        age = (now - evidence.not_before).total_seconds()
        if age > policy.freshness_max_age_s:
            violations.append(f"evidence too old ({int(age)}s > {policy.freshness_max_age_s}s)")
    if evidence.not_after and now > evidence.not_after:
        violations.append("evidence expired (not_after passed)")

    # Security mode / debug
    sec_mode = SecurityMode.PRODUCTION
    debug_flag = bool(_claim_bool(evidence, "debug"))
    if debug_flag:
        sec_mode = SecurityMode.DEBUG
        if not policy.allow_debug:
            violations.append("debug mode not permitted by policy")

    # TCB
    tcb_ok = evidence.tcb_status == TCBStatus.UP_TO_DATE
    if not tcb_ok:
        if evidence.tcb_status == TCBStatus.OUT_OF_DATE and policy.allow_tcb_out_of_date_grace_s:
            # We can't measure "how long" easily here without vendor freshness markers,
            # so we simply allow OUT_OF_DATE if grace is configured and not expired by not_after.
            pass
        elif policy.require_tcb_up_to_date:
            violations.append(f"TCB status {evidence.tcb_status.name} not acceptable")

    # Binding checks (presence)
    if policy.bind_manifest and not exp.manifest_hash:
        violations.append("manifest binding required by policy, but manifest_hash missing")
    if policy.bind_code and not exp.code_hash:
        violations.append("code binding required by policy, but code_hash missing")

    binding = build_measurement_binding(exp, evidence)

    ok = len(violations) == 0
    reason = "ok" if ok else "; ".join(violations)
    return AttestationResult(
        ok=ok,
        reason=reason,
        security_mode=sec_mode,
        tcb_status=evidence.tcb_status,
        measurement_binding=binding,
        claims=_claims_compact(evidence),
        policy_violations=violations,
    )


def _claim_bool(evidence: TEEEvidence, key: str) -> bool:
    v = evidence.claims.get(key)
    if isinstance(v, bool):
        return v
    if isinstance(v, int):
        return v != 0
    if isinstance(v, str):
        return v.lower() in ("1", "true", "yes")
    return False


def _claims_compact(evidence: TEEEvidence) -> Dict[str, Union[int, str, bytes]]:
    """
    Keep a compact view for downstream metrics/logging. Binary fields become hex strings.
    """
    out: Dict[str, Union[int, str, bytes]] = {}
    for k, v in evidence.claims.items():
        if isinstance(v, (bytes, bytearray)):
            out[k] = "0x" + bytes(v).hex()
        else:
            out[k] = v  # type: ignore[assignment]
    return out


# ──────────────────────────────────────────────────────────────────────────────
# Measurement comparators (best-effort, constant-time where relevant)
# ──────────────────────────────────────────────────────────────────────────────

def bytes_eq(a: Optional[bytes], b: Optional[bytes]) -> bool:
    if a is None or b is None:
        return False
    return hmac.compare_digest(a, b)


def sgx_matches(exp: ExpectedMeasurements, claims: Mapping[str, Union[int, bytes, str]]) -> Tuple[bool, List[str]]:
    violations: List[str] = []
    mr_ok = True
    if exp.mrenclave is not None:
        mr_ok &= bytes_eq(exp.mrenclave, _as_bytes(claims.get("mrenclave")))  # type: ignore[arg-type]
        if not mr_ok:
            violations.append("mrenclave mismatch")
    if exp.mrsigner is not None:
        signer_ok = bytes_eq(exp.mrsigner, _as_bytes(claims.get("mrsigner")))  # type: ignore[arg-type]
        if not signer_ok:
            violations.append("mrsigner mismatch")
    if exp.isvprodid is not None:
        if int(claims.get("isvprodid", -1)) != exp.isvprodid:  # type: ignore[arg-type]
            violations.append("isvprodid mismatch")
    if exp.isvsvn is not None:
        if int(claims.get("isvsvn", -1)) < exp.isvsvn:  # type: ignore[arg-type]
            violations.append("isvsvn below minimum")
    return (len(violations) == 0, violations)


def sev_snp_matches(exp: ExpectedMeasurements, claims: Mapping[str, Union[int, bytes, str]]) -> Tuple[bool, List[str]]:
    violations: List[str] = []
    if exp.sev_measurement is not None:
        if not bytes_eq(exp.sev_measurement, _as_bytes(claims.get("measurement"))):  # type: ignore[arg-type]
            violations.append("SEV-SNP measurement mismatch")
    if exp.sev_family_id is not None:
        if not bytes_eq(exp.sev_family_id, _as_bytes(claims.get("family_id"))):  # type: ignore[arg-type]
            violations.append("SEV-SNP family_id mismatch")
    if exp.sev_image_id is not None:
        if not bytes_eq(exp.sev_image_id, _as_bytes(claims.get("image_id"))):  # type: ignore[arg-type]
            violations.append("SEV-SNP image_id mismatch")
    if exp.sev_tcb_svn is not None:
        if int(claims.get("tcb_svn", -1)) < exp.sev_tcb_svn:  # type: ignore[arg-type]
            violations.append("SEV-SNP tcb_svn below minimum")
    return (len(violations) == 0, violations)


def cca_matches(exp: ExpectedMeasurements, claims: Mapping[str, Union[int, bytes, str]]) -> Tuple[bool, List[str]]:
    violations: List[str] = []
    if exp.cca_realm_measurement is not None:
        if not bytes_eq(exp.cca_realm_measurement, _as_bytes(claims.get("realm_measurement"))):  # type: ignore[arg-type]
            violations.append("CCA realm_measurement mismatch")
    if exp.cca_pubkey_hash is not None:
        if not bytes_eq(exp.cca_pubkey_hash, _as_bytes(claims.get("realm_pubkey_hash"))):  # type: ignore[arg-type]
            violations.append("CCA realm_pubkey_hash mismatch")
    return (len(violations) == 0, violations)


def check_measurements(exp: ExpectedMeasurements, evidence: TEEEvidence) -> Tuple[bool, List[str]]:
    """
    Vendor-agnostic measurement matching. Returns (ok, violations).
    """
    if evidence.kind == TEEKind.SGX:
        return sgx_matches(exp, evidence.claims)
    if evidence.kind == TEEKind.SEV_SNP:
        return sev_snp_matches(exp, evidence.claims)
    if evidence.kind == TEEKind.CCA:
        return cca_matches(exp, evidence.claims)
    return (False, [f"unsupported kind {evidence.kind.value}"])


# ──────────────────────────────────────────────────────────────────────────────
# Convenience: one-shot evaluation (vendor module may call this)
# ──────────────────────────────────────────────────────────────────────────────

def evaluate_attestation(
    evidence: TEEEvidence,
    expected: ExpectedMeasurements,
    policy: AttestationPolicy,
) -> AttestationResult:
    """
    Combine measurement matching and policy checks into a single result.
    Vendor verifier should:
      1) construct TEEEvidence (chain_ok, tcb_status, claims parsed)
      2) call evaluate_attestation(...)
      3) propagate AttestationResult to proofs.ai for ψ inputs
    """
    ok_measurements, viols = check_measurements(expected, evidence)
    policy_res = evaluate_policy(evidence, expected, policy)

    violations = list(policy_res.policy_violations)
    if not ok_measurements:
        violations = ["measurement mismatch"] + violations

    ok = ok_measurements and policy_res.ok
    reason = "ok" if ok else "; ".join(violations)
    return AttestationResult(
        ok=ok,
        reason=reason,
        security_mode=policy_res.security_mode,
        tcb_status=evidence.tcb_status,
        measurement_binding=policy_res.measurement_binding,
        claims=policy_res.claims,
        policy_violations=violations,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Pretty helpers (non-consensus; for logs and debugging)
# ──────────────────────────────────────────────────────────────────────────────

def summarize_evidence(e: TEEEvidence) -> str:
    window = ""
    if e.not_before:
        window += f" nb={e.not_before.isoformat()}"
    if e.not_after:
        window += f" na={e.not_after.isoformat()}"
    return (
        f"TEE(kind={e.kind.value}, chain_ok={e.chain_ok}, "
        f"tcb={e.tcb_status.name}{window})"
    )


def summarize_expected(exp: ExpectedMeasurements) -> str:
    def hx(b: Optional[bytes]) -> str:
        return "—" if b is None else ("0x" + b.hex())
    parts = [
        f"mrenclave={hx(exp.mrenclave)}",
        f"mrsigner={hx(exp.mrsigner)}",
        f"isvprodid={exp.isvprodid}",
        f"isvsvn={exp.isvsvn}",
        f"sev.meas={hx(exp.sev_measurement)}",
        f"cca.realm={hx(exp.cca_realm_measurement)}",
        f"code={hx(exp.code_hash)}",
        f"manifest={hx(exp.manifest_hash)}",
    ]
    return "Expected(" + ", ".join(parts) + ")"


__all__ = [
    # Enums & statuses
    "TEEKind",
    "SecurityMode",
    "TCBStatus",
    # Dataclasses
    "ExpectedMeasurements",
    "TEEEvidence",
    "AttestationPolicy",
    "AttestationResult",
    # Builders & evaluators
    "build_measurement_binding",
    "evaluate_policy",
    "evaluate_attestation",
    "check_measurements",
    # Summaries
    "summarize_evidence",
    "summarize_expected",
]
