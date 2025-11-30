from __future__ import annotations

"""
Animica • DA • Protocol Encoding
================================

CBOR-first binary codec for DA wire messages with an optional checksum.

- Primary codec: CBOR via `cbor2` (canonical encoding).
- Fallback codec: MessagePack via `msgspec.msgpack` (non-canonical but compact).

Frame shape (CBOR / MsgPack)
----------------------------
We encode a single top-level map with the following keys:

  { "v": <int protocol_version>,
    "t": <int type_id>,
    "p": <map payload fields>,
    "c": <bytes checksum>  # optional; present by default
  }

Checksum
--------
We compute a 128-bit BLAKE2s digest over the canonical CBOR bytes of the core:

  core = {"v": v, "t": t, "p": payload}
  checksum = BLAKE2s-128(core_cbor)

The checksum covers the semantic content but not itself. Decoders verify if a
checksum is present; if missing and `verify_checksum=True`, decoding fails.

Determinism
-----------
When using CBOR we enable canonical encoding to guarantee stable bytes for the
same message. MessagePack fallback is provided purely for environments where
`cbor2` isn't available; nodes SHOULD run with CBOR in production.

"""

from dataclasses import asdict, is_dataclass
from hashlib import blake2s
from typing import Any, Dict, Tuple, Type

# Optional dependencies
try:
    import cbor2  # type: ignore

    _HAS_CBOR2 = True
except Exception:  # pragma: no cover
    _HAS_CBOR2 = False

try:
    import msgspec  # type: ignore

    _HAS_MSGSPEC = True
except Exception:  # pragma: no cover
    _HAS_MSGSPEC = False

from . import PROTOCOL_VERSION
from .messages import MESSAGE_REGISTRY, ProtocolMessage

# =============================================================================
# Low-level serializer selection
# =============================================================================


def _dumps(obj: Any, *, canonical: bool = False) -> bytes:
    if _HAS_CBOR2:
        # cbor2 supports canonical encoding for deterministic ordering.
        return cbor2.dumps(obj, canonical=canonical)
    if _HAS_MSGSPEC:
        return msgspec.msgpack.encode(obj)
    raise RuntimeError(
        "No serializer available: install 'cbor2' (preferred) or 'msgspec'"
    )


def _loads(buf: bytes) -> Any:
    if _HAS_CBOR2:
        return cbor2.loads(buf)
    if _HAS_MSGSPEC:
        return msgspec.msgpack.decode(buf)
    raise RuntimeError(
        "No serializer available: install 'cbor2' (preferred) or 'msgspec'"
    )


# =============================================================================
# Payload <-> dataclass conversion
# =============================================================================


def _to_payload(msg: ProtocolMessage) -> Dict[str, Any]:
    if not is_dataclass(msg):
        raise TypeError(f"expected dataclass message, got {type(msg)!r}")
    d = asdict(msg)
    # remove the class variable `type_id` if present in dict (it shouldn't be)
    d.pop("type_id", None)
    return d


def _from_payload(type_id: int, payload: Dict[str, Any]) -> ProtocolMessage:
    cls: Type[ProtocolMessage] = MESSAGE_REGISTRY.get(type_id)  # type: ignore[assignment]
    if cls is None:
        raise ValueError(f"unknown DA protocol message type_id: {type_id}")
    return cls(**payload)  # type: ignore[arg-type]


# =============================================================================
# Checksums
# =============================================================================


def _checksum_core(v: int, t: int, p: Dict[str, Any]) -> bytes:
    """
    Compute 16-byte BLAKE2s over the canonical CBOR of {"v":v,"t":t,"p":p}.
    """
    core = {"v": int(v), "t": int(t), "p": p}
    # Force canonical= True to stabilize the digest across environments.
    core_bytes = _dumps(core, canonical=True)
    h = blake2s(digest_size=16)
    h.update(core_bytes)
    return h.digest()


# =============================================================================
# Public API
# =============================================================================


def encode_frame(msg: ProtocolMessage, *, include_checksum: bool = True) -> bytes:
    """
    Encode a protocol message to bytes (CBOR preferred).

    :param msg: dataclass instance from da.protocol.messages
    :param include_checksum: if False, omit "c" from the frame
    :return: bytes suitable for transport
    """
    payload = _to_payload(msg)
    # All message classes define a constant `type_id` ClassVar[int]
    type_id = getattr(type(msg), "type_id", None)
    if not isinstance(type_id, int):
        raise TypeError("message missing integer 'type_id' ClassVar")

    v = int(PROTOCOL_VERSION)
    t = int(type_id)
    p = payload

    frame: Dict[str, Any] = {"v": v, "t": t, "p": p}
    if include_checksum:
        frame["c"] = _checksum_core(v, t, p)
    # Canonical CBOR if available; MsgPack fallback otherwise
    return _dumps(frame, canonical=True)


def decode_frame(buf: bytes, *, verify_checksum: bool = True) -> ProtocolMessage:
    """
    Decode a frame produced by :func:`encode_frame`.

    :param buf: encoded bytes
    :param verify_checksum: verify "c" if present, or require it if True
    :return: message dataclass instance
    :raises: ValueError on malformed input or checksum failure
    """
    try:
        obj = _loads(buf)
    except Exception as e:  # pragma: no cover
        raise ValueError(f"failed to decode DA frame: {e}") from e

    if not isinstance(obj, dict):
        raise ValueError("invalid DA frame: expected map at top-level")

    try:
        v = int(obj["v"])
        t = int(obj["t"])
        p = obj["p"]
        c = obj.get("c", None)
    except Exception as e:
        raise ValueError(f"invalid DA frame fields: {e}") from e

    if v != int(PROTOCOL_VERSION):
        raise ValueError(
            f"unsupported DA protocol version: {v}, expected {PROTOCOL_VERSION}"
        )

    if not isinstance(p, dict):
        raise ValueError("invalid DA frame: payload 'p' must be a map")

    if verify_checksum:
        if c is None:
            raise ValueError("checksum missing from DA frame")
        if not isinstance(c, (bytes, bytearray)):
            raise ValueError("invalid checksum type in DA frame")
        expected = _checksum_core(v, t, p)
        if bytes(c) != expected:
            raise ValueError("DA frame checksum mismatch")

    try:
        msg = _from_payload(t, p)
    except TypeError as e:
        raise ValueError(f"payload does not match message type_id {t}: {e}") from e
    return msg


__all__ = [
    "encode_frame",
    "decode_frame",
]
