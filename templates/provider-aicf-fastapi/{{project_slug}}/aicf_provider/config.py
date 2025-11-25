"""
Configuration loader for the AICF provider template (framework-agnostic).

- Pure standard library (safe to import anywhere).
- Reads environment variables once and produces a small, typed config object.
- Performs light validation (ports, URLs, booleans, durations).

Typical usage (in your FastAPI app entrypoint):

    from aicf_provider.config import load_config
    cfg = load_config()
    app = make_app(cfg)

Environment variables (primary)
-------------------------------
- AICF_PROVIDER_ID      (required)  — stable ID for this provider instance
- AICF_CAP_AI           (optional)  — "1/true/yes/on" to enable AI capability
- AICF_CAP_QUANTUM      (optional)  — "1/true/yes/on" to enable Quantum capability
- AICF_QUEUE_URL        (optional)  — broker/queue base URL used by your worker, if any
- RPC_URL               (required)  — Animica node RPC URL (http/https/ws/wss accepted)
- LOG_LEVEL             (optional)  — debug|info|warning|error|critical (default: info)

Operational knobs (secondary)
-----------------------------
- PROVIDER_HOST         (optional)  — bind host for HTTP server (default: 0.0.0.0)
- PROVIDER_PORT         (optional)  — bind port (default: 8080)
- REQUEST_TIMEOUT       (optional)  — e.g. "30s", "500ms", "2m" (default: 30s)
- QUEUE_POLL_INTERVAL   (optional)  — e.g. "250ms", "1s" (default: 500ms)
- MAX_CONCURRENT_JOBS   (optional)  — int ≥ 1 (default: 2)
- METRICS_ENABLED       (optional)  — "1/true/yes" to enable Prometheus (default: 1)
- METRICS_HOST          (optional)  — bind host for metrics server (default: 0.0.0.0)
- METRICS_PORT          (optional)  — bind port for metrics server (default: 9000)
- CORS_ALLOW_ORIGINS    (optional)  — comma-separated origins, e.g. "https://a.dev,https://b.dev"

Notes
-----
- This module *does not* configure logging handlers; see your app entrypoint.
- Keep the set of env keys in sync with ``aicf_provider.__init__.ENV`` where applicable.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Final, Iterable, List, Mapping, Optional, Tuple
from urllib.parse import urlparse
import os
import re

# Re-use canonical names exported by the package (shared with other modules).
try:
    # Optional import to avoid import cycles in unusual setups.
    from . import ENV as ENV_CANON
except Exception:  # pragma: no cover
    ENV_CANON = {
        "PROVIDER_ID": "AICF_PROVIDER_ID",
        "CAP_AI": "AICF_CAP_AI",
        "CAP_QUANTUM": "AICF_CAP_QUANTUM",
        "QUEUE_URL": "AICF_QUEUE_URL",
        "RPC_URL": "RPC_URL",
        "LOG_LEVEL": "LOG_LEVEL",
    }


# ----------------------------- Exceptions ------------------------------------


class ConfigError(ValueError):
    """Raised when required configuration is missing or invalid."""


# ----------------------------- Parsers ---------------------------------------


_TRUE_SET: Final[set[str]] = {"1", "true", "t", "yes", "y", "on"}
_FALSE_SET: Final[set[str]] = {"0", "false", "f", "no", "n", "off"}
_DUR_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*(?P<num>\d+(\.\d+)?)(?P<unit>ms|s|m|h)?\s*$", re.IGNORECASE
)


def _getenv(key: str, default: Optional[str] = None, env: Mapping[str, str] | None = None) -> Optional[str]:
    """Thin wrapper to allow injection during tests."""
    source = env or os.environ
    return source.get(key, default)


def parse_bool(val: Optional[str], *, default: bool = False) -> bool:
    if val is None or val == "":
        return default
    s = val.strip().lower()
    if s in _TRUE_SET:
        return True
    if s in _FALSE_SET:
        return False
    raise ConfigError(f"Invalid boolean value: {val!r}")


def parse_int(val: Optional[str], *, name: str, minimum: Optional[int] = None, maximum: Optional[int] = None, default: Optional[int] = None) -> int:
    if val is None or val == "":
        if default is None:
            raise ConfigError(f"Missing required integer for {name}")
        out = default
    else:
        try:
            out = int(val, 10)
        except Exception as e:
            raise ConfigError(f"Invalid integer for {name}: {val!r}") from e
    if minimum is not None and out < minimum:
        raise ConfigError(f"{name} must be ≥ {minimum}, got {out}")
    if maximum is not None and out > maximum:
        raise ConfigError(f"{name} must be ≤ {maximum}, got {out}")
    return out


def parse_duration(val: Optional[str], *, default_seconds: float) -> float:
    """
    Parse a human-ish duration string into seconds (float).

    Accepted suffixes: "ms", "s", "m", "h". Examples: "250ms", "2s", "1.5m", "1h".
    Bare numbers are seconds. Returns seconds as float.
    """
    if val is None or val == "":
        return float(default_seconds)
    m = _DUR_RE.match(val)
    if not m:
        raise ConfigError(f"Invalid duration: {val!r}")
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
    # Should not happen due to regex
    raise ConfigError(f"Unsupported duration unit in {val!r}")


def validate_url(u: Optional[str], *, name: str, allow_empty: bool = False, allowed_schemes: Iterable[str] = ("http", "https", "ws", "wss")) -> Optional[str]:
    if (u is None or u.strip() == "") and allow_empty:
        return None
    if not u:
        raise ConfigError(f"Missing required URL for {name}")
    parsed = urlparse(u)
    if parsed.scheme.lower() not in set(allowed_schemes):
        raise ConfigError(f"{name} must use one of {sorted(set(allowed_schemes))}; got scheme '{parsed.scheme}'")
    if not parsed.netloc:
        raise ConfigError(f"{name} must include host:port — got {u!r}")
    return u


def parse_csv(val: Optional[str]) -> List[str]:
    if not val:
        return []
    return [item.strip() for item in val.split(",") if item.strip()]


def normalize_log_level(val: Optional[str], default: str = "info") -> str:
    level = (val or default).strip().lower()
    table = {
        "debug": "DEBUG",
        "info": "INFO",
        "warning": "WARNING",
        "warn": "WARNING",
        "error": "ERROR",
        "critical": "CRITICAL",
        "fatal": "CRITICAL",
    }
    if level not in table:
        raise ConfigError(f"Unknown LOG_LEVEL: {val!r}")
    return table[level]


# ----------------------------- Model -----------------------------------------


@dataclass(slots=True)
class ProviderConfig:
    # Identity & capabilities
    provider_id: str
    cap_ai: bool
    cap_quantum: bool

    # Endpoints
    rpc_url: str
    queue_url: Optional[str] = None

    # Server bind
    host: str = "0.0.0.0"
    port: int = 8080

    # Ops knobs
    log_level: str = "INFO"
    request_timeout_s: float = 30.0
    queue_poll_interval_s: float = 0.5
    max_concurrent_jobs: int = 2

    # Metrics
    metrics_enabled: bool = True
    metrics_host: str = "0.0.0.0"
    metrics_port: int = 9000

    # Web
    cors_allow_origins: List[str] = field(default_factory=list)

    # -------- Derived helpers (no heavy imports) --------

    @property
    def capabilities(self) -> Mapping[str, bool]:
        """Return a compact dict suitable for /version or IDENTIFY-like endpoints."""
        return {"ai": self.cap_ai, "quantum": self.cap_quantum}

    def summary_lines(self) -> Tuple[str, ...]:
        """Human-oriented summary for logs."""
        caps = ",".join(k for k, v in self.capabilities.items() if v) or "none"
        cors = ", ".join(self.cors_allow_origins) if self.cors_allow_origins else "disabled"
        qurl = self.queue_url or "(none)"
        return (
            f"provider_id={self.provider_id}",
            f"capabilities={caps}",
            f"rpc_url={self.rpc_url}",
            f"queue_url={qurl}",
            f"bind={self.host}:{self.port}",
            f"log_level={self.log_level}",
            f"request_timeout_s={self.request_timeout_s:g}",
            f"queue_poll_interval_s={self.queue_poll_interval_s:g}",
            f"max_concurrent_jobs={self.max_concurrent_jobs}",
            f"metrics={'on' if self.metrics_enabled else 'off'}@{self.metrics_host}:{self.metrics_port}",
            f"cors_allow_origins={cors}",
        )

    def redact_for_logs(self) -> dict:
        """Return a dict representation with potentially sensitive values sanitized."""
        d = asdict(self)
        # No secrets here by default; example left for future additions:
        # if 'queue_url' in d and d['queue_url']:
        #     d['queue_url'] = _redact_url(d['queue_url'])
        return d

    # -------------- Construction --------------

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "ProviderConfig":
        E = env or os.environ  # alias

        provider_id = _getenv(ENV_CANON["PROVIDER_ID"], env=E)
        if not provider_id:
            raise ConfigError(f"Missing {ENV_CANON['PROVIDER_ID']}")

        rpc_url = validate_url(_getenv(ENV_CANON["RPC_URL"], env=E), name="RPC_URL")

        cap_ai = parse_bool(_getenv(ENV_CANON["CAP_AI"], env=E), default=False)
        cap_quantum = parse_bool(_getenv(ENV_CANON["CAP_QUANTUM"], env=E), default=False)

        queue_url = validate_url(
            _getenv(ENV_CANON["QUEUE_URL"], env=E),
            name="AICF_QUEUE_URL",
            allow_empty=True,
            allowed_schemes=("http", "https"),
        )

        host = _getenv("PROVIDER_HOST", "0.0.0.0", env=E) or "0.0.0.0"
        port = parse_int(_getenv("PROVIDER_PORT", env=E), name="PROVIDER_PORT", minimum=1, maximum=65535, default=8080)

        log_level = normalize_log_level(_getenv(ENV_CANON["LOG_LEVEL"], env=E), default="info")

        request_timeout_s = parse_duration(_getenv("REQUEST_TIMEOUT", env=E), default_seconds=30.0)
        queue_poll_interval_s = parse_duration(_getenv("QUEUE_POLL_INTERVAL", env=E), default_seconds=0.5)
        max_concurrent_jobs = parse_int(_getenv("MAX_CONCURRENT_JOBS", env=E), name="MAX_CONCURRENT_JOBS", minimum=1, default=2)

        metrics_enabled = parse_bool(_getenv("METRICS_ENABLED", env=E), default=True)
        metrics_host = _getenv("METRICS_HOST", "0.0.0.0", env=E) or "0.0.0.0"
        metrics_port = parse_int(_getenv("METRICS_PORT", env=E), name="METRICS_PORT", minimum=1, maximum=65535, default=9000)

        cors_allow_origins = parse_csv(_getenv("CORS_ALLOW_ORIGINS", env=E))

        return cls(
            provider_id=provider_id,
            cap_ai=cap_ai,
            cap_quantum=cap_quantum,
            rpc_url=rpc_url,  # type: ignore[arg-type]
            queue_url=queue_url,
            host=host,
            port=port,
            log_level=log_level,
            request_timeout_s=request_timeout_s,
            queue_poll_interval_s=queue_poll_interval_s,
            max_concurrent_jobs=max_concurrent_jobs,
            metrics_enabled=metrics_enabled,
            metrics_host=metrics_host,
            metrics_port=metrics_port,
            cors_allow_origins=cors_allow_origins,
        )


# ----------------------------- Public API ------------------------------------


def load_config(env: Mapping[str, str] | None = None) -> ProviderConfig:
    """
    Load and validate configuration from the given mapping (or os.environ).

    Raises:
        ConfigError — if required settings are missing or invalid.
    """
    return ProviderConfig.from_env(env=env)


# ----------------------------- CLI debug -------------------------------------


def _render_box(lines: Iterable[str]) -> str:
    longest = max((len(s) for s in lines), default=0)
    top = "┌" + "─" * (longest + 2) + "┐"
    bot = "└" + "─" * (longest + 2) + "┘"
    body = "\n".join("│ " + s.ljust(longest) + " │" for s in lines)
    return f"{top}\n{body}\n{bot}"


if __name__ == "__main__":  # pragma: no cover
    try:
        cfg = load_config()
        print("Config loaded OK.")
        print(_render_box(cfg.summary_lines()))
        # Print machine-friendly representation (useful in smoke scripts)
        import json
        print(json.dumps(cfg.redact_for_logs(), indent=2, sort_keys=True))
    except ConfigError as e:
        print(f"Configuration error: {e}")
        raise SystemExit(2)
