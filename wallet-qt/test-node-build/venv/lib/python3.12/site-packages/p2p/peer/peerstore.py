from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

from .peer import Peer, PeerRole, PeerStatus
from .p2p_store import read_peers_json, write_peers_json

log = logging.getLogger(__name__)
_READONLY_WARNED = False


def _now() -> float:
    return time.time()


def _connect(path: Path) -> sqlite3.Connection:
    if str(path) == ":memory:":
        conn = sqlite3.connect(str(path), isolation_level=None)  # autocommit
    else:
        uri = f"file:{path}?mode=rwc"
        conn = sqlite3.connect(uri, isolation_level=None, uri=True)  # autocommit
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    # Pragmas tuned for an append-mostly metadata DB.
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    c.execute("PRAGMA foreign_keys=ON")
    c.close()
    return conn


_SCHEMA = """
CREATE TABLE IF NOT EXISTS peers (
  peer_id TEXT PRIMARY KEY,
  address TEXT NOT NULL,
  roles INTEGER NOT NULL,
  chain_id INTEGER NOT NULL,
  alg_policy_root BLOB NOT NULL,
  head_height INTEGER NOT NULL DEFAULT 0,
  caps TEXT NOT NULL,                 -- JSON array of strings
  status TEXT NOT NULL,
  first_seen REAL NOT NULL,
  last_seen REAL NOT NULL,
  connected_at REAL,
  last_disconnect REAL,
  last_disconnect_reason TEXT,
  rtt_ms REAL,
  score REAL,
  snapshot TEXT,                       -- JSON snapshot (optional but preferred)
  direction TEXT                       -- "inbound" or "outbound" or NULL
);
CREATE TABLE IF NOT EXISTS peer_addresses (
  peer_id TEXT NOT NULL,
  address TEXT NOT NULL,
  last_seen REAL NOT NULL,
  PRIMARY KEY (peer_id, address),
  FOREIGN KEY (peer_id) REFERENCES peers(peer_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_peers_last_seen ON peers(last_seen);
CREATE INDEX IF NOT EXISTS idx_peers_score ON peers(score);
CREATE INDEX IF NOT EXISTS idx_addr_last_seen ON peer_addresses(last_seen);
"""


class PeerStore:
    """
    Lightweight persistent peer registry backed by SQLite.

    - Stores a minimal row for quick selection and an optional JSON snapshot
      (Peer.snapshot()) to rehydrate richer in-memory state when needed.
    - Tracks all previously seen addresses per peer.
    - Maintains a coarse score snapshot for bootstrapping selection.

    Thread-safe for basic concurrent access via an internal lock.
    """

    def __init__(self, db_path: str | Path = None, path: str | Path | None = None):
        """
        Initialize PeerStore with a database path.
        
        Args:
            db_path: Path to database file or directory. If directory, uses peers.db inside.
            path: Alternative keyword argument for db_path (for test compatibility).
        """
        # Handle both positional and keyword 'path' argument
        if db_path is None and path is not None:
            db_path = path
        elif db_path is None:
            db_path = ":memory:"
            
        self.path = Path(db_path) if db_path != ":memory:" else Path(db_path)
        
        # Special handling for in-memory database
        if str(db_path) == ":memory:":
            self._lock = threading.RLock()
            with self._locked_conn() as conn:
                for stmt in filter(None, _SCHEMA.split(";")):
                    s = stmt.strip()
                    if s:
                        conn.execute(s)
            return
        
        # If given a directory, use a default db file name inside it
        if self.path.is_dir():
            self.path = self.path / "peers.db"
        elif not self.path.exists() and not self.path.suffix:
            # Path doesn't exist and has no extension - treat as directory
            self.path = self.path / "peers.db"
                
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._locked_conn() as conn:
            for stmt in filter(None, _SCHEMA.split(";")):
                s = stmt.strip()
                if s:
                    conn.execute(s)
            # Migration: Add direction column if it doesn't exist
            self._migrate_add_direction_column(conn)
            self._migrate_add_disconnect_reason_column(conn)

    def _locked_conn(self) -> sqlite3.Connection:
        # Acquire a re-entrant lock and return a connection bound to this thread.
        self._lock.acquire()
        # We open per-call to avoid sharing stateful connections across threads.
        conn = _connect(self.path)

        class _Guard:
            def __init__(self, outer: PeerStore, c: sqlite3.Connection):
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

    def _migrate_add_direction_column(self, conn: sqlite3.Connection) -> None:
        """
        Add the direction column to the peers table if it doesn't exist.
        This migration is safe to run multiple times.
        """
        try:
            # Check if direction column exists
            cursor = conn.execute("PRAGMA table_info(peers)")
            columns = [row[1] for row in cursor.fetchall()]
            if "direction" not in columns:
                conn.execute("ALTER TABLE peers ADD COLUMN direction TEXT")
        except Exception:
            # If migration fails, don't crash - the column may already exist
            pass

    def _migrate_add_disconnect_reason_column(self, conn: sqlite3.Connection) -> None:
        """
        Add the last_disconnect_reason column to the peers table if it doesn't exist.
        This migration is safe to run multiple times.
        """
        try:
            cursor = conn.execute("PRAGMA table_info(peers)")
            columns = [row[1] for row in cursor.fetchall()]
            if "last_disconnect_reason" not in columns:
                conn.execute("ALTER TABLE peers ADD COLUMN last_disconnect_reason TEXT")
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # Upserts & updates
    # ------------------------------------------------------------------ #

    def add(self, peer_id: str, addrs: Optional[list[str]] = None, score: float = 0, direction: Optional[str] = None) -> None:
        """
        Simplified upsert for tests: add/update a peer with minimal required fields.
        
        Args:
            peer_id: Peer identifier
            addrs: List of multiaddr strings (optional)
            score: Initial score (default 0)
            direction: Connection direction ("inbound" or "outbound", optional)
        """
        now = _now()
        addrs = addrs or []
        # Use first address or default
        primary_addr = addrs[0] if addrs else "/ip4/0.0.0.0/tcp/0"
        
        with self._locked_conn() as conn:
            # Insert or update minimal peer record
            conn.execute(
                """
                INSERT INTO peers (peer_id, address, roles, chain_id, alg_policy_root, head_height, caps,
                                   status, first_seen, last_seen, connected_at, last_disconnect, last_disconnect_reason,
                                   rtt_ms, score, snapshot, direction)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(peer_id) DO UPDATE SET
                  address=excluded.address,
                  last_seen=excluded.last_seen,
                  score=excluded.score,
                  direction=COALESCE(direction, excluded.direction)
                """,
                (
                    peer_id,
                    primary_addr,
                    0,  # roles: NONE
                    0,  # chain_id: default
                    b"",  # alg_policy_root: empty
                    0,  # head_height: 0
                    "[]",  # caps: empty list
                    PeerStatus.DISCONNECTED.value,
                    now,
                    now,
                    None,  # connected_at
                    None,  # last_disconnect
                    None,  # last_disconnect_reason
                    None,  # rtt_ms
                    float(score),
                    "{}",  # snapshot: empty
                    direction,  # direction: "inbound", "outbound", or None
                ),
            )
            # Upsert all addresses
            for addr in addrs:
                conn.execute(
                    """
                    INSERT INTO peer_addresses (peer_id, address, last_seen)
                    VALUES (?, ?, ?)
                    ON CONFLICT(peer_id, address) DO UPDATE SET last_seen=excluded.last_seen
                    """,
                    (peer_id, addr, now),
                )
    
    def upsert(self, peer_id: str, addrs: Optional[list[str]] = None, score: float = 0, direction: Optional[str] = None) -> None:
        """Alias for add() to match test expectations."""
        self.add(peer_id, addrs, score, direction)

    def upsert_peer(self, peer: Peer) -> None:
        """Insert or update a peer row + snapshot; refresh last_seen and address mapping."""
        now = _now()
        score = float(peer.compute_score(now))
        snapshot = json.dumps(peer.snapshot(), separators=(",", ":"), sort_keys=True)

        with self._locked_conn() as conn:
            conn.execute(
                """
                INSERT INTO peers (peer_id, address, roles, chain_id, alg_policy_root, head_height, caps,
                                   status, first_seen, last_seen, connected_at, last_disconnect, last_disconnect_reason,
                                   rtt_ms, score, snapshot)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(peer_id) DO UPDATE SET
                  address=excluded.address,
                  roles=excluded.roles,
                  chain_id=excluded.chain_id,
                  alg_policy_root=excluded.alg_policy_root,
                  head_height=excluded.head_height,
                  caps=excluded.caps,
                  status=excluded.status,
                  last_seen=excluded.last_seen,
                  connected_at=excluded.connected_at,
                  last_disconnect=excluded.last_disconnect,
                  last_disconnect_reason=excluded.last_disconnect_reason,
                  rtt_ms=excluded.rtt_ms,
                  score=excluded.score,
                  snapshot=excluded.snapshot
                """,
                (
                    peer.peer_id,
                    peer.address,
                    int(peer.roles),
                    int(peer.chain_id),
                    peer.alg_policy_root,
                    int(peer.head_height),
                    json.dumps(sorted(peer.caps)),
                    peer.status.value,
                    float(peer.connected_at_s or now),
                    float(now),
                    float(peer.connected_at_s or now),
                    (
                        float(peer.last_disconnect_s or 0.0)
                        if peer.last_disconnect_s
                        else None
                    ),
                    peer.last_disconnect_reason,
                    (
                        float(peer.rtt_ms_ewma or 0.0)
                        if peer.rtt_ms_ewma is not None
                        else None
                    ),
                    score,
                    snapshot,
                ),
            )
            conn.execute(
                """
                INSERT INTO peer_addresses (peer_id, address, last_seen)
                VALUES (?, ?, ?)
                ON CONFLICT(peer_id, address) DO UPDATE SET last_seen=excluded.last_seen
                """,
                (peer.peer_id, peer.address, now),
            )

    def record_seen(self, peer_id: str, address: Optional[str] = None) -> None:
        """Refresh last_seen; optionally upsert the address mapping."""
        now = _now()
        with self._locked_conn() as conn:
            conn.execute("UPDATE peers SET last_seen=? WHERE peer_id=?", (now, peer_id))
            if address:
                conn.execute(
                    """
                    INSERT INTO peer_addresses (peer_id, address, last_seen)
                    VALUES (?, ?, ?)
                    ON CONFLICT(peer_id, address) DO UPDATE SET last_seen=excluded.last_seen
                    """,
                    (peer_id, address, now),
                )

    def record_connection(self, peer_id: str) -> None:
        now = _now()
        with self._locked_conn() as conn:
            conn.execute(
                "UPDATE peers SET status=?, connected_at=?, last_seen=? WHERE peer_id=?",
                (PeerStatus.CONNECTED.value, now, now, peer_id),
            )

    def record_disconnection(self, peer_id: str, reason: Optional[str] = None) -> None:
        now = _now()
        with self._locked_conn() as conn:
            conn.execute(
                "UPDATE peers SET status=?, last_disconnect=?, last_disconnect_reason=?, last_seen=? WHERE peer_id=?",
                (PeerStatus.DISCONNECTED.value, now, reason, now, peer_id),
            )

    def note_rtt_sample(
        self, peer_id: str, sample_ms: float, alpha: float = 0.2
    ) -> None:
        """Update the EWMA RTT stored for a peer."""
        with self._locked_conn() as conn:
            row = conn.execute(
                "SELECT rtt_ms FROM peers WHERE peer_id=?", (peer_id,)
            ).fetchone()
            if row is None:
                return
            current = row["rtt_ms"]
            if current is None or current <= 0.0:
                new_rtt = float(sample_ms)
            else:
                new_rtt = float(
                    (1.0 - alpha) * float(current) + alpha * float(sample_ms)
                )
            conn.execute(
                "UPDATE peers SET rtt_ms=?, last_seen=? WHERE peer_id=?",
                (new_rtt, _now(), peer_id),
            )

    def update_score_snapshot(self, peer_id: str, score: float) -> None:
        with self._locked_conn() as conn:
            conn.execute(
                "UPDATE peers SET score=?, last_seen=? WHERE peer_id=?",
                (float(score), _now(), peer_id),
            )
    
    def increment_score(self, peer_id: str, delta: float) -> None:
        """Add delta to the peer's score."""
        with self._locked_conn() as conn:
            row = conn.execute(
                "SELECT score FROM peers WHERE peer_id=?", (peer_id,)
            ).fetchone()
            if row is None:
                return
            current_score = float(row["score"] or 0.0)
            new_score = current_score + float(delta)
            conn.execute(
                "UPDATE peers SET score=?, last_seen=? WHERE peer_id=?",
                (new_score, _now(), peer_id),
            )

    def update_head_height(self, peer_id: str, height: int) -> None:
        with self._locked_conn() as conn:
            conn.execute(
                "UPDATE peers SET head_height=?, last_seen=? WHERE peer_id=?",
                (int(height), _now(), peer_id),
            )

    def ban(self, peer_id: str) -> None:
        with self._locked_conn() as conn:
            conn.execute(
                "UPDATE peers SET status=?, last_seen=? WHERE peer_id=?",
                (PeerStatus.BANNED.value, _now(), peer_id),
            )

    def forget(self, peer_id: str) -> None:
        with self._locked_conn() as conn:
            conn.execute("DELETE FROM peers WHERE peer_id=?", (peer_id,))

    # ------------------------------------------------------------------ #
    # Queries & selection
    # ------------------------------------------------------------------ #

    def get(self, peer_id: str) -> Optional[Peer]:
        with self._locked_conn() as conn:
            row = conn.execute(
                "SELECT * FROM peers WHERE peer_id=?", (peer_id,)
            ).fetchone()
            if row is None:
                return None
            peer = self._row_to_peer(row)
            # Attach addresses from the peer_addresses table
            addr_rows = conn.execute(
                "SELECT address FROM peer_addresses WHERE peer_id=? ORDER BY last_seen DESC",
                (peer_id,),
            ).fetchall()
            # Add as a list attribute for compatibility with tests
            peer.addrs = [r["address"] for r in addr_rows]  # type: ignore
            # Also attach the score snapshot for test compatibility
            peer.score = float(row["score"]) if row["score"] is not None else 0.0  # type: ignore
            # Attach direction if present
            if "direction" in row.keys():
                peer.direction = row["direction"]  # type: ignore
            return peer

    def find_by_address(self, address: str) -> List[str]:
        with self._locked_conn() as conn:
            rows = conn.execute(
                "SELECT peer_id FROM peer_addresses WHERE address=? ORDER BY last_seen DESC",
                (address,),
            ).fetchall()
        return [r["peer_id"] for r in rows]

    def list_known(
        self,
        *,
        limit: int = 100,
        min_score: Optional[float] = None,
        status_in: Optional[Sequence[PeerStatus]] = None,
        order_by: str = "score",  # "score" | "last_seen" | "rtt_ms"
    ) -> List[Peer]:
        where = []
        args: list = []
        if min_score is not None:
            where.append("score IS NOT NULL AND score >= ?")
            args.append(float(min_score))
        if status_in:
            placeholders = ",".join("?" for _ in status_in)
            where.append(f"status IN ({placeholders})")
            args.extend([s.value for s in status_in])
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""
        order_sql = {
            "score": "score DESC NULLS LAST, last_seen DESC",
            "last_seen": "last_seen DESC",
            "rtt_ms": "rtt_ms ASC NULLS LAST, score DESC",
        }.get(order_by, "score DESC")
        sql = f"SELECT * FROM peers {where_sql} ORDER BY {order_sql} LIMIT ?"
        args.append(int(limit))

        with self._locked_conn() as conn:
            rows = conn.execute(sql, tuple(args)).fetchall()
            return [self._row_to_peer(r) for r in rows]

    def count_known(self, status_in: Optional[Sequence[PeerStatus]] = None) -> int:
        """
        Return the number of peers stored in the peerstore.

        Optionally filter by peer status.
        """
        with self._locked_conn() as conn:
            query = "SELECT COUNT(*) as c FROM peers"
            args: list[object] = []
            if status_in:
                placeholders = ",".join("?" for _ in status_in)
                query += f" WHERE status IN ({placeholders})"
                args.extend([s.value for s in status_in])
            row = conn.execute(query, args).fetchone()
            if row is None:
                return 0
            return int(row["c"] or 0)

    def list_addresses(
        self, *, limit: int = 200, since: Optional[float] = None
    ) -> List[Tuple[str, str, float]]:
        where = "WHERE 1=1"
        args: list = []
        if since is not None:
            where += " AND last_seen >= ?"
            args.append(float(since))
        sql = f"SELECT peer_id, address, last_seen FROM peer_addresses {where} ORDER BY last_seen DESC LIMIT ?"
        args.append(int(limit))
        with self._locked_conn() as conn:
            rows = conn.execute(sql, tuple(args)).fetchall()
            return [(r["peer_id"], r["address"], float(r["last_seen"])) for r in rows]

    # ------------------------------------------------------------------ #
    # GC & maintenance
    # ------------------------------------------------------------------ #

    def prune(
        self,
        *,
        older_than_s: float,
        statuses: Iterable[PeerStatus] = (PeerStatus.BANNED,),
    ) -> int:
        """
        Remove peers with status in `statuses` whose last_seen is older than the given threshold.
        Returns number of rows removed.
        """
        cutoff = _now() - older_than_s
        statuses_vals = tuple(s.value for s in statuses)
        placeholders = ",".join("?" for _ in statuses_vals)
        with self._locked_conn() as conn:
            cur = conn.execute(
                f"DELETE FROM peers WHERE status IN ({placeholders}) AND last_seen < ?",
                (*statuses_vals, cutoff),
            )
            return cur.rowcount or 0

    def vacuum(self) -> None:
        with self._locked_conn() as conn:
            conn.execute("VACUUM")

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _row_to_peer(self, row: sqlite3.Row) -> Peer:
        """
        Rehydrate a Peer from row. Prefer the JSON snapshot if present; otherwise construct a minimal Peer.
        """
        snap_txt = row["snapshot"]
        if snap_txt:
            try:
                snap = json.loads(snap_txt)
                # Minimal reconstruction path; Peer.snapshot() already contains enough fields.
                p = Peer(
                    peer_id=snap["peer_id"],
                    address=snap["address"],
                    roles=PeerRole(int(snap.get("roles", row["roles"]))),
                    chain_id=int(snap.get("chain_id", row["chain_id"])),
                    alg_policy_root=bytes(row["alg_policy_root"]),
                    head_height=int(snap.get("head_height", row["head_height"])),
                    caps=set(snap.get("caps", [])),
                )
                # Restore lifecycle bits
                status = snap.get("status", row["status"])
                p.status = PeerStatus(status)
                p.connected_at_s = snap.get("connected_at_s") or row["connected_at"]
                p.last_seen_s = snap.get("last_seen_s") or row["last_seen"]
                p.last_disconnect_s = (
                    snap.get("last_disconnect_s") or row["last_disconnect"]
                )
                p.last_disconnect_reason = snap.get("last_disconnect_reason")
                if p.last_disconnect_reason is None and "last_disconnect_reason" in row.keys():
                    p.last_disconnect_reason = row["last_disconnect_reason"]
                # Restore RTT if present
                rtt = snap.get("rtt_ms_ewma")
                if rtt is None:
                    rtt = row["rtt_ms"]
                p.rtt_ms_ewma = float(rtt) if rtt is not None else None
                # Topic scores / penalties are best-effort; skip to keep I/O light.
                return p
            except Exception:
                # Fall through to minimal reconstruction.
                pass

        # Minimal constructor from columns.
        try:
            caps_raw = row["caps"]
            try:
                caps = set(json.loads(caps_raw or "[]"))
            except Exception:
                log.debug("PeerStore: failed to parse caps; defaulting to empty", exc_info=True)
                caps = set()

            try:
                status = PeerStatus(row["status"])
            except Exception:
                log.warning(
                    "PeerStore: unknown status '%s'; treating as disconnected", row["status"]
                )
                status = PeerStatus.DISCONNECTED

            try:
                alg_root = bytes(row["alg_policy_root"])
            except Exception:
                alg_root = b""

            p = Peer(
                peer_id=row["peer_id"],
                address=row["address"],
                roles=PeerRole(int(row["roles"] or 0)),
                chain_id=int(row["chain_id"] or 0),
                alg_policy_root=alg_root,
                head_height=int(row["head_height"] or 0),
                caps=caps,
            )
            p.status = status
            p.connected_at_s = row["connected_at"]
            p.last_seen_s = row["last_seen"]
            p.last_disconnect_s = row["last_disconnect"]
            if "last_disconnect_reason" in row.keys():
                p.last_disconnect_reason = row["last_disconnect_reason"]
            try:
                p.rtt_ms_ewma = float(row["rtt_ms"]) if row["rtt_ms"] is not None else None
            except Exception:
                p.rtt_ms_ewma = None
            return p
        except Exception:
            log.warning("PeerStore: failed to rehydrate peer row; skipping", exc_info=True)
            try:
                chain_val = row["chain_id"]
            except Exception:
                chain_val = 0
            return Peer(
                peer_id=row["peer_id"],
                address=row["address"],
                roles=PeerRole.NONE,
                chain_id=int(chain_val or 0),
                alg_policy_root=b"",
            )


# Convenience factory: memory store (for tests)
def in_memory_store() -> PeerStore:
    return PeerStore(":memory:")


@dataclass
class _JsonPeer:
    peer_id: str
    address: str
    score: float = 0.0
    last_seen: Optional[float] = None
    connected: bool = False
    direction: Optional[str] = None


class JsonPeerStore:
    """
    Minimal JSON-backed peer store used when SQLite is unavailable or read-only.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self._peers: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            self._peers = {}
            return
        data = read_peers_json(self.path)
        self._peers = {
            p.get("peer_id"): dict(p)
            for p in data.get("peers", [])
            if isinstance(p, dict) and p.get("peer_id")
        }

    def _save(self) -> None:
        data = {"peers": list(self._peers.values())}
        write_peers_json(self.path, data)

    def add(
        self,
        peer_id: str,
        addrs: Optional[list[str]] = None,
        score: float = 0.0,
        direction: Optional[str] = None,
    ) -> None:
        now = _now()
        entry = self._peers.get(peer_id) or {
            "peer_id": peer_id,
            "addrs": [],
            "score": float(score),
            "last_seen": now,
            "connected": False,
            "direction": direction,
        }
        entry["score"] = float(score)
        entry["last_seen"] = now
        if direction:
            entry["direction"] = direction
        if addrs:
            entry["addrs"] = list(dict.fromkeys((entry.get("addrs") or []) + addrs))
            if entry["addrs"]:
                entry["address"] = entry["addrs"][0]
        self._peers[peer_id] = entry
        self._save()

    def record_seen(self, peer_id: str, address: Optional[str] = None) -> None:
        now = _now()
        entry = self._peers.get(peer_id) or {"peer_id": peer_id, "addrs": []}
        entry["last_seen"] = now
        if address:
            entry["addrs"] = list(
                dict.fromkeys((entry.get("addrs") or []) + [address])
            )
            entry["address"] = entry["addrs"][0]
        self._peers[peer_id] = entry
        self._save()

    def record_connection(self, peer_id: str) -> None:
        entry = self._peers.get(peer_id) or {"peer_id": peer_id, "addrs": []}
        entry["connected"] = True
        entry["last_seen"] = _now()
        self._peers[peer_id] = entry
        self._save()

    def record_disconnection(self, peer_id: str, reason: Optional[str] = None) -> None:
        entry = self._peers.get(peer_id) or {"peer_id": peer_id, "addrs": []}
        entry["connected"] = False
        entry["last_seen"] = _now()
        if reason:
            entry["last_disconnect_reason"] = reason
        self._peers[peer_id] = entry
        self._save()

    def update_head_height(self, peer_id: str, height: int) -> None:
        entry = self._peers.get(peer_id) or {"peer_id": peer_id, "addrs": []}
        entry["head_height"] = int(height)
        entry["last_seen"] = _now()
        self._peers[peer_id] = entry
        self._save()

    def increment_score(self, peer_id: str, delta: float) -> None:
        entry = self._peers.get(peer_id)
        if not entry:
            return
        entry["score"] = float(entry.get("score", 0.0)) + float(delta)
        entry["last_seen"] = _now()
        self._peers[peer_id] = entry
        self._save()

    def list_known(
        self,
        *,
        limit: int = 100,
        min_score: Optional[float] = None,
        status_in: Optional[Sequence[PeerStatus]] = None,
        order_by: str = "score",
    ) -> List[_JsonPeer]:
        peers = []
        for pid, entry in self._peers.items():
            if min_score is not None and float(entry.get("score", 0.0)) < min_score:
                continue
            addr = entry.get("address") or (entry.get("addrs") or [None])[0]
            if not addr:
                continue
            peers.append(
                _JsonPeer(
                    peer_id=pid,
                    address=addr,
                    score=float(entry.get("score", 0.0)),
                    last_seen=entry.get("last_seen"),
                    connected=bool(entry.get("connected", False)),
                    direction=entry.get("direction"),
                )
            )
        if order_by == "last_seen":
            peers.sort(key=lambda p: p.last_seen or 0, reverse=True)
        else:
            peers.sort(key=lambda p: p.score, reverse=True)
        return peers[: int(limit)]

    def list_addresses(
        self, *, limit: int = 200, since: Optional[float] = None
    ) -> List[Tuple[str, str, float]]:
        rows: list[Tuple[str, str, float]] = []
        for pid, entry in self._peers.items():
            last_seen = float(entry.get("last_seen") or 0)
            if since is not None and last_seen < since:
                continue
            for addr in entry.get("addrs") or []:
                rows.append((pid, addr, last_seen))
        rows.sort(key=lambda r: r[2], reverse=True)
        return rows[: int(limit)]

    def count_known(self, status_in: Optional[Sequence[PeerStatus]] = None) -> int:
        return len(self._peers)


def open_peerstore(path: str | Path, *, json_fallback: Optional[Path] = None):
    """
    Open a PeerStore, falling back to JSON when the sqlite DB is read-only.
    """
    global _READONLY_WARNED
    try:
        return PeerStore(path)
    except sqlite3.OperationalError as exc:
        msg = str(exc).lower()
        if (
            "readonly" not in msg
            and "read-only" not in msg
            and "unable to open database file" not in msg
        ):
            raise
        if not _READONLY_WARNED:
            log.warning(
                "PeerStore database is read-only; falling back to JSON store: %s", exc
            )
            _READONLY_WARNED = True
        fallback_path = Path(json_fallback) if json_fallback else Path(path).with_suffix(".json")
        return JsonPeerStore(fallback_path)
