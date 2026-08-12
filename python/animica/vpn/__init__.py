"""
Animica dVPN — a decentralized VPN built on WireGuard, with ANM bandwidth rewards.

Three planes, all forward-only and NON-consensus (nothing here touches the chain):

  * data plane   — a real single-hop WireGuard tunnel between a native client and an
                   exit node. Kernel ``wg-quick`` is primary; ``boringtun``/``wireguard-go``
                   userspace is an experimental fallback for hosts without the module.
  * control plane— the Animica marketplace acts as the registry / session broker /
                   IOU reconciler (``/api/mkt/v1/vpn/*``). It only ever sees PUBLIC
                   WireGuard keys + endpoints; it is never on the wire.
  * companion    — an optional MV3 browser-proxy mode (separate build). It is NOT a
                   system VPN and must never be labelled one.

Honesty rules enforced across this package (see ``docs/vpn.md``):
  * the native client is the only system-wide tunnel; the browser extension is a proxy.
  * this is single-hop — it hides traffic from your ISP/LAN, NOT from the exit operator.
    It is not Tor and provides no anonymity against the exit or the registry.
  * bandwidth rewards are IOUs (``VpnExit.rewardNanm``); treasury-funded settlement is
    deferred and manually audited. Never claim an executed on-chain transfer.
  * running an exit egresses third-party traffic from your IP — opt-in, ToS-gated,
    off by default, with hard isolation from the mainnet node.
"""

from __future__ import annotations

# WireGuard transport layout (fixed, documented so config/tests agree).
WG_IFACE = "anmwg0"
WG_SUBNET = "10.99.0.0/16"
WG_SERVER_IP = "10.99.0.1"
WG_LISTEN_PORT = 51820          # NOT 443 — the mainnet node owns 443/udp.
WG_CLIENT_BASE = "10.99.0."     # clients get 10.99.0.2 .. 10.99.255.254, each a /32
NFT_TABLE = "animica_vpn"       # isolated nftables table; never touches docker/node chains
KILLSWITCH_TABLE = "anmvpn_ks"  # client-side fail-closed table
SIGN_DOMAIN = "animica:signMessage:"   # must match apps/animica-marketplace lib/wallet-verify.ts

__all__ = [
    "WG_IFACE", "WG_SUBNET", "WG_SERVER_IP", "WG_LISTEN_PORT", "WG_CLIENT_BASE",
    "NFT_TABLE", "KILLSWITCH_TABLE", "SIGN_DOMAIN",
]
