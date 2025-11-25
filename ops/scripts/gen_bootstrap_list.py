#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Animica Ops — gen_bootstrap_list.py
Discover live P2P nodes from one or more JSON-RPC endpoints and produce
a seeds/bootstrap_nodes.json file suitable for bootstrapping discovery.

It tries several candidate RPC methods to list peers (best-effort):
  - "p2p.listPeers", "p2p.getPeers", "p2p.peers", "admin_peers", "net_peers"

Each method may return either:
  - List[str] of endpoint addresses (e.g., "tcp://host:port", "ws://host:port/path")
  - List[dict] with fields like "multiaddr" | "address" | "addr" | "url" | ("host","port")
    and optional "peer_id" | "peerId" | "id".

Addresses are normalized to a compact form:
  - If a scheme is present (e.g., tcp://, ws://, quic://), kept as-is.
  - If "host:port" is provided, normalized to "tcp://host:port".
  - IPv6 literals are bracketed: "tcp://[2001:db8::1]:9000".

We optionally TCP-probe addresses to mark "reachable" (default on).

Usage:
  python ops/scripts/gen_bootstrap_list.py \
      --rpc http://localhost:8545/rpc \
      --rpc http://other-host:8545/rpc \
      --out seeds/bootstrap_nodes.json

Env defaults (used if no --rpc provided):
  RPC_HTTP_URL=http://localhost:8545/rpc

Exit codes:
  0 on success (file written),
  2 on argument/config error,
  3 if no peers could be discovered from the provided sources.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union
from urllib.error import URLError, HTTPError

CANDIDATE_METHODS: Tuple[str, ...] = (
    "p2p.listPeers",
    "p2p.getPeers",
    "p2p.peers",
    "admin_peers",
    "net_peers",
)

JSONRPC_ID = 1


@dataclass
class PeerAddr:
    address: str
    peer_id: Optional[str] = None
    reachable: Optional[bool] = None


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _log(msg: str, *, verbose: bool = False) -> None:
    if verbose:
        print(msg, file=sys.stderr)


def _jsonrpc(url: str, method: str, params: Optional[list] = None, timeout: float = 4.0) -> Any:
    body = {
        "jsonrpc": "2.0",
        "id": JSONRPC_ID,
        "method": method,
        "params": params or [],
    }
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url=url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if "error" in payload and payload["error"]:
        raise RuntimeError(f"JSON-RPC error from {url} {method}: {payload['error']}")
    return payload.get("result")


def _discover_chain_id(rpc_url: str, timeout: float, verbose: bool) -> Optional[int]:
    try:
        res = _jsonrpc(rpc_url, "chain.getChainId", [], timeout=timeout)
        if isinstance(res, int):
            return res
        # sometimes a string; try int()
        if isinstance(res, str) and res.isdigit():
            return int(res)
    except Exception as exc:
        _log(f"[warn] chainId probe failed on {rpc_url}: {exc}", verbose=verbose)
    return None


def _extract_addr_from_obj(obj: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """
    Return (address, peer_id) from a peer descriptor dict.
    """
    # try common fields for address-like info
    for key in ("multiaddr", "address", "addr", "url", "endpoint"):
        val = obj.get(key)
        if isinstance(val, str) and val:
            return val.strip(), _extract_peer_id(obj)

    # host/port form
    host = obj.get("host") or obj.get("ip")
    port = obj.get("port") or obj.get("p2p_port") or obj.get("tcp_port")
    if isinstance(host, str) and (isinstance(port, int) or (isinstance(port, str) and port.isdigit())):
        return f"{host}:{int(port)}", _extract_peer_id(obj)

    return None, _extract_peer_id(obj)


def _extract_peer_id(obj: Dict[str, Any]) -> Optional[str]:
    for key in ("peer_id", "peerId", "id", "node_id", "nodeId"):
        val = obj.get(key)
        if isinstance(val, str) and val:
            return val
    return None


def _has_scheme(addr: str) -> bool:
    return "://" in addr


def _bracket_ipv6(host: str) -> str:
    # add brackets only if it's an IPv6 literal (contains ':', not already bracketed)
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host


def _normalize_addr(raw: str) -> str:
    s = raw.strip()
    if not s:
        return s
    if _has_scheme(s):
        return s  # keep as-is
    # assume host:port
    if s.count(":") >= 1:
        # could be IPv6 with colons; split from right on the last colon
        host, port = s.rsplit(":", 1)
        host = _bracket_ipv6(host)
        return f"tcp://{host}:{int(port)}"
    # single token host -> no port; leave as-is (unlikely)
    return s


def _tcp_probe(addr: str, timeout: float) -> bool:
    """
    Try to establish a TCP connection for tcp://host:port (or host:port).
    For ws/wss/http/https/quic schemes we try TCP to their default/explicit port.
    """
    try:
        target = addr
        scheme = None
        host = None
        port = None

        if "://" in target:
            scheme, rest = target.split("://", 1)
            hostport = rest.split("/", 1)[0]
        else:
            hostport = target

        # Extract host and port (handle IPv6 brackets)
        if hostport.startswith("["):
            # [v6]:port
            host, port_str = hostport.split("]:", 1)
            host = host[1:]
            port = int(port_str)
        elif ":" in hostport:
            host, port_str = hostport.rsplit(":", 1)
            port = int(port_str)
        else:
            # No port: infer from scheme
            host = hostport
            defaults = {"http": 80, "ws": 80, "https": 443, "wss": 443, "quic": 443, "tcp": 0}
            port = defaults.get(scheme or "tcp", 0)

        if port <= 0:
            return False

        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def discover_peers_from_rpc(rpc_url: str, *, timeout: float, verbose: bool) -> List[PeerAddr]:
    peers: List[PeerAddr] = []
    last_err: Optional[Exception] = None
    for method in CANDIDATE_METHODS:
        try:
            res = _jsonrpc(rpc_url, method, [], timeout=timeout)
        except Exception as exc:
            last_err = exc
            _log(f"[debug] {rpc_url} {method} failed: {exc}", verbose=verbose)
            continue

        # Parse result
        if isinstance(res, list):
            for item in res:
                if isinstance(item, str):
                    peers.append(PeerAddr(address=_normalize_addr(item)))
                elif isinstance(item, dict):
                    addr_raw, pid = _extract_addr_from_obj(item)
                    if addr_raw:
                        peers.append(PeerAddr(address=_normalize_addr(addr_raw), peer_id=pid))
        elif isinstance(res, dict):
            # sometimes wrapped like {"peers": [...]}
            for key in ("peers", "result", "list"):
                inner = res.get(key)
                if isinstance(inner, list):
                    for item in inner:
                        if isinstance(item, str):
                            peers.append(PeerAddr(address=_normalize_addr(item)))
                        elif isinstance(item, dict):
                            addr_raw, pid = _extract_addr_from_obj(item)
                            if addr_raw:
                                peers.append(PeerAddr(address=_normalize_addr(addr_raw), peer_id=pid))
        # if we managed to parse anything, stop trying further methods
        if peers:
            _log(f"[info] {rpc_url} {method} → {len(peers)} peers", verbose=verbose)
            break

    if not peers and last_err:
        _log(f"[warn] No peers discovered from {rpc_url}: last error: {last_err}", verbose=verbose)
    return peers


def unique_by_address(peers: Iterable[PeerAddr]) -> List[PeerAddr]:
    seen: Dict[str, PeerAddr] = {}
    for p in peers:
        if not p.address:
            continue
        # prefer preserving peer_id if any
        if p.address not in seen or (p.peer_id and not seen[p.address].peer_id):
            seen[p.address] = p
    return list(seen.values())


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate seeds/bootstrap_nodes.json from live RPC nodes.")
    ap.add_argument("--rpc", "-r", action="append", help="JSON-RPC URL (repeatable)")
    ap.add_argument("--from-file", type=str, help="Path to file with RPC URLs (one per line)")
    ap.add_argument("--out", "-o", type=str, default="seeds/bootstrap_nodes.json", help="Output JSON path")
    ap.add_argument("--timeout", type=float, default=4.0, help="Per-request timeout in seconds")
    ap.add_argument("--probe-timeout", type=float, default=1.0, help="TCP probe timeout per node in seconds")
    ap.add_argument("--expected-chain-id", type=int, default=None, help="Fail if discovered chainId mismatches")
    ap.add_argument("--drop-unreachable", action="store_true", help="Drop nodes that fail TCP probe")
    ap.add_argument("--no-probe", action="store_true", help="Skip TCP reachability probes")
    ap.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")
    args = ap.parse_args()

    rpc_urls: List[str] = []
    if args.rpc:
        rpc_urls.extend(args.rpc)
    if args.from_file:
        try:
            with open(args.from_file, "r", encoding="utf-8") as fh:
                for line in fh:
                    s = line.strip()
                    if not s or s.startswith("#"):
                        continue
                    rpc_urls.append(s)
        except OSError as exc:
            print(f"ERROR: could not read --from-file {args.from_file}: {exc}", file=sys.stderr)
            return 2
    if not rpc_urls:
        env_rpc = os.getenv("RPC_HTTP_URL")
        if env_rpc:
            rpc_urls.append(env_rpc)

    if not rpc_urls:
        print("ERROR: no RPC URLs provided (use --rpc or RPC_HTTP_URL).", file=sys.stderr)
        return 2

    # Discover chain id (best-effort) and check consistency
    discovered_chain_ids: List[int] = []
    for url in rpc_urls:
        cid = _discover_chain_id(url, timeout=args.timeout, verbose=args.verbose)
        if cid is not None:
            discovered_chain_ids.append(cid)

    chain_id: Optional[int] = None
    if discovered_chain_ids:
        # prefer the majority value
        from collections import Counter

        counts = Counter(discovered_chain_ids)
        chain_id, _ = counts.most_common(1)[0]
        if args.verbose:
            print(f"[info] chainId votes: {dict(counts)}; selected={chain_id}", file=sys.stderr)

    if args.expected_chain_id is not None and chain_id is not None and chain_id != args.expected_chain_id:
        print(
            f"ERROR: expected chainId {args.expected_chain_id} but discovered {chain_id}",
            file=sys.stderr,
        )
        return 2

    # Discover peers
    all_peers: List[PeerAddr] = []
    for url in rpc_urls:
        peers = discover_peers_from_rpc(url, timeout=args.timeout, verbose=args.verbose)
        all_peers.extend(peers)

    uniq = unique_by_address(all_peers)

    if not uniq:
        print("ERROR: discovered 0 peers from provided RPC URLs.", file=sys.stderr)
        return 3

    # Probe reachability (best-effort)
    if not args.no_probe:
        for p in uniq:
            # only probe tcp://* or host:port; for others, try to TCP to explicit/default port
            p.reachable = _tcp_probe(p.address, timeout=args.probe_timeout)

        if args.drop_unreachable:
            before = len(uniq)
            uniq = [p for p in uniq if p.reachable]
            if args.verbose:
                print(f"[info] dropped {before - len(uniq)} unreachable nodes", file=sys.stderr)

    # Assemble output doc
    out_doc: Dict[str, Any] = {
        "generated_at": _iso_now(),
        "sources": rpc_urls,
        "chain_id": chain_id,
        "nodes": [
            {"address": p.address, **({"peer_id": p.peer_id} if p.peer_id else {}), **({"reachable": p.reachable} if p.reachable is not None else {})}
            for p in sorted(uniq, key=lambda x: x.address)
        ],
    }

    # Ensure dir exists
    out_path = args.out
    out_dir = os.path.dirname(out_path) or "."
    os.makedirs(out_dir, exist_ok=True)

    # Write
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(out_doc, fh, indent=2, sort_keys=False)
        fh.write("\n")

    # Summary
    total = len(out_doc["nodes"])
    reachable = sum(1 for n in out_doc["nodes"] if n.get("reachable") is True)
    print(
        f"Wrote {total} node(s) to {out_path} "
        + (f"(reachable: {reachable}) " if not args.no_probe else "")
        + (f"for chainId={chain_id}" if chain_id is not None else "(chainId unknown)")
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
