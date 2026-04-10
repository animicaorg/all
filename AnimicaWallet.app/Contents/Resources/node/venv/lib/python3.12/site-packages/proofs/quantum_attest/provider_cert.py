"""
Animica • proofs.quantum_attest.provider_cert
============================================

Provider identity certificate parsing & verification with **hybrid** assurances:

- JWS/JWT (X.509-backed keys exposed via JWKS, typically EdDSA/ECDSA/RSA)
- Optional X.509 chain binding (leaf key ↔ JWS key) and time validity check
- Optional **Post-Quantum** signature over the canonical claims (e.g., Dilithium3 / SPHINCS+)

This module focuses on *identity* of quantum compute providers (IBM, Azure Quantum, Google, …).
It does **not** validate TEE/QPU *execution* attestation — see proofs/quantum_attest/traps.py and
proofs/ai.py for workload/trap verification.

Inputs we support
-----------------
1) **Compact JWS string**: "eyJhbGciOi….<payload>.<sig>", with claims JSON.
2) **Hybrid JSON envelope** (recommended):

   {
     "format": "hybrid-v1",
     "claims": {...},                   # canonical JSON (provider id, endpoints, capabilities, jwk thumbprint, etc.)
     "jws": "eyJhbGciOi...<compact>",   # JWS over the same 'claims'
     "x509_chain_pem": "-----BEGIN CERTIFICATE-----\\n...\\n",
     "pq": {
       "alg": "dilithium3" | "sphincs_shake_128s",
       "pub": "<base64 or hex>",        # raw public key bytes
       "sig": "<base64 or hex>"         # signature over canonical JSON(claims)
     }
   }

Verification strategy
---------------------
- Parse -> extract `kid`/`alg` from JWS header -> resolve public key from the local JWKS cache populated by
  `proofs/attestations/vendor_roots/install_official_qpu_roots.sh`. See `proofs.quantum_attest.__init__`.
- Verify JWS signature using `cryptography` (Ed25519/ES256/RS256) if available.
- If provided, parse `x509_chain_pem` and check:
    * now ∈ [not_before, not_after] for the leaf
    * chain linkage leaf->...->root (subject/issuer) and signature checks
    * binding: the leaf public key equals the JWS public key (strong binding)
- If provided, verify PQ signature over the canonical-JSON of `claims` using pq/py/verify.

Outputs
-------
`VerifiedProvider` with booleans per mechanism and a combined decision flag.

Notes
-----
- We avoid network I/O here; everything works from files & the local JWKS cache.
- If `cryptography` is missing, X.509/JWS checks may be skipped with a clear error.
- PQ verify requires the local pq package (see pq/py/*). If unavailable, we flag it as skipped.

"""

from __future__ import annotations

import base64
import binascii
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

# Optional crypto dependencies (graceful degradation)
try:
    from cryptography import x509
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import (ec, ed25519,
                                                           padding, rsa)
    from cryptography.hazmat.primitives.serialization import \
        load_pem_public_key
except Exception:  # pragma: no cover - optional
    x509 = None  # type: ignore

# Local JWKS helpers
try:
    from proofs.quantum_attest import (QPUKeyRef, find_key, parse_jwt_header,
                                       qpu_jwks_cache_dir, qpu_registry_path)
except Exception as e:  # pragma: no cover
    raise RuntimeError(f"Import failure: ensure repository is on PYTHONPATH; {e}")

# Optional Post-Quantum verification
try:
    from pq.py.registry import AlgId  # type: ignore
    from pq.py.verify import verify as pq_verify  # type: ignore
except Exception:  # pragma: no cover
    pq_verify = None
    AlgId = None  # type: ignore


# ---------- Utilities ----------


def _b64url_decode(data: str) -> bytes:
    pad = "=" * ((4 - len(data) % 4) % 4)
    return base64.urlsafe_b64decode(data + pad)


def _is_hex_like(s: str) -> bool:
    try:
        int(s, 16)
        return True
    except Exception:
        return False


def _decode_hex_or_b64(s: str) -> bytes:
    s = s.strip()
    if _is_hex_like(s.lower()):
        # allow 0x-prefix
        if s.lower().startswith("0x"):
            s = s[2:]
        return binascii.unhexlify(s)
    # try std/base64 and base64url
    try:
        return base64.b64decode(s, validate=True)
    except Exception:
        return _b64url_decode(s)


def _canonical_json(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ---------- Data classes ----------


@dataclass(frozen=True)
class JWSBundle:
    compact: str
    header: Dict[str, Any]
    payload: Dict[str, Any]


@dataclass(frozen=True)
class PQBundle:
    alg: str
    pub: bytes
    sig: bytes


@dataclass(frozen=True)
class X509Bundle:
    chain_pem: str  # one or more concatenated PEM certs


@dataclass
class ProviderCert:
    """
    Normalized provider certificate envelope.
    """

    claims: Dict[str, Any]
    jws: Optional[JWSBundle] = None
    x509: Optional[X509Bundle] = None
    pq: Optional[PQBundle] = None
    raw: Any = field(default=None)


@dataclass
class VerifiedProvider:
    claims: Dict[str, Any]
    jws_verified: bool
    x509_verified: bool
    pq_verified: bool
    key_ref: Optional[QPUKeyRef]
    jwks_slug_used: Optional[str]
    jwks_kid: Optional[str]
    alg: Optional[str]
    decisions: Dict[str, str]  # per-mechanism notes
    overall_ok: bool


# ---------- Parsing ----------


def parse_input(data: bytes) -> ProviderCert:
    """
    Accepts:
      - raw compact JWS (bytes/utf8)
      - JSON object of the hybrid envelope (see module doc)
    """
    txt = None
    try:
        txt = data.decode("utf-8")
    except Exception:
        pass

    # Try compact JWS pattern
    if txt and txt.count(".") == 2:
        header_b64, payload_b64, sig_b64 = txt.split(".")
        header = json.loads(_b64url_decode(header_b64))
        payload = json.loads(_b64url_decode(payload_b64))
        return ProviderCert(
            claims=payload,
            jws=JWSBundle(compact=txt, header=header, payload=payload),
            x509=None,
            pq=None,
            raw=txt,
        )

    # Else JSON envelope
    try:
        obj = json.loads(data)
    except Exception as e:
        raise ValueError(f"Unrecognized provider cert format: {e}")

    fmt = obj.get("format")
    claims = obj.get("claims")
    if not isinstance(claims, dict):
        raise ValueError("hybrid envelope missing dict 'claims'")

    # JWS piece
    jws_obj = None
    jws_compact = obj.get("jws")
    if isinstance(jws_compact, str) and jws_compact.count(".") == 2:
        h, p, _ = jws_compact.split(".")
        jws_obj = JWSBundle(
            compact=jws_compact,
            header=json.loads(_b64url_decode(h)),
            payload=json.loads(_b64url_decode(p)),
        )

    # X509 piece
    x509_obj = None
    if (
        isinstance(obj.get("x509_chain_pem"), str)
        and "BEGIN CERTIFICATE" in obj["x509_chain_pem"]
    ):
        x509_obj = X509Bundle(chain_pem=obj["x509_chain_pem"])

    # PQ piece
    pq_obj = None
    if isinstance(obj.get("pq"), dict):
        pqd = obj["pq"]
        alg = str(pqd.get("alg", "")).lower()
        pub = _decode_hex_or_b64(str(pqd.get("pub", "")))
        sig = _decode_hex_or_b64(str(pqd.get("sig", "")))
        if alg and pub and sig:
            pq_obj = PQBundle(alg=alg, pub=pub, sig=sig)

    return ProviderCert(
        claims=claims, jws=jws_obj, x509=x509_obj, pq=pq_obj, raw=obj if fmt else None
    )


# ---------- JWS verification using JWKS cache ----------


def _jwk_to_public_key(jwk: Dict[str, Any]):
    """
    Build a cryptography public key from a minimal JWK.
    Supports: RSA (RS256), EC P-256 (ES256), OKP Ed25519 (EdDSA).
    """
    if x509 is None:
        raise RuntimeError("cryptography not available for JWS verification")

    kty = jwk.get("kty")
    alg = jwk.get("alg")
    if kty == "OKP" and jwk.get("crv") == "Ed25519":
        from cryptography.hazmat.primitives import serialization

        raw = _b64url_decode(jwk["x"])
        # Construct from raw 32-byte Ed25519 pubkey
        return ed25519.Ed25519PublicKey.from_public_bytes(raw)

    if kty == "EC" and jwk.get("crv") == "P-256":
        x = int.from_bytes(_b64url_decode(jwk["x"]), "big")
        y = int.from_bytes(_b64url_decode(jwk["y"]), "big")
        public_numbers = ec.EllipticCurvePublicNumbers(x, y, ec.SECP256R1())
        return public_numbers.public_key(default_backend())

    if kty == "RSA":
        n = int.from_bytes(_b64url_decode(jwk["n"]), "big")
        e = int.from_bytes(_b64url_decode(jwk["e"]), "big")
        public_numbers = rsa.RSAPublicNumbers(e=e, n=n)
        return public_numbers.public_key(default_backend())

    raise ValueError(f"Unsupported JWK kty/crv combo: kty={kty}, alg={alg}")


def verify_jws_with_jwks(
    bundle: JWSBundle,
) -> Tuple[bool, Optional[QPUKeyRef], Optional[str], str]:
    """
    Verify the compact JWS using keys from local JWKS cache.

    Returns: (ok, key_ref, slug_used, note)
    """
    if x509 is None:
        return False, None, None, "cryptography not installed (cannot verify JWS)"

    header = bundle.header
    kid = header.get("kid")
    alg = header.get("alg")

    if not kid:
        return False, None, None, "JWS header missing 'kid'"

    # Find matching key across all cached providers
    key_ref = find_key(kid=kid, alg=alg, slugs=None)
    if not key_ref:
        return False, None, None, f"kid {kid} not found in JWKS cache"

    # Load the JWKS JSON for the slug to reconstruct the exact JWK
    jwks_path = qpu_jwks_cache_dir() / f"{key_ref.slug}.jwks.json"
    jwks = json.loads(jwks_path.read_text(encoding="utf-8"))
    jwk = None
    for k in jwks.get("keys", []):
        if k.get("kid") == kid:
            jwk = k
            break
    if jwk is None:
        return (
            False,
            key_ref,
            key_ref.slug,
            f"JWK {kid} disappeared from {jwks_path.name}",
        )

    # Build public key
    pub = _jwk_to_public_key(jwk)

    # Verify signature
    head_b64, pay_b64, sig_b64 = bundle.compact.split(".")
    signed = (head_b64 + "." + pay_b64).encode("utf-8")
    sig = _b64url_decode(sig_b64)

    try:
        if alg in ("EdDSA", "Ed25519"):
            assert isinstance(pub, ed25519.Ed25519PublicKey)
            pub.verify(sig, signed)
        elif alg in ("ES256",):
            assert isinstance(pub, ec.EllipticCurvePublicKey)
            pub.verify(sig, signed, ec.ECDSA(hashes.SHA256()))
        elif alg in ("RS256",):
            assert isinstance(pub, rsa.RSAPublicKey)
            pub.verify(sig, signed, padding.PKCS1v15(), hashes.SHA256())
        else:
            return False, key_ref, key_ref.slug, f"Unsupported alg {alg}"
    except Exception as e:
        return False, key_ref, key_ref.slug, f"Bad JWS signature: {e}"

    # Optional: payload claims time checks
    try:
        now = int(_now_utc().timestamp())
        payload = bundle.payload
        if "nbf" in payload and int(payload["nbf"]) > now + 60:
            return False, key_ref, key_ref.slug, "nbf in the future"
        if "exp" in payload and int(payload["exp"]) < now - 60:
            return False, key_ref, key_ref.slug, "exp in the past"
    except Exception:
        pass

    return True, key_ref, key_ref.slug, "ok"


# ---------- X.509 chain verification & binding ----------


def _load_pem_chain(pem_concatenated: str):
    if x509 is None:
        raise RuntimeError("cryptography not installed (cannot verify X.509)")
    certs = []
    for blob in pem_concatenated.split("-----END CERTIFICATE-----"):
        blob = blob.strip()
        if not blob:
            continue
        blob += "\n-----END CERTIFICATE-----\n"
        certs.append(
            x509.load_pem_x509_certificate(blob.encode("utf-8"), default_backend())
        )
    if not certs:
        raise ValueError("no certificates in PEM chain")
    return certs


def _pubkey_equal_to_jwk(cert: "x509.Certificate", jwk: Dict[str, Any]) -> bool:
    pub = cert.public_key()
    kty = jwk.get("kty")
    if kty == "OKP" and jwk.get("crv") == "Ed25519":
        want = _b64url_decode(jwk["x"])
        if isinstance(pub, ed25519.Ed25519PublicKey):
            try:
                raw = pub.public_bytes(encoding=None, format=None)  # type: ignore[arg-type]
            except Exception:
                # Older cryptography needs explicit enums:
                from cryptography.hazmat.primitives import serialization

                raw = pub.public_bytes(
                    serialization.Encoding.Raw,
                    serialization.PublicFormat.Raw,
                )
            return raw == want
        return False
    if kty == "EC" and jwk.get("crv") == "P-256":
        xwant = int.from_bytes(_b64url_decode(jwk["x"]), "big")
        ywant = int.from_bytes(_b64url_decode(jwk["y"]), "big")
        if isinstance(pub, ec.EllipticCurvePublicKey):
            nums = pub.public_numbers()
            return nums.x == xwant and nums.y == ywant
        return False
    if kty == "RSA":
        n = int.from_bytes(_b64url_decode(jwk["n"]), "big")
        e = int.from_bytes(_b64url_decode(jwk["e"]), "big")
        if isinstance(pub, rsa.RSAPublicKey):
            nums = pub.public_numbers()
            return nums.n == n and nums.e == e
        return False
    return False


def verify_x509_chain_binding(
    x509_bundle: X509Bundle, jws_bundle: Optional[JWSBundle]
) -> Tuple[bool, str]:
    """
    Minimal chain validation (time validity + chain linkage + JWS pubkey binding if provided).
    Not a full PKI path validator; good enough for vendor-rooted provider identity chains.

    Returns: (ok, note)
    """
    if x509 is None:
        return False, "cryptography not installed (cannot verify X.509)"

    certs = _load_pem_chain(x509_bundle.chain_pem)
    leaf, *intermediates = certs[0], certs[1:]

    # Time validity (leaf only)
    now = _now_utc()
    if leaf.not_valid_before.replace(tzinfo=timezone.utc) > now:
        return False, "leaf cert not yet valid"
    if leaf.not_valid_after.replace(tzinfo=timezone.utc) < now:
        return False, "leaf cert expired"

    # Simple linkage & signature checks: leaf->intermediates sequentially
    chain = certs
    for i in range(len(chain) - 1):
        child = chain[i]
        parent = chain[i + 1]
        if child.issuer.rfc4514_string() != parent.subject.rfc4514_string():
            return False, f"issuer/subject mismatch at position {i}"
        try:
            parent_pub = parent.public_key()
            # Attempt signature verification on TBSCertificate
            parent_pub.verify(
                child.signature,
                child.tbs_certificate_bytes,
                # Signature algorithm depends on child.signature_algorithm_oid; use default mapping:
                (
                    padding.PKCS1v15()
                    if isinstance(parent_pub, rsa.RSAPublicKey)
                    else ec.ECDSA(hashes.SHA256())
                ),
            )
        except Exception as e:
            return False, f"cert signature invalid at {i}: {e}"

    # Optional binding to JWS key (strongly recommended)
    if jws_bundle is not None:
        kid = jws_bundle.header.get("kid")
        if not kid:
            return False, "JWS missing kid for binding"
        # Recover exact JWK used (same as in JWS verification)
        from pathlib import Path as _Path

        # Search every JWKS for the kid (small cost, local)
        pick = None
        for p in sorted(qpu_jwks_cache_dir().glob("*.jwks.json")):
            jwks = json.loads(p.read_text(encoding="utf-8"))
            for k in jwks.get("keys", []):
                if k.get("kid") == kid:
                    pick = k
                    break
            if pick:
                break
        if not pick:
            return False, f"JWS kid {kid} not found in JWKS cache for binding"
        if not _pubkey_equal_to_jwk(leaf, pick):
            return False, "leaf public key does not match JWS key (binding failed)"

    return True, "ok"


# ---------- PQ signature verification ----------


def verify_pq_signature(pqb: PQBundle, claims: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Verify a PQ signature over canonical JSON(claims). Requires pq/py to be available.
    """
    if pq_verify is None or AlgId is None:
        return False, "pq/py not available"

    msg = _canonical_json(claims)
    alg = pqb.alg.lower()
    if alg not in ("dilithium3", "sphincs_shake_128s"):
        return False, f"unsupported PQ alg {pqb.alg}"

    try:
        ok = pq_verify(alg, pqb.pub, msg, pqb.sig)  # type: ignore[arg-type]
        return (True, "ok") if ok else (False, "PQ signature invalid")
    except Exception as e:
        return False, f"PQ verify error: {e}"


# ---------- High-level verify entrypoints ----------


def verify_provider_cert(cert: ProviderCert) -> VerifiedProvider:
    """
    Perform JWS, X.509 (if present), and PQ (if present) verifications.
    Returns a VerifiedProvider with per-mechanism and overall decisions.
    """
    decisions: Dict[str, str] = {}
    jws_ok = False
    x509_ok = False
    pq_ok = False
    key_ref: Optional[QPUKeyRef] = None
    slug_used: Optional[str] = None
    kid: Optional[str] = None
    alg: Optional[str] = None

    # JWS
    if cert.jws:
        jws_ok, key_ref, slug_used, note = verify_jws_with_jwks(cert.jws)
        decisions["jws"] = note
        kid = cert.jws.header.get("kid")
        alg = cert.jws.header.get("alg")
    else:
        decisions["jws"] = "absent"

    # X.509
    if cert.x509:
        ok, note = verify_x509_chain_binding(cert.x509, cert.jws)
        x509_ok = ok
        decisions["x509"] = note
    else:
        decisions["x509"] = "absent"

    # PQ
    if cert.pq:
        pq_ok, note = verify_pq_signature(cert.pq, cert.claims)
        decisions["pq"] = note
    else:
        decisions["pq"] = "absent"

    # Policy: overall_ok if (JWS ok) AND (binding ok if x509 present) AND (PQ ok if present)
    overall = jws_ok and (x509_ok or cert.x509 is None) and (pq_ok or cert.pq is None)

    return VerifiedProvider(
        claims=cert.claims,
        jws_verified=jws_ok,
        x509_verified=x509_ok,
        pq_verified=pq_ok,
        key_ref=key_ref,
        jwks_slug_used=slug_used,
        jwks_kid=kid,
        alg=alg,
        decisions=decisions,
        overall_ok=overall,
    )


def load_and_verify(path: str | Path | bytes) -> VerifiedProvider:
    """
    Convenience: load from file path or raw bytes, parse, verify.
    """
    if isinstance(path, (str, Path)):
        data = Path(path).read_bytes()
    else:
        data = path
    cert = parse_input(data)
    return verify_provider_cert(cert)


# ---------- CLI (dev helper) ----------


def _cli(argv: list[str]) -> int:  # pragma: no cover
    import argparse

    ap = argparse.ArgumentParser(description="Verify a provider identity certificate")
    ap.add_argument(
        "infile", help="Path to provider certificate (compact JWS or hybrid JSON)"
    )
    args = ap.parse_args(argv)

    try:
        vp = load_and_verify(args.infile)
    except Exception as e:
        print(f"[!] Parse/verify error: {e}")
        return 2

    print(
        json.dumps(
            {
                "overall_ok": vp.overall_ok,
                "jws_verified": vp.jws_verified,
                "x509_verified": vp.x509_verified,
                "pq_verified": vp.pq_verified,
                "jwks_slug_used": vp.jwks_slug_used,
                "jwks_kid": vp.jwks_kid,
                "alg": vp.alg,
                "decisions": vp.decisions,
                "claims": vp.claims,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_cli(sys.argv[1:]))
