"""
Isolated nftables rules for the Animica dVPN.

Two independent tables, both in their OWN nft table so they can be added and removed
atomically without ever touching docker's or the mainnet node's firewall:

  * exit table ``inet animica_vpn`` — a scoped MASQUERADE for the tunnel subnet plus a
    mandatory default-deny EGRESS ACL. Its chains use ``policy accept`` and every rule is
    scoped to ``iifname <wg>`` / ``ip saddr 10.99.0.0/16``, so it only ever ADDS drops for
    tunnel-sourced abuse — it can never drop docker/host traffic. Teardown is a single
    ``nft delete table inet animica_vpn``.

  * client killswitch ``inet anmvpn_ks`` — fail-closed: if the tunnel is down, all egress
    except to the exit endpoint + the tunnel itself is dropped, so traffic can never leak
    in the clear.

Nothing here calls ``nft flush``, changes a base-chain policy to drop, or edits a
``DOCKER*`` chain — verified by test_nftables_render.py.
"""

from __future__ import annotations

import subprocess
from typing import Optional

from . import NFT_TABLE, KILLSWITCH_TABLE, WG_SUBNET

# Destinations an exit must never let tunnelled traffic reach (SSRF / LAN / cloud metadata).
BLOCKED_DST_V4 = [
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",  # RFC1918
    "127.0.0.0/8",                                      # loopback
    "169.254.0.0/16",                                   # link-local incl. 169.254.169.254 metadata
    "100.64.0.0/10",                                    # CGNAT
]
# Ports commonly used for abuse (spam, lateral movement, amplification).
BLOCKED_TCP_PORTS = [25, 465, 587, 137, 138, 139, 445, 3389]
BLOCKED_UDP_PORTS = [137, 138, 139, 5353]
BLOCKED_BOTH_PORTRANGE = "6881-6889"   # default BitTorrent range


def render_exit_ruleset(uplink: str, wg_iface: str, *, subnet: str = WG_SUBNET,
                        extra_block_v4: Optional[list[str]] = None) -> str:
    """Render the isolated exit table. `uplink` = the host's egress iface (e.g. eth0)."""
    blocked = list(BLOCKED_DST_V4) + list(extra_block_v4 or [])
    dst_set = ", ".join(blocked)
    tcp_ports = ", ".join(str(p) for p in BLOCKED_TCP_PORTS)
    udp_ports = ", ".join(str(p) for p in BLOCKED_UDP_PORTS)
    return f"""\
table inet {NFT_TABLE} {{
  # --- default-deny egress ACL (only ADDS drops for tunnel traffic; policy accept so
  #     it never interferes with docker/host forwarding) ---
  chain forward {{
    type filter hook forward priority filter + 5; policy accept;

    # BCP38 anti-spoofing: a tunnel packet must originate from the tunnel subnet.
    iifname "{wg_iface}" ip saddr != {subnet} drop

    # No IPv6 exit in v1 — drop tunnelled v6 so it can't leak around the v4 ACL.
    iifname "{wg_iface}" meta nfproto ipv6 drop

    # Block private / metadata / CGNAT destinations from the tunnel.
    iifname "{wg_iface}" ip daddr {{ {dst_set} }} drop

    # Block abuse-prone ports from the tunnel.
    iifname "{wg_iface}" tcp dport {{ {tcp_ports} }} drop
    iifname "{wg_iface}" udp dport {{ {udp_ports} }} drop
    iifname "{wg_iface}" tcp dport {BLOCKED_BOTH_PORTRANGE} drop
    iifname "{wg_iface}" udp dport {BLOCKED_BOTH_PORTRANGE} drop
  }}

  # --- scoped source NAT for the tunnel subnet only ---
  chain postrouting {{
    type nat hook postrouting priority srcnat; policy accept;
    ip saddr {subnet} oifname "{uplink}" masquerade
  }}
}}
"""


def render_killswitch_ruleset(wg_iface: str, exit_endpoint_ip: str, exit_port: int) -> str:
    """Client fail-closed killswitch: allow only loopback, the wg iface, and the UDP
    handshake to the exit endpoint; drop everything else so a dropped tunnel can't leak."""
    return f"""\
table inet {KILLSWITCH_TABLE} {{
  chain output {{
    type filter hook output priority filter - 5; policy drop;
    oifname "lo" accept
    oifname "{wg_iface}" accept
    ip daddr {exit_endpoint_ip} udp dport {exit_port} accept
    ct state established,related accept
  }}
}}
"""


def _nft_apply(script: str, *, netns: Optional[str] = None) -> None:
    cmd = (["ip", "netns", "exec", netns] if netns else []) + ["nft", "-f", "-"]
    cp = subprocess.run(cmd, input=script.encode(), capture_output=True)
    if cp.returncode != 0:
        raise RuntimeError(f"nft apply failed: {cp.stderr.decode(errors='replace').strip()}")


def _nft_delete_table(table: str, *, netns: Optional[str] = None) -> None:
    cmd = (["ip", "netns", "exec", netns] if netns else []) + \
          ["nft", "delete", "table", "inet", table]
    subprocess.run(cmd, capture_output=True)  # ignore "No such file" on teardown


def apply_exit(uplink: str, wg_iface: str, *, netns: Optional[str] = None, **kw) -> None:
    _nft_apply(render_exit_ruleset(uplink, wg_iface, **kw), netns=netns)


def remove_exit(*, netns: Optional[str] = None) -> None:
    _nft_delete_table(NFT_TABLE, netns=netns)


def apply_killswitch(wg_iface: str, exit_endpoint_ip: str, exit_port: int, *, netns: Optional[str] = None) -> None:
    _nft_apply(render_killswitch_ruleset(wg_iface, exit_endpoint_ip, exit_port), netns=netns)


def remove_killswitch(*, netns: Optional[str] = None) -> None:
    _nft_delete_table(KILLSWITCH_TABLE, netns=netns)
