"""
WireGuard backend wrapper — kernel-first, userspace (boringtun/wireguard-go) fallback.

Every primitive here shells out to the standard ``wg`` / ``wg-quick`` / ``ip`` tools.
Design rules that this module enforces (learned the hard way in the netns smoke test):

  * keys are written to 0600 FILES, never passed on argv or via ``<(process sub)``
    (under dash the process-substitution form silently fails to load the key).
  * on the exit side, an allowed-ip is a crypto ACL, NOT a route: a peer's /32 must
    also be added with ``ip route add <peer>/32 dev <iface>`` or packets never return.
  * nothing here binds a port in, or edits the firewall of, the default namespace;
    NAT/egress rules live in nftables.py under a dedicated table.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Optional


class WgError(RuntimeError):
    pass


@dataclass
class Backend:
    kind: str            # "kernel" | "userspace"
    impl: Optional[str]  # None for kernel, else "boringtun" / "wireguard-go"
    note: str


def _run(cmd: list[str], *, check: bool = True, netns: Optional[str] = None,
         input_bytes: Optional[bytes] = None, env: Optional[dict] = None) -> subprocess.CompletedProcess:
    if netns:
        cmd = ["ip", "netns", "exec", netns] + cmd
    try:
        cp = subprocess.run(cmd, capture_output=True, input=input_bytes,
                            env={**os.environ, **(env or {})})
    except FileNotFoundError as e:
        raise WgError(f"missing tool: {e.filename}") from e
    if check and cp.returncode != 0:
        raise WgError(f"`{' '.join(cmd)}` failed ({cp.returncode}): "
                      f"{cp.stderr.decode(errors='replace').strip()}")
    return cp


def tool_available(name: str) -> bool:
    return shutil.which(name) is not None


def detect_backend() -> Backend:
    """Pick the WireGuard data-plane backend, kernel first."""
    if not tool_available("wg"):
        raise WgError("wireguard-tools not installed (need `wg` / `wg-quick`). "
                      "Install wireguard-tools for your OS.")
    # Kernel module: try to create a throwaway wg interface.
    if tool_available("ip"):
        probe = "anmwgprobe0"
        try:
            _run(["ip", "link", "add", probe, "type", "wireguard"], check=True)
            _run(["ip", "link", "del", probe], check=False)
            return Backend("kernel", None, "kernel WireGuard module available")
        except WgError:
            pass
    # Userspace fallback.
    for impl in ("boringtun", "wireguard-go"):
        if tool_available(impl):
            return Backend("userspace", impl,
                           f"kernel module unavailable — using {impl} userspace (EXPERIMENTAL, slower)")
    raise WgError("no WireGuard data plane: kernel module unavailable and neither "
                  "boringtun nor wireguard-go is installed. Install one to use the tunnel.")


def userspace_env(backend: Backend) -> dict:
    """Env for wg-quick to select a userspace implementation, if applicable."""
    if backend.kind == "userspace" and backend.impl:
        return {"WG_QUICK_USERSPACE_IMPLEMENTATION": backend.impl, "WG_SUDO": "1"}
    return {}


# --------------------------------------------------------------------------- keys

def genkey() -> str:
    """Return a fresh WireGuard private key (base64)."""
    return _run(["wg", "genkey"]).stdout.decode().strip()


def pubkey(private_key: str) -> str:
    """Derive the public key from a private key (base64)."""
    return _run(["wg", "pubkey"], input_bytes=(private_key + "\n").encode()).stdout.decode().strip()


def genkeys() -> tuple[str, str]:
    priv = genkey()
    return priv, pubkey(priv)


def write_key_file(private_key: str, directory: str) -> str:
    """Write a private key to a 0600 file (NEVER pass keys on argv)."""
    os.makedirs(directory, mode=0o700, exist_ok=True)
    fd, path = tempfile.mkstemp(prefix="wgkey-", dir=directory)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, (private_key + "\n").encode())
    finally:
        os.close(fd)
    return path


# ------------------------------------------------------------------- raw iface ops
# These operate on a bare `ip link ... type wireguard` interface (used by the exit
# daemon and the netns smoke test). The client uses wg-quick via config_gen instead.

def iface_add(iface: str, *, netns: Optional[str] = None) -> None:
    _run(["ip", "link", "add", iface, "type", "wireguard"], netns=netns)


def iface_del(iface: str, *, netns: Optional[str] = None) -> None:
    _run(["ip", "link", "del", iface], check=False, netns=netns)


def iface_set_key(iface: str, private_key: str, *, listen_port: Optional[int] = None,
                  netns: Optional[str] = None, keydir: Optional[str] = None) -> None:
    keydir = keydir or tempfile.mkdtemp(prefix="anmwg-")
    kf = write_key_file(private_key, keydir)
    cmd = ["wg", "set", iface, "private-key", kf]
    if listen_port is not None:
        cmd += ["listen-port", str(listen_port)]
    try:
        _run(cmd, netns=netns)
    finally:
        try:
            os.unlink(kf)
        except OSError:
            pass


def peer_add(iface: str, peer_pubkey: str, allowed_ips: str, *,
             endpoint: Optional[str] = None, keepalive: Optional[int] = None,
             netns: Optional[str] = None) -> None:
    cmd = ["wg", "set", iface, "peer", peer_pubkey, "allowed-ips", allowed_ips]
    if endpoint:
        cmd += ["endpoint", endpoint]
    if keepalive:
        cmd += ["persistent-keepalive", str(keepalive)]
    _run(cmd, netns=netns)


def peer_remove(iface: str, peer_pubkey: str, *, netns: Optional[str] = None) -> None:
    _run(["wg", "set", iface, "peer", peer_pubkey, "remove"], check=False, netns=netns)


def addr_add(iface: str, cidr: str, *, netns: Optional[str] = None) -> None:
    _run(["ip", "addr", "add", cidr, "dev", iface], netns=netns)


def iface_up(iface: str, *, netns: Optional[str] = None) -> None:
    _run(["ip", "link", "set", iface, "up"], netns=netns)


def route_add(dest: str, iface: str, *, netns: Optional[str] = None) -> None:
    """Add a route to `dest` via `iface`. On the exit, a peer's /32 needs this —
    allowed-ips is only a crypto ACL, not a kernel route."""
    _run(["ip", "route", "add", dest, "dev", iface], check=False, netns=netns)


# ------------------------------------------------------------------- introspection

_HS_RE = re.compile(r"^(?P<pub>\S+)\s+(?P<epoch>\d+)$")


def latest_handshakes(iface: str, *, netns: Optional[str] = None) -> dict[str, int]:
    """peer pubkey -> unix epoch of last handshake (0 if never)."""
    out = _run(["wg", "show", iface, "latest-handshakes"], check=False, netns=netns).stdout.decode()
    result: dict[str, int] = {}
    for line in out.splitlines():
        m = _HS_RE.match(line.strip())
        if m:
            result[m.group("pub")] = int(m.group("epoch"))
    return result


def transfer(iface: str, *, netns: Optional[str] = None) -> dict[str, tuple[int, int]]:
    """peer pubkey -> (rx_bytes, tx_bytes) cumulative for the interface's lifetime."""
    out = _run(["wg", "show", iface, "transfer"], check=False, netns=netns).stdout.decode()
    result: dict[str, tuple[int, int]] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 3:
            try:
                result[parts[0]] = (int(parts[1]), int(parts[2]))
            except ValueError:
                continue
    return result
