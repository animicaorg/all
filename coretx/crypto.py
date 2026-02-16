"""
coretx.crypto - PQ Cryptography Registry
=========================================
"""

from __future__ import annotations

import hashlib
import logging
from typing import Optional, Protocol

from .errors import VerifyResult
from .schemes import RuntimeScheme, build_runtime_scheme_table, load_policy_disabled_scheme_ids

__all__ = [
    "SCHEME_DILITHIUM3",
    "SCHEME_SPHINCS_SHAKE_128S",
    "SCHEME_SPHINCS_SHAKE_128F",
    "SCHEME_SPHINCS_SHAKE_256S",
    "SignFunc",
    "VerifyFunc",
    "SchemeInfo",
    "register_scheme",
    "get_scheme",
    "list_schemes",
    "list_scheme_descriptors",
    "infer_scheme_ids_by_lengths",
    "verify_signature",
    "pubkey_fingerprint",
]

log = logging.getLogger(__name__)

SCHEME_DILITHIUM3 = 1
SCHEME_SPHINCS_SHAKE_128S = 2
SCHEME_SPHINCS_SHAKE_128F = 3
SCHEME_SPHINCS_SHAKE_256S = 4


class SignFunc(Protocol):
    def __call__(self, message: bytes, secret_key: bytes) -> bytes: ...


class VerifyFunc(Protocol):
    def __call__(self, message: bytes, signature: bytes, public_key: bytes) -> bool: ...


class SchemeInfo:
    def __init__(
        self,
        scheme_id: int,
        name: str,
        sign_func: Optional[SignFunc],
        verify_func: Optional[VerifyFunc],
        pubkey_lengths: tuple[int, ...],
        signature_lengths: tuple[int, ...],
        enabled_by_default: bool = True,
        enabled: bool = False,
        reason_if_disabled: Optional[str] = None,
    ):
        self.scheme_id = scheme_id
        self.name = name
        self.sign_func = sign_func
        self.verify_func = verify_func
        self.pubkey_lengths = pubkey_lengths
        self.signature_lengths = signature_lengths
        self.enabled_by_default = enabled_by_default
        self.enabled = enabled
        self.reason_if_disabled = reason_if_disabled


_SCHEMES: dict[int, SchemeInfo] = {}


def register_scheme(info: SchemeInfo) -> None:
    _SCHEMES[info.scheme_id] = info


def get_scheme(scheme_id: int) -> Optional[SchemeInfo]:
    return _SCHEMES.get(scheme_id)


def list_schemes() -> list[SchemeInfo]:
    return list(_SCHEMES.values())


def list_scheme_descriptors() -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for scheme in sorted(_SCHEMES.values(), key=lambda s: s.scheme_id):
        out.append(
            {
                "schemeId": scheme.scheme_id,
                "name": scheme.name,
                "pubkeyLengths": list(scheme.pubkey_lengths),
                "signatureLengths": list(scheme.signature_lengths),
                "enabled": bool(scheme.enabled and scheme.verify_func is not None),
                "reasonIfDisabled": scheme.reason_if_disabled,
            }
        )
    return out


def infer_scheme_ids_by_lengths(pubkey_len: int, sig_len: int, *, enabled_only: bool = True) -> list[int]:
    matches: list[int] = []
    for scheme in _SCHEMES.values():
        if enabled_only and (not scheme.enabled or scheme.verify_func is None):
            continue
        if pubkey_len in scheme.pubkey_lengths and sig_len in scheme.signature_lengths:
            matches.append(scheme.scheme_id)
    return sorted(matches)


def pubkey_fingerprint(pubkey: bytes) -> str:
    h = hashlib.sha3_256(pubkey).digest()
    return h[:8].hex()


def verify_signature(
    scheme_id: int,
    message: bytes,
    signature: bytes,
    public_key: bytes,
) -> VerifyResult:
    scheme = get_scheme(scheme_id)
    if scheme is None:
        return VerifyResult.failure(
            "scheme_unsupported",
            scheme_id=scheme_id,
            pubkeyLen=len(public_key),
            sigLen=len(signature),
            supported=[{"id": s["schemeId"], "name": s["name"]} for s in list_scheme_descriptors() if s.get("enabled")],
        )

    if not scheme.enabled:
        return VerifyResult.failure(
            "scheme_disabled_by_policy",
            schemeId=scheme_id,
            name=scheme.name,
            policyRoot="ANIMICA_DISABLED_SIGNATURE_SCHEMES",
            hint="Enable scheme in node policy or switch wallet scheme",
            disabledReason=scheme.reason_if_disabled,
        )

    if scheme.verify_func is None:
        return VerifyResult.failure(
            "scheme_disabled_by_policy",
            schemeId=scheme_id,
            name=scheme.name,
            policyRoot="crypto.backends",
            hint="Install the required PQ backend for this scheme",
            reason="backend_missing",
        )

    if len(public_key) not in scheme.pubkey_lengths:
        return VerifyResult.failure(
            "invalid_pubkey_length",
            scheme_id=scheme_id,
            expected_lengths=list(scheme.pubkey_lengths),
            got=len(public_key),
            pubkey_fp=pubkey_fingerprint(public_key),
        )

    if len(signature) not in scheme.signature_lengths:
        return VerifyResult.failure(
            "invalid_signature_length",
            scheme_id=scheme_id,
            expected_lengths=list(scheme.signature_lengths),
            got=len(signature),
            pubkey_fp=pubkey_fingerprint(public_key),
        )

    try:
        valid = scheme.verify_func(message, signature, public_key)
        if valid:
            return VerifyResult.success()
        return VerifyResult.failure(
            "signature_invalid",
            schemeId=scheme_id,
            name=scheme.name,
            detail="verify() returned false",
            pubkey_fp=pubkey_fingerprint(public_key),
        )
    except Exception as e:
        log.warning("Signature verification raised exception: %s: %s", type(e).__name__, e)
        return VerifyResult.failure(
            "signature_invalid",
            schemeId=scheme_id,
            name=scheme.name,
            detail=f"verify() raised {type(e).__name__}",
            error_class=type(e).__name__,
            error_message=str(e),
            pubkey_fp=pubkey_fingerprint(public_key),
        )


def _bootstrap_schemes() -> None:
    table: dict[int, RuntimeScheme] = build_runtime_scheme_table()
    disabled_by_policy = load_policy_disabled_scheme_ids()

    for runtime in table.values():
        if runtime.spec.scheme_id in disabled_by_policy:
            runtime.enabled = False
            runtime.reason_if_disabled = "disabled_by_policy"
        else:
            runtime.enabled = runtime.spec.enabled_by_default

    try:
        from pq.py import dilithium3_sign, dilithium3_verify

        runtime = table[SCHEME_DILITHIUM3]
        runtime.sign_fn = dilithium3_sign
        runtime.verify_fn = dilithium3_verify
    except ImportError:
        table[SCHEME_DILITHIUM3].reason_if_disabled = "backend_missing"

    try:
        from pq.py import sphincs_shake_128s_sign, sphincs_shake_128s_verify

        runtime = table[SCHEME_SPHINCS_SHAKE_128S]
        runtime.sign_fn = sphincs_shake_128s_sign
        runtime.verify_fn = sphincs_shake_128s_verify
    except ImportError:
        table[SCHEME_SPHINCS_SHAKE_128S].reason_if_disabled = "backend_missing"

    try:
        from pq.py import sphincs_shake_128f_sign, sphincs_shake_128f_verify

        runtime = table[SCHEME_SPHINCS_SHAKE_128F]
        runtime.sign_fn = sphincs_shake_128f_sign
        runtime.verify_fn = sphincs_shake_128f_verify
    except ImportError:
        table[SCHEME_SPHINCS_SHAKE_128F].reason_if_disabled = "backend_missing"

    try:
        from pq.py import sphincs_shake_256s_sign, sphincs_shake_256s_verify

        runtime = table[SCHEME_SPHINCS_SHAKE_256S]
        runtime.sign_fn = sphincs_shake_256s_sign
        runtime.verify_fn = sphincs_shake_256s_verify
    except ImportError:
        table[SCHEME_SPHINCS_SHAKE_256S].reason_if_disabled = "backend_missing"

    _SCHEMES.clear()
    for scheme_id, runtime in sorted(table.items()):
        enabled = runtime.enabled and runtime.verify_fn is not None
        reason = runtime.reason_if_disabled
        if runtime.enabled and runtime.verify_fn is None:
            enabled = False
            reason = "backend_missing"
        info = SchemeInfo(
            scheme_id=runtime.spec.scheme_id,
            name=runtime.spec.name,
            sign_func=runtime.sign_fn,
            verify_func=runtime.verify_fn,
            pubkey_lengths=runtime.spec.pubkey_lengths,
            signature_lengths=runtime.spec.signature_lengths,
            enabled_by_default=runtime.spec.enabled_by_default,
            enabled=enabled,
            reason_if_disabled=reason,
        )
        register_scheme(info)
        log.info(
            "Signature scheme: id=%s name=%s enabled=%s reason=%s verify_fn=%s",
            info.scheme_id,
            info.name,
            info.enabled,
            info.reason_if_disabled,
            bool(info.verify_func),
        )


_bootstrap_schemes()
