"""
coretx.crypto - PQ Cryptography Registry
=========================================
"""

from __future__ import annotations

import hashlib
import logging
from typing import Optional, Protocol

from .errors import VerifyResult
from .schemes import (
    PolicyEvaluation,
    RuntimeScheme,
    build_runtime_scheme_table,
    evaluate_scheme_policy,
    load_policy_disabled_scheme_ids,
    load_allowed_signature_schemes_override,
    load_policy_override,
    required_schemes_for_chain,
    policy_roots,
)

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
    "get_signature_policy_status",
    "log_effective_policy_status",
    "assert_required_pq_for_chain",
    "enforce_non_empty_enabled_policy",
]

log = logging.getLogger(__name__)

_BACKEND_STATUS: dict[int, dict[str, object]] = {}

SCHEME_DILITHIUM3 = 1
SCHEME_SPHINCS_SHAKE_128S = 2
SCHEME_SPHINCS_SHAKE_128F = 3
SCHEME_SPHINCS_SHAKE_256S = 4

# v2 schemes — real PQ crypto (FIPS 204 ML-DSA-65). schemes 1+2 are
# commitment-style stubs vulnerable to forgery; ml_dsa_65 (11) is the
# vendored jack4818/dilithium-py reference and is the new default for
# `animica wallet create` from python package v0.2.0 onward.
SCHEME_ML_DSA_65 = 11


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
        enabled_by_code: bool = True,
        enabled_by_policy: bool = True,
        enabled_effective: bool = False,
        policy_root: str = "ANIMICA_DISABLED_SIGNATURE_SCHEMES",
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
        self.enabled_by_code = enabled_by_code
        self.enabled_by_policy = enabled_by_policy
        self.enabled_effective = enabled_effective
        self.policy_root = policy_root


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
                "enabledByCode": scheme.enabled_by_code,
                "enabledByPolicy": scheme.enabled_by_policy,
                "enabledEffective": bool(scheme.enabled and scheme.verify_func is not None),
                "reasonIfDisabled": scheme.reason_if_disabled,
                "policyRoot": scheme.policy_root,
            }
        )
    return out


def get_signature_policy_status() -> dict[str, object]:
    schemes = list_scheme_descriptors()
    return {
        "policyRoots": policy_roots(),
        "schemes": schemes,
        "override": _load_override_status(),
    }


def log_effective_policy_status(logger: logging.Logger | None = None) -> dict[str, object]:
    """Log effective signature policy details and return the status payload."""
    status = get_signature_policy_status()
    lg = logger or log
    enabled = [
        f"{s.get('name')}[{s.get('schemeId')}]"
        for s in status.get("schemes", [])
        if bool(s.get("enabledEffective"))
    ]
    lg.info(
        "Effective signature policy: enabled_schemes=%s policy_roots=%s",
        enabled,
        status.get("policyRoots", {}),
    )
    backends = status.get("backends", [])
    lg.info("PQ backend availability: %s", backends)
    return status


def assert_required_pq_for_chain(
    *,
    chain_id: int,
    is_validator: bool = False,
    is_mainnet_rpc: bool = False,
) -> None:
    """Fail fast when chain critical PQ schemes are not effectively enabled."""
    if chain_id != 1 and not is_validator and not is_mainnet_rpc:
        return

    status = get_signature_policy_status()
    scheme_map = {int(s.get("schemeId", -1)): s for s in status.get("schemes", [])}
    # ANM-C01: mainnet must require the real FIPS-204 scheme (ml_dsa_65 / 11),
    # NOT the forgeable commitment stubs 1 & 2. This dict was hardcoded to
    # {dilithium3, sphincs} which forced the forgeable schemes to stay enabled at
    # startup (the root of C01). The node fails closed if ml_dsa_65 is unavailable.
    required = {
        SCHEME_ML_DSA_65: "ml_dsa_65",
    }
    missing: list[str] = []
    for sid, name in required.items():
        row = scheme_map.get(sid)
        if not row or not bool(row.get("enabledEffective")):
            reason = (row or {}).get("reasonIfDisabled") or "unavailable"
            missing.append(f"{name}[{sid}] reason={reason}")

    if missing:
        role = "validator" if is_validator else "mainnet-rpc" if is_mainnet_rpc else "chain"
        raise RuntimeError(
            "Required PQ signature schemes are not enabled for "
            f"{role} on chainId={chain_id}: {', '.join(missing)}. "
            "Refusing startup to avoid partial/unsafe operation."
        )




def enforce_non_empty_enabled_policy(*, chain_id: int) -> None:
    status = get_signature_policy_status()
    enabled = [s for s in status.get("schemes", []) if bool(s.get("enabledEffective"))]
    if enabled:
        return

    required = sorted(required_schemes_for_chain(chain_id))
    env_ctx = {
        "ANIMICA_CHAIN_ID": __import__("os").environ.get("ANIMICA_CHAIN_ID"),
        "ANIMICA_ALLOWED_SIG_SCHEMES": __import__("os").environ.get("ANIMICA_ALLOWED_SIG_SCHEMES"),
        "ANIMICA_DISABLED_SIGNATURE_SCHEMES": __import__("os").environ.get("ANIMICA_DISABLED_SIGNATURE_SCHEMES"),
        "ANIMICA_ENABLE_POLICY_OVERRIDE": __import__("os").environ.get("ANIMICA_ENABLE_POLICY_OVERRIDE"),
        "ANIMICA_POLICY_OVERRIDE_FILE": __import__("os").environ.get("ANIMICA_POLICY_OVERRIDE_FILE"),
    }
    raise RuntimeError(
        "Signature policy resolved to enabled_schemes=[]; refusing startup. "
        f"chainId={chain_id} required={required} env={env_ctx} schemes={status.get('schemes', [])}"
    )

def _load_override_status() -> dict[str, object]:
    override = load_policy_override()
    return {
        "enabled": override.enabled,
        "mode": override.mode,
        "allowSigSchemes": list(override.allow_sig_schemes),
        "denySigSchemes": list(override.deny_sig_schemes),
        "comment": override.comment,
        "file": override.file_path,
    }


def _backend_diag_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for scheme_id in sorted(_BACKEND_STATUS):
        row = dict(_BACKEND_STATUS[scheme_id])
        row.setdefault("schemeId", scheme_id)
        rows.append(row)
    return rows


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
            supported=[{"id": s["schemeId"], "name": s["name"]} for s in list_scheme_descriptors() if s.get("enabledEffective")],
        )

    if not scheme.enabled:
        reason = (scheme.reason_if_disabled or "disabled_by_policy")
        kind = "backend_missing" if reason.startswith("backend_") else "disabled_by_policy"
        return VerifyResult.failure(
            kind,
            kind=kind,
            schemeId=scheme_id,
            name=scheme.name,
            policyRoot=scheme.policy_root,
            hint="Call tx.getSupportedSignatureSchemes and switch to an enabled scheme",
            disabledReason=reason,
            supported=[
                {"id": s["schemeId"], "name": s["name"], "enabledEffective": bool(s.get("enabledEffective"))}
                for s in list_scheme_descriptors()
            ],
        )

    if scheme.verify_func is None:
        return VerifyResult.failure(
            "backend_missing",
            kind="backend_missing",
            schemeId=scheme_id,
            name=scheme.name,
            policyRoot="crypto.backends",
            hint="Install the required PQ backend for this scheme",
            reason="backend_missing",
            supported=[
                {"id": s["schemeId"], "name": s["name"], "enabledEffective": bool(s.get("enabledEffective"))}
                for s in list_scheme_descriptors()
            ],
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
    override = load_policy_override()

    policy_eval: dict[int, PolicyEvaluation] = {}
    for runtime in table.values():
        decision = evaluate_scheme_policy(runtime.spec, disabled_by_policy=disabled_by_policy, override=override)
        policy_eval[runtime.spec.scheme_id] = decision
        runtime.enabled = decision.enabled_effective
        runtime.reason_if_disabled = decision.reason_if_disabled

    _BACKEND_STATUS.clear()

    try:
        from pq.py.algs import dilithium3 as dilithium3_backend
        from pq.py.algs import sphincs_shake_128s as sphincs_128s_backend
        from pq.py.algs import ml_dsa_65 as ml_dsa_65_backend

        def _sign_dilithium3(message: bytes, secret_key: bytes) -> bytes:
            return dilithium3_backend.sign(secret_key, message)

        def _verify_dilithium3(message: bytes, signature: bytes, public_key: bytes) -> bool:
            return bool(dilithium3_backend.verify(public_key, message, signature))

        def _sign_sphincs_128s(message: bytes, secret_key: bytes) -> bytes:
            return sphincs_128s_backend.sign(secret_key, message)

        def _verify_sphincs_128s(message: bytes, signature: bytes, public_key: bytes) -> bool:
            return bool(sphincs_128s_backend.verify(public_key, message, signature))

        def _sign_ml_dsa_65(message: bytes, secret_key: bytes) -> bytes:
            return ml_dsa_65_backend.sign(secret_key, message)

        def _verify_ml_dsa_65(message: bytes, signature: bytes, public_key: bytes) -> bool:
            return bool(ml_dsa_65_backend.verify(public_key, message, signature))

        runtime = table[SCHEME_DILITHIUM3]
        runtime.sign_fn = _sign_dilithium3
        runtime.verify_fn = _verify_dilithium3

        runtime = table[SCHEME_SPHINCS_SHAKE_128S]
        runtime.sign_fn = _sign_sphincs_128s
        runtime.verify_fn = _verify_sphincs_128s

        if SCHEME_ML_DSA_65 in table:
            runtime = table[SCHEME_ML_DSA_65]
            runtime.sign_fn = _sign_ml_dsa_65
            runtime.verify_fn = _verify_ml_dsa_65

        backend_modules = {
            SCHEME_DILITHIUM3: dilithium3_backend,
            SCHEME_SPHINCS_SHAKE_128S: sphincs_128s_backend,
            SCHEME_ML_DSA_65: ml_dsa_65_backend,
        }
        for scheme_id, alg in (
            (SCHEME_DILITHIUM3, "dilithium3"),
            (SCHEME_SPHINCS_SHAKE_128S, "sphincs_shake_128s"),
            (SCHEME_ML_DSA_65, "ml_dsa_65"),
        ):
            if scheme_id not in table:
                continue
            mod = backend_modules[scheme_id]
            status: dict[str, object] = {
                "schemeId": scheme_id,
                "name": alg,
                "available": False,
                "module": getattr(mod, "__name__", None),
                "modulePath": getattr(mod, "__file__", None),
                "moduleVersion": getattr(mod, "__version__", None),
            }
            try:
                if alg == "dilithium3":
                    sk, pk = dilithium3_backend.keypair()
                    sig = dilithium3_backend.sign(sk, b"animica-pq-selftest")
                    ok = bool(dilithium3_backend.verify(pk, b"animica-pq-selftest", sig))
                elif alg == "ml_dsa_65":
                    sk, pk = ml_dsa_65_backend.keypair()
                    sig = ml_dsa_65_backend.sign(sk, b"animica-pq-selftest")
                    ok = bool(ml_dsa_65_backend.verify(pk, b"animica-pq-selftest", sig))
                else:
                    sk, pk = sphincs_128s_backend.keypair()
                    sig = sphincs_128s_backend.sign(sk, b"animica-pq-selftest")
                    ok = bool(sphincs_128s_backend.verify(pk, b"animica-pq-selftest", sig))
                status["available"] = ok
                status["selfTest"] = "ok" if ok else "failed"
                if not ok:
                    table[scheme_id].verify_fn = None
                    table[scheme_id].reason_if_disabled = "backend_selftest_failed"
            except Exception as exc:
                table[scheme_id].verify_fn = None
                table[scheme_id].reason_if_disabled = "backend_missing"
                status["error"] = f"{type(exc).__name__}: {exc}"
                status["selfTest"] = "error"
            _BACKEND_STATUS[scheme_id] = status
    except Exception as exc:
        for scheme_id, name in ((SCHEME_DILITHIUM3, "dilithium3"), (SCHEME_SPHINCS_SHAKE_128S, "sphincs_shake_128s")):
            table[scheme_id].reason_if_disabled = "backend_missing"
            _BACKEND_STATUS[scheme_id] = {
                "schemeId": scheme_id,
                "name": name,
                "available": False,
                "error": f"{type(exc).__name__}: {exc}",
                "selfTest": "error",
            }

    # optional variants intentionally disabled unless explicit backend wiring is added
    for sid in (SCHEME_SPHINCS_SHAKE_128F, SCHEME_SPHINCS_SHAKE_256S):
        table[sid].reason_if_disabled = table[sid].reason_if_disabled or "backend_missing"

    _SCHEMES.clear()
    for scheme_id, runtime in sorted(table.items()):
        decision = policy_eval[scheme_id]
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
            enabled_by_code=decision.enabled_by_code,
            enabled_by_policy=decision.enabled_by_policy,
            enabled_effective=enabled,
            policy_root=decision.policy_root,
        )
        register_scheme(info)
        log.info(
            "Signature scheme: id=%s name=%s enabled=%s enabled_by_policy=%s reason=%s verify_fn=%s",
            info.scheme_id,
            info.name,
            info.enabled,
            info.enabled_by_policy,
            info.reason_if_disabled,
            bool(info.verify_func),
        )

    chain_id = int((__import__("os").environ.get("ANIMICA_CHAIN_ID") or "0") or "0")
    required = set(required_schemes_for_chain(chain_id))
    allowed_override = set(load_allowed_signature_schemes_override())
    if required and not allowed_override:
        log.info(
            "Signature policy source: safe_defaults_for_chain chainId=%s required=%s",
            chain_id,
            sorted(required),
        )
    elif allowed_override:
        log.info("Signature policy source: ANIMICA_ALLOWED_SIG_SCHEMES allowed=%s", sorted(allowed_override))


_orig_get_signature_policy_status = get_signature_policy_status


def get_signature_policy_status() -> dict[str, object]:
    status = _orig_get_signature_policy_status()
    status["backends"] = _backend_diag_rows()
    return status


_bootstrap_schemes()
