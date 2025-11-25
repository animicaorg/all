from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Protocol, Tuple


__all__ = [
    "PeerView",
    "GossipTopicView",
    "HealthSnapshot",
    "HealthMonitor",
    "health_snapshot",
]


# ---------------------------------------------------------------------------
# Lightweight protocols to decouple from concrete implementations
# ---------------------------------------------------------------------------

class PPeer(Protocol):
    id: str
    addr: str
    inbound: bool
    agent: Optional[str]
    roles: Tuple[str, ...]  # e.g. ("full", "miner")
    rtt_ms_avg: Optional[float]
    score: Optional[float]
    topics: Tuple[str, ...]
    last_seen_unix: Optional[int]
    bytes_in: int
    bytes_out: int


class PPeerStore(Protocol):
    def list_peers(self) -> Iterable[PPeer]: ...


class PConnManager(Protocol):
    def num_dialing(self) -> int: ...
    def num_open(self) -> int: ...
    def num_backoff(self) -> int: ...


class PGossipEngine(Protocol):
    def topics(self) -> Iterable[str]: ...
    def topic_stats(self, topic: str) -> Mapping[str, Any]: ...
    # topic_stats example keys:
    #   subscribers:int, fanout:int, mesh:int, in_q:int, out_q:int, dropped:int


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

@dataclass
class PeerView:
    peer_id: str
    addr: str
    inbound: bool
    agent: Optional[str]
    roles: List[str]
    rtt_ms: Optional[float]
    score: Optional[float]
    topics: List[str]
    last_seen_unix: Optional[int]
    bytes_in: int
    bytes_out: int


@dataclass
class GossipTopicView:
    topic: str
    subscribers: int
    fanout: int
    mesh: int
    in_q: int
    out_q: int
    dropped: int


@dataclass
class HealthSnapshot:
    ts_unix: int
    peers_total: int
    peers_inbound: int
    peers_outbound: int
    peers_dialing: int
    peers_backoff: int
    peers_open: int
    rtt_ms_avg: Optional[float]
    bytes_in_total: int
    bytes_out_total: int
    topics_total: int
    peers: List[PeerView] = field(default_factory=list)
    topics: List[GossipTopicView] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"), sort_keys=True)


# ---------------------------------------------------------------------------
# Monitor
# ---------------------------------------------------------------------------

class HealthMonitor:
    """
    Aggregates a point-in-time health snapshot of the P2P node. Works against
    abstract protocols so the rest of the system can evolve independently.
    """

    def __init__(
        self,
        *,
        peerstore: PPeerStore,
        connmgr: Optional[PConnManager] = None,
        gossip: Optional[PGossipEngine] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._peerstore = peerstore
        self._connmgr = connmgr
        self._gossip = gossip
        self._extra = extra or {}

    def sample(self) -> HealthSnapshot:
        now = int(time.time())

        peers: List[PeerView] = []
        inbound = outbound = 0
        rtts: List[float] = []
        bin_total = bout_total = 0

        for p in self._peerstore.list_peers():
            inbound += int(p.inbound)
            outbound += int(not p.inbound)
            if p.rtt_ms_avg is not None:
                rtts.append(p.rtt_ms_avg)
            bin_total += int(getattr(p, "bytes_in", 0))
            bout_total += int(getattr(p, "bytes_out", 0))
            peers.append(
                PeerView(
                    peer_id=p.id,
                    addr=p.addr,
                    inbound=p.inbound,
                    agent=getattr(p, "agent", None),
                    roles=list(getattr(p, "roles", ()) or ()),
                    rtt_ms=p.rtt_ms_avg,
                    score=getattr(p, "score", None),
                    topics=list(getattr(p, "topics", ()) or ()),
                    last_seen_unix=getattr(p, "last_seen_unix", None),
                    bytes_in=int(getattr(p, "bytes_in", 0)),
                    bytes_out=int(getattr(p, "bytes_out", 0)),
                )
            )

        rtt_avg: Optional[float] = (sum(rtts) / len(rtts)) if rtts else None

        # Connection manager figures
        dialing = backoff = open_ = 0
        if self._connmgr is not None:
            try:
                dialing = int(self._connmgr.num_dialing())
                backoff = int(self._connmgr.num_backoff())
                open_ = int(self._connmgr.num_open())
            except Exception:
                # tolerate partial implementations
                pass

        # Gossip topics
        topics_views: List[GossipTopicView] = []
        topics_total = 0
        if self._gossip is not None:
            try:
                for t in self._gossip.topics():
                    stats = dict(self._gossip.topic_stats(t))
                    topics_total += 1
                    topics_views.append(
                        GossipTopicView(
                            topic=t,
                            subscribers=int(stats.get("subscribers", 0)),
                            fanout=int(stats.get("fanout", 0)),
                            mesh=int(stats.get("mesh", 0)),
                            in_q=int(stats.get("in_q", 0)),
                            out_q=int(stats.get("out_q", 0)),
                            dropped=int(stats.get("dropped", 0)),
                        )
                    )
            except Exception:
                # best-effort
                pass

        snapshot = HealthSnapshot(
            ts_unix=now,
            peers_total=len(peers),
            peers_inbound=inbound,
            peers_outbound=outbound,
            peers_dialing=dialing,
            peers_backoff=backoff,
            peers_open=open_,
            rtt_ms_avg=rtt_avg,
            bytes_in_total=bin_total,
            bytes_out_total=bout_total,
            topics_total=topics_total,
            peers=peers,
            topics=topics_views,
            extra=self._extra.copy(),
        )
        return snapshot


# ---------------------------------------------------------------------------
# Convenience
# ---------------------------------------------------------------------------

def health_snapshot(
    *,
    peerstore: PPeerStore,
    connmgr: Optional[PConnManager] = None,
    gossip: Optional[PGossipEngine] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """One-shot helper that returns a dict (JSON-ready)."""
    return HealthMonitor(peerstore=peerstore, connmgr=connmgr, gossip=gossip, extra=extra).sample().to_dict()


# ---------------------------------------------------------------------------
# CLI (developer aid)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Minimal fake objects so you can run: python -m p2p.node.health
    class _Peer:
        def __init__(self, i: int) -> None:
            self.id = f"peer{i}"
            self.addr = f"127.0.0.1:{3000+i}"
            self.inbound = (i % 2) == 0
            self.agent = "animica/test"
            self.roles = ("full",)
            self.rtt_ms_avg = 20.0 + i
            self.score = 100 - i
            self.topics = ("blocks", "txs") if i % 2 == 0 else ("headers",)
            self.last_seen_unix = int(time.time()) - i * 7
            self.bytes_in = 1000 * i
            self.bytes_out = 800 * i

    class _PeerStore:
        def list_peers(self) -> Iterable[PPeer]:
            return [_Peer(i) for i in range(5)]

    class _ConnMgr:
        def num_dialing(self) -> int: return 1
        def num_open(self) -> int: return 5
        def num_backoff(self) -> int: return 0

    class _Gossip:
        def topics(self) -> Iterable[str]:
            return ["blocks", "headers", "txs"]
        def topic_stats(self, topic: str) -> Mapping[str, Any]:
            base = {"subscribers": 4, "fanout": 2, "mesh": 3, "in_q": 1, "out_q": 0, "dropped": 0}
            if topic == "txs":
                base.update(in_q=3, out_q=2)
            return base

    snap = health_snapshot(peerstore=_PeerStore(), connmgr=_ConnMgr(), gossip=_Gossip(), extra={"chainId": 1})
    print(json.dumps(snap, indent=2, sort_keys=True))
