"""
Configuration loader for the {{ project_slug }} Indexer Lite.

Design goals
------------
- **Zero extra dependencies** (no pydantic/python-dotenv); pure stdlib.
- **Predictable defaults** that let you point at a local devnet and go.
- **Strict validation** for URLs and types, with human-friendly errors.
- **Immutable settings** (frozen dataclass) for thread-safety and clarity.

Environment variables
---------------------
The entrypoints in this template typically load a ``.env`` file *before*
calling :func:`from_env`. This module will also (lightly) parse an adjacent
``.env`` as a convenience if present and variables are not already set.

Required-ish
~~~~~~~~~~~~
- ``RPC_URL`` (str): HTTP endpoint of the node (e.g. http://127.0.0.1:8545).
- ``CHAIN_ID`` (int): Chain id (defaults to 1337 for devnet).

Optional
~~~~~~~~
- ``WS_URL`` (str): WebSocket endpoint (e.g. ws://127.0.0.1:8546/ws).
  If omitted and ``WS_SUBSCRIBE=true``, we will attempt to derive it from
  ``RPC_URL`` by switching scheme http→ws / https→wss and appending ``/ws``.
- ``WS_SUBSCRIBE`` (bool, default: true): Enable live tail via WS.
- ``DB_PATH`` (str): SQLite file path (default: "indexer.db").
- ``DATABASE_URL`` (str): SQLAlchemy-like URL; if set, overrides DB_PATH.
  (This template ships SQLite helpers; switching to another DB is up to you.)
- ``METRICS_PORT`` (int): Optional Prometheus exporter port.
- ``BACKFILL_FROM`` (int): Inclusive block number to start historical backfill.
- ``BACKFILL_TO`` (int): Inclusive block number to stop backfill (omit for head).
- ``MAX_BATCH_SIZE`` (int, default: 500): Max blocks per HTTP batch pull.
- ``HTTP_TIMEOUT`` (duration, default: "10s"): Per-request timeout.
- ``HTTP_RETRIES`` (int, default: 3): Simple retry budget for transient HTTP.
- ``POLL_INTERVAL`` (duration, default: "2s"): Poll cadence if WS is disabled.
- ``WS_HEARTBEAT`` (duration, default: "20s"): WS ping period (client side).
- ``WS_BACKOFF_INITIAL`` (duration, default: "500ms"): WS reconnect backoff start.
- ``WS_BACKOFF_MAX`` (duration, default: "30s"): WS reconnect backoff cap.
- ``LOG_LEVEL`` (str, default: "INFO"): Root log level (INFO/DEBUG/WARN/ERROR).

Example
-------
>>> cfg = from_env()
>>> cfg.rpc_url.startswith("http")
True
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Mapping, Optional
from urllib.parse import urlparse, urlunparse

# ------------------------------- utilities --------------------------------- #

_BOOL_TRUE = {"1", "true", "yes", "on", "y", "t"}
_BOOL_FALSE = {"0", "false", "no", "off", "n", "f"}

_DURATION_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>ms|s|m|h)?\s*$", re.IGNORECASE
)


def parse_bool(value: Optional[str], default: bool = False) -> bool:
    if value is None:
        return default
    v = value.strip().lower()
    if v in _BOOL_TRUE:
        return True
    if v in _BOOL_FALSE:
        return False
    raise ValueError(f"Invalid boolean value: {value!r}")


def parse_int(value: Optional[str], default: Optional[int] = None) -> Optional[int]:
    if value is None or value == "":
        return default
    try:
        return int(value, 10)
    except Exception as e:
        raise ValueError(f"Invalid integer value: {value!r}") from e


def parse_duration(value: Optional[str], default_seconds: float) -> float:
    """
    Parse durations like "500ms", "2s", "1.5m", "1h" into seconds (float).
    """
    if value is None or value == "":
        return float(default_seconds)
    m = _DURATION_RE.match(value)
    if not m:
        raise ValueError(f"Invalid duration: {value!r}")
    num = float(m.group("num"))
    unit = (m.group("unit") or "s").lower()
    if unit == "ms":
        return num / 1000.0
    if unit == "s":
        return num
    if unit == "m":
        return num * 60.0
    if unit == "h":
        return num * 3600.0
    # Should never happen due to regex
    raise ValueError(f"Invalid duration unit in: {value!r}")


def _ensure_http_url(u: str) -> str:
    p = urlparse(u)
    if p.scheme not in {"http", "https"}:
        raise ValueError(f"RPC_URL must be http/https, got: {u!r}")
    if not p.netloc:
        raise ValueError(f"RPC_URL missing host:port: {u!r}")
    return u


def _ensure_ws_url(u: str) -> str:
    p = urlparse(u)
    if p.scheme not in {"ws", "wss"}:
        raise ValueError(f"WS_URL must be ws/wss, got: {u!r}")
    if not p.netloc:
        raise ValueError(f"WS_URL missing host:port: {u!r}")
    return u


def _derive_ws_from_http(http_url: str) -> str:
    p = urlparse(http_url)
    scheme = "wss" if p.scheme == "https" else "ws"
    # If path already looks like a WS mount (endswith /ws), reuse; else append /ws
    path = p.path.rstrip("/")
    if not path.endswith("/ws"):
        path = (path + "/ws") if path else "/ws"
    return urlunparse((scheme, p.netloc, path, "", "", ""))


def _build_db_url(env: Mapping[str, str]) -> str:
    # Prefer DATABASE_URL if present
    db_url = env.get("DATABASE_URL")
    if db_url:
        return db_url
    # Otherwise, build sqlite URL from DB_PATH (default indexer.db in CWD)
    db_path = Path(env.get("DB_PATH", "indexer.db")).expanduser().resolve()
    return f"sqlite:///{db_path}"


def _maybe_load_dotenv(dotenv_path: Path) -> None:
    """
    Minimal .env reader: assign only variables that are not already set.

    This is intentionally tiny; it supports lines like:
        KEY=value
        KEY="value with spaces"
        # comments and empty lines are ignored
    """
    try:
        if not dotenv_path.exists():
            return
        for raw in dotenv_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip("'").strip('"')
            if key and key not in os.environ:
                os.environ[key] = val
    except Exception:
        # Never fail hard due to .env parsing; entrypoints may load it explicitly.
        pass


# --------------------------------- config ---------------------------------- #


@dataclass(frozen=True)
class IndexerConfig:
    # Core connectivity
    rpc_url: str
    chain_id: int

    # Live tail (WebSocket) — may be disabled for cron/backfill runners
    ws_subscribe: bool = True
    ws_url: Optional[str] = None

    # Storage
    db_url: str = field(default_factory=lambda: _build_db_url(os.environ))

    # Observability
    metrics_port: Optional[int] = None
    log_level: str = "INFO"

    # Backfill window (inclusive); if None, fetch from node's earliest or head
    backfill_from: Optional[int] = None
    backfill_to: Optional[int] = None

    # Batch & network
    max_batch_size: int = 500
    http_timeout_s: float = 10.0
    http_retries: int = 3
    poll_interval_s: float = 2.0
    ws_heartbeat_s: float = 20.0
    ws_backoff_initial_s: float = 0.5
    ws_backoff_max_s: float = 30.0

    # ----- helpers / derived ----- #

    @property
    def ws_enabled(self) -> bool:
        return self.ws_subscribe and self.ws_url is not None

    def as_dict(self) -> Mapping[str, object]:
        # Handy for logging or passing to a UI; redact nothing here as secrets are not stored
        return {
            "rpc_url": self.rpc_url,
            "chain_id": self.chain_id,
            "ws_subscribe": self.ws_subscribe,
            "ws_url": self.ws_url,
            "db_url": self.db_url,
            "metrics_port": self.metrics_port,
            "log_level": self.log_level,
            "backfill_from": self.backfill_from,
            "backfill_to": self.backfill_to,
            "max_batch_size": self.max_batch_size,
            "http_timeout_s": self.http_timeout_s,
            "http_retries": self.http_retries,
            "poll_interval_s": self.poll_interval_s,
            "ws_heartbeat_s": self.ws_heartbeat_s,
            "ws_backoff_initial_s": self.ws_backoff_initial_s,
            "ws_backoff_max_s": self.ws_backoff_max_s,
        }


def from_env(env: Optional[Mapping[str, str]] = None) -> IndexerConfig:
    """
    Load :class:`IndexerConfig` from environment variables (with a tiny .env
    reader as a convenience). Validates URLs and types and derives WS_URL if
    needed.

    Parameters
    ----------
    env:
        Optional mapping (defaults to ``os.environ``). Useful for tests.

    Returns
    -------
    IndexerConfig

    Raises
    ------
    KeyError:
        When required variables are missing.
    ValueError:
        When a provided value is malformed (e.g., bad URL, bad int).
    """
    # Light .env support as a fallback only when using real os.environ
    if env is None:
        _maybe_load_dotenv(Path(".env"))
        env = os.environ

    rpc_url = env.get("RPC_URL")
    if not rpc_url:
        raise KeyError("RPC_URL is required (e.g., http://127.0.0.1:8545)")
    rpc_url = _ensure_http_url(rpc_url)

    chain_id = parse_int(env.get("CHAIN_ID"), default=1337)
    if chain_id is None or chain_id < 0:
        raise ValueError(
            f"CHAIN_ID must be a non-negative integer, got: {env.get('CHAIN_ID')!r}"
        )

    ws_subscribe = parse_bool(env.get("WS_SUBSCRIBE"), default=True)

    raw_ws_url = env.get("WS_URL")
    ws_url: Optional[str]
    if ws_subscribe:
        if raw_ws_url:
            ws_url = _ensure_ws_url(raw_ws_url)
        else:
            # attempt to derive from RPC_URL (add /ws and swap scheme)
            ws_url = _derive_ws_from_http(rpc_url)
    else:
        ws_url = _ensure_ws_url(raw_ws_url) if raw_ws_url else None

    db_url = _build_db_url(env)

    metrics_port = parse_int(env.get("METRICS_PORT"), default=None)
    log_level = (env.get("LOG_LEVEL") or "INFO").upper()

    backfill_from = parse_int(env.get("BACKFILL_FROM"), default=None)
    backfill_to = parse_int(env.get("BACKFILL_TO"), default=None)
    if backfill_from is not None and backfill_from < 0:
        raise ValueError("BACKFILL_FROM cannot be negative")
    if backfill_to is not None and backfill_to < 0:
        raise ValueError("BACKFILL_TO cannot be negative")
    if (backfill_from is not None and backfill_to is not None) and (
        backfill_to < backfill_from
    ):
        raise ValueError("BACKFILL_TO must be >= BACKFILL_FROM")

    max_batch_size = parse_int(env.get("MAX_BATCH_SIZE"), default=500) or 500
    if max_batch_size <= 0:
        raise ValueError("MAX_BATCH_SIZE must be positive")

    http_timeout_s = parse_duration(env.get("HTTP_TIMEOUT"), 10.0)
    http_retries = parse_int(env.get("HTTP_RETRIES"), default=3) or 3
    poll_interval_s = parse_duration(env.get("POLL_INTERVAL"), 2.0)
    ws_heartbeat_s = parse_duration(env.get("WS_HEARTBEAT"), 20.0)
    ws_backoff_initial_s = parse_duration(env.get("WS_BACKOFF_INITIAL"), 0.5)
    ws_backoff_max_s = parse_duration(env.get("WS_BACKOFF_MAX"), 30.0)

    if ws_backoff_initial_s <= 0 or ws_backoff_max_s <= 0:
        raise ValueError("WS backoff durations must be positive")
    if ws_backoff_initial_s > ws_backoff_max_s:
        raise ValueError("WS_BACKOFF_INITIAL must be <= WS_BACKOFF_MAX")

    return IndexerConfig(
        rpc_url=rpc_url,
        chain_id=chain_id,
        ws_subscribe=ws_subscribe,
        ws_url=ws_url,
        db_url=db_url,
        metrics_port=metrics_port,
        log_level=log_level,
        backfill_from=backfill_from,
        backfill_to=backfill_to,
        max_batch_size=max_batch_size,
        http_timeout_s=http_timeout_s,
        http_retries=http_retries,
        poll_interval_s=poll_interval_s,
        ws_heartbeat_s=ws_heartbeat_s,
        ws_backoff_initial_s=ws_backoff_initial_s,
        ws_backoff_max_s=ws_backoff_max_s,
    )


__all__ = [
    "IndexerConfig",
    "from_env",
    "parse_bool",
    "parse_int",
    "parse_duration",
]
