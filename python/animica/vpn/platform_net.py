"""
Cross-platform network primitives for the dVPN (Linux + macOS/Darwin).

The exit daemon and the client both shell out to OS networking tools. Those tools differ
by platform, so every call that is NOT `wg`/`wg-quick` (which are native on both) is routed
through here:

  * uplink detection      Linux: `ip route show default`   Darwin: `route -n get default`
  * IPv4 forwarding        Linux: /proc + sysctl net.ipv4.ip_forward
                           Darwin: sysctl net.inet.ip.forwarding
  * exit wg interface      Linux: raw `ip link add type wireguard` (kernel/boringtun)
                           Darwin: `wg-quick up` of a minimal [Interface] conf (utun +
                                   userspace wireguard-go); peers are added dynamically
                                   afterwards with `wg set`, which resolves the name via
                                   /var/run/wireguard/<iface>.name.
  * per-peer host route     Linux: `ip route add <ip>/32 dev <iface>`
                           Darwin: `route add -host <ip> -interface <utun>`

Design rules:
  * the Linux branches issue byte-identical commands to the pre-9.0.7 code (no regression).
  * Darwin never uses network namespaces; `netns` is a Linux-only test hook and is ignored
    on Darwin.
  * anything uncertain is surfaced as an error, never a silent pass — an exit that cannot
    bring its interface up must refuse, not run half-configured.
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Optional

from . import WG_IFACE, WG_SERVER_IP, wg

IS_DARWIN = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

_WG_RUN_DIR = "/var/run/wireguard"


def os_name() -> str:
    if IS_DARWIN:
        return "darwin"
    if IS_LINUX:
        return "linux"
    return sys.platform


# --------------------------------------------------------------------------- uplink

def detect_uplink() -> str:
    """The host's default-route egress interface (e.g. eth0 / en0)."""
    if IS_DARWIN:
        out = subprocess.run(["route", "-n", "get", "default"],
                             capture_output=True, text=True).stdout
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("interface:"):
                iface = line.split(":", 1)[1].strip()
                if iface:
                    return iface
        return "en0"
    out = subprocess.run(["ip", "route", "show", "default"],
                         capture_output=True, text=True).stdout
    toks = out.split()
    if "dev" in toks:
        return toks[toks.index("dev") + 1]
    return "eth0"


# ------------------------------------------------------------------- ip forwarding

def read_ip_forward(netns: Optional[str] = None) -> str:
    if IS_DARWIN:
        out = subprocess.run(["sysctl", "-n", "net.inet.ip.forwarding"],
                             capture_output=True, text=True).stdout.strip()
        return "1" if out == "1" else "0"
    if netns:
        return subprocess.run(["ip", "netns", "exec", netns, "sysctl", "-n", "net.ipv4.ip_forward"],
                              capture_output=True, text=True).stdout.strip()
    try:
        with open("/proc/sys/net/ipv4/ip_forward") as f:
            return f.read().strip()
    except OSError:
        return "0"


def write_ip_forward(val: str, netns: Optional[str] = None) -> None:
    if IS_DARWIN:
        subprocess.run(["sysctl", "-w", f"net.inet.ip.forwarding={val}"], capture_output=True)
        return
    cmd = (["ip", "netns", "exec", netns] if netns else []) + \
          ["sysctl", "-qw", f"net.ipv4.ip_forward={val}"]
    subprocess.run(cmd, capture_output=True)


# --------------------------------------------------------- exit wg interface lifecycle

def _darwin_conf_path(confdir: str) -> str:
    # wg-quick derives the interface name from the conf basename, so it MUST be <WG_IFACE>.conf.
    return os.path.join(confdir, f"{WG_IFACE}.conf")


def _darwin_real_iface() -> str:
    """The concrete utunN device wg-quick bound to WG_IFACE (for `route`/`ifconfig`)."""
    try:
        with open(os.path.join(_WG_RUN_DIR, f"{WG_IFACE}.name")) as f:
            name = f.read().strip()
            return name or WG_IFACE
    except OSError:
        return WG_IFACE


def wg_device(*, strict: bool = False) -> str:
    """The kernel device the firewall must scope rules to. On Linux the wg interface IS its
    own device (WG_IFACE). On macOS wg-quick creates a utunN and maps it via
    /var/run/wireguard/<iface>.name — pf matches the utunN, NOT the logical name, so a rule on
    the logical name would never match (fail-open). `strict` raises if the mapping is missing
    (used by the firewall so it refuses rather than installing an inert, fail-open ACL)."""
    if not IS_DARWIN:
        return WG_IFACE
    path = os.path.join(_WG_RUN_DIR, f"{WG_IFACE}.name")
    try:
        with open(path) as f:
            name = f.read().strip()
    except OSError:
        name = ""
    if not name:
        if strict:
            raise wg.WgError(
                f"cannot resolve the real WireGuard device from {path} — refusing to install a "
                f"firewall on the logical name '{WG_IFACE}' (pf would not match it → fail-open). "
                f"Ensure `wg-quick up` created the interface first.")
        return WG_IFACE
    return name


def _wgquick_env(backend: Optional["wg.Backend"]) -> dict:
    env = dict(os.environ)
    env.update(wg.userspace_env(backend) if backend else {})
    return env


def exit_iface_up(priv: str, listen_port: int, *, confdir: str, keydir: str,
                  backend: Optional["wg.Backend"] = None, netns: Optional[str] = None) -> None:
    """Bring up the exit WireGuard interface (address WG_SERVER_IP/16, given listen port).

    Linux: raw `ip link` + `wg set` + `ip addr` + `ip link up` (unchanged from pre-9.0.7).
    Darwin: `wg-quick up` a minimal [Interface] conf — creates the utun, assigns the address
            and listen port; peers are attached later with `wg set`.
    """
    if IS_DARWIN:
        _darwin_exit_iface_up(priv, listen_port, confdir=confdir, backend=backend)
        return
    wg.iface_del(WG_IFACE, netns=netns)  # clean any stale
    wg.iface_add(WG_IFACE, netns=netns)
    wg.iface_set_key(WG_IFACE, priv, listen_port=listen_port, netns=netns, keydir=keydir)
    wg.addr_add(WG_IFACE, f"{WG_SERVER_IP}/16", netns=netns)
    wg.iface_up(WG_IFACE, netns=netns)


def _darwin_exit_iface_up(priv: str, listen_port: int, *, confdir: str,
                          backend: Optional["wg.Backend"]) -> None:
    os.makedirs(confdir, mode=0o700, exist_ok=True)
    conf = (f"[Interface]\n"
            f"PrivateKey = {priv}\n"
            f"ListenPort = {int(listen_port)}\n"
            f"Address = {WG_SERVER_IP}/16\n")
    path = _darwin_conf_path(confdir)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, conf.encode())
    finally:
        os.close(fd)
    env = _wgquick_env(backend)
    # tear down any stale instance first (ignore errors), then bring up.
    subprocess.run(["wg-quick", "down", path], capture_output=True, env=env)
    cp = subprocess.run(["wg-quick", "up", path], capture_output=True, text=True, env=env)
    if cp.returncode != 0:
        raise wg.WgError(f"`wg-quick up {path}` failed ({cp.returncode}): {cp.stderr.strip()}. "
                         f"macOS needs wireguard-tools + wireguard-go (`brew install wireguard-tools`).")


def exit_iface_down(*, confdir: str, netns: Optional[str] = None) -> None:
    if IS_DARWIN:
        path = _darwin_conf_path(confdir)
        target = path if os.path.exists(path) else WG_IFACE
        subprocess.run(["wg-quick", "down", target], capture_output=True)
        return
    wg.iface_del(WG_IFACE, netns=netns)


# ----------------------------------------------------------------- per-peer routing

def route_add_host(dest_cidr: str, *, netns: Optional[str] = None) -> None:
    """Add a route for a peer's /32 so return traffic reaches it (allowed-ips is only an ACL).
    Best-effort on both platforms (the interface's own subnet route often already covers it)."""
    if IS_DARWIN:
        ip = dest_cidr.split("/")[0]
        real = _darwin_real_iface()
        subprocess.run(["route", "-n", "add", "-host", ip, "-interface", real], capture_output=True)
        return
    wg.route_add(dest_cidr, WG_IFACE, netns=netns)
