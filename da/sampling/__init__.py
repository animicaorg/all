"""
Animica • DA • Sampling package

This package contains the light client Data Availability Sampling (DAS) logic:

Modules (added in this directory):
  - sampler.py       : active sampler that executes a sampling plan against a DA service
  - probability.py   : probability math (p_fail bounds, required sample counts)
  - light_client.py  : light verifier that combines header DA-root + samples to decide availability
  - verifier.py      : proof verification for individual samples (branches, indices, roots)
  - scheduler.py     : periodic/background sampling policies for full/light nodes
  - queries.py       : helpers to construct sampling plans (uniform / stratified / custom)

This __init__ exposes a few convenience entrypoints via lazy imports so importing
`da.sampling` is lightweight and does not pull heavy deps until you call them.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any, Iterable, Mapping, Optional

# Re-export package version (from top-level da.version)
try:  # pragma: no cover - tiny wiring
    from da.version import __version__  # type: ignore
except Exception:  # pragma: no cover
    __version__ = "0.0.0+unknown"

__all__ = [
    "DataAvailabilitySampler",
    "plan_uniform",
    "plan_stratified",
    "verify_samples",
    "light_verify",
]

# ----------------------------- Lazy wiring ---------------------------------


def _lazy(module: str, attr: str) -> Any:
    try:
        mod = import_module(module)
    except Exception as e:  # pragma: no cover
        raise RuntimeError(
            f"Required module '{module}' is not available yet. "
            f"Make sure '{module.split('.')[-1]}.py' exists and is importable."
        ) from e
    try:
        return getattr(mod, attr)
    except AttributeError as e:  # pragma: no cover
        raise RuntimeError(f"Attribute '{attr}' not found in module '{module}'.") from e


# Public convenience call-throughs (keep import-time side effects minimal)

def DataAvailabilitySampler(*args, **kwargs) -> Any:
    """
    Factory: returns an instance of sampling.sampler.DataAvailabilitySampler.
    """
    cls = _lazy("da.sampling.sampler", "DataAvailabilitySampler")
    return cls(*args, **kwargs)


def plan_uniform(*, population_size: int, sample_count: int, seed: Optional[int] = None) -> Any:
    """
    Build a uniform random sampling plan over a population of shares/leaves.
    """
    fn = _lazy("da.sampling.queries", "plan_uniform")
    return fn(population_size=population_size, sample_count=sample_count, seed=seed)


def plan_stratified(*, strata: Mapping[str, int], per_stratum: Mapping[str, int], seed: Optional[int] = None) -> Any:
    """
    Build a stratified sampling plan (e.g., by row/column groups).
    """
    fn = _lazy("da.sampling.queries", "plan_stratified")
    return fn(strata=strata, per_stratum=per_stratum, seed=seed)


def verify_samples(*args, **kwargs) -> Any:
    """
    Verify a set of samples and their proofs against a commitment/root.
    """
    fn = _lazy("da.sampling.verifier", "verify_samples")
    return fn(*args, **kwargs)


def light_verify(*args, **kwargs) -> Any:
    """
    Light client check: header DA-root + sample proofs => availability boolean (+ report).
    """
    fn = _lazy("da.sampling.light_client", "light_verify")
    return fn(*args, **kwargs)
