from __future__ import annotations

from collections import OrderedDict
import hashlib
import json
import logging
import os
import errno
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from rpc import deps
from mempool.tx_hash import normalized_tx_bytes, tx_hash_hex as _tx_hash_hex
from core.utils.tx import normalize_tx_envelope, TxNormalizationError, coerce_int, normalize_tx_fields
from core.utils.address_codec import account_key_from_pubkey, account_key_from_raw, AccountKeyError
from core.utils.address import address_to_bytes
from mempool.config import MempoolConfig, load_config as load_mempool_config
from mempool.errors import (
    AdmissionError,
    Expired,
    FeeTooLow,
    InsufficientFundsPending,
    NonceGap,
    NonceTooLow,
    NotYetValid,
    PersistenceFailed,
    Replay,
    ReplacementUnsupported,
)
from mempool.pool import Pool, PoolConfig
from mempool.select import PendingTxEntry, select_for_block
from mempool.types import EffectiveFee, PoolTx, TxMeta
from mempool.accounting import estimate_max_spend
from mempool.rejects import MempoolReject, RejectReason, reject as mk_reject, REJECT_CODE

try:
    from core.types.tx import Tx  # type: ignore
except Exception:  # pragma: no cover - runtime fallback when core not available
    class Tx:  # type: ignore
        pass

log = logging.getLogger("animica.rpc.mempool")

try:
    from rpc.metrics import TX_LEGACY_GASLIMIT_DICT_TOTAL
except Exception:  # pragma: no cover
    class _Counter:
        def inc(self, *args, **kwargs):
            pass
    TX_LEGACY_GASLIMIT_DICT_TOTAL = _Counter()  # type: ignore[assignment]


def _normalize_reject(reason: str, message: str, context: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    r = (reason or "").strip() or "internal_error"
    m = message or "mempool admission failed"
    l = (m + " " + r).lower()
    if r == "admission_failed":
        if "signature" in l:
            r = "invalid_signature"
        elif "chain_id" in l or "chain id" in l:
            r = "chain_id_mismatch"
        elif "nonce" in l and "low" in l:
            r = "nonce_too_low"
        elif "nonce" in l and ("gap" in l or "high" in l):
            r = "nonce_gap"
        elif "insufficient" in l and "fund" in l:
            r = "insufficient_funds"
        elif "fee" in l or "gas price" in l:
            r = "fee_too_low"
        elif "known" in l or "duplicate" in l:
            r = "tx_already_known"
        elif "decode" in l or "format" in l or "normalization" in l:
            r = "invalid_format"
    if r == "replacement_unsupported":
        r = "nonce_conflict"
    if r == "insufficient_funds_pending":
        r = "insufficient_funds"
    return r, m, context



def _infer_bad_field_type_from_exception(tx: Any, exc: Exception) -> dict[str, Any] | None:
    msg = str(exc)
    if not isinstance(exc, TypeError) or "int() argument" not in msg:
        return None
    numeric_types = _tx_numeric_field_types(tx)
    dict_fields = [name for name, info in numeric_types.items() if info.get("type") == "dict"]
    if not dict_fields:
        return None
    field = dict_fields[0]
    info = numeric_types.get(field, {})
    keys: list[str] | None = None
    value = info.get("value")
    if isinstance(value, dict):
        keys = sorted(str(k) for k in value.keys())
    return {
        "reason": "bad_field_type",
        "field": field,
        "received_type": "dict",
        "received_keys": keys,
    }
def _tx_numeric_field_types(tx: Any) -> dict[str, dict[str, Any]]:
    body = _tx_body(tx)
    if not isinstance(body, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    def _add(name: str, value: Any) -> None:
        out[name] = {"type": type(value).__name__, "value": value}
    for field in ("chainId", "chain_id", "nonce", "value", "fee_reserved", "reserve_amount", "gasLimit", "maxFee", "validAfter", "validUntil"):
        if field in body:
            _add(field, body.get(field))
    gas = body.get("gas")
    if isinstance(gas, dict):
        for sub in ("price", "limit"):
            if sub in gas:
                _add(f"gas.{sub}", gas.get(sub))
    payload = body.get("payload")
    if isinstance(payload, dict):
        v = payload.get("v")
        if isinstance(v, dict) and "amount" in v:
            _add("payload.v.amount", v.get("amount"))
    return out



def log_exception(trace_id: str, tx: Any, exc: Exception, *, field_hint: str | None = None, field_value: Any | None = None) -> None:
    """Log full traceback and useful locals snapshot for admission failures."""
    numeric_types = _tx_numeric_field_types(tx)
    context = _tx_context_fields(tx)
    debug_tx = os.getenv("ANIMICA_DEBUG_TX", "0") == "1"
    debug_mempool = os.getenv("ANIMICA_DEBUG_MEMPOOL", "0") == "1"
    if field_hint is None and isinstance(exc, TypeError) and "int() argument" in str(exc):
        for fname, info in numeric_types.items():
            if info.get("type") == "dict":
                field_hint = fname
                field_value = info.get("value")
                break
    payload: dict[str, Any] = {
        "trace_id": trace_id,
        "error_class": exc.__class__.__name__,
        "error_message": str(exc),
        "context": context,
    }
    if field_hint is not None:
        payload["int_conversion_field"] = field_hint
    if field_value is not None:
        payload["int_conversion_value_preview"] = repr(field_value)[:160]
    if debug_tx:
        payload["numeric_types"] = numeric_types
    if debug_mempool or debug_tx:
        log.error("mempool admission exception %s", json.dumps(payload, sort_keys=True, default=str), exc_info=True)
    else:
        log.error(
            "mempool admission exception trace_id=%s class=%s message=%s",
            trace_id,
            exc.__class__.__name__,
            str(exc),
            exc_info=True,
        )
def _tx_context_fields(tx: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    out["tx_kind"] = _tx_kind(tx)
    try:
        nonce = _tx_nonce(tx)
        if nonce is not None:
            out["nonce"] = coerce_int("nonce", nonce)
    except Exception:
        pass
    try:
        cid = _tx_chain_id(tx)
        if cid is not None:
            out["chain_id"] = coerce_int("chain_id", cid)
    except Exception:
        pass
    body = tx.get("body") if isinstance(tx, dict) else None
    if isinstance(body, dict):
        for key in ("from", "to", "value", "fee", "fee_reserved", "reserve_amount"):
            if key in body:
                out[key] = body.get(key)
    if isinstance(tx, dict):
        sigs = tx.get("sigs")
        if isinstance(sigs, list) and sigs:
            s0 = sigs[0]
            if isinstance(s0, dict):
                alg = s0.get("algId") if "algId" in s0 else s0.get("alg_id")
                if alg is not None:
                    out["scheme_id"] = alg
                pub = s0.get("pubkey")
                sig = s0.get("sig")
                if isinstance(pub, (bytes, bytearray)):
                    out["pubkey_len"] = len(pub)
                if isinstance(sig, (bytes, bytearray)):
                    out["sig_len"] = len(sig)
    return out


def _hint_for_reason(reason: str) -> str:
    hints = {
        "invalid_signature": "check signer scheme/domain and keypair",
        "invalid_format": "ensure tx envelope is canonical CBOR",
        "chain_id_mismatch": "set --chain-id to node chain id",
        "insufficient_funds": "fund sender for value + fee reserve",
        "insufficient_funds_pending": "fund sender for value + fee reserve",
        "nonce_too_low": "retry without --nonce to use latest pending nonce",
        "nonce_too_high": "submit lower nonce transaction first",
        "nonce_gap": "submit missing lower nonce transaction first",
        "nonce_conflict": "wait for existing same-nonce tx or replace with policy",
        "fee_too_low": "increase --max-fee",
        "fee_reserved_invalid": "set non-negative reserve fields",
        "tx_already_known": "tx is already in mempool",
        "policy_reject": "inspect mempool policy and retry",
        "internal_error": "run `animica node logs --network <network>` and grep by trace_id",
    }
    return hints.get(reason, "check transaction fields and retry")


def _to_mempool_reject(*, reason: str, message: str, context: dict[str, Any], exc: Exception | None = None) -> MempoolReject:
    try:
        reason_enum = RejectReason(reason)
    except Exception:
        reason_enum = RejectReason.internal_error if exc is not None else RejectReason.policy_reject
    return mk_reject(
        reason_enum,
        message=message or "mempool admission failed",
        hint=_hint_for_reason(reason_enum.value),
        context=context,
        error_class=(exc.__class__.__name__ if exc is not None else None),
    )


def _build_replay_error(
    *, tx_hash_hex: str, sender_hex: str, replay_source: str
) -> Exception:
    """
    Build a replay exception that preserves typed replay semantics even when
    runtime class loading differences make Replay(...) keyword construction fail.
    """
    try:
        replay_exc = Replay(tx_hash=tx_hash_hex, sender=sender_hex)
        if hasattr(replay_exc, "context") and isinstance(replay_exc.context, dict):
            replay_exc.context.setdefault("replay_source", replay_source)
        return replay_exc
    except Exception:
        return AdmissionError(
            "replay detected",
            context={
                "reason": "replay",
                "tx_hash": tx_hash_hex,
                "sender": sender_hex,
                "replay_source": replay_source,
            },
        )


@dataclass(frozen=True)
class MempoolSnapshot:
    entries: list[PendingTxEntry]
    raw_by_hash: dict[str, bytes]
    total: int


def _normalize_hash_hex(hash_hex: str) -> str:
    if hash_hex.startswith("0x"):
        return hash_hex.lower()
    return f"0x{hash_hex.lower()}"


def _normalize_hash_bytes(hash_value: Any) -> bytes:
    if isinstance(hash_value, (bytes, bytearray)):
        return bytes(hash_value)
    if isinstance(hash_value, str):
        value = hash_value[2:] if hash_value.startswith("0x") else hash_value
        return bytes.fromhex(value)
    return bytes(hash_value)


def _tx_body(tx: Any) -> Optional[dict]:
    if not isinstance(tx, dict):
        return None
    body = tx.get("body")
    if isinstance(body, dict):
        return body
    nested = tx.get("tx")
    if isinstance(nested, dict):
        return nested
    return tx


def _sender_from_signature(tx: Any) -> Optional[bytes]:
    sig = None
    if hasattr(tx, "sigs"):
        sigs = getattr(tx, "sigs", None)
        if isinstance(sigs, (list, tuple)) and sigs:
            sig = sigs[0]
    if sig is None and isinstance(tx, dict):
        sigs = tx.get("sigs")
        if isinstance(sigs, list) and sigs:
            sig = sigs[0]
        elif "sig" in tx:
            sig = tx.get("sig")
        elif "signature" in tx:
            sig = tx.get("signature")

    if sig is None:
        return None

    if isinstance(sig, dict):
        alg_id = sig.get("alg") or sig.get("alg_id") or sig.get("algId")
        pubkey = sig.get("pubkey") or sig.get("pub") or sig.get("pk")
    else:
        alg_id = getattr(sig, "alg_id", getattr(sig, "alg", None))
        pubkey = getattr(sig, "pubkey", getattr(sig, "pub", None))

    if pubkey is None:
        return None
    
    # Convert pubkey to bytes safely
    if isinstance(pubkey, str):
        if pubkey.startswith("0x"):
            try:
                pubkey = bytes.fromhex(pubkey[2:])
            except ValueError:
                return None
        else:
            return None
    
    # Ensure pubkey is bytes - handle buffer protocol types
    if not isinstance(pubkey, bytes):
        try:
            pubkey = bytes(pubkey)
        except (TypeError, ValueError):
            return None
    
    # Safe conversion of alg_id to int
    alg_id_int = None
    if alg_id is not None:
        try:
            alg_id_int = int(alg_id)
        except (TypeError, ValueError):
            return None
    
    try:
        return account_key_from_pubkey(pubkey, alg_id_int)
    except (AccountKeyError, ValueError, TypeError):
        return None


def _sender_from_payload(tx: Any) -> Optional[bytes]:
    sender = None

    # Handle dict envelope with "body"/"tx" key (CLI/SDK format)
    body = _tx_body(tx)
    if isinstance(body, dict):
        # Try "from" first (CLI uses this), then "sender"
        sender = body.get("from") or body.get("sender")

    # Handle Tx dataclass format
    if sender is None:
        unsigned = getattr(tx, "unsigned", None)
        if unsigned is not None:
            sender = getattr(unsigned, "sender", None)
    if sender is None:
        sender = getattr(tx, "sender", None)

    # Convert sender to bytes
    if isinstance(sender, str):
        if sender.startswith("0x"):
            try:
                return bytes.fromhex(sender[2:])
            except ValueError:
                return None
        return None
    if isinstance(sender, (bytes, bytearray)):
        try:
            return account_key_from_raw(bytes(sender))
        except AccountKeyError:
            return None
    return None


def _sender_bytes(tx: Any) -> Optional[bytes]:
    sender_sig = _sender_from_signature(tx)
    sender_payload = _sender_from_payload(tx)
    return sender_sig or sender_payload


def _sender_hex(sender: Optional[bytes]) -> str:
    if not sender:
        return "0x"
    return "0x" + bytes(sender).hex()


def _tx_valid_after(tx: Any) -> Optional[int]:
    body = _tx_body(tx)
    valid_after = None
    if isinstance(body, dict):
        if "validAfter" in body:
            valid_after = body.get("validAfter")
        elif "valid_after" in body:
            valid_after = body.get("valid_after")
    if valid_after is None:
        unsigned = getattr(tx, "unsigned", None)
        if unsigned is not None:
            valid_after = getattr(unsigned, "valid_after", None)
    if valid_after is None:
        valid_after = getattr(tx, "valid_after", None)
    try:
        return int(valid_after) if valid_after is not None else None
    except Exception:
        return None


def _tx_valid_until(tx: Any) -> Optional[int]:
    body = _tx_body(tx)
    valid_until = None
    if isinstance(body, dict):
        if "validUntil" in body:
            valid_until = body.get("validUntil")
        elif "valid_until" in body:
            valid_until = body.get("valid_until")
    if valid_until is None:
        unsigned = getattr(tx, "unsigned", None)
        if unsigned is not None:
            valid_until = getattr(unsigned, "valid_until", None)
    if valid_until is None:
        valid_until = getattr(tx, "valid_until", None)
    try:
        return int(valid_until) if valid_until is not None else None
    except Exception:
        return None


def _tx_nonce(tx: Any) -> Optional[int]:
    body = _tx_body(tx)
    nonce = None
    if isinstance(body, dict):
        nonce = body.get("nonce")
    if nonce is None:
        unsigned = getattr(tx, "unsigned", None)
        if unsigned is not None:
            nonce = getattr(unsigned, "nonce", None)
    if nonce is None:
        nonce = getattr(tx, "nonce", None)
    try:
        return int(nonce) if nonce is not None else None
    except Exception:
        return None


def _tx_salt(tx: Any) -> Optional[bytes]:
    body = _tx_body(tx)
    salt = None
    if isinstance(body, dict):
        salt = body.get("salt")
    if salt is None:
        unsigned = getattr(tx, "unsigned", None)
        if unsigned is not None:
            salt = getattr(unsigned, "salt", None)
    if salt is None:
        salt = getattr(tx, "salt", None)
    if isinstance(salt, (bytes, bytearray)):
        return bytes(salt)
    return None


def _tx_gas_limit(tx: Any) -> int:
    gas = None

    # Handle dict envelope with "body"/"tx" key (CLI/SDK format)
    body = _tx_body(tx)
    if isinstance(body, dict):
        # Try "gasLimit" first (CLI uses this), then "gas_limit"
        gas = body.get("gasLimit") or body.get("gas_limit")
        if gas is None:
            gas = body.get("gas")
        if isinstance(gas, dict):
            gas = gas.get("limit")

    # Handle Tx dataclass format
    if gas is None:
        unsigned = getattr(tx, "unsigned", None)
        if unsigned is not None:
            gas = getattr(unsigned, "gas_limit", None)
    if gas is None:
        gas = getattr(tx, "gas_limit", None)

    try:
        return int(gas or 0)
    except (TypeError, ValueError):
        return 0


def _tx_offered_fee_wei(tx: Any, fee: Any) -> int:
    """
    Effective per-gas fee offered by a tx, in wei (ANM-H07 fee floor input).

    Prefers the canonical EffectiveFee reader (correct for Tx objects), and
    falls back to reading the fee straight out of a dict envelope body when
    EffectiveFee cannot (it only understands attribute-style objects). This
    keeps the fee floor from misfiring on dict-shaped submissions.
    """
    try:
        offered = int(fee.effective_gas_price(None))
    except Exception:
        offered = 0
    if offered > 0:
        return offered

    body = _tx_body(tx)
    if isinstance(body, dict):
        candidate = (
            body.get("gasPrice")
            or body.get("gas_price")
            or body.get("maxFee")
            or body.get("max_fee")
            or body.get("maxFeePerGas")
            or body.get("max_fee_per_gas")
        )
        if candidate is None:
            gas = body.get("gas")
            if isinstance(gas, dict):
                candidate = gas.get("price")
        try:
            if candidate is not None:
                return int(candidate)
        except (TypeError, ValueError):
            return offered
    return offered


def _tx_chain_id(tx: Any) -> Optional[int]:
    chain_id = None

    # Handle dict envelope with "body"/"tx" key (CLI/SDK format)
    body = _tx_body(tx)
    if isinstance(body, dict):
        # Try "chainId" first (CLI uses this), then "chain_id"
        chain_id = body.get("chainId") or body.get("chain_id")

    # Handle Tx dataclass format
    if chain_id is None:
        unsigned = getattr(tx, "unsigned", None)
        if unsigned is not None:
            chain_id = getattr(unsigned, "chain_id", None)
    if chain_id is None:
        chain_id = getattr(tx, "chain_id", None)

    try:
        return int(chain_id) if chain_id is not None else None
    except Exception:
        return None


def _tx_kind(tx: Any) -> str:
    body = _tx_body(tx)
    kind_val = None
    if isinstance(body, dict):
        payload = body.get("payload")
        if isinstance(payload, dict):
            kind_val = payload.get("t")
        if kind_val is None:
            kind_val = body.get("kind") or body.get("type")
    if kind_val is None:
        unsigned = getattr(tx, "unsigned", None)
        if unsigned is not None:
            kind_val = getattr(unsigned, "kind", None)
    if kind_val is None:
        kind_val = getattr(tx, "kind", None)

    if hasattr(kind_val, "value"):
        kind_val = getattr(kind_val, "value", kind_val)
    if isinstance(kind_val, str):
        lowered = kind_val.strip().lower()
        if lowered in {"transfer", "deploy", "call", "coinbase"}:
            return lowered
    if isinstance(kind_val, int):
        mapping = {0: "transfer", 1: "deploy", 2: "call", 3: "coinbase"}
        if kind_val in mapping:
            return mapping[kind_val]

    if isinstance(body, dict):
        has_data = body.get("data") is not None or body.get("input") is not None
        to = body.get("to")
        if to in (None, "", b"") and has_data:
            return "deploy"
    return "unknown"


def _tx_transfer_recipient_key(tx: Any) -> bytes | None:
    recipient = None
    body = _tx_body(tx)
    if isinstance(body, dict):
        payload = body.get("payload")
        if isinstance(payload, dict):
            payload_value = payload.get("v")
            if isinstance(payload_value, dict):
                recipient = payload_value.get("to") or payload_value.get("recipient")
        recipient = recipient or body.get("to") or body.get("recipient")
    if recipient is None:
        unsigned = getattr(tx, "unsigned", None)
        if unsigned is not None:
            payload = getattr(unsigned, "payload", None)
            if payload is not None:
                recipient = getattr(payload, "to", getattr(payload, "recipient", None))
    if recipient is None:
        recipient = getattr(tx, "to", getattr(tx, "recipient", None))

    if isinstance(recipient, str):
        text = recipient.strip()
        if not text:
            return None
        if text.startswith(("0x", "0X")):
            try:
                recipient = bytes.fromhex(text[2:])
            except Exception:
                return None
        elif text.startswith("anim1"):
            try:
                recipient = address_to_bytes(text)
            except Exception:
                return None
        else:
            try:
                recipient = bytes.fromhex(text)
            except Exception:
                return None

    if not isinstance(recipient, (bytes, bytearray, memoryview)):
        return None
    try:
        return account_key_from_raw(bytes(recipient))
    except AccountKeyError:
        return None


def _coerce_nonnegative_height(value: Any) -> int | None:
    try:
        if value is None:
            return None
        height = int(value)
    except Exception:
        return None
    return height if height >= 0 else None


def _head_height_candidates(head: Any) -> list[int]:
    candidates: list[int] = []
    if isinstance(head, dict):
        for key in (
            "height",
            "canonicalHeight",
            "head_height",
            "headHeight",
            "block_height",
            "blockHeight",
            "number",
        ):
            height = _coerce_nonnegative_height(head.get(key))
            if height is not None:
                candidates.append(height)
        header = head.get("header")
        if isinstance(header, dict):
            for key in ("height", "canonicalHeight", "number", "blockHeight"):
                height = _coerce_nonnegative_height(header.get(key))
                if height is not None:
                    candidates.append(height)
        else:
            for attr in ("height", "canonicalHeight", "number", "blockHeight"):
                height = _coerce_nonnegative_height(getattr(header, attr, None))
                if height is not None:
                    candidates.append(height)
        return candidates
    if isinstance(head, (tuple, list)):
        if head:
            height = _coerce_nonnegative_height(head[0])
            if height is not None:
                candidates.append(height)
        if len(head) >= 2:
            candidates.extend(_head_height_candidates(head[1]))
        return candidates
    height = _coerce_nonnegative_height(head)
    if height is not None:
        candidates.append(height)
    return candidates


def _extract_head_height(head: Any) -> int | None:
    active_keys = (
        "height",
        "head_height",
        "headHeight",
        "block_height",
        "blockHeight",
        "number",
    )
    canonical_keys = ("canonicalHeight", "canonical_height")

    def _field(source: Any, keys: tuple[str, ...]) -> int | None:
        if isinstance(source, dict):
            getter = source.get
        else:
            getter = lambda key: getattr(source, key, None)
        for key in keys:
            height = _coerce_nonnegative_height(getter(key))
            if height is not None:
                return height
        return None

    if isinstance(head, dict):
        sources: list[Any] = [head]
        header = head.get("header")
        if header is not None:
            sources.append(header)

        zero_height: int | None = None
        for source in sources:
            height = _field(source, active_keys)
            if height is None:
                continue
            if height > 0:
                return height
            zero_height = 0

        for source in sources:
            height = _field(source, canonical_keys)
            if height is not None:
                return height
        return zero_height

    if isinstance(head, (tuple, list)):
        zero_height: int | None = None
        if head:
            height = _coerce_nonnegative_height(head[0])
            if height is not None:
                if height > 0:
                    return height
                zero_height = 0
        if len(head) >= 2:
            nested = _extract_head_height(head[1])
            if nested is not None:
                if nested > 0:
                    return nested
                zero_height = 0
        return zero_height

    return _coerce_nonnegative_height(head)


def _current_height() -> int:
    try:
        ctx = deps.get_ctx()
    except Exception:
        return 0

    head = None
    try:
        getter = getattr(ctx, "get_head", None)
        if callable(getter):
            head = getter()
    except Exception:
        head = None

    parsed = _extract_head_height(head)
    if parsed is not None:
        return parsed

    block_db = getattr(ctx, "block_db", None)
    if block_db is not None:
        try:
            db_head = block_db.get_head()
        except Exception:
            db_head = None
        parsed = _extract_head_height(db_head)
        if parsed is not None:
            return parsed
        try:
            canonical_height = block_db.get_canonical_height()
        except Exception:
            canonical_height = None
        parsed = _coerce_nonnegative_height(canonical_height)
        if parsed is not None:
            return parsed

    return 0


class MempoolService:
    def __init__(
        self,
        *,
        pool: Pool,
        chain_id: int,
        min_gas_price_wei: int,
        state_db: Any | None,
        tx_index: Any | None,
        data_dir: str | Path | None = None,
        persist_enabled: bool = True,
        persist_ttl_s: int = 0,
        per_sender_max_txs: int = 0,
        verify_signatures: bool | None = None,
    ) -> None:
        self.pool = pool
        self.chain_id = int(chain_id)
        self.min_gas_price_wei = int(min_gas_price_wei)
        self._init_admission_policy(
            per_sender_max_txs=per_sender_max_txs,
            verify_signatures=verify_signatures,
        )
        self.state_db = state_db
        self.tx_index = tx_index
        self._persist_enabled = bool(persist_enabled)
        self._persist_backend = "memory"
        self._persistence_error: dict[str, Any] | None = None
        self._fallback_reason: str | None = None
        self._fallback_active = False
        self._persist_required = (os.environ.get("ANIMICA_MEMPOOL_REQUIRE_PERSIST", "0").strip().lower() in {"1", "true", "yes", "on"})
        self._persist_ttl_s = int(persist_ttl_s) if int(persist_ttl_s) > 0 else 0
        self._persist_lock = threading.RLock()
        self._persist_path: Path | None = None
        if data_dir:
            self._persist_path = Path(data_dir).expanduser() / "mempool" / "pending.jsonl"
            if self._persist_enabled:
                self._persist_backend = "persistent"
        self._restoring = False
        self._rejection_ttl_s = int(
            os.getenv("ANIMICA_MEMPOOL_REJECTION_TTL_S", "300") or 300
        )
        self._last_rejections: dict[str, dict[str, Any]] = {}
        self._replay_window_blocks = int(
            os.getenv("ANIMICA_REPLAY_WINDOW_BLOCKS", "200") or 200
        )
        self._recent_txids: dict[str, int] = {}
        self._quarantine_cap = int(
            os.getenv("ANIMICA_MEMPOOL_QUARANTINE_CAP", "50000") or 50000
        )
        self._quarantined_txids: OrderedDict[str, float] = OrderedDict()
        # Per-sender admission locks to serialize balance/pending accounting
        self._sender_locks: dict[str, threading.RLock] = {}
        self._sender_locks_lock = threading.RLock()
        # Optional P2P broadcast callback - set by P2P service to trigger tx propagation
        self._p2p_broadcast_callback: Optional[Any] = None
        self._p2p_broadcast_loop: Optional["asyncio.AbstractEventLoop"] = None
        self._instant_block_callback: Optional[Any] = None
        self._instant_block_loop: Optional["asyncio.AbstractEventLoop"] = None
        if self._persist_enabled:
            self._load_persisted()

    # ------------------------------------------------------------------
    # Admission-policy hardening (ANM-H07 / ANM-H10 / ANM-M09).
    #
    # These are LOCAL mempool admission/relay knobs only. They do NOT touch
    # block validity (core/chain/block_import.py is unchanged): a block that
    # already contains such a tx is still accepted by the importer, so there
    # is no consensus/split risk. Stricter enforcement is scoped to mainnet
    # (chain_id=1) and/or gated behind env with safe defaults so existing
    # non-mainnet flows keep their current behavior.
    # ------------------------------------------------------------------
    def _init_admission_policy(
        self, *, per_sender_max_txs: int, verify_signatures: bool | None
    ) -> None:
        def _env_flag(name: str, default: bool) -> bool:
            raw = os.getenv(name)
            if raw is None:
                return default
            return raw.strip().lower() in {"1", "true", "yes", "on"}

        def _env_int(name: str, default: int) -> int:
            raw = os.getenv(name)
            if raw is None:
                return default
            try:
                return int(raw)
            except (TypeError, ValueError):
                return default

        is_mainnet = int(self.chain_id) == 1

        # ANM-H07: real minimum-fee floor. The genesis params ship
        # min_gas_price=0 which disables the fee gate entirely, so an unset
        # floor is defaulted to 1 wei on mainnet. The live wallet already
        # attaches maxFee>=1, so this rejects only truly-free (fee==0) spam.
        # Set MEMPOOL_MIN_FEE_WEI=0 to opt out.
        if self.min_gas_price_wei <= 0 and is_mainnet:
            floor_default = _env_int("MEMPOOL_MIN_FEE_WEI", 1)
            if floor_default > 0:
                self.min_gas_price_wei = int(floor_default)
                log.warning(
                    "Mempool min-fee floor not configured; defaulting to %d wei "
                    "for chain_id=1 (set min_gas_price or MEMPOOL_MIN_FEE_WEI=0 "
                    "to override)",
                    floor_default,
                )

        # ANM-H07: per-sender pending cap (0 disables). Wired from
        # mempool.config.Limits.per_sender_max_txs; env override wins.
        cap = _env_int("MEMPOOL_PER_SENDER_MAX_TXS", int(per_sender_max_txs or 0))
        self._per_sender_max_txs = cap if cap > 0 else 0

        # ANM-H07: reject completely-unfunded senders (on-chain balance <= 0)
        # when state is readable. Such txs can never pay a fee and are pure
        # relay spam. Only bites when a real integer balance is obtained.
        self._require_funded_sender = _env_flag("MEMPOOL_REQUIRE_FUNDED_SENDER", True)

        # ANM-M09: intrinsic-gas lower bound + block-gas upper bound at
        # admission (parity with block admission). Default on; the bounds are
        # chosen so no tx the live wallet/miner produce is rejected.
        self._enforce_gas_bounds = _env_flag("MEMPOOL_ENFORCE_GAS_BOUNDS", True)
        # Generous upper bound (~10x the largest configured VM gas tier) so a
        # legitimate high-gas contract tx is never rejected, while the absurd
        # gas_limit=10**18 spam of the finding is. Operators may tighten it.
        block_gas = _env_int("MEMPOOL_MAX_GAS_LIMIT", 1_000_000_000)
        self._block_gas_limit = block_gas if block_gas > 0 else 0

        # ANM-H10: mandatory in-mempool signature verification. This is a
        # chain-security invariant and must not be delegated to (or skippable
        # by) callers. Enforced for mainnet by default; the kill-switch exists
        # only for emergencies/tests and defaults to the secure setting.
        if verify_signatures is None:
            verify_signatures = _env_flag("ANIMICA_MEMPOOL_VERIFY_SIGS", True)
        self._verify_sigs = bool(verify_signatures) and is_mainnet

    def _intrinsic_gas_floor(self, tx: Any, decoded_tx_obj: Any | None) -> int:
        """
        Best-effort intrinsic-gas floor for a tx (ANM-M09).

        Returns a value that is guaranteed to be <= the real intrinsic gas the
        executor would charge, so the lower-bound check never over-rejects a
        block-valid tx. In practice this resolves to the base tx cost (21000)
        for the common case, and higher when a decoded Tx object exposes its
        calldata/access-list.
        """
        try:
            from mempool.accounting import intrinsic_gas as _intrinsic_gas
            from mempool.accounting import AccountingConfig as _AccCfg
        except Exception:
            return 21_000
        cfg = _AccCfg(enforce_intrinsic_leq_limit=False)
        best = 0
        for candidate in (decoded_tx_obj, tx):
            if candidate is None:
                continue
            try:
                g = int(_intrinsic_gas(candidate, cfg=cfg))
            except Exception:
                continue
            if g > best:
                best = g
        # base_tx (21000) is the minimum intrinsic for any non-coinbase tx.
        return best if best > 0 else 21_000

    def _verify_tx_signature(
        self, raw_bytes: bytes, tx_hash_hex: str, sender_hex: str | None
    ) -> None:
        """
        ANM-H10: mandatory signature verification performed INSIDE admission.

        Re-decodes the canonical bytes and runs the same PQ verification the
        RPC/P2P callers run, so a forged/unsigned tx is rejected regardless of
        which caller submitted it and regardless of ANIMICA_PQ_VERIFY_OPTIONAL.
        Fails closed on mainnet if the verification backend is unavailable.
        """
        try:
            from rpc.methods import tx as tx_methods
        except Exception as exc:
            self._record_rejection(
                tx_hash_hex, "verify_unavailable", {"error": str(exc)}
            )
            raise AdmissionError(
                "signature verification backend unavailable",
                context={"tx_hash": tx_hash_hex, "reason": "invalid_signature"},
            ) from exc

        try:
            tx_like, obj = tx_methods._decode_tx(raw_bytes)
        except Exception as exc:
            self._record_rejection(
                tx_hash_hex,
                "invalid_signature",
                {"step": "verify_decode", "error": str(exc)},
            )
            raise AdmissionError(
                "tx could not be decoded for signature verification",
                context={"tx_hash": tx_hash_hex, "reason": "invalid_signature"},
            ) from exc

        try:
            tx_methods._verify_pq_signature(tx_like, obj, chain_id=int(self.chain_id))
        except Exception as exc:
            self._record_rejection(
                tx_hash_hex,
                "invalid_signature",
                {"error": str(exc), "sender": sender_hex},
            )
            raise AdmissionError(
                "invalid signature",
                context={
                    "tx_hash": tx_hash_hex,
                    "sender": sender_hex,
                    "reason": "invalid_signature",
                },
            ) from exc

    def _disable_persistence_fallback(self, *, reason: str, exc: Exception, path_attempted: str | None = None) -> None:
        errno_value = getattr(exc, "errno", None)
        payload = {
            "kind": "mempool_unavailable",
            "pathAttempted": path_attempted or (str(self._persist_path) if self._persist_path else None),
            "errno": errno_value,
            "error": str(exc),
            "suggestion": "Set ANIMICA_DATA_DIR to a writable path (/data or /var/lib/animica) or unset ANIMICA_MEMPOOL_REQUIRE_PERSIST to allow fallback.",
        }
        self._persistence_error = payload
        self._fallback_reason = reason
        if self._persist_required:
            raise PersistenceFailed(tx_hash="unknown", error=str(exc)) from exc
        self._persist_enabled = False
        self._persist_backend = "memory"
        self._fallback_active = True
        log.error(
            "Mempool persistence disabled; using in-memory fallback",
            extra={"fallback": payload, "reason": reason},
            exc_info=exc,
        )
    
    def set_p2p_broadcast_callback(
        self, callback: Any, *, loop: Optional["asyncio.AbstractEventLoop"] = None
    ) -> None:
        """Set callback to trigger P2P broadcast when tx is accepted to mempool."""
        self._p2p_broadcast_callback = callback
        if loop is not None:
            self._p2p_broadcast_loop = loop
            return
        owner = getattr(callback, "__self__", None)
        owner_loop = getattr(owner, "loop", None) if owner is not None else None
        if owner_loop is not None:
            self._p2p_broadcast_loop = owner_loop

    def set_instant_block_callback(
        self, callback: Any, *, loop: Optional["asyncio.AbstractEventLoop"] = None
    ) -> None:
        """Set callback to emit instant tx block receipts when tx is accepted."""
        self._instant_block_callback = callback
        if loop is not None:
            self._instant_block_loop = loop
            return
        owner = getattr(callback, "__self__", None)
        owner_loop = getattr(owner, "loop", None) if owner is not None else None
        if owner_loop is not None:
            self._instant_block_loop = owner_loop

    def _record_rejection(
        self,
        tx_hash_hex: str,
        reason: str,
        details: dict[str, Any] | None = None,
        *,
        stage: str = "mempool_admission",
        tx_kind: str | None = None,
    ) -> None:
        tx_hash_hex = _normalize_hash_hex(tx_hash_hex)
        payload = dict(details or {})
        payload.setdefault("tx_hash", tx_hash_hex)
        payload.setdefault("stage", stage)
        if tx_kind:
            payload.setdefault("tx_kind", tx_kind)
        self._last_rejections[tx_hash_hex] = {
            "reason": reason,
            "details": payload,
            "ts": time.time(),
        }
        self._prune_rejections()

    def _prune_rejections(self) -> None:
        if not self._last_rejections:
            return
        cutoff = time.time() - float(self._rejection_ttl_s)
        expired = [k for k, v in self._last_rejections.items() if v.get("ts", 0) < cutoff]
        for k in expired:
            self._last_rejections.pop(k, None)

    def _prune_recent_txids(self, current_height: int) -> None:
        if not self._recent_txids:
            return
        expired = [h for h, until in self._recent_txids.items() if until < current_height]
        for h in expired:
            self._recent_txids.pop(h, None)

    def _mark_quarantined(self, tx_hash_hex: str) -> None:
        tx_hash_hex = _normalize_hash_hex(tx_hash_hex)
        self._quarantined_txids[tx_hash_hex] = time.time()
        self._quarantined_txids.move_to_end(tx_hash_hex, last=True)
        while len(self._quarantined_txids) > max(1, int(self._quarantine_cap)):
            self._quarantined_txids.popitem(last=False)

    def is_quarantined(self, tx_hash_hex: str) -> bool:
        tx_hash_hex = _normalize_hash_hex(tx_hash_hex)
        return tx_hash_hex in self._quarantined_txids

    def quarantine_hashes(
        self,
        tx_hashes: Iterable[str],
        *,
        reason: str = "duplicate_canonical",
        details: dict[str, Any] | None = None,
        permanent: bool = True,
    ) -> dict[str, int]:
        """
        Quarantine tx hashes to prevent re-admission after duplicate/replay failures.

        Quarantined hashes are also evicted from the active pool immediately.
        """
        unique_hashes: list[str] = []
        seen: set[str] = set()
        for tx_hash in tx_hashes:
            try:
                normalized = _normalize_hash_hex(tx_hash)
            except Exception:
                continue
            if normalized in seen:
                continue
            seen.add(normalized)
            unique_hashes.append(normalized)

        if not unique_hashes:
            return {"quarantined": 0, "removed": 0}

        quarantined = 0
        for hash_hex in unique_hashes:
            if permanent:
                self._mark_quarantined(hash_hex)
            payload = dict(details or {})
            payload.setdefault("quarantined", True)
            payload.setdefault("permanent", bool(permanent))
            self._record_rejection(
                hash_hex,
                reason,
                payload,
                stage="duplicate_tx_resolver",
            )
            quarantined += 1

        removed = self.remove_included(unique_hashes)
        log.warning(
            "Mempool duplicate tx resolver quarantined hashes",
            extra={
                "reason": reason,
                "quarantined": quarantined,
                "removed": int(removed),
                "permanent": bool(permanent),
            },
        )
        return {"quarantined": quarantined, "removed": int(removed)}

    def get_rejection(self, tx_hash_hex: str) -> dict[str, Any] | None:
        tx_hash_hex = _normalize_hash_hex(tx_hash_hex)
        self._prune_rejections()
        return self._last_rejections.get(tx_hash_hex)

    def _confirmed_nonce(self, sender_bytes: bytes) -> int | None:
        if self.state_db is None:
            return None
        if hasattr(self.state_db, "get_nonce"):
            try:
                return int(self.state_db.get_nonce(sender_bytes))
            except Exception:
                pass
        if hasattr(self.state_db, "get_account"):
            try:
                acct = self.state_db.get_account(sender_bytes)
            except Exception:
                acct = None
            if acct is not None:
                if hasattr(acct, "nonce"):
                    return int(acct.nonce)
                if isinstance(acct, dict) and "nonce" in acct:
                    return int(acct["nonce"])
        if hasattr(self.state_db, "get"):
            try:
                acct = self.state_db.get(sender_bytes)
            except Exception:
                acct = None
            if acct is not None:
                if hasattr(acct, "nonce"):
                    return int(acct.nonce)
                if isinstance(acct, dict) and "nonce" in acct:
                    return int(acct["nonce"])
        return None

    def _pending_entries_by_sender(self, sender_hex: str) -> list[Any]:
        try:
            return list(self.pool.index.get_by_sender(sender_hex))
        except Exception:
            return []

    def pending_nonces(self, sender_bytes: bytes) -> set[int]:
        sender_hex = _sender_hex(sender_bytes)
        pending = set()
        for entry in self._pending_entries_by_sender(sender_hex):
            nonce = getattr(entry.meta, "nonce", None)
            if nonce is None:
                tx_obj = getattr(entry.tx, "tx", entry.tx)
                nonce = _tx_nonce(tx_obj)
            if nonce is None:
                continue
            pending.add(int(nonce))
        return pending

    def pending_nonce(self, sender_bytes: bytes, confirmed_nonce: int | None = None) -> int | None:
        pending = self.pending_nonces(sender_bytes)
        if not pending:
            return None
        highest_pending = max(pending)
        if confirmed_nonce is None:
            return highest_pending + 1
        return max(int(confirmed_nonce), highest_pending + 1)

    def get_next_nonce(self, sender_bytes: bytes, confirmed_nonce: int) -> int:
        pending_next = self.pending_nonce(sender_bytes, confirmed_nonce)
        return int(pending_next) if pending_next is not None else int(confirmed_nonce)

    def _pending_by_nonce(self, sender_hex: str) -> dict[int, str]:
        by_nonce: dict[int, str] = {}
        for entry in self._pending_entries_by_sender(sender_hex):
            tx_obj = getattr(entry.tx, "tx", entry.tx)
            nonce = getattr(entry.meta, "nonce", None)
            if nonce is None:
                nonce = _tx_nonce(tx_obj)
            if nonce is None:
                continue
            tx_hash = getattr(entry, "tx_hash", None) or getattr(entry.tx, "tx_hash", None)
            if tx_hash is None:
                continue
            by_nonce[int(nonce)] = "0x" + _normalize_hash_bytes(tx_hash).hex()
        return by_nonce

    @classmethod
    def create(
        cls,
        *,
        chain_id: int,
        min_gas_price_wei: int,
        state_db: Any | None,
        tx_index: Any | None,
        data_dir: str | Path | None = None,
        config: MempoolConfig | None = None,
    ) -> "MempoolService":
        mp_cfg = config or load_mempool_config()
        pool_cfg = PoolConfig(
            max_txs=mp_cfg.limits.max_txs,
            max_bytes=mp_cfg.limits.max_bytes,
            target_util=mp_cfg.gas.target_utilization,
            accept_below_floor_for_local=True,
        )
        pool = Pool(cfg=pool_cfg)
        persist_env = os.getenv("ANIMICA_MEMPOOL_PERSIST")
        persist_enabled = True
        if persist_env is not None:
            persist_enabled = persist_env.strip().lower() in {"1", "true", "yes", "on"}
        persist_ttl_s = int(
            os.getenv("ANIMICA_MEMPOOL_PERSIST_TTL_S", str(mp_cfg.ttls.pending_seconds))
            or mp_cfg.ttls.pending_seconds
        )
        return cls(
            pool=pool,
            chain_id=chain_id,
            min_gas_price_wei=min_gas_price_wei,
            state_db=state_db,
            tx_index=tx_index,
            data_dir=data_dir,
            persist_enabled=persist_enabled,
            persist_ttl_s=persist_ttl_s,
            # ANM-H07: wire the (previously unused) per-sender pending cap.
            per_sender_max_txs=int(getattr(mp_cfg.limits, "per_sender_max_txs", 0) or 0),
        )

    def _persist_snapshot(self) -> None:
        if not self._persist_enabled:
            return
        if self._persist_path is None:
            log.error("Mempool persistence enabled but persist path is unset")
            raise PersistenceFailed(tx_hash="unknown", error="persist_path_unset")
        snapshot = self.snapshot(limit=len(self.pool) + 1)
        now = time.time()
        ttl_s = self._persist_ttl_s or 0
        entries: list[dict[str, Any]] = []
        for entry in snapshot.entries:
            raw = entry.raw
            if not raw:
                continue
            first_seen = (
                float(entry.received_at) if entry.received_at is not None else now
            )
            expires_at = (
                float(entry.expires_at)
                if entry.expires_at is not None
                else (first_seen + ttl_s if ttl_s else None)
            )
            if expires_at is not None and expires_at <= now:
                continue
            sender = _sender_hex(_sender_bytes(entry.tx))
            valid_after = _tx_valid_after(entry.tx)
            valid_until = _tx_valid_until(entry.tx)
            salt = _tx_salt(entry.tx)
            gas_limit = _tx_gas_limit(entry.tx)
            fee = EffectiveFee.from_tx(entry.tx)
            entries.append(
                {
                    "hash": entry.hash_hex,
                    "raw": raw.hex(),
                    "first_seen": first_seen,
                    "expires_at": expires_at,
                    "sender": sender,
                    "valid_after": int(valid_after) if valid_after is not None else None,
                    "valid_until": int(valid_until) if valid_until is not None else None,
                    "salt": "0x" + bytes(salt).hex() if salt is not None else None,
                    "gas_limit": int(gas_limit) if gas_limit is not None else None,
                    "fee_wei": int(fee.effective_gas_price(None)),
                    "chain_id": int(self.chain_id),
                }
            )
        if self._persist_path.parent:
            try:
                self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                if exc.errno in (errno.EROFS, errno.EACCES, errno.ENOENT):
                    self._disable_persistence_fallback(reason="mkdir_failed", exc=exc)
                    return
                raise
        tmp_path = self._persist_path.with_suffix(".tmp")
        with self._persist_lock:
            try:
                with tmp_path.open("wt", encoding="utf-8") as fh:
                    for entry in entries:
                        fh.write(json.dumps(entry) + "\n")
                tmp_path.replace(self._persist_path)
                self._persist_backend = "persistent"
            except OSError as exc:
                if exc.errno in (errno.EROFS, errno.EACCES, errno.ENOENT):
                    self._disable_persistence_fallback(reason="write_failed", exc=exc)
                    return
                raise

    def _load_persisted(self) -> None:
        if self._persist_path is None or not self._persist_path.exists():
            return
        try:
            lines = self._persist_path.read_text(encoding="utf-8").splitlines()
        except Exception as exc:
            log.error("Failed to read mempool persistence file", exc_info=exc)
            return
        restored = 0
        bad_entries: list[str] = []
        self._restoring = True
        now = time.time()
        for line in lines:
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except Exception:
                bad_entries.append(line)
                continue
            if entry.get("chain_id") not in (None, self.chain_id, int(self.chain_id)):
                continue
            expires_at = entry.get("expires_at")
            if expires_at is not None and float(expires_at) <= now:
                continue
            raw_hex = entry.get("raw") or ""
            if not isinstance(raw_hex, str) or not raw_hex:
                bad_entries.append(line)
                continue
            try:
                raw = bytes.fromhex(raw_hex)
            except ValueError:
                bad_entries.append(line)
                continue
            if not raw:
                bad_entries.append(line)
                continue
            try:
                normalized_env = normalize_tx_envelope(raw)
                raw = normalized_env.get("raw") or raw
            except TxNormalizationError:
                bad_entries.append(line)
                continue
            try:
                tx_obj = (
                    Tx.from_cbor(raw)  # type: ignore[attr-defined]
                    if hasattr(Tx, "from_cbor")
                    else None
                )
            except Exception:
                bad_entries.append(line)
                tx_obj = None
            if tx_obj is None:
                continue
            try:
                self.submit(tx=tx_obj, raw=raw, local=True)
            except Exception:
                continue
            restored += 1
        self._restoring = False
        if restored:
            log.info("Restored mempool entries", extra={"count": restored})
        if bad_entries and self._persist_path is not None:
            quarantine = self._persist_path.with_suffix(".bad.jsonl")
            try:
                quarantine.write_text("\n".join(bad_entries) + "\n", encoding="utf-8")
            except Exception:
                pass
            log.error(
                "Dropped invalid persisted mempool entries",
                extra={"count": len(bad_entries), "quarantine": str(quarantine)},
            )
        if restored or bad_entries:
            self._persist_snapshot()

    def has_hash(self, tx_hash_hex: str) -> bool:
        try:
            return self.pool.get(_normalize_hash_bytes(tx_hash_hex)) is not None
        except Exception:
            return False

    def persistence_status(self) -> dict[str, Any]:
        return {
            "enabled": bool(self._persist_enabled),
            "backend": self._persist_backend,
            "fallback_active": bool(self._fallback_active),
            "fallback_reason": self._fallback_reason,
            "path": str(self._persist_path) if self._persist_path else None,
            "error": self._persistence_error,
            "require_persist": bool(self._persist_required),
        }

    def submit(
        self,
        *,
        tx: Any,
        raw: bytes,
        tx_hash_hex: str | None = None,
        local: bool = True,
        origin_peer: str | None = None,
        simulate: bool = False,
    ) -> str:
        try:
            raw_bytes = normalized_tx_bytes(raw)
        except Exception as exc:
            if tx_hash_hex is None:
                try:
                    tx_hash_hex = _tx_hash_hex(bytes(raw))
                except Exception:
                    tx_hash_hex = "0x" + hashlib.sha3_256(bytes(raw)).hexdigest()
            self._record_rejection(
                tx_hash_hex,
                "decode_error",
                {"step": "normalize_raw", "error": str(exc)},
            )
            raise AdmissionError(
                "invalid raw tx bytes",
                context={"tx_hash": tx_hash_hex, "error": str(exc)},
            ) from exc

        if tx_hash_hex is None:
            tx_hash_hex = _tx_hash_hex(raw_bytes)
        tx_hash_hex = _normalize_hash_hex(tx_hash_hex)
        tx_hash_bytes = _normalize_hash_bytes(tx_hash_hex)
        expected_hash = _tx_hash_hex(raw_bytes)
        if tx_hash_hex != expected_hash:
            self._record_rejection(
                tx_hash_hex,
                "hash_mismatch",
                {"expected": expected_hash, "got": tx_hash_hex},
            )
            raise AdmissionError(
                "tx hash mismatch for raw bytes",
                context={
                    "tx_hash": tx_hash_hex,
                    "expected": expected_hash,
                },
            )

        try:
            normalized_env = normalize_tx_envelope(raw_bytes)
        except TxNormalizationError as exc:
            detail_ctx = dict(exc.details or {})
            detail_ctx.update({"step": "normalize_envelope", "error": str(exc)})
            self._record_rejection(
                tx_hash_hex,
                exc.reason or "decode_error",
                detail_ctx,
            )
            raise AdmissionError(
                "tx envelope normalization failed",
                context={
                    "tx_hash": tx_hash_hex,
                    "error": str(exc),
                    "reason": exc.reason or "invalid_format",
                    **(exc.details or {}),
                },
            ) from exc

        env_hash = normalized_env.get("hash")
        if isinstance(env_hash, str) and env_hash.lower() != tx_hash_hex:
            self._record_rejection(
                tx_hash_hex,
                "hash_mismatch",
                {"expected": tx_hash_hex, "got": env_hash},
            )
            raise AdmissionError(
                "tx hash mismatch after normalization",
                context={"tx_hash": tx_hash_hex, "expected": tx_hash_hex, "got": env_hash},
            )

        current_height = _current_height()
        self._prune_recent_txids(current_height)

        if isinstance(tx, dict):
            try:
                raw_from_dict = normalized_tx_bytes(tx)
            except Exception as exc:
                self._record_rejection(
                    tx_hash_hex,
                    "decode_error",
                    {"step": "normalize_envelope", "error": str(exc)},
                )
                raise AdmissionError(
                    "tx envelope missing canonical raw bytes",
                    context={"tx_hash": tx_hash_hex, "error": str(exc)},
                ) from exc
            if raw_from_dict != raw_bytes:
                self._record_rejection(
                    tx_hash_hex,
                    "raw_mismatch",
                    {
                        "expected_hash": expected_hash,
                        "dict_hash": tx_hash_hex,
                    },
                )
                raise AdmissionError(
                    "raw bytes mismatch between envelope and admission",
                    context={"tx_hash": tx_hash_hex},
                )

        if isinstance(tx, dict):
            legacy_target = tx.get("tx") if isinstance(tx.get("tx"), dict) else tx.get("body") if isinstance(tx.get("body"), dict) else tx
            if isinstance(legacy_target, dict):
                try:
                    normalized_fields, warnings = normalize_tx_fields(legacy_target)
                except TxNormalizationError as exc:
                    self._record_rejection(
                        tx_hash_hex,
                        exc.reason or "bad_field_type",
                        {"step": "normalize_fields", "error": str(exc), **(exc.details or {})},
                    )
                    raise AdmissionError(
                        "tx field normalization failed",
                        context={"tx_hash": tx_hash_hex, "reason": exc.reason, **(exc.details or {})},
                    ) from exc
                if warnings:
                    TX_LEGACY_GASLIMIT_DICT_TOTAL.inc()
                    if isinstance(tx.get("tx"), dict):
                        tx["tx"] = normalized_fields
                    elif isinstance(tx.get("body"), dict):
                        tx["body"] = normalized_fields
                    else:
                        tx.update(normalized_fields)

        origin_label = "local" if local else f"peer:{origin_peer or 'unknown'}"
        log.info(
            "MempoolService.submit: entry, tx_hash=%s, local=%s, pool_size=%d",
            tx_hash_hex,
            local,
            len(self.pool),
        )

        if self.is_quarantined(tx_hash_hex):
            self._record_rejection(
                tx_hash_hex,
                "tx_quarantined",
                {"reason": "duplicate_canonical"},
            )
            raise AdmissionError(
                "transaction is quarantined due to duplicate/replay conflict",
                context={"tx_hash": tx_hash_hex, "reason": "tx_quarantined"},
            )

        if self.has_hash(tx_hash_hex):
            log.info(
                "MempoolService.submit: duplicate (already in pool), tx_hash=%s",
                tx_hash_hex,
            )
            return tx_hash_hex

        chain_id = _tx_chain_id(tx)
        if chain_id is not None and chain_id != self.chain_id:
            self._record_rejection(
                tx_hash_hex,
                "chain_id_mismatch",
                {"expected": self.chain_id, "got": chain_id},
            )
            raise AdmissionError(
                f"chain_id mismatch: tx={chain_id}, node={self.chain_id}",
                context={"tx_hash": tx_hash_hex, "chain_id": chain_id},
            )

        sender_sig = _sender_from_signature(tx)
        sender_payload = _sender_from_payload(tx)
        sender = sender_sig or sender_payload
        env_sender = None
        if isinstance(normalized_env.get("tx"), dict):
            env_sender = normalized_env["tx"].get("from")
        if isinstance(env_sender, str) and env_sender.startswith("0x"):
            try:
                env_sender = bytes.fromhex(env_sender[2:])
            except ValueError:
                env_sender = None
        if isinstance(env_sender, (bytes, bytearray)):
            sender = bytes(env_sender)
        if sender_sig is not None and sender is not None and sender_sig != sender:
            self._record_rejection(
                tx_hash_hex,
                "sender_mismatch",
                {"sig_sender": _sender_hex(sender_sig), "payload_sender": _sender_hex(sender)},
            )
            raise AdmissionError(
                "sender mismatch between signature and payload",
                context={"tx_hash": tx_hash_hex},
            )
        if sender is None:
            self._record_rejection(
                tx_hash_hex,
                "missing_sender",
                {"sender": _sender_hex(sender)},
            )
            raise AdmissionError(
                "missing sender",
                context={"tx_hash": tx_hash_hex},
            )

        sender_hex = _sender_hex(sender)

        decoded_tx_obj: Any | None = None
        try:
            if hasattr(Tx, "from_obj"):
                decoded_tx_obj = Tx.from_obj(  # type: ignore[attr-defined]
                    {"tx": normalized_env.get("tx"), "sigs": normalized_env.get("sigs", [])}
                )
            elif hasattr(Tx, "from_cbor"):
                decoded_tx_obj = Tx.from_cbor(raw_bytes)  # type: ignore[attr-defined]
        except Exception as exc:
            self._record_rejection(
                tx_hash_hex,
                "decode_error",
                {"step": "decode_envelope", "error": str(exc)},
            )
            raise AdmissionError(
                "tx envelope could not be decoded",
                context={"tx_hash": tx_hash_hex, "error": str(exc)},
            ) from exc

        # ANM-H10: mandatory in-mempool signature verification. Runs regardless
        # of caller so a forged/unsigned tx cannot enter the pool even via a
        # relay/caller path that forgot to verify. Skipped only when restoring
        # already-verified persisted entries. Scoped to mainnet in
        # _init_admission_policy (self._verify_sigs), fail-closed there.
        if self._verify_sigs and not self._restoring:
            self._verify_tx_signature(raw_bytes, tx_hash_hex, sender_hex)

        replay_in_index = False
        replay_source = "tx_index"
        if self.tx_index is not None and hasattr(self.tx_index, "exists"):
            try:
                replay_in_index = bool(self.tx_index.exists(tx_hash_bytes))
            except Exception:
                replay_in_index = False
            # Backward compatibility: tx_index historically keyed replay entries by
            # unsigned/sign-bytes hash. Check that legacy domain too so already-
            # applied transactions cannot be re-admitted after restart.
            if (
                not replay_in_index
                and decoded_tx_obj is not None
                and hasattr(decoded_tx_obj, "unsigned_hash")
            ):
                try:
                    legacy_unsigned_hash = bytes(decoded_tx_obj.unsigned_hash())
                except Exception:
                    legacy_unsigned_hash = None
                if (
                    isinstance(legacy_unsigned_hash, (bytes, bytearray))
                    and len(legacy_unsigned_hash) == 32
                    and bytes(legacy_unsigned_hash) != tx_hash_bytes
                ):
                    try:
                        replay_in_index = bool(self.tx_index.exists(bytes(legacy_unsigned_hash)))
                    except Exception:
                        replay_in_index = False
                    if replay_in_index:
                        replay_source = "tx_index_unsigned_hash"
        if replay_in_index:
            self._record_rejection(
                tx_hash_hex,
                "replay",
                {"tx_hash": tx_hash_hex},
            )
            raise _build_replay_error(
                tx_hash_hex=tx_hash_hex,
                sender_hex=sender_hex,
                replay_source=replay_source,
            )

        # Do not treat transient recent-cache entries as canonical replay source.
        # A transaction is "already known" only if it is in canonical mempool
        # or canonical chain index.
        if tx_hash_hex in self._recent_txids:
            log.debug(
                "Ignoring stale recent tx cache entry during admission",
                extra={"tx_hash": tx_hash_hex},
            )
            self._recent_txids.pop(tx_hash_hex, None)

        # Acquire per-sender lock to prevent TOCTOU race between getNextNonce and admission
        sender_lock = self._get_sender_lock(sender_hex)

        with sender_lock:
            tx_for_meta = normalized_env if isinstance(tx, dict) else tx
            tx_kind = _tx_kind(tx_for_meta)
            valid_after = _tx_valid_after(tx_for_meta)
            valid_until = _tx_valid_until(tx_for_meta)
            salt = _tx_salt(tx_for_meta)
            tx_version = 1
            if isinstance(normalized_env.get("tx"), dict):
                try:
                    tx_version = coerce_int("v", normalized_env["tx"].get("v", 1))
                except Exception:
                    tx_version = 1

            if tx_kind == "transfer":
                recipient_key = _tx_transfer_recipient_key(tx_for_meta)
                if recipient_key is None or not any(recipient_key):
                    recipient_repr = (
                        recipient_key.hex()
                        if isinstance(recipient_key, (bytes, bytearray))
                        else None
                    )
                    self._record_rejection(
                        tx_hash_hex,
                        "invalid_recipient",
                        {"sender": sender_hex, "recipient": recipient_repr},
                        tx_kind=tx_kind,
                    )
                    raise AdmissionError(
                        "transfer requires a non-zero recipient",
                        context={
                            "tx_hash": tx_hash_hex,
                            "sender": sender_hex,
                            "recipient": recipient_repr,
                            "reason": "invalid_recipient",
                        },
                    )

            if tx_version == 2 and (valid_after is None or valid_until is None or salt is None):
                missing = []
                if valid_after is None:
                    missing.append("validAfter")
                if valid_until is None:
                    missing.append("validUntil")
                if salt is None:
                    missing.append("salt")
                self._record_rejection(
                    tx_hash_hex,
                    "missing_validity_window",
                    {
                        "valid_after": valid_after,
                        "valid_until": valid_until,
                        "missing": missing,
                        "expected_location": "tx.body",
                    },
                )
                raise AdmissionError(
                    "missing validity window fields",
                    context={
                        "tx_hash": tx_hash_hex,
                        "sender": sender_hex,
                        "nonce": normalized_env.get("nonce"),
                        "missing": missing,
                        "expected_location": "tx.body",
                    },
                )

            nonce = normalized_env.get("nonce")
            if tx_version == 1:
                if nonce is None:
                    self._record_rejection(
                        tx_hash_hex,
                        "missing_nonce",
                        {"sender": sender_hex},
                    )
                    raise AdmissionError(
                        "missing nonce",
                        context={"tx_hash": tx_hash_hex, "sender": sender_hex},
                    )

                # Convert nonce to int before use to prevent TypeError in comparisons and dict lookups
                nonce_original_type = type(nonce).__name__
                try:
                    nonce = coerce_int("nonce", nonce)
                except TxNormalizationError as exc:
                    details = exc.details or {}
                    self._record_rejection(
                        tx_hash_hex,
                        "bad_field_type",
                        {
                            "sender": sender_hex,
                            "nonce": str(nonce),
                            "nonce_type": nonce_original_type,
                            "field": details.get("field", "nonce"),
                            "received_type": details.get("received_type", nonce_original_type),
                            "received_keys": details.get("received_keys"),
                        },
                    )
                    raise AdmissionError(
                        "invalid nonce type",
                        context={
                            "tx_hash": tx_hash_hex,
                            "sender": sender_hex,
                            "reason": "bad_field_type",
                            "field": details.get("field", "nonce"),
                            "received_type": details.get("received_type", nonce_original_type),
                            "received_keys": details.get("received_keys"),
                        },
                    ) from exc

                confirmed_nonce = self._confirmed_nonce(sender)
                expected_nonce = self.get_next_nonce(sender, confirmed_nonce or 0)
                pending_by_nonce = self._pending_by_nonce(sender_hex)
                if nonce in pending_by_nonce:
                    existing_hash = pending_by_nonce[nonce]
                    if existing_hash == tx_hash_hex:
                        return tx_hash_hex
                    self._record_rejection(
                        tx_hash_hex,
                        "replacement_unsupported",
                        {
                            "sender": sender_hex,
                            "nonce": int(nonce),
                            "existing_tx_hash": existing_hash,
                            "expected_nonce": expected_nonce,
                        },
                    )
                    raise ReplacementUnsupported(
                        sender=sender_hex,
                        nonce=int(nonce),
                        tx_hash_new=tx_hash_hex,
                        tx_hash_old=existing_hash,
                    )

                if nonce < expected_nonce:
                    self._record_rejection(
                        tx_hash_hex,
                        "nonce_too_low",
                        {
                            "expected": expected_nonce,
                            "got": int(nonce),
                            "confirmed": confirmed_nonce,
                        },
                    )
                    raise NonceTooLow(
                        expected_nonce=int(expected_nonce),
                        got_nonce=int(nonce),
                        sender=sender_hex,
                        tx_hash=tx_hash_hex,
                    )

                if nonce > expected_nonce:
                    self._record_rejection(
                        tx_hash_hex,
                        "nonce_gap",
                        {
                            "expected": expected_nonce,
                            "got": int(nonce),
                            "confirmed": confirmed_nonce,
                        },
                    )
                    raise NonceGap(
                        expected_nonce=int(expected_nonce),
                        got_nonce=int(nonce),
                        sender=sender_hex,
                        tx_hash=tx_hash_hex,
                    )
            else:
                nonce = None

            if tx_version == 2 and valid_after is not None and valid_until is not None:
                max_ttl_blocks = int(os.getenv("ANIMICA_MAX_TX_TTL_BLOCKS", "200") or 200)
                if valid_until - valid_after > max_ttl_blocks:
                    self._record_rejection(
                        tx_hash_hex,
                        "ttl_too_long",
                        {"valid_after": valid_after, "valid_until": valid_until},
                    )
                    raise AdmissionError(
                        "validity window exceeds maximum TTL",
                        context={"tx_hash": tx_hash_hex, "max_ttl_blocks": max_ttl_blocks},
                    )

                current_height = _current_height()
                if current_height < valid_after:
                    self._record_rejection(
                        tx_hash_hex,
                        "not_yet_valid",
                        {"valid_after": valid_after, "current_height": current_height},
                    )
                    raise NotYetValid(
                        valid_after=valid_after,
                        current_height=current_height,
                        sender=sender_hex,
                        tx_hash=tx_hash_hex,
                    )
                if current_height > valid_until:
                    self._record_rejection(
                        tx_hash_hex,
                        "expired",
                        {"valid_until": valid_until, "current_height": current_height},
                    )
                    raise Expired(
                        valid_until=valid_until,
                        current_height=current_height,
                        sender=sender_hex,
                        tx_hash=tx_hash_hex,
                    )

            gas_limit = _tx_gas_limit(tx)
            if gas_limit <= 0:
                self._record_rejection(
                    tx_hash_hex,
                    "invalid_gas_limit",
                    {"gas_limit": gas_limit},
                )
                raise AdmissionError(
                    "gas_limit must be > 0",
                    context={"tx_hash": tx_hash_hex},
                )

            # ANM-M09: apply the intrinsic-gas lower bound and block-gas upper
            # bound at admission so the mempool never admits/relays a tx that
            # can never be mined (gas_limit below intrinsic, or above the block
            # gas cap). LOCAL admission policy only; block validity unchanged.
            if self._enforce_gas_bounds:
                intrinsic = self._intrinsic_gas_floor(tx, decoded_tx_obj)
                if int(gas_limit) < int(intrinsic):
                    self._record_rejection(
                        tx_hash_hex,
                        "intrinsic_gas_too_low",
                        {"gas_limit": int(gas_limit), "intrinsic_gas": int(intrinsic)},
                    )
                    raise AdmissionError(
                        "gas_limit below intrinsic gas",
                        context={
                            "tx_hash": tx_hash_hex,
                            "gas_limit": int(gas_limit),
                            "intrinsic_gas": int(intrinsic),
                            "reason": "intrinsic_gas_too_low",
                        },
                    )
                if self._block_gas_limit and int(gas_limit) > int(self._block_gas_limit):
                    self._record_rejection(
                        tx_hash_hex,
                        "exceeds_block_gas",
                        {
                            "gas_limit": int(gas_limit),
                            "block_gas_limit": int(self._block_gas_limit),
                        },
                    )
                    raise AdmissionError(
                        "gas_limit exceeds block gas limit",
                        context={
                            "tx_hash": tx_hash_hex,
                            "gas_limit": int(gas_limit),
                            "block_gas_limit": int(self._block_gas_limit),
                            "reason": "exceeds_block_gas",
                        },
                    )

            fee = EffectiveFee.from_tx(tx)
            offered = _tx_offered_fee_wei(tx, fee)
            if self.min_gas_price_wei and offered < self.min_gas_price_wei:
                self._record_rejection(
                    tx_hash_hex,
                    "fee_too_low",
                    {"offered": offered, "min_required": self.min_gas_price_wei},
                )
                raise FeeTooLow(
                    offered_gas_price_wei=offered,
                    min_required_wei=self.min_gas_price_wei,
                    tx_hash=tx_hash_hex,
                    sender=sender_hex,
                )

            # ANM-H07: per-sender pending cap. Bound the number of distinct
            # pending nonces a single sender may occupy so an attacker cannot
            # flood the pool from one (even unfunded) account. A same-nonce
            # replacement (RBF) does not consume a new slot, so it is exempt.
            if self._per_sender_max_txs and self._per_sender_max_txs > 0:
                try:
                    sender_pending = list(self.pool.index.get_by_sender(sender_hex))
                except Exception:
                    sender_pending = []
                existing_nonces: set[int] = set()
                for e in sender_pending:
                    en = getattr(getattr(e, "meta", None), "nonce", None)
                    if en is not None:
                        try:
                            existing_nonces.add(int(en))
                        except (TypeError, ValueError):
                            pass
                is_replacement = nonce is not None and int(nonce) in existing_nonces
                if not is_replacement and len(sender_pending) >= self._per_sender_max_txs:
                    self._record_rejection(
                        tx_hash_hex,
                        "too_many_pending_from_sender",
                        {
                            "sender": sender_hex,
                            "pending": len(sender_pending),
                            "limit": self._per_sender_max_txs,
                        },
                    )
                    raise AdmissionError(
                        "too many pending transactions from sender",
                        context={
                            "tx_hash": tx_hash_hex,
                            "sender": sender_hex,
                            "pending": len(sender_pending),
                            "limit": self._per_sender_max_txs,
                            "reason": "too_many_pending_from_sender",
                        },
                    )

            # Pending debit accounting (per-sender)
            if self.state_db is not None and hasattr(self.state_db, "get_balance"):
                try:
                    balance = int(self.state_db.get_balance(sender))
                except Exception:
                    balance = None

                # ANM-H07: reject completely-unfunded senders. An account with
                # zero on-chain balance can never pay a fee, so its txs are pure
                # relay spam. Only enforced when a real integer balance was read.
                if (
                    self._require_funded_sender
                    and balance is not None
                    and int(balance) <= 0
                ):
                    self._record_rejection(
                        tx_hash_hex,
                        "insufficient_funds_pending",
                        {"required": 1, "available": 0, "unfunded_sender": True},
                    )
                    raise InsufficientFundsPending(
                        sender=sender_hex,
                        tx_hash=tx_hash_hex,
                        required=1,
                        available=0,
                    )

                if balance is not None:
                    try:
                        new_spend = int(estimate_max_spend(tx).total_max_spend)
                    except Exception:
                        new_spend = 0

                    pending_entries = []
                    try:
                        pending_entries = list(self.pool.index.get_by_sender(sender_hex))
                    except Exception:
                        pending_entries = []

                    pending_spend = 0
                    for entry in pending_entries:
                        try:
                            spend = int(estimate_max_spend(entry.tx).total_max_spend)
                        except Exception:
                            spend = 0
                        pending_spend += spend

                    available = balance - pending_spend
                    if available < new_spend:
                        self._record_rejection(
                            tx_hash_hex,
                            "insufficient_funds_pending",
                            {"required": new_spend, "available": max(0, available)},
                        )
                        raise InsufficientFundsPending(
                            sender=sender_hex,
                            tx_hash=tx_hash_hex,
                            required=new_spend,
                            available=max(0, available),
                        )

            tx_to_store: Any = tx
            if not isinstance(tx, Tx):
                try:
                    if hasattr(Tx, "from_cbor"):
                        tx_to_store = Tx.from_cbor(raw_bytes)  # type: ignore[attr-defined]
                except Exception:
                    tx_to_store = tx

            meta = TxMeta(
                sender=sender_hex,
                nonce=int(nonce) if nonce is not None else None,
                gas_limit=gas_limit,
                size_bytes=len(raw_bytes),
                first_seen=time.time(),
                local=local,
                effective_fee_wei=offered,
                origin=origin_label,
                peer_id=origin_peer,
                valid_after=valid_after,
                valid_until=valid_until,
                salt=salt,
            )
            pool_tx = PoolTx(
                tx=tx_to_store,
                tx_hash=tx_hash_bytes,
                raw=raw_bytes,
                meta=meta,
                fee=fee,
            )
            
            if simulate:
                return tx_hash_hex

            log.info(
                "MempoolService.submit: calling pool.add(), tx_hash=%s, sender=%s",
                tx_hash_hex,
                meta.sender,
            )
            try:
                self.pool.add(pool_tx, meta, is_local=local)
            except Exception as exc:
                self._record_rejection(
                    tx_hash_hex,
                    "pool_reject",
                    {"error": str(exc)},
                )
                raise

        # Verify tx was actually added to pool
        if not self.has_hash(tx_hash_hex):
            log.error(
                "MempoolService.submit: CRITICAL - pool.add() succeeded but tx not in pool, tx_hash=%s",
                tx_hash_hex,
            )
            self._record_rejection(
                tx_hash_hex,
                "pool_missing",
                {"tx_hash": tx_hash_hex},
            )
            raise AdmissionError(
                "pool.add succeeded but tx not in pool",
                context={"tx_hash": tx_hash_hex},
            )

        if not self._restoring:
            try:
                self._persist_snapshot()
            except Exception as exc:
                self._record_rejection(
                    tx_hash_hex,
                    "persistence_failed",
                    {"tx_hash": tx_hash_hex, "error": str(exc)},
                )
                log.error(
                    "MempoolService.submit: persistence failed, keeping in memory",
                    extra={"tx_hash": tx_hash_hex, "error": str(exc)},
                    exc_info=True,
                )
                raise PersistenceFailed(tx_hash=tx_hash_hex, error=str(exc)) from exc

        self._recent_txids[tx_hash_hex] = current_height + self._replay_window_blocks

        try:
            from rpc.instant_tx import get_instant_tx_service_singleton, instant_enabled
            from rpc import deps as _rpc_deps

            if instant_enabled():
                instant_svc = get_instant_tx_service_singleton()
                if instant_svc is not None:
                    anchor_hash = "0x" + ("00" * 32)
                    try:
                        # Avoid recursive startup: submit() may run while deps.build_context()
                        # restores persisted mempool entries before _CTX is available.
                        head = _rpc_deps.get_ctx().get_head()
                        if isinstance(head, dict):
                            hh = str(head.get("hash") or "")
                            if hh:
                                anchor_hash = hh if hh.startswith("0x") else ("0x" + hh)
                    except Exception:
                        pass
                    instant_svc.emit_local(txid=tx_hash_hex, anchor_hash=anchor_hash)
        except Exception:
            log.debug("instant tx receipt emit failed", exc_info=True)

        log.info(
            "MempoolService.submit: SUCCESS - tx added and persisted, tx_hash=%s, pool_size=%d",
            tx_hash_hex,
            len(self.pool),
        )
        log.info(
            "mempool.accepted",
            extra={
                "hash": tx_hash_hex,
                "from": meta.sender,
                "origin": origin_label,
                "peer": origin_peer,
            },
        )
        
        # Trigger P2P broadcast for all admitted txs (best-effort, non-blocking)
        if self._p2p_broadcast_callback is not None:
            log.info(f"[DIAG] P2P broadcast callback is set, triggering for tx {tx_hash_hex}")
            try:
                import asyncio

                callback = self._p2p_broadcast_callback
                try:
                    running_loop = asyncio.get_running_loop()
                except RuntimeError:
                    running_loop = None

                if running_loop is not None and running_loop.is_running():
                    asyncio.ensure_future(callback(tx_hash_bytes, raw_bytes), loop=running_loop)
                    log.info(
                        f"[DIAG] P2P broadcast scheduled for tx {tx_hash_hex} on current loop",
                        extra={"tx_hash": tx_hash_hex, "trigger": "mempool_submit"}
                    )
                else:
                    target_loop = self._p2p_broadcast_loop
                    if target_loop is not None and target_loop.is_running():
                        asyncio.run_coroutine_threadsafe(
                            callback(tx_hash_bytes, raw_bytes), target_loop
                        )
                        log.info(
                            f"[DIAG] P2P broadcast scheduled for tx {tx_hash_hex} on stored loop",
                            extra={"tx_hash": tx_hash_hex, "trigger": "mempool_submit"}
                        )
                    else:
                        log.warning(
                            f"[DIAG] No running event loop for P2P broadcast for tx {tx_hash_hex}",
                            extra={"tx_hash": tx_hash_hex}
                        )
            except Exception as e:
                log.error(
                    f"[DIAG] P2P broadcast callback failed for tx {tx_hash_hex}: {e}",
                    extra={"tx_hash": tx_hash_hex, "error": str(e)},
                    exc_info=True
                )
        else:
            log.info(f"[DIAG] P2P broadcast callback is NOT set for tx {tx_hash_hex}")

        if self._instant_block_callback is not None:
            try:
                import asyncio

                callback = self._instant_block_callback
                try:
                    running_loop = asyncio.get_running_loop()
                except RuntimeError:
                    running_loop = None

                if running_loop is not None and running_loop.is_running():
                    asyncio.ensure_future(callback(tx_hash_bytes, raw_bytes), loop=running_loop)
                else:
                    target_loop = self._instant_block_loop
                    if target_loop is not None and target_loop.is_running():
                        asyncio.run_coroutine_threadsafe(
                            callback(tx_hash_bytes, raw_bytes), target_loop
                        )
            except Exception:
                log.debug("instant block callback failed", exc_info=True)

        return tx_hash_hex

    def submit_atomic(
        self,
        *,
        tx: Any,
        raw: bytes,
        tx_hash_hex: str | None = None,
        local: bool = True,
        origin_peer: str | None = None,
        simulate: bool = False,
    ) -> tuple[bool, dict[str, Any] | None, str]:
        """
        Atomically submit a transaction and report admission status.

        Returns (accepted, reject_payload, tx_hash_hex).
        """
        computed_hash = tx_hash_hex
        if computed_hash is None:
            try:
                computed_hash = _tx_hash_hex(raw)
            except Exception:
                computed_hash = "0x" + bytes(raw).hex()

        try:
            admitted_hash = self.submit(
                tx=tx,
                raw=raw,
                tx_hash_hex=computed_hash,
                local=local,
                origin_peer=origin_peer,
                simulate=simulate,
            )
        except Exception as exc:
            reason = getattr(exc, "reason", None) or "internal_error"
            message = getattr(exc, "message", None) or str(exc)
            trace_id = uuid.uuid4().hex
            context = getattr(exc, "context", None)
            if not isinstance(context, dict):
                context = {"error_class": exc.__class__.__name__}
            context = {**_tx_context_fields(tx), **context}
            numeric_types = _tx_numeric_field_types(tx)
            if numeric_types:
                context.setdefault("numeric_field_types", numeric_types)
            context.setdefault("tx_hash", computed_hash)
            if reason == "admission_failed" and context.get("reason"):
                reason = str(context.get("reason"))
            norm_reason, norm_message, norm_context = _normalize_reject(str(reason), str(message), context)
            norm_context.setdefault("stage", "mempool_admission")
            inferred_bad_type = _infer_bad_field_type_from_exception(tx, exc)
            if inferred_bad_type and norm_reason == "internal_error":
                norm_reason = "bad_field_type"
                norm_context.update({k: v for k, v in inferred_bad_type.items() if k != "reason" and v is not None})
            if norm_reason == "internal_error":
                norm_context.setdefault("error_class", exc.__class__.__name__)
                norm_context.setdefault("error_message", str(exc))
                norm_context["trace_id"] = trace_id
            reject_obj = _to_mempool_reject(
                reason=norm_reason,
                message=norm_message,
                context=norm_context,
                exc=exc if norm_reason == "internal_error" else None,
            )
            reject = reject_obj.to_dict()
            reject["reason_code"] = norm_reason
            reject.setdefault("stage", "mempool_admission")
            reject.setdefault("tx_kind", norm_context.get("tx_kind", "unknown"))
            if "code" not in reject or reject.get("code") == 1000:
                reject["code"] = int(REJECT_CODE.get(reject_obj.reason, REJECT_CODE[RejectReason.internal_error]))
            if os.getenv("ANIMICA_DEBUG_MEMPOOL", "0") == "1":
                try:
                    log.info(json.dumps({"mempool_reject": reject}, sort_keys=True, separators=(",", ":"), default=str))
                except Exception:
                    pass
            
            # Log the full exception for debugging and trace correlation
            if norm_reason == "internal_error" or os.getenv("ANIMICA_DEBUG_MEMPOOL", "0") == "1":
                log_exception(
                    norm_context.get("trace_id") or trace_id,
                    tx,
                    exc,
                    field_hint=norm_context.get("field"),
                    field_value=norm_context.get("got_value_preview"),
                )
            log.warning(
                "MempoolService.submit_atomic: admission rejected, tx_hash=%s, reason=%s, trace_id=%s",
                computed_hash,
                reason,
                norm_context.get("trace_id"),
                exc_info=log.isEnabledFor(logging.DEBUG),
            )
            return False, reject, computed_hash

        if not self.has_hash(admitted_hash):
            return False, mk_reject(
                RejectReason.internal_error,
                message="pool missing admitted transaction",
                hint="retry submission",
                context={"tx_hash": admitted_hash},
                error_class="PoolMissing",
            ).to_dict(), admitted_hash

        return True, None, admitted_hash




    def snapshot(self, *, limit: int = 1000) -> MempoolSnapshot:
        raw_by_hash: dict[str, bytes] = {}
        entries: list[PendingTxEntry] = []
        total = len(self.pool)

        seen_hashes: set[str] = set()
        try:
            for tx_item, meta in self.pool.iter_ready():  # type: ignore[misc]
                pool_tx = tx_item
                raw = getattr(pool_tx, "raw", b"")
                tx = getattr(pool_tx, "tx", pool_tx)
                tx_hash_value = getattr(pool_tx, "tx_hash", None) or getattr(
                    meta, "tx_hash", None
                )
                if tx_hash_value is None:
                    continue
                tx_hash_hex = _normalize_hash_hex(
                    "0x" + _normalize_hash_bytes(tx_hash_value).hex()
                )
                if tx_hash_hex in seen_hashes:
                    continue
                seen_hashes.add(tx_hash_hex)
                raw_by_hash[tx_hash_hex] = raw
                entries.append(
                    PendingTxEntry(
                        hash_hex=tx_hash_hex,
                        raw=raw,
                        tx=tx,
                        received_at=getattr(meta, "first_seen", None),
                        expires_at=getattr(meta, "expires_at", None),
                    )
                )
                if len(entries) >= limit:
                    return MempoolSnapshot(entries=entries, raw_by_hash=raw_by_hash, total=total)
        except Exception as exc:
            log.debug("mempool ready snapshot failed", exc_info=exc)

        held_entries: list[tuple[float, PendingTxEntry, bytes]] = []
        for hash_bytes, entry in self.pool.index.all_items():
            tx_item = entry.tx
            meta = entry.meta
            pool_tx = tx_item
            raw = getattr(pool_tx, "raw", b"")
            tx = getattr(pool_tx, "tx", pool_tx)
            tx_hash_hex = _normalize_hash_hex(
                "0x" + _normalize_hash_bytes(hash_bytes).hex()
            )
            if tx_hash_hex in seen_hashes:
                continue
            seen_hashes.add(tx_hash_hex)
            received_at = getattr(meta, "first_seen", None)
            pending_entry = PendingTxEntry(
                hash_hex=tx_hash_hex,
                raw=raw,
                tx=tx,
                received_at=received_at,
                expires_at=getattr(meta, "expires_at", None),
            )
            held_entries.append((float(received_at or 0.0), pending_entry, raw))

        held_entries.sort(key=lambda item: item[0])
        for _ts, entry, raw in held_entries:
            raw_by_hash[entry.hash_hex] = raw
            entries.append(entry)
            if len(entries) >= limit:
                break

        return MempoolSnapshot(entries=entries, raw_by_hash=raw_by_hash, total=total)

    def get_raw(self, tx_hash_hex: str) -> bytes | None:
        try:
            tx_hash_bytes = _normalize_hash_bytes(tx_hash_hex)
        except Exception:
            return None
        entry = self.pool.index.get(tx_hash_bytes)
        if entry is None:
            return None

        # Index entries can be either IndexEntry(tx=PoolTx, meta=TxMeta) or PoolTx.
        raw = getattr(entry, "raw", None)
        if isinstance(raw, (bytes, bytearray)):
            return bytes(raw)

        tx_obj = getattr(entry, "tx", entry)
        raw = getattr(tx_obj, "raw", None)
        if isinstance(raw, (bytes, bytearray)):
            return bytes(raw)
        raw = getattr(tx_obj, "raw_cbor", None)
        if isinstance(raw, (bytes, bytearray)):
            return bytes(raw)
        if hasattr(tx_obj, "to_cbor"):
            try:
                raw_bytes = tx_obj.to_cbor()
            except Exception:
                raw_bytes = None
            if isinstance(raw_bytes, (bytes, bytearray)):
                return bytes(raw_bytes)
        return None

    def list_pending(self, *, limit: int = 1000) -> list[str]:
        snapshot = self.snapshot(limit=limit)
        return [entry.hash_hex for entry in snapshot.entries]

    def stats(self) -> dict[str, int]:
        stats = self.pool.stats()
        return {
            "count": int(getattr(stats, "total_txs", len(self.pool))),
            "totalBytes": int(getattr(stats, "total_bytes", 0)),
            "totalGas": int(getattr(stats, "total_gas", 0)),
        }

    def remove_included(self, tx_hashes: Iterable[str]) -> int:
        removed = 0
        for h in tx_hashes:
            try:
                if self.has_hash(h):
                    self.pool.remove_included([_normalize_hash_bytes(h)])
                    removed += 1
            except Exception:
                continue
        if removed and not self._restoring:
            self._persist_snapshot()
        return removed

    def revalidate(self) -> dict[str, int]:
        snapshot = self.snapshot(limit=len(self.pool) + 1)
        if not snapshot.entries:
            return {"evicted": 0}

        selection = select_for_block(
            head_state={"chain_id": self.chain_id, "height": _current_height()},
            limits={"max_gas": 10**18, "max_bytes": 10**18, "max_txs": None},
            pending=snapshot.entries,
            decode=None,
            state_db=self.state_db,
            policy={"min_gas_price": self.min_gas_price_wei},
            tx_index=self.tx_index,
            signature_validator=None,
        )

        evicted = 0
        for hash_hex, reason in selection.rejected_by_hash.items():
            if reason in {"exceeds_block_gas"}:
                continue
            try:
                self.pool.remove_included([_normalize_hash_bytes(hash_hex)])
                evicted += 1
            except Exception:
                continue
        if evicted and not self._restoring:
            self._persist_snapshot()
        return {"evicted": evicted}

    
    def _get_sender_lock(self, sender_hex: str) -> threading.RLock:
        """
        Get or create a lock for a specific sender to prevent TOCTOU races.
        
        Args:
            sender_hex: The sender address as hex string
            
        Returns:
            A reentrant lock for this sender
        """
        with self._sender_locks_lock:
            if sender_hex not in self._sender_locks:
                self._sender_locks[sender_hex] = threading.RLock()
            return self._sender_locks[sender_hex]

    def diagnose(self, *, limit: int = 1000) -> dict[str, dict[str, Any]]:
        snapshot = self.snapshot(limit=limit)
        if not snapshot.entries:
            return {}

        selection = select_for_block(
            head_state={"chain_id": self.chain_id, "height": _current_height()},
            limits={"max_gas": 10**18, "max_bytes": 10**18, "max_txs": None},
            pending=snapshot.entries,
            decode=None,
            state_db=self.state_db,
            policy={"min_gas_price": self.min_gas_price_wei},
            tx_index=self.tx_index,
            signature_validator=None,
        )

        diagnostics: dict[str, dict[str, Any]] = {}
        selected = set(selection.selected_hashes)
        for entry in snapshot.entries:
            if entry.hash_hex in selected:
                diagnostics[entry.hash_hex] = {"status": "eligible", "reason": None}
            else:
                reason = selection.rejected_by_hash.get(entry.hash_hex, "unknown")
                diagnostics[entry.hash_hex] = {"status": "rejected", "reason": reason}
        return diagnostics

    async def admit_tx(
        self,
        raw: bytes,
        local: bool | None = False,
        origin_peer: str | None = None,
    ) -> tuple[bool, str | None]:
        """
        Async wrapper for submit_atomic() used by P2P TX relay.

        Returns (accepted, reason_code) where reason_code is stable and actionable.
        """
        try:
            from core.encoding.cbor import loads as cbor_loads

            try:
                tx_obj = cbor_loads(raw)
            except Exception as exc:
                return False, f"decode_error:{type(exc).__name__}"

            accepted, reject, _ = self.submit_atomic(
                tx=tx_obj,
                raw=raw,
                tx_hash_hex=None,
                local=bool(local),
                origin_peer=origin_peer,
                simulate=False,
            )
            if accepted:
                return True, None
            payload = reject or {}
            reason_code = str(payload.get("reason_code") or payload.get("reason") or "internal_error")
            context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
            if reason_code == "internal_error" and context.get("trace_id"):
                return False, f"internal_error:trace_id={context['trace_id']}"
            return False, reason_code
        except Exception as exc:
            trace_id = uuid.uuid4().hex
            log_exception(trace_id, raw, exc)
            log.error(
                "Unexpected error in admit_tx trace_id=%s",
                trace_id,
                extra={"error": str(exc), "error_type": type(exc).__name__, "trace_id": trace_id},
                exc_info=True,
            )
            return False, f"internal_error:trace_id={trace_id}"


    def stats(self) -> dict[str, t.Any]:
        """Get mempool statistics."""
        return {
            "pending_count": len(self.pool),
            "pending_bytes": sum(
                len(getattr(getattr(entry, "tx", entry), "raw", b""))
                for entry in (self.pool.entries() if hasattr(self.pool, "entries") else [])
            ),
        }


# Global singleton for easy access from RPC methods
_mempool_service_singleton: MempoolService | None = None


def set_mempool_service_singleton(svc: MempoolService) -> None:
    """Set the global mempool service singleton."""
    global _mempool_service_singleton
    _mempool_service_singleton = svc


def get_mempool_service_singleton() -> MempoolService | None:
    """Get the global mempool service singleton."""
    return _mempool_service_singleton


__all__ = ["MempoolService", "MempoolSnapshot", "set_mempool_service_singleton", "get_mempool_service_singleton"]
