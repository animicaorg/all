"""
Render wg-quick .conf files for the native client (and a static exit, for docs/tests).

The client conf routes all traffic (full-tunnel) or a set of CIDRs (split-tunnel) into
the tunnel and forces DNS through it so queries can't leak to the LAN/ISP resolver.
The killswitch is applied separately (nftables.py) so it can be reasoned about and tested
independently of wg-quick's PostUp hooks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from . import WG_LISTEN_PORT, WG_SERVER_IP


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


def render_client_conf(c: ClientConf) -> str:
    lines = ["[Interface]",
             f"PrivateKey = {c.private_key}",
             f"Address = {c.address}"]
    if c.dns:
        lines.append(f"DNS = {c.dns}")
    lines += ["",
              "[Peer]",
              f"PublicKey = {c.exit_pubkey}",
              f"Endpoint = {c.exit_endpoint}",
              f"AllowedIPs = {c.allowed_ips}",
              f"PersistentKeepalive = {c.keepalive}",
              ""]
    return "\n".join(lines)


def render_exit_conf(e: ExitConf) -> str:
    lines = ["[Interface]",
             f"PrivateKey = {e.private_key}",
             f"Address = {e.address}",
             f"ListenPort = {e.listen_port}",
             ""]
    for pub, aips in e.peers:
        lines += ["[Peer]",
                  f"PublicKey = {pub}",
                  f"AllowedIPs = {aips}",
                  ""]
    return "\n".join(lines)
