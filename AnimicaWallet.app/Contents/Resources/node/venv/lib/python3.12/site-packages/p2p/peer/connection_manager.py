from __future__ import annotations

import asyncio
import logging
import math
import random
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

try:
    # Preferred typings if present in this repo
    from p2p.transport.base import Conn, Transport  # type: ignore
except Exception:  # pragma: no cover
    # Minimal protocol fallbacks for type-checking without importing the full stack.
    class Conn:  # type: ignore
        remote_addr: str
        is_closed: bool = False

        async def close(self) -> None: ...

    class Transport:  # type: ignore
        async def dial(self, addr: str, timeout: float | None = None) -> Conn: ...
        def name(self) -> str:
            return "transport"


try:
    from p2p.peer.peer import Peer  # type: ignore
except Exception:  # pragma: no cover

    @dataclass
    class Peer:  # type: ignore
        peer_id: str
        address: str
        conn: Conn
        direction: str  # "inbound" | "outbound"
        last_rtt_ms: Optional[float] = None
        last_seen: float = field(default_factory=lambda: time.time())
        meta: Dict[str, Any] = field(default_factory=dict)


try:
    from p2p.peer.ping import ping_once  # type: ignore
except Exception:  # pragma: no cover

    async def ping_once(conn: Conn, timeout: float = 3.0) -> float:
        # Fallback "ping": just sleep a tiny bit and pretend RTT ~ 20ms.
        await asyncio.sleep(0.02)
        return 20.0


try:
    from p2p.peer.identify import perform_identify  # type: ignore
except Exception:  # pragma: no cover

    async def perform_identify(conn: Conn, timeout: float = 5.0) -> Dict[str, Any]:
        # Fallback identify: derive a pseudo id from remote address
        rid = getattr(conn, "remote_addr", "unknown")
        return {"peer_id": f"peer:{rid}", "caps": [], "agent": "unknown", "height": 0}


try:
    from p2p.peer.address_book import AddressBook  # type: ignore
except Exception:  # pragma: no cover
    AddressBook = Any  # type: ignore


@dataclass
class DialBackoff:
    failures: int = 0
    next_try_at: float = 0.0
    total_attempts: int = 0  # Track total connection attempts
    first_failure_at: float = 0.0  # Track when failures started
    last_success_at: float = 0.0  # Track last successful connection

    def mark_success(self) -> None:
        self.failures = 0
        self.next_try_at = 0.0
        self.last_success_at = time.time()
        self.first_failure_at = 0.0

    def mark_failure(self, base: float, jitter: float, max_backoff: float) -> None:
        self.failures += 1
        self.total_attempts += 1
        if self.first_failure_at == 0.0:
            self.first_failure_at = time.time()
        # Exponential backoff with decorrelated jitter
        exp = min(max_backoff, (base**self.failures))
        delay = min(max_backoff, exp * (1.0 + random.random() * jitter))
        self.next_try_at = time.time() + delay
    
    def should_give_up(self, max_retries: int) -> bool:
        """Check if we've exceeded maximum retry attempts."""
        return self.failures >= max_retries


@dataclass
class PeerSlot:
    peer: Peer
    addr: str
    connected_at: float = field(default_factory=lambda: time.time())
    last_ping_at: float = 0.0
    last_rtt_ms: Optional[float] = None
    is_active: bool = True
    direction: str = "outbound"  # or "inbound"


@dataclass
class CMConfig:
    target_outbound: int = 16
    max_outbound: int = 32
    max_inbound: int = 128
    keepalive_interval_s: float = 30.0
    keepalive_timeout_s: float = 4.0
    dial_timeout_s: float = 5.0
    dial_rate_per_s: float = 5.0
    backoff_base: float = 1.7
    backoff_jitter: float = 0.25
    backoff_max_s: float = 300.0
    prune_idle_s: float = 2 * 3600.0  # 2h
    ban_s: float = 6 * 3600.0
    # P2P flow rewrite enhancements
    max_dial_retries: int = 5  # Maximum connection retry attempts
    priority_boost_stable: float = 10.0  # Score boost for stable peers
    priority_boost_recent: float = 5.0  # Score boost for recently seen peers
    stability_threshold_s: float = 3600.0  # Consider peer stable after 1 hour uptime
    bootstrap_priority_boost: float = 20.0  # Boost for bootstrap/seed-tagged peers
    verbosity: int = 0  # 0=normal, 1=verbose, 2=debug


class ConnectionManager:
    """
    Dials and maintains peer connections with:
      - Exponential backoff + jitter per address
      - Outbound target / global limits
      - Periodic keepalive pings
      - Simple event bus for join/leave/fail
      - Enhanced logging and peer prioritization (P2P rewrite)
    """

    def __init__(
        self,
        transport: Transport,
        addr_book: AddressBook,
        cfg: Optional[CMConfig] = None,
    ):
        self.t: Transport = transport
        self.ab: AddressBook = addr_book
        self.cfg: CMConfig = cfg or CMConfig()
        self._running = False
        self._tasks: List[asyncio.Task] = []
        self._events: "asyncio.Queue[Tuple[str, Dict[str, Any]]]" = asyncio.Queue()
        self._dial_tokens = self._make_token_bucket(self.cfg.dial_rate_per_s)
        self._lock = asyncio.Lock()
        
        # Setup logger with appropriate verbosity
        self._log = logging.getLogger("animica.p2p.connmgr")
        if self.cfg.verbosity >= 2:
            self._log.setLevel(logging.DEBUG)
        elif self.cfg.verbosity >= 1:
            self._log.setLevel(logging.INFO)

        # live peers
        self._peers_by_id: Dict[str, PeerSlot] = {}
        self._peers_by_addr: Dict[str, PeerSlot] = {}

        # backoff state
        self._backoff: Dict[str, DialBackoff] = {}
        self._banned_until: Dict[str, float] = {}
        self._ban_enabled = False

        # async iter guard
        self._event_iter_open = 0
        
        # P2P rewrite: Track peer priorities and statistics
        self._peer_priorities: Dict[str, float] = {}  # addr -> priority score
        self._connection_stats: Dict[str, Dict[str, Any]] = {}  # addr -> stats

    # -------------------- lifecycle -------------------- #

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._tasks = [
            asyncio.create_task(self._dialer_loop(), name="cm.dialer"),
            asyncio.create_task(self._keepalive_loop(), name="cm.keepalive"),
            asyncio.create_task(self._pruner_loop(), name="cm.prune"),
            asyncio.create_task(self._refill_tokens_loop(), name="cm.tokens"),
        ]

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        # Close all peers
        for slot in list(self._peers_by_id.values()):
            try:
                await slot.peer.conn.close()
            except Exception:
                pass
        self._peers_by_id.clear()
        self._peers_by_addr.clear()

    # -------------------- public API -------------------- #

    async def connect(self, address: str, *, tag: str = "manual") -> Optional[Peer]:
        """Force-connect to a specific address (ignores backoff/bans)."""
        try:
            entry = self.ab.add(address, tag=tag)
        except Exception as e:
            await self._push_event(
                "peer_connect_failed", {"address": address, "error": str(e)}
            )
            return None
        return await self._dial_one(entry.norm)

    async def disconnect(self, peer_id: str) -> bool:
        slot = self._peers_by_id.get(peer_id)
        if not slot:
            return False
        try:
            await slot.peer.conn.close()
        finally:
            await self._on_disconnect(slot.peer.peer_id, reason="manual")
        return True

    def list_peers(self) -> List[Peer]:
        return [s.peer for s in self._peers_by_id.values()]

    def set_outbound_target(self, n: int) -> None:
        self.cfg.target_outbound = max(0, min(self.cfg.max_outbound, int(n)))

    async def events(self) -> AsyncIterator[Tuple[str, Dict[str, Any]]]:
        """Async iterator of (event, payload)."""
        self._event_iter_open += 1
        try:
            while self._running or not self._events.empty():
                try:
                    item = await asyncio.wait_for(self._events.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                yield item
        finally:
            self._event_iter_open = max(0, self._event_iter_open - 1)

    def ban(self, address_or_peer_id: str, seconds: Optional[float] = None) -> None:
        if not self._ban_enabled:
            return
        until = time.time() + (seconds if seconds is not None else self.cfg.ban_s)
        self._banned_until[address_or_peer_id] = until

    # -------------------- loops -------------------- #

    async def _dialer_loop(self) -> None:
        """Continuously try to reach the outbound target with prioritization."""
        try:
            while self._running:
                await asyncio.sleep(0.05)
                outbound = sum(
                    1
                    for s in self._peers_by_id.values()
                    if s.direction == "outbound" and s.is_active
                )
                if outbound >= self.cfg.target_outbound:
                    if self.cfg.verbosity >= 2:
                        self._log.debug(f"Outbound target reached: {outbound}/{self.cfg.target_outbound}")
                    await asyncio.sleep(0.25)
                    continue

                # Get candidates from address book (prefer recent good)
                candidates = self.ab.list_recent(limit=500)
                
                # P2P rewrite: Sort candidates by priority score
                candidates_with_priority = []
                now = time.time()
                for e in candidates:
                    addr = e.norm
                    if addr in self._peers_by_addr:
                        continue
                    if self._is_banned(addr) or self._is_banned(
                        getattr(e, "peer_id", "") or ""
                    ):
                        continue
                    bo = self._backoff.get(addr)
                    if bo and now < bo.next_try_at:
                        if self.cfg.verbosity >= 2:
                            wait_time = bo.next_try_at - now
                            self._log.debug(f"Skipping {addr}: backoff active, retry in {wait_time:.1f}s")
                        continue
                    if bo and bo.should_give_up(self.cfg.max_dial_retries):
                        if self.cfg.verbosity >= 1:
                            self._log.info(f"Skipping {addr}: exceeded max retries ({self.cfg.max_dial_retries})")
                        continue
                    
                    priority = self._calculate_peer_priority(addr, tag=e.tag)
                    candidates_with_priority.append((priority, addr))
                
                # Sort by priority (highest first)
                candidates_with_priority.sort(reverse=True, key=lambda x: x[0])

                picked = None
                if candidates_with_priority:
                    priority, picked = candidates_with_priority[0]
                    if self.cfg.verbosity >= 1:
                        self._log.info(f"Selected peer {picked} with priority {priority:.2f}")

                if not picked:
                    # Nothing to dial right now; wait a bit
                    if self.cfg.verbosity >= 2:
                        self._log.debug(f"No eligible peers to dial (candidates: {len(candidates)})")
                    await asyncio.sleep(0.5)
                    continue

                # Token-bucket throttle
                if not await self._dial_tokens.get():
                    # Wait for token refill
                    if self.cfg.verbosity >= 2:
                        self._log.debug("Dial rate limit reached, waiting for token")
                    await asyncio.sleep(0.2)
                    continue

                # Fire and forget (let it handle its own backoff updates)
                asyncio.create_task(self._dial_one(picked))
        except asyncio.CancelledError:
            return

    async def _keepalive_loop(self) -> None:
        try:
            while self._running:
                await asyncio.sleep(1.0)
                now = time.time()
                for slot in list(self._peers_by_id.values()):
                    if not slot.is_active:
                        continue
                    if now - slot.last_ping_at < self.cfg.keepalive_interval_s:
                        continue
                    asyncio.create_task(self._ping_slot(slot))
        except asyncio.CancelledError:
            return

    async def _pruner_loop(self) -> None:
        try:
            while self._running:
                await asyncio.sleep(30.0)
                # prune address book of very old or very bad addresses
                try:
                    self.ab.prune(older_than_s=self.cfg.prune_idle_s)
                except Exception:
                    pass
                # clean banned set
                now = time.time()
                to_del = [k for k, until in self._banned_until.items() if until <= now]
                for k in to_del:
                    self._banned_until.pop(k, None)
        except asyncio.CancelledError:
            return

    async def _refill_tokens_loop(self) -> None:
        try:
            while self._running:
                await asyncio.sleep(1.0)
                self._dial_tokens.refill()
        except asyncio.CancelledError:
            return

    # -------------------- actions -------------------- #

    async def _dial_one(self, address: str) -> Optional[Peer]:
        """Dial and register a peer; manages backoff and events."""
        # Respect per-address bans (but connect() bypasses this via add() then direct call here)
        if self._is_banned(address):
            self._log.info(f"Skipping banned peer: {address}")
            await self._push_event("peer_connect_skipped_banned", {"address": address})
            return None

        conn: Optional[Conn] = None
        dial_start = time.time()
        
        try:
            if self.cfg.verbosity >= 1:
                self._log.info(f"Attempting to dial peer: {address}")
            
            conn = await asyncio.wait_for(
                self.t.dial(address, timeout=self.cfg.dial_timeout_s),
                timeout=self.cfg.dial_timeout_s + 0.5,
            )
            
            if self.cfg.verbosity >= 2:
                dial_time = time.time() - dial_start
                self._log.debug(f"Connection established to {address} in {dial_time:.2f}s")
            
            ident = await asyncio.wait_for(
                perform_identify(conn, timeout=5.0), timeout=6.0
            )
            peer_id = ident.get("peer_id") or getattr(conn, "remote_addr", address)
            
            if self.cfg.verbosity >= 2:
                self._log.debug(f"Identified peer {peer_id} at {address}: {ident}")
            
            peer = Peer(
                peer_id=peer_id,
                address=address,
                conn=conn,
                direction="outbound",
                meta=ident,
            )
            slot = PeerSlot(peer=peer, addr=address, direction="outbound")
            async with self._lock:
                # If already connected under this id, drop older
                old = self._peers_by_id.get(peer_id)
                if old:
                    self._log.info(f"Replacing existing connection to {peer_id}")
                    try:
                        await old.peer.conn.close()
                    except Exception:
                        pass
                    await self._on_disconnect(peer_id, reason="replaced")
                self._peers_by_id[peer_id] = slot
                self._peers_by_addr[address] = slot
            # Address book success mark
            try:
                self.ab.mark_seen(address, good=True, peer_id=peer_id)
            except Exception:
                pass
            # Reset backoff and update stats
            self._backoff.setdefault(address, DialBackoff()).mark_success()
            self._update_connection_stats(address, success=True)
            
            total_time = time.time() - dial_start
            self._log.info(f"Successfully connected to {peer_id} at {address} (took {total_time:.2f}s)")
            
            await self._push_event(
                "peer_connected",
                {
                    "peer_id": peer_id,
                    "address": address,
                    "direction": "outbound",
                    "meta": ident,
                    "connection_time_s": total_time,
                },
            )
            return peer
        except asyncio.TimeoutError as e:
            self._log.warning(f"Connection timeout to {address} after {time.time() - dial_start:.2f}s")
            self._update_connection_stats(address, success=False)
            await self._record_failure(address, "timeout", e)
        except Exception as e:
            self._log.warning(f"Connection failed to {address}: {e.__class__.__name__}: {e}")
            self._update_connection_stats(address, success=False)
            await self._record_failure(address, "error", e)
        # Ensure connection is closed on failure
        if conn is not None:
            try:
                await conn.close()
            except Exception:
                pass
        return None

    async def _ping_slot(self, slot: PeerSlot) -> None:
        slot.last_ping_at = time.time()
        try:
            rtt = await asyncio.wait_for(
                ping_once(slot.peer.conn, timeout=self.cfg.keepalive_timeout_s),
                timeout=self.cfg.keepalive_timeout_s + 0.2,
            )
            slot.last_rtt_ms = rtt
            slot.peer.last_rtt_ms = rtt
            slot.peer.last_seen = time.time()
            
            if self.cfg.verbosity >= 2:
                self._log.debug(f"Ping successful to {slot.peer.peer_id}: RTT={rtt:.1f}ms")
            
            await self._push_event(
                "peer_ping_ok", {"peer_id": slot.peer.peer_id, "rtt_ms": rtt}
            )
        except Exception as e:
            # Treat as soft failure; if repeated, connection may drop elsewhere
            self._log.warning(f"Ping failed to {slot.peer.peer_id}: {e.__class__.__name__}: {e}")
            await self._push_event(
                "peer_ping_fail", {"peer_id": slot.peer.peer_id, "error": str(e)}
            )

    async def _on_disconnect(self, peer_id: str, *, reason: str = "unknown") -> None:
        async with self._lock:
            slot = self._peers_by_id.pop(peer_id, None)
            if slot:
                self._peers_by_addr.pop(slot.addr, None)
        if slot:
            slot.is_active = False
            uptime = time.time() - slot.connected_at if slot.connected_at else 0
            self._log.info(f"Peer disconnected: {peer_id} ({slot.addr}), reason={reason}, uptime={uptime:.1f}s")
            
            # Mark address as seen (bad)
            try:
                self.ab.mark_seen(slot.addr, good=False, peer_id=peer_id)
            except Exception:
                pass
            await self._push_event(
                "peer_disconnected",
                {"peer_id": peer_id, "address": slot.addr, "reason": reason, "uptime_s": uptime},
            )

    async def _record_failure(self, address: str, kind: str, err: Exception) -> None:
        # Backoff bump
        bo = self._backoff.setdefault(address, DialBackoff())
        bo.mark_failure(
            self.cfg.backoff_base, self.cfg.backoff_jitter, self.cfg.backoff_max_s
        )
        
        retry_delay = bo.next_try_at - time.time()
        if bo.should_give_up(self.cfg.max_dial_retries):
            self._log.warning(
                f"Peer {address} exceeded max retries ({self.cfg.max_dial_retries}), "
                f"giving up after {bo.total_attempts} attempts"
            )
        else:
            self._log.info(
                f"Connection failed to {address} ({kind}): {err}. "
                f"Retry #{bo.failures} in {retry_delay:.1f}s"
            )
        
        # Mark bad in addr book
        try:
            self.ab.mark_seen(address, good=False)
        except Exception:
            pass
        await self._push_event(
            "peer_connect_failed",
            {
                "address": address,
                "kind": kind,
                "error": str(err),
                "next_try_at": bo.next_try_at,
                "failures": bo.failures,
                "total_attempts": bo.total_attempts,
            },
        )

    # -------------------- inbound hooks -------------------- #

    async def register_inbound(self, conn: Conn) -> Optional[Peer]:
        """
        Called by transport/server when an inbound connection is accepted.
        Performs identify and registers the peer if under limits.
        """
        # Inbound limit
        inbounds = sum(
            1
            for s in self._peers_by_id.values()
            if s.direction == "inbound" and s.is_active
        )
        if inbounds >= self.cfg.max_inbound:
            try:
                await conn.close()
            except Exception:
                pass
            await self._push_event(
                "peer_reject_inbound_full",
                {"remote": getattr(conn, "remote_addr", "unknown")},
            )
            return None

        try:
            ident = await asyncio.wait_for(
                perform_identify(conn, timeout=5.0), timeout=6.0
            )
            peer_id = ident.get("peer_id") or getattr(conn, "remote_addr", "unknown")
            # Ban check
            if self._is_banned(peer_id):
                try:
                    await conn.close()
                except Exception:
                    pass
                await self._push_event(
                    "peer_reject_inbound_banned", {"peer_id": peer_id}
                )
                return None
            peer = Peer(
                peer_id=peer_id,
                address=getattr(conn, "remote_addr", peer_id),
                conn=conn,
                direction="inbound",
                meta=ident,
            )
            slot = PeerSlot(peer=peer, addr=peer.address, direction="inbound")
            async with self._lock:
                self._peers_by_id[peer_id] = slot
                self._peers_by_addr[slot.addr] = slot
            try:
                self.ab.add(slot.addr, tag="peer", peer_id=peer_id)
                self.ab.mark_seen(slot.addr, good=True, peer_id=peer_id)
            except Exception:
                pass
            await self._push_event(
                "peer_connected",
                {
                    "peer_id": peer_id,
                    "address": slot.addr,
                    "direction": "inbound",
                    "meta": ident,
                },
            )
            return peer
        except Exception as e:
            try:
                await conn.close()
            except Exception:
                pass
            await self._push_event(
                "peer_inbound_identify_fail",
                {"remote": getattr(conn, "remote_addr", "unknown"), "error": str(e)},
            )
            return None

    # -------------------- utilities -------------------- #

    def _is_banned(self, key: str) -> bool:
        if not self._ban_enabled:
            return False
        if not key:
            return False
        until = self._banned_until.get(key)
        return bool(until and time.time() < until)

    async def _push_event(self, evt: str, payload: Dict[str, Any]) -> None:
        # Drop if too many outstanding (avoid unbounded growth)
        if self._events.qsize() > 4096 and self._event_iter_open == 0:
            try:
                self._events.get_nowait()
            except Exception:
                pass
        await self._events.put((evt, payload))
    
    # -------------------- P2P rewrite: Enhanced peer prioritization -------------------- #
    
    def _calculate_peer_priority(self, address: str, tag: Optional[str] = None) -> float:
        """
        Calculate priority score for a peer based on:
        - Connection stability (long uptime)
        - Recent successful connections
        - Success/failure ratio
        - Bootstrap/seed tags
        - Manual priority boosts
        """
        priority = 0.0
        
        # Check backoff history
        bo = self._backoff.get(address)
        if bo:
            # Penalize peers with many failures
            priority -= bo.failures * 2.0
            
            # Boost peers with recent successful connections
            if bo.last_success_at > 0:
                time_since_success = time.time() - bo.last_success_at
                if time_since_success < 3600.0:  # Within last hour
                    priority += self.cfg.priority_boost_recent
                
                # Boost stable long-running connections
                if time_since_success >= self.cfg.stability_threshold_s:
                    priority += self.cfg.priority_boost_stable
        
        # Check connection statistics
        stats = self._connection_stats.get(address, {})
        total_conn = stats.get("total_connections", 0)
        successful_conn = stats.get("successful_connections", 0)
        
        if total_conn > 0:
            success_ratio = successful_conn / total_conn
            priority += success_ratio * 5.0  # Up to +5 for perfect success rate

        if tag is None:
            try:
                entry = self.ab.get(address)
            except Exception:
                entry = None
            if entry:
                tag = entry.tag

        if tag and tag.lower() in {"seed", "bootstrap", "bootstrap_seed"}:
            priority += self.cfg.bootstrap_priority_boost

        # Apply any manual priority overrides
        manual_priority = self._peer_priorities.get(address, 0.0)
        priority += manual_priority
        
        return priority
    
    def set_peer_priority(self, address: str, priority: float) -> None:
        """Manually set priority for a specific peer (for testing or manual control)."""
        self._peer_priorities[address] = priority
        self._log.info(f"Set manual priority for {address}: {priority}")
    
    def _update_connection_stats(self, address: str, success: bool) -> None:
        """Track connection statistics for peer prioritization."""
        if address not in self._connection_stats:
            self._connection_stats[address] = {
                "total_connections": 0,
                "successful_connections": 0,
                "last_attempt": 0.0,
                "last_success": 0.0,
            }
        
        stats = self._connection_stats[address]
        stats["total_connections"] += 1
        stats["last_attempt"] = time.time()
        
        if success:
            stats["successful_connections"] += 1
            stats["last_success"] = time.time()
    
    def get_peer_diagnostics(self, address: str) -> Dict[str, Any]:
        """Get diagnostic information about a peer for debugging."""
        bo = self._backoff.get(address)
        stats = self._connection_stats.get(address, {})
        priority = self._calculate_peer_priority(address)
        
        return {
            "address": address,
            "priority": priority,
            "backoff": {
                "failures": bo.failures if bo else 0,
                "total_attempts": bo.total_attempts if bo else 0,
                "next_try_at": bo.next_try_at if bo else 0.0,
                "last_success_at": bo.last_success_at if bo else 0.0,
            } if bo else None,
            "stats": stats,
            "is_banned": self._is_banned(address),
            "is_connected": address in self._peers_by_addr,
        }

    # Simple token bucket for dial throttling
    class _Bucket:
        def __init__(self, rate_per_s: float, capacity: Optional[int] = None):
            self.rate = max(0.1, float(rate_per_s))
            self.capacity = int(capacity or math.ceil(self.rate * 2))
            self.tokens = self.capacity
            self.last = time.time()
            self._q: asyncio.Queue[bool] = asyncio.Queue()

        async def get(self) -> bool:
            # Try fast-path
            if self.tokens > 0:
                self.tokens -= 1
                return True
            # Wait for a token to arrive (producer refills once per second)
            try:
                await asyncio.wait_for(self._q.get(), timeout=1.5)
                return True
            except asyncio.TimeoutError:
                return False

        def refill(self) -> None:
            now = time.time()
            elapsed = max(0.0, now - self.last)
            self.last = now
            add = int(elapsed * self.rate)
            if add <= 0:
                return
            before = self.tokens
            self.tokens = min(self.capacity, self.tokens + add)
            # push events for each newly available token so waiters wake up
            for _ in range(self.tokens - before):
                try:
                    self._q.put_nowait(True)
                except Exception:
                    break

    def _make_token_bucket(self, rps: float) -> "ConnectionManager._Bucket":
        return self._Bucket(rate_per_s=rps)


# Convenience factory
def make_default_manager(
    transport: Transport, addr_book: AddressBook, **overrides: Any
) -> ConnectionManager:
    cfg = CMConfig(**overrides) if overrides else CMConfig()
    return ConnectionManager(transport, addr_book, cfg)
