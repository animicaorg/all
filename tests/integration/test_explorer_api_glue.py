# -*- coding: utf-8 -*-
"""
Integration: explorer-web API glue — endpoints return sane aggregates.

This test is deliberately flexible: explorer-web is often fronted by a tiny
"explorer-api" or the web app exposes a JSON summary proxy. We try several
well-known paths and validate shapes/aggregates if present. If no compatible
endpoint is reachable, the test SKIPS gracefully.

Environment
-----------
• RUN_INTEGRATION_TESTS=1                — enable integration tests
• ANIMICA_EXPLORER_API_URL or EXPLORER_API_URL
                                         — base URL for explorer API (RECOMMENDED)
• ANIMICA_HTTP_TIMEOUT                   — per-call timeout seconds (default: 5)
• ANIMICA_RESULT_WAIT_SECS               — overall wait/poll window (default: 120)
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import pytest

from tests.integration import env  # gating helper

# --------------------------------- helpers -----------------------------------


def _timeout() -> float:
    try:
        return float(env("ANIMICA_HTTP_TIMEOUT", "5"))
    except Exception:
        return 5.0


def _api_base() -> Optional[str]:
    return env("ANIMICA_EXPLORER_API_URL") or env("EXPLORER_API_URL")


def _join(base: str, path: str) -> str:
    return urllib.parse.urljoin(base.rstrip("/") + "/", path.lstrip("/"))


def _get_json(url: str) -> Optional[Any]:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=_timeout()) as resp:
            raw = resp.read()
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return None


def _try_paths(base: str, paths: Sequence[str]) -> Tuple[Optional[str], Optional[Any]]:
    last_err = None
    for p in paths:
        u = _join(base, p)
        try:
            data = _get_json(u)
            if data is not None:
                return p, data
        except Exception as exc:
            last_err = exc
            continue
    return None, None


def _is_hex(s: Any) -> bool:
    if not isinstance(s, str) or not s.startswith("0x") or len(s) < 3:
        return False
    try:
        int(s[2:], 16)
        return True
    except Exception:
        return False


def _as_int(x: Any) -> Optional[int]:
    if isinstance(x, bool) or x is None:
        return None
    if isinstance(x, int):
        return x
    if isinstance(x, float):
        return int(x)
    if isinstance(x, str):
        try:
            return int(x, 16) if x.startswith("0x") else int(x)
        except Exception:
            return None
    return None


# ----------------------------------- test ------------------------------------


@pytest.mark.timeout(600)
def test_explorer_api_endpoints_return_sane_aggregates():
    base = _api_base()
    if not base:
        pytest.skip(
            "Set ANIMICA_EXPLORER_API_URL (or EXPLORER_API_URL) to run explorer-web API checks."
        )

    # 1) Summary endpoint — head & basic counters
    sum_path, summary = _try_paths(
        base,
        [
            "/summary",
            "/api/summary",
            "/v1/summary",
            "/explorer/summary",
        ],
    )
    if not summary:
        pytest.skip("Explorer API summary not found at common paths.")

    # Accept a variety of summary shapes.
    # Common keys: chainId, headHeight, headHash, txCount, blockCount, tps, etc.
    chain_id = summary.get("chainId") or summary.get("chain_id") or summary.get("chain")
    head_h = (
        summary.get("headHeight") or summary.get("height") or summary.get("head_height")
    )
    head_hash = (
        summary.get("headHash") or summary.get("hash") or summary.get("head_hash")
    )

    # Minimal sanity: we should get at least height as an integer >= 0
    hh_i = _as_int(head_h)
    assert (
        hh_i is not None and hh_i >= 0
    ), f"Invalid head height in {sum_path}: {head_h!r}"

    if chain_id is not None:
        cid_i = _as_int(chain_id)
        assert (
            cid_i is not None and cid_i >= 0
        ), f"Invalid chainId in {sum_path}: {chain_id!r}"

    if head_hash is not None:
        assert _is_hex(
            head_hash
        ), f"headHash is not hex string in {sum_path}: {head_hash!r}"

    # Optional counters (if present) should be non-negative.
    for k in ("txCount", "txs", "blockCount", "blocks", "pendingTxs", "pending"):
        v = summary.get(k)
        if v is not None:
            vi = _as_int(v)
            assert (
                vi is not None and vi >= 0
            ), f"Counter {k} invalid in {sum_path}: {v!r}"

    # 2) Blocks list — most recent N and monotonic heights
    blk_path, blks = _try_paths(
        base,
        [
            "/blocks?limit=5",
            "/api/blocks?limit=5",
            "/v1/blocks?limit=5",
            "/explorer/blocks?limit=5",
        ],
    )
    if blks:
        # Could be {"items":[...]} or a list
        items = blks.get("items") if isinstance(blks, dict) else blks
        if isinstance(items, list) and items:
            heights = []
            for b in items:
                if isinstance(b, dict):
                    h = _as_int(b.get("height") or b.get("number") or b.get("h"))
                    if h is not None:
                        heights.append(h)
                    # Basic hash sanity if present
                    hsh = b.get("hash") or b.get("blockHash")
                    if hsh is not None:
                        assert _is_hex(
                            hsh
                        ), f"Block hash not hex in {blk_path}: {hsh!r}"
            # Heights should be monotonic (typically descending)
            if len(heights) >= 2:
                assert all(
                    heights[i] >= heights[i + 1] for i in range(len(heights) - 1)
                ), f"Heights not monotonic (desc) in {blk_path}: {heights}"

    # 3) PoIES / consensus aggregates — Γ / acceptance / sharesByType (if present)
    po_path, po = _try_paths(
        base,
        [
            "/poies",
            "/poies/aggregate",
            "/api/poies",
            "/v1/poies/summary",
            "/consensus/summary",
            "/stats/poies",
        ],
    )
    if po:
        # Look for acceptance/fairness metrics.
        acc = po.get("acceptRate") or po.get("acceptance") or po.get("accept_rate")
        if acc is not None:
            a = (
                float(acc)
                if isinstance(acc, (int, float))
                else float(_as_int(acc) or 0)
            )
            assert 0.0 <= a <= 1.0, f"Accept rate out of range in {po_path}: {acc!r}"
        gamma = (
            po.get("Gamma")
            or po.get("gamma")
            or po.get("totalGamma")
            or po.get("total_gamma")
        )
        if gamma is not None:
            g = _as_int(gamma)
            assert g is not None and g >= 0, f"Γ invalid in {po_path}: {gamma!r}"
        shares = po.get("sharesByType") or po.get("byType") or po.get("types")
        if isinstance(shares, dict):
            for typ, val in shares.items():
                vi = _as_int(val)
                assert (
                    vi is not None and vi >= 0
                ), f"sharesByType[{typ}] invalid in {po_path}: {val!r}"

    # 4) AICF aggregates — providers/jobs/throughput (if present)
    aicf_path, aicf = _try_paths(
        base,
        [
            "/aicf",
            "/aicf/metrics",
            "/api/aicf/summary",
            "/v1/aicf/summary",
            "/stats/aicf",
        ],
    )
    if aicf:
        for k in (
            "providersOnline",
            "providers",
            "jobsQueued",
            "jobsPending",
            "jobsCompleted",
        ):
            v = aicf.get(k)
            if v is not None:
                vi = _as_int(v)
                assert (
                    vi is not None and vi >= 0
                ), f"AICF metric {k} invalid in {aicf_path}: {v!r}"

    # 5) DA aggregates — blobs/commitments/availability (if present)
    da_path, da = _try_paths(
        base,
        [
            "/da",
            "/da/summary",
            "/api/da/summary",
            "/v1/da/summary",
            "/stats/da",
        ],
    )
    if da:
        for k in (
            "blobs",
            "commitments",
            "proofsServed",
            "proofs",
            "samplePFail",
            "p_fail",
        ):
            v = da.get(k)
            if v is None:
                continue
            if "fail" in k.lower():
                # probability-like metric
                try:
                    f = float(v) if not isinstance(v, str) else float(v)
                    assert (
                        0.0 <= f <= 1.0
                    ), f"DA metric {k} out of [0,1] in {da_path}: {v!r}"
                except Exception:
                    pytest.fail(f"DA metric {k} not a number in {da_path}: {v!r}")
            else:
                vi = _as_int(v)
                assert (
                    vi is not None and vi >= 0
                ), f"DA metric {k} invalid in {da_path}: {v!r}"

    # 6) Randomness / beacon — round and beacon present if exposed
    rnd_path, rnd = _try_paths(
        base,
        [
            "/randomness",
            "/randomness/summary",
            "/api/randomness/summary",
            "/v1/randomness/summary",
            "/beacon/summary",
            "/rand/summary",
        ],
    )
    if rnd:
        r_id = rnd.get("roundId") or rnd.get("round") or rnd.get("id")
        if r_id is not None:
            ri = _as_int(r_id)
            assert (
                ri is not None and ri >= 0
            ), f"Beacon round invalid in {rnd_path}: {r_id!r}"
        out = rnd.get("beacon") or rnd.get("output") or rnd.get("mix")
        if out is not None:
            assert _is_hex(out), f"Beacon output not hex-like in {rnd_path}: {out!r}"

    # If we've reached here without assertion, the explorer API glue is sane enough.
