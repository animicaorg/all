from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Mapping, Optional, Union

__all__ = [
    "P2PError",
    "HandshakeError",
    "RateLimitError",
    "ProtocolError",
    "P2PErrorCode",
    "as_error_dict",
]


class P2PErrorCode:
    """
    Canonical string codes for P2P errors.
    Kept stable for logs/metrics and cross-process handling.
    """

    GENERIC = "P2P_ERROR"
    HANDSHAKE_FAILED = "HANDSHAKE_FAILED"
    RATE_LIMITED = "RATE_LIMITED"
    PROTOCOL_VIOLATION = "PROTOCOL_VIOLATION"


@dataclass
class P2PError(Exception):
    """
    Base class for P2P-layer errors with structured context.

    Attributes:
        message: Human-friendly message.
        code: Stable machine-readable code (see P2PErrorCode).
        retryable: Whether the caller MAY retry the operation later.
        disconnect: Whether the connection should be dropped immediately.
        peer_id: Peer identity string (hash of node pubkey, etc.) if known.
        remote: Remote socket address / multiaddr string if known.
        cause: Underlying exception (not serialized by default).
        details: Extra structured context (topic, msg_id, frame_seq, etc.).
    """

    message: str
    code: str = P2PErrorCode.GENERIC
    retryable: bool = False
    disconnect: bool = False
    peer_id: Optional[str] = None
    remote: Optional[str] = None
    cause: Optional[BaseException] = None
    details: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Ensure details is a dict (may receive Mapping)
        if not isinstance(self.details, dict) and isinstance(self.details, Mapping):
            self.details = dict(self.details)

    def __str__(self) -> str:
        prefix = f"[{self.code}]"
        peer = f" peer={self.peer_id}" if self.peer_id else ""
        addr = f" remote={self.remote}" if self.remote else ""
        retry = " retryable" if self.retryable else ""
        disc = " disconnect" if self.disconnect else ""
        return f"{prefix} {self.message}{peer}{addr}{retry}{disc}"

    def to_dict(self, include_cause: bool = False) -> Dict[str, Any]:
        """
        Serialize for logs/metrics/JSON-RPC error data.
        `include_cause` adds the repr of the underlying cause (avoid PII).
        """
        d = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "disconnect": self.disconnect,
            "peer_id": self.peer_id,
            "remote": self.remote,
            "details": self.details or {},
        }
        if include_cause and self.cause is not None:
            d["cause"] = repr(self.cause)
        return d

    # Fluent helpers to enrich context
    def with_peer(
        self, peer_id: Optional[str] = None, remote: Optional[str] = None
    ) -> "P2PError":
        self.peer_id = peer_id or self.peer_id
        self.remote = remote or self.remote
        return self

    def with_detail(self, **kwargs: Any) -> "P2PError":
        self.details.update(kwargs)
        return self

    def with_cause(self, exc: BaseException) -> "P2PError":
        self.cause = exc
        return self


@dataclass
class HandshakeError(P2PError):
    """
    Errors encountered while performing the initial secure handshake
    (e.g., Kyber KEM / HKDF key schedule, identity proof, policy/chain mismatch).
    Almost always a disconnect; retryability depends on reason.
    """

    code: str = P2PErrorCode.HANDSHAKE_FAILED
    disconnect: bool = True

    @staticmethod
    def identity_mismatch(peer_algo: str, expected_algo: str) -> "HandshakeError":
        return HandshakeError(
            message="Peer identity algorithm mismatch",
            retryable=False,
            details={"peer_algo": peer_algo, "expected_algo": expected_algo},
        )

    @staticmethod
    def chain_or_policy_mismatch(
        peer_chain: Union[int, str],
        expected_chain: Union[int, str],
        peer_alg_policy_root: str,
        expected_alg_policy_root: str,
    ) -> "HandshakeError":
        return HandshakeError(
            message="Peer chainId/alg-policy mismatch",
            retryable=False,
            details={
                "peer_chain": peer_chain,
                "expected_chain": expected_chain,
                "peer_alg_policy_root": peer_alg_policy_root,
                "expected_alg_policy_root": expected_alg_policy_root,
            },
        )

    @staticmethod
    def cipher_mismatch(negotiated: str, supported: list[str]) -> "HandshakeError":
        return HandshakeError(
            message="No common AEAD/cipher suite",
            retryable=False,
            details={"negotiated": negotiated, "supported": supported},
        )

    @staticmethod
    def transcript_invalid() -> "HandshakeError":
        return HandshakeError(
            message="Handshake transcript hash invalid",
            retryable=False,
        )


@dataclass
class RateLimitError(P2PError):
    """
    Raised when per-peer, per-topic, or global token buckets are exceeded.
    Typically not a disconnect; caller SHOULD back off for `retry_after_seconds`.
    """

    code: str = P2PErrorCode.RATE_LIMITED
    retryable: bool = True
    disconnect: bool = False
    retry_after_seconds: Optional[float] = None
    bucket: Optional[str] = None
    limit_per_sec: Optional[float] = None
    burst: Optional[int] = None

    def __post_init__(self) -> None:
        super().__post_init__()
        # Surface rate fields into details for uniform logging
        if self.retry_after_seconds is not None:
            self.details.setdefault("retry_after_seconds", self.retry_after_seconds)
        if self.bucket is not None:
            self.details.setdefault("bucket", self.bucket)
        if self.limit_per_sec is not None:
            self.details.setdefault("limit_per_sec", self.limit_per_sec)
        if self.burst is not None:
            self.details.setdefault("burst", self.burst)


@dataclass
class ProtocolError(P2PError):
    """
    Wire/protocol violations after handshake: bad frames, schema mismatch,
    unexpected message in state machine, checksum failures, etc.
    These are typically fatal for the connection.
    """

    code: str = P2PErrorCode.PROTOCOL_VIOLATION
    disconnect: bool = True

    @staticmethod
    def bad_frame(reason: str, frame_seq: Optional[int] = None) -> "ProtocolError":
        err = ProtocolError(message=f"Bad frame: {reason}")
        if frame_seq is not None:
            err.details["frame_seq"] = frame_seq
        return err

    @staticmethod
    def invalid_message(
        msg_id: int, reason: str, topic: Optional[str] = None
    ) -> "ProtocolError":
        err = ProtocolError(message=f"Invalid message {msg_id}: {reason}")
        if topic:
            err.details["topic"] = topic
        err.details["msg_id"] = msg_id
        return err

    @staticmethod
    def schema_mismatch(msg_id: int, expected: str, got: str) -> "ProtocolError":
        return ProtocolError(
            message="Message schema mismatch",
            details={"msg_id": msg_id, "expected": expected, "got": got},
        )


def as_error_dict(exc: BaseException, include_cause: bool = False) -> Dict[str, Any]:
    """
    Convert an exception to a structured dict suitable for JSON logs / metrics.
    Unknown exceptions are wrapped as a generic P2PError with minimal context.
    """
    if isinstance(exc, P2PError):
        return exc.to_dict(include_cause=include_cause)
    # Wrap unknown exceptions
    wrapped = P2PError(
        message=str(exc) or exc.__class__.__name__,
        code=P2PErrorCode.GENERIC,
        retryable=False,
        disconnect=True,  # unknown failure at P2P layer → safer to drop
        cause=exc,
    )
    return wrapped.to_dict(include_cause=include_cause)
