"""
Render wg-quick .conf files for the native client (and a static exit, for docs/tests).

The client conf routes all traffic (full-tunnel) or a set of CIDRs (split-tunnel) into
the tunnel and forces DNS through it so queries can't leak to the LAN/ISP resolver.
The killswitch is applied separately (nftables.py) so it can be reasoned about and tested
independently of wg-quick's PostUp hooks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from . import WG_LISTEN_PORT, WG_SERVER_IP


class ConfigInjectionError(ValueError):
    """A field destined for a wg-quick .conf contained an illegal character.

    wg-quick executes PostUp/PostDown lines as root, so a registry- or peer-controlled
    field that smuggles a newline could inject an arbitrary `PostUp = <cmd>` directive.
    Every value interpolated into a conf is therefore validated first and any control
    character (CR/LF/NUL/…) is a hard error, never silently stripped.
    """


# Conservative allow-lists per field. wg-quick values are single-line; none of these
# fields ever legitimately contains whitespace-run, '=', or control characters.
_WG_KEY_RE = re.compile(r"^[A-Za-z0-9+/]{42,45}=?$")          # base64 Curve25519 key
_ENDPOINT_RE = re.compile(r"^[A-Za-z0-9.\-\[\]:]{1,255}:[0-9]{1,5}$")  # host:port / [v6]:port
_ADDRESS_RE = re.compile(r"^[0-9a-fA-F:.]+/[0-9]{1,3}$")      # ip/prefix
_DNS_RE = re.compile(r"^[0-9a-fA-F:.]{1,45}(,\s*[0-9a-fA-F:.]{1,45})*$")
_ALLOWED_RE = re.compile(r"^[0-9a-fA-F:.]+/[0-9]{1,3}(\s*,\s*[0-9a-fA-F:.]+/[0-9]{1,3})*$")


def _no_control(name: str, value: str) -> str:
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in value):
        raise ConfigInjectionError(f"{name} contains a control character (conf injection blocked)")
    return value


def _check(name: str, value: str, pattern: re.Pattern) -> str:
    _no_control(name, value)
    if not pattern.match(value):
        raise ConfigInjectionError(f"{name} is malformed: {value!r}")
    return value


@dataclass
class ClientConf:
    private_key: str
    address: str                       # assigned /32, e.g. 10.99.0.7/32
    exit_pubkey: str
    exit_endpoint: str                 # host:port
    allowed_ips: str = "0.0.0.0/0"     # full-tunnel; or "0.0.0.0/0, ::/0"; or split CIDRs
    dns: Optional[str] = "1.1.1.1"     # forced through the tunnel to prevent DNS leaks
    keepalive: int = 25


@dataclass
class ExitConf:
    private_key: str
    address: str = f"{WG_SERVER_IP}/16"
    listen_port: int = WG_LISTEN_PORT
    peers: list[tuple[str, str]] = field(default_factory=list)   # (pubkey, allowed_ips /32)


def _augment_allowed_for_dns(allowed_ips: str, dns: Optional[str]) -> str:
    """In a SPLIT tunnel, a forced DNS server that isn't itself routed into the tunnel means
    every DNS query egresses in the clear to the LAN/ISP resolver — a DNS leak. Route each
    resolver as a host route through the tunnel so queries follow the tunnel too. Full-tunnel
    (0.0.0.0/0 or ::/0) already covers the resolver, so leave it untouched."""
    cidrs = [c.strip() for c in allowed_ips.split(",") if c.strip()]
    if not dns or "0.0.0.0/0" in cidrs or "::/0" in cidrs:
        return allowed_ips
    additions = []
    for server in (s.strip() for s in dns.split(",") if s.strip()):
        host_route = f"{server}/128" if ":" in server else f"{server}/32"
        if host_route not in cidrs and server not in cidrs:
            additions.append(host_route)
    return ", ".join(cidrs + additions) if additions else allowed_ips


def render_client_conf(c: ClientConf) -> str:
    # Validate every field BEFORE it reaches the file. exit_pubkey / exit_endpoint come
    # from the registry (an untrusted exit could set them), so a newline here would be
    # a root RCE via an injected PostUp line — reject rather than sanitize.
    _check("PrivateKey", c.private_key, _WG_KEY_RE)
    _check("Address", c.address, _ADDRESS_RE)
    _check("PublicKey", c.exit_pubkey, _WG_KEY_RE)
    _check("Endpoint", c.exit_endpoint, _ENDPOINT_RE)
    if c.dns:
        _check("DNS", c.dns, _DNS_RE)
    allowed_ips = _augment_allowed_for_dns(c.allowed_ips, c.dns)
    _check("AllowedIPs", allowed_ips, _ALLOWED_RE)
    if not isinstance(c.keepalive, int):
        raise ConfigInjectionError("PersistentKeepalive must be an int")
    lines = ["[Interface]",
             f"PrivateKey = {c.private_key}",
             f"Address = {c.address}"]
    if c.dns:
        lines.append(f"DNS = {c.dns}")
    lines += ["",
              "[Peer]",
              f"PublicKey = {c.exit_pubkey}",
              f"Endpoint = {c.exit_endpoint}",
              f"AllowedIPs = {allowed_ips}",
              f"PersistentKeepalive = {int(c.keepalive)}",
              ""]
    return "\n".join(lines)


def render_exit_conf(e: ExitConf) -> str:
    _check("PrivateKey", e.private_key, _WG_KEY_RE)
    _check("Address", e.address, _ADDRESS_RE)
    if not isinstance(e.listen_port, int):
        raise ConfigInjectionError("ListenPort must be an int")
    lines = ["[Interface]",
             f"PrivateKey = {e.private_key}",
             f"Address = {e.address}",
             f"ListenPort = {int(e.listen_port)}",
             ""]
    for pub, aips in e.peers:
        _check("PublicKey", pub, _WG_KEY_RE)
        _check("AllowedIPs", aips, _ALLOWED_RE)
        lines += ["[Peer]",
                  f"PublicKey = {pub}",
                  f"AllowedIPs = {aips}",
                  ""]
    return "\n".join(lines)
