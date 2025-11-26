"""
PoIES policy loader & model.

Reads spec/poies_policy.yaml and exposes a strongly-typed, validated
representation used by scorer/validator:

- Γ (gamma_cap_micro): total per-block ψ cap (µ-nats).
- Per-type caps: max ψ per proof-type (and per single-proof maxima).
- Escort/diversity rule q: ensure a minimum fraction of ψ is "useful".
- Weights: mapping knobs for how verified proof metrics map to ψ inputs
  (actual mapping lives in proofs/policy_adapter.py; this file just provides
  tuned weights from the policy).

The YAML format is intentionally permissive; unknown keys are ignored
(to allow forward-compatible rollout). All numeric units here are µ-nats
unless otherwise stated (basis points = bp = 1/100 of a percent).

This module is pure (no DB). It is imported by consensus.scorer,
consensus.validator and mining.proof_selector.

Example minimal policy YAML (see spec/poies_policy.yaml for the canonical file):

version: 1
gamma_cap_micro: 12000000
escort:
  enabled: true
  min_useful_ratio_bp: 3000   # 30% of Σψ must come from useful types
  useful_types: [AI, QUANTUM, STORAGE, VDF]
caps:
  per_type_micro:
    HASH:    8000000
    AI:      8000000
    QUANTUM: 8000000
    STORAGE: 4000000
    VDF:     2000000
  per_proof_micro_max:
    HASH:    4000000
    AI:      4000000
    QUANTUM: 4000000
    STORAGE: 2000000
    VDF:     1000000
weights:
  HASH:
    d_ratio_weight_micro_per_unit: 1000000
  AI:
    ai_units_weight_micro_per_unit: 50000
    traps_ratio_weight_micro_per_bp: 2000
    qos_weight_micro_per_bp: 1000
  QUANTUM:
    quantum_units_weight_micro_per_unit: 80000
    traps_ratio_weight_micro_per_bp: 3000
    qos_weight_micro_per_bp: 1000
  STORAGE:
    qos_weight_micro_per_bp: 500
  VDF:
    seconds_weight_micro_per_sec: 50000
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Mapping, Optional, Set, Any, Tuple
import json
import hashlib

try:
    import yaml  # PyYAML
except Exception:  # pragma: no cover - lightweight fallback for test environments
    import types

    def _jsonish_safe_load(data: Any) -> Any:
        """Minimal loader that accepts JSON as a subset of YAML for tests."""

        if isinstance(data, (bytes, bytearray)):
            data = data.decode("utf-8")
        return json.loads(data)

    yaml = types.SimpleNamespace(safe_load=_jsonish_safe_load)

from .errors import PolicyError
from .types import ProofType, ThetaMicro, GammaMicro, MicroNat


# ---------------------------
# Dataclasses (policy shapes)
# ---------------------------

@dataclass(frozen=True)
class TypeCap:
    """Per-proof-type caps (in µ-nats)."""
    per_type_micro: MicroNat           # Max Σψ for this type within a block
    per_proof_micro_max: MicroNat      # Max ψ contribution from a single proof of this type


@dataclass(frozen=True)
class EscortRule:
    """
    Diversity/escort rule: enforce that at least `min_useful_ratio_bp` basis
    points of the final accepted Σψ comes from a set of "useful" types.
    """
    enabled: bool
    min_useful_ratio_bp: int           # 0..10_000
    useful_types: Set[ProofType]       # e.g. {AI, QUANTUM, STORAGE, VDF}


@dataclass(frozen=True)
class Weights:
    """
    Per-type weight knobs (units are µ-nats per metric unit). Only a subset is
    meaningful per type; unused fields should be 0. The policy adapter will
    read only relevant knobs for each proof's metrics.

    Fields ending with `_per_unit` multiply a dimensionless "units" metric.
    Fields ending with `_per_bp` multiply basis points (0..10_000).
    """
    # HASH
    d_ratio_weight_micro_per_unit: int = 0        # ψ ≈ w * d_ratio

    # AI
    ai_units_weight_micro_per_unit: int = 0       # ψ += w * ai_units
    traps_ratio_weight_micro_per_bp: int = 0      # ψ += w * traps_bp
    qos_weight_micro_per_bp: int = 0              # ψ += w * qos_bp

    # QUANTUM
    quantum_units_weight_micro_per_unit: int = 0  # ψ += w * quantum_units

    # STORAGE
    storage_units_weight_micro_per_unit: int = 0  # optional hook for storage units

    # VDF
    seconds_weight_micro_per_sec: int = 0         # ψ += w * seconds_equiv


@dataclass(frozen=True)
class PoiesPolicy:
    """
    Canonical policy object used throughout consensus/mining:

    - version: monotonically increasing schema/content version
    - gamma_cap: block-level cap on Σψ
    - caps: per-type caps (TypeCap)
    - escort: optional EscortRule
    - weights: per-type Weights
    - policy_root: stable hash (sha3-256 over canonical JSON) used in headers
    """
    version: int
    gamma_cap: GammaMicro
    caps: Mapping[ProofType, TypeCap]
    escort: Optional[EscortRule]
    weights: Mapping[ProofType, Weights]
    policy_root: bytes

    # --------
    # Helpers
    # --------
    def to_canonical_json(self) -> bytes:
        """
        Canonical JSON for hashing: sorted keys, integers only, enums → names.
        This excludes `policy_root` itself to avoid self-reference.
        """
        def cap_to_dict(tp: TypeCap) -> dict:
            return {
                "per_type_micro": int(tp.per_type_micro),
                "per_proof_micro_max": int(tp.per_proof_micro_max),
            }

        def w_to_dict(w: Weights) -> dict:
            return {
                "d_ratio_weight_micro_per_unit": w.d_ratio_weight_micro_per_unit,
                "ai_units_weight_micro_per_unit": w.ai_units_weight_micro_per_unit,
                "traps_ratio_weight_micro_per_bp": w.traps_ratio_weight_micro_per_bp,
                "qos_weight_micro_per_bp": w.qos_weight_micro_per_bp,
                "quantum_units_weight_micro_per_unit": w.quantum_units_weight_micro_per_unit,
                "storage_units_weight_micro_per_unit": w.storage_units_weight_micro_per_unit,
                "seconds_weight_micro_per_sec": w.seconds_weight_micro_per_sec,
            }

        escort_dict = None
        if self.escort:
            escort_dict = {
                "enabled": self.escort.enabled,
                "min_useful_ratio_bp": self.escort.min_useful_ratio_bp,
                "useful_types": [pt.name for pt in sorted(self.escort.useful_types, key=lambda x: x.value)],
            }

        payload = {
            "version": self.version,
            "gamma_cap_micro": int(self.gamma_cap),
            "caps": {
                pt.name: cap_to_dict(cap) for pt, cap in sorted(self.caps.items(), key=lambda kv: kv[0].value)
            },
            "escort": escort_dict,
            "weights": {
                pt.name: w_to_dict(w) for pt, w in sorted(self.weights.items(), key=lambda kv: kv[0].value)
            },
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def hex_policy_root(self) -> str:
        return "0x" + self.policy_root.hex()


# ---------------------------
# YAML → Policy loader
# ---------------------------

def _parse_proof_type(name: str) -> ProofType:
    try:
        return ProofType[name.upper()]
    except KeyError as e:
        raise PolicyError(f"Unknown proof type '{name}' in policy") from e


def _get_int(map_: Mapping[str, Any], key: str, default: Optional[int] = None) -> int:
    if key in map_:
        v = map_[key]
        if not isinstance(v, int):
            raise PolicyError(f"Expected integer for '{key}', got {type(v).__name__}")
        if v < 0:
            raise PolicyError(f"Negative value for '{key}' not allowed")
        return v
    if default is not None:
        return default
    raise PolicyError(f"Missing required integer key '{key}'")


def _load_caps(caps_cfg: Mapping[str, Any], gamma_cap: int) -> Dict[ProofType, TypeCap]:
    per_type = caps_cfg.get("per_type_micro", {})
    per_proof = caps_cfg.get("per_proof_micro_max", {})

    if not isinstance(per_type, dict) or not isinstance(per_proof, dict):
        raise PolicyError("caps.per_type_micro and caps.per_proof_micro_max must be maps")

    caps: Dict[ProofType, TypeCap] = {}
    seen_types: Set[ProofType] = set()

    for k, v in per_type.items():
        pt = _parse_proof_type(k)
        seen_types.add(pt)
        pt_cap = _get_int(per_type, k)
        pp_cap = _get_int(per_proof, k) if k in per_proof else pt_cap  # default to same as per-type cap
        if pt_cap > gamma_cap:
            raise PolicyError(f"Per-type cap for {pt.name} exceeds gamma_cap")
        if pp_cap > pt_cap:
            raise PolicyError(f"Per-proof cap for {pt.name} exceeds per-type cap")
        caps[pt] = TypeCap(per_type_micro=pt_cap, per_proof_micro_max=pp_cap)

    # Fill missing types with zero caps (disabled)
    for pt in ProofType:
        if pt not in seen_types:
            caps[pt] = TypeCap(per_type_micro=0, per_proof_micro_max=0)

    return caps


def _load_escort(escort_cfg: Any) -> Optional[EscortRule]:
    if escort_cfg is None:
        return None
    if not isinstance(escort_cfg, dict):
        raise PolicyError("escort must be a map/object")

    enabled = bool(escort_cfg.get("enabled", False))
    min_bp = int(escort_cfg.get("min_useful_ratio_bp", 0))
    if not (0 <= min_bp <= 10_000):
        raise PolicyError("escort.min_useful_ratio_bp must be between 0 and 10_000")

    useful_raw = escort_cfg.get("useful_types", [])
    if not isinstance(useful_raw, list):
        raise PolicyError("escort.useful_types must be a list")
    useful: Set[ProofType] = set()
    for name in useful_raw:
        useful.add(_parse_proof_type(str(name)))

    return EscortRule(enabled=enabled, min_useful_ratio_bp=min_bp, useful_types=useful)


def _load_weights(weights_cfg: Any) -> Dict[ProofType, Weights]:
    if not isinstance(weights_cfg, dict):
        raise PolicyError("weights must be a map/object")

    out: Dict[ProofType, Weights] = {}
    # Defaults = all zeros; we only override present fields
    for pt in ProofType:
        out[pt] = Weights()

    def _update_from_map(pt: ProofType, m: Mapping[str, Any]) -> None:
        # Allow unknown keys (ignored)
        cur = out[pt].__dict__.copy()
        for key in cur.keys():
            if key in m:
                v = m[key]
                if not isinstance(v, int) or v < 0:
                    raise PolicyError(f"weights.{pt.name}.{key} must be a non-negative integer")
                cur[key] = v
        out[pt] = Weights(**cur)

    for k, v in weights_cfg.items():
        pt = _parse_proof_type(k)
        if not isinstance(v, dict):
            raise PolicyError(f"weights.{pt.name} must be a map/object")
        _update_from_map(pt, v)

    return out


def _compute_policy_root(canonical_json: bytes) -> bytes:
    # sha3-256 over canonical JSON; header binds this root for enforcement
    return hashlib.sha3_256(canonical_json).digest()


def load_poies_policy(yaml_path: str) -> PoiesPolicy:
    """
    Load and validate a policy from YAML file.

    Raises PolicyError on any validation failure.
    """
    try:
        with open(yaml_path, "rb") as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError as e:
        raise PolicyError(f"policy file not found: {yaml_path}") from e

    if not isinstance(data, dict):
        raise PolicyError("policy YAML must be a mapping at the top level")

    version = int(data.get("version", 1))
    gamma_cap = _get_int(data, "gamma_cap_micro")

    # Caps
    caps_cfg = data.get("caps", {})
    caps = _load_caps(caps_cfg, gamma_cap)

    # Escort
    escort = _load_escort(data.get("escort"))

    # Weights
    weights = _load_weights(data.get("weights", {}))

    # Sanity: per-type cap sum may exceed gamma_cap (that is allowed), but individual caps cannot.
    # Escort useful_types subset sanity
    if escort and escort.enabled and len(escort.useful_types) == 0 and escort.min_useful_ratio_bp > 0:
        raise PolicyError("escort.enabled is true but useful_types is empty")

    # Construct and hash
    dummy = PoiesPolicy(
        version=version,
        gamma_cap=GammaMicro(gamma_cap),
        caps=caps,
        escort=escort,
        weights=weights,
        policy_root=b"",  # temp
    )
    cj = dummy.to_canonical_json()
    root = _compute_policy_root(cj)

    return PoiesPolicy(
        version=version,
        gamma_cap=GammaMicro(gamma_cap),
        caps=caps,
        escort=escort,
        weights=weights,
        policy_root=root,
    )


# ---------------------------
# Convenience: from dict (tests)
# ---------------------------

def poies_policy_from_dict(cfg: Mapping[str, Any]) -> PoiesPolicy:
    """
    Build a PoiesPolicy directly from a dict (same shape as YAML),
    primarily for tests. Performs the same validations and hashing.
    """
    # Reuse the loader pieces but without file I/O
    if not isinstance(cfg, dict):
        raise PolicyError("policy dict must be a mapping at the top level")

    version = int(cfg.get("version", 1))
    gamma_cap = _get_int(cfg, "gamma_cap_micro")
    caps = _load_caps(cfg.get("caps", {}), gamma_cap)
    escort = _load_escort(cfg.get("escort"))
    weights = _load_weights(cfg.get("weights", {}))

    dummy = PoiesPolicy(
        version=version,
        gamma_cap=GammaMicro(gamma_cap),
        caps=caps,
        escort=escort,
        weights=weights,
        policy_root=b"",
    )
    cj = dummy.to_canonical_json()
    root = _compute_policy_root(cj)

    return PoiesPolicy(
        version=version,
        gamma_cap=GammaMicro(gamma_cap),
        caps=caps,
        escort=escort,
        weights=weights,
        policy_root=root,
    )


# ---------------------------
# Debug CLI (optional)
# ---------------------------

if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        print("usage: python -m consensus.policy path/to/spec/poies_policy.yaml")
        sys.exit(2)
    pol = load_poies_policy(sys.argv[1])
    print(f"version: {pol.version}")
    print(f"gamma_cap_micro: {int(pol.gamma_cap)}")
    print(f"policy_root: {pol.hex_policy_root()}")
    for pt, cap in pol.caps.items():
        print(f"  caps[{pt.name}]: per_type={cap.per_type_micro} per_proof_max={cap.per_proof_micro_max}")
    if pol.escort:
        e = pol.escort
        utypes = ",".join(t.name for t in sorted(e.useful_types, key=lambda x: x.value))
        print(f"escort: enabled={e.enabled} min_useful_ratio_bp={e.min_useful_ratio_bp} useful=[{utypes}]")
