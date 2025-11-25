"""
Minimal light verification adapter.

This bridges studio-services to the Python SDK's light-client verifier
(`omni_sdk.light_client.verify`). It is intentionally defensive about
the exact function names to tolerate small API drifts between SDK versions.

Exports:
- `LightVerifyError`
- `VerifyResult` dataclass
- `verify_header_da(header, da_light_proof=None, trusted=None) -> VerifyResult`
- `extract_da_root(header) -> str | None`

Where:
- `header` is a dict shaped like core/types/header.py public view (as returned
  by RPC methods such as `chain.getBlockByNumber(..., includeReceipts=False)`).
- `da_light_proof` is an optional dict carrying a DA light-proof (e.g., DAS
  samples + branches).
- `trusted` can carry trust anchors, such as:
    {"genesis_hash": "0x...", "chain_id": 1}

Result is a structured `VerifyResult` with `ok: bool`, optional `reason`,
and `details` (implementation-defined diagnostic data).
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Callable, Dict, Optional

try:
    # Preferred import path per SDK layout you provided
    from omni_sdk.light_client import verify as lc_verify  # type: ignore
except Exception:  # pragma: no cover - handled gracefully below
    lc_verify = None  # type: ignore


# ----------------------------- Types & Errors --------------------------------


class LightVerifyError(Exception):
    """Raised when verification cannot be performed (missing SDK or fatal error)."""


@dataclass
class VerifyResult:
    ok: bool
    reason: Optional[str] = None
    details: Dict[str, Any] | None = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ----------------------------- Helpers ---------------------------------------


def extract_da_root(header: Dict[str, Any]) -> Optional[str]:
    """
    Attempt to retrieve the DA root commitment from a header dict.
    Supports a couple of plausible shapes:
      - header["daRoot"]
      - header["roots"]["da"]
    Returns a 0x-prefixed hex string if present, otherwise None.
    """
    if not isinstance(header, dict):
        return None
    if "daRoot" in header and isinstance(header["daRoot"], str):
        return header["daRoot"]
    roots = header.get("roots")
    if isinstance(roots, dict) and isinstance(roots.get("da"), str):
        return roots["da"]
    return None


def _sdk_available() -> bool:
    return lc_verify is not None


def _call_best_effort_verify(
    header: Dict[str, Any],
    da_light_proof: Optional[Dict[str, Any]],
    trusted: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Try multiple well-known call signatures to the SDK light verifier and return
    a details dict. Raises on hard failure.
    """
    if lc_verify is None:
        raise LightVerifyError(
            "omni_sdk.light_client.verify is not available. Ensure omni-sdk (Python) is installed."
        )

    # Try canonical function names in a forgiving order.
    # We return a normalized details dict with at least {'ok': bool}.
    verifier_funcs: list[Callable[..., Any]] = []
    # 1) Module exposes a 'verify' callable (preferred)
    if hasattr(lc_verify, "verify"):
        verifier_funcs.append(getattr(lc_verify, "verify"))
    # 2) Legacy names
    for name in ("verify_header_and_da", "verify_light", "verify_light_client"):
        if hasattr(lc_verify, name):
            verifier_funcs.append(getattr(lc_verify, name))

    last_err: Exception | None = None
    for func in verifier_funcs:
        try:
            # Try named args first
            try:
                result = func(header=header, da_light_proof=da_light_proof, trusted=trusted)
            except TypeError:
                # Fall back to positional variants
                if da_light_proof is not None and trusted is not None:
                    result = func(header, da_light_proof, trusted)
                elif da_light_proof is not None:
                    result = func(header, da_light_proof)
                elif trusted is not None:
                    result = func(header, trusted)
                else:
                    result = func(header)

            # Normalize result into a dict
            if isinstance(result, dict):
                # Expect at least an 'ok' flag in the dict
                if "ok" not in result:
                    result = {"ok": bool(result), "raw": result}
                return result
            if isinstance(result, (bool,)):
                return {"ok": bool(result)}
            # Tuple forms like (ok, details)
            if isinstance(result, tuple) and result:
                ok = bool(result[0])
                details = {"extra": result[1:]} if len(result) > 1 else {}
                details["ok"] = ok
                return details

            # Unknown shape: wrap as raw
            return {"ok": bool(result), "raw": result}
        except Exception as e:  # try next signature/name
            last_err = e
            continue

    # If we got here, all attempts failed
    raise LightVerifyError(f"SDK light verification call failed: {last_err!r}")


# ----------------------------- Public API ------------------------------------


def verify_header_da(
    header: Dict[str, Any],
    da_light_proof: Optional[Dict[str, Any]] = None,
    *,
    trusted: Optional[Dict[str, Any]] = None,
) -> VerifyResult:
    """
    Verify a header (and optionally a DA light proof) using the omni-sdk verifier.

    Parameters
    ----------
    header : dict
        Header view as returned by the node RPC (JSON-serializable).
    da_light_proof : dict | None
        Optional DA proof object (e.g., DAS samples/branches) to be verified
        against the header's DA root.
    trusted : dict | None
        Optional trust anchors (e.g., {"genesis_hash": "0x..", "chain_id": 1}).

    Returns
    -------
    VerifyResult
        ok=True on success; details include SDK-provided diagnostics.
    """
    if not _sdk_available():
        raise LightVerifyError(
            "omni-sdk (Python) is not installed or cannot be imported; cannot perform light verification."
        )

    try:
        details = _call_best_effort_verify(header, da_light_proof, trusted)
        ok = bool(details.get("ok", False))
        reason = None if ok else (details.get("reason") or details.get("error") or "verification failed")
        return VerifyResult(ok=ok, reason=reason, details=details)
    except LightVerifyError:
        raise
    except Exception as e:  # pragma: no cover - defensive
        raise LightVerifyError(f"Verification threw an exception: {e!r}") from e


__all__ = [
    "LightVerifyError",
    "VerifyResult",
    "verify_header_da",
    "extract_da_root",
]
