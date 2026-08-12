"""
Platform-dispatched firewall facade for the dVPN.

Linux uses the isolated `nft` tables (nftables.py); macOS uses pf anchors (pf.py). Both
implement the SAME security policy — a scoped-NAT + default-deny-abuse exit ACL, and a
fail-closed client killswitch. Callers (exit_daemon, client, cli) go through here so the
platform branch lives in exactly one place.

`netns` is a Linux-only test hook (ignored on macOS, which never uses network namespaces).
`confdir` is where the macOS pf snapshot is persisted (ignored on Linux).
"""

from __future__ import annotations

from typing import Optional

from . import nftables, platform_net
from .platform_net import IS_DARWIN


def apply_exit(uplink: str, wg_iface: str, *, netns: Optional[str] = None,
               confdir: str = "/var/lib/animica-vpn", **kw) -> None:
    if IS_DARWIN:
        from . import pf
        # pf matches the REAL kernel device (utunN), not the logical name — resolve it or refuse
        # (a rule on the logical name never matches → fail-open egress).
        dev = platform_net.wg_device(strict=True)
        pf.apply_exit(uplink, dev, confdir=confdir, **kw)
    else:
        nftables.apply_exit(uplink, wg_iface, netns=netns, **kw)


def remove_exit(*, netns: Optional[str] = None, confdir: str = "/var/lib/animica-vpn") -> None:
    if IS_DARWIN:
        from . import pf
        pf.remove_exit(confdir=confdir)
    else:
        nftables.remove_exit(netns=netns)


def apply_killswitch(wg_iface: str, exit_endpoint_ip: str, exit_port: int, *,
                     netns: Optional[str] = None, confdir: str = "/var/lib/animica-vpn") -> None:
    if IS_DARWIN:
        from . import pf
        dev = platform_net.wg_device(strict=True)
        pf.apply_killswitch(dev, exit_endpoint_ip, exit_port, confdir=confdir)
    else:
        nftables.apply_killswitch(wg_iface, exit_endpoint_ip, exit_port, netns=netns)


def remove_killswitch(*, netns: Optional[str] = None, confdir: str = "/var/lib/animica-vpn") -> None:
    if IS_DARWIN:
        from . import pf
        pf.remove_killswitch(confdir=confdir)
    else:
        nftables.remove_killswitch(netns=netns)


def killswitch_loaded(table_name: str, *, confdir: str = "/var/lib/animica-vpn") -> bool:
    """Whether the client killswitch is currently in force (doctor's leak check)."""
    if IS_DARWIN:
        from . import pf
        return pf.anchor_loaded(table_name)
    import subprocess
    present = subprocess.run(["nft", "list", "tables"], capture_output=True, text=True).stdout
    return f"inet {table_name}" in present
