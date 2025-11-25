# SPDX-License-Identifier: MIT
"""
Arm CCA Realm attestation token (EAT/COSE_Sign1) parser & verifier.

Scope (pragmatic subset for Animica):
- Accepts a COSE_Sign1-encoded token (CBOR array: [protected_bstr, unprotected_map, payload_bstr, signature_bstr]).
- Decodes protected/unprotected headers and CBOR payload (EAT/CCA claims).
- Extracts a certificate chain from COSE header parameter 'x5chain' (label = 33, RFC 9360).
- Verifies the COSE signature using the leaf certificate public key (ES256/ES384 or EdDSA).
- Optionally verifies a simple issuer→subject X.509 chain against a provided CCA root.
- Returns a TEEEvidence object (see .common) with measurement, nonce, pubkey-hash, platform hash, etc.

References:
  - Arm CCA Realm Attestation (RA-TOKEN) profiles (EAT-based, COSE_Sign1)
  - RFC 8152 / RFC 9052 (COSE), RFC 9360 (X.509 certs in COSE, x5chain=33)
  - CWT/EAT claims conventions

Notes:
  * We purposefully keep dependencies minimal. We try 'cbor2' first, and fall back to msgspec's CBOR if available.
  * We use 'cryptography' for X.509 and signature verification. If unavailable, we still parse but cannot verify.
  * The exact claim keys in real CCA deployments may vary slightly by profile. We accept common spellings and
    surface the entire claim map via evidence.meta['claims'].

"""

from __future__ import annotations
from dataclasses import asdict
from typing import Any, Dict, List, Optional, Tuple, Union

import binascii
import hashlib

# --- Optional CBOR backends ---------------------------------------------------
_CBOR_ERR = None
try:
    import cbor2 as _cbor
except Exception as e:  # pragma: no cover
    _CBOR_ERR = e
    try:
        import msgspec as _msgspec  # type: ignore
        def _cbor_dumps(x: Any) -> bytes:
            return _msgspec.cbor.encode(x)
        def _cbor_loads(b: bytes) -> Any:
            return _msgspec.cbor.decode(b)
    except Exception as e2:  # pragma: no cover
        _CBOR_ERR = (e, e2)
        _cbor = None

if "_cbor" in globals() and _cbor is not None:
    def _cbor_dumps(x: Any) -> bytes:
        return _cbor.dumps(x)
    def _cbor_loads(b: bytes) -> Any:
        return _cbor.loads(b)

# --- Optional crypto backend ---------------------------------------------------
try:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec, ed25519, utils
    _HAS_CRYPTO = True
except Exception:  # pragma: no cover
    _HAS_CRYPTO = False

from ..errors import AttestationError
from .common import TEEKind, TEEEvidence, TCBStatus

# COSE header labels we care about
COSE_HEADER_ALG   = 1      # 'alg'
COSE_HEADER_KID   = 4      # 'kid' (optional)
COSE_HEADER_X5C   = 33     # 'x5chain' (RFC 9360): array of DER certs (leaf first)

# COSE alg values we handle (per IANA COSE Algorithms)
ALG_ES256 = -7
ALG_ES384 = -35
ALG_ES512 = -36
ALG_EDDSA = -8  # Ed25519 or Ed448 (we support Ed25519 here)

# ---------------------------- Parsing helpers --------------------------------

def _require_cbor():
    if _cbor_dumps is None or _cbor_loads is None:
        raise AttestationError("CBOR backend not available; install 'cbor2' or 'msgspec'")

def _hex(b: Optional[bytes]) -> Optional[str]:
    return None if b is None else binascii.hexlify(b).decode()

def parse_cose_sign1(buf: Union[bytes, bytearray, memoryview]) -> Dict[str, Any]:
    """
    Parse a COSE_Sign1 structure.

    Returns:
      {
        "protected_bstr": bytes,
        "protected": dict,
        "unprotected": dict,
        "payload": bytes,
        "signature": bytes,
        "alg": int,
        "x5chain_der": List[bytes],   # may be empty
        "kid": Optional[bytes],
      }
    """
    _require_cbor()
    if not isinstance(buf, (bytes, bytearray, memoryview)):
        raise AttestationError("COSE token must be bytes-like")

    arr = _cbor_loads(bytes(buf))
    if not (isinstance(arr, list) or isinstance(arr, tuple)) or len(arr) != 4:
        raise AttestationError("Not a valid COSE_Sign1 (expected 4-element array)")

    protected_bstr, unprotected_map, payload_bstr, signature_bstr = arr

    if not isinstance(protected_bstr, (bytes, bytearray)):
        raise AttestationError("COSE protected header must be bstr")
    if not isinstance(unprotected_map, dict):
        raise AttestationError("COSE unprotected header must be map")
    if not isinstance(payload_bstr, (bytes, bytearray)):
        raise AttestationError("COSE payload must be bstr")
    if not isinstance(signature_bstr, (bytes, bytearray)):
        raise AttestationError("COSE signature must be bstr")

    # protected_bstr contains a CBOR-encoded map
    protected = _cbor_loads(bytes(protected_bstr)) if protected_bstr else {}
    if not isinstance(protected, dict):
        raise AttestationError("COSE protected header (decoded) must be map")

    alg = protected.get(COSE_HEADER_ALG, None)
    if alg not in (ALG_ES256, ALG_ES384, ALG_ES512, ALG_EDDSA):
        raise AttestationError(f"Unsupported/unknown COSE alg: {alg!r}")

    kid = protected.get(COSE_HEADER_KID, unprotected_map.get(COSE_HEADER_KID))

    x5chain_der: List[bytes] = []
    if COSE_HEADER_X5C in unprotected_map:
        v = unprotected_map[COSE_HEADER_X5C]
        if not isinstance(v, (list, tuple)) or not all(isinstance(c, (bytes, bytearray)) for c in v):
            raise AttestationError("x5chain must be an array of DER certificates")
        x5chain_der = [bytes(c) for c in v]

    return {
        "protected_bstr": bytes(protected_bstr),
        "protected": protected,
        "unprotected": unprotected_map,
        "payload": bytes(payload_bstr),
        "signature": bytes(signature_bstr),
        "alg": int(alg),
        "x5chain_der": x5chain_der,
        "kid": kid if isinstance(kid, (bytes, bytearray)) else None,
    }

# ---------------------------- Claim extraction --------------------------------

# Common CCA/EAT keys seen in realm tokens (string keys in practice).
CLAIM_KEYS = {
    "measurement": [
        "cca-realm-measurement",        # preferred
        "cca-realm-hash",               # alternative naming
        "realm_measurement",            # fallback
    ],
    "nonce": [
        "cca-realm-challenge",
        "nonce",
        "challenge",
    ],
    "pubkey_hash": [
        "cca-realm-public-key-hash",
        "realm_pubkey_hash",
        "realm-public-key-hash",
    ],
    "platform_hash": [
        "cca-platform-hash",
        "platform_hash",
    ],
    "personalization": [
        "cca-realm-personalization-value",
        "realm_personalization",
    ],
    "signer_id": [
        "cca-signer-id",
        "signer_id",
    ],
    "sw_components": [
        "sw-components",
        "cca-software-components",
        "software_components",
    ],
}

def _first_claim(claims: Dict[Any, Any], keys: List[str]) -> Optional[bytes]:
    for k in keys:
        if k in claims:
            v = claims[k]
            if isinstance(v, (bytes, bytearray)):
                return bytes(v)
            if isinstance(v, str):
                # allow hex string
                try:
                    return binascii.unhexlify(v)
                except Exception:
                    return v.encode()
    return None

def parse_cca_payload(payload: bytes) -> Dict[str, Any]:
    """
    Decode CBOR payload (EAT claims) and extract salient fields for scoring.
    Returns dict with binary fields as bytes (not hex).
    """
    _require_cbor()
    if not isinstance(payload, (bytes, bytearray)):
        raise AttestationError("CCA payload must be bytes-like")

    claims = _cbor_loads(bytes(payload))
    if not isinstance(claims, dict):
        raise AttestationError("CCA payload (decoded) must be a map of claims")

    # Extract common fields
    measurement   = _first_claim(claims, CLAIM_KEYS["measurement"])
    nonce         = _first_claim(claims, CLAIM_KEYS["nonce"])
    pubkey_hash   = _first_claim(claims, CLAIM_KEYS["pubkey_hash"])
    platform_hash = _first_claim(claims, CLAIM_KEYS["platform_hash"])
    personalization = _first_claim(claims, CLAIM_KEYS["personalization"])
    signer_id     = _first_claim(claims, CLAIM_KEYS["signer_id"])

    sw_components = None
    for k in CLAIM_KEYS["sw_components"]:
        if k in claims and isinstance(claims[k], list):
            sw_components = claims[k]
            break

    return {
        "claims": claims,
        "measurement": measurement,
        "nonce": nonce,
        "pubkey_hash": pubkey_hash,
        "platform_hash": platform_hash,
        "personalization": personalization,
        "signer_id": signer_id,
        "sw_components": sw_components,
    }

# -------------------------- Signature verification ----------------------------

def _ecdsa_verify_raw(pubkey: ec.EllipticCurvePublicKey, msg: bytes, sig_raw: bytes, hash_alg) -> bool:
    """
    COSE uses raw r||s for ECDSA signatures. Convert to DER for 'cryptography'.
    """
    if len(sig_raw) % 2 != 0:
        return False
    half = len(sig_raw) // 2
    r = int.from_bytes(sig_raw[:half], "big")
    s = int.from_bytes(sig_raw[half:], "big")
    der = utils.encode_dss_signature(r, s)
    try:
        pubkey.verify(der, msg, ec.ECDSA(hash_alg))
        return True
    except Exception:
        return False

def _sig_structure(protected_bstr: bytes, payload_bstr: bytes, external_aad: bytes = b"") -> bytes:
    # Sig_structure = ["Signature1", protected_bstr, external_aad, payload_bstr]
    _require_cbor()
    return _cbor_dumps(["Signature1", protected_bstr, external_aad, payload_bstr])

def _verify_with_leaf(cert_pem: bytes, alg: int, to_be_signed: bytes, sig: bytes) -> bool:
    if not _HAS_CRYPTO:
        return False
    try:
        cert = x509.load_pem_x509_certificate(cert_pem)
        pub = cert.public_key()
        if alg == ALG_EDDSA:
            # We only support Ed25519 here
            if not isinstance(pub, ed25519.Ed25519PublicKey):
                return False
            try:
                pub.verify(sig, to_be_signed)
                return True
            except Exception:
                return False
        elif alg in (ALG_ES256, ALG_ES384, ALG_ES512):
            if not isinstance(pub, ec.EllipticCurvePublicKey):
                return False
            if   alg == ALG_ES256: h = hashes.SHA256()
            elif alg == ALG_ES384: h = hashes.SHA384()
            else:                  h = hashes.SHA512()
            return _ecdsa_verify_raw(pub, to_be_signed, sig, h)
        else:
            return False
    except Exception:
        return False

def _der_to_pem_chain(der_list: List[bytes]) -> List[bytes]:
    if not _HAS_CRYPTO:
        return []
    out = []
    for der in der_list:
        try:
            cert = x509.load_der_x509_certificate(der)
            out.append(cert.public_bytes(serialization.Encoding.PEM))
        except Exception:
            # keep as-is in PEM-ish wrapper if needed
            out.append(
                b"-----BEGIN CERTIFICATE-----\n"
                + binascii.b2a_base64(der).replace(b"\n", b"") + b"\n-----END CERTIFICATE-----\n"
            )
    return out

def verify_chain_simple(leaf_pem: bytes, chain_pems: List[bytes], root_pem: Optional[bytes]) -> bool:
    """
    Simple issuer->subject chain walk up to provided root (if given).
    This mirrors the helper used in other attesters (not a full PKI engine).
    """
    if not _HAS_CRYPTO:
        return False
    try:
        leaf = x509.load_pem_x509_certificate(leaf_pem)
        by_subject = {}
        for pem in chain_pems:
            try:
                c = x509.load_pem_x509_certificate(pem)
                by_subject[c.subject.rfc4514_string()] = c
            except Exception:
                pass
        root = x509.load_pem_x509_certificate(root_pem) if root_pem else None
        if root:
            by_subject[root.subject.rfc4514_string()] = root

        curr = leaf
        while True:
            isub = curr.issuer.rfc4514_string()
            ssub = curr.subject.rfc4514_string()
            if isub == ssub:
                # self-signed (root)
                if root and curr.fingerprint(hashes.SHA256()) != root.fingerprint(hashes.SHA256()):
                    return False
                try:
                    curr.public_key().verify(curr.signature, curr.tbs_certificate_bytes,
                                             ec.ECDSA(curr.signature_hash_algorithm))
                except Exception:
                    return False
                return True
            parent = by_subject.get(isub)
            if parent is None:
                # If a root is required but not reached -> fail; else pass leniently
                return root is None
            try:
                parent.public_key().verify(curr.signature, curr.tbs_certificate_bytes,
                                           ec.ECDSA(curr.signature_hash_algorithm))
            except Exception:
                return False
            curr = parent
    except Exception:
        return False

# ---------------------------- High-level verify -------------------------------

def verify_cca_realm_token(
    token: Union[bytes, bytearray, memoryview],
    *,
    cca_root_pem: Optional[bytes] = None,
    external_aad: bytes = b"",
) -> TEEEvidence:
    """
    Verify a CCA Realm attestation token (COSE_Sign1).

    Args:
      token: COSE_Sign1 binary.
      cca_root_pem: Optional ARM CCA root-of-trust PEM to anchor 'x5chain'.
      external_aad: Optional external AAD to bind in COSE Sig_structure (normally empty).

    Returns:
      TEEEvidence with:
        vendor="arm", kind=TEEKind.CCA_REALM
        measurement (bytes), nonce (bytes), pubkey_hash (bytes), platform_hash (bytes)
        signature_ok (bool), chain_ok (bool), tcb_status (OK)
        meta: {
          'kid', 'alg', 'x5chain_len', 'claims' (full decoded map), and misc claim hex
        }
    """
    parts = parse_cose_sign1(token)
    payload_info = parse_cca_payload(parts["payload"])

    # Build Sig_structure for COSE_Sign1
    to_be_signed = _sig_structure(parts["protected_bstr"], parts["payload"], external_aad)

    # Extract x5chain (PEM) and pick leaf
    chain_pems = _der_to_pem_chain(parts["x5chain_der"])
    leaf_pem = chain_pems[0] if chain_pems else None

    signature_ok = False
    if leaf_pem:
        signature_ok = _verify_with_leaf(leaf_pem, parts["alg"], to_be_signed, parts["signature"])

    chain_ok = True
    if leaf_pem and cca_root_pem:
        chain_ok = verify_chain_simple(leaf_pem, chain_pems[1:], cca_root_pem)

    # Construct evidence
    evidence = TEEEvidence(
        vendor="arm",
        kind=getattr(TEEKind, "CCA_REALM", TEEKind.CCA),  # prefer CCA_REALM if present
        measurement=payload_info["measurement"] or b"",
        report=bytes(token),
        report_data=payload_info["nonce"] or b"",
        host_data=payload_info["platform_hash"] or b"",
        policy=0,
        guest_svn=0,
        vmpl=0,
        platform_flags={},  # not applicable for CCA token
        signing_key="x5chain" if leaf_pem else "unknown",
        chain_ok=bool(chain_ok),
        signature_ok=bool(signature_ok),
        tcb={"sw_components": payload_info["sw_components"]},
        tcb_status=TCBStatus.OK,
        meta={
            "kid": _hex(parts["kid"]) if parts["kid"] else None,
            "alg": parts["alg"],
            "x5chain_len": len(chain_pems),
            "pubkey_hash": _hex(payload_info["pubkey_hash"]) if payload_info["pubkey_hash"] else None,
            "platform_hash": _hex(payload_info["platform_hash"]) if payload_info["platform_hash"] else None,
            "personalization": _hex(payload_info["personalization"]) if payload_info["personalization"] else None,
            "signer_id": _hex(payload_info["signer_id"]) if payload_info["signer_id"] else None,
            "claims": payload_info["claims"],
        },
    )
    return evidence

# Convenience helper (non-standard): hash of protected||payload for transcripts.
def transcript_hash(token: Union[bytes, bytearray, memoryview]) -> str:
    parts = parse_cose_sign1(token)
    return hashlib.sha3_256(parts["protected_bstr"] + parts["payload"]).hexdigest()

__all__ = [
    "verify_cca_realm_token",
    "parse_cose_sign1",
    "parse_cca_payload",
    "transcript_hash",
]
