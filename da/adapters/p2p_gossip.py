from __future__ import annotations

"""
Animica • DA • P2P Gossip Adapter
=================================

Thin publish/subscribe helpers for Data Availability (DA) messages carried over
the node's P2P stack.

This module does **not** implement a P2P network by itself. Instead, it defines
a minimal async transport protocol that the real P2P layer implements, and
provides convenient, type-safe helpers to:

- Announce new **blob commitments** (per-blob NMT roots).
- Publish **DAS sample responses** (indices + proof branches) for a commitment.
- Subscribe to those topics and receive parsed messages.

Topics
------
We use canonical topic strings from :mod:`da.adapters.p2p_topics`. Example:

  - Commitments:  animica/da/v1/chain/1337/commitment
  - Samples:      animica/da/v1/chain/1337/samples/ns/24

Transport contract
------------------
The transport only moves opaque bytes. We serialize messages as compact JSON
(UTF-8) by default; callers can switch to a different codec by replacing the
`encode_msg` / `decode_msg` functions if needed.

A tiny in-memory `LocalBusTransport` is provided for tests and demos.

"""

from dataclasses import dataclass, asdict
from typing import (
    Awaitable,
    Callable,
    Coroutine,
    Dict,
    List,
    Literal,
    Optional,
    Protocol,
    Tuple,
    Union,
)
import asyncio
import json

from da.errors import DAError
from da.utils.bytes import bytes_to_hex, hex_to_bytes
from da.adapters.p2p_topics import (
    VERSION as TOPIC_VERSION,
    commitment_topic,
    samples_topic,
    parse_topic,
)


# =============================================================================
# Message shapes
# =============================================================================

MsgKind = Literal["commitment", "samples"]

@dataclass(frozen=True)
class CommitmentMsg:
    kind: Literal["commitment"]
    version: str
    chain_id: int
    namespace: int
    commitment: str  # 0x-hex (32 bytes)
    size: int        # original blob size in bytes

    @staticmethod
    def build(chain_id: int, namespace: int, commitment: bytes, size: int) -> "CommitmentMsg":
        if not isinstance(commitment, (bytes, bytearray)) or len(commitment) != 32:
            raise DAError("commitment must be 32 bytes")
        if not (0 <= namespace <= 0xFFFFFFFF):
            raise DAError("namespace must be uint32")
        if size < 0:
            raise DAError("size must be non-negative")
        return CommitmentMsg(
            kind="commitment",
            version=TOPIC_VERSION,
            chain_id=int(chain_id),
            namespace=int(namespace),
            commitment=bytes_to_hex(bytes(commitment)),
            size=int(size),
        )


@dataclass(frozen=True)
class SamplesMsg:
    kind: Literal["samples"]
    version: str
    chain_id: int
    namespace: int
    commitment: str   # 0x-hex (32 bytes) the blob commitment samples refer to
    indices: List[int]
    # Proof branches serialized as 0x-hex for transport (format is DA-implementation specific).
    branches: List[str]

    @staticmethod
    def build(
        chain_id: int,
        namespace: int,
        commitment: bytes,
        indices: List[int],
        branches: List[bytes],
    ) -> "SamplesMsg":
        if not isinstance(commitment, (bytes, bytearray)) or len(commitment) != 32:
            raise DAError("commitment must be 32 bytes")
        if not (0 <= namespace <= 0xFFFFFFFF):
            raise DAError("namespace must be uint32")
        if any(i < 0 for i in indices):
            raise DAError("sample indices must be non-negative")
        return SamplesMsg(
            kind="samples",
            version=TOPIC_VERSION,
            chain_id=int(chain_id),
            namespace=int(namespace),
            commitment=bytes_to_hex(bytes(commitment)),
            indices=[int(i) for i in indices],
            branches=[bytes_to_hex(b) for b in branches],
        )


Message = Union[CommitmentMsg, SamplesMsg]


# =============================================================================
# Encoding (transport payloads)
# =============================================================================

def encode_msg(msg: Message) -> bytes:
    """
    Serialize a message to bytes for transport. Defaults to JSON (UTF-8).
    """
    return json.dumps(asdict(msg), separators=(",", ":"), sort_keys=True).encode("utf-8")


def decode_msg(payload: bytes) -> Message:
    """
    Parse a transport payload back into a message dataclass.
    """
    try:
        obj = json.loads(payload.decode("utf-8"))
        kind = obj.get("kind")
        if kind == "commitment":
            return CommitmentMsg(
                kind="commitment",
                version=str(obj["version"]),
                chain_id=int(obj["chain_id"]),
                namespace=int(obj["namespace"]),
                commitment=str(obj["commitment"]),
                size=int(obj["size"]),
            )
        if kind == "samples":
            return SamplesMsg(
                kind="samples",
                version=str(obj["version"]),
                chain_id=int(obj["chain_id"]),
                namespace=int(obj["namespace"]),
                commitment=str(obj["commitment"]),
                indices=[int(i) for i in obj["indices"]],
                branches=[str(b) for b in obj["branches"]],
            )
    except Exception as e:
        raise DAError(f"failed to decode DA gossip message: {e}") from e
    raise DAError(f"unknown DA gossip message kind: {obj!r}")


# =============================================================================
# Transport protocol
# =============================================================================

class P2PTransport(Protocol):
    """
    Minimal async pub/sub transport. Concrete P2P layers should implement this.

    - `publish(topic, payload)` MUST deliver the payload to all current
       subscribers of the topic (best-effort in real networks).
    - `subscribe(topic, handler)` MUST register a callback; it returns a callable
       that *unsubscribes* the handler when invoked.
    """

    async def publish(self, topic: str, payload: bytes) -> None: ...
    def subscribe(self, topic: str, handler: Callable[[bytes], Awaitable[None]]) -> Callable[[], None]: ...


class LocalBusTransport(P2PTransport):
    """
    Tiny in-memory event bus suitable for unit tests and demos.
    """

    def __init__(self) -> None:
        self._subs: Dict[str, List[Callable[[bytes], Awaitable[None]]]] = {}
        self._lock = asyncio.Lock()

    def subscribe(self, topic: str, handler: Callable[[bytes], Awaitable[None]]) -> Callable[[], None]:
        self._subs.setdefault(topic, []).append(handler)
        def _unsub() -> None:
            handlers = self._subs.get(topic, [])
            try:
                handlers.remove(handler)
            except ValueError:
                pass
        return _unsub

    async def publish(self, topic: str, payload: bytes) -> None:
        async with self._lock:
            handlers = list(self._subs.get(topic, []))
        # Fanout without holding the lock; deliver concurrently but don't fail on one handler error.
        async def _deliver(h: Callable[[bytes], Awaitable[None]]) -> None:
            try:
                await h(payload)
            except Exception:
                # Swallow to avoid breaking other subscribers in tests; real transports should log.
                pass
        await asyncio.gather(*(_deliver(h) for h in handlers), return_exceptions=True)


# =============================================================================
# High-level publish helpers
# =============================================================================

async def publish_commitment(
    transport: P2PTransport,
    *,
    chain_id: int,
    namespace: int,
    commitment: bytes,
    size: int,
) -> str:
    """
    Publish a commitment announcement on the canonical topic.
    Returns the topic used.
    """
    topic = commitment_topic(chain_id=chain_id)
    msg = CommitmentMsg.build(chain_id=chain_id, namespace=namespace, commitment=commitment, size=size)
    await transport.publish(topic, encode_msg(msg))
    return topic


async def publish_samples(
    transport: P2PTransport,
    *,
    chain_id: int,
    namespace: int,
    commitment: bytes,
    indices: List[int],
    branches: List[bytes],
) -> str:
    """
    Publish DAS sample responses (indices + proof branches) for a blob commitment.
    Returns the topic used.
    """
    topic = samples_topic(chain_id=chain_id, namespace=namespace)
    msg = SamplesMsg.build(
        chain_id=chain_id,
        namespace=namespace,
        commitment=commitment,
        indices=indices,
        branches=branches,
    )
    await transport.publish(topic, encode_msg(msg))
    return topic


# =============================================================================
# High-level subscribe helpers
# =============================================================================

def subscribe_commitments(
    transport: P2PTransport,
    *,
    chain_id: int,
    handler: Callable[[CommitmentMsg], Awaitable[None]],
) -> Callable[[], None]:
    """
    Subscribe to commitment announcements for a given chain.

    Returns an `unsubscribe()` callable.
    """
    topic = commitment_topic(chain_id=chain_id)

    async def _on_payload(payload: bytes) -> None:
        msg = decode_msg(payload)
        if isinstance(msg, CommitmentMsg) and msg.chain_id == chain_id:
            await handler(msg)
        # else ignore

    return transport.subscribe(topic, _on_payload)


def subscribe_samples(
    transport: P2PTransport,
    *,
    chain_id: int,
    namespace: Optional[int] = None,
    handler: Callable[[SamplesMsg], Awaitable[None]],
) -> Callable[[], None]:
    """
    Subscribe to sample responses. If `namespace` is provided, subscribe to the
    namespace-scoped topic; otherwise subscribe to all namespaces (less common).
    """
    topic = samples_topic(chain_id=chain_id, namespace=namespace)

    async def _on_payload(payload: bytes) -> None:
        msg = decode_msg(payload)
        if isinstance(msg, SamplesMsg) and msg.chain_id == chain_id:
            if namespace is None or msg.namespace == namespace:
                await handler(msg)

    return transport.subscribe(topic, _on_payload)


__all__ = [
    "MsgKind",
    "CommitmentMsg",
    "SamplesMsg",
    "Message",
    "encode_msg",
    "decode_msg",
    "P2PTransport",
    "LocalBusTransport",
    "publish_commitment",
    "publish_samples",
    "subscribe_commitments",
    "subscribe_samples",
]
