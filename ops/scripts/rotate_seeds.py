#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Animica Ops — rotate_seeds.py
Prune bad seeds, de-duplicate, (optionally re-probe), and sort deterministically.

Typical use (in-place):
  python ops/scripts/rotate_seeds.py \
      --in seeds/bootstrap_nodes.json --in-place --backup

You can also merge several sources (JSON with {"nodes":[...]}, or plain files
with one address per line). Addresses are normalized so that "host:port" becomes
"tcp://host:port" (IPv6 bracketed). We keep the "best" record per address
(preferring reachable=True and entries with peer_id). Sorting groups by:
  reachable desc → scheme priority → host → port → peer_id.

Exit codes:
  0 success, 2 argument/config error, 3 no valid nodes after pruning.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import socket
import sys
import tempfile
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlsplit

ALLOWED_SCHEMES = ("quic", "tcp", "wss", "ws", "https", "http")

SCHEME_PRIORITY = {
    "quic": 0,
    "tcp": 1,
    "wss": 2,
    "ws": 3,
    "https": 4,
    "http": 5,
    # unknown => 9
}


@dataclass
class Node:
    address: str
    peer_id: Optional[str] = None
    reachable: Optional[bool] = None


def _log(msg: str, *, verbose: bool = False) -> None:
    if verbose:
        print(msg, file=sys.stderr)


def _bracket_ipv6(host: str) -> str:
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host


def _has_scheme(addr: str) -> bool:
    return "://" in addr


def normalize_address(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return s
    if _has_scheme(s):
        return s
    # assume host:port (IPv6 supported)
    if ":" in s:
        host, port = s.rsplit(":", 1)
        host = _bracket_ipv6(host)
        try:
            port_i = int(port)
        except ValueError:
            return ""  # invalid
        return f"tcp://{host}:{port_i}"
    return s  # single token (unlikely/use-less)


def parse_addr(addr: str) -> Tuple[str, str, int]:
    """
    Return (scheme, host, port). Raises ValueError if malformed.
    """
    if not addr:
        raise ValueError("empty address")
    if not _has_scheme(addr):
        addr = normalize_address(addr)
    parts = urlsplit(addr)
    scheme = parts.scheme or "tcp"
    if scheme not in ALLOWED_SCHEMES:
        raise ValueError(f"disallowed scheme: {scheme}")
    hostport = parts.netloc or parts.path  # handle tcp://host:port (no explicit path)
    if not hostport:
        raise ValueError("missing host:port")
    host: str
    port_s: str
    if hostport.startswith("["):  # [::1]:9000
        if "]:" not in hostport:
            raise ValueError("malformed IPv6 host:port")
        host = hostport[1:].split("]", 1)[0]
        port_s = hostport.split("]:", 1)[1]
    else:
        if ":" not in hostport:
            raise ValueError("missing :port")
        host, port_s = hostport.rsplit(":", 1)
    port = int(port_s)
    if port <= 0 or port > 65535:
        raise ValueError("port out of range")
    return scheme, host, port


def tcp_probe(addr: str, timeout: float) -> bool:
    """
    Best-effort reachability: attempt TCP connect to the derived host:port.
    """
    try:
        scheme, host, port = parse_addr(addr)
        # For http/ws/https/wss/quic we still attempt TCP to the declared port.
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def load_nodes_from_json(path: str, *, verbose: bool) -> List[Node]:
    with open(path, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    # Accept a straight list of strings or dicts as well
    if isinstance(doc, dict) and "nodes" in doc and isinstance(doc["nodes"], list):
        items = doc["nodes"]
    elif isinstance(doc, list):
        items = doc
    else:
        raise ValueError(
            "Unrecognized JSON structure (expecting {'nodes': [...]} or a list)"
        )
    out: List[Node] = []
    for it in items:
        if isinstance(it, str):
            out.append(Node(address=normalize_address(it)))
        elif isinstance(it, dict):
            addr = normalize_address(str(it.get("address", "")))
            if not addr:
                continue
            pid = it.get("peer_id") or it.get("peerId") or it.get("id")
            reach = it.get("reachable")
            reach_b = bool(reach) if isinstance(reach, bool) else None
            out.append(
                Node(address=addr, peer_id=str(pid) if pid else None, reachable=reach_b)
            )
    _log(f"[info] loaded {len(out)} node(s) from {path}", verbose=verbose)
    return out


def load_nodes_from_plain(path: str, *, verbose: bool) -> List[Node]:
    out: List[Node] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            out.append(Node(address=normalize_address(s)))
    _log(f"[info] loaded {len(out)} node(s) (plain) from {path}", verbose=verbose)
    return out


def load_any(path: str, *, verbose: bool) -> List[Node]:
    try:
        return load_nodes_from_json(path, verbose=verbose)
    except Exception:
        return load_nodes_from_plain(path, verbose=verbose)


def merge_dedupe(nodes: Iterable[Node]) -> List[Node]:
    """
    Deduplicate by normalized address. Prefer entries that:
      1) have reachable == True,
      2) have a peer_id,
      3) otherwise keep the first seen.
    """
    best: Dict[str, Node] = {}
    for n in nodes:
        if not n.address:
            continue
        cur = best.get(n.address)
        if cur is None:
            best[n.address] = n
            continue
        # Decide if n is better than cur
        cur_score = (cur.reachable is True, bool(cur.peer_id))
        n_score = (n.reachable is True, bool(n.peer_id))
        if n_score > cur_score:
            best[n.address] = n
    return list(best.values())


def prune_and_validate(
    nodes: Iterable[Node],
    *,
    drop_unreachable: bool,
    reprobe: bool,
    probe_timeout: float,
) -> List[Node]:
    out: List[Node] = []
    for n in nodes:
        # Validate/normalize; skip malformed or disallowed schemes
        try:
            # parse to validate scheme/host/port
            parse_addr(n.address)
        except Exception:
            continue

        # Optionally re-probe to refresh reachability
        if reprobe:
            n.reachable = tcp_probe(n.address, timeout=probe_timeout)

        if drop_unreachable and (n.reachable is False or n.reachable is None):
            # If we demand reachability, only keep nodes proven True
            continue

        out.append(n)
    return out


def sort_nodes(nodes: Iterable[Node]) -> List[Node]:
    def key(n: Node) -> Tuple[int, int, str, int, str]:
        try:
            scheme, host, port = parse_addr(n.address)
        except Exception:
            # shove unknowns to the end
            scheme, host, port = ("zzz", n.address, 0)
        reach_rank = 0 if n.reachable is True else 1
        scheme_rank = SCHEME_PRIORITY.get(scheme, 9)
        pid = n.peer_id or ""
        return (reach_rank, scheme_rank, host, port, pid)

    return sorted(nodes, key=key)


def atomic_write_json(path: str, payload: Dict[str, Any]) -> None:
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=d, delete=False) as tmp:
        json.dump(payload, tmp, indent=2)
        tmp.write("\n")
        tmp_path = tmp.name
    os.replace(tmp_path, path)


def main() -> int:
    ap = argparse.ArgumentParser(description="Prune, dedupe, and sort seed nodes.")
    ap.add_argument(
        "--in",
        dest="inputs",
        action="append",
        help="Input file (repeatable). JSON or plain list.",
    )
    ap.add_argument(
        "--out",
        dest="out",
        type=str,
        help="Output JSON (default: in-place or seeds/bootstrap_nodes.json)",
    )
    ap.add_argument(
        "--in-place",
        action="store_true",
        help="Write back to the first --in path (atomic).",
    )
    ap.add_argument(
        "--backup", action="store_true", help="If in-place, write a .bak copy first."
    )
    ap.add_argument(
        "--drop-unreachable",
        action="store_true",
        help="Drop nodes that are not reachable.",
    )
    ap.add_argument(
        "--reprobe", action="store_true", help="Re-check reachability with TCP probes."
    )
    ap.add_argument(
        "--probe-timeout",
        type=float,
        default=1.0,
        help="Probe timeout seconds (default: 1.0).",
    )
    ap.add_argument(
        "--keep", type=int, default=None, help="Cap the list to N nodes after sorting."
    )
    ap.add_argument(
        "--shuffle-before-sort",
        action="store_true",
        help="Shuffle to reduce bias before dedupe/sort.",
    )
    ap.add_argument(
        "--verbose", "-v", action="true", default=False, help=argparse.SUPPRESS
    )
    args = ap.parse_args()

    inputs: List[str] = args.inputs or ["seeds/bootstrap_nodes.json"]
    out_path: Optional[str] = None
    if args.in_place:
        out_path = inputs[0]
    else:
        out_path = args.out or "seeds/bootstrap_nodes.json"

    all_nodes: List[Node] = []
    for p in inputs:
        if not os.path.exists(p):
            print(f"ERROR: input not found: {p}", file=sys.stderr)
            return 2
        try:
            nodes = load_any(p, verbose=getattr(args, "verbose", False))
            all_nodes.extend(nodes)
        except Exception as exc:
            print(f"ERROR: failed to read {p}: {exc}", file=sys.stderr)
            return 2

    if args.shuffle_before_sort:
        random.shuffle(all_nodes)

    # Normalize addresses one more time, drop empties
    all_nodes = [
        Node(
            address=normalize_address(n.address),
            peer_id=n.peer_id,
            reachable=n.reachable,
        )
        for n in all_nodes
        if n.address
    ]

    # Validate/prune and optionally reprobe
    cleaned = prune_and_validate(
        all_nodes,
        drop_unreachable=args.drop_unreachable,
        reprobe=args.reprobe,
        probe_timeout=args.probe_timeout,
    )

    # Deduplicate (prefer reachable + peer_id)
    deduped = merge_dedupe(cleaned)

    # Sort deterministically
    ordered = sort_nodes(deduped)

    # Cap the list if requested
    if args.keep is not None and args.keep >= 0:
        ordered = ordered[: args.keep]

    if not ordered:
        print("ERROR: 0 nodes remain after pruning.", file=sys.stderr)
        return 3

    out_doc: Dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "nodes": [
            {
                "address": n.address,
                **({"peer_id": n.peer_id} if n.peer_id else {}),
                **({"reachable": n.reachable} if n.reachable is not None else {}),
            }
            for n in ordered
        ],
    }

    # Backup if requested and path exists
    if args.in_place and args.backup and os.path.exists(out_path):
        ts = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
        backup_path = f"{out_path}.{ts}.bak"
        shutil.copy2(out_path, backup_path)

    atomic_write_json(out_path, out_doc)

    total = len(out_doc["nodes"])
    reachable = sum(1 for n in out_doc["nodes"] if n.get("reachable") is True)
    print(
        f"Rotated seeds → {out_path} (nodes={total}"
        + (
            f", reachable={reachable}"
            if args.reprobe
            or any(n.get("reachable") is not None for n in out_doc["nodes"])
            else ""
        )
        + ")"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
