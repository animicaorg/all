"""
Animica • proofs.quantum_attest

Convenience utilities and stable entrypoints for Quantum provider attestation:
- Locates the vendor roots directory and JWKS cache populated by
  `proofs/attestations/vendor_roots/install_official_qpu_roots.sh`.
- Loads JWKS (JSON Web Key Sets) for known providers (IBM, Azure, Google) and any extras.
- Exposes small helpers to enumerate keys and pick the right public key by KID/algo from a token header.
- Provides a light wrapper to refresh the local JWKS cache.

The heavy-duty parsing/verification lives in:
  - proofs/quantum_attest/provider_cert.py   (identity / cert / JOSE verification)
  - proofs/quantum_attest/traps.py           (trap-circuit math)
  - proofs/quantum_attest/benchmarks.py      (units/throughput reference)

This module deliberately has *no* hard dependency on JOSE libs; it only does:
  - path discovery
  - JSON/JWKS IO
  - JWT header (first segment) parse to extract `kid`/`alg`

Usage:
    from proofs.quantum_attest import (
        qpu_jwks_cache_dir, list_available_jwks, load_jwks, find_key,
        parse_jwt_header, refresh_jwks_cache, QPUKeyRef,
    )

    hdr = parse_jwt_header(token)  # {'kid': '...', 'alg': 'RS256', ...}
    key = find_key(kid=hdr.get('kid'), alg=hdr.get('alg'))
    # Pass `key` to provider_cert.verify_* for the actual signature check.

"""

from __future__ import annotations

import base64
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

__all__ = [
    "vendor_roots_dir",
    "qpu_jwks_cache_dir",
    "qpu_registry_path",
    "list_available_jwks",
    "load_jwks",
    "get_jwks_for",
    "jwks_iter_keys",
    "find_key",
    "parse_jwt_header",
    "refresh_jwks_cache",
    "QPUKeyRef",
]


# ---------- Paths & discovery ----------

def _repo_root_from_here() -> Path:
    # proofs/quantum_attest/__init__.py -> proofs/quantum_attest -> proofs -> repo root
    return Path(__file__).resolve().parents[2]


def vendor_roots_dir() -> Path:
    """
    Return the directory that holds vendor roots and QPU JWKS cache:
      <repo>/proofs/attestations/vendor_roots
    """
    return _repo_root_from_here() / "proofs" / "attestations" / "vendor_roots"


def qpu_jwks_cache_dir() -> Path:
    """
    Return the QPU JWKS cache directory:
      <repo>/proofs/attestations/vendor_roots/qpu_cache
    """
    return vendor_roots_dir() / "qpu_cache"


def qpu_registry_path() -> Path:
    """
    Return the QPU registry (providers+JWKS URIs) JSON file:
      <repo>/proofs/attestations/vendor_roots/qpu_roots.json
    """
    return vendor_roots_dir() / "qpu_roots.json"


# ---------- Models & helpers ----------

@dataclass(frozen=True)
class QPUKeyRef:
    """Minimal reference for a JWKS public key."""
    slug: str                 # provider slug (e.g., 'ibm_quantum')
    kid: str                  # key id
    alg: Optional[str]        # 'RS256', 'ES256', etc.
    kty: str                  # 'RSA', 'EC', 'OKP', ...
    crv: Optional[str] = None # for EC/OKP
    n: Optional[str] = None   # RSA modulus (base64url)
    e: Optional[str] = None   # RSA exponent (base64url)
    x: Optional[str] = None   # EC/OKP x (base64url)
    y: Optional[str] = None   # EC y (base64url)

    @staticmethod
    def from_jwk(slug: str, jwk: Dict[str, Any]) -> "QPUKeyRef":
        return QPUKeyRef(
            slug=slug,
            kid=jwk.get("kid", ""),
            alg=jwk.get("alg"),
            kty=jwk.get("kty", ""),
            crv=jwk.get("crv"),
            n=jwk.get("n"),
            e=jwk.get("e"),
            x=jwk.get("x"),
            y=jwk.get("y"),
        )


def _read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def list_available_jwks() -> List[Tuple[str, Path, int]]:
    """
    Return a list of (slug, path, num_keys) for all JWKS files in the cache.
    """
    cache = qpu_jwks_cache_dir()
    results: List[Tuple[str, Path, int]] = []
    if not cache.exists():
        return results
    for p in sorted(cache.glob("*.jwks.json")):
        slug = p.stem  # e.g., 'ibm_quantum' or 'extra_accounts_googleapis_com_certs'
        try:
            nkeys = len(_read_json(p).get("keys", []))
        except Exception:
            nkeys = -1
        results.append((slug, p, nkeys))
    return results


def load_jwks(slug: str) -> Dict[str, Any]:
    """
    Load a JWKS JSON object for the given slug from the cache directory.
    Raises FileNotFoundError if missing; ValueError if malformed.
    """
    path = qpu_jwks_cache_dir() / f"{slug}.jwks.json"
    obj = _read_json(path)
    if "keys" not in obj or not isinstance(obj["keys"], list):
        raise ValueError(f"JWKS at {path} missing 'keys' array")
    return obj


def get_jwks_for(slug: str) -> List[QPUKeyRef]:
    """Convenience: return QPUKeyRef list for a provider slug."""
    jwks = load_jwks(slug)
    return [QPUKeyRef.from_jwk(slug, k) for k in jwks.get("keys", [])]


def jwks_iter_keys(slug_patterns: Optional[Iterable[str]] = None) -> Iterable[QPUKeyRef]:
    """
    Iterate keys across one or many providers. If slug_patterns is None,
    iterate all cache entries.
    """
    if slug_patterns is None:
        for slug, path, _ in list_available_jwks():
            try:
                for ref in get_jwks_for(slug):
                    yield ref
            except Exception:
                continue
    else:
        for slug in slug_patterns:
            try:
                for ref in get_jwks_for(slug):
                    yield ref
            except Exception:
                continue


def find_key(kid: Optional[str], alg: Optional[str] = None, slugs: Optional[Iterable[str]] = None) -> Optional[QPUKeyRef]:
    """
    Find the first key that matches `kid` (and optionally `alg`) across the cache (or the given slugs).
    Returns QPUKeyRef or None.
    """
    if not kid:
        return None
    for ref in jwks_iter_keys(slugs):
        if ref.kid == kid and (alg is None or ref.alg == alg):
            return ref
    # Fallback: ignore alg if none matched strictly
    if alg is not None:
        for ref in jwks_iter_keys(slugs):
            if ref.kid == kid:
                return ref
    return None


# ---------- JWT header parsing (no verification here) ----------

def _b64url_pad(s: str) -> str:
    return s + "=" * ((4 - len(s) % 4) % 4)


def parse_jwt_header(token: str) -> Dict[str, Any]:
    """
    Parse the JOSE header (segment 0) of a JWT/JWS without verifying the signature.
    Useful to extract `kid` and `alg` to select a key from JWKS.

    Returns a dict (may be empty). Raises ValueError for malformed tokens.
    """
    try:
        head_b64 = token.split(".", 1)[0]
        raw = base64.urlsafe_b64decode(_b64url_pad(head_b64))
        return json.loads(raw.decode("utf-8"))
    except Exception as e:
        raise ValueError(f"Invalid JWT header: {e}") from e


# ---------- Cache refresh (invokes the installer script) ----------

def refresh_jwks_cache(extra_jwks: Optional[List[str]] = None) -> None:
    """
    Run the vendor_roots/install_official_qpu_roots.sh script to populate/refresh JWKS cache.
    Pass `extra_jwks` as a list of additional URIs (strings) to fetch alongside built-ins.
    """
    vroot = vendor_roots_dir()
    script = vroot / "install_official_qpu_roots.sh"
    if not script.exists():
        raise FileNotFoundError(f"Installer not found: {script}")
    env = os.environ.copy()
    if extra_jwks:
        env["QPU_EXTRA_JWKS"] = ",".join(extra_jwks)
    subprocess.run([str(script)], cwd=str(vroot), env=env, check=True)


# End of module
