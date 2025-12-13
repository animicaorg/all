from __future__ import annotations

import dataclasses as _dc
import logging
import os
import typing as t

from rpc import deps
from rpc import errors as rpc_errors
from rpc.methods import method

log = logging.getLogger(__name__)
_PQ_VERIFY_DEBUG = os.environ.get("ANIMICA_PQ_VERIFY_DEBUG") == "1"

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
    from core.encoding.canonical import \
        tx_sign_bytes as _tx_sign_bytes  # type: ignore
except Exception:  # pragma: no cover
    _tx_sign_bytes = None  # type: ignore

# Shared Animica helper for deterministic tx sign-bytes
try:
    from animica.tx.signing import \
        build_signable_tx_bytes as _build_signable_tx_bytes
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


def _extract_sender_address(obj: dict) -> str | None:
    """
    Extract the bech32m sender address from a signed transaction object.
    
    Uses the signature envelope to reconstruct the address from pubkey + alg_id.
    
    Args:
        obj: Transaction object, expected to have structure:
             {"tx": {...unsigned tx...}, "sigs": [{"alg": int, "pubkey": bytes, "sig": bytes}]}
             Field names may vary: alg/alg_id/algId, pubkey/pub/pk
    
    Returns:
        Bech32m address string (e.g., "anim1...") or None if signatures are missing
        or address encoding is unavailable.
    """
    if _address_from_pubkey is None:
        return None
    
    # Try to extract signature from obj["sigs"][0]
    sigs = obj.get("sigs")
    if not sigs or not isinstance(sigs, list) or len(sigs) == 0:
        return None
    
    sig = sigs[0]
    if not isinstance(sig, dict):
        return None
    
    # Extract alg_id and pubkey from signature (support multiple field name variations)
    alg_id = sig.get("alg") or sig.get("alg_id") or sig.get("algId")
    pubkey = sig.get("pubkey") or sig.get("pub") or sig.get("pk")
    
    if alg_id is None or pubkey is None:
        return None
    
    # Handle alg_id as string (e.g., "dilithium3")
    if isinstance(alg_id, str) and _ALG_ID is not None:
        try:
            if alg_id in _ALG_ID:
                alg_id = _ALG_ID[alg_id]
            else:
                # Try parsing as int
                alg_id = int(alg_id, 0)
        except Exception:
            return None
    
    # Ensure pubkey is bytes
    if isinstance(pubkey, str):
        pubkey = _b(pubkey)
    
    # Convert to bech32m address
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
        # If tx_like is a Tx dataclass with txid() method, use it
        if hasattr(tx_like, "txid") and callable(getattr(tx_like, "txid")):
            return _hex(tx_like.txid()) or ""  # type: ignore[return-value]
        
        # If tx_like is a Tx dataclass with to_cbor() method, use it
        if hasattr(tx_like, "to_cbor") and callable(getattr(tx_like, "to_cbor")):
            cbor_bytes = tx_like.to_cbor()
            return _hex(_sha3_256(cbor_bytes)) or ""  # type: ignore[return-value]
        
        # Fallback: tx_like is a dict (full signed tx structure)
        if _cbor_dumps is None:
            raise RuntimeError("No CBOR encoder available")
        if _dc.is_dataclass(tx_like):
            obj = _dcd(tx_like)
        else:
            obj = dict(tx_like)
        # Hash the full object including signatures
        cbor_bytes = _cbor_dumps(obj)
        return _hex(_sha3_256(cbor_bytes)) or ""  # type: ignore[return-value]
    except Exception as e:  # pragma: no cover
        raise rpc_errors.InternalError(f"tx hash failed: {e}")


def _collect_sign_bytes(tx_like: t.Any) -> list[tuple[str, bytes]]:
    """Return a list of candidate SignBytes encodings for defensive verify.

    The order of candidates is intentional: we try the most canonical helpers
    first (shared tx signing helpers), then fall back to SDK helpers, and
    finally to a minimal CBOR encoding. Duplicates are removed while preserving
    order so verification can short-circuit on the first success.
    """

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
        except Exception as exc:  # pragma: no cover - defensive path
            errors.append(f"{label}: {exc}")

    # Primary: shared Animica helper (aligns CLI/SDK/node)
    if _build_signable_tx_bytes is not None:
        _add("animica.tx.signing", lambda: _build_signable_tx_bytes(tx_like))

    # Core canonical helper (legacy compatibility)
    if _tx_sign_bytes is not None:
        _add("core.encoding.canonical", lambda: _tx_sign_bytes(tx_like))

    # SDK helper (used by CLI/SDK signing)
    if _sdk_sign_bytes is not None:
        _add("omni_sdk.tx.encode", lambda: _sdk_sign_bytes(tx_like))

    # Minimal fallback using local CBOR encoder
    if _cbor_dumps is not None:
        def _fallback_body() -> bytes:
            # Extract body from signed envelope or use the object directly
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


def _build_sig_env(alg_id: t.Any, sig: bytes):
    from pq.py.sign import Signature  # type: ignore

    if _ALG_NAME is not None and isinstance(alg_id, int):
        alg_name = _ALG_NAME.get(alg_id, f"alg_0x{alg_id:02x}")
    else:
        alg_name = f"alg_0x{alg_id:02x}" if isinstance(alg_id, int) else str(alg_id)

    sig_env = Signature(
        alg_id=alg_id,
        alg_name=alg_name,
        domain="tx",  # Standard domain for transaction signatures (matches CLI/SDK)
        prehash="sha3-512",  # Standard prehash for tx signatures
        sig=sig,
    )
    return sig_env, alg_name


def _verify_pq_candidates(
    candidates: list[tuple[str, bytes]], sig_env, pub: bytes, *, chain_id: int
) -> tuple[bool, str | None, list[str]]:
    ok = False
    used_label: str | None = None
    verify_errors: list[str] = []

    for label, candidate in candidates:
        try:
            attempt_ok = _pq_verify.verify_detached(  # type: ignore[attr-defined]
                candidate, sig_env, pub, chain_id=chain_id
            )
        except Exception as verify_exc:  # pragma: no cover - defensive
            verify_errors.append(f"{label}: {verify_exc}")
            continue

        if attempt_ok:
            ok = True
            used_label = label
            break

    return ok, used_label, verify_errors


def _chain_id_required() -> int:
    """Return the configured chain ID for this node."""

    # First, rely on the public deps helper which is backed by the live RPC
    # context. This reflects ANIMICA_CHAIN_ID/ANIMICA_NETWORK and keeps testnet
    # nodes from silently defaulting to mainnet (chain_id=1).
    if hasattr(deps, "get_chain_id"):
        try:
            return int(deps.get_chain_id())  # type: ignore[arg-type]
        except Exception:
            # Fall through to legacy paths if deps.get_chain_id is unavailable
            # or misconfigured during early boot.
            pass

    # Legacy fallbacks for older contexts/tests
    if hasattr(deps, "get_chain_params"):
        cp = deps.get_chain_params()  # type: ignore[attr-defined]
        cid = getattr(cp, "chain_id", getattr(cp, "chainId", None))
        if cid is not None:
            return int(cid)
    if hasattr(deps, "chain_id"):
        return int(getattr(deps, "chain_id"))  # type: ignore[attr-defined]
    if hasattr(deps, "config") and hasattr(deps.config, "chain_id"):
        return int(deps.config.chain_id)  # type: ignore[attr-defined]

    # Fallback mainnet id
    return 1


def _extract_sig(obj: dict) -> tuple[int, bytes, bytes]:
    """
    Extract (alg_id, pubkey, signature) from obj["sig"], obj["signature"], or obj["sigs"][0].
    Supports hex strings or raw bytes.
    Handles both flat and nested tx structures.
    
    Expected envelope structures:
    1. Flat with sig dict:
       { "body": {...}, "sig": {"algId": int, "pubkey": bytes, "sig": bytes} }
    2. Flat with signature dict:
       { "body": {...}, "signature": {"algId": int, "pubkey": bytes, "sig": bytes} }
    3. Array with sigs:
       { "body": {...}, "sigs": [{"algId": int, "pubkey": bytes, "sig": bytes}] }
    
    The sig/signature/sigs[0] value MUST be a dict, not raw bytes.
    """
    # Try flat structure first (obj.sig or obj.signature)
    sig = obj.get("sig") or obj.get("signature")
    
    # If not found, try nested structure (obj.sigs[0])
    if sig is None:
        sigs = obj.get("sigs")
        if isinstance(sigs, list) and len(sigs) > 0:
            sig = sigs[0]
    
    if not isinstance(sig, dict):
        raise rpc_errors.InvalidParams("Missing 'sig' object")
    
    alg_id = sig.get("algId") or sig.get("alg_id") or sig.get("alg")
    if alg_id is None:
        raise rpc_errors.InvalidParams("Missing 'sig.algId'")
    
    # Allow str or int for alg_id
    if isinstance(alg_id, str) and _ALG_ID is not None:
        # Try to map alg_name to alg_id if it's a string
        try:
            if alg_id in _ALG_ID:
                alg_id = _ALG_ID[alg_id]
            else:
                # Try parsing as int
                alg_id = int(alg_id, 0)
        except Exception:
            # leave as str; will be handled by verification
            pass
    
    pub = sig.get("pubkey") or sig.get("pub") or sig.get("pk")
    s = sig.get("sig") or sig.get("signature")
    if pub is None or s is None:
        raise rpc_errors.InvalidParams("Missing 'sig.pubkey' or 'sig.sig'")
    return (
        alg_id,
        _b(pub) if isinstance(pub, str) else bytes(pub),
        _b(s) if isinstance(s, str) else bytes(s),
    )


def _extract_chain_id(tx_like: t.Any, obj: dict) -> int:
    """
    Extract chain_id from transaction object.
    
    Handles various structure formats:
    - Flat: obj.chainId or obj.chain_id
    - Nested: obj.tx.chainId or obj.body.chainId
    - Dataclass: tx_like.chain_id or tx_like.chainId
    
    Returns
    -------
    int
        The extracted chain_id
    
    Raises
    ------
    rpc_errors.InvalidParams
        If chain_id cannot be found
    """
    # Try dataclass attributes first
    if hasattr(tx_like, "chain_id"):
        return int(tx_like.chain_id)
    if hasattr(tx_like, "chainId"):
        return int(tx_like.chainId)

    # Prefer the signed body if present (authoritative)
    if "body" in obj and isinstance(obj["body"], dict):
        body_obj = obj["body"]
        cid = body_obj.get("chainId") or body_obj.get("chain_id")
        if cid is not None:
            return int(cid)

    # Try flat structure
    cid = obj.get("chainId") or obj.get("chain_id")

    # If not found, try nested structures
    if cid is None and "tx" in obj and isinstance(obj["tx"], dict):
        tx_obj = obj["tx"]
        cid = tx_obj.get("chainId") or tx_obj.get("chain_id")

    if cid is None:
        raise rpc_errors.InvalidParams("Transaction missing chain_id")
    
    return int(cid)


def _validate_chain_id(obj: dict) -> int:
    """Validate chainId against node expectation and return the value used."""

    want = _chain_id_required()

    # Use shared extraction logic
    try:
        cid = _extract_chain_id(obj, obj)
    except rpc_errors.InvalidParams:
        cid = None
    
    # Debug logging to diagnose chainId extraction
    log.debug(
        "ChainId validation: extracted=%s, expected=%s, envelope_keys=%s",
        cid,
        want,
        list(obj.keys()) if isinstance(obj, dict) else "not-dict",
    )
    
    if cid is None:
        # ChainId is required in all transactions
        log.warning(
            "ChainId missing in transaction envelope: keys=%s, nested_keys=%s",
            list(obj.keys()),
            list(obj.get("tx", {}).keys()) if isinstance(obj.get("tx"), dict) else None,
        )
        raise rpc_errors.ChainIdMismatch(
            got=0,  # Use 0 to indicate missing chain ID
            expected=want
        )
    if int(cid) != int(want):
        log.warning(
            "ChainId mismatch: got=%s, expected=%s",
            int(cid),
            int(want),
        )
        raise rpc_errors.ChainIdMismatch(got=int(cid), expected=int(want))

    return int(cid)


def _verify_pq_signature(tx_like: t.Any, obj: dict, *, chain_id: int) -> None:
    if _pq_verify is None:
        raise rpc_errors.InternalError("PQ verification unavailable")
    alg_id, pub, sig = _extract_sig(obj)

    # Get the raw message (CBOR body) to verify
    # Always derive the sign-bytes from the decoded envelope object rather than
    # any dataclass representation. Some dataclass constructors add default
    # fields (e.g., gasPrice/accessList) that were not present in the signed
    # body, which would change the CBOR encoding and cause verification to
    # fail even with a valid signature. The decoded `obj` retains the exact
    # body that was signed by the CLI/SDK, so we canonicalize that here.
    candidates = _collect_sign_bytes(obj)
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
    except Exception:  # pragma: no cover - best effort logging
        pass
    
    # Map alg_id to alg_name for logging
    alg_name_for_log = f"alg_0x{alg_id:02x}" if isinstance(alg_id, int) else str(alg_id)
    if _ALG_NAME is not None and isinstance(alg_id, int):
        alg_name_for_log = _ALG_NAME.get(alg_id, alg_name_for_log)
    
    # Debug logging (matches CLI format)
    log.debug(
        "PQ signature verification: alg_id=%s, pubkey_len=%d, sig_len=%d, msg_len=%d, chain_id=%d (msg_source=%s)",
        alg_id,
        len(pub),
        len(sig),
        len(msg),
        chain_id,
        msg_label,
    )
    log.debug(
        "PQ SIGNATURE VERIFY DEBUG: algorithm=%s (id=%s), pubkey_len=%d, sig_len=%d, message_len=%d, message_prefix=%s, chain_id=%d",
        alg_name_for_log,
        alg_id,
        len(pub),
        len(sig),
        len(msg),
        msg[:16].hex() if len(msg) >= 16 else msg.hex(),
        chain_id,
    )
    
    # Construct a Signature envelope for verify_detached
    # The pq.py.verify API expects a Signature dataclass with alg_id, alg_name, domain, prehash, sig
    try:
        sig_env, alg_name = _build_sig_env(alg_id, sig)

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
        
        # Call verify_detached with the signature envelope and chain_id
        # verify_detached signature: (msg: bytes, sig: Signature, pk: bytes, chain_id: int, **kwargs) -> bool
        ok, used_label, verify_errors = _verify_pq_candidates(
            candidates, sig_env, pub, chain_id=chain_id
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
        # Fallback error for unexpected issues
        log.error("PQ signature verification setup failed: %s", e, exc_info=True)
        raise rpc_errors.InternalError(f"PQ signature verification setup failed: {e}")
    
    if not ok:
        # Enhanced error logging for debugging
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
        raise rpc_errors.BadSignature("Invalid post-quantum signature: verification failed")


def _decode_tx(raw: bytes) -> tuple[t.Any, dict]:
    if _cbor_loads is None:
        raise rpc_errors.InternalError("CBOR decoder unavailable")
    obj = _cbor_loads(raw)
    if _Tx is not None:
        try:
            # Try friendly constructors if present
            if hasattr(_Tx, "from_obj"):
                tx = _Tx.from_obj(obj)  # type: ignore[attr-defined]
            elif hasattr(_Tx, "from_dict"):
                tx = _Tx.from_dict(obj)  # type: ignore[attr-defined]
            else:
                tx = _Tx(**obj)  # type: ignore[call-arg]
            return tx, obj
        except Exception:
            # Fall back to dict shape
            pass
    if not isinstance(obj, dict):
        raise rpc_errors.InvalidParams("CBOR did not decode to a Tx object")
    return obj, obj


def _tx_view(
    tx: t.Any,
    obj: dict,
    *,
    pending: bool,
    block_hash: bytes | None = None,
    block_number: int | None = None,
    tx_index: int | None = None,
) -> dict:
    # Handle both flat and nested tx structures
    # If obj has 'tx' key, it's a nested structure
    tx_obj = obj.get("tx", obj) if isinstance(obj, dict) else obj
    
    # Extract sender address
    # First, try to get bech32m address from signature (for pending txs with sigs)
    _from = _extract_sender_address(obj)
    
    # Fallback to raw bytes from transaction structure
    if _from is None:
        _from = tx_obj.get("from") or tx_obj.get("sender")
        if _from is None and hasattr(tx, "unsigned"):
            # tx is a Tx dataclass, get sender from unsigned
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
    
    # Handle gas - can be a dict {'limit': ..., 'price': ...} or direct values
    gas_obj = tx_obj.get("gas")
    if isinstance(gas_obj, dict):
        gas = gas_obj.get("limit")
        tip = gas_obj.get("price")
    else:
        gas = gas_obj or tx_obj.get("gasLimit")
        tip = tx_obj.get("tip") or tx_obj.get("gasPrice")
    
    if gas is None and hasattr(tx, "unsigned"):
        gas = getattr(tx.unsigned, "gas_limit", None)
    if gas is None:
        gas = getattr(tx, "gas_limit", None)
    
    if tip is None and hasattr(tx, "unsigned"):
        tip = getattr(tx.unsigned, "gas_price", None)
    if tip is None:
        tip = getattr(tx, "tip", None)
    # Handle payload - can be a dict {'t': type, 'v': {actual payload}} or direct values
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
    
    # Compute hash - use the txid() method if available (for Tx dataclass)
    if hasattr(tx, "txid") and callable(getattr(tx, "txid")):
        hash_hex = _hex(tx.txid()) or ""
    else:
        # Fallback: hash the full obj
        hash_hex = _hex(_sha3_256(_cbor_dumps(obj))) or "" if _cbor_dumps else ""
    v = {
        "hash": hash_hex,
        "from": _hex(_from) if isinstance(_from, (bytes, bytearray)) else _from,
        "to": _hex(to) if isinstance(to, (bytes, bytearray)) else to,
        "nonce": int(nonce) if nonce is not None else None,
        "gas": int(gas) if gas is not None else None,
        "tip": int(tip) if tip is not None else None,
        "value": int(value) if value is not None else None,
        "data": _hex(data) if isinstance(data, (bytes, bytearray)) else data,
        "blockHash": (
            None
            if pending
            else (
                _hex(block_hash)
                if isinstance(block_hash, (bytes, bytearray))
                else block_hash
            )
        ),
        "blockNumber": (
            None
            if pending
            else (int(block_number) if block_number is not None else None)
        ),
        "transactionIndex": (
            None if pending else (int(tx_index) if tx_index is not None else None)
        ),
    }
    return {k: v for k, v in v.items() if v is not None}


def _pending_put(tx_hash_hex: str, raw: bytes) -> None:
    # Prefer the real pool
    if _PEND is not None and hasattr(_PEND, "add_raw"):
        _PEND.add_raw(tx_hash_hex, raw)  # type: ignore[attr-defined]
        return
    if _PEND is not None and hasattr(_PEND, "add"):
        _PEND.add(tx_hash_hex, raw)  # type: ignore[attr-defined]
        return
    # Fallback (dev)
    _FALLBACK_PENDING[tx_hash_hex] = raw


def _pending_get(tx_hash_hex: str) -> bytes | None:
    if _PEND is not None and hasattr(_PEND, "get_raw"):
        return _PEND.get_raw(tx_hash_hex)  # type: ignore[attr-defined]
    if _PEND is not None and hasattr(_PEND, "get"):
        return _PEND.get(tx_hash_hex)  # type: ignore[attr-defined]
    return _FALLBACK_PENDING.get(tx_hash_hex)


def _lookup_persisted_tx(
    tx_hash_hex: str,
) -> tuple[dict | None, int | None, int | None, bytes | None]:
    """
    Return (obj_view, block_number, tx_index, block_hash) if found in DB; otherwise (None, None, None, None).
    """
    # Use state_service if exposed
    svc = getattr(deps, "state_service", None)
    if svc is not None:
        # Expect methods like: get_transaction_by_hash, get_receipt_by_hash, etc.
        if hasattr(svc, "get_transaction_by_hash"):
            tx_rec = svc.get_transaction_by_hash(tx_hash_hex)  # type: ignore
            if tx_rec:
                # tx_rec is expected to have (tx, block_number, index, block_hash)
                tx_obj = tx_rec.get("tx") or tx_rec
                block_number = tx_rec.get("blockNumber")
                index = tx_rec.get("transactionIndex")
                b_hash = tx_rec.get("blockHash")
                if isinstance(b_hash, str):
                    b_hash = _b(b_hash)
                # tx_obj might be raw CBOR or dict or dataclass
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
    
    # Try lower-level deps if present
    if hasattr(deps, "get_tx_by_hash"):
        rec = deps.get_tx_by_hash(tx_hash_hex)  # type: ignore
        if rec:
            # Best effort projection
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
        "    \"body\": { ...transaction fields... },\n"
        "    \"sig\": {\n"
        "      \"algId\": <int>,     # PQ algorithm ID\n"
        "      \"pubkey\": <bytes>,  # Public key bytes\n"
        "      \"sig\": <bytes>      # Signature bytes\n"
        "    }\n"
        "  }\n"
        "Alternative envelope with sigs array is also supported:\n"
        "  { \"body\": {...}, \"sigs\": [{\"algId\": ..., \"pubkey\": ..., \"sig\": ...}] }\n"
    ),
    aliases=("tx_sendRawTransaction",),
)
def tx_send_raw_transaction(rawTx: str) -> str:
    # Accept hex only for now
    if not isinstance(rawTx, str):
        raise rpc_errors.InvalidParams("rawTx must be a hex string")
    if rawTx.startswith("0b:"):
        raise rpc_errors.InvalidParams("base64 not supported yet; send hex (0x…)")
    
    raw = _b(rawTx)
    log.debug("tx.sendRawTransaction: decoding %d CBOR bytes", len(raw))
    tx_like, obj = _decode_tx(raw)
    
    # Log the decoded structure for debugging
    log.debug(
        "tx.sendRawTransaction: decoded envelope type=%s, keys=%s",
        type(tx_like).__name__ if hasattr(type(tx_like), "__name__") else type(tx_like),
        list(obj.keys()) if isinstance(obj, dict) else "not-dict",
    )
    
    # Basic chainId check and reuse the validated value for signature verification
    chain_id = _validate_chain_id(obj)

    # PQ signature verify
    _verify_pq_signature(tx_like, obj, chain_id=chain_id)
    
    # Compute hash from the original CBOR bytes to ensure consistency
    # Per spec: TxID = sha3_256(CBOR(SignedTxMap))
    tx_hash_hex = _hex(_sha3_256(raw)) or ""
    
    # Duplicate suppression: if already in pending/persisted, return hash (idempotent)
    if _pending_get(tx_hash_hex) is not None:
        return tx_hash_hex
    persisted, *_ = _lookup_persisted_tx(tx_hash_hex)
    if persisted is not None:
        return tx_hash_hex
    
    # Admit to pending pool (stateless checks already done here)
    _pending_put(tx_hash_hex, raw)
    
    # Notify WS hub (best-effort)
    try:
        if hasattr(deps, "ws_broadcast_pending"):
            deps.ws_broadcast_pending(tx_hash_hex, obj)  # type: ignore
    except Exception:
        pass
    
    return tx_hash_hex


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
    tx_like, obj = _decode_tx(raw)
    chain_id = _validate_chain_id(obj)

    alg_id, pub, sig = _extract_sig(obj)
    candidates = _collect_sign_bytes(obj)

    sig_env, alg_name = _build_sig_env(alg_id, sig)

    ok, used_label, verify_errors = _verify_pq_candidates(
        candidates, sig_env, pub, chain_id=chain_id
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
    
    # 1) Check pending pool
    raw = _pending_get(tx_hash_hex)
    if raw is not None and _cbor_loads is not None:
        obj = _cbor_loads(raw)
        tx_like = obj
        return _tx_view(
            tx_like, obj if isinstance(obj, dict) else _dcd(obj), pending=True
        )
    
    # 2) Check persisted DB via deps/state_service
    view, *_etc = _lookup_persisted_tx(tx_hash_hex)
    if view is not None:
        return view
    
    # 3) Not found
    return None


@method(
    "tx.getTransactionReceipt",
    desc="Get transaction receipt by hash",
    aliases=("tx_getTransactionReceipt",),
)
def tx_get_transaction_receipt(txHash: str) -> t.Optional[dict]:
    """
    Retrieve the receipt for a transaction by its hash.
    
    Returns None if the transaction is still pending or not found.
    Returns a receipt object if the transaction has been included in a block.
    
    Expected receipt structure (when implemented):
    {
        "transactionHash": "0x...",
        "blockHash": "0x...",
        "blockNumber": int,
        "transactionIndex": int,
        "from": "anim1...",
        "to": "anim1..." or null,
        "gasUsed": int,
        "status": int (1 for success, 0 for failure),
        "logs": [...],
        "logsBloom": "0x..."
    }
    """
    if not isinstance(txHash, str):
        raise rpc_errors.InvalidParams("txHash must be hex string")
    
    # Validate hex format
    tx_hash_hex = txHash.strip().lower()
    if not tx_hash_hex.startswith("0x"):
        tx_hash_hex = "0x" + tx_hash_hex
    
    # Validate it's a valid hex string after 0x prefix
    try:
        _ = bytes.fromhex(tx_hash_hex[2:])
    except (ValueError, TypeError):
        raise rpc_errors.InvalidParams("txHash must be valid hex string")
    
    # Receipts are only available for persisted transactions (not pending)
    # Check if it's in the pending pool first
    raw = _pending_get(tx_hash_hex)
    if raw is not None:
        # Transaction is still pending, no receipt yet
        return None
    
    # Try to get the receipt from the persisted DB via deps/state_service
    try:
        ctx = deps.get_ctx()
        if hasattr(ctx, "block_db"):
            # Stub implementation: Query block_db.get_receipt(tx_hash_hex) to retrieve:
            # - Receipt object containing execution results (gas used, status, logs)
            # - Block information (block hash, number, tx index)
            # - From/To addresses extracted from the original transaction
            # When implemented, this should return a properly formatted receipt dict
            # matching the structure documented in the docstring above.
            return None
    except Exception:
        pass
    
    # Not found or not yet mined
    return None