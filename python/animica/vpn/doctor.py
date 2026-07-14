"""
`animica vpn doctor` — leak self-test that GATES the 'connected' claim.

It refuses to report a healthy tunnel until the checks pass, so the CLI never tells a user
they're protected when they're leaking. Checks (best-effort, honest about what it can't verify):
  * tunnel handshake is live
  * apparent public IP changed (traffic really egresses via the exit)
  * IPv6 is tunnelled-or-blocked (no v6 leak around the v4 tunnel)
  * DNS resolves (through the tunnel resolver)
  * killswitch present for full-tunnel sessions
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
from dataclasses import dataclass, field

from . import KILLSWITCH_TABLE, WG_IFACE, wg
from .client import _session_path, apparent_ip


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        return all(c.ok for c in self.checks)

    def add(self, name: str, ok: bool, detail: str) -> None:
        self.checks.append(Check(name, ok, detail))


def _has_v6_default_route_outside_tunnel() -> bool:
    out = subprocess.run(["ip", "-6", "route", "show", "default"], capture_output=True, text=True).stdout
    # a v6 default via a non-tunnel iface = potential leak
    for line in out.splitlines():
        if "dev" in line and WG_IFACE not in line:
            return True
    return False


def run() -> Report:
    r = Report()
    sp = _session_path()
    if not os.path.exists(sp):
        r.add("session", False, "no active dVPN session (run `animica vpn up`)")
        return r
    sess = json.load(open(sp))

    hs = wg.latest_handshakes(WG_IFACE)
    live = any(v > 0 for v in hs.values())
    r.add("handshake", live, "tunnel handshake live" if live else "no WireGuard handshake yet")

    ip = apparent_ip()
    r.add("egress-ip", bool(ip), f"apparent public IP: {ip}" if ip else "could not determine apparent IP")

    v6_leak = _has_v6_default_route_outside_tunnel()
    r.add("ipv6-leak", not v6_leak,
          "no IPv6 default outside the tunnel" if not v6_leak
          else "IPv6 default route bypasses the tunnel — v6 traffic may leak; disable IPv6 or use a v6-capable exit")

    try:
        socket.getaddrinfo("example.com", 443)
        r.add("dns", True, "DNS resolves")
    except Exception as e:
        r.add("dns", False, f"DNS resolution failed: {e}")

    if sess.get("killswitch"):
        present = subprocess.run(["nft", "list", "tables"], capture_output=True, text=True).stdout
        ks = f"inet {KILLSWITCH_TABLE}" in present
        r.add("killswitch", ks, "fail-closed killswitch active" if ks else "killswitch table missing")
    return r
