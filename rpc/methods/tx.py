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

log = logging.getLogger(__name__)
_PQ_VERIFY_DEBUG = os.environ.get("ANIMICA_PQ_VERIFY_DEBUG") == "1"
_PQ_VERIFY_OPTIONAL = os.environ.get("ANIMICA_PQ_VERIFY_OPTIONAL") == "1" or (
    os.environ.get("ANIMICA_SKIP_PQ_VERIFY") == "1"
)
_RPC_DEBUG = os.environ.get("ANIMICA_RPC_DEBUG") == "1"

# ——— Validation failure metrics ———
try:
    from rpc.metrics import TX_VALIDATION_FAILURES
except Exception:  # pragma: no cover
    # Fallback: no-op counter
    class _Counter:
        def labels(self, **kwargs):
            """Return self to support chaining."""
            return self

        def inc(self, *args, **kwargs):
            pass

    TX_VALIDATION_FAILURES = _Counter()  # type: ignore[assignment]

# ——— Optional deps (be tolerant during early bring-up) ———

# CBOR codec (canonical, from core)
try:
    from core.encoding.cbor import dumps as _cbor_dumps
    from core.encoding.cbor import loads as _cbor_loads  # type: ignore
except Exception as _e:  # pragma: no cover
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

# Hashing
try:
    from core.utils.hash import sha3_256 as _sha3_256  # type: ignore
except Exception:  # pragma: no cover
    import hashlib

    def _sha3_256(b: bytes) -> bytes:  # type: ignore
        return hashlib.sha3_256(b).digest()


# Pending pool (strongly preferred)
_PEND = None
try:
    from rpc.pending_pool import pool as _PEND  # type: ignore
except Exception:  # pragma: no cover
    _PEND = None  # type: ignore

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
# Map tx_hash_hex → raw_tx_bytes
_FALLBACK_PENDING: dict[str, bytes] = {}
# Arrival timestamps for pending txs (for mempool stats)
_FALLBACK_PENDING_TS: dict[str, float] = {}


# ——— Helpers ———


def _dcd(obj: t.Any) -> t.Any:
    """Dataclass → dict (deep)."""
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


def _error_data(kind: str, exc: BaseException, where: str, hint: str) -> dict:
    data: dict[str, t.Any] = {
        "kind": kind,
        "cause": str(exc),
        "where": where,
        "hint": hint,
    }
    if _RPC_DEBUG:
        data["stack"] = "".join(traceback.format_exception(exc)).strip()
    return data


def _canonicalize_envelope_for_core(envelope: dict) -> dict:
    """
    Convert common RPC/CLI envelope shapes into the "core" shape most internal
    code expects: {"tx": {...}, "sigs": [{...}, ...]}.

    Also ensures tx.sender exists (copied from tx.from) and normalizes signature
    key names (pk→pubkey, alg→algId, signature→sig).
    """
    if not isinstance(envelope, dict):
        return {}

    env = dict(envelope)

    # Remove non-canonical keys we may have added for convenience
    env.pop("hash", None)
    env.pop("raw", None)

    # body -> tx
    if "tx" not in env and "body" in env and isinstance(env["body"], dict):
        env["tx"] = env.pop("body")

    # sig/signature -> sigs[]
    if "sigs" not in env:
        if "sig" in env and isinstance(env["sig"], dict):
            env["sigs"] = [env.pop("sig")]
        elif "signature" in env and isinstance(env["signature"], dict):
            env["sigs"] = [env.pop("signature")]

    # if sigs is accidentally a dict, wrap it
    if isinstance(env.get("sigs"), dict):
        env["sigs"] = [env["sigs"]]

    # Normalize tx fields
    tx = env.get("tx")
    if isinstance(tx, dict):
        # Many callers refer to sender, but CLI commonly encodes "from"
        if "sender" not in tx and "from" in tx:
            tx["sender"] = tx["from"]

        # Some older code uses "gas" instead of gasLimit; don't force here.
        # Nonce should be present, but guard anyway.
        if "nonce" not in tx and "n" in tx:
            tx["nonce"] = tx["n"]

        env["tx"] = tx

    # Normalize first signature dict (and all sigs) key names
    sigs = env.get("sigs")
    if isinstance(sigs, list):
        new_sigs: list[dict] = []
        for s in sigs:
            if not isinstance(s, dict):
                continue
            sd = dict(s)

            # algId
            if "algId" not in sd:
                if "alg" in sd:
                    sd["algId"] = sd["alg"]
                elif "alg_id" in sd:
                    sd["algId"] = sd["alg_id"]
                elif "algid" in sd:
                    sd["algId"] = sd["algid"]

            # pubkey
            if "pubkey" not in sd:
                if "pk" in sd:
                    sd["pubkey"] = sd["pk"]
                elif "pub" in sd:
                    sd["pubkey"] = sd["pub"]

            # sig
            if "sig" not in sd:
                if "signature" in sd:
                    sd["sig"] = sd["signature"]

            new_sigs.append(sd)

        env["sigs"] = new_sigs

    return env


def _mempool_has(mempool: t.Any, tx_hash_hex: str) -> bool:
    """
    Make has_hash checks robust across implementations (hex with/without 0x, bytes).
    """
    if mempool is None or not hasattr(mempool, "has_hash"):
        return False

    candidates: list[t.Any] = []
    h = (tx_hash_hex or "").lower()
    if h:
        candidates.append(h)
        if h.startswith("0x"):
            candidates.append(h[2:])
        else:
            candidates.append("0x" + h)

        try:
            candidates.append(_b(h))
        except Exception:
            pass

    for cand in candidates:
        try:
            if mempool.has_hash(cand):  # type: ignore[call-arg]
                return True
        except Exception:
            continue
    return False


def _extract_sender_address(obj: dict) -> str | None:
    """
    Extract the bech32m sender address from a signed transaction object.

    Uses the signature envelope to reconstruct the address from pubkey + alg_id.
    """
    if _address_from_pubkey is None:
        return None

    sigs = obj.get("sigs")
    if not sigs or not isinstance(sigs, list) or len(sigs) == 0:
        return None

    sig = sigs[0]
    if not isinstance(sig, dict):
        return None

    alg_id = sig.get("alg") or sig.get("alg_id") or sig.get("algId") or sig.get("algId")
    pubkey = sig.get("pubkey") or sig.get("pub") or sig.get("pk")

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
        return _address_from_pubkey(pubkey, alg_id)
    except Exception:
        return None


def _compute_tx_hash(tx_like: t.Any) -> str:
    """
    Compute tx hash (txid) from the full signed transaction CBOR.
    Per spec: TxID = sha3_256(CBOR(SignedTxMap)), i.e., includes signatures.
    """
    try:
        if hasattr(tx_like, "txid") and callable(getattr(tx_like, "txid")):
            return _hex(tx_like.txid()) or ""  # type: ignore[return-value]

        if hasattr(tx_like, "to_cbor") and callable(getattr(tx_like, "to_cbor")):
            cbor_bytes = tx_like.to_cbor()
            return _hex(_sha3_256(cbor_bytes)) or ""  # type: ignore[return-value]

        if _cbor_dumps is None:
            raise RuntimeError("No CBOR encoder available")
        if _dc.is_dataclass(tx_like):
            obj = _dcd(tx_like)
        else:
            obj = dict(tx_like)
        cbor_bytes = _cbor_dumps(obj)
        return _hex(_sha3_256(cbor_bytes)) or ""  # type: ignore[return-value]
    except Exception as e:  # pragma: no cover
        raise rpc_errors.InternalError(f"tx hash failed: {e}")


def _collect_sign_bytes(tx_like: t.Any) -> list[tuple[str, bytes]]:
    """Return a list of candidate SignBytes encodings for defensive verify."""
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
        raise rpc_errors.InternalError(
            "No canonical encoder for SignBytes (all helpers unavailable)"
        )

    if errors:
        log.debug("SignBytes helper errors (ignored): %s", "; ".join(errors))

    return candidates


def _build_sig_env(
    alg_id: t.Any, sig: bytes, *, domain: str = "tx", prehash: str = "sha3-512"
):
    from pq.py.sign import Signature  # type: ignore

    if _ALG_NAME is not None and isinstance(alg_id, int):
        alg_name = _ALG_NAME.get(alg_id, f"alg_0x{alg_id:02x}")
    else:
        alg_name = f"alg_0x{alg_id:02x}" if isinstance(alg_id, int) else str(alg_id)

    sig_env = Signature(
        alg_id=alg_id,
        alg_name=alg_name,
        domain=domain or "tx",
        prehash=prehash or "sha3-512",
        sig=sig,
    )
    return sig_env, alg_name


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
    """Return the configured chain ID for this node."""
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
    """Return the configured fork_id for this node (derived from genesis)."""
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
    """Extract (alg_id, pubkey, signature, domain, prehash) from tx envelope."""
    sig = obj.get("sig") or obj.get("signature")

    if sig is None:
        sigs = obj.get("sigs")
        if isinstance(sigs, list) and len(sigs) > 0:
            sig = sigs[0]

    if not isinstance(sig, dict):
        raise rpc_errors.InvalidParams("Missing 'sig' object")

    alg_id = sig.get("algId") or sig.get("alg_id") or sig.get("alg")
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

    pub = sig.get("pubkey") or sig.get("pub") or sig.get("pk")
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

    return (
        alg_id,
        _b(pub) if isinstance(pub, str) else bytes(pub),
        _b(s) if isinstance(s, str) else bytes(s),
        str(domain),
        str(prehash),
    )


def _extract_chain_id(tx_like: t.Any, obj: dict) -> int:
    """Extract chain_id from transaction object."""
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
    """Validate chainId against node expectation and return the value used."""
    want = _chain_id_required()

    try:
        cid = _extract_chain_id(obj, obj)
    except rpc_errors.InvalidParams:
        cid = None

    log.debug(
        "ChainId validation: extracted=%s, expected=%s, envelope_keys=%s",
        cid,
        want,
        list(obj.keys()) if isinstance(obj, dict) else "not-dict",
    )

    if cid is None:
        log.warning(
            "ChainId missing in transaction envelope: keys=%s, nested_keys=%s",
            list(obj.keys()),
            list(obj.get("tx", {}).keys()) if isinstance(obj.get("tx"), dict) else None,
        )
        raise rpc_errors.ChainIdMismatch(got=0, expected=want)

    if int(cid) != int(want):
        log.warning("ChainId mismatch: got=%s, expected=%s", int(cid), int(want))
        raise rpc_errors.ChainIdMismatch(got=int(cid), expected=int(want))

    return int(cid)


def _verify_pq_signature(tx_like: t.Any, obj: dict, *, chain_id: int) -> None:
    if _pq_verify is None:
        if _PQ_VERIFY_OPTIONAL:
            log.warning(
                "PQ verification unavailable; skipping due to ANIMICA_PQ_VERIFY_OPTIONAL/ANIMICA_SKIP_PQ_VERIFY",
            )
            return

        raise rpc_errors.InternalError(
            "PQ verification unavailable",
            **_error_data(
                "pq_verify",
                RuntimeError("Missing pq.py.verify backend (animica-pq not installed?)"),
                "_verify_pq_signature",
                "Ensure animica-pq is installed or set ANIMICA_PQ_VERIFY_OPTIONAL=1 for dev",
            ),
        )

    alg_id, pub, sig, domain, prehash = _extract_sig(obj)

    candidates = _collect_sign_bytes(obj)
    if not candidates:
        raise rpc_errors.InvalidTx("No sign-bytes candidates found for PQ verification")
    msg_label, msg = candidates[0]

    log.debug(
        "PQ sign-bytes source=%s len=%d hex_prefix=%s",
        msg_label,
        len(msg),
        msg[:64].hex() if len(msg) >= 64 else msg.hex(),
    )

    tx_view = obj.get("tx", obj) if isinstance(obj, dict) else {}
    try:
        debug_fields = {
            "from": tx_view.get("from"),
            "to": tx_view.get("to"),
            "value": tx_view.get("value"),
            "nonce": tx_view.get("nonce"),
            "gasLimit": tx_view.get("gasLimit"),
            "maxFee": tx_view.get("maxFee"),
            "chainId": tx_view.get("chainId"),
        }
        log.debug("PQ tx fields for verification: %s", debug_fields)
    except Exception:  # pragma: no cover
        pass

    alg_name_for_log = f"alg_0x{alg_id:02x}" if isinstance(alg_id, int) else str(alg_id)
    if _ALG_NAME is not None and isinstance(alg_id, int):
        alg_name_for_log = _ALG_NAME.get(alg_id, alg_name_for_log)

    log.debug(
        "PQ signature verification: alg_id=%s, pubkey_len=%d, sig_len=%d, msg_len=%d, chain_id=%d (msg_source=%s, prehash=%s)",
        alg_id,
        len(pub),
        len(sig),
        len(msg),
        chain_id,
        msg_label,
        prehash,
    )

    try:
        sig_env, alg_name = _build_sig_env(alg_id, sig, domain=domain, prehash=prehash)

        if _PQ_VERIFY_DEBUG:
            log.info(
                "PQ VERIFY DEBUG: algorithm=%s (id=%s), pubkey_len=%d bytes, sig_len=%d bytes, message_len=%d bytes, message_prefix=%s, chain_id=%d",
                alg_name,
                alg_id,
                len(pub),
                len(sig),
                len(msg),
                msg[:16].hex() if len(msg) >= 16 else msg.hex(),
                chain_id,
            )

        ok, used_label, verify_errors = _verify_pq_candidates(
            candidates, sig_env, pub, chain_id=chain_id, fork_id=_fork_id_required()
        )

        if ok and used_label and used_label != msg_label:
            log.warning(
                "PQ signature verified using alternate SignBytes source (primary=%s, used=%s, primary_len=%d, alt_len=%d)",
                msg_label,
                used_label,
                len(msg),
                next((len(c) for lbl, c in candidates if lbl == used_label), 0),
            )

        log.debug(
            "PQ signature verification result: %s (domain=%s, alg=%s)",
            "PASS" if ok else "FAIL",
            sig_env.domain,
            alg_name,
        )

        if verify_errors:
            log.debug("PQ verify helper errors: %s", "; ".join(verify_errors))
    except Exception as e:
        log.error("PQ signature verification setup failed: %s", e, exc_info=True)
        raise rpc_errors.InternalError(f"PQ signature verification setup failed: {e}")

    if not ok:
        log.error(
            "PQ signature verification FAILED: algorithm=%s (id=%s), pubkey_len=%d bytes, sig_len=%d bytes, message_len=%d bytes, message_prefix=%s, chain_id=%d, domain=%s, prehash=%s",
            alg_name_for_log,
            alg_id,
            len(pub),
            len(sig),
            len(msg),
            msg[:16].hex() if len(msg) >= 16 else msg.hex(),
            chain_id,
            sig_env.domain,
            sig_env.prehash,
        )
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
    """Decode a raw CBOR transaction envelope."""
    if _cbor_loads is None:
        raise rpc_errors.InternalError("CBOR decoder unavailable")
    obj = _cbor_loads(raw)

    tx_hash_hex = _hex(_sha3_256(raw)) or ""

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

    enriched_obj = dict(obj)
    enriched_obj["hash"] = tx_hash_hex
    enriched_obj["raw"] = raw

    if _Tx is not None:
        try:
            if hasattr(_Tx, "from_obj"):
                tx = _Tx.from_obj(obj)  # type: ignore[attr-defined]
            elif hasattr(_Tx, "from_dict"):
                tx = _Tx.from_dict(obj)  # type: ignore[attr-defined]
            else:
                tx = _Tx(**obj)  # type: ignore[call-arg]
            return tx, enriched_obj
        except Exception:
            pass

    return enriched_obj, enriched_obj


def _validate_sufficient_balance(obj: dict) -> None:
    """Validate that the sender has sufficient balance to cover value + max fees."""
    tx_obj = obj.get("body", obj.get("tx", obj))

    sender_addr = _extract_sender_address(obj)
    if sender_addr is None:
        log.debug("_validate_sufficient_balance: cannot determine sender, skipping")
        return

    value = tx_obj.get("value", 0)
    gas_limit = tx_obj.get("gasLimit") or tx_obj.get("gas_limit") or tx_obj.get("gas", 0)
    max_fee = (
        tx_obj.get("maxFee")
        or tx_obj.get("max_fee")
        or tx_obj.get("gasPrice")
        or tx_obj.get("gas_price", 0)
    )

    value = int(value) if value is not None else 0
    gas_limit = int(gas_limit) if gas_limit is not None else 0
    max_fee = int(max_fee) if max_fee is not None else 0

    required = value + (gas_limit * max_fee)

    try:
        ctx = deps.get_ctx()
        if not hasattr(ctx, "state_db") or ctx.state_db is None:
            log.debug("_validate_sufficient_balance: state_db not available, skipping")
            return

        state_db = ctx.state_db

        if _parse_address is None:
            log.debug("_validate_sufficient_balance: parse_address not available, skipping")
            return

        try:
            sender_bytes = _parse_address(sender_addr)
        except Exception as e:
            log.debug(
                "_validate_sufficient_balance: failed to parse sender %s: %s",
                sender_addr,
                e,
            )
            return

        balance = None
        for method_name in ("get_balance", "read_balance", "balance_of"):
            if hasattr(state_db, method_name):
                try:
                    balance = int(getattr(state_db, method_name)(sender_bytes))
                    break
                except Exception as e:
                    log.debug("_validate_sufficient_balance: %s failed: %s", method_name, e)
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
        _from = tx_obj.get("from") or tx_obj.get("sender")
        if _from is None and hasattr(tx, "unsigned"):
            _from = getattr(tx.unsigned, "sender", None)
        if _from is None:
            _from = getattr(tx, "sender", None)

    to = tx_obj.get("to")
    if to is None and hasattr(tx, "unsigned"):
        payload = getattr(tx.unsigned, "payload", None)
        to = getattr(payload, "to", None)
    if to is None:
        to = getattr(tx, "to", None)

    nonce = tx_obj.get("nonce")
    if nonce is None and hasattr(tx, "unsigned"):
        nonce = getattr(tx.unsigned, "nonce", None)
    if nonce is None:
        nonce = getattr(tx, "nonce", None)

    gas_obj = tx_obj.get("gas")
    if isinstance(gas_obj, dict):
        gas = gas_obj.get("limit")
        tip = gas_obj.get("price")
    else:
        gas = gas_obj or tx_obj.get("gasLimit") or tx_obj.get("gas_limit")
        tip = tx_obj.get("tip") or tx_obj.get("gasPrice") or tx_obj.get("gas_price")

    if gas is None and hasattr(tx, "unsigned"):
        gas = getattr(tx.unsigned, "gas_limit", None)
    if gas is None:
        gas = getattr(tx, "gas_limit", None)

    if tip is None and hasattr(tx, "unsigned"):
        tip = getattr(tx.unsigned, "gas_price", None)
    if tip is None:
        tip = getattr(tx, "tip", None)

    max_fee = tx_obj.get("maxFee") or tx_obj.get("max_fee")
    if max_fee is None and tip is not None:
        max_fee = tip

    chain_id = tx_obj.get("chainId") or tx_obj.get("chain_id")
    if chain_id is None and hasattr(tx, "unsigned"):
        chain_id = getattr(tx.unsigned, "chain_id", None) or getattr(tx.unsigned, "chainId", None)
    if chain_id is None:
        chain_id = getattr(tx, "chain_id", None) or getattr(tx, "chainId", None)

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

    if value is None and hasattr(tx, "unsigned"):
        payload = getattr(tx.unsigned, "payload", None)
        value = getattr(payload, "amount", getattr(payload, "value", 0))
    if value is None:
        value = getattr(tx, "value", 0)

    if data is None and hasattr(tx, "unsigned"):
        payload = getattr(tx.unsigned, "payload", None)
        data = getattr(payload, "data", None)
    if data is None:
        data = getattr(tx, "data", None)

    if hasattr(tx, "txid") and callable(getattr(tx, "txid")):
        hash_hex = _hex(tx.txid()) or ""
    else:
        hash_hex = _hex(_sha3_256(_cbor_dumps(obj))) or "" if _cbor_dumps else ""

    v = {
        "hash": hash_hex,
        "from": _hex(_from) if isinstance(_from, (bytes, bytearray)) else _from,
        "to": _hex(to) if isinstance(to, (bytes, bytearray)) else to,
        "nonce": int(nonce) if nonce is not None else None,
        "gas": int(gas) if gas is not None else None,
        "gasLimit": int(gas) if gas is not None else None,
        "tip": int(tip) if tip is not None else None,
        "gasPrice": int(tip) if tip is not None else None,
        "maxFee": int(max_fee) if max_fee is not None else None,
        "value": int(value) if value is not None else None,
        "chainId": int(chain_id) if chain_id is not None else None,
        "data": _hex(data) if isinstance(data, (bytes, bytearray)) else data,
        "blockHash": (
            None if pending else (_hex(block_hash) if isinstance(block_hash, (bytes, bytearray)) else block_hash)
        ),
        "blockNumber": None if pending else (int(block_number) if block_number is not None else None),
        "transactionIndex": None if pending else (int(tx_index) if tx_index is not None else None),
    }
    return {k: v for k, v in v.items() if v is not None}


def _pending_put(tx_hash_hex: str, raw: bytes) -> None:
    if _PEND is not None and hasattr(_PEND, "add_raw"):
        _PEND.add_raw(tx_hash_hex, raw)  # type: ignore[attr-defined]
        return
    if _PEND is not None and hasattr(_PEND, "add"):
        _PEND.add(tx_hash_hex, raw)  # type: ignore[attr-defined]
        return
    _FALLBACK_PENDING[tx_hash_hex] = raw
    _FALLBACK_PENDING_TS[tx_hash_hex] = time.time()


def _get_mempool_service():
    try:
        ctx = deps.get_ctx()
    except Exception:
        return None
    return getattr(ctx, "mempool", None)


def _gossip_tx_to_peers(raw_tx: bytes) -> None:
    """Best-effort: gossip admitted tx to connected peers."""
    try:
        ctx = deps.get_ctx()
        if not hasattr(ctx, "p2p_service") or ctx.p2p_service is None:
            log.debug("P2P service not available; tx not gossiped")
            return

        p2p_service = ctx.p2p_service

        if hasattr(p2p_service, "relay_tx") and callable(getattr(p2p_service, "relay_tx")):
            try:
                loop = asyncio.get_running_loop()
                asyncio.ensure_future(p2p_service.relay_tx(raw_tx), loop=loop)  # type: ignore[call-arg]
                log.debug("Scheduled tx relay via P2PService.relay_tx()")
                return
            except RuntimeError:
                log.debug("No running event loop; tx not relayed")
                return

        if hasattr(p2p_service, "tx_relay_handler"):
            tx_relay_handler = p2p_service.tx_relay_handler
            try:
                loop = asyncio.get_running_loop()
                asyncio.ensure_future(tx_relay_handler.publish_local_tx(raw_tx), loop=loop)
                log.debug("Scheduled tx gossip via TxRelayHandler")
                return
            except RuntimeError:
                log.debug("No running event loop; tx not gossiped")
                return
            except AttributeError:
                log.debug("TxRelayHandler missing publish_local_tx; falling back")
                pass

        if not hasattr(p2p_service, "gossip"):
            log.debug("P2P gossip engine not available; tx not gossiped")
            return

        gossip_engine = p2p_service.gossip
        if not hasattr(gossip_engine, "publish") or not callable(gossip_engine.publish):
            log.debug("P2P gossip publish not available; tx not gossiped")
            return

        try:
            from p2p.gossip import topics as gossip_topics

            chain_id = _chain_id_required()
            tx_topic = gossip_topics.txs(chain_id)
            topic_path = tx_topic.path
        except Exception:
            topic_path = "txs"
            log.debug("Using fallback topic path 'txs'")

        try:
            loop = asyncio.get_running_loop()
            asyncio.ensure_future(gossip_engine.publish(topic_path, raw_tx), loop=loop)
            log.debug("Scheduled tx gossip to topic %s", topic_path)
        except RuntimeError:
            log.debug("No running event loop; tx not gossiped")
    except Exception as e:
        log.debug("Failed to gossip tx: %s", e)


def _pending_get(tx_hash_hex: str) -> bytes | None:
    if _PEND is not None and hasattr(_PEND, "get_raw"):
        return _PEND.get_raw(tx_hash_hex)  # type: ignore[attr-defined]
    if _PEND is not None and hasattr(_PEND, "get"):
        return _PEND.get(tx_hash_hex)  # type: ignore[attr-defined]
    return _FALLBACK_PENDING.get(tx_hash_hex)


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


def _lookup_persisted_tx(
    tx_hash_hex: str,
) -> tuple[dict | None, int | None, int | None, bytes | None]:
    """Return (obj_view, block_number, tx_index, block_hash) if found in DB."""
    ctx = deps.get_ctx()
    if hasattr(ctx, "block_db") and ctx.block_db is not None:
        block_db = ctx.block_db
        if hasattr(block_db, "get_transaction_by_hash"):
            try:
                tx_hash_bytes = _b(tx_hash_hex)
                result = block_db.get_transaction_by_hash(tx_hash_bytes)
                if result is not None:
                    height, idx, block_hash, tx_obj = result
                    obj = (
                        _dcd(tx_obj)
                        if _dc.is_dataclass(tx_obj)
                        else dict(tx_obj) if isinstance(tx_obj, dict) else {}
                    )
                    view = _tx_view(
                        tx_obj,
                        obj,
                        pending=False,
                        block_hash=block_hash,
                        block_number=height,
                        tx_index=idx,
                    )
                    return view, height, idx, block_hash
            except Exception as e:
                log.debug(f"block_db.get_transaction_by_hash failed: {e}")

    svc = getattr(deps, "state_service", None)
    if svc is not None and hasattr(svc, "get_transaction_by_hash"):
        tx_rec = svc.get_transaction_by_hash(tx_hash_hex)  # type: ignore
        if tx_rec:
            tx_obj = tx_rec.get("tx") or tx_rec
            block_number = tx_rec.get("blockNumber")
            index = tx_rec.get("transactionIndex")
            b_hash = tx_rec.get("blockHash")
            if isinstance(b_hash, str):
                b_hash = _b(b_hash)
            if isinstance(tx_obj, (bytes, bytearray)) and _cbor_loads:
                obj = _cbor_loads(bytes(tx_obj))
                tx_like = obj
            else:
                tx_like = tx_obj
                obj = _dcd(tx_obj) if _dc.is_dataclass(tx_obj) else dict(tx_obj)
            view = _tx_view(
                tx_like,
                obj,
                pending=False,
                block_hash=b_hash,
                block_number=block_number,
                tx_index=index,
            )
            return (
                view,
                int(block_number) if block_number is not None else None,
                int(index) if index is not None else None,
                b_hash if isinstance(b_hash, (bytes, bytearray)) else None,
            )

    if hasattr(deps, "get_tx_by_hash"):
        rec = deps.get_tx_by_hash(tx_hash_hex)  # type: ignore
        if rec:
            obj = rec.get("obj", {})
            blk = rec.get("block", {})
            h = blk.get("number") or blk.get("height")
            idx = rec.get("index")
            bh = blk.get("hash")
            if isinstance(bh, str):
                bh = _b(bh)
            view = _tx_view(
                obj,
                obj if isinstance(obj, dict) else _dcd(obj),
                pending=False,
                block_hash=bh,
                block_number=h,
                tx_index=idx,
            )
            return view, h, idx, bh
    return None, None, None, None


# ——— Methods ———


@method(
    "tx.sendRawTransaction",
    desc=(
        "Submit a signed CBOR-encoded transaction. "
        "Param: rawTx (hex string '0x…' or base64 '0b:…'). "
        "Returns tx hash. "
        "\n\n"
        "The rawTx parameter must be a CBOR-encoded envelope with structure:\n"
        "  {\n"
        '    "body": { ...transaction fields... },\n'
        '    "sig": {\n'
        '      "algId": <int>,\n'
        '      "pubkey": <bytes>,\n'
        '      "sig": <bytes>\n'
        "    }\n"
        "  }\n"
        "Alternative envelope with sigs array is also supported:\n"
        '  { "body": {...}, "sigs": [{"algId": ..., "pubkey": ..., "sig": ...}] }\n'
    ),
    aliases=("tx_sendRawTransaction",),
)
def tx_send_raw_transaction(rawTx: str) -> str:
    try:
        return _tx_send_raw_transaction(rawTx)
    except rpc_errors.RpcError:
        raise
    except Exception as e:  # pragma: no cover
        raise rpc_errors.InvalidTx(
            "tx.sendRawTransaction failed",
            **_error_data(
                "unknown",
                e,
                "tx.sendRawTransaction",
                "Enable ANIMICA_RPC_DEBUG=1 for stack trace",
            ),
        ) from e


def _tx_send_raw_transaction(rawTx: str) -> str:
    if not isinstance(rawTx, str):
        raise rpc_errors.InvalidParams("rawTx must be a hex string")
    if rawTx.startswith("0b:"):
        raise rpc_errors.InvalidParams("base64 not supported yet; send hex (0x…)")

    try:
        raw = _b(rawTx)
    except Exception as e:
        log.error("tx.sendRawTransaction: hex decode failed, len=%d", len(rawTx) if rawTx else 0)
        TX_VALIDATION_FAILURES.labels(reason="hex_decode_failed").inc()
        raise rpc_errors.InvalidTx(
            "rawTx decode failed",
            **_error_data("decode", e, "tx.sendRawTransaction._b", "Ensure rawTx is 0x-prefixed hex"),
        ) from e

    log.debug("tx.sendRawTransaction: decoding %d CBOR bytes", len(raw))

    try:
        tx_like, obj = _decode_tx(raw)
    except rpc_errors.RpcError:
        raise
    except Exception as e:
        log.error("tx.sendRawTransaction: CBOR decode failed, raw_len=%d", len(raw), exc_info=True)
        TX_VALIDATION_FAILURES.labels(reason="cbor_decode_failed").inc()
        raise rpc_errors.InvalidTx(
            "Transaction decode failed",
            **_error_data("decode", e, "_decode_tx", "Ensure rawTx is CBOR {body, sig}"),
        ) from e

    log.info(
        "tx.sendRawTransaction: decoded envelope type=%s, keys=%s, body_keys=%s",
        type(tx_like).__name__ if hasattr(type(tx_like), "__name__") else type(tx_like),
        list(obj.keys()) if isinstance(obj, dict) else "not-dict",
        (list(obj.get("body", {}).keys()) if isinstance(obj, dict) and "body" in obj else "no-body"),
    )

    try:
        try:
            chain_id = _validate_chain_id(obj)
        except rpc_errors.ChainIdMismatch as e:
            log.warning(
                "tx.sendRawTransaction: chainId mismatch, got=%s, expected=%s",
                e.data.get("got") if e.data else "unknown",
                e.data.get("expected") if e.data else "unknown",
            )
            TX_VALIDATION_FAILURES.labels(reason="chain_id_mismatch").inc()
            raise

        try:
            _verify_pq_signature(tx_like, obj, chain_id=chain_id)
        except rpc_errors.BadSignature as e:
            log.warning("tx.sendRawTransaction: PQ signature invalid, chain_id=%d", chain_id)
            TX_VALIDATION_FAILURES.labels(reason="signature_invalid").inc()
            raise

        tx_hash_hex = _hex(_sha3_256(raw)) or ""
        log.info("tx.sendRawTransaction: validation passed, tx_hash=%s, chain_id=%d", tx_hash_hex, chain_id)

        try:
            _validate_sufficient_balance(obj)
        except rpc_errors.InsufficientFunds as e:
            log.warning(
                "tx.sendRawTransaction: insufficient balance, required=%s, available=%s",
                e.data.get("required") if e.data else "unknown",
                e.data.get("available") if e.data else "unknown",
            )
            TX_VALIDATION_FAILURES.labels(reason="insufficient_balance").inc()
            raise

        mempool_service = _get_mempool_service()

        # Duplicate suppression (idempotent)
        if mempool_service is not None and _mempool_has(mempool_service, tx_hash_hex):
            log.info("tx.sendRawTransaction: duplicate tx (already in mempool), hash=%s", tx_hash_hex)
            return tx_hash_hex
        if _pending_get(tx_hash_hex) is not None:
            log.info("tx.sendRawTransaction: duplicate tx (already pending), hash=%s", tx_hash_hex)
            return tx_hash_hex
        persisted, *_ = _lookup_persisted_tx(tx_hash_hex)
        if persisted is not None:
            log.info("tx.sendRawTransaction: duplicate tx (already persisted), hash=%s", tx_hash_hex)
            return tx_hash_hex

        # Canonicalize into the shape core/mempool typically expects
        core_env = _canonicalize_envelope_for_core(obj)

        tx_obj = None
        if _Tx is not None:
            try:
                if hasattr(_Tx, "from_obj"):
                    tx_obj = _Tx.from_obj(core_env)  # type: ignore[attr-defined]
                elif hasattr(_Tx, "from_dict"):
                    tx_obj = _Tx.from_dict(core_env)  # type: ignore[attr-defined]
                else:
                    tx_obj = _Tx(**core_env)  # type: ignore[call-arg]
            except Exception as e:
                log.debug("Failed to construct core Tx from canonical envelope: %s", e)
                tx_obj = None

        if mempool_service is not None and tx_obj is not None:
            try:
                mempool_size_before = mempool_service.count() if hasattr(mempool_service, "count") else "?"
                log.info(
                    "tx.sendRawTransaction: mempool_service available, path=service.submit, hash=%s, mempool_id=%s, size_before=%s",
                    tx_hash_hex,
                    id(mempool_service),
                    mempool_size_before,
                )

                # Try submit with Tx object first
                try:
                    mempool_service.submit(tx=tx_obj, raw=raw, tx_hash_hex=tx_hash_hex)
                except Exception as exc:
                    # If admission complains about missing sender/nonce, try dict-shaped fallbacks
                    msg = str(exc).lower()
                    if "missing sender" in msg or "missing nonce" in msg or "sender or nonce" in msg:
                        log.warning(
                            "tx.sendRawTransaction: submit(tx_obj) failed (%s); retrying with canonical dict forms",
                            msg,
                        )
                        last_exc = exc
                        # 1) whole canonical envelope
                        try:
                            mempool_service.submit(tx=core_env, raw=raw, tx_hash_hex=tx_hash_hex)
                        except Exception as exc2:
                            last_exc = exc2
                            # 2) just the tx body dict (has sender/nonce explicitly)
                            tx_dict = core_env.get("tx") if isinstance(core_env, dict) else None
                            if isinstance(tx_dict, dict):
                                mempool_service.submit(tx=tx_dict, raw=raw, tx_hash_hex=tx_hash_hex)
                            else:
                                raise last_exc
                    else:
                        raise

                mempool_size_after = mempool_service.count() if hasattr(mempool_service, "count") else "?"
                log.info(
                    "tx.sendRawTransaction: submit() completed, hash=%s, size_after=%s",
                    tx_hash_hex,
                    mempool_size_after,
                )

                # Verify presence with a short grace window (some mempools enqueue writes)
                has_tx = False
                for _ in range(20):
                    if _mempool_has(mempool_service, tx_hash_hex):
                        has_tx = True
                        break
                    time.sleep(0.01)

                log.info(
                    "tx.sendRawTransaction: post-submit verification, hash=%s, in_mempool=%s",
                    tx_hash_hex,
                    has_tx,
                )

                if not has_tx:
                    log.error(
                        "tx.sendRawTransaction: VERIFICATION FAILED - tx not in mempool after submit(), hash=%s, mempool_id=%s",
                        tx_hash_hex,
                        id(mempool_service),
                    )
                    raise rpc_errors.InternalError(
                        "Transaction submitted but not in mempool",
                        data={
                            "tx_hash": tx_hash_hex,
                            "reason": "verification_failed",
                            "hint": "mempool admission returned success but did not persist the tx",
                        },
                    )

                _pending_put(tx_hash_hex, raw)

            except Exception as exc:
                raise rpc_errors.to_error(exc) from exc

        else:
            log.info(
                "tx.sendRawTransaction: mempool_service unavailable (service=%s, tx_obj=%s), path=pending_put, hash=%s",
                "None" if mempool_service is None else "available",
                "None" if tx_obj is None else "available",
                tx_hash_hex,
            )
            _pending_put(tx_hash_hex, raw)
            log.info("tx.sendRawTransaction: tx admitted to pending pool, hash=%s", tx_hash_hex)

        try:
            if hasattr(deps, "ws_broadcast_pending"):
                deps.ws_broadcast_pending(tx_hash_hex, obj)  # type: ignore
        except Exception:
            pass

        try:
            _gossip_tx_to_peers(raw)
        except Exception as e:
            log.debug("Failed to gossip tx to peers: %s", e)

        return tx_hash_hex

    except rpc_errors.BadSignature as e:
        data = dict(e.data or {})
        data.setdefault("kind", "pq_verify")
        data.setdefault("where", "tx.sendRawTransaction")
        data.setdefault("hint", "Invalid PQ signature or unsupported prehash")
        if _RPC_DEBUG and "stack" not in data:
            data["stack"] = "".join(traceback.format_exception(e)).strip()
        raise rpc_errors.BadSignature(str(e), **data) from e

    except rpc_errors.RpcError:
        raise

    except Exception as e:
        log.exception("tx.sendRawTransaction: unexpected failure")
        raise rpc_errors.InvalidTx(
            "tx.sendRawTransaction failed",
            **_error_data(
                "unknown",
                e,
                "tx.sendRawTransaction",
                "Enable ANIMICA_RPC_DEBUG=1 for stack trace",
            ),
        ) from e


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

    try:
        raw = _b(rawTx)
    except Exception as e:
        raise rpc_errors.InvalidTx(
            "rawTx decode failed",
            **_error_data("decode", e, "tx.decodeRawTransaction._b", "Ensure rawTx is 0x-prefixed hex"),
        ) from e

    log.debug("tx.decodeRawTransaction: decoding %d CBOR bytes", len(raw))

    try:
        tx_like, obj = _decode_tx(raw)
    except rpc_errors.RpcError:
        raise
    except Exception as e:
        raise rpc_errors.InvalidTx(
            "Transaction decode failed",
            **_error_data("decode", e, "_decode_tx", "Ensure rawTx is CBOR {body, sig}"),
        ) from e

    decoded_obj = obj if isinstance(obj, dict) else _dcd(obj)
    return {
        "len": len(raw),
        "type": type(tx_like).__name__ if hasattr(type(tx_like), "__name__") else str(type(tx_like)),
        "tx": _jsonify(decoded_obj),
    }


@method(
    "tx.debugVerifyRawTransaction",
    desc=(
        "Decode and verify a raw PQ transaction without admitting it to the mempool. "
        "Returns verification diagnostics instead of a tx hash."
    ),
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
    _tx_like, obj = _decode_tx(raw)
    chain_id = _validate_chain_id(obj)

    alg_id, pub, sig, domain, prehash = _extract_sig(obj)
    candidates = _collect_sign_bytes(obj)

    sig_env, alg_name = _build_sig_env(alg_id, sig, domain=domain, prehash=prehash)

    ok, used_label, verify_errors = _verify_pq_candidates(
        candidates, sig_env, pub, chain_id=chain_id, fork_id=_fork_id_required()
    )

    candidate_views = [
        {
            "label": lbl,
            "len": len(data),
            "prefix": data[:32].hex(),
            "sha3_256": _hex(_sha3_256(data)),
        }
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

    log.debug("tx.getTransactionByHash: looking up tx_hash=%s", tx_hash_hex)

    raw = _pending_get(tx_hash_hex)
    if raw is not None and _cbor_loads is not None:
        log.debug("tx.getTransactionByHash: found in pending pool, raw_len=%d", len(raw))
        try:
            obj = _cbor_loads(raw)
            tx_like = obj
            view = _tx_view(tx_like, obj if isinstance(obj, dict) else _dcd(obj), pending=True)
            log.debug(
                "tx.getTransactionByHash: returning pending tx view, fields=%s",
                list(view.keys()) if view else "none",
            )
            return view
        except Exception as e:
            log.error(
                "tx.getTransactionByHash: failed to decode pending tx, hash=%s, error=%s",
                tx_hash_hex,
                e,
                exc_info=True,
            )

    view, *_etc = _lookup_persisted_tx(tx_hash_hex)
    if view is not None:
        log.debug("tx.getTransactionByHash: found in persisted DB")
        return view

    log.debug("tx.getTransactionByHash: tx not found, hash=%s", tx_hash_hex)
    return None


# NOTE: tx.getTransactionReceipt is registered in rpc/methods/receipt.py
