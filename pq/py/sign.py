
from __future__ import annotations

import hashlib
from typing import Any, Dict, Optional, Tuple, Union

import oqs  # type: ignore

DILITHIUM3_ID = 0x1001


def _normalize_alg(alg: Union[int, str, Any]) -> Tuple[int, str]:
    if isinstance(alg, int):
        if alg == DILITHIUM3_ID:
            return DILITHIUM3_ID, "dilithium3"
        raise NotImplementedError(f"Unknown alg id: 0x{alg:04x}")
    if isinstance(alg, str):
        n = alg.lower().strip()
        if n in ("dilithium3", "ml-dsa-65", "mldsa65"):
            return DILITHIUM3_ID, "dilithium3"
        raise NotImplementedError(f"Unknown alg name: {alg}")
    if hasattr(alg, "alg_id"):
        return _normalize_alg(int(getattr(alg, "alg_id")))
    if hasattr(alg, "name"):
        return _normalize_alg(str(getattr(alg, "name")))
    raise NotImplementedError(f"Unsupported alg descriptor: {type(alg)}")


def _enabled_mechs() -> list[str]:
    for fn in ("get_enabled_sig_mechanisms", "get_enabled_mechanisms"):
        if hasattr(oqs, fn):
            try:
                return list(getattr(oqs, fn)())
            except Exception:
                continue
    return []


def _pick_sig_mech(alg_name: str) -> str:
    mechs = _enabled_mechs()
    if alg_name == "dilithium3":
        if "ML-DSA-65" in mechs:
            return "ML-DSA-65"
        if "Dilithium3" in mechs:
            return "Dilithium3"
        return "ML-DSA-65"
    raise NotImplementedError(f"Unsupported signature alg: {alg_name}")


def _normalize_domain_path(domain: str, *, alg_name: str) -> str:
    d = (domain or "").strip()
    if not d:
        return f"sig|{alg_name}|tx"
    # If already fully-qualified, keep it:
    if d.startswith("sig|"):
        return d
    # Shorthand: "tx" -> "sig|dilithium3|tx"
    return f"sig|{alg_name}|{d}"


def build_domain_tag(
    *,
    chain_id: int,
    domain: str,
    alg_name: str,
) -> bytes:
    """
    Domain tag string is ASCII and included in the signed bytes.
    Keep this stable: node must generate identical tag to verify.
    """
    domain_path = _normalize_domain_path(domain, alg_name=alg_name)
    # animica:<chainId>|<domainPath>
    return f"animica:{chain_id}|{domain_path}".encode("utf-8")


def build_sign_bytes(
    message: bytes,
    *,
    chain_id: int,
    domain: str,
    alg_name: str,
    context: bytes = b"",
) -> bytes:
    """
    Sign-bytes format (v1):
      b"ANM|" + domain_tag + b"|" + context + b"|" + message
    """
    if not isinstance(message, (bytes, bytearray)):
        raise TypeError("message must be bytes")
    if not isinstance(context, (bytes, bytearray)):
        raise TypeError("context must be bytes")
    tag = build_domain_tag(chain_id=chain_id, domain=domain, alg_name=alg_name)
    return b"ANM|" + tag + b"|" + bytes(context) + b"|" + bytes(message)


def _apply_prehash(sign_bytes: bytes, prehash: Optional[str]) -> bytes:
    if prehash is None or prehash == "" or prehash == "none":
        return sign_bytes
    if prehash == "sha3-256":
        return hashlib.sha3_256(sign_bytes).digest()
    raise NotImplementedError(f"Unsupported prehash: {prehash}")


def pq_sign_detached(
    message: bytes,
    alg: Union[int, str, Any],
    secret_key: bytes,
    *,
    domain: str = "tx",
    chain_id: int,
    context: bytes = b"",
    prehash: Optional[str] = "sha3-256",
) -> Dict[str, Any]:
    """
    Returns a CBOR/JSON-friendly signature envelope dict.
    """
    alg_id, alg_name = _normalize_alg(alg)
    mech = _pick_sig_mech(alg_name)

    sign_bytes = build_sign_bytes(
        message,
        chain_id=chain_id,
        domain=domain,
        alg_name=alg_name,
        context=context,
    )
    to_sign = _apply_prehash(sign_bytes, prehash)

    # Different liboqs-python versions accept secret_key in different ways.
    sig_obj = None
    try:
        sig_obj = oqs.Signature(mech, secret_key=secret_key)
    except TypeError:
        sig_obj = oqs.Signature(mech)
        if hasattr(sig_obj, "import_secret_key"):
            sig_obj.import_secret_key(secret_key)  # type: ignore

    signature = sig_obj.sign(to_sign)

    return {
        "alg": alg_id,
        "sig": bytes(signature),
        "domain": domain,
        "prehash": prehash or "none",
        "chain_id": int(chain_id),
    }
