#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ops/seeds/check_seeds.py — dial seeds & write report

Checks bootstrap/DNS seeds for reachability and policy compliance, then writes
a machine-readable JSON report plus a short human summary.

Design goals
------------
- **Zero deps** (stdlib only) so this runs in minimal CI/ops environments.
- Conservative network tests:
  - TCP: real connect with latency.
  - UDP/QUIC: best-effort "probe" (UDP send); not authoritative.
- Policy gates: consult allowlist/blocklist (CIDR/ASN/FQDN).
- Exit codes suitable for CI:
  0 = healthy; 1 = error; 2 = blocklist hit; 3 = insufficient reachable seeds.

Inputs
------
- ops/seeds/bootstrap_nodes.json        (multiaddr-like entries)
- ops/seeds/dnsseeds.txt                (authoritative DNS seeds; optional)
- ops/seeds/seed_health.allowlist       (optional)
- ops/seeds/seed_health.blocklist       (optional)

Outputs
-------
- ops/seeds/reports/seed_report_<ts>.json
- ops/seeds/reports/seed_report_latest.json (atomic replace)
- Summary printed to stdout

Usage
-----
$ python ops/seeds/check_seeds.py \
    --min-ok 2 \
    --timeout 2.0 \
    --seeds ops/seeds/bootstrap_nodes.json \
    --dns ops/seeds/dnsseeds.txt
"""
from __future__ import annotations

import argparse
import concurrent.futures
import ipaddress
import json
import os
import socket
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# --------------------------------------------------------------------------------------
# Files & defaults
# --------------------------------------------------------------------------------------

DEFAULT_SEEDS = Path("ops/seeds/bootstrap_nodes.json")
DEFAULT_DNS = Path("ops/seeds/dnsseeds.txt")
DEFAULT_ALLOW = Path("ops/seeds/seed_health.allowlist")
DEFAULT_BLOCK = Path("ops/seeds/seed_health.blocklist")
REPORT_DIR = Path("ops/seeds/reports")

# --------------------------------------------------------------------------------------
# Helpers: parsing policy lists
# --------------------------------------------------------------------------------------


@dataclass
class Policy:
    allow_asn: set[int]
    allow_cidr: List[ipaddress._BaseNetwork]
    allow_fqdn: set[str]
    block_asn: set[int]
    block_cidr: List[ipaddress._BaseNetwork]
    block_fqdn: set[str]


def _read_policy_list(
    path: Path,
) -> Tuple[set[int], List[ipaddress._BaseNetwork], set[str]]:
    asns: set[int] = set()
    cidrs: List[ipaddress._BaseNetwork] = []
    fqdns: set[str] = set()
    if not path.is_file():
        return asns, cidrs, fqdns
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            key, value = line.split(":", 1)
        except ValueError:
            continue
        value = value.strip()
        if key == "asn":
            try:
                asns.add(int(value))
            except ValueError:
                pass
        elif key == "cidr":
            try:
                cidrs.append(ipaddress.ip_network(value, strict=False))
            except ValueError:
                pass
        elif key == "fqdn":
            fqdns.add(value.lower())
    return asns, cidrs, fqdns


def load_policy(allow_path: Path, block_path: Path) -> Policy:
    a_asn, a_cidr, a_fqdn = _read_policy_list(allow_path)
    b_asn, b_cidr, b_fqdn = _read_policy_list(block_path)
    return Policy(
        allow_asn=a_asn,
        allow_cidr=a_cidr,
        allow_fqdn=a_fqdn,
        block_asn=b_asn,
        block_cidr=b_cidr,
        block_fqdn=b_fqdn,
    )


# --------------------------------------------------------------------------------------
# Multiaddr-like parsing
# Supported forms (subset):
#   /ip4/1.2.3.4/tcp/30333
#   /ip6/2001:db8::1/tcp/30333
#   /dns/seed.example.org/udp/443/quic-v1
#   /dns/seed.example.org/tcp/30333
# --------------------------------------------------------------------------------------


@dataclass
class Endpoint:
    raw: str
    host: str
    port: Optional[int]
    proto: str  # 'tcp' or 'udp'
    transport: Optional[str]  # e.g., 'quic-v1'
    addr_type: str  # 'ip4','ip6','dns'


def parse_multiaddr(ma: str) -> Optional[Endpoint]:
    parts = [p for p in ma.split("/") if p]
    if len(parts) < 4:
        return None
    atype = parts[0]
    host = parts[1]
    proto = parts[2]
    port: Optional[int] = None
    transport: Optional[str] = None
    if proto not in ("tcp", "udp"):
        # Sometimes it's /udp/443/quic or /udp/443/quic-v1 — accept but mark proto=udp
        # If third token not a proto, try shift:
        # e.g., /dns/host/quic-v1/udp/443 -> uncommon; ignore
        return None
    try:
        port = int(parts[3])
    except ValueError:
        return None
    if len(parts) >= 5:
        transport = parts[4]
    return Endpoint(
        raw=ma, host=host, port=port, proto=proto, transport=transport, addr_type=atype
    )


# --------------------------------------------------------------------------------------
# Seed sources
# --------------------------------------------------------------------------------------


def load_bootstrap_seeds(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception as exc:
        raise SystemExit(f"Failed to parse {path}: {exc}")


def load_dnsseeds(path: Path) -> List[str]:
    if not path.is_file():
        return []
    out: List[str] = []
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line)
    return out


# --------------------------------------------------------------------------------------
# Policy checks
# --------------------------------------------------------------------------------------


def ip_allowed(ip: str, policy: Policy) -> Tuple[bool, Optional[str]]:
    try:
        ipaddr = ipaddress.ip_address(ip)
    except ValueError:
        return True, None  # can't evaluate; don't block
    # Blocklist overrides allowlist
    for net in policy.block_cidr:
        if ipaddr in net:
            return False, f"blocked_by_cidr:{net}"
    # Allowlist (if present) — if list is non-empty, require membership; else pass-through
    if policy.allow_cidr:
        for net in policy.allow_cidr:
            if ipaddr in net:
                return True, f"allowed_by_cidr:{net}"
        return False, "not_in_allow_cidr"
    return True, None


def fqdn_allowed(name: str, policy: Policy) -> Tuple[bool, Optional[str]]:
    nm = (name or "").lower()
    if nm in policy.block_fqdn:
        return False, "blocked_by_fqdn"
    if policy.allow_fqdn and nm not in policy.allow_fqdn:
        return False, "not_in_allow_fqdn"
    return True, None


def asn_allowed(asn: Optional[int], policy: Policy) -> Tuple[bool, Optional[str]]:
    if asn is None:
        # If allowlist has ASN entries, but we don't know the ASN, don't fail hard; mark as unknown.
        return (True if not policy.allow_asn else False), (
            "asn_unknown" if policy.allow_asn else None
        )
    if asn in policy.block_asn:
        return False, "blocked_by_asn"
    if policy.allow_asn and asn not in policy.allow_asn:
        return False, "not_in_allow_asn"
    return True, None


# --------------------------------------------------------------------------------------
# Net probes
# --------------------------------------------------------------------------------------


def resolve_host(host: str) -> List[Tuple[str, int]]:
    """Return list of (ip, family) tuples for host. family: 4 or 6."""
    out: List[Tuple[str, int]] = []
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)  # name only
        for fam, _stype, _proto, _canon, sa in infos:
            ip = sa[0]
            if fam == socket.AF_INET:
                out.append((ip, 4))
            elif fam == socket.AF_INET6:
                out.append((ip, 6))
    except socket.gaierror:
        pass
    # dedupe
    seen = set()
    uniq: List[Tuple[str, int]] = []
    for ip, fam in out:
        key = (ip, fam)
        if key not in seen:
            seen.add(key)
            uniq.append((ip, fam))
    return uniq


def probe_tcp(ip: str, port: int, timeout: float) -> Tuple[bool, float, Optional[str]]:
    start = time.perf_counter()
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            elapsed = (time.perf_counter() - start) * 1000.0
            return True, elapsed, None
    except Exception as exc:
        elapsed = (time.perf_counter() - start) * 1000.0
        return False, elapsed, str(exc)


def probe_udp(ip: str, port: int, timeout: float) -> Tuple[bool, float, Optional[str]]:
    """
    UDP has no handshake; we mark success if we can send a datagram without local error.
    This is a *weak* signal (SENT), not a confirmation of a listening service.
    """
    start = time.perf_counter()
    try:
        with socket.socket(
            socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_DGRAM
        ) as s:
            s.settimeout(timeout)
            s.connect((ip, port))
            # Send a tiny "ping" payload
            s.send(b"ping")
            elapsed = (time.perf_counter() - start) * 1000.0
            return True, elapsed, None
    except Exception as exc:
        elapsed = (time.perf_counter() - start) * 1000.0
        return False, elapsed, str(exc)


# --------------------------------------------------------------------------------------
# Report structs
# --------------------------------------------------------------------------------------


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def atomic_write(path: Path, data: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(data)
    os.replace(tmp, path)


# --------------------------------------------------------------------------------------
# Core logic
# --------------------------------------------------------------------------------------


def check_endpoints(
    endpoints: List[Endpoint], timeout: float, policy: Policy
) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []

    def work(ep: Endpoint) -> Dict[str, Any]:
        # Resolve host → IPs (or pass-through if already IP)
        host_is_ip = False
        ips: List[Tuple[str, int]] = []
        try:
            ipaddress.ip_address(ep.host)
            host_is_ip = True
            ips = [(ep.host, 4 if "." in ep.host else 6)]
        except ValueError:
            ips = resolve_host(ep.host)

        if not ips:
            return dict(
                endpoint=ep.raw,
                host=ep.host,
                port=ep.port,
                proto=ep.proto,
                transport=ep.transport,
                resolved_ips=[],
                status="RESOLVE_FAIL",
                ok=False,
                reason="dns_no_answer",
            )

        best: Optional[Dict[str, Any]] = None
        for ip, fam in ips:
            # Policy: FQDN & CIDR gates
            f_ok, f_reason = fqdn_allowed(ep.host, policy)
            i_ok, i_reason = ip_allowed(ip, policy)
            status_note = f_reason or i_reason

            if not f_ok or not i_ok:
                results.append(
                    dict(
                        endpoint=ep.raw,
                        host=ep.host,
                        port=ep.port,
                        proto=ep.proto,
                        transport=ep.transport,
                        ip=ip,
                        ip_family=fam,
                        ok=False,
                        status="POLICY_BLOCK",
                        reason=status_note,
                    )
                )
                continue

            # Probe network
            if ep.proto == "tcp" and ep.port:
                ok, lat_ms, err = probe_tcp(ip, ep.port, timeout)
                entry = dict(
                    endpoint=ep.raw,
                    host=ep.host,
                    port=ep.port,
                    proto=ep.proto,
                    transport=ep.transport,
                    ip=ip,
                    ip_family=fam,
                    ok=ok,
                    status="TCP_OK" if ok else "TCP_FAIL",
                    latency_ms=round(lat_ms, 2),
                    error=err,
                )
            elif ep.proto == "udp" and ep.port:
                ok, lat_ms, err = probe_udp(ip, ep.port, timeout)
                entry = dict(
                    endpoint=ep.raw,
                    host=ep.host,
                    port=ep.port,
                    proto=ep.proto,
                    transport=ep.transport,
                    ip=ip,
                    ip_family=fam,
                    ok=ok,
                    status="UDP_SENT" if ok else "UDP_FAIL",
                    latency_ms=round(lat_ms, 2),
                    error=err,
                )
            else:
                entry = dict(
                    endpoint=ep.raw,
                    host=ep.host,
                    port=ep.port,
                    proto=ep.proto,
                    transport=ep.transport,
                    ip=ip,
                    ip_family=fam,
                    ok=False,
                    status="UNSUPPORTED",
                    reason="missing_port_or_proto",
                )

            # Pick the fastest successful entry, else keep the last failure for context
            if best is None:
                best = entry
            else:
                if entry.get("ok") and (
                    not best.get("ok")
                    or entry.get("latency_ms", 1e9) < best.get("latency_ms", 1e9)
                ):
                    best = entry
                elif not best.get("ok") and not entry.get("ok"):
                    best = entry  # keep the latest failure detail

        return (
            best
            if best is not None
            else dict(
                endpoint=ep.raw,
                host=ep.host,
                port=ep.port,
                proto=ep.proto,
                transport=ep.transport,
                ok=False,
                status="NO_IPS",
                reason="no_resolved_ips",
            )
        )

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=min(32, max(4, len(endpoints)))
    ) as pool:
        for entry in pool.map(work, endpoints):
            results.append(entry)
    return results


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Check seeds & produce reachability report"
    )
    ap.add_argument(
        "--seeds", type=Path, default=DEFAULT_SEEDS, help="Path to bootstrap_nodes.json"
    )
    ap.add_argument(
        "--dns", type=Path, default=DEFAULT_DNS, help="Path to dnsseeds.txt"
    )
    ap.add_argument(
        "--allowlist", type=Path, default=DEFAULT_ALLOW, help="Policy allowlist file"
    )
    ap.add_argument(
        "--blocklist", type=Path, default=DEFAULT_BLOCK, help="Policy blocklist file"
    )
    ap.add_argument(
        "--timeout", type=float, default=2.0, help="Per-connection timeout (seconds)"
    )
    ap.add_argument(
        "--min-ok", type=int, default=2, help="Minimum successful endpoints required"
    )
    ap.add_argument(
        "--outdir", type=Path, default=REPORT_DIR, help="Directory for JSON reports"
    )
    ap.add_argument(
        "--chain-id", type=str, default=None, help="Override chain_id in report"
    )
    args = ap.parse_args(argv)

    args.outdir.mkdir(parents=True, exist_ok=True)

    policy = load_policy(args.allowlist, args.blocklist)

    # Load bootstrap endpoints
    bootstrap = load_bootstrap_seeds(args.seeds)
    chain_id = args.chain_id or bootstrap.get("chain_id") or "unknown"
    generated_at = now_iso()

    endpoints: List[Endpoint] = []
    seed_records: List[Dict[str, Any]] = []

    # From bootstrap multiaddrs
    for seed in bootstrap.get("seeds", []):
        peer_id = seed.get("peer_id")
        region = seed.get("region")
        asn = seed.get("asn")
        per_seed_eps: List[Endpoint] = []
        for ma in seed.get("multiaddrs", []):
            ep = parse_multiaddr(ma)
            if ep:
                endpoints.append(ep)
                per_seed_eps.append(ep)
        seed_records.append(
            {
                "peer_id": peer_id,
                "region": region,
                "asn": asn,
                "multiaddr_count": len(per_seed_eps),
            }
        )

    # From DNS seeds (we treat these as /dns/<host>/udp/443/quic-v1 and /dns/<host>/tcp/30333 probes)
    dns_seeds = load_dnsseeds(args.dns)
    for host in dns_seeds:
        # Two probes (common defaults): UDP QUIC@443 and TCP@30333
        endpoints.append(
            Endpoint(
                raw=f"/dns/{host}/udp/443/quic-v1",
                host=host,
                port=443,
                proto="udp",
                transport="quic-v1",
                addr_type="dns",
            )
        )
        endpoints.append(
            Endpoint(
                raw=f"/dns/{host}/tcp/30333",
                host=host,
                port=30333,
                proto="tcp",
                transport=None,
                addr_type="dns",
            )
        )

    # Quick block checks on FQDN level
    fqdn_block_hits = [h for h in dns_seeds if not fqdn_allowed(h, policy)[0]]

    # Evaluate endpoints
    results = check_endpoints(endpoints, timeout=args.timeout, policy=policy)

    ok_tcp = [r for r in results if r.get("ok") and r.get("proto") == "tcp"]
    ok_udp = [r for r in results if r.get("ok") and r.get("proto") == "udp"]
    blocked = [r for r in results if r.get("status") == "POLICY_BLOCK"]

    # ASN-level checks (from bootstrap metadata only; we cannot resolve ASN in stdlib)
    asn_block_hits = []
    asn_allow_misses = []
    for s in bootstrap.get("seeds", []):
        asn = s.get("asn")
        a_ok, a_reason = asn_allowed(asn, policy)
        if not a_ok:
            if a_reason == "blocked_by_asn":
                asn_block_hits.append(s)
            else:
                asn_allow_misses.append(
                    {k: s.get(k) for k in ("peer_id", "asn", "region")}
                )

    # Compose report
    report = {
        "generated_at": generated_at,
        "chain_id": chain_id,
        "inputs": {
            "bootstrap_nodes": str(args.seeds),
            "dnsseeds": str(args.dns),
            "allowlist": str(args.allowlist),
            "blocklist": str(args.blocklist),
            "timeout_sec": args.timeout,
        },
        "summary": {
            "endpoints_total": len(endpoints),
            "tcp_ok": len(ok_tcp),
            "udp_sent": len(ok_udp),
            "policy_blocked": len(blocked),
            "fqdn_block_hits": fqdn_block_hits,
            "asn_block_hits": [
                {k: s.get(k) for k in ("peer_id", "asn", "region")}
                for s in asn_block_hits
            ],
            "asn_allow_misses": asn_allow_misses,
            "min_ok_required": args.min_ok,
            "ok_enough": (len(ok_tcp) >= args.min_ok),
        },
        "bootstrap_seeds": seed_records,
        "results": results,
    }

    # Write files
    ts_name = f"seed_report_{generated_at.replace(':','').replace('-','')}.json"
    out_ts = args.outdir / ts_name
    out_latest = args.outdir / "seed_report_latest.json"
    data = json.dumps(report, sort_keys=True, indent=2)
    atomic_write(out_ts, data)
    atomic_write(out_latest, data)

    # Pretty summary
    print(f"[Animica] Seed Health Report @ {generated_at}  chain={chain_id}")
    print(
        f"  Endpoints: {len(endpoints)} | TCP OK: {len(ok_tcp)} | UDP SENT: {len(ok_udp)} | Blocked: {len(blocked)}"
    )
    if fqdn_block_hits:
        print(f"  FQDN block hits: {', '.join(fqdn_block_hits)}")
    if asn_block_hits:
        short = ", ".join(
            [f"{s.get('peer_id')[:10]}…(asn={s.get('asn')})" for s in asn_block_hits]
        )
        print(f"  ASN block hits: {short}")
    if asn_allow_misses:
        print(
            f"  ASN allow misses: {len(asn_allow_misses)} (allowlist present but some seeds had unknown/disallowed ASN)"
        )

    # Exit codes
    if blocked or fqdn_block_hits or asn_block_hits:
        print("-> Blocklist violation detected.", file=sys.stderr)
        return 2
    if len(ok_tcp) < args.min_ok:
        print(
            f"-> Insufficient reachable TCP endpoints (need >= {args.min_ok}).",
            file=sys.stderr,
        )
        return 3
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        sys.exit(130)
