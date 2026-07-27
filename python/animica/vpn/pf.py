"""
macOS (pf / pfctl) firewall for the dVPN — the Darwin counterpart of nftables.py.

Linux gets a fully-isolated `inet animica_vpn` nft table. macOS pf has no equivalent per-table
isolation: rules only take effect when an anchor is referenced from the MAIN ruleset, and the
main ruleset has a STRICT section order (options → normalization → queueing → translation →
filtering). Naively concatenating `pfctl -sn` + `pfctl -sr` dumps reorders those sections and
pfctl rejects it, so we do NOT rebuild from dumps. Instead we author a known-good ordered main
ruleset that references the standard `com.apple/*` anchors PLUS our own, with our filter anchors
placed BEFORE com.apple's (every rule in ours is `quick`, so ours win and no pre-existing host
rule can preempt the security policy). Restore reloads the canonical /etc/pf.conf.

Security posture — FAIL CLOSED. Every step is validated with `pfctl -nf` before being applied;
pf is enabled with a reference token and its enabled state is re-verified; pre-existing states
are flushed so no cleartext flow survives; and the anchor is confirmed loaded AND referenced.
Any failure is a refusal to serve — an exit must never egress third-party traffic without its
egress ACL, and a killswitch must never be reported active unless it actually filters.

Callers pass the REAL interface (the utunN wg-quick created), never the logical `anmwg0` — pf
matches kernel device names, so a logical name would load rules that never match (fail-open).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from typing import Optional

from . import KILLSWITCH_TABLE, NFT_TABLE, WG_SERVER_IP, WG_SUBNET
from .nftables import (BLOCKED_BOTH_PORTRANGE, BLOCKED_DST_V4, BLOCKED_TCP_PORTS,
                       BLOCKED_UDP_PORTS)

_APPLE_ANCHORS = "/etc/pf.anchors/com.apple"
_PF_CONF = "/etc/pf.conf"


class PfError(RuntimeError):
    pass


# --------------------------------------------------------------------------- render

def render_exit_anchor(uplink: str, wg_iface: str, *, subnet: str = WG_SUBNET,
                       server_ip: str = WG_SERVER_IP,
                       extra_block_v4: Optional[list[str]] = None) -> str:
    """Render the exit anchor body: scoped NAT + default-deny-abuse egress ACL for the tunnel.
    `wg_iface` MUST be the real kernel device (utunN on macOS). Mirrors nftables.render_exit_ruleset."""
    blocked = list(BLOCKED_DST_V4) + list(extra_block_v4 or [])
    dst_set = ", ".join(blocked)
    tcp_ports = ", ".join(str(p) for p in BLOCKED_TCP_PORTS)
    udp_ports = ", ".join(str(p) for p in BLOCKED_UDP_PORTS)
    lo, hi = BLOCKED_BOTH_PORTRANGE.split("-")
    return f"""\
# --- scoped source NAT for the tunnel subnet only (never the host's own traffic) ---
nat on {uplink} inet from {subnet} to any -> ({uplink})

# --- protect the exit HOST from the tunnel: allow only PMTU/liveness ICMP echo to the gateway ---
pass in quick on {wg_iface} inet proto icmp from {subnet} to {server_ip} icmp-type echoreq keep state

# BCP38 anti-spoofing: a tunnel packet must originate from the tunnel subnet.
block drop in quick on {wg_iface} inet from ! {subnet} to any

# No IPv6 exit in v1 — drop tunnelled v6 so it can't leak around the v4 ACL.
block drop in quick on {wg_iface} inet6 all

# Block private / metadata / loopback / CGNAT destinations from the tunnel (SSRF/LAN).
block drop in quick on {wg_iface} inet from {subnet} to {{ {dst_set} }}

# Host-service protection: the tunnel may not reach ANY address on this host (pf `self`).
block drop in quick on {wg_iface} inet from {subnet} to self

# Block abuse-prone ports from the tunnel (spam, lateral movement, torrents).
block drop in quick on {wg_iface} inet proto tcp from {subnet} to any port {{ {tcp_ports} }}
block drop in quick on {wg_iface} inet proto udp from {subnet} to any port {{ {udp_ports} }}
block drop in quick on {wg_iface} inet proto tcp from {subnet} to any port {lo}:{hi}
block drop in quick on {wg_iface} inet proto udp from {subnet} to any port {lo}:{hi}

# Everything else from the tunnel may egress (NAT translates it out the uplink).
pass in quick on {wg_iface} inet from {subnet} to any keep state
"""


def render_killswitch_anchor(wg_iface: str, exit_endpoint_ip: str, exit_port: int) -> str:
    """Client fail-closed killswitch anchor. `wg_iface` MUST be the real kernel device (utunN)."""
    return f"""\
# fail-closed: allow only lo, the tunnel iface, and the encrypted transport to the exit.
pass out quick on lo0 all
pass out quick on {wg_iface} all
pass out quick inet proto udp from any to {exit_endpoint_ip} port {exit_port} keep state
# Everything else out the physical uplink is dropped while this anchor is loaded.
block drop out quick inet from any to any
block drop out quick inet6 from any to any
"""


# ----------------------------------------------------------------------- pfctl plumbing

def _pfctl(args: list[str], *, input_text: Optional[str] = None,
           check: bool = True) -> subprocess.CompletedProcess:
    try:
        cp = subprocess.run(["pfctl", *args], capture_output=True, text=True, input=input_text)
    except FileNotFoundError as e:
        raise PfError("pfctl not found — macOS packet filter unavailable") from e
    if check and cp.returncode != 0:
        raise PfError(f"`pfctl {' '.join(args)}` failed ({cp.returncode}): {cp.stderr.strip()}")
    return cp


def _pf_enabled() -> bool:
    cp = _pfctl(["-s", "info"], check=False)
    return "Status: Enabled" in cp.stdout


def _snapshot_path(confdir: str) -> str:
    # ONE shared snapshot for the host's pf state (both anchors share it); avoids the per-anchor
    # clobber where removing one anchor restored a stale full snapshot that detached the other.
    return os.path.join(confdir, "pf-snapshot.json")


def anchor_loaded(anchor: str) -> bool:
    """True iff our anchor has rules AND pf is enabled AND the main ruleset references it —
    a non-empty anchor alone is not proof the policy is in force."""
    if not shutil.which("pfctl"):
        return False
    if not _pf_enabled():
        return False
    rules = _pfctl(["-a", anchor, "-s", "rules"], check=False).stdout.strip()
    if not rules:
        return False
    main = _pfctl(["-s", "rules"], check=False).stdout
    return f'anchor "{anchor}"' in main


def build_main_ruleset(*, exit_active: bool, killswitch_active: bool,
                       apple_anchors: bool = True) -> str:
    """Author a correctly-ordered pf main ruleset that references the standard com.apple anchors
    plus whichever animica anchors are active. Section order is fixed (normalization → translation
    → queueing/dummynet → filtering); our filter anchors come BEFORE com.apple's so their `quick`
    rules win. Pure string builder — unit-tested for ordering."""
    lines: list[str] = []
    if apple_anchors:
        lines += ['scrub-anchor "com.apple/*"',
                  'nat-anchor "com.apple/*"',
                  'rdr-anchor "com.apple/*"']
    if exit_active:
        lines.append(f'nat-anchor "{NFT_TABLE}"')          # translation section
    if apple_anchors:
        lines.append('dummynet-anchor "com.apple/*"')      # macOS queueing, after translation
    if exit_active:
        lines.append(f'anchor "{NFT_TABLE}"')              # filtering — before com.apple (quick)
    if killswitch_active:
        lines.append(f'anchor "{KILLSWITCH_TABLE}"')
    if apple_anchors:
        lines.append('anchor "com.apple/*"')
        lines.append(f'load anchor "com.apple" from "{_APPLE_ANCHORS}"')
    return "\n".join(lines) + "\n"


def _active_anchors(besides: Optional[str] = None, plus: Optional[str] = None) -> dict:
    """Which animica anchors currently have rules loaded (for composing the main ruleset)."""
    def loaded(a: str) -> bool:
        if a == plus:
            return True
        if a == besides:
            return False
        return bool(_pfctl(["-a", a, "-s", "rules"], check=False).stdout.strip())
    return {"exit": loaded(NFT_TABLE), "ks": loaded(KILLSWITCH_TABLE)}


def _apply_anchor(anchor: str, body: str, *, confdir: str, flush_states: bool) -> None:
    if not shutil.which("pfctl"):
        raise PfError("pfctl not found — refusing to run without the macOS packet filter")

    was_enabled = _pf_enabled()
    apple = os.path.exists(_APPLE_ANCHORS)

    # Validate + load our anchor rules first (does not affect traffic until referenced).
    _pfctl(["-a", anchor, "-n", "-f", "-"], input_text=body)  # parse-only
    _pfctl(["-a", anchor, "-f", "-"], input_text=body)

    active = _active_anchors(plus=anchor)
    main = build_main_ruleset(exit_active=active["exit"], killswitch_active=active["ks"],
                              apple_anchors=apple)
    try:
        _pfctl(["-n", "-f", "-"], input_text=main)           # parse-only: correct section order
    except PfError:
        _pfctl(["-a", anchor, "-F", "all"], check=False)     # roll our anchor back
        raise

    # Persist enough to restore exactly on teardown.
    os.makedirs(confdir, mode=0o700, exist_ok=True)
    snap = _load_snapshot(confdir)
    if "was_enabled" not in snap:
        snap["was_enabled"] = was_enabled
    snap["apple"] = apple
    _save_snapshot(confdir, snap)

    try:
        _pfctl(["-f", "-"], input_text=main)                 # load the referencing main ruleset
        if not was_enabled:
            tok = _enable_pf()
            snap["enable_token"] = tok
            _save_snapshot(confdir, snap)
        if not _pf_enabled():
            raise PfError("pf did not enable — refusing (fail-closed)")
        if flush_states:
            # Drop pre-existing (cleartext) states so nothing survives the policy switch; the
            # WG handshake re-establishes through the allowed rule.
            _pfctl(["-F", "states"], check=False)
        if not anchor_loaded(anchor):
            raise PfError(f"pf anchor '{anchor}' is not in force after load — refusing (fail-closed)")
    except PfError:
        _rollback(anchor, confdir)
        raise


def _enable_pf() -> Optional[str]:
    """Enable pf with a reference token (`pfctl -E`) so another agent's `pfctl -d` can't silently
    disable us; return the token to release on teardown."""
    cp = _pfctl(["-E"], check=False)
    m = re.search(r"Token\s*:\s*(\d+)", (cp.stdout or "") + (cp.stderr or ""))
    return m.group(1) if m else None


def _load_snapshot(confdir: str) -> dict:
    try:
        with open(_snapshot_path(confdir)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_snapshot(confdir: str, snap: dict) -> None:
    try:
        with open(_snapshot_path(confdir), "w") as f:
            json.dump(snap, f)
    except OSError:
        pass


def _rollback(anchor: str, confdir: str) -> None:
    _pfctl(["-a", anchor, "-F", "all"], check=False)
    _remove_anchor(anchor, confdir=confdir)


def _remove_anchor(anchor: str, *, confdir: str) -> None:
    """Flush our anchor, rebuild the main ruleset from the OTHER still-active animica anchor (so
    removing one never detaches the other), and if none remain restore the canonical /etc/pf.conf
    and release our pf-enable token. Idempotent, best-effort but noisy on failure."""
    if not shutil.which("pfctl"):
        return
    _pfctl(["-a", anchor, "-F", "all"], check=False)
    snap = _load_snapshot(confdir)
    apple = snap.get("apple", os.path.exists(_APPLE_ANCHORS))
    active = _active_anchors(besides=anchor)
    if active["exit"] or active["ks"]:
        main = build_main_ruleset(exit_active=active["exit"], killswitch_active=active["ks"],
                                  apple_anchors=apple)
        cp = _pfctl(["-f", "-"], input_text=main, check=False)
        if cp.returncode != 0:
            # Loud, not swallowed: leaving a stale reference is a real problem.
            import sys
            print(f"[animica-vpn] WARNING: pf restore reload failed: {cp.stderr.strip()}",
                  file=sys.stderr)
        return
    # No animica anchors remain — restore the host's canonical ruleset and release our token.
    if os.path.exists(_PF_CONF):
        cp = _pfctl(["-f", _PF_CONF], check=False)
        if cp.returncode != 0:
            import sys
            print(f"[animica-vpn] WARNING: pf restore from {_PF_CONF} failed: {cp.stderr.strip()}",
                  file=sys.stderr)
    token = snap.get("enable_token")
    if snap.get("was_enabled") is False:
        if token:
            _pfctl(["-X", str(token)], check=False)          # release our reference
        else:
            _pfctl(["-d"], check=False)
    try:
        os.unlink(_snapshot_path(confdir))
    except OSError:
        pass


# --------------------------------------------------------------------------- public API

def apply_exit(uplink: str, wg_iface: str, *, confdir: str, **kw) -> None:
    _apply_anchor(NFT_TABLE, render_exit_anchor(uplink, wg_iface, **kw),
                  confdir=confdir, flush_states=True)


def remove_exit(*, confdir: str) -> None:
    _remove_anchor(NFT_TABLE, confdir=confdir)


def apply_killswitch(wg_iface: str, exit_endpoint_ip: str, exit_port: int, *, confdir: str) -> None:
    _apply_anchor(KILLSWITCH_TABLE,
                  render_killswitch_anchor(wg_iface, exit_endpoint_ip, exit_port),
                  confdir=confdir, flush_states=True)


def remove_killswitch(*, confdir: str) -> None:
    _remove_anchor(KILLSWITCH_TABLE, confdir=confdir)
