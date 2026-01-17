"""
Abuse prevention and ban management for the mining pool.

Handles connection rate limiting, invalid share tracking, and ban enforcement.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

from .db import PoolDatabase


class BanReason(Enum):
    """Reasons for banning a connection."""

    INVALID_SHARE_RATIO = "invalid_share_ratio"
    INVALID_SHARE_SPAM = "invalid_share_spam"
    AUTH_FAILURE_SPAM = "auth_failure_spam"
    CONNECTION_FLOOD = "connection_flood"
    SUBMIT_RATE_EXCEEDED = "submit_rate_exceeded"
    MANUAL = "manual"


@dataclass
class AbuseConfig:
    """Configuration for abuse prevention."""

    # Connection limits
    max_conns_per_ip: int = 10
    max_new_conns_per_min_per_ip: int = 5
    max_total_conns: int = 1000

    # Share submission limits
    max_submits_per_sec: float = 10.0  # Sustained rate
    max_submit_burst: int = 20  # Burst allowance

    # Invalid share banning
    ban_invalid_ratio_threshold: float = 0.5  # 50% invalid
    ban_invalid_min_submits: int = 20  # Need at least this many submits
    ban_invalid_spam_count: int = 10  # N invalid within M seconds
    ban_invalid_spam_window_sec: float = 60.0  # M seconds

    # Auth failure limits
    max_auth_failures_per_min: int = 5

    # Ban durations (exponential backoff)
    ban_base_duration_sec: int = 60  # 1 minute
    ban_max_duration_sec: int = 86400  # 24 hours
    ban_escalation_factor: int = 5  # 1m, 5m, 25m, 125m, ...

    # Ban cleanup
    ban_cleanup_interval_sec: int = 300  # Check every 5 minutes


@dataclass
class ConnectionStats:
    """Per-connection statistics for abuse tracking."""

    ip: str
    connection_id: str
    connected_at: float = field(default_factory=time.time)

    # Submit tracking
    submit_timestamps: deque[float] = field(default_factory=deque)
    total_submits: int = 0
    invalid_submits: int = 0
    stale_submits: int = 0

    # Auth tracking
    auth_failures: deque[float] = field(default_factory=deque)


@dataclass
class IPStats:
    """Per-IP statistics for abuse tracking."""

    ip: str
    connection_count: int = 0
    new_connection_timestamps: deque[float] = field(default_factory=deque)
    last_connection_at: float = field(default_factory=time.time)


@dataclass
class Ban:
    """Ban record."""

    ip: str
    reason: str
    created_at: datetime
    expires_at: datetime
    strike_count: int  # Number of times banned


class AbuseManager:
    """
    Manages abuse prevention and ban enforcement.

    Tracks per-connection and per-IP statistics, enforces rate limits,
    and manages bans with exponential backoff.
    """

    def __init__(
        self,
        db: PoolDatabase,
        config: AbuseConfig,
        *,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._db = db
        self._config = config
        self._log = logger or logging.getLogger("animica.pool.abuse")

        # In-memory tracking
        self._conn_stats: dict[str, ConnectionStats] = {}
        self._ip_stats: dict[str, IPStats] = {}

        # Ban cache (loaded from DB)
        self._active_bans: dict[str, Ban] = {}

        # Ensure bans table exists
        self._ensure_bans_table()

        # Load existing bans
        self._load_bans()

    def _ensure_bans_table(self) -> None:
        """Ensure bans table exists."""
        try:
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS bans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ip TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TIMESTAMP NOT NULL,
                    expires_at TIMESTAMP NOT NULL,
                    strike_count INTEGER DEFAULT 1
                )
                """
            )
            self._db.execute("CREATE INDEX IF NOT EXISTS idx_bans_ip ON bans(ip)")
            self._db.execute("CREATE INDEX IF NOT EXISTS idx_bans_expires ON bans(expires_at)")
            self._db.commit()
        except Exception as e:
            self._log.warning(f"Could not ensure bans table: {e}")

    def _load_bans(self) -> None:
        """Load active bans from database."""
        try:
            now = datetime.utcnow()
            rows = self._db.fetchall(
                "SELECT ip, reason, created_at, expires_at, strike_count FROM bans WHERE expires_at > ?",
                (now,),
            )

            for row in rows:
                ban = Ban(
                    ip=row["ip"],
                    reason=row["reason"],
                    created_at=datetime.fromisoformat(row["created_at"]),
                    expires_at=datetime.fromisoformat(row["expires_at"]),
                    strike_count=row["strike_count"],
                )
                self._active_bans[ban.ip] = ban

            self._log.info(f"Loaded {len(self._active_bans)} active bans")
        except Exception as e:
            # Table might not exist yet (first run or migration pending)
            self._log.warning(f"Could not load bans (table may not exist yet): {e}")
            self._active_bans = {}

    def is_banned(self, ip: str) -> tuple[bool, Optional[Ban]]:
        """
        Check if an IP is currently banned.

        Args:
            ip: IP address

        Returns:
            (is_banned, ban_record)
        """
        ban = self._active_bans.get(ip)
        if ban and ban.expires_at > datetime.utcnow():
            return True, ban

        # Ban expired, remove it
        if ban:
            del self._active_bans[ip]

        return False, None

    def can_connect(self, ip: str) -> tuple[bool, Optional[str]]:
        """
        Check if a new connection from IP is allowed.

        Args:
            ip: IP address

        Returns:
            (allowed, reason_if_denied)
        """
        # Check ban
        is_banned, ban = self.is_banned(ip)
        if is_banned:
            return False, f"Banned: {ban.reason} (expires: {ban.expires_at})"

        # Check per-IP connection limit
        ip_stat = self._ip_stats.get(ip)
        if ip_stat and ip_stat.connection_count >= self._config.max_conns_per_ip:
            return False, f"Too many connections from IP ({self._config.max_conns_per_ip} max)"

        # Check new connection rate
        if ip_stat:
            now = time.time()
            cutoff = now - 60.0
            recent_conns = sum(1 for ts in ip_stat.new_connection_timestamps if ts >= cutoff)
            if recent_conns >= self._config.max_new_conns_per_min_per_ip:
                return False, f"Connection rate limit exceeded ({self._config.max_new_conns_per_min_per_ip}/min max)"

        # Check global connection limit
        total_conns = sum(stat.connection_count for stat in self._ip_stats.values())
        if total_conns >= self._config.max_total_conns:
            return False, f"Pool connection limit reached ({self._config.max_total_conns} max)"

        return True, None

    def register_connection(self, connection_id: str, ip: str) -> None:
        """
        Register a new connection.

        Args:
            connection_id: Unique connection identifier
            ip: IP address
        """
        # Create connection stats
        self._conn_stats[connection_id] = ConnectionStats(
            ip=ip,
            connection_id=connection_id,
        )

        # Update IP stats
        ip_stat = self._ip_stats.get(ip)
        if not ip_stat:
            ip_stat = IPStats(ip=ip)
            self._ip_stats[ip] = ip_stat

        ip_stat.connection_count += 1
        now = time.time()
        ip_stat.new_connection_timestamps.append(now)
        ip_stat.last_connection_at = now

        # Prune old timestamps
        cutoff = now - 60.0
        while ip_stat.new_connection_timestamps and ip_stat.new_connection_timestamps[0] < cutoff:
            ip_stat.new_connection_timestamps.popleft()

        self._log.debug(f"Registered connection {connection_id} from {ip}")

    def unregister_connection(self, connection_id: str) -> None:
        """
        Unregister a connection.

        Args:
            connection_id: Connection identifier
        """
        conn_stat = self._conn_stats.get(connection_id)
        if not conn_stat:
            return

        ip = conn_stat.ip

        # Remove connection stats
        del self._conn_stats[connection_id]

        # Update IP stats
        ip_stat = self._ip_stats.get(ip)
        if ip_stat:
            ip_stat.connection_count = max(0, ip_stat.connection_count - 1)

            # Clean up IP stats if no connections AND no recent history
            if ip_stat.connection_count == 0:
                # Keep IP stats if there are recent connections (for rate limiting)
                now = time.time()
                cutoff = now - 60.0
                # Prune old timestamps first
                while ip_stat.new_connection_timestamps and ip_stat.new_connection_timestamps[0] < cutoff:
                    ip_stat.new_connection_timestamps.popleft()
                
                # Only delete if no recent activity
                if not ip_stat.new_connection_timestamps:
                    del self._ip_stats[ip]

        self._log.debug(f"Unregistered connection {connection_id}")

    def record_submit(self, connection_id: str, is_valid: bool, is_stale: bool = False) -> Optional[str]:
        """
        Record a share submission and check for abuse.

        Args:
            connection_id: Connection identifier
            is_valid: Whether share was valid
            is_stale: Whether share was stale (not counted as invalid for banning)

        Returns:
            Warning message if rate limit approached, None otherwise
        """
        conn_stat = self._conn_stats.get(connection_id)
        if not conn_stat:
            return None

        now = time.time()
        conn_stat.submit_timestamps.append(now)
        conn_stat.total_submits += 1

        if not is_valid:
            if is_stale:
                conn_stat.stale_submits += 1
            else:
                conn_stat.invalid_submits += 1

        # Prune old timestamps
        cutoff = now - 60.0
        while conn_stat.submit_timestamps and conn_stat.submit_timestamps[0] < cutoff:
            conn_stat.submit_timestamps.popleft()

        # Check submit rate limit
        recent_submits = len(conn_stat.submit_timestamps)
        if recent_submits > self._config.max_submits_per_sec:
            return f"Submit rate limit approached ({recent_submits}/sec)"

        return None

    def check_and_ban(self, connection_id: str) -> Optional[Ban]:
        """
        Check if connection should be banned based on abuse metrics.

        Args:
            connection_id: Connection identifier

        Returns:
            Ban record if connection was banned, None otherwise
        """
        conn_stat = self._conn_stats.get(connection_id)
        if not conn_stat:
            return None

        ip = conn_stat.ip

        # Skip if already banned
        if ip in self._active_bans:
            return None

        # Check invalid share ratio
        if conn_stat.total_submits >= self._config.ban_invalid_min_submits:
            invalid_ratio = conn_stat.invalid_submits / conn_stat.total_submits
            if invalid_ratio >= self._config.ban_invalid_ratio_threshold:
                return self._create_ban(
                    ip,
                    BanReason.INVALID_SHARE_RATIO.value,
                    f"Invalid share ratio: {invalid_ratio:.1%}",
                )

        # Check invalid share spam (N invalid in M seconds)
        now = time.time()
        cutoff = now - self._config.ban_invalid_spam_window_sec
        recent_invalid = 0
        for ts in conn_stat.submit_timestamps:
            if ts >= cutoff:
                # We don't track which submits were invalid individually,
                # so we estimate based on ratio
                pass

        # Simplified spam check: if most recent submits are invalid
        if conn_stat.total_submits >= self._config.ban_invalid_spam_count:
            recent_count = min(self._config.ban_invalid_spam_count, conn_stat.total_submits)
            recent_invalid_ratio = conn_stat.invalid_submits / conn_stat.total_submits
            if recent_invalid_ratio >= 0.8:  # 80% of recent submits invalid
                return self._create_ban(
                    ip,
                    BanReason.INVALID_SHARE_SPAM.value,
                    f"Invalid share spam: {recent_invalid_ratio:.1%}",
                )

        return None

    def record_auth_failure(self, connection_id: str) -> Optional[Ban]:
        """
        Record an authentication failure and check for abuse.

        Args:
            connection_id: Connection identifier

        Returns:
            Ban record if connection was banned, None otherwise
        """
        conn_stat = self._conn_stats.get(connection_id)
        if not conn_stat:
            return None

        now = time.time()
        conn_stat.auth_failures.append(now)

        # Prune old failures
        cutoff = now - 60.0
        while conn_stat.auth_failures and conn_stat.auth_failures[0] < cutoff:
            conn_stat.auth_failures.popleft()

        # Check rate limit
        if len(conn_stat.auth_failures) >= self._config.max_auth_failures_per_min:
            return self._create_ban(
                conn_stat.ip,
                BanReason.AUTH_FAILURE_SPAM.value,
                f"Auth failures: {len(conn_stat.auth_failures)}/min",
            )

        return None

    def _create_ban(self, ip: str, reason: str, details: str) -> Ban:
        """
        Create and store a new ban.

        Args:
            ip: IP address
            reason: Ban reason
            details: Human-readable details

        Returns:
            Ban record
        """
        now = datetime.utcnow()

        # Get previous strike count
        prev_ban = self._db.fetchone(
            "SELECT MAX(strike_count) as max_strikes FROM bans WHERE ip = ?",
            (ip,),
        )
        strike_count = (prev_ban["max_strikes"] or 0) + 1 if prev_ban else 1

        # Calculate duration with exponential backoff
        base = self._config.ban_base_duration_sec
        factor = self._config.ban_escalation_factor
        duration_sec = min(
            base * (factor ** (strike_count - 1)),
            self._config.ban_max_duration_sec,
        )
        expires_at = now + timedelta(seconds=duration_sec)

        # Create ban
        ban = Ban(
            ip=ip,
            reason=reason,
            created_at=now,
            expires_at=expires_at,
            strike_count=strike_count,
        )

        # Store in DB
        self._db.execute(
            """
            INSERT INTO bans (ip, reason, created_at, expires_at, strike_count)
            VALUES (?, ?, ?, ?, ?)
            """,
            (ip, reason, now, expires_at, strike_count),
        )
        self._db.commit()

        # Add to active bans
        self._active_bans[ip] = ban

        self._log.warning(
            f"Banned IP {ip} for {duration_sec}s: {reason} ({details}) "
            f"[strike {strike_count}]"
        )

        return ban

    def add_manual_ban(self, ip: str, duration_minutes: int, reason: str) -> Ban:
        """
        Manually ban an IP.

        Args:
            ip: IP address
            duration_minutes: Ban duration in minutes
            reason: Ban reason

        Returns:
            Ban record
        """
        now = datetime.utcnow()
        expires_at = now + timedelta(minutes=duration_minutes)

        ban = Ban(
            ip=ip,
            reason=BanReason.MANUAL.value,
            created_at=now,
            expires_at=expires_at,
            strike_count=1,
        )

        # Store in DB
        self._db.execute(
            """
            INSERT INTO bans (ip, reason, created_at, expires_at, strike_count)
            VALUES (?, ?, ?, ?, ?)
            """,
            (ip, f"manual: {reason}", now, expires_at, 1),
        )
        self._db.commit()

        # Add to active bans
        self._active_bans[ip] = ban

        self._log.info(f"Manually banned IP {ip} for {duration_minutes} minutes: {reason}")

        return ban

    def remove_ban(self, ip: str) -> bool:
        """
        Remove a ban for an IP.

        Args:
            ip: IP address

        Returns:
            True if ban was removed, False if not found
        """
        if ip not in self._active_bans:
            return False

        # Remove from DB (set expires_at to now)
        now = datetime.utcnow()
        self._db.execute(
            "UPDATE bans SET expires_at = ? WHERE ip = ? AND expires_at > ?",
            (now, ip, now),
        )
        self._db.commit()

        # Remove from cache
        del self._active_bans[ip]

        self._log.info(f"Removed ban for IP {ip}")

        return True

    def list_bans(self, active_only: bool = True) -> list[Ban]:
        """
        List bans.

        Args:
            active_only: If True, only return active bans

        Returns:
            List of Ban records
        """
        if active_only:
            now = datetime.utcnow()
            rows = self._db.fetchall(
                "SELECT ip, reason, created_at, expires_at, strike_count FROM bans WHERE expires_at > ? ORDER BY created_at DESC",
                (now,),
            )
        else:
            rows = self._db.fetchall(
                "SELECT ip, reason, created_at, expires_at, strike_count FROM bans ORDER BY created_at DESC"
            )

        bans = []
        for row in rows:
            bans.append(
                Ban(
                    ip=row["ip"],
                    reason=row["reason"],
                    created_at=datetime.fromisoformat(row["created_at"]),
                    expires_at=datetime.fromisoformat(row["expires_at"]),
                    strike_count=row["strike_count"],
                )
            )

        return bans

    def clear_expired_bans(self) -> int:
        """
        Clear expired bans from cache and database.

        Returns:
            Number of bans cleared
        """
        now = datetime.utcnow()

        # Remove from cache
        expired_ips = [
            ip for ip, ban in self._active_bans.items() if ban.expires_at <= now
        ]
        for ip in expired_ips:
            del self._active_bans[ip]

        # Clean up from DB (optional, for housekeeping)
        # We keep expired bans in DB for history/analytics

        self._log.debug(f"Cleared {len(expired_ips)} expired bans from cache")

        return len(expired_ips)

    def get_connection_stats(self, connection_id: str) -> Optional[dict]:
        """Get statistics for a connection."""
        conn_stat = self._conn_stats.get(connection_id)
        if not conn_stat:
            return None

        return {
            "connection_id": connection_id,
            "ip": conn_stat.ip,
            "connected_at": conn_stat.connected_at,
            "total_submits": conn_stat.total_submits,
            "invalid_submits": conn_stat.invalid_submits,
            "stale_submits": conn_stat.stale_submits,
            "invalid_ratio": (
                conn_stat.invalid_submits / conn_stat.total_submits
                if conn_stat.total_submits > 0
                else 0.0
            ),
            "recent_submit_rate": len(conn_stat.submit_timestamps) / 60.0,  # per second
            "auth_failures": len(conn_stat.auth_failures),
        }

    def get_ip_stats(self, ip: str) -> Optional[dict]:
        """Get statistics for an IP."""
        ip_stat = self._ip_stats.get(ip)
        if not ip_stat:
            return None

        return {
            "ip": ip,
            "connection_count": ip_stat.connection_count,
            "recent_connections": len(ip_stat.new_connection_timestamps),
            "last_connection_at": ip_stat.last_connection_at,
        }
