"""Shared timeout helpers for Animica CLI commands.

This module centralizes how we resolve network timeouts so commands can opt
into a single, consistent behaviour:

* Default: **no timeout** (wait indefinitely) to avoid premature failures on
  slow or loaded nodes.
* CLI flags or environment variables can still set a finite timeout.
* A value of ``0`` or strings like ``"none"``/``"off"`` disable timeouts.

Usage::

    resolved = resolve_timeout("RPC timeout", cli_value, env_var=RPC_TIMEOUT_ENV)
    async with httpx.AsyncClient(timeout=resolved):
        ...
"""

from __future__ import annotations

import os
from typing import Optional, Union

# Environment variable used by CLI commands to override timeouts.
RPC_TIMEOUT_ENV = "ANIMICA_RPC_TIMEOUT"

# Default timeout for RPC calls: None = wait indefinitely.
DEFAULT_RPC_TIMEOUT: Optional[float] = None

# Values that explicitly disable timeouts when provided via CLI/env.
_UNBOUNDED_SENTINELS = {
    "0",
    "0.0",
    "none",
    "null",
    "no",
    "off",
    "disable",
    "disabled",
    "infinite",
    "infinity",
    "unbounded",
    "unlimited",
}


def describe_timeout(default: Optional[float]) -> str:
    """Human-readable label for help strings."""

    if default is None:
        return "no timeout (wait indefinitely)"
    return f"{default} seconds"


def _normalize_timeout(raw: Union[str, float, int], label: str) -> Optional[float]:
    if isinstance(raw, str):
        value = raw.strip().lower()
        if not value:
            return None
        if value in _UNBOUNDED_SENTINELS:
            return None
        try:
            parsed = float(value)
        except ValueError as exc:  # pragma: no cover - validated by callers
            raise ValueError(f"Invalid {label} value: {raw}") from exc
    else:
        parsed = float(raw)

    if parsed < 0:
        raise ValueError(f"{label} must not be negative, got {parsed}")
    if parsed == 0:
        return None
    return parsed


def resolve_timeout(
    label: str,
    cli_value: Optional[float],
    *,
    env_var: str = RPC_TIMEOUT_ENV,
    default: Optional[float] = DEFAULT_RPC_TIMEOUT,
) -> Optional[float]:
    """Resolve timeout preference from CLI flag, environment, or default.

    The precedence order is:
      1. CLI value (if provided)
      2. Environment variable (if set and non-empty)
      3. Supplied default (defaults to unlimited)
    """

    if cli_value is not None:
        return _normalize_timeout(cli_value, label)

    env_value = os.environ.get(env_var)
    if env_value is not None and env_value.strip():
        return _normalize_timeout(env_value, label)

    return default

