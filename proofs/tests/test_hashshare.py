from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import pytest

import proofs.hashshare as hmod
import proofs.cbor as pcbor


# --------------------------- helpers & adapters ---------------------------

def _load_vectors() -> Dict[str, Any]:
    path = Path(__file__).resolve().parents[1] / "test_vectors" / "hashshare.json"
    with path.open("rb") as f:
        return json.load(f)


def _hex_to_bytes(x: Any) -> Any:
    if isinstance(x, str) and x.startswith("0x"):
        return bytes.fromhex(x[2:])
    return x


def _normalize_hex_rec(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _normalize_hex_rec(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_normalize_hex_rec(v) for v in obj]
    return _hex_to_bytes(obj)


def _find_verify_fn():
    # Prefer explicit names
    for name in ("verify_hashshare", "verify", "validate_hashshare", "validate", "check"):
        fn = getattr(hmod, name, None)
        if callable(fn):
            return fn
    raise AssertionError("proofs.hashshare must expose a verify/validate function")


VERIFY = _find_verify_fn()


def _call_verify(body: Dict[str, Any], header: Optional[Dict[str, Any]]) -> Tuple[bool, Dict[str, Any]]:
    """
    Call VERIFY with flexible signatures:
      - VERIFY(body, header)
      - VERIFY(header, body)
      - VERIFY(envelope={'type_id':..., 'body':..., 'header':...})
      - VERIFY(body)     (if body embeds header_hash and module only checks link)
    Expect either:
      - bool
      - (bool, metrics:dict)
      - {'ok':bool, 'metrics':{...}} or {'accept':bool,...}
    Return: (ok, metrics)
    """
    # Try (body, header)
    try_orders = [
        (body, header),
        (header, body),
        ({"type_id": 0, "body": body, "header": header}, None),
        (body, None),
    ]
    last_exc: Optional[Exception] = None
    for a, b in try_orders:
        try:
            if b is None:
                res = VERIFY(a)  # type: ignore[arg-type]
            else:
                res = VERIFY(a, b)  # type: ignore[arg-type]
            return _normalize_result(res)
        except TypeError as e:
            last_exc = e
            continue
    raise AssertionError(f"Unable to invoke hashshare.verify with any supported signature: {last_exc}")


def _normalize_result(res: Any) -> Tuple[bool, Dict[str, Any]]:
    # bool only
    if isinstance(res, bool):
        return res, {}
    # tuple (bool, dict) in either order
    if isinstance(res, tuple) and len(res) == 2:
        a, b = res
        if isinstance(a, bool) and isinstance(b, dict):
            return a, b
        if isinstance(b, bool) and isinstance(a, dict):
            return b, a
    # dict payload
    if isinstance(res, dict):
        if "ok" in res or "accept" in res:
            ok = bool(res.get("ok", res.get("accept")))
            metrics = dict(res.get("metrics", {}))
            # merge top-level numeric fields into metrics for convenience
            for k, v in res.items():
                if k not in ("ok", "accept", "metrics") and isinstance(v, (int, float)):
                    metrics[k] = v
            return ok, metrics
    # unknown payload: try to CBOR-encode round-trip (should not happen in tests)
    return bool(res), {}


# Aliases for body/header fields across implementations
HEADER_HASH_KEYS = ("header_hash", "headerHash", "hdr_hash")
NONCE_KEYS = ("nonce", "ctr", "n")
TARGET_KEYS = ("share_target", "target", "shareTarget", "T_share")
MIXSEED_KEYS = ("mix_seed", "mixSeed", "seed")


def _get(d: Dict[str, Any], names) -> Any:
    for k in names:
        if k in d:
            return d[k]
    raise KeyError(f"Expected one of keys {names} in {list(d.keys())}")


def _set(d: Dict[str, Any], names, value: Any) -> None:
    for k in names:
        if k in d:
            d[k] = value
            return
    # Fallback: set the first canonical name if none found
    d[names[0]] = value


# ------------------------------- tests -----------------------------------


def test_vectors_accept_and_dratio_ranges():
    """Cross-check against module-local vectors; verify accept/reject and d_ratio bounds."""
    vec = _load_vectors()
    cases = vec.get("vectors") or vec.get("cases") or []
    assert cases, "hashshare.json must contain an array under 'vectors' or 'cases'"

    for i, case in enumerate(cases):
        body = _normalize_hex_rec(case.get("body", case.get("share", {})))
        header = _normalize_hex_rec(case.get("header", {})) or None
        expect = case.get("expect", {})
        ok, metrics = _call_verify(body, header)

        assert ok is bool(expect.get("accept", ok)), f"vector[{i}] expected accept={expect.get('accept')} got {ok}"

        # d_ratio checks: allow several expectation styles
        dr = metrics.get("d_ratio", metrics.get("dRatio", metrics.get("D_ratio")))
        if dr is not None:
            # sanity
            assert isinstance(dr, (int, float))
            assert dr >= 0, "d_ratio must be non-negative"
            if "d_ratio_min" in expect:
                assert dr >= float(expect["d_ratio_min"]) - 1e-12
            if "d_ratio_max" in expect:
                assert dr <= float(expect["d_ratio_max"]) + 1e-12
            if "d_ratio_approx" in expect:
                approx = float(expect["d_ratio_approx"])
                tol = float(expect.get("tol", 1e-9))
                assert abs(dr - approx) <= tol, f"vector[{i}] d_ratio {dr} not within {tol} of {approx}"


def test_header_binding_rejects_mismatch():
    """Change the header_hash and expect verification to fail."""
    vec = _load_vectors()
    case = next((c for c in (vec.get("vectors") or []) if c.get("expect", {}).get("accept") is True), None)
    if not case:
        pytest.skip("No accepting vector to run header-binding test")
    body = _normalize_hex_rec(case["body"])
    header = _normalize_hex_rec(case.get("header") or {})

    # If header is present and contains a header hash, ensure link; otherwise rely on body.header_hash
    if header:
        # Flip one bit of header hash (if present), else mutate a relevant header field used in binding
        try:
            hh = _get(header, HEADER_HASH_KEYS)
            if isinstance(hh, (bytes, bytearray)) and len(hh) >= 1:
                mutated = bytes([hh[0] ^ 0x01]) + bytes(hh[1:])
                _set(header, HEADER_HASH_KEYS, mutated)
        except KeyError:
            # As a fallback, tack on an extra byte to break binding if implementation hashes the whole header object
            header["__tamper__"] = 1
    else:
        # Mutate body.header_hash if that's what the verifier binds to
        hh = _get(body, HEADER_HASH_KEYS)
        if isinstance(hh, (bytes, bytearray)) and len(hh) >= 1:
            mutated = bytes([hh[0] ^ 0x01]) + bytes(hh[1:])
            _set(body, HEADER_HASH_KEYS, mutated)

    ok, _ = _call_verify(body, header or None)
    assert not ok, "hashshare must reject when header-link/binding is broken"


def test_target_monotonicity_and_nonce_sensitivity():
    """Increasing share_target should not decrease d_ratio; changing nonce should change outcome/distribution."""
    # Build a synthetic but consistent body, then probe monotonicity.
    # Prefer using first vector's body/header as a base for realism.
    vec = _load_vectors()
    base_case = (vec.get("vectors") or [])[0]
    body = _normalize_hex_rec(base_case.get("body", {})).copy()
    header = _normalize_hex_rec(base_case.get("header", {})) or None

    # Ensure required fields exist (fill simple defaults if vector is minimal)
    for keys, default in (
        (HEADER_HASH_KEYS, b"\x11" * 32),
        (NONCE_KEYS, 1),
        (TARGET_KEYS, 1_000_000),
    ):
        try:
            _get(body, keys)
        except KeyError:
            _set(body, keys, default)

    # Baseline
    ok1, m1 = _call_verify(body.copy(), header)
    dr1 = float(m1.get("d_ratio", m1.get("dRatio", 0.0)))

    # Double the share target; d_ratio should not go down (monotonicity in target)
    body2 = body.copy()
    _set(body2, TARGET_KEYS, int(_get(body, TARGET_KEYS)) * 2)
    ok2, m2 = _call_verify(body2, header)
    dr2 = float(m2.get("d_ratio", m2.get("dRatio", 0.0)))
    assert dr2 >= dr1 - 1e-12, f"d_ratio should be monotone non-decreasing with share_target (got {dr1} -> {dr2})"

    # Changing nonce must affect the draw; acceptance probability / d_ratio should usually differ.
    body3 = body.copy()
    _set(body3, NONCE_KEYS, int(_get(body, NONCE_KEYS)) + 1)
    ok3, m3 = _call_verify(body3, header)
    # Not strictly guaranteed to change, but over random space extremely likely; allow equality only if metrics absent.
    dr3 = float(m3.get("d_ratio", m3.get("dRatio", -1.0)))
    if dr1 >= 0 and dr3 >= 0:
        assert dr3 != dr1 or ok3 != ok1, "nonce change should alter the u-draw outcome (d_ratio or accept flag)"


def test_cbor_equivalence_of_body():
    """If verifier accepts CBOR-encoded body, dict vs CBOR must yield identical outcome."""
    vec = _load_vectors()
    case = (vec.get("vectors") or [])[0]
    body = _normalize_hex_rec(case.get("body", {}))
    header = _normalize_hex_rec(case.get("header", {})) or None

    ok_dict, m_dict = _call_verify(body, header)

    try:
        cbor_body = pcbor.encode(body)
        ok_cbor, m_cbor = _call_verify(cbor_body, header)  # type: ignore[arg-type]
    except Exception:
        pytest.skip("Verifier does not support CBOR body input")
    else:
        assert ok_dict == ok_cbor
        # Compare common metric keys if present
        for k in ("d_ratio", "dRatio"):
            if k in m_dict or k in m_cbor:
                assert pytest.approx(float(m_dict.get(k, 0.0)), rel=1e-12, abs=1e-12) == float(m_cbor.get(k, 0.0))
