"""
zk.integration.policy
=====================

Allowlist & resource policy for zero-knowledge verification.

What this module provides
-------------------------
- **Allowlist** of circuit IDs that are permitted to be verified on-chain.
- **Per-kind limits** on proof/VK sizes and public inputs.
- **Gas/units schedule** to estimate deterministic cost for verification,
  returned to the caller (e.g., execution/capabilities host).

Typical usage
-------------
    from zk.integration.policy import (
        DEFAULT_POLICY, load_policy, check_and_meter, PolicyError
    )
    from zk.integration.types import ProofEnvelope

    env = ProofEnvelope(
        kind="groth16_bn254",
        proof=...,
        public_inputs=[...],
        vk_ref="counter_groth16_bn254@1",
        meta={"circuit_id": "counter_groth16_bn254@1"},
    )

    # Raises if not allowed or over limits; returns integer "units" / gas
    units = check_and_meter(env)

Design notes
------------
- We keep **formats/toolchains out of policy**; only sizes and counts matter.
- Gas/units here are *chain-local abstractions* (not EVM gas). Tweak freely.
- Limits are enforced on canonical JSON byte lengths (sorted keys, compact).

You may override defaults by loading a JSON/YAML file with the same structure
as `Policy.model()` (see `load_policy`).

License: MIT
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional

# Local helpers/types
from zk.integration.types import ProofEnvelope, canonical_json_bytes

# =============================================================================
# Errors
# =============================================================================


class PolicyError(Exception):
    """Base class for policy violations."""


class NotAllowedCircuit(PolicyError):
    """Circuit ID is not in the allowlist."""


class LimitExceeded(PolicyError):
    """A configured resource limit was exceeded (proof size, vk size, inputs)."""


# =============================================================================
# Data structures
# =============================================================================


@dataclass(frozen=True)
class SizeLimits:
    """Per-kind hard limits."""

    max_proof_bytes: int
    max_vk_bytes: int
    max_public_inputs: int


@dataclass(frozen=True)
class GasRule:
    """
    Cost schedule for a verifier *kind*.

    All fields are non-negative integers; final cost is:
        cost = base
             + per_public_input * num_public_inputs
             + per_proof_byte   * proof_bytes
             + per_vk_byte      * vk_bytes
             + per_opening      * kzg_openings   (if applicable)
    """

    base: int = 0
    per_public_input: int = 0
    per_proof_byte: int = 0
    per_vk_byte: int = 0
    per_opening: int = 0  # used by PLONK/KZG families


@dataclass(frozen=True)
class Policy:
    """
    Top-level ZK verification policy.

    Fields:
      allowlist: exact circuit_ids allowed (e.g., "poseidon2_arity3_plonk_kzg_bn254@1").
                 Use {"*"} to allow any circuit id.
      limits: map of verifier kind → SizeLimits
      gas:    map of verifier kind → GasRule
    """

    allowlist: frozenset[str] = field(
        default_factory=lambda: frozenset(
            {
                "counter_groth16_bn254@1",
                "poseidon2_arity3_plonk_kzg_bn254@1",
                "merkle_membership_air_stark_fri@0",
            }
        )
    )
    limits: Mapping[str, SizeLimits] = field(
        default_factory=lambda: {
            # Conservative ceilings; adjust with real benchmarks
            "groth16_bn254": SizeLimits(
                max_proof_bytes=128_000,  # snarkjs proof json is small; ceiling is generous
                max_vk_bytes=256_000,  # Groth16 VKs are small; allow cushion
                max_public_inputs=64,
            ),
            "plonk_kzg_bn254": SizeLimits(
                max_proof_bytes=256_000,  # PLONK proofs typically few KB; ceiling generous
                max_vk_bytes=1_048_576,  # 1 MiB; VK may include commitments, selector data, etc.
                max_public_inputs=128,
            ),
            "stark_fri_merkle": SizeLimits(
                max_proof_bytes=512_000,  # toy STARK/Merkle proof (demo)
                max_vk_bytes=256_000,  # FRI params or AIR metadata
                max_public_inputs=16,
            ),
        }
    )
    gas: Mapping[str, GasRule] = field(
        default_factory=lambda: {
            # Numbers are chain-local "units" (not EVM gas). Tune with real benches.
            "groth16_bn254": GasRule(
                base=250_000,
                per_public_input=12_000,
                per_proof_byte=2,
                per_vk_byte=0,
            ),
            "plonk_kzg_bn254": GasRule(
                base=420_000,
                per_public_input=14_000,
                per_proof_byte=2,
                per_vk_byte=0,
                per_opening=95_000,  # single-opening KZG demo; set openings=1 unless hinted
            ),
            "stark_fri_merkle": GasRule(
                base=300_000,
                per_public_input=2_000,
                per_proof_byte=1,
                per_vk_byte=0,
            ),
        }
    )

    # ---- helpers ----

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a plain dict for JSON/YAML."""
        return {
            "allowlist": sorted(self.allowlist),
            "limits": {k: asdict(v) for k, v in self.limits.items()},
            "gas": {k: asdict(v) for k, v in self.gas.items()},
        }

    @staticmethod
    def from_mapping(m: Mapping[str, Any]) -> "Policy":
        """Create a Policy from a JSON/YAML-like mapping."""
        allow = frozenset(m.get("allowlist", []) or [])
        limits_m = m.get("limits", {}) or {}
        gas_m = m.get("gas", {}) or {}

        limits: Dict[str, SizeLimits] = {}
        for k, v in limits_m.items():
            limits[k] = SizeLimits(
                max_proof_bytes=int(v.get("max_proof_bytes", 0)),
                max_vk_bytes=int(v.get("max_vk_bytes", 0)),
                max_public_inputs=int(v.get("max_public_inputs", 0)),
            )

        gas: Dict[str, GasRule] = {}
        for k, v in gas_m.items():
            gas[k] = GasRule(
                base=int(v.get("base", 0)),
                per_public_input=int(v.get("per_public_input", 0)),
                per_proof_byte=int(v.get("per_proof_byte", 0)),
                per_vk_byte=int(v.get("per_vk_byte", 0)),
                per_opening=int(v.get("per_opening", 0)),
            )

        return Policy(allowlist=allow, limits=limits, gas=gas)


# Default singleton
DEFAULT_POLICY = Policy()


# =============================================================================
# Loading / overrides
# =============================================================================


def load_policy(
    path: Optional[Path] = None, *, fallback: Policy = DEFAULT_POLICY
) -> Policy:
    """
    Load a Policy from a JSON or YAML file. If `path` is None or missing,
    returns the provided `fallback` (DEFAULT_POLICY).
    """
    if path is None:
        return fallback
    p = Path(path)
    if not p.exists():
        return fallback

    text = p.read_text(encoding="utf-8")
    if text.lstrip().startswith("{"):
        import json

        data = json.loads(text)
    else:
        try:
            import yaml  # type: ignore
        except Exception as e:
            raise RuntimeError(
                f"{p} appears to be YAML but PyYAML isn't installed: {e}"
            )
        data = yaml.safe_load(text) or {}

    return Policy.from_mapping(data)


# =============================================================================
# Enforcement & metering
# =============================================================================


def _len_bytes(obj: Any) -> int:
    """Length of canonical JSON representation in bytes."""
    return len(canonical_json_bytes(obj))


def _resolve_circuit_id(env: ProofEnvelope) -> Optional[str]:
    """
    Prefer explicit `meta.circuit_id`, fallback to `vk_ref` if it looks like an id,
    else None.
    """
    cid = (env.meta or {}).get("circuit_id") if isinstance(env.meta, dict) else None
    if isinstance(cid, str) and cid:
        return cid
    if isinstance(env.vk_ref, str) and env.vk_ref:
        return env.vk_ref
    return None


def _allowed(policy: Policy, circuit_id: Optional[str]) -> bool:
    allow = policy.allowlist
    if "*" in allow:
        return True
    return isinstance(circuit_id, str) and circuit_id in allow


def estimate_units(
    policy: Policy,
    *,
    kind: str,
    proof_bytes: int,
    vk_bytes: int,
    num_public_inputs: int,
    kzg_openings: int = 1,
) -> int:
    """
    Deterministic unit estimate for verification under `policy`.
    """
    rule = policy.gas.get(kind)
    if rule is None:
        # Unknown kind: be conservative
        rule = GasRule(base=500_000, per_public_input=10_000, per_proof_byte=2)

    units = rule.base
    units += rule.per_public_input * max(0, int(num_public_inputs))
    units += rule.per_proof_byte * max(0, int(proof_bytes))
    units += rule.per_vk_byte * max(0, int(vk_bytes))
    if rule.per_opening and kzg_openings > 0:
        units += rule.per_opening * int(kzg_openings)
    return int(units)


def check_limits(
    policy: Policy,
    *,
    kind: str,
    proof_bytes: int,
    vk_bytes: int,
    num_public_inputs: int,
) -> None:
    """
    Raise LimitExceeded if any per-kind limits are violated.
    """
    lim = policy.limits.get(kind)
    if lim is None:
        # For unknown kinds, apply a strict but generic limit.
        lim = SizeLimits(
            max_proof_bytes=1_000_000, max_vk_bytes=2_000_000, max_public_inputs=256
        )

    if proof_bytes > lim.max_proof_bytes:
        raise LimitExceeded(
            f"proof too large for kind={kind}: {proof_bytes} > {lim.max_proof_bytes}"
        )
    if vk_bytes > lim.max_vk_bytes:
        raise LimitExceeded(
            f"vk too large for kind={kind}: {vk_bytes} > {lim.max_vk_bytes}"
        )
    if num_public_inputs > lim.max_public_inputs:
        raise LimitExceeded(
            f"too many public inputs for kind={kind}: {num_public_inputs} > {lim.max_public_inputs}"
        )


def check_and_meter(
    env: ProofEnvelope,
    *,
    policy: Policy = DEFAULT_POLICY,
    kzg_openings_hint: Optional[int] = None,
) -> int:
    """
    Enforce allowlist & size limits for `env`, and return a deterministic unit cost.

    Args:
        env: ProofEnvelope (vk_ref or vk should be present).
        policy: Policy to enforce (defaults to DEFAULT_POLICY).
        kzg_openings_hint: If provided, overrides default '1' opening for PLONK/KZG.

    Returns:
        int: metered units for this verification.

    Raises:
        NotAllowedCircuit
        LimitExceeded
        ValueError (if env lacks vk material or required fields)
    """
    env.require_vk_material()

    circuit_id = _resolve_circuit_id(env)
    if not _allowed(policy, circuit_id):
        raise NotAllowedCircuit(
            f"circuit_id '{circuit_id}' is not allowed (kind={env.kind})"
        )

    # Compute sizes on canonical JSON encodings
    proof_bytes = _len_bytes(env.proof)
    vk_bytes = _len_bytes(env.vk) if env.vk is not None else 0
    n_pub = len(env.public_inputs or [])

    # Enforce per-kind limits
    check_limits(
        policy,
        kind=env.kind,
        proof_bytes=proof_bytes,
        vk_bytes=vk_bytes,
        num_public_inputs=n_pub,
    )

    # Estimate cost
    openings = kzg_openings_hint or (1 if env.kind == "plonk_kzg_bn254" else 0)
    return estimate_units(
        policy,
        kind=env.kind,
        proof_bytes=proof_bytes,
        vk_bytes=vk_bytes,
        num_public_inputs=n_pub,
        kzg_openings=openings,
    )


# =============================================================================
# (Optional) tiny CLI for ops/docs
# =============================================================================


def _build_argparser():
    import argparse

    ap = argparse.ArgumentParser(description="ZK policy inspector")
    ap.add_argument("--policy", type=Path, help="Path to JSON/YAML policy file")
    ap.add_argument("--format", choices=("json", "yaml"), default="json")
    return ap


def _main(argv=None):
    import json

    try:
        import yaml  # type: ignore
    except Exception:
        yaml = None  # type: ignore

    ap = _build_argparser()
    args = ap.parse_args(argv)

    pol = load_policy(args.policy) if args.policy else DEFAULT_POLICY
    data = pol.to_dict()

    if args.format == "json":
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        if yaml is None:
            raise SystemExit("PyYAML not installed; use --format json")
        print(yaml.safe_dump(data, sort_keys=True))


if __name__ == "__main__":
    _main()
