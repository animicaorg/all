from __future__ import annotations

"""
Transaction relay protocol: payload shapes + lightweight admission & dedupe.

This module defines compact wire payloads for tx relay and a small "relay gate"
that performs:
  • stateless admission checks (size bounds, minimal CBOR sanity via length)
  • dedupe via a rolling Bloom filter (to avoid rebroadcast storms)
  • optional host-provided fast-verify callback (e.g., PQ-sig precheck)

Wire framing (message ids, AEAD, etc.) is handled by p2p.wire.*.  Here we only
encode/decode the message *payloads* with a local type tag.

Messages
--------
- TxInv        (TAG_TX_INV):    announce a set of tx hashes (sha3-256, 32B each)
- TxGetData    (TAG_TX_GET):    ask for a set of tx bodies by hash
- TxBodies     (TAG_TX_BODIES): respond with CBOR-encoded tx bodies (raw bytes)

Dedupe
------
We use a RollingBloom (N generations of Bloom filters) so entries age out
naturally without expensive deletes.  Hosts should call .rotate() periodically
(e.g., every M seconds or every K blocks) to advance the window.

This module intentionally avoids importing mempool/ or core/ to keep the P2P
surface trim.  Integrations can plug a verify callback that mirrors the fast
stateless path in mempool/validate.py if desired.

"""

import hashlib
import os
import struct
import time
from dataclasses import dataclass
from typing import Callable, Iterable, List, Optional

try:
    import msgspec  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    msgspec = None  # type: ignore

# ---- Constants (fallbacks; prefer p2p.constants if available) ----------------
try:
    from p2p.constants import HASH_LEN, MAX_TX_BODIES, MAX_TX_BYTES, MAX_TX_INV
except Exception:  # pragma: no cover
    HASH_LEN = 32
    MAX_TX_INV = 4096  # max hashes per inv/getdata
    MAX_TX_BODIES = 1024  # max bodies per TxBodies
    MAX_TX_BYTES = 512 * 1024  # 512 KiB hard cap for individual tx body

# ---- Local payload tags (not global wire msg_ids) ----------------------------
TAG_TX_INV = 20
TAG_TX_GET = 21
TAG_TX_BODIES = 22


# ---- Errors ------------------------------------------------------------------
class ProtocolError(Exception):
    pass


class AdmissionError(Exception):
    pass


# ---- Message structs ---------------------------------------------------------
if msgspec is not None:

    class _TxInvS(msgspec.Struct, omit_defaults=True):  # type: ignore[misc,attr-defined]
        """TxInv: announce a batch of tx hashes (32-byte sha3-256)."""

        t: int
        ids: List[bytes]

    class _TxGetDataS(msgspec.Struct, omit_defaults=True):  # type: ignore[misc,attr-defined]
        """TxGetData: ask for tx bodies by hash."""

        t: int
        ids: List[bytes]

    class _TxBodiesS(msgspec.Struct, omit_defaults=True):  # type: ignore[misc,attr-defined]
        """TxBodies: raw CBOR-encoded bodies, in request order (missing items omitted)."""

        t: int
        tx: List[bytes]

else:  # pragma: no cover - fallback
    _TxInvS = _TxGetDataS = _TxBodiesS = object  # type: ignore


if msgspec is not None:
    ENC = msgspec.msgpack.Encoder()  # type: ignore[attr-defined]
    DEC_INV = msgspec.msgpack.Decoder(type=_TxInvS)  # type: ignore[attr-defined]
    DEC_GET = msgspec.msgpack.Decoder(type=_TxGetDataS)  # type: ignore[attr-defined]
    DEC_BOD = msgspec.msgpack.Decoder(type=_TxBodiesS)  # type: ignore[attr-defined]
else:  # pragma: no cover - fallback for minimal environments
    from types import SimpleNamespace

    try:
        from core.encoding.cbor import dumps as _cbor_dumps
        from core.encoding.cbor import loads as _cbor_loads
    except Exception:  # pragma: no cover
        import cbor2

        _cbor_dumps = cbor2.dumps
        _cbor_loads = cbor2.loads

    class _Enc:
        def encode(self, obj):
            if hasattr(obj, "__dict__"):
                return _cbor_dumps(obj.__dict__)
            return _cbor_dumps(obj)

    class _Dec:
        def __init__(self, tag: int):
            self._tag = tag

        def decode(self, data: bytes):
            d = _cbor_loads(data)
            if not isinstance(d, dict):
                raise ProtocolError("fallback decode expected map")
            return SimpleNamespace(**d)

    ENC = _Enc()
    DEC_INV = _Dec(TAG_TX_INV)
    DEC_GET = _Dec(TAG_TX_GET)
    DEC_BOD = _Dec(TAG_TX_BODIES)


# ---- Hashing helper ----------------------------------------------------------
def tx_hash(tx_cbor: bytes) -> bytes:
    """
    Canonical tx hash used on the wire: sha3-256 over the CBOR-encoded tx bytes.
    (Core may also expose a typed Tx->hash; this helper matches the raw-wire form.)
    """
    return hashlib.sha3_256(tx_cbor).digest()


def _check_hash_list(ids: Iterable[bytes], limit: int, tag: str) -> List[bytes]:
    out: List[bytes] = []
    for h in ids:
        if not isinstance(h, (bytes, bytearray)) or len(h) != HASH_LEN:
            raise ProtocolError(f"{tag}: bad hash length (expected {HASH_LEN})")
        out.append(bytes(h))
        if len(out) > limit:
            raise ProtocolError(f"{tag}: too many ids (>{limit})")
    return out


# ---- Builders / Parsers ------------------------------------------------------
def build_tx_inv(ids: Iterable[bytes]) -> bytes:
    ids_l = _check_hash_list(ids, MAX_TX_INV, "TxInv")
    return ENC.encode(_TxInvS(t=TAG_TX_INV, ids=ids_l))


def parse_tx_inv(data: bytes) -> List[bytes]:
    m = DEC_INV.decode(data)
    if m.t != TAG_TX_INV:
        raise ProtocolError("TxInv tag mismatch")
    return _check_hash_list(m.ids, MAX_TX_INV, "TxInv")


def build_tx_get(ids: Iterable[bytes]) -> bytes:
    ids_l = _check_hash_list(ids, MAX_TX_INV, "TxGetData")
    return ENC.encode(_TxGetDataS(t=TAG_TX_GET, ids=ids_l))


def parse_tx_get(data: bytes) -> List[bytes]:
    m = DEC_GET.decode(data)
    if m.t != TAG_TX_GET:
        raise ProtocolError("TxGetData tag mismatch")
    return _check_hash_list(m.ids, MAX_TX_INV, "TxGetData")


def build_tx_bodies(bodies: Iterable[bytes]) -> bytes:
    tx_list = [bytes(b) for b in bodies]
    if len(tx_list) > MAX_TX_BODIES:
        raise ProtocolError("TxBodies has too many items")
    for b in tx_list:
        if not isinstance(b, (bytes, bytearray)):
            raise ProtocolError("TxBodies contains non-bytes")
        if len(b) == 0 or len(b) > MAX_TX_BYTES:
            raise ProtocolError("TxBodies includes empty or oversized body")
    return ENC.encode(_TxBodiesS(t=TAG_TX_BODIES, tx=tx_list))


def parse_tx_bodies(data: bytes) -> List[bytes]:
    m = DEC_BOD.decode(data)
    if m.t != TAG_TX_BODIES:
        raise ProtocolError("TxBodies tag mismatch")
    out = []
    for b in m.tx:
        if len(b) == 0 or len(b) > MAX_TX_BYTES:
            raise ProtocolError("TxBodies body size out of range")
        out.append(bytes(b))
        if len(out) > MAX_TX_BODIES:
            raise ProtocolError("TxBodies item limit exceeded")
    return out


# ---- Bloom filters -----------------------------------------------------------
def _mix64(x: int) -> int:
    """A fast 64-bit mixer (splitmix64-ish) for bloom hash derivation."""
    x = (x + 0x9E3779B97F4A7C15) & 0xFFFFFFFFFFFFFFFF
    x = (x ^ (x >> 30)) * 0xBF58476D1CE4E5B9 & 0xFFFFFFFFFFFFFFFF
    x = (x ^ (x >> 27)) * 0x94D049BB133111EB & 0xFFFFFFFFFFFFFFFF
    return x ^ (x >> 31)


class Bloom:
    """
    Simple Bloom filter: m bits, k hash functions (derived from sha3-256 seed).
    Optimized for small memory and speed. Not thread-safe (callers should guard).
    """

    __slots__ = ("m_bits", "k", "_arr")

    def __init__(self, m_bits: int, k: int) -> None:
        if m_bits <= 0 or k <= 0:
            raise ValueError("Bloom requires positive m_bits and k")
        self.m_bits = int(m_bits)
        self.k = int(k)
        self._arr = bytearray((m_bits + 7) // 8)

    def _indexes(self, h: bytes) -> Iterable[int]:
        # Use sha3-256 → two 64-bit seeds, derive k positions by mix.
        d = hashlib.sha3_256(h).digest()
        s1 = struct.unpack_from(">Q", d, 0)[0]
        s2 = struct.unpack_from(">Q", d, 8)[0] ^ struct.unpack_from(">Q", d, 16)[0]
        for i in range(self.k):
            # 128-bit like progression via mixed sums
            v = _mix64(s1 + i * 0x9E3779B97F4A7C15) ^ _mix64(
                s2 + i * 0xBF58476D1CE4E5B9
            )
            yield v % self.m_bits

    def add(self, h: bytes) -> None:
        for idx in self._indexes(h):
            self._arr[idx >> 3] |= 1 << (idx & 7)

    def contains(self, h: bytes) -> bool:
        for idx in self._indexes(h):
            if not (self._arr[idx >> 3] & (1 << (idx & 7))):
                return False
        return True


class RollingBloom:
    """
    Rolling Bloom composed of N generations; membership is the union across all.
    Call rotate() periodically to age out old entries.
    """

    def __init__(self, m_bits: int, k: int, generations: int = 3) -> None:
        if generations < 1:
            raise ValueError("generations must be >= 1")
        self._gens = [Bloom(m_bits, k) for _ in range(generations)]
        self._head = 0

    def add(self, h: bytes) -> None:
        self._gens[self._head].add(h)

    def contains(self, h: bytes) -> bool:
        return any(g.contains(h) for g in self._gens)

    def rotate(self) -> None:
        self._head = (self._head + 1) % len(self._gens)
        # Drop oldest by replacing with a fresh empty Bloom of identical shape
        shape = (self._gens[self._head].m_bits, self._gens[self._head].k)
        self._gens[self._head] = Bloom(*shape)


# ---- Relay gate --------------------------------------------------------------
@dataclass
class AdmitResult:
    accepted: bool
    reason: Optional[str]
    tx_hash: Optional[bytes]


class TxRelayGate:
    """
    Guard for tx relay:
      - prevents re-announce/re-fetch of seen txs (rolling bloom)
      - performs size checks before allocating resources
      - optional 'verify' callback for fast stateless validation (domain-separated sig precheck)
    """

    def __init__(
        self,
        bloom_m_bits: int = 1_048_576,  # 128 KiB
        bloom_k: int = 7,
        generations: int = 3,
        verify: Optional[Callable[[bytes], bool]] = None,
    ) -> None:
        self.seen = RollingBloom(bloom_m_bits, bloom_k, generations)
        self.verify_cb = verify
        self._last_rotate_at = time.monotonic()
        self._rotate_interval_s = (
            60.0  # default rotation cadence; caller may also call rotate() explicitly
        )

    # -- lifecycle / maintenance --
    def rotate(self) -> None:
        self.seen.rotate()
        self._last_rotate_at = time.monotonic()

    def maybe_rotate(self) -> None:
        if (time.monotonic() - self._last_rotate_at) >= self._rotate_interval_s:
            self.rotate()

    # -- admission paths --
    def admit_inv_hashes(self, ids: Iterable[bytes]) -> List[bytes]:
        """
        Given a batch of announced hashes, return the subset that are *new* to us.
        The returned set is also marked as seen to dampen echo.
        """
        out: List[bytes] = []
        for h in _check_hash_list(ids, MAX_TX_INV, "admit_inv"):
            if not self.seen.contains(h):
                out.append(h)
                self.seen.add(h)
        return out

    def admit_tx_body(self, tx_body: bytes) -> AdmitResult:
        """
        Validate a raw tx body enough to warrant asking mempool/core to parse.
        On success, returns (accepted=True, tx_hash=sha3-256(tx_body)).
        Applies size bounds, optional verify callback, and dedupe.
        """
        blen = len(tx_body)
        if blen == 0:
            return AdmitResult(False, "empty", None)
        if blen > MAX_TX_BYTES:
            return AdmitResult(False, f"oversize>{MAX_TX_BYTES}", None)

        h = tx_hash(tx_body)
        if self.seen.contains(h):
            return AdmitResult(False, "duplicate", h)

        # Optional fast verification (e.g., PQ sig domain precheck).
        if self.verify_cb is not None:
            try:
                ok = bool(self.verify_cb(tx_body))
            except Exception as e:  # pragma: no cover
                return AdmitResult(False, f"verify-exc:{type(e).__name__}", None)
            if not ok:
                return AdmitResult(False, "verify-fail", None)

        # Mark as seen and accept.
        self.seen.add(h)
        return AdmitResult(True, None, h)


# ---- TxRelayHandler ----------------------------------------------------------
import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Iterable

if TYPE_CHECKING:  # pragma: no cover
    from typing import Protocol

    class ConnLike(Protocol):
        remote_addr: str

        async def send_frame(
            self, msg_id: int, payload: bytes, *, acks: bool = False
        ) -> None: ...
        def is_closed(self) -> bool: ...

    class Frame(Protocol):
        msg_id: int
        seq: int
        flags: int
        payload: bytes


log = logging.getLogger("animica.p2p.tx_relay")


@dataclass
class TxRelayHandler:
    """
    Protocol handler for transaction relay (gossip-based).

    Responsibilities:
      • Receive TX_INV/TX_GET/TX_BODIES messages from peers
      • Validate, deduplicate, and admit transactions to local mempool
      • Enforce fee floor, nonce ordering, chain_id, signature validity
      • Publish accepted transactions to gossip mesh
      • Track metrics for relay events (accept/reject/publish)

    This handler integrates with:
      - TxRelayGate for dedupe and fast admission checks
      - p2p.deps.AsyncP2PDeps for mempool admission
      - gossip.GossipEngine for outbound publishing
    """

    cfg: Any  # P2PConfig
    codec: Any  # wire codec for encode/decode
    deps: Any  # AsyncP2PDeps (provides admit_tx)
    gossip: Any  # GossipEngine
    ratelimiter: Any  # PeerRateLimiter

    # Internal state
    _gate: TxRelayGate = field(init=False)
    _metrics: dict = field(default_factory=dict, init=False)
    _subscribed: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        """Initialize the relay gate and metrics."""
        self._gate = TxRelayGate(
            bloom_m_bits=1_048_576,  # 128 KiB
            bloom_k=7,
            generations=3,
        )
        self._metrics = {
            "rx_inv": 0,
            "rx_get": 0,
            "rx_bodies": 0,
            "tx_admitted": 0,
            "tx_duplicate": 0,
            "tx_rejected_oversize": 0,
            "tx_rejected_verify_fail": 0,
            "tx_rejected_mempool": 0,
            "tx_published": 0,
        }
        log.info("TxRelayHandler initialized")

    def msg_ids(self) -> Iterable[int]:
        """Message IDs this handler accepts (wire-level)."""
        # For now, we rely on gossip engine for TX gossip.
        # Direct wire messages (INV/GET/BODIES) can be added later if needed.
        return []

    async def handle(self, conn: "ConnLike", frame: "Frame") -> None:
        """
        Handle a direct protocol message (not currently used; gossip handles TX relay).

        If we later add direct INV/GET/BODIES messages (non-gossip), we'd implement:
          - frame.msg_id == MSG_TX_INV: parse_tx_inv, respond with GET for missing
          - frame.msg_id == MSG_TX_GET: parse_tx_get, respond with BODIES
          - frame.msg_id == MSG_TX_BODIES: parse_tx_bodies, admit to mempool
        """
        log.debug(
            f"Unexpected direct frame msg_id={frame.msg_id} from {conn.remote_addr}"
        )

    async def start(self) -> None:
        """
        Start the handler: subscribe to the txs gossip topic.
        """
        if self._subscribed:
            return

        try:
            # Import topics helper
            from p2p.gossip import topics as gossip_topics

            # Get chain_id from deps
            chain_id = getattr(self.deps, "chain_id", 1337)

            # Build the txs topic path
            tx_topic = gossip_topics.txs(chain_id)

            # Subscribe to gossip
            await self.gossip.subscribe(tx_topic.path)

            # Register our callback for inbound gossip messages
            self.gossip.on_message(self._handle_gossip_tx)

            self._subscribed = True
            log.info(f"TxRelayHandler subscribed to gossip topic: {tx_topic.path}")
        except Exception as e:
            log.error(f"Failed to subscribe to tx gossip topic: {e}", exc_info=True)

    async def stop(self) -> None:
        """Stop the handler and unsubscribe from gossip."""
        if not self._subscribed:
            return

        try:
            from p2p.gossip import topics as gossip_topics

            chain_id = getattr(self.deps, "chain_id", 1337)
            tx_topic = gossip_topics.txs(chain_id)
            await self.gossip.unsubscribe(tx_topic.path)
            self._subscribed = False
            log.info("TxRelayHandler unsubscribed from tx gossip")
        except Exception as e:
            log.error(f"Failed to unsubscribe: {e}", exc_info=True)

    async def _handle_gossip_tx(self, peer_id: str, topic: str, payload: bytes) -> None:
        """
        Handle an incoming transaction from the gossip mesh.

        Args:
            peer_id: Source peer ID
            topic: Gossip topic path
            payload: Raw CBOR-encoded transaction
        """
        self._metrics["rx_bodies"] += 1

        # Rotate bloom filter periodically
        self._gate.maybe_rotate()

        # Fast admission check via gate
        admit_result = self._gate.admit_tx_body(payload)

        if not admit_result.accepted:
            reason = admit_result.reason or "unknown"
            if reason == "duplicate":
                self._metrics["tx_duplicate"] += 1
                log.debug(
                    f"Duplicate tx from {peer_id}, hash={admit_result.tx_hash.hex() if admit_result.tx_hash else 'N/A'}"
                )
            elif "oversize" in reason:
                self._metrics["tx_rejected_oversize"] += 1
                log.warning(f"Oversized tx from {peer_id}: {reason}")
            elif "verify" in reason:
                self._metrics["tx_rejected_verify_fail"] += 1
                log.warning(f"Verification failed for tx from {peer_id}: {reason}")
            else:
                log.debug(f"Rejected tx from {peer_id}: {reason}")
            return

        # Admit to mempool via deps (prefer raw bytes; deps decide how to parse).
        try:
            accepted, reason = await self.deps.admit_tx(payload)
        except Exception as e:
            self._metrics["tx_rejected_mempool"] += 1
            log.error(f"Error admitting tx from {peer_id}: {e}", exc_info=True)
            return

        if accepted:
            self._metrics["tx_admitted"] += 1
            log.info(
                f"Admitted relayed tx from {peer_id}",
                extra={
                    "tx_hash": (
                        admit_result.tx_hash.hex() if admit_result.tx_hash else "N/A"
                    ),
                    "peer": peer_id,
                },
            )
            # Re-announce accepted tx to gossip mesh (best-effort, deduped by gate).
            try:
                await self.publish_local_tx(payload, allow_seen=True)
            except Exception:
                log.debug(
                    "Failed to re-announce relayed tx",
                    extra={
                        "tx_hash": (
                            admit_result.tx_hash.hex() if admit_result.tx_hash else "N/A"
                        ),
                        "peer": peer_id,
                    },
                )
        else:
            self._metrics["tx_rejected_mempool"] += 1
            log.debug(
                f"Mempool rejected tx from {peer_id}: {reason}",
                extra={
                    "tx_hash": (
                        admit_result.tx_hash.hex() if admit_result.tx_hash else "N/A"
                    ),
                    "reason": reason,
                },
            )

    async def publish_local_tx(self, tx_cbor: bytes, *, allow_seen: bool = False) -> bool:
        """
        Publish a locally-submitted transaction to the gossip mesh.

        Args:
            tx_cbor: Raw CBOR-encoded transaction

        Returns:
            True if published successfully, False otherwise
        """
        if not self._subscribed:
            log.warning("Cannot publish tx: handler not subscribed to gossip")
            return False

        try:
            # Check if we've already seen this tx (avoid re-broadcast)
            h = tx_hash(tx_cbor)
            if self._gate.seen.contains(h) and not allow_seen:
                log.debug(f"Skipping publish of already-seen tx {h.hex()[:16]}...")
                return False

            # Mark as seen
            self._gate.seen.add(h)

            # Get topic
            from p2p.gossip import topics as gossip_topics

            chain_id = getattr(self.deps, "chain_id", 1337)
            tx_topic = gossip_topics.txs(chain_id)

            # Publish to gossip mesh
            await self.gossip.publish(tx_topic.path, tx_cbor)

            self._metrics["tx_published"] += 1
            log.info(f"Published local tx to gossip: {h.hex()[:16]}...")
            return True
        except Exception as e:
            log.error(f"Failed to publish local tx: {e}", exc_info=True)
            return False

    def get_metrics(self) -> dict:
        """Return current metrics snapshot."""
        return dict(self._metrics)


# ---- Public exports ----------------------------------------------------------
__all__ = [
    # tags
    "TAG_TX_INV",
    "TAG_TX_GET",
    "TAG_TX_BODIES",
    # builders/parsers
    "build_tx_inv",
    "parse_tx_inv",
    "build_tx_get",
    "parse_tx_get",
    "build_tx_bodies",
    "parse_tx_bodies",
    # hashing
    "tx_hash",
    # dedupe
    "Bloom",
    "RollingBloom",
    # gate
    "TxRelayGate",
    "AdmitResult",
    # handler
    "TxRelayHandler",
    # errors
    "ProtocolError",
    "AdmissionError",
    # constants
    "HASH_LEN",
    "MAX_TX_INV",
    "MAX_TX_BODIES",
    "MAX_TX_BYTES",
]
