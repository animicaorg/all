from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

import pytest

from proofs.errors import AttestationError

# Optional imports are wrapped so tests still run if a given backend is not present.
sgx = None
sev = None
cca = None
try:
    from proofs.attestations.tee import sgx as _sgx

    sgx = _sgx
except Exception:
    pass

try:
    from proofs.attestations.tee import sev_snp as _sev

    sev = _sev
except Exception:
    pass

try:
    from proofs.attestations.tee import cca as _cca

    cca = _cca
except Exception:
    pass


ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "fixtures"
VENDOR = ROOT / "attestations" / "vendor_roots"

# -----------------------------------------------------------------------------
# Generic verifier invoker (tolerates different APIs across modules)
# -----------------------------------------------------------------------------

Verifier = Callable[..., Any]


def _find_verifier(mod: Any) -> Verifier:
    """
    Try common verifier names in a module and return a callable.
    The callable may accept (evidence_bytes, roots_dir=PathLike, **kw).
    """
    if mod is None:
        raise RuntimeError("Module not available")

    candidates = [
        "verify",
        "validate",
        "verify_quote",
        "verify_report",
        "verify_token",
        "parse_and_verify",
        "check",
    ]
    for name in candidates:
        fn = getattr(mod, name, None)
        if callable(fn):
            return fn  # type: ignore[return-value]
    # Fall back to a pure parse, for structure-only checks
    for name in ("parse", "decode", "parse_quote", "parse_report", "parse_token"):
        fn = getattr(mod, name, None)
        if callable(fn):
            return fn  # type: ignore[return-value]
    raise RuntimeError(f"No verifier/parse function found in module {mod.__name__}")


def _call_verifier(
    vf: Verifier, evidence: bytes, roots_dir: Optional[Path] = None, **kw
) -> Tuple[bool, Dict[str, Any]]:
    """
    Attempt to call the verifier with a few signatures. Normalize result to (ok, details).
    Accepts either:
      - bool
      - (bool, dict)
      - {'ok': bool, 'accept': bool, 'details': {...}, 'metrics': {...}}
      - parsed dict (treated as ok=True for parse-only functions)
    """
    # Try with roots_dir first if provided
    tried: list[Tuple[tuple, dict]] = []
    for args, kwargs in (
        ((evidence,), {"roots_dir": roots_dir, **kw}),
        ((evidence, roots_dir), kw),
        ((evidence,), kw),
    ):
        try:
            res = vf(*args, **kwargs)
            return _normalize_result(res)
        except TypeError:
            tried.append((args, kwargs))
            continue
    # As a last resort, try parse-only
    res = vf(evidence)  # type: ignore[misc]
    ok, det = _normalize_result(res)
    if not ok and not det:
        # Treat parse-only return (dict) as OK if it yielded structure
        if isinstance(res, dict):
            return True, {"parsed": True}
    return ok, det


def _normalize_result(res: Any) -> Tuple[bool, Dict[str, Any]]:
    if isinstance(res, bool):
        return res, {}
    if isinstance(res, tuple) and len(res) == 2:
        a, b = res
        if isinstance(a, bool) and isinstance(b, dict):
            return a, b
        if isinstance(b, bool) and isinstance(a, dict):
            return b, a
    if isinstance(res, dict):
        # Support a variety of shapes
        ok = res.get("ok")
        if ok is None and "accept" in res:
            ok = res.get("accept")
        if isinstance(ok, bool):
            det = dict(res)
            det.pop("ok", None)
            det.pop("accept", None)
            return ok, det
        # If this looks like a parsed structure, consider ok=True (parse-only mode)
        if res:
            return True, {"parsed": True, **res}
    # Unknown payload type → coerce
    return bool(res), {}


def _mutate(b: bytes, offset: int = 0) -> bytes:
    if not b:
        return b
    idx = min(max(offset, 0), len(b) - 1)
    return b[:idx] + bytes([b[idx] ^ 0x01]) + b[idx + 1 :]


# -----------------------------------------------------------------------------
# SGX
# -----------------------------------------------------------------------------


@pytest.mark.skipif(sgx is None, reason="SGX attestation module not available")
def test_sgx_quote_parse_and_verify_or_parse_only():
    quote_path = FIX / "sgx_quote.bin"
    assert quote_path.exists(), "Missing fixture sgx_quote.bin"
    quote = quote_path.read_bytes()

    vf = _find_verifier(sgx)
    ok, det = _call_verifier(
        vf,
        quote,
        roots_dir=VENDOR,  # may be placeholder roots; verification may legitimately fail
        allow_untrusted=True,  # many implementations support a parse-only/lenient flag
    )

    # We accept either a real verification (ok=True) or a parse-only success (parsed structure present).
    assert ok or det.get("parsed") is True
    # Should surface at least one identity-ish field in details if parsed
    if det:
        found_identity = any(
            k in det for k in ("mrenclave", "mrsigner", "tcb", "tee", "qe", "quote")
        )
        assert (
            found_identity
        ), f"Expected identity-ish fields in details, got keys={list(det.keys())[:6]}"

    # Corrupt the quote → verifier must fail or raise
    bad = _mutate(quote, 5)
    with pytest.raises(AttestationError):
        _call_verifier(vf, bad, roots_dir=VENDOR, allow_untrusted=False)


@pytest.mark.skipif(sgx is None, reason="SGX attestation module not available")
def test_sgx_rejects_truncated_input():
    quote = (FIX / "sgx_quote.bin").read_bytes()
    truncated = quote[: max(0, len(quote) // 3)]
    vf = _find_verifier(sgx)
    with pytest.raises(AttestationError):
        _call_verifier(vf, truncated, roots_dir=VENDOR)


# -----------------------------------------------------------------------------
# SEV-SNP
# -----------------------------------------------------------------------------


@pytest.mark.skipif(sev is None, reason="SEV-SNP attestation module not available")
def test_sev_snp_report_parse_and_verify_or_parse_only():
    rpt_path = FIX / "sev_snp_report.bin"
    assert rpt_path.exists(), "Missing fixture sev_snp_report.bin"
    rpt = rpt_path.read_bytes()

    vf = _find_verifier(sev)
    ok, det = _call_verifier(vf, rpt, roots_dir=VENDOR, allow_untrusted=True)
    assert ok or det.get("parsed") is True
    if det:
        found_identity = any(
            k in det
            for k in ("chip_id", "family_id", "report", "policy", "tcb_version")
        )
        assert (
            found_identity
        ), f"Expected SEV/SNP fields, got keys={list(det.keys())[:6]}"

    # Corrupt report → must fail or raise
    with pytest.raises(AttestationError):
        _call_verifier(vf, _mutate(rpt, 9), roots_dir=VENDOR, allow_untrusted=False)


@pytest.mark.skipif(sev is None, reason="SEV-SNP attestation module not available")
def test_sev_rejects_empty_or_short():
    vf = _find_verifier(sev)
    with pytest.raises(AttestationError):
        _call_verifier(vf, b"", roots_dir=VENDOR)


# -----------------------------------------------------------------------------
# Arm CCA realm token (CBOR/COSE)
# -----------------------------------------------------------------------------


@pytest.mark.skipif(cca is None, reason="CCA attestation module not available")
def test_cca_token_parse_and_verify_or_parse_only():
    tok_path = FIX / "cca_token.cbor"
    assert tok_path.exists(), "Missing fixture cca_token.cbor"
    token = tok_path.read_bytes()

    vf = _find_verifier(cca)
    ok, det = _call_verifier(vf, token, roots_dir=VENDOR, allow_untrusted=True)
    assert ok or det.get("parsed") is True
    if det:
        # Expect at least a few well-known claims
        expect_any = (
            "platform-hash",
            "realm-hash",
            "realm-svn",
            "nonce",
            "profile",
            "claims",
            "cose",
        )
        assert any(
            k in det for k in expect_any
        ), f"Expected CCA claims; got keys={list(det.keys())[:6]}"

    # Tamper with payload → fail or raise
    with pytest.raises(AttestationError):
        _call_verifier(vf, _mutate(token, 3), roots_dir=VENDOR, allow_untrusted=False)


@pytest.mark.skipif(cca is None, reason="CCA attestation module not available")
def test_cca_rejects_garbage():
    vf = _find_verifier(cca)
    with pytest.raises(AttestationError):
        _call_verifier(
            vf, b"\xd8\x50\xa1\x01", roots_dir=VENDOR
        )  # malformed tiny CBOR/COSE


# -----------------------------------------------------------------------------
# Cross-backend sanity: consistent error signaling on clearly wrong input
# -----------------------------------------------------------------------------


@pytest.mark.parametrize("backend_name", ["sgx", "sev", "cca"])
def test_backends_raise_on_non_bytes(backend_name: str):
    mod = {"sgx": sgx, "sev": sev, "cca": cca}[backend_name]
    if mod is None:
        pytest.skip(f"{backend_name} module not available")
    vf = _find_verifier(mod)
    with pytest.raises((AttestationError, TypeError, ValueError)):
        _call_verifier(vf, "not-bytes", roots_dir=VENDOR)  # type: ignore[arg-type]
