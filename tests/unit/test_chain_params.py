# SPDX-License-Identifier: Apache-2.0
"""
Validate chain/spec parameters:
- Load a params JSON (env CHAIN_PARAMS_FILE or common defaults)
- Validate against a minimal schema (structure & types)
- Check premine equals the sum of distribution buckets
- Enforce updated amounts for treasury and dev fund

This test is resilient to different nesting layouts (e.g. params embedded in a
larger genesis file). It will walk nested dicts to find an object containing
`premine` and `distribution`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

# --- Discovery ---------------------------------------------------------------


def _candidate_paths() -> list[Path]:
    """Potential locations for chain params / genesis-like JSON."""
    env = os.getenv("CHAIN_PARAMS_FILE")
    paths: list[Path] = []
    if env:
        paths.append(Path(env))
    # Common fallbacks (repo-local)
    paths += [
        Path("spec/chain_params.json"),
        Path("spec/genesis.json"),
        Path("config/chain_params.json"),
        Path("config/genesis.json"),
        Path("genesis.json"),
        Path("tests/fixtures/chain_params.json"),
        Path("tests/fixtures/genesis.json"),
    ]
    # Make absolute relative to CWD once; keep order
    out: list[Path] = []
    seen: set[str] = set()
    for p in paths:
        ap = p.resolve()
        if str(ap) not in seen:
            out.append(ap)
            seen.add(str(ap))
    return out


def _load_first_existing() -> tuple[Path, Dict[str, Any]]:
    for p in _candidate_paths():
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return p, data
            except Exception:
                # try next file
                pass
    pytest.skip(
        "Could not locate chain params JSON. Set CHAIN_PARAMS_FILE to an existing file.",
        allow_module_level=True,
    )


# --- Extraction --------------------------------------------------------------

Params = Dict[str, Any]


def _find_params_blob(obj: Dict[str, Any]) -> Optional[Params]:
    """
    Walk the dict to locate a sub-dict with keys {premine, distribution}.
    Breadth-first over nested dicts, limited depth to avoid pathological cases.
    """
    from collections import deque

    def is_params(d: Dict[str, Any]) -> bool:
        return isinstance(d.get("premine"), (int, float)) and isinstance(
            d.get("distribution"), dict
        )

    # Quick checks for common keys
    for key in ("params", "chain_params", "chain", "genesis", "config"):
        sub = obj.get(key)
        if isinstance(sub, dict) and is_params(sub):
            return sub

    if is_params(obj):
        return obj

    # BFS search up to reasonable depth
    q = deque([(obj, 0)])
    while q:
        cur, depth = q.popleft()
        if depth > 6:
            continue
        for _, v in cur.items():
            if isinstance(v, dict):
                if is_params(v):
                    return v
                q.append((v, depth + 1))
            elif isinstance(v, list):
                for item in v:
                    if isinstance(item, dict):
                        if is_params(item):
                            return item
                        q.append((item, depth + 1))
    return None


def _normalize_distribution(dist: Dict[str, Any]) -> Dict[str, int]:
    """
    Normalize distribution keys and coerce to ints.
    Accepts either 'dev_fund' or 'dev_reserve' (aliases).
    """

    def to_int(x: Any) -> int:
        if isinstance(x, bool):
            raise TypeError("boolean is not a valid numeric amount")
        if isinstance(x, (int,)):
            return int(x)
        if isinstance(x, float):
            # amounts should be integral units; allow float if whole
            if abs(x - round(x)) < 1e-9:
                return int(round(x))
            raise ValueError(f"non-integer amount {x}")
        if isinstance(x, str) and x.strip().isdigit():
            return int(x.strip())
        raise TypeError(f"unsupported amount type: {type(x)}")

    out: Dict[str, int] = {}
    # Canonical keys we expect to see
    for k in ("treasury", "aicf", "foundation", "faucet", "dev_fund", "dev_reserve"):
        if k in dist:
            out[k] = to_int(dist[k])

    # If only one of dev_fund/dev_reserve exists, mirror to the canonical 'dev_fund'
    if "dev_fund" not in out and "dev_reserve" in out:
        out["dev_fund"] = out["dev_reserve"]
    return out


# --- Minimal schema checks ---------------------------------------------------


def _assert_schema(params: Params) -> None:
    assert "premine" in params, "params must contain 'premine'"
    assert "distribution" in params and isinstance(
        params["distribution"], dict
    ), "params must contain object 'distribution'"

    premine = params["premine"]
    assert isinstance(premine, (int, float)), "'premine' must be a number"
    assert premine > 0, "'premine' must be positive"

    dist = _normalize_distribution(params["distribution"])
    for req in ("treasury", "aicf", "foundation", "faucet"):
        assert req in dist, f"distribution missing required key '{req}'"
        assert (
            isinstance(dist[req], int) and dist[req] >= 0
        ), f"'{req}' must be a non-negative integer"

    # At least one dev key present
    assert ("dev_fund" in dist) or (
        "dev_reserve" in dist
    ), "distribution must include 'dev_fund' (or legacy 'dev_reserve')"
    if "dev_fund" in dist:
        assert (
            isinstance(dist["dev_fund"], int) and dist["dev_fund"] >= 0
        ), "'dev_fund' must be a non-negative integer"


# --- Tests -------------------------------------------------------------------


def test_params_schema_and_sum():
    path, root = _load_first_existing()
    params = _find_params_blob(root)
    assert (
        params is not None
    ), f"Could not locate 'premine'+'distribution' object in {path}"
    _assert_schema(params)

    premine = int(round(float(params["premine"])))
    dist_norm = _normalize_distribution(params["distribution"])

    total = sum(
        v
        for k, v in dist_norm.items()
        if k in {"treasury", "aicf", "foundation", "faucet", "dev_fund"}
    )
    assert total == premine, (
        f"Distribution sum ({total:,}) must equal premine ({premine:,}). "
        f"Got: {dist_norm}"
    )

    # Non-negative and reasonable magnitudes
    for k, v in dist_norm.items():
        assert v >= 0, f"{k} cannot be negative"
        # Guard against absurd magnitudes by mistake (e.g., wei vs whole units)
        assert v <= 10_000_000_000_000, f"{k} looks too large: {v}"


def test_updated_amounts_treasury_and_devfund():
    """
    Project decision: enforce updated amounts:
      - premine: 18,000,000
      - treasury: 8,800,000
      - dev fund (aka dev_reserve): 2,180,000
    Skip if STRICT_DISTRIBUTION=0.
    """
    if os.getenv("STRICT_DISTRIBUTION", "1").lower() in {"0", "false", "no"}:
        pytest.skip("STRICT_DISTRIBUTION disabled")

    _, root = _load_first_existing()
    params = _find_params_blob(root)
    assert params is not None, "params not found"
    premine = int(round(float(params["premine"])))
    dist = _normalize_distribution(params["distribution"])

    assert premine == 18_000_000, f"premine must be 18,000,000; got {premine:,}"
    assert (
        dist.get("treasury") == 8_800_000
    ), f"treasury must be 8,800,000; got {dist.get('treasury'):,}"
    # Accept either 'dev_fund' or 'dev_reserve' source; normalized to dev_fund.
    assert (
        dist.get("dev_fund") == 2_180_000
    ), f"dev_fund must be 2,180,000; got {dist.get('dev_fund'):,}"


def test_percentages_reasonable_monotone():
    """
    Soft sanity checks: ensure no single bucket exceeds premine, and
    that major buckets are non-zero.
    """
    _, root = _load_first_existing()
    params = _find_params_blob(root)
    assert params is not None, "params not found"
    premine = int(round(float(params["premine"])))
    dist = _normalize_distribution(params["distribution"])

    for k, v in dist.items():
        assert v <= premine, f"{k} allocation exceeds premine"
    for k in ("treasury", "aicf", "foundation", "faucet", "dev_fund"):
        assert dist.get(k, 0) > 0, f"{k} should be positive"
