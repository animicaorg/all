from __future__ import annotations

import asyncio
import contextlib
import hashlib
import ipaddress
import logging
import os
import signal
import time
from dataclasses import dataclass, field
from typing import (Any, Awaitable, Callable, Dict, Iterable, List, Optional,
                    Tuple)

# Local imports are intentionally late/dynamic in a few places to avoid import cycles.
from .. import version as p2p_version
from ..config import P2PConfig  # typed config (see p2p/config.py)
from ..metrics import \
    get_metrics  # light wrapper; no-op metrics if not configured
from ..peer import connection_manager as conman
from ..peer import identify as idsvc
from ..peer import peerstore as pstore
from ..peer import ping as pingsvc
from ..peer import ratelimit as prlimit
from ..transport import base as tbase
from ..transport import multiaddr as ma

try:
    from ..gossip import engine as gossip_engine
    from ..gossip import topics as gossip_topics
    from ..protocol import block_announce as proto_blk
    from ..protocol import flow_control as proto_flow
    from ..protocol import hello as proto_hello
    from ..protocol import inventory as proto_inv
    from ..protocol import share_relay as proto_share
    from ..protocol import tx_relay as proto_tx
    from ..wire import encoding as wire_codec
except Exception:  # pragma: no cover - optional full stack
    gossip_engine = None  # type: ignore
    gossip_topics = None  # type: ignore
    proto_hello = proto_inv = proto_blk = proto_tx = proto_share = proto_flow = wire_codec = None  # type: ignore

# Node router/event-bus (these are small glue modules under p2p/node/)
from . import events as node_events
from . import health as node_health
from . import router as node_router

log = logging.getLogger("animica.p2p.node")

OnAccept = Callable[[tbase.Conn], Awaitable[None]]


@dataclass
class NodeDeps:
    """
    Injected glue to core/consensus/proofs so protocol handlers can look up/persist things
    without hard-coding imports. See p2p/deps.py for a ready-made provider.
    """

    head_reader: Any
    block_io: Any
    tx_io: Any
    proofs_view: Any
    consensus_view: Any


@dataclass
class _Listener:
    addr: str
    listener: tbase.Listener
    task: asyncio.Task


@dataclass
class NodeService:
    """
    Orchestrates the full P2P node:
      • Binds listeners for the configured transports (TCP/QUIC/WS)
      • Performs PQ handshake (Kyber768 + HKDF) and upgrades to AEAD
      • Wires protocol handlers (HELLO, INV/GETDATA, block announce, tx/share relay)
      • Runs the gossip mesh + discovery + ping/identify services
      • Exposes a tiny event bus & health snapshot
    """

    cfg: P2PConfig
    deps: NodeDeps
    loop: asyncio.AbstractEventLoop = field(default_factory=asyncio.get_event_loop)

    # runtime members
    started: bool = field(default=False, init=False)
    stopping: bool = field(default=False, init=False)
    _listeners: List[_Listener] = field(default_factory=list, init=False)
    _tasks: List[asyncio.Task] = field(default_factory=list, init=False)

    # services
    peerstore: pstore.PeerStore = field(init=False)
    connmgr: conman.ConnectionManager = field(init=False)
    ratelimiter: prlimit.PeerRateLimiter = field(init=False)
    events: node_events.EventBus = field(init=False)
    router: node_router.Router = field(init=False)
    gossip: gossip_engine.GossipEngine = field(init=False)
    ping: pingsvc.PingService = field(init=False)
    identify: idsvc.IdentifyService = field(init=False)
    flowctl: proto_flow.FlowController = field(init=False)
    tx_relay_handler: Any = field(init=False)  # TxRelayHandler

    # crypto/ids
    node_keys: Any = field(init=False)
    peer_id: bytes = field(init=False)

    # metrics
    metrics: Any = field(default_factory=get_metrics, init=False)

    def __post_init__(self) -> None:
        # Load or generate long-term node identity (Dilithium3/SPHINCS+) + peer-id
        from ..crypto import keys as node_keys_mod
        from ..crypto import peer_id as peer_id_mod

        self.node_keys = node_keys_mod.load_or_create(
            self.cfg.keys_path, alg=self.cfg.identity_alg
        )
        self.peer_id = peer_id_mod.derive_peer_id(
            self.node_keys.public_key, self.node_keys.alg_id
        )

        # Core services
        self.peerstore = pstore.PeerStore(self.cfg.data_dir)
        self.ratelimiter = prlimit.PeerRateLimiter(
            per_peer=self.cfg.limit_per_peer,
            per_topic=self.cfg.limit_per_topic,
            global_limits=self.cfg.limit_global,
        )
        self.connmgr = conman.ConnectionManager(
            cfg=self.cfg,
            peerstore=self.peerstore,
            ratelimiter=self.ratelimiter,
            loop=self.loop,
        )
        self.events = node_events.EventBus(self.loop)
        self.router = node_router.Router(loop=self.loop, events=self.events)
        self.gossip = gossip_engine.GossipEngine(
            cfg=self.cfg.gossip,
            router=self.router,
            ratelimiter=self.ratelimiter,
            peerstore=self.peerstore,
            loop=self.loop,
        )
        self.ping = pingsvc.PingService(self.connmgr, window_size=16)
        self.identify = idsvc.IdentifyService(
            connmgr=self.connmgr,
            peer_id=self.peer_id,
            version=p2p_version.__version__,
            head_reader=self.deps.head_reader,
            alg_policy_root=self.cfg.alg_policy_root,
        )
        self.flowctl = proto_flow.FlowController(self.cfg.flow_control)

        # Mount protocol handlers into the router
        self._mount_protocols()

    # ——————————————————————————————————————————————————————————
    # Lifecycle
    # ——————————————————————————————————————————————————————————
    async def start(self) -> None:
        if self.started:
            return
        self.started = True
        log.info(
            "P2P node starting",
            extra={"peer_id": self.peer_id.hex(), "version": p2p_version.__version__},
        )

        # Bind listeners (TCP/WS/QUIC) per cfg.listen_multiaddrs
        for addr in self.cfg.listen_multiaddrs:
            listener = await self._bind_listener(addr)
            task = self.loop.create_task(
                self._accept_loop(listener), name=f"accept@{addr}"
            )
            self._listeners.append(_Listener(addr=addr, listener=listener, task=task))

        # Start TxRelayHandler (subscribe to gossip topic)
        await self.tx_relay_handler.start()

        # Start background services
        self._tasks.extend(
            [
                self.loop.create_task(self.connmgr.run(), name="connmgr"),
                self.loop.create_task(self.gossip.run(), name="gossip"),
                self.loop.create_task(self.ping.run(), name="ping"),
                self.loop.create_task(self.identify.run(), name="identify"),
                self.loop.create_task(self._seed_and_discover(), name="discovery"),
                self.loop.create_task(self.flowctl.run(), name="flowctl"),
            ]
        )

        # Hook OS signals for graceful shutdown (best-effort)
        self._install_signal_handlers()
        log.info(
            "P2P node started", extra={"listeners": [l.addr for l in self._listeners]}
        )

    async def stop(self) -> None:
        if self.stopping:
            return
        self.stopping = True
        log.info("P2P node stopping")

        # Stop TxRelayHandler
        await self.tx_relay_handler.stop()

        # Stop background tasks
        for t in self._tasks:
            t.cancel()
        await self._gather("background", *self._tasks)

        # Close listeners
        for L in self._listeners:
            with contextlib.suppress(Exception):
                await L.listener.close()
            if not L.task.done():
                L.task.cancel()
        await self._gather("listeners", *(L.task for L in self._listeners))

        # Stop subordinate services that own resources
        await self.gossip.close()
        await self.connmgr.close()

        self.started = False
        log.info("P2P node stopped")

    # ——————————————————————————————————————————————————————————
    # Transports & handshake
    # ——————————————————————————————————————————————————————————
    async def _bind_listener(self, addr: str) -> tbase.Listener:
        """
        Resolve a multiaddr-like string and bind the appropriate transport listener.
        Supported schemes: tcp://, ws://, wss://, quic:// (if enabled).
        """
        parsed = ma.parse(addr)
        scheme = parsed.scheme
        host, port = parsed.host, parsed.port

        if scheme == "tcp":
            from ..transport import tcp as tmod

            return await tmod.listen(host, port)
        elif scheme in ("ws", "wss"):
            from ..transport import ws as tmod

            return await tmod.listen(
                host, port, secure=(scheme == "wss"), cors=self.cfg.ws_cors
            )
        elif scheme == "quic":
            from ..transport import quic as tmod

            return await tmod.listen(host, port, alpn=self.cfg.quic_alpn)
        else:
            raise ValueError(f"unsupported listen scheme: {scheme}")

    async def _accept_loop(self, listener: tbase.Listener) -> None:
        """
        Accept raw connections, run the Kyber+HKDF handshake to derive AEAD keys, then register with ConnectionManager.
        """
        from ..crypto.handshake import \
            kyber_handshake  # async: (raw_conn, node_keys, hkdf_salt) -> Conn

        hkdf_salt = self.cfg.handshake_hkdf_salt

        async for raw in listener.accept():
            self.metrics.accepted.inc()
            self.loop.create_task(
                self._upgrade_and_register(raw, kyber_handshake, hkdf_salt),
                name="upgrade+register",
            )

    async def _upgrade_and_register(
        self,
        raw: tbase.Conn,
        do_handshake: Callable[[tbase.Conn, Any, bytes], Awaitable[tbase.Conn]],
        hkdf_salt: bytes,
    ) -> None:
        try:
            conn = await do_handshake(raw, self.node_keys, hkdf_salt)
            await self.connmgr.register(conn)
            # Once registered, route frames through the router
            self.loop.create_task(
                self._read_frames(conn), name=f"read@{conn.remote_addr}"
            )
        except Exception as e:
            self.metrics.handshake_failures.inc()
            log.warning("Handshake/registration failed", exc_info=e)
            with contextlib.suppress(Exception):
                await raw.close()

    async def _read_frames(self, conn: tbase.Conn) -> None:
        """
        Read frames from a secure connection and feed them to the router.
        """
        try:
            async for frame in conn.read_frames():
                # Optional fast-path flow control
                if not self.flowctl.permit(conn, frame):
                    continue
                await self.router.dispatch(conn, frame)
                self.metrics.frames_rx.inc()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.debug(
                "conn read error", extra={"peer": str(conn.remote_addr)}, exc_info=e
            )
        finally:
            await self.connmgr.deregister(conn)
            with contextlib.suppress(Exception):
                await conn.close()

    # ——————————————————————————————————————————————————————————
    # Protocols & gossip wiring
    # ——————————————————————————————————————————————————————————
    def _mount_protocols(self) -> None:
        """
        Register all protocol handlers in the router and gossip engine.
        """
        # Router-wide codec (CBOR/msgspec) + checksum is centralized in wire_codec
        codec = wire_codec.Codec()

        # HELLO & identify (version/chain/alg-policy root)
        self.router.add_handler(
            proto_hello.HelloHandler(
                cfg=self.cfg,
                codec=codec,
                identify=self.identify,
                peerstore=self.peerstore,
            )
        )

        # Inventory + data requests (headers/blocks/txs/shares)
        self.router.add_handler(
            proto_inv.InventoryHandler(
                cfg=self.cfg, codec=codec, deps=self.deps, connmgr=self.connmgr
            )
        )
        self.router.add_handler(
            proto_blk.BlockAnnounceHandler(
                cfg=self.cfg, codec=codec, deps=self.deps, gossip=self.gossip
            )
        )
        self.tx_relay_handler = proto_tx.TxRelayHandler(
            cfg=self.cfg,
            codec=codec,
            deps=self.deps,
            gossip=self.gossip,
            ratelimiter=self.ratelimiter,
        )
        self.router.add_handler(self.tx_relay_handler)
        self.router.add_handler(
            proto_share.ShareRelayHandler(
                cfg=self.cfg,
                codec=codec,
                deps=self.deps,
                gossip=self.gossip,
                ratelimiter=self.ratelimiter,
            )
        )

        # Flow control (credits/window updates)
        self.router.add_handler(self.flowctl.handler(codec))

        # Gossip topics
        self.gossip.register_topic(gossip_topics.BLOCKS)
        self.gossip.register_topic(gossip_topics.HEADERS)
        self.gossip.register_topic(gossip_topics.TXS)
        self.gossip.register_topic(gossip_topics.SHARES)
        # DA topics can be added by da/adapter later

    # ——————————————————————————————————————————————————————————
    # Discovery / seeds
    # ——————————————————————————————————————————————————————————
    async def _seed_and_discover(self) -> None:
        """
        Dial configured seeds, then keep the discovery loop running (DNS seeds, Kademlia, mDNS).
        Uses network-specific seeds from config with automatic DNS/IP fallback.
        """
        from ..discovery import kademlia as kad
        from ..discovery import mdns as md
        from ..discovery import seeds as seedmod

        # Use seeds from config (already network-specific with DNS + IP fallback)
        # Seeds are provided as multiaddr strings, ready to dial
        seed_addrs = list(self.cfg.seeds) if self.cfg.seeds else []

        # If no seeds configured, try to discover based on chain_id
        if not seed_addrs:
            try:
                # Try to get chain_id from deps
                chain_id = getattr(self.deps, "chain_id", None)
                if chain_id and chain_id in seedmod.NETWORK_DNS_SEEDS:
                    log.info("[bootstrap] discovering seeds for chain_id=%d", chain_id)
                    bundle = await seedmod.discover_for_network(
                        chain_id, resolve=True, include_fallbacks=True
                    )
                    # Convert SeedEndpoints to multiaddr format
                    for ep in bundle.endpoints:
                        # Determine IP type (ip4 vs ip6) or DNS type
                        try:
                            ip_obj = ipaddress.ip_address(ep.host)
                            ip_type = "ip6" if ip_obj.version == 6 else "ip4"
                        except ValueError:
                            # It's a hostname - use dns4 as default
                            # (dns6 exists but is rarely used; modern DNS resolvers
                            # return both A and AAAA records via dns4)
                            ip_type = "dns4"

                        # Build multiaddr based on scheme
                        if ep.scheme == "quic":
                            # /ip4/host/udp/port/quic-v1 or /dns4/host/udp/port/quic-v1
                            seed_addrs.append(
                                f"/{ip_type}/{ep.host}/udp/{ep.port}/quic-v1"
                            )
                        elif ep.scheme == "tcp":
                            # /ip4/host/tcp/port or /dns4/host/tcp/port
                            seed_addrs.append(f"/{ip_type}/{ep.host}/tcp/{ep.port}")
                        elif ep.scheme in ("ws", "wss"):
                            # /ip4/host/tcp/port/ws or /dns4/host/tcp/port/ws
                            proto = "ws" if ep.scheme == "ws" else "wss"
                            seed_addrs.append(
                                f"/{ip_type}/{ep.host}/tcp/{ep.port}/{proto}"
                            )
                        else:
                            # Fallback: try URL-style (will be parsed by dial)
                            port = f":{ep.port}" if ep.port is not None else ""
                            path = ep.path or ""
                            seed_addrs.append(f"{ep.scheme}://{ep.host}{port}{path}")
            except Exception as e:
                log.warning("Dynamic seed discovery failed", exc_info=e)

        # Dial all seeds (DNS names will be resolved by transport layer)
        for addr in seed_addrs:
            with contextlib.suppress(Exception):
                log.info("[bootstrap] dialing seed %s", addr)
                await self._dial(addr)

        # Run ongoing discovery backends
        tasks = []
        backoff: Dict[str, float] = {}

        async def _periodic_dials() -> None:
            try:
                while not self.stopping:
                    await asyncio.sleep(10.0)
                    try:
                        candidates = [
                            addr
                            for _, addr, _ in self.peerstore.list_addresses(limit=64)
                        ]
                    except Exception:
                        candidates = []
                    now = time.time()
                    for addr in list(dict.fromkeys(seed_addrs + candidates)):
                        if backoff.get(addr, 0.0) > now:
                            continue
                        backoff[addr] = now + 30.0
                        self.loop.create_task(self._dial(addr), name=f"dial@{addr}")
            except asyncio.CancelledError:
                return

        tasks.append(asyncio.create_task(_periodic_dials(), name="seed-loop"))
        if self.cfg.discovery.enable_kademlia:
            tasks.append(
                asyncio.create_task(
                    kad.run(self.cfg, self.peerstore, self.connmgr), name="kad"
                )
            )
        if self.cfg.discovery.enable_mdns:
            tasks.append(
                asyncio.create_task(
                    md.run(self.cfg, self.peerstore, self.connmgr), name="mdns"
                )
            )

        try:
            await asyncio.gather(*tasks)
        finally:
            for t in tasks:
                t.cancel()
            await self._gather("discovery-backends", *tasks)

    async def _dial(self, addr: str) -> None:
        """
        Dial and upgrade a single address. Called by discovery and manual CLI.
        """
        parsed = ma.parse(addr)
        scheme = parsed.scheme
        host, port = parsed.host, parsed.port

        if scheme == "tcp":
            from ..transport import tcp as tmod

            raw = await tmod.dial(host, port, timeout=self.cfg.dial_timeout)
        elif scheme in ("ws", "wss"):
            from ..transport import ws as tmod

            raw = await tmod.dial(
                host, port, secure=(scheme == "wss"), timeout=self.cfg.dial_timeout
            )
        elif scheme == "quic":
            from ..transport import quic as tmod

            raw = await tmod.dial(
                host, port, alpn=self.cfg.quic_alpn, timeout=self.cfg.dial_timeout
            )
        else:
            raise ValueError(f"unsupported dial scheme: {scheme}")

        from ..crypto.handshake import kyber_handshake

        conn = await kyber_handshake(raw, self.node_keys, self.cfg.handshake_hkdf_salt)
        await self.connmgr.register(conn)
        self.loop.create_task(self._read_frames(conn), name=f"read@{conn.remote_addr}")

    # ——————————————————————————————————————————————————————————
    # Utilities
    # ——————————————————————————————————————————————————————————
    async def _gather(self, label: str, *tasks: asyncio.Task) -> None:
        if not tasks:
            return
        res = await asyncio.gather(*tasks, return_exceptions=True)
        for r in res:
            if isinstance(r, Exception) and not isinstance(r, asyncio.CancelledError):
                log.debug("task error", extra={"where": label}, exc_info=r)

    def _install_signal_handlers(self) -> None:
        # Safe on POSIX; ignored on platforms that don't support it
        try:
            self.loop.add_signal_handler(
                signal.SIGTERM, lambda: asyncio.create_task(self.stop())
            )
            self.loop.add_signal_handler(
                signal.SIGINT, lambda: asyncio.create_task(self.stop())
            )
        except NotImplementedError:
            pass

    # Public helpers for CLI/ops
    async def publish(self, topic: str, payload: bytes) -> None:
        await self.gossip.publish(topic, payload)

    def health(self) -> Dict[str, Any]:
        return node_health.snapshot(
            peer_id=self.peer_id.hex(),
            version=p2p_version.__version__,
            listeners=[l.addr for l in self._listeners],
            peers=self.connmgr.snapshot(),
            gossip=self.gossip.snapshot(),
        )


# -------------------------------------------------------------------------------------
# Thin compatibility service (devnet-friendly)
# -------------------------------------------------------------------------------------


class P2PServiceLegacy:
    """
    Lightweight wrapper that exposes a stable API for the CLI and tests.

    The full NodeService above is more featureful but is still being wired. To
    keep the listener CLI functional across environments, we provide a
    deterministic TCP-only service that performs the authenticated handshake and
    tracks connected peers in-memory.
    """

    def __init__(
        self,
        *,
        listen_addrs: list[str] | None = None,
        seeds: list[str] | None = None,
        chain_id: int = 0,
        enable_quic: bool = False,
        enable_ws: bool = False,
        nat: bool = False,
        deps: Any = None,
        peerstore_path: str | None = None,
    ) -> None:
        from ..transport.base import ListenConfig
        from ..transport.multiaddr import parse_multiaddr
        from ..transport.tcp import TcpTransport  # lazy import

        self.listen_addrs = listen_addrs or ["/ip4/0.0.0.0/tcp/42069"]
        self.seeds = seeds or []
        self.chain_id = chain_id
        self.enable_quic = enable_quic
        self.enable_ws = enable_ws
        self.nat = nat
        self.deps = deps

        self.loop = asyncio.get_event_loop()
        prologue = f"animica/tcp/{chain_id}".encode()
        self._transport = TcpTransport(handshake_prologue=prologue, chain_id=chain_id)
        self._listen_cfg = ListenConfig(addr="tcp://0.0.0.0:0")
        self._accept_task: asyncio.Task | None = None
        self._dial_tasks: list[asyncio.Task] = []
        self._consensus_task: asyncio.Task | None = None
        self._peers: Dict[str, Dict[str, Any]] = {}
        self._running = False
        self._parse_multiaddr = parse_multiaddr
        self._listen_config_cls = ListenConfig
        self._log = logging.getLogger("animica.p2p.service")

        # Initialize persistent peer store
        if peerstore_path is None:
            # Default to network-specific directory
            network_name = {1: "mainnet", 2: "testnet", 1337: "devnet"}.get(
                chain_id, "custom"
            )
            peerstore_path = os.path.expanduser(f"~/.animica/p2p/{network_name}")
        self.peerstore = pstore.PeerStore(peerstore_path)
        self._log.info(f"Initialized persistent peer store at {peerstore_path}")

        # Lazy identify helper (filled on-demand). We intentionally keep the
        # devnet-friendly service light, but still want peers to exchange
        # basic metadata (height, versions, caps) so operators can confirm the
        # network is healthy.
        self._identify = idsvc.perform_identify

        # Metrics object for test compatibility
        class _Metrics:
            def __init__(self, service):
                self._service = service

            @property
            def peer_count(self):
                # Count both in-memory and persistent peers
                return len(self._service._peers)

        self.metrics = _Metrics(self)

    async def start(self) -> None:
        if self._running:
            return
        self._running = True

        # Load previously known peers from persistent store
        try:
            known_peers = self.peerstore.list_known(limit=100)
            self._log.info(f"Loaded {len(known_peers)} peers from persistent store")
        except Exception as e:
            self._log.warning(f"Failed to load peers from store: {e}", exc_info=True)
            known_peers = []

        # Bind listeners
        for ma in self.listen_addrs:
            parsed = self._parse_multiaddr(ma)
            if parsed.transport != "tcp":
                continue
            host = parsed.host or "0.0.0.0"
            port = parsed.port or 0
            cfg = self._listen_config_cls(addr=f"tcp://{host}:{port}")
            await self._transport.listen(cfg)
        self._accept_task = self.loop.create_task(
            self._accept_loop(), name="tcp-accept"
        )

        # Dial seeds (best-effort, fire-and-forget)
        seed_count = 0
        for seed in self.seeds:
            try:
                parsed = self._parse_multiaddr(seed)
            except Exception as e:
                self._log.warning(f"Failed to parse seed address {seed}: {e}")
                continue
            if parsed.transport != "tcp":
                self._log.debug(
                    f"Skipping non-TCP seed: {seed} (transport={parsed.transport})"
                )
                continue
            addr = f"tcp://{parsed.host}:{parsed.port}"
            self._log.info(f"Dialing seed: {addr}")
            self._dial_tasks.append(
                self.loop.create_task(self._dial(addr), name=f"dial@{addr}")
            )
            seed_count += 1

        if seed_count == 0 and len(self.seeds) > 0:
            self._log.warning(
                f"No TCP seeds to dial (total seeds: {len(self.seeds)}). Ensure at least one TCP seed is configured."
            )
        elif seed_count == 0:
            self._log.warning(
                "No seeds configured. Node will not connect to network unless peers connect to it."
            )

        # Also try to reconnect to previously known peers (best effort)
        for peer in known_peers[:10]:  # Limit to first 10 to avoid overwhelming
            if hasattr(peer, "address") and peer.address:
                try:
                    # Parse address and dial
                    addr_str = peer.address
                    if addr_str.startswith("/"):
                        parsed = self._parse_multiaddr(addr_str)
                        if parsed.transport == "tcp":
                            addr_str = f"tcp://{parsed.host}:{parsed.port}"
                    self._dial_tasks.append(
                        self.loop.create_task(
                            self._dial(addr_str), name=f"redial@{addr_str}"
                        )
                    )
                except Exception:
                    pass  # Skip invalid addresses

        # Continuous consensus/identify probing so peers agree on head hash/height
        self._consensus_task = self.loop.create_task(
            self._consensus_watch_loop(), name="consensus-watch"
        )

        self._log.info(
            "Started full P2P service",
            extra={
                "listen": self.listen_addrs,
                "seeds": self.seeds,
                "known_peers": len(known_peers),
            },
        )

    async def stop(self) -> None:
        self._running = False
        if self._accept_task:
            self._accept_task.cancel()
            with contextlib.suppress(Exception):
                await self._accept_task
        for t in self._dial_tasks:
            t.cancel()
        if self._dial_tasks:
            await asyncio.gather(*self._dial_tasks, return_exceptions=True)
        if self._consensus_task:
            self._consensus_task.cancel()
            with contextlib.suppress(Exception):
                await self._consensus_task
        # Close live connections and record disconnections
        for peer in list(self._peers.values()):
            conn = peer.get("conn")
            peer_id = peer.get("peer_id")
            if conn:
                with contextlib.suppress(Exception):
                    await conn.close()
            # Record disconnection in peer store
            if peer_id:
                try:
                    self.peerstore.record_disconnection(peer_id)
                except Exception:
                    pass
        with contextlib.suppress(Exception):
            await self._transport.close()

    async def _accept_loop(self) -> None:
        try:
            while self._running:
                try:
                    conn = await self._transport.accept()
                except asyncio.CancelledError:
                    return
                except Exception:
                    if self._running:
                        self._log.warning("accept loop terminating", exc_info=True)
                    return
                self._track_peer(conn, direction="inbound")
        except asyncio.CancelledError:
            # Swallow expected cancellation during shutdown
            return

    async def _dial(self, addr: str) -> None:
        try:
            conn = await self._transport.dial(addr, timeout=5.0)
        except Exception as e:
            self._log.warning(f"Failed to dial {addr}: {e.__class__.__name__}: {e}")
            return
        self._track_peer(conn, direction="outbound")

    def _track_peer(self, conn: Any, direction: str = "outbound") -> None:
        remote = (
            getattr(conn.info, "remote_addr", None)
            or getattr(conn, "remote_addr", None)
            or "unknown"
        )
        # Generate peer_id from remote address
        peer_id = f"peer_{hashlib.sha256(str(remote).encode()).hexdigest()[:32]}"

        self._peers[remote] = {
            "remote": remote,
            "connected": True,
            "last_seen": time.time(),
            "conn": conn,
            "peer_id": peer_id,
            "direction": direction,
        }
        self._log.info(
            "peer connected",
            extra={"remote": remote, "peer_id": peer_id, "direction": direction},
        )

        # Persist peer to PeerStore
        try:
            self.peerstore.add(
                peer_id=peer_id, addrs=[str(remote)], score=0.0, direction=direction
            )
            self.peerstore.record_connection(peer_id)
            self._log.debug(
                f"Persisted peer {peer_id} to store with direction={direction}"
            )
        except Exception as e:
            self._log.warning(f"Failed to persist peer to store: {e}", exc_info=True)

        # Best-effort IDENTIFY so peers exchange heights/caps/versions.
        async def _do_identify() -> None:
            info: Dict[str, Any] | None = None
            try:
                local_height, local_hash = self._local_head()
                info = await self._identify(
                    conn,
                    timeout=5.0,
                    local_height=local_height,
                    network_id=str(self.chain_id),
                    agent=f"animica-p2p/{p2p_version.__version__}",
                    head_hash=local_hash,
                )
            except Exception:
                self._log.debug(
                    "identify failed", exc_info=True, extra={"remote": remote}
                )
            if info:
                self._peers[remote].update(
                    info=info,
                    height=info.get("height"),
                    head_hash=info.get("head_hash"),
                )
                self._log.info(
                    "peer identified",
                    extra={
                        "remote": remote,
                        "network": info.get("network_id"),
                        "height": info.get("height"),
                        "head_hash": info.get("head_hash"),
                    },
                )
                # Update peer store with identified info
                try:
                    self.peerstore.record_seen(peer_id, str(remote))
                except Exception:
                    pass

        self.loop.create_task(_do_identify(), name=f"identify@{remote}")

    async def _consensus_watch_loop(self) -> None:
        """
        Periodically re-identify peers to keep head height/hash in sync.
        Logs divergences so operators know when consensus is drifting.
        """
        try:
            while self._running:
                await asyncio.sleep(10.0)
                local_height, local_hash = self._local_head()
                for remote, peer in list(self._peers.items()):
                    conn = peer.get("conn")
                    peer_id = peer.get("peer_id")
                    if conn is None or peer_id is None:
                        continue
                    try:
                        info = await self._identify(
                            conn,
                            timeout=5.0,
                            local_height=local_height,
                            network_id=str(self.chain_id),
                            agent=f"animica-p2p/{p2p_version.__version__}",
                            head_hash=local_hash,
                        )
                        # Update local cache
                        peer.update(
                            info=info,
                            height=info.get("height"),
                            head_hash=info.get("head_hash"),
                        )
                        remote_height = int(info.get("height") or 0)
                        remote_hash = info.get("head_hash")

                        # Persist latest height for prioritization/dialing
                        try:
                            self.peerstore.update_head_height(peer_id, remote_height)
                            self.peerstore.record_seen(
                                peer_id, peer.get("remote") or remote
                            )
                        except Exception:
                            pass

                        # Surface consensus drift
                        if (
                            local_hash
                            and remote_hash
                            and remote_height == local_height
                            and str(remote_hash) != str(local_hash)
                        ):
                            self._log.warning(
                                "Consensus mismatch detected",
                                extra={
                                    "peer": peer_id,
                                    "remote": remote,
                                    "local_height": local_height,
                                    "local_head": local_hash,
                                    "remote_head": remote_hash,
                                },
                            )
                    except Exception:
                        self._log.debug(
                            "consensus probe failed",
                            exc_info=True,
                            extra={"peer": peer_id, "remote": remote},
                        )
        except asyncio.CancelledError:
            return

    def _local_height(self) -> int:
        """Read the local canonical height if available (best-effort)."""

        if self.deps and hasattr(self.deps, "block_db"):
            try:
                getter = getattr(self.deps.block_db, "get_canonical_height", None)
                if callable(getter):
                    return int(getter())
                head = getattr(self.deps.block_db, "get_head", None)
                if callable(head):
                    h, _hdr = head()
                    return int(h)
            except Exception:
                self._log.debug("local height probe failed", exc_info=True)
        return 0

    def _local_head(self) -> tuple[int, Optional[str]]:
        """
        Return (height, head_hash_hex) for the local node when available.
        Falls back to height-only if the hash cannot be read.
        """
        height = self._local_height()
        head_hash: Optional[str] = None

        if self.deps and hasattr(self.deps, "block_db"):
            block_db = self.deps.block_db
            try:
                # Prefer tuple (height, hash_bytes)
                head_tuple = None
                if hasattr(block_db, "get_canonical_head"):
                    head_tuple = block_db.get_canonical_head()
                elif hasattr(block_db, "get_head"):
                    head_tuple = block_db.get_head()

                if (
                    head_tuple
                    and isinstance(head_tuple, (list, tuple))
                    and len(head_tuple) >= 2
                ):
                    height = int(head_tuple[0])
                    hh = head_tuple[1]
                    if isinstance(hh, (bytes, bytearray)):
                        head_hash = hh.hex()
                    elif isinstance(hh, str):
                        head_hash = hh
            except Exception:
                self._log.debug("local head probe failed", exc_info=True)

        return height, head_hash

    # Exposed for tests/ops
    @property
    def peers(self) -> Dict[str, Dict[str, Any]]:
        return {
            k: {kk: vv for kk, vv in v.items() if kk != "conn"}
            for k, v in self._peers.items()
        }

    async def dial(self, addr: str) -> None:
        """
        Public dial method for tests and CLI.
        Connects to a peer at the given multiaddr string.
        """
        # Parse multiaddr format (/ip4/host/tcp/port) to tcp://host:port
        if addr.startswith("/"):
            try:
                parsed = self._parse_multiaddr(addr)
                if parsed.transport == "tcp":
                    addr = f"tcp://{parsed.host}:{parsed.port}"
            except Exception:
                pass  # Fall through and try as-is
        await self._dial(addr)


# -------------------------------------------------------------------------------------
# Public service: use the production implementation by default.
# -------------------------------------------------------------------------------------

# This repo historically shipped a devnet-only stub (P2PServiceLegacy).  Keep it
# around for reference/tests, but export the real service as `P2PService`.
from .p2p_service import P2PService  # noqa: E402
