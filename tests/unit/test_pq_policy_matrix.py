# SPDX-License-Identifier: Apache-2.0
"""
PQ algorithm policy matrix — cross-package consistency & stable root.

This test checks that all places that *define or imply* the set of supported
post-quantum signature algorithms agree with each other, and that the
canonical "policy root" derived from that set is stable.

Sources compared:
- omni_sdk.wallet.signer: advertised PQ signer algorithms
- studio_services.adapters.pq_addr: address-kind / bech32 rules for PQ algs
- Optional: EXPECTED root exposed by pq_addr (or env PQ_POLICY_ROOT)

The policy root is SHA3-256 over a canonical JSON array of entries:
  [{"alg": "<name>", "addr_kind": "<kind-or-null>"}] sorted by "alg".
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from hashlib import sha3_256
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import pytest

# -----------------------------------------------------------------------------
# Helpers to fetch algorithms from omni_sdk.wallet.signer
# -----------------------------------------------------------------------------


def _collect_algorithms_from_signer() -> Set[str]:
    """
    Try several known shapes to get the set of supported PQ algorithms from
    omni_sdk.wallet.signer. This stays tolerant to minor refactors.
    """
    try:
        from omni_sdk.wallet import signer as _signer  # type: ignore
    except Exception as exc:  # pragma: no cover - if package not installed
        pytest.skip(f"omni_sdk not importable: {exc}", allow_module_level=True)
        raise

    # 1) Common constant names
    for name in (
        "SUPPORTED_PQ_ALGS",
        "PQ_ALGS",
        "AVAILABLE_PQ_ALGOS",
        "AVAILABLE_PQ_ALGS",
    ):
        algs = getattr(_signer, name, None)
        if isinstance(algs, (set, list, tuple)):
            return set(str(a) for a in algs)

    # 2) Registry-style mapping: ALG -> class/factory
    for name in ("REGISTRY", "PQ_REGISTRY", "SIGNER_REGISTRY"):
        reg = getattr(_signer, name, None)
        if isinstance(reg, dict) and reg:
            # keys may be names; otherwise capture classes with .ALG attribute
            keys = set()
            for k, v in reg.items():
                if isinstance(k, str):
                    keys.add(k)
                else:
                    # fallback: read ALG attr from value
                    alg = getattr(v, "ALG", None)
                    if isinstance(alg, str):
                        keys.add(alg)
            if keys:
                return keys

    # 3) Introspect module for classes carrying ALG = "name"
    keys = set()
    for obj in _signer.__dict__.values():
        try:
            alg = getattr(obj, "ALG", None)
            if isinstance(alg, str):
                keys.add(alg)
        except Exception:
            pass
    if keys:
        return keys

    pytest.fail("Could not determine PQ algorithms from omni_sdk.wallet.signer")


# -----------------------------------------------------------------------------
# Helpers to fetch address-kind mapping from studio_services.adapters.pq_addr
# -----------------------------------------------------------------------------


def _load_addr_kinds() -> Dict[str, Optional[str]]:
    """
    Return mapping {alg_name -> addr_kind_or_None}. If an adapter doesn't
    provide kinds per alg, map to None but ensure names match the signer set.
    """
    try:
        from studio_services.adapters import pq_addr as _pq  # type: ignore
    except Exception as exc:
        # Studio services are optional in some environments; we still want
        # to verify at least the signer list exists.
        return {}

    # Try common shapes
    for name in (
        "ALG_TO_ADDR_KIND",
        "PQ_ALG_TO_KIND",
        "ADDR_KIND_FOR_ALG",
    ):
        m = getattr(_pq, name, None)
        if isinstance(m, dict):
            # coerce keys/values to str
            out = {}
            for k, v in m.items():
                k2 = str(k)
                v2 = None if v is None else str(v)
                out[k2] = v2
            return out

    # Maybe there's a table of tuples/list of records
    tab = getattr(_pq, "POLICY_TABLE", None)
    if isinstance(tab, (list, tuple)) and tab:
        out = {}
        for row in tab:
            if isinstance(row, dict):
                k = str(row.get("alg"))
                out[k] = (
                    None if row.get("addr_kind") is None else str(row.get("addr_kind"))
                )
            elif isinstance(row, (list, tuple)) and len(row) >= 2:
                k = str(row[0])
                out[k] = None if row[1] is None else str(row[1])
        if out:
            return out

    # If adapter has no explicit mapping, return empty (treated as unknown kinds)
    return {}


# -----------------------------------------------------------------------------
# Canonical root calculation
# -----------------------------------------------------------------------------


def _canonical_bytes(entries: List[Dict[str, Optional[str]]]) -> bytes:
    """
    Deterministic, minimal JSON encoding with sorted keys and array sorted by "alg".
    """
    entries2 = sorted(
        [{"alg": e.get("alg"), "addr_kind": e.get("addr_kind")} for e in entries],
        key=lambda x: (x["alg"] or ""),
    )
    return json.dumps(entries2, sort_keys=True, separators=(",", ":")).encode("utf-8")


def compute_policy_root(algos: Iterable[str], kinds: Dict[str, Optional[str]]) -> str:
    """
    domain-separated SHA3-256 over canonical entries.
    """
    entries = [
        {"alg": a, "addr_kind": kinds.get(a)} for a in sorted(set(map(str, algos)))
    ]
    payload = b"pq-policy-v1|" + _canonical_bytes(entries)
    return sha3_256(payload).hexdigest()


# -----------------------------------------------------------------------------
# Tests
# -----------------------------------------------------------------------------


def test_policy_sets_are_consistent_and_root_matches_expected(
    monkeypatch: pytest.MonkeyPatch,
):
    signer_algs = _collect_algorithms_from_signer()
    addr_kinds = _load_addr_kinds()

    # If adapter exists and exposes alg list, it must match signer set.
    if addr_kinds:
        adapter_algs = set(addr_kinds.keys())
        assert (
            adapter_algs == signer_algs
        ), f"PQ alg mismatch between signer {sorted(signer_algs)} and pq_addr {sorted(adapter_algs)}"

    # Compute canonical root
    root = compute_policy_root(signer_algs, addr_kinds)

    # Allow expected root to come from adapter constant or env var
    expected: Optional[str] = None
    try:
        from studio_services.adapters import pq_addr as _pq  # type: ignore

        for name in ("EXPECTED_POLICY_ROOT", "POLICY_ROOT", "PQ_POLICY_ROOT"):
            val = getattr(_pq, name, None)
            if isinstance(val, str) and len(val) >= 16:
                expected = val.lower()
                break
    except Exception:
        pass

    env_expected = os.getenv("PQ_POLICY_ROOT")
    if not expected and env_expected:
        expected = env_expected.lower()

    # If an expected root is provided anywhere, enforce match.
    if expected:
        assert (
            root == expected
        ), f"PQ policy root mismatch: computed {root}, expected {expected}"

    # Always assert root shape (hex-64)
    assert len(root) == 64 and all(
        c in "0123456789abcdef" for c in root
    ), "root must be hex-64"


def test_policy_root_is_order_independent():
    # Build two differently ordered views and ensure root is stable.
    signer_algs = _collect_algorithms_from_signer()
    addr_kinds = _load_addr_kinds()

    root1 = compute_policy_root(sorted(signer_algs), addr_kinds)
    root2 = compute_policy_root(sorted(signer_algs, reverse=True), addr_kinds)
    assert root1 == root2
