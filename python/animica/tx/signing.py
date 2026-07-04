"""Canonical transaction signing and txid helpers.

This module is the single source of truth for transaction signing preimages.
"""

from __future__ import annotations

import dataclasses as _dc
import hashlib
import logging
import os
import re
from typing import Any, Mapping

import cbor2
from pq.py.address import address_from_pubkey
from pq.py.registry import get_sig
from pq.py.sign import Signature, sign_detached
from pq.py.verify import verify_detached

try:
    from core.utils.tx import normalize_tx_body as _normalize_tx_body
except Exception:  # pragma: no cover
    _normalize_tx_body = None

try:
    from core.encoding.cbor import dumps as _canonical_cbor_dumps
except Exception:  # pragma: no cover
    _canonical_cbor_dumps = None

DOMAIN_TX_SIGN_V1 = "animica.tx.v1"

# ── ANM-C01/C02: production transaction signature-scheme allowlist ─────────
# Only the real FIPS-204 ML-DSA-65 scheme (alg_id 0x1003 / 4099) may authorize a
# transaction. alg_ids 0x1001 ("dilithium3") and 0x1002 ("sphincs_shake_128s")
# are FORGEABLE stubs — their verify() recomputes a public commitment
# shake_256(pubkey[:32] || msg) with no secret key — so accepting them lets
# anyone forge a signature for any pubkey. Because the state DB keys accounts by
# sha3_256(pubkey) with the alg_id stripped, a forged 0x1001 tx drains the SAME
# balance as the victim's real 0x1003 account. This allowlist is enforced
# node-locally at every tx admission/relay/mining call (always on, no consensus
# impact). Block-import rejection is additionally gated at the pq_hardening
# activation height (core.network_params) to grandfather existing history.
ACCEPTED_TX_SIG_ALG_IDS: frozenset = frozenset({0x1003})


def _tx_scheme_accepted(alg_id: int) -> bool:
    """True iff `alg_id` is an accepted production tx signature scheme."""
    try:
        return int(alg_id) in ACCEPTED_TX_SIG_ALG_IDS
    except (TypeError, ValueError):
        return False

__all__ = [
    "DOMAIN_TX_SIGN_V1",
    "ChainContext",
    "VerifyResult",
    "build_signable_tx_bytes",
    "extract_chain_id",
    "pq_sign_tx",
    "pq_verify_tx",
    "tx_sign_hash",
    "tx_canonical_bytes_unsigned",
    "tx_verify_signature",
    "txid",
    "tx_signing_preimage",
    "txid_from_canonical_bytes",
]

_LOG = logging.getLogger(__name__)


_HEX_RE = re.compile(r"^[0-9a-fA-F]*$")


@_dc.dataclass(frozen=True)
class ChainContext:
    chain_id: int
    genesis_hash: bytes = b""
    network: str = "unknown"
    fork_id: int | None = None
    domain: str = "tx"
    prehash: str = "sha3-512"


@_dc.dataclass(frozen=True)
class VerifyResult:
    ok: bool
    sign_hash_hex: str
    preimage_hex: str
    pub_fingerprint: str
    sig_fingerprint: str = ""
    scheme_id: int | None = None
    pubkey_len: int = 0
    sig_len: int = 0
    sign_bytes_len: int = 0
    domain: str = ""
    chain_id: int | None = None
    genesis_included: bool = False
    reason: str | None = None


def _as_dict(obj: Any) -> Any:
    """Dataclass → nested dict/list (deep) helper."""
    if _dc.is_dataclass(obj):
        return {k: _as_dict(v) for k, v in _dc.asdict(obj).items()}
    if isinstance(obj, Mapping):
        return {k: _as_dict(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_as_dict(x) for x in obj]
    return obj


def _coerce_data_to_bytes(v: Any) -> bytes:
    """
    Coerce tx `data` into bytes, accepting common forms:
    - None / "" -> b""
    - bytes / bytearray / memoryview -> bytes(...)
    - hex string "0x..." or bare hex -> bytes.fromhex(...)
    - list[int]/tuple[int] -> bytes(list)
    - str (non-hex) -> UTF-8 bytes (last-resort)
    """
    if v is None or v == "":
        return b""

    if isinstance(v, (bytes, bytearray, memoryview)):
        return bytes(v)

    if isinstance(v, (list, tuple)):
        # assume sequence of ints
        return bytes(v)

    if isinstance(v, str):
        s = v.strip()
        if s == "" or s.lower() == "0x":
            return b""
        if s.startswith(("0x", "0X")):
            h = s[2:]
            if len(h) % 2 == 1:
                h = "0" + h
            if not _HEX_RE.match(h):
                raise ValueError(f"tx.data looks like hex but contains non-hex chars: {v!r}")
            return bytes.fromhex(h)
        # bare hex?
        if _HEX_RE.match(s) and len(s) >= 2:
            h = s
            if len(h) % 2 == 1:
                h = "0" + h
            return bytes.fromhex(h)
        # last-resort: treat as UTF-8
        return s.encode("utf-8")

    raise TypeError(f"Unsupported tx.data type: {type(v).__name__}")


def _canonical_body(tx: Any) -> dict:
    """
    Build the canonical tx body map without signature metadata.
    Mirrors common field aliases and ensures deterministic types.
    """

    def _get(key: str) -> Any:
        if hasattr(tx, key):
            return getattr(tx, key)
        if isinstance(tx, Mapping) and key in tx:
            return tx[key]

        # aliases
        if key == "from":
            if hasattr(tx, "from_addr"):
                return getattr(tx, "from_addr")
            if isinstance(tx, Mapping) and "from_addr" in tx:
                return tx["from_addr"]

        if key == "gasLimit":
            if hasattr(tx, "gas_limit"):
                return getattr(tx, "gas_limit")
            if isinstance(tx, Mapping) and "gas_limit" in tx:
                return tx["gas_limit"]

        if key == "maxFee":
            if hasattr(tx, "max_fee"):
                return getattr(tx, "max_fee")
            if isinstance(tx, Mapping) and "max_fee" in tx:
                return tx["max_fee"]

        if key == "chainId":
            if hasattr(tx, "chain_id"):
                return getattr(tx, "chain_id")
            if isinstance(tx, Mapping) and "chain_id" in tx:
                return tx["chain_id"]

        raise KeyError(key)

    body = {
        "chainId": int(_get("chainId")),
        "from": str(_get("from")),
        "to": _get("to"),
        "nonce": int(_get("nonce")),
        "value": int(_get("value")),
        "gasLimit": int(_get("gasLimit")),
        "maxFee": int(_get("maxFee")),
        "data": _coerce_data_to_bytes(_get("data") if ("data" in tx if isinstance(tx, Mapping) else hasattr(tx, "data")) else None),
    }

    # normalize "to"
    if body["to"] in ("", None):
        body["to"] = None
    else:
        body["to"] = str(body["to"])

    return body


def extract_chain_id(tx: Any) -> int:
    """
    Extract chainId/chain_id from common envelope shapes.
    Raises ValueError if not present.
    """
    obj = _as_dict(tx)

    # Nested body is authoritative
    if isinstance(obj, Mapping) and "body" in obj and isinstance(obj["body"], Mapping):
        cid = obj["body"].get("chainId") or obj["body"].get("chain_id")
        if cid is not None:
            return int(cid)

    if isinstance(obj, Mapping):
        cid = obj.get("chainId") or obj.get("chain_id")
        if cid is not None:
            return int(cid)

    raise ValueError("Transaction missing chain_id/chainId")


def _extract_body(tx: Any) -> dict:
    obj = _as_dict(tx)

    # Check normalized envelope format FIRST (canonical representation).
    # During peer import/normalization both keys can exist, but verification
    # must use the normalized body to match canonical signing preimage bytes.
    if isinstance(obj, Mapping) and "tx" in obj and isinstance(obj["tx"], Mapping):
        # Normalized envelope format {"tx": {...}, "sigs": [...]}.
        body = dict(obj["tx"])
    elif isinstance(obj, Mapping) and "body" in obj and isinstance(obj["body"], Mapping):
        # Legacy/original envelope format {"body": {...}, "sig": {...}}.
        body = dict(obj["body"])
    else:
        # fall back to canonical extraction
        try:
            body = _canonical_body(obj)
        except Exception:
            body = dict(obj) if isinstance(obj, Mapping) else {}

    # Remove signature metadata
    for k in ("sig", "signature", "sigs"):
        if k in body:
            body.pop(k, None)

    # Ensure data is bytes if present
    if "data" in body:
        body["data"] = _coerce_data_to_bytes(body["data"])

    return body


def _canonical_cbor(obj: Any) -> bytes:
    if _canonical_cbor_dumps is not None:
        return _canonical_cbor_dumps(obj)
    return cbor2.dumps(obj, canonical=True)


def _raw_bytes(v: Any, *, field: str) -> bytes:
    if isinstance(v, (bytes, bytearray, memoryview)):
        return bytes(v)
    if isinstance(v, str):
        raise TypeError(f"{field} must be raw bytes, not hex/string")
    raise TypeError(f"{field} must be raw bytes")


def _fp8(data: bytes) -> str:
    try:
        import blake3  # type: ignore

        return blake3.blake3(data).hexdigest()[:16]
    except Exception:
        return hashlib.sha3_256(data).hexdigest()[:16]


def tx_canonical_bytes_unsigned(tx: Any) -> bytes:
    body = _extract_body(tx)
    if _normalize_tx_body is not None and isinstance(body, Mapping):
        try:
            body = _normalize_tx_body(body)
        except Exception:
            pass
    return _canonical_cbor(body)


def tx_signing_preimage(tx: Any, ctx: ChainContext | None = None, *, chain_id: int | None = None, genesis: bytes | None = None, network: str | None = None, domain: str = DOMAIN_TX_SIGN_V1, message_type: str = "tx") -> bytes:
    """Build the canonical, versioned tx signing preimage.

    The preimage intentionally excludes signatures and includes explicit domain
    separators plus chain/genesis bindings.
    """
    if ctx is not None:
        chain_id = int(ctx.chain_id)
        genesis = bytes(ctx.genesis_hash)
        network = str(ctx.network)
    if chain_id is None or genesis is None or network is None:
        raise ValueError("tx_signing_preimage requires either ctx or chain_id+genesis+network")

    body = _extract_body(tx)
    if _normalize_tx_body is not None and isinstance(body, Mapping):
        try:
            body = _normalize_tx_body(body)
        except Exception:
            pass

    tx_version = int(body.get("v", body.get("version", 1)))
    preimage_obj = {
        1: str(domain),
        2: int(chain_id),
        3: bytes(genesis),
        4: str(network),
        5: str(message_type),
        6: int(tx_version),
        7: body,
    }
    return _canonical_cbor(preimage_obj)


def tx_sign_hash(tx: Any, ctx: ChainContext) -> bytes:
    return hashlib.sha3_512(
        tx_signing_preimage(
            tx,
            chain_id=int(ctx.chain_id),
            genesis=bytes(ctx.genesis_hash),
            network=str(ctx.network),
            domain=DOMAIN_TX_SIGN_V1,
            message_type="tx",
        )
    ).digest()


def pq_sign_tx(tx: Any, privkey: bytes, pubkey: bytes, alg_id: int, ctx: ChainContext) -> Signature:
    preimage = tx_signing_preimage(
        tx,
        chain_id=int(ctx.chain_id),
        genesis=bytes(ctx.genesis_hash),
        network=str(ctx.network),
        domain=DOMAIN_TX_SIGN_V1,
        message_type="tx",
    )
    return sign_detached(
        preimage,
        alg=alg_id,
        sk=privkey,
        pk=pubkey,
        domain=ctx.domain,
        chain_id=int(ctx.chain_id),
        fork_id=ctx.fork_id,
        prehash=ctx.prehash,  # type: ignore[arg-type]
    )


def pq_verify_tx(tx: Any, signature: Signature, pubkey: bytes, ctx: ChainContext, *, from_addr: str | None = None) -> VerifyResult:
    return tx_verify_signature(tx, signature, pubkey, ctx, from_addr=from_addr)


def tx_verify_signature(tx: Any, signature: Signature, pubkey: bytes, ctx: ChainContext, *, from_addr: str | None = None) -> VerifyResult:
    pubkey = _raw_bytes(pubkey, field="pubkey")
    sig_bytes = _raw_bytes(signature.sig, field="signature")
    preimage = tx_signing_preimage(
        tx, ctx,
        domain=DOMAIN_TX_SIGN_V1,
        message_type="tx",
    )
    sign_hash = hashlib.sha3_512(preimage).digest()
    pub_fp = "0x" + hashlib.sha3_256(pubkey).hexdigest()[:16]
    sig_fp = "0x" + _fp8(sig_bytes)

    info = get_sig(signature.alg_id)
    if info is None:
        return VerifyResult(False, "0x" + sign_hash.hex(), "0x" + preimage.hex(), pub_fp, sig_fingerprint=sig_fp, scheme_id=signature.alg_id, pubkey_len=len(pubkey), sig_len=len(sig_bytes), sign_bytes_len=len(preimage), domain=ctx.domain, chain_id=int(ctx.chain_id), genesis_included=bool(ctx.genesis_hash), reason=f"unsupported_scheme:{signature.alg_id}")
    # ANM-C01/C02: reject forgeable/deprecated schemes BEFORE dispatch so the
    # forgeable stub verifier is never reached at admission/relay/mining.
    if not _tx_scheme_accepted(signature.alg_id):
        return VerifyResult(False, "0x" + sign_hash.hex(), "0x" + preimage.hex(), pub_fp, sig_fingerprint=sig_fp, scheme_id=signature.alg_id, pubkey_len=len(pubkey), sig_len=len(sig_bytes), sign_bytes_len=len(preimage), domain=ctx.domain, chain_id=int(ctx.chain_id), genesis_included=bool(ctx.genesis_hash), reason=f"scheme_deprecated:{signature.alg_id}")
    if len(pubkey) != info.pubkey_size or len(sig_bytes) != info.signature_size:
        return VerifyResult(False, "0x" + sign_hash.hex(), "0x" + preimage.hex(), pub_fp, sig_fingerprint=sig_fp, scheme_id=signature.alg_id, pubkey_len=len(pubkey), sig_len=len(sig_bytes), sign_bytes_len=len(preimage), domain=ctx.domain, chain_id=int(ctx.chain_id), genesis_included=bool(ctx.genesis_hash), reason=f"invalid_signature_size:expected_pk={info.pubkey_size},expected_sig={info.signature_size}")
    try:
        ok = verify_detached(
            preimage,
            signature,
            pubkey,
            domain=ctx.domain,
            chain_id=int(ctx.chain_id),
            fork_id=ctx.fork_id,
            prehash=ctx.prehash,  # type: ignore[arg-type]
        )
    except Exception as exc:
        return VerifyResult(
            ok=False,
            sign_hash_hex="0x" + sign_hash.hex(),
            preimage_hex="0x" + preimage.hex(),
            pub_fingerprint=pub_fp,
            sig_fingerprint=sig_fp,
            scheme_id=signature.alg_id,
            pubkey_len=len(pubkey),
            sig_len=len(sig_bytes),
            sign_bytes_len=len(preimage),
            domain=ctx.domain,
            chain_id=int(ctx.chain_id),
            genesis_included=bool(ctx.genesis_hash),
            reason=str(exc),
        )

    if ok and from_addr:
        try:
            expected = address_from_pubkey(pubkey, signature.alg_id)
            if expected != from_addr:
                return VerifyResult(
                    ok=False,
                    sign_hash_hex="0x" + sign_hash.hex(),
                    preimage_hex="0x" + preimage.hex(),
                    pub_fingerprint=pub_fp,
                    sig_fingerprint=sig_fp,
                    scheme_id=signature.alg_id,
                    pubkey_len=len(pubkey),
                    sig_len=len(sig_bytes),
                    sign_bytes_len=len(preimage),
                    domain=ctx.domain,
                    chain_id=int(ctx.chain_id),
                    genesis_included=bool(ctx.genesis_hash),
                    reason="from address does not match signature public key",
                )
        except Exception:
            pass

    out = VerifyResult(
        ok=bool(ok),
        sign_hash_hex="0x" + sign_hash.hex(),
        preimage_hex="0x" + preimage.hex(),
        pub_fingerprint=pub_fp,
        sig_fingerprint=sig_fp,
        scheme_id=signature.alg_id,
        pubkey_len=len(pubkey),
        sig_len=len(sig_bytes),
        sign_bytes_len=len(preimage),
        domain=ctx.domain,
        chain_id=int(ctx.chain_id),
        genesis_included=bool(ctx.genesis_hash),
        reason=None if ok else "verification failed",
    )
    if os.getenv("ANIMICA_DEBUG_TX_VERIFY") == "1":
        _LOG.warning(
            "tx_verify tx_hash=%s scheme_id=%s pubkey_len=%s sig_len=%s pub_fp=%s sig_fp=%s sign_bytes_len=%s sign_hash=%s domain=%s chain_id=%s genesis_included=%s ok=%s reason=%s",
            txid(tx).hex()[:16],
            out.scheme_id,
            out.pubkey_len,
            out.sig_len,
            out.pub_fingerprint,
            out.sig_fingerprint,
            out.sign_bytes_len,
            out.sign_hash_hex,
            out.domain,
            out.chain_id,
            out.genesis_included,
            out.ok,
            out.reason,
        )
    return out


def build_signable_tx_bytes(tx: Any, chain_id: int | None = None) -> bytes:
    """Backward-compatible alias used by existing callers.

    Returns canonical CBOR(tx body), i.e. the payload embedded into
    ``tx_signing_preimage(...)[7]``.
    """
    cid = extract_chain_id(tx)
    if chain_id is not None and int(chain_id) != cid:
        raise ValueError(f"Transaction chain_id mismatch: tx={cid}, override={int(chain_id)}")

    body = _extract_body(tx)
    return _canonical_cbor(body)


def txid_from_canonical_bytes(tx_canonical_bytes: bytes) -> bytes:
    """Consensus txid definition: sha3-256(full canonical signed tx bytes)."""
    import hashlib

    return hashlib.sha3_256(bytes(tx_canonical_bytes)).digest()


def txid(tx: Any) -> bytes:
    return txid_from_canonical_bytes(_canonical_cbor(_as_dict(tx)))
