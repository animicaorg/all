from __future__ import annotations

import os
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable, Dict, Optional, Tuple

from mining.hash_search import micro_threshold_to_target256

from .nonce_domain import derive_mix_seed  # mixSeed evolution

# We *prefer* the canonical header SignBytes encoder from core if present.
try:
    from core.encoding.canonical import \
        encode_header_sign_bytes as _encode_header_sign_bytes  # type: ignore
except Exception:  # pragma: no cover
    _encode_header_sign_bytes = None  # fallback defined below


# ─────────────────────────────────────────────────────────────────────────────
# Lightweight, opinionated header template builder
#
# This module intentionally depends on *functional* adapters (callables) rather
# than importing the full core/consensus/proofs stacks. The mining/adapters/*
# files wire real node internals into these callables. For smoke tests, you can
# pass simple lambdas.
# ─────────────────────────────────────────────────────────────────────────────

ZERO32 = b"\x00" * 32
D1_TARGET = (0xFFFF) * 2 ** (8 * (0x1D - 3))


# What the miner needs to scan:
#  - deterministic header "SignBytes" (excludes nonce)
#  - current Θ (theta) target (µ-nats) for acceptance predicate
#  - mixSeed to bind the u-draw domain across blocks
#  - a few policy roots to pin consensus/pq policies
@dataclass(frozen=True)
class HeaderTemplate:
    parent_hash: bytes
    number: int
    chain_id: int
    state_root: bytes
    txs_root: bytes
    receipts_root: bytes
    proofs_root: bytes
    da_root: bytes
    theta_target_micro: int
    mix_seed: bytes
    pq_alg_policy_root: bytes
    poies_policy_root: bytes
    timestamp: int  # seconds since epoch (informational; included in SignBytes)
    work_type: int | None = None

    def to_sign_bytes(self) -> bytes:
        """Canonical header SignBytes for nonce preimages."""
        body = {
            # sorted, stable key set — must match core/encoding/canonical expectations
            "chainId": self.chain_id,
            "parentHash": self.parent_hash,
            "number": self.number,
            "stateRoot": self.state_root,
            "txsRoot": self.txs_root,
            "receiptsRoot": self.receipts_root,
            "proofsRoot": self.proofs_root,
            "daRoot": self.da_root,
            "thetaTargetMicro": self.theta_target_micro,
            "mixSeed": self.mix_seed,
            "pqAlgPolicyRoot": self.pq_alg_policy_root,
            "poiesPolicyRoot": self.poies_policy_root,
            "timestamp": self.timestamp,
        }
        if self.work_type is not None:
            body["workType"] = int(self.work_type)
        return _sign_bytes(body)


@dataclass(frozen=True)
class WorkTemplate:
    """Pack a ready-to-scan unit for the miner."""

    header: HeaderTemplate
    sign_bytes: bytes
    theta_target_micro: int
    height: int
    parent_hash: bytes
    work_type: int | None = None

    @property
    def mix_seed(self) -> bytes:
        return self.header.mix_seed


@dataclass(frozen=True)
class MiningJob:
    """
    Canonical mining job model shared by solo, pool, stratum, and submit paths.
    """

    job_id: str
    parent_hash: bytes
    parent_height: int
    chain_id: int
    target: int
    theta_target_micro: int
    proof_type: str
    challenge: Optional[Dict[str, Any]]
    expires_at: Optional[float]
    template_version: int
    header: HeaderTemplate
    sign_bytes: bytes
    work_type: int | None = None

    def to_dict(self) -> Dict[str, Any]:
        header_dict = asdict(self.header)
        header_view = {
            k: (_to_hex(v) if isinstance(v, (bytes, bytearray)) else v)
            for k, v in header_dict.items()
        }
        return {
            "jobId": self.job_id,
            "parentHash": _to_hex(self.parent_hash),
            "parentHeight": int(self.parent_height),
            "chainId": int(self.chain_id),
            "target": hex(int(self.target)),
            "thetaMicro": int(self.theta_target_micro),
            "proofType": self.proof_type,
            "challenge": self.challenge,
            "expiresAt": int(self.expires_at) if self.expires_at else None,
            "templateVersion": int(self.template_version),
            "header": header_view,
            "signBytes": _to_hex(self.sign_bytes),
            "hints": {"mixSeed": _to_hex(self.header.mix_seed)},
            "workType": int(self.work_type) if self.work_type is not None else None,
        }

    def is_expired(self, now: Optional[float] = None) -> bool:
        if self.expires_at is None:
            return False
        return (now or time.time()) >= self.expires_at


# ─────────────────────────────────────────────────────────────────────────────
# Adapter types (injected by mining/adapters/*)
# ─────────────────────────────────────────────────────────────────────────────

# Returns (parent_hash, height, parent_mix_seed, chain_id, parent_state_root)
GetHeadInfo = Callable[[], Tuple[bytes, int, bytes, int, bytes]]

# Returns theta_target_micro (µ-nats)
GetTheta = Callable[[], int]

# Returns (pq_alg_policy_root, poies_policy_root)
GetPolicyRoots = Callable[[], Tuple[bytes, bytes]]

# Returns the latest randomness beacon bytes (may be empty)
GetBeacon = Callable[[], bytes]


# ─────────────────────────────────────────────────────────────────────────────
# Builder
# ─────────────────────────────────────────────────────────────────────────────
class TemplateBuilder:
    """
    Stateless builder that assembles a fresh HeaderTemplate when the head or
    Θ changes. You provide four tiny callables; the builder does the rest.

    Typical wiring (see mining/adapters/*):
      - get_head_info()         ← core/chain/head.py + core/db/*
      - get_theta()             ← consensus/difficulty.py view
      - get_policy_roots()      ← consensus/policy.py + pq/alg_policy root
      - get_beacon()            ← randomness/beacon adapter (or b"" if none)
    """

    def __init__(
        self,
        get_head_info: GetHeadInfo,
        get_theta: GetTheta,
        get_policy_roots: GetPolicyRoots,
        get_beacon: GetBeacon,
        *,
        txs_root_supplier: Callable[[], bytes] | None = None,
        receipts_root_supplier: Callable[[], bytes] | None = None,
        proofs_root_supplier: Callable[[], bytes] | None = None,
        da_root_supplier: Callable[[], bytes] | None = None,
        work_selector: Callable[[int, int], int | None] | None = None,
    ) -> None:
        self._get_head = get_head_info
        self._get_theta = get_theta
        self._get_roots = get_policy_roots
        self._get_beacon = get_beacon
        if work_selector is not None:
            self._select_work = work_selector
        else:
            try:
                from mining.useful_work import auto_select_work_type  # type: ignore

                self._select_work = (
                    lambda cid, h: auto_select_work_type(chain_id=cid, height=h)
                )
            except Exception:
                self._select_work = None

        # At template time these roots are often empty; header_packer will
        # recompute them before submit. You may inject suppliers for special
        # demos/tests.
        self._txs_root = txs_root_supplier or (lambda: ZERO32)
        self._receipts_root = receipts_root_supplier or (lambda: ZERO32)
        self._proofs_root = proofs_root_supplier or (lambda: ZERO32)
        self._da_root = da_root_supplier or (lambda: ZERO32)

        # Cached tuple identifying the last head+θ we built a template for.
        self._cache_key: Optional[Tuple[bytes, int, int | None]] = None
        self._cached: Optional[WorkTemplate] = None

    # Public API ---------------------------------------------------------------
    def current_template(self, *, force: bool = False) -> WorkTemplate:
        """
        Return a WorkTemplate for the current head/Θ.
        Caches the result until head changes or Θ changes.
        """
        parent_hash, height, parent_mix, chain_id, parent_state_root = self._get_head()
        theta = self._get_theta()
        work_type = None
        # Only stamp a workType once the adaptive PoW fork activates
        if self._select_work is not None:
            activation_height = 100_000 if chain_id == 1 else 0
            if (height + 1) >= activation_height:
                try:
                    work_type = self._select_work(chain_id, height + 1)
                except Exception:
                    work_type = None

        key = (parent_hash, theta, work_type)
        if not force and self._cache_key == key and self._cached is not None:
            return self._cached

        pq_root, poies_root = self._get_roots()

        # Mix evolution binds to parent mix, randomness beacon, and chain_id.
        beacon = self._get_beacon()
        mix_seed = derive_mix_seed(parent_mix, beacon, chain_id.to_bytes(8, "big"))

        # Most roots are empty placeholders at template time; block packer fills them.
        header = HeaderTemplate(
            parent_hash=parent_hash,
            number=height + 1,
            chain_id=chain_id,
            state_root=parent_state_root,  # parent state root as starting point
            txs_root=self._txs_root(),
            receipts_root=self._receipts_root(),
            proofs_root=self._proofs_root(),
            da_root=self._da_root(),
            theta_target_micro=theta,
            mix_seed=mix_seed,
            pq_alg_policy_root=pq_root,
            poies_policy_root=poies_root,
            timestamp=int(time.time()),
            work_type=work_type,
        )
        wt = WorkTemplate(
            header=header,
            sign_bytes=header.to_sign_bytes(),
            theta_target_micro=theta,
            height=height + 1,
            parent_hash=parent_hash,
            work_type=work_type,
        )
        self._cache_key = key
        self._cached = wt
        return wt

    def current_job(
        self,
        *,
        force: bool = False,
        proof_type: str = "sha256d",
        challenge: Optional[Dict[str, Any]] = None,
        max_age_secs: Optional[int] = 20,
        template_version: int = 1,
    ) -> MiningJob:
        wt = self.current_template(force=force)
        target = micro_threshold_to_target256(wt.theta_target_micro)
        job_id = compute_job_id(
            parent_hash=wt.parent_hash,
            parent_height=wt.height - 1,
            chain_id=wt.header.chain_id,
            theta_target_micro=wt.theta_target_micro,
            target=target,
            proof_type=proof_type,
            template_version=template_version,
            header=wt.header,
            challenge=challenge,
        )
        expires_at = (
            time.time() + max_age_secs if max_age_secs is not None else None
        )
        return MiningJob(
            job_id=job_id,
            parent_hash=wt.parent_hash,
            parent_height=wt.height - 1,
            chain_id=wt.header.chain_id,
            target=target,
            theta_target_micro=wt.theta_target_micro,
            proof_type=proof_type,
            challenge=challenge,
            expires_at=expires_at,
            template_version=template_version,
            header=wt.header,
            sign_bytes=wt.sign_bytes,
            work_type=wt.work_type,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _sign_bytes(body: Dict[str, object]) -> bytes:
    """
    Encode header SignBytes using the canonical encoder if available,
    otherwise fall back to a stable, deterministic CBOR-with-sorted-keys
    (via msgspec if present) or JSON as a last resort.

    NOTE: The canonical path *must* be available for real mining, otherwise
    your u-draw preimages will not match validator recomputation. The fallback
    is provided to keep unit tests and demos running.
    """
    if _encode_header_sign_bytes is not None:
        return _encode_header_sign_bytes(body)

    # Fallback path — try msgspec (CBOR-like) then JSON.
    try:  # pragma: no cover
        import msgspec  # type: ignore

        # CBOR canonical: maps sorted by key; msgspec.cbor uses stable ordering for dict keys
        return msgspec.dumps(body, enc_hook=_enc_hook_msgspec)
    except Exception:  # pragma: no cover
        import json

        # Deterministic JSON (keys sorted, no spaces) with bytes→hex coercion so
        # the fallback path remains serializable even without msgspec installed.
        norm = {
            k: (v.hex() if isinstance(v, (bytes, bytearray)) else v)
            for k, v in body.items()
        }
        return json.dumps(norm, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _to_hex(b: bytes) -> str:
    return "0x" + b.hex()


def _json_stable(obj: Any) -> str:
    def _default(o: Any) -> Any:
        if isinstance(o, (bytes, bytearray)):
            return o.hex()
        if isinstance(o, (int, float, str, bool)) or o is None:
            return o
        if isinstance(o, dict):
            return {k: _default(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [_default(v) for v in o]
        return str(o)

    normalized = _default(obj)
    import json

    return json.dumps(normalized, sort_keys=True, separators=(",", ":"))


def _hash_job_payload(payload: Dict[str, Any]) -> str:
    try:
        from proofs.utils.hash import sha3_256  # type: ignore

        digest = sha3_256(_json_stable(payload).encode("utf-8"))
    except Exception:
        import hashlib

        digest = hashlib.sha3_256(_json_stable(payload).encode("utf-8")).digest()
    return digest.hex()


def compute_job_id(
    *,
    parent_hash: bytes,
    parent_height: int,
    chain_id: int,
    theta_target_micro: int,
    target: int,
    proof_type: str,
    template_version: int,
    header: HeaderTemplate,
    challenge: Optional[Dict[str, Any]],
) -> str:
    payload = {
        "parentHash": parent_hash.hex(),
        "parentHeight": int(parent_height),
        "chainId": int(chain_id),
        "thetaTargetMicro": int(theta_target_micro),
        "target": int(target),
        "proofType": proof_type,
        "templateVersion": int(template_version),
        "challenge": challenge,
        "header": {
            "stateRoot": header.state_root.hex(),
            "txsRoot": header.txs_root.hex(),
            "receiptsRoot": header.receipts_root.hex(),
            "proofsRoot": header.proofs_root.hex(),
            "daRoot": header.da_root.hex(),
            "pqAlgPolicyRoot": header.pq_alg_policy_root.hex(),
            "poiesPolicyRoot": header.poies_policy_root.hex(),
            "mixSeed": header.mix_seed.hex(),
            "timestamp": int(header.timestamp),
            "workType": int(header.work_type) if header.work_type is not None else None,
            "number": int(header.number),
        },
    }
    return _hash_job_payload(payload)


def _enc_hook_msgspec(obj):  # pragma: no cover
    if isinstance(obj, (bytes, bytearray)):
        return bytes(obj)
    if hasattr(obj, "hex") and callable(getattr(obj, "hex")):
        # Keep raw bytes; consumers expect bytes, not hex-strings
        return obj
    return obj


def compact_from_target(target: int) -> str:
    """
    Convert a 256-bit target integer into Bitcoin-style compact bits hex.

    This is helpful when exposing Animica templates to SHA256 stratum clients
    that expect the traditional compact representation.
    """
    if target < 0:
        target = 0
    exponent = (target.bit_length() + 7) // 8
    if exponent <= 3:
        mantissa = target << (8 * (3 - exponent))
    else:
        mantissa = target >> (8 * (exponent - 3))
    if mantissa & 0x800000:
        mantissa >>= 8
        exponent += 1
    compact = (exponent << 24) | (mantissa & 0x7FFFFF)
    return f"{compact:08x}"


def share_target_to_difficulty(theta_micro: int, share_target: float) -> float:
    """
    Map Animica's shareTarget ratio into a SHA256-style difficulty figure.

    ASIC dashboards dislike probability-like decimals; they expect the classic
    difficulty numbers derived from the D1 target. We approximate by turning
    the micro-threshold into a 256-bit target and scaling against D1.
    """
    share_target = max(float(share_target), 1e-12)
    theta_micro = max(int(theta_micro), 0)
    share_threshold = int(theta_micro * share_target)
    target = micro_threshold_to_target256(share_threshold)
    if target <= 0:
        return 1.0
    difficulty = D1_TARGET / target
    return max(difficulty, 1e-6)


# ─────────────────────────────────────────────────────────────────────────────
# Minimal fake adapters (useful for local smoke tests)
# ─────────────────────────────────────────────────────────────────────────────
def dummy_get_head() -> Tuple[bytes, int, bytes, int, bytes]:  # pragma: no cover
    parent_hash = os.urandom(32)
    height = 0
    parent_mix_seed = b"\x00" * 32
    chain_id = 1
    parent_state_root = ZERO32
    return parent_hash, height, parent_mix_seed, chain_id, parent_state_root


def dummy_get_theta() -> int:  # pragma: no cover
    # ~ln(2) * 1e6 µ-nats → rough 1 trial / 2 acceptance baseline
    return 693147


def dummy_get_roots() -> Tuple[bytes, bytes]:  # pragma: no cover
    return (b"\x11" * 32, b"\x22" * 32)


def dummy_get_beacon() -> bytes:  # pragma: no cover
    return b"dummy-beacon"


if __name__ == "__main__":  # pragma: no cover
    # Smoke test: build a template with dummy adapters
    tb = TemplateBuilder(
        get_head_info=dummy_get_head,
        get_theta=dummy_get_theta,
        get_policy_roots=dummy_get_roots,
        get_beacon=dummy_get_beacon,
    )
    wt = tb.current_template(force=True)
    print("height      :", wt.height)
    print("parent_hash :", wt.parent_hash.hex())
    print("theta (µ)   :", wt.theta_target_micro)
    print("mix_seed    :", wt.mix_seed.hex())
    print("sign_bytes  :", wt.sign_bytes.hex()[:64] + "…")
