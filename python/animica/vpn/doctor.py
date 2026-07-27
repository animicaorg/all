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
import subprocess
from dataclasses import dataclass, field

from . import KILLSWITCH_TABLE, WG_IFACE, firewall, wg
from .client import _session_path, apparent_ip, state_dir
from .platform_net import IS_DARWIN


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
    if IS_DARWIN:
        # macOS: the tunnel device is the real utunN, not the logical name; compare against it.
        from .platform_net import wg_device
        dev = wg_device()
        out = subprocess.run(["netstat", "-rn", "-f", "inet6"], capture_output=True, text=True).stdout
        for line in out.splitlines():
            if line.split()[:1] == ["default"] and dev not in line:
                return True
        return False
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

    # egress-ip: it is NOT enough that some IP was returned — a full plaintext leak also returns
    # an IP (the user's real one). Prove the apparent IP actually CHANGED from the pre-tunnel IP.
    before = sess.get("beforeIp")
    ip = apparent_ip()
    if not ip:
        r.add("egress-ip", False, "could not determine apparent public IP — cannot confirm egress")
    elif before and ip == before:
        r.add("egress-ip", False,
              f"apparent IP {ip} is unchanged from before the tunnel — traffic is NOT egressing via the exit (leak)")
    elif before and ip != before:
        r.add("egress-ip", True, f"egress IP changed {before} -> {ip} (tunnel confirmed)")
    else:
        # no baseline recorded (older session): we can report the IP but not that it changed
        r.add("egress-ip", False, f"apparent IP {ip}, but no pre-tunnel baseline to prove it changed — unverified")

    v6_leak = _has_v6_default_route_outside_tunnel()
    # A fail-closed killswitch drops ALL inet6 egress, so a v6 default route can't actually leak.
    ks_v6_covered = bool(sess.get("killswitch")) and firewall.killswitch_loaded(
        KILLSWITCH_TABLE, confdir=state_dir())
    if v6_leak and ks_v6_covered:
        r.add("ipv6-leak", True, "IPv6 default route present but the killswitch blocks all v6 egress")
    else:
        r.add("ipv6-leak", not v6_leak,
              "no IPv6 default outside the tunnel" if not v6_leak
              else "IPv6 default route bypasses the tunnel — v6 traffic may leak; disable IPv6 or use a v6-capable exit")

    # dns: resolution working proves nothing about WHERE the query went. Check the active
    # resolver is the tunnel-forced DNS; a LAN/ISP resolver in resolv.conf is a DNS leak.
    want_dns = {s.strip() for s in str(sess.get("dns") or "").split(",") if s.strip()}
    active = _active_resolvers()
    if not want_dns:
        r.add("dns", True, f"resolver(s): {', '.join(active) or 'unknown'} (no forced DNS configured)")
    elif active and active.issubset(want_dns):
        r.add("dns", True, f"resolver is the tunnel DNS ({', '.join(sorted(active))}) — no DNS leak")
    elif active:
        leaked = active - want_dns
        r.add("dns", False,
              f"active resolver(s) {', '.join(sorted(leaked))} are NOT the tunnel DNS {', '.join(sorted(want_dns))} — DNS may leak")
    else:
        r.add("dns", False, "could not read the active resolver — DNS leak status unverified")

    if sess.get("killswitch"):
        ks = firewall.killswitch_loaded(KILLSWITCH_TABLE, confdir=state_dir())
        r.add("killswitch", ks, "fail-closed killswitch active" if ks else "killswitch table missing")
    return r


def _active_resolvers() -> set[str]:
    """The nameservers the OS will actually use, from /etc/resolv.conf."""
    servers: set[str] = set()
    try:
        with open("/etc/resolv.conf") as f:
            for line in f:
                line = line.strip()
                if line.startswith("nameserver"):
                    parts = line.split()
                    if len(parts) >= 2:
                        servers.add(parts[1])
    except OSError:
        pass
    return servers
