from __future__ import annotations

import asyncio
import logging
import os
import random
import socket
from dataclasses import dataclass, field
from typing import Iterable, Optional

from p2p.transport.multiaddr import parse_multiaddr

from .addrman import AddressManager
from .connman import ConnectionManager
from .net_processing import NetProcessing
from .netaddress import NetAddress
from .sync_manager import ChainAdapter

log = logging.getLogger("animica.p2p.core_p2p.service")


def _resolve_host(host: str) -> Optional[str]:
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except OSError:
        return None
    for family, _, _, _, addr in infos:
        if family in (socket.AF_INET, socket.AF_INET6):
            return addr[0]
    return None


def _parse_seed(addr: str) -> Optional[NetAddress]:
    if not addr:
        return None
    if addr.startswith("/"):
        try:
            parsed = parse_multiaddr(addr)
        except Exception:
            return None
        if parsed.transport != "tcp":
            return None
        host = parsed.host or ""
        port = int(parsed.port or 0)
        if not host or port <= 0:
            return None
        resolved = _resolve_host(host)
        if resolved is None:
            return None
        return NetAddress(services=1, ip=resolved, port=port)

    if ":" in addr:
        host, port_str = addr.rsplit(":", 1)
        try:
            port = int(port_str)
        except ValueError:
            return None
        resolved = _resolve_host(host) if host else None
        if resolved is None:
            return None
        return NetAddress(services=1, ip=resolved, port=port)

    resolved = _resolve_host(addr)
    if resolved is None:
        return None
    return None


@dataclass
class CoreP2PService:
    chain: ChainAdapter
    listen_host: str
    listen_port: int
    seeds: Iterable[str] = field(default_factory=tuple)
    max_outbound: int = 8
    max_inbound: int = 16
    dial_interval: float = 5.0

    addrman: AddressManager = field(init=False)
    net_processing: NetProcessing = field(init=False)
    connman: ConnectionManager = field(init=False)
    _tasks: list[asyncio.Task] = field(default_factory=list, init=False)
    _running: bool = field(default=False, init=False)
    _head_task: Optional[asyncio.Task] = field(default=None, init=False)
    _last_head_hash: Optional[bytes] = field(default=None, init=False)
    _seed_list: list[str] = field(default_factory=list, init=False)
    _mempool_rebroadcast_interval: float = field(default=0.0, init=False)
    _mempool_rebroadcast_limit: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self.addrman = AddressManager()
        self._mempool_rebroadcast_interval = float(
            os.environ.get("ANIMICA_P2P_CORE_MEMPOOL_REBROADCAST_SEC", "15") or 15
        )
        self._mempool_rebroadcast_limit = int(
            os.environ.get("ANIMICA_P2P_CORE_MEMPOOL_LIMIT", "512") or 512
        )
        self._seed_list = list(self.seeds)
        for seed in self._seed_list:
            parsed = _parse_seed(seed)
            if parsed is not None:
                self.addrman.add([parsed])
            else:
                log.debug("Skipping unsupported core seed", extra={"seed": seed})
        self.net_processing = NetProcessing(chain=self.chain, addrman=self.addrman)
        self.connman = ConnectionManager(
            self.listen_host,
            self.listen_port,
            self.addrman,
            self.net_processing,
            max_inbound=self.max_inbound,
            max_outbound=self.max_outbound,
        )
        # Sync check interval - request headers periodically if we have pending blocks
        sync_check_sec = os.environ.get("ANIMICA_P2P_CORE_SYNC_CHECK_SEC", "10")
        try:
            self._sync_check_interval = float(sync_check_sec)
        except (ValueError, TypeError):
            self._sync_check_interval = 10.0

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        await self.connman.start()
        self._tasks = [
            asyncio.create_task(self._dial_loop(), name="core_p2p.dial"),
            asyncio.create_task(self._head_watch_loop(), name="core_p2p.head_watch"),
            asyncio.create_task(self._sync_check_loop(), name="core_p2p.sync_check"),
        ]
        if self._mempool_rebroadcast_interval > 0:
            self._tasks.append(
                asyncio.create_task(
                    self._mempool_rebroadcast_loop(), name="core_p2p.mempool_rebroadcast"
                )
            )
        log.info(
            "core p2p started",
            extra={
                "listen": f"{self.listen_host}:{self.listen_port}",
                "seeds": len(self._seed_list),
            },
        )

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        await self.connman.stop()

    async def _dial_loop(self) -> None:
        try:
            while self._running:
                await self._ensure_outbound()
                await asyncio.sleep(self.dial_interval)
        except asyncio.CancelledError:
            return

    async def _ensure_outbound(self) -> None:
        while self.connman.outbound_count() < self.max_outbound:
            candidate = self.addrman.select()
            if candidate is None:
                return
            try:
                await self.connman.dial(candidate)
            except Exception as exc:
                log.debug(
                    "core p2p dial failed",
                    exc_info=exc,
                    extra={"peer": candidate.key()},
                )
                return

    async def _head_watch_loop(self) -> None:
        try:
            while self._running:
                await asyncio.sleep(2.0)
                current_hash = self._best_head_hash()
                if current_hash and current_hash != self._last_head_hash:
                    self._last_head_hash = current_hash
                    await self.net_processing.announce_block(
                        self.connman.peers().values(),
                        current_hash,
                        self.connman._send,
                    )
        except asyncio.CancelledError:
            return

    async def _mempool_rebroadcast_loop(self) -> None:
        try:
            while self._running:
                await asyncio.sleep(self._mempool_rebroadcast_interval)
                await self._broadcast_pending_txs()
        except asyncio.CancelledError:
            return

    async def _broadcast_pending_txs(self) -> None:
        deps = getattr(self.chain, "deps", None)
        if deps is None:
            return
        fn = getattr(deps, "list_pending_hashes", None)
        if not callable(fn):
            return
        try:
            hashes = fn(limit=self._mempool_rebroadcast_limit)
            if asyncio.iscoroutine(hashes):
                hashes = await hashes
        except Exception:
            return
        if not hashes:
            return
        peers = list(self.connman.peers().values())
        if not peers:
            return
        for h in hashes:
            if not isinstance(h, (bytes, bytearray)) or len(h) != 32:
                continue
            await self.net_processing.announce_tx(peers, bytes(h), self.connman._send)

    def _best_head_hash(self) -> Optional[bytes]:
        getter = getattr(self.chain, "best_header_hash", None)
        if callable(getter):
            return getter()
        head = self.chain.best_header()
        if not head:
            return None
        return head[-32:]

    async def _sync_check_loop(self) -> None:
        """
        Periodically check if sync is progressing and request headers if needed.
        
        This ensures continuous sync even if initial getheaders doesn't complete
        or if we fall behind while connected.
        """
        try:
            last_check_height = -1
            stall_count = 0
            while self._running:
                await asyncio.sleep(self._sync_check_interval)
                
                # Timeout any stale block requests and re-queue them
                timed_out = self.net_processing.sync.timeout_stale_requests()
                if timed_out > 0:
                    log.info(
                        "core p2p timed out stale block requests",
                        extra={"count": timed_out}
                    )
                
                # Get current chain height
                current_height = self._current_height()
                
                # Check if we have connected peers
                peers = list(self.connman.peers().values())
                if not peers:
                    continue
                
                # Check if we have pending blocks or inflight requests
                has_pending = bool(self.net_processing.sync.pending_blocks)
                has_inflight = bool(self.net_processing.sync.inflight_blocks)
                
                # Detect stall: height hasn't changed and no pending/inflight work
                if current_height == last_check_height and not has_pending and not has_inflight:
                    stall_count += 1
                else:
                    stall_count = 0
                
                last_check_height = current_height
                
                # Request more headers if stalled for 2+ checks (20+ seconds)
                if stall_count >= 2:
                    log.info(
                        "core p2p sync appears stalled, requesting headers",
                        extra={
                            "height": current_height,
                            "pending": len(self.net_processing.sync.pending_blocks),
                            "inflight": len(self.net_processing.sync.inflight_blocks),
                            "peers": len(peers),
                        }
                    )
                    # Request headers from all connected peers
                    msg = self.net_processing.sync.build_getheaders()
                    for peer in peers:
                        if peer.handshake_complete:
                            try:
                                await self.connman._send(peer, "getheaders", msg.serialize())
                            except Exception as exc:
                                log.debug("failed to send getheaders", exc_info=exc)
                    stall_count = 0  # Reset after requesting
                
                # Request pending blocks if we have them but no inflight requests
                # (this can happen after timeouts or if initial requests were missed)
                if has_pending and not has_inflight and peers:
                    log.debug(
                        "core p2p requesting pending blocks",
                        extra={"pending": len(self.net_processing.sync.pending_blocks)}
                    )
                    # Pick a random peer to request from
                    peer = random.choice(peers)
                    if peer.handshake_complete:
                        try:
                            await self.net_processing._request_pending_blocks(
                                lambda cmd, data: self.connman._send(peer, cmd, data)
                            )
                        except Exception as exc:
                            log.debug("failed to request pending blocks", exc_info=exc)
                
        except asyncio.CancelledError:
            return
    
    def _current_height(self) -> int:
        """
        Get current chain height.
        
        Note: This method assumes the header format follows Bitcoin-style encoding
        where the first 4 bytes (little-endian) represent the block height.
        For chains using different header formats, this method should be overridden
        or the ChainAdapter should provide a best_height() method.
        """
        getter = getattr(self.chain, "best_height", None)
        if callable(getter):
            return getter()
        head = self.chain.best_header()
        if not head or len(head) < 4:
            return 0
        # Bitcoin-style: first 4 bytes are height in little-endian
        return int.from_bytes(head[:4], "little", signed=False)
