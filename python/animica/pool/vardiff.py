"""
Variable Difficulty (VarDiff) implementation for mining pool.

Dynamically adjusts per-connection difficulty to maintain target share rate.
Uses EMA smoothing and hysteresis to prevent difficulty thrashing.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class VarDiffConfig:
    """Configuration for variable difficulty."""

    enabled: bool = True
    target_shares_per_min: float = 10.0  # Target share submission rate
    retarget_sec: float = 30.0  # How often to retarget
    min_difficulty: float = 0.01  # Minimum share difficulty
    max_difficulty: float = 1.0  # Maximum share difficulty
    variance_percent: float = 15.0  # ±% change threshold to trigger retarget
    smoothing_alpha: float = 0.2  # EMA smoothing factor (0-1)
    window_sec: float = 60.0  # Window for calculating share rate


@dataclass
class VarDiffState:
    """Per-connection variable difficulty state."""

    connection_id: str
    current_difficulty: float
    share_timestamps: deque[float] = field(default_factory=deque)
    last_retarget_time: float = field(default_factory=time.time)
    observed_rate_ema: float = 0.0  # EMA of observed shares/min
    retarget_count: int = 0

    def __post_init__(self):
        """Initialize with current time if not set."""
        if not hasattr(self, "last_retarget_time") or self.last_retarget_time == 0:
            self.last_retarget_time = time.time()


class VarDiffManager:
    """
    Manages variable difficulty for pool connections.

    Tracks per-connection share submission rates and adjusts difficulty
    to maintain target share rate. Uses EMA smoothing and hysteresis
    to prevent difficulty thrashing.
    """

    def __init__(
        self,
        config: VarDiffConfig,
        *,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._config = config
        self._log = logger or logging.getLogger("animica.pool.vardiff")
        self._states: dict[str, VarDiffState] = {}

    def create_state(
        self,
        connection_id: str,
        initial_difficulty: Optional[float] = None,
    ) -> VarDiffState:
        """
        Create VarDiff state for a new connection.

        Args:
            connection_id: Unique connection identifier
            initial_difficulty: Starting difficulty (default: config min)

        Returns:
            New VarDiffState
        """
        if initial_difficulty is None:
            initial_difficulty = self._config.min_difficulty

        # Clamp initial difficulty
        initial_difficulty = max(
            self._config.min_difficulty,
            min(initial_difficulty, self._config.max_difficulty),
        )

        state = VarDiffState(
            connection_id=connection_id,
            current_difficulty=initial_difficulty,
        )
        self._states[connection_id] = state

        self._log.debug(
            f"Created VarDiff state for connection {connection_id}, "
            f"initial difficulty: {initial_difficulty}"
        )

        return state

    def remove_state(self, connection_id: str) -> None:
        """Remove VarDiff state for a disconnected connection."""
        if connection_id in self._states:
            del self._states[connection_id]
            self._log.debug(f"Removed VarDiff state for connection {connection_id}")

    def get_state(self, connection_id: str) -> Optional[VarDiffState]:
        """Get VarDiff state for a connection."""
        return self._states.get(connection_id)

    def record_share(self, connection_id: str) -> None:
        """
        Record a share submission for rate tracking.

        Args:
            connection_id: Connection identifier
        """
        state = self._states.get(connection_id)
        if not state:
            self._log.warning(
                f"No VarDiff state for connection {connection_id}, cannot record share"
            )
            return

        now = time.time()
        state.share_timestamps.append(now)

        # Prune old timestamps outside window
        cutoff = now - self._config.window_sec
        while state.share_timestamps and state.share_timestamps[0] < cutoff:
            state.share_timestamps.popleft()

    def should_retarget(self, connection_id: str) -> bool:
        """
        Check if connection should be retargeted.

        Args:
            connection_id: Connection identifier

        Returns:
            True if retarget interval has elapsed
        """
        state = self._states.get(connection_id)
        if not state:
            return False

        now = time.time()
        elapsed = now - state.last_retarget_time
        return elapsed >= self._config.retarget_sec

    def calculate_new_difficulty(self, connection_id: str) -> Optional[float]:
        """
        Calculate new difficulty for a connection.

        Returns None if difficulty should not change.

        Args:
            connection_id: Connection identifier

        Returns:
            New difficulty if change needed, else None
        """
        if not self._config.enabled:
            return None

        state = self._states.get(connection_id)
        if not state:
            return None

        # Calculate observed share rate
        now = time.time()
        window_start = now - self._config.window_sec
        shares_in_window = sum(
            1 for ts in state.share_timestamps if ts >= window_start
        )

        # Convert to shares per minute
        window_minutes = self._config.window_sec / 60.0
        observed_rate = shares_in_window / window_minutes if window_minutes > 0 else 0.0

        # Update EMA
        if state.observed_rate_ema == 0.0:
            state.observed_rate_ema = observed_rate
        else:
            alpha = self._config.smoothing_alpha
            state.observed_rate_ema = (
                alpha * observed_rate + (1 - alpha) * state.observed_rate_ema
            )

        # Need enough shares to make a decision
        min_shares = max(3, int(self._config.target_shares_per_min * 0.5))
        if shares_in_window < min_shares:
            self._log.debug(
                f"Connection {connection_id}: Not enough shares yet "
                f"({shares_in_window} < {min_shares})"
            )
            return None

        # Calculate new difficulty
        target_rate = self._config.target_shares_per_min
        if state.observed_rate_ema <= 0:
            return None

        # New difficulty = old * (observed / target)
        ratio = state.observed_rate_ema / target_rate
        new_difficulty = state.current_difficulty * ratio

        # Clamp to bounds
        new_difficulty = max(
            self._config.min_difficulty,
            min(new_difficulty, self._config.max_difficulty),
        )

        # Check if change is significant enough (hysteresis)
        change_ratio = abs(new_difficulty - state.current_difficulty) / state.current_difficulty
        threshold = self._config.variance_percent / 100.0

        if change_ratio < threshold:
            self._log.debug(
                f"Connection {connection_id}: Difficulty change too small "
                f"({change_ratio:.2%} < {threshold:.2%}), skipping"
            )
            return None

        self._log.info(
            f"Connection {connection_id}: Retargeting difficulty "
            f"{state.current_difficulty:.4f} → {new_difficulty:.4f} "
            f"(observed rate: {state.observed_rate_ema:.2f} shares/min, "
            f"target: {target_rate:.2f})"
        )

        return new_difficulty

    def apply_new_difficulty(
        self,
        connection_id: str,
        new_difficulty: float,
    ) -> None:
        """
        Apply new difficulty to connection state.

        Args:
            connection_id: Connection identifier
            new_difficulty: New difficulty value
        """
        state = self._states.get(connection_id)
        if not state:
            return

        state.current_difficulty = new_difficulty
        state.last_retarget_time = time.time()
        state.retarget_count += 1

        self._log.debug(
            f"Applied new difficulty {new_difficulty:.4f} to connection {connection_id}"
        )

    def retarget(self, connection_id: str) -> Optional[float]:
        """
        Retarget difficulty for a connection if needed.

        Convenience method combining should_retarget, calculate_new_difficulty,
        and apply_new_difficulty.

        Args:
            connection_id: Connection identifier

        Returns:
            New difficulty if changed, else None
        """
        if not self.should_retarget(connection_id):
            return None

        new_difficulty = self.calculate_new_difficulty(connection_id)
        if new_difficulty is not None:
            self.apply_new_difficulty(connection_id, new_difficulty)
            return new_difficulty

        return None

    def get_stats(self, connection_id: str) -> Optional[dict]:
        """
        Get VarDiff statistics for a connection.

        Args:
            connection_id: Connection identifier

        Returns:
            Statistics dict or None
        """
        state = self._states.get(connection_id)
        if not state:
            return None

        now = time.time()
        window_start = now - self._config.window_sec
        shares_in_window = sum(
            1 for ts in state.share_timestamps if ts >= window_start
        )
        window_minutes = self._config.window_sec / 60.0
        current_rate = shares_in_window / window_minutes if window_minutes > 0 else 0.0

        return {
            "connection_id": connection_id,
            "current_difficulty": state.current_difficulty,
            "observed_rate": current_rate,
            "observed_rate_ema": state.observed_rate_ema,
            "target_rate": self._config.target_shares_per_min,
            "shares_in_window": shares_in_window,
            "retarget_count": state.retarget_count,
            "time_since_retarget": now - state.last_retarget_time,
        }
