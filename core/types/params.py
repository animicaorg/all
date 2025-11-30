from __future__ import annotations

"""
ChainParams (core subset)
=========================

A compact, strongly-typed view over the canonical YAML in spec/params.yaml.
Only the fields the **core node** needs at boot and during block import are
included here. Other subsystems (mempool, VM, DA, etc.) read their own
specialized params elsewhere.

Layout expected in spec/params.yaml (relevant subset):

chain:
  id: 1
  name: "Animica Mainnet"
genesis:
  time: "2025-01-01T00:00:00Z"
  hash: "0x…"   # 32-byte hex
policy_roots:
  alg_policy_root:  "0x…"  # 32-byte hex
  poies_policy_root:"0x…"  # 32-byte hex
consensus:
  theta_initial:  1450000        # µ-nats threshold at genesis (example)
  gamma_total_cap: 900000         # total Γ cap in µ-nats-equivalent units
  retarget:
    window:  2048                 # blocks per EMA window
    ema_alpha: 0.10               # smoothing factor (0..1)
    bounds:
      min: 0.5                    # clamp factor per window (down)
      max: 2.0                    # clamp factor per window (up)
block:
  target_seconds: 2.0
  max_bytes: 1500000              # ~1.5 MiB
  max_gas: 20000000               # execution gas cap per block
  tx_max_bytes: 131072
  min_gas_price: 1000             # protocol floor (in smallest unit)

Note: values above are illustrative; your repo's spec/params.yaml is the
source of truth. This module focuses on parsing, validation, and making the
fields easily available across core/.
"""

import binascii
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Tuple

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - optional at import time
    yaml = None  # Loaded lazily in load_yaml()


Bytes32 = bytes  # semantic alias


def _hex_to_bytes32(x: str, *, field: str) -> Bytes32:
    if not isinstance(x, str):
        raise TypeError(f"{field}: expected hex str, got {type(x).__name__}")
    s = x.lower().strip()
    if s.startswith("0x"):
        s = s[2:]
    try:
        b = binascii.unhexlify(s)
    except binascii.Error as e:  # bad hex
        raise ValueError(f"{field}: invalid hex: {e}") from e
    if len(b) != 32:
        raise ValueError(f"{field}: expected 32 bytes, got {len(b)}")
    return b


def _require_range(name: str, val: float, lo: float, hi: float) -> float:
    if not (lo <= val <= hi):
        raise ValueError(f"{name}: {val} out of range [{lo},{hi}]")
    return val


@dataclass(frozen=True)
class RetargetBounds:
    """Clamp factors applied to Θ per retarget window."""

    min: float
    max: float

    @classmethod
    def from_mapping(cls, m: Mapping[str, Any]) -> "RetargetBounds":
        return cls(
            min=_require_range("retarget.bounds.min", float(m["min"]), 0.1, 1.0),
            max=_require_range("retarget.bounds.max", float(m["max"]), 1.0, 10.0),
        )


@dataclass(frozen=True)
class RetargetParams:
    """EMA-based fractional retarget schedule for Θ."""

    window: int
    ema_alpha: float
    bounds: RetargetBounds

    @classmethod
    def from_mapping(cls, m: Mapping[str, Any]) -> "RetargetParams":
        window = int(m["window"])
        if window <= 0:
            raise ValueError("retarget.window must be > 0")
        ema_alpha = _require_range(
            "retarget.ema_alpha", float(m["ema_alpha"]), 0.0, 1.0
        )
        bounds = RetargetBounds.from_mapping(m["bounds"])
        return cls(window=window, ema_alpha=ema_alpha, bounds=bounds)


@dataclass(frozen=True)
class BlockLimits:
    target_seconds: float
    max_bytes: int
    max_gas: int
    tx_max_bytes: int
    min_gas_price: int

    @classmethod
    def from_mapping(cls, m: Mapping[str, Any]) -> "BlockLimits":
        tgt = float(m["target_seconds"])
        _require_range("block.target_seconds", tgt, 0.2, 60.0)
        max_bytes = int(m["max_bytes"])
        max_gas = int(m["max_gas"])
        tx_max_bytes = int(m["tx_max_bytes"])
        min_gas_price = int(m.get("min_gas_price", 0))
        if max_bytes <= 0 or tx_max_bytes <= 0 or max_gas <= 0:
            raise ValueError("block.{max_bytes|tx_max_bytes|max_gas} must be > 0")
        if tx_max_bytes > max_bytes:
            raise ValueError("block.tx_max_bytes must be ≤ block.max_bytes")
        return cls(
            target_seconds=tgt,
            max_bytes=max_bytes,
            max_gas=max_gas,
            tx_max_bytes=tx_max_bytes,
            min_gas_price=min_gas_price,
        )


@dataclass(frozen=True)
class ChainParams:
    """
    Core chain parameters loaded from spec/params.yaml.

    Only the subset needed by core boot, header validation and fork-choice
    is included here. Other modules may extend via their own config.
    """

    chain_id: int
    chain_name: str

    # Genesis
    genesis_time: str  # ISO-8601
    genesis_hash: Bytes32

    # Policy roots (binds consensus validation to published policy trees)
    alg_policy_root: Bytes32
    poies_policy_root: Bytes32

    # Consensus knobs
    theta_initial: int  # micro-nats (µ-nats) threshold at genesis
    gamma_total_cap: int  # total Γ cap (same unit scale as ψ inputs)
    retarget: RetargetParams

    # Block-level limits
    block: BlockLimits

    # ------ factories / helpers ------

    @classmethod
    def from_mapping(cls, m: Mapping[str, Any]) -> "ChainParams":
        chain = m["chain"]
        genesis = m["genesis"]
        roots = m["policy_roots"]
        cons = m["consensus"]
        block = m["block"]

        chain_id = int(chain["id"])
        if chain_id <= 0:
            raise ValueError("chain.id must be positive")
        chain_name = str(chain["name"]).strip()
        if not chain_name:
            raise ValueError("chain.name must be non-empty")

        theta_initial = int(cons["theta_initial"])
        if theta_initial <= 0:
            raise ValueError("consensus.theta_initial must be > 0")

        gamma_total_cap = int(cons["gamma_total_cap"])
        if gamma_total_cap <= 0:
            raise ValueError("consensus.gamma_total_cap must be > 0")

        return cls(
            chain_id=chain_id,
            chain_name=chain_name,
            genesis_time=str(genesis["time"]),
            genesis_hash=_hex_to_bytes32(genesis["hash"], field="genesis.hash"),
            alg_policy_root=_hex_to_bytes32(
                roots["alg_policy_root"], field="policy_roots.alg_policy_root"
            ),
            poies_policy_root=_hex_to_bytes32(
                roots["poies_policy_root"], field="policy_roots.poies_policy_root"
            ),
            theta_initial=theta_initial,
            gamma_total_cap=gamma_total_cap,
            retarget=RetargetParams.from_mapping(cons["retarget"]),
            block=BlockLimits.from_mapping(block),
        )

    @classmethod
    def _from_networks_mapping(
        cls,
        m: Mapping[str, Any],
        *,
        chain_id_hint: int | None = None,
        network_name: str | None = None,
    ) -> "ChainParams":
        """
        Compatibility loader for the newer `spec/params.yaml` layout which
        nests network configs under a top-level `networks:` map.

        We select a network based on (in order):
          1) explicit ``network_name`` if provided,
          2) ``chain_id_hint`` matching the ``<name>:<id>`` suffix,
          3) the first entry in the map (deterministic insertion order in Py3.7+).
        Missing optional fields fall back to safe defaults so the core boot
        process can proceed even if the broader spec has additional knobs.
        """

        def _pick_network() -> tuple[str, Mapping[str, Any]]:
            networks = m.get("networks")
            if not isinstance(networks, Mapping) or not networks:
                raise KeyError("networks")

            if network_name:
                for k, v in networks.items():
                    if k.lower() == network_name.lower():
                        return k, v

            if chain_id_hint is not None:
                for k, v in networks.items():
                    if ":" in k:
                        try:
                            cid = int(k.split(":")[-1])
                        except ValueError:
                            continue
                        if cid == chain_id_hint:
                            return k, v

            # Fallback: first entry (insertion order is deterministic)
            k, v = next(iter(networks.items()))
            return k, v

        def _bytes32_or_zero(val: str | None, field: str) -> Bytes32:
            if val is None:
                return b"\x00" * 32
            try:
                return _hex_to_bytes32(val, field=field)
            except Exception:
                return b"\x00" * 32

        network_key, net = _pick_network()
        chain_id = chain_id_hint
        if chain_id is None and ":" in network_key:
            try:
                chain_id = int(network_key.split(":")[-1])
            except ValueError:
                chain_id = None
        chain_id = int(
            chain_id if chain_id is not None else net.get("chain_id", 0) or 0
        )
        if chain_id <= 0:
            raise ValueError("chain.id must be positive")

        chain_name = str(net.get("name") or network_key).strip()
        if not chain_name:
            raise ValueError("chain.name must be non-empty")

        genesis = net.get("genesis") or {}
        genesis_time = str(
            genesis.get("time") or net.get("genesis_time_utc") or "1970-01-01T00:00:00Z"
        )
        genesis_hash = _bytes32_or_zero(
            genesis.get("hash") or net.get("genesis_hash"), field="genesis.hash"
        )

        roots = net.get("policy_roots") or {}
        alg_root = _bytes32_or_zero(
            roots.get("alg_policy_root"), field="policy_roots.alg_policy_root"
        )
        poies_root = _bytes32_or_zero(
            roots.get("poies_policy_root"), field="policy_roots.poies_policy_root"
        )

        cons = (net.get("consensus") or {}).get("poies", {})
        theta_initial = int(cons.get("theta_initial_munats", 1_000_000))
        if theta_initial <= 0:
            raise ValueError("consensus.theta_initial must be > 0")
        gamma_cfg = cons.get("gamma") or {}
        gamma_total_cap = int(gamma_cfg.get("total_cap_munats", 1_000_000))
        if gamma_total_cap <= 0:
            raise ValueError("consensus.gamma_total_cap must be > 0")

        rt_cfg = cons.get("retarget") or {}
        bounds_cfg = rt_cfg.get("clamp_ratio_per_window") or {}
        retarget = RetargetParams(
            window=int(rt_cfg.get("window_blocks", 2048)),
            ema_alpha=float(rt_cfg.get("ema_beta", 0.1)),
            bounds=RetargetBounds(
                min=float(bounds_cfg.get("min", 0.5)),
                max=float(bounds_cfg.get("max", 2.0)),
            ),
        )

        blocks_cfg = net.get("blocks") or {}
        issuance_cfg = net.get("issuance") or {}
        target_ms = issuance_cfg.get("target_block_interval_ms", 2000)
        try:
            target_seconds = float(target_ms) / 1000.0
        except Exception:
            target_seconds = 2.0
        block_limits = BlockLimits(
            target_seconds=target_seconds,
            max_bytes=int(blocks_cfg.get("max_bytes", 1_500_000)),
            max_gas=int(blocks_cfg.get("max_gas", 20_000_000)),
            tx_max_bytes=int(
                blocks_cfg.get("tx_max_bytes", blocks_cfg.get("max_bytes", 1_500_000))
            ),
            min_gas_price=int(blocks_cfg.get("min_gas_price", 0)),
        )

        return cls(
            chain_id=chain_id,
            chain_name=chain_name,
            genesis_time=genesis_time,
            genesis_hash=genesis_hash,
            alg_policy_root=alg_root,
            poies_policy_root=poies_root,
            theta_initial=theta_initial,
            gamma_total_cap=gamma_total_cap,
            retarget=retarget,
            block=block_limits,
        )

    @classmethod
    def load_yaml(
        cls,
        path: os.PathLike[str] | str,
        *,
        chain_id_hint: int | None = None,
        network_name: str | None = None,
    ) -> "ChainParams":
        """
        Load from YAML file on disk. Requires PyYAML at runtime.
        """
        global yaml
        if yaml is None:
            try:
                import yaml as _yaml  # type: ignore
            except Exception as e:  # pragma: no cover
                raise RuntimeError(
                    "PyYAML is required to load YAML params. "
                    "Install with `pip install pyyaml`."
                ) from e
            yaml = _yaml  # type: ignore
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, Mapping):
            raise TypeError("YAML root must be a mapping")
        if "chain" in data:
            return cls.from_mapping(data)
        if "networks" in data:
            return cls._from_networks_mapping(
                data, chain_id_hint=chain_id_hint, network_name=network_name
            )
        raise KeyError("chain")

    def to_public_dict(self) -> Mapping[str, Any]:
        """
        A minimal, JSON-friendly public view of core params (for RPC).
        """

        def b32(x: bytes) -> str:
            return "0x" + x.hex()

        return {
            "chain": {"id": self.chain_id, "name": self.chain_name},
            "genesis": {"time": self.genesis_time, "hash": b32(self.genesis_hash)},
            "policy_roots": {
                "alg_policy_root": b32(self.alg_policy_root),
                "poies_policy_root": b32(self.poies_policy_root),
            },
            "consensus": {
                "theta_initial": self.theta_initial,
                "gamma_total_cap": self.gamma_total_cap,
                "retarget": {
                    "window": self.retarget.window,
                    "ema_alpha": self.retarget.ema_alpha,
                    "bounds": {
                        "min": self.retarget.bounds.min,
                        "max": self.retarget.bounds.max,
                    },
                },
            },
            "block": {
                "target_seconds": self.block.target_seconds,
                "max_bytes": self.block.max_bytes,
                "max_gas": self.block.max_gas,
                "tx_max_bytes": self.block.tx_max_bytes,
                "min_gas_price": self.block.min_gas_price,
            },
        }


# -------- discovery helpers --------


def default_params_path(env_var: str = "ANIMICA_PARAMS") -> Path:
    """
    Resolve the default spec/params.yaml path:
      1) $ANIMICA_PARAMS if set
      2) repo root relative to this file: ../../spec/params.yaml
    """
    override = os.environ.get(env_var)
    if override:
        return Path(override).expanduser().resolve()
    # .../animica/core/types/params.py → repo_root/spec/params.yaml
    here = Path(__file__).resolve()
    repo_root = here.parents[2]
    return repo_root / "spec" / "params.yaml"


def load_default_params(
    path: Optional[os.PathLike[str] | str] = None,
    *,
    chain_id_hint: int | None = None,
    network_name: str | None = None,
) -> ChainParams:
    """
    Load params from the default location (or provided path).
    """
    p = Path(path) if path is not None else default_params_path()
    return ChainParams.load_yaml(
        p, chain_id_hint=chain_id_hint, network_name=network_name
    )


# -------- tiny CLI for sanity --------


def _main(argv: list[str]) -> int:
    import json

    p = default_params_path() if len(argv) < 2 else Path(argv[1])
    params = ChainParams.load_yaml(p)
    print(json.dumps(params.to_public_dict(), indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    import sys

    raise SystemExit(_main(sys.argv))
