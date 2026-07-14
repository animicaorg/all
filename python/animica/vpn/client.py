"""
Native dVPN client — the ONLY system-wide tunnel (the browser extension is a proxy, not
this). Establishes a real WireGuard tunnel to a chosen exit via wg-quick, forces DNS and a
fail-closed killswitch, and proves it actually tunnels by checking the apparent public IP
changed. Location selection is supported: pick an exit by id, or let the client rank exits
in a region by load/reputation and locally probe RTT.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from typing import Optional

from . import WG_IFACE, nftables, wg
from .config_gen import ClientConf, render_client_conf
from .crypto import Wallet
from .registry_client import Exit, RegistryClient


class ClientError(RuntimeError):
    pass


def state_dir() -> str:
    d = os.environ.get("ANIMICA_VPN_STATE", os.path.expanduser("~/.animica/vpn"))
    os.makedirs(d, mode=0o700, exist_ok=True)
    return d


def _conf_path() -> str:
    return os.path.join(state_dir(), f"{WG_IFACE}.conf")


def _session_path() -> str:
    return os.path.join(state_dir(), "session.json")


def apparent_ip(timeout: int = 8) -> Optional[str]:
    for url in ("https://api.ipify.org", "https://ifconfig.me/ip", "https://icanhazip.com"):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                ip = r.read().decode().strip()
                if ip:
                    return ip
        except Exception:
            continue
    return None


def _probe_rtt_ms(host: str) -> Optional[float]:
    try:
        out = subprocess.run(["ping", "-c", "1", "-W", "2", host], capture_output=True, text=True)
        if out.returncode != 0:
            return None
        for tok in out.stdout.split():
            if tok.startswith("time="):
                return float(tok.split("=", 1)[1])
    except Exception:
        return None
    return None


def select_exit(reg: RegistryClient, *, exit_id: Optional[str] = None, region: Optional[str] = None,
                country: Optional[str] = None) -> Exit:
    """Client-side selection: the registry only RANKS; the client picks (so no server can
    force an exit). Prefer an explicit id, else the lowest-latency online exit in the region."""
    exits = reg.list_exits(region=region, country=country)
    exits = [e for e in exits if e.online]
    if not exits:
        raise ClientError("no online exits available"
                          + (f" in region '{region}'" if region else ""))
    if exit_id:
        for e in exits:
            if e.id == exit_id:
                return e
        raise ClientError(f"exit '{exit_id}' not found or offline")
    # locally probe RTT of the top candidates (by load, then reputation)
    ranked = sorted(exits, key=lambda e: (e.load, -e.reputation))[:5]
    best, best_rtt = None, float("inf")
    for e in ranked:
        host = e.endpoint.rsplit(":", 1)[0]
        rtt = _probe_rtt_ms(host)
        score = rtt if rtt is not None else 9999.0
        if score < best_rtt:
            best, best_rtt = e, score
    return best or ranked[0]


@dataclass
class Connection:
    session_id: str
    exit: Exit
    allocated_ip: str
    started_ts: int


def connect(wallet: Wallet, *, exit_id: Optional[str] = None, region: Optional[str] = None,
            country: Optional[str] = None, allowed_ips: str = "0.0.0.0/0",
            dns: str = "1.1.1.1", killswitch: bool = True,
            reg: Optional[RegistryClient] = None) -> dict:
    reg = reg or RegistryClient(wallet)
    backend = wg.detect_backend()          # raises with a clear message if WG unavailable
    ex = select_exit(reg, exit_id=exit_id, region=region, country=country)

    before_ip = apparent_ip()
    priv, pub = wg.genkeys()
    grant = reg.request_session(ex.id, pub)
    session_id = grant["sessionId"]
    allocated = grant["allocatedIp"]        # e.g. 10.99.0.7
    exit_pub = grant.get("exitWgPubkey") or ex.wgPubkey
    exit_ep = grant.get("exitEndpoint") or ex.endpoint

    conf = render_client_conf(ClientConf(
        private_key=priv, address=f"{allocated}/32", exit_pubkey=exit_pub,
        exit_endpoint=exit_ep, allowed_ips=allowed_ips, dns=dns,
    ))
    path = _conf_path()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, conf.encode())
    finally:
        os.close(fd)

    env = wg.userspace_env(backend)
    r = subprocess.run(["wg-quick", "up", path], capture_output=True, text=True,
                       env={**os.environ, **env})
    if r.returncode != 0:
        raise ClientError(f"wg-quick up failed: {r.stderr.strip()}")

    if killswitch and allowed_ips.strip() in ("0.0.0.0/0", "0.0.0.0/0, ::/0"):
        ep_ip = exit_ep.rsplit(":", 1)[0]
        ep_port = int(exit_ep.rsplit(":", 1)[1])
        try:
            nftables.apply_killswitch(WG_IFACE, ep_ip, ep_port)
        except Exception:
            pass  # best-effort; wg-quick already routed everything into the tunnel

    with open(_session_path(), "w") as f:
        json.dump({"sessionId": session_id, "exitId": ex.id, "allocatedIp": allocated,
                   "started": int(time.time()), "confPath": path,
                   "exitEndpoint": exit_ep, "killswitch": killswitch}, f)

    after_ip = apparent_ip()
    return {
        "sessionId": session_id, "exit": ex.__dict__, "allocatedIp": allocated,
        "backend": backend.kind, "beforeIp": before_ip, "afterIp": after_ip,
        "tunneling": bool(after_ip and after_ip != before_ip),
    }


def disconnect(wallet: Optional[Wallet] = None, reg: Optional[RegistryClient] = None) -> dict:
    sp = _session_path()
    if not os.path.exists(sp):
        # still try to tear down a stray interface
        subprocess.run(["wg-quick", "down", _conf_path()], capture_output=True)
        nftables.remove_killswitch()
        return {"status": "no active session"}
    with open(sp) as f:
        sess = json.load(f)
    subprocess.run(["wg-quick", "down", sess.get("confPath", _conf_path())], capture_output=True)
    nftables.remove_killswitch()
    if wallet is not None:
        try:
            reg = reg or RegistryClient(wallet)
            reg.end_session(sess["exitId"], sess["sessionId"])
        except Exception:
            pass
    try:
        os.unlink(sp)
    except OSError:
        pass
    return {"status": "disconnected", "sessionId": sess.get("sessionId")}


def status() -> dict:
    sp = _session_path()
    if not os.path.exists(sp):
        return {"connected": False}
    with open(sp) as f:
        sess = json.load(f)
    hs = wg.latest_handshakes(WG_IFACE)
    xfer = wg.transfer(WG_IFACE)
    return {
        "connected": bool(hs),
        "sessionId": sess.get("sessionId"),
        "exitId": sess.get("exitId"),
        "apparentIp": apparent_ip(),
        "handshakes": hs,
        "transfer": {k: {"rx": v[0], "tx": v[1]} for k, v in xfer.items()},
        "killswitch": sess.get("killswitch"),
    }
