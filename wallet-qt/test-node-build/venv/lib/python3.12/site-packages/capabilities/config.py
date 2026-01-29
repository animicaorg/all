"""
capabilities.config
-------------------

Feature flags, gas-cost hooks (constants), queue sizes, and safety limits for
the Animica capabilities subsystem (blob pin/get, AI/Quantum enqueue, zk.verify,
deterministic randomness, treasury hooks).

This module is dependency-light and safe to import early. It exposes:

- Dataclasses with sane defaults:
    * FeatureFlags: enable/disable individual capability providers.
    * GasCosts: base/per-unit costs charged to contract calls.
    * QueueLimits: sizing, payload caps, per-caller quotas.
    * ResultPolicy: retention/TTL and maximum result sizes.
    * SecurityLimits: global sanity ceilings (bytes, items, timeouts).
    * Config: the whole bundle.

- load_config(): build Config from environment (and optional file) with validation.
  All environment variables are optional. When provided, they override defaults.

Environment variables (prefix: ANIMICA_CAP_*)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# Feature flags
ANIMICA_CAP_ENABLE_BLOB=true|false
ANIMICA_CAP_ENABLE_AI=true|false
ANIMICA_CAP_ENABLE_QUANTUM=true|false
ANIMICA_CAP_ENABLE_ZK=true|false
ANIMICA_CAP_ENABLE_RANDOM=true|false

# Queue & payload sizing (numbers accept "KB/MB/GiB" style suffixes)
ANIMICA_CAP_QUEUE_MAX_INFLIGHT=2048
ANIMICA_CAP_QUEUE_MAX_PER_CALLER=128
ANIMICA_CAP_MAX_PAYLOAD_BYTES=1MiB
ANIMICA_CAP_MAX_RESULT_BYTES=1MiB
ANIMICA_CAP_RESULT_TTL_BLOCKS=720
ANIMICA_CAP_QUEUE_BACKPRESSURE_TARGET=0.8   # fraction (0.0..1.0)

# Gas costs (integers, "gas units")
ANIMICA_CAP_GAS_BLOB_PIN_BASE=2000
ANIMICA_CAP_GAS_BLOB_PIN_PER_KB=12
ANIMICA_CAP_GAS_AI_ENQUEUE_BASE=5000
ANIMICA_CAP_GAS_AI_UNIT=40
ANIMICA_CAP_GAS_QUANTUM_UNIT=60
ANIMICA_CAP_GAS_ZK_VERIFY_BASE=8000
ANIMICA_CAP_GAS_ZK_VERIFY_PER_BYTE=4
ANIMICA_CAP_GAS_RANDOM_BYTES_PER_32=50

# Timeouts (seconds; support "ms/s/m" suffix)
ANIMICA_CAP_ENQUEUE_TIMEOUT=5s
ANIMICA_CAP_RESULT_READ_TIMEOUT=2s

# Optional config file (JSON or YAML if PyYAML present). Env still wins.
ANIMICA_CAP_CONFIG=/path/to/capabilities.config.json

Notes
-----
- Gas constants act as *inputs* to the runtime charging model. The actual gas
  accounting happens in execution/vm_py bindings; these constants keep policy
  in one place.
- Bytes and duration parsers accept simple suffixes (KB/MB/GiB, ms/s/m).

"""

from __future__ import annotations

import dataclasses
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

# -----------------------------
# Helpers: parsing & validation
# -----------------------------

_SIZE_RE = re.compile(
    r"^\s*(?P<num>\d+)(?P<unit>kb|kib|mb|mib|gb|gib|b)?\s*$", re.IGNORECASE
)
_DUR_RE = re.compile(r"^\s*(?P<num>\d+)(?P<unit>ms|s|m)?\s*$", re.IGNORECASE)


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.getenv(name)
    return v if v is not None and v != "" else default


def _parse_bool(v: Optional[str], default: bool) -> bool:
    if v is None:
        return default
    vv = v.strip().lower()
    if vv in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if vv in {"0", "false", "f", "no", "n", "off"}:
        return False
    return default


def _parse_int(v: Optional[str], default: int) -> int:
    if v is None:
        return default
    try:
        return int(v)
    except ValueError:
        return default


def _parse_fraction(v: Optional[str], default: float) -> float:
    if v is None:
        return default
    try:
        x = float(v)
        return max(0.0, min(1.0, x))
    except ValueError:
        return default


def _parse_bytes(v: Optional[str], default: int) -> int:
    if v is None:
        return default
    m = _SIZE_RE.match(v)
    if not m:
        return default
    num = int(m.group("num"))
    unit = (m.group("unit") or "b").lower()
    if unit in ("b",):
        mul = 1
    elif unit in ("kb",):
        mul = 1000
    elif unit in ("kib",):
        mul = 1024
    elif unit in ("mb",):
        mul = 1000**2
    elif unit in ("mib",):
        mul = 1024**2
    elif unit in ("gb",):
        mul = 1000**3
    elif unit in ("gib",):
        mul = 1024**3
    else:
        mul = 1
    return num * mul


def _parse_duration_seconds(v: Optional[str], default: float) -> float:
    if v is None:
        return default
    m = _DUR_RE.match(v)
    if not m:
        return default
    num = float(m.group("num"))
    unit = (m.group("unit") or "s").lower()
    if unit == "ms":
        return num / 1000.0
    if unit == "s":
        return num
    if unit == "m":
        return num * 60.0
    return default


def _load_file_config(path: Path) -> Dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
        # Try JSON first
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        # Try YAML if available
        try:
            import yaml  # type: ignore

            data = yaml.safe_load(text)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    except Exception:
        return {}


# -----------------------------
# Dataclasses
# -----------------------------


@dataclass(frozen=True)
class FeatureFlags:
    blob: bool = True
    ai: bool = True
    quantum: bool = True
    zk: bool = True
    random: bool = True


@dataclass(frozen=True)
class GasCosts:
    # Blob pin/get
    blob_pin_base: int = 2_000
    blob_pin_per_kb: int = 12
    # AI/Quantum enqueue (unit costs correspond to normalized "work units")
    ai_enqueue_base: int = 5_000
    ai_unit: int = 40
    quantum_unit: int = 60
    # zk.verify
    zk_verify_base: int = 8_000
    zk_verify_per_byte: int = 4
    # Deterministic randomness helper
    random_bytes_per_32: int = 50


@dataclass(frozen=True)
class QueueLimits:
    max_inflight: int = 2_048  # total jobs in queue (pending+leased)
    max_per_caller: int = 128  # per-caller outstanding jobs
    enqueue_timeout_s: float = 5.0
    result_read_timeout_s: float = 2.0
    backpressure_target: float = 0.80  # fraction utilization to start shedding


@dataclass(frozen=True)
class ResultPolicy:
    ttl_blocks: int = 720  # ~one day at 2-min blocks; network-specific
    max_result_bytes: int = 1_048_576  # 1 MiB result ceiling


@dataclass(frozen=True)
class SecurityLimits:
    max_payload_bytes: int = 1_048_576  # 1 MiB input ceiling per syscall
    # Future: per-kind ceilings could be added here (e.g., max circuits/ops for quantum)


@dataclass(frozen=True)
class Config:
    features: FeatureFlags = dataclasses.field(default_factory=FeatureFlags)
    gas: GasCosts = dataclasses.field(default_factory=GasCosts)
    queue: QueueLimits = dataclasses.field(default_factory=QueueLimits)
    results: ResultPolicy = dataclasses.field(default_factory=ResultPolicy)
    limits: SecurityLimits = dataclasses.field(default_factory=SecurityLimits)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "features": dataclasses.asdict(self.features),
            "gas": dataclasses.asdict(self.gas),
            "queue": dataclasses.asdict(self.queue),
            "results": dataclasses.asdict(self.results),
            "limits": dataclasses.asdict(self.limits),
        }


# -----------------------------
# Loader
# -----------------------------


def _apply_overrides(base: Dict[str, Any], overrides: Dict[str, Any]) -> Dict[str, Any]:
    """
    Shallow merge: keys in overrides replace keys in base; dictionaries merge 1-level deep.
    """
    out = dict(base)
    for k, v in overrides.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            nv = dict(out[k])
            nv.update(v)
            out[k] = nv
        else:
            out[k] = v
    return out


def load_config(
    file_path: Optional[Path] = None, overrides: Optional[Dict[str, Any]] = None
) -> Config:
    """
    Build a Config from (defaults) ← file (JSON/YAML) ← environment ← overrides.
    """
    # Start from defaults
    d: Dict[str, Any] = Config().to_dict()

    # File config
    path_env = _env("ANIMICA_CAP_CONFIG")
    p = file_path or (Path(path_env) if path_env else None)
    if p:
        file_cfg = _load_file_config(Path(p))
        if file_cfg:
            d = _apply_overrides(d, file_cfg)

    # Environment overrides
    features = d.get("features", {})
    gas = d.get("gas", {})
    queue = d.get("queue", {})
    results = d.get("results", {})
    limits = d.get("limits", {})

    features.update(
        {
            "blob": _parse_bool(
                _env("ANIMICA_CAP_ENABLE_BLOB"), features.get("blob", True)
            ),
            "ai": _parse_bool(_env("ANIMICA_CAP_ENABLE_AI"), features.get("ai", True)),
            "quantum": _parse_bool(
                _env("ANIMICA_CAP_ENABLE_QUANTUM"), features.get("quantum", True)
            ),
            "zk": _parse_bool(_env("ANIMICA_CAP_ENABLE_ZK"), features.get("zk", True)),
            "random": _parse_bool(
                _env("ANIMICA_CAP_ENABLE_RANDOM"), features.get("random", True)
            ),
        }
    )

    gas.update(
        {
            "blob_pin_base": _parse_int(
                _env("ANIMICA_CAP_GAS_BLOB_PIN_BASE"),
                gas.get("blob_pin_base", GasCosts.blob_pin_base),
            ),
            "blob_pin_per_kb": _parse_int(
                _env("ANIMICA_CAP_GAS_BLOB_PIN_PER_KB"),
                gas.get("blob_pin_per_kb", GasCosts.blob_pin_per_kb),
            ),
            "ai_enqueue_base": _parse_int(
                _env("ANIMICA_CAP_GAS_AI_ENQUEUE_BASE"),
                gas.get("ai_enqueue_base", GasCosts.ai_enqueue_base),
            ),
            "ai_unit": _parse_int(
                _env("ANIMICA_CAP_GAS_AI_UNIT"), gas.get("ai_unit", GasCosts.ai_unit)
            ),
            "quantum_unit": _parse_int(
                _env("ANIMICA_CAP_GAS_QUANTUM_UNIT"),
                gas.get("quantum_unit", GasCosts.quantum_unit),
            ),
            "zk_verify_base": _parse_int(
                _env("ANIMICA_CAP_GAS_ZK_VERIFY_BASE"),
                gas.get("zk_verify_base", GasCosts.zk_verify_base),
            ),
            "zk_verify_per_byte": _parse_int(
                _env("ANIMICA_CAP_GAS_ZK_VERIFY_PER_BYTE"),
                gas.get("zk_verify_per_byte", GasCosts.zk_verify_per_byte),
            ),
            "random_bytes_per_32": _parse_int(
                _env("ANIMICA_CAP_GAS_RANDOM_BYTES_PER_32"),
                gas.get("random_bytes_per_32", GasCosts.random_bytes_per_32),
            ),
        }
    )

    queue.update(
        {
            "max_inflight": _parse_int(
                _env("ANIMICA_CAP_QUEUE_MAX_INFLIGHT"),
                queue.get("max_inflight", QueueLimits.max_inflight),
            ),
            "max_per_caller": _parse_int(
                _env("ANIMICA_CAP_QUEUE_MAX_PER_CALLER"),
                queue.get("max_per_caller", QueueLimits.max_per_caller),
            ),
            "enqueue_timeout_s": _parse_duration_seconds(
                _env("ANIMICA_CAP_ENQUEUE_TIMEOUT"),
                queue.get("enqueue_timeout_s", QueueLimits.enqueue_timeout_s),
            ),
            "result_read_timeout_s": _parse_duration_seconds(
                _env("ANIMICA_CAP_RESULT_READ_TIMEOUT"),
                queue.get("result_read_timeout_s", QueueLimits.result_read_timeout_s),
            ),
            "backpressure_target": _parse_fraction(
                _env("ANIMICA_CAP_QUEUE_BACKPRESSURE_TARGET"),
                queue.get("backpressure_target", QueueLimits.backpressure_target),
            ),
        }
    )

    results.update(
        {
            "ttl_blocks": _parse_int(
                _env("ANIMICA_CAP_RESULT_TTL_BLOCKS"),
                results.get("ttl_blocks", ResultPolicy.ttl_blocks),
            ),
            "max_result_bytes": _parse_bytes(
                _env("ANIMICA_CAP_MAX_RESULT_BYTES"),
                results.get("max_result_bytes", ResultPolicy.max_result_bytes),
            ),
        }
    )

    limits.update(
        {
            "max_payload_bytes": _parse_bytes(
                _env("ANIMICA_CAP_MAX_PAYLOAD_BYTES"),
                limits.get("max_payload_bytes", SecurityLimits.max_payload_bytes),
            ),
        }
    )

    # Reassemble
    d["features"] = features
    d["gas"] = gas
    d["queue"] = queue
    d["results"] = results
    d["limits"] = limits

    # Apply explicit overrides last
    if overrides:
        d = _apply_overrides(d, overrides)

    # Construct typed config and run sanity checks
    cfg = Config(
        features=FeatureFlags(**d["features"]),
        gas=GasCosts(**d["gas"]),
        queue=QueueLimits(**d["queue"]),
        results=ResultPolicy(**d["results"]),
        limits=SecurityLimits(**d["limits"]),
    )
    return _sanity(cfg)


def _sanity(cfg: Config) -> Config:
    """
    Clamp/validate ranges to safe values; return a potentially adjusted Config.
    """
    # Backpressure target between 0.1 and 0.99
    bpt = max(0.10, min(0.99, cfg.queue.backpressure_target))
    max_inflight = max(1, min(1_000_000, cfg.queue.max_inflight))
    max_per_caller = max(1, min(100_000, cfg.queue.max_per_caller))
    enqueue_t = max(0.01, min(60.0, cfg.queue.enqueue_timeout_s))
    read_t = max(0.01, min(30.0, cfg.queue.result_read_timeout_s))

    ttl_blocks = max(1, min(10_000_000, cfg.results.ttl_blocks))
    max_result = max(1_024, min(1 << 30, cfg.results.max_result_bytes))
    max_payload = max(1_024, min(1 << 30, cfg.limits.max_payload_bytes))

    if (
        bpt == cfg.queue.backpressure_target
        and max_inflight == cfg.queue.max_inflight
        and max_per_caller == cfg.queue.max_per_caller
        and enqueue_t == cfg.queue.enqueue_timeout_s
        and read_t == cfg.queue.result_read_timeout_s
        and ttl_blocks == cfg.results.ttl_blocks
        and max_result == cfg.results.max_result_bytes
        and max_payload == cfg.limits.max_payload_bytes
    ):
        return cfg  # no changes

    # Return a new, clamped instance
    return Config(
        features=cfg.features,
        gas=cfg.gas,
        queue=QueueLimits(
            max_inflight=max_inflight,
            max_per_caller=max_per_caller,
            enqueue_timeout_s=enqueue_t,
            result_read_timeout_s=read_t,
            backpressure_target=bpt,
        ),
        results=ResultPolicy(ttl_blocks=ttl_blocks, max_result_bytes=max_result),
        limits=SecurityLimits(max_payload_bytes=max_payload),
    )


__all__ = [
    "FeatureFlags",
    "GasCosts",
    "QueueLimits",
    "ResultPolicy",
    "SecurityLimits",
    "Config",
    "load_config",
]
