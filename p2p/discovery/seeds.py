"""
Animica P2P — Discovery: bootstrap seeds from DNS TXT and HTTPS JSON
====================================================================

Goals
-----
- Provide *portable* seed discovery with **no hard dependency** on extra packages.
- Prefer asyncio-friendly APIs but offer sync fallbacks.
- Accept flexible seed address formats:
    • multiaddr-like:   "tcp://seed.animica.dev:6750"
    • explicit scheme:  "quic://seed.animica.dev:6751"
    • ws/wss:           "wss://seed.animica.dev/p2p"
    • animica peer URL: "animica://<peer-id>@host:port?proto=quic"
- Optionally resolve hostnames to IPs (A/AAAA) for faster first dials.
- Be resilient: best-effort parsing; ignore unknown records gracefully.

TXT record formats supported
----------------------------
1) CSV-style key/val tokens (order free), e.g.
   animica=seed:v1, addr=tcp://seed1.animica.dev:6750, addr=quic://seed1.animica.dev:6751

2) JSON object embedded in a single TXT string:
   {"animica":"seed:v1","peers":["tcp://seed1:6750","quic://seed1:6751"]}

HTTPS JSON schema
-----------------
GET <url> → JSON:
{
  "version": "seed:v1",
  "peers": [
    "tcp://seed1.animica.dev:6750",
    "quic://seed2.animica.dev:6751",
    "wss://seed3.animica.dev/p2p"
  ]
}

This module does *not* persist peers; it just returns a set of candidate
multiaddrs/endpoint dicts to feed into the PeerStore/ConnectionManager.

"""

from __future__ import annotations

import asyncio
import json
import os
import re
import socket
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence, Tuple, Dict, Any, Set

# Optional DNS dependency (dnspython). If missing, TXT discovery is skipped gracefully.
try:
    import dns.resolver  # type: ignore
    _HAS_DNSPYTHON = True
except Exception:
    _HAS_DNSPYTHON = False

# Optional: import multiaddr helpers if available.
try:
    from p2p.transport.multiaddr import parse_multiaddr, MultiAddr
except Exception:
    # Minimal shim to keep this module usable without multiaddr
    @dataclass(frozen=True)
    class MultiAddr:
        scheme: str
        host: str
        port: Optional[int] = None
        path: Optional[str] = None
        params: Dict[str, str] = field(default_factory=dict)

    def parse_multiaddr(addr: str) -> MultiAddr:
        # Very small tolerant parser for scheme://host:port[/path][?k=v...]
        m = re.match(r"(?i)^(?P<scheme>[a-z0-9+.-]+)://(?P<rest>.+)$", addr.strip())
        if not m:
            raise ValueError(f"not a URL-like address: {addr!r}")
        scheme = m.group("scheme").lower()
        rest = m.group("rest")
        path = None
        params: Dict[str, str] = {}
        if "?" in rest:
            rest, q = rest.split("?", 1)
            for kv in q.split("&"):
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    params[k] = v
                elif kv:
                    params[kv] = ""
        if "/" in rest:
            hostport, path = rest.split("/", 1)
            path = "/" + path
        else:
            hostport = rest
        host = hostport
        port: Optional[int] = None
        if hostport.count(":") == 1 and not hostport.startswith("["):
            h, p = hostport.split(":", 1)
            host, port = h, int(p)
        elif hostport.startswith("["):
            # IPv6 [addr]:port
            h, p = hostport.rsplit("]:", 1)
            host, port = h + "]", int(p)
        return MultiAddr(scheme=scheme, host=host, port=port, path=path, params=params)


# ----------------------------
# Data structures & utilities
# ----------------------------

@dataclass(frozen=True)
class SeedEndpoint:
    """Normalized endpoint produced by discovery."""
    scheme: str      # tcp|quic|ws|wss|animica
    host: str
    port: Optional[int] = None
    path: Optional[str] = None
    params: Dict[str, str] = field(default_factory=dict)

    def to_multiaddr(self) -> MultiAddr:
        return MultiAddr(self.scheme, self.host, self.port, self.path, dict(self.params))


@dataclass
class SeedBundle:
    """A set of deduplicated endpoints + resolution metadata."""
    endpoints: List[SeedEndpoint] = field(default_factory=list)
    resolved_ips: Dict[str, List[str]] = field(default_factory=dict)  # host → [ip...]
    source: str = ""             # e.g., "dns:seeds.animica.dev" or "https:https://…/seeds.json"
    fetched_at: float = field(default_factory=time.time)


_VALID_SCHEMES = {"tcp", "quic", "ws", "wss", "animica"}

_ADDR_RE = re.compile(
    r"(?i)\b("
    r"(?:tcp|quic|ws|wss|animica)://"
    r"[A-Za-z0-9\.\-\[\]:]+(?::\d+)?(?:/[^\s,;\"']*)?(?:\?[^\s,;\"']*)?"
    r")\b"
)

def _normalize_addr(addr: str) -> Optional[SeedEndpoint]:
    try:
        m = parse_multiaddr(addr)
    except Exception:
        return None
    scheme = m.scheme.lower()
    if scheme not in _VALID_SCHEMES:
        return None
    return SeedEndpoint(scheme=scheme, host=m.host, port=m.port, path=getattr(m, "path", None), params=getattr(m, "params", {}))


def _dedupe(endpoints: Iterable[SeedEndpoint]) -> List[SeedEndpoint]:
    seen: Set[Tuple[str, str, Optional[int], Optional[str], Tuple[Tuple[str, str], ...]]] = set()
    out: List[SeedEndpoint] = []
    for ep in endpoints:
        key = (ep.scheme, ep.host, ep.port, ep.path, tuple(sorted(ep.params.items())))
        if key in seen:
            continue
        seen.add(key)
        out.append(ep)
    return out


# ----------------------------
# TXT record discovery (DNS)
# ----------------------------

async def discover_from_dns_txt(name: str, timeout: float = 3.0) -> SeedBundle:
    """
    Resolve seeds from DNS TXT records at `name`.
    Accepts CSV-like k=v tokens or JSON payloads; extracts any addr=... or URL tokens.
    Requires dnspython if present; otherwise returns an empty bundle.
    """
    endpoints: List[SeedEndpoint] = []
    if not _HAS_DNSPYTHON:
        return SeedBundle(endpoints=[], source=f"dns:{name} (dnspython unavailable)")

    def _resolve() -> List[str]:
        resolver = dns.resolver.Resolver()  # type: ignore
        resolver.lifetime = timeout
        try:
            answers = resolver.resolve(name, "TXT")  # type: ignore
        except Exception:
            return []
        out: List[str] = []
        for rr in answers:
            try:
                # dnspython returns a sequence of strings per record; join them
                s = "".join([t.decode("utf-8") if isinstance(t, (bytes, bytearray)) else str(t) for t in rr.strings])  # type: ignore
                out.append(s)
            except Exception:
                pass
        return out

    txt_records = await asyncio.to_thread(_resolve)
    for rec in txt_records:
        rec = rec.strip().strip('"').strip("'")
        # Try JSON first
        if rec.startswith("{") and rec.endswith("}"):
            try:
                obj = json.loads(rec)
                peers = obj.get("peers") or obj.get("addr") or []
                if isinstance(peers, str):
                    peers = [peers]
                for a in peers:
                    ep = _normalize_addr(str(a))
                    if ep:
                        endpoints.append(ep)
                continue
            except Exception:
                # fall-through to regex extraction
                pass
        # CSV tokens and raw URLs
        # Extract URL-like tokens
        for match in _ADDR_RE.findall(rec):
            ep = _normalize_addr(match)
            if ep:
                endpoints.append(ep)
        # Extract addr=... tokens
        for token in rec.split(","):
            token = token.strip()
            if token.lower().startswith("addr="):
                ep = _normalize_addr(token.split("=", 1)[1].strip())
                if ep:
                    endpoints.append(ep)

    endpoints = _dedupe(endpoints)
    resolved = await resolve_hosts({ep.host for ep in endpoints})
    return SeedBundle(endpoints=endpoints, resolved_ips=resolved, source=f"dns:{name}")


# ----------------------------
# HTTPS JSON discovery
# ----------------------------

async def discover_from_https_json(url: str, timeout: float = 3.5) -> SeedBundle:
    """
    Fetch <url> and parse JSON with a "peers" list (see schema in module docstring).
    Uses stdlib urllib; runs in a thread to avoid blocking the loop.
    """
    def _fetch() -> Dict[str, Any]:
        req = urllib.request.Request(url, headers={"User-Agent": "animica-p2p/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
        return json.loads(data.decode("utf-8"))

    endpoints: List[SeedEndpoint] = []
    try:
        obj = await asyncio.to_thread(_fetch)
    except Exception:
        return SeedBundle(endpoints=[], source=f"https:{url} (fetch-failed)")

    peers = obj.get("peers") or []
    for a in peers:
        ep = _normalize_addr(str(a))
        if ep:
            endpoints.append(ep)

    endpoints = _dedupe(endpoints)
    resolved = await resolve_hosts({ep.host for ep in endpoints})
    return SeedBundle(endpoints=endpoints, resolved_ips=resolved, source=f"https:{url}")


# ----------------------------
# Static config discovery
# ----------------------------

def discover_from_static(addrs: Sequence[str]) -> SeedBundle:
    eps = [e for a in addrs if (e := _normalize_addr(a))]
    return SeedBundle(endpoints=_dedupe(eps), resolved_ips={}, source="static:list")


# ----------------------------
# Hostname resolution helper
# ----------------------------

async def resolve_hosts(hosts: Iterable[str], timeout: float = 2.0) -> Dict[str, List[str]]:
    """
    Resolve a small set of hostnames to A/AAAA using stdlib socket in a thread pool.
    Returns host→[ip...] mapping. IPv6 results are included.
    """
    async def _resolve_one(host: str) -> Tuple[str, List[str]]:
        def _do() -> List[str]:
            try:
                infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
                ips = []
                for family, _, _, _, sockaddr in infos:
                    ip = sockaddr[0]
                    # Normalize IPv6 to compressed form
                    if family == socket.AF_INET6:
                        ip = socket.inet_ntop(socket.AF_INET6, socket.inet_pton(socket.AF_INET6, ip))
                    ips.append(ip)
                # preserve order but dedupe
                seen = set()
                ordered = [x for x in ips if not (x in seen or seen.add(x))]
                return ordered
            except Exception:
                return []
        return host, await asyncio.to_thread(_do)

    tasks = [_resolve_one(h) for h in set(hosts)]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    out: Dict[str, List[str]] = {}
    for r in results:
        if isinstance(r, Exception):
            continue
        host, ips = r
        out[host] = ips
    return out


# ----------------------------
# Composite discovery
# ----------------------------

async def discover_all(
    dns_names: Sequence[str] = (),
    https_urls: Sequence[str] = (),
    static_addrs: Sequence[str] = (),
    resolve: bool = True,
) -> SeedBundle:
    """
    Run discovery across DNS, HTTPS, and static lists; merge & dedupe.
    """
    bundles: List[SeedBundle] = []

    # DNS
    for name in dns_names:
        try:
            bundles.append(await discover_from_dns_txt(name))
        except Exception:
            bundles.append(SeedBundle(endpoints=[], source=f"dns:{name} (error)"))

    # HTTPS
    for url in https_urls:
        try:
            bundles.append(await discover_from_https_json(url))
        except Exception:
            bundles.append(SeedBundle(endpoints=[], source=f"https:{url} (error)"))

    # Static
    if static_addrs:
        bundles.append(discover_from_static(static_addrs))

    # Merge
    endpoints: List[SeedEndpoint] = []
    resolved_ips: Dict[str, List[str]] = {}
    for b in bundles:
        endpoints.extend(b.endpoints)
        resolved_ips.update(b.resolved_ips)

    endpoints = _dedupe(endpoints)

    # Optionally (re)resolve any new hosts
    if resolve:
        need = {ep.host for ep in endpoints if ep.host not in resolved_ips}
        if need:
            extra = await resolve_hosts(need)
            resolved_ips.update(extra)

    return SeedBundle(endpoints=endpoints, resolved_ips=resolved_ips, source="composite")


# ----------------------------
# Sync helpers (for CLI/tests)
# ----------------------------

def discover_all_sync(
    dns_names: Sequence[str] = (),
    https_urls: Sequence[str] = (),
    static_addrs: Sequence[str] = (),
    resolve: bool = True,
) -> SeedBundle:
    return asyncio.run(discover_all(dns_names, https_urls, static_addrs, resolve))


# ----------------------------
# Tiny CLI
# ----------------------------

def _env_list(key: str) -> List[str]:
    v = os.getenv(key, "").strip()
    if not v:
        return []
    return [x.strip() for x in v.split(",") if x.strip()]

def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="Animica P2P seed discovery")
    ap.add_argument("--dns", action="append", default=[], help="DNS TXT name (repeatable)")
    ap.add_argument("--https", action="append", default=[], help="HTTPS JSON URL (repeatable)")
    ap.add_argument("--addr", action="append", default=[], help="Static seed address (repeatable)")
    ap.add_argument("--no-resolve", action="store_true", help="Do not resolve hostnames to IPs")
    args = ap.parse_args()

    dns_names = list(args.dns) or _env_list("ANIMICA_P2P_SEEDS_DNS")
    https_urls = list(args.https) or _env_list("ANIMICA_P2P_SEEDS_HTTPS")
    addrs = list(args.addr) or _env_list("ANIMICA_P2P_SEEDS_ADDRS")

    bundle = discover_all_sync(dns_names, https_urls, addrs, resolve=not args.no_resolve)

    print(f"# Source: {bundle.source}")
    print(f"# Endpoints: {len(bundle.endpoints)}")
    for ep in bundle.endpoints:
        port = f":{ep.port}" if ep.port is not None else ""
        path = ep.path or ""
        params = ""
        if ep.params:
            params = "?" + "&".join(f"{k}={v}" for k, v in ep.params.items())
        print(f"{ep.scheme}://{ep.host}{port}{path}{params}")
    if bundle.resolved_ips:
        print("# Resolved IPs:")
        for host, ips in bundle.resolved_ips.items():
            print(f"{host} -> {', '.join(ips)}")

if __name__ == "__main__":
    main()
