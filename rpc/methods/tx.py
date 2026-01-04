from __future__ import annotations

import asyncio
import dataclasses as _dc
import logging
import os
import time
import traceback
import typing as t

from rpc import deps
from rpc import errors as rpc_errors
from rpc.methods import method
from rpc.methods import miner as miner_methods
from animica.sync.readiness import assess_tx_submission_readiness
from mempool.tx_hash import tx_hash_hex as _tx_hash_hex

log = logging.getLogger(__name__)
_PQ_VERIFY_DEBUG = os.environ.get("ANIMICA_PQ_VERIFY_DEBUG") == "1"
_PQ_VERIFY_OPTIONAL = os.environ.get("ANIMICA_PQ_VERIFY_OPTIONAL") == "1" or (
    os.environ.get("ANIMICA_SKIP_PQ_VERIFY") == "1"
)
_RPC_DEBUG = os.environ.get("ANIMICA_RPC_DEBUG") == "1"
_TX_SEND_FORCE_CHAIN = os.environ.get("ANIMICA_TX_SEND_FORCE_CHAIN", "1") == "1"
_TX_SEND_FORCE_CHAIN_TIMEOUT_S = float(os.environ.get("ANIMICA_TX_SEND_FORCE_CHAIN_TIMEOUT_S", "5") or 5)

# ——— Validation failure metrics ———
try:
    from rpc.metrics import TX_VALIDATION_FAILURES
except Exception:  # pragma: no cover
    class _Counter:
        def labels(self, **kwargs):
            return self

        def inc(self, *args, **kwargs):
            pass

    TX_VALIDATION_FAILURES = _Counter()  # type: ignore[assignment]

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
    from animica.tx.signing import build_signable_tx_bytes as _build_signable_tx_bytes
except Exception:  # pragma: no cover
    _build_signable_tx_bytes = None  # type: ignore

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
    from mempool.errors import MempoolError, ReplacementUnsupported  # type: ignore
except Exception:  # pragma: no cover
    MempoolError = None  # type: ignore
    ReplacementUnsupported = None  # type: ignore

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
        try:
            snap = svc.sync_status_snapshot(refresh=True)
        except TypeError:
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
    raise rpc_errors.TemporarilyUnavailable(
        "Node is still syncing; transaction submission is unavailable",
        phase=phase.lower() if phase else None,
        head_height=head_height,
        best_header_height=best_header_height,
        hint="Wait for sync to reach synced state before resubmitting.",
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


def _collect_sign_bytes(tx_like: t.Any) -> list[tuple[str, bytes]]:
    candidates: list[tuple[str, bytes]] = []
    errors: list[str] = []

    def _add(label: str, fn: t.Callable[[], bytes]) -> None:
        try:
            data = fn()
            if not isinstance(data, (bytes, bytearray)):
                return
            b = bytes(data)
            if all(existing != b for _, existing in candidates):
                candidates.append((label, b))
        except Exception as exc:  # pragma: no cover
            errors.append(f"{label}: {exc}")

    if _build_signable_tx_bytes is not None:
        _add("animica.tx.signing", lambda: _build_signable_tx_bytes(tx_like))
    if _tx_sign_bytes is not None:
        _add("core.encoding.canonical", lambda: _tx_sign_bytes(tx_like))
    if _sdk_sign_bytes is not None:
        _add("omni_sdk.tx.encode", lambda: _sdk_sign_bytes(tx_like))

    if _cbor_dumps is not None:

        def _fallback_body() -> bytes:
            if _dc.is_dataclass(tx_like):
                obj = _dcd(tx_like)
            else:
                obj = dict(tx_like)
            if "body" in obj:
                body = obj["body"]
            else:
                body = dict(obj)
                for k in ("sig", "signature", "sigs"):
                    body.pop(k, None)
            return _cbor_dumps(body)

        _add("local.cbor_fallback", _fallback_body)

    if not candidates:
        raise rpc_errors.InternalError("No canonical encoder for SignBytes (all helpers unavailable)")

    if errors:
        log.debug("SignBytes helper errors (ignored): %s", "; ".join(errors))

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
    candidates = _collect_sign_bytes(obj)
    if not candidates:
        raise rpc_errors.InvalidTx("No sign-bytes candidates found for PQ verification")
    msg_label, msg = candidates[0]

    sig_env, alg_name = _build_sig_env(alg_id, sig, domain=domain, prehash=prehash)

    ok, used_label, verify_errors = _verify_pq_candidates(
        candidates, sig_env, pub, chain_id=chain_id, fork_id=_fork_id_required()
    )

    if _PQ_VERIFY_DEBUG:
        log.info(
            "PQ VERIFY DEBUG ok=%s alg=%s used=%s primary=%s msg_len=%d pub_len=%d sig_len=%d chain_id=%d",
            ok,
            alg_name,
            used_label,
            msg_label,
            len(msg),
            len(pub),
            len(sig),
            chain_id,
        )
    if verify_errors:
        log.debug("PQ verify helper errors: %s", "; ".join(verify_errors))

    if not ok:
        raise rpc_errors.BadSignature(
            "Invalid post-quantum signature: verification failed",
            **_error_data(
                "pq_verify",
                ValueError("verification failed"),
                "_verify_pq_signature",
                "Ensure signature, prehash, and pubkey match the tx body",
            ),
        )


def _decode_tx(raw: bytes) -> tuple[t.Any, dict]:
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

    if isinstance(obj, dict):
        raw_body = obj.get("body")
        if isinstance(raw_body, dict):
            normalized_env.setdefault("body", raw_body)

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
    if isinstance(obj, dict) and "body" in obj and isinstance(obj.get("body"), dict):
        enriched_obj["body"] = obj["body"]
    enriched_obj["hash"] = tx_hash_hex
    enriched_obj["raw"] = raw_canonical
    return enriched_obj, enriched_obj


def _validate_sufficient_balance(obj: dict) -> None:
    tx_obj = obj.get("body", obj.get("tx", obj))

    sender_addr = _extract_sender_address(obj)
    if sender_addr is None:
        log.debug("_validate_sufficient_balance: cannot determine sender address, skipping")
        return

    value = int(tx_obj.get("value", 0) or 0)
    gas_limit = int((tx_obj.get("gasLimit") or tx_obj.get("gas_limit") or tx_obj.get("gas") or 0) or 0)
    max_fee = int((tx_obj.get("maxFee") or tx_obj.get("max_fee") or tx_obj.get("gasPrice") or tx_obj.get("gas_price") or 0) or 0)

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
    if _PEND is not None and hasattr(_PEND, "add_raw"):
        _PEND.add_raw(tx_hash_hex, raw)  # type: ignore[attr-defined]
        return
    if _PEND is not None and hasattr(_PEND, "add"):
        _PEND.add(tx_hash_hex, raw)  # type: ignore[attr-defined]
        return
    _FALLBACK_PENDING[tx_hash_hex] = raw
    _FALLBACK_PENDING_TS[tx_hash_hex] = time.time()


def _pending_get(tx_hash_hex: str) -> bytes | None:
    if _PEND is not None and hasattr(_PEND, "get_raw"):
        return _PEND.get_raw(tx_hash_hex)  # type: ignore[attr-defined]
    if _PEND is not None and hasattr(_PEND, "get"):
        return _PEND.get(tx_hash_hex)  # type: ignore[attr-defined]
    raw = _FALLBACK_PENDING.get(tx_hash_hex)
    if raw is not None:
        return raw
    return _mempool_get_raw(tx_hash_hex)


def _ensure_tx_persisted_to_chain(tx_hash_hex: str) -> tuple[bool, str | None]:
    if not _TX_SEND_FORCE_CHAIN:
        return False, None

    view, *_ = _lookup_persisted_tx(tx_hash_hex)
    if view is not None:
        return True, None

    try:
        miner_methods.miner_mine(
            count=1,
            include_mempool=True,
            allow_offline_mining=True,
            allow_unsynced_mining=True,
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
            try:
                snap = svc.sync_status_snapshot(refresh=True)
            except TypeError:
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
    return _FALLBACK_PENDING.pop(tx_hash_hex, None) is not None


def _get_mempool_service():
    """
    Return the authoritative mempool service from the live node context.

    This MUST match what:
      - miner block-template builder uses
      - mempool.* RPC methods use

    We therefore probe multiple likely attribute names and wrappers.
    """
    try:
        ctx = deps.get_ctx()
    except Exception:
        return None

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

    return None


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
        if local is not None:
            kwargs["local"] = local
        if origin_peer is not None:
            kwargs["origin_peer"] = origin_peer
        try:
            svc.submit(**kwargs)
            return
        except TypeError:
            svc.submit(tx=tx_obj, raw=raw, tx_hash_hex=tx_hash_hex)
            return
    if hasattr(svc, "submit_atomic"):
        accepted, reason, _hash_hex = svc.submit_atomic(
            tx=tx_obj,
            raw=raw,
            tx_hash_hex=tx_hash_hex,
            local=local if local is not None else True,
            origin_peer=origin_peer,
        )
        if not accepted:
            raise rpc_errors.InvalidTx(
                "mempool admission rejected",
                data={
                    "mempoolError": {
                        "code": 1000,
                        "reason": reason or "admission_failed",
                        "message": "mempool admission rejected",
                        "context": {"tx_hash": _hash_hex},
                    }
                },
            )
        return
    if hasattr(svc, "admit"):
        try:
            svc.admit(tx_obj, raw=raw, tx_hash_hex=tx_hash_hex, local=local, origin_peer=origin_peer)
        except TypeError:
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
    return None, None, None, None


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
        payload_v = payload_obj.get("v", {})
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
        "gas": int(gas) if gas is not None else None,
        "gasLimit": int(gas) if gas is not None else None,
        "tip": int(tip) if tip is not None else None,
        "gasPrice": int(tip) if tip is not None else None,
        "maxFee": int(max_fee) if max_fee is not None else None,
        "validAfter": int(valid_after) if valid_after is not None else None,
        "validUntil": int(valid_until) if valid_until is not None else None,
        "salt": _hex(salt) if isinstance(salt, (bytes, bytearray)) else salt,
        "forkId": int(fork_id) if fork_id is not None else None,
        "value": int(value) if value is not None else None,
        "chainId": int(chain_id) if chain_id is not None else None,
        "data": _hex(data) if isinstance(data, (bytes, bytearray)) else data,
        "blockHash": None if pending else (_hex(block_hash) if isinstance(block_hash, (bytes, bytearray)) else block_hash),
        "blockNumber": None if pending else (int(block_number) if block_number is not None else None),
        "transactionIndex": None if pending else (int(tx_index) if tx_index is not None else None),
    }
    return {k: vv for k, vv in v.items() if vv is not None}


# ——— Methods ———

@method(
    "tx.sendRawTransaction",
    desc=(
        "Submit a signed CBOR-encoded transaction. Param: rawTx (hex string '0x…'). Returns tx hash.\n\n"
        "Envelope formats supported:\n"
        '  { "body": {...}, "sig": { "algId": <int>, "pubkey": <bytes>, "sig": <bytes> } }\n'
        '  { "body": {...}, "sigs": [{ "algId": ..., "pubkey": ..., "sig": ... }] }\n'
    ),
    aliases=("tx_sendRawTransaction",),
)
def tx_send_raw_transaction(rawTx: str) -> t.Any:
    try:
        return _tx_send_raw_transaction(rawTx)
    except rpc_errors.RpcError:
        raise
    except Exception as e:  # pragma: no cover
        raise rpc_errors.InvalidTx(
            "tx.sendRawTransaction failed",
            **_error_data("unknown", e, "tx.sendRawTransaction", "Enable ANIMICA_RPC_DEBUG=1 for stack trace"),
        ) from e


def _tx_send_raw_transaction(rawTx: str) -> t.Any:
    start_s = time.time()
    tx_hash_hex = ""
    tx_view: dict[str, t.Any] = {}
    sender = None
    nonce = None
    fee = None
    gas_limit = None
    reason = None
    raw = b""

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
        return payload
    def _safe_hash_hex(raw_bytes: bytes) -> str:
        try:
            return _tx_hash_hex(raw_bytes)
        except Exception:
            return _hex(_sha3_256(raw_bytes)) or ""

    if not isinstance(rawTx, str):
        raise rpc_errors.InvalidParams("rawTx must be a hex string")
    if rawTx.startswith("0b:"):
        raise rpc_errors.InvalidParams("base64 not supported yet; send hex (0x…)")

    try:
        try:
            raw = _b(rawTx)
        except Exception as e:
            TX_VALIDATION_FAILURES.labels(reason="hex_decode_failed").inc()
            raise rpc_errors.InvalidTx(
                "rawTx decode failed",
                **_error_data("decode", e, "tx.sendRawTransaction._b", "Ensure rawTx is 0x-prefixed hex"),
            ) from e

        try:
            tx_like, obj = _decode_tx(raw)
        except rpc_errors.RpcError:
            raise
        except Exception as e:
            TX_VALIDATION_FAILURES.labels(reason="cbor_decode_failed").inc()
            raise rpc_errors.InvalidTx(
                "Transaction decode failed",
                **_error_data("decode", e, "_decode_tx", "Ensure rawTx is CBOR {body, sig}"),
            ) from e

        # chainId and PQ verify
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
            raise rpc_errors.InternalError(
                "Mempool service unavailable; tx cannot be admitted",
                data={
                    "tx_hash": tx_hash_hex,
                    "hint": "Node context is missing mempool service; fix ctx wiring or mempool init",
                },
            )

        # Admit to mempool using robust method probing
        try:
            _mempool_submit(svc, tx_obj=tx_obj, raw=raw_canonical, tx_hash_hex=tx_hash_hex)
        except Exception as exc:
            if ReplacementUnsupported is not None and isinstance(exc, ReplacementUnsupported):
                existing = exc.context.get("existing_tx_hash") if hasattr(exc, "context") else None
                hint = (
                    "Use animica mempool drop <tx_hash> to clear the existing transaction "
                    "before resubmitting, or wait for it to be mined."
                )
                _log_decision("accepted", "replacement_unsupported")
                return _format_send_result(
                    tx_hash=existing or tx_hash_hex,
                    accepted_to_mempool=True,
                    persisted_to_chain=False,
                    status="replacement_unsupported",
                    reason_value="replacement_unsupported",
                    existing_tx_hash=existing,
                    hint=hint,
                )
            raise
    except rpc_errors.RpcError as exc:
        reason = getattr(exc, "message", str(exc))
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
        # Surface as a mempool admission failure (so CLI sees a real error)
        raise rpc_errors.InvalidTx(
            "mempool admission failed",
            data={
                "mempoolError": {
                    "code": 1000,
                    "reason": "admission_failed",
                    "message": "mempool admission failed",
                    "context": {"tx_hash": tx_hash_hex},
                }
            },
        ) from exc
    log.info(
        "Mempool admission accepted",
        extra={"tx_hash": tx_hash_hex},
    )
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

        # Add to pending cache for tx.getTransactionByHash pending view (best-effort)
        try:
            _pending_put(tx_hash_hex, raw_canonical)
        except Exception:
            pass

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
    tx_like, obj = _decode_tx(raw)

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
    tx_like, obj = _decode_tx(raw)
    chain_id = _validate_chain_id(obj)

    alg_id, pub, sig, domain, prehash = _extract_sig(obj)
    candidate_source = obj
    if not (isinstance(candidate_source, dict) and isinstance(candidate_source.get("body"), dict)):
        candidate_source = tx_like
    candidates = _collect_sign_bytes(candidate_source)
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
    }


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
            tx_like, obj = _decode_tx(raw)
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

    svc = _get_mempool_service()
    if svc is not None:
        try:
            has = _mempool_has(svc, tx_hash_hex)
        except Exception:
            has = None
        if has:
            return {"hash": tx_hash_hex, "status": "pending"}

    if _pending_get(tx_hash_hex) is not None:
        return {"hash": tx_hash_hex, "status": "pending"}

    view, height, idx, block_hash = _lookup_persisted_tx(tx_hash_hex)
    if view is not None:
        return {
            "hash": tx_hash_hex,
            "status": "confirmed",
            "blockNumber": int(height) if height is not None else None,
            "blockHash": _hex(block_hash) if block_hash is not None else None,
            "transactionIndex": int(idx) if idx is not None else None,
        }

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
    if not seen_in_mempool and _pending_get(tx_hash_hex) is not None:
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

    if included_hash is not None:
        status = "confirmed"
    elif reorged_out:
        status = "reorged_out"
    elif seen_in_mempool:
        status = "pending"
    else:
        status = "not_found"

    return {
        "hash": tx_hash_hex,
        "status": status,
        "seen_in_mempool": seen_in_mempool,
        "included_in_block_hash": included_hash,
        "included_height": included_height,
        "confirmations": confirmations,
        "finalized": finalized,
        "reorged_out": reorged_out,
    }

# NOTE: tx.getTransactionReceipt is in rpc/methods/receipt.py
