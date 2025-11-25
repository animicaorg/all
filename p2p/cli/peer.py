#!/usr/bin/env python3
"""
Animica P2P CLI — peer management
=================================

Manage peer addresses, scores, and bans; optionally probe connectivity.

This tool operates directly on the peer store. If the full P2P stack is installed,
it will use `p2p.peer.peerstore.PeerStore`. If not, it falls back to a simple JSON
store so you can still curate bootstrap lists offline.

Examples
--------
# List peers
python -m p2p.cli.peer list

# Add a peer
python -m p2p.cli.peer add /ip4/203.0.113.10/tcp/42069

# Connect probe (TCP open) to a multiaddr
python -m p2p.cli.peer connect /ip4/127.0.0.1/tcp/42069 --probe

# Ban for 1h
python -m p2p.cli.peer ban peer12ab... --for 1h

# Export/import
python -m p2p.cli.peer export peers.json
python -m p2p.cli.peer import peers.json
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import ipaddress
import json
import os
import re
import socket
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

DEFAULT_HOME = Path(os.environ.get("ANIMICA_HOME", Path.home() / ".animica"))
DEFAULT_STORE = DEFAULT_HOME / "p2p" / "peers.json"

# ---- Optional imports from full stack -------------------------------------------------
# We do imports lazily and guard everything with fallbacks so this CLI works standalone.
try:  # peer store & types
    from p2p.peer.peerstore import PeerStore as _PeerStore  # type: ignore
except Exception:  # pragma: no cover - environment-dependent
    _PeerStore = None  # type: ignore[assignment]

try:  # multiaddr helpers
    from p2p.transport.multiaddr import parse_multiaddr as _parse_multiaddr  # type: ignore
except Exception:  # pragma: no cover
    _parse_multiaddr = None  # type: ignore[assignment]

# --------------------------------------------------------------------------------------


def _ensure_dirs(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _now() -> float:
    return time.time()


def _fmt_ts(ts: Optional[float]) -> str:
    if not ts:
        return "-"
    return dt.datetime.fromtimestamp(ts).isoformat(timespec="seconds")


def _fmt_dur(seconds: Optional[float]) -> str:
    if not seconds:
        return "-"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    d, h = divmod(h, 24)
    parts = []
    if d:
        parts.append(f"{d}d")
    if h:
        parts.append(f"{h}h")
    if m:
        parts.append(f"{m}m")
    if s or not parts:
        parts.append(f"{s}s")
    return "".join(parts)


_DUR_RX = re.compile(r"^\s*(\d+)\s*([smhdw]?)\s*$", re.I)


def parse_duration(expr: str) -> int:
    """
    Parse '30s', '15m', '2h', '1d', '1w' → seconds.
    """
    m = _DUR_RX.match(expr)
    if not m:
        raise ValueError(f"Bad duration: {expr!r}")
    n = int(m.group(1))
    unit = (m.group(2) or "s").lower()
    mult = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[unit]
    return n * mult


# =========================
# Fallback JSON Peer Store
# =========================

@dataclasses.dataclass
class JPeer:
    peer_id: str
    addrs: List[str]
    score: float = 0.0
    last_seen: Optional[float] = None
    connected: bool = False
    banned_until: Optional[float] = None
    tags: Dict[str, Any] = dataclasses.field(default_factory=dict)


class JsonPeerStore:
    """
    Minimal peer store used as a fallback when the full p2p.peer.peerstore is not present.
    The on-disk format is a JSON dict: { "peers": [ ... ] }
    """
    def __init__(self, path: Path):
        self.path = path
        self._peers: Dict[str, JPeer] = {}
        if path.exists():
            self._load()

    # CRUD
    def upsert(self, peer: JPeer) -> None:
        self._peers[peer.peer_id] = peer
        self._save()

    def ensure_addr(self, peer_id: str, addr: str) -> None:
        p = self._peers.get(peer_id)
        if not p:
            p = JPeer(peer_id=peer_id, addrs=[addr])
        else:
            if addr not in p.addrs:
                p.addrs.append(addr)
        self._peers[peer_id] = p
        self._save()

    def remove(self, peer_id: str) -> bool:
        existed = peer_id in self._peers
        self._peers.pop(peer_id, None)
        self._save()
        return existed

    def list(self) -> List[JPeer]:
        return list(self._peers.values())

    def get(self, peer_id: str) -> Optional[JPeer]:
        return self._peers.get(peer_id)

    def find_by_addr(self, addr: str) -> Optional[JPeer]:
        for p in self._peers.values():
            if addr in p.addrs:
                return p
        return None

    # Ban / scores
    def ban(self, peer_id: str, until_ts: float) -> None:
        p = self._peers.get(peer_id)
        if not p:
            p = JPeer(peer_id=peer_id, addrs=[])
        p.banned_until = until_ts
        self._peers[peer_id] = p
        self._save()

    def unban(self, peer_id: str) -> None:
        p = self._peers.get(peer_id)
        if p:
            p.banned_until = None
            self._save()

    def set_score(self, peer_id: str, score: float) -> None:
        p = self._peers.get(peer_id)
        if not p:
            p = JPeer(peer_id=peer_id, addrs=[])
        p.score = score
        self._peers[peer_id] = p
        self._save()

    # Import/Export
    def export_file(self, dest: Path) -> None:
        _ensure_dirs(dest)
        with dest.open("w", encoding="utf-8") as f:
            json.dump({"peers": [dataclasses.asdict(p) for p in self._peers.values()]}, f, indent=2)

    def import_file(self, src: Path, merge: bool) -> None:
        with src.open("r", encoding="utf-8") as f:
            data = json.load(f)
        incoming = {p["peer_id"]: JPeer(**p) for p in data.get("peers", [])}
        if merge:
            self._peers.update(incoming)
        else:
            self._peers = incoming
        self._save()

    # I/O
    def _load(self) -> None:
        try:
            with self.path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
        self._peers = {p["peer_id"]: JPeer(**p) for p in data.get("peers", [])}

    def _save(self) -> None:
        _ensure_dirs(self.path)
        with self.path.open("w", encoding="utf-8") as f:
            json.dump({"peers": [dataclasses.asdict(p) for p in self._peers.values()]}, f, indent=2)


# =========================
# Compatibility Facade
# =========================

class StoreFacade:
    """
    Thin facade to unify full PeerStore vs JsonPeerStore.
    """
    def __init__(self, path: Path):
        self.path = path
        self._impl = None
        if _PeerStore is not None:
            try:
                self._impl = _PeerStore.open(str(path))  # type: ignore[attr-defined]
            except Exception:
                self._impl = None
        if self._impl is None:
            self._impl = JsonPeerStore(path)

    # Dispatch methods with graceful fallbacks
    def list(self) -> List[Dict[str, Any]]:
        if isinstance(self._impl, JsonPeerStore):
            return [dataclasses.asdict(p) for p in self._impl.list()]
        # Expected API from full store: .list() -> iterable of dict-like
        return list(self._impl.list())  # type: ignore[no-any-return]

    def upsert(self, peer_id: str, addr: Optional[str] = None) -> None:
        if isinstance(self._impl, JsonPeerStore):
            if addr:
                self._impl.ensure_addr(peer_id, addr)
            else:
                self._impl.upsert(JPeer(peer_id=peer_id, addrs=[]))
            return
        self._impl.upsert(peer_id=peer_id, addr=addr)  # type: ignore[attr-defined]

    def ensure_addr(self, peer_id: str, addr: str) -> None:
        if isinstance(self._impl, JsonPeerStore):
            self._impl.ensure_addr(peer_id, addr)
            return
        self._impl.ensure_addr(peer_id=peer_id, addr=addr)  # type: ignore[attr-defined]

    def remove(self, peer_id: str) -> bool:
        if isinstance(self._impl, JsonPeerStore):
            return self._impl.remove(peer_id)
        return bool(self._impl.remove(peer_id=peer_id))  # type: ignore[attr-defined]

    def ban(self, peer_id: str, seconds: int) -> None:
        until_ts = _now() + seconds
        if isinstance(self._impl, JsonPeerStore):
            self._impl.ban(peer_id, until_ts)
            return
        self._impl.ban(peer_id=peer_id, until_ts=until_ts)  # type: ignore[attr-defined]

    def unban(self, peer_id: str) -> None:
        if isinstance(self._impl, JsonPeerStore):
            self._impl.unban(peer_id)
            return
        self._impl.unban(peer_id=peer_id)  # type: ignore[attr-defined]

    def set_score(self, peer_id: str, score: float) -> None:
        if isinstance(self._impl, JsonPeerStore):
            self._impl.set_score(peer_id, score)
            return
        self._impl.set_score(peer_id=peer_id, score=score)  # type: ignore[attr-defined]

    def export_file(self, dest: Path) -> None:
        if isinstance(self._impl, JsonPeerStore):
            self._impl.export_file(dest)
            return
        self._impl.export_file(str(dest))  # type: ignore[attr-defined]

    def import_file(self, src: Path, merge: bool) -> None:
        if isinstance(self._impl, JsonPeerStore):
            self._impl.import_file(src, merge)
            return
        self._impl.import_file(str(src), merge=merge)  # type: ignore[attr-defined]


# =========================
# Multiaddr parsing (fallback)
# =========================

def _fallback_parse_multiaddr(s: str) -> Tuple[str, int]:
    """
    Parse very small subset: /ip4/<ip>/tcp/<port>  OR host:port
    Returns (host, port)
    """
    if s.startswith("/"):
        parts = [p for p in s.split("/") if p]
        # Expect: ip4, <ip>, tcp, <port>
        if len(parts) >= 4 and parts[0] in ("ip4", "dns", "dns4") and parts[2] == "tcp":
            host = parts[1]
            port = int(parts[3])
            return host, port
        if len(parts) >= 4 and parts[0] == "ip6" and parts[2] == "tcp":
            host = parts[1]
            port = int(parts[3])
            return host, port
        raise ValueError(f"Unsupported multiaddr (fallback parser): {s}")
    # host:port
    if ":" in s:
        host, port = s.rsplit(":", 1)
        return host, int(port)
    raise ValueError(f"Cannot parse address: {s}")


def parse_addr(s: str) -> Tuple[str, int]:
    if _parse_multiaddr is not None:
        host, port = _parse_multiaddr(s)  # type: ignore[misc]
        return host, int(port)
    return _fallback_parse_multiaddr(s)


# =========================
# Networking probes
# =========================

def tcp_probe(addr: str, timeout: float = 2.5) -> Tuple[bool, Optional[float], Optional[str]]:
    """
    Best-effort TCP connect() probe. Returns (ok, rtt_seconds, error_message).
    """
    try:
        host, port = parse_addr(addr)
        # Validate host an IP or DNS
        try:
            ipaddress.ip_address(host)
        except ValueError:
            # DNS resolve first
            host = socket.gethostbyname(host)
        t0 = time.perf_counter()
        with socket.create_connection((host, port), timeout=timeout):
            rtt = time.perf_counter() - t0
            return True, rtt, None
    except Exception as e:
        return False, None, str(e)


# =========================
# CLI
# =========================

def add_common_store_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--store",
        type=Path,
        default=DEFAULT_STORE,
        help=f"Path to peer store (default: {DEFAULT_STORE})",
    )


def cmd_list(args: argparse.Namespace) -> int:
    store = StoreFacade(args.store)
    peers = store.list()
    if not peers:
        print("(no peers)")
        return 0

    # Pretty table
    headers = ["PEER_ID", "BANNED_UNTIL", "SCORE", "LAST_SEEN", "CONNECTED", "ADDRS"]
    rows = []
    for p in peers:
        banned_until = p.get("banned_until") or p.get("ban_until")  # tolerate different field names
        last_seen = p.get("last_seen")
        rows.append([
            p.get("peer_id", "?")[:20] + ("…" if len(p.get("peer_id","")) > 20 else ""),
            _fmt_ts(banned_until),
            f'{p.get("score", 0.0):.2f}',
            _fmt_ts(last_seen),
            "Y" if p.get("connected") else "-",
            ", ".join(p.get("addrs", [])),
        ])

    colw = [max(len(h), *(len(str(row[i])) for row in rows)) for i, h in enumerate(headers)]
    fmt = "  ".join("{:<" + str(w) + "}" for w in colw)
    print(fmt.format(*headers))
    print(fmt.format(*["-" * w for w in colw]))
    for r in rows:
        print(fmt.format(*r))
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    store = StoreFacade(args.store)
    peer_id = args.peer_id
    addr = args.addr
    store.ensure_addr(peer_id, addr)
    print(f"[+] ensured addr for {peer_id}: {addr}")
    if args.probe:
        ok, rtt, err = tcp_probe(addr, timeout=args.timeout)
        if ok:
            print(f"[probe] TCP connect OK in {_fmt_dur(rtt)}")
        else:
            print(f"[probe] FAILED: {err}")
            return 2
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    store = StoreFacade(args.store)
    if store.remove(args.peer_id):
        print(f"[-] removed {args.peer_id}")
        return 0
    print(f"[!] peer not found: {args.peer_id}")
    return 1


def cmd_ban(args: argparse.Namespace) -> int:
    store = StoreFacade(args.store)
    seconds = parse_duration(args.duration)
    store.ban(args.peer_id, seconds)
    print(f"[!] banned {args.peer_id} for {args.duration} ({seconds}s)")
    return 0


def cmd_unban(args: argparse.Namespace) -> int:
    store = StoreFacade(args.store)
    store.unban(args.peer_id)
    print(f"[+] unbanned {args.peer_id}")
    return 0


def cmd_score(args: argparse.Namespace) -> int:
    store = StoreFacade(args.store)
    store.set_score(args.peer_id, float(args.score))
    print(f"[+] set score for {args.peer_id} to {args.score}")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    store = StoreFacade(args.store)
    dest = Path(args.path)
    store.export_file(dest)
    print(f"[+] exported peers → {dest}")
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    store = StoreFacade(args.store)
    src = Path(args.path)
    store.import_file(src, merge=not args.replace)
    print(f"[+] imported peers from {src} ({'merge' if not args.replace else 'replace'})")
    return 0


def cmd_connect(args: argparse.Namespace) -> int:
    """
    When --probe is given, perform a TCP probe now.
    Otherwise, this simply ensures the addr is recorded; the running node can pick it up.
    """
    store = StoreFacade(args.store)
    peer_id = args.peer_id or f"peer:{args.addr}"
    store.ensure_addr(peer_id, args.addr)
    print(f"[+] recorded desired connection: {peer_id} @ {args.addr}")
    if args.probe:
        ok, rtt, err = tcp_probe(args.addr, timeout=args.timeout)
        if ok:
            print(f"[probe] TCP connect OK in {_fmt_dur(rtt)}")
            return 0
        print(f"[probe] FAILED: {err}")
        return 2
    return 0


def cmd_disconnect(args: argparse.Namespace) -> int:
    """
    Without a running node control plane, we can't force-drop sockets.
    We mark the peer as 'banned for 60s' as a hint to connection managers that read this store.
    """
    store = StoreFacade(args.store)
    store.ban(args.peer_id, 60)
    print(f"[~] hinted disconnect by temporary ban (60s) for {args.peer_id}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    store = StoreFacade(args.store)
    peers = store.list()
    target = None
    for p in peers:
        if p.get("peer_id") == args.peer_id:
            target = p
            break
    if not target:
        print(f"[!] peer not found: {args.peer_id}")
        return 1
    print(json.dumps(target, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="animica-p2p peer", add_help=True)
    sub = p.add_subparsers(dest="cmd", metavar="<cmd>")

    # list
    sp = sub.add_parser("list", help="List known peers")
    add_common_store_arg(sp)
    sp.set_defaults(func=cmd_list)

    # add
    sp = sub.add_parser("add", help="Add or update a peer address")
    add_common_store_arg(sp)
    sp.add_argument("peer_id", help="Peer ID (e.g., peer12abc...)")
    sp.add_argument("addr", help="Multiaddr or host:port (e.g., /ip4/1.2.3.4/tcp/42069)")
    sp.add_argument("--probe", action="bloom_false", help=argparse.SUPPRESS)  # keep help tidy
    sp.add_argument("--probe", dest="probe", action="store_true", help="TCP connect() probe after add")
    sp.add_argument("--timeout", type=float, default=2.5, help="Probe timeout (seconds)")
    sp.set_defaults(func=cmd_add)

    # remove
    sp = sub.add_parser("remove", help="Remove a peer by id")
    add_common_store_arg(sp)
    sp.add_argument("peer_id")
    sp.set_defaults(func=cmd_remove)

    # ban
    sp = sub.add_parser("ban", help="Ban a peer for a duration (e.g., 15m, 1h, 1d)")
    add_common_store_arg(sp)
    sp.add_argument("peer_id")
    sp.add_argument("--for", dest="duration", required=True, help="Duration like 15m / 1h / 1d")
    sp.set_defaults(func=cmd_ban)

    # unban
    sp = sub.add_parser("unban", help="Lift a ban on a peer")
    add_common_store_arg(sp)
    sp.add_argument("peer_id")
    sp.set_defaults(func=cmd_unban)

    # score
    sp = sub.add_parser("score", help="Set a manual score on a peer")
    add_common_store_arg(sp)
    sp.add_argument("peer_id")
    sp.add_argument("score", type=float)
    sp.set_defaults(func=cmd_score)

    # export
    sp = sub.add_parser("export", help="Export peers to a JSON file")
    add_common_store_arg(sp)
    sp.add_argument("path", help="Destination file")
    sp.set_defaults(func=cmd_export)

    # import
    sp = sub.add_parser("import", help="Import peers from a JSON file")
    add_common_store_arg(sp)
    sp.add_argument("path", help="Source JSON file")
    sp.add_argument("--replace", action="store_true", help="Replace existing store instead of merging")
    sp.set_defaults(func=cmd_import)

    # connect
    sp = sub.add_parser("connect", help="Record a desired connection (and optionally probe it)")
    add_common_store_arg(sp)
    sp.add_argument("addr", help="Multiaddr or host:port")
    sp.add_argument("--peer-id", help="Optional peer id; defaults to derived label")
    sp.add_argument("--probe", action="store_true", help="TCP connect() probe immediately")
    sp.add_argument("--timeout", type=float, default=2.5, help="Probe timeout (seconds)")
    sp.set_defaults(func=cmd_connect)

    # disconnect
    sp = sub.add_parser("disconnect", help="Hint disconnect by temporary short ban")
    add_common_store_arg(sp)
    sp.add_argument("peer_id")
    sp.set_defaults(func=cmd_disconnect)

    # show
    sp = sub.add_parser("show", help="Show a single peer detail (JSON)")
    add_common_store_arg(sp)
    sp.add_argument("peer_id")
    sp.set_defaults(func=cmd_show)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    parser = build_parser()
    if not argv:
        parser.print_help()
        return 0
    args = parser.parse_args(argv)
    # Ensure default store directory exists
    if args.store:
        _ensure_dirs(Path(args.store))
    func = getattr(args, "func", None)
    if not func:
        parser.print_help()
        return 2
    try:
        return int(func(args) or 0)
    except KeyboardInterrupt:
        print("\n^C")
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
