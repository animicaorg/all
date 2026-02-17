"""
rpc.methods.tx2 - New Transaction RPC Methods (Mempool2)
========================================================

Production-ready RPC methods using the new mempool2 system.

These methods replace the old tx.py handlers which had TypeError issues.
All methods use coretx canonical encoding and mempool2 admission engine.

Methods:
- tx2.sendRawTransaction: Submit raw CBOR transaction
- tx2.getTransaction: Query transaction by hash
- tx2.getTransactionStatus: Get tx status (pending/confirmed/unknown)
- tx2.getMempoolStats: Get mempool statistics
"""

from __future__ import annotations

import ipaddress
import logging
import os
import hashlib
import json
from typing import Any, Optional

from coretx import TxEnvelope, TxId
from coretx.canonical import decode_tx_envelope
from coretx.crypto import get_signature_policy_status, list_scheme_descriptors
from coretx.errors import RejectReason

from rpc import errors as rpc_errors
from rpc.methods import method
from rpc.mempool2_service import get_mempool2_service
from mempool2 import TxSource
import rpc.deps as deps

__all__ = [
    "send_raw_transaction_v2",
    "get_transaction_v2",
    "get_transaction_status_v2",
    "get_mempool_stats_v2",
    "get_supported_signature_schemes",
    "get_policy_status",
    "get_pq_alg_policy",
    "get_effective_policy",
]

log = logging.getLogger(__name__)
_DEBUG = os.environ.get("ANIMICA_DEBUG_TX") == "1"


def _hex_to_bytes(hex_str: str) -> bytes:
    if not isinstance(hex_str, str):
        raise ValueError(f"Expected string, got {type(hex_str).__name__}")
    if hex_str.startswith("0x") or hex_str.startswith("0X"):
        hex_str = hex_str[2:]
    try:
        return bytes.fromhex(hex_str)
    except ValueError as e:
        raise ValueError(f"Invalid hex string: {e}") from e


def _bytes_to_hex(data: bytes) -> str:
    return "0x" + data.hex()


def _txid_from_hex(hex_str: str) -> TxId:
    data = _hex_to_bytes(hex_str)
    if len(data) != 32:
        raise ValueError(f"TxId must be 32 bytes, got {len(data)}")
    return TxId(bytes32=data)


def _envelope_to_dict(envelope: TxEnvelope) -> dict[str, Any]:
    body = envelope.body
    auth = envelope.auth
    return {
        "txid": _bytes_to_hex(envelope.txid.bytes32),
        "body": {
            "version": body.version,
            "chain_id": body.chain_id,
            "nonce": body.nonce,
            "from_addr": _bytes_to_hex(body.from_addr),
            "to_addr": _bytes_to_hex(body.to_addr),
            "value": str(body.value),
            "fee": str(body.fee),
            "gas_limit": body.gas_limit,
            "data": _bytes_to_hex(body.data),
            "memo": body.memo,
            "timestamp": body.timestamp,
            "kind": int(body.kind),
        },
        "auth": {
            "scheme_id": auth.scheme_id,
            "pubkey_bytes": _bytes_to_hex(auth.pubkey_bytes),
            "signature_bytes": _bytes_to_hex(auth.signature_bytes),
            "prehash_id": auth.prehash_id,
        },
    }


def _is_localhost_ctx(ctx: Any) -> bool:
    try:
        client = getattr(ctx, "client", None)
        host = None
        if isinstance(client, tuple) and client:
            host = client[0]
        elif client is not None:
            host = getattr(client, "host", None)
        if not host:
            return False
        ip = ipaddress.ip_address(str(host))
        return bool(ip.is_loopback)
    except Exception:
        return False


def _admin_token_authorized(ctx: Any) -> bool:
    required = (os.environ.get("ANIMICA_RPC_ADMIN_TOKEN") or "").strip()
    if not required:
        return False
    try:
        headers = {str(k).lower(): str(v) for k, v in getattr(ctx, "headers", {}).items()}
    except Exception:
        headers = {}
    got = headers.get("x-animica-admin-token", "")
    return bool(got) and got == required


@method("tx2.sendRawTransaction", desc="Submit raw CBOR transaction to mempool2")
async def send_raw_transaction_v2(raw_tx: str) -> dict[str, Any]:
    try:
        try:
            tx_bytes = _hex_to_bytes(raw_tx)
        except ValueError as e:
            if _DEBUG:
                log.debug(f"Hex decode failed: {e}")
            raise rpc_errors.InvalidParams(f"Invalid hex encoding: {e}")

        try:
            envelope = decode_tx_envelope(tx_bytes)
        except (ValueError, TypeError) as e:
            if _DEBUG:
                log.debug(f"CBOR decode failed: {e}")
            raise rpc_errors.InvalidParams(f"Invalid CBOR encoding: {e}")

        if _DEBUG:
            log.debug(
                f"Decoded tx {envelope.txid.hex()[:16]}... "
                f"chain_id={envelope.body.chain_id}, "
                f"nonce={envelope.body.nonce}"
            )

        try:
            mempool = get_mempool2_service()
        except Exception as e:
            log.error(f"Failed to get mempool2 service: {e}")
            raise rpc_errors.ServerError(f"Mempool service unavailable: {e}")

        success, rejection = mempool.admit_tx(
            envelope=envelope,
            source=TxSource.RPC,
            peer_id=None,
        )

        if success:
            if _DEBUG:
                log.info(f"Admitted tx {envelope.txid.hex()[:16]}... via RPC")

            return {
                "txid": _bytes_to_hex(envelope.txid.bytes32),
                "admitted": True,
            }

        if not rejection:
            raise rpc_errors.InvalidTx("Transaction rejected (no details)")

        if _DEBUG:
            log.debug(
                f"Rejected tx {envelope.txid.hex()[:16]}...: "
                f"{rejection.reason.value} - {rejection.message}"
            )

        code_map = {
            RejectReason.invalid_signature: rpc_errors.AnimicaCode.INVALID_TX,
            RejectReason.invalid_pubkey: rpc_errors.AnimicaCode.INVALID_TX,
            RejectReason.scheme_unsupported: rpc_errors.AnimicaCode.INVALID_TX,
            RejectReason.policy_reject: rpc_errors.AnimicaCode.INVALID_TX,
            RejectReason.chain_id_mismatch: rpc_errors.AnimicaCode.CHAIN_ID_MISMATCH,
            RejectReason.insufficient_funds: rpc_errors.AnimicaCode.INSUFFICIENT_FUNDS,
            RejectReason.nonce_too_low: rpc_errors.AnimicaCode.NONCE_TOO_LOW,
            RejectReason.nonce_too_high: rpc_errors.AnimicaCode.NONCE_TOO_HIGH,
            RejectReason.nonce_gap: rpc_errors.AnimicaCode.NONCE_TOO_LOW,
            RejectReason.fee_too_low: rpc_errors.AnimicaCode.FEE_TOO_LOW,
            RejectReason.tx_oversize: rpc_errors.AnimicaCode.TX_TOO_LARGE,
            RejectReason.tx_already_known: rpc_errors.AnimicaCode.DUPLICATE_TX,
        }

        rpc_code = code_map.get(rejection.reason, rpc_errors.AnimicaCode.INVALID_TX)
        error_data = rejection.to_dict()
        error_data["txid"] = _bytes_to_hex(envelope.txid.bytes32)

        raise rpc_errors.RpcError(
            code=int(rpc_code),
            message=rejection.message,
            data=error_data,
        )

    except rpc_errors.RpcError:
        raise
    except Exception as e:
        log.exception(f"Unexpected error in tx2.sendRawTransaction: {e}")
        raise rpc_errors.InternalError(f"Internal error: {e}")


@method("tx2.getTransaction", desc="Get transaction by hash from mempool2 or blocks")
async def get_transaction_v2(tx_hash: str) -> Optional[dict[str, Any]]:
    try:
        try:
            txid = _txid_from_hex(tx_hash)
        except ValueError as e:
            raise rpc_errors.InvalidParams(f"Invalid tx_hash: {e}")

        mempool = get_mempool2_service()
        entry = mempool.get_tx(txid)

        if entry:
            result = _envelope_to_dict(entry.envelope)
            result["status"] = "pending"
            result["arrival_time"] = entry.arrival_time
            result["fee_rate"] = entry.fee_rate
            result["source"] = entry.source.value
            return result

        return None

    except rpc_errors.RpcError:
        raise
    except Exception as e:
        log.exception(f"Unexpected error in tx2.getTransaction: {e}")
        raise rpc_errors.InternalError(f"Internal error: {e}")


@method("tx2.getTransactionStatus", desc="Get transaction status (pending/confirmed/unknown)")
async def get_transaction_status_v2(tx_hash: str) -> dict[str, Any]:
    try:
        try:
            txid = _txid_from_hex(tx_hash)
        except ValueError as e:
            raise rpc_errors.InvalidParams(f"Invalid tx_hash: {e}")

        mempool = get_mempool2_service()
        in_mempool = mempool.has_tx(txid)

        if in_mempool:
            return {
                "txid": tx_hash,
                "status": "pending",
                "in_mempool": True,
            }

        return {
            "txid": tx_hash,
            "status": "unknown",
            "in_mempool": False,
        }

    except rpc_errors.RpcError:
        raise
    except Exception as e:
        log.exception(f"Unexpected error in tx2.getTransactionStatus: {e}")
        raise rpc_errors.InternalError(f"Internal error: {e}")


@method("tx2.getMempoolStats", desc="Get mempool2 statistics")
async def get_mempool_stats_v2() -> dict[str, Any]:
    try:
        mempool = get_mempool2_service()
        stats = mempool.get_stats()
        return stats.to_dict()

    except Exception as e:
        log.exception(f"Unexpected error in tx2.getMempoolStats: {e}")
        raise rpc_errors.InternalError(f"Internal error: {e}")


@method("tx.getSupportedSignatureSchemes", aliases=("tx_getSupportedSignatureSchemes",), desc="List signature schemes supported by this node")
def get_supported_signature_schemes() -> dict[str, Any]:
    status = get_signature_policy_status()
    return {
        "policyRoots": status.get("policyRoots", {}),
        "schemes": [
            {
                "schemeId": s.get("schemeId"),
                "name": s.get("name"),
                "pubkeyLengths": s.get("pubkeyLengths", []),
                "signatureLengths": s.get("signatureLengths", []),
                "enabledByCode": bool(s.get("enabledByCode")),
                "enabledByPolicy": bool(s.get("enabledByPolicy")),
                "enabledEffective": bool(s.get("enabledEffective")),
                "reasonIfDisabled": s.get("reasonIfDisabled"),
                "enabled": bool(s.get("enabledEffective")),
            }
            for s in list_scheme_descriptors()
        ],
    }




@method("policy.getPqAlgPolicy", aliases=("policy_getPqAlgPolicy",), desc="Return evaluated PQ signature policy allowlist")
def get_pq_alg_policy() -> dict[str, Any]:
    chain_id = int(deps.get_chain_id())
    schemes = list_scheme_descriptors()
    allowed = [
        {
            "id": int(s.get("schemeId")),
            "name": str(s.get("name") or f"scheme_{s.get('schemeId')}"),
            "enabled": bool(s.get("enabledEffective")),
        }
        for s in schemes
    ]
    default_scheme_id = next((entry["id"] for entry in allowed if entry["enabled"]), None)
    policy_roots = get_signature_policy_status().get("policyRoots", {})
    return {
        "chainId": chain_id,
        "allowedSchemes": allowed,
        "defaultSchemeId": default_scheme_id,
        "policyRoot": policy_roots.get("pqAlgPolicy"),
    }


@method("policy.getEffective", aliases=("policy_getEffective", "policy.get"), desc="Return effective signature policy, roots, and enabled scheme matrix")
def get_effective_policy() -> dict[str, Any]:
    chain_id = int(deps.get_chain_id())
    status = get_signature_policy_status()
    schemes = list_scheme_descriptors()
    enabled_names = [str(s.get("name")) for s in schemes if bool(s.get("enabledEffective"))]
    payload = {
        "chainId": chain_id,
        "enabledSignatureSchemes": enabled_names,
        "schemes": schemes,
        "policyRoots": status.get("policyRoots", {}),
        "policySource": "env/runtime",
        "override": status.get("override", {}),
    }
    payload["policyHash"] = "0x" + hashlib.sha3_256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return payload

@method("admin.getPolicyStatus", desc="Show active signature policy status and operator override")
def get_policy_status(ctx: Any = None) -> dict[str, Any]:
    if not _is_localhost_ctx(ctx) and not _admin_token_authorized(ctx):
        raise rpc_errors.AccessDenied("Unauthorized", method="admin.getPolicyStatus", required="localhost or valid admin token")

    status = get_signature_policy_status()
    return {
        "policyRoots": status.get("policyRoots", {}),
        "override": status.get("override", {}),
        "schemes": [
            {
                "schemeId": s.get("schemeId"),
                "name": s.get("name"),
                "enabledByCode": bool(s.get("enabledByCode")),
                "enabledByPolicy": bool(s.get("enabledByPolicy")),
                "enabledEffective": bool(s.get("enabledEffective")),
                "reasonIfDisabled": s.get("reasonIfDisabled"),
            }
            for s in list_scheme_descriptors()
        ],
    }
