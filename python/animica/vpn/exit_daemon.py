"""
dVPN exit daemon — OFF by default, opt-in, ToS-gated, hard-isolated from the mainnet node.

Lifecycle:
  setup()    detect backend, bring up the WireGuard exit interface, enable forwarding,
             install the isolated `inet animica_vpn` nft table (scoped NAT + egress ACL),
             and optionally start the browser-proxy companion.
  register() publish a signed RelayDescriptor (public wg key, endpoint, region, capacity,
             price hint, tos_hash) to the registry — requires a stored ToS acceptance.
  run()      heartbeat + reconcile granted peers (add/remove wg peers + /32 routes) +
             meter per-session bytes + POST signed usage reports + reap idle peers.
  teardown() remove peers, delete the nft table, tear down the interface, stop the proxy,
             and restore ip_forward only if we changed it — leaving the host byte-identical.

Nothing here touches consensus, the node's ports, or docker's firewall.
"""

from __future__ import annotations

import os
import secrets
import subprocess
import time
from dataclasses import dataclass, field
from typing import Optional

from . import (NFT_TABLE, WG_IFACE, WG_LISTEN_PORT, WG_SERVER_IP, WG_SUBNET,
               accounting, guard, http_proxy, nftables, tos, wg)
from .crypto import Wallet
from .registry_client import RegistryClient


def state_dir() -> str:
    d = os.environ.get("ANIMICA_VPN_STATE", "/var/lib/animica-vpn")
    os.makedirs(d, mode=0o700, exist_ok=True)
    return d


def _exit_key_path() -> str:
    return os.path.join(state_dir(), "exit-wg.key")


def _detect_public_ip() -> Optional[str]:
    import urllib.request
    for u in ("https://api.ipify.org", "https://ifconfig.me/ip"):
        try:
            with urllib.request.urlopen(u, timeout=8) as r:
                return r.read().decode().strip()
        except Exception:
            continue
    return None


def _detect_uplink() -> str:
    out = subprocess.run(["ip", "route", "show", "default"], capture_output=True, text=True).stdout
    for tok in out.split():
        if tok == "dev":
            idx = out.split().index("dev")
            return out.split()[idx + 1]
    return "eth0"


@dataclass
class ExitConfig:
    region: str = "unknown"
    country: str = ""
    city: str = ""
    label: str = "animica-exit"
    capacity_mbps: int = 100
    price_hint_nanm_per_gb: int = 0        # 0 = free-but-capped v1
    listen_port: int = WG_LISTEN_PORT
    enable_browser_proxy: bool = False
    browser_proxy_port: int = 8443
    allow_validator: bool = False


@dataclass
class _PeerState:
    session_id: str
    pubkey: str
    allocated_ip: str
    meter: accounting.UsageMeter
    last_seen: float


class ExitDaemon:
    def __init__(self, wallet: Wallet, cfg: ExitConfig, *, reg: Optional[RegistryClient] = None):
        self.wallet = wallet
        self.cfg = cfg
        self.reg = reg or RegistryClient(wallet)
        self.backend = None
        self.exit_id: Optional[str] = None
        self.wg_pub: str = ""
        self.uplink = _detect_uplink()
        self.public_ip: Optional[str] = None
        self.peers: dict[str, _PeerState] = {}     # pubkey -> state
        self._set_ip_forward = False
        self._proxy = None
        self._proxy_token = ""
        self._ns: Optional[str] = None             # test hook: run inside a netns

    # -------------------------------------------------------------- lifecycle
    def preflight(self) -> None:
        guard.enforce(port=self.cfg.listen_port, allow_validator=self.cfg.allow_validator)
        acc = tos.load_acceptance()
        if acc is None or acc.address != self.wallet.address:
            raise RuntimeError("exit ToS not accepted for this wallet — run "
                               "`animica vpn exit register` and accept the terms first.")

    def setup(self, *, netns: Optional[str] = None) -> None:
        self._ns = netns
        self.backend = wg.detect_backend()
        # persistent exit key
        if os.path.exists(_exit_key_path()):
            with open(_exit_key_path()) as f:
                priv = f.read().strip()
        else:
            priv = wg.genkey()
            fd = os.open(_exit_key_path(), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            os.write(fd, (priv + "\n").encode())
            os.close(fd)
        self.wg_pub = wg.pubkey(priv)

        wg.iface_del(WG_IFACE, netns=netns)  # clean any stale
        wg.iface_add(WG_IFACE, netns=netns)
        wg.iface_set_key(WG_IFACE, priv, listen_port=self.cfg.listen_port, netns=netns,
                         keydir=state_dir())
        wg.addr_add(WG_IFACE, f"{WG_SERVER_IP}/16", netns=netns)
        wg.iface_up(WG_IFACE, netns=netns)

        # enable forwarding (record whether we changed it, to restore on teardown)
        cur = _read_ip_forward(netns)
        if cur == "0":
            _write_ip_forward("1", netns)
            self._set_ip_forward = True

        nftables.apply_exit(self.uplink, WG_IFACE, netns=netns)

        if self.cfg.enable_browser_proxy and netns is None:
            self._proxy_token = secrets.token_urlsafe(24)
            self._proxy, _ = http_proxy.serve_in_thread("0.0.0.0", self.cfg.browser_proxy_port, self._proxy_token)

    def register(self) -> str:
        self.public_ip = self.public_ip or _detect_public_ip()
        if not self.public_ip:
            raise RuntimeError("could not determine this host's public IP for the exit endpoint")
        endpoint = f"{self.public_ip}:{self.cfg.listen_port}"
        http_proxy_ep = (f"{self.public_ip}:{self.cfg.browser_proxy_port}"
                         if self.cfg.enable_browser_proxy else None)
        descriptor = {
            "label": self.cfg.label, "region": self.cfg.region, "country": self.cfg.country,
            "city": self.cfg.city, "wgPubkey": self.wg_pub, "endpoint": endpoint,
            "httpProxy": http_proxy_ep, "proxyToken": self._proxy_token or None,
            "capacityMbps": self.cfg.capacity_mbps,
            "priceHintNanmPerGb": self.cfg.price_hint_nanm_per_gb,
            "tosHash": tos.tos_hash(),
        }
        res = self.reg.register_exit(descriptor, idem=f"exit-register:{self.wg_pub}")
        self.exit_id = res.get("id") or res.get("exitId")
        if not self.exit_id:
            raise RuntimeError(f"registry did not return an exit id: {res}")
        return self.exit_id

    def _reconcile_peers(self) -> None:
        try:
            data = self.reg._req("GET", f"/vpn/exits/{self.exit_id}/peer")
        except Exception:
            return
        granted = {p["clientWgPub"]: p for p in (data.get("peers") or [])}
        now = time.time()
        # add new
        for pub, p in granted.items():
            if pub not in self.peers:
                aip = p["allocatedIp"]
                wg.peer_add(WG_IFACE, pub, f"{aip}/32", netns=self._ns)
                wg.route_add(f"{aip}/32", WG_IFACE, netns=self._ns)  # allowed-ips is an ACL, not a route
                self.peers[pub] = _PeerState(session_id=p["sessionId"], pubkey=pub,
                                             allocated_ip=aip,
                                             meter=accounting.UsageMeter(p["sessionId"], "exit"),
                                             last_seen=now)
        # remove revoked
        for pub in list(self.peers):
            if pub not in granted:
                wg.peer_remove(WG_IFACE, pub, netns=self._ns)
                self.peers.pop(pub, None)

    def _meter_and_report(self) -> None:
        xfer = wg.transfer(WG_IFACE, netns=self._ns)
        hs = wg.latest_handshakes(WG_IFACE, netns=self._ns)
        now = int(time.time())
        for pub, st in list(self.peers.items()):
            rx, tx = xfer.get(pub, (0, 0))
            sample = st.meter.observe(rx, tx, now)
            if hs.get(pub, 0):
                st.last_seen = time.time()
            try:
                report = st.meter.signed_report(self.wallet, sample)
                self.reg.post_report(st.session_id, report)
            except Exception:
                pass
            # reap idle peers (>180s no handshake)
            if time.time() - st.last_seen > 180:
                wg.peer_remove(WG_IFACE, pub, netns=self._ns)
                self.peers.pop(pub, None)

    def heartbeat(self) -> None:
        try:
            self.reg.heartbeat(self.exit_id, {
                "load": min(1.0, len(self.peers) / max(1, self.cfg.capacity_mbps // 10)),
                "openSessions": len(self.peers),
            })
        except Exception:
            pass

    def run(self, *, interval: int = 15, once: bool = False) -> None:
        while True:
            self.heartbeat()
            self._reconcile_peers()
            self._meter_and_report()
            if once:
                return
            time.sleep(interval)

    def teardown(self) -> None:
        for pub in list(self.peers):
            wg.peer_remove(WG_IFACE, pub, netns=self._ns)
        self.peers.clear()
        nftables.remove_exit(netns=self._ns)
        wg.iface_del(WG_IFACE, netns=self._ns)
        if self._proxy is not None:
            try:
                self._proxy.shutdown()
            except Exception:
                pass
        if self._set_ip_forward:
            _write_ip_forward("0", self._ns)
            self._set_ip_forward = False


def _read_ip_forward(netns: Optional[str]) -> str:
    if netns:
        return subprocess.run(["ip", "netns", "exec", netns, "sysctl", "-n", "net.ipv4.ip_forward"],
                              capture_output=True, text=True).stdout.strip()
    try:
        with open("/proc/sys/net/ipv4/ip_forward") as f:
            return f.read().strip()
    except OSError:
        return "0"


def _write_ip_forward(val: str, netns: Optional[str]) -> None:
    cmd = (["ip", "netns", "exec", netns] if netns else []) + \
          ["sysctl", "-qw", f"net.ipv4.ip_forward={val}"]
    subprocess.run(cmd, capture_output=True)
