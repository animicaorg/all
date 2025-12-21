from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

from .peer import Peer, PeerRole, PeerStatus

log = logging.getLogger(__name__)


def _now() -> float:
    return time.time()


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), isolation_level=None)  # autocommit
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
                                   status, first_seen, last_seen, connected_at, last_disconnect, rtt_ms, score, snapshot, direction)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                                   status, first_seen, last_seen, connected_at, last_disconnect, rtt_ms, score, snapshot)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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

    def record_disconnection(self, peer_id: str) -> None:
        now = _now()
        with self._locked_conn() as conn:
            conn.execute(
                "UPDATE peers SET status=?, last_disconnect=?, last_seen=? WHERE peer_id=?",
                (PeerStatus.DISCONNECTED.value, now, now, peer_id),
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
