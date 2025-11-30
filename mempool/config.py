"""
mempool.config
--------------

Configuration for the Animica mempool:

- Limits: global and per-sender caps on tx count/bytes and max tx size.
- Min gas price policy: static floor + optional dynamic surge based on occupancy.
- TTLs: eviction and rebroadcast timings.
- Peer caps: per-peer/global admission rate limits for gossip/ingress.

The loader supports environment variables and optional YAML/JSON files.

ENV overrides (all optional; examples shown as defaults):
  MEMPOOL_MAX_TXS=100000
  MEMPOOL_MAX_BYTES=536870912              # 512 MiB
  MEMPOOL_MAX_TX_SIZE=1048576              # 1 MiB
  MEMPOOL_PER_SENDER_MAX_TXS=1024
  MEMPOOL_PER_SENDER_MAX_BYTES=16777216    # 16 MiB

  MEMPOOL_GAS_FLOOR_GWEI=1.0
  MEMPOOL_GAS_DYNAMIC_ENABLED=1
  MEMPOOL_GAS_TARGET_UTIL=0.60
  MEMPOOL_GAS_SURGE_MULT=3.0
  MEMPOOL_GAS_ALPHA=2.0
  MEMPOOL_GAS_EMA_HALFLIFE=50              # blocks

  MEMPOOL_TTL_PENDING_SEC=7200
  MEMPOOL_TTL_ORPHAN_SEC=300
  MEMPOOL_TTL_REANNOUNCE_SEC=30
  MEMPOOL_TTL_REPLACEMENT_GRACE_SEC=30

  MEMPOOL_PEER_MAX_IN_FLIGHT=1024
  MEMPOOL_PEER_TXS_PER_SEC=50
  MEMPOOL_PEER_BURST=200
  MEMPOOL_GLOBAL_TXS_PER_SEC=500
  MEMPOOL_GOSSIP_BATCH=100
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Union

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None

# --------- Helpers -----------------------------------------------------------

Number = Union[int, float]


def _get_env(name: str, default: Number) -> Number:
    val = os.environ.get(name)
    if val is None:
        return default
    try:
        if "." in val:
            return float(val)
        return int(val)
    except ValueError:
        return default


def _get_env_bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "y", "on"}


# Gas unit helpers (gwei <-> wei)
GWEI = 10**9


def gwei_to_wei(g: Number) -> int:
    return int(round(float(g) * GWEI))


def wei_to_gwei(w: int) -> float:
    return float(w) / GWEI


# --------- Dataclasses -------------------------------------------------------


@dataclass(frozen=True)
class Limits:
    # Global bounds
    max_txs: int = 100_000
    max_bytes: int = 512 * 1024 * 1024  # 512 MiB
    max_tx_size_bytes: int = 1 * 1024 * 1024  # 1 MiB

    # Per-sender bounds
    per_sender_max_txs: int = 1_024
    per_sender_max_bytes: int = 16 * 1024 * 1024  # 16 MiB


@dataclass(frozen=True)
class MinGasPricePolicy:
    """
    Mempool admission minimum gas price (gwei). The policy is:

        min_gas_gwei = max(
            floor_gwei,
            dynamic(utilization) if dynamic_enabled else 0
        )

    where utilization ∈ [0, 1] is the rolling occupancy ratio of the mempool:
        utilization ≈ current_bytes / limits.max_bytes

    dynamic(util) = floor_gwei * surge_multiplier * (util/target_util) ** alpha
    clipped to at least floor_gwei when util >= target_util and dynamic_enabled.

    An EMA (in blocks) can be used by the caller to smooth utilization before
    calling `min_gas_gwei(util)`.
    """

    floor_gwei: float = 1.0
    dynamic_enabled: bool = True
    target_utilization: float = 0.60
    surge_multiplier: float = 3.0
    alpha: float = 2.0
    ema_halflife_blocks: int = 50  # for external smoothing of utilization

    def min_gas_gwei(self, utilization: float) -> float:
        u = max(0.0, min(1.0, float(utilization)))
        base = float(self.floor_gwei)
        if not self.dynamic_enabled:
            return base
        if u <= 0.0:
            return base
        # When u == target_utilization, dynamic term = base * surge_multiplier
        dyn = (
            base
            * self.surge_multiplier
            * (u / max(1e-9, self.target_utilization)) ** self.alpha
        )
        # Never go below base
        return max(base, dyn)

    def min_gas_wei(self, utilization: float) -> int:
        return gwei_to_wei(self.min_gas_gwei(utilization))


@dataclass(frozen=True)
class TTLs:
    # Eviction window for pending txs that never land on-chain
    pending_seconds: int = 2 * 60 * 60  # 2h
    # Orphans (missing prerequisites like nonce gap) are kept briefly
    orphan_seconds: int = 5 * 60  # 5m
    # Periodic rebroadcast interval to peers
    reannounce_interval_seconds: int = 30
    # Replacement (price bump) grace to mitigate rapid thrash
    replacement_grace_seconds: int = 30


@dataclass(frozen=True)
class PeerCaps:
    # Per-peer backpressure & rates
    max_in_flight: int = 1_024
    txs_per_sec: int = 50
    burst: int = 200

    # Global ceiling across all peers
    global_txs_per_sec: int = 500

    # Gossip batching
    gossip_batch_size: int = 100


@dataclass(frozen=True)
class MempoolConfig:
    limits: Limits = Limits()
    gas: MinGasPricePolicy = MinGasPricePolicy()
    ttls: TTLs = TTLs()
    peers: PeerCaps = PeerCaps()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "limits": asdict(self.limits),
            "gas": asdict(self.gas),
            "ttls": asdict(self.ttls),
            "peers": asdict(self.peers),
        }

    # Convenience: compute effective min gas in gwei/wei given current occupancy.
    def min_gas_gwei(self, current_bytes: int) -> float:
        util = (
            0.0
            if self.limits.max_bytes <= 0
            else min(1.0, max(0.0, current_bytes / float(self.limits.max_bytes)))
        )
        return self.gas.min_gas_gwei(util)

    def min_gas_wei(self, current_bytes: int) -> int:
        return gwei_to_wei(self.min_gas_gwei(current_bytes))

    # Sanity checks; raise ValueError on misconfiguration.
    def validate(self) -> None:
        if self.limits.max_txs <= 0:
            raise ValueError("limits.max_txs must be > 0")
        if self.limits.max_bytes < 1 << 20:  # < 1 MiB
            raise ValueError("limits.max_bytes unrealistically small")
        if (
            self.limits.max_tx_size_bytes <= 0
            or self.limits.max_tx_size_bytes > self.limits.max_bytes
        ):
            raise ValueError("limits.max_tx_size_bytes out of range")
        if self.limits.per_sender_max_txs <= 0:
            raise ValueError("per_sender_max_txs must be > 0")
        if self.limits.per_sender_max_bytes <= 0:
            raise ValueError("per_sender_max_bytes must be > 0")

        if not (0.0 < self.gas.target_utilization <= 1.0):
            raise ValueError("gas.target_utilization must be in (0, 1]")
        if self.gas.floor_gwei <= 0.0:
            raise ValueError("gas.floor_gwei must be > 0")
        if self.gas.alpha <= 0.0:
            raise ValueError("gas.alpha must be > 0")
        if self.gas.surge_multiplier < 1.0 and self.gas.dynamic_enabled:
            raise ValueError(
                "gas.surge_multiplier should be >= 1.0 when dynamic_enabled"
            )

        if self.ttls.pending_seconds < 60:
            raise ValueError("ttls.pending_seconds is too small (< 60)")
        if self.ttls.reannounce_interval_seconds <= 0:
            raise ValueError("ttls.reannounce_interval_seconds must be > 0")

        if self.peers.max_in_flight <= 0:
            raise ValueError("peers.max_in_flight must be > 0")
        if self.peers.txs_per_sec <= 0 or self.peers.burst < self.peers.txs_per_sec:
            # burst can be == txs_per_sec but typical is >.
            pass
        if self.peers.global_txs_per_sec < self.peers.txs_per_sec:
            raise ValueError("peers.global_txs_per_sec should be >= peers.txs_per_sec")
        if self.peers.gossip_batch_size <= 0:
            raise ValueError("peers.gossip_batch_size must be > 0")


# --------- Loading -----------------------------------------------------------


def _from_env(base: Optional[MempoolConfig] = None) -> MempoolConfig:
    b = base or MempoolConfig()

    limits = Limits(
        max_txs=int(_get_env("MEMPOOL_MAX_TXS", b.limits.max_txs)),
        max_bytes=int(_get_env("MEMPOOL_MAX_BYTES", b.limits.max_bytes)),
        max_tx_size_bytes=int(
            _get_env("MEMPOOL_MAX_TX_SIZE", b.limits.max_tx_size_bytes)
        ),
        per_sender_max_txs=int(
            _get_env("MEMPOOL_PER_SENDER_MAX_TXS", b.limits.per_sender_max_txs)
        ),
        per_sender_max_bytes=int(
            _get_env("MEMPOOL_PER_SENDER_MAX_BYTES", b.limits.per_sender_max_bytes)
        ),
    )
    gas = MinGasPricePolicy(
        floor_gwei=float(_get_env("MEMPOOL_GAS_FLOOR_GWEI", b.gas.floor_gwei)),
        dynamic_enabled=_get_env_bool(
            "MEMPOOL_GAS_DYNAMIC_ENABLED", b.gas.dynamic_enabled
        ),
        target_utilization=float(
            _get_env("MEMPOOL_GAS_TARGET_UTIL", b.gas.target_utilization)
        ),
        surge_multiplier=float(
            _get_env("MEMPOOL_GAS_SURGE_MULT", b.gas.surge_multiplier)
        ),
        alpha=float(_get_env("MEMPOOL_GAS_ALPHA", b.gas.alpha)),
        ema_halflife_blocks=int(
            _get_env("MEMPOOL_GAS_EMA_HALFLIFE", b.gas.ema_halflife_blocks)
        ),
    )
    ttls = TTLs(
        pending_seconds=int(
            _get_env("MEMPOOL_TTL_PENDING_SEC", b.ttls.pending_seconds)
        ),
        orphan_seconds=int(_get_env("MEMPOOL_TTL_ORPHAN_SEC", b.ttls.orphan_seconds)),
        reannounce_interval_seconds=int(
            _get_env("MEMPOOL_TTL_REANNOUNCE_SEC", b.ttls.reannounce_interval_seconds)
        ),
        replacement_grace_seconds=int(
            _get_env(
                "MEMPOOL_TTL_REPLACEMENT_GRACE_SEC", b.ttls.replacement_grace_seconds
            )
        ),
    )
    peers = PeerCaps(
        max_in_flight=int(
            _get_env("MEMPOOL_PEER_MAX_IN_FLIGHT", b.peers.max_in_flight)
        ),
        txs_per_sec=int(_get_env("MEMPOOL_PEER_TXS_PER_SEC", b.peers.txs_per_sec)),
        burst=int(_get_env("MEMPOOL_PEER_BURST", b.peers.burst)),
        global_txs_per_sec=int(
            _get_env("MEMPOOL_GLOBAL_TXS_PER_SEC", b.peers.global_txs_per_sec)
        ),
        gossip_batch_size=int(
            _get_env("MEMPOOL_GOSSIP_BATCH", b.peers.gossip_batch_size)
        ),
    )
    cfg = MempoolConfig(limits=limits, gas=gas, ttls=ttls, peers=peers)
    cfg.validate()
    return cfg


def _from_mapping(m: Dict[str, Any]) -> MempoolConfig:
    limits = Limits(**m.get("limits", {}))
    gas = MinGasPricePolicy(**m.get("gas", {}))
    ttls = TTLs(**m.get("ttls", {}))
    peers = PeerCaps(**m.get("peers", {}))
    cfg = MempoolConfig(limits=limits, gas=gas, ttls=ttls, peers=peers)
    cfg.validate()
    return cfg


def load_config(path: Optional[Union[str, Path]] = None) -> MempoolConfig:
    """
    Load configuration from (in order of precedence):
      1) File at `path` (YAML/JSON), if provided and exists.
      2) Environment variables (see header).
      3) Built-in defaults.

    The file (if any) is loaded first, then environment variables override.
    """
    base = MempoolConfig()
    if path:
        p = Path(path)
        if p.exists():
            text = p.read_text(encoding="utf-8")
            data: Dict[str, Any]
            if p.suffix.lower() in {".yml", ".yaml"}:
                if yaml is None:
                    raise RuntimeError("pyyaml not installed but YAML config provided")
                data = yaml.safe_load(text) or {}
            else:
                data = json.loads(text)
            base = _from_mapping(data)
    # Apply env overrides on top
    return _from_env(base)


# --------- CLI ---------------------------------------------------------------


def _main(argv: list[str]) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Print mempool config or compute min gas")
    ap.add_argument(
        "--config", type=str, help="Path to YAML/JSON config file", default=None
    )
    ap.add_argument(
        "--bytes",
        type=int,
        help="Current mempool bytes to compute min gas",
        default=None,
    )
    ap.add_argument("--json", action="store_true", help="Emit JSON")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    if args.bytes is None:
        out = cfg.to_dict()
    else:
        out = {
            "min_gas_gwei": cfg.min_gas_gwei(args.bytes),
            "min_gas_wei": cfg.min_gas_wei(args.bytes),
            "utilization": min(1.0, args.bytes / float(cfg.limits.max_bytes)),
        }
    if args.json:
        print(json.dumps(out, indent=2, sort_keys=True))
    else:
        if isinstance(out, dict):
            print(json.dumps(out, indent=2, sort_keys=True))
        else:
            print(out)
    return 0


if __name__ == "__main__":  # pragma: no cover
    import sys

    raise SystemExit(_main(sys.argv[1:]))
