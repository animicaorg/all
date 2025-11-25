"""
Animica P2P — Lightweight Kademlia-style Discovery (UDP)
========================================================

- NodeId == peer_id (sha3-256-based 32-byte identifier; hex when on the wire).
- Transport: asyncio UDP with compact CBOR/msgspec if available, else JSON.
- Features:
  * Routing table with 256 prefix buckets (Kademlia-inspired), K=20.
  * Iterative lookups (α=3 concurrency): find_node() and find_peers().
  * Local peer announcements for a *topic* (e.g., "miners", "rpc", contract hash).
  * Graceful operation with NAT: we record sender (ip, port) from packets.
  * No external deps required (msgspec optional for speed).

Security & Scope
----------------
This is a discovery helper — it is best-effort and unauthenticated. Production
nodes should combine it with higher-level identity and rate-limits at the P2P
service. Messages are bounded and ignored if too large.

API (typical use)
-----------------
svc = KademliaService(peer_id_str, port=6767, seeds=[("203.0.113.10", 6767)])
await svc.start()
await svc.bootstrap()
await svc.advertise_peer("miners")    # announce locally under the "miners" topic
nodes = await svc.find_node(target_peer_id_hex)
peers = await svc.find_peers("miners", limit=32)
await svc.close()
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
import socket
import struct
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Deque, Dict, Iterable, List, Optional, Set, Tuple

_LOG = logging.getLogger("p2p.discovery.kademlia")

# --- Optional codec (msgspec) -------------------------------------------------
_ENC = "json"
try:  # pragma: no cover - optional acceleration
    import msgspec  # type: ignore

    _ENC = "msgspec"
    _enc = msgspec.Encoder()
    _dec = msgspec.Decoder(type=dict)
except Exception:  # pragma: no cover - fallback
    _enc = None
    _dec = None


def _encode(obj: Dict[str, Any]) -> bytes:
    if _ENC == "msgspec":
        return _enc.encode(obj)  # type: ignore[union-attr]
    # tiny JSON framing
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _decode(data: bytes) -> Dict[str, Any]:
    if _ENC == "msgspec":
        return _dec.decode(data)  # type: ignore[union-attr]
    return json.loads(data.decode("utf-8", "replace"))


# --- Id / distance helpers ----------------------------------------------------
ID_BYTES = 32
K = 20        # bucket size
ALPHA = 3     # parallelism for lookups
MAX_DATAGRAM = 1200  # bytes
RESP_TIMEOUT = 0.9   # seconds per query
LOOKUP_ROUNDS_MAX = 8


def sha3_256(b: bytes) -> bytes:
    return hashlib.sha3_256(b).digest()


def peer_id_to_bytes(pid: str) -> bytes:
    """
    Accept a hex string (64 nybbles) or an arbitrary string; produce 32 bytes.
    """
    pid = pid.strip()
    try:
        b = bytes.fromhex(pid)
        if len(b) == ID_BYTES:
            return b
    except Exception:
        pass
    return sha3_256(pid.encode("utf-8"))


def bytes_to_hex(b: bytes) -> str:
    return b.hex()


def xor_distance(a: bytes, b: bytes) -> int:
    return int.from_bytes(bytes(x ^ y for x, y in zip(a, b)), "big")


def common_prefix_len(a: bytes, b: bytes) -> int:
    """Number of shared prefix bits in a and b (0..ID_BITS)."""
    x = xor_distance(a, b)
    if x == 0:
        return ID_BYTES * 8
    return (ID_BYTES * 8) - x.bit_length()


def topic_to_id(topic: str) -> bytes:
    """
    Map an arbitrary topic string onto the ID space (stable).
    """
    return sha3_256(topic.encode("utf-8"))


# --- Model types --------------------------------------------------------------
@dataclass(eq=True, frozen=True)
class Node:
    id_hex: str
    host: str
    port: int

    def id_bytes(self) -> bytes:
        return bytes.fromhex(self.id_hex)

    def endpoint(self) -> Tuple[str, int]:
        return (self.host, self.port)


@dataclass
class NodeMeta:
    node: Node
    last_seen: float = field(default_factory=lambda: time.time())
    rtt_ms: Optional[float] = None


# --- Routing table (prefix buckets) ------------------------------------------
class RoutingTable:
    """
    256-bit space routing table with 256 buckets by shared-prefix length with self id.
    Each bucket is an LRU of size K (Kademlia-like behavior).
    """
    def __init__(self, self_id: bytes, k: int = K) -> None:
        self.self_id = self_id
        self.k = k
        self.buckets: List[Deque[NodeMeta]] = [deque() for _ in range(ID_BYTES * 8)]
        self._index_cache: Dict[str, int] = {}

    def _bucket_index(self, node_id: bytes) -> int:
        # bucket index = shared prefix length in bits (0..255)
        return common_prefix_len(self.self_id, node_id)

    def _find_in_bucket(self, bucket: Deque[NodeMeta], id_hex: str) -> Optional[int]:
        for i, meta in enumerate(bucket):
            if meta.node.id_hex == id_hex:
                return i
        return None

    def touch(self, node: Node, rtt_ms: Optional[float] = None) -> None:
        idx = self._bucket_index(node.id_bytes())
        b = self.buckets[idx]
        pos = self._find_in_bucket(b, node.id_hex)
        now = time.time()
        if pos is not None:
            meta = b[pos]
            # move to tail (MRU)
            try:
                del b[pos]
            except Exception:
                # worst case, rebuild without the element
                b = deque(x for j, x in enumerate(b) if j != pos)
                self.buckets[idx] = b
            b.append(NodeMeta(node=node, last_seen=now, rtt_ms=rtt_ms or meta.rtt_ms))
        else:
            if len(b) >= self.k:
                # Evict LRU (left)
                try:
                    b.popleft()
                except IndexError:
                    pass
            b.append(NodeMeta(node=node, last_seen=now, rtt_ms=rtt_ms))

    def closest(self, target: bytes, limit: int) -> List[Node]:
        # Collect from all buckets; sort by XOR distance
        candidates: List[Node] = []
        for b in self.buckets:
            for meta in b:
                candidates.append(meta.node)
        candidates.sort(key=lambda n: xor_distance(n.id_bytes(), target))
        if limit <= 0:
            return candidates
        return candidates[:limit]

    def size(self) -> int:
        return sum(len(b) for b in self.buckets)


# --- UDP protocol -------------------------------------------------------------
class _DiscoveryProto(asyncio.DatagramProtocol):
    def __init__(self, svc: "KademliaService") -> None:
        self.svc = svc

    def datagram_received(self, data: bytes, addr: Tuple[str, int]) -> None:
        if len(data) > MAX_DATAGRAM:
            return
        try:
            msg = _decode(data)
            asyncio.create_task(self.svc._on_message(msg, addr))
        except Exception:
            return

    def error_received(self, exc: Exception) -> None:  # pragma: no cover
        _LOG.debug("UDP error: %s", exc)


# --- Service -----------------------------------------------------------------
class KademliaService:
    """
    Minimal Kademlia-like discovery node.
    """

    def __init__(
        self,
        peer_id: str,
        host: str = "0.0.0.0",
        port: int = 6767,
        seeds: Optional[List[Tuple[str, int]]] = None,
        alpha: int = ALPHA,
        k: int = K,
    ) -> None:
        self.self_id = peer_id_to_bytes(peer_id)
        self.self_hex = bytes_to_hex(self.self_id)
        self.host = host
        self.port = int(port)
        self.alpha = int(alpha)
        self.k = int(k)

        self.router = RoutingTable(self.self_id, k=self.k)
        self.seeds = seeds or []

        self._loop = asyncio.get_event_loop()
        self._transport: Optional[asyncio.DatagramTransport] = None
        self._proto: Optional[_DiscoveryProto] = None

        self._pending: Dict[int, asyncio.Future] = {}
        self._topics_local: Dict[str, float] = {}           # topic -> lastAnnounceTs
        self._topics_seen: Dict[str, Dict[Node, float]] = {}  # topic -> {node: expires_at}
        self._tasks: Set[asyncio.Task] = set()

    # -- lifecycle
    async def start(self) -> None:
        self._proto = _DiscoveryProto(self)
        self._transport, _ = await self._loop.create_datagram_endpoint(
            lambda: self._proto,
            local_addr=(self.host, self.port),
            allow_broadcast=True,
        )
        _LOG.info("Discovery UDP listening on %s:%d (peer_id=%s…)", self.host, self.port, self.self_hex[:8])
        # periodic cleanup
        self._tasks.add(self._loop.create_task(self._janitor()))

    async def close(self) -> None:
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.cancel()
        self._pending.clear()
        for t in list(self._tasks):
            t.cancel()
        if self._transport:
            self._transport.close()
            self._transport = None

    # -- bootstrap
    async def bootstrap(self) -> None:
        if not self.seeds:
            return
        _LOG.info("Bootstrapping via %d seeds…", len(self.seeds))
        # Ping & learn table
        for (h, p) in self.seeds:
            try:
                await self._ping((h, p))
            except asyncio.TimeoutError:
                continue
        # Lookup our own id to fill closest buckets
        try:
            await self.find_node(self.self_hex, limit=self.k)
        except Exception:
            pass

    # -- public API
    async def find_node(self, target_hex: str, limit: int = K) -> List[Node]:
        """Iterative lookup for the closest nodes to `target_hex`."""
        target = bytes.fromhex(target_hex)
        shortlist: Dict[str, Node] = {n.id_hex: n for n in self.router.closest(target, limit=self.k)}
        queried: Set[str] = set()
        best_before = None

        for _ in range(LOOKUP_ROUNDS_MAX):
            # select α closest not yet queried
            ordered = sorted(shortlist.values(), key=lambda n: xor_distance(n.id_bytes(), target))
            to_query = [n for n in ordered if n.id_hex not in queried][: self.alpha]
            if not to_query:
                break
            # send queries
            tasks = [self._find_node_rpc(n.endpoint(), target_hex) for n in to_query]
            queried.update(n.id_hex for n in to_query)
            try:
                results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=RESP_TIMEOUT * 1.5)
            except asyncio.TimeoutError:
                results = []
            changed = False
            for res in results:
                if isinstance(res, list):
                    for nn in res:
                        if nn.id_hex == self.self_hex:
                            continue
                        if nn.id_hex not in shortlist:
                            shortlist[nn.id_hex] = nn
                            changed = True
                            # touch into routing table
                            self.router.touch(nn)
            # convergence check
            best_after = tuple(n.id_hex for n in sorted(shortlist.values(), key=lambda n: xor_distance(n.id_bytes(), target))[:limit])
            if best_after == best_before:
                break
            best_before = best_after

        return list(sorted(shortlist.values(), key=lambda n: xor_distance(n.id_bytes(), target))[:limit])

    async def advertise_peer(self, topic: str, ttl_s: int = 900) -> None:
        """
        Announce (topic -> self) to the DHT; the service also keeps a local index entry.
        """
        now = time.time()
        self._topics_local[topic] = now + ttl_s
        target = topic_to_id(topic)
        closest = await self.find_node(target.hex(), limit=self.k)
        if not closest and self.seeds:
            # no table yet: try seeds directly
            closest = [Node(id_hex="0"*64, host=h, port=p) for (h, p) in self.seeds]
        # best-effort announce to α closest nodes
        tasks = [self._announce_rpc(n.endpoint(), topic) for n in closest[: self.alpha]]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def find_peers(self, topic: str, limit: int = 32) -> List[Node]:
        """
        Iterative topic lookup. Returns up to `limit` peers that have announced `topic`.
        """
        target = topic_to_id(topic)
        shortlist_nodes = await self.find_node(target.hex(), limit=max(self.k, self.alpha * 2))
        found: Dict[str, Node] = {}

        to_query = shortlist_nodes[: self.alpha]
        queried: Set[Tuple[str, int]] = set()
        rounds = 0
        while to_query and rounds < LOOKUP_ROUNDS_MAX and len(found) < limit:
            rounds += 1
            tasks = [self._find_peers_rpc(n.endpoint(), topic) for n in to_query if n.endpoint() not in queried]
            for n in to_query:
                queried.add(n.endpoint())
            try:
                results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=RESP_TIMEOUT * 1.5)
            except asyncio.TimeoutError:
                results = []

            new_nodes: List[Node] = []
            for res in results:
                if isinstance(res, dict):
                    for p in res.get("peers", []):
                        found[p.id_hex] = p
                    for nn in res.get("closer", []):
                        new_nodes.append(nn)
                        self.router.touch(nn)

            # Next wave: pick α closest new nodes we haven't queried
            all_known = shortlist_nodes + new_nodes
            all_known.sort(key=lambda n: xor_distance(n.id_bytes(), target))
            to_query = [n for n in all_known if n.endpoint() not in queried][: self.alpha]

        return list(found.values())[:limit]

    # -- janitor task
    async def _janitor(self) -> None:  # pragma: no cover - scheduling dependent
        try:
            while True:
                now = time.time()
                # expire topic observations
                for topic, m in list(self._topics_seen.items()):
                    for node, exp in list(m.items()):
                        if exp < now:
                            del m[node]
                    if not m:
                        del self._topics_seen[topic]
                # refresh local announcements (half-life reannounce)
                for topic, exp in list(self._topics_local.items()):
                    if (exp - now) < 450:  # reannounce if less than 7.5 minutes left of a 15-minute default
                        try:
                            await self.advertise_peer(topic)
                        except Exception:
                            pass
                        # push expiry out
                        self._topics_local[topic] = time.time() + 900
                await asyncio.sleep(30)
        except asyncio.CancelledError:
            return

    # --- RPC client helpers ---------------------------------------------------
    async def _ping(self, endpoint: Tuple[str, int]) -> None:
        await self._rpc(endpoint, "PING", {})

    async def _find_node_rpc(self, endpoint: Tuple[str, int], target_hex: str) -> List[Node]:
        resp = await self._rpc(endpoint, "FIND_NODE", {"target": target_hex})
        nodes = []
        for it in resp.get("nodes", []):
            try:
                nodes.append(Node(id_hex=str(it["id"]), host=str(it["h"]), port=int(it["p"])))
            except Exception:
                continue
        return nodes

    async def _announce_rpc(self, endpoint: Tuple[str, int], topic: str) -> None:
        await self._rpc(endpoint, "ANNOUNCE", {"topic": topic})

    async def _find_peers_rpc(self, endpoint: Tuple[str, int], topic: str) -> Dict[str, List[Node]]:
        resp = await self._rpc(endpoint, "FIND_PEERS", {"topic": topic})
        peers = []
        for it in resp.get("peers", []):
            try:
                peers.append(Node(id_hex=str(it["id"]), host=str(it["h"]), port=int(it["p"])))
            except Exception:
                continue
        closer = []
        for it in resp.get("closer", []):
            try:
                closer.append(Node(id_hex=str(it["id"]), host=str(it["h"]), port=int(it["p"])))
            except Exception:
                continue
        return {"peers": peers, "closer": closer}

    async def _rpc(self, endpoint: Tuple[str, int], typ: str, q: Dict[str, Any]) -> Dict[str, Any]:
        if not self._transport:
            raise RuntimeError("Service not started")
        req = secrets.randbits(63)
        fut: asyncio.Future = self._loop.create_future()
        self._pending[req] = fut
        msg = {
            "v": 1,
            "t": typ,
            "id": self.self_hex,
            "req": req,
            "q": q,
        }
        try:
            self._transport.sendto(_encode(msg), endpoint)
            return await asyncio.wait_for(fut, timeout=RESP_TIMEOUT)
        finally:
            self._pending.pop(req, None)

    # --- Server handlers ------------------------------------------------------
    async def _on_message(self, msg: Dict[str, Any], addr: Tuple[str, int]) -> None:
        # Common parse
        if not isinstance(msg, dict):
            return
        typ = msg.get("t")
        their_id_hex = str(msg.get("id", ""))
        req = msg.get("req")
        q = msg.get("q") or {}
        # Learn contact
        try:
            node = Node(id_hex=their_id_hex, host=addr[0], port=addr[1])
            if their_id_hex and len(their_id_hex) == 64:
                self.router.touch(node)
        except Exception:
            node = None  # type: ignore
        # Response correlation
        if typ in ("PONG", "NODES", "PEERS", "ACK") and isinstance(req, int):
            fut = self._pending.get(req)
            if fut and not fut.done():
                # Normalize payload
                if typ == "PONG" or typ == "ACK":
                    fut.set_result({})
                elif typ == "NODES":
                    fut.set_result({"nodes": msg.get("nodes", [])})
                elif typ == "PEERS":
                    fut.set_result({"peers": msg.get("peers", []), "closer": msg.get("closer", [])})
            return

        # Handle requests
        if typ == "PING":
            await self._reply(addr, "PONG", req, {})
            return

        if typ == "FIND_NODE":
            target_hex = str(q.get("target", ""))
            try:
                target = bytes.fromhex(target_hex)
            except Exception:
                target = sha3_256(b"")
            nodes = [
                {"id": n.id_hex, "h": n.host, "p": n.port}
                for n in self.router.closest(target, limit=self.k)
            ]
            await self._reply(addr, "NODES", req, {"nodes": nodes})
            return

        if typ == "ANNOUNCE":
            topic = str(q.get("topic", ""))
            if topic:
                # Index sender under topic (15 min TTL)
                self._topics_seen.setdefault(topic, {})[node] = time.time() + 900  # type: ignore[arg-type]
            await self._reply(addr, "ACK", req, {})
            return

        if typ == "FIND_PEERS":
            topic = str(q.get("topic", ""))
            # Return known local peers + suggest closer nodes for continued lookup
            peers = []
            now = time.time()
            for (n, exp) in (self._topics_seen.get(topic, {}) or {}).items():
                if exp >= now:
                    peers.append({"id": n.node.id_hex if isinstance(n, NodeMeta) else n.id_hex,  # type: ignore[union-attr]
                                  "h": (n.node.host if isinstance(n, NodeMeta) else n.host),    # type: ignore[union-attr]
                                  "p": int(n.node.port if isinstance(n, NodeMeta) else n.port)})  # type: ignore[union-attr]
            target = topic_to_id(topic)
            closer = [
                {"id": n.id_hex, "h": n.host, "p": n.port}
                for n in self.router.closest(target, limit=self.alpha * 2)
            ]
            await self._reply(addr, "PEERS", req, {"peers": peers, "closer": closer})
            return

        # Unknown type: ignore silently

    async def _reply(self, addr: Tuple[str, int], typ: str, req: Optional[int], payload: Dict[str, Any]) -> None:
        if not self._transport:
            return
        msg = {"v": 1, "t": typ, "id": self.self_hex, "req": req, **payload}
        try:
            self._transport.sendto(_encode(msg), addr)
        except Exception:
            pass


__all__ = [
    "KademliaService",
    "RoutingTable",
    "Node",
    "xor_distance",
    "topic_to_id",
]
