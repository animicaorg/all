"""
Animica • DA • Light Client Verification

Given a DA commitment (e.g., an NMT root embedded in a block header) and one
or more batches of sample proofs returned by a DA retrieval service, decide if
the data is *available* with a target failure probability bound.

This module does not fetch proofs itself (see da.sampling.sampler) — it only
verifies provided batches and aggregates the result into a simple verdict.

Usage (high level)
------------------
from da.sampling.light_client import light_verify, LightVerifyConfig

report = light_verify(
    commitment=bytes.fromhex("<root hex without 0x>"),
    population_size=65536,                 # number of shares/leaves covered by the root
    sample_payloads=[proof_batch_1, proof_batch_2, ...],  # JSON-like dicts from the DA API
    config=LightVerifyConfig(target_p_fail=1e-9),
)

if report.available:
    print("DA available (p_fail <= ", report.p_fail_estimate, ")")
else:
    print("Unavailable:", report.reasons)

Contract
--------
- Each element of `sample_payloads` must contain enough material for
  `da.sampling.verifier.verify_samples(commitment, payload)` to verify and
  to return which indices were good/bad. See that module for exact fields.
- No network I/O here; everything is pure and deterministic given inputs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, List, Mapping, Optional, Sequence, Tuple, Callable, Dict, Set
import binascii

VerifyFn = Callable[[bytes, Mapping[str, Any]], Any]  # returns shapes normalized below


# ------------------------------- Config & Report ----------------------------

@dataclass(frozen=True)
class LightVerifyConfig:
    """
    Tuning parameters for the light verification decision.
    """
    target_p_fail: float = 1e-9
    without_replacement: bool = True
    require_no_bad: bool = True          # any bad sample ⇒ unavailable
    min_ok_ratio: float = 1.0            # fraction of checked samples that must be OK (≤1.0)
    require_min_unique: int = 1          # require at least this many unique sample indices


@dataclass
class LightVerifyReport:
    """
    Result of a light-verify pass.
    """
    commitment: bytes
    population_size: int
    total_samples: int
    unique_samples: int
    ok_indices: List[int]
    bad_indices: List[int]
    p_fail_estimate: float
    available: bool
    reasons: List[str] = field(default_factory=list)

    def ok_ratio(self) -> float:
        if self.total_samples <= 0:
            return 0.0
        return len(self.ok_indices) / float(self.total_samples)


# ------------------------------- Public API --------------------------------

def light_verify(
    *,
    commitment: bytes | str,
    population_size: int,
    sample_payloads: Sequence[Mapping[str, Any]],
    config: Optional[LightVerifyConfig] = None,
    verify_fn: Optional[VerifyFn] = None,
) -> LightVerifyReport:
    """
    Verify sample proofs against the commitment and decide availability.

    Parameters
    ----------
    commitment :
        NMT/DA root as raw bytes or hex string (with or without '0x').
    population_size :
        Total number of shares/leaves addressed by the root (N).
    sample_payloads :
        One or more JSON-like proof batches returned by the DA API.
    config :
        Decision thresholds (p_fail target, min_ok ratio, etc).
    verify_fn :
        Optional override for the verifier. By default, lazily imports
        `da.sampling.verifier.verify_samples`.

    Returns
    -------
    LightVerifyReport with `available` True/False and reasons.
    """
    cfg = config or LightVerifyConfig()
    root = _as_bytes(commitment)

    # Resolve verifier lazily to avoid heavy imports until needed.
    if verify_fn is None:
        verify_fn = _lazy_import("da.sampling.verifier", "verify_samples")

    # Accumulate per-batch results
    ok_set: Set[int] = set()
    bad_set: Set[int] = set()
    total_seen = 0

    for payload in sample_payloads:
        vr = verify_fn(root, payload)
        ok_idx, bad_idx = _normalize_verify_result(vr, payload)
        total_seen += len(ok_idx) + len(bad_idx)
        ok_set.update(int(i) for i in ok_idx)
        bad_set.update(int(i) for i in bad_idx)

    # Decision logic
    reasons: List[str] = []
    unique_samples = len(ok_set | bad_set)

    # Estimate p_fail bound using unique sample count
    p_fail = _estimate_p_fail(
        population_size=population_size,
        total_samples=unique_samples,
        without_replacement=cfg.without_replacement,
    )

    if cfg.require_no_bad and bad_set:
        reasons.append(f"{len(bad_set)} sample(s) failed verification")

    ok_ratio = (len(ok_set) / float(total_seen)) if total_seen > 0 else 0.0
    if ok_ratio < cfg.min_ok_ratio:
        reasons.append(f"ok_ratio {ok_ratio:.6f} < required {cfg.min_ok_ratio:.6f}")

    if unique_samples < cfg.require_min_unique:
        reasons.append(f"unique_samples {unique_samples} < required {cfg.require_min_unique}")

    if p_fail > cfg.target_p_fail:
        reasons.append(f"p_fail {p_fail:.3e} > target {cfg.target_p_fail:.3e}")

    available = len(reasons) == 0

    return LightVerifyReport(
        commitment=root,
        population_size=int(population_size),
        total_samples=int(total_seen),
        unique_samples=int(unique_samples),
        ok_indices=sorted(ok_set),
        bad_indices=sorted(bad_set),
        p_fail_estimate=float(p_fail),
        available=available,
        reasons=reasons,
    )


# ------------------------------- Internals ---------------------------------

def _as_bytes(x: bytes | str) -> bytes:
    if isinstance(x, bytes):
        return x
    s = x.strip().lower()
    if s.startswith("0x"):
        s = s[2:]
    try:
        return binascii.unhexlify(s)
    except Exception as e:
        raise ValueError("commitment must be raw bytes or hex string") from e


def _normalize_verify_result(v: Any, payload: Mapping[str, Any]) -> Tuple[List[int], List[int]]:
    """
    Accept result shapes from `verify_samples`:

      - (ok_indices, bad_indices)
      - {"ok_indices":[...], "bad_indices":[...]}
      - True / False  (then treat all indices present in payload as ok/bad)

    The payload *should* include the "indices" field when verifier returns bool.
    """
    if isinstance(v, tuple) and len(v) == 2:
        a, b = v
        return list(map(int, a)), list(map(int, b))
    if isinstance(v, Mapping):
        ok = v.get("ok_indices") or v.get("ok") or []
        bad = v.get("bad_indices") or v.get("bad") or []
        return list(map(int, ok)), list(map(int, bad))
    # Fallback to boolean; try to read indices from payload
    indices = payload.get("indices") or payload.get("sample_indices") or []
    if isinstance(v, bool):
        if v:
            return list(map(int, indices)), []
        else:
            return [], list(map(int, indices))
    # Unexpected shape: be permissive and assume ok
    return list(map(int, payload.get("indices", []))), []


def _estimate_p_fail(
    *,
    population_size: int,
    total_samples: int,
    without_replacement: bool,
) -> float:
    """
    Upper-bound p_fail under the weakest non-trivial corruption assumption (≥1 bad share).
    """
    est = _lazy_import("da.sampling.probability", "estimate_p_fail_upper")
    return float(
        est(
            population_size=int(population_size),
            sample_count=int(total_samples),
            assumed_corrupt_fraction=None,      # implies "≥ 1 bad share"
            without_replacement=bool(without_replacement),
        )
    )


def _lazy_import(module: str, attr: str) -> Any:
    import importlib
    mod = importlib.import_module(module)
    try:
        return getattr(mod, attr)
    except AttributeError as e:  # pragma: no cover
        raise RuntimeError(f"Attribute '{attr}' not found in module '{module}'") from e


__all__ = [
    "LightVerifyConfig",
    "LightVerifyReport",
    "light_verify",
]
