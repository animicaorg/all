from __future__ import annotations

import asyncio
import base64
import dataclasses as _dc
import hashlib
import inspect
import logging
import os
import time
import traceback
import uuid
import typing as t

from rpc import deps
from rpc import errors as rpc_errors
from rpc.methods import method
from rpc.methods import miner as miner_methods
from animica.sync.readiness import assess_tx_submission_readiness
from mempool.tx_hash import tx_hash_hex as _tx_hash_hex
from core.utils.tx import coerce_int as _coerce_tx_int, TxNormalizationError as _TxNormalizationError
from core.utils.tx import normalize_tx_fields as _normalize_tx_fields
from rpc.instant_tx import get_instant_tx_service_singleton

try:
    from core.utils.deploy_metadata import (
        build_python_vm_package_deploy_metadata as _build_pyvm_deploy_metadata,
        copy_deploy_metadata_fields as _copy_deploy_metadata_fields,
    )
except Exception:  # pragma: no cover
    _build_pyvm_deploy_metadata = None  # type: ignore[assignment]
    _copy_deploy_metadata_fields = None  # type: ignore[assignment]

log = logging.getLogger(__name__)
_PQ_VERIFY_DEBUG = os.environ.get("ANIMICA_PQ_VERIFY_DEBUG") == "1"
_PQ_VERIFY_OPTIONAL = os.environ.get("ANIMICA_PQ_VERIFY_OPTIONAL") == "1" or (
    os.environ.get("ANIMICA_SKIP_PQ_VERIFY") == "1"
)
_RPC_DEBUG = os.environ.get("ANIMICA_RPC_DEBUG") == "1"
# Safe, read-only tx diagnostics (verify / signing-preimage / sign-hash) are on by
# default for integrators; set ANIMICA_DEBUG_RPC=0 to disable. (They never mutate
# state or leak secrets.)
_DEBUG_RPC = os.environ.get("ANIMICA_DEBUG_RPC", "1") != "0"
_TX_SEND_FORCE_CHAIN = os.environ.get("ANIMICA_TX_SEND_FORCE_CHAIN", "0") == "1"
_TX_SEND_FORCE_CHAIN_TIMEOUT_S = float(os.environ.get("ANIMICA_TX_SEND_FORCE_CHAIN_TIMEOUT_S", "5") or 5)

# ——— Validation failure metrics ———
try:
    from rpc.metrics import (
        TX_VALIDATION_FAILURES,
        TX_DECODER_SUCCESS,
        TX_DECODER_FALLBACK,
        TX_DECODER_ALL_FAILED,
    )
except Exception:  # pragma: no cover
    class _Counter:
        def labels(self, **kwargs):
            return self

        def inc(self, *args, **kwargs):
            pass

    TX_VALIDATION_FAILURES = _Counter()  # type: ignore[assignment]
    TX_DECODER_SUCCESS = _Counter()  # type: ignore[assignment]
    TX_DECODER_FALLBACK = _Counter()  # type: ignore[assignment]
    TX_DECODER_ALL_FAILED = _Counter()  # type: ignore[assignment]

# ——— Optional deps (be tolerant during early bring-up) ———

# CBOR codec (canonical, from core)
try:
    from core.encoding.cbor import dumps as _cbor_dumps
    from core.encoding.cbor import loads as _cbor_loads  # type: ignore
except Exception:  # pragma: no cover
    _cbor_loads = None  # type: ignore
    _cbor_dumps = None  # type: ignore

# Canonical SignBytes encoders (preferred)
try:
    from core.encoding.canonical import tx_sign_bytes as _tx_sign_bytes  # type: ignore
except Exception:  # pragma: no cover
    _tx_sign_bytes = None  # type: ignore

# Shared Animica helper for deterministic tx sign-bytes
try:
    from animica.tx.signing import (
        DOMAIN_TX_SIGN_V1 as _DOMAIN_TX_SIGN_V1,
        ChainContext as _ChainContext,
        build_signable_tx_bytes as _build_signable_tx_bytes,
        pq_verify_tx as _pq_verify_tx,
        tx_signing_preimage as _tx_signing_preimage,
    )
except Exception:  # pragma: no cover
    _DOMAIN_TX_SIGN_V1 = "animica.tx.v1"
    _ChainContext = None  # type: ignore
    _build_signable_tx_bytes = None  # type: ignore
    _pq_verify_tx = None  # type: ignore
    _tx_signing_preimage = None  # type: ignore

# SDK SignBytes helper (fallback for defensive verification)
try:
    from omni_sdk.tx.encode import sign_bytes as _sdk_sign_bytes  # type: ignore
except Exception:  # pragma: no cover
    _sdk_sign_bytes = None  # type: ignore

# Tx dataclass (optional; we can operate on dicts too)
try:
    from core.types.tx import Tx as _Tx  # type: ignore
except Exception:  # pragma: no cover
    _Tx = None  # type: ignore

# Tx normalization (canonical hashing)
try:
    from core.utils.tx import normalize_tx_bytes as _normalize_tx_bytes  # type: ignore
    from core.utils.tx import normalize_tx_envelope as _normalize_tx_envelope  # type: ignore
    from core.utils.tx import TxNormalizationError as _TxNormalizationError  # type: ignore
except Exception:  # pragma: no cover
    _normalize_tx_bytes = None  # type: ignore
    _normalize_tx_envelope = None  # type: ignore
    _TxNormalizationError = None  # type: ignore

# Hashing
try:
    from core.utils.hash import sha3_256 as _sha3_256  # type: ignore
except Exception:  # pragma: no cover
    import hashlib

    def _sha3_256(b: bytes) -> bytes:  # type: ignore
        return hashlib.sha3_256(b).digest()


# Pending pool (optional fallback store, but NOT sufficient for mining)
_PEND = None
try:
    from rpc.pending_pool import pool as _PEND  # type: ignore
except Exception:  # pragma: no cover
    _PEND = None  # type: ignore

# Mempool errors (optional)
try:  # pragma: no cover
    from mempool.errors import MempoolError, ReplacementUnsupported, PersistenceFailed  # type: ignore
except Exception:  # pragma: no cover
    MempoolError = None  # type: ignore
    ReplacementUnsupported = None  # type: ignore
    PersistenceFailed = None  # type: ignore

# PQ verify
try:
    from pq.py import verify as _pq_verify  # type: ignore
except Exception:  # pragma: no cover
    _pq_verify = None  # type: ignore

# State service for balance queries (needed for pre-mempool balance validation)
try:
    from rpc.state_service import parse_address as _parse_address  # type: ignore
except Exception:  # pragma: no cover
    _parse_address = None  # type: ignore

# Bech32m address encoding
try:
    from pq.py.address import address_from_pubkey as _address_from_pubkey  # type: ignore
except Exception:  # pragma: no cover
    _address_from_pubkey = None  # type: ignore

# PQ algorithm registry (for alg_id lookups)
try:
    from pq.py.registry import ALG_ID as _ALG_ID  # type: ignore
    from pq.py.registry import ALG_NAME as _ALG_NAME  # type: ignore
except Exception:  # pragma: no cover
    _ALG_ID = None  # type: ignore
    _ALG_NAME = None  # type: ignore

# Alternative CBOR decoders (for defensive import fallback)
try:
    import cbor2 as _cbor2_module  # type: ignore
except Exception:  # pragma: no cover
    _cbor2_module = None  # type: ignore

try:
    import msgspec as _msgspec_module  # type: ignore
except Exception:  # pragma: no cover
    _msgspec_module = None  # type: ignore

# JSON codec (always available in standard library, used for defensive fallback)
import json as _json_module


# ——— Local fallback pending store (development only) ———
_FALLBACK_PENDING: dict[str, bytes] = {}
_FALLBACK_PENDING_TS: dict[str, float] = {}

# Track txs seen in blocks that were later reorged out.
_REORGED_TXS: dict[str, float] = {}
_REORGED_TXS_TTL_S = float(os.environ.get("ANIMICA_REORG_TX_TTL_S", "86400") or 86400)


# ——— Helpers ———

def _dcd(obj: t.Any) -> t.Any:
    if _dc.is_dataclass(obj):
        return {k: _dcd(v) for k, v in _dc.asdict(obj).items()}
    if isinstance(obj, (list, tuple)):
        return [_dcd(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _dcd(v) for k, v in obj.items()}
    return obj


def _hex(b: bytes | bytearray | None) -> str | None:
    return None if b is None else "0x" + bytes(b).hex()


def _b(x: str | bytes | bytearray) -> bytes:
    if isinstance(x, (bytes, bytearray)):
        return bytes(x)
    s = x.strip().lower()
    if s.startswith("0x"):
        s = s[2:]
    return bytes.fromhex(s)


def normalize_send_raw_tx_params(
    params: t.Any,
    *,
    rawTx: t.Any = None,
    raw_tx: t.Any = None,
    tx: t.Any = None,
    raw: t.Any = None,
    cbor: t.Any = None,
    txBytes: t.Any = None,
) -> tuple[bytes, dict[str, t.Any]]:
    """Normalize tx.sendRawTransaction params into canonical raw transaction bytes."""

    expected_schema = {
        "accepted": [
            {"params": ["0xdeadbeef"]},
            {"params": ["base64...", {"broadcast": True}]},
            {"params": {"tx": "0xdeadbeef"}},
            {"params": "0xdeadbeef"},
        ],
        "keys": ["raw", "rawTx", "rawtx", "tx", "signedTx", "signed_tx", "cbor", "txBytes"],
    }
    attempts: list[dict[str, t.Any]] = []
    key_priority = ("rawTx", "raw_tx", "rawtx", "tx", "raw", "signedTx", "signed_tx", "cbor", "txBytes")

    def _preview(value: t.Any) -> t.Any:
        if isinstance(value, str):
            return value[:64] + ("..." if len(value) > 64 else "")
        if isinstance(value, (bytes, bytearray)):
            return f"<bytes:{len(value)}>"
        if isinstance(value, list):
            return [_preview(v) for v in value[:3]]
        if isinstance(value, dict):
            return {k: _preview(v) for k, v in list(value.items())[:6]}
        return value

    def _invalid(field: str, detail: str, source: t.Any) -> rpc_errors.InvalidParams:
        return rpc_errors.InvalidParams(
            f"Field {field} must be hex string or base64; {detail}",
            data={
                "expected": expected_schema,
                "field": field,
                "received_type": type(source).__name__,
                "received_preview": _preview(source),
                "normalize_attempts": attempts,
                "hints": [
                    "Use params as object|array|string.",
                    "Provide tx bytes as 0x-prefixed hex or base64 string.",
                ],
                "suggested_payload": {"jsonrpc": "2.0", "id": 1, "method": "tx.sendRawTransaction", "params": {"tx": "0xdeadbeef"}},
            },
        )

    source = params
    if source is None:
        kwargs_obj = {"rawTx": rawTx, "raw_tx": raw_tx, "tx": tx, "raw": raw, "cbor": cbor, "txBytes": txBytes}
        present = {k: v for k, v in kwargs_obj.items() if v is not None}
        if len(present) == 1:
            source = next(iter(present.values()))
            attempts.append({"step": "kwargs.single_value", "ok": True, "key": next(iter(present.keys()))})
        elif len(present) > 1:
            source = present
            attempts.append({"step": "kwargs.to_object", "ok": True, "keys": list(present.keys())})

    shape = type(source).__name__
    key_used: str | None = None
    meta: dict[str, t.Any] = {"shape": shape, "key": None, "size_bytes": 0}

    if source is None:
        attempts.append({"step": "missing_params", "ok": False, "reason": "params missing or null"})
        raise _invalid("params", "got null/missing", source)

    if isinstance(source, str):
        s = source.strip()
        if s.startswith("{") or s.startswith("["):
            try:
                source = _json_module.loads(s)
                attempts.append({"step": "string.json_parse", "ok": True})
            except Exception as exc:
                attempts.append({"step": "string.json_parse", "ok": False, "reason": str(exc)})

    if isinstance(source, dict) and isinstance(source.get("params"), list):
        attempts.append({"step": "unwrap_nested_params", "ok": True})
        source = source["params"]

    tx_value: t.Any = None
    options: dict[str, t.Any] = {}

    if isinstance(source, (list, tuple)):
        if not source:
            attempts.append({"step": "extract_list", "ok": False, "reason": "empty list"})
            raise _invalid("params[0]", "list cannot be empty", source)
        first = source[0]
        if isinstance(first, dict):
            for key in key_priority:
                if key in first:
                    key_used = key
                    tx_value = first.get(key)
                    break
            options = source[1] if len(source) > 1 and isinstance(source[1], dict) else {}
        else:
            tx_value = first
            options = source[1] if len(source) > 1 and isinstance(source[1], dict) else {}
        attempts.append({"step": "extract_list", "ok": tx_value is not None, "key": key_used})
    elif isinstance(source, dict):
        for key in key_priority:
            if key in source:
                key_used = key
                tx_value = source.get(key)
                break
        if tx_value is None and isinstance(source.get("params"), (dict, list, str)):
            source = source["params"]
            attempts.append({"step": "unwrap_object.params", "ok": True})
            return normalize_send_raw_tx_params(source)
        options = source.get("meta") if isinstance(source.get("meta"), dict) else {}
        attempts.append({"step": "extract_dict", "ok": tx_value is not None, "key": key_used})
    else:
        tx_value = source
        attempts.append({"step": "direct_value", "ok": True})

    if isinstance(tx_value, list) and all(isinstance(x, int) and 0 <= x <= 255 for x in tx_value):
        raw_bytes = bytes(tx_value)
        attempts.append({"step": "bytes_like_list", "ok": True, "size": len(raw_bytes)})
        meta.update({"key": key_used, "size_bytes": len(raw_bytes), "meta": options})
        return raw_bytes, meta

    if isinstance(tx_value, (bytes, bytearray)):
        raw_bytes = bytes(tx_value)
        attempts.append({"step": "bytes_direct", "ok": True, "size": len(raw_bytes)})
        meta.update({"key": key_used, "size_bytes": len(raw_bytes), "meta": options})
        return raw_bytes, meta

    if not isinstance(tx_value, str):
        attempts.append({"step": "string_required", "ok": False, "reason": f"got {type(tx_value).__name__}"})
        raise _invalid(key_used or "tx", f"got {type(tx_value).__name__}", tx_value)

    s = tx_value.strip()
    if s.startswith("0x") or s.startswith("0X"):
        hex_value = s[2:]
        try:
            raw_bytes = bytes.fromhex(hex_value)
            attempts.append({"step": "decode_hex", "ok": True, "size": len(raw_bytes)})
            meta.update({"key": key_used, "has_0x": True, "size_bytes": len(raw_bytes), "rawTx_hex": s[:66], "meta": options})
            return raw_bytes, meta
        except Exception as exc:
            attempts.append({"step": "decode_hex", "ok": False, "reason": str(exc)})
    else:
        attempts.append({"step": "decode_hex", "ok": False, "reason": "missing 0x prefix"})

    try:
        raw_bytes = base64.b64decode(s, validate=True)
        if raw_bytes:
            attempts.append({"step": "decode_base64", "ok": True, "size": len(raw_bytes)})
            meta.update({"key": key_used, "encoding": "base64", "size_bytes": len(raw_bytes), "meta": options})
            return raw_bytes, meta
    except Exception as exc:
        attempts.append({"step": "decode_base64", "ok": False, "reason": str(exc)})

    raise _invalid(key_used or "tx", "unable to decode as hex or base64", tx_value)


def _jsonify(obj: t.Any) -> t.Any:
    if isinstance(obj, (bytes, bytearray)):
        return _hex(obj)
    if _dc.is_dataclass(obj):
        return _jsonify(_dcd(obj))
    if isinstance(obj, dict):
        return {k: _jsonify(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonify(x) for x in obj]
    return obj


def _prune_reorged_txs(now: float | None = None) -> None:
    if not _REORGED_TXS:
        return
    ttl = _REORGED_TXS_TTL_S
    if ttl <= 0:
        _REORGED_TXS.clear()
        return
    now = time.time() if now is None else now
    for h, ts in list(_REORGED_TXS.items()):
        if now - ts > ttl:
            _REORGED_TXS.pop(h, None)


def _record_reorged_txs(tx_hashes: t.Iterable[bytes | str]) -> None:
    now = time.time()
    _prune_reorged_txs(now=now)
    for h in tx_hashes:
        if isinstance(h, (bytes, bytearray)):
            hex_str = "0x" + bytes(h).hex()
        else:
            hex_str = str(h)
            if not hex_str.startswith("0x"):
                hex_str = "0x" + hex_str
        _REORGED_TXS[hex_str.lower()] = now


def _coerce_optional_tx_int(field_name: str, value: t.Any) -> int | None:
    if value is None:
        return None
    try:
        return _coerce_tx_int(field_name, value)
    except _TxNormalizationError as exc:
        details = exc.details or {}
        raise rpc_errors.InvalidTx(
            f"bad numeric field: {field_name}",
            data={
                "mempoolError": {
                    "reason": "bad_field_type",
                    "reason_code": "bad_field_type",
                    "message": str(exc),
                    "context": {
                        "field": details.get("field", field_name),
                        "received_type": details.get("received_type"),
                        "received_keys": details.get("received_keys"),
                    },
                }
            },
        ) from exc


def _error_data(kind: str, exc: BaseException, where: str, hint: str) -> dict:
    data: dict[str, t.Any] = {"kind": kind, "cause": str(exc), "where": where, "hint": hint}
    if _RPC_DEBUG:
        data["stack"] = "".join(traceback.format_exception(exc)).strip()
    return data


def _sync_gate_tx_submit() -> None:
    try:
        ctx = deps.get_ctx()
    except Exception:
        return

    svc = getattr(ctx, "p2p_service", None) or getattr(ctx, "core_p2p_service", None)
    if svc is None or not hasattr(svc, "sync_status_snapshot"):
        return

    try:
        # Check if the function accepts the refresh parameter
        accepts_refresh = _function_accepts_params(svc.sync_status_snapshot, ["refresh"])
        if accepts_refresh:
            snap = svc.sync_status_snapshot(refresh=True)
        else:
            snap = svc.sync_status_snapshot()
        status = snap.to_dict() if hasattr(snap, "to_dict") else t.cast(dict[str, t.Any], snap)
    except Exception:
        return

    allowed, info = assess_tx_submission_readiness(status)
    if allowed:
        return

    phase = info.get("phase") or ""
    head_height = int(info.get("head_height") or 0)
    best_header_height = int(info.get("best_header_height") or 0)
    max_allowed_behind = info.get("max_allowed_behind")
    
    # Generate appropriate error message based on height status
    if head_height > 0 and best_header_height > 0 and head_height < best_header_height:
        blocks_behind = best_header_height - head_height
        if isinstance(max_allowed_behind, int) and max_allowed_behind >= 0:
            msg = (
                "Node is too far behind network tip; transaction submission is unavailable "
                f"(behind by {blocks_behind} block{'s' if blocks_behind != 1 else ''}, "
                f"allowed lag is {max_allowed_behind})"
            )
            hint = (
                f"Wait until node is within {max_allowed_behind} block"
                f"{'s' if max_allowed_behind != 1 else ''} of height {best_header_height}, then retry."
            )
        else:
            msg = f"Node is not at highest height; transaction submission is unavailable (behind by {blocks_behind} block{'s' if blocks_behind != 1 else ''})"
            hint = f"Wait for node to sync to height {best_header_height} before resubmitting."
    else:
        msg = "Node is still syncing; transaction submission is unavailable"
        hint = "Wait for sync to reach synced state before resubmitting."
    
    raise rpc_errors.TemporarilyUnavailable(
        msg,
        phase=phase.lower() if phase else None,
        head_height=head_height,
        best_header_height=best_header_height,
        hint=hint,
    )


def _extract_sender_address(obj: dict) -> str | None:
    if _address_from_pubkey is None:
        return None
    sigs = obj.get("sigs")
    if not sigs or not isinstance(sigs, list) or len(sigs) == 0:
        return None
    sig = sigs[0]
    if not isinstance(sig, dict):
        return None

    alg_id = sig.get("alg") or sig.get("alg_id") or sig.get("algId") or sig.get("algID")
    pubkey = sig.get("pubkey") or sig.get("pub") or sig.get("pk") or sig.get("publicKey")

    if alg_id is None or pubkey is None:
        return None

    if isinstance(alg_id, str) and _ALG_ID is not None:
        try:
            if alg_id in _ALG_ID:
                alg_id = _ALG_ID[alg_id]
            else:
                alg_id = int(alg_id, 0)
        except Exception:
            return None

    if isinstance(pubkey, str):
        pubkey = _b(pubkey)

    try:
        return _address_from_pubkey(bytes(pubkey), int(alg_id))
    except Exception:
        return None


def _extract_nonce(obj: dict) -> int | None:
    if not isinstance(obj, dict):
        return None
    body = obj.get("body")
    if isinstance(body, dict) and "nonce" in body:
        try:
            return int(body.get("nonce"))
        except Exception:
            return None
    nested = obj.get("tx")
    if isinstance(nested, dict) and "nonce" in nested:
        try:
            return int(nested.get("nonce"))
        except Exception:
            return None
    if "nonce" in obj:
        try:
            return int(obj.get("nonce"))
        except Exception:
            return None
    return None


def _collect_sign_bytes(tx_like: t.Any, *, chain_id: int) -> list[tuple[str, bytes]]:
    candidates: list[tuple[str, bytes]] = []

    def _as_map(val: t.Any) -> dict[str, t.Any]:
        if _dc.is_dataclass(val):
            return _dcd(val)
        if isinstance(val, dict):
            return dict(val)
        if hasattr(val, "to_obj") and callable(getattr(val, "to_obj")):
            try:
                out = val.to_obj()
                if isinstance(out, dict):
                    return dict(out)
            except Exception:
                return {}
        if hasattr(val, "__dict__") and isinstance(getattr(val, "__dict__"), dict):
            return dict(getattr(val, "__dict__"))
        return {}

    tx_obj = _as_map(tx_like)
    ident = deps.get_chain_identity() if hasattr(deps, "get_chain_identity") else {}
    genesis_hex = ident.get("genesisHash") if isinstance(ident, dict) else None
    genesis = _b(genesis_hex) if isinstance(genesis_hex, str) and genesis_hex else b""
    network = str((ident or {}).get("network") or (ident or {}).get("name") or "unknown") if isinstance(ident, dict) else "unknown"

    if _tx_signing_preimage is not None:
        preimage = _tx_signing_preimage(
            tx_obj,
            chain_id=int(chain_id),
            genesis=genesis,
            network=network,
            domain=_DOMAIN_TX_SIGN_V1,
            message_type="tx",
        )
        candidates.append(("animica.tx.v1.preimage", preimage))
        return candidates

    canonical_body = _build_signable_tx_bytes(tx_like) if _build_signable_tx_bytes is not None else None
    if canonical_body is None:
        raise rpc_errors.InternalError("No canonical encoder for SignBytes")
    candidates.append(("legacy.body_cbor", canonical_body))
    return candidates


def _build_sig_env(alg_id: t.Any, sig: bytes, *, domain: str = "tx", prehash: str = "sha3-512"):
    from pq.py.sign import Signature  # type: ignore

    if _ALG_NAME is not None and isinstance(alg_id, int):
        alg_name = _ALG_NAME.get(alg_id, f"alg_0x{alg_id:02x}")
    else:
        alg_name = f"alg_0x{alg_id:02x}" if isinstance(alg_id, int) else str(alg_id)

    return (
        Signature(
            alg_id=alg_id,
            alg_name=alg_name,
            domain=domain or "tx",
            prehash=prehash or "sha3-512",
            sig=sig,
        ),
        alg_name,
    )


def _verify_pq_candidates(
    candidates: list[tuple[str, bytes]],
    sig_env,
    pub: bytes,
    *,
    chain_id: int,
    fork_id: int | None,
) -> tuple[bool, str | None, list[str]]:
    ok = False
    used_label: str | None = None
    verify_errors: list[str] = []

    for label, candidate in candidates:
        try:
            attempt_ok = _pq_verify.verify_detached(  # type: ignore[attr-defined]
                candidate, sig_env, pub, chain_id=chain_id, fork_id=fork_id
            )
        except Exception as verify_exc:  # pragma: no cover
            verify_errors.append(f"{label}: {verify_exc}")
            continue

        if attempt_ok:
            ok = True
            used_label = label
            break

    return ok, used_label, verify_errors


def _chain_id_required() -> int:
    if hasattr(deps, "get_chain_id"):
        try:
            return int(deps.get_chain_id())  # type: ignore[arg-type]
        except Exception:
            pass

    if hasattr(deps, "get_chain_params"):
        cp = deps.get_chain_params()  # type: ignore[attr-defined]
        cid = getattr(cp, "chain_id", getattr(cp, "chainId", None))
        if cid is not None:
            return int(cid)

    if hasattr(deps, "chain_id"):
        return int(getattr(deps, "chain_id"))  # type: ignore[attr-defined]

    if hasattr(deps, "config") and hasattr(deps.config, "chain_id"):
        return int(deps.config.chain_id)  # type: ignore[attr-defined]

    return 1


def _fork_id_required() -> int | None:
    if hasattr(deps, "get_chain_identity"):
        try:
            ident = deps.get_chain_identity()  # type: ignore[arg-type]
            fork_id = ident.get("forkId") if isinstance(ident, dict) else None
            if fork_id is not None:
                return int(fork_id)
        except Exception:
            pass
    return None


def _extract_sig(obj: dict) -> tuple[int, bytes, bytes, str, str]:
    sig = obj.get("sig") or obj.get("signature")
    if sig is None:
        sigs = obj.get("sigs")
        if isinstance(sigs, list) and len(sigs) > 0:
            sig = sigs[0]

    if not isinstance(sig, dict):
        raise rpc_errors.InvalidParams("Missing 'sig' object")

    alg_id = sig.get("algId") or sig.get("alg_id") or sig.get("alg") or sig.get("algID")
    if alg_id is None:
        raise rpc_errors.InvalidParams("Missing 'sig.algId'")

    if isinstance(alg_id, str) and _ALG_ID is not None:
        try:
            if alg_id in _ALG_ID:
                alg_id = _ALG_ID[alg_id]
            else:
                alg_id = int(alg_id, 0)
        except Exception:
            pass

    pub = sig.get("pubkey") or sig.get("pub") or sig.get("pk") or sig.get("publicKey")
    s = sig.get("sig") or sig.get("signature")
    if pub is None or s is None:
        raise rpc_errors.InvalidParams("Missing 'sig.pubkey' or 'sig.sig'")

    prehash = sig.get("prehash") or sig.get("preHash") or "sha3-512"
    if isinstance(prehash, (bytes, bytearray)):
        try:
            prehash = prehash.decode()
        except Exception:
            prehash = "sha3-512"
    if isinstance(prehash, str):
        prehash = prehash.lower()
    if prehash not in ("sha3-512", "sha3-256"):
        raise rpc_errors.BadSignature(
            "Unsupported prehash",
            **_error_data(
                "pq_verify",
                ValueError(f"Unsupported prehash: {prehash}"),
                "_extract_sig",
                "Use sha3-512 or sha3-256 prehash",
            ),
        )

    domain = sig.get("domain") or "tx"
    if isinstance(domain, (bytes, bytearray)):
        try:
            domain = domain.decode()
        except Exception:
            domain = "tx"

    pub_b = _b(pub) if isinstance(pub, str) else bytes(pub)
    sig_b = _b(s) if isinstance(s, str) else bytes(s)
    return (int(alg_id) if isinstance(alg_id, int) else alg_id, pub_b, sig_b, str(domain), str(prehash))


def _extract_chain_id(tx_like: t.Any, obj: dict) -> int:
    if hasattr(tx_like, "chain_id"):
        return int(tx_like.chain_id)
    if hasattr(tx_like, "chainId"):
        return int(tx_like.chainId)

    if "body" in obj and isinstance(obj["body"], dict):
        body_obj = obj["body"]
        cid = body_obj.get("chainId") or body_obj.get("chain_id")
        if cid is not None:
            return int(cid)

    cid = obj.get("chainId") or obj.get("chain_id")
    if cid is None and "tx" in obj and isinstance(obj["tx"], dict):
        tx_obj = obj["tx"]
        cid = tx_obj.get("chainId") or tx_obj.get("chain_id")

    if cid is None:
        raise rpc_errors.InvalidParams("Transaction missing chain_id")
    return int(cid)


def _validate_chain_id(obj: dict) -> int:
    want = _chain_id_required()
    try:
        cid = _extract_chain_id(obj, obj)
    except rpc_errors.InvalidParams:
        cid = None

    log.debug("ChainId validation: extracted=%s expected=%s keys=%s", cid, want, list(obj.keys()))
    if cid is None:
        raise rpc_errors.ChainIdMismatch(got=0, expected=want)
    if int(cid) != int(want):
        raise rpc_errors.ChainIdMismatch(got=int(cid), expected=int(want))
    return int(cid)


def _verify_pq_signature(tx_like: t.Any, obj: dict, *, chain_id: int) -> None:
    if _pq_verify is None:
        if _PQ_VERIFY_OPTIONAL:
            log.warning("PQ verification unavailable; skipping due to optional flags")
            return
        raise rpc_errors.InternalError(
            "PQ verification unavailable",
            **_error_data(
                "pq_verify",
                RuntimeError("Missing pq.py.verify backend (animica-pq not installed?)"),
                "_verify_pq_signature",
                "Install animica-pq or set ANIMICA_PQ_VERIFY_OPTIONAL=1 for dev",
            ),
        )

    alg_id, pub, sig, domain, prehash = _extract_sig(obj)
    sig_env, alg_name = _build_sig_env(alg_id, sig, domain=domain, prehash=prehash)

    ident = deps.get_chain_identity() if hasattr(deps, "get_chain_identity") else {}
    genesis_hex = ident.get("genesisHash") if isinstance(ident, dict) else None
    genesis = _b(genesis_hex) if isinstance(genesis_hex, str) and genesis_hex else b""
    network = str((ident or {}).get("network") or (ident or {}).get("name") or "unknown") if isinstance(ident, dict) else "unknown"

    verify_result = None
    from_addr: str | None = None
    if isinstance(obj, dict):
        tx_obj = obj.get("tx") if isinstance(obj.get("tx"), dict) else obj.get("body")
        if isinstance(tx_obj, dict):
            from_raw = tx_obj.get("from") or tx_obj.get("from_addr")
            if isinstance(from_raw, str) and from_raw:
                from_addr = from_raw
    if _pq_verify_tx is not None and _ChainContext is not None:
        ctx = _ChainContext(
            chain_id=int(chain_id),
            genesis_hash=genesis,
            network=network,
            fork_id=_fork_id_required(),
            domain=domain,
            prehash=prehash,
        )
        # Pass full tx object (not just body) to pq_verify_tx.
        # The _extract_body() function inside pq_verify_tx handles envelope extraction.
        # Previously: obj.get("body", obj) would extract only the body if envelope had "body" key,
        # causing mismatch with signing which passes full tx to pq_sign_tx.
        verify_result = _pq_verify_tx(
            obj,
            sig_env,
            pub,
            ctx,
            from_addr=from_addr,
        )
        ok = bool(verify_result.ok)
        used_label = "animica.tx.v1.preimage"
        verify_errors: list[str] = [] if ok else [verify_result.reason or "verification failed"]
    else:
        candidates = _collect_sign_bytes(obj, chain_id=chain_id)
        if not candidates:
            raise rpc_errors.InvalidTx("No sign-bytes candidates found for PQ verification")
        msg_label, msg = candidates[0]
        ok, used_label, verify_errors = _verify_pq_candidates(
            candidates, sig_env, pub, chain_id=chain_id, fork_id=_fork_id_required()
        )

    if _PQ_VERIFY_DEBUG:
        # Enhanced debug instrumentation (no secret leakage)
        pub_fp = "0x" + hashlib.sha3_256(pub).hexdigest()[:16]
        sig_fp = "0x" + hashlib.sha3_256(sig).hexdigest()[:16]
        sign_hash_val = verify_result.sign_hash_hex if verify_result is not None else "N/A"
        preimage_prefix = (verify_result.preimage_hex[:66] + "...") if verify_result is not None else "N/A"
        log.info(
            "PQ VERIFY DEBUG ok=%s alg=%s(id=%d) used=%s pub_len=%d pub_fp=%s sig_len=%d sig_fp=%s "
            "chain_id=%d genesis_len=%d network=%s domain=%s prehash=%s sign_hash=%s preimage_prefix=%s",
            ok,
            alg_name,
            alg_id,
            used_label,
            len(pub),
            pub_fp,
            len(sig),
            sig_fp,
            chain_id,
            len(genesis),
            network,
            domain,
            prehash,
            sign_hash_val,
            preimage_prefix,
        )
    if verify_errors:
        log.debug("PQ verify helper errors: %s", "; ".join(verify_errors))

    if not ok:
        pub_fp = verify_result.pub_fingerprint if verify_result is not None else "0x" + hashlib.sha3_256(pub).hexdigest()[:16]
        sign_hash = verify_result.sign_hash_hex if verify_result is not None else None
        err = _error_data(
            "pq_verify",
            ValueError("verification failed"),
            "_verify_pq_signature",
            "Ensure signature, prehash, and pubkey match the tx body",
        )
        err.update(
            {
                "scheme_id": alg_id,
                "pubkey_len": len(pub),
                "sig_len": len(sig),
                "prehash": prehash,
                "domain": domain,
                "chain_id": chain_id,
                "sign_hash": sign_hash,
                "pub_fingerprint": pub_fp,
            }
        )
        raise rpc_errors.BadSignature(
            "Invalid post-quantum signature: verification failed",
            **err,
        )


def _decode_tx(raw: bytes) -> tuple[t.Any, dict]:
    """
    Legacy single-decoder transaction decoder.
    
    This function is kept for backward compatibility and direct calls that don't
    need defensive decoding. New code should use _decode_tx_defensive() instead.
    """
    if _cbor_loads is None:
        raise rpc_errors.InternalError("CBOR decoder unavailable")
    obj = _cbor_loads(raw)

    if not isinstance(obj, dict):
        raise rpc_errors.InvalidTx(
            "CBOR did not decode to a Tx object",
            **_error_data(
                "decode",
                TypeError(f"Decoded type={type(obj).__name__}"),
                "_decode_tx",
                "Ensure rawTx is a CBOR map with body/sig keys",
            ),
        )

    if _normalize_tx_envelope is None:
        raise rpc_errors.InternalError("tx envelope normalization unavailable")
    try:
        normalized_env = _normalize_tx_envelope(obj)
    except Exception as exc:
        if _TxNormalizationError is not None and isinstance(exc, _TxNormalizationError):
            raise rpc_errors.InvalidTx(
                "Transaction envelope normalization failed",
                **_error_data(
                    exc.reason,
                    exc,
                    "_decode_tx.normalize",
                    "Ensure tx envelope has tx/body and sigs fields",
                ),
            ) from exc
        raise

    # NOTE: Do not re-introduce original "body" after normalization.
    # The normalized "tx" representation is canonical and required for
    # deterministic signing preimage reconstruction during verification.

    raw_canonical = normalized_env.get("raw") or raw
    tx_hash_hex = normalized_env.get("hash") or (_hex(_sha3_256(raw_canonical)) or "")

    if _Tx is not None:
        try:
            tx_payload = {"tx": normalized_env.get("tx"), "sigs": normalized_env.get("sigs", [])}
            if hasattr(_Tx, "from_obj"):
                tx = _Tx.from_obj(tx_payload)  # type: ignore[attr-defined]
            elif hasattr(_Tx, "from_dict"):
                tx = _Tx.from_dict(tx_payload)  # type: ignore[attr-defined]
            else:
                tx = _Tx(**tx_payload)  # type: ignore[call-arg]

            if hasattr(tx, "to_cbor"):
                raw_canonical = tx.to_cbor()
                tx_hash_hex = _hex(_sha3_256(raw_canonical)) or ""

            enriched_obj = dict(normalized_env)
            enriched_obj["hash"] = tx_hash_hex
            enriched_obj["raw"] = raw_canonical
            return tx, enriched_obj
        except Exception:
            pass

    enriched_obj = dict(normalized_env)
    # Keep normalized envelope canonical; avoid adding legacy "body".
    enriched_obj["hash"] = tx_hash_hex
    enriched_obj["raw"] = raw_canonical
    return enriched_obj, enriched_obj


def _try_alternative_decoders(raw: bytes) -> tuple[dict | None, list[tuple[str, str]]]:
    """
    Try alternative CBOR/JSON decoders when primary decoder fails.
    
    Returns:
        (decoded_obj, failure_reasons) where decoded_obj is None if all failed,
        and failure_reasons is a list of (decoder_name, error_message) tuples.
    """
    failure_reasons: list[tuple[str, str]] = []
    
    # Try cbor2 library
    if _cbor2_module is not None:
        try:
            obj = _cbor2_module.loads(raw)
            if isinstance(obj, dict):
                log.info("Transaction decoded successfully using cbor2 fallback decoder")
                TX_DECODER_FALLBACK.labels(fallback_decoder="cbor2").inc()
                TX_DECODER_SUCCESS.labels(decoder="cbor2").inc()
                return obj, failure_reasons
            failure_reasons.append(("cbor2", f"Decoded to {type(obj).__name__}, expected dict"))
        except Exception as e:
            failure_reasons.append(("cbor2", f"{type(e).__name__}: {str(e)}"))
    else:
        failure_reasons.append(("cbor2", "library not available"))
    
    # Try msgspec library
    if _msgspec_module is not None:
        try:
            obj = _msgspec_module.msgpack.decode(raw)
            if isinstance(obj, dict):
                log.info("Transaction decoded successfully using msgspec fallback decoder")
                TX_DECODER_FALLBACK.labels(fallback_decoder="msgspec").inc()
                TX_DECODER_SUCCESS.labels(decoder="msgspec").inc()
                return obj, failure_reasons
            failure_reasons.append(("msgspec", f"Decoded to {type(obj).__name__}, expected dict"))
        except Exception as e:
            failure_reasons.append(("msgspec", f"{type(e).__name__}: {str(e)}"))
    else:
        failure_reasons.append(("msgspec", "library not available"))
    
    # Try JSON as last resort (in case someone sent JSON instead of CBOR)
    if _json_module is not None:
        try:
            text = raw.decode('utf-8')
        except UnicodeDecodeError as e:
            failure_reasons.append(("json", f"not valid UTF-8: {str(e)}"))
        else:
            try:
                # Check if it looks like JSON (starts with '{' or '[' after stripping whitespace)
                stripped = text.lstrip()
                if stripped.startswith('{') or stripped.startswith('['):
                    obj = _json_module.loads(text)
                    if isinstance(obj, dict):
                        log.info("Transaction decoded successfully using JSON fallback decoder")
                        TX_DECODER_FALLBACK.labels(fallback_decoder="json").inc()
                        TX_DECODER_SUCCESS.labels(decoder="json").inc()
                        return obj, failure_reasons
                    failure_reasons.append(("json", f"Decoded to {type(obj).__name__}, expected dict"))
                else:
                    failure_reasons.append(("json", "data does not appear to be JSON"))
            except Exception as e:
                failure_reasons.append(("json", f"{type(e).__name__}: {str(e)}"))
    else:
        failure_reasons.append(("json", "library not available"))
    
    return None, failure_reasons


def _process_decoded_obj(obj: dict, raw: bytes) -> tuple[t.Any, dict]:
    """
    Process a decoded transaction object (from any decoder) into normalized form.
    
    This is the common path for all decoders after successful decoding.
    """
    if _normalize_tx_envelope is None:
        raise rpc_errors.InternalError("tx envelope normalization unavailable")
    
    try:
        normalized_env = _normalize_tx_envelope(obj)
    except Exception as exc:
        if _TxNormalizationError is not None and isinstance(exc, _TxNormalizationError):
            raise rpc_errors.InvalidTx(
                "Transaction envelope normalization failed",
                **_error_data(
                    exc.reason,
                    exc,
                    "_process_decoded_obj.normalize",
                    "Ensure tx envelope has tx/body and sigs fields",
                ),
            ) from exc
        raise
    
    # NOTE: Do not add legacy/original "body" into normalized envelopes.
    # Verification/signing preimage logic should consume canonical "tx" only.
    
    raw_canonical = normalized_env.get("raw") or raw
    tx_hash_hex = normalized_env.get("hash") or (_hex(_sha3_256(raw_canonical)) or "")
    
    if _Tx is not None:
        try:
            tx_payload = {"tx": normalized_env.get("tx"), "sigs": normalized_env.get("sigs", [])}
            if hasattr(_Tx, "from_obj"):
                tx = _Tx.from_obj(tx_payload)  # type: ignore[attr-defined]
            elif hasattr(_Tx, "from_dict"):
                tx = _Tx.from_dict(tx_payload)  # type: ignore[attr-defined]
            else:
                tx = _Tx(**tx_payload)  # type: ignore[call-arg]
            
            if hasattr(tx, "to_cbor"):
                raw_canonical = tx.to_cbor()
                tx_hash_hex = _hex(_sha3_256(raw_canonical)) or ""
            
            enriched_obj = dict(normalized_env)
            enriched_obj["hash"] = tx_hash_hex
            enriched_obj["raw"] = raw_canonical
            return tx, enriched_obj
        except Exception:
            pass
    
    enriched_obj = dict(normalized_env)
    enriched_obj["hash"] = tx_hash_hex
    enriched_obj["raw"] = raw_canonical
    return enriched_obj, enriched_obj


def _decode_tx_defensive(raw: bytes) -> tuple[t.Any, dict]:
    """
    Defensive transaction decoder that tries multiple decoding methods.
    
    This is the primary entry point for transaction decoding. It tries:
    1. Primary CBOR decoder (core.encoding.cbor)
    2. Alternative CBOR decoders (cbor2, msgspec)
    3. JSON fallback (if data appears to be JSON)
    
    If all methods fail, raises InvalidTx with details about all attempted methods.
    """
    all_failures: list[tuple[str, str]] = []
    
    # Try primary CBOR decoder first
    if _cbor_loads is not None:
        try:
            obj = _cbor_loads(raw)
            if not isinstance(obj, dict):
                all_failures.append((
                    "core.encoding.cbor (primary)",
                    f"Decoded to {type(obj).__name__}, expected dict"
                ))
            else:
                # Primary decoder succeeded, process the object
                TX_DECODER_SUCCESS.labels(decoder="primary").inc()
                return _process_decoded_obj(obj, raw)
        except Exception as e:
            all_failures.append((
                "core.encoding.cbor (primary)",
                f"{type(e).__name__}: {str(e)}"
            ))
    else:
        all_failures.append(("core.encoding.cbor (primary)", "decoder unavailable"))
    
    # Try alternative decoders
    log.info("Primary CBOR decoder failed, trying alternative decoders...")
    fallback_obj, fallback_failures = _try_alternative_decoders(raw)
    all_failures.extend(fallback_failures)
    
    if fallback_obj is not None:
        # One of the fallback decoders succeeded
        try:
            return _process_decoded_obj(fallback_obj, raw)
        except Exception as e:
            # Decoding succeeded but normalization failed
            all_failures.append((
                "normalization (after fallback decode)",
                f"{type(e).__name__}: {str(e)}"
            ))
    
    # All decoders failed - build comprehensive error message
    TX_DECODER_ALL_FAILED.inc()
    
    failure_summary = "\n".join([
        f"  - {decoder}: {reason}"
        for decoder, reason in all_failures
    ])
    
    hint_parts = [
        "Transaction could not be decoded by any available method.",
        "Attempted decoders and their failures:",
        failure_summary,
        "",
        "Ensure the transaction is properly encoded as CBOR map with structure: {body: {...}, sigs: [...]}"
    ]
    
    raise rpc_errors.InvalidTx(
        "Transaction decode failed after trying all available decoders",
        **_error_data(
            "decode_all_failed",
            ValueError(f"All {len(all_failures)} decoder(s) failed"),
            "_decode_tx_defensive",
            "\n".join(hint_parts),
        ),
    )


def _validate_sufficient_balance(obj: dict) -> None:
    tx_obj = obj.get("body", obj.get("tx", obj))

    sender_addr = _extract_sender_address(obj)
    if sender_addr is None:
        log.debug("_validate_sufficient_balance: cannot determine sender address, skipping")
        return

    try:
        value = _coerce_tx_int("value", tx_obj.get("value", 0) or 0)
        
        # Handle gasLimit - may be int or dict {"limit": int, "price": int}
        gas_limit_raw = tx_obj.get("gasLimit") or tx_obj.get("gas_limit") or tx_obj.get("gas") or 0
        if isinstance(gas_limit_raw, dict):
            # Extract 'limit' from fee quote dict format
            gas_limit = _coerce_tx_int("gasLimit", gas_limit_raw.get("limit", 0) or 0)
        else:
            gas_limit = _coerce_tx_int("gasLimit", gas_limit_raw or 0)
        
        # Handle maxFee - may be in gasLimit dict as "price"
        max_fee_raw = tx_obj.get("maxFee") or tx_obj.get("max_fee") or tx_obj.get("gasPrice") or tx_obj.get("gas_price")
        if max_fee_raw is None and isinstance(gas_limit_raw, dict):
            # If maxFee not set but gasLimit is a dict, try to get price from it
            max_fee_raw = gas_limit_raw.get("price")
        max_fee = _coerce_tx_int("maxFee", max_fee_raw or 0)
    except _TxNormalizationError as exc:
        details = exc.details if isinstance(getattr(exc, "details", None), dict) else {}
        raise rpc_errors.InvalidTx(
            "tx numeric field validation failed",
            data={
                "mempoolError": {
                    "code": 1004,
                    "reason": "bad_field_type",
                    "reason_code": str(details.get("reason_code") or "bad_field_type"),
                    "message": str(exc),
                    "context": details,
                }
            },
        ) from exc

    required = value + (gas_limit * max_fee)

    try:
        ctx = deps.get_ctx()
        state_db = getattr(ctx, "state_db", None)
        if state_db is None:
            log.debug("_validate_sufficient_balance: state_db not available, skipping")
            return
        if _parse_address is None:
            log.debug("_validate_sufficient_balance: parse_address not available, skipping")
            return

        try:
            sender_bytes = _parse_address(sender_addr)
        except Exception as e:
            log.debug("_validate_sufficient_balance: failed to parse sender %s: %s", sender_addr, e)
            return

        balance = None
        for method_name in ("get_balance", "read_balance", "balance_of"):
            if hasattr(state_db, method_name):
                try:
                    balance = int(getattr(state_db, method_name)(sender_bytes))
                    break
                except Exception:
                    continue

        if balance is None:
            log.debug("_validate_sufficient_balance: could not retrieve balance, skipping")
            return

        if balance < required:
            raise rpc_errors.InsufficientFunds(required=required, available=balance)
    except rpc_errors.InsufficientFunds:
        raise
    except Exception as e:
        log.debug("_validate_sufficient_balance: unexpected error, skipping: %s", e)
        return


def _pending_put(tx_hash_hex: str, raw: bytes) -> None:
    # Canonical path: if authoritative mempool service exists, do not shadow-write
    # into legacy fallback stores (avoids split-brain status visibility).
    if _get_mempool_service() is not None:
        return
    if _PEND is not None and hasattr(_PEND, "add_raw"):
        _PEND.add_raw(tx_hash_hex, raw)  # type: ignore[attr-defined]
        return
    if _PEND is not None and hasattr(_PEND, "add"):
        _PEND.add(tx_hash_hex, raw)  # type: ignore[attr-defined]
        return
    _FALLBACK_PENDING[tx_hash_hex] = raw
    _FALLBACK_PENDING_TS[tx_hash_hex] = time.time()


def _pending_get(tx_hash_hex: str) -> bytes | None:
    # When canonical mempool is available, only trust that source.
    if _get_mempool_service() is not None:
        return _mempool_get_raw(tx_hash_hex)
    if _PEND is not None and hasattr(_PEND, "get_raw"):
        return _PEND.get_raw(tx_hash_hex)  # type: ignore[attr-defined]
    if _PEND is not None and hasattr(_PEND, "get"):
        return _PEND.get(tx_hash_hex)  # type: ignore[attr-defined]
    raw = _FALLBACK_PENDING.get(tx_hash_hex)
    if raw is not None:
        return raw
    return _mempool_get_raw(tx_hash_hex)


def _instant_receipt(tx_hash_hex: str) -> dict | None:
    try:
        svc = get_instant_tx_service_singleton()
    except Exception:
        svc = None
    if svc is None:
        return None
    try:
        return svc.get_receipt(tx_hash_hex)
    except Exception:
        return None


def _ensure_tx_persisted_to_chain(tx_hash_hex: str) -> tuple[bool, str | None]:
    if not _TX_SEND_FORCE_CHAIN:
        return False, None

    view, *_ = _lookup_persisted_tx(tx_hash_hex)
    if view is not None:
        return True, None

    min_spacing_s = miner_methods._min_block_spacing_s()
    if min_spacing_s > 0:
        head_ts = miner_methods._head_timestamp_seconds()
        if head_ts is not None:
            now = time.time()
            earliest = head_ts + min_spacing_s
            if now < earliest:
                wait_s = max(0.0, earliest - now)
                return False, f"min_block_spacing_wait:{wait_s:.3f}s"

    # Mine a block to persist the transaction
    # Use instant_block=True to ensure zero rewards for tx send blocks
    try:
        miner_methods.miner_mine(
            count=1,
            include_mempool=True,
            allow_offline_mining=True,
            allow_unsynced_mining=True,
            instant_block=True,
        )
    except Exception as exc:
        return False, str(exc)

    deadline = time.time() + max(0.0, _TX_SEND_FORCE_CHAIN_TIMEOUT_S)
    while time.time() <= deadline:
        view, *_ = _lookup_persisted_tx(tx_hash_hex)
        if view is not None:
            return True, None
        time.sleep(0.1)

    return False, None


def _force_sync_before_tx_submit() -> None:
    try:
        ctx = deps.get_ctx()
    except Exception:
        ctx = None

    svc = None
    if ctx is not None:
        svc = getattr(ctx, "p2p_service", None) or getattr(ctx, "core_p2p_service", None)

    if svc is not None and hasattr(svc, "sync_status_snapshot"):
        try:
            # Check if the function accepts the refresh parameter
            accepts_refresh = _function_accepts_params(svc.sync_status_snapshot, ["refresh"])
            if accepts_refresh:
                snap = svc.sync_status_snapshot(refresh=True)
            else:
                snap = svc.sync_status_snapshot()
            status = snap.to_dict() if hasattr(snap, "to_dict") else t.cast(dict[str, t.Any], snap)
            allowed, _info = assess_tx_submission_readiness(status)
            if allowed:
                return
        except Exception:
            pass

    try:
        from rpc.methods import sync as sync_methods
    except Exception:
        return

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    try:
        if loop is not None and loop.is_running():
            asyncio.ensure_future(sync_methods.sync_force(), loop=loop)
        else:
            asyncio.run(sync_methods.sync_force())
    except Exception:
        return


def _mempool_get_raw(tx_hash_hex: str) -> bytes | None:
    svc = _get_mempool_service()
    if svc is None:
        return None
    getter = getattr(svc, "get_raw", None)
    if callable(getter):
        try:
            raw = getter(tx_hash_hex)
        except Exception:
            return None
        if isinstance(raw, (bytes, bytearray)):
            return bytes(raw)
    snapshot = getattr(svc, "snapshot", None)
    if callable(snapshot):
        try:
            snap = snapshot(limit=1000)
        except Exception:
            return None
        raw_map = getattr(snap, "raw_by_hash", None)
        if isinstance(raw_map, dict):
            raw = raw_map.get(tx_hash_hex)
            if isinstance(raw, (bytes, bytearray)):
                return bytes(raw)
    return None


def _pending_remove(tx_hash_hex: str) -> bool:
    if _PEND is not None and hasattr(_PEND, "remove"):
        try:
            res = _PEND.remove(tx_hash_hex)  # type: ignore[attr-defined]
            if asyncio.iscoroutine(res):
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    return bool(asyncio.run(res))
                else:
                    loop.create_task(res)
                    return True
            return bool(res)
        except Exception:
            return False
    removed = _FALLBACK_PENDING.pop(tx_hash_hex, None) is not None
    _FALLBACK_PENDING_TS.pop(tx_hash_hex, None)
    return removed


def _pending_nonce_for_sender(svc: t.Any, sender_value: t.Any) -> int | None:
    if sender_value is None or _parse_address is None:
        return None
    sender_bytes: bytes | None = None
    if isinstance(sender_value, (bytes, bytearray)):
        sender_bytes = bytes(sender_value)
    elif isinstance(sender_value, str):
        try:
            sender_bytes = _parse_address(sender_value)
        except Exception:
            sender_bytes = None
    if sender_bytes is None:
        return None
    try:
        confirmed = None
        if hasattr(svc, "_confirmed_nonce"):
            confirmed = svc._confirmed_nonce(sender_bytes)  # type: ignore[attr-defined]
        if hasattr(svc, "get_next_nonce"):
            return int(svc.get_next_nonce(sender_bytes, int(confirmed or 0)))
    except Exception:
        return None
    return None


def _get_mempool_service():
    """
    Return the authoritative mempool service from the live node context.

    This MUST match what:
      - miner block-template builder uses
      - mempool.* RPC methods use

    We therefore probe multiple likely attribute names and wrappers.
    """
    ctx = None
    try:
        ctx = deps.get_ctx()
    except Exception:
        ctx = None

    # Common names across refactors
    candidates = [
        "mempool",
        "mempool_service",
        "mempoolSvc",
        "mempool_manager",
        "mempool_mgr",
        "txpool",
        "tx_pool",
        "pool",
    ]

    if ctx is not None:
        for name in candidates:
            svc = getattr(ctx, name, None)
            if svc is None:
                continue
            # Some contexts store a wrapper with .mempool or .service
            if hasattr(svc, "submit") or hasattr(svc, "admit") or hasattr(svc, "add_raw") or hasattr(svc, "has_hash"):
                return svc
            inner = getattr(svc, "mempool", None) or getattr(svc, "service", None)
            if inner is not None:
                if hasattr(inner, "submit") or hasattr(inner, "admit") or hasattr(inner, "add_raw") or hasattr(inner, "has_hash"):
                    return inner

    try:
        from rpc.mempool_service import get_mempool_service_singleton
    except Exception:
        return None
    return get_mempool_service_singleton()


def _mempool_has(svc: t.Any, tx_hash_hex: str) -> bool | None:
    try_methods = ["has_hash", "has", "contains", "__contains__"]
    for m in try_methods:
        if hasattr(svc, m):
            try:
                fn = getattr(svc, m)
                if m == "__contains__":
                    return bool(tx_hash_hex in svc)
                res = fn(tx_hash_hex)
                return bool(res)
            except Exception:
                continue
    return None


def _mempool_size(svc: t.Any) -> int | None:
    if hasattr(svc, "stats"):
        try:
            stats = svc.stats()
            if hasattr(stats, "total_txs"):
                return int(stats.total_txs)
            if isinstance(stats, dict):
                total = stats.get("total_txs") or stats.get("totalTxs")
                if total is not None:
                    return int(total)
        except Exception:
            pass
    if hasattr(svc, "snapshot"):
        try:
            snap = svc.snapshot()
            if hasattr(snap, "entries"):
                return len(snap.entries)
            if isinstance(snap, dict):
                total = snap.get("total_txs") or snap.get("totalTxs")
                if total is not None:
                    return int(total)
        except Exception:
            pass
    try:
        return len(svc)
    except Exception:
        return None


def _tx_reject_category(reason: str | None) -> str:
    if not reason:
        return "UNKNOWN"
    r = str(reason).lower()
    if "chain_id" in r:
        return "CHAIN_ID"
    if "verify" in r or "sig" in r:
        return "BAD_SIG"
    if "not_yet_valid" in r or "valid_after" in r:
        return "NOT_YET_VALID"
    if "expired" in r or "valid_until" in r:
        return "EXPIRED"
    if "nonce" in r:
        return "NONCE"
    if "replacement" in r:
        return "REPLACEMENT"
    if "replay" in r or "already_seen" in r:
        return "REPLAY"
    if "insufficient_funds_pending" in r or "pending" in r:
        return "INSUFFICIENT_FUNDS_PENDING"
    if "balance" in r or "insufficient" in r:
        return "INSUFFICIENT_BALANCE"
    if "gas" in r:
        return "BAD_GAS"
    if "fee" in r:
        return "POLICY"
    if "duplicate" in r:
        return "DUPLICATE"
    return "POLICY"


def _function_accepts_params(func: t.Any, param_names: list[str]) -> bool:
    """
    Check if a function accepts ALL of the given parameter names.
    
    Returns True if the function signature explicitly includes all parameter names
    or accepts **kwargs. Returns False if the signature is missing any of the
    parameters or cannot be inspected.
    
    Note: This requires ALL parameters in param_names to be present (not just any).
    This is appropriate for checking optional parameter sets that should be
    supported together (e.g., both 'local' and 'origin_peer' in mempool methods).
    """
    try:
        sig = inspect.signature(func)
        params = sig.parameters
        
        # If function has **kwargs, it accepts any parameter
        for param in params.values():
            if param.kind == inspect.Parameter.VAR_KEYWORD:
                return True
        
        # Check if all required param names are in the signature
        param_set = set(params.keys())
        return all(name in param_set for name in param_names)
    except (ValueError, TypeError):
        # If we can't inspect the signature, assume it doesn't accept the params
        # This is conservative and will fall back to the simpler call
        return False


def _mempool_submit(
    svc: t.Any,
    *,
    tx_obj: t.Any,
    raw: bytes,
    tx_hash_hex: str,
    local: bool | None = None,
    origin_peer: str | None = None,
) -> None:
    """
    Admit tx into the mempool using whatever method this mempool exposes.
    """
    if hasattr(svc, "submit"):
        kwargs: dict[str, t.Any] = {"tx": tx_obj, "raw": raw, "tx_hash_hex": tx_hash_hex}
        # Check if the function accepts the optional parameters.
        # We check for both params together because mempool methods either support
        # the full extended signature (local + origin_peer) or the basic signature.
        # There's no intermediate case in the codebase where only one is supported.
        accepts_extended = _function_accepts_params(svc.submit, ["local", "origin_peer"])
        if accepts_extended:
            if local is not None:
                kwargs["local"] = local
            if origin_peer is not None:
                kwargs["origin_peer"] = origin_peer
        try:
            svc.submit(**kwargs)
        except Exception as exc:
            if PersistenceFailed is not None and isinstance(exc, PersistenceFailed):
                path_attempted = None
                if hasattr(svc, "persistence_status"):
                    try:
                        pstat = svc.persistence_status()
                        err = pstat.get("error") if isinstance(pstat, dict) else None
                        path_attempted = err.get("pathAttempted") if isinstance(err, dict) else None
                    except Exception:
                        path_attempted = None
                raise rpc_errors.ServerError(
                    "Mempool service unavailable",
                    data={
                        "kind": "mempool_unavailable",
                        "pathAttempted": path_attempted,
                        "errno": getattr(exc, "context", {}).get("errno") if hasattr(exc, "context") else None,
                        "suggestion": "Configure ANIMICA_DATA_DIR to a writable mount or disable ANIMICA_MEMPOOL_REQUIRE_PERSIST",
                    },
                ) from exc
            raise
        return
    if hasattr(svc, "submit_atomic"):
        accepted, reject, _hash_hex = svc.submit_atomic(
            tx=tx_obj,
            raw=raw,
            tx_hash_hex=tx_hash_hex,
            local=local if local is not None else True,
            origin_peer=origin_peer,
        )
        if not accepted:
            reject = reject or {
                "code": 2999,
                "reason": "internal_error",
                "reason_code": "internal_error",
                "message": "mempool admission failed",
                "hint": "run animica node logs --network <network>",
                "context": {"tx_hash": _hash_hex},
            }
            raise rpc_errors.InvalidTx(
                f"mempool admission failed: {reject.get('reason','admission_failed')}",
                data={"mempoolError": reject},
            )
        return
    if hasattr(svc, "admit"):
        # Check if the function accepts the optional parameters.
        # We check for both params together because mempool methods either support
        # the full extended signature (local + origin_peer) or the basic signature.
        # There's no intermediate case in the codebase where only one is supported.
        accepts_extended = _function_accepts_params(svc.admit, ["local", "origin_peer"])
        if accepts_extended:
            svc.admit(tx_obj, raw=raw, tx_hash_hex=tx_hash_hex, local=local, origin_peer=origin_peer)
        else:
            svc.admit(tx_obj, raw=raw, tx_hash_hex=tx_hash_hex)
        return
    if hasattr(svc, "add_raw"):
        svc.add_raw(tx_hash_hex, raw)
        return
    if hasattr(svc, "add"):
        # some pools want (hash, obj) or (obj) or (hash, raw)
        try:
            svc.add(tx_obj, raw=raw, tx_hash_hex=tx_hash_hex)
            return
        except Exception:
            pass
        try:
            svc.add(tx_hash_hex, raw)
            return
        except Exception:
            pass
        svc.add(tx_obj)
        return
    raise AttributeError("No supported mempool admission method found")


def _mempool_simulate_submit(
    svc: t.Any,
    *,
    tx_obj: t.Any,
    raw: bytes,
    tx_hash_hex: str,
) -> None:
    if hasattr(svc, "submit_atomic"):
        accepted, reject, _hash_hex = svc.submit_atomic(
            tx=tx_obj,
            raw=raw,
            tx_hash_hex=tx_hash_hex,
            local=True,
            simulate=True,
        )
        if not accepted:
            reject = reject or {
                "code": 2999,
                "reason": "internal_error",
                "reason_code": "internal_error",
                "message": "mempool admission failed",
                "hint": "run animica node logs --network <network>",
                "context": {"tx_hash": _hash_hex},
            }
            raise rpc_errors.InvalidTx(
                f"mempool admission failed: {reject.get('reason','admission_failed')}",
                data={"mempoolError": reject},
            )
        return
    raise rpc_errors.MethodNotFound("mempool simulation not supported by current mempool service")


def _gossip_tx_to_peers(raw_tx: bytes) -> None:
    try:
        ctx = deps.get_ctx()
        p2p_service = getattr(ctx, "p2p_service", None)
        core_p2p_service = getattr(ctx, "core_p2p_service", None)
        loop = None
        running_loop = None
        try:
            running_loop = asyncio.get_running_loop()
            loop = running_loop
        except RuntimeError:
            loop = getattr(p2p_service, "loop", None) if p2p_service is not None else None

        did_relay = False

        if p2p_service is not None and hasattr(p2p_service, "relay_tx") and callable(
            getattr(p2p_service, "relay_tx")
        ):
            if loop is not None and loop.is_running():
                try:
                    if running_loop is not None and loop is running_loop:
                        asyncio.ensure_future(p2p_service.relay_tx(raw_tx), loop=loop)  # type: ignore[call-arg]
                    else:
                        asyncio.run_coroutine_threadsafe(
                            p2p_service.relay_tx(raw_tx), loop
                        )
                except RuntimeError:
                    pass
                else:
                    did_relay = True
                    log.info(
                        "tx relay scheduled via p2p service",
                        extra={"tx_hash": _sha3_256(raw_tx).hex()},
                    )
            elif loop is None or not loop.is_running():
                try:
                    asyncio.run(p2p_service.relay_tx(raw_tx))
                except RuntimeError:
                    pass
                else:
                    did_relay = True
                    log.info(
                        "tx relay executed via p2p service (sync fallback)",
                        extra={"tx_hash": _sha3_256(raw_tx).hex()},
                    )

        handler = (
            getattr(p2p_service, "tx_relay_handler", None) if p2p_service is not None else None
        )
        if not did_relay and handler is not None and hasattr(handler, "publish_local_tx"):
            if loop is not None and loop.is_running():
                try:
                    if running_loop is not None and loop is running_loop:
                        asyncio.ensure_future(handler.publish_local_tx(raw_tx), loop=loop)
                    else:
                        asyncio.run_coroutine_threadsafe(
                            handler.publish_local_tx(raw_tx), loop
                        )
                except RuntimeError:
                    pass
                else:
                    did_relay = True
                    log.info(
                        "tx relay scheduled via tx handler",
                        extra={"tx_hash": _sha3_256(raw_tx).hex()},
                    )

        gossip = getattr(p2p_service, "gossip", None) if p2p_service is not None else None
        if not did_relay and gossip is not None and hasattr(gossip, "publish"):
            try:
                from p2p.gossip import topics as gossip_topics
                chain_id = _chain_id_required()
                topic_path = gossip_topics.txs(chain_id).path
            except Exception:
                topic_path = "txs"

            if loop is not None and loop.is_running():
                try:
                    if running_loop is not None and loop is running_loop:
                        asyncio.ensure_future(gossip.publish(topic_path, raw_tx), loop=loop)
                    else:
                        asyncio.run_coroutine_threadsafe(
                            gossip.publish(topic_path, raw_tx), loop
                        )
                except RuntimeError:
                    pass
                else:
                    did_relay = True
                    log.info(
                        "tx relay scheduled via gossip publish",
                        extra={"tx_hash": _sha3_256(raw_tx).hex()},
                    )

        if not did_relay and core_p2p_service is not None:
            core_loop = loop or getattr(core_p2p_service.connman, "loop", None)
            if core_loop is None or not core_loop.is_running():
                return
            try:
                tx_hash = _sha3_256(raw_tx)
                coro = core_p2p_service.net_processing.announce_tx(
                    core_p2p_service.connman.peers().values(),
                    tx_hash,
                    core_p2p_service.connman._send,
                )
                if running_loop is not None and core_loop is running_loop:
                    asyncio.ensure_future(coro, loop=core_loop)
                else:
                    asyncio.run_coroutine_threadsafe(coro, core_loop)
            except RuntimeError:
                return
    except Exception:
        return


def _lookup_persisted_tx(tx_hash_hex: str) -> tuple[dict | None, int | None, int | None, bytes | None]:
    ctx = deps.get_ctx()
    if hasattr(ctx, "block_db") and ctx.block_db is not None:
        block_db = ctx.block_db
        if hasattr(block_db, "get_transaction_by_hash"):
            try:
                tx_hash_bytes = _b(tx_hash_hex)
                result = block_db.get_transaction_by_hash(tx_hash_bytes)
                if result is not None:
                    height, idx, block_hash, tx_obj = result
                    obj = _dcd(tx_obj) if _dc.is_dataclass(tx_obj) else (dict(tx_obj) if isinstance(tx_obj, dict) else {})
                    view = _tx_view(tx_obj, obj, pending=False, block_hash=block_hash, block_number=height, tx_index=idx)
                    return view, height, idx, block_hash
            except Exception:
                pass
    # Fallback: tx index may know (height, index, block_hash) even if block_db has no direct tx-hash lookup.
    tidx = getattr(ctx, "tx_index", None)
    bdb = getattr(ctx, "block_db", None)
    if tidx is not None and bdb is not None:
        pointer = None
        for meth in ("get", "lookup", "get_loc"):
            if hasattr(tidx, meth):
                try:
                    pointer = getattr(tidx, meth)(_b(tx_hash_hex))  # type: ignore[misc]
                    if pointer is not None:
                        break
                except Exception:
                    continue
        if pointer is not None:
            height: int | None = None
            idx: int | None = None
            block_hash: bytes | None = None
            if isinstance(pointer, (tuple, list)) and len(pointer) >= 2:
                try:
                    height = int(pointer[0])
                    idx = int(pointer[1])
                    if len(pointer) >= 3 and isinstance(pointer[2], (bytes, bytearray)):
                        block_hash = bytes(pointer[2])
                except Exception:
                    height = None
                    idx = None
            elif isinstance(pointer, dict):
                try:
                    height = int(pointer.get("height")) if pointer.get("height") is not None else None
                    idx = int(pointer.get("index")) if pointer.get("index") is not None else None
                    bh = pointer.get("block_hash")
                    if isinstance(bh, (bytes, bytearray)):
                        block_hash = bytes(bh)
                except Exception:
                    height = None
                    idx = None
            else:
                try:
                    height_v = getattr(pointer, "height", None)
                    idx_v = getattr(pointer, "index", None)
                    if height_v is not None and idx_v is not None:
                        height = int(height_v)
                        idx = int(idx_v)
                    bh = getattr(pointer, "block_hash", None)
                    if isinstance(bh, (bytes, bytearray)):
                        block_hash = bytes(bh)
                except Exception:
                    height = None
                    idx = None

            if height is not None and idx is not None:
                blk = None
                try:
                    if block_hash is not None and hasattr(bdb, "get_block_by_hash"):
                        blk = bdb.get_block_by_hash(block_hash)  # type: ignore[attr-defined]
                    if blk is None and hasattr(bdb, "get_block_by_height"):
                        blk = bdb.get_block_by_height(height)  # type: ignore[attr-defined]
                        if blk is not None and hasattr(getattr(blk, "header", None), "hash"):
                            block_hash = getattr(blk.header, "hash")()
                except Exception:
                    blk = None

                tx_obj = None
                if blk is not None:
                    txs = getattr(blk, "txs", None)
                    if isinstance(txs, (list, tuple)) and 0 <= idx < len(txs):
                        tx_obj = txs[idx]
                if tx_obj is not None:
                    obj = _dcd(tx_obj) if _dc.is_dataclass(tx_obj) else (dict(tx_obj) if isinstance(tx_obj, dict) else {})
                    view = _tx_view(tx_obj, obj, pending=False, block_hash=block_hash, block_number=height, tx_index=idx)
                    return view, height, idx, block_hash

    # Final fallback: scan recent canonical blocks for tx hash when tx_index/block_db hash lookup misses.
    # This keeps tx.getStatus aligned with canonical chain state even when auxiliary indexes lag.
    if bdb is not None:
        target = _b(tx_hash_hex)
        scan_depth = int(os.getenv("ANIMICA_TX_STATUS_SCAN_DEPTH", "4096") or 4096)
        head_height: int | None = None
        try:
            getter = getattr(ctx, "get_head", None)
            if callable(getter):
                head = getter()
                if isinstance(head, dict):
                    head_height = int(head.get("height") or 0)
        except Exception:
            head_height = None
        if head_height is None:
            try:
                if hasattr(bdb, "get_height"):
                    head_height = int(bdb.get_height())  # type: ignore[attr-defined]
            except Exception:
                head_height = None
        if head_height is not None and head_height >= 0:
            lower = max(0, int(head_height) - max(1, scan_depth) + 1)

            def _tx_hash_bytes_from_obj(tx_obj: t.Any) -> bytes | None:
                if isinstance(tx_obj, dict):
                    h = tx_obj.get("hash")
                    if isinstance(h, str) and h:
                        if h.startswith("0x"):
                            h = h[2:]
                        try:
                            return bytes.fromhex(h)
                        except Exception:
                            return None
                    if isinstance(h, (bytes, bytearray)):
                        return bytes(h)
                txid_fn = getattr(tx_obj, "txid", None)
                if callable(txid_fn):
                    try:
                        hv = txid_fn()
                        if isinstance(hv, (bytes, bytearray)):
                            return bytes(hv)
                    except Exception:
                        pass
                h_attr = getattr(tx_obj, "hash", None)
                if callable(h_attr):
                    try:
                        hv = h_attr()
                        if isinstance(hv, (bytes, bytearray)):
                            return bytes(hv)
                    except Exception:
                        pass
                elif isinstance(h_attr, (bytes, bytearray)):
                    return bytes(h_attr)
                elif isinstance(h_attr, str):
                    raw = h_attr[2:] if h_attr.startswith("0x") else h_attr
                    try:
                        return bytes.fromhex(raw)
                    except Exception:
                        return None
                return None

            for h in range(int(head_height), lower - 1, -1):
                blk = None
                try:
                    if hasattr(bdb, "get_block_by_height"):
                        blk = bdb.get_block_by_height(h)  # type: ignore[attr-defined]
                    elif hasattr(bdb, "get_block"):
                        blk = bdb.get_block(h)  # type: ignore[attr-defined]
                except Exception:
                    blk = None
                if blk is None:
                    continue
                txs = getattr(blk, "txs", None)
                if txs is None and isinstance(blk, dict):
                    txs = blk.get("txs") or blk.get("transactions")
                if not isinstance(txs, (list, tuple)):
                    continue
                for idx, tx_obj in enumerate(txs):
                    tx_hash_b = _tx_hash_bytes_from_obj(tx_obj)
                    if tx_hash_b != target:
                        continue
                    block_hash: bytes | None = None
                    try:
                        header = getattr(blk, "header", None)
                        h_fn = getattr(header, "hash", None)
                        if callable(h_fn):
                            hv = h_fn()
                            if isinstance(hv, (bytes, bytearray)):
                                block_hash = bytes(hv)
                    except Exception:
                        block_hash = None
                    if block_hash is None and isinstance(blk, dict):
                        hv = blk.get("hash")
                        if isinstance(hv, str):
                            raw = hv[2:] if hv.startswith("0x") else hv
                            try:
                                block_hash = bytes.fromhex(raw)
                            except Exception:
                                block_hash = None
                        elif isinstance(hv, (bytes, bytearray)):
                            block_hash = bytes(hv)
                    obj = _dcd(tx_obj) if _dc.is_dataclass(tx_obj) else (dict(tx_obj) if isinstance(tx_obj, dict) else {})
                    view = _tx_view(
                        tx_obj,
                        obj,
                        pending=False,
                        block_hash=block_hash,
                        block_number=h,
                        tx_index=idx,
                    )
                    return view, h, idx, block_hash
    return None, None, None, None


def _lookup_deploy_metadata(tx_hash_hex: str) -> dict | None:
    try:
        tx_hash_bytes = _b(tx_hash_hex)
    except Exception:
        return None
    ctx = deps.get_ctx()
    bdb = getattr(ctx, "block_db", None)
    if bdb is None:
        return None
    getter = getattr(bdb, "get_deploy_metadata_by_tx_hash", None)
    if not callable(getter):
        return None
    try:
        out = getter(tx_hash_bytes)
    except Exception:
        return None
    return out if isinstance(out, dict) else None


def _tx_view(
    tx: t.Any,
    obj: dict,
    *,
    pending: bool,
    block_hash: bytes | None = None,
    block_number: int | None = None,
    tx_index: int | None = None,
) -> dict:
    if isinstance(obj, dict):
        if "body" in obj:
            tx_obj = obj["body"]
        elif "tx" in obj:
            tx_obj = obj["tx"]
        elif "unsigned" in obj:
            # Persisted Tx dataclasses decode (via dataclasses.asdict) to
            # {"unsigned": {...}, "sigs": [...]} rather than the wire envelope.
            tx_obj = obj["unsigned"]
        else:
            tx_obj = obj
    else:
        tx_obj = obj

    _from = _extract_sender_address(obj)
    if _from is None:
        _from = tx_obj.get("from") or tx_obj.get("sender") or getattr(tx, "sender", None)

    to = tx_obj.get("to") or getattr(tx, "to", None)
    valid_after = tx_obj.get("validAfter") or tx_obj.get("valid_after") or getattr(tx, "valid_after", None)
    valid_until = tx_obj.get("validUntil") or tx_obj.get("valid_until") or getattr(tx, "valid_until", None)
    salt = tx_obj.get("salt") or getattr(tx, "salt", None)
    fork_id = tx_obj.get("forkId") or tx_obj.get("fork_id") or getattr(tx, "fork_id", None)

    gas_obj = tx_obj.get("gas")
    if isinstance(gas_obj, dict):
        gas = gas_obj.get("limit")
        tip = gas_obj.get("price")
    else:
        gas = tx_obj.get("gasLimit") or tx_obj.get("gas_limit") or tx_obj.get("gas")
        tip = tx_obj.get("tip") or tx_obj.get("gasPrice") or tx_obj.get("gas_price")

    max_fee = tx_obj.get("maxFee") or tx_obj.get("max_fee") or tip
    chain_id = tx_obj.get("chainId") or tx_obj.get("chain_id")

    payload_obj = tx_obj.get("payload")
    if isinstance(payload_obj, dict):
        # Wire envelope nests the payload under "v" ({"t": kind, "v": {...}});
        # persisted dataclass dicts hold the fields flat ({to, amount, data}).
        payload_v = payload_obj.get("v")
        if not isinstance(payload_v, dict):
            payload_v = payload_obj
        value = payload_v.get("amount", payload_v.get("value", 0))
        data = payload_v.get("data")
        if to is None:
            to = payload_v.get("to")
    else:
        value = tx_obj.get("value")
        data = tx_obj.get("data")

    if hasattr(tx, "txid") and callable(getattr(tx, "txid")):
        hash_hex = _hex(tx.txid()) or ""
    else:
        hash_hex = obj.get("hash") if isinstance(obj, dict) else None
        if not hash_hex and _cbor_dumps is not None:
            try:
                hash_hex = _hex(_sha3_256(_cbor_dumps(obj))) or ""
            except Exception:
                hash_hex = ""

    v = {
        "hash": hash_hex,
        "from": _hex(_from) if isinstance(_from, (bytes, bytearray)) else _from,
        "to": _hex(to) if isinstance(to, (bytes, bytearray)) else to,
        "gas": _coerce_optional_tx_int("gasLimit", gas),
        "gasLimit": _coerce_optional_tx_int("gasLimit", gas),
        "tip": _coerce_optional_tx_int("tip", tip),
        "gasPrice": _coerce_optional_tx_int("gasPrice", tip),
        "maxFee": _coerce_optional_tx_int("maxFee", max_fee),
        "validAfter": _coerce_optional_tx_int("validAfter", valid_after),
        "validUntil": _coerce_optional_tx_int("validUntil", valid_until),
        "salt": _hex(salt) if isinstance(salt, (bytes, bytearray)) else salt,
        "forkId": _coerce_optional_tx_int("forkId", fork_id),
        "value": _coerce_optional_tx_int("value", value),
        "chainId": _coerce_optional_tx_int("chainId", chain_id),
        "data": _hex(data) if isinstance(data, (bytes, bytearray)) else data,
        "blockHash": None if pending else (_hex(block_hash) if isinstance(block_hash, (bytes, bytearray)) else block_hash),
        "blockNumber": None if pending else (_coerce_optional_tx_int("blockNumber", block_number)),
        "transactionIndex": None if pending else (_coerce_optional_tx_int("transactionIndex", tx_index)),
    }

    deploy_meta = None
    if not pending and isinstance(hash_hex, str) and hash_hex:
        deploy_meta = _lookup_deploy_metadata(hash_hex)
    if deploy_meta is None and not pending and callable(_build_pyvm_deploy_metadata):
        try:
            deploy_meta = _build_pyvm_deploy_metadata(
                tx,
                tx_hash=hash_hex,
                block_hash=block_hash,
                block_number=block_number,
                tx_index=tx_index,
                chain_id=v.get("chainId"),
            )
        except Exception:
            deploy_meta = None
    if deploy_meta is not None and callable(_copy_deploy_metadata_fields):
        try:
            _copy_deploy_metadata_fields(v, deploy_meta, include_created_alias=True)
        except Exception:
            pass

    return {k: vv for k, vv in v.items() if vv is not None}


# ——— Methods ———

@method(
    "tx.sendRawTransaction",
    desc=(
        "Submit a signed CBOR-encoded transaction. Param may be positional [rawTx] or named {rawTx/raw_tx/tx}. Returns tx hash.\n\n"
        "Envelope formats supported:\n"
        '  { "body": {...}, "sig": { "algId": <int>, "pubkey": <bytes>, "sig": <bytes> } }\n'
        '  { "body": {...}, "sigs": [{ "algId": ..., "pubkey": ..., "sig": ... }] }\n'
    ),
    aliases=("tx_sendRawTransaction",),
)
def tx_send_raw_transaction(
    rawTx: t.Any = None,
    raw_tx: t.Any = None,
    tx: t.Any = None,
    raw: t.Any = None,
    cbor: t.Any = None,
    txBytes: t.Any = None,
) -> t.Any:
    try:
        return _tx_send_raw_transaction(
            rawTx,
            raw_tx=raw_tx,
            tx=tx,
            raw_value=raw,
            cbor=cbor,
            txBytes=txBytes,
        )
    except rpc_errors.RpcError:
        raise
    except Exception as e:  # pragma: no cover
        raise rpc_errors.InvalidTx(
            "tx.sendRawTransaction failed",
            **_error_data("unknown", e, "tx.sendRawTransaction", "Enable ANIMICA_RPC_DEBUG=1 for stack trace"),
        ) from e




@method(
    "mempool.simulateAdmission",
    desc="Simulate mempool admission for a signed CBOR tx without insertion.",
)
def mempool_simulate_admission(rawTx: str) -> t.Any:
    return _tx_send_raw_transaction(rawTx, simulate=True)


@method("tx.sendHelp", desc="Return accepted parameter shapes and examples for tx send methods.")
def tx_send_help() -> dict[str, t.Any]:
    return {
        "method": "tx.sendRawTransaction",
        "aliases": ["tx.send", "tx.broadcast", "tx.submit", "sendRawTransaction", "eth_sendRawTransaction"],
        "accepted": {
            "object": [{"tx": "0xdeadbeef"}, {"rawTx": "base64..."}],
            "array": [["0xdeadbeef"], ["0xdeadbeef", {"broadcast": True}]],
            "string": ["0xdeadbeef", "base64..."],
        },
        "notes": [
            "Chain ID must be 1.",
            "PQ signatures are required and validated after param normalization.",
        ],
    }

def _tx_send_raw_transaction(
    rawTx: t.Any,
    *,
    simulate: bool = False,
    raw_tx: t.Any = None,
    tx: t.Any = None,
    raw_value: t.Any = None,
    cbor: t.Any = None,
    txBytes: t.Any = None,
) -> t.Any:
    start_s = time.time()
    trace_id = uuid.uuid4().hex
    tx_hash_hex = ""
    tx_view: dict[str, t.Any] = {}
    sender = None
    nonce = None
    fee = None
    gas_limit = None
    reason = None
    raw = b""
    tx_warnings: list[dict[str, t.Any]] = []

    if os.getenv("ANIMICA_DEBUG_TX", "0") == "1":
        try:
            log.info(json.dumps({"trace_id": trace_id, "event": "rpc_tx_submit_request", "method": "tx.sendRawTransaction", "raw_type": type(rawTx).__name__, "raw_len": len(rawTx) if isinstance(rawTx, str) else None}, sort_keys=True, separators=(",", ":")))
        except Exception:
            pass

    def _log_decision(decision: str, reason_value: str | None) -> None:
        latency_ms = int((time.time() - start_s) * 1000)
        log.info(
            "tx.sendRawTransaction",
            extra={
                "tx_hash": tx_hash_hex,
                "sender": sender,
                "nonce": nonce,
                "fee": fee,
                "gas_limit": gas_limit,
                "decision": decision,
                "reason": reason_value,
                "latency_ms": latency_ms,
            },
        )
    def _format_send_result(
        *,
        tx_hash: str,
        accepted_to_mempool: bool,
        persisted_to_chain: bool,
        status: str | None = None,
        reason_value: str | None = None,
        existing_tx_hash: str | None = None,
        hint: str | None = None,
        warnings: list[dict[str, t.Any]] | None = None,
    ) -> t.Any:
        if (
            not _TX_SEND_FORCE_CHAIN
            and not status
            and not reason_value
            and persisted_to_chain
        ):
            return tx_hash
        if _TX_SEND_FORCE_CHAIN and persisted_to_chain and not status and not reason_value:
            return tx_hash
        payload = {
            "tx_hash": tx_hash,
            "hash": tx_hash,
            "accepted_to_mempool": accepted_to_mempool,
            "persisted_to_chain": persisted_to_chain,
        }
        if status:
            payload["status"] = status
        if reason_value:
            payload["reason"] = reason_value
        if existing_tx_hash:
            payload["existing_tx_hash"] = existing_tx_hash
        if hint:
            payload["hint"] = hint
        if warnings:
            payload.setdefault("context", {})
            payload["context"]["warnings"] = warnings
        return payload
    def _safe_hash_hex(raw_bytes: bytes) -> str:
        try:
            return _tx_hash_hex(raw_bytes)
        except Exception:
            return _hex(_sha3_256(raw_bytes)) or ""

    try:
        raw, param_meta = normalize_send_raw_tx_params(
            None,
            rawTx=rawTx,
            raw_tx=raw_tx,
            tx=tx,
            raw=raw_value,
            cbor=cbor,
            txBytes=txBytes,
        )
    except rpc_errors.RpcError:
        raise
    except Exception as exc:
        raise rpc_errors.InvalidParams("expected params [\"0x..\"] or {rawTx:\"0x..\"}") from exc

    log.info(
        "tx.sendRawTransaction.params",
        extra={
            "shape": param_meta.get("shape"),
            "key": param_meta.get("key"),
            "has_0x": param_meta.get("has_0x"),
            "size_bytes": param_meta.get("size_bytes"),
        },
    )

    try:
        try:
            tx_like, obj = _decode_tx_defensive(raw)
            if os.getenv("ANIMICA_DEBUG_TX", "0") == "1":
                try:
                    body = obj.get("tx") if isinstance(obj, dict) else None
                    type_map = {}
                    if isinstance(body, dict):
                        for fld in ("chainId", "nonce", "validAfter", "validUntil"):
                            if fld in body:
                                type_map[fld] = type(body.get(fld)).__name__
                        gas = body.get("gas")
                        if isinstance(gas, dict):
                            for fld in ("price", "limit"):
                                if fld in gas:
                                    type_map[f"gas.{fld}"] = type(gas.get(fld)).__name__
                        payload_v = (body.get("payload") or {}).get("v") if isinstance(body.get("payload"), dict) else None
                        if isinstance(payload_v, dict) and "amount" in payload_v:
                            type_map["payload.v.amount"] = type(payload_v.get("amount")).__name__
                    log.info(json.dumps({"trace_id": trace_id, "event": "rpc_tx_decoded", "tx_hash": obj.get("hash") if isinstance(obj, dict) else None, "numeric_types": type_map}, sort_keys=True, separators=(",", ":")))
                except Exception:
                    pass
        except rpc_errors.RpcError:
            raise
        except Exception as e:
            TX_VALIDATION_FAILURES.labels(reason="decode_failed").inc()
            raise rpc_errors.InvalidTx("rawTx decode failed", hint="Ensure rawTx is CBOR {body, sig}") from e

        # chainId and PQ verify
        if isinstance(obj, dict):
            tx_payload_key = "tx" if isinstance(obj.get("tx"), dict) else "body" if isinstance(obj.get("body"), dict) else None
            if tx_payload_key is not None:
                try:
                    tx_norm, tx_warnings = _normalize_tx_fields(obj[tx_payload_key])
                    obj[tx_payload_key] = tx_norm
                except _TxNormalizationError as exc:
                    raise rpc_errors.InvalidTx(
                        "tx numeric field validation failed",
                        data={
                            "mempoolError": {
                                "code": 1004,
                                "reason": str(getattr(exc, "reason", None) or "bad_field_type"),
                                "reason_code": str(getattr(exc, "reason", None) or "bad_field_type"),
                                "message": str(exc),
                                "context": getattr(exc, "details", {}) or {},
                            }
                        },
                    ) from exc

        tx_view = _tx_view(tx_like, obj, pending=True)
        sender = tx_view.get("from")
        nonce = _extract_nonce(obj) if isinstance(obj, dict) else None
        gas_limit = tx_view.get("gasLimit") or tx_view.get("gas")
        fee = tx_view.get("maxFee") or tx_view.get("gasPrice") or tx_view.get("tip")

        try:
            chain_id = _validate_chain_id(obj)
        except Exception as exc:
            log.info(
                "TX_VALIDATE_REJECT",
                extra={
                    "hash": _safe_hash_hex(raw),
                    "reason": _tx_reject_category(f"chain_id:{exc}"),
                    "detail": str(exc),
                    "sender": tx_view.get("from"),
                    "gas": tx_view.get("gas"),
                },
            )
            raise
        try:
            _verify_pq_signature(tx_like, obj, chain_id=chain_id)
        except Exception as exc:
            log.info(
                "TX_VALIDATE_REJECT",
                extra={
                    "hash": _safe_hash_hex(raw),
                    "reason": _tx_reject_category(f"verify:{exc}"),
                    "detail": str(exc),
                    "sender": tx_view.get("from"),
                    "gas": tx_view.get("gas"),
                },
            )
            raise

        raw_canonical = raw
        if isinstance(obj, dict):
            raw_from_obj = obj.get("raw")
            if isinstance(raw_from_obj, (bytes, bytearray)):
                raw_canonical = bytes(raw_from_obj)
        if _normalize_tx_bytes is not None:
            try:
                raw_canonical = _normalize_tx_bytes(raw_canonical)
            except Exception:
                raw_canonical = bytes(raw_canonical)

        tx_hash_hex = _tx_hash_hex(raw_canonical)
        if not tx_hash_hex:
            raise rpc_errors.InternalError("Failed to compute tx hash")

        _force_sync_before_tx_submit()
        _sync_gate_tx_submit()

        # optional balance check
        _validate_sufficient_balance(obj)

        # duplicates: if already pending or persisted, return (idempotent)
        svc = _get_mempool_service()
        if svc is not None:
            has0 = _mempool_has(svc, tx_hash_hex)
            if has0 is True:
                persisted, *_ = _lookup_persisted_tx(tx_hash_hex)
                _log_decision("accepted", "already_known")
                return _format_send_result(
                    tx_hash=tx_hash_hex,
                    accepted_to_mempool=True,
                    persisted_to_chain=bool(persisted),
                    status="already_known",
                    hint=(
                        "Mine a block or wait for miners"
                        if _TX_SEND_FORCE_CHAIN and not persisted
                        else None
                    ),
                )
        persisted, *_ = _lookup_persisted_tx(tx_hash_hex)
        if persisted is not None:
            _log_decision("accepted", None)
            return tx_hash_hex

        if svc is not None:
            tx_index = getattr(svc, "tx_index", None)
            if tx_index is not None and hasattr(tx_index, "exists"):
                try:
                    if tx_index.exists(_b(tx_hash_hex)):
                        _log_decision("accepted", None)
                        return tx_hash_hex
                except Exception:
                    pass

        # Build tx object if possible
        tx_obj = tx_like
        if _Tx is not None and not isinstance(tx_like, _Tx) and isinstance(obj, dict):
            try:
                if hasattr(_Tx, "from_obj"):
                    tx_obj = _Tx.from_obj(obj)  # type: ignore[attr-defined]
            except Exception:
                tx_obj = tx_like

        # ===== CRITICAL FIX =====
        # We will NOT "accept but not mine". If mempool is missing, error.
        if svc is None:
            raise rpc_errors.ServerError(
                "Mempool service unavailable",
                data={
                    "kind": "mempool_unavailable",
                    "pathAttempted": None,
                    "errno": None,
                    "suggestion": "Check ANIMICA_DATA_DIR and writable mount for mempool persistence",
                    "tx_hash": tx_hash_hex,
                },
            )

        # Admit to mempool using robust method probing
        pending_nonce_before = _pending_nonce_for_sender(svc, sender or tx_view.get("from"))
        try:
            _mempool_submit(svc, tx_obj=tx_obj, raw=raw_canonical, tx_hash_hex=tx_hash_hex, local=True) if not simulate else _mempool_simulate_submit(svc, tx_obj=tx_obj, raw=raw_canonical, tx_hash_hex=tx_hash_hex)
        except Exception as exc:
            if ReplacementUnsupported is not None and isinstance(exc, ReplacementUnsupported):
                existing = exc.context.get("existing_tx_hash") if hasattr(exc, "context") else None
                hint = (
                    "A same-sender nonce conflict already exists in canonical mempool. "
                    "Drop or wait for the existing tx before resubmitting."
                )
                raise rpc_errors.InvalidTx(
                    "mempool admission failed: replacement_unsupported",
                    data={
                        "mempoolError": {
                            "code": 1042,
                            "reason": "replacement_unsupported",
                            "reason_code": "replacement_unsupported",
                            "message": "same-sender nonce conflict in canonical mempool",
                            "hint": hint,
                            "context": {
                                "tx_hash": tx_hash_hex,
                                "existing_tx_hash": existing,
                            },
                        }
                    },
                ) from exc
            raise
        pending_nonce_after = _pending_nonce_for_sender(svc, sender or tx_view.get("from"))
        log.info(
            "tx.mempool_insert",
            extra={
                "tx_hash": tx_hash_hex,
                "sender": sender or tx_view.get("from"),
                "nonce": nonce,
                "ok": True,
                "pending_nonce_before": pending_nonce_before,
                "pending_nonce_after": pending_nonce_after,
            },
        )
    except rpc_errors.RpcError as exc:
        reason = getattr(exc, "message", str(exc))
        if int(getattr(exc, "code", 0)) == -32603 and "mempool" in str(reason).lower():
            raise rpc_errors.ServerError(
                "Mempool service unavailable",
                data={
                    "kind": "mempool_unavailable",
                    "pathAttempted": None,
                    "errno": None,
                    "suggestion": "Check ANIMICA_DATA_DIR and writable mount for mempool persistence",
                    "cause": str(exc),
                },
            ) from exc
        log.info(
            "TX_VALIDATE_REJECT",
            extra={
                "hash": tx_hash_hex or _safe_hash_hex(raw) or "",
                "reason": _tx_reject_category(reason),
                "detail": str(exc),
                "sender": sender or tx_view.get("from"),
                "gas": tx_view.get("gas"),
            },
        )
        _log_decision("rejected", reason)
        raise
    except Exception as exc:
        reason = str(exc)
        log.info(
            "TX_VALIDATE_REJECT",
            extra={
                "hash": tx_hash_hex or _safe_hash_hex(raw) or "",
                "reason": _tx_reject_category(reason),
                "detail": str(exc),
                "sender": sender or tx_view.get("from"),
                "gas": tx_view.get("gas"),
            },
        )
        log.warning(
            "Mempool admission rejected",
            extra={
                "tx_hash": tx_hash_hex,
                "error": str(exc),
            },
        )
        log.info(
            "tx.rejected",
            extra={"hash": tx_hash_hex, "reason": str(exc)},
        )
        log.info(
            "TX_MEMPOOL_REJECTED",
            extra={"hash": tx_hash_hex, "origin": "local", "reason": str(exc)},
        )
        _log_decision("rejected", reason)
        if isinstance(exc, rpc_errors.RpcError):
            raise
        if MempoolError is not None and isinstance(exc, MempoolError):
            raise rpc_errors.to_error(exc) from exc
        short_trace = "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__, limit=5)
        ).strip()
        log.error(
            "Mempool admission unexpected error",
            extra={
                "tx_hash": tx_hash_hex,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "traceback": short_trace,
            },
        )
        log.error(
            "Mempool admission internal error trace_id=%s",
            trace_id,
            extra={"tx_hash": tx_hash_hex, "trace_id": trace_id},
            exc_info=True,
        )
        # Surface as a mempool admission failure (so CLI sees a real error)
        raise rpc_errors.InvalidTx(
            "mempool admission failed: internal_error",
            data={
                "mempoolError": {
                    "code": 2999,
                    "reason": "internal_error",
                    "reason_code": "internal_error",
                    "message": "mempool admission failed",
                    "error_class": type(exc).__name__,
                    "hint": "run animica node logs --network <network>",
                    "context": {
                        "tx_hash": tx_hash_hex,
                        "error_class": type(exc).__name__,
                        "error_message": str(exc),
                        "trace_id": trace_id,
                    },
                }
            },
        ) from exc
    log.info(
        "Mempool admission accepted",
        extra={"tx_hash": tx_hash_hex},
    )
    if simulate:
        out = {"tx_hash": tx_hash_hex, "accepted_to_mempool": False, "simulated": True, "status": "would_admit"}
        if tx_warnings:
            out["context"] = {"warnings": tx_warnings}
        return out
    tx_view = _tx_view(tx_obj, obj, pending=True)
    mempool_size = _mempool_size(svc)
    log.info(
        "TX_ACCEPTED",
        extra={
            "hash": tx_hash_hex,
            "origin": "local",
            "sender": tx_view.get("from"),
            "mempool_size": mempool_size,
        },
    )
    log.info(
        "tx.accepted_local",
        extra={"tx_hash": tx_hash_hex},
    )
    log.info(
        "tx.mempool_added",
        extra={"tx_hash": tx_hash_hex},
    )
    log.info(
        "TX_MEMPOOL_ADDED",
        extra={
            "hash": tx_hash_hex,
            "origin": "local",
            "mempool_size": mempool_size,
        },
    )

    try:
        # Post-submit verification MUST pass
        has1 = _mempool_has(svc, tx_hash_hex)
        if has1 is not True:
            # Do not lie: return error instead of “accepted”
            raise rpc_errors.InternalError(
                "Transaction submitted but not present in mempool",
                data={
                    "tx_hash": tx_hash_hex,
                    "hint": "Mempool submit returned without persisting; check admission path and has_hash implementation",
                },
            )

        # Notify WS hub (best-effort)
        try:
            if hasattr(deps, "ws_broadcast_pending"):
                deps.ws_broadcast_pending(tx_hash_hex, obj)  # type: ignore
        except Exception:
            pass

        # Gossip to P2P peers (best-effort)
        try:
            _gossip_tx_to_peers(raw_canonical)
        except Exception:
            pass

        # Status-store diagnostics (best-effort): this should reflect the same
        # canonical admission event, not a parallel acceptance path.
        status_row = _instant_receipt(tx_hash_hex)
        log.info(
            "tx.status_store_update",
            extra={
                "tx_hash": tx_hash_hex,
                "ok": bool(status_row is not None),
                "reason": (status_row or {}).get("reason") if isinstance(status_row, dict) else None,
            },
        )

        persisted, mine_error = _ensure_tx_persisted_to_chain(tx_hash_hex)
        if _TX_SEND_FORCE_CHAIN and not persisted:
            hint = "Mine a block or wait for miners"
            if mine_error:
                hint = f"{hint} (mining error: {mine_error})"
            return _format_send_result(
                tx_hash=tx_hash_hex,
                accepted_to_mempool=True,
                persisted_to_chain=False,
                status="accepted_to_mempool",
                reason_value="accepted_to_mempool",
                hint=hint,
            )
    except Exception as exc:
        reason = str(exc)
        _log_decision("rejected", reason)
        raise

    _log_decision("accepted", None)

    return tx_hash_hex


@method(
    "tx.decodeRawTransaction",
    desc="Decode a raw CBOR-encoded transaction without signature verification.",
    aliases=("tx_decodeRawTransaction",),
)
def tx_decode_raw_transaction(rawTx: str) -> dict:
    if not isinstance(rawTx, str):
        raise rpc_errors.InvalidParams("rawTx must be a hex string")
    if rawTx.startswith("0b:"):
        raise rpc_errors.InvalidParams("base64 not supported yet; send hex (0x…)")

    raw = _b(rawTx)
    tx_like, obj = _decode_tx_defensive(raw)

    decoded_obj = obj if isinstance(obj, dict) else _dcd(obj)
    return {
        "len": len(raw),
        "type": type(tx_like).__name__ if hasattr(type(tx_like), "__name__") else str(type(tx_like)),
        "tx": _jsonify(decoded_obj),
    }


@method(
    "tx.debugVerifyRawTransaction",
    desc="Decode and verify a raw PQ transaction without admitting it to the mempool.",
    aliases=("tx_debugVerifyRawTransaction",),
)
def tx_debug_verify_raw_transaction(rawTx: str) -> dict:
    if not isinstance(rawTx, str):
        raise rpc_errors.InvalidParams("rawTx must be a hex string")
    if rawTx.startswith("0b:"):
        raise rpc_errors.InvalidParams("base64 not supported yet; send hex (0x…)")

    if _pq_verify is None:
        raise rpc_errors.InternalError("PQ verification unavailable")

    raw = _b(rawTx)
    tx_like, obj = _decode_tx_defensive(raw)
    chain_id = _validate_chain_id(obj)

    alg_id, pub, sig, domain, prehash = _extract_sig(obj)
    candidate_source = obj
    if not (isinstance(candidate_source, dict) and isinstance(candidate_source.get("body"), dict)):
        candidate_source = tx_like
    candidates = _collect_sign_bytes(candidate_source, chain_id=chain_id)
    sig_env, alg_name = _build_sig_env(alg_id, sig, domain=domain, prehash=prehash)

    ok, used_label, verify_errors = _verify_pq_candidates(
        candidates, sig_env, pub, chain_id=chain_id, fork_id=_fork_id_required()
    )

    candidate_views = [
        {"label": lbl, "len": len(data), "prefix": data[:32].hex(), "sha3_256": _hex(_sha3_256(data))}
        for lbl, data in candidates
    ]

    return {
        "ok": ok,
        "chainId": chain_id,
        "algorithm": alg_name,
        "algId": sig_env.alg_id,
        "usedCandidate": used_label,
        "candidates": candidate_views,
        "errors": verify_errors,
        "domainTag": "ANIMICA_TXSIG_V1",
        "hashAlg": "sha3_256",
        "pubkeyFingerprint": "0x" + hashlib.sha3_256(pub).hexdigest()[:16],
    }


@method("tx.debugVerify", desc="Verify tx signature diagnostics by txid/rawTx or explicit tx+sig fields.")
def tx_debug_verify(target: t.Any) -> dict:
    if not _DEBUG_RPC:
        raise rpc_errors.MethodNotFound("Debug RPC is disabled (set ANIMICA_DEBUG_RPC=1)")
    if isinstance(target, dict):
        tx_body = target.get("tx") or target.get("body")
        pub = target.get("pubkey") or target.get("pk")
        sig = target.get("signature") or target.get("sig")
        scheme_id = target.get("scheme_id") or target.get("algId") or target.get("alg")
        if not isinstance(tx_body, dict) or not isinstance(pub, str) or not isinstance(sig, str) or scheme_id is None:
            raise rpc_errors.InvalidParams("dict target requires tx/body + pubkey + signature + scheme_id")
        chain_id = int(target.get("chain_id") or tx_body.get("chainId") or tx_body.get("chain_id") or _chain_id_required())

        tx_env = {
            "body": tx_body,
            "sig": {
                "algId": int(scheme_id),
                "pubkey": _b(pub),
                "sig": _b(sig),
                "domain": str(target.get("domain") or "tx"),
                "prehash": str(target.get("prehash") or "sha3-512"),
                "chainId": chain_id,
            },
        }
        try:
            _verify_pq_signature(tx_env, tx_env, chain_id=chain_id)
            return {"ok": True, "reason": None, "chain_id": chain_id}
        except rpc_errors.BadSignature as exc:
            data = getattr(exc, "data", {}) or {}
            return {"ok": False, "reason": str(exc), "chain_id": chain_id, "details": data}

    if isinstance(target, str) and target.startswith("0x") and len(target) == 66:
        raw = _mempool_get_raw(target.lower()) or _pending_get(target.lower())
        if raw is None:
            raise rpc_errors.InvalidParams("tx not found in mempool/pending cache")
        return tx_debug_verify_raw_transaction("0x" + raw.hex())
    return tx_debug_verify_raw_transaction(target)


@method("tx.debugSigningPreimage", desc="Return canonical tx signing preimage diagnostics.", aliases=("tx.debugSignBytes", "tx_debugSignBytes"))
def tx_debug_signing_preimage(txid: str) -> dict:
    if not _DEBUG_RPC:
        raise rpc_errors.MethodNotFound("Debug RPC is disabled (set ANIMICA_DEBUG_RPC=1)")
    if not isinstance(txid, str):
        raise rpc_errors.InvalidParams("txid must be hex string")
    tx_hash_hex = txid.lower()
    if not tx_hash_hex.startswith("0x"):
        tx_hash_hex = "0x" + tx_hash_hex

    raw = _mempool_get_raw(tx_hash_hex) or _pending_get(tx_hash_hex)
    if raw is None:
        raise rpc_errors.InvalidParams("tx not found in mempool/pending cache")

    tx_like, obj = _decode_tx_defensive(raw)
    chain_id = _extract_chain_id(tx_like, obj)
    candidates = _collect_sign_bytes(obj, chain_id=chain_id)
    if not candidates:
        raise rpc_errors.InvalidTx("no signing preimage candidates")
    label, preimage = candidates[0]

    ident = deps.get_chain_identity() if hasattr(deps, "get_chain_identity") else {}
    return {
        "txid": tx_hash_hex,
        "label": label,
        "preimageHex": "0x" + preimage.hex(),
        "domain": _DOMAIN_TX_SIGN_V1,
        "chainId": chain_id,
        "network": (ident or {}).get("network") if isinstance(ident, dict) else None,
        "genesisHash": (ident or {}).get("genesisHash") if isinstance(ident, dict) else None,
        "canonicalRules": {
            "encoding": "cbor-canonical",
            "definiteLengths": True,
            "mapKeyOrder": "deterministic",
            "excludeSignatureFields": True,
        },
    }


@method("tx.debugSignHash", desc="Compute canonical body/sign-bytes/sign-hash for tx body.")
def tx_debug_sign_hash(tx_body: dict) -> dict:
    if not _DEBUG_RPC:
        raise rpc_errors.MethodNotFound("Debug RPC is disabled (set ANIMICA_DEBUG_RPC=1)")
    if not isinstance(tx_body, dict):
        raise rpc_errors.InvalidParams("tx body json object required")
    chain_id = int(tx_body.get("chainId") or tx_body.get("chain_id") or _chain_id_required())

    body = dict(tx_body)
    if _normalize_tx_fields is not None:
        body = _normalize_tx_fields(body)

    ident = deps.get_chain_identity() if hasattr(deps, "get_chain_identity") else {}
    genesis_hex = ident.get("genesisHash") if isinstance(ident, dict) else None
    genesis = _b(genesis_hex) if isinstance(genesis_hex, str) and genesis_hex else b""
    network = str((ident or {}).get("network") or (ident or {}).get("name") or "unknown") if isinstance(ident, dict) else "unknown"

    if _tx_signing_preimage is None:
        raise rpc_errors.InternalError("tx signing preimage helper unavailable")
    sign_bytes = _tx_signing_preimage(body, chain_id=chain_id, genesis=genesis, network=network)
    sign_hash = hashlib.sha3_512(sign_bytes).digest()
    canonical_body = _cbor_dumps(body) if _cbor_dumps is not None else sign_bytes

    return {
        "canonical_body_hex": "0x" + bytes(canonical_body).hex(),
        "sign_bytes_hex": "0x" + sign_bytes.hex(),
        "sign_hash_hex": "0x" + sign_hash.hex(),
        "chain_id": chain_id,
        "domain": "tx",
        "prehash": "sha3-512",
    }


@method(
    "tx.explainNotIncluded",
    desc="Explain why a transaction is not included in a block yet.",
    aliases=("tx_explainNotIncluded",),
)
def tx_explain_not_included(tx_hash: str) -> dict:
    try:
        from rpc.methods.mempool import mempool_explain
    except Exception as exc:
        raise rpc_errors.InternalError(
            "mempool explain unavailable",
            data={"error": str(exc)},
        ) from exc
    return mempool_explain(tx_hash)


@method(
    "tx.getTransactionByHash",
    desc="Get a transaction by hash. Returns full object with pending/persisted context.",
    aliases=("tx_getTransactionByHash",),
)
def tx_get_transaction_by_hash(txHash: str) -> t.Optional[dict]:
    if not isinstance(txHash, str):
        raise rpc_errors.InvalidParams("txHash must be hex string")
    tx_hash_hex = txHash.lower()
    if not tx_hash_hex.startswith("0x"):
        tx_hash_hex = "0x" + tx_hash_hex

    raw = _mempool_get_raw(tx_hash_hex)
    if raw is not None:
        try:
            tx_like, obj = _decode_tx_defensive(raw)
            decoded_obj = obj if isinstance(obj, dict) else _dcd(obj)
            return _tx_view(tx_like, decoded_obj, pending=True)
        except Exception:
            pass

    raw = _pending_get(tx_hash_hex)
    if raw is not None and _cbor_loads is not None:
        try:
            obj = _cbor_loads(raw)
            tx_like = obj
            return _tx_view(tx_like, obj if isinstance(obj, dict) else _dcd(obj), pending=True)
        except Exception:
            pass

    view, *_etc = _lookup_persisted_tx(tx_hash_hex)
    if view is not None:
        return view

    return None


@method(
    "tx.getTransaction",
    desc="Get a transaction by hash (alias of tx.getTransactionByHash).",
    aliases=("tx_getTransaction",),
)
def tx_get_transaction(txHash: str) -> t.Optional[dict]:
    return tx_get_transaction_by_hash(txHash)


@method(
    "tx.getTransactionStatus",
    desc="Return transaction status (pending, confirmed, rejected, not_found).",
    aliases=("tx_getTransactionStatus",),
)
def tx_get_transaction_status(txHash: str) -> dict:
    if not isinstance(txHash, str):
        raise rpc_errors.InvalidParams("txHash must be hex string")
    tx_hash_hex = txHash.lower()
    if not tx_hash_hex.startswith("0x"):
        tx_hash_hex = "0x" + tx_hash_hex

    view, height, idx, block_hash = _lookup_persisted_tx(tx_hash_hex)
    if view is not None:
        return {
            "hash": tx_hash_hex,
            "status": "confirmed",
            "blockNumber": int(height) if height is not None else None,
            "blockHash": _hex(block_hash) if block_hash is not None else None,
            "transactionIndex": int(idx) if idx is not None else None,
        }

    svc = _get_mempool_service()
    if svc is not None:
        try:
            has = _mempool_has(svc, tx_hash_hex)
        except Exception:
            has = None
        if has:
            return {"hash": tx_hash_hex, "status": "pending"}

    if svc is not None:
        rejection = getattr(svc, "get_rejection", None)
        if callable(rejection):
            try:
                rejected = rejection(tx_hash_hex)
            except Exception:
                rejected = None
            if rejected:
                return {
                    "hash": tx_hash_hex,
                    "status": "rejected",
                    "reason": rejected.get("reason"),
                    "details": rejected.get("details"),
                }

    return {"hash": tx_hash_hex, "status": "not_found"}


@method(
    "tx.getStatus",
    desc=(
        "Return detailed transaction status (mempool presence, inclusion, confirmations, reorg state)."
    ),
    aliases=("tx_getStatus", "tx.status", "tx_getStatusDetail"),
)
def tx_get_status(txHash: str) -> dict:
    if not isinstance(txHash, str):
        raise rpc_errors.InvalidParams("txHash must be hex string")
    tx_hash_hex = txHash.lower()
    if not tx_hash_hex.startswith("0x"):
        tx_hash_hex = "0x" + tx_hash_hex

    _prune_reorged_txs()

    seen_in_mempool = False
    svc = _get_mempool_service()
    if svc is not None:
        try:
            seen_in_mempool = bool(_mempool_has(svc, tx_hash_hex))
        except Exception:
            seen_in_mempool = False
    # Only consult legacy fallback cache when canonical mempool service is absent.
    if svc is None and not seen_in_mempool and _pending_get(tx_hash_hex) is not None:
        seen_in_mempool = True

    included_height = None
    included_hash = None
    view, height, _idx, block_hash = _lookup_persisted_tx(tx_hash_hex)
    if view is not None:
        included_height = int(height) if height is not None else None
        included_hash = _hex(block_hash) if block_hash is not None else None

    head_height = None
    try:
        head = deps.ensure_started().get_head()
        if isinstance(head, dict):
            head_height = int(head.get("height") or 0)
    except Exception:
        head_height = None

    confirmations = None
    if included_height is not None and head_height is not None:
        if head_height >= included_height:
            confirmations = int(head_height - included_height + 1)
        else:
            confirmations = 0

    finality = int(os.environ.get("ANIMICA_TX_FINALITY_CONFIRMATIONS", "12") or 12)
    finalized = bool(confirmations is not None and confirmations >= finality)

    reorged_out = False
    if included_hash is None and tx_hash_hex in _REORGED_TXS:
        reorged_out = True

    rejected = None
    if svc is not None:
        rejection = getattr(svc, "get_rejection", None)
        if callable(rejection):
            try:
                rejected = rejection(tx_hash_hex)
            except Exception:
                rejected = None

    receipt = _instant_receipt(tx_hash_hex)
    receipt_reason = receipt.get("reason") if isinstance(receipt, dict) else None
    pending_receipt_reason = receipt_reason in {"accepted_to_mempool", "pending_mempool"}
    instant_confirmed = bool(
        receipt is not None
        and receipt.get("instant_confirmed")
        and not pending_receipt_reason
    )
    finalized_in_pow = bool(included_hash is not None) or bool(receipt is not None and receipt.get("finalized_in_pow"))

    # Canonical state machine:
    # pending_mempool -> included_block -> finalized
    # plus rejected/evicted/reorged_out side states.
    state = "not_found"
    if included_hash is not None:
        state = "finalized" if finalized else "included_block"
    elif reorged_out:
        state = "reorged_out"
    elif rejected is not None:
        state = "rejected"
    elif seen_in_mempool or pending_receipt_reason:
        state = "pending_mempool"
    elif receipt_reason in {"evicted", "evicted_from_mempool"}:
        state = "evicted"

    # Backward-compatible status mapping.
    status_map = {
        "pending_mempool": "pending",
        "included_block": "confirmed",
        "finalized": "finalized",
        "rejected": "rejected",
        "evicted": "evicted",
        "reorged_out": "reorged_out",
        "not_found": "not_found",
    }
    status = status_map.get(state, "not_found")
    reason = None
    if rejected is not None:
        reason = rejected.get("reason")
    elif receipt_reason:
        reason = receipt_reason

    return {
        "hash": tx_hash_hex,
        "status": status,
        "state": state,
        "seen_in_mempool": seen_in_mempool,
        "included_in_block_hash": included_hash,
        "included_height": included_height,
        "confirmations": confirmations,
        "finalized": finalized,
        "reorged_out": reorged_out,
        "instant_confirmed": instant_confirmed,
        "finalized_in_pow": finalized_in_pow,
        "reason": reason,
        "rejection_details": rejected.get("details") if isinstance(rejected, dict) else None,
    }

# NOTE: tx.getTransactionReceipt is in rpc/methods/receipt.py


@method(
    "tx.getInstantReceipt",
    desc="Return instant tx block receipt for a transaction hash.",
    aliases=("tx_getInstantReceipt",),
)
def tx_get_instant_receipt(txHash: str) -> dict:
    if not isinstance(txHash, str):
        raise rpc_errors.InvalidParams("txHash must be hex string")
    tx_hash_hex = txHash.lower()
    if not tx_hash_hex.startswith("0x"):
        tx_hash_hex = "0x" + tx_hash_hex
    receipt = _instant_receipt(tx_hash_hex)
    if receipt is None:
        return {"hash": tx_hash_hex, "status": "not_found"}
    return {
        "hash": tx_hash_hex,
        "status": "instant_confirmed" if receipt.get("instant_confirmed") else "known",
        "receipt": receipt,
    }
