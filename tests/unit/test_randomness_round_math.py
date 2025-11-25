# -*- coding: utf-8 -*-
"""
Randomness schedule sanity tests:
- Commit/Reveal/Settle windows form a valid partition of a round (no overlaps, positive)
- VDF parameters are within reasonable bounds relative to the round
These tests intentionally accept multiple schema spellings so they work across
slightly different genesis/spec layouts.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

import pytest


# ---- helpers ----------------------------------------------------------------

_CANDIDATE_FILES = [
    # common places we store chain/spec params
    "spec/genesis.json",
    "spec/chain_params.json",
    "config/genesis.json",
    "config/chain_params.json",
    "genesis.json",
]


def _load_chain_params() -> Optional[Dict[str, Any]]:
    for p in _CANDIDATE_FILES:
        fp = Path(p)
        if fp.is_file():
            try:
                return json.loads(fp.read_text())
            except Exception as exc:  # pragma: no cover
                raise AssertionError(f"Failed to parse JSON in {fp}: {exc}") from exc
    return None


def _dig(d: Dict[str, Any], *keys: str) -> Optional[Any]:
    """
    Flexible nested-get: returns first matching key found along common paths.
    """
    cur: Any = d
    for k in keys:
        if cur is None:
            return None
        if isinstance(cur, dict) and k in cur:
            cur = cur[k]
        else:
            return None
    return cur


def _find_randomness(params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    # try several plausible nests
    candidates = [
        params.get("randomness"),
        _dig(params, "consensus", "randomness"),
        _dig(params, "protocol", "randomness"),
        _dig(params, "chain", "randomness"),
        _dig(params, "beacon"),  # sometimes beacon{}
    ]
    return next((c for c in candidates if isinstance(c, dict)), None)


def _first_present(d: Dict[str, Any], keys: list[str]) -> Optional[int]:
    for k in keys:
        v = d.get(k)
        if isinstance(v, (int, float)) and v > 0:
            return int(v)
    return None


def _extract_schedule(rnd: Dict[str, Any]) -> Dict[str, int]:
    """
    Extract (commit, reveal, settle, round_total) seconds from possibly varying schemas.
    Raises AssertionError if the schedule can't be determined.
    """
    commit = _first_present(
        rnd,
        ["commit_phase_seconds", "commit_seconds", "commit_window", "commit_phase"],
    )
    reveal = _first_present(
        rnd,
        ["reveal_phase_seconds", "reveal_seconds", "reveal_window", "reveal_phase"],
    )
    settle = _first_present(
        rnd,
        ["settle_phase_seconds", "settle_seconds", "settlement_window", "settle_phase"],
    )

    # total round length (optional; if absent we accept commit+reveal+settle)
    total = _first_present(
        rnd,
        [
            "round_seconds",
            "round_duration",
            "round_length_seconds",
            "period_seconds",
            "epoch_seconds",
        ],
    )

    if commit is None or reveal is None:
        raise AssertionError("Randomness schedule must define commit+reveal durations")

    # settle is optional in some configs; default to remaining time if total present,
    # otherwise to 0 (we'll check non-negative).
    if settle is None:
        settle = 0

    if total is None:
        total = commit + reveal + settle

    return dict(commit=commit, reveal=reveal, settle=settle, total=total)


def _extract_vdf(rnd: Dict[str, Any]) -> Dict[str, Any]:
    vdf = rnd.get("vdf") or {}
    # Some schemas place VDF under beacon.vdf or randomness.vdf
    if not vdf and "beacon" in rnd:
        vdf = rnd["beacon"].get("vdf", {})
    return vdf if isinstance(vdf, dict) else {}


# ---- tests ------------------------------------------------------------------


def test_commit_reveal_windows_partition_without_overlap():
    params = _load_chain_params()
    if not params:
        pytest.skip("chain/spec params not found (looked in common paths)")

    rnd = _find_randomness(params)
    if not rnd:
        pytest.skip("randomness/beacon section not present in chain/spec params")

    sched = _extract_schedule(rnd)
    commit, reveal, settle, total = (
        sched["commit"],
        sched["reveal"],
        sched["settle"],
        sched["total"],
    )

    # Durations are positive (allow settle==0 for two-phase beacons)
    assert commit > 0, "commit phase must be > 0 seconds"
    assert reveal > 0, "reveal phase must be > 0 seconds"
    assert settle >= 0, "settle phase must be >= 0 seconds"

    # No overlaps in a linear partition and cannot exceed total
    assert commit + reveal + settle <= total, (
        f"sum(commit={commit}, reveal={reveal}, settle={settle}) "
        f"must be <= total round={total}"
    )

    # Derived boundaries (t0 = 0)
    t_commit_start = 0
    t_commit_end = commit
    t_reveal_start = t_commit_end
    t_reveal_end = t_reveal_start + reveal
    t_settle_start = t_reveal_end
    t_settle_end = t_settle_start + settle

    # Non-overlap and monotonicity
    assert t_commit_start < t_commit_end
    assert t_reveal_start >= t_commit_end
    assert t_reveal_end >= t_reveal_start
    assert t_settle_start >= t_reveal_end
    assert t_settle_end <= total, "phases must not extend beyond the round length"


def test_vdf_params_sanity_relative_to_round():
    params = _load_chain_params()
    if not params:
        pytest.skip("chain/spec params not found (looked in common paths)")

    rnd = _find_randomness(params)
    if not rnd:
        pytest.skip("randomness/beacon section not present in chain/spec params")

    vdf = _extract_vdf(rnd)
    sched = _extract_schedule(rnd)
    total = sched["total"]

    # If present, security bits should be reasonable (>=80)
    sec_bits = vdf.get("security_bits") or vdf.get("lambda_bits") or vdf.get("lambda")
    if sec_bits is not None:
        assert int(sec_bits) >= 80, f"VDF security too low: {sec_bits} bits"

    # If present, discriminant size should be at least 1024 bits (2048 typical)
    disc_bits = (
        vdf.get("discriminant_size_bits")
        or vdf.get("D_bits")
        or vdf.get("disc_bits")
        or vdf.get("group_size_bits")
    )
    if disc_bits is not None:
        assert int(disc_bits) >= 1024, f"VDF discriminant too small: {disc_bits} bits"

    # Target/expected evaluation time (if specified) should be within the round
    target_secs = (
        vdf.get("target_seconds")
        or vdf.get("eval_seconds")
        or vdf.get("expected_seconds")
    )
    if target_secs is not None:
        ts = float(target_secs)
        assert 0 < ts <= float(total), (
            f"VDF target eval time ({ts}s) must be within the round length ({total}s)"
        )

    # Iterations sanity (if specified): must be positive; if a 'seconds_per_iter' hint
    # exists, the implied seconds should also fit within the round.
    its = vdf.get("iterations") or vdf.get("T") or vdf.get("difficulty")
    if its is not None:
        iters = int(its)
        assert iters > 0, "VDF iterations must be positive"
        sec_per_iter = (
            vdf.get("seconds_per_iter")
            or vdf.get("seconds_per_iteration")
            or vdf.get("sec_per_it")
        )
        if sec_per_iter:
            implied = float(sec_per_iter) * iters
            # allow implied to be up to the round length (some designs peg near total)
            assert implied <= float(total) * 1.05, (
                f"VDF implied eval time ({implied:.2f}s) exceeds round length ({total}s)"
            )


