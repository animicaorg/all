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
    from omni_sdk.tx.encode import \
        sign_bytes as _sdk_sign_bytes  # type: ignore
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
    from rpc.state_service import \
        parse_address as _parse_address  # type: ignore
except Exception:  # pragma: no cover
    _parse_address = None  # type: ignore

# Bech32m address encoding
try:
    from pq.py.address import \
        address_from_pubkey as _address_from_pubkey  # type: ignore
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
            got=0, expected=want  # Use 0 to indicate missing chain ID
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
        # Allow developers to bypass PQ verification when liboqs/omni PQ backend
        # is unavailable (e.g., during local bring-up or in minimal CI images).
        if _PQ_VERIFY_OPTIONAL:
            log.warning(
                "PQ verification unavailable; skipping due to ANIMICA_PQ_VERIFY_OPTIONAL/ANIMICA_SKIP_PQ_VERIFY",
            )
            return

        raise rpc_errors.InternalError(
            "PQ verification unavailable",
            **_error_data(
                "pq_verify",
                RuntimeError(
                    "Missing pq.py.verify backend (animica-pq not installed?)"
                ),
                "_verify_pq_signature",
                "Ensure animica-pq is installed in the node container or set ANIMICA_PQ_VERIFY_OPTIONAL=1 to bypass in dev",
            ),
        )
    alg_id, pub, sig, domain, prehash = _extract_sig(obj)

    # Get the raw message (CBOR body) to verify
    # Always derive the sign-bytes from the decoded envelope object rather than
    # any dataclass representation. Some dataclass constructors add default
    # fields (e.g., gasPrice/accessList) that were not present in the signed
    # body, which would change the CBOR encoding and cause verification to
    # fail even with a valid signature. The decoded `obj` retains the exact
    # body that was signed by the CLI/SDK, so we canonicalize that here.
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
    except Exception:  # pragma: no cover - best effort logging
        pass

    # Map alg_id to alg_name for logging
    alg_name_for_log = f"alg_0x{alg_id:02x}" if isinstance(alg_id, int) else str(alg_id)
    if _ALG_NAME is not None and isinstance(alg_id, int):
        alg_name_for_log = _ALG_NAME.get(alg_id, alg_name_for_log)

    # Debug logging (matches CLI format)
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

        # Call verify_detached with the signature envelope and chain_id
        # verify_detached signature: (msg: bytes, sig: Signature, pk: bytes, chain_id: int, **kwargs) -> bool
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
    """
    Decode a raw CBOR transaction envelope.

    Returns:
        tuple[tx, obj]: (Tx instance or dict, dict envelope with hash/raw added)

    The returned dict always includes:
    - "hash": hex string of tx hash (sha3_256 of raw CBOR)
    - "raw": raw CBOR bytes (for recomputing hash or re-serialization)
    """
    if _cbor_loads is None:
        raise rpc_errors.InternalError("CBOR decoder unavailable")
    obj = _cbor_loads(raw)

    # Compute tx hash (canonical: sha3_256(raw_cbor_bytes))
    tx_hash_hex = _hex(_sha3_256(raw)) or ""

    # Ensure obj is a dict so we can add hash/raw fields
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

    # Add hash and raw to the envelope (non-destructive - doesn't modify obj in-place)
    enriched_obj = dict(obj)
    enriched_obj["hash"] = tx_hash_hex
    enriched_obj["raw"] = raw

    # Try to construct Tx instance if possible
    if _Tx is not None:
        try:
            # Try friendly constructors if present
            if hasattr(_Tx, "from_obj"):
                tx = _Tx.from_obj(obj)  # type: ignore[attr-defined]
            elif hasattr(_Tx, "from_dict"):
                tx = _Tx.from_dict(obj)  # type: ignore[attr-defined]
            else:
                tx = _Tx(**obj)  # type: ignore[call-arg]
            return tx, enriched_obj
        except Exception:
            # Fall back to dict shape
            pass

    return enriched_obj, enriched_obj


def _validate_sufficient_balance(obj: dict) -> None:
    """
    Validate that the sender has sufficient balance to cover the transaction value + gas fees.

    This validation is performed before adding the transaction to the mempool. It is skipped
    in the following scenarios:
    - Sender address cannot be determined from the transaction signature
    - State DB is not available in the RPC context
    - Address parsing fails
    - Balance query methods are not available on state_db

    If balance check fails due to unexpected errors (not InsufficientFunds), the validation
    is skipped and the transaction proceeds. This ensures that transient issues don't block
    valid transactions.

    Raises:
        rpc_errors.InsufficientFunds: If sender balance is insufficient
    """
    # Extract transaction fields
    tx_obj = obj.get("body", obj.get("tx", obj))

    # Extract sender address (32-byte digest)
    sender_addr = _extract_sender_address(obj)
    if sender_addr is None:
        # If we can't determine sender, skip balance check (signature validation will catch this)
        log.debug(
            "_validate_sufficient_balance: cannot determine sender address, skipping"
        )
        return

    # Extract value and gas parameters
    value = tx_obj.get("value", 0)
    gas_limit = (
        tx_obj.get("gasLimit") or tx_obj.get("gas_limit") or tx_obj.get("gas", 0)
    )
    max_fee = (
        tx_obj.get("maxFee")
        or tx_obj.get("max_fee")
        or tx_obj.get("gasPrice")
        or tx_obj.get("gas_price", 0)
    )

    # Convert to int
    value = int(value) if value is not None else 0
    gas_limit = int(gas_limit) if gas_limit is not None else 0
    max_fee = int(max_fee) if max_fee is not None else 0

    # Calculate total required
    max_gas_cost = gas_limit * max_fee
    required = value + max_gas_cost

    # Query sender balance from state
    try:
        ctx = deps.get_ctx()
        if not hasattr(ctx, "state_db") or ctx.state_db is None:
            log.debug("_validate_sufficient_balance: state_db not available, skipping")
            return

        state_db = ctx.state_db

        # Convert bech32 address to 32-byte digest for state lookup
        # sender_addr is already a bech32 address string from _extract_sender_address
        if _parse_address is None:
            log.debug(
                "_validate_sufficient_balance: parse_address not available, skipping"
            )
            return

        try:
            sender_bytes = _parse_address(sender_addr)
        except Exception as e:
            log.debug(
                "_validate_sufficient_balance: failed to parse sender address %s: %s",
                sender_addr,
                e,
            )
            return

        # Get balance using state_db methods
        balance = None
        for method_name in ("get_balance", "read_balance", "balance_of"):
            if hasattr(state_db, method_name):
                try:
                    balance = int(getattr(state_db, method_name)(sender_bytes))
                    break
                except Exception as e:
                    log.debug(
                        "_validate_sufficient_balance: %s failed: %s", method_name, e
                    )
                    continue

        if balance is None:
            log.debug(
                "_validate_sufficient_balance: could not retrieve balance, skipping"
            )
            return

        # Check if balance is sufficient
        if balance < required:
            shortfall = required - balance
            raise rpc_errors.InsufficientFunds(
                required=required,
                available=balance,
            )
    except rpc_errors.InsufficientFunds:
        raise
    except Exception as e:
        # Don't fail tx submission if balance check fails for unexpected reasons
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
    # Handle both flat and nested tx structures
    # RPC envelope: {body: {...}, sig/sigs: ...}
    # Core envelope: {tx: {...}, sigs: [...]}
    # Flat: {...fields directly...}
    if isinstance(obj, dict):
        if "body" in obj:
            # RPC envelope format
            tx_obj = obj["body"]
        elif "tx" in obj:
            # Core envelope format
            tx_obj = obj["tx"]
        else:
            # Flat format
            tx_obj = obj
    else:
        tx_obj = obj

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

    # Extract maxFee (distinct from tip/gasPrice)
    max_fee = tx_obj.get("maxFee") or tx_obj.get("max_fee")
    if max_fee is None and tip is not None:
        # Fallback: use tip as maxFee if maxFee not present
        max_fee = tip

    # Extract chainId
    chain_id = tx_obj.get("chainId") or tx_obj.get("chain_id")
    if chain_id is None and hasattr(tx, "unsigned"):
        chain_id = getattr(tx.unsigned, "chain_id", None) or getattr(
            tx.unsigned, "chainId", None
        )
    if chain_id is None:
        chain_id = getattr(tx, "chain_id", None) or getattr(tx, "chainId", None)
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
        "gasLimit": int(gas) if gas is not None else None,  # Alias for compatibility
        "tip": int(tip) if tip is not None else None,
        "gasPrice": int(tip) if tip is not None else None,  # Alias for compatibility
        "maxFee": int(max_fee) if max_fee is not None else None,
        "value": int(value) if value is not None else None,
        "chainId": int(chain_id) if chain_id is not None else None,
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
    _FALLBACK_PENDING_TS[tx_hash_hex] = time.time()


def _get_mempool_service():
    try:
        ctx = deps.get_ctx()
    except Exception:
        return None
    return getattr(ctx, "mempool", None)


def _gossip_tx_to_peers(raw_tx: bytes) -> None:
    """
    Gossip a transaction to connected P2P peers via TxRelayHandler.

    This function attempts to publish the transaction to the 'txs' gossip topic
    on the P2P network. It's called after a transaction is successfully
    admitted to the local pending pool via RPC.

    Args:
        raw_tx: Raw CBOR-encoded transaction bytes
    """
    try:
        # Get P2P service from RPC context
        ctx = deps.get_ctx()
        if not hasattr(ctx, "p2p_service") or ctx.p2p_service is None:
            log.debug("P2P service not available; tx not gossiped")
            return

        p2p_service = ctx.p2p_service

        # Preferred: production P2PService exposes relay_tx() which performs INV/GETDATA gossip.
        if hasattr(p2p_service, "relay_tx") and callable(
            getattr(p2p_service, "relay_tx")
        ):
            try:
                loop = asyncio.get_running_loop()
                asyncio.ensure_future(p2p_service.relay_tx(raw_tx), loop=loop)  # type: ignore[call-arg]
                log.debug("Scheduled tx relay via P2PService.relay_tx()")
                return
            except RuntimeError:
                log.debug("No running event loop; tx not relayed to peers")
                return

        # Use TxRelayHandler if available (preferred path)
        if hasattr(p2p_service, "tx_relay_handler"):
            tx_relay_handler = p2p_service.tx_relay_handler
            try:
                loop = asyncio.get_running_loop()
                asyncio.ensure_future(
                    tx_relay_handler.publish_local_tx(raw_tx), loop=loop
                )
                log.debug("Scheduled tx gossip via TxRelayHandler")
                return
            except RuntimeError:
                log.debug("No running event loop; tx not gossiped to peers")
                return
            except AttributeError:
                # Handler exists but publish_local_tx method missing; fall through to legacy path
                log.debug(
                    "TxRelayHandler missing publish_local_tx; falling back to direct gossip"
                )
                pass

        # Fallback: direct gossip engine access (legacy path)
        if not hasattr(p2p_service, "gossip"):
            log.debug("P2P gossip engine not available; tx not gossiped")
            return

        gossip_engine = p2p_service.gossip

        if not hasattr(gossip_engine, "publish") or not callable(gossip_engine.publish):
            log.debug("P2P gossip publish method not available; tx not gossiped")
            return

        # Build the proper topic path using Topics helper
        try:
            from p2p.gossip import topics as gossip_topics

            chain_id = _chain_id_required()
            tx_topic = gossip_topics.txs(chain_id)
            topic_path = tx_topic.path
        except Exception:
            # Fallback to bare topic string
            topic_path = "txs"
            log.debug("Using fallback topic path 'txs'")

        # Publish to gossip mesh
        try:
            loop = asyncio.get_running_loop()
            asyncio.ensure_future(gossip_engine.publish(topic_path, raw_tx), loop=loop)
            log.debug("Scheduled tx gossip to topic %s", topic_path)
        except RuntimeError:
            log.debug("No running event loop; tx not gossiped to peers")

    except Exception as e:
        log.debug("Failed to gossip tx to P2P peers: %s", e)


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
    """
    Return (obj_view, block_number, tx_index, block_hash) if found in DB; otherwise (None, None, None, None).
    """
    # Try block_db.get_transaction_by_hash first (preferred path)
    ctx = deps.get_ctx()
    if hasattr(ctx, "block_db") and ctx.block_db is not None:
        block_db = ctx.block_db
        if hasattr(block_db, "get_transaction_by_hash"):
            try:
                # Convert hex hash to bytes
                tx_hash_bytes = _b(tx_hash_hex)
                result = block_db.get_transaction_by_hash(tx_hash_bytes)
                if result is not None:
                    height, idx, block_hash, tx_obj = result
                    # Convert Tx dataclass to dict for _tx_view
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

    # Use state_service if exposed (fallback)
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

    # Try lower-level deps if present (last resort)
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
        '    "body": { ...transaction fields... },\n'
        '    "sig": {\n'
        '      "algId": <int>,     # PQ algorithm ID\n'
        '      "pubkey": <bytes>,  # Public key bytes\n'
        '      "sig": <bytes>      # Signature bytes\n'
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
    except Exception as e:  # pragma: no cover - top-level guard
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
    # Accept hex only for now
    if not isinstance(rawTx, str):
        raise rpc_errors.InvalidParams("rawTx must be a hex string")
    if rawTx.startswith("0b:"):
        raise rpc_errors.InvalidParams("base64 not supported yet; send hex (0x…)")

    try:
        raw = _b(rawTx)
    except Exception as e:
        log.error(
            "tx.sendRawTransaction: hex decode failed, len=%d",
            len(rawTx) if rawTx else 0,
        )
        TX_VALIDATION_FAILURES.labels(reason="hex_decode_failed").inc()
        raise rpc_errors.InvalidTx(
            "rawTx decode failed",
            **_error_data(
                "decode",
                e,
                "tx.sendRawTransaction._b",
                "Ensure rawTx is 0x-prefixed hex",
            ),
        ) from e

    log.debug("tx.sendRawTransaction: decoding %d CBOR bytes", len(raw))

    try:
        tx_like, obj = _decode_tx(raw)
    except rpc_errors.RpcError:
        raise
    except Exception as e:
        log.error(
            "tx.sendRawTransaction: CBOR decode failed, raw_len=%d",
            len(raw),
            exc_info=True,
        )
        TX_VALIDATION_FAILURES.labels(reason="cbor_decode_failed").inc()
        raise rpc_errors.InvalidTx(
            "Transaction decode failed",
            **_error_data(
                "decode",
                e,
                "_decode_tx",
                "Ensure rawTx is CBOR {body, sig}",
            ),
        ) from e

    # Log the decoded structure for debugging
    log.info(
        "tx.sendRawTransaction: decoded envelope type=%s, keys=%s, body_keys=%s",
        type(tx_like).__name__ if hasattr(type(tx_like), "__name__") else type(tx_like),
        list(obj.keys()) if isinstance(obj, dict) else "not-dict",
        (
            list(obj.get("body", {}).keys())
            if isinstance(obj, dict) and "body" in obj
            else "no-body"
        ),
    )

    try:
        # Basic chainId check and reuse the validated value for signature verification
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

        # PQ signature verify
        try:
            _verify_pq_signature(tx_like, obj, chain_id=chain_id)
        except rpc_errors.BadSignature as e:
            log.warning(
                "tx.sendRawTransaction: PQ signature invalid, chain_id=%d", chain_id
            )
            TX_VALIDATION_FAILURES.labels(reason="signature_invalid").inc()
            raise

        # Compute hash from the original CBOR bytes to ensure consistency
        # Per spec: TxID = sha3_256(CBOR(SignedTxMap))
        tx_hash_hex = _hex(_sha3_256(raw)) or ""

        log.info(
            "tx.sendRawTransaction: validation passed, tx_hash=%s, chain_id=%d",
            tx_hash_hex,
            chain_id,
        )

        # Balance validation: check if sender has sufficient funds
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

        # Duplicate suppression: if already in pending/persisted, return hash (idempotent)
        # Note: Duplicates are not validation failures - they're expected and idempotent
        mempool_service = _get_mempool_service()
        if mempool_service is not None and mempool_service.has_hash(tx_hash_hex):
            log.info(
                "tx.sendRawTransaction: duplicate tx (already in mempool), hash=%s",
                tx_hash_hex,
            )
            return tx_hash_hex
        if _pending_get(tx_hash_hex) is not None:
            log.info(
                "tx.sendRawTransaction: duplicate tx (already pending), hash=%s",
                tx_hash_hex,
            )
            return tx_hash_hex
        persisted, *_ = _lookup_persisted_tx(tx_hash_hex)
        if persisted is not None:
            log.info(
                "tx.sendRawTransaction: duplicate tx (already persisted), hash=%s",
                tx_hash_hex,
            )
            return tx_hash_hex

        tx_obj = tx_like
        if _Tx is not None and not isinstance(tx_like, _Tx) and isinstance(obj, dict):
            try:
                tx_obj = _Tx.from_obj(obj)  # type: ignore[attr-defined]
            except Exception:
                tx_obj = None

        # Admit to mempool (preferred) or pending pool fallback
        if mempool_service is not None and tx_obj is not None:
            try:
                mempool_size_before = (
                    mempool_service.count() if hasattr(mempool_service, "count") else "?"
                )
                log.info(
                    "tx.sendRawTransaction: mempool_service available, path=service.submit, hash=%s, mempool_id=%s, size_before=%s",
                    tx_hash_hex,
                    id(mempool_service),
                    mempool_size_before,
                )
                mempool_service.submit(tx=tx_obj, raw=raw, tx_hash_hex=tx_hash_hex)
                
                mempool_size_after = (
                    mempool_service.count() if hasattr(mempool_service, "count") else "?"
                )
                log.info(
                    "tx.sendRawTransaction: submit() completed, hash=%s, size_after=%s",
                    tx_hash_hex,
                    mempool_size_after,
                )
                
                # CRITICAL: Verify tx is actually in mempool before returning success
                has_tx = mempool_service.has_hash(tx_hash_hex)
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
                            "hint": "pool.add() may have silently failed",
                        },
                    )
                
                log.info(
                    "tx.sendRawTransaction: VERIFIED tx in mempool, hash=%s",
                    tx_hash_hex,
                )
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
            log.info(
                "tx.sendRawTransaction: tx admitted to pending pool, hash=%s",
                tx_hash_hex,
            )

        # Notify WS hub (best-effort)
        try:
            if hasattr(deps, "ws_broadcast_pending"):
                deps.ws_broadcast_pending(tx_hash_hex, obj)  # type: ignore
        except Exception:
            pass

        # Gossip to P2P peers (best-effort)
        try:
            _gossip_tx_to_peers(raw)
        except Exception as e:
            log.debug("Failed to gossip tx to peers: %s", e)

        return tx_hash_hex
    except rpc_errors.BadSignature as e:
        # Normalize PQ failures to a consistent error code/message
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
            **_error_data(
                "decode",
                e,
                "tx.decodeRawTransaction._b",
                "Ensure rawTx is 0x-prefixed hex",
            ),
        ) from e

    log.debug("tx.decodeRawTransaction: decoding %d CBOR bytes", len(raw))

    try:
        tx_like, obj = _decode_tx(raw)
    except rpc_errors.RpcError:
        raise
    except Exception as e:
        raise rpc_errors.InvalidTx(
            "Transaction decode failed",
            **_error_data(
                "decode",
                e,
                "_decode_tx",
                "Ensure rawTx is CBOR {body, sig}",
            ),
        ) from e

    decoded_obj = obj if isinstance(obj, dict) else _dcd(obj)
    return {
        "len": len(raw),
        "type": (
            type(tx_like).__name__
            if hasattr(type(tx_like), "__name__")
            else str(type(tx_like))
        ),
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
    tx_like, obj = _decode_tx(raw)
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

    # 1) Check pending pool
    raw = _pending_get(tx_hash_hex)
    if raw is not None and _cbor_loads is not None:
        log.debug(
            "tx.getTransactionByHash: found in pending pool, raw_len=%d", len(raw)
        )
        try:
            obj = _cbor_loads(raw)
            tx_like = obj
            view = _tx_view(
                tx_like, obj if isinstance(obj, dict) else _dcd(obj), pending=True
            )
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
            # Fall through to check persisted

    # 2) Check persisted DB via deps/state_service
    view, *_etc = _lookup_persisted_tx(tx_hash_hex)
    if view is not None:
        log.debug("tx.getTransactionByHash: found in persisted DB")
        return view

    # 3) Not found
    log.debug("tx.getTransactionByHash: tx not found, hash=%s", tx_hash_hex)
    return None


# NOTE: tx.getTransactionReceipt is registered in rpc/methods/receipt.py
# to avoid duplicate registration warnings. See receipt.py for implementation.
