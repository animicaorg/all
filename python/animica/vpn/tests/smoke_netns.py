#!/usr/bin/env python3
"""
End-to-end dVPN smoke test exercising the REAL animica.vpn.wg + animica.vpn.nftables code
in two isolated network namespaces. Zero impact on the host's default namespace (no port
bound there, no host default route changed, no docker/nginx rule touched); full teardown.

Topology (mirrors the proven PoC): the host is the router/NAT.
    client(NS_A) --veth-- host --veth-- exit(NS_B) --(host NAT)--> internet
The client's ONLY internet path is the WireGuard tunnel to the exit, so any egress proves
the tunnel carries it.

Proofs:
  P1 real WireGuard handshake + tunnel data plane (client pings exit tunnel IP)
  P2 real internet egress THROUGH the exit (apparent IP == host public IP)
  P3 exit egress ACL blocks a metadata/private destination (169.254.169.254)
  P4 byte counters move (accounting signal)

Run as root:  python3 -m animica.vpn.tests.smoke_netns
"""

from __future__ import annotations

import subprocess
import sys
import time

from animica.vpn import WG_LISTEN_PORT, nftables, wg

NS_A, NS_B = "anmvpn_smoke_a", "anmvpn_smoke_b"     # client, exit
IFACE = "anmwg0"
HOST_A, EXIT_A = "10.211.0.1", "10.211.0.2"         # host<->client transport
HOST_B, EXIT_B = "10.212.0.1", "10.212.0.2"         # host<->exit uplink
CLIENT_WG, EXIT_WG = "10.99.0.2", "10.99.0.1"
TAG = "ANMVPNSMOKE"


def sh(*args, ns=None, check=True):
    cmd = (["ip", "netns", "exec", ns] if ns else []) + list(args)
    cp = subprocess.run(cmd, capture_output=True, text=True)
    if check and cp.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} -> {cp.returncode}: {cp.stderr.strip()}")
    return cp


def _host_egress_iface() -> str:
    out = subprocess.run(["ip", "route", "show", "default"], capture_output=True, text=True).stdout.split()
    return out[out.index("dev") + 1] if "dev" in out else "eth0"


def cleanup(defif="eth0"):
    for ns in (NS_A, NS_B):
        subprocess.run(["ip", "netns", "del", ns], capture_output=True)
    for dev in ("veth_ha", "veth_hb"):
        subprocess.run(["ip", "link", "del", dev], capture_output=True)
    subprocess.run(["bash", "-c", f"""
      rm -rf /etc/netns/{NS_A}
      while iptables -t nat -C POSTROUTING -s 10.212.0.0/24 -o {defif} -m comment --comment {TAG} -j MASQUERADE 2>/dev/null; do
        iptables -t nat -D POSTROUTING -s 10.212.0.0/24 -o {defif} -m comment --comment {TAG} -j MASQUERADE; done
      for pair in "10.211.0.0/24 10.212.0.0/24" "10.212.0.0/24 10.211.0.0/24"; do set -- $pair
        while iptables -C FORWARD -s $1 -d $2 -m comment --comment {TAG} -j ACCEPT 2>/dev/null; do
          iptables -D FORWARD -s $1 -d $2 -m comment --comment {TAG} -j ACCEPT; done
      done"""], capture_output=True)


def main() -> int:
    try:
        backend = wg.detect_backend()
    except wg.WgError as e:
        print(f"SKIP: {e}")
        return 0
    print(f"backend: {backend.kind} ({backend.note})")
    defif = _host_egress_iface()
    cleanup(defif)
    ok = False
    try:
        # namespaces
        sh("ip", "netns", "add", NS_A)
        sh("ip", "netns", "add", NS_B)
        # host <-> client
        sh("ip", "link", "add", "veth_ha", "type", "veth", "peer", "name", "veth_a", "netns", NS_A)
        sh("ip", "addr", "add", f"{HOST_A}/24", "dev", "veth_ha"); sh("ip", "link", "set", "veth_ha", "up")
        sh("ip", "-n", NS_A, "addr", "add", f"{EXIT_A}/24", "dev", "veth_a")
        sh("ip", "-n", NS_A, "link", "set", "veth_a", "up"); sh("ip", "-n", NS_A, "link", "set", "lo", "up")
        # host <-> exit
        sh("ip", "link", "add", "veth_hb", "type", "veth", "peer", "name", "veth_b", "netns", NS_B)
        sh("ip", "addr", "add", f"{HOST_B}/24", "dev", "veth_hb"); sh("ip", "link", "set", "veth_hb", "up")
        sh("ip", "-n", NS_B, "addr", "add", f"{EXIT_B}/24", "dev", "veth_b")
        sh("ip", "-n", NS_B, "link", "set", "veth_b", "up"); sh("ip", "-n", NS_B, "link", "set", "lo", "up")
        sh("ip", "-n", NS_B, "route", "add", "default", "via", HOST_B)   # exit -> internet via host

        # host routing: NAT the exit uplink to the internet; forward client<->exit transport
        sh("sysctl", "-qw", "net.ipv4.ip_forward=1")
        sh("iptables", "-t", "nat", "-A", "POSTROUTING", "-s", "10.212.0.0/24", "-o", defif,
           "-m", "comment", "--comment", TAG, "-j", "MASQUERADE")
        sh("iptables", "-A", "FORWARD", "-s", "10.211.0.0/24", "-d", "10.212.0.0/24",
           "-m", "comment", "--comment", TAG, "-j", "ACCEPT")
        sh("iptables", "-A", "FORWARD", "-s", "10.212.0.0/24", "-d", "10.211.0.0/24",
           "-m", "comment", "--comment", TAG, "-j", "ACCEPT")

        # --- exit side: REAL wg + nftables modules ---
        exit_priv, exit_pub = wg.genkeys()
        client_priv, client_pub = wg.genkeys()
        wg.iface_add(IFACE, netns=NS_B)
        wg.iface_set_key(IFACE, exit_priv, listen_port=WG_LISTEN_PORT, netns=NS_B)
        wg.peer_add(IFACE, client_pub, f"{CLIENT_WG}/32", netns=NS_B)
        wg.addr_add(IFACE, f"{EXIT_WG}/16", netns=NS_B)
        wg.iface_up(IFACE, netns=NS_B)
        wg.route_add(f"{CLIENT_WG}/32", IFACE, netns=NS_B)     # allowed-ips is an ACL, not a route
        sh("sysctl", "-qw", "net.ipv4.ip_forward=1", ns=NS_B)
        nftables.apply_exit("veth_b", IFACE, netns=NS_B)       # REAL egress ACL + scoped NAT

        # --- client side (raw wg, mirrors config_gen full-tunnel) ---
        wg.iface_add(IFACE, netns=NS_A)
        wg.iface_set_key(IFACE, client_priv, netns=NS_A)
        wg.peer_add(IFACE, exit_pub, "0.0.0.0/0", endpoint=f"{EXIT_B}:{WG_LISTEN_PORT}",
                    keepalive=15, netns=NS_A)
        wg.addr_add(IFACE, f"{CLIENT_WG}/32", netns=NS_A)
        wg.iface_up(IFACE, netns=NS_A)
        # to reach the exit's public endpoint, go via the host; everything else via the tunnel
        sh("ip", "-n", NS_A, "route", "add", f"{EXIT_B}/32", "via", HOST_A)
        sh("ip", "-n", NS_A, "route", "add", "default", "dev", IFACE)
        # force DNS through the tunnel to a public resolver (mirrors wg-quick DNS=1.1.1.1);
        # the host's 127.0.0.53 systemd-resolved would be blocked by the exit's loopback ACL.
        subprocess.run(["bash", "-c", f"mkdir -p /etc/netns/{NS_A}; echo 'nameserver 1.1.1.1' > /etc/netns/{NS_A}/resolv.conf"], check=True)
        # host must forward client->exit-endpoint (10.212.0.2); route already covered by FORWARD accept
        sh("ip", "route", "add", "10.212.0.0/24", "dev", "veth_hb", check=False)

        # wait for handshake
        for _ in range(12):
            subprocess.run(["ip", "netns", "exec", NS_A, "ping", "-c1", "-W1", EXIT_WG], capture_output=True)
            if any(v > 0 for v in wg.latest_handshakes(IFACE, netns=NS_A).values()):
                break
            time.sleep(1)

        p1 = sh("ping", "-c3", "-W2", EXIT_WG, ns=NS_A, check=False).stdout
        p1_ok = "0% packet loss" in p1
        print(f"P1 tunnel data plane   : {'PASS' if p1_ok else 'FAIL'}")

        tun_ip = sh("curl", "-s", "--max-time", "12", "https://api.ipify.org", ns=NS_A, check=False).stdout.strip()
        host_ip = subprocess.run(["curl", "-s", "--max-time", "8", "https://api.ipify.org"],
                                 capture_output=True, text=True).stdout.strip()
        p2_ok = bool(tun_ip) and tun_ip == host_ip
        print(f"P2 internet via exit   : {'PASS' if p2_ok else 'FAIL'} (tunnel={tun_ip!r} host={host_ip!r})")

        meta = sh("curl", "-s", "--max-time", "5", "-o", "/dev/null", "-w", "%{http_code}",
                  "http://169.254.169.254/", ns=NS_A, check=False).stdout.strip()
        p3_ok = meta in ("", "000")
        print(f"P3 egress ACL (metadata): {'PASS' if p3_ok else 'FAIL'} (code={meta!r})")

        xfer = wg.transfer(IFACE, netns=NS_B)
        p4_ok = any(rx > 0 or tx > 0 for rx, tx in xfer.values())
        print(f"P4 byte counters move  : {'PASS' if p4_ok else 'FAIL'}")

        ok = p1_ok and p2_ok and p3_ok and p4_ok
    finally:
        cleanup(defif)
    print("\nSMOKE:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
