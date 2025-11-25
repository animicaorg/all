"""
Data Availability (DA) configuration.

This module defines the configuration surface for the DA subsystem:
- Namespaces (width and reserved ranges)
- Erasure coding parameters (Reed–Solomon k/n and share size)
- Store locations (filesystem/SQLite), GC retention
- HTTP API host/port & CORS
- Basic limits and sampler targets

All fields have sensible defaults and can be overridden via environment
variables. Nothing here imports heavy dependencies.

Environment variables (all optional):

  # API
  ANIMICA_DA_HOST=127.0.0.1
  ANIMICA_DA_PORT=8648
  ANIMICA_DA_CORS=https://studio.animica.dev,https://local.animica.dev

  # Storage
  ANIMICA_DA_STORAGE_DIR=./data/da
  ANIMICA_DA_SQLITE_PATH=./data/da/da.sqlite3
  ANIMICA_DA_GC_RETENTION=2048          # in blocks

  # Namespaces
  ANIMICA_DA_NS_BYTES=2                 # 1..8
  ANIMICA_DA_NS_RESERVED_LOW=0x0000-0x00FF
  ANIMICA_DA_NS_RESERVED_HIGH=0xFF00-0xFFFF
  ANIMICA_DA_NS_DEFAULT_USER=0x0100

  # Erasure coding & sizes
  ANIMICA_DA_K=64
  ANIMICA_DA_N=128
  ANIMICA_DA_SHARE_SIZE=4096            # bytes (supports KiB/MiB suffixes too)
  ANIMICA_DA_MAX_BLOB=8MiB
  ANIMICA_DA_POST_MAX=9MiB              # HTTP POST limit (blob + envelope)

  # Sampling targets
  ANIMICA_DA_P_FAIL=2^-40               # or float like 1e-12
  ANIMICA_DA_MIN_SAMPLES=60
  ANIMICA_DA_MAX_SAMPLES=256
  ANIMICA_DA_SAMPLER_TIMEOUT_MS=1500    # per-sample network timeout

"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, asdict
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Tuple


# ------------------------------- helpers ------------------------------------


_SIZE_RE = re.compile(
    r"^\s*(?P<num>(?:\d+)(?:\.\d+)?)\s*(?P<unit>bytes?|b|kb|kib|mb|mib|gb|gib)?\s*$",
    re.IGNORECASE,
)


def _parse_size(value: str, *, default: int) -> int:
    """Parse human sizes like '4096', '4KiB', '8MB' → bytes."""
    if not value:
        return default
    m = _SIZE_RE.match(value)
    if not m:
        # allow hex for exact byte sizes
        v = value.strip().lower()
        if v.startswith("0x"):
            return int(v, 16)
        raise ValueError(f"Invalid size: {value!r}")
    num = float(m.group("num"))
    unit = (m.group("unit") or "b").lower()
    mult = 1
    if unit in ("b", "byte", "bytes"):
        mult = 1
    elif unit in ("kb",):
        mult = 1000
    elif unit in ("kib",):
        mult = 1024
    elif unit in ("mb",):
        mult = 1000**2
    elif unit in ("mib",):
        mult = 1024**2
    elif unit in ("gb",):
        mult = 1000**3
    elif unit in ("gib",):
        mult = 1024**3
    else:
        raise ValueError(f"Unknown unit in size: {value!r}")
    return int(num * mult)


def _parse_probability(s: str, *, default: float) -> float:
    """
    Parse '2^-40' or '1e-12' or '0.000000000001' to a float probability.
    """
    if not s:
        return default
    s = s.strip().lower()
    pow_match = re.fullmatch(r"2\^-\s*(\d+)", s)
    if pow_match:
        exp = int(pow_match.group(1))
        return 2.0 ** (-exp)
    try:
        return float(s)
    except ValueError as e:
        raise ValueError(f"Invalid probability: {s!r}") from e


def _getenv(key: str, default: str | None = None) -> str | None:
    v = os.environ.get(key)
    return v if v is not None and v.strip() != "" else default


def _getenv_int(key: str, default: int) -> int:
    v = _getenv(key)
    if v is None:
        return default
    base = 10
    vv = v.strip().lower()
    if vv.startswith("0x"):
        base = 16
    try:
        return int(v, base)
    except Exception as e:
        raise ValueError(f"Invalid int for {key}: {v!r}") from e


def _split_csv(s: str | None) -> List[str]:
    if not s:
        return []
    return [x.strip() for x in s.split(",") if x.strip()]


def _parse_ns_range(s: str, ns_bytes: int) -> Tuple[int, int]:
    """
    Parse '0x0000-0x00FF' or '0-255' into (lo, hi) inclusive.
    """
    parts = s.split("-")
    if len(parts) != 2:
        raise ValueError(f"Invalid namespace range {s!r}")
    lo, hi = parts[0].strip(), parts[1].strip()
    lo_v = int(lo, 16) if lo.lower().startswith("0x") else int(lo, 10)
    hi_v = int(hi, 16) if hi.lower().startswith("0x") else int(hi, 10)
    if not (0 <= lo_v <= hi_v <= (1 << (8 * ns_bytes)) - 1):
        raise ValueError(f"Namespace range out of bounds: {s!r}")
    return lo_v, hi_v


# ------------------------------- config -------------------------------------


@dataclass(frozen=True)
class NamespaceConfig:
    """
    Namespace identifier width and reserved ranges.

    We pick 2 bytes (16-bit) by default:
      - reserved_low:   0x0000..0x00FF   (system/protocol)
      - user_default:   0x0100
      - reserved_high:  0xFF00..0xFFFF   (future/reserved)
    """
    id_bytes: int = 2
    reserved_low: Tuple[int, int] = (0x0000, 0x00FF)
    reserved_high: Tuple[int, int] = (0xFF00, 0xFFFF)
    default_user_ns: int = 0x0100

    def validate(self) -> None:
        max_id = (1 << (8 * self.id_bytes)) - 1
        lo0, lo1 = self.reserved_low
        hi0, hi1 = self.reserved_high
        if not (1 <= self.id_bytes <= 8):
            raise ValueError("id_bytes must be in 1..8")
        if not (0 <= lo0 <= lo1 <= max_id):
            raise ValueError("reserved_low out of range")
        if not (0 <= hi0 <= hi1 <= max_id):
            raise ValueError("reserved_high out of range")
        if not (0 <= self.default_user_ns <= max_id):
            raise ValueError("default_user_ns out of range")
        # Ensure no overlap between low and high reserved spans
        if (lo0 <= hi0 <= lo1) or (hi0 <= lo0 <= hi1):
            raise ValueError("reserved ranges overlap")


@dataclass(frozen=True)
class ErasureConfig:
    """
    Reed–Solomon parameters for DA blob shares.

    - k: data shards
    - n: total shards (data + parity)
    - share_size: bytes per share (payload per leaf before NMT framing)
    - max_blob_bytes: upper bound for a single blob (pre-encoding)
    """
    k: int = 64
    n: int = 128
    share_size: int = 4096
    max_blob_bytes: int = 8 * 1024 * 1024

    def validate(self) -> None:
        if not (1 <= self.k < self.n <= 1_024):
            raise ValueError("Require 1 <= k < n <= 1024")
        if self.share_size <= 0 or self.share_size % 256 != 0:
            raise ValueError("share_size must be positive and a multiple of 256 bytes")
        if self.max_blob_bytes < self.share_size:
            raise ValueError("max_blob_bytes must be >= share_size")


@dataclass(frozen=True)
class StoreConfig:
    """
    Persistent store configuration.

    - storage_dir: base directory for blobs and indices (content-addressed)
    - sqlite_path: path to SQLite DB for metadata and indexes
    - gc_retention_blocks: how many blocks to keep pinned by default
    """
    storage_dir: Path = Path("./data/da")
    sqlite_path: Path = Path("./data/da/da.sqlite3")
    gc_retention_blocks: int = 2048

    def validate(self) -> None:
        if self.gc_retention_blocks < 0:
            raise ValueError("gc_retention_blocks must be >= 0")


@dataclass(frozen=True)
class ApiConfig:
    """
    HTTP API configuration for the DA retrieval service.
    """
    host: str = "127.0.0.1"
    port: int = 8648
    cors_allow_origins: Tuple[str, ...] = field(default_factory=tuple)

    def validate(self) -> None:
        if not (0 < self.port < 65536):
            raise ValueError("port must be 1..65535")


@dataclass(frozen=True)
class LimitsConfig:
    """
    Request/response limits and simple rate guidance.

    - post_max_bytes: maximum accepted POST payload (blob + envelope)
    - rate_rps_hint: global ingress guidance (actual rate limiting is in the API layer)
    """
    post_max_bytes: int = 9 * 1024 * 1024
    rate_rps_hint: int = 200

    def validate(self) -> None:
        if self.post_max_bytes <= 0:
            raise ValueError("post_max_bytes must be > 0")
        if self.rate_rps_hint <= 0:
            raise ValueError("rate_rps_hint must be > 0")


@dataclass(frozen=True)
class SamplingConfig:
    """
    Data Availability Sampling (DAS) client defaults.

    - p_fail_target: acceptable false-accept probability
    - min_samples/max_samples: clamp sampler effort per blob (or per window)
    - sample_timeout_ms: per-sample network timeout
    """
    p_fail_target: float = 2.0 ** -40
    min_samples: int = 60
    max_samples: int = 256
    sample_timeout_ms: int = 1500

    def validate(self) -> None:
        if not (0.0 < self.p_fail_target < 1.0):
            raise ValueError("p_fail_target must be between 0 and 1")
        if not (1 <= self.min_samples <= self.max_samples):
            raise ValueError("Require 1 <= min_samples <= max_samples")
        if self.sample_timeout_ms <= 0:
            raise ValueError("sample_timeout_ms must be > 0")


@dataclass(frozen=True)
class DAConfig:
    """
    Top-level DA configuration.
    """
    namespaces: NamespaceConfig = field(default_factory=NamespaceConfig)
    erasure: ErasureConfig = field(default_factory=ErasureConfig)
    store: StoreConfig = field(default_factory=StoreConfig)
    api: ApiConfig = field(default_factory=ApiConfig)
    limits: LimitsConfig = field(default_factory=LimitsConfig)
    sampling: SamplingConfig = field(default_factory=SamplingConfig)

    def validate(self) -> None:
        self.namespaces.validate()
        self.erasure.validate()
        self.store.validate()
        self.api.validate()
        self.limits.validate()
        self.sampling.validate()

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


# ------------------------------- loader -------------------------------------


def _load_from_env() -> DAConfig:
    # Namespaces
    ns_bytes = _getenv_int("ANIMICA_DA_NS_BYTES", 2)
    ns_low = _getenv("ANIMICA_DA_NS_RESERVED_LOW", "0x0000-0x00FF")
    ns_high = _getenv("ANIMICA_DA_NS_RESERVED_HIGH", "0xFF00-0xFFFF")
    ns_default_user = _getenv_int("ANIMICA_DA_NS_DEFAULT_USER", 0x0100)
    ns_cfg = NamespaceConfig(
        id_bytes=ns_bytes,
        reserved_low=_parse_ns_range(ns_low, ns_bytes),
        reserved_high=_parse_ns_range(ns_high, ns_bytes),
        default_user_ns=ns_default_user,
    )

    # Erasure
    k = _getenv_int("ANIMICA_DA_K", 64)
    n = _getenv_int("ANIMICA_DA_N", 128)
    share_size = _parse_size(_getenv("ANIMICA_DA_SHARE_SIZE", "4096"), default=4096)
    max_blob = _parse_size(_getenv("ANIMICA_DA_MAX_BLOB", "8MiB"), default=8 * 1024 * 1024)
    erasure_cfg = ErasureConfig(k=k, n=n, share_size=share_size, max_blob_bytes=max_blob)

    # Store
    storage_dir = Path(_getenv("ANIMICA_DA_STORAGE_DIR", "./data/da")).resolve()
    sqlite_path = Path(_getenv("ANIMICA_DA_SQLITE_PATH", str(storage_dir / "da.sqlite3"))).resolve()
    gc_ret = _getenv_int("ANIMICA_DA_GC_RETENTION", 2048)
    store_cfg = StoreConfig(storage_dir=storage_dir, sqlite_path=sqlite_path, gc_retention_blocks=gc_ret)

    # API
    host = _getenv("ANIMICA_DA_HOST", "127.0.0.1") or "127.0.0.1"
    port = _getenv_int("ANIMICA_DA_PORT", 8648)
    cors = tuple(_split_csv(_getenv("ANIMICA_DA_CORS", "")))
    api_cfg = ApiConfig(host=host, port=port, cors_allow_origins=cors)

    # Limits
    post_max = _parse_size(_getenv("ANIMICA_DA_POST_MAX", "9MiB"), default=9 * 1024 * 1024)
    limits_cfg = LimitsConfig(post_max_bytes=post_max)

    # Sampling
    p_fail = _parse_probability(_getenv("ANIMICA_DA_P_FAIL", "2^-40"), default=2.0 ** -40)
    min_s = _getenv_int("ANIMICA_DA_MIN_SAMPLES", 60)
    max_s = _getenv_int("ANIMICA_DA_MAX_SAMPLES", 256)
    tout = _getenv_int("ANIMICA_DA_SAMPLER_TIMEOUT_MS", 1500)
    sampling_cfg = SamplingConfig(p_fail_target=p_fail, min_samples=min_s, max_samples=max_s, sample_timeout_ms=tout)

    cfg = DAConfig(
        namespaces=ns_cfg,
        erasure=erasure_cfg,
        store=store_cfg,
        api=api_cfg,
        limits=limits_cfg,
        sampling=sampling_cfg,
    )
    cfg.validate()
    return cfg


@lru_cache(maxsize=1)
def get_config() -> DAConfig:
    """
    Load and validate configuration (cached). Clear the cache in tests
    via `get_config.cache_clear()` to observe env changes.
    """
    return _load_from_env()


# Provide a convenient module-level constant for consumers that don't need
# dynamic reloading.
CONFIG: DAConfig = get_config()


# Pretty-print helper (useful in CLIs)
def format_config(cfg: DAConfig | None = None) -> str:
    cfg = cfg or get_config()
    d = cfg.to_dict()
    lines: List[str] = []

    def kv(prefix: str, k: str, v: object) -> None:
        lines.append(f"{prefix}{k}: {v}")

    # Namespaces
    ns = d["namespaces"]  # type: ignore[assignment]
    kv("", "namespaces.id_bytes", ns["id_bytes"])  # type: ignore[index]
    kv("", "namespaces.reserved_low", ns["reserved_low"])
    kv("", "namespaces.reserved_high", ns["reserved_high"])
    kv("", "namespaces.default_user_ns", hex(ns["default_user_ns"]))  # type: ignore[index]

    # Erasure
    er = d["erasure"]  # type: ignore[assignment]
    kv("", "erasure.k", er["k"])
    kv("", "erasure.n", er["n"])
    kv("", "erasure.share_size", er["share_size"])
    kv("", "erasure.max_blob_bytes", er["max_blob_bytes"])

    # Store
    st = d["store"]  # type: ignore[assignment]
    kv("", "store.storage_dir", st["storage_dir"])
    kv("", "store.sqlite_path", st["sqlite_path"])
    kv("", "store.gc_retention_blocks", st["gc_retention_blocks"])

    # API
    api = d["api"]  # type: ignore[assignment]
    kv("", "api.host", api["host"])
    kv("", "api.port", api["port"])
    kv("", "api.cors_allow_origins", api["cors_allow_origins"])

    # Limits
    lm = d["limits"]  # type: ignore[assignment]
    kv("", "limits.post_max_bytes", lm["post_max_bytes"])
    kv("", "limits.rate_rps_hint", lm["rate_rps_hint"])

    # Sampling
    sp = d["sampling"]  # type: ignore[assignment]
    kv("", "sampling.p_fail_target", sp["p_fail_target"])
    kv("", "sampling.min_samples", sp["min_samples"])
    kv("", "sampling.max_samples", sp["max_samples"])
    kv("", "sampling.sample_timeout_ms", sp["sample_timeout_ms"])

    return "\n".join(lines)


__all__ = [
    "NamespaceConfig",
    "ErasureConfig",
    "StoreConfig",
    "ApiConfig",
    "LimitsConfig",
    "SamplingConfig",
    "DAConfig",
    "get_config",
    "CONFIG",
    "format_config",
]
