from __future__ import annotations

"""
Fast, allocation-light pre-validation for gossip payloads *before* doing any
heavy decoding. These checks are intentionally conservative: they only reject
blatantly malformed payloads (wrong size class, obviously-wrong CBOR prefix,
indefinite-length top-level, etc.). Full semantic validation happens later.

Design goals:
- O(1) time on the hot path, no heap churn.
- No imports of heavy decoders or crypto.
- Configurable per-topic with safe defaults and easy overrides.
"""

from dataclasses import dataclass
from typing import Callable, Dict, Iterable, Optional, Set, Tuple

from .topics import is_valid_topic  # type: ignore[import]

# -----------------------------------------------------------------------------
# CBOR prefix sniffers (single-byte checks)
# -----------------------------------------------------------------------------

_CBOR_MAJOR_NAMES = {
    0: "unsigned",
    1: "negative",
    2: "bytes",
    3: "text",
    4: "array",
    5: "map",
    6: "tag",
    7: "simple/float",
}


def cbor_major_type(b0: int) -> int:
    """Return CBOR major type (0..7) from the first byte."""
    return (b0 >> 5) & 0x07


def cbor_additional_info(b0: int) -> int:
    """Return CBOR 'additional information' (low 5 bits) from the first byte."""
    return b0 & 0x1F


def cbor_top_is_indefinite(b0: int) -> bool:
    """True if top-level item uses indefinite length (AI=31)."""
    return cbor_additional_info(b0) == 31


# -----------------------------------------------------------------------------
# Topic validator
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class TopicValidator:
    """
    A fast validator configured for a topic "kind". The check function may
    perform additional custom logic but must be O(1) over |raw| (no scanning).
    """

    name: str
    min_size: int
    max_size: int
    allowed_major_types: Set[int]
    require_definite_top: bool = True
    # Optional extra single-byte guard (runs after size/major-type checks)
    extra_check: Optional[Callable[[bytes], Optional[str]]] = None

    def validate(self, raw: bytes) -> Optional[str]:
        """
        Validate payload. Returns None if OK, or a short reason string if not.
        """
        n = len(raw)
        if n < self.min_size:
            return f"too small: {n} < {self.min_size}"
        if n > self.max_size:
            return f"too large: {n} > {self.max_size}"
        b0 = raw[0]
        mt = cbor_major_type(b0)
        if self.allowed_major_types and mt not in self.allowed_major_types:
            return f"unexpected CBOR major-type {mt} ({_CBOR_MAJOR_NAMES.get(mt,'?')})"
        if self.require_definite_top and cbor_top_is_indefinite(b0):
            return "indefinite-length top-level not allowed"
        if self.extra_check is not None:
            reason = self.extra_check(raw)
            if reason:
                return reason
        return None


# -----------------------------------------------------------------------------
# Defaults (heuristic yet conservative ranges)
# -----------------------------------------------------------------------------

# These ranges are set to be permissive enough for evolution but still
# protective against obvious garbage/DoS. Tune in deployment if needed.
_DEFAULT_VALIDATORS: Dict[str, TopicValidator] = {
    # Typical signed transaction object encoded as a small CBOR map.
    "txs": TopicValidator(
        name="txs",
        min_size=64,  # signatures + body
        max_size=8_192,  # guard against pathological big txs
        allowed_major_types={5},  # CBOR map
    ),
    # Canonical header (roots, Θ, nonce, mixSeed, etc.) as a CBOR map.
    "headers": TopicValidator(
        name="headers",
        min_size=128,
        max_size=2_048,
        allowed_major_types={5},
    ),
    # Full blocks: header + txs + proofs. Upper bound is generous; blobs ride a
    # different topic.
    "blocks": TopicValidator(
        name="blocks",
        min_size=512,
        max_size=8_000_000,  # ~8 MB cap for safety
        allowed_major_types={5},
    ),
    # HashShare / AI / Quantum / Storage / VDF micro receipts typically encode
    # as maps; keep ranges tight.
    "shares": TopicValidator(
        name="shares",
        min_size=48,
        max_size=1_024,
        allowed_major_types={5, 4},  # allow array for compact encodings
    ),
    # Data-availability blobs (raw bytes or small arrays of bytes).
    "blobs": TopicValidator(
        name="blobs",
        min_size=32,
        max_size=16_000_000,  # 16 MB soft cap
        allowed_major_types={2, 4},  # bytes or array
        # For bytes, require additional-info != 31 (no indefinite top).
        extra_check=lambda raw: (
            "blob must be bytes or array-of-bytes"
            if (cbor_major_type(raw[0]) == 2 and cbor_top_is_indefinite(raw[0]))
            else None
        ),
    ),
    # Safe catch-all, in case a deployment introduces a new topic-kind.
    "generic": TopicValidator(
        name="generic",
        min_size=1,
        max_size=16_000_000,
        allowed_major_types=set(),  # any
        require_definite_top=False,  # be maximally permissive
    ),
}


def _guess_kind(topic_path: str) -> str:
    """
    Try to infer the logical kind of a topic from its path. This makes the
    validator usable with a variety of naming schemes, e.g.:

      "animica/1/blocks", "blocks/1", "chain.blocks", "blocks", etc.
    """
    s = topic_path.lower()
    endings = ("blocks", "headers", "txs", "shares", "blobs")
    for k in endings:
        if (
            s.endswith("/" + k)
            or s.endswith("." + k)
            or s.endswith(k)
            or ("/" + k + "/") in s
        ):
            return k
    return "generic"


# -----------------------------------------------------------------------------
# Registry and API
# -----------------------------------------------------------------------------


class ValidatorRegistry:
    """
    Registry mapping exact topic paths (strings) to validators. Unknown topics
    fall back to a kind-derived default based on their path suffix.
    """

    def __init__(self) -> None:
        self._by_topic: Dict[str, TopicValidator] = {}

    def register(self, topic_path: str, validator: TopicValidator) -> None:
        if not is_valid_topic(topic_path):
            raise ValueError(f"invalid topic path: {topic_path!r}")
        self._by_topic[topic_path] = validator

    def get(self, topic_path: str) -> TopicValidator:
        v = self._by_topic.get(topic_path)
        if v is not None:
            return v
        # Fall back to guessed kind
        kind = _guess_kind(topic_path)
        return _DEFAULT_VALIDATORS.get(kind, _DEFAULT_VALIDATORS["generic"])

    def prefilter(self, topic_path: str, raw: bytes) -> Tuple[bool, Optional[str]]:
        """
        Fast check suitable for ingress pipelines:
          - (True, None) if the payload is plausibly valid for this topic.
          - (False, reason) otherwise.
        """
        v = self.get(topic_path)
        reason = v.validate(raw)
        return (reason is None, reason)


# Singleton registry for convenience
_registry = ValidatorRegistry()

# Convenience exports
register = _registry.register
get_validator = _registry.get
prefilter = _registry.prefilter

__all__ = [
    "TopicValidator",
    "ValidatorRegistry",
    "register",
    "get_validator",
    "prefilter",
    "cbor_major_type",
    "cbor_additional_info",
    "cbor_top_is_indefinite",
]
