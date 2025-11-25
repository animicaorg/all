"""
Animica P2P — Optional NAT traversal helpers (UPnP / NAT-PMP)
==============================================================

This module provides best-effort, dependency-optional helpers to discover the
public IP and to map/unmap a listening port using:

- UPnP IGD (via `miniupnpc`, if available)
- NAT-PMP / PCP (via `natpmp` Python bindings, if available)

Design goals:
- Non-fatal: if nothing is available, we safely no-op.
- Async-friendly: blocking calls are routed through `asyncio.to_thread`.
- Graceful teardown: mappings are deleted on `stop()` when possible.
- Renewal: mappings are refreshed periodically before TTL expiry.

Example
-------
>>> import asyncio
>>> from p2p.discovery.nat import NATManager
>>>
>>> async def main():
...     nat = NATManager(internal_port=6769, proto="TCP", desc="Animica P2P")
...     await nat.start()                      # best-effort public mapping
...     print("Public:", nat.public_endpoint())# ('203.0.113.5', 6769) or (None, None)
...     await asyncio.sleep(10)
...     await nat.stop()                       # best-effort unmap
...
>>> asyncio.run(main())

Security notes
--------------
- This only tries to open inbound pinholes on *your* gateway when explicitly
  requested via `start()`. It never touches random routers.
- Discovery and mapping results are informational; validate externally if needed.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
from dataclasses import dataclass
from typing import Optional, Tuple

_LOG = logging.getLogger("p2p.discovery.nat")

# ---- Optional deps -----------------------------------------------------------

# miniupnpc (UPnP IGD)
try:  # pragma: no cover - optional path
    import miniupnpc as _miniupnpc  # type: ignore
except Exception:  # pragma: no cover - optional
    _miniupnpc = None  # type: ignore

# natpmp (NAT-PMP / PCP)
try:  # pragma: no cover - optional path
    # There are a few variants; try the common one first.
    from natpmp import NatPMP, NATPMPNetworkError, NATPMPProtocolError  # type: ignore
except Exception:  # pragma: no cover - optional
    NatPMP = None  # type: ignore
    NATPMPNetworkError = Exception  # type: ignore
    NATPMPProtocolError = Exception  # type: ignore


# ---- Data model --------------------------------------------------------------

@dataclass
class NATStatus:
    method: Optional[str] = None         # "upnp" | "natpmp" | None
    public_ip: Optional[str] = None
    public_port: Optional[int] = None
    internal_ip: Optional[str] = None
    internal_port: Optional[int] = None
    proto: str = "TCP"
    ttl: int = 30 * 60                   # seconds (mapping lifetime)
    ok: bool = False
    details: Optional[str] = None


# ---- Manager -----------------------------------------------------------------

class NATManager:
    """
    A small stateful helper that tries UPnP first, then NAT-PMP, to:
      - discover public IP
      - map an inbound port (public_port -> internal_port)
      - renew mapping periodically
      - unmap on stop()

    All operations are best-effort and will never raise to the caller unless
    the event loop is closed. Inspect `status` for results.
    """

    def __init__(
        self,
        internal_port: int,
        public_port: Optional[int] = None,
        proto: str = "TCP",
        desc: str = "Animica P2P",
        ttl_seconds: int = 30 * 60,
        prefer: str = "auto",   # "auto" | "upnp" | "natpmp"
    ) -> None:
        self.internal_port = int(internal_port)
        self.public_port = int(public_port or internal_port)
        self.proto = proto.upper()
        self.desc = str(desc)
        self.ttl = int(ttl_seconds)
        self.prefer = prefer
        self.status = NATStatus(
            method=None,
            public_ip=None,
            public_port=None,
            internal_ip=None,
            internal_port=self.internal_port,
            proto=self.proto,
            ttl=self.ttl,
            ok=False,
            details=None,
        )
        self._task: Optional[asyncio.Task] = None
        self._stopped = asyncio.Event()

        # Backends
        self._upnpc = None       # type: ignore
        self._natpmp = None      # type: ignore

    # -- lifecycle -------------------------------------------------------------

    async def start(self) -> NATStatus:
        """
        Try to establish a public mapping. Chooses backend according to `prefer`.
        """
        self._stopped.clear()

        order = ["upnp", "natpmp"]
        if self.prefer == "upnp":
            order = ["upnp", "natpmp"]
        elif self.prefer == "natpmp":
            order = ["natpmp", "upnp"]

        # Try in order until one succeeds.
        for method in order:
            if method == "upnp" and _miniupnpc is not None:
                ok = await self._try_upnp()
                if ok:
                    self.status.method = "upnp"
                    break
            if method == "natpmp" and NatPMP is not None:
                ok = await self._try_natpmp()
                if ok:
                    self.status.method = "natpmp"
                    break

        if not self.status.ok:
            self.status.details = "No NAT traversal available (no UPnP/NAT-PMP)."
            _LOG.info(self.status.details)

        # Start renewal loop if mapping is live
        if self.status.ok:
            self._task = asyncio.create_task(self._renew_loop(), name="nat-renew")

        return self.status

    async def stop(self) -> None:
        """Best-effort unmap and stop renewing."""
        self._stopped.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        try:
            if self.status.method == "upnp" and self._upnpc is not None:
                await asyncio.to_thread(
                    self._upnpc.deleteportmapping, self.public_port, self.proto
                )
            elif self.status.method == "natpmp" and self._natpmp is not None:
                # NAT-PMP "delete" is re-map with lifetime=0
                await asyncio.to_thread(
                    self._natpmp.send_new_port_mapping_request,
                    6 if self.proto == "TCP" else 17,
                    self.internal_port,
                    self.public_port,
                    0,  # lifetime
                )
        except Exception as e:  # best-effort
            _LOG.debug("NAT unmap failed: %r", e)

    def public_endpoint(self) -> Tuple[Optional[str], Optional[int]]:
        """Return (public_ip, public_port), or (None, None) if not mapped."""
        return (self.status.public_ip, self.status.public_port)

    # -- backend: UPnP ---------------------------------------------------------

    async def _try_upnp(self) -> bool:
        try:
            ok = await asyncio.to_thread(self._upnp_map)
            if ok:
                _LOG.info(
                    "UPnP mapped %s %s:%d -> %s:%d (TTL=%ds)",
                    self.proto,
                    self.status.public_ip,
                    self.status.public_port or -1,
                    self.status.internal_ip,
                    self.internal_port,
                    self.ttl,
                )
                return True
        except Exception as e:
            _LOG.debug("UPnP mapping failed: %r", e)
        return False

    def _upnp_map(self) -> bool:
        if _miniupnpc is None:
            return False
        u = _miniupnpc.UPnP()  # type: ignore[operator]
        u.discoverdelay = 200
        try:
            # discover returns number of devices; we don't rely on it
            u.discover()
            u.selectigd()
        except Exception:
            return False

        internal_ip = u.lanaddr
        proto = self.proto
        pub_port = self.public_port
        ok = u.addportmapping(
            pub_port,
            proto,
            internal_ip,
            self.internal_port,
            self.desc,
            ""  # remoteHost (wildcard)
        )
        if not ok:
            return False
        try:
            public_ip = u.externalipaddress()
            # Normalize IP
            ipaddress.ip_address(public_ip)
        except Exception:
            public_ip = None  # type: ignore[assignment]

        self._upnpc = u
        self.status.ok = True
        self.status.internal_ip = internal_ip
        self.status.public_ip = public_ip
        self.status.public_port = pub_port
        self.status.details = "upnp:ok"
        return True

    async def _upnp_renew(self) -> None:
        if self._upnpc is None:
            return
        # Re-add the same mapping (some IGDs treat this as renewal).
        try:
            await asyncio.to_thread(
                self._upnpc.addportmapping,
                self.public_port,
                self.proto,
                self._upnpc.lanaddr,
                self.internal_port,
                self.desc,
                "",
            )
        except Exception as e:
            _LOG.debug("UPnP renew failed: %r", e)

    # -- backend: NAT-PMP / PCP ------------------------------------------------

    async def _try_natpmp(self) -> bool:
        try:
            ok = await asyncio.to_thread(self._natpmp_map)
            if ok:
                _LOG.info(
                    "NAT-PMP mapped %s :%d -> :%d (public %s:%d, TTL=%ds)",
                    self.proto,
                    self.internal_port,
                    self.public_port,
                    self.status.public_ip,
                    self.status.public_port or -1,
                    self.ttl,
                )
                return True
        except Exception as e:
            _LOG.debug("NAT-PMP mapping failed: %r", e)
        return False

    def _natpmp_map(self) -> bool:
        if NatPMP is None:
            return False
        try:
            gw = NatPMP()  # autodetect gateway
            pub = gw.get_public_address()
            # Many bindings expose as a tuple of 4 ints or a string; normalize
            public_ip = getattr(pub, "ip", None)
            if isinstance(public_ip, (tuple, list)) and len(public_ip) == 4:
                public_ip = ".".join(str(x) for x in public_ip)
            if isinstance(public_ip, int):
                # some libs return a 32-bit int
                public_ip = ".".join(str((public_ip >> (8 * i)) & 0xFF) for i in (3, 2, 1, 0))
            ipaddress.ip_address(public_ip)  # validate
        except Exception:
            public_ip = None

        proto_num = 6 if self.proto == "TCP" else 17
        # Map: some libs provide dedicated map_tcp_port/map_udp_port; others expose a generic call.
        try:
            if hasattr(gw, "map_tcp_port") and self.proto == "TCP":
                res = gw.map_tcp_port(self.internal_port, self.public_port, self.ttl)
            elif hasattr(gw, "map_udp_port") and self.proto == "UDP":
                res = gw.map_udp_port(self.internal_port, self.public_port, self.ttl)
            else:
                # Generic
                res = gw.send_new_port_mapping_request(
                    proto_num, self.internal_port, self.public_port, self.ttl
                )
            # obtain actual external port if library returns it
            pub_port = getattr(res, "public_port", None) or self.public_port
        except (NATPMPNetworkError, NATPMPProtocolError, Exception):
            return False

        self._natpmp = gw
        self.status.ok = True
        self.status.internal_ip = None  # NAT-PMP API doesn't expose LAN IP reliably
        self.status.public_ip = public_ip
        self.status.public_port = int(pub_port)
        self.status.details = "natpmp:ok"
        return True

    async def _natpmp_renew(self) -> None:
        if self._natpmp is None:
            return
        try:
            await asyncio.to_thread(
                self._natpmp.send_new_port_mapping_request,
                (6 if self.proto == "TCP" else 17),
                self.internal_port,
                self.public_port,
                self.ttl,
            )
        except Exception as e:
            _LOG.debug("NAT-PMP renew failed: %r", e)

    # -- renewal loop ----------------------------------------------------------

    async def _renew_loop(self) -> None:
        # Renew a bit before TTL to be conservative.
        # Use a minimum of 5 minutes for safety if ttl is very large.
        interval = max(300, int(self.ttl * 0.6))
        try:
            while not self._stopped.is_set():
                await asyncio.wait_for(self._stopped.wait(), timeout=interval)
                if self._stopped.is_set():
                    break
                if self.status.method == "upnp":
                    await self._upnp_renew()
                elif self.status.method == "natpmp":
                    await self._natpmp_renew()
        except asyncio.TimeoutError:
            # normal wake-up
            pass
        except asyncio.CancelledError:
            pass

# ---- Convenience -------------------------------------------------------------

async def auto_configure(
    internal_port: int,
    public_port: Optional[int] = None,
    proto: str = "TCP",
    desc: str = "Animica P2P",
    ttl_seconds: int = 30 * 60,
    prefer: str = "auto",
) -> NATManager:
    """
    Create a NATManager, start it, and return it. The caller owns the lifecycle.
    """
    mgr = NATManager(
        internal_port=internal_port,
        public_port=public_port,
        proto=proto,
        desc=desc,
        ttl_seconds=ttl_seconds,
        prefer=prefer,
    )
    await mgr.start()
    return mgr


def capabilities() -> dict:
    """
    Report which backends are available in this environment.
    """
    return {
        "upnp": bool(_miniupnpc is not None),
        "natpmp": bool(NatPMP is not None),
    }


__all__ = [
    "NATManager",
    "NATStatus",
    "auto_configure",
    "capabilities",
]
