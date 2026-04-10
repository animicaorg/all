from __future__ import annotations

import ipaddress
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from p2p.peer.peer_addr import normalize_peer_addr

@dataclass(frozen=True)
class AddressEntry:
    address: str  # original address as provided
    norm: str  # normalized, canonical string
    proto: str  # tcp|quic|ws|wss (best-effort)
    host: str  # IP or DNS
    port: int
    peer_id: Optional[str]
    tag: str  # seed|manual|learned|peer
    first_seen: float
    last_seen: float
    bad_count: int
    good_count: int
    score: Optional[float]


def _now() -> float:
    return time.time()


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    c.execute("PRAGMA foreign_keys=ON")
    c.close()
    return conn


_SCHEMA = """
CREATE TABLE IF NOT EXISTS addresses (
  address   TEXT PRIMARY KEY,            -- as provided (trimmed)
  norm      TEXT NOT NULL,               -- canonical normalized representation
  proto     TEXT NOT NULL,               -- tcp|quic|ws|wss (best-effort)
  host      TEXT NOT NULL,
  port      INTEGER NOT NULL,
  peer_id   TEXT,
  tag       TEXT NOT NULL,               -- seed|manual|learned|peer
  first_seen REAL NOT NULL,
  last_seen  REAL NOT NULL,
  bad_count  INTEGER NOT NULL DEFAULT 0,
  good_count INTEGER NOT NULL DEFAULT 0,
  score      REAL
);
CREATE INDEX IF NOT EXISTS idx_addresses_last_seen ON addresses(last_seen);
CREATE INDEX IF NOT EXISTS idx_addresses_score ON addresses(score);
"""


class AddressBook:
    """
    Persistent address book with validation & normalization.

    Accepts addresses in:
      - Multiaddr-like:  /ip4/1.2.3.4/tcp/30303[/quic|/ws|/wss]
                         /dns/seed.example.org/tcp/30303
      - URL-like:        tcp://host:port, quic://host:port, ws://host:port, wss://host:port
      - host:port:       example.org:30303, 1.2.3.4:30303, [::1]:30303

    Normalization rules (best-effort, conservative):
      - If a valid multiaddr is provided, keep it as `norm`.
      - If URL/host:port is provided, normalize to "proto://host:port".
      - IPv6 hosts are stored bracketed in URL form, unbracketed in `host`.
    """

    def __init__(self, db_path: str | Path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._locked_conn() as conn:
            for stmt in filter(None, _SCHEMA.split(";")):
                s = stmt.strip()
                if s:
                    conn.execute(s)

    # --------------- Parsing / validation ---------------- #

    def validate(self, addr: str) -> Tuple[str, str, str, int]:
        """
        Validate and normalize an address.

        Returns (norm, proto, host, port). Raises ValueError on invalid input.
        """
        parsed = normalize_peer_addr(addr, allow_quic=True, allow_ws=True)
        if not parsed.addr:
            raise ValueError(parsed.reason or "unrecognized address format")
        host = parsed.addr.host
        port = parsed.addr.port
        _validate_host(host)
        _validate_port(port)
        return parsed.addr.canonical, parsed.addr.scheme, host, port

    # --------------- Storage ops ---------------- #

    def add(
        self,
        addr: str,
        *,
        tag: str = "manual",
        peer_id: Optional[str] = None,
        score: Optional[float] = None,
    ) -> AddressEntry:
        """
        Validate + upsert an address. Returns the stored entry.
        """
        norm, proto, host, port = self.validate(addr)
        now = _now()
        with self._locked_conn() as conn:
            conn.execute(
                """
                INSERT INTO addresses (address, norm, proto, host, port, peer_id, tag, first_seen, last_seen, score)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(address) DO UPDATE SET
                  norm=excluded.norm,
                  proto=excluded.proto,
                  host=excluded.host,
                  port=excluded.port,
                  peer_id=COALESCE(excluded.peer_id, addresses.peer_id),
                  tag=excluded.tag,
                  last_seen=excluded.last_seen,
                  score=COALESCE(excluded.score, addresses.score)
                """,
                (addr.strip(), norm, proto, host, port, peer_id, tag, now, now, score),
            )
            return self.get(addr)  # type: ignore[return-value]

    def remove(self, addr: str) -> None:
        with self._locked_conn() as conn:
            conn.execute("DELETE FROM addresses WHERE address=?", (addr.strip(),))

    def mark_seen(
        self, addr: str, *, good: bool = True, peer_id: Optional[str] = None
    ) -> None:
        """
        Mark address as seen now, incrementing good/bad counters.
        """
        now = _now()
        # Ensure it's present
        try:
            self.add(addr, tag="learned", peer_id=peer_id)
        except ValueError:
            # If it's invalid, we still want to track the attempt? No: ignore invalids.
            return
        with self._locked_conn() as conn:
            if good:
                conn.execute(
                    "UPDATE addresses SET last_seen=?, good_count=good_count+1, peer_id=COALESCE(?, peer_id) WHERE address=?",
                    (now, peer_id, addr.strip()),
                )
            else:
                conn.execute(
                    "UPDATE addresses SET last_seen=?, bad_count=bad_count+1, peer_id=COALESCE(?, peer_id) WHERE address=?",
                    (now, peer_id, addr.strip()),
                )

    def set_score(self, addr: str, score: Optional[float]) -> None:
        with self._locked_conn() as conn:
            conn.execute(
                "UPDATE addresses SET score=? WHERE address=?", (score, addr.strip())
            )

    def last_seen(self, addr: str) -> Optional[float]:
        with self._locked_conn() as conn:
            row = conn.execute(
                "SELECT last_seen FROM addresses WHERE address=?", (addr.strip(),)
            ).fetchone()
            return float(row["last_seen"]) if row else None

    def get(self, addr: str) -> Optional[AddressEntry]:
        with self._locked_conn() as conn:
            row = conn.execute(
                "SELECT * FROM addresses WHERE address=?", (addr.strip(),)
            ).fetchone()
            return _row_to_entry(row) if row else None

    def list_recent(
        self,
        *,
        limit: int = 200,
        since: Optional[float] = None,
        tags: Optional[Iterable[str]] = None,
    ) -> List[AddressEntry]:
        where = ["1=1"]
        args: list = []
        if since is not None:
            where.append("last_seen >= ?")
            args.append(float(since))
        if tags:
            tags_list = list(tags)
            where.append(f"tag IN ({','.join('?' for _ in tags_list)})")
            args.extend(tags_list)
        sql = f"SELECT * FROM addresses WHERE {' AND '.join(where)} ORDER BY last_seen DESC LIMIT ?"
        args.append(int(limit))
        with self._locked_conn() as conn:
            rows = conn.execute(sql, tuple(args)).fetchall()
            return [_row_to_entry(r) for r in rows]

    def import_seed_list(self, text: str, *, default_proto: str = "tcp") -> int:
        """
        Import a newline-separated list of addresses. Lines beginning with '#' are ignored.
        Returns number of successfully imported entries.
        """
        n = 0
        for line in text.splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            # Allow bare host:port; allow "host port" -> convert; allow "host" -> reject.
            if "://" not in s and not s.startswith("/") and " " in s:
                parts = s.split()
                if len(parts) == 2 and parts[1].isdigit():
                    s = f"{default_proto}://{parts[0]}:{parts[1]}"
            try:
                self.add(s, tag="seed")
                n += 1
            except ValueError:
                # Skip invalid seed line
                continue
        return n

    def load_seed_file(self, path: str | Path) -> int:
        p = Path(path)
        if not p.exists():
            return 0
        return self.import_seed_list(p.read_text())

    def prune(self, *, older_than_s: float, max_bad_ratio: float = 0.85) -> int:
        """
        Drop addresses that haven't been seen recently or have very poor success ratio.
        Returns number of rows removed.
        """
        cutoff = _now() - older_than_s
        with self._locked_conn() as conn:
            # Remove by age
            cur1 = conn.execute("DELETE FROM addresses WHERE last_seen < ?", (cutoff,))
            removed = cur1.rowcount or 0
            # Remove by bad ratio
            rows = conn.execute(
                "SELECT address, good_count, bad_count FROM addresses"
            ).fetchall()
            for r in rows:
                good, bad = int(r["good_count"]), int(r["bad_count"])
                tot = good + bad
                if tot >= 5 and bad / max(tot, 1) >= max_bad_ratio:
                    conn.execute(
                        "DELETE FROM addresses WHERE address=?", (r["address"],)
                    )
                    removed += 1
            return removed

    # --------------- internals ---------------- #

    def _locked_conn(self) -> sqlite3.Connection:
        self._lock.acquire()
        conn = _connect(self.path)

        class _Guard:
            def __init__(self, outer: AddressBook, c: sqlite3.Connection):
                self._outer = outer
                self._c = c

            def __enter__(self) -> sqlite3.Connection:
                return self._c

            def __exit__(self, exc_type, exc, tb) -> None:
                try:
                    self._c.close()
                finally:
                    self._outer._lock.release()

        return _Guard(self, conn)  # type: ignore[return-value]


# ----------------- helpers ----------------- #


def _row_to_entry(r: sqlite3.Row) -> AddressEntry:
    return AddressEntry(
        address=r["address"],
        norm=r["norm"],
        proto=r["proto"],
        host=r["host"],
        port=int(r["port"]),
        peer_id=r["peer_id"],
        tag=r["tag"],
        first_seen=float(r["first_seen"]),
        last_seen=float(r["last_seen"]),
        bad_count=int(r["bad_count"]),
        good_count=int(r["good_count"]),
        score=float(r["score"]) if r["score"] is not None else None,
    )


def _is_ipv6(host: str) -> bool:
    try:
        return isinstance(ipaddress.ip_address(host), ipaddress.IPv6Address)
    except Exception:
        return False


def _validate_host(host: str) -> None:
    # Accept IPv4/IPv6 or a reasonable DNS name
    try:
        ipaddress.ip_address(host)
        return
    except Exception:
        pass
    if not re.match(
        r"^[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9\-]{1,63})*$",
        host,
    ):
        raise ValueError(f"invalid host: {host}")


def _validate_port(port: int) -> None:
    if not (0 < int(port) < 65536):
        raise ValueError(f"invalid port: {port}")

