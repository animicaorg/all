"""
Animica P2P — mDNS presence & discovery (optional)
==================================================

This module exposes a tiny, optional mDNS layer so nodes on the same LAN can
find each other without seed servers. It **gracefully no-ops** if the
`zeroconf` package isn't installed.

Service type
------------
We advertise `_animica._tcp.local.` with a TXT record carrying:
- `peer`:    short peer id (sha3(pubkey)+alg id)
- `chain`:   chain id (int)
- `vers`:    node version string
- `endpt`:   comma-separated list of dialable endpoints (e.g. tcp://host:6750,quic://host:6751)
- `proto`:   supported protos (e.g. "tcp,quic,ws")
- `ts`:      unix seconds (advert time)

Usage
-----
# Advertise:
from p2p.discovery.mdns import MdnsAdvertiser
adv = MdnsAdvertiser(
    peer_id="12ab34cd…", chain_id=1, port=6750,
    version="0.1.0",
    endpoints=["tcp://192.168.1.20:6750","quic://192.168.1.20:6751"]
)
await adv.start()
# …
await adv.close()

# Browse:
from p2p.discovery.mdns import MdnsBrowser
browser = MdnsBrowser()
await browser.start()
async for evt in browser.events_iter():
    print(evt.kind, evt.name, evt.txt.get("endpt"))
# …
await browser.close()

Install (optional)
------------------
    pip install zeroconf

Notes
-----
- mDNS is **best-effort**: do not rely on it for production bootstrapping.
- On some networks multicast may be blocked.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import struct
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, AsyncIterator

_LOG = logging.getLogger("p2p.discovery.mdns")

# Optional dependency
try:
    from zeroconf import (
        IPVersion,
        ServiceBrowser,
        ServiceInfo,
        Zeroconf,
        InterfaceChoice,
    )
    _HAS_ZC = True
except Exception:  # pragma: no cover - environment-specific
    _HAS_ZC = False


_SERVICE_TYPE = "_animica._tcp.local."


def _ipv4_addrs() -> List[bytes]:
    """Collect local IPv4 addresses encoded as 4-byte packed form for zeroconf."""
    addrs: List[bytes] = []
    try:
        infos = socket.getaddrinfo(None, 0, family=socket.AF_INET, type=socket.SOCK_STREAM)
        # getaddrinfo(None, 0, …) returns wildcard 0.0.0.0; we prefer interface scan by hostname below.
    except Exception:
        infos = []

    # Fallback via hostname resolution
    try:
        host = socket.gethostname()
        for fam, _, _, _, sa in socket.getaddrinfo(host, None, socket.AF_INET):
            if fam == socket.AF_INET:
                try:
                    addrs.append(socket.inet_aton(sa[0]))
                except Exception:
                    pass
    except Exception:
        pass

    # Always ensure at least loopback exists to satisfy zeroconf
    if not addrs:
        addrs.append(socket.inet_aton("127.0.0.1"))

    # Dedupe while preserving order
    seen = set()
    out: List[bytes] = []
    for a in addrs:
        if a not in seen:
            seen.add(a)
            out.append(a)
    return out


def _mk_txt(peer_id: str, chain_id: int, version: str, endpoints: List[str]) -> Dict[str, bytes]:
    # TXT values must be bytes
    ep = ",".join(endpoints)[:650]  # stay well below 255 per key by splitting if needed (we keep it short anyway)
    return {
        b"peer": peer_id.encode("utf-8"),
        b"chain": str(chain_id).encode("utf-8"),
        b"vers": version.encode("utf-8"),
        b"endpt": ep.encode("utf-8"),
        b"proto": ",".join(sorted({e.split("://", 1)[0] for e in endpoints})).encode("utf-8"),
        b"ts": str(int(time.time())).encode("utf-8"),
    }


def _parse_txt(txt: Dict[bytes, bytes]) -> Dict[str, str]:
    return {k.decode("utf-8", "ignore"): v.decode("utf-8", "ignore") for k, v in txt.items()}


@dataclass(frozen=True)
class MdnsEvent:
    kind: str          # "added" | "removed" | "updated"
    name: str          # instance name
    host: str          # target hostname
    port: int
    txt: Dict[str, str] = field(default_factory=dict)


class MdnsAdvertiser:
    """
    Register a local service via mDNS (zeroconf). No-ops if zeroconf is unavailable.
    """

    def __init__(
        self,
        peer_id: str,
        chain_id: int,
        port: int,
        version: str,
        endpoints: Optional[List[str]] = None,
        instance_name: Optional[str] = None,
        ttl: int = 120,
    ):
        self.peer_id = peer_id
        self.chain_id = int(chain_id)
        self.port = int(port)
        self.version = version
        self.endpoints = endpoints or [f"tcp://{socket.gethostname()}:{port}"]
        self.instance = instance_name or f"animica-{peer_id[:8]}-{self.chain_id}"
        self.ttl = ttl

        self._zc: Optional["Zeroconf"] = None
        self._info: Optional["ServiceInfo"] = None

    async def start(self) -> None:
        if not _HAS_ZC:  # pragma: no cover
            _LOG.info("zeroconf not installed; MdnsAdvertiser no-op")
            return

        def _register() -> None:
            addrs = _ipv4_addrs()
            props = _mk_txt(self.peer_id, self.chain_id, self.version, self.endpoints)
            info = ServiceInfo(
                type_=_SERVICE_TYPE,
                name=f"{self.instance}.{_SERVICE_TYPE}",
                addresses=addrs,
                port=self.port,
                weight=0,
                priority=0,
                properties=props,
                server=f"{socket.gethostname()}.",
            )
            zc = Zeroconf(interfaces=InterfaceChoice.Default, ip_version=IPVersion.V4Only)
            zc.register_service(info, ttl=self.ttl)
            self._zc = zc
            self._info = info
            _LOG.info("mDNS advertised %s on port %d", self.instance, self.port)

        await asyncio.to_thread(_register)

    async def close(self) -> None:
        if not _HAS_ZC:  # pragma: no cover
            return
        def _unregister() -> None:
            if self._zc and self._info:
                try:
                    self._zc.unregister_service(self._info)
                except Exception:
                    pass
                try:
                    self._zc.close()
                except Exception:
                    pass
        await asyncio.to_thread(_unregister)


class _Listener:  # zeroconf callback target
    def __init__(self, zc: "Zeroconf", queue: "asyncio.Queue[MdnsEvent]"):
        self.zc = zc
        self.q = queue

    def _emit(self, kind: str, type_: str, name: str) -> None:
        try:
            info = self.zc.get_service_info(type_, name, 2000)
            if info is None:
                # Removed before we could fetch details; synthesize minimal event
                evt = MdnsEvent(kind=kind, name=name, host="", port=0, txt={})
            else:
                host = info.server.rstrip(".")
                port = info.port
                txt = _parse_txt(info.properties or {})
                evt = MdnsEvent(kind=kind, name=name, host=host, port=port, txt=txt)
        except Exception:
            evt = MdnsEvent(kind=kind, name=name, host="", port=0, txt={})
        try:
            # Non-async context; put_nowait to avoid blocking zeroconf threads
            self.q.put_nowait(evt)
        except Exception:
            pass

    # zeroconf listener API:
    def add_service(self, zc: "Zeroconf", type_: str, name: str) -> None:  # noqa: N802
        self._emit("added", type_, name)

    def update_service(self, zc: "Zeroconf", type_: str, name: str) -> None:  # noqa: N802
        self._emit("updated", type_, name)

    def remove_service(self, zc: "Zeroconf", type_: str, name: str) -> None:  # noqa: N802
        self._emit("removed", type_, name)


class MdnsBrowser:
    """
    mDNS browser that streams add/update/remove events into an asyncio Queue.
    No-ops if zeroconf is unavailable.
    """

    def __init__(self, service_type: str = _SERVICE_TYPE):
        self.service_type = service_type
        self._zc: Optional["Zeroconf"] = None
        self._browser: Optional["ServiceBrowser"] = None
        self._queue: "asyncio.Queue[MdnsEvent]" = asyncio.Queue()

    async def start(self) -> None:
        if not _HAS_ZC:  # pragma: no cover
            _LOG.info("zeroconf not installed; MdnsBrowser no-op")
            return

        def _start() -> None:
            zc = Zeroconf(interfaces=InterfaceChoice.Default, ip_version=IPVersion.V4Only)
            listener = _Listener(zc, self._queue)
            browser = ServiceBrowser(zc, self.service_type, listener=listener)
            self._zc = zc
            self._browser = browser
            _LOG.info("mDNS browsing started for %s", self.service_type)

        await asyncio.to_thread(_start)

    async def close(self) -> None:
        if not _HAS_ZC:  # pragma: no cover
            return

        def _stop() -> None:
            if self._browser:
                try:
                    # No explicit stop on ServiceBrowser; closing Zeroconf stops it
                    pass
                except Exception:
                    pass
            if self._zc:
                try:
                    self._zc.close()
                except Exception:
                    pass
        await asyncio.to_thread(_stop)

    async def events_iter(self) -> AsyncIterator[MdnsEvent]:
        """Async iterator over discovered events."""
        while True:
            evt = await self._queue.get()
            yield evt


# Stubs to make imports safe when zeroconf is missing
if not _HAS_ZC:  # pragma: no cover
    class MdnsAdvertiser:  # type: ignore[no-redef]
        def __init__(self, *a, **k):
            _LOG.info("MdnsAdvertiser stub active (pip install zeroconf to enable)")

        async def start(self) -> None:
            _LOG.info("MdnsAdvertiser.start(): no-op (zeroconf missing)")

        async def close(self) -> None:
            _LOG.info("MdnsAdvertiser.close(): no-op (zeroconf missing)")

    class MdnsBrowser:  # type: ignore[no-redef]
        def __init__(self, *a, **k):
            self._queue = asyncio.Queue()
            _LOG.info("MdnsBrowser stub active (pip install zeroconf to enable)")

        async def start(self) -> None:
            _LOG.info("MdnsBrowser.start(): no-op (zeroconf missing)")

        async def close(self) -> None:
            _LOG.info("MdnsBrowser.close(): no-op (zeroconf missing)")

        async def events_iter(self) -> AsyncIterator[MdnsEvent]:
            while False:
                yield MdnsEvent(kind="added", name="", host="", port=0, txt={})


__all__ = [
    "MdnsAdvertiser",
    "MdnsBrowser",
    "MdnsEvent",
]
