from __future__ import annotations
"""
aicf.config — configuration for the AI Compute Fund (AICF)

Covers:
- Payout rates per work unit (AI / Quantum), in nano-tokens (1e-9 token units)
- Reward split between provider / treasury / miner (basis points, 10_000 = 100%)
- Stake minimums and lock/unlock periods
- SLA thresholds (traps ratio, QoS, latency, availability)
- Slashing parameters (penalties and jail durations)

Environment overrides (all optional; sensible defaults provided):

  # Payout rates (nano-tokens per work unit)
  AICF_AI_UNIT_RATE_NANO=1000000
  AICF_QUANTUM_UNIT_RATE_NANO=2000000

  # Reward split (basis points; must sum to 10000)
  AICF_SPLIT_PROVIDER_BPS=8000
  AICF_SPLIT_TREASURY_BPS=1500
  AICF_SPLIT_MINER_BPS=500

  # Stake & lock (nano-tokens; blocks)
  AICF_MIN_STAKE_AI_NANO=100000000000
  AICF_MIN_STAKE_QUANTUM_NANO=200000000000
  AICF_LOCK_PERIOD_BLOCKS=7200
  AICF_UNBONDING_PERIOD_BLOCKS=7200

  # SLA thresholds
  AICF_SLA_TRAPS_RATIO_MIN=0.66
  AICF_SLA_QOS_MIN=0.95
  AICF_SLA_LATENCY_P95_MAX_MS=3000
  AICF_SLA_AVAILABILITY_MIN=0.98

  # Slashing (basis points & jail)
  AICF_SLASH_TRAPS_FAIL_BPS=1000
  AICF_SLASH_QOS_FAIL_BPS=500
  AICF_SLASH_AVAIL_FAIL_BPS=250
  AICF_SLASH_MISBEHAVIOR_BPS=5000
  AICF_JAIL_BLOCKS=14400

You can also load from a JSON or YAML file via `AICF_CONFIG_FILE=/path/to/config.(json|yaml|yml)`.
File values override defaults; environment overrides the file.
"""


from dataclasses import dataclass, asdict
from typing import Any, Dict, Optional
import json
import os
from pathlib import Path

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - yaml is optional
    yaml = None  # type: ignore


# -------------------------- Data classes --------------------------


@dataclass
class PayoutRates:
    """Per-unit payout rates in nano-tokens (1 token = 1_000_000_000 nanos)."""
    ai_unit_rate_nano: int = 1_000_000         # 0.001 token / AI unit
    quantum_unit_rate_nano: int = 2_000_000    # 0.002 token / Quantum unit

    def validate(self) -> None:
        if self.ai_unit_rate_nano < 0 or self.quantum_unit_rate_nano < 0:
            raise ValueError("Payout rates must be non-negative (nano-tokens).")


@dataclass
class RewardSplit:
    """Reward split in basis points (10_000 = 100%). Must sum to 10_000."""
    provider_bps: int = 8000
    treasury_bps: int = 1500
    miner_bps: int = 500

    def total_bps(self) -> int:
        return self.provider_bps + self.treasury_bps + self.miner_bps

    def validate(self) -> None:
        for name, v in (("provider_bps", self.provider_bps),
                        ("treasury_bps", self.treasury_bps),
                        ("miner_bps", self.miner_bps)):
            if not (0 <= v <= 10_000):
                raise ValueError(f"{name} must be between 0 and 10000 (got {v}).")
        if self.total_bps() != 10_000:
            raise ValueError(f"RewardSplit must sum to 10000 bps (got {self.total_bps()}).")


@dataclass
class StakeConfig:
    """Minimum stake and lock/unbonding periods."""
    min_stake_ai_nano: int = 100_000_000_000        # 100 tokens
    min_stake_quantum_nano: int = 200_000_000_000   # 200 tokens
    lock_period_blocks: int = 7_200                  # ~1 day at 12s blocks
    unbonding_period_blocks: int = 7_200

    def validate(self) -> None:
        if self.min_stake_ai_nano < 0 or self.min_stake_quantum_nano < 0:
            raise ValueError("Minimum stakes must be non-negative.")
        if self.lock_period_blocks <= 0 or self.unbonding_period_blocks <= 0:
            raise ValueError("Lock/unbonding periods must be positive blocks.")


@dataclass
class SLAThresholds:
    """Provider SLA thresholds used by the evaluator."""
    traps_ratio_min: float = 0.66         # fraction of trap-circuit passes required
    qos_min: float = 0.95                 # quality-of-service composite score
    latency_p95_max_ms: int = 3_000       # 95th percentile latency bound
    availability_min: float = 0.98        # fraction of up/healthy heartbeats

    def validate(self) -> None:
        for name, v in (("traps_ratio_min", self.traps_ratio_min),
                        ("qos_min", self.qos_min),
                        ("availability_min", self.availability_min)):
            if not (0.0 <= v <= 1.0):
                raise ValueError(f"{name} must be in [0.0, 1.0] (got {v}).")
        if self.latency_p95_max_ms <= 0:
            raise ValueError("latency_p95_max_ms must be positive.")


@dataclass
class SlashingParams:
    """Penalty magnitudes (basis points) and jail duration (blocks)."""
    traps_fail_bps: int = 1_000         # 10% slash for failing traps
    qos_fail_bps: int = 500             # 5% slash for QoS failure
    availability_fail_bps: int = 250    # 2.5% slash for availability failure
    misbehavior_bps: int = 5_000        # 50% slash for explicit misbehavior
    jail_blocks: int = 14_400           # ~2 days at 12s blocks

    def validate(self) -> None:
        for name, v in (("traps_fail_bps", self.traps_fail_bps),
                        ("qos_fail_bps", self.qos_fail_bps),
                        ("availability_fail_bps", self.availability_fail_bps),
                        ("misbehavior_bps", self.misbehavior_bps)):
            if not (0 <= v <= 10_000):
                raise ValueError(f"{name} must be between 0 and 10000 (got {v}).")
        if self.jail_blocks <= 0:
            raise ValueError("jail_blocks must be positive.")


@dataclass
class AICFConfig:
    """Top-level configuration container."""
    payouts: PayoutRates = PayoutRates()
    split: RewardSplit = RewardSplit()
    stake: StakeConfig = StakeConfig()
    sla: SLAThresholds = SLAThresholds()
    slashing: SlashingParams = SlashingParams()

    token_decimals: int = 9  # informational (nano-token base)
    # Optional: make chain-wide constants accessible if needed later
    chain_id: Optional[int] = None

    def validate(self) -> None:
        self.payouts.validate()
        self.split.validate()
        self.stake.validate()
        self.sla.validate()
        self.slashing.validate()
        if self.token_decimals <= 0:
            raise ValueError("token_decimals must be positive.")

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d


# -------------------------- Loaders --------------------------


def _getenv_int(name: str, default: int) -> int:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    try:
        return int(str(v).replace("_", ""))
    except Exception as e:
        raise ValueError(f"Invalid int for {name}: {v!r}") from e


def _getenv_float(name: str, default: float) -> float:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    try:
        return float(v)
    except Exception as e:
        raise ValueError(f"Invalid float for {name}: {v!r}") from e


def _getenv_bps(name: str, default: int) -> int:
    bps = _getenv_int(name, default)
    if not (0 <= bps <= 10_000):
        raise ValueError(f"{name} must be between 0 and 10000 bps (got {bps}).")
    return bps


def from_env(base: Optional[AICFConfig] = None, prefix: str = "AICF_") -> AICFConfig:
    """
    Build an AICFConfig from environment variables, optionally layering on top of `base`.
    """
    cfg = base or AICFConfig()

    # Payouts
    ai_rate = _getenv_int(f"{prefix}AI_UNIT_RATE_NANO", cfg.payouts.ai_unit_rate_nano)
    q_rate = _getenv_int(f"{prefix}QUANTUM_UNIT_RATE_NANO", cfg.payouts.quantum_unit_rate_nano)

    # Split
    prov = _getenv_bps(f"{prefix}SPLIT_PROVIDER_BPS", cfg.split.provider_bps)
    tres = _getenv_bps(f"{prefix}SPLIT_TREASURY_BPS", cfg.split.treasury_bps)
    mine = _getenv_bps(f"{prefix}SPLIT_MINER_BPS", cfg.split.miner_bps)

    # Stake
    stake_ai = _getenv_int(f"{prefix}MIN_STAKE_AI_NANO", cfg.stake.min_stake_ai_nano)
    stake_q = _getenv_int(f"{prefix}MIN_STAKE_QUANTUM_NANO", cfg.stake.min_stake_quantum_nano)
    lock = _getenv_int(f"{prefix}LOCK_PERIOD_BLOCKS", cfg.stake.lock_period_blocks)
    unbond = _getenv_int(f"{prefix}UNBONDING_PERIOD_BLOCKS", cfg.stake.unbonding_period_blocks)

    # SLA
    traps_min = _getenv_float(f"{prefix}SLA_TRAPS_RATIO_MIN", cfg.sla.traps_ratio_min)
    qos_min = _getenv_float(f"{prefix}SLA_QOS_MIN", cfg.sla.qos_min)
    lat_p95 = _getenv_int(f"{prefix}SLA_LATENCY_P95_MAX_MS", cfg.sla.latency_p95_max_ms)
    avail_min = _getenv_float(f"{prefix}SLA_AVAILABILITY_MIN", cfg.sla.availability_min)

    # Slashing
    slash_traps = _getenv_bps(f"{prefix}SLASH_TRAPS_FAIL_BPS", cfg.slashing.traps_fail_bps)
    slash_qos = _getenv_bps(f"{prefix}SLASH_QOS_FAIL_BPS", cfg.slashing.qos_fail_bps)
    slash_avail = _getenv_bps(f"{prefix}SLASH_AVAIL_FAIL_BPS", cfg.slashing.availability_fail_bps)
    slash_mis = _getenv_bps(f"{prefix}SLASH_MISBEHAVIOR_BPS", cfg.slashing.misbehavior_bps)
    jail = _getenv_int(f"{prefix}JAIL_BLOCKS", cfg.slashing.jail_blocks)

    # Optional chain id
    chain_id = os.getenv(f"{prefix}CHAIN_ID")
    chain_id_val = int(chain_id) if chain_id not in (None, "") else cfg.chain_id

    new_cfg = AICFConfig(
        payouts=PayoutRates(ai_unit_rate_nano=ai_rate, quantum_unit_rate_nano=q_rate),
        split=RewardSplit(provider_bps=prov, treasury_bps=tres, miner_bps=mine),
        stake=StakeConfig(
            min_stake_ai_nano=stake_ai,
            min_stake_quantum_nano=stake_q,
            lock_period_blocks=lock,
            unbonding_period_blocks=unbond,
        ),
        sla=SLAThresholds(
            traps_ratio_min=traps_min,
            qos_min=qos_min,
            latency_p95_max_ms=lat_p95,
            availability_min=avail_min,
        ),
        slashing=SlashingParams(
            traps_fail_bps=slash_traps,
            qos_fail_bps=slash_qos,
            availability_fail_bps=slash_avail,
            misbehavior_bps=slash_mis,
            jail_blocks=jail,
        ),
        token_decimals=cfg.token_decimals,
        chain_id=chain_id_val,
    )
    new_cfg.validate()
    return new_cfg


def from_file(path: str | os.PathLike[str]) -> AICFConfig:
    """
    Load configuration from a JSON or YAML file.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(p)

    text = p.read_text(encoding="utf-8")
    if p.suffix.lower() in (".yaml", ".yml"):
        if yaml is None:
            raise RuntimeError("YAML config requested but PyYAML is not installed.")
        data = yaml.safe_load(text) or {}
    else:
        data = json.loads(text or "{}")

    def pick(dct: Dict[str, Any], key: str, default: Any) -> Any:
        return dct.get(key, default)

    payouts = data.get("payouts", {})
    split = data.get("split", {})
    stake = data.get("stake", {})
    sla = data.get("sla", {})
    slashing = data.get("slashing", {})

    cfg = AICFConfig(
        payouts=PayoutRates(
            ai_unit_rate_nano=pick(payouts, "ai_unit_rate_nano", PayoutRates().ai_unit_rate_nano),
            quantum_unit_rate_nano=pick(payouts, "quantum_unit_rate_nano", PayoutRates().quantum_unit_rate_nano),
        ),
        split=RewardSplit(
            provider_bps=pick(split, "provider_bps", RewardSplit().provider_bps),
            treasury_bps=pick(split, "treasury_bps", RewardSplit().treasury_bps),
            miner_bps=pick(split, "miner_bps", RewardSplit().miner_bps),
        ),
        stake=StakeConfig(
            min_stake_ai_nano=pick(stake, "min_stake_ai_nano", StakeConfig().min_stake_ai_nano),
            min_stake_quantum_nano=pick(stake, "min_stake_quantum_nano", StakeConfig().min_stake_quantum_nano),
            lock_period_blocks=pick(stake, "lock_period_blocks", StakeConfig().lock_period_blocks),
            unbonding_period_blocks=pick(stake, "unbonding_period_blocks", StakeConfig().unbonding_period_blocks),
        ),
        sla=SLAThresholds(
            traps_ratio_min=pick(sla, "traps_ratio_min", SLAThresholds().traps_ratio_min),
            qos_min=pick(sla, "qos_min", SLAThresholds().qos_min),
            latency_p95_max_ms=pick(sla, "latency_p95_max_ms", SLAThresholds().latency_p95_max_ms),
            availability_min=pick(sla, "availability_min", SLAThresholds().availability_min),
        ),
        slashing=SlashingParams(
            traps_fail_bps=pick(slashing, "traps_fail_bps", SlashingParams().traps_fail_bps),
            qos_fail_bps=pick(slashing, "qos_fail_bps", SlashingParams().qos_fail_bps),
            availability_fail_bps=pick(slashing, "availability_fail_bps", SlashingParams().availability_fail_bps),
            misbehavior_bps=pick(slashing, "misbehavior_bps", SlashingParams().misbehavior_bps),
            jail_blocks=pick(slashing, "jail_blocks", SlashingParams().jail_blocks),
        ),
        token_decimals=pick(data, "token_decimals", AICFConfig().token_decimals),
        chain_id=pick(data, "chain_id", AICFConfig().chain_id),
    )
    cfg.validate()
    return cfg


def load() -> AICFConfig:
    """
    Load configuration using the following precedence:
      1) File at $AICF_CONFIG_FILE (JSON/YAML)
      2) Environment variables (AICF_*), applied on top of defaults or file values
    """
    file_path = os.getenv("AICF_CONFIG_FILE")
    base = from_file(file_path) if file_path else AICFConfig()
    return from_env(base=base)


# -------------------------- Utilities --------------------------


def pretty(cfg: Optional[AICFConfig] = None) -> str:
    """Return a human-readable JSON string of the current config."""
    obj = (cfg or load()).to_dict()
    return json.dumps(obj, indent=2, sort_keys=True)


__all__ = [
    "PayoutRates",
    "RewardSplit",
    "StakeConfig",
    "SLAThresholds",
    "SlashingParams",
    "AICFConfig",
    "from_env",
    "from_file",
    "load",
    "pretty",
]
