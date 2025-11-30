"""
Randomness module configuration.

This file defines typed configuration objects and helpers for:
- Round timing (commit/reveal/grace/VDF windows)
- VDF parameters (Wesolowski by default)
- Optional QRNG mixing
- Storage URIs for commitments, reveals, and beacon history

It is dependency-free (standard library only) and provides:
- Dataclass-based configs with validation
- Loading from environment variables (prefix configurable)
- Loading from a JSON or YAML* file (*if PyYAML is available)
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional
from urllib.parse import urlparse

# -------------------------
# Sub-configs
# -------------------------


@dataclass
class VDFParams:
    """
    Parameters for the time-delay step within each randomness round.

    algorithm: currently "wesolowski" (reference verifier elsewhere in repo)
    hash_fn: domain hash used by the VDF construction (e.g., sha3_256)
    modulus_bits: RSA modulus size (sequential squaring group size)
    iterations: number of squarings (time hardness). Nodes should target a
                wall-clock delay ~= vdf_window_s using local benchmarks.
    """

    algorithm: str = "wesolowski"
    hash_fn: str = "sha3_256"
    modulus_bits: int = 2048
    iterations: int = 1 << 26  # ~67M squarings (tune per network / hardware)

    def validate(self) -> None:
        if self.algorithm not in {"wesolowski"}:
            raise ValueError(f"Unsupported VDF algorithm: {self.algorithm}")
        if self.hash_fn not in {"sha3_256", "sha3_512"}:
            raise ValueError(f"Unsupported VDF hash: {self.hash_fn}")
        if self.modulus_bits < 1024 or self.modulus_bits % 256 != 0:
            raise ValueError("modulus_bits must be >=1024 and a multiple of 256")
        if self.iterations <= 0:
            raise ValueError("iterations must be > 0")


@dataclass
class QRNGConfig:
    """
    Optional quantum RNG mixing. When enabled, QRNG bytes are mixed into the
    beacon *after* commit/reveal/VDF, with deterministic weighting.

    provider:
        - "none"       : disabled (ignore endpoint)
        - "nist"       : NIST beacon compatible endpoint
        - "cloudflare" : Cloudflare QRNG JSON API
        - "custom"     : any HTTP(S) endpoint returning raw or JSON-wrapped bytes
    endpoint: HTTPS URL for the provider (if applicable)
    api_key_env: name of the environment variable holding an API key (if needed)
    timeout_s: HTTP timeout for QRNG fetch (caller-side, if networking is enabled)
    mix_weight: 0..1 fraction of QRNG strength in the final mix function
    """

    enabled: bool = False
    provider: str = "none"
    endpoint: Optional[str] = None
    api_key_env: Optional[str] = None
    timeout_s: int = 2
    mix_weight: float = 0.25

    def validate(self) -> None:
        if not self.enabled:
            return
        if self.provider not in {"nist", "cloudflare", "custom"}:
            raise ValueError(
                "provider must be one of {nist, cloudflare, custom} when enabled"
            )
        if not self.endpoint:
            raise ValueError("endpoint is required when QRNG is enabled")
        u = urlparse(self.endpoint)
        if u.scheme not in {"http", "https"}:
            raise ValueError("QRNG endpoint must be http(s)")
        if not (0.0 <= self.mix_weight <= 1.0):
            raise ValueError("mix_weight must be between 0.0 and 1.0")
        if self.timeout_s <= 0:
            raise ValueError("timeout_s must be > 0")


@dataclass
class StorageConfig:
    """
    Where the randomness module persists its artifacts.

    URIs:
      - file://… paths are treated as local filesystem paths
      - sqlite://… or rocksdb://… may be supported by the adapter layer

    max_history_rounds: how many past rounds to retain (GC policy hint)
    """

    commitments_uri: str = "file://./data/randomness/commitments"
    reveals_uri: str = "file://./data/randomness/reveals"
    beacon_uri: str = "file://./data/randomness/beacon"
    max_history_rounds: int = 4096

    def validate(self) -> None:
        for name in ("commitments_uri", "reveals_uri", "beacon_uri"):
            uri = getattr(self, name)
            if "://" not in uri:
                raise ValueError(f"{name} must be a URI (e.g., file://...)")
        if self.max_history_rounds <= 0:
            raise ValueError("max_history_rounds must be > 0")


# -------------------------
# Top-level config
# -------------------------


@dataclass
class RandomnessConfig:
    """
    Round timing:
      - round_period_s: total nominal round length
      - commit_window_s: commit phase (seed commitments)
      - reveal_window_s: reveal phase (openings)
      - reveal_grace_s: extra slack after reveal close to tolerate network skew
      - vdf_window_s: time budget for VDF computation (derived if None)

    Synchronization:
      - genesis_time_unix: UNIX epoch the first round starts (optional)
      - start_height: chain height corresponding to genesis_time_unix (optional)

    VDF / QRNG / Storage: nested sub-configs
    """

    round_period_s: int = 30
    commit_window_s: int = 10
    reveal_window_s: int = 10
    reveal_grace_s: int = 2
    vdf_window_s: Optional[int] = None  # if None, derived as remainder

    # Optional anchors for round alignment (used by schedulers/light-clients)
    genesis_time_unix: Optional[int] = None
    start_height: Optional[int] = None

    # Misc network tolerance knobs
    max_clock_skew_s: int = 2  # allowed local skew in scheduling logic

    vdf: VDFParams = field(default_factory=VDFParams)
    qrng: QRNGConfig = field(default_factory=QRNGConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)

    def effective_vdf_window_s(self) -> int:
        """Return the usable VDF window, deriving it if not set."""
        if self.vdf_window_s is not None:
            return self.vdf_window_s
        rem = self.round_period_s - (
            self.commit_window_s + self.reveal_window_s + self.reveal_grace_s
        )
        return max(rem, 0)

    def validate(self) -> None:
        if self.round_period_s <= 0:
            raise ValueError("round_period_s must be > 0")
        for f_name in ("commit_window_s", "reveal_window_s", "reveal_grace_s"):
            if getattr(self, f_name) < 0:
                raise ValueError(f"{f_name} must be >= 0")
        if self.commit_window_s == 0:
            raise ValueError("commit_window_s must be > 0")
        if self.reveal_window_s == 0:
            raise ValueError("reveal_window_s must be > 0")
        if self.max_clock_skew_s < 0:
            raise ValueError("max_clock_skew_s must be >= 0")

        vdf_win = self.effective_vdf_window_s()
        used = (
            self.commit_window_s + self.reveal_window_s + self.reveal_grace_s + vdf_win
        )
        if used > self.round_period_s:
            raise ValueError(
                f"phase windows exceed round_period_s: commit({self.commit_window_s}) "
                f"+ reveal({self.reveal_window_s}) + grace({self.reveal_grace_s}) "
                f"+ vdf({vdf_win}) = {used} > {self.round_period_s}"
            )
        if vdf_win <= 0:
            raise ValueError("vdf_window_s (effective) must be > 0")

        # Sub-configs
        self.vdf.validate()
        self.qrng.validate()
        self.storage.validate()

        # Sanity for anchors
        if (
            self.genesis_time_unix is not None
            and self.genesis_time_unix <= 1_500_000_000
        ):
            # Arbitrary lower bound (2017) to catch unset/placeholder values
            raise ValueError("genesis_time_unix looks too old or unset")
        if self.start_height is not None and self.start_height < 0:
            raise ValueError("start_height must be >= 0")

    # -------------------------
    # Serialization helpers
    # -------------------------

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        # ensure derived vdf_window if unset
        if data.get("vdf_window_s") is None:
            data["vdf_window_s"] = self.effective_vdf_window_s()
        return data

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)

    # -------------------------
    # Loaders
    # -------------------------

    @staticmethod
    def from_env(prefix: str = "ANIMICA_RAND_") -> "RandomnessConfig":
        """
        Load configuration from environment variables. All variables are optional.

        Supported keys (examples):
          - ANIMICA_RAND_ROUND_PERIOD_S=30
          - ANIMICA_RAND_COMMIT_WINDOW_S=10
          - ANIMICA_RAND_REVEAL_WINDOW_S=10
          - ANIMICA_RAND_REVEAL_GRACE_S=2
          - ANIMICA_RAND_VDF_WINDOW_S=8

          - ANIMICA_RAND_GENESIS_TIME_UNIX=1750000000
          - ANIMICA_RAND_START_HEIGHT=0
          - ANIMICA_RAND_MAX_CLOCK_SKEW_S=2

          - ANIMICA_RAND_VDF_ALGO=wesolowski
          - ANIMICA_RAND_VDF_HASH=sha3_256
          - ANIMICA_RAND_VDF_MOD_BITS=2048
          - ANIMICA_RAND_VDF_ITERATIONS=67108864

          - ANIMICA_RAND_QRNG_ENABLED=true
          - ANIMICA_RAND_QRNG_PROVIDER=cloudflare
          - ANIMICA_RAND_QRNG_ENDPOINT=https://qrng.…
          - ANIMICA_RAND_QRNG_API_KEY_ENV=CF_QRNG_KEY
          - ANIMICA_RAND_QRNG_TIMEOUT_S=2
          - ANIMICA_RAND_QRNG_MIX_WEIGHT=0.25

          - ANIMICA_RAND_STORE_COMMIT=file://./data/randomness/commitments
          - ANIMICA_RAND_STORE_REVEAL=file://./data/randomness/reveals
          - ANIMICA_RAND_STORE_BEACON=file://./data/randomness/beacon
          - ANIMICA_RAND_STORE_MAX_HISTORY=4096
        """

        def _get(name: str, cast: Any, default: Any) -> Any:
            key = prefix + name
            raw = os.getenv(key)
            if raw is None:
                return default
            try:
                if cast is bool:
                    return raw.lower() in {"1", "true", "yes", "on"}
                return cast(raw)
            except Exception as e:
                raise ValueError(f"Invalid value for {key}: {raw!r}") from e

        cfg = RandomnessConfig(
            round_period_s=_get("ROUND_PERIOD_S", int, 30),
            commit_window_s=_get("COMMIT_WINDOW_S", int, 10),
            reveal_window_s=_get("REVEAL_WINDOW_S", int, 10),
            reveal_grace_s=_get("REVEAL_GRACE_S", int, 2),
            vdf_window_s=_get("VDF_WINDOW_S", int, None),  # type: ignore[arg-type]
            genesis_time_unix=_get("GENESIS_TIME_UNIX", int, None),  # type: ignore[arg-type]
            start_height=_get("START_HEIGHT", int, None),  # type: ignore[arg-type]
            max_clock_skew_s=_get("MAX_CLOCK_SKEW_S", int, 2),
            vdf=VDFParams(
                algorithm=_get("VDF_ALGO", str, "wesolowski"),
                hash_fn=_get("VDF_HASH", str, "sha3_256"),
                modulus_bits=_get("VDF_MOD_BITS", int, 2048),
                iterations=_get("VDF_ITERATIONS", int, 1 << 26),
            ),
            qrng=QRNGConfig(
                enabled=_get("QRNG_ENABLED", bool, False),
                provider=_get("QRNG_PROVIDER", str, "none"),
                endpoint=_get("QRNG_ENDPOINT", str, None),  # type: ignore[arg-type]
                api_key_env=_get("QRNG_API_KEY_ENV", str, None),  # type: ignore[arg-type]
                timeout_s=_get("QRNG_TIMEOUT_S", int, 2),
                mix_weight=_get("QRNG_MIX_WEIGHT", float, 0.25),
            ),
            storage=StorageConfig(
                commitments_uri=_get(
                    "STORE_COMMIT", str, "file://./data/randomness/commitments"
                ),
                reveals_uri=_get(
                    "STORE_REVEAL", str, "file://./data/randomness/reveals"
                ),
                beacon_uri=_get("STORE_BEACON", str, "file://./data/randomness/beacon"),
                max_history_rounds=_get("STORE_MAX_HISTORY", int, 4096),
            ),
        )
        cfg.validate()
        return cfg

    @staticmethod
    def from_file(path: str) -> "RandomnessConfig":
        """
        Load configuration from a JSON or YAML file. Keys mirror the dataclass
        structure. Example (YAML):

            round_period_s: 30
            commit_window_s: 10
            reveal_window_s: 10
            reveal_grace_s: 2
            vdf:
              algorithm: wesolowski
              hash_fn: sha3_256
              modulus_bits: 2048
              iterations: 67108864
            qrng:
              enabled: false
            storage:
              commitments_uri: "file://./data/randomness/commitments"
              reveals_uri: "file://./data/randomness/reveals"
              beacon_uri: "file://./data/randomness/beacon"
        """
        text = _read_text(path)
        data = _parse_json_or_yaml(text, path)

        def _pop(d: Dict[str, Any], key: str, default: Any) -> Any:
            return d.pop(key, default) if isinstance(d, dict) else default

        vdf_d = _pop(data, "vdf", {}) or {}
        qrng_d = _pop(data, "qrng", {}) or {}
        storage_d = _pop(data, "storage", {}) or {}

        cfg = RandomnessConfig(
            round_period_s=_pop(data, "round_period_s", 30),
            commit_window_s=_pop(data, "commit_window_s", 10),
            reveal_window_s=_pop(data, "reveal_window_s", 10),
            reveal_grace_s=_pop(data, "reveal_grace_s", 2),
            vdf_window_s=_pop(data, "vdf_window_s", None),
            genesis_time_unix=_pop(data, "genesis_time_unix", None),
            start_height=_pop(data, "start_height", None),
            max_clock_skew_s=_pop(data, "max_clock_skew_s", 2),
            vdf=VDFParams(
                algorithm=_pop(vdf_d, "algorithm", "wesolowski"),
                hash_fn=_pop(vdf_d, "hash_fn", "sha3_256"),
                modulus_bits=_pop(vdf_d, "modulus_bits", 2048),
                iterations=_pop(vdf_d, "iterations", 1 << 26),
            ),
            qrng=QRNGConfig(
                enabled=_pop(qrng_d, "enabled", False),
                provider=_pop(qrng_d, "provider", "none"),
                endpoint=_pop(qrng_d, "endpoint", None),
                api_key_env=_pop(qrng_d, "api_key_env", None),
                timeout_s=_pop(qrng_d, "timeout_s", 2),
                mix_weight=_pop(qrng_d, "mix_weight", 0.25),
            ),
            storage=StorageConfig(
                commitments_uri=_pop(
                    storage_d, "commitments_uri", "file://./data/randomness/commitments"
                ),
                reveals_uri=_pop(
                    storage_d, "reveals_uri", "file://./data/randomness/reveals"
                ),
                beacon_uri=_pop(
                    storage_d, "beacon_uri", "file://./data/randomness/beacon"
                ),
                max_history_rounds=_pop(storage_d, "max_history_rounds", 4096),
            ),
        )
        cfg.validate()
        return cfg


# -------------------------
# Utilities
# -------------------------


def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _parse_json_or_yaml(text: str, path_hint: str) -> Dict[str, Any]:
    # First try JSON
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Then try YAML if available
    try:
        import yaml  # type: ignore

        return yaml.safe_load(text) or {}
    except Exception as e:
        # Give a helpful error with a tiny JSON/YAML hint
        raise ValueError(
            f"Failed to parse {path_hint!r} as JSON or YAML. "
            f"Install PyYAML or provide valid JSON. Original error: {e}"
        ) from e


# A handy default instance for quick use in REPL/tests.
DEFAULT: RandomnessConfig = RandomnessConfig()
try:
    # Provide a reasonable default genesis time if unset, just for local runs.
    if DEFAULT.genesis_time_unix is None:
        DEFAULT.genesis_time_unix = int(time.time()) + 5  # start ~5s in the future
except Exception:
    pass


__all__ = [
    "VDFParams",
    "QRNGConfig",
    "StorageConfig",
    "RandomnessConfig",
    "DEFAULT",
]
