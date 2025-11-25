from __future__ import annotations

import os
import time
from dataclasses import dataclass, asdict
from typing import Callable, Dict, Optional, Tuple

from .nonce_domain import derive_mix_seed  # mixSeed evolution
# We *prefer* the canonical header SignBytes encoder from core if present.
try:
    from core.encoding.canonical import encode_header_sign_bytes as _encode_header_sign_bytes  # type: ignore
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
        return _sign_bytes(body)


@dataclass(frozen=True)
class WorkTemplate:
    """Pack a ready-to-scan unit for the miner."""
    header: HeaderTemplate
    sign_bytes: bytes
    theta_target_micro: int
    height: int
    parent_hash: bytes

    @property
    def mix_seed(self) -> bytes:
        return self.header.mix_seed


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
    ) -> None:
        self._get_head = get_head_info
        self._get_theta = get_theta
        self._get_roots = get_policy_roots
        self._get_beacon = get_beacon

        # At template time these roots are often empty; header_packer will
        # recompute them before submit. You may inject suppliers for special
        # demos/tests.
        self._txs_root = txs_root_supplier or (lambda: ZERO32)
        self._receipts_root = receipts_root_supplier or (lambda: ZERO32)
        self._proofs_root = proofs_root_supplier or (lambda: ZERO32)
        self._da_root = da_root_supplier or (lambda: ZERO32)

        # Cached tuple identifying the last head+θ we built a template for.
        self._cache_key: Optional[Tuple[bytes, int]] = None
        self._cached: Optional[WorkTemplate] = None

    # Public API ---------------------------------------------------------------
    def current_template(self, *, force: bool = False) -> WorkTemplate:
        """
        Return a WorkTemplate for the current head/Θ.
        Caches the result until head changes or Θ changes.
        """
        parent_hash, height, parent_mix, chain_id, parent_state_root = self._get_head()
        theta = self._get_theta()
        key = (parent_hash, theta)
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
            state_root=parent_state_root,         # parent state root as starting point
            txs_root=self._txs_root(),
            receipts_root=self._receipts_root(),
            proofs_root=self._proofs_root(),
            da_root=self._da_root(),
            theta_target_micro=theta,
            mix_seed=mix_seed,
            pq_alg_policy_root=pq_root,
            poies_policy_root=poies_root,
            timestamp=int(time.time()),
        )
        wt = WorkTemplate(
            header=header,
            sign_bytes=header.to_sign_bytes(),
            theta_target_micro=theta,
            height=height + 1,
            parent_hash=parent_hash,
        )
        self._cache_key = key
        self._cached = wt
        return wt


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
        # Deterministic JSON (keys sorted, no spaces)
        return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _enc_hook_msgspec(obj):  # pragma: no cover
    if isinstance(obj, (bytes, bytearray)):
        return bytes(obj)
    if hasattr(obj, "hex") and callable(getattr(obj, "hex")):
        # Keep raw bytes; consumers expect bytes, not hex-strings
        return obj
    return obj


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
