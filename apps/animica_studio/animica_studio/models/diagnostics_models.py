"""Data models for the Diagnostics service."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Generic, Iterator, TypeVar

T = TypeVar("T")


class RingBuffer(Generic[T]):
    """A fixed-capacity ring buffer.

    Older items are silently discarded when capacity is exceeded.

    Parameters
    ----------
    capacity:
        Maximum number of items to retain.  Must be >= 1.

    Raises
    ------
    ValueError
        If *capacity* < 1.
    """

    def __init__(self, capacity: int) -> None:
        if capacity < 1:
            raise ValueError(f"RingBuffer capacity must be >= 1, got {capacity}")
        self._capacity = capacity
        self._data: deque[T] = deque(maxlen=capacity)

    def append(self, item: T) -> None:
        """Append *item*, dropping the oldest element if at capacity."""
        self._data.append(item)

    def items(self) -> list[T]:
        """Return all items as a list (oldest first)."""
        return list(self._data)

    def clear(self) -> None:
        """Remove all items."""
        self._data.clear()

    def __len__(self) -> int:
        return len(self._data)

    def __iter__(self) -> Iterator[T]:
        return iter(self._data)


@dataclass
class DiagnosticEvent:
    """A single diagnostic event captured by the Diagnostics service.

    Attributes
    ----------
    ts:
        Unix timestamp when the event occurred.
    level:
        Severity: one of ``"INFO"``, ``"WARN"``, ``"ERROR"``.
    source:
        Module or component name that generated the event.
    message:
        Human-readable description.
    context:
        Optional dict of structured data attached to the event.
    """

    ts: float
    level: str
    source: str
    message: str
    context: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def make(
        cls,
        level: str,
        source: str,
        message: str,
        context: dict[str, Any] | None = None,
    ) -> "DiagnosticEvent":
        """Create a :class:`DiagnosticEvent` with the current timestamp."""
        return cls(
            ts=time.time(),
            level=level,
            source=source,
            message=message,
            context=context or {},
        )
